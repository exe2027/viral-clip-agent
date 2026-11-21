"""Recuperación de la fuente VOD de los clips en vivo sin source_id.

Reglas que se prueban aquí (con mocks; la verificación con datos
reales está en runs/vod-recovery-2026-11-20/ y el test condicional
de clip 7 al final):

- el match de audio recupera el offset exacto (sintético y, si
  están los archivos de validación, el clip 7: VOD 2932415021,
  start 3200).
- el offset candidato es T0 + start_sec - 12 - VOD.created_at y
  un offset imposible se rechaza (no se inventa).
- el clip se mapea a su cut-job por influencer + created_at; sin
  job o con varios que encajen, no se adivina (dudoso).
- sin VOD que cubra el clip -> sin_vod definitivo, sin inventar.
- un match de confianza baja -> dudoso y NO entra en el mapa.
- solo las recuperaciones de confianza alta entran en el mapa
  (mismo formato que el dry-run de remake_all.py).
- el SQL de D1 se genera (no se ejecuta) solo para recuperados.
- remake_all.resolve_source usa el mapa cuando el panel no trae
  source_id; si el panel ya trae uno, manda el panel.
"""

import json
import os
import tempfile
import unittest
import wave
from unittest import mock

import numpy as np

from clipagent import recover_vod_source as rvs
from clipagent import remake_all as ra


def _write_wav(path, samples, sr=16000):
    """Escribe float32 [-1, 1] como WAV PCM16 mono."""
    pcm = np.clip(samples, -1.0, 1.0)
    pcm = (pcm * 32767).astype(np.int16)
    with wave.open(path, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes(pcm.tobytes())


def _fake_runs():
    return [{
        "run_id": 111,
        "created_at": "2026-11-19T01:00:00Z",
        "cuts": [
            {
                "job_id": 1,
                "name": "Cortar en vivo (noni)",
                "streamer": "noni",
                "conclusion": "success",
                "started_at": "2026-11-19T21:15:03Z",
                "completed_at": "2026-11-19T21:25:09Z",
                "step_seguir": {
                    "started_at": "2026-11-19T21:15:05Z",
                    "completed_at": "2026-11-19T21:25:00Z",
                    "conclusion": "success",
                },
            },
            {
                "job_id": 2,
                "name": "Cortar en vivo (rubius)",
                "streamer": "rubius",
                "conclusion": "success",
                "started_at": "2026-11-19T21:15:03Z",
                "completed_at": "2026-11-19T21:25:09Z",
                "step_seguir": {
                    "started_at": "2026-11-19T21:15:05Z",
                    "completed_at": "2026-11-19T21:25:00Z",
                    "conclusion": "success",
                },
            },
        ],
    }]


def _clip(**over):
    clip = {
        "id": 155,
        "title": "clip de prueba",
        "streamer": "noni",
        "platform": "twitch",
        "start_sec": 110,
        "end_sec": 150,
        "created_at": 1795123143000,  # 2026-11-19T21:19:03Z
        "video_r2_key": "clips/sebastian/155/x.mp4",
    }
    clip.update(over)
    return clip


class TestMatchAudio(unittest.TestCase):
    def test_recovers_exact_offset_synthetic(self):
        rng = np.random.default_rng(7)
        sr = 16000
        # Ruido con envolvente lenta (como voz/música, sin tonos
        # periódicos que creen picos falsos por periodicidad).
        t = np.arange(sr * 200) / sr
        env = 0.6 + 0.4 * np.sin(2 * np.pi * 0.5 * t)
        vod = (rng.normal(0, 0.3, sr * 200) * env).astype(np.float64)
        offset = 100.0
        clip = vod[int(offset * sr): int((offset + 40) * sr)].copy()
        clip += rng.normal(0, 0.01, len(clip))  # ruido de render
        with tempfile.TemporaryDirectory() as d:
            vp = os.path.join(d, "vod.wav")
            cp = os.path.join(d, "clip.wav")
            _write_wav(vp, vod)
            _write_wav(cp, clip)
            m = rvs.match_audio(cp, vp, 0)
        self.assertAlmostEqual(m["offset"], offset, delta=0.1)
        self.assertGreaterEqual(m["correlation"], 0.9)
        self.assertGreaterEqual(m["ratio"], 10.0)
        self.assertEqual(rvs.confidence(m), "alta")

    def test_unrelated_audio_is_dudosa(self):
        rng = np.random.default_rng(11)
        sr = 16000
        vod = rng.normal(0, 0.3, sr * 120).astype(np.float64)
        clip = rng.normal(0, 0.3, sr * 40).astype(np.float64)
        with tempfile.TemporaryDirectory() as d:
            vp = os.path.join(d, "vod.wav")
            cp = os.path.join(d, "clip.wav")
            _write_wav(vp, vod)
            _write_wav(cp, clip)
            m = rvs.match_audio(cp, vp, 0)
        self.assertEqual(rvs.confidence(m), "dudosa")


class TestCandidateOffset(unittest.TestCase):
    def test_formula(self):
        # T0 2026-11-19T21:15:15Z; VOD creado 2026-11-19T10:01:07Z.
        t0 = 1795122915.0
        vod = {"vod_id": "2933696686", "created_at": 1795082467.0,
               "duration": 41240.0}
        cand, motivo = rvs.candidate_offset(
            {"start_sec": 110, "end_sec": 150}, t0, vod)
        self.assertEqual(motivo, "")
        self.assertAlmostEqual(cand, 40546.0, delta=0.01)

    def test_impossible_offset_rejected(self):
        t0 = 1795122915.0
        vod = {"vod_id": "2933696686", "created_at": 1795082467.0,
               "duration": 41240.0}
        # start_sec enorme: el candidato cae fuera del VOD.
        cand, motivo = rvs.candidate_offset(
            {"start_sec": 50000, "end_sec": 50040}, t0, vod)
        self.assertIsNone(cand)
        self.assertIn("fuera del VOD", motivo)
        # Rango invertido también se rechaza.
        cand, motivo = rvs.candidate_offset(
            {"start_sec": 150, "end_sec": 110}, t0, vod)
        self.assertIsNone(cand)


class TestMapClipToJob(unittest.TestCase):
    def test_finds_job_by_streamer_and_created_at(self):
        (run_job, motivo) = rvs.map_clip_to_job(_clip(), _fake_runs())
        self.assertEqual(motivo, "")
        run, job = run_job
        self.assertEqual(run["run_id"], 111)
        self.assertEqual(job["streamer"], "noni")

    def test_streamer_name_normalized(self):
        # D1 trae "Kai Cenat" (nombre visible) y el job "kaicenat":
        # la comparación normaliza (minúsculas, sin espacios).
        clip = _clip(streamer="Kai Cenat")
        runs = [{
            "run_id": 111,
            "created_at": "2026-11-19T21:00:00Z",
            "cuts": [{
                "job_id": 1,
                "name": "Cortar en vivo (kaicenat)",
                "streamer": "kaicenat",
                "conclusion": "success",
                "started_at": "2026-11-19T21:15:03Z",
                "completed_at": "2026-11-19T21:25:09Z",
                "step_seguir": {
                    "started_at": "2026-11-19T21:15:05Z",
                    "completed_at": "2026-11-19T21:25:00Z",
                    "conclusion": "success",
                },
            }],
        }]
        (run_job, motivo) = rvs.map_clip_to_job(clip, runs)
        self.assertEqual(motivo, "")
        self.assertEqual(run_job[1]["streamer"], "kaicenat")

    def test_no_job_covering(self):
        clip = _clip(created_at=1795200000000)  # fuera de la ventana
        (run_job, motivo) = rvs.map_clip_to_job(clip, _fake_runs())
        self.assertIsNone(run_job)
        self.assertIn("ningún cut-job", motivo)

    def test_clip_created_before_step_does_not_map(self):
        # Clip 13: el clip se creó DURANTE el setup del job (el
        # job empezó a las 21:15:03Z pero el paso "Seguir directo"
        # no empezó hasta las 21:20:05Z). El clip no puede ser de
        # ese job (el paso aún no cortaba): lo cortó uno anterior
        # y su fila se creó tarde. Con la ventana del job (viejo)
        # mapearía mal; con la del paso, no mapea y entra el
        # fallback.
        runs = [{
            "run_id": 111,
            "created_at": "2026-11-19T21:00:00Z",
            "cuts": [{
                "job_id": 1,
                "name": "Cortar en vivo (noni)",
                "streamer": "noni",
                "conclusion": "success",
                "started_at": "2026-11-19T21:15:03Z",
                "completed_at": "2026-11-19T21:25:09Z",
                "step_seguir": {
                    "started_at": "2026-11-19T21:20:05Z",
                    "completed_at": "2026-11-19T21:25:00Z",
                    "conclusion": "success",
                },
            }],
        }]
        # Clip creado a las 21:17:00Z (durante el setup).
        clip = _clip(created_at=1795123020000)
        (run_job, motivo) = rvs.map_clip_to_job(clip, runs)
        self.assertIsNone(run_job)
        self.assertIn("ningún cut-job", motivo)

    def test_ambiguous_jobs_not_guessed(self):
        runs = _fake_runs()
        # Dos jobs de noni que cubren el mismo created_at.
        runs[0]["cuts"].append(dict(runs[0]["cuts"][0], job_id=3))
        (run_job, motivo) = rvs.map_clip_to_job(_clip(), runs)
        self.assertIsNone(run_job)
        self.assertIn("ambiguo", motivo)

    def test_t0_from_step(self):
        job = _fake_runs()[0]["cuts"][0]
        t0, aprox = rvs.t0_from_job(job)
        # Paso a las 21:15:05Z + 10 s = 21:15:15Z.
        self.assertAlmostEqual(t0, 1795122915.0, delta=0.01)
        self.assertFalse(aprox)


class TestConfidence(unittest.TestCase):
    def test_thresholds(self):
        self.assertEqual(
            rvs.confidence({"correlation": 0.9, "ratio": 30.0}), "alta")
        self.assertEqual(
            rvs.confidence({"correlation": 0.5, "ratio": 5.0}), "media")
        self.assertEqual(
            rvs.confidence({"correlation": 0.018, "ratio": 1.2}), "dudosa")
        # Por debajo de 0,85 el ratio sí importa.
        self.assertEqual(
            rvs.confidence({"correlation": 0.7, "ratio": 2.0}), "dudosa")

    def test_high_corr_is_alta_despite_low_ratio(self):
        # Clip 14: corr 0,984, ratio 2,97 (música en bucle). La
        # forma de onda coincide casi muestra a muestra: es un
        # match aunque el ratio sea bajo.
        self.assertEqual(
            rvs.confidence({"correlation": 0.984, "ratio": 2.97}),
            "alta")
        self.assertEqual(
            rvs.confidence({"correlation": 0.85, "ratio": 1.5}),
            "alta")
        # Por debajo de 0,85 el ratio sí importa.
        self.assertEqual(
            rvs.confidence({"correlation": 0.7, "ratio": 2.0}),
            "dudosa")


class TestTwitchLogin(unittest.TestCase):
    def test_known_streamers(self):
        login, _ = rvs.twitch_login("Kai Cenat")
        self.assertEqual(login, "kaicenat")
        login, _ = rvs.twitch_login("DjMaRiiO")
        self.assertEqual(login, "djmariio")
        login, _ = rvs.twitch_login("Rubius")
        self.assertEqual(login, "rubius")

    def test_non_twitch_returns_none(self):
        login, motivo = rvs.twitch_login("N3ON")
        self.assertIsNone(login)
        self.assertIn("no es de Twitch", motivo)

    def test_unknown_normalized_with_note(self):
        login, nota = rvs.twitch_login("Streamer Nuevo")
        self.assertEqual(login, "streamernuevo")
        self.assertIn("supuesto", nota)


class TestFindCoveringVod(unittest.TestCase):
    def test_covering_vod_found(self):
        cache = {"noni": [
            {"vod_id": "111", "created_at": 1795082467.0,
             "duration": 41240.0, "title": "directo"},
            {"vod_id": "222", "created_at": 1795000000.0,
             "duration": 10000.0, "title": "viejo"},
        ]}
        vod, motivo = rvs.find_covering_vod(
            "noni", 1795123145.0, cache)
        self.assertEqual(motivo, "")
        self.assertEqual(vod["vod_id"], "111")

    def test_no_covering_vod(self):
        cache = {"noni": [
            {"vod_id": "222", "created_at": 1795000000.0,
             "duration": 10000.0, "title": "viejo"},
        ]}
        vod, motivo = rvs.find_covering_vod(
            "noni", 1795123145.0, cache)
        self.assertIsNone(vod)
        self.assertIn("ningún VOD", motivo)

    def test_vod_list_fetched_and_cached(self):
        cache = {}
        fake = [{"vod_id": "333", "title": "directo"}]
        meta = {"vod_id": "333", "created_at": 1795082467.0,
                "duration": 41240.0, "title": "directo"}
        with mock.patch.object(rvs, "_vod_entries", return_value=fake), \
                mock.patch.object(rvs, "_vod_meta", return_value=meta):
            vod, motivo = rvs.find_covering_vod(
                "noni", 1795123145.0, cache)
        self.assertEqual(vod["vod_id"], "333")
        self.assertIn("noni", cache)

    def test_ytdlp_timeout_returns_none(self):
        # Un timeout de yt-dlp no tumba el batch: devuelve None y
        # el clip queda dudoso (reintentable), no sin VOD.
        import subprocess as sp

        def boom(*a, **k):
            raise sp.TimeoutExpired(cmd="yt-dlp", timeout=120)

        with mock.patch.object(rvs.subprocess, "run", boom), \
                mock.patch.object(rvs.shutil, "which",
                                  return_value="/usr/bin/yt-dlp"):
            self.assertIsNone(
                rvs._ytdlp_json("https://www.twitch.tv/kaicenat/videos"))
            self.assertIsNone(rvs._vod_entries("kaicenat"))

    def test_vod_entries_limits_to_recent(self):
        # Solo los 15 VODs más recientes: la lista completa de un
        # canal grande (kaicenat) tarda >120 s.
        seen = {}

        def fake(url, timeout=120, extra_args=None):
            seen["timeout"] = timeout
            seen["extra"] = extra_args
            return {"entries": []}

        with mock.patch.object(rvs, "_ytdlp_json", side_effect=fake):
            self.assertEqual(rvs._vod_entries("kaicenat"), [])
        self.assertEqual(seen["extra"],
                         ["--flat-playlist", "--playlist-end", "15"])
        self.assertGreaterEqual(seen["timeout"], 300)

    def test_vod_id_v_prefix_stripped(self):
        # yt-dlp devuelve "v2907187856" en la lista; el id numérico
        # es el que va en D1/source_id.
        fake_list = {"entries": [
            {"id": "v2907187856", "title": "directo"},
            {"id": 2933696686, "title": "otro"},
        ]}
        fake_meta = {"id": "2907187856", "timestamp": 1795082467.0,
                     "duration": 41240.0, "title": "directo"}

        def fake_ytdlp(url, timeout=120, extra_args=None):
            if url.endswith("/videos"):
                return fake_list
            return fake_meta

        with mock.patch.object(rvs, "_ytdlp_json",
                               side_effect=fake_ytdlp):
            entries = rvs._vod_entries("noni")
            meta = rvs._vod_meta("v2907187856")
        self.assertEqual(entries[0]["vod_id"], "2907187856")
        self.assertEqual(entries[1]["vod_id"], "2933696686")
        self.assertEqual(meta["vod_id"], "2907187856")


class TestRecoverOne(unittest.TestCase):
    def _ctx(self, tmp):
        return {
            "runs": _fake_runs(),
            "vod_cache": {"noni": [
                {"vod_id": "2933696686",
                 "created_at": 1795082467.0,
                 "duration": 41240.0, "title": "directo"},
            ]},
            "streamers": None,
            "work_dir": tmp,
            "clip_video": "r2",
            "bridge": None,
            "keep_files": False,
        }

    def test_sin_vod_when_no_covering(self):
        with tempfile.TemporaryDirectory() as tmp:
            ctx = self._ctx(tmp)
            ctx["vod_cache"] = {"noni": [
                {"vod_id": "222", "created_at": 1795000000.0,
                 "duration": 10000.0, "title": "viejo"},
            ]}
            rec = rvs.recover_one(_clip(), ctx)
        self.assertEqual(rec["outcome"], "sin_vod")
        self.assertNotIn("source_id", rec)

    def test_recuperado_high_confidence(self):
        with tempfile.TemporaryDirectory() as tmp:
            ctx = self._ctx(tmp)
            match = {"lag": 43.95, "correlation": 0.89,
                     "second_best": 0.026, "ratio": 34.2,
                     "offset": 40543.95}
            with mock.patch.object(
                    rvs, "download_clip_video_r2",
                    return_value=os.path.join(tmp, "c.mp4")), \
                    mock.patch.object(
                        rvs, "download_vod_audio",
                        return_value=os.path.join(tmp, "v.mp4")), \
                    mock.patch.object(
                        rvs, "match_audio", return_value=match):
                rec = rvs.recover_one(_clip(), ctx)
        self.assertEqual(rec["outcome"], "recuperado")
        self.assertEqual(rec["confianza"], "alta")
        self.assertEqual(rec["source_id"], 2933696686)
        self.assertEqual(rec["start_sec"], 40544)
        self.assertEqual(rec["end_sec"], 40584)

    def test_dudoso_low_confidence_not_in_map(self):
        with tempfile.TemporaryDirectory() as tmp:
            ctx = self._ctx(tmp)
            match = {"lag": 50.0, "correlation": 0.02,
                     "second_best": 0.015, "ratio": 1.2,
                     "offset": 40550.0}
            with mock.patch.object(
                    rvs, "download_clip_video_r2",
                    return_value=os.path.join(tmp, "c.mp4")), \
                    mock.patch.object(
                        rvs, "download_vod_audio",
                        return_value=os.path.join(tmp, "v.mp4")), \
                    mock.patch.object(
                        rvs, "match_audio", return_value=match):
                rec = rvs.recover_one(_clip(), ctx)
        self.assertEqual(rec["outcome"], "dudoso")
        self.assertEqual(rec["confianza"], "dudosa")
        self.assertNotIn("source_id", rec)

    def test_fallback_late_row_tries_jobs_in_vod(self):
        # Clip 15: la fila se creó 2 h después de terminar el
        # directo (ningún job cubre created_at). El fallback
        # prueba los jobs del streamer dentro del último VOD y
        # el match de audio decide cuál es el bueno.
        with tempfile.TemporaryDirectory() as tmp:
            ctx = self._ctx(tmp)
            # El VOD terminó antes de la creación del clip.
            ctx["vod_cache"] = {"noni": [
                {"vod_id": "2933696686",
                 "created_at": 1795082467.0,
                 "duration": 41240.0, "title": "directo"},
            ]}
            # Dos jobs de noni dentro del VOD; el primero no casa,
            # el segundo sí.
            ctx["runs"] = [{
                "run_id": 111,
                "created_at": "2026-11-19T21:00:00Z",
                "cuts": [
                    {
                        "job_id": 1,
                        "name": "Cortar en vivo (noni)",
                        "streamer": "noni",
                        "conclusion": "success",
                        "started_at": "2026-11-19T21:15:03Z",
                        "completed_at": "2026-11-19T21:25:09Z",
                        "step_seguir": {
                            "started_at": "2026-11-19T21:15:05Z",
                            "completed_at": "2026-11-19T21:25:00Z",
                            "conclusion": "success",
                        },
                    },
                    {
                        "job_id": 2,
                        "name": "Cortar en vivo (noni)",
                        "streamer": "noni",
                        "conclusion": "success",
                        "started_at": "2026-11-19T21:20:03Z",
                        "completed_at": "2026-11-19T21:30:09Z",
                        "step_seguir": {
                            "started_at": "2026-11-19T21:20:05Z",
                            "completed_at": "2026-11-19T21:30:00Z",
                            "conclusion": "success",
                        },
                    },
                ],
            }]
            # Clip creado a las 2026-11-20T00:00:00Z (tras el VOD).
            clip = _clip(created_at=1795132800000)
            dud = {"lag": 50.0, "correlation": 0.02,
                   "second_best": 0.015, "ratio": 1.2,
                   "offset": 40550.0}
            good = {"lag": 43.95, "correlation": 0.89,
                    "second_best": 0.026, "ratio": 34.2,
                    "offset": 40543.95}
            with mock.patch.object(
                    rvs, "download_clip_video_r2",
                    return_value=os.path.join(tmp, "c.mp4")), \
                    mock.patch.object(
                        rvs, "download_vod_audio",
                        return_value=os.path.join(tmp, "v.mp4")), \
                    mock.patch.object(
                        rvs, "match_audio", side_effect=[dud, good]):
                rec = rvs.recover_one(clip, ctx)
        self.assertEqual(rec["outcome"], "recuperado")
        self.assertEqual(rec["confianza"], "alta")
        self.assertEqual(rec["source_id"], 2933696686)
        self.assertEqual(rec["intentos"], 2)


class TestFetchRuns(unittest.TestCase):
    def test_fetch_runs_parses_jobs_and_steps(self):
        runs_page = {
            "workflow_runs": [{
                "id": 111,
                "created_at": "2026-11-19T21:00:00Z",
                "conclusion": "success",
                "event": "repository_dispatch",
                "head_sha": "abc1234",
            }],
        }
        jobs_page = {
            "jobs": [{
                "id": 1,
                "name": "Cortar en vivo (noni)",
                "conclusion": "success",
                "started_at": "2026-11-19T21:15:03Z",
                "completed_at": "2026-11-19T21:25:09Z",
                "steps": [{
                    "name": "Seguir directo y cortar picos",
                    "started_at": "2026-11-19T21:15:05Z",
                    "completed_at": "2026-11-19T21:25:00Z",
                    "conclusion": "success",
                }],
            }, {
                "id": 2,
                "name": "Detectar en vivo en GTA VI",
                "conclusion": "success",
                "started_at": "2026-11-19T21:00:00Z",
                "completed_at": "2026-11-19T21:01:00Z",
                "steps": [],
            }],
        }

        def fake_run(cmd, **kw):
            class R:
                returncode = 0
                stderr = ""
                stdout = json.dumps(
                    runs_page if "runs?" in cmd[2] else jobs_page)
            return R()

        with mock.patch.object(rvs.subprocess, "run", fake_run):
            runs = rvs.fetch_runs_via_gh(
                "2026-11-19T20:00:00Z", "2026-11-19T22:00:00Z")
        self.assertEqual(len(runs), 1)
        self.assertEqual(runs[0]["run_id"], 111)
        self.assertEqual(len(runs[0]["cuts"]), 1)
        self.assertEqual(runs[0]["cuts"][0]["streamer"], "noni")
        self.assertEqual(
            runs[0]["cuts"][0]["step_seguir"]["started_at"],
            "2026-11-19T21:15:05Z")

    def test_runs_for_clips_uses_clip_range(self):
        seen = {}

        def fake_fetch(desde, hasta):
            seen["desde"] = desde
            seen["hasta"] = hasta
            return []

        with mock.patch.object(rvs, "fetch_runs_via_gh", fake_fetch):
            rvs.runs_for_clips([_clip()])
        # Clip a las 21:19:03Z del 19 nov: rango ±1 día.
        self.assertEqual(seen["desde"], "2026-11-18T21:19:03Z")
        self.assertEqual(seen["hasta"], "2026-11-20T21:19:03Z")


class TestDownloadVodAudio(unittest.TestCase):
    def test_collision_overwrite_flag(self):
        # Sin --collision Overwrite, TwitchDownloaderCLI pregunta
        # [O]/[R]/[E] si el archivo existe y se cuelga esperando
        # la entrada (el subproceso no tiene stdin).
        seen = {}

        def fake_run(cmd, **kw):
            seen["cmd"] = cmd
            # Simular una descarga correcta: crear el archivo.
            out_i = cmd.index("-o") + 1
            with open(cmd[out_i], "wb") as f:
                f.write(b"nuevo")

            class R:
                returncode = 0
                stderr = ""
                stdout = ""
            return R()

        out = os.path.join(tempfile.gettempdir(), "vod_test.mp4")
        with open(out, "wb") as f:
            f.write(b"viejo")
        with mock.patch.object(rvs.subprocess, "run", fake_run), \
                mock.patch.object(rvs, "_twitch_cli",
                                  return_value="/usr/bin/TwitchDownloaderCLI"):
            rvs.download_vod_audio("123", 100, 200, out)
        self.assertIn("--collision", seen["cmd"])
        self.assertIn("Overwrite", seen["cmd"])
        # El archivo viejo se borra antes de descargar (el
        # contenido es el nuevo, no el viejo).
        with open(out, "rb") as f:
            self.assertEqual(f.read(), b"nuevo")
        os.remove(out)


class TestDownloadVodAudioHls(unittest.TestCase):
    """Fallback HLS (VOD enorme, caso clip 24 / VOD 2932305259 de
    40,5 h: TwitchDownloaderCLI se cuelga en Fetching Video
    Info). Se baja solo los segmentos .ts que cubren el rango."""

    _PLAYLIST = """#EXTM3U
#EXT-X-VERSION:3
#EXT-X-TARGETDURATION:10
#EXT-X-PROGRAM-DATE-TIME:2026-01-01T00:00:00.000Z
#EXTINF:10.000,
0.ts
#EXT-X-PROGRAM-DATE-TIME:2026-01-01T00:00:10.000Z
#EXTINF:10.000,
1.ts
#EXT-X-PROGRAM-DATE-TIME:2026-01-01T00:00:20.000Z
#EXTINF:10.000,
2.ts
#EXT-X-PROGRAM-DATE-TIME:2026-01-01T00:00:30.000Z
#EXTINF:10.000,
3.ts
#EXT-X-PROGRAM-DATE-TIME:2026-01-01T00:00:40.000Z
#EXTINF:10.000,
4.ts
"""

    def _fake_curl(self, url, max_time=60):
        if url.endswith(".m3u8"):
            return self._PLAYLIST.encode()
        name = url.rsplit("/", 1)[-1]
        return b"SEG-" + name.encode()

    def test_hls_downloads_only_covering_segments(self):
        class FakeRun:
            returncode = 0
            stderr = ""
            stdout = "https://cdn.example.com/x/audio_only/index.m3u8\n"

        out = os.path.join(tempfile.gettempdir(), "vod_hls_test.ts")
        with mock.patch.object(rvs.subprocess, "run",
                               return_value=FakeRun()), \
                mock.patch.object(rvs, "_ytdlp",
                                  return_value="/usr/bin/yt-dlp"), \
                mock.patch.object(rvs, "_curl_get",
                                  side_effect=self._fake_curl):
            path, seg_start = rvs.download_vod_audio_hls(
                "999", 15, 35, out)
        self.assertEqual(path, out)
        # Segmentos 1, 2, 3 (cubren [15, 35]); el 0 y el 4 no.
        with open(out, "rb") as f:
            data = f.read()
        self.assertIn(b"SEG-1.ts", data)
        self.assertIn(b"SEG-2.ts", data)
        self.assertIn(b"SEG-3.ts", data)
        self.assertNotIn(b"SEG-0.ts", data)
        self.assertNotIn(b"SEG-4.ts", data)
        # Anclaje por offset acumulado (timeline de
        # TwitchDownloaderCLI): el segmento 1 empieza a los 10 s.
        # NO por PDT menos created_at: el created_at puede ir
        # segundos después del primer segmento (clip 24: 3,7 s).
        self.assertAlmostEqual(seg_start, 10.0, delta=0.1)
        os.remove(out)

    def test_hls_range_outside_playlist_rejected(self):
        class FakeRun:
            returncode = 0
            stderr = ""
            stdout = "https://cdn.example.com/x/audio_only/index.m3u8\n"

        out = os.path.join(tempfile.gettempdir(), "vod_hls_test2.ts")
        with mock.patch.object(rvs.subprocess, "run",
                               return_value=FakeRun()), \
                mock.patch.object(rvs, "_ytdlp",
                                  return_value="/usr/bin/yt-dlp"), \
                mock.patch.object(rvs, "_curl_get",
                                  side_effect=self._fake_curl):
            with self.assertRaises(rvs.RecoverError):
                rvs.download_vod_audio_hls("999", 9000, 9100, out)

    def test_hls_fmp4_segments_with_init(self):
        # Algunos VODs usan fMP4 (.mp4 + #EXT-X-MAP init) en vez
        # de .ts (VOD 2933767960, clip 40). El init va primero o
        # el archivo no se decodifica.
        playlist = """#EXTM3U
#EXT-X-VERSION:6
#EXT-X-TARGETDURATION:10
#EXT-X-MAP:URI="init-0.mp4"
#EXT-X-PROGRAM-DATE-TIME:2026-01-01T00:00:00.000Z
#EXTINF:10.000,
0.mp4
#EXT-X-PROGRAM-DATE-TIME:2026-01-01T00:00:10.000Z
#EXTINF:10.000,
1.mp4
#EXT-X-PROGRAM-DATE-TIME:2026-01-01T00:00:20.000Z
#EXTINF:10.000,
2.mp4
"""

        class FakeRun:
            returncode = 0
            stderr = ""
            stdout = "https://cdn.example.com/x/audio_only/index.m3u8\n"

        def fake_curl(url, max_time=60):
            if url.endswith(".m3u8"):
                return playlist.encode()
            name = url.rsplit("/", 1)[-1]
            return b"SEG-" + name.encode()

        out = os.path.join(tempfile.gettempdir(), "vod_hls_fmp4.mp4")
        with mock.patch.object(rvs.subprocess, "run",
                               return_value=FakeRun()), \
                mock.patch.object(rvs, "_ytdlp",
                                  return_value="/usr/bin/yt-dlp"), \
                mock.patch.object(rvs, "_curl_get",
                                  side_effect=fake_curl):
            path, seg_start = rvs.download_vod_audio_hls(
                "999", 12, 28, out)
        self.assertEqual(path, out)
        with open(out, "rb") as f:
            data = f.read()
        # El init va primero, luego solo los segmentos 1 y 2.
        self.assertTrue(data.startswith(b"SEG-init-0.mp4"))
        self.assertIn(b"SEG-1.mp4", data)
        self.assertIn(b"SEG-2.mp4", data)
        self.assertNotIn(b"SEG-0.mp4", data)
        self.assertAlmostEqual(seg_start, 10.0, delta=0.1)
        os.remove(out)


    def test_curl_get_uses_max_time_and_fails_hard(self):
        # _curl_get es el límite duro de reloj: urllib con timeout
        # se quedaba colgado ~10 min goteando por el proxy (clips
        # 124 y 159). curl --max-time corta pase lo que pase.
        seen = {}

        def fake_run(cmd, **kw):
            seen["cmd"] = cmd
            seen["timeout"] = kw.get("timeout")

            class R:
                returncode = 0
                stderr = b""
                stdout = b"OK"
            return R()

        with mock.patch.object(rvs.subprocess, "run", fake_run):
            out = rvs._curl_get("https://example.com/x.m3u8",
                                max_time=60)
        self.assertEqual(out, b"OK")
        self.assertIn("--max-time", seen["cmd"])
        self.assertIn("60", seen["cmd"])
        self.assertIsNotNone(seen["timeout"])

        def fake_run_fail(cmd, **kw):
            class R:
                returncode = 22
                stderr = b"HTTP 404"
                stdout = b""
            return R()

        with mock.patch.object(rvs.subprocess, "run", fake_run_fail):
            with self.assertRaises(rvs.RecoverError):
                rvs._curl_get("https://example.com/nope", max_time=30)


class TestSearchWindows(unittest.TestCase):
    def test_three_passes_up_to_180s(self):
        # Tres pasadas (±30, ±90, ±180 s) como red de seguridad por
        # si el T0 o la latencia fallan más de lo calibrado (~±15 s).
        self.assertEqual(rvs.SEARCH_HALF_S, 30)
        self.assertEqual(rvs.SEARCH_HALF_WIDE_S, 90)
        self.assertEqual(rvs.SEARCH_HALF_XWIDE_S, 180)
        self.assertGreater(rvs.SEARCH_HALF_XWIDE_S,
                           rvs.SEARCH_HALF_WIDE_S)


class TestD1Sql(unittest.TestCase):
    def test_only_recuperado_rows(self):
        records = [
            {"id": 7, "outcome": "recuperado", "streamer": "rubius",
             "title": "t", "source_id": 2932415021,
             "start_sec": 3200, "end_sec": 3248},
            {"id": 8, "outcome": "sin_vod", "streamer": "noni",
             "title": "t"},
            {"id": 9, "outcome": "dudoso", "streamer": "noni",
             "title": "t"},
        ]
        sql = rvs.build_d1_sql(records)
        self.assertIn(
            "UPDATE clips SET source_id = 2932415021, start_sec = 3200, "
            "end_sec = 3248 WHERE id = 7;", sql)
        self.assertNotIn("WHERE id = 8", sql)
        self.assertNotIn("WHERE id = 9", sql)


class TestResolveSourceWithMap(unittest.TestCase):
    def test_map_used_when_panel_has_no_source(self):
        clip = {"id": 7, "source_id": 0, "start_sec": 3200,
                "end_sec": 3248}
        source, motivo = ra.resolve_source(
            clip, {7: {"source_id": 2932415021, "start_sec": 3200,
                       "end_sec": 3248}})
        self.assertEqual(source, (2932415021, 3200, 3248))
        self.assertEqual(motivo, "")

    def test_panel_source_wins_over_map(self):
        clip = {"id": 7, "source_id": 111, "start_sec": 10,
                "end_sec": 50}
        source, _ = ra.resolve_source(
            clip, {7: {"source_id": 2932415021, "start_sec": 3200,
                       "end_sec": 3248}})
        self.assertEqual(source, (111, 10, 50))

    def test_no_source_no_map_is_sin_vod(self):
        clip = {"id": 7, "source_id": 0, "start_sec": 3200,
                "end_sec": 3248}
        source, motivo = ra.resolve_source(clip, {})
        self.assertIsNone(source)
        self.assertIn("sin source_id", motivo)


class TestClip7RealData(unittest.TestCase):
    """Calibración real (2026-11-20): clip 7, VOD 2932415021,
    start 3200. Se salta si no están los archivos de validación."""

    def test_clip7_recovers_exact(self):
        base = os.path.expanduser(
            "~/workspace/gta6-clips/runs/vod-recovery-2026-11-20")
        clip_path = os.path.join(base, "clip_7.mp4")
        vod_path = os.path.join(base, "vod_7.mp4")
        if not (os.path.isfile(clip_path) and os.path.isfile(vod_path)):
            self.skipTest("sin archivos de validación de clip 7")
        m = rvs.match_audio(clip_path, vod_path, 3100)
        self.assertAlmostEqual(m["offset"], 3200.0, delta=0.5)
        self.assertGreaterEqual(m["correlation"], 0.9)
        self.assertEqual(rvs.confidence(m), "alta")


if __name__ == "__main__":
    unittest.main()

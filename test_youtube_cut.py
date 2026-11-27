#!/usr/bin/env python3
"""Pruebas del corte automático de clips desde YouTube
(SPEC-2026-11-24-corte-automatico-youtube.md, v8 aprobada).

Sin red ni panel: yt-dlp, transcripción, RMS, segmento, render
y validación van mockeados; el SEO y la viralidad son reales
(motor local y fórmula intacta).

- is_youtube_url: youtube.com / www / m / youtu.be sí, twitch no.
- plan_audio_samples: URL única o <= 30 min -> audio completo;
  barrido < 1 h -> cada 120 s; >= 1 h -> 300 s con tope de 24
  muestras uniformes (t_i = (i+0,5) x duración/24).
- hype_points / rms_buckets_10s / audio_multiple: fixtures
  deterministas (grito 3,3x pasa; plano 1,0x no pasa; silencio
  con mediana 0 -> 1,0 y descarte).
- build_audio_input + score_clip: el dict se LLAMA con
  score_clip (no solo se comprueba su forma): words es NÚMERO
  (la lista revienta con TypeError), detail=None -> chat 5,0,
  total 47-55, filter_passed false, peso de audio 1,0.
- audio_peak_gate: >= 2,0 + hype > 0 o LLM.
- run_youtube_clips con yt-dlp mockeado: contrato completo del
  manifiesto (viral.best_title no vacío, viral.viral_multiple,
  source_key, vod_id, vod_layout, n, title_es/title_en,
  segmentos en segments/), manifiesto en disco, gates y
  burn_and_enqueue con bridge mockeado y título no vacío en el
  POST, seo_scores del POST como números planos.
- download_audio: stderr «max-filesize» -> AudioTooLarge
  (muestreo, no fallo); otro fallo -> YoutubeError.
- audio completo > 50 MB -> camino de muestreo.
- rescate: ningún clip de YouTube cae en 40-44 (mínimo 47).
"""

import json
import os
import sys
import tempfile
import types
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from clipagent import llm  # noqa: E402
from clipagent.detect import score_clip  # noqa: E402
from clipagent.vod_fallback import (  # noqa: E402
    burn_and_enqueue,
    clip_passes_gates,
    panel_score,
)
from clipagent.youtube import is_youtube_url  # noqa: E402
from clipagent.youtube_cut import (  # noqa: E402
    AudioTooLarge,
    audio_multiple,
    audio_peak_gate,
    build_audio_input,
    download_audio,
    fetch_youtube_metadata,
    hype_points,
    plan_audio_samples,
    rms_buckets_10s,
    run_youtube_clips,
    score_youtube_clip,
)

VIDEO_ID = "NrNdw_Y6_bI"
VIDEO_URL = f"https://youtu.be/{VIDEO_ID}"
DURATION = 1750  # 29 min 10 s, el vídeo de aceptación


def _seo(score):
    return {p: {"score": score, "status": "ok", "can_publish": True}
            for p in ("youtube_shorts", "tiktok", "instagram_reels",
                      "facebook_reels")}


def _words(text, start=0.0):
    """Palabras con timestamps de un texto (formato Whisper)."""
    out = []
    t = start
    for word in text.split():
        out.append({"word": word, "start": round(t, 2),
                    "end": round(t + 0.3, 2)})
        t += 0.4
    return out


def _transcript(windows):
    """windows: [(inicio_s, texto)] -> transcript faster-whisper."""
    segments = []
    for start, text in windows:
        segments.append({
            "start": float(start), "end": float(start) + 28.0,
            "text": text, "words": _words(text, start)})
    return {"segments": segments, "language": "es"}


def _full_transcript():
    """Transcripción del vídeo completo: 58 ventanas de 30 s;
    solo la de 600 s tiene risas/hype."""
    windows = []
    for i in range(58):
        start = i * 30
        if start == 600:
            text = ("jaja qué jugada brutal increíble vamos "
                    "no lo puedo creer")
        else:
            text = "hablando normal del juego y del directo"
        windows.append((start, text))
    return _transcript(windows)


def _segment_transcript():
    """Transcripción del segmento: 60 palabras (audio >= 50 -> +1).

    Texto realista (2026-11-26, spec títulos-verdad): antes eran
    60 «palabraN» genéricas, lo que valía cuando el título no leía
    la transcripción; ahora el paquete Gemini se ancla al momento
    y el título falso debe nombrar la persecución de 6 estrellas.
    El conteo (60) no cambia: el score 54,4 queda intacto."""
    text = ("rubius escapa de la policía en el aeropuerto con seis "
            "estrellas encima y el helicóptero viene por detrás no "
            "hay forma de salir de aquí el coche ya no corre más "
            "mira el mapa está en rojo todo el mundo me busca vamos "
            "vamos que nos pillan en la pista vamos vamos vamos "
            "corre corre corre no pares sigue sigue")
    return {"segments": [{"start": 0.0, "end": 49.0, "text": text,
                          "words": _words(text, 0.0)}]}


class FakeLLM(llm.LLMClient):
    """LLM de pruebas: devuelve el paquete que se le pasa."""

    def __init__(self, payload):
        self.payload = payload

    def available(self):
        return True

    def generate(self, prompt):
        return json.dumps(self.payload)


# Paquete Gemini falso para el camino VOD (spec títulos-verdad):
# título anclado a la transcripción del segmento (persecución de
# 6 estrellas en el aeropuerto), descripción con el juego y cierre
# con pregunta, 4 hashtags de contenido + #Shorts, miniatura con
# sentido. Sin él el paquete queda retenido (Gemini -> retención).
YT_PACKAGE = {
    "title": "GTA 6: Rubius escapa de la policía en el aeropuerto",
    "description": "Rubius escapa de la policía en el aeropuerto "
                   "de GTA 6 con seis estrellas encima. ¿Crees que "
                   "logra salir?",
    "hashtags": ["#GTA6", "#Rubius", "#Policia", "#Aeropuerto",
                 "#Shorts"],
    "thumbnail_text": "ESCAPA DEL AEROPUERTO",
}


def _rms_full():
    """RMS del audio completo: -30 dBFS con un grito de 5 s a
    -15 dBFS en 600-604 s (bucket 600: 3,31x sobre la mediana)."""
    values = [-30.0] * DURATION
    for s in range(600, 605):
        values[s] = -15.0
    return [(s, v) for s, v in enumerate(values)]


def _rms_segment():
    """RMS del segmento: 25 s a -40 y 25 s a -15 (std 12,5 dB,
    rango 25 dB -> audio 9,375 con 60 palabras -> total 54,4)."""
    return [(s, -40.0 if s < 25 else -15.0) for s in range(50)]


def _rms_flat():
    """Audio plano: 30 s a -30 dBFS (std 0, estables 100%)."""
    return [(s, -30.0) for s in range(30)]


def _rms_short():
    """RMS de un vídeo de 50 s: -30 dBFS con un grito de 5 s a
    -15 dBFS en 10-14 s (bucket 10: 3,31x sobre la mediana)."""
    values = [-30.0] * 50
    for s in range(10, 15):
        values[s] = -15.0
    return [(s, v) for s, v in enumerate(values)]


def _transcript_short():
    """Transcripción de un vídeo de 50 s con risas/hype."""
    text = "jaja qué jugada brutal increíble vamos"
    return {"segments": [{"start": 0.0, "end": 49.0, "text": text,
                          "words": _words(text, 0.0)}]}


def _rms_silence():
    """Silencio: -90 dBFS (lineal 0 -> mediana 0)."""
    return [(s, -90.0) for s in range(30)]


def _meta(**over):
    base = {
        "id": VIDEO_ID,
        "duration": DURATION,
        "title": "Conseguí 6 Estrellas en GTA 6 ⭐",
        "channel_url": "https://www.youtube.com/channel/"
                       "UCcjIvuxmWlS5IEQ0JdPV4Ng",
        "channel": "Rubius Z",
        "description": ("Rubius consigue las 6 estrellas en GTA 6 "
                        "y la policía lo persigue por todo el "
                        "aeropuerto. El mejor momento del directo "
                        "de GTA 6."),
        "heatmap": [],
        "views": 3530194,
        "likes": 62831,
        "categories": [],
        "category": "",
    }
    base.update(over)
    return base


class FakeBridge:
    """Puente falso: registra POST / PATCH / subida / preview."""

    def __init__(self, clips=None, influencers=None):
        self.clips = list(clips or [])
        self.influencers = list(influencers or [])
        self.created = []
        self.uploaded = []
        self.patched = []
        self.previews = []
        self._next_id = 100

    def list_clips(self):
        return list(self.clips)

    def list_influencers(self):
        return list(self.influencers)

    def create_clip(self, payload):
        self.created.append(payload)
        self._next_id += 1
        clip = {"id": self._next_id}
        self.clips.append({
            "source_id": payload.get("source_id"),
            "source_key": payload.get("source_key", ""),
            "start_sec": payload.get("start_sec"),
            "title": payload.get("title"),
            "influencer_id": 7,
            "created_at": 1795532400000,
            "platforms": list(payload.get("platforms") or [])})
        return clip

    def upload_video(self, clip_id, video_path):
        self.uploaded.append((clip_id, video_path))
        return {"ok": True}

    def update_clip(self, clip_id, fields):
        self.patched.append((clip_id, fields))
        return {"ok": True}

    def preview_seen(self, clip_id):
        self.previews.append(clip_id)
        return {"ok": True}


def _patch_burn():
    """Mocks de subtitle_clip + validate_final_clip para
    burn_and_enqueue (como en test_vod_fallback)."""
    sub = mock.patch(
        "clipagent.workers.subtitle_clip",
        return_value={"publish_blocked": False, "no_speech": False,
                      "srt": "", "burn": "", "transcript": None})
    sub.start()
    verdict = {"decision": "aprobado", "pasa": True,
               "hard_pasa": True, "needs_rerender": False,
               "rerender_reason": "", "hard_failures": [],
               "motivo": "", "resumen": "aprobado",
               "fallos": [], "scores": {}, "frames_n": 0,
               "model": "", "transcript_words": 0,
               "observe_only": True,
               "face": {"pasa": True}, "template": {"pasa": True},
               "gemini": {"decision": "aprobado"}}
    review = mock.patch(
        "clipagent.final_review.validate_final_clip",
        return_value=verdict)
    review.start()
    return sub, review


class TestIsYoutubeUrl(unittest.TestCase):
    def test_hosts_si(self):
        for url in ("https://www.youtube.com/watch?v=NrNdw_Y6_bI",
                    "https://youtube.com/watch?v=NrNdw_Y6_bI",
                    "https://m.youtube.com/watch?v=NrNdw_Y6_bI",
                    "https://youtu.be/NrNdw_Y6_bI"):
            self.assertTrue(is_youtube_url(url), url)

    def test_otros_no(self):
        for url in ("https://www.twitch.tv/videos/2932415021",
                    "https://kick.com/rubius",
                    "https://example.com/watch?v=x",
                    "", None):
            self.assertFalse(is_youtube_url(url), url)


class TestPlanAudioSamples(unittest.TestCase):
    def test_url_unica_o_corto_audio_completo(self):
        for duration, single in ((1750, True), (1750, False),
                                 (900, False), (1800, False)):
            plan = plan_audio_samples(duration, single)
            self.assertEqual(plan["mode"], "full", (duration, single))
            self.assertEqual(plan["starts"], [])

    def test_barrido_corto_cada_120s(self):
        plan = plan_audio_samples(2700, False)
        self.assertEqual(plan["mode"], "sample")
        self.assertEqual(plan["starts"],
                         list(range(0, 2700, 120)))

    def test_barrido_6h_tope_24_uniformes(self):
        plan = plan_audio_samples(6 * 3600, False)
        self.assertEqual(plan["mode"], "sample")
        starts = plan["starts"]
        self.assertEqual(len(starts), 24)
        # Uniformes por toda la duración, no las primeras 24:
        # t_i = (i+0,5) x duración/24.
        self.assertEqual(starts[0], 450)
        self.assertEqual(starts[-1], 21150)
        self.assertEqual(starts[1] - starts[0], 900)

    def test_barrido_2h_cada_300s(self):
        plan = plan_audio_samples(2 * 3600, False)
        self.assertEqual(plan["mode"], "sample")
        self.assertEqual(len(plan["starts"]), 24)
        self.assertEqual(plan["starts"][0], 0)
        self.assertEqual(plan["starts"][-1], 6900)


class TestHypePoints(unittest.TestCase):
    def test_risas_y_hype_cuentan(self):
        points, risas, hype = hype_points(
            "jaja qué jugada brutal increíble")
        self.assertGreater(risas, 0)
        self.assertGreater(hype, 0)
        self.assertEqual(points, risas + hype)

    def test_texto_normal_cero(self):
        self.assertEqual(
            hype_points("hablando normal del juego y del directo"),
            (0, 0, 0))


class TestAudioMultiple(unittest.TestCase):
    def test_grito_3x_sobre_mediana(self):
        buckets = rms_buckets_10s(_rms_full())
        mult, peak, median = audio_multiple(buckets, 590, 640)
        self.assertAlmostEqual(mult, 3.31, places=1)
        self.assertGreater(mult, 2.0)
        self.assertAlmostEqual(median, 10 ** (-30 / 20), places=4)

    def test_plano_1x(self):
        buckets = rms_buckets_10s(_rms_flat())
        mult, _peak, _med = audio_multiple(buckets, 0, 30)
        self.assertAlmostEqual(mult, 1.0, places=2)

    def test_mediana_0_da_1_y_descarta(self):
        buckets = rms_buckets_10s(_rms_silence())
        mult, _peak, median = audio_multiple(buckets, 0, 30)
        self.assertEqual(median, 0.0)
        self.assertEqual(mult, 1.0)
        ok, _why = audio_peak_gate(mult, 5)
        self.assertFalse(ok)

    def test_sin_buckets_en_ventana_da_1(self):
        buckets = rms_buckets_10s(_rms_flat())
        mult, _peak, _med = audio_multiple(buckets, 100, 130)
        self.assertEqual(mult, 1.0)


class TestAudioInputAndAdapter(unittest.TestCase):
    def test_words_es_numero_no_lista(self):
        audio = build_audio_input(_rms_flat(), _segment_transcript())
        self.assertIsInstance(audio["words"], int)
        self.assertEqual(audio["words"], 60)
        self.assertIn("stats", audio)

    def test_score_clip_con_el_dict_construido(self):
        # El test LLAMA a score_clip con el dict construido (un
        # test de forma no cazaría el crash de la lista).
        audio = build_audio_input(_rms_flat(), _segment_transcript())
        result = score_clip(detail=None, chat_multiple=None,
                            audio=audio, platform=None)
        # Plano (std 0, estables 100%) -> 2,0; 60 palabras -> +1.
        self.assertEqual(
            result["components"]["audio"]["value"], 3.0)
        self.assertEqual(result["components"]["chat"]["value"], 5.0)
        self.assertEqual(result["total"], 48.0)
        self.assertEqual(result["audio_weight"], 1.0)
        self.assertFalse(result["filter_passed"])

    def test_pasar_la_lista_revienta(self):
        # Bloqueador B4 de la vuelta 3: transcript_words devuelve
        # LISTA y audio_component espera un NÚMERO.
        from clipagent.audio import transcript_words

        words = transcript_words(_segment_transcript())
        self.assertIsInstance(words, list)
        with self.assertRaises(TypeError):
            score_clip(detail=None, chat_multiple=None,
                       audio={"stats": {"std_db": 12.0,
                                        "steady_frac": 0.1,
                                        "range_db": 20.0},
                              "words": words},
                       platform=None)

    def test_total_47_55_y_sin_rescate(self):
        # Rescate por gritos NO aplica a YouTube: el total es
        # siempre >= 47 (audio medido 2-10); ningún clip cae en
        # la banda de rescate 40-44.
        flat = score_youtube_clip(_rms_flat(), {"segments": []}, 1.0)
        self.assertEqual(flat[0]["total"], 47.0)
        dyn = score_youtube_clip(_rms_segment(),
                                 _segment_transcript(), 3.31)
        self.assertEqual(dyn[0]["total"], 54.4)
        self.assertEqual(dyn[1]["audio_multiple"], 3.31)
        for total in (flat[0]["total"], dyn[0]["total"]):
            self.assertGreaterEqual(total, 47)
            self.assertLessEqual(total, 55)
            self.assertFalse(40 <= total <= 44)

    def test_sin_rms_no_se_estima_se_descarta(self):
        with self.assertRaises(ValueError):
            score_youtube_clip([], _segment_transcript(), 1.0)


class TestAudioPeakGate(unittest.TestCase):
    def test_pasa_con_pico_y_hype(self):
        self.assertTrue(audio_peak_gate(2.5, 3)[0])
        self.assertTrue(audio_peak_gate(2.0, 1)[0])

    def test_pico_sin_hype_no_pasa_sin_llm(self):
        ok, why = audio_peak_gate(3.0, 0)
        self.assertFalse(ok)
        self.assertIn("hype", why)

    def test_pico_sin_hype_pasa_con_llm(self):
        self.assertTrue(audio_peak_gate(3.0, 0, llm_selected=True)[0])

    def test_sin_pico_no_pasa_aunque_haya_hype(self):
        self.assertFalse(audio_peak_gate(1.5, 5)[0])


class TestDownloadAudio(unittest.TestCase):
    def _runner(self, stderr, returncode=1):
        def _run(cmd):
            return types.SimpleNamespace(
                returncode=returncode, stdout="", stderr=stderr)
        return _run

    def test_max_filesize_es_audio_demasiado_grande(self):
        with self.assertRaises(AudioTooLarge):
            download_audio(
                VIDEO_URL, "/tmp/noexiste.wav",
                runner=self._runner(
                    "ERROR: File is larger than max-filesize "
                    "(50.00MiB)"),
                sleep=lambda s: None)

    def test_otro_fallo_es_youtube_error(self):
        from clipagent.youtube import YoutubeError

        with self.assertRaises(YoutubeError):
            download_audio(
                VIDEO_URL, "/tmp/noexiste.wav",
                runner=self._runner("ERROR: unable to extract"),
                sleep=lambda s: None)

    def test_fetch_metadata(self):
        payload = json.dumps({
            "id": VIDEO_ID, "duration": DURATION,
            "title": "Conseguí 6 Estrellas en GTA 6 ⭐",
            "channel_url": "https://www.youtube.com/channel/UCx",
            "uploader": "Rubius Z", "description": "desc",
            "heatmap": [], "view_count": 100, "like_count": 10,
            "categories": []})

        def _run(cmd):
            return types.SimpleNamespace(
                returncode=0, stdout=payload, stderr="")

        meta = fetch_youtube_metadata(VIDEO_URL, runner=_run)
        self.assertEqual(meta["id"], VIDEO_ID)
        self.assertEqual(meta["duration"], DURATION)
        self.assertEqual(meta["channel"], "Rubius Z")


class TestRunYoutubeClips(unittest.TestCase):
    """Cortador con yt-dlp mockeado (contrato completo)."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.out = os.path.join(self.tmp.name, "out")
        self.addCleanup(self.tmp.cleanup)
        self.full_transcript = _full_transcript()
        self.segment_transcript = _segment_transcript()
        self.calls = {"audio": [], "samples": [], "segments": []}

    def _audio(self, url, out, runner=None):
        self.calls["audio"].append(url)
        with open(out, "wb") as f:
            f.write(b"fake-wav")
        return out

    def _sample(self, url, out, start_s=None, end_s=None,
                runner=None):
        self.calls["samples"].append(start_s)
        with open(out, "wb") as f:
            f.write(b"fake-wav")
        return out

    def _transcribe(self, path):
        if "full_audio" in path:
            return self.full_transcript
        if "sample_" in path:
            start = int(path.split("sample_")[1].split(".")[0])
            text = ""
            for seg in self.full_transcript["segments"]:
                if int(seg["start"]) == start:
                    text = seg["text"]
            return _transcript([(start, text)])
        return self.segment_transcript

    def _rms(self, path):
        if "full_audio" in path:
            return _rms_full()
        if "sample_" in path:
            start = int(path.split("sample_")[1].split(".")[0])
            if start == 600:
                return [(s, -15.0 if s < 5 else -30.0)
                        for s in range(30)]
            return _rms_flat()
        return _rms_segment()

    def _segment(self, url, start, end, out):
        self.calls["segments"].append((start, end))
        with open(out, "wb") as f:
            f.write(b"fake-mp4")
        return out

    def _render(self, seg_path, clip_path, duration, cfg,
                layout_mode="auto"):
        self.calls.setdefault("render_layout", []).append(layout_mode)
        with open(clip_path, "wb") as f:
            f.write(b"fake-mp4")
        return {"mode": "rubius", "segments": []}

    def _validate(self, clip_path, title, text, cfg, plantilla=None):
        self.calls.setdefault("validate_plantilla", []).append(plantilla)
        return {"decision": "aprobado"}

    def _layout(self, seg_path, duration, cfg):
        return {"vod_id": VIDEO_ID, "facecam_box": None,
                "method": None, "hits": 0, "samples": 0}

    def _run(self, **over):
        kwargs = {
            "metadata_fn": lambda url: _meta(),
            "audio_fn": self._audio,
            "sample_fn": self._sample,
            "transcribe_fn": self._transcribe,
            "rms_fn": self._rms,
            "segment_fn": self._segment,
            "render_fn": self._render,
            "validate_fn": self._validate,
            "layout_fn": self._layout,
            # Paquete Gemini falso (spec títulos-verdad): sin LLM
            # el paquete queda retenido y no sale ningún clip.
            "llm_client": FakeLLM(YT_PACKAGE),
        }
        kwargs.update(over)
        return run_youtube_clips(VIDEO_URL, self.out, top_n=2,
                                  lang="es", single_url=True,
                                  **kwargs)

    def test_contrato_completo_y_manifiesto_en_disco(self):
        manifest = self._run()
        self.assertIsNotNone(manifest)
        self.assertEqual(len(manifest["clips"]), 1)
        clip = manifest["clips"][0]
        # Contrato §3.1, campo a campo.
        self.assertEqual(clip["start"], 590)
        self.assertEqual(clip["duration"], 50)
        self.assertTrue(clip["file"].startswith("clips/clip_01_"))
        self.assertTrue(os.path.isfile(
            os.path.join(self.out, clip["file"])))
        self.assertEqual(clip["source"], "youtube")
        self.assertEqual(clip["source_key"], VIDEO_ID)
        self.assertEqual(clip["vod_id"], VIDEO_ID)
        self.assertEqual(clip["n"], 1)
        self.assertIsNotNone(clip["vod_layout"])
        self.assertEqual(clip["moment_card"]["game"], "GTA VI")
        self.assertIn("title", clip["moment_card"])
        self.assertTrue(clip["transcript"]["segments"])
        viral = clip["viral"]
        self.assertTrue(viral["best_title"])
        self.assertIn("GTA 6", viral["best_title"])
        self.assertAlmostEqual(viral["viral_multiple"], 3.31,
                               places=1)
        self.assertTrue(clip["title_es"])
        self.assertTrue(clip["title_en"])
        self.assertEqual(clip["score_kind"], "youtube_audio")
        self.assertEqual(clip["viral_breakdown"]["audio_multiple"],
                         viral["viral_multiple"])
        self.assertEqual(clip["viral_score"]["total"], 54.4)
        self.assertFalse(clip["viral_score"]["filter_passed"])
        # Segmentos en segments/ con el nombrado.
        segs = os.listdir(os.path.join(self.out, "segments"))
        self.assertEqual(segs, ["seg_01_t0h09m50s.mp4"])
        # El manifiesto EXISTE EN DISCO (no solo en memoria).
        manifest_path = os.path.join(self.out, "manifest.json")
        self.assertTrue(os.path.isfile(manifest_path))
        with open(manifest_path, encoding="utf-8") as f:
            on_disk = json.load(f)
        self.assertEqual(len(on_disk["clips"]), 1)
        self.assertEqual(on_disk["clips"][0]["viral"]["best_title"],
                         viral["best_title"])
        # Puertas: sin chat, con SEO completo, pasa.
        ok, reasons = clip_passes_gates(clip, VIDEO_ID)
        self.assertTrue(ok, reasons)
        self.assertTrue(all(
            clip["seo_scores"][p]["score"] >= 80
            for p in ("youtube_shorts", "tiktok", "instagram_reels",
                      "facebook_reels")))

    def test_audio_completo_llamado_una_vez(self):
        self._run()
        self.assertEqual(len(self.calls["audio"]), 1)
        self.assertEqual(self.calls["samples"], [])

    def test_burn_and_enqueue_con_titulo_y_viral_plano(self):
        manifest = self._run()
        clip = manifest["clips"][0]
        sub, review = _patch_burn()
        self.addCleanup(sub.stop)
        self.addCleanup(review.stop)
        bridge = FakeBridge()
        panel_id, why = burn_and_enqueue(
            bridge, "rubius", VIDEO_ID, self.out, clip)
        self.assertEqual(panel_id, 101, why)
        self.assertEqual(len(bridge.created), 1)
        payload = bridge.created[0]
        self.assertTrue(payload["title"])
        self.assertEqual(payload["source_key"], VIDEO_ID)
        self.assertEqual(payload["source_id"], 0)
        viral = payload["viral"]
        self.assertEqual(viral["viral_score"]["total"], 54.4)
        self.assertEqual(viral["seo_scores"], {
            p: clip["seo_scores"][p]["score"]
            for p in ("youtube_shorts", "tiktok", "instagram_reels",
                      "facebook_reels")})
        self.assertTrue(all(
            isinstance(v, (int, float))
            for v in viral["seo_scores"].values()))
        self.assertEqual(viral["viral_breakdown"]["audio_multiple"],
                         3.31)
        self.assertEqual(viral["priority"], panel_score(
            clip["viral_score"]["total"], clip["seo_scores"], 3.31))
        self.assertGreaterEqual(viral["priority"], 53)
        self.assertEqual(viral["score_kind"], "youtube_audio")
        # El PATCH auto-seo sigue existiendo.
        self.assertEqual(len(bridge.patched), 1)

    def test_video_corto_sin_clip(self):
        manifest = run_youtube_clips(
            VIDEO_URL, self.out, top_n=2, lang="es",
            single_url=True,
            metadata_fn=lambda url: _meta(duration=25),
            audio_fn=self._audio, transcribe_fn=self._transcribe,
            rms_fn=self._rms, segment_fn=self._segment,
            render_fn=self._render, validate_fn=self._validate,
            layout_fn=self._layout)
        self.assertEqual(manifest["clips"], [])
        self.assertTrue(os.path.isfile(
            os.path.join(self.out, "manifest.json")))

    def test_video_40_60_entero_es_el_candidato(self):
        manifest = run_youtube_clips(
            VIDEO_URL, self.out, top_n=2, lang="es",
            single_url=True,
            metadata_fn=lambda url: _meta(duration=50),
            audio_fn=self._audio,
            transcribe_fn=lambda p: _transcript_short(),
            rms_fn=lambda p: _rms_short(),
            segment_fn=self._segment,
            render_fn=self._render, validate_fn=self._validate,
            layout_fn=self._layout, llm_client=FakeLLM(YT_PACKAGE))
        self.assertEqual(len(manifest["clips"]), 1)
        clip = manifest["clips"][0]
        self.assertEqual(clip["start"], 0)
        self.assertEqual(clip["duration"], 50)

    def test_sin_webcam_render_game_y_plantilla_en_manifiesto(self):
        # Decisión §2.1 sin_webcam: el render recibe layout_mode
        # "game", la validación la plantilla y el manifiesto la
        # lleva (spec §9.1/§9.3).
        with mock.patch(
                "clipagent.template_decision.decide_template_for_segment",
                return_value=("rubius_sin_webcam", None, "firme", False)):
            manifest = run_youtube_clips(
                VIDEO_URL, self.out, top_n=2, lang="es",
                single_url=True,
                metadata_fn=lambda url: _meta(duration=50),
                audio_fn=self._audio,
                transcribe_fn=lambda p: _transcript_short(),
                rms_fn=lambda p: _rms_short(),
                segment_fn=self._segment,
                render_fn=self._render, validate_fn=self._validate,
                layout_fn=self._layout, llm_client=FakeLLM(YT_PACKAGE))
        self.assertEqual(len(manifest["clips"]), 1)
        self.assertEqual(self.calls["render_layout"], ["game"])
        self.assertEqual(
            self.calls["validate_plantilla"], ["rubius_sin_webcam"])
        self.assertEqual(
            manifest["clips"][0].get("plantilla"),
            "rubius_sin_webcam")

    def test_validate_sin_avatar_no_pierde_plantilla(self):
        # §4.2 (I-13): guarda POR KWARG — un inyectable que acepta
        # `plantilla` pero no `avatar` recibe plantilla (no se
        # pierde por la conjuntiva) y NO recibe avatar (si lo
        # recibiera, TypeError -> sin veredicto -> sin llamada).
        seen = []

        def _validate_viejo(clip_path, title, text, cfg,
                            plantilla=None):
            seen.append(plantilla)
            return {"decision": "aprobado"}

        with mock.patch(
                "clipagent.template_decision.decide_template_for_segment",
                return_value=("rubius", None, "firme", True)):
            manifest = self._run(validate_fn=_validate_viejo)
        self.assertEqual(len(seen), 1)
        self.assertIsNone(seen[0])
        self.assertEqual(len(manifest["clips"]), 1)

    def test_validate_con_avatar_recibe_ambos(self):
        seen = []

        def _validate_nuevo(clip_path, title, text, cfg,
                            plantilla=None, avatar=False):
            seen.append((plantilla, avatar))
            return {"decision": "aprobado"}

        with mock.patch(
                "clipagent.template_decision.decide_template_for_segment",
                return_value=("rubius", None, "firme", True)):
            manifest = self._run(validate_fn=_validate_nuevo)
        self.assertEqual(seen, [(None, True)])
        self.assertEqual(len(manifest["clips"]), 1)

    def test_no_gta_se_salta(self):
        manifest = run_youtube_clips(
            VIDEO_URL, self.out, top_n=2, lang="es",
            single_url=True,
            metadata_fn=lambda url: _meta(
                title="Minecraft con amigos", category="Minecraft"),
            audio_fn=self._audio, transcribe_fn=self._transcribe,
            rms_fn=self._rms, segment_fn=self._segment,
            render_fn=self._render, validate_fn=self._validate,
            layout_fn=self._layout)
        self.assertEqual(manifest["clips"], [])
        self.assertEqual(self.calls["audio"], [])

    def test_sin_pico_no_hay_clip(self):
        flat = _transcript([(i * 30, "hablando normal del juego")
                            for i in range(58)])
        manifest = run_youtube_clips(
            VIDEO_URL, self.out, top_n=2, lang="es",
            single_url=True,
            metadata_fn=lambda url: _meta(),
            audio_fn=self._audio,
            transcribe_fn=lambda p: flat,
            rms_fn=lambda p: _rms_flat(),
            segment_fn=self._segment,
            render_fn=self._render, validate_fn=self._validate,
            layout_fn=self._layout)
        self.assertEqual(manifest["clips"], [])
        self.assertEqual(self.calls["segments"], [])

    def test_audio_mayor_50mb_cae_a_muestreo(self):
        def _too_big(url, out, runner=None):
            raise AudioTooLarge("audio supera 50 MB")

        manifest = run_youtube_clips(
            VIDEO_URL, self.out, top_n=2, lang="es",
            single_url=True,
            metadata_fn=lambda url: _meta(),
            audio_fn=_too_big,
            sample_fn=self._sample,
            transcribe_fn=self._transcribe,
            rms_fn=self._rms,
            segment_fn=self._segment,
            render_fn=self._render, validate_fn=self._validate,
            layout_fn=self._layout, llm_client=FakeLLM(YT_PACKAGE))
        # Muestreo: 15 muestras cada 120 s (1750 s, barrido).
        self.assertEqual(len(self.calls["samples"]), 15)
        self.assertEqual(len(manifest["clips"]), 1)
        self.assertEqual(manifest["clips"][0]["start"], 590)

    def test_rechazado_no_se_guarda(self):
        manifest = self._run(
            validate_fn=lambda *a: {"decision": "rechazado",
                                     "motivo": "sin cara"})
        self.assertEqual(manifest["clips"], [])
        self.assertTrue(any(
            "validación final" in (d.get("reason") or "")
            for d in manifest["descartados"]))


class TestGatesYoutubeManifest(unittest.TestCase):
    def _clip(self, **over):
        base = {
            "n": 1, "file": "clips/clip_01.mp4", "peak": 620.0,
            "start": 590, "duration": 50,
            "viral": {"best_title": "Conseguí 6 Estrellas en GTA 6",
                      "game": "GTA VI",
                      "description": "Rubius consigue las 6 "
                                     "estrellas en GTA 6.",
                      "tags": ["#GTA6"],
                      "thumbnail_text": "¡QUÉ JUGADA!",
                      "viral_multiple": 3.31},
            "viral_score": {"total": 54.4, "filter_passed": False},
            "viral_breakdown": {"audio_multiple": 3.31},
            "seo_scores": _seo(85),
            "moment_card": {"game": "GTA VI"},
            "source": "youtube",
            "source_key": VIDEO_ID,
            "vod_id": VIDEO_ID,
        }
        base.update(over)
        return base

    def test_pasa_sin_chat(self):
        ok, reasons = clip_passes_gates(self._clip(), VIDEO_ID)
        self.assertTrue(ok, reasons)

    def test_filter_passed_false_no_bloquea(self):
        clip = self._clip()
        self.assertFalse(clip["viral_score"]["filter_passed"])
        ok, _ = clip_passes_gates(clip, VIDEO_ID)
        self.assertTrue(ok)

    def test_falta_de_seo_no_pasa(self):
        seo = _seo(85)
        del seo["tiktok"]
        ok, reasons = clip_passes_gates(
            self._clip(seo_scores=seo), VIDEO_ID)
        self.assertFalse(ok)


if __name__ == "__main__":
    unittest.main()

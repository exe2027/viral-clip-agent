#!/usr/bin/env python3
"""Pruebas de la cola FIFO + worker + semáforo (bloque 1 del tribunal).

No toca la cola real (usa una BD temporal) ni descarga nada: los
manifiestos y clips son ficticios en un tmpdir.

Uso: python3 test_queue.py
"""
import json
import os
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from jobqueue import (  # noqa: E402
    Queue, QueueError, check_semaphore, classify_error, detect_game,
    kill_switch_active, parse_url,
)
from worker import _validate_manifest  # noqa: E402


def _manifest(tmpdir, clips):
    """Escribe un manifiesto ficticio con clips y crea sus archivos."""
    os.makedirs(os.path.join(tmpdir, "clips"), exist_ok=True)
    entries = []
    for n, with_layout in clips:
        rel = f"clips/clip_{n:02d}.mp4"
        open(os.path.join(tmpdir, rel), "wb").close()
        entry = {"n": n, "file": rel}
        if with_layout:
            entry["layout"] = {"mode": "auto",
                               "segments": [{"start": 0, "end": 60,
                                             "layout": "center"}]}
        entries.append(entry)
    path = os.path.join(tmpdir, "manifest.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump({"vod_id": "x", "clips": entries}, f)
    return path


def _job(**over):
    job = {
        "streamer": "coker",
        "allowlist_bypass": 0,
        "vod_category": "Grand Theft Auto VI",
        "vod_title": "GTA 6 directo",
        "vod_start_time": "2026-11-20T20:00:00+00:00",
        "manifest_path": None,
        "subtitles_failed": 0,
    }
    job.update(over)
    return job


class TestParseUrl(unittest.TestCase):
    def test_kick(self):
        self.assertEqual(
            parse_url("https://kick.com/coker/videos/01a1dffd-8fb8-71ee-abb7-66e4629249f9"),
            ("kick", "kick-coker-01a1dffd", "coker"))

    def test_twitch_url(self):
        self.assertEqual(parse_url("https://www.twitch.tv/videos/2929669422"),
                         ("twitch", "2929669422", None))

    def test_twitch_id(self):
        self.assertEqual(parse_url("2929669422"), ("twitch", "2929669422", None))

    def test_invalida(self):
        with self.assertRaises(QueueError):
            parse_url("https://youtube.com/watch?v=abc")


class TestDetectGame(unittest.TestCase):
    def test_gta6(self):
        self.assertEqual(detect_game("Grand Theft Auto VI", ""), "gta6")
        self.assertEqual(detect_game("GTA 6", "directo"), "gta6")

    def test_otro(self):
        self.assertEqual(detect_game("Rob Run", ""), "other")
        self.assertEqual(detect_game("Grand Theft Auto V", ""), "other")

    def test_titulo_que_miente_no_es_gta6(self):
        # Casos reales de IlloJuan (2026-11-16): el título menciona
        # GTA VI/6 pero el juego es otro. La categoría manda.
        # VOD 2924180678 "10 DÍAS 📅 DESCARGA DE GTA VI", sin categoría:
        # no se da por GTA 6 -> None (revisión humana).
        self.assertIsNone(
            detect_game(None, "10 DÍAS 📅 DESCARGA DE GTA VI"))
        self.assertIsNone(
            detect_game("", "10 DÍAS 📅 DESCARGA DE GTA VI"))
        # VOD 2901476435 "ÚLTIMO TRÁILER DE GTA 6", categoría
        # Just Chatting, contenido San Andreas -> other.
        self.assertEqual(
            detect_game("Just Chatting", "ÚLTIMO TRÁILER DE GTA 6"),
            "other")
        self.assertEqual(
            detect_game("Grand Theft Auto V", "DESCARGA DE GTA VI"),
            "other")
        self.assertEqual(
            detect_game("Grand Theft Auto: San Andreas",
                        "ÚLTIMO TRÁILER DE GTA 6"),
            "other")

    def test_gta6_por_categoria_aunque_el_titulo_no_lo_diga(self):
        self.assertEqual(
            detect_game("Grand Theft Auto VI", "jugando al nuevo"), "gta6")
        self.assertEqual(detect_game("GTA VI", ""), "gta6")

    def test_sin_categoria_nunca_es_gta6_por_el_titulo(self):
        self.assertIsNone(detect_game(None, "GTA 6 en directo"))
        self.assertIsNone(detect_game("", "GTA VI"))

    def test_sin_datos(self):
        self.assertIsNone(detect_game(None, None))
        self.assertIsNone(detect_game("", ""))


class TestGamesConfig(unittest.TestCase):
    """Fase 2.3: juegos y lanzamiento viven en config, no en el código."""

    GAMES2 = [
        {"name": "GTA 6", "id": "gta6",
         "patterns": ["gta 6", "gta vi", "grand theft auto vi"]},
        {"name": "GTA 5", "id": "gta5",
         "patterns": ["gta 5", "gta v", "grand theft auto v"]},
    ]

    def test_otro_juego_de_la_lista_no_es_rojo(self):
        self.assertEqual(
            detect_game("Grand Theft Auto V", "", self.GAMES2), "gta5")

    def test_juego_fuera_de_la_lista_sigue_siendo_other(self):
        self.assertEqual(
            detect_game("Rob Run", "", self.GAMES2), "other")

    def test_lanzamiento_desde_config(self):
        from jobqueue import load_games_config
        games, rel, tz = load_games_config()
        self.assertEqual([g["id"] for g in games], ["gta6"])
        self.assertEqual(rel.isoformat(), "2026-11-19")
        self.assertEqual(tz, "America/Santiago")


class TestSemaphoreTimezone(unittest.TestCase):
    """Fase 2.3: la fecha del VOD se compara en America/Santiago."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()

    def tearDown(self):
        self.tmp.cleanup()

    def _check(self, **over):
        mpath = _manifest(self.tmp.name, [(1, True)])
        return check_semaphore(_job(manifest_path=mpath, **over),
                               allowlist={"coker"})

    def test_vod_19nov_01utc_es_18nov_en_chile(self):
        # 01:00 UTC del 19 = 22:00 del 18 en Chile: antes del lanzamiento.
        reds = self._check(vod_start_time="2026-11-19T01:00:00+00:00")
        self.assertTrue(any("anterior al lanzamiento" in r for r in reds))

    def test_vod_19nov_04utc_es_19nov_en_chile(self):
        # 04:00 UTC del 19 = 01:00 del 19 en Chile: ya no es anterior.
        reds = self._check(vod_start_time="2026-11-19T04:00:00+00:00")
        self.assertFalse(any("anterior al lanzamiento" in r for r in reds))


class TestQueue(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.q = Queue(os.path.join(self.tmp.name, "test.db"))

    def tearDown(self):
        self.q.close()
        self.tmp.cleanup()

    def test_add_rechaza_sin_allowlist(self):
        with self.assertRaises(QueueError) as cm:
            self.q.add("https://kick.com/coker/videos/01a1dffd-8fb8-71ee-abb7-66e4629249f9",
                       lang="es", allowlist=set())
        self.assertIn("lista blanca", str(cm.exception))

    def test_add_con_allowlist(self):
        job_id = self.q.add(
            "https://kick.com/coker/videos/01a1dffd-8fb8-71ee-abb7-66e4629249f9",
            lang="es", allowlist={"coker"})
        job = self.q.get_job(job_id)
        self.assertEqual(job["state"], "queued")
        self.assertEqual(job["vod_id"], "kick-coker-01a1dffd")
        self.assertEqual(job["streamer"], "coker")
        self.assertEqual(job["allowlist_bypass"], 0)

    def test_add_force_marca_bypass(self):
        job_id = self.q.add(
            "https://kick.com/coker/videos/01a1dffd-8fb8-71ee-abb7-66e4629249f9",
            lang="es", force=True, allowlist=set())
        self.assertEqual(self.q.get_job(job_id)["allowlist_bypass"], 1)

    def test_dedup_activo(self):
        url = "https://kick.com/coker/videos/01a1dffd-8fb8-71ee-abb7-66e4629249f9"
        self.q.add(url, lang="es", allowlist={"coker"})
        with self.assertRaises(QueueError) as cm:
            self.q.add(url, lang="es", allowlist={"coker"})
        self.assertIn("Ya hay un job activo", str(cm.exception))
        # Otro idioma sí se puede encolar (dedup es vod_id + idioma).
        self.q.add(url, lang="en", allowlist={"coker"})

    def test_dedup_no_bloquea_tras_rechazo(self):
        url = "https://kick.com/coker/videos/01a1dffd-8fb8-71ee-abb7-66e4629249f9"
        job_id = self.q.add(url, lang="es", allowlist={"coker"})
        self.q.set_state(job_id, "ready_for_review",
                         manifest_path="/no/existe.json")
        self.q.set_state(job_id, "rejected")
        self.q.add(url, lang="es", allowlist={"coker"})  # no levanta

    def test_disco_lleno_rechaza(self):
        with self.assertRaises(QueueError) as cm:
            self.q.add("https://kick.com/coker/videos/01a1dffd-8fb8-71ee-abb7-66e4629249f9",
                       lang="es", allowlist={"coker"}, min_free_gb=10 ** 9)
        self.assertIn("Disco", str(cm.exception))

    def test_requeue_stale_processing(self):
        job_id = self.q.add(
            "https://kick.com/coker/videos/01a1dffd-8fb8-71ee-abb7-66e4629249f9",
            lang="es", allowlist={"coker"})
        self.q.set_state(job_id, "processing")
        self.assertEqual(self.q.requeue_stale_processing(), 1)
        job = self.q.get_job(job_id)
        self.assertEqual(job["state"], "queued")
        self.assertEqual(job["attempts"], 0)  # no gasta intento
        self.assertEqual(self.q.requeue_stale_processing(), 0)

    def test_fifo(self):
        a = self.q.add("https://kick.com/coker/videos/01a1dffd-8fb8-71ee-abb7-66e4629249f9",
                       lang="es", allowlist={"coker"})
        b = self.q.add("https://kick.com/locones/videos/01a1e069-8470-7626-8d5e-21ccb729c509",
                       lang="es", allowlist={"locones"})
        self.assertEqual(self.q.next_queued()["id"], a)
        self.q.set_state(a, "processing")
        self.assertEqual(self.q.next_queued()["id"], b)

    def test_approve_y_reject(self):
        job_id = self.q.add(
            "https://kick.com/coker/videos/01a1dffd-8fb8-71ee-abb7-66e4629249f9",
            lang="es", allowlist={"coker"})
        with self.assertRaises(QueueError):
            self.q.approve(job_id)  # no está en ready_for_review
        mpath = _manifest(self.tmp.name, [(1, True)])
        self.q.set_state(job_id, "ready_for_review", manifest_path=mpath,
                         vod_category="Grand Theft Auto VI",
                         vod_title="GTA 6 directo",
                         vod_start_time="2026-11-20T20:00:00+00:00")
        reds = self.q.approve(job_id, allowlist={"coker"})
        self.assertEqual(reds, [])
        self.assertEqual(self.q.get_job(job_id)["state"], "approved")

    def test_reject(self):
        job_id = self.q.add(
            "https://kick.com/coker/videos/01a1dffd-8fb8-71ee-abb7-66e4629249f9",
            lang="es", allowlist={"coker"})
        self.q.set_state(job_id, "ready_for_review",
                         manifest_path=_manifest(self.tmp.name, [(1, True)]))
        self.q.reject(job_id, reason="no es el momento")
        self.assertEqual(self.q.get_job(job_id)["state"], "rejected")


class TestSemaphore(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()

    def tearDown(self):
        self.tmp.cleanup()

    def _check(self, **over):
        mpath = _manifest(self.tmp.name, [(1, True)])
        return check_semaphore(_job(manifest_path=mpath, **over),
                               allowlist={"coker"})

    def test_sin_rojos(self):
        self.assertEqual(self._check(), [])

    def test_coker_como_esta(self):
        # El VOD real de Coker: categoría Rob Run, 15 nov 2026 ->
        # 2 rojos (juego ≠ GTA 6 y VOD anterior al lanzamiento).
        # 2026-11-16: el permiso YA NO es un rojo del semáforo.
        reds = check_semaphore(
            _job(manifest_path=_manifest(self.tmp.name, [(1, True)]),
                 vod_category="Rob Run",
                 vod_title="⚠️ SALVANDO EL DOMINGO ⚠️ ROB RUN ⚠️",
                 vod_start_time="2026-11-15T16:14:59+00:00",
                 allowlist_bypass=1),
            allowlist=set())
        self.assertEqual(len(reds), 2)
        self.assertFalse(any("permiso" in r for r in reds))
        self.assertTrue(any("≠ GTA 6" in r for r in reds))
        self.assertTrue(any("anterior al lanzamiento" in r for r in reds))

    def test_juego_no_verificable(self):
        reds = self._check(vod_category=None, vod_title=None)
        self.assertTrue(any("juego no verificable" in r for r in reds))

    def test_fecha_no_verificable(self):
        reds = self._check(vod_start_time=None)
        self.assertTrue(any("fecha del VOD no verificable" in r for r in reds))

    def test_musica(self):
        reds = self._check(vod_title="DJ set GTA 6 music")
        self.assertTrue(any("música con copyright" in r for r in reds))

    def test_sin_manifiesto(self):
        reds = check_semaphore(_job(manifest_path=None), allowlist={"coker"})
        self.assertTrue(any("sin manifiesto" in r for r in reds))

    def test_manifiesto_vacio(self):
        mpath = _manifest(self.tmp.name, [])
        reds = check_semaphore(_job(manifest_path=mpath), allowlist={"coker"})
        self.assertTrue(any("sin clips generados" in r for r in reds))

    def test_clip_sin_layout(self):
        mpath = _manifest(self.tmp.name, [(1, False)])
        reds = check_semaphore(_job(manifest_path=mpath), allowlist={"coker"})
        self.assertTrue(any("sin decisión de layout" in r for r in reds))

    def test_clip_falta_archivo(self):
        mpath = _manifest(self.tmp.name, [(1, True)])
        os.remove(os.path.join(self.tmp.name, "clips", "clip_01.mp4"))
        reds = check_semaphore(_job(manifest_path=mpath), allowlist={"coker"})
        self.assertTrue(any("falta el archivo" in r for r in reds))

    def test_subtitulos_fallidos(self):
        reds = self._check(subtitles_failed=1)
        self.assertTrue(any("subtítulos fallidos" in r for r in reds))

    def test_approve_bloquea_con_rojos(self):
        tmp = tempfile.TemporaryDirectory()
        q = Queue(os.path.join(tmp.name, "t.db"))
        try:
            job_id = q.add(
                "https://kick.com/coker/videos/01a1dffd-8fb8-71ee-abb7-66e4629249f9",
                lang="es", force=True, allowlist=set())
            mpath = _manifest(tmp.name, [(1, True)])
            q.set_state(job_id, "ready_for_review", manifest_path=mpath,
                        vod_category="Rob Run",
                        vod_title="ROB RUN",
                        vod_start_time="2026-11-15T16:14:59+00:00")
            with self.assertRaises(QueueError) as cm:
                q.approve(job_id, allowlist=set())
            self.assertIn("rojo(s)", str(cm.exception))
            self.assertEqual(q.get_job(job_id)["state"], "ready_for_review")
            # Con --force-confirm sí pasa, y queda registrado.
            # 2026-11-16: el permiso ya no es rojo; quedan 2
            # (juego ≠ GTA 6 y VOD anterior al lanzamiento).
            reds = q.approve(job_id, force_confirm=True, allowlist=set())
            self.assertEqual(len(reds), 2)
            self.assertEqual(q.get_job(job_id)["state"], "approved")
        finally:
            q.close()
            tmp.cleanup()


class TestClassifyError(unittest.TestCase):
    def test_transitorio(self):
        self.assertEqual(classify_error("Connection reset by peer"), "transient")
        self.assertEqual(classify_error("HTTP 503 Service Unavailable"), "transient")
        self.assertEqual(classify_error("timed out after 30s"), "transient")

    def test_permanente(self):
        self.assertEqual(classify_error("No encontré el VOD en Kick"), "permanent")
        self.assertEqual(classify_error("sin clips generados"), "permanent")
        self.assertEqual(classify_error("algo raro sin pista"), "permanent")


class TestKillSwitch(unittest.TestCase):
    def test_archivo(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "queue.kill")
            self.assertFalse(kill_switch_active(path))
            open(path, "w").close()
            self.assertTrue(kill_switch_active(path))


class TestSinMomentosVirales(unittest.TestCase):
    """1.3: un manifiesto 'sin_momentos_virales' no es un fallo.

    El job sale de la cola como rechazado (nada que revisar), sin
    gastar reintentos y sin ir a 'failed'.
    """
    def test_process_job_rechaza_sin_gastar_intento(self):
        from unittest import mock
        import worker as worker_mod
        with tempfile.TemporaryDirectory() as tmp:
            out_dir = os.path.join(tmp, "runs")
            os.makedirs(out_dir)
            with open(os.path.join(out_dir, "manifest.json"), "w",
                      encoding="utf-8") as f:
                json.dump({"vod_id": "x", "clips": [],
                           "descartados": [{"peak": 40, "motivos": ["x"]}],
                           "status": "sin_momentos_virales"}, f)
            q = Queue(os.path.join(tmp, "t.db"))
            try:
                # URL de Twitch: con Kick el worker resolvería los
                # metadatos por red antes de correr auto_clips.
                job_id = q.add("2929669422", lang="es", force=True,
                               allowlist=set())
                q.set_state(job_id, "queued", out_dir=out_dir)
                job = q.get_job(job_id)
                with mock.patch.object(
                        worker_mod, "_run_auto_clips",
                        return_value=(0, os.path.join(tmp, "worker.log"))):
                    state = worker_mod.process_job(q, job, top_n=5)
                self.assertEqual(state, "rejected")
                final = q.get_job(job_id)
                self.assertEqual(final["state"], "rejected")
                self.assertEqual(final["attempts"], 0)
                self.assertIn("sin momentos virales", final["error"])
                self.assertEqual(final["error_kind"], "no_viral")
            finally:
                q.close()


class TestWorkerKill(unittest.TestCase):
    """1.4: matar el árbol de procesos, tope de reintentos por timeout
    y no borrar worker.lock."""

    def _job_en_cola(self, q, tmp):
        # URL de Twitch (no de Kick): con Kick el worker resolvería
        # los metadatos por red antes de correr auto_clips.
        job_id = q.add("2929669422", lang="es", force=True,
                       allowlist=set())
        out_dir = os.path.join(tmp, "runs")
        os.makedirs(out_dir, exist_ok=True)
        q.set_state(job_id, "queued", out_dir=out_dir)
        return q.get_job(job_id)

    def test_timeout_mata_el_arbol_entero(self):
        import subprocess as sp
        import worker as worker_mod
        with tempfile.TemporaryDirectory() as tmp:
            fake = os.path.join(tmp, "fake_auto_clips.py")
            with open(fake, "w", encoding="utf-8") as f:
                f.write("import subprocess, time\n"
                        "subprocess.Popen(['sleep', '297'])\n"
                        "time.sleep(297)\n")
            job = {"id": 1, "url": "2929669422", "lang": "es",
                   "out_dir": os.path.join(tmp, "runs")}
            os.makedirs(job["out_dir"], exist_ok=True)
            with mock.patch.object(worker_mod, "AUTO_CLIPS", fake):
                with self.assertRaises(TimeoutError):
                    worker_mod._run_auto_clips(job, top_n=5, timeout_s=2)
            # Ni el script falso ni su hijo `sleep` quedan vivos.
            self.assertNotEqual(
                sp.run(["pgrep", "-f", "sleep 297"],
                       capture_output=True).returncode, 0)
            self.assertNotEqual(
                sp.run(["pgrep", "-f", fake],
                       capture_output=True).returncode, 0)

    def test_timeout_solo_un_reintento(self):
        import worker as worker_mod
        with tempfile.TemporaryDirectory() as tmp:
            q = Queue(os.path.join(tmp, "t.db"))
            try:
                job = self._job_en_cola(q, tmp)
                with mock.patch.object(
                        worker_mod, "_run_auto_clips",
                        side_effect=TimeoutError("auto_clips superó el timeout")):
                    self.assertEqual(
                        worker_mod.process_job(q, job, top_n=5), "queued")
                    job = q.get_job(job["id"])
                    self.assertEqual(job["attempts"], 1)
                    self.assertEqual(q.count_log(job["id"], "timeout"), 1)
                    self.assertEqual(
                        worker_mod.process_job(q, job, top_n=5), "dead")
                    job = q.get_job(job["id"])
                    self.assertEqual(job["attempts"], 2)
                    self.assertEqual(job["error_kind"], "timeout")
            finally:
                q.close()

    def test_worker_lock_no_se_borra_al_salir(self):
        import worker as worker_mod
        with tempfile.TemporaryDirectory() as tmp:
            lock_path = os.path.join(tmp, "worker.lock")
            db_path = os.path.join(tmp, "t.db")
            with mock.patch.object(worker_mod, "WORKER_LOCK", lock_path):
                self.assertEqual(
                    worker_mod.main(["--db", db_path, "--once"]), 0)
            self.assertTrue(os.path.isfile(lock_path),
                            "worker.lock se borró al salir (carrera)")


class TestValidateManifest(unittest.TestCase):
    def test_ok_y_vacio(self):
        with tempfile.TemporaryDirectory() as tmp:
            _manifest(tmp, [(1, True)])
            manifest, problems = _validate_manifest(tmp)
            self.assertEqual(problems, [])
            self.assertEqual(len(manifest["clips"]), 1)
            manifest, problems = _validate_manifest(os.path.join(tmp, "nope"))
            self.assertTrue(any("sin manifiesto" in p for p in problems))


if __name__ == "__main__":
    unittest.main(verbosity=2)

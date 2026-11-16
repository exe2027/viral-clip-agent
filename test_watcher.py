#!/usr/bin/env python3
"""Tests del watcher (sin red): fixtures de respuestas reales.

  * GQL de Twitch: respuesta real de illojuan (2026-11-16, en vivo).
  * Kick: respuestas reales de coker (2026-11-16, en vivo).
  * Frenos y dedup: con BD temporal de queue.py y estado temporal.

Uso: python3 test_watcher.py
"""

import json
import os
import sys
import tempfile
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import watcher
from watcher import (TwitchPublicGQL, decide, kick_check, load_state,
                     record_vod, run_once)
from queue import Queue

# --- Fixture: GQL real de illojuan, 2026-11-16 19:15 (en vivo, GTA V) ---
GQL_ILLOJUAN = [{
    "data": {
        "user": {
            "id": "90075649", "login": "illojuan", "displayName": "IlloJuan",
            "stream": {
                "id": "318368224356", "type": "live",
                "title": "2 DÍAS 📅 ADIÓS GTA 5 👋🏻 - Grand Theft Auto V #7 [FINAL]",
                "createdAt": "2026-11-16T18:41:08Z", "viewersCount": 60371,
                "game": {"name": "Grand Theft Auto V"},
            },
            "videos": {"edges": [
                {"node": {"id": "2930700035",
                          "title": "2 DÍAS 📅 ADIÓS GTA 5 👋🏻 - Grand Theft Auto V #7 [FINAL]",
                          "createdAt": "2026-11-16T18:41:15Z",
                          "lengthSeconds": 12926,
                          "game": {"name": "Just Chatting"}, "viewCount": 5756}},
                {"node": {"id": "2929822269",
                          "title": "3 DÍAS 📅 ÚLTIMOS MOMENTOS - Grand Theft Auto V #6",
                          "createdAt": "2026-11-15T18:25:21Z",
                          "lengthSeconds": 26750,
                          "game": {"name": "Just Chatting"}, "viewCount": 891759}},
            ]},
        }
    },
}]

# --- Fixture: Kick real de coker, 2026-11-16 (en vivo) ---
KICK_COKER_CHANNEL = {
    "slug": "coker",
    "livestream": {
        "id": 138242194, "is_live": True,
        "session_title": "⚠️ DOS DIAS PARA GTA VI ⚠️ ROB RUN ⚠️ TOP GLOBALES ⚠️",
        "created_at": "2026-11-16 20:46:50", "viewer_count": 12345,
        "categories": [{"name": "Just Chatting"}],
    },
}
KICK_COKER_VIDEOS = [
    {"id": 138242194, "start_time": "2026-11-16 20:46:49", "duration": 0,
     "views": 0, "language": "Spanish",
     "session_title": "⚠️ DOS DIAS PARA GTA VI ⚠️ ROB RUN ⚠️ TOP GLOBALES ⚠️",
     "categories": [{"name": "Just Chatting"}],
     "video": {"uuid": "a6a0df30-4408-43b8-9e9a-f30acc362d4d"}},
    {"id": 138010835, "start_time": "2026-11-15 16:14:59", "duration": 57095000,
     "views": 291922, "language": "Spanish",
     "session_title": "⚠️ SALVANDO EL DOMINGO ⚠️ ROB RUN ⚠️ TOP GLOBALES ⚠️",
     "categories": [{"name": "Rob Run"}],
     "video": {"uuid": "2419da7f-c06d-4adf-921b-e0b300e9d7b5"}},
]

GTA6_VOD = {"platform": "kick", "vod_id": "kick-testgta-aaaaaaaa",
            "url": "https://kick.com/testgta/videos/aaaaaaaa-1111-4111-8111-111111111111",
            "title": "GTA 6 POR FIN - primer directo en Leonida",
            "category": "Grand Theft Auto VI", "start_time": None,
            "duration_s": 7200, "views": 100, "live": False}
GTAV_VOD = dict(GTA6_VOD, vod_id="kick-testgta-bbbbbbbb",
                url="https://kick.com/testgta/videos/bbbbbbbb-1111-4111-8111-111111111111",
                title="GTA V RP noche de atracos",
                category="Grand Theft Auto V")

# VODs reales de IlloJuan (Twitch) reportados 2026-11-16: el título
# menciona GTA VI/6 pero el juego NO es GTA 6. Antes del arreglo,
# detect_game() casaba el título y el watcher los marcaba
# "would_enqueue". Con la corrección (la categoría manda) deben salir
# "skip" con juego_no_gta6.
ILLOJUAN_DESCARGA = {"platform": "twitch", "vod_id": "2924180678",
                     "url": "https://www.twitch.tv/videos/2924180678",
                     "title": "10 DÍAS 📅 DESCARGA DE GTA VI",
                     "category": "", "start_time": None,
                     "duration_s": 3600, "views": 0, "live": False}
ILLOJUAN_TRAILER = {"platform": "twitch", "vod_id": "2901476435",
                    "url": "https://www.twitch.tv/videos/2901476435",
                    "title": "ÚLTIMO TRÁILER DE GTA 6",
                    "category": "Just Chatting", "start_time": None,
                    "duration_s": 3600, "views": 0, "live": False}

INF_CONCEDIDO = {"usuario": "testgta", "plataforma": "kick", "idioma": "es",
                 "juego": "GTA 6", "estado": "concedido", "canal_original": ""}
INF_PENDIENTE = dict(INF_CONCEDIDO, estado="pendiente")
# 2026-11-16: estar en la lista = permiso ya dado; solo denegado/revocado excluyen.
INF_DENEGADO = dict(INF_CONCEDIDO, estado="denegado")
INF_REVOCADO = dict(INF_CONCEDIDO, estado="revocado")


class TestTwitchGQL(unittest.TestCase):
    def test_live_detectado_y_vod_en_curso(self):
        # En GQL el id del stream NO es el id del VOD: el VOD en curso
        # se detecta por createdAt (7 s de diferencia con el inicio).
        with mock.patch.object(watcher, "http_json", return_value=GQL_ILLOJUAN):
            live, vods = TwitchPublicGQL().check("illojuan")
        self.assertTrue(live)
        self.assertEqual(live["game"], "Grand Theft Auto V")
        self.assertEqual(live["viewers"], 60371)
        self.assertEqual(len(vods), 2)
        self.assertTrue(vods[0]["live"])   # 2930700035 = directo en curso
        self.assertEqual(vods[0]["vod_id"], "2930700035")
        self.assertFalse(vods[1]["live"])  # 2929822269 = VOD terminado
        self.assertEqual(vods[1]["duration_s"], 26750)


class TestKick(unittest.TestCase):
    def test_live_y_vods(self):
        def fake_http(url, **kw):
            if url.endswith("/videos"):
                return KICK_COKER_VIDEOS
            return KICK_COKER_CHANNEL
        import kick_downloader.core as core
        with mock.patch.object(core, "http_json", side_effect=fake_http):
            live, vods = kick_check("coker")
        self.assertTrue(live)
        self.assertEqual(live["game"], "Just Chatting")
        self.assertEqual(len(vods), 2)
        self.assertTrue(vods[0]["live"])   # duration 0 = directo en curso
        self.assertEqual(vods[0]["vod_id"], "kick-coker-a6a0df30")
        self.assertFalse(vods[1]["live"])
        self.assertEqual(vods[1]["duration_s"], 57095)  # ms -> s
        self.assertEqual(vods[1]["vod_id"], "kick-coker-2419da7f")


class TestDecide(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.state = load_state(os.path.join(self.tmp, "seen.json"))
        self.cfg = {"max_new_per_day": 5}
        self.db = os.path.join(self.tmp, "q.db")
        self.q = Queue(self.db)

    def tearDown(self):
        self.q.close()

    def test_pendiente_ya_no_bloquea_en_lista(self):
        # 2026-11-16: estar en la lista = permiso ya dado; "pendiente"
        # ya no bloquea. En dry-run se propone (would_enqueue).
        action, reason = decide(GTA6_VOD, INF_PENDIENTE, self.state,
                                self.q, self.cfg, False, True)
        self.assertEqual(action, "would_enqueue")

    def test_denegado_bloquea_aunque_sea_gta6(self):
        action, reason = decide(GTA6_VOD, INF_DENEGADO, self.state,
                                self.q, self.cfg, False, True)
        self.assertEqual(action, "skip")
        self.assertIn("fuera_de_lista", reason)

    def test_revocado_bloquea_aunque_sea_gta6(self):
        action, reason = decide(GTA6_VOD, INF_REVOCADO, self.state,
                                self.q, self.cfg, False, True)
        self.assertEqual(action, "skip")
        self.assertIn("fuera_de_lista", reason)

    def test_gta5_bloquea_aunque_haya_permiso(self):
        action, reason = decide(GTAV_VOD, INF_CONCEDIDO, self.state,
                                self.q, self.cfg, False, True)
        self.assertEqual(action, "skip")
        self.assertIn("juego_no_gta6", reason)

    def test_illojuan_descarga_gta_vi_sin_categoria_no_se_encola(self):
        # VOD 2924180678: título "DESCARGA DE GTA VI", sin categoría.
        # No es GTA 6 por el título solo -> skip, a revisión humana.
        action, reason = decide(ILLOJUAN_DESCARGA, INF_CONCEDIDO,
                                self.state, self.q, self.cfg, False, True)
        self.assertEqual(action, "skip")
        self.assertIn("juego_no_gta6", reason)

    def test_illojuan_trailer_gta6_just_chatting_no_se_encola(self):
        # VOD 2901476435: título "ÚLTIMO TRÁILER DE GTA 6", categoría
        # Just Chatting, contenido San Andreas -> skip.
        action, reason = decide(ILLOJUAN_TRAILER, INF_CONCEDIDO,
                                self.state, self.q, self.cfg, False, True)
        self.assertEqual(action, "skip")
        self.assertIn("juego_no_gta6", reason)

    def test_kill_switch_bloquea_todo(self):
        action, reason = decide(GTA6_VOD, INF_CONCEDIDO, self.state,
                                self.q, self.cfg, True, True)
        self.assertEqual(action, "skip")
        self.assertIn("kill_switch", reason)

    def test_tope_diario(self):
        from datetime import date as _date
        cfg = {"max_new_per_day": 1}
        self.state["daily"][f"testgta|{_date.today().isoformat()}"] = 1
        action, reason = decide(GTA6_VOD, INF_CONCEDIDO, self.state,
                                self.q, cfg, False, True)
        self.assertEqual(action, "skip")
        self.assertIn("tope_diario", reason)

    def test_gta6_con_permiso_se_propone_en_dry_run(self):
        action, reason = decide(GTA6_VOD, INF_CONCEDIDO, self.state,
                                self.q, self.cfg, False, True)
        self.assertEqual(action, "would_enqueue")

    def test_dedup_segunda_pasada_no_repropone(self):
        record_vod(self.state, GTA6_VOD, INF_CONCEDIDO, "would_enqueue")
        action, reason = decide(GTA6_VOD, INF_CONCEDIDO, self.state,
                                self.q, self.cfg, False, True)
        self.assertEqual(action, "seen")

    def test_un_job_en_processing_bloquea_al_siguiente(self):
        job_id = self.q.add(GTA6_VOD["url"], lang="es", streamer="testgta",
                            allowlist={"testgta"})
        self.q.set_state(job_id, "processing")
        vod2 = dict(GTA6_VOD, vod_id="kick-testgta-cccccccc",
                    url="https://kick.com/testgta/videos/cccccccc-1111-4111-8111-111111111111")
        action, reason = decide(vod2, INF_CONCEDIDO, self.state,
                                self.q, self.cfg, False, True)
        self.assertEqual(action, "skip")
        self.assertIn("en_processing", reason)


class TestRunOnceEndToEnd(unittest.TestCase):
    def test_encola_gta6_con_permiso_y_dedup(self):
        tmp = tempfile.mkdtemp()
        state_path = os.path.join(tmp, "seen.json")
        db_path = os.path.join(tmp, "q.db")
        cfg = {"max_new_per_day": 5, "poll_minutes": 15,
               "state_file": state_path}
        fake_live = None
        fake_vods = [dict(GTA6_VOD)]
        with mock.patch.object(watcher, "kick_check",
                               return_value=(fake_live, fake_vods)):
            summary, errors = run_once([INF_CONCEDIDO], cfg, state_path,
                                       db_path, dry_run=False, verbose=False)
        self.assertEqual(errors, 0)
        self.assertEqual(summary["enqueued"], 1)
        q = Queue(db_path)
        jobs = q.list_jobs("queued")
        self.assertEqual(len(jobs), 1)
        self.assertEqual(jobs[0]["vod_id"], "kick-testgta-aaaaaaaa")
        self.assertEqual(jobs[0]["streamer"], "testgta")
        q.close()
        # Segunda pasada: dedup — no se vuelve a encolar nada.
        with mock.patch.object(watcher, "kick_check",
                               return_value=(fake_live, fake_vods)):
            summary2, _ = run_once([INF_CONCEDIDO], cfg, state_path,
                                   db_path, dry_run=False, verbose=False)
        self.assertEqual(summary2["enqueued"], 0)
        self.assertEqual(summary2["seen"], 1)

    def test_dry_run_no_toca_la_cola(self):
        tmp = tempfile.mkdtemp()
        state_path = os.path.join(tmp, "seen.json")
        db_path = os.path.join(tmp, "q.db")
        cfg = {"max_new_per_day": 5, "poll_minutes": 15,
               "state_file": state_path}
        with mock.patch.object(watcher, "kick_check",
                               return_value=(None, [dict(GTA6_VOD)])):
            summary, _ = run_once([INF_CONCEDIDO], cfg, state_path,
                                  db_path, dry_run=True, verbose=False)
        self.assertEqual(summary["would_enqueue"], 1)
        self.assertEqual(summary["enqueued"], 0)
        self.assertFalse(os.path.exists(db_path))


if __name__ == "__main__":
    unittest.main(verbosity=2)

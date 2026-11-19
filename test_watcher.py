#!/usr/bin/env python3
"""Tests del watcher (sin red): fixtures de respuestas reales.

  * GQL de Twitch: respuesta real de illojuan (2026-11-16, en vivo).
  * Kick: respuestas reales de coker (2026-11-16, en vivo).
  * Frenos y dedup: con BD temporal de jobqueue.py y estado temporal.

Uso: python3 test_watcher.py
"""

import os
import sys
import tempfile
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import watcher
from watcher import (TwitchHelix, TwitchPublicGQL, WatcherError, decide,
                     kick_check, load_state, record_vod, resolve_vod_category,
                     run_once, save_state, title_mentions_gta6)
from jobqueue import Queue

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
# "skip" con juego_no_permitido.
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
INF_RUBIUS = {"usuario": "rubius", "plataforma": "twitch", "idioma": "es",
              "juego": "GTA VI", "estado": "concedido", "canal_original": ""}

# --- Fixture: Helix real (forma) de Rubius, 2026-11-19 ---
# /videos NO trae game_name; el VOD en curso se reconoce por
# stream_id (== id del stream), no por id de vídeo.
HELIX_STREAM_RUBIUS = [{"id": "51515151515",
                        "title": "🌴GTA VI🌴 DIRECTO",
                        "game_name": "Grand Theft Auto VI",
                        "game_id": "1", "viewer_count": 329979,
                        "started_at": "2026-11-19T18:00:00Z"}]
HELIX_VIDEOS_RUBIUS = [
    {"id": "2932415021", "stream_id": "51515151515",
     "title": "🌴GTA VI🌴 DIRECTO",
     "created_at": "2026-11-19T18:00:05Z", "duration": "7h13m22s",
     "view_count": 1000},
    {"id": "2930000000", "stream_id": "51500000000",
     "title": "GTA VI — día 1 en Leonida",
     "created_at": "2026-11-18T18:00:00Z", "duration": "5h0m0s",
     "view_count": 500},
]


def _helix_get_factory(streams_data, videos_data):
    """_get mockeado de TwitchHelix: /users, /streams, /videos."""
    def fake_get(self, path, params):
        if path == "/users":
            return {"data": [{"id": "111", "login": "rubius"}]}
        if path == "/streams":
            return {"data": streams_data}
        if path == "/videos":
            return {"data": videos_data}
        raise AssertionError(f"ruta Helix no esperada: {path}")
    return fake_get


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
        self.assertIn("juego_no_permitido", reason)

    def test_illojuan_descarga_gta_vi_sin_categoria_no_se_encola(self):
        # VOD 2924180678: título "DESCARGA DE GTA VI", sin categoría.
        # No es GTA 6 por el título solo -> skip, a revisión humana.
        action, reason = decide(ILLOJUAN_DESCARGA, INF_CONCEDIDO,
                                self.state, self.q, self.cfg, False, True)
        self.assertEqual(action, "skip")
        self.assertIn("juego_no_permitido", reason)

    def test_illojuan_trailer_gta6_just_chatting_no_se_encola(self):
        # VOD 2901476435: título "ÚLTIMO TRÁILER DE GTA 6", categoría
        # Just Chatting, contenido San Andreas -> skip.
        action, reason = decide(ILLOJUAN_TRAILER, INF_CONCEDIDO,
                                self.state, self.q, self.cfg, False, True)
        self.assertEqual(action, "skip")
        self.assertIn("juego_no_permitido", reason)

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


class TestResolveCategory(unittest.TestCase):
    def test_titulo_gta_vida_no_es_gta6(self):
        # El \b de la lista blanca evita "GTA VIDA".
        self.assertFalse(title_mentions_gta6("GTA VIDA real"))
        self.assertFalse(title_mentions_gta6("GTA V en Leonida"))
        self.assertTrue(title_mentions_gta6("🌴GTA VI🌴 DIRECTO"))
        self.assertTrue(title_mentions_gta6("ÚLTIMO TRÁILER DE GTA 6"))

    def test_categoria_que_trae_manda(self):
        vod = {"category": "Just Chatting", "title": "GTA VI", "live": True}
        cat, src = resolve_vod_category(
            vod, live_game="Grand Theft Auto VI")
        self.assertEqual((cat, src), ("Just Chatting", "api"))

    def test_vod_en_curso_toma_juego_del_vivo(self):
        vod = {"category": "", "title": "🌴GTA VI🌴 DIRECTO", "live": True}
        cat, src = resolve_vod_category(
            vod, live_game="Grand Theft Auto VI")
        self.assertEqual((cat, src),
                         ("Grand Theft Auto VI", "directo-en-curso"))

    def test_fallback_exige_titulo_y_canal(self):
        vod = {"category": "", "title": "10 DÍAS DESCARGA DE GTA VI",
               "live": False}
        # Título con GTA VI pero canal en Just Chatting: no basta.
        cat, src = resolve_vod_category(
            vod, channel_game="Just Chatting")
        self.assertEqual((cat, src), ("", "sin-categoria"))
        # Título con GTA VI y canal en GTA VI: sí.
        cat, src = resolve_vod_category(
            vod, channel_game="Grand Theft Auto VI")
        self.assertEqual((cat, src),
                         ("Grand Theft Auto VI", "fallback-titulo+canal"))


class TestTwitchHelix(unittest.TestCase):
    def _check(self, streams, videos, gql_vods=(), channel_game=""):
        """TwitchHelix.check con Helix/GQL/canal mockeados (sin red)."""
        helix = TwitchHelix("id", "secret")
        gql = ([], [{"vod_id": v["vod_id"], "category": v["category"]}
                     for v in gql_vods])
        with mock.patch.object(TwitchHelix, "_get", autospec=True,
                               side_effect=_helix_get_factory(
                                   streams, videos)), \
             mock.patch.object(watcher.TwitchPublicGQL, "check",
                               return_value=gql), \
             mock.patch.object(TwitchHelix, "channel_category",
                               return_value={"game_name": channel_game,
                                             "game_id": "",
                                             "source": "test"}):
            return helix.check("rubius")

    def test_helix_vod_en_curso_por_stream_id(self):
        # C5: el VOD 2932415021 tiene id de vídeo != id de stream, pero
        # su stream_id sí coincide con el directo en curso.
        live, vods = self._check(HELIX_STREAM_RUBIUS, HELIX_VIDEOS_RUBIUS)
        self.assertTrue(live)
        self.assertEqual(live["id"], "51515151515")
        self.assertEqual(live["game"], "Grand Theft Auto VI")
        by_id = {v["vod_id"]: v for v in vods}
        vod_live = by_id["2932415021"]
        self.assertTrue(vod_live["live"])
        self.assertEqual(vod_live["stream_id"], "51515151515")
        self.assertEqual(vod_live["category"], "Grand Theft Auto VI")
        self.assertEqual(vod_live["category_source"], "directo-en-curso")
        # VOD terminado con otro stream_id: no en curso.
        self.assertFalse(by_id["2930000000"]["live"])

    def test_helix_categoria_viene_de_gql(self):
        # C4: sin directo en vivo, Helix /videos no trae juego; la
        # categoría llega por GQL (game.name por vídeo).
        gql_vods = [{"vod_id": "2930000000",
                     "category": "Grand Theft Auto VI"}]
        live, vods = self._check([], HELIX_VIDEOS_RUBIUS[1:],
                                 gql_vods=gql_vods)
        self.assertIsNone(live)
        self.assertEqual(vods[0]["category"], "Grand Theft Auto VI")
        self.assertEqual(vods[0]["category_source"], "gql")

    def test_helix_fallback_titulo_mas_canal(self):
        # C4: GQL sin categoría + canal en GTA VI + título con GTA VI.
        live, vods = self._check([], HELIX_VIDEOS_RUBIUS[1:],
                                 channel_game="Grand Theft Auto VI")
        self.assertEqual(vods[0]["category"], "Grand Theft Auto VI")
        self.assertEqual(vods[0]["category_source"],
                         "fallback-titulo+canal")

    def test_helix_fallback_no_se_activa_sin_canal_gta6(self):
        # C4: "10 DÍAS DESCARGA DE GTA VI" con canal en Just Chatting
        # sigue sin categoría y decide() lo sigue saltando: el título
        # solo nunca basta.
        descarga = {"id": "2924180678", "stream_id": "29200000000",
                    "title": "10 DÍAS 📅 DESCARGA DE GTA VI",
                    "created_at": "2026-11-15T18:00:00Z",
                    "duration": "1h0m0s", "view_count": 10}
        _, vods = self._check([], [descarga],
                              channel_game="Just Chatting")
        self.assertEqual(vods[0]["category"], "")
        self.assertEqual(vods[0]["category_source"], "sin-categoria")
        tmp = tempfile.mkdtemp()
        state = load_state(os.path.join(tmp, "seen.json"))
        q = Queue(os.path.join(tmp, "q.db"))
        try:
            action, reason = decide(vods[0], INF_CONCEDIDO, state, q,
                                    {"max_new_per_day": 5}, False, True)
        finally:
            q.close()
        self.assertEqual(action, "skip")
        self.assertIn("juego_no_permitido", reason)


class TestDecideC4C5(TestDecide):
    def test_vod_vivo_nunca_se_decide(self):
        # C5: decide() no encola ni marca permanente un VOD vivo.
        vod = dict(GTA6_VOD, live=True)
        action, reason = decide(vod, INF_CONCEDIDO, self.state,
                                self.q, self.cfg, False, True)
        self.assertEqual(action, "skip")
        self.assertIn("vod_en_curso", reason)

    def test_skip_permanente_sin_categoria_se_reevalua(self):
        # C4: VOD marcado skipped:juego_no_permitido con categoría
        # vacía (bug Helix) se re-evalúa cuando llega la categoría.
        viejo = dict(ILLOJUAN_DESCARGA)  # categoría ""
        record_vod(self.state, viejo, INF_CONCEDIDO,
                   "skipped:juego_no_permitido")
        nuevo = dict(viejo, category="Grand Theft Auto VI")
        action, _ = decide(nuevo, INF_CONCEDIDO, self.state,
                           self.q, self.cfg, False, True)
        self.assertEqual(action, "would_enqueue")

    def test_skip_permanente_sin_categoria_se_reevalua_en_live(self):
        viejo = dict(ILLOJUAN_DESCARGA)  # categoría ""
        record_vod(self.state, viejo, INF_CONCEDIDO,
                   "skipped:juego_no_permitido")
        nuevo = dict(viejo, category="Grand Theft Auto VI")
        action, _ = decide(nuevo, INF_CONCEDIDO, self.state,
                           self.q, self.cfg, False, False)
        self.assertEqual(action, "enqueue")

    def test_skip_permanente_con_juego_confirmado_no_se_reevalua(self):
        # Categoría Just Chatting registrada: juego confirmado, el
        # skip sigue siendo definitivo.
        viejo = dict(ILLOJUAN_TRAILER)
        record_vod(self.state, viejo, INF_CONCEDIDO,
                   "skipped:juego_no_permitido")
        action, reason = decide(viejo, INF_CONCEDIDO, self.state,
                                self.q, self.cfg, False, True)
        self.assertEqual(action, "seen")
        self.assertIn("definitivo", reason)

    def test_skip_permanente_de_vod_en_curso_se_reevalua(self):
        # C5: el VOD estaba en curso cuando se marcó (live=True):
        # se re-evalúa aunque trajera categoría registrada.
        viejo = dict(GTA6_VOD, live=True)
        record_vod(self.state, viejo, INF_CONCEDIDO,
                   "skipped:juego_no_permitido")
        nuevo = dict(GTA6_VOD, live=False)
        action, _ = decide(nuevo, INF_CONCEDIDO, self.state,
                           self.q, self.cfg, False, True)
        self.assertEqual(action, "would_enqueue")


class TestRunOnceVodEnCurso(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.state_path = os.path.join(self.tmp, "seen.json")
        self.db_path = os.path.join(self.tmp, "q.db")
        self.cfg = {"max_new_per_day": 5, "poll_minutes": 15,
                    "state_file": self.state_path}

    def _fake_twitch(self, live, vods):
        fake_client = mock.Mock()
        fake_client.check.return_value = (live, vods)
        return mock.patch.object(watcher, "twitch_client",
                                 return_value=(fake_client, "helix-test"))

    def test_vod_en_curso_no_llega_a_decide(self):
        # C5: el VOD 2932415021 en curso se registra como
        # skipped:vod_en_curso (reintentable) y nunca se encola ni se
        # marca juego_no_permitido.
        from datetime import datetime, timezone
        live = {"id": "51515151515", "title": "🌴GTA VI🌴 DIRECTO",
                "game": "Grand Theft Auto VI", "viewers": 329979,
                "started_at": "2026-11-19T18:00:00Z"}
        vod = {"platform": "twitch", "vod_id": "2932415021",
               "url": "https://www.twitch.tv/videos/2932415021",
               "title": "🌴GTA VI🌴 DIRECTO",
               "category": "Grand Theft Auto VI",
               "category_source": "directo-en-curso",
               "start_time": datetime(2026, 11, 19, 18, 0, 5,
                                      tzinfo=timezone.utc),
               "duration_s": 3600, "views": 1000, "live": True}
        with self._fake_twitch(live, [vod]):
            summary, errors = run_once(
                [INF_RUBIUS], self.cfg, self.state_path, self.db_path,
                dry_run=True, verbose=False)
        self.assertEqual(errors, 0)
        self.assertEqual(summary["would_enqueue"], 0)
        self.assertEqual(summary["enqueued"], 0)
        state = load_state(self.state_path)
        rec = state["vods"]["2932415021"]
        self.assertEqual(rec["outcome"], "skipped:vod_en_curso")
        self.assertTrue(rec["live"])

    def test_rescate_vod_marcado_sin_categoria_se_reprocesa(self):
        # Rescate 2932415021: marcado skipped:juego_no_permitido con
        # categoría vacía (bug C4) -> la pasada lo re-evalúa con la
        # categoría GQL y lo propone (would_enqueue en dry-run).
        from datetime import datetime, timezone
        state = load_state(self.state_path)
        viejo = {"platform": "twitch", "vod_id": "2932415021",
                 "url": "https://www.twitch.tv/videos/2932415021",
                 "title": "🌴GTA VI🌴 DIRECTO",
                 "category": "", "start_time": None,
                 "duration_s": 26000, "views": 0, "live": False}
        record_vod(state, viejo, INF_RUBIUS,
                   "skipped:juego_no_permitido")
        save_state(self.state_path, state)
        vod = dict(viejo, category="Grand Theft Auto VI",
                   category_source="gql",
                   start_time=datetime(2026, 11, 19, 18, 0, 5,
                                       tzinfo=timezone.utc))
        with self._fake_twitch(None, [vod]):
            summary, errors = run_once(
                [INF_RUBIUS], self.cfg, self.state_path, self.db_path,
                dry_run=True, verbose=False)
        self.assertEqual(errors, 0)
        self.assertEqual(summary["would_enqueue"], 1)


class TestRunOnceC7(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.state_path = os.path.join(self.tmp, "seen.json")
        self.db_path = os.path.join(self.tmp, "q.db")
        self.cfg = {"max_new_per_day": 5, "poll_minutes": 15,
                    "state_file": self.state_path}

    def test_youtube_es_aviso_no_error_fatal(self):
        # C7: ishowspeed [youtube] se salta con motivo visible y no
        # suma a errores: la pasada no tumba el paso watch.
        inf = {"usuario": "ishowspeed", "plataforma": "youtube",
               "idioma": "en", "juego": "GTA VI", "estado": "concedido",
               "canal_original": ""}
        summary, errors = run_once([inf], self.cfg, self.state_path,
                                   self.db_path, dry_run=True, verbose=False)
        self.assertEqual(errors, 0)
        self.assertEqual(summary["warnings"], 1)
        self.assertTrue(any("ishowspeed" in w and "youtube" in w
                            for w in summary["skipped_influencers"]))

    def test_main_youtube_solo_sale_0(self):
        # C7: una pasada con solo ishowspeed (youtube) sale con
        # código 0, no 2.
        inf_path = os.path.join(self.tmp, "streamers.yaml")
        with open(inf_path, "w", encoding="utf-8") as f:
            f.write("streamers:\n"
                    "  - name: ishowspeed\n"
                    "    platform: youtube\n"
                    "    language: en\n"
                    "    estado: concedido\n")
        rc = watcher.main(["--once", "--dry-run",
                           "--influencers", inf_path,
                           "--state", self.state_path,
                           "--db", self.db_path])
        self.assertEqual(rc, 0)

    def test_error_de_influencer_es_sigue_siendo_fatal(self):
        # C7: un fallo que impide vigilar a un streamer ES sigue
        # contando como error (exit 2).
        inf = dict(INF_CONCEDIDO, plataforma="twitch", usuario="rubius")
        with mock.patch.object(watcher, "twitch_client",
                               side_effect=WatcherError("red caída")):
            summary, errors = run_once(
                [inf], self.cfg, self.state_path, self.db_path,
                dry_run=True, verbose=False)
        self.assertEqual(errors, 1)
        self.assertEqual(summary["warnings"], 0)

    def test_error_de_influencer_en_es_aviso(self):
        # C7: el mismo fallo en un influencer EN es aviso, no fatal.
        inf = dict(INF_CONCEDIDO, plataforma="twitch", usuario="kaicenat",
                   idioma="en")
        with mock.patch.object(watcher, "twitch_client",
                               side_effect=WatcherError("red caída")):
            summary, errors = run_once(
                [inf], self.cfg, self.state_path, self.db_path,
                dry_run=True, verbose=False)
        self.assertEqual(errors, 0)
        self.assertEqual(summary["warnings"], 1)
        self.assertTrue(any("kaicenat" in w
                            for w in summary["skipped_influencers"]))


if __name__ == "__main__":
    unittest.main(verbosity=2)

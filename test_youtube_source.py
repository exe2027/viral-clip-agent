#!/usr/bin/env python3
"""Tests de la fuente YouTube (2026-11-21, sin red).

``clipagent.youtube``: ``youtube_check`` (yt-dlp --flat-playlist
JSON, sin API key, $0), verificación del juego por título +
canal, y descarga por segmentos con --download-sections.

Fixtures: el VOD real de Speed del run
``runs/speed-52h-save-2026-11-21`` (``OIeLbmyraRQ``, "BEATING
GTA 6 RIGHT NOW 100% (FINAL MISSION)", 20.934.651 vistas) y la
ventana del clip 05:31:46-05:32:34. Nada toca la red: el runner
de yt-dlp se inyecta falso.

Uso: python3 test_youtube_source.py
"""

import json
import os
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

from clipagent.youtube import (  # noqa: E402
    YoutubeError,
    _entry_to_vod,
    channel_videos_url,
    download_youtube_segment,
    format_hms,
    youtube_check,
    youtube_game_from_title,
    youtube_vod_is_gta6,
)

# --- Fixtures: --flat-playlist -J real (forma) del canal de Speed ---
# Verificado en vivo 2026-11-21: la pestaña /streams trae los
# directos pasados (el maratón de 52 h: OIeLbmyraRQ, hwSg39yIBGU,
# qSyo5d-dVzA) y /videos solo subidas normales (el tráiler). Las
# entradas NO traen upload_date ni channel/uploader: traen
# timestamp (casi siempre None), uploader_url y live_status
# ("was_live" = directo ya terminado, VOD válido).
SPEED_STREAMS = {
    "id": "UCWsDFcIhY2DBi3GB5uykGXA",
    "title": "IShowSpeed - Live",
    "entries": [
        {
            "id": "OIeLbmyraRQ",
            "title": "🔴 BEATING GTA 6 RIGHT NOW 100% (FINAL MISSION)",
            "url": "https://www.youtube.com/watch?v=OIeLbmyraRQ",
            "duration": 22061,
            "view_count": 20934651,
            "timestamp": None,
            "live_status": "was_live",
            "uploader_url": "https://www.youtube.com/@IShowSpeed",
            "categories": [],
        },
        {
            "id": "hwSg39yIBGU",
            "title": "🔴PLAYING GTA 6 RIGHT NOW GET IN HERE!",
            "url": "https://www.youtube.com/watch?v=hwSg39yIBGU",
            "duration": 85283,
            "view_count": 15000000,
            "timestamp": None,
            "live_status": "was_live",
            "uploader_url": "https://www.youtube.com/@IShowSpeed",
            "categories": [],
        },
        {
            "id": "LIVE12345678",
            "title": "🔴 BEATING GTA 6 RIGHT NOW",
            "url": "https://www.youtube.com/watch?v=LIVE12345678",
            "duration": None,
            "view_count": 400000,
            "timestamp": None,
            "live_status": "is_live",
            "uploader_url": "https://www.youtube.com/@IShowSpeed",
            "categories": [],
        },
    ],
}
SPEED_VIDEOS = {
    "id": "UCWsDFcIhY2DBi3GB5uykGXA",
    "title": "IShowSpeed - Videos",
    "entries": [
        {
            "id": "2BRL4ALMCyA",
            "title": "iSHOWSPEED x GTA 6 | OFFICIAL TRAILER",
            "url": "https://www.youtube.com/watch?v=2BRL4ALMCyA",
            "duration": 67,
            "view_count": 8000000,
            "timestamp": None,
            "live_status": "not_live",
            "uploader_url": "https://www.youtube.com/@IShowSpeed",
            "categories": [],
        },
        {
            "id": "IRL00000001",
            "title": "I BOUGHT A $100,000 CAR",
            "url": "https://www.youtube.com/watch?v=IRL00000001",
            "duration": 1200,
            "view_count": 5000000,
            "timestamp": None,
            "live_status": "not_live",
            "uploader_url": "https://www.youtube.com/@IShowSpeed",
            "categories": [],
        },
        # Duplicado entre pestañas: se cuenta una sola vez.
        {
            "id": "OIeLbmyraRQ",
            "title": "🔴 BEATING GTA 6 RIGHT NOW 100% (FINAL MISSION)",
            "url": "https://www.youtube.com/watch?v=OIeLbmyraRQ",
            "duration": 22061,
            "view_count": 20934651,
            "timestamp": None,
            "live_status": "was_live",
            "uploader_url": "https://www.youtube.com/@IShowSpeed",
            "categories": [],
        },
    ],
}


class _Proc:
    """CompletedProcess mínimo (returncode / stdout / stderr)."""

    def __init__(self, returncode=0, stdout="", stderr=""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


class _Runner:
    """Runner falso de yt-dlp: devuelve los resultados en orden y
    registra los comandos y los sleeps."""

    def __init__(self, results, touch_out=False, touch_suffix=""):
        self.results = list(results)
        self.cmds = []
        self.sleeps = []
        self.touch_out = touch_out
        self.touch_suffix = touch_suffix

    def __call__(self, cmd):
        self.cmds.append(list(cmd))
        if self.touch_out and "-o" in cmd:
            out = cmd[cmd.index("-o") + 1]
            open(out + self.touch_suffix, "wb").close()
        if len(self.results) > 1:
            return self.results.pop(0)
        return self.results[0]

    def sleep(self, seconds):
        self.sleeps.append(seconds)


def _ok(data):
    return _Proc(0, json.dumps(data))


BOT_FAIL = _Proc(1, "", "Sign in to confirm you're not a bot")

# Pestaña /live sin directo: yt-dlp sale con error (respuesta
# definitiva, no un fallo: no se reintenta).
NOT_LIVE_FAIL = _Proc(
    1, "", "ERROR: [youtube:tab] @IShowSpeed: The channel is not currently live"
)

# Misma lista de /streams sin el directo en curso: para probar
# la pestaña /live como única fuente del directo.
SPEED_STREAMS_SIN_VIVO = {
    "id": SPEED_STREAMS["id"],
    "title": SPEED_STREAMS["title"],
    "entries": [
        e for e in SPEED_STREAMS["entries"]
        if e.get("live_status") != "is_live"
    ],
}

# Pestaña /live con directo en curso: resuelve al vídeo (dict,
# sin entries). Forma real 2026-11-21 (canal @LofiGirl en vivo,
# campos recortados): live_status is_live, concurrent_view_count
# y timestamp (epoch).
LIVE_TAB_VIDEO = {
    "id": "LIVE12345678",
    "title": "🔴 BEATING GTA 6 RIGHT NOW",
    "live_status": "is_live",
    "is_live": True,
    "view_count": 500000,
    "concurrent_view_count": 12000,
    "timestamp": 1795267200,
    "upload_date": "20261121",
    "duration": None,
    "uploader": "IShowSpeed",
    "channel": "IShowSpeed",
    "categories": ["Gaming"],
}


class TestFormatHms(unittest.TestCase):
    def test_casos(self):
        self.assertEqual(format_hms(0), "00:00:00")
        self.assertEqual(format_hms(19906), "05:31:46")
        self.assertEqual(format_hms(22061), "06:07:41")
        # Un VOD de YouTube puede durar 24 h: horas > 24 valen.
        self.assertEqual(format_hms(90061), "25:01:01")

    def test_negativo_falla(self):
        with self.assertRaises(ValueError):
            format_hms(-1)


class TestChannelVideosUrl(unittest.TestCase):
    def test_handle(self):
        self.assertEqual(
            channel_videos_url("@IShowSpeed"),
            "https://www.youtube.com/@IShowSpeed/videos",
        )
        self.assertEqual(
            channel_videos_url("IShowSpeed"),
            "https://www.youtube.com/@IShowSpeed/videos",
        )

    def test_url_completa_y_barra(self):
        self.assertEqual(
            channel_videos_url("https://www.youtube.com/@IShowSpeed/"),
            "https://www.youtube.com/@IShowSpeed/videos",
        )
        self.assertEqual(
            channel_videos_url("https://www.youtube.com/@IShowSpeed/videos"),
            "https://www.youtube.com/@IShowSpeed/videos",
        )

    def test_sin_url_falla(self):
        with self.assertRaises(YoutubeError):
            channel_videos_url("")


class TestEntryToVod(unittest.TestCase):
    def test_forma_real_sin_fecha(self):
        # Forma real 2026-11-21: sin upload_date, timestamp None,
        # canal en uploader_url, was_live = VOD terminado.
        vod = _entry_to_vod(SPEED_STREAMS["entries"][0])
        self.assertEqual(vod["vod_id"], "OIeLbmyraRQ")
        self.assertEqual(vod["platform"], "youtube")
        self.assertEqual(vod["duration_s"], 22061)
        self.assertEqual(vod["views"], 20934651)
        self.assertIsNone(vod["start_time"])
        self.assertFalse(vod["live"])
        self.assertEqual(vod["channel"], "@IShowSpeed")

    def test_timestamp_epoch_y_upload_date(self):
        vod = _entry_to_vod(
            {
                "id": "X",
                "title": "T",
                "duration": 60,
                "timestamp": 1795267200,
                "live_status": "not_live",
            }
        )
        self.assertEqual(vod["start_time"].strftime("%Y-%m-%d"), "2026-11-21")
        vod2 = _entry_to_vod(
            {
                "id": "Y",
                "title": "T",
                "duration": 60,
                "upload_date": "20261120",
                "live_status": "not_live",
            }
        )
        self.assertEqual(vod2["start_time"].strftime("%Y-%m-%d"), "2026-11-20")


class TestYoutubeCheck(unittest.TestCase):
    def test_devuelve_forma_watcher(self):
        runner = _Runner([_ok(SPEED_STREAMS), _ok(SPEED_VIDEOS)])
        live, vods = youtube_check(
            "https://www.youtube.com/@IShowSpeed",
            runner=runner,
            sleep=runner.sleep,
        )
        # /streams primero: el más reciente es el maratón (20-nov).
        self.assertEqual(vods[0]["vod_id"], "OIeLbmyraRQ")
        self.assertEqual(
            vods[0]["url"], "https://www.youtube.com/watch?v=OIeLbmyraRQ"
        )
        self.assertEqual(
            vods[0]["title"], "🔴 BEATING GTA 6 RIGHT NOW 100% (FINAL MISSION)"
        )
        self.assertEqual(vods[0]["platform"], "youtube")
        self.assertEqual(vods[0]["duration_s"], 22061)
        self.assertEqual(vods[0]["views"], 20934651)
        self.assertIsNone(vods[0]["start_time"])
        self.assertFalse(vods[0]["live"])
        self.assertEqual(vods[0]["channel"], "@IShowSpeed")
        # El directo en curso ya no es None: viene de la entrada
        # is_live de /streams (la pestaña /live devuelve en este
        # fixture el listado de /videos, sin is_live).
        self.assertEqual(live["id"], "LIVE12345678")
        self.assertEqual(live["title"], "🔴 BEATING GTA 6 RIGHT NOW")
        self.assertEqual(live["game"], "gta6")
        self.assertEqual(live["viewers"], 400000)
        self.assertEqual(live["started_at"], "")
        # El directo en curso viene marcado live (se salta como
        # "en curso", no como VOD terminado).
        live_entry = [v for v in vods if v["vod_id"] == "LIVE12345678"][0]
        self.assertTrue(live_entry["live"])
        # El duplicado entre pestañas se cuenta una sola vez, y el
        # tráiler de /videos también llega.
        ids = [v["vod_id"] for v in vods]
        self.assertEqual(ids.count("OIeLbmyraRQ"), 1)
        self.assertIn("2BRL4ALMCyA", ids)
        self.assertEqual(len(vods), 5)
        # Tres llamadas: /streams, /videos y /live (directo).
        self.assertEqual(len(runner.cmds), 3)
        self.assertTrue(runner.cmds[0][-1].endswith("/streams"))
        self.assertTrue(runner.cmds[1][-1].endswith("/videos"))
        self.assertTrue(runner.cmds[2][-1].endswith("/live"))
        # Sin --flat-playlist no hay listado: el comando lo lleva.
        self.assertIn("--flat-playlist", runner.cmds[0])
        self.assertIn("-J", runner.cmds[0])
        # Certificados del sistema, no certifi (en esta VM el
        # proxy de salida solo vive en el almacén del sistema).
        self.assertIn("no-certifi", runner.cmds[0])

    def test_shorts_tab_solo_con_flag(self):
        # Forma real 2026-11-21: /shorts trae id/título/view_count
        # (sin duración, fecha ni categoría). Sin el flag no se
        # lee; con él, la entrada llega marcada from_shorts_tab.
        shorts = {"id": "speed-shorts", "title": "Shorts",
                  "entries": [{"id": "kVk_Nwca6Wk",
                               "title": "I BEAT GTA 6!",
                               "view_count": 4000000}]}
        runner = _Runner([_ok(SPEED_STREAMS), _ok(SPEED_VIDEOS),
                          _ok(shorts)])
        _live, vods = youtube_check(
            "https://www.youtube.com/@IShowSpeed",
            runner=runner, sleep=runner.sleep,
            include_shorts_tab=True)
        short = [v for v in vods if v["vod_id"] == "kVk_Nwca6Wk"][0]
        self.assertTrue(short["from_shorts_tab"])
        self.assertEqual(short["duration_s"], 0)
        self.assertEqual(short["views"], 4000000)
        self.assertEqual(short["title"], "I BEAT GTA 6!")
        self.assertEqual(short["channel"], "@IShowSpeed")
        self.assertFalse(short["live"])
        self.assertEqual(len(runner.cmds), 4)
        self.assertTrue(runner.cmds[2][-1].endswith("/shorts"))
        self.assertTrue(runner.cmds[3][-1].endswith("/live"))
        # Sin el flag no se lee /shorts (el watcher no quiere
        # VODs sin duración ni fecha).
        runner2 = _Runner([_ok(SPEED_STREAMS), _ok(SPEED_VIDEOS)])
        _live2, vods2 = youtube_check(
            "https://www.youtube.com/@IShowSpeed",
            runner=runner2, sleep=runner2.sleep)
        self.assertNotIn("kVk_Nwca6Wk", [v["vod_id"] for v in vods2])
        self.assertEqual(len(runner2.cmds), 3)

    def test_canal_viene_de_la_url_pedida(self):
        # En vivo las entradas traen uploader_url/channel_url en
        # None: el canal del VOD es el canal pedido, no vacío.
        import copy

        streams = copy.deepcopy(SPEED_STREAMS)
        videos = copy.deepcopy(SPEED_VIDEOS)
        for tab in (streams, videos):
            for entry in tab["entries"]:
                entry.pop("uploader_url", None)
                entry.pop("channel_url", None)
                entry.pop("channel", None)
                entry.pop("uploader", None)
        runner = _Runner([_ok(streams), _ok(videos)])
        _live, vods = youtube_check(
            "https://www.youtube.com/@IShowSpeed",
            runner=runner,
            sleep=runner.sleep,
        )
        self.assertTrue(all(v["channel"] == "@IShowSpeed" for v in vods))
        self.assertTrue(
            youtube_vod_is_gta6(vods[0], expected_channel="@IShowSpeed")
        )

    def test_sin_videos_lista_vacia(self):
        runner = _Runner([_ok({"entries": []}), _ok({"entries": []})])
        live, vods = youtube_check(
            "@IShowSpeed", runner=runner, sleep=runner.sleep
        )
        self.assertIsNone(live)
        self.assertEqual(vods, [])

    def test_reintenta_bot_check(self):
        # Probado en el run de Speed: los intentos 1-2 los bloquea
        # el bot-check y el 3º funciona.
        runner = _Runner(
            [BOT_FAIL, BOT_FAIL, _ok(SPEED_STREAMS), _ok(SPEED_VIDEOS)]
        )
        live, vods = youtube_check(
            "@IShowSpeed", runner=runner, sleep=runner.sleep
        )
        # /streams reintenta 2 veces (3 llamadas) + /videos + /live
        # = 5 en total. El directo viene de la entrada is_live de
        # /streams (la pestaña /live devuelve el listado de
        # /videos, sin is_live).
        self.assertEqual(len(runner.cmds), 5)
        self.assertEqual(len(runner.sleeps), 2)
        self.assertEqual(vods[0]["vod_id"], "OIeLbmyraRQ")
        self.assertEqual(live["id"], "LIVE12345678")

    def test_falla_tras_reintentos(self):
        runner = _Runner([BOT_FAIL])
        with self.assertRaises(YoutubeError):
            youtube_check("@IShowSpeed", runner=runner, sleep=runner.sleep)
        self.assertEqual(len(runner.cmds), 3)

    def test_json_invalido_falla(self):
        runner = _Runner([_Proc(0, "esto no es json")])
        with self.assertRaises(YoutubeError):
            youtube_check("@IShowSpeed", runner=runner, sleep=runner.sleep)


class TestYoutubeLive(unittest.TestCase):
    """Detección del directo en curso (2026-11-21).

    La pestaña /live manda; si falla, la entrada is_live de
    /streams. Un fallo de /live no tumba el chequeo: live=None
    y el listado de VODs sigue.
    """

    def test_live_pestana_live(self):
        # Solo /live trae el directo (en /streams no hay is_live):
        # live con la forma del watcher y el VOD añadido al
        # listado como en curso (para que el watcher lo registre).
        runner = _Runner(
            [_ok(SPEED_STREAMS_SIN_VIVO), _ok(SPEED_VIDEOS),
             _ok(LIVE_TAB_VIDEO)]
        )
        live, vods = youtube_check(
            "@IShowSpeed", runner=runner, sleep=runner.sleep
        )
        self.assertEqual(live["id"], "LIVE12345678")
        self.assertEqual(live["title"], "🔴 BEATING GTA 6 RIGHT NOW")
        self.assertEqual(live["game"], "gta6")
        # Espectadores concurrentes, no el total de vistas.
        self.assertEqual(live["viewers"], 12000)
        self.assertTrue(live["started_at"].startswith("2026-11-21"))
        live_entry = [v for v in vods if v["vod_id"] == "LIVE12345678"][0]
        self.assertTrue(live_entry["live"])
        self.assertEqual(live_entry["duration_s"], 0)
        self.assertEqual(len(vods), 5)
        self.assertEqual(len(runner.cmds), 3)
        self.assertTrue(runner.cmds[2][-1].endswith("/live"))

    def test_live_sin_directo(self):
        # Sin directo, /live sale con error "not currently live":
        # respuesta definitiva (sin reintentos en esa pestaña),
        # live None y el listado sigue.
        runner = _Runner(
            [_ok(SPEED_STREAMS_SIN_VIVO), _ok(SPEED_VIDEOS), NOT_LIVE_FAIL]
        )
        live, vods = youtube_check(
            "@IShowSpeed", runner=runner, sleep=runner.sleep
        )
        self.assertIsNone(live)
        self.assertEqual(len(vods), 4)
        self.assertEqual(len(runner.cmds), 3)
        self.assertEqual(len(runner.sleeps), 0)

    def test_live_desde_streams_si_live_falla(self):
        # /live con bot-check (3 intentos) pero /streams trae el
        # is_live: el directo viene de /streams, no se pierde.
        runner = _Runner(
            [_ok(SPEED_STREAMS), _ok(SPEED_VIDEOS),
             BOT_FAIL, BOT_FAIL, BOT_FAIL]
        )
        live, vods = youtube_check(
            "@IShowSpeed", runner=runner, sleep=runner.sleep
        )
        self.assertEqual(live["id"], "LIVE12345678")
        self.assertEqual(live["game"], "gta6")
        self.assertEqual(len(vods), 5)
        self.assertEqual(len(runner.cmds), 5)
        self.assertEqual(len(runner.sleeps), 2)

    def test_live_bot_check_no_tumba(self):
        # /live con bot-check tras los reintentos y sin is_live en
        # /streams: live None, pero el listado de VODs llega igual.
        runner = _Runner(
            [_ok(SPEED_STREAMS_SIN_VIVO), _ok(SPEED_VIDEOS),
             BOT_FAIL, BOT_FAIL, BOT_FAIL]
        )
        live, vods = youtube_check(
            "@IShowSpeed", runner=runner, sleep=runner.sleep
        )
        self.assertIsNone(live)
        self.assertEqual(len(vods), 4)
        self.assertEqual(len(runner.cmds), 5)
        self.assertEqual(len(runner.sleeps), 2)


class TestYoutubeVodIsGta6(unittest.TestCase):
    def setUp(self):
        self.speed = {
            "vod_id": "OIeLbmyraRQ",
            "title": "🔴 BEATING GTA 6 RIGHT NOW 100% (FINAL MISSION)",
            "channel": "@IShowSpeed",
            "category": "",
        }
        self.irl = {
            "vod_id": "IRL00000001",
            "title": "I BOUGHT A $100,000 CAR",
            "channel": "@IShowSpeed",
            "category": "",
        }

    def test_titulo_gta6(self):
        self.assertTrue(youtube_vod_is_gta6(self.speed))
        self.assertTrue(
            youtube_vod_is_gta6(self.speed, expected_channel="@IShowSpeed")
        )

    def test_titulo_sin_gta6(self):
        self.assertFalse(youtube_vod_is_gta6(self.irl))

    def test_canal_que_no_coincide(self):
        # El título menciona GTA VI pero el VOD no es de Speed.
        self.assertFalse(
            youtube_vod_is_gta6(self.speed, expected_channel="@OtherChannel")
        )

    def test_categoria_gta6_sin_mencion_en_titulo(self):
        vod = {
            "title": "DIRECTO",
            "channel": "IShowSpeed",
            "category": "Grand Theft Auto VI",
        }
        self.assertTrue(youtube_vod_is_gta6(vod))

    def test_game_from_title(self):
        self.assertEqual(
            youtube_game_from_title(
                "🔴 BEATING GTA 6 RIGHT NOW 100% (FINAL MISSION)"
            ),
            "gta6",
        )
        self.assertIsNone(youtube_game_from_title("I BOUGHT A $100,000 CAR"))


class TestDownloadSegment(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.out = os.path.join(self.tmp, "segment.mp4")
        self.url = "https://www.youtube.com/watch?v=OIeLbmyraRQ"

    def test_construye_comando_del_run_de_speed(self):
        runner = _Runner([_Proc(0, "")], touch_out=True)
        got = download_youtube_segment(
            self.url, 19906, 19954, self.out, runner=runner, sleep=runner.sleep
        )
        self.assertEqual(got, self.out)
        cmd = runner.cmds[0]
        # La ventana del clip de Speed: 05:31:46-05:32:34.
        self.assertIn("--download-sections", cmd)
        self.assertIn("*05:31:46-05:32:34", cmd)
        self.assertIn("no-certifi", cmd)
        self.assertIn("-f", cmd)
        self.assertIn("298+140", cmd[cmd.index("-f") + 1])
        self.assertIn(self.url, cmd)
        self.assertTrue(os.path.isfile(self.out))

    def test_reintenta_bot_check(self):
        runner = _Runner([BOT_FAIL, BOT_FAIL, _Proc(0, "")], touch_out=True)
        got = download_youtube_segment(
            self.url, 19906, 19954, self.out, runner=runner, sleep=runner.sleep
        )
        self.assertEqual(got, self.out)
        self.assertEqual(len(runner.cmds), 3)
        self.assertEqual(len(runner.sleeps), 2)

    def test_falla_tras_reintentos(self):
        runner = _Runner([BOT_FAIL])
        with self.assertRaises(YoutubeError):
            download_youtube_segment(
                self.url,
                19906,
                19954,
                self.out,
                runner=runner,
                sleep=runner.sleep,
            )
        self.assertEqual(len(runner.cmds), 3)
        self.assertFalse(os.path.isfile(self.out))

    def test_formato_webm_devuelve_ruta_real(self):
        # 2026-11-26: los Shorts de Speed bajan en webm (396+251):
        # yt-dlp escribe segment.mp4.webm y el isfile de
        # segment.mp4 fallaba con código 0 aunque la descarga
        # estaba completa. La función devuelve la ruta real.
        runner = _Runner([_Proc(0, "")], touch_out=True,
                         touch_suffix=".webm")
        got = download_youtube_segment(
            self.url, 0, 34, self.out, runner=runner, sleep=runner.sleep
        )
        self.assertEqual(got, self.out + ".webm")
        self.assertTrue(os.path.isfile(self.out + ".webm"))
        self.assertFalse(os.path.isfile(self.out))

    def test_codigo_0_sin_archivo_falla(self):
        # Código 0 pero sin archivo (ni con otra extensión):
        # sigue siendo fallo, no se devuelve una ruta fantasma.
        runner = _Runner([_Proc(0, "")])
        with self.assertRaises(YoutubeError):
            download_youtube_segment(
                self.url, 0, 34, self.out, runner=runner,
                sleep=runner.sleep,
            )

    def test_segmento_invalido(self):
        runner = _Runner([_Proc(0, "")])
        with self.assertRaises(ValueError):
            download_youtube_segment(
                self.url, 100, 100, self.out, runner=runner, sleep=runner.sleep
            )
        self.assertEqual(runner.cmds, [])


if __name__ == "__main__":
    unittest.main(verbosity=2)

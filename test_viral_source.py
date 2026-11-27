"""Tests de la fuente viral (clipagent/viral_source.py).

2026-11-21: la fuente viral es parte permanente del sistema
(orden del usuario 12:31). Estos tests cubren la config (única
fuente, falla fuerte), el descubrimiento/ranking, el
enriquecimiento (likes/comentarios/edad), los claims (un vídeo, UNA sola
cuenta) y el re-make + encolado (con el pipeline real mockeado
en sus costuras: yt-dlp, Whisper, ffmpeg y el panel).
"""

import json
import os
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from clipagent import viral_source as vs
from clipagent.config import load_config
from clipagent.jobs import PLATFORMS
from clipagent.validate import ConfigError


def _cfg(**over):
    """Config mínima con viral_source + scoring válidos."""
    cfg = {
        "viral_source": {
            "enabled": True,
            "min_views": 100000,
            "min_likes": 5000,
            "min_comments": 1000,
            "min_outlier_multiple": 3.0,
            "absolute_viral_views": 3000000,
            "max_age_days": 30,
            "max_candidates": 5,
            "max_per_run": 2,
            "shorts_only": True,
            "min_duration_s": 15,
            "max_duration_s": 60,
            "cooldown_days": 7,
            "youtube_channels": [],
            "state_file": "viral-source-claims.json",
        },
        "scoring": {"viral_min": 45, "seo_gate": 80},
    }
    cfg["viral_source"].update(over)
    return cfg


def _yt_streamers():
    return [
        {"name": "ishowspeed", "language": "en",
         "channel_url": "https://www.youtube.com/@IShowSpeed"},
        {"name": "illojuan", "language": "es",
         "channel_url": "https://www.youtube.com/@illojuan"},
    ]


def _vod(vid, title, views, duration, live=False, channel="@IShowSpeed",
         category="Grand Theft Auto VI", from_shorts_tab=False):
    return {
        "platform": "youtube", "vod_id": vid,
        "url": f"https://www.youtube.com/watch?v={vid}",
        "title": title, "category": category,
        "category_source": "youtube", "start_time": None,
        "duration_s": duration, "views": views, "live": live,
        "channel": channel, "from_shorts_tab": from_shorts_tab,
    }


def _meta(vid="abc123", likes=9000, views=500000, duration=45,
          upload_date="20261120", comments=1500):
    return {
        "id": vid, "title": "GTA 6: Speed steals a scooter",
        "channel": "IShowSpeed", "uploader": "IShowSpeed",
        "uploader_id": "@IShowSpeed", "channel_id": "UC123",
        "view_count": views, "like_count": likes,
        "comment_count": comments,
        "upload_date": upload_date, "duration": duration,
        "description": "Speed plays GTA 6.\nSecond line.",
        "tags": ["GTA 6", "Speed"],
    }


def _runner_for(meta):
    """Runner fake que devuelve la metadata dada a yt-dlp -J."""
    def _run(cmd):
        return SimpleNamespace(returncode=0,
                               stdout=json.dumps(meta), stderr="")
    return _run


class TestLoadViralConfig(unittest.TestCase):
    def test_config_del_repo(self):
        vcfg = vs.load_viral_config(load_config())
        self.assertTrue(vcfg["enabled"])
        # Umbrales 2026-11-26 11:48 (enmienda spec v3): piso 300k
        # vistas; likes y comentarios ya no filtran (en 0).
        self.assertEqual(vcfg["min_views"], 300000)
        self.assertEqual(vcfg["min_likes"], 0)
        self.assertEqual(vcfg["min_comments"], 0)
        self.assertEqual(vcfg["absolute_viral_views"], 3000000)
        self.assertEqual(vcfg["max_age_days"], 30)
        self.assertEqual(vcfg["max_per_run"], 2)
        self.assertTrue(vcfg["shorts_only"])
        self.assertEqual(vcfg["viral_min"], 45)
        self.assertEqual(vcfg["seo_gate"], 80)

    def test_sin_seccion_falla_fuerte(self):
        with self.assertRaises(ConfigError):
            vs.load_viral_config({"scoring": {"viral_min": 45,
                                              "seo_gate": 80}})

    def test_clave_faltante_falla_fuerte(self):
        cfg = _cfg()
        del cfg["viral_source"]["min_views"]
        with self.assertRaises(ConfigError):
            vs.load_viral_config(cfg)

    def test_min_comments_faltante_falla_fuerte(self):
        cfg = _cfg()
        del cfg["viral_source"]["min_comments"]
        with self.assertRaises(ConfigError):
            vs.load_viral_config(cfg)

    def test_sin_scoring_falla_fuerte(self):
        cfg = _cfg()
        del cfg["scoring"]
        with self.assertRaises(ConfigError):
            vs.load_viral_config(cfg)

    def test_valores_invalidos_fallan_fuerte(self):
        for over in ({"min_views": 0}, {"max_per_run": 0},
                     {"max_duration_s": 15, "min_duration_s": 15},
                     {"cooldown_days": -1}, {"min_comments": -1}):
            with self.assertRaises(ConfigError, msg=str(over)):
                vs.load_viral_config(_cfg(**over))


class TestYoutubeStreamers(unittest.TestCase):
    def test_filtra_youtube_y_excluye_denegados(self):
        streamers = [
            {"name": "ishowspeed", "platform": "youtube",
             "channel_url": "https://www.youtube.com/@IShowSpeed",
             "language": "en", "estado": "concedido"},
            {"name": "illojuan", "platform": "twitch",
             "channel_url": "https://www.twitch.tv/illojuan",
             "youtube_url": "https://www.youtube.com/@illojuan",
             "language": "es", "estado": "concedido"},
            {"name": "xokas", "platform": "twitch",
             "channel_url": "https://www.twitch.tv/xokas",
             "language": "es", "estado": "concedido"},
            {"name": "vetado", "platform": "youtube",
             "channel_url": "https://www.youtube.com/@vetado",
             "language": "es", "estado": "denegado"},
            {"name": "raro", "platform": "youtube",
             "channel_url": "https://www.youtube.com/@raro",
             "language": "xx", "estado": "concedido"},
        ]
        out = vs.youtube_streamers(streamers)
        self.assertEqual([s["name"] for s in out],
                         ["ishowspeed", "illojuan", "raro"])
        self.assertEqual(out[0]["language"], "en")
        self.assertEqual(out[2]["language"], "es")  # idioma raro -> es


class TestDiscover(unittest.TestCase):
    def test_rankea_filtra_y_capa(self):
        vods_speed = [
            _vod("v1", "GTA 6: Speed steals a scooter", 500000, 50),
            _vod("v2", "GTA 6: 52 hours gone", 900000, 48),
            _vod("v3", "GTA 6 full playthrough part 1", 2000000, 3600),
            _vod("v4", "Cooking with Speed", 800000, 40,
                 category="Just Chatting"),
            _vod("v5", "GTA 6: tiny moment", 50000, 30),
            _vod("v6", "GTA 6: live now", 700000, 45, live=True),
            # Pestaña /shorts (sin duración: es Short por
            # construcción) -> entra; sin la marca y sin duración
            # no se puede confirmar que sea un Short -> fuera.
            _vod("v7", "I BEAT GTA 6!", 4000000, 0,
                 from_shorts_tab=True),
            _vod("v8", "GTA 6: sin duración", 5000000, 0),
        ]
        calls = []

        def fake_check(url, runner=None, include_shorts_tab=False):
            calls.append((url, include_shorts_tab))
            if "IShowSpeed" in url:
                return None, list(vods_speed)
            return None, []

        vcfg = vs.load_viral_config(_cfg(max_candidates=2))
        with mock.patch.object(vs, "youtube_check", fake_check):
            out = vs.discover_candidates(_yt_streamers(), vcfg)
        # v3 fuera (no es Short), v4 fuera (no es GTA 6),
        # v5 fuera (< min_views), v6 fuera (en vivo),
        # v8 fuera (duración desconocida y no es de /shorts).
        # Rankeados por vistas: v7 (4 M, de /shorts) antes que
        # v2 (900k), capados a max_candidates=2.
        self.assertEqual([c["video_id"] for c in out], ["v7", "v2"])
        self.assertEqual(out[0]["streamer"], "ishowspeed")
        self.assertEqual(out[0]["language"], "en")
        self.assertTrue(out[0]["from_shorts_tab"])
        self.assertEqual(len(calls), 2)
        # La fuente viral lee la pestaña /shorts (si no, no hay
        # Shorts de GTA 6: /videos solo trae 11 y ninguno es GTA).
        self.assertTrue(all(flag for _url, flag in calls))

    def test_canal_caido_no_tumba_la_corrida(self):
        from clipagent.youtube import YoutubeError

        def fake_check(url, runner=None, **kwargs):
            raise YoutubeError("bot-check")

        vcfg = vs.load_viral_config(_cfg())
        with mock.patch.object(vs, "youtube_check", fake_check):
            out = vs.discover_candidates(_yt_streamers(), vcfg)
        self.assertEqual(out, [])


class TestOutlier(unittest.TestCase):
    """Outlier multiple (2026-11-21, ig-viral): vistas / mediana
    de ESA cuenta. >3x es señal; los likes crudos no son
    evidencia."""

    def _shorts_listing(self, median_views, n=12):
        """Listado fake de /shorts: n Shorts con ~median_views."""
        return [_vod(f"s{i}", f"Short {i}", median_views, 0,
                     from_shorts_tab=True)
                for i in range(n)]

    def test_mediana_de_los_ultimos_shorts(self):
        vods = self._shorts_listing(10000)
        # La mediana de 12 Shorts de 10k es 10k.
        self.assertEqual(vs.channel_shorts_median(vods), 10000)

    def test_sin_suficientes_shorts_no_hay_mediana(self):
        # Con 2 Shorts no se inventa mediana (mínimo 3).
        vods = self._shorts_listing(10000, n=2)
        self.assertIsNone(vs.channel_shorts_median(vods))
        self.assertIsNone(vs.channel_shorts_median([]))

    def test_outlier_multiple(self):
        self.assertEqual(vs.outlier_multiple(400000, 10000), 40.0)
        self.assertEqual(vs.outlier_multiple(400000, 300000), 1.33)
        self.assertIsNone(vs.outlier_multiple(400000, None))
        self.assertIsNone(vs.outlier_multiple(0, 10000))

    def test_400k_con_mediana_10k_pasa_40x(self):
        # 400k vistas con mediana 10k = 40x: señal, pasa.
        vods = (self._shorts_listing(10000)
                + [_vod("big", "GTA 6: gran momento", 400000, 40,
                        from_shorts_tab=True)])
        vcfg = vs.load_viral_config(_cfg(max_candidates=5))

        def fake_check(url, runner=None, **kwargs):
            return None, list(vods)

        with mock.patch.object(vs, "youtube_check", fake_check):
            out = vs.discover_candidates(
                [{"name": "speed", "language": "en",
                  "channel_url": "https://www.youtube.com/@IShowSpeed"}],
                vcfg)
        hit = [c for c in out if c["video_id"] == "big"]
        self.assertEqual(len(hit), 1)
        self.assertEqual(hit[0]["outlier"], 40.0)
        self.assertEqual(hit[0]["channel_median"], 10000)
        self.assertEqual(hit[0]["outlier_status"], "ok")

    def test_400k_con_mediana_300k_se_rechaza_1_3x(self):
        # 400k vistas con mediana 300k = 1,33x: su día normal,
        # no enseña nada -> fuera, con motivo. (Los 12 Shorts
        # de 300k también superan min_views y también se
        # rechazan por outlier 1,0x: es el comportamiento
        # correcto, no un fallo del test.)
        vods = (self._shorts_listing(300000)
                + [_vod("big", "GTA 6: gran momento", 400000, 40,
                        from_shorts_tab=True)])
        vcfg = vs.load_viral_config(_cfg(max_candidates=5))
        rejected = []

        def fake_check(url, runner=None, **kwargs):
            return None, list(vods)

        with mock.patch.object(vs, "youtube_check", fake_check):
            out = vs.discover_candidates(
                [{"name": "speed", "language": "en",
                  "channel_url": "https://www.youtube.com/@IShowSpeed"}],
                vcfg, rejected=rejected)
        self.assertEqual(out, [])
        self.assertIn("big", [r["video_id"] for r in rejected])
        big = [r for r in rejected if r["video_id"] == "big"][0]
        self.assertIn("1.33x", big["motivo"])
        self.assertIn("3.0x", big["motivo"])

    def test_sin_mediana_pasa_por_fallback_vistas(self):
        # Sin Shorts suficientes no hay mediana: el candidato
        # pasa por el fallback (vistas >= min_views; los likes
        # se verifican en enrich) y queda marcado sin-mediana.
        vods = [_vod("big", "GTA 6: gran momento", 400000, 40,
                     from_shorts_tab=True)]
        vcfg = vs.load_viral_config(_cfg(max_candidates=5))

        def fake_check(url, runner=None, **kwargs):
            return None, list(vods)

        with mock.patch.object(vs, "youtube_check", fake_check):
            out = vs.discover_candidates(
                [{"name": "speed", "language": "en",
                  "channel_url": "https://www.youtube.com/@IShowSpeed"}],
                vcfg)
        self.assertEqual(len(out), 1)
        self.assertIsNone(out[0]["outlier"])
        self.assertIsNone(out[0]["channel_median"])
        self.assertEqual(out[0]["outlier_status"], "sin-mediana")

    def test_min_outlier_multiple_faltante_falla_fuerte(self):
        cfg = _cfg()
        del cfg["viral_source"]["min_outlier_multiple"]
        with self.assertRaises(ConfigError):
            vs.load_viral_config(cfg)

    def test_min_outlier_multiple_invalido_falla_fuerte(self):
        with self.assertRaises(ConfigError):
            vs.load_viral_config(_cfg(min_outlier_multiple=0))

    def test_7m_con_mediana_6m_pasa_por_absoluto(self):
        # Caso real Speed 2026-11-26: mediana 6,05M, candidato
        # 7,2M = 1,19x < 3,0x. Con >=3M vistas es viral absoluto:
        # pasa sin outlier, marcado "absoluto". (Los 3 Shorts de
        # la mediana también pasan: 6,05M >= 3M, también absoluto;
        # el override no distingue "el breakout" del "canal
        # grande": a ese nivel de vistas, ambos son virales.)
        vods = (self._shorts_listing(6050000, n=3)
                + [_vod("big", "GTA 6: gran momento", 7200000, 40,
                        from_shorts_tab=True)])
        vcfg = vs.load_viral_config(_cfg(max_candidates=5))
        rejected = []

        def fake_check(url, runner=None, **kwargs):
            return None, list(vods)

        with mock.patch.object(vs, "youtube_check", fake_check):
            out = vs.discover_candidates(
                [{"name": "speed", "language": "en",
                  "channel_url": "https://www.youtube.com/@IShowSpeed"}],
                vcfg, rejected=rejected)
        self.assertEqual([c["video_id"] for c in out],
                         ["big", "s0", "s1", "s2"])
        self.assertEqual(out[0]["outlier"], 1.19)
        self.assertEqual(out[0]["outlier_status"], "absoluto")
        self.assertTrue(all(c["outlier_status"] == "absoluto"
                            for c in out))

    def test_2m_con_mediana_6m_sigue_fuera(self):
        # Por debajo del absoluto (2M < 3M) el outlier sigue
        # mandando: 0,33x < 3,0x -> fuera, con motivo. (Los 3
        # Shorts de la mediana pasan por absoluto, 6,05M >= 3M;
        # el que se rechaza es solo "big".)
        vods = (self._shorts_listing(6050000, n=3)
                + [_vod("big", "GTA 6: gran momento", 2000000, 40,
                        from_shorts_tab=True)])
        vcfg = vs.load_viral_config(_cfg(max_candidates=5))
        rejected = []

        def fake_check(url, runner=None, **kwargs):
            return None, list(vods)

        with mock.patch.object(vs, "youtube_check", fake_check):
            out = vs.discover_candidates(
                [{"name": "speed", "language": "en",
                  "channel_url": "https://www.youtube.com/@IShowSpeed"}],
                vcfg, rejected=rejected)
        self.assertEqual([c["video_id"] for c in out],
                         ["s0", "s1", "s2"])
        self.assertNotIn("big", [c["video_id"] for c in out])
        big = [r for r in rejected if r["video_id"] == "big"][0]
        self.assertIn("0.33x", big["motivo"])

    def test_absolute_viral_views_faltante_falla_fuerte(self):
        cfg = _cfg()
        del cfg["viral_source"]["absolute_viral_views"]
        with self.assertRaises(ConfigError):
            vs.load_viral_config(cfg)

    def test_absolute_viral_views_invalido_falla_fuerte(self):
        with self.assertRaises(ConfigError):
            vs.load_viral_config(_cfg(absolute_viral_views=0))


class TestEnrich(unittest.TestCase):
    def test_enriquece_con_likes_tags_y_descripcion(self):
        cand = {"video_id": "abc123",
                "url": "https://www.youtube.com/watch?v=abc123",
                "title": "GTA 6: Speed steals a scooter",
                "views": 500000, "likes": None, "duration_s": 45,
                "channel": "@IShowSpeed", "streamer": "ishowspeed",
                "language": "en"}
        vcfg = vs.load_viral_config(_cfg())
        now = datetime(2026, 11, 21, 12, 0, tzinfo=timezone.utc)
        out = vs.enrich_candidate(
            cand, vcfg, runner=_runner_for(_meta()), now=now)
        self.assertIsNotNone(out)
        self.assertEqual(out["likes"], 9000)
        self.assertEqual(out["comments"], 1500)
        self.assertEqual(out["tags"], ["GTA 6", "Speed"])
        self.assertIn("Speed plays GTA 6", out["description"])

    def test_pocos_comentarios_fuera(self):
        cand = {"video_id": "abc123",
                "url": "https://www.youtube.com/watch?v=abc123",
                "title": "t", "views": 500000, "likes": None,
                "duration_s": 45, "channel": "", "streamer": "s",
                "language": "en"}
        vcfg = vs.load_viral_config(_cfg())
        out = vs.enrich_candidate(
            cand, vcfg, runner=_runner_for(_meta(comments=50)))
        self.assertIsNone(out)

    def test_pocos_likes_fuera(self):
        cand = {"video_id": "abc123",
                "url": "https://www.youtube.com/watch?v=abc123",
                "title": "t", "views": 500000, "likes": None,
                "duration_s": 45, "channel": "", "streamer": "s",
                "language": "en"}
        vcfg = vs.load_viral_config(_cfg())
        out = vs.enrich_candidate(
            cand, vcfg, runner=_runner_for(_meta(likes=100)))
        self.assertIsNone(out)

    def test_viejo_fuera(self):
        cand = {"video_id": "abc123",
                "url": "https://www.youtube.com/watch?v=abc123",
                "title": "t", "views": 500000, "likes": None,
                "duration_s": 45, "channel": "", "streamer": "s",
                "language": "en"}
        vcfg = vs.load_viral_config(_cfg())
        now = datetime(2026, 11, 21, 12, 0, tzinfo=timezone.utc)
        out = vs.enrich_candidate(
            cand, vcfg,
            runner=_runner_for(_meta(upload_date="20261001")),
            now=now)
        self.assertIsNone(out)

    def test_sin_metadata_fuera(self):
        cand = {"video_id": "abc123",
                "url": "https://www.youtube.com/watch?v=abc123",
                "title": "t", "views": 500000, "likes": None,
                "duration_s": 45, "channel": "", "streamer": "s",
                "language": "en"}
        vcfg = vs.load_viral_config(_cfg())

        def _fail(cmd):
            return SimpleNamespace(returncode=1, stdout="", stderr="x")

        self.assertIsNone(vs.enrich_candidate(
            cand, vcfg, runner=_fail, sleep=lambda s: None))

    def test_reintenta_bot_check(self):
        # Bot-check intermitente de YouTube (verificado en vivo
        # 2026-11-21 con 1wzzrOvl_Jg: el fetch fallaba 1 de cada
        # 3 veces): dos fallos y luego éxito -> el candidato
        # se enriquece, no se pierde.
        cand = {"video_id": "abc123",
                "url": "https://www.youtube.com/watch?v=abc123",
                "title": "t", "views": 500000, "likes": None,
                "duration_s": 45, "channel": "", "streamer": "s",
                "language": "en"}
        vcfg = vs.load_viral_config(_cfg())
        now = datetime(2026, 11, 21, 12, 0, tzinfo=timezone.utc)
        calls = []

        def _flaky(cmd):
            calls.append(cmd)
            if len(calls) < 3:
                return SimpleNamespace(returncode=1, stdout="",
                                       stderr="bot-check")
            return SimpleNamespace(returncode=0,
                                   stdout=json.dumps(_meta()),
                                   stderr="")

        out = vs.enrich_candidate(cand, vcfg, runner=_flaky, now=now,
                                  sleep=lambda s: None)
        self.assertIsNotNone(out)
        self.assertEqual(out["likes"], 9000)
        self.assertEqual(len(calls), 3)


class TestViralTotal(unittest.TestCase):
    def test_escala_logaritmica(self):
        self.assertEqual(vs.viral_total_from_views(100000, 100000), 55)
        self.assertEqual(vs.viral_total_from_views(1000000, 100000), 70)
        self.assertEqual(vs.viral_total_from_views(10000000, 100000), 85)
        self.assertEqual(vs.viral_total_from_views(100000000, 100000),
                         100)
        self.assertEqual(vs.viral_total_from_views(0, 100000), 0)


class TestClaims(unittest.TestCase):
    def test_un_video_una_sola_cuenta(self):
        claims = {}
        now = datetime(2026, 11, 21, 12, 0, tzinfo=timezone.utc)
        self.assertTrue(vs.claim_video(claims, "v1", "sebastian", 7,
                                       now=now))
        # Otro tenant: nunca.
        self.assertFalse(vs.claim_video(claims, "v1", "kaez", 7,
                                        now=now))
        # Mismo tenant dentro del cooldown: no se reprocesa.
        self.assertFalse(vs.claim_video(claims, "v1", "sebastian", 7,
                                        now=now))
        # Pasado el cooldown: sí.
        later = datetime(2026, 11, 29, 12, 0, tzinfo=timezone.utc)
        self.assertTrue(vs.claim_video(claims, "v1", "sebastian", 7,
                                       now=later))
        # Otro vídeo: independiente.
        self.assertTrue(vs.claim_video(claims, "v2", "kaez", 7,
                                       now=now))

    def test_guardar_y_cargar(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "claims.json")
            claims = {}
            now = datetime(2026, 11, 21, 12, 0, tzinfo=timezone.utc)
            vs.claim_video(claims, "v1", "sebastian", 7, now=now)
            vs.save_claims(path, claims)
            loaded = vs.load_claims(path)
            self.assertEqual(loaded["v1"]["tenant"], "sebastian")
            self.assertEqual(loaded["v1"]["status"], "claimed")
            # Archivo corrupto = {}.
            with open(path, "w", encoding="utf-8") as f:
                f.write("{no json")
            self.assertEqual(vs.load_claims(path), {})
            self.assertEqual(vs.load_claims(
                os.path.join(tmp, "no-existe.json")), {})


class _FakeBridge:
    """Panel fake: registra create_clip y upload_video."""

    def __init__(self):
        self.created = []
        self.uploaded = []
        self._next = 1000

    def create_clip(self, payload):
        self._next += 1
        self.created.append(payload)
        return {"id": self._next}

    def upload_video(self, clip_id, video_path):
        self.uploaded.append((clip_id, video_path))
        return {"ok": True}


def _candidate(**over):
    cand = {"video_id": "abc123",
            "url": "https://www.youtube.com/watch?v=abc123",
            "title": "GTA 6: Speed steals a grandma's scooter",
            "views": 500000, "likes": 9000, "duration_s": 50,
            "channel": "@IShowSpeed", "streamer": "ishowspeed",
            "language": "en",
            "description": "Speed plays GTA 6.\nSecond line.",
            "tags": ["GTA 6", "Speed"]}
    cand.update(over)
    return cand


def _patch_pipeline(test, decision="aprobado", seo=90):
    """Mockea el pipeline pesado: descarga, Whisper, render,
    validador y SEO. Devuelve el bridge fake."""
    transcript = {"segments": [
        {"start": 0.0, "end": 2.0, "text": "give me that scooter"}]}
    test.addCleanup(mock.patch.stopall)
    mock.patch.object(vs, "download_youtube_segment",
                      return_value="/tmp/segment.mp4").start()
    mock.patch.object(vs, "transcribe_audio",
                      return_value=(transcript, {})).start()
    mock.patch.object(vs, "generate_srt",
                      return_value=(True, 0)).start()
    mock.patch.object(vs, "make_word_ass_from_transcript",
                      return_value="[Script Info]\n").start()
    # Decisión §2.1 (spec rubius sin webcam): la plantilla la
    # decide la fuente; este camino solo re-hace a rubius.
    mock.patch(
        "clipagent.template_decision.decide_template_for_segment",
        return_value=("rubius", (0, 0, 1080, 840), "firme", False)).start()
    mock.patch.object(vs, "make_banner_png",
                      return_value="/tmp/banner.png").start()
    mock.patch.object(vs, "rubius_render",
                      return_value=(50.0, "/tmp/final.mp4")).start()
    review = {"decision": decision, "motivo": "",
              "fallos": [], "needs_rerender": False,
              "needs_human_review": False}
    if decision == "rechazado":
        review.update({"motivo": "plantilla: titulo_banner",
                       "fallos": ["titulo_banner"]})
    if decision == "rerender":
        review.update({"decision": "pendiente_revision",
                       "needs_rerender": True,
                       "motivo": "plantilla: sin cara"})
    mock.patch.object(vs, "validate_final_clip",
                      return_value=review).start()
    mock.patch.object(
        vs, "score_all_platforms",
        return_value={p: {"score": seo} for p in PLATFORMS}).start()
    return _FakeBridge()


class TestProcessCandidate(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.cfg = load_config()
        self.vcfg = vs.load_viral_config(self.cfg)

    def test_usa_la_ruta_real_que_devuelve_la_descarga(self):
        # 2026-11-26: con formato webm la descarga escribe
        # segment.mp4.webm; seguir usando la ruta pedida rompía
        # ffmpeg (exit 254) en los 2 Shorts de Speed del dry-run.
        bridge = _patch_pipeline(self)
        mock.patch.object(
            vs, "download_youtube_segment",
            return_value="/tmp/segment.mp4.webm").start()
        tr = mock.patch.object(
            vs, "transcribe_audio",
            return_value=({"segments": []}, {})).start()
        vs.process_candidate(bridge, _candidate(), self.tmp.name,
                             self.cfg, self.vcfg, "sebastian")
        self.assertEqual(tr.call_args[0][0], "/tmp/segment.mp4.webm")

    def test_encola_con_el_payload_del_camino_vod(self):
        bridge = _patch_pipeline(self)
        panel_id, motivo, report = vs.process_candidate(
            bridge, _candidate(), self.tmp.name, self.cfg, self.vcfg,
            "sebastian")
        self.assertEqual(panel_id, 1001)
        self.assertEqual(motivo, "")
        # viral_total ancla en min_views (300k desde la enmienda
        # 2026-11-26 11:48): el candidato tiene 500k vistas ->
        # 55 + 15*log10(500/300) = 58.
        self.assertEqual(report["viral_total"], 58)
        self.assertEqual(len(bridge.created), 1)
        payload = bridge.created[0]
        self.assertEqual(payload["origin"], "viral")
        self.assertEqual(payload["game"], "GTA VI")
        self.assertEqual(payload["language"], "en")
        self.assertEqual(payload["influencer_id"], "ishowspeed")
        self.assertEqual(payload["source_id"], 0)
        self.assertEqual(payload["start_sec"], 0)
        self.assertEqual(payload["end_sec"], 50)
        self.assertIn("abc123", payload["reason"])
        self.assertEqual(payload["subtitles_complete"], 1)
        self.assertEqual(bridge.uploaded, [(1001, "/tmp/final.mp4")])
        # Spec §8: la plantilla del render viaja dentro de viral.
        self.assertEqual(payload["viral"], {"plantilla": "rubius"})

    def test_rechazado_no_se_encola(self):
        bridge = _patch_pipeline(self, decision="rechazado")
        panel_id, motivo, _report = vs.process_candidate(
            bridge, _candidate(), self.tmp.name, self.cfg, self.vcfg,
            "sebastian")
        self.assertIsNone(panel_id)
        self.assertIn("validación final", motivo)
        self.assertEqual(bridge.created, [])
        self.assertEqual(bridge.uploaded, [])

    def test_needs_rerender_no_se_encola_en_v1(self):
        bridge = _patch_pipeline(self, decision="rerender")
        panel_id, motivo, _report = vs.process_candidate(
            bridge, _candidate(), self.tmp.name, self.cfg, self.vcfg,
            "sebastian")
        self.assertIsNone(panel_id)
        self.assertIn("revisión humana", motivo)
        self.assertEqual(bridge.created, [])

    def test_puertas_no_se_encola_sin_force(self):
        bridge = _patch_pipeline(self, seo=70)
        panel_id, motivo, _report = vs.process_candidate(
            bridge, _candidate(), self.tmp.name, self.cfg, self.vcfg,
            "sebastian")
        self.assertIsNone(panel_id)
        self.assertIn("puertas", motivo)
        self.assertEqual(bridge.created, [])

    def test_force_salta_las_puertas(self):
        bridge = _patch_pipeline(self, seo=70)
        panel_id, _motivo, _report = vs.process_candidate(
            bridge, _candidate(), self.tmp.name, self.cfg, self.vcfg,
            "sebastian", force=True)
        self.assertEqual(panel_id, 1001)

    def test_sin_webcam_no_se_puede_rehacer(self):
        bridge = _patch_pipeline(self)
        with mock.patch(
                "clipagent.template_decision.decide_template_for_segment",
                return_value=("rubius_sin_webcam", None, "firme",
                              False)):
            panel_id, motivo, _report = vs.process_candidate(
                bridge, _candidate(), self.tmp.name, self.cfg,
                self.vcfg, "sebastian")
        self.assertIsNone(panel_id)
        self.assertIn("sin webcam", motivo)
        self.assertEqual(bridge.created, [])

    def test_ambiguo_no_se_puede_rehacer(self):
        bridge = _patch_pipeline(self)
        with mock.patch(
                "clipagent.template_decision.decide_template_for_segment",
                return_value=("rubius", None, "ambiguo", False)):
            panel_id, motivo, _report = vs.process_candidate(
                bridge, _candidate(), self.tmp.name, self.cfg,
                self.vcfg, "sebastian")
        self.assertIsNone(panel_id)
        self.assertIn("sin webcam", motivo)
        self.assertEqual(bridge.created, [])

    def test_dry_run_valida_sin_encolar(self):
        bridge = _patch_pipeline(self)
        panel_id, motivo, _report = vs.process_candidate(
            bridge, _candidate(), self.tmp.name, self.cfg, self.vcfg,
            "sebastian", dry_run=True)
        self.assertEqual(panel_id, "dry-run")
        self.assertEqual(bridge.created, [])
        self.assertEqual(bridge.uploaded, [])


class TestRunViralSource(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.cfg = _cfg()
        self.vcfg = vs.load_viral_config(self.cfg)

    def test_descubrimiento_encola_hasta_max_per_run(self):
        cands = [_candidate(video_id=f"v{i}",
                            url=f"https://www.youtube.com/watch?v=v{i}",
                            views=500000 + i)
                 for i in range(3)]
        with mock.patch.object(vs, "load_streamers",
                               return_value=[{
                                   "name": "ishowspeed",
                                   "platform": "youtube",
                                   "channel_url": "https://www.youtube.com/@IShowSpeed",
                                   "language": "en",
                                   "estado": "concedido"}]), \
                mock.patch.object(vs, "discover_candidates",
                                  return_value=cands), \
                mock.patch.object(vs, "enrich_candidate",
                                  side_effect=lambda c, v, runner=None,
                                  now=None: c), \
                mock.patch.object(vs, "process_candidate",
                                  return_value=(4242, "", {})) as proc:
            report = vs.run_viral_source(
                None, self.cfg, self.tmp.name, "sebastian")
        # max_per_run=2: el tercero se salta por tope.
        self.assertEqual(len(report["encolados"]), 2)
        self.assertEqual(proc.call_count, 2)
        self.assertEqual(len(report["saltados"]), 1)
        self.assertIn("tope max_per_run", report["saltados"][0]["motivo"])
        self.assertEqual(report["descubiertos"], 3)
        self.assertEqual(report["enriquecidos"], 3)
        # Los claims quedaron guardados.
        claims = vs.load_claims(os.path.join(
            self.tmp.name, "viral-source-claims.json"))
        self.assertEqual(claims["v0"]["tenant"], "sebastian")
        self.assertEqual(claims["v0"]["status"], "done")
        self.assertEqual(claims["v0"]["panel_id"], 4242)
        self.assertNotIn("v2", claims)

    def test_video_reclamado_por_otro_tenant_no_se_toca(self):
        claims = {}
        now = datetime(2026, 11, 21, 12, 0, tzinfo=timezone.utc)
        vs.claim_video(claims, "v0", "kaez", 7, now=now)
        vs.save_claims(os.path.join(
            self.tmp.name, "viral-source-claims.json"), claims)
        cands = [_candidate(video_id="v0")]
        with mock.patch.object(vs, "load_streamers",
                               return_value=[{
                                   "name": "ishowspeed",
                                   "platform": "youtube",
                                   "channel_url": "https://www.youtube.com/@IShowSpeed",
                                   "language": "en",
                                   "estado": "concedido"}]), \
                mock.patch.object(vs, "discover_candidates",
                                  return_value=cands), \
                mock.patch.object(vs, "enrich_candidate",
                                  side_effect=lambda c, v, runner=None,
                                  now=None: c), \
                mock.patch.object(vs, "process_candidate") as proc:
            report = vs.run_viral_source(
                None, self.cfg, self.tmp.name, "sebastian")
        self.assertEqual(proc.call_count, 0)
        self.assertEqual(len(report["encolados"]), 0)
        self.assertIn("kaez", report["saltados"][0]["motivo"])
        self.assertIn("UNA sola cuenta", report["saltados"][0]["motivo"])

    def test_modo_video_url_canal_listado_llega_a_procesar(self):
        with mock.patch.object(vs, "load_streamers",
                               return_value=[{
                                   "name": "ishowspeed",
                                   "platform": "youtube",
                                   "channel_url": "https://www.youtube.com/@IShowSpeed",
                                   "language": "en",
                                   "estado": "concedido"}]), \
                mock.patch.object(vs, "fetch_video_metadata",
                                  return_value=_meta()), \
                mock.patch.object(vs, "process_candidate",
                                  return_value=(4242, "", {})):
            report = vs.run_viral_source(
                None, self.cfg, self.tmp.name, "sebastian",
                video_url="https://www.youtube.com/watch?v=abc123")
        # El meta fake es de IShowSpeed (@IShowSpeed): está en la
        # lista, pasa likes/edad/duración y se procesa.
        self.assertEqual(report["modo"], "video")
        self.assertEqual(report["descubiertos"], 1)
        self.assertEqual(report["enriquecidos"], 1)
        self.assertEqual(len(report["encolados"]), 1)

    def test_modo_video_url_canal_sin_permiso(self):
        meta = _meta()
        meta["uploader_id"] = "@OtroCanal"
        meta["channel"] = "OtroCanal"
        meta["uploader"] = "OtroCanal"
        with mock.patch.object(vs, "load_streamers",
                               return_value=[{
                                   "name": "ishowspeed",
                                   "platform": "youtube",
                                   "channel_url": "https://www.youtube.com/@IShowSpeed",
                                   "language": "en",
                                   "estado": "concedido"}]), \
                mock.patch.object(vs, "fetch_video_metadata",
                                  return_value=meta):
            report = vs.run_viral_source(
                None, self.cfg, self.tmp.name, "sebastian",
                video_url="https://www.youtube.com/watch?v=abc123")
        self.assertEqual(len(report["saltados"]), 1)
        self.assertIn("sin permiso", report["saltados"][0]["motivo"])

    def test_dry_run_no_persiste_claims(self):
        cands = [_candidate(video_id="v0")]
        with mock.patch.object(vs, "load_streamers",
                               return_value=[{
                                   "name": "ishowspeed",
                                   "platform": "youtube",
                                   "channel_url": "https://www.youtube.com/@IShowSpeed",
                                   "language": "en",
                                   "estado": "concedido"}]), \
                mock.patch.object(vs, "discover_candidates",
                                  return_value=cands), \
                mock.patch.object(vs, "enrich_candidate",
                                  side_effect=lambda c, v, runner=None,
                                  now=None: c), \
                mock.patch.object(vs, "process_candidate",
                                  return_value=("dry-run", "", {})):
            report = vs.run_viral_source(
                None, self.cfg, self.tmp.name, "sebastian",
                dry_run=True)
        self.assertEqual(len(report["encolados"]), 1)
        self.assertEqual(report["encolados"][0]["panel_id"], "dry-run")
        # El dry-run NO deja claims en disco: la corrida real
        # tiene que poder reclamar el mismo vídeo después.
        self.assertFalse(os.path.exists(os.path.join(
            self.tmp.name, "viral-source-claims.json")))

    def test_desactivada_no_procesa(self):
        cfg = _cfg(enabled=False)
        with mock.patch.object(vs, "load_streamers") as ls:
            report = vs.run_viral_source(
                None, cfg, self.tmp.name, "sebastian")
        ls.assert_not_called()
        self.assertIn("enabled", report["motivo"])


class TestCLI(unittest.TestCase):
    def test_help(self):
        with self.assertRaises(SystemExit) as ctx:
            vs.main(["--help"])
        self.assertEqual(ctx.exception.code, 0)

    def test_dry_run_sin_credenciales_usa_la_lista_del_repo(self):
        from clipagent.youtube import YoutubeError

        def _sin_red(url, runner=None, **kwargs):
            raise YoutubeError("sin red en tests")

        with mock.patch.dict(os.environ, {}, clear=True), \
                mock.patch.object(vs, "youtube_check", _sin_red):
            rc = vs.main(["--tenant", "sebastian", "--dry-run",
                          "--out-dir", tempfile.mkdtemp()])
        # Sin red no descubre nada, pero no falla por credenciales
        # (dry-run usa streamers.yaml del repo) ni tumba la corrida.
        self.assertEqual(rc, 0)


if __name__ == "__main__":
    unittest.main(verbosity=2)

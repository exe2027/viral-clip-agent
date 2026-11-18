#!/usr/bin/env python3
"""Reglas del usuario 2026-11-18 (test_reglas_usuario_2026_11_18).

TODAS las reglas que el usuario dio en el chat tienen que estar
codificadas en el repo (código/config), no solo en chat/memoria.
Este archivo fija las decisiones del 2026-11-18 y las reglas
anteriores que siguen vigentes, con su punto de aplicación:

1. Duración 40-60 s (hoy): nunca < 40 ni > 60, nunca 30 s exacto,
   rango 25-35 prohibido. El clip p2010 de Rubius (38 s) queda fuera.
2. Hype por ráfaga v2 (hoy, ver test_hype_v2.py): velocidad por 10 s
   y 60 s contra baseline rodante (mediana 5 min); la ráfaga pesa
   más que el total.
3. Rubius prioritario; IlloJuan pausado 13:34 y REACTIVADO 15:30
   (hoy): 10 streamers activos (Rubius + IlloJuan + 8 nuevos), todos
   con categoría GTA VI. clip_02 sigue publish_blocked por
   subtítulos sin verificar (no publicar). Sin override de p2010
   (viralidad 44,5 < 50).
4. SIN franja horaria (hoy 14:25): da lo mismo la hora, solo se
   sube. Topes diarios y espaciamiento 2 h siguen vigentes.
5. Layout split vertical (2026-11-16): juego arriba, cámara abajo
   más chica, sin tapar minimapa ni HUD, dos webcams apiladas; si
   el juego no se ve, la cámara se achica, nunca al revés.
6. Subtítulos SIEMPRE y COMPLETOS (2026-11-17 22:10): quemados,
   todo lo que dice el influencer, nunca inventar texto; sin voz
   verificable = BLOCKED / needs_human_transcription, no publicar.
7. Descargar SOLO la parte del clip, nunca el video entero; chat
   solo en la ventana alrededor del pico (±120 s).
8. Prioridad LIVE sobre VOD, fórmula INTOCABLE
   (viralidad^0,5 × SEO^0,3 × chat_norm^0,2), filtros duros
   (viralidad < 50 descarta, SEO < 90 no publica salvo override),
   clip > 7 días -10 pts/día, no dos clips del mismo momento el
   mismo día en la misma plataforma.
9. Publicación automática con red de seguridad, sin aprobación por
   clip, con puertas (preview, subtítulos, permiso, SEO, topes,
   espaciamiento). Regla Nº1: no ser baneados (topes por
   plataforma, cuentas nuevas 1/día).
10. Todo $0, gratis. Panel en Cloudflare avc-em9.pages.dev, login
    por usuario, cada tenant ve solo lo suyo.
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from clipagent.config import (
    DEFAULT_CONFIG,
    RULES_FORBIDDEN_DURATION_RANGE,
    RULES_HARD_CAP_S,
    RULES_MIN_DURATION_S,
    clip_duration_ok,
    load_config,
)
from clipagent.policy import evaluate
from clipagent.priority import (
    DEFAULT_WEIGHTS,
    SEO_GATE,
    VIRAL_MIN_PUBLISH,
    priority,
    publish_status,
)
from clipagent.publish import (
    DAILY_LIMITS,
    MIN_SPACING_HOURS,
    NEW_ACCOUNT_DAILY,
    PUBLISH_WINDOW,
)
from clipagent.render import _split_params
from clipagent.streamers import get_streamer, is_allowed


def _clip(duration=40.0, viral=80, seo=95, platform="youtube_shorts"):
    return {"duration": duration,
            "category": "Grand Theft Auto VI",
            "viral_score": {"total": viral},
            "seo_scores": {platform: seo},
            "title_es": "t", "desc_es": "d", "hashtags_es": "",
            "visible": "gameplay", "lang": "es"}


class TestReglas20261118Duracion(unittest.TestCase):
    def test_entre_40_y_60(self):
        self.assertEqual(RULES_MIN_DURATION_S, 40)
        self.assertEqual(RULES_HARD_CAP_S, 60)
        self.assertEqual(RULES_FORBIDDEN_DURATION_RANGE, (25, 35))
        for bad in (21.0, 30.0, 35.0, 38.0, 39.9, 61.0):
            ok, _ = clip_duration_ok(bad)
            self.assertFalse(ok, f"{bad} s debe rechazarse")
        for good in (40.0, 50.0, 60.0):
            ok, _ = clip_duration_ok(good)
            self.assertTrue(ok, f"{good} s debe pasar")

    def test_p2010_38s_rechazado_por_duracion_y_por_viralidad(self):
        ok, why = clip_duration_ok(38.005)
        self.assertFalse(ok)
        self.assertIn("40", why)
        # Y además su viralidad 44,5 < 50 lo descarta sin override.
        status, _ = publish_status(44.5, 98, seo_override=True)
        self.assertEqual(status, "descartado")
        clip = _clip(duration=38.005, viral=44, seo=98)
        dec = evaluate(clip, "youtube_shorts")
        self.assertEqual(dec["decision"], "retenido")
        self.assertIn("40", dec["reason"])


class TestReglas20261118Streamers(unittest.TestCase):
    def test_diez_streamers_activos_todos_gta_vi(self):
        # Orden del usuario 2026-11-18 15:30: 8 nuevos (AuronPlay,
        # Ibai, TheGrefg, Xokas, DjMaRiiO, Knekro, RDJavi, Westcol)
        # + Rubius + IlloJuan REACTIVADO 15:30 (pausado 13:34).
        # Todos con categoría GTA VI (ver test_streamers para el
        # detalle campo por campo).
        rubius = get_streamer("rubius")
        self.assertIsNotNone(rubius)
        self.assertEqual(rubius["estado"], "concedido")
        self.assertTrue(rubius.get("enabled", True))
        self.assertTrue(is_allowed("rubius"))
        illojuan = get_streamer("illojuan")
        self.assertIsNotNone(illojuan)
        self.assertTrue(illojuan.get("enabled", True),
                        "IlloJuan REACTIVADO 2026-11-18 15:30: enabled=true")
        # El watcher vigila a los 10 activos (ver test_streamers).
        from watcher import load_influencers
        watched = {i["usuario"] for i in load_influencers()}
        self.assertEqual(watched, {
            "illojuan", "rubius", "auronplay", "ibai", "thegrefg",
            "xokas", "djmariio", "knekro", "rdjavi", "westcol",
        })


class TestReglas20261118SinFranja(unittest.TestCase):
    def test_sin_franja_topes_intactos(self):
        # Franja eliminada 2026-11-18 14:25; PUBLISH_WINDOW en None.
        self.assertIsNone(PUBLISH_WINDOW)
        # Topes diarios y espaciamiento NO cambian (anti-ban).
        self.assertEqual(DAILY_LIMITS["youtube_shorts"], (3, 5))
        self.assertEqual(DAILY_LIMITS["tiktok"], (2, 3))
        self.assertEqual(DAILY_LIMITS["instagram_reels"], (2, 3))
        self.assertEqual(DAILY_LIMITS["facebook_reels"], (2, 3))
        self.assertEqual(MIN_SPACING_HOURS, 2)
        self.assertEqual(NEW_ACCOUNT_DAILY, 1)


class TestReglasPrioridadIntocable(unittest.TestCase):
    def test_formula_y_filtros(self):
        self.assertEqual(DEFAULT_WEIGHTS,
                         {"viral": 0.5, "seo": 0.3, "chat": 0.2})
        self.assertEqual(VIRAL_MIN_PUBLISH, 50)
        self.assertEqual(SEO_GATE, 90)
        # viralidad < 50 descarta aunque el SEO sea 100 y con override.
        self.assertEqual(publish_status(49, 100)[0], "descartado")
        self.assertEqual(publish_status(44.5, 98, seo_override=True)[0],
                         "descartado")
        # SEO < 90 sin override no publica; con override sí (70-89).
        self.assertEqual(publish_status(80, 89)[0], "bloqueado_seo")
        self.assertEqual(publish_status(80, 89, seo_override=True)[0],
                         "publicable_override")
        self.assertEqual(publish_status(80, 95)[0], "publicable")

    def test_clip_viejo_pierde_10_por_dia(self):
        p0 = priority(80, 95, 5.0, age_days=0)["priority"]
        p8 = priority(80, 95, 5.0, age_days=8)["priority"]
        self.assertAlmostEqual(p0 - p8, 10.0, places=1)

    def test_mismo_momento_mismo_dia_no_duplica(self):
        from clipagent.policy import evaluate as ev
        from clipagent.priority import moment_key

        class Q:
            def published_today(self, platform):
                return [{"moment_key": list(moment_key("2932415021", 2010))}]

        clip = _clip()
        clip["start"] = 2010
        dec = ev(clip, "youtube_shorts", queue=Q(), vod_id="2932415021")
        self.assertEqual(dec["decision"], "retenido")
        self.assertIn("mismo momento", dec["reason"])


class TestReglasLayoutSubtitulosDescarga(unittest.TestCase):
    def test_split_vertical_y_camara_se_achica(self):
        cfg = load_config()["clips"]
        self.assertEqual(cfg["split_game_h"], 1320)
        self.assertEqual(cfg["split_cam_h"], 600)
        # Caja normal: juego 1320, cámara 600.
        p = _split_params([10, 10, 40, 20], DEFAULT_CONFIG["clips"])
        # Variante por defecto para GTA 6: RDJavi; encuadre cara.
        self.assertEqual(p["split_variant"], "rdjavi")
        self.assertEqual(p["framing"], "face")
        self.assertEqual(p["game_h"], 1320)
        self.assertEqual(p["cam_h"], 600)
        # Caja muy ancha (> 45% del frame, el juego no se vería):
        # la CÁMARA se achica (480) y el juego crece (1440), nunca
        # al revés.
        p2 = _split_params([10, 10, 90, 20], DEFAULT_CONFIG["clips"])
        self.assertEqual(p2["cam_h"], 480)
        self.assertEqual(p2["game_h"], 1440)

    def test_subtitulos_siempre_completos_y_bloqueo_sin_voz(self):
        subs = load_config()["subtitles"]
        self.assertTrue(subs["required"])
        self.assertTrue(subs["complete"])
        self.assertTrue(subs["block_without_subtitles"])
        self.assertTrue(subs["needs_human_transcription_on_no_speech"])
        from clipagent.transcribe import (
            effective_subtitle_margin_v,
            subtitles_overlap_webcam,
        )
        # Estilo RDJavi total (2026-11-18): referencia 230, mínimo 200.
        self.assertFalse(subtitles_overlap_webcam(230))
        self.assertEqual(effective_subtitle_margin_v(140), 200)
        self.assertEqual(effective_subtitle_margin_v(230), 230)

    def test_solo_segmento_y_chat_en_ventana(self):
        cfg = load_config()["clips"]
        self.assertEqual(cfg["chat_window_s"], 120)
        import auto_clips
        self.assertTrue(callable(auto_clips.download_chat_window))
        self.assertTrue(callable(auto_clips.download_twitch_segment))


class TestReglasCeroCostoYPanel(unittest.TestCase):
    def test_sin_secretos_en_config_versionada(self):
        # config.yaml es versionado y nunca lleva secretos: las
        # credenciales van por variables de entorno / GitHub Secrets.
        with open(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                               "config.yaml"), encoding="utf-8") as f:
            text = f.read()
        for bad in ("api_key:", "client_secret:", "access_token:",
                    "password:"):
            self.assertNotIn(bad, text)

    def test_panel_cloudflare_en_config(self):
        cfg = load_config()
        self.assertIn("avc-em9.pages.dev",
                      (cfg.get("panel") or {}).get("base_url", "") or
                      str(cfg.get("panel") or ""))


if __name__ == "__main__":
    unittest.main()

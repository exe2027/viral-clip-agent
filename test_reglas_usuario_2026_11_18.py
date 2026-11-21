#!/usr/bin/env python3
"""Reglas del usuario 2026-11-18 (test_reglas_usuario_2026_11_18).

TODAS las reglas que el usuario dio en el chat tienen que estar
codificadas en el repo (código/config), no solo en chat/memoria.
Este archivo fija las decisiones del 2026-11-18 y las reglas
anteriores que siguen vigentes, con su punto de aplicación:

1. Duración: la regla 40-60 s con mínimo 40 y rango 25-35 prohibido
   fue ELIMINADA por el usuario el 2026-11-21 ("para siempre, en todo
   el canal"). Sin mínimo ni rango prohibido; solo queda el tope duro
   de 60 s. El clip p2010 de Rubius (38 s) ya pasa duración (sigue
   descartado por viralidad 44,5 < 50).
2. Hype por ráfaga v2 (hoy, ver test_hype_v2.py): velocidad por 10 s
   y 60 s contra baseline rodante (mediana 5 min); la ráfaga pesa
   más que el total.
3. Rubius prioritario; IlloJuan pausado 13:34 y REACTIVADO 15:30
   (hoy): 10 streamers activos (Rubius + IlloJuan + 8 nuevos), todos
   con categoría GTA VI. clip_02 sigue publish_blocked por
   subtítulos sin verificar (no publicar). Sin override de p2010
   (viralidad 44,5 < 50).
4. Franja horaria: la decisión 2026-11-18 14:25 "sin franja" queda
   REVOCADA por las reglas de publicación 2026-11-19: vuelven las
   franjas Chile 12-14 / 18-20 / 21-23 (ver test_reglas_franja
   abajo y test_publication_rules_2026_11_19.py). Topes diarios y
   espaciamiento 3 h (4 h YT) siguen vigentes.
5. Layout split vertical (2026-11-16): juego arriba, cámara abajo
   más chica, sin tapar minimapa ni HUD, dos webcams apiladas; si
   el juego no se ve, la cámara se achica, nunca al revés.
6. Subtítulos SIEMPRE y COMPLETOS cuando hay voz (2026-11-17
   22:10, matizada 2026-11-19 18:41): quemados, todo lo que dice
   el influencer, nunca inventar texto; sin voz verificable +
   pasa puertas (45/80) = se publica igual sin subtítulos, sin
   voz + bajo puertas = fuera.
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
11. Puerta de render PRE-CORTE (hoy): el clip SOLO se corta/
    renderiza si ya pasa las puertas con el paquete provisional
    (viralidad estimada >= 45 pre-corte — calibrado, no 50 — y SEO
    >= 90 en al menos una plataforma); si no pasa, se descarta SIN
    descargar el segmento ni renderizar. El 50 REAL sigue en la
    puerta de publicación.
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
    # Regla 40-60 / 25-35 ELIMINADA por el usuario el 2026-11-21
    # ("para siempre, en todo el canal"): sin mínimo ni rango
    # prohibido; solo queda el tope duro de 60 s.
    def test_sin_minimo_ni_rango_prohibido(self):
        self.assertEqual(RULES_MIN_DURATION_S, 1)
        self.assertEqual(RULES_HARD_CAP_S, 60)
        self.assertEqual(RULES_FORBIDDEN_DURATION_RANGE, ())
        for bad in (61.0,):
            ok, _ = clip_duration_ok(bad)
            self.assertFalse(ok, f"{bad} s debe rechazarse")
        for good in (15.0, 21.0, 25.6, 30.0, 38.0, 40.0, 50.0, 60.0):
            ok, _ = clip_duration_ok(good)
            self.assertTrue(ok, f"{good} s debe pasar")

    def test_p2010_38s_pasa_duracion_pero_sigue_descartado_por_viralidad(self):
        ok, _ = clip_duration_ok(38.005)
        self.assertTrue(ok)
        # Su viralidad 44,5 < 50 lo descarta sin override.
        status, _ = publish_status(44.5, 98, seo_override=True)
        self.assertEqual(status, "descartado")
        clip = _clip(duration=38.005, viral=44, seo=98)
        dec = evaluate(clip, "youtube_shorts")
        self.assertEqual(dec["decision"], "descartado")
        self.assertNotIn("40", dec["reason"])


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
        # El watcher vigila a los activos (ver test_streamers): los
        # 12 ES (10 de la orden 15:30 + peereira7 y noni, 2026-11-19)
        # + 8 EN añadidos después
        # (418586a, 2026-11-18: KaiCenat, IShowSpeed, N3ON, xQc,
        # TheBurntPeanut, Burn, Kaysan, WillNeff), todos GTA VI.
        from watcher import load_influencers
        watched = {i["usuario"] for i in load_influencers()}
        self.assertEqual(watched, {
            "illojuan", "rubius", "auronplay", "ibai", "thegrefg",
            "xokas", "djmariio", "knekro", "rdjavi", "westcol",
            "peereira7", "noni",
            "kaicenat", "ishowspeed", "n3on", "xqc",
            "theburntpeanut", "burn", "kaysan", "willneff",
        })


class TestReglas20261118SinFranja(unittest.TestCase):
    def test_franja_vuelve_topes_nuevos_2026_11_19(self):
        # REVOCADO 2026-11-19: la decisión 2026-11-18 "sin franja"
        # queda sin efecto; PUBLISH_WINDOW vuelve a ser el dict de
        # franjas Chile 12-14 / 18-20 / 21-23.
        self.assertEqual(PUBLISH_WINDOW, {
            "morning": ("12:00", "14:00"),
            "afternoon": ("18:00", "20:00"),
            "night": ("21:00", "23:00")})
        # Topes nuevos 2026-11-19 (anti-ban).
        self.assertEqual(DAILY_LIMITS["youtube_shorts"], (1, 2))
        self.assertEqual(DAILY_LIMITS["tiktok"], (2, 3))
        self.assertEqual(DAILY_LIMITS["instagram_reels"], (1, 2))
        self.assertEqual(DAILY_LIMITS["facebook_reels"], (1, 2))
        self.assertEqual(MIN_SPACING_HOURS, 3)
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
        # Variante por defecto para GTA 6: Rubius (decisión del
        # usuario 2026-11-19 20:48, todos los clips a rubius);
        # encuadre cara.
        self.assertEqual(p["split_variant"], "rubius")
        self.assertEqual(p["framing"], "face")
        self.assertEqual(p["game_h"], 1320)
        self.assertEqual(p["cam_h"], 600)
        # Caja muy ancha (> 45% del frame, el juego no se vería):
        # la CÁMARA se achica (480) y el juego crece (1440), nunca
        # al revés.
        p2 = _split_params([10, 10, 90, 20], DEFAULT_CONFIG["clips"])
        self.assertEqual(p2["cam_h"], 480)
        self.assertEqual(p2["game_h"], 1440)

    def test_subtitulos_siempre_completos_y_sin_voz_publica_si_pasa(self):
        # Regla 2026-11-17 22:10, matizada 2026-11-19 18:41:
        # subtítulos siempre y completos cuando hay voz; sin voz +
        # pasa puertas (45/80) el clip se publica igual sin
        # subtítulos, nunca se inventa texto.
        subs = load_config()["subtitles"]
        self.assertTrue(subs["required"])
        self.assertTrue(subs["complete"])
        self.assertTrue(subs["publish_no_speech_if_passes_gates"])
        from clipagent.transcribe import (
            effective_subtitle_margin_v,
            subtitles_overlap_webcam,
        )
        # Estilo RDJavi v2 (2026-11-18): subtítulos en la barra
        # negra 1740-1920 (referencia MarginV 12, mínimo 0); la
        # referencia vieja 230 queda en el gameplay y solapa.
        self.assertTrue(subtitles_overlap_webcam(230))
        self.assertFalse(subtitles_overlap_webcam(12, fontsize=64))
        self.assertEqual(effective_subtitle_margin_v(140), 140)
        self.assertEqual(effective_subtitle_margin_v(230), 140)
        self.assertEqual(effective_subtitle_margin_v(12, fontsize=64),
                         12)

    def test_solo_segmento_y_chat_en_ventana(self):
        cfg = load_config()["clips"]
        self.assertEqual(cfg["chat_window_s"], 120)
        import auto_clips
        self.assertTrue(callable(auto_clips.download_chat_window))
        self.assertTrue(callable(auto_clips.download_twitch_segment))

    def test_puerta_de_render_pre_corte(self):
        # Regla 11 (hoy): el clip solo se corta si ya pasa las
        # puertas con el paquete provisional. El umbral pre-corte es
        # 45 (calibrado: p3220 estima 46,3 vs real 53,8; p2010 44,5);
        # la puerta de publicación bajó a 45/80 el 2026-11-19 por
        # orden del usuario (antes 50/90), así que el 45 ya es el
        # listón real en ambos lados.
        from clipagent.policy import render_gate
        from clipagent.workers import PRE_CORTE_VIRAL_MIN
        self.assertEqual(PRE_CORTE_VIRAL_MIN, 45)
        seo_bueno = {"youtube_shorts": 92, "tiktok": 94,
                     "instagram_reels": 94, "facebook_reels": 94}
        # p3220 estimado pasa la puerta pre-corte; p2010 no.
        self.assertTrue(render_gate(
            46.3, seo_bueno, viral_min=PRE_CORTE_VIRAL_MIN)["pasa"])
        self.assertFalse(render_gate(
            44.5, seo_bueno, viral_min=PRE_CORTE_VIRAL_MIN)["pasa"])
        # Con el listón bajado a 45, la estimación de p3220 (46,3)
        # pasa también la puerta por defecto.
        self.assertTrue(render_gate(46.3, seo_bueno)["pasa"])
        self.assertTrue(render_gate(53.8, seo_bueno)["pasa"])


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

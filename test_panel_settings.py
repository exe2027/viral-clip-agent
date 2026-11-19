"""Ajustes del panel (D1) leídos por el cerebro (2026-11-18).

El usuario cambia la configuración en la pestaña Ajustes del panel
web; el cerebro la lee por el endpoint puente del Worker. Sin
entorno configurado, manda config.yaml.
"""
import unittest

from clipagent.config import load_config
from clipagent.panel_settings import (
    apply_panel_settings,
    fetch_panel_settings,
    sanitize_panel_settings,
)


class TestSanitizePanelSettings(unittest.TestCase):
    def test_solo_campos_conocidos_y_en_rango(self):
        clean = sanitize_panel_settings({
            "min_duration_s": 40, "max_duration_s": 60,
            "min_virality": 50, "min_seo": 90,
            "layout": "rdjavi", "subtitle_style": "ref",
            "campo_inventado": 123,
        })
        self.assertEqual(clean["min_duration_s"], 40)
        self.assertEqual(clean["layout"], "rdjavi")
        self.assertNotIn("campo_inventado", clean)

    def test_fuera_de_rango_se_ignora(self):
        clean = sanitize_panel_settings({
            "min_virality": 150, "min_seo": -5, "min_duration_s": 0,
        })
        self.assertNotIn("min_virality", clean)
        self.assertNotIn("min_seo", clean)
        self.assertNotIn("min_duration_s", clean)

    def test_min_mayor_que_max_se_descarta(self):
        clean = sanitize_panel_settings({
            "min_duration_s": 70, "max_duration_s": 60,
        })
        self.assertNotIn("min_duration_s", clean)
        self.assertNotIn("max_duration_s", clean)

    def test_djmariio_aparcado_resuelve_rdjavi(self):
        cfg = apply_panel_settings(load_config(), {"layout": "djmariio"})
        self.assertEqual(cfg["layout"]["split_variant"], "rdjavi")


class TestApplyPanelSettings(unittest.TestCase):
    def test_ajustes_mandan_sobre_config(self):
        cfg = apply_panel_settings(load_config(), {
            "min_duration_s": 45, "max_duration_s": 55,
            "min_virality": 55, "min_seo": 92,
            "subtitle_style": "karaoke", "layout": "plain",
            "category": "GTA VI", "thumbnail_style": "gta6",
            "hype_burst": False,
            "youtube_plan_per_day": 2, "other_plan_per_day": 1,
            "tiktok_zernio_per_day": 10,
        })
        self.assertEqual(cfg["rules"]["min_duration_s"], 45)
        self.assertEqual(cfg["rules"]["max_duration_s"], 55)
        self.assertEqual(cfg["scoring"]["viral_min"], 55)
        self.assertEqual(cfg["scoring"]["seo_gate"], 92)
        self.assertEqual(cfg["subtitles"]["default_style"], "karaoke")
        self.assertEqual(cfg["layout"]["split_variant"], "plain")
        self.assertEqual(cfg["rules"]["category"], "GTA VI")
        self.assertEqual(cfg["rules"]["thumbnail_style"], "gta6")
        self.assertFalse(cfg["rules"]["hype_burst_enabled"])
        limits = cfg["_panel_limits_override"]
        self.assertEqual(limits["youtube_shorts"]["plan_per_day"], 2)
        self.assertEqual(limits["tiktok"]["plan_per_day"], 1)
        self.assertEqual(limits["tiktok"]["zernio_per_day"], 10)

    def test_sin_ajustes_no_cambia_nada(self):
        cfg = load_config()
        out = apply_panel_settings(cfg, {})
        self.assertEqual(out, cfg)
        out2 = apply_panel_settings(cfg, None)
        self.assertEqual(out2, cfg)


class TestWatermarkPanelSettings(unittest.TestCase):
    """Marca de agua (2026-11-18): logo de colores, arriba a la
    derecha, 320 px, 20% (elección del usuario)."""

    def test_sanitize_guarda_los_campos(self):
        clean = sanitize_panel_settings({
            "watermark_enabled": True,
            "watermark_url": "https://x/logo.png?sig=1",
            "watermark_opacity": 20, "watermark_size_px": 320,
            "watermark_position": "top-right",
        })
        self.assertTrue(clean["watermark_enabled"])
        self.assertEqual(clean["watermark_url"],
                         "https://x/logo.png?sig=1")
        self.assertEqual(clean["watermark_opacity"], 20)
        self.assertEqual(clean["watermark_size_px"], 320)
        self.assertEqual(clean["watermark_position"], "top-right")

    def test_sanitize_descarta_posicion_y_rangos_invalidos(self):
        clean = sanitize_panel_settings({
            "watermark_position": "arriba",
            "watermark_opacity": 150, "watermark_size_px": 10,
        })
        self.assertNotIn("watermark_position", clean)
        self.assertNotIn("watermark_opacity", clean)
        self.assertNotIn("watermark_size_px", clean)

    def test_apply_mapea_a_cfg_watermark(self):
        cfg = apply_panel_settings(load_config(), {
            "watermark_enabled": True,
            "watermark_url": "https://x/logo.png?sig=1",
            "watermark_opacity": 20, "watermark_size_px": 320,
            "watermark_position": "top-right",
        })
        self.assertEqual(cfg["watermark"], {
            "enabled": True, "url": "https://x/logo.png?sig=1",
            "opacity": 20, "size_px": 320, "position": "top-right"})

    def test_apply_apagada_desactiva(self):
        cfg = apply_panel_settings(load_config(), {
            "watermark_enabled": False})
        self.assertEqual(cfg["watermark"], {"enabled": False})

    def test_sin_campos_de_marca_no_toca_cfg(self):
        cfg = apply_panel_settings(load_config(), {"layout": "plain"})
        self.assertNotIn("watermark", cfg)


class TestFetchPanelSettings(unittest.TestCase):
    def test_sin_entorno_devuelve_none_sin_romper(self):
        self.assertIsNone(fetch_panel_settings())

    def test_url_invalida_devuelve_none_sin_romper(self):
        self.assertIsNone(fetch_panel_settings(
            worker_url="http://127.0.0.1:1", bridge_token="x",
            tenant="sebastian", timeout=1))


if __name__ == "__main__":
    unittest.main()

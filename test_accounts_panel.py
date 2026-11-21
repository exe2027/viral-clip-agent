#!/usr/bin/env python3
"""Tests de madurez desde el panel + sin identificadores por
defecto (2026-11-19, cerebro genérico): la etapa sale de
GET /api/accounts del tenant; sin fuente no se inventa ningún
handle.
"""
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from clipagent import accounts

PANEL_ROWS = [
    {"platform": "youtube", "language": "es",
     "account": {"label": "YouTube ES"},
     "maturity": {"stage": "semana1"}},
    {"platform": "youtube", "language": "en",
     "account": {"label": "YouTube EN"},
     "maturity": {"stage": "establecida"}},
    {"platform": "tiktok", "language": "es",
     "account": {"label": "TikTok ES"},
     "maturity": {"stage": "semana2"}},
    {"platform": "meta", "language": "es",
     "account": {"label": "Meta ES"},
     "maturity": {"stage": "establecida"}},
]


class FakeBridge:
    def __init__(self, rows=None):
        self.rows = rows or []

    def list_api_accounts(self):
        return self.rows


class TestPanelStage(unittest.TestCase):
    def test_mapeo_de_etapas_del_panel(self):
        self.assertEqual(accounts.warmup_stage_from_panel("semana1"), "week1")
        self.assertEqual(accounts.warmup_stage_from_panel("semana2"), "week2")
        self.assertEqual(accounts.warmup_stage_from_panel("semana3"), "normal")
        self.assertEqual(accounts.warmup_stage_from_panel("establecida"), "normal")
        self.assertIsNone(accounts.warmup_stage_from_panel("rara"))
        self.assertIsNone(accounts.warmup_stage_from_panel(None))

    def test_warmup_stage_con_panel_stage_manda(self):
        with tempfile.TemporaryDirectory() as tmp:
            # Sin archivo local la etapa sería "normal"; el panel manda.
            self.assertEqual(
                accounts.warmup_stage("youtube_shorts", base_dir=tmp,
                                      panel_stage="semana1"), "week1")
            self.assertEqual(
                accounts.warmup_stage("youtube_shorts", base_dir=tmp,
                                      panel_stage="establecida"), "normal")
            self.assertEqual(
                accounts.warmup_stage("youtube_shorts", base_dir=tmp),
                "normal")

    def test_panel_maturity_prefiere_es_y_meta_cubre_ig_y_fb(self):
        accounts._PANEL_ACCOUNTS_CACHE = None
        out = accounts.panel_maturity(bridge=FakeBridge(PANEL_ROWS))
        self.assertEqual(out["youtube_shorts"], "week1")
        self.assertEqual(out["tiktok"], "week2")
        self.assertEqual(out["instagram_reels"], "normal")
        self.assertEqual(out["facebook_reels"], "normal")

    def test_panel_maturity_sin_bridge_devuelve_vacio(self):
        accounts._PANEL_ACCOUNTS_CACHE = None
        with unittest.mock.patch(
                "clipagent.panel_bridge.PanelBridge.from_env",
                return_value=None):
            self.assertEqual(accounts.panel_maturity(), {})

    def test_panel_labels_devuelve_la_etiqueta_del_tenant(self):
        accounts._PANEL_ACCOUNTS_CACHE = None
        out = accounts.panel_labels(bridge=FakeBridge(PANEL_ROWS))
        self.assertEqual(out["youtube_shorts"], "YouTube ES")
        self.assertEqual(out["tiktok"], "TikTok ES")
        self.assertEqual(out["instagram_reels"], "Meta ES")


class TestNoDefaultIdentifiers(unittest.TestCase):
    def test_sin_accounts_yaml_no_hay_identificadores(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(accounts.load_identifiers(tmp), {})

    def test_compute_state_sin_identificadores_no_rompe(self):
        with tempfile.TemporaryDirectory() as tmp:
            state = accounts.compute_state(tmp)
        self.assertEqual(state["youtube_shorts"]["label"], "YouTube")
        self.assertEqual(state["tiktok"]["label"], "TikTok")
        self.assertEqual(state["instagram_reels"]["label"], "Instagram")
        self.assertEqual(state["facebook_reels"]["label"], "Facebook")

    def test_accounts_yaml_local_sigue_leyendose(self):
        with tempfile.TemporaryDirectory() as tmp:
            with open(os.path.join(tmp, "accounts.yaml"), "w",
                      encoding="utf-8") as f:
                f.write("youtube_shorts:\n"
                        "  handle: \"@mi-canal\"\n"
                        "  label: \"YouTube — mi canal\"\n"
                        "  created_at: \"2026-11-01\"\n")
            ident = accounts.load_identifiers(tmp)
            self.assertEqual(ident["youtube_shorts"]["handle"], "@mi-canal")
            self.assertEqual(
                accounts.account_age_days("youtube_shorts", tmp) is not None,
                True)


if __name__ == "__main__":
    unittest.main()

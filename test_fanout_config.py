#!/usr/bin/env python3
"""Pruebas de la URL pública configurable y los IDs de cuenta
(fan-out). Nada toca la red ni escribe secretos."""
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from clipagent import platform_accounts as pa
from clipagent import public_url as pu


class TestPublicUrl(unittest.TestCase):
    def test_base_de_config_mas_ruta_relativa(self):
        cfg = {"public_url": {"base_url": "https://clips.ejemplo.com/"}}
        url, reason = pu.get_public_url(
            "/repo/runs/v1/clips/c1.mp4", cfg=cfg, base_dir="/repo")
        self.assertEqual(url, "https://clips.ejemplo.com/runs/v1/clips/c1.mp4")
        self.assertIsNone(reason)

    def test_env_manda_sobre_config_vacia(self):
        cfg = {"public_url": {"base_url": ""}}
        with mock.patch.dict(os.environ, {"CLIP_PUBLIC_BASE_URL":
                                          "https://h.ejemplo.com"}):
            url, _ = pu.get_public_url("/repo/runs/v1/clips/c1.mp4",
                                       cfg=cfg, base_dir="/repo")
        self.assertEqual(url, "https://h.ejemplo.com/runs/v1/clips/c1.mp4")

    def test_sin_base_devuelve_motivo_explicito(self):
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("CLIP_PUBLIC_BASE_URL", None)
            url, reason = pu.get_public_url(
                "runs/v1/clips/c1.mp4", cfg={}, base_dir="/repo")
        self.assertIsNone(url)
        self.assertIn("sin URL pública configurada", reason)
        self.assertIn("public_url.base_url", reason)

    def test_base_sin_https_se_rechaza(self):
        cfg = {"public_url": {"base_url": "http://clips.ejemplo.com"}}
        url, reason = pu.get_public_url("runs/v1/clips/c1.mp4", cfg=cfg,
                                        base_dir="/repo")
        self.assertIsNone(url)
        self.assertIn("https://", reason)

    def test_clip_fuera_del_repo_se_rechaza(self):
        cfg = {"public_url": {"base_url": "https://clips.ejemplo.com"}}
        url, reason = pu.get_public_url("/tmp/fuera.mp4", cfg=cfg,
                                        base_dir="/repo")
        self.assertIsNone(url)
        self.assertIn("fuera del repo", reason)


class TestPlatformAccounts(unittest.TestCase):
    def test_ids_desde_config(self):
        cfg = {"platform_accounts": {
            "instagram_reels": {"ig_user_id": "1784"},
            "facebook_reels": {"page_id": "555"}}}
        self.assertEqual(pa.get_ig_user_id(cfg), "1784")
        self.assertEqual(pa.get_facebook_page_id(cfg), "555")

    def test_env_manda_cuando_config_vacia(self):
        cfg = {"platform_accounts": {
            "instagram_reels": {"ig_user_id": ""},
            "facebook_reels": {"page_id": ""}}}
        with mock.patch.dict(os.environ, {"IG_USER_ID": "999",
                                          "FACEBOOK_PAGE_ID": "888"}):
            self.assertEqual(pa.get_ig_user_id(cfg), "999")
            self.assertEqual(pa.get_facebook_page_id(cfg), "888")

    def test_vacios_sin_configuracion(self):
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("IG_USER_ID", None)
            os.environ.pop("FACEBOOK_PAGE_ID", None)
            self.assertEqual(pa.get_ig_user_id({}), "")
            self.assertEqual(pa.get_facebook_page_id(None), "")


if __name__ == "__main__":
    unittest.main()

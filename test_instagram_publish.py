#!/usr/bin/env python3
"""Pruebas de la publicación de Reels en Instagram (fan-out).

Flujo verificado 2026-11-17: crear contenedor -> esperar FINISHED
-> publicar. Nada toca la red de verdad.
"""
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from clipagent import credentials as creds
from clipagent import platforms as pl

URL = "https://ejemplo.com/clips/c1.mp4"


class _FakeTransport:
    """Responde según el trozo de URL; la cola de estados alimenta
    las consultas de status_code."""

    def __init__(self, statuses=None):
        self.statuses = list(statuses or [])
        self.calls = []

    def __call__(self, method, url, headers, body):
        self.calls.append((method, url, headers, body))
        if "/media?" in url and method == "POST":
            return 200, {"id": "cont1"}
        if "media_publish" in url:
            return 200, {"id": "media9"}
        if "fields=status_code" in url:
            code = self.statuses.pop(0) if self.statuses else "FINISHED"
            return 200, {"status_code": code}
        return 404, {"error": {"code": "not_found", "message": "?"}}

def _adapter(tmp, transport=None):
    creds.save_token("instagram_reels", {"access_token": "t",
                                         "expires_at": 9999999999}, tmp)
    return pl.InstagramAdapter(tmp, transport)


class TestInstagramPublish(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.base = self.tmp.name

    def tearDown(self):
        self.tmp.cleanup()

    def test_sin_token_a_borrador(self):
        r = pl.InstagramAdapter(self.base, None).publish_reel(
            "123", URL, "caption", sleep_fn=lambda s: None)
        self.assertEqual(r["mode"], "draft")
        self.assertIn("sin token", r["reason"])

    def test_sin_ig_user_id_a_borrador(self):
        ad = _adapter(self.base, _FakeTransport())
        r = ad.publish_reel("", URL, "caption",
                            sleep_fn=lambda s: None)
        self.assertEqual(r["mode"], "draft")
        self.assertIn("ig_user_id", r["reason"])

    def test_sin_url_publica_a_borrador(self):
        ad = _adapter(self.base, _FakeTransport())
        r = ad.publish_reel("123", "/tmp/local.mp4", "caption",
                            sleep_fn=lambda s: None)
        self.assertEqual(r["mode"], "draft")
        self.assertIn("URL pública", r["reason"])

    def test_flujo_completo_publica(self):
        fn = _FakeTransport(["IN_PROGRESS", "FINISHED"])
        ad = _adapter(self.base, fn)
        r = ad.publish_reel("123", URL, "caption",
                            sleep_fn=lambda s: None)
        self.assertTrue(r["ok"], r)
        self.assertEqual(r["media_id"], "media9")
        self.assertTrue(any("/media?" in c[1] for c in fn.calls))
        self.assertTrue(any("media_publish" in c[1]
                            for c in fn.calls))

    def test_contenedor_en_error_es_final(self):
        fn = _FakeTransport(["ERROR"])
        ad = _adapter(self.base, fn)
        r = ad.publish_reel("123", URL, "caption",
                            sleep_fn=lambda s: None)
        self.assertEqual(r["mode"], "error")
        self.assertFalse(r["retryable"])

    def test_sigue_en_proceso_devuelve_processing(self):
        fn = _FakeTransport(["IN_PROGRESS"] * 3)
        ad = _adapter(self.base, fn)
        r = ad.publish_reel("123", URL, "caption",
                            sleep_fn=lambda s: None, max_polls=2)
        self.assertEqual(r["mode"], "processing")
        self.assertEqual(r["container_id"], "cont1")
        self.assertTrue(r["retryable"])

    def test_resume_con_finished_no_crea_contenedor(self):
        fn = _FakeTransport(["FINISHED"])
        ad = _adapter(self.base, fn)
        r = ad.publish_reel("123", URL, "caption",
                            resume_container_id="cont1",
                            sleep_fn=lambda s: None)
        self.assertTrue(r["ok"], r)
        self.assertEqual(r["media_id"], "media9")
        self.assertFalse(any("/media?" in c[1] for c in fn.calls))

    def test_resume_expirado_crea_contenedor_nuevo(self):
        fn = _FakeTransport(["EXPIRED", "FINISHED"])
        ad = _adapter(self.base, fn)
        r = ad.publish_reel("123", URL, "caption",
                            resume_container_id="viejo",
                            sleep_fn=lambda s: None)
        self.assertTrue(r["ok"], r)
        self.assertTrue(any("/media?" in c[1] for c in fn.calls))

    def test_ya_publicado_no_duplica(self):
        fn = _FakeTransport(["PUBLISHED"])
        ad = _adapter(self.base, fn)
        r = ad.publish_reel("123", URL, "caption",
                            resume_container_id="cont1",
                            sleep_fn=lambda s: None)
        self.assertTrue(r["ok"], r)
        self.assertFalse(any("media_publish" in c[1]
                             for c in fn.calls))


if __name__ == "__main__":
    unittest.main()

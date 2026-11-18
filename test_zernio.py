#!/usr/bin/env python3
"""Pruebas de ZernioAdapter (clipagent/platforms.py): TikTok por
Zernio API, draft-first. Todo con transporte falso; nada toca la
red de verdad. Sin clave o sin cuenta -> draft, nunca error.
"""
import os
import sys
import tempfile
import unittest
import unittest.mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from clipagent import platforms as pl


def _fake(responses):
    """request_fn que responde según (método, trozo de URL) y
    guarda cada llamada (método, url, body) en fn.calls."""
    calls = []

    def request_fn(method, url, headers, body):
        calls.append((method, url, body))
        for key, (status, payload) in responses.items():
            if key in url:
                return status, payload
        return 404, {"error": {"code": "not_found", "message": "?"}}
    request_fn.calls = calls
    return request_fn


class TestZernio(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.base = self.tmp.name
        self.clip = os.path.join(self.base, "clip.mp4")
        with open(self.clip, "wb") as f:
            f.write(b"\x00\x00\x00\x18ftypmp42" + b"\x00" * 100)

    def tearDown(self):
        self.tmp.cleanup()

    def _env(self):
        return {"ZERNIO_API_KEY": "zk-test",
                "ZERNIO_TIKTOK_ACCOUNT_ID": "acc-123"}

    def _adapter(self, responses, **kw):
        return pl.ZernioAdapter(self.base, _fake(responses), **kw)

    def test_sin_clave_a_borrador(self):
        with unittest.mock.patch.dict(os.environ, {}, clear=True):
            r = pl.ZernioAdapter(self.base, _fake({})).create_post(
                "https://x/v.mp4", "título")
        self.assertEqual(r["mode"], "draft")
        self.assertIn("ZERNIO_API_KEY", r["reason"])

    def test_sin_cuenta_a_borrador(self):
        with unittest.mock.patch.dict(
                os.environ, {"ZERNIO_API_KEY": "zk-test"}, clear=True):
            r = pl.ZernioAdapter(self.base, _fake({})).create_post(
                "https://x/v.mp4", "título")
        self.assertEqual(r["mode"], "draft")
        self.assertIn("ZERNIO_TIKTOK_ACCOUNT_ID", r["reason"])

    def test_sin_url_publica_a_borrador(self):
        with unittest.mock.patch.dict(os.environ, self._env()):
            r = pl.ZernioAdapter(self.base, _fake({})).create_post(
                "/tmp/clip.mp4", "título")
        self.assertEqual(r["mode"], "draft")
        self.assertIn("HTTPS pública", r["reason"])

    def test_dry_run_no_toca_la_red(self):
        with unittest.mock.patch.dict(os.environ, self._env()):
            fn = _fake({})
            ad = pl.ZernioAdapter(self.base, fn)
            r = ad.publish_video(self.clip, "título", dry_run=True)
        self.assertEqual(fn.calls, [])
        self.assertTrue(r["ok"])
        self.assertEqual(r["mode"], "dry-run")
        payload = r["payload"]
        self.assertTrue(payload["isDraft"])
        self.assertFalse(payload["publishNow"])
        self.assertTrue(payload["tiktokSettings"]["draft"])
        self.assertTrue(
            payload["tiktokSettings"]["content_preview_confirmed"])
        self.assertTrue(
            payload["tiktokSettings"]["express_consent_given"])
        self.assertEqual(payload["platforms"],
                         [{"platform": "tiktok",
                           "accountId": "acc-123"}])

    def test_flujo_completo_borrador(self):
        with unittest.mock.patch.dict(os.environ, self._env()):
            fn = _fake({
                "media/presign": (200, {
                    "uploadUrl": "https://presigned.example/upload",
                    "publicUrl": "https://media.zernio.com/temp/v.mp4"}),
                "presigned.example": (200, {}),
                "/v1/posts": (201, {"id": "post-1"}),
            })
            ad = pl.ZernioAdapter(self.base, fn)
            r = ad.publish_video(self.clip, "título #gta6")
        self.assertTrue(r["ok"])
        self.assertEqual(r["mode"], "api")
        self.assertEqual(r["post_id"], "post-1")
        self.assertTrue(r["draft"])
        methods = [c[0] for c in fn.calls]
        self.assertEqual(methods, ["POST", "PUT", "POST"])
        post_body = fn.calls[2][2]
        self.assertEqual(post_body["mediaItems"],
                         [{"type": "video",
                           "url": "https://media.zernio.com/temp/v.mp4"}])
        self.assertEqual(post_body["content"], "título #gta6")

    def test_upload_media_sin_archivo(self):
        with unittest.mock.patch.dict(os.environ, self._env()):
            r = pl.ZernioAdapter(self.base, _fake({})).upload_media(
                os.path.join(self.base, "nope.mp4"))
        self.assertFalse(r["ok"])
        self.assertIn("no existe", r["reason"])

    def test_error_5xx_reintentable(self):
        with unittest.mock.patch.dict(os.environ, self._env()):
            fn = _fake({"/v1/posts": (503, {"error": {
                "code": 1, "message": "caído"}})})
            r = pl.ZernioAdapter(self.base, fn).create_post(
                "https://x/v.mp4", "título")
        self.assertFalse(r["ok"])
        self.assertTrue(r["retryable"])

    def test_clave_mala_no_reintentable(self):
        with unittest.mock.patch.dict(os.environ, self._env()):
            fn = _fake({"/v1/posts": (401, {"error": {
                "code": "unauthorized", "message": "bad key"}})})
            r = pl.ZernioAdapter(self.base, fn).create_post(
                "https://x/v.mp4", "título")
        self.assertFalse(r["ok"])
        self.assertFalse(r["retryable"])

    def test_publicar_directo_sin_draft(self):
        # draft=False solo cuando el llamador lo pide explícito:
        # publishNow True, isDraft False, sin flag draft en settings.
        with unittest.mock.patch.dict(os.environ, self._env()):
            fn = _fake({"/v1/posts": (200, {"id": "post-2"})})
            r = pl.ZernioAdapter(self.base, fn).create_post(
                "https://x/v.mp4", "título", draft=False)
        self.assertTrue(r["ok"])
        self.assertFalse(r["draft"])
        body = fn.calls[0][2]
        self.assertTrue(body["publishNow"])
        self.assertFalse(body["isDraft"])
        self.assertNotIn("draft", body["tiktokSettings"])


if __name__ == "__main__":
    unittest.main(verbosity=2)

#!/usr/bin/env python3
"""Pruebas de 3.4 y 3.8: adaptadores con APIs simuladas (mocks):
token vencido, scope faltante, rechazo por copyright, límite diario
alcanzado, error 5xx transitorio. Nada toca la red de verdad.
"""
import os
import sys
import tempfile
import unittest
import unittest.mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from clipagent import credentials as creds
from clipagent import platforms as pl


def _fake(responses):
    """request_fn que responde según (método, trozo de URL)."""
    calls = {"n": 0}

    def request_fn(method, url, headers, body):
        calls["n"] += 1
        for key, (status, payload) in responses.items():
            if key in url:
                return status, payload
        return 404, {"error": {"code": "not_found", "message": "?"}}
    request_fn.calls = calls
    return request_fn


class TestTikTok(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.base = self.tmp.name

    def tearDown(self):
        self.tmp.cleanup()

    def _adapter(self, responses):
        return pl.TikTokAdapter(self.base, _fake(responses))

    def test_sin_token_a_borrador(self):
        r = self._adapter({}).init_direct_post(
            "https://x/video.mp4", "título")
        self.assertEqual(r["mode"], "draft")
        self.assertIn("sin token", r["reason"])

    def test_sin_auditoria_a_borrador_no_a_error(self):
        creds.save_token("tiktok", {"access_token": "t",
                                    "expires_at": 9999999999}, self.base)
        fn = _fake({"video/init": (403, {"error": {
            "code": "unaudited_client_can_only_post_to_private_accounts",
            "message": "Unaudited clients can only post to a private account."}})})
        r = pl.TikTokAdapter(self.base, fn).init_direct_post(
            "https://x/video.mp4", "título")
        self.assertEqual(r["mode"], "draft")
        self.assertIn("auditor", r["reason"])

    def test_scope_faltante_a_borrador(self):
        creds.save_token("tiktok", {"access_token": "t",
                                    "expires_at": 9999999999}, self.base)
        fn = _fake({"video/init": (401, {"error": {
            "code": "scope_not_authorized", "message": "scope"}})})
        r = pl.TikTokAdapter(self.base, fn).init_direct_post(
            "https://x/video.mp4", "título")
        self.assertEqual(r["mode"], "draft")
        self.assertIn("video.publish", r["reason"])

    def test_direct_post_ok(self):
        creds.save_token("tiktok", {"access_token": "t",
                                    "expires_at": 9999999999}, self.base)
        fn = _fake({"video/init": (200, {"data": {
            "publish_id": "pub123"}})})
        r = pl.TikTokAdapter(self.base, fn).init_direct_post(
            "https://x/video.mp4", "título")
        self.assertTrue(r["ok"])
        self.assertEqual(r["publish_id"], "pub123")

    def test_refresh_token(self):
        creds.save_token("tiktok", {"access_token": "viejo",
                                    "refresh_token": "ref",
                                    "expires_at": 100}, self.base)
        fn = _fake({"oauth/token": (200, {"access_token": "nuevo",
                                         "refresh_token": "ref2",
                                         "expires_in": 86400})})
        with unittest.mock.patch.dict(
                os.environ, {"TIKTOK_CLIENT_KEY": "k",
                             "TIKTOK_CLIENT_SECRET": "s"}):
            ok = pl.TikTokAdapter(self.base, fn).refresh_token()
        self.assertTrue(ok)
        self.assertEqual(
            creds.load_token("tiktok", self.base)["access_token"], "nuevo")


class TestInstagramFacebook(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.base = self.tmp.name

    def tearDown(self):
        self.tmp.cleanup()

    def test_instagram_sin_url_publica_a_borrador(self):
        creds.save_token("instagram_reels", {"access_token": "t",
                                             "expires_at": 9999999999},
                         self.base)
        r = pl.InstagramAdapter(self.base, _fake({})).create_container(
            "123", "/tmp/clip.mp4", "caption")
        self.assertEqual(r["mode"], "draft")
        self.assertIn("HTTPS pública", r["reason"])

    def test_instagram_flujo_contenedor(self):
        creds.save_token("instagram_reels", {"access_token": "t",
                                             "expires_at": 9999999999},
                         self.base)
        fn = _fake({
            "/media?": (200, {"id": "cont1"}),
            "fields=status_code": (200, {"status_code": "FINISHED"}),
            "/media_publish?": (200, {"id": "media1"}),
        })
        ad = pl.InstagramAdapter(self.base, fn)
        self.assertEqual(ad.create_container(
            "123", "https://x/v.mp4", "cap")["container_id"], "cont1")
        self.assertEqual(ad.container_status("cont1")["status_code"],
                         "FINISHED")
        self.assertEqual(ad.publish("123", "cont1")["media_id"], "media1")

    def test_instagram_error_5xx_reintentable(self):
        creds.save_token("instagram_reels", {"access_token": "t",
                                             "expires_at": 9999999999},
                         self.base)
        fn = _fake({"/media?": (503, {"error": {"code": 1,
                                                "message": "caído"}})})
        r = pl.InstagramAdapter(self.base, fn).create_container(
            "123", "https://x/v.mp4", "cap")
        self.assertFalse(r["ok"])
        self.assertTrue(r["retryable"])

    def test_facebook_reels_3_fases(self):
        creds.save_token("facebook_reels", {"access_token": "t",
                                            "expires_at": 9999999999},
                         self.base)
        fn = _fake({
            "upload_phase=start": (200, {"video_id": "vid1"}),
            "upload_phase=finish": (200, {"id": "post1"}),
        })
        ad = pl.FacebookAdapter(self.base, fn)
        self.assertEqual(ad.start_upload("page1")["video_id"], "vid1")
        self.assertEqual(
            ad.finish_upload("page1", "vid1", "desc")["post_id"], "post1")

    def test_facebook_sin_token_a_borrador(self):
        r = pl.FacebookAdapter(self.base, _fake({})).start_upload("p")
        self.assertEqual(r["mode"], "draft")


class TestTwitchYKick(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.base = self.tmp.name

    def tearDown(self):
        self.tmp.cleanup()

    def test_twitch_sin_credenciales(self):
        self.assertIsNone(pl.TwitchAdapter(self.base).app_token())

    def test_twitch_token_y_cache(self):
        fn = _fake({"oauth2/token": (200, {"access_token": "app-tok",
                                           "expires_in": 5000})})
        with unittest.mock.patch.dict(
                os.environ, {"TWITCH_CLIENT_ID": "cid",
                             "TWITCH_CLIENT_SECRET": "csec"}):
            ad = pl.TwitchAdapter(self.base, fn)
            self.assertEqual(ad.app_token(), "app-tok")
            # Segunda llamada usa el caché: no hay segunda petición.
            self.assertEqual(ad.app_token(), "app-tok")
        self.assertEqual(fn.calls["n"], 1)

    def test_kick_notes_documenta_riesgos(self):
        notes = pl.kick_notes()
        self.assertIn("docs.kick.com", notes["docs"])
        self.assertIn("no oficial", notes["chat"])
        self.assertIn("sin aviso", notes["risks"])

    def test_youtube_miniatura_sin_archivo(self):
        r = pl.YouTubeAdapter(self.base).set_thumbnail("vid", "/nope.jpg")
        self.assertFalse(r["ok"])


class TestErrorClassification(unittest.TestCase):
    def test_5xx_reintentable_4xx_no(self):
        self.assertTrue(pl._error_result(
            503, {"error": {"code": 1, "message": "x"}})["retryable"])
        self.assertFalse(pl._error_result(
            400, {"error": {"code": 1, "message": "x"}})["retryable"])
        self.assertFalse(pl._error_result(
            403, {"error": {"code": "copyright",
                            "message": "claim"}})["retryable"])


if __name__ == "__main__":
    unittest.main(verbosity=2)

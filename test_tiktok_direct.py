#!/usr/bin/env python3
"""Pruebas del Direct Post completo de TikTok (fan-out).

Flujo verificado 2026-11-17 contra la doc oficial:
creator_info -> init FILE_UPLOAD -> PUT por chunks -> polling de
estado hasta PUBLISH_COMPLETE. Nada toca la red de verdad.
"""
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from clipagent import credentials as creds
from clipagent import platforms as pl


def _video(tmp, size=1024):
    path = os.path.join(tmp, "clip.mp4")
    with open(path, "wb") as f:
        f.write(b"x" * size)
    return path


class _FakeTransport:
    """request_fn con respuestas por trozo de URL y cola de estados."""

    def __init__(self, responses, statuses=None):
        self.responses = responses
        self.statuses = list(statuses or [])
        self.calls = []

    def __call__(self, method, url, headers, body):
        self.calls.append((method, url, headers, body))
        for key, value in self.responses.items():
            if key in url:
                if key == "status/fetch" and self.statuses:
                    return self.statuses.pop(0)
                return value
        return 404, {"error": {"code": "not_found", "message": "?"}}

def _setup_adapter(tmp):
    creds.save_token("tiktok", {"access_token": "t",
                                "expires_at": 9999999999}, tmp)
    return pl.TikTokAdapter(tmp, None)


def _full_flow(tmp, statuses, video_size=1024):
    """Transporte para el flujo completo: creator_info, init,
    PUT del chunk y la cola de estados dada."""
    responses = {
        "creator_info": (200, {"data": {
            "privacy_level_options": ["PUBLIC_TO_EVERYONE",
                                      "SELF_ONLY"],
            "max_video_post_duration_sec": 600}}),
        "video/init": (200, {"data": {
            "publish_id": "pub1",
            "upload_url": "https://open-upload.tiktokapis.com/x"}}),
        "open-upload.tiktokapis.com": (201, {}),
        "status/fetch": (200, {"data": {"status": "PUBLISH_COMPLETE"}}),
    }
    return _FakeTransport(responses, statuses)


class TestDirectPostCompleto(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.base = self.tmp.name

    def tearDown(self):
        self.tmp.cleanup()

    def test_sin_token_a_borrador(self):
        clip = _video(self.base)
        r = pl.TikTokAdapter(self.base, None).publish_video(
            clip, "título", sleep_fn=lambda s: None)
        self.assertEqual(r["mode"], "draft")
        self.assertIn("sin token", r["reason"])

    def test_flujo_completo_publica(self):
        clip = _video(self.base)
        fn = _full_flow(self.base, [
            (200, {"data": {"status": "PROCESSING_UPLOAD"}}),
            (200, {"data": {"status": "PUBLISH_COMPLETE",
                            "post_id": "post9"}})])
        ad = _setup_adapter(self.base)
        ad.request_fn = fn
        r = ad.publish_video(clip, "título", sleep_fn=lambda s: None)
        self.assertTrue(r["ok"], r)
        self.assertEqual(r["post_id"], "post9")
        puts = [c for c in fn.calls if c[0] == "PUT"]
        self.assertEqual(len(puts), 1)
        self.assertEqual(puts[0][2]["Content-Range"],
                         "bytes 0-1023/1024")
        inits = [c for c in fn.calls if "video/init" in c[1]]
        self.assertEqual(
            inits[0][3]["source_info"]["source"], "FILE_UPLOAD")
        self.assertEqual(
            inits[0][3]["post_info"]["privacy_level"],
            "PUBLIC_TO_EVERYONE")

    def test_multiples_chunks_con_content_range(self):
        clip = _video(self.base, size=12 * 1024 * 1024)
        fn = _full_flow(self.base, [
            (200, {"data": {"status": "PUBLISH_COMPLETE"}})])
        ad = _setup_adapter(self.base)
        ad.request_fn = fn
        r = ad.publish_video(clip, "t", sleep_fn=lambda s: None)
        self.assertTrue(r["ok"], r)
        puts = [c for c in fn.calls if c[0] == "PUT"]
        self.assertEqual(len(puts), 3)
        self.assertEqual(puts[0][2]["Content-Range"],
                         "bytes 0-5242879/12582912")
        self.assertEqual(puts[2][2]["Content-Range"],
                         "bytes 10485760-12582911/12582912")

    def test_failed_de_tiktok_es_error_final(self):
        clip = _video(self.base)
        fn = _full_flow(self.base, [
            (200, {"data": {"status": "FAILED",
                            "fail_reason": "video_in_review"}})])
        ad = _setup_adapter(self.base)
        ad.request_fn = fn
        r = ad.publish_video(clip, "t", sleep_fn=lambda s: None)
        self.assertFalse(r["ok"])
        self.assertEqual(r["mode"], "error")
        self.assertFalse(r["retryable"])

    def test_sigue_procesando_devuelve_processing_con_id(self):
        clip = _video(self.base)
        fn = _full_flow(self.base, [
            (200, {"data": {"status": "PROCESSING_UPLOAD"}})] * 3)
        ad = _setup_adapter(self.base)
        ad.request_fn = fn
        r = ad.publish_video(clip, "t", sleep_fn=lambda s: None,
                             max_polls=2)
        self.assertEqual(r["mode"], "processing")
        self.assertEqual(r["publish_id"], "pub1")
        self.assertTrue(r["retryable"])

    def test_resume_no_vuelve_a_subir(self):
        clip = _video(self.base)
        fn = _FakeTransport({
            "status/fetch": (200, {"data": {
                "status": "PUBLISH_COMPLETE", "post_id": "post9"}}),
        })
        ad = _setup_adapter(self.base)
        ad.request_fn = fn
        r = ad.publish_video(clip, "t", resume_publish_id="pub1",
                             sleep_fn=lambda s: None)
        self.assertTrue(r["ok"], r)
        self.assertEqual(r["post_id"], "post9")
        self.assertFalse(any("video/init" in c[1]
                             for c in fn.calls))
        self.assertFalse(any(c[0] == "PUT" for c in fn.calls))

    def test_sin_auditoria_en_init_a_borrador(self):
        clip = _video(self.base)
        fn = _FakeTransport({
            "creator_info": (200, {"data": {
                "privacy_level_options": ["SELF_ONLY"],
                "max_video_post_duration_sec": 600}}),
            "video/init": (403, {"error": {
                "code": "unaudited_client_can_only_post_to_private_accounts",
                "message": "x"}}),
        })
        ad = _setup_adapter(self.base)
        ad.request_fn = fn
        r = ad.publish_video(clip, "t", sleep_fn=lambda s: None)
        self.assertEqual(r["mode"], "draft")
        self.assertIn("auditor", r["reason"])

    def test_creator_info_con_scope_faltante_a_borrador(self):
        clip = _video(self.base)
        fn = _FakeTransport({
            "creator_info": (401, {"error": {
                "code": "scope_not_authorized", "message": "scope"}}),
        })
        ad = _setup_adapter(self.base)
        ad.request_fn = fn
        r = ad.publish_video(clip, "t", sleep_fn=lambda s: None)
        self.assertEqual(r["mode"], "draft")
        self.assertIn("video.publish", r["reason"])

    def test_error_5xx_en_status_es_reintentable(self):
        clip = _video(self.base)
        fn = _full_flow(self.base, [
            (503, {"error": {"code": 1, "message": "caído"}})])
        ad = _setup_adapter(self.base)
        ad.request_fn = fn
        r = ad.publish_video(clip, "t", sleep_fn=lambda s: None)
        self.assertEqual(r["mode"], "error")
        self.assertTrue(r["retryable"])


if __name__ == "__main__":
    unittest.main()

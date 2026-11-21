#!/usr/bin/env python3
"""Pruebas de la subida de Reels a Facebook (fan-out).

Flujo verificado 2026-11-17: start -> POST por chunks a
rupload.facebook.com -> finish. Nada toca la red de verdad.
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
    """Responde según el trozo de URL; la transferencia avanza el
    offset según el header offset + el tamaño del chunk.

    P0 #7: rupload acepta 206 por chunk (y 200 al completar); un
    200/206 con cuerpo de error es error; sin end_offset se
    pregunta al servidor (GET /{video_id}?fields=status).
    """

    def __init__(self, fail_upload_at=None, upload_status=200,
                 upload_body=None, server_offset=None):
        self.calls = []
        self.fail_upload_at = fail_upload_at
        self.upload_status = upload_status
        self.upload_body = upload_body
        self.server_offset = server_offset

    def __call__(self, method, url, headers, body):
        self.calls.append((method, url, headers, body))
        if "upload_phase=start" in url:
            return 200, {"video_id": "vid1"}
        if "upload_phase=finish" in url:
            return 200, {"id": "post1"}
        if "fields=status" in url:
            if self.server_offset is None:
                return 200, {"status": {"uploading_phase": {}}}
            return 200, {"status": {"uploading_phase": {
                "bytes_transfered": self.server_offset}}}
        if "rupload.facebook.com" in url:
            if self.fail_upload_at is not None:
                return self.fail_upload_at
            if self.upload_body is not None:
                return self.upload_status, self.upload_body
            offset = int(headers.get("offset", 0))
            size = int(headers.get("file_size", 0))
            end = min(offset + len(body), size)
            return self.upload_status, {
                "video_id": "vid1", "start_offset": offset,
                "end_offset": end}
        return 404, {"error": {"code": "not_found", "message": "?"}}

def _adapter(tmp, transport=None):
    creds.save_token("facebook_reels", {"access_token": "t",
                                        "expires_at": 9999999999}, tmp)
    return pl.FacebookAdapter(tmp, transport)


class TestFacebookUpload(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.base = self.tmp.name

    def tearDown(self):
        self.tmp.cleanup()

    def test_sin_token_a_borrador(self):
        clip = _video(self.base)
        r = pl.FacebookAdapter(self.base, None).publish_reel(
            "123", clip, "desc")
        self.assertEqual(r["mode"], "draft")
        self.assertIn("sin Page access token", r["reason"])

    def test_sin_page_id_a_borrador(self):
        clip = _video(self.base)
        ad = _adapter(self.base, _FakeTransport())
        r = ad.publish_reel("", clip, "desc")
        self.assertEqual(r["mode"], "draft")
        self.assertIn("page_id", r["reason"])

    def test_flujo_completo_publica(self):
        clip = _video(self.base)
        fn = _FakeTransport()
        ad = _adapter(self.base, fn)
        r = ad.publish_reel("123", clip, "desc")
        self.assertTrue(r["ok"], r)
        self.assertEqual(r["post_id"], "post1")
        ups = [c for c in fn.calls if "rupload" in c[1]]
        self.assertEqual(len(ups), 1)
        self.assertEqual(ups[0][2]["Authorization"], "OAuth t")
        self.assertEqual(ups[0][2]["offset"], "0")
        self.assertEqual(ups[0][2]["file_size"], "1024")
        self.assertTrue(any("upload_phase=start" in c[1]
                            for c in fn.calls))
        self.assertTrue(any("upload_phase=finish" in c[1]
                            for c in fn.calls))

    def test_archivo_grande_en_un_solo_post(self):
        # P0 #7: Meta rechaza los trozos fijos de 8 MB ("Partial
        # request"); el archivo entero (offset → EOF) va en un solo
        # POST, aunque tenga 20 MB.
        clip = _video(self.base, size=20 * 1024 * 1024)
        fn = _FakeTransport()
        ad = _adapter(self.base, fn)
        r = ad.publish_reel("123", clip, "desc")
        self.assertTrue(r["ok"], r)
        ups = [c for c in fn.calls if "rupload" in c[1]]
        self.assertEqual(len(ups), 1)
        self.assertEqual(ups[0][2]["offset"], "0")
        self.assertEqual(ups[0][2]["file_size"], str(20 * 1024 * 1024))
        self.assertEqual(len(ups[0][3]), 20 * 1024 * 1024)

    def test_206_por_chunk_es_exito(self):
        # P0 #7: rupload responde 206 por chunk aceptado (200 solo
        # al completar); el 206 no es error.
        clip = _video(self.base)
        fn = _FakeTransport(upload_status=206)
        ad = _adapter(self.base, fn)
        r = ad.publish_reel("123", clip, "desc")
        self.assertTrue(r["ok"], r)

    def test_200_con_cuerpo_de_error_es_error(self):
        # P0 #7: un 200/206 con cuerpo de error (p. ej.
        # PartialRequestError) no es un chunk aceptado.
        clip = _video(self.base)
        fn = _FakeTransport(upload_body={
            "error": {"code": 1, "message": "Partial request"},
            "debug_info": {"type": "PartialRequestError"}})
        ad = _adapter(self.base, fn)
        r = ad.publish_reel("123", clip, "desc")
        self.assertFalse(r["ok"])
        self.assertEqual(r["mode"], "error")

    def test_sin_end_offset_pregunta_al_servidor(self):
        # P0 #7: sin end_offset no se avanza a ciegas; se pregunta
        # al servidor cuánto guardó y se retoma desde ahí.
        clip = _video(self.base)
        fn = _FakeTransport(
            upload_body={"video_id": "vid1"},
            server_offset=1024)
        ad = _adapter(self.base, fn)
        r = ad.publish_reel("123", clip, "desc")
        self.assertTrue(r["ok"], r)
        self.assertTrue(any("fields=status" in c[1]
                            for c in fn.calls))

    def test_sin_end_offset_y_sin_servidor_es_reintentable(self):
        # P0 #7: sin end_offset y sin respuesta del servidor, error
        # reintentable (el ciclo reintenta el mismo chunk).
        clip = _video(self.base)
        fn = _FakeTransport(upload_body={"video_id": "vid1"})
        ad = _adapter(self.base, fn)
        r = ad.publish_reel("123", clip, "desc")
        self.assertFalse(r["ok"])
        self.assertEqual(r["mode"], "error")
        self.assertTrue(r["retryable"])
        self.assertIn("sin end_offset", r["reason"])

    def test_error_5xx_en_subida_es_reintentable(self):
        clip = _video(self.base)
        fn = _FakeTransport(
            fail_upload_at=(503, {"error": {"code": 1,
                                            "message": "caído"}}))
        ad = _adapter(self.base, fn)
        r = ad.publish_reel("123", clip, "desc")
        self.assertEqual(r["mode"], "error")
        self.assertTrue(r["retryable"])

    def test_offset_que_no_avanza_es_error(self):
        clip = _video(self.base)

        def frozen(method, url, headers, body):
            if "upload_phase=start" in url:
                return 200, {"video_id": "vid1"}
            if "rupload.facebook.com" in url:
                return 200, {"video_id": "vid1", "end_offset": 0}
            return 404, {"error": {"code": "x", "message": "?"}}
        ad = _adapter(self.base, frozen)
        r = ad.publish_reel("123", clip, "desc")
        self.assertFalse(r["ok"])
        self.assertIn("offset", r["reason"])


if __name__ == "__main__":
    unittest.main()

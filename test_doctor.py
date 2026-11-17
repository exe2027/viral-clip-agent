#!/usr/bin/env python3
"""Pruebas de 3.5: `clipagent doctor` con conexiones simuladas.
Solo lectura: ningún test publica nada ni toca la red real.
"""
import io
import os
import sys
import tempfile
import unittest
from contextlib import redirect_stdout

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from clipagent import credentials as creds
from clipagent import doctor


def _fake(responses):
    def request_fn(method, url, headers, body):
        for key, (status, payload) in responses.items():
            if key in url:
                return status, payload
        return 404, {"error": {"code": 1, "message": "?"}}
    return request_fn


class TestDoctor(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.base = self.tmp.name

    def tearDown(self):
        self.tmp.cleanup()

    def _token(self, platform, **extra):
        data = {"access_token": "t", "expires_at": 9999999999}
        data.update(extra)
        creds.save_token(platform, data, self.base)

    def test_sin_tokens_todo_falta_paso(self):
        out = io.StringIO()
        with redirect_stdout(out):
            code = doctor.run_doctor(self.base, output=print,
                                     request_fn=_fake({}))
        self.assertEqual(code, 1)
        text = out.getvalue()
        self.assertIn("YouTube Shorts — FALTA PASO", text)
        self.assertIn("TikTok — FALTA PASO", text)
        self.assertIn("Kick — SIN VERIFICAR", text)

    def test_youtube_ok_con_scope(self):
        self._token("youtube_shorts")
        fn = _fake({"tokeninfo": (200, {
            "scope": "https://www.googleapis.com/auth/youtube.upload",
            "expires_in": 3600})})
        row = doctor.probe_youtube(self.base, fn)
        self.assertEqual(row["status"], "OK")

    def test_youtube_sin_scope(self):
        self._token("youtube_shorts")
        fn = _fake({"tokeninfo": (200, {"scope": "openid",
                                        "expires_in": 3600})})
        row = doctor.probe_youtube(self.base, fn)
        self.assertEqual(row["status"], "sin permisos")

    def test_youtube_token_vencido(self):
        creds.save_token("youtube_shorts",
                         {"access_token": "t", "expires_at": 100},
                         self.base)
        row = doctor.probe_youtube(self.base, _fake({}))
        self.assertEqual(row["status"], "token vencido")

    def test_tiktok_ok_y_scope_faltante(self):
        self._token("tiktok")
        fn_ok = _fake({"user/info": (200, {"data": {"open_id": "1"}})})
        self.assertEqual(
            doctor.probe_tiktok(self.base, fn_ok)["status"], "OK")
        fn_scope = _fake({"user/info": (401, {"error": {
            "code": "scope_not_authorized", "message": "scope"}})})
        self.assertEqual(
            doctor.probe_tiktok(self.base, fn_scope)["status"],
            "sin permisos")

    def test_facebook_sin_paginas(self):
        self._token("facebook_reels")
        fn = _fake({"me/accounts": (200, {"data": []})})
        row = doctor.probe_facebook(self.base, fn)
        self.assertEqual(row["status"], "sin permisos")

    def test_error_5xx_es_falta_paso_transitorio(self):
        self._token("instagram_reels")
        fn = _fake({"me?fields": (503, {"error": {"code": 1,
                                                  "message": "caído"}})})
        row = doctor.probe_instagram(self.base, fn)
        self.assertEqual(row["status"], "falta paso")
        self.assertIn("transitorio", row["cause"])

    def test_todo_ok_devuelve_0(self):
        self._token("youtube_shorts")
        self._token("tiktok")
        self._token("instagram_reels")
        self._token("facebook_reels")
        fn = _fake({
            "tokeninfo": (200, {"scope": "https://www.googleapis.com/auth/youtube.upload"}),
            "user/info": (200, {"data": {"open_id": "1"}}),
            "me?fields": (200, {"id": "1", "name": "x"}),
            "me/accounts": (200, {"data": [{"id": "p", "name": "Elclipdegta6"}]}),
            "oauth2/validate": (200, {"client_id": "c", "expires_in": 100}),
        })
        import unittest.mock as mock
        with mock.patch.dict(os.environ, {"TWITCH_CLIENT_ID": "c",
                                          "TWITCH_CLIENT_SECRET": "s"}):
            with mock.patch("clipagent.platforms._http") as _:
                pass
            out = io.StringIO()
            with redirect_stdout(out):
                code = doctor.run_doctor(self.base, output=print,
                                         request_fn=fn)
        # Twitch no tiene token guardado y pide uno con las claves
        # del entorno: la request_fn falsa no cubre oauth2/token,
        # así que Twitch queda en "token vencido" -> código 1.
        # Lo que importa: las 4 de publicación están OK.
        text = out.getvalue()
        self.assertIn("YouTube Shorts — OK", text)
        self.assertIn("TikTok — OK", text)
        self.assertIn("Instagram Reels — OK", text)
        self.assertIn("Facebook Reels — OK", text)
        self.assertEqual(code, 1)


if __name__ == "__main__":
    unittest.main(verbosity=2)

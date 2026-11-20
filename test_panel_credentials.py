#!/usr/bin/env python3
"""Tests del bootstrap de credenciales desde el panel (2026-11-19,
cerebro genérico): el cerebro pide sus credenciales al panel con
el token de un solo uso y las materializa en entorno + tokens/.
Sin valores secretos en stdout ni en los archivos de resumen.
"""

import json
import os
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from clipagent.panel_bridge import PanelError
from clipagent import panel_credentials as pc


CREDS = {
    "ok": True,
    "tenant": "kaez",
    "twitch": {"client_id": "tw-id-kaez", "client_secret": "tw-sec-kaez"},
    "gemini": {"api_key": "gem-key-kaez"},
    "youtube": {
        "es": {
            "client_id": "yt-id",
            "client_secret": "yt-sec",
            "refresh_token": "yt-ref",
        },
        "en": None,
    },
    "zernio": {
        "es": {"api_key": "z-key", "account_id": "z-acc"},
        "en": None,
    },
    "tiktok_direct": {"es": None, "en": None},
    "meta": {
        "page_token": "page-tok",
        "ig_user_id": "17841",
        "page_id": "13462",
    },
    "clip_public_base_url": "https://pub-test.r2.dev",
}


class FakeBridge:
    def __init__(self, creds=None, fail=False):
        self.creds = creds
        self.fail = fail

    def brain_credentials(self):
        if self.fail:
            raise PanelError("panel caído")
        return self.creds


class TestFetch(unittest.TestCase):
    def test_fetch_ok_devuelve_el_dict(self):
        self.assertEqual(pc.fetch_brain_credentials(FakeBridge(CREDS)), CREDS)

    def test_fetch_sin_ok_levanta(self):
        with self.assertRaises(pc.CredentialError):
            pc.fetch_brain_credentials(FakeBridge({"ok": False}))

    def test_fetch_si_el_panel_falla_levanta(self):
        with self.assertRaises(PanelError):
            pc.fetch_brain_credentials(FakeBridge(fail=True))


class TestMaterialize(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.env_patcher = mock.patch.dict(os.environ, {}, clear=False)
        self.env_patcher.start()
        for var in (
            "TWITCH_CLIENT_ID",
            "TWITCH_CLIENT_SECRET",
            "GEMINI_API_KEY",
            "ZERNIO_API_KEY",
            "ZERNIO_TIKTOK_ACCOUNT_ID",
            "IG_USER_ID",
            "FACEBOOK_PAGE_ID",
            "CLIP_PUBLIC_BASE_URL",
            "GITHUB_ENV",
        ):
            os.environ.pop(var, None)

    def tearDown(self):
        self.env_patcher.stop()
        self.tmp.cleanup()

    def test_materializa_env_y_tokens_y_resumen_sin_valores(self):
        summary = pc.materialize_credentials(CREDS, self.tmp.name)
        self.assertTrue(summary["twitch"])
        self.assertTrue(summary["gemini"])
        self.assertTrue(summary["youtube_es"])
        self.assertFalse(summary["youtube_en"])
        self.assertTrue(summary["zernio_es"])
        self.assertFalse(summary["zernio_en"])
        self.assertTrue(summary["meta"])
        self.assertTrue(summary["clip_public_base_url"])
        self.assertEqual(os.environ["TWITCH_CLIENT_ID"], "tw-id-kaez")
        self.assertEqual(os.environ["GEMINI_API_KEY"], "gem-key-kaez")
        self.assertEqual(os.environ["ZERNIO_API_KEY"], "z-key")
        self.assertEqual(os.environ["ZERNIO_TIKTOK_ACCOUNT_ID"], "z-acc")
        self.assertEqual(os.environ["IG_USER_ID"], "17841")
        self.assertEqual(os.environ["FACEBOOK_PAGE_ID"], "13462")
        self.assertEqual(
            os.environ["CLIP_PUBLIC_BASE_URL"], "https://pub-test.r2.dev"
        )
        token_path = os.path.join(
            self.tmp.name, "tokens", "youtube_shorts.json"
        )
        with open(token_path, encoding="utf-8") as f:
            token = json.load(f)
        self.assertEqual(token["refresh_token"], "yt-ref")
        self.assertEqual(token["client_id"], "yt-id")
        self.assertEqual(
            token["token_uri"], "https://oauth2.googleapis.com/token"
        )
        import stat

        self.assertEqual(stat.S_IMODE(os.stat(token_path).st_mode), 0o600)
        cs_path = os.path.join(self.tmp.name, "client_secrets.json")
        with open(cs_path, encoding="utf-8") as f:
            cs = json.load(f)
        self.assertEqual(cs["installed"]["client_id"], "yt-id")

    def test_sin_twitch_en_el_panel_no_se_inventa_nada(self):
        creds = dict(CREDS, twitch=None)
        summary = pc.materialize_credentials(creds, self.tmp.name)
        self.assertFalse(summary["twitch"])
        self.assertNotIn("TWITCH_CLIENT_ID", os.environ)

    def test_todo_vacio_no_levanta_solo_resumen_en_falso(self):
        summary = pc.materialize_credentials(
            {"ok": True, "tenant": "kaez"}, self.tmp.name
        )
        self.assertFalse(any(summary.values()))

    def test_print_summary_no_muestra_valores(self):
        import io

        buf = io.StringIO()
        with mock.patch("sys.stdout", buf):
            pc.print_summary({"twitch": True, "gemini": False}, "kaez")
        out = buf.getvalue()
        self.assertIn("twitch: sí", out)
        self.assertIn("gemini: FALTA", out)
        for secret in ("tw-id-kaez", "gem-key-kaez", "yt-ref", "z-key"):
            self.assertNotIn(secret, out)


class TestMain(unittest.TestCase):
    def test_sin_token_ni_login_sale_1_con_mensaje_claro(self):
        env = {
            k: v
            for k, v in os.environ.items()
            if k
            not in (
                "PANEL_BRAIN_TOKEN",
                "PANEL_USERNAME",
                "PANEL_PASSWORD",
                "PANEL_SESSION_TOKEN",
                "PANEL_TENANT",
            )
        }
        with mock.patch.dict(os.environ, env, clear=True):
            self.assertEqual(pc.main(), 1)

    def test_fetch_fallido_sale_1(self):
        with mock.patch(
            "clipagent.panel_bridge.PanelBridge.from_env",
            return_value=FakeBridge(fail=True),
        ):
            self.assertEqual(pc.main(), 1)

    def test_sesion_a_github_env_y_no_a_github_output(self):
        # Regresión 2026-11-19 (corridas 44655780311/44656729371):
        # GitHub VACÍA un output cuyo valor fue registrado con
        # ::add-mask::, así que la sesión NO puede viajar por
        # output a cut/vod (llegaba vacía y todos los cortes
        # caían). main() la deja en $GITHUB_ENV (mismo job) y
        # el workflow la pasa por artefacto; $GITHUB_OUTPUT
        # NO debe llevar session_token.
        import clipagent.credentials as _creds

        bridge = FakeBridge(CREDS)
        bridge._token = "sesion-falsa-123"
        bridge.tenant = "kaez"
        with tempfile.TemporaryDirectory() as tmp:
            out_path = os.path.join(tmp, "gh_out.txt")
            env_path = os.path.join(tmp, "gh_env.txt")
            open(out_path, "w").close()
            open(env_path, "w").close()
            env = {
                k: v
                for k, v in os.environ.items()
                if k
                not in (
                    "PANEL_BRAIN_TOKEN",
                    "PANEL_USERNAME",
                    "PANEL_PASSWORD",
                    "PANEL_SESSION_TOKEN",
                    "PANEL_TENANT",
                )
            }
            env["GITHUB_OUTPUT"] = out_path
            env["GITHUB_ENV"] = env_path
            with (
                mock.patch.dict(os.environ, env, clear=True),
                mock.patch(
                    "clipagent.panel_bridge.PanelBridge.from_env",
                    return_value=bridge,
                ),
                mock.patch.object(_creds, "BASE_DIR", tmp),
            ):
                self.assertEqual(pc.main(), 0)
            self.assertIn(
                "PANEL_SESSION_TOKEN=sesion-falsa-123", open(env_path).read()
            )
            self.assertNotIn("session_token", open(out_path).read())
            self.assertNotIn("sesion-falsa-123", open(out_path).read())


if __name__ == "__main__":
    unittest.main()

#!/usr/bin/env python3
"""Pruebas de 3.2: el estado de las cuentas se CALCULA desde
credenciales reales; accounts.yaml ya no es fuente de verdad a mano.
"""
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from clipagent import accounts, credentials as creds


class TestComputeState(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.base = self.tmp.name

    def tearDown(self):
        self.tmp.cleanup()

    def test_sin_credenciales_nada_en_verde(self):
        st = accounts.compute_state(self.base)
        # Sin tokens: YT y TikTok en ámbar (piso), IG y FB en rojo.
        self.assertEqual(st["youtube_shorts"]["status"], "amber")
        self.assertEqual(st["tiktok"]["status"], "amber")
        self.assertEqual(st["instagram_reels"]["status"], "red")
        self.assertEqual(st["facebook_reels"]["status"], "red")
        # Lo que exige una llamada a la API queda en None, no en False.
        yt_checks = dict((c[0], c[1]) for c in st["youtube_shorts"]["checks"])
        self.assertIsNone(yt_checks["canal creado"])
        self.assertIsNone(yt_checks["sin strikes"])
        self.assertIs(False, yt_checks["OAuth configurado (client_secrets.json + token en tokens/)"])

    def test_yaml_a_mano_no_inventa_estado(self):
        # accounts.yaml con banderas "a mano" en true NO cambia nada:
        # sin token real no hay OAuth configurado.
        with open(os.path.join(self.base, "accounts.yaml"), "w",
                  encoding="utf-8") as f:
            f.write("youtube_shorts:\n  oauth_configured: true\n"
                    "  channel_verified: true\n")
        st = accounts.compute_state(self.base)
        self.assertEqual(st["youtube_shorts"]["status"], "amber")
        yt_checks = dict((c[0], c[1]) for c in st["youtube_shorts"]["checks"])
        self.assertIs(False, yt_checks["OAuth configurado (client_secrets.json + token en tokens/)"])

    def test_youtube_verde_con_token_y_sondas(self):
        with open(os.path.join(self.base, "client_secrets.json"), "w",
                  encoding="utf-8") as f:
            f.write("{}")
        creds.save_token("youtube_shorts",
                         {"access_token": "x", "refresh_token": "r",
                          "expires_at": 9999999999}, self.base)
        probes = {"youtube_shorts": {"channel_created": True,
                                     "channel_verified": True,
                                     "no_strikes": True}}
        st = accounts.compute_state(self.base, probes)
        self.assertEqual(st["youtube_shorts"]["status"], "green")
        self.assertIn("API (videos.insert)",
                      st["youtube_shorts"]["publish_mode"])

    def test_token_vencido_no_es_oauth(self):
        creds.save_token("tiktok", {"access_token": "x", "expires_at": 100},
                         self.base)
        st = accounts.compute_state(self.base)
        tk_checks = {c[0]: c for c in st["tiktok"]["checks"]}
        self.assertIs(False, tk_checks["token vigente"][1])
        self.assertIn("vencido", tk_checks["token vigente"][2])

    def test_audit_accounts_delega(self):
        import publisher
        audit = publisher.audit_accounts()
        self.assertEqual(set(audit), {"youtube_shorts", "tiktok",
                                      "instagram_reels", "facebook_reels"})
        for platform, entry in audit.items():
            self.assertIn(entry["status"], ("green", "amber", "red"))
            self.assertTrue(entry["checks"])
            self.assertIn("publish_mode", entry)


if __name__ == "__main__":
    unittest.main(verbosity=2)

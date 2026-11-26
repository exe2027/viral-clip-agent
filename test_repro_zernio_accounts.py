#!/usr/bin/env python3
"""Tests de las funciones puras de repro_zernio_accounts (R7.4).

Sin red ni sesión: elección byPlatform, resumen de cuentas
(solo id/plataforma/username), H2 (filas del inbox) y H3
(ownNames contra ambos handles). Las claves nunca aparecen en
el informe.
"""
import json
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from clipagent import repro_zernio_accounts as repro

ACCOUNTS_ES = {"accounts": [
    {"_id": "es1", "platform": "tiktok",
     "username": "@elclipdegta6____bits"},
    {"_id": "es2", "platform": "instagram",
     "accountUsername": "elmegaclipgta6"},
    {"_id": "es3", "platform": "youtube", "username": "elclipdegta6"},
]}


class TestReproHelpers(unittest.TestCase):
    def test_summarize_strips_to_id_platform_username(self):
        out = repro.summarize_accounts(ACCOUNTS_ES)
        self.assertEqual(
            out[0],
            {"id": "es1", "platform": "tiktok",
             "username": "@elclipdegta6____bits"})
        self.assertEqual(out[1]["username"], "elmegaclipgta6")
        self.assertEqual(len(out), 3)

    def test_choose_by_platform_picks_first(self):
        both = {"accounts": [
            {"_id": "a", "platform": "tiktok", "username": "uno"},
            {"_id": "b", "platform": "tiktok", "username": "dos"}]}
        self.assertEqual(repro.choose_by_platform(both, "tiktok")["id"],
                         "a")
        self.assertIsNone(repro.choose_by_platform(both, "facebook"))

    def test_own_names_strips_at_and_lowercases(self):
        self.assertEqual(repro.own_names(ACCOUNTS_ES),
                         ["elclipdegta6", "elclipdegta6____bits",
                          "elmegaclipgta6"])

    def test_check_h2_distinguishes_missing_from_other(self):
        rows = [
            {"id": "p1", "platform": "tiktok", "accountId": "es1",
             "commentCount": 3},
            {"id": "p2", "platform": "tiktok", "accountId": "",
             "commentCount": 1},
            {"id": "p3", "platform": "tiktok", "accountId": "en1",
             "commentCount": 2},
        ]
        out = repro.check_h2(rows, "es1")
        self.assertEqual(out["total"], 3)
        self.assertEqual(out["de_esta_cuenta"], 1)
        self.assertEqual(out["sin_account_id"], 1)
        self.assertEqual(len(out["de_otra_cuenta"]), 1)
        self.assertEqual(out["de_otra_cuenta"][0]["accountId"], "en1")

    def test_check_h3_recognises_both_handles(self):
        out = repro.check_h3(["elclipdegta6____bits"],
                             ["elclipdegta6____bits",
                              "elclipdegta6en___"])
        self.assertTrue(out["elclipdegta6____bits"])
        self.assertFalse(out["elclipdegta6en___"])

    def test_reproduce_never_logs_keys(self):
        class _Bridge:
            tenant = "sebastian"

            def brain_credentials(self):
                return {"zernio": {
                    "es": {"api_key": "SECRET_ES"},
                    "en": {"api_key": "SECRET_EN"}}}

        def fake_get(key, path):
            if path == "/accounts":
                if key == "SECRET_ES":
                    return ACCOUNTS_ES
                return {"accounts": [
                    {"_id": "en1", "platform": "tiktok",
                     "username": "@elclipdegta6en___"},
                    {"_id": "en2", "platform": "tiktok",
                     "username": "@elclipdegta6____bits"}]}
            return {"comments": [
                {"id": "p1", "platform": "tiktok",
                 "accountId": "es1", "commentCount": 1}]}

        with mock.patch.object(repro, "_zernio_get",
                               side_effect=fake_get):
            report = repro.reproduce("sebastian",
                                     ["elclipdegta6____bits",
                                      "elclipdegta6en___"],
                                     bridge=_Bridge())
        text = json.dumps(report)
        self.assertNotIn("SECRET_ES", text)
        self.assertNotIn("SECRET_EN", text)
        self.assertEqual(
            report["por_idioma"]["es"]["by_platform"]["tiktok"]["id"],
            "es1")
        # H1 reproducida en EN: la clave EN lista las dos cuentas
        # TikTok y byPlatform coge la primera (la EN).
        self.assertTrue(report["por_idioma"]["en"]["h1_reproducida"])
        self.assertEqual(
            report["por_idioma"]["en"]["by_platform"]["tiktok"]["id"],
            "en1")
        self.assertTrue(
            report["por_idioma"]["en"]["h3"]["elclipdegta6en___"])


if __name__ == "__main__":
    unittest.main(verbosity=2)

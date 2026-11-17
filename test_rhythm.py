#!/usr/bin/env python3
"""Pruebas de 6.5: ritmo (topes, espaciamiento, franja, cuentas nuevas).
"""
import copy
import json
import os
import sys
import tempfile
import unittest
from datetime import date
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from clipagent import accounts
from clipagent.config import DEFAULT_CONFIG
from clipagent.publish import PublishQueue, publish_one


def cfg_full():
    c = copy.deepcopy(DEFAULT_CONFIG)
    c["autopublish"]["mode"] = "full"
    return c


class TestNewAccount(unittest.TestCase):
    def test_cuenta_de_ayer_es_nueva(self):
        with tempfile.TemporaryDirectory() as tmp:
            with open(os.path.join(tmp, "accounts.yaml"), "w",
                      encoding="utf-8") as f:
                f.write("youtube_shorts:\n"
                        '  created_at: "2026-11-16"\n')
            self.assertTrue(accounts.is_new_account(
                "youtube_shorts", tmp, today=date(2026, 11, 17)))
            self.assertEqual(accounts.account_age_days(
                "youtube_shorts", tmp, today=date(2026, 11, 17)), 1)

    def test_cuenta_de_hace_8_dias_ya_no_es_nueva(self):
        with tempfile.TemporaryDirectory() as tmp:
            with open(os.path.join(tmp, "accounts.yaml"), "w",
                      encoding="utf-8") as f:
                f.write("youtube_shorts:\n"
                        '  created_at: "2026-11-09"\n')
            self.assertFalse(accounts.is_new_account(
                "youtube_shorts", tmp, today=date(2026, 11, 17)))

    def test_sin_fecha_es_cuenta_antigua(self):
        with tempfile.TemporaryDirectory() as tmp:
            with open(os.path.join(tmp, "accounts.yaml"), "w",
                      encoding="utf-8") as f:
                f.write("youtube_shorts:\n  handle: '@x'\n")
            self.assertFalse(accounts.is_new_account(
                "youtube_shorts", tmp, today=date(2026, 11, 17)))

    def test_cuentas_reales_son_nuevas_esta_semana(self):
        # Las 4 cuentas ES se crearon el 2026-11-16: hoy (17 nov)
        # están en su primera semana -> ritmo reducido.
        self.assertTrue(accounts.is_new_account(
            "youtube_shorts", today=date(2026, 11, 17)))
        self.assertTrue(accounts.is_new_account(
            "tiktok", today=date(2026, 11, 17)))


class TestPublishOneLimits(unittest.TestCase):
    def _setup(self, tmp):
        run_dir = os.path.join(tmp, "runs", "v1")
        clips_dir = os.path.join(run_dir, "clips")
        os.makedirs(clips_dir, exist_ok=True)
        clip_path = os.path.join(clips_dir, "c1.mp4")
        with open(clip_path, "wb") as f:
            f.write(b"x")
        manifest = {"vod_id": "v1", "seo_overrides": [],
                    "clips": [{"peak": 100, "start": 80,
                               "file": "clips/c1.mp4",
                               "viral_score": {"total": 80},
                               "seo_scores": {
                                   "youtube_shorts": {"score": 95}},
                               "viral": {"viral_multiple": 8.0},
                               "title_es": "t", "desc_es": "d",
                               "hashtags_es": "#GTA6", "visible": True}]}
        with open(os.path.join(run_dir, "manifest.json"), "w",
                  encoding="utf-8") as f:
            json.dump(manifest, f)
        q = PublishQueue(os.path.join(tmp, "pq.json"))
        item = q.enqueue(clip_path, "youtube_shorts", vod_id="v1",
                         moment_key=["v1", 8])
        return q, item

    def test_cuenta_nueva_con_1_hoy_queda_limitada(self):
        with tempfile.TemporaryDirectory() as tmp:
            q, item = self._setup(tmp)
            # Un clip ya publicado hoy en YouTube + cuenta nueva.
            other = q.enqueue(os.path.join(tmp, "otro.mp4"),
                              "youtube_shorts")
            q.mark_published(other, "abc")
            with mock.patch("clipagent.publish.check_limits",
                           return_value=(True, "ok")):
                r = publish_one(q, item, cfg=cfg_full(), live=True)
            self.assertEqual(r["action"], "limited")
            self.assertIn("cuenta nueva", r["reason"])
            self.assertEqual(q._find(item["clip"],
                                     "youtube_shorts")["state"], "queued")

    def test_tope_diario_deja_el_item_en_cola(self):
        with tempfile.TemporaryDirectory() as tmp:
            q, item = self._setup(tmp)
            with mock.patch("clipagent.accounts.is_new_account",
                           return_value=False), \
                    mock.patch("clipagent.publish.check_limits",
                               return_value=(False, "tope diario")):
                r = publish_one(q, item, cfg=cfg_full(), live=True)
            self.assertEqual(r["action"], "limited")
            self.assertIn("tope diario", r["reason"])
            self.assertEqual(q._find(item["clip"],
                                     "youtube_shorts")["state"], "queued")


if __name__ == "__main__":
    unittest.main(verbosity=2)

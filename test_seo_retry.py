#!/usr/bin/env python3
"""Pruebas de 6B.4: auto-mejora del SEO (70-89) con el LLM antes
de descartar; sin LLM, el clip queda retenido.
"""
import copy
import datetime
import json
import os
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from clipagent.config import DEFAULT_CONFIG
from clipagent.llm import LLMClient, NullLLMClient, make_seo_variant
from clipagent.publish import PublishQueue, publish_one

DESC = ("IlloJuan juega a GTA 6 y pasa esto en directo. El mejor clip "
        "de GTA 6 de hoy, no te lo pierdas. Suscríbete para más clips "
        "de GTA 6.")
TAGS = "#GTA6 #GTA6Online #Rockstar #Gaming #Clips"
TITLE_OK = "GTA 6: IlloJuan y el momento más loco del directo"


class FakeLLM(LLMClient):
    """LLM de pruebas: responde según el bloque que se le pide."""

    def available(self):
        return True

    def generate(self, prompt):
        if "hashtags" in prompt:
            return "#GTA6 #GTA6Online #Rockstar #Gaming #Clips"
        if "descripción" in prompt:
            return DESC
        if "miniatura" in prompt:
            return "¡QUÉ MOMENTO!"
        return TITLE_OK


class SilentLLM(LLMClient):
    """LLM que no responde nada útil."""

    def available(self):
        return True

    def generate(self, prompt):
        return ""


class TestMakeSeoVariant(unittest.TestCase):
    def test_cambia_solo_el_bloque_mas_debil(self):
        variant = make_seo_variant(FakeLLM())
        pkg = {"title": "t", "description": DESC, "tags": [],
               "thumbnail_text": None, "hashtags": TAGS.split(),
               "game_verified": True, "visible": True, "lang": "es"}
        out = variant(pkg, "title")
        self.assertEqual(out["title"], TITLE_OK)
        self.assertEqual(out["description"], DESC)
        self.assertEqual(out["hashtags"], TAGS.split())

    def test_sin_respuesta_devuelve_none(self):
        variant = make_seo_variant(SilentLLM())
        pkg = {"title": "t", "description": DESC}
        self.assertIsNone(variant(pkg, "title"))

    def test_sin_llm_devuelve_none(self):
        variant = make_seo_variant(NullLLMClient())
        self.assertIsNone(variant({"title": "t"}, "title"))


def _setup(tmp):
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
                           "seo_scores": {"youtube_shorts": {"score": 85}},
                           "title_es": "t", "desc_es": DESC,
                           "hashtags_es": TAGS, "visible": True,
                           "viral": {"viral_multiple": 8.0,
                                     "platforms": {"youtube_shorts": {
                                         "title": "t",
                                         "caption": DESC,
                                         "tags": ["GTA6"]}}}}]}
    with open(os.path.join(run_dir, "manifest.json"), "w",
              encoding="utf-8") as f:
        json.dump(manifest, f)
    q = PublishQueue(os.path.join(tmp, "pq.json"))
    item = q.enqueue(clip_path, "youtube_shorts", vod_id="v1",
                     moment_key=["v1", 8], game="GTA 6")
    cfg = copy.deepcopy(DEFAULT_CONFIG)
    cfg["autopublish"]["mode"] = "full"
    return q, item, cfg, run_dir


def _patches(now):
    return [mock.patch("clipagent.publish._now_chile",
                       return_value=now),
            mock.patch("clipagent.publish.check_limits",
                       return_value=(True, "ok")),
            mock.patch("clipagent.accounts.is_new_account",
                       return_value=False)]


class TestSeoRetryPublish(unittest.TestCase):
    def test_con_llm_llega_al_gate_y_se_publica(self):
        with tempfile.TemporaryDirectory() as tmp:
            q, item, cfg, run_dir = _setup(tmp)
            fake_pub = mock.Mock()
            fake_pub.publish.return_value = {
                "ok": True, "video_id": "xyz",
                "url": "https://www.youtube.com/shorts/xyz"}
            now = datetime.datetime(2026, 11, 17, 20, 0,
                                    tzinfo=datetime.timezone(
                                        datetime.timedelta(hours=-3)))
            patches = _patches(now) + [
                mock.patch("clipagent.llm.get_llm_client",
                           return_value=FakeLLM()),
                mock.patch("clipagent.publish.get_publisher",
                           return_value=fake_pub)]
            for p in patches:
                p.start()
                self.addCleanup(p.stop)
            r = publish_one(q, item, cfg=cfg, live=True)
            self.assertEqual(r["action"], "published", r)
            with open(os.path.join(run_dir, "manifest.json"),
                      encoding="utf-8") as f:
                manifest = json.load(f)
            clip = manifest["clips"][0]
            # El manifiesto guarda el paquete mejorado, no el viejo.
            self.assertEqual(clip["title_es"], TITLE_OK)
            self.assertGreaterEqual(
                clip["seo_scores"]["youtube_shorts"]["score"], 90)
            self.assertEqual(
                clip["viral"]["platforms"]["youtube_shorts"]["title"],
                TITLE_OK)

    def test_llm_silencioso_queda_retenido(self):
        with tempfile.TemporaryDirectory() as tmp:
            q, item, cfg, run_dir = _setup(tmp)
            now = datetime.datetime(2026, 11, 17, 20, 0,
                                    tzinfo=datetime.timezone(
                                        datetime.timedelta(hours=-3)))
            patches = _patches(now) + [
                mock.patch("clipagent.llm.get_llm_client",
                           return_value=SilentLLM())]
            for p in patches:
                p.start()
                self.addCleanup(p.stop)
            r = publish_one(q, item, cfg=cfg, live=True)
            self.assertEqual(r["action"], "retained")
            self.assertIn("SEO 85", r["reason"])

    def test_sin_llm_queda_retenido(self):
        with tempfile.TemporaryDirectory() as tmp:
            q, item, cfg, run_dir = _setup(tmp)
            now = datetime.datetime(2026, 11, 17, 20, 0,
                                    tzinfo=datetime.timezone(
                                        datetime.timedelta(hours=-3)))
            patches = _patches(now) + [
                mock.patch("clipagent.llm.get_llm_client",
                           return_value=NullLLMClient())]
            for p in patches:
                p.start()
                self.addCleanup(p.stop)
            r = publish_one(q, item, cfg=cfg, live=True)
            self.assertEqual(r["action"], "retained")


if __name__ == "__main__":
    unittest.main(verbosity=2)

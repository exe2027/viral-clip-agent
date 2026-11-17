#!/usr/bin/env python3
"""Pruebas del fan-out: publicación directa en TikTok, Instagram y
Facebook (5a) y propagación desde YouTube (5b).

Los adaptadores se prueban con APIs simuladas en sus propios
archivos (test_tiktok_direct, test_facebook_upload,
test_instagram_publish); aquí se prueba el cableado: DirectPublisher
+ publish_one + publish_loop, con los adaptadores simulados.
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
from clipagent.publish import PublishQueue, publish_one

CHILE = datetime.timezone(datetime.timedelta(hours=-3))
NOW = datetime.datetime(2026, 11, 17, 20, 0, tzinfo=CHILE)


def _manifest(tmp, clips):
    """runs/v1 con clips, puntajes y paquete por plataforma."""
    run_dir = os.path.join(tmp, "runs", "v1")
    clips_dir = os.path.join(run_dir, "clips")
    os.makedirs(clips_dir, exist_ok=True)
    entries = []
    for c in clips:
        path = os.path.join(clips_dir, c["file"])
        with open(path, "wb") as f:
            f.write(b"x")
        platforms_pkg = {}
        for p in ("youtube_shorts", "tiktok", "instagram_reels",
                  "facebook_reels"):
            platforms_pkg[p] = {
                "title": c.get("title", "título"),
                "caption": c.get("caption", "caption #GTA6"),
                "tags": ["GTA6"]}
        entries.append({
            "peak": c.get("peak", 100), "start": c.get("start", 80),
            "file": f"clips/{c['file']}",
            "viral_score": {"total": c["virality"]},
            "seo_scores": {p: {"score": s}
                           for p, s in c["seo"].items()},
            "viral": {"viral_multiple": c.get("chat", 8.0),
                      "platforms": platforms_pkg},
            "title_es": c.get("title", "título"),
            "desc_es": c.get("caption", "caption #GTA6"),
            "hashtags_es": "#GTA6", "visible": True})
    with open(os.path.join(run_dir, "manifest.json"), "w",
              encoding="utf-8") as f:
        json.dump({"vod_id": "v1", "streamer": "illojuan",
                   "seo_overrides": [], "clips": entries}, f)
    return clips_dir


def _clip(tmp, platform="tiktok", virality=80, seo=95):
    clips = [{"file": "c1.mp4", "virality": virality,
              "seo": {platform: seo,
                      "youtube_shorts": 95, "tiktok": 95,
                      "instagram_reels": 95, "facebook_reels": 95}}]
    clips_dir = _manifest(tmp, clips)
    q = PublishQueue(os.path.join(tmp, "pq.json"))
    item = q.enqueue(os.path.join(clips_dir, "c1.mp4"), platform,
                     streamer="illojuan", vod_id="v1",
                     moment_key=["v1", 8])
    cfg = copy.deepcopy(DEFAULT_CONFIG)
    cfg["autopublish"]["mode"] = "full"
    return q, item, cfg


def _patches():
    """Parches comunes: lista blanca, topes, cuenta no nueva, hora
    y paquete manual (no escribe en el repo en los tests)."""
    return [
        mock.patch("clipagent.publish.check_whitelist",
                   return_value=(True, "en lista blanca")),
        mock.patch("clipagent.publish.check_limits",
                   return_value=(True, "ok")),
        mock.patch("clipagent.accounts.is_new_account",
                   return_value=False),
        mock.patch("clipagent.publish._now_chile", return_value=NOW),
        mock.patch("clipagent.publish.DirectPublisher.publish_draft",
                   return_value={"ok": True,
                                 "draft_dir": "drafts/c1_X"}),
    ]


def _fake_adapter(**methods):
    """Adapter falso con los métodos dados."""
    ad = mock.Mock()
    for name, value in methods.items():
        getattr(ad, name).return_value = value
    return ad


class TestDirectPublisher(unittest.TestCase):
    def test_tiktok_publica_por_api(self):
        with tempfile.TemporaryDirectory() as tmp:
            q, item, cfg = _clip(tmp, "tiktok")
            fake = _fake_adapter(publish_video={
                "ok": True, "mode": "api", "post_id": "tt1"})
            with mock.patch("clipagent.platforms.TikTokAdapter",
                           return_value=fake):
                for p in _patches():
                    p.start()
                    self.addCleanup(p.stop)
                r = publish_one(q, item, cfg=cfg, live=True)
            self.assertEqual(r["action"], "published", r)
            self.assertEqual(item["platform_id"], "tt1")

    def test_tiktok_sin_token_queda_en_draft_con_motivo(self):
        with tempfile.TemporaryDirectory() as tmp:
            q, item, cfg = _clip(tmp, "tiktok")
            fake = _fake_adapter(publish_video={
                "ok": False, "mode": "draft",
                "reason": "sin token de TikTok en tokens/"})
            with mock.patch("clipagent.platforms.TikTokAdapter",
                           return_value=fake):
                for p in _patches():
                    p.start()
                    self.addCleanup(p.stop)
                r = publish_one(q, item, cfg=cfg, live=True)
            self.assertEqual(r["action"], "draft", r)
            self.assertEqual(item["state"], "draft")
            self.assertIn("sin token", r["reason"])
            # El paquete manual se genera igual (simulado en tests).
            self.assertIn("paquete manual en", r["reason"])

    def test_instagram_sin_url_publica_queda_en_draft(self):
        with tempfile.TemporaryDirectory() as tmp:
            q, item, cfg = _clip(tmp, "instagram_reels")
            cfg["platform_accounts"]["instagram_reels"] = {
                "ig_user_id": "1784"}
            with mock.patch(
                    "clipagent.public_url.get_public_url",
                    return_value=(None, "sin URL pública configurada: "
                                        "configura public_url.base_url")):
                for p in _patches():
                    p.start()
                    self.addCleanup(p.stop)
                r = publish_one(q, item, cfg=cfg, live=True)
            self.assertEqual(r["action"], "draft", r)
            self.assertIn("sin URL pública configurada", r["reason"])

    def test_instagram_publica_con_media_id(self):
        with tempfile.TemporaryDirectory() as tmp:
            q, item, cfg = _clip(tmp, "instagram_reels")
            cfg["platform_accounts"]["instagram_reels"] = {
                "ig_user_id": "1784"}
            fake = _fake_adapter(publish_reel={
                "ok": True, "mode": "api", "media_id": "ig9"})
            with mock.patch("clipagent.platforms.InstagramAdapter",
                           return_value=fake), \
                    mock.patch("clipagent.public_url.get_public_url",
                               return_value=("https://x/c1.mp4", None)):
                for p in _patches():
                    p.start()
                    self.addCleanup(p.stop)
                r = publish_one(q, item, cfg=cfg, live=True)
            self.assertEqual(r["action"], "published", r)
            self.assertEqual(item["platform_id"], "ig9")

    def test_facebook_sin_page_id_queda_en_draft(self):
        with tempfile.TemporaryDirectory() as tmp:
            q, item, cfg = _clip(tmp, "facebook_reels")
            for p in _patches():
                p.start()
                self.addCleanup(p.stop)
            r = publish_one(q, item, cfg=cfg, live=True)
            self.assertEqual(r["action"], "draft", r)
            self.assertIn("page_id", r["reason"])

    def test_facebook_publica_por_api(self):
        with tempfile.TemporaryDirectory() as tmp:
            q, item, cfg = _clip(tmp, "facebook_reels")
            cfg["platform_accounts"]["facebook_reels"] = {
                "page_id": "555"}
            fake = _fake_adapter(publish_reel={
                "ok": True, "mode": "api", "post_id": "fb1"})
            with mock.patch("clipagent.platforms.FacebookAdapter",
                           return_value=fake):
                for p in _patches():
                    p.start()
                    self.addCleanup(p.stop)
                r = publish_one(q, item, cfg=cfg, live=True)
            self.assertEqual(r["action"], "published", r)
            self.assertEqual(item["platform_id"], "fb1")


class TestReintentoTransitorio(unittest.TestCase):
    def _setup_error(self, tmp):
        q, item, cfg = _clip(tmp, "tiktok")
        fake = _fake_adapter(publish_video={
            "ok": False, "mode": "error", "status": 503,
            "reason": "TikTok caído (503)", "retryable": True})
        return q, item, cfg, fake

    def test_5xx_se_reintenta_sin_cambiar_el_estado(self):
        with tempfile.TemporaryDirectory() as tmp:
            q, item, cfg, fake = self._setup_error(tmp)
            with mock.patch("clipagent.platforms.TikTokAdapter",
                           return_value=fake):
                for p in _patches():
                    p.start()
                    self.addCleanup(p.stop)
                r = publish_one(q, item, cfg=cfg, live=True)
            self.assertEqual(r["action"], "failed", r)
            # El ítem sigue en la cola (queued), no en failed.
            self.assertEqual(item["state"], "queued")
            self.assertIn("fallo transitorio (intento 1/3)",
                          item["history"][-1]["note"])

    def test_tres_transitorios_y_el_cuarto_va_a_failed(self):
        with tempfile.TemporaryDirectory() as tmp:
            q, item, cfg, fake = self._setup_error(tmp)
            with mock.patch("clipagent.platforms.TikTokAdapter",
                           return_value=fake):
                for p in _patches():
                    p.start()
                    self.addCleanup(p.stop)
                for _ in range(3):
                    r = publish_one(q, item, cfg=cfg, live=True)
                    self.assertEqual(r["action"], "failed", r)
                    self.assertEqual(item["state"], "queued")
                r = publish_one(q, item, cfg=cfg, live=True)
            self.assertEqual(item["state"], "failed")


class TestProcessing(unittest.TestCase):
    def test_processing_guarda_publish_id_y_retoma(self):
        with tempfile.TemporaryDirectory() as tmp:
            q, item, cfg = _clip(tmp, "tiktok")
            calls = {"n": 0}

            def video(*a, **k):
                calls["n"] += 1
                if calls["n"] == 1:
                    return {"ok": False, "mode": "processing",
                            "publish_id": "pub1", "retryable": True,
                            "reason": "TikTok sigue procesando"}
                return {"ok": True, "mode": "api", "post_id": "tt1"}
            fake = mock.Mock()
            fake.publish_video.side_effect = video
            with mock.patch("clipagent.platforms.TikTokAdapter",
                           return_value=fake):
                for p in _patches():
                    p.start()
                    self.addCleanup(p.stop)
                r1 = publish_one(q, item, cfg=cfg, live=True)
                self.assertEqual(r1["action"], "processing", r1)
                self.assertEqual(item["state"], "queued")
                self.assertEqual(
                    item["platform_data"]["publish_id"], "pub1")
                r2 = publish_one(q, item, cfg=cfg, live=True)
            self.assertEqual(r2["action"], "published", r2)
            self.assertEqual(item["platform_id"], "tt1")
            # El resume pasó el publish_id en la segunda llamada.
            self.assertEqual(
                fake.publish_video.call_args[1]["resume_publish_id"],
                "pub1")


if __name__ == "__main__":
    unittest.main()

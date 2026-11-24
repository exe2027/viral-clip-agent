#!/usr/bin/env python3
"""Pruebas del fan-out: publicación por el puente al panel
(P0 #8, #64) y propagación desde YouTube (5b).

Los adaptadores se prueban con APIs simuladas en sus propios
archivos (test_zernio, test_facebook_upload,
test_instagram_publish); aquí se prueba el cableado: publish_one +
publish_loop con un puente falso al Worker (el único publicador).
La subida directa por adapters está retirada: publish_direct con
live=True llama a PanelBridge.publish_clip(panel_clip_id).
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
from clipagent.publish import (PublishQueue, publish_loop, publish_one)

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
            "category": "GTA VI",
            "viral_score": {"total": c["virality"]},
            "seo_scores": {p: {"score": s}
                           for p, s in c["seo"].items()},
            "viral": {"viral_multiple": c.get("chat", 8.0),
                      "platforms": platforms_pkg},
            "title_es": c.get("title", "título"),
            "desc_es": c.get("caption", "caption #GTA6"),
            "hashtags_es": "#GTA6", "visible": True,
            # Origen del clip (id de YouTube si lo es): lo lee
            # publish_one para resolver el streamer por plataforma.
            "source_key": c.get("source_key", "")})
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
                     streamer="rubius", vod_id="v1",
                     moment_key=["v1", 8], panel_clip_id=123)
    cfg = copy.deepcopy(DEFAULT_CONFIG)
    cfg["autopublish"]["mode"] = "full"
    return q, item, cfg


def _patches():
    """Parches comunes: lista blanca, topes, cuenta no nueva, hora,
    paquete manual (no escribe en el repo en los tests) y breaker
    (no toca circuit_breaker.json)."""
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
        mock.patch("clipagent.breaker.is_tripped",
                   return_value=(False, "")),
        mock.patch("clipagent.breaker.record_success",
                   return_value=None),
        mock.patch("clipagent.breaker.record_failure",
                   return_value={"tripped": False}),
    ]


def _fake_adapter(**methods):
    """Adapter falso con los métodos dados."""
    ad = mock.Mock()
    for name, value in methods.items():
        getattr(ad, name).return_value = value
    return ad


class _FakeBridge:
    """Puente falso al panel (P0 #8): publish_clip encola jobs
    en el Worker para las 4 plataformas, salvo las bloqueadas."""

    def __init__(self, blocked=None, fail_with=None):
        self.published = []
        self.blocked = blocked or {}
        self.fail_with = fail_with

    def publish_clip(self, clip_id):
        if self.fail_with is not None:
            raise self.fail_with
        self.published.append(clip_id)
        jobs = [{"id": 7, "platform": p}
                for p in ("youtube_shorts", "tiktok",
                          "instagram_reels", "facebook_reels")
                if p not in self.blocked]
        blocked = [f"{p}: {why}"
                   for p, why in self.blocked.items()]
        return {"jobs": jobs, "blocked": blocked}


class TestPublicaPorElPuente(unittest.TestCase):
    """P0 #8: publish_one con live=True publica solo vía el puente
    al Worker (PanelBridge.publish_clip). La subida directa por
    adapters está retirada; los adapters se prueban en sus propios
    archivos (test_zernio, test_facebook_upload,
    test_instagram_publish)."""

    def test_tiktok_publica_por_el_puente(self):
        with tempfile.TemporaryDirectory() as tmp:
            q, item, cfg = _clip(tmp, "tiktok")
            bridge = _FakeBridge()
            for p in _patches():
                p.start()
                self.addCleanup(p.stop)
            r = publish_one(q, item, cfg=cfg, live=True, bridge=bridge)
            self.assertEqual(r["action"], "published", r)
            self.assertEqual(item["platform_id"], "worker-job-7")
            self.assertEqual(bridge.published, [123])

    def test_sin_clip_en_panel_queda_en_draft(self):
        with tempfile.TemporaryDirectory() as tmp:
            q, item, cfg = _clip(tmp, "tiktok")
            item["panel_clip_id"] = None
            for p in _patches():
                p.start()
                self.addCleanup(p.stop)
            r = publish_one(q, item, cfg=cfg, live=True,
                            bridge=_FakeBridge())
            self.assertEqual(r["action"], "draft", r)
            self.assertEqual(item["state"], "draft")
            self.assertIn("panel_clip_id", r["reason"])

    def test_sin_puente_queda_en_draft(self):
        # Sin bridge inyectado ni credenciales en el entorno, no
        # se publica por adapters directos: a draft con motivo.
        with tempfile.TemporaryDirectory() as tmp:
            q, item, cfg = _clip(tmp, "tiktok")
            for p in _patches():
                p.start()
                self.addCleanup(p.stop)
            with mock.patch.dict(os.environ, {}, clear=True):
                r = publish_one(q, item, cfg=cfg, live=True)
            self.assertEqual(r["action"], "draft", r)
            self.assertIn("sin puente", r["reason"])

    def test_instagram_publica_por_el_puente(self):
        with tempfile.TemporaryDirectory() as tmp:
            q, item, cfg = _clip(tmp, "instagram_reels")
            bridge = _FakeBridge()
            for p in _patches():
                p.start()
                self.addCleanup(p.stop)
            r = publish_one(q, item, cfg=cfg, live=True, bridge=bridge)
            self.assertEqual(r["action"], "published", r)
            self.assertEqual(item["platform_id"], "worker-job-7")
            self.assertEqual(bridge.published, [123])

    def test_bloqueado_por_el_worker_queda_en_draft(self):
        with tempfile.TemporaryDirectory() as tmp:
            q, item, cfg = _clip(tmp, "facebook_reels")
            bridge = _FakeBridge(
                blocked={"facebook_reels": "tope diario"})
            for p in _patches():
                p.start()
                self.addCleanup(p.stop)
            r = publish_one(q, item, cfg=cfg, live=True, bridge=bridge)
            self.assertEqual(r["action"], "draft", r)
            self.assertIn("tope diario", r["reason"])

    def test_facebook_publica_por_el_puente(self):
        with tempfile.TemporaryDirectory() as tmp:
            q, item, cfg = _clip(tmp, "facebook_reels")
            bridge = _FakeBridge()
            for p in _patches():
                p.start()
                self.addCleanup(p.stop)
            r = publish_one(q, item, cfg=cfg, live=True, bridge=bridge)
            self.assertEqual(r["action"], "published", r)
            self.assertEqual(item["platform_id"], "worker-job-7")


class TestReintentoTransitorio(unittest.TestCase):
    """El puente falla (red/login): error reintentable, el ítem
    sigue en la cola y al 4º intento va a failed."""

    def test_5xx_se_reintenta_sin_cambiar_el_estado(self):
        with tempfile.TemporaryDirectory() as tmp:
            q, item, cfg = _clip(tmp, "tiktok")
            bridge = _FakeBridge(
                fail_with=RuntimeError("Worker caído (503)"))
            for p in _patches():
                p.start()
                self.addCleanup(p.stop)
            r = publish_one(q, item, cfg=cfg, live=True, bridge=bridge)
            self.assertEqual(r["action"], "failed", r)
            # El ítem sigue en la cola (queued), no en failed.
            self.assertEqual(item["state"], "queued")
            self.assertIn("fallo transitorio (intento 1/3)",
                          item["history"][-1]["note"])

    def test_tres_transitorios_y_el_cuarto_va_a_failed(self):
        with tempfile.TemporaryDirectory() as tmp:
            q, item, cfg = _clip(tmp, "tiktok")
            bridge = _FakeBridge(
                fail_with=RuntimeError("Worker caído (503)"))
            for p in _patches():
                p.start()
                self.addCleanup(p.stop)
            for _ in range(3):
                r = publish_one(q, item, cfg=cfg, live=True,
                                bridge=bridge)
                self.assertEqual(r["action"], "failed", r)
                self.assertEqual(item["state"], "queued")
            r = publish_one(q, item, cfg=cfg, live=True, bridge=bridge)
            self.assertEqual(item["state"], "failed")


class TestFanOutLoop(unittest.TestCase):
    def _setup_loop(self, tmp, blocked=None):
        """Cola con un ítem de YouTube + puente falso al Worker.

        P0 #8: todas las plataformas publican vía el puente
        (publish_clip); el fan-out propaga el panel_clip_id a
        los ítems nuevos para que también puedan publicar.
        """
        clips = [{"file": "c1.mp4", "virality": 80, "chat": 8.0,
                  "seo": {"youtube_shorts": 95, "tiktok": 95,
                          "instagram_reels": 95,
                          "facebook_reels": 95}}]
        clips_dir = _manifest(tmp, clips)
        q = PublishQueue(os.path.join(tmp, "pq.json"))
        yt_item = q.enqueue(os.path.join(clips_dir, "c1.mp4"),
                            "youtube_shorts", streamer="rubius",
                            vod_id="v1", moment_key=["v1", 8],
                            panel_clip_id=123)
        cfg = copy.deepcopy(DEFAULT_CONFIG)
        cfg["autopublish"]["mode"] = "full"
        bridge = _FakeBridge(blocked=blocked or {})
        return q, yt_item, cfg, _patches(), bridge

    def test_youtube_publica_y_las_otras_tres_tambien(self):
        with tempfile.TemporaryDirectory() as tmp:
            q, _yt, cfg, patches, bridge = self._setup_loop(tmp)
            notes = []
            for p in patches:
                p.start()
                self.addCleanup(p.stop)
            stats = publish_loop(
                q, live=True, once=True, cfg=cfg,
                metrics_path=os.path.join(tmp, "metrics.db"),
                notify_fn=lambda e, d: notes.append((e, d)),
                bridge=bridge)
            self.assertEqual(stats["published"], 4, stats)
            self.assertEqual(stats["fanout"], 3, stats)
            for platform in ("youtube_shorts", "tiktok",
                             "instagram_reels", "facebook_reels"):
                item = q._find(
                    os.path.join(tmp, "runs", "v1", "clips", "c1.mp4"),
                    platform)
                self.assertIsNotNone(item, platform)
                self.assertEqual(item["state"], "published", platform)
            self.assertTrue(any(e == "fan-out" for e, _ in notes))

    def test_fallo_en_una_no_bloquea_las_otras(self):
        # El Worker bloquea TikTok (tope diario): las otras 3
        # publican igual; el ítem de TikTok queda en draft.
        with tempfile.TemporaryDirectory() as tmp:
            q, _yt, cfg, patches, bridge = self._setup_loop(
                tmp, blocked={"tiktok": "tope diario"})
            for p in patches:
                p.start()
                self.addCleanup(p.stop)
            stats = publish_loop(
                q, live=True, once=True, cfg=cfg,
                metrics_path=os.path.join(tmp, "metrics.db"),
                bridge=bridge)
            self.assertEqual(stats["published"], 3, stats)
            self.assertEqual(stats["draft"], 1, stats)
            clip = os.path.join(tmp, "runs", "v1", "clips", "c1.mp4")
            self.assertEqual(
                q._find(clip, "tiktok")["state"], "draft")
            self.assertIn(
                "tope diario",
                q._find(clip, "tiktok")["history"][-1]["note"])
            self.assertEqual(
                q._find(clip, "instagram_reels")["state"], "published")
            self.assertEqual(
                q._find(clip, "facebook_reels")["state"], "published")

    def test_fanout_no_duplica_si_el_item_ya_existe(self):
        with tempfile.TemporaryDirectory() as tmp:
            q, _yt, cfg, patches, bridge = self._setup_loop(tmp)
            clip = os.path.join(tmp, "runs", "v1", "clips", "c1.mp4")
            q.enqueue(clip, "tiktok", streamer="rubius", vod_id="v1",
                      moment_key=["v1", 8], panel_clip_id=123)
            for p in patches:
                p.start()
                self.addCleanup(p.stop)
            stats = publish_loop(
                q, live=True, once=True, cfg=cfg,
                metrics_path=os.path.join(tmp, "metrics.db"),
                bridge=bridge)
            self.assertEqual(stats["published"], 4, stats)
            self.assertEqual(stats["fanout"], 2, stats)
            tk_items = [i for i in q.data["items"]
                        if i["platform"] == "tiktok"]
            self.assertEqual(len(tk_items), 1)

    def test_fanout_desactivado_por_config(self):
        with tempfile.TemporaryDirectory() as tmp:
            q, _yt, cfg, patches, bridge = self._setup_loop(tmp)
            cfg["fanout"]["enabled"] = False
            for p in patches:
                p.start()
                self.addCleanup(p.stop)
            stats = publish_loop(
                q, live=True, once=True, cfg=cfg,
                metrics_path=os.path.join(tmp, "metrics.db"),
                bridge=bridge)
            self.assertEqual(stats["published"], 1, stats)
            self.assertEqual(stats["fanout"], 0, stats)
            self.assertEqual(len(q.data["items"]), 1)

    def test_en_dry_run_no_hay_fanout(self):
        with tempfile.TemporaryDirectory() as tmp:
            q, _yt, cfg, patches, bridge = self._setup_loop(tmp)
            for p in patches:
                p.start()
                self.addCleanup(p.stop)
            stats = publish_loop(
                q, live=False, once=True, cfg=cfg,
                metrics_path=os.path.join(tmp, "metrics.db"),
                bridge=bridge)
            self.assertEqual(stats["published"], 0, stats)
            self.assertEqual(stats["fanout"], 0, stats)
            self.assertEqual(len(q.data["items"]), 1)


class TestPublishResuelveStreamerPorPlataforma(unittest.TestCase):
    """publish.py:1931 (2026-11-24, corte YouTube): con dos Rubius
    (twitch desactivado + youtube activo), el chequeo 6.2 resuelve
    por (nombre, plataforma de origen) vía el manifiesto
    (source_key = id de YouTube -> youtube, si no twitch)."""

    STREAMERS = [
        {"name": "rubius", "platform": "twitch", "language": "es",
         "enabled": False, "estado": "concedido"},
        {"name": "rubius", "platform": "youtube", "language": "es",
         "enabled": True, "estado": "concedido"},
    ]

    def _clip_src(self, tmp, source_key=None):
        clips = [{"file": "c1.mp4", "virality": 80,
                  "seo": {"tiktok": 95, "youtube_shorts": 95,
                          "instagram_reels": 95,
                          "facebook_reels": 95},
                  "source_key": source_key}]
        clips_dir = _manifest(tmp, clips)
        q = PublishQueue(os.path.join(tmp, "pq.json"))
        item = q.enqueue(os.path.join(clips_dir, "c1.mp4"), "tiktok",
                         streamer="rubius", vod_id="v1",
                         moment_key=["v1", 8], panel_clip_id=123)
        cfg = copy.deepcopy(DEFAULT_CONFIG)
        cfg["autopublish"]["mode"] = "full"
        return q, item, cfg

    def _run_one(self, tmp, source_key):
        q, item, cfg = self._clip_src(tmp, source_key)
        bridge = _FakeBridge()
        for p in _patches():
            p.start()
            self.addCleanup(p.stop)
        with mock.patch("clipagent.streamers.load_streamers",
                        return_value=self.STREAMERS):
            return publish_one(q, item, cfg=cfg, live=True,
                               bridge=bridge), bridge

    def test_clip_twitch_con_twitch_desactivado_retiene(self):
        with tempfile.TemporaryDirectory() as tmp:
            r, _bridge = self._run_one(tmp, None)
            self.assertEqual(r["action"], "retained", r)
            self.assertIn("desactivado", r["reason"])

    def test_clip_youtube_con_youtube_activo_publica(self):
        with tempfile.TemporaryDirectory() as tmp:
            r, bridge = self._run_one(tmp, "NrNdw_Y6_bI")
            self.assertEqual(r["action"], "published", r)
            self.assertEqual(bridge.published, [123])


if __name__ == "__main__":
    unittest.main()

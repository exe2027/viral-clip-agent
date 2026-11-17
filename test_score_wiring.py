#!/usr/bin/env python3
"""Pruebas de 6B: el puntaje manda sobre qué se publica y cuándo.

6B.1 (orden por prioridad, nunca FIFO), 6B.2 (filtros duros),
6B.8 (a-e) y 6B.9 (regresión Coker ~86 / IlloJuan ~61).
"""
import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from clipagent.priority import order_queue, priority, recalibrate
from clipagent.publish import PublishQueue


def _manifest(tmp, clips):
    """Crea runs/v1 con clips y un manifest.json con sus puntajes."""
    run_dir = os.path.join(tmp, "runs", "v1")
    clips_dir = os.path.join(run_dir, "clips")
    os.makedirs(clips_dir, exist_ok=True)
    entries = []
    for c in clips:
        path = os.path.join(clips_dir, c["file"])
        with open(path, "wb") as f:
            f.write(b"x")
        entries.append({
            "peak": c.get("peak", 100), "start": c.get("start", 80),
            "file": f"clips/{c['file']}",
            "viral_score": {"total": c["virality"]},
            "seo_scores": {p: {"score": s}
                           for p, s in c["seo"].items()},
            "viral": {"viral_multiple": c.get("chat", 8.0)},
            "title_es": c.get("title", "t"), "desc_es": "d",
            "hashtags_es": "#GTA6", "visible": True})
    with open(os.path.join(run_dir, "manifest.json"), "w",
              encoding="utf-8") as f:
        json.dump({"vod_id": "v1", "seo_overrides": [],
                   "clips": entries}, f)
    return clips_dir


def _enqueue(tmp, clips_dir, clips, platform):
    q = PublishQueue(os.path.join(tmp, "pq.json"))
    items = []
    for c in clips:
        items.append(q.enqueue(
            os.path.join(clips_dir, c["file"]), platform,
            vod_id="v1", moment_key=["v1", c.get("start", 80) // 10]))
    return q, items


class TestOrderQueue(unittest.TestCase):
    def test_mayor_prioridad_primero_aunque_llego_despues(self):
        # 6B.8(a): A (IlloJuan ~61) se encola antes que B (Coker ~86).
        with tempfile.TemporaryDirectory() as tmp:
            clips = [
                {"file": "a.mp4", "virality": 51, "chat": 6.44,
                 "seo": {"youtube_shorts": 84, "tiktok": 84}},
                {"file": "b.mp4", "virality": 78, "chat": 13.0,
                 "seo": {"youtube_shorts": 95, "tiktok": 95}},
            ]
            clips_dir = _manifest(tmp, clips)
            q, _ = _enqueue(tmp, clips_dir, clips, "youtube_shorts")
            due = q.due_items("youtube_shorts", states=("queued",))
            self.assertEqual([os.path.basename(i["clip"]) for i in due],
                             ["b.mp4", "a.mp4"])

    def test_seo_por_plataforma_da_orden_distinto(self):
        # 6B.8(d): mismo viral/chat; el SEO decide y cambia por
        # plataforma: en YT gana B (SEO 98), en TikTok gana A (98).
        with tempfile.TemporaryDirectory() as tmp:
            clips = [
                {"file": "a.mp4", "virality": 60, "chat": 8.0,
                 "seo": {"youtube_shorts": 70, "tiktok": 98}},
                {"file": "b.mp4", "virality": 60, "chat": 8.0,
                 "seo": {"youtube_shorts": 98, "tiktok": 70}},
            ]
            clips_dir = _manifest(tmp, clips)
            q, _ = _enqueue(tmp, clips_dir, clips, "youtube_shorts")
            q2 = PublishQueue(os.path.join(tmp, "pq2.json"))
            for c in clips:
                q2.enqueue(os.path.join(clips_dir, c["file"]), "tiktok",
                           vod_id="v1",
                           moment_key=["v1", 8])
            yt = [os.path.basename(i["clip"]) for i in
                  q.due_items("youtube_shorts", states=("queued",))]
            tk = [os.path.basename(i["clip"]) for i in
                  q2.due_items("tiktok", states=("queued",))]
            self.assertEqual(yt, ["b.mp4", "a.mp4"])
            self.assertEqual(tk, ["a.mp4", "b.mp4"])

    def test_sin_manifiesto_queda_al_final(self):
        with tempfile.TemporaryDirectory() as tmp:
            clips = [{"file": "a.mp4", "virality": 78, "chat": 13.0,
                      "seo": {"youtube_shorts": 95}}]
            clips_dir = _manifest(tmp, clips)
            q = PublishQueue(os.path.join(tmp, "pq.json"))
            q.enqueue(os.path.join(clips_dir, "a.mp4"), "youtube_shorts",
                      vod_id="v1", moment_key=["v1", 8])
            q.enqueue(os.path.join(tmp, "huerfano.mp4"), "youtube_shorts",
                      vod_id="v1", moment_key=["v1", 9])
            due = q.due_items("youtube_shorts", states=("queued",))
            self.assertEqual(os.path.basename(due[0]["clip"]), "a.mp4")
            self.assertEqual(os.path.basename(due[1]["clip"]),
                             "huerfano.mp4")

    def test_scored_items_trae_desglose(self):
        with tempfile.TemporaryDirectory() as tmp:
            clips = [{"file": "a.mp4", "virality": 78, "chat": 13.0,
                      "seo": {"youtube_shorts": 95}}]
            clips_dir = _manifest(tmp, clips)
            q, _ = _enqueue(tmp, clips_dir, clips, "youtube_shorts")
            scored = q.scored_items("youtube_shorts")
            self.assertEqual(len(scored), 1)
            self.assertAlmostEqual(scored[0]["priority"]["priority"],
                                   85.7, places=1)
            self.assertEqual(scored[0]["status"], "publicable")
            self.assertEqual(scored[0]["queue_position"], 1)


class TestRegressionExamples(unittest.TestCase):
    def test_coker_86_illojuan_61(self):
        # 6B.9: los ejemplos de referencia no cambian.
        self.assertAlmostEqual(priority(78, 95, 13)["priority"],
                               86, delta=1.0)
        self.assertAlmostEqual(priority(51, 84, 6.44)["priority"],
                               61, delta=1.0)
        ordered = order_queue([
            {"virality": 51, "seo": 84, "chat_multiple": 6.44,
             "vod_id": "v1", "start_sec": 100},
            {"virality": 78, "seo": 95, "chat_multiple": 13,
             "vod_id": "v1", "start_sec": 200},
        ], "youtube_shorts")
        self.assertGreater(ordered[0]["priority"]["priority"],
                           ordered[1]["priority"]["priority"])
        self.assertEqual(ordered[0]["virality"], 78)

    def test_recalibrate_no_pasa_del_25_porciento(self):
        # 6B.8(f): aunque la correlación sea perfecta, cada peso se
        # mueve como máximo ±25% por ciclo.
        records = [{"virality": v, "seo": s, "chat_multiple": c,
                    "views": v * 100 + s * 10}
                   for v, s, c in
                   [(90, 95, 12), (80, 90, 10), (70, 85, 8),
                    (60, 80, 6), (50, 70, 5)] * 6]
        new, report = recalibrate(records)
        self.assertEqual(report["status"], "recalibrated")
        for key, old in {"viral": 0.5, "seo": 0.3,
                         "chat": 0.2}.items():
            self.assertLessEqual(abs(new[key] - old), 0.25 * old + 1e-9)


class TestHardFilters(unittest.TestCase):
    """6B.8(b)(c)(e): los filtros duros se aplican de verdad."""

    def _setup(self, tmp, virality, seo, start=80):
        import copy
        import datetime
        from unittest import mock
        from clipagent.config import DEFAULT_CONFIG
        clips = [{"file": "c1.mp4", "virality": virality, "chat": 8.0,
                  "seo": {"youtube_shorts": seo}, "start": start}]
        clips_dir = _manifest(tmp, clips)
        q = PublishQueue(os.path.join(tmp, "pq.json"))
        item = q.enqueue(os.path.join(clips_dir, "c1.mp4"),
                         "youtube_shorts", vod_id="v1",
                         moment_key=["v1", start // 10])
        cfg = copy.deepcopy(DEFAULT_CONFIG)
        cfg["autopublish"]["mode"] = "full"
        self.now = datetime.datetime(2026, 11, 17, 20, 0,
                                     tzinfo=datetime.timezone(
                                         datetime.timedelta(hours=-3)))
        self.patches = [
            mock.patch("clipagent.publish._now_chile",
                       return_value=self.now),
            mock.patch("clipagent.publish.check_limits",
                       return_value=(True, "ok")),
            mock.patch("clipagent.accounts.is_new_account",
                       return_value=False),
        ]
        for p in self.patches:
            p.start()
            self.addCleanup(p.stop)
        return q, item, cfg

    def test_viralidad_49_no_se_publica(self):
        # 6B.8(b)
        from clipagent.publish import publish_one
        with tempfile.TemporaryDirectory() as tmp:
            q, item, cfg = self._setup(tmp, 49, 95)
            r = publish_one(q, item, cfg=cfg, live=True)
            self.assertEqual(r["action"], "discarded")
            self.assertIn("viralidad 49", r["reason"])

    def test_seo_89_sin_override_no_se_publica(self):
        # 6B.8(c): sin LLM no hay reintento: queda retenido.
        from clipagent.publish import publish_one
        with tempfile.TemporaryDirectory() as tmp:
            q, item, cfg = self._setup(tmp, 80, 89)
            r = publish_one(q, item, cfg=cfg, live=True)
            self.assertEqual(r["action"], "retained")
            self.assertIn("SEO 89", r["reason"])

    def test_seo_69_es_bloqueo_duro(self):
        from clipagent.publish import publish_one
        with tempfile.TemporaryDirectory() as tmp:
            q, item, cfg = self._setup(tmp, 80, 69)
            r = publish_one(q, item, cfg=cfg, live=True)
            self.assertEqual(r["action"], "discarded")
            self.assertIn("bloqueo duro", r["reason"])

    def test_mismo_momento_no_se_publica_dos_veces_el_mismo_dia(self):
        # 6B.8(e): dos clips del mismo momento, misma plataforma,
        # mismo día: solo el primero se publica.
        import datetime
        from unittest import mock
        from clipagent.publish import publish_one
        with tempfile.TemporaryDirectory() as tmp:
            clips = [
                {"file": "c1.mp4", "virality": 80, "chat": 8.0,
                 "seo": {"youtube_shorts": 95}, "start": 80},
                {"file": "c2.mp4", "virality": 75, "chat": 8.0,
                 "seo": {"youtube_shorts": 95}, "start": 85},
            ]
            clips_dir = _manifest(tmp, clips)
            q = PublishQueue(os.path.join(tmp, "pq.json"))
            items = []
            for c in clips:
                items.append(q.enqueue(
                    os.path.join(clips_dir, c["file"]), "youtube_shorts",
                    vod_id="v1", moment_key=["v1", 8]))
            import copy
            from clipagent.config import DEFAULT_CONFIG
            cfg = copy.deepcopy(DEFAULT_CONFIG)
            cfg["autopublish"]["mode"] = "full"
            fake_pub = mock.Mock()
            fake_pub.publish.return_value = {
                "ok": True, "video_id": "xyz",
                "url": "https://www.youtube.com/shorts/xyz"}
            now = datetime.datetime(2026, 11, 17, 20, 0,
                                    tzinfo=datetime.timezone(
                                        datetime.timedelta(hours=-3)))
            with mock.patch("clipagent.publish._now_chile",
                           return_value=now), \
                    mock.patch("clipagent.publish.get_publisher",
                               return_value=fake_pub), \
                    mock.patch("clipagent.publish.check_limits",
                               return_value=(True, "ok")), \
                    mock.patch("clipagent.accounts.is_new_account",
                               return_value=False):
                r1 = publish_one(q, items[0], cfg=cfg, live=True)
                r2 = publish_one(q, items[1], cfg=cfg, live=True)
            self.assertEqual(r1["action"], "published")
            self.assertEqual(r2["action"], "retained")
            self.assertIn("mismo momento", r2["reason"])

    def test_clip_viejo_de_prioridad_baja_se_archiva_solo(self):
        # 6B.2: 4 días en cola, prioridad < 70 -> archivado solo.
        import datetime
        from unittest import mock
        from clipagent.publish import publish_loop
        with tempfile.TemporaryDirectory() as tmp:
            clips = [{"file": "c1.mp4", "virality": 51, "chat": 6.44,
                      "seo": {"youtube_shorts": 84}}]
            clips_dir = _manifest(tmp, clips)
            q = PublishQueue(os.path.join(tmp, "pq.json"))
            item = q.enqueue(os.path.join(clips_dir, "c1.mp4"),
                             "youtube_shorts", vod_id="v1",
                             moment_key=["v1", 8])
            # El ítem entró hace 4 días.
            old = (datetime.datetime(2026, 11, 13, 20, 0,
                                     tzinfo=datetime.timezone(
                                         datetime.timedelta(hours=-3)))
                   .isoformat())
            item["history"][0]["at"] = old
            q.save()
            import copy
            from clipagent.config import DEFAULT_CONFIG
            cfg = copy.deepcopy(DEFAULT_CONFIG)
            cfg["autopublish"]["mode"] = "full"
            now = datetime.datetime(2026, 11, 17, 20, 0,
                                    tzinfo=datetime.timezone(
                                        datetime.timedelta(hours=-3)))
            with mock.patch("clipagent.publish._now_chile",
                           return_value=now):
                stats = publish_loop(q, live=False, once=True, cfg=cfg)
            self.assertEqual(stats["archived"], 1)
            self.assertEqual(q._find(item["clip"],
                                     "youtube_shorts")["state"],
                             "archived")


if __name__ == "__main__":
    unittest.main(verbosity=2)

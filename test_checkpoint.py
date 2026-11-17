#!/usr/bin/env python3
"""Pruebas de 5.5: checkpoint por job y reanudación en auto_clips.py.
"""
import json
import os
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from clipagent import checkpoint as cp
from test_auto_clips_filter import _chat_1_3, _video


class TestCheckpointModule(unittest.TestCase):
    def test_roundtrip_y_etapas(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(cp.load_state(tmp), {})
            self.assertIsNone(cp.load_manifest(tmp))
            state = {}
            cp.mark_stage(state, "chat")
            cp.mark_clip_done(state, 40, "clip_01_t00m40s.mp4")
            cp.save_state(tmp, state)
            loaded = cp.load_state(tmp)
            self.assertTrue(cp.stage_done(loaded, "chat"))
            self.assertTrue(cp.clip_done(loaded, 40))
            self.assertFalse(cp.clip_done(loaded, 90))
            self.assertEqual(loaded["clips"]["peak_40"]["file"],
                             "clips/clip_01_t00m40s.mp4")

    def test_estado_corrupto_no_rompe(self):
        with tempfile.TemporaryDirectory() as tmp:
            with open(os.path.join(tmp, "state.json"), "w",
                      encoding="utf-8") as f:
                f.write("{no json")
            self.assertEqual(cp.load_state(tmp), {})
            self.assertIsNone(cp.load_manifest(tmp))

    def test_resume_entry_por_pico(self):
        manifest = {"clips": [{"peak": 40, "file": "clips/a.mp4"},
                              {"peak": 90, "file": "clips/b.mp4"}]}
        self.assertEqual(cp.resume_entry(manifest, 90)["file"],
                         "clips/b.mp4")
        self.assertIsNone(cp.resume_entry(manifest, 140))
        self.assertIsNone(cp.resume_entry(None, 40))


class TestResumeE2E(unittest.TestCase):
    def test_segunda_corrida_reutiliza_los_clips(self):
        from auto_clips import main
        with tempfile.TemporaryDirectory() as tmp:
            chat = _chat_1_3(os.path.join(tmp, "chat.json"))
            video = _video(os.path.join(tmp, "vod.mp4"))
            out_dir = os.path.join(tmp, "runs")
            argv = ["auto_clips.py", "--chat", chat, "--video", video,
                    "--out", out_dir, "--top-n", "10", "--min-separation",
                    "20", "--window", "10", "--duration", "10",
                    "--pre-roll", "5", "--no-precise-cut", "--no-viral"]
            with mock.patch.object(sys, "argv", argv):
                self.assertEqual(main(), 0)
            with open(os.path.join(out_dir, "manifest.json"),
                      encoding="utf-8") as f:
                manifest1 = json.load(f)
            self.assertEqual(len(manifest1["clips"]), 3)
            state = cp.load_state(out_dir)
            self.assertTrue(cp.stage_done(state, "detect"))
            self.assertTrue(cp.stage_done(state, "manifest"))
            self.assertEqual(len(state.get("clips", {})), 3)
            mtimes = {c["file"]: os.path.getmtime(
                os.path.join(out_dir, c["file"]))
                for c in manifest1["clips"]}
            # Segunda corrida: los 3 clips se reutilizan del checkpoint.
            import io
            buf = io.StringIO()
            with mock.patch.object(sys, "argv", argv), \
                    mock.patch("sys.stdout", buf):
                self.assertEqual(main(), 0)
            out = buf.getvalue()
            self.assertEqual(out.count("reutilizado del checkpoint"), 3)
            with open(os.path.join(out_dir, "manifest.json"),
                      encoding="utf-8") as f:
                manifest2 = json.load(f)
            self.assertEqual(len(manifest2["clips"]), 3)
            self.assertEqual(
                [c["peak"] for c in manifest2["clips"]],
                [c["peak"] for c in manifest1["clips"]])
            for c in manifest2["clips"]:
                self.assertEqual(
                    os.path.getmtime(os.path.join(out_dir, c["file"])),
                    mtimes[c["file"]])  # no se re-cortó nada
            # --fresh rehace todo (ningún reutilizado).
            buf = io.StringIO()
            with mock.patch.object(sys, "argv", argv + ["--fresh"]), \
                    mock.patch("sys.stdout", buf):
                self.assertEqual(main(), 0)
            self.assertNotIn("reutilizado del checkpoint", buf.getvalue())
            with open(os.path.join(out_dir, "manifest.json"),
                      encoding="utf-8") as f:
                manifest3 = json.load(f)
            self.assertEqual(len(manifest3["clips"]), 3)


if __name__ == "__main__":
    unittest.main(verbosity=2)

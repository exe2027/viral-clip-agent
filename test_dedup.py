#!/usr/bin/env python3
"""Pruebas de 7.3: dedup (sin solapes en el mismo VOD ni el mismo
momento en dos VODs; hash perceptual de 3 frames + ventana).
"""
import json
import os
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from clipagent.dedup import (clip_fingerprint, find_duplicate,
                             frame_hash, hash_distance, is_duplicate,
                             windows_overlap)


def _make_video(path, duration=10, source="testsrc"):
    if source.startswith("color="):
        spec = f"{source}:s=320x180:r=10:d={duration}"
    else:
        spec = f"{source}=duration={duration}:size=320x180:rate=10"
    cmd = ["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", spec,
           "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt",
           "yuv420p", path]
    subprocess.run(cmd, check=True)


class TestHash(unittest.TestCase):
    def test_mismo_frame_distancia_cero(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "a.mp4")
            _make_video(path)
            h1 = frame_hash(path, 2)
            h2 = frame_hash(path, 2)
            self.assertIsNotNone(h1)
            self.assertEqual(hash_distance(h1, h2), 0)

    def test_videos_distintos_distancia_grande(self):
        with tempfile.TemporaryDirectory() as tmp:
            a = os.path.join(tmp, "a.mp4")
            b = os.path.join(tmp, "b.mp4")
            _make_video(a, source="testsrc")
            _make_video(b, source="color=c=red")
            ha = frame_hash(a, 2)
            hb = frame_hash(b, 2)
            self.assertGreater(hash_distance(ha, hb), 5)

    def test_mismo_video_es_duplicado(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "a.mp4")
            _make_video(path)
            fp = clip_fingerprint(path, 10)
            self.assertIsNotNone(fp)
            self.assertEqual(len(fp), 3)
            self.assertTrue(is_duplicate(fp, fp))

    def test_videos_distintos_no_son_duplicados(self):
        with tempfile.TemporaryDirectory() as tmp:
            a = os.path.join(tmp, "a.mp4")
            b = os.path.join(tmp, "b.mp4")
            _make_video(a, source="testsrc")
            _make_video(b, source="color=c=red")
            self.assertFalse(is_duplicate(clip_fingerprint(a, 10),
                                         clip_fingerprint(b, 10)))

    def test_archivo_inexistente_devuelve_none(self):
        self.assertIsNone(frame_hash("/no/existe.mp4", 1))
        self.assertIsNone(clip_fingerprint("/no/existe.mp4", 10))


class TestWindows(unittest.TestCase):
    def test_solape(self):
        self.assertTrue(windows_overlap(100, 140, 130, 160))
        self.assertTrue(windows_overlap(100, 140, 90, 101))
        self.assertFalse(windows_overlap(100, 140, 140, 180))
        self.assertFalse(windows_overlap(100, 140, 150, 180))


class TestFindDuplicate(unittest.TestCase):
    def _run_dir(self, tmp, vod, clips):
        run_dir = os.path.join(tmp, vod)
        os.makedirs(os.path.join(run_dir, "clips"), exist_ok=True)
        with open(os.path.join(run_dir, "manifest.json"), "w",
                  encoding="utf-8") as f:
            json.dump({"vod_id": vod, "clips": clips}, f)
        return run_dir

    def test_ventana_solapada_mismo_vod(self):
        with tempfile.TemporaryDirectory() as tmp:
            clip_path = os.path.join(tmp, "nuevo.mp4")
            _make_video(clip_path)
            existing = [{"file": "clips/c1.mp4", "start": 100,
                         "duration": 40, "peak": 120}]
            reason, entry = find_duplicate(
                clip_path, 30, 130, tmp, "v1",
                manifest_clips=existing)
            self.assertIsNotNone(reason)
            self.assertIn("solapada", reason)
            self.assertEqual(entry["file"], "clips/c1.mp4")

    def test_sin_solape_no_hay_duplicado(self):
        with tempfile.TemporaryDirectory() as tmp:
            clip_path = os.path.join(tmp, "nuevo.mp4")
            _make_video(clip_path, source="color=c=blue")
            existing = [{"file": "clips/c1.mp4", "start": 100,
                         "duration": 40, "peak": 120}]
            reason, _ = find_duplicate(clip_path, 30, 200, tmp, "v1",
                                       manifest_clips=existing)
            self.assertIsNone(reason)

    def test_mismo_vod_sin_solape_no_es_duplicado_aunque_se_vea_igual(self):
        # Dos momentos distintos del mismo VOD pueden verse igual
        # (mismo testsrc): sin solape de ventana, no es duplicado.
        with tempfile.TemporaryDirectory() as tmp:
            clip_path = os.path.join(tmp, "nuevo.mp4")
            _make_video(clip_path)
            fp = clip_fingerprint(clip_path, 10)
            existing = [{"file": "clips/c1.mp4", "start": 100,
                         "duration": 10, "peak": 105,
                         "fingerprint": [f"{h:016x}" for h in fp]}]
            reason, _ = find_duplicate(clip_path, 10, 200, tmp, "v1",
                                       manifest_clips=existing)
            self.assertIsNone(reason)

    def test_manifiesto_propio_no_es_otro_vod(self):
        # Regresión (Fase 9, test de humo final): con --fresh el clip
        # re-cortado se comparaba contra su propio manifiesto anterior
        # (el directorio "runs" no coincide con vod_id=None) y se
        # descartaba como duplicado de sí mismo. Con exclude_manifest
        # no se marca duplicado aunque la huella sea idéntica.
        with tempfile.TemporaryDirectory() as tmp:
            nuevo = os.path.join(tmp, "nuevo.mp4")
            _make_video(nuevo)
            fp = clip_fingerprint(nuevo, 10)
            own = [{"file": "clips/c1.mp4", "start": 100,
                    "duration": 10, "peak": 105,
                    "fingerprint": [f"{h:016x}" for h in fp]}]
            own_dir = self._run_dir(tmp, "runs", own)
            reason, _ = find_duplicate(
                nuevo, 10, 200, tmp, None,
                exclude_manifest=os.path.join(own_dir, "manifest.json"))
            self.assertIsNone(reason)
            # Sin la exclusión, el mismo manifiesto sí lo marcaba.
            reason, entry = find_duplicate(nuevo, 10, 200, tmp, None)
            self.assertIsNotNone(reason)
            self.assertIn("otro VOD (runs)", reason)

    def test_mismo_momento_en_otro_vod_por_huella(self):
        with tempfile.TemporaryDirectory() as tmp:
            # Otro VOD con un clip cuya huella es la del vídeo nuevo.
            nuevo = os.path.join(tmp, "nuevo.mp4")
            _make_video(nuevo)
            fp = clip_fingerprint(nuevo, 10)
            other = [{"file": "clips/c1.mp4", "start": 500,
                      "duration": 10, "peak": 505,
                      "fingerprint": [f"{h:016x}" for h in fp]}]
            self._run_dir(tmp, "v2", other)
            reason, entry = find_duplicate(nuevo, 10, 100, tmp, "v1")
            self.assertIsNotNone(reason)
            self.assertIn("otro VOD (v2)", reason)
            self.assertEqual(entry["file"], "clips/c1.mp4")


if __name__ == "__main__":
    unittest.main(verbosity=2)

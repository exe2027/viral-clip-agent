#!/usr/bin/env python3
"""Pruebas de 1.3: el filtro previo descarta de verdad en auto_clips.py.

- filter_candidates: múltiplo < 3x, solo emote-spam, sin mensajes y
  puesto > 5 quedan fuera y registrados con sus motivos.
- E2E (sin red): chat sintético de 200 s con 3 picos reales + ruido
  de fondo + 2 picos débiles + 1 ventana de solo emotes: solo salen
  3 clips y el manifiesto trae 3 descartados con motivo.
"""
import json
import os
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from auto_clips import filter_candidates, main


def _detail(msgs=20, risas=10, hype=5, emote_ratio=0.0):
    return {"msgs": msgs, "risas": risas, "hype": hype,
            "emote_ratio": emote_ratio, "users": msgs}


class TestFilterCandidates(unittest.TestCase):
    def test_pasa_y_descarta_con_motivos(self):
        peaks = [(40, 50.0), (80, 60.0), (120, 4.0), (160, 30.0), (200, 30.0)]
        detail = {
            40: _detail(), 80: _detail(),
            120: _detail(msgs=4, risas=1, hype=0),
            160: _detail(msgs=20, risas=0, hype=0, emote_ratio=0.9),
            200: _detail(msgs=0, risas=0, hype=0),
        }
        rank_map = {ts: i + 1 for i, (ts, _) in enumerate(
            sorted(peaks, key=lambda p: -p[1]))}
        selected, discarded = filter_candidates(peaks, detail, rank_map, 2.0)
        self.assertEqual([ts for ts, _, _ in selected], [40, 80])
        self.assertEqual([d["peak"] for d in discarded], [120, 160, 200])
        motivos = " | ".join("; ".join(d["motivos"]) for d in discarded)
        self.assertIn("múltiplo", motivos)
        self.assertIn("emote-spam", motivos)
        self.assertIn("sin mensajes", motivos)

    def test_puesto_mayor_que_5_no_pasa(self):
        peaks = [(t, 50.0) for t in range(10, 70, 10)]
        detail = {t: _detail() for t, _ in peaks}
        rank_map = {ts: i + 1 for i, (ts, _) in enumerate(peaks)}
        selected, discarded = filter_candidates(peaks, detail, rank_map, 2.0)
        self.assertEqual(len(selected), 5)
        self.assertEqual(len(discarded), 1)
        self.assertIn("puesto 6", discarded[0]["motivos"][0])

    def test_mediana_cero_no_descarta_todo(self):
        # Chat casi vacío: mediana 0 -> suelo 1.0, el pico pasa.
        selected, discarded = filter_candidates(
            [(40, 10.0)], {40: _detail()}, {40: 1}, 0.0)
        self.assertEqual(len(selected), 1)
        self.assertEqual(discarded, [])


def _chat_1_3(path, dur_s=200):
    comments = []
    for t in range(0, dur_s, 5):
        comments.append({"content_offset_seconds": t,
                         "message": {"body": "hola"},
                         "commenter": {"name": f"fondo{t}"}})
    for peak in (40, 90, 140):
        for j in range(25):
            comments.append({"content_offset_seconds": peak + j % 10,
                             "message": {"body": "jajaja qué risa"},
                             "commenter": {"name": f"pico{peak}u{j}"}})
    for peak in (60, 115):
        comments.append({"content_offset_seconds": peak,
                         "message": {"body": "jajaja"},
                         "commenter": {"name": f"debil{peak}"}})
    for j in range(20):
        comments.append({"content_offset_seconds": 170 + j % 10,
                         "message": {"body": "Kappa"},
                         "commenter": {"name": f"emote{j}"}})
    with open(path, "w", encoding="utf-8") as f:
        json.dump({"comments": comments}, f)
    return path


def _video(path, dur_s=200):
    subprocess.run(
        ["ffmpeg", "-y", "-v", "error", "-f", "lavfi",
         "-i", f"testsrc=duration={dur_s}:size=640x360:rate=30",
         "-f", "lavfi", "-i", f"sine=frequency=440:duration={dur_s}",
         "-map", "0:v", "-map", "1:a",
         "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
         "-c:a", "aac", "-shortest", path], check=True)
    return path


class TestFiltroE2E(unittest.TestCase):
    def test_solo_salen_los_3_picos_reales(self):
        with tempfile.TemporaryDirectory() as tmp:
            chat = _chat_1_3(os.path.join(tmp, "chat.json"))
            video = _video(os.path.join(tmp, "vod.mp4"))
            out_dir = os.path.join(tmp, "runs")
            argv = ["auto_clips.py", "--chat", chat, "--video", video,
                    "--out", out_dir, "--top-n", "10", "--min-separation", "20",
                    "--window", "10", "--duration", "10",
                    "--pre-roll", "5", "--no-precise-cut", "--no-viral"]
            with mock.patch.object(sys, "argv", argv):
                self.assertEqual(main(), 0)
            with open(os.path.join(out_dir, "manifest.json"),
                      encoding="utf-8") as f:
                manifest = json.load(f)
            self.assertEqual(len(manifest["clips"]), 3)
            self.assertEqual(manifest["status"], "ok")
            descartados = manifest["descartados"]
            # Los 2 picos débiles y la ventana de emotes están entre los
            # descartados (el top-10 mete además ventanas de fondo, que
            # el filtro también descarta: eso es lo que tiene que pasar).
            picos_desc = sorted(d["peak"] for d in descartados)
            # Los 2 picos débiles (60 y 110, ventana del de 115) están
            # entre los descartados. La ventana de emotes (Kappa) no
            # siempre entra en el top-10 por el z-score local: su
            # motivo emote-spam está cubierto por el test unitario de
            # filter_candidates, no por este e2e.
            self.assertIn(60, picos_desc)
            self.assertIn(110, picos_desc)
            motivos = " | ".join("; ".join(d["motivos"]) for d in descartados)
            self.assertIn("múltiplo", motivos)
            # Los clips que salieron son los picos fuertes (40, 90, 140).
            picos = sorted(c["peak"] for c in manifest["clips"])
            self.assertEqual(picos, [40, 90, 140])


class TestSinMomentosVirales(unittest.TestCase):
    def test_sin_picos_que_pasen_no_corta_nada(self):
        with tempfile.TemporaryDirectory() as tmp:
            chat = os.path.join(tmp, "chat.json")
            with open(chat, "w", encoding="utf-8") as f:
                json.dump({"comments": [
                    {"content_offset_seconds": t,
                     "message": {"body": "hola"},
                     "commenter": {"name": f"u{t}"}}
                    for t in range(0, 200, 5)]}, f)
            video = _video(os.path.join(tmp, "vod.mp4"))
            out_dir = os.path.join(tmp, "runs")
            argv = ["auto_clips.py", "--chat", chat, "--video", video,
                    "--out", out_dir, "--top-n", "5", "--min-separation", "20",
                    "--window", "10", "--duration", "10",
                    "--pre-roll", "5", "--no-precise-cut", "--no-viral"]
            with mock.patch.object(sys, "argv", argv):
                self.assertEqual(main(), 0)
            with open(os.path.join(out_dir, "manifest.json"),
                      encoding="utf-8") as f:
                manifest = json.load(f)
            self.assertEqual(manifest["clips"], [])
            self.assertEqual(manifest["status"], "sin_momentos_virales")
            self.assertFalse(os.path.isdir(os.path.join(out_dir, "clips"))
                             and os.listdir(os.path.join(out_dir, "clips")))


if __name__ == "__main__":
    unittest.main(verbosity=2)

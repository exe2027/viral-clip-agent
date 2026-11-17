#!/usr/bin/env python3
"""Pruebas de 4.1: una sola lista de streamers (streamers.yaml),
con migración sin pérdidas desde las tres listas viejas.
"""
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from clipagent import streamers


class TestSingleList(unittest.TestCase):
    def test_migracion_sin_perdidas(self):
        entries = streamers.load_streamers()
        names = {e["name"] for e in entries}
        self.assertIn("illojuan", names)
        illo = streamers.get_streamer("illojuan", entries)
        self.assertEqual(illo["platform"], "twitch")
        self.assertEqual(illo["game"], "GTA 6")
        self.assertEqual(illo["language"], "es")
        self.assertEqual(illo["estado"], "concedido")
        self.assertEqual(illo["fecha_permiso"], "2026-11-16")
        self.assertEqual(illo["channel_url"],
                         "https://www.twitch.tv/illojuan")
        self.assertEqual(illo["publish_platforms"],
                         ["youtube", "tiktok", "instagram", "facebook"])
        # evidencia_url existe y es opcional (vacío).
        self.assertEqual(illo["evidencia_url"], "")
        self.assertTrue(streamers.is_allowed("illojuan", entries))
        self.assertFalse(streamers.is_allowed("coker", entries))

    def test_lectores_usan_la_lista_unica(self):
        from jobqueue import load_allowlist
        self.assertIn("illojuan", load_allowlist())
        self.assertNotIn("coker", load_allowlist())
        from watcher import load_influencers
        watched = {i["usuario"] for i in load_influencers()}
        self.assertEqual(watched, {"illojuan"})
        from publisher import load_whitelist
        self.assertIn("illojuan", load_whitelist())

    def test_respaldo_listas_viejas(self):
        with tempfile.TemporaryDirectory() as tmp:
            with open(os.path.join(tmp, "influencers.yaml"), "w",
                      encoding="utf-8") as f:
                f.write("influencers:\n"
                        "  - usuario: viejo\n"
                        "    plataforma: kick\n"
                        "    estado: concedido\n")
            entries = streamers.load_streamers(base_dir=tmp)
            self.assertEqual([e["name"] for e in entries], ["viejo"])
            self.assertEqual(entries[0]["platform"], "kick")

    def test_add_y_remove(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "streamers.yaml")
            ok, msg = streamers.add_streamer(
                {"name": "nuevo", "platform": "kick"}, path=path, base_dir=tmp)
            self.assertTrue(ok, msg)
            ok, msg = streamers.add_streamer(
                {"name": "nuevo", "platform": "kick"}, path=path, base_dir=tmp)
            self.assertFalse(ok)  # no se duplica
            self.assertIn("ya está", msg)
            ok, msg = streamers.add_streamer(
                {"name": "malo", "platform": "youtube"}, path=path,
                base_dir=tmp)
            self.assertFalse(ok)
            self.assertIn("twitch o kick", msg)
            self.assertTrue(streamers.remove_streamer("nuevo", path, tmp))
            self.assertFalse(streamers.remove_streamer("nuevo", path, tmp))
            self.assertEqual(streamers.load_streamers(path, tmp), [])

    def test_denegado_excluye(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "streamers.yaml")
            streamers.add_streamer(
                {"name": "fuera", "platform": "twitch",
                 "estado": "denegado"}, path=path, base_dir=tmp)
            self.assertFalse(streamers.is_allowed("fuera", base_dir=tmp))


if __name__ == "__main__":
    unittest.main(verbosity=2)

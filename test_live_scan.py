#!/usr/bin/env python3
"""Pruebas del barrido en vivo simultáneo (2026-11-18).

FALLO REAL 2026-11-18: cerebro-live solo miraba a rubius. El
barrido debe mirar a TODOS los enabled de streamers.yaml (ES + EN,
Twitch y Kick) y devolver solo los en vivo en GTA VI.

Los clientes (Helix/GQL, Kick) se simulan: sin red.
"""
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from clipagent import live_scan


class _FakeTwitch:
    def __init__(self, live_map):
        self.live_map = live_map  # name -> game o None (offline)

    def check(self, name):
        game = self.live_map.get(name)
        if game is None:
            return None, []
        return {"game": game, "title": "x", "viewers": 10}, []


class TestLiveScan(unittest.TestCase):
    def _patch(self, live_map, kick_map):
        def fake_kick(slug):
            game = kick_map.get(slug)
            if game is None:
                return None, []
            return {"game": game, "title": "x", "viewers": 5}, []

        return mock.patch("watcher.twitch_client",
                          return_value=(_FakeTwitch(live_map), "helix")), \
            mock.patch("watcher.kick_check", side_effect=fake_kick)

    def test_barrido_todos_en_vivo_gta_vi(self):
        """6 en vivo en GTA VI (mezcla ES/EN, Twitch/Kick); el resto
        offline, en otro juego o en YouTube se queda fuera."""
        tw, kick = self._patch(
            {"illojuan": "Grand Theft Auto VI",
             "rubius": "Grand Theft Auto VI",
             "auronplay": None,
             "ibai": "Just Chatting",   # en vivo pero NO GTA VI
             "kaicenat": "Grand Theft Auto VI",
             "theburntpeanut": "Grand Theft Auto VI"},
            {"xqc": "Grand Theft Auto VI (GTA)",
             "n3on": None})
        with tw, kick:
            r = live_scan.scan_live()
        names = {x["name"] for x in r["live"]}
        self.assertEqual(names, {
            "illojuan", "rubius", "kaicenat", "theburntpeanut", "xqc"})
        self.assertEqual({x["platform"] for x in r["live"]
                          if x["name"] == "xqc"}, {"kick"})
        self.assertNotIn("ibai", names)      # Just Chatting: fuera
        self.assertNotIn("auronplay", names)  # offline: fuera
        self.assertIn("ishowspeed", " ".join(r["skipped"]))  # youtube

    def test_nadie_en_vivo_lista_vacia_sin_fallo(self):
        tw, kick = self._patch({}, {})
        with tw, kick:
            r = live_scan.scan_live()
        self.assertEqual(r["live"], [])
        self.assertEqual(r["checked"], 18)

    def test_matriz_lleva_el_idioma_del_canal(self):
        # 2026-11-19, calibración: el workflow pasa --lang al corte
        # desde matrix.target.language; sin él todo era español.
        tw, kick = self._patch(
            {"illojuan": "Grand Theft Auto VI",
             "kaicenat": "Grand Theft Auto VI"}, {})
        with tw, kick:
            r = live_scan.scan_live()
        by_name = {x["name"]: x for x in r["live"]}
        self.assertEqual(by_name["illojuan"]["language"], "es")
        self.assertEqual(by_name["kaicenat"]["language"], "en")

    def test_filtro_only(self):
        tw, kick = self._patch(
            {"illojuan": "Grand Theft Auto VI",
             "rubius": "Grand Theft Auto VI"}, {})
        with tw, kick:
            r = live_scan.scan_live(only=["illojuan"])
        self.assertEqual([x["name"] for x in r["live"]], ["illojuan"])

    def test_error_en_un_streamer_no_tumba_el_barrido(self):
        def kick_con_error(slug):
            raise RuntimeError("kick caído")

        tw = mock.patch("watcher.twitch_client",
                        return_value=(_FakeTwitch(
                            {"illojuan": "Grand Theft Auto VI"}), "helix"))
        with tw, mock.patch("watcher.kick_check",
                            side_effect=kick_con_error):
            r = live_scan.scan_live()
        self.assertEqual([x["name"] for x in r["live"]], ["illojuan"])
        self.assertTrue(any("xqc" in s for s in r["skipped"]))

    def test_main_escribe_github_output(self):
        import tempfile
        tw, kick = self._patch(
            {"illojuan": "Grand Theft Auto VI"}, {})
        with tempfile.NamedTemporaryFile("w", delete=False) as f:
            gh_out = f.name
        with mock.patch.dict(os.environ, {"GITHUB_OUTPUT": gh_out}):
            with tw, kick:
                self.assertEqual(live_scan.main(), 0)
        with open(gh_out, encoding="utf-8") as f:
            body = f.read()
        self.assertIn('"name": "illojuan"', body)
        self.assertIn("count=1", body)
        os.unlink(gh_out)


if __name__ == "__main__":
    unittest.main()

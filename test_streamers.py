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
        self.assertEqual(illo["game"], "GTA VI")
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
        # Decisión del usuario 2026-11-18 15:30: 10 streamers activos
        # (Rubius + IlloJuan reactivado + 8 nuevos), todos GTA VI.
        # IlloJuan estuvo pausado 13:34-15:30, ya no.
        # Orden 2026-11-18: + 8 en inglés (Kai Cenat, IShowSpeed,
        # N3ON, xQc, TheBurntPeanut, Burn, Kaysan, Will Neff).
        self.assertEqual(watched, {
            "illojuan", "rubius", "auronplay", "ibai", "thegrefg",
            "xokas", "djmariio", "knekro", "rdjavi", "westcol",
            "kaicenat", "ishowspeed", "n3on", "xqc", "theburntpeanut",
            "burn", "kaysan", "willneff",
        })
        self.assertIn("illojuan", watched)
        from clipagent.publish import load_whitelist
        self.assertIn("illojuan", load_whitelist())

    def test_diez_activos_todos_gta_vi(self):
        """Orden 2026-11-18 15:30: los 10 con categoría GTA VI, en
        streamers.yaml (repo) y en el panel (D1, tenant sebastian).
        El gate de categoría (clipagent/category.is_gta6, panel
        isGtaViGame) acepta 'GTA VI' / 'GTA 6' / 'Grand Theft Auto
        VI'; aquí se fija el valor canónico 'GTA VI'."""
        from clipagent.category import is_gta6
        entries = streamers.load_streamers()
        esperado = {
            "illojuan": "2026-11-16",
            "rubius": "2026-11-18",
            "auronplay": "2026-11-18",
            "ibai": "2026-11-18",
            "thegrefg": "2026-11-18",
            "xokas": "2026-11-18",
            "djmariio": "2026-11-18",
            "knekro": "2026-11-18",
            "rdjavi": "2026-11-18",
            "westcol": "2026-11-18",
        }
        self.assertEqual(len([e for e in entries
                              if e["language"] == "es"]), 10)
        for name, fecha in esperado.items():
            with self.subTest(streamer=name):
                e = streamers.get_streamer(name, entries)
                self.assertIsNotNone(e, f"falta {name} en streamers.yaml")
                self.assertTrue(e.get("enabled", True), f"{name} activo")
                self.assertEqual(e["platform"], "twitch")
                self.assertEqual(e["game"], "GTA VI")
                self.assertTrue(is_gta6(e["game"]))
                self.assertEqual(e["language"], "es")
                self.assertEqual(e["estado"], "concedido")
                self.assertEqual(e["fecha_permiso"], fecha)
                canal = {"djmariio": "https://www.twitch.tv/djMariio"}.get(
                    name, f"https://www.twitch.tv/{name}")
                self.assertEqual(e["channel_url"], canal)
                self.assertEqual(e["publish_platforms"],
                                 ["youtube", "tiktok", "instagram",
                                  "facebook"])

    def test_ocho_ingleses_todos_gta_vi(self):
        """Orden 2026-11-18: 8 influencers EN con categoría GTA VI.
        Handles verificados en vivo antes de agregarlos (ninguno
        inventado): KaiCenat e IShowSpeed (YouTube), n3on y xQc
        (Kick), TheBurntPeanut, Burn, Kaysan y WillNeff (Twitch)."""
        from clipagent.category import is_gta6
        entries = streamers.load_streamers()
        en = [e for e in entries if e["language"] == "en"]
        self.assertEqual(len(en), 8)
        esperado = {
            "kaicenat": ("twitch", "https://www.twitch.tv/kaicenat"),
            "ishowspeed": ("youtube",
                           "https://www.youtube.com/@IShowSpeed"),
            "n3on": ("kick", "https://kick.com/n3on"),
            "xqc": ("kick", "https://kick.com/xqc"),
            "theburntpeanut": ("twitch",
                               "https://www.twitch.tv/theburntpeanut"),
            "burn": ("twitch", "https://www.twitch.tv/burn"),
            "kaysan": ("twitch", "https://www.twitch.tv/kaysan"),
            "willneff": ("twitch",
                         "https://www.twitch.tv/willneff"),
        }
        for name, (platform, canal) in esperado.items():
            with self.subTest(streamer=name):
                e = streamers.get_streamer(name, entries)
                self.assertIsNotNone(e, f"falta {name} en streamers.yaml")
                self.assertTrue(e.get("enabled", True), f"{name} activo")
                self.assertEqual(e["platform"], platform)
                self.assertEqual(e["channel_url"], canal)
                self.assertEqual(e["game"], "GTA VI")
                self.assertTrue(is_gta6(e["game"]))
                self.assertEqual(e["language"], "en")
                self.assertEqual(e["estado"], "concedido")
                self.assertEqual(e["fecha_permiso"], "2026-11-18")
                self.assertEqual(e["publish_platforms"],
                                 ["youtube", "tiktok", "instagram",
                                  "facebook"])

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
                {"name": "malo", "platform": "facebook"}, path=path,
                base_dir=tmp)
            self.assertFalse(ok)
            self.assertIn("twitch, kick o youtube", msg)
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

    def test_watcher_ignora_listas_viejas(self):
        """4.3: con streamers.yaml presente, el watcher NO vigila a
        quien solo está en influencers.yaml (lista vieja)."""
        import watcher as watcher_mod
        with tempfile.TemporaryDirectory() as tmp:
            with open(os.path.join(tmp, "streamers.yaml"), "w",
                      encoding="utf-8") as f:
                f.write("streamers:\n"
                        "  - name: illojuan\n"
                        "    platform: twitch\n")
            with open(os.path.join(tmp, "influencers.yaml"), "w",
                      encoding="utf-8") as f:
                f.write("influencers:\n"
                        "  - usuario: illojuan\n"
                        "    plataforma: twitch\n"
                        "  - usuario: coker\n"
                        "    plataforma: kick\n")
            old_base = streamers.BASE_DIR
            streamers.BASE_DIR = tmp
            try:
                watched = {i["usuario"]
                           for i in watcher_mod.load_influencers()}
            finally:
                streamers.BASE_DIR = old_base
            self.assertEqual(watched, {"illojuan"})

    def test_tope_diario_por_streamer(self):
        """4.3: el tope de VODs nuevos por streamer por día existe
        en config (max_new_vods_per_streamer_per_day: 5) y el
        watcher lo respeta; ver test_watcher.TestDecide.test_tope_diario."""
        from clipagent.config import load_config
        cfg = load_config()
        self.assertEqual(
            cfg["watcher"]["max_new_vods_per_streamer_per_day"], 5)


if __name__ == "__main__":
    unittest.main(verbosity=2)

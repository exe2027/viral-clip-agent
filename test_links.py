#!/usr/bin/env python3
"""Pruebas de 4.2: entrada única de links (canal y VOD).
"""
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from clipagent import links, streamers


class TestParseLink(unittest.TestCase):
    def test_canal_twitch_y_kick(self):
        self.assertEqual(
            links.parse_streamer_link("https://www.twitch.tv/IlloJuan"),
            ("twitch", "illojuan", None))
        self.assertEqual(
            links.parse_streamer_link("twitch.tv/illojuan/"),
            ("twitch", "illojuan", None))
        self.assertEqual(
            links.parse_streamer_link("https://kick.com/coker?x=1"),
            ("kick", "coker", None))

    def test_vod_kick_lleva_el_canal(self):
        self.assertEqual(
            links.parse_streamer_link(
                "https://kick.com/coker/videos/01a1dffd-8fb8-71ee-abb7-66e4629249f9"),
            ("kick", "coker", None))

    def test_vod_twitch_sin_canal(self):
        self.assertEqual(
            links.parse_streamer_link("https://twitch.tv/videos/2929669422"),
            ("twitch", None, "2929669422"))

    def test_links_invalidos(self):
        for bad in ("", "hola", "https://youtube.com/@alguien",
                    "https://twitch.tv/", "https://kick.com/",
                    "https://twitch.tv/videos/abc"):
            with self.assertRaises(ValueError, msg=bad):
                links.parse_streamer_link(bad)


class TestAddLink(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.base = self.tmp.name
        self.path = os.path.join(self.base, "streamers.yaml")

    def tearDown(self):
        self.tmp.cleanup()

    def test_add_desde_link_de_canal(self):
        ok, msg = links.add_link("https://kick.com/coker",
                                 base_dir=self.base)
        self.assertTrue(ok, msg)
        entry = streamers.get_streamer("coker", base_dir=self.base)
        self.assertEqual(entry["platform"], "kick")
        self.assertEqual(entry["estado"], "concedido")
        self.assertEqual(entry["channel_url"], "https://kick.com/coker")

    def test_add_duplicado(self):
        links.add_link("https://kick.com/coker", base_dir=self.base)
        ok, msg = links.add_link("https://www.kick.com/coker",
                                 base_dir=self.base)
        self.assertFalse(ok)
        self.assertIn("ya está", msg)

    def test_add_link_invalido(self):
        ok, msg = links.add_link("https://youtube.com/@x",
                                 base_dir=self.base)
        self.assertFalse(ok)
        self.assertIn("no reconozco", msg)

    def test_vod_twitch_con_resolucion(self):
        ok, msg = links.add_link(
            "https://twitch.tv/videos/2929669422", base_dir=self.base,
            resolve_fn=lambda vod_id, base: "peereira7")
        self.assertTrue(ok, msg)
        self.assertEqual(
            streamers.get_streamer("peereira7",
                                   base_dir=self.base)["platform"], "twitch")

    def test_vod_twitch_sin_resolucion_error_claro(self):
        ok, msg = links.add_link(
            "https://twitch.tv/videos/2929669422", base_dir=self.base,
            resolve_fn=lambda vod_id, base: None)
        self.assertFalse(ok)
        self.assertIn("no se puede sacar el canal", msg)
        self.assertIn("twitch.tv/<canal>", msg)

    def test_quitar_bloquea_nuevas_subidas(self):
        # Quitar al streamer de la lista bloquea nuevas subidas
        # aunque haya clips aprobados: check_whitelist relee la
        # lista en cada publicación (ver test_streamers).
        links.add_link("https://kick.com/coker", base_dir=self.base)
        # check_whitelist lee el streamers.yaml del repo real, no el
        # temporal: se comprueba con el lector directo.
        self.assertTrue(streamers.is_allowed("coker", base_dir=self.base))
        self.assertTrue(streamers.remove_streamer(
            "coker", self.path, self.base))
        self.assertFalse(streamers.is_allowed("coker", base_dir=self.base))


if __name__ == "__main__":
    unittest.main(verbosity=2)

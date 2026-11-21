#!/usr/bin/env python3
"""Tests de streamers por tenant desde el panel (2026-11-19,
cerebro genérico): load_streamers() pide la lista al panel
cuando hay bridge o PANEL_TENANT; si el panel falla, levanta
(nunca cae en silencio al streamers.yaml del repo, que es la
lista de otro tenant).
"""
import os
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from clipagent import streamers
from clipagent.panel_bridge import PanelError

ROWS = [
    {"handle": "Rubius", "name": "Rubius", "platform": "twitch",
     "profile_url": "https://www.twitch.tv/rubius", "language": "es",
     "permission": True, "active": True, "game": "GTA VI",
     "permission_date": "2026-11-18",
     "publish_platforms": ["youtube", "tiktok", "instagram", "facebook"]},
    {"handle": "KaiCenat", "name": "KaiCenat", "platform": "twitch",
     "profile_url": "https://www.twitch.tv/kaicenat", "language": "en",
     "permission": True, "active": True, "game": "GTA VI",
     "permission_date": "2026-11-18",
     "publish_platforms": ["youtube", "tiktok"]},
    {"handle": "Noni", "name": "Noni", "platform": "twitch",
     "profile_url": "https://www.twitch.tv/noni", "language": "es",
     "permission": False, "active": True, "game": "GTA VI",
     "permission_date": "", "publish_platforms": []},
]


class FakeBridge:
    def __init__(self, rows=None, fail=False):
        self.rows = rows
        self.fail = fail

    def list_influencers(self):
        if self.fail:
            raise PanelError("panel caído")
        return self.rows


class TestPanelFirst(unittest.TestCase):
    def test_mapea_los_influencers_del_panel(self):
        entries = streamers.load_streamers(bridge=FakeBridge(ROWS))
        by_name = {e["name"]: e for e in entries}
        self.assertEqual(set(by_name), {"rubius", "kaicenat", "noni"})
        rubius = by_name["rubius"]
        self.assertEqual(rubius["platform"], "twitch")
        self.assertEqual(rubius["language"], "es")
        self.assertEqual(rubius["estado"], "concedido")
        self.assertTrue(rubius["enabled"])
        self.assertEqual(rubius["channel_url"],
                         "https://www.twitch.tv/rubius")
        self.assertEqual(rubius["fecha_permiso"], "2026-11-18")
        self.assertEqual(rubius["publish_platforms"],
                         ["youtube", "tiktok", "instagram", "facebook"])
        kai = by_name["kaicenat"]
        self.assertEqual(kai["language"], "en")
        noni = by_name["noni"]
        self.assertEqual(noni["estado"], "pendiente")
        self.assertFalse(noni["enabled"])
        self.assertTrue(streamers.is_allowed("rubius", entries))
        self.assertTrue(streamers.is_allowed("kaicenat", entries))

    def test_si_el_panel_falla_levanta_sin_caer_al_archivo(self):
        with self.assertRaises(PanelError):
            streamers.load_streamers(bridge=FakeBridge(fail=True))

    def test_panel_tenant_sin_credenciales_levanta(self):
        env = {k: v for k, v in os.environ.items()
               if k not in ("PANEL_BRAIN_TOKEN", "PANEL_USERNAME",
                            "PANEL_PASSWORD", "PANEL_SESSION_TOKEN")}
        env["PANEL_TENANT"] = "kaez"
        with mock.patch.dict(os.environ, env, clear=True):
            with self.assertRaises(RuntimeError):
                streamers.load_streamers()

    def test_path_explicito_gana_sin_tocar_el_panel(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "streamers.yaml")
            with open(path, "w", encoding="utf-8") as f:
                f.write("streamers:\n"
                        "  - name: local\n"
                        "    platform: twitch\n"
                        "    language: es\n"
                        "    estado: concedido\n")
            env = dict(os.environ, PANEL_TENANT="kaez")
            with mock.patch.dict(os.environ, env, clear=False):
                entries = streamers.load_streamers(path=path)
            self.assertEqual([e["name"] for e in entries], ["local"])

    def test_sin_panel_ni_tenant_lee_el_archivo_como_antes(self):
        env = {k: v for k, v in os.environ.items()
               if k not in ("PANEL_TENANT", "PANEL_BRAIN_TOKEN",
                            "PANEL_USERNAME", "PANEL_PASSWORD",
                            "PANEL_SESSION_TOKEN")}
        with mock.patch.dict(os.environ, env, clear=True):
            entries = streamers.load_streamers()
        names = {e["name"] for e in entries}
        self.assertIn("illojuan", names)


if __name__ == "__main__":
    unittest.main()

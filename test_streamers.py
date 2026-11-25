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


class TestGetStreamerPorPlataforma(unittest.TestCase):
    """get_streamer(name, platform) (2026-11-24, corte YouTube):
    misma persona, dos entradas (Rubius twitch + youtube)."""

    ENTRIES = [
        {"name": "rubius", "platform": "twitch",
         "channel_url": "https://www.twitch.tv/rubius",
         "language": "es", "estado": "concedido", "enabled": True},
        {"name": "rubius", "platform": "youtube",
         "channel_url": "https://www.youtube.com/@rubius",
         "language": "es", "estado": "concedido", "enabled": True},
    ]

    def test_por_defecto_twitch(self):
        entry = streamers.get_streamer("rubius", self.ENTRIES)
        self.assertEqual(entry["platform"], "twitch")
        entry = streamers.get_streamer(
            "rubius", self.ENTRIES, platform="twitch")
        self.assertEqual(entry["platform"], "twitch")

    def test_youtube_devuelve_la_entrada_youtube(self):
        entry = streamers.get_streamer(
            "rubius", self.ENTRIES, platform="youtube")
        self.assertEqual(entry["platform"], "youtube")
        self.assertEqual(entry["channel_url"],
                         "https://www.youtube.com/@rubius")

    def test_is_allowed_por_plataforma(self):
        entries = [
            dict(self.ENTRIES[0], estado="denegado"),
            self.ENTRIES[1],
        ]
        self.assertFalse(streamers.is_allowed(
            "rubius", entries, platform="twitch"))
        self.assertTrue(streamers.is_allowed(
            "rubius", entries, platform="youtube"))

    def test_sin_entrada_de_esa_plataforma_cae_al_primer_match(self):
        # Preserva el comportamiento de los llamadores existentes.
        entry = streamers.get_streamer(
            "rubius", self.ENTRIES[:1], platform="youtube")
        self.assertEqual(entry["platform"], "twitch")
        self.assertIsNone(
            streamers.get_streamer("coker", self.ENTRIES))


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
            "peereira7", "noni",
            "kaicenat", "ishowspeed", "n3on", "xqc", "theburntpeanut",
            "burn", "kaysan", "willneff",
            # Virales de campaña (2026-11-23, orden del usuario 18:37).
            # Bobby Van Riper NO está: desactivado por la regla de
            # fama 18:50 (734 seguidores), no se vigila.
            "dasgasd3m", "arthurtv", "dylanpage",
            "bendadonnn",
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
            "peereira7": "2026-11-19",
            "noni": "2026-11-19",
        }
        self.assertEqual(len([e for e in entries
                              if e["language"] == "es"]), 12)
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

    def test_trece_ingleses_todos_gta_vi(self):
        """Orden 2026-11-18: 8 influencers EN con categoria GTA VI,
        + 5 virales de campana EN (2026-11-23). Handles verificados
        en vivo antes de agregarlos (ninguno inventado): KaiCenat e
        IShowSpeed (YouTube), n3on y xQc (Kick), TheBurntPeanut,
        Burn, Kaysan y WillNeff (Twitch); Dasgasd3m (Kick),
        ArthurTV (Twitch), Bobby Van Riper (Twitch), Dylan Page
        (YouTube-only) y BenDaDonnn (Twitch). Bobby queda
        DESACTIVADO por la regla 2026-11-23 18:50 (solo famosos:
        734 seguidores); los otros 12 EN siguen activos."""
        from clipagent.category import is_gta6
        entries = streamers.load_streamers()
        en = [e for e in entries if e["language"] == "en"]
        self.assertEqual(len(en), 13)
        esperado = {
            "kaicenat": ("twitch", "https://www.twitch.tv/kaicenat",
                         "2026-11-18",
                         ["youtube", "tiktok", "instagram", "facebook"]),
            "ishowspeed": ("youtube",
                           "https://www.youtube.com/@IShowSpeed",
                           "2026-11-18",
                           ["youtube", "tiktok", "instagram", "facebook"]),
            "n3on": ("kick", "https://kick.com/n3on", "2026-11-18",
                     ["youtube", "tiktok", "instagram", "facebook"]),
            "xqc": ("kick", "https://kick.com/xqc", "2026-11-18",
                    ["youtube", "tiktok", "instagram", "facebook"]),
            "theburntpeanut": ("twitch",
                               "https://www.twitch.tv/theburntpeanut",
                               "2026-11-18",
                               ["youtube", "tiktok", "instagram",
                                "facebook"]),
            "burn": ("twitch", "https://www.twitch.tv/burn",
                     "2026-11-18",
                     ["youtube", "tiktok", "instagram", "facebook"]),
            "kaysan": ("twitch", "https://www.twitch.tv/kaysan",
                       "2026-11-18",
                       ["youtube", "tiktok", "instagram", "facebook"]),
            "willneff": ("twitch", "https://www.twitch.tv/willneff",
                         "2026-11-18",
                         ["youtube", "tiktok", "instagram", "facebook"]),
            "dasgasd3m": ("kick", "https://kick.com/dasgasd3m",
                          "2026-11-23", ["youtube", "tiktok"]),
            "arthurtv": ("twitch", "https://www.twitch.tv/arthurtv",
                         "2026-11-23", ["tiktok"]),
            "bobby_vanriper": (
                "twitch", "https://www.twitch.tv/bobby_vanriper",
                "2026-11-23", ["youtube", "tiktok", "instagram"]),
            "dylanpage": ("youtube",
                          "https://www.youtube.com/@dylan.page.",
                          "2026-11-23",
                          ["youtube", "tiktok", "instagram"]),
            "bendadonnn": ("twitch", "https://www.twitch.tv/bendadonnn",
                           "2026-11-23",
                           ["youtube", "tiktok", "instagram", "facebook"]),
        }
        for name, (platform, canal, fecha, publish) in esperado.items():
            with self.subTest(streamer=name):
                e = streamers.get_streamer(name, entries)
                self.assertIsNotNone(e, f"falta {name} en streamers.yaml")
                if name == "bobby_vanriper":
                    # Regla 18:50 (solo famosos): desactivado, no se vigila.
                    self.assertFalse(e.get("enabled", True),
                                     "bobby_vanriper debe estar desactivado")
                else:
                    self.assertTrue(e.get("enabled", True), f"{name} activo")
                self.assertEqual(e["platform"], platform)
                self.assertEqual(e["channel_url"], canal)
                self.assertEqual(e["game"], "GTA VI")
                self.assertTrue(is_gta6(e["game"]))
                self.assertEqual(e["language"], "en")
                self.assertEqual(e["estado"], "concedido")
                self.assertEqual(e["fecha_permiso"], fecha)
                self.assertEqual(e["publish_platforms"], publish)

    def test_cuatro_virales_famosos_campana_y_bobby_excluido(self):
        """Los 5 virales de campana (2026-11-23) llevan su campana,
        tarifa bruta/neta y minimo en notas; Dylan Page es
        youtube-only (sin barrido en vivo) y BenDaDonnn queda bajo
        el piso neto ($0,90 < $1/1k). Regla 2026-11-23 18:50 (solo
        FAMOSOS, >= 50.000 seguidores): los 4 famosos llevan su
        evidencia de fama en notas; Bobby Van Riper (734 seguidores
        en Twitch) queda EXCLUIDO del ranking/prioridad/activación
        y desactivado en el yaml, aunque su campaña pague $1,50."""
        entries = streamers.load_streamers()
        campanas = {
            "dasgasd3m": ("051002dd-5c46-43d0-a446-43f2957ba3a9",
                          "$2,00/1k bruto = $1,80 neto"),
            "arthurtv": ("6e9ca548-f36c-42c2-abc0-61f89b4b53ba",
                         "$1,50/1k bruto = $1,35 neto"),
            "bobby_vanriper": ("f583b069-ced7-49e5-9ed3-2d3a13d6df0c",
                               "$1,50/1k bruto = $1,35 neto"),
            "dylanpage": ("39753a1a-f1f3-491b-a8f6-0cfee0af0c97",
                          "$1,20/1k bruto = $1,08 neto"),
            "bendadonnn": ("bb66f9eb-b90e-4dd0-8cee-9f3cd566c9a0",
                           "$0,90 NETO"),
        }
        fama = {
            "dasgasd3m": "62.500",
            "arthurtv": "500.000",
            "dylanpage": "1,17M",
            "bendadonnn": "645.690",
        }
        for name, (campaign_id, tarifa) in campanas.items():
            with self.subTest(streamer=name):
                e = streamers.get_streamer(name, entries)
                self.assertIsNotNone(e, f"falta {name} en streamers.yaml")
                self.assertIn(campaign_id, e["notas"])
                self.assertIn(tarifa, e["notas"])
                self.assertTrue(streamers.is_allowed(name, entries))
                # Material (pregunta del usuario 18:49): ninguna campaña
                # entrega clips listos; hay que cortar tu propio edit.
                self.assertIn("cortar tu propio edit", e["notas"])
                self.assertIn("no entrega clips listos", e["notas"])
                if name in fama:
                    self.assertIn(fama[name], e["notas"])
                    self.assertTrue(e.get("enabled", True), f"{name} activo")
        dylan = streamers.get_streamer("dylanpage", entries)
        self.assertEqual(dylan["platform"], "youtube")
        self.assertIn("VOD-fallback", dylan["notas"])
        self.assertIn("#DylanPage", dylan["notas"])
        ben = streamers.get_streamer("bendadonnn", entries)
        self.assertIn("BAJO el piso", ben["notas"])
        self.assertIn("SIN prioridad", ben["notas"])
        bobby = streamers.get_streamer("bobby_vanriper", entries)
        self.assertIn("bobby.vanriper", bobby["notas"])  # variante pendiente
        self.assertIn("EXCLUIDO", bobby["notas"])
        self.assertIn("734", bobby["notas"])
        self.assertIn("18:50", bobby["notas"])
        self.assertFalse(bobby.get("enabled", True),
                         "bobby_vanriper desactivado por fama")

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
                {"name": "bueno", "platform": "facebook"}, path=path,
                base_dir=tmp)
            # 2026-11-25 (spec entrada Facebook §3.3): "facebook"
            # es plataforma válida de streamer (antes se rechazaba).
            self.assertTrue(ok, msg)
            ok, msg = streamers.add_streamer(
                {"name": "malo", "platform": "tiktok"}, path=path,
                base_dir=tmp)
            self.assertFalse(ok)
            self.assertIn("twitch, kick, youtube o facebook", msg)
            self.assertTrue(streamers.remove_streamer("nuevo", path, tmp))
            self.assertFalse(streamers.remove_streamer("nuevo", path, tmp))
            self.assertTrue(streamers.remove_streamer("bueno", path, tmp))
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

#!/usr/bin/env python3
"""Categoría GTA VI (regla del usuario 2026-11-18).

Un clip solo es válido si la categoría en la que está streameando
el influencer es GTA VI (Grand Theft Auto VI). Un momento de Just
Chatting (o de otro juego) se descarta, aunque el chat explote.

Fuentes (APIs oficiales, nunca scraping):
- Twitch en vivo: Helix GET /helix/streams?user_login (game_name)
  y GET /helix/channels?broadcaster_id (game_name).
- Twitch VOD por tramos: capítulos del VOD (chat.json
  video.chapters, gameDisplayName).
- Kick: GET https://kick.com/api/v2/channels/<slug> (categoría).

El gate de publicación (policy.evaluate) exige la categoría: sin
categoría verificada se retiene; con otra categoría se descarta.
"""
import json
import os
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from clipagent.category import (
    category_at,
    chapter_category_from_chat,
    effective_category,
    is_gta6,
)
from clipagent.live import kick_channel_category
from clipagent.policy import evaluate
from watcher import TwitchHelix


class TestIsGta6(unittest.TestCase):
    def test_nombres_gta6_valen(self):
        for name in ("GTA VI", "gta vi", "GTA 6", "gta6",
                     "Grand Theft Auto VI", "Grand Theft Auto 6",
                     "Grand Theft Auto VI (GTA 6)", "  gta vi  "):
            self.assertTrue(is_gta6(name), name)

    def test_otros_no_valen(self):
        for name in ("Just Chatting", "GTA V", "Grand Theft Auto V",
                     "Fortnite", "Minecraft", "", None):
            self.assertFalse(is_gta6(name), repr(name))


class TestCategoryAt(unittest.TestCase):
    # Capítulos del VOD 2932415021 (Rubius): Just Chatting 0-167 s,
    # GTA VI desde 167 s.
    CHAPTERS = [
        {"startMilliseconds": 0, "lengthMilliseconds": 167000,
         "gameDisplayName": "Just Chatting"},
        {"startMilliseconds": 167000, "lengthMilliseconds": 3600000,
         "gameDisplayName": "Grand Theft Auto VI"},
    ]

    def test_pico_en_just_chatting(self):
        self.assertEqual(category_at(self.CHAPTERS, 100), "Just Chatting")

    def test_pico_en_gta6(self):
        self.assertEqual(category_at(self.CHAPTERS, 2010),
                         "Grand Theft Auto VI")

    def test_borde_de_capitulo(self):
        self.assertEqual(category_at(self.CHAPTERS, 167),
                         "Grand Theft Auto VI")

    def test_fuera_de_capitulos(self):
        self.assertIsNone(category_at(self.CHAPTERS, 99999))

    def test_sin_capitulos(self):
        self.assertIsNone(category_at([], 100))
        self.assertIsNone(category_at(None, 100))

    def test_capitulos_en_segundos(self):
        ch = [{"start": 0, "length": 167, "game": "Just Chatting"},
              {"start": 167, "length": 3600, "game": "Grand Theft Auto VI"}]
        self.assertEqual(category_at(ch, 2010), "Grand Theft Auto VI")


class TestChapterCategoryFromChat(unittest.TestCase):
    def _chat(self, tmp, chapters):
        path = os.path.join(tmp, "chat.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump({"comments": [],
                       "video": {"chapters": chapters}}, f)
        return path

    def test_lee_capitulo_del_pico(self):
        with tempfile.TemporaryDirectory() as tmp:
            ch = TestCategoryAt.CHAPTERS
            path = self._chat(tmp, ch)
            self.assertEqual(chapter_category_from_chat(path, 100),
                             "Just Chatting")
            self.assertEqual(chapter_category_from_chat(path, 2010),
                             "Grand Theft Auto VI")

    def test_sin_video(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "chat.json")
            with open(path, "w", encoding="utf-8") as f:
                json.dump({"comments": []}, f)
            self.assertIsNone(chapter_category_from_chat(path, 100))


class TestHelixCategory(unittest.TestCase):
    def _helix(self, get_side_effect):
        h = TwitchHelix("cid", "csecret")
        h._get = mock.Mock(side_effect=get_side_effect)
        return h

    def test_live_category_gta6(self):
        h = self._helix(lambda p, q: {"data": [{
            "game_name": "Grand Theft Auto VI", "game_id": "123"}]})
        cat = h.live_category("rubius")
        self.assertTrue(cat["live"])
        self.assertEqual(cat["game_name"], "Grand Theft Auto VI")
        self.assertTrue(is_gta6(cat["game_name"]))

    def test_live_category_offline(self):
        h = self._helix(lambda p, q: {"data": []})
        cat = h.live_category("rubius")
        self.assertFalse(cat["live"])
        self.assertEqual(cat["game_name"], "")

    def test_channel_category(self):
        def get(path, params):
            if path == "/users":
                return {"data": [{"id": "999"}]}
            return {"data": [{"game_name": "Just Chatting",
                              "game_id": "444"}]}
        h = self._helix(get)
        cat = h.channel_category("rubius")
        self.assertEqual(cat["game_name"], "Just Chatting")
        self.assertFalse(is_gta6(cat["game_name"]))


class TestKickCategory(unittest.TestCase):
    def test_categoria_en_vivo(self):
        payload = {"data": {"livestream": {"categories": [
            {"name": "Grand Theft Auto VI"}]}}}
        fn = mock.Mock(return_value=(200, payload))
        self.assertEqual(kick_channel_category("rubius", fn),
                         "Grand Theft Auto VI")

    def test_sin_directo(self):
        fn = mock.Mock(return_value=(200, {"data": {"livestream": None}}))
        self.assertEqual(kick_channel_category("rubius", fn), "")

    def test_error_no_asume(self):
        fn = mock.Mock(return_value=(404, {}))
        self.assertIsNone(kick_channel_category("rubius", fn))


class TestPolicyCategoryGate(unittest.TestCase):
    def _clip(self, category="Grand Theft Auto VI"):
        c = {"duration": 40.0,
             "viral_score": {"total": 80},
             "seo_scores": {"youtube_shorts": 95},
             "title_es": "t", "desc_es": "d", "hashtags_es": "",
             "visible": "gameplay", "lang": "es"}
        if category is not None:
            c["category"] = category
        return c

    def test_gta6_pasa_el_gate(self):
        r = evaluate(self._clip(), "youtube_shorts")
        self.assertEqual(r["decision"], "publicar")

    def test_just_chatting_descarta(self):
        r = evaluate(self._clip("Just Chatting"), "youtube_shorts")
        self.assertEqual(r["decision"], "descartado")
        self.assertIn("GTA VI", r["reason"])

    def test_sin_categoria_no_retiene_en_publicacion(self):
        # Sin categoría no se retiene en publicación: el gate vive
        # en el corte + panel (ver test_policy.py). Una categoría
        # conocida fuera de GTA VI sí descarta.
        r = evaluate(self._clip(None), "youtube_shorts")
        self.assertEqual(r["decision"], "publicar")
        self.assertTrue(any("categor" in check for check in r["checks"]))

    def test_effective_category_orden(self):
        self.assertEqual(
            effective_category({"category": "Grand Theft Auto VI",
                                "game": "Fortnite"}),
            "Grand Theft Auto VI")
        self.assertEqual(effective_category({"game": "GTA 6"}), "GTA 6")
        self.assertEqual(effective_category({}, "GTA VI"), "GTA VI")
        self.assertEqual(effective_category({}), "")


if __name__ == "__main__":
    unittest.main()

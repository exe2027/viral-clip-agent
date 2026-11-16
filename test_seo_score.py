#!/usr/bin/env python3
"""Pruebas del score SEO por plataforma (seo_score.py).

Casos:
  * Paquete bueno (título con keyword al inicio, descripción con
    hook + CTA, tags/hashtags con mezcla, miniatura corta sin
    repetir el título, juego verificado): 90+ en las 4 plataformas.
  * Paquete malo (sin keyword, título >60 car., keyword stuffing,
    solo hashtags genéricos, miniatura que repite el título,
    "gameplay GTA 6" sin verificar): bloqueado, Coherencia 0.
  * Reglas sueltas: floor sin redondeo hacia arriba (89,6 es 89),
    can_publish / override, retry_best, pick_best.

Uso: python3 test_seo_score.py
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from seo_score import (  # noqa: E402
    _floor_total,
    can_publish,
    coherence_component,
    description_component,
    pick_best,
    register_override,
    retry_best,
    score_all_platforms,
    score_platform,
    status_of,
    tags_component,
    thumbnail_component,
    title_component,
)

GOOD_PKG = {
    "title": "GTA 6: la jugada que rompió el chat 😱",
    "description": ("GTA 6: el chat explotó en segundos con esta jugada. "
                    "Clip completo en vertical, sin contexto necesario. "
                    "¿Lo viste venir? Comenta qué harías tú."),
    "tags": ["gta 6", "gta 6 clips", "gta vi", "rockstar games",
             "clips gaming", "stream clips", "gameplay", "gta 6 funny",
             "gta 6 español", "illojuan"],
    "thumbnail_text": "NO PUEDE SER",
    "hashtags": ["#GTA6", "#GTA6Clips", "#ElClipDeGTA6", "#GTA", "#gaming"],
    "game_verified": True, "visible": "gameplay", "lang": "es",
}

BAD_PKG = {
    "title": ("Gameplay GTA 6: mira lo que pasó en el directo de anoche, "
              "no te lo vas a creer para nada"),
    "description": "GTA 6 GTA 6 GTA 6 GTA 6 el mejor momento GTA 6",
    "tags": [],
    "thumbnail_text": "MIRA LO QUE PASÓ",
    "hashtags": ["#fyp", "#viral"],
    "game_verified": False, "visible": "cams-only", "lang": "es",
}


class TestFloorAndStatus(unittest.TestCase):
    def test_floor_never_rounds_up(self):
        self.assertEqual(_floor_total(89.6), 89)
        self.assertEqual(_floor_total(89.0), 89)
        self.assertEqual(_floor_total(94.99), 94)
        self.assertEqual(_floor_total(100.0), 100)

    def test_status_bands(self):
        self.assertEqual(status_of(100), "ideal")
        self.assertEqual(status_of(95), "ideal")
        self.assertEqual(status_of(94), "publicable")
        self.assertEqual(status_of(90), "publicable")
        self.assertEqual(status_of(89), "override")
        self.assertEqual(status_of(70), "override")
        self.assertEqual(status_of(69), "bloqueado")
        self.assertEqual(status_of(0), "bloqueado")


class TestTitleComponent(unittest.TestCase):
    def test_good_title(self):
        value, _notes = title_component(GOOD_PKG["title"], "GTA 6")
        # Keyword al inicio 40 + largo 40-55 (38 car.) 22? No: 38 car.
        # está en 35-39 -> 22; caps 1 (GTA) 15; sustancia 15 = 92.
        self.assertEqual(value, 92.0)

    def test_keyword_late(self):
        value, _n = title_component(
            "La jugada que rompió el chat anoche en el directo en GTA 6")
        # Keyword fuera de los primeros 40 car.: 20 en vez de 40.
        self.assertLess(value, 92.0)

    def test_long_title_penalized(self):
        value, _n = title_component("GTA 6: " + "x" * 70)
        # Keyword 40 + largo >60: 5 + caps 15 + 3 palabras: 5 = 65,
        # por debajo del título bueno (92): el largo penaliza.
        self.assertEqual(value, 65.0)

    def test_unverified_gameplay_claim_minus_20(self):
        ok, _ = title_component("GTA 6: la jugada del año", "GTA 6",
                                game_verified=True, visible="gameplay")
        bad, notes = title_component(
            "Gameplay GTA 6: la jugada del año", "GTA 6",
            game_verified=False, visible="cams-only")
        self.assertLess(bad, ok)
        self.assertTrue(any("-20%" in n for n in notes))


class TestDescriptionComponent(unittest.TestCase):
    def test_good_description(self):
        value, _n = description_component(GOOD_PKG["description"], "GTA 6")
        # Hook+keyword 40 + CTA 30 + cuerpo 15 + sin stuffing 15 = 100.
        self.assertEqual(value, 100.0)

    def test_keyword_stuffing_minus_30(self):
        value, notes = description_component(BAD_PKG["description"], "GTA 6")
        # 40 + 10 + 3 = 53, x0,7 = 37,1.
        self.assertAlmostEqual(value, 37.1, places=1)
        self.assertTrue(any("stuffing" in n for n in notes))

    def test_no_cta(self):
        value, _n = description_component(
            "GTA 6: el chat explotó en segundos con esta jugada. "
            "Clip completo en vertical, sin contexto necesario.")
        self.assertLess(value, 100.0)


class TestTagsComponent(unittest.TestCase):
    def test_youtube_tags(self):
        value, _n = tags_component(GOOD_PKG["tags"], GOOD_PKG["hashtags"],
                                   "youtube_shorts", "es", "GTA 6")
        self.assertEqual(value, 100.0)

    def test_generic_only_capped(self):
        value, notes = tags_component([], ["#fyp", "#viral"],
                                      "tiktok", "es", "GTA 6")
        self.assertLessEqual(value, 20.0)
        self.assertTrue(any("genéricos" in n for n in notes))

    def test_no_hashtags(self):
        value, _n = tags_component([], [], "tiktok", "es", "GTA 6")
        self.assertLess(value, 50.0)


class TestThumbnailComponent(unittest.TestCase):
    def test_good_thumbnail(self):
        value, _n = thumbnail_component("NO PUEDE SER", GOOD_PKG["title"])
        # 3 palabras 40 + no repite 30 + 3 palabras en MAYÚSCULAS
        # (>2 permitidas) 10 = 80. La miniatura por defecto del
        # proyecto tiene 3 palabras en mayúsculas: la regla muerde.
        self.assertEqual(value, 80.0)

    def test_thumbnail_two_caps_is_100(self):
        value, _n = thumbnail_component("NO PUEDE", GOOD_PKG["title"])
        self.assertEqual(value, 100.0)

    def test_repeats_title(self):
        value, _n = thumbnail_component("MIRA LO QUE PASÓ", BAD_PKG["title"])
        self.assertLess(value, 70.0)

    def test_too_many_words(self):
        value, _n = thumbnail_component("ESTO NO TE LO VAS A CREER",
                                        GOOD_PKG["title"])
        self.assertLess(value, 80.0)

    def test_no_thumbnail_is_none(self):
        value, _n = thumbnail_component(None, GOOD_PKG["title"])
        self.assertIsNone(value)


class TestCoherenceComponent(unittest.TestCase):
    def test_unverified_gameplay_is_zero(self):
        value, _n, blocks = coherence_component(
            BAD_PKG["title"], game_verified=False, visible="cams-only")
        self.assertEqual(value, 0.0)
        self.assertTrue(blocks)

    def test_verified_gameplay_is_100(self):
        value, _n, blocks = coherence_component(
            GOOD_PKG["title"], game_verified=True, visible="gameplay")
        self.assertEqual(value, 100.0)
        self.assertFalse(blocks)

    def test_cams_only_naming_game(self):
        value, _n, blocks = coherence_component(
            "GTA 6: no lo podía creer", game_verified=True,
            visible="cams-only")
        self.assertEqual(value, 20.0)
        self.assertTrue(blocks)


class TestScorePlatform(unittest.TestCase):
    def test_good_package_all_platforms(self):
        res = score_all_platforms(GOOD_PKG)
        self.assertEqual(set(res), {"youtube_shorts", "tiktok",
                                    "instagram_reels", "facebook_reels"})
        for platform, r in res.items():
            self.assertGreaterEqual(r["score"], 90, platform)
            self.assertIn(r["status"], ("ideal", "publicable"))
            self.assertTrue(r["can_publish"])
            self.assertEqual(set(r["components"]),
                             {"title", "description", "tags", "thumbnail",
                              "coherence"})
        self.assertEqual(res["youtube_shorts"]["score"], 93)
        self.assertEqual(res["tiktok"]["score"], 96)

    def test_bad_package_blocked(self):
        res = score_all_platforms(BAD_PKG)
        yt = res["youtube_shorts"]
        # Título 60*0,35 + desc 37,1*0,20 + tags 10*0,10 + mini 30*0,20
        # + coherencia 0 = 35,42 -> 35.
        self.assertEqual(yt["score"], 35)
        self.assertEqual(yt["status"], "bloqueado")
        self.assertFalse(yt["can_publish"])
        self.assertEqual(yt["components"]["coherence"]["value"], 0.0)
        self.assertTrue(yt["blocks"])
        for r in res.values():
            self.assertEqual(r["status"], "bloqueado")

    def test_scores_are_ints(self):
        res = score_all_platforms(GOOD_PKG)
        for r in res.values():
            self.assertIsInstance(r["score"], int)

    def test_unknown_platform_raises(self):
        with self.assertRaises(ValueError):
            score_platform("twitch", title="GTA 6: algo")

    def test_thumbnail_none_renormalizes(self):
        # TikTok no tiene miniatura propia: se excluye y los pesos se
        # reparten; el score sigue siendo 0-100.
        r = score_platform("tiktok", **{k: v for k, v in GOOD_PKG.items()
                                        if k != "thumbnail_text"},
                           thumbnail_text=None)
        self.assertIsNone(r["components"]["thumbnail"]["value"])
        self.assertGreaterEqual(r["score"], 90)


class TestCanPublishAndOverride(unittest.TestCase):
    def test_ideal_and_publicable_publish(self):
        self.assertTrue(can_publish({"platform": "tiktok", "status": "ideal"}))
        self.assertTrue(can_publish({"platform": "tiktok", "status": "publicable"}))

    def test_override_needs_record(self):
        r = {"platform": "tiktok", "status": "override"}
        self.assertFalse(can_publish(r))
        self.assertFalse(can_publish(r, overrides={}))
        self.assertTrue(can_publish(r, overrides={"tiktok"}))

    def test_blocked_never_publishes(self):
        r = {"platform": "tiktok", "status": "bloqueado"}
        self.assertFalse(can_publish(r, overrides={"tiktok"}))

    def test_register_override(self):
        manifest = {"clips": []}
        rec = register_override(manifest, "tiktok", 82, "título revisado a mano")
        self.assertEqual(rec["platform"], "tiktok")
        self.assertEqual(rec["score"], 82)
        self.assertEqual(rec["motivo"], "título revisado a mano")
        self.assertIn("timestamp", rec)
        self.assertEqual(len(manifest["seo_overrides"]), 1)
        # El override no cambia el score: solo lo registra.
        self.assertEqual(manifest["seo_overrides"][0]["score"], 82)


class TestRetryBest(unittest.TestCase):
    def test_retry_improves_worst_block(self):
        # El peor bloque del paquete malo es Coherencia (0): el título
        # promete gameplay sin verificar. Arreglar el título (quitar
        # la promesa) sube Título y Coherencia a la vez.
        def fix_title(pkg, worst_block):
            if worst_block not in ("title", "coherence"):
                return None
            fixed = dict(pkg)
            fixed["title"] = "GTA 6: la jugada que rompió el chat 😱"
            return fixed

        out = retry_best(BAD_PKG, fix_title, "youtube_shorts", max_attempts=3)
        self.assertTrue(out["improved"])
        self.assertGreater(out["best_result"]["score"],
                           score_platform("youtube_shorts", **{
                               k: BAD_PKG[k] for k in
                               ("title", "description", "tags",
                                "thumbnail_text", "hashtags",
                                "game_verified", "visible", "lang")})["score"])
        self.assertGreaterEqual(len(out["attempts"]), 2)

    def test_retry_stops_when_no_variant(self):
        out = retry_best(BAD_PKG, lambda pkg, block: None,
                         "youtube_shorts", max_attempts=3)
        self.assertFalse(out["improved"])
        self.assertEqual(len(out["attempts"]), 1)

    def test_retry_stops_at_ideal(self):
        calls = []

        def variant(pkg, block):
            calls.append(block)
            return dict(pkg)

        out = retry_best(GOOD_PKG, variant, "tiktok", max_attempts=3)
        self.assertEqual(len(out["attempts"]), 1)
        self.assertEqual(calls, [])

    def test_pick_best(self):
        best, result = pick_best([BAD_PKG, GOOD_PKG], "youtube_shorts")
        self.assertEqual(best["title"], GOOD_PKG["title"])
        self.assertGreaterEqual(result["score"], 90)
        self.assertEqual(pick_best([], "youtube_shorts"), (None, None))


if __name__ == "__main__":
    unittest.main(verbosity=2)

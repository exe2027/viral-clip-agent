#!/usr/bin/env python3
"""Pruebas de reel_study.py (estudio de reels virales, sin copiarlos).

Fixtures sintéticos + 1 caso real pasado (reel de David Madrazo,
2026-11-12, solo como referencia offline para probar la
clasificación; NUNCA como fuente activa ni clip a encolar):
  * pregunta: "¿Viste lo que pasó?" -> question + comment_question.
  * número: "3 cosas que nadie te contó de GTA 6" -> number
    (el 6 de "GTA 6" solo NO cuenta: "GTA 6 updates" es statement).
  * superlativo: "La jugada más brutal" -> superlative.
  * CTA comenta-palabra: 'comenta "MÁQUINA"' -> comment_word/follow.
  * sin seguidores: engagement None, nunca inventado.
  * títulos sugeridos: solo pasan los que aprueba check_title.

Uso: python3 test_reel_study.py
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from reel_study import (  # noqa: E402
    aggregate_patterns,
    analyze_reel,
    classify_cta,
    classify_hook,
    engagement_rate,
    enrich_package,
    hook_templates,
    normalize_reel,
    outlier_multiple,
    suggest_titles_from_patterns,
)

# Ficha sintética tipo Coker (ver test_viral_score.py / moment_card):
# GTA V RP, qué = "errar un tiro", cita del chat, múltiplo alto.
COKER_CARD = {
    "game": "GTA V RP", "game_label": "GTA V RP",
    "game_source": "test (sintético)",
    "evidence": "medium", "evidence_notes": [],
    "evidence_tokens": ["erra", "tiro", "chat"],
    "transcript_text": "lo erra estando armado y lo erra",
    "what": "errar un tiro", "what_key": "erra",
    "dominant_reaction": "risas", "risas": 14, "hype": 1,
    "viral_multiple": 56.5, "unique_users_peak_30s": 50,
    "chat_msgs_peak_10s": 50,
    "quote_chat": {"text": "ESTANDO ARMADO Y LO ERRA", "count": 2},
    "quote_streamer": {"text": "No, no, no, no, no.", "ts_clip": 19.2},
    "chat_top": [{"text": "estando armado y lo erra", "count": 2}],
    "payoff_ts_clip": 20.0, "clip_duration_s": 60,
    "streamer": "coker", "language": "es",
}


class TestClassifyHook(unittest.TestCase):
    def test_question(self):
        t, _ = classify_hook("¿Viste lo que pasó?\nEl resto del caption.")
        self.assertEqual(t, "question")

    def test_number(self):
        t, _ = classify_hook("3 cosas que nadie te contó de GTA 6")
        self.assertEqual(t, "number")

    def test_gta6_digit_is_not_number_hook(self):
        # El 6 de "GTA 6" es el nombre del juego, no un dato.
        t, _ = classify_hook("Follow @Bars for GTA 6 updates")
        self.assertNotEqual(t, "number")

    def test_handle_digit_is_not_number_hook(self):
        # El 2 de "@madi2hotty" es parte del usuario, no un dato.
        t, _ = classify_hook("Probably the greatest trailer @madi2hotty")
        self.assertNotEqual(t, "number")

    def test_superlative(self):
        t, _ = classify_hook("La jugada más brutal de la noche")
        self.assertEqual(t, "superlative")

    def test_empty(self):
        self.assertEqual(classify_hook("")[0], "none")


class TestClassifyCta(unittest.TestCase):
    def test_comment_word(self):
        t, _ = classify_cta('Sígueme y comenta "MÁQUINA"\nTe enseño todo.')
        self.assertIn(t, ("follow", "comment_word"))

    def test_comment_question(self):
        t, _ = classify_cta("El chat explotó con esto.\n¿Lo viste?")
        self.assertEqual(t, "comment_question")

    def test_none(self):
        t, _ = classify_cta("Watch Grand Theft Auto VI: An Extended Look.")
        self.assertEqual(t, "none")


class TestEngagement(unittest.TestCase):
    def test_rate(self):
        # (1000 likes + 100 comentarios) / 10000 seguidores = 0,11.
        self.assertAlmostEqual(engagement_rate(1000, 100, 10000), 0.11)

    def test_no_followers_is_none(self):
        self.assertIsNone(engagement_rate(1000, 100, None))
        self.assertIsNone(engagement_rate(1000, 100, 0))

    def test_outlier_needs_author_median(self):
        self.assertIsNone(outlier_multiple(1000, None))
        self.assertAlmostEqual(outlier_multiple(3000, 1000), 3.0)


class TestAnalyzeReel(unittest.TestCase):
    def test_david_reel(self):
        # Caso real pasado (2026-11-12): caption con CTA de seguir +
        # comentar palabra, sin hashtags, engagement alto porque la
        # cuenta es pequeña (25.508 seguidores) y el reel explotó.
        reel = {
            "url": "https://www.instagram.com/reel/DfpHUNFiKke/",
            "username": "davidmadrazo.ia",
            "caption": ("Sígueme @davidmadrazo.ia y comenta “MÁQUINA” "
                        "y te enseño por dentro todo lo que hace "
                        "mientras duermo.\n\nLo difícil nunca fue "
                        "editar, era saber qué grabar."),
            "likes": 48720, "comments": 32422, "followers": 25508,
        }
        a = analyze_reel(reel)
        self.assertEqual(a["cta_type"], "follow")
        self.assertEqual(a["hashtag_count"], 0)
        self.assertAlmostEqual(a["engagement_rate"],
                               (48720 + 32422) / 25508, places=3)

    def test_normalize_instagram_cli_keys(self):
        raw = {"post_url": "https://www.instagram.com/reel/X/",
               "username": "bars", "post_caption": "How is this not real life??",
               "like_count": 266484, "comment_count": 2309,
               "follower_count": 2490141}
        a = analyze_reel(raw)
        self.assertEqual(a["url"], "https://www.instagram.com/reel/X/")
        self.assertEqual(a["likes"], 266484)
        self.assertEqual(a["hook_type"], "question")


class TestAggregate(unittest.TestCase):
    def test_small_sample_warns(self):
        pats = aggregate_patterns([analyze_reel(
            {"caption": "¿Viste esto?", "likes": 10, "comments": 1,
             "followers": 100})])
        self.assertEqual(pats["sample_n"], 1)
        self.assertTrue(any("muestra pequeña" in n for n in pats["notes"]))
        self.assertEqual(pats["top_hook_type"], "question")

    def test_empty(self):
        pats = aggregate_patterns([])
        self.assertEqual(pats["sample_n"], 0)
        self.assertIsNone(pats["top_hook_type"])


class TestTemplatesAndTitles(unittest.TestCase):
    def test_templates_only_from_repeated_hook(self):
        pats = aggregate_patterns([
            analyze_reel({"caption": "¿Viste esto?", "likes": 10,
                          "comments": 1, "followers": 100}),
            analyze_reel({"caption": "¿Lo viste venir?", "likes": 20,
                          "comments": 2, "followers": 100}),
        ])
        templates = hook_templates(pats, lang="es", game="GTA 6")
        self.assertTrue(templates)
        self.assertEqual(templates[0]["hook_type"], "question")
        # Plantilla con hueco, no caption copiado.
        self.assertIn("{what}", templates[0]["template"])

    def test_suggested_titles_pass_check(self):
        # Patrón dominante pregunta + ficha Coker: el título
        # sugerido "¿Viste errar un tiro?" debe pasar el check
        # (qué con evidencia, juego real, sin superlativo).
        pats = aggregate_patterns([
            analyze_reel({"caption": "¿Viste esto?", "likes": 10,
                          "comments": 1, "followers": 100}),
            analyze_reel({"caption": "¿Lo viste venir?", "likes": 20,
                          "comments": 2, "followers": 100}),
        ])
        titles = suggest_titles_from_patterns(COKER_CARD, pats, lang="es")
        self.assertTrue(titles)
        self.assertTrue(all(t["check_ok"] for t in titles))
        self.assertFalse(any("GTA 6" in t["title"] for t in titles),
                         "la ficha es GTA V RP: ningún título puede decir GTA 6")

    def test_no_card_no_titles(self):
        pats = aggregate_patterns([
            analyze_reel({"caption": "¿Viste esto?", "likes": 10,
                          "comments": 1, "followers": 100})])
        self.assertEqual(suggest_titles_from_patterns(None, pats), [])

    def test_enrich_package_does_not_change_best_title(self):
        pkg = {"best_title": "GTA V RP: Errar un tiro y el chat explotó",
               "viral_multiple": 56.5}
        pats = aggregate_patterns([
            analyze_reel({"caption": "¿Viste esto?", "likes": 10,
                          "comments": 1, "followers": 100})])
        enriched = enrich_package(pkg, pats, moment_card=COKER_CARD)
        self.assertEqual(enriched["best_title"], pkg["best_title"])
        self.assertIn("reel_study", enriched)
        self.assertEqual(enriched["reel_study"]["sample_n"], 1)


if __name__ == "__main__":
    unittest.main(verbosity=2)

#!/usr/bin/env python3
"""Tests §8 grupos 1-7 y 13 de la spec títulos-verdad (2026-11-25)
+ enmienda 2026-11-26 (Gemini o retención, sin fallback de reglas).

Grupo 1: regresión 213/185/191 con las transcripciones reales de
  runs/titulos-verdad-2026-11-25/evidencia/transcripcion-*-base.txt
  y las fichas reconstruidas. El título generado es el propuesto
  en §7 (o su forma tal como la produce el pipeline — ver nota del
  191 abajo) y NUNCA el viejo; el check rechaza los tres viejos;
  SEO mínimo >= 80 en las 4 plataformas.
Grupo 2: cada frase R2.1 sin evidencia -> rechazada; con "jaja"
  hablado en la transcripción -> pasa; cita literal I3 solo con
  tramo contiguo >= 3 palabras + atribución.
Grupo 3: "risa" sin evidencia en transcripción/chat/ficha falla.
Grupo 4: "imparable"/"máximo"/"desatado" e "insane"/"craziest"
  sueltos fallan aunque multiple >= 5; en cita literal pasan solo
  con tramo >= 3 + atribución.
Grupo 5: cita vacía (espacios, solo comillas, un único token de
  tecleo) no produce candidato `Chat said it: ""` ni `El chat: ""`.
Grupo 6: dedupe 30 días con lista simulada (5 días se rechaza,
  40 días se acepta).
Grupo 7: patrón — los 6 esqueletos observados se rechazan con un
  anterior del mismo esqueleto en ventana; un título con momento
  distinto pasa.
Grupo 8 (R4.4): variedad del paquete — mismo esqueleto en las 2
  primeras líneas de la descripción o en el texto de miniatura
  (misma ventana de 10) retiene el paquete aunque el título sea
  distinto; entradas sin descripción/miniatura no cuentan.
Grupo 13: falta un valor requerido en `titles:` -> ConfigError
  ruidoso (sin default silencioso).

Notas de ajustes medidos (ver §7.4):
- Descripciones 213/185 llevan "GTA 6" una vez en los primeros
  120 car. (sin eso Facebook baja de 80: la descripción no trae
  la keyword). La del 191 también ("in GTA 6"), porque
  _gemini_first_package exige el juego en la descripción.
- Miniatura del 191: la propuesta §7 ("That moan, bro") repite el
  título palabra por palabra y la regla preexistente (miniatura no
  repite el título) la rechaza; se usa "Awkward live moment".
- Título del 191: la propuesta §7 termina en comilla de cierre
  ("...bro?'", 44 car.). El _clean_title viejo de llm.py hacía
  .strip("\"'") y la quitaba; el fix (2026-11-26, integración)
  solo quita un par de comillas que envuelva TODO el título, así
  que la comilla de cierre de la cita interior sobrevive: el
  pipeline produce el título §7 tal cual.
"""
import copy
import json
import os
import sys
import unittest
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from clipagent import llm
from clipagent.config import DEFAULT_CONFIG, load_titles_config
from clipagent.metadata import (
    check_title_detail,
    generate_titles,
    is_duplicate_title,
    is_package_repeat,
    is_pattern_repeat,
    package_clip,
    score_package_seo,
    validate_title_final,
)
from clipagent.validate import ConfigError, validate_config

# --- Transcripciones reales §7 (base, sin timestamps) -------------

T213 = ("que nos responda acá, vamos a seguir con la misión principal, "
        "había que hacerlo de Brian para saltar el banco, eso va a estar guapo "
        "vas a ruinar las relaciones y las escuidas, rey te llamas a esa nuca, "
        "eso va a empezar segundo, he estado con ella literalmente hace 20 minutos, "
        "chill, chill por Dios ¿Dónde está la vuelta?")
T185 = ("Está en el motor de la moto, chicos, está en el motor de la moto, "
        "está en el motor de la moto, está en el motor de la moto. "
        "Me landado, es el mejor easter egg de la historia, ¿qué haces? ¿Qué haces? "
        "¿Qué haces? ¿Qué haces? ¿Qué haces? Yo como le amo la moto, a la lucía ¿no? "
        "¿Qué haces? Vale, un segunito que voy a ver si puedo guardar el outfit y todo eso. "
        "o qué bien chicos la tenemos la tenemos la tenemos se confirma que la tenemos a "
        "todo esto yo tenía que descansar pero no sé si al hacerse de día como que ya "
        "cambiaba el tema esto siga así")
T191 = ("Okay, I guess I got a document then That's it out of focus. Yeah, "
        "that's beautiful. What was that moan bro and there you go "
        "Oh awkward I I got you a I got you a drink but "
        "Okay, yeah, just make me feel fat make me feel fat")

# --- Paquetes §7 (descripciones ajustadas por SEO, ver nota) ------

GEM_213 = {
    "title": "GTA 6: La misión de Brian: saltar el banco",
    "description": "Rubius sigue la misión principal en GTA 6: el trabajo de Brian "
                   "para saltar el banco. \"Eso va a estar guapo\". "
                   "🎬 Clip de rubius en directo. ¿Crees que el atraco sale bien?",
    "hashtags": ["#GTA6", "#Rubius", "#Atraco", "#MisionBrian", "#Shorts"],
    "thumbnail_text": "El atraco de Brian",
}
GEM_185 = {
    "title": "GTA 6: Grefg: el mejor easter egg está en la moto",
    "description": "Grefg encuentra en el motor de la moto de GTA 6 lo que llama "
                   "\"el mejor easter egg de la historia\". Y encima quiere guardar "
                   "el outfit. 🎬 Clip de thegrefg en directo. "
                   "¿Tú también lo habías visto?",
    "hashtags": ["#GTA6", "#Grefg", "#EasterEgg", "#Moto", "#Shorts"],
    "thumbnail_text": "Motor de la moto",
}
GEM_191 = {
    "title": "GTA 6: Burn asks: 'What was that moan, bro?'",
    "description": "Burn reacts to an awkward live moment in GTA 6: "
                   "\"What was that moan, bro?\" and \"make me feel fat\". "
                   "🎬 Clip de burn en directo. Did you catch that too?",
    "hashtags": ["#GTA6", "#Burn", "#Moan", "#Awkward", "#Shorts"],
    "thumbnail_text": "Awkward live moment",
}
# Título que produce de verdad el pipeline para el 191 (ver nota):
TITLE_191_PRODUCED = "GTA 6: Burn asks: 'What was that moan, bro?'"

OLD_TITLES = {
    "213": "GTA 6: La risa imparable de Rubius",
    "185": "GTA 6: La risa de Grefg con el chat",
    "191": "GTA 6: Chat said it: \"\"",
}


class FakeLLM(llm.LLMClient):
    """LLM de pruebas: devuelve el paquete que se le pasa."""

    def __init__(self, payload):
        self.payload = payload

    def available(self):
        return True

    def generate(self, prompt):
        if isinstance(self.payload, str):
            return self.payload
        return json.dumps(self.payload)


class SilentLLM(llm.LLMClient):
    """LLM disponible que no responde nada útil -> retención."""

    def available(self):
        return True

    def generate(self, prompt):
        return ""


def _card(streamer, transcript, lang="es", **over):
    card = {
        "streamer": streamer, "game": "GTA 6", "game_label": "GTA 6",
        "game_source": "categoría de Twitch", "evidence": "medium",
        "evidence_notes": [], "evidence_tokens": [],
        "transcript_text": transcript, "what": None, "what_key": None,
        "dominant_reaction": "risas", "risas": 0, "hype": 0,
        "viral_multiple": 1.0, "payoff_ts_clip": 20.0,
        "clip_duration_s": 30, "unique_users_peak_30s": 10,
        "chat_msgs_peak_10s": 5, "quote_chat": None,
        "quote_streamer": None, "chat_top": [], "language": lang,
        "vod_id": "v1",
    }
    card.update(over)
    return card


def _package(card, lang, gemini, recent_titles=None, **over):
    return package_clip(1, 100, 90.0, 0, 0, 10.0, [90.0] * 10, lang=lang,
                        moment_card=card, game="GTA 6",
                        streamer=card["streamer"], vod_id="v1",
                        visible="gameplay", llm_client=gemini,
                        recent_titles=recent_titles, **over)


def _seo_scores(pkg, lang):
    seo = score_package_seo(pkg, lang=lang)
    return {p: (r or {}).get("score") for p, r in seo.items()}


class TestGroup1Regression213185191(unittest.TestCase):
    def test_213_proposed_title_and_seo(self):
        card = _card("rubius", T213, "es")
        pkg = _package(card, "es", FakeLLM(GEM_213), recent_titles=[])
        print(f"\n[213] producido: {pkg['best_title']!r} "
              f"SEO={_seo_scores(pkg, 'es')}")
        self.assertEqual(pkg["best_title"], GEM_213["title"])
        self.assertNotEqual(pkg["best_title"], OLD_TITLES["213"])
        self.assertEqual(pkg["pkg_source"], "gemini")
        self.assertFalse(pkg["needs_human_review"])
        self.assertTrue(pkg["dedupe_verified"])
        self.assertEqual(pkg["hashtags"], GEM_213["hashtags"])
        self.assertEqual(pkg["thumbnail_text"], GEM_213["thumbnail_text"])
        self.assertEqual(len(pkg["best_title"]), 42)
        self.assertTrue(40 <= len(pkg["best_title"]) <= 55)
        for platform, score in _seo_scores(pkg, "es").items():
            self.assertGreaterEqual(score, 80, platform)

    def test_185_proposed_title_and_seo(self):
        card = _card("thegrefg", T185, "es")
        pkg = _package(card, "es", FakeLLM(GEM_185), recent_titles=[])
        print(f"\n[185] producido: {pkg['best_title']!r} "
              f"SEO={_seo_scores(pkg, 'es')}")
        self.assertEqual(pkg["best_title"], GEM_185["title"])
        self.assertNotEqual(pkg["best_title"], OLD_TITLES["185"])
        self.assertEqual(pkg["pkg_source"], "gemini")
        self.assertFalse(pkg["needs_human_review"])
        self.assertTrue(pkg["dedupe_verified"])
        self.assertEqual(pkg["hashtags"], GEM_185["hashtags"])
        self.assertEqual(pkg["thumbnail_text"], GEM_185["thumbnail_text"])
        self.assertTrue(40 <= len(pkg["best_title"]) <= 55)
        for platform, score in _seo_scores(pkg, "es").items():
            self.assertGreaterEqual(score, 80, platform)

    def test_191_proposed_title_and_seo(self):
        card = _card("burn", T191, "en")
        pkg = _package(card, "en", FakeLLM(GEM_191), recent_titles=[])
        print(f"\n[191] producido: {pkg['best_title']!r} "
              f"SEO={_seo_scores(pkg, 'en')}")
        # Ver nota del módulo: _clean_title (llm.py) quita la
        # comilla de cierre final del título §7.
        self.assertEqual(pkg["best_title"], TITLE_191_PRODUCED)
        self.assertNotEqual(pkg["best_title"], OLD_TITLES["191"])
        self.assertEqual(pkg["pkg_source"], "gemini")
        self.assertFalse(pkg["needs_human_review"])
        self.assertTrue(pkg["dedupe_verified"])
        self.assertEqual(pkg["hashtags"], GEM_191["hashtags"])
        self.assertEqual(pkg["thumbnail_text"], GEM_191["thumbnail_text"])
        self.assertTrue(40 <= len(pkg["best_title"]) <= 55)
        for platform, score in _seo_scores(pkg, "en").items():
            self.assertGreaterEqual(score, 80, platform)

    def test_old_titles_rejected(self):
        cases = [
            ("213", "es", _card("rubius", T213, "es")),
            ("185", "es", _card("thegrefg", T185, "es")),
            ("191", "en", _card("burn", T191, "en")),
        ]
        for name, lang, card in cases:
            with self.subTest(clip=name):
                v = validate_title_final(OLD_TITLES[name], card,
                                         visible="gameplay", lang=lang)
                self.assertFalse(v["ok"], "; ".join(v["reasons"]))
                self.assertIsNone(v["degraded_title"])

    def test_no_gemini_means_retention(self):
        card = _card("rubius", T213, "es")
        pkg = _package(card, "es", SilentLLM(), recent_titles=[])
        self.assertIsNone(pkg["best_title"])
        self.assertTrue(pkg["needs_human_review"])
        self.assertEqual(pkg["pkg_source"], "retained")
        self.assertEqual(pkg["description"], "")
        self.assertEqual(pkg["hashtags"], [])
        self.assertEqual(pkg["thumbnail_text"], "")
        self.assertFalse(pkg["dedupe_verified"])


class TestGroup2Cliches(unittest.TestCase):
    ES_NO_EVIDENCE = _card("rubius", T213, "es")
    EN_NO_EVIDENCE = _card(
        "burn",
        "Okay, I guess I got a document then That's it out of focus. "
        "Yeah, that's beautiful.", "en")

    ES_CLICHES = [
        "La risa imparable de Rubius",
        "El chat no paraba de reírse",
        "Se murieron de risa",
        "El chat se murió de risa con esto",
        "El chat enloqueció con esto",
        "El chat explotó con esto",
        "La risa de Rubius con el chat",
        "No lo podía creer",
        "El momento que el chat clippeó",
        "Mira por qué reaccionó el chat",
        "Rubius hizo reír al chat",
        "Nadie se lo creyó",
        "El hype máximo",
        "El hype desatado",
        "La reacción total",
    ]
    EN_CLICHES = [
        "Chat couldn't stop laughing",
        "Chat went crazy for this",
        "Chat exploded over this",
        "Nobody expected this",
        "Everyone laughed at this",
        "The moment chat clipped",
        "Watch what chat reacted to",
        "Rubius broke chat",
        # "Chat reacted to this" NO es frase R2.1 (la lista trae
        # "watch what chat reacted to"); no va en este grupo.
        "The hype was insane",
        "The hype was unreal",
        "Craziest moment ever",
    ]

    def test_es_cliches_without_evidence_rejected(self):
        for phrase in self.ES_CLICHES:
            title = f"GTA 6: {phrase}"
            with self.subTest(title=title):
                v = validate_title_final(title, self.ES_NO_EVIDENCE,
                                         visible="gameplay", lang="es")
                self.assertFalse(v["ok"], "; ".join(v["reasons"]))

    def test_en_cliches_without_evidence_rejected(self):
        for phrase in self.EN_CLICHES:
            title = f"GTA 6: {phrase}"
            with self.subTest(title=title):
                v = validate_title_final(title, self.EN_NO_EVIDENCE,
                                         visible="gameplay", lang="en")
                self.assertFalse(v["ok"], "; ".join(v["reasons"]))

    def test_same_title_with_spoken_laughter_passes(self):
        # "jaja" hablado = evidencia de risa (§3.3) para el cliché;
        # el superlativo "imparable" exige además multiple >= 5.
        card = _card("rubius",
                     T213 + " jaja, no me lo puedo creer, jaja, "
                     "qué risa, me muero de risa", "es",
                     viral_multiple=8.0)
        v = validate_title_final("GTA 6: La risa imparable de Rubius",
                                 card, visible="gameplay", lang="es")
        self.assertTrue(v["ok"], "; ".join(v["reasons"]))

    def test_literal_quote_i3_passes_with_span_and_attribution(self):
        card = _card("thegrefg", T185, "es")
        v = validate_title_final(
            "GTA 6: Grefg: el mejor easter egg está en la moto",
            card, visible="gameplay", lang="es")
        self.assertTrue(v["ok"], "; ".join(v["reasons"]))

    def test_literal_quote_word_alone_fails(self):
        card = _card("thegrefg", T185, "es")
        v = validate_title_final("GTA 6: El mejor atraco de Grefg",
                                 card, visible="gameplay", lang="es")
        self.assertFalse(v["ok"], "; ".join(v["reasons"]))

    def test_literal_quote_without_attribution_fails(self):
        card = _card("thegrefg", T185, "es")
        v = validate_title_final(
            "GTA 6: El mejor easter egg de la historia",
            card, visible="gameplay", lang="es")
        self.assertFalse(v["ok"], "; ".join(v["reasons"]))

    def test_superlative_outside_quote_without_chat_evidence_fails(self):
        card = _card("rubius", T213, "es")
        v = validate_title_final("GTA 6: El hype máximo de Rubius",
                                 card, visible="gameplay", lang="es")
        self.assertFalse(v["ok"], "; ".join(v["reasons"]))


class TestGroup3RisaWithoutEvidence(unittest.TestCase):
    def test_risa_without_evidence_fails(self):
        card = _card("rubius", T213, "es")
        v = validate_title_final("GTA 6: La risa de Rubius en el atraco",
                                 card, visible="gameplay", lang="es")
        self.assertFalse(v["ok"], "; ".join(v["reasons"]))

    def test_risa_with_chat_laughter_tokens_passes_noun_check(self):
        card = _card("rubius", T213, "es",
                     chat_top=[{"text": "jajaja qué bueno", "count": 12}])
        detail = check_title_detail("GTA 6: La risa de Rubius en el atraco",
                                    card)
        self.assertTrue(detail["ok"], "; ".join(detail["reasons"]))


class TestGroup4Superlatives(unittest.TestCase):
    def test_es_superlatives_without_evidence_fail(self):
        card = _card("rubius", T213, "es")
        for word in ("imparable", "máximo", "desatado"):
            title = f"GTA 6: El hype {word} de Rubius"
            with self.subTest(title=title):
                v = validate_title_final(title, card,
                                         visible="gameplay", lang="es")
                self.assertFalse(v["ok"], "; ".join(v["reasons"]))

    def test_en_superlatives_word_alone_fail_even_with_multiple(self):
        card = _card("burn", T191, "en", viral_multiple=8.0)
        for title in ("GTA 6: The insane hype",
                      "GTA 6: The craziest moment"):
            with self.subTest(title=title):
                v = validate_title_final(title, card,
                                         visible="gameplay", lang="en")
                self.assertFalse(v["ok"], "; ".join(v["reasons"]))

    def test_en_superlative_in_literal_quote_passes(self):
        card = _card("speed",
                     "this is insane, this is the craziest moment ever, "
                     "I cannot believe what just happened", "en")
        v = validate_title_final(
            "GTA 6: Speed: this is the craziest moment ever",
            card, visible="gameplay", lang="en")
        self.assertTrue(v["ok"], "; ".join(v["reasons"]))


class TestGroup5EmptyQuote(unittest.TestCase):
    def _titles_with(self, quote_text, lang="es"):
        card = _card("rubius", T213, lang,
                     quote_chat={"text": quote_text, "count": 5},
                     chat_msgs_peak_10s=30)
        return [t["title"] for t in generate_titles(card, lang=lang, n=10)]

    def test_spaces_only_quote_produces_no_chat_said_it(self):
        titles = self._titles_with("   ")
        self.assertNotIn("GTA 6: El chat: \"\"", titles)
        self.assertTrue(all('""' not in t for t in titles), titles)

    def test_quotes_only_quote_produces_no_chat_said_it(self):
        titles = self._titles_with('""')
        self.assertNotIn("GTA 6: El chat: \"\"", titles)
        self.assertTrue(all('""' not in t for t in titles), titles)

    def test_single_typing_token_produces_no_chat_said_it(self):
        titles = self._titles_with("w")
        self.assertNotIn("GTA 6: El chat: \"\"", titles)
        self.assertTrue(all('""' not in t for t in titles), titles)

    def test_en_spaces_only_quote_produces_no_chat_said_it(self):
        card = _card("burn", T191, "en",
                     quote_chat={"text": "   ", "count": 5},
                     chat_msgs_peak_10s=30)
        titles = [t["title"] for t in generate_titles(card, lang="en", n=10)]
        self.assertTrue(all('""' not in t for t in titles), titles)
        self.assertTrue(all("Chat said it" not in t or '""' not in t
                            for t in titles), titles)


class TestGroup6Dedupe30Days(unittest.TestCase):
    TITLE = "GTA 6: La misión de Brian: saltar el banco"

    def _recent(self, days_ago):
        when = (datetime.now(timezone.utc)
                - timedelta(days=days_ago)).isoformat()
        return [{"title": self.TITLE, "language": "es",
                 "created_at": when}]

    def test_title_from_5_days_ago_is_duplicate(self):
        self.assertTrue(is_duplicate_title(
            self.TITLE, recent_titles=self._recent(5), language="es"))

    def test_title_from_40_days_ago_is_not_duplicate(self):
        self.assertFalse(is_duplicate_title(
            self.TITLE, recent_titles=self._recent(40), language="es"))

    def test_package_with_recent_duplicate_is_retained(self):
        card = _card("rubius", T213, "es")
        pkg = _package(card, "es", FakeLLM(GEM_213),
                       recent_titles=self._recent(5))
        self.assertIsNone(pkg["best_title"])
        self.assertTrue(pkg["needs_human_review"])
        self.assertEqual(pkg["pkg_source"], "retained")


class TestGroup7Pattern(unittest.TestCase):
    CASES = [
        ("GTA 6: El hype máximo de Rubius",
         ["GTA 6: El hype desatado de IlloJuan"]),
        ("GTA 6: El chat enloqueció con esto",
         ["GTA 6: El chat explotó con esto"]),
        ("GTA 6: Chat said it: 'qué locura'",
         ["GTA 6: Chat said it: 'no puede ser'"]),
        ("GTA 6: La risa de Grefg con el chat",
         ["GTA 6: La risa de Rubius"]),
        ("GTA 6: El hype desatado",
         ["GTA 6: El hype máximo"]),
        ("GTA 6: El chat explotó",
         ["GTA 6: El chat enloqueció"]),
    ]

    def test_same_skeleton_in_window_is_pattern_repeat(self):
        for title, recent in self.CASES:
            with self.subTest(title=title):
                self.assertTrue(is_pattern_repeat(title, recent))

    def test_different_moment_passes(self):
        recent = [r for _, rs in self.CASES for r in rs]
        self.assertFalse(is_pattern_repeat(
            "GTA 6: La misión de Brian: saltar el banco", recent))

    def test_positional_window_arg_is_tolerated(self):
        # llm.py llama is_pattern_repeat(title, recent, 10).
        self.assertTrue(is_pattern_repeat(
            "GTA 6: El hype máximo de Rubius",
            ["GTA 6: El hype desatado de IlloJuan"], 10))


class TestGroup8PackageVariety(unittest.TestCase):
    """R4.4: variedad en las 2 primeras líneas de la descripción
    y en el texto de miniatura — mismo esqueleto, misma ventana
    de 10 que R4.2. El cierre fijo («¿Te lo esperabas?
    Suscríbete...») y la miniatura repetida son el patrón que
    esto caza; el título distinto no salva el paquete."""

    def _recent(self, description, thumb_text):
        return [{"title": "GTA 6: Otro momento distinto",
                 "language": "es",
                 "created_at": datetime.now(timezone.utc).isoformat(),
                 "description": description,
                 "thumb_text": thumb_text}]

    def test_same_description_opening_is_package_repeat(self):
        # Las 2 primeras líneas idénticas (misma apertura) aunque
        # el resto de la descripción cambie: es el mismo patrón.
        # La descripción reciente se escribe con saltos de línea
        # para que «primeras 2 líneas» sea literal.
        recent_desc = ("Rubius sigue la misión principal en GTA 6: "
                       "el trabajo de Brian para saltar el banco.\n"
                       "\"Eso va a estar guapo\".\n"
                       "🎬 Clip de rubius en directo. "
                       "¿Crees que el atraco sale bien?")
        new_desc = ("Rubius sigue la misión principal en GTA 6: "
                    "el trabajo de Brian para saltar el banco.\n"
                    "\"Eso va a estar guapo\".\n"
                    "🎬 Clip de rubius en directo. "
                    "¿Otra pregunta distinta?")
        self.assertTrue(is_package_repeat(
            new_desc, "Otro texto distinto",
            self._recent(recent_desc, "Miniatura distinta")))

    def test_same_thumbnail_is_package_repeat(self):
        # Misma miniatura («El atraco de Brian») con descripción
        # distinta: también es patrón.
        self.assertTrue(is_package_repeat(
            "Una descripción totalmente distinta sobre otro momento "
            "en GTA 6 sin relación con el atraco.",
            "El atraco de Brian", self._recent(
                "Otra descripción cualquiera en GTA 6.",
                GEM_213["thumbnail_text"])))

    def test_different_description_and_thumbnail_pass(self):
        self.assertFalse(is_package_repeat(
            GEM_213["description"], GEM_213["thumbnail_text"],
            self._recent(
                "Grefg encuentra en el motor de la moto de GTA 6 "
                "lo que llama el mejor easter egg.",
                "Motor de la moto")))

    def test_entries_without_description_or_thumbnail_do_not_count(self):
        self.assertFalse(is_package_repeat(
            GEM_213["description"], GEM_213["thumbnail_text"],
            [{"title": "GTA 6: Otro momento distinto",
              "language": "es",
              "created_at": datetime.now(timezone.utc).isoformat()}]))

    def test_package_with_repeated_description_is_retained(self):
        card = _card("rubius", T213, "es")
        pkg = _package(card, "es", FakeLLM(GEM_213),
                       recent_titles=self._recent(
                           GEM_213["description"], "Miniatura distinta"))
        self.assertIsNone(pkg["best_title"])
        self.assertTrue(pkg["needs_human_review"])
        self.assertEqual(pkg["pkg_source"], "retained")

    def test_package_with_repeated_thumbnail_is_retained(self):
        card = _card("rubius", T213, "es")
        pkg = _package(card, "es", FakeLLM(GEM_213),
                       recent_titles=self._recent(
                           "Otra descripción cualquiera en GTA 6.",
                           GEM_213["thumbnail_text"]))
        self.assertIsNone(pkg["best_title"])
        self.assertTrue(pkg["needs_human_review"])


class TestGroup13TitlesConfig(unittest.TestCase):
    def test_missing_dedupe_days_raises(self):
        cfg = copy.deepcopy(DEFAULT_CONFIG)
        del cfg["titles"]["dedupe_days"]
        with self.assertRaises(ConfigError):
            load_titles_config(cfg)

    def test_missing_whole_section_raises(self):
        cfg = copy.deepcopy(DEFAULT_CONFIG)
        del cfg["titles"]
        with self.assertRaises(ConfigError):
            load_titles_config(cfg)

    def test_empty_cliches_list_raises(self):
        cfg = copy.deepcopy(DEFAULT_CONFIG)
        cfg["titles"]["cliches_es"] = []
        with self.assertRaises(ConfigError):
            load_titles_config(cfg)

    def test_bad_title_len_raises(self):
        cfg = copy.deepcopy(DEFAULT_CONFIG)
        cfg["titles"]["title_len"] = [55, 40]
        with self.assertRaises(ConfigError):
            load_titles_config(cfg)

    def test_validate_config_rejects_missing_key(self):
        cfg = copy.deepcopy(DEFAULT_CONFIG)
        del cfg["titles"]["gemini_retries"]
        with self.assertRaises(ConfigError):
            validate_config(cfg)


if __name__ == "__main__":
    unittest.main(verbosity=2)

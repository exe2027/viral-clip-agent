#!/usr/bin/env python3
"""Tests de clipagent/humanize.py (adopción 1, 2026-11-26).

Port de humanize.py + detect.py de instagram-agent-skill (0 tests en
el origen) con lexicón español desde textos reales del proyecto
(ver runs/adopciones-2026-11-26/PLAN-1-humanizador.md §3).

Calibración medida (2026-11-26, con este lexicón):
- Texto EN malo del estilo del README: 15,5 FLAGGED -> tras humanize
  62,0 REVIEW (el ejemplo del README original iba 32,5 -> 79,7; el
  texto aquí es más corto y el VOICE manda menos).
- Texto EN limpio (variado, con números y nombres): 77,0 PASS.
- Texto ES limpio (variado, con números y nombres): 57,4 REVIEW.
  El check VOICE está afinado para inglés (contracciones): en
  español no hay contracciones y el VOICE se queda en ~30, así que
  un texto ES limpio rara vez pasa de REVIEW. Solo FLAGGED retiene;
  REVIEW es informativo. Los umbrales NO se retocan para forzar
  PASS en español (sería inventar una calibración sin corpus).
- Títulos reales de los fixtures (213/185/191 + hookscore): 62,0
  REVIEW (textos cortos: BURSTINESS/SPECIFICITY/VOICE devuelven 50
  "too short to judge"); nunca FLAGGED.
"""
import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from clipagent.humanize import (
    HOLD_REASON_HUMANIZE,
    check_slop,
    detect_package_texts,
    humanize,
    humanize_title,
    load_lexicon,
    run,
    scan_structures,
)
from clipagent.metadata import package_clip
from test_titles_verdad import GEM_185, GEM_191, GEM_213, FakeLLM, _card

# Texto malo estilo README del origen: muletillas EN + tipografía
# de máquina + frases parejas. Sin humanizar: FLAGGED.
BAD_EN = (
    "In today's fast-paced world, it is worth noting that we need to "
    "delve into this. Moreover, the future is here. Let that sink in. "
    "Furthermore, it is a game-changer that will elevate your content "
    "seamlessly. At the end of the day, follow for more."
)

# Texto EN limpio: frases de longitud variada, números, nombres,
# contracciones, persona. Humanizado: 77,0 PASS (medido).
CLEAN_EN = (
    "I watched Rubius try the bank job three times. He failed twice. "
    "Then Brian's plan finally clicked on the third run, and chat "
    "went wild for a solid 20 seconds while the alarm screamed. "
    "It wasn't luck. It was timing: he waited for the guard to turn, "
    "sprinted across the lot, and grabbed the keys with 5 seconds "
    "left on the clock. "
    "You could hear the relief in his voice when the door opened. "
    "That is the clip. He laughed, looked at the camera, and said "
    "the crew owed him dinner. "
    "Three attempts. One clean getaway. 40,000 people watching live."
)

# Texto ES limpio: frases de longitud variada, números, nombres.
# Humanizado: 57,4 REVIEW (medido); VOICE 30,0 es el suelo ES.
CLEAN_ES = (
    "Rubius sigue la misión principal en GTA 6: el trabajo de Brian "
    "para saltar el banco. Lo intenta tres veces. Falla dos. "
    "Al tercer intento el plan sale, y el chat explota justo cuando "
    "abre la puerta, con 40.000 personas mirando en directo. "
    "Brian le dice que no vuelva a improvisar. Rubius se ríe. Mira a "
    "la cámara y promete que el próximo atraco sale a la primera. "
    "Son 20 segundos de tensión, un giro y un final que nadie "
    "esperaba."
)

# Títulos reales de los fixtures (nunca FLAGGED tras humanizar).
REAL_TITLES = [
    "GTA 6: La misión de Brian: saltar el banco",  # 213 propuesto
    "GTA 6: Grefg: el mejor easter egg está en la moto",  # 185
    "GTA 6: Burn asks: 'What was that moan, bro?'",  # 191
    "GTA 6: Rubius descubre cómo remolcar la moto acuática",
    "GTA 6: TheGrefg y la patada nuclear imposible",
    "GTA 6: AuronPlay «No es una cinemática»!!",
]


class TestInvisible(unittest.TestCase):
    def test_zero_width_y_bom_se_borran(self):
        text = "a\u200bb\u200cc\u200dd\ufeffe"
        clean, report = humanize(text)
        self.assertEqual(clean, "abcde\n")
        self.assertTrue(any(h["count"] >= 1
                            for h in report["invisible"]))

    def test_tag_chars_se_borran(self):
        text = "hola\U000e0041\U000e0042mundo"
        clean, _report = humanize(text)
        self.assertEqual(clean, "holamundo\n")

    def test_nbsp_se_vuelve_espacio(self):
        clean, _report = humanize("a\u00a0b\u202fc\u2009d")
        self.assertEqual(clean, "a b c d\n")

    def test_otro_cf_se_borra(self):
        # U+2064 INVISIBLE PLUS: Cf no listado en el lexicón.
        clean, report = humanize("a\u2064b")
        self.assertEqual(clean, "ab\n")
        self.assertTrue(any("other invisible" in h["name"]
                            for h in report["invisible"]))

    def test_urls_protegidas(self):
        # Dentro de una URL no se reescribe nada.
        text = "mira https://ejemplo.com/delve-into-x y delve into y"
        clean, _report = humanize(text)
        self.assertIn("https://ejemplo.com/delve-into-x", clean)
        self.assertIn("look at y", clean)


class TestTypographic(unittest.TestCase):
    def test_em_dash_a_coma(self):
        clean, _r = humanize("Rubius — en directo — lo hizo")
        self.assertEqual(clean, "Rubius, en directo, lo hizo\n")

    def test_en_dash_rango_y_como_em_dash(self):
        clean, _r = humanize("5–10 minutos – y sigue")
        self.assertEqual(clean, "5-10 minutos, y sigue\n")

    def test_comillas_curvas_a_rectas_y_elipsis(self):
        clean, _r = humanize("“Eso va a estar guapo…” dijo")
        self.assertEqual(clean, '"Eso va a estar guapo..." dijo\n')

    def test_humanize_title_solo_invisible_y_tipografico(self):
        # El título NO pasa por el pase léxico: las palabras no
        # cambian (no invalida la validación ni la cita I3).
        title = "GTA 6: La misión de Brian — saltar el banco"
        self.assertEqual(
            humanize_title(title),
            "GTA 6: La misión de Brian, saltar el banco")
        # Un zero-width entre palabra y espacio se borra sin dejar
        # rastro; dentro de una palabra se borra sin meter espacio
        # (fiel al original: la acción es "delete").
        self.assertEqual(
            humanize_title("GTA 6: hype\u200b máximo de Rubius"),
            "GTA 6: hype máximo de Rubius")
        self.assertEqual(
            humanize_title("GTA 6: hype\u200bmáximo de Rubius"),
            "GTA 6: hypemáximo de Rubius")


class TestLexiconEN(unittest.TestCase):
    def test_delve_y_opener(self):
        clean, _r = humanize(
            "In today's fast-paced world, we delve into this.")
        self.assertEqual(clean, "Right now, we look at this.\n")

    def test_it_is_worth_noting_se_borra(self):
        clean, _r = humanize(
            "It is worth noting that the plan worked.")
        self.assertEqual(clean, "Note that the plan worked.\n")

    def test_follow_for_more_se_borra(self):
        clean, _r = humanize("Great clip. Follow for more.")
        self.assertEqual(clean, "Great clip.\n")


class TestLexiconES(unittest.TestCase):
    def test_equivalentes_es(self):
        cases = [
            ("Sumérgete en el atraco.", "Mira el atraco."),
            ("En el mundo actual, todo cambia.",
             "Ahora, todo cambia."),
            ("Es importante destacar que salió bien.",
             "Salió bien."),
            ("Nadie está hablando de esto.", "Poca gente hace esto."),
            ("Sígueme para más.", ""),
            ("En conclusión, salió bien.", "Salió bien."),
            ("No vas a creer lo que pasó.", "Lo que pasó."),
            ("Quédate hasta el final.", ""),
            ("Al final del día, salió bien.", "Salió bien."),
        ]
        for src, expected in cases:
            with self.subTest(src=src):
                clean, _r = humanize(src)
                self.assertEqual(clean, expected + "\n")

    def test_estructuras_es_se_reportan(self):
        flags = scan_structures(
            "No es solo un gol, es el gol del año.", load_lexicon())
        self.assertTrue(any("No es solo" in f["name"] for f in flags))
        flags = scan_structures(
            "En este vídeo te voy a enseñar cómo se hace.",
            load_lexicon())
        self.assertTrue(any("Preámbulo" in f["name"] for f in flags))

    def test_cliches_r21_detect_only(self):
        # humanize NO los toca (lavarían un título prohibido tras
        # la validación); detect SÍ los cuenta como slop.
        lex = load_lexicon()
        for cliche in ("hype máximo", "el chat explotó",
                       "no lo podía creer", "el chat enloqueció"):
            with self.subTest(cliche=cliche):
                clean, _r = humanize(f"GTA 6: {cliche} de Rubius")
                self.assertIn(cliche, clean)
                _score, detail = check_slop(
                    f"GTA 6: {cliche} de Rubius", lex)
                self.assertIn(cliche, detail)


class TestFillerHashtags(unittest.TestCase):
    def test_relleno_se_borra_y_shorts_sobrevive(self):
        tags = "#GTA6 #Rubius #viral #fyp #Shorts"
        clean = humanize(tags)[0].split()
        self.assertEqual(clean, ["#GTA6", "#Rubius", "#Shorts"])

    def test_hashtag_no_casa_por_prefijo(self):
        # #viral no debe borrar #viralvideo (frontera de palabra:
        # tras "viral" viene "v", sin frontera). (#viralreels SÍ es
        # relleno y se borra: está en FILLER_TAGS.)
        clean = humanize("#viralvideo #Shorts")[0].split()
        self.assertEqual(clean, ["#viralvideo", "#Shorts"])


class TestVerdicts(unittest.TestCase):
    def test_texto_malo_flagged_y_humanizado_suba(self):
        _r, overall_bad, verdict_bad = run(BAD_EN)
        self.assertEqual(verdict_bad, "FLAGGED")
        self.assertLess(overall_bad, 50.0)
        clean = humanize(BAD_EN)[0]
        _r, overall, verdict = run(clean)
        self.assertGreater(overall, overall_bad)
        self.assertNotEqual(verdict, "FLAGGED")

    def test_texto_en_limpio_pass(self):
        clean = humanize(CLEAN_EN)[0]
        _r, overall, verdict = run(clean)
        self.assertEqual(verdict, "PASS")
        self.assertGreaterEqual(overall, 70.0)

    def test_texto_es_limpio_no_flagged(self):
        # ES limpio: REVIEW por el suelo de VOICE (~30, sin
        # contracciones). Solo FLAGGED retiene; REVIEW no bloquea.
        clean = humanize(CLEAN_ES)[0]
        _r, overall, verdict = run(clean)
        self.assertIn(verdict, ("REVIEW", "PASS"))
        self.assertGreaterEqual(overall, 50.0)

    def test_umbrales_pass_review_flagged(self):
        # PASS exige media >= 70 Y ningún check < 55; REVIEW >= 50.
        results, overall, verdict = run(humanize(CLEAN_EN)[0])
        self.assertEqual(verdict, "PASS")
        self.assertGreaterEqual(min(v[0] for v in results.values()),
                                55.0)
        self.assertGreaterEqual(overall, 70.0)

    def test_descripcion_corta_con_cliches_flagged(self):
        # Descripción corta saturada de clichés R2.1 (detect_only):
        # densidad de slop alta -> FLAGGED aunque el resto pase.
        desc = ("Rubius sigue la misión principal en GTA 6. "
                "El chat explotó, hype máximo, nadie se lo creía, "
                "el chat enloqueció con esto.")
        _r, overall, verdict = run(humanize(desc)[0])
        self.assertEqual(verdict, "FLAGGED")
        self.assertLess(overall, 50.0)

    def test_descripcion_corta_fragmentada_no_flagged(self):
        # Falso positivo medido 2026-11-26 (paquete cams-only): 4
        # fragmentos de ~6 palabras (frase + pregunta + crédito +
        # contexto) daban BURSTINESS 0 y FLAGGED sin ni una
        # muletilla. Con <25 palabras, burstiness es "too short to
        # judge" como los demás checks: REVIEW, nunca FLAGGED.
        desc = ("Rubius reacciona a un momento inesperado. "
                "¿Qué crees que acaba de pasar?\n\n"
                "🎬 Clip de rubius en directo.\n"
                "Contexto en pantalla: reaccionando a GTA 6.")
        _r, overall, verdict = run(humanize(desc)[0])
        self.assertNotEqual(verdict, "FLAGGED")
        self.assertGreaterEqual(overall, 50.0)


class TestNoFalsePositives(unittest.TestCase):
    def test_titulos_reales_nunca_flagged(self):
        for title in REAL_TITLES:
            with self.subTest(title=title):
                _r, _o, verdict = run(humanize_title(title))
                self.assertNotEqual(verdict, "FLAGGED")

    def test_paquetes_213_185_191_nunca_flagged(self):
        # Paquetes propuestos §7 (test_titles_verdad): REVIEW o PASS,
        # nunca FLAGGED, por campo y en conjunto.
        for name, gem in (("213", GEM_213), ("185", GEM_185),
                          ("191", GEM_191)):
            with self.subTest(paquete=name):
                det = detect_package_texts(
                    humanize_title(gem["title"]),
                    humanize(gem["description"])[0].strip(),
                    humanize(" ".join(gem["hashtags"]))[0].split())
                self.assertNotEqual(det["verdict"], "FLAGGED")
                for field, res in det["fields"].items():
                    self.assertNotEqual(res["verdict"], "FLAGGED", field)

    def test_cita_literal_i3_no_es_slop(self):
        # "el mejor easter egg" (cita literal del 185) NO es cliché:
        # los superlativos sueltos no están en el lexicón.
        _r, _o, verdict = run(
            "GTA 6: Grefg: el mejor easter egg está en la moto")
        self.assertNotEqual(verdict, "FLAGGED")


class TestPackageIntegration(unittest.TestCase):
    """FLAGGED en el paquete = retención, igual que un título sin
    verdad (R3.4). El título humanizado ES el best_title."""

    def _package(self, payload, lang="es", transcript=None):
        card = _card("rubius", transcript or (
            "que nos responda acá, vamos a seguir con la misión "
            "principal, había que hacerlo de Brian para saltar el "
            "banco, eso va a estar guapo"), lang)
        return package_clip(
            1, 100, 90.0, 0, 0, 10.0, [90.0] * 10, lang=lang,
            moment_card=card, game="GTA 6", streamer="rubius",
            vod_id="v1", visible="gameplay", llm_client=FakeLLM(payload),
            recent_titles=[])

    def test_paquete_limpio_se_publica(self):
        pkg = self._package(GEM_213)
        self.assertEqual(pkg["best_title"], GEM_213["title"])
        self.assertFalse(pkg["needs_human_review"])
        self.assertEqual(pkg["pkg_source"], "gemini")
        self.assertIsNone(pkg["hold_reason"])
        self.assertIn(pkg["humanize"]["verdict"], ("REVIEW", "PASS"))

    def test_descripcion_con_cliches_retiene(self):
        payload = dict(GEM_213)
        payload["description"] = (
            "Rubius sigue la misión principal en GTA 6. "
            "El chat explotó, hype máximo, nadie se lo creía, "
            "el chat enloqueció con esto.")
        pkg = self._package(payload)
        self.assertIsNone(pkg["best_title"])
        self.assertTrue(pkg["needs_human_review"])
        self.assertEqual(pkg["pkg_source"], "retained")
        self.assertEqual(pkg["hold_reason"], HOLD_REASON_HUMANIZE)
        self.assertEqual(pkg["humanize"]["verdict"], "FLAGGED")
        self.assertIn("description", pkg["humanize"]["flagged"])

    def test_titulo_con_cero_width_se_limpia_y_se_publica(self):
        payload = dict(GEM_213)
        payload["title"] = "GTA 6: La misión\u200b de Brian: saltar el banco"
        pkg = self._package(payload)
        self.assertEqual(pkg["best_title"], GEM_213["title"])
        self.assertNotIn("\u200b", pkg["best_title"])
        self.assertFalse(pkg["needs_human_review"])

    def test_hashtags_relleno_se_borran_en_paquete(self):
        payload = dict(GEM_213)
        payload["hashtags"] = ["#GTA6", "#Rubius", "#viral", "#fyp",
                               "#Shorts"]
        pkg = self._package(payload)
        self.assertEqual(pkg["hashtags"],
                         ["#GTA6", "#Rubius", "#Shorts"])
        self.assertFalse(pkg["needs_human_review"])

    def test_titulo_con_em_dash_se_humaniza_antes_de_quemar(self):
        # El título quemado es el del paquete: si Gemini trae un em
        # dash, el best_title publicado ya lleva coma, no el dash.
        payload = dict(GEM_213)
        payload["title"] = "GTA 6: La misión de Brian — saltar el banco"
        pkg = self._package(payload)
        self.assertEqual(
            pkg["best_title"],
            "GTA 6: La misión de Brian, saltar el banco")
        self.assertNotIn("—", pkg["best_title"])
        self.assertFalse(pkg["needs_human_review"])


class TestHoldReasonThreading(unittest.TestCase):
    """_enqueue_panel manda al panel el hold_reason del paquete
    (humanizador), no el genérico R3.4."""

    def _session(self, tmp, bridge):
        from clipagent.live_cutter import LiveCutterSession
        return LiveCutterSession(
            "rubius", platform="twitch", stream_id="2026-11-18",
            out_dir=os.path.join(tmp, "clips"), base_dir=tmp,
            bridge=bridge, dry_run=False, vod_id="123")

    def _record(self, hold_reason):
        pkg_path = os.path.join(tempfile.gettempdir(), "pkg.json")
        with open(pkg_path, "w", encoding="utf-8") as f:
            json.dump({"priority": {"youtube_shorts": {"priority": 45}}},
                      f)
        record = {
            "clip_id": "live:rubius:x:p100", "clip": __file__,
            "title": "Sin título — revisión pendiente",
            "start_sec": 100, "end_sec": 140, "peak_ts": 167.0,
            "category": "Grand Theft Auto VI",
            "category_source": "helix", "game": "GTA 6",
            "origin": "live", "viral_total": 55.0, "viral_auto": 55.0,
            "review": None,
            "streamer_hype": {"count": 0, "distinct": 0, "phrases": []},
            "subtitles_complete": True, "publish_blocked": True,
            "seo": {}, "publish_status": {}, "package_path": pkg_path,
            "hype_score": 8.0, "burst_score": 8.0, "hype_ratio": 4.0,
            "needs_human_review": True,
            "viral": {"dedupe_verified": True},
        }
        if hold_reason is not None:
            record["viral"]["hold_reason"] = hold_reason
        return record

    def _enqueue(self, tmp, record):
        class FakeBridge:
            def __init__(self):
                self.payloads = []

            def create_clip(self, payload):
                self.payloads.append(payload)
                return {"id": 97}

            def upload_video(self, clip_id, path):
                return {"ok": True}

            def tracking_step(self, *a, **k):
                return {"ok": True}

        bridge = FakeBridge()
        session = self._session(tmp, bridge)
        panel_id = session._enqueue_panel(record)
        self.assertEqual(panel_id, 97)
        return bridge.payloads[0]

    def test_hold_reason_humanizador_llega_al_panel(self):
        with tempfile.TemporaryDirectory() as tmp:
            payload = self._enqueue(
                tmp, self._record(HOLD_REASON_HUMANIZE))
            self.assertEqual(payload["viral"]["hold_reason"],
                             HOLD_REASON_HUMANIZE)

    def test_sin_hold_reason_propio_cae_al_generico(self):
        from clipagent.live_cutter import HOLD_REASON
        with tempfile.TemporaryDirectory() as tmp:
            payload = self._enqueue(tmp, self._record(None))
            self.assertEqual(payload["viral"]["hold_reason"],
                             HOLD_REASON)


class TestLexiconFile(unittest.TestCase):
    def test_secciones_es_con_source(self):
        lex = load_lexicon()
        for key in ("words_es", "phrases_es", "structures_es",
                    "hashtags"):
            self.assertIn(key, lex)
            self.assertTrue(lex[key], key)
        for entry in lex["words_es"] + lex["phrases_es"]:
            self.assertIn("source", entry, entry["find"])

    def test_sin_archivo_falla_ruidoso(self):
        from clipagent import humanize as hz
        old = hz._LEXICON_CACHE
        hz._LEXICON_CACHE = None
        try:
            with self.assertRaises(FileNotFoundError):
                hz.load_lexicon("/no/existe/slop.json")
        finally:
            hz._LEXICON_CACHE = old

    def test_sin_secretos_ni_urls_en_lexicon(self):
        lex = load_lexicon()
        blob = json.dumps(lex, ensure_ascii=False)
        self.assertNotIn("http", blob)


if __name__ == "__main__":
    unittest.main()

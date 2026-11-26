"""Tests de clipagent/hook_formulas.py (adopción 3/5).

Sin ganchos inventados: los ejemplos EN son el campo ``example`` de
cada fórmula en ``clipagent/hooks.json`` (repo origen); los ejemplos
ES son la traducción directa de ese ``example``/``template`` (citada
por id); los no-clasificados son títulos reales nuestros (de
``test_hookscore.py``) o el ``unclassified`` del README origen.

Uso: .venv/bin/python -m pytest test_hook_formulas.py -q
"""

import json
import os
import re
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from clipagent import audit_published as ap
from clipagent import viral_source as vs
from clipagent.hook_formulas import (
    UNCLASSIFIED,
    all_formulas,
    classify_hook,
    formula_label,
    formula_name,
    formulas_by_id,
)


def _hooks_json():
    here = os.path.dirname(os.path.abspath(__file__))
    with open(os.path.join(here, "clipagent", "hooks.json"),
              encoding="utf-8") as fh:
        return json.load(fh)


# Ejemplos ES: traducción directa del example/template del origen,
# citada por id (ver PLAN-3-hook-formulas.md §3, capa ES).
ES_EXAMPLES = {
    # id 1: "$18,000 is what one missing clause cost me."
    1: "$18.000 es lo que me costó una cláusula",
    # id 2: "Stop posting three times a day. Post twice and reply..."
    2: "Deja de publicar tres veces al día. Publica dos y responde "
       "a cada comentario una hora.",
    # id 3: "Nobody tells you that your first 30 reels are supposed
    # to flop."
    3: "Nadie te dice que tus primeros 30 reels están hechos para "
       "fracasar",
    # id 4: "This $12 tool replaced the editor I was paying $2,000
    # a month."
    4: "Esta herramienta de $12 reemplazó al editor al que pagaba "
       "$2.000 al mes",
    # id 5: "Proposals used to take me five hours. They take twenty
    # minutes now."
    5: "Las propuestas antes me tomaban cinco horas. Ahora me toman "
       "veinte minutos.",
    # id 6: "I posted one reel a day for 60 days. Here is what it did
    # to my DMs."
    6: "Publiqué un reel al día durante 60 días. Esto es lo que pasó "
       "con mis DMs.",
    # id 7: "You are writing captions wrong, and it is not your fault.
    # Instagram hides everything after line one."
    7: "Estás escribiendo mal los captions y no es tu culpa",
    # id 8: "I spent six years selling agency retainers. Here is what
    # we never put in the proposal."
    8: "Pasé 6 años vendiendo retainers. Esto es lo que nunca pusimos "
       "en la propuesta.",
    # id 9: "Steal this four-line follow-up. It took me two years and
    # a lot of dead leads."
    9: "Roba este seguimiento de cuatro líneas. Me tomó dos años "
       "dejarlo bien.",
    # id 10: "If your reels get views and no followers, the next 40
    # seconds fix it."
    10: "Si tus reels tienen vistas y no seguidores, los próximos 40 "
        "segundos lo arreglan.",
    # id 11: "Five caption edits that double saves. Number four is the
    # one nobody does."
    11: "5 ediciones de caption que duplican los guardados. El número "
        "4 es el que nadie hace.",
    # id 12: "\"I don't want to be on camera.\" Fine. Here is the
    # faceless version that still sells."
    12: "«No quiero salir en cámara». Vale. Aquí está la versión sin "
        "cara que igual vende.",
    # id 13: "This was my profile in March. This is it now. I changed
    # the name field, nothing else."
    13: "Este era mi perfil en marzo. Este es ahora. Cambié el nombre, "
        "nada más.",
    # id 14: "Photographers charging under $2,000: this is the pricing
    # page you have been avoiding."
    14: "Para los fotógrafos que cobran menos de $2.000: esta es la "
        "página de precios que están evitando.",
    # id 15: "My first 47 reels averaged 300 views. Number 48 did
    # 400,000. One thing was different."
    15: "Mis primeros 47 reels promediaron 300 vistas. El 48 hizo "
        "400.000. Una cosa fue distinta.",
    # id 16: "\"How do I post consistently without hating it?\" I get
    # this one every week."
    16: "«¿Cómo publico siempre sin odiarlo?» Me lo preguntan cada "
        "semana.",
    # id 17: "Carousels versus reels. I ran both for 90 days and it was
    # not close."
    17: "Carruseles contra reels. Los probé 90 días y no estuvo cerca.",
    # id 18: "Watch what happens when I paste a competitor's caption
    # into this."
    18: "Mira lo que pasa cuando pego el caption de un competidor aquí.",
    # id 19: "Your top nine grid crops change the second you post a
    # reel. Fix the covers first."
    19: "Tu top 9 cambia en cuanto publicas un reel. Arregla las "
        "portadas primero.",
    # id 20: "You do not need a niche. You need one person you can
    # name."
    20: "No necesitas un nicho. Necesitas una persona que puedas "
        "nombrar.",
    # id 21: "...and that is when the client asked for the money back.
    # Three days after approving it."
    21: "...y entonces el cliente pidió que le devolvieran el dinero. "
        "Tres días después de aprobarlo.",
    # id 22: "Nice guys finish first."
    22: "Los buenos terminan primero.",
    # id 23: "33% of high school graduates never read another book."
    23: "33% de los graduados de secundaria no vuelven a leer un libro.",
    # id 24: "This is the new Sony point and shoot, and it fixes the
    # one thing I hated."
    24: "Esta es la nueva cámara Sony, y arregla lo único que odiaba.",
    # id 25: "There was one creator who grew a million subscribers in
    # a single month."
    25: "Hubo un creador que ganó un millón de suscriptores en un solo "
        "mes.",
    # id 26: "The fastest way to get confident is to build evidence you
    # cannot argue with."
    26: "La forma más rápida de ganar confianza es construir evidencia "
        "que no puedas discutir.",
}


class TestClassifyEnglish(unittest.TestCase):
    def test_los_26_examples_del_json_clasifican_a_su_formula(self):
        # El example de cada fórmula es un gancho real del repo
        # origen. 6 de ellos solo casan por EN_FIX (el regex EN
        # original no los nombra: ver hook_formulas.py).
        data = _hooks_json()
        self.assertEqual(len(data["hooks"]), 26)
        for entry in data["hooks"]:
            with self.subTest(formula=entry["id"]):
                found = classify_hook(entry["example"])
                self.assertIsNotNone(
                    found, f"example sin clasificar: {entry['example']!r}")
                self.assertEqual(found["id"], entry["id"])
                self.assertEqual(found["name"], entry["name"])

    def test_los_6_huecos_en_fix_son_del_regex_origen(self):
        # Medido 2026-11-26: estos 6 examples NO casan con su regex
        # EN verbatim (sin EN_FIX ni capa ES). El test fija el
        # hallazgo: si el origen arregla sus regex, aquí se ve.
        data = _hooks_json()
        by_id = {h["id"]: h for h in data["hooks"]}
        for fid in (7, 8, 11, 12, 14, 19):
            with self.subTest(formula=fid):
                pattern = re.compile(by_id[fid]["match"],
                                     re.IGNORECASE)
                self.assertIsNone(
                    pattern.search(by_id[fid]["example"]),
                    f"el regex EN de {fid} ahora casa solo: "
                    "revisar si EN_FIX sobra")

    def test_classify_order_cubre_las_26_sin_faltar_ni_sobrar(self):
        data = _hooks_json()
        ids = {h["id"] for h in data["hooks"]}
        self.assertEqual(set(data["classify_order"]), ids)
        self.assertEqual(len(data["classify_order"]), 26)
        self.assertEqual([f["id"] for f in all_formulas()],
                         data["classify_order"])

    def test_gancho_con_dos_formulas_gana_la_primera_en_orden(self):
        # "Nobody tells you that your first 30 reels..." casa con
        # id 3 (Nobody Tells You) y con id 15 (first 30 reels); en
        # classify_order el 3 va antes que el 15.
        found = classify_hook(
            "Nobody tells you that your first 30 reels are supposed "
            "to flop.")
        self.assertEqual(found["id"], 3)
        self.assertEqual(found["name"], "Nobody Tells You")

    def test_case_insensitive(self):
        found = classify_hook(
            "NOBODY TELLS YOU THAT YOUR FIRST 30 REELS ARE SUPPOSED "
            "TO FLOP.")
        self.assertEqual(found["id"], 3)


class TestClassifySpanish(unittest.TestCase):
    def test_las_26_formulas_clasifican_ganchos_es(self):
        by_id = formulas_by_id()
        self.assertEqual(len(ES_EXAMPLES), 26)
        for fid, text in ES_EXAMPLES.items():
            with self.subTest(formula=fid):
                found = classify_hook(text)
                self.assertIsNotNone(
                    found, f"gancho ES sin clasificar (id {fid}): "
                           f"{text!r}")
                self.assertEqual(found["id"], fid)
                self.assertEqual(found["name"], by_id[fid]["name"])

    def test_formula_name_y_label(self):
        self.assertEqual(
            formula_name("Nadie te dice que tus primeros 30 reels "
                         "están hechos para fracasar"),
            "Nobody Tells You")
        self.assertEqual(
            formula_label("Steal this four-line follow-up. It took "
                          "me two years and a lot of dead leads."),
            "#9 The Steal")
        self.assertEqual(formula_label("sin fórmula aquí"), UNCLASSIFIED)


class TestUnclassified(unittest.TestCase):
    def test_titulos_reales_nuestros_no_tienen_formula(self):
        # Títulos reales de test_hookscore.py: son títulos
        # («GTA 6: streamer + momento»), no ganchos hablados.
        # Que no clasifiquen es correcto, no un fallo.
        for title in (
                "GTA 6: Rubius descubre cómo remolcar la moto acuática",
                "GTA 6: TheGrefg y la patada nuclear imposible",
                "GTA 6: AuronPlay «No es una cinemática»!!",
                "GTA 6: tres formas de robar un avión"):
            with self.subTest(title=title):
                self.assertIsNone(classify_hook(title))
                self.assertEqual(formula_name(title), UNCLASSIFIED)

    def test_unclassified_del_readme_origen(self):
        # README origen, salida de swipe.py: "in this video I am
        # going to show you my morning routine" -> unclassified.
        self.assertIsNone(classify_hook(
            "in this video I am going to show you my morning routine"))

    def test_vacio_y_none_no_rompen(self):
        self.assertIsNone(classify_hook(""))
        self.assertIsNone(classify_hook("   "))
        self.assertIsNone(classify_hook(None))
        self.assertEqual(formula_name(""), UNCLASSIFIED)


def _audit_clip(title, outlier, views=10000):
    return {"title": title, "url": "https://www.youtube.com/shorts/x",
            "views": views, "duration_s": 30,
            "posted_day": "2026-11-21", "follows": None,
            "platform": "youtube_shorts", "outlier": outlier}


class TestAuditIntegration(unittest.TestCase):
    def test_la_formula_aparece_por_clip_y_agregada(self):
        # 7 clips: los 2 de arriba son The Steal, los 5 de abajo
        # son títulos reales sin fórmula. Top 5 = clips 1-5,
        # bottom 5 = clips 3-7: The Steal solo está arriba.
        clips = [
            _audit_clip("Steal this hook. It took me two years.", 10.0),
            _audit_clip("Steal this caption. It took me two years.", 9.0),
            _audit_clip("GTA 6: Rubius roba un jet", 5.0),
            _audit_clip("GTA 6: TheGrefg y la patada nuclear imposible",
                        4.0),
            _audit_clip("GTA 6: AuronPlay «No es una cinemática»!!", 3.0),
            _audit_clip("GTA 6: Rubius descubre cómo remolcar la moto "
                        "acuática", 2.0),
            _audit_clip("GTA 6: tres formas de robar un avión", 1.0),
        ]
        report = ap.build_report(clips, 1000, "sebastian",
                                 "youtube_shorts")
        self.assertEqual(report["top5"][0]["hook_formula"], "The Steal")
        self.assertEqual(report["top5"][1]["hook_formula"], "The Steal")
        self.assertEqual(report["top5"][2]["hook_formula"], UNCLASSIFIED)
        self.assertEqual(report["formula_top"],
                         {"The Steal": 2, UNCLASSIFIED: 3})
        self.assertEqual(report["formula_bottom"], {UNCLASSIFIED: 5})
        self.assertEqual(report["n_unclassified"], 5)
        self.assertTrue(any("The Steal" in line
                            and "arriba" in line
                            for line in report["reading"]))
        self.assertTrue(any("Más de la mitad sin fórmula" in line
                            for line in report["reading"]))
        text = ap.render_text(report)
        self.assertIn("FÓRMULAS", text)
        self.assertIn("The Steal (2)", text)
        self.assertIn("sin clasificar: 5 de 7", text)
        self.assertIn("(The Steal)", text)

    def test_sin_clips_ranked_no_hay_seccion_de_formulas(self):
        report = ap.build_report([], None, "sebastian",
                                 "youtube_shorts")
        self.assertEqual(report["formula_top"], {})
        self.assertEqual(report["formula_bottom"], {})
        self.assertEqual(report["n_unclassified"], 0)
        self.assertNotIn("FÓRMULAS", ap.render_text(report))


class TestViralIntegration(unittest.TestCase):
    def test_process_candidate_anota_la_formula_en_el_report(self):
        # El report se construye ANTES del primer retorno (candidato
        # incompleto): no hace falta mockear el pipeline pesado.
        # Solo se prueba el cableado (la fórmula viaja en el report).
        candidate = {
            "video_id": "abc123",
            "url": "https://www.youtube.com/watch?v=abc123",
            "title": "Nobody tells you that your first 30 reels are "
                     "supposed to flop.",
            "views": 500000, "likes": 9000, "duration_s": 0,
            "channel": "@IShowSpeed", "streamer": "ishowspeed",
            "language": "en",
        }
        with tempfile.TemporaryDirectory() as tmp:
            panel_id, motivo, report = vs.process_candidate(
                None, candidate, tmp, None, None, "sebastian")
        self.assertIsNone(panel_id)
        self.assertIn("candidato incompleto", motivo)
        self.assertEqual(report["hook_formula"], "Nobody Tells You")
        self.assertIn("hook_score", report)

    def test_process_candidate_titulo_real_es_unclassified(self):
        candidate = {
            "video_id": "abc123",
            "url": "https://www.youtube.com/watch?v=abc123",
            "title": "GTA 6: Rubius descubre cómo remolcar la moto "
                     "acuática",
            "views": 500000, "likes": 9000, "duration_s": 0,
            "channel": "@IShowSpeed", "streamer": "ishowspeed",
            "language": "es",
        }
        with tempfile.TemporaryDirectory() as tmp:
            _pid, _motivo, report = vs.process_candidate(
                None, candidate, tmp, None, None, "sebastian")
        self.assertEqual(report["hook_formula"], UNCLASSIFIED)


if __name__ == "__main__":
    unittest.main()

#!/usr/bin/env python3
"""Pruebas de 7.1: selección de momentos con LLM (opcional, con
fallback). El LLM es una señal más, no reemplaza el score.
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from clipagent.detect import score_clip
from clipagent.llm import LLMClient, NullLLMClient
from clipagent.select import (apply_llm_selection, build_selection_prompt,
                              parse_selection, select_moments,
                              transcript_text)


class FakeLLM(LLMClient):
    def __init__(self, reply):
        self.reply = reply

    def available(self):
        return True

    def generate(self, prompt):
        return self.reply


CANDIDATES = [
    {"start": 100, "end": 160,
     "transcript": {"segments": [{"text": "¡No puedo creer lo que "
                                            "acaba de pasar!"}]},
     "chat_score": 90.0, "chat_multiple": 8.0, "risas": 12, "hype": 5},
    {"start": 500, "end": 560,
     "transcript": {"segments": [{"text": "Vamos a ver qué hay aquí."}]},
     "chat_score": 40.0, "chat_multiple": 3.5, "risas": 1, "hype": 0},
]


class TestPromptAndParse(unittest.TestCase):
    def test_prompt_incluye_transcripcion_y_chat(self):
        prompt = build_selection_prompt(CANDIDATES)
        self.assertIn("¡No puedo creer", prompt)
        self.assertIn("90", prompt)
        self.assertIn("8.0x", prompt)
        self.assertIn("JSON", prompt)

    def test_parse_valido(self):
        text = ('[{"start": 102, "end": 150, "hook": "¡No lo vas a creer!", '
                '"por_que_viral": "reacción exagerada", "score": 88}]')
        out = parse_selection(text, CANDIDATES)
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0]["candidate_index"], 0)
        self.assertEqual(out[0]["start"], 102)
        self.assertEqual(out[0]["score"], 88)

    def test_parse_descarta_fuera_de_ventana(self):
        text = ('[{"start": 900, "end": 950, "hook": "x", '
                '"por_que_viral": "y", "score": 90}]')
        self.assertEqual(parse_selection(text, CANDIDATES), [])

    def test_parse_descarta_score_fuera_de_rango_y_duracion(self):
        text = ('[{"start": 100, "end": 105, "hook": "x", '
                '"por_que_viral": "y", "score": 150}, '
                '{"start": 500, "end": 505, "hook": "corto", '
                '"por_que_viral": "y", "score": 50}]')
        self.assertEqual(parse_selection(text, CANDIDATES), [])

    def test_parse_json_roto_devuelve_vacio(self):
        self.assertEqual(parse_selection("no es json", CANDIDATES), [])
        self.assertEqual(parse_selection("", CANDIDATES), [])

    def test_parse_recorta_a_la_ventana(self):
        text = ('[{"start": 90, "end": 170, "hook": "h", '
                '"por_que_viral": "y", "score": 70}]')
        out = parse_selection(text, CANDIDATES)
        self.assertEqual(out[0]["start"], 100)
        self.assertEqual(out[0]["end"], 160)

    def test_transcript_text_aplana_segmentos(self):
        t = {"segments": [{"text": "Hola "}, {"text": "mundo"}]}
        self.assertEqual(transcript_text(t), "Hola mundo")
        self.assertEqual(transcript_text(None), "")


class TestSelectMoments(unittest.TestCase):
    def test_con_llm_devuelve_selecciones(self):
        reply = ('[{"start": 102, "end": 150, "hook": "¡No lo vas a creer!", '
                 '"por_que_viral": "reacción exagerada", "score": 88}]')
        selections, used = select_moments(CANDIDATES, FakeLLM(reply))
        self.assertTrue(used)
        self.assertEqual(len(selections), 1)
        self.assertEqual(selections[0]["hook"], "¡No lo vas a creer!")

    def test_sin_llm_fallback_sin_levantar(self):
        selections, used = select_moments(CANDIDATES, NullLLMClient())
        self.assertFalse(used)
        self.assertEqual(selections, [])

    def test_llm_que_explota_fallback(self):
        class BoomLLM(LLMClient):
            def available(self):
                return True

            def generate(self, prompt):
                raise RuntimeError("sin red")

        selections, used = select_moments(CANDIDATES, BoomLLM())
        self.assertFalse(used)
        self.assertEqual(selections, [])


class TestApplySelection(unittest.TestCase):
    def test_aplica_hook_y_recalcula_viralidad(self):
        detail = {"msgs": 50, "risas": 12, "hype": 5, "unique_users": 30}
        base = score_clip(detail, 8.0, rank=1, human=None, audio=None)
        manifest = {"clips": [{"peak": 100, "start": 100, "duration": 60,
                               "viral_score": dict(base)}]}
        inputs = {100: (detail, 8.0, 1, None, None)}
        selections = [{"candidate_index": 0, "start": 102, "end": 150,
                       "hook": "¡No lo vas a creer!",
                       "por_que_viral": "reacción exagerada", "score": 88}]
        n = apply_llm_selection(manifest, selections, inputs)
        self.assertEqual(n, 1)
        clip = manifest["clips"][0]
        self.assertEqual(clip["llm"]["score"], 88)
        self.assertEqual(clip["llm"]["hook"], "¡No lo vas a creer!")
        # El hook LLM (8,8/10) reemplaza la estimación 5,0: el total
        # sube, con los mismos pesos y la misma fórmula.
        self.assertEqual(clip["viral_score"]["components"]["hook"]["value"],
                         8.8)
        self.assertGreater(clip["viral_score"]["total"], base["total"])

    def test_hook_humano_manda_sobre_el_llm(self):
        detail = {"msgs": 50, "risas": 12, "hype": 5, "unique_users": 30}
        human = {"hook": {"value": 9.0, "reason": "tribunal"}}
        base = score_clip(detail, 8.0, rank=1, human=human, audio=None)
        manifest = {"clips": [{"peak": 100, "start": 100, "duration": 60,
                               "viral_score": dict(base)}]}
        inputs = {100: (detail, 8.0, 1, human, None)}
        selections = [{"candidate_index": 0, "start": 102, "end": 150,
                       "hook": "¡No lo vas a creer!",
                       "por_que_viral": "reacción", "score": 88}]
        n = apply_llm_selection(manifest, selections, inputs)
        self.assertEqual(n, 0)
        self.assertEqual(manifest["clips"][0]["llm"]["score"], 88)
        self.assertEqual(
            manifest["clips"][0]["viral_score"]["components"]["hook"]["value"],
            9.0)


if __name__ == "__main__":
    unittest.main(verbosity=2)

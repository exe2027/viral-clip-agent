#!/usr/bin/env python3
"""Tests de la adopción 5/5: beats.py (gancho hablado ≤3 s).

Sin números inventados: el fixture principal son las primeras
palabras REALES del clip de IlloJuan
``runs/2930700035/clips/clip_01_t26m05s_s19.22_r4h47_transcript.json``
(segmento 0, 0,0–4,7 s, 19 palabras, faster-whisper con
``word_timestamps=True``), copiadas tal cual con sus timestamps.
Ese archivo es un artefacto local gitignorado (no está en git),
así que el fixture va incrustado aquí con la cita de la fuente;
ningún test lee ``runs/``. Los demás fixtures sintéticos usan
la misma técnica de ``test_titles_subtitles.py`` (palabras
repartidas uniformemente en el segmento).
"""
import io
import json
import os
import sys
import tempfile
import unittest
from contextlib import redirect_stdout

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from clipagent.beats import (
    analyse_script,
    analyse_spoken,
    concrete_count,
    main,
    words,
)
from clipagent.quality_gate import build_quality_gate

# Primeras palabras reales del clip de IlloJuan (fuente citada
# arriba). Primera frase: "GT A3." termina a los 0,48 s.
ILLOJUAN_SEG0 = [
    ("GT", 0.0, 0.18), ("A3.", 0.18, 0.48),
    ("¿Sabes", 1.26, 1.56), ("lo", 1.56, 1.62),
    ("que", 1.62, 1.7), ("te", 1.7, 1.8),
    ("digo?", 1.8, 2.02),
    ("O", 2.36, 2.44), ("acordáis", 2.44, 2.72),
    ("cuando", 2.72, 2.88), ("salió", 2.88, 3.08),
    ("o", 3.08, 3.24), ("jugar", 3.24, 3.42),
    ("legacy", 3.42, 3.84), ("y", 3.84, 4.04),
    ("todo", 4.04, 4.14), ("el", 4.14, 4.3),
    ("puto", 4.3, 4.54), ("mundo", 4.54, 4.7),
]
ILLOJUAN_TRANSCRIPT = {"segments": [{
    "start": 0.0, "end": 4.7,
    "text": "GT A3. ¿Sabes lo que te digo? O acordáis cuando "
            "salió o jugar legacy y todo el puto mundo",
    "words": [{"word": w, "start": s, "end": e}
              for w, s, e in ILLOJUAN_SEG0],
}]}


def _words(text, start, step):
    """Palabras con timestamps uniformes (técnica de
    test_titles_subtitles.py)."""
    out = []
    for i, w in enumerate(text.split()):
        out.append({"word": w, "start": start + i * step,
                    "end": start + (i + 1) * step})
    return out


def _transcript(*segments):
    """segments: (start, text, step_por_palabra)."""
    segs = []
    for start, text, step in segments:
        ws = _words(text, start, step)
        segs.append({"start": start, "end": ws[-1]["end"],
                     "text": text, "words": ws})
    return {"segments": segs}


def _review(**over):
    base = {
        "decision": "aprobado", "pasa": True, "hard_pasa": True,
        "observe_only": True,
        "scores": {"webcam_visible": 8.0, "titulo_legible": 8.0,
                   "sin_barras_negras": 8.0,
                   "momento_comprensible": 8.0},
        "fallos": [], "motivo": "", "transcript_words": 12,
        "face": {"pasa": True, "method": "facecrop", "share": 1.0,
                 "motivo": ""},
        "template": {"dimensiones": True, "titulo_banner": True},
    }
    base.update(over)
    return base


class TestDurationModel(unittest.TestCase):
    def test_once_palabras_a_165_wpm_son_4_segundos(self):
        a = analyse_script(" ".join(f"palabra{i}" for i in range(11)))
        self.assertAlmostEqual(a["beats"][0]["dur"], 4.0, places=2)
        self.assertEqual(a["beats"][0]["words"], 11)

    def test_wpm_configurable_cambia_la_duracion(self):
        text = " ".join(f"palabra{i}" for i in range(11))
        self.assertAlmostEqual(
            analyse_script(text, wpm=150)["beats"][0]["dur"],
            11 / (150 / 60.0), places=2)
        self.assertAlmostEqual(
            analyse_script(text, wpm=200)["beats"][0]["dur"],
            11 / (200 / 60.0), places=2)

    def test_words_cuenta_18000_como_una_palabra(self):
        self.assertEqual(len(words("gano $18,000 en un día")), 5)


class TestConcreteEs(unittest.TestCase):
    def test_numero_y_nombre_propio_son_concretos(self):
        # "PlayStation 1" es del mismo clip de IlloJuan (segmento 2:
        # "PlayStation" 9,62–9,86, "1" después).
        self.assertGreater(
            concrete_count("y la piedra filosofal de PlayStation 1"), 0)

    def test_numero_dicho_en_voz_alta_es_concreto(self):
        self.assertGreater(concrete_count("tres libros en un día"), 0)

    def test_frase_sin_nada_concreto_da_cero(self):
        self.assertEqual(
            concrete_count("¿Sabes lo que te digo?"), 0)


class TestSpokenHookRealTimestamps(unittest.TestCase):
    def test_illojuan_primera_frase_cabe_en_3s(self):
        a = analyse_spoken(ILLOJUAN_TRANSCRIPT)
        self.assertEqual(a["source"], "timestamps")
        # "GT A3." termina a los 0,48 s: el gancho hablado cabe.
        self.assertAlmostEqual(a["hook_end_s"], 0.48, places=2)
        self.assertAlmostEqual(a["hook_start_s"], 0.0, places=2)
        self.assertEqual(a["hook_words"], 2)
        self.assertNotIn("hook_long", a["issues"])

    def test_illojuan_gancho_sin_numero_ni_nombre(self):
        # "GT A3." no tiene cifra ni nombre propio según la
        # definición de concreto de hookscore: se marca, no se
        # esconde. Es señal de calidad, no un título falso.
        a = analyse_spoken(ILLOJUAN_TRANSCRIPT)
        self.assertEqual(a["hook_concrete"], 0)
        self.assertIn("hook_not_concrete", a["issues"])

    def test_illojuan_tres_beats_abstractos_seguidos(self):
        # Los 3 beats del fixture no tienen nada concreto.
        a = analyse_spoken(ILLOJUAN_TRANSCRIPT)
        self.assertIn("abstract_run", a["issues"])
        self.assertEqual(len(a["beats"]), 3)


class TestSpokenIssues(unittest.TestCase):
    def test_gancho_largo_marcado(self):
        # Primera frase de 7 palabras a 0,5 s cada una: 3,5 s.
        t = _transcript((0.0, "esto es una frase muy larga ya.", 0.5))
        a = analyse_spoken(t)
        self.assertAlmostEqual(a["hook_end_s"], 3.5, places=2)
        self.assertIn("hook_long", a["issues"])

    def test_gancho_tardio_por_silencio_inicial(self):
        # El beat dura 1,2 s, pero empieza a los 2,0 s: termina a
        # los 3,2 s y NO cabe en la ventana medida desde t=0.
        t = _transcript((2.0, "mira esto.", 0.6))
        a = analyse_spoken(t)
        self.assertAlmostEqual(a["beats"][0]["dur"], 1.2, places=2)
        self.assertAlmostEqual(a["hook_end_s"], 3.2, places=2)
        self.assertIn("hook_long", a["issues"])

    def test_gancho_concreto_no_se_marca(self):
        t = _transcript((0.0, "PlayStation 1.", 0.4),
                        (0.4, "Todo el mundo jugando.", 0.4))
        a = analyse_spoken(t)
        self.assertGreater(a["hook_concrete"], 0)
        self.assertNotIn("hook_not_concrete", a["issues"])
        self.assertNotIn("hook_long", a["issues"])

    def test_beat_largo_marcado(self):
        # Segunda frase de 9 palabras a 0,5 s: 4,5 s > MAX_BEAT.
        t = _transcript((0.0, "Corto.", 0.4),
                        (0.4, "esta es una frase que dura demasiado tiempo ya.", 0.5))
        a = analyse_spoken(t)
        self.assertIn("beat_long", a["issues"])
        self.assertTrue(a["beats"][1]["flags"])

    def test_racha_de_dos_no_se_marca(self):
        t = _transcript((0.0, "Mira esto.", 0.4),
                        (0.8, "Qué locura.", 0.4),
                        (1.6, "PlayStation 1.", 0.4))
        a = analyse_spoken(t)
        self.assertNotIn("abstract_run", a["issues"])

    def test_racha_rota_por_beat_concreto_no_se_marca(self):
        t = _transcript((0.0, "Mira esto.", 0.4),
                        (0.8, "Qué locura.", 0.4),
                        (1.6, "PlayStation 1.", 0.4),
                        (2.8, "No me lo creo.", 0.4))
        a = analyse_spoken(t)
        self.assertNotIn("abstract_run", a["issues"])

    def test_desvio_objetivo_por_encima_y_por_debajo(self):
        # 12 palabras a 0,5 s = 6,0 s de habla.
        t = _transcript(
            (0.0, "Una frase corta aquí.", 0.5),
            (2.0, "Otra frase corta aquí.", 0.5),
            (4.0, "Tercera frase corta ya.", 0.5))
        over = analyse_spoken(t, target=5.0)
        self.assertIn("over_target", over["issues"])
        self.assertIn("Corta unas", " ".join(over["notes"]))
        under = analyse_spoken(t, target=30.0)
        self.assertIn("under_target", under["issues"])
        within = analyse_spoken(t, target=6.0)
        self.assertNotIn("over_target", within["issues"])
        self.assertNotIn("under_target", within["issues"])

    def test_sin_loop_en_lo_hablado(self):
        # La última frase repite "moto" del gancho: en guion es
        # loop, en clip cortado NO se marca (sin consumidor).
        t = _transcript((0.0, "La moto vuela.", 0.4),
                        (1.2, "Todo el mundo mirando.", 0.4),
                        (2.8, "La moto otra vez.", 0.4))
        a = analyse_spoken(t)
        self.assertNotIn("loop", " ".join(a["notes"]).lower())
        self.assertNotIn("loop", " ".join(a["issues"]).lower())

    def test_sin_timestamps_usa_estimacion_y_lo_declara(self):
        t = {"segments": [{"start": 0.0, "end": 2.0,
                           "text": "Mira esto ya."}]}
        a = analyse_spoken(t)
        self.assertEqual(a["source"], "estimated")
        self.assertAlmostEqual(a["hook_end_s"], 2.0, places=2)

    def test_transcripcion_vacia_devuelve_none(self):
        self.assertIsNone(analyse_spoken({}))
        self.assertIsNone(analyse_spoken({"segments": []}))
        self.assertIsNone(analyse_spoken({"segments": [{"text": ""}]}))


class TestScriptStructure(unittest.TestCase):
    def test_etiquetas_hook_mid_cta_y_loop(self):
        script = ("GTA 6: Rubius roba 3 coches en un minuto.\n"
                  "Nadie esperaba lo que pasa después.\n"
                  "El chat explota cuando lo consigue.\n"
                  "Rubius roba el tercer coche y gana.")
        a = analyse_script(script)
        self.assertEqual(a["beats"][0]["label"], "HOOK")
        self.assertEqual(a["beats"][-1]["label"], "CTA")
        self.assertTrue(any(r["label"] == "MID" for r in a["beats"]))
        # La última línea repite "Rubius" y "roba" del gancho.
        self.assertTrue(any("Loop" in n for n in a["notes"]))

    def test_script_vacio_devuelve_none(self):
        self.assertIsNone(analyse_script(""))
        self.assertIsNone(analyse_script("   \n  "))


class TestQualityGateIntegration(unittest.TestCase):
    def test_beats_en_el_gate_sin_cambiar_la_decision(self):
        # Gancho largo a propósito: 7 palabras a 0,5 s = 3,5 s.
        t = _transcript(
            (0.0, "esto es una frase muy larga ya.", 0.5))
        gate = build_quality_gate(_review(), thumbnail_text="GTA 6",
                                  transcript=t)
        self.assertEqual(gate["decision"], "aprobado")
        self.assertIs(gate["pasa"], True)
        self.assertEqual(gate["beats"]["status"], "ok")
        self.assertIn("hook_long", gate["beats"]["issues"])
        self.assertAlmostEqual(gate["beats"]["hook_end_s"], 3.5,
                               places=2)

    def test_beats_no_rescata_un_rechazado(self):
        gate = build_quality_gate(
            _review(decision="rechazado", pasa=False),
            thumbnail_text="GTA 6", transcript=ILLOJUAN_TRANSCRIPT)
        self.assertEqual(gate["decision"], "rechazado")
        self.assertIs(gate["pasa"], False)
        self.assertEqual(gate["beats"]["status"], "ok")

    def test_sin_transcripcion_no_hay_clave_beats(self):
        gate = build_quality_gate(_review(), thumbnail_text="GTA 6")
        self.assertNotIn("beats", gate)
        self.assertEqual(gate["decision"], "aprobado")

    def test_transcripcion_vacia_es_skipped(self):
        gate = build_quality_gate(_review(), thumbnail_text="GTA 6",
                                  transcript={"segments": []})
        self.assertEqual(gate["beats"]["status"], "skipped")
        self.assertEqual(gate["decision"], "aprobado")


class TestCli(unittest.TestCase):
    def test_cli_transcript_json(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "clip.transcript.json")
            with open(path, "w", encoding="utf-8") as f:
                json.dump(ILLOJUAN_TRANSCRIPT, f)
            buf = io.StringIO()
            with redirect_stdout(buf):
                rc = main(["--transcript", path, "--json"])
            self.assertEqual(rc, 0)
            data = json.loads(buf.getvalue())
            self.assertEqual(data["source"], "timestamps")
            self.assertAlmostEqual(data["hook_end_s"], 0.48, places=2)

    def test_cli_guion_vacio_es_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "vacio.txt")
            with open(path, "w", encoding="utf-8") as f:
                f.write("")
            self.assertEqual(main([path]), 2)


if __name__ == "__main__":
    unittest.main()

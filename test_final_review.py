#!/usr/bin/env python3
"""Pruebas de la validación final del preview (final_review.py).

Reglas que se verifican aquí:
- Gemini ve el MP4 final (4 fotogramas) y aprueba o frena por
  criterio; el veredicto se re-calcula en código (un criterio
  < 5 frena aunque Gemini diga que pasa).
- Sin Gemini (sin key, error, JSON inválido) el clip queda
  PENDIENTE de revisión humana, nunca aprobado.
- Con la validación apagada no hay veredicto (desactivada).
"""
import json
import os
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from clipagent import final_review as fr

REPO = os.path.dirname(os.path.abspath(__file__))


def _gemini_ok(**overrides):
    """Respuesta Gemini válida: todo 8,0 salvo lo sobreescrito."""
    criterios = {}
    for name in fr.CRITERIA:
        criterios[name] = {"score": 8.0, "motivo": "bien"}
    for name, score in overrides.items():
        criterios[name] = {"score": score, "motivo": "mal"}
    fallos = [n for n, c in criterios.items() if c["score"] < 5]
    return json.dumps({
        "criterios": criterios,
        "fallos": fallos,
        "pasa": not fallos,
        "resumen": "clip correcto",
    })


class _FakeFrames:
    """4 JPG falsos en un tmpdir (el módulo solo los lee en base64)."""

    def __init__(self, test):
        self.dir = tempfile.mkdtemp(prefix="final_review_")
        self.frames = []
        for i, role in enumerate(fr.FRAME_ROLES, start=1):
            path = os.path.join(self.dir, f"final_{i:02d}_{role}.jpg")
            with open(path, "wb") as f:
                f.write(b"\xff\xd8\xff\xe0fake-jpeg")
            self.frames.append({"path": path, "ts_clip": float(i),
                                "role": role})
        test.addCleanup(lambda: __import__("shutil").rmtree(
            self.dir, ignore_errors=True))


def _review_with_mock(test, gemini_text, clip_exists=True):
    """review_final_clip con fotogramas y Gemini falsos."""
    frames = _FakeFrames(test)
    clip = os.path.join(frames.dir, "clip.mp4")
    if clip_exists:
        with open(clip, "wb") as f:
            f.write(b"fake-mp4")
    calls = []
    if isinstance(gemini_text, Exception):
        def gemini_fn(parts):
            calls.append(parts)
            raise gemini_text
    else:
        def gemini_fn(parts):
            calls.append(parts)
            return gemini_text
    with mock.patch.object(
            fr, "extract_final_review_frames",
            return_value=frames.frames):
        result = fr.review_final_clip(
            clip, "Título de prueba", transcript_text="hola qué tal",
            gemini_fn=gemini_fn)
    return result, calls


class TestParse(unittest.TestCase):
    def test_json_valido(self):
        scores, motivos, fallos, resumen = fr._parse_final_review(
            _gemini_ok())
        self.assertEqual(scores["webcam_visible"], 8.0)
        self.assertEqual(fallos, [])
        self.assertEqual(resumen, "clip correcto")
        self.assertIn("bien", motivos["titulo_legible"])

    def test_json_entre_cercas(self):
        scores, _, fallos, _ = fr._parse_final_review(
            "```json\n" + _gemini_ok() + "\n```")
        self.assertEqual(scores["momento_comprensible"], 8.0)
        self.assertEqual(fallos, [])

    def test_falta_criterio_lanza(self):
        data = json.loads(_gemini_ok())
        del data["criterios"]["webcam_visible"]
        with self.assertRaises(ValueError):
            fr._parse_final_review(json.dumps(data))

    def test_fallo_desconocido_lanza(self):
        data = json.loads(_gemini_ok())
        data["fallos"] = ["no_existe"]
        with self.assertRaises(ValueError):
            fr._parse_final_review(json.dumps(data))

    def test_sin_json_lanza(self):
        with self.assertRaises(ValueError):
            fr._parse_final_review("no es json")


class TestDecide(unittest.TestCase):
    def test_todo_bien_pasa(self):
        scores = {c: 8.0 for c in fr.CRITERIA}
        self.assertEqual(fr._decide(scores, []), [])

    def test_un_criterio_bajo_frena(self):
        scores = {c: 8.0 for c in fr.CRITERIA}
        scores["subtitulos_ok"] = 4.9
        self.assertEqual(fr._decide(scores, []), ["subtitulos_ok"])

    def test_fallo_declarado_suma(self):
        scores = {c: 8.0 for c in fr.CRITERIA}
        self.assertEqual(fr._decide(scores, ["titulo_legible"]),
                         ["titulo_legible"])


class TestReview(unittest.TestCase):
    def test_pasa_con_fotogramas_correctos(self):
        result, calls = _review_with_mock(self, _gemini_ok())
        self.assertEqual(result["decision"], "aprobado")
        self.assertTrue(result["pasa"])
        self.assertEqual(result["revision"], "gemini")
        self.assertEqual(result["frames_n"], 4)
        # 1 texto (prompt) + 4 imágenes en el mismo mensaje.
        self.assertEqual(len(calls[0]), 5)
        self.assertIn("webcam", calls[0][0]["text"])
        self.assertIn("hola qué tal", calls[0][0]["text"])

    def test_frena_si_webcam_no_se_ve(self):
        result, _ = _review_with_mock(
            self, _gemini_ok(webcam_visible=2.0))
        self.assertEqual(result["decision"], "rechazado")
        self.assertFalse(result["pasa"])
        self.assertIn("webcam_visible", result["fallos"])
        self.assertIn("webcam", result["motivo"])

    def test_frena_si_subtitulos_tapan_el_juego(self):
        result, _ = _review_with_mock(
            self, _gemini_ok(subtitulos_ok=3.5))
        self.assertEqual(result["decision"], "rechazado")
        self.assertIn("subtitulos_ok", result["fallos"])

    def test_frena_si_titulo_duplicado(self):
        result, _ = _review_with_mock(
            self, _gemini_ok(titulo_legible=4.0))
        self.assertEqual(result["decision"], "rechazado")
        self.assertIn("titulo_legible", result["fallos"])

    def test_codigo_manda_sobre_el_pasa_de_gemini(self):
        # Gemini dice pasa=true con un criterio en 2: el código frena.
        data = json.loads(_gemini_ok(webcam_visible=2.0))
        data["pasa"] = True
        data["fallos"] = []
        result, _ = _review_with_mock(self, json.dumps(data))
        self.assertEqual(result["decision"], "rechazado")
        self.assertFalse(result["pasa"])

    def test_sin_gemini_queda_pendiente_no_aprobado(self):
        result, _ = _review_with_mock(
            self, RuntimeError("Gemini HTTP 500"))
        self.assertEqual(result["decision"], "pendiente_revision")
        self.assertFalse(result["pasa"])
        self.assertEqual(result["revision"], "no_disponible")

    def test_json_invalido_queda_pendiente(self):
        result, _ = _review_with_mock(self, "basura sin json")
        self.assertEqual(result["decision"], "pendiente_revision")
        self.assertFalse(result["pasa"])

    def test_sin_clip_queda_pendiente(self):
        result, _ = _review_with_mock(self, _gemini_ok(),
                                      clip_exists=False)
        self.assertEqual(result["decision"], "pendiente_revision")
        self.assertFalse(result["pasa"])


class TestEnabled(unittest.TestCase):
    def test_encendida_por_defecto(self):
        # Orden del usuario 2026-11-18 22:57 (100% automático): el
        # default vive en el código del repo, sin env vars a mano.
        with mock.patch.dict(os.environ, {}, clear=True):
            self.assertTrue(fr.final_review_enabled())
            self.assertTrue(fr.final_review_enabled({}))

    def test_env_la_enciende(self):
        with mock.patch.dict(os.environ,
                             {"FINAL_REVIEW_ENABLED": "1"}):
            self.assertTrue(fr.final_review_enabled())

    def test_env_la_apaga(self):
        with mock.patch.dict(os.environ,
                             {"FINAL_REVIEW_ENABLED": "0"}):
            self.assertFalse(fr.final_review_enabled())

    def test_cfg_la_enciende(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            self.assertTrue(fr.final_review_enabled(
                {"final_review": {"enabled": True}}))

    def test_cfg_la_apaga(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            self.assertFalse(fr.final_review_enabled(
                {"final_review": {"enabled": False}}))

    def test_maybe_apagada_no_valida(self):
        with mock.patch.dict(os.environ,
                             {"FINAL_REVIEW_ENABLED": "0"}):
            result = fr.maybe_review_final_clip(
                "/no/existe.mp4", "Título")
        self.assertEqual(result["decision"], "desactivada")
        self.assertIsNone(result["pasa"])


class TestFrames(unittest.TestCase):
    def test_tiempos_por_fraccion_de_duracion(self):
        clip = os.path.join(tempfile.mkdtemp(), "clip.mp4")
        with open(clip, "wb") as f:
            f.write(b"fake")
        seen = []

        def fake_extract(path, ts, out_path):
            seen.append(round(ts, 1))
            with open(out_path, "wb") as f:
                f.write(b"x")
            return out_path

        with mock.patch("clipagent.thumbnails.get_duration",
                        return_value=40.0), mock.patch(
                            "clipagent.thumbnails._extract_frame",
                            side_effect=fake_extract):
            frames = fr.extract_final_review_frames(clip)
        self.assertEqual([f["role"] for f in frames],
                         ["inicio", "payoff", "reaccion", "final"])
        self.assertEqual(seen, [2.0, 20.0, 28.8, 38.0])

    def test_sin_clip_lanza(self):
        with self.assertRaises(FileNotFoundError):
            fr.extract_final_review_frames("/no/existe.mp4")


class TestVodHook(unittest.TestCase):
    """Enganche VOD: empaquetar -> publicar pasa por la validación."""

    def _advance(self, decision):
        from clipagent.jobs import StepJobs
        from clipagent.workers import advance_pipeline
        tmp = tempfile.mkdtemp(prefix="final_review_vod_")
        self.addCleanup(lambda: __import__("shutil").rmtree(
            tmp, ignore_errors=True))
        clip = os.path.join(tmp, "clip.mp4")
        with open(clip, "wb") as f:
            f.write(b"fake-mp4")
        store = StepJobs(":memory:")
        job = {"id": 1, "clip_id": "vod1", "paso": "empaquetar",
               "payload": {"clip": clip, "title": "Título"},
               "prioridad": 0, "vod_job_id": None}
        verdict = {"decision": decision, "pasa": decision == "aprobado",
                   "hard_pasa": decision != "rechazado",
                   "needs_rerender": False, "rerender_reason": "",
                   "hard_failures": [],
                   "motivo": "", "resumen": decision,
                   "fallos": [], "scores": {}, "frames_n": 0,
                   "model": "", "transcript_words": 0,
                   "observe_only": True,
                   "face": {"pasa": decision != "rechazado"},
                   "template": {"pasa": decision != "rechazado"},
                   "gemini": {"decision": decision}}
        with mock.patch.dict(os.environ, {}, clear=True), mock.patch(
                "clipagent.final_review.validate_final_clip",
                return_value=verdict):
            cfg = {"validator": {
                "face_sample_every_s": 2, "face_min_frames": 8,
                "face_min_share": 0.5, "face_min_width_frac": 0.08,
                "min_score": 5, "max_rerenders": 1,
                "gemini_hard": False,
                "chain": ["facecrop", "edge", "yunet"]},
                "final_review": {
                    "enabled": decision != "desactivada",
                    "observe_only": True},
                "layout": {"split_variant": "auto"},
                "subtitles": {"default_style": "rdjavi"}}
            return advance_pipeline(store, job, {}, cfg=cfg)

    def test_aprobado_no_encola_publicar(self):
        # P0 #8: aprobado -> la cadena local termina en empaquetar;
        # ya no se auto-encola el job local "publicar" (lo ejecutaba
        # el publish loop --live y duplicaba con el Worker).
        self.assertEqual(self._advance("aprobado"), [])

    def test_rechazado_no_encola_nada(self):
        self.assertEqual(self._advance("rechazado"), [])

    def test_pendiente_no_encola_publicar(self):
        # P0 #8: pendiente -> tampoco se encola nada; el clip queda
        # en local para revisión humana (no en un job draft local).
        self.assertEqual(self._advance("pendiente_revision"), [])

    def test_desactivada_no_encola_publicar(self):
        # P0 #8: con la validación desactivada tampoco se encola
        # nada (antes salía en mode api); la cadena local termina
        # en empaquetar.
        self.assertEqual(self._advance("desactivada"), [])


class TestObserveOnly(unittest.TestCase):
    """Modo observación (2026-11-19, orden del usuario): el
    validador registra pero NO bloquea el encolado hasta calibrar
    con 10-20 clips reales. Un rechazado se reescribe como
    "observado" (pasa True, scores y fallos intactos)."""

    def _rechazado(self):
        scores = {c: 8.0 for c in fr.CRITERIA}
        scores["webcam_visible"] = 0.0
        return {
            "decision": "rechazado", "pasa": False,
            "revision": "gemini", "scores": scores,
            "motivos": {c: "bien" for c in fr.CRITERIA},
            "fallos": ["webcam_visible"],
            "motivo": "validación final: webcam visible (0.0)",
            "resumen": "sin webcam", "frames_n": 4,
            "model": "x", "transcript_words": 5,
        }

    def _maybe(self, verdict, env=None, cfg=None):
        with mock.patch.dict(os.environ, env or {}, clear=True), \
                mock.patch.object(fr, "review_final_clip",
                                  return_value=verdict):
            return fr.maybe_review_final_clip(
                "/clip.mp4", "Título", cfg=cfg)

    def test_observa_por_defecto(self):
        result = self._maybe(self._rechazado())
        self.assertEqual(result["decision"], "observado")
        self.assertTrue(result["pasa"])
        self.assertEqual(result["fallos"], ["webcam_visible"])
        self.assertEqual(result["scores"]["webcam_visible"], 0.0)
        self.assertTrue(result["observe_only"])
        self.assertIn("observación", result["motivo"])
        self.assertIn("webcam visible (0.0)", result["motivo"])

    def test_env_vuelve_a_bloquear(self):
        result = self._maybe(
            self._rechazado(),
            env={"FINAL_REVIEW_OBSERVE_ONLY": "0"})
        self.assertEqual(result["decision"], "rechazado")
        self.assertFalse(result["pasa"])
        self.assertFalse(result["observe_only"])

    def test_cfg_manda_sobre_env(self):
        # cfg False vence a env 1: vuelve a bloquear.
        result = self._maybe(
            self._rechazado(), env={"FINAL_REVIEW_OBSERVE_ONLY": "1"},
            cfg={"final_review": {"observe_only": False}})
        self.assertEqual(result["decision"], "rechazado")
        # cfg True vence a env 0: observa.
        result = self._maybe(
            self._rechazado(), env={"FINAL_REVIEW_OBSERVE_ONLY": "0"},
            cfg={"final_review": {"observe_only": True}})
        self.assertEqual(result["decision"], "observado")

    def test_aprobado_sigue_aprobado(self):
        verdict = self._rechazado()
        verdict["decision"] = "aprobado"
        verdict["pasa"] = True
        verdict["fallos"] = []
        verdict["motivo"] = ""
        result = self._maybe(verdict)
        self.assertEqual(result["decision"], "aprobado")
        self.assertTrue(result["pasa"])
        self.assertTrue(result["observe_only"])

    def test_prompt_acepta_avatar_vtuber(self):
        # La webcam puede ser un avatar (theburntpeanut): el prompt
        # exige "cara visible" y un avatar visible puntuaría 0 sin
        # esta línea. La marca de agua NO va en el prompt: verificado
        # 2026-11-19, no aparece en los fotogramas del clip 01:08 y
        # no es la causa del webcam 0.0 (la causa era el recorte).
        self.assertIn("VTuber", fr.FINAL_REVIEW_PROMPT)
        self.assertIn("avatar", fr.FINAL_REVIEW_PROMPT)
        self.assertNotIn("marca de agua", fr.FINAL_REVIEW_PROMPT)

    def test_observe_only_por_defecto(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            self.assertTrue(fr.final_review_observe_only())
            self.assertTrue(fr.final_review_observe_only({}))
        with mock.patch.dict(os.environ,
                             {"FINAL_REVIEW_OBSERVE_ONLY": "0"}):
            self.assertFalse(fr.final_review_observe_only())

    def test_desactivada_sigue_sin_validar(self):
        # Apagar la validación sigue apagándola: observación no es
        # lo mismo que desactivada.
        with mock.patch.dict(
                os.environ, {"FINAL_REVIEW_ENABLED": "0"}):
            result = fr.maybe_review_final_clip(
                "/no/existe.mp4", "Título")
        self.assertEqual(result["decision"], "desactivada")
        self.assertIsNone(result["pasa"])


if __name__ == "__main__":
    unittest.main()

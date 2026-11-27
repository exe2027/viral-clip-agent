"""Puerta de calidad para rubius_sin_webcam (spec §6.4/§10).

- La cara se salta SOLO para esta plantilla, y solo si la rama de
  plantilla pasó su prueba positiva de layout (`gameplay_puro`).
- Un rubius normal sin cara SIGUE fallando.
- `_criterion` guarda el motivo aunque pase (la píldora del panel
  y D1 leen ese motivo).
"""
import unittest

from clipagent.quality_gate import _criterion, build_quality_gate


def _review(plantilla="rubius", face_pasa=True, gameplay_puro=None,
            decision="aprobado"):
    template = {"checks": {
        "dimensiones": {"pasa": True},
        "titulo_banner": {"pasa": True},
    }}
    if gameplay_puro is not None:
        template["checks"]["gameplay_puro"] = {"pasa": gameplay_puro}
    return {
        "decision": decision,
        "plantilla": plantilla,
        "scores": {"momento_comprensible": 8, "titulo_legible": 8,
                   "webcam_visible": 8, "sin_barras_negras": 8},
        "face": {"pasa": face_pasa,
                 "motivo": "" if face_pasa else "sin cara verificada"},
        "template": template,
        "transcript_words": 5,
        "observe_only": True,
    }


class TestQualityGateSinWebcam(unittest.TestCase):
    def test_sin_webcam_con_gameplay_puro_pasa(self):
        gate = build_quality_gate(
            _review(plantilla="rubius_sin_webcam", face_pasa=None,
                    gameplay_puro=True),
            thumbnail_text="El momento concreto", no_speech=False)
        self.assertTrue(gate["pasa"])
        self.assertEqual(gate["decision"], "aprobado")
        self.assertTrue(gate["criterios"]["webcam"]["pasa"])
        self.assertEqual(
            gate["criterios"]["webcam"]["motivo"],
            "sin webcam por plantilla: layout gameplay puro verificado")

    def test_sin_webcam_sin_gameplay_puro_falla_duro(self):
        gate = build_quality_gate(
            _review(plantilla="rubius_sin_webcam", face_pasa=None,
                    gameplay_puro=False),
            thumbnail_text="El momento concreto", no_speech=False)
        self.assertFalse(gate["pasa"])
        self.assertEqual(gate["decision"], "rechazado")
        self.assertIn("webcam", gate["fallos_duros"])
        self.assertEqual(
            gate["criterios"]["webcam"]["motivo"],
            "layout gameplay puro no verificado")

    def test_sin_webcam_sin_medida_de_gameplay_puro_falla_cerrado(self):
        # Sin la prueba positiva no hay exención: falla cerrado.
        gate = build_quality_gate(
            _review(plantilla="rubius_sin_webcam", face_pasa=None,
                    gameplay_puro=None),
            thumbnail_text="El momento concreto", no_speech=False)
        self.assertFalse(gate["pasa"])
        self.assertIn("webcam", gate["fallos_duros"])

    def test_rubius_normal_sin_cara_sigue_fallando(self):
        gate = build_quality_gate(
            _review(plantilla="rubius", face_pasa=False),
            thumbnail_text="El momento concreto", no_speech=False)
        self.assertFalse(gate["pasa"])
        self.assertIn("webcam", gate["fallos_duros"])

    def test_criterion_guarda_motivo_aunque_pase(self):
        self.assertEqual(
            _criterion("webcam", True, True, "motivo que pasa")["motivo"],
            "motivo que pasa")


if __name__ == "__main__":
    unittest.main()

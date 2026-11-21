"""Modo batch del validador (2026-11-19): clasifica TODOS los
clips (Cola + publicados) y deja la lista de trabajo para el
batch de re-hacer a rubius en un JSON.

Reglas que se prueban aquí (con mocks; el validador real se
prueba en test_final_gate.py):
- pasa solo si cara + plantilla + Gemini pasan.
- la duda nunca pasa (cara dudosa = falla).
- falla en Cola -> re-render; falla publicado -> re-make-replace.
- sin vídeo / descarga fallida -> error, avisar-humano, sin
  inventar veredicto; el batch sigue con el siguiente.
- Gemini no disponible (pendiente_revision) -> reintentar, no
  re-renderizar.
- la auditoría manual (clips.json) se clasifica con las mismas
  reglas de acción en --dry-run, sin descargar ni validar.
"""

import json
import os
import tempfile
import unittest
from unittest import mock

from clipagent import validate_all as va


def _clip(clip_id, status="en_carpeta", title="GTA 6: prueba"):
    return {
        "id": clip_id,
        "title": title,
        "streamer": "Rubius",
        "status": status,
        "plantilla": None,
        "video_r2_key": f"clips/sebastian/{clip_id}/x.mp4",
        "language": "es",
    }


def _veredicto(pasa=True, decision="aprobado", hard=None, motivo=""):
    return {
        "decision": decision,
        "pasa": pasa,
        "plantilla": "rubius",
        "hard_pasa": pasa,
        "needs_rerender": not pasa,
        "rerender_reason": motivo,
        "hard_failures": hard or [],
        "motivo": motivo,
        "resumen": motivo,
        "frames_n": 10,
        "face": {"pasa": pasa},
        "template": {"pasa": pasa},
        "gemini": {"decision": decision},
    }


def _audit(
    clip_id,
    cara,
    formato,
    basura="no",
    status="en_carpeta",
    problemas=None,
    error=None,
):
    return {
        "id": clip_id,
        "title": "GTA 6: prueba",
        "streamer": "Rubius",
        "origin": "live",
        "created_at": 1795137543614,
        "status": status,
        "publish_blocked": 0,
        "language": "es",
        "score": 55,
        "video_r2_key": "clips/sebastian/1/x.mp4",
        "audit_v": 5,
        "cara": cara,
        "formato": formato,
        "formato_problemas": problemas or [],
        "notas": [],
        "basura": basura,
        "error": error,
    }


class TestClassifyValidation(unittest.TestCase):
    def test_pasa_no_hace_nada(self):
        rec = va.classify_validation(_clip(1), _veredicto(True))
        self.assertEqual(rec["outcome"], "pasa")
        self.assertEqual(rec["accion"], "nada")
        self.assertEqual(rec["capas"], [])

    def test_falla_cara_en_cola_es_re_render(self):
        res = _veredicto(
            False, "rechazado", ["cara"], "sin cara en la zona de cámara"
        )
        rec = va.classify_validation(_clip(2), res)
        self.assertEqual(rec["outcome"], "falla")
        self.assertEqual(rec["capas"], ["cara"])
        self.assertEqual(rec["accion"], "re-render")

    def test_falla_plantilla_en_cola_es_re_render(self):
        res = _veredicto(
            False,
            "rechazado",
            ["plantilla"],
            "banda de título fuera de la receta",
        )
        rec = va.classify_validation(_clip(3), res)
        self.assertEqual(rec["capas"], ["plantilla"])
        self.assertEqual(rec["accion"], "re-render")

    def test_falla_gemini_sola_es_falla(self):
        res = _veredicto(False, "rechazado", [], "Gemini: momento (2.0)")
        rec = va.classify_validation(_clip(4), res)
        self.assertEqual(rec["outcome"], "falla")
        self.assertEqual(rec["capas"], ["gemini"])
        self.assertEqual(rec["accion"], "re-render")

    def test_falla_publicado_es_re_make_replace(self):
        res = _veredicto(
            False, "rechazado", ["cara", "plantilla"], "sin cara y formato mal"
        )
        rec = va.classify_validation(_clip(7, status="publicado"), res)
        self.assertEqual(rec["outcome"], "falla")
        self.assertEqual(rec["accion"], "re-make-replace")

    def test_pendiente_revision_es_reintentar_no_rerender(self):
        res = _veredicto(
            False, "pendiente_revision", [], "Gemini no disponible"
        )
        rec = va.classify_validation(_clip(5), res)
        self.assertEqual(rec["outcome"], "pendiente")
        self.assertEqual(rec["accion"], "reintentar")

    def test_desactivada_no_hace_nada(self):
        res = _veredicto(None, "desactivada", [])
        rec = va.classify_validation(_clip(6), res)
        self.assertEqual(rec["outcome"], "desactivada")
        self.assertEqual(rec["accion"], "nada")


class TestClassifyAudit(unittest.TestCase):
    def test_bueno_pasa(self):
        rec = va.classify_audit_record(_audit(11, "con_cara", "correcto"))
        self.assertEqual(rec["outcome"], "pasa")
        self.assertEqual(rec["accion"], "nada")

    def test_sin_cara_es_re_render(self):
        rec = va.classify_audit_record(
            _audit(
                156, "sin_cara", "mal", problemas=["sin webcam en el vídeo"]
            )
        )
        self.assertEqual(rec["outcome"], "falla")
        self.assertEqual(rec["capas"], ["cara", "plantilla"])
        self.assertEqual(rec["accion"], "re-render")

    def test_dudoso_tampoco_pasa(self):
        # La duda nunca pasa: cara dudosa = falla aunque el
        # formato sea correcto (clip 12, avatar).
        rec = va.classify_audit_record(_audit(12, "dudoso", "correcto"))
        self.assertEqual(rec["outcome"], "falla")
        self.assertEqual(rec["capas"], ["cara"])
        self.assertEqual(rec["accion"], "re-render")

    def test_publicado_que_falla_es_re_make_replace(self):
        rec = va.classify_audit_record(
            _audit(
                7,
                "sin_cara",
                "mal",
                status="publicado",
                problemas=["sin webcam"],
            )
        )
        self.assertEqual(rec["outcome"], "falla")
        self.assertEqual(rec["accion"], "re-make-replace")

    def test_sin_video_es_error_avisar_humano(self):
        rec = va.classify_audit_record(
            _audit(1, "sin_video", "sin_video", error="sin video_r2_key")
        )
        self.assertEqual(rec["outcome"], "error")
        self.assertEqual(rec["accion"], "avisar-humano")
        self.assertIn("sin video_r2_key", rec["motivo"])

    def test_error_descarga_es_error_avisar_humano(self):
        rec = va.classify_audit_record(
            _audit(2, "error", "error", error="no se pudo descargar el vídeo")
        )
        self.assertEqual(rec["outcome"], "error")
        self.assertEqual(rec["accion"], "avisar-humano")


class TestValidateOne(unittest.TestCase):
    def test_sin_video_no_llama_al_validador(self):
        validate = mock.Mock()
        download = mock.Mock(
            side_effect=va.VideoMissingError("clip 9: sin MP4")
        )
        rec = va.validate_one(
            _clip(9), "/tmp", download_fn=download, validate_fn=validate
        )
        validate.assert_not_called()
        self.assertEqual(rec["outcome"], "error")
        self.assertEqual(rec["accion"], "avisar-humano")

    def test_validador_que_lanza_es_error_no_pasa(self):
        download = mock.Mock(return_value="/tmp/clip_10.mp4")
        validate = mock.Mock(side_effect=RuntimeError("boom"))
        rec = va.validate_one(
            _clip(10), "/tmp", download_fn=download, validate_fn=validate
        )
        self.assertEqual(rec["outcome"], "error")
        self.assertEqual(rec["accion"], "avisar-humano")

    def test_lleva_la_plantilla_resuelta_por_el_validador(self):
        download = mock.Mock(return_value="/tmp/clip_11.mp4")
        validate = mock.Mock(return_value=_veredicto(True))
        rec = va.validate_one(
            _clip(11), "/tmp", download_fn=download, validate_fn=validate
        )
        self.assertEqual(rec["plantilla"], "rubius")
        self.assertEqual(rec["outcome"], "pasa")


class TestRunBatch(unittest.TestCase):
    def _fakes(self, results):
        def download(clip_id, dest):
            with open(dest, "wb") as f:
                f.write(b"x")
            return dest

        def validate(path, title, plantilla=None, cfg=None):
            clip_id = int(os.path.basename(path).split("_")[1].split(".")[0])
            return results[clip_id]

        return download, validate

    def test_mezcla_escribe_json_con_resumen(self):
        clips = [
            _clip(11),
            _clip(12),
            _clip(7, "publicado"),
            _clip(13, "publicado"),
        ]
        results = {
            11: _veredicto(True),
            12: _veredicto(False, "rechazado", ["cara"], "sin cara"),
            7: _veredicto(
                False, "rechazado", ["cara", "plantilla"], "sin cara y mal"
            ),
            13: _veredicto(
                False, "pendiente_revision", [], "Gemini no disponible"
            ),
        }
        download, validate = self._fakes(results)
        with tempfile.TemporaryDirectory() as tmp:
            out = os.path.join(tmp, "res.json")
            payload = va.run_batch(
                clips,
                out_path=out,
                work_dir=tmp,
                tenant="sebastian",
                modo="panel",
                download_fn=download,
                validate_fn=validate,
                default_template="rubius",
            )
            self.assertEqual(payload["resumen"]["total"], 4)
            self.assertEqual(payload["resumen"]["pasa"], 1)
            self.assertEqual(payload["resumen"]["falla"], 2)
            self.assertEqual(payload["resumen"]["pendiente"], 1)
            self.assertEqual(payload["resumen"]["re_render"], 1)
            self.assertEqual(payload["resumen"]["re_make_replace"], 1)
            self.assertEqual(payload["resumen"]["reintentar"], 1)
            self.assertTrue(os.path.isfile(out))
            same = json.load(open(out, encoding="utf-8"))
            self.assertEqual(same["resumen"], payload["resumen"])
            by_id = {r["id"]: r for r in payload["clips"]}
            self.assertEqual(by_id[12]["accion"], "re-render")
            self.assertEqual(by_id[7]["accion"], "re-make-replace")
            self.assertEqual(by_id[13]["accion"], "reintentar")

    def test_descarga_fallida_registra_error_y_sigue(self):
        def download(clip_id, dest):
            if clip_id == 2:
                raise va.VideoMissingError("clip 2: sin MP4")
            with open(dest, "wb") as f:
                f.write(b"x")
            return dest

        validate = mock.Mock(return_value=_veredicto(True))
        with tempfile.TemporaryDirectory() as tmp:
            payload = va.run_batch(
                [_clip(1), _clip(2), _clip(3)],
                out_path=os.path.join(tmp, "res.json"),
                work_dir=tmp,
                tenant="sebastian",
                modo="panel",
                download_fn=download,
                validate_fn=validate,
            )
            self.assertEqual(payload["resumen"]["total"], 3)
            self.assertEqual(payload["resumen"]["pasa"], 2)
            self.assertEqual(payload["resumen"]["error"], 1)
            self.assertEqual(payload["resumen"]["avisar_humano"], 1)
            by_id = {r["id"]: r for r in payload["clips"]}
            self.assertEqual(by_id[2]["outcome"], "error")
            self.assertEqual(by_id[3]["outcome"], "pasa")

    def test_lista_vacia_escribe_resumen_cero(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = os.path.join(tmp, "res.json")
            payload = va.run_batch(
                [],
                out_path=out,
                work_dir=tmp,
                tenant="sebastian",
                modo="panel",
            )
            self.assertEqual(payload["resumen"]["total"], 0)
            self.assertTrue(os.path.isfile(out))


class TestDryRun(unittest.TestCase):
    def test_clasifica_la_auditoria_sin_descargar(self):
        records = [
            _audit(11, "con_cara", "correcto"),
            _audit(156, "sin_cara", "mal"),
            _audit(7, "sin_cara", "mal", status="publicado"),
            _audit(1, "sin_video", "sin_video", error="sin video_r2_key"),
        ]
        with tempfile.TemporaryDirectory() as tmp:
            out = os.path.join(tmp, "res.json")
            payload = va.run_dry_run(records, out, "sebastian")
            self.assertEqual(payload["modo"], "dry-run")
            self.assertEqual(payload["resumen"]["total"], 4)
            self.assertEqual(payload["resumen"]["pasa"], 1)
            self.assertEqual(payload["resumen"]["falla"], 2)
            self.assertEqual(payload["resumen"]["error"], 1)
            self.assertEqual(payload["resumen"]["re_render"], 1)
            self.assertEqual(payload["resumen"]["re_make_replace"], 1)
            self.assertEqual(payload["resumen"]["avisar_humano"], 1)
            by_id = {r["id"]: r for r in payload["clips"]}
            self.assertEqual(by_id[156]["accion"], "re-render")
            self.assertEqual(by_id[7]["accion"], "re-make-replace")


class TestMainDryRun(unittest.TestCase):
    def test_cli_dry_run_escribe_json_y_sale_0(self):
        records = [
            _audit(11, "con_cara", "correcto"),
            _audit(156, "sin_cara", "mal"),
        ]
        with tempfile.TemporaryDirectory() as tmp:
            audit = os.path.join(tmp, "clips.json")
            with open(audit, "w", encoding="utf-8") as f:
                json.dump(records, f)
            out = os.path.join(tmp, "res.json")
            code = va.main(
                [
                    "--dry-run",
                    "--from-audit",
                    audit,
                    "--out",
                    out,
                    "--tenant",
                    "sebastian",
                ]
            )
            self.assertEqual(code, 0)
            payload = json.load(open(out, encoding="utf-8"))
            self.assertEqual(payload["resumen"]["total"], 2)
            self.assertEqual(payload["resumen"]["pasa"], 1)
            self.assertEqual(payload["resumen"]["falla"], 1)

    def test_cli_dry_run_sin_audit_falla_en_voz_alta(self):
        code = va.main(["--dry-run"])
        self.assertEqual(code, 2)


class TestDownload(unittest.TestCase):
    def test_sin_sesion_falla_en_voz_alta(self):
        env = {
            k: v for k, v in os.environ.items() if k != "PANEL_SESSION_TOKEN"
        }
        with mock.patch.dict(os.environ, env, clear=True):
            with self.assertRaises(va.ValidateAllError):
                va.download_clip_video(1, "/tmp/no.mp4")


if __name__ == "__main__":
    unittest.main()

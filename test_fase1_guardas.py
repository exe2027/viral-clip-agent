"""Guardas y render de la Fase 1 (spec inglés correcto v7.1, §10.2).

Lo que YA cubren otros archivos (no se duplica aquí — verificado
por grep/lectura 2026-11-27):
- regla reconciliada p7775 ✓ / p3220 ✓ / centrada grande ✗ /
  y0 ≥ 0,50 ✗, `_caja_en_esquina` sola y `validate_cam_box(...,
  require_containment=False)`: test_yunet_facecam_fallback.py.
- `_cam_box_valida(..., avatar=True)` salta solo posición:
  test_layout_gate.py.
- decisión 4-tupla y contrato nuevo: test_template_decision.py.
- `resolve_template(vcfg, "rubius_sin_webcam")` sin ConfigError:
  test_config_sin_webcam.py.
- re-render live/VOD con layout_mode="game" y avatar en la
  re-validación: test_live_cutter_sin_webcam.py,
  test_vod_sin_webcam.py.
- 7 puntos reales con modo "game": test_render_gate.py
  (job), test_youtube_cut.py / test_facebook_cut.py
  (render_layout ["game"]), test_auto_clips_layout.py (VOD),
  test_live_cutter_sin_webcam.py (live).
- `_do_cut` pasa el facecam_box top-level:
  test_render_gate.py.
- p7775 con `clip.get("streamer") == "rubius"`:
  test_remake_all.py.
- `params["avatar"]` desde la rama vtuber: test_vtuber.py
  (test_decide_layout_is_split_with_both_boxes).

Lo que faltaba y cubre ESTE archivo:
- config.yaml sin llaves de nivel superior duplicadas (§6.1).
- modo forzado "game" en decide_layout (§6.2) + discriminador
  I-24 (el "game" de game_voice NO lleva "plantilla").
- `_template_fullscreen_game` sin KeyError sin `cam` + la
  prueba positiva `gameplay_puro` (§6.3/§6.5).
- `validate_final_clip` sin_webcam: centinelas de cara/layout
  + retirada de `webcam_visible` de Gemini (§6.3, I-23).
- `record["plantilla"]` / `record["avatar"]` tras el re-render
  live y `clip["plantilla"]` / POST VOD con la plantilla
  nueva (§4.4).
"""
import os
import subprocess
import tempfile
import unittest
from unittest import mock

import cv2
import numpy as np

REPO = os.path.dirname(os.path.abspath(__file__))
CONFIG_YAML = os.path.join(REPO, "config.yaml")


def _video_plano(tmp, dur=6):
    """Vídeo 320x180 de color plano (sin cara, sin avatar)."""
    path = os.path.join(tmp, "plano.mp4")
    subprocess.run(
        ["ffmpeg", "-y", "-v", "error", "-f", "lavfi",
         "-i", f"color=c=0x204060:s=320x180:r=30:d={dur}",
         "-c:v", "libx264", "-pix_fmt", "yuv420p", path],
        check=True)
    return path


class TestConfigSinDuplicados(unittest.TestCase):
    def test_ninguna_llave_de_nivel_superior_duplicada(self):
        # §6.1: config.yaml tenía validator:/templates:/sfx:
        # dos veces y yaml.safe_load tomaba la última en silencio.
        # La guarda lee el AST (no el dict cargado, que ya perdió
        # los duplicados) y exige cada llave una sola vez.
        import yaml
        with open(CONFIG_YAML, encoding="utf-8") as f:
            root = yaml.compose(f)
        self.assertIsNotNone(root, "config.yaml vacío")
        # MappingNode.value es [(clave, valor), ...].
        keys = [str(k.value) for k, _v in root.value]
        vistos = {}
        for k in keys:
            self.assertNotIn(k, vistos,
                             f"llave de nivel superior duplicada: {k}")
            vistos[k] = True
        for obligatoria in ("validator", "templates", "sfx",
                            "layout"):
            self.assertIn(obligatoria, vistos,
                          f"falta la llave {obligatoria}")


class TestDecideLayoutGameForzado(unittest.TestCase):
    def test_game_forzado_toda_la_duracion_con_plantilla(self):
        # §6.2: decide_layout con mode="game" devuelve un segmento
        # "game" para toda la duración + "plantilla":
        # "rubius_sin_webcam" en la decisión.
        from clipagent.render import decide_layout
        with tempfile.TemporaryDirectory() as tmp:
            path = _video_plano(tmp)
            dec = decide_layout(path, 6.0, cfg={"mode": "game"})
        self.assertEqual(dec["mode"], "game")
        self.assertEqual(dec["segments"], [
            {"start": 0.0, "end": 6.0, "layout": "game",
             "params": {}, "reason": "forzado sin_webcam"}])
        self.assertEqual(dec.get("plantilla"), "rubius_sin_webcam")

    def test_otros_forzados_no_llevan_plantilla(self):
        from clipagent.render import decide_layout
        with tempfile.TemporaryDirectory() as tmp:
            path = _video_plano(tmp)
            for forced in ("stack", "pip", "center"):
                dec = decide_layout(path, 6.0,
                                    cfg={"mode": forced})
                self.assertNotIn("plantilla", dec,
                                 f"forzado {forced} no debe llevar "
                                 f"plantilla")
                self.assertEqual(
                    [s["layout"] for s in dec["segments"]],
                    [forced])

    def test_game_de_game_voice_no_lleva_plantilla(self):
        # Discriminador I-24: el "game" de game_voice (voz del
        # juego en auto) NO lleva "plantilla"; solo el forzado
        # la lleva. Nadie lee decision["mode"] para distinguirlos.
        from clipagent.render import decide_layout
        with tempfile.TemporaryDirectory() as tmp:
            path = _video_plano(tmp)
            dec = decide_layout(path, 6.0, cfg={"mode": "auto"},
                                game_voice=[[0, 6]])
        self.assertTrue(dec["segments"])
        self.assertTrue(all(s["layout"] == "game"
                            for s in dec["segments"]),
                        f"segmentos: {dec['segments']}")
        self.assertNotIn("plantilla", dec)


def _textura(seed, w=1080, h=1080):
    """Textura con esquinas SIFT (círculos + rectángulos)."""
    r = np.random.default_rng(seed)
    img = np.zeros((h, w), dtype=np.uint8)
    for _ in range(60):
        x, y = int(r.integers(0, w)), int(r.integers(0, h))
        rad = int(r.integers(20, 120))
        cv2.circle(img, (x, y), rad, int(r.integers(40, 220)), -1)
    for _ in range(25):
        x, y = int(r.integers(0, w)), int(r.integers(0, h))
        ww = int(r.integers(30, 200))
        hh = int(r.integers(30, 200))
        cv2.rectangle(img, (x, y), (x + ww, y + hh),
                      int(r.integers(40, 220)), 3)
    return cv2.GaussianBlur(img, (3, 3), 0)


def _frame_rgb(top, bottom):
    """Fotograma 1080x1920 RGB con mitades dadas (grises -> RGB)."""
    arr = np.zeros((1920, 1080, 3), dtype=np.uint8)
    arr[0:840, :, :] = top[:, :, None]
    arr[840:1920, :, :] = bottom[:, :, None]
    return arr


class TestTemplateFullscreenGame(unittest.TestCase):
    """§6.3/§6.5: la rama sin_webcam no lee recipe["cam"]."""

    @classmethod
    def setUpClass(cls):
        from clipagent.config import load_validator_config
        cls.vcfg = load_validator_config()
        cls.recipe = cls.vcfg["templates"]["rubius_sin_webcam"]
        assert "cam" not in cls.recipe

    def _run(self, arrays):
        from clipagent.final_review import _template_fullscreen_game
        checks = {}

        def _check(name, ok, medicion, motivo=""):
            checks[name] = {"pasa": bool(ok), "medicion": medicion,
                            "motivo": motivo if not ok else ""}
            return bool(ok)

        # Sin KeyError aunque la receta no tenga "cam" (el bug que
        # cazaría _template_word_white / _layout_check_arrays).
        _template_fullscreen_game(_check, arrays, [], self.recipe,
                                  {"pasa": True}, vcfg=self.vcfg)
        return checks

    def test_mitades_distintas_pasan_gameplay_puro(self):
        top = _textura(11, h=840)
        bottom = _textura(22, h=1080)
        checks = self._run([(1.0, _frame_rgb(top, bottom))])
        self.assertIn("gameplay_puro", checks)
        self.assertTrue(checks["gameplay_puro"]["pasa"],
                        checks["gameplay_puro"])

    def test_mitades_duplicadas_fallan_gameplay_puro(self):
        # Un rubius duplicado mal etiquetado como sin_webcam:
        # arriba y abajo son la misma imagen -> rechazado.
        top = _textura(11, h=840)
        bottom = cv2.resize(top, (1080, 1080))
        checks = self._run([(1.0, _frame_rgb(top, bottom))])
        self.assertIn("gameplay_puro", checks)
        self.assertFalse(checks["gameplay_puro"]["pasa"],
                         checks["gameplay_puro"])
        self.assertGreaterEqual(
            int(checks["gameplay_puro"]["medicion"].split(
                "máx. ")[1].split(" celdas")[0]), 8)

    def test_mas_de_8_fotogramas_se_submuestrea(self):
        # Misma regla que §5.1: con >8 fotogramas se usan 8.
        top = _textura(11, h=840)
        bottom = _textura(22, h=1080)
        arrays = [(float(t), _frame_rgb(top, bottom))
                  for t in range(1, 25, 2)]
        checks = self._run(arrays)
        self.assertTrue(checks["gameplay_puro"]["pasa"],
                        checks["gameplay_puro"])
        self.assertIn("en 8 fotogramas",
                      checks["gameplay_puro"]["medicion"])

    def test_template_gate_despacha_por_nombre(self):
        # §6.5: template_gate con template_name sin_webcam ejecuta
        # la rama nueva (gameplay_puro presente) y NO los checks de
        # cámara de word_white.
        from test_final_gate import _make_video, _vcfg
        from clipagent.final_review import template_gate
        clip = _make_video("rubius_sin_webcam")
        res = template_gate(clip, _vcfg(), self.recipe,
                            srt_path=None, face_result={"pasa": True},
                            sfx_events=[],
                            template_name="rubius_sin_webcam")
        self.assertIn("gameplay_puro", res["checks"])
        self.assertNotIn("camara_ancho_completo", res["checks"])
        self.assertNotIn("cam_live", res["checks"])


class TestValidateSinWebcam(unittest.TestCase):
    """§6.3: centinelas de cara/layout + orden de Gemini (I-23)."""

    def _validate(self, gemini_fn):
        from test_final_gate import _cfg_dict, _make_video
        from clipagent.final_review import validate_final_clip
        return validate_final_clip(
            _make_video("rubius_sin_webcam"), "Título",
            cfg=_cfg_dict(), gemini_fn=gemini_fn,
            plantilla="rubius_sin_webcam")

    def test_centinelas_cara_y_layout(self):
        from test_final_gate import _gemini_ok
        res = self._validate(_gemini_ok)
        # Cara: centinela pasa=True (NUNCA None), motivo "" (M-18),
        # share None, sin medir cara.
        face = res["face"]
        self.assertEqual(face, {
            "pasa": True, "method": "sin_webcam", "motivo": "",
            "share": None, "frames": 0,
            "frames_total": res["frames_n"], "face_widths": [],
            "detail": ("sin_webcam: sin webcam por plantilla, "
                       "no aplica")})
        self.assertNotIn("cara", res["hard_failures"])
        # Layout: centinela (la prueba es gameplay_puro, dentro
        # de _template_fullscreen_game, no layout_gate).
        layout = res["layout"]
        self.assertTrue(layout["pasa"])
        self.assertEqual(layout["motivo"], "")
        self.assertIsNone(layout["sift_max_cells"])
        self.assertIn("gameplay_puro",
                      res["template"]["checks"])
        self.assertEqual(res["plantilla"], "rubius_sin_webcam")

    def test_gemini_webcam_visible_se_retira_sin_typeerror(self):
        # El juez dice "no hay cámara" en un sin_webcam: tiene
        # razón, no es un error. (1) se retira de gemini_fallos
        # ANTES de la rama de override; (2) esa rama NO se ejecuta
        # con method == "sin_webcam" (su formato de share: None
        # levantaría TypeError sin el blindaje (3)).
        from test_final_gate import _gemini_rechaza
        res = self._validate(_gemini_rechaza("webcam_visible"))
        self.assertNotIn("webcam_visible", res["fallos"])
        self.assertTrue(any(
            o.startswith("webcam_visible: sin webcam por plantilla")
            for o in res["overridden"]),
            res["overridden"])
        # La retirada es la (1), no la (2): el override de medida
        # no se dispara para sin_webcam.
        self.assertFalse(any(
            "cara verificada" in o for o in res["overridden"]))


class TestRerenderCallerActualizaPlantillaAvatar(unittest.TestCase):
    """§4.4: la plantilla y el avatar del re-render son los que
    valen — en el llamador live (record) y en el VOD (clip + POST).
    """

    def test_vod_post_lleva_la_plantilla_del_rerender(self):
        # burn_and_enqueue: la primera validación pide re-render;
        # _rerender_vod_clip devuelve review2 sin_webcam; el
        # llamador actualiza clip["plantilla"] ANTES del POST, así
        # que viral_payload["plantilla"] es la nueva (§4.4, §6.4).
        from test_vod_fallback import (
            FakeBridge, TestBurnAndEnqueueSeoPatch, _clip)
        from clipagent.vod_fallback import burn_and_enqueue
        harness = TestBurnAndEnqueueSeoPatch()
        harness.setUp()
        try:
            harness._patch_subtitles()
            primera = {"decision": "pendiente_revision",
                       "pasa": False, "hard_pasa": False,
                       "needs_rerender": True, "rerender_reason": "x",
                       "hard_failures": ["plantilla"],
                       "motivo": "x", "resumen": "x", "fallos": [],
                       "scores": {"momento_comprensible": 8,
                                  "titulo_legible": 8,
                                  "sin_barras_negras": 8,
                                  "webcam_visible": 8},
                       "observe_only": True,
                       "face": {"pasa": True},
                       "template": {"pasa": True, "checks": {}},
                       "gemini": {"decision": "pendiente_revision"}}
            review2 = {"decision": "aprobado", "pasa": True,
                       "hard_pasa": True, "needs_rerender": False,
                       "rerender_reason": "", "hard_failures": [],
                       "motivo": "", "resumen": "aprobado",
                       "fallos": [],
                       "scores": {"momento_comprensible": 8,
                                  "titulo_legible": 8,
                                  "sin_barras_negras": 8,
                                  "webcam_visible": 8},
                       "observe_only": True,
                       "plantilla": "rubius_sin_webcam",
                       "avatar": True,
                       "face": {"pasa": True,
                                "method": "sin_webcam"},
                       "template": {"pasa": True, "checks": {
                           "gameplay_puro": {"pasa": True}}},
                       "gemini": {"decision": "aprobado"}}
            video = harness.video
            with mock.patch(
                    "clipagent.final_review.validate_final_clip",
                    return_value=primera), \
                mock.patch(
                    "clipagent.vod_fallback._rerender_vod_clip",
                    return_value=(review2, video)) as m_rerender:
                bridge = FakeBridge()
                panel_id, why = burn_and_enqueue(
                    bridge, "rubius", "2932415021",
                    harness.tmp.name, _clip())
            self.assertEqual(panel_id, 101, why)
            m_rerender.assert_called_once()
            # El POST lleva la plantilla DEL RE-RENDER, no la del
            # primer corte (clip["plantilla"] se actualizó antes).
            payload = bridge.created[0]
            self.assertEqual(payload["viral"]["plantilla"],
                             "rubius_sin_webcam")
            # El PATCH no la repite: ya va en el POST.
            _cid, fields = bridge.patched[0]
            self.assertNotIn("plantilla",
                             fields.get("viral") or {})
        finally:
            harness.doCleanups()
            harness.tearDown()

    def test_live_record_toma_plantilla_y_avatar_del_rerender(self):
        # process_peak: tras _rerender_after_gate, record toma
        # final_review["plantilla"] y ["avatar"] (§4.4). Se maneja
        # el pico con mocks hasta el re-render: el corte, los
        # subtítulos, el SFX, el paquete y la validación son
        # inyectados; solo se prueba el cableado del llamador,
        # no el render.
        from clipagent.jobs import StepJobs
        from clipagent.live_cutter import LiveCutterSession
        with tempfile.TemporaryDirectory() as tmp:
            store = StepJobs(os.path.join(tmp, "queue.db"))
            try:
                session = LiveCutterSession(
                    "rubius", platform="twitch",
                    stream_id="2932415021", store=store,
                    out_dir=os.path.join(tmp, "clips"),
                    base_dir=tmp, dry_run=True, vod_id="2932415021",
                    latency_s=0, bridge=mock.Mock())
                session._report_event = mock.Mock()
                session.detector = None
                session.gate = mock.Mock()
                session.gate.check.return_value = (
                    True, "Grand Theft Auto VI", "live")
                session.gate.chat_path = None
                session.recorder = mock.Mock()
                session.recorder.wait_until.return_value = None
                session.recorder.extract_window.return_value = (
                    40.0, 1970.0)
                srt_path = os.path.join(tmp, "x.srt")
                open(srt_path, "w").close()
                final_clip = os.path.join(tmp, "final.mp4")
                open(final_clip, "wb").close()
                pkg_path = os.path.join(tmp, "package.json")
                final1 = {"decision": "pendiente_revision",
                          "pasa": False, "hard_pasa": False,
                          "needs_rerender": True,
                          "rerender_reason": "puerta dura",
                          "hard_failures": ["plantilla"],
                          "motivo": "puerta dura",
                          "resumen": "puerta dura", "fallos": [],
                          "scores": {}, "observe_only": True,
                          "plantilla": "rubius"}
                review2 = {"decision": "aprobado", "pasa": True,
                           "hard_pasa": True, "needs_rerender": False,
                           "rerender_reason": "",
                           "hard_failures": [], "motivo": "",
                           "resumen": "aprobado", "fallos": [],
                           "scores": {}, "observe_only": True,
                           "plantilla": "rubius_sin_webcam",
                           "avatar": True,
                           "rerendered_from": "x.mp4"}
                pub_pkg = {"best_title": "GTA 6: Rubius descubre",
                           "description": "d", "tags": [],
                           "hashtags": [], "thumbnail_text": "T",
                           "pkg_source": "rules"}
                with mock.patch(
                        "clipagent.detect.score_clip",
                        return_value={"total": 80.0}), \
                    mock.patch(
                        "clipagent.review.decide_render",
                        return_value=("directo", "")), \
                    mock.patch(
                        "clipagent.template_decision"
                        ".decide_template_for_segment",
                        return_value=("rubius", None, "firme",
                                      False)), \
                    mock.patch(
                        "clipagent.workers.cut_clip",
                        return_value={"layout": {}}), \
                    mock.patch(
                        "clipagent.workers.subtitle_clip",
                        return_value={"srt": srt_path,
                                      "transcript": None,
                                      "no_speech": False,
                                      "burn": final_clip}), \
                    mock.patch(
                        "clipagent.workers.apply_sfx",
                        return_value=(final_clip, {"events": []})), \
                    mock.patch(
                        "clipagent.live_cutter._definitive_package",
                        return_value=(pub_pkg, "hola", True)), \
                    mock.patch(
                        "clipagent.workers.package_clip_step",
                        return_value={"package_path": pkg_path}), \
                    mock.patch(
                        "clipagent.live_cutter.finalize_package",
                        return_value={"seo": {}}), \
                    mock.patch(
                        "clipagent.final_review"
                        ".transcript_text_for_srt",
                        return_value="hola"), \
                    mock.patch(
                        "clipagent.final_review.validate_final_clip",
                        return_value=final1), \
                    mock.patch(
                        "clipagent.quality_gate.build_quality_gate",
                        return_value={"decision": "aprobado",
                                      "pasa": True, "criterios": {},
                                      "fallos": [], "fallos_duros": [],
                                      "beats": {}}), \
                    mock.patch.object(
                        LiveCutterSession, "_rerender_after_gate",
                        return_value=(review2, final_clip,
                                      {"srt": srt_path,
                                       "transcript": None})), \
                    mock.patch.object(
                        LiveCutterSession, "_enqueue_panel"):
                    record = session.process_peak(
                        (2010, 104.0, 8.0,
                         {"msgs": 104, "risas": 11, "hype": 1,
                          "users": 90, "burst_score": 8.0,
                          "hype_score": 8.5, "hype_ratio": 4.2}))
                self.assertIsNotNone(record)
                self.assertEqual(record["plantilla"],
                                 "rubius_sin_webcam")
                self.assertTrue(record["avatar"])
                self.assertTrue(record["rerendered"])
            finally:
                store.close()


if __name__ == "__main__":
    unittest.main()

"""Re-hecho automático a rubius de los clips bloqueados
(2026-11-20): render desde el VOD -> validación final ->
subida + desbloqueo si pasa; re-render si falla.

Reglas que se prueban aquí (con mocks; el render rubius y el
validador reales se prueban en test_rubius.py y
test_final_gate.py):
- un clip sin source_id se marca avisar-humano (sin_vod) y el
  batch sigue con el siguiente (nunca muere por un clip).
- un fallo de validación reintenta con la otra caja de cámara
  y no bloquea: si el re-render pasa, se sube y se desbloquea.
- un pasa sube el vídeo y desbloquea (publish_blocked=0);
  un publicado que pasa se reemplaza pero NO se desbloquea.
- Gemini no disponible (pendiente_revision) -> reintentar,
  sin subir nada.
- sin voz + bajo las puertas -> no se sube (sin_voz); sin voz
  + pasa las puertas -> se re-hace sin subtítulos
  (subtitles_complete=0).
- un clip que falla tras todos los intentos queda falla /
  avisar-humano, sin subir ni desbloquear.
- el dry-run clasifica los bloqueados de la auditoría según
  tengan VOD resoluble o no, sin descargar ni renderizar.
"""

import json
import os
import tempfile
import unittest
from unittest import mock

from clipagent import remake_all as ra


CFG = {
    "scoring": {"seo_gate": 90, "viral_min": 50},
    "subtitles": {"publish_no_speech_if_passes_gates": True},
    "watermark": {"enabled": False},
}


def _clip(clip_id, status="en_carpeta", source_id=2932415021,
          start=3200, end=3248, viral=55, seo=None):
    return {
        "id": clip_id,
        "title": "GTA 6: prueba",
        "seo_title": "GTA 6: prueba",
        "streamer": "Rubius",
        "status": status,
        "origin": "vod",
        "language": "es",
        "source_id": source_id,
        "start_sec": start,
        "end_sec": end,
        "publish_blocked": 1,
        "viral_score": viral,
        "seo_scores": seo if seo is not None else {"tiktok": 95},
    }


def _veredicto(pasa=True, decision="aprobado", motivo=""):
    # Veredicto realista de validate_final_clip: con scores de
    # Gemini, palabras transcritas y checks de plantilla, para
    # que build_quality_gate (P0 #4) recalcule "aprobado" y no
    # "formato sin medir" / "sin voz verificable".
    return {
        "decision": decision,
        "pasa": pasa,
        "plantilla": "rubius",
        "hard_pasa": pasa,
        "needs_rerender": not pasa,
        "rerender_reason": motivo,
        "hard_failures": [] if pasa else ["cara"],
        "motivo": motivo,
        "resumen": motivo,
        "frames_n": 10,
        "face": {"pasa": pasa},
        "template": {"pasa": pasa, "checks": {
            "dimensiones": {"pasa": pasa},
            "titulo_banner": {"pasa": pasa},
        }},
        "gemini": {"decision": decision},
        "scores": {
            "sin_barras_negras": 9 if pasa else 1,
            "webcam_visible": 9 if pasa else 1,
            "momento_comprensible": 9 if pasa else 1,
            "titulo_legible": 9 if pasa else 1,
        },
        "transcript_words": 20 if pasa else 0,
    }


def _gate_aprobado():
    """Veredicto B4 que build_quality_gate calcula del _veredicto
    que pasa (los 6 criterios en True, sin fallos). P0 (2026-11-27):
    el veredicto lleva hard_pasa (lo necesita el Worker para el
    fallo abierto del juez)."""
    return {
        "decision": "aprobado",
        "pasa": True,
        "hard_pasa": True,
        "criterios": {
            "formato_9_16": {"pasa": True, "duro": True, "motivo": ""},
            "webcam": {"pasa": True, "duro": True, "motivo": ""},
            "hook": {"pasa": True, "duro": False, "motivo": ""},
            "titulo": {"pasa": True, "duro": True, "motivo": ""},
            "miniatura": {"pasa": True, "duro": True, "motivo": ""},
            "audio": {"pasa": True, "duro": True, "motivo": ""},
        },
        "fallos": [],
        "fallos_duros": [],
        "motivo": "",
    }


class _FakeBridge:
    """Panel mínimo: lista, registro, subida y PATCH."""

    def __init__(self, clips=None):
        self.tenant = "sebastian"
        self.clips = clips or []
        self.uploads = []
        self.updates = []
        self.get_clip_calls = []

    def list_clips(self):
        return self.clips

    def get_clip(self, clip_id):
        self.get_clip_calls.append(clip_id)
        for c in self.clips:
            if c.get("id") == clip_id:
                return dict(c)
        return {}

    def upload_video(self, clip_id, video_path):
        self.uploads.append((clip_id, video_path))
        return {"ok": True}

    def update_clip(self, clip_id, fields):
        self.updates.append((clip_id, fields))
        return {"ok": True}


def _fake_download(content=b"fake-segment"):
    def download(vod_id, start, end, out_path):
        with open(out_path, "wb") as f:
            f.write(content)
        return out_path

    return download


def _fake_transcribe(no_speech=False, calls=None):
    def transcribe(segment, srt=None, burn=None, cfg=None, lang="es",
                   title=""):
        if calls is not None:
            calls.append(segment)
        path = srt or (segment + ".srt")
        with open(path, "w", encoding="utf-8") as f:
            if not no_speech:
                f.write(
                    "1\n00:00:00,000 --> 00:00:02,000\n"
                    "esto es una prueba\n"
                )
        return {"srt": path, "transcript": None,
                "no_speech": no_speech}

    return transcribe


def _fake_render(calls=None, fail_first=False):
    def render(segment, start, duration, ass_path, out_path,
               banner_png=None, cam_box=None, cam_square=None,
               cfg=None, watermark=None):
        if calls is not None:
            calls.append((out_path, cam_box, cam_square))
        if fail_first and len(calls or []) == 1:
            raise RuntimeError("ffmpeg falló (simulado)")
        with open(out_path, "wb") as f:
            f.write(b"fake-rubius")
        return (1.0, out_path)

    return render


class _PatchBannerAss(unittest.TestCase):
    """Parchea el banner y el ASS reales (fuente Arial Black) en
    los tests de render: aquí solo se prueba la orquestación."""

    def setUp(self):
        super().setUp()
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.work = os.path.join(self.tmp.name, "work")
        os.makedirs(self.work)
        self.out = os.path.join(self.tmp.name, "out.json")
        patch_banner = mock.patch.object(
            ra, "make_banner_png",
            lambda title, path: open(path, "wb").write(b"banner"),
        )
        patch_ass = mock.patch.object(
            ra, "make_word_ass", lambda blocks, **kw: "[Script Info]\n",
        )
        self.addCleanup(patch_banner.stop)
        self.addCleanup(patch_ass.stop)
        patch_banner.start()
        patch_ass.start()

    def _remake(self, clip, bridge=None, **fns):
        return ra.remake_one(
            clip, bridge or _FakeBridge(), self.work, CFG, **fns,
        )


class TestResolveSource(unittest.TestCase):
    def test_sin_source_id_es_humano(self):
        src, motivo = ra.resolve_source(_clip(126, source_id=None))
        self.assertIsNone(src)
        self.assertIn("sin source_id", motivo)

    def test_source_id_cero_es_humano(self):
        src, _ = ra.resolve_source(_clip(126, source_id=0))
        self.assertIsNone(src)

    def test_rango_invalido_es_humano(self):
        src, motivo = ra.resolve_source(_clip(1, start=100, end=50))
        self.assertIsNone(src)
        self.assertIn("rango inválido", motivo)

    def test_con_vod_resuelve(self):
        src, motivo = ra.resolve_source(_clip(7))
        self.assertEqual(src, (2932415021, 3200, 3248))
        self.assertEqual(motivo, "")


class TestBoxStrategies(unittest.TestCase):
    def test_sin_deteccion_solo_default(self):
        strats = ra.box_strategies("seg.mp4", 48.0, CFG,
                                   detect_fn=lambda *a: None)
        self.assertEqual(len(strats), 1)
        self.assertEqual(strats[0][0], "default")

    def test_con_deteccion_dos_estrategias(self):
        strats = ra.box_strategies(
            "seg.mp4", 48.0, CFG,
            detect_fn=lambda *a: {"box": [100, 50, 400, 300],
                                  "method": "yunet"},
        )
        self.assertEqual([s[0] for s in strats],
                         ["detectada", "default"])
        self.assertEqual(strats[0][1], "400:300:100:50")

    def test_deteccion_igual_al_default_no_duplica(self):
        strats = ra.box_strategies(
            "seg.mp4", 48.0, CFG,
            detect_fn=lambda *a: {"box": [0, 0, 561, 365],
                                  "method": "yunet"},
        )
        self.assertEqual(len(strats), 1)
        self.assertEqual(strats[0][0], "default")

    def test_p7775_solo_rubius_antes_del_default(self):
        # §4.1.5: 4ª estrategia, solo Rubius, ANTES del default
        # (para probarse dentro de max_intentos=3).
        strats = ra.box_strategies(
            "seg.mp4", 48.0, CFG,
            detect_fn=lambda *a: None, influencer_name="Rubius")
        self.assertEqual([s[0] for s in strats],
                         ["p7775", "default"])
        self.assertEqual(strats[0][1], "561:365:0:626")
        self.assertEqual(strats[0][2], "365:365:90:626")

    def test_p7775_no_dispara_para_otro_streamer(self):
        strats = ra.box_strategies(
            "seg.mp4", 48.0, CFG,
            detect_fn=lambda *a: None, influencer_name="IlloJuan")
        self.assertEqual([s[0] for s in strats], ["default"])
        strats = ra.box_strategies("seg.mp4", 48.0, CFG,
                                   detect_fn=lambda *a: None)
        self.assertEqual([s[0] for s in strats], ["default"])

    def test_p7775_insensible_a_mayusculas(self):
        strats = ra.box_strategies(
            "seg.mp4", 48.0, CFG,
            detect_fn=lambda *a: None, influencer_name="RUBIUS")
        self.assertEqual(strats[0][0], "p7775")

    def test_p7775_se_dedupa_contra_detectada(self):
        # Detectada == p7775: no se queman dos intentos en el
        # mismo render.
        strats = ra.box_strategies(
            "seg.mp4", 48.0, CFG,
            detect_fn=lambda *a: {"box": [0, 626, 561, 365],
                                  "method": "yunet"},
            influencer_name="rubius")
        self.assertEqual([s[0] for s in strats],
                         ["detectada", "default"])


class TestRemakeOne(_PatchBannerAss):
    def test_sin_source_id_no_descarga_ni_sube(self):
        bridge = _FakeBridge()
        calls = []
        rec = self._remake(
            _clip(126, source_id=None), bridge,
            download_fn=_fake_download(),
            transcribe_fn=_fake_transcribe(calls=calls),
        )
        self.assertEqual(rec["outcome"], "sin_vod")
        self.assertEqual(rec["accion"], "avisar-humano")
        self.assertEqual(calls, [])
        self.assertEqual(bridge.uploads, [])
        self.assertEqual(bridge.updates, [])

    def test_pasa_sube_y_desbloquea(self):
        bridge = _FakeBridge()
        rec = self._remake(
            _clip(7), bridge,
            download_fn=_fake_download(),
            transcribe_fn=_fake_transcribe(),
            render_fn=_fake_render(),
            validate_fn=lambda *a, **kw: _veredicto(True),
            detect_fn=lambda *a: None,
        )
        self.assertEqual(rec["outcome"], "rehecho")
        self.assertEqual(rec["accion"], "desbloqueado")
        self.assertEqual(len(bridge.uploads), 1)
        self.assertEqual(bridge.uploads[0][0], 7)
        self.assertTrue(bridge.uploads[0][1].endswith("rubius_r1.mp4"))
        # P0 #4: el PATCH desbloquea Y refresca el quality_gate
        # con el veredicto del re-make (el viejo "rechazado" no
        # puede quedar: el gate del Worker lo seguiría leyendo).
        self.assertEqual(bridge.updates, [
            (7, {"publish_blocked": 0, "subtitles_complete": 1,
                 "viral": {"quality_gate": _gate_aprobado()}}),
        ])
        self.assertEqual(rec["quality_gate"], _gate_aprobado())

    def test_pasa_refresca_quality_gate_en_el_reemplazo_publicado(self):
        bridge = _FakeBridge()
        rec = self._remake(
            _clip(14, status="publicado"), bridge,
            download_fn=_fake_download(),
            transcribe_fn=_fake_transcribe(),
            render_fn=_fake_render(),
            validate_fn=lambda *a, **kw: _veredicto(True),
            detect_fn=lambda *a: None,
        )
        self.assertEqual(rec["outcome"], "rehecho-reemplazo")
        self.assertEqual(bridge.updates, [
            (14, {"subtitles_complete": 1,
                  "viral": {"quality_gate": _gate_aprobado()}}),
        ])
        self.assertEqual(rec["quality_gate"], _gate_aprobado())

    def test_publicado_que_pasa_reemplaza_pero_no_desbloquea(self):
        bridge = _FakeBridge()
        rec = self._remake(
            _clip(13, status="publicado"), bridge,
            download_fn=_fake_download(),
            transcribe_fn=_fake_transcribe(),
            render_fn=_fake_render(),
            validate_fn=lambda *a, **kw: _veredicto(True),
            detect_fn=lambda *a: None,
        )
        self.assertEqual(rec["outcome"], "rehecho-reemplazo")
        self.assertEqual(rec["accion"], "re-make-replace")
        self.assertEqual(len(bridge.uploads), 1)
        # Sin publish_blocked en el PATCH: desbloquearlo haría
        # que el auto-publicador lo volviera a publicar. El
        # quality_gate sí se refresca (P0 #4): el vídeo nuevo
        # tiene su propio veredicto.
        self.assertEqual(bridge.updates, [
            (13, {"subtitles_complete": 1,
                  "viral": {"quality_gate": _gate_aprobado()}}),
        ])

    def test_falla_de_validacion_reintenta_y_pasa(self):
        bridge = _FakeBridge()
        verdicts = [_veredicto(False, "rechazado", "sin cara"),
                    _veredicto(True)]
        render_calls = []
        rec = self._remake(
            _clip(16), bridge,
            download_fn=_fake_download(),
            transcribe_fn=_fake_transcribe(),
            render_fn=_fake_render(calls=render_calls),
            validate_fn=lambda *a, **kw: verdicts.pop(0),
            detect_fn=lambda *a: {"box": [100, 50, 400, 300],
                                  "method": "yunet"},
        )
        self.assertEqual(rec["outcome"], "rehecho")
        self.assertEqual(rec["accion"], "desbloqueado")
        # Dos estrategias de caja (detectada + default): dos
        # renders, dos validaciones, una subida (la del que pasó).
        self.assertEqual(len(render_calls), 2)
        self.assertEqual(render_calls[0][1], "400:300:100:50")
        self.assertEqual(len(bridge.uploads), 1)
        self.assertTrue(bridge.uploads[0][1].endswith("rubius_r2.mp4"))
        self.assertEqual(len(rec["intentos"]), 2)
        self.assertFalse(rec["intentos"][0]["pasa"])
        self.assertTrue(rec["intentos"][1]["pasa"])

    def test_render_roto_en_la_primera_estrategia_reintenta(self):
        bridge = _FakeBridge()
        render_calls = []
        rec = self._remake(
            _clip(16), bridge,
            download_fn=_fake_download(),
            transcribe_fn=_fake_transcribe(),
            render_fn=_fake_render(calls=render_calls, fail_first=True),
            validate_fn=lambda *a, **kw: _veredicto(True),
            detect_fn=lambda *a: {"box": [100, 50, 400, 300],
                                  "method": "yunet"},
        )
        self.assertEqual(rec["outcome"], "rehecho")
        self.assertEqual(len(render_calls), 2)
        self.assertEqual(len(bridge.uploads), 1)

    def test_falla_tras_todos_los_intentos_no_sube_ni_desbloquea(self):
        bridge = _FakeBridge()
        rec = self._remake(
            _clip(16), bridge,
            download_fn=_fake_download(),
            transcribe_fn=_fake_transcribe(),
            render_fn=_fake_render(),
            validate_fn=lambda *a, **kw: _veredicto(
                False, "rechazado", "sin cara en la zona de cámara"),
            detect_fn=lambda *a: {"box": [100, 50, 400, 300],
                                  "method": "yunet"},
        )
        self.assertEqual(rec["outcome"], "falla")
        self.assertEqual(rec["accion"], "avisar-humano")
        self.assertIn("sin cara", rec["motivo"])
        self.assertEqual(bridge.uploads, [])
        self.assertEqual(bridge.updates, [])

    def test_pendiente_revision_reintenta_sin_subir(self):
        bridge = _FakeBridge()
        rec = self._remake(
            _clip(16), bridge,
            download_fn=_fake_download(),
            transcribe_fn=_fake_transcribe(),
            render_fn=_fake_render(),
            validate_fn=lambda *a, **kw: _veredicto(
                False, "pendiente_revision",
                "Gemini no disponible"),
            detect_fn=lambda *a: None,
        )
        self.assertEqual(rec["outcome"], "pendiente")
        self.assertEqual(rec["accion"], "reintentar")
        self.assertEqual(bridge.uploads, [])
        self.assertEqual(bridge.updates, [])

    def test_descarga_rota_es_error_y_no_tumba_nada(self):
        bridge = _FakeBridge()

        def _boom(vod_id, start, end, out_path):
            raise RuntimeError("TwitchDownloaderCLI: 404")

        rec = self._remake(
            _clip(16), bridge, download_fn=_boom,
            transcribe_fn=_fake_transcribe(),
            render_fn=_fake_render(),
            validate_fn=lambda *a, **kw: _veredicto(True),
        )
        self.assertEqual(rec["outcome"], "error")
        self.assertEqual(rec["accion"], "avisar-humano")
        self.assertEqual(bridge.uploads, [])

    def test_sin_voz_bajo_puertas_no_sube(self):
        bridge = _FakeBridge()
        validate_calls = []
        rec = self._remake(
            _clip(25, viral=40, seo={"tiktok": 70}), bridge,
            download_fn=_fake_download(),
            transcribe_fn=_fake_transcribe(no_speech=True),
            render_fn=_fake_render(),
            validate_fn=lambda *a, **kw: validate_calls.append(1)
            or _veredicto(True),
            detect_fn=lambda *a: None,
        )
        self.assertEqual(rec["outcome"], "sin_voz")
        self.assertEqual(rec["accion"], "avisar-humano")
        # Sin voz y bajo las puertas: ni se valida ni se sube.
        self.assertEqual(validate_calls, [])
        self.assertEqual(bridge.uploads, [])

    def test_sin_voz_que_pasa_puertas_se_rehace_sin_subtitulos(self):
        bridge = _FakeBridge()
        rec = self._remake(
            _clip(25, viral=55, seo={"tiktok": 95}), bridge,
            download_fn=_fake_download(),
            transcribe_fn=_fake_transcribe(no_speech=True),
            render_fn=_fake_render(),
            validate_fn=lambda *a, **kw: _veredicto(True),
            detect_fn=lambda *a: None,
        )
        self.assertEqual(rec["outcome"], "rehecho")
        self.assertEqual(rec["accion"], "desbloqueado")
        self.assertTrue(rec["no_speech"])
        # P0 #4: el gate se refresca también sin voz. El audio es
        # criterio duro y sin voz verificable falla: el veredicto
        # nuevo es "rechazado" (como en el camino en vivo, que
        # manda estos clips a borrador). El clip queda desbloqueado
        # pero no publica hasta revisión humana (preview_seen=1,
        # P0 #2): el veredicto viejo no puede quedar.
        self.assertEqual(bridge.updates, [
            (25, {"publish_blocked": 0, "subtitles_complete": 0,
                  "viral": {"quality_gate": {
                      "decision": "rechazado", "pasa": False,
                      "criterios": {
                          "formato_9_16": {"pasa": True, "duro": True, "motivo": ""},
                          "webcam": {"pasa": True, "duro": True, "motivo": ""},
                          "hook": {"pasa": True, "duro": False, "motivo": ""},
                          "titulo": {"pasa": True, "duro": True, "motivo": ""},
                          "miniatura": {"pasa": True, "duro": True, "motivo": ""},
                          "audio": {"pasa": False, "duro": True, "motivo": "sin voz verificable"},
                      },
                      "fallos": ["audio"],
                      "fallos_duros": ["audio"],
                      "hard_pasa": True,
                      "motivo": "calidad: audio (sin voz verificable)",
                  }}}),
        ])
        self.assertEqual(rec["quality_gate"]["decision"], "rechazado")
        self.assertIs(rec["quality_gate"]["pasa"], False)


class TestRunBatch(_PatchBannerAss):
    def test_sin_vod_no_mata_el_batch(self):
        bridge = _FakeBridge()
        clips = [_clip(126, source_id=None), _clip(7)]
        payload = ra.run_batch(
            clips, bridge, out_path=self.out, work_dir=self.work,
            tenant="sebastian", cfg=CFG,
            download_fn=_fake_download(),
            transcribe_fn=_fake_transcribe(),
            render_fn=_fake_render(),
            validate_fn=lambda *a, **kw: _veredicto(True),
            detect_fn=lambda *a: None,
        )
        self.assertEqual(payload["resumen"]["total"], 2)
        self.assertEqual(payload["resumen"]["sin_vod"], 1)
        self.assertEqual(payload["resumen"]["rehecho"], 1)
        self.assertEqual(payload["clips"][0]["outcome"], "sin_vod")
        self.assertEqual(payload["clips"][1]["outcome"], "rehecho")
        self.assertEqual(len(bridge.uploads), 1)
        self.assertTrue(os.path.isfile(self.out))

    def test_un_clip_que_lanza_no_tumba_el_batch(self):
        bridge = _FakeBridge()
        # Un registro que no es dict lanza dentro de remake_one:
        # el batch lo registra como error y sigue con el siguiente.
        clips = ["registro-roto", _clip(7)]
        payload = ra.run_batch(
            clips, bridge, out_path=self.out, work_dir=self.work,
            tenant="sebastian", cfg=CFG,
            download_fn=_fake_download(),
            transcribe_fn=_fake_transcribe(),
            render_fn=_fake_render(),
            validate_fn=lambda *a, **kw: _veredicto(True),
            detect_fn=lambda *a: None,
        )
        self.assertEqual(payload["resumen"]["total"], 2)
        self.assertEqual(payload["resumen"]["error"], 1)
        self.assertEqual(payload["resumen"]["rehecho"], 1)
        self.assertEqual(payload["clips"][0]["outcome"], "error")


class TestLoadBlockedClips(unittest.TestCase):
    def test_solo_bloqueados_y_completa_el_registro(self):
        bridge = _FakeBridge([
            _clip(7, source_id=None),
            _clip(8, source_id=1),
            {**_clip(9), "publish_blocked": 0},
        ])
        # get_clip completa el source_id del 7 (la lista lo traía
        # vacío, como le pasa a los clips en vivo).
        bridge.clips[0] = {**_clip(7, source_id=None)}
        orig = bridge.get_clip

        def _full(clip_id):
            rec = orig(clip_id)
            if clip_id == 7:
                rec["source_id"] = 2932415021
            return rec

        bridge.get_clip = _full
        clips = ra.load_blocked_clips(bridge)
        self.assertEqual([c["id"] for c in clips], [7, 8])
        self.assertEqual(clips[0]["source_id"], 2932415021)


class TestDryRun(unittest.TestCase):
    def setUp(self):
        super().setUp()
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.out = os.path.join(self.tmp.name, "dry.json")

    def _audit(self, clip_id, blocked=1, status="en_carpeta"):
        return {
            "id": clip_id,
            "title": "GTA 6: prueba",
            "streamer": "Rubius",
            "origin": "live",
            "status": status,
            "publish_blocked": blocked,
            "language": "es",
            "score": 55,
            "cara": "sin_cara",
            "formato": "mal",
            "basura": "no",
        }

    def test_split_vod_y_sin_vod(self):
        records = [
            self._audit(16),
            self._audit(126),
            self._audit(7, status="publicado"),
            self._audit(23, blocked=0),  # no bloqueado: fuera
        ]
        source_map = {
            16: {"source_id": 2932415021, "start_sec": 3200,
                 "end_sec": 3248},
            7: {"source_id": 2932415021, "start_sec": 100,
                "end_sec": 148},
        }
        payload = ra.run_dry_run(records, source_map, self.out,
                                 "sebastian")
        self.assertEqual(payload["resumen"]["total"], 3)
        self.assertEqual(payload["resumen"]["rehecho"], 2)
        self.assertEqual(payload["resumen"]["sin_vod"], 1)
        by_id = {r["id"]: r for r in payload["clips"]}
        self.assertEqual(by_id[16]["outcome"], "rehacer")
        self.assertEqual(by_id[16]["accion"], "desbloqueado")
        self.assertEqual(by_id[126]["outcome"], "sin_vod")
        self.assertEqual(by_id[126]["accion"], "avisar-humano")
        self.assertEqual(by_id[7]["outcome"], "rehacer")
        self.assertEqual(by_id[7]["accion"], "re-make-replace")
        self.assertTrue(os.path.isfile(self.out))


class TestMainDryRun(unittest.TestCase):
    def test_cli_dry_run_contra_la_auditoria(self):
        with tempfile.TemporaryDirectory() as tmp:
            audit = os.path.join(tmp, "clips.json")
            smap = os.path.join(tmp, "map.json")
            out = os.path.join(tmp, "res.json")
            records = [
                {"id": 16, "title": "GTA 6: a", "streamer": "Rubius",
                 "origin": "vod", "status": "en_carpeta",
                 "publish_blocked": 1},
                {"id": 126, "title": "GTA 6: b", "streamer": "Rubius",
                 "origin": "live", "status": "en_carpeta",
                 "publish_blocked": 1},
            ]
            with open(audit, "w", encoding="utf-8") as f:
                json.dump(records, f)
            with open(smap, "w", encoding="utf-8") as f:
                json.dump({16: {"source_id": 2932415021,
                                "start_sec": 3200, "end_sec": 3248}},
                          f)
            rc = ra.main([
                "--dry-run", "--from-audit", audit,
                "--source-map", smap, "--out", out,
            ])
            self.assertEqual(rc, 0)
            with open(out, encoding="utf-8") as f:
                payload = json.load(f)
            self.assertEqual(payload["resumen"]["total"], 2)
            self.assertEqual(payload["resumen"]["sin_vod"], 1)
            self.assertEqual(payload["resumen"]["rehecho"], 1)

    def test_cli_sin_panel_falla_en_voz_alta(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = os.path.join(tmp, "res.json")
            with mock.patch.dict(os.environ, {}, clear=True):
                rc = ra.main(["--from-panel", "--out", out])
            self.assertEqual(rc, 2)
            self.assertFalse(os.path.exists(out))


class TestRemakePlantilla(_PatchBannerAss):
    """Spec §9.4 (rubius sin webcam): la plantilla la decide la
    fuente antes de renderizar y se propaga a la validación, al
    registro por clip y a los payloads de informe."""

    def _remake_con_decision(self, clip, decision, validate_calls):
        bridge = _FakeBridge()
        with mock.patch(
            "clipagent.template_decision"
            ".decide_template_for_segment",
            return_value=decision,
        ):
            rec = self._remake(
                clip, bridge,
                download_fn=_fake_download(),
                transcribe_fn=_fake_transcribe(),
                render_fn=_fake_render(),
                validate_fn=lambda *a, **kw: (
                    validate_calls.append(kw.get("plantilla")),
                    _veredicto(True),
                )[1],
                detect_fn=lambda *a: None,
            )
        return rec, bridge

    def test_decision_avatar_llega_a_la_validacion(self):
        # §5.2/I-23: la 4-tupla trae el flag avatar y remake_one
        # lo enhebra en validate_final_clip (sin él, 12 y 244 se
        # validarían como humanos y face_gate/layout (ii) los
        # tumbarían; el re-make no lo pasaba antes del fix).
        seen = {}
        bridge = _FakeBridge()
        with mock.patch(
            "clipagent.template_decision"
            ".decide_template_for_segment",
            return_value=("rubius", None, "firme", True),
        ):
            rec = self._remake(
                _clip(7), bridge,
                download_fn=_fake_download(),
                transcribe_fn=_fake_transcribe(),
                render_fn=_fake_render(),
                validate_fn=lambda *a, **kw: (
                    seen.update(kw), _veredicto(True),
                )[1],
                detect_fn=lambda *a: None,
            )
        self.assertEqual(rec["outcome"], "rehecho")
        self.assertIs(seen.get("avatar"), True)
        self.assertEqual(rec["intentos"][0]["avatar"], True)

    def test_decision_rubius_valida_con_rubius(self):
        calls = []
        rec, _ = self._remake_con_decision(
            _clip(7), ("rubius", [100, 50, 400, 300], "firme", False),
            calls)
        self.assertEqual(rec["outcome"], "rehecho")
        self.assertEqual(calls, ["rubius"])
        self.assertEqual(rec["plantilla"], "rubius")

    def test_decision_sin_webcam_valida_con_sin_webcam(self):
        calls = []
        rec, _ = self._remake_con_decision(
            _clip(7),
            ("rubius_sin_webcam", None, "firme", False),
            calls)
        self.assertEqual(rec["outcome"], "rehecho")
        self.assertEqual(calls, ["rubius_sin_webcam"])
        self.assertEqual(rec["plantilla"], "rubius_sin_webcam")

    def test_remake_pasa_streamer_a_box_strategies(self):
        # §4.1.5: el consumidor pasa clip.get("streamer") — el
        # clip construido solo trae "streamer" (load_blocked_clips
        # no copia "influencer_name"/"influencer", que son None).
        seen = {}

        def _fake_strategies(seg, dur, cfg, detect_fn=None,
                             influencer_name=None):
            seen["influencer_name"] = influencer_name
            return [("default", "561:365:0:0", "365:365:90:0")]

        calls = []
        with mock.patch.object(ra, "box_strategies", _fake_strategies):
            rec, _ = self._remake_con_decision(
                _clip(7),
                ("rubius", [100, 50, 400, 300], "firme", False),
                calls)
        self.assertEqual(seen["influencer_name"], "Rubius")
        self.assertEqual(rec["outcome"], "rehecho")

    def test_batch_mixto_y_batch_acuerdo(self):
        for decisiones, esperada in (
            ([("rubius", [1, 1, 2, 2], "firme", False),
              ("rubius_sin_webcam", None, "firme", False)], "mixto"),
            ([("rubius", [1, 1, 2, 2], "firme", False),
              ("rubius", [1, 1, 2, 2], "firme", False)], "rubius"),
        ):
            bridge = _FakeBridge()
            with mock.patch(
                "clipagent.template_decision"
                ".decide_template_for_segment",
                side_effect=list(decisiones),
            ):
                payload = ra.run_batch(
                    [_clip(7), _clip(8)], bridge,
                    out_path=self.out, work_dir=self.work,
                    tenant="sebastian", cfg=CFG,
                    download_fn=_fake_download(),
                    transcribe_fn=_fake_transcribe(),
                    render_fn=_fake_render(),
                    validate_fn=lambda *a, **kw: _veredicto(True),
                    detect_fn=lambda *a: None,
                )
            self.assertEqual(payload["plantilla"], esperada)
            self.assertEqual(
                [r["plantilla"] for r in payload["clips"]],
                [d[0] for d in decisiones])

    def test_dry_run_omite_plantilla(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = os.path.join(tmp, "dry.json")
            payload = ra.run_dry_run(
                [{"id": 16, "title": "t", "streamer": "Rubius",
                  "origin": "live", "status": "en_carpeta",
                  "publish_blocked": 1, "language": "es"}],
                {16: {"source_id": 2932415021, "start_sec": 3200,
                      "end_sec": 3248}},
                out, "sebastian")
            self.assertNotIn("plantilla", payload)


class TestRubiusGameCrop(_PatchBannerAss):
    """El crop del gameplay se adapta al segmento (run 45981380031).

    rubius.GAME_CROP (1440:1440:560:0) vale para 2560x1440; los
    VOD de Twitch bajan a 1920x1080 (VOD de Burn 2938677411) y
    el crop fijo tumbaba el render (ffmpeg: Invalid too big ...
    width '1440' or height '1440', mismo Parsed_crop_9 que el
    grafo default sobre un 1920x1080 sintético). 7 de los 12
    clips de §10.4 murieron así (183, 184, 186, 189, 191, 197
    y 12). El remake adapta el crop con las fracciones de
    render.RUBIUS_GAME_CROP_FRACS, como el camino en vivo v2.
    """

    def test_1080p_adapta_el_crop(self):
        with mock.patch(
            "clipagent.render._probe_size",
            return_value=(1920, 1080),
        ):
            self.assertEqual(
                ra._rubius_game_crop("seg.mp4"), "1080:1080:420:0")

    def test_1440p_mantiene_el_default(self):
        with mock.patch(
            "clipagent.render._probe_size",
            return_value=(2560, 1440),
        ):
            self.assertEqual(
                ra._rubius_game_crop("seg.mp4"), "1440:1440:560:0")

    def test_sin_tamano_devuelve_none(self):
        with mock.patch(
            "clipagent.render._probe_size",
            side_effect=RuntimeError("sin ffprobe"),
        ):
            self.assertIsNone(ra._rubius_game_crop("seg.mp4"))

    def test_remake_pasa_el_crop_adaptado_al_render(self):
        seen = {}

        def _render_con_crop(segment, start, duration, ass_path,
                             out_path, banner_png=None, cam_box=None,
                             cam_square=None, cfg=None, watermark=None,
                             game_crop=None):
            seen["game_crop"] = game_crop
            with open(out_path, "wb") as f:
                f.write(b"fake-rubius")
            return (1.0, out_path)

        bridge = _FakeBridge()
        with mock.patch(
            "clipagent.render._probe_size",
            return_value=(1920, 1080),
        ):
            rec = self._remake(
                _clip(7), bridge,
                download_fn=_fake_download(),
                transcribe_fn=_fake_transcribe(),
                render_fn=_render_con_crop,
                validate_fn=lambda *a, **kw: _veredicto(True),
                detect_fn=lambda *a: None,
            )
        self.assertEqual(rec["outcome"], "rehecho")
        self.assertEqual(seen["game_crop"], "1080:1080:420:0")


if __name__ == "__main__":
    unittest.main()

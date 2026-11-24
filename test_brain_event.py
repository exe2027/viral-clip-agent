#!/usr/bin/env python3
"""Pruebas del reporte de eventos de clip al panel (2026-11-19).

El cerebro reporta cada resultado de clip a POST /api/worker/event
(contrato CONGELADO): clip_cortado, clip_encolado, clip_descartado
o corte_fallido, con un mensaje de una línea en español (<=500
caracteres: streamer + título + resultado + números). El usuario
ve así dónde se pierden los clips.

- PanelBridge.report_event: POST correcto (URL, Bearer, body),
  kind inválido sin POST, mensaje normalizado a una línea y
  truncado a 500, nunca levanta sin sesión ni sin red.
- report_brain_event: con bridge inyectado, sin credenciales y
  con bridge roto (nunca levanta).
- live_cutter: cada descarte emite exactamente UN evento con el
  motivo (revisión <50, SEO post-corte <90, final_review
  rechazado, sin voz); como máximo un clip_descartado por clip
  (el primer motivo manda); corte fallido en el loop y en el
  panel; en dry_run no se reporta nada. "observado" (decisión
  nueva del validador final, 2026-11-19) cuenta como ENCOLADO,
  no descartado: solo "rechazado" frena.
- vod_fallback: cada rechazo de clip_passes_gates y cada fallo de
  quemado/encolado emite exactamente un clip_descartado con el
  motivo exacto; el encolado emite clip_encolado; el dedup
  (already_enqueued) NO reporta; en dry_run nada.
- Listones del fallback (2026-11-19, orden del usuario): viralidad
  >= 45, SEO >= 80 (bajados de 50/90). Un clip 47,0/89 que antes
  moría ahora pasa y se encola.
- Pre-chequeo de VOD (2026-11-19): un VOD que claramente no es
  GTA VI se salta sin gastar cupo ni enfriamiento; ante la duda
  se procesa.
"""
import json
import os
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from clipagent import panel_bridge  # noqa: E402
from clipagent.live_cutter import LiveCutterSession  # noqa: E402
from clipagent.panel_bridge import (  # noqa: E402
    MAX_EVENT_MESSAGE, PanelBridge, report_brain_event)


class _RecordingBridge:
    """Puente falso: registra eventos y encolados del panel."""

    def __init__(self, clips=None, fail_create=False):
        self.clips = list(clips or [])
        self.created = []
        self.uploaded = []
        self.patched = []
        self.events = []
        self.phases = []
        self.fail_create = fail_create
        self._next_id = 100

    def list_clips(self):
        return list(self.clips)

    def create_clip(self, payload):
        if self.fail_create:
            raise RuntimeError("panel caído")
        self.created.append(payload)
        self._next_id += 1
        clip = {"id": self._next_id}
        self.clips.append({"source_id": payload.get("source_id"),
                           "start_sec": payload.get("start_sec")})
        return clip

    def upload_video(self, clip_id, video_path):
        self.uploaded.append((clip_id, video_path))
        return {"ok": True}

    def update_clip(self, clip_id, patch):
        # auto-seo 2026-11-24: el PATCH de SEO tras crear el clip; si el
        # puente no lo tuviera, _enqueue_panel reportaria seo_sin_enviar.
        self.patched.append((clip_id, patch))
        return {"ok": True}

    def tracking_step(self, clip_id, step, message, estado="completado"):
        return {"ok": True}

    def preview_seen(self, clip_id):
        return {"ok": True}

    def report_event(self, kind, message):
        self.events.append((kind, message))
        return {"ok": True}

    def report_phase(self, phase, detail="", run_id=""):
        self.phases.append((phase, detail, run_id))
        return {"ok": True}


class _Resp:
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def read(self):
        return b'{"ok": true}'


class TestReportEvent(unittest.TestCase):
    def _bridge(self):
        return PanelBridge(base_url="https://example.invalid",
                           token="tok-123", tenant="sebastian")

    def test_post_a_api_worker_event_con_cuerpo_y_bearer(self):
        calls = []

        def fake_urlopen(req, timeout=60):
            calls.append(req)
            return _Resp()

        msg = ("rubius: «Título» encolado (viralidad 56.7; "
               "SEO YT 92 · TT 91 · IG 90 · FB 93)")
        with mock.patch("urllib.request.urlopen",
                        side_effect=fake_urlopen):
            res = self._bridge().report_event("clip_encolado", msg)
        self.assertEqual(res, {"ok": True})
        self.assertEqual(len(calls), 1)
        req = calls[0]
        self.assertEqual(req.method, "POST")
        self.assertTrue(req.full_url.endswith("/api/worker/event"))
        self.assertEqual(req.headers.get("Authorization"),
                         "Bearer tok-123")
        body = json.loads(req.data.decode("utf-8"))
        self.assertEqual(body, {"kind": "clip_encolado",
                                "message": msg})

    def test_kind_invalido_no_hace_post(self):
        calls = []

        def fake_urlopen(req, timeout=60):
            calls.append(req)
            return _Resp()

        with mock.patch("urllib.request.urlopen",
                        side_effect=fake_urlopen):
            self.assertIsNone(
                self._bridge().report_event("pico_detectado", "x"))
        self.assertEqual(calls, [])

    def test_kind_revalidado_si_hace_post(self):
        # auto-seo 2026-11-24: el loop marca cada intento con
        # "revalidado"; antes el puente lo rechazaba en local
        # ("evento kind inválido") y el anti-atasco de 24 h nunca
        # quedaba registrado en el panel.
        calls = []

        def fake_urlopen(req, timeout=60):
            calls.append(req)
            return _Resp()

        with mock.patch("urllib.request.urlopen",
                        side_effect=fake_urlopen):
            res = self._bridge().report_event(
                "revalidado", "clip 12 «X»: SEO 89 veredicto rechazado")
        self.assertEqual(res, {"ok": True})
        self.assertEqual(len(calls), 1)
        body = json.loads(calls[0].data.decode("utf-8"))
        self.assertEqual(body["kind"], "revalidado")

    def test_mensaje_se_normaliza_a_una_linea_y_500_max(self):
        calls = []

        def fake_urlopen(req, timeout=60):
            calls.append(req)
            return _Resp()

        with mock.patch("urllib.request.urlopen",
                        side_effect=fake_urlopen):
            self._bridge().report_event(
                "clip_descartado",
                "rubius: título\ncon saltos   y   espacios " + "x" * 600)
        body = json.loads(calls[0].data.decode("utf-8"))
        self.assertNotIn("\n", body["message"])
        self.assertNotIn("  ", body["message"])
        self.assertLessEqual(len(body["message"]), MAX_EVENT_MESSAGE)
        self.assertEqual(MAX_EVENT_MESSAGE, 500)

    def test_sin_sesion_no_levanta(self):
        bridge = PanelBridge(base_url="https://example.invalid",
                             tenant="sebastian")
        self.assertIsNone(bridge.report_event("clip_cortado", "x"))

    def test_error_del_panel_no_levanta(self):
        bridge = self._bridge()
        with mock.patch.object(bridge, "_request",
                               side_effect=panel_bridge.PanelError(
                                   "caído")):
            self.assertIsNone(bridge.report_event("clip_cortado", "x"))


class TestReportBrainEvent(unittest.TestCase):
    def test_sin_credenciales_no_hace_nada_y_no_levanta(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            self.assertFalse(report_brain_event("clip_cortado", "x"))

    def test_con_bridge_inyectado_lo_usa(self):
        rec = _RecordingBridge()
        self.assertTrue(report_brain_event(
            "clip_encolado", "rubius: encolado", bridge=rec))
        self.assertEqual(rec.events, [("clip_encolado",
                                       "rubius: encolado")])

    def test_bridge_sin_report_event_cae_a_from_env(self):
        class OldBridge:
            def report_phase(self, *a, **k):
                return {"ok": True}

        with mock.patch.dict(os.environ, {}, clear=True):
            self.assertFalse(report_brain_event(
                "clip_cortado", "x", bridge=OldBridge()))

    def test_error_del_bridge_no_levanta(self):
        class Broken:
            def report_event(self, *a, **k):
                raise RuntimeError("boom")

        self.assertFalse(report_brain_event("clip_cortado", "x",
                                            bridge=Broken()))


class _PassGate:
    """Gate que siempre deja pasar (GTA VI)."""

    def __init__(self, category="Grand Theft Auto VI"):
        self.category = category

    def check(self, ts=None, chat_path=None):
        return True, self.category, "helix"


class _FakeRecorder:
    """Recorder falso: crea un fichero vacío como ventana."""

    def wait_until(self, *a, **k):
        return None

    def extract_window(self, start, duration, out_path):
        with open(out_path, "wb") as f:
            f.write(b"fake")
        return (out_path, 0.0)


class TestLiveCutterEvents(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        from clipagent.jobs import StepJobs
        self.store = StepJobs(os.path.join(self.tmp.name, "queue.db"))
        self.addCleanup(self.store.close)

    def _session(self, bridge, dry_run=False):
        return LiveCutterSession(
            "rubius", platform="twitch", stream_id="2932415021",
            gate=_PassGate(), store=self.store,
            out_dir=os.path.join(self.tmp.name, "clips"),
            base_dir=self.tmp.name, bridge=bridge, dry_run=dry_run,
            vod_id="2932415021", latency_s=0)

    def _direct_peak(self):
        detail = {"msgs": 104, "risas": 11, "hype": 1, "users": 90,
                  "burst_score": 8.0, "hype_score": 8.5,
                  "hype_ratio": 4.2}
        return (2010, 104.0, 8.0, detail)

    def _revision_peak(self):
        # Viralidad auto 44,0 (chat 1,0 + humanos estimados 5,0):
        # va a revisión automática (40-44,9 tras bajar
        # REVIEW_VIRAL_MAX a 45; con el MAX viejo en 50 también).
        detail = {"msgs": 50, "risas": 5, "hype": 1, "users": 30}
        return (2010, 15.0, 1.0, detail)

    def _run_peak(self, session, peak, final_review, seo_statuses,
                  subtitle_blocked=False, review=None):
        """process_peak con el render mockeado: corte, subtítulos,
        paquete y validación final inyectados."""
        session.recorder = _FakeRecorder()
        pkg_path = os.path.join(self.tmp.name, "package.json")
        seo = {"youtube_shorts": 49, "tiktok": 52,
               "instagram_reels": 48, "facebook_reels": 50}

        def _cut(video, start, dur, out, **k):
            with open(out, "wb") as f:
                f.write(b"fake")
            return {"layout": {"mode": "split", "segments": [
                {"layout": "split"}]}}

        def _sub(*a, **k):
            return {"publish_blocked": subtitle_blocked,
                    "no_speech": subtitle_blocked,
                    "srt": None, "transcript": None}

        def _pre_corte(*a, **k):
            return ({"best_title": "Título Gemini",
                     "description_first_2_lines": "desc",
                     "tags": ["gta6"], "hashtags": [],
                     "thumbnail_text": "txt",
                     "pkg_source": "gemini"},
                    {p: {"score": v} for p, v in seo.items()})

        def _pkg_step(*a, **k):
            return {"package_path": pkg_path}

        def _finalize(*a, **k):
            return {"seo": dict(seo)}

        def _pub_status(viral, score, **_k):
            # publish_status recibe seo_gate/viral_min desde
            # config.yaml (2026-11-19, 80/45); el mock los ignora.
            return seo_statuses

        def _final(*a, **k):
            return dict(final_review)

        patches = [
            mock.patch("clipagent.workers.cut_clip", side_effect=_cut),
            mock.patch("clipagent.workers.subtitle_clip",
                       side_effect=_sub),
            mock.patch("clipagent.workers.pre_corte_package",
                       side_effect=_pre_corte),
            mock.patch("clipagent.workers.package_clip_step",
                       side_effect=_pkg_step),
            mock.patch("clipagent.live_cutter.build_title",
                       return_value="Título de prueba"),
            mock.patch("clipagent.live_cutter.finalize_package",
                       side_effect=_finalize),
            mock.patch("clipagent.priority.publish_status",
                       side_effect=_pub_status),
            mock.patch(
                "clipagent.final_review.validate_final_clip",
                side_effect=_final),
        ]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)
        if review is not None:
            rp = mock.patch("clipagent.review.review_peak",
                            return_value=dict(review))
            rp.start()
            self.addCleanup(rp.stop)
        return session.process_peak(peak)

    def _aprobado(self):
        return {"decision": "aprobado", "pasa": True, "motivo": "",
                "fallos": [], "resumen": "", "scores": {},
                "frames_n": 3, "hard_pasa": True,
                "needs_rerender": False, "rerender_reason": "",
                "hard_failures": [], "observe_only": True,
                "face": {"pasa": True}, "template": {"pasa": True},
                "gemini": {"decision": "aprobado"}}

    def _kinds(self, bridge):
        return [k for k, _m in bridge.events]

    def test_enqueue_panel_emite_clip_encolado_con_numeros(self):
        rec = _RecordingBridge()
        session = self._session(rec)
        record = self._run_peak(
            session, self._direct_peak(), self._aprobado(),
            ("publicable", "SEO 92 >= 90"))
        self.assertIsNotNone(record)
        self.assertEqual(len(rec.created), 1)
        self.assertEqual(self._kinds(rec),
                         ["clip_cortado", "clip_encolado"])
        _kind, msg = rec.events[1]
        self.assertIn("encolado", msg)
        self.assertIn("56.7", msg)
        self.assertIn("YT 49", msg)

    def test_enqueue_panel_fallido_emite_corte_fallido(self):
        rec = _RecordingBridge(fail_create=True)
        session = self._session(rec)
        record = self._run_peak(
            session, self._direct_peak(), self._aprobado(),
            ("publicable", "SEO 92 >= 90"))
        self.assertIsNotNone(record)
        self.assertEqual(len(rec.created), 0)
        kinds = self._kinds(rec)
        self.assertIn("clip_cortado", kinds)
        self.assertIn("corte_fallido", kinds)
        self.assertNotIn("clip_encolado", kinds)

    def test_revision_descartada_un_evento_con_motivo(self):
        rec = _RecordingBridge()
        session = self._session(rec)
        record = self._run_peak(
            session, self._revision_peak(), self._aprobado(),
            ("publicable", "ok"),
            review={"pasa": False, "viral_auto": 44.0,
                    "viral_revisado": 44.5, "revision": "gemini",
                    "motivo": "viralidad revisada 44.5 < 45"})
        self.assertIsNone(record)
        self.assertEqual(self._kinds(rec), ["clip_descartado"])
        _kind, msg = rec.events[0]
        self.assertIn("revisión", msg)
        self.assertIn("44", msg)

    def test_puerta_viral_descartada_reporta_motivo(self):
        # Con REVIEW_VIRAL_MAX en 45 (commit del padre), un pico
        # auto 45-49,9 va directo; si la puerta de publicación lo
        # descarta por viralidad (< 45), el evento lo dice con el
        # motivo exacto de la puerta (no se pierde en silencio).
        # El decide_render se mockea para no depender del valor
        # vigente de REVIEW_VIRAL_MAX.
        rec = _RecordingBridge()
        session = self._session(rec)
        with mock.patch("clipagent.review.decide_render",
                        return_value=("directo", "a render")):
            record = self._run_peak(
                session, self._revision_peak(), self._aprobado(),
                ("descartado", "viralidad 44 < 45: descartado"))
        self.assertIsNotNone(record)
        self.assertEqual(self._kinds(rec),
                         ["clip_cortado", "clip_descartado",
                          "clip_encolado"])
        _kind, msg = rec.events[1]
        self.assertIn("viralidad 44 < 45", msg)

    def test_puerta_dura_sin_cara_no_encola_ni_en_observacion(self):
        # 2026-11-19: la puerta dura de cara frena el encolado
        # aunque el modo observación esté ON. El re-render no es
        # posible aquí (ventana falsa, sin cara en la fuente): el
        # clip queda para revisión humana con motivo visible,
        # nunca bloqueado en silencio.
        rec = _RecordingBridge()
        session = self._session(rec)
        hard_fail = {"decision": "rechazado", "pasa": False,
                     "hard_pasa": False, "needs_rerender": True,
                     "rerender_reason": "sin cara verificada",
                     "hard_failures": ["cara"],
                     "needs_human_review": True,
                     "motivo": "sin cara verificada en el vídeo final",
                     "resumen": "sin cara", "fallos": ["cara"],
                     "scores": {}, "frames_n": 10, "model": "",
                     "transcript_words": 0, "observe_only": True,
                     "face": {"pasa": False, "duda": False},
                     "template": {"pasa": True},
                     "gemini": {"decision": "aprobado"}}
        record = self._run_peak(
            session, self._direct_peak(), hard_fail,
            ("publicable", "SEO 92 >= 90"))
        self.assertIsNotNone(record)
        self.assertEqual(len(rec.created), 0)
        self.assertEqual(self._kinds(rec),
                         ["clip_cortado", "clip_descartado"])
        _kind, msg = rec.events[1]
        self.assertIn("revisión humana", msg)
        self.assertTrue(record["final_review"]["needs_human_review"])

    def test_sin_voz_con_puertas_se_encola_sin_bloquear(self):
        # Regla 2026-11-19 18:41: sin voz + puertas (viral 56,7,
        # publicable) -> se encola PUBLICABLE sin subtítulos: sin
        # descarte, subtitles_complete=0 honesto, y el payload no
        # pide transcripción humana.
        rec = _RecordingBridge()
        session = self._session(rec)
        record = self._run_peak(
            session, self._direct_peak(), self._aprobado(),
            ("publicable", "ok"), subtitle_blocked=True)
        self.assertIsNotNone(record)
        self.assertFalse(record["publish_blocked"])
        self.assertFalse(record["subtitles_complete"])
        self.assertTrue(record["no_speech"])
        self.assertEqual(self._kinds(rec),
                         ["clip_cortado", "clip_encolado"])
        self.assertEqual(len(rec.created), 1)
        self.assertEqual(
            rec.created[0]["needs_human_transcription"], 0)
        self.assertEqual(rec.created[0]["subtitles_complete"], 0)

    def test_sin_voz_bajo_puertas_se_encola_bloqueado(self):
        # Sin voz + ninguna plataforma publicable -> BLOQUEADO,
        # como antes: un solo descarte con "sin voz", y el clip
        # sigue entrando a la Cola bloqueado (sin jobs).
        rec = _RecordingBridge()
        session = self._session(rec)
        record = self._run_peak(
            session, self._direct_peak(), self._aprobado(),
            ("bloqueado_seo", "SEO < 80"), subtitle_blocked=True)
        self.assertIsNotNone(record)
        self.assertTrue(record["publish_blocked"])
        self.assertFalse(record["subtitles_complete"])
        self.assertEqual(self._kinds(rec),
                         ["clip_cortado", "clip_descartado",
                          "clip_encolado"])
        _kind, msg = rec.events[1]
        self.assertIn("sin voz", msg)
        self.assertEqual(len(rec.created), 1)
        self.assertEqual(
            rec.created[0]["needs_human_transcription"], 1)

    def test_validacion_final_rechazada_no_encola(self):
        rec = _RecordingBridge()
        session = self._session(rec)
        record = self._run_peak(
            session, self._direct_peak(),
            {"decision": "rechazado", "pasa": False,
             "motivo": "webcam no visible", "fallos": ["webcam"],
             "resumen": "", "scores": {}, "frames_n": 3},
            ("publicable", "ok"))
        self.assertIsNotNone(record)
        self.assertEqual(len(rec.created), 0)
        self.assertEqual(self._kinds(rec),
                         ["clip_cortado", "clip_descartado"])
        _kind, msg = rec.events[1]
        self.assertIn("validación final", msg)
        self.assertIn("webcam", msg)

    def test_seo_post_corte_un_descarte_con_los_4_numeros(self):
        rec = _RecordingBridge()
        session = self._session(rec)
        record = self._run_peak(
            session, self._direct_peak(), self._aprobado(),
            ("bloqueado_seo", "SEO < 80"))
        self.assertIsNotNone(record)
        self.assertEqual(self._kinds(rec),
                         ["clip_cortado", "clip_descartado",
                          "clip_encolado"])
        _kind, msg = rec.events[1]
        self.assertIn("SEO post-corte", msg)
        self.assertIn("YT 49", msg)
        self.assertIn("TT 52", msg)
        self.assertIn("IG 48", msg)
        self.assertIn("FB 50", msg)

    def test_observado_encola_como_aprobado_sin_descarte(self):
        # Decisión nueva del validador final (2026-11-19): un
        # rechazado reescrito con pasa True; solo "rechazado"
        # frena, "observado" encola como un aprobado.
        rec = _RecordingBridge()
        session = self._session(rec)
        record = self._run_peak(
            session, self._direct_peak(),
            {"decision": "observado", "pasa": True,
             "motivo": "webcam 0.0 en observación",
             "fallos": ["webcam"], "resumen": "", "scores": {},
             "frames_n": 3},
            ("publicable", "ok"))
        self.assertIsNotNone(record)
        self.assertEqual(len(rec.created), 1)
        self.assertEqual(self._kinds(rec),
                         ["clip_cortado", "clip_encolado"])

    def test_corte_fallido_en_el_loop_emite_evento(self):
        rec = _RecordingBridge()
        session = self._session(rec)
        session.recorder = None  # process_peak levanta sin recorder
        session._cut_queue.put(self._direct_peak())
        session.start_cutter_thread()
        try:
            session._cut_queue.join()
        finally:
            session.stop_cutter_thread()
        self.assertTrue(any("fallo al cortar" in (s.get("reason") or "")
                            for s in session.skipped))
        self.assertEqual(self._kinds(rec), ["corte_fallido"])
        self.assertIn("corte fallido", rec.events[0][1])

    def test_discard_solo_una_vez_por_clip(self):
        rec = _RecordingBridge()
        session = self._session(rec)
        session._report_discard_once("live:rubius:x:p1", "primer motivo")
        session._report_discard_once("live:rubius:x:p1", "segundo motivo")
        session._report_discard_once("live:rubius:x:p2", "otro clip")
        self.assertEqual(rec.events,
                         [("clip_descartado", "primer motivo"),
                          ("clip_descartado", "otro clip")])

    def test_dry_run_no_reporta_nada(self):
        rec = _RecordingBridge()
        session = self._session(rec, dry_run=True)
        session._report_event("clip_cortado", "x")
        session._report_discard_once("live:rubius:x:p1", "x")
        self.assertEqual(rec.events, [])


# ---------------------------------------------------------------------------
# Fallback VOD: eventos + rescate limítrofe (opción c)
# ---------------------------------------------------------------------------

def _seo(score, worst_platform=None, worst_score=None):
    out = {p: {"score": score, "status": "ok", "can_publish": True}
           for p in ("youtube_shorts", "tiktok", "instagram_reels",
                     "facebook_reels")}
    if worst_platform:
        out[worst_platform] = {"score": worst_score, "status": "ok",
                               "can_publish": False}
    return out


def _manifest_clip(**over):
    """Clip de manifiesto (total 47,0, SEO 94): con los listones
    45/80 pasa; con los viejos 50/90 moría por viralidad."""
    base = {
        "n": 1, "file": "clips/clip_01.mp4", "peak": 3220.0,
        "start": 3200, "duration": 48, "score": 30.0,
        "risas": 5, "hype": 1,
        "title_es": "Rubius descubre los cromos en GTA 6",
        "chat_examples": [{"user": "u1", "text": "qué golazo"}],
        "viral": {"best_title": "GTA 6: Rubius descubre los cromos",
                  "game": "GTA VI",
                  "description": "Rubius descubre los cromos.",
                  "description_first_2_lines": "Rubius descubre.",
                  "tags": ["#GTA6"], "hashtags": ["#GTA6"],
                  "thumbnail_text": "CROMOS", "pkg_source": "rules"},
        "viral_score": {"total": 47.0, "band": "revisar",
                        "chat_multiple": 1.9},
        "seo_scores": _seo(94),
        "moment_card": {"game": "GTA VI", "streamer": "rubius",
                        "risas": 5, "hype": 1, "viral_multiple": 1.9},
        "visible": "cams+gameplay",
        "layout": {"mode": "split", "segments": [
            {"layout": "split"}]},
    }
    base.update(over)
    return base


_VOD = {"vod_id": "2932415021",
        "url": "https://www.twitch.tv/videos/2932415021",
        "title": "GTA 6", "category": "Grand Theft Auto VI",
        "live": False}
_STREAMERS_ES = [
    {"name": "rubius", "platform": "twitch", "language": "es",
     "enabled": True, "estado": "concedido"},
    {"name": "illojuan", "platform": "twitch", "language": "es",
     "enabled": True, "estado": "concedido"},
]


class TestFallbackEvents(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.state = os.path.join(self.tmp.name, "state.json")
        self.out = os.path.join(self.tmp.name, "out")

    def _scan(self, live=None):
        return lambda only=None: {"live": live or [], "skipped": [],
                                  "checked": 1,
                                  "twitch_mode": "helix"}

    def _run(self, manifest, bridge=None, enqueue_fn=None,
             auto_clips_fn=None):
        from clipagent.vod_fallback import run
        if auto_clips_fn is None:
            def _auto(url, out_dir, top_n=2, lang="es"):
                return manifest
            auto_clips_fn = _auto
        with mock.patch("clipagent.streamers.load_streamers",
                        return_value=_STREAMERS_ES):
            return run(
                max_vods=1, max_clips=2, top_n=2,
                state_path=self.state, out_root=self.out,
                bridge=bridge if bridge is not None
                else _RecordingBridge(),
                scan_fn=self._scan(),
                latest_vod_fn=lambda entry: (
                    _VOD if entry.get("name") == "rubius" else None),
                auto_clips_fn=auto_clips_fn,
                enqueue_fn=enqueue_fn)

    def test_rechazo_de_gates_un_evento_con_motivo_exacto(self):
        rec = _RecordingBridge()
        bad = _manifest_clip(
            viral_score={"total": 44.0, "chat_multiple": 1.1})
        n, _ = self._run({"clips": [bad]}, bridge=rec)
        self.assertEqual(n, 0)
        self.assertEqual(len(rec.events), 1)
        kind, msg = rec.events[0]
        self.assertEqual(kind, "clip_descartado")
        self.assertIn("viralidad 44.0 < 45", msg)

    def test_encolado_un_evento_con_viralidad_y_seo(self):
        rec = _RecordingBridge()
        good = _manifest_clip(
            viral_score={"total": 53.8, "chat_multiple": 1.9})
        seen = {}

        def _enqueue(bridge, streamer, vod_id, out_dir, clip,
                     lang="es"):
            seen["clip"] = clip
            return 101, ""

        n, _ = self._run({"clips": [good]}, bridge=rec,
                         enqueue_fn=_enqueue)
        self.assertEqual(n, 1)
        self.assertEqual(len(rec.events), 1)
        kind, msg = rec.events[0]
        self.assertEqual(kind, "clip_encolado")
        self.assertIn("53.8", msg)
        self.assertIn("YT 94", msg)

    def test_sin_voz_con_puertas_se_encola(self):
        # Regla 2026-11-19 18:41: sin voz + viral 53,8 / SEO 94
        # (pasa 45/80) -> el VOD se encola publicable sin
        # subtítulos. El mock trae publish_blocked=True viejo:
        # burn_and_enqueue ya no se fía de ese flag.
        rec = _RecordingBridge()
        out_dir = os.path.join(self.out, "2932415021", "clips")
        os.makedirs(out_dir, exist_ok=True)
        with open(os.path.join(out_dir, "clip_01.mp4"), "wb") as f:
            f.write(b"fake-mp4")
        good = _manifest_clip(
            viral_score={"total": 53.8, "chat_multiple": 1.9})
        # El vídeo es falso (sin duración medible): la puerta dura
        # real lo rechazaría; el test cubre el encolado sin voz,
        # no el gate (ver _patch_review en test_vod_fallback.py).
        verdict = {"decision": "aprobado", "pasa": True,
                   "hard_pasa": True, "needs_rerender": False,
                   "rerender_reason": "", "hard_failures": [],
                   "motivo": "", "resumen": "aprobado",
                   "fallos": [], "scores": {}, "frames_n": 0,
                   "model": "", "transcript_words": 0,
                   "observe_only": True,
                   "face": {"pasa": True}, "template": {"pasa": True},
                   "gemini": {"decision": "aprobado"}}
        with mock.patch(
                "clipagent.workers.subtitle_clip",
                return_value={"publish_blocked": True,
                              "no_speech": True, "srt": None,
                              "transcript": None}), mock.patch(
                "clipagent.final_review.validate_final_clip",
                return_value=verdict):
            n, _ = self._run({"clips": [good]}, bridge=rec)
        self.assertEqual(n, 1)
        self.assertEqual(len(rec.created), 1)
        self.assertEqual(rec.created[0]["subtitles_complete"], 0)
        self.assertEqual(
            rec.created[0]["needs_human_transcription"], 0)
        self.assertEqual([k for k, _m in rec.events],
                         ["clip_encolado"])

    def test_ya_en_cola_no_reporta_nada(self):
        rec = _RecordingBridge(
            clips=[{"source_id": 2932415021, "start_sec": 3200}])
        good = _manifest_clip(
            viral_score={"total": 53.8, "chat_multiple": 1.9})
        called = []

        def _enqueue(*a, **k):
            called.append(a)
            return 101, ""

        n, _ = self._run({"clips": [good]}, bridge=rec,
                         enqueue_fn=_enqueue)
        self.assertEqual(n, 0)
        self.assertEqual(called, [])
        self.assertEqual(rec.events, [])

    def test_dry_run_no_reporta_eventos(self):
        from clipagent.vod_fallback import run
        rec = _RecordingBridge()
        with mock.patch("clipagent.streamers.load_streamers",
                        return_value=_STREAMERS_ES):
            n, _ = run(
                state_path=self.state, out_root=self.out, dry_run=True,
                bridge=rec, scan_fn=self._scan(),
                latest_vod_fn=lambda entry: _VOD)
        self.assertEqual(n, 0)
        self.assertEqual(rec.events, [])



class TestVodPrecheck(unittest.TestCase):
    """Pre-chequeo de VOD (2026-11-19): un VOD que claramente no
    es GTA VI se salta sin gastar cupo ni enfriamiento; ante la
    duda se procesa (fail open)."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.state = os.path.join(self.tmp.name, "state.json")
        self.out = os.path.join(self.tmp.name, "out")

    def _scan(self, live=None):
        return lambda only=None: {"live": live or [], "skipped": [],
                                  "checked": 1,
                                  "twitch_mode": "helix"}

    def test_listones_nuevos_45_80(self):
        from clipagent.vod_fallback import MIN_SEO, MIN_VIRALITY
        self.assertEqual(MIN_VIRALITY, 45)
        self.assertEqual(MIN_SEO, 80)

    def test_clip_47_89_ahora_pasa(self):
        # El caso real que motivó la bajada (IlloJuan, viral 54,7 /
        # SEO 89): con 45/80 pasa; con 50/90 moría.
        from clipagent.vod_fallback import clip_passes_gates
        clip = _manifest_clip(
            viral_score={"total": 54.7, "chat_multiple": 1.9},
            seo_scores=_seo(94, "tiktok", 89))
        ok, reasons = clip_passes_gates(clip, "2932866051")
        self.assertTrue(ok, reasons)

    def test_gta_iv_se_salta(self):
        from clipagent.vod_fallback import vod_clearly_not_gta6
        vod = {"vod_id": "1", "title": "GTA IV RP con el chat",
               "category": "Grand Theft Auto IV"}
        reason = vod_clearly_not_gta6(vod)
        self.assertIsNotNone(reason)
        self.assertIn("Grand Theft Auto IV", reason)

    def test_slots_se_salta(self):
        from clipagent.vod_fallback import vod_clearly_not_gta6
        vod = {"vod_id": "1", "title": "APUESTAS LOCAS $$",
               "category": "Slots"}
        self.assertIsNotNone(vod_clearly_not_gta6(vod))

    def test_just_chatting_con_titulo_gta_se_procesa(self):
        # La categoría Helix es la del ÚLTIMO tramo (Rubius
        # 2932415021 termina en Just Chatting tras horas de GTA
        # VI): el título manda a procesar.
        from clipagent.vod_fallback import vod_clearly_not_gta6
        vod = {"vod_id": "2932415021", "title": "GTA 6 con Rubius",
               "category": "Just Chatting"}
        self.assertIsNone(vod_clearly_not_gta6(vod))

    def test_sin_categoria_se_procesa(self):
        from clipagent.vod_fallback import vod_clearly_not_gta6
        vod = {"vod_id": "1", "title": "directo de anoche",
               "category": ""}
        self.assertIsNone(vod_clearly_not_gta6(vod))

    def test_categoria_gta_vi_se_procesa(self):
        from clipagent.vod_fallback import vod_clearly_not_gta6
        vod = {"vod_id": "1", "title": "directo de anoche",
               "category": "Grand Theft Auto VI"}
        self.assertIsNone(vod_clearly_not_gta6(vod))

    def test_titulo_vacio_con_categoria_no_gta_se_salta(self):
        from clipagent.vod_fallback import vod_clearly_not_gta6
        vod = {"vod_id": "1", "title": "",
               "category": "Slots"}
        self.assertIsNotNone(vod_clearly_not_gta6(vod))

    def test_vod_no_gta_no_gasta_cupo_ni_enfriamiento(self):
        # Con max_vods=1: el VOD de Rubius (GTA IV) se salta sin
        # gastar el cupo y el de IlloJuan (GTA VI) se procesa; el
        # saltado NO queda marcado como intentado en el estado.
        from clipagent.vod_fallback import load_state, run
        gta4 = {"vod_id": "111", "url": "https://x/111",
                "title": "GTA IV RP", "category": "Grand Theft Auto IV",
                "live": False}
        gtavi = {"vod_id": "222", "url": "https://x/222",
                 "title": "GTA 6", "category": "Grand Theft Auto VI",
                 "live": False}
        vods = {"rubius": gta4, "illojuan": gtavi}
        rec = _RecordingBridge()
        seen = {}

        def _enqueue(bridge, streamer, vod_id, out_dir, clip,
                     lang="es"):
            seen["streamer"] = streamer
            return 101, ""

        with mock.patch("clipagent.streamers.load_streamers",
                        return_value=_STREAMERS_ES):
            n, _ = run(
                max_vods=1, max_clips=2, top_n=2,
                state_path=self.state, out_root=self.out,
                bridge=rec, scan_fn=self._scan(),
                latest_vod_fn=lambda entry: vods.get(entry["name"]),
                auto_clips_fn=lambda url, out_dir, top_n=2,
                lang="es": {"clips": [_manifest_clip(
                    viral_score={"total": 53.8,
                                 "chat_multiple": 1.9})]},
                enqueue_fn=_enqueue)
        self.assertEqual(n, 1)
        self.assertEqual(seen["streamer"], "illojuan")
        st = load_state(self.state)
        self.assertNotIn("111", st["vods"])
        self.assertIn("222", st["vods"])
        self.assertEqual(len(rec.events), 1)
        self.assertEqual(rec.events[0][0], "clip_encolado")



if __name__ == "__main__":
    unittest.main()


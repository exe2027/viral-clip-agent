#!/usr/bin/env python3
"""Pruebas de la revisión automática de los mejores picos (2026-11-18).

Regla del usuario (APROBADA 2026-11-18, LISTONES BAJADOS
2026-11-19 por su orden directa): picos con viralidad auto >= 45
van directos a render; top 3 por directo con auto 40-44,9 ->
revisión con fotogramas + transcripción (Gemini), se re-calcula
la viralidad; revisado >= 45 y SEO >= 80 -> render + LIVE; si no,
descarte sin render.

Gemini/Whisper se simulan: sin red. El caso con datos reales de
p3220 usa su chat (si existe) solo para el `detail`.
"""
import json
import os
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from clipagent import review
from clipagent.review import (REVIEW_TOP_N, decide_render,
                              review_human_scores, review_peak)

HERE = os.path.dirname(os.path.abspath(__file__))
P3220_CHAT = os.path.join(
    HERE, "runs", "2932415021-p3220", "chat_w3100-3340.json")

GEMINI_ALTO = json.dumps({
    "hook": 8.0, "claridad": 8.0, "novedad": 7.0, "loop": 8.0,
    "packaging": 8.0,
    "razones": {k: "muy bueno" for k in
                ("hook", "claridad", "novedad", "loop", "packaging")}})
GEMINI_BAJO = json.dumps({
    "hook": 4.0, "claridad": 4.0, "novedad": 4.0, "loop": 4.0,
    "packaging": 4.0,
    "razones": {k: "flojo" for k in
                ("hook", "claridad", "novedad", "loop", "packaging")}})
# 4,5 en todo: viral revisado ~46,5 (entre 45 y 50): el rango que
# antes rescataban los gritos y que ahora pasa directo por la
# puerta 45 (2026-11-19: el rescate queda subsumido).
GEMINI_MEDIO = json.dumps({
    "hook": 4.5, "claridad": 4.5, "novedad": 4.5, "loop": 4.5,
    "packaging": 4.5,
    "razones": {k: "medio" for k in
                ("hook", "claridad", "novedad", "loop", "packaging")}})


def p3220_detail():
    from clipagent.ingest import score_windows
    _scores, detail = score_windows(P3220_CHAT, lang="es")
    return detail[3220]


class TestDecideRender(unittest.TestCase):
    def test_directo_45_o_mas(self):
        # 2026-11-19: el listón bajó de 50 a 45; los picos auto
        # 48-49 ya no pasan por la revisión que los tumbaba a ~35.
        self.assertEqual(decide_render(45.0, 0)[0], "directo")
        self.assertEqual(decide_render(48.5, 0)[0], "directo")
        self.assertEqual(decide_render(50.0, 0)[0], "directo")
        self.assertEqual(decide_render(53.8, 3)[0], "directo")

    def test_revision_40_a_44(self):
        self.assertEqual(decide_render(44.9, 2)[0], "revision")
        self.assertEqual(decide_render(40.0, 0)[0], "revision")

    def test_descartar_menos_40(self):
        self.assertEqual(decide_render(39.9, 0)[0], "descartar")
        self.assertEqual(decide_render(30.0, 0)[0], "descartar")

    def test_top3_el_cuarto_se_descarta(self):
        self.assertEqual(decide_render(44.0, REVIEW_TOP_N)[0],
                         "descartar")
        self.assertEqual(decide_render(44.0, REVIEW_TOP_N - 1)[0],
                         "revision")


class TestParseHumanScores(unittest.TestCase):
    def test_json_valido(self):
        human, _ = review._parse_human_scores(GEMINI_ALTO)
        self.assertEqual(human["hook"], 8.0)
        self.assertEqual(len(human), 5)

    def test_con_fences_markdown(self):
        human, _ = review._parse_human_scores(
            "```json\n" + GEMINI_ALTO + "\n```")
        self.assertEqual(human["loop"], 8.0)

    def test_falta_componente(self):
        with self.assertRaises(ValueError):
            review._parse_human_scores('{"hook": 8}')

    def test_fuera_de_rango_se_limita(self):
        human, _ = review._parse_human_scores(json.dumps({
            "hook": 99, "claridad": -3, "novedad": 5, "loop": 5,
            "packaging": 5, "razones": {}}))
        self.assertEqual(human["hook"], 10.0)
        self.assertEqual(human["claridad"], 0.0)


class TestReviewPeak(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.window = os.path.join(self.tmp.name, "window.mp4")
        open(self.window, "wb").close()
        self.frames = []
        for i in range(3):
            p = os.path.join(self.tmp.name, f"f{i}.jpg")
            open(p, "wb").close()
            self.frames.append({"path": p, "role": "payoff",
                                "ts_clip": 20.0})
        self.transcript = {"segments": [
            {"text": "no me creo que estemos aquí",
             "words": [{"word": "no"}, {"word": "me"},
                       {"word": "creo"}]}],
            "language": "es"}
        self.chat = os.path.join(self.tmp.name, "chat.json")
        with open(self.chat, "w", encoding="utf-8") as f:
            json.dump({"comments": [
                {"content_offset_seconds": 3220,
                 "message": {"body": "cinema"},
                 "commenter": {"name": "u1"}}]}, f)

    def tearDown(self):
        self.tmp.cleanup()

    def _patch_all(self, gemini_text, seo=None, audio_extra=None):
        seo = seo if seo is not None else {
            "youtube_shorts": {"score": 92},
            "tiktok": {"score": 94},
            "instagram_reels": {"score": 94},
            "facebook_reels": {"score": 94}}
        pkg = {"best_title": 'GTA 6: El chat: "Cinema"'}
        audio = {"stats": {"std_db": 16.0, "steady_frac": 0.2},
                 "words": 40}
        audio.update(audio_extra or {})
        patches = [
            mock.patch("clipagent.workers.pre_corte_package",
                       return_value=(pkg, seo)),
            mock.patch("clipagent.thumbnails.extract_thumbnail_frames",
                       return_value=self.frames),
            mock.patch("clipagent.transcribe.transcribe_audio",
                       return_value=(self.transcript, {})),
            mock.patch("clipagent.audio.audio_verdict",
                       return_value=audio),
            mock.patch("clipagent.vision._call_gemini",
                       return_value=gemini_text),
            mock.patch("clipagent.vision.get_api_key",
                       return_value="fake-key"),
        ]
        return patches

    def _run(self, patches, detail, multiple=1.09):
        for p in patches:
            p.start()
        self.addCleanup(lambda: [p.stop() for p in patches])
        return review_peak(self.window, detail, multiple, self.chat,
                           3220, 14.3, game="GTA 6", lang="es",
                           streamer="rubius")

    def test_revision_pasa_con_gemini_alto(self):
        if not os.path.isfile(P3220_CHAT):
            self.skipTest("sin datos reales de p3220")
        r = self._run(self._patch_all(GEMINI_ALTO), p3220_detail())
        self.assertTrue(r["pasa"], r["motivo"])
        self.assertEqual(r["revision"], "gemini")
        self.assertGreaterEqual(r["viral_revisado"], 50.0)
        self.assertGreater(r["viral_revisado"], r["viral_auto"])
        self.assertEqual(r["human"]["hook"], 8.0)

    def test_revision_falla_con_gemini_bajo(self):
        if not os.path.isfile(P3220_CHAT):
            self.skipTest("sin datos reales de p3220")
        r = self._run(self._patch_all(GEMINI_BAJO), p3220_detail())
        self.assertFalse(r["pasa"], r["motivo"])
        self.assertLess(r["viral_revisado"], 50.0)
        self.assertEqual(r["revision"], "gemini")

    def test_revision_guarda_paquete_y_seo_detail(self):
        # 2026-11-19, calibración: la revisión persiste el paquete
        # pre-corte y el desglose SEO completo (el paso 7 del
        # cutter reutiliza el paquete; el embudo queda auditable
        # sin reconstruir nada).
        if not os.path.isfile(P3220_CHAT):
            self.skipTest("sin datos reales de p3220")
        r = self._run(self._patch_all(GEMINI_ALTO), p3220_detail())
        self.assertEqual(r["package"]["best_title"],
                         'GTA 6: El chat: "Cinema"')
        self.assertEqual(r["seo_detail"]["youtube_shorts"]["score"], 92)
        self.assertEqual(r["seo"]["youtube_shorts"], 92)

    def test_revision_falla_por_seo_bajo(self):
        # 2026-11-19: la puerta SEO bajó de 90 a 80; el fixture
        # viejo (85/88) ya pasa, así que el fallo se prueba con
        # un SEO por debajo de 80.
        if not os.path.isfile(P3220_CHAT):
            self.skipTest("sin datos reales de p3220")
        r = self._run(self._patch_all(GEMINI_ALTO, seo={
            "youtube_shorts": {"score": 75},
            "tiktok": {"score": 78}}), p3220_detail())
        self.assertFalse(r["pasa"], r["motivo"])
        self.assertGreaterEqual(r["viral_revisado"], 45.0)

    def test_revision_medio_pasa_directo_por_puerta_45(self):
        """2026-11-19: viral revisado 45-49,9 + SEO >= 80 -> pasa
        por la puerta principal (el rescate por gritos queda
        subsumido: ya no hace falta gritar para pasar)."""
        if not os.path.isfile(P3220_CHAT):
            self.skipTest("sin datos reales de p3220")
        detail = p3220_detail()
        r = self._run(
            self._patch_all(GEMINI_MEDIO, audio_extra={
                "streamer_shout_count": 2,
                "streamer_shout_seconds": [18, 19]}),
            detail)
        self.assertGreaterEqual(r["viral_revisado"], 45.0)
        self.assertLess(r["viral_revisado"], 50.0)
        self.assertTrue(r["pasa"], r["motivo"])
        self.assertFalse(r["streamer_shout_rescue"])

    def test_revision_bajo_falla_aunque_grite(self):
        """Por debajo de 45 no hay rescate: ni con 2 s de grito
        pasa un revisado < 45."""
        if not os.path.isfile(P3220_CHAT):
            self.skipTest("sin datos reales de p3220")
        detail = p3220_detail()
        r = self._run(
            self._patch_all(GEMINI_BAJO, audio_extra={
                "streamer_shout_count": 2,
                "streamer_shout_seconds": [18, 19]}),
            detail)
        self.assertLess(r["viral_revisado"], 45.0)
        self.assertFalse(r["pasa"], r["motivo"])
        self.assertFalse(r["streamer_shout_rescue"])

    def test_revision_bajo_falla_sin_gritos(self):
        """Sin gritos y por debajo de 45 tampoco pasa."""
        if not os.path.isfile(P3220_CHAT):
            self.skipTest("sin datos reales de p3220")
        detail = p3220_detail()
        r = self._run(
            self._patch_all(GEMINI_BAJO, audio_extra={
                "streamer_shout_count": 0,
                "streamer_shout_seconds": []}),
            detail)
        self.assertLess(r["viral_revisado"], 45.0)
        self.assertFalse(r["pasa"], r["motivo"])
        self.assertFalse(r["streamer_shout_rescue"])

    def test_gemini_caido_fallback_pre_corte(self):
        """Sin juez, fallback al umbral pre-corte 45 (no se tumba
        la revisión ni el loop): p3220 auto 46,3 pasa, 42 no."""
        if not os.path.isfile(P3220_CHAT):
            self.skipTest("sin datos reales de p3220")
        detail = p3220_detail()
        patches = self._patch_all(GEMINI_ALTO)
        patches[-2] = mock.patch(
            "clipagent.vision._call_gemini",
            side_effect=RuntimeError("Gemini caído"))
        r = self._run(patches, detail)
        self.assertEqual(r["revision"], "fallback-pre-corte")
        self.assertTrue(r["pasa"], r["motivo"])  # auto 46,3 >= 45
        # Chat plano (1 msg/30 s, sin ráfaga): auto ~44 < 45.
        debil = {"msgs": 2, "risas": 0, "hype": 0, "users": 2,
                 "msgs_per_10s": 0, "msgs_per_60s": 2,
                 "hype_score": 0.0, "hype_ratio": 1.0,
                 "burst_score": 0.0, "emote_ratio": 0.0,
                 "rep_ratio": 0.0, "lex_diversity": 1.0}
        patches2 = self._patch_all(GEMINI_ALTO)
        patches2[-2] = mock.patch(
            "clipagent.vision._call_gemini",
            side_effect=RuntimeError("Gemini caído"))
        r2 = self._run(patches2, debil, multiple=1.0)
        self.assertEqual(r2["revision"], "fallback-pre-corte")
        self.assertLess(r2["viral_auto"], 45.0)
        self.assertFalse(r2["pasa"])

    def test_sin_api_key_fallback(self):
        if not os.path.isfile(P3220_CHAT):
            self.skipTest("sin datos reales de p3220")
        patches = self._patch_all(GEMINI_ALTO)
        patches[-1] = mock.patch("clipagent.vision.get_api_key",
                                 return_value=None)
        r = self._run(patches, p3220_detail())
        self.assertEqual(r["revision"], "fallback-pre-corte")


class TestReviewHumanScoresLang(unittest.TestCase):
    """El juez usa el prompt del idioma del canal (2026-11-19,
    calibración: antes el prompt era siempre español, también para
    clips en inglés)."""

    def _run(self, lang):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        frame = os.path.join(tmp.name, "f0.jpg")
        open(frame, "wb").close()
        seen = {}
        fake = mock.Mock(return_value=json.dumps({
            "hook": 7.0, "claridad": 7.0, "novedad": 7.0, "loop": 7.0,
            "packaging": 7.0,
            "razones": {k: "ok" for k in
                        ("hook", "claridad", "novedad", "loop",
                         "packaging")}}))
        def capture(parts, **kwargs):
            seen["prompt"] = parts[0]["text"]
            return fake(parts, **kwargs)
        with mock.patch("clipagent.vision.get_api_key",
                        return_value="fake-key"), \
                mock.patch("clipagent.vision._call_gemini",
                           side_effect=capture):
            human, _reasons = review_human_scores(
                [{"path": frame, "role": "payoff", "ts_clip": 20.0}],
                "hello chat", "GTA 6: test", lang=lang)
        self.assertEqual(human["hook"], 7.0)
        return seen["prompt"]

    def test_prompt_en_para_clips_en_ingles(self):
        prompt = self._run("en")
        self.assertIn("virality judge", prompt)
        self.assertNotIn("Eres el juez", prompt)

    def test_prompt_es_para_clips_en_espanol(self):
        prompt = self._run("es")
        self.assertIn("Eres el juez", prompt)
        self.assertNotIn("virality judge", prompt)


class TestProcessPeakReview(unittest.TestCase):
    """El cableado en el loop en vivo: qué picos llegan a revisión
    y cuáles a render. Recorder/corte/subtítulos simulados."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.out = os.path.join(self.tmp.name, "clips")
        os.makedirs(self.out, exist_ok=True)

    def tearDown(self):
        self.tmp.cleanup()

    def _session(self, viral_auto):
        from clipagent.live_cutter import LiveCutterSession

        class Gate:
            def check(self, ts, chat_path=None):
                return True, "Grand Theft Auto VI", "helix"

        class Recorder:
            def wait_until(self, rel_ts, timeout=120):
                return True

            def extract_window(self, start, duration, out_path):
                open(out_path, "wb").close()
                return None, 0.0

        session = LiveCutterSession(
            "rubius", platform="twitch", out_dir=self.out,
            recorder=Recorder(), gate=Gate(), dry_run=True, cfg={})
        return session

    def _peak(self, ts=3220):
        detail = {"msgs": 100, "risas": 7, "hype": 4, "users": 50,
                  "msgs_per_10s": 134, "msgs_per_60s": 638,
                  "hype_score": 5.9, "hype_ratio": 1.31,
                  "burst_score": 5.9}
        return (float(ts), 14.3, 1.09, detail)

    def _patch_process(self, viral_auto, review_result):
        pkg_path = os.path.join(self.out, "pkg.json")
        patches = [
            mock.patch("clipagent.detect.score_clip",
                       return_value={"total": viral_auto}),
            mock.patch("clipagent.review.review_peak",
                       return_value=review_result),
            mock.patch("clipagent.workers.cut_clip",
                       return_value={"layout": {}}),
            mock.patch("clipagent.workers.subtitle_clip",
                       return_value={"publish_blocked": False}),
            mock.patch("clipagent.workers.package_clip_step",
                       return_value={"package_path": pkg_path}),
            mock.patch("clipagent.live_cutter.finalize_package",
                       return_value={"seo": {"youtube_shorts": 92},
                                     "priority": {}}),
            mock.patch("clipagent.live_cutter.build_title",
                       return_value="Título test"),
            mock.patch("clipagent.priority.publish_status",
                       return_value=("publicable", "ok")),
            # La puerta final se mockea como aprobada: estos tests
            # cubren el flujo revisión -> paquete, no el gate (el
            # gate sobre vídeos reales se cubre en
            # test_final_gate.py; con el gate real el vídeo falso
            # fallaría y dispararía un re-corte).
            mock.patch("clipagent.final_review.validate_final_clip",
                       return_value={
                           "decision": "aprobado", "pasa": True,
                           "motivo": "", "fallos": [], "resumen": "",
                           "scores": {}, "frames_n": 3,
                           "hard_pasa": True, "needs_rerender": False,
                           "rerender_reason": "", "hard_failures": [],
                           "observe_only": True,
                           "face": {"pasa": True},
                           "template": {"pasa": True},
                           "gemini": {"decision": "aprobado"}}),
        ]
        for p in patches:
            p.start()
        self.addCleanup(lambda: [p.stop() for p in patches])
        return patches

    def test_pico_44_revision_pasa_renderiza(self):
        review_ok = {"pasa": True, "motivo": "revisión ok",
                     "viral_revisado": 53.8, "human": {"hook": 8},
                     "audio": {"stats": {}, "words": 40},
                     "revision": "gemini", "seo": {}}
        self._patch_process(44.5, review_ok)
        session = self._session(44.5)
        with mock.patch("clipagent.workers.cut_clip",
                        return_value={"layout": {}}) as cut:
            record = session.process_peak(self._peak())
        self.assertIsNotNone(record)
        self.assertEqual(record["viral_auto"], 44.5)
        self.assertEqual(record["review"]["viral_revisado"], 53.8)
        self.assertEqual(len(session._reviewed_peaks), 1)
        cut.assert_called_once()

    def test_paso7_usa_el_paquete_de_la_revision(self):
        # 2026-11-19, calibración: el SEO post-corte se mide sobre
        # el paquete Gemini de la revisión (título + descripción +
        # tags + miniatura). 2026-11-19 (título Gemini): el paquete
        # se construye ANTES del quemado, así que la franja lleva
        # el título Gemini y no el de reglas: quemado y publicación
        # usan el MISMO título.
        review_ok = {"pasa": True, "motivo": "revisión ok",
                     "viral_revisado": 53.8, "human": {"hook": 8},
                     "audio": {"stats": {}, "words": 40},
                     "revision": "gemini", "seo": {},
                     "package": {
                         "best_title": 'GTA 6: El chat: "Cinema"',
                         "description": "GTA 6: el chat explotó.",
                         "tags": ["gta 6", "cinema"],
                         "hashtags": ["#GTA6"],
                         "thumbnail_text": "CINEMA",
                         "pkg_source": "gemini"}}
        self._patch_process(44.5, review_ok)
        session = self._session(44.5)
        record = session.process_peak(self._peak())
        self.assertIsNotNone(record)
        from clipagent import workers as _workers
        _args, kwargs = _workers.package_clip_step.call_args
        # El título viaja como 2º argumento posicional.
        self.assertEqual(_args[1], 'GTA 6: El chat: "Cinema"')
        self.assertEqual(kwargs["description"],
                         "GTA 6: el chat explotó.")
        self.assertEqual(kwargs["tags"], ["gta 6", "cinema"])
        self.assertEqual(kwargs["thumbnail_text"], "CINEMA")
        self.assertEqual(kwargs["lang"], "es")
        # El registro lleva el título de publicación, y el quemado
        # es EL MISMO (el paquete se construyó antes del quemado).
        self.assertEqual(record["title"], 'GTA 6: El chat: "Cinema"')
        self.assertEqual(record["burned_title"],
                         'GTA 6: El chat: "Cinema"')
        self.assertEqual(record["pkg_source"], "gemini")
        self.assertEqual(record["thumbnail_text"], "CINEMA")

    def test_paso7_directo_construye_paquete_con_visible(self):
        # Sin revisión (viral auto >= 50): el paso 7 construye el
        # mismo paquete Gemini que el pre-corte, con lo visible
        # real del layout (split -> cams+gameplay).
        pkg_full = {"best_title": "GTA 6: directo total",
                    "description_first_2_lines":
                        "GTA 6: jugada directa.",
                    "tags": ["gta 6"], "hashtags": ["#GTA6"],
                    "thumbnail_text": "DIRECTO", "pkg_source": "gemini"}
        self._patch_process(51.4, None)
        session = self._session(51.4)
        layout = {"mode": "split", "segments": [
            {"start": 0.0, "end": 40.0, "layout": "split"}]}
        with mock.patch("clipagent.workers.cut_clip",
                        return_value={"layout": layout}), \
                mock.patch("clipagent.workers.pre_corte_package",
                           return_value=(pkg_full, {})):
            record = session.process_peak(self._peak())
        self.assertIsNotNone(record)
        from clipagent import workers as _workers
        _args, kwargs = _workers.package_clip_step.call_args
        self.assertEqual(_args[1], "GTA 6: directo total")
        self.assertEqual(kwargs["visible"], "cams+gameplay")
        self.assertEqual(kwargs["lang"], "es")

    def test_revision_queda_en_el_log_del_job(self):
        # Antes la revisión solo vivía en live_cuts.json: en la
        # salida de la corrida no se veía si un pico pasó por
        # revisión ni con qué resultado.
        import io
        from contextlib import redirect_stdout
        review_ok = {"pasa": True, "motivo": "revisión ok",
                     "viral_revisado": 53.8, "human": {"hook": 8},
                     "audio": {"stats": {}, "words": 40},
                     "revision": "gemini", "seo": {}}
        self._patch_process(44.5, review_ok)
        session = self._session(44.5)
        buf = io.StringIO()
        with redirect_stdout(buf):
            record = session.process_peak(self._peak())
        self.assertIsNotNone(record)
        out = buf.getvalue()
        self.assertIn("REVISIÓN pico 3220s", out)
        self.assertIn("gemini", out)
        self.assertIn("44.5 -> revisado 53.8", out)
        self.assertIn("PASA", out)

    def test_pico_44_revision_falla_no_renderiza(self):
        review_no = {"pasa": False, "motivo": "viral 46 < 50",
                     "viral_revisado": 46.0, "human": {"hook": 4},
                     "audio": None, "revision": "gemini", "seo": {}}
        self._patch_process(44.5, review_no)
        session = self._session(44.5)
        with mock.patch("clipagent.workers.cut_clip",
                        return_value={"layout": {}}) as cut:
            record = session.process_peak(self._peak())
        self.assertIsNone(record)
        cut.assert_not_called()
        self.assertIn("revisión", session.skipped[-1]["reason"])
        self.assertEqual(len(session._reviewed_peaks), 1)

    def test_pico_30_ni_revision_ni_render(self):
        self._patch_process(30.0, {"pasa": True})
        session = self._session(30.0)
        with mock.patch("clipagent.review.review_peak") as rv, \
                mock.patch("clipagent.workers.cut_clip",
                           return_value={"layout": {}}) as cut:
            record = session.process_peak(self._peak())
        self.assertIsNone(record)
        rv.assert_not_called()
        cut.assert_not_called()

    def test_cuarto_pico_borderline_fuera_del_top3(self):
        review_ok = {"pasa": True, "motivo": "ok",
                     "viral_revisado": 53.8, "human": {"hook": 8},
                     "audio": None, "revision": "gemini", "seo": {}}
        self._patch_process(44.5, review_ok)
        session = self._session(44.5)
        session._reviewed_peaks = [{"peak_ts": 1}, {"peak_ts": 2},
                                   {"peak_ts": 3}]
        with mock.patch("clipagent.review.review_peak") as rv, \
                mock.patch("clipagent.workers.cut_clip",
                           return_value={"layout": {}}) as cut:
            record = session.process_peak(self._peak())
        self.assertIsNone(record)
        rv.assert_not_called()
        cut.assert_not_called()
        self.assertIn("top 3", session.skipped[-1]["reason"])


if __name__ == "__main__":
    unittest.main()

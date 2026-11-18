#!/usr/bin/env python3
"""Pruebas de la revisión automática de los mejores picos (2026-11-18).

Regla del usuario (APROBADA): top 3 picos por directo con viralidad
auto 40-49,9 -> revisión con fotogramas + transcripción (Gemini),
se re-calcula la viralidad; >= 50 y SEO >= 90 -> render + LIVE;
si no, descarte sin render. Puerta de publicación sigue en 50.

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
                              review_peak)

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
# 4,5 en todo: viral revisado ~46,5 (entre 45 y 50): el rango del
# rescate por gritos del streamer.
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
    def test_directo_50_o_mas(self):
        self.assertEqual(decide_render(50.0, 0)[0], "directo")
        self.assertEqual(decide_render(53.8, 3)[0], "directo")

    def test_revision_40_a_49(self):
        self.assertEqual(decide_render(46.3, 0)[0], "revision")
        self.assertEqual(decide_render(40.0, 0)[0], "revision")
        self.assertEqual(decide_render(49.9, 2)[0], "revision")

    def test_descartar_menos_40(self):
        self.assertEqual(decide_render(39.9, 0)[0], "descartar")
        self.assertEqual(decide_render(30.0, 0)[0], "descartar")

    def test_top3_el_cuarto_se_descarta(self):
        self.assertEqual(decide_render(46.3, REVIEW_TOP_N)[0],
                         "descartar")
        self.assertEqual(decide_render(46.3, REVIEW_TOP_N - 1)[0],
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

    def test_revision_falla_por_seo_bajo(self):
        if not os.path.isfile(P3220_CHAT):
            self.skipTest("sin datos reales de p3220")
        r = self._run(self._patch_all(GEMINI_ALTO, seo={
            "youtube_shorts": {"score": 85},
            "tiktok": {"score": 88}}), p3220_detail())
        self.assertFalse(r["pasa"], r["motivo"])
        self.assertGreaterEqual(r["viral_revisado"], 50.0)

    def test_revision_rescate_por_gritos_del_streamer(self):
        """Rescate (2026-11-18): viral revisado 45-49,9 + 2+ s de
        grito DEL STREAMER + SEO >= 90 -> pasa como punto aparte."""
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
        self.assertTrue(r["streamer_shout_rescue"])
        self.assertIn("gritos", r["motivo"])

    def test_revision_sin_rescate_con_un_solo_grito(self):
        """Con 1 solo segundo de grito no hay rescate."""
        if not os.path.isfile(P3220_CHAT):
            self.skipTest("sin datos reales de p3220")
        detail = p3220_detail()
        r = self._run(
            self._patch_all(GEMINI_MEDIO, audio_extra={
                "streamer_shout_count": 1,
                "streamer_shout_seconds": [18]}),
            detail)
        self.assertLess(r["viral_revisado"], 50.0)
        self.assertFalse(r["pasa"], r["motivo"])
        self.assertFalse(r["streamer_shout_rescue"])

    def test_revision_sin_rescate_sin_gritos(self):
        """Sin gritos del streamer no hay rescate aunque esté en
        45-49,9."""
        if not os.path.isfile(P3220_CHAT):
            self.skipTest("sin datos reales de p3220")
        detail = p3220_detail()
        r = self._run(
            self._patch_all(GEMINI_MEDIO, audio_extra={
                "streamer_shout_count": 0,
                "streamer_shout_seconds": []}),
            detail)
        self.assertLess(r["viral_revisado"], 50.0)
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

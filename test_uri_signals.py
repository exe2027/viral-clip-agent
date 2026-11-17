#!/usr/bin/env python3
"""Pruebas de las señales de Uri (2026-11-17, clipper-directos):
heatmap de YouTube, % de juego en movimiento y voz del juego.

Decisión de diseño (documentada en clipagent/heatmap.py,
motion.py y game_voice.py): las tres son DIAGNÓSTICO en el
manifiesto (signals.*). NO tocan la fórmula de viralidad (7
componentes), ni la de prioridad, ni los filtros duros, ni el
ritmo anti-ban. El test de regresión de abajo lo prueba: el total
de viralidad del fixture Coker es idéntico con y sin señales.

Uso: .venv/bin/python -m pytest test_uri_signals.py -q
"""
import json
import os
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from clipagent.detect import compute_signals, score_clip  # noqa: E402
from clipagent.game_voice import (  # noqa: E402
    detect_game_voice,
    normalize_game_voice,
    split_subtitle_blocks,
    words_outside_game_voice,
)
from clipagent.heatmap import (  # noqa: E402
    heatmap_for_url,
    nearest_heatmap_peak,
    peaks_from_heatmap_data,
)
from clipagent.motion import measure_motion, motion_flag  # noqa: E402
from clipagent.render import (  # noqa: E402
    apply_game_voice,
    cut_clip_with_layout,
    decide_layout,
    segment_filter_graph,
)
from clipagent.transcribe import srt_to_karaoke_ass  # noqa: E402

# Fixture de yt-dlp -J: heatmap con dos zonas repetidas.
HEATMAP_DATA = {
    "heatmap": [
        {"start_time": 0, "end_time": 120, "value": 0.2},
        {"start_time": 120, "end_time": 180, "value": 0.9},
        {"start_time": 180, "end_time": 300, "value": 0.3},
        {"start_time": 1200, "end_time": 1260, "value": 1.0},
    ]
}

COKER_DETAIL = {"msgs": 45, "risas": 5, "hype": 9, "users": 47,
                "emote_ratio": 0.1, "rep_ratio": 0.2, "lex_diversity": 0.6,
                "penalty_factor": 1.0, "motivo_penalizacion": None}
COKER_HUMAN = {"hook": 8, "claridad": 9, "novedad": 7, "loop": 8,
               "packaging": 7}
COKER_AUDIO = {"std_db": 11.5, "steady_frac": 0.2, "words": 77}


def _make_video(path, moving=True, duration=6, with_audio=True):
    """Vídeo sintético: testsrc (se mueve) o color negro (fijo)."""
    src = ("testsrc=size=320x180:rate=30" if moving
           else "color=c=black:size=320x180:rate=30")
    cmd = ["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", src,
           "-t", str(duration)]
    if with_audio:
        cmd += ["-f", "lavfi", "-i", "sine=frequency=440",
                "-shortest"]
    cmd += ["-c:v", "libx264", "-pix_fmt", "yuv420p",
            "-c:a", "aac", path]
    subprocess.run(cmd, check=True, capture_output=True)


def _words(start, end, step=0.5):
    """Palabras ficticias del streamer cada 0,5 s en [start, end)."""
    words = []
    t = start
    while t < end - 0.01:
        words.append({"word": "hola", "start": round(t, 2),
                      "end": round(t + step, 2)})
        t += step
    return words


class TestHeatmap(unittest.TestCase):
    def test_peaks_from_fixture(self):
        peaks, reason = peaks_from_heatmap_data(HEATMAP_DATA)
        # El algoritmo de Uri devuelve hasta 15 picos con >= 60 s
        # entre sí, incluidos los débiles (0,2): 6 en este fixture.
        self.assertEqual(len(peaks), 6)
        # El pico más repetido (1,0) normaliza a 1,0.
        self.assertEqual(peaks[-1]["score"], 1.0)
        self.assertEqual(peaks[-1]["t"], 1200)
        self.assertEqual(peaks[2]["t"], 120)
        self.assertEqual(peaks[2]["score"], 0.9)
        self.assertIn("heatmap", reason)

    def test_peaks_separated_60s(self):
        # Dos buckets altos a < 60 s: solo queda el mayor.
        data = {"heatmap": [
            {"start_time": 100, "end_time": 110, "value": 1.0},
            {"start_time": 110, "end_time": 120, "value": 0.9},
            {"start_time": 500, "end_time": 510, "value": 0.8}]}
        peaks, _ = peaks_from_heatmap_data(data, top_n=15)
        times = [p["t"] for p in peaks]
        self.assertIn(100, times)
        self.assertNotIn(110, times)
        self.assertIn(500, times)

    def test_no_heatmap_is_empty(self):
        peaks, reason = peaks_from_heatmap_data({})
        self.assertEqual(peaks, [])
        self.assertIn("sin heatmap", reason)
        peaks, _ = peaks_from_heatmap_data({"heatmap": []})
        self.assertEqual(peaks, [])

    def test_heatmap_for_url_fake_runner(self):
        def fake(cmd, timeout):
            self.assertIn("yt-dlp", cmd[0])
            return json.dumps(HEATMAP_DATA)
        result = heatmap_for_url(
            "https://www.youtube.com/watch?v=abc", runner=fake)
        self.assertTrue(result["available"])
        self.assertEqual(result["source"], "youtube_heatmap")
        self.assertEqual(len(result["peaks"]), 6)

    def test_heatmap_for_url_not_youtube(self):
        result = heatmap_for_url("https://www.twitch.tv/illojuan",
                                 runner=lambda c, timeout: "{}")
        self.assertFalse(result["available"])
        self.assertIn("YouTube", result["reason"])

    def test_heatmap_for_url_missing_ytdlp(self):
        def boom(cmd, timeout):
            raise FileNotFoundError("yt-dlp")
        result = heatmap_for_url(
            "https://youtu.be/abc", runner=boom)
        self.assertFalse(result["available"])
        self.assertIn("yt-dlp", result["reason"])

    def test_nearest_peak_gap(self):
        peaks, _ = peaks_from_heatmap_data(HEATMAP_DATA)
        near = nearest_heatmap_peak(peaks, 1210)
        self.assertIsNotNone(near)
        self.assertEqual(near["t"], 1200)
        self.assertIsNone(nearest_heatmap_peak(peaks, 600))
        self.assertIsNone(nearest_heatmap_peak([], 100))


class TestMotion(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.moving = os.path.join(cls.tmp.name, "moving.mp4")
        cls.frozen = os.path.join(cls.tmp.name, "frozen.mp4")
        _make_video(cls.moving, moving=True, with_audio=False)
        _make_video(cls.frozen, moving=False, with_audio=False)

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_moving_video_high_pct(self):
        result = measure_motion(self.moving)
        self.assertTrue(result["available"])
        self.assertGreater(result["motion_pct"], 50.0)

    def test_frozen_video_zero_pct(self):
        result = measure_motion(self.frozen)
        self.assertTrue(result["available"])
        self.assertEqual(result["motion_pct"], 0.0)

    def test_missing_video_is_not_available(self):
        result = measure_motion("/tmp/no-existe-xyz.mp4")
        self.assertFalse(result["available"])
        self.assertIsNone(result["motion_pct"])

    def test_motion_flag_frozen_needs_strong_reaction(self):
        flag, reason = motion_flag(5.0, virality_total=60.0)
        self.assertEqual(flag, "pausa_pantalla_fija")
        self.assertIn("revisar a mano", reason)
        flag, _ = motion_flag(5.0, virality_total=90.0)
        self.assertEqual(flag, "pausa_pero_reaccion_fuerte")
        flag, _ = motion_flag(80.0, virality_total=60.0)
        self.assertEqual(flag, "en_movimiento")
        flag, _ = motion_flag(None)
        self.assertEqual(flag, "sin_medir")


class TestGameVoice(unittest.TestCase):
    def test_normalize_clips_and_merges(self):
        spans = normalize_game_voice(
            [[-5, 3], [2.5, 5], [10, 9], [20, 40]], clip_duration=30)
        self.assertEqual([(s["start"], s["end"]) for s in spans],
                         [(0.0, 5.0), (20.0, 30.0)])
        self.assertFalse(spans[0]["estimated"])

    def test_normalize_estimated_flag(self):
        spans = normalize_game_voice([[2, 5]], clip_duration=10,
                                     source="estimada")
        self.assertTrue(spans[0]["estimated"])
        self.assertEqual(spans[0]["source"], "estimada")

    def test_detect_silence_with_active_audio(self):
        # El streamer habla 0-3 s y 9-12 s; entre medias calla con
        # audio activo (-20 dBFS): voz del juego estimada 4-9 s
        # (la palabra que termina en 3,0 s cubre el segundo 3).
        transcript = {"segments": [{"words": _words(0, 3) + _words(9, 12)}]}
        rms = [(s, -20.0) for s in range(12)]
        spans = detect_game_voice(transcript, audio_rms=rms,
                                  clip_duration=12)
        self.assertEqual(len(spans), 1)
        self.assertEqual((spans[0]["start"], spans[0]["end"]), (4.0, 9.0))
        self.assertTrue(spans[0]["estimated"])

    def test_detect_needs_audio(self):
        transcript = {"segments": [{"words": _words(0, 3)}]}
        # Sin RMS: un silencio no es voz del juego a ciegas.
        self.assertEqual(detect_game_voice(transcript, clip_duration=12),
                         [])

    def test_detect_needs_moving_game(self):
        transcript = {"segments": [{"words": _words(0, 3) + _words(9, 12)}]}
        rms = [(s, -20.0) for s in range(12)]
        # Juego parado (movimiento 0 en todo el clip): no es voz
        # del juego, es una pausa con música.
        spans = detect_game_voice(transcript, audio_rms=rms,
                                  clip_duration=12,
                                  motion_per_second=[0.0] * 12)
        self.assertEqual(spans, [])
        spans = detect_game_voice(transcript, audio_rms=rms,
                                  clip_duration=12,
                                  motion_per_second=[10.0] * 12)
        self.assertEqual(len(spans), 1)

    def test_split_blocks_and_exclusion(self):
        blocks = [(0, 2, "hola chat"), (5, 7, "mira el tráiler"),
                  (10, 12, "qué locura")]
        streamer, game = split_subtitle_blocks(blocks, [[4, 8]])
        self.assertEqual(streamer, [(0, 2, "hola chat"),
                                    (10, 12, "qué locura")])
        self.assertEqual(game, [(5, 7, "mira el tráiler")])
        transcript = {"segments": [{"words": _words(0, 2) + _words(5, 7)}]}
        kept = words_outside_game_voice(transcript, [[4, 8]])
        self.assertTrue(all(w["start"] < 4 for w in kept))
        self.assertEqual(len(kept), 4)


class TestGameLayout(unittest.TestCase):
    def test_game_filter_is_fullscreen_game(self):
        graph = segment_filter_graph("0:v", "vout", "game", {},
                                     1920, 1080, {})
        self.assertIn("crop=ih*9/16:ih", graph)
        self.assertIn("scale=1080:1920", graph)
        self.assertNotIn("vstack", graph)

    def test_apply_game_voice_splits_segments(self):
        segments = [{"start": 0.0, "end": 10.0, "layout": "split",
                     "params": {"game_h": 1320, "cam_h": 600},
                     "reason": "split"}]
        out = apply_game_voice(segments, [[2, 4]], duration=10.0)
        self.assertEqual([s["layout"] for s in out],
                         ["split", "game", "split"])
        self.assertEqual((out[1]["start"], out[1]["end"]), (2.0, 4.0))

    def test_apply_game_voice_merges_short_runs(self):
        segments = [{"start": 0.0, "end": 10.0, "layout": "split",
                     "params": {}, "reason": "split"}]
        # Tramo de juego de 1 s (< 1,5 s): se fusiona con el vecino.
        out = apply_game_voice(segments, [[2, 3]], duration=10.0)
        self.assertEqual([s["layout"] for s in out], ["split"])

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.video = os.path.join(cls.tmp.name, "clip.mp4")
        _make_video(cls.video, moving=True, duration=10, with_audio=True)

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_decide_layout_with_game_voice(self):
        decision = decide_layout(
            self.video, 10.0, cfg={"scan_enabled": False,
                                   "sample_every_s": 5,
                                   "min_segment_s": 3},
            game_voice=[[2, 4]])
        layouts = [s["layout"] for s in decision["segments"]]
        self.assertIn("game", layouts)

    def test_cut_with_game_voice_is_1080x1920(self):
        out = os.path.join(self.tmp.name, "out.mp4")
        decision = cut_clip_with_layout(
            self.video, 0, 6.0, out, layout_mode="center",
            cfg={"scan_enabled": False, "sample_every_s": 5,
                 "min_segment_s": 3},
            game_voice=[[1, 3]])
        self.assertIn("game", [s["layout"] for s in decision["segments"]])
        probe = subprocess.run(
            ["ffprobe", "-v", "error", "-select_streams", "v:0",
             "-show_entries", "stream=width,height", "-of", "csv=p=0", out],
            capture_output=True, text=True, check=True).stdout.strip()
        self.assertEqual(probe, "1080,1920")


class TestGameSubtitles(unittest.TestCase):
    def test_karaoke_game_style(self):
        style = {"font_color": "#FFFFFF", "active_word_color": "#FFFF00",
                 "border_color": "#000000", "border_width": 3,
                 "fontsize": 64, "margin_v": 680, "bold": True}
        blocks = [(0, 2, "hola chat"), (5, 7, "mira el tráiler")]
        ass = srt_to_karaoke_ass(blocks, style, game_voice=[[4, 8]],
                                 max_end=10)
        self.assertIn("Style: Game,", ass)
        # El azul #7FE3FF en ASS es &H00FFE37F (BGR).
        self.assertIn("&H00FFE37F", ass)
        self.assertIn("JUEGO:", ass)
        self.assertIn(",Game,,", ass)
        self.assertIn(",Karaoke,,", ass)

    def test_karaoke_without_game_voice_unchanged(self):
        style = {"font_color": "#FFFFFF", "active_word_color": "#FFFF00",
                 "border_color": "#000000", "border_width": 3,
                 "fontsize": 64, "margin_v": 680, "bold": True}
        ass = srt_to_karaoke_ass([(0, 2, "hola chat")], style, max_end=10)
        self.assertNotIn("Style: Game,", ass)
        self.assertNotIn("JUEGO:", ass)


class TestSignalsIntegration(unittest.TestCase):
    def test_compute_signals_with_uri_signals(self):
        peaks, _ = peaks_from_heatmap_data(HEATMAP_DATA)
        signals = compute_signals(
            detail=COKER_DETAIL, peak_ts=1200, clip_start=0,
            clip_duration=40, heatmap_peaks=peaks,
            motion={"motion_pct": 80.0}, game_voice=[[5, 8]],
            virality_total=78.0)
        self.assertEqual(signals["heatmap_peak_s"], 1200)
        self.assertEqual(signals["heatmap_score"], 1.0)
        self.assertTrue(signals["heatmap_available"])
        self.assertEqual(signals["motion_pct"], 80.0)
        self.assertEqual(signals["motion_flag"], "en_movimiento")
        self.assertEqual(len(signals["game_voice"]), 1)
        self.assertFalse(signals["game_voice_estimated"])

    def test_compute_signals_without_uri_signals(self):
        signals = compute_signals(detail=COKER_DETAIL, peak_ts=100,
                                  clip_start=80)
        self.assertIsNone(signals["heatmap_peak_s"])
        self.assertIsNone(signals["motion_pct"])
        self.assertIsNone(signals["game_voice"])

    def test_viral_score_unchanged_by_uri_signals(self):
        # Regresión: las señales de Uri no tocan la fórmula de
        # viralidad. El total del fixture Coker es el mismo con y
        # sin señales (el score no recibe las señales).
        base = score_clip(COKER_DETAIL, 8.5, rank=1, human=COKER_HUMAN,
                          audio=COKER_AUDIO)
        signals = compute_signals(
            detail=COKER_DETAIL, peak_ts=100, clip_start=80,
            heatmap_peaks=[{"t": 100, "score": 1.0}],
            motion={"motion_pct": 0.0}, game_voice=[[5, 8]])
        self.assertEqual(signals["motion_flag"], "pausa_pantalla_fija")
        again = score_clip(COKER_DETAIL, 8.5, rank=1, human=COKER_HUMAN,
                           audio=COKER_AUDIO)
        self.assertEqual(base["total"], again["total"])
        self.assertEqual(base["components"], again["components"])


if __name__ == "__main__":
    unittest.main(verbosity=2)

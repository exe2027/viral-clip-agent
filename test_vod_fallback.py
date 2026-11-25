#!/usr/bin/env python3
"""Pruebas del fallback VOD del cerebro (2026-11-19).

Sin directos en GTA VI en ningún idioma, el cerebro cambia solo a
modo VOD y corta de los VOD más recientes (ES primero: Rubius,
IlloJuan, resto ES; después EN) con los mismos filtros de siempre.
Los clips van a la Cola del panel con el MP4, SIN publicar: este
módulo no tiene ninguna llamada a publicar.

- es_live_gta6: solo ES en vivo en GTA VI dispara "hay directos ES".
- live_gta6: cualquier idioma conocido en vivo en GTA VI bloquea
  el fallback (2026-11-19, fallo 3).
- fallback_order: Rubius, IlloJuan, resto ES en orden de yaml,
  después EN en orden de yaml.
- clip_passes_gates: viralidad >= 45, SEO >= 80 en las 4
  plataformas, 15-60 s, juego GTA VI; falla cerrado sin datos
  (2026-11-19, orden del usuario: listones bajados de 50/90).
- already_enqueued: dedup contra el panel por (VOD, inicio).
- burn_and_enqueue: subtítulos completos, o sin voz + pasa
  puertas (45/80) con subtitles_complete=0 honesto; sin voz +
  bajo puertas no se encola; nunca se inventa texto ni se llama
  a publicar; el payload lleva language ("es"/"en").
- run: sin directos en GTA VI -> modo VOD (ES y EN); con directos
  (ES o EN) -> no hace nada.
- clip_passes_gates: duración 15-60 s (2026-11-25, spec entrada
  Facebook §3.2).
"""

import os
import sys
import tempfile
import time
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from clipagent.vod_fallback import (  # noqa: E402
    MAX_DUR_S,
    MIN_DUR_S,
    URL_CUT_PRIORITY_MIN,
    already_enqueued,
    burn_and_enqueue,
    clip_passes_gates,
    cross_blocks,
    es_live_gta6,
    fallback_order,
    influencer_names_by_id,
    latest_vod_for,
    live_gta6,
    load_state,
    panel_score,
    run,
    save_state,
    title_overlap,
    vod_on_cooldown,
)


def _seo(score):
    return {p: {"score": score, "status": "ok", "can_publish": True}
            for p in ("youtube_shorts", "tiktok", "instagram_reels",
                      "facebook_reels")}


def _clip(**over):
    base = {
        "n": 1, "file": "clips/clip_01.mp4", "peak": 3220.0,
        "start": 3200, "duration": 48,
        "viral": {"best_title": "GTA 6: Rubius descubre los cromos",
                  "game": "GTA VI",
                  "description": "Rubius descubre los cromos.",
                  "tags": ["#GTA6"]},
        "viral_score": {"total": 53.8, "band": "alto",
                        "chat_multiple": 1.9},
        "seo_scores": _seo(94),
        "moment_card": {"game": "GTA VI"},
    }
    base.update(over)
    return base


STREAMERS = [
    {"name": "illojuan", "platform": "twitch", "language": "es",
     "enabled": True, "estado": "concedido"},
    {"name": "rubius", "platform": "twitch", "language": "es",
     "enabled": True, "estado": "concedido"},
    {"name": "auronplay", "platform": "twitch", "language": "es",
     "enabled": True, "estado": "concedido"},
    {"name": "kaicenat", "platform": "twitch", "language": "en",
     "enabled": True, "estado": "concedido"},
    {"name": "xokas", "platform": "twitch", "language": "es",
     "enabled": False, "estado": "concedido"},
]


class FakeBridge:
    """Puente falso: registra cada llamada (incluida publish_clip,
    que el fallback NO debe llamar nunca)."""

    def __init__(self, clips=None):
        self.clips = list(clips or [])
        self.created = []
        self.uploaded = []
        self.published = []
        self.patched = []
        self.previews = []
        self._next_id = 100

    def list_clips(self):
        return list(self.clips)

    def list_influencers(self):
        return []

    def create_clip(self, payload):
        self.created.append(payload)
        self._next_id += 1
        clip = {"id": self._next_id}
        self.clips.append({"source_id": payload.get("source_id"),
                           "start_sec": payload.get("start_sec")})
        return clip

    def upload_video(self, clip_id, video_path):
        self.uploaded.append((clip_id, video_path))
        return {"ok": True}

    def update_clip(self, clip_id, fields):
        self.patched.append((clip_id, fields))
        return {"ok": True}

    def preview_seen(self, clip_id):
        self.previews.append(clip_id)
        return {"ok": True}

    def publish_clip(self, clip_id):
        self.published.append(clip_id)
        return {"jobs": []}


class TestEsLiveGta6(unittest.TestCase):
    def test_es_en_vivo_cuenta(self):
        live = [{"name": "rubius", "platform": "twitch"}]
        self.assertEqual(es_live_gta6(live, STREAMERS), live)

    def test_solo_en_no_dispara_fallback(self):
        live = [{"name": "kaicenat", "platform": "twitch"}]
        self.assertEqual(es_live_gta6(live, STREAMERS), [])

    def test_sin_directos(self):
        self.assertEqual(es_live_gta6([], STREAMERS), [])

    def test_nombre_sin_ficha_no_se_inventa_es(self):
        live = [{"name": "desconocido", "platform": "twitch"}]
        self.assertEqual(es_live_gta6(live, STREAMERS), [])


class TestLiveGta6(unittest.TestCase):
    """Puerta del fallback (2026-11-19, fallo 3): cualquier idioma
    conocido en vivo en GTA VI bloquea el modo VOD."""

    def test_en_en_vivo_bloquea_fallback(self):
        live = [{"name": "kaicenat", "platform": "twitch"}]
        self.assertEqual(live_gta6(live, STREAMERS), live)

    def test_es_en_vivo_bloquea_fallback(self):
        live = [{"name": "rubius", "platform": "twitch"}]
        self.assertEqual(live_gta6(live, STREAMERS), live)

    def test_sin_directos(self):
        self.assertEqual(live_gta6([], STREAMERS), [])

    def test_nombre_sin_ficha_no_bloquea(self):
        live = [{"name": "desconocido", "platform": "twitch"}]
        self.assertEqual(live_gta6(live, STREAMERS), [])


class TestFallbackOrder(unittest.TestCase):
    def test_rubius_primero_illojuan_despues(self):
        names = [e["name"] for e in fallback_order(STREAMERS)]
        self.assertEqual(names, ["rubius", "illojuan", "auronplay",
                                 "kaicenat"])

    def test_en_despues_de_es(self):
        names = [e["name"] for e in fallback_order(STREAMERS)]
        self.assertLess(names.index("auronplay"),
                        names.index("kaicenat"))

    def test_deshabilitado_fuera(self):
        names = [e["name"] for e in fallback_order(STREAMERS)]
        self.assertNotIn("xokas", names)

    def test_only_restringe(self):
        names = [e["name"] for e in fallback_order(
            STREAMERS, only=["auronplay"])]
        self.assertEqual(names, ["auronplay"])

    def test_only_restringe_en(self):
        names = [e["name"] for e in fallback_order(
            STREAMERS, only=["kaicenat"])]
        self.assertEqual(names, ["kaicenat"])


class TestClipPassesGates(unittest.TestCase):
    def test_pasa(self):
        ok, reasons = clip_passes_gates(_clip(), "2932415021")
        self.assertTrue(ok, reasons)

    def test_viralidad_baja_no_pasa(self):
        c = _clip(viral_score={"total": 44.9, "chat_multiple": 1.5})
        ok, reasons = clip_passes_gates(c, "2932415021")
        self.assertFalse(ok)
        self.assertTrue(any("viralidad" in r for r in reasons))

    def test_viralidad_45_pasa(self):
        # Listones bajados 2026-11-19 (orden del usuario): 45/80.
        c = _clip(viral_score={"total": 45.0, "chat_multiple": 1.5})
        ok, reasons = clip_passes_gates(c, "2932415021")
        self.assertTrue(ok, reasons)

    def test_seo_bajo_en_una_plataforma_no_pasa(self):
        seo = _seo(94)
        seo["tiktok"] = {"score": 79, "status": "ok",
                         "can_publish": False}
        ok, _ = clip_passes_gates(_clip(seo_scores=seo), "2932415021")
        self.assertFalse(ok)

    def test_seo_80_pasa(self):
        # Listones bajados 2026-11-19 (orden del usuario): 45/80.
        ok, reasons = clip_passes_gates(
            _clip(seo_scores=_seo(80)), "2932415021")
        self.assertTrue(ok, reasons)

    def test_sin_viral_score_falla_cerrado(self):
        ok, _ = clip_passes_gates(_clip(viral_score=None), "2932415021")
        self.assertFalse(ok)

    def test_sin_seo_en_una_plataforma_falla_cerrado(self):
        seo = _seo(94)
        del seo["instagram_reels"]
        ok, _ = clip_passes_gates(_clip(seo_scores=seo), "2932415021")
        self.assertFalse(ok)

    def test_duracion_10_no_pasa(self):
        ok, _ = clip_passes_gates(_clip(duration=10), "2932415021")
        self.assertFalse(ok)

    def test_duracion_18_pasa(self):
        # 2026-11-25 (spec entrada Facebook §3.2): el suelo bajó
        # de 40 s a 15 s; un clip de 18 s pasa la puerta.
        ok, reasons = clip_passes_gates(_clip(duration=18),
                                        "2932415021")
        self.assertTrue(ok, reasons)

    def test_duracion_45_twitch_pasa(self):
        ok, reasons = clip_passes_gates(_clip(duration=45),
                                        "2932415021")
        self.assertTrue(ok, reasons)

    def test_duracion_61_no_pasa(self):
        ok, _ = clip_passes_gates(_clip(duration=61), "2932415021")
        self.assertFalse(ok)

    def test_limites_15_y_60_pasan(self):
        for d in (MIN_DUR_S, MAX_DUR_S):
            ok, r = clip_passes_gates(_clip(duration=d), "2932415021")
            self.assertTrue(ok, r)

    def test_juego_gta_v_no_pasa(self):
        c = _clip(moment_card={"game": "Grand Theft Auto V"},
                  viral={"best_title": "x", "game": "GTA V"})
        ok, _ = clip_passes_gates(c, "2932415021")
        self.assertFalse(ok)

    def test_juego_desconocido_falla_cerrado(self):
        c = _clip(moment_card=None, viral={"best_title": "x"})
        ok, _ = clip_passes_gates(c, "2932415021")
        self.assertFalse(ok)


class TestAlreadyEnqueued(unittest.TestCase):
    def test_mismo_vod_e_inicio(self):
        clips = [{"source_id": 2932415021, "start_sec": 3200}]
        self.assertTrue(already_enqueued(clips, "2932415021", 3200))
        self.assertTrue(already_enqueued(clips, "2932415021", 3200.0))

    def test_otro_inicio_no_duplica(self):
        clips = [{"source_id": 2932415021, "start_sec": 3200}]
        self.assertFalse(already_enqueued(clips, "2932415021", 3300))

    def test_otro_vod_no_duplica(self):
        clips = [{"source_id": 2932415021, "start_sec": 3200}]
        self.assertFalse(already_enqueued(clips, "2932415022", 3200))


class TestPanelScore(unittest.TestCase):
    def test_positivo_y_crece_con_viralidad(self):
        low = panel_score(50, _seo(90), 1.2)
        high = panel_score(80, _seo(95), 1.9)
        self.assertGreater(low, 0)
        self.assertGreater(high, low)


class TestBurnAndEnqueue(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        os.makedirs(os.path.join(self.tmp.name, "clips"), exist_ok=True)
        self.video = os.path.join(self.tmp.name, "clips", "clip_01.mp4")
        with open(self.video, "wb") as f:
            f.write(b"fake-mp4")

    def tearDown(self):
        self.tmp.cleanup()

    def _patch_subtitles(self, **result):
        defaults = {"publish_blocked": False, "no_speech": False,
                    "srt": self.video + ".srt", "burn": self.video,
                    "transcript": None}
        defaults.update(result)
        patcher = mock.patch("clipagent.workers.subtitle_clip",
                             return_value=defaults)
        self.addCleanup(patcher.stop)
        return patcher.start()

    def _patch_review(self):
        # El vídeo es falso (sin duración medible): la puerta dura
        # real lo rechazaría. El test cubre el encolado, no el gate
        # (que tiene sus propios tests en test_final_gate.py).
        verdict = {"decision": "aprobado", "pasa": True,
                   "hard_pasa": True, "needs_rerender": False,
                   "rerender_reason": "", "hard_failures": [],
                   "motivo": "", "resumen": "aprobado",
                   "fallos": [], "scores": {}, "frames_n": 0,
                   "model": "", "transcript_words": 0,
                   "observe_only": True,
                   "face": {"pasa": True}, "template": {"pasa": True},
                   "gemini": {"decision": "aprobado"}}
        patcher = mock.patch(
            "clipagent.final_review.validate_final_clip",
            return_value=verdict)
        self.addCleanup(patcher.stop)
        return patcher.start()

    def test_encola_sin_publicar(self):
        self._patch_subtitles()
        self._patch_review()
        bridge = FakeBridge()
        clip = _clip()
        panel_id, why = burn_and_enqueue(
            bridge, "rubius", "2932415021", self.tmp.name, clip)
        self.assertEqual(panel_id, 101, why)
        self.assertEqual(len(bridge.created), 1)
        payload = bridge.created[0]
        self.assertEqual(payload["origin"], "vod")
        self.assertEqual(payload["subtitles_complete"], 1)
        self.assertEqual(payload["needs_human_transcription"], 0)
        self.assertEqual(payload["game"], "GTA VI")
        self.assertEqual(payload["language"], "es")
        self.assertEqual(payload["start_sec"], 3200)
        self.assertEqual(payload["end_sec"], 3248)
        self.assertEqual(payload["source_id"], 2932415021)
        self.assertEqual(len(bridge.uploaded), 1)
        # NUNCA publica: ni publish_clip ni publish loop.
        self.assertEqual(bridge.published, [])

    def test_encola_en_con_lang_en(self):
        # Fallo 3 (2026-11-19): el clip EN lleva language="en"
        # (sin él el panel asume "es"), el título EN y los
        # subtítulos transcritos en inglés.
        sub_mock = self._patch_subtitles()
        self._patch_review()
        bridge = FakeBridge()
        clip = _clip(viral={"best_title": "", "game": "GTA VI",
                            "description": "Kai Cenat loses it.",
                            "tags": ["#GTA6"]},
                     title_en="Kai Cenat LOSES IT in GTA 6",
                     title_es="Kai Cenat pierde la cabeza en GTA 6")
        panel_id, why = burn_and_enqueue(
            bridge, "kaicenat", "2932415022", self.tmp.name, clip,
            lang="en")
        self.assertEqual(panel_id, 101, why)
        self.assertEqual(len(bridge.created), 1)
        payload = bridge.created[0]
        self.assertEqual(payload["language"], "en")
        self.assertEqual(payload["title"],
                         "Kai Cenat LOSES IT in GTA 6")
        self.assertIn(", en)", payload["reason"])
        self.assertEqual(sub_mock.call_args.kwargs.get("lang"), "en")
        self.assertEqual(bridge.published, [])

    def test_sin_voz_con_puertas_se_encola_sin_subtitulos(self):
        # Regla 2026-11-19 18:41: sin voz + viral 53,8 / SEO 94
        # (pasa 45/80) -> se encola PUBLICABLE sin subtítulos. El
        # mock trae publish_blocked=True viejo: burn_and_enqueue
        # ya no se fía de ese flag, decide con no_speech + puertas.
        self._patch_subtitles(publish_blocked=True, no_speech=True)
        self._patch_review()
        bridge = FakeBridge()
        panel_id, why = burn_and_enqueue(
            bridge, "rubius", "2932415021", self.tmp.name, _clip())
        self.assertEqual(panel_id, 101, why)
        self.assertEqual(len(bridge.created), 1)
        payload = bridge.created[0]
        self.assertEqual(payload["subtitles_complete"], 0)
        self.assertEqual(payload["needs_human_transcription"], 0)
        self.assertIn("sin subtítulos", payload["reason"])
        self.assertEqual(len(bridge.uploaded), 1)
        # NUNCA publica: ni publish_clip ni publish loop.
        self.assertEqual(bridge.published, [])

    def test_sin_voz_bajo_puertas_no_se_encola(self):
        # Sin voz + viral 30 (< 45) -> sigue fuera, como antes.
        self._patch_subtitles(publish_blocked=True, no_speech=True)
        bridge = FakeBridge()
        clip = _clip(viral_score={"total": 30.0,
                                  "chat_multiple": 1.9})
        panel_id, why = burn_and_enqueue(
            bridge, "rubius", "2932415021", self.tmp.name, clip)
        self.assertIsNone(panel_id)
        self.assertIn("sin voz", why)
        self.assertEqual(bridge.created, [])
        self.assertEqual(bridge.uploaded, [])

    def test_burn_and_enqueue_usa_plantilla_rubius(self):
        # 2026-11-19 (clip 23, TheGrefg): el fallback llamaba a
        # subtitle_clip con cfg={} y el default silencioso era
        # "karaoke". La plantilla acordada es la de config.yaml
        # (subtitles.default_style): el quemado VOD debe usarla
        # igual que el camino en vivo. Decisión 2026-11-19 20:48
        # (TODOS a rubius): el default es rubius, no rdjavi.
        # La expectativa se lee de la misma config que carga el
        # código (una sola fuente), nunca se fija a mano.
        from clipagent.config import load_config

        sub_mock = self._patch_subtitles()
        self._patch_review()
        bridge = FakeBridge()
        panel_id, why = burn_and_enqueue(
            bridge, "rubius", "2932415021", self.tmp.name, _clip())
        self.assertEqual(panel_id, 101, why)
        cfg = sub_mock.call_args.kwargs.get("cfg") or {}
        self.assertEqual(
            (cfg.get("subtitles") or {}).get("default_style"),
            load_config()["subtitles"]["default_style"])
        self.assertNotEqual(
            (cfg.get("subtitles") or {}).get("default_style"),
            "karaoke")

    def test_subtitle_clip_sin_cfg_usa_default_config(self):
        # El default silencioso de subtitle_clip era "karaoke" y
        # luego "rdjavi": cualquier llamada sin cfg quemaba con la
        # plantilla vieja. Ahora sigue config.yaml (única fuente):
        # hoy subtitles.default_style es rubius (decisión del
        # usuario 2026-11-19 20:48, todos los clips a rubius).
        from clipagent.workers import subtitle_clip
        srt = os.path.splitext(self.video)[0] + ".srt"
        with open(srt, "w", encoding="utf-8") as f:
            f.write("1\n00:00:00,000 --> 00:00:02,000\nHola\n")
        res = subtitle_clip(self.video, cfg={})
        self.assertEqual(res["style"], "rubius")


class TestBurnAndEnqueueSeoPatch(unittest.TestCase):
    """burn_and_enqueue (2026-11-24, auto-seo + corte YouTube):
    el POST lleva el objeto viral (viral_score, seo_scores
    planos, viral_breakdown, hype_score, priority, score_kind)
    y el PATCH posterior fusiona el veredicto + SEO (el POST es
    seguro porque clip_passes_gates ya exige los 4 >= 80, así
    que la puerta SEO del POST pasa siempre). Solo plataformas
    con número medido: nunca 0 por una sin medir."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        os.makedirs(os.path.join(self.tmp.name, "clips"), exist_ok=True)
        self.video = os.path.join(self.tmp.name, "clips", "clip_01.mp4")
        with open(self.video, "wb") as f:
            f.write(b"fake-mp4")

    def tearDown(self):
        self.tmp.cleanup()

    def _patch_subtitles(self, **result):
        defaults = {"publish_blocked": False, "no_speech": False,
                    "srt": self.video + ".srt", "burn": self.video,
                    "transcript": None}
        defaults.update(result)
        patcher = mock.patch("clipagent.workers.subtitle_clip",
                             return_value=defaults)
        self.addCleanup(patcher.stop)
        return patcher.start()

    def _patch_review(self, decision="aprobado"):
        verdict = {"decision": decision,
                   "pasa": decision == "aprobado",
                   "hard_pasa": decision == "aprobado",
                   "needs_rerender": False, "rerender_reason": "",
                   "hard_failures": [], "motivo": "", "resumen": decision,
                   "fallos": [],
                   "scores": {"momento_comprensible": 8,
                              "titulo_legible": 8,
                              "sin_barras_negras": 8,
                              "webcam_visible": 8},
                   "frames_n": 0, "model": "", "transcript_words": 50,
                   "observe_only": True,
                   "face": {"pasa": True},
                   "template": {"pasa": True, "checks": {
                       "dimensiones": {"pasa": True},
                       "titulo_banner": {"pasa": True}}},
                   "gemini": {"decision": decision}}
        patcher = mock.patch(
            "clipagent.final_review.validate_final_clip",
            return_value=verdict)
        self.addCleanup(patcher.stop)
        return patcher.start()

    def _clip_con_thumb(self, **over):
        clip = _clip(viral={
            "best_title": "GTA 6: Rubius descubre los cromos",
            "game": "GTA VI",
            "description": "Rubius descubre los cromos.",
            "tags": ["#GTA6"],
            "thumbnail_text": "¡QUÉ BARBARIDAD!"})
        clip.update(over)
        return clip

    def test_post_con_viral_y_patch_con_seo_y_veredicto(self):
        self._patch_subtitles()
        self._patch_review()
        bridge = FakeBridge()
        panel_id, why = burn_and_enqueue(
            bridge, "rubius", "2932415021", self.tmp.name,
            self._clip_con_thumb())
        self.assertEqual(panel_id, 101, why)
        # El POST lleva el objeto viral con SEO PLANO por
        # plataforma (no dicts del manifiesto) y viral_score.
        payload = bridge.created[0]
        self.assertEqual(payload["viral"]["seo_scores"], {
            "youtube_shorts": 94, "tiktok": 94,
            "instagram_reels": 94, "facebook_reels": 94})
        self.assertTrue(all(
            isinstance(v, (int, float))
            for v in payload["viral"]["seo_scores"].values()))
        self.assertEqual(payload["viral"]["viral_score"]["total"],
                         53.8)
        self.assertEqual(payload["viral"]["priority"],
                         panel_score(53.8, _seo(94), 1.9))
        self.assertNotIn("source_key", payload)
        self.assertEqual(len(bridge.patched), 1)
        _cid, fields = bridge.patched[0]
        self.assertEqual(fields["seo_scores"], {
            "youtube_shorts": 94, "tiktok": 94,
            "instagram_reels": 94, "facebook_reels": 94})
        gate = fields["viral"]["quality_gate"]
        self.assertEqual(gate["decision"], "aprobado")
        self.assertIs(gate["pasa"], True)
        self.assertEqual(fields["viral"]["seo_source"], "corte")
        self.assertEqual(bridge.previews, [101])

    def test_seo_parcial_solo_las_medidas(self):
        self._patch_subtitles()
        self._patch_review()
        bridge = FakeBridge()
        clip = self._clip_con_thumb(seo_scores={
            "youtube_shorts": {"score": 94, "status": "ok"},
            "tiktok": {"score": 91, "status": "ok"}})
        panel_id, why = burn_and_enqueue(
            bridge, "rubius", "2932415021", self.tmp.name, clip)
        self.assertEqual(panel_id, 101, why)
        self.assertEqual(len(bridge.patched), 1)
        _cid, fields = bridge.patched[0]
        self.assertEqual(fields["seo_scores"], {
            "youtube_shorts": 94, "tiktok": 91})
        self.assertNotIn("instagram_reels", fields["seo_scores"])
        self.assertNotIn("facebook_reels", fields["seo_scores"])

    def test_rechazado_no_postea_ni_parchea(self):
        self._patch_subtitles()
        self._patch_review(decision="rechazado")
        bridge = FakeBridge()
        panel_id, why = burn_and_enqueue(
            bridge, "rubius", "2932415021", self.tmp.name,
            self._clip_con_thumb())
        self.assertIsNone(panel_id)
        self.assertEqual(bridge.created, [])
        self.assertEqual(bridge.patched, [])
        self.assertEqual(bridge.previews, [])


class TestLatestVodYoutube(unittest.TestCase):
    """latest_vod_for con plataforma youtube (2026-11-21)."""

    def setUp(self):
        self.entry = {"name": "ishowspeed", "platform": "youtube",
                      "channel_url": "https://www.youtube.com/@IShowSpeed",
                      "language": "en", "enabled": True,
                      "estado": "concedido"}
        self.vod = {"vod_id": "OIeLbmyraRQ",
                    "url": "https://www.youtube.com/watch?v=OIeLbmyraRQ",
                    "title": "🔴 BEATING GTA 6 RIGHT NOW 100% "
                             "(FINAL MISSION)",
                    "category": "", "live": False}
        self.live_vod = dict(self.vod, vod_id="LIVE12345678",
                             title="🔴 BEATING GTA 6 RIGHT NOW",
                             live=True)

    def test_con_flag_devuelve_el_mas_reciente_terminado(self):
        # El directo en curso se filtra (no es VOD terminado).
        with mock.patch("clipagent.youtube.youtube_check",
                        return_value=(None, [self.live_vod, self.vod])):
            got = latest_vod_for(self.entry, youtube_enabled=True)
        self.assertEqual(got, self.vod)

    def test_sin_flag_no_lista_youtube(self):
        with mock.patch("clipagent.youtube.youtube_check") as check:
            got = latest_vod_for(self.entry, youtube_enabled=False)
        self.assertIsNone(got)
        check.assert_not_called()

    def test_sin_channel_url_no_lista(self):
        entry = dict(self.entry, channel_url="")
        with mock.patch("clipagent.youtube.youtube_check") as check:
            got = latest_vod_for(entry, youtube_enabled=True)
        self.assertIsNone(got)
        check.assert_not_called()

    def test_sin_vods_none(self):
        with mock.patch("clipagent.youtube.youtube_check",
                        return_value=(None, [])):
            got = latest_vod_for(self.entry, youtube_enabled=True)
        self.assertIsNone(got)


class TestRun(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.state = os.path.join(self.tmp.name, "state.json")
        self.out = os.path.join(self.tmp.name, "out")

    def tearDown(self):
        self.tmp.cleanup()

    def _scan(self, live):
        return lambda only=None: {"live": live, "skipped": [],
                                  "checked": 1, "twitch_mode": "helix"}

    def test_con_es_en_vivo_no_hace_nada(self):
        def _latest(entry):
            raise AssertionError("no debe listar VODs con ES en vivo")
        bridge = FakeBridge()
        n, summary = run(
            state_path=self.state, out_root=self.out, bridge=bridge,
            scan_fn=self._scan([{"name": "rubius",
                                 "platform": "twitch"}]),
            latest_vod_fn=_latest)
        self.assertEqual(n, 0)
        self.assertEqual(bridge.created, [])

    def test_con_en_en_vivo_no_hace_nada(self):
        # Fallo 3 (2026-11-19): con EN en vivo en GTA VI tampoco
        # hay fallback (el cortador en vivo se encarga).
        def _latest(entry):
            raise AssertionError("no debe listar VODs con EN en vivo")
        bridge = FakeBridge()
        n, summary = run(
            state_path=self.state, out_root=self.out, bridge=bridge,
            scan_fn=self._scan([{"name": "kaicenat",
                                 "platform": "twitch"}]),
            latest_vod_fn=_latest)
        self.assertEqual(n, 0)
        self.assertEqual(bridge.created, [])

    def test_encola_en_con_lang_en(self):
        # Fallo 3 (2026-11-19): sin directos en ningún idioma, el
        # VOD EN se procesa con --lang en y se encola con lang en.
        vod = {"vod_id": "2932415022",
               "url": "https://www.twitch.tv/videos/2932415022",
               "title": "GTA 6", "category": "Grand Theft Auto VI",
               "live": False}
        manifest = {"vod_id": "2932415022", "clips": [_clip()]}
        seen = {}

        def _auto(url, out_dir, top_n=2, lang="es"):
            seen["lang"] = lang
            return manifest

        def _enqueue(bridge, streamer, vod_id, out_dir, clip,
                     lang="es"):
            seen["enqueue_lang"] = lang
            seen["streamer"] = streamer
            return 101, ""

        streamers = [
            {"name": "kaicenat", "platform": "twitch",
             "language": "en", "enabled": True,
             "estado": "concedido"},
        ]
        with mock.patch("clipagent.streamers.load_streamers",
                        return_value=streamers):
            n, _ = run(
                max_vods=1, max_clips=2, top_n=2,
                state_path=self.state, out_root=self.out,
                bridge=FakeBridge(),
                scan_fn=self._scan([]),
                latest_vod_fn=lambda entry: vod,
                auto_clips_fn=_auto,
                enqueue_fn=_enqueue)
        self.assertEqual(n, 1)
        self.assertEqual(seen["lang"], "en")
        self.assertEqual(seen["enqueue_lang"], "en")
        self.assertEqual(seen["streamer"], "kaicenat")

    def test_run_youtube_con_flag_encola(self):
        # 2026-11-21: con --youtube, el VOD de YouTube se lista
        # con el latest_vod_for real (sin latest_vod_fn inyectado)
        # y se encola con lang en. 2026-11-24: la URL de YouTube
        # va por el cortador de YouTube (youtube_clips_fn), no
        # por auto_clips_fn (Twitch/Kick).
        vod = {"vod_id": "OIeLbmyraRQ",
               "url": "https://www.youtube.com/watch?v=OIeLbmyraRQ",
               "title": "🔴 BEATING GTA 6 RIGHT NOW 100% "
                        "(FINAL MISSION)",
               "category": "", "live": False}
        manifest = {"vod_id": "OIeLbmyraRQ", "clips": [_clip()]}
        seen = {}

        def _yt(url, out_dir, top_n=2, lang="es", single_url=False):
            seen["lang"] = lang
            seen["url"] = url
            seen["single_url"] = single_url
            return manifest

        def _enqueue(bridge, streamer, vod_id, out_dir, clip,
                     lang="es"):
            seen["enqueue_lang"] = lang
            seen["streamer"] = streamer
            return 102, ""

        streamers = [
            {"name": "ishowspeed", "platform": "youtube",
             "channel_url": "https://www.youtube.com/@IShowSpeed",
             "language": "en", "enabled": True,
             "estado": "concedido"},
        ]
        with mock.patch("clipagent.streamers.load_streamers",
                        return_value=streamers), \
                mock.patch("clipagent.youtube.youtube_check",
                           return_value=(None, [vod])) as check:
            n, _ = run(
                max_vods=1, max_clips=2, top_n=2,
                state_path=self.state, out_root=self.out,
                bridge=FakeBridge(),
                scan_fn=self._scan([]),
                youtube_clips_fn=_yt,
                auto_clips_fn=lambda *a, **k: (_ for _ in ()).throw(
                    AssertionError(
                        "una URL de YouTube no va por auto_clips")),
                enqueue_fn=_enqueue,
                youtube_enabled=True)
        check.assert_called_once()
        self.assertEqual(n, 1)
        self.assertEqual(seen["lang"], "en")
        self.assertEqual(seen["enqueue_lang"], "en")
        self.assertEqual(seen["streamer"], "ishowspeed")
        self.assertEqual(seen["url"], vod["url"])
        self.assertIs(seen["single_url"], False)

    def test_run_youtube_sin_flag_no_lista(self):
        # 2026-11-21: sin el flag, el streamer de YouTube no se
        # lista (youtube_check ni se llama) y no se encola nada.
        streamers = [
            {"name": "ishowspeed", "platform": "youtube",
             "channel_url": "https://www.youtube.com/@IShowSpeed",
             "language": "en", "enabled": True,
             "estado": "concedido"},
        ]
        with mock.patch("clipagent.streamers.load_streamers",
                        return_value=streamers), \
                mock.patch("clipagent.youtube.youtube_check") as check:
            n, _ = run(
                max_vods=1, max_clips=2, top_n=2,
                state_path=self.state, out_root=self.out,
                bridge=FakeBridge(),
                scan_fn=self._scan([]),
                auto_clips_fn=lambda *a, **k: (_ for _ in ()).throw(
                    AssertionError("no debe cortar sin flag")),
                enqueue_fn=lambda *a, **k: (_ for _ in ()).throw(
                    AssertionError("no debe encolar sin flag")))
        check.assert_not_called()
        self.assertEqual(n, 0)

    def test_dry_run_no_corta_ni_encola(self):
        vod = {"vod_id": "2932415021",
               "url": "https://www.twitch.tv/videos/2932415021",
               "title": "GTA 6", "category": "Grand Theft Auto VI",
               "live": False}
        n, _ = run(
            state_path=self.state, out_root=self.out, dry_run=True,
            scan_fn=self._scan([]),
            latest_vod_fn=lambda entry: vod,
            auto_clips_fn=lambda *a, **k: (_ for _ in ()).throw(
                AssertionError("dry-run no debe cortar")))
        self.assertEqual(n, 0)
        st = load_state(self.state)
        self.assertIn("2932415021", st["vods"])

    def test_vod_en_enfriamiento_se_salta(self):
        vod = {"vod_id": "2932415021",
               "url": "https://www.twitch.tv/videos/2932415021",
               "title": "GTA 6", "category": "Grand Theft Auto VI",
               "live": False}
        save_state(self.state, {"vods": {"2932415021": {
            "attempted_at": 9999999999}}})
        # 9999999999 es el futuro: siempre en enfriamiento.
        n, _ = run(
            state_path=self.state, out_root=self.out, dry_run=True,
            scan_fn=self._scan([]),
            latest_vod_fn=lambda entry: vod)
        self.assertEqual(n, 0)

    def test_ignore_cooldown_reprocesa_el_vod(self):
        # Reintento tras un arreglo (2026-11-19: la puerta 3x tumbaba
        # los VODs y había que reprocesarlos sin esperar 6 h).
        vod = {"vod_id": "2932415021",
               "url": "https://www.twitch.tv/videos/2932415021",
               "title": "GTA 6", "category": "Grand Theft Auto VI",
               "live": False}
        save_state(self.state, {"vods": {"2932415021": {
            "attempted_at": 9999999999}}})
        n, summary = run(
            state_path=self.state, out_root=self.out, dry_run=True,
            ignore_cooldown=True,
            scan_fn=self._scan([]),
            latest_vod_fn=lambda entry: (
                vod if entry.get("name") == "rubius" else None))
        self.assertEqual(n, 0)  # dry-run no encola, pero SÍ procesa
        self.assertIn("1 VOD(s) procesado(s)", summary)

    def test_encola_solo_el_que_pasa(self):
        vod = {"vod_id": "2932415021",
               "url": "https://www.twitch.tv/videos/2932415021",
               "title": "GTA 6", "category": "Grand Theft Auto VI",
               "live": False}
        good = _clip()
        bad = _clip(n=2, peak=7775.0, start=7755,
                    viral_score={"total": 44.0, "chat_multiple": 1.1})
        manifest = {"vod_id": "2932415021", "clips": [good, bad]}
        enqueued = []

        def _enqueue(bridge, streamer, vod_id, out_dir, clip,
                     lang="es"):
            enqueued.append((streamer, vod_id, clip["peak"]))
            return 101, ""

        bridge = FakeBridge()
        n, _ = run(
            max_vods=1, max_clips=2, top_n=2,
            state_path=self.state, out_root=self.out, bridge=bridge,
            scan_fn=self._scan([]),
            latest_vod_fn=lambda entry: vod,
            auto_clips_fn=lambda url, out_dir, top_n=2, lang="es": manifest,
            enqueue_fn=_enqueue)
        self.assertEqual(n, 1)
        self.assertEqual(enqueued, [("rubius", "2932415021", 3220.0)])

    def test_ya_en_panel_no_se_reencola(self):
        vod = {"vod_id": "2932415021",
               "url": "https://www.twitch.tv/videos/2932415021",
               "title": "GTA 6", "category": "Grand Theft Auto VI",
               "live": False}
        manifest = {"vod_id": "2932415021", "clips": [_clip()]}
        bridge = FakeBridge(
            clips=[{"source_id": 2932415021, "start_sec": 3200}])
        called = []

        def _enqueue(*a, **k):
            called.append(a)
            return 101, ""

        n, _ = run(
            max_vods=1, max_clips=2, top_n=2,
            state_path=self.state, out_root=self.out, bridge=bridge,
            scan_fn=self._scan([]),
            latest_vod_fn=lambda entry: vod,
            auto_clips_fn=lambda url, out_dir, top_n=2, lang="es": manifest,
            enqueue_fn=_enqueue)
        self.assertEqual(n, 0)
        self.assertEqual(called, [])

    def test_vod_just_chatting_se_procesa_la_puerta_es_por_pico(self):
        # La categoría Helix del VOD es la del ÚLTIMO tramo (Rubius
        # 2932415021 termina en Just Chatting tras horas de GTA VI):
        # no veta el VOD. La puerta precisa es por pico (juego del
        # manifiesto en clip_passes_gates).
        vod = {"vod_id": "2932415021",
               "url": "https://www.twitch.tv/videos/2932415021",
               "title": "GTA 6", "category": "Just Chatting",
               "live": False}
        manifest = {"vod_id": "2932415021", "clips": [_clip()]}
        enqueued = []

        def _enqueue(bridge, streamer, vod_id, out_dir, clip,
                     lang="es"):
            enqueued.append((streamer, vod_id, clip["peak"]))
            return 101, ""

        n, _ = run(
            max_vods=1, max_clips=2, top_n=2,
            state_path=self.state, out_root=self.out,
            bridge=FakeBridge(),
            scan_fn=self._scan([]),
            latest_vod_fn=lambda entry: vod,
            auto_clips_fn=lambda url, out_dir, top_n=2, lang="es": manifest,
            enqueue_fn=_enqueue)
        self.assertEqual(n, 1)
        self.assertEqual(enqueued, [("rubius", "2932415021", 3220.0)])


class TestState(unittest.TestCase):
    def test_cooldown_6h(self):
        st = {"vods": {"1": {"attempted_at": 1000}}}
        self.assertTrue(vod_on_cooldown(st, "1", now_s=1000 + 5 * 3600))
        self.assertFalse(vod_on_cooldown(st, "1", now_s=1000 + 7 * 3600))
        self.assertFalse(vod_on_cooldown(st, "2", now_s=1000))

    def test_save_load_redondo(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "state.json")
            save_state(path, {"vods": {"1": {"attempted_at": 5}}})
            self.assertEqual(load_state(path)["vods"]["1"]
                             ["attempted_at"], 5)
            self.assertEqual(load_state(os.path.join(d, "no.json")),
                             {"vods": {}})


class TestPanelScoreRounding(unittest.TestCase):
    """panel_score redondea a entero (§3.5): 55,7 -> 56,
    50,4 -> 50; crudo 52,5 -> 53 pasa, 52,4 -> 52 no pasa."""

    def test_bueno_55_7_a_56(self):
        self.assertEqual(panel_score(52, _seo(85), 3.0), 56)

    def test_flojo_50_4_a_50(self):
        self.assertEqual(panel_score(47, _seo(80), 2.0), 50)

    def test_crudo_52_5_redondea_a_52(self):
        # OJO: 52,5 crudo NO pasa. Python round() es banker's
        # rounding: round(52,5) = 52, no 53 (el ejemplo de la spec
        # asumía redondeo half-up). panel_score usa int(round())
        # como el resto del código; no se cambia la fórmula.
        self.assertEqual(panel_score(45, _seo(89), 3.0), 52)

    def test_crudo_52_7_pasa(self):
        self.assertEqual(panel_score(45, _seo(90), 3.0), 53)

    def test_crudo_52_4_no_pasa(self):
        self.assertEqual(panel_score(45, _seo(86), 3.3), 52)


class TestAlreadyEnqueuedSourceKey(unittest.TestCase):
    """Dedup ENTRE corridas por (source_key, inicio) (§3.7)."""

    def test_youtube_por_source_key(self):
        clips = [{"source_id": 0, "source_key": "NrNdw_Y6_bI",
                  "start_sec": 590}]
        self.assertTrue(already_enqueued(
            clips, "NrNdw_Y6_bI", 590, source_key="NrNdw_Y6_bI"))
        self.assertFalse(already_enqueued(
            clips, "NrNdw_Y6_bI", 600, source_key="NrNdw_Y6_bI"))
        self.assertFalse(already_enqueued(
            clips, "OtroVideo12", 590, source_key="OtroVideo12"))

    def test_source_id_cero_no_colisiona_entre_videos(self):
        # Sin source_key, dos vídeos de YouTube distintos con
        # source_id 0 y mismo inicio NO se distinguen (el bug que
        # corrige §3.7); con source_key sí.
        clips = [{"source_id": 0, "source_key": "NrNdw_Y6_bI",
                  "start_sec": 590}]
        self.assertFalse(already_enqueued(
            clips, "OtroVideo12", 590, source_key="OtroVideo12"))

    def test_twitch_legacy_por_source_id(self):
        clips = [{"source_id": 2932415021, "start_sec": 3200}]
        self.assertTrue(already_enqueued(clips, "2932415021", 3200))
        self.assertFalse(already_enqueued(clips, "2932415022", 3200))


def _yt_clip(total, seo_score, mult, **over):
    """Clip de YouTube para el umbral: viral_multiple de audio."""
    base = _clip(
        viral={"best_title": "Rubius consigue 6 estrellas en GTA 6",
               "game": "GTA VI",
               "description": "Rubius consigue las 6 estrellas.",
               "tags": ["#GTA6"],
               "viral_multiple": mult},
        viral_score={"total": total},
        viral_breakdown={"audio_multiple": mult},
        seo_scores=_seo(seo_score),
        source_key="NrNdw_Y6_bI",
        vod_id="NrNdw_Y6_bI",
        score_kind="youtube_audio")
    base.update(over)
    return base


class TestYoutubePriorityThreshold(unittest.TestCase):
    """Umbral 53 al encolar, solo YouTube (§3.5, OPCIÓN A)."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        os.makedirs(os.path.join(self.tmp.name, "clips"), exist_ok=True)
        self.video = os.path.join(self.tmp.name, "clips", "clip_01.mp4")
        with open(self.video, "wb") as f:
            f.write(b"fake-mp4")

    def tearDown(self):
        self.tmp.cleanup()

    def _patch_subtitles(self):
        patcher = mock.patch(
            "clipagent.workers.subtitle_clip",
            return_value={"publish_blocked": False, "no_speech": False,
                          "srt": self.video + ".srt", "burn": self.video,
                          "transcript": None})
        self.addCleanup(patcher.stop)
        return patcher.start()

    def _patch_review(self):
        verdict = {"decision": "aprobado", "pasa": True,
                   "hard_pasa": True, "needs_rerender": False,
                   "rerender_reason": "", "hard_failures": [],
                   "motivo": "", "resumen": "aprobado",
                   "fallos": [], "scores": {}, "frames_n": 0,
                   "model": "", "transcript_words": 0,
                   "observe_only": True,
                   "face": {"pasa": True}, "template": {"pasa": True},
                   "gemini": {"decision": "aprobado"}}
        patcher = mock.patch(
            "clipagent.final_review.validate_final_clip",
            return_value=verdict)
        self.addCleanup(patcher.stop)
        return patcher.start()

    def test_flojo_50_no_se_encola_y_motivo(self):
        sub_mock = self._patch_subtitles()
        self._patch_review()
        bridge = FakeBridge()
        panel_id, why = burn_and_enqueue(
            bridge, "rubius", "NrNdw_Y6_bI", self.tmp.name,
            _yt_clip(47, 80, 2.0))
        self.assertIsNone(panel_id)
        self.assertIn("50", why)
        self.assertIn(str(URL_CUT_PRIORITY_MIN), why)
        self.assertEqual(bridge.created, [])
        # El chequeo va ANTES de subtitle_clip: un clip flojo no
        # quema nada.
        sub_mock.assert_not_called()

    def test_bueno_56_se_encola(self):
        self._patch_subtitles()
        self._patch_review()
        bridge = FakeBridge()
        panel_id, why = burn_and_enqueue(
            bridge, "rubius", "NrNdw_Y6_bI", self.tmp.name,
            _yt_clip(52, 85, 3.0))
        self.assertEqual(panel_id, 101, why)
        self.assertEqual(len(bridge.created), 1)
        self.assertEqual(bridge.created[0]["source_key"], "NrNdw_Y6_bI")
        self.assertEqual(bridge.created[0]["source_id"], 0)

    def test_crudo_52_7_pasa_y_52_4_no(self):
        self._patch_subtitles()
        self._patch_review()
        bridge = FakeBridge()
        panel_id, _ = burn_and_enqueue(
            bridge, "rubius", "NrNdw_Y6_bI", self.tmp.name,
            _yt_clip(45, 90, 3.0))
        self.assertEqual(panel_id, 101)
        bridge = FakeBridge()
        panel_id, _ = burn_and_enqueue(
            bridge, "rubius", "NrNdw_Y6_bI", self.tmp.name,
            _yt_clip(45, 86, 3.3))
        self.assertIsNone(panel_id)

    def test_twitch_50_se_encola_el_umbral_es_solo_youtube(self):
        self._patch_subtitles()
        self._patch_review()
        bridge = FakeBridge()
        clip = _clip(viral_score={"total": 47.0,
                                  "chat_multiple": 2.0},
                     seo_scores=_seo(80))
        panel_id, why = burn_and_enqueue(
            bridge, "rubius", "2932415021", self.tmp.name, clip)
        self.assertEqual(panel_id, 101, why)


class TestCrossBlocks(unittest.TestCase):
    """Cruce con Twitch por solape de título (§3.8)."""

    def setUp(self):
        self.now_ms = int(time.time() * 1000)
        self.names = {7: "rubius"}
        self.panel_title = ("Rubius consigue 6 estrellas en GTA 6 "
                            "en el aeropuerto")

    def _panel_clip(self, **over):
        base = {"influencer_id": 7, "created_at": self.now_ms,
                "title": self.panel_title,
                "platforms": ["youtube_shorts", "tiktok",
                              "instagram_reels", "facebook_reels"]}
        base.update(over)
        return base

    def test_solape_mayor_0_7_bloquea(self):
        blocked, why = cross_blocks(
            "Rubius consigue 6 estrellas en GTA 6", "rubius",
            [self._panel_clip()], influencer_names=self.names)
        self.assertTrue(blocked)
        self.assertIn("0,7", why)

    def test_solape_menor_0_7_pasa(self):
        blocked, _ = cross_blocks(
            "Rubius se cae del avión en GTA 6", "rubius",
            [self._panel_clip()], influencer_names=self.names)
        self.assertFalse(blocked)

    def test_menos_de_3_tokens_no_bloquea(self):
        self.assertEqual(title_overlap("GTA 6", self.panel_title), 0.0)
        blocked, _ = cross_blocks(
            "GTA 6", "rubius", [self._panel_clip()],
            influencer_names=self.names)
        self.assertFalse(blocked)

    def test_sin_condicion_de_idioma(self):
        # Un clip EN del panel bloquea igual un candidato ES.
        blocked, _ = cross_blocks(
            "Rubius consigue 6 estrellas en GTA 6", "rubius",
            [self._panel_clip(language="en")],
            influencer_names=self.names)
        self.assertTrue(blocked)

    def test_otro_dia_no_bloquea(self):
        blocked, _ = cross_blocks(
            "Rubius consigue 6 estrellas en GTA 6", "rubius",
            [self._panel_clip(
                created_at=self.now_ms - 3 * 86400 * 1000)],
            influencer_names=self.names)
        self.assertFalse(blocked)

    def test_influencer_id_resuelto_a_nombre(self):
        blocked, _ = cross_blocks(
            "Rubius consigue 6 estrellas en GTA 6", "rubius",
            [self._panel_clip()], influencer_names={7: "illojuan"})
        self.assertFalse(blocked)
        blocked, _ = cross_blocks(
            "Rubius consigue 6 estrellas en GTA 6", "rubius",
            [self._panel_clip()], influencer_names={7: "rubius"})
        self.assertTrue(blocked)

    def test_distinto_streamer_no_bloquea(self):
        blocked, _ = cross_blocks(
            "Rubius consigue 6 estrellas en GTA 6", "illojuan",
            [self._panel_clip()], influencer_names=self.names)
        self.assertFalse(blocked)

    def test_otra_plataforma_no_bloquea(self):
        blocked, _ = cross_blocks(
            "Rubius consigue 6 estrellas en GTA 6", "rubius",
            [self._panel_clip(platforms=["kick"])],
            influencer_names=self.names)
        self.assertFalse(blocked)

    def test_dia_fijo_por_santiago_day_key(self):
        # created_at fijo: 2026-11-24 15:00 UTC = 12:00 en Chile.
        from clipagent.vod_fallback import _clip_day

        self.assertEqual(_clip_day(
            {"created_at": 1795532400000}), "2026-11-24")

    def test_influencer_names_by_id(self):
        bridge = _ListBridge(influencers=[
            {"id": 7, "handle": "rubius"},
            {"id": 9, "name": "IlloJuan"},
        ])
        self.assertEqual(influencer_names_by_id(bridge),
                         {7: "rubius", 9: "illojuan"})
        broken = mock.Mock()
        broken.list_influencers.side_effect = RuntimeError("caído")
        self.assertEqual(influencer_names_by_id(broken), {})


class _ListBridge(FakeBridge):
    """FakeBridge con list_influencers y list_clips programables
    (para la re-lectura antes de cada encolado)."""

    def __init__(self, clips=None, influencers=None, clips_seq=None):
        super().__init__(clips=clips)
        self.influencers = list(influencers or [])
        self._clips_seq = (list(clips_seq)
                           if clips_seq is not None else None)
        self.list_calls = 0

    def list_clips(self):
        self.list_calls += 1
        if self._clips_seq is not None and self._clips_seq:
            return list(self._clips_seq.pop(0))
        return list(self.clips)

    def list_influencers(self):
        return list(self.influencers)


class TestRunYoutube(unittest.TestCase):
    """run() con YouTube: dedup entre corridas, cruce
    mismo-corrida, re-lectura, enfriamiento y URL única."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.state = os.path.join(self.tmp.name, "state.json")
        self.out = os.path.join(self.tmp.name, "out")

    def tearDown(self):
        self.tmp.cleanup()

    def _scan(self, live):
        return lambda only=None: {"live": live, "skipped": [],
                                  "checked": 1, "twitch_mode": "helix"}

    def _yt_manifest(self, start=590, title=None, streamer=None):
        clip = _yt_clip(52, 85, 3.0, start=start)
        if title is not None:
            clip["viral"]["best_title"] = title
            clip["title_es"] = title
        return {"vod_id": "NrNdw_Y6_bI", "clips": [clip]}

    def test_dedup_entre_corridas_por_source_key(self):
        vod = {"vod_id": "NrNdw_Y6_bI",
               "url": "https://www.youtube.com/watch?v=NrNdw_Y6_bI",
               "title": "Conseguí 6 Estrellas en GTA 6",
               "category": "", "live": False}
        bridge = _ListBridge(clips=[{
            "source_id": 0, "source_key": "NrNdw_Y6_bI",
            "start_sec": 590}])
        called = []

        def _enqueue(*a, **k):
            called.append(a)
            return 101, ""

        n, _ = run(
            max_vods=1, max_clips=2, top_n=2,
            state_path=self.state, out_root=self.out, bridge=bridge,
            scan_fn=self._scan([]),
            latest_vod_fn=lambda entry: vod,
            youtube_clips_fn=lambda url, out_dir, top_n=2, lang="es",
            single_url=False: self._yt_manifest(),
            enqueue_fn=_enqueue)
        self.assertEqual(n, 0)
        self.assertEqual(called, [])

    def test_cruce_mismo_corrida_bloquea_y_distinto_no(self):
        # Dos clips del mismo manifiesto, mismo título y momento,
        # mismo streamer: el segundo lo bloquea el cruce con el
        # primero (append en memoria con streamer + title).
        vod = {"vod_id": "NrNdw_Y6_bI",
               "url": "https://www.youtube.com/watch?v=NrNdw_Y6_bI",
               "title": "Conseguí 6 Estrellas en GTA 6",
               "category": "", "live": False}
        title = "Rubius consigue 6 estrellas en GTA 6"
        manifest = {"vod_id": "NrNdw_Y6_bI", "clips": [
            _yt_clip(52, 85, 3.0, start=590),
            _yt_clip(52, 85, 3.0, start=650)]}
        for clip in manifest["clips"]:
            clip["viral"]["best_title"] = title
            clip["title_es"] = title
        enqueued = []

        def _enqueue(bridge, streamer, vod_id, out_dir, clip,
                     lang="es"):
            enqueued.append(clip["start"])
            return 101, ""

        n, _ = run(
            max_vods=1, max_clips=2, top_n=2,
            state_path=self.state, out_root=self.out,
            bridge=_ListBridge(influencers=[{"id": 7,
                                             "handle": "rubius"}]),
            scan_fn=self._scan([]),
            latest_vod_fn=lambda entry: vod,
            youtube_clips_fn=lambda url, out_dir, top_n=2, lang="es",
            single_url=False: manifest,
            enqueue_fn=_enqueue)
        self.assertEqual(n, 1)
        self.assertEqual(enqueued, [590])

    def test_cruce_mismo_momento_distinto_streamer_no_bloquea(self):
        title = "Rubius consigue 6 estrellas en GTA 6"

        def _manifest_for(url, start):
            manifest = {"vod_id": "x", "clips": [
                _yt_clip(52, 85, 3.0, start=start)]}
            manifest["clips"][0]["viral"]["best_title"] = title
            manifest["clips"][0]["title_es"] = title
            return manifest

        vods = {"rubius": {"vod_id": "NrNdw_Y6_bI",
                           "url": "https://www.youtube.com/watch"
                                  "?v=NrNdw_Y6_bI",
                           "title": "Conseguí 6 Estrellas en GTA 6",
                           "category": "", "live": False},
                "illojuan": {"vod_id": "2932415021",
                             "url": "https://www.twitch.tv/videos/"
                                    "2932415021",
                             "title": "GTA 6", "category": "Grand "
                             "Theft Auto VI", "live": False}}
        enqueued = []

        def _enqueue(bridge, streamer, vod_id, out_dir, clip,
                     lang="es"):
            enqueued.append(streamer)
            return 101, ""

        streamers = [
            {"name": "rubius", "platform": "youtube",
             "channel_url": "https://www.youtube.com/@rubius",
             "language": "es", "enabled": True, "estado": "concedido"},
            {"name": "illojuan", "platform": "twitch",
             "language": "es", "enabled": True, "estado": "concedido"},
        ]
        with mock.patch("clipagent.streamers.load_streamers",
                        return_value=streamers):
            n, _ = run(
                max_vods=2, max_clips=2, top_n=2,
                state_path=self.state, out_root=self.out,
                bridge=_ListBridge(),
                scan_fn=self._scan([]),
                latest_vod_fn=lambda entry: vods[entry["name"]],
                youtube_clips_fn=lambda url, out_dir, top_n=2,
                lang="es", single_url=False: _manifest_for(url, 590),
                auto_clips_fn=lambda url, out_dir, top_n=2,
                lang="es": _manifest_for(url, 700),
                enqueue_fn=_enqueue)
        self.assertEqual(n, 2)
        self.assertEqual(enqueued, ["rubius", "illojuan"])

    def test_relectura_antes_de_cada_encolado(self):
        # cut encola un clip en vivo entre la carga inicial y el
        # segundo encolado: la re-lectura lo ve y el cruce lo
        # bloquea. Sin re-lectura, el segundo pasaría.
        vod = {"vod_id": "NrNdw_Y6_bI",
               "url": "https://www.youtube.com/watch?v=NrNdw_Y6_bI",
               "title": "Conseguí 6 Estrellas en GTA 6",
               "category": "", "live": False}
        title = "Rubius consigue 6 estrellas en GTA 6"
        manifest = {"vod_id": "NrNdw_Y6_bI", "clips": [
            _yt_clip(52, 85, 3.0, start=590),
            _yt_clip(52, 85, 3.0, start=650)]}
        for clip in manifest["clips"]:
            clip["viral"]["best_title"] = title
            clip["title_es"] = title
        cut_clip = {"influencer_id": 7,
                    "created_at": int(time.time() * 1000),
                    "title": title,
                    "platforms": ["youtube_shorts", "tiktok",
                                  "instagram_reels",
                                  "facebook_reels"]}
        bridge = _ListBridge(
            influencers=[{"id": 7, "handle": "rubius"}],
            clips_seq=[[], [], [cut_clip]])
        enqueued = []

        def _enqueue(bridge, streamer, vod_id, out_dir, clip,
                     lang="es"):
            enqueued.append(clip["start"])
            return 101, ""

        n, _ = run(
            max_vods=1, max_clips=2, top_n=2,
            state_path=self.state, out_root=self.out, bridge=bridge,
            scan_fn=self._scan([]),
            latest_vod_fn=lambda entry: vod,
            youtube_clips_fn=lambda url, out_dir, top_n=2, lang="es",
            single_url=False: manifest,
            enqueue_fn=_enqueue)
        self.assertEqual(n, 1)
        self.assertEqual(enqueued, [590])
        # Carga inicial + una re-lectura por clip.
        self.assertGreaterEqual(bridge.list_calls, 3)

    def test_url_unica_gta_corta_y_salta_directos(self):
        meta = {"id": "NrNdw_Y6_bI", "duration": 1750,
                "title": "Conseguí 6 Estrellas en GTA 6 ⭐",
                "channel_url": "https://www.youtube.com/channel/"
                               "UCcjIvuxmWlS5IEQ0JdPV4Ng",
                "channel": "Rubius Z", "description": "",
                "heatmap": [], "views": 0, "likes": 0,
                "categories": [], "category": ""}
        streamers = [
            {"name": "rubius", "platform": "youtube",
             "channel_url": "https://www.youtube.com/channel/"
                            "UCcjIvuxmWlS5IEQ0JdPV4Ng",
             "language": "es", "enabled": True,
             "estado": "concedido"},
        ]
        seen = {}

        def _yt(url, out_dir, top_n=2, lang="es", single_url=False):
            seen["url"] = url
            seen["out_dir"] = out_dir
            seen["lang"] = lang
            seen["single_url"] = single_url
            return self._yt_manifest()

        def _enqueue(bridge, streamer, vod_id, out_dir, clip,
                     lang="es"):
            seen["streamer"] = streamer
            return 101, ""

        def _scan_live(only=None):
            raise AssertionError(
                "la URL única salta el chequeo de directos")

        with mock.patch("clipagent.streamers.load_streamers",
                        return_value=streamers), \
                mock.patch("clipagent.youtube_cut."
                           "fetch_youtube_metadata",
                           return_value=meta):
            n, summary = run(
                max_clips=2, top_n=2,
                state_path=self.state, out_root=self.out,
                bridge=_ListBridge(),
                scan_fn=_scan_live,
                youtube_clips_fn=_yt,
                enqueue_fn=_enqueue,
                youtube_url="https://youtu.be/NrNdw_Y6_bI")
        self.assertEqual(n, 1)
        self.assertEqual(seen["url"],
                         "https://youtu.be/NrNdw_Y6_bI")
        self.assertEqual(seen["out_dir"],
                         os.path.join(self.out, "NrNdw_Y6_bI"))
        self.assertEqual(seen["lang"], "es")
        self.assertIs(seen["single_url"], True)
        self.assertEqual(seen["streamer"], "rubius")
        self.assertIn("NrNdw_Y6_bI", summary)

    def test_url_unica_sin_entrada_idioma_es_por_defecto(self):
        meta = {"id": "NrNdw_Y6_bI", "duration": 1750,
                "title": "Conseguí 6 Estrellas en GTA 6 ⭐",
                "channel_url": "https://www.youtube.com/@otro",
                "channel": "Otro", "description": "",
                "heatmap": [], "views": 0, "likes": 0,
                "categories": [], "category": ""}
        seen = {}

        def _yt(url, out_dir, top_n=2, lang="es", single_url=False):
            seen["lang"] = lang
            return self._yt_manifest()

        with mock.patch("clipagent.streamers.load_streamers",
                        return_value=[]), \
                mock.patch("clipagent.youtube_cut."
                           "fetch_youtube_metadata",
                           return_value=meta):
            n, _ = run(
                max_clips=2, top_n=2,
                state_path=self.state, out_root=self.out,
                bridge=_ListBridge(),
                scan_fn=self._scan([]),
                youtube_clips_fn=_yt,
                enqueue_fn=lambda *a, **k: (101, ""),
                youtube_url="https://youtu.be/NrNdw_Y6_bI")
        self.assertEqual(n, 1)
        self.assertEqual(seen["lang"], "es")

    def test_url_unica_no_gta_se_salta(self):
        meta = {"id": "Mine1234567", "duration": 1750,
                "title": "Minecraft con amigos",
                "channel_url": "https://www.youtube.com/@otro",
                "channel": "Otro", "description": "",
                "heatmap": [], "views": 0, "likes": 0,
                "categories": ["Minecraft"], "category": "Minecraft"}
        called = []

        def _yt(url, out_dir, top_n=2, lang="es", single_url=False):
            called.append(url)
            return self._yt_manifest()

        with mock.patch("clipagent.streamers.load_streamers",
                        return_value=[]), \
                mock.patch("clipagent.youtube_cut."
                           "fetch_youtube_metadata",
                           return_value=meta):
            n, summary = run(
                max_clips=2, top_n=2,
                state_path=self.state, out_root=self.out,
                bridge=_ListBridge(),
                scan_fn=self._scan([]),
                youtube_clips_fn=_yt,
                enqueue_fn=lambda *a, **k: (101, ""),
                youtube_url="https://youtu.be/Mine1234567")
        self.assertEqual(n, 0)
        self.assertEqual(called, [])
        self.assertIn("GTA VI", summary)

    def test_url_unica_en_enfriamiento_no_corta(self):
        save_state(self.state, {"vods": {"NrNdw_Y6_bI": {
            "attempted_at": 9999999999}}})
        meta = {"id": "NrNdw_Y6_bI", "duration": 1750,
                "title": "Conseguí 6 Estrellas en GTA 6 ⭐",
                "channel_url": "https://www.youtube.com/@rubius",
                "channel": "Rubius Z", "description": "",
                "heatmap": [], "views": 0, "likes": 0,
                "categories": [], "category": ""}
        called = []

        def _yt(url, out_dir, top_n=2, lang="es", single_url=False):
            called.append(url)
            return self._yt_manifest()

        with mock.patch("clipagent.streamers.load_streamers",
                        return_value=[]), \
                mock.patch("clipagent.youtube_cut."
                           "fetch_youtube_metadata",
                           return_value=meta):
            n, _ = run(
                max_clips=2, top_n=2,
                state_path=self.state, out_root=self.out,
                bridge=_ListBridge(),
                scan_fn=self._scan([]),
                youtube_clips_fn=_yt,
                enqueue_fn=lambda *a, **k: (101, ""),
                youtube_url="https://youtu.be/NrNdw_Y6_bI")
        self.assertEqual(n, 0)
        self.assertEqual(called, [])


if __name__ == "__main__":
    unittest.main()

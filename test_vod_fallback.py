#!/usr/bin/env python3
"""Pruebas del fallback VOD del cerebro (2026-11-19).

Sin ES en vivo en GTA VI, el cerebro cambia solo a modo VOD y corta
de los VOD más recientes (Rubius primero, después IlloJuan) con los
mismos filtros de siempre. Los clips van a la Cola del panel con el
MP4, SIN publicar: este módulo no tiene ninguna llamada a publicar.

- es_live_gta6: solo ES en vivo en GTA VI dispara "hay directos".
- fallback_order: Rubius, IlloJuan, resto ES en orden de yaml.
- clip_passes_gates: viralidad >= 50, SEO >= 90 en las 4
  plataformas, 40-60 s, juego GTA VI; falla cerrado sin datos.
- already_enqueued: dedup contra el panel por (VOD, inicio).
- burn_and_enqueue: subtítulos completos o no se encola; nunca
  llama a publicar.
- run: sin ES en vivo -> modo VOD; con ES en vivo -> no hace nada.
"""

import os
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from clipagent.vod_fallback import (  # noqa: E402
    MAX_DUR_S,
    MIN_DUR_S,
    already_enqueued,
    burn_and_enqueue,
    clip_passes_gates,
    es_live_gta6,
    fallback_order,
    load_state,
    panel_score,
    run,
    save_state,
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
        self._next_id = 100

    def list_clips(self):
        return list(self.clips)

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


class TestFallbackOrder(unittest.TestCase):
    def test_rubius_primero_illojuan_despues(self):
        names = [e["name"] for e in fallback_order(STREAMERS)]
        self.assertEqual(names, ["rubius", "illojuan", "auronplay"])

    def test_deshabilitado_fuera(self):
        names = [e["name"] for e in fallback_order(STREAMERS)]
        self.assertNotIn("xokas", names)

    def test_only_restringe(self):
        names = [e["name"] for e in fallback_order(
            STREAMERS, only=["auronplay"])]
        self.assertEqual(names, ["auronplay"])


class TestClipPassesGates(unittest.TestCase):
    def test_pasa(self):
        ok, reasons = clip_passes_gates(_clip(), "2932415021")
        self.assertTrue(ok, reasons)

    def test_viralidad_baja_no_pasa(self):
        c = _clip(viral_score={"total": 49.9, "chat_multiple": 1.5})
        ok, reasons = clip_passes_gates(c, "2932415021")
        self.assertFalse(ok)
        self.assertTrue(any("viralidad" in r for r in reasons))

    def test_seo_bajo_en_una_plataforma_no_pasa(self):
        seo = _seo(94)
        seo["tiktok"] = {"score": 89, "status": "ok",
                         "can_publish": False}
        ok, _ = clip_passes_gates(_clip(seo_scores=seo), "2932415021")
        self.assertFalse(ok)

    def test_sin_viral_score_falla_cerrado(self):
        ok, _ = clip_passes_gates(_clip(viral_score=None), "2932415021")
        self.assertFalse(ok)

    def test_sin_seo_en_una_plataforma_falla_cerrado(self):
        seo = _seo(94)
        del seo["instagram_reels"]
        ok, _ = clip_passes_gates(_clip(seo_scores=seo), "2932415021")
        self.assertFalse(ok)

    def test_duracion_30_no_pasa(self):
        ok, _ = clip_passes_gates(_clip(duration=30), "2932415021")
        self.assertFalse(ok)

    def test_duracion_61_no_pasa(self):
        ok, _ = clip_passes_gates(_clip(duration=61), "2932415021")
        self.assertFalse(ok)

    def test_limites_40_y_60_pasan(self):
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

    def test_encola_sin_publicar(self):
        self._patch_subtitles()
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
        self.assertEqual(payload["start_sec"], 3200)
        self.assertEqual(payload["end_sec"], 3248)
        self.assertEqual(payload["source_id"], 2932415021)
        self.assertEqual(len(bridge.uploaded), 1)
        # NUNCA publica: ni publish_clip ni publish loop.
        self.assertEqual(bridge.published, [])

    def test_sin_voz_no_se_encola(self):
        self._patch_subtitles(publish_blocked=True, no_speech=True)
        bridge = FakeBridge()
        panel_id, why = burn_and_enqueue(
            bridge, "rubius", "2932415021", self.tmp.name, _clip())
        self.assertIsNone(panel_id)
        self.assertIn("sin voz", why)
        self.assertEqual(bridge.created, [])
        self.assertEqual(bridge.uploaded, [])


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

        def _enqueue(bridge, streamer, vod_id, out_dir, clip):
            enqueued.append((streamer, vod_id, clip["peak"]))
            return 101, ""

        bridge = FakeBridge()
        n, _ = run(
            max_vods=1, max_clips=2, top_n=2,
            state_path=self.state, out_root=self.out, bridge=bridge,
            scan_fn=self._scan([]),
            latest_vod_fn=lambda entry: vod,
            auto_clips_fn=lambda url, out_dir, top_n=2: manifest,
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
            auto_clips_fn=lambda url, out_dir, top_n=2: manifest,
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

        def _enqueue(bridge, streamer, vod_id, out_dir, clip):
            enqueued.append((streamer, vod_id, clip["peak"]))
            return 101, ""

        n, _ = run(
            max_vods=1, max_clips=2, top_n=2,
            state_path=self.state, out_root=self.out,
            bridge=FakeBridge(),
            scan_fn=self._scan([]),
            latest_vod_fn=lambda entry: vod,
            auto_clips_fn=lambda url, out_dir, top_n=2: manifest,
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


if __name__ == "__main__":
    unittest.main()

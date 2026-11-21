#!/usr/bin/env python3
"""Pruebas de 6.3: aprobación automática por política.
"""
import copy
import json
import os
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from clipagent import policy
from clipagent.config import DEFAULT_CONFIG
from clipagent.publish import PublishQueue, publish_one


def base_cfg(**over):
    cfg = copy.deepcopy(DEFAULT_CONFIG)
    cfg["autopublish"]["mode"] = "full"
    cfg.update(over)
    return cfg


def clip(virality=80, seo=95, audio_decision=None, multiple=8.0):
    c = {"peak": 100, "start": 80, "file": "clips/clip_01.mp4",
         # Categoría verificada (capítulo GTA VI del VOD) — regla
         # del usuario 2026-11-18: sin categoría GTA VI no se publica.
         "category": "Grand Theft Auto VI",
         "viral_score": {"total": virality, "band": "prioritario"},
         "seo_scores": {"youtube_shorts": {"score": seo},
                        "tiktok": {"score": seo}},
         "viral": {"viral_multiple": multiple},
         "title_es": "GTA 6: la jugada", "desc_es": "El chat explotó",
         "hashtags_es": "#GTA6", "visible": True}
    if audio_decision:
        c["audio"] = {"decision": audio_decision, "reason": "plano"}
    return c


class TestEvaluate(unittest.TestCase):
    def test_publica_si_pasa_todo(self):
        r = policy.evaluate(clip(), "youtube_shorts",
                            cfg=base_cfg(), game="GTA 6", vod_id="v1")
        self.assertEqual(r["decision"], "publicar")
        self.assertEqual(r["band"], "prioritario")
        self.assertIn("priority", r)
        self.assertIsNotNone(r["priority"])

    def test_viralidad_baja_descarta(self):
        r = policy.evaluate(clip(virality=49), "youtube_shorts",
                            cfg=base_cfg())
        self.assertEqual(r["decision"], "descartado")
        self.assertIn("49", r["reason"])

    def test_seo_89_sin_llm_retiene(self):
        r = policy.evaluate(clip(seo=89), "youtube_shorts",
                            cfg=base_cfg())
        self.assertEqual(r["decision"], "retenido")
        self.assertIn("89", r["reason"])

    def test_seo_69_bloqueo_duro(self):
        r = policy.evaluate(clip(seo=69), "youtube_shorts",
                            cfg=base_cfg())
        self.assertEqual(r["decision"], "descartado")
        self.assertIn("70", r["reason"])

    def test_override_permite_seo_89(self):
        r = policy.evaluate(clip(seo=89), "youtube_shorts",
                            cfg=base_cfg(), seo_override=True)
        self.assertEqual(r["decision"], "publicar")

    def test_rojos_retienen(self):
        r = policy.evaluate(clip(), "youtube_shorts", cfg=base_cfg(),
                            red_flags=["juego_no_gta6"])
        self.assertEqual(r["decision"], "retenido")
        self.assertIn("juego_no_gta6", r["reason"])

    def test_audio_sospechoso_retiene(self):
        r = policy.evaluate(clip(audio_decision="sospechar"),
                            "youtube_shorts", cfg=base_cfg())
        self.assertEqual(r["decision"], "retenido")
        self.assertIn("sospechoso", r["reason"])

    def test_juego_no_permitido_retiene(self):
        r = policy.evaluate(clip(), "youtube_shorts", cfg=base_cfg(),
                            game="Fortnite")
        self.assertEqual(r["decision"], "retenido")
        self.assertIn("no permitido", r["reason"])

    def test_sin_juego_no_es_rojo(self):
        r = policy.evaluate(clip(), "youtube_shorts", cfg=base_cfg(),
                            game=None)
        self.assertEqual(r["decision"], "publicar")

    def test_categoria_just_chatting_descarta(self):
        # Regla 2026-11-18: solo vale GTA VI. Just Chatting no es
        # un juego ni GTA VI: el clip se descarta, aunque el chat
        # explote.
        c = clip()
        c["category"] = "Just Chatting"
        r = policy.evaluate(c, "youtube_shorts", cfg=base_cfg())
        self.assertEqual(r["decision"], "descartado")
        self.assertIn("GTA VI", r["reason"])

    def test_categoria_otro_juego_descarta(self):
        c = clip()
        c["category"] = "Fortnite"
        r = policy.evaluate(c, "youtube_shorts", cfg=base_cfg())
        self.assertEqual(r["decision"], "descartado")
        self.assertIn("GTA VI", r["reason"])

    def test_sin_categoria_verificada_no_retiene_en_publicacion(self):
        # Sin categoría NO se retiene en publicación (commit 18d3a30):
        # el gate de categoría vive en el corte (detect_peaks descarta
        # lo que no es GTA VI y etiqueta el resto) y en el panel
        # (POST /api/clips exige game GTA VI + publishGate). Retener
        # aquí bloqueaba clips heredados/en cola sin categoría en el
        # manifiesto (p. ej. p3220, verificado GTA VI por ficha/VOD)
        # antes de llegar a sus propias puertas. La categoría
        # CONOCIDA que no es GTA VI sí descarta (ver test anterior).
        c = clip()
        del c["category"]
        r = policy.evaluate(c, "youtube_shorts", cfg=base_cfg())
        self.assertEqual(r["decision"], "publicar")
        self.assertTrue(any("categor" in check for check in r["checks"]))

    def test_categoria_gta_vi_alias_valen(self):
        for cat in ("GTA VI", "GTA 6", "gta6",
                    "Grand Theft Auto VI", "Grand Theft Auto 6"):
            c = clip()
            c["category"] = cat
            r = policy.evaluate(c, "youtube_shorts", cfg=base_cfg())
            self.assertEqual(r["decision"], "publicar", cat)

    def test_banda_minima(self):
        cfg = base_cfg()
        cfg["auto_approve"]["min_band"] = "urgente"
        r = policy.evaluate(clip(virality=80), "youtube_shorts", cfg=cfg)
        self.assertEqual(r["decision"], "retenido")
        self.assertIn("urgente", r["reason"])

    def test_auto_approve_apagado(self):
        cfg = base_cfg()
        cfg["auto_approve"]["enabled"] = False
        r = policy.evaluate(clip(), "youtube_shorts", cfg=cfg)
        self.assertEqual(r["decision"], "retenido")

    def test_umbrales_desde_config(self):
        cfg = base_cfg()
        cfg["scoring"] = {"seo_gate": 80, "viral_min": 40}
        r = policy.evaluate(clip(virality=45, seo=85), "youtube_shorts",
                            cfg=cfg)
        self.assertEqual(r["decision"], "publicar")

    def test_duplicado_mismo_momento_mismo_dia(self):
        with tempfile.TemporaryDirectory() as tmp:
            q = PublishQueue(os.path.join(tmp, "pq.json"))
            item = q.enqueue("/tmp/x.mp4", "youtube_shorts",
                             moment_key=["v1", 8], vod_id="v1")
            q.mark_published(item, "abc")
            r = policy.evaluate(clip(), "youtube_shorts", queue=q,
                                cfg=base_cfg(), vod_id="v1")
            self.assertEqual(r["decision"], "retenido")
            self.assertIn("mismo momento", r["reason"])

    def test_modo_gradual_bloquea_en_fase_1(self):
        from counter import load_state
        cfg = base_cfg()
        cfg["autopublish"]["mode"] = "gradual"
        state = load_state()
        r = policy.evaluate(clip(), "youtube_shorts", cfg=cfg,
                            mode="gradual", counter_state=state)
        self.assertEqual(r["decision"], "retenido")
        self.assertIn("gradual", r["reason"])


class TestPublishOne(unittest.TestCase):
    def _setup(self, tmp, virality=80, seo=95, platform="youtube_shorts"):
        run_dir = os.path.join(tmp, "runs", "v1")
        clips_dir = os.path.join(run_dir, "clips")
        os.makedirs(clips_dir, exist_ok=True)
        clip_path = os.path.join(clips_dir, "clip_01.mp4")
        with open(clip_path, "wb") as f:
            f.write(b"fake mp4")
        manifest = {"vod_id": "v1", "seo_overrides": [],
                    "clips": [clip(virality, seo)]}
        manifest["clips"][0]["file"] = "clips/clip_01.mp4"
        with open(os.path.join(run_dir, "manifest.json"), "w",
                  encoding="utf-8") as f:
            json.dump(manifest, f)
        q = PublishQueue(os.path.join(tmp, "pq.json"))
        item = q.enqueue(clip_path, platform, vod_id="v1",
                         moment_key=["v1", 8])
        return q, item, clip_path

    def test_retenido_por_seo(self):
        with tempfile.TemporaryDirectory() as tmp:
            q, item, _ = self._setup(tmp, seo=85)
            with mock.patch("clipagent.publish._now_chile") as now:
                import datetime
                now.return_value = datetime.datetime(
                    2026, 11, 17, 20, 0,
                    tzinfo=datetime.timezone(
                        datetime.timedelta(hours=-3)))
                r = publish_one(q, item, cfg=base_cfg(), live=False)
            self.assertEqual(r["action"], "retained")
            self.assertEqual(q._find(item["clip"],
                                     "youtube_shorts")["state"], "retained")

    def test_descartado_por_viralidad(self):
        with tempfile.TemporaryDirectory() as tmp:
            q, item, _ = self._setup(tmp, virality=30)
            r = publish_one(q, item, cfg=base_cfg(), live=False)
            self.assertEqual(r["action"], "discarded")

    def test_publica_youtube_con_mock(self):
        with tempfile.TemporaryDirectory() as tmp:
            q, item, clip_path = self._setup(tmp)
            fake_pub = mock.Mock()
            fake_pub.publish.return_value = {
                "ok": True, "video_id": "xyz",
                "url": "https://www.youtube.com/shorts/xyz"}
            with mock.patch("clipagent.publish._now_chile") as now, \
                    mock.patch("clipagent.publish.get_publisher",
                               return_value=fake_pub), \
                    mock.patch("clipagent.publish.check_limits",
                               return_value=(True, "ok")):
                import datetime
                now.return_value = datetime.datetime(
                    2026, 11, 17, 20, 0,
                    tzinfo=datetime.timezone(
                        datetime.timedelta(hours=-3)))
                r = publish_one(q, item, cfg=base_cfg(), live=True)
            self.assertEqual(r["action"], "published")
            self.assertEqual(q._find(clip_path,
                                     "youtube_shorts")["state"], "published")

    def test_tiktok_sin_directo_cae_a_draft(self):
        with tempfile.TemporaryDirectory() as tmp:
            q, item, clip_path = self._setup(tmp, platform="tiktok")
            with mock.patch("clipagent.publish.check_limits",
                           return_value=(True, "ok")):
                r = publish_one(q, item, cfg=base_cfg(), live=True)
            self.assertEqual(r["action"], "draft")
            self.assertEqual(q._find(clip_path, "tiktok")["state"],
                             "draft")


class TestWorkerEnqueue(unittest.TestCase):
    def test_encola_por_plataforma_del_streamer(self):
        from worker import _enqueue_clips_for_publish
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = os.path.join(tmp, "runs", "v1")
            os.makedirs(os.path.join(run_dir, "clips"), exist_ok=True)
            clip_path = os.path.join(run_dir, "clips", "c1.mp4")
            with open(clip_path, "wb") as f:
                f.write(b"x")
            manifest = {"vod_id": "v1",
                        "clips": [{"file": "clips/c1.mp4", "start": 80,
                                   "moment_card": {"game": "GTA 6"}}]}
            job = {"out_dir": run_dir, "streamer": "illojuan"}
            with mock.patch("clipagent.publish.QUEUE_STATE_FILE",
                           os.path.join(tmp, "pq.json")):
                n = _enqueue_clips_for_publish(job, manifest, [])
            self.assertEqual(n, 4)  # illojuan publica en las 4


class TestNoSpeech(unittest.TestCase):
    """Regla 2026-11-19 18:41: sin voz + pasa puertas (45/80) ->
    publicable sin subtítulos; sin voz + bajo puertas -> bloqueado;
    nunca se inventa texto."""

    def _cfg(self, **subs):
        cfg = {"scoring": {"seo_gate": 80, "viral_min": 45},
               "subtitles": {"publish_no_speech_if_passes_gates": True}}
        cfg["subtitles"].update(subs)
        return cfg

    def test_pasa_puertas_acepta_numeros_y_mapas(self):
        cfg = self._cfg()
        # Camino en vivo: SEO por plataforma como número.
        self.assertTrue(policy.passes_publish_gates(
            53.8, {"youtube_shorts": 94, "tiktok": 90}, cfg))
        # Camino VOD: SEO por plataforma como mapa con "score".
        self.assertTrue(policy.passes_publish_gates(
            53.8, {"youtube_shorts": {"score": 94}}, cfg))
        self.assertFalse(policy.passes_publish_gates(
            53.8, {"youtube_shorts": 79, "tiktok": 70}, cfg))
        self.assertFalse(policy.passes_publish_gates(
            44.9, {"youtube_shorts": 94}, cfg))
        self.assertFalse(policy.passes_publish_gates(53.8, {}, cfg))
        self.assertFalse(policy.passes_publish_gates(None, {}, cfg))

    def test_sin_voz_con_puertas_no_bloquea(self):
        self.assertFalse(policy.no_speech_blocked(
            True, 53.8, {"youtube_shorts": {"score": 94}},
            self._cfg()))

    def test_sin_voz_bajo_viralidad_bloquea(self):
        self.assertTrue(policy.no_speech_blocked(
            True, 30.0, {"youtube_shorts": {"score": 94}},
            self._cfg()))

    def test_sin_voz_bajo_seo_bloquea(self):
        self.assertTrue(policy.no_speech_blocked(
            True, 53.8, {"youtube_shorts": {"score": 79}},
            self._cfg()))

    def test_sin_voz_sin_scores_bloquea_falla_cerrado(self):
        self.assertTrue(policy.no_speech_blocked(
            True, None, {}, self._cfg()))

    def test_con_voz_nunca_bloquea_por_esto(self):
        self.assertFalse(policy.no_speech_blocked(
            False, 10.0, {"youtube_shorts": {"score": 10}},
            self._cfg()))

    def test_excepcion_apagada_vuelve_a_bloquear(self):
        cfg = self._cfg(publish_no_speech_if_passes_gates=False)
        self.assertTrue(policy.no_speech_blocked(
            True, 53.8, {"youtube_shorts": {"score": 94}}, cfg))


if __name__ == "__main__":
    unittest.main(verbosity=2)

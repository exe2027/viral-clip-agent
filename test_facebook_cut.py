#!/usr/bin/env python3
"""Pruebas de la entrada de Facebook al programa de clips
(SPEC-2026-11-25-entrada-facebook.md, v1).

Sin red ni panel: yt-dlp, transcripción, RMS, segmento, render
y validación van mockeados; el SEO y la viralidad son reales
(motor local y fórmula intacta).

- is_facebook_url: facebook.com/reel, /share/r/, /watch/?v=,
  m.facebook.com y fb.watch con cualquier ruta sí; youtube.com,
  twitch.tv y rutas de Facebook que no son reel no.
- fetch_facebook_metadata: un solo yt-dlp -J, reintento con
  stdout vacío y JSON inválido (3 intentos); el id sale del -J.
- download_facebook_segment: argv exacto con FACEBOOK_FORMAT
  literal (hd preferido, fallback AV1 en el selector).
- facebook_clearly_not_gta6: #gta6 -> verificado; hashtags sin
  GTA -> se salta; sin hashtags -> decide la transcripción.
- build_facebook_package: game solo si verificado; descripción
  del reel o extracto real + línea factual. MEDIDO 2026-11-25:
  el paquete suggest-only real puntúa SEO 51-54 (por debajo de
  80): el clip real NO pasa clip_passes_gates por SEO y la
  calibración vuelve (spec §3.1). Por eso el test de puertas +
  encolado inyecta titles_fn con SEO 85 (el inyectable existe
  para eso); el test del paquete real registra los valores
  medidos sin afirmar que pasen.
- run_facebook_clips con yt-dlp mockeado: contrato completo del
  manifiesto (viral.best_title no vacío, viral.viral_multiple,
  viral_breakdown con audio_multiple, hype_score, score_kind
  "facebook_audio", peak, source_key "fb:...", vod_id, vod_layout,
  n, title_es/title_en, segmentos en segments/), manifiesto en
  disco; bandas 14 s / 18,85 s / 70 s; puerta de pico con buckets
  de 1 s (el test que caza el bloqueador: 2 buckets de 10 s dan
  1,07x y no pasan; los mismos segundos con buckets de 1 s dan
  2,51x y pasan).
- Umbral 53 al encolar (URL_CUT_PRIORITY_MIN): Facebook 50 no se
  encola, 56 sí; el renombre no deja referencias viejas.
- Orquestación: run(facebook_url=...) + CLI --facebook-url +
  _run_single_facebook (enfriamiento, pre-chequeo GTA, dry-run,
  autor sin entrada -> (0, motivo) + evento clip_descartado).
- source_id y clave de estado: source_id == id numérico del reel;
  el enfriamiento se clavea por str(vod_id), no por source_key.
"""

import json
import os
import sys
import tempfile
import types
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import clipagent.vod_fallback as vod_fallback  # noqa: E402
from clipagent import llm  # noqa: E402
from clipagent.detect import score_clip  # noqa: E402
from clipagent.facebook_cut import (  # noqa: E402
    FACEBOOK_FORMAT,
    FacebookError,
    audio_multiple,
    audio_peak_gate,
    build_facebook_package,
    download_facebook_segment,
    extract_audio_from_video,
    facebook_clearly_not_gta6,
    fetch_facebook_metadata,
    is_facebook_url,
    rms_buckets_10s,
    run_facebook_clips,
    score_facebook_clip,
    _resolve_single_facebook_influencer,
)
from clipagent.vod_fallback import (  # noqa: E402
    URL_CUT_PRIORITY_MIN,
    already_enqueued,
    burn_and_enqueue,
    clip_passes_gates,
    load_state,
    main,
    mark_vod_attempted,
    panel_score,
    run,
    save_state,
    vod_on_cooldown,
)

REEL_ID = "1918676049105232"
REEL_URL = "https://www.facebook.com/share/r/18cMsFx95G/"
UPLOADER_ID = "61559354617566"
PROFILE_URL = ("https://facebook.com/people/Clips-de-Noni/"
               "61559354617566/")


def _seo(score):
    return {p: {"score": score, "status": "ok", "can_publish": True}
            for p in ("youtube_shorts", "tiktok", "instagram_reels",
                      "facebook_reels")}


def _words(text, start=0.0):
    out = []
    t = start
    for word in text.split():
        out.append({"word": word, "start": round(t, 2),
                    "end": round(t + 0.3, 2)})
        t += 0.4
    return out


def _transcript(windows):
    segments = []
    for start, text in windows:
        segments.append({
            "start": float(start), "end": float(start) + 28.0,
            "text": text, "words": _words(text, start)})
    return {"segments": segments, "language": "es"}


def _segment_transcript(n_words=60):
    """Transcripción del segmento: 60 palabras reales (2026-11-26,
    spec títulos-verdad): antes eran 60 «palabraN» genéricas, lo
    que valía cuando el título no leía la transcripción; ahora el
    paquete Gemini se ancla al momento (la jugada brutal de Noni)
    y el conteo (60) no cambia: el score 54,4 queda intacto."""
    text = ("jaja qué jugada brutal increíble vamos no lo puedo "
            "creer en gta 6 el avión se queda sin combustible y se "
            "estrella entre edificios mira el humo sale del motor "
            "y cae en picado sobre la ciudad vamos vamos vamos no "
            "me lo creo esto es de cine total qué locura de "
            "aterrizaje chicos madre mía madre mía madre mía")
    return {"segments": [{"start": 0.0, "end": 17.0, "text": text,
                          "words": _words(text, 0.0)}]}


class FakeLLM(llm.LLMClient):
    """LLM de pruebas: devuelve el paquete que se le pasa."""

    def __init__(self, payload):
        self.payload = payload

    def available(self):
        return True

    def generate(self, prompt):
        return json.dumps(self.payload)


# Paquete Gemini falso para el camino VOD (spec títulos-verdad):
# título anclado a la transcripción del segmento, descripción con
# el juego y cierre con pregunta, 4 hashtags de contenido +
# #Shorts, miniatura con sentido. Sin él el paquete queda retenido
# (Gemini -> retención).
FB_PACKAGE = {
    "title": "GTA 6: Noni y su jugada brutal increíble",
    "description": "Noni hace una jugada brutal increíble en GTA 6 "
                   "con el avión sin combustible. ¿Viste cómo "
                   "termina?",
    "hashtags": ["#GTA6", "#Noni", "#JugadaBrutal", "#Avion",
                 "#Shorts"],
    "thumbnail_text": "LA JUGADA BRUTAL",
}

# Paquete para el reel de 70 s (test_reel_70s): el múltiple de
# audio real de ese clip es 3.31 (< 5), así que el título NO
# puede usar superlativos con condición multiple>=5 («brutal»,
# «increíble»): la verificación los rechaza y el clip queda
# retenido. Mismo momento, sin superlativos.
FB_PACKAGE_70S = {
    "title": "GTA 6: Noni y su jugada en el avión sin combustible",
    "description": "Noni hace una jugada en GTA 6 con el avión sin "
                   "combustible. ¿Viste cómo termina?",
    "hashtags": ["#GTA6", "#Noni", "#Jugada", "#Avion",
                 "#Shorts"],
    "thumbnail_text": "LA JUGADA DEL AVIÓN",
}


def _meta(**over):
    base = {
        "id": REEL_ID,
        "duration": 18,
        "uploader": "Clips de Noni",
        "uploader_id": UPLOADER_ID,
        "description": "#gta6 #noni #noniclips #clipsdenoni #gta",
        "views": 15816,
    }
    base.update(over)
    return base


def _rms_short_1s():
    """RMS del reel de 18 s: -30 dBFS con 3 s a -15 dBFS en
    15-17 s. Buckets de 1 s: pico 0,178 / mediana 0,032 = 5,62x."""
    return [(s, -15.0 if s >= 15 else -30.0) for s in range(18)]


def _rms_segment():
    """RMS del segmento: 9 s a -40 y 9 s a -15 (std 12,5 dB,
    rango 25 dB -> audio 9,375 con 60 palabras -> total 54,4)."""
    return [(s, -40.0 if s < 9 else -15.0) for s in range(18)]


def _rms_flat_18():
    return [(s, -30.0) for s in range(18)]


def _transcript_short():
    text = "jaja qué jugada brutal increíble vamos no lo puedo creer"
    return {"segments": [{"start": 0.0, "end": 17.0, "text": text,
                          "words": _words(text, 0.0)}]}


def _fb_clip(total=54.4, seo_score=85, mult=5.62, **over):
    """Clip de Facebook que pasa las puertas (para umbral,
    dedup y orquestación)."""
    base = {
        "n": 1, "file": "clips/clip_01.mp4", "peak": 15.0,
        "start": 0, "duration": 18,
        "viral": {"best_title": "Noni intenta un vuelo "
                                "cinematográfico en GTA 6",
                  "game": "GTA VI",
                  "description": "#gta6 #noni #noniclips "
                                 "#clipsdenoni #gta",
                  "tags": ["#GTA6"],
                  "thumbnail_text": "¡QUÉ JUGADA!",
                  "viral_multiple": mult},
        "viral_score": {"total": total, "filter_passed": False},
        "viral_breakdown": {"audio_multiple": mult},
        "seo_scores": _seo(seo_score),
        "moment_card": {"game": "GTA VI"},
        "source": "facebook",
        "source_key": f"fb:{REEL_ID}",
        "vod_id": REEL_ID,
        "score_kind": "facebook_audio",
    }
    base.update(over)
    return base


class FakeBridge:
    """Puente falso: registra POST / PATCH / subida / preview /
    eventos (como test_youtube_cut.FakeBridge + report_event)."""

    def __init__(self, clips=None, influencers=None):
        self.clips = list(clips or [])
        self.influencers = list(influencers or [])
        self.created = []
        self.uploaded = []
        self.patched = []
        self.previews = []
        self.events = []
        self._next_id = 100

    def list_clips(self):
        return list(self.clips)

    def list_influencers(self):
        return list(self.influencers)

    def create_clip(self, payload):
        self.created.append(payload)
        self._next_id += 1
        clip = {"id": self._next_id}
        self.clips.append({
            "source_id": payload.get("source_id"),
            "source_key": payload.get("source_key", ""),
            "start_sec": payload.get("start_sec"),
            "title": payload.get("title"),
            "influencer_id": 7,
            "created_at": 1795532400000,
            "platforms": list(payload.get("platforms") or [])})
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

    def report_event(self, kind, message):
        self.events.append((kind, message))
        return True


def _patch_burn():
    """Mocks de subtitle_clip + validate_final_clip para
    burn_and_enqueue (como en test_vod_fallback)."""
    sub = mock.patch(
        "clipagent.workers.subtitle_clip",
        return_value={"publish_blocked": False, "no_speech": False,
                      "srt": "", "burn": "", "transcript": None})
    sub.start()
    verdict = {"decision": "aprobado", "pasa": True,
               "hard_pasa": True, "needs_rerender": False,
               "rerender_reason": "", "hard_failures": [],
               "motivo": "", "resumen": "aprobado",
               "fallos": [], "scores": {}, "frames_n": 0,
               "model": "", "transcript_words": 0,
               "observe_only": True,
               "face": {"pasa": True}, "template": {"pasa": True},
               "gemini": {"decision": "aprobado"}}
    review = mock.patch(
        "clipagent.final_review.validate_final_clip",
        return_value=verdict)
    review.start()
    return sub, review


def _titles_passing(**kw):
    """titles_fn que usa el paquete real pero con SEO 85: el
    paquete suggest-only real mide 51-54 (ver
    TestBuildFacebookPackage) y no pasaría la puerta SEO >= 80;
    la calibración de ese SEO es trabajo de aceptación, no de
    este test de puertas/encolado."""
    pkg = build_facebook_package(**kw)
    pkg["seo_scores"] = _seo(85)
    return pkg


class TestIsFacebookUrl(unittest.TestCase):
    def test_reel_share_watch_y_fbwatch_si(self):
        for url in (
                "https://www.facebook.com/reel/1918676049105232",
                "https://facebook.com/reel/1918676049105232",
                "https://m.facebook.com/reel/1918676049105232",
                "https://www.facebook.com/share/r/18cMsFx95G/",
                "https://www.facebook.com/watch/?v=1918676049105232",
                "https://fb.watch/abc123/",
                "https://fb.watch/cualquier/ruta"):
            self.assertTrue(is_facebook_url(url), url)

    def test_otros_no(self):
        for url in ("https://www.youtube.com/watch?v=NrNdw_Y6_bI",
                    "https://www.twitch.tv/videos/2932415021",
                    "https://www.facebook.com/noni/posts/123",
                    "https://www.facebook.com/watch/",
                    "https://www.facebook.com/share/p/xyz",
                    "https://example.com/reel/123",
                    "", None):
            self.assertFalse(is_facebook_url(url), url)


class TestFetchFacebookMetadata(unittest.TestCase):
    def _payload(self, **over):
        data = {"id": REEL_ID, "duration": 18.853,
                "uploader": "Clips de Noni",
                "uploader_id": UPLOADER_ID,
                "description": "#gta6 #noni",
                "view_count": 15816, "like_count": None}
        data.update(over)
        return json.dumps(data)

    def _runner(self, responses):
        calls = []

        def _run(cmd):
            calls.append(cmd)
            resp = responses[min(len(calls) - 1, len(responses) - 1)]
            return types.SimpleNamespace(
                returncode=resp[0], stdout=resp[1], stderr="")
        _run.calls = calls
        return _run

    def test_stdout_vacio_y_json_invalido_reintentan(self):
        # El caso observado en §1: salida vacía y luego JSON
        # inválido; el tercer intento devuelve el JSON con id.
        runner = self._runner([
            (0, ""), (0, "no es json"), (0, self._payload())])
        meta = fetch_facebook_metadata(
            REEL_URL, runner=runner, sleep=lambda s: None)
        self.assertEqual(meta["id"], REEL_ID)
        self.assertEqual(meta["duration"], 18)
        self.assertEqual(meta["uploader"], "Clips de Noni")
        self.assertEqual(meta["uploader_id"], UPLOADER_ID)
        self.assertEqual(meta["description"], "#gta6 #noni")
        self.assertEqual(meta["views"], 15816)
        self.assertEqual(len(runner.calls), 3)

    def test_stdout_siempre_vacio_falla_tras_3(self):
        runner = self._runner([(0, "")])
        with self.assertRaises(FacebookError):
            fetch_facebook_metadata(
                REEL_URL, runner=runner, sleep=lambda s: None)
        self.assertEqual(len(runner.calls), 3)

    def test_json_sin_id_falla_tras_3(self):
        runner = self._runner([(0, json.dumps({"duration": 18}))])
        with self.assertRaises(FacebookError):
            fetch_facebook_metadata(
                REEL_URL, runner=runner, sleep=lambda s: None)
        self.assertEqual(len(runner.calls), 3)

    def test_codigo_distinto_de_cero_falla_tras_3(self):
        runner = self._runner([(1, ""), (1, ""), (1, "")])
        with self.assertRaises(FacebookError):
            fetch_facebook_metadata(
                REEL_URL, runner=runner, sleep=lambda s: None)
        self.assertEqual(len(runner.calls), 3)

    def test_cmd_sin_no_check_certificate(self):
        runner = self._runner([(0, self._payload())])
        fetch_facebook_metadata(
            REEL_URL, runner=runner, sleep=lambda s: None)
        cmd = runner.calls[0]
        self.assertIn("--skip-download", cmd)
        self.assertIn("-J", cmd)
        self.assertIn(REEL_URL, cmd)
        self.assertNotIn("--no-check-certificate", cmd)
        self.assertNotIn("--no-check-certificates", cmd)


class TestDownloadFacebookSegment(unittest.TestCase):
    def test_argv_exacto_con_facebok_format(self):
        seen = {}

        def _run(cmd):
            seen["cmd"] = cmd
            with open(seen["out"], "wb") as f:
                f.write(b"fake-mp4")
            return types.SimpleNamespace(
                returncode=0, stdout="", stderr="")

        with tempfile.TemporaryDirectory() as tmp:
            out = os.path.join(tmp, "seg.mp4")
            seen["out"] = out
            got = download_facebook_segment(
                REEL_URL, 10, 30, out, runner=_run,
                sleep=lambda s: None)
        self.assertEqual(got, out)
        cmd = seen["cmd"]
        # El selector combinado literal, hd preferido (primera
        # alternativa) con fallback AV1 (bv*+ba) en el mismo
        # selector.
        self.assertIn("-f", cmd)
        self.assertIn(FACEBOOK_FORMAT, cmd)
        self.assertEqual(FACEBOOK_FORMAT,
                         "hd/sd/bv*[height<=1080]+ba/b[height<=1080]/b")
        self.assertTrue(FACEBOOK_FORMAT.startswith("hd/"))
        self.assertIn("bv*[height<=1080]+ba", FACEBOOK_FORMAT)
        self.assertIn("--download-sections", cmd)
        self.assertIn("*00:00:10-00:00:30", cmd)
        self.assertIn(REEL_URL, cmd)
        self.assertNotIn("--no-check-certificate", cmd)

    def test_fallo_levanta_facebook_error(self):
        def _run(cmd):
            return types.SimpleNamespace(
                returncode=1, stdout="", stderr="ERROR: no se pudo")

        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(FacebookError):
                download_facebook_segment(
                    REEL_URL, 0, 18, os.path.join(tmp, "seg.mp4"),
                    runner=_run, sleep=lambda s: None)


class TestExtractAudioFromVideo(unittest.TestCase):
    def test_ffmpeg_vn_a_wav(self):
        seen = {}

        def _run(cmd):
            seen["cmd"] = cmd
            with open(seen["out"], "wb") as f:
                f.write(b"fake-wav")
            return types.SimpleNamespace(
                returncode=0, stdout="", stderr="")

        with tempfile.TemporaryDirectory() as tmp:
            out = os.path.join(tmp, "full_audio.wav")
            seen["out"] = out
            got = extract_audio_from_video(
                os.path.join(tmp, "seg.mp4"), out, runner=_run)
        self.assertEqual(got, out)
        self.assertEqual(seen["cmd"][0], "ffmpeg")
        self.assertIn("-vn", seen["cmd"])
        self.assertIn(out, seen["cmd"])

    def test_fallo_levanta_facebook_error(self):
        def _run(cmd):
            return types.SimpleNamespace(
                returncode=1, stdout="", stderr="ffmpeg: error")

        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(FacebookError):
                extract_audio_from_video(
                    os.path.join(tmp, "seg.mp4"),
                    os.path.join(tmp, "out.wav"), runner=_run)


class TestFacebookClearlyNotGta6(unittest.TestCase):
    def test_hashtag_gta6_verificado(self):
        self.assertIsNone(facebook_clearly_not_gta6(
            {"description": "#gta6 #noni #noniclips #clipsdenoni "
                            "#gta"}))
        self.assertIsNone(facebook_clearly_not_gta6(
            {"description": "#GTA6"}))

    def test_hashtags_sin_gta_se_salta(self):
        reason = facebook_clearly_not_gta6(
            {"description": "#cocina #recetas #noni"})
        self.assertIsNotNone(reason)
        self.assertIn("hashtags sin GTA", reason)

    def test_sin_hashtags_no_decide_por_aqui(self):
        # Sin hashtags la decisión pasa a la transcripción (el
        # cortador); el pre-chequeo no veta.
        self.assertIsNone(facebook_clearly_not_gta6(
            {"description": ""}))
        self.assertIsNone(facebook_clearly_not_gta6(
            {"description": "Noni juega GTA 6 en el aeropuerto"}))
        self.assertIsNone(facebook_clearly_not_gta6({}))


class TestResolveSingleFacebookInfluencer(unittest.TestCase):
    def _streamers(self):
        return [
            {"name": "noni", "platform": "facebook",
             "profile_url": PROFILE_URL, "language": "es",
             "enabled": True, "estado": "concedido"},
            {"name": "noni", "platform": "twitch",
             "channel_url": "https://www.twitch.tv/noni",
             "language": "es", "enabled": True,
             "estado": "concedido"},
        ]

    def test_match_por_uploader_id_en_profile_url(self):
        lang, name = _resolve_single_facebook_influencer(
            _meta(), self._streamers())
        self.assertEqual((lang, name), ("es", "noni"))

    def test_match_por_channel_url_tambien(self):
        streamers = [{"name": "noni", "platform": "facebook",
                      "channel_url": PROFILE_URL, "language": "es"}]
        lang, name = _resolve_single_facebook_influencer(
            _meta(), streamers)
        self.assertEqual((lang, name), ("es", "noni"))

    def test_normalizacion_clips_de_noni_a_noni(self):
        streamers = [{"name": "noni", "platform": "facebook",
                      "channel_url": "https://facebook.com/otra",
                      "language": "es"}]
        meta = _meta(uploader_id="999")
        lang, name = _resolve_single_facebook_influencer(
            meta, streamers)
        self.assertEqual((lang, name), ("es", "noni"))
        meta2 = _meta(uploader="Clips Noni", uploader_id="999")
        self.assertEqual(
            _resolve_single_facebook_influencer(meta2, streamers),
            ("es", "noni"))

    def test_sin_match_fail_closed(self):
        lang, name = _resolve_single_facebook_influencer(
            _meta(), [{"name": "noni", "platform": "twitch",
                       "channel_url": "https://www.twitch.tv/noni",
                       "language": "es"}])
        self.assertIsNone(lang)
        self.assertEqual(name, "Clips de Noni")
        lang, name = _resolve_single_facebook_influencer(_meta(), [])
        self.assertIsNone(lang)


class TestBuildFacebookPackage(unittest.TestCase):
    def test_verificado_game_solo_si_verificado(self):
        pkg = build_facebook_package(
            "#gta6 #noni #noniclips #clipsdenoni #gta",
            "jaja qué jugada brutal increíble", "noni", "es",
            2, 3, 5.62, True, llm_client=FakeLLM(FB_PACKAGE))
        self.assertEqual(pkg["viral"]["game"], "GTA VI")
        self.assertEqual(pkg["moment_card"]["game"], "GTA VI")
        self.assertTrue(pkg["viral"]["best_title"])
        self.assertTrue(pkg["title_es"])
        self.assertTrue(pkg["title_en"])
        # La descripción ya NO es la del vídeo: la escribe Gemini
        # desde el momento (spec títulos-verdad §6.4); el título
        # del vídeo/descripción del reel no fija el paquete.
        self.assertIn("jugada brutal", pkg["viral"]["description"])
        self.assertIn("GTA 6", pkg["viral"]["description"])
        # El cierre es una pregunta generada por clip; el crédito
        # fijo («Clip de noni en directo») va DESPUÉS, así que la
        # descripción no termina en «?».
        self.assertIn("¿Viste cómo termina?",
                      pkg["viral"]["description"])
        self.assertIn("Clip de noni en directo",
                      pkg["viral"]["description"])
        self.assertEqual(pkg["viral"]["viral_multiple"], 5.62)
        self.assertEqual(
            set(pkg["seo_scores"]),
            {"youtube_shorts", "tiktok", "instagram_reels",
             "facebook_reels"})
        # El paquete suggest-only real medía 51-54 (no llegaba a
        # 80) — esa era la causa sistemática del bug de títulos.
        # Con el paquete Gemini el SEO pasa la puerta en las 4.
        scores = {p: v["score"]
                  for p, v in pkg["seo_scores"].items()}
        self.assertTrue(all(isinstance(v, (int, float))
                            for v in scores.values()))
        self.assertTrue(all(v >= 80 for v in scores.values()),
                        scores)

    def test_sin_verificar_no_hay_game(self):
        pkg = build_facebook_package(
            "#noni", "hablando normal del juego", "noni", "es",
            0, 0, 1.0, False, llm_client=FakeLLM({
                "title": "Noni habla del juego en directo sin parar",
                "description": "Noni habla del juego en directo "
                               "sin parar. ¿Qué te parece?",
                "hashtags": ["#Noni", "#Directo", "#Juego", "#Charla",
                             "#Shorts"],
                # La miniatura NO puede repetir el título (regla
                # preexistente: el texto completo es subcadena del
                # título y el paquete se rechaza).
                "thumbnail_text": "CHARLA EN DIRECTO",
            }))
        self.assertNotIn("game", pkg["viral"])
        self.assertNotIn("game", pkg["moment_card"])
        self.assertNotIn("GTA", pkg["viral"]["best_title"])

    def test_sin_descripcion_extracto_mas_linea_factual(self):
        pkg = build_facebook_package(
            "", "el avión se queda sin combustible y se estrella "
            "entre edificios", "noni", "es", 1, 2, 3.0, True,
            llm_client=FakeLLM({
                "title": "GTA 6: El avión se queda sin combustible",
                "description": "El avión se queda sin combustible "
                               "y se estrella entre edificios en "
                               "GTA 6. ¿Viste cómo termina?",
                "hashtags": ["#GTA6", "#Noni", "#Avion", "#Edificios",
                             "#Shorts"],
                # La miniatura NO puede repetir el título (regla
                # preexistente): «SIN COMBUSTIBLE» es subcadena del
                # título y el paquete se rechaza; el payoff es que
                # el avión cae.
                "thumbnail_text": "EL AVIÓN CAE",
            }))
        self.assertIn("El avión se queda sin combustible",
                      pkg["viral"]["description"])
        # La línea factual fija («Clip de noni en GTA 6») ya no
        # existe: el crédito que se mantiene es «Clip de noni en
        # directo» (R6.1).
        self.assertIn("Clip de noni en directo",
                      pkg["viral"]["description"])


class TestScoreFacebookClip(unittest.TestCase):
    def test_total_47_55_y_sin_rescate(self):
        flat = score_facebook_clip(_rms_flat_18(),
                                   {"segments": []}, 1.0)
        self.assertEqual(flat[0]["total"], 47.0)
        dyn = score_facebook_clip(_rms_segment(),
                                  _segment_transcript(), 5.62)
        self.assertEqual(dyn[0]["total"], 54.4)
        self.assertEqual(dyn[1]["audio_multiple"], 5.62)
        self.assertEqual(dyn[1]["chat_component_estimated"], 5.0)

    def test_sin_rms_no_se_estima_se_descarta(self):
        with self.assertRaises(ValueError):
            score_facebook_clip([], _segment_transcript(), 1.0)

    def test_words_es_numero_no_lista(self):
        from clipagent.youtube_cut import build_audio_input

        audio = build_audio_input(_rms_flat_18(),
                                  _segment_transcript())
        self.assertIsInstance(audio["words"], int)
        result = score_clip(detail=None, chat_multiple=None,
                            audio=audio, platform=None)
        self.assertEqual(result["components"]["chat"]["value"], 5.0)


class TestPeakGateBuckets1s(unittest.TestCase):
    """El test que caza el bloqueador (§3.1): con buckets de 10 s
    un reel de 20 s tiene 2 buckets y el múltiplo no llega a 2,0;
    con buckets de 1 s el mismo audio sí pasa."""

    def _rms_20s_con_pico_de_1s(self):
        # 20 s a -30 dBFS salvo 1 s a -22 dBFS (2,51x lineal).
        return [(s, -22.0 if s == 5 else -30.0) for s in range(20)]

    def test_buckets_10s_1_07_no_pasan(self):
        buckets = rms_buckets_10s(self._rms_20s_con_pico_de_1s(),
                                  bucket_s=10)
        self.assertEqual(len(buckets), 2)
        mult, _peak, _med = audio_multiple(buckets, 0, 20)
        self.assertAlmostEqual(mult, 1.07, places=2)
        self.assertLess(mult, 2.0)
        self.assertFalse(audio_peak_gate(mult, 3)[0])

    def test_buckets_1s_2_51_pasan(self):
        buckets = rms_buckets_10s(self._rms_20s_con_pico_de_1s(),
                                  bucket_s=1)
        self.assertEqual(len(buckets), 20)
        mult, _peak, _med = audio_multiple(buckets, 0, 20)
        self.assertAlmostEqual(mult, 2.51, places=1)
        self.assertGreaterEqual(mult, 2.0)
        self.assertTrue(audio_peak_gate(mult, 3)[0])

    def test_bucket_values_1_0_y_0_6_dan_1_25(self):
        # Los valores del ejemplo de la spec, a nivel de bucket.
        mult, _peak, _med = audio_multiple(
            [(0, 1.0), (10, 0.6)], 0, 20)
        self.assertAlmostEqual(mult, 1.25, places=2)
        self.assertFalse(audio_peak_gate(mult, 3)[0])


class TestRunFacebookClips(unittest.TestCase):
    """Cortador con yt-dlp mockeado (contrato completo)."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.out = os.path.join(self.tmp.name, "out")
        self.addCleanup(self.tmp.cleanup)
        self.calls = {"audio": [], "samples": [], "segments": [],
                      "renders": []}
        self._extract = mock.patch(
            "clipagent.facebook_cut.extract_audio_from_video",
            side_effect=self._fake_extract)
        self._extract.start()
        self.addCleanup(self._extract.stop)

    def _fake_extract(self, video_path, out_wav, runner=None):
        with open(out_wav, "wb") as f:
            f.write(b"fake-wav")
        return out_wav

    def _audio(self, url, out, runner=None):
        self.calls["audio"].append(url)
        with open(out, "wb") as f:
            f.write(b"fake-wav")
        return out

    def _sample(self, url, out, start_s=None, end_s=None,
                runner=None):
        self.calls["samples"].append(start_s)
        with open(out, "wb") as f:
            f.write(b"fake-wav")
        return out

    def _transcribe(self, path):
        if "full_audio" in path:
            return _transcript_short()
        return _segment_transcript()

    def _rms(self, path):
        if "full_audio" in path:
            return _rms_short_1s()
        return _rms_segment()

    def _segment(self, url, start, end, out):
        self.calls["segments"].append((start, end))
        with open(out, "wb") as f:
            f.write(b"fake-mp4")
        return out

    def _render(self, seg_path, clip_path, duration, cfg):
        self.calls["renders"].append(clip_path)
        with open(clip_path, "wb") as f:
            f.write(b"fake-mp4")
        return {"mode": "rubius", "segments": []}

    def _validate(self, clip_path, title, text, cfg):
        return {"decision": "aprobado"}

    def _layout(self, seg_path, duration, cfg):
        return {"vod_id": REEL_ID, "facecam_box": None,
                "method": None, "hits": 0, "samples": 0}

    def _run(self, **over):
        kwargs = {
            "metadata_fn": lambda url: _meta(),
            "audio_fn": self._audio,
            "sample_fn": self._sample,
            "transcribe_fn": self._transcribe,
            "rms_fn": self._rms,
            "segment_fn": self._segment,
            "render_fn": self._render,
            "validate_fn": self._validate,
            "layout_fn": self._layout,
            # Paquete Gemini falso (spec títulos-verdad): sin LLM
            # el paquete queda retenido y no sale ningún clip.
            "llm_client": FakeLLM(FB_PACKAGE),
        }
        kwargs.update(over)
        return run_facebook_clips(REEL_URL, self.out, top_n=2,
                                  lang="es", single_url=True,
                                  **kwargs)

    def test_contrato_completo_y_manifiesto_en_disco(self):
        manifest = self._run()
        self.assertIsNotNone(manifest)
        self.assertEqual(len(manifest["clips"]), 1)
        clip = manifest["clips"][0]
        # Contrato §3.1, campo a campo.
        self.assertEqual(clip["start"], 0)
        self.assertEqual(clip["duration"], 18)
        self.assertTrue(clip["file"].startswith("clips/clip_01_"))
        self.assertTrue(os.path.isfile(
            os.path.join(self.out, clip["file"])))
        self.assertEqual(clip["source"], "facebook")
        self.assertEqual(clip["source_key"], f"fb:{REEL_ID}")
        self.assertEqual(clip["vod_id"], REEL_ID)
        self.assertEqual(clip["n"], 1)
        self.assertIsNotNone(clip["vod_layout"])
        self.assertEqual(clip["moment_card"]["game"], "GTA VI")
        self.assertIn("title", clip["moment_card"])
        self.assertTrue(clip["transcript"]["segments"])
        viral = clip["viral"]
        self.assertTrue(viral["best_title"])
        self.assertIn("GTA", viral["best_title"])
        self.assertAlmostEqual(viral["viral_multiple"], 5.62,
                               places=1)
        self.assertTrue(clip["title_es"])
        self.assertTrue(clip["title_en"])
        self.assertEqual(clip["score_kind"], "facebook_audio")
        self.assertEqual(clip["viral_breakdown"]["audio_multiple"],
                         viral["viral_multiple"])
        self.assertEqual(clip["viral_score"]["total"], 54.4)
        self.assertFalse(clip["viral_score"]["filter_passed"])
        self.assertIsInstance(clip["peak"], float)
        self.assertGreaterEqual(clip["peak"], 0)
        self.assertLess(clip["peak"], 18)
        self.assertEqual(clip["hype_score"], clip["score"])
        # Segmentos en segments/ con el nombrado; el segmento del
        # reel entero se descarga UNA vez y se reutiliza.
        segs = os.listdir(os.path.join(self.out, "segments"))
        self.assertEqual(segs, ["seg_01_t0h00m00s.mp4"])
        self.assertEqual(self.calls["segments"], [(0, 18)])
        # El audio NO se baja aparte (sale del vídeo descargado).
        self.assertEqual(self.calls["audio"], [])
        self.assertEqual(self.calls["samples"], [])
        # El manifiesto EXISTE EN DISCO (no solo en memoria).
        manifest_path = os.path.join(self.out, "manifest.json")
        self.assertTrue(os.path.isfile(manifest_path))
        with open(manifest_path, encoding="utf-8") as f:
            on_disk = json.load(f)
        self.assertEqual(len(on_disk["clips"]), 1)
        self.assertEqual(on_disk["clips"][0]["viral"]["best_title"],
                         viral["best_title"])
        # Puertas (2026-11-26, spec títulos-verdad): el paquete
        # suggest-only real medía 51-54 y NO pasaba la puerta SEO;
        # con el paquete Gemini (título, descripción, hashtags y
        # miniatura desde el momento) el SEO pasa y el clip pasa
        # las puertas. El 51-54 era el bug, no la calibración.
        ok, reasons = clip_passes_gates(clip, REEL_ID)
        self.assertTrue(ok, reasons)

    def test_con_titulos_que_pasan_seo_pasa_puertas_y_se_encola(self):
        manifest = self._run(titles_fn=_titles_passing)
        self.assertEqual(len(manifest["clips"]), 1)
        clip = manifest["clips"][0]
        ok, reasons = clip_passes_gates(clip, REEL_ID)
        self.assertTrue(ok, reasons)
        sub, review = _patch_burn()
        self.addCleanup(sub.stop)
        self.addCleanup(review.stop)
        bridge = FakeBridge()
        panel_id, why = burn_and_enqueue(
            bridge, "noni", REEL_ID, self.out, clip)
        self.assertEqual(panel_id, 101, why)
        self.assertEqual(len(bridge.created), 1)
        payload = bridge.created[0]
        self.assertTrue(payload["title"])
        self.assertEqual(payload["source_key"], f"fb:{REEL_ID}")
        # source_id == id numérico del reel (cabe en 64 bits).
        self.assertEqual(payload["source_id"], int(REEL_ID))
        viral = payload["viral"]
        self.assertEqual(viral["viral_score"]["total"], 54.4)
        self.assertEqual(viral["seo_scores"], {
            p: clip["seo_scores"][p]["score"]
            for p in ("youtube_shorts", "tiktok", "instagram_reels",
                      "facebook_reels")})
        self.assertTrue(all(
            isinstance(v, (int, float))
            for v in viral["seo_scores"].values()))
        self.assertEqual(viral["viral_breakdown"]["audio_multiple"],
                         5.62)
        self.assertEqual(viral["priority"], panel_score(
            clip["viral_score"]["total"], clip["seo_scores"], 5.62))
        self.assertGreaterEqual(viral["priority"], 53)
        self.assertEqual(viral["score_kind"], "facebook_audio")
        self.assertEqual(len(bridge.patched), 1)

    def test_reel_14s_ningun_clip(self):
        manifest = run_facebook_clips(
            REEL_URL, self.out, top_n=2, lang="es",
            single_url=True,
            metadata_fn=lambda url: _meta(duration=14),
            audio_fn=self._audio, transcribe_fn=self._transcribe,
            rms_fn=self._rms, segment_fn=self._segment,
            render_fn=self._render, validate_fn=self._validate,
            layout_fn=self._layout)
        self.assertEqual(manifest["clips"], [])
        self.assertTrue(os.path.isfile(
            os.path.join(self.out, "manifest.json")))
        self.assertTrue(any(
            "15" in (d.get("reason") or "")
            for d in manifest["descartados"]))
        self.assertEqual(self.calls["segments"], [])

    def test_reel_70s_audio_completo_no_muestreo(self):
        def _transcribe70(path):
            if "full_audio" in path:
                return _transcript([
                    (0, "hablando normal del juego y del directo"),
                    (30, "jaja qué jugada brutal increíble vamos"),
                    (60, "hablando normal del juego y del directo")])
            return _segment_transcript()

        def _rms70(path):
            if "full_audio" in path:
                values = [-30.0] * 70
                for s in range(40, 45):
                    values[s] = -15.0
                return [(s, v) for s, v in enumerate(values)]
            return [(s, -40.0 if s < 25 else -15.0)
                    for s in range(50)]

        manifest = run_facebook_clips(
            REEL_URL, self.out, top_n=2, lang="es",
            single_url=True,
            metadata_fn=lambda url: _meta(duration=70),
            audio_fn=self._audio,
            sample_fn=self._sample,
            transcribe_fn=_transcribe70,
            rms_fn=_rms70, segment_fn=self._segment,
            render_fn=self._render, validate_fn=self._validate,
            layout_fn=self._layout, titles_fn=_titles_passing,
            llm_client=FakeLLM(FB_PACKAGE_70S))
        # Audio completo UNA vez (single_url=True -> modo full);
        # el muestreo no se usa.
        self.assertEqual(len(self.calls["audio"]), 1)
        self.assertEqual(self.calls["samples"], [])
        self.assertEqual(len(manifest["clips"]), 1)
        clip = manifest["clips"][0]
        self.assertEqual(clip["start"], 20)
        self.assertEqual(clip["duration"], 50)
        self.assertEqual(self.calls["segments"], [(20, 70)])

    def test_hashtags_sin_gta_se_salta(self):
        manifest = run_facebook_clips(
            REEL_URL, self.out, top_n=2, lang="es",
            single_url=True,
            metadata_fn=lambda url: _meta(
                description="#cocina #recetas #noni"),
            audio_fn=self._audio, transcribe_fn=self._transcribe,
            rms_fn=self._rms, segment_fn=self._segment,
            render_fn=self._render, validate_fn=self._validate,
            layout_fn=self._layout)
        self.assertEqual(manifest["clips"], [])
        self.assertEqual(self.calls["segments"], [])
        self.assertEqual(self.calls["audio"], [])
        self.assertTrue(any(
            "hashtags sin GTA" in (d.get("reason") or "")
            for d in manifest["descartados"]))

    def test_sin_hashtags_y_transcripcion_sin_gta_fail_closed(self):
        manifest = self._run(
            metadata_fn=lambda url: _meta(description=""))
        self.assertEqual(manifest["clips"], [])
        self.assertTrue(any(
            "juego no verificado" in (d.get("reason") or "")
            for d in manifest["descartados"]))

    def test_sin_hashtags_pero_transcripcion_menciona_gta(self):
        def _transcribe_gta(path):
            if "full_audio" in path:
                return _transcript([(0, "jaja qué jugada en gta 6 "
                                       "brutal increíble")])
            return _segment_transcript()

        manifest = self._run(
            metadata_fn=lambda url: _meta(description=""),
            transcribe_fn=_transcribe_gta)
        self.assertEqual(len(manifest["clips"]), 1)
        self.assertEqual(
            manifest["clips"][0]["moment_card"]["game"], "GTA VI")

    def test_sin_audio_descarta(self):
        manifest = self._run(rms_fn=lambda p: [])
        self.assertEqual(manifest["clips"], [])
        self.assertTrue(any(
            "sin pista de audio" in (d.get("reason") or "")
            for d in manifest["descartados"]))

    def test_sin_pico_no_hay_clip(self):
        manifest = self._run(rms_fn=lambda p: _rms_flat_18())
        self.assertEqual(manifest["clips"], [])
        self.assertEqual(self.calls["renders"], [])

    def test_rechazado_no_se_guarda(self):
        manifest = self._run(
            validate_fn=lambda *a: {"decision": "rechazado",
                                     "motivo": "sin cara"})
        self.assertEqual(manifest["clips"], [])
        self.assertTrue(any(
            "validación final" in (d.get("reason") or "")
            for d in manifest["descartados"]))

    def test_url_no_facebook_devuelve_none(self):
        self.assertIsNone(run_facebook_clips(
            "https://youtu.be/NrNdw_Y6_bI", self.out,
            metadata_fn=lambda url: _meta()))


class TestFacebookPriorityThreshold(unittest.TestCase):
    """Umbral 53 al encolar, solo URL única (YouTube/Facebook)."""

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
                          "srt": self.video + ".srt",
                          "burn": self.video, "transcript": None})
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
            bridge, "noni", REEL_ID, self.tmp.name,
            _fb_clip(total=47, seo_score=80, mult=2.0))
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
            bridge, "noni", REEL_ID, self.tmp.name,
            _fb_clip(total=52, seo_score=85, mult=3.0))
        self.assertEqual(panel_id, 101, why)
        self.assertEqual(len(bridge.created), 1)
        self.assertEqual(bridge.created[0]["source_key"],
                         f"fb:{REEL_ID}")
        self.assertEqual(bridge.created[0]["source_id"], int(REEL_ID))

    def test_renombre_sin_referencias_viejas(self):
        self.assertTrue(hasattr(vod_fallback,
                                "URL_CUT_PRIORITY_MIN"))
        self.assertFalse(hasattr(vod_fallback,
                                 "YOUTUBE_PRIORITY_MIN"))
        self.assertEqual(URL_CUT_PRIORITY_MIN, 53)


class TestFacebookDedupAndState(unittest.TestCase):
    def test_dedup_entre_corridas_por_source_key(self):
        clips = [{"source_id": int(REEL_ID),
                  "source_key": f"fb:{REEL_ID}", "start_sec": 0}]
        self.assertTrue(already_enqueued(
            clips, REEL_ID, 0, source_key=f"fb:{REEL_ID}"))
        self.assertFalse(already_enqueued(
            clips, REEL_ID, 10, source_key=f"fb:{REEL_ID}"))
        self.assertFalse(already_enqueued(
            clips, "1918676049105233", 0,
            source_key="fb:1918676049105233"))

    def test_enfriamiento_por_str_vod_id_no_por_source_key(self):
        state = {"vods": {}}
        mark_vod_attempted(state, REEL_ID, [0])
        self.assertTrue(vod_on_cooldown(state, REEL_ID))
        self.assertTrue(vod_on_cooldown(state, int(REEL_ID)))
        self.assertFalse(vod_on_cooldown(state, f"fb:{REEL_ID}"))
        self.assertIn(str(REEL_ID), state["vods"])
        self.assertNotIn(f"fb:{REEL_ID}", state["vods"])


class TestRunFacebookUrl(unittest.TestCase):
    """run() con Facebook: URL única, enfriamiento, pre-chequeo
    GTA, dry-run y autor sin entrada."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.state = os.path.join(self.tmp.name, "state.json")
        self.out = os.path.join(self.tmp.name, "out")
        self.addCleanup(self.tmp.cleanup)

    def _scan_live(self, only=None):
        raise AssertionError(
            "la URL única salta el chequeo de directos")

    def _streamers_fb(self):
        return [{"name": "noni", "platform": "facebook",
                 "profile_url": PROFILE_URL, "language": "es",
                 "enabled": True, "estado": "concedido"}]

    def _fb_manifest(self):
        return {"vod_id": REEL_ID, "clips": [_fb_clip()]}

    def _run(self, **over):
        kwargs = {
            "max_clips": 2, "top_n": 2,
            "state_path": self.state, "out_root": self.out,
            "bridge": FakeBridge(),
            "scan_fn": self._scan_live,
            "facebook_url": REEL_URL,
        }
        kwargs.update(over)
        return run(**kwargs)

    def _patch_meta_streamers(self, meta, streamers):
        p1 = mock.patch(
            "clipagent.facebook_cut.fetch_facebook_metadata",
            return_value=meta)
        p2 = mock.patch("clipagent.streamers.load_streamers",
                        return_value=streamers)
        p1.start()
        p2.start()
        self.addCleanup(p1.stop)
        self.addCleanup(p2.stop)

    def test_url_unica_corta_y_salta_directos(self):
        self._patch_meta_streamers(_meta(), self._streamers_fb())
        seen = {}

        def _fb(url, out_dir, top_n=2, lang="es", single_url=True):
            seen["url"] = url
            seen["out_dir"] = out_dir
            seen["lang"] = lang
            seen["single_url"] = single_url
            return self._fb_manifest()

        def _enqueue(bridge, streamer, vod_id, out_dir, clip,
                     lang="es"):
            seen["streamer"] = streamer
            return 101, ""

        n, summary = self._run(facebook_clips_fn=_fb,
                               enqueue_fn=_enqueue)
        self.assertEqual(n, 1)
        self.assertEqual(seen["url"], REEL_URL)
        self.assertEqual(seen["out_dir"],
                         os.path.join(self.out, REEL_ID))
        self.assertEqual(seen["lang"], "es")
        self.assertIs(seen["single_url"], True)
        self.assertEqual(seen["streamer"], "noni")
        self.assertIn(REEL_ID, summary)
        # El enfriamiento queda claveado por el id del reel.
        self.assertTrue(vod_on_cooldown(
            load_state(self.state), REEL_ID))

    def test_url_unica_no_gta_se_salta(self):
        self._patch_meta_streamers(
            _meta(description="#cocina #recetas"),
            self._streamers_fb())
        called = []

        def _fb(url, out_dir, top_n=2, lang="es", single_url=True):
            called.append(url)
            return self._fb_manifest()

        n, summary = self._run(
            facebook_clips_fn=_fb,
            enqueue_fn=lambda *a, **k: (101, ""))
        self.assertEqual(n, 0)
        self.assertEqual(called, [])
        self.assertIn("GTA VI", summary)

    def test_url_unica_en_enfriamiento_no_corta(self):
        save_state(self.state, {"vods": {REEL_ID: {
            "attempted_at": 9999999999}}})
        self._patch_meta_streamers(_meta(), self._streamers_fb())
        called = []

        def _fb(url, out_dir, top_n=2, lang="es", single_url=True):
            called.append(url)
            return self._fb_manifest()

        n, _ = self._run(
            facebook_clips_fn=_fb,
            enqueue_fn=lambda *a, **k: (101, ""))
        self.assertEqual(n, 0)
        self.assertEqual(called, [])

    def test_url_unica_dry_run(self):
        self._patch_meta_streamers(_meta(), self._streamers_fb())
        called = []

        def _fb(url, out_dir, top_n=2, lang="es", single_url=True):
            called.append(url)
            return self._fb_manifest()

        n, summary = self._run(
            facebook_clips_fn=_fb,
            enqueue_fn=lambda *a, **k: (101, ""),
            dry_run=True)
        self.assertEqual(n, 0)
        self.assertEqual(called, [])
        self.assertIn("dry-run", summary)
        self.assertTrue(vod_on_cooldown(
            load_state(self.state), REEL_ID))

    def test_autor_sin_entrada_fail_closed_con_evento(self):
        self._patch_meta_streamers(_meta(), [])
        bridge = FakeBridge()
        called = []

        def _fb(url, out_dir, top_n=2, lang="es", single_url=True):
            called.append(url)
            return self._fb_manifest()

        n, summary = self._run(
            bridge=bridge, facebook_clips_fn=_fb,
            enqueue_fn=lambda *a, **k: (101, ""))
        self.assertEqual(n, 0)
        self.assertEqual(called, [])
        self.assertIn("autor sin entrada facebook en el panel",
                      summary)
        # El evento clip_descartado llega al panel (puente
        # mockeado que lo registra).
        self.assertEqual(len(bridge.events), 1)
        kind, message = bridge.events[0]
        self.assertEqual(kind, "clip_descartado")
        self.assertIn("autor sin entrada facebook en el panel",
                      message)
        self.assertIn(REEL_ID, message)

    def test_url_no_facebook(self):
        n, summary = self._run(
            facebook_url="https://youtu.be/NrNdw_Y6_bI",
            facebook_clips_fn=lambda *a, **k: self._fb_manifest(),
            enqueue_fn=lambda *a, **k: (101, ""))
        self.assertEqual(n, 0)
        self.assertIn("no es de Facebook", summary)

    def test_sin_metadatos(self):
        with mock.patch(
                "clipagent.facebook_cut.fetch_facebook_metadata",
                side_effect=FacebookError("yt-dlp falló")), \
                mock.patch("clipagent.streamers.load_streamers",
                           return_value=self._streamers_fb()):
            n, summary = self._run(
                facebook_clips_fn=lambda *a, **k: self._fb_manifest(),
                enqueue_fn=lambda *a, **k: (101, ""))
        self.assertEqual(n, 0)
        self.assertIn("sin metadatos", summary)

    def test_cli_facebook_url(self):
        with mock.patch("clipagent.vod_fallback.run",
                        return_value=(0, "")) as run_mock:
            code = main(["--facebook-url", REEL_URL,
                         "--state", self.state,
                         "--out-dir", self.out])
        self.assertEqual(code, 0)
        self.assertEqual(run_mock.call_count, 1)
        self.assertEqual(
            run_mock.call_args.kwargs.get("facebook_url"), REEL_URL)


if __name__ == "__main__":
    unittest.main()

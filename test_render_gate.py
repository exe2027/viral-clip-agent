#!/usr/bin/env python3
"""Puerta de render PRE-CORTE (regla del usuario 2026-11-18).

El clip SOLO se corta/renderiza si ya pasa las puertas (viralidad
y SEO >= 90) con el paquete provisional. Si no pasa, se descarta
sin render.

Mediciones que calibran la puerta (2026-11-18, con datos reales):
- p3220 (Rubius, clip bueno): viralidad real 53,8 pero estimación
  pre-corte 46,3; SEO pre-corte 89/91/92/92 (máx. 92). PASA.
- p2010 (Rubius, descartado): viralidad 44,5 (= estimación, sin
  human); SEO 92/94/94/94. NO PASA (viralidad).
Por eso el umbral pre-corte de viralidad es 45 (estimación con
defaults conservadores 5,0, subestima ~7 puntos), no 50: con 50,
p3220 se descartaría. El 50 REAL sigue en la puerta de publicación.
"""
import json
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from clipagent.jobs import StepJobs  # noqa: E402
from clipagent.policy import render_gate  # noqa: E402
from clipagent.workers import (  # noqa: E402
    PRE_CORTE_VIRAL_MIN,
    StepError,
    advance_pipeline,
    detect_peaks,
    gate_peak_for_render,
)

HERE = os.path.dirname(os.path.abspath(__file__))
P3220_CHAT = os.path.join(
    HERE, "runs", "2932415021-p3220", "chat_w3100-3340.json")

SEO_BUENO = {"youtube_shorts": 92, "tiktok": 94,
             "instagram_reels": 94, "facebook_reels": 94}
SEO_BAJO = {"youtube_shorts": 75, "tiktok": 74,
            "instagram_reels": 73, "facebook_reels": 72}


# --- render_gate (pura) -----------------------------------------------

def test_render_gate_p2010_falla_por_viralidad():
    """p2010: viralidad 44,5 < 45 con SEO 92-94 -> no pasa."""
    g = render_gate(44.5, SEO_BUENO)
    assert g["pasa"] is False
    assert "44.5" in g["motivo"]
    assert g["plataformas_publicables"] == []


def test_render_gate_p3220_pasa():
    """p3220: viralidad 53,8 y SEO 92-94 -> pasa."""
    g = render_gate(53.8, SEO_BUENO)
    assert g["pasa"] is True
    assert len(g["plataformas_publicables"]) == 4


def test_render_gate_seo_bajo_falla():
    g = render_gate(55.0, SEO_BAJO)
    assert g["pasa"] is False
    assert "SEO" in g["motivo"]


def test_render_gate_max_no_min():
    """Basta UNA plataforma >= 80: el clip se corta si se puede
    publicar en alguna; la puerta por plataforma sigue en
    publicación."""
    g = render_gate(55.0, {"youtube_shorts": 79, "tiktok": 81,
                            "instagram_reels": 78, "facebook_reels": 77})
    assert g["pasa"] is True
    assert g["plataformas_publicables"] == ["tiktok"]


def test_render_gate_seo_sin_medir_no_descarta():
    """Sin SEO medible (sin juego o sin título), la puerta decide
    solo por viralidad y lo anota."""
    g = render_gate(55.0, {})
    assert g["pasa"] is True
    assert g["seo_medido"] is False
    g2 = render_gate(44.0, {})
    assert g2["pasa"] is False


def test_render_gate_sin_viralidad_no_pasa():
    g = render_gate(None, SEO_BUENO)
    assert g["pasa"] is False


def test_render_gate_umbral_pre_corte_45():
    """El umbral pre-corte calibrado es 45 (no 50): p3220 estima
    46,3 y p2010 44,5."""
    assert PRE_CORTE_VIRAL_MIN == 45
    assert render_gate(46.3, SEO_BUENO, viral_min=45)["pasa"] is True
    assert render_gate(44.5, SEO_BUENO, viral_min=45)["pasa"] is False
    # Y con el umbral real (50) p3220 estimado NO pasaría: por eso
    # el 50 solo se aplica en publicación, no pre-corte.
    assert render_gate(46.3, SEO_BUENO, viral_min=50)["pasa"] is False


# --- gate_peak_for_render ----------------------------------------------

def _chat_with_burst(path, burst_start=100, n_burst=40, n_base=20):
    comments = []
    for i in range(n_base):
        comments.append({
            "content_offset_seconds": i * 30,
            "message": {"body": "hola, qué tal"},
            "commenter": {"name": f"base{i}"}})
    for i in range(n_burst):
        comments.append({
            "content_offset_seconds": burst_start + (i % 8),
            "message": {"body": "JAJAJAJA qué bueno"},
            "commenter": {"name": f"fan{i}"}})
    with open(path, "w", encoding="utf-8") as f:
        json.dump({"comments": comments}, f)
    return path


@pytest.mark.skipif(not os.path.isfile(P3220_CHAT),
                    reason="falta el chat real de p3220 en runs/")
def test_gate_p3220_real_pasa():
    """Con los datos reales de p3220, la puerta pre-corte PASA por
    viralidad: estimación 46,3 >= 45.

    2026-11-26 (enmienda aprobada: Gemini o retención, sin fallback
    de reglas): sin clave de Gemini en local NO hay título
    provisional honesto, así que el SEO pre-corte NO se mide
    (`seo_medido` False, `pre_corte_title` None) y la puerta decide
    solo por viralidad (regla preexistente de render_gate: sin SEO
    medible no se descarta por él; la retención llega después, con
    el paquete definitivo). El test viejo afirmaba SEO máx. 81 con
    el paquete de reglas — ese paquete ya no existe.
    """
    from clipagent.ingest import median_over_all, score_windows
    scores, detail = score_windows(P3220_CHAT, lang="es")
    median = median_over_all(scores)
    gate = gate_peak_for_render(
        P3220_CHAT, 3220, 14.3, 1.09, detail[3220], median,
        list(scores.values()), lang="es", game="GTA 6",
        streamer="rubius", vod_id="2932415021",
        game_source="capítulo de Twitch: Grand Theft Auto VI",
        clip_start=3200, duration=48, n=1)
    assert gate["viral_estimate"] == pytest.approx(46.3, abs=0.2)
    assert gate["seo_medido"] is False
    assert gate["pre_corte_title"] is None
    assert gate["pasa"] is True
    assert "SEO sin medir" in gate["motivo"]


def test_gate_pico_debil_no_pasa(tmp_path):
    """Chat plano (1 msg cada 30 s, sin ráfaga): la estimación es
    44,0 < 45 y la puerta lo descarta sin render. (Una ráfaga de
    solo 4 mensajes ya estima 47,5+: el umbral 45 separa 'el chat
    no se movió' de 'hubo ráfaga'.)"""
    from clipagent.ingest import median_over_all, score_windows
    comments = [{
        "content_offset_seconds": i * 30,
        "message": {"body": "hola"},
        "commenter": {"name": f"u{i}"}} for i in range(20)]
    chat_path = str(tmp_path / "chat.json")
    with open(chat_path, "w", encoding="utf-8") as f:
        json.dump({"comments": comments}, f)
    scores, detail = score_windows(chat_path, lang="es")
    median = median_over_all(scores)
    peak_ts = max(scores, key=scores.get)
    gate = gate_peak_for_render(
        chat_path, peak_ts, scores[peak_ts], 1.0, detail[peak_ts],
        median, list(scores.values()), lang="es", game="GTA 6",
        streamer="rubius", vod_id="1", n=1)
    assert gate["viral_estimate"] == pytest.approx(44.0, abs=0.5)
    assert gate["pasa"] is False


# --- detect_peaks + advance + _h_corte ---------------------------------

def test_detect_peaks_pico_fuerte_pasa_puerta(tmp_path):
    """El pico fuerte del chat sintético pasa la puerta (viralidad
    estimada 57,5; sin juego verificado el SEO queda sin medir y
    no descarta) y lleva los campos de la puerta."""
    chat = _chat_with_burst(str(tmp_path / "chat.json"))
    res = detect_peaks(chat, top_n=5)
    assert len(res["peaks"]) >= 1
    peak = res["peaks"][0]
    assert peak["render_gate"]["pasa"] is True
    assert peak["render_gate"]["viral_estimate"] >= 45
    assert peak["seo_scores"] == {} or peak["render_gate"]["seo_medido"] is False
    assert "puerta de render" not in str(res["discarded"])


def test_h_corte_rechaza_sin_render(monkeypatch):
    """_h_corte con scores que no pasan la puerta NO llama a
    cut_clip (no se gasta ffmpeg)."""
    from clipagent import workers
    calls = []
    monkeypatch.setattr(
        workers, "cut_clip",
        lambda *a, **k: calls.append((a, k)) or {"output": "x.mp4"})
    job = {"payload": {"video": "v.mp4", "start_sec": 0,
                       "duration": 48, "output": "o.mp4",
                       "viral_total": 44.5, "seo_scores": SEO_BUENO}}
    with pytest.raises(StepError, match="puerta de render"):
        workers._h_corte(None, job, cfg={})
    assert calls == []


def test_h_corte_pasa_y_sin_scores_corta(monkeypatch):
    """Con scores que pasan, y sin scores (corte explícito/manual),
    el corte se hace."""
    from clipagent import workers
    calls = []
    monkeypatch.setattr(
        workers, "cut_clip",
        lambda *a, **k: calls.append((a, k)) or {"output": "x.mp4"})
    ok = {"payload": {"video": "v.mp4", "start_sec": 0,
                      "duration": 48, "output": "o.mp4",
                      "viral_total": 55.0, "seo_scores": SEO_BUENO}}
    # _h_corte añade la clave 'plantilla' (diseño del validador,
    # 2026-11-19): None aquí porque el corte simulado no trae
    # layout; sin ella el validador cae al default (rubius).
    assert workers._h_corte(None, ok, cfg={}) == {
        "output": "x.mp4", "plantilla": None}
    manual = {"payload": {"video": "v.mp4", "start_sec": 0,
                          "duration": 48, "output": "o.mp4"}}
    assert workers._h_corte(None, manual, cfg={}) == {
        "output": "x.mp4", "plantilla": None}
    assert len(calls) == 2


def test_h_corte_sin_webcam_corta_game(monkeypatch):
    """Decisión §2.1 sin_webcam -> layout_mode "game" y plantilla
    rubius_sin_webcam en el resultado (spec §9.1)."""
    from clipagent import template_decision, workers
    calls = []
    monkeypatch.setattr(
        workers, "cut_clip",
        lambda *a, **k: calls.append((a, k)) or {
            "output": "x.mp4", "layout": {"mode": "game"}})
    monkeypatch.setattr(
        template_decision, "decide_template_for_segment",
        lambda *a, **k: ("rubius_sin_webcam", None, "firme", False))
    job = {"payload": {"video": "v.mp4", "start_sec": 0,
                       "duration": 40, "output": "o.mp4"}}
    assert workers._h_corte(None, job, cfg={}) == {
        "output": "x.mp4", "layout": {"mode": "game"},
        "plantilla": "rubius_sin_webcam"}
    assert calls[0][1]["layout_mode"] == "game"


def test_h_corte_modo_explicito_respeta_sin_decidir(monkeypatch):
    """Un layout_mode explícito del productor se respeta: la
    decisión §2.1 ni se corre."""
    from clipagent import template_decision, workers
    calls = []
    monkeypatch.setattr(
        workers, "cut_clip",
        lambda *a, **k: calls.append((a, k)) or {"output": "x.mp4"})
    decide_calls = []
    monkeypatch.setattr(
        template_decision, "decide_template_for_segment",
        lambda *a, **k: decide_calls.append(1)
        or ("rubius_sin_webcam", None, "firme", False))
    job = {"payload": {"video": "v.mp4", "start_sec": 0,
                       "duration": 40, "output": "o.mp4",
                       "layout_mode": "stack"}}
    workers._h_corte(None, job, cfg={})
    assert decide_calls == []
    assert calls[0][1]["layout_mode"] == "stack"


def test_h_corte_decision_que_falla_no_tumba(monkeypatch):
    """Si la decisión §2.1 levanta, el corte sigue en auto."""
    from clipagent import template_decision, workers
    calls = []
    monkeypatch.setattr(
        workers, "cut_clip",
        lambda *a, **k: calls.append((a, k)) or {"output": "x.mp4"})

    def _boom(*a, **k):
        raise RuntimeError("sin vídeo")

    monkeypatch.setattr(
        template_decision, "decide_template_for_segment", _boom)
    job = {"payload": {"video": "v.mp4", "start_sec": 0,
                       "duration": 40, "output": "o.mp4"}}
    assert workers._h_corte(None, job, cfg={}) == {
        "output": "x.mp4", "plantilla": None}
    assert calls[0][1]["layout_mode"] == "auto"


@pytest.fixture()
def store(tmp_path):
    s = StepJobs(str(tmp_path / "jobs.db"))
    yield s
    s.close()


def test_advance_picos_a_corte_lleva_scores(store, tmp_path):
    """El fan-out picos -> corte lleva viral_total + seo_scores en
    el payload, para que _h_corte re-verifique."""
    video = tmp_path / "vod.mp4"
    video.write_bytes(b"v" * 10)
    out_dir = tmp_path / "runs" / "vod1"
    out_dir.mkdir(parents=True)
    job = {"id": 1, "clip_id": "vod1", "paso": "picos",
           "payload": {"video": str(video), "out_dir": str(out_dir)}}
    result = {"peaks": [{
        "start": 100, "score": 15.0, "multiple": 3.0,
        "viral_total": 57.5, "seo_scores": SEO_BUENO,
        "pre_corte_title": "GTA 6: El chat"}]}
    created = advance_pipeline(store, job, result)
    assert len(created) == 1
    assert created[0]["paso"] == "corte"
    assert created[0]["payload"]["viral_total"] == 57.5
    assert created[0]["payload"]["seo_scores"] == SEO_BUENO
    assert created[0]["payload"]["pre_corte_title"] == "GTA 6: El chat"


def test_do_cut_pasa_facecam_box_top_level(monkeypatch):
    """§4.2 (B-N2): _do_cut fusiona el facecam_box top-level de
    config.yaml en la config de layout (antes se perdía: solo se
    pasaba la subsección `layout`)."""
    from clipagent import workers
    seen = {}
    monkeypatch.setattr(
        "clipagent.render.cut_clip_with_layout",
        lambda *a, **k: seen.update(k) or {"layout": {}})
    cfg = {"facecam_box": [100, 50, 400, 200],
           "layout": {"mode": "auto"}}
    workers._do_cut("v.mp4", 0, 40, "o.mp4", cfg=cfg)
    assert seen["cfg"]["facecam_box"] == [100, 50, 400, 200]
    # Copia por valor del fusionado: mutar lo pasado no toca cfg.
    seen["cfg"]["facecam_box"][0] = 999
    assert cfg["facecam_box"] == [100, 50, 400, 200]


def test_do_cut_precedencia_layout_sobre_top_level(monkeypatch):
    """El top-level solo manda si la subsección `layout` no trae
    la caja (precedencia de get_layout_config)."""
    from clipagent import workers
    seen = {}
    monkeypatch.setattr(
        "clipagent.render.cut_clip_with_layout",
        lambda *a, **k: seen.update(k) or {"layout": {}})
    cfg = {"facecam_box": [100, 50, 400, 200],
           "layout": {"facecam_box": [10, 20, 30, 40]}}
    workers._do_cut("v.mp4", 0, 40, "o.mp4", cfg=cfg)
    assert seen["cfg"]["facecam_box"] == [10, 20, 30, 40]


def test_h_corte_fija_avatar_en_payload(monkeypatch):
    """§4.3.2: _h_corte fija el flag avatar de la decisión en
    payload["avatar"] (el merge de pasos copia todo el payload
    hasta la validación del job)."""
    from clipagent import template_decision, workers
    monkeypatch.setattr(
        workers, "cut_clip",
        lambda *a, **k: {"output": "x.mp4", "layout": {}})
    monkeypatch.setattr(
        template_decision, "decide_template_for_segment",
        lambda *a, **k: ("rubius", None, "firme", True))
    job = {"payload": {"video": "v.mp4", "start_sec": 0,
                       "duration": 40, "output": "o.mp4"}}
    workers._h_corte(None, job, cfg={})
    assert job["payload"]["avatar"] is True

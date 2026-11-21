"""Tests del gate cuadro por cuadro rubius (clipagent/render_gate.py).

El gate es el espejo compacto del template_gate de main (camino
rubius): mismas comprobaciones y mismos umbrales calibrados
(scooter vs 52h). Los tests unitarios usan arrays sintéticos;
el e2e renderiza el clip sintético de test_rubius.py (con boom
real en el payoff) y exige que el gate PASE, y que un clip sin
banner/subs/SFX NO PASE.

(No confundir con test_render_gate.py: ese es la puerta de
render PRE-CORTE de policy.py, otra cosa.)
"""

import os

import numpy as np
import pytest

from clipagent import render_gate as rg
from clipagent.config import load_config
from test_rubius import _make_synthetic_segment

HERE = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(HERE, "config.yaml")


def _yellow_zone(w=100, h=50):
    a = np.zeros((h, w, 3), dtype=np.uint8)
    a[:, :] = (253, 210, 39)  # amarillo del banner rubius
    return a


def test_count_color_yellow():
    zone = _yellow_zone()
    assert rg._banner_yellow_frac(zone) == pytest.approx(1.0)
    assert rg._count_color(zone, rg._WHITE) == 0
    plain = np.zeros((50, 100, 3), dtype=np.uint8)
    assert rg._banner_yellow_frac(plain) == 0.0


def test_subs_edge_coverages_single_word():
    # Una palabra centrada: blanco en el centro, bordes limpios.
    a = np.zeros((120, 1080, 3), dtype=np.uint8)
    a[:, 400:680] = 255
    left, right = rg._subs_edge_coverages(a, 0, 120)
    assert left < 0.10 and right < 0.10
    # Una línea completa: blanco de borde a borde.
    a[:, :] = 255
    left, right = rg._subs_edge_coverages(a, 0, 120)
    assert left >= 0.10 and right >= 0.10


def test_ncc_best_self_is_one():
    rng = np.random.default_rng(7)
    x = rng.normal(size=16000)
    corr, lag = rg._ncc_best(x, x[:8000], 16000)
    assert corr == pytest.approx(1.0, abs=0.01)
    assert lag == pytest.approx(0.0, abs=0.01)


def test_srt_active_times(tmp_path):
    srt = tmp_path / "c.srt"
    srt.write_text(
        "1\n00:00:01,000 --> 00:00:02,500\nhola\n\n"
        "2\n00:01:00,000 --> 00:01:01,000\nmundo\n",
        encoding="utf-8")
    assert rg._srt_active_times(str(srt)) == [(1.0, 2.5), (60.0, 61.0)]
    assert rg._srt_active_times(None) == []
    assert rg._is_active(2.0, [(1.0, 2.5)])
    assert not rg._is_active(5.0, [(1.0, 2.5)])


def test_detect_sfx_in_clip_with_boom(tmp_path):
    """Un boom mezclado se detecta con NCC ~1; sin él, no."""
    from clipagent.sfx_mix import mix_sfx

    seg, _srt = _make_synthetic_segment(tmp_path)
    mixed, _info = mix_sfx(
        seg, [{"effect": "vine_boom", "start": 6.0, "duration": 2.0,
               "reason": "test"}],
        str(tmp_path / "mixed.mp4"), lift=False)
    res = rg.detect_sfx_in_clip(mixed)
    assert res["found"] is True
    assert res["effect"] == "vine_boom"
    assert res["corr"] > 0.9
    res2 = rg.detect_sfx_in_clip(seg)
    assert res2["found"] is False


def test_render_gate_passes_synthetic_rubius(tmp_path):
    """El render sintético con banner + subs + boom PASA el gate."""
    from clipagent import rubius

    seg, srt = _make_synthetic_segment(tmp_path)
    cfg = load_config(CONFIG_PATH)
    banner = tmp_path / "banner.png"
    rubius.make_banner_png("GTA 6: Prueba sintética", str(banner))
    from test_rubius import _make_ass
    ass = _make_ass(tmp_path, srt)
    out = tmp_path / "clip_rubius.mp4"
    _dt, final = rubius.render(
        seg, 0, 12.0, ass, str(out), banner_png=str(banner),
        cam_box="800:450:560:315", cam_square="450:450:735:315",
        punch_in=None, game_crop="1080:1080:420:0",
        cfg=cfg, sfx_srt=srt, payoff_s=6.0,
        punch_zoom=1.30, shake=True, impact=True,
        banner_slide=True, fade_in=True, whoosh=False,
    )
    res = rg.render_gate(final, srt_path=srt, cfg=cfg,
                         out_dir=str(tmp_path / "gate"))
    assert res["pasa"], res
    for name in ("dimensiones", "gameplay", "sin_bandas",
                 "titulo_banner", "subtitulos_blancos",
                 "subtitulos_una_palabra", "banda_viva",
                 "sfx_presente"):
        assert res["checks"][name]["pasa"], (name, res["checks"][name])


def test_render_gate_fails_plain_clip(tmp_path):
    """Un clip sin banner/subs/SFX NO PASA (el gate no es laxo)."""
    seg, _srt = _make_synthetic_segment(tmp_path)
    cfg = load_config(CONFIG_PATH)
    res = rg.render_gate(seg, srt_path=None, cfg=cfg,
                         out_dir=str(tmp_path / "gate2"))
    assert not res["pasa"]
    assert "titulo_banner" in res["fallos"]
    assert "sfx_presente" in res["fallos"]

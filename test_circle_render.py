"""Tests del driver de render CircleCI (clipagent/circle_render.py).

Sin red: la descarga se prueba con una URL file:// a un MP4
sintético local; la validación de tiempos y el corte se prueban
en local. La descarga real (yt-dlp) y la transcripción
(faster-whisper) solo se ejercitan en vivo en CircleCI.
"""

import os
import subprocess

import pytest

from clipagent import circle_render as cr


def _make_source(tmp_path, duration=30.0):
    src = tmp_path / "source.mp4"
    subprocess.run([
        "ffmpeg", "-y", "-v", "error",
        "-f", "lavfi", "-i",
        f"testsrc2=size=1920x1080:rate=30:duration={duration}",
        "-f", "lavfi", "-i", f"sine=frequency=440:duration={duration}",
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "23",
        "-c:a", "aac", "-b:a", "128k",
        "-t", str(duration), str(src),
    ], check=True)
    return str(src)


def test_circle_render_validates_times(tmp_path):
    with pytest.raises(RuntimeError, match="mayor que start"):
        cr.circle_render("https://example.com/v.mp4", "Rubius",
                         "GTA 6: Prueba", 10, 5, "es", str(tmp_path))
    with pytest.raises(RuntimeError, match="tope es 60"):
        cr.circle_render("https://example.com/v.mp4", "Rubius",
                         "GTA 6: Prueba", 0, 61, "es", str(tmp_path))


def test_cut_and_probe(tmp_path):
    src = _make_source(tmp_path)
    assert cr._probe_size(src) == (1920, 1080)
    assert abs(cr._probe_duration(src) - 30.0) < 0.5
    out = tmp_path / "cut.mp4"
    cr._cut(src, 5, 15, str(out))
    assert out.is_file() and out.stat().st_size > 0
    assert abs(cr._probe_duration(str(out)) - 10.0) < 0.5


def test_download_mp4_direct_file_url(tmp_path):
    src = _make_source(tmp_path, duration=20.0)
    url = f"file://{src}"
    seg = cr.download_source(url, 2, 12, str(tmp_path / "work"))
    assert os.path.isfile(seg) and os.path.getsize(seg) > 0
    assert abs(cr._probe_duration(seg) - 10.0) < 0.5


def test_main_returns_2_on_bad_input(tmp_path):
    rc = cr.main([
        "--source-url", "https://example.com/v.mp4",
        "--streamer", "Rubius", "--title", "GTA 6: Prueba",
        "--start", "10", "--end", "5",
        "--out-dir", str(tmp_path),
    ])
    assert rc == 2

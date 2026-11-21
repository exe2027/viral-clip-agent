#!/usr/bin/env python3
"""Regla del usuario 2026-11-17 22:10, matizada 2026-11-19 18:41.

* Todo clip lleva subtítulos quemados de TODO lo que dice el
  influencer, no solo del pico, cuando hay voz.
* Sin voz verificable el clip ya NO se bloquea siempre: si pasa
  las puertas (viralidad >=45, SEO >=80) se publica igual SIN
  subtítulos (muchas veces es un grito, no silencio). Nunca se
  inventa texto: el SRT queda vacío.
* La transcripción completa (tiny -> base) cubre el clip entero.

Uso: python3 test_subtitles_always.py
"""
import sys

sys.path.insert(0, __file__.rsplit("/", 1)[0])

from clipagent.config import load_config  # noqa: E402
from clipagent.validate import validate_config  # noqa: E402
from clipagent.workers import subtitle_clip  # noqa: E402


def main() -> int:
    cfg = load_config()
    subs = cfg.get("subtitles") or {}
    assert subs.get("required") is True, subs
    assert subs.get("complete") is True, subs
    assert subs.get("publish_no_speech_if_passes_gates") is True, subs
    rules = cfg.get("rules") or {}
    assert rules.get("subtitles_always") is True, rules
    assert rules.get("subtitles_complete") is True, rules
    assert rules.get("publish_no_speech_if_passes_gates") is True, rules
    print("OK config: required/complete/"
          "publish_no_speech_if_passes_gates=true")

    errors = validate_config(cfg)
    assert not errors, errors
    print("OK validate_config sin errores")

    # Sin voz -> paso OK, SIN bloquear, SRT vacío (nada inventado).
    import clipagent.workers as workers

    orig = workers._do_transcribe
    workers._do_transcribe = lambda *a, **k: (
        {"no_speech": True, "word_count": 0, "speech_ratio": 0.0}, None)
    try:
        import os
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            video = os.path.join(td, "clip.mp4")
            open(video, "wb").write(b"fake")
            res = subtitle_clip(video, cfg=cfg)
            assert res["no_speech"] is True, res
            assert res["needs_human_transcription"] is False, res
            assert res["publish_blocked"] is False, res
            assert "burned" not in res, res
            # SRT vacío: ni una palabra inventada.
            with open(res["srt"], encoding="utf-8") as f:
                assert f.read().strip() == "", res
    finally:
        workers._do_transcribe = orig
    print("OK no_speech -> sin bloqueo, SRT vacío, sin quemar subtítulos")
    print("3/3 casos OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

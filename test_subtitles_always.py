#!/usr/bin/env python3
"""Regla del usuario 2026-11-17 22:10: SIEMPRE subtítulos, COMPLETOS.

* Todo clip lleva subtítulos quemados de TODO lo que dice el
  influencer, no solo del pico.
* Sin subtítulos no se publica: un clip sin voz detectada queda
  BLOQUEADO (needs_human_transcription + publish_blocked) hasta
  que el usuario lo transcriba de oído. Nunca se inventa texto.
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
    assert subs.get("block_without_subtitles") is True, subs
    assert subs.get("needs_human_transcription_on_no_speech") is True, subs
    rules = cfg.get("rules") or {}
    assert rules.get("subtitles_always") is True, rules
    assert rules.get("subtitles_complete") is True, rules
    assert rules.get("block_without_subtitles") is True, rules
    print("OK config: required/complete/block_without_subtitles=true")

    errors = validate_config(cfg)
    assert not errors, errors
    print("OK validate_config sin errores")

    # Sin voz -> paso OK pero clip BLOQUEADO, no publicable.
    import clipagent.workers as workers

    orig = workers._do_transcribe
    workers._do_transcribe = lambda *a, **k: (
        {"no_speech": True, "word_count": 0, "speech_ratio": 0.0}, None)
    try:
        import tempfile, os
        with tempfile.TemporaryDirectory() as td:
            video = os.path.join(td, "clip.mp4")
            open(video, "wb").write(b"fake")
            res = subtitle_clip(video, cfg=cfg)
            assert res["no_speech"] is True, res
            assert res["needs_human_transcription"] is True, res
            assert res["publish_blocked"] is True, res
            assert "burned" not in res, res
    finally:
        workers._do_transcribe = orig
    print("OK no_speech -> needs_human_transcription + publish_blocked, sin quemar")
    print("3/3 casos OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

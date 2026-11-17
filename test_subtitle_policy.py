#!/usr/bin/env python3
"""Pruebas de la política de traducción de subtítulos (v2.5).

Regla del usuario (2026-11-16):
  * audio en español -> subtítulos SOLO en español, sin traducir.
  * audio en cualquier otro idioma -> traducir los subtítulos a inglés.

No necesita faster-whisper ni audio: prueba la decisión pura
(config.resolve_subtitle_task), que es la rama que decide la tarea.
La rama de audio real se prueba con los clips (ver README, v2.5):
rumano -> translate/en en el clip de Locones, inglés -> transcribe/en
en el clip de Twitch 2929669422.

Uso: python3 test_subtitle_policy.py
"""
import sys

sys.path.insert(0, __file__.rsplit("/", 1)[0])

from clipagent.config import get_subtitle_policy, resolve_subtitle_task  # noqa: E402

POLICY = {"es": "transcribe-only", "others": "translate-to-en"}

CASES = [
    # (detectado, subtitle_lang, translate, esperado)
    ("es", "auto", None, ("transcribe", "es")),   # español: NUNCA traducir
    ("es", "auto", True, ("transcribe", "es")),   # ni forzando --translate
    ("es", "en", None, ("transcribe", "es")),     # --subtitle-lang en no traduce el español
    ("ro", "auto", None, ("translate", "en")),    # rumano -> inglés
    ("ro", "auto", False, ("transcribe", "ro")),  # --no-translate lo bloquea
    ("ro", "es", None, ("transcribe", "ro")),     # Whisper no traduce a español
    ("en", "auto", None, ("transcribe", "en")),   # inglés ya está en inglés
    ("en", "en", None, ("transcribe", "en")),
    ("fr", "auto", None, ("translate", "en")),    # francés -> inglés
    ("de", "auto", None, ("translate", "en")),    # alemán -> inglés
    ("pt", "auto", None, ("translate", "en")),    # portugués -> inglés
]


def main() -> int:
    policy = get_subtitle_policy({"subtitle_policy": POLICY})
    assert policy == POLICY, policy
    failures = 0
    for detected, sub_lang, translate, expected in CASES:
        got = resolve_subtitle_task(detected, subtitle_lang=sub_lang,
                                    translate=translate, policy=POLICY)
        ok = got == expected
        failures += not ok
        print(f"{'OK ' if ok else 'FALLO'} {detected:3} lang={sub_lang:4} "
              f"translate={str(translate):5} -> {got} (esperado {expected})")
    print(f"{len(CASES) - failures}/{len(CASES)} casos OK")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""Frases hype del STREAMER (2026-11-18, idea del usuario).

El detector live mira el CHAT (velocidad + ráfaga). Pero el hype
también está en lo que DICE el streamer: cuando suelta un
"no lo puedo creer", un "WTF" o un "Dios mío", eso marca un clip
aunque el chat no se dispare tanto. Lluvia de ideas del usuario
(2026-11-18 18:06-18:07): "wow", "WTF", "OMG", "Dios mío" y
"cosas así".

Dónde se usa: en la transcripción Whisper que ya existe por clip
(``review.review_peak`` y el subtítulo de ``live_cutter``). NO
cambia ninguna fórmula ni puerta: la viralidad, el SEO, la
prioridad y los filtros duros (viralidad >= 50, SEO >= 90)
siguen intactos. La señal queda registrada en el clip
(``streamer_hype``) y visible en la Cola LIVE, para que un clip
con frases del streamer se reconozca como momento fuerte.

Detección: insensible a mayúsculas y acentos, y tolera letras
alargadas ("nooo", "creeer", "Diiios") porque cada letra de la
frase puede repetirse. Solo stdlib.
"""

import re
import unicodedata

# Frases de asombro / incredulidad (ES).
PHRASES_ES = (
    "no me lo creo",
    "no lo puedo creer",
    "no puede ser",
    "qué locura",
    "esto es increíble",
    "madre mía",
    "dios mío",
    "no jodas",
    "qué acabo de ver",
    "esto no es real",
    "estoy flipando",
    "estoy alucinando",
    "qué pasada",
    "lo conseguí",
    "lo hice",
    "me muero",
    "no puedo más",
    "qué risa",
    "estoy llorando",
    "la he liado",
    "qué fail",
    "qué acabo de hacer",
    "mira eso",
    "habéis visto eso",
    "qué gráficos",
    "qué mapa",
    "esto es otro nivel",
    "primera vez",
    "qué detalle",
    "qué persecución",
    "qué explosión",
    "qué golazo",
    "no, no, no",
    "cojones",
    "ole",
)
# "cojones" y "ole" del reel de GTA VI CLIPS (2026-11-18): el
# streamer los suelta al ver que tiran al personaje del auto.
# "vamos" fuera: demasiado genérico ("vamos a seguir...") y salta
# en habla normal sin hype.

# Exclamaciones en inglés (KaiCenat, xQc, TheBurntPeanut...).
PHRASES_EN = (
    "wtf",
    "omg",
    "oh my god",
    "no way",
    "what the hell",
    "this is insane",
    "holy shit",
    "that's crazy",
    "i can't believe this",
    "let's go",
    "bro what",
    "what the fuck",
)

ALL_PHRASES = PHRASES_ES + PHRASES_EN


def _normalize(text):
    """Minúsculas, sin acentos, espacios colapsados."""
    text = (text or "").lower()
    text = "".join(
        c for c in unicodedata.normalize("NFD", text)
        if unicodedata.category(c) != "Mn")
    return re.sub(r"\s+", " ", text).strip()


def _phrase_pattern(phrase):
    """Regex de la frase con letras repetibles ("nooo" casa "no").

    Cada carácter alfanumérico puede aparecer 1+ veces; los
    espacios admiten 1+ separadores. Con bordes de palabra.
    """
    parts = []
    for ch in _normalize(phrase):
        if ch.isalnum():
            parts.append(re.escape(ch) + "+")
        elif ch == " ":
            parts.append(r"\s+")
        else:
            parts.append(re.escape(ch) + r"?")
    return re.compile(r"\b" + "".join(parts) + r"\b")


_PATTERNS = [(phrase, _phrase_pattern(phrase)) for phrase in ALL_PHRASES]


def find_streamer_hype(text):
    """Frases hype del streamer en un texto (transcripción).

    Devuelve {"count": nº total de apariciones, "distinct": nº de
    frases distintas, "phrases": [frases distintas en orden de
    aparición]}. Sin texto devuelve ceros (nunca lanza).
    """
    norm = _normalize(text)
    if not norm:
        return {"count": 0, "distinct": 0, "phrases": []}
    hits = []
    for phrase, pattern in _PATTERNS:
        for m in pattern.finditer(norm):
            hits.append((m.start(), phrase))
    hits.sort()
    phrases = []
    for _pos, phrase in hits:
        if phrase not in phrases:
            phrases.append(phrase)
    return {"count": len(hits), "distinct": len(phrases),
            "phrases": phrases}


def transcript_text(transcript):
    """Texto plano de una transcripción Whisper (dict).

    Acepta {"segments": [{"text": ...}]} o {"text": ...}.
    Sin voz devuelve "".
    """
    if not transcript:
        return ""
    if isinstance(transcript, dict):
        parts = []
        for seg in transcript.get("segments") or []:
            text = ((seg or {}).get("text") or "").strip()
            if text:
                parts.append(text)
        if parts:
            return " ".join(parts)
        return (transcript.get("text") or "").strip()
    return ""

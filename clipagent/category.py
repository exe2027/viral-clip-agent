#!/usr/bin/env python3
"""Categoría del stream/VOD: solo GTA VI vale para un clip.

Regla del usuario 2026-11-18: un clip solo es válido si la
categoría en la que está streameando el influencer es GTA VI
(Grand Theft Auto VI). Un momento de Just Chatting (o de otro
juego) se descarta, aunque el chat explote.

Fuentes (APIs oficiales, nunca scraping):
- Twitch en vivo: Helix GET /helix/streams?user_login=<login>
  (game_name/game_id) y GET /helix/channels?broadcaster_id=<id>
  (ver watcher.TwitchHelix.live_category / channel_category).
- Twitch VOD por tramos: capítulos del VOD. Helix NO expone
  capítulos: los trae el chat.json descargado (video.chapters con
  gameDisplayName, el mismo dato que usa el reproductor de Twitch).
  category_at() lee el capítulo donde cae el pico.
- Kick: GET https://kick.com/api/v2/channels/<slug> (categoría
  actual; ver clipagent.live.kick_channel_category).

Este módulo es lógica pura (sin red): la red vive en watcher.py
(Twitch) y live.py (Kick), y el gate en policy.py / panel.
"""
import json

# Nombres canónicos de la categoría (Twitch game_name / Kick
# category name). La comparación normaliza (minúsculas, espacios).
GTA6_NAMES = {
    "gta vi",
    "gta 6",
    "gta6",
    "grand theft auto vi",
    "grand theft auto 6",
    "grand theft auto vi (gta 6)",
}


def normalize_category(name):
    """Categoría a forma comparable: minúsculas, espacios simples."""
    if not name:
        return ""
    return " ".join(str(name).strip().lower().split())


def is_gta6(name):
    """True solo si la categoría es GTA VI (Grand Theft Auto VI)."""
    norm = normalize_category(name)
    if norm in GTA6_NAMES:
        return True
    # "grand theft auto vi ..." con sufijo (ediciones, RP...): el
    # prefijo manda, pero "gta v" / "grand theft auto v" NO valen.
    return (norm.startswith("grand theft auto vi")
            or norm.startswith("gta vi "))


def _chapter_bounds(ch):
    """(start_s, length_s, name) de un capítulo, o None.

    Acepta capítulos del chat.json (startMilliseconds /
    lengthMilliseconds + gameDisplayName) y capítulos ya en
    segundos (start / length + game/name/description).
    """
    if not isinstance(ch, dict):
        return None
    try:
        if "startMilliseconds" in ch or "lengthMilliseconds" in ch:
            start = float(ch.get("startMilliseconds") or 0) / 1000.0
            length = float(ch.get("lengthMilliseconds") or 0) / 1000.0
        else:
            start = float(ch.get("start") or 0)
            length = float(ch.get("length") or 0)
    except (TypeError, ValueError):
        return None
    name = (ch.get("gameDisplayName") or ch.get("game")
            or ch.get("name") or ch.get("description") or "")
    return start, length, name


def category_at(chapters, ts):
    """Categoría (nombre tal cual) del capítulo donde cae `ts`.

    chapters: lista de capítulos (ver _chapter_bounds). Si el pico
    cae fuera de todos los capítulos o no hay capítulos, None (sin
    categoría verificada: el gate lo retiene, no lo asume).
    """
    if not chapters:
        return None
    for ch in chapters:
        parsed = _chapter_bounds(ch)
        if parsed is None:
            continue
        start, length, name = parsed
        if length <= 0:
            continue
        if start <= ts < start + length:
            return name or None
    return None


def chapter_category_from_chat(chat_path, peak_ts):
    """Categoría cruda del capítulo del VOD donde cae el pico.

    Lee video.chapters del chat.json (el mismo dato que el
    reproductor de Twitch). Sin capítulos o fuera de ellos: None.
    """
    try:
        with open(chat_path, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return None
    vid = (data or {}).get("video") or {}
    if not isinstance(vid, dict):
        return None
    return category_at(vid.get("chapters") or [], float(peak_ts))


def effective_category(clip, game=None):
    """Categoría efectiva de un clip para el gate.

    Orden: clip["category"] (capítulo donde cae el pico) >
    clip["game"] > parámetro game. Sin ninguna: "" (sin verificar).
    """
    clip = clip or {}
    return (clip.get("category") or clip.get("game") or game or "")

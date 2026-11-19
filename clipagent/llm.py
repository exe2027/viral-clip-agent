"""Cliente LLM intercambiable (Fase 3.4g).

Interfaz mínima `LLMClient`: un método generate(prompt) -> str.
El proveedor se elige con la variable LLM_PROVIDER (gemini | none).
Sin clave, o si el proveedor falla, el llamador usa el fallback
al scoring actual: el LLM es una señal más, nunca un requisito.

Hoy solo hay proveedor Gemini (REST, sin dependencia nueva:
urllib). Añadir otro proveedor es implementar generate().
"""
import json
import os
import re
import urllib.error
import urllib.request

from . import credentials as _creds

DEFAULT_MODEL = "gemini-3.5-flash-lite"


def _clean_title(text):
    """Primera línea, sin comillas, máx. 60 car. (limpiador común:
    lo usan el reintento SEO y el primer paquete SEO)."""
    line = (text or "").strip().splitlines()
    title = line[0].strip().strip("\"'") if line else ""
    return title[:60] if title else ""


def _clean_hashtags(text):
    """Hashtags (#...) de un texto, máx. 8."""
    tags = [t for t in (text or "").replace(",", " ").split()
            if t.startswith("#")]
    if not tags:
        tags = ["#" + t.strip("#.,") for t in
                (text or "").split() if t.strip("#.,")]
    return tags[:8]


def _clean_hashtag_list(items):
    """Lista de hashtags limpia: con #, sin duplicados, máx. 8."""
    out = []
    for t in items or []:
        if not isinstance(t, str):
            continue
        tag = t.strip().strip(",")
        if not tag:
            continue
        if not tag.startswith("#"):
            tag = "#" + tag.lstrip("#")
        tag = "#" + re.sub(r"[^\w]", "", tag[1:], flags=re.UNICODE)
        if len(tag) > 1 and tag not in out:
            out.append(tag)
    return out[:8]


def _parse_seo_package_json(text):
    """Extrae {title, description, hashtags, thumbnail_text} de la
    respuesta del LLM (JSON puro o entre cercas markdown). None si
    no hay JSON válido o faltan campos."""
    raw = (text or "").strip()
    if not raw:
        return None
    m = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", raw, re.DOTALL)
    if m:
        raw = m.group(1)
    else:
        start, end = raw.find("{"), raw.rfind("}")
        if start < 0 or end <= start:
            return None
        raw = raw[start:end + 1]
    try:
        data = json.loads(raw)
    except (json.JSONDecodeError, ValueError):
        return None
    if not isinstance(data, dict):
        return None
    title = _clean_title(data.get("title") or "")
    desc = data.get("description") or ""
    desc = desc.strip() if isinstance(desc, str) else ""
    hashtags = data.get("hashtags")
    if isinstance(hashtags, str):
        hashtags = _clean_hashtags(hashtags)
    else:
        hashtags = _clean_hashtag_list(hashtags)
    thumb = _clean_title(data.get("thumbnail_text") or "")
    thumb = " ".join(thumb.split()[:4])
    if not title or not desc or not hashtags or not thumb:
        return None
    return {"title": title, "description": desc[:1000],
            "hashtags": hashtags, "thumbnail_text": thumb}


def _seo_package_prompt(card, visible=None, lang="es"):
    """Prompt del primer paquete SEO: la ficha del momento es la
    única fuente (transcripción, cita del chat, streamer, reacción
    dominante, risas, juego). Sin inventar nada fuera de ella."""
    card = card or {}
    game = (card.get("game_label") or card.get("game") or "").strip()
    streamer = (card.get("streamer") or "").strip()
    reaction = (card.get("dominant_reaction") or "").strip()
    risas = card.get("risas") or 0
    msgs = card.get("chat_msgs_peak_10s") or 0
    what = (card.get("what") or "").strip()
    transcript = (card.get("transcript_text") or "").strip()[:500]
    quote_chat = (card.get("quote_chat") or {}).get("text") or ""
    quote_streamer = (card.get("quote_streamer") or {}).get("text") or ""
    cams_only = visible == "cams-only"  # mismo valor que metadata.VISIBLE_CAMS_ONLY
    en = lang == "en"
    if en:
        lines = ["Write the full SEO package for a Short",
                 f"about {game or 'a stream moment'}." if game else
                 "about a stream moment.",
                 "",
                 "Moment (do not invent anything outside this):"]
        if streamer:
            lines.append(f"- Streamer: {streamer}")
        if game:
            lines.append(f"- Game: {game}")
        if reaction:
            lines.append(f"- Dominant reaction: {reaction} "
                         f"({risas} laughs, {msgs} messages in 10 s)")
        if what:
            lines.append(f"- What happens: {what}")
        if transcript:
            lines.append(f"- Transcript: \"{transcript}\"")
        if quote_chat:
            lines.append(f"- Chat said: \"{quote_chat}\"")
        if quote_streamer:
            lines.append(f"- Streamer said: \"{quote_streamer}\"")
        lines.append("")
        lines.append("Rules:")
        if cams_only:
            lines.append("- The title is a REACTION title (what chat / "
                         "the streamer felt). Do NOT name any game and "
                         "do NOT promise gameplay.")
        elif game:
            lines.append(f"- The title starts with \"{game}:\" and is "
                         "40-55 characters long.")
        else:
            lines.append("- The title does NOT name any game "
                         "(not verified which one it is).")
        if game:
            lines.append(f"- The description is 2-3 sentences and "
                         f"mentions {game}.")
        else:
            lines.append("- The description is 2-3 sentences.")
        lines.append("- 5 hashtags.")
        lines.append("- The thumbnail text is max 4 words, names the "
                     "concrete moment (use the transcript or the quote), "
                     "and does not repeat words from the title. Never a "
                     "generic exclamation like 'WOW!' or 'UNREAL!'.")
        lines.append("- The thumbnail text NAMES the concrete moment "
                     "(what happened, what object, what reaction). "
                     "NEVER a generic exclamation like 'WOW!', "
                     "'UNREAL!', 'NO WAY!' or 'WAIT FOR IT'.")
        lines.append("")
        lines.append("Return ONLY this JSON, no explanation: "
                     "{\"title\": \"...\", \"description\": \"...\", "
                     "\"hashtags\": [\"#...\", ...], "
                     "\"thumbnail_text\": \"...\"}")
        return "\n".join(lines)
    lines = ["Escribe el paquete SEO completo para un Short",
             f"sobre {game}." if game else "sobre un momento de directo.",
             "",
             "Momento (no inventes nada fuera de esto):"]
    if streamer:
        lines.append(f"- Streamer: {streamer}")
    if game:
        lines.append(f"- Juego: {game}")
    if reaction:
        lines.append(f"- Reacción dominante: {reaction} "
                     f"({risas} risas, {msgs} mensajes en 10 s)")
    if what:
        lines.append(f"- Qué pasa: {what}")
    if transcript:
        lines.append(f"- Transcripción: \"{transcript}\"")
    if quote_chat:
        lines.append(f"- El chat dijo: \"{quote_chat}\"")
    if quote_streamer:
        lines.append(f"- El streamer dijo: \"{quote_streamer}\"")
    lines.append("")
    lines.append("Reglas:")
    if cams_only:
        lines.append("- El título es de REACCIÓN (lo que sintió el chat "
                     "/ el streamer), NO nombra ningún juego ni promete "
                     "gameplay.")
    elif game:
        lines.append(f"- El título empieza con \"{game}:\" y tiene "
                     "entre 40 y 55 caracteres.")
    else:
        lines.append("- El título NO nombra ningún juego "
                     "(no está verificado cuál es).")
    if game:
        lines.append(f"- La descripción tiene 2-3 frases y menciona {game}.")
    else:
        lines.append("- La descripción tiene 2-3 frases.")
    lines.append("- 5 hashtags en español.")
    lines.append("- El texto de miniatura tiene máx. 4 palabras, nombra "
                 "el momento concreto (usa la transcripción o la cita) y "
                 "no repite palabras del título. Nunca una exclamación "
                 "genérica como '¡QUÉ LOCURA!' o 'NO PUEDE SER'.")
    lines.append("- El texto de miniatura NOMBRA el momento concreto "
                 "(qué pasó, qué objeto, qué reacción). NUNCA una "
                 "exclamación genérica como '¡QUÉ LOCURA!', "
                 "'¡INCREÍBLE!' o '¡NO LO CREO!' (regla 2026-11-18).")
    lines.append("")
    lines.append("Devuelve SOLO este JSON, sin explicación: "
                 "{\"title\": \"...\", \"description\": \"...\", "
                 "\"hashtags\": [\"#...\", ...], "
                 "\"thumbnail_text\": \"...\"}")
    return "\n".join(lines)


class LLMClient:
    """Interfaz: cualquier proveedor implementa generate()."""

    name = "base"

    def available(self):
        """¿Se puede usar (hay clave/configuración)? Sin LLM: False."""
        return False

    def generate(self, prompt: str) -> str:
        raise NotImplementedError


class NullLLMClient(LLMClient):
    """Sin LLM: devuelve vacío y el llamador usa el fallback."""

    name = "none"

    def generate(self, prompt: str) -> str:
        return ""


class GeminiLLMClient(LLMClient):
    """Gemini por REST (generateContent), sin SDK.

    Key en GEMINI_API_KEY (entorno o .env). Modelo por
    LLM_MODEL (por defecto gemini-3.5-flash-lite).
    """

    name = "gemini"

    def __init__(self, api_key=None, model=None, request_fn=None):
        self.api_key = api_key or _creds.get_secret("GEMINI_API_KEY")
        self.model = model or os.environ.get("LLM_MODEL") or DEFAULT_MODEL
        self.request_fn = request_fn

    def available(self):
        return bool(self.api_key)

    def generate(self, prompt: str) -> str:
        if not self.api_key:
            return ""
        url = ("https://generativelanguage.googleapis.com/v1beta/models/"
               f"{self.model}:generateContent?key={self.api_key}")
        body = {"contents": [{"parts": [{"text": prompt}]}]}
        if self.request_fn is not None:
            status, payload = self.request_fn("POST", url, {}, body)
        else:
            data = json.dumps(body).encode("utf-8")
            req = urllib.request.Request(
                url, data=data, method="POST",
                headers={"Content-Type": "application/json"})
            try:
                with urllib.request.urlopen(req, timeout=60) as resp:
                    status, payload = resp.status, json.load(resp)
            except urllib.error.HTTPError:
                return ""
            except Exception:
                return ""
        if status != 200:
            return ""
        try:
            return payload["candidates"][0]["content"]["parts"][0]["text"]
        except (KeyError, IndexError, TypeError):
            return ""


def make_seo_variant(client, lang="es"):
    """make_variant(pkg, worst_block) para seo_score.retry_best (6B.4).

    Pide al LLM una variante SOLO del bloque más débil (título,
    descripción, hashtags o texto de miniatura) y devuelve el pkg
    con ese bloque cambiado. Sin LLM disponible, o si el LLM no
    responde nada útil, devuelve None (el reintento se para y el
    clip queda retenido, nunca se publica con SEO < 90).
    """
    def variant(pkg, worst):
        if client is None or not client.available():
            return None
        title = pkg.get("title") or ""
        desc = pkg.get("description") or ""
        if worst == "title" or worst == "coherence":
            prompt = ("Escribe UN título nuevo en español para un Short "
                      "sobre GTA 6. Debe incluir 'GTA 6' al inicio, tener "
                      f"entre 40 y 55 caracteres, y describir este momento: "
                      f"{desc[:200]}. Título actual: {title}. Devuelve SOLO "
                      "el título, sin comillas ni explicación.")
            new = _clean_title(client.generate(prompt))
            if not new or new == title:
                return None
            return dict(pkg, title=new)
        if worst == "description":
            prompt = ("Escribe una descripción nueva en español (2-3 "
                      "frases) para un Short sobre GTA 6 con este título: "
                      f"{title}. Debe mencionar GTA 6. Devuelve SOLO la "
                      "descripción, sin comillas ni explicación.")
            new = (client.generate(prompt) or "").strip()
            if not new or new == desc:
                return None
            return dict(pkg, description=new[:1000])
        if worst == "tags":
            prompt = ("Devuelve 5 hashtags en español para un Short "
                      f"sobre GTA 6 con este título: {title}. Solo los "
                      "hashtags separados por espacios, nada más.")
            new = _clean_hashtags(client.generate(prompt))
            if not new or new == list(pkg.get("hashtags") or []):
                return None
            return dict(pkg, hashtags=new, tags=new)
        if worst == "thumbnail":
            prompt = ("Escribe un texto corto (máx. 4 palabras) para la "
                      f"miniatura de un Short sobre GTA 6 con este título: "
                      f"{title}. El texto tiene que nombrar el momento "
                      "concreto de este Short (usa la descripción: "
                      f"{desc[:200]}), no una exclamación genérica como "
                      "'¡QUÉ LOCURA!' o '¡INCREÍBLE!'. "
                      "Devuelve SOLO el texto.")
            new = _clean_title(client.generate(prompt))
            if not new:
                return None
            return dict(pkg, thumbnail_text=new)
        return None

    return variant


def make_seo_package(client, lang="es"):
    """Primer paquete SEO con el LLM (no solo el reintento).

    Pide al LLM el paquete COMPLETO (título, descripción, hashtags,
    texto de miniatura) desde la ficha del momento y devuelve
    generate(card, visible=None) -> dict | None. Sin LLM disponible,
    o si la respuesta no trae JSON válido, devuelve None y el
    llamador usa el paquete por reglas. La validación (check_title
    + lint + juego + SEO) la hace el llamador (metadata).
    """
    def generate(card, visible=None):
        if client is None or not client.available():
            return None
        prompt = _seo_package_prompt(card or {}, visible=visible,
                                     lang=lang)
        try:
            raw = client.generate(prompt)
        except Exception:
            return None
        return _parse_seo_package_json(raw)

    return generate


def get_llm_client(provider=None, request_fn=None):
    """El cliente según LLM_PROVIDER (gemini | none).

    Por defecto: gemini si hay key, si no NullLLMClient (fallback).
    """
    provider = (provider or os.environ.get("LLM_PROVIDER") or "").lower()
    if provider in ("", "auto"):
        provider = "gemini" if _creds.get_secret("GEMINI_API_KEY") else "none"
    if provider == "gemini":
        return GeminiLLMClient(request_fn=request_fn)
    return NullLLMClient()

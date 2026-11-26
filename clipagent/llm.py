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

# Transcripción completa en el prompt cuando el clip es corto
# (spec títulos-verdad, R1.1): un clip de 40 s ronda los 600-900
# caracteres, así que por debajo de este tope va entera; por
# encima se recorta al tramo del payoff ±20 s, nunca solo el
# inicio (ver _transcript_for_prompt).
TRANSCRIPT_FULL_MAX_CHARS = 2500

# Clichés prohibidos salvo evidencia literal en la transcripción
# (spec títulos-verdad, R2.1). Fuente única: config.yaml, sección
# `titles:` (la lee banned_cliches); esta lista es el respaldo
# cuando la config aún no trae la sección (la escribe el agente
# de config en paralelo) y coincide con R2.1.
_FALLBACK_CLICHES_ES = (
    "risa imparable", "no puede parar de reírse",
    "no paraba de reírse", "se murió de risa", "hype máximo",
    "hype desatado", "el chat explotó", "el chat enloqueció",
    "el chat no paraba", "no lo podía creer",
    "nadie se lo creía", "momento que el chat clippeó",
    "mira por qué reaccionó el chat", "hizo reír al chat",
    "la risa de X con el chat")
_FALLBACK_CLICHES_EN = (
    "couldn't stop laughing", "chat went crazy", "chat exploded",
    "chat lost it", "nobody could believe it",
    "the moment chat clipped", "watch what chat reacted to",
    "broke chat", "insane", "unreal", "crazy", "craziest")


def banned_cliches(lang="es"):
    """Lista de clichés prohibidos (R2.1) para el idioma del clip.

    Fuente única: config.yaml `titles:` (claves `cliches_es` /
    `cliches_en`; también se aceptan banned_cliches/cliches/banned
    por compatibilidad); sin esa sección, la lista de respaldo de
    la spec (mismos textos). Nunca lista vacía por silencio: si
    la config trae la sección pero vacía, se usa igualmente el
    respaldo.
    """
    lang = "en" if lang == "en" else "es"
    try:
        from .config import load_config
        titles = (load_config() or {}).get("titles") or {}
        items = titles.get(f"cliches_{lang}") or []
        if items:
            return [str(t) for t in items if str(t).strip()]
        banned = (titles.get("banned_cliches")
                  or titles.get("cliches")
                  or titles.get("banned") or {})
        if isinstance(banned, dict):
            items = banned.get(lang) or []
            if items:
                return [str(t) for t in items if str(t).strip()]
        elif isinstance(banned, (list, tuple)) and banned:
            return [str(t) for t in banned if str(t).strip()]
    except Exception:  # noqa: BLE001 - sin config: respaldo
        pass
    return list(_FALLBACK_CLICHES_EN if lang == "en"
                else _FALLBACK_CLICHES_ES)


def _transcript_for_prompt(card):
    """Transcripción para el prompt (R1.1): entera cuando el clip
    es corto; si es larga, el tramo del payoff ±20 s (con los
    segmentos con tiempos de la ficha, `transcript_segments_timed`
    + `payoff_ts_clip`); sin tiempos, centrada en la cita del
    streamer (el payoff); como último recurso, el inicio + el
    final, nunca solo el inicio."""
    text = (card.get("transcript_text") or "").strip()
    if not text:
        return ""
    if len(text) <= TRANSCRIPT_FULL_MAX_CHARS:
        return text
    segments = card.get("transcript_segments_timed") or []
    payoff = card.get("payoff_ts_clip")
    if segments and payoff is not None:
        try:
            payoff = float(payoff)
            parts = [str(s.get("text") or "")
                     for s in segments
                     if payoff - 20 <= float(s.get("start", 0))
                     <= payoff + 20]
            joined = " ".join(p for p in parts if p).strip()
            if joined:
                return joined
        except (TypeError, ValueError):
            pass
    quote = (card.get("quote_streamer") or {}).get("text") or ""
    if quote:
        idx = text.find(quote)
        if idx >= 0:
            start = max(0, idx - 1200)
            end = min(len(text), idx + len(quote) + 1200)
            return text[start:end]
    return text[:1250] + " … " + text[-1250:]


def _clean_title(text):
    """Primera línea, máx. 60 car. (limpiador común: lo usan el
    reintento SEO y el primer paquete SEO).

    Quita solo un PAR de comillas que envuelvan TODO el título
    (las que añade el LLM alrededor de su respuesta). Una comilla
    de cierre sin su apertura NO se toca: el título del 191
    (``GTA 6: Burn asks: 'What was that moan, bro?'``) termina en
    la comilla que cierra la cita del streamer, y el
    ``.strip("\\"'")`` anterior se la comía.
    """
    line = (text or "").strip().splitlines()
    title = line[0].strip() if line else ""
    if len(title) >= 2 and title[0] == title[-1] \
            and title[0] in "\"'":
        title = title[1:-1].strip()
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


def _pattern_pack_guidance(pattern_pack, lang="es"):
    """Líneas de guía de estilo del pack (spec v3 §3.2).

    Solo plantillas genéricas y palabras frecuentes: nunca el
    texto literal de otros creadores (las hook_text no viajan al
    prompt; son solo corpus de la guardia anti-copia).
    """
    if not pattern_pack:
        return []
    en = lang == "en"
    types = "; ".join(
        f"{t.get('hook_type')}: \"{t.get('template')}\""
        for t in (pattern_pack.get("top_hook_types") or [])
        if t.get("hook_type") and t.get("template"))
    words = ", ".join((pattern_pack.get("top_hook_words") or [])[:10])
    median = pattern_pack.get("median_caption_chars")
    if en:
        lines = ["- Style guide (patterns from viral reels; the moment "
                 "card rules; do not copy these texts):"]
        if types:
            lines.append(f"  Most repeated hooks: {types}.")
        if words:
            lines.append(f"  Frequent hook words: {words}.")
        if median:
            lines.append(f"  Median caption length: {median} characters.")
    else:
        lines = ["- Guía de estilo (patrones de reels virales; la ficha "
                 "del momento manda; no copies estos textos):"]
        if types:
            lines.append(f"  Ganchos que más se repiten: {types}.")
        if words:
            lines.append(f"  Palabras frecuentes en ganchos: {words}.")
        if median:
            lines.append(f"  Largo mediano de caption: {median} caracteres.")
    return lines


def _seo_package_prompt(card, visible=None, lang="es", pattern_pack=None):
    """Prompt del primer paquete SEO: la ficha del momento es la
    única fuente (transcripción, cita del chat, streamer, reacción
    dominante, risas, juego). Sin inventar nada fuera de ella.
    ``pattern_pack`` (spec v3 §3.2) añade solo guía de estilo de
    reels virales; la ficha sigue mandando."""
    card = card or {}
    game = (card.get("game_label") or card.get("game") or "").strip()
    streamer = (card.get("streamer") or "").strip()
    reaction = (card.get("dominant_reaction") or "").strip()
    risas = card.get("risas") or 0
    msgs = card.get("chat_msgs_peak_10s") or 0
    what = (card.get("what") or "").strip()
    # R1.1: transcripción entera cuando el clip es corto; si es
    # larga, el tramo del payoff ±20 s (nunca solo el inicio).
    transcript = _transcript_for_prompt(card)
    quote_chat = (card.get("quote_chat") or {}).get("text") or ""
    quote_streamer = (card.get("quote_streamer") or {}).get("text") or ""
    cams_only = visible == "cams-only"  # mismo valor que metadata.VISIBLE_CAMS_ONLY
    en = lang == "en"
    cliches = banned_cliches("en" if en else "es")
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
            # R1.2(a): la reacción del CHAT y la del STREAMER son
            # dos cosas distintas. Los conteos de abajo son del
            # chat (mensajes); nada de esto dice que el streamer se
            # riera, gritara o no se lo creyera: eso solo lo dice
            # la transcripción / la cita del streamer.
            lines.append(f"- CHAT reaction: {reaction} "
                         f"({risas} laugh messages, {msgs} messages "
                         f"in 10 s). This is what the CHAT did, not "
                         f"the streamer.")
        if quote_streamer:
            lines.append("- STREAMER reaction: only what the "
                         "transcript / streamer quote shows. Do not "
                         "attribute to the streamer anything only "
                         "the chat did.")
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
        # R1.2(b): el título nombra el momento concreto, con la
        # misma fuerza que la regla de la miniatura.
        lines.append("- The title NAMES the concrete moment (what "
                     "happens, what object, what the streamer says). "
                     "Never a generic exclamation and never a "
                     "superlative without an anchor in the "
                     "transcript.")
        if game:
            lines.append(f"- The description is 2-3 sentences and "
                         f"mentions {game}.")
        else:
            lines.append("- The description is 2-3 sentences.")
        # R6.1 (M5): el cierre lo genera el modelo por clip,
        # ligado al momento concreto; nunca la CTA fija.
        lines.append("- The description ends with a question or "
                     "invitation tied to THIS concrete moment (not "
                     "a fixed generic call to action).")
        lines.append("- 5 hashtags: 4 about the moment (streamer, "
                     "object, action) + #Shorts as the fifth.")
        # R2.1: clichés prohibidos salvo evidencia literal.
        lines.append("- Banned phrases unless the transcript shows "
                     "them literally: " + "; ".join(cliches) + ".")
        lines.append("- The thumbnail text is max 4 words, names the "
                     "concrete moment (use the transcript or the quote), "
                     "and does not repeat words from the title. Never a "
                     "generic exclamation like 'WOW!' or 'UNREAL!'.")
        lines.append("- The thumbnail text NAMES the concrete moment "
                     "(what happened, what object, what reaction). "
                     "NEVER a generic exclamation like 'WOW!', "
                     "'UNREAL!', 'NO WAY!' or 'WAIT FOR IT'.")
        lines.extend(_pattern_pack_guidance(pattern_pack, lang="en"))
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
        # R1.2(a): la reacción del CHAT y la del STREAMER son dos
        # cosas distintas. Los conteos de abajo son del chat
        # (mensajes); nada de esto dice que el streamer se riera,
        # gritara o no se lo creyera: eso solo lo dice la
        # transcripción / la cita del streamer.
        lines.append(f"- Reacción del CHAT: {reaction} "
                     f"({risas} mensajes con risa, {msgs} mensajes "
                     f"en 10 s). Esto es lo que hizo el CHAT, no el "
                     f"streamer.")
    if quote_streamer:
        lines.append("- Reacción del STREAMER: solo lo que muestra "
                     "la transcripción / la cita del streamer. No "
                     "le atribuyas al streamer nada que solo hiciera "
                     "el chat.")
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
    # R1.2(b): el título nombra el momento concreto, con la misma
    # fuerza que la regla de la miniatura.
    lines.append("- El título NOMBRA el momento concreto (qué pasa, "
                 "qué objeto, qué dice el streamer). Nunca una "
                 "exclamación genérica ni un superlativo sin anclaje "
                 "en la transcripción.")
    if game:
        lines.append(f"- La descripción tiene 2-3 frases y menciona {game}.")
    else:
        lines.append("- La descripción tiene 2-3 frases.")
    # R6.1 (M5): el cierre lo genera el modelo por clip, ligado
    # al momento concreto; nunca la CTA fija.
    lines.append("- La descripción termina con una pregunta o "
                 "invitación ligada a ESTE momento concreto (no una "
                 "frase fija genérica).")
    lines.append("- 5 hashtags en español: 4 sobre el momento "
                 "(streamer, objeto, acción) + #Shorts como quinto.")
    # R2.1: clichés prohibidos salvo evidencia literal.
    lines.append("- Frases prohibidas salvo que la transcripción las "
                 "muestre literales: " + "; ".join(cliches) + ".")
    lines.append("- El texto de miniatura tiene máx. 4 palabras, nombra "
                 "el momento concreto (usa la transcripción o la cita) y "
                 "no repite palabras del título. Nunca una exclamación "
                 "genérica como '¡QUÉ LOCURA!' o 'NO PUEDE SER'.")
    lines.append("- El texto de miniatura NOMBRA el momento concreto "
                 "(qué pasó, qué objeto, qué reacción). NUNCA una "
                 "exclamación genérica como '¡QUÉ LOCURA!', "
                 "'¡INCREÍBLE!' o '¡NO LO CREO!' (regla 2026-11-18).")
    lines.extend(_pattern_pack_guidance(pattern_pack, lang="es"))
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


def _norm_variant_title(text):
    """Normalización local para comparar títulos (como
    metadata._norm_title: sin tildes, emojis ni puntuación)."""
    import unicodedata
    text = unicodedata.normalize("NFKD", text or "")
    text = "".join(c for c in text if not unicodedata.combining(c))
    text = re.sub(r"[^\w ]", " ", text.lower())
    return re.sub(r"\s+", " ", text).strip()


def _variant_title_ok(title, moment_card, recent_titles, lang="es"):
    """¿La variante de título pasa los MISMOS checks que el primer
    paquete (M7)? Dedupe exacto R4.1 contra la lista del panel,
    patrón R4.2 (si metadata.py ya expone is_pattern_repeat) y
    verificación título-vs-transcripción R3.1 (solo con ficha:
    sin ficha no hay nada que verificar y no se inventa un fallo).
    Fail-open solo en dedupe/patrón si metadata.py aún no expone
    esas funciones; en veracidad, fail-closed."""
    if not (title or "").strip():
        return False
    if recent_titles is not None:
        # Dedupe exacto R4.1 contra la lista del panel (misma
        # función que el primer paquete).
        try:
            from .metadata import is_duplicate_title
            if is_duplicate_title(title, recent_titles=recent_titles,
                                  language=lang):
                return False
        except TypeError:
            norm = _norm_variant_title(title)
            for row in recent_titles:
                other = (row.get("title") if isinstance(row, dict)
                         else row)
                if other and _norm_variant_title(str(other)) == norm:
                    return False
        except Exception:  # noqa: BLE001 - sin dedupe, no tumba
            pass
        # Patrón R4.2 (misma función que el primer paquete).
        try:
            from .metadata import is_pattern_repeat
            recent = [(r.get("title") if isinstance(r, dict)
                       else r) for r in recent_titles]
            if is_pattern_repeat(title, recent):
                return False
        except Exception:  # noqa: BLE001 - sin patrón, no tumba
            pass
    if moment_card:
        try:
            from .metadata import validate_title_final
            if not validate_title_final(
                    title, moment_card, lang=lang).get("ok"):
                return False
        except Exception:  # noqa: BLE001 - sin veredicto, no pasa
            return False
    return True


def make_seo_variant(client, lang="es", moment_card=None,
                     recent_titles=None):
    """make_variant(pkg, worst_block) para seo_score.retry_best (6B.4).

    Pide al LLM una variante SOLO del bloque más débil (título,
    descripción, hashtags o texto de miniatura) y devuelve el pkg
    con ese bloque cambiado. Sin LLM disponible, o si el LLM no
    responde nada útil, devuelve None (el reintento se para y el
    clip queda retenido, nunca se publica con SEO < 90).

    Endurecimiento (spec títulos-verdad, §6.3/M7): prompt por
    idioma (ES/EN según el clip, no siempre español), con la lista
    de clichés prohibidos y el momento (descripción + fragmento de
    transcripción del payoff). La variante de título pasa los
    MISMOS checks que el primer paquete (validate_title_final,
    dedupe R4.1, patrón R4.2) antes de aceptarse; si no pasa,
    devuelve None. Los checks solo corren cuando hay contexto
    (moment_card y/o recent_titles): sin ficha no hay nada que
    verificar contra la transcripción y sin lista no hay dedupe
    que medir; el llamador que los tenga los pasa aquí (o en
    pkg["moment_card"] / pkg["recent_titles"]).
    """
    def variant(pkg, worst):
        if client is None or not client.available():
            return None
        pkg_lang = (pkg.get("lang") or lang or "es").lower()
        en = pkg_lang == "en"
        moment = moment_card or pkg.get("moment_card") or {}
        recent = (recent_titles if recent_titles is not None
                  else pkg.get("recent_titles"))
        cliches = banned_cliches("en" if en else "es")
        frag = _transcript_for_prompt(moment)[:400] if moment else ""
        title = pkg.get("title") or ""
        desc = pkg.get("description") or ""
        moment_txt = (desc[:200] if desc else "")
        if en:
            banned_txt = ("Banned phrases unless the transcript "
                          "shows them literally: "
                          + "; ".join(cliches) + ".")
            moment_line = (f"Moment: {moment_txt}."
                           if moment_txt else "")
            transcript_line = (f"Transcript: \"{frag}\"."
                               if frag else "")
        else:
            banned_txt = ("Frases prohibidas salvo que la "
                          "transcripción las muestre literales: "
                          + "; ".join(cliches) + ".")
            moment_line = (f"Momento: {moment_txt}."
                           if moment_txt else "")
            transcript_line = (f"Transcripción: \"{frag}\"."
                               if frag else "")
        if worst == "title" or worst == "coherence":
            if en:
                prompt = ("Write ONE new title in English for a "
                          "Short about GTA 6. It must start with "
                          "'GTA 6', be 40-55 characters long, and "
                          "name the concrete moment (what happens, "
                          "what object, what the streamer says). "
                          f"{moment_line} {transcript_line} "
                          f"{banned_txt} Current title: {title}. "
                          "Return ONLY the title, no quotes, no "
                          "explanation.")
            else:
                prompt = ("Escribe UN título nuevo en español para "
                          "un Short sobre GTA 6. Debe incluir "
                          "'GTA 6' al inicio, tener entre 40 y 55 "
                          "caracteres, y nombrar el momento concreto "
                          "(qué pasa, qué objeto, qué dice el "
                          "streamer). "
                          f"{moment_line} {transcript_line} "
                          f"{banned_txt} Título actual: {title}. "
                          "Devuelve SOLO el título, sin comillas ni "
                          "explicación.")
            new = _clean_title(client.generate(prompt))
            if not new or new == title:
                return None
            if moment or recent is not None:
                if not _variant_title_ok(new, moment, recent,
                                         lang=pkg_lang):
                    return None
            return dict(pkg, title=new)
        if worst == "description":
            if en:
                prompt = ("Write a new description in English (2-3 "
                          "sentences) for a Short about GTA 6 with "
                          f"this title: {title}. It must mention "
                          "GTA 6 and end with a question or "
                          "invitation tied to this concrete moment. "
                          f"{moment_line} {transcript_line} Return "
                          "ONLY the description, no quotes, no "
                          "explanation.")
            else:
                prompt = ("Escribe una descripción nueva en español "
                          "(2-3 frases) para un Short sobre GTA 6 "
                          f"con este título: {title}. Debe mencionar "
                          "GTA 6 y terminar con una pregunta o "
                          "invitación ligada a este momento concreto. "
                          f"{moment_line} {transcript_line} Devuelve "
                          "SOLO la descripción, sin comillas ni "
                          "explicación.")
            new = (client.generate(prompt) or "").strip()
            if not new or new == desc:
                return None
            return dict(pkg, description=new[:1000])
        if worst == "tags":
            if en:
                prompt = ("Return 5 hashtags in English for a Short "
                          f"about GTA 6 with this title: {title}. 4 "
                          "about the moment (streamer, object, "
                          "action) + #Shorts as the fifth. "
                          f"{moment_line} Only the hashtags "
                          "separated by spaces, nothing else.")
            else:
                prompt = ("Devuelve 5 hashtags en español para un "
                          f"Short sobre GTA 6 con este título: "
                          f"{title}. 4 sobre el momento (streamer, "
                          "objeto, acción) + #Shorts como quinto. "
                          f"{moment_line} Solo los hashtags "
                          "separados por espacios, nada más.")
            new = _clean_hashtags(client.generate(prompt))
            if not new or new == list(pkg.get("hashtags") or []):
                return None
            return dict(pkg, hashtags=new, tags=new)
        if worst == "thumbnail":
            if en:
                prompt = ("Write a short text (max 4 words) for the "
                          "thumbnail of a Short about GTA 6 with "
                          f"this title: {title}. The text must name "
                          "the concrete moment of this Short "
                          f"(use the moment: {moment_txt}; "
                          f"transcript: {frag}), not a generic "
                          "exclamation like 'WOW!' or 'UNREAL!'. "
                          "Return ONLY the text.")
            else:
                prompt = ("Escribe un texto corto (máx. 4 palabras) "
                          "para la miniatura de un Short sobre GTA 6 "
                          f"con este título: {title}. El texto tiene "
                          "que nombrar el momento concreto de este "
                          f"Short (usa el momento: {moment_txt}; "
                          f"transcripción: {frag}), no una "
                          "exclamación genérica como '¡QUÉ LOCURA!' "
                          "o '¡INCREÍBLE!'. Devuelve SOLO el texto.")
            new = _clean_title(client.generate(prompt))
            if not new:
                return None
            return dict(pkg, thumbnail_text=new)
        return None

    return variant


def make_seo_package(client, lang="es", pattern_pack=None):
    """Primer paquete SEO con el LLM (no solo el reintento).

    Pide al LLM el paquete COMPLETO (título, descripción, hashtags,
    texto de miniatura) desde la ficha del momento y devuelve
    generate(card, visible=None) -> dict | None. Sin LLM disponible,
    o si la respuesta no trae JSON válido, devuelve None y el
    llamador usa el paquete por reglas. La validación (check_title
    + lint + juego + SEO) la hace el llamador (metadata).

    ``pattern_pack`` (spec v3 §3.2) viaja al prompt como guía de
    estilo; generate() lo reenvía en cada llamada.

    R1.1: la transcripción viaja en la ficha (`transcript_text`)
    hasta el prompt sin truncar a 500 cuando el clip es corto (lo
    decide `_transcript_for_prompt` dentro de
    `_seo_package_prompt`).
    """
    def generate(card, visible=None):
        if client is None or not client.available():
            return None
        prompt = _seo_package_prompt(card or {}, visible=visible,
                                     lang=lang, pattern_pack=pattern_pack)
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

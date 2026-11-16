#!/usr/bin/env python3
"""
viral_package.py — empaquetado viral para cada clip.

Combina la lógica de Twitch Auto Clips v2 con las skills de
youtube-agent-skill (github.com/Jakeschincariol/youtube-agent-skill):

  * yt-viral  — no rankear por puntaje bruto, sino por MÚLTIPLO sobre la
    mediana del propio stream. Un pico de 30 pts en un chat callado vale
    más que uno de 30 en un chat que explota todo el rato.
    multiple = puntaje_ventana / mediana(puntajes). Solo así el ranking
    habla del momento, no del tamaño del chat.
  * yt-shorts — un Short no es "el mejor momento", es un momento que
    sobrevive SIN el video alrededor: abre con una idea completa,
    tiene un giro y termina en una frase, no en un balbuceo.
    Por eso cada clip lleva: primera línea nueva, texto en pantalla
    en los primeros 2 s (distinto de lo hablado) y punto de loop.
  * yt-package — título + miniatura son UNA unidad. Reglas que se
    verifican aquí: título ≤60 caracteres (40 en móvil), miniatura
    ≤3 palabras y SIN repetir el título, máx. 2 palabras en MAYÚSCULAS,
    y un número/nombre/fecha antes que un adjetivo.
  * yt-seo — descripción: las 2 primeras líneas son el snippet de
    búsqueda. Tags: máx. 15, solo para desambiguar. Y la prueba de
    las 3 búsquedas que el video debería ganar.
  * yt-retention — el leak del hook es en los primeros 15-30 s:
    la primera línea tiene que confirmar el título, subir una
    pregunta que no se pueda cerrar y probar que el payoff existe.

Nada de esto publica. Como en las skills: esto escribe y tú publicas.
Cada clip termina con la pregunta: ¿lo subo así, o lo cambio?

BLOQUE 2 (tribunal, 2026-11-16) — títulos reales + anti-clickbait:
la v2.4 generaba 3 títulos fijos con "GTA 6" hardcodeado, y el
título mentía en clips que no eran GTA 6 (Coker es GTA V RP, Locones
es Counter-Strike 2) y prometía una "jugada" que no existía. Desde
v2.6 el título sale de la ficha del momento (``moment_card.py``),
única fuente de verdad, SIN LLM en caliente:

  * ``generate_titles(moment_card, lang)`` — 10 títulos por clip,
    construidos con el juego real, la acción evidenciada, la frase
    literal y la reacción dominante de la ficha. Sin ficha no hay
    juego: el título no nombra ningún juego.
  * ``check_title(title, moment_card) -> (bool, motivo)`` —
    BLOQUEANTE. Falla si el juego del título no es el juego real,
    si el sustantivo prometido no aparece en transcripción/chat,
    si el payoff no ocurre dentro del clip, o si hay un superlativo
    sin evidencia (múltiplo/risas) que lo respalde. También falla
    el lint yt-package (≤60 car., ≤2 palabras en MAYÚSCULAS).
  * Anti-repetición: ningún título repetido en el mismo VOD ni el
    mismo día. El registro vive en ``titles_seen.json`` (gitignored)
    y solo se anota el título elegido (``best_title``), no los 10
    candidatos: registrar los 9 no usados envenenaría el registro.
  * Etiqueta de juego veraz: "GTA 6" solo si el juego real es GTA 6.
    Los tags y hashtags salen del juego real, no de #GTA6 fijo.

PARA LAS 4 PLATAFORMAS, NO SOLO YOUTUBE: las reglas de yt-package,
yt-seo, yt-shorts y yt-retention son las mismas ideas, pero cada
plataforma las aplica distinto. Un mismo clip lleva 4 paquetes:
  * YouTube Shorts — título + descripción + tags, miniatura = frame
    (por API no hay miniatura personalizada fiable), subtítulos SRT
    aparte además de quemados, métrica: visto vs deslizado.
  * TikTok — no hay título separado, el título ES el caption;
    caption corto, 3-5 hashtags, sin miniatura propia por API,
    subtítulos QUEMADOS obligatorios (no acepta SRT), modo borrador
    hasta pasar la auditoría, métrica: % finalización y compartidos.
  * Instagram Reels — caption + hashtags, SÍ admite portada
    (cover_url, ver thumbnails.py), subtítulos quemados, métrica:
    reproducciones y compartidos por DM. Requiere cuenta Profesional + Página de FB.
  * Facebook Reels — solo a Página, nunca a perfil; duración 3-90 s
    (nuestros 60 s caben justo), miniatura = frame, subtítulos
    quemados, métrica: reproducciones y compartidos.
El título, el hook y el texto en pantalla son los mismos en las 4;
lo que cambia es el caption, los hashtags, la portada y el modo de
subida. Nunca se copia el mismo texto literal en las 4.

Uso:
    from viral_package import package_clip, viral_multiple, package_for_platforms
    from moment_card import build_moment_card
    card = build_moment_card(clip_path=..., chat_path=..., srt_path=...,
                             streamer="coker", game="GTA V RP", ...)
    pkg = package_clip(n=1, peak_ts=11710, score=56.5, risas=14, hype=1,
                       median_score=1.0, all_scores=[],
                       lang="es", moment_card=card, register=True)
    check_title("GTA 6: La jugada que rompió el chat", card)
    # (False, "el título dice 'GTA 6' pero el juego real es 'GTA V RP'; ...")
"""

import json
import re
import statistics
import unicodedata
from datetime import date as _date
from pathlib import Path

# Fórmulas de hook de yt-script/hooks.json, adaptadas a clips de GTA 6.
# Solo las que tienen sentido para un clip de 60 s sin guion previo.
HOOK_FORMULAS = {
    "the-reveal": "The Reveal — promete algo concreto y lo muestra.",
    "the-superlative": "The Superlative — el mejor/peor/más rápido de su tipo.",
    "the-mistake": "The Mistake — nombra el error que el espectador está cometiendo.",
    "the-statistic": "The Statistic — un número que no sabías, luego la consecuencia.",
    "contrarian-flip": "Contrarian Flip — lo que todos dicen, y por qué está mal.",
}


# Reglas por plataforma (Agente 5/10 del tribunal). Los límites de
# subida son orientativos y cambian; lo que no cambia es el formato.
PLATFORMS = {
    "youtube_shorts": {
        "label": "YouTube Shorts",
        "title_max": 60, "title_mobile_max": 40,
        "caption_max": None, "hashtags": 0,  # hashtags van en descripción, no en caption
        "tags_max": 15,
        "thumbnail": "frame del pico (por API no hay miniatura personalizada fiable)",
        "subtitles": "SRT aparte (captions.insert) + quemados en el video",
        "duration": "hasta 60 s (nuestros clips), Shorts admite más",
        "daily_limit": "~100 subidas/día por proyecto",
        "publish_mode": "API oficial (videos.insert) — 100% automatizable",
        "metric": "visto vs deslizado, duración media vista, CTR",
    },
    "tiktok": {
        "label": "TikTok",
        "title_max": 150,  # el caption es el título: corto, lo que se ve en el feed
        "title_mobile_max": 150,
        "caption_max": 150, "hashtags": 5,
        "tags_max": 0,
        "thumbnail": "sin miniatura propia por API — manda el primer frame",
        "subtitles": "QUEMADOS obligatorios (no acepta SRT por API)",
        "duration": "hasta 10 min, nuestros 60 s bien",
        "daily_limit": "~15/día por creador",
        "publish_mode": "borrador por API hasta pasar auditoría (1-4 sem.); Direct Post solo tras auditoría",
        "metric": "% finalización, compartidos, guardados, replays",
    },
    "instagram_reels": {
        "label": "Instagram Reels",
        "title_max": None, "title_mobile_max": None,
        "caption_max": 2200, "hashtags": 10,
        "tags_max": 0,
        "thumbnail": "SÍ admite portada (cover_url) — usar el texto de miniatura",
        "subtitles": "QUEMADOS obligatorios (no acepta SRT por API)",
        "duration": "hasta 90 s en Reels clásicos, nuestros 60 s bien",
        "daily_limit": "~50 publicaciones/24 h por cuenta",
        "publish_mode": "Graph API (contenedor → publicar) — requiere cuenta Profesional + Página de FB",
        "metric": "reproducciones, % finalización, compartidos por DM, guardados",
    },
    "facebook_reels": {
        "label": "Facebook Reels",
        "title_max": 60, "title_mobile_max": 40,
        "caption_max": None, "hashtags": 5,
        "tags_max": 0,
        "thumbnail": "frame del pico (sin miniatura personalizada)",
        "subtitles": "QUEMADOS obligatorios (no acepta SRT por API)",
        "duration": "3-90 s OBLIGATORIO — nuestros 60 s caben justo",
        "daily_limit": "30 Reels/24 h por Página",
        "publish_mode": "Graph API a Página, nunca a perfil personal",
        "metric": "reproducciones, % finalización, compartidos",
    },
}

# ---------------------------------------------------------------------------
# Bloque 2 — juego veraz, superlativos, sustantivos, anti-repetición
# ---------------------------------------------------------------------------

# Alias normalizados -> juego canónico. Un título que nombre un juego
# solo pasa si el juego real de la ficha es ese (o uno de sus alias).
# "GTA V RP" acepta "GTA V"/"GTA 5" como alias, pero NUNCA "GTA 6".
KNOWN_GAMES = {
    "gta 6": "GTA 6", "gta vi": "GTA 6",
    "gta v rp": "GTA V RP", "gta 5 rp": "GTA V RP", "gta v": "GTA V",
    "gta 5": "GTA V", "gta online": "GTA V",
    "counter-strike 2": "Counter-Strike 2", "counter strike 2": "Counter-Strike 2",
    "cs2": "Counter-Strike 2", "cs:go": "Counter-Strike 2",
    "fortnite": "Fortnite", "valorant": "Valorant",
    "minecraft": "Minecraft", "roblox": "Roblox",
    "league of legends": "League of Legends", "lol": "League of Legends",
    "call of duty": "Call of Duty", "warzone": "Call of Duty",
}
# Qué alias acepta cada juego real como "el mismo juego" en un título.
GAME_ALIASES = {
    "GTA 6": {"gta 6", "gta vi"},
    "GTA V RP": {"gta v rp", "gta 5 rp", "gta v", "gta 5"},
    "GTA V": {"gta v", "gta 5"},
    "Counter-Strike 2": {"counter-strike 2", "counter strike 2", "cs2"},
    "Fortnite": {"fortnite"}, "Valorant": {"valorant"},
    "Minecraft": {"minecraft"}, "Roblox": {"roblox"},
    "League of Legends": {"league of legends", "lol"},
    "Call of Duty": {"call of duty", "warzone"},
}

# Hashtags y tags por juego real. Sin juego conocido: genéricos, sin #GTA6.
GAME_HASHTAGS = {
    "GTA 6": (["#GTA6", "#GTA6Clips", "#ElClipDeGTA6", "#GTA", "#RockstarGames"],
              ["GTA 6", "GTA VI", "clips GTA 6", "GTA 6 gameplay", "Rockstar Games"]),
    "GTA V RP": (["#GTAV", "#GTARP", "#GTA5", "#Roleplay", "#GTA"],
                 ["GTA V RP", "GTA 5 RP", "GTA V roleplay", "GTA 5 gameplay", "Rockstar Games"]),
    "GTA V": (["#GTAV", "#GTA5", "#GTA", "#RockstarGames"],
              ["GTA V", "GTA 5", "GTA 5 gameplay", "Rockstar Games"]),
    "Counter-Strike 2": (["#CS2", "#CounterStrike2", "#CSGO", "#Gaming"],
                         ["Counter-Strike 2", "CS2", "CS2 clips", "Counter Strike gameplay"]),
}
GENERIC_HASHTAGS = (["#Gaming", "#Clips", "#Directo", "#StreamClips"],
                    ["clips gaming", "stream clips", "gameplay", "momentos gaming"])

# Superlativos/absolutos y la evidencia mínima que los respalda.
# multiple = viral_multiple de la ficha; risas/hype = conteos del pico.
SUPERLATIVES = {
    # palabra -> (condición que la respalda, por qué)
    "mejor": ("multiple>=5 o risas>=10", lambda c: (c.get("viral_multiple") or 0) >= 5 or (c.get("risas") or 0) >= 10),
    "peor": ("multiple>=5 o risas>=10", lambda c: (c.get("viral_multiple") or 0) >= 5 or (c.get("risas") or 0) >= 10),
    "loco": ("multiple>=5", lambda c: (c.get("viral_multiple") or 0) >= 5),
    "locura": ("multiple>=5", lambda c: (c.get("viral_multiple") or 0) >= 5),
    "insano": ("multiple>=5", lambda c: (c.get("viral_multiple") or 0) >= 5),
    "increíble": ("multiple>=5", lambda c: (c.get("viral_multiple") or 0) >= 5),
    "increible": ("multiple>=5", lambda c: (c.get("viral_multiple") or 0) >= 5),
    "épico": ("multiple>=5", lambda c: (c.get("viral_multiple") or 0) >= 5),
    "epico": ("multiple>=5", lambda c: (c.get("viral_multiple") or 0) >= 5),
    "brutal": ("multiple>=5", lambda c: (c.get("viral_multiple") or 0) >= 5),
    "imposible": ("multiple>=10 y risas>=5", lambda c: (c.get("viral_multiple") or 0) >= 10 and (c.get("risas") or 0) >= 5),
    "roto": ("multiple>=10 y (risas>=5 o hype>=5 o usuarios>=50)",
             lambda c: (c.get("viral_multiple") or 0) >= 10 and ((c.get("risas") or 0) >= 5 or (c.get("hype") or 0) >= 5 or (c.get("unique_users_peak_30s") or 0) >= 50)),
    "rompió": ("multiple>=10 y (risas>=5 o hype>=5 o usuarios>=50)",
               lambda c: (c.get("viral_multiple") or 0) >= 10 and ((c.get("risas") or 0) >= 5 or (c.get("hype") or 0) >= 5 or (c.get("unique_users_peak_30s") or 0) >= 50)),
    "nadie": ("usuarios>=30 o mensajes del pico>=100",
              lambda c: (c.get("unique_users_peak_30s") or 0) >= 30 or (c.get("chat_msgs_peak_10s") or 0) >= 100),
    "nunca": ("usuarios>=30 o mensajes del pico>=100",
              lambda c: (c.get("unique_users_peak_30s") or 0) >= 30 or (c.get("chat_msgs_peak_10s") or 0) >= 100),
    "todos": ("usuarios>=30 o mensajes del pico>=100",
              lambda c: (c.get("unique_users_peak_30s") or 0) >= 30 or (c.get("chat_msgs_peak_10s") or 0) >= 100),
    "siempre": ("usuarios>=30 o mensajes del pico>=100",
                lambda c: (c.get("unique_users_peak_30s") or 0) >= 30 or (c.get("chat_msgs_peak_10s") or 0) >= 100),
}
SUPERLATIVES_EN = {
    "best": ("multiple>=5 o risas>=10", lambda c: (c.get("viral_multiple") or 0) >= 5 or (c.get("risas") or 0) >= 10),
    "worst": ("multiple>=5 o risas>=10", lambda c: (c.get("viral_multiple") or 0) >= 5 or (c.get("risas") or 0) >= 10),
    "craziest": ("multiple>=5", lambda c: (c.get("viral_multiple") or 0) >= 5),
    "insane": ("multiple>=5", lambda c: (c.get("viral_multiple") or 0) >= 5),
    "epic": ("multiple>=5", lambda c: (c.get("viral_multiple") or 0) >= 5),
    "broke": ("multiple>=10 y (risas>=5 o hype>=5 o usuarios>=50)",
              lambda c: (c.get("viral_multiple") or 0) >= 10 and ((c.get("risas") or 0) >= 5 or (c.get("hype") or 0) >= 5 or (c.get("unique_users_peak_30s") or 0) >= 50)),
    "nobody": ("usuarios>=30 o mensajes del pico>=100",
               lambda c: (c.get("unique_users_peak_30s") or 0) >= 30 or (c.get("chat_msgs_peak_10s") or 0) >= 100),
    "everyone": ("usuarios>=30 o mensajes del pico>=100",
                 lambda c: (c.get("unique_users_peak_30s") or 0) >= 30 or (c.get("chat_msgs_peak_10s") or 0) >= 100),
    "ever": ("usuarios>=30 o mensajes del pico>=100",
             lambda c: (c.get("unique_users_peak_30s") or 0) >= 30 or (c.get("chat_msgs_peak_10s") or 0) >= 100),
}

# Sustantivo prometido -> tokens que deben aparecer en la evidencia
# (transcripción + chat + what de la ficha). Si el título promete un
# "penal" y nadie dijo "penal", el título miente.
NOUN_EVIDENCE = {
    "jugada": ["jugada", "jugar", "jugo", "jugó"],
    "tiro": ["tiro", "dispar", "erra", "patea"],
    "disparo": ["dispar", "tiro"],
    "penal": ["penal", "penalti"],
    "gol": ["gol"],
    "partido": ["partido"],
    "muerte": ["muere", "murio", "mata", "mato"],
    "robo": ["robo", "roba", "robo"],
    "coche": ["coche", "auto", "carro"],
    "clutch": ["clutch"],
    "risa": ["risa", "jaja", "kekw", "lol"],
    "reacción": ["reaccion", "no"],
    "reaccion": ["reaccion", "no"],
    "play": ["play", "jugada", "clutch"],
    "shot": ["shot", "tiro", "dispar"],
    "goal": ["gol", "goal"],
}
# Palabras que un título puede usar sin evidencia: son el medio, no la promesa.
GENERIC_WORDS = set(
    "chat directo stream streamer clip video momento segundos mensajes "
    "risa risas hype reacción reaccion explotó exploto perdió perdona "
    "nadie esperaba".split()
)

# ``what`` de la ficha viene en español (verbo+objeto); para los títulos
# EN se usa esta equivalencia fija, sin traducir con LLM. Si la acción
# no tiene equivalente aquí, los títulos EN no usan ``what``.
WHAT_EN = {
    "erra": "missing a shot", "patea": "missing a kick",
    "dispara": "shooting", "mata": "getting a kill",
    "muere": "dying in game", "gana": "winning the match",
    "pierde": "losing the match", "roba": "stealing something",
    "choca": "crashing the car", "cae": "falling",
    "gol": "scoring a goal", "penal": "a penalty",
    "clutch": "winning a clutch", "falla": "missing",
}

DEFAULT_SEEN_PATH = Path(__file__).with_name("titles_seen.json")


def _norm_title(title: str) -> str:
    """Normalización para comparar títulos (sin tildes, emojis ni puntuación)."""
    text = unicodedata.normalize("NFKD", title or "")
    text = "".join(c for c in text if not unicodedata.combining(c))
    text = re.sub(r"[^\w ]", " ", text.lower())
    return re.sub(r"\s+", " ", text).strip()


def _today_iso():
    return _date.today().isoformat()


def load_seen(seen_path=None):
    """Registro anti-repetición: {título normalizado: [{vod_id, date, ...}]}."""
    path = Path(seen_path) if seen_path else DEFAULT_SEEN_PATH
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data.get("titles", {}) if isinstance(data, dict) else {}
    except (json.JSONDecodeError, OSError):
        return {}


def save_seen(seen, seen_path=None):
    path = Path(seen_path) if seen_path else DEFAULT_SEEN_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"titles": seen}, ensure_ascii=False, indent=1),
                    encoding="utf-8")
    return path


def is_duplicate_title(title, vod_id=None, date=None, seen=None, seen_path=None):
    """True si el título ya se usó en el mismo VOD o el mismo día."""
    seen = seen if seen is not None else load_seen(seen_path)
    uses = seen.get(_norm_title(title), [])
    for u in uses:
        if vod_id and u.get("vod_id") == vod_id:
            return True
        if date and u.get("date") == date:
            return True
    return False


def register_title(title, vod_id=None, date=None, seen_path=None,
                   clip=None, lang=None):
    """Anota un título como usado (solo el elegido, no los candidatos)."""
    seen = load_seen(seen_path)
    key = _norm_title(title)
    uses = seen.setdefault(key, [])
    entry = {"title": title, "vod_id": vod_id, "date": date or _today_iso(),
             "clip": clip, "lang": lang}
    if entry not in uses:
        uses.append(entry)
    save_seen(seen, seen_path)
    return entry


def game_hashtags_tags(game):
    """(hashtags, tags) del juego real; genéricos si no se conoce."""
    if game in GAME_HASHTAGS:
        return GAME_HASHTAGS[game]
    return GENERIC_HASHTAGS


def _evidence_text(card):
    parts = []
    for k in ("transcript_text", "what"):
        if card.get(k):
            parts.append(str(card[k]))
    for m in card.get("chat_top", []) or []:
        parts.append(str(m.get("text", "")))
    if (card.get("quote_chat") or {}).get("text"):
        parts.append(card["quote_chat"]["text"])
    if (card.get("quote_streamer") or {}).get("text"):
        parts.append(card["quote_streamer"]["text"])
    return " " + " ".join(parts) + " "


def _check_title_lint(title):
    issues = []
    if len(title) > 60:
        issues.append(f"largo ({len(title)} car., se corta en escritorio a los 60)")
    if len(title) > 40:
        issues.append(f"se corta en móvil a los 40 ({len(title)} car.)")
    caps = [w for w in title.split() if w.isupper() and len(w) > 1]
    if len(caps) > 2:
        issues.append(f"{len(caps)} palabras en MAYÚSCULAS, el tope es 2")
    return issues


def check_title_detail(title, moment_card):
    """Check promesa-payoff BLOQUEANTE. Devuelve dict con ok/reasons.

    Falla si: sin ficha; juego del título != juego real; sustantivo
    prometido sin evidencia en transcripción/chat; payoff fuera del
    clip o evidencia baja con afirmación concreta; superlativo sin
    evidencia; o lint yt-package (largo, MAYÚSCULAS).
    """
    reasons = []
    if not title or not str(title).strip():
        return {"ok": False, "reasons": ["título vacío"], "warnings": [], "title": title}
    if not moment_card:
        return {"ok": False,
                "reasons": ["sin ficha del momento: el título no se puede verificar"],
                "warnings": [], "title": title}
    card = moment_card
    low = _norm_title(title)
    game = card.get("game")  # None = desconocido, no se asume ninguno

    # 1. Lint yt-package. Bloqueante: >60 car. y >2 palabras en MAYÚSCULAS.
    # >40 car. es solo aviso ("se corta en móvil", preferible, no bloqueo):
    # con un prefijo de juego como "Counter-Strike 2: " casi todo pasa
    # de 40 y seguiría siendo un título válido de escritorio.
    warnings = []
    for issue in _check_title_lint(title):
        if issue.startswith("se corta en móvil"):
            warnings.append(f"lint: {issue}")
        else:
            reasons.append(f"lint: {issue}")

    # 2. Juego veraz: cualquier juego nombrado debe ser el juego real.
    found_games = [alias for alias in KNOWN_GAMES if alias in low]
    # "gta" a secas sin número: solo vale si el juego real es algún GTA.
    if "gta" in low and not found_games:
        if not (game or "").lower().startswith("gta"):
            reasons.append("el título dice 'GTA' pero el juego real "
                           f"es '{card.get('game_label')}'")
    for alias in found_games:
        canonical = KNOWN_GAMES[alias]
        allowed = GAME_ALIASES.get(game or "", set())
        if alias not in allowed:
            reasons.append(f"el título dice '{canonical}' pero el juego real "
                           f"es '{card.get('game_label')}'")
    # Un título sin juego nombrado nunca falla por juego; si el juego es
    # desconocido y el título nombra uno, ya falló arriba.

    # 3. Sustantivo prometido: debe aparecer en transcripción/chat/ficha.
    evidence = _norm_title(_evidence_text(card))
    evidence_tokens = set(card.get("evidence_tokens") or [])
    for noun, tokens in NOUN_EVIDENCE.items():
        if re.search(r"\b" + re.escape(noun) + r"\b", low):
            if noun in GENERIC_WORDS:
                continue
            hit = any(t in evidence or t in evidence_tokens for t in tokens)
            if not hit:
                reasons.append(f"el título promete '{noun}' pero no aparece "
                               f"en transcripción/chat/ficha")

    # 4. Payoff dentro del clip + evidencia suficiente para afirmar algo.
    payoff = card.get("payoff_ts_clip")
    duration = card.get("clip_duration_s") or 60
    if payoff is None:
        reasons.append("sin payoff conocido: no se sabe si el momento ocurre dentro del clip")
    elif not (0 <= payoff <= duration):
        reasons.append(f"el payoff ({payoff} s) cae fuera del clip (0-{duration} s)")
    if card.get("evidence") == "low":
        # Evidencia baja: no se puede prometer qué pasa (sustantivos
        # concretos). Nombrar el juego real NO se bloquea aquí: el juego
        # viene del metadato del VOD, no de la evidencia del clip.
        specific = [n for n in NOUN_EVIDENCE
                    if re.search(r"\b" + re.escape(n) + r"\b", low) and n not in GENERIC_WORDS]
        if specific:
            reasons.append("evidencia baja: la ficha no sabe qué pasa, "
                           f"no se puede prometer {specific}")

    # 5. Superlativos/absolutos solo con evidencia (múltiplo/risas).
    sup = dict(SUPERLATIVES)
    if card.get("language") == "en":
        sup.update(SUPERLATIVES_EN)
    else:
        sup.update(SUPERLATIVES_EN)  # los títulos EN se chequean igual
    for word, (need, ok_fn) in sup.items():
        if re.search(r"\b" + re.escape(word) + r"\b", low):
            try:
                supported = bool(ok_fn(card))
            except Exception:
                supported = False
            if not supported:
                reasons.append(f"superlativo '{word}' sin evidencia "
                               f"(hace falta {need}; múltiple "
                               f"{card.get('viral_multiple')}, risas "
                               f"{card.get('risas')}, hype {card.get('hype')}, "
                               f"usuarios {card.get('unique_users_peak_30s')})")
    return {"ok": not reasons, "reasons": reasons, "warnings": warnings, "title": title}


def check_title(title, moment_card):
    """Check promesa-payoff BLOQUEANTE -> (bool, motivo).

    (True, "pasa: ...") si el título se puede publicar; si no,
    (False, "motivo1; motivo2") con cada bloqueo.
    """
    detail = check_title_detail(title, moment_card)
    if detail["ok"]:
        return True, "pasa: juego veraz, sustantivo con evidencia, payoff dentro del clip, sin superlativos sin respaldo"
    return False, "; ".join(detail["reasons"])


def _check_title(title: str) -> list[str]:
    """Lint yt-package (compat: lo usaba la v2.4). Ver check_title para el bloqueo real."""
    return _check_title_lint(title)


# ---------------------------------------------------------------------------
# Generación de títulos desde la ficha (10 por clip, sin plantillas fijas)
# ---------------------------------------------------------------------------

def _title_quote(text, max_len=28):
    """Frase literal lista para un título: recortada, en minúsculas
    salvo la primera letra (el chat grita en MAYÚSCULAS y eso rompería
    el lint de ≤2 palabras en mayúsculas) y sin el token final de
    tecleo (\"jasdjasjd\": 3+ consonantes seguidas, no es una palabra).
    Las palabras se conservan literales; solo cambia la caja."""
    text = _short(text, max_len).strip().strip('"')
    words = text.split()
    if words and re.search(r"[bcdfghjklmnpqrstvwxz]{3,}", words[-1].lower()):
        words = words[:-1]
    text = " ".join(words).lower()
    return text[:1].upper() + text[1:] if text else text


def _short(text, max_len=34):
    """Recorta una frase literal sin romperla a mitad de palabra."""
    text = (text or "").strip()
    if len(text) <= max_len:
        return text
    cut = text[:max_len].rsplit(" ", 1)[0]
    return cut or text[:max_len]


def _cap_first(text):
    return text[:1].upper() + text[1:] if text else text


def generate_titles(moment_card, lang="es", n=10, seen=None, seen_path=None,
                    vod_id=None, date=None):
    """10 títulos por clip desde la ficha (no 2 plantillas fijas).

    Solo devuelve títulos que pasan ``check_title`` y el lint, y que
    no están repetidos en el mismo VOD ni el mismo día. Si pasan
    menos de ``n``, devuelve los que pasan y el llamador lo ve en
    ``len(titles)``: mejor pocos títulos reales que 10 inventados.
    Cada título lleva su check: {"title", "issues", "check_ok", "check_reason"}.
    """
    card = moment_card or {}
    game = card.get("game")
    game_label = card.get("game_label") or "desconocido"
    streamer = card.get("streamer") or ""
    streamer = "" if streamer == "desconocido" else streamer
    what = card.get("what")  # verbo+objeto, o None si no hay evidencia
    reaction = card.get("dominant_reaction") or ""
    risas = card.get("risas") or 0
    hype = card.get("hype") or 0
    multiple = card.get("viral_multiple")
    users = card.get("unique_users_peak_30s") or 0
    msgs = card.get("chat_msgs_peak_10s") or 0
    quote_chat = (card.get("quote_chat") or {}).get("text") or ""
    quote_chat_count = (card.get("quote_chat") or {}).get("count") or 0
    # Una cita del chat con 1 sola mención no es "el chat": es un usuario.
    # Solo se cita si al menos 2 mensajes dicen lo mismo.
    if quote_chat_count < 2:
        quote_chat = ""
    quote_streamer = (card.get("quote_streamer") or {}).get("text") or ""
    evidence_low = card.get("evidence") == "low"
    game_prefix = f"{game_label}: " if game else ""
    seen = seen if seen is not None else load_seen(seen_path)
    vod = vod_id or card.get("vod_id")
    day = date or _today_iso()

    candidates = []
    what_en = WHAT_EN.get(card.get("what_key")) if lang == "en" else None
    if lang == "en":
        if quote_chat:
            candidates.append(f"{game_prefix}Chat said it: \"{_title_quote(quote_chat, 30)}\"")
        if what_en:
            candidates.append(f"{game_prefix}{_cap_first(what_en)} and chat lost it")
            candidates.append(f"Nobody expected {what_en}")
        if reaction == "risas":
            candidates.append(f"{game_prefix}Chat couldn't stop laughing")
            candidates.append(f"{risas} laughs in 10 seconds" if risas >= 5 else f"{game_prefix}Everyone laughed at this")
        if reaction == "hype":
            candidates.append(f"{game_prefix}Chat went crazy for this")
        if reaction == "volumen":
            candidates.append(f"{msgs} messages in 10 seconds over this")
            candidates.append(f"{game_prefix}Chat exploded over this")
        if multiple and multiple >= 5:
            candidates.append(f"{multiple}x the median: {game_label}")
        if quote_streamer:
            candidates.append(f"{streamer + ': ' if streamer else ''}\"{_title_quote(quote_streamer, 30)}\"")
        if what_en:
            candidates.append(f"{_cap_first(what_en)} — watch chat react")
        candidates.append(f"{game_prefix}The moment chat clipped")
        candidates.append(f"{game_prefix}Watch what chat reacted to")
        candidates.append(f"{streamer + ' broke chat' if streamer else 'This broke chat'}" if reaction in ("risas", "hype") else f"{game_prefix}Chat reacted to this")
    else:
        if quote_chat:
            candidates.append(f"{game_prefix}El chat: \"{_title_quote(quote_chat, 28)}\"")
        if what:
            candidates.append(f"{game_prefix}{_cap_first(what)} y el chat explotó")
            candidates.append(f"{_cap_first(what)} que nadie se creyó")
        if reaction == "risas":
            candidates.append(f"{game_prefix}El chat no paraba de reírse")
            candidates.append(f"{risas} risas en 10 segundos" if risas >= 5 else f"{game_prefix}Se murieron de risa")
            candidates.append("El chat se murió de risa con esto")
        if reaction == "hype":
            candidates.append(f"{game_prefix}El chat enloqueció con esto")
        if reaction == "volumen":
            candidates.append(f"{msgs} mensajes en 10 segundos por esto")
            candidates.append(f"{game_prefix}El chat explotó con esto")
        if multiple and multiple >= 5:
            candidates.append(f"{multiple}x la mediana: {game_label}")
        if quote_streamer:
            candidates.append(f"{streamer + ': ' if streamer else ''}\"{_title_quote(quote_streamer, 30)}\"")
        if what:
            candidates.append(f"{_cap_first(what)}: mira la reacción")
        candidates.append(f"{game_prefix}El momento que el chat clippeó")
        candidates.append(f"{game_prefix}Mira por qué reaccionó el chat")
        if reaction in ("risas", "hype"):
            candidates.append(f"{streamer + ' hizo reír al chat' if streamer else 'Esto hizo reír al chat'}")

    # Con evidencia baja no se promete nada concreto: solo reacción/volumen.
    if evidence_low:
        candidates = [t for t in candidates
                      if not re.search(r"\b(jugada|tiro|penal|gol|partido|clutch|robo)\b",
                                       _norm_title(t))]

    out, seen_norm = [], set()
    for t in candidates:
        if len(out) >= n:
            break
        norm = _norm_title(t)
        if not t or norm in seen_norm or len(t) > 60:
            continue
        if is_duplicate_title(t, vod_id=vod, date=day, seen=seen):
            continue
        ok, reason = check_title(t, card)
        if not ok:
            continue
        seen_norm.add(norm)
        detail = check_title_detail(t, card)
        out.append({"title": t, "issues": _check_title_lint(t),
                    "warnings": detail.get("warnings", []),
                    "check_ok": True, "check_reason": reason})
    return out


def viral_multiple(score: int, all_scores, median_score=None) -> float:
    """Múltiplo sobre la mediana del stream (yt-viral).

    v2.4 (auditoría, bug 6): si se pasa ``median_score`` se usa ese
    valor — el llamador debe calcularlo sobre TODAS las ventanas,
    incluidas las vacías (ver ``clips_chat_v2.median_over_all``).
    Antes el parámetro existía pero se ignoraba y la mediana se
    calculaba solo sobre las ventanas con mensajes, sesgándola.
    """
    if median_score is not None and median_score > 0:
        return round(score / median_score, 2)
    if not all_scores:
        return 0.0
    median = statistics.median(all_scores)
    if median <= 0:
        return float(score)
    return round(score / median, 2)


def package_for_platforms(best_title, desc, tags, thumb, lang="both",
                          hashtags=None, game=None):
    """Un paquete por plataforma a partir del paquete base.

    Misma idea, mismo hook y mismo texto en pantalla en las 4;
    cambia caption, hashtags, portada y modo de subida.
    Los hashtags salen del juego real (``game``); sin juego, genéricos.
    """
    if hashtags is None:
        hashtags, _ = game_hashtags_tags(game)
    hashtags_es = hashtags
    hashtags_en = hashtags
    hashtags = hashtags_en if lang == "en" else hashtags_es
    first_line = desc.split("\n")[0]
    out = {}
    for key, rules in PLATFORMS.items():
        if key == "youtube_shorts":
            caption = f"{best_title}\n\n{desc}\n\n{' '.join(hashtags)}" if best_title else f"{desc}\n\n{' '.join(hashtags)}"
            out[key] = {
                "platform": rules["label"], "title": best_title,
                "caption": caption, "hashtags": hashtags,
                "tags": tags[: rules["tags_max"]],
                "thumbnail": rules["thumbnail"], "thumbnail_text": thumb,
                "subtitles": rules["subtitles"],
                "duration_rule": rules["duration"],
                "daily_limit": rules["daily_limit"],
                "publish_mode": rules["publish_mode"],
                "metric": rules["metric"],
            }
        elif key == "tiktok":
            # TikTok: caption corto = título + hashtags, sin descripción larga.
            caption = f"{best_title} {' '.join(hashtags[:5])}" if best_title else f"{first_line} {' '.join(hashtags[:5])}"
            if len(caption) > 150:
                caption = caption[:147] + "..."
            out[key] = {
                "platform": rules["label"], "title": None,
                "caption": caption, "hashtags": hashtags[:5],
                "tags": [],
                "thumbnail": rules["thumbnail"], "thumbnail_text": None,
                "subtitles": rules["subtitles"],
                "duration_rule": rules["duration"],
                "daily_limit": rules["daily_limit"],
                "publish_mode": rules["publish_mode"],
                "metric": rules["metric"],
            }
        elif key == "instagram_reels":
            caption = f"{first_line}\n\n{' '.join(hashtags[:10])}"
            out[key] = {
                "platform": rules["label"], "title": None,
                "caption": caption, "hashtags": hashtags[:10],
                "tags": [],
                "thumbnail": rules["thumbnail"], "thumbnail_text": thumb,
                "subtitles": rules["subtitles"],
                "duration_rule": rules["duration"],
                "daily_limit": rules["daily_limit"],
                "publish_mode": rules["publish_mode"],
                "metric": rules["metric"],
            }
        else:  # facebook_reels
            caption = f"{best_title}\n\n{first_line}\n\n{' '.join(hashtags[:5])}" if best_title else f"{first_line}\n\n{' '.join(hashtags[:5])}"
            out[key] = {
                "platform": rules["label"], "title": best_title,
                "caption": caption, "hashtags": hashtags[:5],
                "tags": [],
                "thumbnail": rules["thumbnail"], "thumbnail_text": None,
                "subtitles": rules["subtitles"],
                "duration_rule": rules["duration"],
                "daily_limit": rules["daily_limit"],
                "publish_mode": rules["publish_mode"],
                "metric": rules["metric"],
            }
    return out


def package_clip(n, peak_ts, score, risas, hype, median_score, all_scores,
                 lang="both", examples=None, moment_card=None, game=None,
                 streamer=None, seen_path=None, vod_id=None, date=None,
                 register=False):
    """Devuelve el paquete viral completo de un clip.

    Con ``moment_card`` (recomendado, Bloque 2): los 10 títulos, el
    hook, la descripción, los tags y los hashtags salen de la ficha;
    ``best_title`` es el primer título que pasa el check promesa-payoff
    y no está repetido en el mismo VOD ni el mismo día. Sin ficha, el
    juego es desconocido y ningún título nombra un juego: el paquete
    queda marcado ``needs_human_review`` si ningún título pasa.
    Con ``register=True`` se anota el ``best_title`` en titles_seen.json.
    """
    examples = examples or []
    multiple = viral_multiple(score, all_scores, median_score=median_score)
    card = dict(moment_card) if moment_card else {}
    if card:
        # La ficha manda: sus números son los del pico real.
        multiple = card.get("viral_multiple") or multiple
        risas = card.get("risas", risas)
        hype = card.get("hype", hype)
        game = card.get("game") or game
        streamer = card.get("streamer") or streamer
        vod_id = vod_id or card.get("vod_id")
    reaction = card.get("dominant_reaction") if card else None
    if not reaction:
        reaction = "risas" if risas >= hype else "hype"
    is_hype = reaction == "hype"
    formula = "the-superlative" if is_hype else ("the-statistic" if reaction == "volumen" else "the-reveal")

    game_label = game or "desconocido"
    hashtags, tags = game_hashtags_tags(game)
    tags = (tags + ["clips gaming", "stream clips"])[:15]

    # Títulos desde la ficha (10, con check). Sin ficha: ficha mínima
    # con evidencia low, para que check_title pueda bloquear lo inventado.
    check_card = card or {
        "game": None, "game_label": "desconocido",
        "game_source": "sin ficha del momento",
        "evidence": "low", "evidence_notes": ["sin ficha del momento"],
        "evidence_tokens": [], "transcript_text": "",
        "what": None, "what_key": None, "dominant_reaction": reaction,
        "risas": risas, "hype": hype, "viral_multiple": multiple,
        # Sin ficha no se sabe dónde cae el payoff, pero el corte estándar
        # es pre-roll 20 s + 60 s: el pico queda a los 20 s del clip.
        # Si el pre-roll cambia, la ficha (moment_card) es la que manda.
        "payoff_ts_clip": 20.0, "clip_duration_s": 60,
        "unique_users_peak_30s": None, "chat_msgs_peak_10s": None,
        "quote_chat": None, "quote_streamer": None, "chat_top": [],
        "streamer": streamer or "desconocido", "language": lang,
    }
    titles = generate_titles(check_card, lang=lang, n=10,
                             seen_path=seen_path, vod_id=vod_id, date=date)
    best = titles[0]["title"] if titles else None
    if register and best:
        register_title(best, vod_id=vod_id, date=date, seen_path=seen_path,
                       clip=f"clip_{n:02d}", lang=lang)

    what = card.get("what") if card else None
    quote_streamer = (card.get("quote_streamer") or {}).get("text") if card else None
    if lang == "en":
        thumb = "NO WAY" if reaction == "risas" else ("WAIT FOR IT" if is_hype else "CHAT REACTION")
        hook = (f"Watch why chat lost it here." if reaction == "risas"
                else "Watch this moment — chat went crazy in seconds.")
        onscreen = "CHAT REACTION"
        loop = "And that's why everyone clipped it."
        if what and game:
            desc = (f"{game}: {what} — chat reacted live.\n"
                    "Full clip, vertical, no context needed.")
        elif game:
            desc = (f"{game}: the moment chat reacted to.\n"
                    "Full clip, vertical, no context needed.")
        else:
            desc = ("The moment chat reacted to — game not verified.\n"
                    "Full clip, vertical, no context needed.")
        queries = [f"{game_label.lower()} best moments", f"{game_label.lower()} funny clips"] if game else ["funny stream clips", "chat reaction clips"]
    else:
        thumb = "NO PUEDE SER" if reaction == "risas" else ("ESPERA EL FINAL" if is_hype else "LA REACCIÓN")
        hook = ("Mira por qué el chat se murió de risa acá." if reaction == "risas"
                else "Mira este momento, el chat explotó en segundos.")
        onscreen = "LA REACCIÓN"
        loop = "Y por eso todos lo clippearon."
        if what and game:
            desc = (f"{game_label}: {what} — el chat reaccionó en vivo.\n"
                    "Clip completo en vertical, sin contexto necesario.")
        elif game:
            desc = (f"{game_label}: el momento al que reaccionó el chat.\n"
                    "Clip completo en vertical, sin contexto necesario.")
        else:
            desc = ("El momento al que reaccionó el chat — juego sin verificar.\n"
                    "Clip completo en vertical, sin contexto necesario.")
        queries = [f"{game_label.lower()} mejores momentos", f"{game_label.lower()} clips graciosos"] if game else ["clips graciosos stream", "reacción del chat clips"]

    # La miniatura no repite el título: si el thumb coincide, se cambia.
    if best and _norm_title(thumb) in _norm_title(best):
        thumb = "MIRA ESTO"

    return {
        "n": n,
        "viral_multiple": multiple,  # yt-viral: x veces la mediana del stream
        "formula": formula,
        "formula_desc": HOOK_FORMULAS[formula],
        "titles": titles,  # 10 títulos reales, cada uno con su check
        "best_title": best,
        "title_issues": _check_title_lint(best) if best else ["sin título que pase el check promesa-payoff"],
        "needs_human_review": best is None,
        "moment_card": card or None,
        "game": game,
        "game_label": game_label,
        "thumbnail_text": thumb,  # ≤3 palabras, distinto del título
        "hook_first_line": hook,  # primera línea nueva (yt-shorts)
        "onscreen_first_2s": onscreen,  # distinto de lo hablado
        "loop_point": loop,
        "description_first_2_lines": desc,
        "tags": tags[:15],
        "search_queries": queries,
        "self_contained": True,  # el pre-roll de 20 s da el contexto que falta
        "chat_examples": examples,
        # yt-retention: el hook se juega en los primeros 15 s, no al final.
        "retention_note": "Hook en los primeros 15 s: confirma el título, sube la pregunta y muestra el payoff. Si el clip arranca lento, recorta el pre-roll.",
        # Las 4 plataformas: mismo clip, 4 paquetes distintos.
        "platforms": package_for_platforms(best, desc, tags, thumb, lang=lang,
                                           hashtags=hashtags, game=game),
    }

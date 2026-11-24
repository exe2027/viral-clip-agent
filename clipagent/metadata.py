#!/usr/bin/env python3
"""
viral_package.py — empaquetado viral para cada clip.

Combina la lógica de Twitch Auto Clips v2 con las reglas de
empaquetado viral del proyecto:

  * Múltiplo viral — no rankear por puntaje bruto, sino por MÚLTIPLO
    sobre la mediana del propio stream. Un pico de 30 pts en un chat
    callado vale más que uno de 30 en un chat que explota todo el
    rato. multiple = puntaje_ventana / mediana(puntajes). Solo así
    el ranking habla del momento, no del tamaño del chat.
  * Shorts — un Short no es "el mejor momento", es un momento que
    sobrevive SIN el video alrededor: abre con una idea completa,
    tiene un giro y termina en una frase, no en un balbuceo.
    Por eso cada clip lleva: primera línea nueva, texto en pantalla
    en los primeros 2 s (distinto de lo hablado) y punto de loop.
  * Paquete — título + miniatura son UNA unidad. Reglas que se
    verifican aquí: título ≤60 caracteres (40 en móvil), miniatura
    ≤3 palabras y SIN repetir el título, máx. 2 palabras en MAYÚSCULAS,
    y un número/nombre/fecha antes que un adjetivo.
  * SEO — descripción: las 2 primeras líneas son el snippet de
    búsqueda. Tags: máx. 15, solo para desambiguar. Y la prueba de
    las 3 búsquedas que el video debería ganar.
  * Retención — el leak del hook es en los primeros 15-30 s:
    la primera línea tiene que confirmar el título, subir una
    pregunta que no se pueda cerrar y probar que el payoff existe.

Nada de esto publica. Esto escribe y tú publicas.
Cada clip termina con la pregunta: ¿lo subo así, o lo cambio?

BLOQUE 2 (revisión interna, 2026-11-16) — títulos reales + anti-clickbait:
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
    el lint de paquete (≤60 car., ≤2 palabras en MAYÚSCULAS).
  * Anti-repetición: ningún título repetido en el mismo VOD ni el
    mismo día. El registro vive en ``titles_seen.json`` (gitignored)
    y solo se anota el título elegido (``best_title``), no los 10
    candidatos: registrar los 9 no usados envenenaría el registro.
  * Etiqueta de juego veraz: "GTA 6" solo si el juego real es GTA 6.
    Los tags y hashtags salen del juego real, no de #GTA6 fijo.

ARREGLOS 3 y 4 (2026-11-16) — qué se ve + contexto en pantalla:
la lógica anti-mentira del Bloque 2 ya existía, pero NADIE le decía
qué se ve en el clip: ``auto_clips.py`` llamaba a ``package_clip``
sin ficha del momento (y sin juego/streamer/VOD), así que los
títulos salían de la ficha mínima de respaldo, y el manifiesto
seguía encabezando con ``suggest_titles()`` ("QUÉ JUGADA 😱 GTA 6"
hardcodeado, sin validar jamás). El arreglo 3 añade lo que faltaba:

  * ``visible_from_layout(layout_decision, payoff_ts_clip)`` — qué se
    ve en el tramo del payoff según layout.py: "gameplay" (solo
    gameplay), "cams+gameplay" (webcam PiP sobre gameplay) o
    "cams-only" (solo cams/reacción, sin gameplay visible). stack ->
    cams-only; pip -> cams+gameplay; center + 0 cams -> gameplay;
    center + 2 cams -> cams-only (Just Chatting con dos personas,
    como el clip de IlloJuan 2930700035, donde layout.py deja
    "center" porque no hay costura central). center + 1 cam o sin
    datos -> "unknown": no se afirma nada.
  * ``validate_title_final(title, moment_card, visible, layout)`` —
    validación FINAL antes de publicar: el check promesa-payoff de
    siempre MÁS las reglas de lo visible. En cams-only el título no
    puede nombrar ningún juego ni prometer nada de gameplay (jugada,
    tiro, penal, gol, clutch...): si miente, devuelve
    ``degraded_title``, un título de reacción ("El chat no paraba
    de reírse", "No lo podía creer").
  * ``generate_titles(..., visible=...)`` y ``package_clip(...,
    visible=..., layout=...)`` ya aceptan lo visible: en cams-only
    solo salen títulos de reacción (sin prefijo de juego), el
    ``best_title`` pasa la validación final como red de seguridad,
    y el paquete expone ``visible``, ``title_validation``,
    ``title_degraded`` y ``thumbnail_subject`` ("faces" en
    cams-only: las caras van en la miniatura, no el juego).
  * ``validate_legacy_title(title, ...)`` — lo mismo para títulos
    que vienen de fuera (p. ej. ``suggest_titles`` de auto_clips):
    (ok, motivo, degradado). El cableado del manifiesto (usar
    ``best_title`` en vez de ``suggest_titles``) vive en
    auto_clips.py y NO es parte de este arreglo.

El arreglo 4 (contexto en pantalla) es ``extract_reaction_context``:
si el clip es solo cams y el streamer mira algo fuera de cámara, saca
de la transcripción (+ ejemplos de chat) QUÉ mira — medio
(tráiler/documental/vídeo) + tema conocido — y devuelve p. ej.
"reaccionando al tráiler de GTA 3". Sin pareja medio+tema clara,
devuelve None y NO se quema nada. Nunca se mete imagen ni vídeo de
lo que mira: solo texto. El quemado vive en subtitles.py
(``burn_subtitles(..., context_text=...)``) y el paquete expone
``reaction_context`` para el manifiesto.

PARA LAS 4 PLATAFORMAS, NO SOLO YOUTUBE: las reglas de paquete,
seo, shorts y retención son las mismas ideas, pero cada
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
    from .metadata import package_clip, viral_multiple, package_for_platforms
    from .moment_card import build_moment_card
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

# Fórmulas de hook, adaptadas a clips de GTA 6.
# Solo las que tienen sentido para un clip de 60 s sin guion previo.
HOOK_FORMULAS = {
    "the-reveal": "The Reveal — promete algo concreto y lo muestra.",
    "the-superlative": "The Superlative — el mejor/peor/más rápido de su tipo.",
    "the-mistake": "The Mistake — nombra el error que el espectador está cometiendo.",
    "the-statistic": "The Statistic — un número que no sabías, luego la consecuencia.",
    "contrarian-flip": "Contrarian Flip — lo que todos dicen, y por qué está mal.",
}


# Reglas por plataforma (revisión interna). Los límites de
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
        # Instagram capó los hashtags a 5 por post (18-dic-2025);
        # el 10 era el límite viejo (2026-11-21, ig-caption).
        "caption_max": 2200, "hashtags": 5,
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
    "GTA 6": (["#GTA6", "#GTA6Clips", "#GTA", "#RockstarGames"],
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
    evidencia; o lint de paquete (largo, MAYÚSCULAS).
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

    # 1. Lint de paquete. Bloqueante: >60 car. y >2 palabras en MAYÚSCULAS.
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
    """Lint de paquete (compat: lo usaba la v2.4). Ver check_title para el bloqueo real."""
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


def _fit_what(what, prefix_len, target_min=40, target_max=55):
    """`what` ajustado para un título de largo ideal (7.5).

    Corta por palabras para que `prefix + what` quede entre 40 y
    55 caracteres (el largo que da +30 en el componente título).
    Sin recorte a mitad de palabra y sin "…": si no cabe nada de
    40 car., devuelve None (el llamador usa otro candidato).
    """
    words = (what or "").split()
    if not words:
        return None
    budget = target_max - prefix_len
    kept = []
    for word in words:
        trial = " ".join(kept + [word])
        if len(trial) > budget:
            break
        kept.append(word)
    text = " ".join(kept)
    if len(text) + prefix_len < target_min:
        return None
    return text


def generate_titles(moment_card, lang="es", n=10, seen=None, seen_path=None,
                    vod_id=None, date=None, visible=None):
    """10 títulos por clip desde la ficha (no 2 plantillas fijas).

    Solo devuelve títulos que pasan ``check_title`` y el lint, y que
    no están repetidos en el mismo VOD ni el mismo día. Si pasan
    menos de ``n``, devuelve los que pasan y el llamador lo ve en
    ``len(titles)``: mejor pocos títulos reales que 10 inventados.
    Cada título lleva su check: {"title", "issues", "check_ok", "check_reason"}.

    ``visible`` (arreglo 3): "cams-only" degrada a títulos de
    reacción — sin prefijo de juego y sin sustantivos de gameplay
    (ver ``_title_has_gameplay_word``): el clip no muestra gameplay,
    así que el título no puede prometerlo. Sin ``visible`` (o con
    "gameplay"/"cams+gameplay"/"unknown") el comportamiento es el de
    siempre.
    """
    card = moment_card or {}
    game = card.get("game")
    game_label = card.get("game_label") or "desconocido"
    streamer = card.get("streamer") or ""
    streamer = "" if streamer == "desconocido" else streamer
    what = card.get("what")  # verbo+objeto, o None si no hay evidencia
    reaction = card.get("dominant_reaction") or ""
    risas = card.get("risas") or 0
    multiple = card.get("viral_multiple")
    msgs = card.get("chat_msgs_peak_10s") or 0
    quote_chat = (card.get("quote_chat") or {}).get("text") or ""
    quote_chat_count = (card.get("quote_chat") or {}).get("count") or 0
    # Una cita del chat con 1 sola mención no es "el chat": es un usuario.
    # Solo se cita si al menos 2 mensajes dicen lo mismo.
    if quote_chat_count < 2:
        quote_chat = ""
    quote_streamer = (card.get("quote_streamer") or {}).get("text") or ""
    evidence_low = card.get("evidence") == "low"
    # Arreglo 3: en un clip solo cams el título no nombra ningún
    # juego: no se ve gameplay, así que "GTA V RP: ..." mentiría
    # igual que "GTA 6: ...". El prefijo solo existe si se ve juego.
    cams_only = visible == VISIBLE_CAMS_ONLY
    game_prefix = f"{game_label}: " if game and not cams_only else ""
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
            fitted = _fit_what(what_en, len(game_prefix))
            if fitted:
                candidates.append(f"{game_prefix}{_cap_first(fitted)}")
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
            # 7.5: título de largo ideal (40-55 car.) con el `what`
            # ajustado por palabras; si no cabe, no se agrega.
            fitted = _fit_what(what, len(game_prefix))
            if fitted:
                candidates.append(f"{game_prefix}{_cap_first(fitted)}")
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

    # Arreglo 3: en cams-only no vale ningún candidato que nombre un
    # juego o prometa gameplay — incluidas las citas del chat que
    # describen una acción fuera de cámara ("lo erra" es lo que pasó
    # fuera, no lo que se ve). Solo quedan títulos de reacción.
    if cams_only:
        candidates = [t for t in candidates
                      if not _title_names_game(t)
                      and not _title_has_gameplay_word(t)]

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


# ---------------------------------------------------------------------------
# Arreglos 3 y 4 (2026-11-16) — qué se ve + contexto en pantalla
# ---------------------------------------------------------------------------

# Qué se ve en el clip (lo decide layout.py o el capítulo del VOD,
# nunca el título ni el chat). "unknown" = no se sabe: no se afirma
# nada sobre lo visible, ni gameplay ni cams.
VISIBLE_GAMEPLAY = "gameplay"            # solo gameplay visible
VISIBLE_CAMS_GAMEPLAY = "cams+gameplay"  # webcam PiP sobre gameplay
VISIBLE_CAMS_ONLY = "cams-only"          # solo cams/reacción, sin gameplay
VISIBLE_UNKNOWN = "unknown"

# Palabras que prometen gameplay: sustantivos de NOUN_EVIDENCE más
# los verbos de acción del léxico de moment_card (erra, mata...) y
# "gameplay" mismo. En un clip solo cams, un título con cualquiera
# de ellas promete algo que no se ve.
GAMEPLAY_WORDS = (
    "jugada", "tiro", "disparo", "penal", "gol", "partido", "muerte",
    "robo", "coche", "clutch", "play", "shot", "goal", "gameplay",
    "erra", "patea", "dispara", "mata", "muere", "gana", "pierde",
    "roba", "choca", "cae", "falla",
)


def _title_names_game(title):
    """True si el título nombra algún juego conocido (o 'GTA' a secas)."""
    low = _norm_title(title)
    if "gta" in low:
        return True
    return any(alias in low for alias in KNOWN_GAMES)


def _title_has_gameplay_word(title):
    """True si el título promete gameplay (sustantivo o verbo de acción)."""
    low = _norm_title(title)
    return any(re.search(r"\b" + re.escape(w) + r"\b", low)
               for w in GAMEPLAY_WORDS)


def _scan_cams_from_reason(reason):
    """Nº de cams del texto 'escaneo: N cams (...)' de layout.py.

    Es el fallback cuando la decisión de layout no trae
    ``scan_segments`` ni ``samples`` (p. ej. la guardada en el
    manifiesto, que solo guarda mode/segments). Devuelve int o None.
    """
    m = re.search(r"escaneo:\s*(\d+)\s*cams", reason or "")
    return int(m.group(1)) if m else None


def visible_from_layout(layout_decision, payoff_ts_clip=20.0):
    """Qué se ve en el tramo del payoff, según layout.py.

    ``layout_decision`` es lo que devuelve ``layout.decide_layout``
    (o lo guardado en el manifiesto: {"mode", "segments": [...]}).
    ``payoff_ts_clip`` es el segundo del clip donde cae el momento
    (20 = pre-roll estándar). Devuelve {"visible", "reason",
    "layout", "cams"}.

    Reglas (la decisión de layout manda; el escaneo de cams la
    corrige donde layout.py no se atreve):
      * stack (dos webcams apiladas) -> cams-only.
      * pip (webcam sobre gameplay) -> cams+gameplay.
      * split RDJavi (streamer arriba, gameplay abajo) -> cams+gameplay.
      * center + 0 cams -> gameplay.
      * center + 2 cams -> cams-only: dos caras/personas sin
        gameplay visible (Just Chatting). layout.py deja "center"
        aquí porque sin costura central no corrige a stack ("puede
        ser una imagen estática"), pero para el título la
        consecuencia es la misma: no se ve gameplay.
      * center + 1 cam, o sin datos de cams -> unknown: una sola
        región estable a pantalla completa puede ser una webcam
        sola o una imagen estática; no se afirma nada.
    """
    if not layout_decision:
        return {"visible": VISIBLE_UNKNOWN, "reason": "sin decisión de layout",
                "layout": None, "cams": None}
    segments = layout_decision.get("segments") or []
    seg = None
    for s in segments:
        if s.get("start", 0) <= payoff_ts_clip < s.get("end", float("inf")):
            seg = s
            break
    if seg is None and segments:
        # Sin tramo que cubra el payoff: el tramo más largo.
        seg = max(segments, key=lambda s: s.get("end", 0) - s.get("start", 0))
    layout = (seg or {}).get("layout") or layout_decision.get("mode")
    # Nº de cams en el payoff: scan_segments > samples > texto reason.
    cams = None
    for s in layout_decision.get("scan_segments") or []:
        if s.get("start", 0) <= payoff_ts_clip < s.get("end", float("inf")):
            cams = s.get("cams")
            break
    if cams is None:
        samples = layout_decision.get("samples") or []
        if samples:
            nearest = min(samples, key=lambda s: abs(s.get("t", 0) - payoff_ts_clip))
            if abs(nearest.get("t", 0) - payoff_ts_clip) <= 5:
                cams = nearest.get("cams")
    if cams is None and seg:
        cams = _scan_cams_from_reason(seg.get("reason", ""))
    if layout == "stack":
        return {"visible": VISIBLE_CAMS_ONLY,
                "reason": "layout stack: dos webcams apiladas, sin gameplay",
                "layout": layout, "cams": cams}
    if layout == "pip":
        return {"visible": VISIBLE_CAMS_GAMEPLAY,
                "reason": "layout pip: webcam sobre gameplay",
                "layout": layout, "cams": cams}
    if layout == "split":
        return {"visible": VISIBLE_CAMS_GAMEPLAY,
                "reason": "layout split RDJavi: streamer arriba y gameplay abajo, se ven ambos",
                "layout": layout, "cams": cams}
    if layout == "game":
        return {"visible": VISIBLE_GAMEPLAY,
                "reason": "layout game (voz del juego): juego a pantalla completa",
                "layout": layout, "cams": cams}
    if layout == "center":
        if cams is None or cams == 1:
            return {"visible": VISIBLE_UNKNOWN,
                    "reason": ("layout center con "
                               f"{'1 cam' if cams == 1 else 'cams sin medir'}: "
                               "puede ser webcam sola a pantalla completa o "
                               "imagen estática; no se afirma gameplay ni cams"),
                    "layout": layout, "cams": cams}
        if cams >= 2:
            return {"visible": VISIBLE_CAMS_ONLY,
                    "reason": ("layout center + escaneo de 2 cams: dos caras "
                               "sin gameplay visible (Just Chatting); sin "
                               "costura central layout.py no lo llama stack, "
                               "pero para el título es lo mismo: no se ve juego"),
                    "layout": layout, "cams": cams}
        return {"visible": VISIBLE_GAMEPLAY,
                "reason": "layout center sin cams: solo gameplay",
                "layout": layout, "cams": cams}
    return {"visible": VISIBLE_UNKNOWN,
            "reason": f"layout no reconocido: {layout!r}",
            "layout": layout, "cams": cams}


def visible_from_chapter(chapter_game):
    """Qué se ve según el capítulo de Twitch donde cae el pico.

    "Just Chatting" -> cams-only (el streamer a cámara, sin juego).
    Cualquier otro capítulo -> None: el capítulo dice el juego, no
    si se ve gameplay o cams, así que no se afirma nada (eso lo
    decide el layout). Para el clip de IlloJuan 2930700035 el pico
    cae en el capítulo Just Chatting (0-93 min).
    """
    if (chapter_game or "").strip().lower() == "just chatting":
        return VISIBLE_CAMS_ONLY
    return None


def degrade_title_to_reaction(moment_card, lang="es"):
    """Título de reacción para un clip solo cams (arreglo 3).

    Primero intenta ``generate_titles(..., visible="cams-only")``
    (títulos reales de la ficha, sin juego ni gameplay); si no sale
    ninguno, cae a una plantilla de reacción según la reacción
    dominante — nunca a un título con juego. Devuelve str o None
    (None solo si ni la ficha ni la reacción dan nada).
    """
    card = moment_card or {}
    try:
        titles = generate_titles(card, lang=lang, n=10,
                                 visible=VISIBLE_CAMS_ONLY)
        if titles:
            # La degradación prefiere un título de reacción plano
            # ("El chat no paraba de reírse") antes que una cita del
            # chat ('El chat: "W"'): la cita es honesta, pero como
            # título de rescate dice poco de qué pasó.
            plain = [t["title"] for t in titles
                     if not t["title"].startswith('El chat: "')
                     and not t["title"].startswith('Chat said it: "')]
            return plain[0] if plain else titles[0]["title"]
    except Exception:
        pass
    reaction = (card or {}).get("dominant_reaction") or ""
    if lang == "en":
        return {"risas": "Chat couldn't stop laughing",
                "hype": "Chat went crazy for this",
                "volumen": "Chat exploded over this"}.get(
                    reaction, "Nobody could believe it")
    return {"risas": "El chat no paraba de reírse",
            "hype": "El chat enloqueció con esto",
            "volumen": "El chat explotó con esto"}.get(
                reaction, "No lo podía creer")


def validate_title_final(title, moment_card=None, visible=None, layout=None,
                         lang="es"):
    """Validación FINAL del título antes de publicar (arreglo 3).

    Es el check promesa-payoff (``check_title_detail``) MÁS las
    reglas de lo visible. En cams-only el título no puede nombrar
    ningún juego ni prometer gameplay: si lo hace, el título se
    rechaza y se devuelve ``degraded_title`` (título de reacción).
    En gameplay/cams+gameplay/unknown solo aplica el check de
    siempre.

    Devuelve {"ok", "reasons", "warnings", "title", "visible",
    "degraded_title"}: ``degraded_title`` es None si el título pasa
    o si no hay degradación posible (visible != cams-only).
    """
    if visible is None and layout is not None:
        visible = visible_from_layout(layout).get("visible")
    detail = check_title_detail(title, moment_card)
    reasons = list(detail["reasons"])
    warnings = list(detail.get("warnings", []))
    if visible == VISIBLE_CAMS_ONLY:
        low = _norm_title(title)
        found = [a for a in KNOWN_GAMES if a in low]
        if "gta" in low and not found:
            reasons.append("clip solo cams: el título dice 'GTA' pero "
                           "no se ve gameplay de ningún GTA")
        for alias in found:
            reasons.append(f"clip solo cams: el título dice "
                           f"'{KNOWN_GAMES[alias]}' pero no se ve "
                           f"gameplay (solo caras/reacción)")
        words = [w for w in GAMEPLAY_WORDS
                 if re.search(r"\b" + re.escape(w) + r"\b", low)]
        if words:
            reasons.append("clip solo cams: el título promete "
                           f"{words} pero no se ve gameplay")
    ok = not reasons
    degraded = None
    if not ok and visible == VISIBLE_CAMS_ONLY:
        degraded = degrade_title_to_reaction(moment_card, lang=lang)
    return {"ok": ok, "reasons": reasons, "warnings": warnings,
            "title": title, "visible": visible or VISIBLE_UNKNOWN,
            "degraded_title": degraded}


def validate_legacy_title(title, moment_card=None, visible=None, layout=None,
                          lang="es"):
    """Valida un título que viene de fuera (arreglo 3).

    Para títulos generados fuera de ``generate_titles`` — p. ej. los
    de ``suggest_titles`` en auto_clips.py ("QUÉ JUGADA 😱 GTA 6"),
    que nunca pasaron por el check. Devuelve (ok, motivo,
    degradado): ``degradado`` es el título de reacción cuando el
    original miente en un clip solo cams, o None si pasa o no hay
    degradación. El cableado (usar ``best_title`` o degradar el
    título de plantilla en el manifiesto) vive en auto_clips.py.
    """
    detail = validate_title_final(title, moment_card, visible=visible,
                                  layout=layout, lang=lang)
    if detail["ok"]:
        return True, "pasa: juego veraz, sin promesas de gameplay que no se ve", None
    return False, "; ".join(detail["reasons"]), detail["degraded_title"]


# ---------------------------------------------------------------------------
# Arreglo 4 — contexto en pantalla para clips de reacción
# ---------------------------------------------------------------------------

# Medio que el streamer mira fuera de cámara -> etiqueta para el
# texto quemado. "viendo"/"mira" es medio débil (no dice QUÉ medio):
# solo vale para "reaccionando a X", sin nombrar medio.
REACTION_MEDIA_ES = {
    "tráiler": "tráiler", "trailer": "tráiler",
    "documental": "documental",
    "vídeo": "vídeo", "video": "vídeo",
    "anuncio": "anuncio", "teaser": "teaser",
    "presentación": "presentación", "presentacion": "presentación",
    "gameplay": "gameplay",
}
REACTION_MEDIA_EN = {
    "trailer": "trailer", "documentary": "documentary",
    "video": "video", "announcement": "announcement",
    "teaser": "teaser", "presentation": "presentation",
    "gameplay": "gameplay",
}
_REACTION_WEAK_MEDIA_ES = ("viendo", "mira", "mira este", "vamos a ver",
                           "estoy viendo", "ver este")
_REACTION_WEAK_MEDIA_EN = ("watching", "look at", "check out", "let's watch")

# Temas que se pueden nombrar: (alias normalizados, nombre en pantalla).
# Solo estos: un tema fuera de la lista no se nombra jamás (no se
# inventa qué mira). "legacy" solo vale como Hogwarts Legacy cuando
# viene con "jugar/jugando legacy" (es como lo dice el chat) o con
# harry potter/hogwarts cerca; "gta" a secas no es tema (falta el
# número). Whisper a veces escribe "GT A3": se normaliza antes.
REACTION_TOPICS = [
    (("gta 3", "gta iii", "gt a3"), "GTA 3"),
    (("gta vice city", "vice city"), "GTA Vice City"),
    (("gta san andreas", "san andreas"), "GTA San Andreas"),
    (("gta v", "gta 5", "gt a v", "gt a 5"), "GTA 5"),
    (("gta 6", "gta vi", "gt a 6"), "GTA 6"),
    (("gta online",), "GTA Online"),
    (("harry potter",), "Harry Potter"),
    (("hogwarts legacy", "jugar legacy", "jugando legacy",
      "jugando a legacy", "jugar a legacy"), "Hogwarts Legacy"),
    (("counter-strike 2", "counter strike 2", "cs2"), "Counter-Strike 2"),
    (("fortnite",), "Fortnite"),
    (("minecraft",), "Minecraft"),
    (("league of legends",), "League of Legends"),
    (("valorant",), "Valorant"),
    (("call of duty", "warzone"), "Call of Duty"),
]
# Ventana máxima (caracteres) entre el medio y el tema para que
# cuenten como pareja. La transcripción del clip de IlloJuan tiene
# "GTA 3" al abrir y "viendo un trailer" ~450 caracteres después.
REACTION_PAIR_WINDOW = 800


def _norm_reaction_text(text):
    """Normalización para buscar medio+tema: sin tildes, 'GT A3' -> 'GTA 3'."""
    text = _norm_title(text or "")
    # Whisper parte "GTA" en "GT A": "gt a3" -> "gta 3", "gt a v" -> "gta v".
    text = re.sub(r"\bgt\s*a\s*", "gta ", text)
    return re.sub(r"\s+", " ", text).strip()


def _reaction_text_sources(moment_card=None, transcript=None,
                           chat_examples=None):
    """Texto (normalizado) + posiciones de cada ocurrencia de tema/medio.

    Acepta ficha del momento (transcript_text + chat_top), transcript
    JSON de faster-whisper (segments) o texto plano. Devuelve
    (text, chat_text): el texto principal y el del chat por separado
    (el chat solo corrobora, no crea contexto por sí solo).
    """
    parts = []
    if transcript is not None:
        if isinstance(transcript, dict):
            segs = transcript.get("segments") or []
            parts.append(" ".join((s.get("text") or "") for s in segs))
            if not parts[-1] and transcript.get("text"):
                parts[-1] = transcript["text"]
        elif isinstance(transcript, (list, tuple)):
            parts.append(" ".join((s.get("text") or "") if isinstance(s, dict)
                                  else str(s) for s in transcript))
        else:
            parts.append(str(transcript))
    if moment_card:
        if moment_card.get("transcript_text"):
            parts.append(str(moment_card["transcript_text"]))
        for m in moment_card.get("chat_top", []) or []:
            parts.append(str(m.get("text", "")))
    chat_texts = []
    if chat_examples:
        chat_texts = [str(e.get("text", "") if isinstance(e, dict) else e)
                      for e in chat_examples]
    if moment_card:
        for m in moment_card.get("chat_top", []) or []:
            chat_texts.append(str(m.get("text", "")))
    text = _norm_reaction_text(" ".join(p for p in parts if p))
    chat_text = _norm_reaction_text(" ".join(chat_texts))
    return text, chat_text


def extract_reaction_context(moment_card=None, transcript=None,
                             chat_examples=None, visible=None, lang="es"):
    """Contexto que mira el streamer, para quemarlo en pantalla.

    Solo para clips solo cams (``visible == "cams-only"``): si se ve
    gameplay o no se sabe lo visible, devuelve None (en gameplay el
    contexto ya está en pantalla; en unknown no se afirma nada).
    Con ``visible=None`` se intenta igual: el llamador es quien
    decide si el clip es de reacción.

    Busca una pareja (medio, tema) en la transcripción: el medio es
    tráiler/documental/vídeo/anuncio/teaser/presentación/gameplay
    (o el verbo débil viendo/mira) y el tema uno de REACTION_TOPICS.
    Si varios temas cuadran con el mismo medio, gana el que se
    nombra PRIMERO: el streamer suele abrir nombrando lo que va a
    ver y los demás temas son el contraste (en el clip de IlloJuan
    abre con "GTA 3" y luego habla de lo que juega el resto:
    Legacy, Harry Potter PS1). El chat solo corrobora (desempate).

    Devuelve {"text", "medium", "topic", "lang", "evidence"} o None.
    Sin pareja clara: None, y NO se quema nada. Nunca mete imágenes
    ni vídeo de lo que mira: solo este texto.
    """
    if visible in (VISIBLE_GAMEPLAY, VISIBLE_CAMS_GAMEPLAY):
        return None
    text, chat_text = _reaction_text_sources(moment_card, transcript,
                                             chat_examples)
    if not text:
        return None
    media = REACTION_MEDIA_ES if lang != "en" else REACTION_MEDIA_EN
    weak = (_REACTION_WEAK_MEDIA_ES if lang != "en"
            else _REACTION_WEAK_MEDIA_EN)
    # Ocurrencias de cada medio (posición, etiqueta, fuerte/débil).
    media_hits = []
    for alias, label in media.items():
        for m in re.finditer(r"\b" + re.escape(alias) + r"\b", text):
            media_hits.append((m.start(), label, True))
    for alias in weak:
        for m in re.finditer(r"\b" + re.escape(alias) + r"\b", text):
            media_hits.append((m.start(), None, False))
    if not media_hits:
        return None
    # Ocurrencias de cada tema (primera mención, todas las posiciones).
    topic_hits = {}
    for aliases, label in REACTION_TOPICS:
        positions = []
        for alias in aliases:
            for m in re.finditer(r"\b" + re.escape(alias) + r"\b", text):
                positions.append(m.start())
        if positions:
            topic_hits[label] = sorted(positions)
    if not topic_hits:
        return None
    # Mejor pareja: medio y tema a <= ventana; gana el tema nombrado
    # primero (apertura del clip), desempate por cercanía al medio y
    # por corroboración en el chat.
    best = None
    for topic, positions in topic_hits.items():
        first = positions[0]
        corroborated = any(
            alias in chat_text
            for aliases, label in REACTION_TOPICS if label == topic
            for alias in aliases)
        for m_pos, m_label, strong in media_hits:
            dist = min(abs(p - m_pos) for p in positions)
            if dist > REACTION_PAIR_WINDOW:
                continue
            # Puntuación: manda la primera mención (apertura), luego
            # medio fuerte (tráiler/documental) sobre verbo débil
            # (viendo/mira), luego cercanía al medio y corroboración.
            score = (-first, 1 if strong else 0, -dist,
                     1 if corroborated else 0)
            if best is None or score > best[0]:
                best = (score, topic, m_label, strong, first, dist,
                        corroborated)
    if best is None:
        return None
    _, topic, medium, strong, first, dist, corroborated = best
    if lang == "en":
        if medium == "trailer":
            out = f"reacting to the {topic} trailer"
        elif medium == "documentary":
            out = f"reacting to the {topic} documentary"
        elif medium == "video":
            out = f"reacting to the {topic} video"
        elif medium:
            out = f"reacting to the {topic} {medium}"
        else:
            out = f"reacting to {topic}"
    else:
        if medium:
            out = f"reaccionando al {medium} de {topic}"
        else:
            out = f"reaccionando a {topic}"
    if len(out) > 60:
        return None
    evidence = [f"tema '{topic}' mencionado primero en el carácter {first}",
                f"medio '{medium or 'viendo/mira'}' a {dist} caracteres"]
    if corroborated:
        evidence.append("tema corroborado en el chat")
    return {"text": out, "medium": medium, "topic": topic, "lang": lang,
            "evidence": evidence}


def viral_multiple(score: int, all_scores, median_score=None) -> float:
    """Múltiplo sobre la mediana del stream.

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


def dedupe_hashtags(hashtags, limit=5):
    """Hashtags únicos y capados (2026-11-21, ig-caption).

    Instagram capó los hashtags a 5 por post (18-dic-2025) y el
    resto de plataformas también va a 5 como techo
    (``publish.hashtag_limit``). Sin duplicados (sin distinguir
    mayúsculas: ``#GTA6`` y ``#gta6`` son el mismo) y sin vacíos.
    El total final nunca pasa de ``limit``.
    """
    seen = set()
    out = []
    for tag in hashtags or []:
        tag = str(tag or "").strip()
        if not tag:
            continue
        if not tag.startswith("#"):
            tag = "#" + tag
        key = tag.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(tag)
        if len(out) >= max(int(limit), 0):
            break
    return out


# Saludos/preámbulos que nunca abren un caption (la primera línea
# es lo único visible en el feed antes de "...more").
_GREETING_OPENERS = (
    "hola", "hey", "buenas", "qué tal", "que tal", "bienvenidos",
    "bienvenidas", "chicos", "chicas", "gente",
)


def caption_first_line(desc, title, game, lang="es"):
    """Primera línea del caption: frase buscable + gancho, <=125 car.

    Instagram muestra solo los primeros ~125 caracteres en el
    feed; el resto queda tras "...more" (2026-11-21, ig-caption).
    La primera línea lleva la frase buscable como texto natural
    (el juego, p. ej. "GTA 6", el streamer, el momento) y el
    gancho/pregunta, sin saludo ni hashtag como primer carácter.
    Si la primera línea de ``desc`` ya trae la frase buscable se
    queda (recortada a 125); si no, manda el título, que es el
    gancho y trae juego + streamer + momento. Nunca inventa cifras.
    """
    line = (str(desc or "").split("\n")[0] or "").strip()
    # La frase buscable es el juego; "GTA VI" y "GTA 6" son el
    # mismo (GAME_ALIASES): vale cualquiera de las dos formas.
    # El juego puede venir como "GTA VI" (canónico en el panel)
    # o como "GTA 6" (clave de GAME_ALIASES): se resuelve el
    # alias en ambos sentidos.
    game_key = (game or "").strip()
    searchable_forms = {game_key.lower()} if game_key else set()
    for canonical, aliases in GAME_ALIASES.items():
        alias_lows = {a.lower() for a in aliases}
        if (game_key.lower() == canonical.lower()
                or game_key.lower() in alias_lows):
            searchable_forms |= {canonical.lower()} | alias_lows
            break
    searchable_forms.discard("")
    low = line.lower()
    bad_start = (
        not line
        or line[:1] == "#"
        or any(low.startswith(g + " ") or low == g
               for g in _GREETING_OPENERS)
    )
    missing_search = bool(
        searchable_forms
        and not any(form in low for form in searchable_forms))
    if bad_start or missing_search:
        candidate = (str(title or "").strip()
                     or line)
        # El título solo sirve si trae la frase buscable; si no,
        # se queda la línea original (mejor que inventar texto).
        if (not searchable_forms
                or any(form in candidate.lower()
                       for form in searchable_forms)):
            line = candidate
    if len(line) > 125:
        cut = line[:125].rsplit(" ", 1)[0].strip()
        line = (cut + "…") if cut else line[:124] + "…"
    return line


def package_for_platforms(best_title, desc, tags, thumb, lang="both",
                          hashtags=None, game=None):
    """Un paquete por plataforma a partir del paquete base.

    Misma idea, mismo hook y mismo texto en pantalla en las 4;
    cambia caption, hashtags, portada y modo de subida.
    Los hashtags salen del juego real (``game``); sin juego, genéricos.
    """
    if hashtags is None:
        hashtags, _ = game_hashtags_tags(game)
    # Total final <=5 y sin duplicados en las 4 plataformas
    # (Instagram capó a 5 en dic-2025; techo 5 en el resto).
    hashtags = dedupe_hashtags(hashtags, limit=5)
    hashtags_es = hashtags
    hashtags_en = hashtags
    hashtags = hashtags_en if lang == "en" else hashtags_es
    first_line = caption_first_line(desc, best_title, game, lang)
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
            caption = f"{first_line}\n\n{' '.join(hashtags[:5])}"
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


# 7.5: longitudes máximas del paquete (título, descripción, hook,
# hashtags/tags). La descripción se recorta con "…" si pasa de 1000.
MAX_TITLE_LEN = 60
MAX_DESC_LEN = 1000
MAX_HOOK_LEN = 80
MAX_TAGS = 15

# 7.5: textos de miniatura entre los que elegir (el que mejor puntúe
# contra el título: sin palabras repetidas, <=2 en MAYÚSCULAS).
# Textos de miniatura GENÉRICOS, PROHIBIDOS (2026-11-18, regla
# endurecida por el usuario): exclamaciones que no nombran el
# momento concreto del clip. Ni el prompt de Gemini (ver llm.py)
# ni el fallback de reglas pueden devolverlos: el texto se deriva
# de las palabras del propio momento (ver _moment_thumbnail_text).
# El RENDER (cuando se quema sobre la portada) usa tipografía
# condensada, neón rosa/violeta/naranja y estética Vice City
# (ver THUMBNAIL_STYLE y thumbnails.thumbnail_with_text).
GENERIC_THUMBNAIL_TEXTS = frozenset({
    "NO PUEDE SER", "¡QUÉ LOCURA!", "VICE CITY", "ESPERA EL FINAL",
    "LA REACCIÓN", "MIRA ESTO", "¡INCREÍBLE!", "¡NO LO CREO!",
    "NO WAY", "WAIT FOR IT", "CHAT REACTION", "UNREAL", "INSANE",
    "WOW",
})

# Los mismos, normalizados (sin tildes ni puntuación, en
# MAYÚSCULAS) para compararlos con cualquier texto candidato.
_GENERIC_THUMBNAIL_NORM = frozenset(
    _norm_title(t).upper() for t in GENERIC_THUMBNAIL_TEXTS)

# Palabras que nunca van en un texto de miniatura derivado del
# momento: artículos/preposiciones, verbos vagos y exclamaciones
# genéricas. Solo quedan sustantivos/adjetivos con contenido: el
# objeto del momento ("cartas legendarias", "gaviota", "cromos").
_THUMB_DROP_WORDS = frozenset("""
el la los las un una unos unas de del en con por para a al se su sus
este esta estos estas eso que y o es son fue hay mas muy ya no si mi tu
le lo me te nos les cuando como pero
descubre descubrir encuentra encontrar consigue conseguir pierde perder
gana ganar mata matar roba robar compra comprar vende vender juega jugar
hace hacer dice decir mira mirar pasa pasar llega llegar sale salir entra
entrar coge coger agarra agarrar tira tirar rompe romper explota explotar
rie reir llora llorar grita gritar corre correr salta saltar conduce
conducir atropella atropellar choca chocar vuela volar nada nadar duerme
dormir come comer bebe beber habla hablar canta cantar baila bailar
graba grabar reacciona reaccionar queda quedar pone poner da dar va ir
viene venir esta estar tiene tener ve ver oye oir prueba probar intenta
intentar logra lograr puede puedo puedes creer cree acaba mejor claro
gente despues antes luego siempre nunca tambien tampoco solo nada todo
todos increible increíble locura imposible wow jaja jajaja
discovers finds gets loses wins kills steals buys sells plays makes says
looks happens comes goes takes breaks explodes laughs cries screams runs
jumps drives hits flies sleeps eats talks sings reacts tries fails
cannot believe incredible insane unreal wow haha chat
""".split())


def _thumb_content_words(text, extra_drop=()):
    """Palabras con contenido de un texto, en orden de aparición y
    sin duplicados: normalizadas (sin tildes ni puntuación) para
    comparar, quitando las de _THUMB_DROP_WORDS y extra_drop."""
    if not text:
        return []
    drop = _THUMB_DROP_WORDS | set(extra_drop)
    out = []
    for w in _norm_title(text).split():
        if len(w) < 2 or w in drop or w.isdigit() or w in out:
            continue
        out.append(w)
    return out


def _moment_thumbnail_text(card, title, streamer=None, game_label=None):
    """Texto de miniatura que nombra el momento concreto (2026-11-18,
    regla endurecida por el usuario): se deriva de las palabras del
    propio momento — primero la transcripción (lo que dijo el
    streamer), luego su cita, luego `what` — quitando juego,
    streamer, palabras del título y exclamaciones genéricas. Máx. 3
    palabras (las más frecuentes, en orden de aparición), en
    MAYÚSCULAS con ¡!. Sin palabras del momento, "" (sin texto
    inventado: mejor sin miniatura que una genérica).
    """
    drop = set()
    for src in (game_label, streamer):
        if src:
            drop |= set(_norm_title(src).split())
    if title:
        drop |= set(_norm_title(title).split())
    sources = []
    if card:
        sources.append(card.get("transcript_text") or "")
        quote = card.get("quote_streamer") or {}
        sources.append(quote.get("text")
                       if isinstance(quote, dict) else "")
        sources.append(card.get("what") or "")
    for text in sources:
        words = _thumb_content_words(text, drop)
        if not words:
            continue
        counts = {}
        for w in _norm_title(text).split():
            if w in words:
                counts[w] = counts.get(w, 0) + 1
        ranked = sorted(words, key=lambda w: (-counts[w], words.index(w)))
        core = ranked[:3]
        return "¡" + " ".join(w.upper() for w in core) + "!"
    return ""

# Estilo de miniatura GTA 6 / Vice City (2026-11-18). La miniatura
# es el fotograma elegido por Gemini + el texto de miniatura del
# momento quemado con este estilo: condensada, neón atardecer Vice City,
# contorno negro grueso, sin fondo de color. La tipografía
# condensada es Barlow Condensed (la del panel); si el TTF no está
# en disco, el render usa DejaVu Sans Bold (ver thumbnails.py).
THUMBNAIL_STYLE = {
    "name": "gta6",  # estilo de miniatura (Ajustes del panel)
    "font_family": "Barlow Condensed",
    "font_fallback": "DejaVu Sans",
    "colors": ("#FF2E88", "#7B2FFF", "#FF9E00"),  # rosa, violeta, naranja
    "text_color": "#FFFFFF",  # texto blanco con...
    "accent_color": "#FF2E88",  # ...acento rosa neón (primera palabra / borde)
    "outline_color": "#000000",
    "elements": ("palmeras", "atardecer Vice City", "neón"),
}


def _prefer_ideal_length(titles):
    """El mejor título con 40-55 caracteres (7.5), o el primero.

    El largo ideal da +30 en el componente título (34 car. solo +15):
    entre los títulos que ya pasaron el check, se prefiere uno de
    largo ideal; si no hay ninguno, manda el primero.
    """
    for entry in titles or []:
        title = entry.get("title") if isinstance(entry, dict) else entry
        if title and 40 <= len(title) <= 55:
            return title
    if titles:
        first = titles[0]
        return first.get("title") if isinstance(first, dict) else first
    return None


def _best_thumbnail_text(thumb, title):
    """Valida el texto de miniatura derivado del momento (7.5).

    Ya no hay lista de textos genéricos entre los que elegir
    (2026-11-18, regla endurecida): el thumb llega de
    _moment_thumbnail_text o de Gemini y se devuelve tal cual si
    es válido (1-4 palabras, sin exclamación genérica). Si supera
    las 4 palabras se recorta a las 4 primeras; si es genérico o
    vacío, "" (sin texto inventado).
    """
    if not thumb:
        return ""
    if _norm_title(thumb).upper() in _GENERIC_THUMBNAIL_NORM:
        return ""
    words = str(thumb).replace("¡", "").replace("!", "").split()
    if not words:
        return ""
    if len(words) > 4:
        words = words[:4]
        return "¡" + " ".join(w.upper() for w in words) + "!"
    return thumb


def validate_metadata_lengths(pkg):
    """Longitudes del paquete (7.5). Devuelve [problemas].

    Título <=60, descripción <=1000, hook <=80, hashtags/tags <=15.
    Solo mide y reporta (el recorte de la descripción, si hace
    falta, ya se hizo al construir el paquete).
    """
    issues = []
    title = pkg.get("best_title") or ""
    if len(title) > MAX_TITLE_LEN:
        issues.append(f"título {len(title)} car. (> {MAX_TITLE_LEN})")
    desc = pkg.get("description_first_2_lines") or ""
    if len(desc) > MAX_DESC_LEN:
        issues.append(f"descripción {len(desc)} car. (> {MAX_DESC_LEN})")
    hook = pkg.get("hook_first_line") or ""
    if len(hook) > MAX_HOOK_LEN:
        issues.append(f"hook {len(hook)} car. (> {MAX_HOOK_LEN})")
    for key in ("hashtags", "tags"):
        items = pkg.get(key) or []
        if len(items) > MAX_TAGS:
            issues.append(f"{key}: {len(items)} (> {MAX_TAGS})")
    return issues


# Exclamaciones genéricas prohibidas en la miniatura (2026-11-18,
# regla endurecida). Si el trabajo paralelo de miniaturas ya definió
# _GENERIC_THUMBNAIL_NORM, manda esa lista; si no, esta copia local
# (mismos textos, ya normalizados: sin tildes ni puntuación).
_GEMINI_GENERIC_THUMBNAIL_NORM = frozenset({
    "NO PUEDE SER", "QUE LOCURA", "VICE CITY", "ESPERA EL FINAL",
    "LA REACCION", "MIRA ESTO", "INCREIBLE", "NO LO CREO",
    "NO WAY", "WAIT FOR IT", "CHAT REACTION", "UNREAL", "INSANE", "WOW",
})


def _gemini_first_package(check_card, visible, lang, llm_client=None):
    """Primer paquete SEO con Gemini (título, descripción, hashtags,
    miniatura) desde la ficha. Devuelve el dict validado o None.

    Validación (lo que ya existe): ``validate_title_final``
    (check_title + lint + reglas de lo visible); el título nombra
    el juego real cuando se ve gameplay (sin juego en el título el
    SEO pierde la keyword); descripción con el juego; hashtags con
    #; miniatura de 1-4 palabras sin repetir el título. El SEO por
    plataforma se mide DESPUÉS, sobre el paquete armado
    (``gemini_seo``): si no llega a 90, el reintento existente lo
    mejora o el clip queda retenido como hoy — aquí no es motivo
    de fallback. Sin LLM, o si algo falla, None: reglas.
    Nunca levanta: cualquier excepción es fallback a reglas.
    """
    try:
        from .llm import get_llm_client, make_seo_package
    except Exception:
        return None
    try:
        client = llm_client if llm_client is not None else get_llm_client()
        if client is None or not client.available():
            return None
        raw = make_seo_package(client, lang=lang)(check_card,
                                                  visible=visible)
        if not isinstance(raw, dict):
            return None
        title = (raw.get("title") or "").strip()
        desc = (raw.get("description") or "").strip()
        hashtags = raw.get("hashtags") or []
        thumb = (raw.get("thumbnail_text") or "").strip()
        if not title or not desc or not hashtags or not thumb:
            return None
        validation = validate_title_final(title, check_card,
                                          visible=visible, lang=lang)
        if not validation["ok"]:
            return None
        game = (check_card or {}).get("game")
        if game and visible != VISIBLE_CAMS_ONLY:
            if _norm_title(game) not in _norm_title(title):
                return None
            if _norm_title(game) not in _norm_title(desc):
                return None
        tags = [t for t in hashtags
                if isinstance(t, str) and t.startswith("#")]
        if not tags:
            return None
        if not 1 <= len(thumb.split()) <= 4:
            return None
        if _norm_title(thumb) and _norm_title(thumb) in _norm_title(title):
            return None
        # Sin exclamaciones genéricas (2026-11-18, regla endurecida):
        # el texto de miniatura nombra el momento concreto. Manda la
        # lista del trabajo paralelo de miniaturas si está definida;
        # si no, la copia local de arriba.
        _generic = (globals().get("_GENERIC_THUMBNAIL_NORM")
                    or _GEMINI_GENERIC_THUMBNAIL_NORM)
        if _norm_title(thumb).upper() in _generic:
            return None
        return {"title": title, "description": desc,
                "hashtags": tags[:8], "thumbnail_text": thumb}
    except Exception:
        return None


def package_clip(n, peak_ts, score, risas, hype, median_score, all_scores,
                 lang="both", examples=None, moment_card=None, game=None,
                 streamer=None, seen_path=None, vod_id=None, date=None,
                 register=False, visible=None, layout=None,
                 reel_patterns=None, credit_streamer=True, llm_client=None):
    """Devuelve el paquete viral completo de un clip.

    Con ``moment_card`` (recomendado, Bloque 2): los 10 títulos, el
    hook, la descripción, los tags y los hashtags salen de la ficha;
    ``best_title`` es el primer título que pasa el check promesa-payoff
    y no está repetido en el mismo VOD ni el mismo día. Sin ficha, el
    juego es desconocido y ningún título nombra un juego: el paquete
    queda marcado ``needs_human_review`` si ningún título pasa.
    Con ``register=True`` se anota el ``best_title`` en titles_seen.json.

    Arreglo 3: ``visible`` ("gameplay" | "cams+gameplay" |
    "cams-only" | "unknown") o ``layout`` (la decisión de layout.py)
    dicen qué se ve en el clip. En cams-only los títulos degradan
    a reacción (sin juego ni gameplay) y ``best_title`` pasa la
    validación final como red de seguridad: si mintiera, se degrada
    y queda marcado en ``title_degraded``. ``thumbnail_subject`` es
    "faces" en cams-only (las caras van en la miniatura).
    Arreglo 4: en cams-only, ``reaction_context`` trae el texto de
    contexto quemado ("reaccionando al tráiler de GTA 3") o None.
    Con ``reel_patterns`` (reel_study.py): el paquete añade la clave
    ``reel_study`` con plantillas y títulos sugeridos desde reels
    virales, todos validados por el check; ``best_title`` NO cambia.
    Gemini primero (2026-11-18): el primer paquete SEO (título,
    descripción, hashtags, miniatura) lo escribe Gemini desde la
    ficha (``_gemini_first_package``); si no está disponible o lo
    que devuelve no pasa check_title + lint + juego, las reglas de
    siempre. ``llm_client`` es inyección para tests; sin él se
    resuelve solo (gemini si hay key, si no reglas). El paquete
    expone ``pkg_source`` ("gemini" o "rules") y, con Gemini,
    ``gemini_package`` + ``gemini_seo`` (SEO medido del paquete).
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
    # Arreglo 3: lo visible. Si no se pasa explícito (p. ej. el
    # llamador ya sabe que el pico cae en un capítulo Just Chatting),
    # se deriva de la decisión de layout en el tramo del payoff.
    visible_info = None
    if visible is None and layout is not None:
        visible_info = visible_from_layout(
            layout,
            payoff_ts_clip=check_card.get("payoff_ts_clip") or 20.0)
        visible = visible_info["visible"]
    visible_reason = (visible_info or {}).get("reason") or (
        f"visible pasado por el llamador: {visible}" if visible else
        "sin dato de lo visible")
    # Gemini primero (2026-11-18): el primer paquete SEO lo escribe
    # la IA desde la ficha; si no está disponible o lo que devuelve
    # no valida, las reglas de siempre (sin romper nada).
    # ``llm_client`` es inyección para tests; sin él se resuelve
    # solo (gemini si hay key, si no NullLLMClient -> reglas).
    gemini_pkg = None
    try:
        gemini_pkg = _gemini_first_package(
            check_card, visible, lang, llm_client=llm_client)
    except Exception:
        gemini_pkg = None
    if gemini_pkg:
        # Los hashtags de Gemini reemplazan los del juego; los tags
        # de búsqueda siguen siendo los de las reglas.
        hashtags = gemini_pkg["hashtags"]
    titles = generate_titles(check_card, lang=lang, n=10,
                             seen_path=seen_path, vod_id=vod_id, date=date,
                             visible=visible)
    # 7.5: entre los títulos que pasaron el check, se prefiere uno
    # de largo ideal (40-55 car.): da el máximo en el componente
    # título del SEO. Sin títulos, best queda None como antes.
    # Con Gemini, manda su título (ya validado): se antepone a la
    # lista de candidatos para que quede trazado.
    if gemini_pkg:
        titles = [{"title": gemini_pkg["title"],
                   "issues": _check_title_lint(gemini_pkg["title"]),
                   "warnings": [],
                   "check_ok": True,
                   "check_reason": "Gemini: pasa check_title + lint"}] + titles
        best = gemini_pkg["title"]
    else:
        best = _prefer_ideal_length(titles)
    # Arreglo 3, red de seguridad: el mejor título pasa la validación
    # final contra lo visible. No debería fallar (generate_titles ya
    # filtró), pero si un candidato se colara, se degrada a reacción
    # y queda marcado, en vez de publicar un título mentiroso.
    title_validation = validate_title_final(
        best, check_card, visible=visible, lang=lang) if best else None
    title_degraded = False
    title_degrade_reason = None
    if title_validation and not title_validation["ok"]:
        degraded = title_validation["degraded_title"]
        if degraded and degraded != best:
            title_degrade_reason = "; ".join(title_validation["reasons"])
            best = degraded
            title_degraded = True
            title_validation = validate_title_final(
                best, check_card, visible=visible, lang=lang)
    if register and best:
        register_title(best, vod_id=vod_id, date=date, seen_path=seen_path,
                       clip=f"clip_{n:02d}", lang=lang)

    # Arreglo 4: contexto en pantalla solo si el clip es solo cams.
    reaction_context = extract_reaction_context(
        check_card, visible=visible, lang=lang)

    what = card.get("what") if card else None
    if lang == "en":
        # Miniatura del momento (2026-11-18, regla endurecida): se
        # deriva de las palabras del momento, nunca una exclamación
        # genérica (ver _moment_thumbnail_text).
        thumb = _moment_thumbnail_text(card, best, streamer, game_label)
        hook = ("Watch why chat lost it here." if reaction == "risas"
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
        # Miniatura del momento (2026-11-18, regla endurecida): se
        # deriva de las palabras del momento, nunca una exclamación
        # genérica (ver _moment_thumbnail_text).
        thumb = _moment_thumbnail_text(card, best, streamer, game_label)
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

    # Gemini primero: su descripción y su texto de miniatura
    # reemplazan los de las reglas (el hook/onscreen/loop y los
    # sufijos de abajo — contexto, crédito, CTA — se mantienen).
    if gemini_pkg:
        desc = gemini_pkg["description"]
        thumb = gemini_pkg["thumbnail_text"]

    # Arreglo 4: en un clip solo cams con contexto, la descripción lo
    # dice (el contexto va quemado en pantalla; la descripción no lo
    # promete como si fuera gameplay, solo lo anota).
    if visible == VISIBLE_CAMS_ONLY and reaction_context:
        desc += (f"\nContexto en pantalla: {reaction_context['text']}."
                 if lang != "en" else
                 f"\nOn-screen context: {reaction_context['text']}.")

    # 7.5: crédito al streamer en la descripción (configurable) +
    # CTA/pregunta al final. El crédito nombra al streamer del VOD
    # (nunca hardcodeado); la CTA suma en el componente descripción
    # del SEO (+30 en vez de +10) y va al final, donde la mide el
    # score. Sin streamer conocido no hay crédito.
    _credit_streamer = streamer if streamer not in (None, "", "desconocido") else ""
    if credit_streamer and _credit_streamer and _credit_streamer not in desc:
        if lang == "en":
            desc += f"\n\n🎬 Clip by {_credit_streamer}, live."
        else:
            desc += f"\n\n🎬 Clip de {_credit_streamer} en directo."
    if lang == "en":
        desc += "\nDid you see that coming? Subscribe for more clips."
    else:
        desc += "\n¿Te lo esperabas? Suscríbete para más clips."
    # 7.5: red de seguridad: la descripción nunca pasa de 1000 car.
    # (se recorta antes de construir los paquetes por plataforma).
    if len(desc) > MAX_DESC_LEN:
        desc = desc[:MAX_DESC_LEN - 1] + "…"

    # La miniatura no repite el título: _moment_thumbnail_text ya
    # quita las palabras del título, y el paquete Gemini se valida
    # en _gemini_first_package. Nada se reemplaza por una
    # exclamación genérica (regla 2026-11-18): el texto que nombra
    # el momento comparte palabras con el título por naturaleza y
    # la puntuación SEO lo refleja, sin inventar otro texto.
    if not gemini_pkg:
        thumb = _best_thumbnail_text(thumb, best)

    # Estudio de reels (reel_study.py): solo AÑADE sugerencias
    # validadas al paquete, nunca cambia best_title ni la descripción.
    # reel_patterns puede ser el dict de patrones o el informe
    # completo (con clave "patterns").
    pkg = {
        "n": n,
        "viral_multiple": multiple,  # x veces la mediana del stream
        "formula": formula,
        "formula_desc": HOOK_FORMULAS[formula],
        "titles": titles,  # 10 títulos reales, cada uno con su check
        "best_title": best,
        "title": best,  # alias del título elegido (= best_title)
        "description": desc,  # alias de description_first_2_lines
        "hashtags": hashtags,  # hashtags usados (Gemini o reglas)
        # Gemini primero (2026-11-18): "gemini" si el paquete lo
        # escribió la IA, "rules" si salió por reglas (fallback).
        "pkg_source": "gemini" if gemini_pkg else "rules",
        "title_issues": _check_title_lint(best) if best else ["sin título que pase el check promesa-payoff"],
        "needs_human_review": best is None,
        # Arreglo 3: lo visible y la validación final del título.
        "visible": visible or VISIBLE_UNKNOWN,
        "visible_reason": visible_reason,
        "title_validation": title_validation,
        "title_degraded": title_degraded,
        "title_degrade_reason": title_degrade_reason,
        # Arreglo 3: en cams-only la miniatura lleva las caras, no el juego.
        "thumbnail_subject": "faces" if visible == VISIBLE_CAMS_ONLY else "gameplay",
        # Arreglo 4: contexto quemado (dict) o None si no hay uno claro.
        "reaction_context": reaction_context,
        "moment_card": card or None,
        "game": game,
        "game_label": game_label,
        "thumbnail_text": thumb,  # ≤3 palabras, distinto del título
        "hook_first_line": hook,  # primera línea nueva
        "length_issues": [],  # 7.5: se rellena tras construir el pkg
        "onscreen_first_2s": onscreen,  # distinto de lo hablado
        "loop_point": loop,
        "description_first_2_lines": desc,
        "tags": tags[:15],
        "search_queries": queries,
        "self_contained": True,  # el pre-roll de 20 s da el contexto que falta
        "chat_examples": examples,
        # Retención: el hook se juega en los primeros 15 s, no al final.
        "retention_note": "Hook en los primeros 15 s: confirma el título, sube la pregunta y muestra el payoff. Si el clip arranca lento, recorta el pre-roll.",
        # Las 4 plataformas: mismo clip, 4 paquetes distintos.
        "platforms": package_for_platforms(best, desc, tags, thumb, lang=lang,
                                           hashtags=hashtags, game=game),
    }
    if reel_patterns:
        try:
            from reel_study import enrich_package
            pats = reel_patterns.get("patterns", reel_patterns) \
                if isinstance(reel_patterns, dict) else reel_patterns
            pkg = enrich_package(pkg, pats, moment_card=card or None,
                                 lang=lang if lang in ("es", "en") else "es")
        except Exception as e:
            pkg["reel_study"] = {"error": f"estudio de reels no aplicado: {e}",
                                 "sample_n": 0}
    # 7.5: longitudes validadas (la descripción se recorta si pasa).
    pkg["length_issues"] = validate_metadata_lengths(pkg)
    if gemini_pkg:
        # Paquete Gemini: se guarda tal cual vino y su SEO medido
        # (transparencia). El gate 50/90 y retry_best deciden como
        # siempre: si no llega a 90, el reintento lo mejora o el clip
        # queda retenido.
        pkg["gemini_package"] = gemini_pkg
        try:
            pkg["gemini_seo"] = score_package_seo(
                pkg, lang=lang if lang in ("es", "en") else "es")
        except Exception:
            pkg["gemini_seo"] = {}
    return pkg


def score_package_seo(pkg, game_verified=None, visible=None, lang="es"):
    """Score SEO por plataforma de un paquete de package_clip.

    Mapea los campos del paquete (best_title, description_first_2_lines,
    tags, thumbnail_text, hashtags por plataforma) a seo_score.py y
    devuelve {platform: resultado}. ``game_verified``: True si el juego
    del título es el juego real del clip (None = se deduce del
    paquete: game conocido y distinto de "desconocido"). Si no hay
    seo_score.py, devuelve {}.
    """
    try:
        from .seo_score import score_all_platforms
    except ImportError:
        return {}
    pkg = pkg or {}
    game = pkg.get("game")
    if game_verified is None:
        game_verified = bool(game) and game != "desconocido"
    visible = visible if visible is not None else pkg.get("visible")
    if visible == "unknown":
        visible = None
    platforms = pkg.get("platforms") or {}
    hashtags = []
    if platforms:
        yt = platforms.get("youtube_shorts") or {}
        hashtags = yt.get("hashtags") or []
    if not hashtags:
        hashtags = pkg.get("hashtags") or []
    return score_all_platforms({
        "title": pkg.get("best_title"),
        "description": pkg.get("description_first_2_lines"),
        "tags": pkg.get("tags"),
        "thumbnail_text": pkg.get("thumbnail_text"),
        "hashtags": hashtags,
        "game_verified": game_verified,
        "visible": visible,
        "lang": lang,
        "keyword": "GTA 6" if (game or "GTA 6") in ("GTA 6", "GTA VI") else (game or "GTA 6"),
    })


def seo_inputs_from_package(pkg, lang="es"):
    """Entradas exactas de `score_package_seo` para un paquete.

    Es el MISMO mapeo que `score_package_seo` (mismos campos, mismos
    defaults): el cerebro lo manda al panel junto con los scores
    (2026-11-24, auto-seo) para que el loop de revalidación pueda
    reproducir el score del corte sin el paquete. Sin red, sin
    Gemini, sin coste: solo lee el dict.
    """
    pkg = pkg or {}
    game = pkg.get("game")
    game_verified = bool(game) and game != "desconocido"
    visible = pkg.get("visible")
    if visible == "unknown":
        visible = None
    hashtags = []
    platforms = pkg.get("platforms") or {}
    if platforms:
        yt = platforms.get("youtube_shorts") or {}
        hashtags = yt.get("hashtags") or []
    if not hashtags:
        hashtags = pkg.get("hashtags") or []
    return {
        "hashtags": list(hashtags),
        "visible": visible,
        "game_verified": game_verified,
        "keyword": "GTA 6" if (game or "GTA 6") in ("GTA 6", "GTA VI") else (game or "GTA 6"),
        "lang": lang,
    }


# ---------------------------------------------------------------------------
# Self-test (arreglos 3 y 4): python3 viral_package.py [--self-test]
# Casos reales: Coker (GTA V RP, solo cams), Locones (GTA RP rumano,
# solo volumen) e IlloJuan 2930700035 (Just Chatting, tráiler GTA 3).
# ---------------------------------------------------------------------------

def _self_test():
    here = Path(__file__).parent
    failures = []

    def check(name, cond, extra=""):
        print(f"  {'PASS' if cond else 'FAIL'}  {name}" + (f" — {extra}" if extra else ""))
        if not cond:
            failures.append(name)

    print("== visible_from_layout ==")
    check("stack -> cams-only",
          visible_from_layout({"mode": "auto", "segments": [
              {"start": 0.0, "end": 60.0, "layout": "stack", "params": {},
               "reason": "dos webcams apiladas"}]})["visible"] == "cams-only")
    check("pip -> cams+gameplay",
          visible_from_layout({"mode": "auto", "segments": [
              {"start": 0.0, "end": 60.0, "layout": "pip", "params": {},
               "reason": "caja con borde en esquina superior"}]})["visible"] == "cams+gameplay")
    check("center + 0 cams -> gameplay",
          visible_from_layout({"mode": "auto", "segments": [
              {"start": 0.0, "end": 60.0, "layout": "center", "params": {},
               "reason": "sin franja ni caja | escaneo: 0 cams (celdas estables izq 3% / der 4%)"}]})["visible"] == "gameplay")
    check("center + 1 cam -> unknown (no se afirma nada)",
          visible_from_layout({"mode": "auto", "segments": [
              {"start": 0.0, "end": 60.0, "layout": "center", "params": {},
               "reason": "escaneo: 1 cams (celdas estables izq 57% / der 40%)"}]})["visible"] == "unknown")
    check("sin layout -> unknown",
          visible_from_layout(None)["visible"] == "unknown")
    # Caso real IlloJuan 2930700035: layout "center" pero escaneo 2 cams.
    illo_layout = None
    illo_manifest = here / "runs" / "2930700035" / "manifest.json"
    if illo_manifest.exists():
        illo_layout = json.loads(illo_manifest.read_text(encoding="utf-8"))["clips"][0]["layout"]
    else:
        illo_layout = {"mode": "auto", "segments": [
            {"start": 0.0, "end": 60.0, "layout": "center", "params": {},
             "reason": "sin franja apilada ni caja de webcam; costura máx. 17 en x=43 | escaneo: 2 cams (celdas estables izq 79% / der 90%; SIN costura central (posible imagen estática))"}]}
    check("IlloJuan 2930700035 (center + 2 cams) -> cams-only",
          visible_from_layout(illo_layout, payoff_ts_clip=20.0)["visible"] == "cams-only",
          str(visible_from_layout(illo_layout, payoff_ts_clip=20.0)["reason"])[:60])
    check("Just Chatting -> cams-only (capítulo)",
          visible_from_chapter("Just Chatting") == "cams-only")
    check("capítulo GTA V -> None (no se afirma nada)",
          visible_from_chapter("Grand Theft Auto V") is None)

    print("== validate_title_final / degrade (Coker, GTA V RP, solo cams) ==")
    coker_card = None
    coker_path = here / "runs" / "kick-coker-01a1dffd" / "clips" / "moment_card.json"
    if coker_path.exists():
        coker_card = json.loads(coker_path.read_text(encoding="utf-8"))
    else:
        coker_card = {"game": "GTA V RP", "game_label": "GTA V RP",
                      "dominant_reaction": "risas", "risas": 14, "hype": 1,
                      "viral_multiple": 56.5, "evidence": "medium",
                      "what": "errar un tiro", "what_key": "erra",
                      "transcript_text": "No, no, no. Cuando juega bien en el partido",
                      "payoff_ts_clip": 20.0, "clip_duration_s": 60,
                      "unique_users_peak_30s": 31, "chat_msgs_peak_10s": 56,
                      "quote_chat": {"text": "ESTANDO ARMADO Y LO ERRA", "count": 2},
                      "quote_streamer": {"text": "No, no, no, no, no."},
                      "chat_top": [{"text": "estando armado y lo erra", "count": 2}],
                      "evidence_tokens": ["erra", "partido", "juega"]}
    v = validate_title_final("GTA 6: la jugada que rompió el chat",
                             coker_card, visible="cams-only")
    check("Coker: 'GTA 6: la jugada...' rechazado en cams-only",
          not v["ok"], "; ".join(v["reasons"])[:120])
    check("Coker: degradado a título de reacción",
          v["degraded_title"] == "El chat no paraba de reírse", str(v["degraded_title"]))
    v2 = validate_title_final("GTA V RP: errar un tiro y el chat explotó",
                              coker_card, visible="gameplay")
    check("Coker: mismo título PASA si se viera gameplay",
          v2["ok"], "; ".join(v2["reasons"])[:120])
    titles = generate_titles(coker_card, lang="es", n=10, visible="cams-only")
    check("Coker: generate_titles cams-only no nombra juego ni gameplay",
          all(not _title_names_game(t["title"]) and not _title_has_gameplay_word(t["title"])
              for t in titles) and len(titles) > 0,
          f"{len(titles)} títulos: {[t['title'] for t in titles][:3]}")
    ok, why, deg = validate_legacy_title("QUÉ JUGADA 😱 GTA 6", coker_card,
                                         visible="cams-only")
    check("Coker: título legacy 'QUÉ JUGADA 😱 GTA 6' rechazado + degradado",
          not ok and deg == "El chat no paraba de reírse", f"{why[:80]} -> {deg}")

    print("== Locones (GTA RP rumano, solo volumen, cams-only) ==")
    locones_card = {"game": "GTA V RP", "game_label": "GTA V RP",
                    "dominant_reaction": "volumen", "risas": 0, "hype": 0,
                    "viral_multiple": 24.75, "evidence": "low",
                    "what": None, "what_key": None, "transcript_text": "",
                    "payoff_ts_clip": 20.0, "clip_duration_s": 60,
                    "unique_users_peak_30s": 20, "chat_msgs_peak_10s": 25,
                    "quote_chat": None, "quote_streamer": None,
                    "chat_top": [], "evidence_tokens": []}
    v3 = validate_title_final("GTA 6: La jugada que rompió el chat",
                              locones_card, visible="cams-only")
    check("Locones: 'GTA 6: La jugada que rompió el chat' rechazado",
          not v3["ok"], "; ".join(v3["reasons"])[:120])
    check("Locones: degradado a reacción de volumen (honesto, sin juego)",
          v3["degraded_title"] in ("El chat explotó con esto",
                                   "25 mensajes en 10 segundos por esto")
          and validate_title_final(v3["degraded_title"], locones_card,
                                   visible="cams-only")["ok"],
          str(v3["degraded_title"]))
    check("Locones: sin transcripción no hay contexto",
          extract_reaction_context(locones_card, visible="cams-only") is None)

    print("== IlloJuan 2930700035 (Just Chatting, cams-only) ==")
    illo_transcript = here / "runs" / "2930700035" / "clips" / "clip_01_t26m05s_s19.22_r4h47_transcript.json"
    illo_card = {"game": "Grand Theft Auto V", "game_label": "Grand Theft Auto V",
                 "dominant_reaction": "hype", "risas": 4, "hype": 47,
                 "viral_multiple": 2.8, "evidence": "medium",
                 "what": None, "what_key": None,
                 "payoff_ts_clip": 20.0, "clip_duration_s": 60,
                 "unique_users_peak_30s": 108, "chat_msgs_peak_10s": 47,
                 "quote_chat": {"text": "W", "count": 81},
                 "quote_streamer": {"text": "viendo un trailer, vale, de 1998"},
                 "chat_top": [{"text": "w", "count": 81}],
                 "evidence_tokens": ["trailer", "gta", "legacy"]}
    if illo_transcript.exists():
        illo_card["transcript_text"] = " ".join(
            s.get("text", "") for s in
            json.loads(illo_transcript.read_text(encoding="utf-8")).get("segments", []))
    else:
        illo_card["transcript_text"] = ("GT A3. ¿Sabes lo que te digo? O acordáis cuando salió o jugar "
                                        "legacy y todo el puto mundo estaba jugando a jugar legacy y a mi ese día "
                                        "se me metió en los cojones jugada Harry Potter y la piedra filosofal de "
                                        "PlayStation 1, pues esto va a ser un poco lo mismo. Todo el mundo ya "
                                        "metido en el mundo abierto y yo viendo un trailer, vale, de 1998.")
    v4 = validate_title_final("QUÉ JUGADA 😱 GTA 6", illo_card, visible="cams-only")
    check("IlloJuan: 'QUÉ JUGADA 😱 GTA 6' rechazado en cams-only",
          not v4["ok"], "; ".join(v4["reasons"])[:120])
    check("IlloJuan: degradado a reacción de hype",
          v4["degraded_title"] == "El chat enloqueció con esto", str(v4["degraded_title"]))
    ctx = extract_reaction_context(illo_card, visible="cams-only")
    check("IlloJuan: contexto = reaccionando al tráiler de GTA 3",
          ctx is not None and ctx["text"] == "reaccionando al tráiler de GTA 3",
          str(ctx))
    check("IlloJuan: con gameplay visible NO hay contexto (gated)",
          extract_reaction_context(illo_card, visible="gameplay") is None)
    ctx_en = extract_reaction_context(illo_card, visible="cams-only", lang="en")
    check("IlloJuan EN: reacting to the GTA 3 trailer",
          ctx_en is not None and ctx_en["text"] == "reacting to the GTA 3 trailer",
          str(ctx_en))
    pkg = package_clip(1, 1575, 19.22, 4, 47, None, [1, 2, 3], lang="es",
                       moment_card=illo_card, visible="cams-only")
    check("package_clip IlloJuan: best_title es reacción, sin juego",
          pkg["best_title"] and not _title_names_game(pkg["best_title"])
          and not _title_has_gameplay_word(pkg["best_title"]),
          str(pkg["best_title"]))
    check("package_clip IlloJuan: visible + thumbnail faces + contexto",
          pkg["visible"] == "cams-only"
          and pkg["thumbnail_subject"] == "faces"
          and pkg["reaction_context"] is not None
          and pkg["reaction_context"]["text"] == "reaccionando al tráiler de GTA 3",
          f"visible={pkg['visible']} thumb={pkg['thumbnail_subject']} ctx={pkg['reaction_context']}")

    print("== extract_reaction_context: negativos y sintéticos ==")
    check("Coker: sin medio+tema no hay contexto",
          extract_reaction_context(coker_card, visible="cams-only") is None)
    check("sin transcripción no hay contexto",
          extract_reaction_context({"transcript_text": ""}, visible="cams-only") is None)
    check("solo tema sin medio no hay contexto",
          extract_reaction_context({"transcript_text": "estamos hablando de GTA 6 y de todo un poco"},
                                   visible="cams-only") is None)
    doc = extract_reaction_context(
        {"transcript_text": "vamos a ver este documental sobre GTA San Andreas que es una locura"},
        visible="cams-only")
    check("sintético documental GTA San Andreas",
          doc is not None and doc["text"] == "reaccionando al documental de GTA San Andreas",
          str(doc))
    en = extract_reaction_context(
        {"transcript_text": "we are watching the GTA 6 trailer right now, this is insane"},
        visible="cams-only", lang="en")
    check("sintético EN trailer GTA 6",
          en is not None and en["text"] == "reacting to the GTA 6 trailer", str(en))

    if failures:
        print(f"\n{len(failures)} FALLOS:", failures)
        return 1
    print("\nSelf-test OK: títulos honestos + contexto en pantalla.")
    return 0


if __name__ == "__main__":
    raise SystemExit(_self_test())

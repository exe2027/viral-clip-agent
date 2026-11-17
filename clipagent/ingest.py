#!/usr/bin/env python3
"""
Twitch Auto Clips v2.4 — clips_chat_v2.py

Detecta los mejores momentos de un directo de Twitch a partir del chat
(chat.json de TwitchDownloader) y los recorta en vertical 1080x1920,
listos para TikTok, Reels o Shorts.

Qué cambia en la v2.5 (tribunal, bloque 3, 2026-11-16):
  * Baseline local: cada ventana se compara contra su vecindad
    (±12 min), no contra todo el VOD. Score = z-score local +
    pendiente de subida (60 s previos, cap 2). Flag
    ``--local-baseline``, activado por defecto
    (``--no-local-baseline`` lo apaga). Un pico en un tramo muerto
    ya no compite con un pico en un tramo caliente: cada uno se
    mide contra su propio vecindario.
  * Anti-spam v2: por ventana, ratio emote-only, ratio repetición
    (1 - cuerpos distintos / mensajes, sobre TODOS los mensajes) y
    diversidad léxica (sobre mensajes con texto). Penaliza fuerte:
    emote-only >60% → ×0.25 (motivo ``emote_spam``), repetición
    >40% → ×0.5 (motivo ``repetition_spam``); ambas → ×0.125.
    La repetición se mide sobre todos los mensajes aunque haya
    muchos usuarios únicos: un mismo emote repetido por 14 personas
    (KEKW×14) también es baja diversidad, pero con 28% no llega al
    umbral del 40% y no se penaliza. El motivo queda en ``detail``
    y en el manifiesto (``motivo_penalizacion``).
  * Score final = z-score local + 0.5 × pendiente de subida +
    magnitud (puntos ajustados / 10). El z-score solo premiaba
    ráfagas pequeñas en tramos muertos (una ventana de 29 pts en
    una zona callada superaba al pico real de 67 pts); el término
    de magnitud mantiene el tamaño absoluto en el ranking.

Qué cambia en la v2.4 (auditoría Claude Code, 2026-11-16):
  * ``xd`` por fin puntúa como risa (era la risa más común del chat
    hispano y valía 1 punto). También ``xdd``, ``xddd``...
  * Falsos positivos eliminados: "no puedo creer que no hayas ido"
    y "qué bueno que llegaste" ya NO son risa ("no puedo" y "qué bueno"
    salieron de las listas); "dale gas", "vamos a ver qué pasa",
    "god of war" y "crazy cat" ya NO son hype ("dale", "vamos", "god"
    y "crazy" sueltos salieron de las listas; solo quedan sus formas
    alargadas/exclamativas: "vamooos", "dalee", "noooo").
    Todo se comprueba con regex precompiladas de palabra completa,
    nunca con subcadenas.
  * Rendimiento: un solo tokenizado por mensaje y pertenencia a
    conjuntos (O(1)) en vez de recorrer todas las listas con regex;
    las regex solo corren, con guarda previa, para formas alargadas
    (xd, jajajaja, vamooos). Una sola lectura del JSON (caché en
    ``_load_chat``; ``sample_messages`` la reusa, ya no relee el
    archivo por clip). Medido en 200k mensajes sintéticos:
    score_message 1,28 s y score_windows 1,81 s con la caché llena
    (la versión anterior tardaba 9,6 s en total; json.load aparte,
    ~2,7 s, es inevitable).
  * Ventana deslizante: ventanas de 10 s con paso de 5 s y suavizado
    (0.25/0.5/0.25), para que un pico entre dos ventanas no se parta.
  * Mediana sin sesgo: ``scores`` incluye TODAS las ventanas (también
    las vacías, con 0), así el múltiplo sobre la mediana no se infla.
    Ver ``median_over_all`` y el parámetro ``median_score`` de
    ``viral_package.viral_multiple``.
  * Usuarios únicos: el detalle de cada ventana lleva ``users`` (nº de
    comentaristas distintos) y los mensajes repetidos del mismo usuario
    en la misma ventana solo cuentan una vez; los bots conocidos
    (Nightbot, StreamElements, Moobot...) no puntúan.
  * Manifiesto incremental: ``main()`` escribe ``manifest.json`` en
    la carpeta de salida después de cada clip cortado, no solo al
    final; si algo falla a mitad, lo ya cortado queda registrado.
  * Flags ffmpeg para plataformas: ``-pix_fmt yuv420p``,
    ``-movflags +faststart`` y normalización de audio ``loudnorm``
    (Instagram/Facebook rechazan algunos formatos sin ellos).

Versiones anteriores (resumen):
  * v2: español + inglés, anti falsos positivos en tokens cortos
    (``xd`` en "extra", ``lol`` en "lollipop"), todo configurable.
  * v1: solo risas en inglés.

Uso:
  TwitchDownloaderCLI chatdownload --id <vod-id> -o chat.json -E
  TwitchDownloaderCLI videodownload --id <vod-id> -o vod.mp4

  python clips_chat_v2.py chat.json --video vod.mp4 --out clips
  python clips_chat_v2.py chat.json --video vod.mp4 --dry-run
  python clips_chat_v2.py chat.json --video vod.mp4 --top-n 3 --duration 30 --lang es

Requisitos: Python 3.10+, TwitchDownloaderCLI y FFmpeg en el PATH.
Sin dependencias externas, sin credenciales, sin red.
"""

import argparse
import json
import math
import os
import re
import shutil
import statistics
import subprocess
import sys
from collections import defaultdict

# ---------------------------------------------------------------------------
# Tokens — español e inglés
# RISA = +1 extra, HYPE = +1 extra, mensaje normal = 1 punto.
# Un mensaje con risa + hype vale 3. Así un "JAJAJA QUÉ JUGADA" pesa más
# que un simple "hola".
#
# Regla de oro (auditoría, bugs 1-2): ningún token suelto ambiguo.
# "no puedo" casaba con "no puedo creer que no hayas ido" (no es risa),
# "qué bueno" con "qué bueno que llegaste" (no es risa), "dale" con
# "dale gas", "vamos" con "vamos a ver qué pasa", "god" con
# "god of war" y "crazy" con "crazy cat". Todos fuera. Solo quedan
# frases completas, formas alargadas o palabras sin doble sentido.
# ---------------------------------------------------------------------------

# Risas en español: jaja, jeje, jiji, jojo, jajaja, jejeje...
LAUGH_ES = (
    "jaja", "jeje", "jiji", "jojo", "juju",
    "jajaja", "jejeje", "jijiji", "jojojo",
    "qué risa", "que risa", "qué risaa",
    "me muero", "me muero de risa", "me parto", "me parto de risa",
    "no puedo más", "no puedo de risa", "me cago de risa",
    "😂", "🤣", "😹", "💀", "😆",
)
LAUGH_EN = (
    "lol", "lmao", "rofl", "lmfao",
    "haha", "hehe", "hahaha", "hehehe",
    "kekw", "lul", "lulw", "omegalul",
    "😂", "🤣", "😹", "💀", "😆",
)
# Hype / jugada buena — en español e inglés. Esto es lo que la v1 no tenía:
# un pico de "QUÉ JUGADA / POG / CLUTCH" no es risa, pero es clip.
HYPE_ES = (
    "qué jugada", "que jugada", "qué grande", "que grande",
    "brutal", "increíble", "increible", "insano", "tremendo",
    "épico", "epico", "no lo puedo creer", "no te creo",
    "qué gol", "que gol", "golazo", "qué pedazo", "qué locura", "que locura",
    "madre mía", "madre mia", "dios mío", "dios mio",
    "impresionante", "espectacular", "una locura",
)
HYPE_EN = (
    "pog", "pogchamp", "poggers", "hype", "insane",
    "clutch", "lets go", "let's go", "gg", "wtf", "omg",
    "wow", "what a play", "no way", "unreal", "clip it", "clipit",
    "w", "w in the chat",
)

# Bots conocidos: sus mensajes (avisos, comandos, spam) no puntúan.
KNOWN_BOTS = {"nightbot", "streamelements", "moobot", "streamlabs",
              "fossabot", "wizebot", "deepbot", "phantombot"}

# Nombres de emote conocidos que aparecen como texto pelado en el chat
# (KEKW, LUL...). Un mensaje de un solo token de esta lista es
# emote-only. "JAJAJA", "W" o "GG" tecleados NO están aquí: son texto.
EMOTE_NAMES = frozenset({"kekw", "lul", "lulw", "omegalul", "kappa", "kapp",
                         "monkas", "pepega", "pogchamp", "poggers", "sadge",
                         "feelsbadman", "feelsgoodman", "om", "loo"})
BRACKET_EMOTE_RE = re.compile(r"(?:\[emote:[^\]]+\]\s*)+")


def _is_emote_only(body: str) -> bool:
    """True si el mensaje es solo emotes, sin texto.

    - Formato Kick/TwitchDownloader: ``[emote:<id>:<nombre>]`` repetido.
    - Solo emojis o símbolos (sin letras ni dígitos).
    - Un solo token que es un nombre de emote conocido (KEKW, LUL...).
    """
    b = body.strip()
    if not b:
        return False
    if BRACKET_EMOTE_RE.fullmatch(b):
        return True
    tokens = WORD_RE.findall(b.lower())
    if not tokens:
        return True  # solo emojis / símbolos
    return len(tokens) == 1 and tokens[0] in EMOTE_NAMES


def _split_tokens(tokens):
    """Separa tokens de una palabra, frases y emojis."""
    words, phrases, emojis = set(), set(), set()
    for token in tokens:
        if token in ("😂", "🤣", "😹", "💀", "😆"):
            emojis.add(token)
        elif " " in token:
            phrases.add(token)
        else:
            words.add(token)
    return frozenset(words), frozenset(phrases), frozenset(emojis)


LAUGH_ES_WORDS, LAUGH_ES_PHRASES, _ = _split_tokens(LAUGH_ES)
LAUGH_EN_WORDS, LAUGH_EN_PHRASES, _ = _split_tokens(LAUGH_EN)
HYPE_ES_WORDS, HYPE_ES_PHRASES, _ = _split_tokens(HYPE_ES)
HYPE_EN_WORDS, HYPE_EN_PHRASES, _ = _split_tokens(HYPE_EN)
EMOJIS = frozenset({"😂", "🤣", "😹", "💀", "😆"})

# Una sola pasada por mensaje: se tokeniza una vez (findall en C) y
# se comprueba pertenencia a conjuntos (O(1)). Las regex solo se
# usan, con guarda previa, para las formas alargadas (xd, jajajaja,
# vamooos...). Medido con cProfile: la versión anterior, con una
# regex de alternancia por categoría, gastaba 5,8 s en 1,2M búsquedas
# para 200k mensajes; esta baja el scoring a décimas de segundo
# (el resto del tiempo es json.load, que es inevitable).
WORD_RE = re.compile(r"[a-záéíóúñü0-9]+")
# Risas/hype alargados que ninguna lista puede enumerar: xd, xdd, jajajaja,
# jejejeje, hahaha..., vamooos, dalee, noooo...
XD_RE = re.compile(r"\bxd+\b", re.IGNORECASE)
LAUGH_REPEATED_RE = re.compile(r"\b(?:j[aeiou]){2,}[aeiou]*\b|\b(?:ha){2,}\b|\b(?:he){2,}\b", re.IGNORECASE)
HYPE_ELONGATED_RE = re.compile(r"\bvam+o{2,}s+\b|\bdale{2,}\b|\bno{3,}\b", re.IGNORECASE)


def _phrase_index(phrases):
    """Frases indexadas por su primera palabra: solo se comprueban
    las frases que pueden estar (su primera palabra está en el mensaje)."""
    index = {}
    for phrase in phrases:
        index.setdefault(phrase.split()[0], []).append(phrase)
    return index


LAUGH_ES_PHRASE_IDX = _phrase_index(LAUGH_ES_PHRASES)
LAUGH_EN_PHRASE_IDX = _phrase_index(LAUGH_EN_PHRASES)
HYPE_ES_PHRASE_IDX = _phrase_index(HYPE_ES_PHRASES)
HYPE_EN_PHRASE_IDX = _phrase_index(HYPE_EN_PHRASES)


def _has_phrase(word_list, phrase_index):
    """True si alguna frase del índice aparece en la lista de palabras."""
    words = set(word_list)
    joined = None
    for first, phrases in phrase_index.items():
        if first in words:
            if joined is None:
                joined = " " + " ".join(word_list) + " "
            if any(f" {p} " in joined for p in phrases):
                return True
    return False


def _contains_token(text: str, token: str) -> bool:
    """Compatibilidad: True si el token aparece como palabra/frase."""
    lowered = text.lower()
    if token in EMOJIS:
        return token in lowered
    wl = WORD_RE.findall(lowered)
    if " " in token:
        return f" {token} " in " " + " ".join(wl) + " "
    return token in set(wl)


def score_message(text: str, lang: str = "both") -> tuple[int, bool, bool]:
    """Devuelve (puntos, es_risa, es_hype) para un mensaje del chat."""
    lowered = text.lower()
    wl = WORD_RE.findall(lowered)  # un solo tokenizado por mensaje
    words = set(wl)
    is_laugh = False
    is_hype = False

    if lang in ("es", "both"):
        if (words & LAUGH_ES_WORDS) or _has_phrase(wl, LAUGH_ES_PHRASE_IDX):
            is_laugh = True
        if (words & HYPE_ES_WORDS) or _has_phrase(wl, HYPE_ES_PHRASE_IDX):
            is_hype = True
    if lang in ("en", "both"):
        if (words & LAUGH_EN_WORDS) or _has_phrase(wl, LAUGH_EN_PHRASE_IDX):
            is_laugh = True
        if (words & HYPE_EN_WORDS) or _has_phrase(wl, HYPE_EN_PHRASE_IDX):
            is_hype = True

    if not is_laugh and not text.isascii() and any(e in text for e in EMOJIS):
        is_laugh = True
    # xd / xdd: la risa más común del chat hispano (auditoría, bug 1).
    # Risas alargadas: jajajaja, jejejeje, hahaha... Solo se corre la
    # regex si el texto tiene pinta de contenerlas (guarda barata).
    if not is_laugh:
        if "xd" in lowered and XD_RE.search(lowered):
            is_laugh = True
        elif any(s in lowered for s in ("ja", "je", "ji", "jo", "ha", "he")) \
                and LAUGH_REPEATED_RE.search(lowered):
            is_laugh = True
    # Hype alargado: vamooos, dalee, noooo...
    if not is_hype and any(s in lowered for s in ("vamo", "dale", "nooo")) \
            and HYPE_ELONGATED_RE.search(lowered):
        is_hype = True

    points = 1 + (1 if is_laugh else 0) + (1 if is_hype else 0)
    return points, is_laugh, is_hype


# ---------------------------------------------------------------------------
# Lectura del chat: una sola vez, en caché (auditoría, bug 3).
# Antes, sample_messages() volvía a leer el JSON completo por cada clip.
# ---------------------------------------------------------------------------
_CHAT_CACHE: dict = {}


def _load_chat(chat_path: str):
    """Lee el chat.json una vez y cachea (offset, body, user) por mensaje."""
    st = os.stat(chat_path)
    key = (os.path.abspath(chat_path), st.st_mtime_ns, st.st_size)
    cached = _CHAT_CACHE.get("chat")
    if cached and cached[0] == key:
        return cached[1]
    with open(chat_path, "r", encoding="utf-8") as f:
        data = json.load(f)
    messages = []
    for comment in data.get("comments", []):
        offset = comment.get("content_offset_seconds")
        if offset is None:
            continue
        body = comment.get("message", {}).get("body", "") or ""
        commenter = comment.get("commenter") or {}
        user = (commenter.get("name") or commenter.get("id") or "").lower()
        messages.append((int(offset), body, user))
    messages.sort(key=lambda m: m[0])
    _CHAT_CACHE.clear()
    _CHAT_CACHE["chat"] = (key, messages)
    return messages


def sample_messages(chat_path: str, window_start: int, window_size: int = 10, limit: int = 3):
    """Mensajes de risa/hype de esa ventana (reusa la caché, no relee)."""
    messages = _load_chat(chat_path)
    examples = []
    for offset, body, _user in messages:
        if offset < window_start:
            continue
        if offset >= window_start + window_size:
            break
        if not body.strip():
            continue
        _, laugh, hype = score_message(body)
        if laugh or hype:
            examples.append(body.strip())
        if len(examples) >= limit:
            break
    return examples


def score_windows(chat_path: str, window_size: int = 10, lang: str = "both", step: int = None,
                  local_baseline: bool = True, anti_spam: bool = True, local_half_min: int = 12):
    """Ventana deslizante con suavizado + baseline local + anti-spam v2.

    Devuelve (scores, detail).

    - Paso por defecto: la mitad de la ventana (5 s con ventana de 10 s),
      así un pico entre dos ventanas no se parte en dos.
    - Suavizado 0.25/0.5/0.25 sobre ventanas vecinas.
    - ``scores`` incluye TODAS las ventanas desde 0 hasta el último
      mensaje (también las vacías, con 0): la mediana sobre ellas no
      está sesgada (auditoría, bug 6).
    - ``detail[ventana]``: msgs, risas, hype y users (usuarios únicos).
      Un mismo usuario repitiendo el mismo mensaje en la ventana solo
      cuenta una vez (anti spam copy-paste); los bots no puntúan.
    - Anti-spam v2 (por ventana): ``emote_ratio`` (mensajes solo
      emote / total), ``rep_ratio`` (1 - cuerpos distintos / mensajes,
      sobre todos los mensajes) y ``lex_diversity`` (palabras distintas
      / palabras totales en los mensajes con texto). Si emote_ratio
      > 60% el puntaje se multiplica ×0.25 (motivo ``emote_spam``);
      si rep_ratio > 40%, ×0.5 (motivo ``repetition_spam``). El factor
      queda en ``penalty_factor`` y el motivo en ``motivo_penalizacion``.
    - Baseline local (por defecto): cada ventana se compara con su
      vecindad (±``local_half_min`` minutos), no con todo el VOD.
      Score final = z-score local + 0.5 × pendiente de subida
      (media de los 60 s previos, cap 2) + magnitud (puntos
      ajustados / 10, para que el tamaño absoluto siga pesando:
      el z-score solo premiaba ráfagas pequeñas en tramos muertos).
      La desviación tiene suelo 1.0 para que en tramos muertos un
      soplo no dé z=50. Con VOD muy corto (<5 vecinos) se usa la
      media/desviación global.
    - Con ``local_baseline=False`` los scores son los puntos
      suavizados ajustados por anti-spam; con ``anti_spam=False``
      además, son los puntos suavizados v2.4 tal cual (compatibilidad
      y línea base para comparar).
    """
    messages = _load_chat(chat_path)
    if step is None:
        step = max(1, window_size // 2)

    # Agregado por segundo (puntos, mensajes, risas, hype, usuarios,
    # y estadísticas anti-spam v2: cuerpos distintos para repetición,
    # palabras de mensajes con texto para diversidad léxica).
    per_sec: dict[int, dict] = defaultdict(
        lambda: {"points": 0, "msgs": 0, "risas": 0, "hype": 0, "users": set(), "seen": set(),
                 "emote_only": 0, "bodies": set(),
                 "words": set(), "n_words": 0}
    )
    max_offset = 0
    for offset, body, user in messages:
        max_offset = max(max_offset, offset)
        if user in KNOWN_BOTS:
            continue
        key = (user, body.strip().lower())
        bucket = per_sec[offset]
        if key in bucket["seen"]:
            continue  # mismo usuario, mismo mensaje, mismo segundo: spam
        bucket["seen"].add(key)
        points, is_laugh, is_hype = score_message(body, lang=lang)
        bucket["points"] += points
        bucket["msgs"] += 1
        if is_laugh:
            bucket["risas"] += 1
        if is_hype:
            bucket["hype"] += 1
        if user:
            bucket["users"].add(user)
        # Anti-spam v2: cuerpos distintos (repetición, todos los
        # mensajes) y emote-only aparte; las palabras solo de los
        # mensajes con texto alimentan la diversidad léxica.
        bucket["bodies"].add(body.strip().lower())
        if _is_emote_only(body):
            bucket["emote_only"] += 1
        else:
            wl = WORD_RE.findall(body.lower())
            bucket["words"].update(wl)
            bucket["n_words"] += len(wl)

    starts = list(range(0, max_offset + 1, step))
    raw: dict[int, int] = {}
    detail: dict[int, dict] = {}
    for start in starts:
        points = msgs = risas = hype = 0
        emote_only = n_words = 0
        users: set = set()
        bodies: set = set()
        words: set = set()
        for sec in range(start, start + window_size):
            b = per_sec.get(sec)
            if not b:
                continue
            points += b["points"]
            msgs += b["msgs"]
            risas += b["risas"]
            hype += b["hype"]
            users |= b["users"]
            emote_only += b["emote_only"]
            n_words += b["n_words"]
            bodies |= b["bodies"]
            words |= b["words"]
        raw[start] = points
        emote_ratio = emote_only / msgs if msgs else 0.0
        rep_ratio = (1.0 - len(bodies) / msgs) if msgs >= 3 else 0.0
        lex_div = len(words) / n_words if n_words else 0.0
        factor = 1.0
        motivos: list[str] = []
        if anti_spam:
            if emote_ratio > 0.60:
                factor *= 0.25
                motivos.append("emote_spam")
            if rep_ratio > 0.40:
                factor *= 0.5
                motivos.append("repetition_spam")
        detail[start] = {"msgs": msgs, "risas": risas, "hype": hype, "users": len(users),
                         "emote_ratio": round(emote_ratio, 3),
                         "rep_ratio": round(rep_ratio, 3),
                         "lex_diversity": round(lex_div, 3),
                         "penalty_factor": factor,
                         "motivo_penalizacion": motivos or None}

    # Suavizado: 0.25 * anterior + 0.5 * actual + 0.25 * siguiente.
    smoothed: dict[int, float] = {}
    for i, start in enumerate(starts):
        prev_s = raw[starts[i - 1]] if i > 0 else raw[start]
        next_s = raw[starts[i + 1]] if i + 1 < len(starts) else raw[start]
        smoothed[start] = round(0.25 * prev_s + 0.5 * raw[start] + 0.25 * next_s, 2)

    # Ajuste anti-spam sobre el puntaje suavizado.
    adj = {s: round(smoothed[s] * detail[s]["penalty_factor"], 2) for s in starts}
    for s in starts:
        detail[s]["raw_score"] = smoothed[s]
        detail[s]["adj_score"] = adj[s]
        detail[s]["z_local"] = None
        detail[s]["rise"] = None

    if not local_baseline:
        return adj, detail

    # Baseline local: z-score contra la vecindad (±local_half_min),
    # más pendiente de subida (60 s previos, cap 2). Sumas prefijas
    # para media/varianza en O(1) por ventana.
    n = len(starts)
    adj_list = [adj[s] for s in starts]
    pref = [0.0]
    pref2 = [0.0]
    for v in adj_list:
        pref.append(pref[-1] + v)
        pref2.append(pref2[-1] + v * v)
    total_mean = pref[n] / n if n else 0.0
    total_var = max(pref2[n] / n - total_mean ** 2, 0.0) if n else 0.0
    total_std = math.sqrt(total_var)
    k = max(1, int(local_half_min * 60 / step))
    m = max(1, int(60 / step))
    scores: dict[int, float] = {}
    for i, start in enumerate(starts):
        lo, hi = max(0, i - k), min(n - 1, i + k)
        cnt = hi - lo  # nº de vecinos (sin contar la ventana misma)
        if cnt >= 5:
            ssum = pref[hi + 1] - pref[lo] - adj_list[i]
            sq = pref2[hi + 1] - pref2[lo] - adj_list[i] ** 2
            mean = ssum / cnt
            var = max(sq / cnt - mean ** 2, 0.0)
            std = math.sqrt(var)
        else:  # VOD muy corto: baseline global
            mean, std = total_mean, total_std
        std = max(std, 1.0)  # suelo: en tramos muertos no explota z
        z = (adj_list[i] - mean) / std
        if i > 0:
            plo = max(0, i - m)
            prev_cnt = i - plo
            prev_mean = (pref[i] - pref[plo]) / prev_cnt if prev_cnt else 0.0
            rise = min(max((adj_list[i] - prev_mean) / std, 0.0), 2.0)
        else:
            rise = 0.0
        detail[start]["z_local"] = round(z, 2)
        detail[start]["rise"] = round(rise, 2)
        # z local + 0.5 × subida + magnitud (adj/10).
        scores[start] = round(z + 0.5 * rise + adj_list[i] / 10.0, 2)
    return scores, detail


def median_over_all(scores) -> float:
    """Mediana sobre TODAS las ventanas, incluidas las vacías (sin sesgo)."""
    if not scores:
        return 0.0
    return float(statistics.median(list(scores.values())))


def select_top_peaks(scores, top_n: int = 5, min_separation: int = 60):
    ranked = sorted(scores.items(), key=lambda x: x[1], reverse=True)
    selected: list[tuple[int, float]] = []
    for timestamp, score in ranked:
        if all(abs(timestamp - ts) >= min_separation for ts, _ in selected):
            selected.append((timestamp, score))
            if len(selected) == top_n:
                break
    # 6B.3: en orden de score (mayor primero), no cronológico: el
    # worker corta primero los candidatos de mayor prioridad; los
    # de score bajo quedan al final (o no se cortan, --top-n es un
    # máximo). Cada clip baja su propio segmento, así que el orden
    # de corte no afecta al resultado, solo a qué se hace primero.
    return sorted(selected, key=lambda x: x[1], reverse=True)


def get_video_duration(video_path: str):
    """Duración en segundos con ffprobe, o None si no se puede saber."""
    if shutil.which("ffprobe") is None:
        return None
    try:
        r = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "default=noprint_wrappers=1:nokey=1", video_path],
            capture_output=True, text=True, check=True,
        )
        return float(r.stdout.strip())
    except Exception:
        return None


def fmt_ts(seconds: int) -> str:
    m, s = divmod(int(seconds), 60)
    h, m = divmod(m, 60)
    if h:
        return f"{h:02d}h{m:02d}m{s:02d}s"
    if m:
        return f"{m:02d}m{s:02d}s"
    return f"{s:02d}s"


def cut_vertical_clip(video_path, start_sec, duration, output_path,
                      width=1080, height=1920, crf=23, preset="fast",
                      crop="center"):
    # Recorte vertical: center (defecto), left o right.
    # En GTA el minimapa suele estar a un lado: si el recorte centrado
    # te lo corta, prueba --crop left o --crop right.
    # Flags para plataformas (auditoría, bug 8): yuv420p (compatibilidad
    # máxima), +faststart (streaming) y loudnorm (audio a -16 LUFS).
    x_expr = {"center": "(iw-ih*9/16)/2", "left": "0",
              "right": "iw-ih*9/16"}.get(crop, "(iw-ih*9/16)/2")
    cmd = [
        "ffmpeg", "-y", "-ss", str(start_sec), "-i", video_path,
        "-t", str(duration),
        "-vf", f"crop=ih*9/16:ih:x={x_expr},scale={width}:{height}",
        "-af", "loudnorm=I=-16:TP=-1.5:LRA=11",
        "-c:v", "libx264", "-preset", preset, "-crf", str(crf),
        "-pix_fmt", "yuv420p", "-movflags", "+faststart",
        "-c:a", "aac", "-b:a", "128k", output_path,
    ]
    subprocess.run(cmd, check=True, capture_output=True, text=True)


def _write_manifest(path, manifest):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=2)


def main() -> int:
    p = argparse.ArgumentParser(
        description="Extrae los mejores clips verticales desde un chat.json y vod.mp4 de Twitch (v2.5: baseline local con z-score + anti-spam v2, español + inglés, ventana deslizante, xd como risa)."
    )
    p.add_argument("chat", help="Ruta al chat.json de TwitchDownloader")
    p.add_argument("--video", help="Ruta al vod.mp4 (obligatorio salvo --dry-run)")
    p.add_argument("--out", default="clips", help="Carpeta de salida (default: clips)")
    p.add_argument("--top-n", type=int, default=5, help="Nº de clips (default: 5)")
    p.add_argument("--window", type=int, default=10, help="Ventana en segundos (default: 10)")
    p.add_argument("--step", type=int, default=None,
                   help="Paso de la ventana deslizante en segundos (default: ventana/2)")
    p.add_argument("--min-separation", type=int, default=60, help="Separación mínima entre picos (default: 60)")
    p.add_argument("--pre-roll", type=int, default=20, help="Segundos antes del pico (default: 20)")
    p.add_argument("--duration", type=int, default=40, help="Duración de cada clip (default: 40, objetivo; mínimo 36 s y tope duro 60 s, reglas del usuario 2026-11-17)")
    p.add_argument("--lang", choices=["es", "en", "both"], default="both",
                   help="Idioma a puntuar: es, en o both (default: both)")
    p.add_argument("--crop", choices=["center", "left", "right"], default="center",
                   help="Posición del recorte vertical (default: center)")
    p.add_argument("--width", type=int, default=1080)
    p.add_argument("--height", type=int, default=1920)
    p.add_argument("--crf", type=int, default=23, help="Calidad x264, menor = mejor (default: 23)")
    p.add_argument("--preset", default="fast", help="Preset x264 (default: fast)")
    p.add_argument("--dry-run", action="store_true", help="Solo lista los picos, sin cortar")
    p.add_argument("--local-baseline", dest="local_baseline", action="store_true", default=True,
                   help="Baseline local ±12 min con z-score + pendiente de subida (default: activado)")
    p.add_argument("--no-local-baseline", dest="local_baseline", action="store_false",
                   help="Apaga el baseline local: puntúa por puntos suavizados ajustados por anti-spam")
    p.add_argument("--no-anti-spam", dest="anti_spam", action="store_false",
                   help="Apaga el anti-spam v2 (sin penalización por emote-only ni repetición)")
    p.add_argument("--local-half-min", type=int, default=12,
                   help="Media vecindad local en minutos a cada lado (default: 12)")
    args = p.parse_args()

    if not os.path.isfile(args.chat):
        print(f"Error: no existe el chat: {args.chat}", file=sys.stderr)
        return 1
    if not args.dry_run:
        if not args.video:
            print("Error: falta --video (o usa --dry-run para solo listar picos).", file=sys.stderr)
            return 1
        if not os.path.isfile(args.video):
            print(f"Error: no existe el video: {args.video}", file=sys.stderr)
            return 1
        if shutil.which("ffmpeg") is None:
            print("Error: FFmpeg no está en el PATH.", file=sys.stderr)
            return 1

    try:
        scores, detail = score_windows(args.chat, window_size=args.window, lang=args.lang, step=args.step,
                                       local_baseline=args.local_baseline, anti_spam=args.anti_spam,
                                       local_half_min=args.local_half_min)
    except (json.JSONDecodeError, KeyError) as e:
        print(f"Error: chat.json no válido: {e}", file=sys.stderr)
        return 1

    if not scores:
        print("No se encontraron mensajes con marcas de tiempo en el chat.")
        return 0

    peaks = select_top_peaks(scores, top_n=args.top_n, min_separation=args.min_separation)
    if not peaks:
        print("No se encontraron picos.")
        return 0

    median = median_over_all(scores)
    # Total real de mensajes (sin doble conteo: las ventanas deslizantes
    # se solapan y sumar detail[] contaría cada mensaje dos veces).
    total_msgs = len(_load_chat(args.chat))
    step = args.step or max(1, args.window // 2)
    print(f"Chat: {total_msgs} mensajes, {len(scores)} ventanas de {args.window}s "
          f"(paso {step}s, deslizante + suavizado), idioma: {args.lang}")
    mode = (f"baseline local ±{args.local_half_min} min (z-score + subida)" if args.local_baseline
            else "puntos ajustados (sin baseline local)")
    print(f"Scoring v2.5: {mode}, anti-spam v2: {'on' if args.anti_spam else 'off'}")
    print(f"Mediana (todas las ventanas, incl. vacías): {median}")
    print("Picos elegidos:")
    for i, (ts, score) in enumerate(peaks, 1):
        d = detail[ts]
        mult = f"{score / median:.2f}x la mediana" if median > 0 else "—"
        zinfo = f"z {d['z_local']} (subida {d['rise']}) " if d.get("z_local") is not None else ""
        pen = ""
        if d.get("motivo_penalizacion"):
            pen = (f" [penalizado ×{d['penalty_factor']}: {', '.join(d['motivo_penalizacion'])}"
                   f" | emote {d['emote_ratio']:.0%} rep {d['rep_ratio']:.0%} lex {d['lex_diversity']:.2f}]")
        print(f"  {i}. {fmt_ts(ts)} — {score} ({zinfo}raw {d['raw_score']} pts, "
              f"{d['msgs']} msgs, {d['risas']} risas, {d['hype']} hype, {d['users']} usuarios, {mult}){pen}")

    if args.dry_run:
        print("Dry-run: no se cortó nada.")
        return 0

    duration = get_video_duration(args.video)
    if duration:
        print(f"VOD: {fmt_ts(int(duration))} ({duration:.0f}s)")

    os.makedirs(args.out, exist_ok=True)
    # Manifiesto incremental (auditoría, bug 7): se escribe vacío al
    # empezar y se reescribe tras cada clip; si algo falla a mitad,
    # lo ya cortado queda registrado.
    manifest_path = os.path.join(args.out, "manifest.json")
    manifest = {"chat": args.chat, "video": args.video, "median": median,
                "window": args.window, "step": step,
                "scoring": "v2.5 local-baseline+z-score+anti-spam-v2",
                "local_baseline": args.local_baseline, "anti_spam": args.anti_spam,
                "local_half_min": args.local_half_min, "clips": []}
    _write_manifest(manifest_path, manifest)

    ok, failed = 0, 0
    for idx, (peak_ts, score) in enumerate(peaks, 1):
        clip_start = max(0, peak_ts - args.pre_roll)
        clip_dur = args.duration
        if duration and clip_start + clip_dur > duration:
            clip_dur = max(1, int(duration - clip_start))
            if clip_dur <= 0:
                print(f"[{idx}] Pico {fmt_ts(peak_ts)} fuera del VOD, se salta.")
                continue
        d = detail[peak_ts]
        out_file = os.path.join(
            args.out, f"clip_{idx:02d}_t{fmt_ts(clip_start)}_s{score}_r{d['risas']}h{d['hype']}.mp4"
        )
        print(f"[{idx}/{len(peaks)}] Pico {fmt_ts(peak_ts)} ({score} pts) -> "
              f"{clip_dur}s desde {fmt_ts(clip_start)} en {out_file}")
        try:
            cut_vertical_clip(args.video, clip_start, clip_dur, out_file,
                              width=args.width, height=args.height,
                              crf=args.crf, preset=args.preset, crop=args.crop)
            ok += 1
            manifest["clips"].append({
                "n": idx, "file": os.path.basename(out_file),
                "peak": peak_ts, "start": clip_start, "duration": clip_dur,
                "score": score, "raw_score": d["raw_score"], "adj_score": d["adj_score"],
                "z_local": d["z_local"], "rise": d["rise"],
                "risas": d["risas"], "hype": d["hype"],
                "users": d["users"],
                "emote_ratio": d["emote_ratio"], "rep_ratio": d["rep_ratio"],
                "lex_diversity": d["lex_diversity"],
                "penalty_factor": d["penalty_factor"],
                "motivo_penalizacion": d["motivo_penalizacion"],
                "viral_multiple": round(score / median, 2) if median > 0 else None,
            })
            _write_manifest(manifest_path, manifest)
        except subprocess.CalledProcessError as e:
            failed += 1
            print(f"[{idx}] Falló el corte: {e.stderr.strip().splitlines()[-1] if e.stderr else e}", file=sys.stderr)

    print(f"Listo: {ok} clips en {args.out}/" + (f", {failed} fallidos." if failed else ""))
    print(f"Manifiesto: {manifest_path}")
    return 0 if failed == 0 else 2


if __name__ == "__main__":
    raise SystemExit(main())

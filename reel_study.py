#!/usr/bin/env python3
"""
reel_study.py — estudiar reels virales y sacarles el patrón, no copiarlos.

Origen: el reel de David Madrazo (@davidmadrazo.ia, "content-os",
2026-11-12) que el usuario pasó el 2026-11-16. Su sistema vigila
miles de reels, detecta los virales, los transcribe, descompone
hook/estructura/CTA y reescribe guiones con 122 reglas, genera el
vídeo con voz clonada y lo programa solo, "100% AUTOMÁTICO".

De eso, este módulo toma SOLO la primera mitad, adaptada a este
proyecto:

  * vigilar reels virales del nicho (GTA 6) — a mano, por URL, no
    con un scraper masivo;
  * descomponer cada reel en hook / cuerpo / CTA, hashtags y
    engagement relativo al autor;
  * agregar los patrones (qué tipo de hook y de CTA se repite,
    largo de caption, hashtags) y devolver plantillas genéricas
    con huecos ({what}, {game}), NUNCA el caption copiado.

Lo que NO hace, a propósito (reglas del proyecto, 2026-11-16):

  * NO clona voces ni genera vídeo: nuestros clips usan el audio
    real del directo, con permiso del streamer en lista blanca.
  * NO publica solo ni programa solo: los títulos sugeridos pasan
    por el check promesa-payoff de viral_package.py y quedan para
    aprobación humana en la cola (queue.py). Nada se publica
    desde aquí.
  * NO copia títulos ni captions de otros creadores: las
    plantillas son patrones ("pregunta", "superlativo", "cita del
    chat"), y el título final sale de la ficha del momento
    (moment_card.py), como siempre.
  * NO inventa engagement: si no hay seguidores del autor, el
    engagement es None, no un 5 inventado. Y un reel con muchos
    likes de una cuenta enorme (Rockstar, 33M de seguidores) no es
    comparable con uno de una cuenta pequeña: el informe lo dice
    y el engagement se lee siempre junto al nº de seguidores.

Entrada: dicts de reel normalizados, con estas claves (todas
opcionales salvo url/caption):

    {"url", "username", "caption", "likes", "comments",
     "followers", "created_at", "media_type"}

Se aceptan también las claves que devuelve instagram-cli post
(post_caption, like_count, comment_count, follower_count,
post_url) y las de social.search (author_handle, likes...).
La lectura de Instagram vive en fetch_reel() (opcional, con
instagram-cli); el análisis en sí es puro, sin red, sin keys,
sin dependencias: todo local y gratis ($0).

Uso como librería:

    from reel_study import analyze_reel, aggregate_patterns, \\
        hook_templates, suggest_titles_from_patterns, enrich_package
    analyses = [analyze_reel(r) for r in reels]
    patterns = aggregate_patterns(analyses)
    templates = hook_templates(patterns, lang="es", game="GTA 6")
    titles = suggest_titles_from_patterns(card, patterns, lang="es")

Uso como CLI (los reels se pasan por URL o por JSON):

    python reel_study.py --url https://www.instagram.com/reel/XXXX/ \\
        --url https://www.instagram.com/reel/YYYY/ \\
        --account-id 17841429035950873 --out reel_patterns.json
    python reel_study.py --from-json reels.json --out reel_patterns.json

El --account-id solo se usa para fetch_reel(); sin él, --url
falla con un mensaje claro en vez de inventar datos.
Máximo 10 URLs por corrida: estudiar reels es una pasada manual,
no un scrapeo; si hace falta más, se corre otra vez.
"""

import argparse
import json
import re
import statistics
import subprocess
import unicodedata
from collections import Counter
from pathlib import Path

MAX_URLS_PER_RUN = 10
MIN_SAMPLE_NOTE = 5  # menos de 5 reels: muestra pequeña, se avisa

# ---------------------------------------------------------------------------
# Normalización
# ---------------------------------------------------------------------------

STOPWORDS = set(
    "el la los las un una unos unas de del en y o a que no si es se su sus "
    "con por para como mas pero que cuando donde esto esta este eso esa "
    "yo tu el ella nosotros vos sos estas esta estan muy bien mal ya ahi "
    "ahora despues antes todo todos nada nadie alguien algo vez veces "
    "the a an and or of to in on it is are was were be been this that "
    "with for you he she they we it s".split()
)

SUPERLATIVE_WORDS = (
    "mejor", "peor", "locura", "loco", "brutal", "increible", "increíble",
    "insano", "epico", "épico", "imposible", "nunca", "nadie", "tremendo",
    "best", "craziest", "insane", "epic", "impossible", "nobody", "ever",
)

CTA_KEYWORDS = {
    "follow": ("sigueme", "sígueme", "follow", "sigue a", "seguime"),
    "comment_word": ("comenta", "comment"),
    "share": ("comparte", "share", "enviaselo", "envíaselo"),
    "save": ("guarda", "save"),
    "link_bio": ("link en bio", "link in bio", "enlace en bio", "en bio",
                 "youtube.com", "linktr.ee"),
}


def _norm(text: str) -> str:
    """Minúsculas, sin tildes, espacios colapsados."""
    text = unicodedata.normalize("NFKD", text or "")
    text = "".join(c for c in text if not unicodedata.combining(c))
    text = re.sub(r"[^a-z0-9#@ ]", " ", text.lower())
    return re.sub(r"\s+", " ", text).strip()


def _words(text: str) -> list:
    return [w for w in _norm(text).split()
            if w and w not in STOPWORDS and not w.startswith("#")]


def _hashtags(caption: str) -> list:
    return re.findall(r"#\w+", caption or "")


def _emoji_count(text: str) -> int:
    return sum(1 for ch in (text or "") if ord(ch) > 0x1F000)


def normalize_reel(raw: dict) -> dict:
    """Un reel en cualquier formato de entrada -> formato canónico.

    Acepta claves de instagram-cli (post_caption, like_count...),
    de social.search y el formato canónico. Los números que no se
    pueden leer quedan en None, nunca en 0 inventado.
    """
    raw = raw or {}

    def _int(*keys):
        for k in keys:
            v = raw.get(k)
            if v is None:
                continue
            try:
                return int(v)
            except (TypeError, ValueError):
                continue
        return None

    return {
        "url": raw.get("url") or raw.get("post_url") or None,
        "username": (raw.get("username") or raw.get("author_handle")
                     or raw.get("author_name") or None),
        "caption": raw.get("caption") or raw.get("post_caption") or "",
        "likes": _int("likes", "like_count"),
        "comments": _int("comments", "comment_count"),
        "followers": _int("followers", "follower_count"),
        "created_at": raw.get("created_at") or raw.get("post_created_at"),
        "media_type": raw.get("media_type"),
    }


# ---------------------------------------------------------------------------
# Análisis de un reel
# ---------------------------------------------------------------------------

def classify_hook(caption: str, lang="es"):
    """Tipo de hook de la primera línea del caption.

    Devuelve (tipo, texto_del_hook). Tipos: question, number,
    superlative, quote, promise, statement. La primera línea es el
    hook en Instagram: es lo que se ve sin desplegar el caption.
    Si no hay caption, tipo "none".
    """
    lines = [ln.strip() for ln in (caption or "").splitlines() if ln.strip()]
    if not lines:
        return "none", ""
    hook = lines[0]
    low = _norm(hook)
    if "?" in hook or hook.startswith("¿"):
        return "question", hook
    # Un número solo cuenta como hook de número si no es el nombre
    # del juego ("GTA 6") ni parte de un @usuario ("@madi2hotty"):
    # esos dígitos no son "un número que no sabías", son ruido.
    hook_no_handles = re.sub(r"@\w+", " ", hook)
    hook_no_game = re.sub(r"\bgta\s*(6|vi|v|5)\b", " ", hook_no_handles,
                          flags=re.IGNORECASE)
    if re.search(r"\d", hook_no_game):
        return "number", hook
    if any(w in low.split() for w in SUPERLATIVE_WORDS):
        return "superlative", hook
    if hook.startswith(('"', "“", "El chat:", "Chat:")):
        return "quote", hook
    if any(w in low.split() for w in
           ("mira", "esto", "nadie", "espera", "watch", "how", "nobody")):
        return "promise", hook
    return "statement", hook


def classify_cta(caption: str):
    """Tipo de CTA del caption (se busca en las 2 últimas líneas
    primero, y si no en todo el caption).

    Devuelve (tipo, línea). Tipos: follow, comment_word, share,
    save, link_bio, comment_question, none. "comment_question" es
    una pregunta al final que invita a comentar sin pedirlo
    explícitamente ("¿Lo viste?", "¿qué harías tú?").
    """
    lines = [ln.strip() for ln in (caption or "").splitlines() if ln.strip()]
    if not lines:
        return "none", ""
    tail = lines[-2:]
    tail_norm = _norm(" ".join(tail))
    full_norm = _norm(caption or "")
    for cta_type, keywords in CTA_KEYWORDS.items():
        if any(k in tail_norm or k in full_norm for k in keywords):
            line = next((ln for ln in tail if _norm(ln) and any(
                k in _norm(ln) for k in keywords)), tail[-1])
            return cta_type, line
    if "?" in " ".join(tail):
        return "comment_question", tail[-1]
    return "none", ""


def engagement_rate(likes, comments, followers):
    """Engagement (likes+comentarios)/seguidores, o None sin datos.

    None si faltan likes o seguidores: un reel sin seguidores del
    autor no tiene engagement medible, y no se inventa.
    """
    if likes is None or followers is None or followers <= 0:
        return None
    return round((likes + (comments or 0)) / followers, 4)


def outlier_multiple(likes, author_median_likes):
    """Cuántas veces el reel supera la mediana del autor, o None.

    Es la medida "content-os" (viral = muy por encima de la media
    del propio creador, no en absoluto). Sin mediana del autor no
    se puede calcular: None, no 1,0 inventado.
    """
    if likes is None or not author_median_likes:
        return None
    return round(likes / author_median_likes, 2)


def analyze_reel(raw: dict, author_median_likes=None) -> dict:
    """Descompone un reel en hook / cuerpo / CTA / métricas.

    No copia el caption entero como "patrón": guarda el hook (1ª
    línea), la CTA (últimas líneas) y los conteos. El caption
    completo queda en el dict de entrada, no en el informe.
    """
    reel = normalize_reel(raw)
    caption = reel["caption"] or ""
    lines = [ln.strip() for ln in caption.splitlines() if ln.strip()]
    hook_type, hook_text = classify_hook(caption)
    cta_type, cta_text = classify_cta(caption)
    hashtags = _hashtags(caption)
    likes, comments, followers = reel["likes"], reel["comments"], reel["followers"]
    return {
        "url": reel["url"],
        "username": reel["username"],
        "created_at": reel["created_at"],
        "hook_type": hook_type,
        "hook_text": hook_text,
        "cta_type": cta_type,
        "cta_text": cta_text,
        "body_lines": len(lines) - (1 if lines else 0),
        "caption_chars": len(caption),
        "caption_words": len(_words(caption)),
        "hashtags": hashtags,
        "hashtag_count": len(hashtags),
        "emoji_count": _emoji_count(caption),
        "likes": likes,
        "comments": comments,
        "followers": followers,
        "engagement_rate": engagement_rate(likes, comments, followers),
        "outlier_multiple": outlier_multiple(likes, author_median_likes),
        "hook_words": _words(hook_text),
    }


# ---------------------------------------------------------------------------
# Agregado: patrones de la muestra
# ---------------------------------------------------------------------------

def aggregate_patterns(analyses) -> dict:
    """Patrones de una lista de análisis (analyze_reel).

    Devuelve conteos de hook/CTA, medianas de largo y engagement,
    hashtags y palabras de hook más repetidas, y avisos. Con menos
    de 5 reels la muestra es pequeña y se marca: los patrones son
    orientativos, no una regla.
    """
    analyses = list(analyses or [])
    n = len(analyses)
    hooks = Counter(a["hook_type"] for a in analyses)
    ctas = Counter(a["cta_type"] for a in analyses)
    hashtags = Counter(h for a in analyses for h in a["hashtags"])
    hook_words = Counter(w for a in analyses for w in a["hook_words"])
    engagements = [a["engagement_rate"] for a in analyses
                   if a["engagement_rate"] is not None]
    caption_chars = [a["caption_chars"] for a in analyses]
    notes = []
    if n == 0:
        notes.append("sin reels: no hay patrones que sacar")
    elif n < MIN_SAMPLE_NOTE:
        notes.append(
            f"muestra pequeña ({n} reels, mínimo orientativo "
            f"{MIN_SAMPLE_NOTE}): los patrones son orientativos")
    if engagements and max(engagements) / max(min(engagements), 1e-9) > 10:
        notes.append(
            "engagement muy disperso entre autores: comparar siempre "
            "con los seguidores de cada autor, no en absoluto")
    return {
        "sample_n": n,
        "hook_types": dict(hooks.most_common()),
        "top_hook_type": hooks.most_common(1)[0][0] if hooks else None,
        "cta_types": dict(ctas.most_common()),
        "top_cta_type": ctas.most_common(1)[0][0] if ctas else None,
        "top_hashtags": dict(hashtags.most_common(10)),
        "top_hook_words": dict(hook_words.most_common(10)),
        "median_caption_chars": (statistics.median(caption_chars)
                                 if caption_chars else None),
        "median_engagement_rate": (statistics.median(engagements)
                                   if engagements else None),
        "notes": notes,
    }


# ---------------------------------------------------------------------------
# Plantillas genéricas (patrón, no copia) + títulos sugeridos
# ---------------------------------------------------------------------------

def hook_templates(patterns, lang="es", game="GTA 6"):
    """Plantillas de hook genéricas, con huecos, desde los patrones.

    Solo se devuelven plantillas del tipo de hook más común (y del
    segundo, si existe): un patrón que no se repite en la muestra
    no se convierte en plantilla. {what} se rellena con la acción
    de la ficha del momento; {game} con el juego real; {quote} con
    una cita literal del chat/streamer. Sin ficha no se rellena
    nada: la plantilla queda con huecos, visible como tal.
    """
    patterns = patterns or {}
    hook_types = patterns.get("hook_types") or {}
    ordered = [t for t, _ in sorted(hook_types.items(),
                                    key=lambda kv: -kv[1])]
    game_label = game or "el juego"
    templates = []
    for hook_type in ordered[:2]:
        if lang == "en":
            table = {
                "question": f"Did you see { '{what}' }?",
                "number": f"{ '{what}' } — { '{n}' } in seconds",
                "superlative": f"{game_label}: " + "{what} nobody believed",
                "quote": f"Chat said it: \"{'{quote}'}\"",
                "promise": f"Watch why chat lost it over {'{what}'}",
                "statement": f"{game_label}: {'{what}'} — chat reacted live",
            }
        else:
            table = {
                "question": "¿Viste {what}?",
                "number": "{what} en {n} segundos",
                "superlative": f"{game_label}: " + "{what} que nadie se creyó",
                "quote": "El chat: \"{quote}\"",
                "promise": "Mira por qué el chat explotó con {what}",
                "statement": f"{game_label}: " + "{what} — el chat reaccionó en vivo",
            }
        if hook_type in table:
            templates.append({"hook_type": hook_type,
                              "template": table[hook_type],
                              "lang": lang})
    return templates


def suggest_titles_from_patterns(moment_card, patterns, lang="es", n=5):
    """Títulos sugeridos desde los patrones + la ficha del momento.

    Base: generate_titles() de viral_package (10 títulos con check).
    Encima, si el patrón dominante es pregunta o superlativo y la
    ficha tiene qué/cita, se construyen 1-2 candidatos con ese
    patrón y se validan con check_title(): solo pasan los que el
    check aprueba. Sin ficha, o sin patrón, devuelve solo la base.
    Nunca devuelve un título que no haya pasado el check.
    """
    try:
        from viral_package import check_title, generate_titles
    except Exception:
        return []
    card = moment_card or {}
    if not card:
        return []
    base = generate_titles(card, lang=lang, n=n)
    out = list(base)
    seen = {t["title"] for t in out}
    top_hook = (patterns or {}).get("top_hook_type")
    what = card.get("what")
    quote = (card.get("quote_chat") or {}).get("text") or ""
    game_label = card.get("game_label") or "desconocido"
    candidates = []
    if top_hook == "question" and what:
        if lang == "en":
            candidates.append(f"{what[:1].upper() + what[1:]} — did you see it?")
        else:
            candidates.append(f"¿Viste {what}?")
    if top_hook == "superlative" and what and card.get("game"):
        if lang == "en":
            candidates.append(f"{game_label}: {what[:1].upper() + what[1:]} nobody believed")
        else:
            candidates.append(f"{game_label}: {what} que nadie se creyó")
    if top_hook == "quote" and quote:
        if lang == "en":
            candidates.append(f"Chat said it: \"{quote[:30]}\"")
        else:
            candidates.append(f"El chat: \"{quote[:28]}\"")
    for title in candidates:
        if title in seen or len(title) > 60:
            continue
        ok, reason = check_title(title, card)
        if ok:
            seen.add(title)
            out.insert(0, {"title": title, "issues": [],
                           "warnings": [], "check_ok": True,
                           "check_reason": reason,
                           "from_reel_pattern": top_hook})
    return out[:n]


def enrich_package(package, patterns, moment_card=None, lang="es"):
    """Añade el estudio de reels a un paquete viral, sin tocarlo.

    Devuelve una copia del paquete con la clave "reel_study":
    muestra, hooks/CTA dominantes, plantillas y títulos sugeridos
    (validados). El best_title, la descripción y los hashtags del
    paquete NO cambian: lo sugerido es extra, para que el humano
    elija en la aprobación.
    """
    pkg = dict(package or {})
    patterns = patterns or {}
    pkg["reel_study"] = {
        "sample_n": patterns.get("sample_n", 0),
        "top_hook_type": patterns.get("top_hook_type"),
        "top_cta_type": patterns.get("top_cta_type"),
        "median_engagement_rate": patterns.get("median_engagement_rate"),
        "notes": patterns.get("notes", []),
        "hook_templates": hook_templates(patterns, lang=lang,
                                         game=(moment_card or {}).get("game")),
        "suggested_titles": suggest_titles_from_patterns(
            moment_card, patterns, lang=lang) if moment_card else [],
        "source": "reel_study.py (patrones de reels virales, no copia)",
    }
    return pkg


# ---------------------------------------------------------------------------
# Lectura de Instagram (opcional, por URL)
# ---------------------------------------------------------------------------

def fetch_reel(url, account_id):
    """Un reel por URL con instagram-cli -> dict normalizado.

    Necesita el --account-id del usuario (su user_fbid). Falla con
    un mensaje claro si no hay cuenta o si Instagram no devuelve
    el post: nunca devuelve un reel inventado.
    """
    if not account_id:
        raise ValueError("fetch_reel necesita account_id (user_fbid de Instagram)")
    cmd = ["/opt/hatch/bin/instagram-cli", "post",
           "--account-id", str(account_id), "--url", url]
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
    if r.returncode != 0:
        raise RuntimeError(f"instagram-cli falló ({r.returncode}): "
                           f"{(r.stderr or r.stdout or '').strip()[:300]}")
    try:
        data = json.loads(r.stdout)
    except json.JSONDecodeError as e:
        raise RuntimeError(f"instagram-cli devolvió JSON no válido: {e}")
    posts = data.get("posts") or []
    if not posts:
        raise RuntimeError(f"Instagram no devolvió el post de {url}")
    return normalize_reel(posts[0])


def load_reels_from_json(path):
    """Reels desde un JSON: salida de instagram-cli post, lista, o
    {"reels": [...]}, o un solo reel. Devuelve lista normalizada."""
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if isinstance(data, dict) and data.get("posts"):
        raws = data["posts"]
    elif isinstance(data, dict) and data.get("reels"):
        raws = data["reels"]
    elif isinstance(data, list):
        raws = data
    elif isinstance(data, dict):
        raws = [data]
    else:
        raise ValueError("JSON de reels no reconocido")
    return [normalize_reel(r) for r in raws]


def save_report(analyses, patterns, out_path):
    """Guarda el informe completo (análisis + patrones)."""
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    report = {
        "source": "reel_study.py — patrones de reels virales de GTA, no copia",
        "sample_n": patterns.get("sample_n", 0),
        "patterns": patterns,
        "reels": analyses,
        "hook_templates_es": hook_templates(patterns, lang="es"),
        "hook_templates_en": hook_templates(patterns, lang="en"),
    }
    out_path.write_text(json.dumps(report, ensure_ascii=False, indent=2),
                        encoding="utf-8")
    return out_path


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    p = argparse.ArgumentParser(
        description="Estudiar reels virales (hook/CTA/patrones), sin copiarlos.")
    p.add_argument("--url", action="append", default=[],
                   help="URL de reel de Instagram (repetible, máx. 10 en total)")
    p.add_argument("--from-json", default=None,
                   help="JSON con reels (salida de instagram-cli post o lista)")
    p.add_argument("--account-id", default=None,
                   help="user_fbid de Instagram, solo para --url")
    p.add_argument("--game", default="GTA 6")
    p.add_argument("--lang", default="es", choices=("es", "en", "both"))
    p.add_argument("--out", default="reel_patterns.json",
                   help="Informe JSON de salida")
    args = p.parse_args()

    if len(args.url) > MAX_URLS_PER_RUN:
        raise SystemExit(f"Máx. {MAX_URLS_PER_RUN} URLs por corrida "
                         f"({len(args.url)} dadas): estudiar reels es una "
                         f"pasada manual, no un scrapeo.")
    reels = []
    if args.from_json:
        reels = load_reels_from_json(args.from_json)
    for url in args.url:
        reels.append(fetch_reel(url, args.account_id))

    analyses = [analyze_reel(r) for r in reels]
    patterns = aggregate_patterns(analyses)
    out_path = save_report(analyses, patterns, args.out)

    print(f"Reels estudiados: {patterns['sample_n']}")
    print(f"Hook dominante: {patterns['top_hook_type']} "
          f"{patterns['hook_types']}")
    print(f"CTA dominante: {patterns['top_cta_type']} "
          f"{patterns['cta_types']}")
    print(f"Engagement mediano: {patterns['median_engagement_rate']}")
    print(f"Caption mediano: {patterns['median_caption_chars']} caracteres")
    print(f"Top hashtags: {patterns['top_hashtags']}")
    for note in patterns["notes"]:
        print(f"Nota: {note}")
    for t in hook_templates(patterns, lang="es", game=args.game):
        print(f"Plantilla ES [{t['hook_type']}]: {t['template']}")
    print(f"Informe guardado en {out_path}")
    print("Esto NO publica nada: los títulos sugeridos van a la "
          "aprobación humana, como siempre.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

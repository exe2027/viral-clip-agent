#!/usr/bin/env python3
"""
seo_score.py — score SEO 0-100 POR PLATAFORMA.

Cada plataforma puntúa distinto (pesos sobre 100):

    YouTube   Título 35 / Descripción 20 / Tags 10 / Miniatura 20 / Coherencia 15
    TikTok    Título 25 / Descripción 30 / Tags 20 / Miniatura 10 / Coherencia 15
    Instagram Título 25 / Descripción 30 / Tags 20 / Miniatura 15 / Coherencia 10
    Facebook  Título 25 / Descripción 30 / Tags 15 / Miniatura 20 / Coherencia 10

Reglas que se verifican (todas locales, sin APIs):
  * "GTA 6" (o la keyword del juego) en los primeros 30-40 caracteres.
  * Título 35-60 caracteres (ideal 40-55; más de 60 penaliza).
  * Descripción con hook + keyword en los primeros 100-120 caracteres,
    CTA/pregunta al final, sin keyword stuffing (más de 3 repeticiones
    de la keyword = -30% del componente de descripción).
  * 3-5 hashtags en el caption (5-15 tags en YouTube), mezcla de
    amplio + nicho, idioma correcto, sin genéricos solos (#fyp #viral).
  * Miniatura de 3 palabras como máximo, sin repetir el título,
    2 palabras en MAYÚSCULAS como máximo.
  * Coherencia: la promesa del título se cumple en el clip. Decir
    "gameplay GTA 6" cuando no es GTA 6 verificado = 0 en Coherencia
    y -20% en Título.

El total es entero SIN redondeo hacia arriba (89,6 es 89, no 90).
Estados: ideal 95-100 / publicable 90-94 / override 70-89 /
bloqueado 0-69.

``can_publish`` dice si se puede publicar tal cual; un 70-89 solo
se publica con un override registrado (``register_override``), que
guarda {platform, score, motivo, timestamp} en el manifiesto SIN
cambiar el score. Un 0-69 es bloqueo duro, sin override.

"Rellenar con IA" (``retry_best``): hasta 3 reintentos cambiando
solo el bloque que más puntos pierde, quedándose con la mejor
versión. Esta función solo re-puntúa y elige: la generación de
texto en sí queda fuera (el llamador pasa ``make_variant``, que
recibe el paquete y el bloque peor y devuelve una variante).

Uso:
    from .seo_score import score_all_platforms, can_publish
    res = score_all_platforms({
        "title": "GTA 6: la jugada que rompió el chat",
        "description": "GTA 6: el chat explotó en segundos... ¿lo viste?",
        "tags": ["gta 6", "gta 6 clips", ...],
        "thumbnail_text": "NO PUEDE SER",
        "hashtags": ["#GTA6", "#GTA6Clips", ...],
        "game_verified": True, "visible": "gameplay", "lang": "es"})
    res["youtube_shorts"]["score"]  # entero, sin redondear hacia arriba

Sin dependencias externas, sin credenciales, sin red. Todo local.
"""

import math
import re
from datetime import datetime, timezone

PLATFORMS = ("youtube_shorts", "tiktok", "instagram_reels", "facebook_reels")

WEIGHTS = {
    "youtube_shorts": {"title": 0.35, "description": 0.20, "tags": 0.10,
                       "thumbnail": 0.20, "coherence": 0.15},
    "tiktok": {"title": 0.25, "description": 0.30, "tags": 0.20,
               "thumbnail": 0.10, "coherence": 0.15},
    "instagram_reels": {"title": 0.25, "description": 0.30, "tags": 0.20,
                        "thumbnail": 0.15, "coherence": 0.10},
    "facebook_reels": {"title": 0.25, "description": 0.30, "tags": 0.15,
                       "thumbnail": 0.20, "coherence": 0.10},
}

# Hashtags genéricos: solos no dicen nada (ni juego, ni canal, ni nicho).
GENERIC_HASHTAGS = {"#fyp", "#viral", "#foryou", "#parati", "#para-ti",
                    "#explore", "#explora", "#trending", "#tendencia"}
# Hashtags amplios (juego / género): valen como "amplio" en la mezcla.
BROAD_HASHTAGS = {"#gta6", "#gta", "#rockstargames", "#gaming",
                  "#videojuegos", "#games", "#clips"}
CTA_WORDS = ("suscríbete", "suscribete", "comenta", "sígueme", "sigueme",
             "seguime", "comparte", "no te lo pierdas", "qué te pareció",
             "que te pareció", "¿qué", "mira el final", "mira hasta el final")

MAX_RETRIES = 3


def status_of(score: int) -> str:
    """Estado de publicación de un total entero 0-100."""
    if score >= 95:
        return "ideal"
    if score >= 90:
        return "publicable"
    if score >= 70:
        return "override"
    return "bloqueado"


def _floor_total(value: float) -> int:
    """Entero sin redondeo hacia arriba: 89,6 es 89, no 90.

    El redondeo a 9 decimales quita el polvo de coma flotante de
    la renormalización de pesos (99,9999999999 -> 100, no 99):
    94,99 sigue siendo 94.
    """
    return int(math.floor(round(value, 9)))


def _words(text):
    return re.findall(r"[a-záéíóúñü0-9]+", (text or "").lower())


def _caps_words(text):
    """Palabras en MAYÚSCULAS (de más de 1 letra) del texto."""
    out = []
    for w in re.findall(r"[A-Za-zÁÉÍÓÚÑÜ]+", text or ""):
        if len(w) > 1 and w.isupper():
            out.append(w)
    return out


def _keyword_count(text, keyword):
    return (text or "").lower().count((keyword or "").lower()) if keyword else 0


# ---------------------------------------------------------------------------
# Componentes (cada uno 0-100)
# ---------------------------------------------------------------------------

def title_component(title, keyword="GTA 6", game_verified=True, visible=None):
    """Título 0-100. Devuelve (valor, notas).

    Keyword en los primeros 40 car.: 40 (más tarde: 20, ausente: 0).
    Largo 40-55: 30; 35-39 o 56-60: 22; 30-34: 15; <30: 8; >60: 5.
    <=2 palabras en MAYÚSCULAS: 15, si no 5.
    Sustancia (>= 4 palabras): 15, si no 5.
    Promesa de gameplay sin verificar: -20% sobre el total.
    """
    title = title or ""
    notes = []
    value = 0
    head = title[:40]
    if keyword and keyword.lower() in head.lower():
        value += 40
        notes.append(f"keyword '{keyword}' en los primeros 40 caracteres (+40)")
    elif keyword and keyword.lower() in title.lower():
        value += 20
        notes.append(f"keyword '{keyword}' fuera de los primeros 40 caracteres (+20)")
    else:
        notes.append(f"sin keyword '{keyword}' en el título (+0)")
    n = len(title)
    if 40 <= n <= 55:
        value += 30
        notes.append(f"largo ideal {n} car. (40-55) (+30)")
    elif 35 <= n <= 60:
        value += 22
        notes.append(f"largo aceptable {n} car. (35-60) (+22)")
    elif 30 <= n < 35:
        value += 15
        notes.append(f"largo corto {n} car. (+15)")
    elif n < 30:
        value += 8
        notes.append(f"largo muy corto {n} car. (+8)")
    else:
        value += 5
        notes.append(f"largo {n} car. > 60, penaliza (+5)")
    caps = _caps_words(title)
    if len(caps) <= 2:
        value += 15
        notes.append(f"{len(caps)} palabras en MAYÚSCULAS (<=2) (+15)")
    else:
        value += 5
        notes.append(f"{len(caps)} palabras en MAYÚSCULAS (>2) (+5)")
    if len(_words(title)) >= 4:
        value += 15
        notes.append("título con sustancia (>= 4 palabras) (+15)")
    else:
        value += 5
        notes.append("título muy corto en palabras (+5)")
    # Promesa de gameplay GTA 6 sin verificar: -20% en Título.
    if "gameplay" in title.lower() and keyword and keyword.lower() in title.lower():
        if not game_verified or visible == "cams-only":
            value = value * 0.8
            notes.append("promesa de 'gameplay GTA 6' sin verificar: -20% en Título")
    return round(value, 1), notes


def description_component(description, keyword="GTA 6"):
    """Descripción 0-100. Devuelve (valor, notas).

    Hook + keyword en los primeros 120 car.: 40 (más tarde: 20,
    ausente: 0). CTA/pregunta al final: 30 (si no: 10).
    Largo >= 120 car.: 15; 60-119: 8; <60: 3. Sin stuffing (<=3
    repeticiones de la keyword): 15; más de 3: 0 y -30% del componente.
    """
    description = description or ""
    notes = []
    value = 0
    head = description[:120]
    if keyword and keyword.lower() in head.lower():
        value += 40
        notes.append("hook + keyword en los primeros 120 car. (+40)")
    elif keyword and keyword.lower() in description.lower():
        value += 20
        notes.append("keyword fuera de los primeros 120 car. (+20)")
    else:
        notes.append(f"sin keyword '{keyword}' en la descripción (+0)")
    tail = description[-150:].lower()
    if "?" in tail or any(w in tail for w in CTA_WORDS):
        value += 30
        notes.append("CTA/pregunta al final (+30)")
    else:
        value += 10
        notes.append("sin CTA ni pregunta al final (+10)")
    n = len(description)
    if n >= 120:
        value += 15
        notes.append(f"descripción con cuerpo ({n} car.) (+15)")
    elif n >= 60:
        value += 8
        notes.append(f"descripción corta ({n} car.) (+8)")
    else:
        value += 3
        notes.append(f"descripción muy corta ({n} car.) (+3)")
    reps = _keyword_count(description, keyword)
    if reps <= 3:
        value += 15
        notes.append(f"keyword x{reps} (<=3, sin stuffing) (+15)")
    else:
        value = value * 0.7
        notes.append(f"keyword stuffing: '{keyword}' x{reps} (>3): -30% del componente")
    return round(value, 1), notes


def tags_component(tags, hashtags, platform, lang="es", keyword="GTA 6"):
    """Tags/hashtags 0-100. Devuelve (valor, notas).

    YouTube usa tags (5-15: 40); el resto usa hashtags del caption
    (3-5: 40). Mezcla amplio+nicho: 30 (solo un tipo: 15).
    Relevancia/idioma (al menos un tag del juego o del canal): 30.
    Genéricos solos (#fyp #viral...): tope 20 en el componente.
    """
    tags = list(tags or [])
    hashtags = list(hashtags or [])
    notes = []
    value = 0
    if platform == "youtube_shorts":
        n = len(tags)
        if 5 <= n <= 15:
            value += 40
            notes.append(f"{n} tags (5-15) (+40)")
        elif 3 <= n < 5:
            value += 25
            notes.append(f"{n} tags (+25)")
        elif 1 <= n < 3:
            value += 10
            notes.append(f"{n} tags (+10)")
        else:
            notes.append("sin tags (+0)")
        pool = [t.lower() for t in tags] + [h.lower() for h in hashtags]
    else:
        n = len(hashtags)
        if 3 <= n <= 5:
            value += 40
            notes.append(f"{n} hashtags en caption (3-5) (+40)")
        elif 1 <= n < 3:
            value += 20
            notes.append(f"{n} hashtags en caption (+20)")
        elif n > 5:
            value += 25
            notes.append(f"{n} hashtags en caption (>5, se recorta) (+25)")
        else:
            notes.append("sin hashtags en caption (+0)")
        pool = [h.lower() for h in hashtags]
    broad = any(h in BROAD_HASHTAGS for h in pool)
    niche = any(h not in BROAD_HASHTAGS and h not in GENERIC_HASHTAGS
                for h in pool)
    if broad and niche:
        value += 30
        notes.append("mezcla amplio + nicho (+30)")
    elif broad or niche:
        value += 15
        notes.append("solo amplio o solo nicho (+15)")
    else:
        notes.append("sin mezcla amplio/nicho (+0)")
    kw = (keyword or "").lower().replace(" ", "")
    relevant = any(kw in p.replace(" ", "") or "elclipdegta6" in p
                   for p in pool)
    if relevant:
        value += 30
        notes.append("tag del juego/canal presente (+30)")
    else:
        value += 10
        notes.append("sin tag del juego/canal (+10)")
    if hashtags and all(h.lower() in GENERIC_HASHTAGS for h in hashtags):
        value = min(value, 20)
        notes.append("solo hashtags genéricos (#fyp #viral...): tope 20")
    return round(value, 1), notes


def thumbnail_component(thumbnail_text, title):
    """Miniatura 0-100, o None si la plataforma no tiene miniatura
    propia (TikTok/Facebook: manda el primer frame). Devuelve
    (valor o None, notas).

    <=3 palabras: 40; 4: 20; >4: 5. Sin repetir el título: 30
    (si repite: 0). <=2 palabras en MAYÚSCULAS: 30, si no 10.
    """
    if thumbnail_text is None:
        return None, ["sin miniatura propia en esta plataforma (manda el primer frame)"]
    thumb = (thumbnail_text or "").strip()
    notes = []
    value = 0
    words = _words(thumb)
    if not words:
        return 0.0, ["miniatura vacía"]
    if len(words) <= 3:
        value += 40
        notes.append(f"miniatura de {len(words)} palabras (<=3) (+40)")
    elif len(words) == 4:
        value += 20
        notes.append("miniatura de 4 palabras (+20)")
    else:
        value += 5
        notes.append(f"miniatura de {len(words)} palabras (>4) (+5)")
    title_words = set(_words(title))
    if title_words and not (set(words) & title_words):
        value += 30
        notes.append("no repite el título (+30)")
    else:
        notes.append("repite palabras del título (+0)")
    caps = _caps_words(thumb)
    if len(caps) <= 2:
        value += 30
        notes.append(f"{len(caps)} palabras en MAYÚSCULAS (<=2) (+30)")
    else:
        value += 10
        notes.append(f"{len(caps)} palabras en MAYÚSCULAS (>2) (+10)")
    return round(value, 1), notes


def coherence_component(title, game_verified, visible, keyword="GTA 6"):
    """Coherencia 0-100. Devuelve (valor, notas, bloqueos).

    "gameplay GTA 6" sin verificar (juego no verificado o clip solo
    cams) = 0 + bloqueo. Título que nombra un juego en un clip solo
    cams = 20 + bloqueo. Juego verificado + gameplay visible = 100.
    Juego del título sin verificar = 40 + bloqueo.
    """
    title = title or ""
    notes = []
    blocks = []
    low = title.lower()
    names_game = bool(keyword and keyword.lower() in low) or "gta" in low
    claims_gameplay = "gameplay" in low and names_game
    if claims_gameplay and (not game_verified or visible == "cams-only"):
        blocks.append("el título promete 'gameplay GTA 6' pero el juego "
                      "no está verificado o el clip es solo cams")
        notes.append("promesa de gameplay sin verificar: 0 en Coherencia")
        return 0.0, notes, blocks
    if visible == "cams-only" and names_game:
        blocks.append("el título nombra un juego pero el clip es solo "
                      "cams (sin gameplay visible)")
        notes.append("título nombra un juego en clip solo cams: 20")
        return 20.0, notes, blocks
    if game_verified and visible in ("gameplay", "cams+gameplay"):
        notes.append("juego verificado y gameplay visible: 100")
        return 100.0, notes, blocks
    if game_verified:
        notes.append("juego verificado, lo visible sin confirmar: 80")
        return 80.0, notes, blocks
    if not names_game:
        notes.append("el título no nombra ningún juego: 80")
        return 80.0, notes, blocks
    blocks.append("el título nombra un juego que no está verificado")
    notes.append("juego del título sin verificar: 40")
    return 40.0, notes, blocks


# ---------------------------------------------------------------------------
# Score por plataforma
# ---------------------------------------------------------------------------

def score_platform(platform, title=None, description=None, tags=None,
                   thumbnail_text=None, hashtags=None, game_verified=True,
                   visible=None, lang="es", keyword="GTA 6"):
    """Score SEO de un paquete en una plataforma.

    Devuelve {platform, score (entero, sin redondear hacia arriba),
    components {title, description, tags, thumbnail, coherence} con
    value (0-100 o None) y notes, blocks, status}.
    Un componente None (sin miniatura propia) se excluye y los pesos
    se renormalizan entre los demás.
    """
    if platform not in WEIGHTS:
        raise ValueError(f"Plataforma desconocida: {platform!r}")
    t_val, t_notes = title_component(title, keyword, game_verified, visible)
    d_val, d_notes = description_component(description, keyword)
    g_val, g_notes = tags_component(tags, hashtags, platform, lang, keyword)
    th_val, th_notes = thumbnail_component(thumbnail_text, title)
    c_val, c_notes, blocks = coherence_component(
        title, game_verified, visible, keyword)
    components = {
        "title": {"value": t_val, "notes": t_notes},
        "description": {"value": d_val, "notes": d_notes},
        "tags": {"value": g_val, "notes": g_notes},
        "thumbnail": {"value": th_val, "notes": th_notes},
        "coherence": {"value": c_val, "notes": c_notes},
    }
    weights = dict(WEIGHTS[platform])
    if th_val is None:
        # Sin miniatura propia: su peso se reparte entre los demás.
        weights.pop("thumbnail")
        total_w = sum(weights.values())
        weights = {k: v / total_w for k, v in weights.items()}
    total = sum(components[k]["value"] * weights[k]
                for k in weights if components[k]["value"] is not None)
    score = _floor_total(total)
    return {
        "platform": platform,
        "score": score,
        "components": components,
        "blocks": blocks,
        "status": status_of(score),
        "can_publish": status_of(score) in ("ideal", "publicable"),
    }


def score_all_platforms(pkg):
    """Score SEO en las 4 plataformas de un paquete.

    pkg: {title, description, tags, thumbnail_text, hashtags,
    game_verified, visible, lang, keyword}. Devuelve
    {platform: resultado de score_platform}.
    """
    pkg = pkg or {}
    out = {}
    for platform in PLATFORMS:
        out[platform] = score_platform(
            platform,
            title=pkg.get("title"),
            description=pkg.get("description"),
            tags=pkg.get("tags"),
            thumbnail_text=pkg.get("thumbnail_text"),
            hashtags=pkg.get("hashtags"),
            game_verified=pkg.get("game_verified", True),
            visible=pkg.get("visible"),
            lang=pkg.get("lang", "es"),
            keyword=pkg.get("keyword", "GTA 6"),
        )
    return out


def can_publish(result, overrides=None):
    """¿Se puede publicar con este resultado?

    ideal/publicable: sí. override (70-89): solo si hay un override
    registrado para esa plataforma. bloqueado (0-69): no, sin excepción.
    """
    status = result.get("status") if isinstance(result, dict) else None
    platform = result.get("platform") if isinstance(result, dict) else None
    if status in ("ideal", "publicable"):
        return True
    if status == "override":
        return bool(overrides) and platform in (overrides or {})
    return False


def register_override(manifest, platform, score, motivo):
    """Registra un override {platform, score, motivo, timestamp} en
    el manifiesto, SIN cambiar el score. Devuelve el registro."""
    record = {
        "platform": platform,
        "score": score,
        "motivo": motivo,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
    manifest.setdefault("seo_overrides", []).append(record)
    return record


def worst_component(result):
    """Nombre del componente con menos puntos (para retry_best)."""
    comps = result.get("components", {})
    scored = [(k, v["value"]) for k, v in comps.items()
              if v.get("value") is not None]
    if not scored:
        return None
    return min(scored, key=lambda kv: kv[1])[0]


def retry_best(base_pkg, make_variant, platform, max_attempts=MAX_RETRIES):
    """'Rellenar con IA': hasta ``max_attempts`` reintentos cambiando
    solo el bloque que más puntos pierde, quedándose con la mejor
    versión.

    base_pkg: dict de paquete (ver score_all_platforms).
    make_variant(pkg, worst_block) -> dict nuevo con ese bloque
        cambiado, o None para parar. La generación de texto queda
        fuera: esta función solo re-puntúa y elige.
    Devuelve {"best_pkg", "best_result", "attempts": [...],
    "improved": bool}.
    """
    best_pkg = dict(base_pkg)
    best_result = score_platform(platform, **_pkg_kwargs(best_pkg))
    attempts = [{"attempt": 0, "worst_block": None,
                 "score": best_result["score"],
                 "status": best_result["status"]}]
    for i in range(1, max_attempts + 1):
        if best_result["status"] == "ideal":
            break
        worst = worst_component(best_result)
        if worst is None:
            break
        variant = make_variant(dict(best_pkg), worst)
        if variant is None:
            break
        result = score_platform(platform, **_pkg_kwargs(variant))
        attempts.append({"attempt": i, "worst_block": worst,
                         "score": result["score"],
                         "status": result["status"]})
        if result["score"] > best_result["score"]:
            best_pkg, best_result = variant, result
    return {
        "best_pkg": best_pkg,
        "best_result": best_result,
        "attempts": attempts,
        "improved": best_result["score"] > attempts[0]["score"],
    }


def pick_best(candidates, platform):
    """De una lista de paquetes candidatos, el de mejor score en esa
    plataforma. Devuelve (pkg, resultado) o (None, None)."""
    best = None
    best_result = None
    for pkg in candidates or []:
        result = score_platform(platform, **_pkg_kwargs(pkg))
        if best_result is None or result["score"] > best_result["score"]:
            best, best_result = pkg, result
    return best, best_result


def _pkg_kwargs(pkg):
    """Claves que acepta score_platform desde un dict de paquete."""
    return {
        "title": pkg.get("title"),
        "description": pkg.get("description"),
        "tags": pkg.get("tags"),
        "thumbnail_text": pkg.get("thumbnail_text"),
        "hashtags": pkg.get("hashtags"),
        "game_verified": pkg.get("game_verified", True),
        "visible": pkg.get("visible"),
        "lang": pkg.get("lang", "es"),
        "keyword": pkg.get("keyword", "GTA 6"),
    }


if __name__ == "__main__":
    import json
    good = {
        "title": "GTA 6: la jugada que rompió el chat 😱",
        "description": ("GTA 6: el chat explotó en segundos con esta jugada. "
                        "Clip completo en vertical, sin contexto necesario. "
                        "¿Lo viste venir? Comenta qué harías tú."),
        "tags": ["gta 6", "gta 6 clips", "gta vi", "rockstar games",
                 "clips gaming", "stream clips", "gameplay", "gta 6 funny",
                 "gta 6 español", "illojuan"],
        "thumbnail_text": "NO PUEDE SER",
        "hashtags": ["#GTA6", "#GTA6Clips", "#ElClipDeGTA6", "#GTA", "#gaming"],
        "game_verified": True, "visible": "gameplay", "lang": "es",
    }
    print(json.dumps(score_all_platforms(good), ensure_ascii=False, indent=2))

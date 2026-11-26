#!/usr/bin/env python3
"""caption_lint.py - linter de captions como puerta antes de publicar.

Port de ``caption.py`` de instagram-agent-skill/skills/ig-caption
(MIT, 214 l.), adaptado a nuestra tabla por plataforma
(``clipagent.metadata.PLATFORMS``) y al español. La generación del
caption sigue siendo nuestra (por plataforma, con Gemini); esto es
solo la puerta de verificación: corre DESPUÉS de generar el paquete
y ANTES de publicar, sobre el caption ya construido de cada
plataforma (``package_for_platforms``).

Discrepancia del origen, anotada: el docstring de ``caption.py``
dice "the eight things" pero ``analyse()`` registra 9 chequeos.
Aquí se portan los 9: LENGTH, FIRST LINE, HOOK IS CONCRETE,
HASHTAGS, TAG PLACEMENT, LINKS, ONE ASK, EMOJI, SEARCH TERMS.

Parámetros por plataforma (ver PLAN-2-caption-linter.md §3 para
el origen de cada valor):

- Límite LENGTH: YT 5000 (límite duro de la descripción de
  YouTube), TikTok 150 (``caption_max``), IG 2200 (``caption_max``),
  FB 2200 (supuesto conservador explícito: mismo valor que IG).
- Ventana visible (cut): YT 60 (``title_max``: el caption abre con
  el título; el 40 de móvil no se usa: pondría REVIEW a todos los
  paquetes reales), TikTok 150 (el caption entero cabe), IG 125
  (original), FB 125 (como IG).
- Tope HASHTAGS: YT 15 (regla de YouTube para hashtags en
  descripción; ``tags_max``), TikTok/IG/FB 5.
- SEARCH TERMS: solo las formas del juego (``keywords_for_game``);
  sin juego conocido el chequeo se salta, como el original sin
  ``--keywords``.

Cambios ordenados frente al original (no silenciosos):

- Cualquier hashtag de ``FILLER_TAGS`` (los 15 genéricos del
  original) es FAIL en HASHTAGS, no WARN. Segundo candado: el
  humanizador (adopción 1) ya los borra del campo hashtags.
- TAG PLACEMENT es PASS cuando el caption entero cabe en la
  ventana (sin fold no hay nada que esconder debajo); sin esto,
  TikTok daría WARN siempre (su caption es título + hashtags).
- ONE ASK añade equivalentes ES reales a los 8 patrones EN del
  original (misma intención, uso real en español).

Veredictos por plataforma, como el original: FIX si algún FAIL,
REVIEW si algún WARN, READY si todo PASS. El veredicto global de
``lint_package`` es el peor de las 4 plataformas. El pipeline
(``package_clip``) traduce FIX a retención R3.4 y REVIEW a
«sigue con los avisos registrados»; el ``exit 1`` del CLI original
no se porta al pipeline (el CLI de aquí lo conserva, para uso
manual).

Uso
  python -m clipagent.caption_lint caption.txt
  python -m clipagent.caption_lint caption.txt --platform tiktok \
      --keywords "GTA 6,GTA VI"
  echo "..." | python -m clipagent.caption_lint -
  python -m clipagent.caption_lint caption.txt --json

  from clipagent.caption_lint import analyse_caption, lint_package
"""

import argparse
import json
import re
import sys
import textwrap

# Límite LENGTH del caption por plataforma (ver docstring).
PLATFORM_LIMITS = {
    "youtube_shorts": 5000,
    "tiktok": 150,
    "instagram_reels": 2200,
    "facebook_reels": 2200,
}

# Ventana visible: caracteres antes de "... more" (ver docstring).
PLATFORM_CUTS = {
    "youtube_shorts": 60,
    "tiktok": 150,
    "instagram_reels": 125,
    "facebook_reels": 125,
}

# Tope de hashtags por plataforma (ver docstring).
PLATFORM_HASHTAG_LIMITS = {
    "youtube_shorts": 15,
    "tiktok": 5,
    "instagram_reels": 5,
    "facebook_reels": 5,
}

DEFAULT_PLATFORM = "instagram_reels"

HOLD_REASON_CAPTION_LINT = (
    "Caption con fallos (linter): retención para revisión"
)

HASHTAG_RE = re.compile(r"(?:^|\s)(#[A-Za-z0-9_]+)")
MENTION_RE = re.compile(r"(?:^|\s)(@[A-Za-z0-9_.]+)")
LINK_RE = re.compile(r"https?://\S+|\bwww\.\S+|\b[a-z0-9-]+\.(?:com|co|io|net|org|ai|app)/\S*",
                     re.IGNORECASE)
EMOJI_RE = re.compile(r"[\U0001F300-\U0001FAFF☀-➿←-⇿️]")
# Como el original, pero con mayúscula acentuada inicial y
# minúsculas acentuadas: el original [A-Z][a-z] no caza nombres
# en español («Rubius» con tilde, «Álex»).
CONCRETE_RE = re.compile(r"\$\s?\d|\b\d[\d,.]*\b|(?<!^)\b[A-ZÁÉÍÓÚÑ][a-záéíóúñ]{2,}\b",
                         re.MULTILINE)

# Los 8 patrones EN del original, verbatim, + equivalentes ES
# reales (misma intención). Cada entrada: (regex, nombre del ask).
ASKS = [
    (re.compile(r"(?i)\bcomment (?:the word |\")?[A-Z0-9]{2,}\b"), "comment a keyword"),
    (re.compile(r"(?i)\b(?:dm|message) me\b"), "DM me"),
    (re.compile(r"(?i)\bsave (?:this|it)\b"), "save this"),
    (re.compile(r"(?i)\bshare (?:this|it)\b"), "share this"),
    (re.compile(r"(?i)\bfollow (?:me|for)\b"), "follow"),
    (re.compile(r"(?i)\blink in (?:my )?bio\b"), "link in bio"),
    (re.compile(r"(?i)\b(?:swipe|tap) (?:through|left|right|for|to)\b"), "swipe or tap"),
    (re.compile(r"(?i)\btell me\b|\bwhat would you\b|\bwhat do you think\b|\bwhich one\b"),
     "answer a question"),
    # Equivalentes ES (misma intención, uso real en español).
    (re.compile(r"(?i)¿\s*qué (crees|opinas|te parece|dirías|harías)\b"), "responder una pregunta"),
    (re.compile(r"(?i)¿\s*cuál\b"), "responder una pregunta"),
    (re.compile(r"(?i)\bcuéntame\b|\bcuentame\b|\bcomenta\b"), "comentar"),
    (re.compile(r"(?i)\bguarda (?:este|esto|el)\b"), "guardar"),
    (re.compile(r"(?i)\bcomparte (?:este|esto|el)\b"), "compartir"),
    (re.compile(r"(?i)\bsígueme\b|\bseguime\b|\bsigueme\b"), "seguir"),
    (re.compile(r"(?i)\blink en (?:la )?bio\b|\benlace en (?:la )?bio\b"), "link en bio"),
    (re.compile(r"(?i)\b(?:mándame|mandame|escríbeme|escribeme)(?: un)? (?:dm|mensaje)\b"),
     "DM"),
    (re.compile(r"(?i)\bdesliza\b"), "deslizar"),
]

# Hashtags genéricos prohibidos: FILLER_TAGS verbatim de
# ig-caption/caption.py. Un paquete con uno de estos no pasa la
# puerta (FAIL en HASHTAGS). El humanizador (adopción 1) ya los
# borra del campo hashtags; esto es el segundo candado.
FILLER_TAGS = frozenset({
    "#viral", "#fyp", "#explore", "#explorepage", "#foryou",
    "#foryoupage", "#trending", "#instagood", "#love", "#follow",
    "#like4like", "#reels", "#reelsinstagram", "#viralreels",
    "#instadaily",
})

# Orden de plataformas para el veredicto global y el log.
PLATFORM_ORDER = ("youtube_shorts", "tiktok", "instagram_reels",
                  "facebook_reels")


def keywords_for_game(game):
    """Términos de búsqueda del juego: canónico + alias.

    «GTA 6» y «GTA VI» son el mismo juego (``GAME_ALIASES`` en
    metadata, como en ``caption_first_line``). Sin juego o con
    juego «desconocido» devuelve [] y SEARCH TERMS se salta.
    La importación es perezosa para que este módulo no dependa
    de metadata al importarse (el CLI y los tests lo usan solo).
    """
    game = (game or "").strip()
    if not game or game.lower() == "desconocido":
        return []
    try:
        from .metadata import GAME_ALIASES
    except Exception:
        return [game]
    for canonical, aliases in GAME_ALIASES.items():
        alias_lows = {a.lower() for a in aliases}
        if game.lower() == canonical.lower() or game.lower() in alias_lows:
            # Un solo grupo: las formas son ALTERNATIVAS del mismo
            # término (basta con que aparezca una: «GTA 6» o
            # «GTA VI»). Si se devolvieran como keywords distintas,
            # SEARCH TERMS exigiría las dos a la vez.
            return [[canonical] + sorted(set(aliases))]
    return [[game]]


def visible_window(text, cut):
    """Lo que muestra el feed. El original corta a mitad de palabra."""
    flat = text.strip()
    return flat if len(flat) <= cut else flat[:cut]


def render_box(window, truncated, out=sys.stdout, width=52):
    print("\n  WHAT THE FEED SHOWS", file=out)
    print("  +" + "-" * (width + 2) + "+", file=out)
    lines = []
    for raw in window.split("\n"):
        lines.extend(textwrap.wrap(raw, width) or [""])
    for line in lines[:8]:
        print(f"  | {line:<{width}} |", file=out)
    tail = "... more" if truncated else "(whole caption fits)"
    print("  +" + "-" * (width + 2 - len(tail) - 2) + f" {tail} " + "+", file=out)


def analyse_caption(text, platform=DEFAULT_PLATFORM, keywords=None):
    """Linta un caption para una plataforma. Misma forma de salida
    que ``caption.py:analyse`` (9 chequeos; el docstring original
    dice «eight» pero registra 9).

    ``keywords``: términos de búsqueda que deben aparecer (si es
    None o vacío, SEARCH TERMS se salta, como el original sin
    ``--keywords``).
    """
    limit = PLATFORM_LIMITS.get(platform, PLATFORM_LIMITS[DEFAULT_PLATFORM])
    cut = PLATFORM_CUTS.get(platform, PLATFORM_CUTS[DEFAULT_PLATFORM])
    hashtag_limit = PLATFORM_HASHTAG_LIMITS.get(
        platform, PLATFORM_HASHTAG_LIMITS[DEFAULT_PLATFORM])

    text = (text or "").rstrip()
    stripped = text.strip()
    chars = len(stripped)
    lines = [line for line in stripped.split("\n")]
    first_line = lines[0].strip() if lines else ""
    tags = HASHTAG_RE.findall(stripped)
    mentions = MENTION_RE.findall(stripped)
    links = LINK_RE.findall(stripped)
    emoji = EMOJI_RE.findall(stripped)
    window = visible_window(stripped, cut)
    truncated = chars > cut
    asks = [name for pattern, name in ASKS if pattern.search(stripped)]
    filler = [t for t in tags if t.lower() in FILLER_TAGS]
    # Keywords normalizadas a grupos de alternativas: cada entrada
    # es un string (un término) o una lista (formas alternativas
    # del mismo término: basta una). Un string vacío no es keyword.
    keywords = [[k] if isinstance(k, str) else list(k)
                for k in (keywords or [])]
    keywords = [[str(a).strip() for a in group if str(a).strip()]
                for group in keywords]
    keywords = [group for group in keywords if group]

    checks = []

    def add(name, status, detail):
        checks.append({"check": name, "status": status, "detail": detail})

    add("LENGTH", "FAIL" if chars > limit else "PASS",
        f"{chars} / {limit} characters" + (f", {chars - limit} over the limit"
                                           if chars > limit else ""))

    if not first_line:
        add("FIRST LINE", "FAIL", "the caption opens on a blank line")
    elif first_line.startswith("#") or first_line.startswith("@"):
        add("FIRST LINE", "FAIL",
            "opens on a hashtag or a mention, which is the one position worth a sentence")
    elif len(first_line) > cut:
        add("FIRST LINE", "WARN",
            f"{len(first_line)} characters, so it gets cut at {cut} mid-thought. "
            "Fine if the cut is a cliffhanger, bad if it is a subordinate clause")
    else:
        add("FIRST LINE", "PASS", f"{len(first_line)} characters, lands whole")

    add("HOOK IS CONCRETE", "PASS" if CONCRETE_RE.search(window) else "WARN",
        f"{len(CONCRETE_RE.findall(window))} number(s) or name(s) in the visible window"
        + ("" if CONCRETE_RE.search(window) else " - nothing checkable before the tap"))

    if len(tags) > hashtag_limit or filler:
        detail = ""
        if len(tags) > hashtag_limit:
            detail += (f"{len(tags)} tags, over the cap of {hashtag_limit}. "
                       "Tags past the cap do not count and the block reads as old")
        if filler:
            if detail:
                detail += ". "
            detail += (f"{len(filler)} generic tag(s) "
                       f"({', '.join(sorted(set(filler)))}): banned filler tags")
        add("HASHTAGS", "FAIL", detail)
    else:
        add("HASHTAGS", "PASS", f"{len(tags)} tag(s)" + (f": {' '.join(tags)}" if tags else ""))

    if not tags:
        add("TAG PLACEMENT", "PASS", "no tags to place")
    elif chars <= cut:
        # Sin fold: el caption entero cabe en la ventana visible,
        # así que no hay «debajo del fold» donde esconder los tags.
        # Sin esto, TikTok (caption = título + hashtags) daría
        # WARN siempre.
        add("TAG PLACEMENT", "PASS", "no fold: the whole caption fits in the window")
    elif any(re.search(r"(?:^|\s)" + re.escape(t) + r"\b", window) for t in tags):
        add("TAG PLACEMENT", "WARN", "a hashtag is inside the visible window, "
                                     "spending feed space on a label")
    else:
        add("TAG PLACEMENT", "PASS", "tags are below the fold")

    add("LINKS", "WARN" if links else "PASS",
        f"{len(links)} link(s) in the caption, and captions are not clickable. "
        f"Move it to the bio or the DM" if links else "no dead links in the body")

    if len(asks) == 1:
        add("ONE ASK", "PASS", f"one call to action: {asks[0]}")
    elif not asks:
        add("ONE ASK", "WARN", "no call to action. Decide what this post is for")
    else:
        add("ONE ASK", "WARN", f"{len(asks)} asks ({', '.join(asks)}). "
                               "Two asks is the same as none")

    density = len(emoji) * 100 / max(chars, 1)
    add("EMOJI", "WARN" if density > 4 else "PASS",
        f"{len(emoji)} emoji, {density:.1f} per 100 characters"
        + (" - reads as decoration" if density > 4 else ""))

    if keywords:
        low = stripped.lower()
        # El término vale también en su forma compacta de hashtag:
        # «GTA 6» == «#GTA6». Sin esto, un clip solo-cams (cuyo
        # título y descripción NO pueden nombrar el juego, arreglo
        # 3: no se ve gameplay) daría FAIL aunque el juego esté en
        # los hashtags, que es donde vive en ese caso.
        low_compact = "".join(low.split())
        window_low = window.lower()
        window_compact = "".join(window_low.split())

        def _present(group, text, text_compact):
            return any(
                a.lower() in text
                or a.lower().replace(" ", "") in text_compact
                for a in group)

        # Cada grupo es un término; basta con que aparezca UNA de
        # sus formas alternativas («GTA 6» o «GTA VI»).
        groups = keywords
        found = [g for g in groups if _present(g, low, low_compact)]
        missing = [g for g in groups if g not in found]
        in_window = [g for g in found
                     if _present(g, window_low, window_compact)]
        status = "PASS" if not missing else ("WARN" if found else "FAIL")
        add("SEARCH TERMS", status,
            f"{len(found)}/{len(groups)} present"
            + (f", {len(in_window)} in the visible window" if found else "")
            + (f". Missing: {', '.join('/'.join(g) for g in missing)}"
               if missing else ""))

    fails = sum(1 for c in checks if c["status"] == "FAIL")
    warns = sum(1 for c in checks if c["status"] == "WARN")
    verdict = "FIX" if fails else ("REVIEW" if warns else "READY")

    return {
        "platform": platform,
        "characters": chars, "limit": limit, "truncate_at": cut,
        "visible": window, "truncated": truncated,
        "first_line_chars": len(first_line),
        "hashtags": tags, "mentions": mentions, "links": links,
        "emoji": len(emoji), "asks": asks,
        "checks": checks, "verdict": verdict,
    }


def lint_package(platforms, keywords=None):
    """Linta el caption de cada plataforma de un paquete.

    ``platforms`` es el dict de ``package_for_platforms`` (cada
    entrada con su ``caption``). Devuelve el veredicto global
    (el peor de las plataformas) + los FAIL/WARN para el log.
    """
    platforms = platforms or {}
    results = {}
    for key in PLATFORM_ORDER:
        entry = platforms.get(key)
        if not entry:
            continue
        results[key] = analyse_caption(
            entry.get("caption") or "", platform=key, keywords=keywords)
    fails = [f"{key}: {c['check']} ({c['detail']})"
             for key, res in results.items()
             for c in res["checks"] if c["status"] == "FAIL"]
    warns = [f"{key}: {c['check']} ({c['detail']})"
             for key, res in results.items()
             for c in res["checks"] if c["status"] == "WARN"]
    if fails:
        verdict = "FIX"
    elif warns:
        verdict = "REVIEW"
    else:
        verdict = "READY"
    return {"platforms": results, "verdict": verdict,
            "fails": fails, "warns": warns}


def render(a, out=sys.stdout):
    head = (f"CAPTION LINT  ·  {a['characters']} / {a['limit']} chars  ·  "
            f"{len(a['hashtags'])} hashtags  ·  {len(a['asks'])} ask(s)")
    print("\n" + head, file=out)
    print("=" * max(len(head), 62), file=out)
    render_box(a["visible"], a["truncated"], out=out)
    print("", file=out)
    for c in a["checks"]:
        print(f"  {c['status']:<5} {c['check']:<17} {c['detail']}", file=out)
    print("-" * max(len(head), 62), file=out)
    print(f"  VERDICT  {a['verdict']}\n", file=out)


def main(argv=None):
    ap = argparse.ArgumentParser(description="Lint a caption (multi-platform).")
    ap.add_argument("input", nargs="?", default="-",
                    help="caption file, or - for stdin")
    ap.add_argument("--platform", default=DEFAULT_PLATFORM,
                    choices=sorted(PLATFORM_LIMITS),
                    help=f"platform to lint for (default {DEFAULT_PLATFORM})")
    ap.add_argument("--truncate", type=int, default=None,
                    help="override the visible-window cut for this run")
    ap.add_argument("--keywords", default="",
                    help="comma-separated terms you want to be found for")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)

    raw = sys.stdin.read() if args.input == "-" else open(args.input, encoding="utf-8").read()
    if args.truncate is not None:
        PLATFORM_CUTS[args.platform] = args.truncate
    a = analyse_caption(raw, platform=args.platform,
                        keywords=args.keywords.split(","))
    if args.json:
        print(json.dumps(a, indent=2, ensure_ascii=False))
    else:
        render(a)
    sys.exit(0 if a["verdict"] == "READY" else 1)


if __name__ == "__main__":
    main()

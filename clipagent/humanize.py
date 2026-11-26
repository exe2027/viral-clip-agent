#!/usr/bin/env python3
"""humanize.py — quita la marca de máquina de un texto generado.

Port de ``humanize.py`` + ``detect.py`` de instagram-agent-skill
(Jakeschincariol, MIT), adaptado a NUESTROS textos en español (títulos,
descripciones y hashtags del paquete de publicación). Los dos archivos
originales viven en un solo módulo porque ``clipagent/detect.py`` YA
EXISTE (detecta momentos de chat/audio, no texto generado) y no se toca.

Tres pases, en este orden (igual que el original):

  1. INVISIBLE   borra o normaliza los caracteres que un teclado humano
                 nunca produce: zero-width, BOM, etc.
  2. TYPOGRAPHIC em dash -> coma, comillas curvas -> rectas,
                 elipsis -> tres puntos.
  3. LEXICAL     sustituye el lexicón de muletillas (``slop.json``) por
                 palabras llanas, con su capitalización y sin tocar URLs.

Las estructuras (tríadas, "no es solo X, es Y", muros de hashtags) se
REPORTAN, nunca se reescriben: reescribir la forma de una frase exige
criterio, no un regex.

El lexicón (``clipagent/slop.json``) trae el original inglés verbatim
más secciones ES (``words_es`` / ``phrases_es`` / ``structures_es``),
cada entrada ES con su ``source`` trazable (ver
``runs/adopciones-2026-11-26/PLAN-1-humanizador.md`` §3). Las entradas
con ``detect_only: true`` (clichés R2.1 de la spec de títulos) las
cuenta ``detect`` pero ``humanize`` NO las reescribe: reescribirlas
tras ``validate_title_final`` lavaría un título prohibido.

``detect`` es un panel de 5 checks locales (BURSTINESS, SPECIFICITY,
SLOP DENSITY, FINGERPRINT, VOICE). Cada check devuelve un puntaje
HUMANO de 0 a 100 (más alto = más humano). Veredicto, sin tocar la
fórmula original: PASS si media >= 70 y ningún check < 55; REVIEW si
media >= 50; si no, FLAGGED. El overall es media*0,6 + mínimo*0,4.

USO HONESTO (igual que el original): esto NO es GPTZero ni ningún
detector comercial. No llama a sus APIs ni promete sus veredictos.
Caza las señales en las que todos se fijan, y por eso arreglarlas
suele mover también sus números; la única afirmación honesta es la
de este párrafo. Los números del README original (AUC, corpus) viven
solo en el README, sin script ni corpus que los respalden; aquí no
se citan como medidos.

En el pipeline, ``detect`` corre sobre cada texto generado del
paquete (título, descripción, hashtags) DESPUÉS de ``humanize`` y
después de la validación R1–R4 de títulos; FLAGGED en cualquier campo
retiene el clip (``needs_human_review``, igual que un título sin
verdad). REVIEW no bloquea: queda registrado en el paquete.

Adaptaciones ES respecto al original (algoritmos fieles, resto
adaptado): ``WORD_RE``/``PRONOUNS``/``PROPER`` admiten tildes y ñ
(igual que ``clipagent/hookscore.py``); ``CONTRACTIONS`` queda EN
(en español no hay contracciones de voz equivalentes); las entradas
que empiezan por ``#`` no usan ``\\b`` inicial (entre espacio y `#`
no hay frontera de palabra).

Cálculo local, sin red, sin secretos. El lexicón se carga de junto
al módulo; si falta, error ruidoso (nunca un default silencioso).

Uso:
  python -m clipagent.humanize draft.txt
  python -m clipagent.humanize draft.txt --report
  python -m clipagent.humanize draft.txt --json
  echo "texto..." | python -m clipagent.humanize - --check --json
  from .humanize import humanize, run, detect_package_texts
"""

import argparse
import json
import os
import re
import statistics
import sys
import unicodedata

HERE = os.path.dirname(os.path.abspath(__file__))
LEX = os.path.join(HERE, "slop.json")

# Umbrales del veredicto (original, sin tocar).
PASS_OVERALL = 70.0
PASS_MIN_CHECK = 55.0
REVIEW_OVERALL = 50.0

# FLAGGED en cualquier campo del paquete = retención (igual que un
# título sin verdad, spec títulos-verdad R3.4).
HOLD_REASON_HUMANIZE = "Texto con marca de IA: retención para revisión"

URL_RE = re.compile(r"https?://\S+|www\.\S+|\S+@\S+\.\S+")
SENT_RE = re.compile(r"[^.!?\n]+[.!?]*")
# WORD_RE original: [A-Za-z']+ — no cuenta palabras con tilde/ñ
# ("increíble" contaba como dos). Adaptado como en hookscore.py.
WORD_RE = re.compile(r"[A-Za-zÁÉÍÓÚÜÑáéíóúüñ']+")
CONTRACTIONS = re.compile(r"\b\w+'(?:s|t|re|ve|ll|d|m)\b", re.IGNORECASE)
# Pronombres EN (original) + ES (adaptación: la voz en español
# también se mide por la persona que habla).
PRONOUNS = re.compile(
    r"\b(i|me|my|mine|we|us|our|you|your"
    r"|yo|me|mi|mis|nosotros|nosotras|nos|nuestro|nuestra|nuestros|"
    r"nuestras|tú|te|ti|tu|tus|usted|ustedes|vos|os)\b", re.IGNORECASE)
NUMBERS = re.compile(r"\b\d[\d,.]*%?\b|\$\d")
PROPER = re.compile(r"(?<![.!?]\s)(?<!^)\b[A-ZÁÉÍÓÚÑ][a-záéíóúüñ]{2,}\b",
                    re.MULTILINE)

_LEXICON_CACHE = None


def load_lexicon(path=LEX):
    """Carga slop.json (con caché). Sin archivo: error ruidoso."""
    global _LEXICON_CACHE
    if _LEXICON_CACHE is not None and path == LEX:
        return _LEXICON_CACHE
    with open(path, encoding="utf-8") as fh:
        lex = json.load(fh)
    if path == LEX:
        _LEXICON_CACHE = lex
    return lex


def _lexical_entries(lex):
    """Todas las entradas léxicas (EN + ES + hashtags)."""
    return (lex.get("words", []) + lex.get("phrases", [])
            + lex.get("words_es", []) + lex.get("phrases_es", [])
            + lex.get("hashtags", []))


def _entry_pattern(find):
    """Regex de una entrada. Las que empiezan por `#` no llevan
    ``\\b`` inicial: entre espacio y `#` no hay frontera de palabra
    y ``\\b#viral\\b`` no casaría nunca."""
    body = re.escape(find).replace(r"\ ", r"\s+")
    if find.startswith("#"):
        return re.compile(body + r"\b", re.IGNORECASE)
    return re.compile(r"\b" + body + r"\b", re.IGNORECASE)


def _cp(spec):
    """'U+200B' -> '\\u200b';  'U+E0000-U+E007F' -> (start, end)."""
    if "-" in spec:
        a, b = spec.split("-")
        return (int(a[2:], 16), int(b[2:], 16))
    return int(spec[2:], 16)


def protect_urls(text):
    """Cambia URLs por placeholders para no reescribir dentro de un
    enlace."""
    found = []

    def stash(m):
        found.append(m.group(0))
        return f"\x00URL{len(found) - 1}\x00"

    return URL_RE.sub(stash, text), found


def restore_urls(text, found):
    for i, url in enumerate(found):
        text = text.replace(f"\x00URL{i}\x00", url)
    return text


def pass_invisible(text, lex):
    """Borra o normaliza espacios invisibles. Devuelve (texto, hits)."""
    hits = []
    for entry in lex["invisible"]:
        cp = _cp(entry["cp"])
        if isinstance(cp, tuple):
            pattern = ("[" + re.escape(chr(cp[0])) + "-"
                       + re.escape(chr(cp[1])) + "]")
        else:
            pattern = re.escape(chr(cp))
        n = len(re.findall(pattern, text))
        if n:
            hits.append({"name": entry["cp"] + " " + entry["name"],
                         "count": n, "action": entry["action"]})
            text = re.sub(pattern,
                          "" if entry["action"] == "delete" else " ", text)
    # Cualquier Cf restante es invisible por definición.
    stray = [c for c in text if unicodedata.category(c) == "Cf"]
    if stray:
        hits.append({"name": "other invisible format chars",
                     "count": len(stray), "action": "delete"})
        text = "".join(c for c in text if unicodedata.category(c) != "Cf")
    return text, hits


def pass_typographic(text, lex):
    """Normaliza tipografía de máquina. Devuelve (texto, hits)."""
    hits = []
    for entry in lex["typographic"]:
        ch = entry["from"]
        n = text.count(ch)
        if not n:
            continue
        hits.append({"name": f"{ch} {entry['name']}", "count": n,
                     "to": entry["to"].strip() or "(space)"})
        if ch == "—":
            # " word — word " y "word—word" colapsan a coma + espacio.
            text = re.sub(r"\s*—\s*", ", ", text)
        elif ch == "–":
            text = re.sub(r"\s*–\s*(?=\d)", "-", text)  # 5–10 -> 5-10
            text = re.sub(r"\s+–\s+", ", ", text)  # usada como em dash
            text = text.replace("–", "-")
        else:
            text = text.replace(ch, entry["to"])
    # Una coma insertada antes de puntuación existente queda mal.
    text = re.sub(r",\s*([,.;:!?])", r"\1", text)
    text = re.sub(r",\s*\n", "\n", text)
    return text, hits


def _match_case(src, repl):
    if not repl:
        return repl
    if src.isupper() and len(src) > 1:
        return repl.upper()
    if src[0].isupper():
        return repl[0].upper() + repl[1:]
    return repl


def pass_lexical(text, lex):
    """Sustituye muletillas por palabras llanas. La más larga primero
    para que ganen las frases. Las entradas `detect_only` NO se tocan
    (las cuenta detect; reescribirlas lavaría un texto prohibido).
    Devuelve (texto, hits)."""
    hits = []
    entries = sorted(
        [e for e in _lexical_entries(lex) if not e.get("detect_only")],
        key=lambda e: len(e["find"]), reverse=True)
    for entry in entries:
        find = entry["find"]
        pattern = _entry_pattern(find)
        found = pattern.findall(text)
        if not found:
            continue
        hits.append({"find": find,
                     "replace": entry["replace"] or "(deleted)",
                     "count": len(found), "family": entry["family"]})
        text = pattern.sub(
            lambda m: _match_case(m.group(0), entry["replace"]), text)
    # Limpieza tras los borrados. Borrar una cláusula deja puntuación
    # huérfana ("system. .") o una línea que abre con coma, y eso se
    # lee peor que la muletilla.
    text = re.sub(r"[ \t]{2,}", " ", text)
    text = re.sub(r"(?m)^[ \t]*(?:[,.;:]+[ \t]*)+", "", text)
    text = re.sub(r"(?m)^[ \t](?=\S)", "", text)
    text = re.sub(r"\s+([,.;:!?])", r"\1", text)
    text = re.sub(r",\s*([,.;:!?])", r"\1", text)
    text = text.replace("...", "\x00ELL\x00")  # protege elipsis reales
    text = re.sub(r"\.\s*\.+", ".", text)
    text = re.sub(r"([!?])\s*\.", r"\1", text)
    text = text.replace("\x00ELL\x00", "...")
    text = re.sub(r"(?m)^[ \t]+$", "", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    # Un em dash vuelto coma, seguido de conector, deja una
    # yuxtaposición ("is important, also, it's proof"). Pasa a punto.
    text = re.sub(r",\s*(also|so|still|basically|in the end)\s*,\s*",
                  lambda m: ". " + m.group(1)[0].upper()
                  + m.group(1)[1:] + ", ", text)
    return text, hits


def scan_structures(text, lex):
    """Estructuras que se reportan para reescritura manual (nunca se
    auto-arreglan)."""
    flags = []
    for s in lex.get("structures", []) + lex.get("structures_es", []):
        try:
            pattern = re.compile(s["regex"], re.MULTILINE)
        except re.error:
            continue
        found = pattern.findall(text)
        if found:
            flags.append({"name": s["name"], "count": len(found),
                          "fix": s["fix"]})
    # La uniformidad de longitud de frase también es estructural.
    lens = [len(s.split()) for s in SENT_RE.findall(text)
            if len(s.split()) > 2]
    if len(lens) >= 4:
        mean = sum(lens) / len(lens)
        var = sum((n - mean) ** 2 for n in lens) / len(lens)
        cv = (var ** 0.5) / mean if mean else 0
        if cv < 0.35:
            flags.append({
                "name": f"Uniform sentence length (variation {cv:.2f})",
                "count": len(lens),
                "fix": "Break one sentence in half. Let another run "
                       "long. Machines write even.",
            })
    return flags


def restore_capitals(original, text):
    """Borrar un inicio deja la siguiente palabra en minúscula.

    Solo se arregla si el autor capitaliza sus frases: una voz
    deliberadamente en minúscula es un estilo, no un artefacto.
    """
    starts = re.findall(r"(?:^|[.!?]\s+|\n)\s*([A-Za-zÁÉÍÓÚÜÑáéíóúüñ])",
                        original)
    if not starts or sum(1 for c in starts if c.isupper()) * 2 < len(starts):
        return text
    return re.sub(
        r"(?:^|(?<=[.!?] )|(?<=[.!?]\n)|(?<=\n))\s*"
        r"([a-záéíóúüñ])",
        lambda m: m.group(0)[:-1] + m.group(1).upper(), text)


def humanize(text, lex=None):
    """Tres pases sobre el texto. Devuelve (texto_limpio, report)."""
    lex = lex or load_lexicon()
    raw_for_case = text
    text, urls = protect_urls(text)
    text, inv = pass_invisible(text, lex)
    text, typo = pass_typographic(text, lex)
    text, lexi = pass_lexical(text, lex)
    text = restore_capitals(raw_for_case, text)
    text = restore_urls(text, urls)
    return text.strip() + "\n", {
        "invisible": inv,
        "typographic": typo,
        "lexical": lexi,
        "structures": scan_structures(text, lex),
    }


def humanize_title(text, lex=None):
    """Limpieza de un título (o texto de miniatura) ANTES de quemarlo:
    solo pases invisible + tipográfico. No cambian palabras, así que
    no invalidan la validación del título ni la cita literal I3.
    Devuelve el texto limpio, sin salto final."""
    lex = lex or load_lexicon()
    text, _urls = protect_urls(text or "")
    text, _inv = pass_invisible(text, lex)
    text, _typo = pass_typographic(text, lex)
    text = restore_urls(text, _urls)
    return text.strip()


def clamp(n):
    return max(0.0, min(100.0, n))


def scale(value, human, machine):
    """Mapea value a 0-100 donde `human` -> 100 y `machine` -> 0."""
    if human == machine:
        return 50.0
    return clamp((value - machine) / (human - machine) * 100)


def sentences(text):
    return [s.strip() for s in SENT_RE.findall(text) if len(s.split()) > 2]


def words(text):
    return WORD_RE.findall(text)


def check_burstiness(text):
    """Los humanos varían mucho la longitud de frase. Los modelos
    escriben parejo.

    Adaptación: igual que SPECIFICITY y VOICE, un texto corto es
    "too short to judge". Sin ese suelo, una descripción de 4
    fragmentos cortos (típica: frase + pregunta + crédito +
    contexto, ~6 palabras cada uno) daba BURSTINESS 0 y FLAGGED
    aunque no tuviera ni una muletilla (falso positivo medido
    2026-11-26 en el paquete cams-only, 25 palabras). El suelo es
    30 palabras, no 25: por debajo, la variación de longitud
    refleja la estructura de plantilla (fragmentos), no el ritmo de
    la prosa; con 30+ palabras el check se calcula igual que el
    original."""
    lens = [len(s.split()) for s in sentences(text)]
    if len(lens) < 4 or sum(lens) < 30:
        return 50.0, "too short to judge"
    mean = statistics.mean(lens)
    cv = statistics.pstdev(lens) / mean if mean else 0
    score = scale(cv, human=0.70, machine=0.22)
    return score, (f"variation {cv:.2f} across {len(lens)} sentences "
                   f"(want 0.55+)")


def check_specificity(text):
    """Números, nombres y sustantivos concretos. El slop es abstracto."""
    w = words(text)
    if len(w) < 25:
        return 50.0, "too short to judge"
    per100 = 100 / len(w)
    hits = len(NUMBERS.findall(text)) + len(set(PROPER.findall(text)))
    density = hits * per100
    score = scale(density, human=6.0, machine=0.5)
    return score, (f"{hits} concrete markers, {density:.1f} per 100 "
                   f"words (want 4+)")


def check_slop(text, lex):
    """Densidad de vocabulario de plantilla contra el lexicón
    (incluye entradas `detect_only` y hashtags de relleno)."""
    w = words(text)
    if not w:
        return 50.0, "empty"
    hits, found = 0, []
    for entry in _lexical_entries(lex):
        n = len(_entry_pattern(entry["find"]).findall(text))
        if n:
            hits += n
            found.append(entry["find"])
    density = hits * 100 / len(w)
    score = scale(density, human=0.0, machine=4.0)
    detail = f"{hits} stock terms, {density:.1f} per 100 words"
    if found:
        detail += (" (" + ", ".join(sorted(found)[:4])
                   + (", ..." if len(found) > 4 else "") + ")")
    return score, detail


def check_fingerprint(text):
    """Caracteres que un teclado de teléfono no produce."""
    invisible = sum(1 for c in text if unicodedata.category(c) == "Cf")
    em = text.count("—")
    curly = sum(text.count(c) for c in "‘’“”")
    ellip = text.count("…")
    nbsp = sum(text.count(c) for c in "\u00a0\u202f\u2009")  # NBSP/NBSP estrecho/fino
    total = invisible * 4 + em * 2 + curly + ellip + nbsp
    per1k = total * 1000 / max(len(text), 1)
    score = scale(per1k, human=0.0, machine=12.0)
    detail = (f"{invisible} invisible, {em} em dash, {curly} curly "
              f"quote, {ellip} ellipsis, {nbsp} hard space")
    return score, detail


def check_voice(text, lex):
    """Contracciones, persona y las formas por defecto del modelo."""
    w = words(text)
    if len(w) < 25:
        return 50.0, "too short to judge"
    per100 = 100 / len(w)
    contractions = len(CONTRACTIONS.findall(text)) * per100
    person = len(PRONOUNS.findall(text)) * per100
    tells = 0
    names = []
    for s in lex.get("structures", []) + lex.get("structures_es", []):
        try:
            n = len(re.compile(s["regex"], re.MULTILINE).findall(text))
        except re.error:
            continue
        if n:
            tells += n
            names.append(s["id"])
    bullets = [len(b.split())
               for b in re.findall(r"(?m)^\s*[-*•]\s+(.+)$", text)]
    uniform = (len(bullets) >= 3 and statistics.pstdev(bullets) < 1.6)
    score = (scale(contractions, human=3.0, machine=0.0) * 0.35
             + scale(person, human=8.0, machine=1.0) * 0.35
             + clamp(100 - tells * 22) * 0.30)
    if uniform:
        score -= 12
        names.append("uniform-bullets")
    detail = (f"{contractions:.1f} contractions, {person:.1f} personal "
              f"pronouns per 100 words, {tells} structural tell(s)")
    if names:
        detail += " [" + ", ".join(names[:4]) + "]"
    return clamp(score), detail


CHECKS = ["BURSTINESS", "SPECIFICITY", "SLOP DENSITY", "FINGERPRINT",
          "VOICE"]


def run(text, lex=None):
    """Panel de 5 checks sobre un texto. Devuelve
    (results, overall, verdict)."""
    lex = lex or load_lexicon()
    results = {}
    results["BURSTINESS"] = check_burstiness(text)
    results["SPECIFICITY"] = check_specificity(text)
    results["SLOP DENSITY"] = check_slop(text, lex)
    results["FINGERPRINT"] = check_fingerprint(text)
    results["VOICE"] = check_voice(text, lex)
    scores = [results[c][0] for c in CHECKS]
    # El check más flojo arrastra el veredicto: al detector le basta
    # una señal.
    overall = statistics.mean(scores) * 0.6 + min(scores) * 0.4
    verdict = ("PASS" if overall >= PASS_OVERALL
               and min(scores) >= PASS_MIN_CHECK else (
                   "REVIEW" if overall >= REVIEW_OVERALL else "FLAGGED"))
    return results, overall, verdict


def detect_package_texts(title, description, hashtags, lex=None):
    """`detect` por campo del paquete (título, descripción, hashtags).

    Los hashtags llegan como lista o como texto; se unen con espacios.
    Devuelve {"fields": {campo: {"checks", "human_score", "verdict"}},
    "verdict": peor veredicto entre los campos, "flagged": [campos]}.
    Los textos cortos (título, hashtags) devuelven 50 en BURSTINESS /
    SPECIFICITY / VOICE ("too short to judge", como el original): en
    ellos solo SLOP DENSITY y FINGERPRINT pueden bajar de 50.
    """
    lex = lex or load_lexicon()
    if isinstance(hashtags, (list, tuple)):
        hashtags = " ".join(h or "" for h in hashtags)
    fields = {}
    for name, text in (("title", title or ""),
                       ("description", description or ""),
                       ("hashtags", hashtags or "")):
        results, overall, verdict = run(text, lex)
        fields[name] = {
            "checks": {k: {"score": round(v[0], 1), "detail": v[1]}
                       for k, v in results.items()},
            "human_score": round(overall, 1),
            "verdict": verdict,
        }
    order = {"PASS": 0, "REVIEW": 1, "FLAGGED": 2}
    worst = max(fields.values(), key=lambda f: order[f["verdict"]])
    return {"fields": fields, "verdict": worst["verdict"],
            "flagged": [n for n, f in fields.items()
                        if f["verdict"] == "FLAGGED"]}


def bar(score, width=24):
    filled = round(score / 100 * width)
    return "#" * filled + "." * (width - filled)


def render(results, overall, verdict, label=None, out=sys.stdout):
    title = "AI DETECTION PANEL" + (f"  -  {label}" if label else "")
    print("\n" + title, file=out)
    print("=" * max(len(title), 62), file=out)
    for name in CHECKS:
        score, detail = results[name]
        print(f"  {name:<13} {bar(score)} {score:5.1f}", file=out)
        print(f"  {'':<13} {detail}", file=out)
    print("-" * 62, file=out)
    print(f"  {'HUMAN SCORE':<13} {bar(overall)} {overall:5.1f}   "
          f"{verdict}", file=out)
    if verdict != "PASS":
        weakest = min(CHECKS, key=lambda c: results[c][0])
        print(f"\n  Weakest signal: {weakest}. Fix that first.", file=out)
    print("", file=out)


def render_report(report, out=sys.stderr):
    def head(title):
        print(f"\n{title}\n" + "-" * len(title), file=out)

    total = (sum(h["count"] for h in report["invisible"])
             + sum(h["count"] for h in report["typographic"])
             + sum(h["count"] for h in report["lexical"]))

    head("HUMANIZE REPORT")
    print(f"{total} machine artefacts removed, "
          f"{len(report['structures'])} structural tells flagged for "
          f"rewrite", file=out)

    if report["invisible"]:
        head("1. INVISIBLE CHARACTERS")
        for h in report["invisible"]:
            print(f"  {h['count']:>3}x  {h['name']}  -> {h['action']}",
                  file=out)
    if report["typographic"]:
        head("2. TYPOGRAPHY")
        for h in report["typographic"]:
            print(f"  {h['count']:>3}x  {h['name']}  -> {h['to']}",
                  file=out)
    if report["lexical"]:
        head("3. SLOP LEXICON")
        for h in report["lexical"]:
            print(f"  {h['count']:>3}x  {h['find']}  -> {h['replace']}   "
                  f"[{h['family']}]", file=out)
    if report["structures"]:
        head("4. STRUCTURAL TELLS  (not auto-fixed - rewrite these "
             "yourself)")
        for h in report["structures"]:
            print(f"  {h['count']:>3}x  {h['name']}\n        {h['fix']}",
                  file=out)
    if not any(report.values()):
        head("CLEAN")
        print("  Nothing to strip.", file=out)
    print("", file=out)


def main():
    ap = argparse.ArgumentParser(
        description="Strip the machine fingerprint out of a draft, "
                    "or score how machine-written it looks.")
    ap.add_argument("input", nargs="?", default="-",
                    help="file, or - for stdin")
    ap.add_argument("-o", "--out",
                    help="write cleaned text here instead of stdout")
    ap.add_argument("--report", action="store_true",
                    help="print what changed, to stderr")
    ap.add_argument("--json", action="store_true",
                    help="emit {text, report} (or detect panel) as JSON")
    ap.add_argument("--check", action="store_true",
                    help="run the detect panel instead of humanize")
    ap.add_argument("--lexicon", default=LEX)
    args = ap.parse_args()

    raw = sys.stdin.read() if args.input == "-" else open(
        args.input, encoding="utf-8").read()
    lex = load_lexicon(args.lexicon)

    if args.check:
        results, overall, verdict = run(raw, lex)
        if args.json:
            print(json.dumps({
                "checks": {k: {"score": round(v[0], 1),
                               "detail": v[1]}
                           for k, v in results.items()},
                "human_score": round(overall, 1),
                "verdict": verdict}, indent=2, ensure_ascii=False))
            return
        render(results, overall, verdict,
               label=os.path.basename(args.input)
               if args.input != "-" else None)
        sys.exit(0 if verdict == "PASS" else 1)

    clean, report = humanize(raw, lex)
    if args.json:
        print(json.dumps({"text": clean, "report": report}, indent=2,
                         ensure_ascii=False))
        return
    if args.out:
        with open(args.out, "w", encoding="utf-8") as fh:
            fh.write(clean)
        print(f"wrote {args.out}", file=sys.stderr)
    else:
        sys.stdout.write(clean)
    if args.report:
        render_report(report)


if __name__ == "__main__":
    main()

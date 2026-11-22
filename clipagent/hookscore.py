#!/usr/bin/env python3
"""hookscore.py — puntúa un título de clip en español, para MATAR débiles.

Port del hookscore.py de instagram-agent-skill (Jakeschincariol,
MIT), adaptado a NUESTROS títulos en español ("GTA 6: Rubius
descubre cómo remolcar la moto acuática"). Cinco checks locales
(SPECIFICITY, STAKES, ADDRESS, FRONTLOAD, LENGTH) + dealbreakers,
sobre el texto solo, sin red ni modelos.

USO HONESTO (medición del propio repo, sobre 74 hooks reales):
caza malos con AUC 0.83 (saludos, preámbulos, nada concreto,
demasiado largo) pero NO elige ganadores (AUC 0.56, casi moneda
al aire). Por eso el scorer SOLO descarta: score < 50 = débil y
el título queda marcado para revisión humana (``hook_weak`` en el
manifiesto); NUNCA se bloquea el clip por esto y NUNCA se usa
para elegir "el mejor" entre dos títulos decentes. Eso lo decide
la retención real, no el texto.

Uso:
  python -m clipagent.hookscore --hook "GTA 6: Rubius descubre..."
  python -m clipagent.hookscore titulos.txt
  from .hookscore import score_hook
  score, weakest, dealbreaker = score_hook(title)
"""

import argparse
import json
import re
import statistics
import sys

# < 50 = débil (a revisión humana, nunca bloquea el clip).
WEAK_BELOW = 50.0

WORD_RE = re.compile(r"[A-Za-z0-9$%'’áéíóúñüÁÉÍÓÚÑÜ]+")
NUMBER_RE = re.compile(
    r"\$\s?\d[\d,]*(?:\.\d+)?"                       # dinero
    r"|\b\d[\d,]*(?:\.\d+)?\s?"                      # una cifra, con o
    r"(?:%|k\b|x\b|segundos?\b|segs?\b|minutos?\b"    # sin unidad
    r"|mins?\b|horas?\b|hrs?\b|d[ií]as?\b|semanas?\b"
    r"|meses?\b|a[ñn]os?\b|veces\b)?",
    re.IGNORECASE)
PROPER_RE = re.compile(r"(?<!^)\b[A-ZÁÉÍÓÚÑ][a-záéíóúñü]{2,}\b")
HASHTAG_RE = re.compile(r"(?:^|\s)#\w+")
EMOJI_RE = re.compile(r"[\U0001F300-\U0001FAFF☀-➿]")

# Los hooks hablados dicen sus números en voz alta. "Cero dólares"
# y "tres libros" son tan concretos como "$0" y "3". "Uno/una" y
# "primero" quedan fuera a propósito: son relleno más a menudo
# que cantidad (igual que "one"/"first" en el original).
SPOKEN_NUMBERS = {
    "cero", "dos", "tres", "cuatro", "cinco", "seis", "siete",
    "ocho", "nueve", "diez", "once", "doce", "trece", "catorce",
    "quince", "veinte", "treinta", "cuarenta", "cincuenta",
    "sesenta", "setenta", "ochenta", "noventa", "cien", "ciento",
    "mil", "millón", "millones", "billón", "docena", "mitad",
    "doble", "triple",
}
MONEY_WORDS = {
    "dólares", "dólar", "euros", "euro", "pesos", "peso", "pasta",
    "dinero", "porcentaje", "céntimos", "millonario", "millonaria",
    "ingresos", "sueldo", "alquiler", "fortuna",
}

# Palabras que ponen algo en juego. Un título sin ninguna es una
# afirmación; con una, es una razón para seguir mirando.
STAKES = {
    "para", "nunca", "mal", "error", "errores", "perdió", "perder",
    "perdiendo", "costó", "cuesta", "roto", "rota", "falló",
    "fracaso", "fracasó", "fallar", "nadie", "no", "ni", "tampoco",
    "dejó", "despidió", "borró", "borrar", "mató", "matar",
    "reemplazó", "cortó", "ganó", "gratis", "pagó", "pagado",
    "cobró", "contrató", "ahorró", "primero", "prohibido",
    "ilegal", "peor", "odio", "odia", "desperdició", "estafa",
    "mentira", "mintió", "verdad", "secreto", "escondido", "robó",
    "robado", "antes", "hasta", "en", "vez", "pero", "excepto",
    "menos", "problema", "riesgo", "peligro", "advertencia",
    "arrepentimiento", "ojalá", "debería", "todavía", "ya", "solo",
    "sin", "contra", "realidad", "imposible", "nadie",
}

# Aperturas que gastan el primer segundo sin decir nada.
WEAK_OPENERS = [
    "bueno", "vale", "hola", "hey", "chicos", "chicas", "gente",
    "bienvenidos", "bienvenidas", "hoy", "básicamente",
    "honestamente", "mira", "escucha", "eh", "solo", "déjame",
    "quiero", "quería", "uno de", "has visto", "sabías", "en este",
    "en el vídeo", "en el video", "la cosa", "mucho", "hay",
    "esto es", "es", "como", "cuando", "si has", "sabes",
    "te traigo", "les traigo", "hoy les", "hoy te",
]

# Imperativos que se ganan la primera posición.
IMPERATIVES = {
    "para", "roba", "copia", "borra", "prueba", "lee", "guarda",
    "usa", "construye", "haz", "escribe", "envía", "toma",
    "empieza", "deja", "nunca", "siempre", "no", "pon", "corre",
    "revisa", "imagina", "quédate", "descubre",
}

DEALBREAKERS = [
    (re.compile(r"(?i)^\s*(?:no te muevas|no hagas scroll|no pases|"
                r"no te vayas|mira esto|stop scrolling|don'?t scroll)"),
     "Abre pidiendo atención. Pedir atención prueba que no la "
     "has ganado."),
    (re.compile(r"(?i)\b(?:en (?:este|el) (?:v[íi]deo|reel)"
                r"|te voy a (?:enseñar|mostrar)"
                r"|les voy a (?:enseñar|mostrar)"
                r"|te muestro (?:c[óo]mo|como)"
                r"|hoy (?:les|te) traigo|i'?m going to show you)\b"),
     "Preámbulo de vídeo. Bórralo y abre en el payoff."),
    (re.compile(r"(?i)^\s*(?:hola|hey|qu[ée] tal|bienvenidos|"
                r"bienvenidas|buenas|what'?s up|welcome )"),
     "Saludo. Nadie vino al feed a que lo saluden."),
    (HASHTAG_RE,
     "Hashtag en el título. Los hashtags van al final del "
     "caption, si acaso."),
    (EMOJI_RE,
     "Emoji en el título. El texto en pantalla a tamaño de hook "
     "tiene sitio para palabras o para un emoji, no para los dos."),
]


def clamp(n):
    return max(0.0, min(100.0, n))


def words(text):
    # "$18,000" es una palabra cuando se dice, así que es una
    # palabra aquí también.
    return WORD_RE.findall(re.sub(r"(?<=\d),(?=\d)", "", text))


def check_length(text):
    """El título tiene que aterrizar antes de que el pulgar se mueva."""
    w = words(text)
    n = len(w)
    secs = n / 2.75                      # ~165 palabras por minuto
    chars = len(text.strip())
    if 5 <= n <= 12:
        score = 100.0
    elif n < 5:
        score = clamp(100 - (5 - n) * 20)
    else:
        score = clamp(100 - (n - 12) * 11)
    if chars > 60:                       # dos líneas de texto grande
        score -= 12
    return clamp(score), (f"{n} palabras, {chars} caracteres, "
                          f"~{secs:.1f}s hablado (ideal 5-12 palabras)")


def check_specificity(text):
    """Una cosa concreta vale más que tres abstractas."""
    nums = [n.strip() for n in NUMBER_RE.findall(text) if n.strip()]
    propers = set(PROPER_RE.findall(text))
    low = [w.lower().strip("'’") for w in words(text)]
    spoken = [w for w in low if w in SPOKEN_NUMBERS or w in MONEY_WORDS]
    hits = len(nums) + len(propers) + len(spoken)
    score = 15.0 if hits == 0 else clamp(45 + hits * 30)
    found = ", ".join(nums[:2] + sorted(propers)[:2] + spoken[:2])
    return score, (f"{hits} marcador(es) concreto(s)"
                   + (f": {found}" if found else
                      " - sin número, sin nombre, nada comprobable"))


def check_stakes(text):
    """Tensión, coste, negación. Algo que el espectador puede perder."""
    w = [x.lower().strip("'’") for x in words(text)]
    hits = [x for x in w if x in STAKES]
    markers = sorted(set(hits))
    if re.search(r"\$\s?\d", text):
        markers.append("un precio")
    n = len(markers)
    score = {0: 20.0, 1: 70.0}.get(n, 100.0)
    detail = (f"{n} marcador(es) de tensión"
              + (f": {', '.join(markers[:4])}" if markers else
                 " - nada está en juego en esta línea"))
    return clamp(score), detail


def check_frontload(text):
    """La palabra interesante no puede estar en la posición nueve."""
    w = words(text)
    if not w:
        return 0.0, "vacío"
    low = [x.lower().strip("'’") for x in w]
    opener = " ".join(low[:2])
    penalty = 0
    hit_opener = None
    for weak in WEAK_OPENERS:
        if opener.startswith(weak) or low[0] == weak:
            penalty, hit_opener = 30, weak
            break
    payload = None
    for i, token in enumerate(low):
        if (token in STAKES or token in SPOKEN_NUMBERS
                or token in MONEY_WORDS
                or NUMBER_RE.match(w[i])
                or (i and PROPER_RE.match(w[i]))):
            payload = i
            break
    if payload is None:
        base = 30.0
        where = "ninguna palabra con carga en la línea"
    elif payload <= 3:
        base = 100.0
        where = f"carga en la palabra {payload + 1}"
    elif payload <= 6:
        base = 70.0
        where = f"carga en la palabra {payload + 1}, podría ir antes"
    else:
        base = 40.0
        where = f"carga en la palabra {payload + 1}, demasiado tarde"
    detail = where + (f"; apertura débil \"{hit_opener}\""
                      if hit_opener else "")
    return clamp(base - penalty), detail


def check_address(text):
    """Apunta a un espectador, o flota en el aire."""
    low = text.lower()
    w = [x.lower().strip("'’") for x in words(text)]
    if re.search(r"\b(t[uú]|tu|te|ti|vos|ustedes|os)\b", low):
        return 100.0, "habla al espectador"
    if w and w[0] in IMPERATIVES:
        return 90.0, f"apertura imperativa (\"{w[0]}\")"
    if re.search(r"\b(yo|mi|me|nosotros|nuestro|nuestra|mis)\b", low):
        return 70.0, "primera persona, sin espectador nombrado"
    return 35.0, "tercera persona, nadie en la sala"


CHECKS = ["LENGTH", "SPECIFICITY", "STAKES", "FRONTLOAD", "ADDRESS"]


def run(text):
    results = {
        "LENGTH": check_length(text),
        "SPECIFICITY": check_specificity(text),
        "STAKES": check_stakes(text),
        "FRONTLOAD": check_frontload(text),
        "ADDRESS": check_address(text),
    }
    flags = [msg for pattern, msg in DEALBREAKERS if pattern.search(text)]
    scores = [results[c][0] for c in CHECKS]
    # La propiedad más débil limita el título, igual que en el
    # original: una sola propiedad mala basta para que el pulgar
    # siga moviéndose.
    overall = statistics.mean(scores) * 0.6 + min(scores) * 0.4 - len(flags) * 15
    overall = clamp(overall)
    verdict = "STRONG" if overall >= 70 and min(scores) >= 55 and not flags else (
        "OK" if overall >= WEAK_BELOW else "WEAK")
    return results, overall, verdict, flags


def score_hook(title):
    """Puntúa un título (0-100) para MATAR débiles, no para elegir.

    Devuelve (score, weakest, dealbreaker). score < 50 = débil:
    el título queda marcado para revisión humana (``hook_weak``
    en el manifiesto); NUNCA bloquea el clip y NUNCA se usa para
    elegir "el mejor" entre dos títulos decentes (el propio repo
    mide que no elige ganadores, AUC 0.56).
    """
    results, overall, _verdict, flags = run(title or "")
    weakest = min(CHECKS, key=lambda c: results[c][0])
    return round(overall, 1), weakest, (flags[0] if flags else None)


def bar(score, width=24):
    filled = round(score / 100 * width)
    return "#" * filled + "." * (width - filled)


def render_one(text, results, overall, verdict, flags, out=sys.stdout):
    print("\nHOOK SCORE", file=out)
    print("=" * 62, file=out)
    print(f"  \"{text.strip()}\"\n", file=out)
    for name in CHECKS:
        score, detail = results[name]
        print(f"  {name:<13} {bar(score)} {score:5.1f}", file=out)
        print(f"  {'':<13} {detail}", file=out)
    print("-" * 62, file=out)
    print(f"  {'HOOK SCORE':<13} {bar(overall)} {overall:5.1f}   {verdict}",
          file=out)
    for f in flags:
        print(f"\n  DEALBREAKER  {f}", file=out)
    if verdict != "STRONG":
        weakest = min(CHECKS, key=lambda c: results[c][0])
        print(f"\n  Propiedad más débil: {weakest}. Arregla esa y "
              "vuelve a correr.", file=out)
    print("", file=out)


def main(argv=None):
    ap = argparse.ArgumentParser(
        description="Puntúa un título de clip en español (0-100).")
    ap.add_argument("input", nargs="?", default="-",
                    help="fichero con un título por línea, o -")
    ap.add_argument("--hook", help="puntúa un solo título")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)

    if args.hook:
        lines = [args.hook]
    else:
        raw = (sys.stdin.read() if args.input == "-"
               else open(args.input, encoding="utf-8").read())
        lines = [ln.strip() for ln in raw.splitlines() if ln.strip()]
    if not lines:
        print("nothing to score", file=sys.stderr)
        return 2

    payload = []
    for line in lines:
        results, overall, verdict, flags = run(line)
        payload.append({
            "hook": line,
            "checks": {k: {"score": round(v[0], 1), "detail": v[1]}
                       for k, v in results.items()},
            "weakest": min(CHECKS, key=lambda c: results[c][0]),
            "flags": flags,
            "score": round(overall, 1),
            "verdict": verdict,
        })

    if args.json:
        print(json.dumps(payload if len(payload) > 1 else payload[0],
                         indent=2, ensure_ascii=False))
        return 0

    if len(payload) == 1:
        results, overall, verdict, flags = run(lines[0])
        render_one(lines[0], results, overall, verdict, flags)
    else:
        for row in sorted(payload, key=lambda r: -r["score"]):
            print(f"{row['score']:5.1f} {row['verdict']:<7} {row['hook']}")
    return 0 if max(p["score"] for p in payload) >= 70 else 1


if __name__ == "__main__":
    raise SystemExit(main())

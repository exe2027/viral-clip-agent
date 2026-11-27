#!/usr/bin/env python3
"""beats.py - temporización de guion y gancho hablado (adopción 5/5).

Port de ``beats.py`` de instagram-agent-skill/skills/ig-reel
(Jakeschincariol, MIT, 215 l.), adaptado al español y a nuestro
pipeline. El origen convierte un guion en una hoja de beats con
tiempos estimados por palabras/minuto y marca lo que mata un
reel en el montaje: un gancho que pasa de los 3 segundos, un
beat que dura demasiado para que el espectador se quede, una
racha de líneas sin nada concreto, y una duración total que no
coincide con el objetivo.

Dos usos, y no se mezclan:

1. Guionizado (``analyse_script``): el port fiel del origen, para
   contenido que SE GUIONIZA (la skill ``ig-reel``). Incluye la
   nota de loop del origen (la última línea repite una palabra
   del gancho). Etiquetas HOOK / MID / CTA como el origen.
2. Hablado (``analyse_spoken``): lo nuevo de esta adopción, para
   clips CORTADOS de directos. Usa los timestamps REALES palabra
   por palabra de faster-whisper (``transcribe.py``, que ya
   transcribe con ``word_timestamps=True``) en vez de estimar
   por wpm. Valida que el gancho HABLADO cabe en 3 s y que no
   hay rachas abstractas al inicio. NO calcula loop: la spec de
   títulos eliminó ``loop_point`` por no tener consumidor, y un
   clip cortado no tiene última línea guionizada que hacer de
   eco.

Los tiempos son una estimación por conteo de palabras a una tasa
de palabras por minuto en el caso guionizado; en el hablado son
medidos. En ambos casos sirven para planificar y para señalar
calidad, no como veredicto: un gancho hablado largo es señal de
calidad, nunca un título falso, y por sí solo no bloquea ni
retiene ningún clip (ver ``quality_gate.build_quality_gate``).

Uso
  python -m clipagent.beats guion.txt
  python -m clipagent.beats guion.txt --target 30
  python -m clipagent.beats guion.txt --wpm 185 --target 45
  python -m clipagent.beats --transcript clip.transcript.json
  python -m clipagent.beats --transcript clip.transcript.json --json

  from clipagent.beats import analyse_script, analyse_spoken
"""

import argparse
import json
import re
import sys

try:  # import relativo (uso como paquete) o absoluto (script directo)
    from .hookscore import (
        MONEY_WORDS,
        NUMBER_RE,
        PROPER_RE,
        SPOKEN_NUMBERS,
    )
except ImportError:  # pragma: no cover - ejecución directa
    from clipagent.hookscore import (  # type: ignore[no-redef]
        MONEY_WORDS,
        NUMBER_RE,
        PROPER_RE,
        SPOKEN_NUMBERS,
    )

WORD_RE = re.compile(r"[A-Za-z0-9$%'’áéíóúñüÁÉÍÓÚÑÜ]+")
SENT_RE = re.compile(r"[^.!?]+[.!?]*")
# Fin de frase en lo hablado: como el origen más la elipsis.
SPOKEN_END_RE = re.compile(r"[.!?…]+$")
STOPWORDS = {
    "the", "a", "an", "and", "or", "but", "if", "of", "to", "in", "on",
    "for", "with", "that", "this", "it", "is", "are", "was", "were",
    "be", "been", "you", "your", "i", "my", "me", "we", "our", "they",
    "them", "he", "she", "so", "just", "not", "no", "do", "did",
    "does", "have", "has", "had", "will", "can", "at", "as", "by",
    "from", "out", "up", "off", "one", "all",
    # Stopwords españolas para el loop de guion (mismo papel que
    # las inglesas del origen: no cuentan como eco del gancho).
    "el", "la", "los", "las", "un", "una", "unos", "unas", "de",
    "en", "y", "o", "que", "es", "son", "se", "su", "sus", "con",
    "por", "para", "al", "del", "lo", "le", "les", "mi", "mis",
    "tu", "tus", "yo", "tú", "él", "ella", "nosotros", "vosotros",
    "ellos", "ellas", "este", "esta", "estos", "estas", "eso",
    "si", "no", "sí", "como", "cuando", "donde", "más", "muy",
    "ya", "pero", "porque", "pues", "hasta", "sin", "sobre",
}

WPM_DEFAULT = 165  # palabras por minuto (origen; rango típico 150-200)
HOOK_WINDOW = 3.0  # segundos. Pasado esto, el pulgar ya decidió.
MAX_BEAT = 4.0     # segundos en una idea sin cambio en pantalla.
ABSTRACT_RUN = 3   # beats seguidos sin nada comprobable.
TARGET_TOLERANCE = 0.10  # desvío permitido del objetivo (±10%).


def words(text):
    # "$18,000" es una palabra cuando se dice, así que es una
    # palabra aquí también (igual que en el origen).
    return WORD_RE.findall(re.sub(r"(?<=\d),(?=\d)", "", text))


def pretty(token):
    """Pone el separador de miles de vuelta para mostrarlo."""
    return re.sub(r"(\d)(?=(\d{3})+$)", r"\1,", token)


def tc(seconds):
    m, s = divmod(seconds, 60)
    return f"{int(m)}:{s:04.1f}"


def concrete_count(text):
    """Nº de cosas comprobables en un texto (número o nombre).

    Adaptación al español del CONCRETE_RE del origen (que es
    inglés: $ + dígitos + palabra capitalizada). Reutiliza la
    maquinaria que ya tenemos en español en ``hookscore`` (la
    misma definición de concreto que ``check_specificity``):
    cifras con unidad, nombres propios, números dichos en voz
    alta ("tres", "mil", "millón") y palabras de dinero. Con
    una precisión más: una palabra capitalizada a INICIO DE
    FRASE no es nombre propio ("¿Sabes lo que te digo?" no
    tiene nada concreto, aunque "Sabes" lleve mayúscula tras
    el "¿"). ``hookscore`` no hace esa distinción porque
    puntúa títulos de una línea; aquí las frases existen.
    """
    low = [w.lower().strip("'’") for w in words(text)]
    hits = len(NUMBER_RE.findall(text))
    for m in PROPER_RE.finditer(text):
        before = text[:m.start()].rstrip(" ¿¡")
        if not before or before[-1] in ".!?…":
            continue  # inicio de frase, no nombre propio
        hits += 1
    hits += sum(1 for w in low if w in SPOKEN_NUMBERS)
    hits += sum(1 for w in low if w in MONEY_WORDS)
    return hits


def split_beats(raw, wps):
    """Una línea es un beat, salvo que sea demasiado larga para serlo."""
    beats = []
    for line in [ln.strip() for ln in raw.splitlines()]:
        if not line:
            continue
        if len(words(line)) / wps <= MAX_BEAT * 1.5:
            beats.append(line)
            continue
        # Párrafo largo: se parte por frases para que los tiempos
        # signifiquen algo, y el informe dice que se partió.
        parts = [p.strip() for p in SENT_RE.findall(line) if p.strip()]
        buf = ""
        for part in parts:
            candidate = (buf + " " + part).strip()
            if buf and len(words(candidate)) / wps > MAX_BEAT:
                beats.append(buf)
                buf = part
            else:
                buf = candidate
        if buf:
            beats.append(buf)
    return beats


def _label_rows(rows, total):
    """Etiqueta las posiciones estructurales (origen)."""
    for r in rows:
        if r["n"] == 1 or r["start"] + r["dur"] <= HOOK_WINDOW:
            r["label"] = "HOOK"
    rows[-1]["label"] = "CTA" if rows[-1]["label"] != "HOOK" else "HOOK/CTA"
    half = total / 2
    for r in rows:
        if not r["label"] and r["start"] <= half < r["start"] + r["dur"]:
            r["label"] = "MID"


def _check_notes(rows, total, wpm, target, hook_value=None,
                 hook_spoken=False):
    """Los 5 problemas del origen, sobre filas ya construidas.

    Devuelve (issues, notes): códigos máquina + texto humano.
    El loop NO se chequea aquí: solo lo hace ``analyse_script``,
    que es el único uso guionizado. ``hook_value`` es lo que se
    compara con la ventana de 3 s: la duración del beat 1 en el
    guion; el fin del beat 1 desde t=0 en lo hablado (el silencio
    inicial también cuenta).
    """
    wps = wpm / 60.0
    issues, notes = [], []
    hook_value = rows[0]["dur"] if hook_value is None else hook_value
    if hook_value > HOOK_WINDOW:
        rows[0]["flags"].append(
            f"el gancho dura {rows[0]['dur']:.1f}s, pasado el límite "
            f"de {HOOK_WINDOW:.0f}s")
        issues.append("hook_long")
        if hook_spoken:
            notes.append(
                f"El gancho hablado termina a los {hook_value:.1f}s, "
                f"pasado el límite de {HOOK_WINDOW:.0f}s: llega "
                "después de la decisión.")
        else:
            notes.append(
                f"El beat 1 tarda {rows[0]['dur']:.1f}s en decirse. "
                f"Córtalo a {int(HOOK_WINDOW * wps)} palabras o menos, "
                "o el gancho llega después de la decisión.")
    if rows[0]["concrete"] == 0:
        issues.append("hook_not_concrete")
        notes.append("El beat 1 no tiene número ni nombre. Los "
                     "ganchos sin algo comprobable son los que se "
                     "pasan de largo.")
    for r in rows:
        if r["dur"] > MAX_BEAT:
            r["flags"].append(f"{r['dur']:.1f}s en un beat")
    long_beats = [r["n"] for r in rows if r["dur"] > MAX_BEAT]
    if long_beats:
        issues.append("beat_long")
        notes.append(
            f"Beat(s) {', '.join(map(str, long_beats))} pasan de "
            f"{MAX_BEAT:.0f}s. O se parte la línea o se cambia lo "
            "que hay en pantalla dentro de ella. Un fotograma "
            "estático es donde se va la gente.")

    run, start = 0, None
    for r in rows:
        if r["concrete"] == 0:
            run += 1
            start = start if start is not None else r["n"]
            if run == ABSTRACT_RUN:
                issues.append("abstract_run")
                notes.append(
                    f"Beats {start}-{r['n']} no tienen nada concreto. "
                    "Pon un número, un nombre o un precio en uno "
                    "de ellos.")
        else:
            run, start = 0, None

    if target:
        delta = total - target
        if abs(delta) <= target * TARGET_TOLERANCE:
            notes.append(f"La duración está en objetivo ({total:.1f}s "
                         f"frente a {target}s).")
        elif delta > 0:
            issues.append("over_target")
            notes.append(f"{delta:.1f}s por encima del objetivo. "
                         f"Corta unas {int(delta * wps)} palabras.")
        else:
            issues.append("under_target")
            notes.append(f"{-delta:.1f}s por debajo del objetivo. "
                         f"O añade {int(-delta * wps)} palabras o "
                         "hazlo corto. Corto suele ser lo correcto.")
    return issues, notes


def analyse_script(raw, wpm=WPM_DEFAULT, target=None):
    """Hoja de beats de un GUION (port fiel del origen).

    Duración por línea = palabras / (wpm/60). Incluye la nota de
    loop del origen (la última línea comparte una palabra
    no-stopword con el gancho): solo tiene sentido en contenido
    guionizado, nunca en un clip cortado de un directo.
    """
    wps = wpm / 60.0
    beats = split_beats(raw, wps)
    if not beats:
        return None

    rows, clock = [], 0.0
    for i, text in enumerate(beats):
        n = len(words(text))
        dur = n / wps
        rows.append({
            "n": i + 1,
            "start": round(clock, 2),
            "dur": round(dur, 2),
            "words": n,
            "text": text,
            "concrete": concrete_count(text),
            "label": "",
            "flags": [],
        })
        clock += dur
    total = clock
    _label_rows(rows, total)
    issues, notes = _check_notes(rows, total, wpm, target)

    # ¿La última línea te devuelve a la primera? (origen)
    first = {w.lower() for w in words(rows[0]["text"])
             if w.lower() not in STOPWORDS}
    last = {w.lower() for w in words(rows[-1]["text"])
            if w.lower() not in STOPWORDS}
    loop = sorted(pretty(w) for w in first & last)
    if loop:
        notes.append(f"Loop: el último beat repite "
                     f"\"{', '.join(loop[:3])}\" del gancho. Las "
                     "segundas vistas son alcance gratis.")
    else:
        notes.append("Sin loop. El último beat no comparte ninguna "
                     "palabra con el gancho, así que el vídeo termina "
                     "plano. Repetir una palabra del beat 1 es el "
                     "replay más barato que hay.")

    return {
        "wpm": wpm, "target": target,
        "total_seconds": round(total, 2),
        "total_words": sum(r["words"] for r in rows),
        "beats": rows,
        "issues": issues,
        "notes": notes,
    }


def _words_with_times(transcript, wps):
    """Palabras con (texto, inicio, fin) + si los tiempos son reales.

    Acepta el dict de faster-whisper (``transcribe.py``:
    ``{"segments": [{"start", "end", "text", "words": [{"word",
    "start", "end"}]}]}``) o una lista plana de palabras. Si las
    palabras no traen timestamps, se estiman repartiendo la
    duración del segmento entre sus palabras (como el SRT de
    ``test_titles_subtitles.py``); si ni el segmento trae tiempos,
    se estima por wpm desde el reloj acumulado. En ese caso
    ``timed`` es False y el resultado lo declara (``source``).
    """
    timed_words = []
    estimated = False
    if isinstance(transcript, dict):
        segments = transcript.get("segments") or []
    elif isinstance(transcript, (list, tuple)):
        segments = transcript
    else:
        return [], False

    clock = 0.0
    for seg in segments:
        if not isinstance(seg, dict):
            continue
        seg_words = seg.get("words") or []
        if seg_words and all(
                isinstance(w.get("start"), (int, float))
                and isinstance(w.get("end"), (int, float))
                for w in seg_words if isinstance(w, dict)):
            for w in seg_words:
                timed_words.append({
                    "text": str(w.get("word") or "").strip(),
                    "start": float(w["start"]),
                    "end": float(w["end"]),
                })
                clock = max(clock, float(w["end"]))
            continue
        # Sin timestamps por palabra: palabras del texto del segmento.
        text = str(seg.get("text") or "")
        parts = [p for p in text.split() if p]
        if not parts:
            continue
        seg_start = seg.get("start")
        seg_end = seg.get("end")
        if (isinstance(seg_start, (int, float))
                and isinstance(seg_end, (int, float))
                and seg_end > seg_start):
            step = (seg_end - seg_start) / len(parts)
            for i, p in enumerate(parts):
                timed_words.append({
                    "text": p,
                    "start": seg_start + i * step,
                    "end": seg_start + (i + 1) * step,
                })
            clock = max(clock, float(seg_end))
        else:
            for p in parts:
                dur = 1.0 / wps
                timed_words.append({
                    "text": p, "start": clock, "end": clock + dur,
                })
                clock += dur
        estimated = True
    timed_words = [w for w in timed_words if w["text"]]
    return timed_words, (not estimated and bool(timed_words))


def _spoken_beats(timed_words):
    """Agrupa palabras en beats por fin de frase.

    Un beat termina cuando una palabra termina en ``. ! ? …``.
    Las fronteras de segmento de faster-whisper NO parten beats:
    son artefactos del reconocedor, no de la idea (un segmento
    puede cortarse a mitad de frase). Un beat sin terminador y
    más largo que 6 s se queda como un solo beat y se marca
    ``beat_long``: es el espejo hablado del ``split_beats`` del
    origen.
    """
    beats, cur = [], []
    for w in timed_words:
        cur.append(w)
        if SPOKEN_END_RE.search(w["text"]):
            beats.append(cur)
            cur = []
    if cur:
        beats.append(cur)
    return beats


def analyse_spoken(transcript, wpm=WPM_DEFAULT, target=None):
    """Valida el gancho HABLADO de un clip cortado, con timestamps.

    ``transcript`` es el dict de faster-whisper (ver
    ``_words_with_times``). El gancho es el primer beat, medido
    DESDE EL INICIO DEL CLIP (t=0), no desde la primera palabra:
    si el clip abre con silencio y el primer beat termina a los
    3,5 s, el gancho NO cabe en la ventana de 3 s.

    No calcula loop (ver docstring del módulo). Devuelve None si
    no hay palabras. ``source`` dice si los tiempos son medidos
    (``"timestamps"``) o estimados por wpm (``"estimated"``).
    """
    wps = wpm / 60.0
    timed_words, timed = _words_with_times(transcript, wps)
    if not timed_words:
        return None

    groups = _spoken_beats(timed_words)
    rows = []
    for i, grp in enumerate(groups):
        text = " ".join(w["text"] for w in grp)
        start = grp[0]["start"]
        dur = grp[-1]["end"] - grp[0]["start"]
        rows.append({
            "n": i + 1,
            "start": round(start, 2),
            "dur": round(dur, 2),
            "words": len(grp),
            "text": text,
            "concrete": concrete_count(text),
            "label": "HOOK" if i == 0 else "",
            "flags": [],
        })
    total = sum(r["dur"] for r in rows)
    # El gancho hablado se mide desde t=0 (ver docstring): el
    # primer beat puede empezar tarde por silencio inicial, y
    # eso también cuenta.
    hook_end = rows[0]["start"] + rows[0]["dur"]
    issues, notes = _check_notes(rows, total, wpm, target,
                                 hook_value=hook_end,
                                 hook_spoken=True)

    return {
        "source": "timestamps" if timed else "estimated",
        "wpm": wpm, "target": target,
        "total_seconds": round(total, 2),
        "total_words": sum(r["words"] for r in rows),
        "hook_start_s": round(rows[0]["start"], 2),
        "hook_end_s": round(hook_end, 2),
        "hook_words": rows[0]["words"],
        "hook_concrete": rows[0]["concrete"],
        "beats": rows,
        "issues": issues,
        "notes": notes,
    }


def render(a, out=sys.stdout):
    head = (f"BEAT SHEET  ·  {a['total_words']} words  ·  ~{a['total_seconds']:.1f}s "
            f"at {a['wpm']} wpm" + (f"  ·  target {a['target']}s" if a["target"] else ""))
    print("\n" + head, file=out)
    print("=" * max(len(head), 72), file=out)
    for r in a["beats"]:
        label = f"{r['label']:<8}" if r["label"] else " " * 8
        print(f"  {tc(r['start'])}  {r['dur']:4.1f}s  {label}{r['text']}", file=out)
        for f in r["flags"]:
            print(f"  {'':>6}  {'':>5}  {'':<8}^ {f}", file=out)
    print("-" * max(len(head), 72), file=out)
    for n in a["notes"]:
        print(f"  - {n}", file=out)
    print("", file=out)


def main(argv=None):
    ap = argparse.ArgumentParser(description="Time a Reel script into a beat sheet.")
    ap.add_argument("input", nargs="?", default="-",
                    help="script file, or - for stdin")
    ap.add_argument("--wpm", type=float, default=WPM_DEFAULT,
                    help="speaking rate (default 165)")
    ap.add_argument("--target", type=float,
                    help="target length in seconds")
    ap.add_argument("--transcript", default=None,
                    help="faster-whisper transcript.json: modo hablado")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)

    if args.transcript:
        with open(args.transcript, encoding="utf-8") as f:
            data = json.load(f)
        a = analyse_spoken(data, wpm=args.wpm, target=args.target)
        if not a:
            print("empty transcript", file=sys.stderr)
            return 2
    else:
        raw = sys.stdin.read() if args.input == "-" else open(
            args.input, encoding="utf-8").read()
        a = analyse_script(raw, wpm=args.wpm, target=args.target)
        if not a:
            print("empty script", file=sys.stderr)
            return 2
    if args.json:
        print(json.dumps(a, indent=2, ensure_ascii=False))
    else:
        render(a)
    return 0


if __name__ == "__main__":
    sys.exit(main())

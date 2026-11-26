#!/usr/bin/env python3
"""hook_formulas.py — nombra la fórmula del gancho de un título.

Adopta ``hooks.json`` de instagram-agent-skill (Jakeschincariol, MIT):
26 fórmulas de ganchos, cada una con su regex ``match`` y un
``classify_order`` (de más específica a menos). El clasificador
recorre ese orden y la primera fórmula que casa gana; si ninguna
casa, se abstiene (``unclassified``).

HALLAZGO HONESTO: en el repo origen NO existe código de clasificador
(``hookscore.py`` solo puntúa; ``grep classif`` sobre
``skills/ig-reel/`` solo encuentra la nota de ``hooks.json`` y dos
menciones en ``SKILL.md``). El comportamiento que se implementa aquí
es el que el propio repo documenta: la nota de ``hooks.json`` («Walk
them in ``classify_order``, which is most specific first,
case-insensitive») y la salida de ``swipe.py`` en el README, que
etiqueta ``unclassified`` lo que no casa. El README dice que el
clasificador pasó de nombrar el 8% de ganchos reales al 49% tras
reescribir los regex contra habla transcrita — sin script de
medición ni corpus en el repo, así que se cita con esa salvedad,
no como medido aquí.

USO HONESTO (igual que ``hookscore.py``): esto NOMBRA fórmulas, no
elige ganadores. El scorer mide AUC 0,56 eligiendo ganadores (casi
moneda al aire) y el clasificador ni siquiera puntúa. La fórmula
sirve para la lectura descriptiva de la auditoría («qué fórmula
funcionó») y nunca es una puerta ni un ranking de «mejor fórmula».

CAPA ES: los regex de ``hooks.json`` son en inglés y nuestros títulos
son en español; sin capa ES el clasificador no nombraría casi ningún
clip real y el cableado en auditoría/viral no serviría. Precedente:
adopción 1 (lexicón ES en ``slop.json``) y adopción 2 (asks ES en el
linter). Cada fórmula lleva en ``ES_MATCH`` la traducción directa de
su disparador EN (ver tabla por id abajo); el JSON queda verbatim y
el comportamiento EN no cambia. La capa ES es traducción, no
medición: su cobertura en títulos reales no está medida (el 49% del
origen es habla EN transcrita, no nuestros títulos ES).

  1  Cost Confession      me costó … $… / perdí … $… / $… es lo que …
  2  Negative Command     deja de … / no hagas … / nunca (hagas|publiques|…)
  3  Nobody Tells You     nadie te (dice|dijo|cuenta|…) / lo que nadie dice
  4  The Replacement      reemplazó … $… / en vez de (pagar|contratar)
  5  Time Collapse        antes me (tomaba|llevaba) / ahora me (toma|lleva)
  6  The Receipt          … durante N días / estos son los números
  7  Wrong Way, Right Way el error (más) común / estás … mal
  8  Insider Leak         pasé / trabajé N años … / nunca (contamos|dijimos)
  9  The Steal            roba / copia (este|esta|esto)
  10 If This, Then Watch  si (tus|tu|te|tienes) …
  11 Numbered Favourite   número N (la rama ^N … ya es agnóstica al idioma)
  12 The Objection        cita + vale / bueno / de acuerdo / aquí
  13 Before And After     (este|esto) era … (este|esto) es / antes … después
  14 The Callout          atención / escucha(n) / para (los|las|todos|todas)
  15 The Flop Record      mis primeros N / primeros N (reels|vídeos|…)
  16 Verbatim Question    (¿)cómo / qué / por qué / cuándo / dónde … ?
  17 Head To Head         vs / versus (agnóstico) / X contra Y
  18 Cold Open Demo       mira (lo que|esto|cómo) / te (enseño|muestro)
  19 The Deadline         antes de que (cambie|termine|…) / cambia en cuanto
  20 Permission           no necesitas … / no hace falta …
  21 Mid-Sentence Start   … / y (entonces|ahí) cuando / y (él|ella) (dijo|…)
  22 Contrarian Flip      los/las … (terminan|ganan|pierden) primero
  23 The Statistic        ^N% (agnóstico) / N% de …
  24 The Reveal           (este|esto) es (el|la) nuevo … / este nuevo …
  25 Someone Else's Result hubo un(a) … / un(a) (creador|…) que (ganó|hizo|…)
  26 The Superlative      el/la/lo (mejor|peor|único|más rápido|…) / la forma más …

Nota: los regex EN usan dígitos para los números («I spent six years»
NO casa en EN: el original exige ``\\d+ years``); la capa ES hereda
esa limitación a propósito (``Pasé 6 años …`` casa, ``Pasé seis años``
no), igual que el origen.

EN_FIX: 6 de los 26 ``example`` del propio ``hooks.json`` NO casan
con su propio regex EN (medido 2026-11-26, ver tests). No es un
error de este port: es el origen (su README cuenta que los regex se
reescribieron contra habla transcrita, no contra sus ejemplos).
Cada hueco se tapa en ``EN_FIX`` con la corrección mínima del
disparador, sin tocar el JSON:

  7  Wrong Way     el regex exige «doing» («you are doing … wrong»);
                   el ejemplo dice «writing»: se admite cualquier
                   verbo («you are … wrong»).
  8  Insider Leak  el regex exige dígitos («6 years»); el ejemplo
                   dice «six»: se admiten los números escritos.
  11 Numbered      el regex admite número-escrito + 1 palabra +
                   (that|you); el ejemplo tiene 2 sustantivos
                   («Five caption edits that») y «Number four» en
                   palabra: se admiten 2 sustantivos y «number»
                   en palabra.
  12 The Objection el regex excluye el apóstrofo dentro de la cita
                   ([^"”']); el ejemplo lleva «don't»: se admite.
  14 The Callout   el regex exige el grupo + puntuación inmediata;
                   el ejemplo intercala («Photographers charging
                   under $2,000:»): se admiten palabras entre medias.
  19 The Deadline  el regex exige «changes on» / «before … change»;
                   el ejemplo dice «change the second you post»:
                   se admite «change(s) the second / as soon as».

  El JSON sigue verbatim; EN_FIX es overlay en Python, igual que
  ES_MATCH. Los 26 ``example`` clasifican a su fórmula (test)."

Cálculo local, sin red, sin secretos. Los datos se cargan de junto
al módulo (``hooks.json``); si faltan, error ruidoso (nunca un
default silencioso), igual que ``slop.json`` en ``humanize.py``.

Uso:
  python -m clipagent.hook_formulas "Nobody tells you that ..."
  from .hook_formulas import classify_hook, formula_name
  classify_hook(title)   # -> dict de la fórmula o None
  formula_name(title)    # -> "Nobody Tells You" o "unclassified"
"""

import argparse
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(HERE, "hooks.json")

# Etiqueta cuando ninguna fórmula casa (la del swipe del README origen).
UNCLASSIFIED = "unclassified"

# Traducción directa al español de cada disparador EN (ver docstring).
# Clave: id de la fórmula. Se combina como match_EN | match_ES.
ES_MATCH = {
    1: (r"\$\s?[\d.,]+\s+es lo que\b"
        r"|\bme cost[óo]\b[^.!?]{0,40}\$\s?[\d.,]+"
        r"|\bperd[íi]\b[^.!?]{0,20}\$\s?[\d.,]+"),
    2: (r"^\s*(?:deja de|no hagas|no compres|no uses)\b"
        r"|\bnunca\s+(?:hagas|publiques|compres|uses|digas|empieces|subas)\b"),
    3: (r"\bnadie te (?:dice|dijo|cuenta|contó|enseña|enseñó)\b"
        r"|\blo que nadie (?:te dice|dice|te cuenta)\b"),
    4: (r"\breemplaz[óoa]\b[^.!?]*\$\s?[\d.,]+"
        r"|\$\s?[\d.,]+[^.!?]*\breemplaz[óoa]\b"
        r"|\ben vez de (?:pagar|contratar)\b"),
    5: (r"\bantes me (?:tomaba|tomaban|llevaba|llevaban)\b"
        r"|\bahora (?:solo )?(?:me )?(?:toma|toman|lleva|llevan|tarda|tardan)\b"),
    6: (r"\b(?:hice|publiqu[ée])\b[^.!?]{0,40}\b(?:durante|por)\s+\d+\s+"
        r"(?:d[íi]as|semanas|meses|a[ñn]os)\b"
        r"|\bestos son los n[úu]meros\b"
        r"|\baqu[íi] est[áa]n (?:los|mis) n[úu]meros\b"),
    7: (r"\b(?:el )?(?:error|fallo) (?:m[áa]s )?(?:com[úu]n|grande|grave)\b"
        r"|\best[áa]s\b[^.!?]{0,30}\bmal\b"
        r"|\bhaciendo (?:esto|eso|lo) mal\b"),
    8: (r"\bpas[ée]\s+\d+\s*a[ñn]os\b"
        r"|\btrabaj[ée]\s+\d+\s*a[ñn]os\b"
        r"|\bnunca (?:contamos|dijimos|pusimos)\b"),
    9: (r"^\s*(?:roba|copia) (?:este|esta|esto|estos|estas)\b"
        r"|\b(?:roba|copia) (?:este|esta|esto)\b"),
    10: r"^\s*si (?:tus?|tu|t[úu]|te|tienes)\b",
    11: r"\bn[úu]mero \d+\b",
    12: (r"^\s*[\"“«'][^\"”'»]{5,120}[\"”'»][,.]?\s*"
        r"(?:vale|bueno|de acuerdo|aqu[íi]|as[íi] que)\b"),
    13: (r"\b(?:esto|este|esta) era\b[^.!?]*[.!?]\s*(?:esto|este|esta) es\b"
        r"|\bantes\b[^.!?]*\bdespu[ée]s\b"),
    14: (r"^\s*(?:atenci[óo]n|escucha|escuchen)\b"
        r"|^\s*para (?:los|las|todos|todas)\b"),
    15: (r"\bmis primeros \d+\b"
        r"|\bprimeros \d+\s+(?:reels?|v[íi]deos?|videos?|clientes?|meses)\b"),
    16: (r"^\s*[\"“«¿]{0,2}(?:c[óo]mo|qu[ée]|por qu[ée]|cu[áa]ndo|d[óo]nde|"
        r"qui[ée]n|cu[áa]l|puedes|deber[íi]a|has|tienes)\b[^?]{3,110}\?"),
    17: r"\w+\s+contra\s+\w+",
    18: (r"^\s*mira (?:lo que|esto|c[óo]mo)\b"
        r"|^\s*te (?:ense[ñn]o|muestro)\b"),
    19: (r"\bantes de que\b[^.!?]*\b(?:cambie|termine|cierre|expire)\b"
        r"|\bcambia\b[^.!?]{0,25}\b(?:en cuanto|cuando|el|de)\b"),
    20: r"\bno necesitas\b|\bno hace falta\b",
    21: (r"^\s*(?:\.\.\.|…)"
        r"|^\s*y (?:entonces|ah[íi]) (?:fue )?cuando\b"
        r"|^\s*y (?:[ée]l|ella) (?:dijo|pregunt[óo])\b"),
    22: (r"^\s*(?:los|las)\s+\w+\s+(?:terminan|ganan|pierden)\s+"
        r"(?:primero|primeros|[úu]ltimos|al final)\b"),
    23: r"\b\d[\d,.]*\s?%\s+de\b",
    24: (r"^\s*(?:este|esto|esta) es (?:el |la |mi )?nuev[oa]\b"
        r"|^\s*(?:este|esta) nuevo\b"
        r"|^\s*este \w+ tiene (?:el|la)\b"),
    25: (r"\bhubo (?:un|una|este|esta)\b"
        r"|\b(?:un|una) (?:creador|creadora|chico|chica|estudiante|"
        r"fundador|cliente|amigo|amiga) que "
        r"(?:creci[óo]|hizo|construy[óo]|consigui[óo]|logr[óo]|gan[óo])\b"),
    26: (r"^\s*(?:el|la|lo)\s+(?:mejor|peor|[úu]nico|[úu]nica|m[áa]s r[áa]pido|"
        r"m[áa]s r[áa]pida|m[áa]s f[áa]cil)\b"
        r"|^\s*la (?:forma|manera) m[áa]s\b"),
}

# Corrección mínima de los 6 huecos del regex EN (ver docstring).
# Clave: id de la fórmula. Se combina como match_EN | match_ES | fix.
EN_FIX = {
    7: r"\byou (?:are|'re)\b[^.!?]{0,30}\bwrong\b",
    8: (r"\bi (?:spent|worked|ran|was|have been)\b[^.!?]*\b"
        r"(?:six|seven|eight|nine|ten|eleven|twelve|fifteen|twenty|"
        r"thirty)\s+years?\b"),
    11: (r"\b(?:three|four|five|six|seven|eight|nine|ten)\s+\w+\s+\w+\s+"
         r"(?:every|that|you)\b"
         r"|\bnumber (?:one|two|three|four|five|six|seven|eight|nine|ten)\b"),
    12: (r"^\s*[\"“'][^\"”]{5,120}[\"”][,.]?\s*"
         r"(?:fine|ok|okay|sure|right|here)\b"),
    14: (r"^\s*[a-z][\w ]{2,28}(?:ers|ists|ors|ants)\b[^.!?]{0,40}\s*[:,]"),
    19: r"\bchanges? (?:the second|as soon as)\b",
}

_CACHE = None


def _load_data():
    """Carga hooks.json (con caché). Sin archivo: error ruidoso."""
    global _CACHE
    if _CACHE is not None:
        return _CACHE
    try:
        with open(DATA, encoding="utf-8") as fh:
            raw = json.load(fh)
    except (OSError, ValueError) as exc:
        raise RuntimeError(
            f"hook_formulas: no se pudo cargar {DATA}: {exc}. "
            "Sin datos no hay clasificador (nunca un default "
            "silencioso).") from exc
    hooks = raw.get("hooks") or []
    order = raw.get("classify_order") or []
    by_id = {}
    compiled = {}
    for entry in hooks:
        fid = entry.get("id")
        if fid is None:
            continue
        by_id[fid] = entry
        pattern = str(entry.get("match") or "")
        extra = ES_MATCH.get(fid)
        if extra:
            pattern = f"(?:{pattern})|(?:{extra})" if pattern else extra
        fix = EN_FIX.get(fid)
        if fix:
            pattern = f"(?:{pattern})|(?:{fix})" if pattern else fix
        try:
            compiled[fid] = re.compile(pattern, re.IGNORECASE) \
                if pattern else None
        except re.error as exc:
            raise RuntimeError(
                f"hook_formulas: regex inválido en la fórmula "
                f"{fid} ({entry.get('name')}): {exc}") from exc
    if not by_id:
        raise RuntimeError(
            f"hook_formulas: {DATA} no trae fórmulas.")
    missing = [fid for fid in order if fid not in by_id]
    extra_ids = [fid for fid in by_id if fid not in order]
    if missing or extra_ids:
        raise RuntimeError(
            "hook_formulas: classify_order no cubre las 26 fórmulas "
            f"(faltan en el orden: {missing}; fuera del orden: "
            f"{extra_ids}).")
    _CACHE = (by_id, order, compiled)
    return _CACHE


def all_formulas():
    """Las 26 fórmulas, en classify_order (la más específica primero)."""
    by_id, order, _compiled = _load_data()
    return [by_id[fid] for fid in order]


def formulas_by_id():
    """Fórmulas por id (para mirar una por nombre sin clasificar)."""
    by_id, _order, _compiled = _load_data()
    return by_id


def classify_hook(text):
    """La fórmula del gancho, o None si ninguna casa.

    Recorre ``classify_order`` en orden; la primera cuyo regex
    (EN verbatim + capa ES + EN_FIX) casa con el texto, gana. Vacío o None
    -> None, sin romper. Devuelve el dict completo de la fórmula
    (``id``, ``name``, ``template``, ``example``, …).
    """
    if not text or not str(text).strip():
        return None
    by_id, order, compiled = _load_data()
    text = str(text)
    for fid in order:
        pattern = compiled.get(fid)
        if pattern is not None and pattern.search(text):
            return by_id[fid]
    return None


def formula_name(text):
    """Nombre de la fórmula del gancho, o ``"unclassified"``."""
    found = classify_hook(text)
    return found["name"] if found else UNCLASSIFIED


def formula_label(text):
    """Etiqueta estilo swipe del README origen: ``"#9 The Steal"``.

    Sin fórmula: ``"unclassified"`` (igual que el swipe, que
    imprime ``-  unclassified``).
    """
    found = classify_hook(text)
    if not found:
        return UNCLASSIFIED
    return f"#{found['id']} {found['name']}"


def main(argv=None):
    """CLI: python -m clipagent.hook_formulas "gancho"."""
    parser = argparse.ArgumentParser(
        description="Nombra la fórmula de un gancho "
                    "(26 fórmulas de hooks.json).")
    parser.add_argument("hook", nargs="?", default=None,
                        help="gancho a clasificar (o - para stdin)")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    if args.hook is None:
        parser.print_help()
        return 2
    if args.hook == "-":
        text = sys.stdin.read().strip()
    else:
        text = args.hook
    found = classify_hook(text)
    if args.json:
        print(json.dumps(found, ensure_ascii=False, indent=2)
              if found else json.dumps({"formula": UNCLASSIFIED},
                                      ensure_ascii=False))
        return 0
    if found:
        print(f"#{found['id']} {found['name']}")
        print(f"  ejemplo: {found.get('example') or ''}")
    else:
        print(UNCLASSIFIED)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

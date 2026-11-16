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

Uso:
    from viral_package import package_clip, viral_multiple
    package_clip(n=1, peak_ts=90, score=22, risas=0, hype=11,
                 median_score=5.0, lang="both", examples=[...])
"""

import statistics

# Fórmulas de hook de yt-script/hooks.json, adaptadas a clips de GTA 6.
# Solo las que tienen sentido para un clip de 60 s sin guion previo.
HOOK_FORMULAS = {
    "the-reveal": "The Reveal — promete algo concreto y lo muestra.",
    "the-superlative": "The Superlative — el mejor/peor/más rápido de su tipo.",
    "the-mistake": "The Mistake — nombra el error que el espectador está cometiendo.",
    "the-statistic": "The Statistic — un número que no sabías, luego la consecuencia.",
    "contrarian-flip": "Contrarian Flip — lo que todos dicen, y por qué está mal.",
}


def viral_multiple(score: int, all_scores) -> float:
    """Múltiplo sobre la mediana del stream (yt-viral)."""
    if not all_scores:
        return 0.0
    median = statistics.median(all_scores)
    if median <= 0:
        return float(score)
    return round(score / median, 2)


def _check_title(title: str) -> list[str]:
    issues = []
    if len(title) > 60:
        issues.append(f"largo ({len(title)} car., se corta en escritorio a los 60)")
    if len(title) > 40:
        issues.append(f"se corta en móvil a los 40 ({len(title)} car.)")
    caps = [w for w in title.split() if w.isupper() and len(w) > 1]
    if len(caps) > 2:
        issues.append(f"{len(caps)} palabras en MAYÚSCULAS, el tope es 2")
    return issues


def package_clip(n, peak_ts, score, risas, hype, median_score, all_scores,
                 lang="both", examples=None):
    """Devuelve el paquete viral completo de un clip."""
    examples = examples or []
    multiple = viral_multiple(score, all_scores)
    is_hype = hype >= risas
    # Fórmula de hook según el tipo de pico (yt-script/hooks.json)
    formula = "the-superlative" if is_hype else "the-reveal"

    if lang == "en":
        titles = [
            "GTA 6: This Play Broke Chat" if is_hype else "GTA 6: Chat Lost It",
            "Nobody Expected This in GTA 6" if is_hype else "GTA 6 Moment That Broke Everyone",
            "GTA 6's Wildest 60 Seconds",
        ]
        thumb = "WHAT A PLAY" if is_hype else "NO WAY"
        hook = "Watch this play — chat went crazy in seconds." if is_hype else "You have to see why chat lost it here."
        onscreen = "WAIT FOR IT" if is_hype else "CHAT REACTION"
        loop = "And that's why everyone clipped it."
        desc = ("The best GTA 6 moment from this stream — the play chat couldn't believe.\n"
                "Full clip, vertical, no context needed.")
        tags = ["GTA 6", "GTA VI", "GTA 6 clips", "GTA 6 gameplay", "Rockstar Games",
                "GTA 6 moments", "gta6", "gaming clips", "twitch clips", "GTA VI gameplay"]
        queries = ["gta 6 best moments", "gta 6 funny clips", "gta 6 gameplay clips"]
    else:
        titles = [
            "GTA 6: La jugada que rompió el chat" if is_hype else "GTA 6: El chat no lo podía creer",
            "Nadie esperaba esto en GTA 6",
            "Los 60 segundos más locos de GTA 6",
        ]
        thumb = "QUÉ JUGADA" if is_hype else "NO PUEDE SER"
        hook = ("Mira esta jugada, el chat explotó en segundos." if is_hype
                else "Mira por qué el chat se murió de risa acá.")
        onscreen = "ESPERA EL FINAL" if is_hype else "LA REACCIÓN"
        loop = "Y por eso todos lo clippearon."
        desc = ("El mejor momento de GTA 6 de este directo — la jugada que el chat no podía creer.\n"
                "Clip completo en vertical, sin contexto necesario.")
        tags = ["GTA 6", "GTA VI", "clips GTA 6", "GTA 6 gameplay", "Rockstar Games",
                "momentos GTA 6", "gta6", "clips gaming", "twitch clips", "gameplay español"]
        queries = ["gta 6 mejores momentos", "gta 6 clips graciosos", "gta 6 gameplay español"]

    # yt-package: escribir 3, quedarse con el que pasa el lint.
    ranked = []
    for t in titles:
        ranked.append({"title": t, "issues": _check_title(t)})
    best = min(ranked, key=lambda x: len(x["issues"]))

    return {
        "n": n,
        "viral_multiple": multiple,  # yt-viral: x veces la mediana del stream
        "formula": formula,
        "formula_desc": HOOK_FORMULAS[formula],
        "titles": ranked,
        "best_title": best["title"],
        "title_issues": best["issues"],
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
    }

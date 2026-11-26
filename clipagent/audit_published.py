"""Auditoría de nuestros clips publicados (ig-audit, adaptado).

Rankea NUESTROS reels publicados por outlier multiple (vistas /
mediana de NUESTRA cuenta) y envíos por alcance, no por views
crudos. Un reel de 4.000 views con 90 envíos le ganó a uno de
60.000 con 11.

Alcance realista v1: YouTube Shorts (view counts públicos vía
yt-dlp, sin login). La mediana es la de NUESTRA cuenta (los
clips publicados); el outlier de cada clip es vistas / mediana.
TikTok/IG/FB: NO se inventan métricas ni se hace scraping (el
repo lo prohíbe y nos puede banear); quedan pendientes de sus
APIs oficiales. Alcance de no-seguidores, hold a 3 s, envíos por
alcance y follows por alcance TAMPOCO están en v1: vienen de
YouTube Studio / Meta Insights, no de yt-dlp; el informe dice
qué falta y no los inventa.

Alcance v2 (adopción 4/5): las métricas que YA existen en D1
(``clip_stats``, escritas por el Worker con las APIs oficiales)
se leen y se usan — retención media (IG/FB), shares, saves,
comentarios y vistas multi-plataforma. Las cuatro métricas
objetivo de ig-audit quedan así (ver ``compute_v2_metrics``):
hold@3s, sends per reach, follows per reach y % no-seguidores
NO tienen fuente automática y se marcan «no disponible» con la
fuente que haría falta; nunca se ponen a cero como si fueran
medidas. El CSV manual acepta esas columnas (datos de YouTube
Studio / Meta Insights pegados a mano) y entonces sí se
calculan. El formato de salida es el swipe file de ig-viral
(ranking por múltiplo, tercio superior/inferior, medianas de
hook score y de palabras del gancho, nº de ``unclassified``).

Solo LECTURA: lee D1 (jobs posted) o un CSV de evidence_url, y
las vistas públicas de YouTube. Cero escrituras, cero
publicaciones. Si no hay credenciales/URLs suficientes, lo dice
y no falla en silencio (código 2).

Distinción que salva meses (ig-audit): un reel con views y sin
follows NO es un reel fallido, es un problema de PERFIL. Un
reel sin views es un problema de HOOK. Con datos de follows
(columna ``follows`` en el CSV) el informe distingue ambos; sin
ellos, el diagnóstico de perfil queda pendiente y se dice.

Uso:
  python -m clipagent.audit_published --tenant sebastian
  python -m clipagent.audit_published --csv runs/mis-shorts.csv
  python -m clipagent.audit_published --tenant sebastian \\
      --database-id <d1-id> --out-dir runs/audit-published-2026-11-21
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import re
import statistics
import subprocess
import sys
from datetime import datetime, timezone

from .d1_client import D1Client
from .hook_formulas import UNCLASSIFIED, formula_label, formula_name
from .hookscore import score_hook
from .youtube import (
    MAX_ATTEMPTS,
    YTDLP_BASE_OPTS,
    _run_with_retries,
    _ytdlp_cmd,
)

# Mínimo de clips con vistas para que la mediana sea creíble.
MIN_MEDIAN_N = 3
# Outlier >= 3x = hit; < 1x = por debajo de su día normal.
HIT_OUTLIER = 3.0

# Claves de plataforma del panel -> claves API de clip_stats
# (las que usa el Worker en panel-backend/worker/src/publish/
# stats.ts). Sin este mapeo el join con clip_stats no casa.
PANEL_TO_API_PLATFORM = {
    "youtube_shorts": "youtube",
    "tiktok": "tiktok",
    "instagram_reels": "instagram",
    "facebook_reels": "facebook",
}

# Palabras del gancho (título), como WORD_RE de swipe.py del
# origen, ampliado a acentos españoles.
WORD_RE = re.compile(r"[A-Za-z0-9À-ÿ$%'’-]+")

# Fuentes de las métricas v2, para el informe de disponibilidad.
# «no disponible» nombra la fuente que haría falta; nunca se
# inventa el dato.
V2_SOURCES = {
    "retention_pct": "clip_stats (Worker, IG/FB)",
    "shares": "clip_stats (Worker, IG/FB/TikTok)",
    "sends_per_reach": "no disponible — falta reach: clip_stats "
                       "no tiene columna reach (IG lo pide y lo "
                       "descarta); YT/FB/TikTok ni lo piden",
    "follows_per_reach": "no disponible — new_subs siempre NULL "
                         "en clip_stats y falta reach",
    "hold_3s": "no disponible — sin fuente: YT Analytics v2 "
               "(curva de retención) o reels_skip_rate de Meta "
               "(se pide pero no se guarda; definición sin "
               "verificar)",
    "non_follower_pct": "no disponible — sin desglose por "
                        "seguidor en ninguna API; no hay columna",
}

# Fuente real cuando la métrica SÍ tiene dato (el reach y el
# hold solo pueden venir del CSV manual: ninguna fuente
# automática los guarda).
V2_AVAILABLE_SOURCES = {
    "retention_pct": "clip_stats (Worker, IG/FB) o CSV manual",
    "shares": "clip_stats (Worker, IG/FB/TikTok) o CSV manual",
    "sends_per_reach": "CSV manual (shares + reach)",
    "follows_per_reach": "CSV manual (follows + reach)",
    "hold_3s": "CSV manual (YouTube Studio / Meta Insights)",
    "non_follower_pct": "CSV manual (Meta Insights)",
}


def _to_int(value):
    """Entero o None (vacío/inválido no es 0 medido)."""
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _to_float(value):
    """Float o None (vacío/inválido no es 0 medido)."""
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def fetch_youtube_stats(url, runner=None, sleep=None):
    """Vistas/duración/título públicos de un Short (yt-dlp -J).

    Devuelve el dict o None si no se pudo leer (nunca levanta:
    un vídeo sin stats se marca sin-datos, no tumba la auditoría).
    Reintenta ante bot-check (igual que la fuente viral).
    """
    cmd = (_ytdlp_cmd() + YTDLP_BASE_OPTS
           + ["--skip-download", "-J", "--no-playlist", url])
    run = runner or _default_runner
    try:
        proc = _run_with_retries(cmd, run, sleep, retries=MAX_ATTEMPTS)
    except Exception:  # noqa: BLE001 - sin stats no hay clip
        return None
    if getattr(proc, "returncode", 1) != 0:
        return None
    try:
        data = json.loads(proc.stdout or "")
    except (ValueError, TypeError):
        return None
    if not isinstance(data, dict):
        return None
    try:
        views = int(data.get("view_count") or 0)
    except (TypeError, ValueError):
        views = 0
    try:
        duration = int(data.get("duration") or 0)
    except (TypeError, ValueError):
        duration = 0
    return {
        "views": views,
        "duration_s": duration,
        "title": str(data.get("title") or ""),
    }


def _default_runner(cmd):
    return subprocess.run(cmd, capture_output=True, text=True,
                          timeout=120)


def load_posted_from_d1(client, platform="youtube_shorts",
                        limit=100):
    """Jobs posted de D1 (solo lectura): url, título, plataforma.

    La base D1 ES el ámbito del tenant (jobs no tiene columna
    tenant): la base vieja mezcla sebastian y kaez y el informe
    lo dice. Sin cliente (sin token) devuelve None.
    """
    if client is None:
        return None
    sql = (
        "SELECT j.evidence_url AS url, j.title AS title, "
        "j.platform AS platform, j.posted_day AS posted_day, "
        "j.clip_id AS clip_id, c.title AS clip_title "
        "FROM jobs j LEFT JOIN clips c ON c.id = j.clip_id "
        "WHERE j.state = 'posted' AND j.platform = ? "
        "AND j.evidence_url IS NOT NULL AND j.evidence_url != '' "
        "ORDER BY j.posted_day DESC LIMIT ?"
    )
    rows = client.query(sql, [platform, int(limit)])
    out = []
    for row in rows or []:
        url = str(row.get("url") or "").strip()
        if not url:
            continue
        title = (str(row.get("title") or "").strip()
                 or str(row.get("clip_title") or "").strip())
        out.append({
            "url": url,
            "title": title,
            "platform": str(row.get("platform") or platform),
            "posted_day": str(row.get("posted_day") or ""),
            "clip_id": row.get("clip_id"),
            "follows": None,
            # Métricas v2: las rellena clip_stats (v2) o el CSV;
            # None = sin medir, nunca 0.
            "views": None,
            "retention_pct": None,
            "shares": None,
            "saves": None,
            "comments": None,
            "reach": None,
            "hold_3s": None,
            "non_follower_pct": None,
        })
    return out


def load_stats_from_d1(client, platform="youtube_shorts"):
    """Última medición de clip_stats por clip (solo lectura).

    Devuelve {clip_id: {views, retention_pct, shares, saves,
    comments, new_subs, recorded_at}} o {} si no hay cliente o
    no hay filas. La última fila por clip es la medición más
    reciente (recorded_at DESC, id DESC, igual que
    listClipStats del Worker). Sin filtro de tenant: la base
    vieja mezcla tenants (mismo caveat que load_posted_from_d1)
    y el join es por clip_id + plataforma. Si la tabla no
    existe o la consulta falla, devuelve {} y la auditoría
    sigue con yt-dlp + CSV (nunca tumba el informe).
    """
    if client is None:
        return {}
    api_platform = PANEL_TO_API_PLATFORM.get(platform, platform)
    sql = (
        "SELECT clip_id, views, retention_pct, shares, saves, "
        "comments, new_subs, recorded_at FROM clip_stats "
        "WHERE platform = ? "
        "ORDER BY recorded_at DESC, id DESC"
    )
    try:
        rows = client.query(sql, [api_platform])
    except Exception:  # noqa: BLE001 - sin stats no hay v2
        return {}
    out = {}
    for row in rows or []:
        clip_id = row.get("clip_id")
        if clip_id is None or clip_id in out:
            continue
        out[clip_id] = {
            "views": _to_int(row.get("views")),
            "retention_pct": _to_float(row.get("retention_pct")),
            "shares": _to_int(row.get("shares")),
            "saves": _to_int(row.get("saves")),
            "comments": _to_int(row.get("comments")),
            "new_subs": _to_int(row.get("new_subs")),
            "recorded_at": _to_int(row.get("recorded_at")),
        }
    return out


def load_from_csv(path):
    """Clips desde un CSV: url, title, platform, posted_day, follows.

    Columnas opcionales salvo ``url``. ``follows`` (entero) es el
    dato de YouTube Studio para el diagnóstico de perfil.
    Columnas v2 (opcionales, datos pegados a mano de YouTube
    Studio / Meta Insights): ``views``, ``retention_pct``,
    ``shares``, ``saves``, ``comments``, ``reach``, ``hold_3s``
    (tanto por uno, 0-1, o porcentaje 0-100) y
    ``non_follower_pct`` (porcentaje 0-100). Una columna ausente
    o inválida es None (sin medir), nunca 0.
    """
    out = []
    with open(path, encoding="utf-8", newline="") as f:
        for row in csv.DictReader(f):
            url = str((row or {}).get("url") or "").strip()
            if not url:
                continue
            hold = _to_float((row or {}).get("hold_3s"))
            if hold is not None and hold > 1:
                # Porcentaje 0-100 -> tanto por uno.
                hold = hold / 100.0
            out.append({
                "url": url,
                "title": str((row or {}).get("title") or "").strip(),
                "platform": str((row or {}).get("platform") or
                                "youtube_shorts").strip(),
                "posted_day": str((row or {}).get("posted_day")
                                  or "").strip(),
                "clip_id": (row or {}).get("clip_id"),
                "follows": _to_int((row or {}).get("follows")),
                "views": _to_int((row or {}).get("views")),
                "retention_pct": _to_float(
                    (row or {}).get("retention_pct")),
                "shares": _to_int((row or {}).get("shares")),
                "saves": _to_int((row or {}).get("saves")),
                "comments": _to_int((row or {}).get("comments")),
                "reach": _to_int((row or {}).get("reach")),
                "hold_3s": hold,
                "non_follower_pct": _to_float(
                    (row or {}).get("non_follower_pct")),
            })
    return out


def compute_outliers(clips):
    """Mediana de NUESTRA cuenta y outlier por clip.

    La mediana sale de las vistas de los clips publicados (con
    vistas > 0); sin al menos MIN_MEDIAN_N no es creíble y queda
    None (no se inventa). Devuelve (median, clips) con cada clip
    anotado (``outlier`` float o None).
    """
    views = []
    for clip in clips or []:
        try:
            v = int(clip.get("views") or 0)
        except (TypeError, ValueError):
            v = 0
        if v > 0:
            views.append(v)
    median = None
    if len(views) >= MIN_MEDIAN_N:
        median = int(statistics.median(views))
    for clip in clips or []:
        try:
            v = int(clip.get("views") or 0)
        except (TypeError, ValueError):
            v = 0
        if median and v > 0:
            clip["outlier"] = round(v / median, 2)
        else:
            clip["outlier"] = None
    return median, clips


def diagnose(clip):
    """Hook o perfil, según lo que dicen los datos (ig-audit).

    - Sin views/mediana: "sin datos".
    - Outlier < 1: problema de HOOK (pocas views: el hook no
      retuvo; todo lo demás es distracción).
    - Outlier >= 3 con follows = 0 (y dato de follows presente):
      problema de PERFIL (views sin follows: el perfil no
      convierte; deja de reescribir hooks y ve al perfil).
    - Outlier >= 3: hit. El resto: normal.
    """
    outlier = clip.get("outlier")
    if outlier is None:
        return "sin datos"
    if outlier < 1.0:
        return "problema de hook"
    follows = clip.get("follows")
    if outlier >= HIT_OUTLIER and follows is not None and follows == 0:
        return "problema de perfil"
    if outlier >= HIT_OUTLIER:
        return "hit"
    return "normal"


def _formula_counts(group):
    """Conteos de fórmulas de gancho en un grupo de clips.

    Por título (la misma fuente que el hook score). Devuelve un
    dict {fórmula: n} ordenado por nº desc y luego por nombre.
    """
    counts = {}
    for clip in group or []:
        name = formula_name(str(clip.get("title") or ""))
        counts[name] = counts.get(name, 0) + 1
    return dict(sorted(counts.items(),
                       key=lambda kv: (-kv[1], kv[0])))


def compute_v2_metrics(clip):
    """Métricas v2 de ig-audit para un clip (función pura).

    Entrada: campos crudos del clip (de ``clip_stats`` vía D1 o
    del CSV manual). Salida: dict con las cuatro métricas
    objetivo y los campos crudos. REGLAS HONESTAS:

    - Un ratio solo existe si existen sus dos lados: sin
      ``reach`` no hay sends/reach ni follows/reach; sin
      ``hold_3s`` no hay hold@3s. Lo que falta es ``None``,
      nunca 0.
    - ``shares_per_view`` es un ratio DISTINTO de sends/reach
      (shares / views, ambos reales cuando existen): se
      calcula y se etiqueta con su nombre propio; no sustituye
      a sends/reach.
    - ``retention_pct`` es retención MEDIA (IG: tiempo medio /
      duración; FB: completas / vistas), NO hold@3s: se
      devuelve tal cual y el informe la etiqueta como tal.
    - ``follows`` solo existe si viene del CSV manual o de
      ``new_subs`` de clip_stats (hoy siempre NULL en el
      Worker: ninguna API lo da por medio).
    """
    shares = _to_int(clip.get("shares"))
    views = _to_int(clip.get("views"))
    reach = _to_int(clip.get("reach"))
    follows = _to_int(clip.get("follows"))
    if follows is None:
        follows = _to_int(clip.get("new_subs"))
    hold = _to_float(clip.get("hold_3s"))
    if hold is not None and hold > 1:
        hold = hold / 100.0
    sends_per_reach = None
    if shares is not None and reach:
        sends_per_reach = round(shares / reach, 4)
    follows_per_reach = None
    if follows is not None and reach:
        follows_per_reach = round(follows / reach, 4)
    shares_per_view = None
    if shares is not None and views:
        shares_per_view = round(shares / views, 4)
    return {
        "retention_pct": _to_float(clip.get("retention_pct")),
        "shares": shares,
        "saves": _to_int(clip.get("saves")),
        "comments": _to_int(clip.get("comments")),
        "reach": reach,
        "hold_3s": hold,
        "non_follower_pct": _to_float(clip.get("non_follower_pct")),
        "sends_per_reach": sends_per_reach,
        "follows_per_reach": follows_per_reach,
        "shares_per_view": shares_per_view,
    }


def _median_hook_score(clips):
    """Hook score MEDIANO de un grupo (formato swipe file).

    swipe.py usa la mediana, no la media: un título extremo no
    arrastra el grupo. v1 usaba la media; v2 usa la mediana.
    """
    scores = []
    for clip in clips or []:
        title = str(clip.get("title") or "").strip()
        if not title:
            continue
        score, _weakest, _flag = score_hook(title)
        scores.append(score)
    if not scores:
        return None
    return round(statistics.median(scores), 1)


def _median_hook_words(clips):
    """Mediana de palabras del gancho (título) de un grupo.

    Como ``med(top, "words")`` de swipe.py: la longitud del
    gancho en palabras, en mediana.
    """
    words = []
    for clip in clips or []:
        title = str(clip.get("title") or "").strip()
        if not title:
            continue
        words.append(len(WORD_RE.findall(title)))
    if not words:
        return None
    return round(statistics.median(words), 1)


def build_report(clips, median, tenant, platform):
    """Informe: ranking por outlier + lectura (formato swipe file).

    Como ``swipe.py`` del origen: las medianas (hook score,
    palabras del gancho, duración) usan el TERCIO superior vs el
    tercio inferior por outlier (``third = max(1, n // 3)``);
    los conteos de fórmulas ``formula_top``/``formula_bottom``
    siguen sobre el top 5 / bottom 5 como antes de v2 (incluido
    «unclassified») y ``top_formulas`` lista las del tercio
    superior. El top 5 / bottom 5 se mantiene en el JSON por
    compatibilidad.
    La lectura compara qué separa el tercio de arriba del de
    abajo (hook score mediano, duración media, longitud del
    título, retención media y shares por vista cuando hay dato)
    y lo dice con su confianza: con pocos clips no se puede ver
    un patrón, y decirlo es mejor que inventarlo. Las métricas
    v2 sin fuente quedan como «no disponible» (ver ``v2``).
    """
    ranked = sorted(
        [c for c in clips or [] if c.get("outlier") is not None],
        key=lambda c: (-c["outlier"], c.get("title") or ""))
    no_data = [c for c in clips or []
               if c.get("outlier") is None]
    for clip in ranked:
        clip["v2"] = compute_v2_metrics(clip)
    top = ranked[:5]
    bottom = ranked[-5:][::-1] if ranked else []
    third = max(1, len(ranked) // 3)
    top_third = ranked[:third]
    bottom_third = ranked[-third:][::-1] if ranked else []
    top_hook = _median_hook_score(top_third)
    bottom_hook = _median_hook_score(bottom_third)
    top_words = _median_hook_words(top_third)
    bottom_words = _median_hook_words(bottom_third)
    formula_top = _formula_counts(top)
    formula_bottom = _formula_counts(bottom)
    # top_formulas es la lista swipe v2: fórmulas del tercio
    # superior. formula_top/bottom, en cambio, siguen contando
    # sobre top 5 / bottom 5 como antes de v2 (incluido
    # «unclassified»): la lectura «aparece arriba y no abajo»
    # los compara y el test preexistente los fija así.
    top_formulas = sorted(_formula_counts(top_third).items(),
                          key=lambda kv: (-kv[1], kv[0]))
    n_unclassified = sum(
        1 for c in ranked
        if formula_name(str(c.get("title") or "")) == UNCLASSIFIED)

    def _median(values):
        vals = [v for v in values if v is not None]
        return round(statistics.median(vals), 1) if vals else None

    def _median_pct(values):
        # Ratios (tanto por uno): 4 decimales, para no perder la
        # mediana redondeando a 2 (0,018 -> 0,02 falsearía el dato).
        vals = [v for v in values if v is not None]
        return round(statistics.median(vals), 4) if vals else None

    # Disponibilidad v2: cuántos clips rankeados tienen cada
    # métrica con fuente real (n con dato / n total).
    v2_keys = ["retention_pct", "shares", "sends_per_reach",
               "follows_per_reach", "hold_3s", "non_follower_pct"]
    v2_avail = {}
    for key in v2_keys:
        n = sum(1 for c in ranked
                if c.get("v2", {}).get(key) is not None)
        v2_avail[key] = {"n": n, "total": len(ranked),
                         "source": V2_AVAILABLE_SOURCES[key]
                         if n > 0 else V2_SOURCES[key]}
    v2_median_retention = _median(
        [c["v2"]["retention_pct"] for c in ranked])
    v2_median_spv = _median_pct(
        [c["v2"]["shares_per_view"] for c in ranked])

    def _avg_duration(group):
        durs = [int(c.get("duration_s") or 0) for c in group
                if int(c.get("duration_s") or 0) > 0]
        return round(sum(durs) / len(durs), 1) if durs else None

    def _avg_title_len(group):
        lens = [len(str(c.get("title") or "")) for c in group
                if str(c.get("title") or "")]
        return round(sum(lens) / len(lens), 1) if lens else None

    has_follows = any(c.get("follows") is not None
                      for c in clips or [])
    reading = []
    if median is None:
        reading.append(
            "Sin mediana creíble (menos de "
            f"{MIN_MEDIAN_N} clips con vistas): el outlier no se "
            "puede calcular y el ranking es por views crudos, que "
            "no son evidencia. Publica más y vuelve a correr.")
    elif len(ranked) >= 6:
        if top_hook is not None and bottom_hook is not None:
            if top_hook - bottom_hook >= 10:
                reading.append(
                    f"El hook score (mediana) separa el tercio "
                    f"de arriba del de abajo "
                    f"({top_hook} vs {bottom_hook}): los títulos "
                    "del top puntúan mejor. El hook es la historia.")
            elif bottom_hook - top_hook >= 10:
                reading.append(
                    f"El hook score (mediana) NO separa "
                    f"(top {top_hook} vs bottom {bottom_hook}): "
                    "el título no es lo que decide aquí; mira "
                    "duración y tema.")
            else:
                reading.append(
                    f"Hook score mediano parecido arriba y abajo "
                    f"({top_hook} vs {bottom_hook}): el título no "
                    "explica la diferencia; mira duración y tema.")
        if top_words is not None and bottom_words is not None:
            if abs(top_words - bottom_words) >= 3:
                reading.append(
                    f"Longitud del gancho (mediana): arriba "
                    f"{top_words} palabras vs abajo "
                    f"{bottom_words} palabras.")
        top_dur = _avg_duration(top_third)
        bottom_dur = _avg_duration(bottom_third)
        if top_dur is not None and bottom_dur is not None:
            if abs(top_dur - bottom_dur) >= 10:
                reading.append(
                    f"Duración media: tercio de arriba {top_dur}s "
                    f"vs tercio de abajo {bottom_dur}s.")
        top_len = _avg_title_len(top_third)
        bottom_len = _avg_title_len(bottom_third)
        if top_len is not None and bottom_len is not None:
            if abs(top_len - bottom_len) >= 15:
                reading.append(
                    f"Longitud media del título: arriba {top_len} "
                    f"car. vs abajo {bottom_len} car.")
        # Retención media y shares por vista, si hay dato.
        top_ret = _median([c["v2"]["retention_pct"]
                           for c in top_third])
        bottom_ret = _median([c["v2"]["retention_pct"]
                              for c in bottom_third])
        if (top_ret is not None and bottom_ret is not None
                and abs(top_ret - bottom_ret) >= 10):
            reading.append(
                f"Retención media (mediana): arriba {top_ret}% vs "
                f"abajo {bottom_ret}% — es retención media, no "
                "hold@3s.")
        top_spv = _median_pct([c["v2"]["shares_per_view"]
                               for c in top_third])
        bottom_spv = _median_pct([c["v2"]["shares_per_view"]
                                  for c in bottom_third])
        if (top_spv is not None and bottom_spv is not None
                and top_spv > 0 and bottom_spv > 0
                and (top_spv / bottom_spv >= 2
                     or bottom_spv / top_spv >= 2)):
            reading.append(
                f"Shares por vista (mediana): arriba {top_spv} vs "
                f"abajo {bottom_spv} — ratio honesto, no es "
                "sends/reach.")
        if not reading:
            reading.append(
                "Tercio de arriba y de abajo no se separan en "
                "hook, duración ni título con estos datos: mira "
                "el tema y el streamer de cada clip, no las medias.")
    else:
        reading.append(
            f"Solo {len(ranked)} clips con outlier: con 6 no se ve "
            "un patrón, y decirlo es mejor que inventarlo.")

    # Qué fórmula funcionó (descriptivo, no una puerta: la fórmula
    # nombra el gancho, no elige ganadores — ver hook_formulas.py).
    if ranked:
        top_only = [name for name, n in formula_top.items()
                    if n >= 2 and name not in formula_bottom
                    and name != UNCLASSIFIED]
        for name in top_only:
            reading.append(
                f"La fórmula {name} aparece arriba "
                f"({formula_top[name]}) y no abajo.")
        if n_unclassified > len(ranked) / 2:
            reading.append(
                f"Más de la mitad sin fórmula ({n_unclassified} de "
                f"{len(ranked)}): la mayoría de títulos no sigue "
                "ninguna de las 26 fórmulas (o son títulos nuestros, "
                "no ganchos hablados).")

    def _row(clip):
        v2 = clip.get("v2") or compute_v2_metrics(clip)
        title = str(clip.get("title") or "")
        hook_score = None
        hook_words = None
        if title.strip():
            hook_score, _w, _f = score_hook(title)
            hook_score = round(hook_score, 1)
            hook_words = len(WORD_RE.findall(title))
        return {
            "title": clip.get("title") or "",
            "url": clip.get("url") or "",
            "views": clip.get("views"),
            "outlier": clip.get("outlier"),
            "duration_s": clip.get("duration_s"),
            "posted_day": clip.get("posted_day") or "",
            "diagnosis": diagnose(clip),
            "hook_formula": formula_name(title),
            "hook_formula_label": formula_label(title),
            "hook_score": hook_score,
            "hook_words": hook_words,
            "retention_pct": v2.get("retention_pct"),
            "shares": v2.get("shares"),
            "sends_per_reach": v2.get("sends_per_reach"),
            "follows_per_reach": v2.get("follows_per_reach"),
            "hold_3s": v2.get("hold_3s"),
            "non_follower_pct": v2.get("non_follower_pct"),
            "shares_per_view": v2.get("shares_per_view"),
        }

    gaps = [
        "TikTok/IG/FB: vistas y métricas v2 solo si el Worker "
        "las midió en clip_stats (collectAnalytics, como mucho "
        "1/día por clip y plataforma); sin medición, el clip "
        "queda sin datos — no se inventan ni se hace scraping.",
        ("Diagnóstico de perfil pendiente: sin datos de "
         "follows (columna 'follows' en el CSV o YouTube "
         "Studio).") if not has_follows else
        "Diagnóstico de perfil activo (hay datos de follows).",
    ]
    for key in v2_keys:
        if v2_avail[key]["n"] == 0:
            gaps.append(f"{key}: {V2_SOURCES[key]}.")
    return {
        "tenant": tenant,
        "platform": platform,
        "median_views": median,
        "n_clips": len(clips or []),
        "n_ranked": len(ranked),
        "n_sin_datos": len(no_data),
        "top5": [_row(c) for c in top],
        "bottom5": [_row(c) for c in bottom],
        "ranked": [_row(c) for c in ranked],
        "top_third": [_row(c) for c in top_third],
        "bottom_third": [_row(c) for c in bottom_third],
        "n_third": third,
        # v1 los llamaba medias; v2 son medianas (swipe file).
        "hook_score_top": top_hook,
        "hook_score_bottom": bottom_hook,
        "median_hook_score_top": top_hook,
        "median_hook_score_bottom": bottom_hook,
        "median_hook_words_top": top_words,
        "median_hook_words_bottom": bottom_words,
        "formula_top": formula_top,
        "formula_bottom": formula_bottom,
        "top_formulas": [[name, n] for name, n in top_formulas],
        "n_unclassified": n_unclassified,
        "reading": reading,
        "has_follows_data": has_follows,
        "v2": v2_avail,
        "v2_median_retention_pct": v2_median_retention,
        "v2_median_shares_per_view": v2_median_spv,
        "gaps": gaps,
    }


def render_text(report):
    """Informe legible, formato swipe file (ig-viral/swipe.py).

    Ranking completo por múltiplo (no por views crudos), tercio
    superior/inferior, medianas de hook score y de palabras del
    gancho, fórmulas del tercio superior y nº de unclassified.
    Las métricas v2 sin fuente se imprimen como «no disponible»
    con la fuente que haría falta; nunca como 0.
    """
    n = report["n_ranked"]
    median = report["median_views"]
    median_txt = f"{median:,}" if median is not None else "sin mediana"
    head = (f"AUDITORÍA SWIPE  ·  tenant {report['tenant']}  ·  "
            f"{report['platform']}  ·  {n} clips rankeados de "
            f"{report['n_clips']}  ·  mediana {median_txt} views")
    lines = ["", head, "=" * max(len(head), 78), "",
             "RANKING POR OUTLIER (no por views crudos)"]
    for row in report["ranked"]:
        mult = f"{row['outlier']:.1f}x" if row["outlier"] else "   ?"
        score = (f"{row['hook_score']:.0f}"
                 if row.get("hook_score") is not None else " -")
        label = row.get("hook_formula_label") or UNCLASSIFIED
        views = (f"{row['views']:,}" if row.get("views") is not None
                 else "?")
        lines.append(
            f"  {mult:>7}  hook {score:>3}  {label[:26]:<26} "
            f"{views:>9} views  {row['title'][:64]}  "
            f"[{row['diagnosis']}]  "
            f"({row.get('hook_formula') or UNCLASSIFIED})")
    lines.append("-" * max(len(head), 78))
    lines.append("WHAT IS WORKING IN THIS BATCH")
    if report["top_formulas"]:
        lines.append(
            "  top third by outlier:  " + ", ".join(
                f"{name} x{count}"
                for name, count in report["top_formulas"][:4]))
    top_h = report["median_hook_score_top"]
    bottom_h = report["median_hook_score_bottom"]
    if top_h is not None and bottom_h is not None:
        lines.append(
            f"  median hook score:     top {top_h:.0f}  "
            f"vs bottom {bottom_h:.0f}")
    top_w = report["median_hook_words_top"]
    bottom_w = report["median_hook_words_bottom"]
    if top_w is not None and bottom_w is not None:
        lines.append(
            f"  median hook length:    top {top_w:.0f} words  "
            f"vs bottom {bottom_w:.0f} words")
    lines.append(
        f"  unclassified:          {report['n_unclassified']} of "
        f"{n}. Leerlos a mano: ahí se esconde una fórmula que aún "
        "no tenemos.")
    lines.append("")
    lines.append("MÉTRICAS V2 (fuente real o no disponible — nunca "
                 "inventadas)")
    v2 = report["v2"]
    for key in ("retention_pct", "shares", "sends_per_reach",
                "follows_per_reach", "hold_3s", "non_follower_pct"):
        entry = v2[key]
        if entry["n"] > 0:
            lines.append(
                f"  {key}: {entry['n']} de {entry['total']} con "
                f"dato ({entry['source']})")
        else:
            lines.append(f"  {key}: no disponible — {entry['source']}")
    if report["v2_median_retention_pct"] is not None:
        lines.append(
            "  retención media (mediana): "
            f"{report['v2_median_retention_pct']}% — es retención "
            "media, no hold@3s.")
    if report["v2_median_shares_per_view"] is not None:
        lines.append(
            "  shares por vista (mediana): "
            f"{report['v2_median_shares_per_view']:.2%} — ratio "
            "honesto, no es sends/reach.")
    if report["n_sin_datos"]:
        lines.append("")
        lines.append(f"Sin datos: {report['n_sin_datos']} clip(s) "
                     "(sin vistas o sin mediana).")
    # Sección FÓRMULAS (v1): conteos arriba/abajo y nº sin
    # clasificar. Solo con clips rankeados; sin ellos no hay
    # sección (lo fija test_sin_clips_ranked_no_hay_seccion...).
    if report["n_ranked"]:
        def _fmt(counts):
            return ", ".join(f"{name} ({n})"
                             for name, n in counts.items()) or "—"
        lines.append("")
        lines.append("FÓRMULAS")
        lines.append(f"  arriba: {_fmt(report['formula_top'])}")
        lines.append(f"  abajo: {_fmt(report['formula_bottom'])}")
        lines.append(f"  sin clasificar: {report['n_unclassified']} "
                     f"de {report['n_ranked']}")
    lines.append("")
    lines.append("LECTURA")
    for item in report["reading"]:
        lines.append(f"- {item}")
    lines.append("")
    lines.append("HUECOS")
    for gap in report["gaps"]:
        lines.append(f"- {gap}")
    return "\n".join(lines)


def run_audit(tenant, platform="youtube_shorts", csv_path=None,
              database_id=None, limit=100, runner=None):
    """Una auditoría: D1 o CSV -> vistas -> outlier -> informe.

    Vistas: yt-dlp fresco para YouTube (v1); si no se puede leer
    el vídeo, valen las vistas del CSV manual o de clip_stats
    (D1, medidas por el Worker). Métricas v2: de clip_stats (D1)
    o del CSV manual; lo que no tiene fuente queda None («no
    disponible»), nunca 0.

    Devuelve (report, motivo). Sin D1 ni CSV, o sin clips, el
    motivo lo dice y el report es None (no se falla en silencio).
    """
    clips = None
    source = ""
    stats_by_clip = {}
    stats_note = ""
    if csv_path:
        try:
            clips = load_from_csv(csv_path)
        except OSError as exc:
            return None, f"no se pudo leer el CSV {csv_path}: {exc}"
        source = f"CSV {csv_path}"
    else:
        client = D1Client(database_id=database_id) \
            if database_id else D1Client.from_env()
        if client is None:
            return None, (
                "sin token de Cloudflare (CLOUDFLARE_API_TOKEN) y "
                "sin --csv: la auditoría no tiene de dónde leer "
                "los jobs posted. Pasa --csv con las evidence_url "
                "o define el token.")
        try:
            clips = load_posted_from_d1(
                client, platform=platform, limit=limit)
        except Exception as exc:  # noqa: BLE001 - D1 caído: se dice
            return None, f"D1 no respondió: {exc}"
        source = f"D1 (base {client.database_id})"
        # Métricas v2 reales (solo lectura): última medición de
        # clip_stats por clip. Si no hay, la auditoría sigue con
        # yt-dlp y lo dice en el informe.
        stats_by_clip = load_stats_from_d1(client, platform=platform)
        if stats_by_clip:
            stats_note = (f"; clip_stats: {len(stats_by_clip)} "
                          "clip(s) con medición del Worker")
        else:
            stats_note = ("; sin mediciones en clip_stats "
                          "(el Worker aún no midió o la tabla "
                          "está vacía)")
    if not clips:
        return None, (f"sin clips posted en {source} "
                      f"(plataforma {platform}): nada que auditar.")
    for clip in clips:
        stats = fetch_youtube_stats(clip["url"], runner=runner)
        if stats is not None:
            clip["views"] = stats["views"]
            clip["duration_s"] = stats["duration_s"]
            if not clip.get("title"):
                clip["title"] = stats["title"]
        # Métricas v2 desde clip_stats (solo rellenan lo que el
        # clip no trae ya del CSV manual, que manda).
        d1stats = stats_by_clip.get(clip.get("clip_id")) \
            if clip.get("clip_id") is not None else None
        if d1stats:
            for key in ("retention_pct", "shares", "saves",
                        "comments"):
                if clip.get(key) is None:
                    clip[key] = d1stats.get(key)
            if clip.get("views") is None:
                clip["views"] = d1stats.get("views")
            if clip.get("follows") is None:
                clip["follows"] = d1stats.get("new_subs")
    median, clips = compute_outliers(clips)
    report = build_report(clips, median, tenant, platform)
    report["source"] = source + stats_note
    report["stats_source"] = stats_note.strip("; ")
    return report, ""


def main(argv=None):
    """CLI: python -m clipagent.audit_published --tenant sebastian."""
    parser = argparse.ArgumentParser(
        description="Auditoría de nuestros clips publicados "
                    "(outlier multiple, no views crudos).")
    parser.add_argument("--tenant", required=True,
                        help="tenant (sebastian, kaez)")
    parser.add_argument("--platform", default="youtube_shorts",
                        help="plataforma a auditar "
                             "(por defecto youtube_shorts)")
    parser.add_argument("--csv", default=None,
                        help="CSV con url,title,platform,posted_day,"
                             "follows (en vez de D1)")
    parser.add_argument("--database-id", default=None,
                        help="id de la base D1 (por defecto la de "
                             "D1_DATABASE_ID o la vieja)")
    parser.add_argument("--limit", type=int, default=100,
                        help="máximo de jobs posted a leer de D1")
    parser.add_argument("--out-dir", default=None,
                        help="directorio del informe (por defecto "
                             "runs/audit-published-<fecha>)")
    args = parser.parse_args(argv)

    report, motivo = run_audit(
        args.tenant, platform=args.platform, csv_path=args.csv,
        database_id=args.database_id, limit=args.limit)
    if report is None:
        print(f"[audit-published] {motivo}", file=sys.stderr)
        return 2
    text = render_text(report)
    print(text)
    out_dir = args.out_dir or os.path.join(
        "runs", "audit-published-" + datetime.now(
            timezone.utc).strftime("%Y-%m-%d"))
    os.makedirs(out_dir, exist_ok=True)
    with open(os.path.join(out_dir, "report.json"), "w",
              encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=1,
                  default=str)
    with open(os.path.join(out_dir, "report.md"), "w",
              encoding="utf-8") as f:
        f.write("# Auditoría de clips publicados\n\n")
        f.write(f"Tenant: {report['tenant']}  ·  "
                f"Plataforma: {report['platform']}  ·  "
                f"Fuente: {report['source']}\n\n")
        f.write("```\n" + text + "\n```\n")
    print(f"\n[audit-published] informe en {out_dir}/report.json "
          "y report.md")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

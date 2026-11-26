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
import statistics
import subprocess
import sys
from datetime import datetime, timezone

from .d1_client import D1Client
from .hook_formulas import UNCLASSIFIED, formula_name
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
        })
    return out


def load_from_csv(path):
    """Clips desde un CSV: url, title, platform, posted_day, follows.

    Columnas opcionales salvo ``url``. ``follows`` (entero) es el
    dato de YouTube Studio para el diagnóstico de perfil.
    """
    out = []
    with open(path, encoding="utf-8", newline="") as f:
        for row in csv.DictReader(f):
            url = str((row or {}).get("url") or "").strip()
            if not url:
                continue
            follows = (row or {}).get("follows")
            try:
                follows = int(follows) if follows not in (
                    None, "") else None
            except (TypeError, ValueError):
                follows = None
            out.append({
                "url": url,
                "title": str((row or {}).get("title") or "").strip(),
                "platform": str((row or {}).get("platform") or
                                "youtube_shorts").strip(),
                "posted_day": str((row or {}).get("posted_day")
                                  or "").strip(),
                "clip_id": (row or {}).get("clip_id"),
                "follows": follows,
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


def _avg_hook_score(clips):
    """Hook score medio de un grupo de clips (por su título)."""
    scores = []
    for clip in clips or []:
        title = str(clip.get("title") or "").strip()
        if not title:
            continue
        score, _weakest, _flag = score_hook(title)
        scores.append(score)
    if not scores:
        return None
    return round(sum(scores) / len(scores), 1)


def build_report(clips, median, tenant, platform):
    """Informe: top 5 / bottom 5 por outlier + lectura.

    La lectura compara qué separa el top del bottom (hook score
    medio, duración media, longitud del título) y lo dice con su
    confianza: con pocos clips no se puede ver un patrón, y
    decirlo es mejor que inventarlo.
    """
    ranked = sorted(
        [c for c in clips or [] if c.get("outlier") is not None],
        key=lambda c: (-c["outlier"], c.get("title") or ""))
    no_data = [c for c in clips or []
               if c.get("outlier") is None]
    top = ranked[:5]
    bottom = ranked[-5:][::-1] if ranked else []
    top_hook = _avg_hook_score(top)
    bottom_hook = _avg_hook_score(bottom)
    formula_top = _formula_counts(top)
    formula_bottom = _formula_counts(bottom)
    n_unclassified = sum(
        1 for c in ranked
        if formula_name(str(c.get("title") or "")) == UNCLASSIFIED)

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
                    f"El hook score separa top de bottom "
                    f"({top_hook} vs {bottom_hook}): los títulos "
                    "del top puntúan mejor. El hook es la historia.")
            elif bottom_hook - top_hook >= 10:
                reading.append(
                    f"El hook score NO separa (top {top_hook} vs "
                    f"bottom {bottom_hook}): el título no es lo "
                    "que decide aquí; mira duración y tema.")
            else:
                reading.append(
                    f"Hook score parecido en top y bottom "
                    f"({top_hook} vs {bottom_hook}): el título no "
                    "explica la diferencia; mira duración y tema.")
        top_dur = _avg_duration(top)
        bottom_dur = _avg_duration(bottom)
        if top_dur is not None and bottom_dur is not None:
            if abs(top_dur - bottom_dur) >= 10:
                reading.append(
                    f"Duración media: top {top_dur}s vs bottom "
                    f"{bottom_dur}s.")
        top_len = _avg_title_len(top)
        bottom_len = _avg_title_len(bottom)
        if top_len is not None and bottom_len is not None:
            if abs(top_len - bottom_len) >= 15:
                reading.append(
                    f"Longitud media del título: top {top_len} car. "
                    f"vs bottom {bottom_len} car.")
        if not reading:
            reading.append(
                "Top y bottom no se separan en hook, duración ni "
                "título con estos datos: mira el tema y el "
                "streamer de cada clip, no las medias.")
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
        return {
            "title": clip.get("title") or "",
            "url": clip.get("url") or "",
            "views": clip.get("views"),
            "outlier": clip.get("outlier"),
            "duration_s": clip.get("duration_s"),
            "posted_day": clip.get("posted_day") or "",
            "diagnosis": diagnose(clip),
            "hook_formula": formula_name(
                str(clip.get("title") or "")),
        }

    return {
        "tenant": tenant,
        "platform": platform,
        "median_views": median,
        "n_clips": len(clips or []),
        "n_ranked": len(ranked),
        "n_sin_datos": len(no_data),
        "top5": [_row(c) for c in top],
        "bottom5": [_row(c) for c in bottom],
        "hook_score_top": top_hook,
        "hook_score_bottom": bottom_hook,
        "formula_top": formula_top,
        "formula_bottom": formula_bottom,
        "n_unclassified": n_unclassified,
        "reading": reading,
        "has_follows_data": has_follows,
        "gaps": [
            "TikTok/IG/FB: sin métricas en v1 (sin scraping; "
            "pendientes de sus APIs oficiales).",
            "Alcance de no-seguidores, hold a 3s, envíos por "
            "alcance y follows por alcance: no los da yt-dlp; "
            "vienen de YouTube Studio / Meta Insights.",
            ("Diagnóstico de perfil pendiente: sin datos de "
             "follows (columna 'follows' en el CSV o YouTube "
             "Studio).") if not has_follows else
            "Diagnóstico de perfil activo (hay datos de follows).",
        ],
    }


def render_text(report):
    """Informe legible (el texto que se lee, no solo el JSON)."""
    lines = [
        f"AUDITORÍA  ·  tenant {report['tenant']}  ·  "
        f"{report['platform']}  ·  mediana "
        f"{report['median_views']} views  ·  "
        f"{report['n_clips']} clips",
        "",
        "TOP 5 POR OUTLIER",
    ]
    for row in report["top5"]:
        lines.append(
            f"  {row['outlier']}x  {row['views']} views  "
            f"{row['title'][:70]}  [{row['diagnosis']}]  "
            f"({row.get('hook_formula') or UNCLASSIFIED})")
    lines.append("")
    lines.append("BOTTOM 5 POR OUTLIER")
    for row in report["bottom5"]:
        lines.append(
            f"  {row['outlier']}x  {row['views']} views  "
            f"{row['title'][:70]}  [{row['diagnosis']}]  "
            f"({row.get('hook_formula') or UNCLASSIFIED})")
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
    if report["n_sin_datos"]:
        lines.append("")
        lines.append(f"Sin datos: {report['n_sin_datos']} clip(s) "
                     "(sin vistas o sin mediana).")
    lines.append("")
    lines.append("LECTURA")
    for item in report["reading"]:
        lines.append(f"- {item}")
    lines.append("")
    lines.append("HUECOS (v1)")
    for gap in report["gaps"]:
        lines.append(f"- {gap}")
    return "\n".join(lines)


def run_audit(tenant, platform="youtube_shorts", csv_path=None,
              database_id=None, limit=100, runner=None):
    """Una auditoría: D1 o CSV -> vistas -> outlier -> informe.

    Devuelve (report, motivo). Sin D1 ni CSV, o sin clips, el
    motivo lo dice y el report es None (no se falla en silencio).
    """
    clips = None
    source = ""
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
    if not clips:
        return None, (f"sin clips posted en {source} "
                      f"(plataforma {platform}): nada que auditar.")
    for clip in clips:
        stats = fetch_youtube_stats(clip["url"], runner=runner)
        if stats is None:
            clip["views"] = None
            clip["duration_s"] = None
            continue
        clip["views"] = stats["views"]
        clip["duration_s"] = stats["duration_s"]
        if not clip.get("title"):
            clip["title"] = stats["title"]
    median, clips = compute_outliers(clips)
    report = build_report(clips, median, tenant, platform)
    report["source"] = source
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

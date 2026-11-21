#!/usr/bin/env python3
"""B3 — colchón de 7 días de jobs: planifica y mantiene stock de publicación.

Qué hace
--------
(a) Calcula el stock actual: clips aprobados con jobs programados para
    los próximos 7 días, por cuenta (stock en días, no solo en jobs).
(b) Crea jobs para clips aprobados hasta completar 7 días, DENTRO del
    ritmo (topes de buffer_config.py: máx 2/cuenta/día, 1/día en
    calentamiento, 3 h de separación — 4 h YouTube —, franjas Chile,
    1 día libre/semana por cuenta, máx 3/cuenta/24 h rolling).
(c) Recuperación: si un día se pierde (--recover YYYY-MM-DD),
    reprograma lo perdido en los días siguientes DENTRO del ritmo;
    lo que no cabe queda pendiente y se reporta, nunca se meten
    6 publicaciones de golpe para "compensar".

Modos
-----
- Por defecto DRY-RUN: solo planifica e imprime el plan + el SQL que
  ejecutaría. NO escribe nada en D1, no llama a la API de Cloudflare
  para escribir; con entradas locales (--tabla/--plan) ni siquiera
  lee D1.
- --execute: crea los jobs en D1 (mismo formato que
  create-jobs-2100.py: state scheduled, manual=1, next_run_at = hora
  de la franja planeada para que el ciclo no los tome antes,
  idempotency_key SHA-256 clip|plataforma|cuenta|día). Se niega antes
  de las 21:00 del 2026-11-20 -03 salvo --force (escrituras D1
  agotadas hasta el reset). En --execute relee clips/jobs/cuentas
  de D1 y aplica los pre-chequeos (publish_blocked=0, sin job
  previo, cuenta verify_ok=1).

Entradas (dry-run): tabla.json de la auditoría (clips aprobados =
PUBLICABLE_AHORA, blocked=false) y plan_2100.json (jobs ya
programados vie 20 -> lun 23). Sin clips aprobados nuevos el plan
sale con déficit explícito: el colchón no inventa stock.

Uso:
  python3 buffer_jobs.py --tabla <tabla.json> --plan <plan_2100.json>
  python3 buffer_jobs.py --tabla ... --plan ... --recover 2026-11-21
  python3 buffer_jobs.py --tabla ... --plan ... --execute --approved clips.json
Nunca imprime ni guarda secretos/tokens.
"""
import hashlib
import json
import subprocess
import sys
import time
from datetime import date, datetime, time as dtime, timedelta, timezone
from zoneinfo import ZoneInfo

sys.path.insert(0, __file__.rsplit("/", 1)[0])
from buffer_config import (  # noqa: E402
    ACCOUNT_BY_KEY, ACCOUNT_BY_PLATFORM_LANG, ACCOUNTS,
    CROSS_PLATFORM_MINUTES, HORIZON_DAYS, MIN_SEO, MIN_VIRALITY,
    PLAN_ACCOUNT_NAMES, REST_WEEKDAY, ROLLING_24H_MAX, SLOTS,
    daily_cap, spacing_hours,
)

STGO = ZoneInfo("America/Santiago")
TENANT = "sebastian"
ACCT = "27afc37225a02fd92557f901c05dabee"
DB = "5cf19675-f905-4e49-b8f2-96ae0810fafd"
CF = "/home/hatch/workspace/custom-connectors/custom.cloudflare/bin/cloudflare.py"
CUTOVER = datetime(2026, 11, 20, 21, 0, tzinfo=STGO)
DEFAULT_START = date(2026, 11, 20)

# ---------------------------------------------------------------------------
# Entradas locales (snapshots verificados; no tocan D1)
# ---------------------------------------------------------------------------

def load_tabla(path):
    """Clips aprobados desde tabla.json: PUBLICABLE_AHORA y no bloqueados.

    Devuelve (approved, used_pairs): `used_pairs` son los pares
    (clip_id, account_key) que YA tienen un job en D1 (cualquier
    estado: draft, posted, scheduled) según la tabla. Regla 5: un
    clip no se repite nunca en la misma cuenta, así que el colchón
    no vuelve a programar esos pares.
    """
    with open(path, encoding="utf-8") as f:
        rows = json.load(f)
    approved = []
    used_pairs = set()
    for r in rows:
        for platform, _state in r.get("jobs", []):
            # Par sin cuenta (drafts huérfanos IG/FB EN del clip 12):
            # no hay cuenta que mapear; se ignoran, no rompen la carga.
            acct = ACCOUNT_BY_PLATFORM_LANG.get(
                (platform, r.get("language", "es")))
            if acct is not None:
                used_pairs.add((r["id"], acct["key"]))
        if r.get("categoria") != "PUBLICABLE_AHORA":
            continue
        if r.get("blocked"):
            continue
        approved.append({
            "clip_id": r["id"],
            "language": r.get("language", "es"),
            "virality": r.get("viralidad"),
            "seo": r.get("seo_max"),  # None = sin medir (no bloquea, gate fail-open)
            "title": r.get("title", ""),
        })
    approved.sort(key=lambda c: (-(c["virality"] or 0), c["clip_id"]))
    return approved, used_pairs


def load_plan_2100(path):
    """Jobs ya programados desde plan_2100.json -> entradas de schedule."""
    with open(path, encoding="utf-8") as f:
        plan = json.load(f)
    sched = []
    for e in plan.get("hoy_publicado", []):
        key = _account_key(e["plataforma"], "es" if e["clip"] == 11 else "en")
        hh, mm = _parse_hhmm(e["hora"])
        sched.append(_entry(date(2026, 11, 20), hh, mm, e["clip"], key,
                            "posted", "existing"))
    for e in plan.get("plan", []):
        key = PLAN_ACCOUNT_NAMES[e["cuenta"]]
        hh, mm = _parse_hhmm(e["hora"])
        sched.append(_entry(date.fromisoformat(e["fecha"]), hh, mm,
                            e["clip"], key, "scheduled", "existing"))
    return sched


def _parse_hhmm(s):
    s = str(s).strip().lstrip("~")
    hh, mm = s.split(":")
    return int(hh), int(mm)


def _account_key(platform, language):
    a = ACCOUNT_BY_PLATFORM_LANG.get((platform, language))
    if a is None:
        raise ValueError(f"sin cuenta {platform}/{language}")
    return a["key"]


def _entry(day, hh, mm, clip_id, account_key, state, origin):
    dt = datetime.combine(day, dtime(hh, mm), tzinfo=STGO)
    return {"date": day, "dt": dt, "clip_id": clip_id,
            "account_key": account_key,
            "platform": ACCOUNT_BY_KEY[account_key]["platform"],
            "state": state, "origin": origin}


# ---------------------------------------------------------------------------
# Planificación pura (sin I/O; es lo que testean los tests)
# ---------------------------------------------------------------------------

def _jitter_minutes(account_key, day, slot_idx):
    """Desplazamiento determinista 0–10 min tras el inicio de franja:
    nunca la misma hora exacta todos los días (regla 4b), nunca antes
    de la franja, y reproducible (mismo plan si se re-ejecuta)."""
    raw = f"{account_key}|{day.isoformat()}|{slot_idx}".encode()
    return int(hashlib.sha256(raw).hexdigest(), 16) % 11


def candidate_times(day, account_key):
    """Horas candidatas dentro de las franjas Chile para ese día/cuenta.

    Por franja: inicio + jitter, y luego pasos de +15 min dentro de la
    franja (para el espaciamiento entre plataformas del mismo clip).
    """
    out = []
    for i, (h0, h1) in enumerate(SLOTS):
        base = datetime.combine(day, dtime(h0, 0), tzinfo=STGO)
        base += timedelta(minutes=_jitter_minutes(account_key, day, i))
        end = datetime.combine(day, dtime(h1, 0), tzinfo=STGO)
        t = base
        while t < end:
            out.append(t)
            t += timedelta(minutes=CROSS_PLATFORM_MINUTES)
    return out


def _posts_on(schedule, account_key, day):
    return [s for s in schedule
            if s["account_key"] == account_key and s["date"] == day]


def can_place(schedule, account, when, clip_id, used_pairs=frozenset()):
    """¿Cabe esta publicación en `when` sin romper ningún tope?

    Comprueba, en orden: día libre, tope diario (calentamiento/plan/
    techo del colchón), mismo clip hoy en esta cuenta, mismo clip
    YA en esta cuenta (regla 5: nunca se repite un clip en la misma
    cuenta, ni otro día), espaciamiento con la última subida de la
    cuenta, rolling 24 h, y 15 min con el mismo clip en otra
    plataforma.
    """
    day = when.date()
    if REST_WEEKDAY[account["key"]] == day.weekday():
        return False, "día libre de la cuenta"
    if (clip_id, account["key"]) in used_pairs:
        return False, "clip ya con job en esta cuenta"
    if any(s["clip_id"] == clip_id and s["account_key"] == account["key"]
           for s in schedule):
        return False, "clip ya en esta cuenta"
    today = _posts_on(schedule, account["key"], day)
    if len(today) >= daily_cap(account):
        return False, f"tope diario {len(today)}/{daily_cap(account)}"
    if any(s["clip_id"] == clip_id for s in today):
        return False, "mismo clip hoy en esta cuenta"
    spacing = timedelta(hours=spacing_hours(account["platform"]))
    for s in schedule:
        if s["account_key"] != account["key"]:
            continue
        if abs((when - s["dt"]).total_seconds()) < spacing.total_seconds():
            return False, (f"espaciamiento <{spacing_hours(account['platform'])}"
                           f" h en {account['key']}")
    window_start = when - timedelta(hours=24)
    last24 = [s for s in schedule
              if s["account_key"] == account["key"]
              and window_start < s["dt"] <= when]
    if len(last24) >= ROLLING_24H_MAX:
        return False, f"rolling 24 h {len(last24)}/{ROLLING_24H_MAX}"
    for s in schedule:
        if s["clip_id"] == clip_id and s["date"] == day:
            if abs((when - s["dt"]).total_seconds()) < CROSS_PLATFORM_MINUTES * 60:
                return False, "mismo clip <15 min en otra plataforma"
    return True, ""


def _pick_clip(approved, schedule, account, day, used_today):
    """Elige qué clip poner: primero el clip de hoy ya usado en otra
    cuenta (estira el stock: 1 clip cubre el día en varias cuentas);
    si no, el aprobado con menos colocaciones totales (reparto) y
    mayor viralidad, que pase las puertas y no esté hoy en la cuenta.
    """
    lang = account["language"]
    todays = [c for c in used_today if c["language"] == lang
              and not any(s["clip_id"] == c["clip_id"] and s["date"] == day
                          and s["account_key"] == account["key"]
                          for s in schedule)]
    if todays:
        todays.sort(key=lambda c: (-(c["virality"] or 0), c["clip_id"]))
        return todays[0]
    placed = {}
    for s in schedule:
        placed[s["clip_id"]] = placed.get(s["clip_id"], 0) + 1
    cands = []
    for c in approved:
        if c["language"] != lang:
            continue
        if (c["virality"] or 0) < MIN_VIRALITY:
            continue
        if c["seo"] is not None and c["seo"] < MIN_SEO:
            continue
        if any(s["clip_id"] == c["clip_id"] and s["date"] == day
               and s["account_key"] == account["key"] for s in schedule):
            continue
        cands.append(c)
    if not cands:
        return None
    cands.sort(key=lambda c: (placed.get(c["clip_id"], 0),
                              -(c["virality"] or 0), c["clip_id"]))
    return cands[0]


def plan_buffer(approved, existing, start, horizon=HORIZON_DAYS,
                used_pairs=frozenset()):
    """Completa el colchón hasta `horizon` días desde `start`.

    Devuelve (schedule_completo, nuevos, sin_colocar): `nuevos` son
    las entradas con origin='buffer'; `sin_colocar` los clips
    aprobados que no cupieron en ningún hueco (no se fuerzan).
    """
    schedule = list(existing)
    nuevos = []
    for offset in range(horizon):
        day = start + timedelta(days=offset)
        used_today = []  # clips ya usados hoy (cualquier cuenta)
        for s in schedule:
            if s["date"] == day:
                clip = next((c for c in approved
                             if c["clip_id"] == s["clip_id"]), None)
                if clip and clip not in used_today:
                    used_today.append(clip)
        for account in ACCOUNTS:
            while len(_posts_on(schedule, account["key"], day)) < daily_cap(account):
                clip = _pick_clip(approved, schedule, account, day,
                                  used_today)
                if clip is None:
                    break
                placed = False
                for when in candidate_times(day, account["key"]):
                    ok, _ = can_place(schedule, account, when,
                                      clip["clip_id"], used_pairs)
                    if ok:
                        e = {"date": day, "dt": when,
                             "clip_id": clip["clip_id"],
                             "account_key": account["key"],
                             "platform": account["platform"],
                             "state": "scheduled", "origin": "buffer"}
                        schedule.append(e)
                        nuevos.append(e)
                        if clip not in used_today:
                            used_today.append(clip)
                        placed = True
                        break
                if not placed:
                    break
    placed_ids = {e["clip_id"] for e in nuevos}
    sin_colocar = [c for c in approved
                   if c["clip_id"] not in placed_ids
                   and not any(s["clip_id"] == c["clip_id"]
                               for s in existing)]
    return schedule, nuevos, sin_colocar


def recover_lost(schedule, lost_day, approved, start, horizon=HORIZON_DAYS,
                 used_pairs=frozenset()):
    """Reprograma lo perdido de `lost_day` en los días siguientes.

    Lo ya publicado no se recupera. Cada ítem perdido conserva su
    clip y su cuenta y se coloca en el primer hueco libre posterior
    que respete TODOS los topes. Lo que no cabe queda en `unplaced`
    y se reporta: nunca se meten 6 publicaciones de golpe.
    Los pares (clip, cuenta) de los propios ítems perdidos se
    excluyen de `used_pairs`: ese job no publicó, así que el par
    vuelve a estar libre (si el job draft siguiera vivo en D1, el
    pre-chequeo de --execute lo salta en vez de duplicarlo).
    Devuelve (schedule, placed, unplaced).
    """
    missed = [s for s in schedule
              if s["date"] == lost_day and s["state"] != "posted"]
    rest = [s for s in schedule if s not in missed]
    pairs = set(used_pairs) - {(m["clip_id"], m["account_key"])
                               for m in missed}
    placed, unplaced = [], []
    end = start + timedelta(days=horizon - 1)
    for m in sorted(missed, key=lambda s: s["dt"]):
        account = ACCOUNT_BY_KEY[m["account_key"]]
        done = False
        day = lost_day + timedelta(days=1)
        while day <= end and not done:
            for when in candidate_times(day, account["key"]):
                ok, _ = can_place(rest, account, when, m["clip_id"], pairs)
                if ok:
                    e = {"date": day, "dt": when, "clip_id": m["clip_id"],
                         "account_key": m["account_key"],
                         "platform": m["platform"], "state": "scheduled",
                         "origin": "recovered"}
                    rest.append(e)
                    placed.append(e)
                    done = True
                    break
            day += timedelta(days=1)
        if not done:
            unplaced.append(m)
    return rest, placed, unplaced


def compute_stock(approved, schedule, start, horizon=HORIZON_DAYS):
    """Stock en días: por cuenta y global, + déficit.

    Un día tiene cobertura si hay >=1 publicación (posted, draft,
    scheduled o planned) ese día. `clips_sin_usar` = aprobados sin
    ningún job ni hueco usado. El déficit ES se expresa en días y
    en clips mínimos (1 clip/día cubre las 4 cuentas ES; cada clip
    ya usado en sus 4 plataformas no estira más).
    """
    days = [start + timedelta(days=i) for i in range(horizon)]
    per_account = {}
    for a in ACCOUNTS:
        covered = [d for d in days
                   if _posts_on(schedule, a["key"], d)]
        per_account[a["key"]] = {
            "dias_cubiertos": len(covered),
            "dias": [d.isoformat() for d in covered],
            "dias_sin": [d.isoformat() for d in days if d not in covered],
        }
    covered_days = [d for d in days
                    if any(s["date"] == d for s in schedule)]
    missing = [d for d in days if d not in covered_days]
    used_ids = {s["clip_id"] for s in schedule}
    unused = [c for c in approved if c["clip_id"] not in used_ids]
    return {
        "horizonte": [d.isoformat() for d in days],
        "dias_cubiertos_global": len(covered_days),
        "dias_sin_cobertura": [d.isoformat() for d in missing],
        "por_cuenta": per_account,
        "clips_aprobados": len(approved),
        "clips_sin_usar": [c["clip_id"] for c in unused],
        "faltan_dias_es_min_clips": len(
            [d for d in missing if _es_day_needs_clip(d, schedule)]),
        "faltan_dias": len(missing),
    }


def _es_day_needs_clip(day, schedule):
    """Un día sin cobertura necesita un clip ES salvo que TODAS las
    cuentas ES descansen ese día (no ocurre con la rotación actual:
    como mucho 1 cuenta ES descansa por día)."""
    es_keys = [a["key"] for a in ACCOUNTS if a["language"] == "es"]
    resting = sum(1 for k in es_keys if REST_WEEKDAY[k] == day.weekday())
    return resting < len(es_keys)


# ---------------------------------------------------------------------------
# Escritura D1 (solo --execute; mismo formato que create-jobs-2100.py)
# ---------------------------------------------------------------------------

def d1(sql):
    r = subprocess.run(
        [sys.executable, CF, "post",
         f"/accounts/{ACCT}/d1/database/{DB}/query",
         json.dumps({"sql": sql})],
        capture_output=True, text=True, check=True)
    d = json.loads(r.stdout)
    res = d["result"][0]
    if not res.get("success", True):
        raise RuntimeError(f"D1 fallo: {json.dumps(res)[:400]}")
    return res.get("results", [])


def esc(s):
    return "'" + str(s).replace("'", "''") + "'"


def idem_key(clip_id, platform, account_id, day_key):
    raw = f"{clip_id}|{platform}|{account_id}|{day_key}"
    return hashlib.sha256(raw.encode()).hexdigest()


def build_job_sql(entry, clip_row, now_ms):
    """INSERT del job + entrada publish_states (igual que el modelo)."""
    account = ACCOUNT_BY_KEY[entry["account_key"]]
    day_key = entry["date"].isoformat()
    key = idem_key(entry["clip_id"], entry["platform"],
                   account["account_id"], day_key)
    title = clip_row["seo_title"] or clip_row["title"]
    caption = clip_row["seo_description"] or title
    slot_ms = int(entry["dt"].timestamp() * 1000)
    insert = (
        "INSERT INTO jobs (tenant_id, kind, clip_id, platform, "
        "api_account_id, state, idempotency_key, title, caption, r2_key, "
        "language, attempts, retryable, next_run_at, created_at, "
        "updated_at, manual) VALUES ("
        f"'sebastian', 'publish', {entry['clip_id']}, "
        f"{esc(entry['platform'])}, {account['account_id']}, 'scheduled', "
        f"{esc(key)}, {esc(title)}, {esc(caption)}, "
        f"{esc(clip_row['video_r2_key'])}, {esc(account['language'])}, "
        f"0, 1, {slot_ms}, {now_ms}, {now_ms}, 1)")
    state_entry = {
        "state": "programado",
        "scheduledFor": entry["dt"].astimezone(timezone.utc).isoformat().replace("+00:00", "Z"),
        "publishedAt": None,
        "note": "Colchón B3: job programado dentro del ritmo.",
        "since": None,
        "updatedAt": datetime.fromtimestamp(
            now_ms / 1000, tz=timezone.utc).isoformat().replace("+00:00", "Z"),
    }
    return insert, state_entry, key


def execute_plan(nuevos):
    """Crea en D1 solo los jobs nuevos del plan, con pre-chequeos."""
    now_ms = int(time.time() * 1000)
    stmts = []
    states_by_clip = {}
    for e in nuevos:
        clip_rows = d1(
            "SELECT id, title, seo_title, seo_description, video_r2_key, "
            "language, publish_blocked FROM clips "
            f"WHERE tenant_id = 'sebastian' AND id = {int(e['clip_id'])}")
        if not clip_rows:
            raise RuntimeError(f"clip {e['clip_id']} no existe en D1")
        clip = clip_rows[0]
        if clip["publish_blocked"] != 0:
            raise RuntimeError(
                f"clip {e['clip_id']} publish_blocked=1: no se crea job")
        prev = d1(
            "SELECT id, state FROM jobs WHERE tenant_id = 'sebastian' "
            f"AND clip_id = {int(e['clip_id'])} "
            f"AND platform = {esc(e['platform'])} AND deleted_at IS NULL")
        if prev:
            print(f"# Job {e['clip_id']} {e['platform']}: ya existe "
                  f"job {prev[0]['id']} ({prev[0]['state']}): se salta.")
            continue
        acct = ACCOUNT_BY_KEY[e["account_key"]]
        ok = d1(
            "SELECT id, verify_ok FROM api_accounts "
            f"WHERE tenant_id = 'sebastian' AND id = {acct['account_id']}")
        if not ok or ok[0]["verify_ok"] != 1:
            raise RuntimeError(f"cuenta {acct['account_id']} sin verify_ok=1")
        ins, entry, key = build_job_sql(e, clip, now_ms)
        print(f"# Job {e['clip_id']} {e['platform']} "
              f"({e['account_key']}) {e['dt'].strftime('%Y-%m-%d %H:%M')}: "
              f"key {key}")
        stmts.append((f"job {e['clip_id']} {e['platform']}", ins))
        states_by_clip.setdefault(e["clip_id"], {})[e["platform"]] = entry
    for clip_id, entries in sorted(states_by_clip.items()):
        cur = d1(
            "SELECT publish_states FROM clips WHERE tenant_id = 'sebastian' "
            f"AND id = {int(clip_id)}")[0]["publish_states"] or "{}"
        current = json.loads(cur)
        current.update(entries)
        stmts.append((
            f"publish_states {clip_id}",
            "UPDATE clips SET publish_states = "
            f"{esc(json.dumps(current, ensure_ascii=False))} "
            f"WHERE tenant_id = 'sebastian' AND id = {clip_id}"))
    for desc, sql in stmts:
        d1(sql)
        print(f"# OK: {desc}")
    return len(stmts)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main(argv=None):
    args = sys.argv[1:] if argv is None else argv
    execute = "--execute" in args
    force = "--force" in args
    start = DEFAULT_START
    horizon = HORIZON_DAYS
    tabla = plan_path = None
    recover = None
    for i, a in enumerate(args):
        if a == "--start" and i + 1 < len(args):
            start = date.fromisoformat(args[i + 1])
        if a == "--horizon" and i + 1 < len(args):
            horizon = int(args[i + 1])
        if a == "--tabla" and i + 1 < len(args):
            tabla = args[i + 1]
        if a == "--plan" and i + 1 < len(args):
            plan_path = args[i + 1]
        if a == "--recover" and i + 1 < len(args):
            recover = date.fromisoformat(args[i + 1])
    now = datetime.now(STGO)
    print(f"# Ahora Santiago: {now.strftime('%Y-%m-%d %H:%M:%S')} | "
          f"inicio {start.isoformat()} | horizonte {horizon} días | "
          f"modo {'ESCRITURA' if execute else 'DRY-RUN'}")
    if execute and now < CUTOVER and not force:
        print("# RECHAZADO: antes de las 21:00 las escrituras D1 están "
              "agotadas. Usa --force solo si el límite ya se liberó.")
        return 2
    if not tabla or not plan_path:
        print("# Falta --tabla <tabla.json> y --plan <plan_2100.json>.")
        return 2
    approved, used_pairs = load_tabla(tabla)
    existing = load_plan_2100(plan_path)
    print(f"# Clips aprobados (PUBLICABLE_AHORA, no bloqueados): "
          f"{[c['clip_id'] for c in approved]}")
    print(f"# Jobs ya programados (plan 2100 + hoy): {len(existing)}")
    stock0 = compute_stock(approved, existing, start, horizon)
    _print_stock("STOCK ANTES DEL COLCHÓN", stock0)
    if recover is not None:
        schedule, placed, unplaced = recover_lost(
            existing, recover, approved, start, horizon, used_pairs)
        print(f"# RECUPERACIÓN día perdido {recover.isoformat()}: "
              f"reprogramados {len(placed)}, sin hueco {len(unplaced)}")
        for e in placed:
            print(f"#   + clip {e['clip_id']} {e['account_key']} "
                  f"{e['dt'].strftime('%Y-%m-%d %H:%M')} (recovered)")
        for e in unplaced:
            print(f"#   ! SIN HUECO clip {e['clip_id']} "
                  f"{e['account_key']} (queda pendiente, no se fuerza)")
        stock1 = compute_stock(approved, schedule, start, horizon)
        _print_stock("STOCK TRAS RECUPERACIÓN", stock1)
        return 0
    schedule, nuevos, sin_colocar = plan_buffer(
        approved, existing, start, horizon, used_pairs)
    print(f"# Jobs nuevos del colchón: {len(nuevos)}")
    for e in nuevos:
        print(f"#   + clip {e['clip_id']} {e['platform']} "
              f"({e['account_key']}) {e['dt'].strftime('%Y-%m-%d %H:%M')}")
    if sin_colocar:
        print(f"# Clips aprobados sin hueco (no se fuerzan): "
              f"{[c['clip_id'] for c in sin_colocar]}")
    stock1 = compute_stock(approved, schedule, start, horizon)
    _print_stock("STOCK CON COLCHÓN", stock1)
    if not nuevos:
        print("# Sin jobs nuevos: el colchón no puede crecer sin clips "
              "aprobados. Déficit explícito arriba; depende del batch "
              "de re-hecho.")
    if not execute:
        print("# DRY-RUN: nada escrito en D1. Para crear los jobs: "
              "--execute (a partir de las 21:00).")
        return 0
    n = execute_plan(nuevos)
    print(f"# ESCRITURA: {n} sentencias ejecutadas en D1.")
    return 0


def _print_stock(title, stock):
    print(f"# --- {title} ---")
    print(f"# Días cubiertos (global): {stock['dias_cubiertos_global']}/"
          f"{len(stock['horizonte'])} "
          f"({stock['horizonte'][0]} → {stock['horizonte'][-1]})")
    print(f"# Días sin cobertura: {stock['dias_sin_cobertura'] or 'ninguno'}")
    for key, v in stock["por_cuenta"].items():
        print(f"#   {key}: {v['dias_cubiertos']} días "
              f"(sin: {v['dias_sin'] or '—'})")
    print(f"# Clips aprobados: {stock['clips_aprobados']} | sin usar: "
          f"{stock['clips_sin_usar'] or 'ninguno'}")
    print(f"# Faltan: {stock['faltan_dias']} días de stock | "
          f"mín. {stock['faltan_dias_es_min_clips']} clips ES aprobados "
          f"para cubrirlos (1 clip/día cubre las 4 cuentas ES)")


if __name__ == "__main__":
    raise SystemExit(main())

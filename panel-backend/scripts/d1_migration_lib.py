#!/usr/bin/env python3
"""Librería compartida para el chequeo previo de migraciones D1 (A5).

Usada por scripts/check-migration-budget.py (puerta de presupuesto) y
scripts/split-sql-batches.py (partido en lotes de ~2.000 filas).

SOLO LECTURAS: los conteos de filas se hacen con SELECT COUNT(*) sobre
una SQLite local (el export BK restaurado, modo solo lectura) o sobre
la D1 de origen por la API REST (SELECT por el helper cloudflare.py).
Nunca se escribe en D1 desde aquí.

Reglas de conteo (cada INSERT/UPDATE/DELETE que cambia una fila es
una escritura; el límite es 100.000/día POR CUENTA, todas las D1
juntas; reset 00:00 UTC = 21:00 -03):
  - INSERT ... VALUES con N tuplas: N filas (cota superior si es
    OR IGNORE; OR REPLACE cuenta x2 por tupla: DELETE + INSERT).
  - INSERT ... SELECT / CREATE TABLE ... AS SELECT: COUNT(*) del
    SELECT, ejecutado contra la fuente (lectura).
  - UPDATE / DELETE con WHERE: COUNT(*) de la misma condición;
    sin WHERE: COUNT(*) de la tabla entera.
  - ALTER TABLE / CREATE INDEX / CREATE TABLE (sin AS): 0 filas.
  - Cada migración aplicada por `wrangler d1 migrations apply`
    suma 1 fila en d1_migrations (bookkeeping, opcional).
  - Lo que no se puede contar (sentencia desconocida, o SELECT sin
    fuente disponible) devuelve None: el llamador debe RECHAZAR
    (fail closed), nunca asumir 0.
"""
import json
import os
import re
import sqlite3
import subprocess

DAILY_LIMIT = 100_000
MAX_FRACTION = 0.20
BATCH_SIZE = 2_000
ACCOUNT_ID = "27afc37225a02fd92557f901c05dabee"
CF_HELPER = os.path.expanduser(
    "~/workspace/custom-connectors/custom.cloudflare/bin/cloudflare.py")

# Tablas de solo-agregar con marca temporal, para la estimación de
# escrituras de hoy desde las tablas de eventos (fuente de cuota
# alternativa; es una COTA INFERIOR, no el total exacto).
EVENT_TABLES = [
    ("worker_events", "at"),
    ("clip_events", "at"),
    ("brain_tokens", "created_at"),
]

# ---------------------------------------------------------------------------
# División de SQL en sentencias (usa el tokenizador de SQLite: respeta
# literales con ';' dentro, comentarios -- y /* */).
# ---------------------------------------------------------------------------

def split_statements(sql: str) -> list:
    stmts, buf = [], ""
    for line in sql.splitlines(keepends=True):
        buf += line
        if sqlite3.complete_statement(buf):
            s = buf.strip()
            if s and s != ";":
                stmts.append(s)
            buf = ""
    if buf.strip() and buf.strip() != ";":
        stmts.append(buf.strip())
    return stmts


def strip_leading_comments(stmt: str) -> str:
    s = stmt.strip()
    while True:
        if s.startswith("--"):
            nl = s.find("\n")
            s = s[nl + 1:].strip() if nl != -1 else ""
        elif s.startswith("/*"):
            end = s.find("*/")
            if end == -1:
                return ""
            s = s[end + 2:].strip()
        else:
            return s


def _first_keyword(stmt: str) -> str:
    s = strip_leading_comments(stmt)
    m = re.match(r"([A-Za-z]+)", s)
    return m.group(1).upper() if m else ""


def _find_top_level(text: str, keyword: str) -> int:
    """Índice de `keyword` fuera de literales y paréntesis, o -1."""
    pat = re.compile(r"\b" + re.escape(keyword) + r"\b", re.I)
    depth, i, n = 0, 0, len(text)
    in_str = None  # ' or "
    while i < n:
        c = text[i]
        if in_str:
            if c == in_str:
                if in_str == "'" and i + 1 < n and text[i + 1] == "'":
                    i += 2
                    continue
                in_str = None
        else:
            if c in ("'", '"'):
                in_str = c
            elif c == "(":
                depth += 1
            elif c == ")":
                depth -= 1
            elif depth == 0:
                m = pat.match(text, i)
                if m:
                    return i
        i += 1
    return -1


def count_value_tuples(values_sql: str) -> int:
    """Cuenta los grupos (...) de nivel superior en una cláusula VALUES."""
    depth, count, i, n = 0, 0, 0, len(values_sql)
    in_str = None
    while i < n:
        c = values_sql[i]
        if in_str:
            if c == in_str:
                if in_str == "'" and i + 1 < n and values_sql[i + 1] == "'":
                    i += 2
                    continue
                in_str = None
        else:
            if c in ("'", '"'):
                in_str = c
            elif c == "(":
                if depth == 0:
                    count += 1
                depth += 1
            elif c == ")":
                depth -= 1
        i += 1
    return count


def _extract_table_and_where(stmt: str, keyword: str):
    """Para UPDATE/DELETE: (tabla, where|None). None si no se parsea."""
    s = strip_leading_comments(stmt)
    m = re.match(
        r"(?:UPDATE\s+(?:OR\s+\w+\s+)?|DELETE\s+FROM\s+)"
        r"(?:\"([^\"]+)\"|'([^']+)'|`([^`]+)`|\[([^\]]+)\]|([A-Za-z_][\w$]*))",
        s, re.I)
    if not m:
        return None
    table = next(g for g in m.groups() if g is not None)
    w = _find_top_level(s, "WHERE")
    where = s[w + 5:].strip().rstrip(";").strip() if w != -1 else None
    return table, where


# ---------------------------------------------------------------------------
# Estimación de filas escritas por sentencia.
# counter(sql) -> int | None: ejecuta SELECT COUNT(*) contra la fuente.
# Devuelve (filas | None, nota). None = no se pudo determinar.
# ---------------------------------------------------------------------------

def estimate_statement(stmt: str, counter) -> tuple:
    s = strip_leading_comments(stmt)
    if not s:
        return 0, "vacía"
    kw = _first_keyword(s)

    if kw in ("BEGIN", "COMMIT", "ROLLBACK", "PRAGMA", "VACUUM", "ANALYZE"):
        return 0, f"{kw} (sin filas)"

    if kw in ("CREATE", "ALTER", "DROP"):
        if kw == "CREATE" and re.search(r"\bAS\s+SELECT\b", s, re.I):
            sel = s[_find_top_level(s, "SELECT"):]
            return _count_select(sel, counter, "CREATE TABLE AS SELECT")
        return 0, f"{kw} (esquema, 0 filas)"

    if kw in ("INSERT", "REPLACE"):
        m = re.match(
            r"(?:INSERT\s+(?:OR\s+(IGNORE|REPLACE|ABORT|FAIL)\s+)?"
            r"|REPLACE\s+)INTO\s+"
            r"(?:\"([^\"]+)\"|'([^']+)'|`([^`]+)`|\[([^\]]+)\]|([A-Za-z_][\w$]*))",
            s, re.I)
        if not m:
            return None, "INSERT no parseable"
        conflict = (m.group(1) or "").upper()
        table = next(g for g in m.groups()[1:] if g is not None)
        v = _find_top_level(s, "VALUES")
        sel = _find_top_level(s, "SELECT")
        if v != -1 and (sel == -1 or v < sel):
            n = count_value_tuples(s[v + 6:])
            if kw == "REPLACE" or conflict == "REPLACE":
                return 2 * n, f"REPLACE INTO {table}: {n} tuplas x2 (DELETE+INSERT)"
            return n, f"INSERT INTO {table}: {n} tuplas"
        if sel != -1:
            return _count_select(s[sel:], counter,
                                 f"INSERT INTO {table} ... SELECT")
        return None, f"INSERT INTO {table} sin VALUES ni SELECT"

    if kw == "UPDATE":
        parsed = _extract_table_and_where(s, "UPDATE")
        if parsed is None:
            return None, "UPDATE no parseable"
        table, where = parsed
        return _count_where(table, where, counter, "UPDATE")

    if kw == "DELETE":
        parsed = _extract_table_and_where(s, "DELETE")
        if parsed is None:
            return None, "DELETE no parseable"
        table, where = parsed
        return _count_where(table, where, counter, "DELETE")

    return None, f"sentencia no clasificada ({kw})"


def _count_select(select_sql: str, counter, what: str) -> tuple:
    if counter is None:
        return None, f"{what}: sin fuente para contar el SELECT"
    q = f"SELECT COUNT(*) AS c FROM ({select_sql.rstrip(';').strip()})"
    try:
        n = counter(q)
    except Exception as e:
        if "no such table" in str(e):
            # La tabla aún no existe en destino (D1 nueva: las
            # migraciones anteriores del mismo lote la crean vacía):
            # el SELECT ve 0 filas cuando se aplique.
            return 0, f"{what}: 0 filas (tabla aún no existe en destino)"
        return None, f"{what}: el COUNT falló: {e}"
    if n is None:
        return None, f"{what}: el COUNT no devolvió número"
    return n, f"{what}: {n} filas (SELECT COUNT(*))"


def _count_where(table: str, where, counter, what: str) -> tuple:
    if counter is None:
        return None, f"{what} {table}: sin fuente para contar"
    q = f'SELECT COUNT(*) AS c FROM "{table}"' + (f" WHERE {where}" if where else "")
    try:
        n = counter(q)
    except Exception as e:
        if "no such table" in str(e):
            # Tabla aún no existe en destino (D1 nueva: la crean las
            # migraciones anteriores del mismo lote, vacía): 0 filas.
            return 0, f"{what} {table}: 0 filas (tabla aún no existe en destino)"
        return None, f"{what} {table}: el COUNT falló: {e}"
    if n is None:
        return None, f"{what} {table}: el COUNT no devolvió número"
    return n, f"{what} {table}" + (f" WHERE {where[:80]}" if where else " (sin WHERE)") + f": {n} filas"


def estimate_sql(sql: str, counter, bookkeeping: bool = False):
    """Estima todas las sentencias. Devuelve dict con per_statement,
    total (o None si alguna no se pudo contar) y unknown[]."""
    per, unknown = [], []
    total = 0
    for stmt in split_statements(sql):
        rows, note = estimate_statement(stmt, counter)
        per.append({"sql": stmt[:200], "rows": rows, "note": note})
        if rows is None:
            unknown.append(note + " :: " + stmt[:160].replace("\n", " "))
        else:
            total += rows
    if bookkeeping:
        total += 1  # fila en d1_migrations por migración aplicada
        per.append({"sql": "-- bookkeeping d1_migrations", "rows": 1,
                    "note": "1 fila en d1_migrations por migración aplicada"})
    return {"per_statement": per, "total": None if unknown else total,
            "unknown": unknown}


# ---------------------------------------------------------------------------
# Fuentes de conteo (solo lecturas).
# ---------------------------------------------------------------------------

def make_sqlite_counter(path: str):
    con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)

    def counter(sql: str):
        row = con.execute(sql).fetchone()
        return row[0] if row else None
    return counter


def d1_query(db_uuid: str, sql: str):
    """SELECT por la API REST de D1 (solo lectura). Mismo patrón que
    runs/phase1-2026-11-20/BK/d1_export.py."""
    body = json.dumps({"sql": sql})
    r = subprocess.run(
        ["python3", CF_HELPER, "post",
         f"/accounts/{ACCOUNT_ID}/d1/database/{db_uuid}/query", body],
        capture_output=True, text=True, timeout=120)
    if r.returncode != 0:
        raise RuntimeError(f"helper cloudflare.py rc={r.returncode}: {r.stderr[:300]}")
    payload = json.loads(r.stdout)
    if not payload.get("success"):
        raise RuntimeError(f"D1 API error: {json.dumps(payload.get('errors'))[:400]} sql={sql[:160]}")
    res = payload["result"][0]
    if not res.get("success", True):
        raise RuntimeError(f"D1 result error: {res} sql={sql[:160]}")
    return res["results"]


def make_d1_counter(db_uuid: str):
    def counter(sql: str):
        rows = d1_query(db_uuid, sql)
        return rows[0]["c"] if rows else None
    return counter


def make_multi_counter(counters):
    """Varios contadores (p. ej. varias SQLite): el primero que
    responda el COUNT gana; si todos fallan, None."""
    def counter(sql: str):
        last = None
        for c in counters:
            try:
                v = c(sql)
                if v is not None:
                    return v
            except Exception as e:
                last = e
        if last is not None:
            raise last
        return None
    return counter


def graphql_rows_written_today(account_id: str = ACCOUNT_ID) -> dict:
    """Escrituras D1 de hoy (fecha UTC) por base, vía GraphQL de
    Cloudflare (analítica, solo lectura). Verificado en vivo el
    2026-11-20: d1QueriesAdaptiveGroups / sum.rowsWritten."""
    import datetime
    today = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d")
    query = (
        "query { viewer { accounts(filter: {accountTag: \"%s\"}) { "
        "d1QueriesAdaptiveGroups(filter: {date_geq: \"%s\", date_leq: \"%s\"}, limit: 100) "
        "{ dimensions { date databaseId } sum { rowsWritten rowsRead } } } } }"
        % (account_id, today, today))
    r = subprocess.run(
        ["python3", CF_HELPER, "post", "/graphql", json.dumps({"query": query})],
        capture_output=True, text=True, timeout=60)
    if r.returncode != 0:
        raise RuntimeError(f"GraphQL helper rc={r.returncode}: {r.stderr[:300]}")
    payload = json.loads(r.stdout)
    if payload.get("errors"):
        raise RuntimeError(f"GraphQL errors: {json.dumps(payload['errors'])[:400]}")
    groups = payload["data"]["viewer"]["accounts"][0]["d1QueriesAdaptiveGroups"]
    per_db = {g["dimensions"]["databaseId"]: g["sum"]["rowsWritten"] for g in groups}
    return {"date_utc": today, "per_db": per_db,
            "total": sum(per_db.values())}


def pending_migrations(migrations_dir: str, db_uuid: str) -> list:
    """Migraciones en disco aún no aplicadas en la D1 destino
    (lectura de d1_migrations por API). En una D1 nueva y vacía
    (sin tabla d1_migrations) todas están pendientes."""
    try:
        applied = {r["name"] for r in
                   d1_query(db_uuid, "SELECT name FROM d1_migrations")}
    except Exception as e:
        if "no such table" in str(e):
            applied = set()
        else:
            raise
    files = sorted(f for f in os.listdir(migrations_dir)
                   if f.endswith(".sql"))
    return [os.path.join(migrations_dir, f) for f in files
            if f not in applied]


def events_written_today_estimate(counters, tables=EVENT_TABLES) -> dict:
    """COTA INFERIOR de escrituras de hoy (desde UTC 00:00): filas
    nuevas en las tablas de eventos. Subestima el total real (no ve
    UPDATEs, DELETEs ni otras tablas): solo sirve para rechazar, o
    para decidir con --accept-estimate explícito."""
    import datetime
    start = int(datetime.datetime.now(datetime.timezone.utc)
                .replace(hour=0, minute=0, second=0, microsecond=0)
                .timestamp() * 1000)
    total, detail = 0, {}
    for table, col in tables:
        q = f'SELECT COUNT(*) AS c FROM "{table}" WHERE "{col}" >= {start}'
        try:
            n = counters(q)
        except Exception:
            n = 0
        detail[table] = n or 0
        total += detail[table]
    return {"since_utc_ms": start, "per_table": detail, "total": total}

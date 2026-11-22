#!/usr/bin/env python3
"""Parte una migración SQL grande en lotes de ~2.000 filas (A5).

Una migración de 23.531 filas de una vez rompió la cuota D1 el
2026-11-20. Las migraciones grandes se parten en lotes de 2.000
filas, se aplica un lote, se verifica, y solo entonces sigue el
siguiente. Este script NO escribe en D1: solo parte el archivo y
genera el manifiesto para verificar entre lotes.

Qué parte:
  - INSERT ... VALUES: se parten por tuplas (exacto: cada tupla es
    1 fila; REPLACE cuenta x2, ver d1_migration_lib).
  - El resto de sentencias (CREATE/ALTER/UPDATE/DELETE/...) van
    enteras en el lote 1 (preámbulo) o en su propio lote si escriben
    filas: UPDATE/DELETE no se pueden partir sin conocer la fuente,
    así que cada uno va solo en un lote con su estimación y hay que
    verificarlo a mano antes de seguir.

Uso:
  split-sql-batches.py ENTRADA.sql --out-dir DIR [--batch-size N]
      [--sqlite RUTA.db] [--d1 UUID]
  split-sql-batches.py --verify --sqlite DB --manifest M.json
      --baseline BASE.json --through-batch K
  split-sql-batches.py --record-baseline --sqlite DB --manifest M.json
      --out BASE.json

Con --sqlite/--d1 el script cuenta también los UPDATE/DELETE (por
la API o la SQLite local, solo lecturas) y los empaqueta en el lote
en curso hasta 2.000 filas; sin fuente, cada UPDATE/DELETE va solo
en su propio lote y el manifiesto lo marca como no contable.

Verificación entre lotes (entorno de prueba: SQLite local del BK):
  1. Antes de aplicar nada:
       split-sql-batches.py --record-baseline --sqlite test.db \\
           --manifest mig.batches.json --out baseline.json
  2. Aplicar el lote 1:  sqlite3 test.db < mig.batch-001.sql
  3. Verificar:  split-sql-batches.py --verify --sqlite test.db \\
                     --manifest mig.batches.json --baseline baseline.json \\
                     --through-batch 1
     (compara el conteo por tabla con baseline + deltas del manifiesto;
      si difiere, PARAR y no aplicar el lote siguiente)
  4. Repetir 2-3 para cada lote. En producción el mismo conteo se hace
     con SELECT COUNT(*) por la API de D1 (solo lectura).

Salida: <stem>.batch-001.sql ... + <stem>.batches.json
"""
import argparse
import json
import os
import re
import sqlite3
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import d1_migration_lib as lib


def split_sql(sql: str, batch_size: int, counter=None):
    """Devuelve (batches, manifest). Cada batch: lista de sentencias."""
    batches, current, current_rows = [], [], 0

    def flush():
        nonlocal current, current_rows
        if current:
            batches.append(current)
            current, current_rows = [], 0

    for stmt in lib.split_statements(sql):
        s = lib.strip_leading_comments(stmt)
        kw = s.split(None, 1)[0].upper() if s.split(None, 1) else ""
        if kw in ("BEGIN", "COMMIT", "ROLLBACK"):
            continue  # cada lote lleva su propia transacción
        rows, note = lib.estimate_statement(stmt, counter=counter)
        if kw in ("INSERT", "REPLACE") and rows is not None:
            # Partir por tuplas.
            v = lib._find_top_level(s, "VALUES")
            if v == -1:
                # INSERT ... SELECT: no se puede partir sin reescribir
                # el SELECT (LIMIT/OFFSET); va entero en un lote.
                if current_rows + (rows or 0) > batch_size and current_rows:
                    flush()
                current.append(stmt)
                current_rows += rows or 0
                if current_rows >= batch_size:
                    flush()
                continue
            head, values = s[:v + 6], s[v + 6:]
            tuples = _split_tuples(values)
            factor = 2 if (kw == "REPLACE" or "OR REPLACE" in s[:v].upper()) else 1
            i = 0
            while i < len(tuples):
                room = batch_size - current_rows
                if room <= 0:
                    flush()
                    room = batch_size
                take = min(room // factor, len(tuples) - i)
                if take <= 0:
                    flush()
                    continue
                chunk = tuples[i:i + take]
                current.append(head + ",".join(chunk) + ";")
                current_rows += take * factor
                i += take
                if current_rows >= batch_size:
                    flush()
        else:
            # DDL (0 filas): al lote en curso. DML contable: se
            # empaqueta hasta 2.000. No contable: solo en su lote.
            if rows is None:
                if current_rows:
                    flush()
                batches.append([stmt])
            elif rows == 0:
                current.append(stmt)
            else:
                if current_rows + rows > batch_size:
                    flush()
                current.append(stmt)
                current_rows += rows
                if current_rows >= batch_size:
                    flush()
    flush()
    return batches


def _split_tuples(values_sql: str):
    """Divide la parte VALUES en sus tuplas (...) de nivel superior."""
    out, depth, start, i, n = [], 0, None, 0, len(values_sql)
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
                    start = i
                depth += 1
            elif c == ")":
                depth -= 1
                if depth == 0 and start is not None:
                    out.append(values_sql[start:i + 1])
                    start = None
        i += 1
    return out


def build_manifest(batches, source: str, batch_size: int, counter=None):
    """Manifiesto con dos cifras por lote: `rows` (filas ESCRITAS,
    para el presupuesto) y `net` por tabla (cambio NETO de conteo,
    para la verificación entre lotes: INSERT suma, DELETE resta,
    UPDATE no cambia el conteo, REPLACE se aproxima a neto 0)."""
    manifest = {"source": source, "batch_size": batch_size,
                "batches": [], "total_rows": 0}
    for idx, stmts in enumerate(batches, 1):
        rows, tables, net = 0, {}, {}
        for stmt in stmts:
            r, _ = lib.estimate_statement(stmt, counter=counter)
            if r is None:
                tables.setdefault("__unknown__", 0)
                continue
            rows += r
            m = lib.strip_leading_comments(stmt)
            kw = m.split(None, 1)[0].upper() if m.split(None, 1) else ""
            tm = (re.search(
                r"INTO\s+(?:\"([^\"]+)\"|'([^']+)'|`([^`]+)`|\[([^\]]+)\]|([A-Za-z_][\w$]*))",
                m, re.I)
                or re.search(
                r"DELETE\s+FROM\s+(?:\"([^\"]+)\"|'([^']+)'|`([^`]+)`|\[([^\]]+)\]|([A-Za-z_][\w$]*))",
                m, re.I)
                or re.search(
                r"UPDATE\s+(?:OR\s+\w+\s+)?(?:\"([^\"]+)\"|'([^']+)'|`([^`]+)`|\[([^\]]+)\]|([A-Za-z_][\w$]*))",
                m, re.I))
            if tm:
                t = next(g for g in tm.groups() if g is not None)
                tables[t] = tables.get(t, 0) + r
                # sqlite_sequence es especial: SQLite escribe en ella
                # como efecto lateral de los INSERT en tablas
                # AUTOINCREMENT (la fila la crea el INSERT, no la
                # migración): su conteo neto no se puede verificar
                # con INSERT-DELETE, así que no entra en `net`.
                if t == "sqlite_sequence":
                    continue
                delta = {"INSERT": r, "DELETE": -r, "UPDATE": 0,
                         "REPLACE": 0, "CREATE": r}.get(kw, 0)
                net[t] = net.get(t, 0) + delta
        manifest["batches"].append({
            "batch": idx, "statements": len(stmts), "rows": rows,
            "tables": tables, "net": net})
        manifest["total_rows"] += rows
    return manifest


def _connect_local(path: str):
    """SQLite local solo para LEER: mode=ro si el archivo existe (así
    nunca se toca un .test.db del BK); conexión normal si aún no
    existe (el baseline de una restauración parte de una DB vacía).
    En ambos casos solo se ejecutan SELECT COUNT(*)."""
    if os.path.exists(path):
        return sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    return sqlite3.connect(path)


def record_baseline(sqlite_path: str, manifest: dict):
    con = _connect_local(sqlite_path)
    tables = set()
    for b in manifest["batches"]:
        tables |= {t for t in b["tables"] if not t.startswith("__")}
    counts = {}
    for t in sorted(tables):
        try:
            counts[t] = con.execute(f'SELECT COUNT(*) FROM "{t}"').fetchone()[0]
        except Exception:
            # La tabla aún no existe (la crea la migración): baseline 0.
            counts[t] = 0
    return {"sqlite": sqlite_path, "counts": counts}


def verify(sqlite_path: str, manifest: dict, baseline: dict, through_batch: int):
    con = _connect_local(sqlite_path)
    expected, problems = {}, []
    for b in manifest["batches"][:through_batch]:
        for t, n in b.get("net", b["tables"]).items():
            if t.startswith("__"):
                problems.append(f"lote {b['batch']}: sentencia no contable "
                                f"({t}); verificar a mano")
                continue
            expected[t] = expected.get(t, 0) + n
    ok = True
    for t, delta in sorted(expected.items()):
        base = baseline["counts"].get(t)
        if base is None:
            problems.append(f"tabla {t}: sin baseline (¿no existía?)")
            ok = False
            continue
        want = base + delta
        try:
            got = con.execute(f'SELECT COUNT(*) FROM "{t}"').fetchone()[0]
        except Exception as e:
            problems.append(f"tabla {t}: no se pudo contar: {e}")
            ok = False
            continue
        if got != want:
            problems.append(f"tabla {t}: esperado {want} (baseline {base} + "
                            f"{delta}), hay {got}")
            ok = False
    return ok, problems


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("input", nargs="?", help="archivo .sql a partir")
    ap.add_argument("--out-dir", help="directorio de salida de los lotes")
    ap.add_argument("--batch-size", type=int, default=lib.BATCH_SIZE)
    ap.add_argument("--sqlite", action="append", default=[],
                    help="SQLite local (solo lectura) para contar UPDATE/DELETE")
    ap.add_argument("--d1", action="append", default=[],
                    help="UUID de D1 (API, solo SELECT) para contar UPDATE/DELETE")
    ap.add_argument("--record-baseline", action="store_true")
    ap.add_argument("--verify", action="store_true")
    ap.add_argument("--manifest", help="manifiesto .batches.json")
    ap.add_argument("--baseline", help="baseline .json")
    ap.add_argument("--through-batch", type=int, default=0)
    ap.add_argument("--out", help="salida del baseline")
    args = ap.parse_args()

    if args.record_baseline:
        manifest = json.load(open(args.manifest, encoding="utf-8"))
        base = record_baseline(args.sqlite[0], manifest)
        with open(args.out, "w", encoding="utf-8") as f:
            json.dump(base, f, indent=2)
        print(f"baseline guardado en {args.out}: {base['counts']}")
        return 0

    if args.verify:
        manifest = json.load(open(args.manifest, encoding="utf-8"))
        baseline = json.load(open(args.baseline, encoding="utf-8"))
        through = args.through_batch or len(manifest["batches"])
        ok, problems = verify(args.sqlite[0], manifest, baseline, through)
        if ok:
            print(f"VERIFICADO hasta el lote {through}: conteos por tabla "
                  f"coinciden con baseline + deltas.")
            return 0
        print(f"VERIFICACIÓN FALLIDA hasta el lote {through}:", file=sys.stderr)
        for p in problems:
            print(f"  - {p}", file=sys.stderr)
        print("PARAR: no aplicar el lote siguiente.", file=sys.stderr)
        return 2

    if not args.input or not args.out_dir:
        print("FALLO: hacen falta ENTRADA.sql y --out-dir "
              "(o --record-baseline / --verify)", file=sys.stderr)
        return 2
    counters = [lib.make_sqlite_counter(p) for p in args.sqlite]
    counters += [lib.make_d1_counter(u) for u in args.d1]
    counter = lib.make_multi_counter(counters) if counters else None
    sql = open(args.input, encoding="utf-8").read()
    batches = split_sql(sql, args.batch_size, counter=counter)
    os.makedirs(args.out_dir, exist_ok=True)
    stem = os.path.splitext(os.path.basename(args.input))[0]
    manifest = build_manifest(batches, args.input, args.batch_size,
                              counter=counter)
    for idx, stmts in enumerate(batches, 1):
        name = f"{stem}.batch-{idx:03d}.sql"
        path = os.path.join(args.out_dir, name)
        with open(path, "w", encoding="utf-8") as f:
            f.write("BEGIN TRANSACTION;\n")
            for s in stmts:
                f.write(s.rstrip(";").strip() + ";\n")
            f.write("COMMIT;\n")
        manifest["batches"][idx - 1]["file"] = name
    mpath = os.path.join(args.out_dir, f"{stem}.batches.json")
    with open(mpath, "w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=2, ensure_ascii=False)
    print(f"{len(batches)} lote(s), {manifest['total_rows']:,} filas en total "
          f"(lote ≤ {args.batch_size}):")
    for b in manifest["batches"]:
        print(f"  lote {b['batch']}: {b['file']} — {b['statements']} "
              f"sentencias, {b['rows']:,} filas")
    print(f"manifiesto: {mpath}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

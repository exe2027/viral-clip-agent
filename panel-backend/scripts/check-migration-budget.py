#!/usr/bin/env python3
"""Puerta de presupuesto antes de una migración D1 (A5).

Calcula cuántas filas va a escribir una migración CONTANDO las filas
de origen con una LECTURA (desde la D1 de origen por la API, o desde
un export/SQLite local) y la RECHAZA si supera el 20% de la cuota
restante del día (cuota restante = 100.000 - escritas hoy).

NO escribe en D1: solo lee y decide.

Códigos de salida:
  0 = ACEPTADA (la migración cabe en el 20% de la cuota restante)
  2 = RECHAZADA (supera el 20%, o la estimación ya supera la cuota)
  3 = NO SE PUEDE DECIDIR (fail closed: no se pudo contar una
      sentencia, o no se pudo leer la cuota restante)

Uso:
  check-migration-budget.py migracion.sql [otra.sql ...]
      --sqlite RUTA.db            fuente local (export BK restaurado,
                                  modo solo lectura; repetible)
      --d1 UUID                   fuente D1 por API (solo SELECT;
                                  repetible). También es el destino
                                  cuando se usa --migrations-dir.
      --migrations-dir DIR --d1 UUID
                                  chequea solo las migraciones PENDIENTES
                                  (las que no están en d1_migrations).
      --remaining N               cuota restante explícita (p. ej. del
                                  panel de Cloudflare); anula la lectura
                                  de analíticas.
      --quota-source graphql|events
                                  cómo leer las escritas de hoy si no
                                  se da --remaining (defecto: graphql).
      --accept-estimate           permite decidir con la fuente events
                                  (cota inferior; sin esto, events solo
                                  puede rechazar, nunca aceptar).
      --daily-limit N             (defecto 100000)
      --max-fraction F            (defecto 0.20)
      --batch-size N              (defecto 2000; solo para el plan)
      --no-bookkeeping            no sumar la fila de d1_migrations
      --json                      salida en JSON

Ejemplos:
  # Migración 0034 pendiente, contada desde el export BK local:
  python3 scripts/check-migration-budget.py migrations/0034_x.sql \\
      --sqlite runs/phase1-2026-11-20/BK/clips-avc-sebastian.test.db
  # Todo lo pendiente en la D1 de sebastian, cuota por GraphQL:
  python3 scripts/check-migration-budget.py \\
      --migrations-dir migrations --d1 ee97f58d-0b56-4617-9976-fcf0022edb54
"""
import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import d1_migration_lib as lib


def _fmt(n):
    return f"{n:,}".replace(",", ".")


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("migrations", nargs="*", help="archivos .sql a chequear")
    ap.add_argument("--sqlite", action="append", default=[],
                    help="SQLite local (solo lectura) para los COUNT")
    ap.add_argument("--d1", action="append", default=[],
                    help="UUID de D1 (API, solo SELECT) para los COUNT")
    ap.add_argument("--migrations-dir",
                    help="chequear solo las migraciones pendientes de este dir")
    ap.add_argument("--remaining", type=int, default=None)
    ap.add_argument("--quota-source", choices=["graphql", "events"],
                    default="graphql")
    ap.add_argument("--accept-estimate", action="store_true")
    ap.add_argument("--daily-limit", type=int, default=lib.DAILY_LIMIT)
    ap.add_argument("--max-fraction", type=float, default=lib.MAX_FRACTION)
    ap.add_argument("--batch-size", type=int, default=lib.BATCH_SIZE)
    ap.add_argument("--no-bookkeeping", action="store_true")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    files, bookkeeping = [], not args.no_bookkeeping
    if args.migrations_dir:
        if not args.d1:
            print("FALLO: --migrations-dir exige --d1 <uuid> (destino, "
                  "para leer d1_migrations)", file=sys.stderr)
            return 3
        try:
            files = lib.pending_migrations(args.migrations_dir, args.d1[0])
        except Exception as e:
            print(f"NO SE PUEDE DECIDIR: no se pudo leer d1_migrations "
                  f"de {args.d1[0]}: {e}", file=sys.stderr)
            return 3
        if not files:
            print("OK: sin migraciones pendientes (0 filas estimadas).")
            return 0
    files += args.migrations
    if not files:
        print("FALLO: no se dieron migraciones ni --migrations-dir",
              file=sys.stderr)
        return 2
    for f in files:
        if not os.path.isfile(f):
            print(f"FALLO: no existe: {f}", file=sys.stderr)
            return 2

    # --- fuente de conteo (solo lecturas) ---
    counters = [lib.make_sqlite_counter(p) for p in args.sqlite]
    counters += [lib.make_d1_counter(u) for u in args.d1]
    counter = lib.make_multi_counter(counters) if counters else None

    # --- estimación de filas ---
    total, detail, unknown = 0, [], []
    for f in files:
        sql = open(f, encoding="utf-8").read()
        est = lib.estimate_sql(sql, counter, bookkeeping=bookkeeping)
        detail.append({"file": f, "rows": est["total"],
                       "unknown": est["unknown"],
                       "per_statement": est["per_statement"]})
        if est["total"] is None:
            unknown += [f"{f}: {u}" for u in est["unknown"]]
        else:
            total += est["total"]
    if unknown:
        _report(args, {"decision": "UNDECIDED", "estimated_rows": None,
                       "unknown": unknown, "files": detail})
        print("NO SE PUEDE DECIDIR: hay sentencias que no se pudieron "
              "contar (ver arriba). No se aplica la migración.",
              file=sys.stderr)
        return 3

    # --- cuota restante ---
    remaining, quota_note, quota_estimate = None, "", False
    if args.remaining is not None:
        remaining = args.remaining
        quota_note = "cuota restante explícita (--remaining)"
    elif args.quota_source == "graphql":
        try:
            g = lib.graphql_rows_written_today()
            remaining = args.daily_limit - g["total"]
            quota_note = (f"GraphQL d1QueriesAdaptiveGroups {g['date_utc']}: "
                          f"{_fmt(g['total'])} escritas hoy en la cuenta "
                          f"({', '.join(f'{k[:8]}:{v}' for k, v in g['per_db'].items())})")
        except Exception as e:
            _report(args, {"decision": "UNDECIDED", "estimated_rows": total,
                           "error": f"no se pudo leer la cuota por GraphQL: {e}",
                           "files": detail})
            print("NO SE PUEDE DECIDIR: no se pudo leer la cuota restante "
                  f"por GraphQL: {e}", file=sys.stderr)
            return 3
    else:  # events
        if not counters:
            print("FALLO: --quota-source events exige --sqlite o --d1 "
                  "(fuente para contar eventos)", file=sys.stderr)
            return 3
        ev = lib.events_written_today_estimate(counter)
        remaining = args.daily_limit - ev["total"]
        quota_note = (f"eventos de hoy (COTA INFERIOR, {ev['per_table']}): "
                      f"al menos {_fmt(ev['total'])} escritas")
        quota_estimate = True

    threshold = int(args.max_fraction * remaining)
    batches = (total + args.batch_size - 1) // args.batch_size if total else 0
    result = {"decision": None, "estimated_rows": total,
              "daily_limit": args.daily_limit,
              "remaining_today": remaining,
              "max_fraction": args.max_fraction,
              "threshold_rows": threshold,
              "batch_size": args.batch_size, "batches": batches,
              "quota_note": quota_note, "files": detail}

    if total > remaining:
        result["decision"] = "REJECTED"
        _report(args, result)
        print(f"RECHAZADA: la migración escribiría {_fmt(total)} filas y "
              f"solo quedan {_fmt(remaining)} hoy ({quota_note}). "
              f"Espera al reset (21:00 -03) o pártela en lotes.",
              file=sys.stderr)
        return 2
    if total > threshold:
        result["decision"] = "REJECTED"
        _report(args, result)
        print(f"RECHAZADA: la migración escribiría {_fmt(total)} filas, "
              f"más del {args.max_fraction:.0%} de la cuota restante "
              f"({_fmt(remaining)} -> tope {_fmt(threshold)}). {quota_note}. "
              f"Espera al reset (21:00 -03) o pártela en lotes de "
              f"{_fmt(args.batch_size)}.", file=sys.stderr)
        return 2
    if quota_estimate and not args.accept_estimate:
        result["decision"] = "UNDECIDED"
        _report(args, result)
        print("NO SE PUEDE DECIDIR: la cuota restante es una ESTIMACIÓN "
              "(cota inferior desde las tablas de eventos) y no se pasó "
              "--accept-estimate. La estimación cabe, pero la cifra real "
              "puede ser mayor: confirma con --remaining o con la fuente "
              "graphql antes de aplicar.", file=sys.stderr)
        return 3

    result["decision"] = "ACCEPTED"
    _report(args, result)
    if not args.json:
        print(f"ACEPTADA: {_fmt(total)} filas estimadas, tope "
              f"{_fmt(threshold)} ({args.max_fraction:.0%} de {_fmt(remaining)} "
              f"restantes). {quota_note}. Plan: {batches} lote(s) de hasta "
              f"{_fmt(args.batch_size)} filas, con verificación entre lotes.")
    return 0


def _report(args, result):
    if args.json:
        print(json.dumps(result, indent=2, ensure_ascii=False))
    else:
        for f in result.get("files", []):
            print(f"  {f['file']}: "
                  f"{'?' if f['rows'] is None else _fmt(f['rows'])} filas")
            for st in f["per_statement"]:
                print(f"    - {st['note']}")
        for u in result.get("unknown", []):
            print(f"  SIN CONTAR: {u}")
        if result.get("estimated_rows") is not None:
            print(f"  Total estimado: {_fmt(result['estimated_rows'])} filas; "
                  f"restantes hoy: {_fmt(result['remaining_today'])}; "
                  f"tope ({result['max_fraction']:.0%}): "
                  f"{_fmt(result['threshold_rows'])}; "
                  f"lotes de {result['batch_size']}: {result['batches']}")
        if result.get("error"):
            print(f"  Error: {result['error']}")


if __name__ == "__main__":
    raise SystemExit(main())

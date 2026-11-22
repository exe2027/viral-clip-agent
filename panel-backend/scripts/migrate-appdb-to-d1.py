#!/usr/bin/env python3
"""Migra app.db (SQLite local del panel v17) a D1, por tenant.

Uso:
    python3 scripts/migrate-appdb-to-d1.py --db /ruta/a/app.db \\
        --tenant sebastian --out /tmp/migracion-sebastian.sql
    wrangler d1 execute clips-sebastian --file /tmp/migracion-sebastian.sql \\
        --config tenants/sebastian/wrangler.toml --remote

REGLAS DE SEGURIDAD (no negociables):
- La base se abre en modo SOLO LECTURA (URI mode=ro): nunca modifica app.db.
- Las credenciales NO viajan: api_accounts.credential y .extra se
  escriben VACÍAS en la salida, y credential_enc/extra_enc también.
  El informe lista qué cuentas hay que volver a pegar en el panel
  nuevo (id, plataforma, etiqueta; nunca el valor).
- Al final, el script comprueba que ningún valor redactado aparece
  en el SQL generado; si aparece, aborta sin escribir el archivo.
- No se guarda ningún secreto en el repo: la salida va a /tmp (o a
  la ruta que se pase con --out), nunca a este directorio.

D1: la salida es SQL estándar (INSERT ... VALUES). Si la base es
grande, --split N parte la salida en archivos de N sentencias.
"""
import argparse
import hashlib
import sqlite3
import sys
from pathlib import Path

# Tablas que llevan tenant_id (migración 0012): el script añade la
# columna con el --tenant en cada INSERT.
TENANT_TABLES = {
    "settings", "accounts", "influencers", "sources", "clips",
    "vod_checks", "clip_previews", "panel_users", "worker_state",
    "clip_stats", "api_accounts", "worker_events", "clip_events",
}
# Tablas del panel v17 (migraciones 0001-0010) + 0011 del backend.
TABLES = [
    "settings", "accounts", "influencers", "sources", "clips",
    "vod_checks", "clip_previews", "panel_users", "worker_state",
    "clip_stats", "api_accounts", "worker_events", "clip_events",
]
# Columnas que se redactan (se escriben vacías) y nunca salen del script.
REDACTED = {("api_accounts", "credential"), ("api_accounts", "extra"),
            ("api_accounts", "credential_enc"), ("api_accounts", "extra_enc")}


def sql_literal(value):
    if value is None:
        return "NULL"
    if isinstance(value, bool):
        return "1" if value else "0"
    if isinstance(value, (int, float)):
        return repr(value)
    if isinstance(value, bytes):
        return "X'" + value.hex() + "'"
    return "'" + str(value).replace("'", "''") + "'"


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--db", required=True, help="ruta a app.db (solo lectura)")
    ap.add_argument("--tenant", required=True, choices=["sebastian", "kaez"])
    ap.add_argument("--out", required=True, help="archivo SQL de salida")
    ap.add_argument("--split", type=int, default=0, help="partir cada N sentencias (0 = un archivo)")
    args = ap.parse_args()

    db_path = Path(args.db)
    if not db_path.is_file():
        print(f"no existe: {db_path}", file=sys.stderr)
        return 2
    sha = hashlib.sha256(db_path.read_bytes()).hexdigest()
    con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row

    existing = {r[0] for r in con.execute(
        "SELECT name FROM sqlite_master WHERE type='table'")}
    statements: list[str] = []
    report: list[str] = []
    redacted_values: list[str] = []
    redacted_accounts: list[str] = []

    header = (f"-- Migración app.db → D1 | tenant={args.tenant} | "
              f"origen sha256={sha}\n"
              f"-- Generado por migrate-appdb-to-d1.py. CREDENCIALES REDACTADAS:\n"
              f"-- api_accounts.credential / .extra salen VACÍAS: hay que volver\n"
              f"-- a pegar cada credencial en el panel nuevo (Cuentas → Verificar).\n")
    statements.append("BEGIN TRANSACTION;")

    for table in TABLES:
        if table not in existing:
            report.append(f"{table}: no existe en app.db, se salta")
            continue
        cols = [r[1] for r in con.execute(f"PRAGMA table_info({table})")]
        rows = con.execute(f"SELECT * FROM {table}").fetchall()
        n = 0
        for row in rows:
            vals = []
            for c in cols:
                v = row[c]
                if (table, c) in REDACTED:
                    if isinstance(v, str) and v.strip():
                        redacted_values.append(v)
                    vals.append("''")
                else:
                    vals.append(sql_literal(v))
            if table in TENANT_TABLES:
                cols = [*cols, "tenant_id"]
                vals = [*vals, sql_literal(args.tenant)]
            statements.append(
                f"INSERT INTO {table} ({', '.join(cols)}) VALUES ({', '.join(vals)});")
            n += 1
            if table == "api_accounts":
                redacted_accounts.append(
                    f"  - id={row['id']} plataforma={row['platform']} "
                    f"etiqueta={row['label']} estado={row['status']}")
        report.append(f"{table}: {n} filas")

    statements.append("COMMIT;")
    body = "\n".join(statements)
    full = header + body + "\n"

    # Comprobación: ningún valor redactado aparece en la salida.
    for secret in redacted_values:
        if secret and secret in full:
            print("ABORTO: un valor redactado aparece en la salida generada.",
                  file=sys.stderr)
            return 3

    out = Path(args.out)
    if args.split and len(statements) > args.split:
        base = out.with_suffix("")
        for i in range(0, len(statements), args.split):
            part = out.with_name(f"{base.name}-{i // args.split + 1:03d}.sql")
            part.write_text(header + "\n".join(statements[i:i + args.split]) + "\n",
                            encoding="utf-8")
            print(f"escrito {part} ({min(args.split, len(statements) - i)} sentencias)")
    else:
        out.write_text(full, encoding="utf-8")
        print(f"escrito {out} ({len(statements)} sentencias)")

    print(f"\nOrigen: {db_path} (sha256 {sha[:16]}…), tenant {args.tenant}")
    print("Filas migradas:")
    for line in report:
        print(f"  {line}")
    print("\nCREDENCIALES REDACTADAS — volver a pegar en el panel nuevo:")
    if redacted_accounts:
        print("\n".join(redacted_accounts))
    else:
        print("  (ninguna cuenta con credencial en app.db)")
    print("\nSiguiente paso: wrangler d1 execute clips-avc --file <salida> "
          "--config wrangler.toml --remote")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

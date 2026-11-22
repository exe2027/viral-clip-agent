#!/usr/bin/env python3
"""Tests de A5: chequeo previo de migraciones y partido en lotes.

Solo lecturas: los conteos usan SQLite temporales o los .test.db del
BK (modo solo lectura). NUNCA escriben en la D1 real ni usan red.
Incluye también el dry-run de provision-tenant.sh (cron apagado) y
el strip de triggers de deploy_with_token.py.

Corre:  python3 -m unittest test_check_migration_budget -v
desde scripts/ (o: python3 scripts/test_check_migration_budget.py).
"""
import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)
import d1_migration_lib as lib  # noqa: E402

CHECK = os.path.join(HERE, "check-migration-budget.py")
SPLIT = os.path.join(HERE, "split-sql-batches.py")
BK_SEBASTIAN_DB = os.path.join(
    HERE, "..", "..", "..", "runs", "phase1-2026-11-20", "BK",
    "clips-avc-sebastian.test.db")
MIGRATIONS_DIR = os.path.join(ROOT, "migrations")


def run_check(args, **kw):
    return subprocess.run([sys.executable, CHECK] + args,
                          capture_output=True, text=True, **kw)


def run_split(args, **kw):
    return subprocess.run([sys.executable, SPLIT] + args,
                          capture_output=True, text=True, **kw)


def make_source_db(path):
    con = sqlite3.connect(path)
    con.execute("CREATE TABLE clips (id INTEGER PRIMARY KEY, state TEXT, tenant_id TEXT)")
    con.execute("CREATE TABLE jobs (id INTEGER PRIMARY KEY, state TEXT)")
    con.executemany("INSERT INTO clips VALUES (?,?,?)",
                    [(i, "ok" if i % 2 else "bad", "sebastian") for i in range(1, 101)])
    con.executemany("INSERT INTO jobs VALUES (?,?)",
                    [(i, "processing" if i <= 7 else "scheduled") for i in range(1, 51)])
    con.commit()
    con.close()


class TestSplitStatements(unittest.TestCase):
    def test_semicolon_in_string_and_comments(self):
        sql = ("-- comentario con ; dentro\n"
               "INSERT INTO t VALUES (1, 'a;b'), (2, 'c'';d');\n"
               "/* bloque ; */ CREATE INDEX i ON t(x);\n")
        stmts = lib.split_statements(sql)
        self.assertEqual(len(stmts), 2)
        self.assertIn("INSERT INTO t VALUES (1, 'a;b'), (2, 'c'';d')", stmts[0])

    def test_count_value_tuples(self):
        self.assertEqual(lib.count_value_tuples("(1,'a'),(2,'b (x)'),(3,'c')"), 3)
        self.assertEqual(lib.count_value_tuples("(1, 'x;y'), (2, 'z')"), 2)
        self.assertEqual(lib.count_value_tuples(""), 0)


class TestEstimate(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.db = os.path.join(cls.tmp.name, "src.db")
        make_source_db(cls.db)
        # staticmethod: una función como atributo de clase se ligaría
        # como método (self.counter(sql) recibiría 2 args).
        cls.counter = staticmethod(lib.make_sqlite_counter(cls.db))

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_insert_values(self):
        rows, _ = lib.estimate_statement(
            "INSERT INTO clips (id, state) VALUES (201,'ok'),(202,'ok'),(203,'ok');",
            self.counter)
        self.assertEqual(rows, 3)

    def test_insert_select_counts_source(self):
        rows, _ = lib.estimate_statement(
            "INSERT INTO jobs (id, state) SELECT id, state FROM jobs WHERE state='processing';",
            self.counter)
        self.assertEqual(rows, 7)

    def test_insert_select_where_not_exists(self):
        rows, _ = lib.estimate_statement(
            "INSERT INTO clips (id, state) SELECT 999, 'ok' "
            "WHERE NOT EXISTS (SELECT 1 FROM clips WHERE id = 999);",
            self.counter)
        self.assertEqual(rows, 1)
        rows, _ = lib.estimate_statement(
            "INSERT INTO clips (id, state) SELECT 1, 'ok' "
            "WHERE NOT EXISTS (SELECT 1 FROM clips WHERE id = 1);",
            self.counter)
        self.assertEqual(rows, 0)

    def test_update_where(self):
        rows, _ = lib.estimate_statement(
            "UPDATE jobs SET state='x' WHERE state = 'processing';",
            self.counter)
        self.assertEqual(rows, 7)

    def test_update_no_where_counts_table(self):
        rows, _ = lib.estimate_statement("UPDATE jobs SET state='x';", self.counter)
        self.assertEqual(rows, 50)

    def test_delete_where(self):
        rows, _ = lib.estimate_statement(
            "DELETE FROM clips WHERE state = 'bad';", self.counter)
        self.assertEqual(rows, 50)

    def test_ddl_zero(self):
        for sql in ("CREATE INDEX IF NOT EXISTS i ON clips(state);",
                    "ALTER TABLE clips ADD COLUMN x TEXT;",
                    "CREATE TABLE t2 (a TEXT);"):
            rows, _ = lib.estimate_statement(sql, self.counter)
            self.assertEqual(rows, 0, sql)

    def test_insert_select_without_source_is_unknown(self):
        rows, note = lib.estimate_statement(
            "INSERT INTO jobs (id) SELECT id FROM jobs;", None)
        self.assertIsNone(rows)
        self.assertIn("sin fuente", note)

    def test_unknown_statement_is_unknown(self):
        rows, _ = lib.estimate_statement("WITH x AS (SELECT 1) SELECT * FROM x;",
                                         self.counter)
        self.assertIsNone(rows)

    def test_missing_table_counts_zero(self):
        # D1 nueva y vacía: la tabla aún no existe; las migraciones
        # anteriores del lote la crean vacía, así que el backfill
        # ve 0 filas (no "no se puede decidir").
        import tempfile as _tf
        with _tf.TemporaryDirectory() as d:
            empty = os.path.join(d, "empty.db")
            sqlite3.connect(empty).close()
            counter = lib.make_sqlite_counter(empty)
            rows, note = lib.estimate_statement(
                "UPDATE jobs SET claimed_by = 'worker' WHERE state = 'processing';",
                counter)
            self.assertEqual(rows, 0)
            self.assertIn("aún no existe", note)
            rows, _ = lib.estimate_statement(
                "INSERT INTO accounts (a) SELECT a FROM accounts;",
                counter)
            self.assertEqual(rows, 0)

    def test_estimate_sql_bookkeeping(self):
        est = lib.estimate_sql("CREATE INDEX i ON clips(state);",
                               self.counter, bookkeeping=True)
        self.assertEqual(est["total"], 1)  # 0 filas + 1 d1_migrations


class TestCheckScript(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.db = os.path.join(cls.tmp.name, "src.db")
        make_source_db(cls.db)
        cls.big = os.path.join(cls.tmp.name, "big.sql")
        with open(cls.big, "w") as f:
            f.write("INSERT INTO clips (id, state) VALUES\n")
            f.write(",\n".join(f"({1000 + i},'ok')" for i in range(5000)) + ";\n")
        cls.small = os.path.join(cls.tmp.name, "small.sql")
        with open(cls.small, "w") as f:
            f.write("INSERT INTO clips (id, state) VALUES (1,'ok'),(2,'ok');\n"
                    "CREATE INDEX i ON clips(state);\n")

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_rejects_over_20_percent(self):
        # 5.000 filas, restante 10.000 -> tope 2.000 -> rechaza, exit 2.
        r = run_check([self.big, "--sqlite", self.db, "--remaining", "10000"])
        self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
        self.assertIn("RECHAZADA", r.stderr)

    def test_accepts_under_20_percent(self):
        # 5.000 filas, restante 100.000 -> tope 20.000 -> acepta, exit 0.
        r = run_check([self.big, "--sqlite", self.db, "--remaining", "100000"])
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("ACEPTADA", r.stdout)
        self.assertIn("3 lote(s)", r.stdout)  # 5000 / 2000

    def test_boundary_exactly_20_percent_accepts(self):
        # 2 filas + 1 bookkeeping = 3; restante 15 -> tope 3 -> acepta.
        r = run_check([self.small, "--sqlite", self.db, "--remaining", "15",
                       "--no-bookkeeping"])
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)

    def test_boundary_one_over_rejects(self):
        # 2 filas, restante 9 -> tope 1 (int 0.2*9) -> rechaza.
        r = run_check([self.small, "--sqlite", self.db, "--remaining", "9",
                       "--no-bookkeeping"])
        self.assertEqual(r.returncode, 2, r.stdout + r.stderr)

    def test_exceeds_remaining_rejects(self):
        r = run_check([self.big, "--sqlite", self.db, "--remaining", "4000"])
        self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
        self.assertIn("solo quedan", r.stderr)

    def test_uncountable_without_source_is_undecided(self):
        mig = os.path.join(self.tmp.name, "sel.sql")
        with open(mig, "w") as f:
            f.write("INSERT INTO jobs (id) SELECT id FROM jobs;\n")
        r = run_check([mig, "--remaining", "100000"])
        self.assertEqual(r.returncode, 3, r.stdout + r.stderr)
        self.assertIn("NO SE PUEDE DECIDIR", r.stderr)

    def test_json_output(self):
        r = run_check([self.small, "--sqlite", self.db, "--remaining", "100000",
                       "--json"])
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        data = json.loads(r.stdout)
        self.assertEqual(data["decision"], "ACCEPTED")
        self.assertEqual(data["estimated_rows"], 3)  # 2 + 1 bookkeeping

    def test_real_migrations_against_bk(self):
        # Migraciones reales 0001-0033 contadas desde el BK de sebastian.
        if not os.path.isfile(BK_SEBASTIAN_DB):
            self.skipTest("falta el .test.db del BK")
        total = 0
        for name in sorted(os.listdir(MIGRATIONS_DIR)):
            if not name.endswith(".sql"):
                continue
            sql = open(os.path.join(MIGRATIONS_DIR, name), encoding="utf-8").read()
            est = lib.estimate_sql(
                sql, lib.make_sqlite_counter(BK_SEBASTIAN_DB),
                bookkeeping=True)
            self.assertEqual(est["unknown"], [], f"{name}: {est['unknown']}")
            total += est["total"]
        # 0030 siembra 6 cuentas + 0021 backfill (0 processing en el BK)
        # + 33 filas de bookkeeping; el total real es pequeño (<100).
        self.assertLess(total, 100)
        # La 0030 sola: 6 INSERT ... SELECT (WHERE NOT EXISTS -> 0, ya
        # existen en el BK) + 1 bookkeeping = 1.
        sql = open(os.path.join(MIGRATIONS_DIR,
                                "0030_channel_handles.sql"),
                   encoding="utf-8").read()
        est = lib.estimate_sql(sql, lib.make_sqlite_counter(BK_SEBASTIAN_DB),
                               bookkeeping=True)
        self.assertEqual(est["total"], 1)


class TestSplitter(unittest.TestCase):
    def test_split_4500_into_3_batches(self):
        with tempfile.TemporaryDirectory() as d:
            src = os.path.join(d, "mig.sql")
            with open(src, "w") as f:
                f.write("CREATE TABLE t (id INTEGER, v TEXT);\n")
                f.write("INSERT INTO t VALUES\n")
                f.write(",\n".join(f"({i},'x')" for i in range(4500)) + ";\n")
            out = os.path.join(d, "out")
            r = run_split([src, "--out-dir", out])
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            with open(os.path.join(out, "mig.batches.json"),
                      encoding="utf-8") as f:
                man = json.load(f)
            self.assertEqual(len(man["batches"]), 3)
            self.assertEqual([b["rows"] for b in man["batches"]],
                             [2000, 2000, 500])
            self.assertEqual(man["total_rows"], 4500)
            # Cada lote es transacción propia y aplica limpio (el
            # lote 1 crea la tabla; no pre-crearla en el test).
            db = os.path.join(d, "test.db")
            con = sqlite3.connect(db)
            for b in man["batches"]:
                with open(os.path.join(out, b["file"]),
                          encoding="utf-8") as f:
                    con.executescript(f.read())
            self.assertEqual(
                con.execute("SELECT COUNT(*) FROM t").fetchone()[0], 4500)
            con.close()

    def test_verify_flow_ok_and_tampered(self):
        with tempfile.TemporaryDirectory() as d:
            src = os.path.join(d, "mig.sql")
            with open(src, "w") as f:
                f.write("INSERT INTO t VALUES\n")
                f.write(",\n".join(f"({i},'x')" for i in range(2500)) + ";\n")
            out = os.path.join(d, "out")
            self.assertEqual(run_split([src, "--out-dir", out]).returncode, 0)
            man_path = os.path.join(out, "mig.batches.json")
            db = os.path.join(d, "test.db")
            con = sqlite3.connect(db)
            con.execute("CREATE TABLE t (id INTEGER, v TEXT)")
            con.commit()
            con.close()
            base = os.path.join(d, "baseline.json")
            r = run_split(["--record-baseline", "--sqlite", db,
                           "--manifest", man_path, "--out", base])
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            # Aplicar lote 1 y verificar: OK.
            con = sqlite3.connect(db)
            with open(os.path.join(out, "mig.batch-001.sql"),
                      encoding="utf-8") as f:
                con.executescript(f.read())
            con.close()
            r = run_split(["--verify", "--sqlite", db, "--manifest", man_path,
                           "--baseline", base, "--through-batch", "1"])
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            self.assertIn("VERIFICADO", r.stdout)
            # Manipular (borrar 5 filas) y verificar: FALLA y pide parar.
            con = sqlite3.connect(db)
            con.execute("DELETE FROM t WHERE id < 5")
            con.commit()
            con.close()
            r = run_split(["--verify", "--sqlite", db, "--manifest", man_path,
                           "--baseline", base, "--through-batch", "1"])
            self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
            self.assertIn("PARAR", r.stderr)

    def test_split_packs_small_dml_with_source(self):
        # Con fuente, los UPDATE/DELETE contables se empaquetan en
        # el lote en curso en vez de ir cada uno solo.
        with tempfile.TemporaryDirectory() as d:
            src_db = os.path.join(d, "src.db")
            make_source_db(src_db)
            src = os.path.join(d, "mig.sql")
            with open(src, "w") as f:
                f.write("UPDATE jobs SET state='x' WHERE state='processing';\n"
                        "DELETE FROM clips WHERE state='bad';\n"
                        "INSERT INTO clips (id, state) VALUES (201,'ok');\n")
            out = os.path.join(d, "out")
            r = run_split([src, "--out-dir", out, "--sqlite", src_db])
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            with open(os.path.join(out, "mig.batches.json"),
                      encoding="utf-8") as f:
                man = json.load(f)
            # 7 (UPDATE) + 50 (DELETE) + 1 (INSERT) = 58, en UN lote.
            self.assertEqual(len(man["batches"]), 1)
            self.assertEqual(man["total_rows"], 58)
            # Neto para la verificación: UPDATE 0, DELETE -50, INSERT +1.
            self.assertEqual(man["batches"][0]["net"],
                             {"jobs": 0, "clips": -49})

    def test_split_keeps_ddl_in_first_batch(self):
        with tempfile.TemporaryDirectory() as d:
            src = os.path.join(d, "mig.sql")
            with open(src, "w") as f:
                f.write("CREATE TABLE t (id INTEGER);\n"
                        "CREATE INDEX i ON t(id);\n"
                        "INSERT INTO t VALUES (1),(2);\n")
            out = os.path.join(d, "out")
            self.assertEqual(run_split([src, "--out-dir", out]).returncode, 0)
            with open(os.path.join(out, "mig.batches.json"),
                      encoding="utf-8") as f:
                man = json.load(f)
            self.assertEqual(len(man["batches"]), 1)
            self.assertEqual(man["total_rows"], 2)


class TestProvisionDryRun(unittest.TestCase):
    def test_new_tenant_deploys_without_cron(self):
        """El toml generado en dry-run no debe llevar cron y el
        resumen debe decir que el Worker nace apagado."""
        r = subprocess.run(
            ["bash", "scripts/provision-tenant.sh", "luis-prueba",
             "--dry-run"],
            capture_output=True, text=True, cwd=ROOT)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        out = r.stdout + r.stderr
        self.assertIn("sin [triggers]", out)
        self.assertIn("cron apagado", out.lower())
        self.assertNotIn("crons = [\"* * * * *\"]", out)

    def test_enable_cron_dry_run(self):
        r = subprocess.run(
            ["bash", "scripts/provision-tenant.sh", "sebastian",
             "--enable-cron", "--dry-run"],
            capture_output=True, text=True, cwd=ROOT)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        out = r.stdout + r.stderr
        self.assertIn("enable-cron", out.lower())
        self.assertIn("worker_running=false", out)


class TestDeployScript(unittest.TestCase):
    def test_strip_triggers(self):
        sys.path.insert(0, HERE)
        import deploy_with_token  # noqa: E402
        toml = ('name = "avc-x"\n[triggers]\ncrons = ["* * * * *"]\n\n'
                '[[d1_databases]]\nbinding = "DB"\n')
        stripped = deploy_with_token.strip_triggers(toml)
        self.assertNotIn("crons", stripped)
        self.assertIn('[[d1_databases]]', stripped)
        self.assertIn("Cron APAGADO", stripped)
        self.assertTrue(deploy_with_token.toml_has_triggers(toml))
        self.assertFalse(deploy_with_token.toml_has_triggers(stripped))
        # Idempotente: sin triggers, no cambia (salvo no añadir nada).
        self.assertEqual(deploy_with_token.strip_triggers(stripped), stripped)

    def test_strip_triggers_real_template(self):
        sys.path.insert(0, HERE)
        import deploy_with_token  # noqa: E402
        text = open(os.path.join(ROOT, "wrangler.sebastian.toml"),
                    encoding="utf-8").read()
        self.assertTrue(deploy_with_token.toml_has_triggers(text))
        stripped = deploy_with_token.strip_triggers(text)
        self.assertFalse(deploy_with_token.toml_has_triggers(stripped))
        self.assertNotIn("crons =", stripped)
        self.assertIn("Cron APAGADO", stripped)
        self.assertIn('name = "avc-sebastian"', stripped)
        self.assertIn('TENANT = "sebastian"', stripped)


if __name__ == "__main__":
    unittest.main(verbosity=2)

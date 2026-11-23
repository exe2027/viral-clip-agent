#!/usr/bin/env python3
"""Pruebas de la migración D1 0041 campaign_submissions (2026-11-23).

Sin D1 real: SQLite en memoria aplica el archivo de migración tal
cual y verifica el contrato que usan el Worker y la extensión:
UNIQUE (tenant, clip, plataforma, campaña) = un post, una campaña,
una sola vez; estados pending/submitted/failed/expired; la ventana
de 30 min (expiración por SQL); y la cola de pendientes ordenada
por published_at_ms (la más urgente primero).
"""
import os
import sqlite3
import unittest

BASE = os.path.dirname(os.path.abspath(__file__))
MIGRATION = os.path.join(BASE, "panel-backend", "migrations",
                         "0041_campaign_submissions.sql")
WINDOW_MS = 30 * 60 * 1000
DASGASD3M = "051002dd-5c46-43d0-a446-43f2957ba3a9"


def make_db():
    con = sqlite3.connect(":memory:")
    con.executescript(open(MIGRATION, encoding="utf-8").read())
    return con


def insert(con, tenant="sebastian", clip_id=174, platform="tiktok",
           campaign=DASGASD3M, status="pending", published_at_ms=None,
           post_url=None):
    cur = con.execute(
        "INSERT INTO campaign_submissions (tenant_id, clip_id, platform,"
        " marketplace, campaign_id, campaign_name, streamer,"
        " gross_rate_per_1k, net_rate_per_1k, min_views, cap_per_clip,"
        " post_url, published_at_ms, status, submission_id, views,"
        " amount_usd, error, created_at, updated_at)"
        " VALUES (?, ?, ?, 'content_rewards', ?, ?, ?, 2.0, 1.8, 10000,"
        " 150, ?, ?, ?, NULL, NULL, NULL, NULL, 1000, 1000)",
        (tenant, clip_id, platform, campaign, "Dasgasd3m", "dasgasd3m",
         post_url, published_at_ms, status))
    con.commit()
    return cur.lastrowid


class TestMigration0041(unittest.TestCase):
    def test_archivo_existe_y_sigue_a_0040(self):
        self.assertTrue(os.path.isfile(MIGRATION))
        prev = os.path.join(BASE, "panel-backend", "migrations",
                            "0040_engagement_zernio.sql")
        self.assertTrue(os.path.isfile(prev))
        sql = open(MIGRATION, encoding="utf-8").read()
        self.assertIn("CREATE TABLE IF NOT EXISTS campaign_submissions",
                      sql)
        self.assertIn("idx_campaign_submissions_unique", sql)
        self.assertIn("idx_campaign_submissions_pending", sql)

    def test_unique_bloquea_el_segundo_envio_del_mismo_post(self):
        con = make_db()
        insert(con)
        with self.assertRaises(sqlite3.IntegrityError):
            insert(con)
        self.assertEqual(
            con.execute("SELECT COUNT(*) FROM campaign_submissions"
                        ).fetchone()[0], 1)

    def test_misma_campana_otra_plataforma_u_otro_tenant_no_es_duplicado(self):
        con = make_db()
        insert(con)
        insert(con, platform="youtube")
        insert(con, tenant="kaez")
        insert(con, campaign="6e9ca548-f36c-42c2-abc0-61f89b4b53ba")
        self.assertEqual(
            con.execute("SELECT COUNT(*) FROM campaign_submissions"
                        ).fetchone()[0], 4)

    def test_status_solo_los_cuatro_validos(self):
        con = make_db()
        with self.assertRaises(sqlite3.IntegrityError):
            insert(con, status="enviado")

    def test_expiracion_30_min_por_sql(self):
        con = make_db()
        now = 1795300000000
        old = insert(con, clip_id=1, published_at_ms=now - WINDOW_MS - 1,
                     post_url="https://www.tiktok.com/@x/video/1")
        fresh = insert(con, clip_id=2, published_at_ms=now - 1000,
                       post_url="https://www.tiktok.com/@x/video/2")
        no_pub = insert(con, clip_id=3)
        changed = con.execute(
            "UPDATE campaign_submissions SET status = 'expired',"
            " updated_at = ? WHERE tenant_id = ? AND status = 'pending'"
            " AND published_at_ms IS NOT NULL"
            " AND published_at_ms + ? < ?",
            (now, "sebastian", WINDOW_MS, now)).rowcount
        con.commit()
        self.assertEqual(changed, 1)
        def get(i):
            return con.execute(
                "SELECT status FROM campaign_submissions WHERE id = ?",
                (i,)).fetchone()[0]
        self.assertEqual(get(old), "expired")
        self.assertEqual(get(fresh), "pending")
        self.assertEqual(get(no_pub), "pending")

    def test_cola_pendiente_ordenada_por_urgencia(self):
        con = make_db()
        now = 1795300000000
        insert(con, clip_id=1, published_at_ms=now - 20 * 60 * 1000,
               post_url="https://www.tiktok.com/@x/video/1")
        insert(con, clip_id=2, published_at_ms=now - 5 * 60 * 1000,
               post_url="https://www.tiktok.com/@x/video/2")
        insert(con, clip_id=3)  # sin publicar: no es enviable
        rows = con.execute(
            "SELECT clip_id FROM campaign_submissions"
            " WHERE tenant_id = ? AND status = 'pending'"
            " AND published_at_ms IS NOT NULL"
            " ORDER BY published_at_ms ASC",
            ("sebastian",)).fetchall()
        self.assertEqual([r[0] for r in rows], [1, 2])

    def test_lectura_por_clip_plataforma_campana(self):
        con = make_db()
        insert(con, clip_id=174, platform="tiktok")
        insert(con, clip_id=174, platform="youtube")
        rows = con.execute(
            "SELECT platform FROM campaign_submissions"
            " WHERE tenant_id = ? AND clip_id = ? AND platform = ?"
            " AND campaign_id = ?",
            ("sebastian", 174, "tiktok", DASGASD3M)).fetchall()
        self.assertEqual(len(rows), 1)


if __name__ == "__main__":
    unittest.main()

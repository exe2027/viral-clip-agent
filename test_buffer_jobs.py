#!/usr/bin/env python3
"""Tests B3 — lógica de planificación del colchón de 7 días.

Solo stdlib (unittest). NO tocan la D1 real: todo es planificación
pura sobre fixtures en memoria, o dry-run con la escritura
bloqueada. Ejecutar:
  python3 test_buffer_jobs.py
o:
  python3 -m unittest test_buffer_jobs -v
"""
import sys
import unittest
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo
from unittest import mock

sys.path.insert(0, __file__.rsplit("/", 1)[0])
import buffer_config as cfg
import buffer_jobs as bj

STGO = ZoneInfo("America/Santiago")
START = date(2026, 11, 20)  # viernes


def clip(cid, lang="es", viral=60, seo=None):
    return {"clip_id": cid, "language": lang, "virality": viral,
            "seo": seo, "title": f"clip {cid}"}


def entry(day, hh, mm, cid, key, state="scheduled", origin="existing"):
    dt = datetime.combine(day, datetime.min.time().replace(hour=hh, minute=mm),
                          tzinfo=STGO)
    return {"date": day, "dt": dt, "clip_id": cid, "account_key": key,
            "platform": cfg.ACCOUNT_BY_KEY[key]["platform"],
            "state": state, "origin": origin}


def fresh_accounts(warmup=True):
    """Copia de ACCOUNTS con warmup configurable (los tests no mutan
    el config global)."""
    return [dict(a, warmup=warmup) for a in cfg.ACCOUNTS]


class TestRhythmCaps(unittest.TestCase):
    def test_warmup_one_per_day(self):
        """Cuenta nueva: máx 1/día aunque haya 10 clips aprobados."""
        approved = [clip(i, viral=90 - i) for i in range(1, 11)]
        sched, nuevos, _ = bj.plan_buffer(approved, [], START, horizon=2)
        for a in cfg.ACCOUNTS:
            for offset in range(2):
                day = START + timedelta(days=offset)
                n = len([s for s in sched if s["account_key"] == a["key"]
                         and s["date"] == day])
                self.assertLessEqual(n, 1, f"{a['key']} {day}: {n}")

    def test_buffer_max_two_per_day_non_warmup(self):
        """Sin calentamiento: el colchón nunca pasa de 2/cuenta/día
        aunque el techo de TikTok sea 3."""
        with mock.patch.object(bj, "ACCOUNTS", fresh_accounts(warmup=False)):
            with mock.patch.object(cfg, "ACCOUNTS",
                                  fresh_accounts(warmup=False)):
                approved = [clip(i, viral=90 - i) for i in range(1, 11)]
                sched, nuevos, _ = bj.plan_buffer(approved, [], START,
                                                  horizon=1)
                for a in fresh_accounts(warmup=False):
                    n = len([s for s in sched
                             if s["account_key"] == a["key"]
                             and s["date"] == START])
                    self.assertLessEqual(n, cfg.BUFFER_MAX_PER_DAY,
                                         f"{a['key']}: {n}")

    def test_youtube_spacing_4h(self):
        """YouTube: a las 3 h de la última subida frena; a las 4 h pasa."""
        day = START + timedelta(days=1)  # sábado (YT ES no descansa)
        existing = [entry(day, 12, 0, 1, "youtube_es", "posted")]
        yt = cfg.ACCOUNT_BY_KEY["youtube_es"]
        # daily_cap=3 para aislar el espaciamiento del tope diario.
        with mock.patch.object(bj, "daily_cap", return_value=3):
            t3 = datetime.combine(day, datetime.min.time().replace(hour=15),
                                  tzinfo=STGO)
            ok, reason = bj.can_place(existing, yt, t3, 2)
            self.assertFalse(ok, "3 h debería frenar en YouTube")
            self.assertIn("espaciamiento", reason)
            t4 = datetime.combine(day, datetime.min.time().replace(hour=16),
                                  tzinfo=STGO)
            ok, _ = bj.can_place(existing, yt, t4, 2)
            self.assertTrue(ok, "4 h debería pasar en YouTube")

    def test_general_spacing_3h(self):
        """Resto: a las 2 h frena; a las 3 h pasa."""
        day = START + timedelta(days=1)
        existing = [entry(day, 12, 0, 1, "tiktok_es", "posted")]
        tt = cfg.ACCOUNT_BY_KEY["tiktok_es"]
        with mock.patch.object(bj, "daily_cap", return_value=3):
            t2 = datetime.combine(day, datetime.min.time().replace(hour=14),
                                  tzinfo=STGO)
            ok, reason = bj.can_place(existing, tt, t2, 2)
            self.assertFalse(ok, "2 h debería frenar")
            self.assertIn("espaciamiento", reason)
            t3 = datetime.combine(day, datetime.min.time().replace(hour=15),
                                  tzinfo=STGO)
            ok, _ = bj.can_place(existing, tt, t3, 2)
            self.assertTrue(ok, "3 h debería pasar")

    def test_cross_platform_15min(self):
        """Mismo clip en distintas plataformas el mismo día: >= 15 min."""
        approved = [clip(i, viral=90 - i) for i in range(1, 11)]
        sched, _, _ = bj.plan_buffer(approved, [], START, horizon=3)
        for cid in range(1, 11):
            ts = sorted(s["dt"] for s in sched if s["clip_id"] == cid)
            for a, b in zip(ts, ts[1:]):
                if a.date() == b.date():
                    self.assertGreaterEqual(
                        (b - a).total_seconds(), 15 * 60,
                        f"clip {cid}: {a} -> {b}")

    def test_slots_only(self):
        """Toda hora planeada cae dentro de 12-14 / 18-20 / 21-23 Chile."""
        approved = [clip(i, viral=90 - i) for i in range(1, 11)]
        sched, _, _ = bj.plan_buffer(approved, [], START, horizon=7)
        for s in sched:
            hm = s["dt"].hour + s["dt"].minute / 60
            ok = (12 <= hm < 14) or (18 <= hm < 20) or (21 <= hm < 23)
            self.assertTrue(ok, f"{s['account_key']} {s['dt']}")

    def test_rest_day_respected(self):
        """Ninguna cuenta publica en su día libre semanal."""
        approved = [clip(i, viral=90 - i) for i in range(1, 11)]
        sched, _, _ = bj.plan_buffer(approved, [], START, horizon=7)
        for s in sched:
            self.assertNotEqual(s["date"].weekday(),
                                cfg.REST_WEEKDAY[s["account_key"]],
                                f"{s['account_key']} {s['date']}")

    def test_rolling_24h_max_3(self):
        """Con 3 publicaciones en las últimas 24 h, la cuenta no
        recibe ni una más (techo duro, también a mano)."""
        day = START + timedelta(days=1)  # sábado
        existing = [
            entry(START, 12, 0, 1, "tiktok_es", "posted"),
            entry(START, 18, 0, 2, "tiktok_es", "posted"),
            entry(START, 21, 0, 3, "tiktok_es", "posted"),
        ]
        # Sábado 11:00: los 3 del viernes siguen dentro de 24 h.
        when = datetime.combine(day, datetime.min.time().replace(hour=11),
                                tzinfo=STGO)
        ok, reason = bj.can_place(existing, cfg.ACCOUNT_BY_KEY["tiktok_es"],
                                  when, 99)
        self.assertFalse(ok)
        self.assertIn("rolling", reason)

    def test_no_duplicate_clip_same_account_same_day(self):
        approved = [clip(i, viral=90 - i) for i in range(1, 11)]
        sched, _, _ = bj.plan_buffer(approved, [], START, horizon=7)
        seen = set()
        for s in sched:
            k = (s["date"], s["account_key"], s["clip_id"])
            self.assertNotIn(k, seen, f"duplicado {k}")
            seen.add(k)

    def test_no_repeat_clip_on_same_account(self):
        """Regla 5: un clip no se repite nunca en la misma cuenta,
        ni otro día. Con 1 solo clip aprobado, cada cuenta lo usa
        como mucho 1 vez en 7 días."""
        approved = [clip(1, viral=90)]
        sched, _, _ = bj.plan_buffer(approved, [], START, horizon=7)
        for a in cfg.ACCOUNTS:
            n = len([s for s in sched if s["account_key"] == a["key"]])
            self.assertLessEqual(n, 1, f"{a['key']}: {n}")
        self.assertLessEqual(len(sched), len(cfg.ACCOUNTS))

    def test_gates_filter(self):
        """Viralidad <45 o SEO <80 (cuando se conoce) no entran."""
        approved = [clip(1, viral=44), clip(2, viral=60, seo=79),
                    clip(3, viral=60, seo=80), clip(4, viral=60, seo=None)]
        sched, _, _ = bj.plan_buffer(approved, [], START, horizon=2)
        placed = {s["clip_id"] for s in sched}
        self.assertNotIn(1, placed)
        self.assertNotIn(2, placed)
        self.assertIn(3, placed)
        self.assertIn(4, placed)  # SEO sin medir: fail-open, como el gate


class TestRecovery(unittest.TestCase):
    def _lost_day_schedule(self):
        """Sábado 21-11 con 4 publicaciones programadas (como el plan)."""
        sat = START + timedelta(days=1)
        return [
            entry(sat, 12, 0, 11, "youtube_es"),
            entry(sat, 12, 5, 11, "tiktok_es"),
            entry(sat, 12, 10, 23, "instagram_es"),
            entry(sat, 12, 15, 11, "facebook_es"),
        ]

    def test_recovery_replans_within_rhythm(self):
        approved = [clip(11, viral=54), clip(23, viral=78)]
        sched, placed, unplaced = bj.recover_lost(
            self._lost_day_schedule(), START + timedelta(days=1),
            approved, START, horizon=7)
        self.assertEqual(len(placed), 4)
        self.assertEqual(unplaced, [])
        for s in placed:
            self.assertGreater(s["date"], START + timedelta(days=1))
            ok, _ = bj.can_place(
                [x for x in sched if x is not s],
                cfg.ACCOUNT_BY_KEY[s["account_key"]], s["dt"], s["clip_id"])
            # can_place sobre el schedule final sin el propio ítem:
            # debe pasar (el hueco era libre cuando se colocó).
            self.assertTrue(ok, f"{s} no respeta el ritmo")

    def test_recovery_never_six_at_once(self):
        """Se pierden 6 huecos de la MISMA cuenta: el colchón no los
        mete de golpe. Con calentamiento (1/día) caben 1/día; el resto
        queda pendiente y se reporta, nunca 6 en un día."""
        lost = START + timedelta(days=1)
        # 6 ítems perdidos de tiktok_es (día hipotético saturado).
        missed = [dict(entry(lost, 12, 0, 11 + i, "tiktok_es"),
                       state="scheduled") for i in range(6)]
        sched, placed, unplaced = bj.recover_lost(
            missed, lost, [clip(11 + i, viral=60) for i in range(6)],
            START, horizon=7)
        per_day = {}
        for s in placed:
            per_day.setdefault((s["date"], s["account_key"]), 0)
            per_day[(s["date"], s["account_key"])] += 1
        for (d, k), n in per_day.items():
            self.assertLessEqual(n, 1, f"{k} {d}: {n} de golpe")
        self.assertEqual(len(placed) + len(unplaced), 6)
        # Con 6 días restantes (dom 22 -> jue 26, menos día libre lun
        # de tiktok_es) caben 5; al menos 1 queda pendiente.
        self.assertGreaterEqual(len(unplaced), 1)

    def test_recovery_skips_posted(self):
        """Lo ya publicado no se recupera."""
        lost = START + timedelta(days=1)
        existing = [entry(lost, 12, 0, 11, "tiktok_es", state="posted"),
                    entry(lost, 12, 5, 23, "instagram_es",
                          state="scheduled")]
        sched, placed, unplaced = bj.recover_lost(
            existing, lost, [clip(11, viral=54), clip(23, viral=78)],
            START, horizon=7)
        self.assertEqual([p["clip_id"] for p in placed], [23])


class TestStock(unittest.TestCase):
    def test_stock_real_plan_2100(self):
        """Con los datos reales (tabla + plan 2100): stock = 4 días
        de 7; faltan 3 días; 0 clips aprobados sin usar."""
        import os
        base = os.path.dirname(os.path.abspath(__file__))
        q = os.path.join(base, "..", "runs", "queue-audit-2026-11-20", "tabla.json")
        p = os.path.join(base, "..", "runs", "queue-audit-2026-11-20",
                         "plan_2100.json")
        if not (os.path.exists(q) and os.path.exists(p)):
            self.skipTest("snapshots de auditoría no presentes")
        approved, _used = bj.load_tabla(q)
        existing = bj.load_plan_2100(p)
        self.assertEqual([c["clip_id"] for c in approved], [23, 91, 11, 117])
        stock = bj.compute_stock(approved, existing, START, horizon=7)
        self.assertEqual(stock["dias_cubiertos_global"], 4)
        self.assertEqual(stock["dias_sin_cobertura"],
                         ["2026-11-24", "2026-11-25", "2026-11-26"])
        self.assertEqual(stock["clips_sin_usar"], [])
        self.assertEqual(stock["faltan_dias"], 3)
        self.assertEqual(stock["faltan_dias_es_min_clips"], 3)
        # El planificador no inventa stock: 0 jobs nuevos.
        _, nuevos, _ = bj.plan_buffer(approved, existing, START,
                                      horizon=7, used_pairs=_used)
        self.assertEqual(nuevos, [])

    def test_stock_full_with_enough_clips(self):
        """Con 7 clips aprobados ES, el colchón cubre los 7 días."""
        approved = [clip(i, viral=90 - i) for i in range(1, 8)]
        sched, nuevos, _ = bj.plan_buffer(approved, [], START, horizon=7)
        stock = bj.compute_stock(approved, sched, START, horizon=7)
        self.assertEqual(stock["dias_cubiertos_global"], 7)
        self.assertEqual(stock["dias_sin_cobertura"], [])


class TestDryRunWritesNothing(unittest.TestCase):
    def test_dry_run_never_calls_d1(self):
        """El dry-run no llama a D1 ni para leer: con el helper
        bloqueado, debe completar igual desde archivos locales."""
        import os
        base = os.path.dirname(os.path.abspath(__file__))
        q = os.path.join(base, "..", "runs", "queue-audit-2026-11-20", "tabla.json")
        p = os.path.join(base, "..", "runs", "queue-audit-2026-11-20",
                         "plan_2100.json")
        if not (os.path.exists(q) and os.path.exists(p)):
            self.skipTest("snapshots de auditoría no presentes")
        with mock.patch.object(bj, "d1",
                               side_effect=AssertionError("¡D1 llamado!")):
            with mock.patch.object(bj, "subprocess") as sub:
                sub.run.side_effect = AssertionError("¡subprocess!")
                rc = bj.main(["--tabla", q, "--plan", p,
                              "--start", "2026-11-20"])
        self.assertEqual(rc, 0)

    def test_execute_refuses_before_cutover(self):
        """--execute antes de las 21:00 se niega sin --force."""
        import os
        base = os.path.dirname(os.path.abspath(__file__))
        q = os.path.join(base, "..", "runs", "queue-audit-2026-11-20", "tabla.json")
        p = os.path.join(base, "..", "runs", "queue-audit-2026-11-20",
                         "plan_2100.json")
        if not (os.path.exists(q) and os.path.exists(p)):
            self.skipTest("snapshots de auditoría no presentes")
        with mock.patch.object(bj, "d1",
                               side_effect=AssertionError("¡D1 llamado!")):
            rc = bj.main(["--tabla", q, "--plan", p, "--execute"])
        self.assertEqual(rc, 2)


class TestIdempotency(unittest.TestCase):
    def test_idem_key_stable_and_scoped_by_day(self):
        k1 = bj.idem_key(91, "tiktok", 5, "2026-11-20")
        k2 = bj.idem_key(91, "tiktok", 5, "2026-11-20")
        k3 = bj.idem_key(91, "tiktok", 5, "2026-11-21")
        k4 = bj.idem_key(117, "tiktok", 5, "2026-11-20")
        self.assertEqual(k1, k2)
        self.assertNotEqual(k1, k3)
        self.assertNotEqual(k1, k4)
        self.assertEqual(len(k1), 64)


if __name__ == "__main__":
    unittest.main(verbosity=2)

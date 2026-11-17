#!/usr/bin/env python3
"""Pruebas de 6B.3: la cola de jobs se ordena por score, no FIFO,
con envejecimiento para no dejar morir de hambre a los viejos.
"""
import os
import sys
import tempfile
import unittest
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from clipagent.ingest import select_top_peaks
from jobqueue import Queue


def _add(q, vod_id, streamer="illojuan"):
    return q.add(f"https://www.twitch.tv/videos/{vod_id}", force=True,
                 streamer=streamer)


class TestJobOrder(unittest.TestCase):
    def test_mayor_score_primero(self):
        with tempfile.TemporaryDirectory() as tmp:
            q = Queue(os.path.join(tmp, "q.db"))
            a = _add(q, "111")
            b = _add(q, "222")
            c = _add(q, "333")
            q.set_score(a, 10)
            q.set_score(b, 86)
            q.set_score(c, 61)
            self.assertEqual(q.next_queued()["id"], b)
            q.set_state(b, "processing")
            self.assertEqual(q.next_queued()["id"], c)
            q.close()

    def test_sin_score_manda_la_antiguedad(self):
        # Todos con score 0: el más antiguo primero (FIFO con
        # envejecimiento, no inanición).
        with tempfile.TemporaryDirectory() as tmp:
            q = Queue(os.path.join(tmp, "q.db"))
            a = _add(q, "111")
            _add(q, "222")
            self.assertEqual(q.next_queued()["id"], a)
            q.close()

    def test_viejo_sin_score_pasa_por_delante_de_nuevo_con_10(self):
        # Bono por edad: 7 días x 2 = +14. Un job de hace 7 días con
        # score 0 (14) pasa por delante de uno nuevo con score 10,
        # pero no de uno con 86.
        with tempfile.TemporaryDirectory() as tmp:
            q = Queue(os.path.join(tmp, "q.db"))
            old = _add(q, "111")
            new = _add(q, "222")
            top = _add(q, "333")
            q.set_score(new, 10)
            q.set_score(top, 86)
            week_ago = (datetime.now() - timedelta(days=7)).isoformat(
                timespec="seconds")
            q.conn.execute("UPDATE jobs SET created_at = ? WHERE id = ?",
                           (week_ago, old))
            q.conn.commit()
            self.assertEqual(q.next_queued()["id"], top)
            q.set_state(top, "processing")
            self.assertEqual(q.next_queued()["id"], old)
            q.set_state(old, "processing")
            self.assertEqual(q.next_queued()["id"], new)
            q.close()

    def test_bono_por_edad_con_tope(self):
        with tempfile.TemporaryDirectory() as tmp:
            q = Queue(os.path.join(tmp, "q.db"))
            job_id = _add(q, "111")
            month_ago = (datetime.now() - timedelta(days=30)).isoformat(
                timespec="seconds")
            q.conn.execute("UPDATE jobs SET created_at = ? WHERE id = ?",
                           (month_ago, job_id))
            q.conn.commit()
            job = q.get_job(job_id)
            # 30 días -> tope de 7 días x 2 = +14, no +60.
            self.assertEqual(q.effective_score(job), 14)
            q.close()

    def test_cola_vacia(self):
        with tempfile.TemporaryDirectory() as tmp:
            q = Queue(os.path.join(tmp, "q.db"))
            self.assertIsNone(q.next_queued())
            q.close()


class TestSelectTopPeaksOrder(unittest.TestCase):
    def test_corta_primero_el_de_mayor_score(self):
        peaks = select_top_peaks({100: 50.0, 200: 90.0, 300: 70.0},
                                 top_n=3, min_separation=60)
        self.assertEqual([ts for ts, _ in peaks], [200, 300, 100])

    def test_top_n_es_maximo_y_respeta_separacion(self):
        peaks = select_top_peaks({100: 90.0, 110: 80.0, 200: 70.0},
                                 top_n=2, min_separation=60)
        self.assertEqual([ts for ts, _ in peaks], [100, 200])


if __name__ == "__main__":
    unittest.main(verbosity=2)

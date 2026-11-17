#!/usr/bin/env python3
"""Pruebas de 5.2: supervisor de `clipagent start/stop/status`.
"""
import json
import os
import subprocess
import sys
import tempfile
import threading
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from clipagent import service


class TestSupervisor(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.pid_file = os.path.join(self.tmp.name, "service.pid")

    def tearDown(self):
        self.tmp.cleanup()

    def test_reinicia_hijo_caido_y_para_todo(self):
        stop = threading.Event()
        children = [("a", [sys.executable, "-c",
                           "import time; time.sleep(30)"]),
                    ("b", [sys.executable, "-c",
                           "import time; time.sleep(30)"])]
        result = {}
        t = threading.Thread(
            target=lambda: result.update(
                service.run_supervisor(children, self.pid_file, stop)))
        t.start()
        # Espera a que el PID file exista y los hijos vivan.
        for _ in range(50):
            if os.path.exists(self.pid_file):
                break
            time.sleep(0.1)
        data = json.load(open(self.pid_file, encoding="utf-8"))
        self.assertEqual(set(data["children"]), {"a", "b"})
        self.assertTrue(service.service_running(self.pid_file))
        for pid in data["children"].values():
            os.kill(pid, 0)  # vivos
        stop.set()
        t.join(timeout=20)
        self.assertFalse(t.is_alive())
        self.assertFalse(os.path.exists(self.pid_file))
        for pid in data["children"].values():
            with self.assertRaises(ProcessLookupError):
                os.kill(pid, 0)  # muertos: no quedan huérfanos

    def test_tope_de_reinicios(self):
        stop = threading.Event()
        children = [("corto", [sys.executable, "-c", "raise SystemExit(1)"])]
        restarts = service.run_supervisor(
            children, self.pid_file, stop, restart_delay=0.05,
            max_restarts=2)
        self.assertEqual(restarts["corto"], 2)
        self.assertFalse(os.path.exists(self.pid_file))

    def test_status_sin_servicio(self):
        st = service.service_status(self.pid_file)
        self.assertFalse(st["corriendo"])
        self.assertIsNone(st["supervisor_pid"])
        self.assertIn("cola", st)
        self.assertIn("plataformas", st)

    def test_stop_sin_servicio(self):
        self.assertEqual(service.stop_service(self.pid_file), 2)


class TestCliStartStop(unittest.TestCase):
    def test_start_stop_con_hijos_de_prueba(self):
        # Subproceso real del CLI, con hijos `sleep` (sin red).
        root = os.path.dirname(os.path.abspath(__file__))
        env = dict(os.environ)
        start = subprocess.Popen(
            [sys.executable, "-m", "clipagent", "start",
             "--child", "a=sleep 60", "--child", "b=sleep 60"],
            cwd=root, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True)
        try:
            pid_file = os.path.join(root, "service.pid")
            for _ in range(50):
                if os.path.exists(pid_file):
                    break
                time.sleep(0.1)
            self.assertTrue(os.path.exists(pid_file))
            data = json.load(open(pid_file, encoding="utf-8"))
            self.assertEqual(set(data["children"]), {"a", "b"})
            out = subprocess.run(
                [sys.executable, "-m", "clipagent", "status"],
                cwd=root, capture_output=True, text=True, timeout=30)
            self.assertIn("corriendo", out.stdout)
            stop = subprocess.run(
                [sys.executable, "-m", "clipagent", "stop"],
                cwd=root, capture_output=True, text=True, timeout=60)
            self.assertEqual(stop.returncode, 0, stop.stdout + stop.stderr)
            self.assertFalse(os.path.exists(pid_file))
            for pid in data["children"].values():
                with self.assertRaises(ProcessLookupError):
                    os.kill(pid, 0)
            # Sin kill-switch residual: un stop limpio no deja el freno.
            self.assertFalse(os.path.exists(
                os.path.join(root, "queue.kill")))
        finally:
            if start.poll() is None:
                start.kill()
            start.wait(timeout=10)
            for f in ("service.pid", "queue.kill"):
                try:
                    os.remove(os.path.join(root, f))
                except OSError:
                    pass


if __name__ == "__main__":
    unittest.main(verbosity=2)

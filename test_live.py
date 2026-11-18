#!/usr/bin/env python3
"""Pruebas de 9.2: directos en vivo (IRC Twitch, Pusher Kick,
detector de picos, cooldown y tope por directo/streamer).
"""
import os
import socket
import sys
import tempfile
import threading
import time
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from clipagent.live import (KickPusherClient, LiveLimits,
                            LivePeakDetector, LiveSession,
                            MinimalWebSocket, TwitchIRCClient,
                            load_live_peaks, parse_irc_line,
                            resolve_kick_chatroom_id, save_live_peaks)


class FakeSocket:
    """Socket falso: entrega bytes encolados y registra lo enviado."""

    def __init__(self, chunks):
        self.chunks = list(chunks)
        self.sent = b""
        self.timeout = None
        self.closed = False

    def settimeout(self, timeout):
        self.timeout = timeout

    def recv(self, n):
        if self.closed:
            raise OSError("socket cerrado")
        if not self.chunks:
            time.sleep(0.01)
            raise socket.timeout("sin datos")
        return self.chunks.pop(0)

    def sendall(self, data):
        self.sent += data

    def close(self):
        self.closed = True


class TestIRC(unittest.TestCase):
    def test_parse_privmsg(self):
        line = (":usuario!usuario@usuario.tmi.twitch.tv PRIVMSG "
                "#illojuan :jajaja qué bueno")
        kind, channel, user, text = parse_irc_line(line)
        self.assertEqual((kind, channel, user, text),
                         ("PRIVMSG", "illojuan", "usuario",
                          "jajaja qué bueno"))

    def test_parse_ping(self):
        self.assertEqual(parse_irc_line("PING :tmi.twitch.tv")[0], "PING")

    def test_parse_basura_devuelve_none(self):
        self.assertIsNone(parse_irc_line(":tmi.twitch.tv 001 nick :hola"))

    def test_cliente_lee_mensajes_y_responde_ping(self):
        chunks = [
            b":u1!u@u.tmi.twitch.tv PRIVMSG #illojuan :jajaja\r\n"
            b"PING :tmi.twitch.tv\r\n",
            ":u2!u@u.tmi.twitch.tv PRIVMSG #illojuan :qué risa\r\n"
            .encode("utf-8"),
        ]
        sock = FakeSocket(chunks)
        client = TwitchIRCClient(["illojuan"], sock=sock)
        client.connect()
        self.assertIn(b"JOIN #illojuan", sock.sent)
        msgs = []

        def _pump():
            for item in client.iter_messages(timeout=0.1):
                msgs.append(item)

        thread = threading.Thread(target=_pump, daemon=True)
        thread.start()
        deadline = time.time() + 5
        while len(msgs) < 2 and time.time() < deadline:
            time.sleep(0.05)
        client.close()
        thread.join(timeout=2)
        self.assertEqual(len(msgs), 2)
        self.assertEqual(msgs[0][0], "u1")
        self.assertIn(b"PONG", sock.sent)


class TestWebSocket(unittest.TestCase):
    def test_texto_ida_y_vuelta_por_socketpair(self):
        a, b = socket.socketpair()
        a.settimeout(2)
        b.settimeout(2)
        client = MinimalWebSocket(sock=a)
        # Servidor falso: lee el frame del cliente y responde uno.
        received = {}

        def server():
            head = b.recv(2)
            n = head[1] & 0x7F
            mask = b.recv(4)
            payload = b.recv(n)
            received["text"] = bytes(
                c ^ mask[i % 4] for i, c in enumerate(payload)
            ).decode("utf-8")
            # Respuesta sin máscara (servidor -> cliente).
            body = "hola".encode("utf-8")
            b.sendall(bytes([0x81, len(body)]) + body)

        t = threading.Thread(target=server, daemon=True)
        t.start()
        client.send_text("ping-test")
        self.assertEqual(client.recv_text(timeout=2), "hola")
        t.join(timeout=2)
        self.assertEqual(received["text"], "ping-test")
        a.close()
        b.close()

    def test_resuelve_chatroom_kick(self):
        def fake_request(method, url, headers, body):
            self.assertIn("kick.com/api/v2/channels/coker", url)
            return 200, {"data": {"chatroom": {"id": 12345}}}

        self.assertEqual(
            resolve_kick_chatroom_id("coker", fake_request), 12345)

    def test_chatroom_sin_datos_devuelve_none(self):
        def fake_request(method, url, headers, body):
            return 200, {"data": {}}

        self.assertIsNone(
            resolve_kick_chatroom_id("nadie", fake_request))

    def test_kick_sin_clave_no_conecta(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            client = KickPusherClient("coker")
            ok, msg = client.connect()
        self.assertFalse(ok)
        self.assertIn("KICK_PUSHER_KEY", msg)


class TestDetector(unittest.TestCase):
    def _burst(self, detector, t0, n, text="jajaja qué risa"):
        for i in range(n):
            detector.add_message(f"u{i}", text, t0 + (i % 10))

    def test_pico_con_rafaga(self):
        detector = LivePeakDetector(t0=1000.0)
        # Fondo: 1 mensaje cada 10 s durante 2 min.
        for t in range(0, 120, 10):
            detector.add_message("fondo", "hola", 1000.0 + t)
        # Ráfaga: 30 risas en 10 s.
        self._burst(detector, 1130.0, 30)
        peaks = detector.tick()
        self.assertEqual(len(peaks), 1)
        ts, score, multiple, detail = peaks[0]
        # ts relativo al inicio del directo (la ráfaga va a 130 s).
        self.assertAlmostEqual(ts, 130, delta=10)
        self.assertGreaterEqual(multiple, 3.0)

    def test_sin_rafaga_no_hay_pico(self):
        detector = LivePeakDetector(t0=1000.0)
        for t in range(0, 120, 10):
            detector.add_message("fondo", "hola", 1000.0 + t)
        self.assertEqual(detector.tick(), [])

    def test_mismo_pico_no_se_emite_dos_veces(self):
        detector = LivePeakDetector(t0=1000.0)
        for t in range(0, 120, 10):
            detector.add_message("fondo", "hola", 1000.0 + t)
        self._burst(detector, 1130.0, 30)
        self.assertEqual(len(detector.tick()), 1)
        self.assertEqual(detector.tick(), [])

    def test_pocos_mensajes_no_puntua(self):
        detector = LivePeakDetector(t0=1000.0)
        detector.add_message("u1", "hola", 1000.0)
        self.assertEqual(detector.tick(), [])


class TestLimits(unittest.TestCase):
    def test_cooldown_y_tope(self):
        limits = LiveLimits(cooldown_minutes=10, max_clips_per_stream=2)
        now = 1000000.0
        self.assertTrue(limits.allow("illojuan", "2026-11-17", now)[0])
        limits.record_clip("illojuan", "2026-11-17", now)
        ok, reason = limits.allow("illojuan", "2026-11-17", now + 60)
        self.assertFalse(ok)
        self.assertIn("cooldown", reason)
        self.assertTrue(limits.allow("illojuan", "2026-11-17",
                                     now + 601)[0])
        limits.record_clip("illojuan", "2026-11-17", now + 601)
        ok, reason = limits.allow("illojuan", "2026-11-17", now + 1300)
        self.assertFalse(ok)
        self.assertIn("tope", reason)
        # Otro streamer no se ve afectado.
        self.assertTrue(limits.allow("coker", "2026-11-17",
                                     now + 1300)[0])


class TestSession(unittest.TestCase):
    def test_sesion_registra_pico_y_notifica(self):
        with tempfile.TemporaryDirectory() as tmp:
            notified = []

            def notify(event, detail):
                notified.append((event, detail))
                return True

            session = LiveSession("illojuan", platform="twitch",
                                  stream_id="2026-11-17",
                                  limits=LiveLimits(cooldown_minutes=0,
                                                    max_clips_per_stream=5),
                                  notify_fn=notify, base_dir=tmp)
            t0 = time.time()
            messages = []
            for t in range(0, 120, 10):
                messages.append(("fondo", "hola", t0 + t))
            for i in range(30):
                messages.append((f"u{i}", "jajaja qué risa",
                                 t0 + 130 + (i % 10)))
            peaks = session.run(iter(messages), duration_s=60, tick_s=0)
            self.assertEqual(len(peaks), 1)
            self.assertEqual(len(notified), 1)
            self.assertEqual(notified[0][0], "pico en directo")
            saved = load_live_peaks("illojuan", "2026-11-17", tmp)
            self.assertEqual(len(saved), 1)
            self.assertGreaterEqual(saved[0]["multiple"], 3.0)

    def test_sesion_sin_picos_no_notifica(self):
        with tempfile.TemporaryDirectory() as tmp:
            notified = []
            session = LiveSession("illojuan", base_dir=tmp,
                                  notify_fn=lambda e, d: notified.append(e))
            t0 = time.time()
            messages = [("fondo", "hola", t0 + t)
                        for t in range(0, 120, 10)]
            peaks = session.run(iter(messages), duration_s=60, tick_s=0)
            self.assertEqual(peaks, [])
            self.assertEqual(notified, [])

    def test_guardar_y_cargar_picos(self):
        with tempfile.TemporaryDirectory() as tmp:
            peaks = [{"ts": 100, "score": 90.0, "multiple": 8.0}]
            path = save_live_peaks("illojuan", "2026-11-17", peaks, tmp)
            self.assertTrue(os.path.isfile(path))
            self.assertEqual(load_live_peaks("illojuan", "2026-11-17",
                                             tmp), peaks)


class TestDetectorHype(unittest.TestCase):
    """Puerta live por ráfaga de hype (2026-11-18).

    El múltiplo 3x sobre la mediana es inalcanzable en un chat
    siempre caliente (VOD Rubius: máx. 1,68x en score). La puerta
    nueva emite por hype_ratio >= 1,3 + score >= 14 + hype_score
    >= 1,0, y la salvaguarda emite la mejor ventana al final si
    no hubo ningún pico.
    """

    def _hot_chat(self, detector, t0, seconds=300, per_sec=10,
                  burst_at=None, burst_n=0, burst_texts=None,
                  burst_unique=True):
        """Chat caliente constante (10 msgs/s, como Rubius) + ráfaga
        opcional encima de la base. Con burst_unique=False el texto
        de la ráfaga se repite idéntico (spam de emotes)."""
        n = 0
        for s in range(seconds):
            for k in range(per_sec):
                detector.add_message(
                    f"u{n % 500}", f"qué buen directo {n}",
                    t0 + s + k * 0.1)
                n += 1
        if burst_at is not None:
            texts = burst_texts or ["jajaja no lo puedo creer"]
            for i in range(burst_n):
                text = texts[i % len(texts)]
                if burst_unique:
                    text = f"{text} {i}"
                detector.add_message(f"b{i}", text,
                                     t0 + burst_at + (i % 10))

    def test_chat_caliente_con_rafaga_emite_por_hype(self):
        detector = LivePeakDetector(t0=1000.0)
        burst_texts = ["jajaja qué risa", "WTF qué acaba de pasar",
                       "no lo puedo creer", "qué locura esto",
                       "vamos qué pasada"]
        # Ráfaga: 45 msgs extra en 10 s (ratio ~1,45) sobre una base
        # de 100/10 s: el múltiplo queda ~2,2 (< 3,0), solo la
        # puerta hype puede emitir.
        self._hot_chat(detector, 1000.0, burst_at=200, burst_n=45,
                       burst_texts=burst_texts)
        peaks = detector.tick()
        self.assertGreaterEqual(len(peaks), 1)
        self.assertEqual(peaks[0][3].get("live_gate"), "hype")
        self.assertGreaterEqual(peaks[0][3].get("hype_ratio", 0), 1.3)

    def test_chat_caliente_sin_rafaga_no_emite(self):
        detector = LivePeakDetector(t0=1000.0)
        self._hot_chat(detector, 1000.0)
        self.assertEqual(detector.tick(), [])
        self.assertEqual(detector.best_fallback(), [])

    def test_salvaguarda_mejor_ventana_si_no_hay_pico(self):
        detector = LivePeakDetector(t0=1000.0)
        # Ráfaga moderada: ratio ~1,2 (< 1,3) pero score alto: no
        # pasa por tick, pero la salvaguarda emite la mejor ventana.
        self._hot_chat(detector, 1000.0, burst_at=200, burst_n=20,
                       burst_texts=["jajaja qué risa"])
        self.assertEqual(detector.tick(), [])
        fallback = detector.best_fallback()
        self.assertEqual(len(fallback), 1)
        self.assertEqual(fallback[0][3].get("live_gate"), "fallback")

    def test_emote_spam_no_pasa_por_rafaga(self):
        detector = LivePeakDetector(t0=1000.0)
        # 200 emotes idénticos en 10 s: la ventana puntúa alto,
        # pero es emote-only sin risas ni hype -> anti-spam.
        self._hot_chat(detector, 1000.0, burst_at=200, burst_n=200,
                       burst_texts=["🔥"], burst_unique=False)
        self.assertEqual(detector.tick(), [])
        self.assertEqual(detector.best_fallback(), [])

    def test_calentamiento_no_emite_antes_de_120s(self):
        detector = LivePeakDetector(t0=1000.0)
        for i in range(30):
            detector.add_message(f"u{i}", "jajaja qué risa",
                                 1000.0 + 50 + (i % 10))
        self.assertEqual(detector.tick(), [])


if __name__ == "__main__":
    unittest.main(verbosity=2)

"""Directos en vivo (Fase 9.2).

Twitch por IRC (stdlib, sin dependencias) y Kick por WebSocket
Pusher (cliente WebSocket mínimo en stdlib, sin librerías nuevas).
El chat en vivo alimenta el mismo scoring que el VOD
(ingest.score_windows + detect.filter_passed): cuando una ventana
pasa el filtro, se registra el pico en
runs/live/<streamer>_<fecha>/live_peaks.json y se avisa por
Telegram. Cooldown y tope de clips por directo y por streamer
(config `live:`).

BLOQUEADO (grabación): no hay cómo grabar el directo con las
herramientas instaladas. TwitchDownloaderCLI (1.56.5) no tiene
comando live (solo VOD/clip/chat) y streamlink no está instalado.
La grabación por segmentos de 5-10 min exige instalar streamlink
o un flujo HLS con token (sin documentar). Mientras tanto, los
picos en vivo quedan marcados y el VOD se procesa al terminar el
directo por el watcher normal.
"""
import base64
import json
import os
import re
import socket
import ssl
import struct
import tempfile
import time
from datetime import datetime, timezone

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LIVE_DIR = os.path.join(BASE_DIR, "runs", "live")

IRC_HOST = "irc.chat.twitch.tv"
IRC_PORT = 6697
PUSHER_HOST = "ws-us2.pusher.com"
PUSHER_PATH = "/app/{key}?protocol=7&client=js&version=7.0.3"
KICK_CHANNEL_URL = "https://kick.com/api/v2/channels/{slug}"

DEFAULT_COOLDOWN_MINUTES = 10
DEFAULT_MAX_CLIPS_PER_STREAM = 5
MIN_SEPARATION_S = 60


# ---------------------------------------------------------------------------
# Twitch IRC
# ---------------------------------------------------------------------------

_IRC_PRIVMSG = re.compile(
    r"^:([^!]+)!.* PRIVMSG #(\S+) :(.*)$")


def parse_irc_line(line):
    """(tipo, canal, usuario, texto) de una línea IRC, o None."""
    line = line.strip()
    if line.startswith("PING"):
        return ("PING", None, None, line[5:].strip())
    m = _IRC_PRIVMSG.match(line)
    if not m:
        return None
    return ("PRIVMSG", m.group(2), m.group(1), m.group(3))


class TwitchIRCClient:
    """Cliente IRC mínimo de Twitch (solo lectura de chat)."""

    def __init__(self, channels, token=None, nick=None, sock=None):
        self.channels = [c.strip().lower().lstrip("#")
                         for c in channels]
        self.token = token or os.environ.get("TWITCH_CHAT_OAUTH")
        self.nick = nick or "justinfan12345"
        self._sock = sock
        self._buf = b""

    def connect(self, timeout=15):
        """Conecta y entra en los canales. Devuelve True si conecta."""
        if self._sock is None:
            raw = socket.create_connection((IRC_HOST, IRC_PORT),
                                           timeout=timeout)
            ctx = ssl.create_default_context()
            self._sock = ctx.wrap_socket(raw, server_hostname=IRC_HOST)
        lines = []
        if self.token:
            lines.append(f"PASS oauth:{self.token}")
        lines.append(f"NICK {self.nick}")
        for channel in self.channels:
            lines.append(f"JOIN #{channel}")
        self._sock.sendall(("\r\n".join(lines) + "\r\n").encode("utf-8"))
        return True

    def _read_lines(self, timeout=1.0):
        """Líneas leídas, [] si no hay nada (timeout) o None si se
        cerró la conexión."""
        if self._sock is None:
            return None
        self._sock.settimeout(timeout)
        try:
            data = self._sock.recv(4096)
        except (socket.timeout, TimeoutError):
            return []
        except OSError:
            return None
        if not data:
            return None
        self._buf += data
        lines = []
        while b"\r\n" in self._buf:
            raw, self._buf = self._buf.split(b"\r\n", 1)
            lines.append(raw.decode("utf-8", "ignore"))
        return lines

    def iter_messages(self, timeout=1.0):
        """Generador infinito de (usuario, texto, ts); responde a PING.

        Termina solo si se cierra la conexión. La sesión lo abandona
        al cumplirse su duración y llama a close().
        """
        while True:
            lines = self._read_lines(timeout)
            if lines is None:
                return
            for line in lines:
                parsed = parse_irc_line(line)
                if parsed is None:
                    continue
                kind, _channel, user, text = parsed
                if kind == "PING":
                    try:
                        self._sock.sendall(
                            f"PONG {text}\r\n".encode("utf-8"))
                    except OSError:
                        pass
                    continue
                yield user, text, time.time()

    def close(self):
        try:
            if self._sock is not None:
                self._sock.close()
        except OSError:
            pass
        self._sock = None


# ---------------------------------------------------------------------------
# WebSocket mínimo (stdlib, RFC 6455) + Pusher de Kick
# ---------------------------------------------------------------------------

class MinimalWebSocket:
    """Cliente WebSocket mínimo: handshake, frames de texto, ping/pong."""

    def __init__(self, sock=None):
        self._sock = sock
        self._buf = b""
        self.closed = False

    def connect(self, host, port, path, timeout=15):
        raw = socket.create_connection((host, port), timeout=timeout)
        ctx = ssl.create_default_context()
        self._sock = ctx.wrap_socket(raw, server_hostname=host)
        key = base64.b64encode(os.urandom(16)).decode("ascii")
        req = (f"GET {path} HTTP/1.1\r\nHost: {host}\r\n"
               "Upgrade: websocket\r\nConnection: Upgrade\r\n"
               f"Sec-WebSocket-Key: {key}\r\n"
               "Sec-WebSocket-Version: 13\r\n\r\n")
        self._sock.sendall(req.encode("ascii"))
        resp = b""
        while b"\r\n\r\n" not in resp:
            chunk = self._sock.recv(4096)
            if not chunk:
                raise ConnectionError("sin respuesta al handshake WS")
            resp += chunk
        if b"101" not in resp.split(b"\r\n", 1)[0]:
            raise ConnectionError(
                f"handshake WS rechazado: {resp[:120]!r}")
        return True

    def send_text(self, text):
        payload = text.encode("utf-8")
        mask = os.urandom(4)
        header = bytearray([0x81])
        n = len(payload)
        if n < 126:
            header.append(0x80 | n)
        elif n < 65536:
            header.append(0x80 | 126)
            header += struct.pack("!H", n)
        else:
            header.append(0x80 | 127)
            header += struct.pack("!Q", n)
        masked = bytes(b ^ mask[i % 4] for i, b in enumerate(payload))
        self._sock.sendall(bytes(header) + mask + masked)

    def _recv_exact(self, n, timeout):
        if self._sock is None or self.closed:
            raise ConnectionError("WS cerrado")
        self._sock.settimeout(timeout)
        while len(self._buf) < n:
            chunk = self._sock.recv(4096)
            if not chunk:
                self.closed = True
                raise ConnectionError("WS cerrado")
            self._buf += chunk
        out, self._buf = self._buf[:n], self._buf[n:]
        return out

    def recv_text(self, timeout=1.0):
        """Un mensaje de texto, o None si no hay nada (timeout)."""
        try:
            head = self._recv_exact(2, timeout)
        except (socket.timeout, TimeoutError):
            return None
        except (OSError, ConnectionError):
            return None
        fin = head[0] & 0x80
        opcode = head[0] & 0x0F
        masked = head[1] & 0x80
        length = head[1] & 0x7F
        if length == 126:
            length = struct.unpack("!H", self._recv_exact(2, timeout))[0]
        elif length == 127:
            length = struct.unpack("!Q", self._recv_exact(8, timeout))[0]
        mask = self._recv_exact(4, timeout) if masked else None
        payload = self._recv_exact(length, timeout)
        if mask:
            payload = bytes(b ^ mask[i % 4]
                            for i, b in enumerate(payload))
        if opcode == 9:  # ping -> pong
            self._sock.sendall(bytes([0x8A, 0x80]) + os.urandom(4))
            return self.recv_text(timeout)
        if opcode == 8:  # close
            self.closed = True
            return None
        if opcode == 1 and fin:
            return payload.decode("utf-8", "ignore")
        return None

    def close(self):
        try:
            if self._sock is not None:
                self._sock.close()
        except OSError:
            pass
        self._sock = None


def kick_channel_category(slug, request_fn=None):
    """Categoría ACTUAL de un canal de Kick, o None si no se puede.

    GET https://kick.com/api/v2/channels/<slug> (API pública de
    Kick, sin autenticación). La categoría vive en
    data.livestream.categories[0].name (en vivo) o en
    data.livestream.category / data.category como respaldo.
    Sin directo en curso, la categoría es "" (no se asume GTA VI).
    """
    import urllib.request
    url = KICK_CHANNEL_URL.format(slug=slug)
    try:
        if request_fn is not None:
            status, payload = request_fn("GET", url, {}, None)
        else:
            req = urllib.request.Request(url, headers={
                "User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=15) as resp:
                status, payload = resp.status, json.loads(
                    resp.read().decode("utf-8", "ignore") or "{}")
    except Exception:  # noqa: BLE001 - Kick detrás de Cloudflare, etc.
        return None
    if status != 200:
        return None
    try:
        data = (payload or {}).get("data") or {}
        live = data.get("livestream") or {}
        cats = live.get("categories") or []
        if isinstance(cats, list) and cats:
            first = cats[0]
            if isinstance(first, dict):
                return first.get("name") or first.get("slug") or None
            if isinstance(first, str):
                return first
        cat = live.get("category") or data.get("category")
        if isinstance(cat, dict):
            return cat.get("name") or cat.get("slug") or None
        if isinstance(cat, str):
            return cat or None
        return ""
    except (AttributeError, TypeError, ValueError):
        return None


def resolve_kick_chatroom_id(slug, request_fn=None):
    """ID numérico del chatroom de Kick, o None si no se puede.

    GET https://kick.com/api/v2/channels/{slug} (sin documentar,
    detrás de Cloudflare: puede fallar y es normal).
    """
    import urllib.request
    url = KICK_CHANNEL_URL.format(slug=slug)
    try:
        if request_fn is not None:
            status, payload = request_fn("GET", url, {}, None)
        else:
            req = urllib.request.Request(url, headers={
                "User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=15) as resp:
                status, payload = resp.status, json.loads(
                    resp.read().decode("utf-8", "ignore") or "{}")
    except Exception:  # noqa: BLE001 - el chatroom puede no resolverse
        return None
    if status != 200:
        return None
    try:
        return int(payload["data"]["chatroom"]["id"])
    except (KeyError, TypeError, ValueError):
        return None


class KickPusherClient:
    """Chat en vivo de Kick por Pusher (no oficial).

    La clave de la app Pusher va en .env (KICK_PUSHER_KEY): es
    pública (va en el bundle web de Kick) pero no está verificada
    en ninguna doc oficial, así que no se hardcodea. Sin clave, el
    cliente no conecta y la sesión queda pendiente.
    """

    def __init__(self, slug, pusher_key=None, ws=None, request_fn=None):
        self.slug = slug
        self.pusher_key = pusher_key or os.environ.get("KICK_PUSHER_KEY")
        self._ws = ws or MinimalWebSocket()
        self._request_fn = request_fn
        self.chatroom_id = None

    def connect(self):
        if not self.pusher_key:
            return False, "sin KICK_PUSHER_KEY en .env"
        self.chatroom_id = resolve_kick_chatroom_id(
            self.slug, self._request_fn)
        if not self.chatroom_id:
            return False, ("no se resolvió el chatroom de Kick "
                           "(Cloudflare o canal inexistente)")
        path = PUSHER_PATH.format(key=self.pusher_key)
        try:
            self._ws.connect(PUSHER_HOST, 443, path)
            self._ws.send_text(json.dumps({
                "event": "pusher:subscribe",
                "data": {"channel": f"chatroom.{self.chatroom_id}"}}))
        except (OSError, ConnectionError) as e:
            return False, f"no se conectó a Pusher: {e}"
        return True, f"conectado a chatroom.{self.chatroom_id}"

    def iter_messages(self, timeout=1.0):
        """Generador infinito de (usuario, texto, ts) de ChatMessageEvent.

        Termina solo si se cierra el WebSocket.
        """
        while True:
            raw = self._ws.recv_text(timeout)
            if raw is None:
                if getattr(self._ws, "closed", False):
                    return
                continue
            try:
                envelope = json.loads(raw)
            except ValueError:
                continue
            if envelope.get("event") != "App\\Events\\ChatMessageEvent":
                continue
            try:
                data = json.loads(envelope.get("data") or "{}")
                sender = data.get("sender") or {}
                text = data.get("content") or ""
                created = data.get("created_at") or ""
                ts = time.time()
                if created:
                    try:
                        ts = datetime.fromisoformat(
                            created.replace("Z", "+00:00")).timestamp()
                    except ValueError:
                        pass
                yield sender.get("username") or "?", text, ts
            except (ValueError, TypeError, AttributeError):
                continue

    def close(self):
        self._ws.close()


# ---------------------------------------------------------------------------
# Detección de picos en vivo + límites
# ---------------------------------------------------------------------------

def _chat_json_path(messages):
    """Escribe los mensajes en un chat.json temporal para score_windows."""
    fd, path = tempfile.mkstemp(suffix=".json")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump({"comments": [
            {"content_offset_seconds": int(rel_ts),
             "message": {"body": text},
             "commenter": {"name": user}}
            for user, text, rel_ts in messages
        ]}, f)
    return path


class LivePeakDetector:
    """Picos en vivo con el mismo scoring que el VOD.

    Cada tick re-puntúa el chat acumulado (score_windows) y emite
    las ventanas nuevas que pasan el filtro previo (múltiplo >= 3x).
    `t0` es el inicio del directo (los ts son segundos desde t0).
    """

    def __init__(self, t0=None, window_size=10):
        self.t0 = t0 or time.time()
        self.window_size = window_size
        self.messages = []
        self.emitted = set()

    def add_message(self, user, text, ts=None):
        self.messages.append((user, text, (ts or time.time()) - self.t0))

    def tick(self):
        """Picos nuevos [(ts, score, multiple, detail)]. Nunca levanta."""
        if len(self.messages) < 5:
            return []
        from .detect import MIN_MULTIPLE, filter_passed
        from .ingest import score_windows
        import statistics
        path = _chat_json_path(self.messages)
        try:
            scores, detail = score_windows(path)
        except Exception:  # noqa: BLE001 - el tick nunca tumba la sesión
            return []
        finally:
            try:
                os.remove(path)
            except OSError:
                pass
        if not scores:
            return []
        median = max(statistics.median(scores.values()), 1.0)
        ranked = sorted(scores.items(), key=lambda kv: kv[1],
                        reverse=True)
        rank_map = {ts: i + 1 for i, (ts, _s) in enumerate(ranked)}
        peaks = []
        for ts, score in scores.items():
            if ts in self.emitted:
                continue
            if any(abs(ts - e) < MIN_SEPARATION_S for e in self.emitted):
                continue
            multiple = score / median
            if multiple < MIN_MULTIPLE:
                continue
            pasa, _motivos = filter_passed(
                detail.get(ts, {}), multiple, rank=rank_map.get(ts))
            if not pasa:
                continue
            self.emitted.add(ts)
            peaks.append((ts, round(score, 1), round(multiple, 2),
                          detail.get(ts, {})))
        return sorted(peaks)


class LiveLimits:
    """Cooldown y tope de clips por directo y por streamer (9.2)."""

    def __init__(self, cooldown_minutes=DEFAULT_COOLDOWN_MINUTES,
                 max_clips_per_stream=DEFAULT_MAX_CLIPS_PER_STREAM):
        self.cooldown_s = cooldown_minutes * 60
        self.max_clips = max_clips_per_stream
        self._last_clip = {}
        self._counts = {}

    def allow(self, streamer, stream_id, now=None):
        """(ok, motivo)."""
        now = now or time.time()
        key = (streamer, stream_id)
        if self._counts.get(key, 0) >= self.max_clips:
            return False, (f"tope de {self.max_clips} clips por directo "
                           f"alcanzado ({streamer})")
        last = self._last_clip.get(key)
        if last is not None and now - last < self.cooldown_s:
            wait = int(self.cooldown_s - (now - last))
            return False, (f"cooldown: faltan {wait}s para el próximo "
                           f"clip de {streamer}")
        return True, "ok"

    def record_clip(self, streamer, stream_id, now=None):
        now = now or time.time()
        key = (streamer, stream_id)
        self._last_clip[key] = now
        self._counts[key] = self._counts.get(key, 0) + 1


# ---------------------------------------------------------------------------
# Sesión en vivo
# ---------------------------------------------------------------------------

def live_peaks_path(streamer, stream_id, base_dir=None):
    """runs/live/<streamer>_<stream_id>/live_peaks.json."""
    root = os.path.join(base_dir or BASE_DIR, "runs", "live")
    return os.path.join(root, f"{streamer}_{stream_id}", "live_peaks.json")


def load_live_peaks(streamer, stream_id, base_dir=None):
    path = live_peaks_path(streamer, stream_id, base_dir)
    if not os.path.isfile(path):
        return []
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return []


def save_live_peaks(streamer, stream_id, peaks, base_dir=None):
    """Guarda los picos (atómico). `peaks` es la lista completa."""
    path = live_peaks_path(streamer, stream_id, base_dir)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(peaks, f, indent=2, ensure_ascii=False)
    os.replace(tmp, path)
    return path


class LiveSession:
    """Una sesión en vivo: fuente de chat -> picos -> marcadores.

    `source` es un iterable de (usuario, texto, ts absoluto) o un
    cliente con iter_messages(). `notify_fn(evento, detalle)` se
    llama al detectar un pico (por defecto, Telegram).
    """

    def __init__(self, streamer, platform="twitch", stream_id=None,
                 limits=None, notify_fn=None, base_dir=None):
        self.streamer = streamer
        self.platform = platform
        self.stream_id = stream_id or datetime.now(
            timezone.utc).strftime("%Y-%m-%d")
        self.limits = limits or LiveLimits()
        self.notify_fn = notify_fn
        self.base_dir = base_dir
        self.detector = LivePeakDetector()
        self.peaks = load_live_peaks(streamer, self.stream_id, base_dir)

    def run(self, source, duration_s=3600, tick_s=30):
        """Lee la fuente hasta `duration_s` y registra picos.

        La fuente se bombea en un hilo a una cola: el bucle principal
        despierta cada segundo aunque el chat esté callado, así la
        duración se cumple siempre. Devuelve los picos de la sesión.
        """
        import queue
        import threading
        from .notify import notify as _notify
        notify = self.notify_fn or _notify
        if hasattr(source, "iter_messages"):
            messages = source.iter_messages()
        else:
            messages = iter(source)
        inbox = queue.Queue()
        stop = threading.Event()

        def _pump():
            try:
                for item in messages:
                    inbox.put(item)
                    if stop.is_set():
                        break
            except Exception:  # noqa: BLE001 - la bomba nunca tumba nada
                pass
            finally:
                inbox.put(None)

        thread = threading.Thread(target=_pump, daemon=True)
        thread.start()
        start = time.time()
        last_tick = 0.0
        try:
            while time.time() - start < duration_s:
                try:
                    item = inbox.get(timeout=1.0)
                except queue.Empty:
                    item = "TICK"
                if item is None:
                    break
                if item != "TICK":
                    user, text, ts = item
                    self.detector.add_message(user, text, ts)
                if time.time() - last_tick >= tick_s:
                    last_tick = time.time()
                    for peak in self.detector.tick():
                        self._on_peak(peak, notify)
        finally:
            stop.set()
            close = getattr(source, "close", None)
            if callable(close):
                try:
                    close()
                except Exception:  # noqa: BLE001 - cerrar nunca tumba nada
                    pass
        return self.peaks

    def _on_peak(self, peak, notify):
        ts, score, multiple, detail = peak
        ok, reason = self.limits.allow(self.streamer, self.stream_id)
        if not ok:
            return
        self.limits.record_clip(self.streamer, self.stream_id)
        record = {"ts": ts, "score": score, "multiple": multiple,
                  "risas": detail.get("risas", 0),
                  "hype": detail.get("hype", 0),
                  "at": datetime.now(timezone.utc).isoformat()}
        self.peaks.append(record)
        save_live_peaks(self.streamer, self.stream_id, self.peaks,
                        self.base_dir)
        notify("pico en directo",
               f"{self.streamer} ({self.platform}): pico a {ts:.0f}s "
               f"({score} pts, {multiple}x la mediana)")

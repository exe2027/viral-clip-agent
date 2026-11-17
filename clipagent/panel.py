"""Panel web local mínimo (Fase 6.2).

Solo localhost (127.0.0.1), sin dependencias nuevas (http.server
de la stdlib). Sirve para:
- pegar links de streamers (clipagent add),
- activar/desactivar streamers (interruptor),
- Iniciar/Detener el servicio,
- ver el estado de las plataformas (3.7), la cola, los últimos
  publicados, los retenidos con motivo y los errores recientes.

Uso: `clipagent panel [--port 8765]`. No hay autenticación: solo
escucha en localhost, nunca se expone a la red.
"""
import html
import os
import sqlite3
import subprocess
import sys
import threading
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HOST = "127.0.0.1"
DEFAULT_PORT = 8765


def _repo_root():
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _queue_db_path():
    return os.path.join(_repo_root(), "queue.db")


def _recent_errors(limit=10):
    """Últimos errores del job_log (failed/timeout/dead)."""
    path = _queue_db_path()
    if not os.path.isfile(path):
        return []
    try:
        conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        rows = conn.execute(
            "SELECT job_id, ts, event, detail FROM job_log "
            "WHERE event IN ('failed', 'timeout', 'dead', 'error') "
            "ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
        conn.close()
        return [{"job_id": r[0], "ts": r[1], "event": r[2],
                 "detail": r[3]} for r in rows]
    except sqlite3.Error:
        return []


def _dashboard_data():
    from jobqueue import Queue
    from . import breaker as _breaker
    from .publish import PublishQueue
    from .service import service_status
    from .status import platform_states
    from .streamers import load_streamers
    q = Queue()
    try:
        counts = q.counts()
        jobs = q.list_jobs()[-10:]
    finally:
        q.close()
    pq = PublishQueue()
    items = pq.data.get("items", [])
    published = [i for i in items if i["state"] == "published"][-10:]
    retained = [i for i in items if i["state"] == "retained"]
    drafts = [i for i in items if i["state"] == "draft"]
    return {
        "service": service_status(),
        "platforms": platform_states(),
        "breaker": {p: _breaker.is_tripped(p)
                    for p in ("youtube_shorts", "tiktok",
                              "instagram_reels", "facebook_reels")},
        "streamers": load_streamers(),
        "queue_counts": counts,
        "jobs": jobs,
        "published": published,
        "retained": retained,
        "drafts": drafts,
        "errors": _recent_errors(),
    }


def _esc(text):
    return html.escape(str(text or ""))


def _render(data):
    svc = data["service"]
    rows_platforms = "".join(
        f"<tr><td>{_esc(p)}</td><td>{_esc(v['state'])}</td>"
        f"<td>{_esc(v.get('motivo', ''))}</td>"
        f"<td>{'🔴 breaker' if data['breaker'][p][0] else ''}</td>"
        f"<td>{'' if data['breaker'][p][0] else ''}"
        f"<form method='post' action='/resume' style='display:inline'>"
        f"<input type='hidden' name='platform' value='{_esc(p)}'>"
        f"<button {'disabled' if not data['breaker'][p][0] else ''}"
        f">Reanudar</button></form></td></tr>"
        for p, v in data["platforms"].items())
    rows_streamers = "".join(
        f"<tr><td>{_esc(s['name'])}</td><td>{_esc(s['platform'])}</td>"
        f"<td>{_esc(s.get('game', ''))}</td>"
        f"<td>{'✅' if s.get('enabled', True) else '⏸️'}</td>"
        f"<td><form method='post' action='/toggle'>"
        f"<input type='hidden' name='name' value='{_esc(s['name'])}'>"
        f"<input type='hidden' name='enabled' "
        f"value='{0 if s.get('enabled', True) else 1}'>"
        f"<button>{'Desactivar' if s.get('enabled', True) else 'Activar'}"
        f"</button></form></td></tr>"
        for s in data["streamers"])
    rows_queue = "".join(
        f"<tr><td>#{j['id']}</td><td>{_esc(j['state'])}</td>"
        f"<td>{_esc(j.get('streamer') or '')}</td>"
        f"<td>{_esc(j['url'])}</td></tr>"
        for j in data["jobs"])
    rows_pub = "".join(
        f"<tr><td>{_esc(i['platform'])}</td>"
        f"<td>{_esc(i['clip'])}</td>"
        f"<td>{_esc(i.get('published_at') or '')}</td></tr>"
        for i in data["published"])
    rows_ret = "".join(
        f"<tr><td>{_esc(i['platform'])}</td>"
        f"<td>{_esc(i['clip'])}</td>"
        f"<td>{_esc((i.get('history') or [{}])[-1].get('note', ''))}</td>"
        f"</tr>" for i in data["retained"])
    rows_err = "".join(
        f"<tr><td>#{e['job_id']}</td><td>{_esc(e['ts'])}</td>"
        f"<td>{_esc(e['event'])}</td><td>{_esc(e['detail'])}</td></tr>"
        for e in data["errors"])
    return f"""<!DOCTYPE html><html lang="es"><head><meta charset="utf-8">
<title>viral-clip-agent — panel local</title></head><body>
<h1>🎬 viral-clip-agent (solo localhost)</h1>
<p>Servicio: <b>{'🟢 corriendo' if svc['corriendo'] else '⚪ detenido'}</b>
{f"(pid {svc['supervisor_pid']})" if svc['supervisor_pid'] else ''}
<form method="post" action="/start" style="display:inline">
<button {'disabled' if svc['corriendo'] else ''}>▶️ Iniciar</button></form>
<form method="post" action="/stop" style="display:inline">
<button {'disabled' if not svc['corriendo'] else ''}>⏹️ Detener</button></form></p>
<p>Cola: {_esc(", ".join(f"{k}: {v}" for k, v in sorted(svc['cola'].items())))}</p>
<h2>Pegar link de streamer</h2>
<form method="post" action="/add">
<input name="link" size="60" placeholder="twitch.tv/illojuan o kick.com/coker">
<button>Agregar</button></form>
<h2>Streamers</h2>
<table border="1"><tr><th>Nombre</th><th>Plataforma</th><th>Juego</th>
<th>Activo</th><th></th></tr>{rows_streamers}</table>
<h2>Plataformas</h2>
<table border="1"><tr><th>Plataforma</th><th>Estado</th><th>Motivo</th>
<th>Breaker</th><th></th></tr>{rows_platforms}</table>
<h2>Cola de jobs (últimos 10)</h2>
<table border="1"><tr><th>Job</th><th>Estado</th><th>Streamer</th>
<th>URL</th></tr>{rows_queue}</table>
<h2>Últimos publicados</h2>
<table border="1"><tr><th>Plataforma</th><th>Clip</th>
<th>Publicado</th></tr>{rows_pub}</table>
<h2>Retenidos con motivo</h2>
<table border="1"><tr><th>Plataforma</th><th>Clip</th>
<th>Motivo</th></tr>{rows_ret}</table>
<h2>Errores recientes</h2>
<table border="1"><tr><th>Job</th><th>Fecha</th><th>Evento</th>
<th>Detalle</th></tr>{rows_err}</table>
<p><a href="/">↻ Actualizar</a></p>
</body></html>"""


class _Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):  # no ensuciar la salida
        pass

    def _redirect(self):
        self.send_response(303)
        self.send_header("Location", "/")
        self.end_headers()

    def do_GET(self):
        if self.path in ("/", "/index.html"):
            data = _dashboard_data()
            body = _render(data).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        self.send_response(404)
        self.end_headers()

    def do_POST(self):
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length).decode("utf-8", "ignore")
        form = urllib.parse.parse_qs(raw)
        get = lambda k: (form.get(k) or [""])[0]  # noqa: E731
        if self.path == "/add":
            from .links import add_link
            ok, msg = add_link(get("link"))
            print(f"[panel] add {get('link')!r}: {msg}")
        elif self.path == "/toggle":
            from .streamers import set_enabled
            set_enabled(get("name"), get("enabled") == "1")
        elif self.path == "/start":
            from .service import service_running
            if not service_running():
                subprocess.Popen(
                    [sys.executable, "-m", "clipagent", "start"],
                    cwd=_repo_root(), start_new_session=True,
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        elif self.path == "/stop":
            from .service import stop_service
            threading.Thread(target=stop_service, daemon=True).start()
        elif self.path == "/resume":
            from .breaker import reset
            reset(get("platform"))
        self._redirect()


def make_server(port=DEFAULT_PORT, host=HOST):
    """Servidor del panel (solo localhost). Puerto 0 = efímero (tests)."""
    return ThreadingHTTPServer((host, port), _Handler)


def run_panel(port=DEFAULT_PORT):
    server = make_server(port)
    print(f"Panel local: http://127.0.0.1:{server.server_address[1]}/ "
          "(solo localhost, Ctrl+C para parar)")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    return 0

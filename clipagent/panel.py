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
    from .publish import PLATFORMS, PublishQueue
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
    # 6B.6: cola de publicación por prioridad, con desglose.
    scores = {p: pq.scored_items(p) for p in PLATFORMS}
    # 9.1: analíticas + estado del aprendizaje (6B.7).
    from .metrics import report as _metrics_report
    from .priority import load_weights as _load_weights
    weights, history = _load_weights()
    # 9.2: directos seguidos (picos en vivo).
    live = []
    live_root = os.path.join(_repo_root(), "runs", "live")
    if os.path.isdir(live_root):
        for name in sorted(os.listdir(live_root)):
            path = os.path.join(live_root, name, "live_peaks.json")
            if os.path.isfile(path):
                try:
                    import json
                    with open(path, encoding="utf-8") as f:
                        peaks = json.load(f)
                    live.append({"session": name, "peaks": peaks})
                except (OSError, ValueError):
                    continue
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
        "scores": scores,
        "analytics": _metrics_report(),
        "weights": weights,
        "weights_history": history,
        "live": live,
        "errors": _recent_errors(),
    }


def _esc(text):
    return html.escape(str(text or ""))


def _formula(prio):
    """Desglose corto de la fórmula de prioridad (6B.6)."""
    w = prio.get("weights") or {}
    text = (f"V{prio.get('virality', 0):.0f}^{w.get('viral', 0.5)} × "
            f"S{prio.get('seo', 0):.0f}^{w.get('seo', 0.3)} × "
            f"C{prio.get('chat_normalized', 0):.0f}^{w.get('chat', 0.2)}")
    penalty = prio.get("age_penalty") or 0
    if penalty:
        text += f" −{penalty:.0f} edad ({prio.get('age_days', 0)}d)"
    return text


def register_override_for_clip(clip_path, platform, motivo):
    """6B.5: registra un override de USUARIO en el manifiesto.

    Es la ÚNICA forma de registrar un override: ningún código
    automático lo hace (la política solo lo lee). El override
    permite publicar un clip con SEO 70-89 en esa plataforma;
    con SEO < 70 sigue siendo bloqueo duro.
    Devuelve (ok, mensaje).
    """
    import json
    from .publish import load_clip_package
    from .seo_score import register_override
    clip, manifest = load_clip_package(clip_path)
    if clip is None:
        return False, "sin entrada en el manifiesto"
    score = ((clip.get("seo_scores") or {}).get(platform) or {}).get("score")
    record = register_override(
        manifest, platform, score,
        motivo or "revisado a mano en el panel")
    run_dir = os.path.dirname(os.path.dirname(os.path.abspath(clip_path)))
    manifest_path = os.path.join(run_dir, "manifest.json")
    tmp = manifest_path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=2, ensure_ascii=False)
    os.replace(tmp, manifest_path)
    return True, (f"override registrado en {platform} "
                  f"(SEO {score}): {record['motivo']}")


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
        f"<td><form method='post' action='/override'>"
        f"<input type='hidden' name='clip' value='{_esc(i['clip'])}'>"
        f"<input type='hidden' name='platform' "
        f"value='{_esc(i['platform'])}'>"
        f"<input name='motivo' size='24' "
        f"placeholder='motivo del override'>"
        f"<button>Override (publicar con SEO 70-89)</button></form></td>"
        f"</tr>" for i in data["retained"])
    rows_err = "".join(
        f"<tr><td>#{e['job_id']}</td><td>{_esc(e['ts'])}</td>"
        f"<td>{_esc(e['event'])}</td><td>{_esc(e['detail'])}</td></tr>"
        for e in data["errors"])
    # Fan-out: borradores con motivo visible (sin token, sin
    # auditoría, sin URL pública, sin IDs). Un draft no se reintenta
    # solo: hay que resolver el motivo. El paquete manual está en
    # drafts/ para subir a mano.
    rows_draft = "".join(
        f"<tr><td>{_esc(i['platform'])}</td>"
        f"<td>{_esc(i['clip'])}</td>"
        f"<td>{_esc((i.get('history') or [{}])[-1].get('note', ''))}"
        f"</td></tr>"
        for i in data["drafts"])
    # 6B.6: cola de publicación por plataforma, ordenada por
    # prioridad, con viralidad, SEO, desglose de la fórmula, estado
    # y motivo de retención.
    sections_scores = ""
    for platform, scored in data["scores"].items():
        rows = "".join(
            f"<tr><td>{s['queue_position']}</td>"
            f"<td>{_esc(s['_item']['clip'])}</td>"
            f"<td>{s['virality']:.0f}</td>"
            f"<td>{s['seo']:.0f}</td>"
            f"<td>{s['priority']['priority']:.1f}</td>"
            f"<td>{_esc(_formula(s['priority']))}</td>"
            f"<td>{_esc(s['status'])}</td>"
            f"<td>{_esc(s.get('left_behind') or s['status_reason'])}</td>"
            f"</tr>" for s in scored)
        sections_scores += (
            f"<h3>{_esc(platform)}</h3>"
            f"<table border='1'><tr><th>#</th><th>Clip</th>"
            f"<th>Viralidad</th><th>SEO</th><th>Prioridad</th>"
            f"<th>Fórmula</th><th>Estado</th><th>Motivo</th></tr>"
            f"{rows}</table>")
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
<h2>Cola de publicación (por prioridad, 6B.6)</h2>
<p>Orden por prioridad (viralidad^0,5 × SEO^0,3 × chat^0,2, con
penalización por edad). La cola nunca es FIFO: el SEO cambia por
plataforma, así que el orden también.</p>
{sections_scores}
<h2>Analíticas (9.1)</h2>
<p>Pesos del puntaje: viral {_esc(data['weights'].get('viral'))}, SEO {_esc(data['weights'].get('seo'))}, chat {_esc(data['weights'].get('chat'))}
{' (BETA: menos de 30 clips con datos a 7 días)' if not data['weights_history'] else f" — {len(data['weights_history'])} recalibración(es)"}.
Recalibrar: <code>clipagent recalibrate</code>.</p>
<table border="1"><tr><th>Plataforma</th><th>Clips</th>
<th>Medidos 24 h</th><th>Mediana 24 h</th>
<th>Medidos 48 h</th><th>Mediana 48 h</th><th>Mejor clip</th></tr>
{''.join(f"<tr><td>{_esc(p)}</td><td>{v['clips']}</td>"
         f"<td>{v['medidos_24h']}</td><td>{v['mediana_24h']}</td>"
         f"<td>{v['medidos_48h']}</td><td>{v['mediana_48h']}</td>"
         f"<td>{_esc((v['mejor_clip'] or {}).get('clip', ''))}"
         f" ({(v['mejor_clip'] or {}).get('views', '')})</td></tr>"
         for p, v in data['analytics'].items())}</table>
<h2>Directos en vivo (9.2)</h2>
<p>Picos detectados en directo (la grabación por segmentos está
bloqueada: sin descargador de directos; los picos quedan marcados
y el VOD se procesa al terminar). Seguir un directo:
<code>clipagent live &lt;streamer&gt; [--platform kick] [--minutes N]</code></p>
<table border="1"><tr><th>Sesión</th><th>Picos</th><th>Último pico</th></tr>
{''.join(f"<tr><td>{_esc(s['session'])}</td><td>{len(s['peaks'])}</td>"
         f"<td>{_esc((s['peaks'][-1].get('at', '') if s['peaks'] else ''))}</td></tr>"
         for s in data['live'])}</table>
<h2>Últimos publicados</h2>
<table border="1"><tr><th>Plataforma</th><th>Clip</th>
<th>Publicado</th></tr>{rows_pub}</table>
<h2>Retenidos con motivo</h2>
<p>El override lo registra solo el usuario desde aquí (6B.5): ningún
automatismo lo hace. Con SEO &lt; 70 sigue siendo bloqueo duro.</p>
<table border="1"><tr><th>Plataforma</th><th>Clip</th>
<th>Motivo</th><th>Override</th></tr>{rows_ret}</table>
<h2>Borradores (draft)</h2>
<p>Clips que no se publicaron por API: el motivo está en la última
nota y el paquete manual está en drafts/ para subir a mano. Un
draft por falta de credenciales, auditoría o configuración NO se
reintenta solo: hay que resolver el motivo (ver
<code>clipagent doctor</code>).</p>
<table border="1"><tr><th>Plataforma</th><th>Clip</th>
<th>Motivo</th></tr>{rows_draft}</table>
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
        elif self.path == "/override":
            ok, msg = register_override_for_clip(
                get("clip"), get("platform"), get("motivo"))
            print(f"[panel] override {get('clip')!r}: {msg}")
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

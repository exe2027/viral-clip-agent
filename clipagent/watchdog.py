#!/usr/bin/env python3
"""Vigilante con auto-reparación (2026-11-19, orden del usuario).

REGLA PRINCIPAL: los logs son la primera fuente. El vigilante lee
el mismo flujo de logs que genera las alertas de Telegram
(worker_events vía GET /api/worker/state -> activity, y clip_events
vía GET /api/clips/:id/tracking) y el diagnóstico sale de ahí. Los
chequeos de estado (último clip, vivos en GTA VI, token de Twitch,
última corrida de GitHub) CORROBORAN, no mandan: un atasco requiere
(a) una causa clasificada en los logs O silencio del ciclo, Y (b)
último clip más viejo que stuck_after_min, Y (c) vivos en GTA VI
ahora mismo. Sin vivos no es atasco: el cerebro pasa a VOD solo
(lo cubre cerebro-live, no el vigilante).

Escalera de reparación, EN ORDEN, con cooldown (máx 1 escalera
cada cooldown_min; el último intento queda en
runs/watchdog/state.json):
  1. REDISPATCH: workflow_dispatch a cerebro-live por API de GitHub
     (antes se verifica que no haya ya una corrida en curso/en cola).
  2. REFRESH_TWITCH_TOKEN: se valida el token actual; si falla se
     pide uno nuevo por client-credentials y se valida; si ESO falla
     se registra "sospecha: secreto de Twitch revocado" y se sigue.
  3. RESTART_WORKER: POST /api/worker/start (el botón Iniciar del
     panel: pone running=1 y dispara panel-start, que escucha
     cerebro.yml).
  4. VOD_FALLBACK: el camino VOD dentro de la propia corrida
     (main de vod_fallback importado, nunca copiado; gates 45/80,
     máx 2 clips, enfriamiento por VOD).

Cada paso queda registrado con print "[watchdog]" y, cuando el
panel lo permite, con report_phase (ver _record_step: el servidor
SOLO acepta kinds de clip y fases cerradas, así que los pasos del
vigilante NO se escriben como eventos hasta que el panel acepte el
kind "watchdog"; ver el informe). Nunca publica ni borra nada.

Telegram SOLO si después de toda la escalera sigue atascado: el
mensaje lleva el diagnóstico + las líneas de log citadas + qué se
intentó + qué necesita un humano. El envío va por
PanelBridge.notify_telegram (POST /api/bridge/notify); si la ruta
no existe aún, el método falla con mensaje claro y el Telegram
queda como borrador en el log.

CLI:
    python -m clipagent.watchdog [--tenant NAME] [--dry-run]
        [--simulate-stuck] [--simulate-all-fail] [--no-panel]
Exit 0 SIEMPRE salvo crash: un chequeo sin atasco nunca falla la
corrida. Salida en español, una línea por paso.

Inyección para tests: diagnose/repair aceptan clientes inyectables
(panel, github, twitch, telegram, vod, live_scan, recheck).
"""
import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field

# Acciones que puede devolver classify_log_event.
REFRESH_TWITCH_TOKEN = "REFRESH_TWITCH_TOKEN"
REDISPATCH = "REDISPATCH"
RESTART_WORKER = "RESTART_WORKER"
VOD_FALLBACK = "VOD_FALLBACK"
LOG_ONLY = "LOG_ONLY"  # no es atasco del cerebro: solo se registra

# Orden de la escalera (también orden de prioridad de la causa).
LADDER = (REDISPATCH, REFRESH_TWITCH_TOKEN, RESTART_WORKER,
          VOD_FALLBACK)

GITHUB_API = "https://api.github.com"
GITHUB_REPO = "exe2027/viral-clip-agent"
TWITCH_VALIDATE_URL = "https://id.twitch.tv/oauth2/validate"
TWITCH_TOKEN_URL = "https://id.twitch.tv/oauth2/token"
DEFAULT_STATE_PATH = os.path.join("runs", "watchdog", "state.json")

WATCHDOG_REQUIRED = (
    "stuck_after_min", "cooldown_min", "no_dispatch_min",
    "no_cycle_min", "max_vod_clips", "max_redispatch_per_hour",
    "telegram_enabled", "dispatch_workflow", "watchdog_workflow",
)


def _log(msg):
    print(f"[watchdog] {msg}", flush=True)


def load_watchdog_config(cfg=None):
    """Config del vigilante desde config.yaml (única fuente).

    Regla single-source del repo: si falta la sección o un valor,
    falla fuerte con el nombre del valor (ConfigError), nunca con
    un default silencioso. ``cfg`` puede ser el dict de
    load_config() o None (carga config.yaml en disco).
    """
    from .validate import ConfigError

    if cfg is None:
        from .config import load_config
        cfg = load_config()
    section = (cfg or {}).get("watchdog")
    if not isinstance(section, dict):
        raise ConfigError(
            "watchdog: falta la sección 'watchdog' en config.yaml "
            "(el vigilante no arranca sin sus umbrales; sin "
            "defaults silenciosos)")
    missing = [k for k in WATCHDOG_REQUIRED if k not in section]
    if missing:
        raise ConfigError(
            "watchdog: faltan claves en config.yaml: "
            + ", ".join(f"watchdog.{k}" for k in missing)
            + " (sin defaults silenciosos)")
    out = {}
    for key in ("stuck_after_min", "cooldown_min", "no_dispatch_min",
                "no_cycle_min", "max_vod_clips",
                "max_redispatch_per_hour"):
        value = section[key]
        if not isinstance(value, int) or isinstance(value, bool):
            raise ConfigError(
                f"watchdog.{key}: debe ser entero, es {value!r}")
        out[key] = value
    if not isinstance(section["telegram_enabled"], bool):
        raise ConfigError(
            "watchdog.telegram_enabled: debe ser true/false, es "
            f"{section['telegram_enabled']!r}")
    out["telegram_enabled"] = section["telegram_enabled"]
    for key in ("dispatch_workflow", "watchdog_workflow"):
        value = section[key]
        if not isinstance(value, str) or not value.strip():
            raise ConfigError(
                f"watchdog.{key}: debe ser texto no vacío, es "
                f"{value!r}")
        out[key] = value.strip()
    return out


# ---------------------------------------------------------------------------
# 1. Clasificación pura de UNA línea de log
# ---------------------------------------------------------------------------

def _has(text, *needles):
    return any(n in text for n in needles)


def classify_log_event(message, kind=None):
    """(acción | None) para UNA línea de log. Función PURA.

    ``message`` es el texto del evento, ``kind`` su kind en
    worker_events (github_dispatch, live_dispatch, ciclo,
    auto_publish, analytics, aviso, corte_fallido, ...). Las
    líneas informativas devuelven None: no son causa de atasco.
    """
    text = " ".join(str(message or "").split()).lower()
    k = str(kind or "").strip().lower()

    # Guardas primero: lo que PARECE fallo pero es funcionamiento
    # normal. Una corrida en curso frena el dispatch a propósito
    # (throttle del Worker, 15 min); la medición de analíticas que
    # falla no corta clips; un clip frenado por calentamiento o por
    # falta de subtítulos es una decisión del panel, no un atasco.
    if _has(text, "corrida de cerebro-live en curso",
            "corrida en curso"):
        return None
    if _has(text, "frenado por calentamiento", "frenado en",
            "sin subtítulos completos", "sin subtítulos",
            "medición automática"):
        return None
    if _has(text, "ciclo automático:"):
        return None
    if k == "analytics":
        return None

    # 1. Token de Twitch inválido/expirado (401/403 de Twitch o
    # Helix). OJO: un 403 de YouTube ("permiso denegado (HTTP 403):
    # el token concedido no incluye youtube.readonly") NO es
    # Twitch: exige la palabra twitch/helix en la línea.
    twitchish = _has(text, "twitch", "helix")
    if twitchish and _has(
            text, "401", "403", "token inválido", "token expirado",
            "token caducado", "token revocado", "invalid token",
            "unauthorized", "token no válido"):
        return REFRESH_TWITCH_TOKEN
    if twitchish and _has(text, "token") and _has(
            text, "inválido", "expirado", "caducado", "revocado",
            "no válido"):
        return REFRESH_TWITCH_TOKEN

    # 2. Dispatch fallido (o sin token para emitirlo): el cerebro
    # nunca se entera. "Dispatch ... enviado" NO es fallo: eso lo
    # decide el cruce con GitHub en diagnose().
    if _has(text, "dispatch") and _has(
            text, "falló", "fallo", "error", "no se pudo"):
        return REDISPATCH
    if _has(text, "no se pudo disparar", "no se pudo emitir el token",
            "sin github_token", "sin github token"):
        return REDISPATCH

    # 3. El Worker no barrió / no hubo ciclo / Worker parado.
    if _has(text, "no hubo ciclo", "sin ciclo", "ciclo fallido",
            "el worker no barrió", "no barrió", "worker parado",
            "worker detenido", "worker no está en marcha",
            "no se pudo consultar la fase en vivo"):
        return RESTART_WORKER

    # 4. Sin directos en GTA VI: el camino es VOD, no re-disparar.
    if _has(text, "sin directos", "nadie en gta vi",
            "nadie está en vivo", "nadie en vivo en gta"):
        return VOD_FALLBACK

    # 5. Job / corte fallido: depende del job.
    if k == "corte_fallido" or _has(text, "corte fallido") or (
            "job" in text and "fallido" in text):
        # Rechazo de puerta (viralidad bajo el mínimo): esperado,
        # no es atasco ni se arregla re-disparando.
        if _has(text, "bajo el mínimo", "bajo el minimo"):
            return None
        # Job de publicación: no es atasco del cerebro, solo se
        # registra (el cerebro no corta por un fallo de publicar).
        if _has(text, "publicar", "publicación", "publicacion",
                "auto_publish", "publish"):
            return LOG_ONLY
        # Job de corte en vivo (o sin tipo claro pero con pinta de
        # corte: "pico", "cortar"): re-disparar el cerebro.
        if _has(text, "corte", "cortar", "pico", "live"):
            return REDISPATCH
        return LOG_ONLY

    return None


# ---------------------------------------------------------------------------
# 2. Diagnóstico (logs primero, estado corrobora)
# ---------------------------------------------------------------------------

@dataclass
class Diagnosis:
    stuck: bool = False
    cause: str | None = None
    cause_label: str = ""
    evidence: list = field(default_factory=list)
    actions: list = field(default_factory=list)
    live_now: list = field(default_factory=list)
    last_clip_age_min: float | None = None
    token_ok: bool | None = None
    last_run: dict | None = None
    dispatch_without_run: bool = False
    notes: list = field(default_factory=list)


def _parse_run_time(value):
    """Epoch ms de created_at (ISO de GitHub o ms)."""
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return int(value)
    text = str(value).strip()
    if text.isdigit():
        return int(text)
    try:
        from datetime import datetime
        dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
        return int(dt.timestamp() * 1000)
    except ValueError:
        return None


def _fmt_age(ms, now_ms):
    if ms is None:
        return "desconocido"
    mins = (now_ms - ms) / 60000
    if mins < 60:
        return f"hace {mins:.0f} min"
    return f"hace {mins / 60:.1f} h"


def diagnose(logs, state, github_runs, cfg, live_now=None,
             token_ok=None):
    """Diagnóstico desde los logs + corroboración de estado.

    logs: [{at (ms), kind, message}] (worker_events + clip_events).
    state: dict de worker_state() ({running, lastClipAt,
    lastSweepAt, ...}; acepta también last_clip_at_ms y now_ms
    para tests). github_runs: [{created_at, status, conclusion}]
    del workflow de dispatch, más reciente primero. cfg: dict de
    load_watchdog_config() (plano) o el dict completo de
    load_config() (con la sección "watchdog"). live_now: [{name,
    ...}] en vivo en GTA VI (scan_live). token_ok: bool|None
    (validación Twitch).
    """
    if isinstance(cfg, dict) and "stuck_after_min" in cfg:
        wcfg = cfg
    else:
        wcfg = load_watchdog_config(cfg)
    now_ms = int((state or {}).get("now_ms") or time.time() * 1000)
    running = bool((state or {}).get("running", True))
    last_clip_ms = (state or {}).get("lastClipAt")
    if last_clip_ms is None:
        last_clip_ms = (state or {}).get("last_clip_at_ms")
    age_min = ((now_ms - last_clip_ms) / 60000
               if last_clip_ms else None)
    live = list(live_now or [])
    diag = Diagnosis(live_now=live, last_clip_age_min=age_min,
                     token_ok=token_ok)
    events = sorted(logs or [], key=lambda e: e.get("at") or 0,
                    reverse=True)

    # (a) Clasificar cada línea; la evidencia cita el texto tal cual.
    found = {}
    for ev in events:
        msg = str(ev.get("message") or "")
        action = classify_log_event(msg, ev.get("kind"))
        if action is None:
            continue
        if action == LOG_ONLY:
            diag.notes.append(f"solo registro (no atasco): {msg}")
            continue
        found.setdefault(action, []).append(msg)
    for action in LADDER:
        if action in found:
            diag.actions.append(action)
            diag.evidence.extend(found[action][:3])

    # Cruce dispatch-sin-corrida: el log del Worker dice "enviado"
    # pero GitHub no tiene corrida posterior. Incidente 2026-11-19:
    # dispatches 21:13/21:41/21:57 sin corrida (última 44639214161,
    # 20:37); el tipo de evento no disparaba ningún workflow.
    last_dispatch_ms = None
    last_dispatch_msg = ""
    for ev in events:
        msg = str(ev.get("message") or "")
        if (ev.get("kind") == "github_dispatch"
                and "enviado" in msg.lower()):
            last_dispatch_ms = ev.get("at")
            last_dispatch_msg = msg
            break
    runs = list(github_runs or [])
    last_run_ms = None
    if runs:
        diag.last_run = runs[0]
        last_run_ms = _parse_run_time(runs[0].get("created_at"))
    if last_dispatch_ms is not None:
        gap_min = ((last_dispatch_ms - (last_run_ms or 0)) / 60000
                   if last_run_ms else None)
        if last_run_ms is None or gap_min is None or \
                gap_min > wcfg["no_dispatch_min"]:
            diag.dispatch_without_run = True
            if REDISPATCH not in diag.actions:
                diag.actions.insert(0, REDISPATCH)
            diag.evidence.append(last_dispatch_msg)
            diag.evidence.append(
                "última corrida de GitHub: "
                + (_fmt_age(last_run_ms, now_ms)
                   if last_run_ms else "ninguna"))
            diag.notes.append(
                "el dispatch no dispara ninguna corrida "
                f"(dispatch {_fmt_age(last_dispatch_ms, now_ms)}; "
                "última corrida "
                + (_fmt_age(last_run_ms, now_ms)
                   if last_run_ms else "ninguna") + ")")

    # Silencio del ciclo: ningún evento live_dispatch /
    # github_dispatch / ciclo en no_cycle_min con running=1.
    cycle_kinds = ("live_dispatch", "github_dispatch", "ciclo")
    last_cycle_ms = None
    for ev in events:
        if ev.get("kind") in cycle_kinds:
            last_cycle_ms = ev.get("at")
            break
    if running:
        silent_min = ((now_ms - last_cycle_ms) / 60000
                      if last_cycle_ms else None)
        if silent_min is None or silent_min > wcfg["no_cycle_min"]:
            if RESTART_WORKER not in diag.actions:
                diag.actions.append(RESTART_WORKER)
            diag.notes.append(
                "silencio del ciclo: sin eventos live_dispatch / "
                "github_dispatch / ciclo en "
                + (f"{silent_min:.0f} min" if silent_min is not None
                   else "todo el historial") + " con running=1")

    # El token inválido (chequeo de estado) corrobora el refresco.
    if token_ok is False and REFRESH_TWITCH_TOKEN not in diag.actions:
        diag.actions.append(REFRESH_TWITCH_TOKEN)
        diag.notes.append("el token de Twitch no valida "
                          "(chequeo de estado)")

    # Orden de la escalera.
    diag.actions = [a for a in LADDER if a in diag.actions]

    # (b) + (c): atasco solo con clip viejo Y vivos en GTA VI.
    clip_old = age_min is None or age_min >= wcfg["stuck_after_min"]
    has_cause = bool(diag.actions)
    if has_cause and clip_old and live:
        diag.stuck = True
    elif has_cause and clip_old and not live:
        diag.notes.append(
            "sin vivos en GTA VI: no es atasco; el cerebro pasa a "
            "VOD solo (lo cubre cerebro-live, no el vigilante)")
    elif has_cause and not clip_old:
        diag.notes.append(
            f"último clip {_fmt_age(last_clip_ms, now_ms)} "
            f"(< {wcfg['stuck_after_min']} min): aún no es atasco")

    if diag.stuck:
        diag.cause = diag.actions[0]
        labels = {
            REDISPATCH: "el cerebro no arranca (dispatch sin corrida "
                        "o dispatch fallido)",
            REFRESH_TWITCH_TOKEN: "token de Twitch inválido/expirado",
            RESTART_WORKER: "el Worker no está barriendo (silencio "
                            "del ciclo)",
            VOD_FALLBACK: "sin directos en GTA VI (camino VOD)",
        }
        if diag.dispatch_without_run:
            diag.cause = REDISPATCH
            diag.cause_label = ("el dispatch no dispara ninguna "
                                "corrida")
        else:
            diag.cause_label = labels.get(diag.cause, diag.cause)
    return diag


# ---------------------------------------------------------------------------
# 3. Clientes reales (GitHub / Twitch, urllib, sin dependencias)
# ---------------------------------------------------------------------------

class GitHubError(Exception):
    pass


class GitHubClient:
    """API de GitHub: corridas y workflow_dispatch (sin gh CLI)."""

    def __init__(self, token=None, repo=GITHUB_REPO):
        self.token = (token or os.environ.get("GH_TOKEN")
                      or os.environ.get("GITHUB_TOKEN") or "")
        self.repo = repo

    def _request(self, method, path, body=None):
        url = GITHUB_API + path
        headers = {"Accept": "application/vnd.github+json",
                   "User-Agent": "clipagent-watchdog"}
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        data = (json.dumps(body).encode("utf-8")
                if body is not None else None)
        req = urllib.request.Request(url, data=data, headers=headers,
                                     method=method)
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                payload = resp.read()
                return resp.status, (json.loads(
                    payload.decode("utf-8", "ignore") or "{}"))
        except urllib.error.HTTPError as e:
            detail = e.read().decode("utf-8", "ignore")[:200]
            raise GitHubError(f"{method} {path} -> HTTP {e.code}: "
                              f"{detail}")
        except urllib.error.URLError as e:
            raise GitHubError(f"{method} {path} -> {e}")

    def list_runs(self, workflow, per_page=5):
        """Corridas del workflow, más reciente primero."""
        _st, res = self._request(
            "GET", f"/repos/{self.repo}/actions/workflows/"
            f"{workflow}/runs?per_page={per_page}")
        return res.get("workflow_runs") or []

    def has_run_in_flight(self, workflow):
        """¿Hay una corrida en curso/en cola? (no sumar otra)."""
        for run in self.list_runs(workflow, per_page=5):
            if run.get("status") in ("queued", "in_progress",
                                     "waiting"):
                return True
        return False

    def workflow_dispatch_runs_since(self, workflow, since_ms,
                                     per_page=20):
        """created_at (epoch ms) de las corridas lanzadas por
        workflow_dispatch desde since_ms, más reciente primero.

        Solo event == "workflow_dispatch" (los panel-live del
        Worker son repository_dispatch y NO cuentan: son el
        funcionamiento normal, no re-disparos del vigilante).
        Una corrida sin campo event no cuenta (las respuestas
        reales de GitHub siempre lo traen). Es la memoria durable
        del vigilante: el estado local (runs/watchdog/state.json)
        NO sobrevive entre corridas (runner efímero), así que el
        cooldown y el tope horario se miden aquí, en GitHub.
        """
        out = []
        for run in self.list_runs(workflow, per_page=per_page):
            if run.get("event") != "workflow_dispatch":
                continue
            at = _parse_run_time(run.get("created_at"))
            if at is not None and at >= since_ms:
                out.append(at)
        return sorted(out, reverse=True)

    def dispatch_workflow(self, workflow, tenant, ref="main"):
        """workflow_dispatch. Devuelve True si GitHub lo aceptó."""
        status, _res = self._request(
            "POST", f"/repos/{self.repo}/actions/workflows/"
            f"{workflow}/dispatches",
            body={"ref": ref, "inputs": {"tenant": tenant}})
        return status in (201, 204)


class TwitchError(Exception):
    pass


class TwitchClient:
    """Validación + refresco del token de app de Twitch."""

    def __init__(self, client_id="", client_secret=""):
        self.client_id = client_id or os.environ.get(
            "TWITCH_CLIENT_ID", "")
        self.client_secret = client_secret or os.environ.get(
            "TWITCH_CLIENT_SECRET", "")

    def validate_token(self, token):
        """GET /oauth2/validate: True si el token vale."""
        if not token:
            return False
        req = urllib.request.Request(
            TWITCH_VALIDATE_URL, headers={
                "Authorization": f"Bearer {token}"}, method="GET")
        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                return resp.status == 200
        except urllib.error.HTTPError as e:
            if e.code in (401, 403):
                return False
            raise TwitchError(
                f"validar token de Twitch -> HTTP {e.code}")
        except urllib.error.URLError as e:
            raise TwitchError(f"validar token de Twitch -> {e}")

    def refresh_token(self):
        """Nuevo token por client-credentials + validación.

        Si Twitch rechaza las credenciales (400/403), el secreto
        está revocado o mal: se registra la sospecha y se sigue
        (el vigilante no se para por Twitch).
        """
        if not self.client_id or not self.client_secret:
            raise TwitchError(
                "sin TWITCH_CLIENT_ID/TWITCH_CLIENT_SECRET: no se "
                "puede refrescar el token (sospecha: secreto de "
                "Twitch revocado o sin configurar)")
        data = urllib.parse.urlencode({
            "client_id": self.client_id,
            "client_secret": self.client_secret,
            "grant_type": "client_credentials"}).encode("utf-8")
        req = urllib.request.Request(TWITCH_TOKEN_URL, data=data,
                                     method="POST")
        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                payload = json.loads(
                    resp.read().decode("utf-8", "ignore") or "{}")
        except urllib.error.HTTPError as e:
            raise TwitchError(
                "refrescar token de Twitch -> HTTP "
                f"{e.code}: sospecha de secreto de Twitch revocado")
        except urllib.error.URLError as e:
            raise TwitchError(f"refrescar token de Twitch -> {e}")
        token = payload.get("access_token") or ""
        if not token:
            raise TwitchError(
                "Twitch no devolvió access_token (sospecha: "
                "secreto de Twitch revocado)")
        if not self.validate_token(token):
            raise TwitchError(
                "el token nuevo de Twitch no valida (sospecha: "
                "secreto de Twitch revocado)")
        return token


# ---------------------------------------------------------------------------
# 4. Reparación (la escalera)
# ---------------------------------------------------------------------------

@dataclass
class RepairResult:
    repaired: bool = False
    skipped_cooldown: bool = False
    steps: list = field(default_factory=list)
    telegram_message: str = ""
    telegram_sent: bool = False
    telegram_note: str = ""


def build_telegram_message(diagnosis, steps, tenant=""):
    """Mensaje de Telegram: diagnóstico + líneas citadas + intentos.

    Solo se envía si tras toda la escalera sigue atascado.
    """
    lines = [
        "🚨 Vigilante: el cerebro sigue atascado"
        + (f" ({tenant})" if tenant else ""),
        "",
        f"Causa: {diagnosis.cause_label or diagnosis.cause or '?'}",
        ("Último clip: "
         + (f"hace {diagnosis.last_clip_age_min:.0f} min"
            if diagnosis.last_clip_age_min is not None
            else "desconocido")),
        ("En vivo en GTA VI: "
         + (", ".join(x.get("name", "?")
                      for x in diagnosis.live_now)
            if diagnosis.live_now else "nadie")),
        "",
        "Líneas del log que lo diagnosticaron:",
    ]
    for ev in (diagnosis.evidence or [])[:6]:
        lines.append(f"• {ev}")
    lines.append("")
    lines.append("Qué se intentó (escalera del vigilante):")
    for step in steps:
        mark = "✅" if step.get("ok") else "❌"
        lines.append(f"{mark} {step.get('step')}: "
                     f"{step.get('detail', '')}")
    lines.append("")
    lines.append("Qué necesita un humano:")
    if diagnosis.cause == REFRESH_TWITCH_TOKEN:
        lines.append("• Revisar el secreto de Twitch en el panel "
                     "(Cuentas): puede estar revocado; reautorizar "
                     "o pegar uno nuevo.")
    elif diagnosis.dispatch_without_run:
        lines.append("• Revisar en GitHub que el workflow "
                     "cerebro-live escuche el tipo de dispatch que "
                     "envía el Worker (hoy: panel-live).")
    elif diagnosis.cause == RESTART_WORKER:
        lines.append("• Entrar al panel (Worker) y pulsar Iniciar, "
                     "o revisar el cron del Worker en Cloudflare.")
    else:
        lines.append("• Revisar el panel (Worker) y las corridas de "
                     "GitHub: el vigilante ya intentó re-disparar, "
                     "refrescar el token, reiniciar el Worker y "
                     "cortar de VOD.")
    return "\n".join(lines)


def _load_state(path):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f) or {}
    except (OSError, ValueError):
        return {}


def _save_state(path, data):
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


def _record_step(panel, step, detail, phase=None):
    """Registra un paso: siempre en el log; al panel solo si la
    fase es una de las que el servidor acepta (modo_vod /
    sin_directos). Los pasos del vigilante NO van como eventos:
    POST /api/worker/event valida el kind contra
    (clip_cortado, clip_encolado, clip_descartado, corte_fallido)
    y "watchdog" devuelve 400 (worker-phase.ts:136,154). Hasta que
    el panel acepte el kind "watchdog", el registro del panel es
    solo la fase cuando aplica; el resto vive en el log de la
    corrida + runs/watchdog/state.json."""
    _log(f"{step}: {detail}")
    if panel is not None and phase:
        try:
            panel.report_phase(phase, detail=detail)
        except Exception:  # noqa: BLE001 - el reporte no tumba nada
            pass


def repair(diagnosis, clients, cfg, dry_run=False, tenant="",
           state_path=None, now_ms=None):
    """Ejecuta la escalera EN ORDEN. Devuelve RepairResult.

    clients: {panel, github, twitch, telegram, vod, live_scan,
    recheck}. panel/github/twitch/telegram son objetos con los
    métodos usados (inyectables en tests); vod es un callable
    (n, resumen) o un objeto con .run(); live_scan un callable
    -> {live: [...]}; recheck un callable -> bool (sigue atascado).
    """
    wcfg = cfg if "stuck_after_min" in cfg else load_watchdog_config(
        cfg)
    now = int(now_ms or time.time() * 1000)
    result = RepairResult()
    panel = clients.get("panel")
    if not diagnosis.stuck:
        _log("sin atasco: no se repara nada")
        for note in diagnosis.notes:
            _log(f"nota: {note}")
        if VOD_FALLBACK in diagnosis.actions and not diagnosis.live_now:
            _log("sin vivos: el modo VOD lo cubre cerebro-live")
        return result
    if dry_run:
        _log("dry-run: el atasco existe; esto es lo que se haría:")
        for action in diagnosis.actions:
            _log(f"  haría: {action}")
        _log("  si todo falla: Telegram con las líneas citadas")
        result.steps = [{"step": a, "ok": None,
                         "detail": "dry-run: no ejecutado"}
                        for a in diagnosis.actions]
        return result

    # Cooldown: máx 1 escalera cada cooldown_min (estado local del
    # runner; entre corridas lo frena además el chequeo de corrida
    # en curso en GitHub + el throttle del Worker).
    spath = state_path or DEFAULT_STATE_PATH
    state = _load_state(spath)
    last = state.get("last_attempt_at_ms")
    if last and now - last < wcfg["cooldown_min"] * 60000:
        left = wcfg["cooldown_min"] - (now - last) / 60000
        _log(f"cooldown: última escalera hace {(now - last) / 60000:.0f} "
             f"min; faltan {left:.0f} min para reintentar")
        result.skipped_cooldown = True
        return result
    # Cooldown DURABLE (2026-11-20, cuota D1): el estado local
    # (state.json) NO sobrevive entre corridas de GitHub (runner
    # efímero: cada corrida es un checkout limpio), así que el
    # cooldown local no frena nada entre corridas. El historial de
    # GitHub sí persiste: si el último re-disparo (workflow_dispatch
    # a cerebro-live) fue hace menos de cooldown_min, la escalera
    # ya corrió hace poco y no se repite. Sin cliente de GitHub o
    # si la lectura falla, manda solo el cooldown local (el
    # peldaño REDISPATCH tiene además su propio tope horario).
    github = clients.get("github")
    workflow = wcfg["dispatch_workflow"]
    last_redispatch_ms = None
    if github is not None:
        try:
            runs = github.workflow_dispatch_runs_since(
                workflow, now - wcfg["cooldown_min"] * 60000)
            last_redispatch_ms = runs[0] if runs else None
        except Exception:  # noqa: BLE001 - sin historial, manda el local
            last_redispatch_ms = None
    if last_redispatch_ms is not None:
        _log(f"cooldown durable: último re-disparo "
             f"{_fmt_age(last_redispatch_ms, now)}; la escalera ya "
             f"corrió hace menos de {wcfg['cooldown_min']} min")
        result.skipped_cooldown = True
        return result
    if not clients.get("_skip_state_write"):
        _save_state(spath, {"last_attempt_at_ms": now,
                            "cause": diagnosis.cause,
                            "tenant": tenant})

    recheck = clients.get("recheck")
    twitch = clients.get("twitch")
    vod_fn = clients.get("vod")
    actions = list(diagnosis.actions)
    pending = False  # un peldaño corrió sin reparar: se sigue bajando

    def step_done(step, ok, detail, phase=None):
        result.steps.append({"step": step, "ok": ok, "detail": detail})
        _record_step(panel, step, detail, phase=phase)

    def repaired_by(action, ok):
        """¿Este peldaño reparó el atasco?

        Con recheck: lo dice el recheck (nueva lectura de estado).
        Sin recheck: un peldaño que coincide con la causa se da
        por reparador (la siguiente corrida del vigilante, en
        ~10 min, vuelve a diagnosticar si no fue así).
        """
        if not ok:
            return False
        if recheck is None:
            return action == diagnosis.cause
        return not diag_still_stuck_after(recheck, diagnosis)

    # (a) REDISPATCH por workflow_dispatch (con chequeo previo de
    # corrida en curso: no sumar otra a la cola).
    if REDISPATCH in actions or pending:
        pending = True
        try:
            if github is None:
                raise GitHubError("sin cliente de GitHub")
            # Sin tenant validado no se dispara nada: nunca un
            # tenant por defecto (regla fail-loud; TenantError
            # cae en el except y el peldaño queda como fallido).
            from .tenant import require_tenant

            # Tope horario (2026-11-20, cuota D1): máx
            # max_redispatch_per_hour re-disparos por hora, contados
            # en el historial de GitHub (durable entre corridas;
            # el cooldown durable de arriba ya frena la escalera
            # si el último fue hace <cooldown_min, este tope es
            # la red de seguridad si ese chequeo no pudo correr).
            # Sin historial disponible, el tope no se aplica (el
            # resto de frenos —cooldown local, corrida en curso—
            # sigue vigente).
            recent_hour = []
            try:
                recent_hour = github.workflow_dispatch_runs_since(
                    workflow, now - 3600000)
            except Exception:  # noqa: BLE001 - sin historial, sin tope
                recent_hour = []
            # A4 (2026-11-20, anti-recaída): tope 3 re-disparos/día
            # + backoff 1/5/15/60, contados en el historial durable
            # de GitHub (misma fuente que el cooldown durable y el
            # tope horario: el estado local no sobrevive entre
            # corridas). El tope horario (2/h) sigue vigente y es
            # más estricto dentro de la hora; cooldown_min (30)
            # sigue siendo el suelo entre escaleras. Si el
            # historial no se puede leer, este freno no se aplica
            # (manda el resto de frenos), como el tope horario.
            from .retry import (redispatch_allowed,
                                santiago_day_start_ms)
            a4_block = None
            try:
                runs_today = github.workflow_dispatch_runs_since(
                    workflow, santiago_day_start_ms(now))
                _allowed, _why = redispatch_allowed(runs_today, now)
                if not _allowed:
                    a4_block = f"freno anti-recaída: {_why}"
            except Exception:  # noqa: BLE001 - sin historial, sin freno
                a4_block = None
            if a4_block:
                step_done(REDISPATCH, False,
                          f"{a4_block}: no se re-dispara")
            elif len(recent_hour) >= wcfg["max_redispatch_per_hour"]:
                step_done(REDISPATCH, False,
                          f"tope horario de re-disparos alcanzado "
                          f"({len(recent_hour)}/"
                          f"{wcfg['max_redispatch_per_hour']} en la "
                          f"última hora): no se re-dispara")
            elif github.has_run_in_flight(workflow):
                step_done(REDISPATCH, False,
                          "ya hay una corrida de cerebro-live en "
                          "curso/en cola: no se re-dispara")
                step_done(REDISPATCH, False,
                          "ya hay una corrida de cerebro-live en "
                          "curso/en cola: no se re-dispara")
            elif github.dispatch_workflow(
                    workflow, require_tenant(tenant)):
                ok = True
                step_done(REDISPATCH, True,
                          f"workflow_dispatch a {workflow} aceptado "
                          "por GitHub")
                if repaired_by(REDISPATCH, ok):
                    result.repaired = True
            else:
                step_done(REDISPATCH, False,
                          "GitHub no aceptó el workflow_dispatch")
        except Exception as e:  # noqa: BLE001 - un peldaño no tumba
            step_done(REDISPATCH, False, f"falló: {e}")
        if result.repaired:
            return _finish(result, diagnosis, clients, tenant, wcfg)

    # (b) REFRESH_TWITCH_TOKEN: validar y, si falla, pedir uno nuevo.
    if REFRESH_TWITCH_TOKEN in actions or pending:
        pending = True
        try:
            if twitch is None:
                raise TwitchError("sin cliente de Twitch")
            token = twitch.refresh_token()
            step_done(REFRESH_TWITCH_TOKEN, True,
                      "token nuevo de Twitch pedido y validado "
                      f"(termina en …{token[-4:]})"
                      if token else "token nuevo validado")
            if repaired_by(REFRESH_TWITCH_TOKEN, True):
                result.repaired = True
        except Exception as e:  # noqa: BLE001 - se sigue al peldaño
            step_done(REFRESH_TWITCH_TOKEN, False, f"falló: {e}")
        if result.repaired:
            return _finish(result, diagnosis, clients, tenant, wcfg)

    # (c) RESTART_WORKER: POST /api/worker/start (botón Iniciar).
    if RESTART_WORKER in actions or pending:
        pending = True
        try:
            if panel is None or not hasattr(panel, "restart_worker"):
                raise RuntimeError(
                    "sin panel o sin restart_worker: el reinicio "
                    "queda como paso documentado pendiente del "
                    "panel (POST /api/worker/start)")
            res = panel.restart_worker()
            running = (res.get("running")
                       if isinstance(res, dict) else "?")
            step_done(RESTART_WORKER, True,
                      "Worker reiniciado desde el panel "
                      f"(running={running})")
            if repaired_by(RESTART_WORKER, True):
                result.repaired = True
        except Exception as e:  # noqa: BLE001 - se sigue al peldaño
            step_done(RESTART_WORKER, False, f"falló: {e}")
        if result.repaired:
            return _finish(result, diagnosis, clients, tenant, wcfg)

    # (d) VOD_FALLBACK: último recurso; corta de VOD dentro de esta
    # corrida (nunca publica: vod_fallback solo encola). Reparado
    # solo si encola al menos un clip (con vivos, vod_fallback no
    # corta nada y devuelve 0: no es reparación).
    if VOD_FALLBACK in actions or pending:
        pending = True
        try:
            if vod_fn is None:
                from .vod_fallback import run as _vod_run
                vod_fn = _vod_run
            if callable(vod_fn):
                n, summary = vod_fn(
                    max_vods=2, max_clips=wcfg["max_vod_clips"],
                    top_n=2, bridge=panel)
            else:
                n, summary = vod_fn.run(
                    max_vods=2, max_clips=wcfg["max_vod_clips"],
                    top_n=2, bridge=panel)
            step_done(VOD_FALLBACK, True,
                      f"modo VOD: {n} clip(s) encolado(s) — {summary}",
                      phase="modo_vod")
            if n > 0:
                result.repaired = True
            elif recheck is not None and \
                    not diag_still_stuck_after(recheck, diagnosis):
                result.repaired = True
        except Exception as e:  # noqa: BLE001 - último peldaño
            step_done(VOD_FALLBACK, False, f"falló: {e}")
        if result.repaired:
            return _finish(result, diagnosis, clients, tenant, wcfg)

    if not pending:
        _log("atasco sin acciones en la escalera: nada que ejecutar")
    return _finish(result, diagnosis, clients, tenant, wcfg)


def diag_still_stuck_after(recheck, diagnosis):
    """True si el recheck dice que sigue atascado (o no hay recheck
    y la causa sigue sin resolverse: sin recheck, un peldaño que
    coincide con la causa se da por reparador)."""
    if recheck is None:
        return False
    try:
        return bool(recheck())
    except Exception:  # noqa: BLE001 - ante la duda, sigue atascado
        return True


def _finish(result, diagnosis, clients, tenant, wcfg):
    if result.repaired:
        _log("reparado: el cerebro vuelve a suministrar clips")
        return result
    if not result.steps:
        return result
    # Telegram SOLO si tras toda la escalera sigue atascado.
    result.telegram_message = build_telegram_message(
        diagnosis, result.steps, tenant=tenant)
    if not wcfg.get("telegram_enabled", True):
        result.telegram_note = ("Telegram desactivado en config "
                                "(telegram_enabled: false): el "
                                "mensaje queda como borrador en el "
                                "log")
        _log(result.telegram_note)
        _log("borrador de Telegram:\n" + result.telegram_message)
        return result
    telegram = clients.get("telegram") or clients.get("panel")
    try:
        if telegram is None or not hasattr(telegram,
                                           "notify_telegram"):
            raise RuntimeError(
                "sin cliente de Telegram (falta la ruta "
                "POST /api/bridge/notify en el panel)")
        telegram.notify_telegram(result.telegram_message)
        result.telegram_sent = True
        _log("Telegram enviado con el diagnóstico y las líneas "
             "citadas")
    except Exception as e:  # noqa: BLE001 - el aviso no tumba nada
        result.telegram_note = (
            "Telegram NO enviado "
            f"({e}); el mensaje queda como borrador en el log")
        _log(result.telegram_note)
        _log("borrador de Telegram:\n" + result.telegram_message)
    return result


# ---------------------------------------------------------------------------
# 5. CLI
# ---------------------------------------------------------------------------

def _read_panel_logs(panel):
    """Logs del panel: worker_events + tracking de clips recientes."""
    logs = []
    try:
        for ev in panel.list_worker_events():
            logs.append({"at": ev.get("at"),
                         "kind": ev.get("kind"),
                         "message": ev.get("message") or ""})
    except Exception as e:  # noqa: BLE001 - sin logs no hay diagnóstico
        _log(f"no se pudieron leer los eventos del Worker ({e})")
    try:
        for clip in (panel.list_clips() or [])[:5]:
            for ev in panel.list_clip_events(clip.get("id")):
                logs.append({"at": ev.get("at"),
                             "kind": ev.get("kind") or "tracking",
                             "message": ev.get("message") or ""})
    except Exception as e:  # noqa: BLE001 - el tracking es extra
        _log(f"no se pudo leer el tracking de clips ({e})")
    return logs


def _check_twitch(panel):
    """(token_ok, twitch_client): valida el secreto de Twitch.

    Con panel: credenciales de brain_credentials (client_id +
    client_secret). Sin panel: entorno. Devuelve (None, None) si
    no hay credenciales (no se puede validar, no es un fallo).
    """
    client_id = os.environ.get("TWITCH_CLIENT_ID", "")
    client_secret = os.environ.get("TWITCH_CLIENT_SECRET", "")
    if panel is not None:
        try:
            creds = panel.brain_credentials() or {}
            tw = creds.get("twitch") or {}
            client_id = tw.get("client_id") or client_id
            client_secret = tw.get("client_secret") or client_secret
        except Exception as e:  # noqa: BLE001 - sin credenciales
            _log(f"no se pudieron leer las credenciales ({e})")
    if not client_id or not client_secret:
        _log("sin credenciales de Twitch: no se valida el token")
        return None, None
    client = TwitchClient(client_id, client_secret)
    try:
        client.refresh_token()
        return True, client
    except TwitchError as e:
        _log(f"token de Twitch no válido: {e}")
        return False, client


def _build_simulate_clients(fail_all=False):
    """Fakes del escenario forzado (prueba, nunca toca nada real).

    Último clip hace 90 min, 2 vivos en GTA VI, última corrida
    fallida, token de Twitch vencido, y líneas de log con un 401
    de Twitch y un dispatch fallido. Con fail_all=True cada peldaño
    devuelve error (escenario "todo falla").
    """
    now_ms = int(time.time() * 1000)
    minute = 60000

    class FakePanel:
        def __init__(self):
            self.phases = []
            self.telegrams = []

        def list_worker_events(self):
            return [
                {"at": now_ms - 5 * minute, "kind": "github_dispatch",
                 "message": "Dispatch panel-live enviado a GitHub "
                            "(exe2027/viral-clip-agent)."},
                {"at": now_ms - 12 * minute, "kind": "aviso",
                 "message": "Twitch Helix devolvió HTTP 401: token "
                            "inválido o expirado."},
                {"at": now_ms - 20 * minute, "kind": "github_dispatch",
                 "message": "Dispatch panel-live falló: GitHub "
                            "devolvió HTTP 502."},
                {"at": now_ms - 40 * minute, "kind": "ciclo",
                 "message": "Ciclo automático: 5 jobs procesados, "
                            "0 atascados recuperados."},
            ]

        def worker_state(self):
            return {"running": True,
                    "lastClipAt": now_ms - 90 * minute,
                    "lastSweepAt": now_ms - 20 * minute}

        def list_clips(self):
            return [{"id": 154}]

        def list_clip_events(self, clip_id):
            return [{"at": now_ms - 90 * minute, "kind": "tracking",
                     "message": "clip 154 encolado"}]

        def report_phase(self, phase, detail="", run_id=""):
            self.phases.append((phase, detail))
            return {"ok": True}

        def restart_worker(self):
            if fail_all:
                raise RuntimeError("POST /api/worker/start -> "
                                   "HTTP 502 (simulado)")
            return {"ok": True, "running": True}

        def notify_telegram(self, message):
            if fail_all:
                raise RuntimeError(
                    "POST /api/bridge/notify -> HTTP 404: No "
                    "encontrado. (simulado: la ruta aún no existe)")
            self.telegrams.append(message)
            return {"ok": True}

    class FakeGitHub:
        def __init__(self):
            self.dispatched = []

        def list_runs(self, workflow, per_page=5):
            return [{"id": 44639214161, "status": "completed",
                     "conclusion": "failure",
                     "created_at": "2026-11-19T23:37:00Z"}]

        def has_run_in_flight(self, workflow):
            return False

        def dispatch_workflow(self, workflow, tenant, ref="main"):
            if fail_all:
                raise GitHubError("POST /dispatches -> HTTP 502 "
                                  "(simulado)")
            self.dispatched.append((workflow, tenant))
            return True

    class FakeTwitch:
        def refresh_token(self):
            if fail_all:
                raise TwitchError(
                    "refrescar token de Twitch -> HTTP 403: "
                    "sospecha de secreto de Twitch revocado "
                    "(simulado)")
            return "simulated-app-token-ok"

    calls = {"recheck": 0}

    def fake_vod(max_vods=2, max_clips=2, top_n=2, bridge=None):
        if fail_all:
            raise RuntimeError("modo VOD falló: sin VODs recientes "
                               "(simulado)")
        return 1, ("modo VOD: 1 VOD procesado, 1 clip en la Cola "
                   "del panel (sin publicar)")

    def fake_recheck():
        # Simula que el cerebro sigue sin suministrar clips tras
        # los tres primeros peldaños: así la prueba forzada recorre
        # la escalera COMPLETA hasta el VOD (que encola 1 clip y
        # repara). Llamadas: tras REDISPATCH, tras REFRESH y tras
        # RESTART (las 3 devuelven True = sigue atascado).
        calls["recheck"] += 1
        return calls["recheck"] < 4

    logs = FakePanel().list_worker_events()
    return {
        "panel": FakePanel(),
        "github": FakeGitHub(),
        "twitch": FakeTwitch(),
        "vod": fake_vod,
        "recheck": fake_recheck,
        "logs": logs,
        "state": {"running": True,
                  "lastClipAt": now_ms - 90 * minute, "now_ms": now_ms},
        "github_runs": FakeGitHub().list_runs("cerebro-live.yml"),
        "live_now": [{"name": "rubius", "platform": "twitch",
                      "language": "es"},
                     {"name": "xqc", "platform": "kick",
                      "language": "en"}],
        "token_ok": False,
        "calls": calls,
    }


def main(argv=None):
    p = argparse.ArgumentParser(
        description="Vigilante con auto-reparación: lee los logs del "
                    "panel, diagnostica el atasco y repara solo.")
    p.add_argument("--tenant", default="")
    p.add_argument("--dry-run", action="store_true",
                   help="Chequea y dice qué haría, sin actuar ni "
                        "avisar.")
    p.add_argument("--simulate-stuck", action="store_true",
                   help="Prueba forzada con fakes (nada real).")
    p.add_argument("--simulate-all-fail", action="store_true",
                   help="Prueba forzada donde cada peldaño falla.")
    p.add_argument("--no-panel", action="store_true",
                   help="Solo chequeos sin panel (scan en vivo + "
                        "corridas de GitHub + validación de Twitch).")
    p.add_argument("--config", default=None,
                   help="Ruta de config.yaml (default: la del repo).")
    p.add_argument("--out-dir", default=None,
                   help="Dónde guardar log + borrador de Telegram "
                        "en las pruebas forzadas.")
    args = p.parse_args(argv)

    try:
        from .config import load_config
        cfg = load_config(args.config) if args.config else load_config()
        wcfg = load_watchdog_config(cfg)
    except Exception as e:  # noqa: BLE001 - config rota = crash
        print(f"[watchdog] config inválida: {e}", file=sys.stderr)
        return 1

    tenant = (args.tenant or os.environ.get("PANEL_TENANT") or "")
    simulate = args.simulate_stuck or args.simulate_all_fail
    out_dir = args.out_dir
    if simulate and not out_dir:
        out_dir = os.path.join(os.getcwd(), "runs", "watchdog-sim")
        # La prueba forzada del 2026-11-19 vive en
        # ~/workspace/gta6-clips/runs/watchdog-sim/.
        home_sim = os.path.expanduser(
            "~/workspace/gta6-clips/runs/watchdog-sim")
        if os.path.isdir(os.path.dirname(home_sim)):
            out_dir = home_sim

    if simulate:
        # La prueba forzada también exige tenant explícito
        # (--tenant o PANEL_TENANT): nunca un tenant por defecto,
        # ni siquiera simulando (regla fail-loud).
        from .tenant import require_tenant

        try:
            tenant = require_tenant(tenant)
        except Exception as e:  # noqa: BLE001 - error claro, no traza
            print(f"[watchdog] {e}", file=sys.stderr)
            return 1
        sim = _build_simulate_clients(fail_all=args.simulate_all_fail)
        diag = diagnose(sim["logs"], sim["state"],
                        sim["github_runs"], wcfg,
                        live_now=sim["live_now"],
                        token_ok=sim["token_ok"])
        _log(f"diagnóstico (simulado): stuck={diag.stuck} "
             f"causa={diag.cause} acciones={diag.actions}")
        for ev in diag.evidence:
            _log(f"evidencia: {ev}")
        # La prueba forzada usa estado EFÍMERO (tempfile): cada
        # invocación ejecuta la escalera completa; el cooldown real
        # (runs/watchdog/state.json) no debe bloquear re-correr la
        # prueba, y su cobertura vive en test_watchdog.py.
        import tempfile
        _fd, state_path = tempfile.mkstemp(prefix="watchdog-sim-",
                                           suffix=".json")
        os.close(_fd)
        res = repair(diag, {k: sim[k] for k in (
            "panel", "github", "twitch", "vod", "recheck")},
            wcfg, dry_run=args.dry_run, tenant=tenant,
            state_path=state_path)
        _finish_simulate(res, diag, out_dir,
                         all_fail=args.simulate_all_fail,
                         tenant=tenant)
        return 0

    # --- Corrida real ---
    from .panel_bridge import PanelBridge
    panel = None
    if not args.no_panel:
        try:
            panel = PanelBridge.from_env()
            if panel is not None and tenant and panel.tenant is None:
                from .tenant import require_tenant
                panel.tenant = require_tenant(tenant)
        except Exception as e:  # noqa: BLE001 - sin panel se degrada
            _log(f"sin panel ({e}): se sigue en modo --no-panel")
            panel = None
    if panel is None:
        _log("modo sin panel: solo scan en vivo + corridas de "
             "GitHub + validación de Twitch")

    state = {}
    logs = []
    if panel is not None:
        try:
            state = panel.worker_state() or {}
        except Exception as e:  # noqa: BLE001 - sin estado no hay
            _log(f"no se pudo leer el estado del Worker ({e})")
        logs = _read_panel_logs(panel)
    else:
        state = {"running": False}

    live_now = []
    try:
        from .live_scan import scan_live
        scan = scan_live()
        live_now = scan.get("live") or []
        _log(f"en vivo en GTA VI: {len(live_now)} "
             f"{[x['name'] for x in live_now]}")
    except Exception as e:  # noqa: BLE001 - sin lectura no se decide
        _log(f"sin lectura de vivos ({e}): no se decide atasco")
        return 0

    token_ok, twitch = _check_twitch(panel)
    github = GitHubClient()
    try:
        runs = github.list_runs(wcfg["dispatch_workflow"])
    except GitHubError as e:
        _log(f"no se pudieron listar las corridas ({e})")
        runs = []
    diag = diagnose(logs, state, runs, wcfg, live_now=live_now,
                    token_ok=token_ok)
    _log(f"diagnóstico: stuck={diag.stuck} causa={diag.cause} "
         f"acciones={diag.actions}")
    for ev in diag.evidence:
        _log(f"evidencia: {ev}")
    for note in diag.notes:
        _log(f"nota: {note}")
    clients = {"panel": panel, "github": github, "twitch": twitch,
               "telegram": panel}
    res = repair(diag, clients, wcfg, dry_run=args.dry_run,
                 tenant=tenant)
    if res.telegram_message and not res.telegram_sent:
        _log("borrador de Telegram guardado solo en este log "
             "(ver arriba)")
    return 0


def _finish_simulate(result, diagnosis, out_dir, all_fail=False,
                     tenant=""):
    """Guarda log + borrador de Telegram de la prueba forzada."""
    if not out_dir:
        return
    os.makedirs(out_dir, exist_ok=True)
    suffix = "all-fail" if all_fail else "forced-test-2026-11-19"
    log_path = os.path.join(out_dir, f"{suffix}.log")
    draft_path = os.path.join(out_dir, "telegram-draft.txt"
                              if not all_fail else
                              "telegram-draft-all-fail.txt")
    with open(log_path, "w", encoding="utf-8") as f:
        f.write("Prueba forzada del vigilante — 2026-11-19\n")
        f.write(f"escenario: {'todo falla' if all_fail else 'atasco simulado'}\n")
        f.write(f"stuck={diagnosis.stuck} causa={diagnosis.cause} "
                f"acciones={diagnosis.actions}\n")
        f.write("evidencia (líneas citadas):\n")
        for ev in diagnosis.evidence:
            f.write(f"  • {ev}\n")
        f.write("pasos de la escalera:\n")
        for step in result.steps:
            mark = "OK" if step.get("ok") else "FALLO"
            f.write(f"  [{mark}] {step.get('step')}: "
                    f"{step.get('detail')}\n")
        f.write(f"reparado={result.repaired} "
                f"telegram_enviado={result.telegram_sent}\n")
        if result.telegram_note:
            f.write(f"nota Telegram: {result.telegram_note}\n")
    draft = result.telegram_message
    if not draft:
        draft = ("(sin borrador: el vigilante reparó el atasco y no "
                 "avisó por Telegram — este es el mensaje que SE "
                 "HABRÍA enviado si siguiera atascado:)\n\n"
                 + build_telegram_message(diagnosis, result.steps,
                                          tenant=tenant))
    with open(draft_path, "w", encoding="utf-8") as f:
        f.write(draft + "\n")
    _log(f"prueba forzada guardada en {log_path}")
    _log(f"borrador de Telegram guardado en {draft_path}")


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""Vigilante de tenant atascado: auto-reparación del cerebro.

2026-11-21, orden del usuario: cualquier error lo arregla el
cerebro automáticamente. El caso que no se arregló solo fue el de
kaez: su Worker corría (running=1, Iniciar pulsado dos veces) y
sus cuentas estaban conectadas, pero el panel mostraba ATASCADO
"Sin señal del cerebro todavía" y `last_live_dispatch_at` era
NULL: el cerebro nunca llegó a su tenant. La causa raíz era doble:
(a) el cerebro genérico (cerebro.yml / cerebro-live.yml) resolvía
siempre la URL del Worker de sebastian (PanelBridge solo lee
PANEL_BASE_URL y los workflows fijaban PANEL_URL, que el puente
ignora), así que el canje del brain_token de kaez daba 401
"Token del cerebro inválido" (corridas 44917889824, 44922296080,
44922316075); (b) nadie volvía a intentarlo: el cron del Worker
está apagado y el dispatch de Iniciar es de un solo intento.

Este módulo es la (b), y es INDEPENDIENTE de sebastian: vigila
SOLO el tenant que se le pasa (kaez) y dispara SOLO su workflow
propio (`cerebro-kaez.yml`, evento `panel-start-kaez`), sin tocar
los workflows compartidos ni la D1 de sebastian. La activación de
kaez NO espera a las 21:00 (esa restricción es solo por la cuota
agotada de la D1 de sebastian; la D1 de kaez está fresca): en
cuanto el secreto PANEL_PASSWORD del entorno `kaez` esté puesto,
el vigilante ya vigila y el cerebro de kaez ya arranca.

Condición de atascado (estricta, solo el caso "nunca arrancó"):
  - el Worker está en marcha (running=true), Y
  - `lastSweepAt` (last_live_dispatch_at) es NULL o más viejo que
    `stale_live_dispatch_min`, Y
  - no ha llegado NINGÚN clip ni NINGÚN job (Cola vacía).
Un tenant con clips/jobs aunque el cerebro lleve rato sin
despachar NO se toca aquí (puede estar ocioso sin directos; lo
cubre el vigilante de publicación, no este módulo).

Reparación: repository_dispatch `panel-start-kaez` a GitHub
(evento propio del workflow `cerebro-kaez.yml`, que usa auth por
sesión del entorno `kaez` y PANEL_BASE_URL del Worker de kaez).
No se fuerza nada: el cerebro corre su cadena normal y el Worker
publica respetando franjas, topes y calentamiento. El dispatch
NO pasa por el Worker (no necesita brain_token ni tocar la D1):
es un POST a la API de GitHub con GITHUB_TOKEN.

Guardas (las mismas que el resto del cerebro):
  - Cuota D1 7500: si la lectura del estado devuelve 7500, se
    aborta todo sin dispatch ni reintentos (el circuito de
    clipagent/retry.py ya se dispara en el puente; aquí se
    reporta como "quota" y se sale). El dispatch en sí no escribe
    en D1, así que no agrava la cuota.
  - Idempotencia: no se dispara dos veces en `cooldown_min`:
    si el workflow `cerebro-kaez.yml` ya tiene una corrida
    iniciada hace menos de `cooldown_min`, se salta con motivo
    "cooldown" (se consulta la API de GitHub, no el Worker).
  - Un solo tenant por corrida: el tenant es obligatorio y debe
    estar en tenants.yaml (require_tenant); el workflow solo
    pasa kaez y nunca toca sebastian.
  - $0: solo lee el estado del panel y, como mucho, un POST a la
    API de GitHub; sin ffmpeg, sin Whisper, sin descargas.

Uso (workflow .github/workflows/cerebro-tenant-watchdog.yml):
    python -m clipagent.tenant_watchdog --tenant kaez [--live]
Sin --live es dry-run: informa lo que haría sin disparar.
Sin sesión del panel (PANEL_USERNAME / PANEL_PASSWORD del
entorno `kaez`) no hay puente y sale con error claro (ese es
el único secreto pendiente).
"""
import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone

TENANT_WATCHDOG_REQUIRED = ("cooldown_min", "stale_live_dispatch_min")

# Evento propio del workflow de kaez (cerebro-kaez.yml). No es el
# `panel-start` genérico (que lo escucha cerebro.yml con la URL
# de sebastian): este es solo de kaez.
DISPATCH_EVENT_PREFIX = "panel-start-"

DEFAULT_REPO = "exe2027/viral-clip-agent"
GITHUB_API_BASE = "https://api.github.com"


def _log(msg):
    print(f"[tenant-watchdog] {msg}", flush=True)


def load_tenant_watchdog_config(cfg=None):
    """Config del vigilante de tenant desde config.yaml.

    Regla single-source del repo (igual que
    load_publish_watchdog_config): si falta la sección o un
    valor, falla fuerte con el nombre del valor (ConfigError),
    nunca con un default silencioso.
    """
    from .validate import ConfigError

    if cfg is None:
        from .config import load_config
        cfg = load_config()
    section = (cfg or {}).get("tenant_watchdog")
    if not isinstance(section, dict):
        raise ConfigError(
            "tenant_watchdog: falta la sección 'tenant_watchdog' "
            "en config.yaml (el vigilante de tenant no arranca "
            "sin sus umbrales; sin defaults silenciosos)")
    missing = [k for k in TENANT_WATCHDOG_REQUIRED if k not in section]
    if missing:
        raise ConfigError(
            "tenant_watchdog: faltan claves en config.yaml: "
            + ", ".join(f"tenant_watchdog.{k}" for k in missing)
            + " (sin defaults silenciosos)")
    out = {}
    for key in TENANT_WATCHDOG_REQUIRED:
        value = section[key]
        if not isinstance(value, int) or isinstance(value, bool):
            raise ConfigError(
                f"tenant_watchdog.{key}: debe ser entero, es "
                f"{value!r}")
        if value <= 0:
            raise ConfigError(
                f"tenant_watchdog.{key}: debe ser > 0, es {value!r}")
        out[key] = value
    return out


def is_tenant_stuck(state, n_clips, n_jobs, now_ms, stale_min):
    """¿Tenant atascado-desde-el-nacimiento? (puro, testeable).

    `state` es el dict de GET /api/worker/state (running,
    lastSweepAt). Devuelve (stuck, motivo). La condición es
    estricta: solo el caso "Worker en marcha, cerebro sin señal
    nunca y Cola vacía". Cualquier otro caso no es de este
    módulo (ver docstring).
    """
    if not isinstance(state, dict) or not state.get("running"):
        return False, "worker-parado"
    last = state.get("lastSweepAt")
    if last is not None:
        try:
            last_ms = int(last)
        except (TypeError, ValueError):
            return False, "lastSweepAt-ilegible"
        if now_ms - last_ms <= int(stale_min) * 60000:
            return False, "dispatch-reciente"
    if int(n_clips) != 0 or int(n_jobs) != 0:
        return False, "ya-hay-clips-o-jobs"
    if last is None:
        return True, "sin-dispatch-nunca"
    return True, "dispatch-obsoleto"


def dispatch_event_type(tenant):
    """Tipo de evento repository_dispatch propio del tenant."""
    return f"{DISPATCH_EVENT_PREFIX}{tenant}"


class GithubError(Exception):
    """Fallo de la API de GitHub (dispatch o consulta de runs)."""


def _github_request(method, path, token, body=None):
    """Un intento a la API de GitHub (urllib, sin dependencias)."""
    url = f"{GITHUB_API_BASE}{path}"
    headers = {
        "Accept": "application/vnd.github+json",
        "Authorization": f"Bearer {token}",
        "User-Agent": "tenant-watchdog",
    }
    data = json.dumps(body).encode("utf-8") if body is not None else None
    if data is not None:
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, headers=headers,
                                 method=method)
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            payload = resp.read()
            return resp.status, (json.loads(payload.decode("utf-8"))
                                 if payload else {})
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", "ignore")[:300]
        raise GithubError(
            f"{method} {path} -> HTTP {e.code}: {detail}")
    except urllib.error.URLError as e:
        raise GithubError(f"{method} {path} -> {e}")


def dispatch_brain(tenant, github_token, repo=None, http=None):
    """repository_dispatch `panel-start-<tenant>` a GitHub.

    Dispara el workflow propio del tenant (cerebro-kaez.yml).
    Devuelve True si GitHub lo aceptó (2xx). `http` es
    inyectable en tests: http(method, path, token, body)
    -> (status, parsed_json).
    """
    repo = repo or os.environ.get("GITHUB_REPOSITORY") or DEFAULT_REPO
    event_type = dispatch_event_type(tenant)
    body = {"event_type": event_type,
            "client_payload": {"tenant": tenant}}
    call = http or _github_request
    status, _ = call("POST", f"/repos/{repo}/dispatches",
                     github_token, body)
    if status // 100 != 2:
        raise GithubError(
            f"POST /repos/{repo}/dispatches -> HTTP {status}")
    return True


def recent_brain_run(tenant, github_token, repo=None, now_ms=None,
                     cooldown_min=60, http=None, workflow_file=None):
    """¿El workflow del tenant ya tiene una corrida reciente?

    Consulta las corridas de `cerebro-<tenant>.yml` en GitHub y
    devuelve True si la más reciente empezó hace menos de
    `cooldown_min` (está en curso o en cola: no se dispara otro).
    Es la idempotencia del auto-fix (el dispatch directo no deja
    marca en el Worker, así que la marca es la corrida misma).
    """
    repo = repo or os.environ.get("GITHUB_REPOSITORY") or DEFAULT_REPO
    workflow = workflow_file or f"cerebro-{tenant}.yml"
    now = int(now_ms if now_ms is not None else time.time() * 1000)
    call = http or _github_request
    _status, payload = call(
        "GET",
        f"/repos/{repo}/actions/workflows/{workflow}/runs"
        "?per_page=1",
        github_token)
    runs = (payload or {}).get("workflow_runs") or []
    if not runs:
        return False
    created = runs[0].get("created_at") or ""
    try:
        started = int(datetime.fromisoformat(
            created.replace("Z", "+00:00")).timestamp() * 1000)
    except ValueError:
        return False
    return now - started < int(cooldown_min) * 60000


def run(bridge=None, tenant=None, now_ms=None, live=False,
        wcfg=None, out_dir=None, github_token=None, repo=None,
        http=None):
    """Una pasada del vigilante de tenant. Devuelve el informe.

    `bridge` es un PanelBridge (del entorno si es None).
    `tenant` es obligatorio (require_tenant, lista blanca).
    `github_token` es el GITHUB_TOKEN de Actions (para el
    dispatch y la consulta de corridas); sin él no se puede
    disparar (informe con action="no-github-token").
    Sin --live (dry-run) nunca se dispara.
    Ante un 7500 se aborta sin dispatch (informe con
    action="quota"). Sin puente (sin credenciales) se devuelve
    action="no-bridge" (el llamador decide el exit code).
    """
    from .panel_bridge import PanelBridge, PanelError
    from .retry import (
        is_d1_quota_error,
        panel_quota_circuit_open,
        trip_panel_quota_circuit,
    )
    from .tenant import require_tenant

    now = int(now_ms if now_ms is not None else time.time() * 1000)
    tenant = require_tenant(tenant)
    if wcfg is None:
        wcfg = load_tenant_watchdog_config()
    report = {
        "tenant": tenant,
        "checked_at": datetime.fromtimestamp(
            now / 1000, timezone.utc).isoformat(),
        "live": bool(live),
        "stuck": False,
        "reason": "",
        "action": "none",
        "dispatched": False,
    }
    if panel_quota_circuit_open():
        report.update(reason="quota-circuito-abierto",
                      action="quota")
        _log(f"{tenant}: circuito 7500 abierto, sin comprobar")
        return report
    if bridge is None:
        bridge = PanelBridge.from_env()
    if bridge is None:
        report.update(reason="sin-sesion-del-panel",
                      action="no-bridge")
        _log(f"{tenant}: sin PANEL_USERNAME/PANEL_PASSWORD ni "
             "PANEL_BRAIN_TOKEN: no se puede leer el estado")
        return report
    try:
        state = bridge.worker_state() or {}
        clips = bridge.list_clips() or []
        jobs = bridge.list_jobs() or []
    except PanelError as e:
        if is_d1_quota_error(e):
            trip_panel_quota_circuit()
            report.update(reason="quota-7500", action="quota")
            _log(f"{tenant}: 7500 al leer el estado, sin dispatch")
            return report
        raise
    report["running"] = bool(state.get("running"))
    report["last_sweep_at"] = state.get("lastSweepAt")
    report["n_clips"] = len(clips)
    report["n_jobs"] = len(jobs)
    stuck, reason = is_tenant_stuck(
        state, len(clips), len(jobs), now,
        wcfg["stale_live_dispatch_min"])
    report["stuck"] = stuck
    report["reason"] = reason
    if not stuck:
        _log(f"{tenant}: no atascado ({reason}); nada que hacer")
        return report
    token = (github_token
             or os.environ.get("GITHUB_TOKEN") or "")
    if live and not token:
        report.update(reason="sin-github-token", action="no-github-token")
        _log(f"{tenant}: atascado ({reason}) pero sin GITHUB_TOKEN: "
             "no se puede disparar el cerebro")
        return report
    if live:
        try:
            if recent_brain_run(tenant, token, repo=repo, now_ms=now,
                                cooldown_min=wcfg["cooldown_min"],
                                http=http):
                report.update(reason="cooldown", action="cooldown")
                _log(f"{tenant}: atascado ({reason}) pero el workflow "
                     f"de {tenant} ya corrió hace "
                     f"<{wcfg['cooldown_min']} min; se espera")
                return report
        except GithubError as e:
            report.update(reason=f"github-error: {e}",
                          action="github-error")
            _log(f"{tenant}: no se pudo consultar las corridas: {e}")
            return report
    if not live:
        report.update(reason=reason, action="would-dispatch")
        _log(f"{tenant}: atascado ({reason}); dry-run: se dispararía "
             f"{dispatch_event_type(tenant)} a GitHub")
        return report
    try:
        dispatch_brain(tenant, token, repo=repo, http=http)
    except GithubError as e:
        report.update(reason=f"github-error: {e}",
                      action="github-error")
        _log(f"{tenant}: el dispatch a GitHub falló: {e}")
        return report
    report.update(action="dispatched", dispatched=True)
    _log(f"{tenant}: atascado ({reason}); "
         f"{dispatch_event_type(tenant)} disparado a GitHub")
    if out_dir:
        os.makedirs(out_dir, exist_ok=True)
        with open(os.path.join(out_dir, "report.json"), "w",
                  encoding="utf-8") as f:
            json.dump(report, f, ensure_ascii=False, indent=2)
    return report


def main(argv=None):
    """CLI: python -m clipagent.tenant_watchdog --tenant kaez [--live]."""
    parser = argparse.ArgumentParser(
        description="Vigilante de tenant atascado (auto-reparación).")
    parser.add_argument("--tenant", default="",
                        help="Tenant del panel (p. ej. kaez)")
    parser.add_argument("--live", action="store_true",
                        help="Actuar de verdad (si no, dry-run)")
    parser.add_argument("--out-dir", default="",
                        help="Carpeta donde escribir report.json")
    args = parser.parse_args(argv)
    try:
        wcfg = load_tenant_watchdog_config()
    except Exception as e:  # noqa: BLE001 - config rota: error claro
        print(f"[tenant-watchdog] ERROR: {e}", file=sys.stderr,
              flush=True)
        return 1
    try:
        report = run(tenant=args.tenant or None, live=args.live,
                     wcfg=wcfg,
                     out_dir=args.out_dir or None)
    except Exception as e:  # noqa: BLE001 - el informe ya dice qué pasó
        print(f"[tenant-watchdog] ERROR: {e}", file=sys.stderr,
              flush=True)
        return 1
    if report.get("action") == "no-bridge":
        print("[tenant-watchdog] ERROR: sin sesión del panel. "
              "Configura PANEL_USERNAME/PANEL_PASSWORD del tenant "
              "en el entorno `kaez` de GitHub (ver "
              "docs/tenant-watchdog.md).", file=sys.stderr, flush=True)
        return 1
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

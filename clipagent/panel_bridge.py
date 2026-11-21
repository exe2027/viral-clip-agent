#!/usr/bin/env python3
"""Puente cerebro -> panel web AVC (Cloudflare).

El cerebro (GitHub Actions) encola los clips del cutter en la
Cola del panel: POST /api/clips (origin='live'), PUT del MP4 a
R2 y pasos de tracking. Auth por sesión del panel (login por
usuario + Bearer), nunca por token en el repo.

Credenciales SOLO por entorno (nunca en el repo ni en archivos):
  PANEL_BASE_URL  (default https://avc.vikingosfoodspa.workers.dev)
  PANEL_BRAIN_TOKEN (token de un solo uso que el Worker mete en el
    client_payload del dispatch a GitHub; se canjea UNA vez en
    POST /api/brain/session por una sesión de 12 h. ES la vía
    del cerebro en GitHub Actions: sin secretos por tenant en
    GitHub y sin environments por tenant.)
  PANEL_USERNAME / PANEL_PASSWORD (fallback local/operador: login
    de servicio del tenant; en Actions NO se usa)
  PANEL_TENANT    (OBLIGATORIO cuando hay credenciales: sin valor
    por defecto, regla 2026-11-18. Debe estar en tenants.yaml.
    Con PANEL_BRAIN_TOKEN el tenant también puede venir de la
    sesión canjeada, que es la fuente de verdad.)
  PANEL_SESSION_TOKEN (sesión ya canjeada, p. ej. en tests)

Sin credenciales, from_env() devuelve None y el cutter sigue
solo con la cola del cerebro (el motivo queda registrado).
"""
import json
import os
import time
import urllib.error
import urllib.request

from .retry import (
    MAX_RETRIES_PER_DAY,
    RetryBudget,
    is_d1_quota_error,
    is_transient_panel_error,
    panel_quota_circuit_open,
    retry_delay_seconds,
    trip_panel_quota_circuit,
)

DEFAULT_BASE_URL = "https://avc.vikingosfoodspa.workers.dev"
USER_AGENT = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/120.0 Safari/537.36")

# Eventos de clip al panel (2026-11-19): el cerebro reporta cada
# resultado para que el usuario vea dónde se pierden los clips.
# Contrato CONGELADO: POST /api/worker/event, body {kind, message},
# message <= 500 caracteres, una línea en español.
EVENT_KINDS = ("clip_cortado", "clip_encolado", "clip_descartado",
               "corte_fallido")
MAX_EVENT_MESSAGE = 500


class PanelError(Exception):
    pass


class PanelBridge:
    """Cliente mínimo del panel web (urllib, sin dependencias)."""

    def __init__(self, base_url=None, username=None, password=None,
                 tenant=None, token=None, brain_token=None, *,
                 sleep_fn=None):
        self.base_url = (base_url or os.environ.get("PANEL_BASE_URL")
                         or DEFAULT_BASE_URL).rstrip("/")
        self.username = username or os.environ.get("PANEL_USERNAME")
        self.password = password or os.environ.get("PANEL_PASSWORD")
        # Espera entre reintentos (inyectable en tests; en
        # producción es time.sleep). Ver _request_retry.
        self._sleep = sleep_fn or time.sleep
        # Presupuesto de reintentos por operación y día (A4,
        # clipagent/retry.py): solo memoria del proceso.
        self._retry_budget = RetryBudget()
        # Token de un solo uso del dispatch (2026-11-19, cerebro
        # genérico): se canjea una vez por una sesión y se borra
        # de la memoria. Sin él, el cerebro en Actions no puede
        # autenticarse: no hay secretos por tenant en GitHub.
        self._brain_token = (brain_token
                             or os.environ.get("PANEL_BRAIN_TOKEN") or "")
        # Sin tenant por defecto: None hasta que se valide. Un tenant
        # explícito inválido falla ya aquí; uno ausente falla en
        # from_env()/_ensure_login(), antes de tocar la red.
        raw_tenant = (tenant or os.environ.get("PANEL_TENANT") or "").strip()
        if raw_tenant:
            from .tenant import require_tenant
            self.tenant = require_tenant(raw_tenant)
        else:
            self.tenant = None
        self._token = token or os.environ.get("PANEL_SESSION_TOKEN")

    @classmethod
    def from_env(cls):
        """Bridge si hay credenciales en el entorno, si no None.

        Con credenciales pero sin PANEL_TENANT válido, levanta
        TenantError (no se cae a otro tenant por defecto). Con
        PANEL_BRAIN_TOKEN el tenant puede venir de la sesión
        canjeada (ver _ensure_login).
        """
        bridge = cls()
        if bridge._token:
            return bridge.require_tenant_or_raise()
        if bridge._brain_token:
            return bridge
        if bridge.username and bridge.password:
            return bridge.require_tenant_or_raise()
        return None

    def require_tenant_or_raise(self):
        """Valida el tenant del bridge o levanta TenantError."""
        if self.tenant:
            return self
        from .tenant import require_tenant
        self.tenant = require_tenant()
        return self

    # -- HTTP ----------------------------------------------------------
    def _request(self, method, path, body=None, raw=None,
                 content_type="application/json"):
        """Un intento al panel, con el circuito 7500 (A4).

        Si el circuito está abierto (se vio un 7500 en este
        proceso), levanta sin tocar la red. Si ESTE intento
        devuelve un 7500, dispara el circuito antes de levantar:
        ningún bucle de este proceso vuelve a intentarlo hasta el
        reset de cuota (00:00 UTC = 21:00 -03)."""
        if panel_quota_circuit_open():
            raise PanelError(
                f"{method} {path} -> circuito 7500 abierto: cuota "
                "D1 agotada hasta las 21:00 (-03); sin reintentos")
        try:
            return self._request_once(
                method, path, body=body, raw=raw,
                content_type=content_type)
        except PanelError as e:
            if is_d1_quota_error(e):
                trip_panel_quota_circuit()
            raise

    def _request_retry(self, method, path, key=None, body=None,
                       raw=None, content_type="application/json"):
        """Como _request, con reintento acotado (A4).

        Solo fallos TRANSITORIOS (429, 5xx, red) se reintentan, con
        backoff 1/5/15 min y tope 3 reintentos por operación y día
        (RetryBudget, clave ``key`` o "MÉTODO ruta"). Un 400/401/
        403/404 levanta al primer intento. Un 7500 levanta al
        primer intento y abre el circuito (ver _request): nada de
        reintentos mientras dure. ``key`` distingue por clip en
        las rutas con id (p. ej. publish de cada clip tiene su
        propio presupuesto de 3/día).
        """
        op_key = key or f"{method} {path}"
        attempt = 0
        while True:
            try:
                return self._request(
                    method, path, body=body, raw=raw,
                    content_type=content_type)
            except PanelError as e:
                if is_d1_quota_error(e):
                    raise
                if not is_transient_panel_error(e):
                    raise
                attempt += 1
                if not self._retry_budget.can_retry(op_key):
                    raise PanelError(
                        f"{method} {path} -> sin más reintentos "
                        f"hoy (tope {MAX_RETRIES_PER_DAY}/día): {e}")
                self._retry_budget.record_retry(op_key)
                delay = retry_delay_seconds(attempt)
                if delay is None:
                    raise
                self._sleep(delay)

    def _request_once(self, method, path, body=None, raw=None,
                      content_type="application/json"):
        url = self.base_url + path
        headers = {"User-Agent": USER_AGENT}
        if self._token:
            headers["Authorization"] = f"Bearer {self._token}"
        data = None
        if raw is not None:
            data = raw
            headers["Content-Type"] = content_type
        elif body is not None:
            data = json.dumps(body).encode("utf-8")
            headers["Content-Type"] = "application/json"
        req = urllib.request.Request(url, data=data, headers=headers,
                                     method=method)
        try:
            with urllib.request.urlopen(req, timeout=60) as resp:
                payload = resp.read()
                if not payload:
                    return {}
                return json.loads(payload.decode("utf-8", "ignore"))
        except urllib.error.HTTPError as e:
            detail = e.read().decode("utf-8", "ignore")[:300]
            raise PanelError(f"{method} {path} -> HTTP {e.code}: "
                             f"{detail}")
        except urllib.error.URLError as e:
            raise PanelError(f"{method} {path} -> {e}")

    def login(self):
        """POST /api/auth/login: guarda el token de sesión."""
        if not self.username or not self.password:
            raise PanelError("sin PANEL_USERNAME/PANEL_PASSWORD")
        res = self._request("POST", "/api/auth/login", body={
            "username": self.username, "password": self.password})
        token = res.get("token")
        if not token:
            raise PanelError(f"login sin token: {str(res)[:200]}")
        self._token = token
        return res

    def _exchange_brain_token(self):
        """POST /api/brain/session: canjea el token de un solo uso
        por una sesión de 12 h (sub "cerebro"). El token se borra
        de la memoria tras el canje (vale una sola vez). El tenant
        de la sesión es la fuente de verdad: si el bridge no tiene
        tenant, se toma de aquí (validado contra tenants.yaml); si
        lo tiene y no coincide, se levanta (el dispatch era de
        otro tenant)."""
        if not self._brain_token:
            raise PanelError("sin PANEL_BRAIN_TOKEN")
        res = self._request("POST", "/api/brain/session", body={
            "token": self._brain_token})
        self._brain_token = ""
        token = res.get("token")
        if not token:
            raise PanelError(f"canje del token sin sesión: {str(res)[:200]}")
        self._token = token
        session_tenant = res.get("tenant") or ""
        if session_tenant:
            from .tenant import require_tenant
            session_tenant = require_tenant(session_tenant)
            if self.tenant and self.tenant != session_tenant:
                raise PanelError(
                    f"el token del cerebro es del tenant "
                    f"{session_tenant!r}, no de {self.tenant!r}")
            self.tenant = session_tenant
        return res

    def _ensure_login(self):
        # Con token de un solo uso, canjearlo ANTES de exigir el
        # tenant: la sesión trae el tenant (el workflow pone
        # PANEL_TENANT igual, pero la sesión es la fuente de verdad).
        if not self._token and self._brain_token:
            self._exchange_brain_token()
        self.require_tenant_or_raise()
        if not self._token:
            self.login()

    # -- Cola ------------------------------------------------------------
    def create_clip(self, payload):
        """POST /api/clips -> clip (dict con id).

        SIN reintento (A4): el POST no es idempotente — un
        reintento tras un fallo ambiguo crearía un clip duplicado
        en la Cola. Si falla, el llamador (live_cutter) lo registra
        y sigue con el siguiente clip."""
        self._ensure_login()
        res = self._request("POST", "/api/clips", body=payload)
        clip = res.get("clip") or {}
        if not clip.get("id"):
            raise PanelError(f"POST /api/clips sin id: {str(res)[:300]}")
        return clip

    def upload_video(self, clip_id, video_path):
        """PUT /api/clips/:id/video con el MP4 (máx. 128 MB).

        SIN reintento (A4): re-subir 128 MB tras cada fallo
        transitorio quemaría ancho de banda y tiempo de corrida; el
        llamador decide si el clip se reintenta entero."""
        self._ensure_login()
        with open(video_path, "rb") as f:
            data = f.read()
        res = self._request(
            "PUT", f"/api/clips/{clip_id}/video", raw=data,
            content_type="video/mp4")
        return res

    def tracking_step(self, clip_id, step, message, estado="completado"):
        """POST /api/tracking/step (pasos canónicos del diseño).

        Reintentable (A4): un paso es idempotente a efectos del
        panel (la dedupe de 6 h del Worker lo registra una vez) y
        perderlo es perder trazabilidad del clip."""
        self._ensure_login()
        return self._request_retry(
            "POST", "/api/tracking/step",
            key=f"POST /api/tracking/step/{clip_id}", body={
                "clip_id": int(clip_id), "step": step,
                "message": message, "estado": estado,
                "actor": "live-cutter"})

    def report_phase(self, phase, detail="", run_id=""):
        """POST /api/worker/phase: qué está haciendo el cerebro AHORA.

        La fase se ve en el panel (Worker) en tiempo real:
        buscando_picos / cortando_clip / modo_vod / sin_directos.
        Nunca levanta: si el panel no responde, solo queda en el
        log y se sigue (un reporte de fase no puede tumbar un
        corte ni una encolada)."""
        try:
            self._ensure_login()
            return self._request("POST", "/api/worker/phase", body={
                "phase": phase, "detail": detail, "run_id": run_id})
        except Exception as e:  # noqa: BLE001 - la fase es informativa
            print(f"[panel] fase {phase} no reportada ({e})", flush=True)
            return None

    def report_event(self, kind, message):
        """POST /api/worker/event: un resultado de clip (cortado,
        encolado, descartado o corte fallido) para el feed de
        actividad del cerebro en el panel.

        El mensaje es una línea en español (<=500 caracteres):
        streamer + título + resultado + números. Nunca levanta:
        si el panel no responde, solo queda en el log y se sigue
        (un evento no puede tumbar un corte ni una encolada).
        Devuelve la respuesta del panel o None."""
        try:
            if kind not in EVENT_KINDS:
                print(f"[panel] evento kind inválido: {kind!r}",
                      flush=True)
                return None
            text = " ".join(str(message).split())[:MAX_EVENT_MESSAGE]
            self._ensure_login()
            return self._request("POST", "/api/worker/event", body={
                "kind": kind, "message": text})
        except Exception as e:  # noqa: BLE001 - el evento es informativo
            print(f"[panel] evento {kind} no reportado ({e})", flush=True)
            return None

    def brain_credentials(self):
        """GET /api/bridge/credentials: credenciales descifradas
        DEL TENANT DE LA SESIÓN (2026-11-19, cerebro genérico).
        Devuelve {twitch, gemini, youtube:{es,en}, zernio:{es,en},
        tiktok_direct:{es,en}, meta, clip_public_base_url} (null
        lo que falte en el panel). Nunca se registra el contenido:
        el llamador (panel_credentials) enmascara cada valor."""
        self._ensure_login()
        return self._request_retry("GET", "/api/bridge/credentials")

    def list_influencers(self):
        """GET /api/influencers (los del tenant de la sesión)."""
        self._ensure_login()
        return self._request_retry(
            "GET", "/api/influencers").get("influencers") or []

    def list_api_accounts(self):
        """GET /api/accounts (cuentas API del tenant, con madurez)."""
        self._ensure_login()
        return self._request_retry(
            "GET", "/api/accounts").get("accounts") or []

    def list_clips(self):
        """GET /api/clips (Cola del tenant)."""
        self._ensure_login()
        return self._request_retry(
            "GET", "/api/clips").get("clips") or []

    def get_clip(self, clip_id):
        """GET /api/clips/:id."""
        self._ensure_login()
        return self._request_retry(
            "GET", f"/api/clips/{clip_id}",
            key=f"GET /api/clips/{clip_id}").get("clip") or {}

    def download_video(self, clip_id, out_path):
        """GET /api/clips/:id/video: baja el MP4 del clip a disco.

        La descarga es binaria (no JSON como ``_request``), así que
        va por su propio GET con el token de sesión. Lo usa el
        recuperador de fuente VOD (clipagent/recover_vod_source.py)
        para el match de audio contra el VOD."""
        import urllib.error
        import urllib.request

        self._ensure_login()
        url = self.base_url + f"/api/clips/{clip_id}/video"
        headers = {"User-Agent": USER_AGENT}
        if self._token:
            headers["Authorization"] = f"Bearer {self._token}"
        req = urllib.request.Request(url, headers=headers, method="GET")
        try:
            with urllib.request.urlopen(req, timeout=300) as resp:
                if resp.status != 200:
                    raise PanelError(
                        f"GET /api/clips/{clip_id}/video -> "
                        f"HTTP {resp.status}")
                with open(out_path, "wb") as f:
                    while True:
                        chunk = resp.read(1 << 20)
                        if not chunk:
                            break
                        f.write(chunk)
        except urllib.error.HTTPError as e:
            detail = e.read().decode("utf-8", "ignore")[:300]
            raise PanelError(
                f"GET /api/clips/{clip_id}/video -> HTTP {e.code}: {detail}")
        except urllib.error.URLError as e:
            raise PanelError(f"GET /api/clips/{clip_id}/video -> {e}")
        return out_path

    def update_clip(self, clip_id, fields):
        """PATCH /api/clips/:id: actualiza campos del clip
        (publish_blocked, subtitles_complete, ...). Lo usa el
        re-hecho a rubius (clipagent/remake_all.py) para
        desbloquear el clip cuando su vídeo corregido ya pasó
        la validación y se subió. Reintentable (A4): un PATCH con
        los mismos campos es idempotente."""
        self._ensure_login()
        return self._request_retry(
            "PATCH", f"/api/clips/{clip_id}",
            key=f"PATCH /api/clips/{clip_id}", body=fields)

    def preview_seen(self, clip_id):
        """POST /api/clips/:id/preview-seen: levanta el freno de
        publishGate ('falta ver el preview en el Editor').
        Reintentable (A4): marcar visto dos veces no cambia nada."""
        self._ensure_login()
        return self._request_retry(
            "POST", f"/api/clips/{clip_id}/preview-seen",
            key=f"POST /api/clips/{clip_id}/preview-seen")

    def publish_clip(self, clip_id):
        """POST /api/clips/:id/publish: encola jobs por plataforma
        (las que pasan publishGate). NO publica en el acto: el
        Worker (cron */5) ejecuta los jobs y cada 'posted' lleva su
        evidence_url. Devuelve {jobs, blocked}. Reintentable (A4):
        el Worker encola por idempotency_key, así que un segundo
        intento no duplica jobs."""
        self._ensure_login()
        return self._request_retry(
            "POST", f"/api/clips/{clip_id}/publish",
            key=f"POST /api/clips/{clip_id}/publish")

    def list_jobs(self, state=None, limit=50):
        """GET /api/jobs (vista del usuario)."""
        self._ensure_login()
        q = f"?limit={limit}" + (f"&state={state}" if state else "")
        return self._request_retry("GET", f"/api/jobs{q}").get("jobs") or []

    def worker_state(self):
        """GET /api/worker/state (flag running del tenant de la
        sesión; lo usa la puerta Iniciar del workflow diario)."""
        self._ensure_login()
        return self._request_retry("GET", "/api/worker/state")

    # -- Vigilante (2026-11-19, auto-reparación) -----------------------
    # Solo lectura de logs + reinicio + aviso por Telegram. El
    # registro de pasos del vigilante NO va por report_event: el
    # servidor valida kind contra (clip_cortado, clip_encolado,
    # clip_descartado, corte_fallido) y "watchdog" da 400
    # (worker-phase.ts:136,154); hasta que el panel lo acepte, los
    # pasos viven en el log de la corrida (ver watchdog.py).
    def list_worker_events(self):
        """Actividad reciente del Worker (worker_events, 50).

        GET /api/worker/state devuelve {running, startedAt,
        lastNote, activity, validation, phase, lastSweepAt,
        lastClipAt, nextRunAt}; activity es la lista {at, kind,
        message} (SELECT ... ORDER BY at DESC LIMIT 50). Es la
        primera fuente del vigilante: los logs mandan.
        """
        self._ensure_login()
        state = self._request_retry("GET", "/api/worker/state")
        return state.get("activity") or []

    def list_clip_events(self, clip_id):
        """Eventos de tracking de un clip (clip_events).

        GET /api/clips/:id/tracking -> {ok, events} (SELECT * FROM
        clip_events WHERE clip_id = ? AND tenant_id = ? ORDER BY
        at ASC).
        """
        self._ensure_login()
        return self._request_retry(
            "GET", f"/api/clips/{clip_id}/tracking",
            key=f"GET /api/clips/{clip_id}/tracking").get("events") or []

    def restart_worker(self):
        """POST /api/worker/start: el botón Iniciar del panel.

        Pone running=1 y dispara panel-start al cerebro (lo escucha
        cerebro.yml, no cerebro-live). Es el peldaño RESTART_WORKER
        de la escalera del vigilante. Reintentable (A4): poner
        running=1 es idempotente.
        """
        self._ensure_login()
        return self._request_retry("POST", "/api/worker/start")

    def run_once(self):
        """POST /api/worker/run-once: un ciclo del Worker (force).

        Corre el ciclo para el tenant de la sesión Y para kaez
        (el Worker viejo es el único que descifra sus
        credenciales) y devuelve {ok, ran, processed, stalled,
        autoEnqueued, gate, kaez: {...}}. Lo usa el vigilante de
        publicación para reintentar lo atascado tras cada franja.
        SIN reintento: el ciclo avanza jobs (no es idempotente
        re-dispararlo a ciegas tras un fallo ambiguo); si falla,
        el llamador lo registra y la próxima franja lo reintenta.
        """
        self._ensure_login()
        return self._request("POST", "/api/worker/run-once")

    def notify_telegram(self, message):
        """POST /api/bridge/notify {message}: aviso del vigilante.

        PENDIENTE del panel: la ruta no existe aún (la crea el otro
        agente); hasta entonces levanta PanelError (HTTP 404) con
        mensaje claro y el vigilante deja el Telegram como borrador
        en el log. Nunca se traga el error en silencio.
        """
        self._ensure_login()
        return self._request("POST", "/api/bridge/notify",
                             body={"message": message})

    def health(self):
        """GET /api/health (sin auth)."""
        return self._request_retry("GET", "/api/health")


def report_brain_phase(phase, detail="", bridge=None):
    """Reporta la fase del cerebro al panel (2026-11-19).

    Con el bridge dado (si tiene report_phase) o con uno nuevo del
    entorno (PANEL_USERNAME/PANEL_PASSWORD/PANEL_TENANT). Nunca
    levanta: sin credenciales, sin panel o con error, solo un aviso
    en el log y se sigue. El run_id sale de GITHUB_RUN_ID cuando
    corre en Actions. Devuelve True si el panel aceptó el reporte.
    """
    try:
        run_id = os.environ.get("GITHUB_RUN_ID", "")
        if bridge is not None and hasattr(bridge, "report_phase"):
            bridge.report_phase(phase, detail=detail, run_id=run_id)
            return True
        b = PanelBridge.from_env()
        if b is None:
            return False
        res = b.report_phase(phase, detail=detail, run_id=run_id)
        return res is not None
    except Exception as e:  # noqa: BLE001 - la fase es informativa
        print(f"[panel] fase {phase} no reportada ({e})", flush=True)
        return False


def report_brain_event(kind, message, bridge=None):
    """Reporta un evento de clip al panel (2026-11-19).

    Con el bridge dado (si tiene report_event) o con uno nuevo del
    entorno (PANEL_USERNAME/PANEL_PASSWORD/PANEL_TENANT). Nunca
    levanta: sin credenciales, sin panel o con error, solo un aviso
    en el log y se sigue. Devuelve True si el panel aceptó el
    evento.
    """
    try:
        if bridge is not None and hasattr(bridge, "report_event"):
            bridge.report_event(kind, message)
            return True
        b = PanelBridge.from_env()
        if b is None:
            return False
        res = b.report_event(kind, message)
        return res is not None
    except Exception as e:  # noqa: BLE001 - el evento es informativo
        print(f"[panel] evento {kind} no reportado ({e})", flush=True)
        return False

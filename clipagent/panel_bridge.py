#!/usr/bin/env python3
"""Puente cerebro -> panel web AVC (Cloudflare).

El cerebro (GitHub Actions) encola los clips del cutter en la
Cola del panel: POST /api/clips (origin='live'), PUT del MP4 a
R2 y pasos de tracking. Auth por sesión del panel (login por
usuario + Bearer), nunca por token en el repo.

Credenciales SOLO por entorno (secretos de GitHub Actions, nunca
en el repo ni en archivos):
  PANEL_BASE_URL  (default https://avc.vikingosfoodspa.workers.dev)
  PANEL_USERNAME  (p. ej. el login de servicio del tenant)
  PANEL_PASSWORD
  PANEL_TENANT    (default sebastian)

Sin credenciales, from_env() devuelve None y el cutter sigue
solo con la cola del cerebro (el motivo queda registrado).
"""
import json
import os
import urllib.error
import urllib.request

DEFAULT_BASE_URL = "https://avc.vikingosfoodspa.workers.dev"
USER_AGENT = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/120.0 Safari/537.36")


class PanelError(Exception):
    pass


class PanelBridge:
    """Cliente mínimo del panel web (urllib, sin dependencias)."""

    def __init__(self, base_url=None, username=None, password=None,
                 tenant=None, token=None):
        self.base_url = (base_url or os.environ.get("PANEL_BASE_URL")
                         or DEFAULT_BASE_URL).rstrip("/")
        self.username = username or os.environ.get("PANEL_USERNAME")
        self.password = password or os.environ.get("PANEL_PASSWORD")
        self.tenant = tenant or os.environ.get("PANEL_TENANT") \
            or "sebastian"
        self._token = token or os.environ.get("PANEL_SESSION_TOKEN")

    @classmethod
    def from_env(cls):
        """Bridge si hay credenciales en el entorno, si no None."""
        bridge = cls()
        if bridge._token:
            return bridge
        if bridge.username and bridge.password:
            return bridge
        return None

    # -- HTTP ----------------------------------------------------------
    def _request(self, method, path, body=None, raw=None,
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

    def _ensure_login(self):
        if not self._token:
            self.login()

    # -- Cola ------------------------------------------------------------
    def create_clip(self, payload):
        """POST /api/clips -> clip (dict con id)."""
        self._ensure_login()
        res = self._request("POST", "/api/clips", body=payload)
        clip = res.get("clip") or {}
        if not clip.get("id"):
            raise PanelError(f"POST /api/clips sin id: {str(res)[:300]}")
        return clip

    def upload_video(self, clip_id, video_path):
        """PUT /api/clips/:id/video con el MP4 (máx. 128 MB)."""
        self._ensure_login()
        with open(video_path, "rb") as f:
            data = f.read()
        res = self._request(
            "PUT", f"/api/clips/{clip_id}/video", raw=data,
            content_type="video/mp4")
        return res

    def tracking_step(self, clip_id, step, message, estado="completado"):
        """POST /api/tracking/step (pasos canónicos del diseño)."""
        self._ensure_login()
        return self._request("POST", "/api/tracking/step", body={
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

    def list_clips(self):
        """GET /api/clips (Cola del tenant)."""
        self._ensure_login()
        return self._request("GET", "/api/clips").get("clips") or []

    def get_clip(self, clip_id):
        """GET /api/clips/:id."""
        self._ensure_login()
        return self._request("GET", f"/api/clips/{clip_id}").get("clip") or {}

    def preview_seen(self, clip_id):
        """POST /api/clips/:id/preview-seen: levanta el freno de
        publishGate ('falta ver el preview en el Editor')."""
        self._ensure_login()
        return self._request("POST", f"/api/clips/{clip_id}/preview-seen")

    def publish_clip(self, clip_id):
        """POST /api/clips/:id/publish: encola jobs por plataforma
        (las que pasan publishGate). NO publica en el acto: el
        Worker (cron */5) ejecuta los jobs y cada 'posted' lleva su
        evidence_url. Devuelve {jobs, blocked}."""
        self._ensure_login()
        return self._request("POST", f"/api/clips/{clip_id}/publish")

    def list_jobs(self, state=None, limit=50):
        """GET /api/jobs (vista del usuario)."""
        self._ensure_login()
        q = f"?limit={limit}" + (f"&state={state}" if state else "")
        return self._request("GET", f"/api/jobs{q}").get("jobs") or []

    def health(self):
        """GET /api/health (sin auth)."""
        return self._request("GET", "/api/health")


def report_brain_phase(phase, detail="", bridge=None):
    """Reporta la fase del cerebro al panel (2026-11-19).

    Con el bridge dado (si tiene report_phase) o con uno nuevo del
    entorno (PANEL_USERNAME/PANEL_PASSWORD/PANEL_TENANT). Nunca
    levanta: sin credenciales, sin panel o con error, solo un aviso
    en el log y se sigue. El run_id sale de GITHUB_RUN_ID cuando
    corre en Actions. Devuelve True si el panel aceptó el reporte.
    """
    try:
        if bridge is not None and hasattr(bridge, "report_phase"):
            bridge.report_phase(phase, detail=detail)
            return True
        b = PanelBridge.from_env()
        if b is None:
            return False
        res = b.report_phase(phase, detail=detail,
                             run_id=os.environ.get("GITHUB_RUN_ID", ""))
        return res is not None
    except Exception as e:  # noqa: BLE001 - la fase es informativa
        print(f"[panel] fase {phase} no reportada ({e})", flush=True)
        return False

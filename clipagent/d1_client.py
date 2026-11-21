#!/usr/bin/env python3
"""Cliente mínimo de Cloudflare D1 por API HTTP (sin wrangler).

Lo usa el vigilante de publicación (clipagent/publish_watchdog.py)
para leer los jobs de hoy y crear republicaciones en la base VIEJA
(clips-avc), sin pasar por el panel: el panel no expone la creación
de jobs y su lectura es por tenant (una sesión de sebastian no ve
los jobs de kaez). La API es la misma que usa wrangler
(POST /client/v4/accounts/{account}/d1/database/{db}/query).

Credenciales SOLO por entorno, nunca en el repo ni en logs:
  CLOUDFLARE_API_TOKEN  (obligatorio para leer/escribir; sin él,
                         from_env() devuelve None y el vigilante
                         degrada a modo solo-panel)
  CLOUDFLARE_ACCOUNT_ID (default: la cuenta única del proyecto)
  D1_DATABASE_ID        (default: clips-avc, la base vieja)

Cuota D1: cada query de lectura no consume la cuota de escrituras;
cada INSERT/UPDATE sí. El cliente respeta el circuito 7500 de
clipagent/retry.py: ante un error de cuota levanta D1QuotaError,
dispara el circuito y no se reintenta nada hasta el reset
(00:00 UTC = 21:00 -03). El token NUNCA se registra ni se incluye
en los mensajes de error.
"""
import json
import os
import urllib.error
import urllib.request

from .retry import (
    is_d1_quota_error,
    panel_quota_circuit_open,
    trip_panel_quota_circuit,
)

D1_API_BASE = "https://api.cloudflare.com/client/v4"
# Cuenta única del proyecto (verificada 2026-11-20: el token ve
# exactamente una cuenta). No es un secreto: el token sí lo es.
DEFAULT_ACCOUNT_ID = "27afc37225a02fd92557f901c05dabee"
# Base VIEJA clips-avc (la que publica el Worker `avc`). Las bases
# nuevas (clips-avc-sebastian / clips-avc-kaez) NO publican hoy
# (sus Workers no descifran credenciales): el vigilante trabaja
# solo sobre la vieja, ambos tenants.
DEFAULT_DATABASE_ID = "5cf19675-f905-4e49-b8f2-96ae0810fafd"
DEFAULT_DATABASE_NAME = "clips-avc"


class D1Error(Exception):
    """Fallo de la API de D1 (no es cuota)."""


class D1QuotaError(D1Error):
    """Cuota diaria de escrituras D1 agotada (error 7500)."""


class D1Client:
    """Un cliente = una base D1. urllib, sin dependencias."""

    def __init__(self, token=None, account_id=None, database_id=None,
                 http_post=None):
        self.token = (token or os.environ.get("CLOUDFLARE_API_TOKEN")
                      or "")
        self.account_id = (account_id
                           or os.environ.get("CLOUDFLARE_ACCOUNT_ID")
                           or DEFAULT_ACCOUNT_ID)
        self.database_id = (database_id
                            or os.environ.get("D1_DATABASE_ID")
                            or DEFAULT_DATABASE_ID)
        # Inyección para tests: http_post(url, body, headers)
        # -> (status, parsed_json).
        self._http_post = http_post or self._real_post

    @classmethod
    def from_env(cls):
        """Cliente si hay token en el entorno, si no None.

        Sin token no hay D1: el vigilante degrada a modo
        solo-panel (lectura de sebastian por PanelBridge) y lo
        dice en el informe.
        """
        client = cls()
        if not client.token:
            return None
        return client

    # -- HTTP ----------------------------------------------------------
    def _real_post(self, url, body, headers):
        data = json.dumps(body).encode("utf-8")
        req = urllib.request.Request(url, data=data, headers=headers,
                                     method="POST")
        try:
            with urllib.request.urlopen(req, timeout=60) as resp:
                payload = resp.read()
                return resp.status, json.loads(
                    payload.decode("utf-8", "ignore") or "{}")
        except urllib.error.HTTPError as e:
            detail = e.read().decode("utf-8", "ignore")[:300]
            return e.code, {"success": False, "errors": [
                {"code": e.code, "message": detail}]}
        except urllib.error.URLError as e:
            raise D1Error(f"POST D1 -> {e}")

    def _call(self, sql, params=None):
        """Una llamada a la API. Devuelve el result[0] de D1.

        Levanta D1QuotaError (con el circuito disparado) ante un
        7500, y D1Error ante cualquier otro fallo. El token nunca
        aparece en los mensajes.
        """
        if panel_quota_circuit_open():
            raise D1QuotaError(
                "circuito 7500 abierto: cuota D1 agotada hasta las "
                "21:00 (-03); sin llamadas a D1")
        url = (f"{D1_API_BASE}/accounts/{self.account_id}/d1/"
               f"database/{self.database_id}/query")
        # El token viaja solo en el header Authorization; nada en
        # este módulo registra headers ni el token.
        headers = {"Authorization": f"Bearer {self.token}",
                   "Content-Type": "application/json"}
        status, payload = self._http_post(
            url, {"sql": sql, "params": list(params or [])}, headers)
        errors = (payload or {}).get("errors") or []
        err_text = " ".join(str(e.get("message") or e)
                            for e in errors if isinstance(e, dict))
        if not (payload or {}).get("success", False):
            if is_d1_quota_error(err_text) or status == 7500:
                trip_panel_quota_circuit()
                raise D1QuotaError(
                    f"D1 -> cuota agotada (7500): {err_text[:200]}")
            raise D1Error(
                f"D1 -> HTTP {status}: {err_text[:200] or payload}")
        results = (payload or {}).get("result") or []
        if not results:
            raise D1Error("D1 -> respuesta sin result")
        return results[0]

    # -- API -----------------------------------------------------------
    def query(self, sql, params=None):
        """SELECT: lista de filas (dicts). Solo lectura."""
        res = self._call(sql, params)
        return list(res.get("results") or [])

    def execute(self, sql, params=None):
        """INSERT/UPDATE/DELETE: filas cambiadas (meta.changes).

        Cada escritura consume cuota D1: solo la llama el
        vigilante en modo --live, y solo para republicaciones.
        """
        res = self._call(sql, params)
        return int((res.get("meta") or {}).get("changes") or 0)

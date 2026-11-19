#!/usr/bin/env python3
"""Tenant del panel: sin valor por defecto (2026-11-18).

Regla (MEJORAS-PENDIENTES.md 2.1): si falta ``PANEL_TENANT``, el
proceso debe abortar con error claro, sin tocar la red. Antes el
código caía a ``"sebastian"`` y un workflow de otra persona sin esa
variable leía y escribía la configuración de Sebastian.

El tenant válido se resuelve así:
  1. argumento explícito, o variable ``PANEL_TENANT``;
  2. debe estar en la lista blanca ``tenants.yaml`` (solo IDs, sin
     secretos; se puede apuntar a otro archivo con
     ``PANEL_TENANTS_FILE`` en tests).

``require_tenant`` solo se llama cuando el panel VA a usarse (hay
credenciales o URL del Worker configuradas). Sin panel configurado,
el cerebro sigue solo con ``config.yaml`` y no hace falta tenant.
"""
import os
from pathlib import Path

ENV_VAR = "PANEL_TENANT"
DEFAULT_TENANTS_FILE = Path(__file__).resolve().parents[1] / "tenants.yaml"


class TenantError(ValueError):
    """Falta PANEL_TENANT o no está en la lista blanca."""


def load_tenants(path=None):
    """Lista blanca de tenants desde tenants.yaml."""
    path = Path(path or os.environ.get("PANEL_TENANTS_FILE")
                or DEFAULT_TENANTS_FILE)
    if not path.exists():
        raise TenantError(
            f"Falta PANEL_TENANT y no existe la lista blanca: {path} "
            "(crea tenants.yaml con los IDs permitidos)")
    try:
        import yaml
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        tenants = data.get("tenants") or []
    except ImportError:
        tenants = []
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line.startswith("- "):
                tenants.append(line[2:].strip())
    tenants = [str(t).strip() for t in tenants if str(t).strip()]
    if not tenants:
        raise TenantError(f"Lista blanca vacía en {path}")
    return tenants


def require_tenant(tenant=None, path=None):
    """Devuelve el tenant validado o levanta TenantError.

    Sin tenant (ni argumento ni PANEL_TENANT) aborta: nunca se
    devuelve un tenant por defecto.
    """
    tenant = (tenant or os.environ.get(ENV_VAR) or "").strip()
    if not tenant:
        raise TenantError(
            "Falta PANEL_TENANT: define la variable con el tenant "
            "del entorno del workflow (p. ej. PANEL_TENANT=sebastian). "
            "Sin ella el proceso aborta para no tocar la "
            "configuración de otro tenant.")
    allowed = load_tenants(path)
    if tenant not in allowed:
        raise TenantError(
            f"PANEL_TENANT={tenant!r} no está en la lista blanca "
            f"{allowed} (tenants.yaml). Revisa la variable o añade "
            "el tenant a la lista.")
    return tenant

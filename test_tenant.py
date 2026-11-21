#!/usr/bin/env python3
"""Tenant sin valor por defecto (MEJORAS-PENDIENTES.md 2.1, 2026-11-18).

Si falta PANEL_TENANT, el proceso aborta con TenantError y sin
tocar la red: nunca cae a "sebastian" ni a ningún otro tenant.
"""
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from clipagent.panel_bridge import PanelBridge
from clipagent.panel_settings import fetch_panel_settings
from clipagent.tenant import TenantError, load_tenants, require_tenant


def _env_sin_tenant():
    env = dict(os.environ)
    env.pop("PANEL_TENANT", None)
    return env


class TestRequireTenant(unittest.TestCase):
    def test_sin_tenant_aborta(self):
        with mock.patch.dict(os.environ, _env_sin_tenant(), clear=True):
            with self.assertRaises(TenantError):
                require_tenant()

    def test_tenant_invalido_aborta(self):
        with mock.patch.dict(os.environ, _env_sin_tenant(), clear=True):
            with self.assertRaises(TenantError):
                require_tenant("otro")

    def test_tenant_valido_por_argumento_y_por_entorno(self):
        with mock.patch.dict(os.environ, _env_sin_tenant(), clear=True):
            self.assertEqual(require_tenant("sebastian"), "sebastian")
            self.assertEqual(require_tenant("kaez"), "kaez")
        env = _env_sin_tenant()
        env["PANEL_TENANT"] = "kaez"
        with mock.patch.dict(os.environ, env, clear=True):
            self.assertEqual(require_tenant(), "kaez")

    def test_lista_blanca_son_solo_ids(self):
        self.assertEqual(load_tenants(), ["sebastian", "kaez"])


class TestFetchPanelSettingsSinTenant(unittest.TestCase):
    def test_sin_panel_configurado_no_hace_falta_tenant(self):
        env = _env_sin_tenant()
        env.pop("PANEL_WORKER_URL", None)
        env.pop("PANEL_BRIDGE_TOKEN", None)
        with mock.patch.dict(os.environ, env, clear=True):
            self.assertIsNone(fetch_panel_settings())

    def test_panel_configurado_sin_tenant_aborta_sin_red(self):
        env = _env_sin_tenant()
        env["PANEL_WORKER_URL"] = "https://example.invalid"
        env["PANEL_BRIDGE_TOKEN"] = "tok"
        with mock.patch.dict(os.environ, env, clear=True):
            with mock.patch(
                    "urllib.request.urlopen",
                    side_effect=AssertionError("no debe tocar la red")):
                with self.assertRaises(TenantError):
                    fetch_panel_settings()


class TestPanelBridgeSinTenant(unittest.TestCase):
    def test_sin_credenciales_devuelve_none_sin_tenant(self):
        env = _env_sin_tenant()
        env.pop("PANEL_USERNAME", None)
        env.pop("PANEL_PASSWORD", None)
        env.pop("PANEL_SESSION_TOKEN", None)
        with mock.patch.dict(os.environ, env, clear=True):
            self.assertIsNone(PanelBridge.from_env())

    def test_con_credenciales_sin_tenant_aborta(self):
        env = _env_sin_tenant()
        env["PANEL_USERNAME"] = "seba"
        env["PANEL_PASSWORD"] = "x"
        env.pop("PANEL_SESSION_TOKEN", None)
        with mock.patch.dict(os.environ, env, clear=True):
            with self.assertRaises(TenantError):
                PanelBridge.from_env()

    def test_con_credenciales_y_tenant_funciona(self):
        env = _env_sin_tenant()
        env["PANEL_USERNAME"] = "seba"
        env["PANEL_PASSWORD"] = "x"
        env["PANEL_TENANT"] = "sebastian"
        env.pop("PANEL_SESSION_TOKEN", None)
        with mock.patch.dict(os.environ, env, clear=True):
            bridge = PanelBridge.from_env()
            self.assertIsNotNone(bridge)
            self.assertEqual(bridge.tenant, "sebastian")


if __name__ == "__main__":
    unittest.main(verbosity=2)

#!/usr/bin/env python3
"""Pruebas de 3.4g: interfaz LLMClient con proveedor intercambiable
y fallback al scoring actual cuando no hay clave.
"""
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from clipagent import llm


class TestLLMClient(unittest.TestCase):
    def test_null_devuelve_vacio(self):
        self.assertEqual(llm.NullLLMClient().generate("hola"), "")

    def test_gemini_sin_clave_devuelve_vacio(self):
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("GEMINI_API_KEY", None)
            self.assertEqual(llm.GeminiLLMClient().generate("hola"), "")

    def test_gemini_con_respuesta_falsa(self):
        def fake(method, url, headers, body):
            return 200, {"candidates": [{"content": {"parts": [
                {"text": "[{\"start\": 10}]"}]}}]}
        client = llm.GeminiLLMClient(api_key="k", request_fn=fake)
        self.assertEqual(client.generate("elige"), "[{\"start\": 10}]")

    def test_gemini_error_http_devuelve_vacio(self):
        def fake(method, url, headers, body):
            return 500, {"error": {"message": "caído"}}
        client = llm.GeminiLLMClient(api_key="k", request_fn=fake)
        self.assertEqual(client.generate("elige"), "")

    def test_get_llm_client_segun_env(self):
        self.assertIsInstance(llm.get_llm_client("none"),
                              llm.NullLLMClient)
        with mock.patch.dict(os.environ, {"GEMINI_API_KEY": "k"}):
            self.assertIsInstance(llm.get_llm_client("gemini"),
                                  llm.GeminiLLMClient)
        with mock.patch.dict(os.environ, {}, clear=True):
            os.environ.pop("GEMINI_API_KEY", None)
            self.assertIsInstance(llm.get_llm_client(),
                                  llm.NullLLMClient)


if __name__ == "__main__":
    unittest.main(verbosity=2)

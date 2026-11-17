"""Cliente LLM intercambiable (Fase 3.4g).

Interfaz mínima `LLMClient`: un método generate(prompt) -> str.
El proveedor se elige con la variable LLM_PROVIDER (gemini | none).
Sin clave, o si el proveedor falla, el llamador usa el fallback
al scoring actual: el LLM es una señal más, nunca un requisito.

Hoy solo hay proveedor Gemini (REST, sin dependencia nueva:
urllib). Añadir otro proveedor es implementar generate().
"""
import json
import os
import urllib.error
import urllib.request

from . import credentials as _creds

DEFAULT_MODEL = "gemini-3.5-flash-lite"


class LLMClient:
    """Interfaz: cualquier proveedor implementa generate()."""

    name = "base"

    def generate(self, prompt: str) -> str:
        raise NotImplementedError


class NullLLMClient(LLMClient):
    """Sin LLM: devuelve vacío y el llamador usa el fallback."""

    name = "none"

    def generate(self, prompt: str) -> str:
        return ""


class GeminiLLMClient(LLMClient):
    """Gemini por REST (generateContent), sin SDK.

    Key en GEMINI_API_KEY (entorno o .env). Modelo por
    LLM_MODEL (por defecto gemini-3.5-flash-lite).
    """

    name = "gemini"

    def __init__(self, api_key=None, model=None, request_fn=None):
        self.api_key = api_key or _creds.get_secret("GEMINI_API_KEY")
        self.model = model or os.environ.get("LLM_MODEL") or DEFAULT_MODEL
        self.request_fn = request_fn

    def available(self):
        return bool(self.api_key)

    def generate(self, prompt: str) -> str:
        if not self.api_key:
            return ""
        url = ("https://generativelanguage.googleapis.com/v1beta/models/"
               f"{self.model}:generateContent?key={self.api_key}")
        body = {"contents": [{"parts": [{"text": prompt}]}]}
        if self.request_fn is not None:
            status, payload = self.request_fn("POST", url, {}, body)
        else:
            data = json.dumps(body).encode("utf-8")
            req = urllib.request.Request(
                url, data=data, method="POST",
                headers={"Content-Type": "application/json"})
            try:
                with urllib.request.urlopen(req, timeout=60) as resp:
                    status, payload = resp.status, json.load(resp)
            except urllib.error.HTTPError:
                return ""
            except Exception:
                return ""
        if status != 200:
            return ""
        try:
            return payload["candidates"][0]["content"]["parts"][0]["text"]
        except (KeyError, IndexError, TypeError):
            return ""


def get_llm_client(provider=None, request_fn=None):
    """El cliente según LLM_PROVIDER (gemini | none).

    Por defecto: gemini si hay key, si no NullLLMClient (fallback).
    """
    provider = (provider or os.environ.get("LLM_PROVIDER") or "").lower()
    if provider in ("", "auto"):
        provider = "gemini" if _creds.get_secret("GEMINI_API_KEY") else "none"
    if provider == "gemini":
        return GeminiLLMClient(request_fn=request_fn)
    return NullLLMClient()

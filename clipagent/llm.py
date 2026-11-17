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

    def available(self):
        """¿Se puede usar (hay clave/configuración)? Sin LLM: False."""
        return False

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


def make_seo_variant(client, lang="es"):
    """make_variant(pkg, worst_block) para seo_score.retry_best (6B.4).

    Pide al LLM una variante SOLO del bloque más débil (título,
    descripción, hashtags o texto de miniatura) y devuelve el pkg
    con ese bloque cambiado. Sin LLM disponible, o si el LLM no
    responde nada útil, devuelve None (el reintento se para y el
    clip queda retenido, nunca se publica con SEO < 90).
    """
    def _clean_title(text):
        line = (text or "").strip().splitlines()
        title = line[0].strip().strip("\"'") if line else ""
        return title[:60] if title else ""

    def _clean_hashtags(text):
        tags = [t for t in (text or "").replace(",", " ").split()
                if t.startswith("#")]
        if not tags:
            tags = ["#" + t.strip("#.,") for t in
                    (text or "").split() if t.strip("#.,")]
        return tags[:8]

    def variant(pkg, worst):
        if client is None or not client.available():
            return None
        title = pkg.get("title") or ""
        desc = pkg.get("description") or ""
        if worst == "title" or worst == "coherence":
            prompt = ("Escribe UN título nuevo en español para un Short "
                      "sobre GTA 6. Debe incluir 'GTA 6' al inicio, tener "
                      f"entre 40 y 55 caracteres, y describir este momento: "
                      f"{desc[:200]}. Título actual: {title}. Devuelve SOLO "
                      "el título, sin comillas ni explicación.")
            new = _clean_title(client.generate(prompt))
            if not new or new == title:
                return None
            return dict(pkg, title=new)
        if worst == "description":
            prompt = ("Escribe una descripción nueva en español (2-3 "
                      "frases) para un Short sobre GTA 6 con este título: "
                      f"{title}. Debe mencionar GTA 6. Devuelve SOLO la "
                      "descripción, sin comillas ni explicación.")
            new = (client.generate(prompt) or "").strip()
            if not new or new == desc:
                return None
            return dict(pkg, description=new[:1000])
        if worst == "tags":
            prompt = ("Devuelve 5 hashtags en español para un Short "
                      f"sobre GTA 6 con este título: {title}. Solo los "
                      "hashtags separados por espacios, nada más.")
            new = _clean_hashtags(client.generate(prompt))
            if not new or new == list(pkg.get("hashtags") or []):
                return None
            return dict(pkg, hashtags=new, tags=new)
        if worst == "thumbnail":
            prompt = ("Escribe un texto corto (máx. 4 palabras) para la "
                      f"miniatura de un Short sobre GTA 6 con este título: "
                      f"{title}. Devuelve SOLO el texto.")
            new = _clean_title(client.generate(prompt))
            if not new:
                return None
            return dict(pkg, thumbnail_text=new)
        return None

    return variant


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

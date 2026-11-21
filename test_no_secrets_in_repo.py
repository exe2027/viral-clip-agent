#!/usr/bin/env python3
"""Escaneo de secretos en el repo (MEJORAS-PENDIENTES.md 1.1).

Ningún token, clave, contraseña ni `client_secrets.json` en el
repo, en logs, en `manifest.json`, en `.md` ni en capturas. Este
test escanea todos los archivos versionables del repo con patrones
de secretos conocidos y falla si encuentra algo. Corre en CI
(pytest ya es paso con bloqueo) y complementa a gitleaks, que
escanea además el historial de git.

Exclusiones deliberadas (no son contenido del repo):
  .git, .venv, __pycache__, runs/, drafts/, *.egg-info: estado
  local, dependencias o intermedios (runs/ y drafts/ además están
  en .gitignore). Los secretos viven solo en GitHub Environments
  y en el D1 cifrado del panel.
"""
import os
import re
import sys
import unittest
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

REPO_ROOT = Path(__file__).resolve().parent

EXCLUDED_DIRS = {
    ".git", ".venv", "__pycache__", "runs", "drafts",
    ".pytest_cache", "node_modules", "build", "dist",
}
EXCLUDED_SUFFIXES = {".egg-info", ".pyc", ".mp4", ".png", ".jpg",
                     ".jpeg", ".db", ".sqlite3"}
MAX_FILE_BYTES = 1_000_000

PATTERNS = {
    "clave Google (AIza...)": re.compile(r"AIza[0-9A-Za-z\-_]{35}"),
    "clave AWS (AKIA...)": re.compile(r"AKIA[0-9A-Z]{16}"),
    "token GitHub (ghp_/...)": re.compile(
        r"(ghp_|gho_|ghu_|ghs_|ghr_|github_pat_)[0-9A-Za-z_]{20,}"),
    "clave OpenAI (sk-...)": re.compile(
        r"sk-(proj-[0-9A-Za-z\-_]{20,}|[0-9A-Za-z]{20,})"),
    "clave privada PEM": re.compile(
        r"-----BEGIN (RSA |EC |OPENSSH |PGP )?PRIVATE KEY-----"),
    # Asignación con valor real (no vacía, no placeholder): ej.
    # password="...", client_secret: "...", api_key = "...".
    # Los placeholders ("", "x", "tu-clave", "fake-...") no casan
    # por longitud o por estar en la lista de abajo.
    "secreto asignado con valor": re.compile(
        r"(?i)(password|passwd|client_secret|api[_-]?key|"
        r"secret[_-]?key|access[_-]?token)\s*[:=]\s*"
        r"['\"][A-Za-z0-9\-_/+=]{8,}['\"]"),
}

# Valores que parecen secretos pero son placeholders de tests/docs.
PLACEHOLDERS = {"fake-key", "tu-clave", "tu-token", "clave123",
                "desde_env_file", "DESDE_CONFIG"}


def _escanear():
    hallazgos = []
    for path in sorted(REPO_ROOT.rglob("*")):
        if not path.is_file():
            continue
        if any(part in EXCLUDED_DIRS or part.endswith(".egg-info")
               for part in path.parts[len(REPO_ROOT.parts):]):
            continue
        if path.suffix in EXCLUDED_SUFFIXES:
            continue
        if path.stat().st_size > MAX_FILE_BYTES:
            continue
        try:
            data = path.read_bytes()
        except OSError:
            continue
        if b"\x00" in data[:4096]:
            continue  # binario
        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError:
            continue
        for lineno, line in enumerate(text.splitlines(), 1):
            for name, pattern in PATTERNS.items():
                for m in pattern.finditer(line):
                    if m.group(0) in PLACEHOLDERS:
                        continue
                    hallazgos.append(
                        f"{path.relative_to(REPO_ROOT)}:{lineno}: "
                        f"{name}: {m.group(0)[:12]}...")
    return hallazgos


class TestSinSecretosEnRepo(unittest.TestCase):
    def test_ningun_patron_de_secreto_en_el_repo(self):
        hallazgos = _escanear()
        self.assertEqual(hallazgos, [],
                         "Secretos en el repo:\n" + "\n".join(hallazgos))

    def test_client_secrets_no_versionado(self):
        import subprocess
        r = subprocess.run(
            ["git", "ls-files", "client_secrets.json", "token.json",
             ".env"],
            cwd=REPO_ROOT, capture_output=True, text=True)
        self.assertEqual(r.stdout.strip(), "",
                         "Archivo de secretos versionado: "
                         + r.stdout.strip())


if __name__ == "__main__":
    unittest.main(verbosity=2)

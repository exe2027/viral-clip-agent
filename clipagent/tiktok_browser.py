"""Agente navegador TikTok (Playwright Python, runner autoalojado).

⚠️ PIEZA DE LA ALTERNATIVA (GitHub Actions / runner autoalojado), NO
del plan principal. La vía principal es solo Cloudflare $0 + APIs
oficiales (docs/plan-cloudflare-solo.md), donde este agente NO puede
vivir (Workers no mantiene navegador ni sesión persistente). Si el
usuario cambia de idea sobre GitHub Actions o acepta un runner
autoalojado, esta pieza está lista; si no, TikTok va por API oficial
o Zernio.

ELECCIÓN DE LIBRERÍA Y LENGUAJE: Python + Playwright (no Node +
Puppeteer, aunque JavaScript también vale para esta pieza). Por qué:
el cerebro ya es Python (503 tests, mismo CLI `clipagent`, mismo
estado en disco); Playwright Python es oficial y tiene la misma API
que la versión Node; Puppeteer obligaría a un segundo runtime, otro
paquete de dependencias y otro CLI solo para subir a TikTok. Una sola
pieza en Python, instalada con `pip install -e .[browser]`.

Reglas duras (plan §5, independencia-cloudflare-plan.md):
  * 1 clip/día por cuenta nueva, sin excepciones.
  * Sin Appium/emulador, sin multicuentas, sin rotar IP/dispositivo,
    sin cuenta en privado, sin la @bitsflux vieja.
  * CAPTCHA: al detectarlo el agente se DETIENE de inmediato, marca el
    clip "waiting_captcha", guarda captura y avisa. El humano lo
    resuelve EN ESA MISMA SESIÓN (navegador visible) y el agente
    retoma verificando que sigan el mismo MP4, título y descripción.
    NUNCA hay auto-solver: no existe ese código en este módulo.
    AVISO VISIBLE EN EL PANEL: cada resultado terminal escribe
    `state/panel_status.json` (status, clip, cuenta, fecha, motivo,
    captura, aviso, hora). El panel DEBE leer ese archivo y mostrar
    un aviso cuando status == "waiting_captcha" — es una pausa
    avisada, no un paro silencioso. Lo mismo vale para "plan_b",
    "failed_retryable" y "needs_verification".
  * Ante rechazo: pausa 2 h sin reintentos, sin cambiar nada; UN
    reintento con la misma sesión; si vuelve a fallar, Plan B ese día
    (paquete manual copiable) y no se reintenta hasta el día siguiente.
  * Anti-duplicado: clave sha256(clip|tiktok|cuenta|fecha) guardada
    ANTES de subir; estados scheduled -> processing -> posted/failed.
    `posted` solo con evidencia (toast + URL + captura + verificación
    en el perfil). El MP4 NUNCA se borra desde aquí.

Sesión: Playwright `launch_persistent_context` con
`user_data_dir` local (por defecto ~/.cache/clipagent/tiktok-profile).
El login lo hace el humano una vez con `login` (navegador visible);
este módulo jamás pide ni guarda contraseñas.

Uso (runner autoalojado, tras instalar Playwright, ver README abajo):
    python -m clipagent.tiktok_browser login
    python -m clipagent.tiktok_browser upload --clip clip.mp4 \\
        --title "Título" --description "Descripción" \\
        --hashtags gta6,gta6clips --account @tu-cuenta
    python -m clipagent.tiktok_browser verify --clip clip.mp4
    python -m clipagent.tiktok_browser status

O vía el CLI único:
    python -m clipagent tiktok-browser upload --clip clip.mp4 ...

Selectores de TikTok: la web cambia sin avisar. Todos viven en
SELECTORS (arriba del todo) para actualizarlos en un solo lugar; si
un selector deja de existir el agente falla seguro (failed, no
publica a ciegas) en vez de inventar un clic.
"""
import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

try:
    from zoneinfo import ZoneInfo
    CHILE_TZ = ZoneInfo("America/Santiago")
except Exception:  # pragma: no cover
    CHILE_TZ = timezone(timedelta(hours=-3))

# ---------------------------------------------------------------------------
# Selectores (tiktok.com/upload). Actualizar aquí si TikTok cambia el DOM.
# ---------------------------------------------------------------------------
UPLOAD_URL = "https://www.tiktok.com/upload"
SELECTORS = {
    # Input de archivo (oculto) de la página de subida.
    "file_input": "input[type='file']",
    # Caja de texto del título/descripción (contenteditable).
    "caption": "div[contenteditable='true']",
    # Botón de publicar (UI en inglés o español).
    "post_button_en": "text=Post",
    "post_button_es": "text=Publicar",
    # Indicadores de preview: el vídeo ya cargó y se puede publicar.
    "preview_video": "video",
}
# Textos que delatan un captcha / verificación humana en la página.
CAPTCHA_TEXT = re.compile(
    r"captcha|verify (you are|you're) human|verifica que eres humano"
    r"|security check|comprobaci.n de seguridad|slide to verify"
    r"|desliza para verificar",
    re.IGNORECASE,
)
CAPTCHA_SELECTORS = (
    "[data-testid*='captcha']",
    ".captcha",
    "iframe[title*='captcha' i]",
    "iframe[src*='captcha' i]",
)
# Textos de éxito tras publicar.
POSTED_TEXT = re.compile(
    r"your video (has been uploaded|is live|was posted)"
    r"|video (uploaded|published|publicado)"
    r"|tu video (se subi.|se public.|est. en vivo)",
    re.IGNORECASE,
)
# Textos de rechazo / error de subida.
REJECTED_TEXT = re.compile(
    r"(couldn.t|could not|failed to) upload"
    r"|upload failed|video (rejected|removed)"
    r"|no se pudo subir|error (al|de) subir|video rechazado",
    re.IGNORECASE,
)

MAX_POSTS_PER_DAY = 1          # cuenta nueva: 1/día, sin excepciones
RETRY_PAUSE = timedelta(hours=2)  # pausa tras rechazo, sin reintentos
MAX_ATTEMPTS = 2               # intento inicial + 1 reintento, misma sesión
def default_account():
    """Cuenta de TikTok por defecto: la del tenant (del panel).

    Sin panel (ni PANEL_TENANT ni bridge), None: entonces
    --account es obligatorio. Nunca la cuenta de otro tenant
    (2026-11-19, cerebro genérico: no hay cuenta por defecto).
    """
    try:
        from .accounts import panel_labels
        label = panel_labels().get("tiktok")
        if label:
            return label
    except Exception:
        pass
    return None


DEFAULT_PROFILE_DIR = os.path.expanduser(
    "~/.cache/clipagent/tiktok-profile")
DEFAULT_STATE_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "state")
STATE_FILENAME = "tiktok_browser.json"


class CaptchaNeeded(Exception):
    """Captcha detectado: parar, avisar, que lo resuelva el humano."""


class TikTokBrowserError(Exception):
    """Fallo del agente (rechazo, selector roto, sin evidencia...)."""


@dataclass
class TikTokClip:
    """Lo que se sube: MP4 9:16 + textos, ya decididos por el panel."""
    mp4_path: str
    title: str
    description: str = ""
    hashtags: list = field(default_factory=list)
    account: str = ""  # vacío = se resuelve del panel (--account si no)
    date: str = ""  # YYYY-MM-DD America/Santiago; hoy si vacío

    def __post_init__(self):
        if not self.date:
            self.date = datetime.now(CHILE_TZ).strftime("%Y-%m-%d")
        if not self.account:
            resolved = default_account()
            if not resolved:
                raise TikTokBrowserError(
                    "--account es obligatorio: pasa la cuenta de "
                    "TikTok o define PANEL_TENANT con el panel "
                    "conectado (la cuenta se resuelve de SU panel).")
            self.account = resolved
        if " " not in self.account and not self.account.startswith("@"):
            self.account = "@" + self.account

    @property
    def caption(self):
        """Título + descripción + hashtags, como va en TikTok."""
        parts = [self.title]
        if self.description:
            parts.append(self.description)
        tags = " ".join(
            "#" + t.lstrip("#") for t in self.hashtags if t.strip())
        if tags:
            parts.append(tags)
        return "\n\n".join(parts)

    @property
    def clip_id(self):
        return os.path.splitext(os.path.basename(self.mp4_path))[0]


def idempotency_key(clip_id, account, date):
    """sha256(clip|tiktok|cuenta|fecha). Determinista y única por día."""
    raw = f"{clip_id}|tiktok|{account}|{date}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# Estado local (JSON en disco, nunca se sube a Git: *.json está ignorado).
# Estados: scheduled -> processing -> posted / failed (+ waiting_captcha,
# needs_verification y plan_b como estados no terminales / terminales
# de gestión humana; ver docstring del módulo).
# ---------------------------------------------------------------------------
class StateStore:
    def __init__(self, state_dir=DEFAULT_STATE_DIR):
        self.state_dir = state_dir
        self.path = os.path.join(state_dir, STATE_FILENAME)
        os.makedirs(state_dir, exist_ok=True)
        self._data = self._load()

    def _load(self):
        if not os.path.isfile(self.path):
            return {"records": {}}
        try:
            with open(self.path, encoding="utf-8") as f:
                data = json.load(f)
            return data if isinstance(data, dict) else {"records": {}}
        except (OSError, ValueError):
            return {"records": {}}

    def save(self):
        tmp = self.path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(self._data, f, indent=2, ensure_ascii=False)
        os.replace(tmp, self.path)

    def get(self, key):
        return self._data.setdefault("records", {}).get(key)

    def put(self, key, record):
        self._data.setdefault("records", {})[key] = record
        self.save()

    def posted_today(self, account, date):
        """¿Ya hay un posted hoy para esta cuenta? (tope 1/día)."""
        for rec in self._data.get("records", {}).values():
            if (rec.get("account") == account
                    and rec.get("date") == date
                    and rec.get("state") == "posted"):
                return True
        return False


def check_vertical(mp4_path):
    """True si el MP4 es vertical 9:16, False si no, None si no se
    puede medir (ffprobe ausente). Nunca bloquea por falta de ffprobe.
    """
    try:
        out = subprocess.run(
            ["ffprobe", "-v", "error", "-select_streams", "v:0",
             "-show_entries", "stream=width,height", "-of", "csv=p=0",
             mp4_path],
            capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.SubprocessError):
        return None
    try:
        width, height = (int(x) for x in out.stdout.strip().split(","))
    except ValueError:
        return None
    return height > width


def detect_captcha(page):
    """True si la página muestra un captcha / verificación humana."""
    try:
        html = page.content() or ""
    except Exception:
        html = ""
    if CAPTCHA_TEXT.search(html):
        return True
    for sel in CAPTCHA_SELECTORS:
        try:
            if page.locator(sel).count() > 0:
                return True
        except Exception:
            continue
    return False


def _locator(page, selector):
    return page.locator(selector)


def _click_first_visible(page, selectors):
    """Clica el primer selector con al menos un elemento. False si
    ninguno existe (el llamador decide: fallo seguro, no clic ciego).
    """
    for sel in selectors:
        try:
            loc = _locator(page, sel)
            if loc.count() > 0:
                loc.first.click()
                return True
        except Exception:
            continue
    return False


class TikTokBrowserAgent:
    """Orquesta una subida. Recibe la `page` (Playwright real o fake
    en tests) y no toca red ni credenciales por su cuenta.
    """

    def __init__(self, store, screenshots_dir=None):
        self.store = store
        self.screenshots_dir = screenshots_dir or os.path.join(
            store.state_dir, "screenshots")
        os.makedirs(self.screenshots_dir, exist_ok=True)

    # -- utilidades ------------------------------------------------------
    def _shot(self, page, key, name):
        path = os.path.join(self.screenshots_dir, f"{key[:12]}_{name}.png")
        try:
            page.screenshot(path=path)
        except Exception:
            return None
        return path

    def _write_notice(self, key, filename, text):
        path = os.path.join(self.store.state_dir, filename)
        with open(path, "w", encoding="utf-8") as f:
            f.write(text)
        return path

    def _write_panel_status(self, clip, key, status, reason="",
                            extra=None):
        """Aviso para el panel: el panel lee este archivo y muestra el
        estado de TikTok (en especial waiting_captcha). Una pausa
        avisada, nunca un paro silencioso.
        """
        payload = {
            "platform": "tiktok",
            "status": status,
            "key": key,
            "clip": clip.clip_id,
            "mp4": clip.mp4_path,
            "account": clip.account,
            "date": clip.date,
            "reason": reason,
            "updated_at": datetime.now(CHILE_TZ).isoformat(),
        }
        if extra:
            payload.update(extra)
        path = os.path.join(self.store.state_dir, "panel_status.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2, ensure_ascii=False)
        return path

    # -- flujo principal -------------------------------------------------
    def run(self, clip, page, resume=False):
        """Sube `clip` con la `page` ya abierta (sesión persistente).

        Devuelve dict con status: posted | waiting_captcha |
        failed_retryable | plan_b | skipped_duplicate |
        skipped_daily_limit | needs_verification | skipped_resume_mismatch.
        Nunca borra el MP4. Nunca reintenta dentro de la llamada.
        """
        if not os.path.isfile(clip.mp4_path):
            raise TikTokBrowserError(f"MP4 no existe: {clip.mp4_path}")
        if not clip.mp4_path.lower().endswith(".mp4"):
            raise TikTokBrowserError("El clip debe ser un MP4.")
        vertical = check_vertical(clip.mp4_path)
        if vertical is False:
            raise TikTokBrowserError(
                "El MP4 no es vertical 9:16; no se sube.")

        key = idempotency_key(clip.clip_id, clip.account, clip.date)
        rec = self.store.get(key) or {}
        now = datetime.now(CHILE_TZ)

        # Tope diario: 1 publicación/día por cuenta.
        if (self.store.posted_today(clip.account, clip.date)
                and not (rec.get("state") == "posted")):
            return {"status": "skipped_daily_limit", "key": key,
                    "reason": "Ya hay 1 clip publicado hoy en "
                    f"{clip.account} (tope cuenta nueva)."}

        if rec:
            state = rec.get("state")
            if state == "posted":
                return {"status": "skipped_duplicate", "key": key,
                        "reason": "Clave ya en posted: no se re-sube.",
                        "url": rec.get("evidence", {}).get("url")}
            if state == "processing":
                return {"status": "skipped_duplicate", "key": key,
                        "reason": "Clave ya en processing: hay una "
                        "subida en curso; no duplicar."}
            if state == "waiting_captcha" and not resume:
                return {"status": "waiting_captcha", "key": key,
                        "reason": "Captcha pendiente: el humano debe "
                        "resolverlo y retomar con --resume."}
            if state == "waiting_captcha" and resume:
                # Retomar SOLO si es el mismo MP4/título/descripción.
                if (rec.get("mp4_path") != os.path.abspath(clip.mp4_path)
                        or rec.get("title") != clip.title
                        or rec.get("caption") != clip.caption):
                    return {"status": "skipped_resume_mismatch",
                            "key": key,
                            "reason": "Al retomar tras el captcha el "
                            "MP4/título/descripción deben ser los "
                            "mismos; no coinciden con lo guardado."}
            if state == "failed":
                attempts = rec.get("attempts", 0)
                if attempts >= MAX_ATTEMPTS:
                    return self._plan_b(clip, key, rec,
                                        "2 intentos fallidos.")
                retry_after = rec.get("retry_after")
                if retry_after:
                    retry_dt = datetime.fromisoformat(retry_after)
                    if now < retry_dt:
                        return {"status": "failed_retryable",
                                "key": key,
                                "reason": "En pausa 2 h tras el "
                                "rechazo; reintento a partir de "
                                f"{retry_after}."}
            if state == "plan_b":
                return {"status": "plan_b", "key": key,
                        "reason": "Plan B activo para hoy: subir a "
                        "mano; no se reintenta hasta mañana."}

        # Clave guardada ANTES de subir (anti-duplicado).
        record = {
            "key": key, "clip_id": clip.clip_id,
            "mp4_path": os.path.abspath(clip.mp4_path),
            "title": clip.title, "caption": clip.caption,
            "account": clip.account, "date": clip.date,
            "state": "scheduled",
            "attempts": rec.get("attempts", 0),
            "created_at": now.isoformat(),
        }
        self.store.put(key, record)
        record["state"] = "processing"
        self.store.put(key, record)

        try:
            return self._upload(page, clip, key, record)
        except CaptchaNeeded:
            record["state"] = "waiting_captcha"
            record["captcha_at"] = datetime.now(CHILE_TZ).isoformat()
            self.store.put(key, record)
            self._write_panel_status(
                clip, key, "waiting_captcha",
                "Captcha en TikTok: resuélvelo en esta misma sesión "
                "y retoma con --resume.",
                {"screenshot": record.get("captcha_screenshot"),
                 "notice": record.get("captcha_notice")})
            return {"status": "waiting_captcha", "key": key,
                    "reason": "Captcha detectado: agente detenido. "
                    "El humano lo resuelve en esta misma sesión y se "
                    "retoma con --resume.",
                    "notice": record.get("captcha_notice")}
        except TikTokBrowserError as exc:
            return self._on_rejection(clip, key, record, str(exc))

    # -- subida ----------------------------------------------------------
    def _upload(self, page, clip, key, record):
        page.goto(UPLOAD_URL)
        page.wait_for_timeout(3000)
        if detect_captcha(page):
            shot = self._shot(page, key, "captcha")
            notice = self._write_notice(
                key, f"tiktok_captcha_{key[:12]}.md",
                "# Captcha en TikTok\n\n"
                f"Clip: {clip.mp4_path}\nCuenta: {clip.account}\n"
                f"Fecha: {clip.date}\nCaptura: {shot}\n\n"
                "1. En ESTA MISMA ventana del navegador, resuelve el "
                "captcha a mano.\n"
                "2. NO cierres el navegador ni cambies de cuenta/IP.\n"
                "3. Retoma con:\n"
                f"   python -m clipagent tiktok-browser upload --clip "
                f"{clip.mp4_path} --title {clip.title!r} --resume\n")
            record["captcha_notice"] = notice
            record["captcha_screenshot"] = shot
            self.store.put(key, record)
            raise CaptchaNeeded("captcha en la página de subida")

        try:
            page.set_input_files(SELECTORS["file_input"], clip.mp4_path)
        except Exception as exc:
            raise TikTokBrowserError(f"No se pudo subir el archivo: {exc}")
        page.wait_for_timeout(5000)
        if detect_captcha(page):
            raise CaptchaNeeded("captcha tras elegir el archivo")
        if REJECTED_TEXT.search(page.content() or ""):
            raise TikTokBrowserError("TikTok rechazó la subida del MP4.")

        # Título/descripción/hashtags.
        try:
            caption_loc = _locator(page, SELECTORS["caption"]).first
            caption_loc.click()
            caption_loc.fill(clip.caption)
        except Exception as exc:
            raise TikTokBrowserError(
                f"No se pudo rellenar el título/descripción: {exc}")
        page.wait_for_timeout(1000)
        try:
            filled = _locator(page, SELECTORS["caption"]).first \
                .text_content() or ""
        except Exception:
            filled = ""
        if clip.title not in filled:
            raise TikTokBrowserError(
                "La descripción no quedó con el título esperado; "
                "no se publica sin verificar el texto.")

        # Preview: el vídeo cargó y el botón de publicar existe.
        preview = self._shot(page, key, "preview")
        if not _click_first_visible(
                page, [SELECTORS["post_button_en"],
                       SELECTORS["post_button_es"]]):
            raise TikTokBrowserError(
                "No aparece el botón Publicar/Post: la subida no "
                "llegó a preview; no se publica a ciegas.")
        page.wait_for_timeout(5000)

        evidence = self._collect_evidence(page, clip, key, preview)
        if evidence["toast"] and evidence["url"] \
                and evidence["profile_verified"]:
            record["state"] = "posted"
            record["evidence"] = evidence
            record["posted_at"] = datetime.now(CHILE_TZ).isoformat()
            self.store.put(key, record)
            self._write_panel_status(clip, key, "posted",
                                     "Publicado en TikTok.",
                                     {"url": evidence["url"]})
            return {"status": "posted", "key": key, "evidence": evidence}
        if evidence["toast"] and evidence["url"]:
            # Subido según TikTok pero sin verificar en el perfil:
            # NO se marca posted; lo confirma el humano con `verify`.
            record["state"] = "needs_verification"
            record["evidence"] = evidence
            self.store.put(key, record)
            self._write_panel_status(
                clip, key, "needs_verification",
                "Toast + URL presentes, pero el vídeo no se confirmó "
                "en el perfil. Revisar el perfil y correr "
                "`tiktok-browser verify`.",
                {"url": evidence["url"]})
            return {"status": "needs_verification", "key": key,
                    "evidence": evidence,
                    "reason": "Toast + URL presentes, pero la "
                    "verificación en el perfil no confirmó el vídeo. "
                    "Revisar el perfil y correr "
                    "`tiktok-browser verify`."}
        raise TikTokBrowserError(
            "Sin evidencia de publicación (falta toast o URL).")

    def _collect_evidence(self, page, clip, key, preview_shot):
        html = page.content() or ""
        url = getattr(page, "url", "") or ""
        shot = self._shot(page, key, "posted") or preview_shot
        profile_verified = self._verify_on_profile(page, clip)
        return {
            "toast": bool(POSTED_TEXT.search(html)),
            "url": url if "/video/" in url else "",
            "screenshot": shot,
            "profile_verified": profile_verified,
        }

    def _verify_on_profile(self, page, clip):
        """¿El vídeo aparece en el perfil de la cuenta?"""
        handle = clip.account.lstrip("@")
        try:
            page.goto(f"https://www.tiktok.com/@{handle}")
            page.wait_for_timeout(3000)
            html = page.content() or ""
        except Exception:
            return False
        return "/video/" in html or clip.title in html

    # -- rechazo / reintento / Plan B -------------------------------------
    def _on_rejection(self, clip, key, record, reason):
        attempts = record.get("attempts", 0) + 1
        record["attempts"] = attempts
        record["last_error"] = reason
        if attempts >= MAX_ATTEMPTS:
            return self._plan_b(clip, key, record, reason)
        record["state"] = "failed"
        record["retry_after"] = (
            datetime.now(CHILE_TZ) + RETRY_PAUSE).isoformat()
        self.store.put(key, record)
        self._write_panel_status(
            clip, key, "failed_retryable", reason,
            {"retry_after": record["retry_after"],
             "attempts": attempts})
        return {"status": "failed_retryable", "key": key,
                "reason": reason,
                "retry_after": record["retry_after"],
                "note": "Pausa 2 h sin reintentos, sin cambiar "
                "IP/dispositivo/cuenta. Un reintento con la misma "
                "sesión."}

    def _plan_b(self, clip, key, record, reason):
        record["state"] = "plan_b"
        self.store.put(key, record)
        notice = self._write_notice(
            key, f"tiktok_planb_{key[:12]}.md",
            "# Plan B — subir a mano hoy\n\n"
            f"Motivo: {reason}\nClip: {clip.mp4_path}\n"
            f"Cuenta: {clip.account} — Fecha: {clip.date}\n\n"
            "El agente falló 2 veces. NO reintentar hasta mañana.\n\n"
            "## Para subir a mano (tiktok.com/upload o TikTok Studio)\n\n"
            f"Título: {clip.title}\n\nDescripción:\n{clip.description}\n\n"
            f"Hashtags: {' '.join('#' + h.lstrip('#') for h in clip.hashtags)}\n")
        self._write_panel_status(clip, key, "plan_b", reason,
                                 {"manual_package": notice})
        return {"status": "plan_b", "key": key, "reason": reason,
                "manual_package": notice}

    # -- verificación manual posterior ------------------------------------
    def verify_posted(self, clip, page):
        """Tras `needs_verification`: confirma en el perfil y marca
        posted solo con evidencia. Devuelve dict con status."""
        key = idempotency_key(clip.clip_id, clip.account, clip.date)
        rec = self.store.get(key)
        if not rec:
            return {"status": "not_found", "key": key,
                    "reason": "No hay registro de subida para ese clip."}
        if rec.get("state") == "posted":
            return {"status": "posted", "key": key,
                    "reason": "Ya estaba en posted."}
        if not self._verify_on_profile(page, clip):
            return {"status": "not_verified", "key": key,
                    "reason": "El vídeo no aparece en el perfil todavía."}
        shot = self._shot(page, key, "verify")
        evidence = dict(rec.get("evidence") or {})
        evidence["profile_verified"] = True
        evidence["screenshot"] = shot or evidence.get("screenshot")
        rec["state"] = "posted"
        rec["evidence"] = evidence
        rec["posted_at"] = datetime.now(CHILE_TZ).isoformat()
        self.store.put(key, rec)
        return {"status": "posted", "key": key, "evidence": evidence}


# ---------------------------------------------------------------------------
# Navegador real (Playwright, import perezoso: los tests usan fakes y no
# necesitan Playwright instalado).
# ---------------------------------------------------------------------------
def open_browser(profile_dir=DEFAULT_PROFILE_DIR, headed=True):
    """Contexto persistente Playwright (sesión guardada en disco local).
    Devuelve (playwright, context, page). El llamador cierra.
    """
    from playwright.sync_api import sync_playwright
    pw = sync_playwright().start()
    context = pw.chromium.launch_persistent_context(
        user_data_dir=profile_dir,
        headless=not headed,
        viewport={"width": 1280, "height": 800},
    )
    return pw, context, context.new_page()


def login_flow(profile_dir=DEFAULT_PROFILE_DIR):
    """Abre TikTok con el navegador visible para que el humano inicie
    sesión a mano UNA vez. No pide ni guarda contraseñas."""
    pw, context, page = open_browser(profile_dir, headed=True)
    try:
        page.goto("https://www.tiktok.com/")
        print("Inicia sesión en TikTok en ESTA ventana (a mano).")
        print("Cuando veas tu perfil con sesión iniciada, vuelve "
              "aquí y pulsa Enter.")
        input("Pulsa Enter cuando hayas iniciado sesión... ")
        page.goto("https://www.tiktok.com/upload")
        print("Sesión guardada en:", profile_dir)
        print("Captcha en esta pantalla = resuélvelo a mano y cierra.")
        return 0
    finally:
        context.close()
        pw.stop()


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def build_parser():
    p = argparse.ArgumentParser(
        prog="clipagent tiktok-browser",
        description="Agente navegador TikTok (1 clip/día, sesión "
        "persistente, captcha atendido por humano).")
    sub = p.add_subparsers(dest="command", required=True)

    p_login = sub.add_parser("login", help="Iniciar sesión a mano "
                             "una vez (guarda la sesión en disco).")
    p_login.add_argument("--profile-dir", default=DEFAULT_PROFILE_DIR)

    p_up = sub.add_parser("upload", help="Subir y publicar 1 clip.")
    p_up.add_argument("--clip", required=True, help="MP4 9:16")
    p_up.add_argument("--title", required=True)
    p_up.add_argument("--description", default="")
    p_up.add_argument("--hashtags", default="",
                      help="separados por coma, sin #")
    p_up.add_argument("--account", default=None, help="Cuenta de TikTok (si no, se resuelve del panel)")
    p_up.add_argument("--date", default="",
                      help="YYYY-MM-DD America/Santiago (hoy si vacío)")
    p_up.add_argument("--profile-dir", default=DEFAULT_PROFILE_DIR)
    p_up.add_argument("--state-dir", default=DEFAULT_STATE_DIR)
    p_up.add_argument("--headless", action="store_true",
                      help="NO recomendado: el captcha necesita "
                      "navegador visible")
    p_up.add_argument("--resume", action="store_true",
                      help="Retomar tras resolver un captcha")
    p_up.add_argument("--dry-run", action="store_true",
                      help="Valida y guarda scheduled, sin navegador")

    p_ver = sub.add_parser("verify", help="Confirmar en el perfil un "
                           "clip en needs_verification.")
    p_ver.add_argument("--clip", required=True)
    p_ver.add_argument("--account", default=None, help="Cuenta de TikTok (si no, se resuelve del panel)")
    p_ver.add_argument("--date", default="")
    p_ver.add_argument("--profile-dir", default=DEFAULT_PROFILE_DIR)
    p_ver.add_argument("--state-dir", default=DEFAULT_STATE_DIR)

    p_st = sub.add_parser("status", help="Estado de hoy para la cuenta.")
    p_st.add_argument("--account", default=None, help="Cuenta de TikTok (si no, se resuelve del panel)")
    p_st.add_argument("--date", default="")
    p_st.add_argument("--state-dir", default=DEFAULT_STATE_DIR)
    return p


def _make_clip(args):
    hashtags = [h.strip() for h in (args.hashtags or "").split(",")
                if h.strip()]
    return TikTokClip(mp4_path=args.clip, title=args.title,
                      description=args.description, hashtags=hashtags,
                      account=args.account, date=args.date or "")


def main(argv=None):
    args = build_parser().parse_args(argv)
    if args.command == "login":
        return login_flow(args.profile_dir)

    store = StateStore(args.state_dir)
    agent = TikTokBrowserAgent(store)
    today = args.date or datetime.now(CHILE_TZ).strftime("%Y-%m-%d")

    # Sin --account, la cuenta se resuelve del panel (PANEL_TENANT);
    # sin panel queda None y el clip la exige con error claro.
    if args.command in ("upload", "verify", "status") and not args.account:
        args.account = default_account()

    if args.command == "status":
        print(json.dumps(
            {k: v for k, v in store._data.get("records", {}).items()
             if v.get("account") == args.account
             and v.get("date") == today},
            indent=2, ensure_ascii=False))
        return 0

    clip = _make_clip(args)
    if args.command == "verify":
        pw, context, page = open_browser(args.profile_dir, headed=True)
        try:
            result = agent.verify_posted(clip, page)
        finally:
            context.close()
            pw.stop()
        print(json.dumps(result, indent=2, ensure_ascii=False))
        return 0 if result["status"] == "posted" else 1

    # upload
    if args.dry_run:
        key = idempotency_key(clip.clip_id, clip.account, clip.date)
        if not os.path.isfile(clip.mp4_path):
            print(f"MP4 no existe: {clip.mp4_path}")
            return 1
        print(f"dry-run OK: clave {key[:16]}… estado scheduled "
              "(sin navegador, sin subir nada).")
        return 0
    if args.headless:
        print("AVISO: --headless impide resolver un captcha a mano; "
              "si aparece, el agente se detendrá igual.")
    pw, context, page = open_browser(args.profile_dir,
                                     headed=not args.headless)
    try:
        result = agent.run(clip, page, resume=args.resume)
    finally:
        context.close()
        pw.stop()
    print(json.dumps(result, indent=2, ensure_ascii=False))
    status = result.get("status")
    if status == "posted":
        return 0
    if status == "waiting_captcha":
        return 3  # el humano resuelve y retoma con --resume
    return 1


if __name__ == "__main__":
    sys.exit(main())

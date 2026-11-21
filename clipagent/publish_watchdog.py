#!/usr/bin/env python3
"""Vigilante de publicación (2026-11-21, orden del usuario).

Hace automáticamente lo que hoy hace Muse por el chat cuando el
Worker o las subidas fallan: revisa los jobs de hoy en D1, reintenta
lo atascado, verifica en vivo cada URL publicada y avisa si no
puede arreglarlo. Es el respaldo del plan "revísalo tras cada
franja" (2026-11-21 09:17): la web intenta primero; el vigilante
revisa tras cada franja lo que realmente se publicó.

NO es el vigilante viejo (clipagent/watchdog.py): aquel vigila el
SUMINISTRO de clips (token de Twitch, dispatches, vivos en GTA VI,
VOD). Este vigila la PUBLICACIÓN (jobs, URLs, republicaciones).
Comparten la idea (diagnóstico + reparación + aviso solo si sigue
atascado) y el circuito 7500, nada más.

De dónde salen los datos (sin navegador, todo por API):
  - D1 directo (clipagent/d1_client.py, base vieja clips-avc, ambos
    tenants): jobs, settings (la autoridad viva de topes y
    espaciamiento), api_accounts (madurez: first_published_at,
    published_count, strikes) y worker_state. Sin token de
    Cloudflare, degrada a modo solo-panel (lectura de sebastian por
    PanelBridge; sin kaez y sin crear republicaciones) y lo dice.
  - PanelBridge (sesión sebastian, secretos PANEL_USERNAME /
    PANEL_PASSWORD que ya existen): POST /api/worker/run-once, que
    corre el ciclo del Worker para sebastian Y kaez (el Worker es
    la autoridad: su gate frena lo que no debe salir).

REGLAS QUE NUNCA SE SALTA (leídas de D1 settings + limits.yaml):
  - Franjas Chile 12:00-14:00 / 18:00-20:00 / 21:00-23:00. El
    vigilante corre TRAS cada franja (14:30 / 20:30 / 23:30) y solo
    repara DENTRO de la ventana de 60 min tras la franja (la
    "ventana de reparación"): repara lo que era de ESA franja y se
    quedó atascado. Nunca crea publicaciones nuevas fuera de
    franja ni publica de más: como mucho 1 ciclo por corrida y 1
    publicación por plataforma y franja.
  - Espaciamiento (2 h, 4 h YouTube), topes diarios por plataforma
    (YT 6, TT/IG/FB 3), calentamiento 1/día por plataforma en
    cuentas nuevas (ni a mano se supera), máx 3 por cuenta en 24 h,
    mismo clip no se repite en la misma plataforma y cuenta el
    mismo día.
  - Un `posted` en D1 NO es prueba de que el vídeo esté vivo: cada
    URL se verifica en vivo (oEmbed en YouTube/TikTok; en
    Instagram/Facebook la verificación anónima es DÉBIL — un 200
    no prueba nada, lección del reel FB del 168 — y nunca basta
    para republicar solo: ahí solo se reporta la sospecha).

Qué hace en cada caso:
  - posted + URL viva (oEmbed 200) -> nada; queda en el informe.
  - posted + URL caída (oEmbed 404/401, evidencia FUERTE, solo
    YouTube/TikTok) -> crea un job de republicación con
    idempotency key `|repubN` para MAÑANA 12:00 (la regla de mismo
    clip hoy bloquea republicar hoy; el job queda scheduled y el
    ciclo de mañana lo publica). Sin duplicados: si ya hay una
    republicación en curso, no crea otra.
  - processing atascado (>30 min) o scheduled vencido, con el gate
    a favor y en ventana de reparación -> UN ciclo run-once (el
    ciclo del Worker re-encola los atascados solo y publica lo
    vencido; el vigilante no toca los jobs a mano).
  - draft (el gate lo frenó: calentamiento / tope / mismo clip) ->
    NO se toca: se deja y se informa cuándo sale (mañana, o
    cuando suelte el espaciamiento).
  - failed terminal (retryable=0) -> NO se resucita hoy: el Worker
    ya decidió; se informa el motivo y que se re-encola mañana
    desde la Cola. failed con retryable=1 -> mañana.
  - awaiting_captcha -> necesita un humano; se avisa.
  - Worker parado (running=0) -> NO se arranca solo: se avisa
    ("pulsar Iniciar en el panel"). Arrancar el Worker es una
    decisión del usuario, no del vigilante.
  - Cuota D1 agotada (7500) -> se para todo de inmediato (circuito
    de clipagent/retry.py) y el informe lo dice: nada se reintenta
    hasta las 21:00.

Telegram SOLO si algo no se pudo arreglar (URL caída sin
republicación posible, failed terminal, Worker parado, captcha,
sospecha en IG/FB). El aviso va por PanelBridge.notify_telegram
(POST /api/bridge/notify); si la ruta falla, el mensaje queda
como borrador en el log y el informe.

CLI:
    python -m clipagent.publish_watchdog [--live | --dry-run]
        [--out-dir DIR] [--simulate] [--simulate-down]
  --dry-run es el default: lee, clasifica y verifica, sin escribir
  en D1, sin disparar ciclos y sin avisar. --live actúa. Exit 0
  SIEMPRE salvo crash: un chequeo sin nada que reparar nunca
  falla la corrida. Salida en español, una línea por paso.

Inyección para tests: run() acepta d1, bridge y http_get
inyectables; las funciones puras (franjas, calentamiento,
clasificación, verificación, informe) no tocan la red.
"""
import argparse
import hashlib
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone

try:
    from zoneinfo import ZoneInfo
    _CHILE_TZ = ZoneInfo("America/Santiago")
except Exception:  # pragma: no cover - Python <3.9 o sin tzdata
    _CHILE_TZ = timezone(timedelta(hours=-3))

from .publish import load_limits
from .retry import (
    is_d1_quota_error,
    panel_quota_circuit_open,
    trip_panel_quota_circuit,
)

# Tenants que vigila (la base vieja los tiene a los dos).
TENANTS = ("sebastian", "kaez")

# Plataformas de publicación (jobs.platform) -> familia de reglas.
PLATFORM_FAMILY = {
    "youtube_shorts": "youtube",
    "tiktok": "tiktok",
    "instagram_reels": "instagram",
    "facebook_reels": "facebook",
}

# Estados de job que el ciclo del Worker toma como vencidos
# (dueJobs: scheduled/processing con next_run_at vencido o NULL).
DUE_STATES = ("scheduled", "processing")
# Estados abiertos que el vigilante revisa (además de los posted
# de hoy, que se verifican).
OPEN_STATES = ("scheduled", "processing", "draft", "failed",
               "awaiting_captcha")

PUBLISH_WATCHDOG_REQUIRED = (
    "repair_window_min", "max_cycles_per_run",
    "stuck_processing_min", "verify_timeout_s", "telegram_enabled",
)

# Ventana de reparación por defecto si falta config (nunca se usa
# en producción: load_publish_watchdog_config falla fuerte sin la
# sección; estos valores solo documentan lo que config.yaml dice).
DEFAULT_REPAIR_WINDOW_MIN = 60
DEFAULT_MAX_CYCLES_PER_RUN = 1
DEFAULT_STUCK_PROCESSING_MIN = 30
DEFAULT_VERIFY_TIMEOUT_S = 20

# oEmbed: la única verificación FUERTE sin sesión.
YOUTUBE_OEMBED = "https://www.youtube.com/oembed?url={url}&format=json"
TIKTOK_OEMBED = "https://www.tiktok.com/oembed?url={url}"
USER_AGENT = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/120.0 Safari/537.36")

# Marcas de "no disponible" en el HTML de IG/FB (la verificación
# anónima es débil: un 200 no prueba nada; estas marcas solo
# generan SOSPECHA, nunca una republicación automática).
DOWN_MARKERS = (
    "no está disponible", "no esta disponible",
    "se eliminó", "se elimino", "isn't available",
    "not available", "no longer available",
    "this reel isn't available", "content isn't available",
)


def _log(msg):
    print(f"[pub-watchdog] {msg}", flush=True)


def load_publish_watchdog_config(cfg=None):
    """Config del vigilante de publicación desde config.yaml.

    Regla single-source del repo (igual que load_watchdog_config):
    si falta la sección o un valor, falla fuerte con el nombre del
    valor (ConfigError), nunca con un default silencioso.
    """
    from .validate import ConfigError

    if cfg is None:
        from .config import load_config
        cfg = load_config()
    section = (cfg or {}).get("publish_watchdog")
    if not isinstance(section, dict):
        raise ConfigError(
            "publish_watchdog: falta la sección 'publish_watchdog' "
            "en config.yaml (el vigilante de publicación no arranca "
            "sin sus umbrales; sin defaults silenciosos)")
    missing = [k for k in PUBLISH_WATCHDOG_REQUIRED
               if k not in section]
    if missing:
        raise ConfigError(
            "publish_watchdog: faltan claves en config.yaml: "
            + ", ".join(f"publish_watchdog.{k}" for k in missing)
            + " (sin defaults silenciosos)")
    out = {}
    for key in ("repair_window_min", "max_cycles_per_run",
                "stuck_processing_min", "verify_timeout_s"):
        value = section[key]
        if not isinstance(value, int) or isinstance(value, bool):
            raise ConfigError(
                f"publish_watchdog.{key}: debe ser entero, es "
                f"{value!r}")
        out[key] = value
    if not isinstance(section["telegram_enabled"], bool):
        raise ConfigError(
            "publish_watchdog.telegram_enabled: debe ser true/false, "
            f"es {section['telegram_enabled']!r}")
    out["telegram_enabled"] = section["telegram_enabled"]
    return out


# ---------------------------------------------------------------------------
# 1. Tiempo (hora Chile, America/Santiago) y franjas
# ---------------------------------------------------------------------------

def santiago_day_key(now_ms=None):
    """Día YYYY-MM-DD en America/Santiago (el mismo que posted_day
    y la idempotency_key del panel)."""
    now = int(now_ms if now_ms is not None else time.time() * 1000)
    return datetime.fromtimestamp(now / 1000, _CHILE_TZ).date().isoformat()


def santiago_minute_of_day(now_ms=None):
    """Minutos desde medianoche en America/Santiago."""
    now = int(now_ms if now_ms is not None else time.time() * 1000)
    dt = datetime.fromtimestamp(now / 1000, _CHILE_TZ)
    return dt.hour * 60 + dt.minute


def santiago_ms_for(day_key, hhmm):
    """Epoch ms de un día HH:MM en America/Santiago."""
    dt = datetime.fromisoformat(f"{day_key}T{hhmm}:00").replace(
        tzinfo=_CHILE_TZ)
    return int(dt.timestamp() * 1000)


def next_day_key(day_key):
    """Día siguiente (YYYY-MM-DD)."""
    return (date.fromisoformat(day_key) + timedelta(days=1)).isoformat()


def _hhmm_to_min(hhmm):
    h, m = hhmm.split(":")
    return int(h) * 60 + int(m)


def load_slots(limits=None):
    """Franjas [(etiqueta, inicio_min, fin_min)] ordenadas.

    De limits.yaml (time_slots: morning/afternoon/night). La
    fuente viva es la misma que usa el panel (12-14 / 18-20 /
    21-23 hora Chile).
    """
    slots = ((limits or load_limits()).get("time_slots") or {})
    out = []
    for label, times in slots.items():
        if not isinstance(times, (list, tuple)) or len(times) != 2:
            continue
        out.append((str(label), _hhmm_to_min(str(times[0])),
                    _hhmm_to_min(str(times[1]))))
    out.sort(key=lambda s: s[1])
    return out


def slot_just_ended(now_ms, slots=None, repair_window_min=None):
    """Etiqueta de la franja que terminó hace menos de
    repair_window_min (la ventana de reparación), o None.

    El vigilante corre tras cada franja y solo repara lo de ESA
    franja: si ahora no es la ventana tras una franja, no hay
    reparación posible (solo verificación + informe).
    """
    if slots is None:
        slots = load_slots()
    if repair_window_min is None:
        repair_window_min = DEFAULT_REPAIR_WINDOW_MIN
    minute = santiago_minute_of_day(now_ms)
    for label, _start, end in slots:
        if end <= minute < end + repair_window_min:
            return label
    return None


def in_slot(now_ms, slots=None):
    """True si ahora (hora Chile) cae dentro de alguna franja."""
    if slots is None:
        slots = load_slots()
    minute = santiago_minute_of_day(now_ms)
    return any(start <= minute < end for _l, start, end in slots)


# ---------------------------------------------------------------------------
# 2. Calentamiento (puerto de warmupStage/getMaturityInfo del Worker)
# ---------------------------------------------------------------------------

def warmup_stage(days, published_count, strikes, new_days=14,
                 new_pubs=20):
    """Etapa de calentamiento (misma fórmula que el Worker).

    days: días desde la primera publicación (None = nunca publicó).
    strikes > 0 devuelve la cuenta a semana 1/2 hasta limpiarse.
    """
    if strikes and strikes > 0:
        return "semana2" if (days is not None and days >= 7) \
            else "semana1"
    if days is None or days < new_days // 2:
        return "semana1"
    if days < new_days:
        return "semana2"
    if published_count >= new_pubs:
        return "establecida"
    return "semana3"


def warmup_daily_cap(stage, family, rules):
    """Tope diario por calentamiento para una familia de plataforma.

    Semana 1/2: 1/día (salvo YouTube, 6/día: new_account_daily_youtube).
    Semana 3: el plan normal (pero sin acceso al máximo duro).
    Establecida: el máximo duro.
    rules: {plan, max, new_daily} de la familia.
    """
    if stage in ("semana1", "semana2"):
        return min(rules["new_daily"], rules["plan"], rules["max"])
    if stage == "semana3":
        return rules["plan"]
    return rules["max"]


# ---------------------------------------------------------------------------
# 3. Reglas vivas (D1 settings manda; limits.yaml es el fallback)
# ---------------------------------------------------------------------------

def resolve_rules(settings_rows, limits=None):
    """Topes y espaciamiento por tenant y familia de plataforma.

    settings_rows: [{tenant_id, data}] de la tabla settings (JSON
    en `data`). La autoridad viva es D1: youtube_plan/max y
    resto_plan/max, spacing_hours, new_account_daily(_youtube).
    Lo que D1 no trae (youtube_spacing_hours, rolling_24h_max)
    sale de limits.yaml (defaults del código del Worker: 4 h YT,
    rolling 3). Sin settings (sin token D1), TODO sale de
    limits.yaml y el informe lo dice (puede estar desactualizado:
    limits.yaml dice YT plan 1/máx 2, D1 dice 6/6).
    """
    limits = limits or load_limits()
    out = {}
    by_tenant = {}
    for row in (settings_rows or []):
        data = row.get("data") or {}
        if isinstance(data, str):
            try:
                data = json.loads(data)
            except ValueError:
                data = {}
        by_tenant[row.get("tenant_id")] = data if isinstance(
            data, dict) else {}
    for tenant in TENANTS:
        data = by_tenant.get(tenant) or {}
        yt_plan = data.get("youtube_plan") \
            or limits["youtube_shorts"]["plan_per_day"]
        yt_max = data.get("daily_limit_youtube_shorts") \
            or data.get("youtube_max") \
            or limits["youtube_shorts"]["max_per_day"]
        resto_plan = data.get("resto_plan") \
            or limits["tiktok"]["plan_per_day"]
        resto_max = data.get("resto_max") \
            or limits["tiktok"]["max_per_day"]
        daily = {
            "youtube": data.get("daily_limit_youtube_shorts"),
            "tiktok": data.get("daily_limit_tiktok"),
            "instagram": data.get("daily_limit_instagram"),
            "facebook": data.get("daily_limit_facebook"),
        }
        out[tenant] = {
            "youtube": {
                "plan": int(yt_plan), "max": int(daily["youtube"]
                                                 or yt_max),
                "spacing_h": int(
                    data.get("youtube_spacing_hours")
                    or limits["youtube_spacing_hours"]),
                "new_daily": int(
                    data.get("new_account_daily_youtube") or 6),
            },
            "tiktok": {
                "plan": int(resto_plan),
                "max": int(daily["tiktok"] or resto_max),
                "spacing_h": int(data.get("spacing_hours")
                                 or limits["min_spacing_hours"]),
                "new_daily": int(data.get("new_account_daily")
                                 or limits["new_account_daily"]),
            },
            "instagram": {
                "plan": int(resto_plan),
                "max": int(daily["instagram"] or resto_max),
                "spacing_h": int(data.get("spacing_hours")
                                 or limits["min_spacing_hours"]),
                "new_daily": int(data.get("new_account_daily")
                                 or limits["new_account_daily"]),
            },
            "facebook": {
                "plan": int(resto_plan),
                "max": int(daily["facebook"] or resto_max),
                "spacing_h": int(data.get("spacing_hours")
                                 or limits["min_spacing_hours"]),
                "new_daily": int(data.get("new_account_daily")
                                 or limits["new_account_daily"]),
            },
            "rolling_max": int(data.get("rolling_24h_max")
                               or limits["rolling_24h_max"]),
            "min_virality": int(data.get("min_virality") or 45),
            "min_seo": int(data.get("min_seo") or 80),
            "from_d1": bool(by_tenant.get(tenant)),
        }
    return out

# ---------------------------------------------------------------------------
# 4. Contexto vivo desde D1 (contadores por cuenta y plataforma)
# ---------------------------------------------------------------------------

@dataclass
class AccountCtx:
    account_id: int
    platform: str
    family: str
    tenant: str
    stage: str
    days: object = None
    published_count: int = 0
    strikes: int = 0
    published_today: int = 0
    posted_24h: int = 0
    last_published_at_ms: object = None


@dataclass
class WatchContext:
    rules: dict
    accounts: dict = field(default_factory=dict)
    same_clip_today: set = field(default_factory=set)
    worker_running: dict = field(default_factory=dict)
    day_key: str = ""
    now_ms: int = 0


def build_context(jobs, accounts, rules, now_ms, day_key):
    """Contadores que el gate necesita, derivados de D1.

    jobs: filas de jobs (hoy + abiertos). accounts: filas de
    api_accounts. rules: resolve_rules(). Un job posted cuenta
    para el tope diario de SU cuenta y plataforma, para el
    rolling 24 h de SU cuenta y para el mismo-clip de hoy.
    """
    ctx = WatchContext(rules=rules, day_key=day_key, now_ms=now_ms)
    limits = load_limits()
    warmup = (limits.get("warmup") or {})
    new_days = int(warmup.get("new_if_days") or 14)
    new_pubs = int(warmup.get("new_if_publishes") or 20)
    for row in (accounts or []):
        tenant = row.get("tenant_id")
        account_id = row.get("id")
        platform = row.get("platform") or ""
        family = PLATFORM_FAMILY.get(platform, platform)
        first = row.get("first_published_at")
        days = None
        if first:
            days = max(0, (int(now_ms) - int(first)) // 86400000)
        stage = warmup_stage(
            days, int(row.get("published_count") or 0),
            int(row.get("strikes") or 0), new_days, new_pubs)
        ctx.accounts[(tenant, account_id)] = AccountCtx(
            account_id=account_id, platform=platform, family=family,
            tenant=tenant, stage=stage, days=days,
            published_count=int(row.get("published_count") or 0),
            strikes=int(row.get("strikes") or 0))
    day_start = now_ms - 24 * 3600000
    for job in (jobs or []):
        tenant = job.get("tenant_id")
        account_id = job.get("api_account_id")
        if job.get("state") != "posted":
            continue
        key = (tenant, account_id)
        acct = ctx.accounts.get(key)
        if job.get("posted_day") == day_key:
            ctx.same_clip_today.add(
                (tenant, job.get("clip_id"), job.get("platform"),
                 account_id))
            if acct is not None:
                acct.published_today += 1
        updated = job.get("updated_at") or 0
        if updated and int(updated) >= day_start:
            if acct is not None:
                acct.posted_24h += 1
        if acct is not None:
            last = acct.last_published_at_ms
            if updated and (last is None or int(updated) > last):
                acct.last_published_at_ms = int(updated)
    return ctx


def _hhmm(ms):
    dt = datetime.fromtimestamp(int(ms) / 1000, _CHILE_TZ)
    return dt.strftime("%H:%M")


def gate_hold(job, ctx):
    """(motivo, cuándo) si el gate frena este job, o None.

    Espejo de SOLO LECTURA del gate del Worker (gate.ts): el
    Worker es la autoridad y decide al publicar; esto solo evita
    ciclos inútiles y explica el freno en el informe. Orden:
    calentamiento -> tope diario -> rolling 24 h -> espaciamiento
    -> mismo clip hoy.
    """
    tenant = job.get("tenant_id")
    account_id = job.get("api_account_id")
    platform = job.get("platform") or ""
    family = PLATFORM_FAMILY.get(platform, platform)
    acct = ctx.accounts.get((tenant, account_id))
    if acct is None:
        return ("sin_cuenta",
                "sin cuenta API para este job: revisar en el panel")
    rules = (ctx.rules.get(tenant) or {}).get(family) or {}
    if not rules:
        return ("sin_reglas",
                "sin reglas para esta plataforma: revisar Ajustes")
    cap = warmup_daily_cap(acct.stage, family, rules)
    now_ms = ctx.now_ms
    if acct.published_today >= cap:
        if acct.stage in ("semana1", "semana2"):
            return ("calentamiento",
                    "mañana (sigue en calentamiento 1/día; sube a "
                    "2/día en semana 3, a los 14 días desde la "
                    "primera publicación)")
        if acct.stage == "semana3":
            return ("plan_diario",
                    f"mañana (plan {rules['plan']}/día en semana 3)")
        return ("tope_diario",
                f"mañana (techo {rules['max']}/día)")
    if acct.published_today >= rules["max"]:
        return ("tope_diario", f"mañana (techo {rules['max']}/día)")
    rolling_max = (ctx.rules.get(tenant) or {}).get(
        "rolling_max", 3)
    if acct.posted_24h >= rolling_max:
        return ("rolling_24h",
                "cuando la publicación más antigua cumpla 24 h "
                f"(máx {rolling_max} por cuenta en 24 h)")
    last = acct.last_published_at_ms
    spacing_ms = int(rules["spacing_h"]) * 3600000
    if last and now_ms - last < spacing_ms:
        nxt = last + spacing_ms
        return ("espaciamiento",
                f"a las {_hhmm(nxt)} (mín {rules['spacing_h']} h "
                "entre subidas en esta plataforma)")
    if (tenant, job.get("clip_id"), platform, account_id) \
            in ctx.same_clip_today:
        return ("mismo_clip_hoy",
                "mañana (el mismo clip no se repite en la misma "
                "plataforma y cuenta el mismo día)")
    return None


# ---------------------------------------------------------------------------
# 5. Clasificación de jobs
# ---------------------------------------------------------------------------

def classify_job(job, ctx, slot_just_ended, stuck_min=30):
    """(veredicto, motivo, cuándo) para UN job.

    Veredictos: verify, posted_no_url, processing, stuck, ready,
    waiting_next_run, waiting_slot, held_<motivo>, draft,
    failed_terminal, failed_retryable, captcha, other.
    `ready` = candidato al ciclo (solo en ventana de reparación y
    con el gate a favor). `held_*` = el gate lo frena; nunca se
    fuerza. `draft` nunca es candidato al ciclo (dueJobs no toma
    draft: el gate lo dejó ahí por algo).
    """
    state = job.get("state")
    now_ms = ctx.now_ms
    if state == "posted":
        if (job.get("evidence_url") or "").startswith("https://"):
            return ("verify", "publicado: verificar la URL en vivo",
                    "")
        return ("posted_no_url",
                "publicado sin evidence_url: no se puede verificar",
                "revisar a mano en el panel")
    if state == "processing":
        updated = job.get("updated_at") or 0
        age_min = (now_ms - int(updated)) / 60000 if updated else 9999
        if age_min >= stuck_min:
            hold = gate_hold(job, ctx)
            if hold:
                return (f"held_{hold[0]}", hold[0], hold[1])
            if slot_just_ended:
                return ("ready",
                        "atascado en processing "
                        f"({age_min:.0f} min): el ciclo lo re-encola",
                        "en este ciclo")
            return ("stuck",
                    f"atascado en processing ({age_min:.0f} min)",
                    "próxima franja")
        return ("processing", "en curso", "")
    if state == "scheduled":
        nxt = job.get("next_run_at")
        if nxt and int(nxt) > now_ms:
            return ("waiting_next_run",
                    "programado para más tarde",
                    f"a las {_hhmm(nxt)}")
        hold = gate_hold(job, ctx)
        if hold:
            return (f"held_{hold[0]}", hold[0], hold[1])
        if slot_just_ended:
            return ("ready", "vencido y con el gate a favor",
                    "en este ciclo")
        return ("waiting_slot", "vencido, esperando franja",
                "próxima franja")
    if state == "draft":
        hold = gate_hold(job, ctx)
        if hold:
            return (f"held_{hold[0]}", hold[0], hold[1])
        reason = (job.get("last_error") or "").strip()
        return ("draft",
                f"en borrador: {reason[:120]}" if reason
                else "en borrador (el gate lo frenó)",
                "cuando el gate lo permita")
    if state == "failed":
        reason = (job.get("last_error") or "").strip()
        if int(job.get("retryable") or 0) == 1:
            return ("failed_retryable",
                    f"falló (reintentable): {reason[:120]}",
                    "mañana, re-encolado desde la Cola")
        return ("failed_terminal",
                f"falló (terminal): {reason[:120]}",
                "mañana, re-encolar desde la Cola")
    if state == "awaiting_captcha":
        return ("captcha", "espera un captcha: necesita un humano",
                "resolver el captcha en el panel")
    return ("other", f"estado {state!r}", "")


# ---------------------------------------------------------------------------
# 6. Verificación de URLs en vivo
# ---------------------------------------------------------------------------

def _default_http_get(url, headers, timeout):
    req = urllib.request.Request(url, headers=headers, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, resp.read().decode(
                "utf-8", "ignore")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "ignore")[:2000]
    except urllib.error.URLError as e:
        raise RuntimeError(f"GET {url} -> {e}")


def verify_url(url, platform, http_get=None, timeout=20):
    """(estado, detalle) de una URL publicada.

    YouTube/TikTok por oEmbed: evidencia FUERTE (200 = viva;
    404/401 = caída). Instagram/Facebook por GET anónimo:
    evidencia DÉBIL — un 200 no prueba nada (lección del reel FB
    del 168) y solo las marcas de "no disponible" generan
    sospecha; nunca "down" y nunca basta para republicar solo.
    Estados: live, down, weak, unknown.
    """
    get = http_get or _default_http_get
    if not url or not str(url).startswith("https://"):
        return ("unknown", "sin URL")
    family = PLATFORM_FAMILY.get(platform, platform)
    try:
        if family == "youtube":
            oembed = YOUTUBE_OEMBED.format(
                url=urllib.parse.quote(url, safe=""))
            status, _body = get(oembed, {"User-Agent": USER_AGENT},
                                timeout)
            if status == 200:
                return ("live", "oEmbed 200: el Short existe")
            if status in (401, 404):
                return ("down", f"oEmbed HTTP {status}: no existe")
            return ("unknown", f"oEmbed HTTP {status}")
        if family == "tiktok":
            oembed = TIKTOK_OEMBED.format(
                url=urllib.parse.quote(url, safe=""))
            status, _body = get(oembed, {"User-Agent": USER_AGENT},
                                timeout)
            if status == 200:
                return ("live", "oEmbed 200: el vídeo existe")
            if status in (400, 401, 404):
                return ("down", f"oEmbed HTTP {status}: no existe")
            return ("unknown", f"oEmbed HTTP {status}")
        # Instagram / Facebook: GET anónimo, evidencia débil.
        status, body = get(url, {"User-Agent": USER_AGENT}, timeout)
        if status != 200:
            return ("unknown",
                    f"GET HTTP {status}: no se puede verificar sin "
                    "sesión (podría ser el muro de login)")
        low = (body or "").lower()
        for marker in DOWN_MARKERS:
            if marker in low:
                return ("weak",
                        "sospecha de caída (el HTML dice que no está "
                        "disponible): verificar a mano, NO se "
                        "republica solo con esto")
        short = url.rstrip("/").split("/")[-1]
        if short and short in (body or ""):
            return ("weak", "parece vivo (verificación débil: un "
                    "200 anónimo no prueba nada)")
        return ("unknown", "GET 200 pero sin rastro del vídeo en "
                "el HTML: no se puede verificar")
    except Exception as e:  # noqa: BLE001 - un fallo de red no es caída
        return ("unknown", f"no se pudo comprobar: {e}")


# ---------------------------------------------------------------------------
# 7. Republicaciones (|repubN)
# ---------------------------------------------------------------------------

def repub_key(clip_id, platform, account_id, day_key, n):
    """Idempotency key de una republicación (sha256 hex).

    La clave canónica (sin sufijo) colisiona por UNIQUE con el job
    original del mismo día: por eso la republicación lleva
    `|repubN` (lección del job 118, 2026-11-21).
    """
    raw = f"{clip_id}|{platform}|{account_id}|{day_key}|repub{n}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def next_repub_n(existing_keys):
    """Siguiente N para |repubN (1 si no hay ninguna)."""
    n = 0
    for key in (existing_keys or []):
        m = re.search(r"\|repub(\d+)$", str(key or ""))
        if m:
            n = max(n, int(m.group(1)))
    return n + 1


def plan_republish(job, clip, existing_keys, target_day_key, now_ms):
    """Parámetros del INSERT de la republicación, o None.

    La republicación va para MAÑANA 12:00 (primera franja): la
    regla de mismo clip hoy bloquea republicar hoy. state
    scheduled, next_run_at mañana 12:00 Chile, slot_day mañana.
    Copia título, caption y r2_key del job original (mismo vídeo).
    None si el clip está bloqueado o no tiene vídeo.
    """
    if not clip:
        return None
    if int(clip.get("publish_blocked") or 0) == 1:
        return None
    r2_key = clip.get("video_r2_key") or job.get("r2_key") or ""
    if not r2_key:
        return None
    n = next_repub_n(existing_keys)
    key = repub_key(job.get("clip_id"), job.get("platform"),
                    job.get("api_account_id"), target_day_key, n)
    return {
        "clip_id": job.get("clip_id"),
        "platform": job.get("platform"),
        "api_account_id": job.get("api_account_id"),
        "title": job.get("title") or clip.get("title") or "",
        "caption": job.get("caption") or "",
        "r2_key": r2_key,
        "language": job.get("language") or clip.get("language")
        or "es",
        "tenant_id": job.get("tenant_id"),
        "idempotency_key": key,
        "repub_n": n,
        "target_day": target_day_key,
        "next_run_at": santiago_ms_for(target_day_key, "12:00"),
        "created_at": int(now_ms),
        "updated_at": int(now_ms),
    }


REPUBLISH_INSERT_SQL = (
    "INSERT INTO jobs (kind, clip_id, platform, api_account_id, "
    "state, idempotency_key, title, caption, r2_key, attempts, "
    "last_error, retryable, next_run_at, posted_day, created_at, "
    "updated_at, tenant_id, language, manual, slot_day) VALUES "
    "('publish', ?, ?, ?, 'scheduled', ?, ?, ?, ?, 0, '', 0, ?, "
    "NULL, ?, ?, ?, ?, 1, ?)"
)


def republish_insert_params(plan):
    """Params del INSERT, en el orden del SQL."""
    return [plan["clip_id"], plan["platform"],
            plan["api_account_id"], plan["idempotency_key"],
            plan["title"], plan["caption"], plan["r2_key"],
            plan["next_run_at"], plan["created_at"],
            plan["updated_at"], plan["tenant_id"],
            plan["language"], plan["target_day"]]

# ---------------------------------------------------------------------------
# 8. Lectura de D1 (base vieja, ambos tenants)
# ---------------------------------------------------------------------------

Q_SETTINGS = "SELECT tenant_id, data FROM settings"
Q_JOBS_TODAY = (
    "SELECT id, kind, clip_id, platform, api_account_id, state, "
    "idempotency_key, evidence_url, title, caption, r2_key, "
    "attempts, last_error, retryable, next_run_at, posted_day, "
    "created_at, updated_at, tenant_id, language, manual, slot_day "
    "FROM jobs WHERE tenant_id IN ('sebastian', 'kaez') AND "
    "(posted_day = ? OR state IN ('scheduled', 'processing', "
    "'draft', 'failed', 'awaiting_captcha')) ORDER BY updated_at DESC"
)
Q_ACCOUNTS = (
    "SELECT id, platform, language, label, status, verify_ok, "
    "tenant_id, first_published_at, published_count, strikes FROM "
    "api_accounts WHERE tenant_id IN ('sebastian', 'kaez')"
)
Q_WORKER_RUNNING = "SELECT tenant_id, running FROM worker_state"
Q_CLIP = (
    "SELECT id, title, seo_description, publish_blocked, "
    "video_r2_key, language, tenant_id FROM clips WHERE id = ? "
    "AND tenant_id = ?"
)
Q_EXISTING_KEYS = (
    "SELECT idempotency_key FROM jobs WHERE clip_id = ? AND "
    "platform = ? AND api_account_id = ?"
)


def load_d1_snapshot(d1, day_key):
    """(settings, jobs, accounts, worker_running) desde D1.

    Solo lectura: no consume cuota de escrituras. Levanta
    D1QuotaError si la cuota está agotada (el llamador lo
    convierte en informe, no en crash).
    """
    from .d1_client import D1QuotaError  # noqa: F401 - re-export

    settings = d1.query(Q_SETTINGS)
    jobs = d1.query(Q_JOBS_TODAY, [day_key])
    accounts = d1.query(Q_ACCOUNTS)
    running = {}
    for row in d1.query(Q_WORKER_RUNNING):
        running[row.get("tenant_id")] = int(
            row.get("running") or 0) == 1
    return settings, jobs, accounts, running


def load_panel_snapshot(bridge):
    """Jobs de sebastian por PanelBridge (fallback sin token D1).

    La lectura del panel es por tenant: solo sebastian. No hay
    settings ni madurez por esta vía (el gate lo decide el
    Worker al ciclar); el informe lo dice.
    """
    jobs = []
    for job in bridge.list_jobs(limit=100):
        jobs.append(dict(job))
    return jobs


# ---------------------------------------------------------------------------
# 9. Informe
# ---------------------------------------------------------------------------

def build_telegram_message(report):
    """Aviso corto: qué no se pudo arreglar y qué necesita un humano.

    Solo se envía si hay algo sin arreglar (down sin
    republicación, failed terminal, Worker parado, captcha,
    sospecha IG/FB). Sin líneas de log citadas que no existen:
    el diagnóstico sale de D1 + la verificación en vivo.
    """
    lines = ["🚨 Vigilante de publicación: algo no se pudo arreglar",
             ""]
    # Solo lo DE HOY: los viejos quedan en el informe, no en el
    # aviso (si no, cada corrida avisaría de lo mismo).
    for item in (report.get("unfixable_fresh")
                 or report.get("unfixable") or []):
        lines.append(f"• {item}")
    if report.get("republished"):
        lines.append("")
        lines.append("Republicaciones creadas (salen mañana 12:00):")
        for item in report["republished"]:
            lines.append(f"• {item}")
    if report.get("held"):
        lines.append("")
        lines.append("Frenados (con motivo y cuándo salen):")
        for item in report["held"][:6]:
            lines.append(f"• {item}")
    lines.append("")
    lines.append("Informe completo en el artefacto de la corrida.")
    return "\n".join(lines)


def build_report_md(report):
    """Informe markdown (artefacto de la corrida + log)."""
    rules_src = ("D1 (vivas)" if report["rules_from_d1"]
                 else "limits.yaml (fallback, pueden estar "
                 "desactualizadas)")
    lines = [f"# Vigilante de publicación — {report['day_key']} "
             f"(franja {report.get('slot') or 'sin franja'})",
             "",
             f"Modo: {'LIVE' if report['live'] else 'dry-run'} · "
             f"reglas: {rules_src} · fuente: {report['source']}",
             ""]
    if report.get("notes"):
        lines.append("## Notas")
        for note in report["notes"]:
            lines.append(f"- {note}")
        lines.append("")
    lines.append("## Publicados y verificados")
    if report.get("verified"):
        for item in report["verified"]:
            lines.append(f"- {item}")
    else:
        lines.append("- (ningún job posted hoy)")
    lines.append("")
    lines.append("## Reparados (ciclo run-once)")
    if report.get("repaired"):
        for item in report["repaired"]:
            lines.append(f"- {item}")
    else:
        lines.append("- (nada que reparar en esta corrida)")
    lines.append("")
    lines.append("## Republicaciones")
    if report.get("republished"):
        for item in report["republished"]:
            lines.append(f"- {item}")
    else:
        lines.append("- (ninguna)")
    lines.append("")
    lines.append("## Frenados (con motivo y cuándo salen)")
    if report.get("held"):
        for item in report["held"]:
            lines.append(f"- {item}")
    else:
        lines.append("- (ninguno)")
    lines.append("")
    lines.append("## Sin arreglar (necesita un humano)")
    if report.get("unfixable"):
        for item in report["unfixable"]:
            lines.append(f"- {item}")
    else:
        lines.append("- (nada)")
    lines.append("")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# 10. Orquestación
# ---------------------------------------------------------------------------

@dataclass
class _Outcome:
    verified: list = field(default_factory=list)
    repaired: list = field(default_factory=list)
    republished: list = field(default_factory=list)
    held: list = field(default_factory=list)
    unfixable: list = field(default_factory=list)
    # Subconjunto de unfixable que es DE HOY (job actualizado o
    # publicado hoy, o fallo de esta misma corrida): solo estos
    # disparan Telegram. Los viejos (p. ej. los borradores TikTok
    # cancelados anoche) quedan en el informe, no en el aviso —
    # si no, cada corrida avisaría de lo mismo 3 veces al día.
    unfixable_fresh: list = field(default_factory=list)
    notes: list = field(default_factory=list)


def _job_label(job):
    return (f"job {job.get('id')} (clip {job.get('clip_id')}, "
            f"{job.get('platform')}, {job.get('tenant_id')})")


def run(d1=None, bridge=None, http_get=None, now_ms=None, live=False,
        out_dir=None, cfg=None, slots=None):
    """Una revisión tras una franja. Devuelve el informe (dict).

    d1: D1Client (o fake con .query/.execute) o None. bridge:
    PanelBridge (o fake con .run_once()/.notify_telegram()) o
    None. Sin d1 ni bridge, solo se puede simular: el informe lo
    dice y no se actúa. En dry-run NUNCA se escribe en D1, no se
    dispara ningún ciclo y no se avisa por Telegram.
    """
    wcfg = cfg or load_publish_watchdog_config()
    now = int(now_ms if now_ms is not None else time.time() * 1000)
    day_key = santiago_day_key(now)
    slots = slots if slots is not None else load_slots()
    slot = slot_just_ended(now, slots, wcfg["repair_window_min"])
    out = _Outcome()
    day_start_ms = santiago_ms_for(day_key, "00:00")

    def _add_unfixable(job, text):
        """Añade a unfixable; a unfixable_fresh solo si el job es
        de hoy (publicado o actualizado hoy)."""
        out.unfixable.append(text)
        fresh = (job.get("posted_day") == day_key) or \
            ((job.get("updated_at") or 0) >= day_start_ms)
        if fresh:
            out.unfixable_fresh.append(text)

    report = {
        "day_key": day_key, "slot": slot, "live": bool(live),
        "rules_from_d1": False, "source": "sin datos",
        "verified": out.verified, "repaired": out.repaired,
        "republished": out.republished, "held": out.held,
        "unfixable": out.unfixable,
        "unfixable_fresh": out.unfixable_fresh, "notes": out.notes,
    }
    _log(f"revisión {day_key} (franja: {slot or 'ninguna, '
          'fuera de ventana de reparación'}; "
          f"modo {'LIVE' if live else 'dry-run'})")
    if panel_quota_circuit_open():
        out.notes.append(
            "circuito 7500 abierto: cuota D1 agotada hasta las "
            "21:00 (-03); sin acciones en esta corrida")
        _log("circuito 7500 abierto: sin acciones")
        return _finish(report, out, out_dir)

    settings_rows, jobs, accounts, worker_running = [], [], [], {}
    source = None
    if d1 is not None:
        try:
            (settings_rows, jobs, accounts,
             worker_running) = load_d1_snapshot(d1, day_key)
            source = "D1 (clips-avc)"
        except Exception as e:  # noqa: BLE001 - D1 caído = informe
            if is_d1_quota_error(e):
                trip_panel_quota_circuit()
                out.notes.append(
                    "cuota D1 agotada (7500): sin acciones hasta "
                    "las 21:00 (-03)")
                _log("cuota D1 agotada (7500): sin acciones")
                return _finish(report, out, out_dir)
            out.notes.append(f"D1 no responde ({e}): se intenta "
                             "por el panel")
            _log(f"D1 no responde ({e})")
    if source is None and bridge is not None:
        try:
            jobs = load_panel_snapshot(bridge)
            source = "panel (solo sebastian, sin D1)"
            out.notes.append(
                "sin token D1: solo se ven los jobs de sebastian "
                "por el panel; kaez solo se repara con el ciclo "
                "(run-once corre ambos tenants)")
        except Exception as e:  # noqa: BLE001 - panel caído
            out.notes.append(f"el panel no responde ({e})")
            _log(f"el panel no responde ({e})")
    if source is None:
        out.notes.append(
            "sin D1 ni panel: no hay datos; nada que revisar")
        _log("sin D1 ni panel: nada que revisar")
        return _finish(report, out, out_dir)
    report["source"] = source

    rules = resolve_rules(settings_rows)
    report["rules_from_d1"] = any(
        (rules.get(t) or {}).get("from_d1") for t in TENANTS)
    ctx = build_context(jobs, accounts, rules, now, day_key)
    ctx.worker_running = worker_running

    # 1. Clasificar todos los jobs.
    classified = {}
    for job in jobs:
        verdict, reason, when = classify_job(
            job, ctx, slot,
            stuck_min=wcfg["stuck_processing_min"])
        classified[job.get("id")] = (verdict, reason, when)
        label = _job_label(job)
        if verdict.startswith("held_"):
            out.held.append(f"{label}: frenado ({reason}) — sale "
                            f"{when}")
        elif verdict in ("failed_terminal", "failed_retryable",
                         "captcha", "posted_no_url"):
            _add_unfixable(job, f"{label}: {reason} — {when}")
        elif verdict == "draft":
            out.held.append(f"{label}: {reason} — {when}")
        elif verdict in ("waiting_next_run", "waiting_slot",
                         "processing", "other"):
            _log(f"{label}: {reason} ({when})")

    # 2. Verificar en vivo cada URL publicada.
    down_jobs = []
    for job in jobs:
        verdict = classified.get(job.get("id"), ("", "", ""))[0]
        if verdict != "verify":
            continue
        url = job.get("evidence_url") or ""
        status, detail = verify_url(
            url, job.get("platform") or "", http_get=http_get,
            timeout=wcfg["verify_timeout_s"])
        label = _job_label(job)
        if status == "live":
            out.verified.append(f"{label}: VIVA ({detail}) — {url}")
        elif status == "down":
            out.verified.append(f"{label}: CAÍDA ({detail}) — {url}")
            down_jobs.append(job)
        elif status == "weak":
            out.verified.append(f"{label}: SIN VERIFICAR ({detail})")
            _add_unfixable(
                job,
                f"{label}: la URL no se puede verificar sin sesión "
                f"({detail}) — revisar a mano")
        else:
            out.verified.append(
                f"{label}: SIN VERIFICAR ({detail})")
            out.notes.append(f"{label}: verificación imposible "
                             f"({detail})")

    # 3. Republicaciones (solo evidencia FUERTE de caída).
    tomorrow = next_day_key(day_key)
    for job in down_jobs:
        label = _job_label(job)
        already = [j for j in jobs
                   if j.get("clip_id") == job.get("clip_id")
                   and j.get("platform") == job.get("platform")
                   and j.get("api_account_id")
                   == job.get("api_account_id")
                   and j.get("state") in ("scheduled", "processing",
                                          "draft")
                   and "|repub" in str(j.get("idempotency_key") or "")]
        if already:
            out.notes.append(
                f"{label}: ya hay una republicación en curso "
                f"(job {already[0].get('id')}): no se crea otra")
            continue
        if d1 is None:
            _add_unfixable(
                job,
                f"{label}: URL caída pero sin token D1: no se "
                "puede crear la republicación; crearla a mano "
                "con |repubN para mañana 12:00")
            continue
        try:
            clip_rows = d1.query(
                Q_CLIP, [job.get("clip_id"), job.get("tenant_id")])
            keys_rows = d1.query(
                Q_EXISTING_KEYS,
                [job.get("clip_id"), job.get("platform"),
                 job.get("api_account_id")])
        except Exception as e:  # noqa: BLE001 - sin clip = sin repub
            _add_unfixable(
                job,
                f"{label}: URL caída pero no se pudo leer el clip "
                f"({e}): republicar a mano mañana 12:00")
            continue
        clip = clip_rows[0] if clip_rows else None
        plan = plan_republish(
            job, clip,
            [r.get("idempotency_key") for r in keys_rows],
            tomorrow, now)
        if plan is None:
            _add_unfixable(
                job,
                f"{label}: URL caída pero el clip está bloqueado "
                "o sin vídeo: no se republica")
            continue
        if live:
            try:
                changed = d1.execute(REPUBLISH_INSERT_SQL,
                                     republish_insert_params(plan))
            except Exception as e:  # noqa: BLE001 - la repub no tumba
                if is_d1_quota_error(e):
                    trip_panel_quota_circuit()
                    _add_unfixable(
                        job,
                        f"{label}: cuota D1 agotada al crear la "
                        "republicación: queda para mañana")
                    continue
                _add_unfixable(
                    job,
                    f"{label}: no se pudo crear la republicación "
                    f"({e}): crearla a mano mañana 12:00")
                continue
            if changed:
                out.republished.append(
                    f"{label}: republicación |repub{plan['repub_n']} "
                    f"creada, sale mañana {tomorrow} 12:00")
            else:
                _add_unfixable(
                    job,
                    f"{label}: el INSERT no cambió filas: "
                    "republicar a mano mañana 12:00")
        else:
            out.republished.append(
                f"{label}: [dry-run] se crearía republicación "
                f"|repub{plan['repub_n']} para mañana {tomorrow} "
                "12:00")

    # 4. Ciclo run-once (solo en ventana de reparación, solo si
    # hay algo listo, solo 1 por corrida, solo si el Worker está
    # en marcha). El ciclo del Worker re-encola los atascados y
    # publica lo vencido; su gate frena lo que no debe salir.
    ready = [j for j in jobs
             if classified.get(j.get("id"), ("", "", ""))[0] == "ready"]
    cycles_used = 0
    if ready and slot:
        if not any(worker_running.get(t, True) for t in TENANTS):
            _msg = ("Worker parado en ambos tenants (running=0): "
                    "el ciclo no correría; pulsar Iniciar en el "
                    "panel")
            out.unfixable.append(_msg)
            out.unfixable_fresh.append(_msg)
        elif bridge is None:
            _msg = (f"{len(ready)} job(s) listos para el ciclo "
                    "pero sin sesión del panel: no se puede "
                    "disparar run-once")
            out.unfixable.append(_msg)
            out.unfixable_fresh.append(_msg)
        elif cycles_used < wcfg["max_cycles_per_run"]:
            if live:
                try:
                    res = bridge.run_once()
                    cycles_used += 1
                    ran = (res or {}).get("ran")
                    out.repaired.append(
                        f"ciclo run-once disparado (ran={ran}, "
                        f"processed={(res or {}).get('processed')}, "
                        f"stalled={(res or {}).get('stalled')}, "
                        f"kaez={(res or {}).get('kaez') or {}})")
                    _log(f"ciclo run-once: {res}")
                except Exception as e:  # noqa: BLE001 - el ciclo no tumba
                    _msg = (f"no se pudo disparar el ciclo "
                            f"run-once ({e}): {len(ready)} job(s) "
                            "siguen atascados")
                    out.unfixable.append(_msg)
                    out.unfixable_fresh.append(_msg)
            else:
                out.repaired.append(
                    f"[dry-run] se dispararía 1 ciclo run-once "
                    f"para {len(ready)} job(s) listos: "
                    + ", ".join(_job_label(j) for j in ready))
    elif ready and not slot:
        out.held.append(
            f"{len(ready)} job(s) listos pero fuera de ventana de "
            "reparación: salen en la próxima franja")

    # 5. Tras un ciclo en vivo, releer y verificar lo nuevo.
    if live and cycles_used:
        try:
            (_s2, jobs2, _a2,
             _w2) = load_d1_snapshot(d1, day_key) if d1 is not None \
                else (None, load_panel_snapshot(bridge), [], {})
            posted_now = [j for j in jobs2
                          if j.get("state") == "posted"
                          and j.get("evidence_url")]
            seen = {j.get("id") for j in jobs}
            for job in posted_now:
                if job.get("id") in seen:
                    continue
                url = job.get("evidence_url") or ""
                status, detail = verify_url(
                    url, job.get("platform") or "", http_get=http_get,
                    timeout=wcfg["verify_timeout_s"])
                label = _job_label(job)
                if status == "live":
                    out.verified.append(
                        f"{label}: VIVA tras el ciclo ({detail})")
                else:
                    _add_unfixable(
                        job,
                        f"{label}: publicada por el ciclo pero la "
                        f"URL no verifica ({status}: {detail})")
        except Exception as e:  # noqa: BLE001 - la relectura no tumba
            out.notes.append(f"no se pudo releer tras el ciclo ({e})")

    # 6. Telegram solo si algo DE HOY no se pudo arreglar.
    if live and out.unfixable_fresh and wcfg["telegram_enabled"]:
        if bridge is not None:
            try:
                bridge.notify_telegram(build_telegram_message(report))
                out.notes.append("aviso por Telegram enviado")
            except Exception as e:  # noqa: BLE001 - el aviso no tumba
                out.notes.append(
                    f"Telegram NO enviado ({e}); el mensaje queda "
                    "como borrador en el log")
                _log("borrador de Telegram:\n"
                     + build_telegram_message(report))
        else:
            out.notes.append(
                "sin sesión del panel: el aviso por Telegram queda "
                "como borrador en el log")
            _log("borrador de Telegram:\n"
                 + build_telegram_message(report))
    elif out.unfixable_fresh and not live:
        out.notes.append(
            "[dry-run] con algo de hoy sin arreglar se avisaría "
            "por Telegram en modo live")

    return _finish(report, out, out_dir)


def _finish(report, out, out_dir):
    report["verified"] = out.verified
    report["repaired"] = out.repaired
    report["republished"] = out.republished
    report["held"] = out.held
    report["unfixable"] = out.unfixable
    report["unfixable_fresh"] = out.unfixable_fresh
    report["notes"] = out.notes
    md = build_report_md(report)
    print(md, flush=True)
    if out_dir:
        os.makedirs(out_dir, exist_ok=True)
        path = os.path.join(
            out_dir,
            f"report-{report['day_key']}-"
            f"{report.get('slot') or 'sin-franja'}.md")
        with open(path, "w", encoding="utf-8") as f:
            f.write(md + "\n")
        _log(f"informe guardado en {path}")
    return report

# ---------------------------------------------------------------------------
# 11. CLI + simulación (prueba forzada, nunca toca nada real)
# ---------------------------------------------------------------------------

def _build_simulate_clients(now_ms, day_key, down=True):
    """Fakes del escenario forzado (prueba, nada real).

    Jobs de hoy (tenant sebastian, cuenta TikTok ES en semana 1
    con 1 publicado hoy; YouTube ES con 1 publicado hoy):
      900 YT posted con URL viva (oEmbed 200).
      901 YT posted con URL caída (oEmbed 404) -> republicación.
      902 TT scheduled vencido, gate a favor -> listo para el ciclo.
      903 TT draft frenado por calentamiento -> se deja, mañana.
      904 YT failed terminal -> se informa, mañana.
    Con down=False la 901 también está viva (escenario sin caídas).
    """
    minute = 60000
    hour = 3600000

    def job(job_id, clip, platform, account, state, **kw):
        base = {
            "id": job_id, "kind": "publish", "clip_id": clip,
            "platform": platform, "api_account_id": account,
            "state": state, "idempotency_key": f"sim{job_id}",
            "evidence_url": "", "title": f"clip {clip}",
            "caption": "desc #GTA6", "r2_key": f"clips/x/{clip}.mp4",
            "attempts": 0, "last_error": "", "retryable": 0,
            "next_run_at": None, "posted_day": None,
            "created_at": now_ms - 5 * hour,
            "updated_at": now_ms - 60 * minute,
            "tenant_id": "sebastian", "language": "es", "manual": 1,
            "slot_day": None,
        }
        base.update(kw)
        return base

    jobs = [
        job(899, 49, "tiktok", 5, "posted",
            evidence_url="https://www.tiktok.com/@x/video/899",
            posted_day=day_key, updated_at=now_ms - 5 * hour),
        job(900, 50, "youtube_shorts", 2, "posted",
            evidence_url="https://www.youtube.com/shorts/GOOD900",
            posted_day=day_key, updated_at=now_ms - 5 * hour),
        job(901, 51, "youtube_shorts", 2, "posted",
            evidence_url="https://www.youtube.com/shorts/DEAD901",
            posted_day=day_key, updated_at=now_ms - 5 * hour),
        job(902, 52, "youtube_shorts", 2, "scheduled",
            next_run_at=now_ms - 10 * minute,
            updated_at=now_ms - 60 * minute),
        job(903, 53, "tiktok", 5, "draft",
            last_error="gate de publicación: calentamiento: 1/1 "
                       "hoy en tiktok",
            updated_at=now_ms - 60 * minute),
        job(904, 54, "youtube_shorts", 2, "failed",
            last_error="YouTube devolvió 403: cuota",
            updated_at=now_ms - 60 * minute),
    ]
    if not down:
        # Sin caídas: la 901 también está viva.
        jobs[2]["evidence_url"] = \
            "https://www.youtube.com/shorts/GOOD901"
    # TikTok ES ya publicó 1 hoy (job 899): su tope de
    # calentamiento (semana 1, 1/día) está usado, así que el draft
    # 903 queda frenado por calentamiento. YouTube ES publicó 2
    # hoy (900, 901) hace 5 h: espaciamiento YT (4 h) cumplido y
    # tope 6/día sin usar, así que el 902 está listo para el ciclo.
    settings = [
        {"tenant_id": "sebastian", "data": {
            "daily_limit_youtube_shorts": 6,
            "daily_limit_tiktok": 3,
            "daily_limit_instagram": 3,
            "daily_limit_facebook": 3,
            "youtube_plan": 6, "youtube_max": 6,
            "resto_plan": 2, "resto_max": 3,
            "spacing_hours": 2, "new_account_daily": 1,
            "new_account_daily_youtube": 6,
            "min_virality": 45, "min_seo": 80}},
        {"tenant_id": "kaez", "data": {
            "daily_limit_youtube_shorts": 6,
            "daily_limit_tiktok": 3,
            "daily_limit_instagram": 3,
            "daily_limit_facebook": 3,
            "youtube_plan": 6, "youtube_max": 6,
            "resto_plan": 2, "resto_max": 3,
            "spacing_hours": 2, "new_account_daily": 1,
            "new_account_daily_youtube": 6,
            "min_virality": 45, "min_seo": 80}},
    ]
    accounts = [
        {"id": 2, "platform": "youtube", "language": "es",
         "label": "YouTube", "status": "conectada", "verify_ok": 1,
         "tenant_id": "sebastian",
         "first_published_at": now_ms - 2 * 86400000,
         "published_count": 5, "strikes": 0},
        {"id": 5, "platform": "tiktok", "language": "es",
         "label": "TikTok ES", "status": "conectada", "verify_ok": 1,
         "tenant_id": "sebastian",
         "first_published_at": now_ms - 2 * 86400000,
         "published_count": 5, "strikes": 0},
    ]
    running = {"sebastian": True, "kaez": True}
    clips = {
        51: {"id": 51, "title": "clip 51",
             "seo_description": "desc", "publish_blocked": 0,
             "video_r2_key": "clips/x/51.mp4", "language": "es",
             "tenant_id": "sebastian"},
    }

    class FakeD1:
        def __init__(self):
            self.executed = []

        def query(self, sql, params=None):
            if "FROM settings" in sql:
                return [dict(s) for s in settings]
            if sql.strip().upper().startswith(
                    "SELECT IDEMPOTENCY_KEY FROM JOBS"):
                return []
            if "FROM jobs" in sql:
                return [dict(j) for j in jobs]
            if "FROM api_accounts" in sql:
                return [dict(a) for a in accounts]
            if "FROM worker_state" in sql:
                return [{"tenant_id": t, "running": int(v)}
                        for t, v in running.items()]
            if "FROM clips" in sql:
                clip = clips.get(params[0] if params else None)
                return [dict(clip)] if clip else []
            return []

        def execute(self, sql, params=None):
            self.executed.append((sql, list(params or [])))
            return 1

    class FakeBridge:
        def __init__(self):
            self.cycles = 0
            self.telegrams = []

        def run_once(self):
            self.cycles += 1
            return {"ok": True, "ran": True, "processed": 1,
                    "stalled": 0, "autoEnqueued": 0,
                    "kaez": {"ran": True, "processed": 0,
                             "stalled": 0, "autoEnqueued": 0}}

        def notify_telegram(self, message):
            self.telegrams.append(message)
            return {"ok": True}

        def list_jobs(self, limit=100):
            return [dict(j) for j in jobs]

    def fake_http_get(url, headers, timeout):
        if "youtube.com/oembed" in url:
            if "DEAD901" in url:
                return 404, ""
            return 200, '{"title": "ok"}'
        if "tiktok.com/oembed" in url:
            return 200, '{"title": "ok"}'
        return 200, "<html>video</html>"

    return {"d1": FakeD1(), "bridge": FakeBridge(),
            "http_get": fake_http_get, "jobs": jobs}


def main(argv=None):
    p = argparse.ArgumentParser(
        description="Vigilante de publicación: revisa los jobs de "
                    "hoy en D1, reintenta lo atascado, verifica las "
                    "URLs publicadas y crea republicaciones |repubN.")
    p.add_argument("--live", action="store_true",
                   help="Actúa (escribe en D1, dispara el ciclo, "
                        "avisa). Sin --live es dry-run.")
    p.add_argument("--dry-run", action="store_true",
                   help="Solo lee, clasifica y verifica (default).")
    p.add_argument("--tenant", default="",
                   help="Tenant de la sesión del panel "
                        "(default: PANEL_TENANT o sebastian).")
    p.add_argument("--out-dir", default=None,
                   help="Dónde guardar el informe markdown.")
    p.add_argument("--simulate", action="store_true",
                   help="Prueba forzada con fakes (nada real).")
    p.add_argument("--simulate-down", action="store_true",
                   help="Como --simulate pero sin URLs caídas.")
    p.add_argument("--config", default=None,
                   help="Ruta de config.yaml (default: la del repo).")
    args = p.parse_args(argv)
    live = bool(args.live and not args.dry_run)

    try:
        from .config import load_config
        cfg = load_config(args.config) if args.config else load_config()
        wcfg = load_publish_watchdog_config(cfg)
    except Exception as e:  # noqa: BLE001 - config rota = crash
        print(f"[pub-watchdog] config inválida: {e}", file=sys.stderr)
        return 1

    out_dir = args.out_dir or os.path.join(
        os.getcwd(), "runs", "publish-watchdog")

    if args.simulate or args.simulate_down:
        now_ms = int(time.time() * 1000)
        # La simulación corre en ventana de reparación: 14:45 Chile.
        day_key = santiago_day_key(now_ms)
        sim_now = santiago_ms_for(day_key, "14:45")
        fakes = _build_simulate_clients(
            sim_now, day_key, down=not args.simulate_down)
        _log("simulación: fakes, nada real (14:45, tras la franja "
             "12:00-14:00)")
        run(d1=fakes["d1"], bridge=fakes["bridge"],
            http_get=fakes["http_get"], now_ms=sim_now, live=live,
            out_dir=out_dir, cfg=wcfg)
        _log(f"simulación: ciclos disparados="
             f"{fakes['bridge'].cycles}, escrituras D1="
             f"{len(fakes['d1'].executed)}, telegramas="
             f"{len(fakes['bridge'].telegrams)}")
        return 0

    # Clientes reales: D1 (si hay token) + panel (si hay sesión).
    # Sin token D1, el vigilante degrada a modo solo-panel y lo
    # dice en el informe; sin ninguno de los dos, no hay datos.
    d1 = None
    try:
        from .d1_client import D1Client
        d1 = D1Client.from_env()
    except Exception as e:  # noqa: BLE001 - sin D1, modo panel
        _log(f"sin cliente D1 ({e}): modo solo-panel")
    bridge = None
    try:
        from .panel_bridge import PanelBridge
        tenant = (args.tenant or os.environ.get("PANEL_TENANT")
                  or "sebastian")
        os.environ["PANEL_TENANT"] = tenant
        bridge = PanelBridge.from_env()
    except Exception as e:  # noqa: BLE001 - sin panel, modo D1
        _log(f"sin puente al panel ({e}): modo solo-D1")
    if d1 is None and bridge is None:
        print("[pub-watchdog] sin token D1 (CLOUDFLARE_API_TOKEN) "
              "ni credenciales del panel (PANEL_USERNAME / "
              "PANEL_PASSWORD): no hay datos que revisar",
              file=sys.stderr)
        return 1
    if d1 is None:
        _log("sin CLOUDFLARE_API_TOKEN: modo solo-panel (solo "
             "sebastian; sin republicaciones)")
    run(d1=d1, bridge=bridge, live=live, out_dir=out_dir, cfg=wcfg)
    return 0


if __name__ == "__main__":
    sys.exit(main())

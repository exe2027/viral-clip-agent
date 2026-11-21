#!/usr/bin/env python3
"""B3 — colchón de 7 días de jobs: ÚNICA fuente de los topes de ritmo.

Toda la lógica de planificación (buffer_jobs.py) y todos los tests
(test_buffer_jobs.py) importan los topes de AQUÍ. No hay literales de
ritmo duplicados en ningún otro archivo de B3: si un tope cambia, se
cambia aquí y solo aquí.

Origen de cada número (verificado 2026-11-20, no inventado):
- Planes/máximos por plataforma, espaciamiento, rolling 24 h, días por
  semana y calentamiento: defaults de Ajustes del panel en
  `independencia/panel-backend/worker/src/db.ts:404-460` (TikTok plan
  2/máx 3, Instagram 1/2, YouTube 1/2, Facebook 1/2; spacing 3 h,
  4 h YouTube; rolling_24h_max 3; calentamiento 1/día hasta
  "establecida" >=14 días Y >=20 publicaciones).
- Franjas Chile 12-14 / 18-20 / 21-23 y mismo clip entre plataformas
  mínimo 15 min: `gate.ts:231-236` y `db.ts:442-443`, y
  `reglas-publicacion-viral-clip-agent.md` §§2-4.
- Puertas de calidad (viralidad/SEO): los AJUSTES VIVOS en D1 para
  sebastian son min_virality=45, min_seo=80 (JOB-CREATION §7,
  verificado en vivo 20-11). OJO: los defaults de db.ts son 50/90;
  el panel usa los de D1, así que el colchón usa los de D1. Si los
  Ajustes cambian en D1, el modo --execute los relee y manda el
  valor vivo; estos dos números aquí son solo el fallback del
  dry-run sobre snapshot local.
- Día libre por cuenta: plan_2100.json (auditoría 20-11): YT ES libre
  viernes, TikTok EN libre sábado, YT EN libre domingo. Las otras
  tres cuentas rotan lun/mar/mié (rotación propuesta, no verificada
  en D1: el execute la ignora si D1 dice otra cosa — ver
  buffer_jobs.py, que no programa en día libre solo según esto).
- Cuentas: JOB-CREATION §7 (verificado en vivo 20-11): meta ES (1),
  YouTube ES (2), YouTube EN (3), TikTok EN (4), TikTok ES (5).
  NO existe cuenta meta EN: ningún clip EN tiene jamás IG/FB.
- Todas las cuentas tienen 2-4 días y <20 publicaciones (auditoría
  20-11): TODAS están en calentamiento -> tope 1/día, ni a mano se
  supera (gate.ts:222-224).

Tope propio del colchón (encargo B3, no del gate): BUFFER_MAX_PER_DAY
= 2. El colchón nunca programa más de 2 publicaciones por cuenta y
día aunque el techo de la plataforma lo permita (TikTok máx 3): el
colchón es reserva, no acelerón. Con calentamiento vigente el tope
efectivo es 1/día de todos modos.
"""

# --- Horizonte del colchón -------------------------------------------------
HORIZON_DAYS = 7  # días de jobs ya creados + clips aprobados en stock

# --- Puertas de calidad (ajustes vivos D1; ver docstring) ------------------
MIN_VIRALITY = 45
MIN_SEO = 80

# --- Ritmo por plataforma: (plan/día, máximo duro/día) --------------------
# TikTok 2/3, Instagram 1/2, YouTube 1/2, Facebook 1/2 (db.ts:413-421).
PLATFORM_PLAN = {
    "tiktok": 2,
    "instagram_reels": 1,
    "youtube_shorts": 1,
    "facebook_reels": 1,
}
PLATFORM_MAX = {
    "tiktok": 3,
    "instagram_reels": 2,
    "youtube_shorts": 2,
    "facebook_reels": 2,
}

# --- Tope propio del colchón (nunca más de esto por cuenta y día) ---------
BUFFER_MAX_PER_DAY = 2

# --- Calentamiento: cuenta nueva = 1/día en TODAS las plataformas ---------
WARMUP_DAILY = 1
WARMUP_MIN_DAYS = 14
WARMUP_MIN_POSTS = 20

# --- Espaciamiento ----------------------------------------------------------
SPACING_HOURS = 3            # misma cuenta, mismo día (general)
YOUTUBE_SPACING_HOURS = 4    # misma cuenta, mismo día (YouTube)
MIN_SPACING_MINUTES = 5      # mínimo absoluto entre dos vídeos
CROSS_PLATFORM_MINUTES = 15  # mismo clip en distintas plataformas

# --- Rolling 24 h: nunca más de 3 por cuenta en 24 h móviles -------------
ROLLING_24H_MAX = 3

# --- Franjas Chile (America/Santiago): (hora inicio, hora fin) -------------
SLOTS = [(12, 14), (18, 20), (21, 23)]

# --- Día libre semanal por cuenta (weekday(): lun=0 .. dom=6) --------------
# YT ES vie (4), TikTok EN sáb (5), YT EN dom (6): plan_2100.json.
# TikTok ES lun (0), IG ES mar (1), FB ES mié (2): rotación propuesta.
REST_WEEKDAY = {
    "youtube_es": 4,
    "tiktok_en": 5,
    "youtube_en": 6,
    "tiktok_es": 0,
    "instagram_es": 1,
    "facebook_es": 2,
}

# --- Cuentas (id de api_accounts en D1; verificado 20-11) ------------------
# key, plataforma, account_id, idioma, en calentamiento.
ACCOUNTS = [
    {"key": "youtube_es", "platform": "youtube_shorts", "account_id": 2,
     "language": "es", "warmup": True},
    {"key": "tiktok_es", "platform": "tiktok", "account_id": 5,
     "language": "es", "warmup": True},
    {"key": "instagram_es", "platform": "instagram_reels", "account_id": 1,
     "language": "es", "warmup": True},
    {"key": "facebook_es", "platform": "facebook_reels", "account_id": 1,
     "language": "es", "warmup": True},
    {"key": "youtube_en", "platform": "youtube_shorts", "account_id": 3,
     "language": "en", "warmup": True},
    {"key": "tiktok_en", "platform": "tiktok", "account_id": 4,
     "language": "en", "warmup": True},
]

ACCOUNT_BY_KEY = {a["key"]: a for a in ACCOUNTS}
ACCOUNT_BY_PLATFORM_LANG = {
    (a["platform"], a["language"]): a for a in ACCOUNTS}

# Nombre de cuenta del plan_2100.json -> key.
PLAN_ACCOUNT_NAMES = {
    "YouTube ES": "youtube_es",
    "TikTok ES": "tiktok_es",
    "Instagram ES": "instagram_es",
    "Facebook ES": "facebook_es",
    "YouTube EN": "youtube_en",
    "TikTok EN": "tiktok_en",
}


def daily_cap(account):
    """Tope efectivo de publicaciones/día para una cuenta.

    Calentamiento manda sobre todo (1/día, ni a mano). Sin
    calentamiento: el plan de la plataforma, con el techo propio
    del colchón (máx 2/día) por encima.
    """
    if account["warmup"]:
        return WARMUP_DAILY
    return min(BUFFER_MAX_PER_DAY, PLATFORM_PLAN[account["platform"]])


def spacing_hours(platform):
    """Espaciamiento mínimo (horas) entre dos subidas de la cuenta."""
    return (YOUTUBE_SPACING_HOURS if platform == "youtube_shorts"
            else SPACING_HOURS)

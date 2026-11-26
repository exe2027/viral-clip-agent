#!/usr/bin/env python3
"""Reproducción R7.4 — ¿por qué un post ES llegó al bucle EN de
engagement? (spec títulos-verdad 2026-11-25, R7.4/I6).

PROCEDIMIENTO SOLO: este script NO se ejecuta en este entorno
(sin sesión de panel disponible aquí). Lo corre el coordinador
con la sesión del panel que las corridas live ya usan:

    python -m clipagent.repro_zernio_accounts --tenant sebastian

Bootstrap igual que cerebro-live.yml: PANEL_BRAIN_TOKEN (token de
un solo uso que se canjea por una sesión de 12 h) o
PANEL_USERNAME/PANEL_PASSWORD + PANEL_TENANT (ver
PanelBridge.from_env). Sin secret nuevo, sin clave pegada en
ningún sitio.

Qué hace, por idioma (ES, EN):
  1. Resuelve la clave Zernio desde la sesión
     (GET /api/bridge/credentials -> zernio.{es,en}.api_key, vía
     PanelBridge.brain_credentials()).
  2. Llama a GET https://zernio.com/api/v1/accounts con esa clave
     (la misma llamada que zernioListAccounts en
     worker/src/publish/zernio-engagement.ts:266) y registra qué
     cuentas lista cada clave (id, plataforma, username) y cuál
     elige byPlatform primero por plataforma.
  3. H2 (inbox): GET /api/v1/inbox/comments?platform=tiktok con
     cada clave y comprueba en las filas si `accountId` falta o
     trae la otra cuenta (el filtro del Worker,
     rows.filter(r => r.accountId === acc.id), supone que cada
     fila trae la cuenta dueña).
  4. H3 (ownNames): compara los ownNames de cada clave contra
     ambos handles de TikTok (--handles, por defecto los de
     sebastian: @elclipdegta6____bits y @elclipdegta6en___).

Las claves NUNCA se imprimen ni se registran: solo se registra
su presencia/ausencia y los resúmenes de cuentas (id, plataforma,
username). La salida es un JSON con H1/H2/H3 por idioma; con
--json-out <ruta> se escribe ahí además de stdout.

Fallback manual (misma llamada, sin script):
  curl -s -H "Authorization: Bearer <clave>" \\
      https://zernio.com/api/v1/accounts
una vez con la clave ES y una con la EN, claves tomadas de la
misma llamada a bridge/credentials (nunca en chat, nunca en
disco).

Interpretación (spec R7.4):
  - Si se confirma H1 (una clave lista las dos cuentas TikTok y
    byPlatform elige la primera en los dos bucles) -> la
    corrección de R7 resuelve la cuenta por (plataforma + handle
    del idioma del bucle), nunca primera-por-plataforma.
  - Si `accountId` falta en el inbox, el filtro deja cero filas
    (cero respuestas, no una respuesta equivocada): solo la rama
    "trae la otra cuenta" explica el pantallazo del 213.
  - Si no se reproduce ninguna hipótesis -> R7 se implementa
    acotada a "solo idioma" y el riesgo de ruteo queda como
    limitación conocida en §11 de la spec.
"""
import argparse
import json
import sys
import urllib.error
import urllib.request

API = "https://zernio.com/api/v1"
PLATFORMS = ("tiktok", "instagram", "youtube", "facebook")
DEFAULT_HANDLES = ("elclipdegta6____bits", "elclipdegta6en___")


# -- Funciones puras (testeables sin red ni sesión) -----------------

def summarize_accounts(accounts):
    """[{id, platform, username}] de la respuesta de /accounts.

    Solo esos tres campos: nunca se registra nada más de la
    respuesta (y jamás una clave). Acepta el shape del Worker
    (_id o id; username o accountUsername) y una lista directa.
    """
    rows = []
    if isinstance(accounts, dict):
        accounts = accounts.get("accounts") or []
    for a in accounts or []:
        if not isinstance(a, dict):
            continue
        acc_id = a.get("_id") or a.get("id") or ""
        platform = a.get("platform") or ""
        username = a.get("username") or a.get("accountUsername") or ""
        if acc_id and platform:
            rows.append({"id": str(acc_id), "platform": str(platform),
                         "username": str(username)})
    return rows


def choose_by_platform(accounts, platform):
    """Primera cuenta por plataforma (byPlatform del Worker,
    zernio-engagement.ts:436-437). None si no hay ninguna."""
    for a in summarize_accounts(accounts):
        if a["platform"] == platform:
            return a
    return None


def own_names(accounts):
    """ownNames del Worker (l.438): usernames sin @, en minúsculas."""
    return sorted({(a.get("username") or "").lstrip("@").lower()
                   for a in summarize_accounts(accounts)} - {""})


def check_h2(inbox_rows, account_id):
    """H2 sobre las filas del inbox de una clave.

    Devuelve {total, sin_account_id, de_otra_cuenta, de_esta_cuenta}:
    `sin_account_id` = filas sin accountId (el filtro las deja
    fuera: cero respuestas, no una respuesta equivocada);
    `de_otra_cuenta` = filas con accountId distinto (la única rama
    que explica el pantallazo del 213).
    """
    total = 0
    missing = 0
    other = []
    mine = 0
    for row in inbox_rows or []:
        if not isinstance(row, dict):
            continue
        total += 1
        acc = row.get("accountId") or ""
        if not acc:
            missing += 1
        elif acc == account_id:
            mine += 1
        else:
            other.append({"row_id": str(row.get("id") or ""),
                          "accountId": str(acc)})
    return {"total": total, "sin_account_id": missing,
            "de_otra_cuenta": other, "de_esta_cuenta": mine}


def check_h3(names, handles):
    """H3: ¿qué handles reconoce ownNames? {handle: bool}."""
    known = {n.lower().lstrip("@") for n in names}
    return {h.lstrip("@"): (h.lstrip("@").lower() in known)
            for h in handles}


# -- Red (solo aquí; las claves nunca se registran) -----------------

def _zernio_get(api_key, path):
    """GET a Zernio con Bearer. Devuelve el JSON. Nunca registra
    la clave ni la incluye en el mensaje de error."""
    req = urllib.request.Request(
        API + path, method="GET",
        headers={"Authorization": f"Bearer {api_key}",
                 "Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            return json.loads(resp.read().decode("utf-8", "ignore"))
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"Zernio GET {path} -> HTTP {e.code}")
    except urllib.error.URLError as e:
        raise RuntimeError(f"Zernio GET {path} -> {e}")


def reproduce(tenant, handles, bridge=None):
    """H1/H2/H3 por idioma. Devuelve el informe (dict JSON).

    `bridge`: PanelBridge (inyectable en tests); sin él, se
    resuelve del entorno (PANEL_BRAIN_TOKEN, como cerebro-live).
    """
    from .panel_bridge import PanelBridge
    if bridge is None:
        bridge = PanelBridge.from_env()
    if bridge is None:
        raise RuntimeError(
            "sin sesión de panel: exporta PANEL_BRAIN_TOKEN (o "
            "PANEL_USERNAME/PANEL_PASSWORD + PANEL_TENANT)")
    if tenant:
        from .tenant import require_tenant
        bridge.tenant = require_tenant(tenant)
    creds = bridge.brain_credentials() or {}
    zernio = creds.get("zernio") or {}
    report = {"tenant": bridge.tenant or tenant, "por_idioma": {}}
    for lang in ("es", "en"):
        entry = {"clave_presente": False, "cuentas": [],
                 "by_platform": {}, "h1_reproducida": False,
                 "h2": {}, "h3": {}}
        report["por_idioma"][lang] = entry
        key = (zernio.get(lang) or {}).get("api_key") or ""
        if not key:
            entry["error"] = f"sin clave zernio.{lang} en el panel"
            continue
        entry["clave_presente"] = True
        try:
            raw = _zernio_get(key, "/accounts")
        except Exception as e:  # noqa: BLE001 - se registra y se sigue
            entry["error"] = str(e)
            continue
        accounts = summarize_accounts(raw)
        entry["cuentas"] = accounts
        entry["by_platform"] = {
            p: choose_by_platform(accounts, p) for p in PLATFORMS}
        tiktok = [a for a in accounts if a["platform"] == "tiktok"]
        entry["h1_reproducida"] = len(tiktok) > 1
        names = own_names(accounts)
        entry["own_names"] = names
        entry["h3"] = check_h3(names, handles)
        acc = entry["by_platform"].get("tiktok") or {}
        try:
            inbox = _zernio_get(key, "/inbox/comments?platform=tiktok")
            rows = (inbox.get("comments") if isinstance(inbox, dict)
                    else inbox)
            entry["h2"] = check_h2(rows, acc.get("id") or "")
        except Exception as e:  # noqa: BLE001 - se registra y se sigue
            entry["h2"] = {"error": str(e)}
        # La clave sale del ámbito sin haberse registrado jamás.
        key = ""
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Reproducción R7.4: qué cuentas lista cada "
                    "clave Zernio (ES/EN) y cuál elige byPlatform.")
    parser.add_argument("--tenant", default="sebastian",
                        help="tenant del panel (default: sebastian)")
    parser.add_argument("--handles", nargs="*", default=list(DEFAULT_HANDLES),
                        help="handles TikTok para H3 (sin @ o con @)")
    parser.add_argument("--json-out", default="",
                        help="ruta donde escribir el informe JSON")
    args = parser.parse_args(argv)
    report = reproduce(args.tenant, args.handles)
    text = json.dumps(report, ensure_ascii=False, indent=2)
    if args.json_out:
        with open(args.json_out, "w", encoding="utf-8") as f:
            f.write(text)
    print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())

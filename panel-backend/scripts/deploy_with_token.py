#!/usr/bin/env python3
"""Despliega el panel AVC usando el token Cloudflare guardado.

Obtiene un surrogate hsurr: de authd para custom.cloudflare y lo
pasa como CLOUDFLARE_API_TOKEN a wrangler. El surrogate NUNCA se
imprime: Sentinel lo reemplaza por el token real solo en las
salidas a api.cloudflare.com. No guardar el token en ningún archivo.

Uso:
  python3 scripts/deploy_with_token.py [migrate|deploy-worker|deploy-pages|all]
  python3 scripts/deploy_with_token.py [migrate-<tenant>|deploy-worker-<tenant>]
      <tenant> = sebastian | kaez (Worker por usuario, un solo tenant:
      aplica las migraciones a su D1 y despliega con su config
      wrangler.<tenant>.toml). El modo por defecto sigue siendo `all`
      con wrangler.toml del `avc` viejo, usable hasta el corte.
  python3 scripts/deploy_with_token.py enable-cron-<tenant>
      Enciende el cron de un Worker ya desplegado, solo tras verificar
      salud, worker_running=false y los 6 secretos (A5).

A5 (2026-11-20, despliegues y migraciones seguros):
  - Antes de aplicar migraciones, corre la puerta de presupuesto
    (scripts/check-migration-budget.py): si lo pendiente supera el
    20% de la cuota restante del día, o no se puede contar, NO se
    aplica nada ni se despliega (fail closed).
  - Un Worker NUEVO se despliega SIEMPRE SIN cron: si el script del
    Worker aún no existe en la cuenta, el despliegue usa una copia
    temporal del toml sin [triggers] y avisa que hay que encenderlo
    con enable-cron-<tenant> tras verificar. Un Worker existente se
    despliega con su toml tal cual (nunca se le quita el cron por
    accidente: no está verificado que wrangler elimine triggers al
    desplegar sin ellos, así que no se usa ese camino).
"""
import json, os, re, subprocess, sys, tempfile, urllib.request
sys.path.insert(0, "/opt/muse/skills/custom-connector-setup/bin")
from dynamic_credentials import dynamic_credential_entry

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WRANGLER = os.path.join(ROOT, "worker", "node_modules", ".bin", "wrangler")
CF_HELPER = os.path.expanduser(
    "~/workspace/custom-connectors/custom.cloudflare/bin/cloudflare.py")
ACCOUNT_ID = "27afc37225a02fd92557f901c05dabee"
CHECK_SCRIPT = os.path.join(ROOT, "scripts", "check-migration-budget.py")
MIGRATIONS_DIR = os.path.join(ROOT, "migrations")
REQUIRED_SECRETS = ["SESSION_SECRET", "CREDENTIAL_ENCRYPTION_KEY",
                    "GITHUB_TOKEN", "TELEGRAM_BOT_TOKEN",
                    "TELEGRAM_WEBHOOK_SECRET", "BRIDGE_TOKEN"]

# Worker por usuario (2026-11-19): config y D1 propios por tenant.
TENANT_CONFIGS = {
    "sebastian": {"config": "wrangler.sebastian.toml", "db": "clips-avc-sebastian"},
    "kaez": {"config": "wrangler.kaez.toml", "db": "clips-avc-kaez"},
}

# Cron por franjas de publicación (2026-11-21, ahorro D1): el Worker solo
# despierta en las franjas hora Chile 12-14, 18-20, 21-23 (UTC 15-18 y
# 21-23,0-3, con 1 h de margen por el cambio DST -03/-04). La puerta
# (gate.ts) filtra la franja exacta en hora Chile. Fuera de franja el
# Worker ni despierta, así que no gasta escrituras D1.
SLOT_CRONS = ["*/5 15-18 * * *", "*/5 21-23,0-3 * * *"]


def run(cmd, cwd, env):
    print(f"$ {' '.join(cmd)}", flush=True)
    r = subprocess.run(cmd, cwd=cwd, env=env)
    if r.returncode != 0:
        raise SystemExit(r.returncode)


def cf_api(path):
    """GET a la API de Cloudflare por el helper (solo lectura)."""
    r = subprocess.run(["python3", CF_HELPER, "get", path],
                       capture_output=True, text=True, timeout=60)
    if r.returncode != 0:
        raise RuntimeError(f"cloudflare.py get {path} rc={r.returncode}: {r.stderr[:300]}")
    payload = json.loads(r.stdout)
    if not payload.get("success"):
        raise RuntimeError(f"API Cloudflare: {json.dumps(payload.get('errors'))[:400]}")
    return payload["result"]


def worker_exists(name):
    scripts = cf_api(f"/accounts/{ACCOUNT_ID}/workers/scripts")
    return any(s.get("id") == name for s in scripts)


def worker_crons(name):
    res = cf_api(f"/accounts/{ACCOUNT_ID}/workers/scripts/{name}/schedules")
    return [s.get("cron") for s in res.get("schedules", [])]


def toml_has_triggers(text):
    return bool(re.search(r"^\[triggers\]", text, re.M))


def strip_triggers(text):
    """Quita el bloque [triggers] de un wrangler.toml y deja constancia
    de que el Worker nace apagado (A5). Idempotente."""
    if not toml_has_triggers(text):
        return text
    comment = ("# Cron APAGADO (A5, 2026-11-20): el Worker nuevo se despliega\n"
               "# sin cron y con worker_running=false. Se enciende solo con\n"
               "# deploy_with_token.py enable-cron-<tenant>, tras verificar\n"
               "# salud (/api/health), la D1 y los 6 secretos.\n")
    out, n = re.subn(
        r"^(?:# Cron del Worker:.*\n(?:#.*\n)*)?\[triggers\]\ncrons = \[[^\]]*\]\n?",
        comment, text, count=1, flags=re.M)
    if n == 0 or toml_has_triggers(out):
        # Forma inesperada del bloque: quitar línea a línea y dejar
        # el comentario donde estaba [triggers].
        lines, done = [], False
        for ln in text.splitlines(keepends=True):
            if ln.startswith("[triggers]") and not done:
                lines.append(comment)
                done = True
                continue
            if done and ln.startswith("crons ="):
                done = "crons"
                continue
            if done == "crons" and ln.strip() == "":
                done = True
                continue
            lines.append(ln)
        out = "".join(lines)
    if toml_has_triggers(out):
        raise RuntimeError("no se pudo quitar [triggers] del toml")
    return out


def read_database_id(toml_path):
    text = open(toml_path, encoding="utf-8").read()
    m = re.search(r'^database_id = "([^"]+)"', text, re.M)
    if not m:
        raise SystemExit(f"no se pudo leer database_id de {toml_path}")
    return m.group(1)


def run_budget_gate(db_uuid, db_name):
    """Puerta de presupuesto A5: rechaza (SystemExit) si lo pendiente
    supera el 20% de la cuota restante o no se puede contar."""
    cmd = [sys.executable, CHECK_SCRIPT, "--migrations-dir", MIGRATIONS_DIR,
           "--d1", db_uuid]
    print(f"$ {' '.join(cmd)}", flush=True)
    r = subprocess.run(cmd)
    if r.returncode != 0:
        raise SystemExit(
            f"puerta de presupuesto (A5): el chequeo de {db_name} salió con "
            f"código {r.returncode}; no se aplican migraciones ni se despliega")


def deploy_worker_config(config_path, env, nocron=False):
    """Despliega el Worker con su toml. Si nocron, usa una copia
    temporal sin [triggers] (Worker nuevo: nace apagado)."""
    if not nocron:
        run([WRANGLER, "deploy", "--config", config_path],
            os.path.join(ROOT, "worker"), env)
        return
    text = open(os.path.join(ROOT, config_path), encoding="utf-8").read()
    stripped = strip_triggers(text)
    fd, tmp = tempfile.mkstemp(prefix="wrangler.nocron.", suffix=".toml",
                               dir=ROOT)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(stripped)
    try:
        print("A5: Worker nuevo: se despliega SIN cron "
              f"(temporal {os.path.basename(tmp)}; el toml original no se toca)")
        run([WRANGLER, "deploy", "--config",
             os.path.join("..", os.path.basename(tmp))],
            os.path.join(ROOT, "worker"), env)
    finally:
        os.unlink(tmp)


def worker_url(tenant):
    """URL del Worker por workers.json (sin defaults silenciosos)."""
    path = os.path.join(ROOT, "frontend", "dist", "workers.json")
    try:
        data = json.load(open(path, encoding="utf-8"))
    except Exception as e:
        raise SystemExit(f"no se pudo leer workers.json: {e}")
    for user, url in data.items():
        if url.startswith(f"https://avc-{tenant}."):
            return url
    raise SystemExit(f"workers.json no tiene URL para avc-{tenant}; "
                     "el tenant no está en el routing")


def health_worker_running_false(url, tenant):
    """GET /api/health (sin auth): exige app=avc, tenant correcto y
    worker_running=false. Devuelve True si pasa, si no SystemExit."""
    req = urllib.request.Request(url.rstrip("/") + "/api/health", method="GET")
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            body = json.loads(resp.read().decode())
    except Exception as e:
        raise SystemExit(f"/api/health de {url} no responde: {e}")
    if body.get("app") != "avc" or body.get("tenant") != tenant:
        raise SystemExit(f"/api/health no es app=avc tenant={tenant}: {body}")
    if body.get("worker_running") is not False:
        raise SystemExit(
            f"/api/health dice worker_running={body.get('worker_running')}: "
            "un Worker nuevo nace parado; no se enciende el cron hasta aclararlo")
    return True


def secrets_present(config_rel, env):
    r = subprocess.run(
        [WRANGLER, "secret", "list", "--config", config_rel, "--format", "json"],
        cwd=os.path.join(ROOT, "worker"), env=env,
        capture_output=True, text=True, timeout=60)
    if r.returncode != 0:
        raise SystemExit(f"wrangler secret list falló: {r.stderr[:300]}")
    names = {s["name"] for s in json.loads(r.stdout or "[]")}
    missing = [s for s in REQUIRED_SECRETS if s not in names]
    if missing:
        raise SystemExit(f"faltan secretos en el Worker: {', '.join(missing)}")
    return True


def enable_cron(tenant, env):
    """Enciende el cron de un Worker ya desplegado, solo tras verificar
    salud, worker_running=false, secretos y presupuesto (A5)."""
    if tenant not in TENANT_CONFIGS:
        print(f"Tenant desconocido: {tenant} (usa sebastian o kaez)", file=sys.stderr)
        raise SystemExit(2)
    cfg = TENANT_CONFIGS[tenant]
    name = f"avc-{tenant}"
    print(f"A5 enable-cron: verificando {name} antes de encender el cron")
    if not worker_exists(name):
        raise SystemExit(f"el Worker {name} no existe en la cuenta; "
                         "primero despliega (nace sin cron)")
    if worker_crons(name):
        print(f"el Worker {name} ya tiene cron {worker_crons(name)}: nada que hacer")
        return
    url = worker_url(tenant)
    health_worker_running_false(url, tenant)
    print("  salud OK, worker_running=false")
    secrets_present(os.path.join("..", cfg["config"]), env)
    print("  6 secretos presentes")
    db_uuid = read_database_id(os.path.join(ROOT, cfg["config"]))
    run_budget_gate(db_uuid, cfg["db"])
    run([WRANGLER, "d1", "migrations", "apply", cfg["db"],
         "--config", os.path.join("..", cfg["config"]), "--remote"],
        os.path.join(ROOT, "worker"), env)
    # Añadir [triggers] al toml del tenant (idempotente) y redesplegar.
    path = os.path.join(ROOT, cfg["config"])
    text = open(path, encoding="utf-8").read()
    if not toml_has_triggers(text):
        anchor = text.find("[[d1_databases]]")
        if anchor == -1:
            raise SystemExit(f"no hay [[d1_databases]] en {path} para anclar [triggers]")
        block = ('# Cron (A5): APAGADO en el primer despliegue; encendido con\n'
                 '# enable-cron tras verificar salud, D1 y secretos.\n'
                 '# SOLO en franjas de publicación (2026-11-21, ahorro D1).\n'
                 '[triggers]\ncrons = ["*/5 15-18 * * *", "*/5 21-23,0-3 * * *"]\n\n')
        open(path, "w", encoding="utf-8").write(text[:anchor] + block + text[anchor:])
        print(f"  [triggers] añadido a {cfg['config']}")
    run([WRANGLER, "deploy", "--config", os.path.join("..", cfg["config"])],
        os.path.join(ROOT, "worker"), env)
    crons = worker_crons(name)
    if not all(c in crons for c in SLOT_CRONS):
        raise SystemExit(f"tras el despliegue la API no lista el cron en {name} "
                         f"(llegó: {crons}); revisa a mano")
    print(f"OK enable-cron: {name} tiene cron por franjas {SLOT_CRONS} (verificado por API)")
    print("  El Worker sigue PARADO (worker_running=false): nada corre hasta "
          "que el usuario pulse Iniciar en el panel.")


def main():
    mode = sys.argv[1] if len(sys.argv) > 1 else "all"
    entry = dynamic_credential_entry("custom.cloudflare", "access_token")
    env = dict(os.environ)
    env["CLOUDFLARE_API_TOKEN"] = entry["surrogate"]
    env["CLOUDFLARE_ACCOUNT_ID"] = ACCOUNT_ID
    if mode.startswith("enable-cron-"):
        enable_cron(mode[len("enable-cron-"):], env)
        print("OK deploy_with_token:", mode)
        return
    # Modos por tenant: migrate-<tenant> y deploy-worker-<tenant>
    # (deploy-worker-<tenant> = migraciones + despliegue de ese Worker).
    tenant = None
    for prefix in ("deploy-worker-", "migrate-"):
        if mode.startswith(prefix):
            tenant = mode[len(prefix):]
            break
    if tenant is not None:
        if tenant not in TENANT_CONFIGS:
            print(f"Tenant desconocido: {tenant} (usa sebastian o kaez)", file=sys.stderr)
            raise SystemExit(2)
        cfg = TENANT_CONFIGS[tenant]
        config = os.path.join("..", cfg["config"])
        # A5: puerta de presupuesto ANTES de aplicar migraciones.
        db_uuid = read_database_id(os.path.join(ROOT, cfg["config"]))
        run_budget_gate(db_uuid, cfg["db"])
        run([WRANGLER, "d1", "migrations", "apply", cfg["db"],
             "--config", config, "--remote"],
            os.path.join(ROOT, "worker"), env)
        if mode.startswith("deploy-worker-"):
            # A5: Worker nuevo -> sin cron; existente -> con su toml.
            is_new = not worker_exists(f"avc-{tenant}")
            deploy_worker_config(config, env, nocron=is_new)
            if is_new:
                print(f"A5: Worker avc-{tenant} NUEVO desplegado SIN cron "
                      "(apagado). Para encenderlo tras verificar salud, D1 "
                      "y secretos: python3 scripts/deploy_with_token.py "
                      f"enable-cron-{tenant}")
        print("OK deploy_with_token:", mode)
        return
    if mode in ("migrate", "all"):
        # A5: puerta de presupuesto también para la D1 compartida vieja.
        db_uuid = read_database_id(os.path.join(ROOT, "wrangler.toml"))
        run_budget_gate(db_uuid, "clips-avc")
        run([WRANGLER, "d1", "migrations", "apply", "clips-avc",
             "--config", "../wrangler.toml", "--remote"],
            os.path.join(ROOT, "worker"), env)
    if mode in ("deploy-worker", "all"):
        run([WRANGLER, "deploy", "--config", "../wrangler.toml"],
            os.path.join(ROOT, "worker"), env)
    if mode in ("deploy-pages", "all"):
        run([WRANGLER, "pages", "deploy", "frontend/dist",
             "--project-name", "avc"],
            ROOT, env)
    print("OK deploy_with_token:", mode)

if __name__ == "__main__":
    main()

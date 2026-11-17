"""Asistente de conexión de plataformas (Fase 3.3).

`clipagent setup` revisa por plataforma qué falta, muestra los
pasos EXACTOS y los enlaces a las consolas oficiales, marca lo que
SOLO el usuario puede hacer a mano una vez (crear cuenta/página,
aprobar app, aceptar permisos en el navegador), lanza el flujo
OAuth local cuando corresponde (servidor en localhost para el
callback) y guarda el token en tokens/. Termina con una prueba
real de cada conexión (`doctor`, ver 3.5).

Nada de lo que imprime contiene secretos: solo nombres de variables
y rutas de archivos.
"""
import json
import os
import threading
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, HTTPServer

from . import accounts as _accounts
from . import credentials as _creds

# Pasos por plataforma. "manual": solo el usuario puede hacerlo
# (navegador, consola oficial). "auto": lo hace este asistente.
# Los enlaces son las consolas oficiales (verificados 2026-11-17).
PLATFORMS = {
    "youtube_shorts": {
        "label": "YouTube Shorts",
        "console_url": "https://console.cloud.google.com/apis/library/youtube.googleapis.com",
        "docs_url": "https://developers.google.com/youtube/v3/getting-started",
        "steps": [
            ("manual", "Crea un proyecto en Google Cloud y habilita la YouTube Data API v3",
             "https://console.cloud.google.com/apis/library/youtube.googleapis.com"),
            ("manual", "En Credenciales, crea un ID de cliente OAuth de tipo 'Aplicación de escritorio' y descarga el JSON como client_secrets.json en la raíz del repo",
             "https://console.cloud.google.com/apis/credentials"),
            ("manual", "Verifica el canal en YouTube Studio (teléfono): sin canal verificado no hay miniaturas personalizadas",
             "https://studio.youtube.com/channel/UC/features"),
            ("auto", "Flujo OAuth local: se abre el navegador, aceptas el scope youtube.upload y el token se guarda en tokens/youtube_shorts.json",
             None),
            ("auto", "Prueba real de la conexión (doctor): lista tu canal con la API",
             None),
        ],
    },
    "tiktok": {
        "label": "TikTok",
        "console_url": "https://developers.tiktok.com/",
        "docs_url": "https://developers.tiktok.com/doc/content-posting-api-get-started",
        "steps": [
            ("manual", "Crea una app en TikTok for Developers y añade el producto Content Posting API",
             "https://developers.tiktok.com/apps/"),
            ("manual", "Pide los scopes video.publish (Direct Post) y user.info.basic",
             "https://developers.tiktok.com/apps/"),
            ("manual", "Pasa la revisión de la app (días a 2 semanas). Sin auditoría posterior, todo lo que publiques es PRIVADO (SELF_ONLY): eso lo detecta el publicador y cae a borrador, no a error",
             "https://developers.tiktok.com/application/content-posting-api"),
            ("auto", "Flujo OAuth local: aceptas los permisos y el token se guarda en tokens/tiktok.json",
             None),
            ("auto", "Prueba real de la conexión (doctor): lee tu perfil con la API",
             None),
        ],
    },
    "instagram_reels": {
        "label": "Instagram Reels",
        "console_url": "https://developers.facebook.com/apps/",
        "docs_url": "https://developers.facebook.com/docs/instagram-platform/instagram-api-with-instagram-login/",
        "steps": [
            ("manual", "Pasa la cuenta @elclipdegta6 a Profesional (Creador o Empresa) en la app de Instagram",
             None),
            ("manual", "Vincula la cuenta a la Página de Facebook Elclipdegta6 (ya está vinculada: verificarlo en doctor)",
             None),
            ("manual", "Crea una app en Meta for Developers con el producto Instagram API y los permisos instagram_business_content_publish e instagram_business_basic",
             "https://developers.facebook.com/apps/"),
            ("manual", "Pasa la revisión de la app de Meta (App Review) para esos permisos",
             "https://developers.facebook.com/apps/"),
            ("auto", "Flujo OAuth local: aceptas los permisos y el token se guarda en tokens/instagram_reels.json",
             None),
            ("auto", "Prueba real de la conexión (doctor): lee tu cuenta con la Graph API",
             None),
        ],
    },
    "facebook_reels": {
        "label": "Facebook Reels",
        "console_url": "https://developers.facebook.com/apps/",
        "docs_url": "https://developers.facebook.com/docs/video-api/",
        "steps": [
            ("manual", "Confirma que la Página Elclipdegta6 existe y que tienes rol de administrador o editor (Reels solo se publican a Página, nunca a perfil personal)",
             "https://www.facebook.com/pages/"),
            ("manual", "En tu app de Meta, pide los permisos pages_manage_posts, pages_read_engagement y pages_show_list",
             "https://developers.facebook.com/apps/"),
            ("auto", "Flujo OAuth local: aceptas los permisos y el Page access token se guarda en tokens/facebook_reels.json",
             None),
            ("auto", "Prueba real de la conexión (doctor): lista tus Páginas con la Graph API",
             None),
        ],
    },
    "twitch": {
        "label": "Twitch (watcher)",
        "console_url": "https://dev.twitch.tv/console/apps",
        "docs_url": "https://dev.twitch.tv/docs/authentication/getting-tokens-oauth/#client-credentials-grant-flow",
        "steps": [
            ("manual", "Registra una app en dev.twitch.tv y copia su Client ID y Client Secret a .env (TWITCH_CLIENT_ID, TWITCH_CLIENT_SECRET)",
             "https://dev.twitch.tv/console/apps"),
            ("auto", "Token de app (client credentials) automático: se pide a id.twitch.tv y se guarda en tokens/twitch.json",
             None),
            ("auto", "Sin credenciales, el watcher usa GQL como respaldo (no oficial) y lo avisa",
             None),
        ],
    },
    "kick": {
        "label": "Kick (sin API oficial de chat)",
        "console_url": "https://docs.kick.com/",
        "docs_url": "https://docs.kick.com/",
        "steps": [
            ("manual", "Kick NO tiene API oficial de chat: el chat en vivo se lee por el websocket Pusher que usa la propia web de Kick (no oficial, puede romperse sin aviso). La API pública (api.kick.com/public/v1) cubre usuarios, canales y directos, no el historial de chat",
             "https://docs.kick.com/"),
            ("auto", "Nada que conectar para descargar VODs y chat de VODs: eso ya funciona sin credenciales",
             None),
        ],
    },
}


def plan(base_dir=None):
    """Por plataforma: estado calculado + pasos que faltan.

    Devuelve {plataforma: {"label", "status", "steps": [(tipo,
    texto, url)], "console_url", "docs_url"}}. Los pasos "auto"
    solo se ofrecen cuando los "manual" previos ya están hechos
    (p. ej. sin client_secrets.json no se ofrece el OAuth de YT).
    """
    state = _accounts.compute_state(base_dir)
    out = {}
    for platform, spec in PLATFORMS.items():
        steps = list(spec["steps"])
        if platform == "youtube_shorts":
            has_secrets = os.path.isfile(
                _creds.client_secrets_path(base_dir))
            if not has_secrets:
                steps = [s for s in steps if s[0] == "manual"]
        out[platform] = {
            "label": spec["label"],
            "status": state[platform]["status"],
            "steps": steps,
            "console_url": spec["console_url"],
            "docs_url": spec["docs_url"],
        }
    return out


def plan_text(base_dir=None):
    """El plan en texto, listo para mostrar al usuario."""
    lines = ["CONEXIÓN DE PLATAFORMAS — qué falta y cómo hacerlo"]
    for platform, entry in plan(base_dir).items():
        icon = {"green": "🟢", "amber": "🟡", "red": "🔴"}[entry["status"]]
        lines.append(f"\n{icon} {entry['label']} — {entry['status'].upper()}")
        lines.append(f"   Consola: {entry['console_url']}")
        for kind, text, url in entry["steps"]:
            tag = "[A MANO]" if kind == "manual" else "[AUTO]"
            lines.append(f"   {tag} {text}")
            if url:
                lines.append(f"         → {url}")
    return "\n".join(lines)


class _CallbackHandler(BaseHTTPRequestHandler):
    """Captura el ?code= del OAuth en localhost. Solo guarda el
    código en el servidor; no muestra nada sensible."""

    code = None
    error = None

    def do_GET(self):  # noqa: N802 - nombre de http.server
        query = urllib.parse.urlparse(self.path).query
        params = urllib.parse.parse_qs(query)
        if "code" in params:
            type(self).code = params["code"][0]
            body = ("Código recibido. Ya puedes cerrar esta pestaña "
                    "y volver a la terminal.")
        else:
            type(self).error = params.get("error", ["?"])[0]
            body = f"Error de autorización: {type(self).error}."
        self.send_response(200)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.end_headers()
        self.wfile.write(body.encode("utf-8"))

    def log_message(self, *args):  # noqa: D102 - silenciar el log
        pass


def run_local_oauth(auth_url, token_url, client_id, client_secret,
                    scope, redirect_port=0, timeout=300):
    """Flujo OAuth con callback en localhost.

    Abre la URL de autorización (el llamador la muestra o la abre
    en el navegador), espera el código en http://localhost:<puerto>
    y lo canjea por tokens con una POST. Devuelve el dict de tokens
    o None si falla o expira el tiempo. No imprime secretos.
    """
    redirect_uri = None
    server = HTTPServer(("127.0.0.1", redirect_port), _CallbackHandler)
    port = server.server_address[1]
    redirect_uri = f"http://127.0.0.1:{port}/"
    params = {"client_id": client_id, "redirect_uri": redirect_uri,
              "response_type": "code", "scope": scope}
    url = auth_url + "?" + urllib.parse.urlencode(params)
    thread = threading.Thread(target=server.handle_request, daemon=True)
    thread.start()
    print(f"Abre esta URL en el navegador (o se abrirá sola):\n  {url}")
    try:
        import webbrowser
        webbrowser.open(url)
    except Exception:
        pass
    thread.join(timeout=timeout)
    server.server_close()
    code = _CallbackHandler.code
    if not code:
        return None
    body = urllib.parse.urlencode({
        "client_id": client_id, "client_secret": client_secret,
        "code": code, "grant_type": "authorization_code",
        "redirect_uri": redirect_uri}).encode()
    req = urllib.request.Request(token_url, data=body, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.load(resp)
    except Exception as e:
        print(f"Canje del código falló: {e}")
        return None


def run_youtube_oauth(base_dir=None):
    """OAuth de escritorio de YouTube con google-auth-oauthlib.

    Devuelve True si el token quedó guardado en tokens/. Si falta
    la librería o client_secrets.json, dice qué instalar/hacer y
    devuelve False (no inventa el flujo por HTTP a mano).
    """
    base = base_dir or _creds.BASE_DIR
    secrets = _creds.client_secrets_path(base)
    if not os.path.isfile(secrets):
        print("Falta client_secrets.json en la raíz del repo. "
              "Créalo en Google Cloud (ver pasos de arriba) y "
              "vuelve a ejecutar setup.")
        return False
    try:
        from google_auth_oauthlib.flow import InstalledAppFlow
    except ImportError:
        print("Falta google-auth-oauthlib: "
              "pip install google-api-python-client google-auth-oauthlib")
        return False
    from publisher import YouTubePublisher
    flow = InstalledAppFlow.from_client_secrets_file(
        secrets, [YouTubePublisher.SCOPE])
    creds = flow.run_local_server(port=0)
    data = json.loads(creds.to_json())
    _creds.save_token("youtube_shorts", data, base)
    print("Token de YouTube guardado en tokens/youtube_shorts.json.")
    return True


def run(base_dir=None, platforms=None, non_interactive=False,
       input_fn=input, output=print):
    """Asistente de setup. Devuelve 0 si terminó, 1 si queda algo
    que solo el usuario puede hacer a mano."""
    output(plan_text(base_dir))
    if non_interactive:
        output("\nModo no interactivo: no se lanza ningún flujo OAuth. "
               "Ejecuta sin --non-interactive para conectar.")
        return 1
    todo = platforms or list(PLATFORMS)
    for platform in todo:
        entry = plan(base_dir)[platform]
        if entry["status"] == "green":
            output(f"\n{entry['label']}: ya está conectada. Nada que hacer.")
            continue
        if platform == "youtube_shorts":
            if os.path.isfile(_creds.client_secrets_path(base_dir)):
                answer = input_fn(
                    "\n¿Lanzo el flujo OAuth de YouTube ahora? (s/n) ")
                if answer.strip().lower() in ("s", "si", "sí", "y"):
                    run_youtube_oauth(base_dir)
            else:
                output("\nYouTube: completa los pasos [A MANO] de arriba "
                       "(proyecto + client_secrets.json) y vuelve a "
                       "ejecutar setup para el OAuth.")
        elif platform == "twitch":
            output("\nTwitch: el token de app se pide solo al arrancar "
                   "el watcher (client credentials). Sin TWITCH_CLIENT_ID "
                   "en .env usa GQL como respaldo.")
        else:
            output(f"\n{entry['label']}: completa los pasos [A MANO] de "
                   "arriba y vuelve a ejecutar setup para el OAuth.")
    try:
        from .doctor import run_doctor
        output("\n--- Prueba real de cada conexión (doctor) ---")
        run_doctor(base_dir=base_dir, output=output)
    except ImportError:
        output("\n(doctor aún no implementado: ejecútalo cuando exista.)")
    # Verde solo si todo lo pedido quedó conectado; si no, queda
    # trabajo que solo el usuario puede hacer a mano.
    final = plan(base_dir)
    return 0 if all(final[p]["status"] == "green" for p in todo) else 1

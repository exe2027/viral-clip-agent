# SPEC — Diseño de la extensión AVC como la web del panel (v1.4.0)

Fecha: 2026-11-23. Orden del usuario: "ya instale la extension, le falta diseño asi como el diseño de la web de avc" + "continua hasta que este listo todo".

## Objetivo
Que el popup de la extensión (y la página de instalación en /extension/) se vea como la web del panel AVC: tema GTA 6 / Vice City (atardecer neón). Solo diseño; cero cambio de comportamiento.

## Fuente única de verdad (valores literales de tokens-gta6.css)
- Fondo #100a1a, superficie #261a38, input #150d24, borde #7c6aa0.
- Texto #e8eaf0, tenue #b8b3c9, link #f9a8d4.
- Degradado atardecer: linear-gradient(135deg, #6d28d9 0%, #9d174d 52%, #c2410c 100%); hover #7c3aed → #a21caf → #c2410c.
- Franja atardecer: linear-gradient(90deg, #7c3aed 0%, #ec4899 45%, #ff5e00 100%).
- Foco ámbar #fbbf24. Semántica: ok #22c55e/#14532d/#bbf7d0, bad #f87171/#7f1d1d/#fecaca, warn #f59e0b/#451a03/#fde68a.
- Tipografía: 'Barlow Condensed' (Google Fonts, gratis) para títulos/mayúsculas, 'Barlow' para cuerpo; fallbacks 'Arial Narrow', system-ui (sin webfont sigue legible).
- Radios: control 10px, tarjeta 16px. Sombras: tarjeta + glow atardecer. Neones saturados solo decorativos, nunca texto.

## Alcance
1. `extension/popup.css`: reescritura completa con los tokens (fondo oscuro, tarjetas, botones con degradado, foco ámbar, inputs oscuros). Ancho del popup sigue 360px.
2. `extension/popup.html`: cambios mínimos — `<html data-theme="gta6">`, links de Google Fonts, header con marca AVC + franja atardecer, `class="card"` en cada `<section>`, texto de versión v1.4.0.
3. `panel-backend/frontend/dist/extension/index.html` (página de instalación, en disco + deploy, NO versionada: dist/ está en .gitignore): alinear sus estilos con los mismos tokens + versión y links del ZIP a v1.4.0.
4. Versión 1.3.0 → 1.4.0: manifest.json (version + descripción), popup.html (nota), auto-setup.json (las dos copias: extension/ y dist/extension/), validate.sh (assert + etiquetas + asserts node).
5. `auto-setup.json` es solo datos (campañas, reglas, tenants): sin cambios salvo la versión. No se diseña un JSON.

## Restricciones (no negociables)
- Todos los `id` de popup.html se quedan exactamente igual. popup.js solo usa `document.getElementById` y crea `<p class="note">`; ningún cambio de JS ni de comportamiento.
- Sin permisos nuevos en manifest.json, sin secretos, sin tocar background.js/lib.js/worker.js/popup.js.
- El ZIP lleva exactamente los 8 archivos de siempre (manifest, background, lib, worker, popup.html, popup.css, popup.js, auto-setup.json). Se publica `avc-extension-1.4.0.zip` + alias `avc-extension.zip`.
- dist/ está en .gitignore: lo que cambia ahí (install page, ZIPs, auto-setup.json de dist) va solo a disco + deploy de Pages, NO al commit. El commit lleva solo `extension/*` + esta spec — con una excepción: `manifest.json` y `auto-setup.json` están ignorados por la regla `*.json` del .gitignore, así que TAMPOCO se versionan (un clon fresco no puede cargar la extensión desde el repo; el ZIP es el artefacto de distribución).
- La copia instalada del usuario es local: tras el deploy él tiene que recargar la extensión en chrome://extensions (o reinstalar el ZIP) para ver el diseño nuevo. Avisarlo al entregar.

## Verificación (antes de desplegar)
- `bash extension/validate.sh` en verde.
- Render visual del popup con el CSS nuevo (sin errores de contraste evidentes; texto ≥ 4.5:1 según los pares ya verificados en tokens-gta6.css).
- ZIP 1.4.0 con los 8 archivos y popup.css nuevo dentro.
- Deploy Pages + comprobación en vivo: install page dice v1.4.0 y el ZIP descargado trae el popup nuevo.

## Criterio de éxito
El usuario abre el popup y lo reconoce como la web de AVC (violeta noche, atardecer, Barlow Condensed); todo sigue funcionando igual (consultar worker, analíticas, conectar, enviar, tokens, API key, historial).

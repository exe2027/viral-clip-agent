# Evolución neuroevolutiva de clips (migración 0042, v1.4.0)

Fecha: 2026-11-23. Estado: código + migración en el repo; despliegue
y verificación en vivo en la entrega del trabajo.

## 1. La idea (del usuario, 2026-11-23)

El reel de @hieratico.ia: 200 coches que no saben conducir, 27
generaciones, se copia con pequeños errores a los que llegan más
lejos (algoritmo genético sobre una red neuronal de 68 pesos, sin
gradientes). Aplicado a los clips que pagan en Content Rewards:
población de variantes, copiar a las mejores con pequeñas
mutaciones, matar a las perdedoras.

## 2. Doctrina: A/B testing, no caos (decisión del usuario 19:36)

El usuario advirtió que la evolución podía ser contraproducente
para el algoritmo de sus redes si los videos son siempre
diferentes. Por eso el diseño es así, y así queda documentado en
`worker/src/evolution.ts`:

- **Variantes OFFLINE, no se publican.** La población se genera en
  el cerebro y solo sale **1 ganadora por momento/plataforma**,
  por la vía de autopublicación ya existente (puertas 45/80,
  límites por plataforma/cuenta, huecos de Chile, 39/día).
- **Mutaciones PEQUEÑAS, dentro de la misma plantilla rubius**,
  mismo nicho GTA 6 y mismos streamers: inicio/fin (±1-3 s),
  hook, título y texto de miniatura. El estilo de subtítulos NO
  es eje de mutación (queda el del clip base, rubius).
- **Cada variante es un corte distinto REAL**: start o end
  difieren ≥ 1 s del control (`isDistinctCut`). Solo cambiar un
  efecto o el estilo de subtítulos NO hace un clip distinto
  (regla del usuario).
- **Publicar muchas variantes casi iguales sería lo
  contraproducente**: quemaría huecos, parecería duplicado y
  rompería la consistencia de la cuenta. Las perdedoras no se
  publican ni se reenvían a otra campaña (un post = UNA campaña).
- **Población 6 (1 control + 5 mutantes), no 200.** La variante 0
  es siempre el control (línea base sin mutar): es contra lo que
  se mide todo. Cada mutante cambia el corte + como mucho un eje
  de texto, para saber QUÉ ganó.

## 3. Fitness en dos fases (el real manda)

- **(a) Cerebro**: viralidad/SEO del clip base (puertas 45/80
  intactas) + puerta de calidad B4. El control hereda los
  puntajes del clip base; una mutante solo tiene puntaje si el
  cerebro lo aporta en `brain_scores` tras validar su render.
- **(b) Real a 24-48 h**: vistas aprobadas en Content Rewards
  (mandan) y vistas/retención de analíticas, vía
  `POST /api/evolution/record-real-fitness`. Sin datos reales,
  la variante queda `has_real_data=0` y **NUNCA se inventa un
  fitness**: la generación 1 publica la línea base y las mutantes
  quedan como `candidate` (sin probar), NO como perdedoras.
  `loser` exige datos reales o veredicto del validador. Los
  veredictos rechazados del validador NO se guardan sin revisar
  fotogramas (regla existente).
- Selección (`selectWinners`, pura): ordena por fitness real
  desc, luego viralidad del cerebro, luego SEO. Se quedan las 3
  mejores; las elegibles fuera del top son perdedoras; las que no
  pasan 45/80 quedan `blocked` con motivo.

## 4. Tablas (0042)

- `evo_generations`: una por momento (clip base) + número de
  generación. UNIQUE (tenant, base_clip_id, generation).
- `evo_variants`: una por variante, con `params_json` (corte que
  el cerebro usa para renderizar), `mutations_json` (eje +
  detalle + padre), `is_control`, fitness cerebro vs real,
  `has_real_data`, `status` (candidate/winner/loser/published/
  blocked) y `blocked_reason`.
- `evo_fitness`: una fila por medición (source brain/real), con
  virality, seo, approved_views, views, retention y fitness.

## 5. Rutas (sesión del panel, como campaign-submissions)

- `GET /api/evolution/status` — resumen solo lectura para el
  panel (últimas 10 generaciones: ganadora, fitness real vs
  cerebro, próxima mutación, bloqueo).
- `POST /api/evolution/dry-run` — genera + selecciona + persiste.
  Body: `clip_id`, `platform?` (defecto: primera de la campaña),
  `generation?`, `parent_generation_id?`, `brain_scores?`
  (array [{index, virality, seo}], solo el cerebro).
  Valida, en orden: clip existe; streamer con campaña famosa
  (≥ 50.000) y tarifa neta ≥ $1/1k (Dasgasd3m $1,80;
  ArthurTV $1,35; Dylan Page $1,08; Bobby Van Riper fuera por
  fama; BenDaDonnn fuera por piso); plataforma cubierta por la
  campaña; puertas 45/80; calidad B4; **un post = una campaña**
  (409 si el clip+plataforma ya tiene envío a otra campaña o a
  esta). Si la generación ya existe, la devuelve con
  `duplicate: true` (no duplica). Responde con
  `dry_run: true, published_nothing: true`, la ganadora,
  `publishable` y `publish_block_reason`. **NO publica, NO crea
  jobs ni clips, NO envía a ninguna campaña.**
- `POST /api/evolution/record-real-fitness` — body:
  `variant_id` + `approved_views?` / `views?` /
  `retention_pct?` (al menos uno de los dos primeros, si no 400).
  No cambia el status: la selección se hace en el siguiente
  dry-run, con los datos reales ya guardados.

## 6. Panel (solo lectura, sin configuración)

Tarjeta "Evolución de clips (Content Rewards)" en la pestaña
Analíticas (`dist/index.html` + `renderEvolutionStatus` en
`dist/app.js`): generación actual, mejor variante, fitness real
vs cerebro y próxima mutación. Sin controles ni ajustes nuevos:
el usuario no tiene que tocar nada.

## 8. Riesgo de ban / shadowban — investigado 2026-11-23 ANTES de desplegar

Orden del usuario (19:43): no queremos ban ni shadowban, queremos
hacer plata. Investigación hecha antes de desplegar o publicar nada
real (fuentes: política de spam de YouTube, Community Guidelines
de TikTok vigentes desde el 24-sep-2026, comunicado de Instagram
de abril 2026, Community Standards de spam de Meta, y guías de
rechazos de Content Rewards/Vyro). Distinción honesta: ninguna
plataforma publica la palabra "shadowban" como sanción formal; lo
que documentan es **pérdida de recomendación** (no te muestran a
quienes no te siguen) y, en YouTube/Meta, **strikes**. Los números
que circulan ("70% de fingerprint", "10 reposts en 30 días") son
de fuentes secundarias, NO de las páginas oficiales: no se usan
como umbrales aquí.

| Parte de la evolución | Riesgo verificado | Mitigación en este cambio |
|---|---|---|
| Variantes OFFLINE (población en el cerebro, sin publicar) | Ninguno en plataformas: nada sale. Solo escrituras D1 (~13 por dry-run). | Por diseño: el dry-run NO publica, NO crea jobs ni clips, NO envía a campañas (`published_nothing: true`). |
| Publicar 1 ganadora por momento/plataforma | **YouTube** (spam): "automated mass-production" — usar herramientas automatizadas para sacar grandes volúmenes de contenido similar con cambios mínimos. Textual: "posting a few variations of a video is ok" pero inundar con repetitivo, no. Sanción: aviso/strike, suspensión de monetización o cierre del canal (3 strikes en 90 días). **TikTok**: material "unoriginal or reused without anything new" queda fuera del For You; si se repite, TODA la cuenta queda temporalmente fuera del For You y más difícil de encontrar (con aviso y apelación). **Instagram** (abr 2026): cuentas que sobre todo repostean en 30 días dejan de ser recomendadas a no-seguidores; ni borde, ni marca de agua, ni subtítulos, ni crédito, ni cambio de velocidad cuentan como original. **Facebook/Meta**: sin límite numérico publicado, pero restringe también a frecuencias bajas si hay "posting repetitive content"; el contenido no original pierde distribución y monetización. | 1 ganadora por momento/plataforma (no 200, no 6 publicadas). Cada variante es un corte distinto REAL (≥ 1 s, `isDistinctCut`), mismo nicho y plantilla rubius (consistencia, no caos). Puertas 45/80 + calidad B4 intactas. Topes y huecos existentes sin tocar (39/día, 3 por cuenta, 2 h entre publicaciones, huecos de Chile). **Guardia nueva**: el dry-run da 409 si el clip base YA se publicó en esa plataforma (la evolución corre ANTES de publicar, nunca republica el mismo momento). |
| Envío a campaña (Content Rewards) | Rechazo del clip (no paga): las campañas rechazan edits "copied, duplicate, minimally changed" y edits reutilizados de otra campaña; cada post se acepta una sola vez y solo dentro de 30 min; el material debe ser el autorizado por el brief y reciente; la cuenta debe estar verificada en el marketplace. Un rechazo no es un ban de plataforma, pero un patrón de duplicados puede marcar la cuenta en el marketplace. | Un post = UNA campaña (UNIQUE en 0041 + 409 en dry-run si ya hay envío a otra campaña o a esta). Ventana de 30 min ya cubierta por 0041. El envío lo sigue haciendo solo la extensión con la API key del usuario (el Worker no envía). |
| Frecuencia | Meta no publica nº/día; TikTok/IG/YT miran el patrón, no solo el número. Subir el volumen total sería el riesgo. | La evolución NO aumenta el volumen publicado: los mismos momentos, 1 ganadora cada uno. Los topes actuales no se tocan. |
| Mismo clip en varias plataformas (cross-post) | Las guías de campañas recomiendan "one edit per account": el mismo archivo final en todas partes es lo que rechazan como duplicado. El AVC publica hoy el mismo clip en varias plataformas (regla vigente del proyecto: dentro de la MISMA campaña sí se cobra en varias plataformas, un link por plataforma). | La evolución ya genera la ganadora POR PLATAFORMA (el dry-run recibe `platform`): cuando se renderice la ganadora para campaña, cada plataforma debe llevar su propio corte (otro hook/inicio), no el mismo archivo. Esto queda como requisito del render, ver §7. No se cambia el cross-post actual en este cambio. |

**Veredicto: ningún riesgo sin mitigación con las reglas actuales,
así que el trabajo sigue (sin publicar nada real).** Lo que queda
como puntos ABIERTOS para verificar antes del primer envío real
(no bloquean esta implantación, que no publica nada):

1. **Disclosure (anuncio pagado)**: las guías dicen que todo post
   de campaña es un anuncio y exige disclosure visible + la
   herramienta de paid-partnership de la plataforma; sin ella,
   rechazo en Content Rewards y fuera del For You en TikTok. El
   flujo actual de clips/captions NO incluye todavía esa
   disclosure: verificarla en la revisión 20:30 antes del primer
   envío real.
2. **Brief de cada campaña**: el material debe ser solo el
   autorizado y reciente. El cerebro ya corta solo de streams/VOD
   permitidos, pero el brief concreto (minutos, logos, audios) lo
   lee el usuario; la evolución no lo comprueba.
3. **Mismo momento desde seba Y kaez a la misma campaña**: dos
   cuentas distintas con el mismo momento pueden leerse como
   duplicado en el marketplace. Coordinar en la revisión 20:30
   que un momento vaya a una campaña desde un solo tenant, salvo
   que los cortes sean momentos distintos.
4. **Señal de alarma**: si TikTok/IG muestran "no elegible para
   For You" o bajada brusca de recomendación en un clip de
   campaña, parar esa línea y revisar antes de seguir publicando
   variantes de ese momento (las plataformas avisan en
   analíticas / Account Status; no hay que adivinarlo).

**No tocado en este trabajo (orden expresa)**: el bug de
`engagement_enabled` (D1 guarda `1`, el código exige `=== true`)
es de otro módulo; no se toca aquí.

## 9. Límites de este cambio (a propósito)

- La ganadora queda **lista para renderizar** (params guardados
  en D1); el render lo hace el cerebro con esos params por el
  flujo de corte existente. Este cambio NO crea el clip ni el
  job de la ganadora y NO auto-encola nada: el primer envío real
  sigue pendiente de la revisión 20:30 / del usuario (OAuth, API
  key y token del panel, cobro en Whop).
- La siguiente generación (`parent_generation_id`) exige que la
  padre tenga ganadora; sin datos reales ni puntaje del cerebro,
  el dry-run responde 400 con el motivo exacto.
- Prioridad de streamers: Dasgasd3m primero ($1,80 neto,
  TikTok+YouTube, tope $150/clip), luego ArthurTV ($1,35 neto,
  solo TikTok, tope $300) y Dylan Page ($1,08 neto).

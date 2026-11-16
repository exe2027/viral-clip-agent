# Auditoría Claude Code — viral-clip-agent (2026-11-16)

Resumen del usuario: el repo hoy es un detector de picos de chat que corta en 9:16 y escribe textos de plantilla. No sube nada, no genera miniaturas ni subtítulos, y no soporta Kick ni directos en vivo. Probado en dry-run y con chat sintético.

## Lo prometido vs lo real

- Sube a las 4 cuentas: no hay uploader. README admite "lo único que queda a mano es subirlos".
- Miniaturas: solo un string ("QUÉ JUGADA"). No hay imagen.
- Hooks y texto en pantalla: strings en el manifiesto. Nunca se dibujan en el video.
- SEO: 3 plantillas fijas "GTA 6". Los 5 clips de un stream salen con el mismo título, descripción y tags.
- Kick y live: solo TwitchDownloaderCLI y un VOD ya terminado.

## Bugs reales

1. `xd` no puntúa como risa. Está en SHORT_TOKENS pero no en ninguna lista de risas, así que xd vale 1 punto. Es la risa más común del chat hispano.
2. Falsos positivos en español conversacional: "no puedo creer que no hayas ido" cuenta como risa; "qué bueno que llegaste" cuenta como risa; "dale gas", "vamos a ver qué pasa", "god of war" y "crazy cat" cuentan como hype.
3. Lento con chats grandes. Con 200k mensajes tardó 9,6 s. Un stream de 8 h tiene millones de mensajes. Cada mensaje recorre todas las listas de tokens y se vuelve a leer el JSON completo en sample_messages por cada clip. Con una regex precompilada y una sola lectura baja a décimas de segundo.
4. La ventana de 10 s es fija. Un pico que cae entre dos ventanas se parte en dos. Conviene una ventana deslizante con suavizado.
5. El timing está desfasado. El chat reacciona 5-15 s después de la jugada, y el clip empieza 20 s antes del pico con duración fija de 60 s. Corta a mitad de frase y mete relleno al final.
6. La mediana está sesgada. viral_multiple calcula la mediana solo sobre ventanas con mensajes, y el parámetro median_score no se usa.
7. El manifiesto solo se escribe al final. Si algo falla a mitad, pierdes todo.
8. Faltan flags de ffmpeg para plataformas: -pix_fmt yuv420p, -movflags +faststart y normalizar el audio (loudnorm). Instagram y Facebook rechazan algunos formatos.
9. El recorte 9:16 central pierde la facecam y el HUD. Es la mayor pérdida de calidad visual.

## Qué agregaría (por impacto)

1. Detección multiseñal, no solo chat: audio (picos de volumen/energía, risas y gritos), transcripción con faster-whisper (detectar frases fuertes, cortar en límites de oración, subtítulos y títulos con contexto real), un LLM que puntúe los 15-20 candidatos con la transcripción y elija el top 5 y los límites exactos (JSON con start, end, por_qué_es_viral y hook), ventana deslizante, z-score contra baseline local, tokens por categoría con peso, excluir donaciones/bots/spam, contar usuarios únicos en vez de mensajes.
2. Layout vertical real: fondo desenfocado, gameplay en el centro y facecam arriba. Detectar la facecam una vez por VOD, o --facecam-box x,y,w,h en config. Si no hay facecam, crop con seguimiento (mediapipe).
3. Subtítulos y gráficos quemados: subtítulos palabra por palabra con SRT de Whisper a ASS, hook en pantalla los primeros 2 s y barra de progreso.
4. Miniaturas reales: mejor frame (cara expresiva, nitidez, sin blur) y texto con Pillow. Para IG usar cover_url. Para Shorts/TikTok la portada suele ser el frame.
5. Textos únicos por clip con LLM: título, descripción, hashtags y hook desde la transcripción real, variantes por plataforma, validar longitudes, no repetir títulos, sacar el "GTA 6" fijo y poner --game, --streamer y --niche en config.yaml.
6. Soporte Kick y directos: descargar VODs con yt-dlp y detectar plataforma por URL. El chat de Kick no tiene API oficial para repetirlo, por eso audio y transcripción son más importantes ahí. Para directos: streamlink con grabación por segmentos de 5-10 min y chat por IRC (Twitch). Fase 2, después del pipeline sobre VOD.
7. Subida a las 4 plataformas (datos verificados en doc oficial): YouTube videos.insert cuota 100/día, cuesta 1 unidad, thumbnails.set 50; TikTok sin auditar solo privado, hay que pasar auditoría para público; Instagram tope 100 publicaciones por API cada 24 h, cuenta profesional; Facebook Reels a Página, 3-90 s, mínimo 540x960 y 24-60 fps, tope 30 Reels por API cada 24 h. Para no depender de aprobaciones: publicador ya autorizado como Postiz (open source, autoalojable, con API) o Upload-Post, Ayrshare, Blotato. Módulo publisher/ con interfaz común y adaptador por plataforma.
8. Revisión humana antes de publicar: cola de aprobación con vista previa, título editable, aprobar o rechazar. Web simple con FastAPI o Telegram/Discord con botones.
9. Aprendizaje con métricas: vistas, retención y compartidos a las 24-48 h por API, guardar qué señales predicen clips que funcionan y reajustar pesos.

## Qué sacaría o simplificaría

- viral_package.py como está: mitad texto de las "skills" de otro repo con plantillas estáticas. Quedan solo viral_multiple y el límite de longitudes, dentro de un módulo metadata.py que use el LLM.
- Constantes PLATFORMS con límites diarios inventados: leerlas de limits.yaml con fecha de revisión.
- HASHTAGS_ES y HASHTAGS_EN fijos en auto_clips.py: pasan a la config del canal.
- Duplicación entre auto_clips.py y clips_chat_v2.py: unificar en un paquete con un solo CLI (clipagent run <url>) y pipeline/ con módulos (ingest, detect, transcribe, render, metadata, publish).
- titles.txt y manifest.md: con manifest.json y la cola de revisión alcanza.
- *.json en .gitignore: solo salvando test_chat.json se puede perder una config nueva.

## Riesgo antes de automatizar

Subir clips del VOD de otro streamer a 4 cuentas puede costarte las cuentas: YouTube puede monetizar clips si agregas comentario o modificaciones originales significativas; Meta similar para reposts sin permiso y sin valor agregado; Twitch/Kick tienen sus reglas, y el copyright del juego y la música también cuenta. Lo sensato es trabajar con permiso del streamer (programas de clippers), acreditarlo y agregar subtítulos, hook y edición propia.

## Orden sugerido

1. Corregir los bugs de scoring (1-3) y unificar el CLI.
2. Agregar Whisper y un LLM para elegir los clips y sus límites.
3. Hacer el layout vertical con subtítulos y la miniatura real.
4. Generar metadata única por clip y por plataforma.
5. Conectar un publicador (Postiz o similar) con una cola de aprobación.
6. Sumar Kick, los directos y las métricas.

Fuentes: YouTube Data API cuota, TikTok Content Posting API Direct Post, Instagram Content Publishing, Facebook Reels Publishing API, Meta/YouTube contenido no original, Postiz (GitHub).

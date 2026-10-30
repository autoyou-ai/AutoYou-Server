<div align="center">

[English](../../README.md) · [简体中文](README_zh.md) · [繁體中文](README_zh-TW.md) · [日本語](README_ja.md) · [한국어](README_ko.md) · [हिन्दी](README_hi.md) · **Español** · [Français](README_fr.md) · [Deutsch](README_de.md) · [Português (Brasil)](README_pt-BR.md) · [Русский](README_ru.md) · [العربية](README_ar.md)

<img src="../images/logo.png" alt="Logotipo de AutoYou" width="96" />

# AutoYou Server

### Tu propio servidor de IA, en el ordenador que ya tienes.

**No eres el producto. Eres el dueño.**

[![Public checks](https://github.com/autoyou-ai/AutoYou-Server/actions/workflows/public-checks.yml/badge.svg?branch=main)](https://github.com/autoyou-ai/AutoYou-Server/actions/workflows/public-checks.yml)
[![Latest release](https://img.shields.io/github/v/release/autoyou-ai/AutoYou-Server?style=flat-square&color=22c55e)](https://github.com/autoyou-ai/AutoYou-Server/releases)
[![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-3776AB?style=flat-square&logo=python&logoColor=white)](../../requirements/README.md)
[![Platforms](https://img.shields.io/badge/platforms-Windows%20%7C%20macOS%20%7C%20Linux-536DFE?style=flat-square)](../../servers/README.md)
[![Built-in agents](https://img.shields.io/badge/built--in_agents-45-f97316?style=flat-square)](../../autoyou_agents/README.md)
[![Source-available license](https://img.shields.io/badge/license-source--available-8B5CF6?style=flat-square)](../../LICENSE)

**[Sitio web](https://www.autoyou.me/) · [Apps](https://www.autoyou.me/downloads/) · [Ecosistema](https://www.autoyou.me/ecosystem/) · [Comunidad](https://www.autoyou.me/community/)**

[Inicio rápido](#quick-start) · [Cómo funciona](#how-autoyou-works) · [Qué puedes hacer](#what-you-can-do) · [Agentes](#agents) · [Emparejamiento](#pairing-modes) · [Seguridad](#security-modes-and-tiers) · [Clientes](#supported-clients) · [Preguntas frecuentes](#faq) · [Documentación](#documentation)

<code>./run_autoyou.sh</code>

</div>

> [!IMPORTANT]
> Descarga AutoYou Server desde [autoyou-ai/AutoYou-Server](https://github.com/autoyou-ai/AutoYou-Server) o [autoyou.me](https://www.autoyou.me/), y las apps desde los enlaces de tienda de [Clientes compatibles](#supported-clients). Un repositorio con un nombre parecido o un README copiado no convierte una descarga en oficial.

<div align="center">
<br />
<img src="../images/hero.gif" alt="Emparejar un teléfono, chatear con un modelo local, hacer una llamada, abrir una página y ver la pantalla del ordenador" width="860" />
<br /><br />
</div>

---

Convierte el ordenador de tu escritorio en un servidor de IA privado para todos tus dispositivos. AutoYou Server ejecuta tus modelos y agentes de IA, llamadas de voz y vídeo, escritorio remoto, juegos y tus propias aplicaciones web en tu equipo Windows, macOS o Linux. Tu teléfono, otros ordenadores y navegadores se conectan a él mediante conexiones cifradas punto a punto, en casa o fuera.

Tus conversaciones, archivos y la actividad de los agentes se quedan en ese ordenador, no en un servicio alojado de AutoYou. Ejecuta modelos locales con Ollama para chatear sin facturas por tokens, o conecta el proveedor en la nube que prefieras. Empareja un dispositivo una vez y todo lo que ofrece el servidor está en tu bolsillo: chat, un asistente manos libres, voz en tiempo real en muchos idiomas, tu escritorio, tus juegos y cualquier servicio web que ya tengas en marcha.

> **¿Primera vez?** Ejecuta el lanzador, abre **http://127.0.0.1:8001/** y la guía de configuración te ayudará a elegir tu IA, activar las funciones que quieras y emparejar tu primer dispositivo.

<a id="quick-start"></a>

## Inicio rápido

Necesitas Git y Python 3.11 o superior. Algunas funciones opcionales necesitan un Python más reciente o una plataforma concreta; consulta los [perfiles de dependencias](../../requirements/README.md).

### 1. Descarga el servidor

```bash
git clone https://github.com/autoyou-ai/AutoYou-Server.git
cd AutoYou-Server
```

### 2. Ejecuta el lanzador

Windows:

```powershell
.\run_autoyou.bat --profile base
# Or select the Full profile with LAN access:
.\run_autoyou.bat --profile full --host 0.0.0.0
```

macOS o Linux:

```bash
./run_autoyou.sh --profile base
# Or select the Full profile with LAN access:
./run_autoyou.sh --profile full --host 0.0.0.0
```

El lanzador crea un entorno de Python, instala dependencias desde los índices de paquetes y puede descargar modelos y herramientas nativas. Lee primero los scripts y ejecútalos en un entorno que puedas modificar con seguridad.

### 3. Abre la consola y empareja un dispositivo

Ve a **http://127.0.0.1:8001/**, elige una contraseña de servidor única y sigue la configuración. Te pregunta para qué quieres usar AutoYou (chat, asistente manos libres, llamadas, juegos, programación y más), instala los agentes correspondientes y muestra un código QR para emparejar tu teléfono.

Para una primera configuración desatendida, proporciona `AUTOYOU_SERVER_PASSWORD` mediante tu entorno o un gestor de secretos. No pongas contraseñas reales en comandos compartidos, issues ni capturas de pantalla.

| Perfil | Añade |
| --- | --- |
| `base` | Servidor principal, administración local, conexiones de dispositivos e integraciones con proveedores en la nube |
| `local` | Ayudantes para modelos locales y enrutamiento de intenciones en el dispositivo |
| `recommended` | Dependencias de mensajería y Bluetooth (valor por defecto si omites `--profile`) |
| `full` | Dependencias de voz, navegador y automatización del escritorio |

Los perfiles solo eligen dependencias; las integraciones siguen necesitando configuración. `--skip-nuitka` y `--skip-backend-compile` conservan las bibliotecas integradas del lanzador, así que las actualizaciones de seguridad de dependencias en apps empaquetadas necesitan una recompilación completa.

Para actualizar, ejecuta `git pull` en tu copia y vuelve a lanzar el script. Las notas de versión están en [docs/releases/](../releases/).

<a id="how-autoyou-works"></a>

## Cómo funciona AutoYou

**El servidor es tu propio ordenador, y los dispositivos hablan directamente con él.** Las apps y el servidor se conectan con WebRTC. Tras un breve intercambio de emparejamiento abren un enlace cifrado (DTLS, con SRTP para el audio y el vídeo) que transporta chat, voz, vídeo, pantalla compartida, controles de juego y tráfico web a través de proxy. La mayoría de las redes domésticas y móviles crean ese enlace sin abrir puertos en el router. Si una red es demasiado estricta, la conexión pasa a un relé que reenvía los mismos paquetes cifrados.

```mermaid
flowchart LR
    app["App de AutoYou<br/>teléfono, ordenador, navegador o Pi"]
    server["AutoYou Server<br/>tu ordenador"]
    rendezvous["Punto de encuentro (opcional)<br/>Cloud Pair"]
    relay["Relé (opcional)<br/>reenvía paquetes cifrados"]

    app <-->|"WebRTC cifrado, directo cuando la red lo permite"| server
    app -.->|"señalización"| rendezvous
    rendezvous -.->|"señalización"| server
    app -.->|"ruta alternativa"| relay
    relay -.->|"ruta alternativa"| server
```

En el servidor, un enrutador de intenciones local envía cada petición al agente o herramienta adecuados. Google ADK ejecuta los agentes nativos, y se pueden conectar OpenClaw, Hermes Agent y Odysseus. LiteLLM envía las llamadas al modelo a Ollama en tu equipo o al proveedor en la nube que elijas. La voz se reconoce y se sintetiza localmente, y la memoria vive en SQLite.

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="../images/architecture-dark.png" />
  <img src="../images/architecture-light.png" alt="Arquitectura de AutoYou: las apps se conectan al servidor de tu ordenador por un enlace WebRTC cifrado; los mensajes de Auto Pair viajan por Telegram, Signal o WhatsApp; los clientes MCP se conectan por el puente MCP; el enrutador de intenciones conecta los entornos de agentes, las apps de agentes, la voz local, la memoria y los modelos a través de LiteLLM con Ollama o proveedores en la nube opcionales." width="100%" />
</picture>

El diagrama se genera con [scripts/build_architecture_diagram.py](../../scripts/build_architecture_diagram.py). Los recuadros discontinuos son complementos opcionales o proyectos conectados.

<a id="what-you-can-do"></a>

## Qué puedes hacer

| Función | Lo que te da |
| --- | --- |
| **Chat con IA** | Chatea con tu propia IA desde cualquier dispositivo emparejado; AutoYou no limita los mensajes |
| **Voz en tiempo real** | Llamadas en vivo con tu IA, reconocimiento de voz local y respuestas habladas en más de 30 idiomas |
| **Modo asistente** | Una palabra de activación como "Hey Jarvis" en tu teléfono, ordenador, Raspberry Pi o navegador |
| **Escritorio remoto** | Ve y controla tu ordenador desde el teléfono, incluida la pantalla de bloqueo de Windows |
| **Vídeo del ordenador** | Envía a una llamada la pantalla, una cámara, un archivo de vídeo o fotogramas por API |
| **Modo juego y Game Studio** | Transmite juegos de tu ordenador al teléfono o crea juegos que funcionan sin conexión en él |
| **Fábrica de apps web** | Pide a un agente que cree una app web y ábrela en todos tus dispositivos emparejados |
| **Publica cualquier puerto local** | Comparte cualquier servicio web de tu ordenador, como `localhost:3000`, con tus dispositivos |
| **Agentes** | 45 agentes integrados para notas, archivos, programación, multimedia, navegación, entrenamiento y más |
| **MCP** | Usa herramientas de AutoYou desde ChatGPT, Codex, Claude, Antigravity, Cursor y VS Code |
| **Mensajería** | Llega a tu servidor por Telegram, Signal y WhatsApp con conectores que tú controlas |
| **Memoria** | Memoria en SQLite y un diario de persona privado, con Cognee como complemento opcional |

Las funciones dependen del perfil elegido, el hardware, los permisos y las integraciones configuradas. Las capacidades potentes, como el control del escritorio, el modo juego, la grabación y el acceso a archivos, permanecen desactivadas hasta que las actives.

<a id="ai-chat"></a>

### Chat con IA

Elige un proveedor en **AI** en la consola: **Ollama + Agents** por defecto (modelo local, sin clave de API), **Apple Intelligence** en Mac compatibles, **Gemini**, **OpenRouter** u otros proveedores mediante LiteLLM (Anthropic, OpenAI, Mistral, DeepSeek, xAI y más), siempre con los agentes y herramientas de AutoYou. También puedes usar **OpenClaw**, **Hermes Agent** u **Odysseus** como entorno de sesión principal.

AutoYou no limita cuántos mensajes envías. Con un modelo local, el chat no tiene coste por mensaje y funciona sin conexión a internet. Un proveedor en la nube que conectes te cobra según su propio plan y ve los prompts que le envías. La biblioteca de modelos integrada permite explorar, descargar y cambiar de modelo local, y el agente Model Picker recomienda uno adecuado para tu hardware.

Los modelos locales pequeños y cuantizados reciben ayuda para mantenerse precisos: un clasificador de intenciones sin conexión envía las peticiones habituales directamente al agente adecuado, y un modo de enrutamiento compacto mantiene corta la lista de herramientas. Consulta [cómo se mantienen precisos los modelos pequeños](../../autoyou_agents/README.md#small-local-models).

<a id="realtime-voice-in-many-languages"></a>

### Voz en tiempo real en muchos idiomas

Llama a tu IA y habla. El reconocimiento de voz se ejecuta en tu ordenador (whisper.cpp en la compilación por defecto, faster-whisper en las compilaciones de voz completas), con detección de actividad de voz, interrupción en cuanto empiezas a hablar y una pausa **Espera a que termine de hablar** para ideas largas.

- **Habla en tu idioma.** Deja **STT language** en `auto` y un modelo multilingüe detecta el idioma que hablas.
- **Escucha la respuesta en ese idioma.** **Auto TTS**, la opción por defecto, detecta el idioma de la respuesta y elige una voz del sistema que coincida. El modelo local Supertonic 3 habla 31 idiomas y funciona sin conexión tras una única descarga de unos 401 MB.
- **Elige tu voz.** Voces del sistema, diez voces predefinidas de Supertonic, una voz personalizada entrenada en la app Voice Training, EmotiVoice para chino e inglés, o la voz alojada de OpenAI y Azure.

La síntesis de voz pronuncia la respuesta que recibe; no la traduce. El modelo de chat que elijas también debe admitir el idioma de la conversación.

<a id="assistant-mode"></a>

### Modo asistente

Dale a tu servidor una palabra de activación, como "Hey Jarvis". Dila cerca de un teléfono, ordenador, Raspberry Pi, la extensión de Chrome o la página de administración, y lo que digas a continuación llega a AutoYou. Hasta que se oye la palabra, nada de lo que dices se transcribe ni se envía a la IA. Grábala tres veces, entrénala en tu servidor en unos segundos, pruébala y actívala. En una videollamada, la petición tras la palabra de activación puede incluir la imagen más reciente de la cámara. Consulta [Modo asistente](../admin-ui/assistant-mode.mdx).

<a id="remote-desktop"></a>

### Escritorio remoto

Ve y controla tu ordenador desde un teléfono o app de escritorio emparejados: elige un monitor o una sola ventana, mueve el ratón, escribe y usa teclas modificadoras. Se prefiere H.264 cuando el dispositivo lo admite. En Windows, el servicio opcional **AutoYou Secure Desktop Helper** permite al propietario emparejado ver y usar la pantalla de bloqueo y los avisos de UAC, y el servidor puede mantener el equipo despierto mientras estás conectado. La captura de pantalla y el control son permisos separados, desactivados por defecto, y el escritorio remoto nunca se ofrece a través de un Peer Link.

<a id="computer-video"></a>

### Vídeo del ordenador

En una llamada, el ordenador puede enviar vídeo desde cuatro fuentes: su **pantalla**, una **cámara**, un **archivo de vídeo** que subes y reproduces, o **fotogramas por API** que envía tu propio programa. También puede compartir su micrófono y su sonido. En la consola, **Interact** muestra la imagen en directo de las cámaras de los dispositivos conectados y puede reproducir sus micrófonos en los altavoces del ordenador, sin procesamiento de IA y sin grabar.

<a id="game-mode-and-game-studio"></a>

### Modo juego y Game Studio

- **El modo juego** transmite a un dispositivo emparejado un juego que se renderiza en tu ordenador, y el tacto, los sensores de movimiento, el mando, el ratón y el teclado del dispositivo vuelven como controles del juego. Puedes definir hasta cuatro botones virtuales con nombre. El modo juego es un permiso propio y también activa el envío de pantalla y el control del escritorio remoto.
- **Usa tu propio motor.** Un motor de juego local puede leer fotogramas de entrada autenticados con el protocolo `autoyou_game_v1` desde un WebSocket local. Para Unreal, el servidor ofrece un plugin con código fuente en `/api/game/unreal-plugin`.
- **Juegos sin conexión para el teléfono.** El agente Game Studio te ayuda a crear juegos web o WASM portátiles, los empaqueta como archivos `.aygame` de hasta 5 GiB y los envía a los clientes por WebRTC o por un Peer Link aprobado. Cada juego tiene su propia base de datos SQLite en el dispositivo, y el progreso guardado sobrevive a las actualizaciones.

**Neon Horizon** es la demo integrada: se juega como descarga en el teléfono, en la página de Game Studio o transmitido desde tu ordenador.

<a id="web-app-factory"></a>

### Fábrica de apps web

Describe una app y deja que los agentes la construyan. El agente **Website** crea una interfaz web para un agente: una plantilla sencilla, o React o Angular si lo pides, con backend en Python/FastAPI, Node/TypeScript, Go o Rust. **Agent Builder** genera la estructura de un nuevo agente, y los agentes **Coding** y **Pi** implementan y prueban el código. Cada app se registra en el servicio web local y aparece en el navegador de tus dispositivos emparejados. El agente **Hosting** explica cómo publicar en un enlace público: un túnel temporal gratuito `/pair` o una URL persistente de pago.

<a id="host-any-local-port"></a>

### Publica cualquier puerto local

¿Ya tienes algo en marcha? Compártelo. En **Websites & Browser > Advertised Websites**, añade cualquier puerto del 1 al 65535 con una etiqueta y una URL de reenvío opcional, como `http://127.0.0.1:3000` u otro equipo de tu red. Los clientes emparejados lo descubren y lo abren en el mismo puerto en su lado, así que los paneles, servidores de desarrollo, herramientas de homelab y apps que no se diseñaron para vivir bajo una subruta siguen funcionando. El tráfico viaja dentro del enlace cifrado, por lo que no necesitas abrir el puerto en el router, y los navegadores emparejados reciben el rol `viewer`, `editor` o `admin`, que limita lo que pueden cambiar.

<a id="agents"></a>

## Agentes

AutoYou incluye 45 agentes. El agente principal envía cada petición al especialista adecuado, y muchos especialistas tienen su propia página web en tus dispositivos emparejados. Los marcados como opt-in no se instalan hasta que lo hagas en **Agents** en la consola.

| Área | Agentes |
| --- | --- |
| Día a día | Notes, Tasks, Notify, Memory, Persona, Page, Prompts, Location (opt-in) |
| Web y automatización | Internet, Assistant, Browser (opt-in), Client Browser Control, Proxy (opt-in) |
| Multimedia y creatividad | Audio, TV, Media Generation (opt-in), Game Studio |
| Programación y construcción | Coding, Pi, Agent Builder, Website, Skills, Files, CLI, Claude CLI, Claude Desktop, Codex Desktop, Antigravity, Prompt Builder (todos opt-in) |
| Inteligencia y modelos | Model Picker, OpenClaw, Hermes Agent (todos opt-in) |
| Entrenamiento | Data Collector, Fine-Tuning, Voice Training (todos opt-in) |
| Sistema y ordenador | Admin, Remote Desktop, Backup, Hosting (opt-in), Windows Security (opt-in), macOS Security (opt-in), Education (opt-in) |
| Apoyar a AutoYou | Support Ads, Earnings, Donation |

Añade los tuyos copiando un paquete en `autoyou_agents/`. Todos los detalles están en el [catálogo de agentes](../../autoyou_agents/README.md).

La [referencia de agentes generada](../agents/) enumera cada herramienta que declara cada agente.

<a id="pairing-modes"></a>

## Modos de emparejamiento

| Método | Cómo conecta | Uso típico |
| --- | --- | --- |
| **Local Pair** | Directamente por tu LAN; tu propio servidor responde a la señalización | Misma Wi-Fi o red de oficina |
| **Bluetooth Pair** | Los datos de conexión se entregan por Bluetooth Low Energy | Primera configuración de un teléfono cercano |
| **Live Pair** | El teléfono y el ordenador se muestran códigos QR animados, sin ruta de red entre ellos | Redes aisladas o muy restringidas |
| **Código de un solo uso** | `/pair` en un chat de mensajería devuelve un código temporal y un enlace | Dispositivos de invitados y redes no fiables |
| **Auto Pair** | Un mensaje de emparejamiento cifrado viaja por un chat de Telegram, Signal o WhatsApp que configuras | Emparejar a distancia sin cuenta en la nube |
| **Cloud Pair** | Un punto de encuentro opcional y de pago de AutoYou Cloud encuentra tu ordenador a través de NAT, con relé de respaldo | Fuera de casa o en redes estrictas |

**Peer Link** conecta una app de AutoYou con otra; la app receptora aprueba cada enlace y nunca usa la contraseña de tu ordenador. Los **Lobbies** son salas de grupo que las apps crean y a las que se unen cerca por Wi-Fi y Bluetooth. Consulta los [protocolos de emparejamiento](../technical/pairing-protocols.md).

<a id="security-modes-and-tiers"></a>

## Modos y niveles de seguridad

Un único modo de seguridad se aplica a todo el servidor. Una instalación nueva empieza en **Secure**.

| Modo | Mensajes de emparejamiento | Qué necesita un dispositivo | Datos en el ordenador |
| --- | --- | --- | --- |
| **Normal** | Sin cifrar | Un código de un solo uso | Sin cambios |
| **Secure** | Cifrados con la contraseña del servidor | La contraseña del servidor | Sin cambios |
| **Secure Professional** | Cifrados con la contraseña del servidor | La contraseña y un código del autenticador | Sin cambios |
| **Secure Professional Maximus** | Igual que Secure Professional | La contraseña y un código del autenticador | Sesiones, datos de agentes, webs, notas y ajustes cifrados en reposo |

En los modos Secure, los mensajes de emparejamiento se cifran con una clave obtenida mediante un intercambio de claves autenticado por contraseña (una construcción de la familia CPace sobre ristretto255 con libsodium). Un relé o puente de mensajería que los transporte no puede leerlos, y un intercambio grabado no sirve para probar contraseñas sin conexión.

**El nivel de seguridad del emparejamiento** decide cuánto trabajo exige un emparejamiento por mensaje de chat: **Quick Pairing (B)** permite emparejar con un solo mensaje y ofrece la mejor compatibilidad; **Enhanced Pairing (A)** obliga a cada cliente a completar antes un intercambio hello adicional, lo que dificulta adivinar. Local Pair, Bluetooth Pair y Cloud Pair siempre usan el flujo reforzado.

El navegador de un dispositivo emparejado recibe el rol `viewer`, `editor` o `admin`. Las credenciales y los ajustes de seguridad solo pueden cambiarse desde el propio ordenador o desde una sesión de administración HTTPS abierta directamente en el puerto de administración. El cifrado protege la conexión, no los dispositivos, los permisos de los agentes ni los servicios de terceros. Consulta [modos de seguridad](../security/security-modes.md) y [SECURITY.md](../../SECURITY.md).

<details>
<summary><strong>Qué protege cada capa y dónde termina</strong></summary>

| Capa | Qué hace AutoYou |
| --- | --- |
| Exposición de red | Escucha en `127.0.0.1` por defecto. Escuchar en tu LAN es un ajuste explícito. |
| Acceso | La consola de administración está protegida por la contraseña del servidor, con códigos opcionales de una app autenticadora (TOTP). |
| Emparejamiento | Intercambio de claves autenticado por contraseña, códigos de un solo uso y aprobación por enlace en Peer Link. |
| Transporte | WebRTC DTLS y SRTP entre los dos extremos, también cuando un relé reenvía los paquetes. |
| Datos en reposo | Las credenciales se guardan en un almacén de claves cifrado; Maximus cifra también los datos que gestiona AutoYou. |
| Dependencias | Manifiestos con archivo de bloqueo, comprobaciones de avisos de seguridad y acciones de CI fijadas. |

El cifrado protege la conexión. No protege todo lo que la rodea:

- **Dispositivos.** Un malware en el ordenador servidor o en un dispositivo emparejado tiene el acceso que tenga ese dispositivo.
- **Agentes.** Los agentes se ejecutan con los permisos del servidor. La automatización del navegador, el acceso a archivos, el control del escritorio y la ejecución de comandos son potentes; activa solo lo que necesites y revisa lo que hacen.
- **Terceros.** Los proveedores de modelos en la nube, los puentes de mensajería y los túneles públicos tienen sus propios flujos de datos y condiciones. Revísalos antes de activarlos.
- **Operación.** Mantener un servidor te hace responsable de las actualizaciones, las copias de seguridad y las credenciales.

</details>

<details>
<summary><strong>Qué servicios externos intervienen</strong></summary>

Tú operas un único servidor: el tuyo. No hace falta ningún servidor de aplicaciones operado por AutoYou para ejecutar tu asistente ni para guardar tus datos, y el servidor funciona en una red local sin conexión a AutoYou Cloud ([detalles](../architecture/cloud-and-remote.mdx)). Todo lo demás es un valor por defecto que puedes cambiar o un ayudante opcional:

| Componente | Se ejecuta en | Qué puede ver |
| --- | --- | --- |
| AutoYou Server: agentes, modelos, almacenamiento, consola | Tu ordenador | Tus datos. Es un extremo de la conexión. |
| Apps y clientes de navegador | Tus dispositivos | Lo que compartas con ellos |
| Servidores STUN para descubrir la dirección (activos por defecto) | Los servidores STUN públicos de Google, salvo que los cambies | La IP pública y el puerto desde los que aparece una conexión WebRTC |
| Punto de encuentro de Cloud Pair (opcional) | AutoYou Cloud | Correo de la cuenta, ID del dispositivo, datos de emparejamiento temporales y metadatos de red |
| Relé de respaldo (opcional) | AutoYou Cloud | Paquetes cifrados y metadatos de red |
| Avisos push para despertar apps del teléfono (opcional) | Servicios push de Apple y Google | Que se pidió despertar la app |
| Comprobación de actualizaciones (activa al vincular una cuenta) | Canal de actualizaciones de AutoYou Cloud | Una petición de actualización autenticada con la cuenta vinculada |
| Proveedores de modelos en la nube (opcional) | El proveedor que elijas | Los prompts que le envías |
| Puentes de Telegram, Signal y WhatsApp (opcional) | Esos servicios | Los mensajes que pasan por ellos, según sus condiciones |
| Túnel público para sitios web (opcional) | El proveedor del túnel | El tráfico a la URL pública. No es punto a punto. |

**El STUN público está activo por defecto**, también para Local Pair. Para que esa consulta no salga a internet, define `AUTOYOU_DISABLE_PUBLIC_STUN=1` antes de iniciar el servidor, o apunta a tu propio servidor STUN y TURN con `AUTOYOU_LOCAL_STUNTURN_HOST` (más `AUTOYOU_LOCAL_TURN_USERNAME` y `AUTOYOU_LOCAL_TURN_PASSWORD` para TURN). Sin ningún servidor STUN o TURN, las conexiones a través de NAT pueden fallar.

</details>

<a id="supported-clients"></a>

## Clientes compatibles

AutoYou Server se ejecuta en tu ordenador; las apps llegan a él desde cualquier otro lugar. Las apps cliente se distribuyen por separado y su código no forma parte de este repositorio.

| Plataforma | Dónde conseguirla |
| --- | --- |
| iPhone y iPad | [App Store](https://apps.apple.com/us/app/autoyou/id6760363728) |
| Android | [Google Play](https://play.google.com/store/apps/details?id=com.autoyou.app) |
| Windows | [Microsoft Store](https://apps.microsoft.com/detail/9mw8l2wfw7wv?hl=en-US&gl=US) |
| macOS y Linux | [Descargas](https://www.autoyou.me/downloads/) |
| Navegador | Extensión de Chrome y cliente web ([página del ecosistema](https://www.autoyou.me/ecosystem/)) |
| Raspberry Pi | Cliente de voz en Python para el modo asistente ([configuración](../admin-ui/assistant-mode.mdx)) |

Las apps del teléfono incluyen además una pequeña IA local (LFM 2.5 350M, Gemini Nano o Apple Intelligence, según el dispositivo) para chatear cuando no hay ordenador disponible.

| Modos de emparejamiento | Chatea con tu propia IA | Lobbies cercanos | IA local en el teléfono |
| --- | --- | --- | --- |
| <img src="../images/apps/android-pairing.png" alt="Modos de emparejamiento: Local, Auto, Bluetooth, Cloud, código de un solo uso y Peer Link" width="200"> | <img src="../images/apps/ios-chat.png" alt="Chat con un agente de IA que se ejecuta en tu propio ordenador" width="200"> | <img src="../images/apps/ios-lobbies.png" alt="Descubrir y crear Lobbies cercanos por Wi-Fi y Bluetooth" width="200"> | <img src="../images/apps/android-local-ai.png" alt="Chat con IA local en el teléfono" width="200"> |

- **Lobbies.** Crea o únete a salas cercanas por Wi-Fi y Bluetooth, con chat y audio o vídeo para hasta seis invitados y el anfitrión.
- **Peer Link.** Conecta una app con otra. La app receptora aprueba cada enlace, y las respuestas opcionales de IA usan solo lo que tú le contaste.
- **Tu ordenador, en todas partes.** Chat, llamadas, escritorio remoto, modo juego, webs de agentes y puertos publicados, en cada dispositivo que emparejes.

<a id="mcp"></a>
<a id="use-autoyou-from-your-coding-agent"></a>

## Usa AutoYou desde tu agente de programación

Conecta las funciones compatibles con MCP de ChatGPT, Codex, Claude Code o Claude Desktop, Google Antigravity, Cursor y VS Code con las herramientas compatibles de AutoYou. El adaptador MCP local es un proceso aparte; el servidor no lo instala en esos clientes, y las llamadas MCP exponen las herramientas que admite el adaptador, no un acceso arbitrario a tus archivos ni a todas las funciones de AutoYou.

1. Inicia AutoYou Server y abre **http://127.0.0.1:8001/**.
2. En **Admin UI > Messaging > AutoYou MCP**, activa MCP, genera el token privado del adaptador y descarga `autoyou-mcp.env`. Guarda este archivo en privado.
3. Instala el adaptador, que se mantiene por separado, en un entorno de Python dedicado:

   ```powershell
   git clone https://github.com/autoyou-ai/AutoYou.git
   cd AutoYou
   py -3 -m venv .venv-mcp
   .\.venv-mcp\Scripts\python.exe -m pip install -e .\plugins\autoyou-mcp
   ```

   En macOS o Linux, usa `python3 -m venv .venv-mcp` y `.venv-mcp/bin/python -m pip install -e ./plugins/autoyou-mcp`.

Para los clientes que lanzan un proceso MCP local, usa este comando con rutas absolutas:

```text
<path-to-.venv-mcp-python> -m autoyou_mcp --env-file <path-to-autoyou-mcp.env> --transport stdio
```

<details>
<summary><strong>Añade AutoYou a cada cliente</strong></summary>

Para los clientes que usan el formato JSON `mcpServers`, la entrada es así. Sustituye las dos rutas de ejemplo y escapa las barras invertidas de Windows si las usas en JSON:

```json
{
  "mcpServers": {
    "autoyou": {
      "command": "C:/path/to/AutoYou/.venv-mcp/Scripts/python.exe",
      "args": [
        "-m", "autoyou_mcp",
        "--env-file", "C:/path/to/autoyou-mcp.env",
        "--transport", "stdio"
      ]
    }
  }
}
```

**ChatGPT web** usa una conexión MCP remota, no un proceso `stdio` local. Inicia el adaptador con `--transport streamable-http` y conecta su endpoint local `http://127.0.0.1:8071/mcp` mediante OpenAI Secure MCP Tunnel. ChatGPT necesita el modo desarrollador y el acceso de espacio de trabajo o administrador correspondiente, y OpenAI limita las acciones MCP completas según el plan. Sigue la [configuración de AutoYou MCP para ChatGPT](https://github.com/autoyou-ai/AutoYou/blob/main/plugins/autoyou-mcp/README.md#connect-chatgpt-web) y la [guía de apps MCP de OpenAI](https://help.openai.com/en/articles/12584461-developer-mode-and-mcp-apps-in-chatgpt).

**Codex CLI, la app de escritorio y la extensión del IDE** comparten la configuración MCP del host de Codex:

```powershell
codex mcp add autoyou -- "C:/path/to/AutoYou/.venv-mcp/Scripts/python.exe" -m autoyou_mcp --env-file "C:/path/to/autoyou-mcp.env" --transport stdio
codex mcp list
```

O añade el `command` y los `args` equivalentes en `[mcp_servers.autoyou]` de `~/.codex/config.toml`. Consulta la [guía MCP de Codex](https://learn.chatgpt.com/docs/extend/mcp?surface=cli).

**Claude Code:**

```powershell
claude mcp add --transport stdio autoyou -- "C:/path/to/AutoYou/.venv-mcp/Scripts/python.exe" -m autoyou_mcp --env-file "C:/path/to/autoyou-mcp.env" --transport stdio
claude mcp list
```

Después ejecuta `/mcp` en Claude Code. Consulta la [guía MCP de Claude Code](https://code.claude.com/docs/en/mcp).

**Claude Desktop** lanza el adaptador como servidor stdio local. Añade una entrada `mcpServers.autoyou` a `~/Library/Application Support/Claude/claude_desktop_config.json` en macOS o a `%APPDATA%\Claude\claude_desktop_config.json` en Windows, y cierra y vuelve a abrir Claude Desktop por completo. AutoYou no distribuye por ahora una extensión de escritorio `.mcpb`. Consulta la [guía MCP de Claude Desktop](https://support.claude.com/en/articles/10949351-getting-started-with-local-mcp-servers-on-claude-desktop).

**Google Antigravity** usa `~/.gemini/config/mcp_config.json` de forma global o `.agents/mcp_config.json` en un espacio de trabajo. En Antigravity IDE, abre **MCP Servers > Manage MCP Servers > View raw config**. Consulta la [guía MCP de Antigravity](https://antigravity.google/docs/mcp).

**Cursor** usa `~/.cursor/mcp.json` o `.cursor/mcp.json` con la misma entrada `mcpServers`. Consulta la [guía MCP de Cursor](https://prod.cursor.com/help/customization/mcp).

**VS Code** puede usar el archivo portátil `.mcp.json` en la raíz del espacio de trabajo. `.vscode/mcp.json` usa en su lugar un objeto `servers` de primer nivel, con `"type": "stdio"`. Consulta la [guía MCP de VS Code](https://code.visualstudio.com/docs/agent-customization/mcp-servers).

En macOS o Linux, el ejecutable de Python es `.venv-mcp/bin/python`. Las versiones de los clientes, sus ajustes, las políticas de la organización y la aprobación de herramientas pueden afectar a la disponibilidad.

</details>

Tras conectar, comprueba que `get_autoyou_status` aparece y devuelve el estado esperado del servidor antes de usar cualquier herramienta que escriba. Consulta la [guía de integración MCP de AutoYou](../guides/mcp-integration.mdx) para conocer su alcance, su modelo de seguridad y sus límites.

<a id="admin-console"></a>

## Consola de administración

<p align="center">
  <img src="../images/admin-tour.gif" alt="Recorrido por la consola: resumen del servidor, dispositivos y actividad en directo, acceso y seguridad" width="760" />
</p>

La consola local reúne configuración, modelos, voz, agentes, conexiones, permisos y seguridad. La configuración guiada pregunta para qué quieres usar AutoYou y propone los agentes y permisos que encajan; nada cambia hasta que revisas y guardas el plan. Las capturas usan valores de ejemplo. Selecciona una vista previa para abrir la imagen completa.

| Resumen del servidor | Dispositivos y actividad | Acceso y seguridad |
| --- | --- | --- |
| [<img src="../images/admin/overview.png" alt="Resumen del servidor, servicios y controles de configuración" width="280">](../images/admin/overview.png) | [<img src="../images/admin/live-view.png" alt="Vista en directo con conexiones de dispositivos, tareas e integraciones de mensajería" width="280">](../images/admin/live-view.png) | [<img src="../images/admin/security.png" alt="Contraseña del servidor, autenticador y ajustes de seguridad" width="280">](../images/admin/security.png) |

<a id="other-ways-to-run"></a>

## Otras formas de ejecutarlo

<details>
<summary><strong>Docker</strong></summary>

Proporciona `AUTOYOU_SERVER_PASSWORD` de forma segura y ejecuta:

```bash
docker compose up --build
```

El archivo Compose publica los puertos solo en el loopback del host y guarda la configuración en un volumen con nombre. Abre `http://localhost:8001/` en el mismo ordenador; el archivo declara esa publicación solo en loopback (`AUTOYOU_ADMIN_TRUST_PUBLISHED_LOOPBACK=1`), así que el inicio de sesión no se rechaza con "requires localhost or HTTPS". Con `docker run` a secas, publica en `127.0.0.1` y pasa la misma variable, o pon un proxy TLS delante. Comprueba cómo publica los puertos tu versión de Docker antes de confiar en el aislamiento por loopback.

Para desactivar las descargas de Tunnelmole durante la compilación y la ejecución, define `AUTOYOU_SKIP_TUNNELMOLE_DOWNLOAD=1` y `AUTOYOU_NO_DOWNLOAD_TUNNELMOLE=1` antes de compilar e iniciar. Las funciones que lo necesitan requerirán entonces un binario proporcionado por separado.

</details>

<details>
<summary><strong>Compila una versión local no oficial</strong></summary>

Desde la raíz del repositorio:

```powershell
# Windows
.\servers\windows\build-all.ps1 -Configuration Debug
```

```bash
# macOS
./servers/macos/build-all.sh --no-sign --dev

# Linux / WSL
./servers/wsl/build-backend.sh --unofficial
```

Estas rutas no necesitan una cuenta de AutoYou ni la aprobación de una versión oficial. Pueden instalar o ajustar dependencias. Revisa la licencia y los avisos cuando se te pida. Para aceptarlos sin intervención tras revisarlos, pasa `-AcceptTerms` en Windows o `--accept-terms` en macOS y Linux.

Requisitos, ubicación de los resultados y limitaciones conocidas:
[Windows](../../servers/windows/README.md) · [macOS](../../servers/macos/README.md) · [Linux / WSL](../../servers/wsl/README.md).

</details>

<a id="faq"></a>

## Preguntas frecuentes

<details>
<summary><strong>¿Necesito una cuenta en la nube o una VPN?</strong></summary>

No. Local Pair, Bluetooth Pair, Live Pair, los códigos de un solo uso y Auto Pair funcionan sin cuenta de AutoYou. Cloud Pair es un ayudante opcional de pago. Si ya usas una VPN como Tailscale, AutoYou también funciona a través de ella.

</details>

<details>
<summary><strong>¿El chat con IA es realmente ilimitado?</strong></summary>

AutoYou no limita los mensajes. Con un modelo local de Ollama, cada mensaje es gratis y la velocidad depende de tu ordenador. Un proveedor en la nube que conectes tiene sus propios precios y límites.

</details>

<details>
<summary><strong>¿AutoYou sube mis archivos o conversaciones?</strong></summary>

Se guardan en tu ordenador. Los datos solo salen a través de los servicios que actives, como un proveedor de modelos en la nube, un puente de mensajería o un túnel público, y cada uno tiene sus propias condiciones.

</details>

<details>
<summary><strong>¿En qué idiomas puedo hablar?</strong></summary>

El reconocimiento de voz detecta el idioma cuando **STT language** es `auto` y hay un modelo multilingüe seleccionado. Las respuestas habladas usan una voz del sistema que coincida o el modelo local Supertonic 3, que cubre 31 idiomas. Tu modelo de chat también debe entender el idioma.

</details>

<details>
<summary><strong>¿Puedo compartir una app web o un juego con amigos?</strong></summary>

Sí. Cualquiera que empareje con tu servidor puede abrir tus webs de agentes y tus puertos publicados, dentro del rol que le des. Los dispositivos compartidos se marcan como compartidos, así que tus webs pueden limitar lo que ven. Para quienes no están emparejados, el agente Hosting explica el enlace temporal gratuito y la URL persistente de pago.

</details>

<details>
<summary><strong>¿Qué hago si encuentro un error?</strong></summary>

Primero actualiza; puede que una versión reciente ya lo corrija. Si el problema sigue, [abre un issue](https://github.com/autoyou-ai/AutoYou-Server/issues) con tu versión de AutoYou (`VERSION`), el sistema operativo, el perfil, los pasos para reproducirlo y la salida del error. Informa de vulnerabilidades en privado según [SECURITY.md](../../SECURITY.md).

</details>

<a id="documentation"></a>

## Documentación

- [Inicio rápido](../quickstart.mdx) · [Arquitectura](../architecture/) · [Seguridad y privacidad](../security/) · [Consola de administración](../admin-ui/)
- [Catálogo de agentes](../../autoyou_agents/README.md) · [Referencia de agentes](../agents/) · [Framework de agentes](../agent-framework/)
- [API HTTP y WebSocket](../api-reference/) · [Guías](../guides/) · [Guías de emparejamiento](../../guides/README.md)

<a id="contributing"></a>

## Contribuir

Nos encantaría contar con tu ayuda. Empieza por los [agentes integrados](../../autoyou_agents/README.md), el [Agent Builder](../admin-ui/agent-workbench-ui.mdx) o el [tutorial de agentes personalizados](../guides/custom-agent-tutorial.mdx).

- **Crea** un agente, un juego, una extensión, una integración o una interfaz web.
- **Mejora** la instalación, la accesibilidad, el soporte de plataformas, las traducciones, la documentación o un error reproducible.
- **Comparte** una demo, una guía o una compilación no oficial claramente identificada que respete la licencia.

Lee [CONTRIBUTING.md](../../CONTRIBUTING.md) y el [Código de conducta](../../CODE_OF_CONDUCT.md) antes de enviar tu trabajo. El código original e independiente puede usar la licencia que elijas, incluidas MIT o Apache-2.0. Los cambios al código existente de AutoYou mantienen su licencia.

<a id="license"></a>

## Licencia

AutoYou Server es source-available bajo la [licencia del servidor](../../LICENSE). Actualmente no es un proyecto de código abierto aprobado por la OSI, y prevalecen las definiciones de `LICENSE`.

| Material o uso | Condiciones |
| --- | --- |
| Uso individual, compilaciones locales y distribución gratuita | Permitidos según la licencia del servidor y sus condiciones |
| Modificaciones del código de AutoYou | Mantienen la licencia de AutoYou aplicable |
| Código original, independiente y separable | La licencia que elijas, incluidas MIT o Apache-2.0 |
| Uso institucional o explotación comercial | [Acuerdo Enterprise](https://www.autoyou.me/enterprise/) independiente |

Conserva los avisos, identifica las compilaciones no oficiales y cumple las obligaciones de los componentes incluidos. Consulta [THIRD-PARTY-NOTICES.md](../../THIRD-PARTY-NOTICES.md) y la [política de marcas](../legal/trademark-policy.md).

OpenStorey destina al menos el 15 % de los ingresos netos que cumplen los requisitos al fondo de contribuidores, que se reparte según sus [reglas de revisión y elegibilidad](../contributors/contributor-pool.md). La [hoja de ruta de financiación](https://www.autoyou.me/donate/) incluye un hito de 5 millones de dólares para abrir el código del servidor; un hito no cambia por sí mismo la licencia de una copia que ya tienes. Detalles: [docs/legal/open-source-commitment.md](../legal/open-source-commitment.md).

<a id="support"></a>

## Soporte

- **Preguntas y ayuda con la instalación:** [SUPPORT.md](../../SUPPORT.md) y la [comunidad](https://www.autoyou.me/community/).
- **Errores y propuestas concretas:** [abre un issue](https://github.com/autoyou-ai/AutoYou-Server/issues).
- **Vulnerabilidades:** infórmalas en privado como se describe en [SECURITY.md](../../SECURITY.md). No uses issues públicos para vulnerabilidades no reveladas.

Los agentes se ejecutan con los permisos del servidor: revisa lo que hacen, activa solo lo que necesites y haz copias de seguridad.

Copyright &copy; 2026 OpenStorey LLC and contributors.

<div align="center">

[English](../../README.md) · **简体中文** · [繁體中文](README_zh-TW.md) · [日本語](README_ja.md) · [한국어](README_ko.md) · [हिन्दी](README_hi.md) · [Español](README_es.md) · [Français](README_fr.md) · [Deutsch](README_de.md) · [Português (Brasil)](README_pt-BR.md) · [Русский](README_ru.md) · [العربية](README_ar.md)

<img src="../images/logo.png" alt="AutoYou 标志" width="96" />

# AutoYou Server

### 在你已经拥有的电脑上，运行属于你自己的 AI 服务器。

**你不是产品，你是主人。**

[![Public checks](https://github.com/autoyou-ai/AutoYou-Server/actions/workflows/public-checks.yml/badge.svg?branch=main)](https://github.com/autoyou-ai/AutoYou-Server/actions/workflows/public-checks.yml)
[![Latest release](https://img.shields.io/github/v/release/autoyou-ai/AutoYou-Server?style=flat-square&color=22c55e)](https://github.com/autoyou-ai/AutoYou-Server/releases)
[![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-3776AB?style=flat-square&logo=python&logoColor=white)](../../requirements/README.md)
[![Platforms](https://img.shields.io/badge/platforms-Windows%20%7C%20macOS%20%7C%20Linux-536DFE?style=flat-square)](../../servers/README.md)
[![Built-in agents](https://img.shields.io/badge/built--in_agents-45-f97316?style=flat-square)](../../autoyou_agents/README.md)
[![Source-available license](https://img.shields.io/badge/license-source--available-8B5CF6?style=flat-square)](../../LICENSE)

**[官网](https://www.autoyou.me/) · [应用下载](https://www.autoyou.me/downloads/) · [生态](https://www.autoyou.me/ecosystem/) · [社区](https://www.autoyou.me/community/)**

[快速开始](#quick-start) · [工作原理](#how-autoyou-works) · [功能一览](#what-you-can-do) · [智能体](#agents) · [配对方式](#pairing-modes) · [安全](#security-modes-and-tiers) · [客户端](#supported-clients) · [常见问题](#faq) · [文档](#documentation)

<code>./run_autoyou.sh</code>

</div>

> [!IMPORTANT]
> 请从 [autoyou-ai/AutoYou-Server](https://github.com/autoyou-ai/AutoYou-Server) 或 [autoyou.me](https://www.autoyou.me/) 获取 AutoYou Server，并通过[支持的客户端](#supported-clients)中的应用商店链接获取应用。名称相似的仓库或复制的 README 并不代表下载是官方的。

<div align="center">
<br />
<img src="../images/hero.gif" alt="配对手机、与本地模型聊天、发起通话、浏览页面并查看电脑屏幕" width="860" />
<br /><br />
</div>

---

把你桌上的电脑变成服务你所有设备的私人 AI 服务器。AutoYou Server 在你的 Windows、macOS 或 Linux 电脑上运行 AI 模型和智能体、语音与视频通话、远程桌面、游戏，以及你自己的 Web 应用。无论在家还是外出，手机、其他电脑和浏览器都通过加密的点对点连接访问它。

你的对话、文件和智能体活动保存在这台电脑上，而不是托管的 AutoYou 服务中。用 Ollama 运行本地模型，聊天无需支付 token 费用；也可以接入你喜欢的云端服务商。配对一次，服务器提供的一切都在你的口袋里：聊天、免手动助手、多语言实时语音、你的桌面、你的游戏，以及你已经在运行的任何 Web 服务。

> **第一次使用？** 运行启动脚本，打开 **http://127.0.0.1:8001/**，设置向导会带你选择 AI、开启需要的功能并配对第一台设备。

<a id="quick-start"></a>

## 快速开始

你需要 Git 和 Python 3.11 或更高版本。部分可选功能需要更新的 Python 或特定平台，参见[依赖配置](../../requirements/README.md)。

### 1. 获取服务器

```bash
git clone https://github.com/autoyou-ai/AutoYou-Server.git
cd AutoYou-Server
```

### 2. 运行启动脚本

Windows：

```powershell
.\run_autoyou.bat --profile base
# Or select the Full profile with LAN access:
.\run_autoyou.bat --profile full --host 0.0.0.0
```

macOS 或 Linux：

```bash
./run_autoyou.sh --profile base
# Or select the Full profile with LAN access:
./run_autoyou.sh --profile full --host 0.0.0.0
```

启动脚本会创建 Python 环境、从软件包索引安装依赖，并可能下载模型和原生工具。请先阅读脚本，并在可以安全修改的环境中运行。

### 3. 打开控制台并配对设备

访问 **http://127.0.0.1:8001/**，设置一个唯一的服务器密码，然后按向导操作。它会询问你想用 AutoYou 做什么（聊天、免手动助手、通话、游戏、编程等），安装相应的智能体，并显示用于手机配对的二维码。

无人值守安装时，请通过环境变量或密钥管理器提供 `AUTOYOU_SERVER_PASSWORD`。不要在共享的命令、issue 或截图中暴露真实密码。

| 配置 (profile) | 增加的内容 |
| --- | --- |
| `base` | 核心服务器、本地管理、设备连接和云端服务商集成 |
| `local` | 本地模型辅助工具和设备端意图路由 |
| `recommended` | 消息应用和蓝牙集成依赖（省略 `--profile` 时的默认值） |
| `full` | 语音、浏览器和桌面自动化依赖 |

更新时在仓库目录中运行 `git pull`，然后重新运行启动脚本。发布说明见 [docs/releases/](../releases/)。

<a id="how-autoyou-works"></a>

## 工作原理

**服务器就是你自己的电脑，设备直接与它通信。** 应用和服务器通过 WebRTC 连接。完成简短的配对握手后，双方建立加密链路（DTLS，媒体使用 SRTP），承载聊天、语音、视频、屏幕共享、游戏输入和代理的网页流量。大多数家庭和移动网络无需在路由器上开放端口即可建立连接；网络过于严格时，会回退到转发同样加密数据包的中继。

```mermaid
flowchart LR
    app["AutoYou 应用<br/>手机、电脑、浏览器或树莓派"]
    server["AutoYou Server<br/>你的电脑"]
    rendezvous["会合服务（可选）<br/>Cloud Pair"]
    relay["中继（可选）<br/>转发加密数据包"]

    app <-->|"加密 WebRTC，网络允许时直连"| server
    app -.->|"信令"| rendezvous
    rendezvous -.->|"信令"| server
    app -.->|"回退路径"| relay
    relay -.->|"回退路径"| server
```

在服务器上，设备端意图路由器把每个请求交给合适的智能体或工具。Google ADK 运行原生智能体，也可以桥接 OpenClaw、Hermes Agent 和 Odysseus。LiteLLM 把模型调用发送到你电脑上的 Ollama 或你选择的云端服务商。语音在本地识别、在本地朗读，记忆保存在 SQLite 中。

<a id="what-you-can-do"></a>

## 功能一览

| 功能 | 带给你什么 |
| --- | --- |
| **AI 聊天** | 在任何已配对设备上与你自己的 AI 聊天，AutoYou 不限制消息数量 |
| **实时语音** | 与 AI 实时通话，本地语音识别，30 多种语言的语音回复 |
| **助手模式** | 在手机、电脑、树莓派或浏览器上使用唤醒词，例如 "Hey Jarvis" |
| **远程桌面** | 用手机查看并控制电脑，包括 Windows 锁屏界面 |
| **电脑视频** | 把电脑的屏幕、摄像头、视频文件或 API 输入的画面发送到通话中 |
| **游戏模式与 Game Studio** | 把电脑上的游戏串流到手机，或制作在手机上离线运行的游戏 |
| **Web 应用工厂** | 让智能体构建 Web 应用，并在所有已配对设备上打开 |
| **托管任意本地端口** | 把电脑上的任何 Web 服务（如 `localhost:3000`）分享给已配对设备 |
| **智能体** | 45 个内置智能体，涵盖笔记、文件、编程、媒体、浏览、训练等 |
| **MCP** | 在 ChatGPT、Codex、Claude、Antigravity、Cursor 和 VS Code 中使用 AutoYou 工具 |

功能取决于所选配置、硬件、权限和已配置的集成。桌面控制、游戏模式、录音和文件访问等强大能力在你开启之前都保持关闭。

### AI 聊天

在控制台的 **AI** 页面选择服务商：默认的 **Ollama + Agents**（本地模型，无需 API 密钥）、支持的 Mac 上的 **Apple Intelligence**、**Gemini**、**OpenRouter**，或通过 LiteLLM 接入 Anthropic、OpenAI、Mistral、DeepSeek、xAI 等，同时保留 AutoYou 的全部智能体和工具。也可以把 **OpenClaw**、**Hermes Agent** 或 **Odysseus** 作为主会话运行时。

AutoYou 不限制你发送的消息数量。使用本地模型时，聊天没有按消息计费，断网也能使用；你接入的云端服务商会按其自身方案计费，并能看到你发给它的提示词。内置模型库可以浏览、下载和切换本地模型，Model Picker 智能体会推荐适合你硬件的模型。

### 多语言实时语音

给你的 AI 打电话直接说话。语音识别在你的电脑上运行（默认构建使用 whisper.cpp，完整语音构建使用 faster-whisper），支持语音活动检测、开口即可打断，以及用于长段思考的"等我说完"保持功能。

- **说你的语言。** 将 **STT language** 设为 `auto` 并选择多语言模型，即可自动识别你说的语言。
- **用同一种语言听回复。** 默认的 **Auto TTS** 会检测回复的语言并选择匹配的系统语音。本地 Supertonic 3 模型支持 31 种语言，一次性下载约 401 MB 后可离线使用。
- **选择你的声音。** 系统语音、10 个 Supertonic 预设、在 Voice Training 中训练的自定义声音、中英文 EmotiVoice，或托管的 OpenAI 与 Azure 语音。

文字转语音只朗读给它的回复，不负责翻译；你选择的聊天模型也需要支持对话语言。

### 助手模式

为服务器设置一个唤醒词，例如 "Hey Jarvis"。在已配对的手机、电脑、树莓派、Chrome 扩展或管理页面附近说出它，之后说的话会发送给 AutoYou。在听到唤醒词之前，你说的任何话都不会被转写或发送给 AI。录制三遍、在服务器上几秒钟完成训练、测试后即可开启。视频通话中，唤醒词之后的请求可以附带最新的摄像头画面。详见[助手模式](../admin-ui/assistant-mode.mdx)。

### 远程桌面

从已配对的手机或桌面应用查看并控制电脑：选择显示器或单个应用窗口，移动鼠标、打字并使用修饰键。设备支持时优先使用 H.264。在 Windows 上，可选的 **AutoYou Secure Desktop Helper** 服务允许已配对的主人查看并操作锁屏和 UAC 提示，服务器还能在连接期间保持电脑唤醒。屏幕捕获和输入是两个独立权限，默认关闭；Peer Link 永远不提供远程桌面。

### 电脑视频

通话中，电脑可以从四种来源发送视频：**屏幕**、**摄像头**、你上传并播放的**视频文件**，或你自己的程序输入的 **API 画面**。也可以共享电脑的麦克风和声音。控制台的 **Interact** 页面可以显示已连接设备的实时摄像头画面，并在电脑扬声器上播放它们的麦克风声音，不经过 AI 处理，也不录制。

### 游戏模式与 Game Studio

- **游戏模式**把在电脑上渲染的游戏串流到已配对设备，设备的触摸、运动传感器、手柄、鼠标和键盘作为游戏输入传回。最多可定义四个带标签的虚拟按键。游戏模式是独立权限，同时会开启屏幕发送和远程桌面输入。
- **使用你自己的引擎。** 本地游戏引擎可以通过回环 WebSocket 读取经过认证的 `autoyou_game_v1` 输入帧。服务器在 `/api/game/unreal-plugin` 提供 Unreal 源码插件。
- **手机离线游戏。** Game Studio 智能体帮助你构建可移植的 Web 或 WASM 游戏，打包为最大 5 GiB 的 `.aygame` 文件，并通过 WebRTC 或已批准的 Peer Link 发送给客户端。每个游戏都有独立的设备端 SQLite 数据库，存档在更新后保留。

内置演示游戏 **Neon Horizon** 可以作为手机下载、在 Game Studio 页面中游玩，或从电脑串流。

### Web 应用工厂

描述一个应用，让智能体来构建。**Website** 智能体为智能体创建浏览器前端：简单起步模板，或按需使用 React、Angular，后端可选 Python/FastAPI、Node/TypeScript、Go 或 Rust。**Agent Builder** 搭建新智能体，**Coding** 和 **Pi** 智能体实现并测试代码。每个应用都会注册到本地网站服务，出现在已配对设备的浏览器中。**Hosting** 智能体介绍如何发布到公网：免费的临时 `/pair` 隧道或付费的持久 URL。

### 托管任意本地端口

已经在运行某个服务？直接分享。在 **Websites & Browser > Advertised Websites** 中添加 1 到 65535 之间的任意端口，填写标签和可选的转发 URL（如 `http://127.0.0.1:3000` 或局域网中的其他主机）。已配对客户端会发现它，并在自己这一侧的相同端口打开，因此仪表盘、开发服务器、家庭实验室工具，以及不支持子路径的应用都能正常工作。流量在加密的点对点链路内传输，无需在路由器上开放端口；已配对浏览器会获得 `viewer`、`editor` 或 `admin` 角色，限制它们能修改的内容。

<a id="agents"></a>

## 智能体

AutoYou 内置 45 个智能体。主智能体把每个请求路由到合适的专家，许多专家还在你的已配对设备上提供自己的网页。标记为 opt-in 的智能体在你于控制台 **Agents** 页面安装前保持未安装。

| 领域 | 智能体 |
| --- | --- |
| 日常 | Notes、Tasks、Notify、Memory、Persona、Page、Prompts、Location (opt-in) |
| 网页与自动化 | Internet、Assistant、Browser (opt-in)、Client Browser Control、Proxy (opt-in) |
| 媒体与创作 | Audio、TV、Media Generation (opt-in)、Game Studio |
| 编程与构建 | Coding、Pi、Agent Builder、Website、Skills、Files、CLI、Claude CLI、Claude Desktop、Codex Desktop、Antigravity、Prompt Builder（均为 opt-in） |
| 智能与模型 | Model Picker、OpenClaw、Hermes Agent（均为 opt-in） |
| 训练 | Data Collector、Fine-Tuning、Voice Training（均为 opt-in） |
| 系统与电脑 | Admin、Remote Desktop、Backup、Hosting (opt-in)、Windows Security (opt-in)、macOS Security (opt-in)、Education (opt-in) |
| 支持 AutoYou | Support Ads、Earnings、Donation |

把智能体包复制到 `autoyou_agents/` 即可添加你自己的智能体。完整说明见[智能体目录](../../autoyou_agents/README.md)。

<a id="pairing-modes"></a>

## 配对方式

| 方式 | 连接方式 | 典型用途 |
| --- | --- | --- |
| **Local Pair** | 通过局域网直连，由你自己的服务器处理信令 | 同一 Wi-Fi 或办公网络 |
| **Bluetooth Pair** | 通过低功耗蓝牙传递连接信息 | 首次设置附近的手机 |
| **Live Pair** | 手机和电脑互相展示动态二维码，无需网络通路 | 隔离或受限网络 |
| **一次性代码** | 在消息聊天中发送 `/pair`，返回短时有效的代码和链接 | 访客设备和不受信任的网络 |
| **Auto Pair** | 加密的配对消息通过你设置的 Telegram、Signal 或 WhatsApp 聊天传递 | 无需云账号的远程配对 |
| **Cloud Pair** | 可选的付费 AutoYou Cloud 会合服务跨越 NAT 找到你的电脑，并支持中继回退 | 外出或网络严格时 |

**Peer Link** 连接两个 AutoYou 应用，由接收方批准每个链接，且从不使用电脑的密码。**Lobbies** 是应用在附近通过 Wi-Fi 和蓝牙创建和加入的群组房间。详见[配对协议](../technical/pairing-protocols.md)。

<a id="security-modes-and-tiers"></a>

## 安全模式与安全等级

整个服务器使用同一种安全模式。新安装默认为 **Secure**。

| 模式 | 配对消息 | 设备配对需要 | 电脑上的数据 |
| --- | --- | --- | --- |
| **Normal** | 不加密 | 一次性代码 | 不变 |
| **Secure** | 用服务器密码加密 | 服务器密码 | 不变 |
| **Secure Professional** | 用服务器密码加密 | 密码加当前验证器代码 | 不变 |
| **Secure Professional Maximus** | 同 Secure Professional | 密码加当前验证器代码 | 会话、智能体数据、网站、笔记和设置均静态加密 |

在 Secure 模式下，配对消息使用口令认证密钥交换（基于 ristretto255 和 libsodium 的 CPace 系列构造）派生的密钥加密。转发它们的中继或消息桥无法读取内容，录下的交换过程也不能用于离线猜测密码。

**配对安全等级**决定一次聊天消息配对需要多少步骤：**Quick Pairing (B)** 允许客户端用一条消息完成配对，兼容性最好；**Enhanced Pairing (A)** 要求每个客户端先完成一次额外的 hello 往返，使猜测更难。Local Pair、Bluetooth Pair 和 Cloud Pair 始终使用增强流程。

已配对设备的浏览器会获得 `viewer`、`editor` 或 `admin` 角色。凭据和安全设置只能在电脑本机，或直接在管理端口上打开的 HTTPS 管理会话中修改。加密保护的是连接本身，不保护终端设备、智能体权限或第三方服务。详见[安全模式](../security/security-modes.md)和 [SECURITY.md](../../SECURITY.md)。

<a id="supported-clients"></a>

## 支持的客户端

AutoYou Server 运行在你的电脑上，应用从其他任何地方访问它。客户端应用单独发布，其源代码不在本仓库中。

| 平台 | 获取方式 |
| --- | --- |
| iPhone 和 iPad | [App Store](https://apps.apple.com/us/app/autoyou/id6760363728) |
| Android | [Google Play](https://play.google.com/store/apps/details?id=com.autoyou.app) |
| Windows | [Microsoft Store](https://apps.microsoft.com/detail/9mw8l2wfw7wv?hl=en-US&gl=US) |
| macOS 和 Linux | [下载](https://www.autoyou.me/downloads/) |
| 浏览器 | Chrome 扩展和 Web 客户端，见[生态页面](https://www.autoyou.me/ecosystem/) |
| 树莓派 | 用于助手模式的 Python 语音客户端（[设置](../admin-ui/assistant-mode.mdx)） |

手机应用还内置小型本地 AI（视设备而定为 LFM 2.5 350M、Gemini Nano 或 Apple Intelligence），没有电脑时也能聊天。

<a id="mcp"></a>

## 在编程智能体中使用 AutoYou

本地 MCP 适配器可以把受支持的 AutoYou 工具连接到 ChatGPT、Codex、Claude Code、Claude Desktop、Google Antigravity、Cursor 和 VS Code。在 **Admin UI > Messaging > AutoYou MCP** 中启用 MCP 并下载 `autoyou-mcp.env`，然后按照[英文 README 中的步骤](../../README.md#use-autoyou-from-your-coding-agent)为每个客户端完成设置。

<a id="faq"></a>

## 常见问题

<details>
<summary><strong>需要云账号或 VPN 吗？</strong></summary>

不需要。Local Pair、Bluetooth Pair、Live Pair、一次性代码和 Auto Pair 都无需 AutoYou 账号。Cloud Pair 是可选的付费辅助服务。如果你已经在使用 Tailscale 等 VPN，AutoYou 也能通过它工作。

</details>

<details>
<summary><strong>AI 聊天真的不限量吗？</strong></summary>

AutoYou 不限制消息数量。使用本地 Ollama 模型时，每条消息都不收费，速度取决于你的电脑。你接入的云端服务商有自己的价格和限制。

</details>

<details>
<summary><strong>AutoYou 会上传我的文件或对话吗？</strong></summary>

它们保存在你的电脑上。数据只会通过你开启的服务离开，例如云端模型服务商、消息桥或公网隧道，每项服务都有自己的条款。

</details>

<a id="documentation"></a>

## 文档

- [快速开始](../quickstart.mdx) · [架构](../architecture/) · [安全与隐私](../security/) · [管理控制台](../admin-ui/)
- [智能体目录](../../autoyou_agents/README.md) · [智能体参考](../agents/) · [智能体框架](../agent-framework/)
- [HTTP 与 WebSocket API](../api-reference/) · [指南](../guides/) · [配对指南](../../guides/README.md)

## 参与贡献

欢迎你为 AutoYou 出力：构建智能体、游戏、扩展或集成，改进安装、无障碍、平台支持、翻译和文档，或分享演示和指南。提交前请阅读 [CONTRIBUTING.md](../../CONTRIBUTING.md) 和[行为准则](../../CODE_OF_CONDUCT.md)。

## 许可证

AutoYou Server 以[服务器许可证](../../LICENSE)源码可见（source-available）的方式提供，目前不是 OSI 认可的开源项目，以 `LICENSE` 中的定义为准。个人使用、本地构建和免费分享在许可证条件下允许；机构使用或商业利用需要单独的[企业协议](https://www.autoyou.me/enterprise/)。详见[英文 README](../../README.md#license) 和 [THIRD-PARTY-NOTICES.md](../../THIRD-PARTY-NOTICES.md)。

Copyright &copy; 2026 OpenStorey LLC and contributors.

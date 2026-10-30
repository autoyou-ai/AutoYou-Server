<div align="center">

[English](../../README.md) · [简体中文](README_zh.md) · [繁體中文](README_zh-TW.md) · [日本語](README_ja.md) · [한국어](README_ko.md) · [हिन्दी](README_hi.md) · [Español](README_es.md) · [Français](README_fr.md) · [Deutsch](README_de.md) · **Português (Brasil)** · [Русский](README_ru.md) · [العربية](README_ar.md)

<img src="../images/logo.png" alt="Logotipo do AutoYou" width="96" />

# AutoYou Server

### Seu próprio servidor de IA, no computador que você já tem.

**Você não é o produto. Você é o dono.**

[![Public checks](https://github.com/autoyou-ai/AutoYou-Server/actions/workflows/public-checks.yml/badge.svg?branch=main)](https://github.com/autoyou-ai/AutoYou-Server/actions/workflows/public-checks.yml)
[![Latest release](https://img.shields.io/github/v/release/autoyou-ai/AutoYou-Server?style=flat-square&color=22c55e)](https://github.com/autoyou-ai/AutoYou-Server/releases)
[![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-3776AB?style=flat-square&logo=python&logoColor=white)](../../requirements/README.md)
[![Platforms](https://img.shields.io/badge/platforms-Windows%20%7C%20macOS%20%7C%20Linux-536DFE?style=flat-square)](../../servers/README.md)
[![Built-in agents](https://img.shields.io/badge/built--in_agents-45-f97316?style=flat-square)](../../autoyou_agents/README.md)
[![Source-available license](https://img.shields.io/badge/license-source--available-8B5CF6?style=flat-square)](../../LICENSE)

**[Site](https://www.autoyou.me/) · [Apps](https://www.autoyou.me/downloads/) · [Ecossistema](https://www.autoyou.me/ecosystem/) · [Comunidade](https://www.autoyou.me/community/)**

[Início rápido](#quick-start) · [Como funciona](#how-autoyou-works) · [O que você pode fazer](#what-you-can-do) · [Agentes](#agents) · [Pareamento](#pairing-modes) · [Segurança](#security-modes-and-tiers) · [Clientes](#supported-clients) · [Perguntas frequentes](#faq) · [Documentação](#documentation)

<code>./run_autoyou.sh</code>

</div>

> [!IMPORTANT]
> Baixe o AutoYou Server em [autoyou-ai/AutoYou-Server](https://github.com/autoyou-ai/AutoYou-Server) ou [autoyou.me](https://www.autoyou.me/), e os apps pelos links das lojas em [Clientes compatíveis](#supported-clients). Um repositório com nome parecido ou um README copiado não torna um download oficial.

<div align="center">
<br />
<img src="../images/hero.gif" alt="Parear um celular, conversar com um modelo local, fazer uma chamada, abrir uma página e ver a tela do computador" width="860" />
<br /><br />
</div>

---

Transforme o computador da sua mesa em um servidor de IA privado para todos os seus dispositivos. O AutoYou Server roda seus modelos e agentes de IA, chamadas de voz e vídeo, área de trabalho remota, jogos e seus próprios apps web no seu Windows, macOS ou Linux. Seu celular, outros computadores e navegadores se conectam a ele por conexões ponto a ponto criptografadas, em casa ou fora.

Suas conversas, arquivos e a atividade dos agentes ficam nesse computador, não em um serviço hospedado do AutoYou. Rode modelos locais com o Ollama para conversar sem conta de tokens, ou conecte o provedor de nuvem que preferir. Pareie um dispositivo uma vez e tudo o que o servidor oferece fica no seu bolso: chat, um assistente mãos livres, voz em tempo real em vários idiomas, sua área de trabalho, seus jogos e qualquer serviço web que você já rode.

> **Primeira vez?** Rode o script de inicialização, abra **http://127.0.0.1:8001/** e a configuração guiada ajuda você a escolher sua IA, ativar os recursos que quiser e parear o primeiro dispositivo.

<a id="quick-start"></a>

## Início rápido

Você precisa do Git e do Python 3.11 ou mais recente. Alguns recursos opcionais exigem um Python mais novo ou uma plataforma específica; veja os [perfis de dependências](../../requirements/README.md).

### 1. Baixe o servidor

```bash
git clone https://github.com/autoyou-ai/AutoYou-Server.git
cd AutoYou-Server
```

### 2. Rode o script de inicialização

Windows:

```powershell
.\run_autoyou.bat --profile base
# Or select the Full profile with LAN access:
.\run_autoyou.bat --profile full --host 0.0.0.0
```

macOS ou Linux:

```bash
./run_autoyou.sh --profile base
# Or select the Full profile with LAN access:
./run_autoyou.sh --profile full --host 0.0.0.0
```

O script cria um ambiente Python, instala dependências dos índices de pacotes e pode baixar modelos e ferramentas nativas. Leia os scripts antes e rode-os em um ambiente que você possa modificar com segurança.

### 3. Abra o console e pareie um dispositivo

Acesse **http://127.0.0.1:8001/**, escolha uma senha de servidor única e siga a configuração. Ela pergunta para que você quer usar o AutoYou (chat, assistente mãos livres, chamadas, jogos, programação e mais), instala os agentes correspondentes e mostra um QR code para parear seu celular.

Em uma primeira configuração sem supervisão, forneça `AUTOYOU_SERVER_PASSWORD` pelo ambiente ou por um gerenciador de segredos. Não coloque senhas reais em comandos compartilhados, issues ou capturas de tela.

| Perfil | Adiciona |
| --- | --- |
| `base` | Servidor principal, administração local, conexões de dispositivos e integrações com provedores de nuvem |
| `local` | Auxiliares para modelos locais e roteamento de intenção no dispositivo |
| `recommended` | Dependências de mensageria e Bluetooth (padrão quando `--profile` é omitido) |
| `full` | Dependências de voz, navegador e automação da área de trabalho |

Os perfis só escolhem dependências; as integrações ainda precisam ser configuradas. `--skip-nuitka` e `--skip-backend-compile` mantêm as bibliotecas embutidas do iniciador, então atualizações de segurança de dependências em apps empacotados exigem uma recompilação completa.

Para atualizar, rode `git pull` na sua cópia e inicie o script de novo. As notas de versão ficam em [docs/releases/](../releases/).

<a id="how-autoyou-works"></a>

## Como o AutoYou funciona

**O servidor é o seu próprio computador, e os dispositivos falam direto com ele.** Apps e servidor se conectam com WebRTC. Depois de um breve handshake de pareamento, abrem um link criptografado (DTLS, com SRTP para mídia) que carrega chat, voz, vídeo, compartilhamento de tela, controles de jogo e tráfego web via proxy. A maioria das redes domésticas e móveis cria esse link sem abrir portas no roteador. Quando a rede é restrita demais, a conexão passa para um relay que encaminha os mesmos pacotes criptografados.

```mermaid
flowchart LR
    app["App AutoYou<br/>celular, computador, navegador ou Pi"]
    server["AutoYou Server<br/>seu computador"]
    rendezvous["Ponto de encontro (opcional)<br/>Cloud Pair"]
    relay["Relay (opcional)<br/>encaminha pacotes criptografados"]

    app <-->|"WebRTC criptografado, direto quando a rede permite"| server
    app -.->|"sinalização"| rendezvous
    rendezvous -.->|"sinalização"| server
    app -.->|"caminho alternativo"| relay
    relay -.->|"caminho alternativo"| server
```

No servidor, um roteador de intenção local envia cada pedido ao agente ou ferramenta certos. O Google ADK roda os agentes nativos, e OpenClaw, Hermes Agent e Odysseus podem ser conectados. O LiteLLM envia as chamadas de modelo ao Ollama na sua máquina ou ao provedor de nuvem que você escolher. A fala é reconhecida e sintetizada localmente, e a memória fica no SQLite.

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="../images/architecture-dark.png" />
  <img src="../images/architecture-light.png" alt="Arquitetura do AutoYou: os apps se conectam ao servidor no seu computador por um link WebRTC criptografado; as mensagens do Auto Pair passam pelo Telegram, Signal ou WhatsApp; os clientes MCP se conectam pela ponte MCP; o roteador de intenções liga os ambientes de agentes, os apps de agentes, a voz local, a memória e os modelos via LiteLLM com Ollama ou provedores em nuvem opcionais." width="100%" />
</picture>

O diagrama é gerado por [scripts/build_architecture_diagram.py](../../scripts/build_architecture_diagram.py). As caixas tracejadas são complementos opcionais ou projetos conectados.

<a id="what-you-can-do"></a>

## O que você pode fazer

| Recurso | O que oferece |
| --- | --- |
| **Chat com IA** | Converse com sua própria IA de qualquer dispositivo pareado; o AutoYou não limita mensagens |
| **Voz em tempo real** | Chamadas ao vivo com sua IA, reconhecimento de voz local e respostas faladas em mais de 30 idiomas |
| **Modo assistente** | Uma palavra de ativação como "Hey Jarvis" no celular, computador, Raspberry Pi ou navegador |
| **Área de trabalho remota** | Veja e controle o computador pelo celular, inclusive a tela de bloqueio do Windows |
| **Vídeo do computador** | Envie para uma chamada a tela, uma câmera, um arquivo de vídeo ou quadros via API |
| **Modo jogo e Game Studio** | Transmita jogos do computador para o celular ou crie jogos que rodam offline nele |
| **Fábrica de apps web** | Peça a um agente para criar um app web e abra-o em todos os dispositivos pareados |
| **Hospede qualquer porta local** | Compartilhe qualquer serviço web do computador, como `localhost:3000`, com seus dispositivos |
| **Agentes** | 45 agentes integrados para notas, arquivos, programação, mídia, navegação, treinamento e mais |
| **MCP** | Use ferramentas do AutoYou no ChatGPT, Codex, Claude, Antigravity, Cursor e VS Code |
| **Mensagens** | Fale com seu servidor pelo Telegram, Signal e WhatsApp com conectores que você controla |
| **Memória** | Memória em SQLite e um diário de persona privado, com Cognee como complemento opcional |

Os recursos dependem do perfil escolhido, do hardware, das permissões e das integrações configuradas. Capacidades poderosas, como controle da área de trabalho, modo jogo, gravação e acesso a arquivos, ficam desligadas até você ativá-las.

<a id="ai-chat"></a>

### Chat com IA

Escolha um provedor em **AI** no console: **Ollama + Agents** por padrão (modelo local, sem chave de API), **Apple Intelligence** em Macs compatíveis, **Gemini**, **OpenRouter** ou outros provedores via LiteLLM (Anthropic, OpenAI, Mistral, DeepSeek, xAI e mais), sempre com os agentes e ferramentas do AutoYou. Você também pode usar **OpenClaw**, **Hermes Agent** ou **Odysseus** como runtime principal da sessão.

O AutoYou não limita quantas mensagens você envia. Com um modelo local, o chat não tem custo por mensagem e funciona sem internet. Um provedor de nuvem que você conectar cobra pelo próprio plano e vê os prompts que você envia. A biblioteca de modelos integrada permite explorar, baixar e trocar modelos locais, e o agente Model Picker recomenda um adequado ao seu hardware.

Modelos locais pequenos e quantizados recebem ajuda para continuar precisos: um classificador de intenções offline envia pedidos comuns direto ao agente certo, e um modo de roteamento compacto mantém a lista de ferramentas curta. Veja [como os modelos pequenos continuam precisos](../../autoyou_agents/README.md#small-local-models).

<a id="realtime-voice-in-many-languages"></a>

### Voz em tempo real em vários idiomas

Ligue para sua IA e fale. O reconhecimento de voz roda no seu computador (whisper.cpp na versão padrão, faster-whisper nas versões completas de voz), com detecção de atividade de voz, interrupção assim que você começa a falar e uma pausa **Espere eu terminar de falar** para pensamentos longos.

- **Fale no seu idioma.** Deixe **STT language** em `auto` e um modelo multilíngue detecta o idioma falado.
- **Ouça a resposta nesse idioma.** O **Auto TTS**, padrão, detecta o idioma da resposta e escolhe uma voz do sistema compatível. O modelo local Supertonic 3 fala 31 idiomas e funciona offline após um único download de cerca de 401 MB.
- **Escolha sua voz.** Vozes do sistema, dez vozes predefinidas do Supertonic, uma voz personalizada treinada no app Voice Training, EmotiVoice para chinês e inglês, ou vozes hospedadas da OpenAI e da Azure.

A síntese de voz fala a resposta que recebe; ela não traduz. O modelo de chat escolhido também precisa suportar o idioma da conversa.

<a id="assistant-mode"></a>

### Modo assistente

Dê ao seu servidor uma palavra de ativação, como "Hey Jarvis". Diga-a perto de um celular, computador, Raspberry Pi, da extensão do Chrome ou da página de administração, e o que você disser em seguida vai para o AutoYou. Até a palavra ser ouvida, nada do que você diz é transcrito nem enviado à IA. Grave-a três vezes, treine no servidor em poucos segundos, teste e ative. Em uma videochamada, o pedido após a palavra de ativação pode levar a imagem mais recente da câmera. Veja [Modo assistente](../admin-ui/assistant-mode.mdx).

<a id="remote-desktop"></a>

### Área de trabalho remota

Veja e controle o computador a partir de um celular ou app de desktop pareado: escolha um monitor ou uma única janela, mova o mouse, digite e use teclas modificadoras. O H.264 é preferido quando o dispositivo suporta. No Windows, o serviço opcional **AutoYou Secure Desktop Helper** permite ao dono pareado ver e usar a tela de bloqueio e os avisos do UAC, e o servidor pode manter o computador acordado durante a conexão. Captura de tela e entrada são permissões separadas, desligadas por padrão, e a área de trabalho remota nunca é oferecida por um Peer Link.

<a id="computer-video"></a>

### Vídeo do computador

Em uma chamada, o computador pode enviar vídeo de quatro fontes: a **tela**, uma **câmera**, um **arquivo de vídeo** que você envia e reproduz, ou **quadros via API** alimentados pelo seu próprio programa. O microfone e o som do computador também podem ser compartilhados. No console, **Interact** mostra ao vivo as câmeras dos dispositivos conectados e pode tocar os microfones deles nos alto-falantes do computador, sem processamento de IA e sem gravação.

<a id="game-mode-and-game-studio"></a>

### Modo jogo e Game Studio

- **O modo jogo** transmite para um dispositivo pareado um jogo renderizado no computador, e o toque, os sensores de movimento, o controle, o mouse e o teclado do dispositivo voltam como comandos do jogo. Você pode definir até quatro botões virtuais com nome. O modo jogo é uma permissão própria e também ativa o envio de tela e a entrada da área de trabalho remota.
- **Use sua própria engine.** Uma engine de jogo local pode ler, por um WebSocket local, quadros de entrada autenticados no protocolo `autoyou_game_v1`. Para Unreal, o servidor oferece um plugin com código-fonte em `/api/game/unreal-plugin`.
- **Jogos offline para o celular.** O agente Game Studio ajuda você a criar jogos web ou WASM portáteis, empacota-os como arquivos `.aygame` de até 5 GiB e os envia aos clientes por WebRTC ou por um Peer Link aprovado. Cada jogo tem seu próprio banco SQLite no dispositivo, e o progresso salvo sobrevive às atualizações.

**Neon Horizon** é a demo integrada: jogue como download no celular, na página do Game Studio ou transmitido do computador.

<a id="web-app-factory"></a>

### Fábrica de apps web

Descreva um app e deixe os agentes construí-lo. O agente **Website** cria uma interface web para um agente: um modelo simples, ou React ou Angular quando você pedir, com back-end em Python/FastAPI, Node/TypeScript, Go ou Rust. O **Agent Builder** cria a estrutura de um novo agente, e os agentes **Coding** e **Pi** implementam e testam o código. Cada app é registrado no serviço web local e aparece no navegador dos seus dispositivos pareados. O agente **Hosting** explica como publicar em um link público: um túnel temporário gratuito `/pair` ou uma URL persistente paga.

<a id="host-any-local-port"></a>

### Hospede qualquer porta local

Já tem algo rodando? Compartilhe. Em **Websites & Browser > Advertised Websites**, adicione qualquer porta de 1 a 65535 com um rótulo e uma URL de encaminhamento opcional, como `http://127.0.0.1:3000` ou outro host da sua rede. Os clientes pareados descobrem e abrem na mesma porta do lado deles, então painéis, servidores de desenvolvimento, ferramentas de homelab e apps que não foram feitos para rodar sob um subcaminho continuam funcionando. O tráfego passa dentro do link criptografado, então não é preciso abrir a porta no roteador, e navegadores pareados recebem o papel `viewer`, `editor` ou `admin`, que limita o que podem alterar.

<a id="agents"></a>

## Agentes

O AutoYou vem com 45 agentes. O agente principal envia cada pedido ao especialista adequado, e muitos especialistas têm a própria página web nos seus dispositivos pareados. Agentes marcados como opt-in só são instalados quando você os instala em **Agents** no console.

| Área | Agentes |
| --- | --- |
| Dia a dia | Notes, Tasks, Notify, Memory, Persona, Page, Prompts, Location (opt-in) |
| Web e automação | Internet, Assistant, Browser (opt-in), Client Browser Control, Proxy (opt-in) |
| Mídia e criação | Audio, TV, Media Generation (opt-in), Game Studio |
| Programação e construção | Coding, Pi, Agent Builder, Website, Skills, Files, CLI, Claude CLI, Claude Desktop, Codex Desktop, Antigravity, Prompt Builder (todos opt-in) |
| Inteligência e modelos | Model Picker, OpenClaw, Hermes Agent (todos opt-in) |
| Treinamento | Data Collector, Fine-Tuning, Voice Training (todos opt-in) |
| Sistema e computador | Admin, Remote Desktop, Backup, Hosting (opt-in), Windows Security (opt-in), macOS Security (opt-in), Education (opt-in) |
| Apoiar o AutoYou | Support Ads, Earnings, Donation |

Adicione os seus copiando um pacote para `autoyou_agents/`. Todos os detalhes estão no [catálogo de agentes](../../autoyou_agents/README.md).

A [referência de agentes gerada](../agents/) lista cada ferramenta que cada agente declara.

<a id="pairing-modes"></a>

## Modos de pareamento

| Método | Como conecta | Uso típico |
| --- | --- | --- |
| **Local Pair** | Direto pela sua LAN; seu próprio servidor responde à sinalização | Mesma Wi-Fi ou rede do escritório |
| **Bluetooth Pair** | Os dados de conexão são passados por Bluetooth Low Energy | Primeira configuração de um celular próximo |
| **Live Pair** | Celular e computador mostram QR codes animados um ao outro, sem caminho de rede | Redes isoladas ou bloqueadas |
| **Código de uso único** | `/pair` em um chat de mensagens devolve um código e um link temporários | Dispositivos de convidados e redes não confiáveis |
| **Auto Pair** | Uma mensagem de pareamento criptografada passa por um chat do Telegram, Signal ou WhatsApp que você configura | Parear à distância sem conta na nuvem |
| **Cloud Pair** | Um ponto de encontro opcional e pago do AutoYou Cloud encontra seu computador através de NAT, com relay de reserva | Fora de casa ou em redes restritas |

O **Peer Link** conecta um app AutoYou a outro; o app que recebe aprova cada link, e a senha do computador nunca é usada. **Lobbies** são salas de grupo que os apps criam e entram por perto via Wi-Fi e Bluetooth. Veja os [protocolos de pareamento](../technical/pairing-protocols.md).

<a id="security-modes-and-tiers"></a>

## Modos e níveis de segurança

Um único modo de segurança vale para o servidor inteiro. Uma instalação nova começa em **Secure**.

| Modo | Mensagens de pareamento | O que um dispositivo precisa | Dados no computador |
| --- | --- | --- | --- |
| **Normal** | Sem criptografia | Um código de uso único | Sem alteração |
| **Secure** | Criptografadas com a senha do servidor | A senha do servidor | Sem alteração |
| **Secure Professional** | Criptografadas com a senha do servidor | A senha e um código atual do autenticador | Sem alteração |
| **Secure Professional Maximus** | Igual ao Secure Professional | A senha e um código atual do autenticador | Sessões, dados de agentes, sites, notas e configurações criptografados em repouso |

Nos modos Secure, as mensagens de pareamento são criptografadas com uma chave obtida por troca de chaves autenticada por senha (uma construção da família CPace sobre ristretto255 com libsodium). Um relay ou ponte de mensagens que as transporte não consegue lê-las, e uma troca gravada não serve para testar senhas offline.

**O nível de segurança do pareamento** define quanto trabalho um pareamento por mensagem de chat exige: **Quick Pairing (B)** permite parear com uma única mensagem e tem a melhor compatibilidade; **Enhanced Pairing (A)** exige de cada cliente uma ida e volta hello extra antes, o que dificulta adivinhações. Local Pair, Bluetooth Pair e Cloud Pair sempre usam o fluxo reforçado.

O navegador de um dispositivo pareado recebe o papel `viewer`, `editor` ou `admin`. Credenciais e configurações de segurança só podem ser alteradas no próprio computador ou em uma sessão de administração HTTPS aberta diretamente na porta de administração. A criptografia protege a conexão, não os dispositivos, as permissões dos agentes nem os serviços de terceiros. Veja [modos de segurança](../security/security-modes.md) e [SECURITY.md](../../SECURITY.md).

<details>
<summary><strong>O que cada camada protege e onde ela termina</strong></summary>

| Camada | O que o AutoYou faz |
| --- | --- |
| Exposição de rede | Escuta em `127.0.0.1` por padrão. Escutar na sua rede local é uma configuração explícita. |
| Acesso | O console de administração é protegido pela senha do servidor, com códigos opcionais de um app autenticador (TOTP). |
| Pareamento | Troca de chaves autenticada por senha, códigos de uso único e aprovação de cada link no Peer Link. |
| Transporte | WebRTC DTLS e SRTP entre as duas pontas, inclusive quando um relay encaminha os pacotes. |
| Dados em repouso | As credenciais ficam em um cofre de chaves criptografado; o Maximus também criptografa os dados gerenciados pelo AutoYou. |
| Dependências | Manifestos com lockfile, verificação de avisos de segurança e actions de CI fixadas. |

A criptografia protege a conexão. Ela não protege tudo ao redor:

- **Dispositivos.** Um malware no computador servidor ou em um dispositivo pareado tem o acesso que esse dispositivo tiver.
- **Agentes.** Os agentes rodam com as permissões do servidor. Automação de navegador, acesso a arquivos, controle da área de trabalho e execução de comandos são poderosos; ative só o que precisar e revise o que fazem.
- **Terceiros.** Provedores de modelos em nuvem, pontes de mensagens e túneis públicos têm seus próprios fluxos de dados e termos. Revise-os antes de ativar.
- **Operação.** Manter um servidor deixa você responsável por atualizações, backups e credenciais.

</details>

<details>
<summary><strong>Quais serviços externos participam</strong></summary>

Você opera um único servidor: o seu. Nenhum servidor de aplicação operado pelo AutoYou é necessário para rodar seu assistente ou guardar seus dados, e o servidor funciona numa rede local sem conexão com o AutoYou Cloud ([detalhes](../architecture/cloud-and-remote.mdx)). Todo o resto é um padrão que você pode mudar ou um auxiliar opcional:

| Componente | Roda em | O que pode ver |
| --- | --- | --- |
| AutoYou Server: agentes, modelos, armazenamento, console | Seu computador | Seus dados. É uma das pontas da conexão. |
| Apps e clientes de navegador | Seus dispositivos | O que você compartilhar com eles |
| Servidores STUN para descobrir o endereço (ativos por padrão) | Os servidores STUN públicos do Google, a menos que você os troque | O IP público e a porta de onde uma conexão WebRTC aparece |
| Ponto de encontro do Cloud Pair (opcional) | AutoYou Cloud | E-mail da conta, ID do dispositivo, dados temporários de pareamento e metadados de rede |
| Relay de reserva (opcional) | AutoYou Cloud | Pacotes criptografados e metadados de rede |
| Notificações push para acordar apps do celular (opcional) | Serviços push da Apple e do Google | Que foi pedido para acordar o app |
| Verificação de atualizações (ativa quando uma conta está vinculada) | Canal de atualizações do AutoYou Cloud | Um pedido de atualização autenticado com a conta vinculada |
| Provedores de modelos em nuvem (opcional) | O provedor que você escolher | Os prompts que você envia a ele |
| Pontes do Telegram, Signal e WhatsApp (opcional) | Esses serviços | As mensagens que passam por eles, conforme os termos deles |
| Túnel público para sites (opcional) | O provedor do túnel | O tráfego para a URL pública. Não é ponta a ponta. |

**O STUN público fica ativo por padrão**, inclusive no Local Pair. Para que essa consulta não saia para a internet, defina `AUTOYOU_DISABLE_PUBLIC_STUN=1` antes de iniciar o servidor, ou aponte para seu próprio servidor STUN e TURN com `AUTOYOU_LOCAL_STUNTURN_HOST` (mais `AUTOYOU_LOCAL_TURN_USERNAME` e `AUTOYOU_LOCAL_TURN_PASSWORD` para TURN). Sem nenhum servidor STUN ou TURN, conexões através de NAT podem falhar.

</details>

<a id="supported-clients"></a>

## Clientes compatíveis

O AutoYou Server roda no seu computador; os apps chegam até ele de qualquer outro lugar. Os apps cliente são distribuídos separadamente e o código deles não faz parte deste repositório.

| Plataforma | Onde obter |
| --- | --- |
| iPhone e iPad | [App Store](https://apps.apple.com/us/app/autoyou/id6760363728) |
| Android | [Google Play](https://play.google.com/store/apps/details?id=com.autoyou.app) |
| Windows | [Microsoft Store](https://apps.microsoft.com/detail/9mw8l2wfw7wv?hl=en-US&gl=US) |
| macOS e Linux | [Downloads](https://www.autoyou.me/downloads/) |
| Navegador | Extensão do Chrome e cliente web ([página do ecossistema](https://www.autoyou.me/ecosystem/)) |
| Raspberry Pi | Cliente de voz em Python para o modo assistente ([configuração](../admin-ui/assistant-mode.mdx)) |

Os apps de celular também trazem uma pequena IA local (LFM 2.5 350M, Gemini Nano ou Apple Intelligence, conforme o aparelho) para conversar mesmo sem computador.

| Modos de pareamento | Converse com sua própria IA | Lobbies próximos | IA local no celular |
| --- | --- | --- | --- |
| <img src="../images/apps/android-pairing.png" alt="Modos de pareamento: Local, Auto, Bluetooth, Cloud, código de uso único e Peer Link" width="200"> | <img src="../images/apps/ios-chat.png" alt="Conversa com um agente de IA rodando no seu próprio computador" width="200"> | <img src="../images/apps/ios-lobbies.png" alt="Descobrir e criar Lobbies próximos por Wi-Fi e Bluetooth" width="200"> | <img src="../images/apps/android-local-ai.png" alt="Conversa com IA local no celular" width="200"> |

- **Lobbies.** Crie ou entre em salas próximas por Wi-Fi e Bluetooth, com chat e áudio ou vídeo para até seis convidados mais o anfitrião.
- **Peer Link.** Conecte um app a outro. O app que recebe aprova cada link, e as respostas opcionais de IA usam só o que você contou a ele.
- **Seu computador, em todo lugar.** Chat, chamadas, área de trabalho remota, modo jogo, sites de agentes e portas publicadas em cada dispositivo que você parear.

<a id="mcp"></a>
<a id="use-autoyou-from-your-coding-agent"></a>

## Use o AutoYou a partir do seu agente de programação

Conecte os recursos compatíveis com MCP do ChatGPT, Codex, Claude Code ou Claude Desktop, Google Antigravity, Cursor e VS Code às ferramentas compatíveis do AutoYou. O adaptador MCP local é um processo separado; o servidor não o instala nesses clientes, e as chamadas MCP expõem as ferramentas que o adaptador suporta, não acesso arbitrário aos seus arquivos nem todos os recursos do AutoYou.

1. Inicie o AutoYou Server e abra **http://127.0.0.1:8001/**.
2. Em **Admin UI > Messaging > AutoYou MCP**, ative o MCP, gere o token privado do adaptador e baixe `autoyou-mcp.env`. Guarde esse arquivo em sigilo.
3. Instale o adaptador, mantido separadamente, em um ambiente Python dedicado:

   ```powershell
   git clone https://github.com/autoyou-ai/AutoYou.git
   cd AutoYou
   py -3 -m venv .venv-mcp
   .\.venv-mcp\Scripts\python.exe -m pip install -e .\plugins\autoyou-mcp
   ```

   No macOS ou Linux, use `python3 -m venv .venv-mcp` e `.venv-mcp/bin/python -m pip install -e ./plugins/autoyou-mcp`.

Para clientes que iniciam um processo MCP local, use este comando com caminhos absolutos:

```text
<path-to-.venv-mcp-python> -m autoyou_mcp --env-file <path-to-autoyou-mcp.env> --transport stdio
```

<details>
<summary><strong>Adicione o AutoYou a cada cliente</strong></summary>

Para clientes que usam o formato JSON `mcpServers`, a entrada fica assim. Troque os dois caminhos de exemplo e escape as barras invertidas do Windows se usá-las em JSON:

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

**O ChatGPT na web** usa uma conexão MCP remota, não um processo `stdio` local. Inicie o adaptador com `--transport streamable-http` e conecte seu endpoint local `http://127.0.0.1:8071/mcp` pelo OpenAI Secure MCP Tunnel. O ChatGPT exige o modo desenvolvedor e o acesso de workspace ou administrador correspondente, e a OpenAI limita as ações MCP completas conforme o plano. Siga a [configuração do AutoYou MCP para o ChatGPT](https://github.com/autoyou-ai/AutoYou/blob/main/plugins/autoyou-mcp/README.md#connect-chatgpt-web) e o [guia de apps MCP da OpenAI](https://help.openai.com/en/articles/12584461-developer-mode-and-mcp-apps-in-chatgpt).

**Codex CLI, o app de desktop e a extensão de IDE** compartilham a configuração MCP do host do Codex:

```powershell
codex mcp add autoyou -- "C:/path/to/AutoYou/.venv-mcp/Scripts/python.exe" -m autoyou_mcp --env-file "C:/path/to/autoyou-mcp.env" --transport stdio
codex mcp list
```

Ou adicione o `command` e os `args` equivalentes em `[mcp_servers.autoyou]` no `~/.codex/config.toml`. Veja o [guia MCP do Codex](https://learn.chatgpt.com/docs/extend/mcp?surface=cli).

**Claude Code:**

```powershell
claude mcp add --transport stdio autoyou -- "C:/path/to/AutoYou/.venv-mcp/Scripts/python.exe" -m autoyou_mcp --env-file "C:/path/to/autoyou-mcp.env" --transport stdio
claude mcp list
```

Depois execute `/mcp` no Claude Code. Veja o [guia MCP do Claude Code](https://code.claude.com/docs/en/mcp).

**O Claude Desktop** inicia o adaptador como servidor stdio local. Adicione uma entrada `mcpServers.autoyou` em `~/Library/Application Support/Claude/claude_desktop_config.json` no macOS ou em `%APPDATA%\Claude\claude_desktop_config.json` no Windows, e feche e reabra o Claude Desktop por completo. O AutoYou ainda não distribui uma extensão de desktop `.mcpb`. Veja o [guia MCP do Claude Desktop](https://support.claude.com/en/articles/10949351-getting-started-with-local-mcp-servers-on-claude-desktop).

**O Google Antigravity** usa `~/.gemini/config/mcp_config.json` globalmente ou `.agents/mcp_config.json` em um workspace. No Antigravity IDE, abra **MCP Servers > Manage MCP Servers > View raw config**. Veja o [guia MCP do Antigravity](https://antigravity.google/docs/mcp).

**O Cursor** usa `~/.cursor/mcp.json` ou `.cursor/mcp.json` com a mesma entrada `mcpServers`. Veja o [guia MCP do Cursor](https://prod.cursor.com/help/customization/mcp).

**O VS Code** pode usar o arquivo portátil `.mcp.json` na raiz do workspace. O `.vscode/mcp.json` usa em vez disso um objeto `servers` de nível superior, com `"type": "stdio"`. Veja o [guia MCP do VS Code](https://code.visualstudio.com/docs/agent-customization/mcp-servers).

No macOS ou Linux, o executável do Python é `.venv-mcp/bin/python`. Versões dos clientes, configurações, políticas da organização e aprovação de ferramentas podem afetar a disponibilidade.

</details>

Depois de conectar, confira se `get_autoyou_status` aparece e retorna o estado esperado do servidor antes de usar qualquer ferramenta que grave dados. Veja o [guia de integração MCP do AutoYou](../guides/mcp-integration.mdx) para o escopo, o modelo de segurança e os limites.

<a id="admin-console"></a>

## Console de administração

<p align="center">
  <img src="../images/admin-tour.gif" alt="Tour pelo console: visão geral do servidor, dispositivos e atividade ao vivo, acesso e segurança" width="760" />
</p>

O console local reúne configuração, modelos, voz, agentes, conexões, permissões e segurança. A configuração guiada pergunta para que você quer usar o AutoYou e sugere os agentes e permissões adequados; nada muda até você revisar e salvar o plano. As capturas usam valores de exemplo. Selecione uma prévia para abrir a imagem em tamanho real.

| Visão geral do servidor | Dispositivos e atividade | Acesso e segurança |
| --- | --- | --- |
| [<img src="../images/admin/overview.png" alt="Visão geral do servidor, serviços e controles de configuração" width="280">](../images/admin/overview.png) | [<img src="../images/admin/live-view.png" alt="Visão ao vivo com conexões de dispositivos, tarefas e integrações de mensagens" width="280">](../images/admin/live-view.png) | [<img src="../images/admin/security.png" alt="Senha do servidor, autenticador e configurações de segurança" width="280">](../images/admin/security.png) |

<a id="other-ways-to-run"></a>

## Outras formas de executar

<details>
<summary><strong>Docker</strong></summary>

Forneça `AUTOYOU_SERVER_PASSWORD` de forma segura e execute:

```bash
docker compose up --build
```

O arquivo Compose publica as portas só no loopback do host e guarda a configuração em um volume nomeado. Abra `http://localhost:8001/` no mesmo computador; o arquivo declara essa publicação só em loopback (`AUTOYOU_ADMIN_TRUST_PUBLISHED_LOOPBACK=1`), então o login não é recusado com "requires localhost or HTTPS". Com um `docker run` simples, publique em `127.0.0.1` e passe a mesma variável, ou coloque um proxy TLS na frente. Confira como sua versão do Docker publica portas antes de confiar no isolamento por loopback.

Para desativar os downloads do Tunnelmole na compilação e na execução, defina `AUTOYOU_SKIP_TUNNELMOLE_DOWNLOAD=1` e `AUTOYOU_NO_DOWNLOAD_TUNNELMOLE=1` antes de compilar e iniciar. Os recursos que dependem dele vão então precisar de um binário fornecido separadamente.

</details>

<details>
<summary><strong>Compile uma versão local não oficial</strong></summary>

A partir da raiz do repositório:

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

Esses caminhos não exigem conta no AutoYou nem aprovação de uma versão oficial. Eles podem instalar ou ajustar dependências. Leia a licença e os avisos quando aparecerem. Para aceitá-los sem interação depois de ler, passe `-AcceptTerms` no Windows ou `--accept-terms` no macOS e Linux.

Requisitos, local dos resultados e limitações conhecidas:
[Windows](../../servers/windows/README.md) · [macOS](../../servers/macos/README.md) · [Linux / WSL](../../servers/wsl/README.md).

</details>

<a id="faq"></a>

## Perguntas frequentes

<details>
<summary><strong>Preciso de conta na nuvem ou de VPN?</strong></summary>

Não. Local Pair, Bluetooth Pair, Live Pair, códigos de uso único e Auto Pair funcionam sem conta AutoYou. O Cloud Pair é um auxiliar opcional e pago. Se você já usa uma VPN como o Tailscale, o AutoYou também funciona por ela.

</details>

<details>
<summary><strong>O chat com IA é mesmo ilimitado?</strong></summary>

O AutoYou não limita mensagens. Com um modelo local do Ollama, cada mensagem é gratuita e a velocidade depende do seu computador. Um provedor de nuvem que você conectar tem os próprios preços e limites.

</details>

<details>
<summary><strong>O AutoYou envia meus arquivos ou conversas para algum lugar?</strong></summary>

Eles ficam no seu computador. Os dados só saem pelos serviços que você ativa, como um provedor de modelos na nuvem, uma ponte de mensagens ou um túnel público, e cada um tem seus próprios termos.

</details>

<details>
<summary><strong>Em quais idiomas posso falar?</strong></summary>

O reconhecimento de voz detecta o idioma quando **STT language** está em `auto` e um modelo multilíngue está selecionado. As respostas faladas usam uma voz do sistema correspondente ou o modelo local Supertonic 3, que cobre 31 idiomas. Seu modelo de chat também precisa entender o idioma.

</details>

<details>
<summary><strong>Posso compartilhar um app web ou um jogo com amigos?</strong></summary>

Sim. Quem parear com seu servidor pode abrir seus sites de agentes e portas publicadas, dentro do papel que você der. Dispositivos compartilhados são marcados como tal, então seus sites podem limitar o que eles veem. Para quem não está pareado, o agente Hosting explica o link temporário gratuito e a URL persistente paga.

</details>

<details>
<summary><strong>O que faço se encontrar um bug?</strong></summary>

Atualize primeiro; uma versão recente pode já ter corrigido. Se o problema continuar, [abra uma issue](https://github.com/autoyou-ai/AutoYou-Server/issues) com sua versão do AutoYou (`VERSION`), o sistema operacional, o perfil, os passos para reproduzir e a saída do erro. Relate vulnerabilidades em sigilo conforme o [SECURITY.md](../../SECURITY.md).

</details>

<a id="documentation"></a>

## Documentação

- [Início rápido](../quickstart.mdx) · [Arquitetura](../architecture/) · [Segurança e privacidade](../security/) · [Console de administração](../admin-ui/)
- [Catálogo de agentes](../../autoyou_agents/README.md) · [Referência de agentes](../agents/) · [Framework de agentes](../agent-framework/)
- [API HTTP e WebSocket](../api-reference/) · [Guias](../guides/) · [Guias de pareamento](../../guides/README.md)

<a id="contributing"></a>

## Contribuir

Sua ajuda é muito bem-vinda. Comece pelos [agentes integrados](../../autoyou_agents/README.md), pelo [Agent Builder](../admin-ui/agent-workbench-ui.mdx) ou pelo [tutorial de agente personalizado](../guides/custom-agent-tutorial.mdx).

- **Crie** um agente, um jogo, uma extensão, uma integração ou uma interface web.
- **Melhore** a instalação, a acessibilidade, o suporte a plataformas, as traduções, a documentação ou um bug reproduzível.
- **Compartilhe** uma demo, um guia ou uma versão não oficial claramente identificada que respeite a licença.

Leia o [CONTRIBUTING.md](../../CONTRIBUTING.md) e o [Código de conduta](../../CODE_OF_CONDUCT.md) antes de enviar seu trabalho. Código original e independente pode usar a licença que você escolher, inclusive MIT ou Apache-2.0. Alterações no código existente do AutoYou mantêm a licença dele.

<a id="license"></a>

## Licença

O AutoYou Server é source-available sob a [licença do servidor](../../LICENSE). Atualmente não é um projeto de código aberto aprovado pela OSI, e valem as definições do `LICENSE`.

| Material ou uso | Condições |
| --- | --- |
| Uso individual, compilações locais e distribuição gratuita | Permitidos conforme a licença do servidor e suas condições |
| Modificações no código do AutoYou | Mantêm a licença do AutoYou aplicável |
| Código original, independente e separável | A licença que você escolher, inclusive MIT ou Apache-2.0 |
| Uso institucional ou exploração comercial | [Acordo Enterprise](https://www.autoyou.me/enterprise/) separado |

Mantenha os avisos, identifique versões não oficiais e cumpra as obrigações dos componentes incluídos. Veja [THIRD-PARTY-NOTICES.md](../../THIRD-PARTY-NOTICES.md) e a [política de marcas](../legal/trademark-policy.md).

A OpenStorey destina pelo menos 15 % da receita líquida elegível ao fundo de colaboradores, distribuído segundo suas [regras de revisão e elegibilidade](../contributors/contributor-pool.md). O [roteiro de financiamento](https://www.autoyou.me/donate/) inclui um marco de 5 milhões de dólares para abrir o código do servidor; um marco, por si só, não muda a licença de uma cópia que você já tem. Detalhes: [docs/legal/open-source-commitment.md](../legal/open-source-commitment.md).

<a id="support"></a>

## Suporte

- **Dúvidas e ajuda na instalação:** [SUPPORT.md](../../SUPPORT.md) e a [comunidade](https://www.autoyou.me/community/).
- **Bugs e propostas concretas:** [abra uma issue](https://github.com/autoyou-ai/AutoYou-Server/issues).
- **Vulnerabilidades:** relate em sigilo como descrito no [SECURITY.md](../../SECURITY.md). Não use issues públicas para vulnerabilidades não divulgadas.

Os agentes rodam com as permissões do servidor: revise o que fazem, ative só o que precisar e faça backups.

Copyright &copy; 2026 OpenStorey LLC and contributors.

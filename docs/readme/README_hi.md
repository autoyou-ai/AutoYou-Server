<div align="center">

[English](../../README.md) · [简体中文](README_zh.md) · [繁體中文](README_zh-TW.md) · [日本語](README_ja.md) · [한국어](README_ko.md) · **हिन्दी** · [Español](README_es.md) · [Français](README_fr.md) · [Deutsch](README_de.md) · [Português (Brasil)](README_pt-BR.md) · [Русский](README_ru.md) · [العربية](README_ar.md)

<img src="../images/logo.png" alt="AutoYou लोगो" width="96" />

# AutoYou Server

### आपका अपना AI सर्वर, उसी कंप्यूटर पर जो आपके पास पहले से है।

**आप उत्पाद नहीं हैं। आप मालिक हैं।**

[![Public checks](https://github.com/autoyou-ai/AutoYou-Server/actions/workflows/public-checks.yml/badge.svg?branch=main)](https://github.com/autoyou-ai/AutoYou-Server/actions/workflows/public-checks.yml)
[![Latest release](https://img.shields.io/github/v/release/autoyou-ai/AutoYou-Server?style=flat-square&color=22c55e)](https://github.com/autoyou-ai/AutoYou-Server/releases)
[![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-3776AB?style=flat-square&logo=python&logoColor=white)](../../requirements/README.md)
[![Platforms](https://img.shields.io/badge/platforms-Windows%20%7C%20macOS%20%7C%20Linux-536DFE?style=flat-square)](../../servers/README.md)
[![Built-in agents](https://img.shields.io/badge/built--in_agents-45-f97316?style=flat-square)](../../autoyou_agents/README.md)
[![Source-available license](https://img.shields.io/badge/license-source--available-8B5CF6?style=flat-square)](../../LICENSE)

**[वेबसाइट](https://www.autoyou.me/) · [ऐप्स](https://www.autoyou.me/downloads/) · [इकोसिस्टम](https://www.autoyou.me/ecosystem/) · [कम्युनिटी](https://www.autoyou.me/community/)**

[क्विक स्टार्ट](#quick-start) · [यह कैसे काम करता है](#how-autoyou-works) · [आप क्या कर सकते हैं](#what-you-can-do) · [एजेंट](#agents) · [पेयरिंग](#pairing-modes) · [सुरक्षा](#security-modes-and-tiers) · [क्लाइंट](#supported-clients) · [FAQ](#faq) · [दस्तावेज़](#documentation)

<code>./run_autoyou.sh</code>

</div>

> [!IMPORTANT]
> AutoYou Server को [autoyou-ai/AutoYou-Server](https://github.com/autoyou-ai/AutoYou-Server) या [autoyou.me](https://www.autoyou.me/) से लें, और ऐप्स को [समर्थित क्लाइंट](#supported-clients) में दिए स्टोर लिंक से लें। मिलते-जुलते नाम वाला रिपॉज़िटरी या कॉपी किया गया README किसी डाउनलोड को आधिकारिक नहीं बनाता।

<div align="center">
<br />
<img src="../images/hero.gif" alt="फ़ोन पेयर करना, लोकल मॉडल से चैट, कॉल करना, पेज ब्राउज़ करना और कंप्यूटर की स्क्रीन देखना" width="860" />
<br /><br />
</div>

---

अपनी मेज़ पर रखे कंप्यूटर को अपने हर डिवाइस के लिए एक निजी AI सर्वर बना दीजिए। AutoYou Server आपके Windows, macOS या Linux कंप्यूटर पर AI मॉडल और एजेंट, वॉइस और वीडियो कॉल, रिमोट डेस्कटॉप, गेम और आपके अपने वेब ऐप चलाता है। आपका फ़ोन, दूसरे कंप्यूटर और ब्राउज़र, घर पर हों या बाहर, एन्क्रिप्टेड पीयर-टू-पीयर कनेक्शन से इससे जुड़ते हैं।

आपकी बातचीत, फ़ाइलें और एजेंट की गतिविधि उसी कंप्यूटर पर रहती हैं, किसी होस्टेड AutoYou सेवा में नहीं। Ollama से लोकल मॉडल चलाएँ तो चैट के लिए टोकन का बिल नहीं आता, या अपनी पसंद का क्लाउड प्रोवाइडर जोड़ें। एक बार डिवाइस पेयर करें और सर्वर की हर सुविधा आपकी जेब में: चैट, हैंड्स-फ़्री असिस्टेंट, कई भाषाओं में लाइव आवाज़, आपका डेस्कटॉप, आपके गेम और कोई भी वेब सेवा जो आप पहले से चला रहे हैं।

> **पहली बार?** लॉन्चर चलाएँ, **http://127.0.0.1:8001/** खोलें, और सेटअप गाइड आपको AI चुनने, ज़रूरी सुविधाएँ चालू करने और पहला डिवाइस पेयर करने में मदद करेगी।

<a id="quick-start"></a>

## क्विक स्टार्ट

आपको Git और Python 3.11 या नया चाहिए। कुछ वैकल्पिक सुविधाओं के लिए नया Python या कोई खास प्लेटफ़ॉर्म चाहिए; [डिपेंडेंसी प्रोफ़ाइल](../../requirements/README.md) देखें।

### 1. सर्वर लें

```bash
git clone https://github.com/autoyou-ai/AutoYou-Server.git
cd AutoYou-Server
```

### 2. लॉन्चर चलाएँ

Windows:

```powershell
.\run_autoyou.bat --profile base
# Or select the Full profile with LAN access:
.\run_autoyou.bat --profile full --host 0.0.0.0
```

macOS या Linux:

```bash
./run_autoyou.sh --profile base
# Or select the Full profile with LAN access:
./run_autoyou.sh --profile full --host 0.0.0.0
```

लॉन्चर एक Python एनवायरनमेंट बनाता है, पैकेज इंडेक्स से डिपेंडेंसी इंस्टॉल करता है, और मॉडल व नेटिव टूल डाउनलोड कर सकता है। पहले स्क्रिप्ट पढ़ें और उन्हें ऐसे एनवायरनमेंट में चलाएँ जिसे आप सुरक्षित रूप से बदल सकें।

### 3. कंसोल खोलें और डिवाइस पेयर करें

**http://127.0.0.1:8001/** पर जाएँ, एक अनोखा सर्वर पासवर्ड चुनें और सेटअप का पालन करें। यह पूछता है कि आप AutoYou से क्या करना चाहते हैं (चैट, हैंड्स-फ़्री असिस्टेंट, कॉल, गेम, कोडिंग और भी बहुत कुछ), उसके हिसाब से एजेंट इंस्टॉल करता है और फ़ोन पेयर करने के लिए QR कोड दिखाता है।

बिना निगरानी वाले सेटअप के लिए `AUTOYOU_SERVER_PASSWORD` को एनवायरनमेंट या सीक्रेट मैनेजर से दें। असली पासवर्ड को साझा कमांड, issue या स्क्रीनशॉट में न डालें।

| प्रोफ़ाइल | क्या जोड़ता है |
| --- | --- |
| `base` | कोर सर्वर, लोकल एडमिनिस्ट्रेशन, डिवाइस कनेक्शन और क्लाउड-प्रोवाइडर इंटीग्रेशन |
| `local` | लोकल-मॉडल हेल्पर और डिवाइस पर इंटेंट रूटिंग |
| `recommended` | मैसेजिंग और Bluetooth इंटीग्रेशन डिपेंडेंसी (`--profile` न देने पर डिफ़ॉल्ट) |
| `full` | स्पीच, ब्राउज़र और डेस्कटॉप-ऑटोमेशन डिपेंडेंसी |

प्रोफ़ाइल सिर्फ़ डिपेंडेंसी चुनती हैं; इंटीग्रेशन को फिर भी सेट अप करना होता है। `--skip-nuitka` और `--skip-backend-compile` लॉन्चर की बंडल लाइब्रेरी बनाए रखते हैं, इसलिए पैकेज्ड ऐप्स में डिपेंडेंसी के सुरक्षा अपडेट के लिए पूरा रीबिल्ड चाहिए।

अपडेट करने के लिए रिपॉज़िटरी में `git pull` चलाएँ और फिर लॉन्चर दोबारा चलाएँ। रिलीज़ नोट्स [docs/releases/](../releases/) में हैं।

<a id="how-autoyou-works"></a>

## यह कैसे काम करता है

**सर्वर आपका अपना कंप्यूटर है, और डिवाइस सीधे उससे बात करते हैं।** ऐप और सर्वर WebRTC से जुड़ते हैं। एक छोटे पेयरिंग हैंडशेक के बाद वे एक एन्क्रिप्टेड लिंक (DTLS, मीडिया के लिए SRTP) खोलते हैं, जो चैट, आवाज़, वीडियो, स्क्रीन शेयरिंग, गेम इनपुट और प्रॉक्सी वेब ट्रैफ़िक ले जाता है। ज़्यादातर घरेलू और मोबाइल नेटवर्क पर राउटर का पोर्ट खोले बिना यह लिंक बन जाता है। नेटवर्क बहुत सख़्त हो तो कनेक्शन एक रिले पर चला जाता है, जो वही एन्क्रिप्टेड पैकेट आगे भेजता है।

```mermaid
flowchart LR
    app["AutoYou ऐप<br/>फ़ोन, कंप्यूटर, ब्राउज़र या Pi"]
    server["AutoYou Server<br/>आपका कंप्यूटर"]
    rendezvous["रेंडेवू (वैकल्पिक)<br/>Cloud Pair"]
    relay["रिले (वैकल्पिक)<br/>एन्क्रिप्टेड पैकेट भेजता है"]

    app <-->|"एन्क्रिप्टेड WebRTC, संभव हो तो सीधा"| server
    app -.->|"सिग्नलिंग"| rendezvous
    rendezvous -.->|"सिग्नलिंग"| server
    app -.->|"फ़ॉलबैक"| relay
    relay -.->|"फ़ॉलबैक"| server
```

सर्वर पर, डिवाइस पर चलने वाला इंटेंट राउटर हर अनुरोध को सही एजेंट या टूल तक भेजता है। Google ADK नेटिव एजेंट चलाता है; OpenClaw, Hermes Agent और Odysseus को भी जोड़ा जा सकता है। LiteLLM मॉडल कॉल को आपके कंप्यूटर के Ollama या आपके चुने क्लाउड प्रोवाइडर तक भेजता है। आवाज़ की पहचान और बोलना दोनों लोकल होते हैं, और मेमोरी SQLite में रहती है।

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="../images/architecture-dark.png" />
  <img src="../images/architecture-light.png" alt="AutoYou का आर्किटेक्चर: ऐप्स एन्क्रिप्टेड WebRTC लिंक से आपके कंप्यूटर के सर्वर से जुड़ते हैं; Auto Pair संदेश Telegram, Signal या WhatsApp से जाते हैं; MCP क्लाइंट MCP ब्रिज से जुड़ते हैं; इंटेंट राउटर एजेंट रनटाइम, एजेंट ऐप्स, लोकल वॉइस, मेमोरी और मॉडलों को LiteLLM के ज़रिए Ollama या वैकल्पिक क्लाउड प्रदाताओं से जोड़ता है।" width="100%" />
</picture>

यह आरेख [scripts/build_architecture_diagram.py](../../scripts/build_architecture_diagram.py) से बनता है। डैश वाले बॉक्स वैकल्पिक ऐड-ऑन या जुड़े हुए प्रोजेक्ट हैं।

<a id="what-you-can-do"></a>

## आप क्या कर सकते हैं

| सुविधा | आपको क्या मिलता है |
| --- | --- |
| **AI चैट** | किसी भी पेयर किए डिवाइस से अपने AI से चैट; AutoYou मैसेज की कोई सीमा नहीं लगाता |
| **रियलटाइम आवाज़** | AI से लाइव कॉल, लोकल स्पीच रिकग्निशन, 30+ भाषाओं में बोले गए जवाब |
| **असिस्टेंट मोड** | फ़ोन, कंप्यूटर, Raspberry Pi या ब्राउज़र पर "Hey Jarvis" जैसा वेक वर्ड |
| **रिमोट डेस्कटॉप** | फ़ोन से कंप्यूटर देखें और चलाएँ, Windows लॉक स्क्रीन समेत |
| **कंप्यूटर वीडियो** | कंप्यूटर की स्क्रीन, कैमरा, वीडियो फ़ाइल या API फ़्रेम को कॉल में भेजें |
| **गेम मोड और Game Studio** | कंप्यूटर के गेम फ़ोन पर स्ट्रीम करें, या फ़ोन पर ऑफ़लाइन चलने वाले गेम बनाएँ |
| **वेब ऐप फ़ैक्टरी** | एजेंट से वेब ऐप बनवाएँ और हर पेयर किए डिवाइस पर खोलें |
| **कोई भी लोकल पोर्ट होस्ट करें** | `localhost:3000` जैसी किसी भी वेब सेवा को पेयर किए डिवाइस से साझा करें |
| **एजेंट** | नोट्स, फ़ाइलें, कोडिंग, मीडिया, ब्राउज़िंग, ट्रेनिंग आदि के लिए 45 बिल्ट-इन एजेंट |
| **MCP** | ChatGPT, Codex, Claude, Antigravity, Cursor और VS Code से AutoYou टूल इस्तेमाल करें |
| **मैसेजिंग** | अपने नियंत्रण वाले कनेक्टरों से Telegram, Signal और WhatsApp के ज़रिए अपने सर्वर तक पहुँचें |
| **मेमोरी** | SQLite मेमोरी और एक निजी पर्सोना जर्नल, Cognee वैकल्पिक ऐड-ऑन के रूप में |

सुविधाएँ चुनी गई प्रोफ़ाइल, हार्डवेयर, अनुमतियों और कॉन्फ़िगर किए इंटीग्रेशन पर निर्भर करती हैं। डेस्कटॉप कंट्रोल, गेम मोड, रिकॉर्डिंग और फ़ाइल एक्सेस जैसी ताक़तवर सुविधाएँ तब तक बंद रहती हैं जब तक आप उन्हें चालू न करें।

<a id="ai-chat"></a>

### AI चैट

कंसोल के **AI** में प्रोवाइडर चुनें: डिफ़ॉल्ट **Ollama + Agents** (लोकल मॉडल, API key की ज़रूरत नहीं), सपोर्टेड Mac पर **Apple Intelligence**, **Gemini**, **OpenRouter**, या LiteLLM के ज़रिए Anthropic, OpenAI, Mistral, DeepSeek, xAI आदि, AutoYou के सभी एजेंट और टूल के साथ। **OpenClaw**, **Hermes Agent** या **Odysseus** को मुख्य सेशन रनटाइम भी बना सकते हैं।

AutoYou यह सीमा नहीं लगाता कि आप कितने मैसेज भेजें। लोकल मॉडल के साथ हर मैसेज मुफ़्त है और इंटरनेट के बिना भी काम करता है। आपका जोड़ा क्लाउड प्रोवाइडर अपने प्लान के अनुसार बिल करता है और आपके भेजे प्रॉम्प्ट देखता है। बिल्ट-इन मॉडल लाइब्रेरी से लोकल मॉडल देख, डाउनलोड और बदल सकते हैं, और Model Picker एजेंट आपके हार्डवेयर के लिए सही मॉडल सुझाता है।

छोटे, क्वांटाइज़्ड लोकल मॉडलों को सटीक बने रहने में मदद मिलती है: एक ऑफ़लाइन इंटेंट क्लासिफ़ायर आम अनुरोधों को सीधे सही एजेंट तक भेजता है, और एक कॉम्पैक्ट रूटिंग मोड टूल सूची को छोटा रखता है। देखें [छोटे मॉडल सटीक कैसे रहते हैं](../../autoyou_agents/README.md#small-local-models)।

<a id="realtime-voice-in-many-languages"></a>

### कई भाषाओं में रियलटाइम आवाज़

अपने AI को कॉल करें और बात करें। स्पीच रिकग्निशन आपके कंप्यूटर पर चलता है (डिफ़ॉल्ट बिल्ड में whisper.cpp, फ़ुल वॉइस बिल्ड में faster-whisper), वॉइस एक्टिविटी डिटेक्शन के साथ; आप बोलना शुरू करें तो जवाब रुक जाता है, और लंबे विचारों के लिए **"मेरी बात पूरी होने तक रुको"** होल्ड है।

- **अपनी भाषा में बोलें।** **STT language** को `auto` रखें और मल्टीलिंगुअल मॉडल चुनें; यह आपकी बोली गई भाषा पहचान लेता है।
- **उसी भाषा में जवाब सुनें।** डिफ़ॉल्ट **Auto TTS** जवाब की भाषा पहचानकर मेल खाती सिस्टम आवाज़ चुनता है। लोकल Supertonic 3 मॉडल हिंदी समेत 31 भाषाएँ बोलता है और लगभग 401 MB के एक बार के डाउनलोड के बाद ऑफ़लाइन चलता है।
- **अपनी आवाज़ चुनें।** सिस्टम आवाज़ें, 10 Supertonic प्रीसेट, Voice Training में ट्रेन की गई अपनी आवाज़, चीनी और अंग्रेज़ी के लिए EmotiVoice, या होस्टेड OpenAI और Azure स्पीच।

टेक्स्ट-टू-स्पीच वही जवाब बोलता है जो उसे मिलता है; वह अनुवाद नहीं करता। आपका चैट मॉडल भी बातचीत की भाषा समझना चाहिए।

<a id="assistant-mode"></a>

### असिस्टेंट मोड

अपने सर्वर को "Hey Jarvis" जैसा वेक वर्ड दें। पेयर किए फ़ोन, कंप्यूटर, Raspberry Pi, Chrome एक्सटेंशन या एडमिन पेज के पास इसे बोलें, और उसके बाद आप जो कहेंगे वह AutoYou तक जाएगा। वेक वर्ड सुनाई देने तक आपकी कोई बात न लिखी जाती है, न AI को भेजी जाती है। शब्द तीन बार रिकॉर्ड करें, सर्वर पर कुछ सेकंड में ट्रेन करें, टेस्ट करें और चालू करें। वीडियो कॉल में, वेक वर्ड के बाद का अनुरोध कैमरे की ताज़ा तस्वीर भी साथ ले जा सकता है। [असिस्टेंट मोड](../admin-ui/assistant-mode.mdx) देखें।

<a id="remote-desktop"></a>

### रिमोट डेस्कटॉप

पेयर किए फ़ोन या डेस्कटॉप ऐप से अपना कंप्यूटर देखें और चलाएँ: कोई मॉनिटर या एक ऐप विंडो चुनें, माउस चलाएँ, टाइप करें और मॉडिफ़ायर कीज़ इस्तेमाल करें। डिवाइस सपोर्ट करे तो H.264 को प्राथमिकता मिलती है। Windows पर वैकल्पिक **AutoYou Secure Desktop Helper** सेवा पेयर किए मालिक को लॉक स्क्रीन और UAC प्रॉम्प्ट देखने और चलाने देती है, और कनेक्शन के दौरान सर्वर कंप्यूटर को जगाए रख सकता है। स्क्रीन कैप्चर और इनपुट अलग-अलग अनुमतियाँ हैं, दोनों डिफ़ॉल्ट रूप से बंद; Peer Link पर रिमोट डेस्कटॉप कभी नहीं मिलता।

<a id="computer-video"></a>

### कंप्यूटर वीडियो

कॉल में कंप्यूटर चार स्रोतों से वीडियो भेज सकता है: अपनी **स्क्रीन**, **कैमरा**, आपकी अपलोड की गई **वीडियो फ़ाइल**, या आपके अपने प्रोग्राम से आने वाले **API फ़्रेम**। कंप्यूटर का माइक्रोफ़ोन और आवाज़ भी साझा की जा सकती है। कंसोल का **Interact** जुड़े डिवाइस के कैमरे की लाइव तस्वीरें दिखाता है और उनके माइक्रोफ़ोन को कंप्यूटर के स्पीकर पर बजा सकता है, बिना AI प्रोसेसिंग और बिना रिकॉर्डिंग।

<a id="game-mode-and-game-studio"></a>

### गेम मोड और Game Studio

- **गेम मोड** आपके कंप्यूटर पर रेंडर हुए गेम को पेयर किए डिवाइस पर स्ट्रीम करता है, और डिवाइस का टच, मोशन सेंसर, कंट्रोलर, माउस और कीबोर्ड गेम इनपुट के रूप में वापस आते हैं। आप चार तक लेबल वाले वर्चुअल बटन बना सकते हैं। गेम मोड एक अलग अनुमति है और यह स्क्रीन भेजना और रिमोट डेस्कटॉप इनपुट भी चालू करता है।
- **अपना इंजन लाएँ।** लोकल गेम इंजन लूपबैक WebSocket से `autoyou_game_v1` प्रोटोकॉल के प्रमाणित इनपुट फ़्रेम पढ़ सकता है। Unreal के लिए सर्वर `/api/game/unreal-plugin` पर सोर्स प्लगइन देता है।
- **फ़ोन के लिए ऑफ़लाइन गेम।** Game Studio एजेंट पोर्टेबल वेब या WASM गेम बनाने में मदद करता है, उन्हें 5 GiB तक की `.aygame` फ़ाइल में पैक करता है और WebRTC या मंज़ूर Peer Link से क्लाइंट तक भेजता है। हर गेम का अपना ऑन-डिवाइस SQLite डेटाबेस होता है, और सेव प्रोग्रेस अपडेट के बाद भी बनी रहती है।

बिल्ट-इन डेमो **Neon Horizon** फ़ोन डाउनलोड, Game Studio पेज या कंप्यूटर से स्ट्रीम करके खेला जा सकता है।

<a id="web-app-factory"></a>

### वेब ऐप फ़ैक्टरी

ऐप का वर्णन करें और एजेंट उसे बना देंगे। **Website** एजेंट किसी एजेंट के लिए ब्राउज़र फ़्रंटएंड बनाता है: एक सरल स्टार्टर, या आपके कहने पर React या Angular, और बैकएंड Python/FastAPI, Node/TypeScript, Go या Rust। **Agent Builder** नए एजेंट का ढाँचा बनाता है, और **Coding** व **Pi** एजेंट कोड लिखते और टेस्ट करते हैं। हर ऐप लोकल वेबसाइट सेवा में रजिस्टर होता है और आपके पेयर किए डिवाइस के ब्राउज़र में दिखता है। **Hosting** एजेंट पब्लिक लिंक पर प्रकाशित करने के तरीके बताता है: मुफ़्त अस्थायी `/pair` टनल या पेड स्थायी URL।

<a id="host-any-local-port"></a>

### कोई भी लोकल पोर्ट होस्ट करें

पहले से कुछ चल रहा है? उसे साझा करें। **Websites & Browser > Advertised Websites** में 1 से 65535 तक का कोई भी पोर्ट, एक लेबल और वैकल्पिक फ़ॉरवर्ड URL (जैसे `http://127.0.0.1:3000` या आपके नेटवर्क का कोई दूसरा होस्ट) के साथ जोड़ें। पेयर किए क्लाइंट उसे खोज लेते हैं और अपनी तरफ़ उसी पोर्ट पर खोलते हैं, इसलिए डैशबोर्ड, डेव सर्वर, होम-लैब टूल और वे ऐप भी चलते रहते हैं जो सब-पाथ के लिए नहीं बने। ट्रैफ़िक एन्क्रिप्टेड पीयर लिंक के अंदर चलता है, इसलिए राउटर पर पोर्ट खोलने की ज़रूरत नहीं। पेयर किए ब्राउज़र को `viewer`, `editor` या `admin` भूमिका मिलती है, जो तय करती है कि वे क्या बदल सकते हैं।

<a id="agents"></a>

## एजेंट

AutoYou में 45 बिल्ट-इन एजेंट हैं। मुख्य एजेंट हर अनुरोध को सही विशेषज्ञ तक भेजता है, और कई विशेषज्ञ आपके पेयर किए डिवाइस पर अपना वेब पेज भी देते हैं। opt-in वाले एजेंट तब तक इंस्टॉल नहीं होते जब तक आप उन्हें कंसोल के **Agents** में इंस्टॉल न करें।

| क्षेत्र | एजेंट |
| --- | --- |
| रोज़मर्रा | Notes, Tasks, Notify, Memory, Persona, Page, Prompts, Location (opt-in) |
| वेब और ऑटोमेशन | Internet, Assistant, Browser (opt-in), Client Browser Control, Proxy (opt-in) |
| मीडिया और क्रिएटिव | Audio, TV, Media Generation (opt-in), Game Studio |
| कोडिंग और निर्माण | Coding, Pi, Agent Builder, Website, Skills, Files, CLI, Claude CLI, Claude Desktop, Codex Desktop, Antigravity, Prompt Builder (सभी opt-in) |
| इंटेलिजेंस और मॉडल | Model Picker, OpenClaw, Hermes Agent (सभी opt-in) |
| ट्रेनिंग | Data Collector, Fine-Tuning, Voice Training (सभी opt-in) |
| सिस्टम और कंप्यूटर | Admin, Remote Desktop, Backup, Hosting (opt-in), Windows Security (opt-in), macOS Security (opt-in), Education (opt-in) |
| AutoYou को समर्थन | Support Ads, Earnings, Donation |

अपना एजेंट जोड़ने के लिए पैकेज को `autoyou_agents/` में कॉपी करें। पूरी जानकारी [एजेंट कैटलॉग](../../autoyou_agents/README.md) में है।

[जनरेट किया गया एजेंट संदर्भ](../agents/) हर एजेंट के घोषित हर टूल की सूची देता है।

<a id="pairing-modes"></a>

## पेयरिंग के तरीके

| तरीका | कैसे जुड़ता है | आम उपयोग |
| --- | --- | --- |
| **Local Pair** | सीधे आपके LAN पर; सिग्नलिंग आपका अपना सर्वर करता है | एक ही Wi-Fi या ऑफ़िस नेटवर्क |
| **Bluetooth Pair** | कनेक्शन की जानकारी Bluetooth Low Energy से दी जाती है | पास के फ़ोन का पहला सेटअप |
| **Live Pair** | फ़ोन और कंप्यूटर एक-दूसरे को एनिमेटेड QR कोड दिखाते हैं, नेटवर्क की ज़रूरत नहीं | अलग-थलग या सख़्त नेटवर्क |
| **वन-टाइम कोड** | मैसेजिंग चैट में `/pair` भेजने पर थोड़ी देर का कोड और लिंक मिलता है | मेहमान डिवाइस और अविश्वसनीय नेटवर्क |
| **Auto Pair** | एन्क्रिप्टेड पेयरिंग मैसेज आपके सेट किए Telegram, Signal या WhatsApp चैट से जाता है | बिना क्लाउड अकाउंट के दूर से पेयरिंग |
| **Cloud Pair** | वैकल्पिक, पेड AutoYou Cloud रेंडेवू NAT के पार आपका कंप्यूटर ढूँढता है, रिले फ़ॉलबैक के साथ | घर से बाहर या सख़्त नेटवर्क पर |

**Peer Link** दो AutoYou ऐप को जोड़ता है; पाने वाला ऐप हर लिंक मंज़ूर करता है और यह कंप्यूटर का पासवर्ड कभी इस्तेमाल नहीं करता। **Lobbies** समूह कमरे हैं जिन्हें ऐप पास में Wi-Fi और Bluetooth पर होस्ट और जॉइन करते हैं। [पेयरिंग प्रोटोकॉल](../technical/pairing-protocols.md) देखें।

<a id="security-modes-and-tiers"></a>

## सुरक्षा मोड और सुरक्षा टियर

पूरे सर्वर पर एक ही सुरक्षा मोड लागू होता है। नई इंस्टॉलेशन **Secure** से शुरू होती है।

| मोड | पेयरिंग मैसेज | पेयर करने के लिए क्या चाहिए | इस कंप्यूटर का डेटा |
| --- | --- | --- | --- |
| **Normal** | एन्क्रिप्ट नहीं | वन-टाइम कोड | बदलाव नहीं |
| **Secure** | सर्वर पासवर्ड से एन्क्रिप्ट | सर्वर पासवर्ड | बदलाव नहीं |
| **Secure Professional** | सर्वर पासवर्ड से एन्क्रिप्ट | पासवर्ड और मौजूदा ऑथेंटिकेटर कोड | बदलाव नहीं |
| **Secure Professional Maximus** | Secure Professional जैसा | पासवर्ड और मौजूदा ऑथेंटिकेटर कोड | सेशन, एजेंट डेटा, वेबसाइटें, नोट्स और सेटिंग्स स्टोरेज में एन्क्रिप्ट |

Secure मोड में पेयरिंग मैसेज पासवर्ड-ऑथेंटिकेटेड की एक्सचेंज (ristretto255 और libsodium पर CPace परिवार की संरचना) से बनी कुंजी से एन्क्रिप्ट होते हैं। उन्हें ले जाने वाला रिले या मैसेजिंग ब्रिज उन्हें पढ़ नहीं सकता, और रिकॉर्ड किए गए एक्सचेंज से ऑफ़लाइन पासवर्ड का अंदाज़ा नहीं लगाया जा सकता।

**पेयरिंग सुरक्षा टियर** तय करता है कि चैट-मैसेज पेयरिंग में कितने कदम लगें: **Quick Pairing (B)** एक ही मैसेज से पेयर करने देता है और सबसे ज़्यादा कम्पैटिबल है; **Enhanced Pairing (A)** हर क्लाइंट से पहले एक अतिरिक्त hello राउंड ट्रिप माँगता है, जिससे अंदाज़ा लगाना कठिन होता है। Local Pair, Bluetooth Pair और Cloud Pair हमेशा एन्हांस्ड फ़्लो इस्तेमाल करते हैं।

पेयर किए डिवाइस के ब्राउज़र को `viewer`, `editor` या `admin` भूमिका मिलती है। क्रेडेंशियल और सुरक्षा सेटिंग्स सिर्फ़ उसी कंप्यूटर से, या एडमिन पोर्ट पर सीधे खोले गए HTTPS एडमिन सेशन से बदली जा सकती हैं। एन्क्रिप्शन कनेक्शन की सुरक्षा करता है, डिवाइस, एजेंट की अनुमतियों या तीसरे पक्ष की सेवाओं की नहीं। [सुरक्षा मोड](../security/security-modes.md) और [SECURITY.md](../../SECURITY.md) देखें।

<details>
<summary><strong>हर परत क्या सुरक्षित रखती है, और कहाँ रुकती है</strong></summary>

| परत | AutoYou क्या करता है |
| --- | --- |
| नेटवर्क पर उपलब्धता | डिफ़ॉल्ट रूप से `127.0.0.1` पर सुनता है। LAN पर सुनना एक स्पष्ट सेटिंग है। |
| एक्सेस | एडमिन कंसोल सर्वर पासवर्ड से सुरक्षित है, ऑथेंटिकेटर ऐप (TOTP) कोड वैकल्पिक हैं। |
| पेयरिंग | पासवर्ड से प्रमाणित की एक्सचेंज, एक बार इस्तेमाल होने वाले कोड, और Peer Link में हर लिंक की मंज़ूरी। |
| ट्रांसपोर्ट | दोनों छोरों के बीच WebRTC DTLS और SRTP, तब भी जब कोई रिले पैकेट आगे भेजता है। |
| संग्रहीत डेटा | क्रेडेंशियल एन्क्रिप्टेड की स्टोर में रहते हैं; Maximus AutoYou द्वारा प्रबंधित डेटा को भी एन्क्रिप्ट करता है। |
| डिपेंडेंसी | लॉकफ़ाइल वाले मैनिफ़ेस्ट, सुरक्षा सलाह की जाँच, और पिन किए गए CI एक्शन। |

एन्क्रिप्शन कनेक्शन को सुरक्षित रखता है। यह उसके आसपास की हर चीज़ को सुरक्षित नहीं रखता:

- **डिवाइस।** सर्वर कंप्यूटर या पेयर किए गए डिवाइस पर मौजूद मैलवेयर के पास वही एक्सेस होता है जो उस डिवाइस के पास है।
- **एजेंट।** एजेंट सर्वर की अनुमतियों के साथ चलते हैं। ब्राउज़र ऑटोमेशन, फ़ाइल एक्सेस, डेस्कटॉप नियंत्रण और कमांड चलाना शक्तिशाली हैं; सिर्फ़ ज़रूरी चीज़ें चालू करें और देखें कि वे क्या करते हैं।
- **तीसरे पक्ष।** क्लाउड मॉडल प्रदाताओं, मैसेजिंग ब्रिज और सार्वजनिक टनल के अपने डेटा प्रवाह और शर्तें हैं। चालू करने से पहले उन्हें देख लें।
- **संचालन।** सर्वर चलाने पर अपडेट, बैकअप और क्रेडेंशियल की ज़िम्मेदारी आपकी होती है।

</details>

<details>
<summary><strong>कौन-सी बाहरी सेवाएँ शामिल हैं</strong></summary>

आप एक ही सर्वर चलाते हैं: अपना। अपने असिस्टेंट को चलाने या डेटा रखने के लिए AutoYou द्वारा चलाया गया कोई ऐप्लिकेशन सर्वर ज़रूरी नहीं है, और सर्वर AutoYou Cloud से जुड़े बिना लोकल नेटवर्क पर चलता है ([विवरण](../architecture/cloud-and-remote.mdx))। बाकी सब कुछ या तो बदला जा सकने वाला डिफ़ॉल्ट है या वैकल्पिक सहायक:

| घटक | कहाँ चलता है | क्या देख सकता है |
| --- | --- | --- |
| AutoYou Server: एजेंट, मॉडल, स्टोरेज, कंसोल | आपका कंप्यूटर | आपका डेटा। यह कनेक्शन का एक छोर है। |
| ऐप्स और ब्राउज़र क्लाइंट | आपके डिवाइस | जो आप उनके साथ साझा करते हैं |
| पता खोजने के लिए STUN सर्वर (डिफ़ॉल्ट रूप से चालू) | Google के सार्वजनिक STUN सर्वर, जब तक आप बदलें नहीं | वह सार्वजनिक IP और पोर्ट जिससे WebRTC कनेक्शन दिखता है |
| Cloud Pair मिलन बिंदु (वैकल्पिक) | AutoYou Cloud | खाते का ईमेल, डिवाइस ID, अस्थायी पेयरिंग डेटा और नेटवर्क मेटाडेटा |
| फ़ॉलबैक रिले (वैकल्पिक) | AutoYou Cloud | एन्क्रिप्टेड पैकेट और नेटवर्क मेटाडेटा |
| फ़ोन ऐप जगाने के लिए पुश सूचनाएँ (वैकल्पिक) | Apple और Google की पुश सेवाएँ | कि ऐप को जगाने का अनुरोध हुआ |
| अपडेट जाँच (खाता जुड़ने पर चालू) | AutoYou Cloud का अपडेट चैनल | जुड़े खाते से प्रमाणित अपडेट अनुरोध |
| क्लाउड मॉडल प्रदाता (वैकल्पिक) | आपका चुना हुआ प्रदाता | आपके भेजे गए प्रॉम्प्ट |
| Telegram, Signal और WhatsApp ब्रिज (वैकल्पिक) | वे सेवाएँ | उनसे गुज़रने वाले संदेश, उनकी शर्तों के अनुसार |
| वेबसाइटों के लिए सार्वजनिक टनल (वैकल्पिक) | टनल प्रदाता | सार्वजनिक URL का ट्रैफ़िक। यह एंड-टू-एंड नहीं है। |

**सार्वजनिक STUN डिफ़ॉल्ट रूप से चालू है**, Local Pair के लिए भी। इस अनुरोध को इंटरनेट पर जाने से रोकने के लिए सर्वर शुरू करने से पहले `AUTOYOU_DISABLE_PUBLIC_STUN=1` सेट करें, या `AUTOYOU_LOCAL_STUNTURN_HOST` से अपने STUN और TURN सर्वर की ओर इशारा करें (TURN के लिए `AUTOYOU_LOCAL_TURN_USERNAME` और `AUTOYOU_LOCAL_TURN_PASSWORD` भी)। बिना किसी STUN या TURN सर्वर के NAT के पार कनेक्शन विफल हो सकते हैं।

</details>

<a id="supported-clients"></a>

## समर्थित क्लाइंट

AutoYou Server आपके कंप्यूटर पर चलता है; ऐप बाकी हर जगह से उस तक पहुँचते हैं। क्लाइंट ऐप अलग से वितरित होते हैं और उनका सोर्स इस रिपॉज़िटरी में नहीं है।

| प्लेटफ़ॉर्म | कहाँ से लें |
| --- | --- |
| iPhone और iPad | [App Store](https://apps.apple.com/us/app/autoyou/id6760363728) |
| Android | [Google Play](https://play.google.com/store/apps/details?id=com.autoyou.app) |
| Windows | [Microsoft Store](https://apps.microsoft.com/detail/9mw8l2wfw7wv?hl=en-US&gl=US) |
| macOS और Linux | [डाउनलोड](https://www.autoyou.me/downloads/) |
| ब्राउज़र | Chrome एक्सटेंशन और वेब क्लाइंट ([इकोसिस्टम पेज](https://www.autoyou.me/ecosystem/)) |
| Raspberry Pi | असिस्टेंट मोड के लिए Python वॉइस क्लाइंट ([सेटअप](../admin-ui/assistant-mode.mdx)) |

फ़ोन ऐप में एक छोटा लोकल AI भी है (डिवाइस के अनुसार LFM 2.5 350M, Gemini Nano या Apple Intelligence), ताकि कंप्यूटर न हो तब भी चैट हो सके।

| पेयरिंग मोड | अपनी AI से चैट करें | आसपास के लॉबी | फ़ोन पर लोकल AI |
| --- | --- | --- | --- |
| <img src="../images/apps/android-pairing.png" alt="पेयरिंग मोड: Local, Auto, Bluetooth, Cloud, एक बार का कोड और Peer Link" width="200"> | <img src="../images/apps/ios-chat.png" alt="आपके अपने कंप्यूटर पर चल रहे AI एजेंट से चैट" width="200"> | <img src="../images/apps/ios-lobbies.png" alt="Wi-Fi और Bluetooth से आसपास के लॉबी खोजें और बनाएँ" width="200"> | <img src="../images/apps/android-local-ai.png" alt="फ़ोन पर लोकल AI से चैट" width="200"> |

- **लॉबी।** Wi-Fi और Bluetooth से आसपास के कमरे बनाएँ या उनमें जुड़ें, होस्ट के अलावा छह मेहमानों तक के लिए चैट और ऑडियो या वीडियो के साथ।
- **Peer Link।** एक ऐप को दूसरे से जोड़ें। पाने वाला ऐप हर लिंक को मंज़ूरी देता है, और वैकल्पिक AI जवाब सिर्फ़ वही इस्तेमाल करते हैं जो आपने उसे बताया।
- **आपका कंप्यूटर, हर जगह।** चैट, कॉल, रिमोट डेस्कटॉप, गेम मोड, एजेंट वेबसाइटें और प्रकाशित पोर्ट, हर पेयर किए गए डिवाइस पर।

<a id="mcp"></a>
<a id="use-autoyou-from-your-coding-agent"></a>

## अपने कोडिंग एजेंट से AutoYou इस्तेमाल करें

ChatGPT, Codex, Claude Code या Claude Desktop, Google Antigravity, Cursor और VS Code की MCP-सक्षम सुविधाओं को AutoYou के समर्थित टूल से जोड़ें। लोकल MCP एडैप्टर एक अलग प्रोसेस है; सर्वर इसे इन क्लाइंट में इंस्टॉल नहीं करता, और MCP कॉल सिर्फ़ एडैप्टर के समर्थित टूल खोलती हैं, आपकी फ़ाइलों तक मनमाना एक्सेस या AutoYou की सभी सुविधाएँ नहीं।

1. AutoYou Server शुरू करें और **http://127.0.0.1:8001/** खोलें।
2. **Admin UI > Messaging > AutoYou MCP** में MCP चालू करें, निजी एडैप्टर टोकन बनाएँ और `autoyou-mcp.env` डाउनलोड करें। इस फ़ाइल को निजी रखें।
3. अलग से रखरखाव किए जाने वाले एडैप्टर को एक समर्पित Python एनवायरनमेंट में इंस्टॉल करें:

   ```powershell
   git clone https://github.com/autoyou-ai/AutoYou.git
   cd AutoYou
   py -3 -m venv .venv-mcp
   .\.venv-mcp\Scripts\python.exe -m pip install -e .\plugins\autoyou-mcp
   ```

   macOS या Linux पर `python3 -m venv .venv-mcp` और `.venv-mcp/bin/python -m pip install -e ./plugins/autoyou-mcp` इस्तेमाल करें।

जो क्लाइंट लोकल MCP प्रोसेस शुरू करते हैं, उनके लिए यह कमांड पूरे (absolute) पाथ के साथ इस्तेमाल करें:

```text
<path-to-.venv-mcp-python> -m autoyou_mcp --env-file <path-to-autoyou-mcp.env> --transport stdio
```

<details>
<summary><strong>हर क्लाइंट में AutoYou जोड़ें</strong></summary>

जो क्लाइंट `mcpServers` JSON फ़ॉर्मेट इस्तेमाल करते हैं, उनके लिए एंट्री ऐसी दिखती है। दोनों उदाहरण पाथ बदलें, और JSON में Windows बैकस्लैश इस्तेमाल करें तो उन्हें एस्केप करें:

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

**ChatGPT वेब** रिमोट MCP कनेक्शन इस्तेमाल करता है, लोकल `stdio` प्रोसेस नहीं। एडैप्टर को `--transport streamable-http` के साथ शुरू करें और उसके लोकल एंडपॉइंट `http://127.0.0.1:8071/mcp` को OpenAI Secure MCP Tunnel से जोड़ें। ChatGPT के लिए डेवलपर मोड और उपयुक्त वर्कस्पेस या एडमिन एक्सेस चाहिए, और OpenAI योजना के अनुसार पूरे MCP एक्शन सीमित करता है। [ChatGPT के लिए AutoYou MCP सेटअप](https://github.com/autoyou-ai/AutoYou/blob/main/plugins/autoyou-mcp/README.md#connect-chatgpt-web) और [OpenAI की MCP ऐप्स गाइड](https://help.openai.com/en/articles/12584461-developer-mode-and-mcp-apps-in-chatgpt) देखें।

**Codex CLI, डेस्कटॉप ऐप और IDE एक्सटेंशन** Codex होस्ट का MCP कॉन्फ़िगरेशन साझा करते हैं:

```powershell
codex mcp add autoyou -- "C:/path/to/AutoYou/.venv-mcp/Scripts/python.exe" -m autoyou_mcp --env-file "C:/path/to/autoyou-mcp.env" --transport stdio
codex mcp list
```

या `~/.codex/config.toml` में `[mcp_servers.autoyou]` के तहत समतुल्य `command` और `args` जोड़ें। [Codex MCP गाइड](https://learn.chatgpt.com/docs/extend/mcp?surface=cli) देखें।

**Claude Code:**

```powershell
claude mcp add --transport stdio autoyou -- "C:/path/to/AutoYou/.venv-mcp/Scripts/python.exe" -m autoyou_mcp --env-file "C:/path/to/autoyou-mcp.env" --transport stdio
claude mcp list
```

फिर Claude Code में `/mcp` चलाएँ। [Claude Code MCP गाइड](https://code.claude.com/docs/en/mcp) देखें।

**Claude Desktop** एडैप्टर को लोकल stdio सर्वर के रूप में चलाता है। macOS पर `~/Library/Application Support/Claude/claude_desktop_config.json` में या Windows पर `%APPDATA%\Claude\claude_desktop_config.json` में `mcpServers.autoyou` एंट्री जोड़ें, फिर Claude Desktop को पूरी तरह बंद करके दोबारा खोलें। AutoYou अभी `.mcpb` डेस्कटॉप एक्सटेंशन नहीं देता। [Claude Desktop MCP गाइड](https://support.claude.com/en/articles/10949351-getting-started-with-local-mcp-servers-on-claude-desktop) देखें।

**Google Antigravity** वैश्विक रूप से `~/.gemini/config/mcp_config.json` या वर्कस्पेस में `.agents/mcp_config.json` इस्तेमाल करता है। Antigravity IDE में **MCP Servers > Manage MCP Servers > View raw config** खोलें। [Antigravity MCP गाइड](https://antigravity.google/docs/mcp) देखें।

**Cursor** उसी `mcpServers` एंट्री के साथ `~/.cursor/mcp.json` या `.cursor/mcp.json` इस्तेमाल करता है। [Cursor MCP गाइड](https://prod.cursor.com/help/customization/mcp) देखें।

**VS Code** वर्कस्पेस रूट की पोर्टेबल `.mcp.json` फ़ाइल इस्तेमाल कर सकता है। `.vscode/mcp.json` इसकी जगह `"type": "stdio"` के साथ टॉप-लेवल `servers` ऑब्जेक्ट इस्तेमाल करता है। [VS Code MCP गाइड](https://code.visualstudio.com/docs/agent-customization/mcp-servers) देखें।

macOS या Linux पर Python एक्ज़ीक्यूटेबल `.venv-mcp/bin/python` है। क्लाइंट संस्करण, सेटिंग्स, संगठन की नीतियाँ और टूल की मंज़ूरी उपलब्धता पर असर डाल सकती हैं।

</details>

जोड़ने के बाद, कोई भी लिखने वाला टूल इस्तेमाल करने से पहले जाँचें कि `get_autoyou_status` दिखता है और सर्वर की अपेक्षित स्थिति लौटाता है। दायरा, सुरक्षा मॉडल और सीमाओं के लिए [AutoYou MCP इंटीग्रेशन गाइड](../guides/mcp-integration.mdx) देखें।

<a id="admin-console"></a>

## एडमिन कंसोल

<p align="center">
  <img src="../images/admin-tour.gif" alt="कंसोल का दौरा: सर्वर अवलोकन, डिवाइस और लाइव गतिविधि, एक्सेस और सुरक्षा" width="760" />
</p>

लोकल कंसोल सेटअप, मॉडल, वॉइस, एजेंट, कनेक्शन, अनुमतियाँ और सुरक्षा को एक जगह रखता है। गाइडेड सेटअप पूछता है कि आप AutoYou का क्या उपयोग करना चाहते हैं और उपयुक्त एजेंट व अनुमतियाँ सुझाता है; जब तक आप योजना देखकर सेव न करें, कुछ नहीं बदलता। स्क्रीनशॉट उदाहरण मानों का उपयोग करते हैं। पूरी छवि खोलने के लिए कोई पूर्वावलोकन चुनें।

| सर्वर अवलोकन | डिवाइस और गतिविधि | एक्सेस और सुरक्षा |
| --- | --- | --- |
| [<img src="../images/admin/overview.png" alt="सर्वर अवलोकन, सेवाएँ और सेटअप नियंत्रण" width="280">](../images/admin/overview.png) | [<img src="../images/admin/live-view.png" alt="डिवाइस कनेक्शन, कार्यों और मैसेजिंग इंटीग्रेशन के साथ लाइव दृश्य" width="280">](../images/admin/live-view.png) | [<img src="../images/admin/security.png" alt="सर्वर पासवर्ड, ऑथेंटिकेटर और सुरक्षा सेटिंग्स" width="280">](../images/admin/security.png) |

<a id="other-ways-to-run"></a>

## चलाने के अन्य तरीके

<details>
<summary><strong>Docker</strong></summary>

`AUTOYOU_SERVER_PASSWORD` को सुरक्षित तरीके से दें, फिर चलाएँ:

```bash
docker compose up --build
```

Compose फ़ाइल पोर्ट सिर्फ़ होस्ट के loopback पर प्रकाशित करती है और कॉन्फ़िगरेशन एक नामित वॉल्यूम में रखती है। उसी कंप्यूटर पर `http://localhost:8001/` खोलें; फ़ाइल इस loopback-only प्रकाशन की घोषणा करती है (`AUTOYOU_ADMIN_TRUST_PUBLISHED_LOOPBACK=1`), इसलिए लॉगिन "requires localhost or HTTPS" के साथ अस्वीकार नहीं होता। सादे `docker run` के साथ `127.0.0.1` पर प्रकाशित करें और वही वेरिएबल दें, या आगे TLS प्रॉक्सी लगाएँ। loopback अलगाव पर भरोसा करने से पहले जाँचें कि आपका Docker संस्करण पोर्ट कैसे प्रकाशित करता है।

बिल्ड और रनटाइम पर Tunnelmole डाउनलोड बंद करने के लिए बिल्ड और शुरू करने से पहले `AUTOYOU_SKIP_TUNNELMOLE_DOWNLOAD=1` और `AUTOYOU_NO_DOWNLOAD_TUNNELMOLE=1` सेट करें। तब इस पर निर्भर सुविधाओं को अलग से दिया गया बाइनरी चाहिए होगा।

</details>

<details>
<summary><strong>एक अनौपचारिक लोकल बिल्ड बनाएँ</strong></summary>

रिपॉज़िटरी रूट से:

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

इन तरीकों के लिए AutoYou खाता या आधिकारिक रिलीज़ की मंज़ूरी ज़रूरी नहीं। ये डिपेंडेंसी इंस्टॉल या समायोजित कर सकते हैं। दिखने पर लाइसेंस और सूचनाएँ पढ़ें। पढ़ने के बाद बिना पूछे स्वीकार करने के लिए Windows पर `-AcceptTerms` या macOS और Linux पर `--accept-terms` दें।

आवश्यकताएँ, आउटपुट का स्थान और ज्ञात सीमाएँ:
[Windows](../../servers/windows/README.md) · [macOS](../../servers/macos/README.md) · [Linux / WSL](../../servers/wsl/README.md).

</details>

<a id="faq"></a>

## FAQ

<details>
<summary><strong>क्या क्लाउड अकाउंट या VPN चाहिए?</strong></summary>

नहीं। Local Pair, Bluetooth Pair, Live Pair, वन-टाइम कोड और Auto Pair बिना AutoYou अकाउंट के चलते हैं। Cloud Pair एक वैकल्पिक पेड सहायक है। अगर आप पहले से Tailscale जैसा VPN इस्तेमाल करते हैं, तो AutoYou उस पर भी चलता है।

</details>

<details>
<summary><strong>क्या AI चैट सच में अनलिमिटेड है?</strong></summary>

AutoYou मैसेज की कोई सीमा नहीं लगाता। लोकल Ollama मॉडल के साथ हर मैसेज मुफ़्त है, और रफ़्तार आपके कंप्यूटर पर निर्भर करती है। आपके जोड़े क्लाउड प्रोवाइडर की अपनी क़ीमत और सीमाएँ होती हैं।

</details>

<details>
<summary><strong>क्या AutoYou मेरी फ़ाइलें या बातचीत अपलोड करता है?</strong></summary>

वे आपके कंप्यूटर पर रहती हैं। डेटा सिर्फ़ उन सेवाओं से बाहर जाता है जिन्हें आप चालू करते हैं, जैसे क्लाउड मॉडल प्रोवाइडर, मैसेजिंग ब्रिज या पब्लिक टनल, और हर सेवा की अपनी शर्तें हैं।

</details>

<details>
<summary><strong>मैं किन भाषाओं में बोल सकता हूँ?</strong></summary>

जब **STT language** `auto` पर हो और कोई बहुभाषी मॉडल चुना हो, तो स्पीच रिकग्निशन भाषा पहचान लेता है। बोले गए जवाब मेल खाती सिस्टम आवाज़ या लोकल Supertonic 3 मॉडल का उपयोग करते हैं, जो 31 भाषाएँ कवर करता है। आपके चैट मॉडल को भी वह भाषा समझनी चाहिए।

</details>

<details>
<summary><strong>क्या मैं कोई वेब ऐप या गेम दोस्तों के साथ साझा कर सकता हूँ?</strong></summary>

हाँ। जो भी आपके सर्वर से पेयर करता है, वह आपकी दी गई भूमिका के भीतर आपकी एजेंट वेबसाइटें और प्रकाशित पोर्ट खोल सकता है। साझा डिवाइस साझा के रूप में चिह्नित होते हैं, ताकि आपकी साइटें सीमित कर सकें कि वे क्या देखें। जो पेयर नहीं हैं, उनके लिए Hosting एजेंट मुफ़्त अस्थायी लिंक और सशुल्क स्थायी URL समझाता है।

</details>

<details>
<summary><strong>अगर मुझे कोई बग मिले तो?</strong></summary>

पहले अपडेट करें; हो सकता है नया संस्करण उसे ठीक कर चुका हो। समस्या बनी रहे तो अपने AutoYou संस्करण (`VERSION`), ऑपरेटिंग सिस्टम, प्रोफ़ाइल, दोहराने के चरण और त्रुटि आउटपुट के साथ [issue खोलें](https://github.com/autoyou-ai/AutoYou-Server/issues)। सुरक्षा कमज़ोरियाँ [SECURITY.md](../../SECURITY.md) के अनुसार निजी रूप से बताएँ।

</details>

<a id="documentation"></a>

## दस्तावेज़

- [क्विक स्टार्ट](../quickstart.mdx) · [आर्किटेक्चर](../architecture/) · [सुरक्षा और निजता](../security/) · [एडमिन कंसोल](../admin-ui/)
- [एजेंट कैटलॉग](../../autoyou_agents/README.md) · [एजेंट रेफ़रेंस](../agents/) · [एजेंट फ़्रेमवर्क](../agent-framework/)
- [HTTP और WebSocket API](../api-reference/) · [गाइड](../guides/) · [पेयरिंग गाइड](../../guides/README.md)

<a id="contributing"></a>

## योगदान

आपकी मदद का स्वागत है। [बिल्ट-इन एजेंट](../../autoyou_agents/README.md), [Agent Builder](../admin-ui/agent-workbench-ui.mdx) या [कस्टम एजेंट ट्यूटोरियल](../guides/custom-agent-tutorial.mdx) से शुरू करें।

- **बनाएँ** एक एजेंट, गेम, एक्सटेंशन, इंटीग्रेशन या वेब इंटरफ़ेस।
- **सुधारें** इंस्टॉलेशन, एक्सेसिबिलिटी, प्लेटफ़ॉर्म सपोर्ट, अनुवाद, दस्तावेज़ या कोई दोहराने योग्य बग।
- **साझा करें** कोई डेमो, गाइड या स्पष्ट रूप से पहचाना गया अनौपचारिक बिल्ड जो लाइसेंस का पालन करता हो।

काम भेजने से पहले [CONTRIBUTING.md](../../CONTRIBUTING.md) और [आचार संहिता](../../CODE_OF_CONDUCT.md) पढ़ें। मूल, स्वतंत्र कोड आपकी पसंद का लाइसेंस इस्तेमाल कर सकता है, जिसमें MIT या Apache-2.0 शामिल हैं। मौजूदा AutoYou कोड में बदलाव उसका लाइसेंस बनाए रखते हैं।

<a id="license"></a>

## लाइसेंस

AutoYou Server [सर्वर लाइसेंस](../../LICENSE) के तहत source-available है। यह अभी OSI-स्वीकृत ओपन-सोर्स प्रोजेक्ट नहीं है, और `LICENSE` की परिभाषाएँ मान्य होती हैं।

| सामग्री या उपयोग | शर्तें |
| --- | --- |
| व्यक्तिगत उपयोग, लोकल बिल्ड और मुफ़्त वितरण | सर्वर लाइसेंस और उसकी शर्तों के तहत अनुमत |
| AutoYou कोड में बदलाव | लागू AutoYou लाइसेंस बनाए रखते हैं |
| मूल, स्वतंत्र और अलग किया जा सकने वाला कोड | आपकी पसंद का लाइसेंस, जिसमें MIT या Apache-2.0 शामिल हैं |
| संस्थागत उपयोग या व्यावसायिक दोहन | अलग [Enterprise समझौता](https://www.autoyou.me/enterprise/) |

सूचनाएँ बनाए रखें, अनौपचारिक बिल्ड की पहचान करें, और शामिल घटकों के दायित्व पूरे करें। [THIRD-PARTY-NOTICES.md](../../THIRD-PARTY-NOTICES.md) और [ट्रेडमार्क नीति](../legal/trademark-policy.md) देखें।

OpenStorey योग्य शुद्ध राजस्व का कम से कम 15% योगदानकर्ता पूल में देता है, जो उसके [समीक्षा और पात्रता नियमों](../contributors/contributor-pool.md) के अनुसार बाँटा जाता है। [फ़ंडिंग रोडमैप](https://www.autoyou.me/donate/) में सर्वर कोड खोलने के लिए 50 लाख डॉलर का पड़ाव शामिल है; कोई पड़ाव अपने आप आपके पास पहले से मौजूद कॉपी का लाइसेंस नहीं बदलता। विवरण: [docs/legal/open-source-commitment.md](../legal/open-source-commitment.md)।

<a id="support"></a>

## सहायता

- **प्रश्न और इंस्टॉलेशन में मदद:** [SUPPORT.md](../../SUPPORT.md) और [समुदाय](https://www.autoyou.me/community/)।
- **बग और ठोस प्रस्ताव:** [issue खोलें](https://github.com/autoyou-ai/AutoYou-Server/issues)।
- **सुरक्षा कमज़ोरियाँ:** [SECURITY.md](../../SECURITY.md) में बताए अनुसार निजी रूप से रिपोर्ट करें। अप्रकाशित कमज़ोरियों के लिए सार्वजनिक issue का उपयोग न करें।

एजेंट सर्वर की अनुमतियों के साथ चलते हैं: देखें कि वे क्या करते हैं, सिर्फ़ ज़रूरी चीज़ें चालू करें, और बैकअप रखें।

Copyright &copy; 2026 OpenStorey LLC and contributors.

/* AutoYou Assistant website.
 *
 * Listens for the wake word in this browser with the same engine as the Admin
 * page, asks the Assistant agent, and speaks the answer. The conversation runs
 * on audio callbacks and timers, never on animation frames, so it keeps going
 * in a hidden tab, a kiosk with the screen off, or a headless browser; the
 * robot is drawn only when someone can see it.
 *
 * Address options: ?headless=1 (no robot, sounds on), ?autostart=0|1,
 * ?voice=server|browser|off.
 */
(function () {
  "use strict";

  if (/\/agent\/assistant_agent$/.test(location.pathname)) {
    location.replace(location.pathname + "/" + location.search + location.hash);
    return;
  }

  var SR = 16000;
  var params = new URLSearchParams(location.search);
  var $ = function (id) { return document.getElementById(id); };
  var ui = {
    body: document.body, status: $("status-text"), hint: $("hint"), you: $("you-text"), reply: $("reply-text"),
    mic: $("mic-btn"), micLabel: $("mic-btn-label"), robotButton: $("robot-button"), settings: $("settings"),
    settingsBtn: $("settings-btn"), settingsClose: $("settings-close"), facts: $("server-facts"),
    unlock: $("unlock"), unlockBtn: $("unlock-btn"), unlockTitle: $("unlock-title"), unlockSub: $("unlock-sub"),
    askForm: $("ask-form"), askInput: $("ask-input"), signout: $("signout-btn")
  };

  // ---- Preferences for this device ------------------------------------------
  var STORE = "autoyou-assistant:";
  function load(key, fallback) {
    try { var v = localStorage.getItem(STORE + key); return v === null ? fallback : JSON.parse(v); }
    catch (e) { return fallback; }
  }
  function save(key, value) { try { localStorage.setItem(STORE + key, JSON.stringify(value)); } catch (e) { /* private mode */ } }
  var headlessParam = params.get("headless") === "1";
  var prefs = {
    autoStart: load("autoStart", null),           // null: follow the server setting
    speak: load("speak", true),
    sounds: load("sounds", true),
    robot: headlessParam ? false : load("robot", true),
    voice: ["server", "browser"].indexOf(params.get("voice")) >= 0 ? params.get("voice") : load("voice", "server")
  };
  var voiceOff = params.get("voice") === "off";

  function id(prefix) {
    var bytes = new Uint8Array(12);
    (window.crypto || window.msCrypto).getRandomValues(bytes);
    return prefix + Array.prototype.map.call(bytes, function (b) { return ("0" + b.toString(16)).slice(-2); }).join("");
  }
  var sessionId = load("session", "");
  if (!/^[A-Za-z0-9_.:-]{8,96}$/.test(sessionId)) { sessionId = id("s"); save("session", sessionId); }

  // ---- State ------------------------------------------------------------------
  var S = {
    status: null, phase: "off", listenerId: id("w"),
    capture: null, out: null, detector: null, modelRevision: "",
    endpoint: null, chunks: [], waited: 0, busy: false, via: "", wake: null, followUp: false,
    speech: null, turnTicket: 0, level: 0, outLevel: 0, bands: new Float32Array(7),
    greetUntil: 0, perk: 0, errorUntil: 0, wantListening: false, reopenTimer: 0, wakeLock: null
  };

  // ---- Server calls -----------------------------------------------------------
  var reloading = false;
  function request(path, body, asBinary) {
    var options = { method: body === undefined ? "GET" : "POST", credentials: "same-origin", headers: {} };
    if (body !== undefined) { options.headers["Content-Type"] = "application/json"; options.body = JSON.stringify(body); }
    return fetch("./" + path, options).then(function (response) {
      if (response.status === 401 && !reloading) {
        reloading = true;
        setTimeout(function () { location.reload(); }, 400);
      }
      if (asBinary && response.ok) return response.arrayBuffer();
      return response.json().catch(function () { return {}; }).then(function (data) {
        if (!response.ok || data.success === false) {
          var error = new Error(data.error || ("Request failed (" + response.status + ")"));
          error.status = response.status; error.data = data;
          throw error;
        }
        return data;
      });
    });
  }

  // ---- Small helpers ----------------------------------------------------------
  function setPhase(phase) {
    S.phase = phase;
    ui.body.setAttribute("data-phase", phase);
    renderStatus();
  }
  function phrase() { return (S.status && S.status.wake_phrase) || "AutoYou"; }
  function renderStatus() {
    var listening = S.status ? S.status.listening : "";
    var text = {
      off: "Not listening",
      starting: "Starting the microphone…",
      waiting: listening === "needs_training" ? "Tap me, then speak" : "Say “" + phrase() + "”",
      command: S.followUp ? "Anything else?" : "I'm listening…",
      thinking: "Thinking…",
      speaking: "Speaking…",
      error: "Something went wrong"
    }[S.phase] || "";
    ui.status.textContent = text;
    var live = Boolean(S.capture);
    ui.mic.setAttribute("aria-pressed", live ? "true" : "false");
    ui.mic.title = live ? "Stop listening" : "Start listening";
    ui.micLabel.textContent = ui.mic.title;
    ui.robotButton.setAttribute("aria-label", text + ". " + (live ? "Tap to talk now." : "Tap to start listening."));
  }
  function hint(text, tone) {
    ui.hint.textContent = text || "";
    ui.hint.className = "hint" + (tone ? " is-" + tone : "");
  }
  function join(chunks) {
    var total = 0, i;
    for (i = 0; i < chunks.length; i += 1) total += chunks[i].length;
    var out = new Float64Array(total), offset = 0;
    for (i = 0; i < chunks.length; i += 1) { out.set(chunks[i], offset); offset += chunks[i].length; }
    return out;
  }
  function wav(samples) {
    var n = samples.length, buffer = new ArrayBuffer(44 + n * 2), view = new DataView(buffer);
    function text(offset, value) { for (var i = 0; i < value.length; i += 1) view.setUint8(offset + i, value.charCodeAt(i)); }
    text(0, "RIFF"); view.setUint32(4, 36 + n * 2, true); text(8, "WAVE"); text(12, "fmt ");
    view.setUint32(16, 16, true); view.setUint16(20, 1, true); view.setUint16(22, 1, true);
    view.setUint32(24, SR, true); view.setUint32(28, SR * 2, true); view.setUint16(32, 2, true); view.setUint16(34, 16, true);
    text(36, "data"); view.setUint32(40, n * 2, true);
    for (var i = 0; i < n; i += 1) {
      var s = Math.max(-1, Math.min(32767 / 32768, samples[i]));
      view.setInt16(44 + i * 2, Math.round(s * 32768), true);
    }
    var bytes = new Uint8Array(buffer), binary = "", chunk = 0x8000;
    for (var offset = 0; offset < bytes.length; offset += chunk) {
      binary += String.fromCharCode.apply(null, bytes.subarray(offset, offset + chunk));
    }
    return btoa(binary);
  }
  function rmsDbfs(samples) {
    if (window.AutoYouWakeWord) return window.AutoYouWakeWord.rmsDbfs(samples);
    var sum = 0;
    for (var i = 0; i < samples.length; i += 1) sum += samples[i] * samples[i];
    return 10 * Math.log10(sum / Math.max(1, samples.length) + 1e-12);
  }
  // Speech start and end on audio time, the server's rule.
  function endpointer() { return { noise: -60, speech: 0, silence: 0, started: false }; }
  function observe(endpoint, samples) {
    var seconds = samples.length / SR, level = rmsDbfs(samples);
    var speech = level >= Math.max(-50, endpoint.noise + 10);
    if (!speech) {
      var weight = level < endpoint.noise ? 0.3 : 0.02;
      endpoint.noise = (1 - weight) * endpoint.noise + weight * level;
      endpoint.silence += seconds;
      if (!endpoint.started) endpoint.speech = 0;
    } else {
      endpoint.silence = 0;
      endpoint.speech += seconds;
      if (endpoint.speech >= 0.06) endpoint.started = true;
    }
    return speech;
  }

  // ---- Sound out: replies and listening chimes ------------------------------
  function outContext() {
    if (S.out) return S.out;
    var Context = window.AudioContext || window.webkitAudioContext;
    if (!Context) return null;
    var ctx = new Context();
    var analyser = ctx.createAnalyser();
    analyser.fftSize = 512;
    analyser.smoothingTimeConstant = 0.5;
    analyser.connect(ctx.destination);
    S.out = { ctx: ctx, analyser: analyser, data: new Float32Array(analyser.fftSize) };
    return S.out;
  }
  function audioRunning(ctx) {
    if (!ctx) return Promise.resolve(false);
    if (ctx.state === "running") return Promise.resolve(true);
    var timer;
    return Promise.race([
      ctx.resume().then(function () { return ctx.state === "running"; }, function () { return false; }),
      new Promise(function (resolve) { timer = setTimeout(function () { resolve(false); }, 1200); })
    ]).then(function (ok) { clearTimeout(timer); return ok; });
  }
  function chime(kind) {
    if (!prefs.sounds) return;
    var out = outContext();
    if (!out || out.ctx.state !== "running") return;
    var notes = { wake: [660, 990], done: [880, 660], error: [260, 200], ready: [523, 784] }[kind] || [700];
    var ctx = out.ctx, now = ctx.currentTime;
    notes.forEach(function (frequency, index) {
      var osc = ctx.createOscillator(), gain = ctx.createGain(), start = now + index * 0.09;
      osc.type = "sine";
      osc.frequency.setValueAtTime(frequency, start);
      gain.gain.setValueAtTime(0.0001, start);
      gain.gain.exponentialRampToValueAtTime(kind === "error" ? 0.08 : 0.12, start + 0.015);
      gain.gain.exponentialRampToValueAtTime(0.0001, start + 0.16);
      osc.connect(gain); gain.connect(ctx.destination);
      osc.start(start); osc.stop(start + 0.18);
    });
  }

  // ---- Unlock: browsers may wait for a tap before audio starts ---------------
  var unlockWaiters = [];
  function needTap(title, sub) {
    ui.unlockTitle.textContent = title || "Tap to start listening";
    ui.unlockSub.textContent = sub || "This browser waits for a tap before it uses the microphone and speaker.";
    ui.unlock.hidden = false;
    return new Promise(function (resolve) { unlockWaiters.push(resolve); });
  }
  function onUnlockTap() {
    ui.unlock.hidden = true;
    var contexts = [S.capture && S.capture.ctx, outContext() && outContext().ctx];
    Promise.all(contexts.map(function (ctx) { return ctx ? ctx.resume().catch(function () {}) : null; })).then(function () {
      if (window.speechSynthesis) { try { window.speechSynthesis.resume(); } catch (e) { /* not supported */ } }
      var waiters = unlockWaiters; unlockWaiters = [];
      waiters.forEach(function (resolve) { resolve(); });
      if (S.wantListening && !S.capture) start();
    });
  }

  // ---- Microphone --------------------------------------------------------------
  function openCapture() {
    if (window.isSecureContext === false) {
      return Promise.reject(new Error("The microphone needs HTTPS (or this computer's own address). Open the Assistant through the HTTPS address and trust this server's certificate."));
    }
    if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
      return Promise.reject(new Error("This browser cannot use a microphone."));
    }
    return navigator.mediaDevices.getUserMedia({
      audio: { channelCount: 1, echoCancellation: true, noiseSuppression: true, autoGainControl: true }
    }).then(function (stream) {
      var Context = window.AudioContext || window.webkitAudioContext, ctx, source;
      try { ctx = new Context({ sampleRate: SR }); source = ctx.createMediaStreamSource(stream); }
      catch (error) {
        // Firefox will not mix a 16 kHz context with a device at another rate.
        if (ctx) { try { ctx.close(); } catch (e) { /* closed */ } }
        ctx = new Context(); source = ctx.createMediaStreamSource(stream);
      }
      var analyser = ctx.createAnalyser();
      analyser.fftSize = 256;
      analyser.smoothingTimeConstant = 0.55;
      source.connect(analyser);
      var ratio = ctx.sampleRate / SR, position = 0, previous = 0;
      var node = ctx.createScriptProcessor(4096, 1, 1);
      node.onaudioprocess = function (event) {
        var input = event.inputBuffer.getChannelData(0);
        if (Math.abs(ratio - 1) < 1e-9) { onChunk(Float64Array.from(input)); return; }
        var out = [];
        for (;;) {
          var whole = Math.floor(position), fraction = position - whole;
          if (whole + 1 >= input.length) break;
          var first = whole < 0 ? previous : input[whole];
          out.push(first + (input[whole + 1] - first) * fraction);
          position += ratio;
        }
        position -= input.length;
        previous = input[input.length - 1];
        onChunk(Float64Array.from(out));
      };
      source.connect(node);
      // Silent output: Chrome only runs a processor that reaches the destination.
      var mute = ctx.createGain();
      mute.gain.value = 0;
      node.connect(mute); mute.connect(ctx.destination);
      stream.getAudioTracks().forEach(function (track) {
        track.addEventListener("ended", function () { if (S.capture && S.capture.stream === stream) micLost(); });
      });
      return { ctx: ctx, stream: stream, source: source, node: node, analyser: analyser, freq: new Uint8Array(analyser.frequencyBinCount) };
    });
  }
  function closeCapture() {
    var capture = S.capture;
    S.capture = null;
    if (!capture) return;
    try { capture.node.disconnect(); capture.source.disconnect(); } catch (e) { /* gone */ }
    capture.stream.getTracks().forEach(function (track) { track.stop(); });
    try { var closing = capture.ctx.close(); if (closing && closing.catch) closing.catch(function () {}); } catch (e) { /* closed */ }
  }
  function micLost() {
    closeCapture();
    setPhase("starting");
    hint("The microphone went away. Trying again…", "warn");
    clearTimeout(S.reopenTimer);
    S.reopenTimer = setTimeout(function () { if (S.wantListening) start(); }, 2500);
  }

  // ---- Wake word model -------------------------------------------------------
  function loadDetector() {
    S.detector = null;
    var status = S.status;
    if (!status || status.listening !== "voice" || !window.AutoYouWakeWord) return Promise.resolve();
    return request("api/assistant/model").then(function (data) {
      var model = window.AutoYouWakeWord.loadModel(data.model);
      S.detector = new window.AutoYouWakeWord.Detector(model, (model.maxFrames * 160) / SR + 0.3);
      S.modelRevision = status.model_revision || "";
    }).catch(function (error) {
      hint("The wake word could not be loaded: " + error.message, "warn");
    });
  }

  // ---- Listening ---------------------------------------------------------------
  function start() {
    S.wantListening = true;
    if (S.capture) return Promise.resolve();
    if (!S.status || !S.status.enabled) {
      setPhase("off");
      hint("Assistant mode is off on the server. Turn it on in the Admin page.", "warn");
      return Promise.resolve();
    }
    setPhase("starting");
    return openCapture().then(function (capture) {
      if (!S.wantListening) { S.capture = capture; closeCapture(); return; }
      S.capture = capture;
      return audioRunning(capture.ctx).then(function (running) {
        if (!running) {
          return needTap().then(function () { return audioRunning(capture.ctx); });
        }
        return true;
      }).then(function () {
        outContext() && audioRunning(outContext().ctx);
        return loadDetector();
      }).then(function () {
        resetToWaiting();
        if (S.status.listening === "needs_training") {
          hint("No wake word is trained yet, so tap me (or press Space) and speak. Train one on the Admin page to talk hands-free.", "warn");
        } else if (S.status.listening === "text") {
          hint("Listening for “" + phrase() + "”. Short bits of speech are checked for the word, then discarded.");
        } else {
          hint("");
        }
        greet();
        requestWakeLock();
        // A device with no screen cannot read the hint above: say it once.
        if (!prefs.robot && S.status.listening === "needs_training") {
          var ticket = ++S.turnTicket;
          speakReply("I'm on, but no wake word is trained yet. Train one on the AutoYou admin page, or choose typed text only.", ticket)
            .then(function () { if (ticket === S.turnTicket) resetToWaiting(); });
        }
      });
    }).catch(function (error) {
      closeCapture();
      setPhase("off");
      var denied = error && (error.name === "NotAllowedError" || error.name === "SecurityError");
      hint(denied ? "Microphone access was blocked. Allow it for this site, then tap the robot." : (error.message || "The microphone could not start."), "error");
      chime("error");
    });
  }
  function stop() {
    S.wantListening = false;
    closeCapture();
    stopSpeaking();
    S.turnTicket += 1;
    setPhase("off");
    hint("Tap the robot or the microphone to listen again.");
    releaseWakeLock();
  }
  function resetToWaiting() {
    S.followUp = false;
    S.chunks = []; S.waited = 0; S.busy = false; S.wake = null; S.via = "";
    S.endpoint = endpointer();
    if (S.detector) S.detector.reset();
    setPhase(S.capture ? "waiting" : "off");
  }
  function beginCommand(preRoll, via, alreadyAccepted, followUp) {
    S.via = via;
    S.followUp = Boolean(followUp);
    S.wake = alreadyAccepted ? Promise.resolve(true) : request("api/assistant/wake", { listener_id: S.listenerId }).then(function (data) {
      if (!data.accepted) hint("Another device in the room is answering.", "warn");
      return Boolean(data.accepted);
    }).catch(function (error) { hint(error.message, "error"); return false; });
    S.chunks = preRoll ? [preRoll] : [];
    S.waited = 0;
    S.endpoint = endpointer();
    S.perk = 1;
    setPhase("command");
    if (!followUp) chime("wake");
  }

  function onChunk(chunk) {
    if (!S.capture) return;
    // Half duplex: what the speaker says is never taken as a question.
    if (S.phase === "thinking" || S.phase === "speaking" || S.phase === "starting" || S.phase === "off") return;
    var speech = observe(S.endpoint, chunk);
    if (S.phase === "waiting") {
      if (S.detector) {
        var hit = S.detector.feed(chunk);
        if (hit) beginCommand(S.status.confirm === "both" ? hit.preRoll : null, "voice", false, false);
        return;
      }
      if (S.status.listening !== "text" || S.busy) return;
      // Typed text only: short bursts are checked for the word, then discarded.
      if (speech || S.endpoint.started) S.chunks.push(chunk); else S.chunks = [];
      var burst = join(S.chunks).length / SR;
      if (S.endpoint.started && (S.endpoint.silence >= 0.6 || burst >= 6)) {
        var audio = join(S.chunks);
        S.chunks = []; S.endpoint = endpointer(); S.busy = true;
        request("api/assistant/transcribe", {
          audio_base64: wav(audio), require_phrase: true, listener_id: S.listenerId, spot_wake: true
        }).then(function (result) {
          if (S.phase !== "waiting") return;
          if (result.matched && result.wake_accepted === false) { hint("Another device in the room is answering.", "warn"); return; }
          if (result.matched && result.command) { chime("wake"); return ask(result.command, "text"); }
          if (result.matched) beginCommand(null, "text", true, false);
        }).catch(function () {}).then(function () { S.busy = false; });
      }
      return;
    }
    if (S.phase !== "command") return;
    S.chunks.push(chunk);
    S.waited += chunk.length / SR;
    var window_ = Number((S.status && S.status.listen_seconds) || 8);
    if (!S.endpoint.started) {
      if (S.waited >= window_) {
        if (!S.followUp) hint("I didn't hear anything after the wake word.");
        resetToWaiting();
      }
      return;
    }
    var seconds = join(S.chunks).length / SR;
    if (S.endpoint.silence >= 1.0 || seconds >= 20) finishCommand();
  }

  function finishCommand() {
    var audio = join(S.chunks), via = S.via, followUp = S.followUp, wake = S.wake;
    S.chunks = [];
    setPhase("thinking");
    chime("done");
    var ticket = ++S.turnTicket;
    Promise.resolve(wake).then(function (accepted) {
      if (accepted === false) return null;
      var requirePhrase = S.status.confirm === "both" && via === "voice" && !followUp;
      return request("api/assistant/transcribe", { audio_base64: wav(audio), require_phrase: requirePhrase, listener_id: S.listenerId });
    }).then(function (result) {
      if (ticket !== S.turnTicket) return;
      if (!result) { resetToWaiting(); return; }
      var text = String(result.command || "").trim();
      if (!text) {
        hint(S.status.confirm === "both" && !result.matched && via === "voice" ? "That didn't sound like the wake word, so nothing was sent." : "Sorry, I didn't catch that.");
        resetToWaiting();
        return;
      }
      return ask(text, via);
    }).catch(function (error) {
      if (ticket !== S.turnTicket) return;
      fail(error.message || "The question could not be heard.");
    });
  }

  // ---- Asking and answering -------------------------------------------------
  function ask(text, via) {
    var ticket = ++S.turnTicket;
    stopSpeaking();
    ui.you.textContent = text;
    ui.reply.textContent = "";
    hint("");
    setPhase("thinking");
    return request("api/assistant/turn", { text: text, session_id: sessionId, wake: via || "" }).then(function (data) {
      if (ticket !== S.turnTicket) return;
      var reply = plain(data.reply || "") || "Done.";
      return speakReply(reply, ticket).then(function () {
        if (ticket !== S.turnTicket) return;
        afterReply(ticket);
      });
    }).catch(function (error) {
      if (ticket !== S.turnTicket) return;
      fail(error.message || "The assistant could not answer.");
    });
  }
  function afterReply(ticket) {
    if (!S.capture) { setPhase("off"); return; }
    if (!(S.status && S.status.follow_up)) { resetToWaiting(); return; }
    // A short window for the next question without the wake word. The mic
    // stays closed a moment longer so the end of the reply is not heard.
    setTimeout(function () {
      if (ticket === S.turnTicket && S.capture && (S.phase === "speaking" || S.phase === "thinking")) {
        beginCommand(null, "follow_up", true, true);
      }
    }, 450);
  }
  function fail(message) {
    S.errorUntil = performance.now() + 2200;
    setPhase("error");
    hint(message, "error");
    chime("error");
    setTimeout(function () { if (S.phase === "error") resetToWaiting(); }, 2200);
  }

  // Markdown and links read badly aloud and on a kiosk.
  function plain(text) {
    return String(text)
      .replace(/```[\s\S]*?```/g, " ")
      .replace(/!\[[^\]]*\]\([^)]*\)/g, "")
      .replace(/\[([^\]]+)\]\([^)]*\)/g, "$1")
      .replace(/`([^`]+)`/g, "$1")
      .replace(/^\s{0,3}#{1,6}\s+/gm, "")
      .replace(/^\s*[-*+]\s+/gm, "• ")
      .replace(/(\*\*|__)(.*?)\1/g, "$2")
      .replace(/(\*|_)(\S[^*_]*?)\1/g, "$2")
      .replace(/[ \t]+/g, " ")
      .replace(/\n{3,}/g, "\n\n")
      .trim();
  }
  // Sentences, joined into stretches that start fast and sound natural.
  function stretches(text) {
    var sentences = String(text).replace(/\s+/g, " ").match(/[^.!?…]+[.!?…]+["')\]]*\s*|[^.!?…]+$/g) || [text];
    var out = [], current = "";
    sentences.forEach(function (sentence) {
      var limit = out.length === 0 ? 140 : 320;
      if (current && (current + sentence).length > limit) { out.push(current.trim()); current = ""; }
      current += sentence;
      while (current.length > 560) { out.push(current.slice(0, 560)); current = current.slice(560); }
    });
    if (current.trim()) out.push(current.trim());
    return out.filter(Boolean);
  }
  function showReply(parts, index) {
    ui.reply.textContent = "";
    parts.forEach(function (part, i) {
      var span = document.createElement("span");
      span.className = i < index ? "said" : (i === index ? "saying" : "unsaid");
      span.textContent = part + " ";
      ui.reply.appendChild(span);
    });
    var current = ui.reply.querySelector(".saying");
    if (current && current.scrollIntoView) current.scrollIntoView({ block: "nearest" });
  }

  function speakingEnabled() {
    return !voiceOff && prefs.speak && (!S.status || S.status.speak_replies !== false);
  }
  function speakReply(text, ticket) {
    var parts = stretches(text);
    if (!speakingEnabled()) { showReply(parts, parts.length); return Promise.resolve(); }
    setPhase("speaking");
    var useServer = prefs.voice !== "browser";
    var fetches = [];
    function fetchPart(i) {
      if (!useServer || i >= parts.length) return null;
      if (!fetches[i]) fetches[i] = request("api/assistant/speak", { text: parts[i] }, true).catch(function (error) { return error; });
      return fetches[i];
    }
    function step(i) {
      if (ticket !== S.turnTicket) return Promise.resolve();
      if (i >= parts.length) { showReply(parts, parts.length); return Promise.resolve(); }
      showReply(parts, i);
      var pending = fetchPart(i);
      fetchPart(i + 1);
      var played = pending ? pending.then(function (audio) {
        if (audio instanceof Error || !audio) { useServer = false; return speakBrowser(parts[i]); }
        return playAudio(audio).catch(function () { useServer = false; return speakBrowser(parts[i]); });
      }) : speakBrowser(parts[i]);
      return played.then(function () { return step(i + 1); });
    }
    return step(0);
  }
  function playAudio(arrayBuffer) {
    var out = outContext();
    if (!out) return Promise.reject(new Error("No audio output"));
    return audioRunning(out.ctx).then(function (running) {
      if (running) return;
      // A screenless device may never be tapped: give up on sound after a
      // while so the conversation (and listening) carries on.
      return Promise.race([
        needTap("Tap to hear the answer", "This browser waits for a tap before it plays sound."),
        new Promise(function (resolve) { setTimeout(resolve, 12000); })
      ]).then(function () {
        if (out.ctx.state !== "running") { ui.unlock.hidden = true; throw new Error("Sound is blocked"); }
      });
    }).then(function () {
      return new Promise(function (resolve, reject) {
        out.ctx.decodeAudioData(arrayBuffer.slice(0), resolve, reject);
      });
    }).then(function (buffer) {
      return new Promise(function (resolve) {
        var source = out.ctx.createBufferSource();
        source.buffer = buffer;
        source.connect(out.analyser);
        var done = false;
        function finish() { if (done) return; done = true; S.speech = null; resolve(); }
        source.onended = finish;
        S.speech = { stop: function () { try { source.stop(); } catch (e) { /* ended */ } finish(); }, browser: false };
        source.start();
      });
    });
  }
  function speakBrowser(text) {
    var synth = window.speechSynthesis;
    if (!synth || typeof window.SpeechSynthesisUtterance !== "function") return Promise.resolve();
    return new Promise(function (resolve) {
      var utterance = new window.SpeechSynthesisUtterance(text.replace(/•/g, ""));
      var done = false;
      function finish() { if (done) return; done = true; S.speech = null; clearInterval(keepAlive); resolve(); }
      utterance.onend = finish; utterance.onerror = finish;
      utterance.onboundary = function () { S.outLevel = Math.max(S.outLevel, 0.75); };
      S.speech = { stop: function () { synth.cancel(); finish(); }, browser: true };
      // Chrome stops long utterances after ~15 s unless nudged.
      var keepAlive = setInterval(function () { if (synth.speaking) { synth.pause(); synth.resume(); } }, 10000);
      synth.speak(utterance);
      // Not started after a moment (no voices, or blocked): move on at once.
      setTimeout(function () { if (!synth.speaking && !synth.pending) finish(); }, 1500);
      setTimeout(finish, 5000 + text.length * 120);
    });
  }
  function stopSpeaking() {
    if (S.speech) S.speech.stop();
    if (window.speechSynthesis) { try { window.speechSynthesis.cancel(); } catch (e) { /* none */ } }
  }

  // ---- Talk now: tap, Space, or the microphone button -------------------------
  function talkNow() {
    if (!S.capture) { start(); return; }
    if (S.phase === "speaking") { S.turnTicket += 1; stopSpeaking(); resetToWaiting(); beginCommand(null, "tap", true, false); return; }
    if (S.phase === "waiting") { beginCommand(null, "tap", true, false); return; }
    if (S.phase === "command" && S.endpoint && S.endpoint.started) { finishCommand(); return; }
    if (S.phase === "command") { resetToWaiting(); }
  }

  // ---- Keep the screen and the page awake on a kiosk ------------------------
  function requestWakeLock() {
    if (!("wakeLock" in navigator) || document.visibilityState !== "visible" || S.wakeLock || !S.capture) return;
    navigator.wakeLock.request("screen").then(function (lock) {
      S.wakeLock = lock;
      lock.addEventListener("release", function () { S.wakeLock = null; });
    }).catch(function () { /* not allowed here */ });
  }
  function releaseWakeLock() { if (S.wakeLock) { S.wakeLock.release().catch(function () {}); S.wakeLock = null; } }

  // ---- Server settings ----------------------------------------------------------
  function refreshStatus(first) {
    return request("api/assistant/status").then(function (status) {
      var before = S.status;
      S.status = status;
      renderFacts();
      ui.signout.hidden = status.access === "open";
      if (!first && before && S.capture && (before.model_revision !== status.model_revision || before.listening !== status.listening)) {
        loadDetector().then(function () { if (S.phase === "waiting") resetToWaiting(); });
      }
      if (!status.enabled && S.capture) { stop(); hint("Assistant mode was turned off on the server.", "warn"); }
      renderStatus();
      return status;
    });
  }
  function renderFacts() {
    var s = S.status || {};
    var listening = { voice: "Your trained voice", text: "Spotted as text", needs_training: "Not trained yet (tap to talk)", off: "Off" }[s.listening] || "";
    var rows = [
      ["Wake word", s.wake_phrase || "—"],
      ["Heard as", listening],
      ["Opens listening", s.auto_start ? "Yes" : "No"],
      ["Speaks replies", s.speak_replies ? "Yes" : "No"],
      ["Follow-up questions", s.follow_up ? "Yes, without the word" : "No"],
      ["Access", s.access === "open" ? "No code needed" : "Authenticator code"]
    ];
    ui.facts.textContent = "";
    rows.forEach(function (row) {
      var dt = document.createElement("dt"), dd = document.createElement("dd");
      dt.textContent = row[0]; dd.textContent = row[1];
      ui.facts.appendChild(dt); ui.facts.appendChild(dd);
    });
  }

  // ---- The robot -------------------------------------------------------------
  var bot = {
    root: $("bot"), head: $("head"), torso: $("torso"), armL: $("arm-left"), armR: $("arm-right"),
    eyeL: $("eye-l"), eyeR: $("eye-r"), smile: $("mouth-smile"), mouth: $("mouth-open"), eq: $("eq"),
    think: $("think"), zzz: $("zzz"), antenna: $("antenna-glow"), earL: $("ear-l"), earR: $("ear-r"),
    core: $("core"), shadow: $("shadow"), visor: $("visor")
  };
  var eqBars = bot.eq ? bot.eq.querySelectorAll("rect") : [];
  var thinkDots = bot.think ? bot.think.querySelectorAll("circle") : [];
  var reduceMotion = window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  var anim = {
    gazeX: 0, gazeY: 0, gazeTX: 0, gazeTY: 0, nextSaccade: 0, blink: 1, nextBlink: 0, blinkStart: -1,
    tilt: 0, armR: -6, armL: 6, happy: 0, open: 0, eqOn: 0, thinkOn: 0, sleep: 0, pointer: null, pointerAt: 0, last: 0
  };
  function greet() { S.greetUntil = performance.now() + 1900; S.perk = 1; }
  function lerp(a, b, k) { return a + (b - a) * k; }
  // Easing per elapsed time, so a slow thin client animates at the same pace.
  var frameFactor = 1;
  function ease(a, b, k) { return a + (b - a) * (1 - Math.pow(1 - k, frameFactor)); }
  function setOpacity(el, value) { if (el) el.setAttribute("opacity", value.toFixed(3)); }

  function readLevels() {
    // Microphone: overall level and seven bands for the visor equaliser.
    var capture = S.capture;
    if (capture && capture.analyser) {
      capture.analyser.getByteFrequencyData(capture.freq);
      var bins = capture.freq, n = bins.length, total = 0;
      for (var b = 0; b < 7; b += 1) {
        var from = Math.floor(Math.pow(b / 7, 1.6) * n * 0.7) + 1, to = Math.max(from + 1, Math.floor(Math.pow((b + 1) / 7, 1.6) * n * 0.7) + 1);
        var sum = 0;
        for (var i = from; i < to; i += 1) sum += bins[i];
        var value = Math.min(1, (sum / (to - from)) / 190);
        S.bands[b] = ease(S.bands[b], value, 0.45);
        total += value;
      }
      S.level = ease(S.level, Math.min(1, total / 4.2), 0.35);
    } else {
      S.level = ease(S.level, 0, 0.2);
    }
    // Speaker: what the robot is saying, for the mouth.
    if (S.out && S.speech && !S.speech.browser) {
      S.out.analyser.getFloatTimeDomainData(S.out.data);
      var energy = 0, data = S.out.data;
      for (var j = 0; j < data.length; j += 1) energy += data[j] * data[j];
      var rms = Math.sqrt(energy / data.length);
      // Follow the voice's own loudness, so a quiet voice still moves the mouth.
      S.outPeak = Math.max(rms, (S.outPeak || 0.05) * 0.995, 0.03);
      S.outLevel = ease(S.outLevel, Math.min(1, rms / S.outPeak), 0.55);
    } else if (S.speech && S.speech.browser) {
      S.outLevel = ease(S.outLevel, 0.25 + 0.2 * Math.sin(performance.now() / 70), 0.25);
    } else {
      S.outLevel = ease(S.outLevel, 0, 0.3);
    }
  }

  function frame(now) {
    requestAnimationFrame(frame);
    if (!prefs.robot || document.hidden || !bot.root) return;
    var dt = Math.min(0.25, (now - (anim.last || now)) / 1000);
    anim.last = now;
    frameFactor = Math.max(0.2, dt * 60);
    readLevels();
    var t = now / 1000, phase = S.phase, motion = reduceMotion ? 0.25 : 1;
    var greeting = now < S.greetUntil, erroring = phase === "error" || now < S.errorUntil;
    S.perk = Math.max(0, S.perk - dt * 2.2);

    // Gaze: follows a pointer when there is one, otherwise looks around.
    if (anim.pointer && now - anim.pointerAt < 2500) {
      anim.gazeTX = anim.pointer.x * 13; anim.gazeTY = anim.pointer.y * 9;
    } else if (phase === "thinking") {
      anim.gazeTX = 9 + Math.sin(t * 1.7) * 3; anim.gazeTY = -8;
    } else if (phase === "command" || phase === "speaking") {
      anim.gazeTX = Math.sin(t * 0.9) * 2; anim.gazeTY = 1;
    } else if (now > anim.nextSaccade) {
      anim.gazeTX = (Math.random() * 2 - 1) * 11; anim.gazeTY = (Math.random() * 2 - 1) * 6;
      anim.nextSaccade = now + 1200 + Math.random() * 2600;
    }
    anim.gazeX = ease(anim.gazeX, anim.gazeTX, 0.14);
    anim.gazeY = ease(anim.gazeY, anim.gazeTY, 0.14);

    // Blinks.
    if (!anim.nextBlink) anim.nextBlink = now + 2000;
    if (now > anim.nextBlink && anim.blinkStart < 0 && phase !== "off") { anim.blinkStart = now; anim.nextBlink = now + 2400 + Math.random() * 3800; }
    if (anim.blinkStart >= 0) {
      var p = (now - anim.blinkStart) / 150;
      anim.blink = p < 1 ? 1 - p * 0.9 : p < 2 ? 0.1 + (p - 1) * 0.9 : 1;
      if (p >= 2) anim.blinkStart = -1;
    }
    anim.sleep = ease(anim.sleep, phase === "off" ? 1 : 0, phase === "off" ? 0.04 : 0.2);
    var eyeOpen = Math.max(0.1, anim.blink * (1 - anim.sleep * 0.88));
    var eyeScale = 1 + (phase === "command" ? 0.12 + S.level * 0.12 : 0) + S.perk * 0.12;
    anim.happy = ease(anim.happy, (phase === "speaking" && Math.sin(t * 0.8) > 0.55) || greeting ? 1 : 0, 0.2);
    var sad = erroring ? 1 : 0;
    [bot.eyeL, bot.eyeR].forEach(function (eye, index) {
      if (!eye) return;
      var x = (index ? 245 : 155) + anim.gazeX, y = 168 + anim.gazeY + sad * 6;
      eye.setAttribute("transform", "translate(" + x.toFixed(2) + " " + y.toFixed(2) + ") scale(" + eyeScale.toFixed(3) + " " + (eyeScale * eyeOpen * (1 - sad * 0.35)).toFixed(3) + ")");
      setOpacity(eye.querySelector(".eye-ring"), Math.max(0, 1 - anim.happy * 2));
      setOpacity(eye.querySelector(".eye-happy"), Math.max(0, anim.happy * 2 - 1));
    });

    // Mouth, equaliser, thinking dots.
    anim.eqOn = ease(anim.eqOn, phase === "command" ? 1 : 0, 0.18);
    anim.thinkOn = ease(anim.thinkOn, phase === "thinking" ? 1 : 0, 0.15);
    anim.open = ease(anim.open, phase === "speaking" ? S.outLevel : 0, 0.5);
    var hideSmile = Math.max(anim.eqOn, anim.thinkOn, Math.min(1, anim.open * 3));
    setOpacity(bot.smile, (1 - hideSmile) * (1 - anim.sleep * 0.6));
    if (bot.smile) bot.smile.setAttribute("d", erroring ? "M184 220Q200 208 216 220" : "M182 212Q200 226 218 212");
    var o = anim.open, w = 16 + o * 8;
    if (bot.mouth) {
      bot.mouth.setAttribute("d", "M" + (200 - w) + " 212Q200 " + (214 - o * 6).toFixed(1) + " " + (200 + w) + " 212Q200 " + (216 + o * 28).toFixed(1) + " " + (200 - w) + " 212Z");
      setOpacity(bot.mouth, Math.min(1, o * 3) * (1 - anim.thinkOn));
    }
    setOpacity(bot.eq, anim.eqOn);
    for (var e = 0; e < eqBars.length; e += 1) {
      var h = 6 + (S.bands[e] * 34 + Math.sin(t * 9 + e) * 2.5 * S.level) * motion;
      eqBars[e].setAttribute("height", h.toFixed(1));
      eqBars[e].setAttribute("y", (214 - h / 2).toFixed(1));
    }
    setOpacity(bot.think, anim.thinkOn);
    for (var d = 0; d < thinkDots.length; d += 1) {
      var angle = t * 4.2 + d * (Math.PI * 2 / 3);
      thinkDots[d].setAttribute("cx", (200 + Math.cos(angle) * 18).toFixed(1));
      thinkDots[d].setAttribute("cy", (216 + Math.sin(angle) * 7).toFixed(1));
      thinkDots[d].setAttribute("r", (3.5 + (Math.sin(angle) + 1) * 1.6).toFixed(1));
    }

    // Glow: ears follow the microphone, the antenna and chest show attention.
    var attention = phase === "command" ? 1 : phase === "thinking" ? 0.6 + 0.4 * Math.sin(t * 6) : phase === "speaking" ? 0.4 + S.outLevel * 0.6 : phase === "waiting" ? 0.18 : 0.05;
    setOpacity(bot.antenna, Math.max(0.05, attention));
    setOpacity(bot.earL, 0.35 + S.level * 0.65 + S.perk * 0.3);
    setOpacity(bot.earR, 0.35 + S.level * 0.65 + S.perk * 0.3);
    setOpacity(bot.core, 0.35 + attention * 0.55 + Math.sin(t * 2) * 0.08);
    setOpacity(bot.zzz, anim.sleep > 0.5 ? (0.4 + 0.4 * Math.sin(t * 1.6)) * anim.sleep : 0);
    if (bot.zzz) bot.zzz.setAttribute("transform", "translate(0 " + (Math.sin(t * 1.2) * 4).toFixed(2) + ")");
    if (bot.visor) bot.visor.setAttribute("opacity", (1 - anim.sleep * 0.25).toFixed(3));

    // Body: breathing, a perk on the wake word, nods while it talks, a shake on errors.
    var breathe = Math.sin(t * 1.6) * 2.2 * motion * (1 - anim.sleep * 0.5);
    var nod = phase === "speaking" ? S.outLevel * 4 * motion : 0;
    var tiltTarget = phase === "thinking" ? 8 : phase === "command" ? -4 : anim.sleep * 6;
    anim.tilt = ease(anim.tilt, tiltTarget + Math.sin(t * 0.7) * 1.5 * motion, 0.06);
    var shake = erroring ? Math.sin(t * 30) * 3 * motion * Math.max(0, (S.errorUntil - now) / 2200) : 0;
    var lift = -S.perk * 10 * motion;
    if (bot.head) bot.head.setAttribute("transform", "translate(" + shake.toFixed(2) + " " + (breathe * 1.3 + nod + lift + anim.sleep * 8).toFixed(2) + ") rotate(" + (anim.tilt + shake).toFixed(2) + " 200 292)");
    if (bot.torso) bot.torso.setAttribute("transform", "translate(0 " + (breathe * 0.6 + lift * 0.5).toFixed(2) + ")");
    var waving = greeting ? -148 + Math.sin(t * 11) * 20 * motion : phase === "speaking" ? -14 - S.outLevel * 22 * motion : phase === "command" ? -18 : -6;
    anim.armR = ease(anim.armR, waving, greeting ? 0.25 : 0.08);
    anim.armL = ease(anim.armL, phase === "thinking" ? 26 : phase === "speaking" ? 10 + Math.sin(t * 2.4) * 5 * motion : 6 + breathe, 0.08);
    if (bot.armR) bot.armR.setAttribute("transform", "translate(0 " + (breathe * 0.6 + lift * 0.5).toFixed(2) + ") rotate(" + anim.armR.toFixed(2) + " 288 322)");
    if (bot.armL) bot.armL.setAttribute("transform", "translate(0 " + (breathe * 0.6 + lift * 0.5).toFixed(2) + ") rotate(" + anim.armL.toFixed(2) + " 112 322)");
    if (bot.shadow) bot.shadow.setAttribute("rx", (96 - lift * 0.8 - breathe).toFixed(1));
    document.documentElement.style.setProperty("--halo-scale", (0.7 + S.level * 0.25).toFixed(3));
  }

  // ---- Wiring -------------------------------------------------------------------
  function applyRobotPref() { ui.body.classList.toggle("no-robot", !prefs.robot); }
  function bindPrefs() {
    Array.prototype.forEach.call(document.querySelectorAll("[data-pref]"), function (input) {
      var key = input.getAttribute("data-pref");
      var value = key === "autoStart" ? (prefs.autoStart === null ? Boolean(S.status && S.status.auto_start) : prefs.autoStart) : prefs[key];
      if (input.type === "checkbox") input.checked = Boolean(value); else input.value = value;
      input.onchange = function () {
        prefs[key] = input.type === "checkbox" ? input.checked : input.value;
        save(key, prefs[key]);
        if (key === "robot") applyRobotPref();
        if (key === "speak" && !prefs.speak) stopSpeaking();
      };
    });
  }
  function toggleSettings(open) {
    ui.settings.hidden = !open;
    ui.settingsBtn.setAttribute("aria-expanded", open ? "true" : "false");
    if (open) { bindPrefs(); ui.settingsClose.focus(); } else { ui.settingsBtn.focus(); }
  }

  ui.robotButton.addEventListener("click", function () { outContext() && outContext().ctx.resume().catch(function () {}); talkNow(); });
  ui.mic.addEventListener("click", function () { if (S.capture) stop(); else start(); });
  ui.settingsBtn.addEventListener("click", function () { toggleSettings(ui.settings.hidden); });
  ui.settingsClose.addEventListener("click", function () { toggleSettings(false); });
  ui.unlockBtn.addEventListener("click", onUnlockTap);
  ui.signout.addEventListener("click", function () {
    request("api/auth/logout", {}).catch(function () {}).then(function () { location.reload(); });
  });
  ui.askForm.addEventListener("submit", function (event) {
    event.preventDefault();
    var text = ui.askInput.value.trim();
    if (!text) return;
    ui.askInput.value = "";
    outContext() && outContext().ctx.resume().catch(function () {});
    ask(text, "typed");
  });
  document.addEventListener("keydown", function (event) {
    var typing = event.target && /^(INPUT|TEXTAREA|SELECT)$/.test(event.target.tagName);
    if (event.key === "Escape") {
      if (!ui.settings.hidden) { toggleSettings(false); return; }
      if (S.phase === "speaking" || S.phase === "thinking") { S.turnTicket += 1; stopSpeaking(); resetToWaiting(); }
      return;
    }
    if (!typing && (event.code === "Space" || event.key === " ") && !event.repeat) {
      event.preventDefault();
      talkNow();
    }
  });
  window.addEventListener("pointermove", function (event) {
    var box = ui.robotButton.getBoundingClientRect();
    if (!box.width) return;
    var x = (event.clientX - (box.left + box.width / 2)) / (window.innerWidth / 2);
    var y = (event.clientY - (box.top + box.height * 0.32)) / (window.innerHeight / 2);
    anim.pointer = { x: Math.max(-1, Math.min(1, x)), y: Math.max(-1, Math.min(1, y)) };
    anim.pointerAt = performance.now();
  }, { passive: true });
  document.addEventListener("visibilitychange", function () {
    if (document.visibilityState === "visible") requestWakeLock();
  });
  window.addEventListener("pagehide", function () { closeCapture(); });

  // For kiosks and embedded devices: a hardware button or a kiosk script can
  // drive the assistant without the screen (for example a GPIO button that
  // runs AutoYouAssistant.talk() through the browser's automation port).
  window.AutoYouAssistant = {
    talk: talkNow,
    start: start,
    stop: stop,
    ask: function (text) { return ask(String(text || ""), "typed"); },
    get phase() { return S.phase; },
    get listening() { return Boolean(S.capture); }
  };

  // ---- Start --------------------------------------------------------------------
  applyRobotPref();
  requestAnimationFrame(frame);
  setPhase("off");
  refreshStatus(true).then(function (status) {
    var param = params.get("autostart");
    var auto = param === "1" ? true : param === "0" ? false : (prefs.autoStart === null ? Boolean(status.auto_start) : Boolean(prefs.autoStart));
    if (auto && status.enabled) {
      start();
    } else if (!status.enabled) {
      hint("Assistant mode is off on the server. Turn it on in the Admin page.", "warn");
    } else {
      hint("Tap the robot to start listening.");
      greet();
    }
    setInterval(function () { refreshStatus(false).catch(function () {}); }, 60000);
  }).catch(function (error) {
    if (error.status !== 401) { setPhase("error"); hint(error.message || "The assistant is not reachable.", "error"); }
  });
})();

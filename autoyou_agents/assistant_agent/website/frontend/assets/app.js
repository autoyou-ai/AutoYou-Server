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
    body: document.body, status: $("status-text"), hint: $("hint"),
    conversation: $("conversation"), turns: $("turns"), toLatest: $("to-latest"),
    mic: $("mic-btn"), micLabel: $("mic-btn-label"), robotButton: $("robot-button"), settings: $("settings"),
    settingsBtn: $("settings-btn"), settingsClose: $("settings-close"), facts: $("server-facts"),
    unlock: $("unlock"), unlockBtn: $("unlock-btn"), unlockTitle: $("unlock-title"), unlockSub: $("unlock-sub"),
    askForm: $("ask-form"), askInput: $("ask-input"), askSend: $("ask-send"), signout: $("signout-btn"),
    clear: $("clear-btn")
  };

  // The server draws its sign-in screen over this page. While it shows, this
  // page stays still (no requests, no microphone), and signing in reloads it.
  var AUTH = window.__AUTOYOU_AUTH__ || {};
  var signedOut = Boolean(AUTH.required && !AUTH.authenticated);

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
    greetUntil: 0, perk: 0, errorUntil: 0, wantListening: false, reopenTimer: 0, wakeLock: null,
    // "call" while this device is in a call: the call owns the microphone.
    pausedFor: "", pausedByServer: false, presenceTimer: 0, speakingReported: false,
    // One microphone at a time: a second start while the first opens waits for it.
    starting: null, hintTimer: 0, warmTimer: 0
  };

  // ---- Server calls -----------------------------------------------------------
  function request(path, body, asBinary) {
    var options = { method: body === undefined ? "GET" : "POST", credentials: "same-origin", headers: {} };
    if (body !== undefined) { options.headers["Content-Type"] = "application/json"; options.body = JSON.stringify(body); }
    return fetch("./" + path, options).then(function (response) {
      if (response.status === 401) signedOutNow();
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

  // The sign-in ended while the page was open (the code expired, or it was
  // signed out elsewhere). Show the sign-in screen once; never reload in a loop.
  function signedOutNow() {
    if (signedOut) return;
    signedOut = true;
    S.wantListening = false;
    S.turnTicket += 1;
    clearTimeout(S.presenceTimer); clearTimeout(S.reopenTimer); clearTimeout(S.warmTimer);
    closeCapture();
    stopSpeaking();
    setPhase("off");
    var last = 0;
    try { last = Number(sessionStorage.getItem(STORE + "authReload") || 0); } catch (e) { /* no storage */ }
    if (Date.now() - last > 60000) {
      try { sessionStorage.setItem(STORE + "authReload", String(Date.now())); } catch (e) { /* no storage */ }
      setTimeout(function () { location.reload(); }, 400);
    } else {
      hint("Signed out. Reload this page to sign in again.", "warn");
    }
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
      paused: S.pausedFor === "call" ? "Paused for the call" : "Paused",
      error: "Something went wrong"
    }[S.phase] || "";
    ui.status.textContent = text;
    var live = Boolean(S.capture);
    ui.mic.setAttribute("aria-pressed", live ? "true" : "false");
    ui.mic.title = live ? "Stop listening" : "Start listening";
    ui.micLabel.textContent = ui.mic.title;
    ui.robotButton.setAttribute("aria-label", text + ". " + (live ? "Tap to talk now." : "Tap to start listening."));
    renderAsk();
  }
  // The typed-question bar follows the voice: what it is doing, and a stop
  // button while it thinks or speaks.
  function busy() { return S.phase === "thinking" || S.phase === "speaking"; }
  function renderAsk() {
    var stop = busy() && !ui.askInput.value.trim();
    ui.askForm.classList.toggle("can-stop", stop);
    ui.askSend.setAttribute("aria-label", stop ? "Stop" : "Ask");
    ui.askSend.title = stop ? "Stop (Esc)" : "Ask";
    ui.askInput.placeholder = {
      command: S.followUp ? "Listening for more… or type" : "Listening… or type",
      thinking: "Thinking… type to ask something else",
      speaking: "Speaking… press stop, or type",
      paused: "Paused for the call. You can still type"
    }[S.phase] || "Or type a question…";
  }
  function hint(text, tone, seconds) {
    clearTimeout(S.hintTimer);
    ui.hint.textContent = text || "";
    ui.hint.className = "hint" + (tone ? " is-" + tone : "");
    // Passing notes clear themselves, so an old one never sits under a new answer.
    if (text && seconds) S.hintTimer = setTimeout(function () { if (ui.hint.textContent === text) hint(""); }, seconds * 1000);
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
  // Speech start and end on audio time, the server's rule. A follow-up needs a
  // little more speech before it counts, so a click or the room echoing the last
  // answer is not taken for a question.
  function endpointer(startSeconds) { return { noise: -60, speech: 0, silence: 0, started: false, voiced: 0, start: startSeconds || 0.06 }; }
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
      endpoint.voiced += seconds;
      if (endpoint.speech >= endpoint.start) endpoint.started = true;
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
      var capture = { ctx: ctx, stream: stream, source: source, node: node, analyser: analyser, freq: new Uint8Array(analyser.frequencyBinCount) };
      node.onaudioprocess = function (event) {
        // Only the microphone in use is heard. A second one (a double start)
        // would otherwise add every stretch of speech twice: "hey hey man man".
        if (S.capture !== capture) return;
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
        track.addEventListener("ended", function () { if (S.capture === capture) micLost(); });
      });
      return capture;
    });
  }
  function stopCapture(capture) {
    if (!capture) return;
    try { capture.node.onaudioprocess = null; capture.node.disconnect(); capture.source.disconnect(); } catch (e) { /* gone */ }
    capture.stream.getTracks().forEach(function (track) { track.stop(); });
    try { var closing = capture.ctx.close(); if (closing && closing.catch) closing.catch(function () {}); } catch (e) { /* closed */ }
  }
  function closeCapture() {
    var capture = S.capture;
    S.capture = null;
    stopCapture(capture);
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

  // ---- Telling the server what this page is doing ---------------------------
  // While this page listens, its device's other listeners stand down; while it
  // speaks an answer, no wake counts in the home; while its device is in a
  // call, the call owns the microphone and this page waits.
  function presence(extra) {
    var body = { listener_id: S.listenerId, listening: Boolean(S.capture) && !S.pausedFor };
    if (extra) Object.keys(extra).forEach(function (key) { body[key] = extra[key]; });
    return request("api/assistant/presence", body).then(function (data) {
      if (data.pause === "in_call" && !S.pausedFor && (S.capture || S.wantListening)) pauseFor("call", true);
      else if (!data.in_call && S.pausedFor === "call" && S.pausedByServer) resumeAfterPause();
      return data;
    }).catch(function () { return null; });
  }
  function schedulePresence() {
    clearTimeout(S.presenceTimer);
    if (!S.capture && !S.pausedFor) return;
    S.presenceTimer = setTimeout(function () { presence().then(schedulePresence); }, S.pausedFor ? 5000 : 15000);
  }
  function reportSpeaking(text) {
    // Roughly how long the rest of the answer takes to say, renewed per stretch.
    S.speakingReported = true;
    presence({ speaking: true, seconds: Math.min(60, 1 + String(text || "").length / 14) });
  }
  function reportSpeakingDone() {
    if (!S.speakingReported) return;
    S.speakingReported = false;
    presence({ speaking: false });
  }
  function pauseFor(reason, byServer) {
    if (S.pausedFor) { S.pausedByServer = S.pausedByServer && Boolean(byServer); return; }
    S.pausedFor = reason;
    S.pausedByServer = Boolean(byServer);
    S.turnTicket += 1;
    stopSpeaking();
    clearTimeout(S.reopenTimer);
    closeCapture();
    releaseWakeLock();
    setPhase("paused");
    hint(reason === "call" ? "Paused while this device is in a call. I'll listen again when it ends." : "Paused.");
    presence({ listening: false, speaking: false });
    schedulePresence();
  }
  function resumeAfterPause() {
    if (!S.pausedFor) return;
    S.pausedFor = "";
    S.pausedByServer = false;
    clearTimeout(S.presenceTimer);
    if (S.wantListening) { hint(""); start(); }
    else { setPhase("off"); hint("Tap the robot to start listening."); }
  }

  // ---- Listening ---------------------------------------------------------------
  function start() {
    S.wantListening = true;
    if (S.pausedFor) {
      setPhase("paused");
      hint("Paused while this device is in a call. I'll listen again when it ends.");
      return Promise.resolve();
    }
    if (S.capture) return Promise.resolve();
    // Already opening (a tap or the mic button during the permission prompt):
    // wait for that one rather than open a second microphone.
    if (S.starting) return S.starting;
    if (!S.status || !S.status.enabled) {
      setPhase("off");
      hint("Assistant mode is off on the server. Turn it on in the Admin page.", "warn");
      return Promise.resolve();
    }
    setPhase("starting");
    var opening = openCapture().then(function (capture) {
      if (!S.wantListening || S.pausedFor || S.capture) { stopCapture(capture); return; }
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
        if (S.pausedFor || !S.wantListening) { closeCapture(); return; }
        resetToWaiting();
        presence().then(schedulePresence);
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
    }).then(function () {
      if (S.starting === opening) S.starting = null;
    });
    S.starting = opening;
    return opening;
  }
  function stop() {
    S.wantListening = false;
    S.pausedFor = "";
    S.pausedByServer = false;
    clearTimeout(S.presenceTimer);
    closeCapture();
    stopSpeaking();
    S.turnTicket += 1;
    setPhase("off");
    hint("Tap the robot or the microphone to listen again.");
    releaseWakeLock();
    presence({ listening: false, speaking: false });
  }
  function resetToWaiting() {
    S.followUp = false;
    S.chunks = []; S.waited = 0; S.busy = false; S.wake = null; S.via = "";
    S.endpoint = endpointer();
    if (S.detector) S.detector.reset();
    setPhase(S.capture ? "waiting" : "off");
  }
  // Stop what it is saying or working on, and listen for the word again.
  function stopTurn() {
    S.turnTicket += 1;
    stopSpeaking();
    hint("Stopped.", "", 2);
    markReplyStopped();
    resetToWaiting();
  }
  function beginCommand(preRoll, via, alreadyAccepted, followUp, score) {
    S.via = via;
    S.followUp = Boolean(followUp);
    // The server answers after a moment: the device that heard the word best
    // in this home answers, and the others stay quiet.
    var claim = { listener_id: S.listenerId };
    if (typeof score === "number" && isFinite(score)) claim.score = score;
    S.wake = alreadyAccepted ? Promise.resolve(true) : request("api/assistant/wake", claim).then(function (data) {
      if (!data.accepted) {
        hint(data.message || "Another device in the room is answering.", "warn");
        if (data.pause === "in_call") pauseFor("call", true);
      }
      return Boolean(data.accepted);
    }).catch(function (error) { hint(error.message, "error"); return false; });
    S.chunks = preRoll ? [preRoll] : [];
    S.waited = 0;
    S.endpoint = endpointer(followUp ? 0.25 : 0.06);
    S.perk = 1;
    setPhase("command");
    if (!followUp) chime("wake");
  }

  function onChunk(chunk) {
    if (!S.capture) return;
    // Half duplex: what the speaker says is never taken as a question.
    if (S.phase === "thinking" || S.phase === "speaking" || S.phase === "starting" || S.phase === "off" || S.phase === "paused") return;
    var speech = observe(S.endpoint, chunk);
    if (S.phase === "waiting") {
      if (S.detector) {
        var hit = S.detector.feed(chunk);
        if (hit) beginCommand(S.status.confirm === "both" ? hit.preRoll : null, "voice", false, false, hit.score);
        return;
      }
      if (S.status.listening !== "text" || S.busy) return;
      // Typed text only: short bursts are checked for the word, then discarded.
      if (speech || S.endpoint.started) S.chunks.push(chunk); else S.chunks = [];
      var burst = join(S.chunks).length / SR;
      if (S.endpoint.started && (S.endpoint.silence >= 0.6 || burst >= 6)) {
        var audio = join(S.chunks);
        S.chunks = []; S.endpoint = endpointer(); S.busy = true;
        transcribe({
          audio_base64: wav(audio), require_phrase: true, listener_id: S.listenerId, spot_wake: true
        }, function () { return S.phase === "waiting" && Boolean(S.capture); }).then(function (result) {
          if (!result || S.phase !== "waiting") return;
          if (result.matched && result.wake_accepted === false) {
            hint(result.message || "Another device in the room is answering.", "warn");
            if (result.pause === "in_call") pauseFor("call", true);
            return;
          }
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
    var voiced = S.endpoint ? S.endpoint.voiced : 1;
    S.chunks = [];
    // Hardly any speech (a cough, a click, the room): nothing to ask. A follow-up
    // simply goes back to waiting; nobody has to hear "didn't catch that".
    if (voiced < 0.2) {
      if (!followUp) hint("Sorry, I didn't catch that.", "", 4);
      resetToWaiting();
      return;
    }
    setPhase("thinking");
    chime("done");
    var ticket = ++S.turnTicket;
    var current = function () { return ticket === S.turnTicket; };
    Promise.resolve(wake).then(function (accepted) {
      if (accepted === false) return null;
      if (S.status && S.status.speech_ready === false && current()) hint(WARMING_NOTE);
      var requirePhrase = S.status.confirm === "both" && via === "voice" && !followUp;
      return transcribe({ audio_base64: wav(audio), require_phrase: requirePhrase, listener_id: S.listenerId }, current);
    }).then(function (result) {
      if (ticket !== S.turnTicket) return;
      if (!result) { clearSlowNote(); resetToWaiting(); return; }
      var text = String(result.command || "").trim();
      if (!text) {
        clearSlowNote();
        if (!followUp) {
          hint(S.status.confirm === "both" && !result.matched && via === "voice" ? "That didn't sound like the wake word, so nothing was sent." : "Sorry, I didn't catch that.", "", 4);
        }
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
  // A quick result comes back with the request. A slow one is collected
  // afterwards, so no proxy gives up on a long wait: the first question after a
  // restart waits for speech recognition to load, the first answer after a
  // break waits for the local model to load, and a web search takes a while.
  var WARMING_NOTE = "Getting speech recognition ready. The first answer after a restart takes up to a minute.";
  var SLOW_NOTE = "Still working on it. The first answer after a break can take up to a minute.";
  function settle(data, wanted) {
    if (!data || !data.pending) return Promise.resolve(data);
    if (wanted()) {
      var warming = S.status && S.status.speech_ready === false;
      hint(warming ? WARMING_NOTE : SLOW_NOTE);
    }
    return collect(data.job_id || data.turn_id, wanted);
  }
  // Each check waits on the server for a few seconds, so this is not a fast poll.
  function collect(jobId, wanted) {
    if (!wanted()) return Promise.resolve(null);
    return request("api/assistant/result/" + encodeURIComponent(jobId)).then(function (data) {
      return settle(data, wanted);
    });
  }
  function transcribe(body, wanted) {
    return request("api/assistant/transcribe", body).then(function (data) { return settle(data, wanted); });
  }
  function clearSlowNote() {
    if (ui.hint.textContent === SLOW_NOTE || ui.hint.textContent === WARMING_NOTE) hint("");
  }
  function askServer(text, via, ticket) {
    var current = function () { return ticket === S.turnTicket; };
    return request("api/assistant/turn", { text: text, session_id: sessionId, wake: via || "" }).then(function (data) {
      return settle(data, current);
    });
  }
  function ask(text, via) {
    var ticket = ++S.turnTicket;
    stopSpeaking();
    markReplyStopped();
    addTurn("you", text);
    addTurn("bot", "");
    if (ui.hint.textContent !== SLOW_NOTE && ui.hint.textContent !== WARMING_NOTE) hint("");
    setPhase("thinking");
    return askServer(text, via, ticket).then(function (data) {
      if (!data || ticket !== S.turnTicket) return;
      clearSlowNote();
      var reply = plain(data.reply || "") || "Done.";
      return speakReply(reply, ticket).then(function () {
        if (ticket !== S.turnTicket) return;
        afterReply(ticket);
      });
    }).catch(function (error) {
      if (ticket !== S.turnTicket) return;
      clearSlowNote();
      dropEmptyReply();
      fail(error.message || "The assistant could not answer.");
    });
  }
  function afterReply(ticket) {
    if (!S.capture) { setPhase("off"); return; }
    if (!(S.status && S.status.follow_up)) { resetToWaiting(); return; }
    // A short window for the next question without the wake word. The mic
    // stays closed a moment longer, so the room's echo of the answer has died
    // away before it listens.
    setTimeout(function () {
      if (ticket === S.turnTicket && S.capture && (S.phase === "speaking" || S.phase === "thinking")) {
        beginCommand(null, "follow_up", true, true);
      }
    }, 650);
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
  // ---- The conversation on screen ---------------------------------------------
  // Questions and answers stay in a list that scrolls (wheel, touch or keys)
  // without a scrollbar. It is kept on this device only, for this page's
  // conversation, and can be cleared in Settings.
  var HISTORY_LIMIT = 60;
  var history = (function () {
    var saved = load("history", []);
    return Array.isArray(saved) ? saved.filter(function (item) {
      return item && (item.who === "you" || item.who === "bot") && typeof item.text === "string";
    }).slice(-HISTORY_LIMIT) : [];
  })();
  var replyEl = null;
  function saveHistory() { save("history", history.slice(-HISTORY_LIMIT)); }
  function nearBottom() {
    var box = ui.conversation;
    return !box || box.scrollHeight - box.scrollTop - box.clientHeight < 48;
  }
  function toLatest(smooth) {
    var box = ui.conversation;
    if (!box) return;
    box.scrollTo ? box.scrollTo({ top: box.scrollHeight, behavior: smooth ? "smooth" : "auto" }) : (box.scrollTop = box.scrollHeight);
    ui.toLatest.hidden = true;
  }
  function follow(wasNear) {
    // Keep the newest words in view, unless someone scrolled up to read.
    if (wasNear) toLatest(true); else ui.toLatest.hidden = false;
  }
  function turnElement(who, text) {
    var item = document.createElement("li");
    item.className = "turn " + who;
    var p = document.createElement("p");
    p.textContent = text;
    item.appendChild(p);
    return item;
  }
  function markEarlier() {
    Array.prototype.forEach.call(ui.turns.children, function (item, index, all) {
      item.classList.toggle("earlier", index < all.length - 2);
    });
  }
  function renderHistory() {
    ui.turns.textContent = "";
    history.forEach(function (item) { ui.turns.appendChild(turnElement(item.who, item.text)); });
    markEarlier();
    replyEl = null;
    toLatest(false);
  }
  function addTurn(who, text) {
    var wasNear = nearBottom();
    var item = turnElement(who, text);
    ui.turns.appendChild(item);
    while (ui.turns.children.length > HISTORY_LIMIT) ui.turns.removeChild(ui.turns.firstChild);
    if (who === "bot") replyEl = item.querySelector("p");
    else { history.push({ who: who, text: text, at: Date.now() }); saveHistory(); }
    markEarlier();
    follow(wasNear);
  }
  function dropEmptyReply() {
    if (replyEl && !replyEl.textContent) { var item = replyEl.parentNode; if (item) item.remove(); replyEl = null; markEarlier(); }
  }
  function keepReply() {
    if (!replyEl || replyEl.dataset.kept || !replyEl.textContent.trim()) return;
    replyEl.dataset.kept = "1";
    history.push({ who: "bot", text: replyEl.textContent.trim(), at: Date.now() });
    saveHistory();
  }
  function markReplyStopped() {
    if (!replyEl) return;
    if (!replyEl.textContent) { dropEmptyReply(); return; }
    // What was not said yet stays readable, just dimmer, and stays in the history.
    Array.prototype.forEach.call(replyEl.querySelectorAll(".saying"), function (span) { span.className = "said"; });
    keepReply();
  }
  function showReply(parts, index) {
    if (!replyEl) addTurn("bot", "");
    var wasNear = nearBottom();
    replyEl.textContent = "";
    parts.forEach(function (part, i) {
      var span = document.createElement("span");
      span.className = i < index ? "said" : (i === index ? "saying" : "unsaid");
      span.textContent = part + " ";
      replyEl.appendChild(span);
    });
    // The whole answer is out: keep it in the conversation.
    if (index >= parts.length) keepReply();
    follow(wasNear);
  }
  function clearHistory() {
    history = [];
    saveHistory();
    renderHistory();
    hint("Conversation cleared on this device.", "", 3);
  }

  function speakingEnabled() {
    // Never over a call on this device: the answer is shown instead.
    return !voiceOff && !S.pausedFor && prefs.speak && (!S.status || S.status.speak_replies !== false);
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
      if (ticket !== S.turnTicket) { reportSpeakingDone(); return Promise.resolve(); }
      if (i >= parts.length) { showReply(parts, parts.length); reportSpeakingDone(); return Promise.resolve(); }
      showReply(parts, i);
      // Other devices in the home ignore the wake word while this is said.
      reportSpeaking(parts.slice(i).join(" "));
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
    reportSpeakingDone();
  }

  // ---- Talk now: tap, Space, or the microphone button -------------------------
  function talkNow() {
    if (S.pausedFor) { hint("Paused while this device is in a call. Type a question instead, or try again after the call."); return; }
    if (!S.capture) { start(); return; }
    if (S.phase === "speaking") { S.turnTicket += 1; stopSpeaking(); markReplyStopped(); resetToWaiting(); beginCommand(null, "tap", true, false); return; }
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
      // Right after a restart speech recognition loads for up to a minute:
      // check again soon, so the page knows when it is ready.
      clearTimeout(S.warmTimer);
      if (status.speech_ready === false) {
        S.warmTimer = setTimeout(function () { refreshStatus(false).catch(function () {}); }, 3000);
      }
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
    core: $("core"), shadow: $("shadow"), visor: $("visor"), upper: $("upper"),
    foreL: $("forearm-left"), foreR: $("forearm-right")
  };
  // Joints: each follows its target like a damped spring (stiffness k, damping
  // ratio z), so a move speeds up, overshoots a touch and settles, and a part
  // carried by another lags behind it. Angles are in degrees, offsets in pixels.
  function joint(x, k, z) { return { x: x, v: 0, k: k, c: 2 * z * Math.sqrt(k) }; }
  var J = {
    lean: joint(0, 38, 0.72), bodyY: joint(0, 95, 0.55), squash: joint(0, 240, 0.3),
    headX: joint(0, 110, 0.62), headY: joint(0, 120, 0.5), tilt: joint(0, 55, 0.6),
    armL: joint(6, 80, 0.55), armR: joint(-6, 80, 0.55),
    foreL: joint(8, 150, 0.38), foreR: joint(-8, 150, 0.38)
  };
  function drive(j, target, dt) {
    var a = j.k * (target - j.x) - j.c * j.v;
    j.v += a * dt;
    j.x += j.v * dt;
  }
  function clamp(value, low, high) { return Math.max(low, Math.min(high, value)); }
  // A tap squashes it like a soft toy; a wake word or a pointer arriving makes it perk up.
  function boop() { J.squash.v -= 2.6; J.headY.v -= 40; }
  function notice() { if (S.phase !== "off" && S.phase !== "paused") { J.headY.v -= 25; J.tilt.v += (Math.random() - 0.5) * 30; } }
  var eqBars = bot.eq ? bot.eq.querySelectorAll("rect") : [];
  var thinkDots = bot.think ? bot.think.querySelectorAll("circle") : [];
  var reduceMotion = window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  var anim = {
    gazeX: 0, gazeY: 0, gazeTX: 0, gazeTY: 0, nextSaccade: 0, blink: 1, nextBlink: 0, blinkStart: -1,
    tilt: 0, armR: -6, armL: 6, happy: 0, open: 0, eqOn: 0, thinkOn: 0, sleep: 1, pointer: null, pointerAt: 0, last: 0
  };
  function greet() { if (S.phase === 'off' || S.phase === 'paused') return; S.greetUntil = performance.now() + 1900; S.perk = 1; }
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

    var resting = phase === "off" || phase === "paused";
    // Blinks (only while awake).
    if (!anim.nextBlink) anim.nextBlink = now + 2000;
    if (now > anim.nextBlink && anim.blinkStart < 0 && !resting) {
      anim.blinkStart = now; anim.nextBlink = now + 2400 + Math.random() * 3800;
    }
    if (anim.blinkStart >= 0) {
      var p = (now - anim.blinkStart) / 150;
      anim.blink = p < 1 ? 1 - p * 0.9 : p < 2 ? 0.1 + (p - 1) * 0.9 : 1;
      if (p >= 2) anim.blinkStart = -1;
    }
    anim.sleep = ease(anim.sleep, resting ? 1 : 0, resting ? 0.08 : 0.2);
    var eyeOpen = Math.max(0.1, anim.blink);
    var eyeScale = 1 + (phase === "command" ? 0.12 + S.level * 0.12 : 0) + S.perk * 0.12;
    anim.happy = ease(anim.happy, (phase === "speaking" && Math.sin(t * 0.8) > 0.55) || greeting ? 1 : 0, 0.2);
    var sad = erroring ? 1 : 0;
    var sleepOp = Math.max(0, Math.min(1, anim.sleep));
    var ringOp = (1 - sleepOp) * Math.max(0, 1 - anim.happy);
    var happyOp = (1 - sleepOp) * Math.max(0, Math.min(1, anim.happy));
    [bot.eyeL, bot.eyeR].forEach(function (eye, index) {
      if (!eye) return;
      var x = (index ? 245 : 155) + anim.gazeX, y = 168 + anim.gazeY + sad * 6;
      eye.setAttribute("transform", "translate(" + x.toFixed(2) + " " + y.toFixed(2) + ") scale(" + eyeScale.toFixed(3) + " " + (eyeScale * eyeOpen * (1 - sad * 0.35)).toFixed(3) + ")");
      setOpacity(eye.querySelector(".eye-ring"), ringOp);
      setOpacity(eye.querySelector(".eye-happy"), happyOp);
      setOpacity(eye.querySelector(".eye-sleep"), sleepOp);
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

    // Body. Targets come from what it is doing; the springs above turn them into
    // motion. Upper arms turn on the centre of the shoulder balls and forearms on
    // the elbow balls, both drawn over the joint, and every angle stays inside a
    // range where no part passes through another (the waving hand stays clear of
    // the ear, the arms never sweep into the chest).
    var m = motion, out = S.outLevel, sleep = anim.sleep;
    var breathe = Math.sin(t * 1.6) * 2.2 * m * (1 - sleep * 0.5);
    var pointer = anim.pointer && now - anim.pointerAt < 2500 ? anim.pointer : null;
    var px = pointer ? pointer.x : 0;
    var shake = erroring ? Math.sin(t * 30) * 3 * m * Math.max(0, (S.errorUntil - now) / 2200) : 0;
    var target = {
      lean: (Math.sin(t * 0.45) * 0.8 + px * 1.6 + (phase === "speaking" ? Math.sin(t * 1.3) * 1.2 * out : 0)) * m,
      bodyY: breathe * 0.6 - S.perk * 5 * m + sleep * 2,
      headX: shake + px * 3.5 * m,
      headY: breathe * 0.7 + (phase === "speaking" ? out * 4 * m : 0) - S.perk * 6 * m + sleep * 8,
      tilt: (phase === "thinking" ? 8 : phase === "command" ? -4 : sleep * 6) + Math.sin(t * 0.7) * 1.5 * m + px * 3 * m + shake,
      armL: 6 + breathe * 0.6, foreL: 8,
      armR: -6 - breathe * 0.6, foreR: -8
    };
    if (greeting) {
      // A wave beside the head: upper arm out, forearm up, the hand waving from the elbow.
      target.armR = -104 + Math.sin(t * 2.2) * 4 * m;
      target.foreR = -55 + Math.sin(t * 11) * 18 * m;
    } else if (phase === "speaking") {
      target.armR = -12 - out * 16 * m; target.foreR = -14 - out * 18 * m + Math.sin(t * 3.1) * 4 * m;
      target.armL = 9 + Math.sin(t * 2.4) * 4 * m; target.foreL = 10 + Math.sin(t * 2.9) * 5 * m;
    } else if (phase === "command") {
      target.armR = -16; target.foreR = -30;
    } else if (phase === "thinking") {
      target.armL = 22; target.foreL = 48 + Math.sin(t * 1.9) * 4 * m;
    } else if (erroring) {
      target.armL = 12; target.foreL = 6; target.armR = -12; target.foreR = -6;
    } else if (sleep > 0.5) {
      target.armL = 3; target.foreL = 2; target.armR = -3; target.foreR = -2;
    }
    // Inertia: a forearm trails its upper arm when that swings.
    target.foreL -= J.armL.v * 0.05;
    target.foreR -= J.armR.v * 0.05;
    target.armL = clamp(target.armL, -8, 140); target.armR = clamp(target.armR, -140, 8);
    target.foreL = clamp(target.foreL, -20, 75); target.foreR = clamp(target.foreR, -75, 20);
    // Fixed small steps keep the springs steady on a slow device.
    var steps = Math.max(1, Math.min(8, Math.ceil(dt / (1 / 120)))), h = dt / steps;
    for (var s = 0; s < steps; s += 1) {
      Object.keys(J).forEach(function (name) { drive(J[name], name === "squash" ? 0 : target[name], h); });
    }
    J.armL.x = clamp(J.armL.x, -10, 142); J.armR.x = clamp(J.armR.x, -142, 10);
    J.foreL.x = clamp(J.foreL.x, -22, 78); J.foreR.x = clamp(J.foreR.x, -78, 22);
    var sx = 1 - J.squash.x * 0.04, sy = 1 + J.squash.x * 0.06;
    if (bot.root) bot.root.setAttribute("transform", "translate(200 512) scale(" + sx.toFixed(4) + " " + sy.toFixed(4) + ") translate(-200 -512)");
    if (bot.upper) bot.upper.setAttribute("transform", "translate(0 " + J.bodyY.x.toFixed(2) + ") rotate(" + J.lean.x.toFixed(2) + " 200 448)");
    if (bot.head) bot.head.setAttribute("transform", "translate(" + J.headX.x.toFixed(2) + " " + J.headY.x.toFixed(2) + ") rotate(" + J.tilt.x.toFixed(2) + " 200 292)");
    if (bot.armL) bot.armL.setAttribute("transform", "rotate(" + J.armL.x.toFixed(2) + " 116 322)");
    if (bot.armR) bot.armR.setAttribute("transform", "rotate(" + J.armR.x.toFixed(2) + " 284 322)");
    if (bot.foreL) bot.foreL.setAttribute("transform", "rotate(" + J.foreL.x.toFixed(2) + " 116 388)");
    if (bot.foreR) bot.foreR.setAttribute("transform", "rotate(" + J.foreR.x.toFixed(2) + " 284 388)");
    if (bot.shadow) {
      bot.shadow.setAttribute("rx", (96 + J.bodyY.x * 0.8 - breathe + J.squash.x * 6).toFixed(1));
      bot.shadow.setAttribute("opacity", (0.28 - Math.min(0.08, Math.max(-0.08, J.bodyY.x * 0.01))).toFixed(3));
    }
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

  ui.robotButton.addEventListener("click", function () {
    outContext() && outContext().ctx.resume().catch(function () {});
    boop();
    talkNow();
  });
  ui.robotButton.addEventListener("pointerenter", function () { notice(); });
  ui.mic.addEventListener("click", function () { if (S.capture || S.starting) stop(); else start(); });
  ui.settingsBtn.addEventListener("click", function () { toggleSettings(ui.settings.hidden); });
  ui.settingsClose.addEventListener("click", function () { toggleSettings(false); });
  ui.unlockBtn.addEventListener("click", onUnlockTap);
  ui.signout.addEventListener("click", function () {
    request("api/auth/logout", {}).catch(function () {}).then(function () { location.reload(); });
  });
  ui.clear.addEventListener("click", clearHistory);
  ui.toLatest.addEventListener("click", function () { toLatest(true); });
  ui.conversation.addEventListener("scroll", function () { if (nearBottom()) ui.toLatest.hidden = true; }, { passive: true });
  ui.askInput.addEventListener("input", renderAsk);
  ui.askForm.addEventListener("submit", function (event) {
    event.preventDefault();
    var text = ui.askInput.value.trim();
    // While it thinks or speaks, the empty bar's button is a stop button.
    if (!text) { if (busy()) stopTurn(); return; }
    ui.askInput.value = "";
    outContext() && outContext().ctx.resume().catch(function () {});
    ask(text, "typed");
  });
  document.addEventListener("keydown", function (event) {
    var typing = event.target && /^(INPUT|TEXTAREA|SELECT)$/.test(event.target.tagName);
    if (event.key === "Escape") {
      if (!ui.settings.hidden) { toggleSettings(false); return; }
      if (busy()) stopTurn();
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
  window.addEventListener("pagehide", function () {
    closeCapture();
    // Best effort: the server also forgets a silent page after a short while.
    try {
      fetch("./api/assistant/presence", {
        method: "POST", credentials: "same-origin", keepalive: true,
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ listener_id: S.listenerId, listening: false, speaking: false })
      }).catch(function () {});
    } catch (e) { /* page is going away */ }
  });

  // For kiosks and embedded devices: a hardware button or a kiosk script can
  // drive the assistant without the screen (for example a GPIO button that
  // runs AutoYouAssistant.talk() through the browser's automation port).
  // The v2 apps call pause("call") when a call starts on the device and
  // resume() when it ends, so the call has the microphone to itself.
  window.AutoYouAssistant = {
    talk: talkNow,
    start: start,
    stop: stop,
    ask: function (text) { return ask(String(text || ""), "typed"); },
    pause: function (reason) { pauseFor(String(reason || "call"), false); },
    resume: function () {
      if (!S.pausedFor) return;
      // The app knows its own calls; the server is asked again only when it paused the page.
      if (S.pausedByServer) { presence(); return; }
      resumeAfterPause();
    },
    get paused() { return S.pausedFor; },
    get phase() { return S.phase; },
    get listening() { return Boolean(S.capture); }
  };

  // ---- Start --------------------------------------------------------------------
  applyRobotPref();
  setPhase("off");
  // Signed out: the server's sign-in screen covers the page. Nothing here may
  // run until it reloads the page after the code is accepted.
  if (signedOut) return;
  renderHistory();
  requestAnimationFrame(frame);
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

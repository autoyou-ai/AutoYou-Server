// Copyright (c) 2026 OpenStorey LLC. All rights reserved.
// Licensed under the AutoYou Source-Available License.
// See LICENSE in the project root for license information.

(function () {
    "use strict";

    async function openLivePairing() {
        if (document.getElementById("ayu-live-pair")) return;
        var dialog = document.createElement("dialog");
        dialog.id = "ayu-live-pair";
        dialog.setAttribute("aria-label", "Live connect another device");
        dialog.style.cssText = "width:min(960px,96vw);max-height:96vh;padding:24px;border:0;border-radius:20px;background:var(--ayu-surface,#fff);color:inherit";
        dialog.innerHTML = '<div style="display:flex;justify-content:space-between;align-items:center"><h2>Live connect</h2><button type="button" data-live="close">Done</button></div>'
            + '<p role="status" data-live-status>Preparing your computer…</p>'
            + '<button type="button" data-live="setup">Share computer setup</button>'
            + '<div data-live-split style="display:flex;gap:16px;height:50vh;margin:16px 0"><video data-live-camera autoplay muted playsinline style="width:50%;object-fit:contain;background:#111;border-radius:16px"></video>'
            + '<div data-live-code style="width:50%;display:flex;flex-direction:column;align-items:center;justify-content:center"><img alt="Live pairing code" style="max-width:100%;max-height:90%;image-rendering:pixelated;display:none"><span data-live-counter>Scan a request, or share computer setup.</span></div></div>'
            + '<label>Paste a pairing message or shared link<textarea data-live-paste rows="2" style="width:100%"></textarea></label>'
            + '<div style="display:flex;gap:12px;flex-wrap:wrap;margin-top:12px"><button type="button" data-live="paste">Use message</button><button type="button" data-live="camera">Start camera</button>'
            + '<button type="button" data-live="full">Full camera</button><button type="button" data-live="share">Share</button><button type="button" data-live="copy">Copy message</button></div>';
        document.body.appendChild(dialog);
        dialog.showModal();
        var status = dialog.querySelector("[data-live-status]"), video = dialog.querySelector("video"), image = dialog.querySelector("img");
        var frames = [], outgoing = "", invite, stream, interval, timer, polling = false, scanning = false, closed = false, sharingSetup = false;
        var assembly = { digest: "", count: 0, parts: new Map(), completed: "", started: 0 }, lastReply = "", lastReceived = "";
        function show(payload) {
            frames = payload.qr_frames || []; outgoing = payload.text || "";
            image.style.display = frames.length ? "block" : "none";
            if (frames.length) image.src = frames[0];
        }
        async function assemble(text) {
            if (new TextEncoder().encode(text).length > 65536) throw new Error("Pairing message is too large");
            if (text.indexOf("ayqr1:") !== 0) return text;
            var parts = text.split(":"), count = Number(parts[3]), index = Number(parts[2]);
            if (parts.length !== 5 || !/^[a-f0-9]{64}$/.test(parts[1]) || !Number.isInteger(count) || !Number.isInteger(index) || index < 0 || index >= count || count > 110 || parts[4].length > 800) throw new Error("Invalid QR frame");
            var bytes = Uint8Array.from(atob(parts[4]), function (c) { return c.charCodeAt(0); });
            if (!bytes.length || bytes.length > 600) throw new Error("Invalid QR frame");
            if (assembly.completed === parts[1]) return null;
            if (assembly.digest !== parts[1] || Date.now() - assembly.started > 120000) assembly = { digest: parts[1], count: count, parts: new Map(), completed: assembly.completed, started: Date.now() };
            if (assembly.count !== count) throw new Error("QR frame counts do not match");
            assembly.parts.set(index, bytes);
            status.textContent = "Reading code " + assembly.parts.size + " of " + count + " · hold steady.";
            if (assembly.parts.size !== count) return null;
            var size = Array.from(assembly.parts.values()).reduce(function (n, b) { return n + b.length; }, 0);
            if (size > 65536) throw new Error("Pairing message is too large");
            var data = new Uint8Array(size), offset = 0;
            for (var i = 0; i < count; i++) { data.set(assembly.parts.get(i), offset); offset += assembly.parts.get(i).length; }
            var hash = Array.from(new Uint8Array(await crypto.subtle.digest("SHA-256", data))).map(function (n) { return n.toString(16).padStart(2, "0"); }).join("");
            if (hash !== assembly.digest) throw new Error("QR frames do not match; scan again");
            assembly.completed = hash;
            return new TextDecoder("utf-8", { fatal: true }).decode(data);
        }
        async function receive(text) {
            if (text.indexOf("autoyou://pair/live#message=") === 0) {
                var encoded = text.split("#message=")[1].replace(/-/g, "+").replace(/_/g, "/");
                if (encoded.length > 131072) throw new Error("Pairing message is too large");
                text = new TextDecoder().decode(Uint8Array.from(atob(encoded), function (c) { return c.charCodeAt(0); }));
            }
            var message = await assemble(text);
            if (message === null || message === lastReceived) return;
            if (!invite) throw new Error("The computer is still preparing. Try again.");
            var request = message.charAt(0) === "/" ? { protocol: "autoyou-live-pair/1", kind: "request", invite_id: invite.invite_id, request_id: crypto.randomUUID(), message: message } : JSON.parse(message);
            var reply = await postJson("/api/live-pair", { action: "exchange", request: request, raw_response: message.charAt(0) === "/" });
            sharingSetup = false;
            lastReceived = message;
            lastReply = reply.result.request_id;
            show(reply);
            status.textContent = "Let the new device scan your updated code.";
        }
        async function scan() {
            if (closed || scanning || !stream || video.readyState < 2) return;
            scanning = true;
            try {
                var text = "";
                if (window.BarcodeDetector) {
                    var codes = await new BarcodeDetector({ formats: ["qr_code"] }).detect(video);
                    if (codes.length) text = codes[0].rawValue;
                } else {
                    var canvas = document.createElement("canvas");
                    canvas.width = 960; canvas.height = Math.round(video.videoHeight / video.videoWidth * 960);
                    canvas.getContext("2d").drawImage(video, 0, 0, canvas.width, canvas.height);
                    text = (await postJson("/api/live-pair/scan", { image: canvas.toDataURL("image/jpeg", 0.7) })).text;
                }
                if (text) await receive(text);
            } catch (error) { assembly.completed = ""; status.textContent = error.message; }
            finally { scanning = false; }
        }
        async function close() {
            if (closed) return;
            closed = true; clearInterval(interval); clearInterval(timer);
            if (stream) stream.getTracks().forEach(function (track) { track.stop(); });
            dialog.close(); dialog.remove();
            await postJson("/api/live-pair", { action: "cancel" }).catch(function () {});
            invite = null; frames = []; outgoing = "";
        }
        dialog.addEventListener("cancel", function (event) { event.preventDefault(); close(); });
        dialog.addEventListener("click", async function (event) {
            var target = event.target.closest("[data-live]");
            if (!target) return;
            try {
                switch (target.dataset.live) {
                case "close": await close(); break;
                case "setup":
                    if (!invite) throw new Error("The computer is still preparing. Try again.");
                    var setup = await postJson("/api/live-pair", { action: "refresh", invite_id: invite.invite_id });
                    invite = setup.result; sharingSetup = true; show(setup);
                    status.textContent = "Let the new device scan your setup, then scan its request."; break;
                case "paste": await receive(dialog.querySelector("[data-live-paste]").value.trim()); break;
                case "camera":
                    if (stream) { stream.getTracks().forEach(function (track) { track.stop(); }); stream = null; target.textContent = "Start camera"; }
                    else { stream = await navigator.mediaDevices.getUserMedia({ video: { facingMode: "environment" }, audio: false }); video.srcObject = stream; await video.play(); target.textContent = "Stop camera"; }
                    break;
                case "full":
                    var code = dialog.querySelector("[data-live-code]"); code.hidden = !code.hidden; code.style.display = code.hidden ? "none" : "flex"; video.style.width = code.hidden ? "100%" : "50%"; target.textContent = code.hidden ? "Split screen" : "Full camera"; break;
                case "copy": await navigator.clipboard.writeText(outgoing); status.textContent = "Message copied. Share it with the new device."; break;
                case "share":
                    if (!outgoing) throw new Error("Scan a request or share computer setup first.");
                    var url = "autoyou://pair/live#message=" + btoa(unescape(encodeURIComponent(outgoing))).replace(/\+/g, "-").replace(/\//g, "_").replace(/=/g, "");
                    if (navigator.share) await navigator.share({ title: "AutoYou live connect", text: url });
                    else { await navigator.clipboard.writeText(url); status.textContent = "Link copied. Share it with the new device."; }
                    break;
                }
            } catch (error) { assembly.completed = ""; status.textContent = error.message; }
        });
        try {
            var previous = await requestJson("/api/live-pair/status");
            lastReply = previous.result ? previous.result.request_id : "";
            invite = (await postJson("/api/live-pair", { action: "start" })).result;
            if (closed) { await postJson("/api/live-pair", { action: "cancel" }); return; }
            status.textContent = "Scan the new device's request, or share computer setup.";
            interval = setInterval(function () {
                if (frames.length) { var i = Math.floor(Date.now() / 650) % frames.length; image.src = frames[i]; dialog.querySelector("[data-live-counter]").textContent = "Hold steady · code " + (i + 1) + " of " + frames.length; }
                scan();
            }, 650);
            var refreshAt = Date.now();
            timer = setInterval(async function () {
                if (closed || polling || scanning) return;
                polling = true;
                try {
                    if (sharingSetup && Date.now() - refreshAt > 20000) { var refreshed = await postJson("/api/live-pair", { action: "refresh", invite_id: invite.invite_id }); invite = refreshed.result; show(refreshed); refreshAt = Date.now(); }
                    var incoming = await requestJson("/api/live-pair/status");
                    if (incoming.result && incoming.result.request_id !== lastReply) { lastReply = incoming.result.request_id; sharingSetup = false; show(incoming); status.textContent = "Incoming connection · let the new device scan this reply."; }
                } catch (error) { status.textContent = error.message; }
                finally { polling = false; }
            }, 2000);
        } catch (error) { status.textContent = error.message; }
    }

    var NAV = [
        { id: "overview", label: "Overview", icon: "home" },
        { id: "chat", label: "Chat & History", icon: "msg" },
        { id: "live", label: "Live View", icon: "eye" },
        { id: "interact", label: "Interact", icon: "agents" },
        { id: "setup", label: "Setup & Boot", icon: "bolt" },
        { id: "ai", label: "AI & Models", icon: "cpu" },
        { id: "agents", label: "Agents", icon: "agents" },
        { id: "page", label: "Websites & Browser", icon: "page" },
        { id: "messaging", label: "Messaging", icon: "msg" },
        { id: "video", label: "Video & Calls", icon: "video" },
        { id: "permissions", label: "Permissions", icon: "shield" },
        { id: "speech", label: "Speech", icon: "mic" },
        { id: "connectivity", label: "Connectivity", icon: "wifi" },
        { id: "security", label: "Security", icon: "shield" },
        { id: "guides", label: "Help & Guides", icon: "book" }
    ];

    var ICONS = {
        menu: "M3 6h18M3 12h18M3 18h18",
        close: "M18 6L6 18M6 6l12 12",
        more: "M5 12h.01M12 12h.01M19 12h.01",
        home: "M3 10.5L12 3l9 7.5V21a1 1 0 01-1 1H5a1 1 0 01-1-1V10.5z M9 22V12h6v10",
        eye: "M2 12s3.5-7 10-7 10 7 10 7-3.5 7-10 7S2 12 2 12z M12 15a3 3 0 100-6 3 3 0 000 6z",
        eyeOff: "M3 3l18 18M10.58 10.58A2 2 0 0012 14a2 2 0 001.42-.58M9.88 4.24A10.8 10.8 0 0112 4c6.5 0 10 8 10 8a18.5 18.5 0 01-3.14 4.28M6.61 6.61A18.6 18.6 0 002 12s3.5 8 10 8a10.8 10.8 0 005.39-1.39",
        cpu: "M9 3H5a2 2 0 00-2 2v4m6-6h10a2 2 0 012 2v4M9 3v18m0 0h10a2 2 0 002-2V9M9 21H5a2 2 0 01-2-2V9m0 0h18",
        agents: "M17 21v-2a4 4 0 00-4-4H5a4 4 0 00-4 4v2M9 7a4 4 0 100 8 4 4 0 000-8zM23 21v-2a4 4 0 00-3-3.87M16 3.13a4 4 0 010 7.75",
        page: "M3 5h18a1 1 0 011 1v12a1 1 0 01-1 1H3a1 1 0 01-1-1V6a1 1 0 011-1zM1 10h22",
        msg: "M21 15a2 2 0 01-2 2H7l-4 4V5a2 2 0 012-2h14a2 2 0 012 2z",
        video: "M23 7l-7 5 7 5V7zM1 5h15v14H1z",
        mic: "M12 1a3 3 0 00-3 3v8a3 3 0 006 0V4a3 3 0 00-3-3zM19 10v2a7 7 0 01-14 0v-2M12 19v4M8 23h8",
        wifi: "M5 12.55a11 11 0 0114.08 0M1.42 9a16 16 0 0121.16 0M8.53 16.11a6 6 0 016.95 0M12 20h.01",
        shield: "M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z",
        book: "M4 19.5A2.5 2.5 0 016.5 17H20M4 19.5A2.5 2.5 0 004 17V5a2 2 0 012-2h12a2 2 0 012 2v14H6.5A2.5 2.5 0 014 19.5z",
        play: "M5 3l14 9-14 9V3z",
        pause: "M6 4h4v16H6zM14 4h4v16h-4z",
        stop: "M18 18H6V6h12v12z",
        refresh: "M23 4v6h-6M1 20v-6h6M3.51 9a9 9 0 0114.85-3.36L23 10M1 14l4.64 4.36A9 9 0 0020.49 15",
        save: "M19 21H5a2 2 0 01-2-2V5a2 2 0 012-2h11l5 5v11a2 2 0 01-2 2zM17 21v-8H7v8M7 3v5h8",
        plus: "M12 5v14M5 12h14",
        trash: "M3 6h18M8 6V4h8v2M19 6v14a2 2 0 01-2 2H7a2 2 0 01-2-2V6",
        external: "M18 13v6a2 2 0 01-2 2H5a2 2 0 01-2-2V8a2 2 0 012-2h6M15 3h6v6M10 14L21 3",
        logout: "M9 21H5a2 2 0 01-2-2V5a2 2 0 012-2h4M16 17l5-5-5-5M21 12H9",
        power: "M18.36 6.64a9 9 0 11-12.73 0M12 2v10",
        cloud: "M18 10h-1.26A8 8 0 103 16.3",
        key: "M21 2l-2 2m-7.61 7.61a5.5 5.5 0 11-7.778 7.778 5.5 5.5 0 017.777-7.777zm0 0L15.5 7.5m0 0l3 3L22 7l-3-3m-3.5 3.5L19 4",
        search: "M21 21l-4.35-4.35M17 11A6 6 0 115 11a6 6 0 0112 0z",
        check: "M20 6L9 17l-5-5",
        info: "M12 22a10 10 0 100-20 10 10 0 000 20zM12 16v-4M12 8h.01",
        bolt: "M13 2L3 14h9l-1 8 10-12h-9l1-8z",
        copy: "M8 4H6a2 2 0 00-2 2v14a2 2 0 002 2h12a2 2 0 002-2V8l-4-4H8zM14 4v4h4",
        qr: "M4 4h6v6H4zM14 4h6v6h-6zM4 14h6v6H4zM15 15h2v2h-2zM18 14h2v6h-2zM14 18h2v2h-2z",
        minus: "M5 12h14",
        camera: "M23 19a2 2 0 01-2 2H3a2 2 0 01-2-2V8a2 2 0 012-2h4l2-3h6l2 3h4a2 2 0 012 2zM12 17a4 4 0 100-8 4 4 0 000 8z",
        arrowUp: "M12 19V5M5 12l7-7 7 7",
        arrowDown: "M12 5v14M19 12l-7 7-7-7",
        arrowLeft: "M19 12H5M12 19l-7-7 7-7",
        arrowRight: "M5 12h14M12 5l7 7-7 7",
        center: "M12 2a10 10 0 100 20 10 10 0 000-20zm0 6a4 4 0 100 8 4 4 0 000-8z",
        edit: "M12 20h9M16.5 3.5a2.12 2.12 0 013 3L7 19l-4 1 1-4L16.5 3.5z",
        download: "M21 15v4a2 2 0 01-2 2H5a2 2 0 01-2-2v-4M7 10l5 5 5-5M12 15V3",
        file: "M14 2H6a2 2 0 00-2 2v16a2 2 0 002 2h12a2 2 0 002-2V8zM14 2v6h6",
        wave: "M2 12h2M6 8v8M10 4v16M14 7v10M18 10v4M22 12h0"
    };

    var root = document.getElementById("autoyou-admin-root");
    var noticeTimer = null;
    var renderState = {
        passiveQueued: false,
        composing: false,
        pointerDown: false,
        pointerTimer: null
    };

    var interactTimer = null;
    var state = {
        screen: "overview",
        navOpen: false,
        bootstrap: null,
        forms: {},
        notice: null,
        modal: null,
        softwareUpdate: { payload: null, loading: false, checked: false },
        localPair: { payload: null, loading: false, error: "" },
        interact: { payload: null, selected: [], dirty: false, loading: false, saving: false, error: "" },
        mcpSetup: { generatedToken: "", configDownloaded: false },
        selectedAgentName: "",
        setup: {
            loading: false,
            payload: null,
            stale: true,
            activeStep: 0,
            linkedScreen: "",
            linkedStep: 0,
            answers: {},
            recipe: null
        },
        aiLibrary: {
            local: null,
            catalog: null,
            downloads: null,
            behavior: null,
            openclawStatus: null,
            hermesStatus: null,
            details: {},
            detailLoadingKey: "",
            selectedCatalogKey: "",
            pendingSelection: null,
            catalogLoading: false,
            localLoading: false,
            downloadsLoading: false,
            behaviorLoading: false,
            openclawLoading: false,
            hermesLoading: false,
            query: "",
            source: "ollama",
            catalogPage: 1,
            catalogHasMore: false,
            catalogError: ""
        },
        pendingActions: {},
        videoFileUpload: {
            fileName: "",
            fileSize: 0,
            status: "idle",
            progress: null,
            serverPath: "",
            error: ""
        },
        instructions: {
            loading: false,
            payload: null,
            mode: "sections",
            selectedVariable: ""
        },
        agentWorkbench: {
            loading: false,
            stale: true,
            payload: null,
            detail: null,
            activeTab: "builder",
            tests: {},
            aiRestartRequired: false,
            aiRestartMessage: ""
        },
        desktopAssets: {
            loading: false,
            payload: null,
            selectedAgent: "",
            formAgent: "",
            error: ""
        },
        operations: {
            loading: false,
            mediaDevicesLoading: false,
            stale: true,
            signalStatus: null,
            signalMessages: null,
            signalDetail: null,
            signalDeviceName: null,
            telegramSenders: null,
            telegramUserStatus: null,
            telegramUserMessages: null,
            whatsappStatus: null,
            cloudStatus: null,
            cloudDevices: null,
            datachannel: null,
            playback: null,
            playbackStatus: null,
            webrtcCapabilities: null,
            audioDevices: null,
            cameraDevices: null,
            monitors: null,
            queue: null,
            taskSummary: null,
            errors: {}
        },
        chat: {
            loaded: false,
            loading: false,
            sessions: [],
            viewer: null,
            search: "",
            filter: "all",
            selected: null,
            userId: "",
            sessionId: "",
            messages: [],
            files: [],
            training: [],
            trainingSummary: null,
            trainingLibrary: null,
            view: "thread",
            filesOpen: false,
            renaming: false,
            titleDraft: "",
            pendingTitle: "",
            composer: "",
            attachments: [],
            recording: null,
            call: {
                phase: "idle",
                status: "Voice pipeline is waiting for a call.",
                readiness: "unknown",
                muted: false,
                sessionId: "",
                pc: null,
                dataChannel: null,
                localStream: null,
                remoteStream: null,
                chunks: {}
            }
        },
        profileMenuOpen: false,
        pageLocalScan: { running: false, discovered: [], checked: false },
        securityPasswordVisible: false,
        speechLibrary: {
            loading: false,
            status: null,
            downloads: null
        },
        jailbreak: {
            loading: false,
            status: null,
            prompt: null
        },
        agentSecurity: {
            loading: false,
            profiles: [],
            lastEnrolment: null,
            verifyError: null,
            enrolmentVerified: false,
            available: true,
            error: null
        },
        guides: {
            selectedId: "",
            byId: {},
            loading: false,
            loadingId: ""
        }
    };

    var liveIntervals = {
        modelJobs: null,
        speechJobs: null,
        pairingModal: null,
        telegramSenders: null
    };

    var liveTimers = {
        totpCode: null
    };

    var bootSweepState = {
        mounted: false,
        running: false,
        timer: null,
        score: 0,
        bestScore: 0,
        startTime: 0,
        rampTick: 0,
        bestLoaded: false,
        highScores: []
    };

    async function requestJson(url, options) {
        var res = await fetch(url, options);
        var text = await res.text();
        var json = null;
        try { json = JSON.parse(text); } catch (e) {}
        if (!res.ok) {
            var msg = res.statusText;
            if (json) {
                msg = json.detail || json.message || json.error || msg;
            } else if (text) {
                msg = text;
            }
            throw new Error(msg);
        }
        return json;
    }

    async function postJson(url, payload) {
        return requestJson(url, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload)
        });
    }

    function isAdminFormControl(element) {
        if (!element || element.nodeType !== 1) {
            return false;
        }
        if (element.isContentEditable) {
            return true;
        }
        var tag = String(element.tagName || "").toLowerCase();
        if (tag === "textarea" || tag === "select") {
            return true;
        }
        if (tag !== "input") {
            return false;
        }
        var type = String(element.getAttribute("type") || "text").toLowerCase();
        return ["button", "file", "hidden", "image", "reset", "submit"].indexOf(type) === -1;
    }

    function activeAdminControl() {
        var element = document.activeElement;
        if (!root || !element || !root.contains(element) || !isAdminFormControl(element)) {
            return null;
        }
        return element;
    }

    function hasActiveAdminControl() {
        // A pressed pointer counts too: a background render between mousedown and
        // mouseup replaces the button under the cursor and the click is lost.
        return renderState.composing || renderState.pointerDown || Boolean(activeAdminControl());
    }

    function attrSelector(name, value) {
        return "[" + name + "=\"" + String(value || "").replace(/\\/g, "\\\\").replace(/"/g, "\\\"") + "\"]";
    }

    function captureControlSelection(element, snapshot) {
        if (typeof element.selectionStart !== "number" || typeof element.selectionEnd !== "number") {
            return;
        }
        try {
            snapshot.selectionStart = element.selectionStart;
            snapshot.selectionEnd = element.selectionEnd;
            snapshot.selectionDirection = element.selectionDirection || "none";
        } catch (error) {}
    }

    function captureActiveControl() {
        var element = activeAdminControl();
        if (!element) {
            return null;
        }
        syncBoundControl(element);
        var snapshot = {
            screen: state.screen,
            tag: String(element.tagName || "").toLowerCase(),
            type: String(element.getAttribute("type") || "").toLowerCase(),
            bind: element.getAttribute("data-bind") || "",
            virtualBind: element.getAttribute("data-virtual-bind") || "",
            action: element.getAttribute("data-action") || "",
            role: element.getAttribute("data-role") || "",
            id: element.id || "",
            name: element.getAttribute("name") || "",
            value: typeof element.value === "string" ? element.value : "",
            checked: typeof element.checked === "boolean" ? element.checked : null,
            scrollTop: typeof element.scrollTop === "number" ? element.scrollTop : 0,
            scrollLeft: typeof element.scrollLeft === "number" ? element.scrollLeft : 0
        };
        captureControlSelection(element, snapshot);
        return snapshot;
    }

    function controlMatchesSnapshot(element, snapshot) {
        if (!element || !snapshot || !isAdminFormControl(element)) {
            return false;
        }
        var tag = String(element.tagName || "").toLowerCase();
        if (snapshot.tag && tag !== snapshot.tag) {
            return false;
        }
        if (tag === "input") {
            var type = String(element.getAttribute("type") || "").toLowerCase();
            if (snapshot.type && type !== snapshot.type) {
                return false;
            }
        }
        if (snapshot.bind && element.getAttribute("data-bind") !== snapshot.bind) {
            return false;
        }
        if (snapshot.virtualBind && element.getAttribute("data-virtual-bind") !== snapshot.virtualBind) {
            return false;
        }
        if (snapshot.action && element.getAttribute("data-action") !== snapshot.action) {
            return false;
        }
        if (snapshot.role && element.getAttribute("data-role") !== snapshot.role) {
            return false;
        }
        return true;
    }

    function findControlForSnapshot(snapshot) {
        if (!root || !snapshot) {
            return null;
        }
        var selectors = [];
        if (snapshot.bind) {
            selectors.push(attrSelector("data-bind", snapshot.bind));
        }
        if (snapshot.virtualBind) {
            selectors.push(attrSelector("data-virtual-bind", snapshot.virtualBind));
        }
        if (snapshot.id) {
            selectors.push(attrSelector("id", snapshot.id));
        }
        if (snapshot.name) {
            selectors.push(attrSelector("name", snapshot.name));
        }
        if (snapshot.action) {
            selectors.push(attrSelector("data-action", snapshot.action));
        }
        if (snapshot.role) {
            selectors.push(attrSelector("data-role", snapshot.role));
        }
        for (var index = 0; index < selectors.length; index += 1) {
            var matches = root.querySelectorAll(selectors[index]);
            for (var matchIndex = 0; matchIndex < matches.length; matchIndex += 1) {
                if (controlMatchesSnapshot(matches[matchIndex], snapshot)) {
                    return matches[matchIndex];
                }
            }
        }
        return null;
    }

    function restoreActiveControl(snapshot) {
        if (!snapshot || snapshot.screen !== state.screen || !root) {
            return;
        }
        var element = findControlForSnapshot(snapshot);
        if (!element || element.disabled) {
            return;
        }
        var chatDraftChanged = snapshot.role === "chat-input" && state.chat.composer !== snapshot.value;
        if (!chatDraftChanged && typeof element.value === "string" && element.value !== snapshot.value) {
            element.value = snapshot.value;
            syncBoundControl(element);
        }
        if (typeof element.checked === "boolean" && snapshot.checked !== null && element.checked !== snapshot.checked) {
            element.checked = snapshot.checked;
            syncBoundControl(element);
        }
        try {
            element.focus({ preventScroll: true });
        } catch (error) {
            try { element.focus(); } catch (focusError) {}
        }
        if (typeof element.setSelectionRange === "function" && typeof snapshot.selectionStart === "number" && typeof snapshot.selectionEnd === "number") {
            try {
                element.setSelectionRange(snapshot.selectionStart, snapshot.selectionEnd, snapshot.selectionDirection || "none");
            } catch (error) {}
        }
        if (typeof element.scrollTop === "number") {
            element.scrollTop = snapshot.scrollTop || 0;
        }
        if (typeof element.scrollLeft === "number") {
            element.scrollLeft = snapshot.scrollLeft || 0;
        }
    }

    function flushPassiveRender() {
        if (!renderState.passiveQueued || hasActiveAdminControl()) {
            return;
        }
        renderApp({ force: true });
    }

    function flushPassiveRenderSoon() {
        window.setTimeout(flushPassiveRender, 0);
    }

    function adminAssetUrl(url) {
        var value = String(url || "").trim();
        if (value.indexOf("/assets/") === 0) {
            return value.slice(1);
        }
        return value;
    }

    function dataUrlToBlob(dataUrl) {
        var parts = String(dataUrl || "").split(",");
        var header = parts[0] || "";
        var payload = parts[1] || "";
        var mimeMatch = header.match(/^data:([^;]+);base64$/i);
        var mime = mimeMatch ? mimeMatch[1] : "application/octet-stream";
        var binary = window.atob(payload);
        var bytes = new Uint8Array(binary.length);
        for (var index = 0; index < binary.length; index += 1) {
            bytes[index] = binary.charCodeAt(index);
        }
        return new Blob([bytes], { type: mime });
    }

    function canvasToBlob(canvas, type, quality) {
        return new Promise(function (resolve, reject) {
            if (!canvas) {
                reject(new Error("Avatar canvas could not be created."));
                return;
            }
            if (canvas.toBlob) {
                canvas.toBlob(function (blob) {
                    if (blob) {
                        resolve(blob);
                        return;
                    }
                    try {
                        resolve(dataUrlToBlob(canvas.toDataURL(type, quality)));
                    } catch (error) {
                        reject(error);
                    }
                }, type, quality);
                return;
            }
            try {
                resolve(dataUrlToBlob(canvas.toDataURL(type, quality)));
            } catch (error) {
                reject(error);
            }
        });
    }

    function loadImageFromFile(file) {
        return new Promise(function (resolve, reject) {
            var objectUrl = URL.createObjectURL(file);
            var image = new Image();
            image.onload = function () {
                URL.revokeObjectURL(objectUrl);
                resolve(image);
            };
            image.onerror = function () {
                URL.revokeObjectURL(objectUrl);
                reject(new Error("Selected file could not be opened as an image."));
            };
            image.src = objectUrl;
        });
    }

    function avatarBlobExtension(type) {
        var normalizedType = String(type || "").toLowerCase();
        if (normalizedType === "image/png") {
            return "png";
        }
        if (normalizedType === "image/webp") {
            return "webp";
        }
        return "jpg";
    }

    function drawSquareAvatarCanvas(image, size) {
        var canvas = document.createElement("canvas");
        var context = canvas.getContext("2d");
        var width = Number(image.naturalWidth || image.width || size) || size;
        var height = Number(image.naturalHeight || image.height || size) || size;
        var sourceSize = Math.max(1, Math.min(width, height));
        var sourceX = Math.max(0, Math.floor((width - sourceSize) / 2));
        var sourceY = Math.max(0, Math.floor((height - sourceSize) / 2));
        canvas.width = size;
        canvas.height = size;
        if (!context) {
            throw new Error("Avatar image processing is unavailable in this browser.");
        }
        context.fillStyle = "#10192b";
        context.fillRect(0, 0, size, size);
        context.imageSmoothingEnabled = true;
        context.imageSmoothingQuality = "high";
        context.drawImage(image, sourceX, sourceY, sourceSize, sourceSize, 0, 0, size, size);
        return canvas;
    }

    async function optimizeProfileImage(file) {
        if (!file) {
            throw new Error("Choose an image file first.");
        }
        var isGif = String(file.type || "").toLowerCase() === "image/gif" || /\.gif$/i.test(file.name || "");
        if (String(file.type || "").indexOf("image/") !== 0 && !isGif) {
            throw new Error("Profile image must be an image file.");
        }
        if (isGif) {
            if (file.size > 2 * 1024 * 1024) {
                throw new Error("Animated profile images must be smaller than 2 MB.");
            }
            return { blob: file, filename: file.name || "profile-avatar.gif" };
        }
        var image = await loadImageFromFile(file);
        var attempts = [
            { size: 96, type: "image/webp", quality: 0.78 },
            { size: 80, type: "image/webp", quality: 0.72 },
            { size: 96, type: "image/jpeg", quality: 0.72 },
            { size: 72, type: "image/jpeg", quality: 0.64 }
        ];
        var best = null;
        for (var index = 0; index < attempts.length; index += 1) {
            var attempt = attempts[index];
            var blob = await canvasToBlob(drawSquareAvatarCanvas(image, attempt.size), attempt.type, attempt.quality);
            if (!blob || !blob.size) {
                continue;
            }
            var normalizedType = String(blob.type || attempt.type || "image/jpeg");
            var candidate = {
                blob: blob,
                filename: "profile-avatar." + avatarBlobExtension(normalizedType)
            };
            if (!best || blob.size < best.blob.size) {
                best = candidate;
            }
            if (blob.size <= 18 * 1024) {
                return candidate;
            }
        }
        if (!best) {
            throw new Error("Profile image could not be optimized.");
        }
        return best;
    }

    async function uploadProfileImage(file) {
        var optimized = await optimizeProfileImage(file);
        var form = new FormData();
        form.append("image", optimized.blob, optimized.filename);
        var response = await requestJson("/api/admin/profile-image", {
            method: "POST",
            body: form
        });
        applyBootstrap(response.bootstrap ? response.bootstrap : response);
        state.profileMenuOpen = false;
        setNotice("success", "Profile image updated.");
        return response;
    }

    async function openProfileCropper(file) {
        if (!file) return;
        if (String(file.type || "").indexOf("image/") !== 0 && !/\.(jpe?g|png|webp|gif|bmp|avif)$/i.test(file.name || "")) {
            setNotice("error", "Please select an image file.");
            return;
        }
        try {
            var img = await loadImageFromFile(file);
            var naturalW = Number(img.naturalWidth || img.width || 300) || 300;
            var naturalH = Number(img.naturalHeight || img.height || 300) || 300;
            var cropSize = 240;
            var baseScale = Math.max(cropSize / naturalW, cropSize / naturalH);
            state.profileCropper = {
                file: file,
                image: img,
                naturalWidth: naturalW,
                naturalHeight: naturalH,
                cropSize: cropSize,
                viewportSize: 320,
                baseScale: baseScale,
                zoom: 1.0,
                minZoom: 0.5,
                maxZoom: 5.0,
                offsetX: 0,
                offsetY: 0,
                isDragging: false,
                dragStartX: 0,
                dragStartY: 0,
                startOffsetX: 0,
                startOffsetY: 0,
                saving: false
            };
            state.profileMenuOpen = false;
            renderApp();
        } catch (err) {
            setNotice("error", err.message || "Failed to load image.");
        }
    }

    function renderProfileCropperModal() {
        var c = state.profileCropper;
        if (!c) return "";
        var zoomPct = Math.round(c.zoom * 100) + "%";
        var saveLabel = c.saving ? "Saving..." : "Save Profile Picture";
        var saveDisabled = c.saving ? " disabled aria-busy=\"true\"" : "";

        return "<div class=\"ayu-modal\" role=\"presentation\">" +
            "<button type=\"button\" class=\"ayu-modal-dismiss\" data-action=\"cropper-cancel\" aria-label=\"Cancel\"></button>" +
            "<div class=\"ayu-modal-card ayu-cropper-card\" role=\"dialog\" aria-modal=\"true\" aria-label=\"Edit Profile Picture\">" +
                "<div class=\"ayu-modal-head\">" +
                    "<div>" +
                        "<h2>Edit Profile Picture</h2>" +
                        "<p>Drag to reposition, zoom in/out, and center to crop your circular profile photo.</p>" +
                    "</div>" +
                    button("Close", "cropper-cancel", "secondary", "close", "sm") +
                "</div>" +
                "<div class=\"ayu-cropper-body\">" +
                    "<div class=\"ayu-cropper-workspace\">" +
                        "<div class=\"ayu-cropper-canvas-wrap\">" +
                            "<canvas class=\"ayu-cropper-canvas\" width=\"" + c.viewportSize + "\" height=\"" + c.viewportSize + "\"></canvas>" +
                            "<div class=\"ayu-cropper-guide-hint\">Drag to reposition &bull; Scroll to zoom</div>" +
                        "</div>" +
                        "<div class=\"ayu-cropper-side\">" +
                            "<div class=\"ayu-cropper-preview-card\">" +
                                "<div class=\"ayu-hint\">Live Preview</div>" +
                                "<div class=\"ayu-cropper-preview-avatar\">" +
                                    "<canvas class=\"ayu-cropper-preview-canvas\" width=\"80\" height=\"80\"></canvas>" +
                                "</div>" +
                            "</div>" +
                            "<div class=\"ayu-cropper-nudge-pad\">" +
                                "<div class=\"ayu-hint\">Nudge</div>" +
                                "<div class=\"ayu-cropper-dpad\">" +
                                    button("", "cropper-nudge-up", "secondary", "arrowUp", "sm", "title=\"Move Up\" aria-label=\"Move Up\"") +
                                    "<div class=\"ayu-cropper-dpad-row\">" +
                                        button("", "cropper-nudge-left", "secondary", "arrowLeft", "sm", "title=\"Move Left\" aria-label=\"Move Left\"") +
                                        button("Center", "cropper-center", "ghost", "center", "sm", "title=\"Reset Center\" aria-label=\"Reset Center\"") +
                                        button("", "cropper-nudge-right", "secondary", "arrowRight", "sm", "title=\"Move Right\" aria-label=\"Move Right\"") +
                                    "</div>" +
                                    button("", "cropper-nudge-down", "secondary", "arrowDown", "sm", "title=\"Move Down\" aria-label=\"Move Down\"") +
                                "</div>" +
                            "</div>" +
                        "</div>" +
                    "</div>" +
                    "<div class=\"ayu-cropper-toolbar\">" +
                        "<div class=\"ayu-cropper-zoom-controls\">" +
                            button("", "cropper-zoom-out", "secondary", "minus", "sm", "title=\"Zoom Out\" aria-label=\"Zoom Out\"") +
                            "<input type=\"range\" class=\"ayu-cropper-zoom-slider\" min=\"0.5\" max=\"5.0\" step=\"0.05\" value=\"" + c.zoom + "\" data-action=\"cropper-zoom-slider\" aria-label=\"Zoom\">" +
                            button("", "cropper-zoom-in", "secondary", "plus", "sm", "title=\"Zoom In\" aria-label=\"Zoom In\"") +
                            "<span class=\"ayu-cropper-zoom-badge\">" + zoomPct + "</span>" +
                            button("Fit", "cropper-reset", "ghost", "refresh", "sm", "title=\"Fit / Reset\" aria-label=\"Fit / Reset\"") +
                        "</div>" +
                    "</div>" +
                "</div>" +
                "<div class=\"ayu-cropper-footer\">" +
                    button("Choose another file", "cropper-choose-other", "ghost", "plus", "sm") +
                    "<div class=\"ayu-inline-actions\">" +
                        button("Cancel", "cropper-cancel", "secondary", "close", "sm") +
                        "<button type=\"button\" class=\"ayu-btn ayu-btn-primary ayu-btn-sm\" data-action=\"cropper-save\"" + saveDisabled + ">" +
                            (c.saving ? "<span class=\"ayu-btn-spinner\"></span>" : icon("check")) +
                            "<span>" + escapeHtml(saveLabel) + "</span>" +
                        "</button>" +
                    "</div>" +
                "</div>" +
            "</div>" +
        "</div>";
    }

    function drawCropperCanvas() {
        var c = state.profileCropper;
        if (!c || !c.image) return;
        var rootEl = root || document;
        var canvas = rootEl.querySelector(".ayu-cropper-canvas");
        var previewCanvas = rootEl.querySelector(".ayu-cropper-preview-canvas");
        if (!canvas) return;

        var ctx = canvas.getContext("2d");
        if (!ctx) return;

        var W = c.viewportSize;
        var H = c.viewportSize;
        var R = c.cropSize / 2;
        var cx = W / 2;
        var cy = H / 2;

        var scale = c.baseScale * c.zoom;
        var drawW = c.naturalWidth * scale;
        var drawH = c.naturalHeight * scale;
        var drawX = cx + c.offsetX - drawW / 2;
        var drawY = cy + c.offsetY - drawH / 2;

        ctx.clearRect(0, 0, W, H);
        ctx.fillStyle = "#090d16";
        ctx.fillRect(0, 0, W, H);

        ctx.imageSmoothingEnabled = true;
        ctx.imageSmoothingQuality = "high";
        ctx.drawImage(c.image, drawX, drawY, drawW, drawH);

        ctx.save();
        ctx.fillStyle = "rgba(7, 11, 20, 0.72)";
        ctx.beginPath();
        ctx.rect(0, 0, W, H);
        ctx.arc(cx, cy, R, 0, Math.PI * 2, true);
        ctx.fill();

        ctx.beginPath();
        ctx.arc(cx, cy, R, 0, Math.PI * 2);
        ctx.strokeStyle = "rgba(255, 255, 255, 0.9)";
        ctx.lineWidth = 2.5;
        ctx.setLineDash([6, 6]);
        ctx.stroke();

        ctx.beginPath();
        ctx.moveTo(cx - 8, cy);
        ctx.lineTo(cx + 8, cy);
        ctx.moveTo(cx, cy - 8);
        ctx.lineTo(cx, cy + 8);
        ctx.strokeStyle = "rgba(255, 255, 255, 0.4)";
        ctx.lineWidth = 1.5;
        ctx.setLineDash([]);
        ctx.stroke();
        ctx.restore();

        if (previewCanvas) {
            var pctx = previewCanvas.getContext("2d");
            if (pctx) {
                var pSize = previewCanvas.width;
                pctx.clearRect(0, 0, pSize, pSize);
                pctx.save();
                pctx.fillStyle = "#10192b";
                pctx.fillRect(0, 0, pSize, pSize);
                pctx.beginPath();
                pctx.arc(pSize / 2, pSize / 2, pSize / 2, 0, Math.PI * 2);
                pctx.clip();

                var pScale = pSize / (2 * R);
                pctx.imageSmoothingEnabled = true;
                pctx.imageSmoothingQuality = "high";
                pctx.drawImage(
                    c.image,
                    (drawX - (cx - R)) * pScale,
                    (drawY - (cy - R)) * pScale,
                    drawW * pScale,
                    drawH * pScale
                );
                pctx.restore();
            }
        }
    }

    function updateCropperZoomUi() {
        var c = state.profileCropper;
        if (!c) return;
        var rootEl = root || document;
        var slider = rootEl.querySelector(".ayu-cropper-zoom-slider");
        if (slider) slider.value = String(c.zoom);
        var badge = rootEl.querySelector(".ayu-cropper-zoom-badge");
        if (badge) badge.textContent = Math.round(c.zoom * 100) + "%";
    }

    function syncCropper() {
        var c = state.profileCropper;
        if (!c) return;
        var rootEl = root || document;
        var canvas = rootEl.querySelector(".ayu-cropper-canvas");
        if (!canvas) return;

        drawCropperCanvas();

        if (canvas._cropperBound) return;
        canvas._cropperBound = true;

        canvas.addEventListener("pointerdown", function (event) {
            if (!state.profileCropper) return;
            var cropper = state.profileCropper;
            try { canvas.setPointerCapture(event.pointerId); } catch (e) {}
            cropper.isDragging = true;
            cropper.dragStartX = event.clientX;
            cropper.dragStartY = event.clientY;
            cropper.startOffsetX = cropper.offsetX;
            cropper.startOffsetY = cropper.offsetY;
            canvas.style.cursor = "grabbing";
        });

        canvas.addEventListener("pointermove", function (event) {
            if (!state.profileCropper) return;
            var cropper = state.profileCropper;
            if (!cropper.isDragging) return;
            var dx = event.clientX - cropper.dragStartX;
            var dy = event.clientY - cropper.dragStartY;
            cropper.offsetX = cropper.startOffsetX + dx;
            cropper.offsetY = cropper.startOffsetY + dy;
            drawCropperCanvas();
        });

        var stopDrag = function (event) {
            if (!state.profileCropper) return;
            var cropper = state.profileCropper;
            cropper.isDragging = false;
            canvas.style.cursor = "grab";
            try { canvas.releasePointerCapture(event.pointerId); } catch (e) {}
        };
        canvas.addEventListener("pointerup", stopDrag);
        canvas.addEventListener("pointercancel", stopDrag);

        canvas.addEventListener("wheel", function (event) {
            if (!state.profileCropper) return;
            event.preventDefault();
            var cropper = state.profileCropper;
            var factor = event.deltaY < 0 ? 1.08 : 0.92;
            cropper.zoom = Math.max(cropper.minZoom, Math.min(cropper.maxZoom, cropper.zoom * factor));
            updateCropperZoomUi();
            drawCropperCanvas();
        }, { passive: false });
    }

    async function saveCroppedProfileImage() {
        var c = state.profileCropper;
        if (!c || c.saving) return;
        c.saving = true;
        renderApp();

        try {
            var scale = c.baseScale * c.zoom;
            var R = c.cropSize / 2;
            var cx = c.viewportSize / 2;
            var cy = c.viewportSize / 2;

            var drawW = c.naturalWidth * scale;
            var drawH = c.naturalHeight * scale;
            var drawX = cx + c.offsetX - drawW / 2;
            var drawY = cy + c.offsetY - drawH / 2;

            var srcX = ((cx - R) - drawX) / scale;
            var srcY = ((cy - R) - drawY) / scale;
            var srcSize = (2 * R) / scale;

            var targetSize = 256;
            var outCanvas = document.createElement("canvas");
            outCanvas.width = targetSize;
            outCanvas.height = targetSize;
            var outCtx = outCanvas.getContext("2d");
            if (!outCtx) throw new Error("Canvas context unavailable.");

            outCtx.fillStyle = "#10192b";
            outCtx.fillRect(0, 0, targetSize, targetSize);
            outCtx.imageSmoothingEnabled = true;
            outCtx.imageSmoothingQuality = "high";
            outCtx.drawImage(c.image, srcX, srcY, srcSize, srcSize, 0, 0, targetSize, targetSize);

            var formats = [
                { type: "image/webp", quality: 0.84 },
                { type: "image/webp", quality: 0.74 },
                { type: "image/jpeg", quality: 0.78 },
                { type: "image/jpeg", quality: 0.65 }
            ];
            var bestBlob = null;
            for (var i = 0; i < formats.length; i++) {
                var blob = await canvasToBlob(outCanvas, formats[i].type, formats[i].quality);
                if (blob && blob.size) {
                    if (!bestBlob || blob.size < bestBlob.size) {
                        bestBlob = blob;
                    }
                    if (blob.size <= 56 * 1024) {
                        bestBlob = blob;
                        break;
                    }
                }
            }
            if (!bestBlob) throw new Error("Failed to encode cropped profile image.");

            var ext = avatarBlobExtension(bestBlob.type);
            var form = new FormData();
            form.append("image", bestBlob, "profile-avatar." + ext);

            var response = await requestJson("/api/admin/profile-image", {
                method: "POST",
                body: form
            });

            state.profileCropper = null;
            applyBootstrap(response.bootstrap ? response.bootstrap : response);
            setNotice("success", "Profile picture updated successfully.");
        } catch (err) {
            if (c) c.saving = false;
            renderApp();
            setNotice("error", err.message || "Failed to save profile picture.");
        }
    }

    function uploadVideoFileWithProgress(form, onProgress) {
        return new Promise(function (resolve, reject) {
            var xhr = new XMLHttpRequest();
            xhr.open("POST", "/api/webrtc/video-file/upload", true);
            xhr.upload.onprogress = function (event) {
                if (!event.lengthComputable || typeof onProgress !== "function") {
                    return;
                }
                onProgress(Math.max(0, Math.min(100, (event.loaded / event.total) * 100)));
            };
            xhr.onload = function () {
                var text = xhr.responseText || "";
                var json = null;
                try { json = text ? JSON.parse(text) : null; } catch (e) {}
                if (xhr.status >= 200 && xhr.status < 300) {
                    resolve(json || {});
                    return;
                }
                var msg = xhr.statusText || "Upload failed";
                if (json) {
                    msg = json.detail || json.message || json.error || msg;
                } else if (text) {
                    msg = text;
                }
                reject(new Error(msg));
            };
            xhr.onerror = function () {
                reject(new Error("Video upload failed before the server responded."));
            };
            xhr.onabort = function () {
                reject(new Error("Video upload was cancelled."));
            };
            xhr.send(form);
        });
    }

    async function uploadVideoFile(file) {
        if (!file) {
            throw new Error("Choose a video file first.");
        }
        var type = String(file.type || "").toLowerCase();
        if (type && type.indexOf("video/") !== 0 && type !== "application/octet-stream") {
            throw new Error("Selected file must be a video file.");
        }
        var uploadName = file.name || "video-file";
        var uploadSize = Number(file.size || 0);
        state.videoFileUpload = {
            fileName: uploadName,
            fileSize: uploadSize,
            status: "uploading",
            progress: 0,
            serverPath: "",
            error: ""
        };
        renderApp();
        var form = new FormData();
        form.append("video", file, uploadName || "video-file.mp4");
        try {
            var response = await uploadVideoFileWithProgress(form, function (progress) {
                state.videoFileUpload = Object.assign({}, state.videoFileUpload, {
                    status: "uploading",
                    progress: progress,
                    error: ""
                });
                renderApp();
            });
            setByPath(state.forms, "videoCall.outbound_source", "video_file");
            setByPath(state.forms, "videoCall.outbound_video_file", true);
            setByPath(state.forms, "videoCall.video_file.path", response.file_path || "");
            state.videoFileUpload = Object.assign({}, state.videoFileUpload, {
                status: "uploaded",
                progress: 100,
                serverPath: response.file_path || "",
                error: ""
            });
            renderApp();
            await patchConfig({ video_call: videoCallFormPayload() }, "Video file uploaded.");
            await ensureOperationsData(true);
            return response;
        } catch (error) {
            state.videoFileUpload = Object.assign({}, state.videoFileUpload, {
                status: "failed",
                error: error.message || String(error)
            });
            renderApp();
            throw error;
        }
    }

    function escapeHtml(value) {
        return String(value == null ? "" : value)
            .replace(/&/g, "&amp;")
            .replace(/</g, "&lt;")
            .replace(/>/g, "&gt;")
            .replace(/\"/g, "&quot;")
            .replace(/'/g, "&#39;");
    }

    async function copyText(value) {
        var text = String(value == null ? "" : value);
        if (navigator.clipboard && navigator.clipboard.writeText) {
            try {
                await navigator.clipboard.writeText(text);
                return;
            } catch (error) {
                if (!document.queryCommandSupported || !document.queryCommandSupported("copy")) {
                    throw error;
                }
            }
        }
        var textarea = document.createElement("textarea");
        textarea.value = text;
        textarea.setAttribute("readonly", "");
        textarea.style.position = "fixed";
        textarea.style.left = "-9999px";
        textarea.style.top = "0";
        document.body.appendChild(textarea);
        textarea.focus();
        textarea.select();
        try {
            if (!document.execCommand || !document.execCommand("copy")) {
                throw new Error("Clipboard access is unavailable in this browser.");
            }
        } finally {
            document.body.removeChild(textarea);
        }
    }

    function generateMcpSecret(prefix) {
        if (!window.crypto || typeof window.crypto.getRandomValues !== "function" || typeof window.btoa !== "function") {
            throw new Error("Secure token generation is unavailable. Open the admin page on localhost or HTTPS and try again.");
        }
        var browserHost = String(window.location && window.location.hostname || "").toLowerCase();
        var localAdmin = browserHost === "localhost" || browserHost === "127.0.0.1" || browserHost === "::1" || browserHost === "[::1]";
        if (!window.isSecureContext && !localAdmin) {
            throw new Error("Open the admin page on localhost or HTTPS before generating a token. This protects the token while it is saved.");
        }
        var bytes = new Uint8Array(32);
        window.crypto.getRandomValues(bytes);
        var binary = "";
        for (var index = 0; index < bytes.length; index += 1) {
            binary += String.fromCharCode(bytes[index]);
        }
        var encoded = window.btoa(binary).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/g, "");
        return String(prefix || "") + encoded;
    }

    function mcpAdapterEnvText() {
        var token = String(getByPath(state.mcpSetup, "generatedToken", "") || "");
        if (!token) {
            throw new Error("Generate and save a server token before downloading the adapter config.");
        }

        var mcpStatus = getByPath(state.bootstrap, "status.mcp", {});
        var serverEndpoint = new URL(String(getByPath(mcpStatus, "server_endpoint", defaultMcpEndpoint())));
        var adapterUrl = new URL(String(getByPath(mcpStatus, "adapter_url", "http://127.0.0.1:8071")));
        var isLoopback = function (hostname) {
            var host = String(hostname || "").toLowerCase();
            return host === "127.0.0.1" || host === "localhost" || host === "::1" || host === "[::1]";
        };
        if (!isLoopback(serverEndpoint.hostname) || !isLoopback(adapterUrl.hostname)) {
            throw new Error("The private adapter config requires the AutoYou server and MCP adapter to use loopback addresses.");
        }
        if (!/^https?:$/.test(serverEndpoint.protocol) || !/^https?:$/.test(adapterUrl.protocol)) {
            throw new Error("The server and adapter addresses must use HTTP or HTTPS.");
        }

        var port = adapterUrl.port || (adapterUrl.protocol === "https:" ? "443" : "80");
        return [
            "# AutoYou MCP private adapter settings. Keep this file private.",
            "AUTOYOU_MCP_AUTH_MODE=none",
            "AUTOYOU_MCP_ENV=private",
            "AUTOYOU_MCP_HOST=127.0.0.1",
            "AUTOYOU_MCP_PORT=" + port,
            "AUTOYOU_MCP_BACKEND_MODE=full",
            "AUTOYOU_MCP_FULL_BASE_URL=" + serverEndpoint.origin,
            "AUTOYOU_MCP_FULL_API_TOKEN=" + token,
            ""
        ].join("\r\n");
    }

    function downloadMcpAdapterConfig() {
        var contents = mcpAdapterEnvText();
        var blob = new Blob([contents], { type: "text/plain;charset=utf-8" });
        var objectUrl = URL.createObjectURL(blob);
        var anchor = document.createElement("a");
        anchor.href = objectUrl;
        anchor.download = "autoyou-mcp.env";
        anchor.style.display = "none";
        document.body.appendChild(anchor);
        anchor.click();
        document.body.removeChild(anchor);
        window.setTimeout(function () { URL.revokeObjectURL(objectUrl); }, 1000);
        state.mcpSetup.configDownloaded = true;
    }

    function applyTheme(theme) {
        document.documentElement.setAttribute('data-theme', theme || 'light');
    }

    function icon(name) {
        var path = ICONS[name] || "";
        return "<svg class=\"ayu-nav-icon\" viewBox=\"0 0 24 24\" fill=\"none\" stroke=\"currentColor\" stroke-width=\"1.8\" stroke-linecap=\"round\" stroke-linejoin=\"round\"><path d=\"" + path + "\"></path></svg>";
    }

    function badge(label, tone) {
        return "<span class=\"ayu-badge ayu-badge-" + escapeHtml(tone || "gray") + "\">" + escapeHtml(label) + "</span>";
    }

    function yesNo(value) {
        return value ? "Yes" : "No";
    }

    function formatByteSize(bytes) {
        var value = Math.max(0, Number(bytes) || 0);
        var units = ["B", "KB", "MB", "GB", "TB"];
        var unitIndex = 0;
        while (value >= 1024 && unitIndex < units.length - 1) {
            value = value / 1024;
            unitIndex += 1;
        }
        var rounded = unitIndex === 0 ? String(Math.round(value)) : String(Math.round(value * 10) / 10);
        return rounded + " " + units[unitIndex];
    }

    function isActionPending(action) {
        return Boolean(action && state.pendingActions && state.pendingActions[action]);
    }

    function isImmediateAction(action) {
        return !action || action.indexOf("nav:") === 0 || action.indexOf("interact-select:") === 0 || action === "toggle-nav" || action === "close-nav" || action === "modal-copy" || action === "profile-image-select" || action === "cropper-choose-other" || action === "video-file-select" || action === "chat-file-select" || action.indexOf("setup-step:") === 0 || action.indexOf("setup-profile:") === 0 || action.indexOf("setup-answer:") === 0 || action === "setup-prev" || action === "setup-next" || action.indexOf("setup-open-screen:") === 0 || action === "setup-return" || action === "security-generate-password" || action === "security-toggle-password" || action === "security-copy-password" || action.indexOf("instructions-section:") === 0 || action.indexOf("instructions-mode:") === 0 || action.indexOf("agent-workbench-tab:") === 0 || action.indexOf("live-target-client:") === 0 || action.indexOf("live-target-session:") === 0 || action.indexOf("select-tts-provider:") === 0 || action === "telegram-approve-selected" || action === "boot-sweep-start" || action === "boot-sweep-stop" || action.indexOf("guide-open:") === 0 || action === "close-modal";
    }

    async function withPendingAction(action, callback) {
        if (!action) {
            return callback();
        }
        state.pendingActions[action] = Number(state.pendingActions[action] || 0) + 1;
        renderApp();
        try {
            return await callback();
        } finally {
            var current = Math.max(0, Number(state.pendingActions[action] || 0) - 1);
            state.pendingActions[action] = current;
            if (!current) {
                delete state.pendingActions[action];
            }
            renderApp();
        }
    }

    function button(label, action, variant, iconName, size, extraAttrs) {
        var pending = isActionPending(action);
        var iconMarkup = pending
            ? "<span class=\"ayu-btn-spinner\" aria-hidden=\"true\"></span>"
            : (iconName ? icon(iconName) : "");
        return "<button class=\"ayu-btn ayu-btn-" + escapeHtml(variant || "secondary") + (size ? " ayu-btn-" + escapeHtml(size) : "") + (pending ? " is-loading" : "") + "\" type=\"button\" data-action=\"" + escapeHtml(action) + "\"" + (pending ? " disabled aria-busy=\"true\"" : "") + (extraAttrs ? " " + extraAttrs : "") + ">" + iconMarkup + "<span>" + escapeHtml(label) + "</span></button>";
    }

    function iconButton(action, label, iconName, extraClass, extraAttrs) {
        return "<button class=\"ayu-icon-btn" + (extraClass ? " " + escapeHtml(extraClass) : "") + "\" type=\"button\" data-action=\"" + escapeHtml(action) + "\" aria-label=\"" + escapeHtml(label) + "\" title=\"" + escapeHtml(label) + "\"" + (extraAttrs ? " " + extraAttrs : "") + ">" + icon(iconName) + "</button>";
    }

    function panel(title, subtitle, body, actions) {
        return "<section class=\"ayu-panel\"><div class=\"ayu-panel-head\"><div><h2>" + escapeHtml(title) + "</h2>" + (subtitle ? "<p>" + escapeHtml(subtitle) + "</p>" : "") + "</div>" + (actions ? "<div class=\"ayu-inline-actions\">" + actions + "</div>" : "") + "</div><div class=\"ayu-panel-body\">" + body + "</div></section>";
    }

    function findOption(options, optionId, fallbackId) {
        var normalizedId = String(optionId || "").trim().toLowerCase();
        var fallback = String(fallbackId || "").trim().toLowerCase();
        return (options || []).find(function (option) {
            return option.id === normalizedId;
        }) || (options || []).find(function (option) {
            return option.id === fallback;
        }) || null;
    }

    function renderChoiceCards(options, activeId, actionPrefix) {
        return "<div class=\"ayu-choice-grid\">" + (options || []).map(function (option) {
            var isActive = option.id === activeId;
            return "<button type=\"button\" class=\"ayu-choice-card" + (isActive ? " active" : "") + "\" data-action=\"" + escapeHtml(actionPrefix + ":" + option.id) + "\"><div class=\"ayu-choice-card-top\"><div><div class=\"ayu-choice-card-eyebrow\">" + escapeHtml(option.eyebrow || "") + "</div><div class=\"ayu-choice-card-title\">" + escapeHtml(option.label) + "</div></div>" + badge(option.badgeLabel || "Available", option.badgeTone || "gray") + "</div><p class=\"ayu-choice-card-copy\">" + escapeHtml(option.description || "") + "</p></button>";
        }).join("") + "</div>";
    }

    function aiCatalogItems() {
        return getByPath(state.aiLibrary, "catalog.items", getByPath(state.aiLibrary, "catalog.models", getByPath(state.aiLibrary, "catalog.results", [])));
    }

    function aiCatalogItemIdentifier(item) {
        return item.reference || item.name || item.model || item.id || item.slug || item.repo_id || "model";
    }

    function aiCatalogItemTitle(item) {
        var ref = aiCatalogItemIdentifier(item);
        return item.title || item.name || item.model || item.id || item.slug || item.repo_id || ref;
    }

    function renderCatalogInlineDetail(source, detail) {
        if (!detail) {
            return "";
        }
        if (source === "ollama") {
            var variants = Array.isArray(detail.variants) ? detail.variants : [];
            var variantMarkup = variants.length ? variants.map(function (variant) {
                var variantReference = String(variant.name || variant.reference || "");
                var variantAttrs = "data-source=\"ollama\" data-reference=\"" + escapeHtml(variantReference) + "\" data-model=\"" + escapeHtml(variantReference) + "\" data-title=\"" + escapeHtml(variant.display_name || variantReference) + "\" data-installed=\"" + (variant.installed ? "1" : "0") + "\"";
                var badges = [
                    badge(variant.is_cloud ? "Cloud" : "Local", variant.is_cloud ? "amber" : "blue"),
                    variant.installed ? badge("Installed", "green") : ""
                ].join(" ");
                var meta = [variant.size, variant.context, variant.input_type].filter(Boolean).join(" | ");
                return "<div class=\"ayu-variant-card\"><div class=\"ayu-variant-head\"><div><strong>" + escapeHtml(variant.display_name || variantReference) + "</strong>" + (meta ? "<small>" + escapeHtml(meta) + "</small>" : "") + "</div><div class=\"ayu-inline-actions\">" + badges + "</div></div>" + (variant.is_cloud ? "<div class=\"ayu-note ayu-note-amber\">Runs remotely through Ollama Cloud after <code>ollama signin</code>.</div>" : "") + "<div class=\"ayu-inline-actions\">" + button(variant.installed ? "Use model" : "Download and use", "ai-detail-select:" + modelKey(variantReference), variant.installed ? "primary" : "secondary", variant.installed ? "save" : "plus", "sm", variantAttrs) + button(variant.is_cloud ? "Pull cloud reference" : "Download", "ai-detail-download:" + modelKey(variantReference), "ghost", "plus", "sm", variantAttrs) + "</div></div>";
            }).join("") : "<div class=\"ayu-empty\">No tags were reported for this model.</div>";
            return (detail.url ? "<div class=\"ayu-inline-actions\"><a class=\"ayu-link-btn ayu-btn ayu-btn-ghost ayu-btn-sm\" href=\"" + escapeHtml(detail.url) + "\" target=\"_blank\" rel=\"noreferrer\">" + icon("external") + "<span>Open on Ollama</span></a></div>" : "") + "<div class=\"ayu-variant-list\">" + variantMarkup + "</div>";
        }
        var files = Array.isArray(detail.gguf_files) ? detail.gguf_files : [];
        var fileMarkup = files.length ? files.map(function (file) {
            var ollamaReference = String(file.ollama_reference || "");
            var fileAttrs = "data-source=\"huggingface\" data-reference=\"" + escapeHtml(ollamaReference) + "\" data-model=\"" + escapeHtml(ollamaReference) + "\" data-title=\"" + escapeHtml(ollamaReference || file.filename || file.quantization || detail.id) + "\" data-repo-id=\"" + escapeHtml(detail.id || "") + "\" data-quantization=\"" + escapeHtml(file.quantization || "") + "\" data-installed=\"" + (file.installed ? "1" : "0") + "\"";
            var meta = [file.size_human, file.filename].filter(Boolean).join(" | ");
            return "<div class=\"ayu-variant-card\"><div class=\"ayu-variant-head\"><div><strong>" + escapeHtml(file.quantization || file.filename || ollamaReference) + "</strong>" + (meta ? "<small>" + escapeHtml(meta) + "</small>" : "") + "</div><div class=\"ayu-inline-actions\">" + badge("GGUF", "blue") + (file.installed ? badge("Installed", "green") : "") + "</div></div><div class=\"ayu-inline-actions\">" + button(file.installed ? "Use reference" : "Download and use", "ai-detail-select:" + modelKey(ollamaReference), file.installed ? "primary" : "secondary", file.installed ? "save" : "plus", "sm", fileAttrs) + button("Download via Ollama", "ai-detail-download:" + modelKey(ollamaReference), "ghost", "plus", "sm", fileAttrs) + "</div></div>";
        }).join("") : "<div class=\"ayu-empty\">No GGUF files were found in this repository.</div>";
        return (detail.url ? "<div class=\"ayu-inline-actions\"><a class=\"ayu-link-btn ayu-btn ayu-btn-ghost ayu-btn-sm\" href=\"" + escapeHtml(detail.url) + "\" target=\"_blank\" rel=\"noreferrer\">" + icon("external") + "<span>Open on Hugging Face</span></a></div>" : "") + "<div class=\"ayu-variant-list\">" + fileMarkup + "</div>";
    }

    function renderAiCatalogMarkup() {
        var catalogItems = aiCatalogItems();
        var listMarkup = "";
        if (state.aiLibrary.catalogLoading && (!Array.isArray(catalogItems) || !catalogItems.length)) {
            listMarkup = "<div class=\"ayu-empty\">Loading model catalog...</div>";
        } else if (Array.isArray(catalogItems) && catalogItems.length) {
            listMarkup = "<div class=\"ayu-list\">" + catalogItems.map(function (item) {
                var ref = aiCatalogItemIdentifier(item);
                var title = aiCatalogItemTitle(item);
                var detailKey = catalogDetailKey(state.aiLibrary.source, ref);
                var isExpanded = state.aiLibrary.selectedCatalogKey === detailKey;
                var detailLoading = state.aiLibrary.detailLoadingKey === detailKey;
                var detailPayload = getByPath(state.aiLibrary, "details." + detailKey, null);
                var details = [
                    item.summary || item.description || "",
                    Array.isArray(item.capabilities) && item.capabilities.length ? ("Capabilities: " + item.capabilities.join(", ")) : "",
                    Array.isArray(item.sizes) && item.sizes.length ? ("Sizes: " + item.sizes.join(", ")) : "",
                    item.pull_count ? ("Pulls: " + item.pull_count) : "",
                    item.updated ? ("Updated: " + item.updated) : ""
                ].filter(Boolean);
                return "<div class=\"ayu-catalog-entry\"><div class=\"ayu-list-row\"><button type=\"button\" class=\"ayu-card-trigger\" data-action=\"ai-catalog-detail:" + escapeHtml(modelKey(ref)) + "\" data-source=\"" + escapeHtml(state.aiLibrary.source) + "\" data-reference=\"" + escapeHtml(ref) + "\" data-title=\"" + escapeHtml(title) + "\"><div class=\"ayu-list-copy\"><strong>" + escapeHtml(title) + "</strong><p>" + escapeHtml(shortText(details.join(" | "), 240)) + "</p><small class=\"ayu-code\">" + escapeHtml(ref) + "</small></div></button><div class=\"ayu-inline-actions\">" + button(isExpanded ? "Hide variants" : "View variants", "ai-catalog-detail:" + modelKey(ref), isExpanded ? "ghost" : "secondary", isExpanded ? "close" : "search", "sm", "data-source=\"" + escapeHtml(state.aiLibrary.source) + "\" data-reference=\"" + escapeHtml(ref) + "\" data-title=\"" + escapeHtml(title) + "\"") + (item.url ? ("<a class=\"ayu-link-btn ayu-btn ayu-btn-ghost ayu-btn-sm\" href=\"" + escapeHtml(item.url) + "\" target=\"_blank\" rel=\"noreferrer\">" + icon("external") + "<span>Details</span></a>") : "") + "</div></div>" + (isExpanded ? "<div class=\"ayu-catalog-detail\">" + (detailLoading ? "<div class=\"ayu-empty\">Loading variants for " + escapeHtml(title) + "...</div>" : renderCatalogInlineDetail(state.aiLibrary.source, detailPayload)) + "</div>" : "") + "</div>";
            }).join("") + "</div>";
        } else {
            listMarkup = "<div class=\"ayu-empty\">No catalog results yet. Search the Ollama or Hugging Face catalog to browse models.</div>";
        }
        if (state.aiLibrary.catalogError) {
            listMarkup = "<div class=\"ayu-note ayu-note-amber\">" + escapeHtml(state.aiLibrary.catalogError) + "</div>" + listMarkup;
        }
        if (state.aiLibrary.catalogHasMore) {
            listMarkup += "<div class=\"ayu-inline-actions\">" + button(state.aiLibrary.catalogLoading ? "Loading more..." : "Load more", "ai-load-more", "secondary", "refresh", "sm", state.aiLibrary.catalogLoading ? "disabled" : "") + "</div>";
        }
        return listMarkup;
    }

    function renderAiProviderSettingsBody() {
        var provider = activeAiProvider();
        var providerOption = findOption(AI_PROVIDER_OPTIONS, provider, "ollama") || AI_PROVIDER_OPTIONS[0];
        var providerFields = [];
        var availableProviders = AI_PROVIDER_OPTIONS.filter(function (option) {
            return option.id !== "apple_intelligence" || provider === option.id || getByPath(state.bootstrap, "status.apple_intelligence.supported", false);
        });
        if (provider === "apple_intelligence") {
            providerFields.push("<div class=\"ayu-note\">" + escapeHtml(getByPath(state.bootstrap, "status.apple_intelligence.detail", "Apple Intelligence is unavailable on this computer.")) + " Text generation runs on your Mac. Tools you request may connect to their own services.</div>");
        }
        if (isOllamaProvider(provider)) {
            var nativeOllama = provider === "ollama_gateway";
            providerFields.push(checkbox("aiProvider.ollama_enabled", "Enable local Ollama runtime", "Disable only if you intentionally do not want the local Ollama process available."));
            providerFields.push(field("Ollama API base", input("aiProvider.ollama_api_base", { placeholder: "http://localhost:11434" }), "Point this at the local Ollama server that hosts your downloaded models."));
            providerFields.push(field("Primary Ollama model", input("aiProvider.ollama_model", { placeholder: "llama3.2:3b" }), nativeOllama ? "This model is sent directly to Ollama's native chat API." : "This becomes the default local model for the AutoYou runtime."));
        } else if (provider === "google") {
            providerFields.push(field("Gemini model", input("aiProvider.google_model", { placeholder: "gemini-2.5-flash" }), "AutoYou uses the official Google API path while keeping its own agents active."));
            providerFields.push(field("Google API key", input("aiProvider.google_api_key", { placeholder: "Leave blank to keep saved key", type: "password" }), "Only enter a value when rotating or adding the saved key."));
        } else if (provider === "litellm") {
            providerFields.push(field("Provider/model string", input("aiProvider.litellm_model", { placeholder: "anthropic/claude-sonnet-4-5" }), "Examples: anthropic/..., openai/..., mistral/..."));
            providerFields.push(field("API base override", input("aiProvider.litellm_api_base", { placeholder: "Optional custom LiteLLM endpoint" }), "Leave empty for the provider's standard API base."));
            providerFields.push(field("API key", input("aiProvider.litellm_api_key", { placeholder: "Leave blank to keep saved key", type: "password" }), "Stored locally; leave empty to preserve the current secret."));
        } else if (provider === "openclaw") {
            providerFields.push(field("Gateway port", input("aiProvider.openclaw_port", { type: "number" }), "Local port for the OpenClaw gateway."));
            providerFields.push(field("Gateway token", input("aiProvider.openclaw_token", { placeholder: "Leave blank to keep saved token", type: "password" }), "Bearer token used for the OpenClaw gateway when required."));
            providerFields.push(field("OpenClaw model", input("aiProvider.openclaw_model", { placeholder: "openclaw/default" }), "Active model or route selected inside the OpenClaw gateway."));
        } else if (provider === "hermes") {
            providerFields.push(field("Gateway port", input("aiProvider.hermes_port", { type: "number" }), "Local port for the Hermes Agent gateway."));
            providerFields.push(field("Gateway token", input("aiProvider.hermes_token", { placeholder: "Leave blank to keep saved token", type: "password" }), "Bearer token used for the Hermes gateway when required."));
            providerFields.push(field("Hermes model", input("aiProvider.hermes_model", { placeholder: "hermes-agent" }), "Active model or route selected inside the Hermes gateway."));
        } else if (provider === "odysseus") {
            providerFields.push(field("Odysseus API base", input("aiProvider.odysseus_api_base", { placeholder: "http://127.0.0.1:7000" }), "The external Odysseus companion service endpoint."));
            providerFields.push(field("Odysseus model", input("aiProvider.odysseus_model", { placeholder: "Leave blank for Odysseus default" }), "Choose a registered Odysseus endpoint model, or leave blank for its first enabled model."));
            providerFields.push(field("Odysseus chat token", input("aiProvider.odysseus_token", { placeholder: "Leave blank to keep saved token", type: "password" }), "Use an Odysseus chat-scoped token. It stays in protected AutoYou configuration."));
        }

        var bridgeInstalled = getByPath(state.bootstrap, "agents.agent_overview", []).some(function (agent) { return agent.agent_name === "openclaw_agent" && agent.installed; });
        var bridgeMarkup = provider === "openclaw" || bridgeInstalled
            ? "<div class=\"ayu-soft-divider\"></div><div><h3 style=\"margin:0 0 10px;font-size:15px;\">Optional OpenClaw sub-agent bridge</h3><div class=\"ayu-grid-2\">" + field("Bridge port", input("aiProvider.openclaw_agent_port", { type: "number" }), "The local OpenClaw instance used by the installed bridge, independently of your main AI provider.") + field("Bridge model", input("aiProvider.openclaw_agent_model", { placeholder: "openclaw/default" }), "Use openclaw:main for the main agent and its workspace, or openclaw/default for the gateway's default agent.") + field("Bridge token", input("aiProvider.openclaw_agent_token", { placeholder: "Leave blank to keep saved token", type: "password" })) + "</div></div>"
            : "";

        var compatibilityNote = aiProviderUsesGateway(provider)
            ? "<div class=\"ayu-note ayu-note-amber\">" + escapeHtml(provider === "ollama_gateway"
                ? "Native Ollama gateway mode sends chat directly to the saved Ollama endpoint. AutoYou AI and Agent Studio do not run for these replies."
                : (provider === "odysseus"
                    ? "Odysseus gateway mode uses Odysseus's authenticated companion sessions directly. AutoYou AI and Agent Studio do not run for these replies."
                    : (provider === "hermes"
                        ? "Hermes provider mode is gateway-first. Hermes-backed sessions follow the Hermes runtime, so use Ollama, Gemini, or Other AI Providers when you want installed AutoYou agents to remain the primary runtime surface."
                        : "OpenClaw provider mode is gateway-first. OpenClaw-backed sessions follow the OpenClaw conversation flow, so use Ollama, Gemini, or Other AI Providers when you want installed AutoYou agents to remain the primary runtime surface."))) + "</div>"
            : "<div class=\"ayu-note ayu-note-green\">This provider keeps AutoYou's own agent runtime and installed agents active as the main orchestration layer.</div>";

        var gatewayStatus = provider === "hermes" ? state.aiLibrary.hermesStatus : (provider === "odysseus" ? state.aiLibrary.odysseusStatus : (provider === "ollama_gateway" ? state.aiLibrary.ollamaStatus : state.aiLibrary.openclawStatus));
        var gatewayLoading = provider === "hermes" ? state.aiLibrary.hermesLoading : (provider === "odysseus" ? state.aiLibrary.odysseusLoading : (provider === "ollama_gateway" ? state.aiLibrary.ollamaLoading : state.aiLibrary.openclawLoading));
        var gatewayName = provider === "hermes" ? "Hermes" : (provider === "odysseus" ? "Odysseus" : (provider === "ollama_gateway" ? "Ollama" : "OpenClaw"));
        var gatewayEndpoint = provider === "hermes"
            ? String(getByPath(state.forms, "aiProvider.hermes_port", 8642))
            : (provider === "odysseus"
                ? String(getByPath(gatewayStatus, "api_base", getByPath(state.forms, "aiProvider.odysseus_api_base", "http://127.0.0.1:7000")))
                : (provider === "ollama_gateway"
                    ? String(getByPath(gatewayStatus, "api_base", getByPath(state.forms, "aiProvider.ollama_api_base", "http://127.0.0.1:11434")))
                    : String(getByPath(state.forms, "aiProvider.openclaw_port", 18789))));
        var gatewayStatusMarkup = aiProviderUsesGateway(provider)
            ? (gatewayLoading
                ? "<div class=\"ayu-empty\">Checking " + escapeHtml(gatewayName) + " gateway status...</div>"
                : renderStatusRows([
                    { label: "Gateway ready", value: (getByPath(gatewayStatus, "running", getByPath(gatewayStatus, "available", false))) ? "Yes" : "No" },
                    { label: provider === "ollama_gateway" || provider === "odysseus" ? "Gateway endpoint" : "Gateway port", value: gatewayEndpoint },
                    { label: "Reported models", value: Array.isArray(getByPath(gatewayStatus, "models", [])) && getByPath(gatewayStatus, "models", []).length ? getByPath(gatewayStatus, "models", []).join(", ") : getByPath(gatewayStatus, "error", "No model list reported") }
                ]))
            : "";

        return renderChoiceCards(availableProviders, provider, "select-ai-provider") + compatibilityNote + "<div class=\"ayu-note\"><strong>Selected path:</strong> " + escapeHtml(providerOption.label) + ". " + escapeHtml(providerOption.description) + "</div><div class=\"ayu-grid-2\">" + providerFields.join("") + "</div>" + gatewayStatusMarkup + bridgeMarkup + "<div class=\"ayu-inline-actions\">" + button("Save provider settings", "save-ai-provider", "primary", "save") + button("Refresh runtime data", "ai-refresh", "secondary", "refresh") + "</div>";
    }

    function renderModelBehaviorBody() {
        var provider = activeAiProvider();
        if (provider === "odysseus") {
            return "<div class=\"ayu-note ayu-note-amber\">Odysseus owns model behavior for this gateway mode. Configure its endpoint, model, and token in the selected provider settings.</div>";
        }
        var behaviorModes = getByPath(state.aiLibrary, "behavior.modes", {});
        var behaviorOptions = Object.keys(behaviorModes).map(function (mode) {
            return { value: mode, label: getByPath(behaviorModes, mode + ".label", prettyLabel(mode)) };
        });
        var currentMode = getByPath(state.forms, "modelBehavior.mode", getByPath(state.aiLibrary, "behavior.mode", "accurate"));
        var presetParams = getByPath(state.aiLibrary, "behavior.modes." + currentMode + ".params", getByPath(state.aiLibrary, "behavior.preset_params", {}));
        var resolvedParams = getByPath(state.aiLibrary, "behavior.resolved_params", {});
        var overrideRows = ["temperature", "top_p", "top_k", "repeat_penalty", "num_ctx"].map(function (fieldName) {
            var customValue = getByPath(state.forms, "modelBehavior." + fieldName, "");
            var presetValue = getByPath(presetParams, fieldName, "");
            var resolvedValue = getByPath(resolvedParams, fieldName, "");
            return {
                label: prettyLabel(fieldName),
                value: hasValue(customValue) ? String(customValue) : "Preset",
                help: "Preset: " + (hasValue(presetValue) ? String(presetValue) : "none") + " | Resolved: " + (hasValue(resolvedValue) ? String(resolvedValue) : "automatic")
            };
        });
        var thinkingCapability = getByPath(state.aiLibrary, "behavior.thinking_capability", {});
        var thinkingSupported = getByPath(thinkingCapability, "supports_thinking", null) === true;
        var thinkingCapabilityAvailable = getByPath(thinkingCapability, "available", false) === true;
        var thinkingLevels = getByPath(thinkingCapability, "thinking_levels", []);
        var thinkingFamily = String(getByPath(thinkingCapability, "family", "") || "").toLowerCase();
        var thinkingIsGptOss = thinkingFamily.indexOf("gptoss") !== -1 || String(getByPath(thinkingCapability, "name", "")).toLowerCase().indexOf("gpt-oss") === 0;
        var thinkingMarkup = "";
        if (isOllamaProvider(provider)) {
            if (thinkingSupported) {
                var thinkingOptions = [{ value: "", label: "Auto" }].concat((Array.isArray(thinkingLevels) ? thinkingLevels : []).map(function (level) {
                    return { value: String(level), label: String(level).toUpperCase() };
                }));
                var thinkingHint = thinkingIsGptOss
                    ? "GPT-OSS always reasons; the level controls its effort. The Show model thinking toggle controls whether reasoning is exposed in chat."
                    : "The level is passed to Ollama only when this model reports thinking support.";
                thinkingMarkup = field("Thinking level", select("modelBehavior.thinking_level", thinkingOptions), thinkingHint);
            } else if (thinkingCapabilityAvailable) {
                thinkingMarkup = "<div class=\"ayu-note ayu-note-gray ayu-model-thinking-note\">Ollama reports that the selected model does not support thinking. AutoYou will omit the thinking option.</div>";
            } else {
                thinkingMarkup = "<div class=\"ayu-note ayu-note-amber ayu-model-thinking-note\">Ollama capability data is unavailable for the selected model. AutoYou will use compatibility defaults until the model can be inspected.</div>";
            }
        }
        var providerNote = isOllamaProvider(provider)
            ? "Ollama uses these presets and can auto-resolve context size unless you override num_ctx manually."
            : (aiProviderUsesGateway(provider)
                ? (provider === "hermes"
                    ? "Hermes manages its own runtime behavior. AutoYou's sampling presets are not the main control surface in this mode."
                    : (provider === "odysseus"
                        ? "Odysseus owns its own model/session behavior. Configure its selected endpoint in the gateway settings above."
                        : "OpenClaw manages its own runtime behavior. AutoYou's sampling presets are not the main control surface in this mode."))
                : "These presets apply through the AutoYou runtime for Gemini and other LiteLLM-backed providers.");
        return "<div class=\"ayu-note\">" + escapeHtml(providerNote) + "</div>" + field("Preset", select("modelBehavior.mode", behaviorOptions.length ? behaviorOptions : ["accurate", "human", "creative", "none"])) + "<div class=\"ayu-note ayu-note-green\">Leave a field blank to keep the preset value. Matching the preset no longer saves a fake custom override.</div><div class=\"ayu-grid-2\">" + field("Temperature", input("modelBehavior.temperature", { placeholder: hasValue(getByPath(presetParams, "temperature", "")) ? String(getByPath(presetParams, "temperature", "")) : "preset" })) + field("Top P", input("modelBehavior.top_p", { placeholder: hasValue(getByPath(presetParams, "top_p", "")) ? String(getByPath(presetParams, "top_p", "")) : "preset" })) + field("Top K", input("modelBehavior.top_k", { placeholder: hasValue(getByPath(presetParams, "top_k", "")) ? String(getByPath(presetParams, "top_k", "")) : "preset" })) + field("Repeat penalty", input("modelBehavior.repeat_penalty", { placeholder: hasValue(getByPath(presetParams, "repeat_penalty", "")) ? String(getByPath(presetParams, "repeat_penalty", "")) : "preset" })) + field("Context override", input("modelBehavior.num_ctx", { placeholder: isOllamaProvider(provider) ? "Leave blank for automatic" : "Optional" }), isOllamaProvider(provider) ? "Leave empty to keep Ollama's automatic context recommendation." : "Only used when the runtime honors context overrides.") + "</div>" + thinkingMarkup + checkbox("modelBehavior.show_thinking", "Show model thinking", "Off by default. Reveals the model's internal reasoning in chat replies instead of hiding it.") + "<div class=\"ayu-inline-actions\">" + button("Save model behavior", "save-model-behavior", "primary", "save") + "</div>" + (state.aiLibrary.behaviorLoading ? "<div class=\"ayu-empty\">Loading model behavior details...</div>" : renderStatusRows(overrideRows) + renderJsonNote({
            preset: getByPath(state.aiLibrary, "behavior.label", currentMode),
            description: getByPath(state.aiLibrary, "behavior.description", ""),
            advanced_overrides: getByPath(state.aiLibrary, "behavior.advanced_overrides", {}),
            show_thinking: getByPath(state.aiLibrary, "behavior.show_thinking", false),
            thinking_capability: thinkingCapability,
            thinking_level: getByPath(state.aiLibrary, "behavior.thinking_level", null)
        }, aiProviderUsesGateway(provider) ? "amber" : "green"));
    }

    function renderManageModelsBody() {
        var cfg = bootstrapConfig();
        var provider = activeAiProvider();
        var localModels = getByPath(state.aiLibrary, "local.models", []);
        var localRuntime = getByPath(state.aiLibrary, "local.runtime", {});
        var selectedModel = getByPath(state.aiLibrary, "local.selected_model", getByPath(cfg, "ollama.model", ""));
        var downloads = getByPath(state.aiLibrary, "downloads.jobs", []);
        var sourceOptions = [
            { value: "ollama", label: "Ollama catalog" },
            { value: "huggingface", label: "Hugging Face GGUF" }
        ];

        var runtimeMarkup = renderStatusRows([
            { label: "Selected model", value: selectedModel || "No model selected" },
            { label: "API base", value: getByPath(localRuntime, "api_base", getByPath(cfg, "ollama.api_base", "http://localhost:11434")), mono: true },
            { label: "Reachable", value: getByPath(localRuntime, "api_reachable", false) ? "Yes" : "No" },
            { label: "Local models", value: String(Array.isArray(localModels) ? localModels.length : 0) }
        ]);

        var localMarkup = state.aiLibrary.localLoading
            ? "<div class=\"ayu-empty\">Loading local model inventory...</div>"
            : (Array.isArray(localModels) && localModels.length
                ? "<div class=\"ayu-list\">" + localModels.map(function (model) {
                    var name = model.name || model.model || model.digest || "model";
                    var capabilityLabels = [];
                    if (getByPath(model, "supports_thinking", false) === true) {
                        capabilityLabels.push("Thinking");
                    }
                    if (getByPath(model, "supports_tools", false) === true) {
                        capabilityLabels.push("Tools");
                    }
                    if (getByPath(model, "supports_vision", false) === true) {
                        capabilityLabels.push("Vision");
                    }
                    var details = [
                        getByPath(model, "size_human", ""),
                        getByPath(model, "modified_at", ""),
                        capabilityLabels.length ? capabilityLabels.join(", ") : "Capabilities not reported"
                    ].filter(Boolean).join(" | ");
                    var capabilityMarkup = capabilityLabels.length
                        ? "<div class=\"ayu-model-capability-list\">" + capabilityLabels.map(function (label) { return "<span class=\"ayu-model-capability\">" + escapeHtml(label) + "</span>"; }).join("") + "</div>"
                        : "";
                    return "<div class=\"ayu-list-row\"><div class=\"ayu-list-copy\"><strong>" + escapeHtml(name) + "</strong>" + capabilityMarkup + "<small>" + escapeHtml(details || "Available locally") + "</small></div><div class=\"ayu-inline-actions\">" + (name === selectedModel ? badge("Selected", "green") : "") + button("Use this model", "ai-select-local:" + modelKey(name), "secondary", "save", "sm", "data-model=\"" + escapeHtml(name) + "\"") + button("Delete", "ai-delete-local:" + modelKey(name), "danger", "trash", "sm", "data-model=\"" + escapeHtml(name) + "\"") + "</div></div>";
                }).join("") + "</div>"
                : "<div class=\"ayu-empty\">No local Ollama models were reported yet.</div>");

        var providerNote = isOllamaProvider(provider)
            ? "<div class=\"ayu-note ayu-note-green\">Local model management is active because Ollama is the current provider.</div>"
            : "<div class=\"ayu-note\">Manage Models always reflects the local Ollama library. This stays separate from cloud providers so you can switch back to local models without reconfiguring them.</div>";

        var downloadsMarkup = Array.isArray(downloads) && downloads.length
            ? "<div class=\"ayu-list\">" + downloads.map(function (job) {
                var progress = Number(getByPath(job, "progress_percent", 0)) || 0;
                var status = getByPath(job, "status", "queued");
                var detailText = [
                    getByPath(job, "message", ""),
                    getByPath(job, "completed_bytes_human", "") && getByPath(job, "total_bytes_human", "") ? (getByPath(job, "completed_bytes_human", "") + " / " + getByPath(job, "total_bytes_human", "")) : ""
                ].filter(Boolean).join(" | ");
                return "<div class=\"ayu-list-row ayu-list-row-stack\"><div class=\"ayu-list-copy\"><strong>" + escapeHtml(getByPath(job, "title", getByPath(job, "reference", getByPath(job, "model", getByPath(job, "job_id", "Download"))))) + "</strong><small>" + escapeHtml(detailText || status) + "</small></div><div class=\"ayu-inline-actions\">" + badge(status, statusTone(status)) + "<span class=\"ayu-hint ayu-mono\">" + escapeHtml(progress.toFixed(1)) + "%</span></div>" + renderProgressTrack(progress, String(status).toLowerCase() === "failed" ? "failed" : "") + (getByPath(job, "error", "") ? "<div class=\"ayu-note ayu-note-red\">" + escapeHtml(getByPath(job, "error", "")) + "</div>" : "") + "</div>";
            }).join("") + "</div>"
            : "<div class=\"ayu-empty\">No active model downloads.</div>";

        if (state.aiLibrary.pendingSelection && getByPath(state.aiLibrary.pendingSelection, "model", "")) {
            downloadsMarkup = "<div class=\"ayu-note ayu-note-green\">AutoYou will switch to <strong>" + escapeHtml(getByPath(state.aiLibrary.pendingSelection, "model", "")) + "</strong> as soon as its download finishes.</div>" + downloadsMarkup;
        }

        return providerNote + runtimeMarkup + "<div class=\"ayu-soft-divider\"></div><h3 style=\"margin:0;font-size:15px;\">Local model library</h3>" + localMarkup + "<div class=\"ayu-inline-actions\">" + button("Refresh local library", "ai-refresh-local", "secondary", "refresh") + "</div><div class=\"ayu-soft-divider\"></div><h3 style=\"margin:0;font-size:15px;\">Model catalog</h3><div class=\"ayu-grid-2\">" + field("Search query", input("agentWorkbench.catalog_query", { placeholder: "llama, qwen, mistral...", extraAttrs: "data-virtual-bind=\"aiLibrary.query\" value=\"" + valueAttr(state.aiLibrary.query) + "\"" })) + field("Source", "<select class=\"ayu-select\" data-action=\"ai-source\">" + sourceOptions.map(function (option) {
            return "<option value=\"" + escapeHtml(option.value) + "\"" + (state.aiLibrary.source === option.value ? " selected" : "") + ">" + escapeHtml(option.label) + "</option>";
        }).join("") + "</select>") + "</div><div class=\"ayu-inline-actions\">" + button("Search catalog", "ai-search-catalog", "primary", "search") + "</div>" + renderAiCatalogMarkup() + "<div class=\"ayu-soft-divider\"></div><h3 style=\"margin:0;font-size:15px;\">Download jobs</h3>" + downloadsMarkup;
    }

    // The admin page is served by the same app that hosts /api/v1/mcp, so its own
    // origin is the right default; 8001 is only the standalone-server fallback.
    function defaultMcpEndpoint() {
        return typeof window !== "undefined" && window.location && window.location.origin
            ? window.location.origin + "/api/v1/mcp"
            : "http://127.0.0.1:8001/api/v1/mcp";
    }

    function getByPath(source, path, fallbackValue) {
        if (!path) {
            return source;
        }
        var node = source;
        var parts = String(path).split(".");
        for (var index = 0; index < parts.length; index += 1) {
            var segment = parts[index];
            if (node == null) {
                return fallbackValue;
            }
            if (Array.isArray(node)) {
                node = node[Number(segment)];
            } else {
                node = node[segment];
            }
        }
        return node == null ? fallbackValue : node;
    }

    function firstNonBlank(values, fallbackValue) {
        for (var index = 0; index < values.length; index += 1) {
            var text = String(values[index] == null ? "" : values[index]).trim();
            if (text) {
                return text;
            }
        }
        return fallbackValue;
    }

    function asBoolean(value, fallbackValue) {
        if (value === undefined || value === null) {
            return Boolean(fallbackValue);
        }
        if (typeof value === "boolean") {
            return value;
        }
        if (typeof value === "number") {
            return value !== 0;
        }
        if (typeof value === "string") {
            var normalized = value.trim().toLowerCase();
            if (["1", "true", "yes", "on"].indexOf(normalized) !== -1) {
                return true;
            }
            if (["0", "false", "no", "off", ""].indexOf(normalized) !== -1) {
                return false;
            }
        }
        return Boolean(fallbackValue);
    }

    function sourceList(value) {
        if (Array.isArray(value)) {
            return value.map(function (item) { return String(item || "").trim(); }).filter(Boolean);
        }
        return String(value || "").split(/[;,]/).map(function (item) { return item.trim(); }).filter(Boolean);
    }

    function initialVideoSources(cfg) {
        var videoCfg = getByPath(cfg, "video_call", {});
        var hasSourceList = Object.prototype.hasOwnProperty.call(videoCfg || {}, "outbound_sources");
        var selected = sourceList(getByPath(cfg, "video_call.outbound_sources", []));
        var legacy = String(getByPath(cfg, "video_call.outbound_source", "remote_desktop") || "remote_desktop");
        if (!selected.length && !hasSourceList) {
            selected = ["camera", "remote_desktop", "api", "video_file"].indexOf(legacy) !== -1 ? [legacy] : [];
        }
        return selected;
    }

    function initialAudioSources(cfg) {
        var videoCfg = getByPath(cfg, "video_call", {});
        var hasSourceList = Object.prototype.hasOwnProperty.call(videoCfg || {}, "audio_sources");
        var selected = sourceList(getByPath(cfg, "video_call.audio_sources", []));
        if (!selected.length && !hasSourceList && asBoolean(getByPath(cfg, "video_call.capture_audio", true), true)) {
            var inputSource = String(getByPath(cfg, "video_call.input_audio_source", "default") || "default");
            var outboundSource = String(getByPath(cfg, "video_call.outbound_source", "remote_desktop") || "remote_desktop");
            selected = (inputSource === "desktop_loopback" || (outboundSource === "remote_desktop" && inputSource === "default"))
                ? ["speaker_loopback"]
                : ["microphone"];
        }
        return selected;
    }

    function setByPath(target, path, value) {
        var parts = String(path).split(".");
        var node = target;
        for (var index = 0; index < parts.length - 1; index += 1) {
            var segment = parts[index];
            var nextSegment = parts[index + 1];
            var isNextIndex = /^\d+$/.test(nextSegment);
            if (Array.isArray(node)) {
                if (node[Number(segment)] == null) {
                    node[Number(segment)] = isNextIndex ? [] : {};
                }
                node = node[Number(segment)];
            } else {
                if (node[segment] == null) {
                    node[segment] = isNextIndex ? [] : {};
                }
                node = node[segment];
            }
        }
        var last = parts[parts.length - 1];
        if (Array.isArray(node)) {
            node[Number(last)] = value;
        } else {
            node[last] = value;
        }
    }

    function clone(value) {
        return value == null ? value : JSON.parse(JSON.stringify(value));
    }

    function shortText(value, maxLength) {
        var text = String(value == null ? "" : value);
        if (!maxLength || maxLength < 1) {
            return "";
        }
        if (text.length <= maxLength) {
            return text;
        }
        if (maxLength <= 3) {
            return text.slice(0, maxLength);
        }
        return text.slice(0, maxLength - 3) + "...";
    }

    function avatarText(value) {
        var text = String(value == null ? "" : value).trim();
        var match = text.match(/[A-Za-z0-9]/);
        return ((match ? match[0] : text.charAt(0)) || "A").toUpperCase();
    }

    function statusTone(value) {
        var text = String(value || "").toLowerCase();
        if (!text || text === "unknown") {
            return "gray";
        }
        if (text.indexOf("error") !== -1 || text.indexOf("failed") !== -1 || text.indexOf("stopped") !== -1) {
            return "red";
        }
        if (text.indexOf("pending") !== -1 || text.indexOf("warning") !== -1 || text.indexOf("disabled") !== -1 || text.indexOf("not paired") !== -1 || text.indexOf("stalled") !== -1) {
            return "amber";
        }
        if (text.indexOf("connected") !== -1 || text.indexOf("running") !== -1 || text.indexOf("active") !== -1 || text.indexOf("enabled") !== -1 || text.indexOf("healthy") !== -1 || text.indexOf("paired") !== -1) {
            return "green";
        }
        return "blue";
    }

    function prettyLabel(value) {
        var text = String(value || "").replace(/_/g, " ").trim();
        if (!text) {
            return "Unknown";
        }
        return text.replace(/\b\w/g, function (letter) {
            return letter.toUpperCase();
        });
    }

    function isConnectedStatus(value) {
        var token = normalizeStatusToken(value).replace(/_/g, " ");
        if (!token || token.indexOf("disconnected") !== -1 || token.indexOf("not connected") !== -1) {
            return false;
        }
        return token === "connected" ||
            token === "paired" ||
            token === "paired and connected" ||
            token.indexOf(" connected") !== -1 ||
            token.indexOf("connected ") !== -1;
    }

    function setupPartnerStatusLabel(value, fallback) {
        var token = normalizeStatusToken(value);
        if (!token || token === "unknown") {
            return fallback || "Unknown";
        }
        if (token === "paired_and_connected") {
            return "Connected";
        }
        return prettyLabel(token);
    }

    function firstMeaningfulText() {
        for (var index = 0; index < arguments.length; index += 1) {
            var value = String(arguments[index] || "").trim();
            if (value && value !== "-") {
                return value;
            }
        }
        return "";
    }

    function clearTotpCodeTimer() {
        if (liveTimers.totpCode) {
            window.clearInterval(liveTimers.totpCode);
            liveTimers.totpCode = null;
        }
    }

    function normalizeStatusToken(value) {
        return String(value || "").trim().toLowerCase();
    }

    function buildSignalStatusSummary() {
        var bootstrapSignal = getByPath(state.bootstrap, "status.signal", {});
        var signalStatus = state.operations.signalStatus || bootstrapSignal;
        var signalDetail = getByPath(state.operations, "signalDetail.status", state.operations.signalDetail || {});
        var rawStatus = normalizeStatusToken(getByPath(signalStatus, "status", getByPath(bootstrapSignal, "status", "")));
        var integrationState = normalizeStatusToken(getByPath(signalDetail, "integration_state", getByPath(signalStatus, "integration_state", "")));
        var paired = Boolean(getByPath(signalDetail, "paired", getByPath(signalStatus, "paired", false)));
        var phoneNumber = String(getByPath(signalDetail, "paired_phone_number", getByPath(signalStatus, "phone_number", "")) || "").trim();
        var containerRunning = getByPath(signalDetail, "container_running", null);
        if (containerRunning == null) {
            containerRunning = rawStatus === "running";
        } else {
            containerRunning = Boolean(containerRunning);
        }
        var displayStatus = rawStatus || normalizeStatusToken(getByPath(bootstrapSignal, "status", ""));
        if (paired && containerRunning) {
            displayStatus = "paired_and_connected";
        } else if (paired) {
            displayStatus = "paired";
        } else if (integrationState && integrationState !== "disconnected") {
            displayStatus = integrationState;
        }
        return {
            status: signalStatus,
            detail: signalDetail,
            rawStatus: rawStatus,
            displayStatus: displayStatus || "unknown",
            integrationState: integrationState || "",
            paired: paired,
            phoneNumber: phoneNumber,
            containerRunning: containerRunning,
            registeredNumbers: getByPath(signalStatus, "registered_numbers", []),
            accounts: getByPath(signalStatus, "accounts", [])
        };
    }

    function buildWhatsAppStatusSummary() {
        var bootstrapWhatsapp = getByPath(state.bootstrap, "status.whatsapp", {});
        var whatsappStatus = state.operations.whatsappStatus || bootstrapWhatsapp;
        var nodeStatusSnapshot = getByPath(whatsappStatus, "node_status_snapshot", {});
        var enabled = asBoolean(getByPath(state.forms, "messaging.whatsapp.enabled", getByPath(bootstrapConfig(), "whatsapp.enabled", getByPath(whatsappStatus, "enabled", getByPath(bootstrapWhatsapp, "enabled", false)))), false);
        var status = normalizeStatusToken(getByPath(whatsappStatus, "status", getByPath(bootstrapWhatsapp, "status", "")));
        return {
            status: status,
            displayStatus: enabled ? (status || "unknown") : "disabled",
            enabled: enabled,
            paired: Boolean(getByPath(whatsappStatus, "paired", false)),
            ready: Boolean(getByPath(whatsappStatus, "ready", false)),
            phoneNumber: String(getByPath(whatsappStatus, "phone_number", "") || "").trim(),
            clientState: String(getByPath(whatsappStatus, "client_state", "") || "").trim().toUpperCase(),
            nodeProcessRunning: Boolean(getByPath(whatsappStatus, "node_process_running", false)),
            websocketConnected: Boolean(getByPath(whatsappStatus, "websocket_connected", false)),
            snapshotReady: Boolean(getByPath(nodeStatusSnapshot, "ready", false)),
            snapshotStatus: normalizeStatusToken(getByPath(nodeStatusSnapshot, "current_service_status", "")),
            snapshotPhoneNumber: String(getByPath(nodeStatusSnapshot, "phone_number", "") || "").trim()
        };
    }

    function buildTelegramUserStatusSummary() {
        var bootstrapTelegramUser = getByPath(state.bootstrap, "status.telegram_user", {});
        var telegramUserStatus = state.operations.telegramUserStatus || bootstrapTelegramUser;
        var enabled = asBoolean(
            getByPath(
                state.forms,
                "messaging.telegramUser.enabled",
                getByPath(bootstrapConfig(), "telegram_user.enabled", getByPath(telegramUserStatus, "enabled", false))
            ),
            false
        );
        var rawStatus = normalizeStatusToken(getByPath(telegramUserStatus, "status", getByPath(bootstrapTelegramUser, "status", "")));
        var connected = Boolean(
            getByPath(telegramUserStatus, "connected", false) ||
            getByPath(telegramUserStatus, "authorized", false) ||
            getByPath(telegramUserStatus, "ready", false) ||
            ["connected", "ready", "authorized"].indexOf(rawStatus) !== -1
        );
        var needsPassword = Boolean(getByPath(telegramUserStatus, "needs_password", false) || rawStatus === "needs_password");
        var displayStatus = connected ? "connected" : (needsPassword ? "needs_password" : (rawStatus || (enabled ? "waiting" : "disabled")));
        if (displayStatus === "awaiting_qr") {
            // The QR code only appears inside the Connect dialog, so the idle
            // status reads as an instruction instead of "Awaiting Qr".
            displayStatus = "ready_to_sign_in";
        }
        return {
            status: telegramUserStatus,
            enabled: enabled,
            connected: connected,
            needsPassword: needsPassword,
            displayStatus: displayStatus
        };
    }

    function messagingDeviceNameLockHint() {
        return "Device name cannot be edited once connected. Reset session, update device name, save, and then re-pair to get a different name.";
    }

    function isSignalDeviceNameLocked(signalSummary) {
        var summary = signalSummary || buildSignalStatusSummary();
        return Boolean(
            summary.paired ||
            summary.phoneNumber ||
            summary.displayStatus === "paired" ||
            summary.displayStatus === "paired_and_connected" ||
            summary.displayStatus === "connected" ||
            (Array.isArray(summary.registeredNumbers) && summary.registeredNumbers.length) ||
            (Array.isArray(summary.accounts) && summary.accounts.length)
        );
    }

    function isWhatsAppDeviceNameLocked(whatsappSummary) {
        var summary = whatsappSummary || buildWhatsAppStatusSummary();
        return Boolean(
            summary.paired ||
            summary.ready ||
            summary.phoneNumber ||
            summary.snapshotReady ||
            summary.snapshotPhoneNumber ||
            summary.status === "connected" ||
            summary.snapshotStatus === "connected" ||
            summary.clientState === "READY"
        );
    }

    function isPairingQrModal(modalObj) {
        return Boolean(
            modalObj &&
            modalObj.kind === "pairing-qr" &&
            (modalObj.platform === "signal" || modalObj.platform === "whatsapp" || modalObj.platform === "telegram_user")
        );
    }

    function pairingQrCompleted(modalObj) {
        if (!isPairingQrModal(modalObj)) {
            return false;
        }
        if (modalObj.platform === "signal") {
            var signalSummary = buildSignalStatusSummary();
            return Boolean(
                signalSummary.paired ||
                signalSummary.phoneNumber ||
                signalSummary.displayStatus === "paired" ||
                signalSummary.displayStatus === "paired_and_connected" ||
                signalSummary.displayStatus === "connected" ||
                signalSummary.displayStatus === "registered_no_client" ||
                (Array.isArray(signalSummary.registeredNumbers) && signalSummary.registeredNumbers.length) ||
                (Array.isArray(signalSummary.accounts) && signalSummary.accounts.length)
            );
        }
        if (modalObj.platform === "telegram_user") {
            return buildTelegramUserStatusSummary().connected;
        }
        var whatsappSummary = buildWhatsAppStatusSummary();
        return Boolean(
            whatsappSummary.paired ||
            whatsappSummary.ready ||
            whatsappSummary.phoneNumber ||
            whatsappSummary.snapshotReady ||
            whatsappSummary.snapshotPhoneNumber ||
            whatsappSummary.status === "connected" ||
            whatsappSummary.snapshotStatus === "connected" ||
            ["READY", "CONNECTED"].indexOf(whatsappSummary.clientState) !== -1
        );
    }

    async function refreshPairingQrModal(force) {
        if (!isPairingQrModal(state.modal)) {
            return;
        }
        var modalObj = state.modal;
        var now = Date.now();
        var isSignal = modalObj.platform === "signal";
        if (!force && isSignal && modalObj.imageUrl) {
            // Signal linking URIs are valid for ~60 seconds. Calling /api/signal/qr
            // spawns a fresh signal-cli link session, replacing the current session and
            // changing the QR code. Keep the active QR stable until it approaches expiration (45s).
            var fetchedAt = modalObj.fetchedAt || 0;
            if (now - fetchedAt < 45000) {
                return;
            }
        }
        if (!force && isSignal && !modalObj.imageUrl) {
            var lastAttempt = modalObj.lastAttempt || 0;
            if (now - lastAttempt < 3000) {
                return;
            }
            modalObj.lastAttempt = now;
        }
        var endpoint = isSignal
            ? (force ? "/api/signal/qr?refresh=true" : "/api/signal/qr")
            : (modalObj.platform === "telegram_user" ? "/api/telegram-user/qr" : "/api/whatsapp/qr");
        var response = await safeRequestJson(endpoint);
        if (!isPairingQrModal(state.modal) || state.modal.platform !== modalObj.platform) {
            return;
        }
        var payload = response.ok ? (response.payload || {}) : {};
        var completionText = String(response.error || getByPath(payload, "error", "") || "").toLowerCase();
        if (modalObj.platform === "telegram_user" && Boolean(getByPath(payload, "needs_password", false))) {
            setModal(telegramUserPasswordModal());
            return;
        }
        if (
            Boolean(getByPath(payload, "connected", false)) ||
            Boolean(getByPath(payload, "paired", false)) ||
            Boolean(getByPath(payload, "ready", false)) ||
            normalizeStatusToken(getByPath(payload, "status", "")) === "connected" ||
            completionText.indexOf("already paired") !== -1
        ) {
            await ensureOperationsData(true, { passive: true }).catch(function () {});
            setModal(null);
            return;
        }
        var nextImageUrl = String(getByPath(payload, "qr_url", "") || "").trim();
        var nextHelper = String(
            getByPath(payload, "error", "") ||
            getByPath(payload, "device_name", "") ||
            state.modal.helper ||
            ""
        ).trim();
        var changed = false;
        if (nextImageUrl && state.modal.imageUrl !== nextImageUrl) {
            state.modal.imageUrl = nextImageUrl;
            state.modal.fetchedAt = Date.now();
            changed = true;
        }
        if (response.ok && !nextImageUrl && state.modal.imageUrl) {
            state.modal.imageUrl = "";
            changed = true;
        }
        if (nextHelper && state.modal.helper !== nextHelper) {
            state.modal.helper = nextHelper;
            changed = true;
        }
        if (!response.ok && nextHelper && state.modal.description !== nextHelper) {
            state.modal.description = nextHelper;
            changed = true;
        }
        if (changed) {
            renderApp();
        }
    }

    function stopPairingModalMonitor() {
        if (!liveIntervals.pairingModal) {
            return;
        }
        window.clearInterval(liveIntervals.pairingModal);
        liveIntervals.pairingModal = null;
    }

    function startPairingModalMonitor() {
        if (liveIntervals.pairingModal) {
            return;
        }
        var tick = function () {
            Promise.all([
                ensureOperationsData(true, { passive: true }).catch(function () {}),
                refreshPairingQrModal().catch(function () {})
            ]).catch(function () {}).then(function () {
                if (pairingQrCompleted(state.modal)) {
                    setModal(null);
                }
            });
        };
        liveIntervals.pairingModal = window.setInterval(tick, 1500);
        tick();
    }

    function syncPairingModalMonitor() {
        if (isPairingQrModal(state.modal) && pairingQrCompleted(state.modal)) {
            setModal(null);
            return;
        }
        if (isPairingQrModal(state.modal)) {
            startPairingModalMonitor();
            return;
        }
        stopPairingModalMonitor();
    }

    function telegramUserPasswordModal() {
        return {
            kind: "telegram-user-password",
            title: "Finish Telegram User connection",
            description: "Enter the password that protects your Telegram account to finish connecting it.",
            html: field("Telegram password", input("messaging.telegramUser.password", { type: "password", placeholder: "Telegram password" }), "This is sent only to finish this connection.") + "<div class=\"ayu-inline-actions\">" + button("Finish connecting", "telegram-user-2fa", "primary", "key") + "</div>"
        };
    }

    function openTelegramUserQrModal(payload) {
        var result = payload || {};
        if (Boolean(getByPath(result, "needs_password", false))) {
            setModal(telegramUserPasswordModal());
            return;
        }
        setModal({
            kind: "pairing-qr",
            platform: "telegram_user",
            title: "Connect Telegram User",
            description: "Scan this QR with the Telegram account you own. AutoYou uses only your Saved Messages.",
            imageUrl: getByPath(result, "qr_url", ""),
            fetchedAt: Date.now(),
            helper: getByPath(result, "error", "") || "Waiting for Telegram to finish connecting."
        });
    }

    function ensureCurrentOption(options, value, label) {
        var normalized = String(value == null ? "" : value);
        var list = Array.isArray(options) ? options.slice() : [];
        if (!normalized) {
            return list;
        }
        var exists = list.some(function (option) {
            return String(option.value) === normalized;
        });
        if (!exists) {
            list.push({ value: normalized, label: label || normalized });
        }
        return list;
    }

    function buildSimpleOptions(values, currentValue, formatter) {
        var seen = {};
        var options = [];
        (values || []).forEach(function (value) {
            var normalized = String(value == null ? "" : value).trim();
            if (!normalized || seen[normalized]) {
                return;
            }
            seen[normalized] = true;
            options.push({ value: normalized, label: formatter ? formatter(normalized) : normalized });
        });
        return ensureCurrentOption(options, currentValue, currentValue ? (formatter ? formatter(currentValue) : currentValue) : "");
    }

    function buildSystemVoiceOptions() {
        var currentVoice = getByPath(state.forms, "speech.system_voice", "");
        var voices = Array.isArray(getByPath(state.speechLibrary, "status.system_voices", [])) ? getByPath(state.speechLibrary, "status.system_voices", []) : [];
        var seen = {};
        var options = [{ value: "", label: "Default system voice" }];
        voices.forEach(function (voice) {
            var voiceId = String(getByPath(voice, "id", "")).trim();
            if (!voiceId || seen[voiceId]) {
                return;
            }
            seen[voiceId] = true;
            var parts = [];
            if (getByPath(voice, "languages", "")) {
                parts.push(getByPath(voice, "languages", ""));
            }
            if (getByPath(voice, "gender", "")) {
                parts.push(getByPath(voice, "gender", ""));
            }
            var label = String(getByPath(voice, "name", voiceId) || voiceId);
            if (parts.length) {
                label += " (" + parts.join(" | ") + ")";
            }
            options.push({ value: voiceId, label: label });
        });
        return ensureCurrentOption(options, currentVoice, currentVoice ? ("Current custom voice: " + currentVoice) : "");
    }

    function buildSpeechModelOptions() {
        var currentModel = getByPath(state.forms, "speech.stt_model", "tiny.en");
        var statusModels = Array.isArray(getByPath(state.speechLibrary, "status.models", [])) ? getByPath(state.speechLibrary, "status.models", []) : [];
        var suggestions = Array.isArray(getByPath(state.speechLibrary, "status.stt_model_suggestions", [])) ? getByPath(state.speechLibrary, "status.stt_model_suggestions", []) : [];
        var options = [];
        var seen = {};
        statusModels.forEach(function (entry) {
            var modelName = String(getByPath(entry, "model", entry) || "").trim();
            if (!modelName || seen[modelName]) {
                return;
            }
            seen[modelName] = true;
            var label = String(getByPath(entry, "label", modelName) || modelName);
            if (getByPath(entry, "installed", false)) {
                label += " (installed)";
            }
            options.push({ value: modelName, label: label });
        });
        suggestions.forEach(function (entry) {
            var modelName = String(entry || "").trim();
            if (!modelName || seen[modelName]) {
                return;
            }
            seen[modelName] = true;
            options.push({ value: modelName, label: modelName });
        });
        return ensureCurrentOption(options, currentModel, currentModel || "tiny.en");
    }

    function catalogDetailKey(source, identifier) {
        return String(source || "ollama") + "::" + String(identifier || "");
    }

    function activeJobList(payload) {
        return Array.isArray(getByPath(payload, "jobs", [])) ? getByPath(payload, "jobs", []) : [];
    }

    function hasActiveJobs(payload) {
        return activeJobList(payload).some(function (job) {
            var status = String(getByPath(job, "status", "")).toLowerCase();
            return status === "queued" || status === "running";
        });
    }

    function localModelNames() {
        var models = Array.isArray(getByPath(state.aiLibrary, "local.models", [])) ? getByPath(state.aiLibrary, "local.models", []) : [];
        return models.map(function (model) {
            return String(model.name || model.model || model.digest || "");
        }).filter(Boolean);
    }

    function modelAvailableLocally(reference) {
        var requested = String(reference || "").replace(/^(ollama_chat|ollama|ollama_local|ollama-local)\//i, "").toLowerCase();
        if (!requested) {
            return false;
        }
        return localModelNames().some(function (candidate) {
            var normalized = String(candidate || "").replace(/^(ollama_chat|ollama|ollama_local|ollama-local)\//i, "").toLowerCase();
            return normalized === requested || (requested.indexOf(":") === -1 && normalized.split(":")[0] === requested);
        });
    }

    function normalizeBehaviorValue(value, fieldName) {
        if (!hasValue(value)) {
            return "";
        }
        if (fieldName === "num_ctx") {
            return String(parseInt(value, 10));
        }
        return String(Number(value));
    }

    function syncModelBehaviorFormFromResponse(payload) {
        if (!payload) {
            return;
        }
        var mode = String(getByPath(payload, "mode", getByPath(state.forms, "modelBehavior.mode", "accurate")) || "accurate");
        setByPath(state.forms, "modelBehavior.mode", mode);
        ["temperature", "top_p", "top_k", "repeat_penalty"].forEach(function (fieldName) {
            var overrideValue = getByPath(payload, "advanced_overrides." + fieldName, null);
            var presetValue = getByPath(payload, "modes." + mode + ".params." + fieldName, getByPath(payload, "preset_params." + fieldName, ""));
            var effectiveValue = hasValue(overrideValue) ? overrideValue : presetValue;
            setByPath(state.forms, "modelBehavior." + fieldName, hasValue(effectiveValue) ? String(effectiveValue) : "");
        });
        var numCtxOverride = getByPath(payload, "advanced_overrides.num_ctx", null);
        setByPath(state.forms, "modelBehavior.num_ctx", hasValue(numCtxOverride) ? String(numCtxOverride) : "");
        setByPath(state.forms, "modelBehavior.show_thinking", Boolean(getByPath(payload, "show_thinking", false)));
        setByPath(state.forms, "modelBehavior.thinking_level", getByPath(payload, "thinking_level", "") || "");
    }

    function applySelectedOllamaModel(payload, fallbackModel) {
        var selectedModel = String(getByPath(payload, "selected_model", getByPath(payload, "model", fallbackModel || "")) || "").trim();
        if (!selectedModel) {
            return;
        }
        if (state.forms) {
            setByPath(state.forms, "aiProvider.ollama_model", selectedModel);
        }
        if (state.bootstrap) {
            setByPath(state.bootstrap, "config.ollama.model", selectedModel);
        }
        if (state.aiLibrary) {
            setByPath(state.aiLibrary, "local.selected_model", selectedModel);
        }
    }

    function buildModelBehaviorPayload() {
        var source = getByPath(state.forms, "modelBehavior", {});
        var mode = String(source.mode || getByPath(state.aiLibrary, "behavior.mode", "accurate") || "accurate");
        var presetParams = getByPath(state.aiLibrary, "behavior.modes." + mode + ".params", {});
        var payload = { mode: mode };
        ["temperature", "top_p", "top_k", "repeat_penalty"].forEach(function (fieldName) {
            var rawValue = getByPath(source, fieldName, "");
            if (!hasValue(rawValue)) {
                payload[fieldName] = "";
                return;
            }
            var normalizedCurrent = normalizeBehaviorValue(rawValue, fieldName);
            var normalizedPreset = normalizeBehaviorValue(getByPath(presetParams, fieldName, ""), fieldName);
            payload[fieldName] = normalizedCurrent === normalizedPreset ? "" : rawValue;
        });
        payload.num_ctx = hasValue(getByPath(source, "num_ctx", "")) ? getByPath(source, "num_ctx", "") : "";
        payload.show_thinking = Boolean(getByPath(source, "show_thinking", false));
        payload.thinking_level = getByPath(source, "thinking_level", "") || "";
        return payload;
    }

    function renderProgressTrack(percent, tone) {
        var safePercent = Math.max(0, Math.min(100, Number(percent) || 0));
        return "<div class=\"ayu-progress\"><div class=\"ayu-progress-fill" + (tone ? " " + escapeHtml(tone) : "") + "\" style=\"width:" + escapeHtml(String(safePercent)) + "%\"></div></div>";
    }

    function joinList(values) {
        if (!Array.isArray(values) || values.length === 0) {
            return "";
        }
        return values.join(", ");
    }

    function splitListValue(value) {
        if (Array.isArray(value)) {
            return value.map(function (item) {
                return String(item == null ? "" : item).trim();
            }).filter(Boolean);
        }
        return String(value == null ? "" : value).split(/[,\s]+/).map(function (item) {
            return item.trim();
        }).filter(Boolean);
    }

    function hasValue(value) {
        if (Array.isArray(value)) {
            return value.length > 0;
        }
        return value != null && String(value).trim() !== "";
    }

    function valueAttr(value) {
        return escapeHtml(value == null ? "" : value);
    }

    var PASSWORD_GENERATOR_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz23456789!@#$%*-_=+?";

    function generateRandomPassword(length) {
        var size = Math.max(16, Math.min(128, Number(length) || 24));
        var values = new Uint32Array(size);
        if (window.crypto && window.crypto.getRandomValues) {
            window.crypto.getRandomValues(values);
        } else {
            for (var fallbackIndex = 0; fallbackIndex < size; fallbackIndex += 1) {
                values[fallbackIndex] = Math.floor(Math.random() * 4294967296);
            }
        }
        var password = "";
        for (var index = 0; index < size; index += 1) {
            password += PASSWORD_GENERATOR_ALPHABET[values[index] % PASSWORD_GENERATOR_ALPHABET.length];
        }
        return password;
    }

    function setSecurityPasswordDraft(password) {
        setByPath(state.forms, "security.new_password", password || "");
        setByPath(state.forms, "security.confirm_password", password || "");
    }

    function hasSecurityPasswordDraft() {
        return hasValue(getByPath(state.forms, "security.new_password", "")) || hasValue(getByPath(state.forms, "security.confirm_password", ""));
    }

    function input(path, options) {
        var type = options && options.type ? options.type : "text";
        var placeholder = options && options.placeholder ? options.placeholder : "";
        var extraAttrs = options && options.extraAttrs ? options.extraAttrs : "";
        var value = getByPath(state.forms, path, "");
        var secretAttrs = type === "password" ? " autocomplete=\"new-password\"" : "";
        return "<input class=\"ayu-input\" data-bind=\"" + escapeHtml(path) + "\" type=\"" + escapeHtml(type) + "\" value=\"" + valueAttr(value) + "\" placeholder=\"" + escapeHtml(placeholder) + "\"" + secretAttrs + " " + extraAttrs + ">";
    }

    function passwordInput(path, placeholder) {
        var value = getByPath(state.forms, path, "");
        var visible = state.securityPasswordVisible && hasSecurityPasswordDraft();
        return "<input class=\"ayu-input\" data-bind=\"" + escapeHtml(path) + "\" type=\"" + (visible ? "text" : "password") + "\" value=\"" + valueAttr(value) + "\" placeholder=\"" + escapeHtml(placeholder || "") + "\" autocomplete=\"new-password\">";
    }

    function selectSecurityPasswordDraft() {
        var passwordField = root ? root.querySelector('[data-bind="security.new_password"]') : null;
        if (!passwordField || typeof passwordField.focus !== "function" || typeof passwordField.select !== "function") {
            return false;
        }
        passwordField.focus();
        passwordField.select();
        return true;
    }

    function renderPasswordDraftFields() {
        return "<div class=\"ayu-grid-2\">" + field("New password", passwordInput("security.new_password", "New password")) + field("Confirm password", passwordInput("security.confirm_password", "Repeat the new password")) + "</div>";
    }

    function renderPasswordGeneratorTools() {
        var visible = state.securityPasswordVisible && hasSecurityPasswordDraft();
        return "<div class=\"ayu-inline-actions ayu-password-actions\">" + button("Generate password", "security-generate-password", "secondary", "key", "sm") + button(visible ? "Hide password" : "Show password", "security-toggle-password", "ghost", visible ? "eyeOff" : "eye", "sm") + button("Copy password", "security-copy-password", "ghost", "copy", "sm") + "</div>";
    }

    function textarea(path, options) {
        var rows = options && options.rows ? options.rows : 6;
        var placeholder = options && options.placeholder ? options.placeholder : "";
        var extraClass = options && options.extraClass ? options.extraClass : "";
        var extraAttrs = options && options.extraAttrs ? options.extraAttrs : "";
        return "<textarea class=\"ayu-textarea " + escapeHtml(extraClass) + "\" data-bind=\"" + escapeHtml(path) + "\" rows=\"" + escapeHtml(rows) + "\" placeholder=\"" + escapeHtml(placeholder) + "\" " + extraAttrs + ">" + escapeHtml(getByPath(state.forms, path, "")) + "</textarea>";
    }

    function select(path, options, extraAttrs) {
        var current = String(getByPath(state.forms, path, ""));
        var markup = (options || []).map(function (item) {
            var value = typeof item === "string" ? item : item.value;
            var label = typeof item === "string" ? item : item.label;
            var disabled = typeof item === "string" ? false : Boolean(item.disabled);
            return "<option value=\"" + escapeHtml(value) + "\"" + (String(value) === current ? " selected" : "") + (disabled ? " disabled" : "") + ">" + escapeHtml(label) + "</option>";
        }).join("");
        return "<select class=\"ayu-select\" data-bind=\"" + escapeHtml(path) + "\"" + (extraAttrs ? " " + extraAttrs : "") + ">" + markup + "</select>";
    }

    function checkbox(path, label, hint, extraAttrs) {
        var riskNotes = {
            "connectivity.tunnelmole.enabled": "<div class=\"ayu-note ayu-note-red\"><strong>Public exposure risk</strong><p>The public link makes Websites & Browser and pairing reachable from the internet. Use Secure Professional, authenticator pair-code mode, URL-only sharing, and a timed lifetime. Never expose the admin page publicly.</p></div>",
            "aiAgent.lan_access_enabled": "<div class=\"ayu-note ayu-note-red\"><strong>Home network exposure risk</strong><p>This exposes the AI Agent runtime (it can create/read chat sessions, browse the internet, and run scheduled tasks) to other devices on your local network, over a dedicated HTTPS port protected by a one-time code. The local dev-ui port (8081) stays loopback-only either way. Only enable this on a trusted network.</p></div>",
        };
        var riskNote = riskNotes[path] || "";
        return "<label class=\"ayu-checkbox\"><input type=\"checkbox\" data-bind=\"" + escapeHtml(path) + "\"" + (getByPath(state.forms, path, false) ? " checked" : "") + (extraAttrs ? " " + extraAttrs : "") + "><div><span>" + escapeHtml(label) + "</span>" + (hint ? "<small>" + escapeHtml(hint) + "</small>" : "") + "</div></label>" + riskNote;
    }

    function modelKey(name) {
        return String(name || "").replace(/[^a-zA-Z0-9_.-]/g, "_");
    }

    function activeJobList(payload) {
        return Array.isArray(getByPath(payload, "jobs", [])) ? getByPath(payload, "jobs", []) : [];
    }

    function hasActiveJobs(payload) {
        return activeJobList(payload).some(function (job) {
            var status = String(getByPath(job, "status", "")).toLowerCase();
            return status === "queued" || status === "running";
        });
    }

    function localModelNames() {
        var models = Array.isArray(getByPath(state.aiLibrary, "local.models", [])) ? getByPath(state.aiLibrary, "local.models", []) : [];
        return models.map(function (model) {
            return String(model.name || model.model || model.digest || "");
        }).filter(Boolean);
    }

    function modelAvailableLocally(reference) {
        var requested = String(reference || "").replace(/^(ollama_chat|ollama|ollama_local|ollama-local)\//i, "").toLowerCase();
        if (!requested) {
            return false;
        }
        return localModelNames().some(function (candidate) {
            var normalized = String(candidate || "").replace(/^(ollama_chat|ollama|ollama_local|ollama-local)\//i, "").toLowerCase();
            return normalized === requested || (requested.indexOf(":") === -1 && normalized.split(":")[0] === requested);
        });
    }

    function normalizeBehaviorValue(value, fieldName) {
        if (!hasValue(value)) {
            return "";
        }
        if (fieldName === "num_ctx") {
            return String(parseInt(value, 10));
        }
        return String(Number(value));
    }

    function syncModelBehaviorFormFromResponse(payload) {
        if (!payload) {
            return;
        }
        var mode = String(getByPath(payload, "mode", getByPath(state.forms, "modelBehavior.mode", "accurate")) || "accurate");
        setByPath(state.forms, "modelBehavior.mode", mode);
        ["temperature", "top_p", "top_k", "repeat_penalty"].forEach(function (fieldName) {
            var overrideValue = getByPath(payload, "advanced_overrides." + fieldName, null);
            var presetValue = getByPath(payload, "modes." + mode + ".params." + fieldName, getByPath(payload, "preset_params." + fieldName, ""));
            var effectiveValue = hasValue(overrideValue) ? overrideValue : presetValue;
            setByPath(state.forms, "modelBehavior." + fieldName, hasValue(effectiveValue) ? String(effectiveValue) : "");
        });
        var numCtxOverride = getByPath(payload, "advanced_overrides.num_ctx", null);
        setByPath(state.forms, "modelBehavior.num_ctx", hasValue(numCtxOverride) ? String(numCtxOverride) : "");
        setByPath(state.forms, "modelBehavior.show_thinking", Boolean(getByPath(payload, "show_thinking", false)));
        setByPath(state.forms, "modelBehavior.thinking_level", getByPath(payload, "thinking_level", "") || "");
    }

    function buildModelBehaviorPayload() {
        var source = getByPath(state.forms, "modelBehavior", {});
        var mode = String(source.mode || getByPath(state.aiLibrary, "behavior.mode", "accurate") || "accurate");
        var presetParams = getByPath(state.aiLibrary, "behavior.modes." + mode + ".params", {});
        var payload = { mode: mode };
        ["temperature", "top_p", "top_k", "repeat_penalty"].forEach(function (fieldName) {
            var rawValue = getByPath(source, fieldName, "");
            if (!hasValue(rawValue)) {
                payload[fieldName] = "";
                return;
            }
            var normalizedCurrent = normalizeBehaviorValue(rawValue, fieldName);
            var normalizedPreset = normalizeBehaviorValue(getByPath(presetParams, fieldName, ""), fieldName);
            payload[fieldName] = normalizedCurrent === normalizedPreset ? "" : rawValue;
        });
        payload.num_ctx = hasValue(getByPath(source, "num_ctx", "")) ? getByPath(source, "num_ctx", "") : "";
        payload.show_thinking = Boolean(getByPath(source, "show_thinking", false));
        payload.thinking_level = getByPath(source, "thinking_level", "") || "";
        return payload;
    }

    function renderProgressTrack(percent, tone) {
        var safePercent = Math.max(0, Math.min(100, Number(percent) || 0));
        return "<div class=\"ayu-progress\"><div class=\"ayu-progress-fill" + (tone ? " " + escapeHtml(tone) : "") + "\" style=\"width:" + escapeHtml(String(safePercent)) + "%\"></div></div>";
    }

    function joinList(values) {
        if (!Array.isArray(values) || values.length === 0) {
            return "";
        }
        return values.join(", ");
    }

    function splitListValue(value) {
        if (Array.isArray(value)) {
            return value.map(function (item) {
                return String(item == null ? "" : item).trim();
            }).filter(Boolean);
        }
        return String(value == null ? "" : value).split(/[,\s]+/).map(function (item) {
            return item.trim();
        }).filter(Boolean);
    }

    function hasValue(value) {
        if (Array.isArray(value)) {
            return value.length > 0;
        }
        return value != null && String(value).trim() !== "";
    }

    function valueAttr(value) {
        return escapeHtml(value == null ? "" : value);
    }

    var PASSWORD_GENERATOR_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz23456789!@#$%*-_=+?";

    function generateRandomPassword(length) {
        var size = Math.max(16, Math.min(128, Number(length) || 24));
        var values = new Uint32Array(size);
        if (window.crypto && window.crypto.getRandomValues) {
            window.crypto.getRandomValues(values);
        } else {
            for (var fallbackIndex = 0; fallbackIndex < size; fallbackIndex += 1) {
                values[fallbackIndex] = Math.floor(Math.random() * 4294967296);
            }
        }
        var password = "";
        for (var index = 0; index < size; index += 1) {
            password += PASSWORD_GENERATOR_ALPHABET[values[index] % PASSWORD_GENERATOR_ALPHABET.length];
        }
        return password;
    }

    function setSecurityPasswordDraft(password) {
        setByPath(state.forms, "security.new_password", password || "");
        setByPath(state.forms, "security.confirm_password", password || "");
    }

    function hasSecurityPasswordDraft() {
        return hasValue(getByPath(state.forms, "security.new_password", "")) || hasValue(getByPath(state.forms, "security.confirm_password", ""));
    }

    function input(path, options) {
        var type = options && options.type ? options.type : "text";
        var placeholder = options && options.placeholder ? options.placeholder : "";
        var extraAttrs = options && options.extraAttrs ? options.extraAttrs : "";
        var value = getByPath(state.forms, path, "");
        var secretAttrs = type === "password" ? " autocomplete=\"new-password\"" : "";
        return "<input class=\"ayu-input\" data-bind=\"" + escapeHtml(path) + "\" type=\"" + escapeHtml(type) + "\" value=\"" + valueAttr(value) + "\" placeholder=\"" + escapeHtml(placeholder) + "\"" + secretAttrs + " " + extraAttrs + ">";
    }

    function passwordInput(path, placeholder) {
        var value = getByPath(state.forms, path, "");
        var visible = state.securityPasswordVisible && hasSecurityPasswordDraft();
        return "<input class=\"ayu-input\" data-bind=\"" + escapeHtml(path) + "\" type=\"" + (visible ? "text" : "password") + "\" value=\"" + valueAttr(value) + "\" placeholder=\"" + escapeHtml(placeholder || "") + "\" autocomplete=\"new-password\">";
    }

    function selectSecurityPasswordDraft() {
        var passwordField = root ? root.querySelector('[data-bind="security.new_password"]') : null;
        if (!passwordField || typeof passwordField.focus !== "function" || typeof passwordField.select !== "function") {
            return false;
        }
        passwordField.focus();
        passwordField.select();
        return true;
    }

    function renderPasswordDraftFields() {
        return "<div class=\"ayu-grid-2\">" + field("New password", passwordInput("security.new_password", "New password")) + field("Confirm password", passwordInput("security.confirm_password", "Repeat the new password")) + "</div>";
    }

    function renderPasswordGeneratorTools() {
        var visible = state.securityPasswordVisible && hasSecurityPasswordDraft();
        return "<div class=\"ayu-inline-actions ayu-password-actions\">" + button("Generate password", "security-generate-password", "secondary", "key", "sm") + button(visible ? "Hide password" : "Show password", "security-toggle-password", "ghost", visible ? "eyeOff" : "eye", "sm") + button("Copy password", "security-copy-password", "ghost", "copy", "sm") + "</div>";
    }

    function textarea(path, options) {
        var rows = options && options.rows ? options.rows : 6;
        var placeholder = options && options.placeholder ? options.placeholder : "";
        var extraClass = options && options.extraClass ? options.extraClass : "";
        var extraAttrs = options && options.extraAttrs ? options.extraAttrs : "";
        return "<textarea class=\"ayu-textarea " + escapeHtml(extraClass) + "\" data-bind=\"" + escapeHtml(path) + "\" rows=\"" + escapeHtml(rows) + "\" placeholder=\"" + escapeHtml(placeholder) + "\" " + extraAttrs + ">" + escapeHtml(getByPath(state.forms, path, "")) + "</textarea>";
    }

    function select(path, options, extraAttrs) {
        var current = String(getByPath(state.forms, path, ""));
        var markup = (options || []).map(function (item) {
            var value = typeof item === "string" ? item : item.value;
            var label = typeof item === "string" ? item : item.label;
            var disabled = typeof item === "string" ? false : Boolean(item.disabled);
            return "<option value=\"" + escapeHtml(value) + "\"" + (String(value) === current ? " selected" : "") + (disabled ? " disabled" : "") + ">" + escapeHtml(label) + "</option>";
        }).join("");
        return "<select class=\"ayu-select\" data-bind=\"" + escapeHtml(path) + "\"" + (extraAttrs ? " " + extraAttrs : "") + ">" + markup + "</select>";
    }

    function checkbox(path, label, hint, extraAttrs) {
        var riskNotes = {
            "connectivity.tunnelmole.enabled": "<div class=\"ayu-note ayu-note-red\"><strong>Public exposure risk</strong><p>The public link makes Websites & Browser and pairing reachable from the internet. Use Secure Professional, authenticator pair-code mode, URL-only sharing, and a timed lifetime. Never expose the admin page publicly.</p></div>",
            "aiAgent.lan_access_enabled": "<div class=\"ayu-note ayu-note-red\"><strong>Home network exposure risk</strong><p>This exposes the AI Agent runtime (it can create/read chat sessions, browse the internet, and run scheduled tasks) to other devices on your local network, over a dedicated HTTPS port protected by a one-time code. The local dev-ui port (8081) stays loopback-only either way. Only enable this on a trusted network.</p></div>",
        };
        var riskNote = riskNotes[path] || "";
        return "<label class=\"ayu-checkbox\"><input type=\"checkbox\" data-bind=\"" + escapeHtml(path) + "\"" + (getByPath(state.forms, path, false) ? " checked" : "") + (extraAttrs ? " " + extraAttrs : "") + "><div><span>" + escapeHtml(label) + "</span>" + (hint ? "<small>" + escapeHtml(hint) + "</small>" : "") + "</div></label>" + riskNote;
    }

    function modelKey(name) {
        return String(name || "").replace(/[^a-zA-Z0-9_.-]/g, "_");
    }

    function setNotice(kind, message) {
        state.notice = { kind: kind || "info", message: String(message || "") };
        if (noticeTimer) {
            window.clearTimeout(noticeTimer);
        }
        noticeTimer = window.setTimeout(function () {
            "use strict";
            if (typeof document !== "undefined" && document.documentElement) {
                document.documentElement.dataset.autoyouScrollManaged = "1";
            }
            state.notice = null;
            noticeTimer = null;
            renderApp({ passive: true });
        }, 4200);
        renderApp();
    }

    function bootstrapConfig() {
        return getByPath(state.bootstrap, "config", {});
    }

    function displayServerId(cloud) {
        var admin = getByPath(state.bootstrap, "admin", {});
        var cfg = bootstrapConfig();
        return firstNonBlank([
            getByPath(cloud, "server_id", ""),
            getByPath(admin, "server_id", ""),
            getByPath(cfg, "server.installation_id", ""),
            getByPath(admin, "server_name", ""),
            getByPath(cfg, "server.name", "")
        ], "not linked");
    }

    function displayCloudEmail(cloud) {
        return firstNonBlank([getByPath(cloud, "email", "")], "Local-only admin session");
    }

    function onboardingCompleted() {
        var cfg = bootstrapConfig();
        var setupPayload = state.setup.payload || {};
        return Boolean(
            getByPath(setupPayload, "wizard_completed", false) ||
            getByPath(cfg, "onboarding.wizard_completed", false)
        );
    }

    function setModal(modalObj) {
        clearTotpCodeTimer();
        state.modal = modalObj || null;
        syncPairingModalMonitor();
        renderApp();
    }

    function field(label, control, hint) {
        return "<div class=\"ayu-field\"><div class=\"ayu-field-label\">" + escapeHtml(label) + "</div>" + control + (hint ? "<div class=\"ayu-field-hint\">" + escapeHtml(hint) + "</div>" : "") + "</div>";
    }

    var AI_PROVIDER_OPTIONS = [
        { id: "apple_intelligence", eyebrow: "On this Mac", label: "Apple Intelligence + Agents",
          description: "Use Apple Intelligence on this Mac with AutoYou agents and tools.", badgeLabel: "Local", badgeTone: "green" },
        {
            id: "ollama",
            eyebrow: "Local default",
            label: "Ollama + Agents",
            description: "Run AutoYou's own agent runtime against a local Ollama server. No API key and no cloud dependency.",
            badgeLabel: "Local",
            badgeTone: "green"
        },
        {
            id: "ollama_gateway",
            eyebrow: "Gateway-first mode",
            label: "Ollama (Native Gateway)",
            description: "Send chat directly to the saved Ollama server while keeping its model and behavior settings. AutoYou agents are not used for replies.",
            badgeLabel: "Gateway",
            badgeTone: "amber"
        },
        {
            id: "odysseus",
            eyebrow: "Gateway-first mode",
            label: "Odysseus",
            description: "Use the external Odysseus companion API and its own authenticated sessions and model endpoints.",
            badgeLabel: "Gateway",
            badgeTone: "amber"
        },
        {
            id: "google",
            eyebrow: "Gemini path",
            label: "Gemini + Agents",
            description: "Keep AutoYou agents active while the runtime calls Google's Gemini API for model responses.",
            badgeLabel: "Cloud",
            badgeTone: "blue"
        },
        {
            id: "litellm",
            eyebrow: "Provider routing",
            label: "Other AI Providers + Agents",
            description: "Use Anthropic, OpenAI, Mistral, DeepSeek, xAI, and other compatible providers while keeping Agent Studio and installed AutoYou agents active.",
            badgeLabel: "Cloud",
            badgeTone: "blue"
        },
        {
            id: "openclaw",
            eyebrow: "Gateway-first mode",
            label: "OpenClaw",
            description: "Use the local OpenClaw gateway and browser flow when you want OpenClaw's own session model to be primary.",
            badgeLabel: "Gateway",
            badgeTone: "amber"
        },
        {
            id: "hermes",
            eyebrow: "Gateway-first mode",
            label: "Hermes Agent",
            description: "Use the local Hermes Agent gateway when you want Hermes to own the primary session runtime and tool loop.",
            badgeLabel: "Gateway",
            badgeTone: "amber"
        }
    ];

    var TTS_PROVIDER_OPTIONS = [
        {
            id: "system",
            eyebrow: "Built-in voices",
            label: "System speech",
            description: "Use the operating system voice already installed on this machine.",
            badgeLabel: "Local",
            badgeTone: "green"
        },
        {
            id: "custom",
            eyebrow: "Local clone",
            label: "Custom cloned voice",
            description: "Use the local voice prepared by the Voice Training app.",
            badgeLabel: "Local",
            badgeTone: "purple"
        },
        {
            id: "emotivoice",
            eyebrow: "Local expressive voice",
            label: "EmotiVoice",
            description: "Chinese and English local synthesis with emotion steered by this conversation.",
            badgeLabel: "Local",
            badgeTone: "green"
        },
        {
            id: "openai",
            eyebrow: "Cloud TTS",
            label: "OpenAI speech",
            description: "Higher quality hosted TTS with model and voice selection.",
            badgeLabel: "Cloud",
            badgeTone: "blue"
        },
        {
            id: "azure",
            eyebrow: "Cloud TTS",
            label: "Azure speech",
            description: "Azure Speech voice synthesis with region, voice, and optional custom endpoint support.",
            badgeLabel: "Cloud",
            badgeTone: "blue"
        },
        {
            id: "off",
            eyebrow: "Disabled",
            label: "Speech off",
            description: "Disable text-to-speech while keeping speech-to-text controls available.",
            badgeLabel: "Muted",
            badgeTone: "gray"
        }
    ];

    var SETUP_WIZARD_STEPS = [
        { id: "name-theme", label: "Name & Theme", subtitle: "Name this server and pick the admin shell theme - the first thing clients, logs, and setup screens see." },
        { id: "security-basics", label: "Security & 2FA", subtitle: "Confirm the default password, Secure Mode, and the shared authenticator before the rest of setup." },
        { id: "ai-provider", label: "AI Provider", subtitle: "Pick the AI path that powers this machine." },
        { id: "manage-models", label: "Manage Models", subtitle: "Select or download the local model inventory used by Ollama." },
        { id: "messaging-partner", label: "Messaging Partner", subtitle: "Choose how phones and desktop clients reach the server when Cloud Pair is not in use." },
        { id: "connectivity", label: "Connectivity", subtitle: "Configure public links and connection helpers." },
        { id: "speech", label: "Speech", subtitle: "Choose TTS and STT settings for calls and voice notes." },
        { id: "account", label: "AutoYou Account", subtitle: "Sign in free to keep this server updated and to collect rewards. Separate from the server password." },
        { id: "finish", label: "Finish", subtitle: "Review readiness and mark bootstrap complete." }
    ];

    var SECURITY_MODE_HELP = {
        normal: {
            label: "Normal Mode (No Encryption)",
            description: "Pairing messages remain plaintext. Use this only for legacy local-first clients."
        },
        secure: {
            label: "Secure Mode (Password Only)",
            description: "Encrypts pairing messages with the server password while keeping setup simple."
        },
        secure_professional: {
            label: "Secure Professional (Password + 2FA)",
            description: "Encrypts pairing with password plus the one shared 2FA setup key used by Secure Professional pairing, authenticator-based public URL pairing, and admin-agent elevation."
        },
        secure_professional_maximus: {
            label: "Secure Professional Maximus",
            description: "Adds encryption to saved sessions, agent data, websites, notes, and settings on this computer. Secure pairing and your existing remote viewer/editor/admin controls continue to work."
        }
    };

    var SECURITY_TIER_OPTIONS = [
        { value: "B", label: "Quick Pairing" },
        { value: "A", label: "Enhanced Pairing" }
    ];
    var SECURITY_TIER_HELP = {
        B: "Single-message pairing: clients can pair with one chat message. Best compatibility.",
        A: "Requires the enhanced pairing handshake (an extra chat round trip) for every client, making password guessing harder."
    };

    function activeAiProvider() {
        var configured = getByPath(state.forms, "aiProvider.provider", getByPath(bootstrapConfig(), "ai_provider.provider", "ollama"));
        var match = findOption(AI_PROVIDER_OPTIONS, configured, "ollama");
        return match ? match.id : "ollama";
    }

    function activeTtsProvider() {
        var configured = getByPath(state.forms, "speech.tts_provider", getByPath(bootstrapConfig(), "speech.tts.provider", "system"));
        var match = findOption(TTS_PROVIDER_OPTIONS, configured, "system");
        return match ? match.id : "system";
    }

    function aiProviderUsesGateway(provider) {
        return provider === "ollama_gateway" || provider === "odysseus" || provider === "openclaw" || provider === "hermes";
    }

    function isOllamaProvider(provider) {
        return provider === "ollama" || provider === "ollama_gateway";
    }

    function statusBadgeLabel(tone) {
        if (tone === "green") {
            return "Ready";
        }
        if (tone === "amber") {
            return "Needs review";
        }
        if (tone === "red") {
            return "Attention";
        }
        if (tone === "blue") {
            return "Configured";
        }
        if (tone === "purple") {
            return "Custom";
        }
        return "Info";
    }

    function syncSetupCompletion(completed) {
        var timestamp = completed ? new Date().toISOString() : "";
        state.setup.payload = state.setup.payload || {};
        setByPath(state.setup.payload, "wizard_completed", completed);
        setByPath(state.setup.payload, "wizard_completed_at", timestamp);
        if (state.bootstrap) {
            setByPath(state.bootstrap, "config.onboarding.wizard_completed", completed);
            setByPath(state.bootstrap, "config.onboarding.wizard_completed_at", timestamp);
        }
        state.setup.stale = false;
    }

    function ensureSetupStepData(stepId) {
        if (stepId === "ai-provider" || stepId === "manage-models") {
            ensureAiData(false);
            if (stepId === "manage-models" && !state.aiLibrary.catalog && !state.aiLibrary.catalogLoading) {
                loadModelCatalog(false);
            }
            return;
        }
        if (stepId === "messaging-partner" || stepId === "connectivity") {
            ensureOperationsData(false, { passive: true });
            return;
        }
        if (stepId === "speech") {
            ensureSpeechData(false);
        }
    }

    function scrollIntoViewIfPresent(selector, options) {
        window.requestAnimationFrame(function () {
            var element = document.querySelector(selector);
            if (!element || typeof element.scrollIntoView !== "function") {
                return;
            }
            element.scrollIntoView(options || { behavior: "smooth", block: "start" });
        });
    }

    function scrollShellToTop() {
        window.requestAnimationFrame(function () {
            var scrollRoot = document.scrollingElement || document.documentElement || document.body;
            var main = document.querySelector(".ayu-main");
            var mainWrap = document.querySelector(".ayu-main-wrap");
            if (scrollRoot) {
                scrollRoot.scrollTop = 0;
            }
            if (document.documentElement) {
                document.documentElement.scrollTop = 0;
            }
            if (document.body) {
                document.body.scrollTop = 0;
            }
            if (main) {
                main.scrollTop = 0;
            }
            if (mainWrap) {
                mainWrap.scrollTop = 0;
            }
            window.scrollTo(0, 0);
        });
    }

    function clearSetupLinkedScreen() {
        state.setup.linkedScreen = "";
        state.setup.linkedStep = 0;
    }

    function openSetupLinkedScreen(screen) {
        state.setup.linkedScreen = screen || "";
        state.setup.linkedStep = Math.max(0, Math.min(Number(state.setup.activeStep) || 0, SETUP_WIZARD_STEPS.length - 1));
        setScreen(screen, { keepSetupLink: true });
    }

    function returnToSetupLinkedStep() {
        var linkedStep = Math.max(0, Math.min(Number(state.setup.linkedStep) || 0, SETUP_WIZARD_STEPS.length - 1));
        clearSetupLinkedScreen();
        state.setup.activeStep = linkedStep;
        setScreen("setup", { keepSetupLink: true });
        scrollIntoViewIfPresent("#ayu-setup-current", { behavior: "smooth", block: "start" });
    }

    function setSetupStep(index) {
        var maxIndex = Math.max(0, SETUP_WIZARD_STEPS.length - 1);
        var nextIndex = Math.min(maxIndex, Math.max(0, Number(index) || 0));
        state.setup.activeStep = nextIndex;
        renderApp();
        scrollIntoViewIfPresent("#ayu-setup-current", { behavior: "smooth", block: "start" });
        ensureSetupStepData(getByPath(SETUP_WIZARD_STEPS[nextIndex], "id", "security-basics"));
    }

    function setupProfilesPayload() {
        return getByPath(state.bootstrap, "metadata.setup_profiles", {});
    }

    function setupProfileTemplates() {
        var templates = getByPath(setupProfilesPayload(), "profile_templates", []);
        return Array.isArray(templates) ? templates : [];
    }

    function setupDecisionTree() {
        var tree = getByPath(setupProfilesPayload(), "decision_tree", []);
        return Array.isArray(tree) ? tree : [];
    }

    function setupProfileById(profileId) {
        var normalized = String(profileId || "").trim() || "private_starter";
        var templates = setupProfileTemplates();
        return templates.find(function (profile) { return profile.id === normalized; }) || templates[0] || null;
    }

    function setupDefaultsForProfile(profile) {
        var defaults = clone(getByPath(profile, "defaults", {}));
        defaults.profile_id = getByPath(profile, "id", "private_starter");
        defaults.intents = Array.isArray(defaults.intents) ? defaults.intents.slice() : [];
        defaults.network_scope = defaults.network_scope || "loopback";
        defaults.ai_path = defaults.ai_path || "local_ollama";
        defaults.agent_visibility = defaults.agent_visibility || "release_ready";
        return defaults;
    }

    function currentSetupAnswers() {
        if (!state.setup.answers || !state.setup.answers.profile_id) {
            state.setup.answers = setupDefaultsForProfile(setupProfileById("private_starter"));
        }
        if (!Array.isArray(state.setup.answers.intents)) {
            state.setup.answers.intents = [];
        }
        return state.setup.answers;
    }

    function selectSetupProfile(profileId) {
        var profile = setupProfileById(profileId);
        state.setup.answers = setupDefaultsForProfile(profile);
        state.setup.recipe = null;
        renderApp();
        scrollIntoViewIfPresent("#ayu-setup-decision-tree", { behavior: "smooth", block: "start" });
    }

    function updateSetupAnswer(questionId, optionId) {
        if (questionId === "profile_id") {
            selectSetupProfile(optionId);
            return;
        }
        var branch = setupDecisionTree().find(function (item) { return item.id === questionId; }) || {};
        var answers = currentSetupAnswers();
        if (branch.type === "multi") {
            var current = Array.isArray(answers[questionId]) ? answers[questionId].slice() : [];
            var index = current.indexOf(optionId);
            if (index === -1) {
                current.push(optionId);
            } else {
                current.splice(index, 1);
            }
            answers[questionId] = current;
        } else {
            answers[questionId] = optionId;
        }
        state.setup.recipe = null;
        renderApp();
    }

    function setupRiskTone(riskLevel) {
        if (riskLevel === "critical") {
            return "red";
        }
        if (riskLevel === "high") {
            return "amber";
        }
        if (riskLevel === "medium") {
            return "blue";
        }
        return "green";
    }

    async function previewSetupRecipe() {
        var recipe = await postJson("/api/setup/recipe/preview", {
            answers: currentSetupAnswers()
        });
        state.setup.recipe = recipe;
        renderApp();
        setNotice("success", "Setup plan preview updated.");
        scrollIntoViewIfPresent("#ayu-setup-recipe", { behavior: "smooth", block: "start" });
        return recipe;
    }

    async function applySetupRecipe(includeGuarded) {
        var recipe = state.setup.recipe || await previewSetupRecipe();
        if (includeGuarded) {
            var warningText = (getByPath(recipe, "warnings", []) || []).map(function (warning) {
                return "- " + (warning.title || warning.body || "Network exposure warning");
            }).join("\n");
            if (!window.confirm("Apply guarded network settings for this setup plan?\n\n" + warningText + "\n\nLAN binding still requires a restart when 0.0.0.0 is selected.")) {
                return null;
            }
        }
        var response = await postJson("/api/setup/recipe/apply", {
            answers: currentSetupAnswers(),
            include_guarded: Boolean(includeGuarded),
            include_security: Boolean(includeGuarded)
        });
        applyBootstrap(getByPath(response, "bootstrap", state.bootstrap));
        state.setup.recipe = getByPath(response, "recipe", recipe);
        await ensureSetupData(true);
        renderApp();
        setNotice("success", includeGuarded ? "Setup plan and guarded network settings applied." : "Safe setup settings applied.");
        return response;
    }

    function initializeForms(bootstrap) {
        var cfg = getByPath(bootstrap, "config", {});
        var selectedVideoSources = initialVideoSources(cfg);
        var gameButtons = getByPath(cfg, "video_call.remote_desktop.game_buttons", [{ label: "A", name: "action_a" }, { label: "B", name: "action_b" }]);
        var selectedAudioSources = initialAudioSources(cfg);
        var agentDetails = getByPath(bootstrap, "agents.agent_details", {});
        var adminDetail = agentDetails.admin_agent || {};
        var adminFrontendEnabled = asBoolean(
            getByPath(
                adminDetail,
                "frontend_control.enabled",
                getByPath(cfg, "admin.frontend_proxy_enabled", getByPath(cfg, "agent_frontends.admin_agent", false))
            ),
            false
        );
        state.forms = {
            overview: {
                serverName: getByPath(cfg, "server.name", "AutoYou-Server"),
                adminTheme: getByPath(bootstrap, "admin.theme", "dark"),
                bindHost: getByPath(cfg, "server.bind_host", "127.0.0.1"),
                nativeUnlockEnabled: asBoolean(getByPath(cfg, "security.native_unlock_enabled", true), true),
                softwareUpdatesEnabled: asBoolean(getByPath(cfg, "software_update.enabled", true), true)
            },
            aiProvider: {
                provider: getByPath(cfg, "ai_provider.provider", "ollama"),
                openclaw_port: getByPath(cfg, "ai_provider.openclaw_port", 18789),
                openclaw_token: "",
                openclaw_model: getByPath(cfg, "ai_provider.openclaw_model", "openclaw/default"),
                openclaw_agent_port: getByPath(cfg, "ai_provider.openclaw_agent_port", 18789),
                openclaw_agent_token: "",
                openclaw_agent_model: getByPath(cfg, "ai_provider.openclaw_agent_model", "openclaw/default"),
                hermes_port: getByPath(cfg, "ai_provider.hermes_port", 8642),
                hermes_token: "",
                hermes_model: getByPath(cfg, "ai_provider.hermes_model", "hermes-agent"),
                litellm_model: getByPath(cfg, "ai_provider.litellm_model", ""),
                litellm_api_key: "",
                litellm_api_base: getByPath(cfg, "ai_provider.litellm_api_base", ""),
                odysseus_api_base: getByPath(cfg, "ai_provider.odysseus_api_base", "http://127.0.0.1:7000"),
                odysseus_model: getByPath(cfg, "ai_provider.odysseus_model", ""),
                odysseus_token: "",
                ollama_enabled: Boolean(getByPath(cfg, "ollama.enabled", true)),
                ollama_api_base: getByPath(cfg, "ollama.api_base", "http://localhost:11434"),
                ollama_model: getByPath(cfg, "ollama.model", ""),
                use_google_api: Boolean(getByPath(cfg, "ollama.use_google_api", false)),
                google_model: getByPath(cfg, "ollama.google_model", "gemini-2.5-flash"),
                google_api_key: ""
            },
            aiAgent: {
                enabled: Boolean(getByPath(cfg, "ai_agent.enabled", true)),
                auto_start: Boolean(getByPath(cfg, "ai_agent.auto_start", true)),
                memory_backend: getByPath(cfg, "ai_agent.memory_backend", "legacy"),
                port: getByPath(cfg, "ai_agent.port", 8081),
                internet_search_enabled: Boolean(getByPath(cfg, "ai_agent.internet_search_enabled", true)),
                lan_access_enabled: Boolean(getByPath(cfg, "ai_agent.lan_access_enabled", false)),
                record_messages_in_database: Boolean(getByPath(cfg, "ai_agent.record_messages_in_database", true))
            },
            audioPlayback: {
                enabled: asBoolean(getByPath(cfg, "audio_playback.enabled", true), true)
            },
            clientIdentity: {
                store_client_names_in_history: Boolean(getByPath(cfg, "client_identity.store_client_names_in_history", false))
            },
            modelBehavior: {
                mode: getByPath(cfg, "model_behavior.mode", "accurate"),
                temperature: getByPath(cfg, "model_behavior.temperature", ""),
                top_p: getByPath(cfg, "model_behavior.top_p", ""),
                top_k: getByPath(cfg, "model_behavior.top_k", ""),
                repeat_penalty: getByPath(cfg, "model_behavior.repeat_penalty", ""),
                num_ctx: getByPath(cfg, "model_behavior.num_ctx", ""),
                show_thinking: Boolean(getByPath(cfg, "model_behavior.show_thinking", false)),
                thinking_level: getByPath(cfg, "model_behavior.thinking_level", "") || ""
            },
            page: {
                port: getByPath(cfg, "autoyou_page.port", 8067),
                auto_start: Boolean(getByPath(cfg, "autoyou_page.auto_start", true)),
                feed_window_days: getByPath(cfg, "autoyou_page.feed_window_days", 0),
                theme: getByPath(cfg, "autoyou_page.theme", "light"),
                remote_access_role: normalizeRemoteAccessRole(getByPath(cfg, "autoyou_page.remote_access_role", "viewer")),
                custom_forward_enabled: Boolean(getByPath(cfg, "autoyou_page.custom_forward_enabled", false)),
                custom_forward_port: getByPath(cfg, "autoyou_page.custom_forward_port", 8067),
                advertisedWebsites: clone(getByPath(cfg, "autoyou_page.advertised_websites", [])),
                newWebsite: { port: "", label: "", description: "", target_url: "", websocket_enabled: false, enabled: true },
                bookmarks: clone(getByPath(cfg, "autoyou_page.bookmarks", [])),
                newBookmark: { title: "", url: "", description: "", enabled: true },
                admin_frontend_enabled: adminFrontendEnabled,
                websiteHosting: {
                    enabled: Boolean(getByPath(cfg, "tunnelmole.website_hosting.enabled", false)),
                    agent_name: getByPath(cfg, "tunnelmole.website_hosting.agent_name", getByPath(bootstrap, "status.browser.default_website.agent_name", "page_agent")),
                    auto_start_on_boot: Boolean(getByPath(cfg, "tunnelmole.auto_start_on_boot", false))
                },
                agentWebsitesSecurity: {
                    require_otp: Boolean(getByPath(cfg, "agent_websites.require_otp", false)),
                    disable_otp: Boolean(getByPath(cfg, "agent_websites.disable_otp", false)),
                    shared_session_enabled: Boolean(getByPath(cfg, "agent_websites.shared_session_enabled", false)),
                    shared_session_ttl_days: getByPath(cfg, "agent_websites.shared_session_ttl_days", 30)
                }
            },
            messaging: {
                telegram: {
                    bot_token: getByPath(cfg, "telegram.bot_token", ""),
                    acl_usernames: joinList(getByPath(cfg, "telegram.acl_usernames", [])),
                    acl_sender_ids: joinList(getByPath(cfg, "telegram.acl_sender_ids", [])),
                    access_gate_enabled: Boolean(getByPath(cfg, "telegram.access_gate_enabled", false)),
                    silent_unapproved_messages: Boolean(getByPath(cfg, "telegram.silent_unapproved_messages", false))
                },
                telegramUser: {
                    enabled: asBoolean(getByPath(cfg, "telegram_user.enabled", getByPath(state.operations, "telegramUserStatus.enabled", false)), false),
                    api_id: getByPath(cfg, "telegram_user.api_id", ""),
                    api_hash: "",
                    training_export_consent: asBoolean(getByPath(cfg, "telegram_user.training_export_consent", false), false),
                    prompt_builder_enabled: asBoolean(getByPath(cfg, "telegram_user.prompt_builder.enabled", false), false),
                    prompt_builder_agent: getByPath(cfg, "telegram_user.prompt_builder.application_agent", "codex_desktop_agent"),
                    password: ""
                },
                signal: {
                    enabled: asBoolean(getByPath(cfg, "signal.enabled", getByPath(state.operations, "signalStatus.enabled", false)), false),
                    port: getByPath(cfg, "signal.port", 8082),
                    device_name: getByPath(cfg, "signal.device_name", "AutoYou-Signal"),
                    shutdown_docker_on_exit: asBoolean(getByPath(cfg, "signal.shutdown_docker_on_exit", true), true)
                },
                whatsapp: {
                    enabled: asBoolean(getByPath(cfg, "whatsapp.enabled", getByPath(state.operations, "whatsappStatus.enabled", false)), false),
                    port: getByPath(cfg, "whatsapp.websocket_port", getByPath(cfg, "whatsapp.port", 8083)),
                    device_name: getByPath(cfg, "whatsapp.device_name", "AutoYou-WhatsApp"),
                    shutdown_on_exit: asBoolean(getByPath(cfg, "whatsapp.shutdown_on_exit", true), true)
                },
                mcp: {
                    enabled: asBoolean(getByPath(cfg, "mcp.enabled", true), true),
                    api_token: "",
                    clear_api_token: false,
                    adapter_url: getByPath(cfg, "mcp.adapter_url", "http://127.0.0.1:8071")
                }
            },
            videoCall: {
                enabled: asBoolean(getByPath(cfg, "video_call.enabled", true), true),
                audio_enabled: asBoolean(getByPath(cfg, "video_call.audio_enabled", true), true),
                disable_autoyou_agents: asBoolean(getByPath(cfg, "video_call.disable_autoyou_agents", false), false),
                ai_audio_replies_enabled: asBoolean(getByPath(cfg, "video_call.ai_audio_replies_enabled", true), true),
                background_mode_enabled: asBoolean(getByPath(cfg, "video_call.background_mode_enabled", false), false),
                silent_recording_enabled: asBoolean(getByPath(cfg, "video_call.silent_recording_enabled", false), false),
                record_audio_only_calls: asBoolean(getByPath(cfg, "video_call.record_audio_only_calls", false), false),
                location_recording_enabled: asBoolean(getByPath(cfg, "video_call.location_recording_enabled", false), false),
                wuift_enabled: asBoolean(getByPath(cfg, "video_call.wuift_enabled", true), true),
                silent_recording_dir: getByPath(cfg, "video_call.silent_recording_dir", ""),
                silent_recording_batch_seconds: getByPath(cfg, "video_call.silent_recording_batch_seconds", 3599),
                record_my_video: asBoolean(getByPath(cfg, "video_call.record_my_video", false), false),
                recording_dir: getByPath(cfg, "video_call.recording_dir", ""),
                recording_mode: getByPath(cfg, "video_call.recording_mode", "video"),
                image_interval_seconds: getByPath(cfg, "video_call.image_interval_seconds", 5),
                outbound_source: getByPath(cfg, "video_call.outbound_source", "remote_desktop"),
                outbound_sources: selectedVideoSources,
                outbound_remote_desktop: selectedVideoSources.indexOf("remote_desktop") !== -1,
                outbound_api: selectedVideoSources.indexOf("api") !== -1,
                outbound_video_file: selectedVideoSources.indexOf("video_file") !== -1,
                outbound_camera: selectedVideoSources.indexOf("camera") !== -1,
                api_video_source_id: getByPath(cfg, "video_call.api_video_source_id", "default"),
                capture_audio: asBoolean(getByPath(cfg, "video_call.capture_audio", true), true),
                audio_sources: selectedAudioSources,
                audio_microphone: selectedAudioSources.indexOf("microphone") !== -1,
                audio_speaker_loopback: selectedAudioSources.indexOf("speaker_loopback") !== -1,
                input_audio_source: getByPath(cfg, "video_call.input_audio_source", "default"),
                camera_device_id: String(getByPath(cfg, "video_call.camera_device_id", 0)),
                video_file: {
                    path: getByPath(cfg, "video_call.video_file.path", ""),
                    loop: asBoolean(getByPath(cfg, "video_call.video_file.loop", true), true)
                },
                remote_desktop: {
                    enabled: asBoolean(getByPath(cfg, "video_call.remote_desktop.enabled", true), true),
                    send_screen: asBoolean(getByPath(cfg, "video_call.remote_desktop.send_screen", true), true),
                    monitor_id: getByPath(cfg, "video_call.remote_desktop.monitor_id", 0),
                    quality: getByPath(cfg, "video_call.remote_desktop.quality", "balanced"),
                    bitrate_kbps: getByPath(cfg, "video_call.remote_desktop.bitrate_kbps", 1500),
                    control_enabled: asBoolean(getByPath(cfg, "video_call.remote_desktop.control_enabled", false), false),
                    game_enabled: asBoolean(getByPath(cfg, "video_call.remote_desktop.game_enabled", false), false),
                    game_buttons: Array.isArray(gameButtons) ? gameButtons.map(function (button) { return button.label + ":" + button.name; }).join(", ") : "A:action_a, B:action_b"
                }
            },
            speech: {
                tts_provider: getByPath(cfg, "speech.tts.provider", "system"),
                tts_rate: getByPath(cfg, "speech.tts.rate", 1.0),
                system_voice: getByPath(cfg, "speech.tts.system_voice", ""),
                openai_api_key: "",
                openai_base_url: getByPath(cfg, "speech.tts.openai.base_url", "https://api.openai.com/v1"),
                openai_model: getByPath(cfg, "speech.tts.openai.model", "gpt-4o-mini-tts"),
                openai_voice: getByPath(cfg, "speech.tts.openai.voice", "alloy"),
                openai_instructions: getByPath(cfg, "speech.tts.openai.instructions", ""),
                azure_speech_key: "",
                azure_speech_region: getByPath(cfg, "speech.tts.azure.speech_region", ""),
                azure_voice: getByPath(cfg, "speech.tts.azure.voice", ""),
                azure_endpoint_id: getByPath(cfg, "speech.tts.azure.endpoint_id", ""),
                emotivoice_speaker: getByPath(cfg, "speech.tts.emotivoice.speaker", "8051"),
                emotivoice_conversation_emotion: asBoolean(getByPath(cfg, "speech.tts.emotivoice.conversation_emotion", true), true),
                stt_model: getByPath(cfg, "speech.stt.model", "tiny.en"),
                stt_language: getByPath(cfg, "speech.stt.language", "en"),
                stt_device: getByPath(cfg, "speech.stt.device", "cpu"),
                stt_compute_type: getByPath(cfg, "speech.stt.compute_type", "float32"),
                stt_silero_sensitivity: getByPath(cfg, "speech.stt.silero_sensitivity", 0.4),
                stt_post_speech_silence_duration: getByPath(cfg, "speech.stt.post_speech_silence_duration", 0.6),
                voice_training_capture_enabled: asBoolean(getByPath(cfg, "speech.voice_training.capture_enabled", false), false),
                download_model: ""
            },
            connectivity: {
                iceImportText: "",
                iceServersText: JSON.stringify(getByPath(cfg, "rtc.iceServers", []), null, 2),
                tunnelmole: {
                    enabled: Boolean(getByPath(cfg, "tunnelmole.enabled", false)),
                    timeout_minutes: getByPath(cfg, "tunnelmole.timeout_minutes", 5),
                    otp_timeout_minutes: getByPath(cfg, "tunnelmole.otp_timeout_minutes", 5),
                    otp_multiuse: Boolean(getByPath(cfg, "tunnelmole.otp_multiuse", false)),
                    pair_code_mode: getByPath(cfg, "tunnelmole.pair_code_mode", getByPath(bootstrap, "metadata.tunnelmole.pair_code_mode", "random_otp")),
                    connection_mode: getByPath(cfg, "tunnelmole.connection_mode", getByPath(bootstrap, "metadata.tunnelmole.connection_mode", "timed")),
                    url_only_pair: Boolean(getByPath(cfg, "tunnelmole.url_only_pair", false)),
                    auto_start_on_boot: Boolean(getByPath(cfg, "tunnelmole.auto_start_on_boot", false))
                },
                bluetoothPairing: {
                    enabled: Boolean(getByPath(cfg, "bluetooth_pairing.enabled", false))
                }
            },
            security: {
                mode: getByPath(cfg, "security.mode", "secure"),
                tier: normalizeSecurityTier(getByPath(cfg, "security.tier", "B")),
                new_password: "",
                confirm_password: "",
                import_totp: ""
            },
            agentWorkbench: {
                name: "website_agent_clone",
                description: "A browser-facing website builder agent.",
                tool_name: "handle_request",
                tool_description: "Handle the agent-specific request.",
                raw_instructions: "",
                draft_description: "",
                draft_instructions: "",
                frontend_ui_purpose: "",
                frontend_app_title: "",
                frontend_local_port: 8094,
                frontend_title: "",
                frontend_description: "",
                frontend_entry_path: "/",
                frontend_recommended_port: "",
                frontend_requires_proxy: true,
                frontend_route_mode: "path_proxy",
                frontend_stack: "fastapi_static",
                backend_stack: "python_fastapi",
                install_after_publish: false,
                jailbreak_prompt: ""
            },
            liveOps: {
                telegram_chat_id: "",
                telegram_reply_to_message_id: "",
                telegram_message: "",
                telegram_user_message: "",
                signal_to: "",
                signal_message: "",
                whatsapp_to: "",
                whatsapp_message: "",
                session_id: "",
                owner_key: "",
                webrtc_message: "",
                playback_file_path: "",
                cloud_notify_title: "AutoYou",
                cloud_notify_body: "Notification from your AutoYou server.",
                cloud_notify_category: "admin"
            }
        };

        if (!state.selectedAgentName) {
            var overview = getByPath(bootstrap, "agents.agent_overview", []);
            state.selectedAgentName = defaultStudioAgentName(overview);
        }
    }

    function applyBootstrap(bootstrap) {
        state.bootstrap = bootstrap;
        var updateLocal = getByPath(bootstrap, "status.software_update", {});
        state.softwareUpdate.payload = Object.assign({}, state.softwareUpdate.payload || {}, updateLocal);
        if (!getByPath(updateLocal, "enabled", true) || !getByPath(updateLocal, "signed_in", false)) {
            state.softwareUpdate.payload = updateLocal;
            state.softwareUpdate.checked = true;
        }
        state.setup.stale = true;
        state.agentWorkbench.stale = true;
        state.agentWorkbench.payload = null;
        state.operations.stale = true;
        state.profileMenuOpen = false;
        if (state.selectedAgentName && !getByPath(bootstrap, "agents.agent_details." + state.selectedAgentName, null)) {
            state.selectedAgentName = "";
            state.agentWorkbench.detail = null;
        }
        initializeForms(bootstrap);
        applyTheme(getByPath(bootstrap, "admin.theme", "dark"));
    }

    async function refreshBootstrap(successMessage) {
        var payload = await requestJson("/api/admin/bootstrap");
        applyBootstrap(payload);
        if (state.screen === "live" || state.screen === "messaging" || state.screen === "video" || state.screen === "connectivity") {
            ensureOperationsData(false, { passive: true });
        }
        if (state.screen === "video") {
            ensureMediaDeviceData(false, { passive: true });
        }
        if (state.screen === "overview" || state.screen === "live") {
            ensureLocalPairInfo(true).catch(function () {});
        }
        if (successMessage) {
            setNotice("success", successMessage);
        } else {
            renderApp();
        }
        checkSoftwareUpdate(false).catch(function () {});
        return payload;
    }

    async function checkSoftwareUpdate(force) {
        var local = getByPath(state.bootstrap, "status.software_update", {});
        if (getByPath(local, "store_managed", false) || !getByPath(local, "enabled", true) || !getByPath(local, "signed_in", false)) {
            state.softwareUpdate.payload = local;
            state.softwareUpdate.checked = true;
            renderApp();
            return local;
        }
        if (state.softwareUpdate.loading || (state.softwareUpdate.checked && !force)) {
            return state.softwareUpdate.payload;
        }
        state.softwareUpdate.loading = true;
        renderApp();
        try {
            var payload = await requestJson("/api/software-update/status");
            state.softwareUpdate.payload = Object.assign({}, local, payload || {});
            state.softwareUpdate.checked = true;
            if (force) {
                setNotice(payload && payload.success === false ? "warning" : "success", getByPath(payload, "message", getByPath(payload, "update_available", false) ? "An AutoYou update is ready." : "AutoYou is up to date."));
            }
            return payload;
        } finally {
            state.softwareUpdate.loading = false;
            renderApp();
        }
    }

    async function patchConfig(payload, successMessage) {
        var response = await postJson("/api/admin/config", payload);
        if (response.bootstrap) {
            applyBootstrap(response.bootstrap);
        } else {
            applyBootstrap(response);
        }
        if (state.screen === "setup" || !onboardingCompleted()) {
            ensureSetupData(false);
        }
        if (state.screen === "live" || state.screen === "messaging" || state.screen === "connectivity") {
            ensureOperationsData(false, { passive: true });
        }
        setNotice("success", successMessage || "Settings updated.");
        return response;
    }

    function snapshotPageForm() {
        return clone(getByPath(state.forms, "page", {}));
    }

    function restorePageDraft(pageDraft) {
        if (!pageDraft || typeof pageDraft !== "object") {
            return;
        }
        [
            "port",
            "auto_start",
            "feed_window_days",
            "theme",
            "remote_access_role",
            "custom_forward_enabled",
            "custom_forward_port",
            "admin_frontend_enabled",
            "websiteHosting",
            "newWebsite",
            "newBookmark"
        ].forEach(function (key) {
            if (Object.prototype.hasOwnProperty.call(pageDraft, key)) {
                setByPath(state.forms, "page." + key, pageDraft[key]);
            }
        });
    }

    async function persistAdvertisedWebsites(successMessage, pageDraft) {
        await patchConfig({
            autoyou_page: {
                advertised_websites: getByPath(state.forms, "page.advertisedWebsites", [])
            }
        }, successMessage || "Advertised websites updated.");
        restorePageDraft(pageDraft);
        renderApp();
    }

    async function persistBookmarks(successMessage, pageDraft) {
        await patchConfig({
            autoyou_page: {
                bookmarks: getByPath(state.forms, "page.bookmarks", [])
            }
        }, successMessage || "Bookmarks updated.");
        restorePageDraft(pageDraft);
        renderApp();
    }

    async function refreshSetupSecurityStep() {
        if (state.screen !== "setup" && onboardingCompleted()) {
            return;
        }
        await ensureSetupData(true);
        ensureSetupStepData("security-basics");
        scrollIntoViewIfPresent("#ayu-setup-current", { behavior: "smooth", block: "start" });
    }

    function savedSecurityMode() {
        return getByPath(bootstrapConfig(), "security.mode", "secure");
    }

    async function saveSecurityModeSelection(modeOverride) {
        var selectedMode = modeOverride || getByPath(state.forms, "security.mode", "secure");
        var securityMode = await postJson("/api/admin/security/mode", { mode: selectedMode });
        applyBootstrap(getByPath(securityMode, "bootstrap", state.bootstrap));
        await refreshSetupSecurityStep();
        return securityMode;
    }

    function normalizeSecurityTier(value) {
        var tier = String(value || "B").trim().toUpperCase();
        return tier === "A" ? "A" : "B";
    }

    function savedSecurityTier() {
        return normalizeSecurityTier(getByPath(bootstrapConfig(), "security.tier", "B"));
    }

    async function saveSecurityTierSelection(tierOverride) {
        var selectedTier = normalizeSecurityTier(tierOverride || getByPath(state.forms, "security.tier", "B"));
        var securityTier = await postJson("/api/admin/security/tier", { tier: selectedTier });
        applyBootstrap(getByPath(securityTier, "bootstrap", state.bootstrap));
        await refreshSetupSecurityStep();
        return securityTier;
    }

    async function saveSecurityPasswordSelection() {
        var passwordResponse = await postJson("/api/admin/password", {
            new_password: getByPath(state.forms, "security.new_password", ""),
            confirm_password: getByPath(state.forms, "security.confirm_password", "")
        });
        applyBootstrap(getByPath(passwordResponse, "bootstrap", state.bootstrap));
        setByPath(state.forms, "security.new_password", "");
        setByPath(state.forms, "security.confirm_password", "");
        state.securityPasswordVisible = false;
        await refreshSetupSecurityStep();
        return passwordResponse;
    }

    async function rotateSecureStorageKey() {
        var rotationResponse = await postJson("/api/admin/security/storage/rotate", {});
        applyBootstrap(getByPath(rotationResponse, "bootstrap", state.bootstrap));
        await refreshSetupSecurityStep();
        return rotationResponse;
    }

    async function safeRequestJson(url, options) {
        try {
            return { ok: true, payload: await requestJson(url, options) };
        } catch (error) {
            return { ok: false, error: error.message || String(error) };
        }
    }

    async function ensureSetupData(force) {
        if (!force && state.setup.payload && !state.setup.stale) {
            return;
        }
        state.setup.loading = true;
        renderApp();
        try {
            state.setup.payload = await requestJson("/api/wizard/status");
            state.setup.stale = false;
        } catch (error) {
            setNotice("error", error.message || String(error));
        } finally {
            state.setup.loading = false;
            renderApp();
        }
    }

    async function ensureAiData(force) {
        if (!force && state.aiLibrary.behavior && state.aiLibrary.local && state.aiLibrary.downloads) {
            return;
        }
        state.aiLibrary.behaviorLoading = true;
        state.aiLibrary.localLoading = true;
        state.aiLibrary.downloadsLoading = true;
        state.aiLibrary.openclawLoading = true;
        state.aiLibrary.hermesLoading = true;
        state.aiLibrary.ollamaLoading = activeAiProvider() === "ollama_gateway";
        state.aiLibrary.odysseusLoading = activeAiProvider() === "odysseus";
        renderApp();
        try {
            var results = await Promise.all([
                requestJson("/api/model-behavior"),
                requestJson("/api/model-library/local"),
                requestJson("/api/model-library/downloads"),
                safeRequestJson("/api/ai/openclaw/status"),
                safeRequestJson("/api/ai/hermes/status"),
                activeAiProvider() === "ollama_gateway" ? safeRequestJson("/api/ai/ollama/status") : Promise.resolve({ ok: true, payload: null }),
                activeAiProvider() === "odysseus" ? safeRequestJson("/api/ai/odysseus/status") : Promise.resolve({ ok: true, payload: null })
            ]);
            state.aiLibrary.behavior = results[0];
            syncModelBehaviorFormFromResponse(results[0]);
            state.aiLibrary.local = results[1];
            applySelectedOllamaModel(results[1], "");
            state.aiLibrary.downloads = results[2];
            state.aiLibrary.openclawStatus = results[3].ok ? results[3].payload : { running: false, error: results[3].error || "OpenClaw gateway not reachable." };
            state.aiLibrary.hermesStatus = results[4].ok ? results[4].payload : { running: false, error: results[4].error || "Hermes gateway not reachable." };
            state.aiLibrary.ollamaStatus = results[5].ok ? results[5].payload : { available: false, error: results[5].error || "Ollama gateway not reachable." };
            state.aiLibrary.odysseusStatus = results[6].ok ? results[6].payload : { available: false, error: results[6].error || "Odysseus gateway not reachable." };
            if (hasActiveJobs(results[2])) {
                startModelDownloadPolling();
            } else {
                stopModelDownloadPolling();
            }
        } catch (error) {
            setNotice("error", error.message || String(error));
        } finally {
            state.aiLibrary.behaviorLoading = false;
            state.aiLibrary.localLoading = false;
            state.aiLibrary.downloadsLoading = false;
            state.aiLibrary.openclawLoading = false;
            state.aiLibrary.hermesLoading = false;
            state.aiLibrary.ollamaLoading = false;
            state.aiLibrary.odysseusLoading = false;
            renderApp();
        }
    }

    async function loadModelCatalog(nextPage) {
        state.aiLibrary.catalogLoading = true;
        if (!nextPage) {
            state.aiLibrary.catalogError = "";
            state.aiLibrary.selectedCatalogKey = "";
            state.aiLibrary.detailLoadingKey = "";
        }
        renderApp();
        try {
            var query = encodeURIComponent(state.aiLibrary.query || "");
            var source = encodeURIComponent(state.aiLibrary.source || "ollama");
            var page = nextPage ? (Number(state.aiLibrary.catalogPage || 1) + 1) : 1;
            var payload = await requestJson("/api/model-library/catalog?source=" + source + "&q=" + query + "&page=" + page);
            var existingItems = getByPath(state.aiLibrary, "catalog.items", getByPath(state.aiLibrary, "catalog.models", getByPath(state.aiLibrary, "catalog.results", [])));
            var incomingItems = getByPath(payload, "items", getByPath(payload, "models", getByPath(payload, "results", [])));
            if (nextPage && Array.isArray(existingItems) && Array.isArray(incomingItems)) {
                if (Array.isArray(payload.items)) {
                    payload.items = existingItems.concat(incomingItems);
                } else if (Array.isArray(payload.models)) {
                    payload.models = existingItems.concat(incomingItems);
                } else if (Array.isArray(payload.results)) {
                    payload.results = existingItems.concat(incomingItems);
                }
            }
            state.aiLibrary.catalog = payload;
            state.aiLibrary.catalogPage = Number(payload.page || page || 1);
            state.aiLibrary.catalogHasMore = Boolean(payload.has_more);
        } catch (error) {
            state.aiLibrary.catalogError = error.message || String(error);
            setNotice("error", state.aiLibrary.catalogError);
        } finally {
            state.aiLibrary.catalogLoading = false;
            renderApp();
        }
    }

    async function ensureCatalogDetail(source, identifier) {
        var detailKey = catalogDetailKey(source, identifier);
        if (getByPath(state.aiLibrary, "details." + detailKey, null)) {
            return getByPath(state.aiLibrary, "details." + detailKey, null);
        }
        state.aiLibrary.detailLoadingKey = detailKey;
        renderApp();
        try {
            var payload = await requestJson("/api/model-library/details?source=" + encodeURIComponent(source || "ollama") + "&id=" + encodeURIComponent(identifier || ""));
            setByPath(state.aiLibrary, "details." + detailKey, payload);
            return payload;
        } finally {
            if (state.aiLibrary.detailLoadingKey === detailKey) {
                state.aiLibrary.detailLoadingKey = "";
                renderApp();
            }
        }
    }

    function stopModelDownloadPolling() {
        if (liveIntervals.modelJobs) {
            window.clearInterval(liveIntervals.modelJobs);
            liveIntervals.modelJobs = null;
        }
    }

    function startModelDownloadPolling() {
        if (liveIntervals.modelJobs) {
            return;
        }
        liveIntervals.modelJobs = window.setInterval(function () {
            refreshModelDownloads(true).catch(function () {});
        }, 2000);
    }

    async function maybeCompletePendingModelSelection() {
        var pending = state.aiLibrary.pendingSelection;
        if (!pending || pending.selecting) {
            return;
        }
        pending.selecting = true;
        try {
            state.aiLibrary.local = await requestJson("/api/model-library/local");
            if (modelAvailableLocally(pending.model)) {
                state.aiLibrary.pendingSelection = null;
                var selectionResult = await postJson("/api/model-library/select", { model: pending.model });
                applySelectedOllamaModel(selectionResult, pending.model);
                await refreshBootstrap();
                await ensureAiData(true);
                setNotice("success", "Model downloaded and selected.");
                return;
            }
            var jobs = activeJobList(state.aiLibrary.downloads);
            var matchingJob = jobs.find(function (job) {
                return getByPath(job, "job_id", "") === pending.jobId || getByPath(job, "reference", getByPath(job, "title", "")) === pending.reference;
            });
            state.aiLibrary.pendingSelection = null;
            if (matchingJob && String(getByPath(matchingJob, "status", "")).toLowerCase() === "failed") {
                setNotice("error", getByPath(matchingJob, "error", getByPath(matchingJob, "message", "Model download failed.")));
            } else {
                setNotice("error", "Model download finished but the model was not detected locally.");
            }
            renderApp();
        } finally {
            if (state.aiLibrary.pendingSelection) {
                state.aiLibrary.pendingSelection.selecting = false;
            }
        }
    }

    async function refreshModelDownloads(silent) {
        state.aiLibrary.downloads = await requestJson("/api/model-library/downloads");
        if (hasActiveJobs(state.aiLibrary.downloads)) {
            startModelDownloadPolling();
        } else {
            stopModelDownloadPolling();
            if (state.aiLibrary.pendingSelection) {
                await maybeCompletePendingModelSelection();
            }
        }
        if (silent) {
            renderApp({ passive: true });
        } else {
            renderApp();
        }
        return state.aiLibrary.downloads;
    }

    async function startModelDownload(payload, pendingSelection) {
        var response = await postJson("/api/model-library/download", payload);
        state.aiLibrary.downloads = {
            success: true,
            jobs: activeJobList(state.aiLibrary.downloads).concat([getByPath(response, "job", {})]).filter(function (job) {
                return job && Object.keys(job).length;
            })
        };
        if (pendingSelection) {
            state.aiLibrary.pendingSelection = {
                model: pendingSelection.model,
                reference: pendingSelection.reference,
                jobId: getByPath(response, "job.job_id", ""),
                selecting: false
            };
        }
        startModelDownloadPolling();
        renderApp();
        return response;
    }

    function stopSpeechDownloadPolling() {
        if (liveIntervals.speechJobs) {
            window.clearInterval(liveIntervals.speechJobs);
            liveIntervals.speechJobs = null;
        }
    }

    function startSpeechDownloadPolling() {
        if (liveIntervals.speechJobs) {
            return;
        }
        liveIntervals.speechJobs = window.setInterval(function () {
            refreshSpeechDownloads(true).catch(function () {});
        }, 2000);
    }

    async function refreshSpeechDownloads(silent) {
        state.speechLibrary.downloads = await requestJson("/api/speech-models/downloads");
        if (hasActiveJobs(state.speechLibrary.downloads)) {
            startSpeechDownloadPolling();
        } else {
            stopSpeechDownloadPolling();
        }
        renderApp(silent ? { passive: true } : undefined);
        return state.speechLibrary.downloads;
    }

    function buildModelDownloadRequest(element) {
        var source = element ? (element.getAttribute("data-source") || state.aiLibrary.source || "ollama") : (state.aiLibrary.source || "ollama");
        var reference = element ? (element.getAttribute("data-reference") || "") : "";
        var title = element ? (element.getAttribute("data-title") || reference) : reference;
        if (source === "huggingface") {
            return {
                source: "huggingface",
                repo_id: element ? (element.getAttribute("data-repo-id") || reference) : reference,
                quantization: element ? (element.getAttribute("data-quantization") || "") : "",
                reference: reference,
                title: title
            };
        }
        return {
            source: "ollama",
            reference: reference,
            title: title
        };
    }

    async function ensureInstructions(force) {
        if (!force && state.instructions.payload) {
            return;
        }
        state.instructions.loading = true;
        renderApp();
        try {
            var payload = await requestJson("/api/agent-instructions");
            state.instructions.payload = payload;
            var sections = Array.isArray(payload.sections) ? payload.sections : [];
            var selected = sections.length ? sections[0].variable : "";
            state.instructions.selectedVariable = state.instructions.selectedVariable || selected;
            setByPath(state.forms, "agentWorkbench.raw_instructions", payload.instructions || "");
            sections.forEach(function (section) {
                setByPath(state.forms, "agentWorkbench.section_" + section.variable, section.value || "");
            });
        } catch (error) {
            setNotice("error", error.message || String(error));
        } finally {
            state.instructions.loading = false;
            renderApp();
        }
    }

    async function ensureSpeechData(force) {
        if (!force && state.speechLibrary.status && state.speechLibrary.downloads) {
            return;
        }
        state.speechLibrary.loading = true;
        renderApp();
        try {
            var results = await Promise.all([
                requestJson("/api/speech-models/status"),
                requestJson("/api/speech-models/downloads")
            ]);
            state.speechLibrary.status = results[0];
            state.speechLibrary.downloads = results[1];
            if (hasActiveJobs(results[1])) {
                startSpeechDownloadPolling();
            } else {
                stopSpeechDownloadPolling();
            }
        } catch (error) {
            setNotice("error", error.message || String(error));
        } finally {
            state.speechLibrary.loading = false;
            renderApp();
        }
    }

    async function ensureSecurityData(force) {
        if (!force && state.jailbreak.status && state.jailbreak.prompt) {
            return;
        }
        state.jailbreak.loading = true;
        renderApp();
        try {
            var results = await Promise.all([
                requestJson("/api/jailbreak/status"),
                requestJson("/api/jailbreak/prompt")
            ]);
            state.jailbreak.status = results[0];
            state.jailbreak.prompt = results[1];
            setByPath(state.forms, "agentWorkbench.jailbreak_prompt", getByPath(results[1], "prompt", ""));
        } catch (error) {
            setNotice("error", error.message || String(error));
        } finally {
            state.jailbreak.loading = false;
            renderApp();
        }
        await loadAgentSecurityProfiles();
    }

    function clearAgentSecurityEnrolment() {
        // Scrub the one-time secret's fields before dropping the reference so
        // nothing keeps the derived TOTP secret reachable in this session beyond
        // this call - it was never persisted server-side either.
        var enrol = state.agentSecurity.lastEnrolment;
        if (enrol && typeof enrol === "object") {
            Object.keys(enrol).forEach(function (key) {
                enrol[key] = "";
            });
        }
        state.agentSecurity.lastEnrolment = null;
        state.agentSecurity.verifyError = null;
        state.agentSecurity.enrolmentVerified = false;
    }

    async function loadAgentSecurityProfiles() {
        try {
            var payload = await requestJson("/api/agent-security/profiles");
            state.agentSecurity.profiles = getByPath(payload, "profiles", []) || [];
            state.agentSecurity.available = true;
            state.agentSecurity.error = null;
        } catch (error) {
            // 409 when the server is locked, or endpoint unavailable - degrade quietly.
            state.agentSecurity.profiles = [];
            state.agentSecurity.available = false;
            state.agentSecurity.error = error.message || String(error);
        }
        renderApp();
    }

    async function ensureLocalPairInfo(force) {
        if (state.localPair.loading) {
            return;
        }
        if (!force && state.localPair.payload) {
            return;
        }
        state.localPair.loading = true;
        try {
            var result = await safeRequestJson("/api/local-pair-info");
            if (result.ok) {
                state.localPair.payload = result.payload;
                state.localPair.error = "";
            } else {
                state.localPair.error = result.error || "Unavailable";
            }
        } finally {
            state.localPair.loading = false;
            renderApp({ passive: true });
        }
    }

    async function ensureOperationsData(force, options) {
        options = options || {};
        var renderOptions = options.passive ? { passive: true } : undefined;
        if (!force && !state.operations.stale && (state.operations.cloudStatus || state.operations.datachannel || state.operations.playback || state.operations.queue || state.operations.taskSummary)) {
            return;
        }
        state.operations.loading = true;
        renderApp(renderOptions);
        try {
            var requests = [
                { key: "signalStatus", url: "/api/signal/status" },
                { key: "signalMessages", url: "/api/signal/messages?limit=12" },
                { key: "signalDetail", url: "/api/signal/detailed-status" },
                { key: "signalDeviceName", url: "/api/signal/device-name" },
                { key: "telegramSenders", url: "/api/telegram/senders" },
                { key: "telegramUserStatus", url: "/api/telegram-user/status" },
                { key: "telegramUserMessages", url: "/api/telegram-user/messages?limit=12" },
                { key: "whatsappStatus", url: "/api/whatsapp/status" },
                { key: "cloudStatus", url: "/api/cloud/status" },
                { key: "cloudDevices", url: "/api/cloud/devices" },
                { key: "datachannel", url: "/api/datachannel-status" },
                { key: "playback", url: "/api/webrtc/playback/enabled" },
                { key: "webrtcCapabilities", url: "/api/webrtc/capabilities" },
                { key: "videoFiles", url: "/api/webrtc/video-files" },
                { key: "queue", url: "/api/scheduler/notification-queue" },
                { key: "taskSummary", url: "/api/scheduler/live-summary" }
            ];
            var results = await Promise.all(requests.map(function (entry) {
                return safeRequestJson(entry.url);
            }));
            state.operations.errors = {};
            requests.forEach(function (entry, index) {
                var result = results[index];
                if (result.ok) {
                    state.operations[entry.key] = result.payload;
                    if (entry.key === "signalDeviceName" && getByPath(result.payload, "device_name", "")) {
                        setByPath(state.forms, "messaging.signal.device_name", getByPath(result.payload, "device_name", ""));
                    }
                } else {
                    if (!state.operations[entry.key]) {
                        state.operations[entry.key] = null;
                    }
                    state.operations.errors[entry.key] = result.error;
                }
            });
            state.operations.stale = false;
            syncPairingModalMonitor();
        } finally {
            state.operations.loading = false;
            renderApp(renderOptions);
        }
    }

    async function ensureMediaDeviceData(force, options) {
        options = options || {};
        var renderOptions = options.passive ? { passive: true } : undefined;
        if (state.operations.mediaDevicesLoading) {
            return;
        }
        if (!force && state.operations.audioDevices && state.operations.cameraDevices && state.operations.monitors) {
            return;
        }
        state.operations.mediaDevicesLoading = true;
        renderApp(renderOptions);
        var suffix = force ? "?refresh=1" : "";
        var requests = [
            { key: "audioDevices", url: "/api/webrtc/audio-devices" + suffix },
            { key: "cameraDevices", url: "/api/webrtc/camera-devices" + suffix },
            { key: "monitors", url: "/api/webrtc/monitors" + suffix }
        ];
        try {
            var results = await Promise.all(requests.map(function (entry) {
                return safeRequestJson(entry.url);
            }));
            requests.forEach(function (entry, index) {
                var result = results[index];
                if (result.ok) {
                    state.operations[entry.key] = result.payload;
                    delete state.operations.errors[entry.key];
                } else {
                    if (!state.operations[entry.key]) {
                        state.operations[entry.key] = null;
                    }
                    state.operations.errors[entry.key] = result.error;
                }
            });
        } finally {
            state.operations.mediaDevicesLoading = false;
            renderApp(renderOptions);
        }
    }

    function chatUuid(prefix) {
        var id = (window.crypto && typeof window.crypto.randomUUID === "function")
            ? window.crypto.randomUUID()
            : (Date.now().toString(36) + Math.random().toString(36).slice(2));
        return (prefix || "chat") + "-" + id;
    }

    function chatDefaultUserId() {
        var key = "autoyou.admin.chat.user";
        var value = "";
        try { value = localStorage.getItem(key) || ""; } catch (error) {}
        if (!value) {
            value = "admin-web-user";
            try { localStorage.setItem(key, value); } catch (error) {}
        }
        return value;
    }

    // The owner, as the admin page presents them everywhere: the sidebar's
    // profile name and photo. The chat user id above is only a history key.
    function chatSelfProfile() {
        var admin = getByPath(state.bootstrap, "admin", {});
        var viewer = state.chat.viewer || {};
        return {
            name: firstNonBlank([getByPath(admin, "server_name", ""), viewer.name], "AutoYou-Server"),
            avatarUrl: String(getByPath(admin, "avatar_url", "") || ""),
            surfaceLabel: String(viewer.surface_label || "This computer"),
            deviceName: String(viewer.device_name || ""),
            userId: state.chat.userId || chatDefaultUserId()
        };
    }

    function chatInitials(value) {
        var words = String(value || "").replace(/['’]/g, "").replace(/[^A-Za-z0-9]+/g, " ").trim().split(" ").filter(Boolean);
        if (!words.length) return "?";
        return (words.length === 1 ? words[0].slice(0, 2) : words[0].charAt(0) + words[1].charAt(0)).toUpperCase();
    }

    // Who a conversation is with. The server decides it from the owner id it
    // assigned; the fallback only covers servers from before identities.
    function chatIdentityFor(item) {
        var identity = item && item.identity;
        if (identity && identity.kind) return identity;
        if (!item || item.user_id === chatDefaultUserId()) {
            return { kind: "self", name: chatSelfProfile().name, detail: "You · Admin page", is_self: true };
        }
        return { kind: "device", name: item.origin || "Paired device", detail: "", is_self: false };
    }

    function chatIdentityLine(identity) {
        return identity.is_self ? String(identity.detail || "You") : [identity.name, identity.detail].filter(Boolean).join(" · ");
    }

    function chatFaceMarkup(identity, size) {
        if (identity && identity.is_self) {
            var self = chatSelfProfile();
            var photo = self.avatarUrl ? adminAssetUrl(self.avatarUrl) : "";
            return "<span class=\"ayu-chat-face ayu-chat-face-" + size + " is-self" + (photo ? " has-image" : "") + "\" aria-hidden=\"true\">" + (photo ? "<img src=\"" + escapeHtml(photo) + "\" alt=\"\">" : escapeHtml(avatarText(self.name))) + "</span>";
        }
        var kind = String((identity && identity.kind) || "device");
        return "<span class=\"ayu-chat-face ayu-chat-face-" + size + " kind-" + escapeHtml(kind) + "\" aria-hidden=\"true\">" + escapeHtml(kind === "room" ? "#" : chatInitials(identity && identity.name)) + "</span>";
    }

    function chatSelfCardMarkup() {
        var self = chatSelfProfile();
        var context = ["You", self.surfaceLabel, self.deviceName].filter(Boolean).join(" · ");
        return "<div class=\"ayu-chat-identity\" title=\"" + escapeHtml(context + ". Chats you start here are saved in history as " + self.userId + ".") + "\">" + chatFaceMarkup({ is_self: true }, "card") + "<span class=\"ayu-chat-identity-copy\"><small>" + escapeHtml(context) + "</small><strong>" + escapeHtml(self.name) + "</strong></span></div>";
    }

    function chatKindForMime(mimetype) {
        var value = String(mimetype || "").toLowerCase();
        if (value.indexOf("image/") === 0) return "image";
        if (value.indexOf("video/") === 0) return "video";
        if (value.indexOf("audio/") === 0) return "audio";
        return "file";
    }

    function chatAttachmentLabel(attachment) {
        return String((attachment || {}).filename || "attachment");
    }

    function chatReadFile(file) {
        return new Promise(function (resolve, reject) {
            var reader = new FileReader();
            reader.onload = function () {
                var result = String(reader.result || "");
                var comma = result.indexOf(",");
                var kind = chatKindForMime(file.type);
                resolve({
                    filename: file.name || "attachment",
                    mimetype: file.type || "application/octet-stream",
                    data: comma >= 0 ? result.slice(comma + 1) : result,
                    size_bytes: file.size || 0,
                    kind: kind,
                    meta: { platform: "admin-web", kind: kind }
                });
            };
            reader.onerror = function () { reject(new Error("Could not read " + (file.name || "the selected file") + ".")); };
            reader.readAsDataURL(file);
        });
    }

    function chatDataUrl(attachment) {
        if (!attachment || !attachment.data) return "";
        return "data:" + String(attachment.mimetype || "application/octet-stream") + ";base64," + attachment.data;
    }

    var CHAT_MEDIA_SOURCE_LABELS = {
        voice_note: "Voice note",
        voice_reply: "Spoken reply",
        media_reply: "Created by AutoYou",
        voice_call: "Voice call clip",
        upload: "Uploaded clip"
    };

    function chatMediaUrl(item, download) {
        var url = String((item || {}).url || "");
        if (!url || url.indexOf("/api/chat/") !== 0) return "";
        return download ? url + (url.indexOf("?") >= 0 ? "&" : "?") + "download=1" : url;
    }

    function chatAttachmentMarkup(attachment, removableIndex) {
        var item = attachment || {};
        var kind = String(item.kind || chatKindForMime(item.mimetype));
        var serverUrl = item.available === false ? "" : chatMediaUrl(item, false);
        var source = item.data ? chatDataUrl(item) : serverUrl;
        var preview = "";
        if (source && kind === "image") {
            preview = "<a class=\"ayu-chat-attachment-link\" href=\"" + escapeHtml(source) + "\" target=\"_blank\" rel=\"noopener\"><img class=\"ayu-chat-attachment-preview\" src=\"" + escapeHtml(source) + "\" alt=\"" + escapeHtml(chatAttachmentLabel(item)) + "\" loading=\"lazy\"></a>";
        } else if (source && kind === "audio") {
            preview = "<audio class=\"ayu-chat-audio\" controls preload=\"" + (item.data ? "metadata" : "none") + "\" src=\"" + escapeHtml(source) + "\"></audio>";
        } else if (source && kind === "video") {
            preview = "<video class=\"ayu-chat-video\" controls preload=\"none\" src=\"" + escapeHtml(source) + "\"></video>";
        }
        var sourceLabel = CHAT_MEDIA_SOURCE_LABELS[String(item.source || "")] || "";
        var meta = [sourceLabel, formatByteSize(item.size_bytes || item.sizeBytes || 0)].filter(Boolean).join(" · ");
        var transcript = item.transcript && item.source !== "voice_reply" ? "<q class=\"ayu-chat-attachment-transcript\">" + escapeHtml(String(item.transcript).slice(0, 280)) + "</q>" : "";
        var download = serverUrl ? "<a class=\"ayu-chat-attachment-download\" href=\"" + escapeHtml(chatMediaUrl(item, true)) + "\" download=\"" + escapeHtml(chatAttachmentLabel(item)) + "\" aria-label=\"Download " + escapeHtml(chatAttachmentLabel(item)) + "\" title=\"Download\">" + icon("download") + "</a>" : "";
        var missing = item.available === false ? "<small class=\"ayu-chat-attachment-missing\">Only the name was kept on this server</small>" : "";
        var remove = removableIndex === undefined || removableIndex === null ? "" : "<button type=\"button\" class=\"ayu-chat-attachment-remove\" data-action=\"chat-discard-attachment:" + removableIndex + "\" aria-label=\"Remove " + escapeHtml(chatAttachmentLabel(item)) + "\">×</button>";
        return "<div class=\"ayu-chat-attachment\"><div class=\"ayu-chat-attachment-icon\">" + icon(kind === "audio" ? "mic" : (kind === "video" ? "video" : (kind === "image" ? "page" : "file"))) + "</div><div class=\"ayu-chat-attachment-copy\"><strong>" + escapeHtml(chatAttachmentLabel(item)) + "</strong><small>" + escapeHtml(meta) + "</small>" + missing + transcript + preview + "</div>" + download + remove + "</div>";
    }

    function chatMessageMarkup(message) {
        var item = message || {};
        var role = item.role === "user" ? "user" : "assistant";
        var attachments = Array.isArray(item.attachments) ? item.attachments : [];
        var body = String(item.content || "").trim();
        var attachmentHtml = attachments.map(function (attachment) { return chatAttachmentMarkup(attachment); }).join("");
        var timestamp = item.timestamp ? formatTimestamp(item.timestamp) : "";
        // The owner's turns read as theirs even inside someone else's
        // conversation; everyone else keeps their own name and face.
        var counterpart = chatThreadIdentity();
        var mine = role === "user" && (counterpart.is_self || item.author === "self");
        // An answer the owner gave the device in person, not one AutoYou wrote.
        var human = role === "assistant" && item.human === true;
        var face = role === "user" ? chatFaceMarkup(mine ? { is_self: true } : counterpart, "message") : (human ? chatFaceMarkup({ is_self: true }, "message") : "<div class=\"ayu-chat-avatar\">AI</div>");
        var authorName = role === "user" ? (mine ? "You" : counterpart.name) : (human ? "You · sent to the device" : "AutoYou");
        return "<article class=\"ayu-chat-message " + role + (role === "user" && !mine ? " from-other" : "") + "\">" + face + "<div class=\"ayu-chat-message-body\"><div class=\"ayu-chat-message-meta\"><strong>" + escapeHtml(authorName) + "</strong>" + (timestamp ? "<span>" + escapeHtml(timestamp) + "</span>" : "") + "</div>" + (body ? "<div class=\"ayu-chat-bubble\"><p>" + escapeHtml(body).replace(/\n/g, "<br>") + "</p>" + attachmentHtml + "</div>" : attachmentHtml) + (item.pending ? "<small class=\"ayu-chat-pending\">Sending…</small>" : "") + (item.failed ? "<small class=\"ayu-chat-error\">Could not send</small>" : "") + "</div></article>";
    }

    function chatThreadIdentity() {
        return chatIdentityFor(state.chat.selected);
    }

    function chatVisibleSessions() {
        var query = String(state.chat.search || "").trim().toLowerCase();
        var filter = state.chat.filter || "all";
        return (state.chat.sessions || []).filter(function (item) {
            var identity = chatIdentityFor(item);
            var haystack = [item.title, item.auto_title, item.preview, item.origin, identity.name, identity.detail, item.user_id, item.session_id].join(" ").toLowerCase();
            if (query && haystack.indexOf(query) < 0) return false;
            if (filter === "voice" && !item.has_voice) return false;
            if (filter === "files" && !item.has_files) return false;
            if (filter === "training" && !item.has_training) return false;
            return true;
        });
    }

    function chatEmptyHistoryMarkup() {
        var filter = state.chat.filter || "all";
        var copy = {
            all: ["No conversations yet", "Start a chat, attach a file, or begin a voice note."],
            voice: ["No voice conversations", "Voice calls, voice notes, and spoken replies from any device appear here."],
            files: ["No conversations with files", "Photos, documents, and files sent from any device appear here."],
            training: ["No linked training clips", "With voice-training capture on in Speech settings, clips from calls appear here."]
        }[filter] || ["No conversations yet", ""];
        if (String(state.chat.search || "").trim()) copy = ["No matches", "Try a different name, message, or device."];
        return "<div class=\"ayu-chat-history-empty\"><span>⌁</span><strong>" + escapeHtml(copy[0]) + "</strong><p>" + escapeHtml(copy[1]) + "</p></div>";
    }

    function chatTrainingLibraryRow() {
        var summary = state.chat.trainingSummary || {};
        var count = Number(summary.count || 0);
        if (!count && state.chat.filter !== "training") return "";
        var active = state.chat.view === "training";
        var detail = count
            ? count + " clip" + (count === 1 ? "" : "s") + (summary.unlinked_count ? " · " + summary.unlinked_count + " not linked to a chat" : "")
            : (summary.capture_enabled ? "Capture is on · no clips yet" : "Capture is off in Speech settings");
        return "<button type=\"button\" class=\"ayu-chat-session-row ayu-chat-training-row" + (active ? " active" : "") + "\" data-action=\"chat-open-training\"><span class=\"ayu-chat-session-glyph\">" + icon("wave") + "</span><span class=\"ayu-chat-session-copy\"><strong>Voice training clips</strong><small>" + escapeHtml(detail) + "</small></span></button>";
    }

    function chatSessionBadges(item) {
        var badges = [];
        if (item.voice_count || item.has_voice) badges.push("<span class=\"ayu-chat-session-flag\" title=\"Voice\">" + icon("mic") + (item.voice_count ? escapeHtml(String(item.voice_count)) : "") + "</span>");
        if (item.file_count || item.has_files) badges.push("<span class=\"ayu-chat-session-flag\" title=\"Files\">" + icon("file") + (item.file_count ? escapeHtml(String(item.file_count)) : "") + "</span>");
        if (item.training_count) badges.push("<span class=\"ayu-chat-session-flag\" title=\"Voice training clips\">" + icon("wave") + escapeHtml(String(item.training_count)) + "</span>");
        return badges.join("");
    }

    function chatSessionListMarkup() {
        var sessions = chatVisibleSessions();
        var library = chatTrainingLibraryRow();
        if (!sessions.length) {
            return (library ? "<div class=\"ayu-chat-history-group\">" + library + "</div>" : "") + chatEmptyHistoryMarkup();
        }
        var groups = {};
        sessions.forEach(function (item) {
            var date = String(item.last_activity || item.created_at || "").slice(0, 10) || "Earlier";
            (groups[date] = groups[date] || []).push(item);
        });
        return (library ? "<div class=\"ayu-chat-history-group\">" + library + "</div>" : "") + Object.keys(groups).map(function (date) {
            var rows = groups[date].map(function (item) {
                var selected = state.chat.view !== "training" && state.chat.selected && state.chat.selected.session_id === item.session_id && state.chat.selected.user_id === item.user_id;
                var ids = " data-user-id=\"" + escapeHtml(item.user_id) + "\" data-session-id=\"" + escapeHtml(item.session_id) + "\"";
                var identity = chatIdentityFor(item);
                var subtitle = [chatIdentityLine(identity), item.custom_title && item.auto_title && item.auto_title !== item.title ? "“" + item.auto_title + "”" : ""].filter(Boolean).join(" · ");
                return "<div class=\"ayu-chat-session-item" + (selected ? " active" : "") + "\"><button type=\"button\" class=\"ayu-chat-session-row" + (selected ? " active" : "") + (identity.is_self ? " is-self" : "") + "\" data-action=\"chat-open-session\" data-identity-kind=\"" + escapeHtml(identity.kind) + "\"" + ids + ">" + chatFaceMarkup(identity, "row") + "<span class=\"ayu-chat-session-copy\"><strong>" + escapeHtml(item.title || "Conversation") + (item.custom_title ? "<span class=\"ayu-chat-session-named\" title=\"Named on this server\">" + icon("edit") + "</span>" : "") + "</strong><small>" + escapeHtml(item.preview || "No preview") + "</small><em>" + escapeHtml(subtitle || item.user_id || "") + "</em><span class=\"ayu-chat-session-flags\">" + chatSessionBadges(item) + "</span></span><span class=\"ayu-chat-session-count\">" + escapeHtml(String(item.message_count || 0)) + "</span></button><button type=\"button\" class=\"ayu-chat-session-rename\" data-action=\"chat-rename-session\"" + ids + " aria-label=\"Rename " + escapeHtml(item.title || "conversation") + "\" title=\"Rename\">" + icon("edit") + "</button></div>";
            }).join("");
            return "<div class=\"ayu-chat-history-group\"><div class=\"ayu-chat-history-group-label\">" + escapeHtml(date) + "</div>" + rows + "</div>";
        }).join("");
    }

    async function ensureChatData(force) {
        if (state.chat.loading) return;
        if (!state.chat.userId) state.chat.userId = chatDefaultUserId();
        if (!force && state.chat.loaded) return;
        state.chat.loading = true;
        renderApp({ passive: true });
        try {
            var response = await requestJson("/api/chat/sessions?limit=100");
            state.chat.sessions = Array.isArray(response && response.sessions) ? response.sessions : [];
            state.chat.viewer = (response && response.viewer) || state.chat.viewer;
            state.chat.trainingSummary = (response && response.voice_training) || null;
            if (state.chat.selected) {
                var fresh = state.chat.sessions.find(function (item) { return item.user_id === state.chat.selected.user_id && item.session_id === state.chat.selected.session_id; });
                if (fresh) state.chat.selected = Object.assign({}, state.chat.selected, fresh);
            }
            state.chat.loaded = true;
        } catch (error) {
            setNotice("error", error.message || "Chat history is unavailable.");
        } finally {
            state.chat.loading = false;
            renderApp({ passive: true });
        }
    }

    async function loadChatSession(userId, sessionId) {
        state.chat.loading = true;
        state.chat.view = "thread";
        state.chat.renaming = false;
        state.chat.pendingTitle = "";
        state.chat.userId = userId || chatDefaultUserId();
        state.chat.sessionId = sessionId || "";
        state.chat.messages = [];
        state.chat.files = [];
        state.chat.training = [];
        renderApp();
        try {
            var response = await requestJson("/api/chat/session?user_id=" + encodeURIComponent(state.chat.userId) + "&session_id=" + encodeURIComponent(state.chat.sessionId));
            state.chat.messages = Array.isArray(response && response.messages) ? response.messages : [];
            state.chat.files = Array.isArray(response && response.files) ? response.files : [];
            state.chat.training = Array.isArray(response && response.voice_training) ? response.voice_training : [];
            var listed = (state.chat.sessions || []).find(function (item) { return item.user_id === state.chat.userId && item.session_id === state.chat.sessionId; });
            state.chat.selected = Object.assign({ user_id: state.chat.userId, session_id: state.chat.sessionId, title: "Conversation" }, listed || {}, {
                title: (response && response.title) || (listed && listed.title) || "Conversation",
                auto_title: (response && response.auto_title) || (listed && listed.auto_title) || "",
                custom_title: Boolean(response && response.custom_title),
                identity: (listed && listed.identity) || (response && response.identity) || null
            });
        } catch (error) {
            setNotice("error", error.message || "Conversation could not be opened.");
        } finally {
            state.chat.loading = false;
            renderApp();
        }
    }

    async function loadChatTrainingLibrary() {
        state.chat.view = "training";
        state.chat.renaming = false;
        state.chat.loading = true;
        renderApp();
        try {
            state.chat.trainingLibrary = await requestJson("/api/chat/voice-training?limit=300");
        } catch (error) {
            setNotice("error", error.message || "Voice training clips are unavailable.");
        } finally {
            state.chat.loading = false;
            renderApp();
        }
    }

    function chatStartRename() {
        if (state.chat.view === "training") return;
        state.chat.renaming = true;
        state.chat.titleDraft = state.chat.selected ? String(state.chat.selected.title || "") : String(state.chat.pendingTitle || "");
        renderApp();
        window.setTimeout(function () {
            var input = root && root.querySelector("[data-role=\"chat-title-input\"]");
            if (input) { input.focus(); input.select(); }
        }, 0);
    }

    function chatCancelRename() {
        state.chat.renaming = false;
        state.chat.titleDraft = "";
        renderApp();
    }

    async function chatSaveTitle(useAutomatic) {
        var input = root && root.querySelector("[data-role=\"chat-title-input\"]");
        var title = useAutomatic ? "" : String(input ? input.value : state.chat.titleDraft || "").replace(/\s+/g, " ").trim().slice(0, 120);
        if (!state.chat.selected) {
            // Not on the server yet: the name is saved with the first message.
            state.chat.pendingTitle = title;
            state.chat.renaming = false;
            renderApp();
            return;
        }
        var target = state.chat.selected;
        var response = await postJson("/api/chat/session/title", { user_id: target.user_id, session_id: target.session_id, title: title });
        var applied = {
            title: response.title || target.auto_title || "Conversation",
            custom_title: Boolean(response.custom_title)
        };
        state.chat.selected = Object.assign({}, target, applied);
        state.chat.sessions = (state.chat.sessions || []).map(function (item) {
            return item.user_id === target.user_id && item.session_id === target.session_id
                ? Object.assign({}, item, applied, { title: response.title || item.auto_title || "Conversation" })
                : item;
        });
        state.chat.renaming = false;
        state.chat.titleDraft = "";
        setNotice("success", applied.custom_title ? "Conversation renamed on this server." : "Automatic name restored.");
    }

    function chatNewConversation() {
        state.chat.userId = chatDefaultUserId();
        state.chat.sessionId = chatUuid("admin-chat");
        state.chat.selected = null;
        state.chat.view = "thread";
        state.chat.renaming = false;
        state.chat.pendingTitle = "";
        state.chat.messages = [];
        state.chat.files = [];
        state.chat.training = [];
        state.chat.composer = "";
        state.chat.attachments = [];
        renderApp();
        window.setTimeout(function () {
            var input = root && root.querySelector("[data-role=\"chat-input\"]");
            if (input) input.focus();
        }, 0);
    }

    async function chatReadSelectedFiles(files) {
        var selected = Array.prototype.slice.call(files || []);
        var accepted = [];
        for (var index = 0; index < selected.length; index += 1) {
            if (selected[index].size > 25 * 1024 * 1024) {
                setNotice("error", selected[index].name + " is larger than the 25 MB browser limit.");
                continue;
            }
            accepted.push(await chatReadFile(selected[index]));
        }
        state.chat.attachments = state.chat.attachments.concat(accepted);
        renderApp();
    }

    function chatRecorderMime() {
        var candidates = ["audio/webm;codecs=opus", "audio/webm", "audio/mp4", "audio/ogg;codecs=opus"];
        for (var index = 0; index < candidates.length; index += 1) {
            if (!window.MediaRecorder || !MediaRecorder.isTypeSupported || MediaRecorder.isTypeSupported(candidates[index])) return candidates[index];
        }
        return "";
    }

    async function chatToggleRecording() {
        if (state.chat.recording && state.chat.recording.phase === "recording") {
            state.chat.recording.phase = "stopping";
            try { state.chat.recording.recorder.stop(); } catch (error) {}
            renderApp();
            return;
        }
        if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia || !window.MediaRecorder) {
            setNotice("error", "This browser cannot record voice notes.");
            return;
        }
        try {
            var stream = await navigator.mediaDevices.getUserMedia({ audio: true });
            var mime = chatRecorderMime();
            var recorder = new MediaRecorder(stream, mime ? { mimeType: mime } : undefined);
            var recording = { phase: "recording", startedAt: Date.now(), recorder: recorder, stream: stream, chunks: [] };
            state.chat.recording = recording;
            recorder.ondataavailable = function (event) { if (event.data && event.data.size) recording.chunks.push(event.data); };
            recorder.onstop = function () {
                var blob = new Blob(recording.chunks, { type: recorder.mimeType || mime || "audio/webm" });
                stream.getTracks().forEach(function (track) { track.stop(); });
                var file = new File([blob], "voice-note." + (blob.type.indexOf("ogg") >= 0 ? "ogg" : "webm"), { type: blob.type || "audio/webm" });
                chatReadFile(file).then(function (attachment) {
                    attachment.kind = "audio";
                    attachment.meta.kind = "audio";
                    attachment.duration_seconds = Math.max(1, Math.round((Date.now() - recording.startedAt) / 1000));
                    state.chat.attachments.push(attachment);
                    state.chat.recording = null;
                    renderApp();
                }).catch(function (error) {
                    state.chat.recording = null;
                    setNotice("error", error.message || "Voice note could not be saved.");
                    renderApp();
                });
            };
            recorder.start(250);
            renderApp();
        } catch (error) {
            setNotice("error", error.name === "NotAllowedError" ? "Microphone access was blocked." : (error.message || "Voice note could not start."));
        }
    }

    function chatSendCallControl(payload) {
        var call = state.chat.call;
        if (!call.dataChannel || call.dataChannel.readyState !== "open") return false;
        var envelope = {
            header: {
                message_id: chatUuid("message"),
                message_type: "voice_call_control",
                timestamp: Date.now() / 1000,
                session_id: call.sessionId,
                user_id: state.chat.userId || chatDefaultUserId()
            },
            payload: Object.assign({ platform: "admin-web", timestamp_ms: Date.now() }, payload || {})
        };
        call.dataChannel.send(JSON.stringify(envelope));
        return true;
    }

    function chatSendChunkAck(frame) {
        var info = frame && frame.chunk_info;
        if (!info) return;
        var call = state.chat.call;
        if (!call.dataChannel || call.dataChannel.readyState !== "open") return;
        call.dataChannel.send(JSON.stringify({
            header: { message_id: chatUuid("ack"), message_type: "chunk_ack", timestamp: Date.now() / 1000, session_id: call.sessionId, user_id: state.chat.userId || chatDefaultUserId() },
            payload: { chunk_id: info.chunk_id, chunk_index: info.chunk_index, total_chunks: info.total_chunks, checksum: info.checksum, original_message_id: (frame.payload || {}).original_message_id }
        }));
    }

    function chatBase64Bytes(value) {
        var raw = atob(String(value || ""));
        var bytes = new Uint8Array(raw.length);
        for (var index = 0; index < raw.length; index += 1) bytes[index] = raw.charCodeAt(index);
        return bytes;
    }

    function chatHandleCallFrame(frame) {
        if (!frame || !frame.header) return;
        var type = frame.header.message_type;
        if (type === "ping") {
            var pingCall = state.chat.call;
            if (pingCall.dataChannel && pingCall.dataChannel.readyState === "open") {
                pingCall.dataChannel.send(JSON.stringify({ header: { message_id: chatUuid("pong"), message_type: "pong", timestamp: Date.now() / 1000, session_id: pingCall.sessionId, user_id: state.chat.userId || chatDefaultUserId() }, payload: { ping_id: frame.header.message_id, fallback_text: "Pong" } }));
            }
            return;
        }
        if (type === "chunk") {
            chatSendChunkAck(frame);
            var info = frame.chunk_info;
            var chunk = frame.payload && frame.payload.chunk_data_b64;
            if (!info || !chunk) return;
            var chunks = state.chat.call.chunks[info.chunk_id] || { parts: [], total: Number(info.total_chunks || 0) };
            chunks.parts[Number(info.chunk_index)] = chunk;
            state.chat.call.chunks[info.chunk_id] = chunks;
            if (chunks.parts.filter(Boolean).length === chunks.total) {
                var raw = "";
                chunks.parts.forEach(function (part) { raw += String.fromCharCode.apply(null, chatBase64Bytes(part)); });
                delete state.chat.call.chunks[info.chunk_id];
                try {
                    var encoded = new Uint8Array(raw.length);
                    for (var byteIndex = 0; byteIndex < raw.length; byteIndex += 1) encoded[byteIndex] = raw.charCodeAt(byteIndex);
                    chatHandleCallFrame(JSON.parse(new TextDecoder().decode(encoded)));
                } catch (error) {}
            }
            return;
        }
        if (type === "voice_call_control") {
            var payload = frame.payload || {};
            var event = String(payload.event || "").toLowerCase();
            if (event === "readiness") {
                state.chat.call.readiness = String(payload.state || "unknown");
                state.chat.call.status = String(payload.detail || "Voice pipeline status updated.");
            } else if (event === "server_capabilities") {
                state.chat.call.status = "Voice pipeline connected. Microphone is live.";
                state.chat.call.readiness = "ready";
            }
            renderApp({ passive: true });
            return;
        }
        if (type === "chat") {
            var chatPayload = frame.payload || {};
            var metadata = chatPayload.metadata || {};
            var fromUser = Boolean(metadata.is_from_user || metadata.is_transcription || (frame.header.session_id && frame.header.session_id === frame.header.user_id));
            state.chat.messages.push({ role: fromUser ? "user" : "assistant", author: fromUser ? "self" : "", content: String(chatPayload.message || chatPayload.text || ""), timestamp: Number(frame.header.timestamp || 0) * 1000, attachments: Array.isArray(chatPayload.context) ? chatPayload.context.reduce(function (all, group) { return all.concat(group.attachments || []); }, []) : [] });
            renderApp({ passive: true });
        }
    }

    async function startAdminCall() {
        var call = state.chat.call;
        if (call.phase !== "idle") return;
        if (!window.RTCPeerConnection || !navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
            setNotice("error", "This browser does not support voice calls.");
            return;
        }
        state.chat.userId = state.chat.userId || chatDefaultUserId();
        call.phase = "starting";
        call.status = "Requesting microphone…";
        call.readiness = "warming";
        call.sessionId = chatUuid("admin-call");
        renderApp();
        try {
            var stream = await navigator.mediaDevices.getUserMedia({ audio: true, video: false });
            var pc = new RTCPeerConnection({ iceServers: [] });
            var dataChannel = pc.createDataChannel("chat", { ordered: true });
            call.localStream = stream;
            call.pc = pc;
            call.dataChannel = dataChannel;
            stream.getAudioTracks().forEach(function (track) { pc.addTrack(track, stream); });
            pc.ontrack = function (event) {
                call.remoteStream = event.streams && event.streams[0] ? event.streams[0] : call.remoteStream;
                var audio = root && root.querySelector("[data-role=\"chat-call-audio\"]");
                if (audio && call.remoteStream) audio.srcObject = call.remoteStream;
            };
            pc.onconnectionstatechange = function () {
                if (["failed", "disconnected", "closed"].indexOf(pc.connectionState) >= 0 && call.phase !== "idle") {
                    call.status = "Voice call disconnected.";
                    endAdminCall(false).catch(function () {});
                }
                renderApp({ passive: true });
            };
            dataChannel.onopen = function () {
                call.phase = "active";
                call.status = "Connected · waiting for voice pipeline";
                chatSendCallControl({ event: "call_state", active: true });
                renderApp({ passive: true });
            };
            dataChannel.onmessage = function (event) {
                try { chatHandleCallFrame(JSON.parse(typeof event.data === "string" ? event.data : "")); } catch (error) {}
            };
            dataChannel.onclose = function () { if (call.phase !== "idle") endAdminCall(false).catch(function () {}); };
            var offer = await pc.createOffer();
            await pc.setLocalDescription(offer);
            await new Promise(function (resolve) {
                if (pc.iceGatheringState === "complete") return resolve();
                var timer = window.setTimeout(resolve, 5000);
                pc.onicegatheringstatechange = function () {
                    if (pc.iceGatheringState === "complete") { window.clearTimeout(timer); resolve(); }
                };
            });
            var response = await postJson("/api/chat/call/offer", {
                session_id: call.sessionId,
                user_id: state.chat.userId,
                offer: { type: pc.localDescription.type, sdp: pc.localDescription.sdp, iceServers: [] }
            });
            await pc.setRemoteDescription(response.answer || response);
            call.status = "Secure voice link established.";
            renderApp({ passive: true });
        } catch (error) {
            call.status = error.message || "Voice call could not start.";
            await endAdminCall(true);
            setNotice("error", call.status);
        }
    }

    async function endAdminCall(notifyServer) {
        var call = state.chat.call;
        var callId = call.sessionId;
        if (call.dataChannel && call.dataChannel.readyState === "open" && notifyServer !== false) {
            try { chatSendCallControl({ event: "call_state", active: false }); } catch (error) {}
        }
        if (call.localStream) call.localStream.getTracks().forEach(function (track) { track.stop(); });
        if (call.pc) { try { call.pc.close(); } catch (error) {} }
        if (notifyServer !== false && callId) {
            try { await postJson("/api/chat/call/close", { session_id: callId, user_id: state.chat.userId || chatDefaultUserId() }); } catch (error) {}
        }
        state.chat.call = { phase: "idle", status: "Voice pipeline is waiting for a call.", readiness: "unknown", muted: false, sessionId: "", pc: null, dataChannel: null, localStream: null, remoteStream: null, chunks: {} };
        renderApp({ passive: true });
    }

    function renderChatCallCard() {
        var call = state.chat.call;
        if (call.phase === "idle") return "";
        var readinessTone = call.readiness === "ready" ? "green" : (call.readiness === "unavailable" ? "red" : "amber");
        return "<section class=\"ayu-chat-call-card\"><audio data-role=\"chat-call-audio\" autoplay></audio><div class=\"ayu-chat-call-orb\"><span></span><span></span><span></span></div><div class=\"ayu-chat-call-copy\"><span class=\"ayu-chat-eyebrow\">Voice call · microphone only</span><h2>Talk to AutoYou</h2><p>" + escapeHtml(call.status || "Connecting…") + "</p><div class=\"ayu-chat-call-status\">" + badge(String(call.readiness || "warming"), readinessTone) + "<span>STT + TTS stays on the server</span></div></div><div class=\"ayu-chat-call-controls\">" + button(call.muted ? "Unmute" : "Mute", "chat-call-mute", call.muted ? "secondary" : "ghost", "mic", "sm", "aria-pressed=\"" + String(Boolean(call.muted)) + "\"") + button("Stop talking", "chat-call-stop", "secondary", "stop", "sm") + button("End call", "chat-call-end", "danger", "close", "sm") + "</div></section>";
    }

    function chatTrainingClipMarkup(clip, showConversation) {
        var item = clip || {};
        var linked = item.session_id ? (state.chat.sessions || []).find(function (session) { return session.user_id === item.user_id && session.session_id === item.session_id; }) : null;
        var warnings = (item.quality_warnings || []).map(function (warning) { return badge(String(warning).replace(/_/g, " "), "amber"); }).join("");
        var status = item.training_eligible ? badge("Ready for training", "green") : "";
        var open = showConversation && item.session_id
            ? "<button type=\"button\" class=\"ayu-chat-clip-open\" data-action=\"chat-open-session\" data-user-id=\"" + escapeHtml(item.user_id) + "\" data-session-id=\"" + escapeHtml(item.session_id) + "\">" + escapeHtml(linked ? linked.title : "Open conversation") + "</button>"
            : (showConversation ? "<span class=\"ayu-chat-clip-unlinked\">Not linked to a conversation</span>" : "");
        var player = item.available === false
            ? "<small class=\"ayu-chat-attachment-missing\">Recording file is missing</small>"
            : "<audio class=\"ayu-chat-audio\" controls preload=\"none\" src=\"" + escapeHtml(chatMediaUrl(item, false)) + "\"></audio>";
        return "<article class=\"ayu-chat-clip\"><div class=\"ayu-chat-clip-head\"><span class=\"ayu-chat-attachment-icon\">" + icon("wave") + "</span><div><strong>" + escapeHtml(CHAT_MEDIA_SOURCE_LABELS[item.source] || "Voice clip") + "</strong><small>" + escapeHtml(formatTimestamp(item.timestamp)) + (item.duration_seconds ? " · " + escapeHtml(Number(item.duration_seconds).toFixed(1)) + "s" : "") + "</small></div>" + (item.available === false ? "" : "<a class=\"ayu-chat-attachment-download\" href=\"" + escapeHtml(chatMediaUrl(item, true)) + "\" download=\"" + escapeHtml(item.filename || "voice-clip.wav") + "\" title=\"Download\" aria-label=\"Download clip\">" + icon("download") + "</a>") + "</div>" + (item.transcript ? "<q class=\"ayu-chat-attachment-transcript\">" + escapeHtml(item.transcript) + "</q>" : "") + player + "<div class=\"ayu-chat-clip-meta\">" + status + warnings + open + "</div></article>";
    }

    function chatFilesPanelMarkup() {
        var chat = state.chat;
        var files = chat.files || [];
        var training = chat.training || [];
        var total = files.length + training.length;
        if (!total) return "";
        var audioFiles = files.filter(function (item) { return item.kind === "audio"; }).length;
        var otherFiles = files.length - audioFiles;
        var voiceCount = audioFiles + training.length;
        var summary = [
            voiceCount ? voiceCount + " voice" : "",
            otherFiles ? otherFiles + (otherFiles === 1 ? " file" : " files") : ""
        ].filter(Boolean).join(" · ");
        var body = chat.filesOpen
            ? "<div class=\"ayu-chat-files-body\">" + files.map(function (item) {
                return "<div class=\"ayu-chat-files-row\"><span class=\"ayu-chat-files-when\">" + escapeHtml(item.role === "assistant" ? "AutoYou" : "Sent") + " · " + escapeHtml(formatTimestamp(item.timestamp)) + "</span>" + chatAttachmentMarkup(item) + "</div>";
            }).join("") + (training.length ? "<div class=\"ayu-chat-files-subhead\">Voice training clips from this conversation</div>" + training.map(function (clip) { return chatTrainingClipMarkup(clip, false); }).join("") : "") + "</div>"
            : "";
        return "<section class=\"ayu-chat-files" + (chat.filesOpen ? " open" : "") + "\"><button type=\"button\" class=\"ayu-chat-files-toggle\" data-action=\"chat-files-toggle\" aria-expanded=\"" + String(Boolean(chat.filesOpen)) + "\">" + icon("file") + "<strong>Files &amp; voice</strong><span>" + escapeHtml(summary || String(total)) + "</span><em>" + (chat.filesOpen ? "Hide" : "Show all") + "</em></button>" + body + "</section>";
    }

    function chatTrainingLibraryMarkup() {
        var library = state.chat.trainingLibrary || {};
        var clips = Array.isArray(library.captures) ? library.captures : [];
        if (state.chat.loading && !clips.length) {
            return '<div class="ayu-chat-empty"><div class="ayu-spinner"></div><span>Loading voice training clips…</span></div>';
        }
        var intro = "<div class=\"ayu-chat-training-intro\">" + (library.capture_enabled
            ? badge("Capture on", "green") + "<span>Clear phrases from voice calls are kept for the Voice Training app. Clips spoken in a conversation link back to it.</span>"
            : badge("Capture off", "gray") + "<span>Turn on voice-training capture in Speech settings to collect clips from calls. Existing clips stay listed here.</span>") + "</div>";
        if (!clips.length) {
            return intro + '<div class="ayu-chat-empty"><span>No voice training clips yet.</span></div>';
        }
        return intro + clips.map(function (clip) { return chatTrainingClipMarkup(clip, true); }).join("");
    }

    function chatThreadHeadMarkup(serverName) {
        var chat = state.chat;
        if (chat.view === "training") {
            var library = chat.trainingLibrary || {};
            return '<header class="ayu-chat-thread-head"><div><span class="ayu-chat-eyebrow">Voice training</span><h2>Voice training clips</h2><p>' + escapeHtml(String(library.count || 0)) + ' clip' + (Number(library.count || 0) === 1 ? "" : "s") + ' on this server</p></div><div class="ayu-chat-thread-actions">' + button("Refresh", "chat-open-training", "ghost", "refresh", "sm") + '</div></header>';
        }
        var selected = chat.selected;
        var title = selected && selected.title ? selected.title : (chat.pendingTitle || serverName);
        var eyebrow = selected ? (chatIdentityLine(chatIdentityFor(selected)) || selected.origin || "Session") : (chat.pendingTitle ? "New conversation" : "Ready when you are");
        var sessionLine = chat.sessionId ? "SessionID " + escapeHtml(chat.sessionId) : "A fresh conversation will be saved automatically";
        if (selected && selected.custom_title && selected.auto_title && selected.auto_title !== selected.title) {
            sessionLine = "Started with “" + escapeHtml(selected.auto_title) + "” · " + sessionLine;
        }
        var threadBadge = chat.sessionId && chat.messages.length ? badge(String(chat.messages.length) + " messages", "blue") : badge("Private admin session", "gray");
        var heading;
        if (chat.renaming) {
            heading = '<div class="ayu-chat-title-form" role="group" aria-label="Rename conversation"><input data-role="chat-title-input" type="text" maxlength="120" value="' + escapeHtml(chat.titleDraft || "") + '" placeholder="' + escapeHtml((selected && selected.auto_title) || "Name this conversation") + '" aria-label="Conversation name"><div class="ayu-chat-title-actions">' + button("Save", "chat-title-save", "primary", "check", "sm") + button("Cancel", "chat-title-cancel", "ghost", "", "sm") + (selected && selected.custom_title ? button("Use automatic name", "chat-title-reset", "ghost", "refresh", "sm") : "") + '</div></div><p>Names stay on this server; connected devices keep their own.</p>';
        } else {
            heading = '<div class="ayu-chat-title-line"><h2>' + escapeHtml(title) + '</h2><button type="button" class="ayu-chat-title-edit" data-action="chat-title-edit" aria-label="Rename conversation" title="Rename conversation">' + icon("edit") + '</button></div><p>' + sessionLine + '</p>';
        }
        return '<header class="ayu-chat-thread-head"><div class="ayu-chat-thread-title"><span class="ayu-chat-eyebrow">' + escapeHtml(eyebrow) + '</span>' + heading + '</div><div class="ayu-chat-thread-actions">' + threadBadge + '</div></header>';
    }

    function renderChatHistoryScreen() {
        var chat = state.chat;
        var serverName = firstNonBlank([
            getByPath(state.bootstrap, "admin.server_name", ""),
            getByPath(bootstrapConfig(), "server.name", "")
        ], "AutoYou-Server");
        var filters = ["all", "voice", "files", "training"].map(function (filter) {
            var label = { all: "All", voice: "Voice", files: "Files", training: "Training" }[filter];
            return '<button type="button" class="' + (chat.filter === filter ? "active" : "") + '" data-action="chat-filter:' + filter + '">' + label + '</button>';
        }).join("");
        var messageMarkup = chat.view === "training"
            ? chatTrainingLibraryMarkup()
            : (chat.loading && !chat.messages.length
                ? '<div class="ayu-chat-empty"><div class="ayu-spinner"></div><span>Opening conversation…</span></div>'
                : chatFilesPanelMarkup() + (chat.messages.length
                    ? chat.messages.map(chatMessageMarkup).join("")
                    : ""));
        var composerAttachmentMarkup = chat.attachments.map(function (attachment, index) { return chatAttachmentMarkup(attachment, index); }).join("");
        var recordingMarkup = chat.recording
            ? '<div class="ayu-chat-recording"><span class="ayu-chat-recording-dot"></span><strong>' + (chat.recording.phase === "stopping" ? "Saving voice note…" : "Recording voice note") + '</strong><span>' + Math.max(0, Math.round((Date.now() - chat.recording.startedAt) / 1000)) + 's</span></div>'
            : "";
        var inputValue = escapeHtml(chat.composer || "");
        var counterpart = chatThreadIdentity();
        // ↑ asks AutoYou inside this conversation; only "Send to" reaches the device itself.
        var deviceLive = !counterpart.is_self && chat.view !== "training" && Boolean(chat.selected && chat.selected.live);
        var replyContextMarkup = counterpart.is_self || chat.view === "training"
            ? ""
            : '<div class="ayu-chat-reply-context">' + chatFaceMarkup({ is_self: true }, "row") + '<span>You are in the conversation with <strong>' + escapeHtml(counterpart.name) + '</strong>. ↑ asks AutoYou here. '
                + (deviceLive ? '<em>Send to ' + escapeHtml(counterpart.name) + '</em> delivers your own words to that device.' : escapeHtml(counterpart.name) + ' is not connected, so your own words cannot reach it now.') + '</span></div>';
        var deviceReplyMarkup = deviceLive
            ? '<button type="button" class="ayu-chat-device-reply" data-action="chat-reply-device" title="Deliver this text to the device as your own message. AutoYou is not asked." ' + (isActionPending("chat-reply-device") ? "disabled" : "") + '>' + (isActionPending("chat-reply-device") ? "Sending…" : "Send to " + escapeHtml(counterpart.name)) + '</button>'
            : "";
        return '<div class="ayu-screen ayu-chat-screen"><div class="ayu-chat-heading"><div><span class="ayu-chat-eyebrow">AutoYou workspace</span><h1>Chat &amp; History</h1><p>One calm place for conversations, voice notes, files, and live calls with your server.</p></div><div class="ayu-chat-heading-actions"><span class="ayu-chat-server-pill"><span></span>Server connected</span>'
            + button("New chat", "chat-new", "primary", "plus", "sm") + '</div></div><div class="ayu-chat-workspace"><aside class="ayu-chat-history"><div class="ayu-chat-history-top"><div><span class="ayu-chat-eyebrow">Your workspace</span><h2>Conversations</h2></div>'
            + button("Refresh", "chat-refresh", "ghost", "refresh", "sm") + '</div><label class="ayu-chat-search"><span>⌕</span><input data-role="chat-search" type="search" value="' + escapeHtml(chat.search || "") + '" placeholder="Search messages and sessions" aria-label="Search conversations"></label><div class="ayu-chat-filters">' + filters + '</div><div class="ayu-chat-history-list">'
            + (chat.loading && !chat.sessions.length ? '<div class="ayu-chat-history-loading"><div class="ayu-spinner"></div>Loading history…</div>' : chatSessionListMarkup())
            + '</div>' + chatSelfCardMarkup() + '</aside><section class="ayu-chat-thread">' + chatThreadHeadMarkup(serverName) + '<div class="ayu-chat-transcript">'
            + renderChatCallCard() + messageMarkup + '</div><div class="ayu-chat-composer"' + (chat.view === "training" ? " hidden" : "") + '>' + replyContextMarkup + '<div class="ayu-chat-attachment-tray">' + composerAttachmentMarkup + recordingMarkup + '</div><textarea data-role="chat-input" rows="2" placeholder="Message ' + escapeHtml(serverName) + '…">' + inputValue + '</textarea><div class="ayu-chat-composer-footer"><div class="ayu-chat-composer-tools"><button type="button" data-action="chat-file-select" aria-label="Attach file" title="Attach file">＋</button><button type="button" data-action="chat-record-toggle" class="' + (chat.recording ? "active" : "") + '" aria-label="Record voice note" title="Record voice note">♩</button><button type="button" data-action="chat-call-toggle" class="ayu-chat-call-tool" aria-label="Start voice call" title="Start voice call">◉</button><input type="file" data-role="chat-file-input" multiple accept="image/*,video/*,audio/*,.pdf,.txt,.md,.csv,.json"></div><div class="ayu-chat-composer-hint">Enter to send · Shift+Enter for a new line</div>' + deviceReplyMarkup + '<button type="button" class="ayu-chat-send" data-action="chat-send" ' + (isActionPending("chat-send") ? "disabled" : "") + '>' + (isActionPending("chat-send") ? "…" : "↑") + '</button></div></div></section></div></div>';
    }

    async function sendChatTurn() {
        var input = root && root.querySelector('[data-role="chat-input"]');
        if (input) state.chat.composer = input.value;
        var text = String(state.chat.composer || "").trim();
        var attachments = state.chat.attachments.slice();
        if (!text && !attachments.length) return;
        state.chat.userId = state.chat.userId || chatDefaultUserId();
        state.chat.sessionId = state.chat.sessionId || chatUuid("admin-chat");
        var userMessage = {
            role: "user",
            author: "self",
            content: text || "Please process the attached file.",
            timestamp: Date.now(),
            attachments: attachments,
            pending: true
        };
        state.chat.messages.push(userMessage);
        state.chat.composer = "";
        state.chat.attachments = [];
        var pendingTitle = state.chat.selected ? "" : String(state.chat.pendingTitle || "");
        state.chat.selected = state.chat.selected || {
            user_id: state.chat.userId,
            session_id: state.chat.sessionId,
            title: pendingTitle || text.slice(0, 96) || "Conversation",
            auto_title: text.slice(0, 96) || "Conversation",
            custom_title: Boolean(pendingTitle)
        };
        state.chat.pendingTitle = "";
        renderApp();
        try {
            var response = await postJson("/api/chat", {
                message: userMessage.content,
                user_id: state.chat.userId,
                session_id: state.chat.sessionId,
                context: attachments.length ? [{ source: "admin-web", attachments: attachments }] : [],
                metadata: { client: "admin-web", source: "admin_web" }
            });
            userMessage.pending = false;
            state.chat.sessionId = response.session_id || state.chat.sessionId;
            var responseAttachments = [];
            if (response.voice_reply_audio) {
                responseAttachments.push(Object.assign({ kind: "audio", meta: { kind: "audio" } }, response.voice_reply_audio));
            }
            (response.media_reply_attachments || []).forEach(function (attachment) {
                responseAttachments.push(Object.assign({ kind: chatKindForMime(attachment.mimetype) }, attachment));
            });
            state.chat.messages.push({
                role: "assistant",
                content: response.response || "No response returned.",
                timestamp: response.timestamp || Date.now(),
                attachments: responseAttachments
            });
            if (pendingTitle) {
                try {
                    await postJson("/api/chat/session/title", { user_id: state.chat.userId, session_id: state.chat.sessionId, title: pendingTitle });
                } catch (titleError) {
                    setNotice("error", "The message was sent, but the name was not saved: " + (titleError.message || "try renaming again."));
                }
            }
            await ensureChatData(true);
        } catch (error) {
            userMessage.pending = false;
            userMessage.failed = true;
            setNotice("error", error.message || "Chat request failed.");
            renderApp();
        }
    }

    // The owner's own words, delivered to the device this conversation is with.
    // Nothing is asked of AutoYou, and an undelivered reply is not kept.
    async function sendChatDeviceReply() {
        var input = root && root.querySelector('[data-role="chat-input"]');
        if (input) state.chat.composer = input.value;
        var text = String(state.chat.composer || "").trim();
        var target = state.chat.selected;
        if (!text || !target || !target.user_id) return;
        if (state.chat.attachments.length) {
            setNotice("error", "A message to the device carries text only. Remove the attachment, or use ↑ to ask AutoYou with it.");
            return;
        }
        var response = await postJson("/api/chat/session/reply", { user_id: target.user_id, message: text.slice(0, 4000) });
        if (!response || response.delivered !== true) {
            setNotice("error", (response && response.reason) || "The reply was not delivered.");
            return;
        }
        state.chat.composer = "";
        await ensureChatData(true);
        // It lands in the device's current conversation, which may be newer than the one open.
        await loadChatSession(target.user_id, response.session_id || target.session_id);
    }

    async function refreshTelegramSenders(showNotice) {
        var result = await safeRequestJson("/api/telegram/senders");
        if (result.ok) {
            state.operations.telegramSenders = result.payload;
            delete state.operations.errors.telegramSenders;
            if (showNotice) {
                setNotice("success", "Telegram Bot sender list refreshed.");
            } else {
                renderApp({ passive: true });
            }
        } else {
            state.operations.errors.telegramSenders = result.error || "Unable to load Telegram Bot senders.";
            if (showNotice) {
                setNotice("error", state.operations.errors.telegramSenders);
            } else {
                renderApp({ passive: true });
            }
        }
    }

    function currentSetupStepId() {
        return getByPath(SETUP_WIZARD_STEPS[state.setup.activeStep || 0], "id", "");
    }

    function shouldPollTelegramSenders() {
        return state.screen === "messaging" || (state.screen === "setup" && currentSetupStepId() === "messaging-partner");
    }

    function syncTelegramSenderPolling() {
        if (!shouldPollTelegramSenders()) {
            if (liveIntervals.telegramSenders) {
                window.clearInterval(liveIntervals.telegramSenders);
                liveIntervals.telegramSenders = null;
            }
            return;
        }
        if (liveIntervals.telegramSenders) {
            return;
        }
        liveIntervals.telegramSenders = window.setInterval(function () {
            if (!shouldPollTelegramSenders()) {
                syncTelegramSenderPolling();
                return;
            }
            if (document.querySelector("input[data-telegram-sender-id]:checked")) {
                return;
            }
            refreshTelegramSenders(false).catch(function () {});
        }, 6500);
    }

    function syncSelectedAgentWorkbenchForms(detail) {
        var selected = detail || {};
        var draftInstruction = getByPath(selected, "draft_instruction", {});
        var draftManifest = getByPath(selected, "draft.frontend_manifest", {});
        var liveFrontend = getByPath(selected, "frontend", getByPath(selected, "frontend_manifest", {}));
        var manifestSource = Object.keys(draftManifest).length ? draftManifest : liveFrontend;
        var displayName = getByPath(selected, "display_name", getByPath(selected, "agent_name", "Agent"));
        setByPath(state.forms, "agentWorkbench.draft_description", getByPath(draftInstruction, "description", ""));
        setByPath(state.forms, "agentWorkbench.draft_instructions", getByPath(draftInstruction, "instructions", ""));
        setByPath(state.forms, "agentWorkbench.frontend_ui_purpose", getByPath(manifestSource, "description", getByPath(selected, "description", "")));
        setByPath(state.forms, "agentWorkbench.frontend_app_title", getByPath(manifestSource, "title", displayName + " Website"));
        setByPath(state.forms, "agentWorkbench.frontend_local_port", getByPath(manifestSource, "recommended_port", 8094));
        setByPath(state.forms, "agentWorkbench.frontend_title", getByPath(manifestSource, "title", ""));
        setByPath(state.forms, "agentWorkbench.frontend_description", getByPath(manifestSource, "description", ""));
        setByPath(state.forms, "agentWorkbench.frontend_entry_path", getByPath(manifestSource, "entry_path", "/"));
        setByPath(state.forms, "agentWorkbench.frontend_recommended_port", getByPath(manifestSource, "recommended_port", ""));
        setByPath(state.forms, "agentWorkbench.frontend_requires_proxy", getByPath(manifestSource, "requires_proxy_registration", true) !== false);
        setByPath(state.forms, "agentWorkbench.frontend_route_mode", getByPath(selected, "frontend_control.route_mode", getByPath(manifestSource, "route_mode", "path_proxy")));
        setByPath(state.forms, "agentWorkbench.frontend_stack", getByPath(manifestSource, "frontend_stack", "fastapi_static"));
        setByPath(state.forms, "agentWorkbench.backend_stack", getByPath(manifestSource, "backend_stack", "python_fastapi"));
    }

    function applyAgentWorkbenchResponse(response, successMessage) {
        var payload = getByPath(response, "payload", null);
        if (payload && state.bootstrap) {
            setByPath(state.bootstrap, "agents", payload);
            state.agentWorkbench.payload = payload;
        }
        if (response && response.agent_name) {
            state.selectedAgentName = response.agent_name;
        }
        if (response && response.detail) {
            state.agentWorkbench.detail = response.detail;
            syncSelectedAgentWorkbenchForms(response.detail);
        }
        if (response && response.draft_test && response.agent_name) {
            state.agentWorkbench.tests[response.agent_name] = response.draft_test;
        }
        if (getByPath(response, "requires_restart", false)) {
            state.agentWorkbench.aiRestartRequired = true;
            state.agentWorkbench.aiRestartMessage = String(
                getByPath(
                    response,
                    "message",
                    "The agent registry changed. Restart AutoYou AI to update chat routing and prompt filtering."
                ) || "The agent registry changed. Restart AutoYou AI to update chat routing and prompt filtering."
            );
        }
        state.agentWorkbench.stale = false;
        var noticeMessage = successMessage !== undefined
            ? String(successMessage || "")
            : String(getByPath(response, "message", "") || "");
        if (noticeMessage) {
            setNotice(getByPath(response, "requires_restart", false) ? "warning" : "success", noticeMessage);
        } else {
            renderApp();
        }
    }

    async function ensureAgentWorkbenchDetail(force, silent) {
        var agentName = state.selectedAgentName;
        if (!agentName) {
            return;
        }
        if (!force && state.agentWorkbench.detail && !state.agentWorkbench.stale && getByPath(state.agentWorkbench.detail, "agent_name", "") === agentName) {
            return;
        }
        state.agentWorkbench.loading = true;
        renderApp();
        try {
            var response = await requestJson("/api/agents/workbench/" + encodeURIComponent(agentName));
            applyAgentWorkbenchResponse(response, silent ? "" : response.message);
        } catch (error) {
            setNotice("error", error.message || String(error));
        } finally {
            state.agentWorkbench.loading = false;
            state.agentWorkbench.stale = false;
            renderApp();
        }
    }

    function desktopAssetAgentRecord(agentName) {
        var agents = getByPath(state.desktopAssets, "payload.agents", []);
        return (Array.isArray(agents) ? agents : []).find(function (item) {
            return String(item.agent_name || "") === String(agentName || "");
        }) || null;
    }

    function activateDesktopAssetAgent(agentName) {
        var record = desktopAssetAgentRecord(agentName);
        if (!record) {
            return null;
        }
        state.desktopAssets.selectedAgent = record.agent_name;
        setByPath(state.forms, "desktopAssets.agent_name", record.agent_name);
        if (state.desktopAssets.formAgent !== record.agent_name) {
            state.desktopAssets.formAgent = record.agent_name;
            setByPath(state.forms, "desktopAssets.platform", getByPath(state.desktopAssets, "payload.platform", "windows"));
            setByPath(state.forms, "desktopAssets.app_version", "");
            setByPath(state.forms, "desktopAssets.theme", getByPath(record, "preferences.theme", "auto"));
            setByPath(state.forms, "desktopAssets.display_scale", getByPath(record, "preferences.display_scale", "auto"));
        }
        return record;
    }

    async function ensureDesktopAssets(force) {
        if (state.desktopAssets.loading || (!force && state.desktopAssets.payload)) {
            return;
        }
        state.desktopAssets.loading = true;
        state.desktopAssets.error = "";
        renderApp({ passive: true });
        try {
            state.desktopAssets.payload = await requestJson("/api/admin/desktop-assets");
            var agents = getByPath(state.desktopAssets, "payload.agents", []);
            var selected = state.desktopAssets.selectedAgent || getByPath(state.forms, "desktopAssets.agent_name", "");
            if (!desktopAssetAgentRecord(selected) && Array.isArray(agents) && agents.length) {
                selected = agents[0].agent_name;
                state.desktopAssets.formAgent = "";
            }
            if (selected) {
                activateDesktopAssetAgent(selected);
            }
        } catch (error) {
            state.desktopAssets.error = error.message || String(error);
        } finally {
            state.desktopAssets.loading = false;
            renderApp({ passive: true });
        }
    }

    async function saveDesktopAssetPreferences() {
        var agentName = state.desktopAssets.selectedAgent;
        if (!agentName) {
            throw new Error("Choose a desktop agent first.");
        }
        var preferences = await postJson("/api/admin/desktop-assets/" + encodeURIComponent(agentName) + "/preferences", {
            theme: getByPath(state.forms, "desktopAssets.theme", "auto"),
            display_scale: getByPath(state.forms, "desktopAssets.display_scale", "auto")
        });
        var record = desktopAssetAgentRecord(agentName);
        if (record) {
            record.preferences = preferences.preferences || {};
        }
        setNotice("success", "Desktop asset preferences saved for " + (record ? record.title : agentName) + ".");
        renderApp();
    }

    async function generateDesktopAssetSetupPrompt() {
        var agentName = state.desktopAssets.selectedAgent;
        if (!agentName) {
            throw new Error("Choose a desktop agent first.");
        }
        var query = new URLSearchParams({
            platform: getByPath(state.forms, "desktopAssets.platform", getByPath(state.desktopAssets, "payload.platform", "windows")),
            app_version: getByPath(state.forms, "desktopAssets.app_version", ""),
            theme: getByPath(state.forms, "desktopAssets.theme", "auto"),
            display_scale: getByPath(state.forms, "desktopAssets.display_scale", "auto")
        });
        var payload = await requestJson("/api/admin/desktop-assets/" + encodeURIComponent(agentName) + "/setup-prompt?" + query.toString());
        var record = desktopAssetAgentRecord(agentName);
        setModal({
            title: (record ? record.title : agentName) + " setup prompt",
            description: "Copy this into your own Claude, ChatGPT, or Codex workspace. AutoYou does not send the prompt to another service.",
            text: payload.prompt,
            copyValue: payload.prompt
        });
    }

    async function importDesktopAssetBundle(file) {
        var agentName = state.desktopAssets.selectedAgent;
        if (!agentName || !file) {
            return;
        }
        var form = new FormData();
        form.append("bundle", file, file.name || "desktop-assets.zip");
        var result = await requestJson("/api/admin/desktop-assets/" + encodeURIComponent(agentName) + "/import", {
            method: "POST",
            body: form
        });
        var record = desktopAssetAgentRecord(agentName);
        if (record) {
            record.asset_packs = result.asset_packs || [];
        }
        setNotice("success", "Installed " + (result.installed_pack_ids || []).length + " local desktop asset pack(s). They are stored in private app data.");
        renderApp();
    }

    async function removeDesktopAssetPack(agentName, storageId) {
        if (!agentName || !storageId) {
            return;
        }
        var result = await requestJson("/api/admin/desktop-assets/" + encodeURIComponent(agentName) + "/" + encodeURIComponent(storageId), { method: "DELETE" });
        var record = desktopAssetAgentRecord(agentName);
        if (record) {
            record.asset_packs = result.asset_packs || [];
        }
        setNotice("success", "Desktop asset pack removed from this machine.");
        renderApp();
    }

    async function refreshInteract() {
        if (state.screen !== "interact" || state.interact.loading || state.interact.saving) return;
        state.interact.loading = true;
        try {
            var payload = await requestJson("/api/screen-listen");
            if (state.screen !== "interact") return;
            var changed = JSON.stringify(payload) !== JSON.stringify(state.interact.payload);
            state.interact.payload = payload;
            if (!state.interact.dirty) state.interact.selected = (payload.selected || []).slice();
            state.interact.error = "";
            var recent = (payload.participants || []).some(function (person) {
                return Date.now() / 1000 - Number(person.last_input_at || 0) < 3;
            });
            if (changed || recent) renderApp({ passive: true });
        } catch (error) {
            if (state.screen === "interact" && state.interact.error !== error.message) {
                state.interact.error = error.message;
                renderApp({ passive: true });
            }
        } finally {
            state.interact.loading = false;
        }
    }

    function setScreen(screen, options) {
        options = options || {};
        if (interactTimer) { clearInterval(interactTimer); interactTimer = null; }
        if (state.screen === "guides" && screen !== "guides" && bootSweepState.running) {
            stopBootSweep({ report: false }).catch(function () {});
        }
        if (!options.keepSetupLink && (screen === "setup" || state.setup.linkedScreen !== screen)) {
            clearSetupLinkedScreen();
        }
        state.screen = screen;
        state.navOpen = false;
        state.profileMenuOpen = false;
        renderApp();
        scrollShellToTop();
        if (screen === "overview" || screen === "live") {
            ensureLocalPairInfo(false);
        }
        if (screen === "interact") {
            refreshInteract();
            interactTimer = window.setInterval(refreshInteract, 750);
        }
        if (screen === "setup") {
            ensureSetupData(false);
            ensureSetupStepData(getByPath(SETUP_WIZARD_STEPS[state.setup.activeStep || 0], "id", "security-basics"));
        } else if (screen === "ai") {
            ensureAiData(false);
            if (!state.aiLibrary.catalog && !state.aiLibrary.catalogLoading) {
                loadModelCatalog(false);
            }
        } else if (screen === "chat") {
            ensureChatData(false);
        } else if (screen === "agents") {
            ensureInstructions(false);
            ensureAgentWorkbenchDetail(false, true);
            ensureDesktopAssets(false);
        } else if (screen === "live" || screen === "messaging" || screen === "video" || screen === "connectivity") {
            ensureOperationsData(false, { passive: true });
            if (screen === "video") {
                ensureMediaDeviceData(false, { passive: true });
            }
        } else if (screen === "speech") {
            ensureSpeechData(false);
        } else if (screen === "security") {
            ensureSecurityData(false);
        } else if (screen === "guides") {
            ensureBootSweepScores(false).catch(function () {});
            ensureDefaultGuide();
        }
    }

    function renderStatusRows(rows) {
        return "<div class=\"ayu-status-list\">" + rows.map(function (row) {
            return "<div class=\"ayu-status-row\"><div><strong>" + escapeHtml(row.label) + "</strong>" + (row.help ? "<small>" + escapeHtml(row.help) + "</small>" : "") + "</div><div class=\"ayu-hint" + (row.mono ? " ayu-mono" : "") + "\">" + escapeHtml(row.value) + "</div></div>";
        }).join("") + "</div>";
    }

    function renderReadonlyTextarea(value, rows) {
        return "<textarea class=\"ayu-textarea ayu-mono\" rows=\"" + escapeHtml(rows || 8) + "\" readonly disabled>" + escapeHtml(value || "") + "</textarea>";
    }

    function renderSetupLinkedBanner(screen) {
        if (!screen || screen === "setup" || state.setup.linkedScreen !== screen) {
            return "";
        }
        var linkedStepIndex = Math.max(0, Math.min(Number(state.setup.linkedStep) || 0, SETUP_WIZARD_STEPS.length - 1));
        var linkedStep = SETUP_WIZARD_STEPS[linkedStepIndex] || null;
        var stepSummary = linkedStep
            ? ("Opened from Setup & Boot step " + (linkedStepIndex + 1) + ": " + linkedStep.label + ". Return to the checklist when this screen is done.")
            : "Opened from Setup & Boot. Return to the checklist when this screen is done.";
        return "<div class=\"ayu-note ayu-note-green\"><strong>Setup handoff:</strong> " + escapeHtml(stepSummary) + "<div class=\"ayu-inline-actions\">" + button("Return to Setup & Boot", "setup-return", "secondary", "bolt", "sm") + "</div></div>";
    }

    function injectScreenBanner(markup, bannerMarkup) {
        if (!bannerMarkup) {
            return markup;
        }
        return markup.replace("<div class=\"ayu-screen\">", "<div class=\"ayu-screen\">" + bannerMarkup);
    }

    function agentFrontendLaunchUrl(detail) {
        var frontend = getByPath(detail, "frontend", getByPath(detail, "frontend_manifest", null));
        var control = getByPath(detail, "frontend_control", {});
        var routeMode = String(getByPath(control, "route_mode", getByPath(detail, "frontend_route_mode", getByPath(frontend, "route_mode", ""))) || "").toLowerCase();
        if (frontend && routeMode === "path_proxy") {
            var explicit = String(getByPath(frontend, "path_proxy_url", getByPath(control, "path_proxy_url", "")) || "").trim();
            if (explicit) {
                return explicit;
            }
            var proxyPath = String(frontend.proxy_path || frontend.launch_path || getByPath(control, "browser_path", "") || getByPath(control, "proxy_path", "") || "").trim();
            if (proxyPath) {
                return joinBrowserBaseAndPath(browserPageServiceBaseUrl(), proxyPath);
            }
        }
        return frontend ? (frontend.open_url || frontend.launch_url || frontend.launch_path || "") : "";
    }

    function agentFrontendCopyPath(detail) {
        var frontend = getByPath(detail, "frontend", getByPath(detail, "frontend_manifest", null));
        return frontend ? (frontend.proxy_path || frontend.launch_path || frontend.open_url || "") : "";
    }

    function promptBuilderLaunchTarget() {
        var agentsPayload = state.agentWorkbench.payload || getByPath(state.bootstrap, "agents", {});
        var detail = getByPath(agentsPayload, "agent_details.build_prompt_agent", {});
        var control = getByPath(detail, "frontend_control", {});
        if (!getByPath(detail, "installed", false)) {
            return { url: "", message: "Install Prompt Builder before opening it." };
        }
        if (!getByPath(detail, "has_frontend", false)) {
            return { url: "", message: "Prompt Builder does not have an available website in this runtime." };
        }
        if (!getByPath(control, "enabled", getByPath(detail, "frontend_enabled", false))) {
            return { url: "", message: "Enable the Prompt Builder website in Agents before opening it." };
        }
        var url = String(agentFrontendLaunchUrl(detail) || "").trim();
        if (!url) {
            var frontends = getByPath(agentsPayload, "frontends", []);
            var frontend = Array.isArray(frontends)
                ? frontends.find(function (entry) { return getByPath(entry, "agent_name", "") === "build_prompt_agent"; })
                : null;
            if (frontend) {
                url = String(frontend.path_proxy_url || frontend.open_url || frontend.launch_url || frontend.proxy_path || "").trim();
                if (url && url.charAt(0) === "/") {
                    url = joinBrowserBaseAndPath(browserPageServiceBaseUrl(), url);
                }
            }
        }
        return url
            ? { url: url, message: "" }
            : { url: "", message: "Prompt Builder is enabled, but its website route is not ready yet. Restart Websites & Browser, then try again." };
    }

    function agentWebsiteRouteModeOptions(control) {
        var modes = getByPath(control, "route_modes", []);
        if (!Array.isArray(modes) || !modes.length) {
            modes = [
                { value: "path_proxy", label: "Primary browser path" },
                { value: "direct_forward", label: "Direct same-port" }
            ];
        }
        return modes.map(function (item) {
            return {
                value: item.value,
                label: item.label || prettyLabel(item.value || ""),
                disabled: Boolean(item.disabled)
            };
        });
    }

    function agentWebsiteFrontendStackOptions() {
        var choices = getByPath(state.bootstrap, "metadata.agent_website_stacks", []);
        if (!Array.isArray(choices) || !choices.length) {
            choices = [
                { id: "fastapi_static", label: "HTML + JavaScript", description: "Small static frontend with no package install or build step." },
                { id: "react_typescript", label: "React + TypeScript", description: "Vite starter for richer browser apps, served after the build step." },
                { id: "angular_typescript", label: "Angular + TypeScript", description: "Angular CLI starter, served after the build step." }
            ];
        }
        return choices.map(function (item) {
            return {
                value: item.id || item.value,
                label: item.label || item.short_label || prettyLabel(item.id || item.value || "")
            };
        });
    }

    function agentWebsiteBackendStackOptions() {
        var choices = getByPath(state.bootstrap, "metadata.agent_website_backend_stacks", []);
        if (!Array.isArray(choices) || !choices.length) {
            choices = [
                { id: "python_fastapi", label: "Python + FastAPI", description: "AutoYou-native backend with the shared per-agent session boundary." },
                { id: "node_typescript", label: "Node + TypeScript", description: "Small Node HTTP server compiled from TypeScript." },
                { id: "go_http", label: "Go HTTP", description: "Small net/http backend for lightweight compiled deployments." },
                { id: "rust_axum", label: "Rust + Axum", description: "Axum/Tokio backend for Rust service deployments." }
            ];
        }
        return choices.map(function (item) {
            return {
                value: item.id || item.value,
                label: item.label || item.short_label || prettyLabel(item.id || item.value || "")
            };
        });
    }

    function agentWebsiteFrontendStackDescription(stack) {
        var current = String(stack || "fastapi_static");
        var choices = getByPath(state.bootstrap, "metadata.agent_website_stacks", []);
        var match = Array.isArray(choices)
            ? choices.find(function (item) { return String(item.id || item.value || "") === current; })
            : null;
        if (match && match.description) {
            return match.description;
        }
        if (current === "react_typescript") {
            return "Creates a React starter. AutoYou serves the built site from the same website route.";
        }
        if (current === "angular_typescript") {
            return "Creates an Angular starter. Build before serving it from the website route.";
        }
        return "Recommended. Creates a small website that works without extra frontend setup or an AI model.";
    }

    function agentWebsiteBackendStackDescription(stack) {
        var current = String(stack || "python_fastapi");
        var choices = getByPath(state.bootstrap, "metadata.agent_website_backend_stacks", []);
        var match = Array.isArray(choices)
            ? choices.find(function (item) { return String(item.id || item.value || "") === current; })
            : null;
        if (match && match.description) {
            return match.description;
        }
        if (current === "go_http") return "Creates a Go net/http backend starter.";
        if (current === "rust_axum") return "Creates a Rust Axum backend starter.";
        if (current === "node_typescript") return "Creates a Node backend compiled from TypeScript.";
        return "Recommended. Uses AutoYou's Python/FastAPI website boundary.";
    }

    function agentWebsiteRouteModeLabel(mode) {
        return String(mode || "") === "direct_forward" ? "Direct same-port" : "Primary browser path";
    }

    function renderAgentOwnerCard(label, owner) {
        var ownerStatus = getByPath(owner, "status", "");
        var summary = getByPath(owner, "summary", getByPath(owner, "message", getByPath(owner, "updated_at", "")));
        if (!ownerStatus || ownerStatus === "idle") {
            return "<div class=\"ayu-note ayu-note-gray\"><strong>" + escapeHtml(label) + "</strong><div class=\"ayu-hint\">Status not tracked</div></div>";
        }
        return "<div class=\"ayu-note ayu-note-" + escapeHtml(statusTone(ownerStatus)) + "\"><strong>" + escapeHtml(label) + "</strong><div class=\"ayu-hint\">" + escapeHtml(prettyLabel(ownerStatus)) + "</div>" + (summary ? "<div class=\"ayu-hint\">" + escapeHtml(shortText(summary, 140)) + "</div>" : "") + "</div>";
    }

    function agentStatusBadges(agent) {
        var tone = agent.state_tone === "installed" ? "green"
            : (agent.state_tone === "blocked" ? "red"
                : (agent.state_tone === "draft" ? "amber" : "blue"));
        var html = badge(agent.state_label || (agent.installed ? "Installed" : "Available"), tone);
        if (agent.builder_status && agent.builder_status !== "idle") { html += badge(prettyLabel(agent.builder_status), statusTone(agent.builder_status)); }
        if (agent.coding_status && agent.coding_status !== "idle") { html += badge(prettyLabel(agent.coding_status), statusTone(agent.coding_status)); }
        if (agent.frontend_status && agent.frontend_status !== "idle") { html += badge(prettyLabel(agent.frontend_status), statusTone(agent.frontend_status)); }
        if (agent.frontend_route_mode) { html += badge(agentWebsiteRouteModeLabel(agent.frontend_route_mode), agent.frontend_route_mode === "direct_forward" ? "purple" : "blue"); }
        return html;
    }

    function renderAgentRuntimeBanner(listingPayload) {
        var packaged = Boolean(getByPath(listingPayload, "packaged_runtime", false));
        var agentsRoot = getByPath(listingPayload, "workspace_agents_root", "");
        var draftsRoot = getByPath(listingPayload, "workspace_drafts_root", "");
        var workspaceOnly = getByPath(listingPayload, "workspace_only_agents", []);
        var rootsRow = (agentsRoot || draftsRoot) ? "<div class=\"ayu-status-list\">"
            + (agentsRoot ? "<div class=\"ayu-status-row\"><div><strong>Agents root</strong></div><div class=\"ayu-hint ayu-mono\">" + escapeHtml(agentsRoot) + "</div></div>" : "")
            + (draftsRoot ? "<div class=\"ayu-status-row\"><div><strong>Drafts root</strong></div><div class=\"ayu-hint ayu-mono\">" + escapeHtml(draftsRoot) + "</div></div>" : "")
            + "</div>" : "";
        if (packaged) {
            var blockedList = Array.isArray(workspaceOnly) && workspaceOnly.length
                ? "<p style=\"margin:8px 0 0\">Workspace-only drafts that cannot run in this packaged build: <strong>" + workspaceOnly.map(escapeHtml).join(", ") + "</strong></p>"
                : "";
            return "<div class=\"ayu-note ayu-note-amber\"><strong>Installed app</strong>"
                + "<p style=\"margin:6px 0 0\">This app includes a fixed set of built-in agents. You can still create and edit <strong>Workspace Drafts</strong>, but publishing and live testing are disabled here.</p>"
                + "<p style=\"margin:6px 0 0\">To ship a custom agent: move its draft into an editable workspace, test it locally, then rebuild the app. The root prompt can still be customized with <strong>Prompt Override</strong> below.</p>"
                + blockedList + rootsRow + "</div>";
        }
        return "<div class=\"ayu-note ayu-note-gray\"><strong>Editable workspace</strong>"
            + "<p style=\"margin:6px 0 0\">Create or clone an agent as a <strong>Workspace Draft</strong>, edit safely, then <strong>Publish</strong> it when it is ready.</p>"
            + rootsRow + "</div>";
    }

    function renderAgentRestartNotice() {
        if (!getByPath(state.agentWorkbench, "aiRestartRequired", false)) {
            return "";
        }
        var message = getByPath(
            state.agentWorkbench,
            "aiRestartMessage",
            "The agent registry changed. Restart AutoYou AI to update chat routing and prompt filtering."
        );
        return "<div class=\"ayu-note ayu-note-amber\"><strong>AI restart required</strong>"
            + "<p style=\"margin:6px 0 0\">" + escapeHtml(message) + "</p>"
            + "<p style=\"margin:6px 0 0\">The registry and website state are already updated. The running chat process keeps its previous agent graph until it is restarted.</p>"
            + "<div class=\"ayu-inline-actions\">" + button("Restart AutoYou AI", "service:ai:restart", "primary", "refresh", "sm") + "</div></div>";
    }

    function renderAgentPromptRuntimeControl(instructionPayload) {
        if (!instructionPayload) {
            return "";
        }
        var compiled = Boolean(getByPath(instructionPayload, "is_compiled", false));
        var jailbreakActive = Boolean(getByPath(instructionPayload, "jailbreak_active", false));
        var notice = getByPath(instructionPayload, "read_only_notice", "");
        var customPromptWarning = "";
        if (getByPath(instructionPayload, "custom_prompt_active", false)) {
            var unavailableAgents = getByPath(instructionPayload, "custom_prompt_unavailable_agents", []);
            var unavailableText = Array.isArray(unavailableAgents) && unavailableAgents.length
                ? "Unavailable agent references: " + unavailableAgents.map(function (name) { return String(name || ""); }).join(", ") + ". "
                : "";
            customPromptWarning = "<div class=\"ayu-note ayu-note-amber\"><strong>Custom root prompt active</strong>"
                + "<p style=\"margin:6px 0 0\">" + escapeHtml(unavailableText + "Custom prompt text is preserved. Runtime install filtering applies automatically to the factory routing sections, but custom routing text should be updated manually after agent changes.") + "</p></div>";
        }
        if (jailbreakActive) {
            // Active in compiled OR source: the override wins; both editors round-trip into it.
            return customPromptWarning + "<div class=\"ayu-note ayu-note-green\"><strong>Prompt Override active</strong><p style=\"margin:6px 0 0\">" + escapeHtml(notice || "Prompt Override takes precedence over the base prompt. Raw and per-section edits both round-trip into it and apply on the next AI restart.") + "</p><div class=\"ayu-inline-actions\">" + button("Deactivate override", "jailbreak-deactivate", "secondary", "bolt", "sm") + "</div></div>";
        }
        if (compiled) {
            return customPromptWarning + "<div class=\"ayu-note ayu-note-amber\"><strong>Root prompt editing is locked</strong><p style=\"margin:6px 0 0\">" + escapeHtml(notice || "Prompt editing is locked in this runtime. Enable Prompt Override to edit it locally.") + "</p><div class=\"ayu-inline-actions\">" + button("Enable Prompt Override", "jailbreak-activate", "primary", "bolt", "sm") + "</div></div>";
        }
        // Editable runtime with Prompt Override inactive: edits apply directly to prompt.py.
        return customPromptWarning + "<div class=\"ayu-note ayu-note-gray\"><strong>Editable runtime</strong><p style=\"margin:6px 0 0\">" + escapeHtml(notice || "Edits save locally and apply on the next AI restart. Prompt Override is only needed when direct editing is locked.") + "</p></div>";
    }

    function formatAgentDraftTestOutput(result) {
        if (!result) {
            return "No draft structural check has been run yet.";
        }
        var lines = [];
        lines.push("Draft check: " + (result.runtime_loadable_estimate ? "ready to publish" : "issues found"));
        lines.push("prompt.py valid: " + (result.prompt_valid ? "yes" : "no"));
        lines.push("agent.py valid: " + (result.agent_valid ? "yes" : "no"));
        lines.push("factory found: " + (result.factory_found ? "yes" : "no"));
        lines.push("frontend manifest valid: " + (result.frontend_manifest_valid ? "yes" : "no"));
        if (Array.isArray(result.errors) && result.errors.length) {
            lines.push("");
            lines.push("Errors:");
            result.errors.forEach(function (item) {
                lines.push("- " + item);
            });
        }
        return lines.join("\n");
    }

    function renderAgentBuilderWorkbenchPane(detail, listingPayload) {
        var runtimePolicy = getByPath(listingPayload, "runtime_policy_note", "Workspace drafts stay isolated until you publish them.");
        var liveMarkup = detail.live_exists
            ? renderStatusRows([
                { label: "Source kind", value: getByPath(detail, "live_source.source_kind", "unknown") },
                { label: "Prompt path", value: getByPath(detail, "live_instruction.prompt_path", "Built in"), mono: true },
                { label: "Agent path", value: getByPath(detail, "live_source.agent_path", "Built in"), mono: true }
            ]) + (getByPath(detail, "live_instruction.immutable_reason", "") ? "<div class=\"ayu-note ayu-note-amber\">" + escapeHtml(getByPath(detail, "live_instruction.immutable_reason", "")) + "</div>" : "")
            : "<div class=\"ayu-empty\">No installed copy exists yet. Publish the draft when you are ready.</div>";
        var draftExists = Boolean(getByPath(detail, "draft.exists", false));
        var draftMarkup = draftExists
            ? renderStatusRows([
                { label: "Draft kind", value: getByPath(detail, "draft.metadata.draft_kind", "workspace_draft") },
                { label: "Draft dir", value: getByPath(detail, "draft.draft_dir", ""), mono: true },
                { label: "Source code", value: getByPath(detail, "draft.has_source_code", false) ? "Available in draft" : "Prompt/frontend only" },
                { label: "Source note", value: getByPath(detail, "draft.source_unavailable_note", "No source gaps reported."), mono: false }
            ])
            : "<div class=\"ayu-empty\">No workspace draft exists yet. Clone the live agent or start a fresh draft from the builder scaffold.</div>";
        var lastTest = state.agentWorkbench.tests[detail.agent_name];
        return "<div class=\"ayu-note\">" + escapeHtml(detail.packaged_runtime ? "Installed AutoYou keeps workspace drafts editable, but publishing and live testing stay disabled here." : runtimePolicy) + "</div><div class=\"ayu-grid-2\">" + panel("Installed agent", "Published files and prompt metadata.", liveMarkup) + panel("Workspace draft", "Editable draft files that stay local until you publish them.", draftMarkup) + "</div><div class=\"ayu-grid-3\">" + [
            renderAgentOwnerCard("Agent Builder", getByPath(detail, "builder_owner", {})),
            renderAgentOwnerCard("Coding Agent", getByPath(detail, "coding_owner", {})),
            renderAgentOwnerCard("Website Agent", getByPath(detail, "frontend_owner", {}))
        ].join("") + "</div>" + (draftExists && !detail.packaged_runtime ? checkbox("agentWorkbench.install_after_publish", "Install after publish", "Install the just-published draft the next time AutoYou AI restarts.") : "") + "<div class=\"ayu-inline-actions\">" + button("Clone to workspace draft", "agent-workbench-clone", "secondary", "copy", "sm", detail.can_clone_draft ? "" : "disabled") + button("Run draft check", "agent-workbench-test", "ghost", "refresh", "sm", detail.can_test_draft ? "" : "disabled") + button("Publish draft", "agent-workbench-publish", "primary", "save", "sm", detail.can_publish_draft ? "" : "disabled") + button("Discard draft", "agent-workbench-discard", "danger", "trash", "sm", detail.can_discard_draft ? "" : "disabled") + button("Reload detail", "agent-workbench-reload", "secondary", "refresh", "sm") + "</div>" + ((!detail.can_publish_draft && getByPath(detail, "publish_block_reason", "")) || (!detail.can_test_draft && getByPath(detail, "test_block_reason", "")) ? "<div class=\"ayu-note ayu-note-amber\">" + [(!detail.can_publish_draft && getByPath(detail, "publish_block_reason", "") ? "Publish disabled: " + escapeHtml(getByPath(detail, "publish_block_reason", "")) : ""), (!detail.can_test_draft && getByPath(detail, "test_block_reason", "") ? "Draft check disabled: " + escapeHtml(getByPath(detail, "test_block_reason", "")) : "")].filter(Boolean).join("<br>") + "</div>" : "") + (lastTest ? "<pre class=\"ayu-note ayu-mono\">" + escapeHtml(formatAgentDraftTestOutput(lastTest)) + "</pre>" : "<div class=\"ayu-empty\">Run Draft Check to verify the draft before publishing.</div>");
    }

    function renderAgentCodingWorkbenchPane(detail) {
        var canEdit = Boolean(getByPath(detail, "can_edit_draft_instructions", false));
        var liveInstruction = getByPath(detail, "live_instruction", {});
        var draftExists = Boolean(getByPath(detail, "draft.exists", false));
        var draftNote = canEdit
            ? "Editing the workspace draft only. The installed agent stays read-only until you publish from a development workspace."
            : (draftExists ? (getByPath(detail, "draft_instruction.error", "This draft does not have editable instructions yet.")) : "No workspace draft exists yet. Clone the live agent or create a new draft first.");
        return "<div class=\"ayu-note\">" + escapeHtml(getByPath(detail, "coding_agent_installed", false) ? "coding_agent is installed, and this draft metadata tracks its latest instruction edits." : "coding_agent is not currently installed, but you can still prepare draft instructions here.") + "</div><div class=\"ayu-grid-2\">" + panel("Live prompt", "Read-only runtime prompt preview.", renderStatusRows([
            { label: "Prompt path", value: getByPath(liveInstruction, "prompt_path", "Built in"), mono: true },
            { label: "Source kind", value: getByPath(liveInstruction, "source_kind", "unknown") },
            { label: "Access", value: getByPath(liveInstruction, "immutable_reason", getByPath(liveInstruction, "error", "Live prompt preview")) }
        ]) + renderReadonlyTextarea(getByPath(liveInstruction, "instructions", ""), 12)) + panel("Draft instructions", "Editable workspace prompt content and short description.", field("Draft description", input("agentWorkbench.draft_description", { placeholder: "Brief draft summary", extraAttrs: canEdit ? "" : "disabled" })) + field("Draft instructions", textarea("agentWorkbench.draft_instructions", { rows: 14, extraClass: "ayu-mono", extraAttrs: canEdit ? "" : "disabled" }), draftNote) + "<div class=\"ayu-inline-actions\">" + button("Save draft instructions", "agent-workbench-save-instructions", "primary", "save", "sm", canEdit ? "" : "disabled") + button("Reload detail", "agent-workbench-reload", "secondary", "refresh", "sm") + button("Clone to draft", "agent-workbench-clone", "ghost", "copy", "sm", detail.can_clone_draft ? "" : "disabled") + "</div>") + "</div>";
    }

    function renderAgentFrontendWorkbenchPane(detail) {
        var liveFrontend = getByPath(detail, "frontend", getByPath(detail, "frontend_manifest", null));
        var frontendControl = getByPath(detail, "frontend_control", null);
        var draftExists = Boolean(getByPath(detail, "draft.exists", false));
        var websiteFiles = getByPath(detail, "draft.website_files", []);
        var selectedStack = getByPath(state.forms, "agentWorkbench.frontend_stack", getByPath(liveFrontend, "frontend_stack", "fastapi_static"));
        var selectedBackendStack = getByPath(state.forms, "agentWorkbench.backend_stack", getByPath(liveFrontend, "backend_stack", "python_fastapi"));
        var liveStack = getByPath(liveFrontend, "frontend_stack", "fastapi_static");
        var liveBackendStack = getByPath(liveFrontend, "backend_stack", "python_fastapi");
        var liveStackLabel = agentWebsiteFrontendStackOptions().reduce(function (label, item) {
            return String(item.value) === String(liveStack) ? item.label : label;
        }, liveStack === "react_typescript" ? "React + TypeScript" : (liveStack === "angular_typescript" ? "Angular + TypeScript" : "HTML + JavaScript"));
        var liveBackendStackLabel = agentWebsiteBackendStackOptions().reduce(function (label, item) {
            return String(item.value) === String(liveBackendStack) ? item.label : label;
        }, liveBackendStack === "go_http" ? "Go HTTP" : (liveBackendStack === "rust_axum" ? "Rust + Axum" : (liveBackendStack === "node_typescript" ? "Node + TypeScript" : "Python + FastAPI")));
        var liveActions = [];
        if (frontendControl) {
            liveActions.push(button(frontendControl.enabled ? "Disable website" : "Enable website", "agent-frontend:" + escapeHtml(detail.agent_name), frontendControl.enabled ? "ghost" : "primary", "bolt", "sm", detail.runtime_blocked ? "disabled" : ""));
        }
        if (agentFrontendLaunchUrl(detail) && !detail.runtime_blocked) {
            liveActions.push("<a class=\"ayu-link-btn ayu-btn ayu-btn-secondary ayu-btn-sm\" href=\"" + escapeHtml(agentFrontendLaunchUrl(detail)) + "\" target=\"_blank\" rel=\"noreferrer\">" + icon("external") + "<span>Open website</span></a>");
        }
        if (agentFrontendCopyPath(detail)) {
            liveActions.push(button("Copy path", "agent-workbench-copy-path", "ghost", "copy", "sm"));
        }
        var routeModeMarkup = frontendControl
            ? "<div class=\"ayu-soft-divider\"></div>" + field("Website route", select("agentWorkbench.frontend_route_mode", agentWebsiteRouteModeOptions(frontendControl)), getByPath(frontendControl, "route_mode", "path_proxy") === "direct_forward" ? "Direct same-port asks paired clients to mirror this website on its own localhost port." : "Primary browser path keeps the website under /agent/name on the main browser port.") + "<div class=\"ayu-inline-actions\">" + button("Save route mode", "agent-frontend-route", "secondary", "save", "sm", detail.runtime_blocked ? "disabled" : "") + "</div>"
            : "";
        var draftIntro = "<div class=\"ayu-note ayu-note-blue\"><strong>Website builder</strong><p style=\"margin:6px 0 0\">Choose a starter, add a title, and create the website files. This step does not need an AI model.</p></div>";
        var draftBody = draftIntro
            + field("Backend", select("agentWorkbench.backend_stack", agentWebsiteBackendStackOptions(), draftExists ? "" : "disabled"), agentWebsiteBackendStackDescription(selectedBackendStack))
            + field("Frontend", select("agentWorkbench.frontend_stack", agentWebsiteFrontendStackOptions(), draftExists ? "" : "disabled"), agentWebsiteFrontendStackDescription(selectedStack))
            + field("Website purpose", input("agentWorkbench.frontend_ui_purpose", { placeholder: "What should this website help people do?", extraAttrs: draftExists ? "" : "disabled" }))
            + field("App title", input("agentWorkbench.frontend_app_title", { placeholder: "Agent website", extraAttrs: draftExists ? "" : "disabled" }))
            + field("Local website port", input("agentWorkbench.frontend_local_port", { type: "number", extraAttrs: draftExists ? "" : "disabled" }), draftExists ? "AutoYou uses this port when opening the website locally." : "Clone or create a draft before scaffolding website assets.")
            + "<div class=\"ayu-inline-actions\">" + button("Scaffold website", "agent-workbench-scaffold-frontend", "primary", "plus", "sm", draftExists ? "" : "disabled") + button("Reload detail", "agent-workbench-reload", "secondary", "refresh", "sm") + "</div><div class=\"ayu-soft-divider\"></div>"
            + field("Manifest title", input("agentWorkbench.frontend_title", { placeholder: "Visible browser title", extraAttrs: draftExists ? "" : "disabled" }))
            + field("Manifest description", textarea("agentWorkbench.frontend_description", { rows: 5, extraAttrs: draftExists ? "" : "disabled" }))
            + "<div class=\"ayu-grid-2\">" + field("Entry path", input("agentWorkbench.frontend_entry_path", { placeholder: "/", extraAttrs: draftExists ? "" : "disabled" })) + field("Recommended port", input("agentWorkbench.frontend_recommended_port", { type: "number", extraAttrs: draftExists ? "" : "disabled" })) + "</div>"
            + checkbox("agentWorkbench.frontend_requires_proxy", "Run this website from AutoYou", "Lets connected browsers open the website through AutoYou.", draftExists ? "" : "disabled")
            + "<div class=\"ayu-inline-actions\">" + button("Save website details", "agent-workbench-save-manifest", "secondary", "save", "sm", draftExists ? "" : "disabled") + "</div>"
            + (Array.isArray(websiteFiles) && websiteFiles.length ? "<div class=\"ayu-chip-list\">" + websiteFiles.map(function (item) {
                return "<span class=\"ayu-chip ayu-mono\">" + escapeHtml(item) + "</span>";
            }).join("") + "</div>" : "<div class=\"ayu-empty\">No draft website files exist yet.</div>");
        return "<div class=\"ayu-note\">" + escapeHtml(getByPath(detail, "website_agent_installed", false) ? "Website Builder is installed. You can create simple website files here, then publish when ready." : "Website Builder is not installed, but you can still scaffold and edit draft website assets here.") + "</div><div class=\"ayu-grid-2\">" + panel("Live website", "Current route and launch status.", (liveFrontend ? renderStatusRows([
            { label: "Proxy path", value: liveFrontend.proxy_path || liveFrontend.launch_path || liveFrontend.entry_path || "/", mono: true },
            { label: "Open URL", value: agentFrontendLaunchUrl(detail) || liveFrontend.open_url || liveFrontend.launch_url || liveFrontend.local_url || "", mono: true },
            { label: "Backend", value: liveBackendStackLabel },
            { label: "Website type", value: liveStackLabel },
            { label: "Route mode", value: getByPath(frontendControl, "route_mode_label", agentWebsiteRouteModeLabel(getByPath(frontendControl, "route_mode", ""))) },
            { label: "Control", value: getByPath(frontendControl, "label", getByPath(frontendControl, "kind", "Website control")) }
        ]) : "<div class=\"ayu-empty\">No live website manifest is registered for this agent yet.</div>") + routeModeMarkup + (liveActions.length ? "<div class=\"ayu-inline-actions\">" + liveActions.join("") + "</div>" : "")) + panel("Draft website", "Create simple website files and keep the details aligned before you publish.", draftBody) + "</div>";
    }

    function currentTitle() {
        var entry = NAV.find(function (item) {
            return item.id === state.screen;
        });
        return entry ? entry.label : "Admin";
    }

    function renderProfileMenu(avatarUrl) {
        var photoActions = avatarUrl
            ? button("Change photo", "profile-image-select", "secondary", "plus", "sm") + button("Remove photo", "profile-image-remove", "danger", "trash", "sm")
            : button("Add photo", "profile-image-select", "secondary", "plus", "sm");
        var runtimeActions = button("Refresh Snapshot", "refresh-bootstrap", "secondary", "refresh", "sm")
            + button("Live View", "nav:live", "ghost", "eye", "sm")
            + "<a class=\"ayu-link-btn ayu-btn ayu-btn-secondary ayu-btn-sm\" href=\"/logout\">" + icon("logout") + "<span>Logout</span></a>"
            + "<form method=\"post\" action=\"/shutdown\"><button class=\"ayu-form-btn ayu-btn ayu-btn-danger ayu-btn-sm\" type=\"submit\">" + icon("power") + "<span>Shutdown Server</span></button></form>";
        return "<div class=\"ayu-profile-menu\">" + photoActions + "<div class=\"ayu-profile-menu-divider\" role=\"presentation\"></div>" + runtimeActions + "</div>";
    }

    function renderSidebar() {
        var admin = getByPath(state.bootstrap, "admin", {});
        var serverName = getByPath(admin, "server_name", "AutoYou-Server");
        var nameFontSize = Math.max(11, Math.min(17, 250 / Math.max(String(serverName).length, 14))).toFixed(1) + "px";
        var avatarUserID = String(getByPath(admin, "avatar_user_id", getByPath(admin, "server_id", "")) || "");
        var avatarUrl = String(getByPath(admin, "avatar_url", "") || "");
        var avatarUploading = isActionPending("profile-image-upload");
        var avatarMarkup = avatarUploading
            ? "<button type=\"button\" class=\"ayu-avatar ayu-avatar-button\" disabled aria-busy=\"true\"><span class=\"ayu-btn-spinner\" aria-hidden=\"true\"></span></button>"
            : (avatarUrl
                ? "<button type=\"button\" class=\"ayu-avatar ayu-avatar-button has-image\" data-profile-user-id=\"" + escapeHtml(avatarUserID) + "\" data-action=\"profile-image-select\" aria-label=\"Change profile image\" title=\"Change profile image\"><img src=\"" + escapeHtml(adminAssetUrl(avatarUrl)) + "\" alt=\"" + escapeHtml(serverName) + " avatar\"></button>"
                : "<button type=\"button\" class=\"ayu-avatar ayu-avatar-button\" data-profile-user-id=\"" + escapeHtml(avatarUserID) + "\" data-action=\"profile-image-select\" aria-label=\"Upload profile image\" title=\"Upload profile image\"><span>" + escapeHtml(avatarText(serverName)) + "</span></button>");
        var profileMenu = state.profileMenuOpen ? renderProfileMenu(avatarUrl) : "";
        var updateStatus = state.softwareUpdate.payload || getByPath(state.bootstrap, "status.software_update", {});
        var updateMarkup = getByPath(updateStatus, "update_available", false)
            ? "<button type=\"button\" class=\"ayu-sidebar-update\" data-action=\"software-update-open\"><span class=\"ayu-sidebar-update-icon\">" + icon("bolt") + "</span><span><strong>Update ready</strong><small>Version " + escapeHtml(getByPath(updateStatus, "latest_version", "")) + "</small></span></button>"
            : "";
        return "<aside class=\"ayu-sidebar\"><div class=\"ayu-sidebar-header\"><div class=\"ayu-logo-wrap\"><img src=\"" + escapeHtml(adminAssetUrl(getByPath(admin, "logo_url", "/assets/logo.png"))) + "\" alt=\"AutoYou logo\"></div><div class=\"ayu-sidebar-header-copy\"><div class=\"ayu-brand-title\" style=\"font-size:" + nameFontSize + "\" title=\"" + escapeHtml(serverName) + "\">" + escapeHtml(serverName) + "</div><div class=\"ayu-brand-subtitle\">Server Admin</div></div></div><nav class=\"ayu-nav\">" + NAV.map(function (item) {
            return "<button type=\"button\" class=\"ayu-nav-btn" + (item.id === state.screen ? " active" : "") + "\" data-action=\"nav:" + escapeHtml(item.id) + "\">" + icon(item.icon) + "<span class=\"ayu-nav-label\">" + escapeHtml(item.label) + "</span></button>";
        }).join("") + "</nav>" + updateMarkup + "<div class=\"ayu-sidebar-footer\"><div class=\"ayu-sidebar-profile\"><div class=\"ayu-sidebar-server\"><div class=\"ayu-sidebar-server-main\">" + avatarMarkup + "<div class=\"ayu-sidebar-server-copy\"><strong style=\"font-size:" + nameFontSize + "\" title=\"" + escapeHtml(serverName) + "\">" + escapeHtml(serverName) + "</strong><div class=\"ayu-hint\">Admin port " + escapeHtml(getByPath(admin, "admin_port", "")) + "</div></div></div>" + iconButton("toggle-profile-menu", "Profile image and server actions", "more", "", "aria-expanded=\"" + escapeHtml(String(Boolean(state.profileMenuOpen))) + "\"") + "</div><input class=\"ayu-file-input\" type=\"file\" accept=\"image/*\" data-role=\"profile-image-input\">" + profileMenu + "</div></div></aside>";
    }

    function renderMobileBar() {
        return "<div class=\"ayu-mobilebar\"><button type=\"button\" class=\"ayu-btn ayu-btn-secondary ayu-btn-sm\" data-action=\"toggle-nav\">" + icon(state.navOpen ? "close" : "menu") + "<span>Menu</span></button><div class=\"ayu-mobilebar-title\">" + escapeHtml(currentTitle()) + "</div><button type=\"button\" class=\"ayu-btn ayu-btn-secondary ayu-btn-sm\" data-action=\"refresh-bootstrap\">" + icon("refresh") + "<span>Refresh</span></button></div>";
    }

    function normalizeOverviewBindHost(value) {
        var normalized = String(value || "").trim().toLowerCase();
        return normalized === "0.0.0.0" || normalized === "::" ? "0.0.0.0" : "127.0.0.1";
    }

    function bindHostAccessLabel(value) {
        return normalizeOverviewBindHost(value) === "0.0.0.0" ? "Accessible to home network" : "Local only";
    }

    function bindHostTone(value) {
        return normalizeOverviewBindHost(value) === "0.0.0.0" ? "amber" : "green";
    }

    function remoteAccessRoleOptions() {
        return [
            { id: "admin", value: "admin", label: "Admin - view, add, edit, delete" },
            { id: "editor", value: "editor", label: "Editor - view, add, edit" },
            { id: "viewer", value: "viewer", label: "Viewer - view only" }
        ];
    }

    function normalizeRemoteAccessRole(value) {
        var role = String(value || "viewer").trim().toLowerCase();
        return role === "viewer" || role === "editor" || role === "admin" ? role : "viewer";
    }

    function remoteAccessRoleLabel(role) {
        var option = findOption(remoteAccessRoleOptions(), normalizeRemoteAccessRole(role), "viewer");
        return option ? option.label.split(" - ")[0] : "Viewer";
    }

    function remoteAccessRoleHelp(role) {
        role = normalizeRemoteAccessRole(role);
        if (role === "viewer") {
            return "Paired clients, partner-connected browsers and browsers on your home network can open pages and read data, but cannot save changes.";
        }
        if (role === "editor") {
            return "Paired clients, partner-connected browsers and browsers on your home network can add or update data, but cannot delete.";
        }
        return "Paired clients, partner-connected browsers and browsers on your home network have full access, including delete and live app connections (WebSocket upgrades).";
    }

    function normalizeHomeNetworkWebsitesMode(value) {
        return String(value || "") === "direct_forward" ? "direct_forward" : "path_proxy";
    }

    function homeNetworkWebsitesLabel(mode) {
        return normalizeHomeNetworkWebsitesMode(mode) === "direct_forward" ? "Direct, with a device pass" : "Only through the admin sign-in";
    }

    function homeNetworkWebsitesHelp(mode) {
        return normalizeHomeNetworkWebsitesMode(mode) === "direct_forward"
            ? "Phones paired with Local Pair open website apps straight from this computer over HTTPS, checked against its key - much faster. Any other device needs a device pass: a browser gets one by signing in here."
            : "Website apps stay on this computer and open on the admin page after signing in. Paired phones keep using their connection.";
    }

    function renderHomeNetworkSecurity(home, exposed, httpsNextBoot) {
        var remoteAccessRole = getByPath(state.forms, "page.remote_access_role", "viewer");
        var parts = [];
        if (exposed && !httpsNextBoot) {
            parts.push("<div class=\"ayu-note ayu-note-red ayu-network-alert\"><strong>Home network without HTTPS</strong><p>Other devices on your network would open pages, notes and the sign-in page over plain HTTP, readable by anyone on the same Wi-Fi. HTTPS is the default whenever home network access is on.</p><div class=\"ayu-inline-actions\">" + button("Turn on HTTPS (recommended)", "overview-https:enable", "primary", "shield", "sm") + "</div></div>");
        }
        if (!asBoolean(getByPath(home, "enabled", false), false)) {
            return parts.join("");
        }
        var liveHttps = asBoolean(getByPath(home, "https", false), false);
        var links = [].concat(getByPath(home, "websites_urls", []) || [], getByPath(home, "admin_urls", []) || []).map(function (url) {
            return "<li><code>" + escapeHtml(url) + "</code></li>";
        }).join("");
        if (links) {
            parts.push("<div class=\"ayu-note ayu-network-note ayu-note-" + (liveHttps ? "blue" : "amber") + "\"><strong>From a browser on another device</strong><ul class=\"ayu-network-urls\">" + links + "</ul>"
                + "<p>Sign in there, then choose Website apps; that browser gets a device pass with the " + escapeHtml(remoteAccessRoleLabel(remoteAccessRole).toLowerCase()) + " role. Paired AutoYou apps need none of this.</p>"
                + (liveHttps ? "<p>To skip the certificate warning, install this server's certificate once on that device: <a href=\"/ca.crt\" download>Download CA certificate</a>.</p>" : "")
                + "</div>");
        }
        return parts.join("");
    }

    function serviceStatusIsRunning(value) {
        var text = String(value || "").trim().toLowerCase();
        if (!text || text === "unknown") {
            return false;
        }
        if (text.indexOf("stopped") !== -1 || text.indexOf("disabled") !== -1 || text.indexOf("error") !== -1 || text.indexOf("failed") !== -1 || text.indexOf("not running") !== -1) {
            return false;
        }
        return text.indexOf("running") !== -1 || text.indexOf("connected") !== -1 || text.indexOf("active") !== -1 || text.indexOf("healthy") !== -1 || text.indexOf("ready") !== -1;
    }

    function renderOverviewServiceRow(label, copy, actionPrefix, statusValue, options) {
        options = options || {};
        var running = serviceStatusIsRunning(statusValue);
        var actionMarkup = running
            ? button("Stop", actionPrefix + ":stop", "secondary", "stop", "sm")
            : button("Start", actionPrefix + ":start", "green", "play", "sm");
        actionMarkup += options.refreshOnly
            ? button("Refresh", actionPrefix + ":refresh", "ghost", "refresh", "sm")
            : button("Restart", actionPrefix + ":restart", "ghost", "refresh", "sm");
        return "<div class=\"ayu-list-row\"><div class=\"ayu-list-copy\"><strong>" + escapeHtml(label) + "</strong><p>" + escapeHtml(copy) + "</p></div><div class=\"ayu-inline-actions\">" + badge(running ? "Running" : "Stopped", running ? "green" : "gray") + actionMarkup + "</div></div>";
    }

    function renderOverviewQuickControls() {
        var status = getByPath(state.bootstrap, "status", {});
        var aiStatus = getByPath(status, "ai_agent.status", "Unknown");
        var pageStatus = getByPath(status, "autoyou_page.status", "Unknown");
        var tunnelStatus = getByPath(status, "tunnelmole.status", getByPath(status, "cloud.status", "Unknown"));
        return "<div class=\"ayu-list\">"
            + renderOverviewServiceRow("AutoYou AI", "Restart after provider changes, prompt updates, or agent installs.", "service:ai", aiStatus)
            + renderOverviewServiceRow("Browser & Page", "Manage the local browser/page host without leaving the shell.", "service:page", pageStatus)
            + renderOverviewServiceRow("Public proxy", "Start or stop the public proxy, then refresh the issued URL status.", "service:tunnelmole", tunnelStatus, { refreshOnly: true })
            + "</div>";
    }

    function renderOverviewAccessPanel() {
        var cfg = bootstrapConfig();
        var status = getByPath(state.bootstrap, "status", {});
        var serverName = getByPath(state.bootstrap, "admin.server_name", getByPath(cfg, "server.name", "AutoYou-Server"));
        var liveHost = getByPath(status, "instance.bind_host", "");
        var liveHostKnown = hasValue(liveHost);
        var nextHost = normalizeOverviewBindHost(getByPath(state.forms, "overview.bindHost", getByPath(cfg, "server.bind_host", "127.0.0.1")));
        var nativeUnlockEnabled = asBoolean(getByPath(state.forms, "overview.nativeUnlockEnabled", getByPath(cfg, "security.native_unlock_enabled", true)), true);
        var securityMode = String(getByPath(cfg, "security.mode", "secure") || "secure");
        // The server reports its own effective HTTPS setting, including the
        // home-network default; derive it here only for an older server.
        var home = getByPath(status, "home_network", null);
        var homeReported = Boolean(home && typeof home.https_next_boot === "boolean");
        var httpsExplicit = getByPath(state.forms, "overview.httpsEnabled", getByPath(cfg, "server.https_enabled", null));
        var httpsEnabled = hasValue(getByPath(state.forms, "overview.httpsEnabled", null))
            ? asBoolean(httpsExplicit, false)
            : (homeReported
                ? home.https_next_boot
                : ((httpsExplicit === null || httpsExplicit === undefined)
                    ? (securityMode === "secure_professional_maximus" || nextHost === "0.0.0.0")
                    : asBoolean(httpsExplicit, false)));
        var httpsLive = asBoolean(getByPath(home, "https", false), false);
        var homeLive = homeReported ? asBoolean(getByPath(home, "enabled", false), false) : (liveHostKnown && normalizeOverviewBindHost(liveHost) === "0.0.0.0");
        var websitesMode = normalizeHomeNetworkWebsitesMode(getByPath(home, "websites_mode_next_boot", getByPath(cfg, "server.home_network_websites", nextHost === "0.0.0.0" ? "path_proxy" : "direct_forward")));
        var discoveryEnabled = asBoolean(getByPath(home, "discovery_enabled", getByPath(cfg, "server.discovery_enabled", true)), true);
        var discoveryLive = asBoolean(getByPath(home, "discovery", false), false);
        var vpnEnabled = asBoolean(getByPath(cfg, "server.vpn_addresses", getByPath(home, "vpn_addresses", false)), false);
        var vpnAddresses = getByPath(home, "vpn_address_list", []) || [];
        var bluetoothEnabled = asBoolean(getByPath(state.forms, "connectivity.bluetoothPairing.enabled", getByPath(cfg, "bluetooth_pairing.enabled", false)), false);
        var bluetoothRuntime = getByPath(status, "bluetooth_pairing", {});
        var bluetoothRunning = Boolean(getByPath(bluetoothRuntime, "running", false));
        var adminFrontendEnabled = asBoolean(getByPath(state.forms, "page.admin_frontend_enabled", getByPath(cfg, "agent_frontends.admin_agent", false)), false);
        var remotePermsLive = asBoolean(getByPath(state.bootstrap, "metadata.allow_remote_admin_permissions", getByPath(home, "allow_remote_admin_permissions", false)), false);
        var remotePermsExplicit = getByPath(state.forms, "overview.allowRemoteAdminPermissions", getByPath(cfg, "server.allow_remote_admin_permissions", null));
        var remotePermsNextBoot = hasValue(remotePermsExplicit) ? asBoolean(remotePermsExplicit, false) : asBoolean(getByPath(home, "allow_remote_admin_permissions_next_boot", getByPath(cfg, "server.allow_remote_admin_permissions", false)), false);
        var nextHostHelp = nextHost === "0.0.0.0"
            ? "Advertise " + serverName + " to the home network on next boot. Requires shutdown."
            : "Boot local-only on 127.0.0.1 next time. Requires shutdown.";
        var rows = renderStatusRows([
            {
                label: "Live machine access",
                value: liveHostKnown ? bindHostAccessLabel(liveHost) : "Unknown",
                help: liveHostKnown ? (normalizeOverviewBindHost(liveHost) === "0.0.0.0" ? "This process is bound to 0.0.0.0 now." : "This process is bound to localhost now.") : "Runtime bind host is not reported in this snapshot."
            },
            { label: "Next boot access", value: bindHostAccessLabel(nextHost), help: nextHostHelp },
            {
                label: "System credential unlock",
                value: nativeUnlockEnabled ? "Enabled next boot" : "Disabled next boot",
                help: nativeUnlockEnabled ? "The login screen can offer OS Keychain/Credential Manager unlock after restart." : "Password entry is required after restart."
            },
            {
                label: "Local HTTPS",
                value: (httpsLive ? "Running now" : "Off now") + " · " + (httpsEnabled ? "on next boot" : "off next boot"),
                help: httpsEnabled
                    ? "Admin and Websites & Browser are also served over TLS; home-network browsers are moved to it. Install this server's certificate on each device so it is trusted."
                    : (nextHost === "0.0.0.0" ? "Home network devices would use plain HTTP. Turn HTTPS on." : "Services are served over plain HTTP on this computer only.")
            },
            {
                label: "Website apps on the home network",
                value: homeNetworkWebsitesLabel(websitesMode),
                help: homeNetworkWebsitesHelp(websitesMode)
            },
            {
                label: "VPN addresses (Tailscale)",
                value: vpnEnabled ? (vpnAddresses.length ? "On · " + vpnAddresses.join(", ") : "On") : "Off",
                help: vpnEnabled
                    ? "Devices on your VPN can use Local Pair and open website apps directly; the HTTPS certificate covers these addresses after restart."
                    : "Only home-network addresses are served. Turn on to also reach this computer over Tailscale."
            },
            {
                label: "Nearby discovery",
                value: discoveryEnabled ? (discoveryLive ? "Announcing now" : (homeLive ? "On, not announcing" : "On while on the home network")) : "Off",
                help: discoveryEnabled
                    ? "AutoYou apps on the same network can find " + serverName + " without typing its address."
                    : "Nearby AutoYou apps will not find this computer; connect by address instead."
            },
            {
                label: "Nearby Computer",
                value: bluetoothEnabled ? (bluetoothRunning ? "Listening" : "Enabled") : "Disabled",
                help: bluetoothEnabled ? "Bluetooth Pair can advertise when the runtime is available." : "Bluetooth Pair is off."
            },
            {
                label: "Admin website exposure",
                value: adminFrontendEnabled ? "Advertised" : "Not advertised",
                help: adminFrontendEnabled ? "The admin shell can be surfaced through Websites & Browser." : "The admin shell is not registered as an agent website."
            },
            {
                label: "Network admin permissions (WSL / Docker / LAN)",
                value: (remotePermsLive ? "Enabled now" : "Disabled now") + " · " + (remotePermsNextBoot ? "enabled next boot" : "disabled next boot"),
                help: remotePermsNextBoot
                    ? "Admins connecting from WSL, Docker, or LAN over HTTPS can view and modify hardware permissions and capture settings."
                    : "Hardware permissions can only be changed from localhost (127.0.0.1)."
            },
        ]);
        var accessActionHost = liveHostKnown ? normalizeOverviewBindHost(liveHost) : nextHost;
        var accessAction = accessActionHost === "0.0.0.0"
            ? button("Change to local-only machine access on next boot", "overview-bind-local", "secondary", "shield", "sm")
            : button("Turn on the home network with HTTPS on next boot", "overview-bind-home", "secondary", "wifi", "sm");
        var websitesActions = websitesMode === "direct_forward"
            ? button("Only through the admin sign-in", "overview-home-websites:path_proxy", "ghost", "shield", "sm")
            : button("Let paired devices open them directly", "overview-home-websites:direct_forward", "secondary", "page", "sm");
        var vpnAction = vpnEnabled
            ? button("Stop serving VPN addresses", "overview-vpn:disable", "ghost", "shield", "sm")
            : button("Also serve VPN addresses", "overview-vpn:enable", "ghost", "wifi", "sm");
        var discoveryAction = discoveryEnabled
            ? button("Stop nearby discovery", "overview-discovery:disable", "ghost", "eye", "sm")
            : button("Turn on nearby discovery", "overview-discovery:enable", "secondary", "wifi", "sm");
        var unlockAction = nativeUnlockEnabled
            ? button("Disable keychain unlock next boot", "overview-native-unlock:disable", "ghost", "shield", "sm")
            : button("Enable keychain unlock next boot", "overview-native-unlock:enable", "secondary", "key", "sm");
        var httpsAction = httpsEnabled
            ? button("Disable HTTPS on next boot", "overview-https:disable", "ghost", "shield", "sm")
            : button("Enable HTTPS on next boot", "overview-https:enable", "secondary", "shield", "sm");
        var remotePermsAction = remotePermsNextBoot
            ? button("Disable network admin permissions next boot", "overview-remote-permissions:disable", "ghost", "shield", "sm")
            : button("Allow network admin permissions next boot", "overview-remote-permissions:enable", "secondary", "shield", "sm");
        var httpsNote = httpsEnabled
            ? "<div class=\"ayu-note ayu-note-blue\"><strong>Local HTTPS:</strong> After restart, Admin and Websites & Browser are also reachable over https://. To make them trusted (no browser warnings), each device installs this server's certificate one time: <a href=\"/ca.crt\" download>Download CA certificate</a>. Safari, iOS and Android never trust a private certificate automatically  -  this one-time install is expected.</div>"
            : "";
        var noteTone = nextHost === "0.0.0.0" || nativeUnlockEnabled ? "amber" : "green";
        var nextHostSummary = nextHost === "0.0.0.0" ? "home network access" : "local-only access";
        var credentialNote = "<div class=\"ayu-note ayu-note-gray\"><strong>Credentials stay on this computer.</strong> Connected devices can sign in and see settings their role allows, but passwords, two-factor, security mode and network exposure only change here or in an HTTPS admin session opened directly on this computer.</div>";
        var homeNetworkNote = "<div class=\"ayu-note ayu-note-blue ayu-network-note\"><strong>Home network:</strong> " + escapeHtml(homeNetworkWebsitesHelp(websitesMode)) + "<p>Website apps and nearby discovery change right away; sharing on the network, HTTPS and VPN addresses apply after a restart.</p></div><div class=\"ayu-inline-actions\">" + websitesActions + vpnAction + discoveryAction + "</div>";
        var wslDockerNote = "<div class=\"ayu-note ayu-note-blue ayu-network-note\"><strong>WSL, Docker, and Virtual IP Access:</strong> If hosting inside WSL or Docker and connecting via IP (e.g. <code>172.x.x.x</code>):<p>1. <strong>Login requires HTTPS:</strong> Connect over <code>https://&lt;ip&gt;:8443/</code> (or behind a trusted TLS proxy with <code>AUTOYOU_TRUSTED_HTTPS_PROXY=1</code>).</p><p>2. <strong>Permissions modification:</strong> Enable <em>Allow network admin permissions next boot</em> above (or set <code>AUTOYOU_ALLOW_REMOTE_ADMIN_PERMISSIONS=1</code>) so your admin login over the virtual IP can change hardware permissions. Public tunnels remain blocked.</p></div>";
        return rows
            + renderHomeNetworkSecurity(home, homeLive || nextHost === "0.0.0.0", httpsEnabled)
            + homeNetworkNote
            + wslDockerNote
            + "<div class=\"ayu-inline-actions\">" + button("Manage permissions", "nav:permissions", "secondary", "shield") + "</div>"
            + credentialNote
            + "<div class=\"ayu-note ayu-note-" + escapeHtml(noteTone) + "\"><strong>Requires shutdown.</strong> Next boot: " + escapeHtml(nextHostSummary) + (nextHost === "0.0.0.0" ? (httpsEnabled ? " with HTTPS" : " without HTTPS") : "") + ". Network binding, HTTPS, network admin permissions and keychain unlock are read when AutoYou starts.</div><div class=\"ayu-inline-actions\">" + accessAction + unlockAction + httpsAction + remotePermsAction + "</div>" + httpsNote;
    }

    function renderOverviewMediaPanel() {
        var cfg = bootstrapConfig();
        var video = getByPath(state.forms, "videoCall", getByPath(cfg, "video_call", {}));
        var speech = getByPath(state.forms, "speech", {});
        var videoEnabled = asBoolean(getByPath(video, "enabled", true), true);
        var audioEnabled = asBoolean(getByPath(video, "audio_enabled", true), true);
        var audioSourceLabels = [];
        if (asBoolean(getByPath(video, "audio_microphone", false), false)) {
            audioSourceLabels.push("Microphone");
        }
        if (asBoolean(getByPath(video, "audio_speaker_loopback", false), false)) {
            audioSourceLabels.push("Computer sound");
        }
        var videoSourceLabels = [];
        if (asBoolean(getByPath(video, "outbound_remote_desktop", false), false)) {
            videoSourceLabels.push("Screen");
        }
        if (asBoolean(getByPath(video, "outbound_api", false), false)) {
            videoSourceLabels.push("Realtime API frames");
        }
        if (asBoolean(getByPath(video, "outbound_video_file", false), false)) {
            videoSourceLabels.push("Video file");
        }
        if (asBoolean(getByPath(video, "outbound_camera", false), false)) {
            videoSourceLabels.push("Webcam");
        }
        var remoteDesktopEnabled = asBoolean(getByPath(video, "remote_desktop.enabled", true), true);
        var remoteDesktopSending = asBoolean(getByPath(video, "remote_desktop.send_screen", true), true);
        var videoSourceLabel = videoEnabled ? (videoSourceLabels.length ? videoSourceLabels.join(" + ") : "None") : "Disabled";
        var videoSourceHelp = "Remote Desktop " + (remoteDesktopEnabled && remoteDesktopSending ? "can send the screen" : "is not sending the screen") + "; selected sources are stitched into one phone video feed.";
        var ttsProvider = getByPath(speech, "tts_provider", getByPath(cfg, "speech.tts.provider", "system"));
        var sttModel = getByPath(speech, "stt_model", getByPath(cfg, "speech.stt.model", "tiny.en"));
        return renderStatusRows([
            { label: "Video sharing", value: videoSourceLabel, help: videoEnabled ? videoSourceHelp : "Video calls and screen/camera sharing are off." },
            {
                label: "Audio input/playback",
                value: audioEnabled ? (audioSourceLabels.length ? audioSourceLabels.join(" + ") : "None") : "Off",
                help: audioSourceLabels.length ? ("Microphone source: " + getByPath(video, "input_audio_source", "default")) : "No computer audio is selected."
            },
            { label: "AI voice playback", value: asBoolean(getByPath(video, "ai_audio_replies_enabled", true), true) ? (prettyLabel(sttModel) + " / " + prettyLabel(ttsProvider)) : "Text only", help: "Speech recognition model and response TTS provider." },
            {
                label: "Safety recording",
                value: asBoolean(getByPath(video, "silent_recording_enabled", false), false) ? "Recording audio" : "Off",
                help: asBoolean(getByPath(video, "silent_recording_enabled", false), false)
                    ? "Safety Recording writes phone microphone audio to files on this server; it is separate from Background Mode and may show iOS's microphone indicator."
                    : (asBoolean(getByPath(video, "background_mode_enabled", false), false)
                        ? "Background Mode keeps the phone connected with its microphone off. It does not capture audio."
                        : "Safety Recording is off. Background Mode does not capture audio unless the phone starts a voice call.")
            },
            {
                label: "Call video recording",
                value: asBoolean(getByPath(video, "record_my_video", false), false) ? prettyLabel(getByPath(video, "recording_mode", "video")) : "Off",
                help: "Detailed capture folders and image interval live under Video & Calls."
            }
        ]) + "<div class=\"ayu-inline-actions\">" + button("Open Permissions", "nav:permissions", "secondary", "shield", "sm") + button("Open Video & Calls", "nav:video", "ghost", "video", "sm") + button("Open Speech", "nav:speech", "ghost", "mic", "sm") + "</div>";
    }

    function renderOverview() {
        var admin = getByPath(state.bootstrap, "admin", {});
        var status = getByPath(state.bootstrap, "status", {});
        var cfg = bootstrapConfig();
        var cloud = getByPath(status, "cloud", {});
        var cloudEmail = displayCloudEmail(cloud);
        var serverId = displayServerId(cloud);
        var setupPayload = state.setup.payload || {};
        var setupComplete = Boolean(getByPath(setupPayload, "wizard_completed", getByPath(cfg, "onboarding.wizard_completed", false)));
        var setupTimestamp = getByPath(setupPayload, "wizard_completed_at", getByPath(cfg, "onboarding.wizard_completed_at", ""));
        var identityMarkup = field("Server name", input("overview.serverName", { placeholder: "AutoYou-Server" }), "This name signs AutoYou responses and appears during pairing. It does not rename a messaging device that is already paired.") + field("Admin shell theme", select("overview.adminTheme", [{ value: "dark", label: "Dark" }, { value: "light", label: "Light" }]), "This changes the admin shell only. Agent Websites follows each device's system appearance.") + "<div class=\"ayu-inline-actions\">" + button("Save identity & theme", "save-overview", "primary", "save") + button("Open setup", "nav:setup", "secondary", "bolt") + button("Open guides", "nav:guides", "ghost", "book") + "</div>";

        var serviceCards = [
            {
                screen: "setup",
                label: "Setup & Boot",
                value: setupComplete ? "Complete" : "Needs setup",
                sub: setupComplete && setupTimestamp ? ("Completed at " + shortText(setupTimestamp, 26)) : "Open the bootstrap checklist and first-run guide.",
                tone: setupComplete ? "green" : "amber"
            },
            {
                screen: "ai",
                label: "AutoYou AI",
                value: shortText(getByPath(status, "ai_agent.status", "Unknown"), 28),
                sub: getByPath(status, "ai_agent.status", "").toLowerCase().indexOf("running") !== -1 ? "Agents and chat are available on this machine." : "AutoYou AI is not running, so chat and agent workflows are unavailable.",
                tone: statusTone(getByPath(status, "ai_agent.status", ""))
            },
            {
                screen: "page",
                label: "Browser & Page",
                value: shortText(getByPath(status, "autoyou_page.status", "Unknown"), 28),
                sub: getByPath(status, "autoyou_page.status", "").toLowerCase().indexOf("running") !== -1 ? "The website/browser host is serving the local proxy and browser routes." : "The website/browser host is stopped, so browser routes and page content are offline.",
                tone: statusTone(getByPath(status, "autoyou_page.status", ""))
            },
            {
                screen: "messaging",
                label: "Messaging partner",
                value: getByPath(status, "cloud.connected", false) ? "AutoYou Cloud connected" : shortText(getByPath(status, "telegram.status", "Not configured"), 28),
                sub: getByPath(status, "cloud.connected", false) ? "Cloud Pair is active for connected clients." : "Messaging partners are optional but needed when Cloud Pair is not used.",
                tone: getByPath(status, "cloud.connected", false) ? "green" : statusTone(getByPath(status, "telegram.status", ""))
            },
            {
                screen: "connectivity",
                label: "Public access",
                value: shortText(getByPath(status, "tunnelmole.status", getByPath(status, "cloud.status", "Unknown")), 28),
                sub: getByPath(status, "tunnelmole.public_url", "No public proxy URL has been issued yet."),
                tone: statusTone(getByPath(status, "tunnelmole.status", getByPath(status, "cloud.status", ""))),
                ignoreBannerFollowUp: true
            },
            {
                screen: "security",
                label: "Security",
                value: findOption([
                    { id: "normal", label: "Normal Mode (No Encryption)" },
                    { id: "secure", label: "Secure Mode (Password Only)" },
                    { id: "secure_professional", label: "Secure Professional (Password + 2FA)" },
                    { id: "secure_professional_maximus", label: "Secure Professional Maximus" }
                ], getByPath(cfg, "security.mode", "secure"), "secure").label,
                sub: getByPath(status, "totp.totp_configured", false) ? "Authenticator is configured for Secure Professional flows." : "No authenticator setup is configured yet.",
                tone: getByPath(status, "totp.totp_configured", false) ? "green" : "amber"
            }
        ];

        var attentionCards = serviceCards.filter(function (card) {
            return (card.tone === "red" || card.tone === "amber") && !card.ignoreBannerFollowUp;
        });
        var bannerTitle = attentionCards.length ? "Local services need follow-up" : "Local services are ready";
        var bannerCopy = attentionCards.length
            ? attentionCards.map(function (card) {
                return card.label + ": " + card.sub;
            }).join(" ")
            : "AutoYou AI and Browser & Page are available. Cloud Pair and messaging partners can be configured when you need remote access outside the local machine.";
        var serviceStatusMarkup = renderStatusRows(serviceCards.map(function (card) {
            return {
                label: card.label,
                value: card.value,
                help: card.sub
            };
        }));
        var accessPanel = panel("Access & unlock", "Live exposure, next-boot network mode, nearby pairing, and system credential unlock.", renderOverviewAccessPanel());
        var mediaPanel = panel("Media & capture", "Video, audio, AI voice, and recording state without opening the detailed live view.", renderOverviewMediaPanel());
        var updatePanel = renderSoftwareUpdatePanel();

        return "<div class=\"ayu-screen\"><div class=\"ayu-hero\"><div class=\"ayu-hero-copy\"><h1>Overview</h1><p>See what is running on this machine right now, what remains optional, and what still needs local setup before phones or desktop clients connect.</p></div><div class=\"ayu-inline-actions\">" + button("Refresh", "refresh-bootstrap", "secondary", "refresh") + button("Open setup", "nav:setup", "ghost", "bolt") + button("Restart AI Runtime", "service:ai:restart", "ghost", "bolt") + "</div></div><div class=\"ayu-banner\"><div><strong>" + escapeHtml(bannerTitle) + "</strong><p>" + escapeHtml(bannerCopy) + "</p></div><div class=\"ayu-banner-meta\"><div>" + escapeHtml(cloudEmail) + "</div><div>Server ID: " + escapeHtml(serverId) + "</div></div></div><div class=\"ayu-grid-4\">" + serviceCards.map(function (card) {
            return "<button type=\"button\" class=\"ayu-stat-card\" data-action=\"nav:" + escapeHtml(card.screen) + "\"><div class=\"ayu-stat-top\"><div class=\"ayu-kicker\">" + escapeHtml(card.label) + "</div>" + badge(statusBadgeLabel(card.tone), card.tone) + "</div><div class=\"ayu-stat-value\">" + escapeHtml(card.value) + "</div><div class=\"ayu-stat-copy\">" + escapeHtml(card.sub) + "</div></button>";
        }).join("") + "</div><div class=\"ayu-grid-2\">" + accessPanel + mediaPanel + "</div>" + updatePanel + panel("Set up your phone", "One scan for the pairing methods available on this computer.", renderZeroTouchPairingBody()) + renderLocalPairPanelMarkup() + "<div class=\"ayu-grid-2\">" + panel("Server identity & appearance", "Update the display name used by clients, setup QR codes, and the admin shell theme.", identityMarkup) + panel("What is live right now", "Actual service state from the current bootstrap snapshot.", serviceStatusMarkup) + "</div><div class=\"ayu-grid-2\">" + panel("Quick controls", "Common actions across the admin stack.", renderOverviewQuickControls()) + panel("Operator next steps", "Use the setup wizard for first-run work, then move into the screen that owns the setting you need.", "<div class=\"ayu-list\"><div class=\"ayu-list-row\"><div class=\"ayu-list-copy\"><strong>Setup & Boot</strong><p>Walk through password, provider, model, partner, connectivity, and speech setup in one serial flow.</p></div><div class=\"ayu-inline-actions\">" + button("Open setup", "nav:setup", "primary", "bolt", "sm") + "</div></div><div class=\"ayu-list-row\"><div class=\"ayu-list-copy\"><strong>AI & Models</strong><p>Choose the provider path, manage local models, and tune model behavior.</p></div><div class=\"ayu-inline-actions\">" + button("Open AI & Models", "nav:ai", "secondary", "cpu", "sm") + "</div></div><div class=\"ayu-list-row\"><div class=\"ayu-list-copy\"><strong>Security</strong><p>Set the pairing mode, rotate the password, and manage the shared authenticator setup.</p></div><div class=\"ayu-inline-actions\">" + button("Open security", "nav:security", "ghost", "shield", "sm") + "</div></div></div>") + "</div></div>";
    }

    function renderSoftwareUpdatePanel() {
        var local = getByPath(state.bootstrap, "status.software_update", {});
        var update = state.softwareUpdate.payload || local;
        var enabled = asBoolean(getByPath(update, "enabled", getByPath(local, "enabled", true)), true);
        var signedIn = asBoolean(getByPath(update, "signed_in", getByPath(local, "signed_in", false)), false);
        var managed = asBoolean(getByPath(local, "managed_by_environment", false), false);
        var available = asBoolean(getByPath(update, "update_available", false), false);
        var updateError = String(getByPath(update, "error", "") || "");
        var latest = String(getByPath(update, "latest_version", "") || "");
        var current = String(getByPath(update, "current_version", getByPath(local, "current_version", "")) || "");
        var storeManaged = asBoolean(getByPath(update, "store_managed", false), false);
        if (storeManaged) {
            var storeBody = renderStatusRows([{ label: "Installed version", value: current || "Unknown", mono: true }])
                + "<p>Updates are managed by the app store. Open it to check for updates.</p>"
                + (updateError ? "<div role=\"alert\" class=\"ayu-note ayu-note-amber\">" + escapeHtml(updateError) + "</div>" : "")
                + button("Open app store", "software-update-apply", "primary", "external", "sm", state.softwareUpdate.loading ? "disabled" : "");
            return "<div id=\"ayu-software-update\" class=\"ayu-update-panel\">" + panel("Software updates", "", storeBody) + "</div>";
        }
        var canApply = available && (storeManaged || getByPath(update, "install_kind", "source") !== "packaged" || getByPath(update, "artifact_available", false));
        // A published release with no build for this platform is a real state
        // (source-only releases). Say so instead of promising an install that
        // this install path cannot run.
        var availableHeadline = canApply
            ? "Version " + latest + " is ready"
            : "Version " + latest + " has no download for this platform yet";
        var headline = !enabled ? "Checks disabled" : (!signedIn ? "AutoYou account required" : (state.softwareUpdate.loading ? "Checking…" : (updateError ? "Update check unavailable" : (available ? availableHeadline : (state.softwareUpdate.checked ? "AutoYou is up to date" : "Ready to check")))));
        var tone = updateError ? "yellow" : (canApply ? "purple" : (available ? "yellow" : (enabled && signedIn ? "green" : "gray")));
        var controls = checkbox("overview.softwareUpdatesEnabled", "Allow software update checks", "When off, AutoYou makes zero requests to the AutoYou update service.", managed ? "disabled" : "")
            + (managed ? "<div class=\"ayu-note ayu-note-blue\">This setting is managed by AUTOYOU_SOFTWARE_UPDATES_ENABLED for this process.</div>" : "")
            + "<div class=\"ayu-inline-actions\">"
            + button("Save preference", "software-update-save", "secondary", "save", "sm", managed ? "disabled" : "")
            + button("Check now", "software-update-check", "ghost", "refresh", "sm", (!enabled || !signedIn) ? "disabled" : "")
            + (canApply ? button(storeManaged ? "Open platform Store" : (getByPath(update, "install_kind", "") === "docker" ? "Show Docker update command" : "Install update"), "software-update-apply", "primary", "bolt", "sm") : "")
            + (!signedIn && enabled ? "<a class=\"ayu-link-btn ayu-btn ayu-btn-primary ayu-btn-sm\" href=\"/api/cloud/link-start\" target=\"_blank\" rel=\"noopener noreferrer\">" + icon("cloud") + "<span>Connect AutoYou account</span></a>" : "")
            + "</div>";
        var rows = renderStatusRows([
            { label: "Status", value: headline },
            { label: "Installed version", value: current || "Unknown", mono: true },
            { label: "Update channel", value: getByPath(update, "channel", "stable") },
            { label: "Install path", value: prettyLabel(getByPath(update, "install_kind", "Not checked")) }
        ]);
        var message = String(getByPath(update, "error", getByPath(update, "message", "Signed manifests and downloaded artifacts are verified before installation.")) || "");
        return "<div id=\"ayu-software-update\" class=\"ayu-update-panel ayu-tone-" + escapeHtml(tone) + "\">" + panel("Software updates", "OAuth-gated checks through app.autoyou.me. iOS and Android continue to use their app stores.", "<div class=\"ayu-grid-2\"><div>" + rows + "<div class=\"ayu-note ayu-note-blue\">" + escapeHtml(message) + "</div></div><div>" + controls + "</div></div>") + "</div>";
    }

    function renderSetupCloudLinkNote(connected) {
        var copy = connected
            ? "This server is linked to AutoYou Cloud - the easiest hosted pairing path for phones and desktops."
            : "Link this server to AutoYou Cloud for hosted pairing - the simplest way for phones and desktops to reach it without your own public URL.";
        var action = connected
            ? button("Manage on Connectivity", "setup-open-screen:connectivity", "secondary", "wifi", "sm")
            : "<a class=\"ayu-link-btn ayu-btn ayu-btn-primary ayu-btn-sm\" href=\"/api/cloud/link-start\" target=\"_blank\" rel=\"noopener noreferrer\">" + icon("cloud") + "<span>Link to AutoYou Cloud</span></a>";
        return "<div class=\"ayu-note ayu-note-blue\"><strong>AutoYou Cloud (recommended)</strong><p style=\"margin:6px 0 0\">" + escapeHtml(copy) + "</p><div class=\"ayu-inline-actions\">" + action + "</div></div>";
    }

    function renderSetupProfileCards(answers) {
        var profiles = setupProfileTemplates();
        if (!profiles.length) {
            return "<div class=\"ayu-note ayu-note-amber\">Setup profile metadata is not available yet. Refresh setup after the server finishes booting.</div>";
        }
        return "<div class=\"ayu-setup-profile-grid\">" + profiles.map(function (profile) {
            var active = getByPath(answers, "profile_id", "private_starter") === profile.id;
            return "<button type=\"button\" class=\"ayu-setup-profile-card" + (active ? " active" : "") + "\" data-action=\"setup-profile:" + escapeHtml(profile.id) + "\"><div class=\"ayu-choice-card-top\"><div><div class=\"ayu-choice-card-eyebrow\">" + escapeHtml(profile.eyebrow || "Recipe") + "</div><div class=\"ayu-choice-card-title\">" + escapeHtml(profile.label || profile.id) + "</div></div>" + badge(profile.badge_label || "Setup", profile.badge_tone || "gray") + "</div><p>" + escapeHtml(profile.description || "") + "</p></button>";
        }).join("") + "</div>";
    }

    function renderSetupDecisionBranches(answers) {
        var branches = setupDecisionTree().filter(function (branch) { return branch.id !== "profile_id"; });
        if (!branches.length) {
            return "";
        }
        return "<div id=\"ayu-setup-decision-tree\" class=\"ayu-setup-tree\">" + branches.map(function (branch) {
            var options = Array.isArray(branch.options) ? branch.options : [];
            var isMulti = branch.type === "multi";
            var selectedList = isMulti ? (Array.isArray(answers[branch.id]) ? answers[branch.id] : []) : [];
            var optionsMarkup = options.map(function (option) {
                var selected = isMulti ? selectedList.indexOf(option.id) !== -1 : answers[branch.id] === option.id;
                return "<button type=\"button\" class=\"ayu-setup-tree-option" + (selected ? " active" : "") + "\" data-action=\"setup-answer:" + escapeHtml(branch.id) + ":" + escapeHtml(option.id) + "\" aria-pressed=\"" + (selected ? "true" : "false") + "\"><strong>" + escapeHtml(option.label || option.id) + "</strong><span>" + escapeHtml(option.description || "") + "</span></button>";
            }).join("");
            return "<section class=\"ayu-setup-tree-branch\"><div class=\"ayu-setup-tree-head\"><div><h3>" + escapeHtml(branch.label || "") + "</h3><p>" + escapeHtml(branch.prompt || "") + "</p></div>" + badge(isMulti ? "Mix any" : "Choose one", isMulti ? "purple" : "blue") + "</div><div class=\"ayu-setup-tree-options\">" + optionsMarkup + "</div></section>";
        }).join("") + "</div>";
    }

    function renderSetupCoverage(payload) {
        var coverage = getByPath(payload, "coverage", {});
        var apiLabel = getByPath(coverage, "api_route_count_label", "Unavailable");
        var featureCount = getByPath(coverage, "feature_group_count", 0) || "Unavailable";
        var agentCount = getByPath(coverage, "agent_count", 0) || "Unavailable";
        var suggestedAgents = getByPath(coverage, "suggested_agent_count", getByPath(coverage, "release_ready_agent_count", 0)) || "Unavailable";
        return "<div class=\"ayu-setup-coverage\"><div><strong>" + escapeHtml(apiLabel) + "</strong><span>live server actions found</span></div><div><strong>" + escapeHtml(featureCount) + "</strong><span>setup groups</span></div><div><strong>" + escapeHtml(agentCount) + "</strong><span>agents found</span></div><div><strong>" + escapeHtml(suggestedAgents) + "</strong><span>agents suggested by default</span></div></div>";
    }

    function renderSetupRecipePreview(payload, answers) {
        var recipe = state.setup.recipe || null;
        var selectedProfile = setupProfileById(getByPath(answers, "profile_id", "private_starter")) || {};
        if (!recipe) {
            return "<section id=\"ayu-setup-recipe\" class=\"ayu-setup-recipe-card\"><div class=\"ayu-setup-recipe-head\"><div><h3>" + escapeHtml(selectedProfile.label || "Setup plan") + "</h3><p>" + escapeHtml(selectedProfile.description || "Preview the current choices to see exact settings, warnings, and next actions.") + "</p></div>" + badge("Preview", "blue") + "</div>" + renderSetupCoverage(payload) + "<div class=\"ayu-note ayu-note-blue\">All choices configure this local AutoYou server. The preview is deterministic and does not use AI.</div><div class=\"ayu-inline-actions\">" + button("Preview plan", "setup-preview-recipe", "primary", "search") + button("Open Security", "setup-open-screen:security", "secondary", "shield") + button("Open AI & Models", "setup-open-screen:ai", "ghost", "cpu") + "</div></section>";
        }

        var warnings = getByPath(recipe, "warnings", []);
        var warningMarkup = warnings.length ? "<div class=\"ayu-setup-warning-list\">" + warnings.map(function (warning) {
            var tone = setupRiskTone(warning.severity || "medium");
            return "<div class=\"ayu-note ayu-note-" + escapeHtml(tone) + "\"><strong>" + escapeHtml(warning.title || "Setup warning") + "</strong><p>" + escapeHtml(warning.body || "") + "</p>" + (warning.action ? "<small>" + escapeHtml(warning.action) + "</small>" : "") + "</div>";
        }).join("") + "</div>" : "<div class=\"ayu-note ayu-note-green\">No elevated network exposure is included in this setup plan.</div>";

        var restartItems = getByPath(recipe, "restart_required", []);
        var restartMarkup = restartItems.length ? "<div class=\"ayu-setup-restart\"><h4>Applies after restart</h4>" + restartItems.map(function (item) {
            var commands = (getByPath(item, "command_examples", []) || []).map(function (command) { return "<code>" + escapeHtml(command) + "</code>"; }).join(" ");
            var services = (getByPath(item, "services", []) || []).join(", ");
            return "<div class=\"ayu-note ayu-note-amber\"><strong>" + escapeHtml(item.label || "Restart required") + "</strong><p>" + commands + "</p><small>" + escapeHtml(services) + "</small></div>";
        }).join("") + "</div>" : "";

        var sections = getByPath(recipe, "sections", []);
        var sectionMarkup = sections.length ? "<div class=\"ayu-setup-section-grid\">" + sections.map(function (section) {
            return "<div class=\"ayu-setup-section-card\"><div>" + badge(prettyLabel(section.status || "ready"), section.status === "restart_required" || section.status === "guarded" ? "amber" : "blue") + "</div><strong>" + escapeHtml(section.label || "") + "</strong><p>" + escapeHtml(section.description || "") + "</p></div>";
        }).join("") + "</div>" : "";

        var agents = getByPath(recipe, "recommended_agents", []);
        var agentMarkup = agents.length ? "<div class=\"ayu-setup-agent-strip\">" + agents.slice(0, 12).map(function (agent) {
            return "<span title=\"" + escapeHtml(agent.rationale || "") + "\">" + escapeHtml(agent.label || agent.name) + "</span>";
        }).join("") + "</div>" : "<div class=\"ayu-note\">No agent suggestions yet. Choose one or more feature branches.</div>";

        var nextActions = getByPath(recipe, "next_actions", []);
        var nextMarkup = nextActions.length ? "<ol class=\"ayu-setup-next-actions\">" + nextActions.map(function (item) {
            return "<li>" + escapeHtml(item) + "</li>";
        }).join("") + "</ol>" : "";

        var hasGuarded = Object.keys(getByPath(recipe, "guarded_config_patch", {}) || {}).length > 0 || restartItems.length > 0;
        var guardedButton = hasGuarded ? button("Apply guarded network settings", "setup-apply-guarded-recipe", "danger", "shield") : "";
        var tooltip = getByPath(recipe, "tooltips.model_picker_agent", "Use Model Picker Agent when you are unsure which model to choose.");

        return "<section id=\"ayu-setup-recipe\" class=\"ayu-setup-recipe-card\"><div class=\"ayu-setup-recipe-head\"><div><h3>" + escapeHtml(recipe.title || "Setup plan") + "</h3><p>" + escapeHtml(recipe.summary || "") + "</p></div>" + badge(prettyLabel(recipe.risk_level || "low") + " risk", setupRiskTone(recipe.risk_level || "low")) + "</div>" + renderSetupCoverage(payload) + warningMarkup + restartMarkup + sectionMarkup + "<div class=\"ayu-soft-divider\"></div><div class=\"ayu-setup-recipe-block\"><h4>Recommended agents</h4>" + agentMarkup + "</div><div class=\"ayu-note ayu-note-blue\"><strong>Model picker:</strong> " + escapeHtml(tooltip) + "</div>" + nextMarkup + "<div class=\"ayu-inline-actions\">" + button("Refresh preview", "setup-preview-recipe", "secondary", "refresh") + button("Apply safe settings", "setup-apply-recipe", "primary", "save") + guardedButton + button("Open Websites & Browser", "setup-open-screen:page", "ghost", "page") + button("Open Video & Calls", "setup-open-screen:video", "ghost", "video") + "</div></section>";
    }

    function renderSetupGuidedMap() {
        var payload = setupProfilesPayload();
        var answers = currentSetupAnswers();
        return "<div class=\"ayu-setup-map\"><div class=\"ayu-setup-map-head\"><div><h2>Guided setup map</h2><p>Choose how this server should run. AutoYou builds a plain setup plan from the current server, Websites & Browser, AI, memory, speech, messaging, and agent settings.</p></div>" + badge("Local config", "green") + "</div><div class=\"ayu-note ayu-note-blue\">Loopback-only AutoPair remains the safe default. LAN access and public proxy links are opt-in choices with warnings and separate apply steps.</div>" + renderSetupProfileCards(answers) + renderSetupDecisionBranches(answers) + renderSetupRecipePreview(payload, answers) + "</div>";
    }

    function telegramApprovedSenderIds() {
        var discovered = getByPath(state.operations, "telegramSenders.approved_sender_ids", []);
        var values = splitListValue(getByPath(state.forms, "messaging.telegram.acl_sender_ids", ""));
        if (Array.isArray(discovered)) {
            values = values.concat(discovered);
        }
        var seen = {};
        return values.map(function (value) {
            return String(value || "").trim();
        }).filter(function (value) {
            if (!value || seen[value]) {
                return false;
            }
            seen[value] = true;
            return true;
        });
    }

    function telegramSenderSelection() {
        var selected = [];
        var seen = {};
        Array.prototype.forEach.call(document.querySelectorAll("input[data-telegram-sender-id]:checked"), function (checkboxEl) {
            if (checkboxEl.disabled) {
                return;
            }
            var senderId = String(checkboxEl.getAttribute("data-telegram-sender-id") || "").trim();
            if (senderId && !seen[senderId]) {
                selected.push(senderId);
                seen[senderId] = true;
            }
        });
        return selected;
    }

    function renderTelegramDiscoveredSenders(payload) {
        payload = payload || {};
        var error = getByPath(state.operations, "errors.telegramSenders", "");
        var senders = Array.isArray(getByPath(payload, "senders", [])) ? getByPath(payload, "senders", []) : [];
        var errorMarkup = error ? "<div class=\"ayu-note ayu-note-amber\">" + escapeHtml(error) + "</div>" : "";
        if (!senders.length) {
            var emptyCopy = getByPath(payload, "configured", false)
                ? "No Telegram Bot senders have been observed yet. Send any message to the saved bot, then refresh this list."
                : "Save the Telegram Bot token first, then send any message to the bot to reveal the sender ID here.";
            return errorMarkup + "<div class=\"ayu-empty\">" + escapeHtml(emptyCopy) + "</div>";
        }
        return errorMarkup + "<div class=\"ayu-telegram-sender-list ayu-list\">" + senders.map(function (sender) {
            var senderId = String(getByPath(sender, "sender_id", "") || "").trim();
            var usernameLabel = String(getByPath(sender, "username_label", "") || "").trim();
            var chatId = String(getByPath(sender, "chat_id", "") || "").trim();
            var senderKey = String(getByPath(sender, "sender_key", "") || "").trim();
            var messageCount = Number(getByPath(sender, "message_count", 0)) || 0;
            var lastSeen = Number(getByPath(sender, "last_seen_at_s", 0)) || 0;
            var approved = Boolean(getByPath(sender, "approved", false));
            var authorized = Boolean(getByPath(sender, "authorized_now", false));
            var label = firstMeaningfulText(usernameLabel, senderId, chatId, senderKey, "Telegram Bot sender");
            var detailParts = [];
            if (senderId) {
                detailParts.push("ID " + senderId);
            }
            if (chatId && chatId !== senderId) {
                detailParts.push("Chat " + chatId);
            }
            if (getByPath(sender, "chat_type", "")) {
                detailParts.push(prettyLabel(getByPath(sender, "chat_type", "")));
            }
            detailParts.push(String(messageCount) + (messageCount === 1 ? " message" : " messages"));
            if (lastSeen) {
                detailParts.push("Last seen " + formatTimestamp(lastSeen));
            }
            if (getByPath(sender, "last_message_kind", "")) {
                detailParts.push("Last event " + prettyLabel(getByPath(sender, "last_message_kind", "")));
            }
            var selector = senderId && !approved
                ? "<label class=\"ayu-telegram-sender-check\"><input type=\"checkbox\" data-telegram-sender-id=\"" + escapeHtml(senderId) + "\"><span>Select</span></label>"
                : "<span class=\"ayu-hint\">" + escapeHtml(approved ? "Approved" : "No user ID") + "</span>";
            return "<div class=\"ayu-list-row ayu-telegram-sender-row\"><div class=\"ayu-list-copy\"><strong>" + escapeHtml(label) + "</strong><p>" + escapeHtml(detailParts.join(" | ")) + "</p></div><div class=\"ayu-inline-actions\">" + (approved ? badge("Approved", "green") : (authorized ? badge("Open", "blue") : badge("Pending", "amber"))) + selector + "</div></div>";
        }).join("") + "</div>";
    }

    function renderTelegramOnboardingBody(options) {
        options = options || {};
        var payload = state.operations.telegramSenders || {};
        var status = getByPath(state.bootstrap, "status", {});
        var telegramStatus = getByPath(status, "telegram.status", "Unknown");
        var botLabel = firstMeaningfulText(getByPath(status, "telegram.name", ""), getByPath(bootstrapConfig(), "telegram.bot_username", ""), getByPath(payload, "configured", false) ? "Configured" : "No Telegram Bot token saved");
        var senderCount = Number(getByPath(payload, "count", 0)) || 0;
        var approvedIds = telegramApprovedSenderIds();
        var approvedUsernames = splitListValue(getByPath(state.forms, "messaging.telegram.acl_usernames", ""));
        var actionSize = options.compact ? "sm" : "";
        return "<div class=\"ayu-telegram-onboarding\">" + renderStatusRows([
            { label: "Telegram Bot status", value: prettyLabel(telegramStatus), help: botLabel },
            { label: "Discovered Telegram Bot senders", value: String(senderCount), help: getByPath(payload, "updated_at_s", 0) ? ("Last refreshed " + formatTimestamp(getByPath(payload, "updated_at_s", 0))) : "Waiting for a sender refresh." },
            { label: "Approved Telegram Bot sender IDs", value: String(approvedIds.length), help: approvedIds.length ? joinList(approvedIds) : "No user ID is approved yet." },
            { label: "Approved usernames", value: String(approvedUsernames.length), help: approvedUsernames.length ? approvedUsernames.map(function (name) { return name.charAt(0) === "@" ? name : "@" + name; }).join(", ") : "Optional fallback when Telegram username is stable." }
        ]) + "<div class=\"ayu-note ayu-note-blue\">Save the Telegram Bot token, send any message to the bot, then approve the discovered sender ID. Discovery stores sender metadata only, not message text.</div><div class=\"ayu-grid-2\">" + field("Telegram Bot token", input("messaging.telegram.bot_token", { placeholder: "1234:abcd", type: "password" }), "Leave empty only if you want Telegram Bot disabled.") + field("Approved Telegram Bot sender IDs", input("messaging.telegram.acl_sender_ids", { placeholder: "123456789, 987654321" }), "Once at least one ID is approved, Telegram Bot remains locked to approved senders.") + "</div>" + field("Approved usernames", input("messaging.telegram.acl_usernames", { placeholder: "alice, bob" }), "Optional. Sender IDs are preferred because usernames can change.") + checkbox("messaging.telegram.access_gate_enabled", "Require approval before responding", "Recommended during setup so unknown senders are visible but cannot use Telegram Bot.") + checkbox("messaging.telegram.silent_unapproved_messages", "Silently ignore unapproved senders") + "<div class=\"ayu-inline-actions\">" + button("Save Telegram Bot", "save-telegram", "primary", "save", actionSize) + button("Start discovery", "telegram-start-discovery", "secondary", "play", actionSize) + button("Refresh senders", "telegram-refresh-senders", "ghost", "refresh", actionSize) + button("Approve selected", "telegram-approve-selected", "green", "check", actionSize) + button("Generate allow code", "telegram-allow-code", "secondary", "key", actionSize) + "</div><div class=\"ayu-soft-divider\"></div><h3 style=\"margin:0 0 10px;font-size:15px;\">Discovered Telegram Bot senders</h3>" + renderTelegramDiscoveredSenders(payload) + "</div>";
    }

    function telegramUserConfigPayload() {
        var source = getByPath(state.forms, "messaging.telegramUser", {});
        var payload = {
            enabled: Boolean(source.enabled),
            api_id: String(source.api_id || "").trim(),
            training_export_consent: Boolean(source.training_export_consent),
            prompt_builder: {
                enabled: Boolean(source.prompt_builder_enabled),
                application_agent: String(source.prompt_builder_agent || "codex_desktop_agent").trim()
            }
        };
        var appKey = String(source.api_hash || "").trim();
        if (appKey) {
            payload.api_hash = appKey;
        }
        return payload;
    }

    function renderTelegramUserMessages() {
        var payload = state.operations.telegramUserMessages || {};
        var messages = getByPath(payload, "messages", []);
        var error = getByPath(state.operations, "errors.telegramUserMessages", "");
        if (error) {
            return "<div class=\"ayu-note ayu-note-amber\">Saved Messages activity is unavailable right now. Try Refresh status.</div>";
        }
        if (!Array.isArray(messages) || !messages.length) {
            return "<div class=\"ayu-empty\">No Saved Messages activity is available yet.</div>";
        }
        return "<div class=\"ayu-list\">" + messages.map(function (message) {
            var direction = firstMeaningfulText(getByPath(message, "direction", ""), getByPath(message, "role", ""), "Saved Message");
            var kind = firstMeaningfulText(getByPath(message, "kind", ""), getByPath(message, "message_type", ""), getByPath(message, "media_type", ""), getByPath(message, "type", ""), "Message");
            var timestamp = getByPath(message, "timestamp", getByPath(message, "created_at", getByPath(message, "received_at", "")));
            return "<div class=\"ayu-list-row\"><div class=\"ayu-list-copy\"><strong>" + escapeHtml(prettyLabel(direction)) + "</strong><p>" + escapeHtml(prettyLabel(kind)) + "</p><small>" + escapeHtml(hasValue(timestamp) ? formatTimestamp(timestamp) : "Timestamp unavailable") + "</small></div></div>";
        }).join("") + "</div>";
    }

    function renderTelegramUserManagementBody() {
        var summary = buildTelegramUserStatusSummary();
        var status = summary.status || {};
        var statusError = getByPath(state.operations, "errors.telegramUserStatus", "");
        return (statusError ? "<div class=\"ayu-note ayu-note-amber\">Telegram User status is unavailable right now. Try Refresh status.</div>" : "") + renderStatusRows([
            { label: "Status", value: prettyLabel(summary.displayStatus), help: summary.connected ? "Connected to your Saved Messages." : "Connect your own Telegram account to use Saved Messages." },
            { label: "Connected", value: summary.connected ? "Yes" : "No" },
            { label: "Saved Messages only", value: getByPath(status, "saved_messages_only", true) ? "Yes" : "No" }
        ]) + "<div class=\"ayu-note ayu-note-blue\">Connect only a Telegram account you own. AutoYou uses Saved Messages only and does not manage other chats.</div>" + checkbox("messaging.telegramUser.enabled", "Enable Telegram User") + "<div class=\"ayu-grid-2\">" + field("Telegram app ID", input("messaging.telegramUser.api_id", { placeholder: "Telegram app ID", type: "text" }), "Get this once from your Telegram developer settings.") + field("Telegram app key", input("messaging.telegramUser.api_hash", { placeholder: "Leave blank to keep the saved key", type: "password" }), "Leave this blank unless you need to change it.") + "</div>" + checkbox("messaging.telegramUser.prompt_builder_enabled", "Enable Telegram Prompt Builder mode", "When enabled, Saved Messages are appended exactly to the selected desktop composer until you send exit confirm.") + field("Prompt Builder desktop agent", select("messaging.telegramUser.prompt_builder_agent", [{ value: "codex_desktop_agent", label: "Codex Desktop" }, { value: "claude_desktop_agent", label: "Claude Desktop" }]), "The website and Telegram mode use this same selected desktop application.") + checkbox("messaging.telegramUser.training_export_consent", "Allow local training tools to use Saved Messages", "Choose this only if you want this account's Saved Messages available to local training tools.") + "<div class=\"ayu-inline-actions\">" + button("Save Telegram User", "save-telegram-user", "primary", "save") + button("Open Prompt Builder", "open-prompt-builder", "secondary", "external", "sm") + button("Connect", "telegram-user-connect", "green", "qr", "sm") + button("Restart", "telegram-user-restart", "secondary", "refresh", "sm") + button("Disconnect", "telegram-user-disconnect", "danger", "trash", "sm") + "</div>";
    }

    function renderSetupScreen() {
        var setup = state.setup.payload || {};
        var status = getByPath(state.bootstrap, "status", {});
        var cfg = bootstrapConfig();
        var guides = getByPath(state.bootstrap, "metadata.guides", []);
        var securityModes = getByPath(state.bootstrap, "metadata.security_modes", []);
        var tunnelMeta = getByPath(state.bootstrap, "metadata.tunnelmole", {});
        var bootstrapGuide = Array.isArray(guides) ? guides.find(function (guide) { return guide.href === "/guides/bootstrap"; }) : null;
        var setupComplete = Boolean(getByPath(setup, "wizard_completed", getByPath(cfg, "onboarding.wizard_completed", false)));
        var completedAt = getByPath(setup, "wizard_completed_at", getByPath(cfg, "onboarding.wizard_completed_at", ""));
        var aiSummary = getByPath(setup, "ai_provider_summary", prettyLabel(getByPath(cfg, "ai_provider.provider", "ollama")));
        var messaging = getByPath(setup, "messaging", {});
        var connectivity = getByPath(setup, "connectivity", {});
        var ollama = getByPath(setup, "ollama", {});
        var localModels = getByPath(ollama, "local_models", []);
        var passwordChanged = Boolean(getByPath(setup, "password_changed", !getByPath(setup, "using_default_password", false)));
        var totpCount = Number(getByPath(setup, "totp_client_count", getByPath(state.bootstrap, "status.totp.usable_totp_client_count", 0))) || 0;
        var setupTelegramStatus = getByPath(messaging, "telegram.status", getByPath(status, "telegram.status", "Unknown"));
        var setupTelegramApprovedSenderCount = telegramApprovedSenderIds().length;
        var setupTelegramReady = Boolean(getByPath(messaging, "telegram.configured", false) || isConnectedStatus(setupTelegramStatus) || setupTelegramApprovedSenderCount > 0);
        var setupTelegramDetail = setupTelegramApprovedSenderCount
            ? String(setupTelegramApprovedSenderCount) + " approved Telegram Bot sender ID" + (setupTelegramApprovedSenderCount === 1 ? "" : "s")
            : (setupTelegramReady ? getByPath(messaging, "telegram.label", "Configured") : "Telegram Bot is not configured yet.");
        var setupTelegramTone = setupTelegramApprovedSenderCount > 0 ? "green" : (setupTelegramReady ? statusTone(setupTelegramStatus) : "gray");
        var setupTelegramUserSummary = buildTelegramUserStatusSummary();
        var setupTelegramUserStatus = setupPartnerStatusLabel(setupTelegramUserSummary.displayStatus, getByPath(messaging, "telegram_user.status", getByPath(status, "telegram_user.status", "Unknown")));
        var setupTelegramUserReady = Boolean(
            getByPath(messaging, "telegram_user.connected", false) ||
            setupTelegramUserSummary.connected ||
            isConnectedStatus(setupTelegramUserStatus)
        );
        var setupTelegramUserConfigured = Boolean(
            getByPath(messaging, "telegram_user.configured", false) ||
            getByPath(cfg, "telegram_user.enabled", false) ||
            setupTelegramUserReady
        );
        var setupTelegramUserDetail = setupTelegramUserReady
            ? "Your Saved Messages connection is ready."
            : "Connect your own Telegram account to use Saved Messages.";
        var setupSignalSummary = buildSignalStatusSummary();
        var setupSignalStatus = setupPartnerStatusLabel(setupSignalSummary.displayStatus, getByPath(messaging, "signal.status", getByPath(status, "signal.status", "Unknown")));
        var setupSignalReady = Boolean(
            getByPath(messaging, "signal.paired", false) ||
            setupSignalSummary.paired ||
            setupSignalSummary.phoneNumber ||
            isConnectedStatus(setupSignalSummary.displayStatus) ||
            isConnectedStatus(setupSignalStatus)
        );
        var setupSignalConfigured = Boolean(getByPath(messaging, "signal.configured", false) || getByPath(cfg, "signal.enabled", false) || setupSignalReady);
        var setupSignalDetail = setupSignalReady
            ? firstMeaningfulText(setupSignalSummary.phoneNumber, getByPath(messaging, "signal.label", ""), getByPath(status, "signal.name", ""), "Connected owner bridge is available.")
            : "Pair Signal when you need a phone-based fallback connection.";
        var setupWhatsappSummary = buildWhatsAppStatusSummary();
        var setupWhatsappStatus = setupPartnerStatusLabel(setupWhatsappSummary.displayStatus, getByPath(messaging, "whatsapp.status", getByPath(status, "whatsapp.status", "Unknown")));
        var setupWhatsappReady = Boolean(
            getByPath(messaging, "whatsapp.paired", false) ||
            setupWhatsappSummary.paired ||
            setupWhatsappSummary.ready ||
            setupWhatsappSummary.phoneNumber ||
            setupWhatsappSummary.snapshotReady ||
            setupWhatsappSummary.snapshotPhoneNumber ||
            isConnectedStatus(setupWhatsappStatus) ||
            ["READY", "CONNECTED"].indexOf(setupWhatsappSummary.clientState) !== -1
        );
        var setupWhatsappConfigured = Boolean(getByPath(messaging, "whatsapp.configured", false) || getByPath(cfg, "whatsapp.enabled", false) || setupWhatsappReady);
        var setupWhatsappDetail = setupWhatsappReady
            ? firstMeaningfulText(setupWhatsappSummary.phoneNumber, setupWhatsappSummary.snapshotPhoneNumber, getByPath(messaging, "whatsapp.label", ""), getByPath(status, "whatsapp.name", ""), "Connected owner bridge is available.")
            : "Pair WhatsApp when you need a phone-based fallback connection.";
        var anyMessagingPartner = Boolean(
            setupTelegramReady ||
            setupTelegramUserConfigured ||
            setupSignalConfigured ||
            setupWhatsappConfigured
        );
        var cloudConnected = Boolean(getByPath(status, "cloud.connected", false) || getByPath(connectivity, "cloud_connected", false));
        var activeStepIndex = Math.max(0, Math.min(state.setup.activeStep || 0, SETUP_WIZARD_STEPS.length - 1));
        var currentStep = SETUP_WIZARD_STEPS[activeStepIndex];
        var selectedSecurityMode = getByPath(state.forms, "security.mode", getByPath(cfg, "security.mode", "secure"));
        var securityHelp = SECURITY_MODE_HELP[selectedSecurityMode] || SECURITY_MODE_HELP.normal;
        var selectedSecurityTier = normalizeSecurityTier(getByPath(state.forms, "security.tier", getByPath(cfg, "security.tier", "B")));
        var securityTierHelp = SECURITY_TIER_HELP[selectedSecurityTier] || SECURITY_TIER_HELP.B;
        var securityModeOptions = Array.isArray(securityModes) && securityModes.length ? securityModes.map(function (mode) {
            return { value: mode.id, label: mode.label };
        }) : [
            { value: "normal", label: "Normal Mode (No Encryption)" },
            { value: "secure", label: "Secure Mode (Password Only)" },
            { value: "secure_professional", label: "Secure Professional (Password + 2FA)" },
            { value: "secure_professional_maximus", label: "Secure Professional Maximus" }
        ];
        var pairCodeOptions = getByPath(tunnelMeta, "pair_code_modes", []).map(function (item) {
            return { value: item.id, label: item.label };
        });
        if (!pairCodeOptions.length) {
            pairCodeOptions = [{ value: getByPath(state.forms, "connectivity.tunnelmole.pair_code_mode", "random_otp"), label: prettyLabel(getByPath(state.forms, "connectivity.tunnelmole.pair_code_mode", "random_otp")) }];
        }
        var connectionModeOptions = getByPath(tunnelMeta, "connection_modes", []).map(function (item) {
            return { value: item.id, label: item.label };
        });
        if (!connectionModeOptions.length) {
            connectionModeOptions = [{ value: getByPath(state.forms, "connectivity.tunnelmole.connection_mode", "timed"), label: prettyLabel(getByPath(state.forms, "connectivity.tunnelmole.connection_mode", "timed")) }];
        }
        var savedSecurityBaseline = String(getByPath(cfg, "security.mode", "secure"));
        var securityModeReady = savedSecurityBaseline !== "normal";
        if (savedSecurityBaseline === "secure_professional" || savedSecurityBaseline === "secure_professional_maximus") {
            securityModeReady = securityModeReady && totpCount > 0;
        }
        var stepReady = {
            "account": Boolean(getByPath(status, "cloud.account_signed_in", false) || getByPath(status, "software_update.signed_in", false)),
            "name-theme": Boolean(getByPath(cfg, "server.name", "")),
            "security-basics": securityModeReady,
            "ai-provider": Boolean(getByPath(cfg, "ai_provider.provider", "")),
            "manage-models": !isOllamaProvider(activeAiProvider()) || (Array.isArray(localModels) && localModels.length > 0),
            "messaging-partner": cloudConnected || anyMessagingPartner,
            "connectivity": cloudConnected || Boolean(getByPath(status, "tunnelmole.public_url", "") || getByPath(cfg, "tunnelmole.enabled", false)),
            "speech": Boolean(getByPath(cfg, "speech.stt.model", "")),
            "finish": setupComplete
        };
        var stepPickerMarkup = renderChoiceCards(SETUP_WIZARD_STEPS.map(function (step, index) {
            var ready = Boolean(stepReady[step.id]);
            var current = index === activeStepIndex;
            return {
                id: String(index),
                eyebrow: "Step " + (index + 1) + " of " + SETUP_WIZARD_STEPS.length,
                label: step.label,
                description: step.subtitle,
                badgeLabel: current ? "Current" : (ready ? "Ready" : "Pending"),
                badgeTone: current ? "blue" : (ready ? "green" : "gray")
            };
        }), String(activeStepIndex), "setup-step");
        var progressMarkup = (state.setup.loading && !state.setup.payload ? "<div class=\"ayu-empty\">Loading first-run bootstrap state...</div>" : "<div class=\"ayu-note ayu-note-" + escapeHtml(setupComplete ? "green" : "amber") + "\">" + escapeHtml(setupComplete ? "Bootstrap checklist marked complete." : "Bootstrap checklist is still open for this server.") + "</div>") + renderStatusRows([
            { label: "Checklist state", value: setupComplete ? "Complete" : "Needs setup", help: "Controls whether first-run onboarding still surfaces as pending." },
            { label: "Completed at", value: completedAt || "Not completed yet", mono: true },
            { label: "Current step", value: currentStep.label, help: currentStep.subtitle },
            { label: "Bootstrap guide", value: bootstrapGuide ? bootstrapGuide.title : "No bootstrap guide returned" }
        ]) + stepPickerMarkup;
        var currentStepBody = "";

        if (currentStep.id === "account") {
            var accountSignedIn = Boolean(getByPath(status, "cloud.account_signed_in", false) || getByPath(status, "software_update.signed_in", false));
            var accountEmail = String(getByPath(status, "cloud.email", "") || "");
            currentStepBody = "<div class=\"ayu-note ayu-note-" + escapeHtml(accountSignedIn ? "green" : "blue") + "\">"
                + escapeHtml(accountSignedIn
                    ? "This server is signed in to a free AutoYou account. Software updates and rewards are active. Paid Cloud Pair remains optional and separate."
                    : "Sign in to a free AutoYou account. It costs nothing, needs no card, and enables software updates and rewards. Paid Cloud Pair remains optional and separate.")
                + "</div>"
                + renderStatusRows([
                    { label: "Account", value: accountSignedIn ? (accountEmail || "Signed in") : "Not signed in", mono: Boolean(accountEmail) },
                    { label: "Signed updates", value: accountSignedIn ? "Enabled" : "Needs an account", help: "Update manifests are served only to a signed-in AutoYou install." },
                    { label: "Server password", value: "Unchanged", help: "Your AutoYou account and this server's password are separate credentials. Signing in does not alter the password." }
                ])
                + "<div class=\"ayu-soft-divider\"></div><div class=\"ayu-inline-actions\">"
                + "<a class=\"ayu-link-btn ayu-btn ayu-btn-" + (accountSignedIn ? "secondary" : "primary") + " ayu-btn-sm\" href=\"/api/cloud/link-start\" target=\"_blank\" rel=\"noopener noreferrer\">"
                + icon("cloud") + "<span>" + (accountSignedIn ? "Re-link AutoYou account" : "Sign in free to AutoYou") + "</span></a>"
                + button("Refresh state", "refresh-bootstrap", "ghost", "refresh", "sm")
                + "</div>"
                + "<div class=\"ayu-note ayu-note-blue\"><strong>Next step:</strong> Continue to Finish. You can sign in later from Overview if you want to skip this now."
                + "<div class=\"ayu-inline-actions\">" + button("Continue to Finish", "setup-next", "secondary", "check", "sm") + "</div></div>";
        } else if (currentStep.id === "name-theme") {
            currentStepBody = "<div class=\"ayu-note\">Name this server and choose the admin theme first. The name appears during pairing, in client headers, logs, and exported setup QR codes.</div>" + renderStatusRows([
                { label: "Server name", value: getByPath(state.forms, "overview.serverName", getByPath(cfg, "server.name", "AutoYou-Server")) },
                { label: "Admin shell theme", value: prettyLabel(getByPath(state.forms, "overview.adminTheme", "dark")) }
            ]) + "<div class=\"ayu-soft-divider\"></div>" + field("Server name", input("overview.serverName", { placeholder: "AutoYou-Server" }), "Shown to clients during pairing and in exported setup QR codes.") + field("Admin shell theme", select("overview.adminTheme", [{ value: "dark", label: "Dark" }, { value: "light", label: "Light" }]), "Changes the admin shell only. Agent Websites follows each device's system appearance.") + "<div class=\"ayu-inline-actions\">" + button("Save name & theme", "save-overview", "primary", "save") + "</div><div class=\"ayu-note ayu-note-blue\"><strong>Next step:</strong> Continue to Security & 2FA to harden the admin baseline.<div class=\"ayu-inline-actions\">" + button("Continue to Security & 2FA", "setup-next", "secondary", "shield", "sm") + "</div></div>";
        } else if (currentStep.id === "security-basics") {
            currentStepBody = "<div class=\"ayu-note ayu-note-" + escapeHtml(passwordChanged ? "green" : "blue") + "\">" + escapeHtml(passwordChanged ? "A custom pairing password is saved for this server." : "Default password autoyou123 is active for first setup. Rotate it here when ready.") + "</div>" + renderStatusRows([
                { label: "Password", value: passwordChanged ? "Custom saved" : "Default autoyou123" },
                { label: "Current security mode", value: prettyLabel(selectedSecurityMode), help: securityHelp.description },
                { label: "Shared authenticator", value: totpCount ? "Configured" : "Not configured", help: "One shared authenticator setup is used for Secure Professional pairing, authenticator pair-code mode, and admin elevation." }
            ]) + "<div class=\"ayu-soft-divider\"></div><h3 style=\"margin:0 0 10px;font-size:15px;\">Password and security mode</h3>" + renderPasswordDraftFields() + renderPasswordGeneratorTools() + field("Security mode", select("security.mode", securityModeOptions), securityHelp.description) + field("Pairing security tier", select("security.tier", SECURITY_TIER_OPTIONS), securityTierHelp) + "<div class=\"ayu-inline-actions\">" + button("Save security baseline", "security-save-baseline", "primary", "save") + button("Update password only", "security-save-password", "secondary", "key") + button("Save security mode only", "security-save-mode", "ghost", "shield") + "</div><div class=\"ayu-soft-divider\"></div><h3 style=\"margin:0 0 10px;font-size:15px;\">Shared authenticator</h3><div class=\"ayu-note ayu-note-blue\">AutoYou keeps one shared authenticator setup for Secure Professional pairing, authenticator pair-code mode, and admin elevation.</div>" + renderTotpManagementBody() + "<div class=\"ayu-soft-divider\"></div><h3 style=\"margin:0 0 10px;font-size:15px;\">Automatic Configuration Setup</h3>" + renderZeroTouchPairingBody() + "<div class=\"ayu-note ayu-note-blue\"><strong>Next step:</strong> Continue to AI & Models once the security baseline is saved.<div class=\"ayu-inline-actions\">" + button("Continue to AI & Models", "setup-next", "secondary", "cpu", "sm") + "</div></div>";
        } else if (currentStep.id === "ai-provider") {
            currentStepBody = "<div class=\"ayu-note\">Pick the AI path that powers this machine first. The rest of the wizard adjusts around that decision.</div>" + renderAiProviderSettingsBody();
        } else if (currentStep.id === "manage-models") {
            currentStepBody = "<div class=\"ayu-note\">Local models stay separate from provider choice so you can switch back to on-device AI without rebuilding the library.</div>" + renderManageModelsBody();
        } else if (currentStep.id === "messaging-partner") {
            var partnerRows = [
                { label: "Cloud Pair", status: cloudConnected ? "Connected" : getByPath(status, "cloud.status", getByPath(status, "cloud.detail", "Not linked")), detail: cloudConnected ? getByPath(status, "cloud.email", "Connected") : "Recommended - the easiest hosted pairing path. Link to AutoYou Cloud below.", tone: cloudConnected ? "green" : statusTone(getByPath(status, "cloud.status", getByPath(status, "cloud.detail", ""))) },
                { label: "Telegram Bot", status: setupTelegramStatus, detail: setupTelegramDetail, tone: setupTelegramTone },
                { label: "Telegram User", status: setupTelegramUserStatus, detail: setupTelegramUserDetail, tone: setupTelegramUserConfigured ? statusTone(setupTelegramUserStatus) : "gray" },
                { label: "Signal", status: setupSignalStatus, detail: setupSignalDetail, tone: setupSignalConfigured ? statusTone(setupSignalStatus) : "gray" },
                { label: "WhatsApp", status: setupWhatsappStatus, detail: setupWhatsappDetail, tone: setupWhatsappConfigured ? statusTone(setupWhatsappStatus) : "gray" }
            ];
            currentStepBody = "<div class=\"ayu-note\">Messaging partners are only required when Cloud Pair is not your only remote path. Keep at least one fallback configured for non-cloud pairing flows.</div><div class=\"ayu-list\">" + partnerRows.map(function (row) {
                return "<div class=\"ayu-list-row\"><div class=\"ayu-list-copy\"><strong>" + escapeHtml(row.label) + "</strong><p>" + escapeHtml(row.detail) + "</p></div><div>" + badge(row.status || "Unknown", row.tone || "gray") + "</div></div>";
            }).join("") + "</div><div class=\"ayu-soft-divider\"></div><h3 style=\"margin:0 0 10px;font-size:15px;\">Telegram Bot onboarding</h3>" + renderTelegramOnboardingBody({ compact: true }) + "<div class=\"ayu-note ayu-note-blue\">Telegram User is separate from Telegram Bot. It connects your own account and keeps AutoYou in Saved Messages only.</div><div class=\"ayu-inline-actions\">" + (cloudConnected ? "" : "<a class=\"ayu-link-btn ayu-btn ayu-btn-primary\" href=\"/api/cloud/link-start\" target=\"_blank\" rel=\"noopener noreferrer\">" + icon("cloud") + "<span>Link to AutoYou Cloud</span></a>") + button("Open messaging", "setup-open-screen:messaging", cloudConnected ? "primary" : "secondary", "msg") + button("Open connectivity", "setup-open-screen:connectivity", "secondary", "wifi") + "</div>";
        } else if (currentStep.id === "connectivity") {
            currentStepBody = renderStatusRows([
                { label: "Cloud link", value: cloudConnected ? "Connected" : getByPath(status, "cloud.status", getByPath(status, "cloud.detail", "Not linked")), help: cloudConnected ? getByPath(status, "cloud.email", "Connected") : "Use Cloud Pair when you want AutoYou-hosted pairing." },
                { label: "Public URL", value: getByPath(status, "tunnelmole.public_url", getByPath(connectivity, "tunnelmole_public_url", "No public URL")), mono: true },
                { label: "Pair URL", value: getByPath(status, "tunnelmole.pair_url", getByPath(connectivity, "pair_url", "No pair URL")), mono: true }
            ]) + "<div class=\"ayu-soft-divider\"></div>" + renderSetupCloudLinkNote(cloudConnected) + "<div class=\"ayu-soft-divider\"></div><h3 style=\"margin:0 0 4px;font-size:15px;\">Public link</h3><div class=\"ayu-hint\" style=\"margin-bottom:6px\">Use this when you are not using Cloud Pair and clients connect from outside your LAN.</div>" + checkbox("connectivity.tunnelmole.enabled", "Enable public link", "Gives this server a public link so clients outside your LAN can pair.") + "<div class=\"ayu-grid-2\">" + field("Timed lifetime (minutes)", input("connectivity.tunnelmole.timeout_minutes", { type: "number" })) + field("Pairing code timeout", input("connectivity.tunnelmole.otp_timeout_minutes", { type: "number" })) + field("Pair-code mode", select("connectivity.tunnelmole.pair_code_mode", pairCodeOptions)) + field("Connection mode", select("connectivity.tunnelmole.connection_mode", connectionModeOptions)) + "</div>" + checkbox("connectivity.tunnelmole.otp_multiuse", "Allow pairing code reuse within the configured timeout window") + checkbox("connectivity.tunnelmole.url_only_pair", "Share URL only (most secure)", "Secure Professional + Authenticator pair-code mode: AutoYou sends only the public link. Clients sign in with the shared 2FA setup key and password you handed over separately - nothing secret travels over the message.") + checkbox("connectivity.tunnelmole.auto_start_on_boot", "Auto-connect public URL on startup", "Brings your persistent public link back online automatically each time AutoYou starts. Requires a Public Proxy plan; free servers skip this and keep using on-demand links.") + "<div class=\"ayu-inline-actions\">" + button("Save link settings", "save-tunnelmole", "primary", "save") + button("Start link", "service:tunnelmole:start", "green", "play", "sm") + button("Stop link", "service:tunnelmole:stop", "secondary", "stop", "sm") + button("Refresh status", "service:tunnelmole:refresh", "ghost", "refresh", "sm") + button("Open full connectivity", "setup-open-screen:connectivity", "ghost", "wifi", "sm") + "</div>";
        } else if (currentStep.id === "speech") {
            currentStepBody = (state.speechLibrary.loading ? "<div class=\"ayu-empty\">Loading speech model status...</div>" : "") + renderSpeechProviderSettingsBody() + "<div class=\"ayu-soft-divider\"></div>" + renderSpeechRecognitionBody();
        } else {
            currentStepBody = "<div class=\"ayu-note ayu-note-" + escapeHtml(setupComplete ? "green" : "amber") + "\">" + escapeHtml(setupComplete ? "The bootstrap checklist is already complete. Reopen it if you want the first-run reminder to return." : "Review the checklist below, then mark bootstrap complete when the local path is ready.") + "</div><div class=\"ayu-list\">" + SETUP_WIZARD_STEPS.filter(function (step) {
                return step.id !== "finish";
            }).map(function (step) {
                return "<div class=\"ayu-list-row\"><div class=\"ayu-list-copy\"><strong>" + escapeHtml(step.label) + "</strong><p>" + escapeHtml(step.subtitle) + "</p></div><div>" + badge(stepReady[step.id] ? "Ready" : "Pending", stepReady[step.id] ? "green" : "amber") + "</div></div>";
            }).join("") + "</div>" + (bootstrapGuide ? ("<div class=\"ayu-soft-divider\"></div><a class=\"ayu-link-card\" href=\"" + escapeHtml(bootstrapGuide.href) + "\"><strong>" + escapeHtml(bootstrapGuide.title) + "</strong><p>" + escapeHtml(bootstrapGuide.description || "") + "</p></a>") : "") + "<div class=\"ayu-inline-actions\">" + button(setupComplete ? "Reopen checklist" : "Mark bootstrap complete", setupComplete ? "setup-reopen" : "setup-complete", setupComplete ? "secondary" : "primary", setupComplete ? "refresh" : "check") + button("Open overview", "setup-open-screen:overview", "ghost", "home") + button("Open guides", "setup-open-screen:guides", "secondary", "book") + "</div>";
        }

        var stepActions = (activeStepIndex > 0 ? button("Previous step", "setup-prev", "secondary", "refresh", "sm") : "") + (activeStepIndex < SETUP_WIZARD_STEPS.length - 1 ? button("Next step", "setup-next", "primary", "bolt", "sm") : "");

        return "<div class=\"ayu-screen\"><div class=\"ayu-hero\"><div class=\"ayu-hero-copy\"><h1>Setup & Boot</h1><p>Walk the first-run path on one screen: harden the local admin baseline, pick the runtime, load models, choose remote fallback paths, and finish with a review step that works on mobile.</p></div><div class=\"ayu-inline-actions\">" + button("Refresh setup", "setup-refresh", "secondary", "refresh") + (bootstrapGuide ? "<a class=\"ayu-link-btn ayu-btn ayu-btn-ghost\" href=\"" + escapeHtml(bootstrapGuide.href) + "\">" + icon("book") + "<span>Bootstrap guide</span></a>" : "") + "</div></div>" + renderSetupGuidedMap() + "<div id=\"ayu-setup-progress\" class=\"ayu-scroll-target\">" + panel("Bootstrap progress", "Select a step to work in sequence, or move with Previous and Next without leaving the page.", progressMarkup, stepActions) + "</div><div id=\"ayu-setup-current\" class=\"ayu-scroll-target\">" + panel(currentStep.label, currentStep.subtitle, currentStepBody, stepActions) + "</div></div>";
    }

    function renderAiScreen() {
        var webSearchOn = Boolean(getByPath(state.forms, "aiAgent.internet_search_enabled", true));
        var webSearchMarkup = checkbox("aiAgent.internet_search_enabled", "Let AutoYou search the web", "Turn this on before asking AutoYou for live web results. Turn it off for offline-only answers.")
            + "<div class=\"ayu-note ayu-note-" + (webSearchOn ? "green" : "amber") + "\"><strong>" + escapeHtml(webSearchOn ? "Web search is allowed." : "Web search is off.") + "</strong><p style=\"margin:6px 0 0;\">" + escapeHtml(webSearchOn ? "AutoYou may use the installed search helper when a prompt needs current internet results." : "AutoYou will answer from local model knowledge, saved memory, and installed tools only.") + "</p></div>";
        var storeClientNames = Boolean(getByPath(state.forms, "clientIdentity.store_client_names_in_history", false));
        var clientNameMarkup = checkbox(
            "clientIdentity.store_client_names_in_history",
            "Store client names in chat history",
            "Off by default. When off - or in Incognito - names are only visible while the client is connected."
        ) + "<div class=\"ayu-note ayu-note-" + (storeClientNames ? "green" : "amber") + "\"><strong>" + escapeHtml(storeClientNames ? "Client names can be saved with chat history." : "Client names are live-only.") + "</strong><p style=\"margin:6px 0 0;\">Incognito and message-storage-off modes always keep client names out of history.</p></div>";
        return "<div class=\"ayu-screen\"><div class=\"ayu-hero\"><div class=\"ayu-hero-copy\"><h1>AI & Models</h1><p>Choose the AI path that powers AutoYou on this machine, then manage local models separately from provider selection so the screen only shows the settings that matter.</p></div><div class=\"ayu-inline-actions\">" + button("Refresh AI data", "ai-refresh", "secondary", "refresh") + button("Open Agent Studio", "nav:agents", "ghost", "agents") + button("Open Permissions", "nav:permissions", "ghost", "shield", "sm") + "</div></div><div class=\"ayu-grid-2\">" + panel("AI provider", "Pick the local or cloud AI path first. Secret fields stay unchanged unless you enter new values.", renderAiProviderSettingsBody()) + panel("AutoYou AI", "Controls for AutoYou's own agent host.", checkbox("aiAgent.enabled", "AutoYou AI enabled") + checkbox("aiAgent.auto_start", "Auto-start AutoYou AI on server boot") + clientNameMarkup + field("Memory backend", select("aiAgent.memory_backend", [{ value: "legacy", label: "AutoYou SQLite" }, { value: "cognee", label: "Cognee self-hosted" }]), "AutoYou SQLite keeps long-term chat and event memory. Cognee adds graph/vector memory when installed.") + webSearchMarkup + field("AutoYou AI port", input("aiAgent.port", { type: "number" })) + "<div class=\"ayu-inline-actions\">" + button("Save AI settings", "save-ai-agent", "primary", "save") + button("Start", "service:ai:start", "green", "play", "sm") + button("Stop", "service:ai:stop", "secondary", "stop", "sm") + button("Restart", "service:ai:restart", "ghost", "refresh", "sm") + button(webSearchOn ? "Turn web search off" : "Turn web search on", "ai-toggle-internet", "secondary", "bolt", "sm") + "</div>") + "</div><div class=\"ayu-grid-2\">" + panel("Model behavior", "Tune AutoYou AI rather than the model catalog.", renderModelBehaviorBody()) + panel("Manage Models", "Browse the local Ollama library, search catalogs, and keep downloads visible in one place.", renderManageModelsBody()) + "</div></div>";
    }

    function isMainAdminAgentName(name) {
        return name === "admin_agent" || name === "autoyou_admin_agent";
    }

    function defaultStudioAgentName(overview) {
        var list = Array.isArray(overview) ? overview : [];
        var admin = list.find(function (item) { return item && isMainAdminAgentName(item.agent_name); });
        if (admin) {
            return admin.agent_name;
        }
        var installed = list.find(function (item) { return item && item.installed; });
        if (installed) {
            return installed.agent_name;
        }
        return list.length ? list[0].agent_name : "";
    }

    function renderAgentStudioPicker(agentOverview, agentDetails) {
        var list = Array.isArray(agentOverview) ? agentOverview : [];
        if (!list.length) {
            return "";
        }
        var options = list.map(function (agent) {
            var isAdmin = isMainAdminAgentName(agent.agent_name);
            var active = agent.agent_name === state.selectedAgentName;
            var detail = getByPath(agentDetails, agent.agent_name, {});
            var installed = getByPath(detail, "installed", agent.installed);
            var blocked = getByPath(detail, "state_tone", agent.state_tone) === "blocked" || Boolean(getByPath(detail, "runtime_blocked", getByPath(agent, "runtime_blocked", false)));
            var stateKey = isAdmin ? "main" : (blocked ? "blocked" : (installed ? "installed" : "available"));
            var stateBadge = isAdmin ? "Main" : (blocked ? "Blocked" : (installed ? "Installed" : "Available"));
            return "<button type=\"button\" class=\"ayu-studio-pick ayu-studio-pick-" + stateKey + (active ? " active" : "") + "\" data-action=\"select-agent:" + escapeHtml(agent.agent_name) + "\" aria-pressed=\"" + (active ? "true" : "false") + "\"><span class=\"ayu-studio-pick-name\">" + escapeHtml(agent.display_name || agent.agent_name) + "</span><span class=\"ayu-studio-pick-tag\">" + escapeHtml(stateBadge) + "</span></button>";
        }).join("");
        return "<div class=\"ayu-studio-picker\"><div class=\"ayu-field-label\">Studio agent</div><div class=\"ayu-studio-pick-list\">" + options + "</div><div class=\"ayu-field-hint\">Pick the agent to manage below. The AutoYou main agent is marked <strong>Main</strong>; sub-agents can be installed or uninstalled.</div></div>";
    }

    function renderDesktopAssetSetupPanel() {
        var catalog = state.desktopAssets.payload || {};
        var agents = Array.isArray(catalog.agents) ? catalog.agents : [];
        if (state.desktopAssets.loading && !catalog.agents) {
            return panel("Desktop app control assets", "Prepare user-local control packs for the desktop apps you run on this computer.", "<div class=\"ayu-empty\">Loading desktop agent setup...</div>");
        }
        if (state.desktopAssets.error) {
            return panel("Desktop app control assets", "Prepare user-local control packs for the desktop apps you run on this computer.", "<div class=\"ayu-note ayu-note-red\"><strong>Desktop asset setup could not load.</strong><p>" + escapeHtml(state.desktopAssets.error) + "</p><div class=\"ayu-inline-actions\">" + button("Retry", "desktop-assets-refresh", "secondary", "refresh", "sm") + "</div></div>");
        }
        if (!agents.length) {
            return panel("Desktop app control assets", "Prepare user-local control packs for the desktop apps you run on this computer.", "<div class=\"ayu-empty\">This AutoYou build does not include any desktop agent templates.</div>");
        }
        var currentName = state.desktopAssets.selectedAgent || getByPath(state.forms, "desktopAssets.agent_name", agents[0].agent_name);
        var selected = activateDesktopAssetAgent(currentName) || agents[0];
        var platform = String(getByPath(state.forms, "desktopAssets.platform", catalog.platform || "windows"));
        var packRows = (selected.asset_packs || []).map(function (pack) {
            var version = pack.app_version
                ? "app " + String(pack.app_version)
                : (pack.app_version_min || pack.app_version_max
                    ? String(pack.app_version_min || "any") + " to " + String(pack.app_version_max || "any")
                    : "any app version");
            var scale = pack.display_scale === "any" || pack.display_scale == null ? "any scale" : String(pack.display_scale) + "x";
            return "<div class=\"ayu-list-row\"><div class=\"ayu-list-copy\"><strong>" + escapeHtml(pack.asset_pack_id) + "</strong><p>" + escapeHtml([pack.platform || "any platform", version, pack.theme || "any theme", scale].join(" | ")) + "</p><small>" + escapeHtml(String(pack.target_count || 0)) + " calibrated targets</small></div><div class=\"ayu-inline-actions\">" + button("Remove", "desktop-pack-remove", "danger", "trash", "sm", "data-agent=\"" + escapeHtml(selected.agent_name) + "\" data-storage-id=\"" + escapeHtml(pack.storage_id) + "\"") + "</div></div>";
        }).join("");
        if (!packRows) {
            packRows = "<div class=\"ayu-empty\">No user-local pack is installed for this desktop agent yet.</div>";
        }
        var fileInput = "<input type=\"file\" accept=\".zip,application/zip\" data-role=\"desktop-pack-input\" style=\"display:none\" aria-label=\"Select desktop asset ZIP\">";
        var preferences = field("Default appearance", input("desktopAssets.theme", { placeholder: "auto, any, light, dark, high_contrast, custom:name" }), "Use a built-in theme name or custom:<name>. Auto follows this computer's appearance.")
            + field("Default display scale", input("desktopAssets.display_scale", { placeholder: "auto, any, or 1.0 to 4.0" }), "Scale is a multiplier such as 1.0 or 1.5. Auto reads this computer's display scaling where available.");
        var promptFields = field("Desktop agent", select("desktopAssets.agent_name", agents.map(function (agent) { return { value: agent.agent_name, label: agent.title || agent.agent_name }; })))
            + field("Target platform", select("desktopAssets.platform", [{ value: "windows", label: "Windows" }, { value: "macos", label: "macOS" }, { value: "linux", label: "Linux" }]))
            + field("Installed app version", input("desktopAssets.app_version", { placeholder: "Leave blank to have your assistant inspect it" }), "The assistant can identify the installed version if you leave this blank.");
        var body = "<div class=\"ayu-note ayu-note-blue\"><strong>Use your own coding assistant to prepare a local pack.</strong><p>AutoYou gives you a prompt to copy into your existing Claude, ChatGPT, or Codex workspace. It does not contact those services. Import the resulting ZIP here. Generated manifests and cropped control images stay in this computer's private AutoYou data folder and are excluded from source and compiled release bundles.</p></div>"
            + "<div class=\"ayu-grid-2\">" + promptFields + "</div><div class=\"ayu-grid-2\">" + preferences + "</div>"
            + "<div class=\"ayu-inline-actions\">" + button("Save preferences", "desktop-asset-save-preferences", "secondary", "save", "sm") + button("Prepare setup prompt", "desktop-asset-generate-prompt", "primary", "copy", "sm") + button("Import ZIP", "desktop-pack-select", "ghost", "file", "sm") + button("Refresh packs", "desktop-assets-refresh", "ghost", "refresh", "sm") + fileInput + "</div>"
            + "<div class=\"ayu-field-hint\">Only use a blank or synthetic app state. Keep chat text, account details, full-screen captures, and other personal content out of the ZIP. A local pack may still be subject to the desktop app's terms and rights in its interface.</div>"
            + "<div class=\"ayu-soft-divider\"></div><div class=\"ayu-list ayu-agent-grid\">" + packRows + "</div>";
        return panel("Desktop app control assets", "Prepare user-local control packs for the desktop apps you run on this computer.", body);
    }

    function renderAgentScreen() {
        var payload = getByPath(state.bootstrap, "agents", {});
        var listingPayload = state.agentWorkbench.payload || payload;
        var agentOverview = getByPath(payload, "agent_overview", []);
        var agentDetails = getByPath(payload, "agent_details", {});
        var installedAgents = agentOverview.filter(function (item) { return item.installed; });
        var availableAgents = agentOverview.filter(function (item) { return !item.installed; });
        var selectedSummary = state.selectedAgentName ? agentDetails[state.selectedAgentName] : null;
        var selectedDetail = state.selectedAgentName && getByPath(state.agentWorkbench.detail, "agent_name", "") === state.selectedAgentName ? state.agentWorkbench.detail : selectedSummary;
        var instructionPayload = state.instructions.payload;
        var sections = Array.isArray(getByPath(instructionPayload, "sections", [])) ? getByPath(instructionPayload, "sections", []) : [];
        var selectedVariable = state.instructions.selectedVariable || (sections.length ? sections[0].variable : "");
        var activeSection = sections.find(function (section) { return section.variable === selectedVariable; }) || sections[0];
        var sectionEditor = activeSection ? "<div class=\"ayu-grid-2\"><div class=\"ayu-list\">" + sections.map(function (section) {
            return "<button type=\"button\" class=\"ayu-tab" + (section.variable === selectedVariable ? " active" : "") + "\" data-action=\"instructions-section:" + escapeHtml(section.variable) + "\">" + escapeHtml(section.label) + "</button>";
        }).join("") + "</div><div>" + field(activeSection.label, textarea("agentWorkbench.section_" + activeSection.variable, { rows: 11 }), activeSection.description || "") + "<div class=\"ayu-inline-actions\">" + button("Save section builder", "instructions-save-sections", "primary", "save") + button("Reload prompt", "instructions-reload", "secondary", "refresh") + button("Revert to fallback", "instructions-revert", "danger", "trash") + "</div></div></div>" : "<div class=\"ayu-empty\">Prompt sections are unavailable. Switch to raw prompt mode.</div>";
        var rawEditor = field("Raw AGENT_INSTRUCTION", textarea("agentWorkbench.raw_instructions", { rows: 12, extraClass: "ayu-mono" }), getByPath(instructionPayload, "read_only_notice", "Edit the live root prompt directly.")) + "<div class=\"ayu-inline-actions\">" + button("Save raw prompt", "instructions-save-raw", "primary", "save") + button("Reload", "instructions-reload", "secondary", "refresh") + button("Revert to fallback", "instructions-revert", "danger", "trash") + button("Start runtime", "service:ai:start", "green", "play", "sm") + button("Stop runtime", "service:ai:stop", "secondary", "stop", "sm") + button("Restart runtime", "service:ai:restart", "ghost", "refresh", "sm") + "</div>";
        var builderSuite = getByPath(listingPayload, "builder_suite", {});
        var builderSuiteInstalled = Boolean(getByPath(builderSuite, "installed", false));
        var builderSuiteModel = getByPath(builderSuite, "recommended_ollama_model", "qwen3.8:27b");
        var builderSuiteNote = "<div class=\"ayu-note\">" + escapeHtml(builderSuiteInstalled ? "Agent Builder, Coding Agent, and Website Builder are installed. Recommended local Ollama model for this workflow: " + builderSuiteModel + "." : "Agent Builder, Coding Agent, and Website Builder can be installed together. Recommended local Ollama model for this workflow: " + builderSuiteModel + ".") + "</div>";
        var builderSuiteAction = builderSuiteInstalled
            ? button("Builder suite installed", "agent-install-builder-suite", "green", "check", "sm", "disabled aria-disabled=\"true\"")
            : button("Install builder suite", "agent-install-builder-suite", "primary", "plus", "sm");

        var selectedHasWebsite = Boolean(selectedDetail && (getByPath(selectedDetail, "has_frontend", false) || getByPath(selectedDetail, "frontend", null) || getByPath(selectedDetail, "frontend_manifest", null)));
        var selectedBadges = selectedDetail ? [
            badge(getByPath(selectedDetail, "installed", false) ? "Installed" : (getByPath(selectedDetail, "draft_only", false) ? "Draft only" : "Available"), getByPath(selectedDetail, "installed", false) ? "green" : (getByPath(selectedDetail, "draft_only", false) ? "amber" : "blue")),
            badge(getByPath(selectedDetail, "builtin_agent", false) ? "Built-in" : "Workspace", getByPath(selectedDetail, "builtin_agent", false) ? "purple" : "gray"),
            badge(getByPath(selectedDetail, "draft_exists", false) ? "Draft ready" : "No draft", getByPath(selectedDetail, "draft_exists", false) ? "amber" : "gray"),
            (selectedHasWebsite
                ? badge(getByPath(selectedDetail, "frontend_control.enabled", false) ? "Website enabled" : "Website disabled", getByPath(selectedDetail, "frontend_control.enabled", false) ? "blue" : "gray")
                : badge("No website", "gray"))
        ].join("") : "";
        var selectedMeta = selectedDetail ? renderStatusRows([
            { label: "Runtime name", value: getByPath(selectedDetail, "runtime_agent_name", getByPath(selectedDetail, "agent_name", state.selectedAgentName)), mono: true },
            { label: "Live source", value: getByPath(selectedDetail, "live_source.source_kind", getByPath(selectedDetail, "live_exists", false) ? "available" : "not published yet") },
            { label: "Workspace root", value: getByPath(listingPayload, "workspace_agents_root", ""), mono: true },
            { label: "Draft root", value: getByPath(listingPayload, "workspace_drafts_root", ""), mono: true },
            { label: "Runtime policy", value: getByPath(selectedDetail, "runtime_block_reason", getByPath(listingPayload, "runtime_policy_note", "Workspace drafts stay isolated until you publish them.")) }
        ]) : "<div class=\"ayu-empty\">Select an agent to open its dedicated builder, coding, and frontend workbenches.</div>";
        var selectedActions = [];
        if (selectedDetail) {
            if (getByPath(selectedDetail, "can_install", false)) {
                selectedActions.push(button("Install live agent", "agent-install:" + escapeHtml(selectedDetail.agent_name), "primary", "plus", "sm"));
            } else if (getByPath(selectedDetail, "can_uninstall", false)) {
                selectedActions.push(button("Uninstall live agent", "agent-uninstall:" + escapeHtml(selectedDetail.agent_name), "danger", "trash", "sm"));
            }
            if (getByPath(selectedDetail, "frontend_control", null)) {
                selectedActions.push(button(getByPath(selectedDetail, "frontend_control.enabled", false) ? "Disable website" : "Enable website", "agent-frontend:" + escapeHtml(selectedDetail.agent_name), "secondary", "bolt", "sm", getByPath(selectedDetail, "runtime_blocked", false) ? "disabled" : ""));
            } else if (!selectedHasWebsite) {
                selectedActions.push(button("No website", "agent-frontend-none", "inert", "bolt", "sm", "disabled aria-disabled=\"true\" title=\"This agent does not ship a website. Use the Agent Website tab to scaffold one.\""));
            }
            if (agentFrontendLaunchUrl(selectedDetail) && !getByPath(selectedDetail, "runtime_blocked", false)) {
                selectedActions.push("<a class=\"ayu-link-btn ayu-btn ayu-btn-ghost ayu-btn-sm\" href=\"" + escapeHtml(agentFrontendLaunchUrl(selectedDetail)) + "\" target=\"_blank\" rel=\"noreferrer\">" + icon("external") + "<span>Open website</span></a>");
            }
            if (agentFrontendCopyPath(selectedDetail)) {
                selectedActions.push(button("Copy path", "agent-workbench-copy-path", "ghost", "copy", "sm"));
            }
            selectedActions.push(button("Reload detail", "agent-workbench-reload", "secondary", "refresh", "sm"));
            selectedActions.push(button("Restart AutoYou AI", "service:ai:restart", "ghost", "bolt", "sm"));
        }
        var workbenchTabs = selectedDetail ? "<div class=\"ayu-tabs\">" + [
            { id: "builder", label: "Build \u0026 Publish" },
            { id: "coding", label: "Agent Instructions" },
            { id: "frontend", label: "Agent Website" }
        ].map(function (tab) {
            return "<button type=\"button\" class=\"ayu-tab" + (state.agentWorkbench.activeTab === tab.id ? " active" : "") + "\" data-action=\"agent-workbench-tab:" + escapeHtml(tab.id) + "\">" + escapeHtml(tab.label) + "</button>";
        }).join("") + "</div>" : "";
        var workbenchBody = !state.selectedAgentName
            ? "<div class=\"ayu-empty\">Pick an agent from the <strong>Studio agent</strong> selector above to open its build, instructions, and website workbenches.</div>"
            : (state.agentWorkbench.loading && !selectedDetail
                ? "<div class=\"ayu-empty\">Loading agent studio detail...</div>"
                : (selectedDetail
                    ? ("<div class=\"ayu-grid-2\"><div class=\"ayu-list-copy\"><strong>" + escapeHtml(getByPath(selectedDetail, "display_name", getByPath(selectedDetail, "agent_name", state.selectedAgentName))) + "</strong><p>" + escapeHtml(getByPath(selectedDetail, "description", "No description available.")) + "</p><div class=\"ayu-inline-actions\">" + selectedBadges + "</div></div><div>" + selectedMeta + "</div></div>" + (selectedActions.length ? "<div class=\"ayu-inline-actions\">" + selectedActions.join("") + "</div>" : "") + workbenchTabs + (state.agentWorkbench.activeTab === "coding" ? renderAgentCodingWorkbenchPane(selectedDetail) : (state.agentWorkbench.activeTab === "frontend" ? renderAgentFrontendWorkbenchPane(selectedDetail) : renderAgentBuilderWorkbenchPane(selectedDetail, listingPayload))))
                    : "<div class=\"ayu-empty\">The selected agent detail could not be loaded. Refresh the workbench and try again.</div>"));
        var studioPickerMarkup = renderAgentStudioPicker(agentOverview, agentDetails);
        var selectedMarkup = "<div id=\"ayu-agent-studio-anchor\" class=\"ayu-scroll-target\">" + panel("Agent Studio", "Pick the agent to manage, then build its lifecycle, edit instructions, and configure its website. Defaults to the AutoYou main agent.", studioPickerMarkup + workbenchBody) + "</div>";

        var installedMarkup = installedAgents.length ? "<div class=\"ayu-list ayu-agent-grid\">" + installedAgents.map(function (agent) {
            var control = getByPath(agentDetails, agent.agent_name + ".frontend_control", {});
            var agentDetail = getByPath(agentDetails, agent.agent_name, {});
            var frontendPath = agentFrontendLaunchUrl(agentDetail) || getByPath(agentDetail, "frontend_path", getByPath(control, "browser_path", ""));
            var hasWebsite = Boolean(getByPath(agent, "has_frontend", false) || getByPath(agentDetail, "has_frontend", false) || frontendPath);
            var websiteActions = hasWebsite
                ? button((control && control.enabled) ? "Disable website" : "Enable website", "agent-frontend:" + escapeHtml(agent.agent_name), "secondary", "bolt", "sm") + (frontendPath ? "<a class=\"ayu-link-btn ayu-btn ayu-btn-ghost ayu-btn-sm\" href=\"" + escapeHtml(frontendPath) + "\" target=\"_blank\" rel=\"noreferrer\">" + icon("external") + "<span>Open website</span></a>" : "")
                : button("No website", "agent-frontend-none", "inert", "bolt", "sm", "disabled aria-disabled=\"true\" title=\"This agent does not ship a website.\"");
            return "<div class=\"ayu-list-row\"><div class=\"ayu-list-copy\"><strong>" + escapeHtml(agent.display_name) + "</strong><p>" + escapeHtml(agent.description || "") + "</p><small class=\"ayu-code\">" + escapeHtml(agent.agent_name) + "</small><div class=\"ayu-inline-actions\">" + agentStatusBadges(agent) + "</div></div><div class=\"ayu-inline-actions\">" + button("Open studio", "select-agent:" + escapeHtml(agent.agent_name), "secondary", "info", "sm") + button("Uninstall", "agent-uninstall:" + escapeHtml(agent.agent_name), "danger", "trash", "sm") + websiteActions + "</div></div>";
        }).join("") + "</div>" : "<div class=\"ayu-empty\">No installed agents were found in the current registry.</div>";

        var availableMarkup = availableAgents.length ? "<div class=\"ayu-list ayu-agent-grid\">" + availableAgents.map(function (agent) {
            return "<div class=\"ayu-list-row\"><div class=\"ayu-list-copy\"><strong>" + escapeHtml(agent.display_name) + "</strong><p>" + escapeHtml(agent.description || "") + "</p><small class=\"ayu-code\">" + escapeHtml(agent.agent_name) + "</small><div class=\"ayu-inline-actions\">" + agentStatusBadges(agent) + "</div></div><div class=\"ayu-inline-actions\">" + button("Install", "agent-install:" + escapeHtml(agent.agent_name), "primary", "plus", "sm", getByPath(agent, "runtime_blocked", false) ? "disabled" : "") + button("Open studio", "select-agent:" + escapeHtml(agent.agent_name), "secondary", "info", "sm") + "</div></div>";
        }).join("") + "</div>" : "<div class=\"ayu-empty\">No additional agents are currently available to install.</div>";

        var scaffoldMarkup = "<div class=\"ayu-grid-2\">" + field("Agent name", input("agentWorkbench.name", { placeholder: "my_custom_agent" })) + field("Description", input("agentWorkbench.description", { placeholder: "A short description of what this agent does." })) + field("First tool name", input("agentWorkbench.tool_name", { placeholder: "handle_request" })) + field("Tool description", input("agentWorkbench.tool_description", { placeholder: "Handle the agent-specific request." })) + "</div><div class=\"ayu-note\">This creates a new agent draft on disk. Once created, open it in Agent Studio below to edit instructions, configure its website, and publish when ready.</div><div class=\"ayu-inline-actions\">" + button("Create agent draft", "agent-scaffold", "primary", "plus") + "</div>";

        var promptMarkup = renderAgentPromptRuntimeControl(instructionPayload) + "<div class=\"ayu-tabs\"><button type=\"button\" class=\"ayu-tab" + (state.instructions.mode === "sections" ? " active" : "") + "\" data-action=\"instructions-mode:sections\">Section builder</button><button type=\"button\" class=\"ayu-tab" + (state.instructions.mode === "raw" ? " active" : "") + "\" data-action=\"instructions-mode:raw\">Raw prompt</button></div>" + (state.instructions.loading ? "<div class=\"ayu-empty\">Loading prompt instructions...</div>" : (state.instructions.mode === "sections" ? sectionEditor : rawEditor));

        return "<div class=\"ayu-screen\"><div class=\"ayu-hero\"><div class=\"ayu-hero-copy\"><h1>Agents</h1><p>Edit the main agent's system prompt, install or build sub-agents, then open Agent Studio to manage instructions and agent websites.</p></div><div class=\"ayu-inline-actions\">" + button("Refresh agents", "refresh-bootstrap", "secondary", "refresh") + builderSuiteAction + button("Reload selected studio", "agent-workbench-reload", "ghost", "refresh") + button("Restart AutoYou AI", "service:ai:restart", "ghost", "bolt") + "</div></div>" + renderAgentRuntimeBanner(listingPayload) + builderSuiteNote + renderAgentRestartNotice() + panel("System Prompt - Main Agent", "The AutoYou main agent's root prompt. It governs the assistant's overall personality and behavior and sits above every installed sub-agent. Use section-builder mode for guided editing, or raw mode for direct control.", promptMarkup) + panel("Installed agents", "Sub-agents currently loaded into AutoYou AI. Open one in Agent Studio or toggle its website.", installedMarkup) + panel("Available to install", "Sub-agents and drafts ready to be installed.", availableMarkup) + "<div class=\"ayu-grid-2\">" + panel("Create New Agent", "Set up a new agent draft that you can then open in Agent Studio to customise.", scaffoldMarkup) + "<div></div></div>" + selectedMarkup + renderDesktopAssetSetupPanel() + "</div>";
    }

    function agentAppsUrl() {
        return browserPageRouteUrl("/websites");
    }

    function agentAppTile(route) {
        var app = route.app || {};
        var colors = Array.isArray(app.colors) ? app.colors : [];
        var safe = function (value, fallback) {
            return /^#[0-9a-f]{3,8}$/i.test(String(value || "")) ? String(value) : fallback;
        };
        var title = String(route.title || route.agent_name || "App");
        var tip = route.description ? title + " - " + route.description : title;
        return "<span class=\"ayu-app-chip\" title=\"" + valueAttr(tip) + "\"><span class=\"ayu-app-icon\" style=\"background:linear-gradient(150deg," + safe(colors[0], "#8e9bff") + "," + safe(colors[1], "#6a3df0") + ")\">" + escapeHtml(title.charAt(0).toUpperCase()) + "</span><span class=\"ayu-app-name\">" + escapeHtml(title) + "</span></span>";
    }

    function renderAgentAppsPanel(routes) {
        var apps = (Array.isArray(routes) ? routes : []).filter(function (route) {
            return String(route.agent_name || "");
        });
        var browser = getByPath(state.bootstrap, "status.browser", {});
        var isHome = String(getByPath(browser, "default_website.agent_name", "") || "") === "agent_websites";
        var strip = apps.length
            ? "<div class=\"ayu-apps-strip\">" + apps.map(agentAppTile).join("") + "</div>"
            : "<div class=\"ayu-empty\">No website apps are ready yet. Turn one on in Agents and it appears here and on every connected device.</div>";
        var url = agentAppsUrl();
        var actions = "<div class=\"ayu-inline-actions\"><a class=\"ayu-link-btn ayu-btn ayu-btn-primary ayu-btn-sm\" href=\"" + escapeHtml(url) + "\" target=\"_blank\" rel=\"noreferrer\">" + icon("external") + "<span>Open Agent Apps</span></a>"
            + button("Copy link", "apps-copy-link", "ghost", "copy", "sm")
            + (isHome ? badge("Home page for connected browsers", "green") : button("Use as home page", "set-default-website:agent_websites", "secondary", "bolt", "sm"))
            + "</div>";
        var note = "<div class=\"ayu-note ayu-note-blue\">Phones, desktops and browsers on your home network open this as an app store. Touch or hover to read about an app, hold and drag to rearrange, pinch to resize. Each person's layout is saved in their own browser, so nothing here changes it.</div>";
        return panel("Agent Apps", "Every website app on this computer, as one launcher.", strip + note + actions);
    }

    function renderPageScreen() {
        var routes = browserWebsiteRoutes();
        var websites = getByPath(state.forms, "page.advertisedWebsites", []);
        var bookmarks = getByPath(state.forms, "page.bookmarks", []);
        var tunnelmole = getByPath(state.bootstrap, "status.tunnelmole", {});
        var publicUrl = String(getByPath(tunnelmole, "public_url", "") || "");
        var hostingEnabled = Boolean(getByPath(state.forms, "page.websiteHosting.enabled", false));
        var hostingAgentName = String(getByPath(state.forms, "page.websiteHosting.agent_name", "") || "");
        var hostableRoutes = Array.isArray(routes) ? routes.filter(function (route) {
            return String(route.agent_name || "") && String(route.agent_name || "") !== "admin_agent";
        }) : [];
        var hostingOptions = hostableRoutes.map(function (route) {
            return { value: String(route.agent_name || ""), label: route.title || route.agent_name };
        });
        if (hostingAgentName && !hostingOptions.some(function (item) { return item.value === hostingAgentName; })) {
            hostingOptions.unshift({ value: hostingAgentName, label: prettyLabel(hostingAgentName) });
        }
        var selectedHostingRoute = hostableRoutes.find(function (route) {
            return String(route.agent_name || "") === hostingAgentName;
        }) || {};
        var hostingStatusTone = hostingEnabled && publicUrl ? "green" : (hostingEnabled ? "amber" : "gray");
        var hostingStatusText = hostingEnabled
            ? (publicUrl ? "Public link is on" : "Ready to turn on")
            : "Public website is off";
        var publicLinkMarkup = publicUrl
            ? "<div class=\"ayu-note ayu-note-green ayu-mono\">" + escapeHtml(publicUrl) + "</div>"
            : "<div class=\"ayu-note ayu-note-amber\">No public link is running yet.</div>";
        var hostingMarkup = "<div class=\"ayu-note ayu-note-blue\">Public Proxy gives one steady public link. Visitors see the website you choose here while AutoYou is open.</div>"
            + "<div class=\"ayu-grid-2\">" + field("Website visitors see", hostingOptions.length ? select("page.websiteHosting.agent_name", hostingOptions) : "<div class=\"ayu-empty\">No installed websites are ready yet.</div>", selectedHostingRoute.description || "Choose one of your installed agent websites.")
            + field("Public link", publicLinkMarkup, "Open this link to check what visitors see.") + "</div>"
            + checkbox("page.websiteHosting.enabled", "Show this website on my public link")
            + checkbox("page.websiteHosting.auto_start_on_boot", "Reconnect this link when AutoYou opens", "Works with the Public Proxy plan.")
            + "<div class=\"ayu-inline-actions\">" + badge(hostingStatusText, hostingStatusTone)
            + button("Save choice", "hosting-save", "primary", "save", "sm", hostingOptions.length ? "" : "disabled")
            + button("Turn on public website", "hosting-start", "green", "play", "sm", hostingOptions.length ? "" : "disabled")
            + button("Turn off website", "hosting-disable", "secondary", "stop", "sm")
            + (publicUrl ? "<a class=\"ayu-link-btn ayu-btn ayu-btn-ghost ayu-btn-sm\" href=\"" + escapeHtml(publicUrl) + "\" target=\"_blank\" rel=\"noreferrer\">" + icon("external") + "<span>Open public link</span></a>" : "")
            + "</div>";
        var scan = state.pageLocalScan || { running: false, discovered: [], checked: false };
        var scanStatusText = scan.running
            ? "Scanning common dev ports (3000, 5173, 8000, 8080...)..."
            : (scan.checked ? (scan.discovered && scan.discovered.length ? ("Discovered " + scan.discovered.length + " running localhost service" + (scan.discovered.length === 1 ? "" : "s") + ".") : "No active localhost servers detected on common ports.") : "Auto-discover active dev servers built with ChatGPT, Claude, Cursor, or Vite.");
        var scanDiscoveredList = scan.discovered && scan.discovered.length ? "<div class=\"ayu-list\" style=\"margin-top:.75rem\">" + scan.discovered.map(function (item) {
            return "<div class=\"ayu-list-row\"><div class=\"ayu-list-copy\"><strong>Port " + escapeHtml(item.port) + " (" + escapeHtml(item.label) + ")</strong><small>http://127.0.0.1:" + escapeHtml(item.port) + "</small></div><div class=\"ayu-inline-actions\">" + button("Add to shared websites", "page-add-discovered:" + item.port + ":" + item.label, "secondary", "plus", "sm") + "</div></div>";
        }).join("") + "</div>" : "";
        var scanMarkup = "<div class=\"ayu-note ayu-note-blue\"><div style=\"display:flex;justify-content:space-between;align-items:center;flex-wrap:wrap;gap:.5rem\"><div><strong>Localhost Dev App Discovery (ChatGPT / Claude / Vite / Next.js)</strong><p style=\"margin:.25rem 0 0;font-size:.85rem\">" + escapeHtml(scanStatusText) + "</p></div><div class=\"ayu-inline-actions\">"
            + button(scan.running ? "Scanning..." : "Scan localhost ports", "page-discover-localhost", "primary", "search", "sm", scan.running ? "disabled" : "")
            + button("Preset: Vite (5173)", "page-preset-website:5173:Vite Dev App", "ghost", "plus", "sm")
            + button("Preset: Next.js (3000)", "page-preset-website:3000:Next.js App", "ghost", "plus", "sm")
            + button("Preset: FastAPI (8000)", "page-preset-website:8000:FastAPI Service", "ghost", "plus", "sm")
            + "</div></div>" + scanDiscoveredList + "</div>";

        var siteRows = Array.isArray(websites) && websites.length ? websites.map(function (site, index) {
            return "<div class=\"ayu-panel ayu-panel-inline\"><div class=\"ayu-panel-body\"><div class=\"ayu-grid-2\">" + field("Port", "<input class=\"ayu-input\" data-bind=\"page.advertisedWebsites." + index + ".port\" type=\"number\" value=\"" + valueAttr(site.port || "") + "\">") + field("Label", "<input class=\"ayu-input\" data-bind=\"page.advertisedWebsites." + index + ".label\" value=\"" + valueAttr(site.label || "") + "\">") + field("Description", "<input class=\"ayu-input\" data-bind=\"page.advertisedWebsites." + index + ".description\" value=\"" + valueAttr(site.description || "") + "\">") + field("Forward URL", "<input class=\"ayu-input ayu-mono\" data-bind=\"page.advertisedWebsites." + index + ".target_url\" value=\"" + valueAttr(site.target_url || "") + "\" placeholder=\"Optional explicit localhost target\">") + "</div>" + checkbox("page.advertisedWebsites." + index + ".enabled", "Advertised website enabled") + checkbox("page.advertisedWebsites." + index + ".websocket_enabled", "Allow live app connections for this website") + "<div class=\"ayu-inline-actions\">" + button("Share P2P link", "page-share-website:" + index, "secondary", "external", "sm") + button("Remove", "page-remove-website:" + index, "danger", "trash", "sm") + "</div></div></div>";
        }).join("") : "<div class=\"ayu-empty\">No advertised website yet.</div>";
        var bookmarkRows = Array.isArray(bookmarks) && bookmarks.length ? bookmarks.map(function (bookmark, index) {
            var bookmarkUrl = bookmark.url || "";
            return "<div class=\"ayu-panel ayu-panel-inline\"><div class=\"ayu-panel-body\"><div class=\"ayu-grid-2\">" + field("Title", "<input class=\"ayu-input\" data-bind=\"page.bookmarks." + index + ".title\" value=\"" + valueAttr(bookmark.title || "") + "\">") + field("URL", "<input class=\"ayu-input ayu-mono\" data-bind=\"page.bookmarks." + index + ".url\" value=\"" + valueAttr(bookmarkUrl) + "\">") + field("Description", "<input class=\"ayu-input\" data-bind=\"page.bookmarks." + index + ".description\" value=\"" + valueAttr(bookmark.description || "") + "\">") + field("Bookmark ID", "<div class=\"ayu-note ayu-mono\">" + escapeHtml(bookmark.id || "generated on save") + "</div>") + "</div>" + checkbox("page.bookmarks." + index + ".enabled", "Bookmark enabled") + "<div class=\"ayu-inline-actions\">" + (bookmarkUrl ? "<a class=\"ayu-link-btn ayu-btn ayu-btn-ghost ayu-btn-sm\" href=\"" + escapeHtml(bookmarkUrl) + "\" target=\"_blank\" rel=\"noreferrer\">" + icon("external") + "<span>Open</span></a>" : "") + button("Copy URL", "page-copy-bookmark:" + index, "ghost", "copy", "sm") + button("Remove", "page-remove-bookmark:" + index, "danger", "trash", "sm") + "</div></div></div>";
        }).join("") : "<div class=\"ayu-empty\">No bookmarks added yet.</div>";

        var pageSettingsMarkup = "<div class=\"ayu-grid-2\">" + field("Page port", input("page.port", { type: "number" })) + field("Page feed window (days, 0 = entire feed)", input("page.feed_window_days", { type: "number", extraAttrs: "min=\"0\" max=\"3650\" inputmode=\"numeric\"" })) + field("Other shared pages theme", select("page.theme", [{ value: "light", label: "Light" }, { value: "dark", label: "Dark" }])) + field("Primary browser forward port", "<div class=\"ayu-note ayu-note-green\">" + escapeHtml(getByPath(state.bootstrap, "admin.browser_forward_port", "")) + "</div>") + "</div>" + checkbox("page.auto_start", "Auto-start Websites & Browser") + "<div class=\"ayu-inline-actions\">" + button("Save page settings", "save-page", "primary", "save") + button("Start", "service:page:start", "green", "play", "sm") + button("Stop", "service:page:stop", "secondary", "stop", "sm") + button("Restart", "service:page:restart", "ghost", "refresh", "sm") + "</div>";

        var forwardingMarkup = checkbox("page.custom_forward_enabled", "Enable custom forwarding", "When enabled, remote browser traffic prefers the custom port over the Websites & Browser port.")
            + field("Custom forward port", input("page.custom_forward_port", { type: "number" }), "Leave equal to the page port to disable the override.")
            + "<div class=\"ayu-inline-actions\">" + button("Save forwarding", "save-page-access", "primary", "save") + button("Manage permissions", "nav:permissions", "ghost", "shield", "sm") + "</div>";

        var addWebsiteMarkup = "<div class=\"ayu-note ayu-note-blue\">Use advertised websites for explicit same-port localhost mirroring. Normal agent websites stay on the primary browser port under /agent/name/.</div><div class=\"ayu-soft-divider\"></div><div class=\"ayu-grid-2\">" + field("Port", input("page.newWebsite.port", { type: "number", placeholder: "3000" })) + field("Label", input("page.newWebsite.label", { placeholder: "Docs UI" })) + field("Description", input("page.newWebsite.description", { placeholder: "Short description" })) + field("Forward URL", input("page.newWebsite.target_url", { placeholder: "Optional explicit target URL" })) + "</div>" + checkbox("page.newWebsite.enabled", "Add as enabled") + checkbox("page.newWebsite.websocket_enabled", "Allow live app connections for the new site") + "<div class=\"ayu-inline-actions\">" + button("Add & save website", "page-add-website", "secondary", "plus") + button("Save website edits", "save-page", "primary", "save") + "</div>";

        var addBookmarkMarkup = "<div class=\"ayu-soft-divider\"></div><div class=\"ayu-grid-2\">" + field("Title", input("page.newBookmark.title", { placeholder: "My bookmark" })) + field("URL", input("page.newBookmark.url", { placeholder: "https://example.com" })) + field("Description", input("page.newBookmark.description", { placeholder: "Short description" })) + field("Enabled", checkbox("page.newBookmark.enabled", "Add as enabled")) + "</div><div class=\"ayu-inline-actions\">" + button("Add & save bookmark", "page-add-bookmark", "secondary", "plus") + button("Save bookmark edits", "save-page", "primary", "save") + button("Clear bookmarks", "page-clear-bookmarks", "danger", "trash", "sm") + "</div>";

        var routesMarkup = Array.isArray(routes) && routes.length ? "<div class=\"ayu-list\">" + routes.map(function (route) {
            var routeUrl = routeLaunchUrl(route);
            return "<div class=\"ayu-list-row\"><div class=\"ayu-list-copy\"><strong>" + escapeHtml(route.title || route.route_id || route.path || "Route") + "</strong><small class=\"ayu-code\">" + escapeHtml(routeDisplayUrl(route)) + "</small></div><div class=\"ayu-inline-actions\">" + badge(routeBadgeLabel(route), routeBadgeTone(route)) + (route.agent_name ? badge(routeAuthBadgeLabel(route), routeAuthBadgeTone(route)) : "") + (routeUrl ? "<a class=\"ayu-link-btn ayu-btn ayu-btn-ghost ayu-btn-sm\" href=\"" + escapeHtml(routeUrl) + "\" target=\"_blank\" rel=\"noreferrer\">" + icon("external") + "<span>Open</span></a>" : "") + "</div></div>";
        }).join("") + "</div>" : "<div class=\"ayu-empty\">No browser routes were discovered yet.</div>";

        var agentSessionsMarkup = "<div class=\"ayu-note ayu-note-blue\">Shared sign-in applies to eligible agent websites in the same browser. Existing sign-ins in this browser are recognized when you save; another browser activates sharing when it revisits a website where it is already signed in. Agents can opt out individually.</div>"
            + checkbox("page.agentWebsitesSecurity.disable_otp", "Disable OTP on all agent websites", "Overrides per-agent OTP settings. The Admin UI keeps its separate password protection.")
            + (getByPath(state.forms, "page.agentWebsitesSecurity.disable_otp", false) ? "<div class=\"ayu-note ayu-note-red\"><strong>Security risk</strong><p>Anyone who can reach an agent website can access its data and actions without an authenticator code. Use this only on a trusted local network. The Admin UI password remains required.</p></div>" : "")
            + checkbox("page.agentWebsitesSecurity.require_otp", "Require OTP login on all agent websites by default", "Gates websites that do not have a per-agent override, except those marked to stay open. Ignored while OTP is disabled globally.")
            + checkbox("page.agentWebsitesSecurity.shared_session_enabled", "Allow one OTP sign-in to unlock multiple agent websites", "Applies only while OTP is enabled and only to eligible websites in the same browser.")
            + field("Shared session length (days)", input("page.agentWebsitesSecurity.shared_session_ttl_days", { type: "number" }))
            + "<div class=\"ayu-inline-actions\">" + button("Save", "agent-websites-security-save", "primary", "save", "sm") + button("Sign out of all agent sessions", "agent-sessions-sign-out-all", "danger", "bolt", "sm") + "</div>";

        return "<div class=\"ayu-screen\"><div class=\"ayu-hero\"><div class=\"ayu-hero-copy\"><h1>Websites & Browser</h1><p>Manage website controls, forwarding, advertised websites, and server-managed browser bookmarks.</p></div><div class=\"ayu-inline-actions\">" + button("Restart Websites & Browser", "service:page:restart", "ghost", "refresh") + button("Manage permissions", "nav:permissions", "secondary", "shield") + "</div></div>" + renderAgentAppsPanel(routes) + "<div class=\"ayu-grid-2\">" + panel("Websites & Browser settings", "Agent Websites follows each device's system appearance; the theme setting applies to other shared pages.", pageSettingsMarkup) + panel("Browser forwarding", "Choose the port used when paired browsers reach local websites.", forwardingMarkup) + "</div>" + panel("Bookmarks", "Add external or local URLs to the same Website Shortcuts list already used by desktop, iOS, and Android clients.", bookmarkRows + addBookmarkMarkup) + panel("Advertised websites", "Add local HTTP services for the AutoYou browser and keep live connection access explicit.", siteRows + addWebsiteMarkup) + panel("Website management", "Choose which agent website opens from your public link.", hostingMarkup) + panel("Agent website sessions", "Control cross-agent session sharing and force everyone signed out of every agent website.", agentSessionsMarkup) + panel("Browser routes", "Path-routed agent websites and explicit same-port routes visible to browser clients. The badge next to each agent shows whether it currently requires an authenticator code.", routesMarkup) + "</div>";
    }

    function formatTimestamp(value) {
        if (!hasValue(value)) {
            return "Unknown";
        }
        var numeric = Number(value);
        if (!isNaN(numeric) && numeric > 0) {
            if (numeric < 1000000000000) {
                numeric *= 1000;
            }
            return new Date(numeric).toLocaleString();
        }
        var parsed = new Date(value);
        if (!isNaN(parsed.getTime())) {
            return parsed.toLocaleString();
        }
        return String(value);
    }

    function formatDurationSeconds(value) {
        var total = Math.max(0, Math.round(Number(value) || 0));
        var hours = Math.floor(total / 3600);
        var minutes = Math.floor((total % 3600) / 60);
        var seconds = total % 60;
        if (hours) {
            return hours + "h " + minutes + "m";
        }
        if (minutes) {
            return minutes + "m " + seconds + "s";
        }
        return seconds + "s";
    }

    function renderJsonNote(payload, tone) {
        return "<pre class=\"ayu-note" + (tone ? " ayu-note-" + escapeHtml(tone) : "") + " ayu-mono\">" + escapeHtml(JSON.stringify(payload || {}, null, 2)) + "</pre>";
    }

    function renderDatachannelSessionList(datachannel) {
        var connections = getByPath(datachannel, "connections", []);
        if (Array.isArray(connections) && connections.length) {
            return "<div class=\"ayu-list\">" + connections.map(function (connection, index) {
                var sessionId = String(getByPath(connection, "session_id", ""));
                var label = String(getByPath(connection, "label", "") || ("Client " + String(index + 1)));
                var lastPing = Number(getByPath(connection, "last_ping_timestamp", 0)) || 0;
                var isSelected = getByPath(state.forms, "liveOps.session_id", "") === sessionId;
                var ownership = String(getByPath(connection, "device_ownership", "") || "");
                var connectedVia = String(getByPath(connection, "connected_via", "") || "");
                var ownershipBadge = ownership ? badge(ownership === "own" ? "Own" : "Shared", ownership === "own" ? "green" : "amber") : "";
                return "<div class=\"ayu-list-row\"><div class=\"ayu-list-copy\"><strong>" + escapeHtml(label) + "</strong><small>" + (connectedVia ? escapeHtml("Via " + connectedVia) + " · " : "") + "Last check-in " + escapeHtml(lastPing ? formatTimestamp(lastPing) : "Not reported") + "</small></div><div class=\"ayu-inline-actions\">" + ownershipBadge + (isSelected ? badge("Selected", "green") : "") + (sessionId ? button("Select client", "live-target-client:" + String(index), isSelected ? "secondary" : "ghost", "bolt", "sm") : "") + "</div></div>";
            }).join("") + "</div>";
        }
        var lastPings = getByPath(datachannel, "last_ping_timestamps", {});
        var sessionIds = Object.keys(lastPings || {});
        if (!sessionIds.length) {
            return "<div class=\"ayu-empty\">No clients are connected right now.</div>";
        }
        return "<div class=\"ayu-list\">" + sessionIds.sort().map(function (sessionId, index) {
            var lastPing = Number(getByPath(lastPings, sessionId, 0)) || 0;
            var isSelected = getByPath(state.forms, "liveOps.session_id", "") === sessionId;
            return "<div class=\"ayu-list-row\"><div class=\"ayu-list-copy\"><strong>" + escapeHtml("Client " + String(index + 1)) + "</strong><small>Last check-in " + escapeHtml(lastPing ? formatTimestamp(lastPing) : "Not reported") + "</small></div><div class=\"ayu-inline-actions\">" + (isSelected ? badge("Selected", "green") : "") + button("Select client", "live-target-client:" + String(index), isSelected ? "secondary" : "ghost", "bolt", "sm") + "</div></div>";
        }).join("") + "</div>";
    }

    function renderSchedulerQueue(payload) {
        if (!payload) {
            return "<div class=\"ayu-empty\">Notification queue status has not loaded yet.</div>";
        }
        if (payload.success === false) {
            return "<div class=\"ayu-note ayu-note-red\">" + escapeHtml(getByPath(payload, "error", "Queue status is unavailable right now.")) + "</div>";
        }
        var queue = getByPath(payload, "queue", payload || {});
        var pendingCount = Number(getByPath(queue, "pending_count", 0)) || 0;
        var readyCount = Number(getByPath(queue, "ready_count", 0)) || 0;
        var retryingCount = Number(getByPath(queue, "retrying_count", 0)) || 0;
        var waitingCount = Number(getByPath(queue, "waiting_count", 0)) || 0;
        var oldestAgeSeconds = Number(getByPath(queue, "oldest_age_seconds", 0)) || 0;
        var items = getByPath(queue, "items", []);
        var summaryMarkup = renderStatusRows([
            { label: "Pending", value: String(pendingCount), help: pendingCount ? (String(waitingCount) + " waiting for the next delivery window") : "No queued reminder or task notifications." },
            { label: "Ready now", value: String(readyCount), help: readyCount ? "Can deliver on the next scheduler sweep." : "Nothing is ready right now." },
            { label: "Retrying", value: String(retryingCount), help: retryingCount ? "Previous attempts are backing off." : "No retries are backing off." },
            { label: "Oldest item", value: pendingCount ? formatDurationSeconds(oldestAgeSeconds) : "0s", help: getByPath(queue, "max_age_seconds", 0) ? ("Broadcast fallback after " + formatDurationSeconds(getByPath(queue, "max_age_seconds", 0))) : "Broadcast fallback applies when the queue expires." }
        ]);
        if (!pendingCount) {
            return summaryMarkup + "<div class=\"ayu-empty\">Queued reminder and task deliveries will appear here when AutoYou has to wait for a connected client or a saved messaging target.</div>";
        }
        var note = "Showing " + escapeHtml(String(getByPath(queue, "displayed_count", Array.isArray(items) ? items.length : 0))) + " of " + escapeHtml(String(pendingCount)) + " queued notification" + (pendingCount === 1 ? "" : "s") + ".";
        if (getByPath(queue, "next_retry_at_s", 0)) {
            note += " Next retry window opens " + escapeHtml(formatTimestamp(getByPath(queue, "next_retry_at_s", 0))) + ".";
        }
        var itemsMarkup = Array.isArray(items) && items.length ? "<div class=\"ayu-list\">" + items.map(function (item) {
            var tone = getByPath(item, "status", "") === "ready" ? "green" : ((Number(getByPath(item, "attempt_count", 0)) || 0) > 0 ? "amber" : "blue");
            var meta = [];
            if (getByPath(item, "owner_key", "") || getByPath(item, "canonical_user_id", "")) {
                meta.push("Paired client");
            }
            if (getByPath(item, "reply_target_label", "")) {
                meta.push("Primary " + getByPath(item, "reply_target_label", ""));
            }
            meta.push("Attempts " + String(getByPath(item, "attempt_count", 0)));
            meta.push("Age " + formatDurationSeconds(getByPath(item, "age_seconds", 0)));
            if (getByPath(item, "status", "") === "ready") {
                meta.push("Delivery window open");
            } else {
                meta.push("Next " + formatDurationSeconds(getByPath(item, "next_attempt_in_seconds", 0)));
            }
            return "<div class=\"ayu-list-row\"><div class=\"ayu-list-copy\"><strong>" + escapeHtml(getByPath(item, "source_label", getByPath(item, "source", "Scheduler"))) + "</strong><p>" + escapeHtml(getByPath(item, "message_preview", "(missing message)")) + "</p><small>" + escapeHtml(meta.join(" | ")) + "</small>" + (getByPath(item, "last_error", "") ? ("<div class=\"ayu-hint\">Last error: " + escapeHtml(getByPath(item, "last_error", "")) + "</div>") : "") + "</div><div>" + badge(prettyLabel(getByPath(item, "status", "waiting")), tone) + "</div></div>";
        }).join("") + "</div>" : "<div class=\"ayu-empty\">Queue details are not available right now.</div>";
        return summaryMarkup + "<div class=\"ayu-note\">" + note + "</div>" + itemsMarkup + (getByPath(queue, "has_more", false) ? "<div class=\"ayu-note\">Additional queued notifications are hidden to keep this view compact on mobile.</div>" : "");
    }

    function browserPageServiceBaseUrl() {
        var browser = getByPath(state.bootstrap, "status.browser", {});
        var pageUrl = String(browser.page_service_url || "").trim();
        if (pageUrl) {
            return pageUrl.replace(/\/+$/, "");
        }
        var pagePort = Number(browser.page_service_port || getByPath(state.bootstrap, "config.autoyou_page.port", 0)) || 8067;
        return "http://127.0.0.1:" + pagePort;
    }

    function joinBrowserBaseAndPath(base, path) {
        var normalizedBase = String(base || "").trim().replace(/\/+$/, "");
        var normalizedPath = String(path || "").trim();
        if (!normalizedPath) {
            return normalizedBase;
        }
        if (/^[a-z][a-z0-9+.-]*:/i.test(normalizedPath) || normalizedPath.indexOf("//") === 0) {
            return normalizedPath;
        }
        if (normalizedPath.charAt(0) !== "/") {
            normalizedPath = "/" + normalizedPath;
        }
        return normalizedBase + normalizedPath;
    }

    function browserProxyRootPrefix() {
        var pathname = String(getByPath(window, "location.pathname", "") || "");
        var marker = "/agent/";
        var index = pathname.indexOf(marker);
        return index > 0 ? pathname.slice(0, index) : "";
    }

    function isBrowserAgentProxyPath() {
        return String(getByPath(window, "location.pathname", "") || "").indexOf("/agent/") >= 0;
    }

    function resolveBrowserProxyPath(path) {
        var raw = String(path || "").trim();
        if (!raw || /^[a-z][a-z0-9+.-]*:/i.test(raw) || raw.indexOf("//") === 0 || raw.charAt(0) !== "/") {
            return raw;
        }
        var root = browserProxyRootPrefix();
        if (!root || raw === root || raw.indexOf(root + "/") === 0) {
            return raw;
        }
        return root + raw;
    }

    function browserPageRouteUrl(path) {
        var resolvedPath = resolveBrowserProxyPath(path);
        if (!resolvedPath || /^[a-z][a-z0-9+.-]*:/i.test(resolvedPath) || resolvedPath.indexOf("//") === 0) {
            return resolvedPath;
        }
        if (isBrowserAgentProxyPath()) {
            return resolvedPath;
        }
        return joinBrowserBaseAndPath(browserPageServiceBaseUrl(), resolvedPath);
    }

    function pathProxyRouteUrl(route) {
        var mode = String(getByPath(route, "route_mode", "") || "").toLowerCase();
        if (mode !== "path_proxy") {
            return "";
        }
        var proxyPath = String(route.proxy_path || route.launch_path || route.open_url || route.path || "").trim();
        if (isBrowserAgentProxyPath() && proxyPath && proxyPath !== "/") {
            return browserPageRouteUrl(proxyPath);
        }
        var explicit = String(getByPath(route, "path_proxy_url", "") || "").trim();
        if (explicit) {
            return explicit;
        }
        if (!proxyPath || proxyPath === "/") {
            return "";
        }
        return browserPageRouteUrl(proxyPath);
    }

    function routeLaunchUrl(route) {
        if (!route) {
            return "";
        }
        var localUrl = String(route.local_url || "").trim();
        var path = String(route.path || "").trim();
        var proxyPath = String(route.proxy_path || route.launch_path || "").trim();
        var launchUrl = String(route.open_url || route.launch_url || "").trim();
        var pathProxyUrl = pathProxyRouteUrl(route);
        if (pathProxyUrl) {
            return pathProxyUrl;
        }
        if (isBrowserAgentProxyPath() && proxyPath && proxyPath.indexOf("/agent/") === 0) {
            return browserPageRouteUrl(proxyPath);
        }
        if (asBoolean(route.uses_direct_forward_port, false) && localUrl) {
            return localUrl;
        }
        if (launchUrl && launchUrl !== "/") {
            return launchUrl;
        }
        if (proxyPath && proxyPath !== "/") {
            return proxyPath;
        }
        if (localUrl) {
            return localUrl;
        }
        return path || "";
    }

    function routeDisplayUrl(route) {
        if (!route) {
            return "";
        }
        var pathProxyUrl = pathProxyRouteUrl(route);
        if (pathProxyUrl) {
            return pathProxyUrl;
        }
        return String(route.open_url || route.proxy_path || route.launch_path || route.local_url || route.path || "").trim();
    }

    function browserWebsiteRoutes() {
        var browser = getByPath(state.bootstrap, "status.browser", {});
        var websiteRoutes = getByPath(browser, "agent_website_routes", []);
        if (Array.isArray(websiteRoutes) && websiteRoutes.length) {
            return websiteRoutes;
        }
        var legacyRoutes = getByPath(browser, "browser_port_routes", []);
        return Array.isArray(legacyRoutes) ? legacyRoutes : [];
    }

    function routeBadgeLabel(route) {
        var mode = String(getByPath(route, "route_mode", "") || "").toLowerCase();
        if (mode === "path_proxy") {
            return "PATH_PROXY";
        }
        if (mode === "direct_forward") {
            return "DIRECT_PORT";
        }
        if (mode === "advertised_direct") {
            return "ADVERTISED";
        }
        return String(getByPath(route, "kind", "route") || "route").toUpperCase();
    }

    function routeBadgeTone(route) {
        var mode = String(getByPath(route, "route_mode", "") || "").toLowerCase();
        if (mode === "path_proxy") {
            return "blue";
        }
        if (mode === "direct_forward") {
            return "purple";
        }
        if (mode === "advertised_direct" || route.kind === "advertised") {
            return "amber";
        }
        return "gray";
    }

    function routeAuthBadgeLabel(route) {
        return route.auth_mode === "open" ? "OPEN" : "OTP REQUIRED";
    }

    function routeAuthBadgeTone(route) {
        return route.auth_mode === "open" ? "green" : "amber";
    }

    function renderBrowserRoutesPanelBody(routes, browser) {
        var defaultWebsite = getByPath(browser, "default_website", {});
        var currentDefaultName = String(getByPath(defaultWebsite, "agent_name", "") || "");
        var directoryIsDefault = currentDefaultName === "agent_websites";
        var defaultTitle = getByPath(defaultWebsite, "title", currentDefaultName) || "page_agent";
        var note = "<div class=\"ayu-note ayu-note-blue\">Default website connected browser clients open: <strong>" + escapeHtml(defaultTitle) + "</strong></div>";
        var list = Array.isArray(routes) && routes.length
            ? "<div class=\"ayu-list\">" + routes.map(function (route) {
                var name = String(route.agent_name || "");
                var isDefault = Boolean(route.default) || (name && name === currentDefaultName);
                var actions = badge(routeBadgeLabel(route), routeBadgeTone(route));
                if (isDefault) {
                    actions += badge("DEFAULT", "green");
                } else if (name) {
                    actions += button("Set default", "set-default-website:" + name, "ghost", "bolt", "sm");
                }
                return "<div class=\"ayu-list-row\"><div class=\"ayu-list-copy\"><strong>" + escapeHtml(route.title || route.route_id || "Route") + "</strong><small class=\"ayu-code\">" + escapeHtml(routeDisplayUrl(route)) + "</small></div><div class=\"ayu-inline-actions\">" + actions + "</div></div>";
            }).join("") + "</div>"
            : "<div class=\"ayu-empty\">No browser routes reported yet.</div>";
        var directoryAction = "<div class=\"ayu-inline-actions\">" + (directoryIsDefault ? badge("Agent Apps is the home page", "green") : button("Use Agent Apps as home", "set-default-website:agent_websites", "secondary", "bolt", "sm")) + "</div>";
        return note + list + directoryAction;
    }

    function tasksMissionControlUrl(routes) {
        var routeList = Array.isArray(routes) ? routes : [];
        var match = routeList.find(function (route) {
            var routeId = String(route.route_id || route.agent_name || "").toLowerCase();
            var agentName = String(route.agent_name || "").toLowerCase();
            var title = String(route.title || "").toLowerCase();
            var path = String(route.path || route.proxy_path || route.local_url || "").toLowerCase();
            return routeId === "tasks_agent" || routeId === "agent:tasks_agent" || agentName === "tasks_agent" || title.indexOf("tasks mission control") !== -1 || path.indexOf("tasks_agent") !== -1;
        });
        if (!match) {
            return "/agent/tasks_agent/";
        }
        return routeLaunchUrl(match) || "/agent/tasks_agent/";
    }

    function renderLiveSessionSummary(datachannel) {
        var connections = getByPath(datachannel, "connections", []);
        if (Array.isArray(connections) && connections.length) {
            return "<div class=\"ayu-list\">" + connections.slice(0, 4).map(function (connection, index) {
                var label = String(getByPath(connection, "label", "") || ("Client " + String(index + 1)));
                var lastPing = Number(getByPath(connection, "last_ping_timestamp", 0)) || 0;
                return "<div class=\"ayu-list-row\"><div class=\"ayu-list-copy\"><strong>" + escapeHtml(label) + "</strong><small>Last check-in " + escapeHtml(lastPing ? formatTimestamp(lastPing) : "Not reported") + "</small></div>" + badge("Connected", "green") + "</div>";
            }).join("") + (connections.length > 4 ? "<div class=\"ayu-note\">Additional clients are available in Connectivity.</div>" : "") + "</div>";
        }
        var lastPings = getByPath(datachannel, "last_ping_timestamps", {});
        var sessionIds = Object.keys(lastPings || {}).sort();
        if (!sessionIds.length) {
            return "<div class=\"ayu-empty\">No clients are currently connected.</div>";
        }
        return "<div class=\"ayu-list\">" + sessionIds.slice(0, 4).map(function (sessionId, index) {
            var lastPing = Number(getByPath(lastPings, sessionId, 0)) || 0;
            return "<div class=\"ayu-list-row\"><div class=\"ayu-list-copy\"><strong>" + escapeHtml("Client " + String(index + 1)) + "</strong><small>Last check-in " + escapeHtml(lastPing ? formatTimestamp(lastPing) : "Not reported") + "</small></div>" + badge("Connected", "green") + "</div>";
        }).join("") + (sessionIds.length > 4 ? "<div class=\"ayu-note\">Additional clients are available in Connectivity.</div>" : "") + "</div>";
    }

    function renderLocalPairPanelMarkup() {
        var payload = state.localPair.payload;
        var body;
        if (!payload && state.localPair.loading) {
            body = "<div class=\"ayu-note\">Detecting this computer's network address…</div>";
        } else if (!payload) {
            body = "<div class=\"ayu-note ayu-note-amber\">Local Pair details are unavailable right now" + (state.localPair.error ? ": " + escapeHtml(state.localPair.error) : ".") + "</div>";
        } else {
            var addresses = Array.isArray(payload.addresses) ? payload.addresses : [];
            var port = payload.port || "";
            var targets = addresses.map(function (address, index) {
                return "<div class=\"ayu-localpair-target" + (index === 0 ? " ayu-localpair-primary" : "") + "\"><span class=\"ayu-localpair-host\">" + escapeHtml(address) + "</span><span class=\"ayu-localpair-port\">Port " + escapeHtml(String(port)) + "</span></div>";
            }).join("");
            var steps = "<div class=\"ayu-localpair-steps\"><strong>On your phone</strong><ol><li>Open the AutoYou app on the same Wi-Fi / network.</li><li>Tap <em>Local Pair - same Wi-Fi / network</em> on the Connection tab.</li><li>Enter the address" + (addresses.length > 1 ? " (try the first one)" : "") + " and port shown here, then connect.</li></ol></div>";
            var warning = "";
            if (payload.loopback_only) {
                warning = "<div class=\"ayu-note ayu-note-amber\"><strong>Local Pair from the LAN needs a restart.</strong><p>This server is listening on 127.0.0.1 only, so other devices cannot reach it. Restart AutoYou bound to your network (for example <code>AUTOYOU_BIND_HOST=0.0.0.0</code> or <code>--host 0.0.0.0</code>) to enable Local Pair from phones. That also exposes the Admin UI, Auth Server, AI Agent server, and Websites & Browser with advertised agent websites to devices on the local network.</p></div>";
            } else if (!addresses.length) {
                warning = "<div class=\"ayu-note ayu-note-amber\">No LAN address was detected. Connect this computer to Wi-Fi or Ethernet, then refresh.</div>";
            }
            body = (targets || "") + warning + steps + "<div class=\"ayu-note\">Uses the same server password and security mode as every other pairing. Nothing is broadcast to the network, but binding to 0.0.0.0 widens who can reach the local server surfaces.</div>";
        }
        return panel("Local Pair - connect a phone on this network", "Type these details into the AutoYou app's Local Pair form. No copy/paste pairing, no cloud account, traffic stays on your network.", body);
    }

    function renderBluetoothPairPanelMarkup() {
        var enabled = Boolean(getByPath(state.forms, "connectivity.bluetoothPairing.enabled", false));
        var runtime = getByPath(state.bootstrap, "status.bluetooth_pairing", {});
        var running = Boolean(getByPath(runtime, "running", false));
        var runtimeError = String(getByPath(runtime, "error", "") || "").trim();
        var listenerValue = enabled ? (running ? "Listening" : "Not listening") : "Off";
        var listenerHelp = enabled
            ? (running ? "Nearby AutoYou clients can discover this computer." : (runtimeError || "Nearby discovery is not active."))
            : "Turn on Allow Bluetooth Pair before nearby clients can scan for this server.";
        var statusRows = renderStatusRows([
            { label: "Bluetooth Pair setting", value: enabled ? "On" : "Off", help: enabled ? "Pairing is allowed by config." : "Nearby clients cannot use Bluetooth Pair." },
            { label: "Nearby listener", value: listenerValue, help: listenerHelp },
            { label: "Connection", value: "Bluetooth", help: "Clients scan nearby instead of entering an IP address." },
            { label: "Server network mode", value: "Stays local", help: "AutoYou can keep listening on this computer only." }
        ]);
        var runtimeWarning = enabled && !running
            ? "<div class=\"ayu-note ayu-note-red\"><strong>Bluetooth Pair is on, but not listening.</strong><p>" + escapeHtml(runtimeError || "Install the Bluetooth requirements or run the host Bluetooth bridge.") + "</p></div>"
            : "";
        var guidance = runtimeWarning
            + "<div class=\"ayu-note ayu-note-blue\">Bluetooth Pair lets nearby AutoYou clients discover this computer and exchange pairing details over Bluetooth. In the AutoYou app, the saved computer name is only a label. Leave the optional Bluetooth filter blank unless several AutoYou computers are advertising nearby.</div>"
            + "<div class=\"ayu-note\">Pick the host machine, not a localhost address, when pairing nearby devices. If AutoYou runs in WSL or Docker, use the desktop Bluetooth helper on the Windows or macOS host that owns the radio.</div>"
            + (enabled
                ? "<div class=\"ayu-note ayu-note-amber\">Leave this on only while you expect nearby clients to pair.</div>"
                : "<div class=\"ayu-note ayu-note-gray\">Off by default. Turn it on only when you are ready to pair a nearby device.</div>");
        return "<div class=\"ayu-soft-divider\"></div><h3 style=\"margin:0 0 10px;font-size:15px;\">Bluetooth Pair</h3>"
            + "<div class=\"ayu-hint\" style=\"margin-bottom:10px\">Let nearby clients pair over Bluetooth without exposing the admin website.</div>"
            + statusRows
            + checkbox("connectivity.bluetoothPairing.enabled", "Allow Bluetooth Pair", "The server password and 2FA still apply.")
            + guidance
            + "<div class=\"ayu-inline-actions\">" + button("Save Bluetooth Pair", "save-bluetooth-pairing", "primary", "save") + button("Refresh listener status", "refresh-bootstrap", "secondary", "refresh", "sm") + "</div>";
    }

    function renderLiveViewScreen() {
        var status = getByPath(state.bootstrap, "status", {});
        var opsErrors = state.operations.errors || {};
        var cloud = state.operations.cloudStatus || getByPath(status, "cloud", {});
        var datachannel = state.operations.datachannel || {};
        var taskSummary = state.operations.taskSummary || {};
        var tasks = getByPath(taskSummary, "tasks", {});
        var queue = getByPath(taskSummary, "queue", getByPath(state.operations, "queue.queue", {}));
        var routes = browserWebsiteRoutes();
        var tasksUrl = tasksMissionControlUrl(routes);
        var signalSummary = buildSignalStatusSummary();
        var whatsappSummary = buildWhatsAppStatusSummary();
        var telegramUserSummary = buildTelegramUserStatusSummary();
        var cloudActiveState = getByPath(cloud, "is_active", null);
        var cloudStatusText = getByPath(cloud, "connected", false)
            ? "Connected"
            : (getByPath(cloud, "enrolled", false) ? "Linked" : "Local only");
        var taskSummaryNote = opsErrors.taskSummary
            ? "<div class=\"ayu-note ayu-note-amber\">" + escapeHtml(opsErrors.taskSummary) + "</div>"
            : "";
        var queuePending = Number(getByPath(queue, "pending_count", 0)) || 0;
        var readyCount = Number(getByPath(queue, "ready_count", 0)) || 0;
        var retryingCount = Number(getByPath(queue, "retrying_count", 0)) || 0;
        var routeRows = Array.isArray(routes) && routes.length
            ? "<div class=\"ayu-list\">" + routes.slice(0, 5).map(function (route) {
                var routeUrl = routeLaunchUrl(route);
                return "<div class=\"ayu-list-row\"><div class=\"ayu-list-copy\"><strong>" + escapeHtml(route.title || route.route_id || "Route") + "</strong><small class=\"ayu-code\">" + escapeHtml(routeDisplayUrl(route)) + "</small></div><div class=\"ayu-inline-actions\">" + badge(routeBadgeLabel(route), routeBadgeTone(route)) + (routeUrl ? "<a class=\"ayu-link-btn ayu-btn ayu-btn-ghost ayu-btn-sm\" href=\"" + escapeHtml(routeUrl) + "\" target=\"_blank\" rel=\"noreferrer\">" + icon("external") + "<span>Open</span></a>" : "") + "</div></div>";
            }).join("") + (routes.length > 5 ? "<div class=\"ayu-note\">Additional routes are listed in Connectivity.</div>" : "") + "</div>"
            : "<div class=\"ayu-empty\">No browser routes reported yet.</div>";
        var cloudMarkup = renderStatusRows([
            { label: "Cloud Pair", value: cloudStatusText, help: getByPath(cloud, "status_message", getByPath(cloud, "detail", "")) || "Current cloud routing state." },
            { label: "Receives client requests", value: cloudActiveState === null ? "Unknown" : (cloudActiveState ? "Yes" : "No") },
            { label: "Linked account", value: displayCloudEmail(cloud) },
            { label: "Server ID", value: displayServerId(cloud), mono: true }
        ]) + "<div class=\"ayu-inline-actions\">" + button("Open connectivity", "nav:connectivity", "secondary", "wifi", "sm") + "</div>";
        var sessionsMarkup = renderStatusRows([
            { label: "Connected clients", value: String(getByPath(datachannel, "connected_clients", getByPath(datachannel, "active_sessions", 0))), help: "Paired clients connected right now." }
        ]) + renderLiveSessionSummary(datachannel) + "<div class=\"ayu-inline-actions\">" + button("Open connectivity", "nav:connectivity", "secondary", "wifi", "sm") + "</div>";
        var taskMarkup = taskSummaryNote + renderStatusRows([
            { label: "Scheduled tasks", value: String(getByPath(tasks, "total_count", 0)), help: String(getByPath(tasks, "enabled_count", 0)) + " enabled, " + String(getByPath(tasks, "paused_count", 0)) + " paused" },
            { label: "Due soon", value: String(getByPath(tasks, "due_soon_count", 0)), help: "Scheduled to run within the next hour." },
            { label: "Pinned targets", value: String(getByPath(tasks, "pinned_count", 0)), help: "Tasks locked to one explicit delivery partner." },
            { label: "Next run", value: getByPath(tasks, "next_run_at_s", null) ? formatTimestamp(getByPath(tasks, "next_run_at_s", 0)) : "None scheduled", help: shortText(getByPath(tasks, "next_run_preview", ""), 96) || "No recurring task is currently scheduled." },
            { label: "Last result", value: getByPath(tasks, "last_result_at_s", null) ? formatTimestamp(getByPath(tasks, "last_result_at_s", 0)) : "No result yet", help: shortText(getByPath(tasks, "last_result_preview", ""), 96) || "Task results will appear after a scheduled run completes." },
            { label: "Delivery queue", value: String(queuePending), help: String(readyCount) + " ready, " + String(retryingCount) + " retrying" }
        ]) + "<div class=\"ayu-inline-actions\"><a class=\"ayu-link-btn ayu-btn ayu-btn-primary ayu-btn-sm\" href=\"" + escapeHtml(tasksUrl) + "\" target=\"_blank\" rel=\"noreferrer\">" + icon("external") + "<span>Open Tasks Mission Control</span></a>" + button("Open connectivity", "nav:connectivity", "ghost", "wifi", "sm") + "</div>";
        var messagingMarkup = renderStatusRows([
            { label: "Telegram Bot", value: prettyLabel(getByPath(status, "telegram.status", "Not configured")), help: getByPath(status, "telegram.bot_username", "") || "Bot connection status from the current snapshot." },
            { label: "Telegram User", value: prettyLabel(telegramUserSummary.displayStatus), help: telegramUserSummary.connected ? "Saved Messages connection is ready." : "Saved Messages is not connected." },
            { label: "Signal", value: prettyLabel(signalSummary.displayStatus), help: signalSummary.paired ? "Paired owner bridge is available." : "Pairing is not active." },
            { label: "WhatsApp", value: prettyLabel(whatsappSummary.displayStatus), help: whatsappSummary.paired ? "Paired owner bridge is available." : "Pairing is not active." }
        ]) + "<div class=\"ayu-inline-actions\">" + button("Open messaging", "nav:messaging", "secondary", "msg", "sm") + "</div>";
        var routesMarkup = renderStatusRows([
            { label: "Website routes", value: String(Array.isArray(routes) ? routes.length : 0), help: "Path-routed agent websites plus explicit same-port routes." },
            { label: "Tasks route", value: tasksUrl, mono: true }
        ]) + routeRows + "<div class=\"ayu-inline-actions\">" + button("Open connectivity", "nav:connectivity", "secondary", "wifi", "sm") + "</div>";

        return "<div class=\"ayu-screen\"><div class=\"ayu-hero\"><div class=\"ayu-hero-copy\"><h1>Live View</h1><p>Current cloud routing, connected clients, task delivery health, messaging partner status, and frontend routes in one scan-friendly admin view.</p></div><div class=\"ayu-inline-actions\">" + button("Refresh status", "ops-refresh", "secondary", "refresh") + "<a class=\"ayu-link-btn ayu-btn ayu-btn-ghost\" href=\"" + escapeHtml(tasksUrl) + "\" target=\"_blank\" rel=\"noreferrer\">" + icon("external") + "<span>Open Tasks</span></a></div></div><div class=\"ayu-grid-2\">" + panel("Cloud Pair", "Whether this server is linked and receiving client requests.", cloudMarkup) + panel("Connected clients", "Current client reachability without direct-send controls.", sessionsMarkup) + "</div>" + renderLocalPairPanelMarkup() + "<div class=\"ayu-grid-2\">" + panel("Tasks & deliveries", "Key Tasks Mission Control counts plus outbound delivery backlog.", taskMarkup) + panel("Messaging partners", "Transport health only; direct sends stay in Messaging.", messagingMarkup) + "</div>" + panel("Browser routes", "Frontend discovery and launch paths visible to browser clients.", routesMarkup) + "</div>";
    }

    function renderMcpSetupPanelBody(mcpStatus) {
        var enabled = Boolean(getByPath(mcpStatus, "enabled", false));
        var configured = Boolean(getByPath(mcpStatus, "configured", false));
        var generated = Boolean(getByPath(state.mcpSetup, "generatedToken", ""));
        var adapterUrl = String(getByPath(mcpStatus, "adapter_url", getByPath(state.forms, "messaging.mcp.adapter_url", "http://127.0.0.1:8071")) || "http://127.0.0.1:8071");
        var serverEndpoint = String(getByPath(mcpStatus, "server_endpoint", defaultMcpEndpoint()));
        var developerModeHelpUrl = "https://help.openai.com/en/articles/12584461-developer-mode-and-full-mcp-connectors-in-chatgpt";
        var statusMessage = !enabled
            ? "AutoYou MCP is disabled. Enable it and generate a private adapter token to continue."
            : configured
                ? "The AutoYou server has a server-to-adapter token saved."
                : "Generate a server-to-adapter token before starting the adapter.";
        var statusToneClass = !enabled ? "ayu-note-amber" : (configured ? "ayu-note-green" : "ayu-note-amber");
        var actionLabel = configured ? (generated ? "Generate a replacement token" : "Rotate server token") : "Generate private adapter token";
        var story = "<div class=\"ayu-note ayu-note-blue\"><strong>Your AutoYou is the brain. ChatGPT is the interface.</strong><p>Your configured AutoYou agents and models process each request on your server. The OpenAI Secure MCP Tunnel is a private outbound connection to ChatGPT; it does not host your data, choose a model, or require a public domain.</p></div>";
        var quickSetup = "<div class=\"ayu-note " + statusToneClass + "\"><strong>Private connection setup</strong><ol class=\"ayu-mcp-steps\"><li>Keep your AutoYou Server and configured agents running on this computer. This adapter connects to <code>" + escapeHtml(serverEndpoint) + "</code>.</li><li>Generate the private adapter token and download <code>autoyou-mcp.env</code>. The file contains the server-to-adapter token. It never contains the model provider API key.</li><li>Install <code>plugins/autoyou-mcp</code> in its own Python environment, then start the adapter. It listens only on loopback at <code>" + escapeHtml(adapterUrl.replace(/\/$/, "")) + "/mcp</code>.</li><li>For ChatGPT web, create a Secure MCP Tunnel in your OpenAI workspace. Give the tunnel client a separate restricted runtime key with Tunnels Read and Use permissions. Do not use the AutoYou model API key or an admin key for the always-on tunnel.</li><li>Set <code>CONTROL_PLANE_TUNNEL_ID</code> and <code>CONTROL_PLANE_ORGANIZATION_ID</code> for this PowerShell session. Use the Platform organization ID associated with the tunnel, not the project ID. Then run <code>scripts/start_autoyou_private_tunnel.ps1</code>. If <code>CONTROL_PLANE_API_KEY</code> is not already set, the script securely prompts for the restricted runtime key and clears it when the tunnel exits. In ChatGPT web Developer Mode, create an app using the Tunnel option and select your tunnel. Test <code>get_autoyou_status</code>, then send a request with <code>chat_with_autoyou</code>.</li></ol><p>For a local MCP client that supports stdio, connect directly to AutoYou without a tunnel. ChatGPT web requires the Secure MCP Tunnel path to reach this private server.</p><p>Changes made here are saved to this running AutoYou server. If you edit <code>AutoYou-Server/.env</code> yourself, restart the main server.</p></div>";
        var handoff = generated
            ? "<div class=\"ayu-note ayu-note-green ayu-mcp-handoff\" role=\"status\"><strong>Server token saved.</strong><p>Download the matching adapter file now and keep it private. This one-time copy is cleared when you leave or reload this page.</p><div class=\"ayu-inline-actions\">" + button("Download private adapter config", "mcp-download-config", "primary", "download", "sm") + "</div></div>"
            : (configured
                ? "<div class=\"ayu-note ayu-note-gray\"><strong>Need a matching adapter file?</strong><p>The saved token is never shown again. Rotate it to create a fresh matching file.</p></div>"
                : "");
        var startInstructions = "<div class=\"ayu-note ayu-note-gray ayu-mcp-start\"><strong>Start the adapter</strong><p>Use a dedicated Python virtual environment. From the AutoYou project folder, run <code>python -m pip install -e plugins/autoyou-mcp</code>, then <code>python -m autoyou_mcp --env-file &lt;path-to-autoyou-mcp.env&gt; --transport streamable-http</code>.</p><p>The local MCP endpoint is <code>" + escapeHtml(adapterUrl.replace(/\/$/, "")) + "/mcp</code>. Leave this process running while connected.</p></div>";
        var chatGptAvailability = "<div class=\"ayu-note ayu-note-blue\"><strong>What works through MCP</strong><p>ChatGPT can send text and supported image, audio, video, and file attachments when it supplies them to the tool. AutoYou's configured agents and models answer. Real-time voice calls are not part of MCP. <a href=\"" + developerModeHelpUrl + "\" target=\"_blank\" rel=\"noreferrer\">Check OpenAI's current Developer Mode requirements</a>.</p></div>";
        var advanced = "<details class=\"ayu-mcp-advanced\"><summary>Advanced settings</summary><div class=\"ayu-mcp-advanced-body\">" +
            checkbox("messaging.mcp.enabled", "Enable AutoYou MCP", "The adapter sends chat and supported attachments into this server's native agent and model runtime.") +
            field("MCP adapter URL", input("messaging.mcp.adapter_url", { placeholder: "http://127.0.0.1:8071" }), "Loopback address used by the local adapter process.") +
            field("Server-to-adapter token", input("messaging.mcp.api_token", { type: "password", placeholder: "Leave blank to keep the saved token", autocomplete: "new-password" }), "The guided setup creates a matching token automatically.") +
            checkbox("messaging.mcp.clear_api_token", "Clear the saved MCP token", "A token in AutoYou-Server/.env remains active as a fallback until you remove it and restart the server.") +
            "<div class=\"ayu-inline-actions\">" + button("Save advanced settings", "save-mcp", "secondary", "save") + "</div></div></details>";

        return "<div class=\"ayu-mcp-setup\">" + renderStatusRows([
            { label: "AutoYou server", value: enabled ? "Enabled" : "Disabled" },
            { label: "Server-to-adapter token", value: configured ? "Saved" : "Missing" },
            { label: "MCP adapter", value: "Separate local process", help: "Start and stop it from this computer." },
            { label: "Adapter address", value: adapterUrl, mono: true }
        ]) + story + quickSetup + "<div class=\"ayu-inline-actions\">" + button(actionLabel, "mcp-generate-token", configured ? "secondary" : "primary", "key") + "</div>" + handoff + startInstructions + chatGptAvailability + advanced + "</div>";
    }

    function renderMessagingScreen() {
        var status = getByPath(state.bootstrap, "status", {});
        var signalSummary = buildSignalStatusSummary();
        var signalStatus = signalSummary.status;
        var signalDetail = signalSummary.detail;
        var signalMessages = getByPath(state.operations, "signalMessages.messages", []);
        var whatsappSummary = buildWhatsAppStatusSummary();
        var whatsappDisplayStatus = whatsappSummary.displayStatus;
        var telegramUserSummary = buildTelegramUserStatusSummary();
        var mcpStatus = getByPath(status, "mcp", {});
        var signalDeviceNameLocked = isSignalDeviceNameLocked(signalSummary);
        var whatsappDeviceNameLocked = isWhatsAppDeviceNameLocked(whatsappSummary);
        var deviceNameLockHint = messagingDeviceNameLockHint();
        var datachannel = state.operations.datachannel || {};
        var playback = state.operations.playback || {};
        var opsErrors = state.operations.errors || {};

        var telegramDirectMarkup = field("Chat ID", input("liveOps.telegram_chat_id", { placeholder: "123456789" })) + field("Reply to message ID", input("liveOps.telegram_reply_to_message_id", { placeholder: "Optional" })) + field("Message", textarea("liveOps.telegram_message", { rows: 5 }), "Use an approved Telegram Bot chat ID for this outbound message.") + "<div class=\"ayu-inline-actions\">" + button("Send Telegram Bot message", "send-telegram-direct", "primary", "msg", "sm") + "</div>";
        var telegramUserDirectMarkup = field("Message", textarea("liveOps.telegram_user_message", { rows: 5 }), "This is sent only to your own Saved Messages.") + "<div class=\"ayu-inline-actions\">" + button("Send to Saved Messages", "send-telegram-user-direct", "primary", "msg", "sm") + "</div>";
        var signalDirectMarkup = field("Recipient", input("liveOps.signal_to", { placeholder: "+15555550123" })) + field("Message", textarea("liveOps.signal_message", { rows: 5 }), "Operator diagnostic only. Use the paired owner/self destination unless you have reviewed the platform terms.") + "<div class=\"ayu-inline-actions\">" + button("Send Signal message", "send-signal-direct", "primary", "msg", "sm") + "</div>";
        var whatsappDirectMarkup = field("Recipient", input("liveOps.whatsapp_to", { placeholder: "+15555550123" })) + field("Message", textarea("liveOps.whatsapp_message", { rows: 5 }), "Operator diagnostic only. Use the paired owner/self chat unless you have reviewed the platform terms.") + "<div class=\"ayu-inline-actions\">" + button("Send WhatsApp message", "send-whatsapp-direct", "primary", "msg", "sm") + "</div>";

        var signalDiagnosticsMarkup = (opsErrors.signalStatus ? "<div class=\"ayu-note ayu-note-amber\">" + escapeHtml(opsErrors.signalStatus) + "</div>" : "") + renderStatusRows([
            { label: "Service status", value: prettyLabel(signalSummary.displayStatus) },
            { label: "Enabled", value: getByPath(signalStatus, "enabled", getByPath(state.forms, "messaging.signal.enabled", false)) ? "Yes" : "No" },
            { label: "Container running", value: signalSummary.containerRunning ? "Yes" : "No" },
            { label: "Paired", value: signalSummary.paired ? "Yes" : "No" },
            { label: "Phone number", value: signalSummary.phoneNumber || "Unknown" },
            { label: "Device name", value: getByPath(state.operations, "signalDeviceName.device_name", getByPath(state.operations, "signalDeviceName.error", getByPath(state.forms, "messaging.signal.device_name", "AutoYou-Signal"))) },
            { label: "Integration detail", value: prettyLabel(signalSummary.integrationState || getByPath(signalDetail, "service_state", getByPath(signalDetail, "status", "No detailed status reported"))) }
        ]) + "<div class=\"ayu-soft-divider\"></div><h3 style=\"margin:0 0 10px;font-size:15px;\">Recent Signal messages</h3>" + (opsErrors.signalMessages ? ("<div class=\"ayu-note ayu-note-amber\">" + escapeHtml(opsErrors.signalMessages) + "</div>") : (Array.isArray(signalMessages) && signalMessages.length ? ("<div class=\"ayu-list\">" + signalMessages.map(function (message) {
            var author = getByPath(message, "sender", getByPath(message, "from", getByPath(message, "source", getByPath(message, "number", "Unknown sender"))));
            var body = getByPath(message, "message", getByPath(message, "text", getByPath(message, "content", "(empty message)")));
            var timestamp = getByPath(message, "timestamp", getByPath(message, "received_at", getByPath(message, "created_at", "")));
            return "<div class=\"ayu-list-row\"><div class=\"ayu-list-copy\"><strong>" + escapeHtml(author) + "</strong><p>" + escapeHtml(shortText(body, 180)) + "</p><small>" + escapeHtml(hasValue(timestamp) ? formatTimestamp(timestamp) : "Timestamp unavailable") + "</small></div></div>";
        }).join("") + "</div>") : "<div class=\"ayu-empty\">No recent Signal messages were returned.</div>"));

        var liveControlMarkup = "<div class=\"ayu-note\">" + escapeHtml(getByPath(playback, "enabled", false) ? "Audio playback is enabled for connected clients." : "Audio playback is currently disabled in server settings.") + "</div>" + renderStatusRows([
            { label: "Connected clients", value: String(getByPath(datachannel, "connected_clients", getByPath(datachannel, "active_sessions", 0))) },
            { label: "Music library dirs", value: (Array.isArray(getByPath(playback, "music_library_dirs", [])) && getByPath(playback, "music_library_dirs", []).length) ? getByPath(playback, "music_library_dirs", []).join(", ") : "None reported" }
        ]) + renderDatachannelSessionList(datachannel) + input("liveOps.session_id", { type: "hidden" }) + input("liveOps.owner_key", { type: "hidden" }) + "<div class=\"ayu-note\">" + escapeHtml("Choose a connected client above before sending a message or playing audio. Choose the server audio file under Video & Calls.") + "</div>" + field("Browser message", textarea("liveOps.webrtc_message", { rows: 5 }), "Choose a connected client above to send a message.") + "<div class=\"ayu-inline-actions\">" + button("Send browser message", "send-webrtc-direct", "primary", "msg", "sm") + button("Manage playback permission", "nav:permissions", "secondary", "shield", "sm") + button("Refresh status", "ops-refresh", "ghost", "refresh", "sm") + "</div><div class=\"ayu-soft-divider\"></div>" + "<div class=\"ayu-inline-actions\">" + button("Playback status", "webrtc-playback-status", "secondary", "info", "sm") + button("Play", "webrtc-playback-play", "primary", "play", "sm") + button("Pause", "webrtc-playback-pause", "secondary", "stop", "sm") + button("Resume", "webrtc-playback-resume", "ghost", "refresh", "sm") + button("Stop", "webrtc-playback-stop", "danger", "trash", "sm") + "</div>" + (state.operations.playbackStatus ? renderJsonNote(state.operations.playbackStatus, getByPath(state.operations, "playbackStatus.state", "") === "error" ? "red" : "green") : "");

        var serverNameMarkup = field(
            "Server name",
            input("overview.serverName", { placeholder: "AutoYou-Server" }),
            "This name signs AutoYou responses and appears during pairing. Connected transport device names stay unchanged until you reset and re-pair them."
        ) + "<div class=\"ayu-inline-actions\">" + button("Save server name", "save-messaging-server-name", "primary", "save") + "</div>";
        var signalSettingsMarkup = field("Status", "<div class=\"ayu-note ayu-note-" + escapeHtml(statusTone(signalSummary.displayStatus)) + "\">" + escapeHtml(prettyLabel(signalSummary.displayStatus)) + "</div>") + checkbox("messaging.signal.enabled", "Enable Signal") + field("Port", input("messaging.signal.port", { type: "number" })) + field("Linked device name", input("messaging.signal.device_name", { placeholder: "AutoYou-Signal", extraAttrs: signalDeviceNameLocked ? "disabled aria-disabled=\"true\"" : "" }), signalDeviceNameLocked ? deviceNameLockHint : "Set this before pairing. It stays stable after pairing.") + checkbox("messaging.signal.shutdown_docker_on_exit", "Stop Signal when AutoYou closes") + "<div class=\"ayu-inline-actions\">" + button("Save Signal", "save-signal", "primary", "save") + button("Restart", "signal-restart", "secondary", "refresh", "sm") + button("Show QR", "signal-qr", "ghost", "qr", "sm") + button("Cleanup pairing", "signal-cleanup", "danger", "trash", "sm") + "</div>";
        var whatsappSettingsMarkup = field("Status", "<div class=\"ayu-note ayu-note-" + escapeHtml(statusTone(whatsappDisplayStatus)) + "\">" + escapeHtml(prettyLabel(whatsappDisplayStatus)) + "</div>") + checkbox("messaging.whatsapp.enabled", "Enable WhatsApp") + field("Port", input("messaging.whatsapp.port", { type: "number" })) + field("Linked device name", input("messaging.whatsapp.device_name", { placeholder: "AutoYou-WhatsApp", extraAttrs: whatsappDeviceNameLocked ? "disabled aria-disabled=\"true\"" : "" }), whatsappDeviceNameLocked ? deviceNameLockHint : "Set this before pairing. It stays stable after pairing.") + checkbox("messaging.whatsapp.shutdown_on_exit", "Stop WhatsApp when AutoYou closes") + "<div class=\"ayu-inline-actions\">" + button("Save WhatsApp", "save-whatsapp", "primary", "save") + button("Restart", "whatsapp-restart", "secondary", "refresh", "sm") + button("Show QR", "whatsapp-qr", "ghost", "qr", "sm") + button("Reset session", "whatsapp-reset", "danger", "trash", "sm") + "</div>";
        var mcpSettingsMarkup = renderMcpSetupPanelBody(mcpStatus);
        var telegramUserDiagnosticsMarkup = renderStatusRows([
            { label: "Connection", value: prettyLabel(telegramUserSummary.displayStatus), help: telegramUserSummary.connected ? "Saved Messages is ready." : "Connect your account to begin." },
            { label: "Saved Messages only", value: getByPath(telegramUserSummary.status, "saved_messages_only", true) ? "Yes" : "No" }
        ]) + "<div class=\"ayu-soft-divider\"></div><h3 style=\"margin:0 0 10px;font-size:15px;\">Recent Saved Messages activity</h3>" + renderTelegramUserMessages();

        return (
            "<div class=\"ayu-screen\"><div class=\"ayu-hero\"><div class=\"ayu-hero-copy\"><h1>Messaging</h1><p>Telegram Bot, Telegram User, Signal, WhatsApp, direct message sending, and live client playback stay together with only the settings and status that matter.</p></div><div class=\"ayu-inline-actions\">" + button("Refresh status", "ops-refresh", "secondary", "refresh") + button("Open connectivity", "nav:connectivity", "ghost", "wifi") + "</div></div>" +
            panel("Server name", "The name AutoYou uses when it responds.", serverNameMarkup) +
            "<div class=\"ayu-grid-2\">" +
                panel("Telegram Bot", "A separate bot connection with approved sender controls.", renderTelegramOnboardingBody()) +
                panel("Telegram User", "Your own Telegram account, limited to Saved Messages.", renderTelegramUserManagementBody()) +
            "</div><div class=\"ayu-grid-2\">" +
                panel("Signal", "Optional owner-only connection for Notes to Self.", signalSettingsMarkup) +
                panel("WhatsApp", "Optional owner-only connection for your self chat.", whatsappSettingsMarkup) +
            "</div>" +
                panel("AutoYou MCP", "Connect a private MCP adapter to AutoYou's native chat, models, agents, and supported media handling.", mcpSettingsMarkup) +
                panel("Connected client control", "Select a connected client, send direct messages, and control remote audio playback.", liveControlMarkup) +
            "<div class=\"ayu-grid-2\">" +
                panel("Send through Telegram Bot", "Send through an approved bot chat.", telegramDirectMarkup) +
                panel("Send to Saved Messages", "Send only to the connected Telegram User account.", telegramUserDirectMarkup) +
                panel("Send through Signal", "Use the paired owner destination.", signalDirectMarkup) +
                panel("Send through WhatsApp", "Use the paired self chat.", whatsappDirectMarkup) +
            "</div><div class=\"ayu-grid-2\">" +
                panel("Telegram User activity", "Connection status and recent Saved Messages activity.", telegramUserDiagnosticsMarkup) +
                panel("Signal health & recent messages", "Linked device name, pairing state, service health, and recent messages.", signalDiagnosticsMarkup) +
            "</div></div>"
        );
    }

    function renderPermissionsScreen() {
        var video = getByPath(state.forms, "videoCall", {});
        var paths = getByPath(state.bootstrap, "metadata.recording_paths", {});
        var editable = asBoolean(getByPath(state.bootstrap, "metadata.permissions_editable", false), false);
        var isLoopback = asBoolean(getByPath(state.bootstrap, "metadata.is_loopback_client", true), true);
        var accessNote = editable
            ? ("<div class=\"ayu-note ayu-note-green\"><strong>" + (isLoopback ? "Local admin controls are available." : "Network admin controls are active.") + "</strong> Permission changes save to this computer immediately after you save this section.</div>")
            : "<div class=\"ayu-note ayu-note-amber\"><strong>View only from this connection.</strong> Open the admin page on this computer at 127.0.0.1 and sign in as admin to change its permissions. If running inside WSL, Docker, or hosting over network IP, enable <em>Allow network admin permissions next boot</em> in Overview (or set <code>AUTOYOU_ALLOW_REMOTE_ADMIN_PERMISSIONS=1</code>) and connect over HTTPS.</div>";
        function localOnly(markup) {
            return editable ? markup : "<fieldset disabled style=\"border:0;padding:0;margin:0;min-width:0\">" + markup + "</fieldset>";
        }
        var audioPermissions = checkbox("videoCall.enabled", "Allow video calls", "Lets paired devices start video calls.")
            + checkbox("videoCall.audio_enabled", "Allow audio calls", "Lets paired devices start voice calls and send microphone audio.")
            + checkbox("audioPlayback.enabled", "Allow audio file playback", "Allows server audio files to be played into connected clients.")
            + checkbox("videoCall.audio_microphone", "Share this computer's microphone", "Sends this computer's selected microphone to connected calls.")
            + checkbox("videoCall.audio_speaker_loopback", "Share this computer's sound", "Captures and sends the computer's audio output during calls.")
            + checkbox("videoCall.ai_audio_replies_enabled", "Play spoken AI replies in calls", "Allows AI voice replies to play into active calls.")
            + checkbox("videoCall.disable_autoyou_agents", "Disable AutoYou Agents for call audio", "When enabled, call audio is not transcribed or sent to AI agents.")
            + checkbox("speech.voice_training_capture_enabled", "Record voice calls", "Saves voice-call audio samples and transcripts in the Voice Training folder.")
            + checkbox("videoCall.record_audio_only_calls", "Record audio-only calls", "Saves received call audio as WAV whenever it is not muxed into a recorded MP4. Call transcripts and WUIFT turn handling continue independently.")
            + checkbox("videoCall.background_mode_enabled", "Allow phone background mode", "Keeps paired phones connected in the background; the phone microphone remains off unless a call or safety recording is active.")
            + checkbox("videoCall.silent_recording_enabled", "Allow safety recording", "Allows a paired phone to send microphone audio for local recording without transcription or AI processing.")
            + checkbox("videoCall.location_recording_enabled", "Allow device location recording", "Accepts new location samples from a connected device that also enabled location sharing and granted its OS permission.")
            + checkbox("videoCall.wuift_enabled", "Allow Wait Until I Finish Talking", "Lets callers hold transcription across pauses before sending a turn.")
            + "<div class=\"ayu-inline-actions\">" + button("Save audio permissions", "save-permissions-audio", "primary", "save") + "</div>";
        var videoPermissions = checkbox("videoCall.record_my_video", "Record received video calls", "When the format is Video file, saves the connected phone's camera and available call audio together in an MP4 on this computer.")
            + checkbox("videoCall.remote_desktop.enabled", "Allow screen capture", "Authorizes native capture of this computer's display.")
            + checkbox("videoCall.remote_desktop.send_screen", "Send this computer's screen", "Includes the selected monitor in active video calls.")
            + checkbox("videoCall.outbound_remote_desktop", "Select screen as a call source", "Makes the computer screen an available video source.")
            + checkbox("videoCall.outbound_camera", "Allow webcam sharing", "Makes the selected webcam an available video source.")
            + checkbox("videoCall.outbound_api", "Allow API video input", "Accepts JPEG frames pushed through the server API.")
            + checkbox("videoCall.outbound_video_file", "Allow video file playback", "Allows a local video file to be streamed into a call. Its soundtrack stops when the caller starts speaking so AutoYou can listen; the looping video keeps playing. Press Play again to restart its sound.")
            + checkbox("videoCall.remote_desktop.control_enabled", "Allow Remote Desktop input", "Allows authenticated, active, full-screen clients to send supported mouse, touch, keyboard, and controller input.")
            + checkbox("videoCall.remote_desktop.game_enabled", "Allow game mode", "Streams the screen and sound with game controls. Remote Desktop input must also be enabled.", getByPath(video, "remote_desktop.control_enabled", false) ? "" : "disabled")
            + "<div class=\"ayu-inline-actions\">" + button("Save video permissions", "save-permissions-video", "primary", "save") + "</div>";
        var dataPermissions = checkbox("aiAgent.record_messages_in_database", "Save chat and event memory", "Stores AutoYou AI chat and event memory in the local database shown below.")
            + checkbox("aiAgent.lan_access_enabled", "Allow AutoYou AI access from the home network", "Exposes the AI Agent on a separate HTTPS port protected by a one-time code.")
            + "<div class=\"ayu-inline-actions\">" + button("Save data and network permissions", "save-permissions-data", "primary", "save") + "</div>";
        var webAccess = field("Paired browser role", select("page.remote_access_role", remoteAccessRoleOptions()), remoteAccessRoleHelp(getByPath(state.forms, "page.remote_access_role", "viewer")))
            + checkbox("page.admin_frontend_enabled", "Share this computer's Admin website with paired browsers", "When enabled, paired browsers can open the Admin website. Its password and authenticator checks still apply.")
            + "<div class=\"ayu-inline-actions\">" + button("Save browser permissions", "save-permissions-web", "primary", "save") + "</div>";
        var storagePaths = renderStatusRows([
            { label: "Safety recording folder", value: getByPath(paths, "safety_recording.resolved_dir", "Unavailable"), mono: true },
            { label: "Video recording folder", value: getByPath(paths, "video_recording.resolved_dir", "Unavailable"), mono: true },
            { label: "Voice training folder", value: getByPath(paths, "voice_training.active_dir", getByPath(paths, "voice_training.default_dir", "Unavailable")), mono: true },
            { label: "Location history database", value: getByPath(paths, "location_recording.database_path", "Unavailable"), mono: true },
            { label: "Saved chat transcripts and event memory database", value: getByPath(paths, "chat_memory.database_path", "Unavailable"), mono: true }
        ]);
        var statusMarkup = renderStatusRows([
            { label: "Video calls", value: yesNo(getByPath(video, "enabled", false)) },
            { label: "Audio calls", value: yesNo(getByPath(video, "audio_enabled", false)) },
            { label: "Audio file playback", value: yesNo(getByPath(state.forms, "audioPlayback.enabled", true)) },
            { label: "Voice-call recording", value: yesNo(getByPath(state.forms, "speech.voice_training_capture_enabled", false)) },
            { label: "Safety recording", value: yesNo(getByPath(video, "silent_recording_enabled", false)) },
            { label: "Location recording", value: yesNo(getByPath(video, "location_recording_enabled", false)) },
            { label: "Phone background mode", value: yesNo(getByPath(video, "background_mode_enabled", false)) },
            { label: "Wait Until I Finish Talking", value: yesNo(getByPath(video, "wuift_enabled", true)) },
            { label: "Received video recording", value: yesNo(getByPath(video, "record_my_video", false)) },
            { label: "Computer microphone", value: yesNo(getByPath(video, "audio_microphone", false)) },
            { label: "Computer audio passthrough", value: yesNo(getByPath(video, "audio_speaker_loopback", false)) },
            { label: "Screen capture allowed", value: yesNo(getByPath(video, "remote_desktop.enabled", false)) },
            { label: "Screen sending allowed", value: yesNo(getByPath(video, "remote_desktop.send_screen", false)) },
            { label: "Screen call source selected", value: yesNo(getByPath(video, "outbound_remote_desktop", false)) },
            { label: "Webcam source", value: yesNo(getByPath(video, "outbound_camera", false)) },
            { label: "API video input", value: yesNo(getByPath(video, "outbound_api", false)) },
            { label: "Video file playback", value: yesNo(getByPath(video, "outbound_video_file", false)) },
            { label: "Spoken AI replies", value: yesNo(getByPath(video, "ai_audio_replies_enabled", true)) },
            { label: "AutoYou Agents in call audio", value: getByPath(video, "disable_autoyou_agents", false) ? "Disabled" : "Allowed" },
            { label: "Remote Desktop control", value: yesNo(getByPath(video, "remote_desktop.control_enabled", false)) },
            { label: "Game mode", value: yesNo(getByPath(video, "remote_desktop.game_enabled", false)) },
            { label: "Chat and event memory", value: yesNo(getByPath(state.forms, "aiAgent.record_messages_in_database", true)) },
            { label: "Home network AI access", value: yesNo(getByPath(state.forms, "aiAgent.lan_access_enabled", false)) },
            { label: "Paired browser role", value: prettyLabel(getByPath(state.forms, "page.remote_access_role", "viewer")) },
            { label: "Admin website shared", value: yesNo(getByPath(state.forms, "page.admin_frontend_enabled", false)) }
        ]);
        return "<div class=\"ayu-screen\"><div class=\"ayu-hero\"><div class=\"ayu-hero-copy\"><h1>Permissions</h1><p>Review what this computer allows AutoYou and paired devices to capture, record, save, and access.</p></div><div class=\"ayu-inline-actions\">" + button("Refresh status", "ops-refresh", "secondary", "refresh") + button("Open Video & Calls", "nav:video", "ghost", "video") + "</div></div>" + accessNote + "<div class=\"ayu-grid-2\">" + panel("Calls and audio", "Microphone capture, computer audio, transcription, voice recording, safety recording, and location.", localOnly(audioPermissions)) + panel("Video and computer control", "Screen, camera, API, playback, video recording, Remote Desktop input, and game mode.", localOnly(videoPermissions)) + "</div><div class=\"ayu-grid-2\">" + panel("Chat, memory, and network access", "Chat retention and AutoYou AI reachability.", localOnly(dataPermissions)) + panel("Paired browser access", "Set the remote web role and decide whether paired devices may open this computer's Admin website.", localOnly(webAccess)) + "</div><div class=\"ayu-grid-2\">" + panel("Where recordings and chat history are saved", "Resolved locations on this computer, including saved chat transcripts and the event memory database.", storagePaths) + panel("Current permission status", "A quick readout of the settings above.", statusMarkup) + "</div></div>";
    }

    function renderVideoScreen() {
        var datachannel = state.operations.datachannel || {};
        var opsErrors = state.operations.errors || {};
        var capabilityPayload = state.operations.webrtcCapabilities || {};
        var capabilities = getByPath(capabilityPayload, "capabilities", {});
        var videoCaps = getByPath(capabilities, "video", {});
        var audioCaps = getByPath(capabilities, "audio", {});
        var remoteCaps = getByPath(capabilities, "remote_desktop", {});
        var outboundCaps = getByPath(capabilities, "outbound_video", {});
        var videoFileCaps = getByPath(outboundCaps, "available_sources.video_file", {});
        var recordingPaths = getByPath(state.bootstrap, "metadata.recording_paths", {});
        var defaultSafetyRecordingDir = firstNonBlank([getByPath(recordingPaths, "safety_recording.default_dir", "")], "AutoYou default safety-recordings folder");
        var defaultVideoRecordingDir = firstNonBlank([getByPath(recordingPaths, "video_recording.default_dir", "")], "AutoYou default video-recordings folder");
        var resolvedSafetyRecordingDir = firstNonBlank([
            getByPath(recordingPaths, "safety_recording.resolved_dir", ""),
            getByPath(audioCaps, "silent_recording.recording_dir", "")
        ], defaultSafetyRecordingDir);
        var resolvedVideoRecordingDir = firstNonBlank([
            getByPath(recordingPaths, "video_recording.resolved_dir", ""),
            getByPath(videoCaps, "recording_dir", "")
        ], defaultVideoRecordingDir);
        var voiceTrainingDir = firstNonBlank([
            getByPath(recordingPaths, "voice_training.active_dir", ""),
            getByPath(recordingPaths, "voice_training.default_dir", "")
        ], "Unknown");
        var capabilityError = opsErrors.webrtcCapabilities
            ? "<div class=\"ayu-note ayu-note-amber\">" + escapeHtml(opsErrors.webrtcCapabilities) + "</div>"
            : "";
        var configuredAudioSource = String(getByPath(state.forms, "videoCall.input_audio_source", "default") || "default");
        var audioDevicesInfo = getByPath(state.operations, "audioDevices", {});
        var audioDevicesPayload = getByPath(audioDevicesInfo, "devices", []);
        var audioDeviceOptions = [
            { value: "default", label: "System default microphone (recommended)" }
        ];
        if (Array.isArray(audioDevicesPayload)) {
            audioDevicesPayload.forEach(function (dev) {
                var deviceValue = String(dev.selector || dev.id);
                if (configuredAudioSource === String(dev.id)) {
                    deviceValue = configuredAudioSource;
                }
                var deviceLabel = dev.name || ("Microphone " + dev.id);
                if (dev.available === false) {
                    deviceLabel += " - unavailable";
                }
                audioDeviceOptions.push({
                    value: deviceValue,
                    label: deviceLabel
                });
            });
        }
        if (configuredAudioSource !== "default" && !audioDeviceOptions.some(function (option) { return String(option.value) === configuredAudioSource; })) {
            audioDeviceOptions.push({ value: configuredAudioSource, label: configuredAudioSource + " - previously selected, unavailable" });
        }
        var videoFilesPayload = getByPath(state.operations, "videoFiles.files", []);
        var videoFileOptions = [
            { value: "", label: "-- Type path manually or choose a file --" }
        ];
        if (Array.isArray(videoFilesPayload)) {
            videoFilesPayload.forEach(function (f) {
                videoFileOptions.push({
                    value: f.path,
                    label: f.name + " (" + Math.round(f.size_bytes / 1024 / 1024 * 10) / 10 + " MB)"
                });
            });
        }
        var cameraDevicesPayload = getByPath(state.operations, "cameraDevices.devices", []);
        var cameraDeviceOptions = [];
        if (Array.isArray(cameraDevicesPayload) && cameraDevicesPayload.length > 0) {
            cameraDevicesPayload.forEach(function (dev) {
                var cameraLabel = dev.name || ("Camera - capture index " + dev.id);
                if (dev.available === false || dev.probe_status === "not_available" || dev.probe_status === "runtime_unavailable") {
                    cameraLabel += " - unavailable";
                } else if (dev.probe_status === "not_probed") {
                    cameraLabel += " - not checked";
                }
                cameraDeviceOptions.push({
                    value: String(dev.id),
                    label: cameraLabel
                });
            });
        }
        var configuredCameraId = String(getByPath(state.forms, "videoCall.camera_device_id", 0));
        var configuredCameraDevice = cameraDevicesPayload.find(function (dev) {
            return String(dev && dev.id) === configuredCameraId;
        });
        var cameraProbePerformed = Boolean(getByPath(state.operations, "cameraDevices.probe_enabled", false));
        var anyWebcamDetected = cameraDevicesPayload.some(function (dev) { return dev && dev.available === true; });
        var noWebcamDetected = cameraProbePerformed && !anyWebcamDetected;
        var cameraReadyLabel = configuredCameraDevice && configuredCameraDevice.available === true
            ? "Yes"
            : configuredCameraDevice && configuredCameraDevice.available === false
                ? "No webcam found"
                : "Not checked - select Refresh devices";
        if (!cameraDeviceOptions.some(function (option) { return String(option.value) === configuredCameraId; })) {
            cameraDeviceOptions.push({ value: configuredCameraId, label: "Configured camera - capture index " + configuredCameraId + " - unavailable" });
        }
        var monitorDevicesPayload = getByPath(state.operations, "monitors.devices", []);
        var configuredMonitorId = String(getByPath(state.forms, "videoCall.remote_desktop.monitor_id", 0));
        var monitorDeviceOptions = [];
        if (Array.isArray(monitorDevicesPayload)) {
            monitorDevicesPayload.forEach(function (monitor) {
                var monitorLabel = monitor.name || ("Display " + monitor.id);
                if (monitor.available === false && monitorLabel.toLowerCase().indexOf("unavailable") === -1) {
                    monitorLabel += " - unavailable";
                }
                monitorDeviceOptions.push({ value: String(monitor.id), label: monitorLabel });
            });
        }
        if (!monitorDeviceOptions.some(function (option) { return String(option.value) === configuredMonitorId; })) {
            monitorDeviceOptions.push({ value: configuredMonitorId, label: "Configured display " + configuredMonitorId + " - unavailable" });
        }
        var audioMixer = getByPath(audioCaps, "mixer", {});
        var mixerSources = getByPath(audioMixer, "sources", []);
        if (!Array.isArray(mixerSources)) {
            mixerSources = [];
        }
        var loopbackAvailable = getByPath(audioCaps, "loopback_available", getByPath(audioDevicesInfo, "loopback_available", null));
        var loopbackReason = firstNonBlank([
            getByPath(audioCaps, "loopback_reason", ""),
            getByPath(audioDevicesInfo, "loopback_reason", "")
        ], "No compatible computer-sound loopback input was found.");
        var optionLabel = function (options, value, fallback) {
            var normalized = String(value == null ? "" : value);
            var option = (options || []).find(function (item) { return String(item.value) === normalized; });
            return option ? option.label : (fallback || normalized || "Default");
        };
        var selectedAudioSourceLabel = function () {
            return optionLabel(audioDeviceOptions, getByPath(audioCaps, "input_audio_source", getByPath(state.forms, "videoCall.input_audio_source", "default")), "Default microphone");
        };
        var selectedAudioSources = getByPath(audioCaps, "audio_sources", []);
        if (!Array.isArray(selectedAudioSources)) {
            selectedAudioSources = [];
        }
        var audioSourceLabel = function (value) {
            var id = String(value || "").toLowerCase();
            if (id === "microphone" || id.indexOf("microphone:") === 0) {
                return "Computer microphone";
            }
            if (id === "speaker_loopback" || id === "desktop_loopback") {
                return "Computer sound";
            }
            if (id === "ai_replies") {
                return "Spoken AI replies";
            }
            return prettyLabel(value || "audio");
        };
        var audioOutputModeLabel = function (value) {
            var id = String(value || "").toLowerCase();
            if (!id || id === "none") {
                return "None";
            }
            if (id === "mixed") {
                return "Mixed";
            }
            return audioSourceLabel(id);
        };
        var outboundSourceLabel = function (value) {
            var labels = {
                remote_desktop: "Remote Desktop",
                api: "Realtime API frames",
                realtime_api: "Realtime API frames",
                video_file: "Video file",
                camera: "Webcam / Camera",
                stitched: "Screen + webcam",
                none: "Off"
            };
            return labels[String(value || "")] || prettyLabel(value || "remote_desktop");
        };
        var mixerSourceLabel = function (source) {
            var label = String(getByPath(source, "label", "") || "").trim();
            if (label) {
                return label;
            }
            var id = String(getByPath(source, "id", "") || "").toLowerCase();
            var kind = String(getByPath(source, "kind", "") || "").toLowerCase();
            if (kind === "capture" || id.indexOf("capture") !== -1) {
                return "Computer audio";
            }
            if (kind === "playback" || id.indexOf("playback") !== -1 || id.indexOf("tts") !== -1) {
                return "Speech playback";
            }
            return "Audio source";
        };
        var mixerSourceDetail = function (source) {
            var id = String(getByPath(source, "id", "") || "").toLowerCase();
            var kind = String(getByPath(source, "kind", "") || "").toLowerCase();
            if (id === "microphone" || id.indexOf("microphone:") === 0) {
                return selectedAudioSourceLabel();
            }
            if (id === "speaker_loopback" || id === "desktop_loopback") {
                return "Default speaker output";
            }
            if (id === "ai_replies" || kind === "assistant") {
                return "Replies from AutoYou";
            }
            if (kind === "capture" || id.indexOf("capture") !== -1) {
                return "Selected computer audio";
            }
            return prettyLabel(kind || "audio");
        };
        var renderCallSourceList = function () {
            var rows = [];
            if (mixerSources.length) {
                mixerSources.forEach(function (source) {
                    var sourceState = String(getByPath(source, "state", getByPath(source, "active", false) ? "active" : (getByPath(source, "enabled", true) === false ? "off" : "ready")));
                    var sourceId = String(getByPath(source, "id", "") || "").toLowerCase();
                    var sourceReason = firstNonBlank([
                        getByPath(source, "reason", ""),
                        getByPath(source, "capture.last_error", "")
                    ], "");
                    if (sourceId === "speaker_loopback" && sourceState !== "active" && loopbackAvailable === false) {
                        sourceState = "unavailable";
                        sourceReason = sourceReason || loopbackReason;
                    }
                    var sourceLabel = mixerSourceLabel(source);
                    if (sourceId === "speaker_loopback" && sourceState === "unavailable") {
                        sourceLabel = "Computer sound unavailable.";
                    }
                    var tone = sourceState === "active" ? "green" : (sourceState === "retrying" ? "amber" : (sourceState === "unavailable" ? "red" : (sourceState === "ready" ? "blue" : "gray")));
                    rows.push({
                        label: sourceLabel,
                        detail: sourceReason || mixerSourceDetail(source),
                        badge: badge(prettyLabel(sourceState), tone)
                    });
                });
            } else {
                var speechReady = Boolean(getByPath(audioCaps, "enabled", false) && getByPath(audioCaps, "tts_available", false));
                var captureReady = Boolean(getByPath(audioCaps, "capture_audio", false));
                rows.push({ label: "Speech playback", detail: "Replies and played audio", badge: badge(speechReady ? "Active" : "Ready", speechReady ? "green" : "blue") });
                rows.push({ label: "Computer audio", detail: selectedAudioSources.length ? selectedAudioSources.map(audioSourceLabel).join(" + ") : "None", badge: badge(captureReady ? "Active" : "Off", captureReady ? "green" : "gray") });
            }
            var outboundSource = getByPath(outboundCaps, "source", getByPath(state.forms, "videoCall.outbound_source", "remote_desktop"));
            var outboundActive = Boolean(getByPath(outboundCaps, "enabled", false));
            var outboundDegraded = Boolean(getByPath(outboundCaps, "degraded", false));
            var outboundRuntimeStatus = String(getByPath(outboundCaps, "runtime_status", outboundActive ? "ready" : "off"));
            var activeOutboundSources = getByPath(outboundCaps, "active_sources", []);
            if (!Array.isArray(activeOutboundSources)) {
                activeOutboundSources = [];
            }
            rows.push({
                label: "Computer video",
                detail: outboundActive
                    ? activeOutboundSources.map(outboundSourceLabel).join(" + ") || outboundSourceLabel(outboundSource)
                    : "No selected source is available",
                badge: badge(
                    outboundDegraded ? "Partial" : prettyLabel(outboundRuntimeStatus),
                    outboundDegraded || outboundRuntimeStatus === "starting" ? "amber" : (outboundRuntimeStatus === "streaming" ? "green" : (outboundActive ? "blue" : "gray"))
                )
            });
            var selfCameraReady = Boolean(getByPath(videoCaps, "receive_enabled", false));
            rows.push({
                label: "Self camera",
                detail: getByPath(videoCaps, "record_my_video", false) ? "Received and recorded" : "Received from phone",
                badge: badge(selfCameraReady ? "Ready" : "Off", selfCameraReady ? "blue" : "gray")
            });
            return "<div class=\"ayu-list\">" + rows.map(function (row) {
                return "<div class=\"ayu-list-row\"><div class=\"ayu-list-copy\"><strong>" + escapeHtml(row.label) + "</strong><small>" + escapeHtml(row.detail) + "</small></div><div class=\"ayu-inline-actions\">" + row.badge + "</div></div>";
            }).join("") + "</div><div class=\"ayu-soft-divider\"></div>" + renderStatusRows([
                { label: "Audio sent to phones", value: audioOutputModeLabel(getByPath(audioCaps, "output_mode", "playback")) },
                { label: "Mixed audio", value: yesNo(getByPath(audioMixer, "enabled", false)) },
                { label: "Connected clients", value: String(getByPath(datachannel, "connected_clients", getByPath(datachannel, "active_sessions", 0))) }
            ]) + "<div class=\"ayu-inline-actions\">" + button("Refresh status", "ops-refresh", "secondary", "refresh", "sm") + "</div>";
        };
        var callSourcesMarkup = renderCallSourceList();
        var selectedOutboundSources = getByPath(outboundCaps, "sources", getByPath(state.forms, "videoCall.outbound_sources", []));
        if (!Array.isArray(selectedOutboundSources)) {
            selectedOutboundSources = [];
        }
        var activeOutboundSources = getByPath(outboundCaps, "active_sources", []);
        if (!Array.isArray(activeOutboundSources)) {
            activeOutboundSources = [];
        }
        var unavailableOutboundSources = getByPath(outboundCaps, "unavailable_sources", []);
        if (!Array.isArray(unavailableOutboundSources)) {
            unavailableOutboundSources = [];
        }
        var selectedOutboundSource = getByPath(outboundCaps, "configured_source", getByPath(outboundCaps, "source", getByPath(state.forms, "videoCall.outbound_source", "remote_desktop")));
        var wantsRemoteDesktop = asBoolean(
            getByPath(state.forms, "videoCall.outbound_remote_desktop", selectedOutboundSources.indexOf("remote_desktop") !== -1 || String(selectedOutboundSource || "") === "remote_desktop"),
            false
        );
        var remoteDesktopReady = Boolean(getByPath(remoteCaps, "enabled", false));
        var remoteAgentInstalled = Boolean(getByPath(remoteCaps, "agent_installed", false));
        var remoteTrackAvailable = Boolean(getByPath(remoteCaps, "track_available", false));
        var remoteSettingEnabled = asBoolean(getByPath(state.forms, "videoCall.remote_desktop.enabled", true), true);
        var remoteScreenSending = asBoolean(getByPath(state.forms, "videoCall.remote_desktop.send_screen", true), true);
        var remoteSettingsActive = wantsRemoteDesktop && remoteSettingEnabled && remoteScreenSending;
        var remoteControlConfigured = asBoolean(getByPath(state.forms, "videoCall.remote_desktop.control_enabled", false), false);
        var remoteControlAvailable = Boolean(getByPath(remoteCaps, "control_available", false));
        var remoteControlEnabled = Boolean(getByPath(remoteCaps, "control_enabled", false));
        var remoteQuality = String(getByPath(remoteCaps, "quality", getByPath(state.forms, "videoCall.remote_desktop.quality", "balanced")) || "balanced");
        var remoteAdvice = !remoteSettingEnabled
            ? "Turn on Remote Desktop for video calls, save, then refresh."
            : (!remoteScreenSending
                ? "Turn on screen sharing, save, then refresh."
                : (!remoteTrackAvailable
                    ? "The screen stream is not available in this runtime yet."
                    : "The selected screen source is available."));
        var remoteDesktopNudge = wantsRemoteDesktop
            ? "<div class=\"ayu-note ayu-note-" + (remoteDesktopReady ? "green" : "amber") + "\"><strong>" + escapeHtml(remoteDesktopReady ? "Native desktop video is ready." : "Desktop video is not ready.") + "</strong><p style=\"margin:6px 0 0;\">" + escapeHtml(remoteDesktopReady ? "Phones can show this computer's screen directly in the video call; the optional Remote Desktop app does not need to be open." : "Phones will show the camera placeholder until the screen source is ready. " + remoteAdvice) + "</p></div>"
            : "<div class=\"ayu-note\"><strong>Remote Desktop is not the selected video source.</strong><p style=\"margin:6px 0 0;\">Choose Remote Desktop above if phones should see this computer's screen.</p></div>";
        var micShareSelected = asBoolean(getByPath(state.forms, "videoCall.audio_microphone", false), false);
        var cameraShareSelected = asBoolean(getByPath(state.forms, "videoCall.outbound_camera", false), false);
        var apiShareSelected = asBoolean(getByPath(state.forms, "videoCall.outbound_api", false), false);
        var videoFileSelected = asBoolean(getByPath(state.forms, "videoCall.outbound_video_file", false), false);
        var videoFilePath = String(getByPath(state.forms, "videoCall.video_file.path", "") || "").trim();
        var videoFileLoop = asBoolean(getByPath(state.forms, "videoCall.video_file.loop", true), true);
        var apiSourceId = String(getByPath(state.forms, "videoCall.api_video_source_id", "default") || "default").trim() || "default";
        var videoFileExists = Boolean(getByPath(videoFileCaps, "file_exists", false));
        var videoFilePlaying = Boolean(getByPath(videoFileCaps, "status.playing", false));
        var videoFilePaused = Boolean(getByPath(videoFileCaps, "status.paused", false));
        var videoFileEnded = Boolean(getByPath(videoFileCaps, "status.ended", false));
        var videoUpload = state.videoFileUpload || {};
        var videoUploadStatus = String(videoUpload.status || "idle");
        var videoUploadBusy = videoUploadStatus === "uploading";
        var videoUploadFailed = videoUploadStatus === "failed";
        var videoUploadDone = videoUploadStatus === "uploaded";
        var videoUploadProgress = Number(videoUpload.progress);
        var videoUploadHasProgress = Number.isFinite(videoUploadProgress);
        var videoUploadPath = String(videoUpload.serverPath || "").trim();
        var videoUploadFileName = String(videoUpload.fileName || "").trim();
        var videoUploadVisible = Boolean(videoUploadBusy || videoUploadFailed || videoUploadDone || videoUploadFileName || videoUploadPath);
        var videoUploadStatusLabel = videoUploadBusy
            ? (videoUploadHasProgress ? ("Uploading " + Math.round(videoUploadProgress) + "%") : "Uploading")
            : (videoUploadDone ? "Uploaded" : (videoUploadFailed ? "Failed" : "Ready"));
        var videoUploadTone = videoUploadFailed ? "red" : (videoUploadDone ? "green" : (videoUploadBusy ? "blue" : "gray"));
        var videoUploadDetail = videoUploadFileName
            ? videoUploadFileName + (Number(videoUpload.fileSize || 0) > 0 ? " (" + formatByteSize(videoUpload.fileSize) + ")" : "")
            : "No browser file selected";
        var videoUploadProgressMarkup = (videoUploadBusy || videoUploadDone || videoUploadFailed)
            ? renderProgressTrack(videoUploadDone ? 100 : (videoUploadHasProgress ? videoUploadProgress : 0), videoUploadFailed ? "failed" : "")
            : "";
        var videoUploadStatusMarkup = videoUploadVisible
            ? "<div class=\"ayu-video-upload-status ayu-video-upload-" + escapeHtml(videoUploadTone) + "\"><div class=\"ayu-video-upload-head\"><div><strong>Browser upload</strong><small>Uploads from the current browser into this AutoYou server.</small></div>" + badge(videoUploadStatusLabel, videoUploadTone) + "</div>" + videoUploadProgressMarkup + renderStatusRows([
                { label: "Selected browser file", value: videoUploadDetail },
                { label: "Saved server path", value: videoUploadPath || "No server path set", mono: true },
                { label: "Upload result", value: videoUploadFailed ? (videoUpload.error || "Upload failed") : (videoUploadDone ? "Config updated with uploaded path" : (videoUploadBusy ? "Copying file to server" : "Waiting for a file")) }
            ]) + "</div>"
            : "";
        var recordMyVideoSelected = asBoolean(getByPath(state.forms, "videoCall.record_my_video", false), false);
        var imagesModeSelected = String(getByPath(state.forms, "videoCall.recording_mode", "video")) === "images";
        var safetyRecordingSelected = asBoolean(getByPath(state.forms, "videoCall.silent_recording_enabled", false), false);
        var callPolicyNote = "<div class=\"ayu-note ayu-note-blue\"><strong>Permission controls moved.</strong> Call availability, audio capture, recording, location, screen sharing, and remote control are now managed in Permissions. This page keeps the computer's device, source, quality, and storage settings.</div>";
        var videoSettingsMarkup = callPolicyNote
            + field("Microphone device", select("videoCall.input_audio_source", audioDeviceOptions), "This device is used when Permissions allows the computer microphone to be shared.")
            + (loopbackAvailable === false ? "<div class=\"ayu-note ayu-note-red\"><strong>Computer sound unavailable.</strong><p style=\"margin:6px 0 0;\">" + escapeHtml(loopbackReason) + " Install a supported loopback audio device, then select Refresh devices.</p></div>" : "")
            + field("Recording format", select("videoCall.recording_mode", [
                { value: "video", label: "Video file" },
                { value: "images", label: "Images every X seconds" }
            ]), "Video recording can be enabled in Permissions. Images mode saves JPEG snapshots on the interval below.")
            + field("Image interval seconds", input("videoCall.image_interval_seconds", { type: "number", placeholder: "5", extraAttrs: "min=\"1\" max=\"3600\" step=\"1\"" }), "Used only when recording format is Images.")
            + field("Video recording location", input("videoCall.recording_dir", { placeholder: defaultVideoRecordingDir }), "Leave blank to use: " + defaultVideoRecordingDir)
            + field("Safety recording location", input("videoCall.silent_recording_dir", { placeholder: defaultSafetyRecordingDir }), "Leave blank to use: " + defaultSafetyRecordingDir)
            + field("Safety recording batch seconds", input("videoCall.silent_recording_batch_seconds", { type: "number", placeholder: "3599", extraAttrs: "min=\"1\" max=\"3599\" step=\"1\"" }), "Each WAV file rotates before one hour to avoid large in-memory batches.")
            + "<div class=\"ayu-soft-divider\"></div>"
            + renderStatusRows([
                { label: "Safety folder", value: resolvedSafetyRecordingDir, mono: true },
                { label: "Video folder", value: resolvedVideoRecordingDir, mono: true },
                { label: "Voice training folder", value: voiceTrainingDir, mono: true }
            ])
            + "<div class=\"ayu-inline-actions\">" + button("Save device and storage settings", "save-video-call", "primary", "save") + button("Refresh status", "ops-refresh", "secondary", "refresh", "sm") + "</div>";
        var outboundSettingsMarkup = field("Realtime video source id", input("videoCall.api_video_source_id", { placeholder: "default" }), "Used by API-pushed JPEG frames when Realtime video input is enabled in Permissions.")
            + field("Server video file", select("videoCall.video_file.path", videoFileOptions), "Choose a file already uploaded to this AutoYou server, or type its path below.")
            + field("Server path", input("videoCall.video_file.path", { placeholder: "Absolute path on this computer" }), "Use a file path that already exists on this computer.")
            + checkbox("videoCall.video_file.loop", "Loop video", "Restart the video automatically when it reaches the end.")
            + "<input class=\"ayu-file-input\" type=\"file\" accept=\"video/*\" data-role=\"video-file-input\" tabindex=\"-1\" aria-hidden=\"true\">"
            + "<div class=\"ayu-inline-actions\">" + button("Upload from browser", "video-file-select", "secondary", "upload", "sm") + button("Play", "video-file-play", "primary", "play", "sm") + button("Pause", "video-file-pause", "secondary", "pause", "sm") + button("Restart", "video-file-restart", "ghost", "refresh", "sm") + "</div>"
            + videoUploadStatusMarkup
            + (videoFileSelected && !videoFileExists
                ? "<div class=\"ayu-note ayu-note-amber\"><strong>Video file is not ready.</strong><p style=\"margin:6px 0 0;\">Upload from this browser, choose an uploaded server file, or enter a path that exists on the server, then save.</p></div>"
                : "")
            + renderStatusRows([
                { label: "File exists", value: yesNo(videoFileExists) },
                { label: "Loop", value: yesNo(videoFileLoop) },
                { label: "Playback", value: videoFilePlaying ? "Playing" : (videoFilePaused ? "Paused" : (videoFileEnded ? "Ended" : "Not started")) },
                { label: "API frames", value: apiShareSelected ? apiSourceId : "Off" }
            ])
            + "<div class=\"ayu-soft-divider\"></div><h3 style=\"margin:0 0 10px;font-size:15px;\">Audio file playback</h3>"
            + field("Server audio file path", input("liveOps.playback_file_path", { placeholder: "/absolute/path/to/file.mp3" }), "Use a server-local audio file path. Allow audio file playback in Permissions, then choose a connected client and start playback from Messaging.")
            + (noWebcamDetected
                ? "<div class=\"ayu-note ayu-note-amber\"><strong>No webcam found.</strong><p style=\"margin:6px 0 0;\">Calls show the screen only  -  the webcam pane stays hidden until a camera is connected. Connect a webcam and select Refresh devices, or turn this off.</p></div>"
                : "")
            + field("Webcam / Camera device", select("videoCall.camera_device_id", cameraDeviceOptions, noWebcamDetected ? "disabled" : ""), noWebcamDetected ? "Connect a webcam, then select Refresh devices to choose it." : "The selected camera is used when Permissions allows webcam sharing.")
            + renderStatusRows([
                { label: "Selected sources", value: selectedOutboundSources.length ? selectedOutboundSources.map(outboundSourceLabel).join(", ") : "None" },
                { label: "Ready to send", value: activeOutboundSources.length ? activeOutboundSources.map(outboundSourceLabel).join(", ") : "None" },
                { label: "Unavailable", value: unavailableOutboundSources.length ? unavailableOutboundSources.map(outboundSourceLabel).join(", ") : "None" },
                { label: "Phone video state", value: getByPath(outboundCaps, "degraded", false) ? "Partially ready" : prettyLabel(getByPath(outboundCaps, "runtime_status", getByPath(outboundCaps, "enabled", false) ? "ready" : "off")) },
                { label: "Webcam ready", value: cameraReadyLabel },
                { label: "Screen ready", value: yesNo(getByPath(outboundCaps, "available_sources.remote_desktop.enabled", false)) }
            ])
            + "<div class=\"ayu-inline-actions\">" + button("Save source", "save-video-call", "primary", "save") + button("Refresh status", "ops-refresh", "secondary", "refresh", "sm") + "</div>";
        var remoteSettingsMarkup = remoteDesktopNudge
            + "<div class=\"ayu-note ayu-note-blue\">Screen sharing, Remote Desktop control, and game mode are controlled in Permissions.</div>"
            + field("Display / monitor", select("videoCall.remote_desktop.monitor_id", monitorDeviceOptions), "Choose all displays or one physical monitor. Refresh devices after connecting or rearranging displays.")
            + "<div class=\"ayu-grid-2\">"
            + field("Capture quality", select("videoCall.remote_desktop.quality", [
                { value: "low", label: "Low - 960 px, 8 FPS" },
                { value: "balanced", label: "Balanced - 1280 px, 12 FPS" },
                { value: "high", label: "High - 1920 px, 20 FPS" },
                { value: "ultra", label: "Ultra - 2560 px, 24 FPS" }
            ]), "Higher settings improve desktop text clarity but use more CPU, GPU, and network bandwidth.")
            + field("Maximum video bitrate (kbps)", input("videoCall.remote_desktop.bitrate_kbps", { type: "number", placeholder: "1500", extraAttrs: "min=\"250\" max=\"3000\" step=\"50\"" }), "Applies to active desktop tracks. An existing connection may need one reconnect when increasing above 1500 kbps.")
            + "</div>"
            + field("Game buttons", input("videoCall.remote_desktop.game_buttons", { placeholder: "A:action_a, B:action_b" }), "Up to four comma-separated Label:button_name controls on iOS and Android. Labels are 1-12 characters without commas or colons. Leave empty for joystick only. Names use letters, digits, underscore, dot, or hyphen and must start with a letter.")
            + "<div class=\"ayu-remote-desktop-runtime ayu-note ayu-note-" + (remoteControlEnabled ? "green" : (remoteControlConfigured && !remoteControlAvailable ? "red" : "blue")) + "\"><strong>" + escapeHtml(remoteControlEnabled ? "Screen control is ready." : (remoteControlConfigured && !remoteControlAvailable ? "Screen control is unavailable on this host." : "Screen viewing is read-only.")) + "</strong><p>" + escapeHtml(remoteControlEnabled ? "iOS and Android provide native full-screen touch, fixed-pointer mode, and game controls. macOS and Windows provide mouse and keyboard; Chrome supports pointer lock. Control pauses when you switch away or leave full screen." : (remoteControlConfigured && remoteControlAvailable ? "Save these settings to enable full-screen input." : "Turn on Control Remote Desktop to permit full-screen input.")) + "</p></div>"
            + renderStatusRows([
                { label: "Shown to phones", value: yesNo(getByPath(remoteCaps, "enabled", false)) },
                { label: "Monitor id", value: String(getByPath(remoteCaps, "monitor_id", getByPath(state.forms, "videoCall.remote_desktop.monitor_id", 0))) },
                { label: "Capture profile", value: prettyLabel(remoteQuality) + " - " + String(getByPath(remoteCaps, "max_width", 1280)) + " px at " + String(getByPath(remoteCaps, "fps", 12)) + " FPS" },
                { label: "Maximum bitrate", value: String(getByPath(remoteCaps, "bitrate_kbps", getByPath(state.forms, "videoCall.remote_desktop.bitrate_kbps", 1500))) + " kbps" },
                { label: "Native control", value: remoteControlEnabled ? "Ready" : (remoteControlConfigured ? (remoteControlAvailable ? "Save to enable" : "Unavailable") : "View only") },
                { label: "Screen stream ready", value: yesNo(remoteTrackAvailable) }
            ])
            + "<div class=\"ayu-inline-actions\">" + button("Save system settings", "save-video-call", "primary", "save") + button("Open Permissions", "nav:permissions", "ghost", "shield", "sm") + (remoteAgentInstalled ? button("Open optional web console", "nav:agents", "ghost", "agents", "sm") : "") + "</div>";
        var capabilityMarkup = capabilityError + renderStatusRows([
            { label: "Video enabled", value: yesNo(getByPath(videoCaps, "enabled", false)) },
            { label: "Receives phone camera", value: yesNo(getByPath(videoCaps, "receive_enabled", false)) },
            { label: "Records received video", value: yesNo(getByPath(videoCaps, "record_my_video", false)) },
            { label: "Audio-only call recording", value: yesNo(getByPath(audioCaps, "audio_only_call_recording.enabled", false)) },
            { label: "Recording mode", value: getByPath(videoCaps, "recording_mode", "video") },
            { label: "Recording format", value: getByPath(videoCaps, "recording_format", "jpeg_frames") },
            { label: "Image interval", value: String(getByPath(videoCaps, "image_interval_seconds", getByPath(state.forms, "videoCall.image_interval_seconds", 5))) + "s" },
            { label: "Recording location", value: getByPath(videoCaps, "recording_dir", getByPath(state.forms, "videoCall.recording_dir", "") || "Default"), mono: true },
            { label: "Records audio calls", value: yesNo(getByPath(state.forms, "speech.voice_training_capture_enabled", false)) },
            { label: "Voice training location", value: voiceTrainingDir, mono: true },
            { label: "Audio enabled", value: yesNo(getByPath(audioCaps, "enabled", false)) },
            { label: "Audio file playback allowed", value: yesNo(getByPath(state.forms, "audioPlayback.enabled", true)) },
            { label: "Background mode", value: yesNo(getByPath(audioCaps, "background_mode.enabled", getByPath(state.forms, "videoCall.background_mode_enabled", false))) },
            { label: "Background audio available", value: yesNo(getByPath(audioCaps, "background_mode.available", false)) },
            { label: "Background audio direction", value: "iOS " + getByPath(audioCaps, "background_mode.ios_client_audio_direction", getByPath(audioCaps, "background_mode.client_audio_direction", "recvonly")) + " / Android " + getByPath(audioCaps, "background_mode.android_client_audio_direction", "inactive") },
            { label: "Background server audio", value: "iOS " + prettyLabel(getByPath(audioCaps, "background_mode.ios_server_audio_output", "quiet_background_audio")) + " / Android " + prettyLabel(getByPath(audioCaps, "background_mode.android_server_audio_output", getByPath(audioCaps, "background_mode.server_audio_output", "none"))) },
            { label: "Safety recording", value: yesNo(getByPath(audioCaps, "silent_recording.enabled", getByPath(state.forms, "videoCall.silent_recording_enabled", false))) },
            { label: "Location recording", value: yesNo(getByPath(capabilities, "location.recording_enabled", getByPath(state.forms, "videoCall.location_recording_enabled", false))) },
            { label: "WUIFT hold-to-finish", value: yesNo(getByPath(audioCaps, "wuift.enabled", getByPath(state.forms, "videoCall.wuift_enabled", true))) },
            { label: "Safety audio direction", value: "Phone " + getByPath(audioCaps, "silent_recording.client_audio_direction", "sendonly") + " / server " + getByPath(audioCaps, "silent_recording.server_audio_direction", "recvonly") },
            { label: "Safety recording batch", value: String(getByPath(audioCaps, "silent_recording.batch_seconds", getByPath(state.forms, "videoCall.silent_recording_batch_seconds", 3599))) + "s" },
            { label: "Safety recording location", value: getByPath(audioCaps, "silent_recording.recording_dir", getByPath(state.forms, "videoCall.silent_recording_dir", "") || "Default"), mono: true },
            { label: "Computer audio sources", value: selectedAudioSources.length ? selectedAudioSources.map(audioSourceLabel).join(", ") : "None" },
            { label: "Microphone source", value: selectedAudioSources.indexOf("microphone") !== -1 ? getByPath(audioCaps, "input_audio_source", "default") : "Not used" },
            { label: "Spoken AI replies", value: yesNo(getByPath(audioCaps, "ai_audio_replies_enabled", true)) },
            { label: "AutoYou Agents disabled", value: yesNo(getByPath(audioCaps, "agents_disabled", getByPath(videoCaps, "disable_autoyou_agents", false))) },
            { label: "Assistant listening", value: yesNo(getByPath(audioCaps, "agent_processing_enabled", false)) },
            { label: "Speech replies ready", value: yesNo(getByPath(audioCaps, "tts_available", false)) },
            { label: "Computer video", value: prettyLabel(getByPath(outboundCaps, "runtime_status", getByPath(outboundCaps, "enabled", false) ? "ready" : "off")) },
            { label: "Computer video sources", value: selectedOutboundSources.length ? selectedOutboundSources.map(outboundSourceLabel).join(", ") : "None" },
            { label: "Connected clients", value: String(getByPath(datachannel, "connected_clients", getByPath(datachannel, "active_sessions", 0))) }
        ]);
        return "<div class=\"ayu-screen\"><div class=\"ayu-hero\"><div class=\"ayu-hero-copy\"><h1>Video & Calls</h1><p>Choose the computer devices, capture profiles, playback inputs, and storage locations used by calls.</p></div><div class=\"ayu-inline-actions\">" + button("Open Permissions", "nav:permissions", "primary", "shield") + button("Refresh devices", "refresh-webrtc-devices", "secondary", "refresh") + button("Live QR connect", "live-pair", "ghost", "qr") + button("Refresh status", "ops-refresh", "ghost", "refresh") + "</div></div><div class=\"ayu-grid-2\">" + panel("Computer audio and video devices", "Select microphones and cameras for computer media sources.", videoSettingsMarkup) + panel("Playback and input sources", "Choose files and source ids for video playback and API-fed frames.", outboundSettingsMarkup) + "</div><div class=\"ayu-grid-2\">" + panel("Display and capture quality", "Choose the monitor, resolution profile, and bitrate for computer screen video.", remoteSettingsMarkup) + panel("Current permission status", "Read-only status from the settings managed in Permissions.", capabilityMarkup) + "</div>" + panel("Current audio mix", "Active computer audio sources and current connection state.", callSourcesMarkup) + "</div>";
    }

    function renderSpeechProviderSettingsBody() {
        var provider = activeTtsProvider();
        var option = findOption(TTS_PROVIDER_OPTIONS, provider, "system") || TTS_PROVIDER_OPTIONS[0];
        var fields = [field("TTS rate", input("speech.tts_rate", { placeholder: "1.0" }), "Use 1.0 for normal speed. Lower values slow the voice down.")];
        if (provider === "system") {
            fields.push(field("System voice", select("speech.system_voice", buildSystemVoiceOptions()), "Leave empty to use the operating system default voice."));
        } else if (provider === "custom") {
            var customVoice = getByPath(state.speechLibrary, "status.custom_voice", {});
            var customVoices = getByPath(state.speechLibrary, "status.custom_voices", []);
            var ready = Boolean(getByPath(customVoice, "ready", false));
            fields.push("<div class=\"ayu-note ayu-note-" + (ready ? "green" : "amber") + "\">" + escapeHtml(ready ? "Custom voice model artifacts are ready for local synthesis." : "Prepare the custom voice in the Voice Training app before using this provider.") + "</div>");
            if (Array.isArray(customVoices) && customVoices.length) {
                fields.push("<div class=\"ayu-list\">" + customVoices.map(function (voice) {
                    var label = getByPath(voice, "display_name", getByPath(voice, "name", "Custom trained voice"));
                    var detail = getByPath(voice, "fine_tuning_applied", false)
                        ? "Fine-tuned custom voice"
                        : "Prepared provider; fine-tuning has not been applied";
                    return "<div class=\"ayu-list-row\"><div class=\"ayu-list-copy\"><strong>" + escapeHtml(label) + "</strong><small>" + escapeHtml(detail) + "</small></div>" + badge(getByPath(voice, "ready", false) ? "Ready" : "Not ready", getByPath(voice, "ready", false) ? "green" : "amber") + "</div>";
                }).join("") + "</div>");
            }
        } else if (provider === "emotivoice") {
            var emotivoice = getByPath(state.speechLibrary, "status.emotivoice", {});
            var modelsReady = Boolean(getByPath(emotivoice, "models_ready", false));
            var runtimeAvailable = Boolean(getByPath(emotivoice, "runtime_available", getByPath(emotivoice, "runtime_source_ready", true)));
            var dependencies = getByPath(emotivoice, "missing_dependencies", []);
            var downloadSupported = Boolean(getByPath(emotivoice, "download_supported", false));
            var acceleration = getByPath(emotivoice, "acceleration", {});
            var selectedDevice = getByPath(acceleration, "selected_device", "cpu");
            var statusNote = !runtimeAvailable
                ? "The compiled EmotiVoice runtime is not included in this server build. Install a full voice build (connector-full or training-full on Windows), then download its model checkpoints here."
                : (!modelsReady && !downloadSupported
                    ? "The compiled EmotiVoice runtime is installed, but model-download packages are missing. Install a full voice profile to enable downloads."
                : (modelsReady && !dependencies.length
                    ? "EmotiVoice checkpoints and runtime dependencies are ready on this server. Accelerator: " + selectedDevice + "."
                    : "The compiled runtime is installed. Download the checkpoints once, then restart the voice session."));
            fields.push("<div class=\"ayu-note ayu-note-" + (runtimeAvailable && modelsReady && !dependencies.length ? "green" : "amber") + "\">" + escapeHtml(statusNote) + "</div>");
            fields.push(field("Voice speaker", select("speech.emotivoice_speaker", buildSimpleOptions(getByPath(emotivoice, "speaker_ids", []), getByPath(state.forms, "speech.emotivoice_speaker", "8051"))), "Choose a local EmotiVoice speaker ID."));
            fields.push(checkbox("speech.emotivoice_conversation_emotion", "Use emotion from this conversation", "AutoYou reads the current user transcript and reply transiently to choose an expressive style."));
            if (runtimeAvailable && !modelsReady && getByPath(emotivoice, "download_supported", false)) {
                fields.push("<div class=\"ayu-inline-actions\">" + button("Download EmotiVoice models", "speech-download-emotivoice", "primary", "download") + "</div>");
                fields.push("<div class=\"ayu-hint\">Model data will be saved under <code>" + escapeHtml(getByPath(emotivoice, "model_dir", "the configured voice-model workspace")) + "</code>.</div>");
                fields.push("<div class=\"ayu-note ayu-note-amber\">" + escapeHtml(getByPath(emotivoice, "model_license_note", "Review upstream model terms before use.")) + "</div>");
            }
            if (dependencies.length) {
                fields.push("<div class=\"ayu-note ayu-note-amber\">Missing runtime packages: " + escapeHtml(dependencies.join(", ")) + ". Install the full voice profile.</div>");
            }
        } else if (provider === "openai") {
            fields.push(field("OpenAI base URL", input("speech.openai_base_url", { placeholder: "https://api.openai.com/v1" })));
            fields.push(field("OpenAI API key", input("speech.openai_api_key", { placeholder: "Leave blank to keep saved key", type: "password" }), "Only enter a value when rotating or adding the saved key."));
            fields.push(field("OpenAI model", select("speech.openai_model", buildSimpleOptions(getByPath(state.speechLibrary, "status.openai_tts_models", []), getByPath(state.forms, "speech.openai_model", "gpt-4o-mini-tts")))));
            fields.push(field("OpenAI voice", select("speech.openai_voice", buildSimpleOptions(getByPath(state.speechLibrary, "status.openai_tts_voices", []), getByPath(state.forms, "speech.openai_voice", "alloy")))));
            fields.push(field("OpenAI instructions", input("speech.openai_instructions", { placeholder: "Optional voice steering" }), "Optional style guidance for the generated voice."));
        } else if (provider === "azure") {
            fields.push(field("Azure speech key", input("speech.azure_speech_key", { placeholder: "Leave blank to keep saved key", type: "password" }), "Only enter a value when rotating or adding the saved key."));
            fields.push(field("Azure region", input("speech.azure_speech_region", { placeholder: "eastus" })));
            fields.push(field("Azure voice", input("speech.azure_voice", { placeholder: "en-US-AvaMultilingualNeural" })));
            fields.push(field("Azure endpoint ID", input("speech.azure_endpoint_id", { placeholder: "Optional custom endpoint" })));
        }
        var providerNote = provider === "off"
            ? "Text-to-speech is disabled. Speech recognition controls remain available below for calls and voice notes."
            : (provider === "custom"
                ? "Custom cloned voice uses local VITS artifacts prepared by the Voice Training app."
                : (provider === "emotivoice"
                ? "EmotiVoice synthesizes locally through AutoYou's existing call and voice-note audio paths."
                : (provider === "system"
                ? "System speech keeps synthesis fully local to this machine."
                : "Cloud text-to-speech stays inactive until you save the selected provider and its credentials.")));
        return renderChoiceCards(TTS_PROVIDER_OPTIONS, provider, "select-tts-provider") + "<div class=\"ayu-note\"><strong>Selected path:</strong> " + escapeHtml(option.label) + ". " + escapeHtml(providerNote) + "</div>" + (fields.length ? ("<div class=\"ayu-grid-2\">" + fields.join("") + "</div>") : "") + "<div class=\"ayu-inline-actions\">" + button("Save speech settings", "save-speech", "primary", "save") + "</div>";
    }

    function renderSpeechRecognitionBody() {
        var statusPayload = state.speechLibrary.status || {};
        var downloads = getByPath(state.speechLibrary, "downloads.jobs", []);
        var models = getByPath(statusPayload, "models", []);
        var installedModels = getByPath(statusPayload, "installed_models", []);
        var recordingPaths = getByPath(state.bootstrap, "metadata.recording_paths", {});
        var voiceTrainingDir = firstNonBlank([
            getByPath(recordingPaths, "voice_training.active_dir", ""),
            getByPath(recordingPaths, "voice_training.default_dir", "")
        ], "Unknown");
        var libraryMarkup = Array.isArray(models) && models.length ? "<div class=\"ayu-list\">" + models.slice(0, 10).map(function (model) {
            if (typeof model === "string") {
                return "<div class=\"ayu-list-row\"><div class=\"ayu-list-copy\"><strong>" + escapeHtml(model) + "</strong><small>Available in the local speech library.</small></div><div class=\"ayu-inline-actions\">" + button("Use the model", "speech-use-model:" + modelKey(model), "secondary", "save", "sm", "data-model=\"" + escapeHtml(model) + "\"") + "</div></div>";
            }
            var name = model.label || model.model || model.repo_id || model.reference || "Speech model";
            var summary = model.summary || model.profile || model.repo_path || model.size_on_disk_human || "Available locally";
            var modelName = getByPath(model, "model", name);
            var installed = getByPath(model, "installed", false);
            return "<div class=\"ayu-list-row\"><div class=\"ayu-list-copy\"><strong>" + escapeHtml(name) + "</strong><small>" + escapeHtml(summary) + "</small></div><div class=\"ayu-inline-actions\">" + badge(installed ? "Installed" : "Available", installed ? "green" : "blue") + (installed ? button("Use the model", "speech-use-model:" + modelKey(modelName), "secondary", "save", "sm", "data-model=\"" + escapeHtml(modelName) + "\"") + button("Delete", "speech-delete-model:" + modelKey(modelName), "danger", "trash", "sm", "data-model=\"" + escapeHtml(modelName) + "\"") : button("Download", "speech-download-model:" + modelKey(modelName), "primary", "plus", "sm", "data-model=\"" + escapeHtml(modelName) + "\"")) + "</div></div>";
        }).join("") + "</div>" : "<div class=\"ayu-empty\">No speech model inventory was reported yet.</div>";
        var downloadsMarkup = Array.isArray(downloads) && downloads.length ? "<div class=\"ayu-list\">" + downloads.map(function (job) {
            var progress = Number(getByPath(job, "progress_percent", 0)) || 0;
            var status = getByPath(job, "status", "queued");
            return "<div class=\"ayu-list-row ayu-list-row-stack\"><div class=\"ayu-list-copy\"><strong>" + escapeHtml(job.reference || job.model || job.job_id || "Job") + "</strong><small>" + escapeHtml(getByPath(job, "message", status)) + "</small></div><div class=\"ayu-inline-actions\">" + badge(status, statusTone(status)) + "<span class=\"ayu-hint ayu-mono\">" + escapeHtml(progress.toFixed(1)) + "%</span></div>" + renderProgressTrack(progress, String(status).toLowerCase() === "failed" ? "failed" : "") + (getByPath(job, "error", "") ? "<div class=\"ayu-note ayu-note-red\">" + escapeHtml(getByPath(job, "error", "")) + "</div>" : "") + "</div>";
        }).join("") + "</div>" : "<div class=\"ayu-empty\">No active STT download jobs.</div>";
        return renderStatusRows([
            { label: "Speech library supported", value: getByPath(statusPayload, "supported", true) ? "Yes" : "No", help: getByPath(statusPayload, "supported", true) ? "Speech models can be downloaded and selected on this machine." : "The local speech model helper is not available in this runtime." },
            { label: "Selected STT model", value: getByPath(statusPayload, "selected_model", getByPath(state.forms, "speech.stt_model", "tiny.en")) },
            { label: "Voice training capture", value: yesNo(getByPath(state.forms, "speech.voice_training_capture_enabled", false)) },
            { label: "Voice training location", value: voiceTrainingDir, mono: true },
            { label: "Installed models", value: String(Array.isArray(installedModels) ? installedModels.length : 0) },
            { label: "Cache directory", value: getByPath(statusPayload, "cache_dir", "Unknown"), mono: true }
        ]) + "<div class=\"ayu-soft-divider\"></div><div class=\"ayu-note\">Voice-call recording is controlled in <a href=\"#\" data-action=\"nav:permissions\">Permissions</a>. The Voice Training app does not need to be open when capture is enabled.</div><div class=\"ayu-soft-divider\"></div><div class=\"ayu-grid-2\">" + field("STT model", select("speech.stt_model", buildSpeechModelOptions())) + field("STT language", input("speech.stt_language", { placeholder: "en" })) + field("STT device", select("speech.stt_device", buildSimpleOptions(getByPath(statusPayload, "stt_device_suggestions", []), getByPath(state.forms, "speech.stt_device", "cpu")))) + field("STT compute type", select("speech.stt_compute_type", buildSimpleOptions(getByPath(statusPayload, "stt_compute_type_suggestions", []), getByPath(state.forms, "speech.stt_compute_type", "float32")))) + field("Silero sensitivity", input("speech.stt_silero_sensitivity", { placeholder: "0.4" })) + field("Post-speech silence", input("speech.stt_post_speech_silence_duration", { placeholder: "0.6" })) + "</div><div class=\"ayu-inline-actions\">" + button("Save speech settings", "save-speech", "secondary", "save") + "</div><div class=\"ayu-soft-divider\"></div><h3 style=\"margin:0 0 10px;font-size:15px;\">Speech model library</h3>" + libraryMarkup + "<div class=\"ayu-soft-divider\"></div>" + field("Model name", select("speech.download_model", buildSpeechModelOptions()), "Use the library name reported by the speech model helper.") + "<div class=\"ayu-inline-actions\">" + button("Download STT model", "speech-download", "primary", "plus") + button("Open speech guide", "open-guide:/guides/speech", "secondary", "book") + "</div><div class=\"ayu-soft-divider\"></div><h3 style=\"margin:0 0 10px;font-size:15px;\">Download jobs</h3>" + downloadsMarkup;
    }

    function renderSpeechScreen() {
        return "<div class=\"ayu-screen\"><div class=\"ayu-hero\"><div class=\"ayu-hero-copy\"><h1>Speech</h1><p>Choose the text-to-speech path for this machine first, then manage local speech recognition models separately so only the relevant settings stay on screen.</p></div><div class=\"ayu-inline-actions\">" + button("Refresh speech data", "speech-refresh", "secondary", "refresh") + "</div></div><div class=\"ayu-grid-2\">" + panel("Speech output", "Switch between local system speech, custom cloned voice, OpenAI, Azure, or fully disabled output.", renderSpeechProviderSettingsBody()) + panel("Speech recognition & downloads", "Local faster-whisper selection, runtime tuning, and download jobs for speech-to-text.", renderSpeechRecognitionBody()) + "</div></div>";
    }

    function renderConnectivityScreen() {
        var status = getByPath(state.bootstrap, "status", {});
        var cloud = state.operations.cloudStatus || getByPath(status, "cloud", {});
        var tunnelmole = getByPath(status, "tunnelmole", {});
        var metadata = getByPath(state.bootstrap, "metadata.tunnelmole", {});
        var routes = browserWebsiteRoutes();
        var cloudEnrolled = Boolean(getByPath(cloud, "enrolled", false));
        var cloudTone = getByPath(cloud, "token_rejected", false) ? "red" : (getByPath(cloud, "connected", false) ? "green" : (cloudEnrolled ? "amber" : ""));
        var cloudMessage = getByPath(cloud, "status_message", getByPath(cloud, "detail", getByPath(cloud, "connected", false) ? "Connected to AutoYou Cloud." : (cloudEnrolled ? "Linked to AutoYou Cloud, but not currently connected." : "This server is not linked to AutoYou Cloud yet.")));
        var cloudActiveState = getByPath(cloud, "is_active", null);
        var cloudNeedsRelink = Boolean(getByPath(cloud, "needs_reregister", false));
        var cloudActivateUrl = getByPath(cloud, "activate_url", "");
        var cloudRoutingNote = cloudActiveState === false
            ? "Cloud Pair client requests are going to another linked server. Use Make active here to move new requests to this server."
            : (cloudActiveState === true ? "This is the active cloud server. Other linked servers will not receive new Cloud Pair client requests." : "Active-server routing has not been verified yet.");
        var cloudNotificationMarkup = "<div class=\"ayu-soft-divider\"></div><h3 style=\"margin:0 0 10px;font-size:15px;\">Offline notification test</h3>" + field("Title", input("liveOps.cloud_notify_title", { placeholder: "AutoYou" })) + field("Message", textarea("liveOps.cloud_notify_body", { rows: 4, placeholder: "Notification from your AutoYou server." }), "Sends through the linked AutoYou account. Client devices must opt in, and background push requires the offline notification tier.") + field("Category", input("liveOps.cloud_notify_category", { placeholder: "admin" })) + "<div class=\"ayu-inline-actions\">" + button("Send notification", "cloud-notify-client", "primary", "msg", "sm", cloudEnrolled ? "" : "disabled") + "</div>";
        var cloudActionButtons = [];
        if (!cloudEnrolled) {
            cloudActionButtons.push("<a class=\"ayu-link-btn ayu-btn ayu-btn-primary\" href=\"/api/cloud/link-start\" target=\"_blank\" rel=\"noopener noreferrer\">" + icon("cloud") + "<span>Link to AutoYou Cloud</span></a>");
        } else if (cloudNeedsRelink) {
            cloudActionButtons.push("<a class=\"ayu-link-btn ayu-btn ayu-btn-primary\" href=\"/api/cloud/link-start\" target=\"_blank\" rel=\"noopener noreferrer\">" + icon("cloud") + "<span>Reconnect AutoYou account</span></a>");
            cloudActionButtons.push("<form method=\"post\" action=\"/api/cloud/unregister\"><button class=\"ayu-form-btn ayu-btn ayu-btn-danger ayu-btn-sm\" type=\"submit\">" + icon("trash") + "<span>Unlink server</span></button></form>");
        } else {
            if (cloudActiveState !== true && cloudActivateUrl) {
                cloudActionButtons.push(button("Make active here", "cloud-activate", "primary", "bolt", "sm"));
            }
            cloudActionButtons.push(button("Reconnect cloud link", "cloud-reregister", "ghost", "refresh", "sm"));
            cloudActionButtons.push("<a class=\"ayu-link-btn ayu-btn ayu-btn-secondary ayu-btn-sm\" href=\"/api/cloud/link-start\" target=\"_blank\" rel=\"noopener noreferrer\">" + icon("cloud") + "<span>Switch account</span></a>");
            cloudActionButtons.push("<form method=\"post\" action=\"/api/cloud/unregister\"><button class=\"ayu-form-btn ayu-btn ayu-btn-danger ayu-btn-sm\" type=\"submit\">" + icon("trash") + "<span>Unlink server</span></button></form>");
        }
        var cloudActionsMarkup = "<div class=\"ayu-inline-actions\">" + cloudActionButtons.join("") + "</div>";
        var cloudMarkup = "<div class=\"ayu-note" + (cloudTone ? " ayu-note-" + escapeHtml(cloudTone) : "") + "\">" + escapeHtml(cloudMessage) + "</div>" + renderStatusRows([
            { label: "Linked account", value: firstNonBlank([getByPath(cloud, "email", "")], "No linked account") },
            { label: "Linked server", value: displayServerId(cloud), mono: true },
            { label: "Cloud link", value: getByPath(cloud, "sse_connected", false) ? "Connected" : "Disconnected", help: "Cloud Pair requests can reach this server when the link is connected." },
            { label: "Receives client requests", value: cloudActiveState === null ? "Unknown" : (cloudActiveState ? "Yes" : "No") },
            { label: "Needs reconnect", value: cloudNeedsRelink ? "Yes" : "No" }
        ]) + "<div class=\"ayu-note ayu-note-blue\">" + escapeHtml(cloudRoutingNote) + "</div>" + (cloudNeedsRelink ? "<div class=\"ayu-note ayu-note-amber\">This account link was rejected. Use Reconnect AutoYou account above to sign in and replace it.</div>" : "") + cloudActionsMarkup + "<div class=\"ayu-note ayu-note-gray\">Linking signs this server in. Making it active is what moves Cloud Pair client requests away from other linked servers.</div>" + cloudNotificationMarkup;
        cloudMarkup += renderCloudDevicesMarkup();
        var sessionMarkup = "<div class=\"ayu-note\">" + escapeHtml("Connected clients " + String(getByPath(state.operations, "datachannel.connected_clients", getByPath(state.operations, "datachannel.active_sessions", 0))) + ".") + "</div>" + renderDatachannelSessionList(state.operations.datachannel || {}) + renderBluetoothPairPanelMarkup();
        return "<div class=\"ayu-screen\"><div class=\"ayu-hero\"><div class=\"ayu-hero-copy\"><h1>Connectivity</h1><p>Cloud link state, public link pairing modes, connected clients, scheduler queue status, and connection helpers used by paired clients.</p></div><div class=\"ayu-inline-actions\">" + button("Refresh status", "ops-refresh", "secondary", "refresh") + button("Open messaging", "nav:messaging", "ghost", "msg") + "<a class=\"ayu-link-btn ayu-btn ayu-btn-secondary\" href=\"/guides/connectivity\" target=\"_blank\" rel=\"noreferrer\">" + icon("book") + "<span>Connectivity Guide</span></a></div></div><div class=\"ayu-grid-2\">" + panel("AutoYou Cloud", "Only the active linked server receives Cloud Pair client requests. Link signs this server in; Make active moves routing here.", cloudMarkup) + panel("Public link", "Give this server a public link so clients outside your LAN can pair. Configure pair-code and connection modes here.", field("Status", "<div class=\"ayu-note ayu-note-" + escapeHtml(statusTone(tunnelmole.status || "")) + "\">" + escapeHtml(tunnelmole.status || "Unknown") + "</div>") + field("Public URL", "<div class=\"ayu-note ayu-note-green ayu-mono\">" + escapeHtml(tunnelmole.public_url || "No public URL") + "</div>") + field("Pair URL", "<div class=\"ayu-note ayu-note-amber ayu-mono\">" + escapeHtml(tunnelmole.pair_url || "No pair URL") + "</div>") + checkbox("connectivity.tunnelmole.enabled", "Enable public link") + "<div class=\"ayu-grid-2\">" + field("Timed lifetime (minutes)", input("connectivity.tunnelmole.timeout_minutes", { type: "number" })) + field("Pairing code timeout", input("connectivity.tunnelmole.otp_timeout_minutes", { type: "number" })) + field("Pair-code mode", select("connectivity.tunnelmole.pair_code_mode", getByPath(metadata, "pair_code_modes", []).map(function (item) { return { value: item.id, label: item.label }; }))) + field("Connection mode", select("connectivity.tunnelmole.connection_mode", getByPath(metadata, "connection_modes", []).map(function (item) { return { value: item.id, label: item.label }; }))) + "</div>" + checkbox("connectivity.tunnelmole.otp_multiuse", "Allow pairing code reuse within the configured timeout window") + checkbox("connectivity.tunnelmole.url_only_pair", "Share URL only (most secure)", "Secure Professional + Authenticator pair-code mode: AutoYou sends only the public link. Clients sign in with the shared 2FA setup key and password you handed over separately - nothing secret travels over the message.") + checkbox("connectivity.tunnelmole.auto_start_on_boot", "Auto-connect public URL on startup", "Brings your persistent public link back online automatically each time AutoYou starts. Requires a Public Proxy plan; free servers skip this and keep using on-demand links.") + "<div class=\"ayu-inline-actions\">" + button("Save link settings", "save-tunnelmole", "primary", "save") + button("Start", "service:tunnelmole:start", "green", "play", "sm") + button("Stop", "service:tunnelmole:stop", "secondary", "stop", "sm") + button("Refresh", "service:tunnelmole:refresh", "ghost", "refresh", "sm") + "</div>") + "</div><div class=\"ayu-grid-2\">" + panel("Connected clients", "Client reachability and one-click selection for messaging and playback.", sessionMarkup) + panel("Notification queue", "Queued reminder and task deliveries waiting for a connection or retry window.", renderSchedulerQueue(state.operations.queue)) + "</div><div class=\"ayu-grid-2\">" + panel("Connection helpers", "Paste provider details or edit the resolved helper list directly. Use Add to merge with existing entries or Replace to overwrite them from the pasted input.", field("Quick import", textarea("connectivity.iceImportText", { rows: 6, extraClass: "ayu-mono", placeholder: "Paste provider details, connection helper JSON, or connection server URLs" }), "Accepts provider details, connection helper JSON, or connection server URLs.") + "<div class=\"ayu-inline-actions\">" + button("Add to existing", "ice-parse-append", "secondary", "plus", "sm") + button("Replace from input", "ice-parse-replace", "ghost", "refresh", "sm") + "<a class=\"ayu-link-btn ayu-btn ayu-btn-secondary ayu-btn-sm\" href=\"/guides/connectivity\" target=\"_blank\" rel=\"noreferrer\">" + icon("book") + "<span>Connectivity Guide</span></a></div><div class=\"ayu-soft-divider\"></div>" + field("Connection helper JSON", textarea("connectivity.iceServersText", { rows: 12, extraClass: "ayu-mono" }), "AutoYou saves the full list and preserves your exact structure.") + "<div class=\"ayu-inline-actions\">" + button("Save helpers", "save-ice", "primary", "save") + "</div>") + panel("Browser routes", "Path-routed agent websites and explicit same-port routes visible to AutoYou browser clients. Set which one connected clients open by default.", renderBrowserRoutesPanelBody(routes, getByPath(status, "browser", {}))) + "</div></div>";
    }

    function renderCloudDevicesMarkup() {
        var data = state.operations.cloudDevices || {};
        var approval = asBoolean(getByPath(data, "require_device_approval", false), false);
        var pending = getByPath(data, "pending", []) || [];
        var devices = getByPath(data, "devices", []) || [];
        function deviceRow(entry, actions) {
            return "<div class=\"ayu-list-row\"><div class=\"ayu-list-copy\"><strong>" + escapeHtml(entry.name || "Unnamed device") + "</strong><p>Key " + escapeHtml(entry.key_fingerprint || "") + "...</p></div><div class=\"ayu-inline-actions\">" + actions + "</div></div>";
        }
        var pendingMarkup = pending.map(function (entry) {
            return deviceRow(entry, button("Approve", "cloud-device-approve:" + entry.device_id, "primary", "check", "sm") + button("Remove", "cloud-device-forget:" + entry.device_id, "ghost", "trash", "sm"));
        }).join("");
        var deviceMarkup = devices.map(function (entry) {
            return deviceRow(entry, button("Remove", "cloud-device-forget:" + entry.device_id, "ghost", "trash", "sm"));
        }).join("");
        return "<div class=\"ayu-soft-divider\"></div><h3 style=\"margin:0 0 10px;font-size:15px;\">Cloud Pair devices</h3>"
            + "<div class=\"ayu-note ayu-note-gray\">Each device's security key is saved the first time it pairs through AutoYou Cloud, so the cloud cannot swap it later. "
            + (approval ? "New devices wait here until you approve them." : "Turn on approval so AutoYou Cloud cannot add a new device without you.") + "</div>"
            + (pendingMarkup ? "<h4 style=\"margin:10px 0 6px;font-size:13px;\">Waiting for approval</h4><div class=\"ayu-list\">" + pendingMarkup + "</div>" : "")
            + (deviceMarkup ? "<div class=\"ayu-list\">" + deviceMarkup + "</div>" : "<div class=\"ayu-note\">No device has paired through AutoYou Cloud yet.</div>")
            + "<div class=\"ayu-inline-actions\">" + (approval
                ? button("Stop asking for approval", "cloud-device-approval:off", "ghost", "shield", "sm")
                : button("Approve new devices here", "cloud-device-approval:on", "secondary", "shield", "sm")) + "</div>";
    }

    function renderPasswordManagementBody() {
        var maximus = savedSecurityMode() === "secure_professional_maximus";
        return renderPasswordDraftFields() + renderPasswordGeneratorTools() + "<div class=\"ayu-inline-actions\">" + button("Update password", "security-save-password", "primary", "key") + button("Rotate Maximus storage key", "security-rotate-storage-key", "secondary", "shield", "sm", maximus ? "" : "disabled title=\"Switch to Secure Professional Maximus first\"") + "</div><div class=\"ayu-note ayu-note-blue\">" + escapeHtml(maximus ? "Re-encrypts protected files and databases with a new data key. Use after suspected exposure or on a planned rotation schedule." : "Storage-key rotation is available after Secure Professional Maximus is enabled.") + "</div>";
    }

    function renderTotpManagementBody() {
        var totp = getByPath(state.bootstrap, "status.totp", {});
        var configured = Boolean(getByPath(totp, "totp_configured", false));
        return "<div class=\"ayu-note ayu-note-" + escapeHtml(configured ? "green" : "amber") + "\">" + escapeHtml(configured ? "Shared authenticator is configured." : "No shared authenticator setup is configured yet.") + "</div><div class=\"ayu-helper-row\"><div><strong>Shared authenticator</strong><div class=\"ayu-hint\">" + escapeHtml(configured ? "1 shared setup available" : "No shared setup available") + "</div></div>" + badge(configured ? "Configured" : "Missing", configured ? "green" : "amber") + "</div>" + field("Import authenticator setup link or key", input("security.import_totp", { placeholder: "Paste setup link or setup key" }), "Use an existing shared authenticator setup when migrating from another machine.") + "<div class=\"ayu-inline-actions\">" + button("Generate setup", "totp-generate", "primary", "plus") + button("Show authenticator QR", "totp-show-secret", "secondary", "qr") + button("Show current code", "totp-show-code", "ghost", "key") + button("Import setup", "totp-import", "secondary", "save") + button("Delete setup", "totp-delete", "danger", "trash") + "</div>";
    }

    function renderZeroTouchPairingBody() {
        return '<div class="ayu-mobile-setup"><ol>'
            + '<li><strong>Link your account.</strong> Sign in to the same AutoYou account on this computer and your phone. Cloud Pair and QR setup are included with a paid account.</li>'
            + '<li><strong>Keep defaults or scan once.</strong> Factory Secure/Enhanced Cloud settings work with the same paid account. For custom settings or all pairing modes, open Settings → Automatic Configuration → Scan QR on your phone.</li>'
            + '<li><strong>Connect.</strong> Cloud Pair uses Enhanced pairing. If you change the computer password later, scan again or enter the same password on your phone.</li>'
            + '</ol><p class="ayu-hint">The QR is created on this computer and contains its pairing credentials. Keep it private.</p>'
            + '<div class="ayu-inline-actions">' + button("Live QR connect", "live-pair", "primary", "camera") + button("Show phone setup QR", "settings-export-qr", "secondary", "qr") + '</div></div>';
    }

    function buildTotpSecretModalHtml(payload) {
        return "<div class=\"ayu-grid\"><div class=\"ayu-note ayu-note-green\"><strong>Manual setup key</strong><div class=\"ayu-mono\">" + escapeHtml(getByPath(payload, "secret", "")) + "</div></div><div class=\"ayu-note ayu-note-gray\"><strong>Authenticator setup link</strong><div class=\"ayu-mono\">" + escapeHtml(getByPath(payload, "otpauth", "")) + "</div></div></div>";
    }

    function showTotpSecretModal(payload, title, description) {
        setModal({
            title: title,
            description: description,
            imageUrl: getByPath(payload, "qr_url", ""),
            html: buildTotpSecretModalHtml(payload),
            copyValue: getByPath(payload, "secret", "")
        });
    }

    function buildTotpCodeModalHtml(code, secondsRemaining, period) {
        var safePeriod = Number(period) || 30;
        var safeSeconds = Math.max(0, Number(secondsRemaining) || safePeriod);
        var pct = Math.max(0, Math.min(100, Math.round((safeSeconds / safePeriod) * 100)));
        return "<div class=\"ayu-totp-live\"><div class=\"ayu-kicker\">Current authenticator code</div><div id=\"ayu-totp-live-code\" class=\"ayu-totp-code\">" + escapeHtml(code) + "</div><div class=\"ayu-totp-meta\"><div class=\"ayu-progress\"><div id=\"ayu-totp-code-bar\" class=\"ayu-progress-fill\" style=\"width:" + escapeHtml(String(pct)) + "%\"></div></div><span id=\"ayu-totp-code-secs\" class=\"ayu-hint ayu-mono\">" + escapeHtml(String(safeSeconds)) + "s</span></div></div>";
    }

    async function showTotpCodeModal() {
        clearTotpCodeTimer();

        async function fetchAndRenderCode() {
            var payload = await requestJson("/admin/security/totp/current-code");
            var secondsRemaining = Number(getByPath(payload, "seconds_remaining", 30)) || 30;
            var period = Number(getByPath(payload, "period", 30)) || 30;
            setModal({
                title: "Current authenticator code",
                description: "This live code refreshes automatically until you close the dialog.",
                html: buildTotpCodeModalHtml(getByPath(payload, "code", ""), secondsRemaining, period),
                copyValue: getByPath(payload, "code", "")
            });
            liveTimers.totpCode = window.setInterval(function () {
                secondsRemaining -= 1;
                if (!state.modal) {
                    clearTotpCodeTimer();
                    return;
                }
                if (secondsRemaining <= 0) {
                    clearTotpCodeTimer();
                    fetchAndRenderCode().catch(function (error) {
                        setModal({
                            title: "Current authenticator code",
                            description: "No live code is available right now.",
                            text: error.message || String(error)
                        });
                    });
                    return;
                }
                var pct = Math.max(0, Math.min(100, Math.round((secondsRemaining / period) * 100)));
                var bar = document.getElementById("ayu-totp-code-bar");
                var secs = document.getElementById("ayu-totp-code-secs");
                if (bar) {
                    bar.style.width = pct + "%";
                }
                if (secs) {
                    secs.textContent = secondsRemaining + "s";
                }
            }, 1000);
        }

        await fetchAndRenderCode();
    }

    function assignableAgentWebsites() {
        var overview = getByPath(state.bootstrap, "agents.agent_overview", []) || [];
        return overview.filter(function (item) {
            return Boolean(item && item.installed && item.has_frontend);
        });
    }

    function renderAgentSecurityEnrolment(enrol) {
        var pid = getByPath(enrol, "profile_id", "");
        var verified = Boolean(state.agentSecurity.enrolmentVerified);
        var qrDataUrl = getByPath(enrol, "qr_data_url", "");
        var qrMarkup = qrDataUrl
            ? "<div class=\"ayu-2fa-enrol-qr\"><img src=\"" + escapeHtml(qrDataUrl) + "\" alt=\"Scan with Google Authenticator, Microsoft Authenticator, Authy, or any TOTP app\" width=\"220\" height=\"220\"></div>"
            : "<div class=\"ayu-note ayu-note-amber\">QR rendering isn't available on this server. Enter the manual key below in your authenticator app instead.</div>";
        var verifyErrorMarkup = state.agentSecurity.verifyError
            ? "<div class=\"ayu-note ayu-note-red\" style=\"margin-top:8px\">" + escapeHtml(state.agentSecurity.verifyError) + "</div>"
            : "";
        var confirmStepMarkup = verified
            ? "<div class=\"ayu-note ayu-note-green\" style=\"margin-top:8px\"><strong>Code verified.</strong> The authenticator matches  -  click Done to clear the secret and finish.</div>"
                + "<div class=\"ayu-inline-actions\">" + button("Done", "agent-2fa-finish:" + pid, "primary", "check", "sm")
                + button("Discard profile", "agent-2fa-discard:" + pid, "danger", "trash", "sm") + "</div>"
            : field("Enter the 6-digit code to confirm", input("agentSecurity.verifyCode", { placeholder: "123456", extraAttrs: "inputmode=\"numeric\" autocomplete=\"one-time-code\" maxlength=\"6\"" }))
                + verifyErrorMarkup
                + "<div class=\"ayu-inline-actions\">" + button("Verify code", "agent-2fa-verify:" + pid, "primary", "check", "sm")
                + button("Discard profile", "agent-2fa-discard:" + pid, "danger", "trash", "sm")
                + button("Done", "agent-2fa-finish:" + pid, "primary", "check", "sm", "disabled") + "</div>";
        return "<div class=\"ayu-note ayu-note-green\">"
            + "<strong>New profile  -  " + escapeHtml(getByPath(enrol, "label", "")) + "</strong>"
            + "<p style=\"margin:6px 0 12px\">1. Open Google Authenticator, Microsoft Authenticator, Authy, or any TOTP-compatible app. 2. Scan the QR code below (or enter the manual key). 3. Type the 6-digit code it shows and click Verify code. Done stays greyed out until that code actually matches  -  the secret is derived on demand, never stored, and is only erased from this page once you confirm Done.</p>"
            + qrMarkup
            + "<div class=\"ayu-hint\" style=\"margin-top:10px;text-align:center;word-break:break-all\">Manual entry key: <code>" + escapeHtml(getByPath(enrol, "manual_entry_secret", "")) + "</code></div>"
            + confirmStepMarkup
            + "</div>";
    }

    function renderAgentSecurityChips(profile, websiteOptions) {
        var assignedAgents = getByPath(profile, "assigned_agents", []) || [];
        if (!assignedAgents.length) {
            return "<div class=\"ayu-hint\">Not assigned to any agent website yet.</div>";
        }
        var knownNames = {};
        websiteOptions.forEach(function (item) { knownNames[item.agent_name] = item.display_name || item.agent_name; });
        return "<div class=\"ayu-2fa-chip-row\">" + assignedAgents.map(function (name) {
            var known = Boolean(knownNames[name]);
            var chipLabel = known ? knownNames[name] : (name + " (not installed)");
            return "<span class=\"ayu-2fa-chip" + (known ? "" : " ayu-2fa-chip-orphan") + "\"><span>" + escapeHtml(chipLabel) + "</span>"
                + "<button type=\"button\" class=\"ayu-2fa-chip-remove\" data-action=\"" + escapeHtml("agent-2fa-unassign:" + name) + "\" title=\"Remove 2FA from " + escapeHtml(name) + "\" aria-label=\"Remove 2FA from " + escapeHtml(name) + "\">&times;</button></span>";
        }).join("") + "</div>";
    }

    function renderAgentSecurityAssignRow(profile, websiteOptions) {
        var pid = getByPath(profile, "profile_id", "");
        var assignedAgents = getByPath(profile, "assigned_agents", []) || [];
        var assignedSet = {};
        assignedAgents.forEach(function (name) { assignedSet[name] = true; });
        var placeholder = websiteOptions.length ? "Choose an agent website..." : "No agent websites installed";
        var options = [{ value: "", label: placeholder, disabled: true }].concat(websiteOptions.map(function (item) {
            return {
                value: item.agent_name,
                label: (item.display_name || item.agent_name) + (assignedSet[item.agent_name] ? " (assigned)" : ""),
                disabled: Boolean(assignedSet[item.agent_name])
            };
        }));
        return "<div class=\"ayu-2fa-assign-row\">"
            + field("Assign to agent website", select("agentSecurity.assign_" + pid, options))
            + "<div class=\"ayu-inline-actions\">" + button("Assign", "agent-2fa-assign:" + pid, "secondary", "bolt", "sm", websiteOptions.length ? "" : "disabled") + "</div>"
            + "</div>";
    }

    function renderAgentSecurityProfilesBody() {
        var sec = state.agentSecurity || {};
        if (!sec.available) {
            return "<div class=\"ayu-note ayu-note-amber\"><strong>Unlock required</strong><p style=\"margin:6px 0 0\">Per-agent 2FA profiles need the server unlocked." + (sec.error ? " " + escapeHtml(sec.error) : "") + "</p></div>";
        }
        var enrol = sec.lastEnrolment;
        var enrolMarkup = enrol ? renderAgentSecurityEnrolment(enrol) : "";
        var createMarkup = enrol ? "" : (field("New profile label", input("agentSecurity.createLabel", { placeholder: "e.g. Persona login" }))
            + "<div class=\"ayu-inline-actions\">" + button("Create 2FA profile", "agent-2fa-create", "primary", "plus") + "</div>");
        var websiteOptions = assignableAgentWebsites();
        var profiles = sec.profiles || [];
        var listMarkup = profiles.length
            ? "<div class=\"ayu-list\">" + profiles.map(function (p) {
                var pid = getByPath(p, "profile_id", "");
                var verified = Boolean(getByPath(p, "last_verified_at", null));
                return "<div class=\"ayu-list-row\"><div class=\"ayu-2fa-profile-card\">"
                    + "<div class=\"ayu-2fa-profile-head\"><div class=\"ayu-list-copy\"><strong>" + escapeHtml(getByPath(p, "label", pid)) + "</strong>"
                    + "<small>Created " + escapeHtml(formatTimestamp(getByPath(p, "created_at", null))) + "</small></div>"
                    + badge(verified ? "Verified" : "Not verified yet", verified ? "green" : "amber") + "</div>"
                    + renderAgentSecurityChips(p, websiteOptions)
                    + renderAgentSecurityAssignRow(p, websiteOptions)
                    + "<div class=\"ayu-inline-actions\">" + button("Wipe profile", "agent-2fa-wipe-profile:" + pid, "danger", "trash", "sm") + "</div>"
                    + "</div></div>";
            }).join("") + "</div>"
            : "<div class=\"ayu-empty\">No 2FA profiles yet. Create one, then assign it to an agent website (e.g. persona_agent).</div>";
        return enrolMarkup + createMarkup + listMarkup;
    }

    function renderSecurityScreen() {
        var cfg = bootstrapConfig();
        var securityModes = getByPath(state.bootstrap, "metadata.security_modes", []);
        var jailbreakStatus = state.jailbreak.status || {};
        var selectedSecurityMode = getByPath(state.forms, "security.mode", getByPath(cfg, "security.mode", "secure"));
        var securityHelp = SECURITY_MODE_HELP[selectedSecurityMode] || SECURITY_MODE_HELP.normal;
        return "<div class=\"ayu-screen\"><div class=\"ayu-hero\"><div class=\"ayu-hero-copy\"><h1>Security</h1><p>Security mode, password rotation, authenticator sharing, Automatic Configuration export, and prompt override controls.</p></div></div><div class=\"ayu-grid-2\">" + panel("Security mode", "Switch between Normal, Secure Mode, Secure Professional, and Secure Professional Maximus.", field("Current mode", select("security.mode", securityModes.map(function (mode) { return { value: mode.id, label: mode.label }; })), securityHelp.description) + "<div class=\"ayu-note\">Current mode: <strong>" + escapeHtml(prettyLabel(getByPath(cfg, "security.mode", "secure"))) + "</strong></div><div class=\"ayu-inline-actions\">" + button("Save security mode", "security-save-mode", "primary", "save") + "</div>" + field("Pairing security tier", select("security.tier", SECURITY_TIER_OPTIONS), SECURITY_TIER_HELP[normalizeSecurityTier(getByPath(state.forms, "security.tier", getByPath(cfg, "security.tier", "B")))]) + "<div class=\"ayu-note\">Current tier: <strong>" + escapeHtml(savedSecurityTier() === "A" ? "Enhanced Pairing" : "Quick Pairing") + "</strong></div><div class=\"ayu-inline-actions\">" + button("Save pairing tier", "security-save-tier", "primary", "save") + "</div>") + panel("Password", "Rotate the admin server password used by pairing and secure-mode flows.", renderPasswordManagementBody()) + "</div><div class=\"ayu-grid-2\">" + panel("Authenticator QR", "Share the shared pairing authenticator setup as a QR or manual import.", renderTotpManagementBody()) + panel("Automatic Configuration Setup", "Create a client setup QR for supported Android and iOS flows.", renderZeroTouchPairingBody()) + "</div>" + panel("Prompt Override", "Advanced root prompt override management for locked runtimes.", "<div class=\"ayu-note ayu-note-" + escapeHtml(getByPath(jailbreakStatus, "active", false) ? "red" : "amber") + "\">" + escapeHtml(getByPath(jailbreakStatus, "active", false) ? "Prompt Override is active. Prompt overrides can change root behavior immediately after restart." : "Prompt Override is inactive.") + "</div>" + field("Override prompt", textarea("agentWorkbench.jailbreak_prompt", { rows: 10, extraClass: "ayu-mono" }), "This prompt only applies when Prompt Override is active.") + "<div class=\"ayu-inline-actions\">" + button("Activate", "jailbreak-activate", "danger", "bolt") + button("Deactivate", "jailbreak-deactivate", "secondary", "stop") + button("Save prompt", "jailbreak-save", "primary", "save") + "</div>") + panel("Per-Agent 2FA Profiles", "Issue independent 2FA profiles (derive-from-password, no stored secret, no 'show secret') and assign them to individual agent websites such as persona_agent. Recovery is by wipe + re-create.", renderAgentSecurityProfilesBody()) + "</div>";
    }

    function bootSweepBoardEl() {
        return document.getElementById("ayu-boot-sweep-board");
    }

    function normalizeBootSweepRun(entry) {
        return {
            id: Number(getByPath(entry, "id", 0)) || 0,
            score: Math.max(0, Math.round(Number(getByPath(entry, "score", 0)) || 0)),
            clicks: Math.max(0, Math.round(Number(getByPath(entry, "clicks", 0)) || 0)),
            seconds: Math.max(0, Number(getByPath(entry, "seconds", 0)) || 0),
            recorded_at: String(getByPath(entry, "recorded_at", "") || "")
        };
    }

    function updateBootSweepScore() {
        var scoreEl = document.getElementById("ayu-boot-sweep-score");
        if (scoreEl) {
            scoreEl.textContent = String(bootSweepState.score || 0);
        }
    }

    function updateBootSweepBest() {
        var bestEl = document.getElementById("ayu-boot-sweep-best");
        if (bestEl) {
            bestEl.textContent = bootSweepState.bestScore > 0 ? String(bootSweepState.bestScore) : "0";
        }
    }

    function setBootSweepStatus(text) {
        var statusEl = document.getElementById("ayu-boot-sweep-status");
        if (statusEl) {
            statusEl.textContent = text || "";
        }
    }

    function setBootSweepResetStatus(text) {
        var resetEl = document.getElementById("ayu-boot-sweep-reset-status");
        if (!resetEl) {
            return;
        }
        resetEl.textContent = text || "";
        if (!text) {
            return;
        }
        window.setTimeout(function () {
            if (resetEl.textContent === text) {
                resetEl.textContent = "";
            }
        }, 2600);
    }

    function syncBootSweepControls() {
        var startBtn = document.querySelector('[data-action="boot-sweep-start"]');
        var stopBtn = document.querySelector('[data-action="boot-sweep-stop"]');
        if (startBtn) {
            startBtn.disabled = Boolean(bootSweepState.running);
        }
        if (stopBtn) {
            stopBtn.disabled = !bootSweepState.running;
        }
    }

    function renderBootSweepHistory() {
        var body = document.getElementById("ayu-boot-sweep-history-body");
        if (!body) {
            return;
        }
        if (!Array.isArray(bootSweepState.highScores) || !bootSweepState.highScores.length) {
            body.innerHTML = "<tr class=\"ayu-boot-sweep-empty\"><td colspan=\"4\">No saved high scores yet.</td></tr>";
            return;
        }
        body.innerHTML = bootSweepState.highScores.map(function (entry) {
            var when = getByPath(entry, "recorded_at", "");
            var parsed = when ? new Date(when) : null;
            var label = parsed && !isNaN(parsed.getTime()) ? parsed.toLocaleString() : (when || "Unknown");
            return "<tr><td>" + escapeHtml(String(getByPath(entry, "score", 0))) + "</td><td>" + escapeHtml(label) + "</td><td>" + escapeHtml(String(getByPath(entry, "clicks", 0))) + "</td><td>" + escapeHtml((Number(getByPath(entry, "seconds", 0)) || 0).toFixed(1)) + "</td></tr>";
        }).join("");
    }

    async function ensureBootSweepScores(force) {
        if (!force && bootSweepState.bestLoaded) {
            updateBootSweepBest();
            renderBootSweepHistory();
            return bootSweepState.bestScore;
        }
        try {
            var payload = await requestJson("/api/login-minigame/high-score");
            bootSweepState.highScores = Array.isArray(payload.high_scores) ? payload.high_scores.map(normalizeBootSweepRun) : [];
            bootSweepState.bestScore = Math.max(0, Number(payload.high_score) || 0, bootSweepState.highScores.length ? Number(bootSweepState.highScores[0].score) || 0 : 0);
            bootSweepState.bestLoaded = true;
        } catch (error) {
            bootSweepState.highScores = bootSweepState.highScores || [];
            bootSweepState.bestScore = bootSweepState.bestScore || 0;
        }
        updateBootSweepBest();
        renderBootSweepHistory();
        return bootSweepState.bestScore;
    }

    async function persistBootSweepRun(scoreValue, clickCount, elapsedSeconds) {
        try {
            var payload = await postJson("/api/login-minigame/high-score", {
                score: scoreValue,
                clicks: clickCount,
                seconds: Number(elapsedSeconds || 0)
            });
            bootSweepState.highScores = Array.isArray(payload.high_scores) ? payload.high_scores.map(normalizeBootSweepRun) : bootSweepState.highScores;
            bootSweepState.bestScore = Math.max(scoreValue, Number(payload.high_score) || 0);
            bootSweepState.bestLoaded = true;
        } catch (error) {
            // Score persistence is auxiliary UI state only.
        }
        updateBootSweepBest();
        renderBootSweepHistory();
        return bootSweepState.bestScore;
    }

    function removeBootSweepNode(node) {
        if (node && node.parentNode) {
            node.parentNode.removeChild(node);
        }
    }

    function spawnBootSweepNode() {
        var board = bootSweepBoardEl();
        if (!board || !bootSweepState.running) {
            return;
        }
        var existing = Array.from(board.querySelectorAll(".ayu-boot-sweep-node")).map(function (node) {
            return {
                x: parseFloat(node.style.left),
                y: parseFloat(node.style.top)
            };
        });
        var left;
        var top;
        var attempt = 0;
        do {
            left = 8 + Math.random() * 82;
            top = 8 + Math.random() * 76;
            attempt += 1;
        } while (attempt < 12 && existing.some(function (entry) {
            return Math.abs(entry.x - left) < 14 && Math.abs(entry.y - top) < 18;
        }));
        var node = document.createElement("button");
        node.type = "button";
        node.className = "ayu-boot-sweep-node";
        node.style.left = left + "%";
        node.style.top = top + "%";
        node.title = "Catch signal ping";
        var hitNode = function (event) {
            if (event) {
                event.preventDefault();
                if (typeof event.stopPropagation === "function") {
                    event.stopPropagation();
                }
            }
            bootSweepState.score += 1;
            updateBootSweepScore();
            removeBootSweepNode(node);
            spawnBootSweepNode();
            spawnBootSweepNode();
        };
        node.addEventListener("click", hitNode);
        node.addEventListener("touchend", hitNode, { passive: false });
        board.appendChild(node);
        window.setTimeout(function () {
            removeBootSweepNode(node);
        }, 2200 + Math.random() * 1600);
    }

    async function startBootSweep() {
        var board = bootSweepBoardEl();
        if (!board || bootSweepState.running) {
            return;
        }
        board.innerHTML = "";
        bootSweepState.score = 0;
        bootSweepState.rampTick = 0;
        bootSweepState.running = true;
        bootSweepState.startTime = Date.now();
        updateBootSweepScore();
        updateBootSweepBest();
        setBootSweepStatus("Running - click the pings.");
        syncBootSweepControls();
        for (var index = 0; index < 5; index += 1) {
            window.setTimeout(spawnBootSweepNode, index * 160);
        }
        bootSweepState.timer = window.setInterval(function () {
            var liveBoard = bootSweepBoardEl();
            if (!liveBoard || !bootSweepState.running) {
                return;
            }
            bootSweepState.rampTick += 1;
            var maxNodes = bootSweepState.rampTick < 6 ? 6 + bootSweepState.rampTick : 14;
            var deficit = maxNodes - liveBoard.childElementCount;
            for (var idx = 0; idx < Math.min(deficit, 3); idx += 1) {
                spawnBootSweepNode();
            }
        }, 400);
    }

    async function stopBootSweep(options) {
        var settings = options || {};
        var shouldReport = settings.report !== false;
        if (!bootSweepState.running && !bootSweepState.timer && !bootSweepState.startTime) {
            syncBootSweepControls();
            return 0;
        }
        bootSweepState.running = false;
        if (bootSweepState.timer) {
            window.clearInterval(bootSweepState.timer);
            bootSweepState.timer = null;
        }
        var board = bootSweepBoardEl();
        if (board) {
            board.innerHTML = "";
        }
        syncBootSweepControls();
        if (bootSweepState.startTime > 0 && bootSweepState.score > 0) {
            var elapsedSeconds = Math.max(1, (Date.now() - bootSweepState.startTime) / 1000);
            var finalScore = Math.round((bootSweepState.score / elapsedSeconds) * 100);
            var clickCount = bootSweepState.score;
            bootSweepState.startTime = 0;
            if (shouldReport) {
                setBootSweepStatus("Score: " + finalScore + " (" + clickCount + " clicks in " + elapsedSeconds.toFixed(1) + "s)");
            }
            if (finalScore > 0) {
                await persistBootSweepRun(finalScore, clickCount, Number(elapsedSeconds.toFixed(3)));
            }
            return finalScore;
        }
        bootSweepState.startTime = 0;
        if (shouldReport) {
            setBootSweepStatus("Stopped.");
        }
        return 0;
    }

    async function resetBootSweepBest() {
        try {
            await requestJson("/api/login-minigame/high-score", { method: "DELETE" });
            bootSweepState.bestScore = 0;
            bootSweepState.highScores = [];
            bootSweepState.bestLoaded = true;
            updateBootSweepBest();
            renderBootSweepHistory();
            setBootSweepResetStatus("Score reset.");
        } catch (error) {
            setBootSweepResetStatus("Failed to reset score.");
        }
    }

    function syncBootSweepUi() {
        if (state.screen !== "guides") {
            return;
        }
        bootSweepState.mounted = true;
        updateBootSweepScore();
        updateBootSweepBest();
        renderBootSweepHistory();
        syncBootSweepControls();
        if (bootSweepState.running && bootSweepBoardEl() && bootSweepBoardEl().childElementCount === 0) {
            for (var index = 0; index < 3; index += 1) {
                window.setTimeout(spawnBootSweepNode, index * 120);
            }
        }
    }

    function guideCatalog() {
        var list = getByPath(state.bootstrap, "metadata.guides", []);
        return Array.isArray(list) ? list : [];
    }

    async function ensureGuideContent(id, force, renderCached) {
        if (!id) {
            return;
        }
        if (!force && state.guides.byId[id]) {
            if (renderCached) {
                renderApp();
            }
            return;
        }
        state.guides.loading = true;
        state.guides.loadingId = id;
        renderApp();
        try {
            var doc = await requestJson("/admin/guides/content?id=" + encodeURIComponent(id));
            state.guides.byId[id] = doc;
        } catch (error) {
            state.guides.byId[id] = {
                id: id,
                title: "Guide unavailable",
                subtitle: "",
                html: "<div class=\"ayu-empty\">This guide could not be loaded. " + escapeHtml(error.message || String(error)) + "</div>"
            };
        } finally {
            if (state.guides.loadingId === id) {
                state.guides.loading = false;
                state.guides.loadingId = "";
                renderApp();
            }
        }
    }

    function ensureDefaultGuide() {
        var catalog = guideCatalog();
        if (!catalog.length) {
            return;
        }
        var current = state.guides.selectedId;
        var exists = catalog.some(function (g) { return g.id === current; });
        if (!current || !exists) {
            state.guides.selectedId = catalog[0].id;
        }
        ensureGuideContent(state.guides.selectedId, false);
    }

    function renderGuideReader() {
        var catalog = guideCatalog();
        if (!catalog.length) {
            return "<div class=\"ayu-empty\">No guides were returned by the server.</div>";
        }
        var selectedId = state.guides.selectedId || catalog[0].id;
        var order = [];
        var groups = {};
        catalog.forEach(function (g) {
            var cat = g.category || "Guides";
            if (!groups[cat]) {
                groups[cat] = [];
                order.push(cat);
            }
            groups[cat].push(g);
        });
        var toc = order.map(function (cat) {
            return "<div class=\"ayu-guide-toc-group\"><div class=\"ayu-kicker\">" + escapeHtml(cat) + "</div>" + groups[cat].map(function (g) {
                return "<button type=\"button\" class=\"ayu-guide-toc-item" + (g.id === selectedId ? " active" : "") + "\" data-action=\"guide-open:" + escapeHtml(g.id) + "\"><strong>" + escapeHtml(g.title) + "</strong><span>" + escapeHtml(g.description || "") + "</span></button>";
            }).join("") + "</div>";
        }).join("");
        var selectedEntry = catalog.filter(function (g) { return g.id === selectedId; })[0] || catalog[0];
        var doc = state.guides.byId[selectedId];
        var content;
        if (state.guides.loading && state.guides.loadingId === selectedId && !doc) {
            content = "<div class=\"ayu-empty\">Loading guide…</div>";
        } else if (doc) {
            content = "<div class=\"ayu-guide-content-head\"><div class=\"ayu-list-copy\"><strong>" + escapeHtml(doc.title) + "</strong><p>" + escapeHtml(doc.subtitle || "") + "</p></div><a class=\"ayu-link-btn ayu-btn ayu-btn-ghost ayu-btn-sm\" href=\"" + escapeHtml(selectedEntry.href) + "\" target=\"_blank\" rel=\"noreferrer\">" + icon("external") + "<span>Open in new tab</span></a></div><div class=\"ayu-guide-doc\">" + doc.html + "</div>";
        } else {
            content = "<div class=\"ayu-empty\">Select a topic to read it here.</div>";
        }
        return "<div id=\"ayu-guide-reader\" class=\"ayu-scroll-target ayu-guide-reader\"><aside class=\"ayu-guide-toc\">" + toc + "</aside><div id=\"ayu-guide-content-anchor\" class=\"ayu-guide-content ayu-scroll-target\">" + content + "</div></div>";
    }

    function renderGuidesScreen() {
        var runtime = getByPath(state.bootstrap, "status.runtime", {});
        var bootSweepMarkup = "<div class=\"ayu-note ayu-note-green\">Catch as many signal pings as you can before the board overwhelms you. Score is clicks per second multiplied by 100.</div><div class=\"ayu-boot-sweep-stats\"><div><strong>Score</strong><div id=\"ayu-boot-sweep-score\" class=\"ayu-boot-sweep-number\">" + escapeHtml(String(bootSweepState.score || 0)) + "</div></div><div><strong>Best</strong><div id=\"ayu-boot-sweep-best\" class=\"ayu-boot-sweep-number\">" + escapeHtml(String(bootSweepState.bestScore || 0)) + "</div></div></div><div id=\"ayu-boot-sweep-board\" class=\"ayu-boot-sweep-board\" aria-label=\"Boot Sweep mini game\"></div><div class=\"ayu-inline-actions\">" + button("Start run", "boot-sweep-start", "primary", "play") + button("Stop run", "boot-sweep-stop", "secondary", "stop") + button("Reset best", "boot-sweep-reset", "ghost", "trash") + "</div><div id=\"ayu-boot-sweep-status\" class=\"ayu-note ayu-note-gray\">Waiting to start.</div><div id=\"ayu-boot-sweep-reset-status\" class=\"ayu-hint\"></div><div class=\"ayu-soft-divider\"></div><table class=\"ayu-boot-sweep-table\"><thead><tr><th>Score</th><th>Recorded</th><th>Clicks</th><th>Seconds</th></tr></thead><tbody id=\"ayu-boot-sweep-history-body\"><tr class=\"ayu-boot-sweep-empty\"><td colspan=\"4\">No saved high scores yet.</td></tr></tbody></table>";
        var publicGuideMarkup = "<div class=\"ayu-list\"><div class=\"ayu-list-row\"><div class=\"ayu-list-copy\"><strong>Community Relay submission</strong><p>Ready-to-submit checklist for coturn, provider-agent, ports, and approval.</p></div><div class=\"ayu-inline-actions\"><a class=\"ayu-link-btn ayu-btn ayu-btn-secondary ayu-btn-sm\" href=\"/guides/doc/community-relay-submission\" target=\"_blank\" rel=\"noreferrer\">" + icon("book") + "<span>Read locally</span></a><a class=\"ayu-link-btn ayu-btn ayu-btn-ghost ayu-btn-sm\" href=\"https://www.autoyou.me/guides/community-relay/\" target=\"_blank\" rel=\"noreferrer\">" + icon("external") + "<span>Open interactive</span></a></div></div><div class=\"ayu-list-row\"><div class=\"ayu-list-copy\"><strong>Home and private network</strong><p>Docker, LAN binding, local HTTPS, private STUN/TURN, and app selection.</p></div><div class=\"ayu-inline-actions\"><a class=\"ayu-link-btn ayu-btn ayu-btn-secondary ayu-btn-sm\" href=\"/guides/doc/home-private-network\" target=\"_blank\" rel=\"noreferrer\">" + icon("book") + "<span>Read locally</span></a><a class=\"ayu-link-btn ayu-btn ayu-btn-ghost ayu-btn-sm\" href=\"https://www.autoyou.me/guides/home-private-network/\" target=\"_blank\" rel=\"noreferrer\">" + icon("external") + "<span>Open interactive</span></a></div></div><div class=\"ayu-list-row\"><div class=\"ayu-list-copy\"><strong>iOS and Android mobile pairing</strong><p>Local, Auto, Telegram, Signal, Bluetooth, and OTP pairing modes.</p></div><div class=\"ayu-inline-actions\"><a class=\"ayu-link-btn ayu-btn ayu-btn-secondary ayu-btn-sm\" href=\"/guides/doc/mobile-pairing\" target=\"_blank\" rel=\"noreferrer\">" + icon("book") + "<span>Read locally</span></a><a class=\"ayu-link-btn ayu-btn ayu-btn-ghost ayu-btn-sm\" href=\"https://www.autoyou.me/guides/mobile-pairing/\" target=\"_blank\" rel=\"noreferrer\">" + icon("external") + "<span>Open interactive</span></a></div></div><div class=\"ayu-list-row\"><div class=\"ayu-list-copy\"><strong>Computer pairing with AutoYou Connect</strong><p>Install AutoYou Connect, pair Chrome over LAN, VPN, Auto Pair, OTP, Cloud, or Bluetooth, then verify Browser and Chat.</p></div><div class=\"ayu-inline-actions\"><a class=\"ayu-link-btn ayu-btn ayu-btn-secondary ayu-btn-sm\" href=\"/guides/doc/chrome-pairing\" target=\"_blank\" rel=\"noreferrer\">" + icon("book") + "<span>Read locally</span></a><a class=\"ayu-link-btn ayu-btn ayu-btn-ghost ayu-btn-sm\" href=\"https://www.autoyou.me/guides/computer-pairing/\" target=\"_blank\" rel=\"noreferrer\">" + icon("external") + "<span>Open interactive</span></a></div></div></div>";
        return "<div class=\"ayu-screen\"><div class=\"ayu-hero\"><div class=\"ayu-hero-copy\"><h1>Help & Guides</h1><p>Built-in documentation for setup, agents, connectivity, security, speech, and troubleshooting - no internet required. The first-run checklist lives in Setup & Boot.</p></div><div class=\"ayu-inline-actions\">" + button("Open setup", "nav:setup", "secondary", "bolt") + button("Open connectivity", "nav:connectivity", "ghost", "wifi") + "</div></div>" + panel("Documentation", "Pick a topic on the left to read it here, or open it in a new tab.", renderGuideReader()) + panel("Public field guides", "Interactive guides for relay contribution, private hosting, and mobile pairing.", publicGuideMarkup) + "<div class=\"ayu-grid-2\">" + panel("Runtime snapshot", "Useful environment details for debugging packaged vs source deployments.", "<pre class=\"ayu-note ayu-note-green ayu-mono\">" + escapeHtml(JSON.stringify(runtime, null, 2)) + "</pre>") + panel("Boot Sweep", "Legacy dashboard minigame, now restored in the shell with the shared score API.", bootSweepMarkup) + "</div></div>";
    }

    function renderInteractScreen() {
        var data = state.interact.payload || {};
        var people = Array.isArray(data.participants) ? data.participants : [];
        var inputs = Array.isArray(data.inputs) ? data.inputs : [];
        var rows = people.map(function (person) {
            var id = String(person.id || "");
            var interactive = person.mode === "interactive";
            var selected = state.interact.selected.indexOf(id) !== -1;
            var recent = Date.now() / 1000 - Number(person.last_input_at || 0) < 1.5 ? badge(person.last_input || "", "blue") : "";
            return "<div class=\"ayu-list-row\"><div class=\"ayu-list-copy\"><strong>" + escapeHtml(person.name || "Connected device") + "</strong><p>" + escapeHtml(interactive ? (person.muted ? "Microphone muted" : "Microphone live") : "Watching only") + "</p></div>" + recent + (interactive ? button(selected ? "Selected" : "Select", "interact-select:" + encodeURIComponent(id), selected ? "primary" : "secondary", selected ? "check" : "plus", "sm", 'aria-pressed="' + (selected ? "true" : "false") + '"') : "") + "</div>";
        }).join("");
        var feed = inputs.slice(-30).reverse().map(function (input) {
            return "<div class=\"ayu-list-row\"><div class=\"ayu-list-copy\"><strong>" + escapeHtml(input.name || "A device") + " sent " + escapeHtml(input.value || "an input") + "</strong></div></div>";
        }).join("");
        return "<div class=\"ayu-screen\"><div class=\"ayu-hero\"><div class=\"ayu-hero-copy\"><h1>Interact</h1><p>See live screen participants and their choice or controller inputs. Listen plays unmuted phone microphones on this computer, without AI processing.</p></div>" + button("Refresh", "interact-refresh", "secondary", "refresh") + "</div>" + (state.interact.error ? "<div class=\"ayu-note ayu-note-red\">" + escapeHtml(state.interact.error) + "</div>" : "") + "<div class=\"ayu-grid-2\">" + panel("Listen", "Local speaker output: " + (data.mode || "off"), "<div class=\"ayu-inline-actions\">" + button("Off", "interact-save:off", "secondary") + button("All unmuted", "interact-save:all", "secondary") + button("Selected", "interact-save:selected", "primary") + "</div><p class=\"ayu-hint\">Select participants below before choosing Selected. Phone microphones start muted.</p>" + (rows || "<div class=\"ayu-empty\">No connected screen sessions.</div>")) + panel("Inputs", "Recent A–D and controller presses", "<div class=\"ayu-list\">" + (feed || "<div class=\"ayu-empty\">No inputs yet.</div>") + "</div>") + "</div></div>";
    }

    function renderCurrentScreen() {
        var markup = "";
        if (state.screen === "overview") {
            markup = renderOverview();
        } else if (state.screen === "chat") {
            markup = renderChatHistoryScreen();
        } else if (state.screen === "live") {
            markup = renderLiveViewScreen();
        } else if (state.screen === "interact") {
            markup = renderInteractScreen();
        } else if (state.screen === "setup") {
            markup = renderSetupScreen();
        } else if (state.screen === "ai") {
            markup = renderAiScreen();
        } else if (state.screen === "agents") {
            markup = renderAgentScreen();
        } else if (state.screen === "page") {
            markup = renderPageScreen();
        } else if (state.screen === "messaging") {
            markup = renderMessagingScreen();
        } else if (state.screen === "video") {
            markup = renderVideoScreen();
        } else if (state.screen === "permissions") {
            markup = renderPermissionsScreen();
        } else if (state.screen === "speech") {
            markup = renderSpeechScreen();
        } else if (state.screen === "connectivity") {
            markup = renderConnectivityScreen();
        } else if (state.screen === "security") {
            markup = renderSecurityScreen();
        } else {
            markup = renderGuidesScreen();
        }
        return injectScreenBanner(markup, renderSetupLinkedBanner(state.screen));
    }

    function renderNotice() {
        if (!state.notice) {
            return "";
        }
        return "<div class=\"ayu-toast " + escapeHtml(state.notice.kind || "info") + "\">" + escapeHtml(state.notice.message || "") + "</div>";
    }

    function renderModal() {
        if (state.profileCropper) {
            return renderProfileCropperModal();
        }
        if (!state.modal) {
            return "";
        }
        var imageMarkup = state.modal.imageUrl ? "<div class=\"ayu-modal-qr\"><img src=\"" + escapeHtml(state.modal.imageUrl) + "\" alt=\"QR code\"></div>" : "";
        var htmlMarkup = state.modal.html ? "<div class=\"ayu-modal-html\">" + state.modal.html + "</div>" : "";
        var textMarkup = state.modal.text ? "<pre class=\"ayu-note ayu-note-green ayu-mono\">" + escapeHtml(state.modal.text) + "</pre>" : "";
        var helperMarkup = state.modal.helper ? "<div class=\"ayu-hint\">" + escapeHtml(state.modal.helper) + "</div>" : "";
        var refreshButtonMarkup = isPairingQrModal(state.modal) ? "<div class=\"ayu-inline-actions\">" + button("Refresh QR code", "refresh-pairing-qr", "ghost", "refresh", "sm") + "</div>" : "";
        return "<div class=\"ayu-modal\" role=\"presentation\"><button type=\"button\" class=\"ayu-modal-dismiss\" data-action=\"close-modal\" aria-label=\"Close dialog\"></button><div class=\"ayu-modal-card\" role=\"dialog\" aria-modal=\"true\" aria-label=\"" + escapeHtml(state.modal.title || "Details") + "\"><div class=\"ayu-modal-head\"><div><h2>" + escapeHtml(state.modal.title || "Details") + "</h2>" + (state.modal.description ? "<p>" + escapeHtml(state.modal.description) + "</p>" : "") + "</div>" + button("Close", "close-modal", "secondary", "close", "sm") + "</div>" + imageMarkup + htmlMarkup + textMarkup + helperMarkup + refreshButtonMarkup + (state.modal.copyValue ? "<div class=\"ayu-inline-actions\">" + button("Copy value", "modal-copy", "primary", "copy") + "</div>" : "") + "</div></div>";
    }

    function renderSecureStorageAlert() {
        // Surfaced on every screen: protected stores left sealed by a Secure
        // Professional Maximus downgrade read closed everywhere until the
        // services that hold them are restarted, so a Security-page-only notice
        // would be missed by a user staring at a broken agent somewhere else.
        var recovery = getByPath(state.bootstrap, "status.secure_storage_recovery", {});
        var recovered = Number(getByPath(recovery, "recovered", 0)) || 0;
        var unrecoverable = Number(getByPath(recovery, "unrecoverable", 0)) || 0;
        if (recovered < 1 && unrecoverable < 1) {
            return "";
        }
        var names = getByPath(recovery, "unrecoverable_names", []) || [];
        var tone = unrecoverable > 0 ? "red" : "amber";
        var heading = unrecoverable > 0
            ? "Protected data stores cannot be unsealed"
            : "Restart required to finish unsealing protected data";
        var body = escapeHtml(String(getByPath(recovery, "message", "")));
        var fileList = names.length
            ? "<ul class=\"ayu-storage-alert-files\">" + names.map(function (name) {
                return "<li><code>" + escapeHtml(String(name)) + "</code></li>";
            }).join("") + "</ul>"
            : "";
        var shutdownAction = "<form method=\"post\" action=\"/shutdown\">"
            + "<button class=\"ayu-form-btn ayu-btn ayu-btn-danger ayu-btn-sm\" type=\"submit\">"
            + icon("power") + "<span>Shutdown Server</span></button></form>";
        return "<div class=\"ayu-storage-alert ayu-note ayu-note-" + escapeHtml(tone) + "\" role=\"alert\">"
            + "<strong>" + escapeHtml(heading) + "</strong>"
            + "<p>" + body + "</p>"
            + fileList
            + "<div class=\"ayu-inline-actions ayu-storage-alert-actions\">" + shutdownAction + "</div>"
            + "</div>";
    }

    function renderApp(options) {
        options = options || {};
        if (!root) {
            return;
        }
        if (options.passive && !options.force && hasActiveAdminControl()) {
            renderState.passiveQueued = true;
            return;
        }
        renderState.passiveQueued = false;
        var activeSnapshot = captureActiveControl();
        if (!state.bootstrap) {
            root.innerHTML = "<div class=\"ayu-screen-loading\"><div class=\"ayu-loading-card\"><div class=\"ayu-spinner\"></div><h1>Loading admin shell</h1><p>Fetching the live bootstrap snapshot from the server.</p></div></div>";
            return;
        }
        root.innerHTML = "<div class=\"ayu-shell" + (state.navOpen ? " nav-open" : "") + "\" data-screen=\"" + escapeHtml(state.screen) + "\">" + renderSidebar() + "<div class=\"ayu-backdrop\" data-action=\"close-nav\"></div><div class=\"ayu-main-wrap\">" + renderMobileBar() + "<main class=\"ayu-main\">" + renderSecureStorageAlert() + renderCurrentScreen() + "</main></div></div>" + renderNotice() + renderModal();
        root.querySelectorAll('input[type="file"]').forEach(function (input) {
            input.tabIndex = -1;
            input.setAttribute("aria-hidden", "true");
        });
        restoreActiveControl(activeSnapshot);
        syncBootSweepUi();
        syncCropper();
        syncTelegramSenderPolling();
        if (state.screen === "guides") {
            ensureBootSweepScores(false).catch(function () {});
        }
    }

    async function handleServiceAction(action) {
        if (action === "service:ai:start") {
            await requestJson("/ai-agent-server/start", { method: "POST" });
            state.agentWorkbench.aiRestartRequired = false;
            state.agentWorkbench.aiRestartMessage = "";
            return refreshBootstrap("AI agent runtime started.");
        }
        if (action === "service:ai:stop") {
            await requestJson("/ai-agent-server/stop", { method: "POST" });
            return refreshBootstrap("AI agent runtime stopped.");
        }
        if (action === "service:ai:restart") {
            await requestJson("/ai-agent-server/restart", { method: "POST" });
            state.agentWorkbench.aiRestartRequired = false;
            state.agentWorkbench.aiRestartMessage = "";
            return refreshBootstrap("AI agent runtime restarted.");
        }
        if (action === "service:page:start") {
            await requestJson("/autoyou-page-service/start", { method: "POST" });
            return refreshBootstrap("Websites & Browser started.");
        }
        if (action === "service:page:stop") {
            await requestJson("/autoyou-page-service/stop", { method: "POST" });
            return refreshBootstrap("Websites & Browser stopped.");
        }
        if (action === "service:page:restart") {
            await requestJson("/autoyou-page-service/restart", { method: "POST" });
            return refreshBootstrap("Websites & Browser restarted.");
        }
        if (action === "service:tunnelmole:start") {
            await requestJson("/tunnelmole/start", { method: "POST" });
            return refreshBootstrap("Public link started.");
        }
        if (action === "service:tunnelmole:stop") {
            await requestJson("/tunnelmole/stop", { method: "POST" });
            return refreshBootstrap("Public link stopped.");
        }
        if (action === "service:tunnelmole:refresh") {
            await requestJson("/tunnelmole/refresh", { method: "POST" });
            return refreshBootstrap("Public link status refreshed.");
        }
    }

    function aiProviderPayload() {
        var source = getByPath(state.forms, "aiProvider", {});
        var payload = {
            ai_provider: {
                provider: source.provider,
                openclaw_port: source.openclaw_port,
                openclaw_model: source.openclaw_model,
                openclaw_agent_port: source.openclaw_agent_port,
                openclaw_agent_model: source.openclaw_agent_model,
                hermes_port: source.hermes_port,
                hermes_model: source.hermes_model,
                litellm_model: source.litellm_model,
                litellm_api_base: source.litellm_api_base,
                odysseus_api_base: source.odysseus_api_base,
                odysseus_model: source.odysseus_model
            },
            ollama: {
                enabled: source.ollama_enabled,
                api_base: source.ollama_api_base,
                model: source.ollama_model,
                use_google_api: source.provider === "google",
                google_model: source.google_model
            }
        };
        if (hasValue(source.openclaw_token)) {
            payload.ai_provider.openclaw_token = source.openclaw_token;
        }
        if (hasValue(source.openclaw_agent_token)) {
            payload.ai_provider.openclaw_agent_token = source.openclaw_agent_token;
        }
        if (hasValue(source.hermes_token)) {
            payload.ai_provider.hermes_token = source.hermes_token;
        }
        if (hasValue(source.litellm_api_key)) {
            payload.ai_provider.litellm_api_key = source.litellm_api_key;
        }
        if (hasValue(source.odysseus_token)) {
            payload.ai_provider.odysseus_token = source.odysseus_token;
        }
        if (hasValue(source.google_api_key)) {
            payload.ollama.google_api_key = source.google_api_key;
        }
        return payload;
    }

    function speechPayload() {
        var source = getByPath(state.forms, "speech", {});
        var payload = {
            speech: {
                tts: {
                    provider: source.tts_provider,
                    rate: source.tts_rate,
                    system_voice: source.system_voice,
                    openai: {
                        base_url: source.openai_base_url,
                        model: source.openai_model,
                        voice: source.openai_voice,
                        instructions: source.openai_instructions
                    },
                    azure: {
                        speech_region: source.azure_speech_region,
                        voice: source.azure_voice,
                        endpoint_id: source.azure_endpoint_id
                    },
                    emotivoice: {
                        speaker: source.emotivoice_speaker,
                        conversation_emotion: asBoolean(source.emotivoice_conversation_emotion, true)
                    }
                },
                stt: {
                    model: source.stt_model,
                    language: source.stt_language,
                    device: source.stt_device,
                    compute_type: source.stt_compute_type,
                    silero_sensitivity: source.stt_silero_sensitivity,
                    post_speech_silence_duration: source.stt_post_speech_silence_duration
                },
            }
        };
        if (hasValue(source.openai_api_key)) {
            payload.speech.tts.openai.api_key = source.openai_api_key;
        }
        if (hasValue(source.azure_speech_key)) {
            payload.speech.tts.azure.speech_key = source.azure_speech_key;
        }
        return payload;
    }

    function videoCallFormPayload() {
        var source = getByPath(state.forms, "videoCall", {});
        var payload = JSON.parse(JSON.stringify(source || {}));
        [
            "enabled", "audio_enabled", "ai_audio_replies_enabled", "disable_autoyou_agents",
            "background_mode_enabled", "silent_recording_enabled", "record_audio_only_calls", "location_recording_enabled",
            "wuift_enabled", "record_my_video", "audio_sources", "capture_audio",
            "outbound_sources", "outbound_source", "audio_microphone", "audio_speaker_loopback",
            "outbound_remote_desktop", "outbound_api", "outbound_video_file", "outbound_camera"
        ].forEach(function (key) { delete payload[key]; });
        if (payload.remote_desktop && typeof payload.remote_desktop === "object") {
            ["enabled", "send_screen", "control_enabled", "game_enabled"].forEach(function (key) {
                delete payload.remote_desktop[key];
            });
        }
        return payload;
    }

    async function savePermissions(fields, successMessage) {
        var formPaths = {
            video_call_enabled: "videoCall.enabled",
            audio_call_enabled: "videoCall.audio_enabled",
            audio_playback_enabled: "audioPlayback.enabled",
            computer_microphone: "videoCall.audio_microphone",
            computer_sound: "videoCall.audio_speaker_loopback",
            ai_audio_replies_enabled: "videoCall.ai_audio_replies_enabled",
            autoyou_agents_disabled: "videoCall.disable_autoyou_agents",
            voice_call_recording_enabled: "speech.voice_training_capture_enabled",
            background_mode_enabled: "videoCall.background_mode_enabled",
            safety_recording_enabled: "videoCall.silent_recording_enabled",
            audio_only_call_recording_enabled: "videoCall.record_audio_only_calls",
            location_recording_enabled: "videoCall.location_recording_enabled",
            wuift_enabled: "videoCall.wuift_enabled",
            video_call_recording_enabled: "videoCall.record_my_video",
            webcam_sharing_enabled: "videoCall.outbound_camera",
            screen_capture_enabled: "videoCall.remote_desktop.enabled",
            screen_send_enabled: "videoCall.remote_desktop.send_screen",
            screen_source_enabled: "videoCall.outbound_remote_desktop",
            api_video_input_enabled: "videoCall.outbound_api",
            video_file_playback_enabled: "videoCall.outbound_video_file",
            remote_desktop_control_enabled: "videoCall.remote_desktop.control_enabled",
            game_mode_enabled: "videoCall.remote_desktop.game_enabled",
            chat_memory_enabled: "aiAgent.record_messages_in_database",
            ai_agent_lan_access_enabled: "aiAgent.lan_access_enabled",
            admin_frontend_enabled: "page.admin_frontend_enabled",
            remote_access_role: "page.remote_access_role"
        };
        var payload = {};
        fields.forEach(function (fieldName) {
            var path = formPaths[fieldName];
            if (!path) return;
            var value = getByPath(state.forms, path, false);
            payload[fieldName] = fieldName === "remote_access_role"
                ? normalizeRemoteAccessRole(value)
                : Boolean(value);
        });
        await postJson("/api/admin/permissions", payload);
        await refreshBootstrap(successMessage);
    }

    async function handleAction(action, element) {
        if (action === "live-pair") { await openLivePairing(); return; }
        if (!action) {
            return;
        }
        if (action.indexOf("nav:") === 0) {
            setScreen(action.split(":")[1]);
            return;
        }
        if (action === "interact-refresh") { await refreshInteract(); return; }
        if (action.indexOf("interact-select:") === 0) {
            var chosen = decodeURIComponent(action.slice("interact-select:".length));
            var connected = ((state.interact.payload || {}).participants || []).some(function (person) {
                return person.mode === "interactive" && person.id === chosen;
            });
            if (!connected) return;
            var selected = state.interact.selected;
            state.interact.selected = selected.indexOf(chosen) === -1
                ? selected.concat([chosen]) : selected.filter(function (id) { return id !== chosen; });
            state.interact.dirty = true;
            renderApp();
            return;
        }
        if (action.indexOf("interact-save:") === 0) {
            var mode = action.slice("interact-save:".length);
            if (["off", "all", "selected"].indexOf(mode) === -1) return;
            state.interact.saving = true;
            try {
                var saved = await postJson("/api/screen-listen", { mode: mode, selected: state.interact.selected });
                state.interact.payload = saved;
                state.interact.selected = (saved.selected || []).slice();
                state.interact.dirty = false;
                state.interact.error = "";
                renderApp();
            } finally { state.interact.saving = false; }
            return;
        }
        if (action === "open-prompt-builder") {
            var promptBuilderTarget = promptBuilderLaunchTarget();
            if (!promptBuilderTarget.url) {
                setNotice("error", promptBuilderTarget.message);
                return;
            }
            window.open(promptBuilderTarget.url, "_blank", "noopener,noreferrer");
            return;
        }
        if (action === "toggle-nav") {
            state.navOpen = !state.navOpen;
            renderApp();
            return;
        }
        if (action === "close-nav") {
            state.navOpen = false;
            renderApp();
            return;
        }
        if (action === "close-modal") {
            setModal(null);
            return;
        }
        if (action === "toggle-profile-menu") {
            state.profileMenuOpen = !state.profileMenuOpen;
            renderApp();
            return;
        }
        if (action === "profile-image-select") {
            var profileInput = root ? root.querySelector('[data-role="profile-image-input"]') : null;
            if (profileInput) {
                profileInput.click();
            }
            return;
        }
        if (action === "cropper-cancel") {
            state.profileCropper = null;
            renderApp();
            return;
        }
        if (action === "cropper-choose-other") {
            var profileInput = root ? root.querySelector('[data-role="profile-image-input"]') : null;
            if (profileInput) {
                profileInput.click();
            }
            return;
        }
        if (action === "cropper-save") {
            saveCroppedProfileImage();
            return;
        }
        if (action === "cropper-center") {
            if (state.profileCropper) {
                state.profileCropper.offsetX = 0;
                state.profileCropper.offsetY = 0;
                drawCropperCanvas();
            }
            return;
        }
        if (action === "cropper-reset") {
            if (state.profileCropper) {
                state.profileCropper.zoom = 1.0;
                state.profileCropper.offsetX = 0;
                state.profileCropper.offsetY = 0;
                updateCropperZoomUi();
                drawCropperCanvas();
            }
            return;
        }
        if (action === "cropper-zoom-in") {
            if (state.profileCropper) {
                state.profileCropper.zoom = Math.min(state.profileCropper.maxZoom, Number((state.profileCropper.zoom + 0.15).toFixed(2)));
                updateCropperZoomUi();
                drawCropperCanvas();
            }
            return;
        }
        if (action === "cropper-zoom-out") {
            if (state.profileCropper) {
                state.profileCropper.zoom = Math.max(state.profileCropper.minZoom, Number((state.profileCropper.zoom - 0.15).toFixed(2)));
                updateCropperZoomUi();
                drawCropperCanvas();
            }
            return;
        }
        if (action === "cropper-nudge-up") {
            if (state.profileCropper) {
                state.profileCropper.offsetY -= 14;
                drawCropperCanvas();
            }
            return;
        }
        if (action === "cropper-nudge-down") {
            if (state.profileCropper) {
                state.profileCropper.offsetY += 14;
                drawCropperCanvas();
            }
            return;
        }
        if (action === "cropper-nudge-left") {
            if (state.profileCropper) {
                state.profileCropper.offsetX -= 14;
                drawCropperCanvas();
            }
            return;
        }
        if (action === "cropper-nudge-right") {
            if (state.profileCropper) {
                state.profileCropper.offsetX += 14;
                drawCropperCanvas();
            }
            return;
        }
        if (action === "video-file-select") {
            var videoInput = root ? root.querySelector('[data-role="video-file-input"]') : null;
            if (videoInput) {
                videoInput.click();
            }
            return;
        }
        if (action === "chat-new") {
            chatNewConversation();
            return;
        }
        if (action === "chat-refresh") {
            await ensureChatData(true);
            return;
        }
        if (action === "chat-file-select") {
            var chatFileInput = root ? root.querySelector('[data-role="chat-file-input"]') : null;
            if (chatFileInput) chatFileInput.click();
            return;
        }
        if (action === "chat-record-toggle") {
            await chatToggleRecording();
            return;
        }
        if (action === "chat-call-toggle") {
            if (state.chat.call.phase === "idle") {
                await startAdminCall();
            } else {
                await endAdminCall(true);
            }
            return;
        }
        if (action === "chat-call-mute") {
            var activeCall = state.chat.call;
            if (activeCall.phase !== "idle") {
                activeCall.muted = !activeCall.muted;
                if (activeCall.localStream) {
                    activeCall.localStream.getAudioTracks().forEach(function (track) { track.enabled = !activeCall.muted; });
                }
                chatSendCallControl({ muted: activeCall.muted });
                renderApp({ passive: true });
            }
            return;
        }
        if (action === "chat-call-stop") {
            chatSendCallControl({ event: "stop_tts" });
            return;
        }
        if (action === "chat-call-end") {
            await endAdminCall(true);
            return;
        }
        if (action === "chat-send") {
            await sendChatTurn();
            return;
        }
        if (action === "chat-reply-device") {
            await sendChatDeviceReply();
            return;
        }
        if (action.indexOf("chat-filter:") === 0) {
            state.chat.filter = action.slice("chat-filter:".length);
            renderApp();
            return;
        }
        if (action === "chat-open-training") {
            await loadChatTrainingLibrary();
            return;
        }
        if (action === "chat-files-toggle") {
            state.chat.filesOpen = !state.chat.filesOpen;
            renderApp();
            return;
        }
        if (action === "chat-title-edit") {
            chatStartRename();
            return;
        }
        if (action === "chat-title-cancel") {
            chatCancelRename();
            return;
        }
        if (action === "chat-title-save" || action === "chat-title-reset") {
            try {
                await chatSaveTitle(action === "chat-title-reset");
            } catch (error) {
                setNotice("error", error.message || "The name could not be saved.");
            }
            return;
        }
        if (action === "chat-rename-session") {
            var renameUser = element && element.getAttribute("data-user-id");
            var renameSession = element && element.getAttribute("data-session-id");
            var alreadyOpen = state.chat.view !== "training" && state.chat.selected && state.chat.selected.user_id === renameUser && state.chat.selected.session_id === renameSession;
            if (renameUser && renameSession && !alreadyOpen) {
                await loadChatSession(renameUser, renameSession);
            }
            chatStartRename();
            return;
        }
        if (action === "chat-open-session" || action.indexOf("chat-open-session:") === 0) {
            var sessionUser = element && element.getAttribute("data-user-id");
            var sessionId = element && element.getAttribute("data-session-id");
            if (!sessionUser || !sessionId) {
                var sessionParts = action.slice("chat-open-session:".length).split(":");
                sessionUser = sessionParts.shift() || chatDefaultUserId();
                sessionId = sessionParts.join(":");
            }
            await loadChatSession(sessionUser, sessionId);
            return;
        }
        if (action.indexOf("chat-discard-attachment:") === 0) {
            var attachmentIndex = Number(action.slice("chat-discard-attachment:".length));
            if (Number.isFinite(attachmentIndex)) {
                state.chat.attachments.splice(attachmentIndex, 1);
                renderApp();
            }
            return;
        }
        if (action === "modal-copy") {
            if (state.modal && state.modal.copyValue) {
                await copyText(state.modal.copyValue);
                setNotice("success", "Copied to clipboard.");
            }
            return;
        }
        if (action === "boot-sweep-start") {
            await ensureBootSweepScores(false);
            await startBootSweep();
            return;
        }
        if (action === "boot-sweep-stop") {
            await stopBootSweep();
            return;
        }
        if (action === "boot-sweep-reset") {
            await resetBootSweepBest();
            return;
        }
        if (action === "refresh-bootstrap") {
            await refreshBootstrap("Snapshot refreshed.");
            return;
        }
        if (action === "software-update-open") {
            setScreen("overview");
            window.setTimeout(function () { scrollIntoViewIfPresent("#ayu-software-update", { behavior: "smooth", block: "start" }); }, 0);
            return;
        }
        if (action === "software-update-save") {
            var updatesEnabled = asBoolean(getByPath(state.forms, "overview.softwareUpdatesEnabled", true), true);
            await patchConfig({ software_update: { enabled: updatesEnabled } }, updatesEnabled ? "Software update checks enabled." : "Software update checks disabled; AutoYou will not query its update service.");
            state.softwareUpdate.checked = !updatesEnabled;
            state.softwareUpdate.payload = getByPath(state.bootstrap, "status.software_update", {});
            if (updatesEnabled) {
                await checkSoftwareUpdate(true);
            }
            return;
        }
        if (action === "software-update-check") {
            await checkSoftwareUpdate(true);
            return;
        }
        if (action === "software-update-apply") {
            var selectedUpdate = state.softwareUpdate.payload || {};
            var updatePrompt = getByPath(selectedUpdate, "store_managed", false)
                ? "Open the platform Store to update this AutoYou installation?"
                : "Download the verified AutoYou update and start the platform install now?";
            if (!window.confirm(updatePrompt)) {
                return;
            }
            var updateResult = await postJson("/api/software-update/apply", {});
            state.softwareUpdate.payload = Object.assign({}, state.softwareUpdate.payload || {}, updateResult || {});
            if (!getByPath(updateResult, "success", false)) {
                throw new Error(getByPath(updateResult, "error", "The update could not be installed."));
            }
            if (getByPath(updateResult, "command", "")) {
                setModal({ title: "Finish the AutoYou update", description: getByPath(updateResult, "message", "Run this command to finish the update."), text: updateResult.command, copyValue: updateResult.command });
            } else {
                setNotice("success", getByPath(updateResult, "message", "The update is ready. Restart AutoYou when prompted."));
            }
            return;
        }
        if (action === "profile-image-remove") {
            var removeResponse = await requestJson("/api/admin/profile-image", { method: "DELETE" });
            applyBootstrap(removeResponse.bootstrap ? removeResponse.bootstrap : removeResponse);
            setNotice("success", "Profile image removed.");
            return;
        }
        if (action.indexOf("open-guide:") === 0) {
            window.location.href = action.slice("open-guide:".length);
            return;
        }
        if (action === "setup-refresh") {
            await ensureSetupData(true);
            setNotice("success", "Setup status refreshed.");
            return;
        }
        if (action.indexOf("setup-step:") === 0) {
            setSetupStep(action.split(":")[1]);
            return;
        }
        if (action.indexOf("setup-profile:") === 0) {
            selectSetupProfile(action.slice("setup-profile:".length));
            return;
        }
        if (action.indexOf("setup-answer:") === 0) {
            var setupAnswerParts = action.split(":");
            updateSetupAnswer(setupAnswerParts[1] || "", setupAnswerParts.slice(2).join(":"));
            return;
        }
        if (action === "setup-preview-recipe") {
            await previewSetupRecipe();
            return;
        }
        if (action === "setup-apply-recipe") {
            await applySetupRecipe(false);
            return;
        }
        if (action === "setup-apply-guarded-recipe") {
            await applySetupRecipe(true);
            return;
        }
        if (action === "setup-prev") {
            setSetupStep((state.setup.activeStep || 0) - 1);
            return;
        }
        if (action === "setup-next") {
            setSetupStep((state.setup.activeStep || 0) + 1);
            return;
        }
        if (action.indexOf("setup-open-screen:") === 0) {
            openSetupLinkedScreen(action.split(":")[1]);
            return;
        }
        if (action === "setup-return") {
            returnToSetupLinkedStep();
            return;
        }
        if (action === "setup-complete" || action === "setup-reopen") {
            var completed = action === "setup-complete";
            await postJson("/api/wizard/complete", { completed: completed });
            syncSetupCompletion(completed);
            renderApp();
            setNotice("success", completed ? "Bootstrap checklist marked complete." : "Bootstrap checklist reopened.");
            return;
        }
        if (action === "overview-bind-home" || action === "overview-bind-local") {
            var bindHost = action === "overview-bind-home" ? "0.0.0.0" : "127.0.0.1";
            setByPath(state.forms, "overview.bindHost", bindHost);
            if (bindHost === "0.0.0.0") {
                // Home network access comes with HTTPS and the safest website
                // route: only the admin port opens, website apps stay behind
                // its sign-in, pages and notes never travel over plain HTTP.
                await patchConfig({ server: { bind_host: bindHost, https_enabled: true, home_network_websites: "path_proxy" } }, "Home network access with HTTPS will apply after shutdown and restart. Website apps stay behind the admin sign-in. Install this server's CA certificate (/ca.crt) once on each device.");
                return;
            }
            await patchConfig({ server: { bind_host: bindHost } }, "Local-only access will apply after shutdown and restart.");
            return;
        }
        if (action === "overview-https:enable" || action === "overview-https:disable") {
            var httpsOn = action === "overview-https:enable";
            var exposedToNetwork = normalizeOverviewBindHost(getByPath(bootstrapConfig(), "server.bind_host", "127.0.0.1")) === "0.0.0.0"
                || asBoolean(getByPath(state.bootstrap, "status.home_network.enabled", false), false);
            if (!httpsOn && exposedToNetwork && !window.confirm("Turn HTTPS off while home network access is on?\n\nOther devices on your network would then open pages, notes and the sign-in page over plain HTTP, readable by anyone on the same Wi-Fi.")) {
                return;
            }
            setByPath(state.forms, "overview.httpsEnabled", httpsOn);
            await patchConfig({ server: { https_enabled: httpsOn } }, httpsOn ? "HTTPS will apply after shutdown and restart. Install this server's CA certificate (/ca.crt) on each device to trust it." : "HTTPS will be turned off after shutdown and restart.");
            return;
        }
        if (action.indexOf("overview-home-websites:") === 0) {
            var websitesMode = normalizeHomeNetworkWebsitesMode(action.split(":")[1]);
            await patchConfig({ server: { home_network_websites: websitesMode } }, websitesMode === "direct_forward" ? "Paired devices open website apps directly; other browsers need a device pass from the admin sign-in." : "Website apps open only through the admin sign-in.");
            return;
        }
        if (action.indexOf("overview-vpn:") === 0) {
            var vpnOn = action.split(":")[1] === "enable";
            await patchConfig({ server: { vpn_addresses: vpnOn } }, vpnOn ? "VPN addresses will be served after restart." : "VPN addresses will stop being served after restart.");
            return;
        }
        if (action === "cloud-device-approval:on" || action === "cloud-device-approval:off") {
            state.operations.cloudDevices = await postJson("/api/cloud/devices/approval", { required: action === "cloud-device-approval:on" });
            setNotice("success", action === "cloud-device-approval:on" ? "New Cloud Pair devices now wait for your approval here." : "New Cloud Pair devices no longer need approval.");
            renderApp();
            return;
        }
        if (action.indexOf("cloud-device-approve:") === 0 || action.indexOf("cloud-device-forget:") === 0) {
            var deviceParts = action.split(":");
            var deviceVerb = deviceParts[0] === "cloud-device-approve" ? "approve" : "forget";
            if (deviceVerb === "forget" && !window.confirm("Remove this device's saved key?\n\nIt will have to pair through AutoYou Cloud again.")) {
                return;
            }
            state.operations.cloudDevices = await postJson("/api/cloud/devices/" + encodeURIComponent(deviceParts[1]) + "/" + deviceVerb, {});
            setNotice("success", deviceVerb === "approve" ? "Device approved." : "Device removed.");
            renderApp();
            return;
        }
        if (action.indexOf("overview-discovery:") === 0) {
            var discoveryOn = action.split(":")[1] === "enable";
            await patchConfig({ server: { discovery_enabled: discoveryOn } }, discoveryOn ? "Nearby AutoYou apps can find this computer on the home network." : "Nearby discovery is off. Devices connect by address instead.");
            return;
        }
        if (action.indexOf("overview-native-unlock:") === 0) {
            var nativeUnlockEnabled = action.split(":")[1] === "enable";
            setByPath(state.forms, "overview.nativeUnlockEnabled", nativeUnlockEnabled);
            await patchConfig({ security: { native_unlock_enabled: nativeUnlockEnabled } }, nativeUnlockEnabled ? "System credential unlock will be available after restart." : "System credential unlock will be disabled after restart.");
            return;
        }
        if (action.indexOf("overview-remote-permissions:") === 0) {
            var remotePermsOn = action.split(":")[1] === "enable";
            setByPath(state.forms, "overview.allowRemoteAdminPermissions", remotePermsOn);
            await patchConfig({ server: { allow_remote_admin_permissions: remotePermsOn } }, remotePermsOn ? "Network admin permissions will apply after shutdown and restart. Admins connecting from WSL, Docker, or LAN over HTTPS will be able to edit hardware permissions." : "Network admin permissions disabled for next boot. Permissions will require localhost.");
            return;
        }
        if (action.indexOf("service:") === 0) {
            await handleServiceAction(action);
            return;
        }
        if (action === "save-overview") {
            await patchConfig({
                server: { name: getByPath(state.forms, "overview.serverName", "AutoYou-Server") },
                ui_theme: getByPath(state.forms, "overview.adminTheme", "dark")
            }, "Server identity updated.");
            return;
        }
        if (action === "save-messaging-server-name") {
            await patchConfig({
                server: { name: getByPath(state.forms, "overview.serverName", "AutoYou-Server") }
            }, "Server name updated.");
            return;
        }
        if (action === "save-ai-provider") {
            await patchConfig(aiProviderPayload(), "AI provider settings updated.");
            await ensureAiData(true);
            return;
        }
        if (action.indexOf("select-ai-provider:") === 0) {
            var selectedProvider = action.split(":")[1] || "ollama";
            setByPath(state.forms, "aiProvider.provider", selectedProvider);
            renderApp();
            if (isOllamaProvider(selectedProvider) && !state.aiLibrary.catalog && !state.aiLibrary.catalogLoading) {
                await loadModelCatalog(false);
            }
            if (selectedProvider === "ollama_gateway" || selectedProvider === "odysseus") {
                await ensureAiData(true);
            }
            return;
        }
        if (action.indexOf("select-tts-provider:") === 0) {
            var selectedTtsProvider = action.split(":")[1] || "system";
            setByPath(state.forms, "speech.tts_provider", selectedTtsProvider);
            renderApp();
            return;
        }
        if (action === "save-ai-agent") {
            var aiAgentSettings = clone(getByPath(state.forms, "aiAgent", {}));
            delete aiAgentSettings.record_messages_in_database;
            delete aiAgentSettings.lan_access_enabled;
            await patchConfig({
                ai_agent: aiAgentSettings,
                client_identity: getByPath(state.forms, "clientIdentity", {})
            }, "AutoYou AI settings updated.");
            return;
        }
        if (action === "ai-toggle-internet") {
            var enabled = !Boolean(getByPath(state.forms, "aiAgent.internet_search_enabled", true));
            await postJson("/api/ai/internet/search_enabled", { enabled: enabled });
            setByPath(state.forms, "aiAgent.internet_search_enabled", enabled);
            await refreshBootstrap("Internet search setting updated.");
            return;
        }
        if (action === "save-model-behavior") {
            await postJson("/api/model-behavior", buildModelBehaviorPayload());
            await ensureAiData(true);
            setNotice("success", "Model behavior updated.");
            return;
        }
        if (action === "ai-refresh" || action === "ai-refresh-local") {
            await ensureAiData(true);
            setNotice("success", "AI data refreshed.");
            return;
        }
        if (action === "ai-search-catalog") {
            await loadModelCatalog(false);
            return;
        }
        if (action.indexOf("ai-catalog-detail:") === 0) {
            var detailSource = element ? (element.getAttribute("data-source") || state.aiLibrary.source || "ollama") : (state.aiLibrary.source || "ollama");
            var detailReference = element ? (element.getAttribute("data-reference") || "") : "";
            var catalogKey = catalogDetailKey(detailSource, detailReference);
            if (state.aiLibrary.selectedCatalogKey === catalogKey) {
                state.aiLibrary.selectedCatalogKey = "";
                renderApp();
                return;
            }
            state.aiLibrary.selectedCatalogKey = catalogKey;
            renderApp();
            await ensureCatalogDetail(detailSource, detailReference);
            return;
        }
        if (action === "ai-source") {
            state.aiLibrary.source = element ? element.value : state.aiLibrary.source;
            state.aiLibrary.catalog = null;
            state.aiLibrary.catalogPage = 1;
            state.aiLibrary.catalogHasMore = false;
            state.aiLibrary.catalogError = "";
            state.aiLibrary.selectedCatalogKey = "";
            renderApp();
            await loadModelCatalog(false);
            return;
        }
        if (action === "ai-load-more") {
            await loadModelCatalog(true);
            return;
        }
        if (action.indexOf("ai-select-local:") === 0 || action.indexOf("ai-select:") === 0) {
            var selectedReference = element ? element.getAttribute("data-model") || element.getAttribute("data-reference") : "";
            var selectedModel = selectedReference || action.split(":").slice(1).join(":");
            var selectedResult = await postJson("/api/model-library/select", { model: selectedModel });
            applySelectedOllamaModel(selectedResult, selectedModel);
            await refreshBootstrap("Model selection updated.");
            await ensureAiData(true);
            return;
        }
        if (action.indexOf("ai-delete-local:") === 0) {
            var deleteReference = element ? (element.getAttribute("data-model") || element.getAttribute("data-reference") || "") : "";
            deleteReference = deleteReference || action.split(":").slice(1).join(":");
            if (!window.confirm("Delete local model \"" + deleteReference + "\"? This removes it from disk and cannot be undone.")) {
                return;
            }
            var deleteResult = await postJson("/api/model-library/delete", { model: deleteReference });
            await ensureAiData(true);
            if (getByPath(deleteResult, "warning", "")) {
                setNotice("warning", getByPath(deleteResult, "warning", ""));
            } else {
                setNotice("success", getByPath(deleteResult, "message", "Model deleted."));
            }
            return;
        }
        if (action.indexOf("ai-detail-select:") === 0) {
            var selectModel = element ? (element.getAttribute("data-model") || element.getAttribute("data-reference") || "") : "";
            var alreadyInstalled = element && element.getAttribute("data-installed") === "1";
            if (alreadyInstalled) {
                var detailSelectionResult = await postJson("/api/model-library/select", { model: selectModel });
                applySelectedOllamaModel(detailSelectionResult, selectModel);
                await refreshBootstrap("Model selection updated.");
                await ensureAiData(true);
                return;
            }
            await startModelDownload(buildModelDownloadRequest(element), {
                model: selectModel,
                reference: element ? (element.getAttribute("data-reference") || selectModel) : selectModel
            });
            setNotice("success", "Model download started. AutoYou will switch to it when the download completes.");
            return;
        }
        if (action.indexOf("ai-download:") === 0) {
            var downloadRef = element ? element.getAttribute("data-reference") : "";
            await postJson("/api/model-library/download", { source: state.aiLibrary.source || "ollama", reference: downloadRef || action.split(":").slice(1).join(":") });
            await ensureAiData(true);
            setNotice("success", "Model download started.");
            return;
        }
        if (action.indexOf("ai-detail-download:") === 0) {
            await startModelDownload(buildModelDownloadRequest(element));
            setNotice("success", "Model download started.");
            return;
        }
        if (action === "desktop-assets-refresh") {
            state.desktopAssets.error = "";
            await ensureDesktopAssets(true);
            if (state.desktopAssets.error) {
                throw new Error(state.desktopAssets.error);
            }
            setNotice("success", "Desktop agent templates and local packs refreshed.");
            return;
        }
        if (action === "desktop-asset-save-preferences") {
            await saveDesktopAssetPreferences();
            return;
        }
        if (action === "desktop-asset-generate-prompt") {
            await generateDesktopAssetSetupPrompt();
            return;
        }
        if (action === "desktop-pack-select") {
            var desktopPackInput = root ? root.querySelector('[data-role="desktop-pack-input"]') : null;
            if (desktopPackInput) {
                desktopPackInput.click();
            }
            return;
        }
        if (action === "desktop-pack-remove") {
            await removeDesktopAssetPack(element && element.getAttribute("data-agent"), element && element.getAttribute("data-storage-id"));
            return;
        }
        if (action.indexOf("select-agent:") === 0) {
            state.selectedAgentName = action.split(":")[1];
            state.agentWorkbench.detail = null;
            state.agentWorkbench.stale = true;
            renderApp();
            await ensureAgentWorkbenchDetail(true, true);
            scrollIntoViewIfPresent("#ayu-agent-studio-anchor", { behavior: "smooth", block: "start" });
            return;
        }
        if (action.indexOf("guide-open:") === 0) {
            state.guides.selectedId = action.slice("guide-open:".length);
            renderApp();
            await ensureGuideContent(state.guides.selectedId, false, true);
            if (window.matchMedia && window.matchMedia("(max-width: 900px)").matches) {
                scrollIntoViewIfPresent("#ayu-guide-content-anchor", { behavior: "smooth", block: "start" });
            }
            return;
        }
        if (action.indexOf("agent-install:") === 0) {
            var installName = action.split(":")[1];
            state.selectedAgentName = installName;
            applyAgentWorkbenchResponse(await postJson("/api/agents/install", { agent_name: installName }));
            await ensureAgentWorkbenchDetail(true, true);
            return;
        }
        if (action === "agent-install-builder-suite") {
            applyAgentWorkbenchResponse(await postJson("/api/agents/install-builder-suite", { restart_ai: true }), "Builder suite installed.");
            await refreshBootstrap("Builder suite installed.");
            if (state.selectedAgentName) {
                await ensureAgentWorkbenchDetail(true, true);
            }
            return;
        }
        if (action.indexOf("agent-uninstall:") === 0) {
            var uninstallName = action.split(":")[1];
            state.selectedAgentName = uninstallName;
            applyAgentWorkbenchResponse(await postJson("/api/agents/uninstall", { agent_name: uninstallName }));
            await ensureAgentWorkbenchDetail(true, true);
            return;
        }
        if (action.indexOf("agent-frontend:") === 0) {
            var agentName = action.split(":")[1];
            var detail = getByPath(state.bootstrap, "agents.agent_details." + agentName, {});
            var current = Boolean(getByPath(detail, "frontend_control.enabled", false));
            state.selectedAgentName = agentName;
            applyAgentWorkbenchResponse(await postJson("/api/agents/frontend", { agent_name: agentName, enabled: !current }), "Website state updated.");
            await ensureAgentWorkbenchDetail(true, true);
            return;
        }
        if (action.indexOf("set-default-website:") === 0) {
            var defaultWebsiteName = action.substring("set-default-website:".length);
            if (!defaultWebsiteName) {
                return;
            }
            var defaultResp = await postJson("/api/agent-websites/default", { agent_name: defaultWebsiteName });
            if (defaultResp && defaultResp.success) {
                await refreshBootstrap("Default website set to " + (getByPath(defaultResp, "default_website.title", defaultWebsiteName) || defaultWebsiteName) + ".");
            } else {
                setNotice("error", getByPath(defaultResp, "error", "Failed to set the default website."));
            }
            return;
        }
        if (action === "hosting-save" || action === "hosting-start" || action === "hosting-disable") {
            var hostingDraft = clone(getByPath(state.forms, "page", {}));
            delete hostingDraft.websiteHosting;
            var hostingPayload = {
                agent_name: getByPath(state.forms, "page.websiteHosting.agent_name", ""),
                enabled: action === "hosting-disable" ? false : Boolean(getByPath(state.forms, "page.websiteHosting.enabled", true)),
                auto_start_on_boot: Boolean(getByPath(state.forms, "page.websiteHosting.auto_start_on_boot", false)),
                start: action === "hosting-start"
            };
            if (action === "hosting-start") {
                hostingPayload.enabled = true;
            }
            var hostingResp = await postJson("/api/tunnelmole/website-hosting", hostingPayload);
            if (hostingResp && hostingResp.success) {
                await refreshBootstrap(action === "hosting-disable" ? "Public website turned off." : (action === "hosting-start" ? "Public website is starting." : "Website choice saved."));
                restorePageDraft(hostingDraft);
                renderApp();
            } else {
                setNotice("error", getByPath(hostingResp, "error", "Website management update failed."));
            }
            return;
        }
        if (action === "agent-websites-security-save") {
            var securityDraft = snapshotPageForm();
            var secResp = await postJson("/api/agent-websites/security", {
                require_otp: Boolean(getByPath(state.forms, "page.agentWebsitesSecurity.require_otp", false)),
                disable_otp: Boolean(getByPath(state.forms, "page.agentWebsitesSecurity.disable_otp", false)),
                shared_session_enabled: Boolean(getByPath(state.forms, "page.agentWebsitesSecurity.shared_session_enabled", false)),
                shared_session_ttl_days: Number(getByPath(state.forms, "page.agentWebsitesSecurity.shared_session_ttl_days", 30))
            });
            if (secResp && secResp.success) {
                await refreshBootstrap("Agent website security settings saved.");
                restorePageDraft(securityDraft);
                renderApp();
            } else {
                setNotice("error", getByPath(secResp, "error", "Failed to save agent website security settings."));
            }
            return;
        }
        if (action === "agent-sessions-sign-out-all") {
            var signOutResp = await postJson("/api/agent-websites/sessions/sign-out-all", {});
            if (signOutResp && signOutResp.success) {
                setNotice("success", "Signed out of " + String(getByPath(signOutResp, "sessions_invalidated", 0)) + " agent session(s).");
            } else {
                setNotice("error", getByPath(signOutResp, "error", "Failed to sign out agent sessions."));
            }
            return;
        }
        if (action === "agent-frontend-route") {
            var routeAgentName = state.selectedAgentName;
            if (!routeAgentName) {
                return;
            }
            applyAgentWorkbenchResponse(await postJson("/api/agents/frontend", {
                agent_name: routeAgentName,
                route_mode: getByPath(state.forms, "agentWorkbench.frontend_route_mode", "path_proxy")
            }), "Website route mode updated.");
            await ensureAgentWorkbenchDetail(true, true);
            return;
        }
        if (action === "agent-scaffold") {
            applyAgentWorkbenchResponse(await postJson("/api/builder/create", {
                name: getByPath(state.forms, "agentWorkbench.name", ""),
                description: getByPath(state.forms, "agentWorkbench.description", ""),
                tool_name: getByPath(state.forms, "agentWorkbench.tool_name", "handle_request"),
                tool_description: getByPath(state.forms, "agentWorkbench.tool_description", "")
            }), "Workspace agent draft created.");
            await ensureAgentWorkbenchDetail(true, true);
            return;
        }
        if (action === "agent-workbench-reload") {
            await ensureAgentWorkbenchDetail(true, true);
            setNotice("success", "Agent studio reloaded.");
            return;
        }
        if (action.indexOf("agent-workbench-tab:") === 0) {
            state.agentWorkbench.activeTab = action.split(":")[1] || "builder";
            renderApp();
            return;
        }
        if (action === "agent-workbench-copy-path") {
            var selectedWorkbenchDetail = state.agentWorkbench.detail;
            var copyPath = agentFrontendCopyPath(selectedWorkbenchDetail || {});
            if (copyPath) {
                await copyText(copyPath);
                setNotice("success", "Website path copied to clipboard.");
            }
            return;
        }
        if (action === "agent-workbench-clone") {
            applyAgentWorkbenchResponse(await postJson("/api/agents/workbench/" + encodeURIComponent(state.selectedAgentName) + "/clone", {}), "Workspace draft cloned.");
            return;
        }
        if (action === "agent-workbench-save-instructions") {
            applyAgentWorkbenchResponse(await postJson("/api/agents/workbench/" + encodeURIComponent(state.selectedAgentName) + "/instructions", {
                description: getByPath(state.forms, "agentWorkbench.draft_description", ""),
                instructions: getByPath(state.forms, "agentWorkbench.draft_instructions", "")
            }), "Draft instructions saved.");
            return;
        }
        if (action === "agent-workbench-scaffold-frontend") {
            applyAgentWorkbenchResponse(await postJson("/api/agents/workbench/" + encodeURIComponent(state.selectedAgentName) + "/frontend/scaffold", {
                ui_purpose: getByPath(state.forms, "agentWorkbench.frontend_ui_purpose", ""),
                app_title: getByPath(state.forms, "agentWorkbench.frontend_app_title", ""),
                local_port: getByPath(state.forms, "agentWorkbench.frontend_local_port", 8094),
                frontend_stack: getByPath(state.forms, "agentWorkbench.frontend_stack", "fastapi_static"),
                backend_stack: getByPath(state.forms, "agentWorkbench.backend_stack", "python_fastapi")
            }), "Draft website scaffold created.");
            return;
        }
        if (action === "agent-workbench-save-manifest") {
            applyAgentWorkbenchResponse(await postJson("/api/agents/workbench/" + encodeURIComponent(state.selectedAgentName) + "/frontend/manifest", {
                title: getByPath(state.forms, "agentWorkbench.frontend_title", ""),
                description: getByPath(state.forms, "agentWorkbench.frontend_description", ""),
                entry_path: getByPath(state.forms, "agentWorkbench.frontend_entry_path", "/"),
                recommended_port: getByPath(state.forms, "agentWorkbench.frontend_recommended_port", ""),
                requires_proxy_registration: getByPath(state.forms, "agentWorkbench.frontend_requires_proxy", true),
                frontend_stack: getByPath(state.forms, "agentWorkbench.frontend_stack", "fastapi_static"),
                backend_stack: getByPath(state.forms, "agentWorkbench.backend_stack", "python_fastapi")
            }), "Draft website details saved.");
            return;
        }
        if (action === "agent-workbench-test") {
            applyAgentWorkbenchResponse(await postJson("/api/agents/workbench/" + encodeURIComponent(state.selectedAgentName) + "/test", {}), "Draft structural check completed.");
            return;
        }
        if (action === "agent-workbench-publish") {
            applyAgentWorkbenchResponse(await postJson("/api/agents/workbench/" + encodeURIComponent(state.selectedAgentName) + "/publish", {
                install_after_publish: getByPath(state.forms, "agentWorkbench.install_after_publish", false)
            }), "Draft published. Restart AutoYou AI to load it.");
            return;
        }
        if (action === "agent-workbench-discard") {
            applyAgentWorkbenchResponse(await postJson("/api/agents/workbench/" + encodeURIComponent(state.selectedAgentName) + "/discard", {}), "Workspace draft discarded.");
            await ensureAgentWorkbenchDetail(true, true);
            return;
        }
        if (action === "instructions-reload") {
            await ensureInstructions(true);
            setNotice("success", "Prompt instructions reloaded.");
            return;
        }
        if (action === "instructions-revert") {
            await requestJson("/api/agent-instructions/revert", { method: "POST" });
            await ensureInstructions(true);
            setNotice("success", "Prompt instructions reverted to fallback.");
            return;
        }
        if (action === "instructions-save-raw") {
            await postJson("/api/agent-instructions", { instructions: getByPath(state.forms, "agentWorkbench.raw_instructions", "") });
            await ensureInstructions(true);
            setNotice("success", "Raw prompt updated.");
            return;
        }
        if (action === "instructions-save-sections") {
            var sectionPayload = (getByPath(state.instructions, "payload.sections", []) || []).map(function (section) {
                return { variable: section.variable, value: getByPath(state.forms, "agentWorkbench.section_" + section.variable, "") };
            });
            await postJson("/api/agent-instructions/sections", { sections: sectionPayload });
            await ensureInstructions(true);
            setNotice("success", "Prompt sections updated.");
            return;
        }
        if (action.indexOf("instructions-section:") === 0) {
            state.instructions.selectedVariable = action.split(":")[1];
            renderApp();
            return;
        }
        if (action.indexOf("instructions-mode:") === 0) {
            state.instructions.mode = action.split(":")[1];
            renderApp();
            return;
        }
        if (action === "save-page") {
            await patchConfig({
                autoyou_page: {
                    port: getByPath(state.forms, "page.port", 8067),
                    auto_start: getByPath(state.forms, "page.auto_start", true),
                    feed_window_days: getByPath(state.forms, "page.feed_window_days", 0),
                    theme: getByPath(state.forms, "page.theme", "light"),
                    custom_forward_enabled: getByPath(state.forms, "page.custom_forward_enabled", false),
                    custom_forward_port: getByPath(state.forms, "page.custom_forward_port", 8067),
                    advertised_websites: getByPath(state.forms, "page.advertisedWebsites", []),
                    bookmarks: getByPath(state.forms, "page.bookmarks", [])
                }
            }, "Page settings updated.");
            return;
        }
        if (action === "save-page-access") {
            await patchConfig({
                autoyou_page: {
                    custom_forward_enabled: getByPath(state.forms, "page.custom_forward_enabled", false),
                    custom_forward_port: getByPath(state.forms, "page.custom_forward_port", 8067)
                }
            }, "Browser forwarding updated.");
            return;
        }
        if (action === "save-permissions-audio") {
            await savePermissions([
                "video_call_enabled", "audio_call_enabled", "audio_playback_enabled", "computer_microphone", "computer_sound",
                "ai_audio_replies_enabled", "autoyou_agents_disabled", "voice_call_recording_enabled",
                "background_mode_enabled", "safety_recording_enabled", "location_recording_enabled", "wuift_enabled"
            ], "Audio permissions updated.");
            return;
        }
        if (action === "save-permissions-video") {
            await savePermissions([
                "video_call_recording_enabled", "webcam_sharing_enabled", "screen_capture_enabled",
                "screen_send_enabled", "screen_source_enabled",
                "api_video_input_enabled", "video_file_playback_enabled", "remote_desktop_control_enabled", "game_mode_enabled"
            ], "Video and computer permissions updated.");
            return;
        }
        if (action === "save-permissions-data") {
            await savePermissions(["chat_memory_enabled", "ai_agent_lan_access_enabled"], "Data and network permissions updated.");
            return;
        }
        if (action === "save-permissions-web") {
            await savePermissions(["remote_access_role", "admin_frontend_enabled"], "Paired browser permissions updated.");
            return;
        }
        if (action === "page-add-website") {
            var pageDraft = snapshotPageForm();
            var draft = clone(getByPath(state.forms, "page.newWebsite", {}));
            if (!hasValue(draft.port)) {
                throw new Error("Port is required before adding an advertised website.");
            }
            getByPath(state.forms, "page.advertisedWebsites", []).push(draft);
            setByPath(state.forms, "page.newWebsite", { port: "", label: "", description: "", target_url: "", websocket_enabled: false, enabled: true });
            pageDraft.newWebsite = getByPath(state.forms, "page.newWebsite", {});
            await persistAdvertisedWebsites("Advertised website added.", pageDraft);
            return;
        }
        if (action.indexOf("page-preset-website:") === 0) {
            var presetParts = action.split(":");
            var presetPort = presetParts[1] || "";
            var presetLabel = presetParts[2] || "";
            setByPath(state.forms, "page.newWebsite.port", presetPort);
            setByPath(state.forms, "page.newWebsite.label", presetLabel);
            setNotice("info", "Preset loaded for port " + presetPort + " (" + presetLabel + "). Click 'Add & save website' to save.");
            render();
            return;
        }
        if (action === "page-discover-localhost") {
            state.pageLocalScan = { running: true, discovered: [], checked: true };
            render();
            var portsToProbe = [
                { port: 3000, label: "Next.js / Node" },
                { port: 3001, label: "React / Node" },
                { port: 4321, label: "Astro" },
                { port: 5000, label: "Flask / Python" },
                { port: 5173, label: "Vite / React / Svelte" },
                { port: 8000, label: "FastAPI / Django" },
                { port: 8080, label: "Express / HTTP" },
                { port: 8081, label: "Metro / React Native" },
                { port: 8888, label: "Jupyter / Web" }
            ];
            var discovered = [];
            var probes = portsToProbe.map(function (item) {
                return new Promise(function (resolve) {
                    var img = new Image();
                    var timer = setTimeout(function () {
                        img.src = "";
                        resolve();
                    }, 450);
                    img.onload = function () {
                        clearTimeout(timer);
                        discovered.push(item);
                        resolve();
                    };
                    img.onerror = function () {
                        clearTimeout(timer);
                        discovered.push(item);
                        resolve();
                    };
                    img.src = "http://127.0.0.1:" + item.port + "/favicon.ico?_ay_probe=" + Date.now();
                });
            });
            await Promise.all(probes);
            state.pageLocalScan = {
                running: false,
                discovered: discovered,
                checked: true
            };
            setNotice("info", discovered.length ? ("Discovered " + discovered.length + " active localhost server(s).") : "Scan finished. No common dev servers were responding.");
            render();
            return;
        }
        if (action.indexOf("page-add-discovered:") === 0) {
            var discParts = action.split(":");
            var discPort = discParts[1] || "";
            var discLabel = discParts[2] || ("Local Dev " + discPort);
            var pageDraftForDisc = snapshotPageForm();
            getByPath(state.forms, "page.advertisedWebsites", []).push({
                port: discPort,
                label: discLabel,
                description: "Auto-discovered local developer web application",
                target_url: "",
                websocket_enabled: true,
                enabled: true
            });
            await persistAdvertisedWebsites("Added " + discLabel + " (port " + discPort + ") to advertised websites.", pageDraftForDisc);
            return;
        }
        if (action.indexOf("page-share-website:") === 0) {
            var shareIndex = Number(action.split(":")[1]);
            var siteToShare = getByPath(state.forms, "page.advertisedWebsites." + shareIndex, {});
            var sharePort = siteToShare.port || "";
            var shareLabel = siteToShare.label || ("Local App " + sharePort);
            var shareUrl = "autoyou://share?type=website&port=" + sharePort + "&label=" + encodeURIComponent(shareLabel);
            if (navigator.share) {
                try {
                    await navigator.share({
                        title: shareLabel + " - AutoYou Peer Website",
                        text: "Connect to my localhost web application (" + shareLabel + ") securely via AutoYou P2P:",
                        url: shareUrl
                    });
                    setNotice("success", "Shared " + shareLabel + " via native share.");
                    return;
                } catch (_) { }
            }
            await copyText(shareUrl);
            setNotice("success", "P2P WebRTC share link for " + shareLabel + " copied to clipboard! (" + shareUrl + ")");
            return;
        }
        if (action.indexOf("page-remove-website:") === 0) {
            var pageDraftForRemove = snapshotPageForm();
            var removeIndex = Number(action.split(":")[1]);
            getByPath(state.forms, "page.advertisedWebsites", []).splice(removeIndex, 1);
            await persistAdvertisedWebsites("Advertised website removed.", pageDraftForRemove);
            return;
        }
        if (action === "page-add-bookmark") {
            var pageDraftForBookmark = snapshotPageForm();
            var bookmarkDraft = clone(getByPath(state.forms, "page.newBookmark", {}));
            if (!hasValue(bookmarkDraft.url)) {
                throw new Error("URL is required before adding a bookmark.");
            }
            getByPath(state.forms, "page.bookmarks", []).push(bookmarkDraft);
            setByPath(state.forms, "page.newBookmark", { title: "", url: "", description: "", enabled: true });
            pageDraftForBookmark.newBookmark = getByPath(state.forms, "page.newBookmark", {});
            await persistBookmarks("Bookmark added.", pageDraftForBookmark);
            return;
        }
        if (action.indexOf("page-remove-bookmark:") === 0) {
            var pageDraftForBookmarkRemove = snapshotPageForm();
            var bookmarkRemoveIndex = Number(action.split(":")[1]);
            getByPath(state.forms, "page.bookmarks", []).splice(bookmarkRemoveIndex, 1);
            await persistBookmarks("Bookmark removed.", pageDraftForBookmarkRemove);
            return;
        }
        if (action === "apps-copy-link") {
            var appsLink = agentAppsUrl();
            if (appsLink) {
                await copyText(/^[a-z][a-z0-9+.-]*:/i.test(appsLink) ? appsLink : window.location.origin + appsLink);
                setNotice("success", "Agent Apps link copied to clipboard.");
            }
            return;
        }
        if (action.indexOf("page-copy-bookmark:") === 0) {
            var bookmarkCopyIndex = Number(action.split(":")[1]);
            var bookmarkCopyUrl = getByPath(state.forms, "page.bookmarks." + bookmarkCopyIndex + ".url", "");
            if (bookmarkCopyUrl) {
                await copyText(bookmarkCopyUrl);
                setNotice("success", "Bookmark URL copied to clipboard.");
            }
            return;
        }
        if (action === "page-clear-bookmarks") {
            var pageDraftForBookmarkClear = snapshotPageForm();
            setByPath(state.forms, "page.bookmarks", []);
            await persistBookmarks("Bookmarks cleared.", pageDraftForBookmarkClear);
            return;
        }
        if (action === "save-telegram") {
            await patchConfig({ telegram: getByPath(state.forms, "messaging.telegram", {}) }, "Telegram Bot settings updated.");
            await refreshTelegramSenders(false);
            return;
        }
        if (action === "telegram-start-discovery") {
            setByPath(state.forms, "messaging.telegram.access_gate_enabled", true);
            await patchConfig({ telegram: getByPath(state.forms, "messaging.telegram", {}) }, "Telegram Bot discovery started. Send a message to the bot, then refresh senders.");
            await refreshTelegramSenders(false);
            return;
        }
        if (action === "telegram-refresh-senders") {
            await refreshTelegramSenders(true);
            return;
        }
        if (action === "telegram-approve-selected") {
            var selectedTelegramSenderIds = telegramSenderSelection();
            if (!selectedTelegramSenderIds.length) {
                throw new Error("Select at least one discovered Telegram Bot sender.");
            }
            var telegramApprovalResponse = await postJson("/api/telegram/senders/approve", {
                sender_ids: selectedTelegramSenderIds
            });
            if (telegramApprovalResponse.bootstrap) {
                applyBootstrap(telegramApprovalResponse.bootstrap);
            }
            state.operations.telegramSenders = telegramApprovalResponse;
            state.operations.stale = false;
            delete state.operations.errors.telegramSenders;
            setByPath(state.forms, "messaging.telegram.acl_sender_ids", joinList(getByPath(telegramApprovalResponse, "approved_sender_ids", telegramApprovedSenderIds())));
            setByPath(state.forms, "messaging.telegram.access_gate_enabled", true);
            setNotice("success", getByPath(telegramApprovalResponse, "message", "Telegram Bot sender approval list updated."));
            return;
        }
        if (action === "save-telegram-user") {
            await patchConfig({ telegram_user: telegramUserConfigPayload() }, "Telegram User settings updated.");
            return;
        }
        if (action === "telegram-user-connect") {
            setByPath(state.forms, "messaging.telegramUser.enabled", true);
            await patchConfig({ telegram_user: telegramUserConfigPayload() }, "Telegram User settings saved.");
            var telegramUserQr = await requestJson("/api/telegram-user/qr");
            if (Boolean(getByPath(telegramUserQr, "connected", false)) || normalizeStatusToken(getByPath(telegramUserQr, "status", "")) === "connected") {
                await refreshBootstrap("Telegram User connected.");
                await ensureOperationsData(true);
                return;
            }
            openTelegramUserQrModal(telegramUserQr);
            return;
        }
        if (action === "telegram-user-2fa") {
            var telegramUserPassword = String(getByPath(state.forms, "messaging.telegramUser.password", "") || "").trim();
            if (!telegramUserPassword) {
                throw new Error("Enter your Telegram password to finish connecting.");
            }
            setByPath(state.forms, "messaging.telegramUser.password", "");
            var telegramUserPasswordResult = await postJson("/api/telegram-user/2fa", { password: telegramUserPassword });
            if (Boolean(getByPath(telegramUserPasswordResult, "connected", false)) || normalizeStatusToken(getByPath(telegramUserPasswordResult, "status", "")) === "connected") {
                setModal(null);
                await refreshBootstrap("Telegram User connected.");
                await ensureOperationsData(true);
                return;
            }
            var refreshedTelegramUserQr = await requestJson("/api/telegram-user/qr");
            openTelegramUserQrModal(refreshedTelegramUserQr);
            return;
        }
        if (action === "telegram-user-restart") {
            await requestJson("/api/telegram-user/restart", { method: "POST" });
            await refreshBootstrap("Telegram User restarted.");
            await ensureOperationsData(true);
            return;
        }
        if (action === "telegram-user-disconnect") {
            if (!window.confirm("Disconnect Telegram User from this server? Saved Messages will stop until you connect it again.")) {
                return;
            }
            await requestJson("/api/telegram-user/disconnect", { method: "POST" });
            await refreshBootstrap("Telegram User disconnected.");
            await ensureOperationsData(true);
            return;
        }
        if (action === "save-signal") {
            await patchConfig({ signal: getByPath(state.forms, "messaging.signal", {}) }, "Signal settings updated.");
            return;
        }
        if (action === "save-whatsapp") {
            await patchConfig({ whatsapp: getByPath(state.forms, "messaging.whatsapp", {}) }, "WhatsApp settings updated.");
            return;
        }
        if (action === "mcp-generate-token") {
            var currentMcpStatus = getByPath(state.bootstrap, "status.mcp", {});
            if (getByPath(currentMcpStatus, "configured", false) && !window.confirm("Replace the saved MCP token? The adapter will need the new matching config before it can reconnect.")) {
                return;
            }
            var mcpToken = generateMcpSecret("autoyou_mcp_");
            var mcpConfig = Object.assign({}, clone(getByPath(state.forms, "messaging.mcp", {})) || {}, {
                enabled: true,
                api_token: mcpToken,
                clear_api_token: false
            });
            await patchConfig({ mcp: mcpConfig }, "Secure MCP token saved. Download the matching adapter config to continue.");
            state.mcpSetup.generatedToken = mcpToken;
            state.mcpSetup.configDownloaded = false;
            setByPath(state.forms, "messaging.mcp.enabled", true);
            setByPath(state.forms, "messaging.mcp.api_token", "");
            setByPath(state.forms, "messaging.mcp.clear_api_token", false);
            renderApp();
            return;
        }
        if (action === "mcp-download-config") {
            downloadMcpAdapterConfig();
            setNotice("success", "Private adapter config downloaded. Keep it private, install the adapter package, then start it with the file path.");
            return;
        }
        if (action === "save-mcp") {
            var mcpSavePayload = getByPath(state.forms, "messaging.mcp", {});
            var mcpSaveStatus = getByPath(state.bootstrap, "status.mcp", {});
            var clearingMcpToken = Boolean(getByPath(mcpSavePayload, "clear_api_token", false));
            var replacingMcpToken = Boolean(String(getByPath(mcpSavePayload, "api_token", "") || "").trim());
            if (clearingMcpToken && getByPath(mcpSaveStatus, "configured", false) && !window.confirm("Clear the token saved in AutoYou settings? A token in AutoYou-Server/.env will remain active as a fallback.")) {
                return;
            }
            if (!clearingMcpToken && replacingMcpToken && getByPath(mcpSaveStatus, "configured", false) && !window.confirm("Replace the saved MCP token? The adapter must receive the matching new config before it can reconnect.")) {
                return;
            }
            await patchConfig({ mcp: mcpSavePayload }, "AutoYou MCP settings updated.");
            return;
        }
        if (action === "save-video-call") {
            var videoCallPayload = speechPayload();
            videoCallPayload.video_call = videoCallFormPayload();
            await patchConfig(videoCallPayload, "Video call settings updated.");
            await ensureOperationsData(true);
            await ensureMediaDeviceData(false);
            await ensureSpeechData(true);
            return;
        }
        if (action === "video-file-play" || action === "video-file-restart") {
            await postJson("/api/webrtc/video-file/play", { restart: action === "video-file-restart" });
            await ensureOperationsData(true);
            setNotice("success", action === "video-file-restart" ? "Video file restarted." : "Video file playback started.");
            return;
        }
        if (action === "video-file-pause") {
            await postJson("/api/webrtc/video-file/pause", {});
            await ensureOperationsData(true);
            setNotice("success", "Video file playback paused.");
            return;
        }
        if (action === "telegram-allow-code") {
            var allowResponse = await postJson("/api/admin/telegram/allow-code", {});
            setModal({
                title: "Telegram Bot allow code",
                description: "Share this one-time allow code with the user you want to approve.",
                text: getByPath(allowResponse, "allow_code", ""),
                helper: "This also enables the Telegram Bot access gate if it was off.",
                copyValue: getByPath(allowResponse, "allow_code", "")
            });
            return;
        }
        if (action === "signal-restart") {
            await requestJson("/api/signal/restart", { method: "POST" });
            await refreshBootstrap("Signal service restarted.");
            await ensureOperationsData(true);
            return;
        }
        if (action === "signal-cleanup") {
            await requestJson("/api/signal/cleanup", { method: "POST" });
            await refreshBootstrap("Signal pairing cleaned up.");
            await ensureOperationsData(true);
            return;
        }
        if (action === "refresh-pairing-qr" && isPairingQrModal(state.modal)) {
            await refreshPairingQrModal(true);
            return;
        }
        if (action === "signal-qr") {
            var signalQr = await requestJson("/api/signal/qr");
            setModal({
                kind: "pairing-qr",
                platform: "signal",
                title: "Signal QR",
                description: "Scan this QR only with a Signal account you own or control. Signal is optional and unaffiliated; AutoYou processes only Notes-to-Self/self-destination messages for the paired owner.",
                imageUrl: signalQr.qr_url,
                fetchedAt: Date.now(),
                helper: signalQr.error || signalQr.device_name || "Signal pairing"
            });
            return;
        }
        if (action === "whatsapp-qr") {
            var whatsappQr = await requestJson("/api/whatsapp/qr");
            setModal({
                kind: "pairing-qr",
                platform: "whatsapp",
                title: "WhatsApp QR",
                description: "Scan this QR only with your own WhatsApp account. WhatsApp is optional and unaffiliated; AutoYou processes verified self-chat messages for the QR-paired owner and ignores non-self chats.",
                imageUrl: whatsappQr.qr_url,
                fetchedAt: Date.now(),
                helper: whatsappQr.error || whatsappQr.device_name || "WhatsApp pairing"
            });
            return;
        }
        if (action === "whatsapp-restart") {
            await requestJson("/api/whatsapp/restart", { method: "POST" });
            await refreshBootstrap("WhatsApp service restarted.");
            await ensureOperationsData(true);
            return;
        }
        if (action === "whatsapp-reset") {
            await requestJson("/api/whatsapp/reset", { method: "POST" });
            await refreshBootstrap("WhatsApp session reset.");
            await ensureOperationsData(true);
            return;
        }
        if (action === "ops-refresh") {
            await Promise.all([
                ensureOperationsData(true),
                ensureLocalPairInfo(true).catch(function () {})
            ]);
            setNotice("success", "Live View status refreshed.");
            return;
        }
        if (action === "refresh-webrtc-devices") {
            await Promise.all([
                ensureMediaDeviceData(true),
                ensureOperationsData(true)
            ]);
            setNotice("success", "Audio, camera, and display devices refreshed.");
            return;
        }
        if (action.indexOf("live-target-client:") === 0) {
            var targetIndex = Number(action.split(":")[1]);
            var datachannel = state.operations.datachannel || {};
            var connections = getByPath(datachannel, "connections", []);
            var targetSessionId = "";
            if (Array.isArray(connections) && connections[targetIndex]) {
                targetSessionId = String(getByPath(connections[targetIndex], "session_id", ""));
            } else {
                var lastPings = getByPath(datachannel, "last_ping_timestamps", {});
                var sessionIds = Object.keys(lastPings || {}).sort();
                targetSessionId = String(sessionIds[targetIndex] || "");
            }
            if (targetSessionId) {
                setByPath(state.forms, "liveOps.session_id", targetSessionId);
                renderApp();
            }
            return;
        }
        if (action.indexOf("live-target-session:") === 0) {
            var targetSessionId = action.split(":")[1] || "";
            setByPath(state.forms, "liveOps.session_id", targetSessionId);
            renderApp();
            return;
        }
        if (action === "send-telegram-direct") {
            var telegramPayload = {
                chat_id: getByPath(state.forms, "liveOps.telegram_chat_id", ""),
                message: getByPath(state.forms, "liveOps.telegram_message", "")
            };
            var replyToId = getByPath(state.forms, "liveOps.telegram_reply_to_message_id", "");
            if (hasValue(replyToId)) {
                telegramPayload.reply_to_message_id = replyToId;
            }
            await postJson("/api/telegram/send", telegramPayload);
            setByPath(state.forms, "liveOps.telegram_message", "");
            setNotice("success", "Telegram Bot message sent.");
            return;
        }
        if (action === "send-telegram-user-direct") {
            await postJson("/api/telegram-user/send", {
                message: getByPath(state.forms, "liveOps.telegram_user_message", "")
            });
            setByPath(state.forms, "liveOps.telegram_user_message", "");
            await ensureOperationsData(true);
            setNotice("success", "Message sent to Saved Messages.");
            return;
        }
        if (action === "send-signal-direct") {
            await postJson("/api/signal/send", {
                to: getByPath(state.forms, "liveOps.signal_to", ""),
                message: getByPath(state.forms, "liveOps.signal_message", "")
            });
            setByPath(state.forms, "liveOps.signal_message", "");
            await ensureOperationsData(true);
            setNotice("success", "Signal message sent.");
            return;
        }
        if (action === "send-whatsapp-direct") {
            await postJson("/api/whatsapp/send", {
                to: getByPath(state.forms, "liveOps.whatsapp_to", ""),
                message: getByPath(state.forms, "liveOps.whatsapp_message", "")
            });
            setByPath(state.forms, "liveOps.whatsapp_message", "");
            setNotice("success", "WhatsApp message sent.");
            return;
        }
        if (action === "send-webrtc-direct") {
            await postJson("/api/webrtc/send", {
                session_id: getByPath(state.forms, "liveOps.session_id", ""),
                owner_key: getByPath(state.forms, "liveOps.owner_key", ""),
                message: getByPath(state.forms, "liveOps.webrtc_message", "")
            });
            setByPath(state.forms, "liveOps.webrtc_message", "");
            await ensureOperationsData(true);
            setNotice("success", "Browser message sent.");
            return;
        }
        if (action === "webrtc-playback-status") {
            state.operations.playbackStatus = getByPath(await postJson("/api/webrtc/playback/status", {
                session_id: getByPath(state.forms, "liveOps.session_id", ""),
                owner_key: getByPath(state.forms, "liveOps.owner_key", "")
            }), "status", {});
            renderApp();
            setNotice("success", "Playback status refreshed.");
            return;
        }
        if (action === "webrtc-playback-play") {
            state.operations.playbackStatus = getByPath(await postJson("/api/webrtc/playback/play", {
                session_id: getByPath(state.forms, "liveOps.session_id", ""),
                owner_key: getByPath(state.forms, "liveOps.owner_key", ""),
                file_path: getByPath(state.forms, "liveOps.playback_file_path", "")
            }), "status", {});
            renderApp();
            setNotice("success", "Playback started.");
            return;
        }
        if (action === "webrtc-playback-pause") {
            state.operations.playbackStatus = getByPath(await postJson("/api/webrtc/playback/pause", {
                session_id: getByPath(state.forms, "liveOps.session_id", ""),
                owner_key: getByPath(state.forms, "liveOps.owner_key", "")
            }), "status", {});
            renderApp();
            setNotice("success", "Playback paused.");
            return;
        }
        if (action === "webrtc-playback-resume") {
            state.operations.playbackStatus = getByPath(await postJson("/api/webrtc/playback/resume", {
                session_id: getByPath(state.forms, "liveOps.session_id", ""),
                owner_key: getByPath(state.forms, "liveOps.owner_key", "")
            }), "status", {});
            renderApp();
            setNotice("success", "Playback resumed.");
            return;
        }
        if (action === "webrtc-playback-stop") {
            state.operations.playbackStatus = getByPath(await postJson("/api/webrtc/playback/stop", {
                session_id: getByPath(state.forms, "liveOps.session_id", ""),
                owner_key: getByPath(state.forms, "liveOps.owner_key", "")
            }), "status", {});
            renderApp();
            setNotice("success", "Playback stopped.");
            return;
        }
        if (action === "save-speech") {
            await patchConfig(speechPayload(), "Speech settings updated.");
            await ensureSpeechData(true);
            return;
        }
        if (action === "speech-refresh") {
            await ensureSpeechData(true);
            setNotice("success", "Speech model status refreshed.");
            return;
        }
        if (action.indexOf("speech-use-model:") === 0) {
            var selectedSpeechModel = element ? (element.getAttribute("data-model") || "") : "";
            setByPath(state.forms, "speech.stt_model", selectedSpeechModel || action.split(":").slice(1).join(":"));
            await patchConfig(speechPayload(), "Speech settings updated.");
            await ensureSpeechData(true);
            return;
        }
        if (action.indexOf("speech-download-model:") === 0) {
            var librarySpeechModel = element ? (element.getAttribute("data-model") || "") : "";
            setByPath(state.forms, "speech.download_model", librarySpeechModel || action.split(":").slice(1).join(":"));
            await postJson("/api/speech-models/download", { model: getByPath(state.forms, "speech.download_model", "") });
            await refreshSpeechDownloads(true);
            setNotice("success", "Speech model download started.");
            return;
        }
        if (action === "speech-download") {
            await postJson("/api/speech-models/download", { model: getByPath(state.forms, "speech.download_model", "") });
            await refreshSpeechDownloads(true);
            setNotice("success", "Speech model download started.");
            return;
        }
        if (action === "speech-download-emotivoice") {
            var emotivoiceStatus = getByPath(state.speechLibrary, "status.emotivoice", {});
            var emotivoiceModelDir = getByPath(emotivoiceStatus, "model_dir", "the configured voice-model workspace");
            var emotivoiceConsent = "Download the EmotiVoice checkpoints and pronunciation data to " + emotivoiceModelDir + "?\n\nThe checkpoints are not bundled with AutoYou. Their model cards do not clearly state redistribution terms; review those terms before continuing. This download is administrator-initiated.";
            if (!window.confirm(emotivoiceConsent)) {
                return;
            }
            await postJson("/api/speech-models/download", { model: "emotivoice" });
            await refreshSpeechDownloads(true);
            setNotice("success", "EmotiVoice model download started. It may take several minutes.");
            return;
        }
        if (action.indexOf("speech-delete-model:") === 0) {
            var deleteSpeechModel = element ? (element.getAttribute("data-model") || "") : "";
            deleteSpeechModel = deleteSpeechModel || action.split(":").slice(1).join(":");
            if (!window.confirm("Delete cached STT model \"" + deleteSpeechModel + "\"? This removes it from the local cache and cannot be undone.")) {
                return;
            }
            var deleteSpeechResult = await postJson("/api/speech-models/delete", { model: deleteSpeechModel });
            await ensureSpeechData(true);
            if (getByPath(deleteSpeechResult, "warning", "")) {
                setNotice("warning", getByPath(deleteSpeechResult, "warning", ""));
            } else {
                setNotice("success", getByPath(deleteSpeechResult, "message", "Speech model deleted."));
            }
            return;
        }
        if (action === "cloud-activate") {
            await requestJson("/api/cloud/activate", { method: "POST" });
            setNotice("success", "This server is now active for Cloud Pair requests.");
            Promise.all([refreshBootstrap(), ensureOperationsData(true)]).catch(function (error) {
                console.warn("Cloud activation succeeded, but its status refresh failed:", error);
            });
            return;
        }
        if (action === "cloud-reregister") {
            await requestJson("/api/cloud/reregister", { method: "POST" });
            setNotice("success", "Cloud link reconnect requested.");
            Promise.all([refreshBootstrap(), ensureOperationsData(true)]).catch(function (error) {
                console.warn("Cloud reconnect succeeded, but its status refresh failed:", error);
            });
            return;
        }
        if (action === "cloud-push-client") {
            var pushResult = await postJson("/api/cloud/push-client", {});
            await ensureOperationsData(true);
            setNotice("success", getByPath(pushResult, "message", getByPath(pushResult, "detail", "Client connection request sent.")));
            return;
        }
        if (action === "cloud-notify-client") {
            var notifyResult = await postJson("/api/cloud/notify-client", {
                title: getByPath(state.forms, "liveOps.cloud_notify_title", "AutoYou"),
                body: getByPath(state.forms, "liveOps.cloud_notify_body", ""),
                category: getByPath(state.forms, "liveOps.cloud_notify_category", "admin")
            });
            await ensureOperationsData(true);
            var rails = [];
            if (getByPath(notifyResult, "sse_delivered", false)) rails.push("live cloud link");
            if (Number(getByPath(notifyResult, "apns_sent", 0)) > 0) rails.push("Apple devices " + String(getByPath(notifyResult, "apns_sent", 0)));
            if (Number(getByPath(notifyResult, "fcm_sent", 0)) > 0) rails.push("Android devices " + String(getByPath(notifyResult, "fcm_sent", 0)));
            setNotice("success", rails.length ? ("Notification sent via " + rails.join(", ") + ".") : "Notification request accepted by AutoYou Cloud.");
            return;
        }
        if (action === "save-tunnelmole") {
            await patchConfig({ tunnelmole: getByPath(state.forms, "connectivity.tunnelmole", {}) }, "Public link settings updated.");
            return;
        }
        if (action === "save-bluetooth-pairing") {
            await patchConfig({ bluetooth_pairing: getByPath(state.forms, "connectivity.bluetoothPairing", {}) }, "Bluetooth Pair settings updated.");
            return;
        }
        if (action === "ice-parse-append" || action === "ice-parse-replace") {
            var currentIce = [];
            try {
                currentIce = JSON.parse(getByPath(state.forms, "connectivity.iceServersText", "[]"));
            } catch (error) {
                currentIce = [];
            }
            var parseResult = await postJson("/api/rtc/parse", {
                text: getByPath(state.forms, "connectivity.iceImportText", ""),
                mode: action === "ice-parse-replace" ? "replace" : "append",
                existing: { iceServers: currentIce }
            });
            setByPath(state.forms, "connectivity.iceServersText", JSON.stringify(getByPath(parseResult, "config.iceServers", []), null, 2));
            setNotice("success", "Connection helpers updated from pasted input.");
            renderApp();
            return;
        }
        if (action === "save-ice") {
            var rawIce = getByPath(state.forms, "connectivity.iceServersText", "[]");
            var parsedIce = JSON.parse(rawIce);
            await patchConfig({ rtc: { iceServers: parsedIce } }, "Connection helper settings updated.");
            return;
        }
        if (action === "security-generate-password") {
            setSecurityPasswordDraft(generateRandomPassword(24));
            state.securityPasswordVisible = true;
            renderApp();
            setNotice("success", "Generated a new server password draft. Save it when ready.");
            return;
        }
        if (action === "security-toggle-password") {
            state.securityPasswordVisible = !state.securityPasswordVisible;
            renderApp();
            return;
        }
        if (action === "security-copy-password") {
            var passwordDraft = String(getByPath(state.forms, "security.new_password", "") || "");
            if (!passwordDraft) {
                throw new Error("Generate or enter a password before copying.");
            }
            try {
                await copyText(passwordDraft);
                setNotice("success", "Password copied.");
            } catch (error) {
                state.securityPasswordVisible = true;
                setNotice("info", "Clipboard blocked. Password selected; press Ctrl+C or Command+C to copy.");
                window.requestAnimationFrame(selectSecurityPasswordDraft);
            }
            return;
        }
        if (action === "security-save-baseline") {
            var changed = [];
            var desiredSecurityMode = getByPath(state.forms, "security.mode", "secure");
            var shouldUpdateMode = desiredSecurityMode !== savedSecurityMode();
            var desiredSecurityTier = normalizeSecurityTier(getByPath(state.forms, "security.tier", "B"));
            var shouldUpdateTier = desiredSecurityTier !== savedSecurityTier();
            var hasPasswordDraft = hasValue(getByPath(state.forms, "security.new_password", "")) || hasValue(getByPath(state.forms, "security.confirm_password", ""));
            if (hasPasswordDraft) {
                await saveSecurityPasswordSelection();
                changed.push("password");
            }
            if (shouldUpdateMode) {
                await saveSecurityModeSelection(desiredSecurityMode);
                changed.push("security mode");
            }
            if (shouldUpdateTier) {
                await saveSecurityTierSelection(desiredSecurityTier);
                changed.push("pairing tier");
            }
            if (!changed.length) {
                await refreshSetupSecurityStep();
                setNotice("success", "Security baseline already matches the saved settings. Continue to AI & Models when ready.");
                return;
            }
            setNotice("success", "Updated " + changed.join(" and ") + ". Next step: AI & Models.");
            return;
        }
        if (action === "security-save-mode") {
            await saveSecurityModeSelection();
            setNotice("success", "Security mode updated.");
            return;
        }
        if (action === "security-save-tier") {
            await saveSecurityTierSelection();
            setNotice("success", "Pairing security tier updated.");
            return;
        }
        if (action === "security-save-password") {
            await saveSecurityPasswordSelection();
            setNotice("success", "Password updated.");
            return;
        }
        if (action === "security-rotate-storage-key") {
            var storageRotation = await rotateSecureStorageKey();
            var storageStatus = getByPath(storageRotation, "secure_storage", {});
            setNotice("success", "Maximus storage key rotated; " + String(getByPath(storageStatus, "files_rekeyed", 0)) + " protected file(s) re-encrypted.");
            return;
        }
        if (action === "totp-generate") {
            var generated = await postJson("/admin/security/totp/generate", { target: "pairing" });
            showTotpSecretModal(generated, "Authenticator QR ready", "Scan this QR code with your authenticator app or copy the setup link.");
            await refreshBootstrap();
            return;
        }
        if (action === "totp-show-secret") {
            var shown = await postJson("/admin/security/totp/show", { target: "pairing" });
            showTotpSecretModal(shown, "Current authenticator QR", "This QR contains the shared pairing authenticator setup currently saved on the server.");
            return;
        }
        if (action === "totp-show-code") {
            await showTotpCodeModal();
            return;
        }
        if (action === "totp-import") {
            var imported = await postJson("/admin/security/totp/import", { value: getByPath(state.forms, "security.import_totp", ""), target: "pairing" });
            setByPath(state.forms, "security.import_totp", "");
            showTotpSecretModal(imported, "Authenticator imported", "The shared authenticator setup has been updated.");
            await refreshBootstrap();
            return;
        }
        if (action === "totp-delete") {
            await postJson("/admin/security/totp/delete", {});
            await refreshBootstrap("Authenticator setup deleted.");
            return;
        }
        if (action === "settings-export-qr") {
            var qrPayload = await requestJson("/api/settings/export-qr");
            setModal({
                title: "Set up your phone",
                description: "On iOS or Android, open Settings → Automatic Configuration → Scan QR. Confirm the setup, then tap Connect.",
                imageUrl: qrPayload.qr_url,
                helper: "Includes: " + (getByPath(qrPayload, "features.pairing_modes", []).map(function (mode) { return ({ cloud_pair: "Cloud Pair", local_pair: "Local Pair", bluetooth_pair: "Bluetooth Pair", auto_pair: "Auto Pair", otp: "Pair with OTP" })[mode] || ""; }).filter(Boolean).join(", ") || "computer settings") + ". Keep this QR private."
            });
            return;
        }
        if (action === "jailbreak-activate") {
            await requestJson("/api/jailbreak/activate", { method: "POST" });
            await ensureSecurityData(true);
            await ensureInstructions(true);
            setNotice("success", "Prompt Override enabled. The root prompt is now editable at runtime.");
            return;
        }
        if (action === "jailbreak-deactivate") {
            await requestJson("/api/jailbreak/deactivate", { method: "POST" });
            await ensureSecurityData(true);
            await ensureInstructions(true);
            setNotice("success", "Prompt Override disabled.");
            return;
        }
        if (action === "jailbreak-save") {
            await postJson("/api/jailbreak/prompt", { prompt: getByPath(state.forms, "agentWorkbench.jailbreak_prompt", "") });
            await ensureSecurityData(true);
            await ensureInstructions(true);
            setNotice("success", "Prompt Override saved.");
            return;
        }
        if (action === "agent-2fa-create") {
            var label = String(getByPath(state.forms, "agentSecurity.createLabel", "") || "").trim();
            if (!label) {
                setNotice("error", "Enter a label for the new 2FA profile.");
                return;
            }
            var created = await postJson("/api/agent-security/profiles", { label: label });
            state.agentSecurity.lastEnrolment = getByPath(created, "enrolment", null);
            state.agentSecurity.verifyError = null;
            state.agentSecurity.enrolmentVerified = false;
            setByPath(state.forms, "agentSecurity.createLabel", "");
            setByPath(state.forms, "agentSecurity.verifyCode", "");
            await loadAgentSecurityProfiles();
            setNotice("success", "2FA profile created. Scan the QR code, then verify a code to finish.");
            return;
        }
        if (action.indexOf("agent-2fa-verify:") === 0) {
            var verifyProfileId = action.slice("agent-2fa-verify:".length);
            var verifyCode = String(getByPath(state.forms, "agentSecurity.verifyCode", "") || "").trim();
            if (!/^[0-9]{6}$/.test(verifyCode)) {
                state.agentSecurity.verifyError = "Enter the 6-digit code from your authenticator app.";
                state.agentSecurity.enrolmentVerified = false;
                renderApp();
                return;
            }
            var verifyResult = await postJson("/api/agent-security/profiles/" + encodeURIComponent(verifyProfileId) + "/verify", { code: verifyCode });
            if (!getByPath(verifyResult, "verified", false)) {
                state.agentSecurity.verifyError = "That code didn't match. Check your device's clock and try the next code.";
                state.agentSecurity.enrolmentVerified = false;
                renderApp();
                return;
            }
            // Confirmed correct - unlock Done, but keep the secret on screen until
            // the admin explicitly clicks Done. No auto-finish on verify.
            state.agentSecurity.verifyError = null;
            state.agentSecurity.enrolmentVerified = true;
            renderApp();
            setNotice("success", "Code verified. Click Done to finish.");
            return;
        }
        if (action.indexOf("agent-2fa-finish:") === 0) {
            var finishProfileId = action.slice("agent-2fa-finish:".length);
            if (!state.agentSecurity.enrolmentVerified || getByPath(state.agentSecurity.lastEnrolment, "profile_id", "") !== finishProfileId) {
                setNotice("error", "Verify the code first - it has to match before you can finish.");
                return;
            }
            clearAgentSecurityEnrolment();
            setByPath(state.forms, "agentSecurity.verifyCode", "");
            await loadAgentSecurityProfiles();
            setNotice("success", "Authenticator verified. The setup secret has been cleared from this session.");
            return;
        }
        if (action.indexOf("agent-2fa-discard:") === 0) {
            var discardProfileId = action.slice("agent-2fa-discard:".length);
            if (!window.confirm("Discard this 2FA profile? You'll need to scan a new QR code if you continue.")) {
                return;
            }
            await postJson("/api/agent-security/profiles/" + encodeURIComponent(discardProfileId) + "/wipe", {});
            clearAgentSecurityEnrolment();
            setByPath(state.forms, "agentSecurity.verifyCode", "");
            await loadAgentSecurityProfiles();
            setNotice("info", "2FA profile discarded.");
            return;
        }
        if (action.indexOf("agent-2fa-assign:") === 0) {
            var assignId = action.slice("agent-2fa-assign:".length);
            var agentName = String(getByPath(state.forms, "agentSecurity.assign_" + assignId, "") || "").trim();
            if (!agentName) {
                setNotice("error", "Choose an agent website to assign this profile to.");
                return;
            }
            await postJson("/api/agent-security/profiles/" + encodeURIComponent(assignId) + "/assign", { agent_name: agentName });
            setByPath(state.forms, "agentSecurity.assign_" + assignId, "");
            await loadAgentSecurityProfiles();
            setNotice("success", "Profile assigned to " + agentName + ".");
            return;
        }
        if (action.indexOf("agent-2fa-unassign:") === 0) {
            var unassignAgentName = action.slice("agent-2fa-unassign:".length);
            if (!window.confirm("Remove the 2FA requirement for \"" + unassignAgentName + "\"?")) {
                return;
            }
            await postJson("/api/agent-security/agents/" + encodeURIComponent(unassignAgentName) + "/wipe", {});
            await loadAgentSecurityProfiles();
            setNotice("success", "2FA requirement removed from " + unassignAgentName + ".");
            return;
        }
        if (action.indexOf("agent-2fa-wipe-profile:") === 0) {
            var wipeId = action.slice("agent-2fa-wipe-profile:".length);
            if (!window.confirm("Wipe this 2FA profile? Every agent website using it will lose its 2FA requirement, and you'll need to re-enrol from scratch.")) {
                return;
            }
            await postJson("/api/agent-security/profiles/" + encodeURIComponent(wipeId) + "/wipe", {});
            await loadAgentSecurityProfiles();
            setNotice("success", "2FA profile wiped.");
            return;
        }
    }

    document.addEventListener("click", function (event) {
        var actionTarget = event.target.closest("[data-action]");
        if (!actionTarget || !root || !root.contains(actionTarget)) {
            return;
        }
        event.preventDefault();
        syncBoundControls();
        var action = actionTarget.getAttribute("data-action");
        var runner = isImmediateAction(action)
            ? handleAction(action, actionTarget)
            : withPendingAction(action, function () {
                return handleAction(action, actionTarget);
            });
        runner.catch(function (error) {
            setNotice("error", error.message || String(error));
        });
    });

    document.addEventListener("click", function (event) {
        var target = event.target;
        if (!state.profileMenuOpen || !root || !root.contains(target)) {
            return;
        }
        if (target.closest(".ayu-sidebar-profile")) {
            return;
        }
        state.profileMenuOpen = false;
        renderApp();
    });

    function maybeRerenderForPath(path) {
        return [
            "aiProvider.provider",
            "aiProvider.use_google_api",
            "modelBehavior.mode",
            "overview.adminTheme",
            "overview.softwareUpdatesEnabled",
            "page.custom_forward_enabled",
            "page.remote_access_role",
            "page.admin_frontend_enabled",
            "page.newWebsite.websocket_enabled",
            "page.agentWebsitesSecurity.disable_otp",
            "agentWorkbench.frontend_stack",
            "agentWorkbench.backend_stack",
            "security.mode",
            "speech.tts_provider",
            "videoCall.audio_microphone",
            "videoCall.outbound_camera",
            "videoCall.outbound_remote_desktop",
            "videoCall.outbound_api",
            "videoCall.outbound_video_file",
            "videoCall.record_my_video",
            "videoCall.record_audio_only_calls",
            "videoCall.recording_mode",
            "videoCall.silent_recording_enabled",
            "videoCall.remote_desktop.enabled",
            "videoCall.remote_desktop.send_screen"
        ].indexOf(path) !== -1 || path.indexOf("page.advertisedWebsites.") === 0;
    }

    function syncVirtualBind(target) {
        var virtual = target.getAttribute("data-virtual-bind");
        if (!virtual) {
            return;
        }
        if (virtual === "aiLibrary.query") {
            state.aiLibrary.query = target.value;
        }
    }

    function syncBoundControl(target) {
        syncVirtualBind(target);
        if (target.hasAttribute("data-bind")) {
            setByPath(state.forms, target.getAttribute("data-bind"), target.type === "checkbox" ? target.checked : target.value);
        }
    }

    function syncBoundControls() {
        if (!root) {
            return;
        }
        Array.prototype.forEach.call(root.querySelectorAll("[data-bind], [data-virtual-bind]"), syncBoundControl);
    }

    document.addEventListener("input", function (event) {
        var target = event.target;
        if (!root || !root.contains(target)) {
            return;
        }
        if (target.getAttribute("data-role") === "chat-input") {
            state.chat.composer = target.value;
            return;
        }
        if (target.getAttribute("data-role") === "chat-search") {
            state.chat.search = target.value;
            renderApp({ passive: true });
            return;
        }
        if (target.getAttribute("data-action") === "cropper-zoom-slider") {
            if (state.profileCropper) {
                state.profileCropper.zoom = Math.max(state.profileCropper.minZoom, Math.min(state.profileCropper.maxZoom, parseFloat(target.value) || 1.0));
                updateCropperZoomUi();
                drawCropperCanvas();
            }
            return;
        }
        if (target.getAttribute("data-role") === "chat-title-input") {
            state.chat.titleDraft = target.value;
            return;
        }
        syncBoundControl(target);
    });

    document.addEventListener("compositionstart", function (event) {
        var target = event.target;
        if (!root || !root.contains(target)) {
            return;
        }
        renderState.composing = true;
    });

    document.addEventListener("compositionend", function (event) {
        var target = event.target;
        renderState.composing = false;
        if (root && root.contains(target)) {
            syncBoundControl(target);
        }
        flushPassiveRenderSoon();
    });

    function releaseAdminPointer() {
        if (renderState.pointerTimer) {
            window.clearTimeout(renderState.pointerTimer);
            renderState.pointerTimer = null;
        }
        if (!renderState.pointerDown) {
            return;
        }
        renderState.pointerDown = false;
        // The click event fires right after pointerup, so flush on the next tick.
        flushPassiveRenderSoon();
    }

    document.addEventListener("pointerdown", function (event) {
        if (!root || !root.contains(event.target)) {
            return;
        }
        renderState.pointerDown = true;
        window.clearTimeout(renderState.pointerTimer);
        // Watchdog: never let a missed pointerup freeze live updates.
        renderState.pointerTimer = window.setTimeout(releaseAdminPointer, 5000);
    }, true);
    document.addEventListener("pointerup", releaseAdminPointer, true);
    document.addEventListener("pointercancel", releaseAdminPointer, true);
    document.addEventListener("dragend", releaseAdminPointer, true);
    window.addEventListener("blur", releaseAdminPointer);

    document.addEventListener("focusout", function (event) {
        if (!root || !root.contains(event.target)) {
            return;
        }
        flushPassiveRenderSoon();
    });

    document.addEventListener("change", function (event) {
        var target = event.target;
        if (!root || !root.contains(target)) {
            return;
        }
        if (target.getAttribute("data-role") === "chat-file-input") {
            var chatFiles = target.files && target.files.length ? target.files : null;
            target.value = "";
            if (chatFiles) {
                chatReadSelectedFiles(chatFiles).catch(function (error) {
                    setNotice("error", error.message || "Could not attach the selected files.");
                });
            }
            return;
        }
        if (target.getAttribute("data-role") === "profile-image-input") {
            var selectedFile = target.files && target.files[0] ? target.files[0] : null;
            target.value = "";
            if (!selectedFile) {
                return;
            }
            if (String(selectedFile.type || "").toLowerCase() === "image/gif" || /\.gif$/i.test(selectedFile.name || "")) {
                state.profileMenuOpen = false;
                state.profileCropper = null;
                withPendingAction("profile-image-upload", function () {
                    return uploadProfileImage(selectedFile);
                }).catch(function (error) {
                    setNotice("error", error.message || String(error));
                });
                return;
            }
            openProfileCropper(selectedFile);

            return;
        }
        if (target.getAttribute("data-role") === "video-file-input") {
            var selectedVideoFile = target.files && target.files[0] ? target.files[0] : null;
            target.value = "";
            if (!selectedVideoFile) {
                return;
            }
            withPendingAction("video-file-select", function () {
                return uploadVideoFile(selectedVideoFile);
            }).catch(function (error) {
                setNotice("error", error.message || String(error));
            });
            return;
        }
        if (target.getAttribute("data-role") === "desktop-pack-input") {
            var desktopPackFile = target.files && target.files[0] ? target.files[0] : null;
            target.value = "";
            if (!desktopPackFile) {
                return;
            }
            withPendingAction("desktop-pack-import", function () {
                return importDesktopAssetBundle(desktopPackFile);
            }).catch(function (error) {
                setNotice("error", error.message || "Could not import the desktop asset ZIP.");
            });
            return;
        }
        syncBoundControl(target);
        if (target.getAttribute("data-bind") === "desktopAssets.agent_name") {
            activateDesktopAssetAgent(target.value);
            renderApp();
            return;
        }
        if (target.hasAttribute("data-bind")) {
            var path = target.getAttribute("data-bind");
            var value = getByPath(state.forms, path, "");
            if (path === "modelBehavior.mode") {
                syncModelBehaviorFormFromResponse(state.aiLibrary.behavior);
            }
            if (path === "overview.adminTheme") {
                applyTheme(value);
            }
            if (maybeRerenderForPath(path)) {
                renderApp();
            }
        }
        if (target.getAttribute("data-action") === "ai-source") {
            state.aiLibrary.source = target.value;
        }
    });

    document.addEventListener("keydown", function (event) {
        if (event.key === "Enter" && !event.shiftKey && !event.isComposing && event.target && event.target.getAttribute("data-role") === "chat-input") {
            event.preventDefault();
            withPendingAction("chat-send", sendChatTurn).catch(function (error) {
                setNotice("error", error.message || String(error));
            });
            return;
        }
        if (event.target && event.target.getAttribute && event.target.getAttribute("data-role") === "chat-title-input" && !event.isComposing) {
            if (event.key === "Enter") {
                event.preventDefault();
                withPendingAction("chat-title-save", function () { return chatSaveTitle(false); }).catch(function (error) {
                    setNotice("error", error.message || "The name could not be saved.");
                });
                return;
            }
            if (event.key === "Escape") {
                event.preventDefault();
                chatCancelRename();
                return;
            }
        }
        if (event.key !== "Escape") {
            return;
        }
        if (state.profileCropper) {
            state.profileCropper = null;
            renderApp();
            return;
        }
        if (state.modal) {
            setModal(null);
            return;
        }
        if (state.navOpen) {
            state.navOpen = false;
            renderApp();
        }
    });

    async function boot() {
        if (!root) {
            return;
        }
        renderApp();
        try {
            await refreshBootstrap();
        } catch (error) {
            root.innerHTML = "<div class=\"ayu-screen-loading\"><div class=\"ayu-loading-card\"><h1>Admin shell failed to load</h1><p>" + escapeHtml(error.message || String(error)) + "</p><p><a href=\"/\">Retry admin shell</a></p></div></div>";
            return;
        }
        if (state.screen === "ai") {
            ensureAiData(false);
        }
        if (!onboardingCompleted()) {
            ensureSetupData(false);
        }
    }

    boot();
}());

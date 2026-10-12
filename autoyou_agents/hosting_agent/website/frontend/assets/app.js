(function () {
  "use strict";

  var bootstrap = window.__BOOTSTRAP__ || {};
  var tokenKey = "autoyou.hosting_agent.token";
  var sessionToken = "";
  try {
    sessionToken = localStorage.getItem(tokenKey) || "";
  } catch (_) {}

  var els = {};
  var data = null;
  // The choice the user has made but not saved yet. render() rebuilds the
  // <select> from `data`, so without this the change handler's own re-render
  // reset the dropdown to the server's saved agent on every pick - the
  // selection snapped back and Save then posted the unchanged value.
  var pendingAgentName = null;

  function $(id) {
    return document.getElementById(id);
  }

  function text(value) {
    return String(value == null ? "" : value);
  }

  function escapeHtml(value) {
    return text(value)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;")
      .replace(/'/g, "&#39;");
  }

  function requestHeaders() {
    var headers = { "Content-Type": "application/json" };
    if (sessionToken) {
      headers.Authorization = "Bearer " + sessionToken;
    }
    return headers;
  }

  async function getJson(path) {
    var response = await fetch(path, { headers: requestHeaders() });
    var payload = await response.json();
    if (!response.ok) {
      var getError = new Error(payload.error || "Request failed.");
      getError.planRequired = Boolean(payload.plan_required);
      throw getError;
    }
    return payload;
  }

  async function postJson(path, body) {
    var response = await fetch(path, {
      method: "POST",
      headers: requestHeaders(),
      body: JSON.stringify(body || {})
    });
    var payload = await response.json();
    if (!response.ok || payload.success === false) {
      var postError = new Error(payload.error || "Request failed.");
      postError.planRequired = Boolean(payload.plan_required);
      throw postError;
    }
    return payload;
  }

  function isAuthed() {
    return Boolean(bootstrap.auth && bootstrap.auth.authenticated) || Boolean(sessionToken);
  }

  function showLogin(message) {
    els.loginGate.classList.remove("hidden");
    els.mainView.classList.add("hidden");
    if (message) {
      els.otpError.textContent = message;
      els.otpError.classList.remove("hidden");
    }
  }

  function showMain() {
    els.loginGate.classList.add("hidden");
    els.mainView.classList.remove("hidden");
  }

  function selectedAgentName() {
    if (pendingAgentName !== null) {
      return pendingAgentName;
    }
    return text((data && data.agent_name) || (data && data.selected && data.selected.agent_name) || "");
  }

  function selectedOption() {
    var selectedName = selectedAgentName();
    var options = (data && data.options) || [];
    for (var i = 0; i < options.length; i += 1) {
      if (text(options[i].agent_name) === selectedName) {
        return options[i];
      }
    }
    return null;
  }

  function render() {
    if (!data) {
      return;
    }
    var options = Array.isArray(data.options) ? data.options : [];
    var selectedName = selectedAgentName();
    els.websiteSelect.innerHTML = options.length
      ? options.map(function (option) {
          var name = text(option.agent_name);
          return "<option value=\"" + escapeHtml(name) + "\"" + (name === selectedName ? " selected" : "") + ">" + escapeHtml(option.title || name) + "</option>";
        }).join("")
      : "<option value=\"\">No websites ready</option>";
    els.websiteSelect.disabled = !options.length;
    els.enabledToggle.checked = Boolean(data.enabled);
    els.autostartToggle.checked = Boolean(data.auto_start_on_boot);

    var selected = selectedOption() || data.selected || {};
    els.selectedTitle.textContent = selected.title || "Choose a website";
    els.selectedDescription.textContent = selected.description || "Your link works while AutoYou is open on this computer.";

    var publicUrl = text(data.public_website_url || data.public_url || "");
    if (publicUrl) {
      els.publicUrl.textContent = publicUrl;
      els.publicUrl.href = publicUrl;
      els.publicUrl.removeAttribute("aria-disabled");
    } else {
      els.publicUrl.textContent = "Not running yet";
      els.publicUrl.href = "#";
      els.publicUrl.setAttribute("aria-disabled", "true");
    }

    var status = text(data.status || "stopped").toLowerCase();
    els.statusChip.className = "chip";
    if (data.enabled && publicUrl) {
      els.statusChip.textContent = "Public link is on";
      els.statusChip.classList.add("on");
    } else if (data.enabled || status === "starting") {
      els.statusChip.textContent = "Ready to turn on";
      els.statusChip.classList.add("waiting");
    } else {
      els.statusChip.textContent = "Public website is off";
    }

    els.saveBtn.disabled = !options.length;
    els.startBtn.disabled = !options.length;
    els.emptyNote.classList.toggle("hidden", Boolean(options.length));
    els.websiteList.innerHTML = options.length ? options.map(function (option) {
      var name = text(option.agent_name);
      var isSelected = name === selectedName;
      return "<div class=\"website-row\"><div><strong>" + escapeHtml(option.title || name) + "</strong><small>" + escapeHtml(option.description || option.public_path || "/agent/" + name + "/") + "</small></div><span class=\"chip" + (isSelected ? " on" : "") + "\">" + (isSelected ? "Selected" : "Ready") + "</span></div>";
    }).join("") : "<div class=\"note\">Install or enable a website in Agents to publish it here.</div>";
  }

  async function load() {
    if (!isAuthed()) {
      showLogin("");
      return;
    }
    showMain();
    try {
      data = await getJson("./api/website-hosting");
      pendingAgentName = null;
      render();
    } catch (error) {
      if (String(error.message || "").toLowerCase().indexOf("authenticated") !== -1) {
        showLogin("");
      } else {
        els.statusChip.textContent = error.message || "Load failed";
        els.statusChip.className = "chip waiting";
      }
    }
  }

  async function login() {
    var code = text(els.otpInput.value).trim();
    els.otpError.classList.add("hidden");
    try {
      var payload = await postJson("./api/auth/login", { code: code });
      sessionToken = text(payload.token || "");
      if (sessionToken) {
        try {
          localStorage.setItem(tokenKey, sessionToken);
        } catch (_) {}
      }
      bootstrap.auth = { authenticated: true };
      els.otpInput.value = "";
      await load();
    } catch (error) {
      els.otpError.textContent = error.message || "Invalid code.";
      els.otpError.classList.remove("hidden");
    }
  }

  async function save(start, enabledOverride) {
    if (!data) {
      return;
    }
    var enabled = enabledOverride == null ? els.enabledToggle.checked : enabledOverride;
    try {
      data = await postJson("./api/website-hosting", {
        agent_name: selectedAgentName(),
        enabled: enabled,
        auto_start_on_boot: els.autostartToggle.checked,
        start: Boolean(start)
      });
      // The server echoed back what it stored, so the pending choice is now
      // the saved one. A failed save deliberately keeps it, so the user does
      // not lose their pick along with the error.
      pendingAgentName = null;
      render();
    } catch (error) {
      els.statusChip.textContent = error.planRequired
        ? "Choose the Public Proxy plan to turn this on."
        : (error.message || "Save failed");
      els.statusChip.className = "chip waiting";
    }
  }

  function bind() {
    els.loginGate = $("login-gate");
    els.mainView = $("main-view");
    els.otpInput = $("otp-input");
    els.otpSubmit = $("otp-submit");
    els.otpError = $("otp-error");
    els.statusChip = $("status-chip");
    els.refreshBtn = $("refresh-btn");
    els.websiteSelect = $("website-select");
    els.enabledToggle = $("enabled-toggle");
    els.autostartToggle = $("autostart-toggle");
    els.saveBtn = $("save-btn");
    els.startBtn = $("start-btn");
    els.offBtn = $("off-btn");
    els.selectedTitle = $("selected-title");
    els.selectedDescription = $("selected-description");
    els.publicUrl = $("public-url");
    els.websiteList = $("website-list");
    els.emptyNote = $("empty-note");

    els.otpSubmit.addEventListener("click", login);
    els.otpInput.addEventListener("keydown", function (event) {
      if (event.key === "Enter") {
        login();
      }
    });
    els.refreshBtn.addEventListener("click", load);
    els.websiteSelect.addEventListener("change", function () {
      pendingAgentName = els.websiteSelect.value;
      render();
    });
    els.saveBtn.addEventListener("click", function () { save(false, null); });
    els.startBtn.addEventListener("click", function () { save(true, true); });
    els.offBtn.addEventListener("click", function () { save(false, false); });
  }

  document.addEventListener("DOMContentLoaded", function () {
    bind();
    load();
  });
})();

'use strict';

var bootstrap = window.__BOOTSTRAP__ || {};
var agentName = bootstrap.agent_name || 'win_security_agent';
var auth = bootstrap.auth || {};
var state = {snapshot: null, overview: null, view: 'summary', refreshTimer: null, busy: false, mapFeatures: null, mapLoading: false, selectedPeerKey: '', focusProcessKey: ''};

function $(id) { return document.getElementById(id); }
function escapeHtml(value) {
  return String(value == null ? '' : value).replace(/[&<>'"]/g, function(ch) {
    return {'&':'&amp;','<':'&lt;','>':'&gt;',"'":'&#39;','"':'&quot;'}[ch];
  });
}
function storedToken() { try { return localStorage.getItem('autoyou_chat_token_' + agentName) || ''; } catch (_) { return ''; } }
function saveToken(token) { try { if (token) localStorage.setItem('autoyou_chat_token_' + agentName, token); else localStorage.removeItem('autoyou_chat_token_' + agentName); } catch (_) {} }
function headers() { var token = storedToken(); return token ? {'Authorization': 'Bearer ' + token} : {}; }
function requestJson(path, options) {
  var opts = Object.assign({cache: 'no-store'}, options || {});
  opts.headers = Object.assign(headers(), opts.headers || {});
  return fetch(path, opts).then(function(response) {
    return response.text().then(function(text) {
      var data = {};
      try { data = text ? JSON.parse(text) : {}; } catch (_) { data = {error: text || 'Invalid server response'}; }
      if (!response.ok) throw new Error(data.error || ('Request failed (' + response.status + ')'));
      return data;
    });
  });
}
function formatCount(value) { return Number(value || 0).toLocaleString(); }
function protocolTags(protocols) { return Object.keys(protocols || {}).map(function(key) { return '<span class="tag">' + escapeHtml(key.toUpperCase()) + ' ' + formatCount(protocols[key]) + '</span>'; }).join(''); }
function processKey(row) { return [row.process_id || 0, row.process_name || '<unknown>', row.executable_path || '', row.user_name || ''].join('|'); }
function peerKey(row) { return [row.remote_address || '', row.remote_port || 0].join('|'); }

function setTheme(theme) {
  document.documentElement.dataset.theme = theme;
  try { localStorage.setItem('autoyou.ui.theme', theme); } catch (_) {}
}
function initTheme() {
  var current = document.documentElement.dataset.theme || 'dark';
  setTheme(current);
  $('theme-toggle').addEventListener('click', function() { current = current === 'dark' ? 'light' : 'dark'; setTheme(current); });
}

function showDashboard() {
  $('auth-chip').textContent = 'Authenticated';
  $('auth-chip').className = 'status-chip authenticated';
  $('login-gate').classList.add('hidden');
  $('dashboard').classList.remove('hidden');
  loadSnapshot();
  loadHistory();
  loadBios();
  loadMapData();
  if (!state.refreshTimer) state.refreshTimer = setInterval(function() { loadSnapshot(); loadHistory(); }, 5000);
}
function showLogin(message) {
  $('auth-chip').textContent = 'Locked';
  $('auth-chip').className = 'status-chip';
  $('login-gate').classList.remove('hidden');
  $('dashboard').classList.add('hidden');
  if (message) $('otp-error').textContent = message;
}
function login() {
  var input = $('otp-input'), button = $('otp-submit'), code = input.value.trim();
  if (!/^\d{6}$/.test(code)) { $('otp-error').textContent = 'Enter the 6-digit authenticator code.'; return; }
  button.disabled = true;
  requestJson('./api/auth/login', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({code: code})})
    .then(function(data) { saveToken(data.token || ''); showDashboard(); })
    .catch(function(error) { $('otp-error').textContent = error.message; input.value = ''; input.focus(); })
    .finally(function() { button.disabled = false; });
}
function initAuth() {
  if (auth.authenticated) { showDashboard(); return; }
  if (storedToken()) {
    requestJson('./api/auth/status').then(function(data) { if (data.auth && data.auth.authenticated) showDashboard(); else { saveToken(''); showLogin(); } }).catch(function() { saveToken(''); showLogin(); });
  } else showLogin();
}

function fallbackOverview(data) {
  var groups = {}, peers = {};
  (data.connections || []).forEach(function(row) {
    var pKey = processKey(row), p = groups[pKey] || {key: pKey, process_id: row.process_id || 0, process_name: row.process_name || '<unknown>', executable_path: row.executable_path || '', user_name: row.user_name || '', total: 0, protocols: {}, states: {}, remote_connections: 0, peer_keys: []};
    p.total += 1; p.protocols[row.protocol] = (p.protocols[row.protocol] || 0) + 1; p.states[row.state] = (p.states[row.state] || 0) + 1;
    if (row.remote_address && row.remote_scope !== 'none') {
      p.remote_connections += 1;
      var key = peerKey(row), peer = peers[key] || {key: key, remote_address: row.remote_address, remote_port: row.remote_port || 0, remote_service_hint: row.remote_service_hint || '', remote_scope: row.remote_scope, connections: 0, protocols: {}, processes: [], states: {}, directions: {}, sources: {}, policy_keys: [], geo: null, mapped: false, correlations: []};
      if (p.peer_keys.indexOf(key) < 0) p.peer_keys.push(key);
      peer.connections += 1; peer.protocols[row.protocol] = (peer.protocols[row.protocol] || 0) + 1; peer.states[row.state] = (peer.states[row.state] || 0) + 1;
      if (row.direction) peer.directions[row.direction] = (peer.directions[row.direction] || 0) + 1;
      peer.sources[row.source || 'endpoint_table'] = (peer.sources[row.source || 'endpoint_table'] || 0) + 1;
      if (peer.policy_keys.indexOf(row.policy_key || key) < 0) peer.policy_keys.push(row.policy_key || key);
      var processRef = peer.processes.filter(function(item) { return item.key === pKey; })[0];
      if (!processRef) { processRef = {key: pKey, process_id: p.process_id, process_name: p.process_name, count: 0}; peer.processes.push(processRef); }
      processRef.count += 1; peers[key] = peer; p.peer_keys.sort();
    }
    groups[pKey] = p;
  });
  Object.keys(peers).forEach(function(key) { var peer = peers[key], labels = []; if (peer.remote_scope === 'public') labels.push('public peer'); if (peer.processes.length > 1) labels.push('shared across processes'); if (peer.remote_service_hint) labels.push(peer.remote_service_hint); if (Object.keys(peer.protocols).length > 1) labels.push('TCP + UDP'); peer.correlations = labels; });
  return {process_groups: Object.keys(groups).map(function(key) { return groups[key]; }).sort(function(a, b) { return b.total - a.total; }), remote_peers: Object.keys(peers).map(function(key) { return peers[key]; }).sort(function(a, b) { return b.connections - a.connections; }), mapped_peers: 0, public_peer_ips: [], unmapped_public_ips: [], correlation_note: 'Algorithmic links combine process, protocol, port, connection state, provider source, and cached IP intelligence.'};
}
function currentOverview(data) { return data.overview || fallbackOverview(data); }

function renderSummary(data) {
  var summary = data.summary || {}, capabilities = data.capabilities || {}, overview = state.overview;
  $('metric-total').textContent = formatCount(summary.total); $('metric-tcp').textContent = formatCount(summary.tcp); $('metric-udp').textContent = formatCount(summary.udp); $('metric-remote').textContent = formatCount(summary.remote);
  var limited = !data.elevated || capabilities.udp_remote_process_attribution === 'requires_wfp_or_etw_provider';
  $('elevation-banner').classList.toggle('hidden', !limited);
  $('capability-note').textContent = !data.elevated ? 'Run AutoYou elevated for protected process metadata; remote UDP attribution still requires the signed WFP/ETW provider.' : 'Remote UDP peer-to-process attribution requires the optional signed WFP/ETW provider.';
  $('process-count').textContent = formatCount((data.processes || []).length); $('connection-count').textContent = formatCount((data.connections || []).length); $('snapshot-status').textContent = 'Updated ' + new Date(data.collected_at).toLocaleTimeString();
  $('summary-process-count').textContent = formatCount(overview.process_groups.length); $('summary-peer-count').textContent = formatCount(overview.remote_peers.length); $('peer-count').textContent = formatCount(overview.remote_peers.length);
  renderSummaryProcesses(overview.process_groups.slice(0, 6)); renderProcessGroups(overview.process_groups); renderPeers('summary-peer-list', overview.remote_peers.slice(0, 6)); renderPeers('remote-peer-list', overview.remote_peers); renderMap(overview.remote_peers);
}
function processGroupMarkup(group) {
  var peerDetails = (group.peer_keys || []).map(function(key) { var peer = state.overview.remote_peers.filter(function(item) { return item.key === key; })[0]; return peer ? '<li><button class="link-button" type="button" data-select-peer="' + escapeHtml(peer.key) + '">' + escapeHtml(peer.remote_address) + ':' + escapeHtml(peer.remote_port) + '</button><span>' + escapeHtml(Object.keys(peer.protocols || {}).join(' + ').toUpperCase()) + ' · ' + formatCount(peer.connections) + ' flows</span></li>' : ''; }).join('');
  return '<details class="process-group" data-process-key="' + escapeHtml(group.key) + '"><summary><span class="process-summary-main"><strong>' + escapeHtml(group.process_name) + '</strong><small>PID ' + escapeHtml(group.process_id) + (group.user_name ? ' · ' + escapeHtml(group.user_name) : '') + '</small></span><span class="process-summary-stats"><strong>' + formatCount(group.total) + '</strong><small>' + formatCount(group.remote_connections) + ' remote</small><span>' + protocolTags(group.protocols) + '</span></span></summary><div class="process-group-body"><p class="process-path">' + escapeHtml(group.executable_path || 'Executable path unavailable') + '</p>' + (peerDetails ? '<ul class="nested-peer-list">' + peerDetails + '</ul>' : '<div class="empty-state">No remote peers in this snapshot.</div>') + '</div></details>';
}
function renderSummaryProcesses(groups) { $('summary-process-list').innerHTML = groups.length ? groups.map(function(group) { return '<button class="compact-process" type="button" data-focus-process="' + escapeHtml(group.key) + '"><span><strong>' + escapeHtml(group.process_name) + '</strong><small>PID ' + escapeHtml(group.process_id) + '</small></span><b>' + formatCount(group.total) + '</b></button>'; }).join('') : '<div class="empty-state">No process data yet.</div>'; }
function renderProcessGroups(groups) { $('process-list').innerHTML = groups.length ? groups.map(processGroupMarkup).join('') : '<div class="empty-state">No process-owned endpoints found.</div>'; }
function peerMarkup(peer) {
  var protocols = Object.keys(peer.protocols || {}), geo = peer.geo && peer.geo.location ? peer.geo.location : {}, location = [geo.city, geo.state, geo.country].filter(Boolean).join(', ');
  return '<details class="peer-group" data-peer-key="' + escapeHtml(peer.key) + '"><summary><span class="peer-summary-main"><strong>' + escapeHtml(peer.remote_address) + ':' + escapeHtml(peer.remote_port) + '</strong><small>' + escapeHtml(location || (peer.remote_scope || 'unknown') + ' location') + '</small></span><span class="peer-summary-stats"><span>' + protocols.map(function(item) { return '<span class="protocol ' + escapeHtml(item) + '">' + escapeHtml(item.toUpperCase()) + '</span>'; }).join(' ') + '</span><b>' + formatCount(peer.connections) + '</b></summary><div class="peer-group-body"><div class="tag-row">' + (peer.correlations || []).map(function(label) { return '<span class="tag">' + escapeHtml(label) + '</span>'; }).join('') + '</div><div class="peer-processes">' + (peer.processes || []).map(function(item) { return '<span>' + escapeHtml(item.process_name) + ' · ' + formatCount(item.count) + '</span>'; }).join('') + '</div><div class="peer-actions"><button class="lookup-button" type="button" data-select-peer="' + escapeHtml(peer.key) + '">Open details</button>' + (peer.remote_scope === 'public' ? '<button class="lookup-button" type="button" data-inspect-ip="' + escapeHtml(peer.remote_address) + '">Inspect IP</button>' : '') + '</div></div></details>';
}
function renderPeers(id, peers) { $(id).innerHTML = peers.length ? peers.map(peerMarkup).join('') : '<div class="empty-state">No remote peers yet.</div>'; }
function projectMapPoint(coordinate) { var lon = Number(coordinate[0]), lat = Number(coordinate[1]); return [(lon + 180) / 360 * 1000, (90 - lat) / 180 * 460]; }
function ringPath(ring) { return ring.map(function(coordinate, index) { var point = projectMapPoint(coordinate); return (index ? 'L' : 'M') + point[0].toFixed(2) + ' ' + point[1].toFixed(2); }).join('') + 'Z'; }
function geoJsonPath(geometry) {
  if (!geometry) return '';
  if (geometry.type === 'Polygon') return geometry.coordinates.map(ringPath).join('');
  if (geometry.type === 'MultiPolygon') return geometry.coordinates.map(function(polygon) { return polygon.map(ringPath).join(''); }).join('');
  return '';
}
function renderEarthBoundaries() {
  if (!state.mapFeatures || !$('map-boundaries')) return;
  $('map-boundaries').innerHTML = state.mapFeatures.map(function(feature) { var name = feature.properties && (feature.properties.NAME_EN || feature.properties.NAME || feature.properties.ADMIN) || 'Country'; return '<path d="' + geoJsonPath(feature.geometry) + '"><title>' + escapeHtml(name) + '</title></path>'; }).join('');
}
function loadMapData() {
  if (state.mapFeatures || state.mapLoading) return;
  state.mapLoading = true;
  requestJson('./api/map/boundaries').then(function(data) { state.mapFeatures = data.features || []; renderEarthBoundaries(); if (state.overview) renderMap(state.overview.remote_peers); }).catch(function(error) { $('map-status').textContent = error.message; }).finally(function() { state.mapLoading = false; });
}
function renderMap(peers) {
  var mapped = peers.filter(function(peer) { return peer.mapped && peer.geo && peer.geo.location; }), points = mapped.map(function(peer) { var location = peer.geo.location, lon = Number(location.longitude), lat = Number(location.latitude); if (!isFinite(lon) || !isFinite(lat)) return ''; var x = Math.max(40, Math.min(960, (lon + 180) / 360 * 1000)), y = Math.max(28, Math.min(432, (90 - lat) / 180 * 460)); return '<g class="map-marker" data-map-peer="' + escapeHtml(peer.key) + '"><circle cx="' + x.toFixed(1) + '" cy="' + y.toFixed(1) + '" r="7"><title>' + escapeHtml(peer.remote_address + ':' + peer.remote_port) + '</title></circle><text x="' + (x + 11).toFixed(1) + '" y="' + (y + 4).toFixed(1) + '">' + escapeHtml(peer.remote_address) + '</text></g>'; }).join('');
  if (state.mapFeatures) renderEarthBoundaries(); $('map-points').innerHTML = points || '<text class="map-empty" x="500" y="230" text-anchor="middle">Map public peers to add geographic signals</text>'; $('map-count').textContent = formatCount(mapped.length) + ' mapped'; $('map-status').textContent = state.mapFeatures ? (mapped.length ? formatCount(mapped.length) + ' cached locations' : 'Earth boundaries loaded; map public peers to add locations') : 'Loading Earth boundaries...';
}
function renderHistory(data) {
  var history = data.process_history || [], snapshots = data.snapshots || [];
  if (history.length) { $('history-list').innerHTML = history.slice(0, 5).map(function(item) { var max = Math.max.apply(null, item.samples.map(function(sample) { return sample.total; }).concat([1])); var bars = item.samples.slice(-8).map(function(sample) { return '<i style="height:' + Math.max(12, sample.total / max * 42) + 'px" title="' + escapeHtml(sample.collected_at) + '"></i>'; }).join(''); return '<div class="history-item"><div><strong>' + escapeHtml(item.process_name) + '</strong><small>' + formatCount(item.current_total) + ' endpoints now</small></div><span class="history-bars">' + bars + '</span></div>'; }).join(''); return; }
  $('history-list').innerHTML = snapshots.length ? snapshots.slice(0, 5).map(function(item) { return '<div class="history-item"><div><strong>Snapshot #' + escapeHtml(item.id) + '</strong><small>' + formatCount(item.total_connections) + ' endpoints</small></div><small>' + escapeHtml(new Date(item.collected_at).toLocaleTimeString()) + '</small></div>'; }).join('') : '<div class="empty-state">History appears after the first snapshot.</div>';
}
function matchesFilter(item, filter) { if (filter === 'tcp' || filter === 'udp') return item.protocol === filter; if (filter === 'remote') return item.remote_scope && item.remote_scope !== 'none'; if (filter === 'listen') return String(item.state || '').toLowerCase() === 'listen'; return true; }
function renderConnections(connections) {
  var visible = connections.filter(function(item) { return matchesFilter(item, $('connection-filter').value); });
  if (!visible.length) { $('connection-list').innerHTML = '<div class="empty-state">No endpoints match this filter.</div>'; return; }
  $('connection-list').innerHTML = visible.map(function(item) { var remote = item.remote_address ? escapeHtml(item.remote_address) + (item.remote_port ? ':' + escapeHtml(item.remote_port) : '') : 'local bind'; var lower = String(item.state || '').toLowerCase(), stateClass = lower === 'established' || lower === 'permitted' || lower === 'flow_established' ? 'good' : (lower === 'listen' ? 'info' : ''); return '<article class="connection-row"><div class="connection-top"><span class="protocol ' + escapeHtml(item.protocol) + '">' + escapeHtml(item.protocol.toUpperCase()) + '</span><span class="state ' + stateClass + '">' + escapeHtml(item.state) + '</span></div><div class="endpoint"><span>' + escapeHtml(item.local_address) + ':' + escapeHtml(item.local_port) + '</span><span class="arrow">→</span><span>' + remote + '</span></div><div class="connection-meta"><span>' + escapeHtml(item.process_name) + ' · PID ' + escapeHtml(item.process_id) + (item.user_name ? ' · ' + escapeHtml(item.user_name) : '') + (item.remote_service_hint ? ' · ' + escapeHtml(item.remote_service_hint) : '') + (item.direction ? ' · ' + escapeHtml(item.direction) : '') + (item.source && item.source !== 'endpoint_table' ? ' · ' + escapeHtml(item.source) : '') + '</span>' + (item.remote_address && item.remote_scope !== 'none' ? '<button class="lookup-button" type="button" data-inspect-ip="' + escapeHtml(item.remote_address) + '">Inspect IP</button>' : '') + '</div></article>'; }).join('');
}
function renderSelection(peer) {
  var geo = peer.geo || {}, location = geo.location || {}, network = geo.network || {}, routing = geo.routing || {}, details = [['Endpoint', peer.remote_address + ':' + peer.remote_port], ['Protocols', Object.keys(peer.protocols || {}).join(' + ').toUpperCase() || 'Unknown'], ['Processes', (peer.processes || []).map(function(item) { return item.process_name + ' (' + item.count + ')'; }).join(', ') || 'Unknown'], ['State', Object.keys(peer.states || {}).join(', ') || 'Unknown'], ['Location', [location.city, location.state, location.country].filter(Boolean).join(', ') || 'Not mapped'], ['Network', [network.asn, network.organization || network.isp].filter(Boolean).join(' · ') || 'Not enriched'], ['Route', [routing.prefix, routing.holder].filter(Boolean).join(' · ') || 'Not enriched'], ['Correlation', (peer.correlations || []).join(' · ') || 'No signal']]; $('selection-title').textContent = peer.remote_address + ':' + peer.remote_port; $('selection-details').innerHTML = details.map(function(row) { return '<div class="enrichment-row"><span>' + escapeHtml(row[0]) + '</span><strong>' + escapeHtml(row[1]) + '</strong></div>'; }).join(''); $('selection-panel').classList.remove('hidden'); }
function selectPeer(key) { var peer = state.overview && state.overview.remote_peers.filter(function(item) { return item.key === key; })[0]; if (peer) { state.selectedPeerKey = key; renderSelection(peer); } }
function captureUiState() {
  return {
    view: state.view,
    scrollY: window.scrollY,
    openProcessKeys: Array.prototype.map.call(document.querySelectorAll('.process-group[open]'), function(item) { return item.dataset.processKey; }),
    openPeerKeys: Array.prototype.map.call(document.querySelectorAll('.peer-group[open]'), function(item) { return item.dataset.peerKey; }),
    selectedPeerKey: state.selectedPeerKey,
    focusProcessKey: state.focusProcessKey,
  };
}
function restoreUiState(previous) {
  setView(previous.view);
  Array.prototype.forEach.call(document.querySelectorAll('.process-group'), function(item) { item.open = previous.openProcessKeys.indexOf(item.dataset.processKey) >= 0; item.classList.toggle('focused', item.dataset.processKey === previous.focusProcessKey); });
  Array.prototype.forEach.call(document.querySelectorAll('.peer-group'), function(item) { item.open = previous.openPeerKeys.indexOf(item.dataset.peerKey) >= 0; });
  var peer = state.overview && state.overview.remote_peers.filter(function(item) { return item.key === previous.selectedPeerKey; })[0];
  if (peer) { state.selectedPeerKey = previous.selectedPeerKey; renderSelection(peer); } else if (previous.selectedPeerKey) { state.selectedPeerKey = ''; $('selection-panel').classList.add('hidden'); }
  state.focusProcessKey = state.overview && state.overview.process_groups.some(function(item) { return item.key === previous.focusProcessKey; }) ? previous.focusProcessKey : '';
  window.scrollTo(0, previous.scrollY);
}
function focusProcess(key) {
  if (!state.overview) return;
  var group = state.overview.process_groups.filter(function(item) { return item.key === key; })[0];
  if (!group) return;
  state.focusProcessKey = key; setView('processes');
  Array.prototype.forEach.call(document.querySelectorAll('.process-group'), function(item) { item.open = item.dataset.processKey === key; item.classList.toggle('focused', item.dataset.processKey === key); });
  var focused = Array.prototype.filter.call(document.querySelectorAll('.process-group'), function(item) { return item.dataset.processKey === key; })[0];
  if (focused) focused.scrollIntoView({block: 'nearest'});
}
function renderEnrichment(data) { var network = data.network || {}, location = data.location || {}, risk = data.risk || {}, routing = data.routing || {}; $('enrichment-title').textContent = 'Remote IP · ' + (data.ip || 'unknown'); $('enrichment-details').innerHTML = [['Network', [network.asn, network.organization || network.isp || network.domain].filter(Boolean).join(' · ') || 'No allocation details'], ['Location', [location.city, location.state, location.country].filter(Boolean).join(', ') || 'Coarse location unavailable'], ['Route', [routing.prefix, routing.holder].filter(Boolean).join(' · ') || 'Routing details unavailable'], ['Risk', risk.score != null ? 'Score ' + risk.score + (risk.is_vpn ? ' · VPN' : '') + (risk.is_tor ? ' · Tor' : '') + (risk.is_proxy ? ' · Proxy' : '') : 'No risk signal']].map(function(row) { return '<div class="enrichment-row"><span>' + escapeHtml(row[0]) + '</span><strong>' + escapeHtml(row[1]) + '</strong></div>'; }).join(''); $('enrichment-note').textContent = data.identity_note || 'IP intelligence describes network signals, not a verified person or exact endpoint identity.'; $('enrichment-panel').classList.remove('hidden'); }
function lookupIp(ip) { $('enrichment-panel').classList.remove('hidden'); $('enrichment-title').textContent = 'Inspecting ' + ip + '...'; $('enrichment-details').innerHTML = '<div class="empty-state">Querying public allocation and routing sources on demand.</div>'; requestJson('./api/network/enrich?ip=' + encodeURIComponent(ip)).then(renderEnrichment).catch(function(error) { $('enrichment-details').innerHTML = '<div class="empty-state">' + escapeHtml(error.message) + '</div>'; }); }
function mapPeers() { var ips = state.overview ? (state.overview.unmapped_public_ips || []).slice(0, 12) : []; if (!ips.length) { $('map-status').textContent = 'No unmapped public peers in this snapshot'; return; } $('map-peers').disabled = true; $('map-status').textContent = 'Mapping ' + ips.length + ' public peers...'; requestJson('./api/network/enrich-batch', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({ips: ips})}).then(function(data) { $('map-status').textContent = formatCount(data.mapped || 0) + ' peers enriched; refreshing view'; loadSnapshot(); }).catch(function(error) { $('map-status').textContent = error.message; }).finally(function() { $('map-peers').disabled = false; }); }
function loadSnapshot() { if (state.busy) return; state.busy = true; $('refresh-button').disabled = true; $('snapshot-status').textContent = 'Collecting...'; requestJson('./api/network/snapshot').then(function(data) { var previous = captureUiState(); state.snapshot = data.snapshot || {}; state.overview = currentOverview(state.snapshot); renderSummary(state.snapshot); renderConnections(state.snapshot.connections || []); restoreUiState(previous); }).catch(function(error) { $('snapshot-status').textContent = error.message; }).finally(function() { state.busy = false; $('refresh-button').disabled = false; }); }
function loadHistory() { requestJson('./api/network/history?limit=24').then(renderHistory).catch(function() {}); }
function loadBios() { $('bios-details').innerHTML = '<div class="empty-state">Reading firmware inventory...</div>'; requestJson('./api/system/bios').then(function(data) { var inventory = data.inventory || {}, bios = inventory.bios || {}, system = inventory.system || {}, board = inventory.baseboard || {}, tpm = inventory.tpm || {}; $('bios-details').innerHTML = [['BIOS', [bios.Manufacturer, bios.SMBIOSBIOSVersion || bios.Version].filter(Boolean).join(' · ') || 'Unavailable'], ['Release', bios.ReleaseDate || 'Unavailable'], ['System', [system.Vendor, system.Name, system.Version].filter(Boolean).join(' · ') || 'Unavailable'], ['Board', [board.Manufacturer, board.Product, board.Version].filter(Boolean).join(' · ') || 'Unavailable'], ['TPM', tpm.TpmPresent == null ? 'Unavailable' : (tpm.TpmPresent ? (tpm.TpmReady ? 'Present / ready' : 'Present / not ready') : 'Not present')]].map(function(row) { return '<div class="enrichment-row"><span>' + escapeHtml(row[0]) + '</span><strong>' + escapeHtml(row[1]) + '</strong></div>'; }).join(''); }).catch(function(error) { $('bios-details').innerHTML = '<div class="empty-state">' + escapeHtml(error.message) + '</div>'; }); }
function setView(view) { state.view = view; Array.prototype.forEach.call(document.querySelectorAll('.view-tab'), function(tab) { var active = tab.dataset.view === view; tab.classList.toggle('active', active); tab.setAttribute('aria-selected', active ? 'true' : 'false'); }); $('summary-view').classList.toggle('hidden', view !== 'summary'); $('peers-view').classList.toggle('hidden', view !== 'peers'); $('connections-view').classList.toggle('hidden', view === 'summary' || view === 'peers'); $('connections-view').classList.toggle('process-only', view === 'processes'); }
document.addEventListener('click', function(event) { var target = event.target.closest ? event.target.closest('[data-inspect-ip],[data-select-peer],[data-map-peer],[data-focus-process]') : null; if (!target) return; if (target.dataset.inspectIp) lookupIp(target.dataset.inspectIp); else if (target.dataset.selectPeer || target.dataset.mapPeer) selectPeer(target.dataset.selectPeer || target.dataset.mapPeer); else if (target.dataset.focusProcess) focusProcess(target.dataset.focusProcess); });
$('refresh-button').addEventListener('click', loadSnapshot); $('bios-refresh').addEventListener('click', loadBios); $('map-peers').addEventListener('click', mapPeers); $('connection-filter').addEventListener('change', function() { if (state.snapshot) renderConnections(state.snapshot.connections || []); }); $('enrichment-close').addEventListener('click', function() { $('enrichment-panel').classList.add('hidden'); }); $('selection-close').addEventListener('click', function() { state.selectedPeerKey = ''; $('selection-panel').classList.add('hidden'); }); $('view-tabs').addEventListener('click', function(event) { var tab = event.target.closest('.view-tab'); if (tab) setView(tab.dataset.view); }); $('otp-submit').addEventListener('click', login); $('otp-input').addEventListener('keydown', function(event) { if (event.key === 'Enter') login(); });
initTheme(); setView('summary'); initAuth();

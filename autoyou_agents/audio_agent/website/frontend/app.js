// Copyright (c) 2026 OpenStorey LLC. All rights reserved.
// Licensed under the AutoYou Source-Available License.
// See LICENSE in the project root for license information.
// AI/ML training use prohibited without written authorization (License S3.9).
// AUTOYOU-PROVENANCE-A-schedule-27dd914d57a1829a23344da0

/* AutoYou Audio Agent app.js
 * Drives the secure dynamic playlist player.
 * Settings (gear icon) are hidden behind OTP auth.
 * Session connection params are pre-fetched after successful auth.
 */

'use strict';

// Iframe Persistent Audio element and creation overrides
if (window.parent && window.parent !== window) {
    if (!window.parent.__autoyou_persistent_audio_element) {
        window.parent.__autoyou_persistent_audio_element = new window.parent.Audio();
    }
    window.Audio = function() {
        return window.parent.__autoyou_persistent_audio_element;
    };
    
    var originalCreateElement = document.createElement;
    document.createElement = function(tagName) {
        if (String(tagName).toLowerCase() === 'audio') {
            return window.parent.__autoyou_persistent_audio_element;
        }
        return originalCreateElement.apply(document, arguments);
    };
}

//  State
var state = {
    tracks: [],
    initialized: false,
    libraryLoadId: 0,
    libraryPagination: {
        hasMore: false,
        nextOffset: null,
        overallLimit: 12000,
        loadingMore: false,
    },
    searchQuery: '',
    searchTimerId: null,
    settings: {
        search_limit: 12000,
        repeat_mode: 'off',
        shuffle_enabled: false,
        auto_relay_on_play: false,
        reply_target: { owner_key: '', session_id: '' },
        audio_sources: {
            voice_transcriptions: true,
            page_feed_audio: true,
            notes_media_audio: true,
        },
        ad_hoc_paths: [],
    },
    audioPaths: [],
    authenticated: false,
    authToken: '',
    currentTab: 'library',
    activeQueueType: null,
    // Set true when the library list is replaced (rescan/refresh) while the
    // library queue is already the active Amplitude queue. Amplitude keeps its
    // own copy of the song array, so a replaced library must force a re-init on
    // the next play; otherwise clicking a row plays the stale pre-refresh track.
    libraryQueueDirty: false,
    playlistQueueDirty: false,
    listExpanded: false,
};

var playlistState = {
    tracks: [],
};

var THEME_KEY = 'ay-audio-theme';
var AUTH_TOKEN_KEY = 'ay-audio-auth';
var INITIAL_LIBRARY_BATCH_SIZE = 80;
var BACKGROUND_LIBRARY_BATCH_SIZE = 80;
var SEARCH_LIBRARY_BATCH_SIZE = 60;
var STATUS_FLASH_MS = 1800;

//  Element References 
var elLoading       = document.getElementById('loading');
var elLoadingText   = document.getElementById('loading-text');
var elPlayer        = document.getElementById('player');
var elLibCount      = document.getElementById('lib-count');
var elStatusText    = document.getElementById('status-text');
var elPlaylist      = document.getElementById('playlist');
var elTrackSearch   = document.getElementById('track-search');
var elSearchRow     = document.getElementById('search-row');
var elSearchToggleBtn = document.getElementById('search-toggle-btn');
var elListDensityBtn = document.getElementById('list-density-btn');
var elRepeatBadge   = document.getElementById('repeat-badge');
var elRescanBtn     = document.getElementById('rescan-btn');
var elThemeToggleBtn = document.getElementById('theme-toggle-btn');
var elSettingsBtn   = document.getElementById('settings-btn');
var elSettingsOverlay = document.getElementById('settings-overlay');
var elSettingsBackdrop = document.getElementById('settings-backdrop');
var elSettingsClose = document.getElementById('settings-close');
var elOtpGate       = document.getElementById('otp-gate');
var elOtpInput      = document.getElementById('otp-input');
var elOtpSubmit     = document.getElementById('otp-submit');
var elOtpError      = document.getElementById('otp-error');
var elSettingsForm  = document.getElementById('settings-form');
var elOwnerKey      = document.getElementById('setting-owner-key');
var elSessionId     = document.getElementById('setting-session-id');
var elSearchLimit   = document.getElementById('setting-search-limit');
var elRepeatMode    = document.getElementById('setting-repeat-mode');
var elAutoRelay     = document.getElementById('setting-auto-relay');
var elSaveSettings  = document.getElementById('save-settings-btn');
var elPrefetchBtn   = document.getElementById('prefetch-btn');
var elPrefetchStatus = document.getElementById('prefetch-status');
var elLogoutBtn     = document.getElementById('logout-btn');
var elRelayResult   = document.getElementById('relay-result');
var elSettingsStatus = document.getElementById('settings-status');
var elSourceVoice   = document.getElementById('setting-source-voice');
var elSourcePage    = document.getElementById('setting-source-page');
var elSourceNotes   = document.getElementById('setting-source-notes');
var elAdHocPaths    = document.getElementById('setting-ad-hoc-paths');
var elAudioPathList = document.getElementById('audio-path-list');
var elAudioUploadInput = document.getElementById('audio-upload-input');
var elAudioUploadBtn = document.getElementById('audio-upload-btn');
var elAudioUploadStatus = document.getElementById('audio-upload-status');
var elAlbumArtImg   = document.getElementById('album-art-img');
var elArtFallback   = document.getElementById('art-fallback');
var elBufferingIndicator = document.getElementById('buffering-indicator');
var elBufferingLabel = document.getElementById('buffering-label');

function scrollElementIntoComfortView(el) {
    if (!el || !el.scrollIntoView) return;
    try {
        var isMobile = /Android|webOS|iPhone|iPad|iPod|BlackBerry|IEMobile|Opera Mini/i.test(navigator.userAgent) || 
                       ('ontouchstart' in window) || (navigator.maxTouchPoints > 0);
        if (isMobile) {
            el.scrollIntoView({ behavior: 'auto', block: 'nearest', inline: 'nearest' });
        } else {
            el.scrollIntoView({ behavior: 'smooth', block: 'center', inline: 'nearest' });
        }
    } catch (_) {
        try { el.scrollIntoView(true); } catch (__) {}
    }
}

function keepElementVisibleAfterViewportSettles(el) {
    if (!el) return;
    scrollElementIntoComfortView(el);
    window.setTimeout(function () { scrollElementIntoComfortView(el); }, 90);
    window.setTimeout(function () { scrollElementIntoComfortView(el); }, 280);
}

function focusWithComfortScroll(input, scrollTarget) {
    if (!input) return;
    var target = scrollTarget || input;
    keepElementVisibleAfterViewportSettles(target);
    try {
        input.focus({ preventScroll: true });
    } catch (_) {
        input.focus();
    }
    keepElementVisibleAfterViewportSettles(target);
}

function bindKeyboardAwareFocus(input, scrollTarget) {
    if (!input || input.dataset.keyboardAwareFocusBound === '1') return;
    input.dataset.keyboardAwareFocusBound = '1';
    var target = scrollTarget || input;
    var keepVisible = function () { keepElementVisibleAfterViewportSettles(target); };
    input.addEventListener('focus', keepVisible);
    input.addEventListener('click', keepVisible);
    if (window.visualViewport && window.visualViewport.addEventListener) {
        window.visualViewport.addEventListener('resize', function () {
            if (document.activeElement === input) keepVisible();
        });
    }
}

//  Theme
(function applyStoredTheme() {
    try {
        var t = localStorage.getItem(THEME_KEY);
        var theme = (t === 'light' || t === 'dark')
            ? t
            : ((window.matchMedia && window.matchMedia('(prefers-color-scheme: dark)').matches) ? 'dark' : 'light');
        document.documentElement.setAttribute('data-theme', theme);
    } catch (_) {}
})();

function currentTheme() {
    return document.documentElement.getAttribute('data-theme') === 'light' ? 'light' : 'dark';
}

function setTheme(theme) {
    var normalized = theme === 'light' ? 'light' : 'dark';
    document.documentElement.setAttribute('data-theme', normalized);
    try { localStorage.setItem(THEME_KEY, normalized); } catch (_) {}
}

function toggleTheme() {
    setTheme(currentTheme() === 'dark' ? 'light' : 'dark');
}

function normalizeSearchLimit(value) {
    var numeric = Number(value);
    if (!Number.isFinite(numeric)) return 12000;
    return Math.max(100, Math.min(12000, Math.round(numeric)));
}

var playbackHealth = {
    timerId: null,
    lastTrackIndex: -1,
    lastSecond: -1,
    stalledTicks: 0,
    recoveryAttempts: 0,
    lastRecoveryAtMs: 0,
    lastLoadStartAtMs: 0,
    lastProgressAtMs: 0,
    userPlaybackRequested: false,
    loadFailureSkips: 0,
};

var bufferingState = {
    active: false,
    hideTimerId: null,
    autoClearTimerId: null,
    progressPollTimerId: null,
    selectionTimerId: null,
    loadTimeoutTimerId: null,
    progressSnapshot: null,
};

var statusFlashTimerId = null;
var searchOpen = false;
var playabilityCache = {};

function openSearch() {
    if (!elSearchRow || !elTrackSearch) return;
    searchOpen = true;
    elSearchRow.hidden = false;
    if (elSearchToggleBtn) {
        elSearchToggleBtn.classList.add('search-active');
        elSearchToggleBtn.setAttribute('aria-expanded', 'true');
    }
    window.setTimeout(function () {
        try { elTrackSearch.focus(); } catch (_) {}
    }, 30);
}

function closeSearch() {
    if (!elSearchRow) return;
    searchOpen = false;
    elSearchRow.hidden = true;
    if (elSearchToggleBtn) {
        elSearchToggleBtn.classList.remove('search-active');
        elSearchToggleBtn.setAttribute('aria-expanded', 'false');
    }
    if (elTrackSearch) {
        elTrackSearch.value = '';
    }
    state.searchQuery = '';
    renderPlaylistView();
}

function toggleSearch() {
    if (searchOpen) { closeSearch(); } else { openSearch(); }
}

function setListExpanded(expanded) {
    state.listExpanded = Boolean(expanded);
    if (elPlayer) elPlayer.classList.toggle('list-expanded', state.listExpanded);
    if (elListDensityBtn) {
        elListDensityBtn.classList.toggle('is-expanded', state.listExpanded);
        elListDensityBtn.setAttribute('aria-pressed', state.listExpanded ? 'true' : 'false');
        elListDensityBtn.setAttribute('aria-label', state.listExpanded ? 'Collapse song list' : 'Expand song list');
        elListDensityBtn.setAttribute('title', state.listExpanded ? 'Collapse song list' : 'Expand song list');
    }
}

function toggleListExpanded() {
    setListExpanded(!state.listExpanded);
}

var playbackVisibilitySync = {
    observer: null,
    queued: false,
    bound: false,
};

function parseClockSeconds(value) {
    var text = String(value || '').trim();
    if (!text) return 0;
    var parts = text.split(':').map(function (part) { return Number(part); });
    if (!parts.length || parts.some(function (n) { return !Number.isFinite(n) || n < 0; })) return 0;
    var seconds = 0;
    for (var i = 0; i < parts.length; i += 1) {
        seconds = seconds * 60 + parts[i];
    }
    return seconds;
}

function getVisiblePlaybackState() {
    var playButton = document.querySelector('.amplitude-play-pause');
    var playing = !!(playButton && playButton.classList.contains('amplitude-playing'));

    var currentTimeEl = document.querySelector('.amplitude-current-time');
    var elapsedSeconds = parseClockSeconds(currentTimeEl ? currentTimeEl.textContent : '');

    var durationEl = document.querySelector('.amplitude-duration-time');
    var durationSeconds = parseClockSeconds(durationEl ? durationEl.textContent : '');

    var slider = document.querySelector('.amplitude-song-slider');
    var sliderValue = Number(slider && slider.value);

    return {
        playing: playing,
        elapsedSeconds: elapsedSeconds,
        durationSeconds: durationSeconds,
        sliderValue: Number.isFinite(sliderValue) ? sliderValue : 0,
    };
}

function hasVisiblePlaybackProgress(playbackUi) {
    var ui = playbackUi || getVisiblePlaybackState();
    return (ui.playing && ui.elapsedSeconds > 0) || (ui.sliderValue > 0.15);
}

function isAudioReadyOrPlaying() {
    var audioEl = getAmplitudeAudioElement();
    if (!audioEl) return false;
    var readyState = Number(audioEl.readyState || 0);
    var currentTime = Number(audioEl.currentTime || 0);
    var activelyPlaying = !audioEl.paused && !audioEl.ended && currentTime > 0.05;
    return activelyPlaying || readyState >= 3;
}

function shouldShowBufferingForAudioEvent() {
    return playbackHealth.userPlaybackRequested || isPlaybackActive();
}

function hasPlaybackProgressAdvanced(snapshot) {
    var ui = getVisiblePlaybackState();
    var audioEl = getAmplitudeAudioElement();
    var audioCurrentTime = audioEl ? Number(audioEl.currentTime || 0) : NaN;
    var startedBeforeVisibleProgress = !snapshot || (
        Number(snapshot.elapsedSeconds || 0) <= 0 && Number(snapshot.sliderValue || 0) <= 0.15
    );

    if (startedBeforeVisibleProgress) {
        return isAudioReadyOrPlaying() || hasVisiblePlaybackProgress(ui);
    }

    if (Number.isFinite(audioCurrentTime) && audioCurrentTime > (Number(snapshot.elapsedSeconds || 0) + 0.2)) {
        return true;
    }
    if (ui.elapsedSeconds > (Number(snapshot.elapsedSeconds || 0) + 0.2)) {
        return true;
    }
    if (ui.sliderValue > (Number(snapshot.sliderValue || 0) + 0.15)) {
        return true;
    }
    return false;
}

function syncBufferingIndicatorToPlayback() {
    if (!bufferingState.active) return false;
    if (!hasPlaybackProgressAdvanced(bufferingState.progressSnapshot)) return false;
    setBufferingIndicator(false);
    return true;
}

function queuePlaybackVisibilitySync() {
    if (playbackVisibilitySync.queued) return;
    playbackVisibilitySync.queued = true;
    setTimeout(function () {
        playbackVisibilitySync.queued = false;
        syncBufferingIndicatorToPlayback();
    }, 0);
}

function bindPlaybackVisibilityWatchers() {
    if (playbackVisibilitySync.bound) return;
    playbackVisibilitySync.bound = true;

    if (typeof MutationObserver !== 'undefined') {
        playbackVisibilitySync.observer = new MutationObserver(function () {
            queuePlaybackVisibilitySync();
        });

        [
            ['.amplitude-play-pause', { attributes: true, attributeFilter: ['class'] }],
            ['.amplitude-current-time', { childList: true, characterData: true, subtree: true }],
            ['.amplitude-duration-time', { childList: true, characterData: true, subtree: true }],
        ].forEach(function (entry) {
            var node = document.querySelector(entry[0]);
            if (node) {
                playbackVisibilitySync.observer.observe(node, entry[1]);
            }
        });
    }

    window.addEventListener('focus', queuePlaybackVisibilitySync);
    document.addEventListener('visibilitychange', function () {
        if (!document.hidden) {
            queuePlaybackVisibilitySync();
        }
    });
}

function ensureBufferingProgressPoll() {
    if (bufferingState.progressPollTimerId) return;
    bufferingState.progressPollTimerId = setInterval(function () {
        if (!bufferingState.active) {
            clearInterval(bufferingState.progressPollTimerId);
            bufferingState.progressPollTimerId = null;
            return;
        }
        syncBufferingIndicatorToPlayback();
    }, 750);
}

function clearBufferingLoadTimeout() {
    if (!bufferingState.loadTimeoutTimerId) return;
    clearTimeout(bufferingState.loadTimeoutTimerId);
    bufferingState.loadTimeoutTimerId = null;
}

function scheduleBufferingLoadTimeout() {
    clearBufferingLoadTimeout();
    bufferingState.loadTimeoutTimerId = setTimeout(function () {
        bufferingState.loadTimeoutTimerId = null;
        if (!bufferingState.active || hasPlaybackProgressAdvanced(bufferingState.progressSnapshot)) return;
        handleCurrentTrackLoadFailure('Track did not start.');
    }, 30000);
}

function setBufferingIndicator(active, labelText) {
    if (!elBufferingIndicator) return;

    if (active) {
        if (bufferingState.hideTimerId) {
            clearTimeout(bufferingState.hideTimerId);
            bufferingState.hideTimerId = null;
        }
        if (bufferingState.autoClearTimerId) {
            clearTimeout(bufferingState.autoClearTimerId);
            bufferingState.autoClearTimerId = null;
        }
        clearBufferingLoadTimeout();
        elBufferingIndicator.hidden = false;
        if (elBufferingLabel) {
            elBufferingLabel.textContent = String(labelText || 'Loading audio stream...');
        }
        bufferingState.active = true;
        bufferingState.progressSnapshot = getVisiblePlaybackState();
        ensureBufferingProgressPoll();

        // Fast local playback can become ready before buffering events arrive.
        bufferingState.autoClearTimerId = setTimeout(function () {
            bufferingState.autoClearTimerId = null;
            syncBufferingIndicatorToPlayback();
        }, 1200);
        scheduleBufferingLoadTimeout();
        return;
    }

    if (!bufferingState.active) {
        elBufferingIndicator.hidden = true;
        clearBufferingLoadTimeout();
        return;
    }

    if (bufferingState.hideTimerId) {
        return;
    }
    bufferingState.hideTimerId = setTimeout(function () {
        elBufferingIndicator.hidden = true;
        bufferingState.hideTimerId = null;
        bufferingState.active = false;
        if (bufferingState.autoClearTimerId) {
            clearTimeout(bufferingState.autoClearTimerId);
            bufferingState.autoClearTimerId = null;
        }
        if (bufferingState.progressPollTimerId) {
            clearInterval(bufferingState.progressPollTimerId);
            bufferingState.progressPollTimerId = null;
        }
        if (bufferingState.selectionTimerId) {
            clearTimeout(bufferingState.selectionTimerId);
            bufferingState.selectionTimerId = null;
        }
        clearBufferingLoadTimeout();
        bufferingState.progressSnapshot = null;
        if (elStatusText) {
            var current = String(elStatusText.textContent || '').trim().toLowerCase();
            if (current === 'preparing stream...' || current === 'downloading audio...' || current === 'playback buffering...') {
                setStatus('');
            }
        }
    }, 350);
}

function isPlaybackActive() {
    var button = document.querySelector('.amplitude-play-pause');
    return !!(button && button.classList.contains('amplitude-playing'));
}

function getAmplitudeAudioElement() {
    if (window.Amplitude && typeof window.Amplitude.getAudio === 'function') {
        try {
            var amplitudeAudio = window.Amplitude.getAudio();
            if (amplitudeAudio) return amplitudeAudio;
        } catch (_) {}
    }
    return document.getElementById('amplitude-audio') || document.querySelector('audio');
}

function notePlaybackProgress() {
    playbackHealth.lastProgressAtMs = Date.now();
    playbackHealth.loadFailureSkips = 0;
}

function canAttemptPlaybackRecovery(reason) {
    var now = Date.now();

    // Android WebView frequently fires abort/emptied during normal source swaps.
    // Treat those as informational so we do not fall into pause/play restart loops.
    if (reason === 'abort' || reason === 'emptied') {
        return false;
    }

    // Give the browser a short grace window after every loadstart before we try to
    // "recover" the stream, otherwise a normal range fetch can look like a stall.
    if (playbackHealth.lastLoadStartAtMs && (now - playbackHealth.lastLoadStartAtMs) < 2500) {
        return false;
    }

    // Avoid stacking multiple recovery attempts on top of each other.
    if (playbackHealth.lastRecoveryAtMs && (now - playbackHealth.lastRecoveryAtMs) < 1500) {
        return false;
    }

    // If media progress advanced recently, the stream is alive even if one transient
    // buffering event just fired.
    if (playbackHealth.lastProgressAtMs && (now - playbackHealth.lastProgressAtMs) < 1200) {
        return false;
    }

    return true;
}

function resetPlaybackHealth() {
    playbackHealth.stalledTicks = 0;
    playbackHealth.recoveryAttempts = 0;
}

function syncTransportPlaybackStateFromAudio() {
    var audioEl = getAmplitudeAudioElement();
    var button = document.querySelector('.amplitude-play-pause');
    if (!audioEl || !button) return;

    var shouldShowPlaying = !audioEl.paused && !audioEl.ended;
    button.classList.toggle('amplitude-playing', shouldShowPlaying);
    button.classList.toggle('amplitude-paused', !shouldShowPlaying);
    button.setAttribute('aria-pressed', shouldShowPlaying ? 'true' : 'false');
    updateMediaSessionPlaybackState(shouldShowPlaying);
}

function recoverFromPlaybackStall(reason) {
    if (!window.Amplitude || !isPlaybackActive()) return;
    if (!canAttemptPlaybackRecovery(reason)) return;

    playbackHealth.lastRecoveryAtMs = Date.now();
    playbackHealth.recoveryAttempts += 1;
    setBufferingIndicator(true, 'Playback buffering...');
    if (playbackHealth.recoveryAttempts <= 2) {
        try {
            window.Amplitude.pause();
            window.Amplitude.play();
            return;
        } catch (_) {}
    }

    playbackHealth.recoveryAttempts = 0;
    try {
        window.Amplitude.next();
        setStatus('Track stalled, skipped to next track.');
    } catch (_) {}
}

function getActiveQueueTracks() {
    return state.activeQueueType === 'playlist' ? playlistState.tracks : state.tracks;
}

function getActiveTrack() {
    if (!window.Amplitude) return null;
    var queue = getActiveQueueTracks();
    var index = Number(window.Amplitude.getActiveIndex());
    return (Number.isFinite(index) && index >= 0 && index < queue.length) ? queue[index] : null;
}

function shortTrackLabel(track) {
    return String((track && (track.title || track.file_name)) || 'Track').trim();
}

function browserCanPlayTrack(track) {
    var contentType = String((track && track.content_type) || '').split(';')[0].trim().toLowerCase();
    if (!contentType || contentType.indexOf('audio/') !== 0) return true;
    if (Object.prototype.hasOwnProperty.call(playabilityCache, contentType)) {
        return playabilityCache[contentType];
    }

    var audioEl = getAmplitudeAudioElement();
    if (!audioEl || typeof audioEl.canPlayType !== 'function') {
        try { audioEl = document.createElement('audio'); } catch (_) {}
    }
    if (!audioEl || typeof audioEl.canPlayType !== 'function') return true;

    var result = String(audioEl.canPlayType(contentType) || '').trim();
    playabilityCache[contentType] = result !== '';
    return playabilityCache[contentType];
}

function rejectUnsupportedTrack(track) {
    playbackHealth.userPlaybackRequested = false;
    setBufferingIndicator(false);
    setStatus('Unsupported in this browser: ' + shortTrackLabel(track));
}

function handleCurrentTrackLoadFailure(message) {
    var track = getActiveTrack();
    var queue = getActiveQueueTracks();
    playbackHealth.userPlaybackRequested = false;
    playbackHealth.loadFailureSkips += 1;
    resetPlaybackHealth();
    setBufferingIndicator(false);
    setStatus((message || 'Track could not play.') + ' ' + shortTrackLabel(track));

    if (!window.Amplitude) return;
    try { window.Amplitude.pause(); } catch (_) {}
    if (queue.length > 1 && playbackHealth.loadFailureSkips < queue.length) {
        try { window.Amplitude.next(); } catch (_) {}
    } else if (queue.length > 1) {
        setStatus('No playable tracks found in this queue.');
    }
}

function bindAudioRecoveryHandlers() {
    var audioEl = getAmplitudeAudioElement();
    if (!audioEl || audioEl.__autoyouRecoveryBound) return;
    audioEl.__autoyouRecoveryBound = true;
    try { audioEl.preload = 'none'; } catch (_) {}

    audioEl.addEventListener('loadstart', function () {
        playbackHealth.lastLoadStartAtMs = Date.now();
        if (shouldShowBufferingForAudioEvent()) {
            setBufferingIndicator(true, 'Fetching track from network...');
        }
    });

    audioEl.addEventListener('waiting', function () {
        if (shouldShowBufferingForAudioEvent()) setBufferingIndicator(true, 'Playback buffering...');
    });

    audioEl.addEventListener('stalled', function () {
        if (shouldShowBufferingForAudioEvent()) setBufferingIndicator(true, 'Playback buffering...');
    });

    audioEl.addEventListener('seeking', function () {
        if (shouldShowBufferingForAudioEvent()) setBufferingIndicator(true, 'Seeking audio...');
    });

    ['canplay', 'canplaythrough', 'playing', 'seeked'].forEach(function (eventName) {
        audioEl.addEventListener(eventName, function () {
            notePlaybackProgress();
            setBufferingIndicator(false);
        });
    });

    ['loadeddata', 'durationchange', 'timeupdate', 'progress'].forEach(function (eventName) {
        audioEl.addEventListener(eventName, function () {
            notePlaybackProgress();
            syncBufferingIndicatorToPlayback();
        });
    });

    audioEl.addEventListener('timeupdate', function () {
        updateMediaSessionPosition();
        syncTransportPlaybackStateFromAudio();
    });

    audioEl.addEventListener('ended', function () {
        notePlaybackProgress();
        playbackHealth.userPlaybackRequested = false;
        setBufferingIndicator(false);
        syncTransportPlaybackStateFromAudio();
    });

    ['play', 'playing'].forEach(function (eventName) {
        audioEl.addEventListener(eventName, function () {
            playbackHealth.userPlaybackRequested = true;
            syncTransportPlaybackStateFromAudio();
        });
    });

    audioEl.addEventListener('pause', function () {
        playbackHealth.userPlaybackRequested = false;
        syncTransportPlaybackStateFromAudio();
    });

    audioEl.addEventListener('stalled', function () {
        recoverFromPlaybackStall('stalled');
    });

    audioEl.addEventListener('error', function () {
        handleCurrentTrackLoadFailure('Skipped unavailable audio.');
    });
}

function startPlaybackWatchdog() {
    if (playbackHealth.timerId) {
        clearInterval(playbackHealth.timerId);
        playbackHealth.timerId = null;
    }

    playbackHealth.timerId = setInterval(function () {
        if (bufferingState.active) {
            syncBufferingIndicatorToPlayback();
        }

        if (!window.Amplitude || !isPlaybackActive()) {
            resetPlaybackHealth();
            return;
        }

        var trackIndex = Number(window.Amplitude.getActiveIndex());
        var played = Number(window.Amplitude.getSongPlayedSeconds() || 0);
        var duration = Number(window.Amplitude.getSongDuration() || 0);

        if (!Number.isFinite(trackIndex) || trackIndex < 0) return;
        if (trackIndex !== playbackHealth.lastTrackIndex) {
            playbackHealth.lastTrackIndex = trackIndex;
            playbackHealth.lastSecond = played;
            resetPlaybackHealth();
            return;
        }

        if (Number.isFinite(duration) && duration > 0 && (duration - played) < 1.5) {
            return;
        }

        if (played > playbackHealth.lastSecond + 0.4) {
            playbackHealth.lastSecond = played;
            playbackHealth.stalledTicks = 0;
            playbackHealth.recoveryAttempts = 0;
            setBufferingIndicator(false);
            return;
        }

        playbackHealth.stalledTicks += 1;
        if (playbackHealth.stalledTicks >= 5) {
            playbackHealth.stalledTicks = 0;
            recoverFromPlaybackStall('buffering');
        }
    }, 3000);
}

//  Fetch Helper 
var FETCH_TIMEOUT_MS = 120000;

function resolveApiPath(path) {
    var raw = String(path || '').trim();
    if (!raw) return raw;
    if (/^[a-z][a-z0-9+.-]*:/i.test(raw) || raw.indexOf('//') === 0) {
        return raw;
    }
    if (raw.charAt(0) === '/') {
        return raw;
    }

    var pathname = String((window.location && window.location.pathname) || '/');
    var marker = '/agent/audio_agent';
    var idx = pathname.indexOf(marker);
    var base = '/';
    if (idx >= 0) {
        base = pathname.slice(0, idx) + marker + '/';
    }
    return base + raw.replace(/^\/+/, '');
}

function apiFetch(path, opts) {
    opts = opts || {};
    var headers = Object.assign({}, opts.headers || {});
    if (state.authToken) {
        headers['Authorization'] = 'Bearer ' + state.authToken;
    }
    var controller = typeof AbortController !== 'undefined' ? new AbortController() : null;
    var timer = controller ? setTimeout(function () { controller.abort(); }, FETCH_TIMEOUT_MS) : null;
    var fetchOpts = Object.assign({}, opts, { headers: headers });
    if (controller) fetchOpts.signal = controller.signal;
    delete fetchOpts.cache;   // avoid browser-level cache conflicts
    var resolvedPath = resolveApiPath(path);
    return fetch(resolvedPath, fetchOpts)
        .then(function (r) {
            if (timer) clearTimeout(timer);
            var ok = r.ok;
            var status = r.status;
            return r.text().then(function (text) {
                var data;
                try { data = JSON.parse(text); } catch (_) { data = { success: false, error: 'Invalid JSON from server', raw: text.slice(0, 200) }; }
                return { ok: ok, status: status, data: data };
            });
        })
        .catch(function (err) {
            if (timer) clearTimeout(timer);
            throw err;
        });
}

function apiJson(path, opts) {
    return apiFetch(path, opts).then(function (res) {
        if (!res.ok || (res.data && res.data.success === false)) {
            var msg = (res.data && res.data.error) || ('HTTP ' + res.status);
            throw new Error(msg);
        }
        return res.data || {};
    });
}

//  Album Art Fallback 
if (elAlbumArtImg) {
    elAlbumArtImg.addEventListener('error', function () {
        elAlbumArtImg.style.display = 'none';
        if (elArtFallback) elArtFallback.style.display = 'flex';
    });
    elAlbumArtImg.addEventListener('load', function () {
        if (elAlbumArtImg.src && elAlbumArtImg.naturalWidth) {
            elAlbumArtImg.style.display = 'block';
            if (elArtFallback) elArtFallback.style.display = 'none';
        }
    });
}

//  Status Helpers 
function setStatus(msg) {
    if (statusFlashTimerId) {
        clearTimeout(statusFlashTimerId);
        statusFlashTimerId = null;
    }
    if (elStatusText) elStatusText.textContent = String(msg || '');
}
function flashStatus(msg, fallbackMsg) {
    if (!elStatusText) return;
    if (statusFlashTimerId) {
        clearTimeout(statusFlashTimerId);
        statusFlashTimerId = null;
    }
    elStatusText.textContent = String(msg || '');
    statusFlashTimerId = setTimeout(function () {
        statusFlashTimerId = null;
        elStatusText.textContent = String(fallbackMsg || '');
    }, STATUS_FLASH_MS);
}
function setRelayMsg(msg) {
    if (elRelayResult) elRelayResult.textContent = String(msg || '');
}
function setSettingsMsg(msg) {
    if (elSettingsStatus) elSettingsStatus.textContent = String(msg || '');
}

function updateLibraryCount(options) {
    options = options || {};
    if (!elLibCount) return;

    if (!state.tracks.length) {
        elLibCount.textContent = 'No tracks found';
        return;
    }

    var text = state.tracks.length + ' track' + (state.tracks.length !== 1 ? 's' : '');
    if (options.loadingMore) {
        text += ' (loading more...)';
    } else if (options.searchLimitReached) {
        text += ' (increase Search Limit in Settings)';
    }
    elLibCount.textContent = text;
}

function getInitialLibraryBatchSize(searchLimit) {
    return Math.max(1, Math.min(INITIAL_LIBRARY_BATCH_SIZE, normalizeSearchLimit(searchLimit)));
}

function getBackgroundLibraryBatchSize(searchLimit) {
    return Math.max(1, Math.min(BACKGROUND_LIBRARY_BATCH_SIZE, normalizeSearchLimit(searchLimit)));
}

function buildLibraryUrl(offset, limit, force, searchText) {
    var url = 'api/library?offset=' + Math.max(0, Number(offset) || 0) + '&limit=' + Math.max(1, Number(limit) || 1);
    if (force) url += '&force=true';
    var search = String(arguments.length >= 4 ? arguments[3] : '').trim();
    if (search) url += '&query=' + encodeURIComponent(search);
    return url;
}

function formatRelayStatus(payload) {
    var relay = (payload && payload.relay && typeof payload.relay === 'object') ? payload.relay : {};
    var explicitMessage = (payload && typeof payload.message === 'string') ? payload.message.trim() : '';
    if (explicitMessage) return explicitMessage;

    var statusValue = relay.status;
    if (typeof statusValue === 'string' && statusValue.trim()) {
        return 'state=' + statusValue.trim();
    }

    if (statusValue && typeof statusValue === 'object') {
        var stateText = String(
            statusValue.state || statusValue.playback_state || statusValue.status || ''
        ).trim();
        var trackText = String(
            statusValue.track || statusValue.file_name || statusValue.current_file || ''
        ).trim();
        var posValue = Number(
            statusValue.position_seconds || statusValue.position || statusValue.offset_seconds || NaN
        );

        var segments = [];
        if (stateText) segments.push('state=' + stateText);
        if (trackText) segments.push('track=' + trackText);
        if (Number.isFinite(posValue) && posValue >= 0) segments.push('position=' + Math.round(posValue) + 's');
        if (segments.length) return segments.join(' | ');
    }

    if (typeof relay.state === 'string' && relay.state.trim()) {
        return 'state=' + relay.state.trim();
    }

    return 'done';
}

//  Playlist Rendering 
function findTrackIndex(track) {
    if (!track) return -1;
    var trackKey = String(track.id || track.stream_url || track.file_name || '');
    for (var i = 0; i < state.tracks.length; i++) {
        var t = state.tracks[i];
        var key = String(t.id || t.stream_url || t.file_name || '');
        if (key && key === trackKey) {
            return i;
        }
    }
    return -1;
}

function loadCustomPlaylist() {
    try {
        var data = localStorage.getItem('ay-custom-playlist');
        if (data) {
            playlistState.tracks = JSON.parse(data);
        }
    } catch (_) {}
}

function saveCustomPlaylist() {
    try {
        localStorage.setItem('ay-custom-playlist', JSON.stringify(playlistState.tracks));
    } catch (_) {}
}

function findCurrentPlaylistTrack(savedTrack, libraryTracks) {
    if (!savedTrack || !libraryTracks.length) return null;

    var savedId = String(savedTrack.id || '').trim();
    var savedPath = String(savedTrack.relative_path || '').trim().toLowerCase();
    var savedFileName = String(savedTrack.file_name || '').trim().toLowerCase();
    var savedTitle = String(savedTrack.title || '').trim().toLowerCase();
    var savedArtist = String(savedTrack.artist || '').trim().toLowerCase();
    var currentTrack = libraryTracks.find(function (track) {
        return savedId && String(track.id || '').trim() === savedId;
    });
    if (!currentTrack && savedPath) {
        var pathMatches = libraryTracks.filter(function (track) {
            return String(track.relative_path || '').trim().toLowerCase() === savedPath;
        });
        currentTrack = pathMatches.length === 1 ? pathMatches[0] : null;
    }
    if (!currentTrack && savedFileName) {
        var nameMatches = libraryTracks.filter(function (track) {
            return String(track.file_name || '').trim().toLowerCase() === savedFileName
                && (!savedTitle || String(track.title || '').trim().toLowerCase() === savedTitle)
                && (!savedArtist || String(track.artist || '').trim().toLowerCase() === savedArtist);
        });
        currentTrack = nameMatches.length === 1 ? nameMatches[0] : null;
    }
    return currentTrack || null;
}

function refreshCustomPlaylistTracks(libraryTracks) {
    if (!playlistState.tracks.length || !libraryTracks.length) return false;

    var changed = false;
    var seen = {};
    var refreshed = playlistState.tracks.map(function (savedTrack) {
        var currentTrack = findCurrentPlaylistTrack(savedTrack, libraryTracks);
        if (currentTrack && (currentTrack.id !== savedTrack.id || currentTrack.stream_url !== savedTrack.stream_url)) {
            changed = true;
        }
        return currentTrack || savedTrack;
    }).filter(function (track) {
        var key = String(track.id || track.stream_url || track.file_name || '');
        if (!key || seen[key]) {
            changed = true;
            return false;
        }
        seen[key] = true;
        return true;
    });

    if (changed) {
        playlistState.tracks = refreshed;
        state.playlistQueueDirty = true;
        saveCustomPlaylist();
    }
    return changed;
}

function resolveSavedPlaylistTrack(track, index) {
    var query = String(track.file_name || track.title || '').trim();
    if (!query) {
        setStatus('Saved track is no longer available.');
        return;
    }
    setBufferingIndicator(true, 'Refreshing saved track...');
    apiJson(buildLibraryUrl(0, 2, false, query)).then(function (payload) {
        var candidates = Array.isArray(payload.tracks) ? payload.tracks : [];
        var currentTrack = !payload.has_more && findCurrentPlaylistTrack(track, candidates);
        if (!currentTrack || playlistState.tracks[index] !== track) {
            throw new Error('Saved track is no longer available.');
        }
        playlistState.tracks[index] = currentTrack;
        state.playlistQueueDirty = true;
        saveCustomPlaylist();
        if (state.currentTab === 'playlist') renderPlaylistView();
        playTrack(currentTrack, index, 'playlist', true);
    }).catch(function (error) {
        setBufferingIndicator(false);
        setStatus((error && error.message) || 'Saved track is no longer available.');
    });
}

function addToPlaylist(track) {
    var exists = playlistState.tracks.some(function (t) {
        return String(t.id || t.stream_url || t.file_name) === String(track.id || track.stream_url || track.file_name);
    });
    if (!exists) {
        playlistState.tracks.push(track);
        saveCustomPlaylist();
        flashStatus('Added to playlist: ' + (track.title || track.file_name), fallbackStatusText());
        if (state.currentTab === 'playlist') {
            renderPlaylistView();
        }
    } else {
        flashStatus('Song already in playlist', fallbackStatusText());
    }
}

function removeFromPlaylist(trackIndex) {
    if (trackIndex >= 0 && trackIndex < playlistState.tracks.length) {
        var track = playlistState.tracks[trackIndex];
        playlistState.tracks.splice(trackIndex, 1);
        saveCustomPlaylist();
        flashStatus('Removed from playlist', fallbackStatusText());
        
        if (state.activeQueueType === 'playlist') {
            var activeIdx = window.Amplitude ? Number(window.Amplitude.getActiveIndex()) : -1;
            var audioEl = getAmplitudeAudioElement();
            var isPlaying = audioEl && !audioEl.paused;
            initAmplitude(playlistState.tracks);
            if (playlistState.tracks.length > 0) {
                var nextIdx = activeIdx;
                if (nextIdx >= playlistState.tracks.length) {
                    nextIdx = playlistState.tracks.length - 1;
                }
                if (isPlaying) {
                    window.Amplitude.playSongAtIndex(nextIdx);
                }
            }
        }
        
        renderPlaylistView();
    }
}

function playTrack(track, index, type, skipPlaylistRefresh) {
    if (type === 'playlist' && !skipPlaylistRefresh && findTrackIndex(track) === -1) {
        resolveSavedPlaylistTrack(track, index);
        return;
    }
    if (!browserCanPlayTrack(track)) {
        rejectUnsupportedTrack(track);
        return;
    }
    playbackHealth.userPlaybackRequested = true;
    playbackHealth.loadFailureSkips = 0;
    setBufferingIndicator(true, 'Fetching track from network...');
    // Re-init Amplitude when the active queue type changes, or when the library
    // queue was refreshed since it was last loaded into Amplitude. Without the
    // dirty check, a rescan updates state.tracks + the rendered list but leaves
    // Amplitude holding the old songs, so playSongAtIndex would play the stale
    // pre-refresh track at that index.
    var libraryStale = (type === 'library' && state.libraryQueueDirty);
    var playlistStale = (type === 'playlist' && state.playlistQueueDirty);
    if (state.activeQueueType !== type || libraryStale || playlistStale) {
        state.activeQueueType = type;
        var tracksToLoad = (type === 'playlist') ? playlistState.tracks : state.tracks;
        initAmplitude(tracksToLoad);
        if (type === 'library') state.libraryQueueDirty = false;
        if (type === 'playlist') state.playlistQueueDirty = false;
    }
    if (window.Amplitude) {
        window.Amplitude.playSongAtIndex(index);
    }
}

function switchPlaylistTab(tab) {
    if (state.currentTab === tab) return;
    state.currentTab = tab;
    
    var tabLibrary = document.getElementById('tab-library');
    var tabPlaylist = document.getElementById('tab-playlist');
    if (tabLibrary) tabLibrary.classList.toggle('active', tab === 'library');
    if (tabPlaylist) tabPlaylist.classList.toggle('active', tab === 'playlist');
    
    if (elSearchToggleBtn) {
        elSearchToggleBtn.style.display = (tab === 'playlist') ? 'none' : '';
    }
    if (tab === 'playlist') {
        closeSearch();
    }
    
    renderPlaylistView();
}
window.switchPlaylistTab = switchPlaylistTab;

function renderPlaylistView(tracksToRender) {
    elPlaylist.innerHTML = '';
    if (state.currentTab === 'playlist') {
        appendPlaylistTracks(playlistState.tracks, 'playlist');
    } else {
        var list = tracksToRender || (state.searchQuery ? [] : state.tracks);
        appendPlaylistTracks(list, 'library');
    }
    if (window.Amplitude) {
        window.Amplitude.bindNewElements();
    }
    // Also sync count
    var countText = '';
    if (state.currentTab === 'playlist') {
        countText = playlistState.tracks.length + ' song(s) queued';
    } else {
        countText = state.tracks.length + ' song(s) in library';
    }
    if (elLibCount) elLibCount.textContent = countText;
}

function syncStateToParent() {
    if (!window.parent || window.parent === window) return;
    
    var audioEl = getAmplitudeAudioElement();
    var activeIndex = window.Amplitude ? window.Amplitude.getActiveIndex() : -1;
    var currentQueue = (state.activeQueueType === 'playlist') ? playlistState.tracks : state.tracks;
    var activeTrack = (window.Amplitude && Number.isFinite(Number(activeIndex)) && activeIndex >= 0 && activeIndex < currentQueue.length)
        ? currentQueue[activeIndex] : null;
        
    window.parent.__autoyou_persistent_audio = {
        playing: audioEl ? !audioEl.paused : false,
        track: activeTrack ? {
            title: activeTrack.title || activeTrack.file_name || 'Unknown',
            artist: activeTrack.artist || 'Local Library',
            id: activeTrack.id,
            cover_art_url: activeTrack.id ? resolveAbsoluteApiPath('api/artwork/' + activeTrack.id) : ''
        } : null,
        duration: audioEl && Number.isFinite(audioEl.duration) ? audioEl.duration : 0,
        currentTime: audioEl ? audioEl.currentTime : 0,
        playlist: currentQueue,
        activeIndex: activeIndex,
        
        play: function() {
            if (window.Amplitude) window.Amplitude.play();
        },
        pause: function() {
            if (window.Amplitude) window.Amplitude.pause();
        },
        next: function() {
            if (window.Amplitude) window.Amplitude.next();
        },
        prev: function() {
            if (window.Amplitude) window.Amplitude.prev();
        }
    };
}

function appendPlaylistTracks(tracks, type, startIndex) {
    tracks.forEach(function (track, relativeIndex) {
        var index;
        if (type === 'library') {
            index = findTrackIndex(track);
            if (index === -1) {
                index = (typeof startIndex === 'number') ? (startIndex + relativeIndex) : relativeIndex;
            }
        } else {
            index = relativeIndex;
        }

        // Keep the library's absolute number in both the visible row and its
        // accessibility label. Mobile WebViews flatten a list item to its
        // aria-label, so omitting the number made a correctly rendered "81"
        // look like an unnumbered/restarted page to UI automation and screen
        // readers after pagination.
        var displayIndex = (type === 'library') ? index : relativeIndex;
        var trackLabel = track.title || track.file_name || ('Track ' + (index + 1));

        var li = document.createElement('li');
        li.className = 'playlist-item amplitude-song-container';
        li.setAttribute('data-amplitude-song-index', String(index));
        li.setAttribute('role', 'listitem');
        li.setAttribute(
            'aria-label',
            type === 'library' ? String(displayIndex + 1) + '. ' + trackLabel : trackLabel
        );

        li.addEventListener('click', function(e) {
            if (e.target.closest('.pl-action-btn')) return;
            playTrack(track, index, type);
        });

        // Index indicator / active dot. Library rows must show their absolute
        // position in the loaded library: paginated batches would otherwise
        // restart at "1" and make the list look truncated.
        var idxSpan = document.createElement('span');
        idxSpan.className = 'pl-index';
        idxSpan.textContent = String(displayIndex + 1);

        var dotSpan = document.createElement('span');
        dotSpan.className = 'pl-active-dot';
        dotSpan.setAttribute('aria-hidden', 'true');
        dotSpan.textContent = '\u266a';

        // Track text
        var textDiv = document.createElement('div');
        textDiv.className = 'pl-text';

        var nameP = document.createElement('p');
        nameP.className = 'pl-name';
        nameP.textContent = track.title || track.file_name || ('Track ' + (index + 1));

        var metaP = document.createElement('p');
        metaP.className = 'pl-meta';
        metaP.textContent = (track.artist || 'Local Library') + (track.relative_path ? ' \u00b7 ' + track.relative_path : '');

        textDiv.appendChild(nameP);
        textDiv.appendChild(metaP);

        li.appendChild(idxSpan);
        li.appendChild(dotSpan);
        li.appendChild(textDiv);

        // Action button
        var actionBtn = document.createElement('button');
        actionBtn.className = 'pl-action-btn';
        if (type === 'playlist') {
            actionBtn.className += ' remove';
            actionBtn.textContent = '\u00d7';
            actionBtn.title = 'Remove from playlist';
            actionBtn.setAttribute('aria-label', 'Remove from playlist');
            actionBtn.addEventListener('click', function(e) {
                e.stopPropagation();
                removeFromPlaylist(index);
            });
        } else {
            actionBtn.textContent = '+';
            actionBtn.title = 'Add to playlist';
            actionBtn.setAttribute('aria-label', 'Add to playlist');
            actionBtn.addEventListener('click', function(e) {
                e.stopPropagation();
                addToPlaylist(track);
            });
        }
        li.appendChild(actionBtn);

        elPlaylist.appendChild(li);
    });
}

function renderPlaylist(tracks) {
    renderPlaylistView(tracks);
}

function trackToAmplitudeSong(track) {
    return {
        name:          track.title || track.file_name || 'Unknown',
        artist:        track.artist || 'Local Library',
        album:         'AutoYou',
        url:           resolveAbsoluteApiPath(track.stream_url),
        cover_art_url: track.id ? resolveAbsoluteApiPath('api/artwork/' + track.id) : '',
        content_type:  track.content_type || '',
    };
}

//  Media Session API (native iOS/Android lock screen and Control Center)
var _mediaSessionRegistered = false;

function resolveAbsoluteApiPath(path) {
    var rel = resolveApiPath(path);
    if (!rel) return '';
    try { return new URL(rel, window.location.href).href; } catch (_) { return rel; }
}

function updateMediaSession(track) {
    if (!('mediaSession' in navigator) || !track) return;
    var artUrl = track.id ? resolveAbsoluteApiPath('api/artwork/' + track.id) : '';
    try {
        navigator.mediaSession.metadata = new MediaMetadata({
            title:   track.title  || track.file_name || 'Unknown',
            artist:  track.artist || 'Local Library',
            album:   'AutoYou Audio',
            artwork: artUrl ? [
                { src: artUrl, sizes: '256x256', type: 'image/jpeg' },
                { src: artUrl, sizes: '512x512', type: 'image/jpeg' },
            ] : [],
        });
    } catch (_) {}

    if (_mediaSessionRegistered) return;
    _mediaSessionRegistered = true;
    try {
        navigator.mediaSession.setActionHandler('play', function () {
            if (window.Amplitude) { try { window.Amplitude.play(); } catch (_) {} }
        });
        navigator.mediaSession.setActionHandler('pause', function () {
            if (window.Amplitude) { try { window.Amplitude.pause(); } catch (_) {} }
        });
        navigator.mediaSession.setActionHandler('nexttrack', function () {
            if (window.Amplitude) { try { window.Amplitude.next(); } catch (_) {} }
        });
        navigator.mediaSession.setActionHandler('previoustrack', function () {
            if (window.Amplitude) { try { window.Amplitude.prev(); } catch (_) {} }
        });
        navigator.mediaSession.setActionHandler('seekbackward', function (details) {
            var audioEl = getAmplitudeAudioElement();
            if (!audioEl) return;
            var offset = (details && details.seekOffset) ? details.seekOffset : 10;
            audioEl.currentTime = Math.max(0, audioEl.currentTime - offset);
        });
        navigator.mediaSession.setActionHandler('seekforward', function (details) {
            var audioEl = getAmplitudeAudioElement();
            if (!audioEl) return;
            var offset = (details && details.seekOffset) ? details.seekOffset : 10;
            var dur = audioEl.duration;
            audioEl.currentTime = Number.isFinite(dur)
                ? Math.min(dur, audioEl.currentTime + offset)
                : audioEl.currentTime + offset;
        });
        try {
            navigator.mediaSession.setActionHandler('stop', function () {
                if (window.Amplitude) { try { window.Amplitude.pause(); } catch (_) {} }
            });
        } catch (_) {}
    } catch (_) {}
}

function updateMediaSessionPlaybackState(playing) {
    if (!('mediaSession' in navigator)) return;
    try { navigator.mediaSession.playbackState = playing ? 'playing' : 'paused'; } catch (_) {}
}

function updateMediaSessionPosition() {
    if (!('mediaSession' in navigator) || !('setPositionState' in navigator.mediaSession)) return;
    var audioEl = getAmplitudeAudioElement();
    if (!audioEl) return;
    var duration = audioEl.duration;
    var position = audioEl.currentTime;
    if (!Number.isFinite(duration) || duration <= 0 || !Number.isFinite(position)) return;
    try {
        navigator.mediaSession.setPositionState({
            duration: duration,
            playbackRate: audioEl.playbackRate || 1,
            position: Math.min(Math.max(0, position), duration),
        });
    } catch (_) {}
}

//  Amplitude Initialization 
function initAmplitude(tracks) {
    if (state.initialized) {
        // Stop relay on the remote end before tearing down local playback
        if (state.settings && state.settings.auto_relay_on_play) {
            relayAction('stop', {}).catch(function () {});
        }
        // Fully stop and clear the audio element so the old stream cannot bleed into the new one
        try { window.Amplitude.stop(); } catch (_) {}
        var oldAudio = getAmplitudeAudioElement();
        if (oldAudio) {
            try { oldAudio.pause(); oldAudio.src = ''; oldAudio.load(); } catch (_) {}
        }
        try { window.Amplitude.init({ songs: [] }); } catch (_) {}
        state.initialized = false;
    }
    var songs = tracks.map(trackToAmplitudeSong);

    window.Amplitude.init({
        songs: songs,
        volume: 80,
        continue_next: true,
        callbacks: {
            play: function () {
                playbackHealth.userPlaybackRequested = true;
                var track = getActiveTrack();
                if (track) updateMediaSession(track);
                updateMediaSessionPlaybackState(true);
                maybeRelayCurrentTrack();
                // Notify iOS native observer immediately after metadata is set so the title/artist
                // are cached before WkWebView timeupdate events can overwrite nowPlayingInfo.
                if (window.__autoyouBackgroundMediaState && typeof window.__autoyouBackgroundMediaState.triggerUpdate === 'function') {
                    window.__autoyouBackgroundMediaState.triggerUpdate();
                }
            },
            pause: function () {
                updateMediaSessionPlaybackState(false);
                // Notify the iOS native background-media observer immediately so
                // latestMediaSnapshot flips to inactive on pause. Without this the
                // native side could keep a stale "playing" snapshot and resume the
                // paused track through the background handoff when the app suspends.
                if (window.__autoyouBackgroundMediaState && typeof window.__autoyouBackgroundMediaState.triggerUpdate === 'function') {
                    window.__autoyouBackgroundMediaState.triggerUpdate();
                }
            },
            song_change: function () {
                var track = getActiveTrack();
                if (track && !browserCanPlayTrack(track)) {
                    handleCurrentTrackLoadFailure('Skipped unsupported audio.');
                    return;
                }
                if (track) updateMediaSession(track);
                // Notify iOS native observer so the new song title is cached promptly.
                if (window.__autoyouBackgroundMediaState && typeof window.__autoyouBackgroundMediaState.triggerUpdate === 'function') {
                    window.__autoyouBackgroundMediaState.triggerUpdate();
                }
            },
        },
    });
    setBufferingIndicator(false);
    state.initialized = true;
    bindAudioRecoveryHandlers();
    bindPlaybackVisibilityWatchers();
    startPlaybackWatchdog();
    applyTransportModes();
    syncTransportPlaybackStateFromAudio();
}

function normalizeRepeatMode(mode) {
    var normalized = String(mode || 'off').trim().toLowerCase();
    return (normalized === 'one' || normalized === 'all') ? normalized : 'off';
}

function nextRepeatMode(mode) {
    var normalized = normalizeRepeatMode(mode);
    if (normalized === 'off') return 'all';
    if (normalized === 'all') return 'one';
    return 'off';
}

function applyTransportModes() {
    if (!window.Amplitude) return;
    var shuffleEnabled = Boolean(state.settings && state.settings.shuffle_enabled);
    var repeatMode = normalizeRepeatMode(state.settings && state.settings.repeat_mode);

    try {
        if (typeof window.Amplitude.setShuffle === 'function') {
            window.Amplitude.setShuffle(shuffleEnabled);
        }
        if (typeof window.Amplitude.setRepeat === 'function') {
            window.Amplitude.setRepeat(repeatMode === 'all');
        }
        // NOTE: In this AmplitudeJS build the public setRepeatSong() is a toggle -
        // it ignores its boolean argument and flips the current value. Calling it as
        // a setter desynced repeat_song from the UI and made songs repeat unexpectedly.
        // getConfig() returns the live config object, so set the flag authoritatively;
        // fall back to the toggle only if the config is somehow unreadable.
        var wantRepeatOne = (repeatMode === 'one');
        var amplitudeConfig = (typeof window.Amplitude.getConfig === 'function') ? window.Amplitude.getConfig() : null;
        if (amplitudeConfig) {
            amplitudeConfig.repeat_song = wantRepeatOne;
        } else if (typeof window.Amplitude.setRepeatSong === 'function' &&
                   window.Amplitude.getConfig && window.Amplitude.getConfig().repeat_song !== wantRepeatOne) {
            window.Amplitude.setRepeatSong(wantRepeatOne);
        }
    } catch (_) {}

    syncTransportModeButtons();
}

function syncTransportModeButtons() {
    var shuffleEnabled = Boolean(state.settings && state.settings.shuffle_enabled);
    var repeatMode = normalizeRepeatMode(state.settings && state.settings.repeat_mode);

    Array.prototype.forEach.call(document.querySelectorAll('.amplitude-shuffle'), function (button) {
        button.classList.toggle('amplitude-shuffle-on', shuffleEnabled);
        button.classList.toggle('amplitude-shuffle-off', !shuffleEnabled);
        button.setAttribute('aria-pressed', shuffleEnabled ? 'true' : 'false');
        button.setAttribute('title', shuffleEnabled ? 'Shuffle on' : 'Shuffle off');
    });

    Array.prototype.forEach.call(document.querySelectorAll('.amplitude-repeat'), function (button) {
        button.classList.toggle('amplitude-repeat-on', repeatMode === 'all');
        button.classList.toggle('amplitude-repeat-one', repeatMode === 'one');
        button.classList.toggle('amplitude-repeat-off', repeatMode === 'off');
        button.setAttribute('aria-pressed', repeatMode === 'off' ? 'false' : 'true');
        button.setAttribute('title', repeatMode === 'one' ? 'Loop song (repeat one)' : (repeatMode === 'all' ? 'Repeat all' : 'Repeat off - click to enable'));
    });

    if (elRepeatBadge) {
        if (repeatMode === 'one') {
            elRepeatBadge.textContent = '1';
            elRepeatBadge.hidden = false;
        } else if (repeatMode === 'all') {
            elRepeatBadge.textContent = 'all';
            elRepeatBadge.hidden = false;
        } else {
            elRepeatBadge.hidden = true;
        }
    }
}

function persistTransportSettings() {
    return apiJson('api/settings', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(state.settings || {}),
    }).then(function (payload) {
        state.settings = payload.settings || state.settings;
        applySettingsToForm();
        applyTransportModes();
        return payload;
    }).catch(function () {});
}

function toggleShuffleMode() {
    state.settings.shuffle_enabled = !Boolean(state.settings && state.settings.shuffle_enabled);
    applyTransportModes();
    flashStatus(state.settings.shuffle_enabled ? 'Shuffle on' : 'Shuffle off', fallbackStatusText());
    persistTransportSettings();
}

function cycleRepeatMode() {
    state.settings.repeat_mode = nextRepeatMode(state.settings && state.settings.repeat_mode);
    applyTransportModes();
    var label = state.settings.repeat_mode === 'one'
        ? 'Repeat one'
        : (state.settings.repeat_mode === 'all' ? 'Repeat all' : 'Repeat off');
    flashStatus(label, fallbackStatusText());
    persistTransportSettings();
}

function appendTracksToAmplitude(tracks) {
    if (!tracks.length || !window.Amplitude) return;
    if (!state.initialized || typeof window.Amplitude.addSong !== 'function') {
        initAmplitude(state.tracks);
        return;
    }
    tracks.forEach(function (track) {
        window.Amplitude.addSong(trackToAmplitudeSong(track));
    });
}

function appendUniqueTracks(tracks) {
    var additions = [];
    var seen = {};

    state.tracks.forEach(function (track) {
        var key = String((track && track.id) || (track && track.stream_url) || (track && track.file_name) || '');
        if (key) seen[key] = true;
    });

    (tracks || []).forEach(function (track) {
        var key = String((track && track.id) || (track && track.stream_url) || (track && track.file_name) || '');
        if (key && seen[key]) return;
        if (key) seen[key] = true;
        state.tracks.push(track);
        additions.push(track);
    });

    return additions;
}

function fallbackStatusText() {
    if (state.searchQuery) return 'Search results';
    if (!state.tracks.length) return '';
    return 'Loaded from your library.';
}

function updatePaginationFromPayload(payload, overallLimit) {
    var reachedSearchLimit = state.tracks.length >= overallLimit;
    state.libraryPagination = {
        hasMore: Boolean(payload && payload.has_more) && !reachedSearchLimit,
        nextOffset: payload && Number.isFinite(Number(payload.next_offset)) ? Number(payload.next_offset) : null,
        overallLimit: overallLimit,
        loadingMore: false,
    };
}

function finalizeLibraryStatus(hasMore, overallLimit, cached) {
    if (hasMore && state.tracks.length >= overallLimit) {
        setStatus('Loaded first ' + overallLimit + ' tracks. Increase Search Limit in Settings to load more.');
        updateLibraryCount({ searchLimitReached: true });
        return;
    }

    updateLibraryCount({ loadingMore: false, searchLimitReached: false });
    if (hasMore && state.tracks.length) {
        setStatus('Loaded ' + state.tracks.length + ' tracks. Scroll for more.');
    } else if (cached && state.tracks.length) {
        setStatus('Loaded from your library.');
    } else if (state.tracks.length) {
        setStatus('Library ready.');
    } else {
        setStatus('');
    }
}

function loadLibraryPage(loadId, offset, overallLimit) {
    if (loadId !== state.libraryLoadId) return Promise.resolve(false);
    if (state.searchQuery) return Promise.resolve(false);

    var remaining = overallLimit - state.tracks.length;
    if (remaining <= 0) {
        finalizeLibraryStatus(true, overallLimit, false);
        return Promise.resolve(false);
    }

    var pageSize = Math.min(getBackgroundLibraryBatchSize(overallLimit), remaining);
    state.libraryPagination.loadingMore = true;
    updateLibraryCount({ loadingMore: true });
    setStatus('Loading more tracks...');
    return apiJson(buildLibraryUrl(offset, pageSize, false, ''))
        .then(function (payload) {
            if (loadId !== state.libraryLoadId) return false;

            var priorCount = state.tracks.length;
            var additions = appendUniqueTracks(Array.isArray(payload.tracks) ? payload.tracks : []);
            var playlistChanged = refreshCustomPlaylistTracks(state.tracks);
            if (additions.length) {
                appendTracksToAmplitude(additions);
                if (state.currentTab === 'library') {
                    appendPlaylistTracks(additions, 'library', priorCount);
                }
            }
            if (playlistChanged && state.currentTab === 'playlist') {
                renderPlaylistView();
            }

            var reachedSearchLimit = state.tracks.length >= overallLimit;
            var hasMore = Boolean(payload.has_more) && !reachedSearchLimit;
            updatePaginationFromPayload(payload, overallLimit);
            updateLibraryCount({
                loadingMore: false,
                searchLimitReached: Boolean(payload.has_more) && reachedSearchLimit,
            });
            finalizeLibraryStatus(hasMore, overallLimit, Boolean(payload.cached));
            return true;
        })
        .catch(function () {
            if (loadId !== state.libraryLoadId) return false;
            state.libraryPagination.loadingMore = false;
            updateLibraryCount({ loadingMore: false, searchLimitReached: false });
            setStatus('Loaded ' + state.tracks.length + ' tracks. More tracks could not load; tap refresh to retry.');
            // Resolve false so the recursive viewport-fill chain stops. The
            // existing offset remains available for a later scroll or refresh.
            return false;
        });
}

function playlistDistanceFromBottom() {
    if (!elPlaylist) return Infinity;
    // Preferred: the playlist is its own scroll container (desktop layout).
    if (elPlaylist.scrollHeight > elPlaylist.clientHeight + 4) {
        return elPlaylist.scrollHeight - elPlaylist.scrollTop - elPlaylist.clientHeight;
    }
    // Mobile WebViews (iOS WKWebView / Android WebView) often unlock the page
    // body as the scroller instead, so the list never overflows its own box and
    // element-scroll events never fire. Measure how far the list bottom sits
    // below the visual viewport in that case.
    var rect = elPlaylist.getBoundingClientRect();
    var viewportHeight = (window.visualViewport && window.visualViewport.height)
        || window.innerHeight
        || document.documentElement.clientHeight
        || 0;
    return rect.bottom - viewportHeight;
}

function maybeLoadMoreLibrary() {
    if (!elPlaylist || state.searchQuery) return;
    var pagination = state.libraryPagination || {};
    if (!pagination.hasMore || pagination.loadingMore || pagination.nextOffset === null) return;
    if (playlistDistanceFromBottom() > 260) return;
    loadLibraryPage(state.libraryLoadId, pagination.nextOffset, pagination.overallLimit || 12000)
        .then(function (loaded) {
            if (loaded) maybeLoadMoreLibrary();
        });
}

function applyLibraryPayload(payload, loadId, overallLimit, options) {
    options = options || {};
    if (loadId !== state.libraryLoadId) return;

    if (payload && Array.isArray(payload.audio_paths)) {
        renderAudioPaths(payload.audio_paths);
    }

    var newTracks = Array.isArray(payload.tracks) ? payload.tracks : [];

    if (options.searchQuery) {
        newTracks.forEach(function (track) {
            var idx = findTrackIndex(track);
            if (idx === -1) {
                state.tracks.push(track);
                if (window.Amplitude) {
                    window.Amplitude.addSong(trackToAmplitudeSong(track));
                }
            }
        });
        
        renderPlaylistView(newTracks);
        
        var reachedSearchLimit = newTracks.length >= overallLimit;
        var hasMore = Boolean(payload.has_more) && !reachedSearchLimit;
        setStatus(hasMore ? 'Showing first matches. Refine search for fewer results.' : 'Search results');
        updateLibraryCount({ searchResultsCount: newTracks.length });
        return;
    }

    state.tracks = newTracks.slice();
    refreshCustomPlaylistTracks(state.tracks);
    updatePaginationFromPayload(payload, overallLimit);
    // The library array was just replaced (initial load or rescan/refresh). If
    // the library queue is already active in Amplitude, mark it dirty so the
    // next play re-inits Amplitude with the fresh tracks instead of playing a
    // stale row. The fresh-init path below handles the first-ever load.
    if (state.activeQueueType === 'library') {
        state.libraryQueueDirty = true;
    }

    elLoading.style.display = 'none';
    elPlayer.classList.remove('hidden');

    if (state.currentTab === 'library') {
        renderPlaylistView();
    }

    if (!state.activeQueueType) {
        state.activeQueueType = 'library';
        initAmplitude(state.tracks);
    }

    if (!state.tracks.length) {
        updateLibraryCount();
        setStatus('Scan complete - no audio files detected.');
        return;
    }

    var reachedSearchLimit = state.tracks.length >= overallLimit;
    var hasMore = Boolean(payload.has_more) && !reachedSearchLimit;
    updateLibraryCount({
        loadingMore: false,
        searchLimitReached: Boolean(payload.has_more) && reachedSearchLimit,
    });

    finalizeLibraryStatus(hasMore, overallLimit, Boolean(payload.cached));
    // Fill a short viewport, then stop. Further pages load only when the user
    // nears the bottom; do not eagerly materialize up to 12,000 rows.
    maybeLoadMoreLibrary();
}

//  Library Load 
function loadLibrary(force) {
    force = Boolean(force);
    state.searchQuery = '';
    if (elTrackSearch) elTrackSearch.value = '';
    state.libraryLoadId += 1;
    var loadId = state.libraryLoadId;
    if (elLoadingText) elLoadingText.textContent = force ? 'Rescanning library...' : 'Loading library...';

    var overallLimit = normalizeSearchLimit((state.settings && state.settings.search_limit) || 12000);
    var initialLimit = Math.min(getInitialLibraryBatchSize(overallLimit), overallLimit);

    return apiJson(buildLibraryUrl(0, initialLimit, force, ''))
        .then(function (payload) {
            applyLibraryPayload(payload, loadId, overallLimit);
        })
        .catch(function (err) {
            if (loadId !== state.libraryLoadId) return;
            // Always make sure loading screen is visible with the error (not blank)
            elPlayer.classList.add('hidden');
            elLoading.style.display = '';
            if (elLoadingText) {
                var msg = err && err.name === 'AbortError' ? 'Request timed out. Tap refresh to retry.' : ('Load failed: ' + (err ? err.message : 'unknown error'));
                elLoadingText.innerHTML = msg + '<br><small style="opacity:.6">Tap refresh to retry</small>';
            }
        });
}

function searchLibrary(query) {
    var searchText = String(query || '').trim();
    if (!searchText) {
        return loadLibrary(false);
    }

    state.searchQuery = searchText;
    state.libraryLoadId += 1;
    var loadId = state.libraryLoadId;
    var limit = Math.min(SEARCH_LIBRARY_BATCH_SIZE, normalizeSearchLimit((state.settings && state.settings.search_limit) || 12000));
    setStatus('Searching...');
    return apiJson(buildLibraryUrl(0, limit, false, searchText))
        .then(function (payload) {
            applyLibraryPayload(payload, loadId, limit, { searchQuery: searchText });
        })
        .catch(function (err) {
            if (loadId !== state.libraryLoadId) return;
            setStatus('Search failed: ' + (err ? err.message : 'unknown error'));
        });
}

function scheduleLibrarySearch() {
    if (!elTrackSearch) return;
    if (state.searchTimerId) {
        clearTimeout(state.searchTimerId);
        state.searchTimerId = null;
    }
    var query = String(elTrackSearch.value || '').trim();
    state.searchTimerId = setTimeout(function () {
        state.searchTimerId = null;
        searchLibrary(query);
    }, 350);
}

//  Auto Relay 
function maybeRelayCurrentTrack() {
    if (!state.settings.auto_relay_on_play) return;
    if (!window.Amplitude) return;
    var track = getActiveTrack();
    if (!track) return;
    relayAction('play', { track_id: track.id }).catch(function () {});
}

//  Settings Form Helpers
function normalizedAudioSourceSettings(settings) {
    var sources = settings && settings.audio_sources && typeof settings.audio_sources === 'object'
        ? settings.audio_sources
        : {};
    return {
        voice_transcriptions: Boolean(sources.voice_transcriptions !== false),
        page_feed_audio: Boolean(sources.page_feed_audio !== false),
        notes_media_audio: Boolean(sources.notes_media_audio !== false),
    };
}

function renderAudioPaths(paths) {
    state.audioPaths = Array.isArray(paths) ? paths.slice() : [];
    if (!elAudioPathList) return;
    while (elAudioPathList.firstChild) elAudioPathList.removeChild(elAudioPathList.firstChild);

    if (!state.audioPaths.length) {
        var empty = document.createElement('li');
        empty.className = 'audio-path-value';
        empty.textContent = 'No server paths selected.';
        elAudioPathList.appendChild(empty);
        return;
    }

    state.audioPaths.forEach(function (item) {
        var row = document.createElement('li');
        row.className = 'audio-path-item';
        var label = document.createElement('span');
        label.className = 'audio-path-label';
        label.textContent = String(item && item.label || 'Server path');
        var value = document.createElement('span');
        value.className = 'audio-path-value' + (item && item.exists ? '' : ' audio-path-missing');
        value.textContent = String(item && item.path || '') + (item && item.exists ? '' : ' (not found)');
        row.appendChild(label);
        row.appendChild(value);
        elAudioPathList.appendChild(row);
    });
}

function resolveAgentApiPath(agentName, path) {
    var agent = String(agentName || '').trim();
    var relative = String(path || '').replace(/^\/+/, '');
    if (!agent) return '/' + relative;
    var pathname = String((window.location && window.location.pathname) || '/');
    var marker = '/agent/audio_agent';
    var idx = pathname.indexOf(marker);
    var base = idx >= 0
        ? pathname.slice(0, idx) + '/agent/' + agent + '/'
        : '/agent/' + agent + '/';
    return base + relative;
}

function setAudioUploadStatus(message) {
    if (elAudioUploadStatus) elAudioUploadStatus.textContent = String(message || '');
}

function uploadAudioFiles() {
    var files = elAudioUploadInput && elAudioUploadInput.files
        ? Array.prototype.slice.call(elAudioUploadInput.files)
        : [];
    if (!files.length) {
        setAudioUploadStatus('Choose one or more audio files first.');
        return Promise.resolve(false);
    }

    if (elAudioUploadBtn) elAudioUploadBtn.disabled = true;
    setAudioUploadStatus('Uploading 0 of ' + files.length + '...');
    var uploaded = 0;
    var blobPath = resolveAgentApiPath('page_agent', 'api/blob');
    var feedPath = resolveAgentApiPath('page_agent', 'api/feed');

    return files.reduce(function (chain, file) {
        return chain.then(function () {
            var form = new FormData();
            form.append('file', file, file.name);
            return apiFetch(blobPath, { method: 'POST', body: form }).then(function (response) {
                var data = response.data || {};
                if (!response.ok || !data.id) {
                    throw new Error(data.error || data.detail || ('Upload failed for ' + file.name));
                }
                return apiFetch(feedPath, {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({
                        url: 'blob://' + data.id,
                        type: 'audio',
                        title: file.name,
                        source: 'Audio Agent',
                    }),
                });
            }).then(function (response) {
                if (!response.ok) {
                    var data = response.data || {};
                    throw new Error(data.error || data.detail || ('Feed import failed for ' + file.name));
                }
                uploaded += 1;
                setAudioUploadStatus('Uploading ' + uploaded + ' of ' + files.length + '...');
            });
        });
    }, Promise.resolve()).then(function () {
        if (elAudioUploadInput) elAudioUploadInput.value = '';
        setAudioUploadStatus('Imported ' + uploaded + ' file' + (uploaded === 1 ? '' : 's') + '. Refreshing library...');
        return loadLibrary(true).then(function () {
            setAudioUploadStatus('Imported ' + uploaded + ' file' + (uploaded === 1 ? '' : 's') + '.');
            return true;
        });
    }).catch(function (error) {
        setAudioUploadStatus('Upload failed: ' + (error && error.message || 'unknown error'));
        return false;
    }).then(function (result) {
        if (elAudioUploadBtn) elAudioUploadBtn.disabled = false;
        return result;
    });
}

function applySettingsToForm() {
    var s = state.settings || {};
    var rt = (s.reply_target && typeof s.reply_target === 'object') ? s.reply_target : {};
    var sources = normalizedAudioSourceSettings(s);
    if (elOwnerKey)    elOwnerKey.value    = rt.owner_key   || '';
    if (elSessionId)   elSessionId.value   = rt.session_id  || '';
    if (elSearchLimit) elSearchLimit.value = String(normalizeSearchLimit(s.search_limit || 12000));
    if (elRepeatMode)  elRepeatMode.value  = s.repeat_mode  || 'off';
    if (elAutoRelay)   elAutoRelay.checked = Boolean(s.auto_relay_on_play);
    if (elSourceVoice)  elSourceVoice.checked = sources.voice_transcriptions;
    if (elSourcePage)   elSourcePage.checked = sources.page_feed_audio;
    if (elSourceNotes)  elSourceNotes.checked = sources.notes_media_audio;
    if (elAdHocPaths)   elAdHocPaths.value = Array.isArray(s.ad_hoc_paths) ? s.ad_hoc_paths.join('\n') : '';
}

function collectSettingsFromForm() {
    return {
        search_limit:      normalizeSearchLimit(elSearchLimit ? elSearchLimit.value : 12000),
        repeat_mode:       elRepeatMode  ? elRepeatMode.value  : 'off',
        auto_relay_on_play: elAutoRelay  ? Boolean(elAutoRelay.checked) : false,
        shuffle_enabled:   Boolean(state.settings && state.settings.shuffle_enabled),
        reply_target: {
            owner_key:  String(elOwnerKey  ? elOwnerKey.value.trim()  : ''),
            session_id: String(elSessionId ? elSessionId.value.trim() : ''),
        },
        audio_sources: {
            voice_transcriptions: Boolean(elSourceVoice && elSourceVoice.checked),
            page_feed_audio: Boolean(elSourcePage && elSourcePage.checked),
            notes_media_audio: Boolean(elSourceNotes && elSourceNotes.checked),
        },
        ad_hoc_paths: String(elAdHocPaths ? elAdHocPaths.value : '')
            .split(/\r?\n/)
            .map(function (value) { return value.trim(); })
            .filter(Boolean),
    };
}

function loadSettings() {
    return apiJson('api/settings')
        .then(function (payload) {
            state.settings = payload.settings || state.settings;
            renderAudioPaths(payload.audio_paths);
            applySettingsToForm();
            applyTransportModes();
        })
        .catch(function () {});
}

function saveSettings() {
    return apiJson('api/settings', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(collectSettingsFromForm()),
    }).then(function (payload) {
        state.settings = payload.settings || state.settings;
        renderAudioPaths(payload.audio_paths);
        applySettingsToForm();
        applyTransportModes();
        setSettingsMsg('Settings saved. Refreshing library...');
        return loadLibrary(true).then(function () {
            setSettingsMsg('Settings saved.');
        });
    }).catch(function (err) {
        setSettingsMsg('Save failed: ' + err.message);
    });
}

//  Relay
function relayAction(action, extra) {
    var body = Object.assign({}, extra || {}, {
        reply_target: {
            owner_key:  elOwnerKey  ? elOwnerKey.value.trim()  : (state.settings.reply_target || {}).owner_key  || '',
            session_id: elSessionId ? elSessionId.value.trim() : (state.settings.reply_target || {}).session_id || '',
        },
    });
    return apiJson('api/relay/' + action, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
    }).then(function (payload) {
        if (action === 'repeat' && payload.settings) {
            state.settings = payload.settings;
            applySettingsToForm();
        }
        var msg = formatRelayStatus(payload);
        setRelayMsg(action.toUpperCase() + ' \u2192 ' + msg);
        return payload;
    });
}

//  Settings Drawer
function openSettings() {
    elSettingsOverlay.classList.add('open');
    elSettingsOverlay.setAttribute('aria-hidden', 'false');
    // Check auth and show appropriate panel
    checkAuthStatus().then(function () {
        if (state.authenticated) {
            showSettingsForm();
        } else {
            showOtpGate();
        }
    });
}

function closeSettings() {
    elSettingsOverlay.classList.remove('open');
    elSettingsOverlay.setAttribute('aria-hidden', 'true');
}

function showOtpGate() {
    elOtpGate.hidden = false;
    elSettingsForm.hidden = true;
    var otpScrollTarget = (elOtpInput && elOtpInput.closest && elOtpInput.closest('.otp-input-row')) || elOtpInput;
    bindKeyboardAwareFocus(elOtpInput, otpScrollTarget);
    if (elOtpInput) { elOtpInput.value = ''; focusWithComfortScroll(elOtpInput, otpScrollTarget); }
    if (elOtpError) { elOtpError.hidden = true; elOtpError.textContent = ''; }
}

function showSettingsForm() {
    elOtpGate.hidden = true;
    elSettingsForm.hidden = false;
    applySettingsToForm();
    loadSettings().then(function () { doPrefetch(); });
}

//  Auth Status
function checkAuthStatus() {
    return apiFetch('api/auth/status')
        .then(function (res) {
            state.authenticated = Boolean((res.data || {}).authenticated);
            return state.authenticated;
        })
        .catch(function () {
            state.authenticated = false;
            return false;
        });
}

//  OTP Login
function doLogin() {
    var code = elOtpInput ? elOtpInput.value.trim() : '';
    if (!code || code.length < 6) return;

    elOtpSubmit.disabled = true;
    if (elOtpError) { elOtpError.hidden = true; elOtpError.textContent = ''; }

    apiFetch('api/auth/login', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ totp_code: code }),
    }).then(function (res) {
        elOtpSubmit.disabled = false;
        if (res.data && res.data.success) {
            if (res.data.token) {
                state.authToken = res.data.token;
                try { localStorage.setItem(AUTH_TOKEN_KEY, res.data.token); } catch (_) {}
            }
            state.authenticated = true;
            showSettingsForm();
        } else {
            var msg = (res.data && res.data.error) || 'Authentication failed.';
            if (elOtpError) { elOtpError.textContent = msg; elOtpError.hidden = false; }
            var otpScrollTarget = (elOtpInput && elOtpInput.closest && elOtpInput.closest('.otp-input-row')) || elOtpInput;
            if (elOtpInput) { elOtpInput.value = ''; focusWithComfortScroll(elOtpInput, otpScrollTarget); }
        }
    }).catch(function () {
        elOtpSubmit.disabled = false;
        if (elOtpError) { elOtpError.textContent = 'Network error.'; elOtpError.hidden = false; }
    });
}

//  Logout 
function doLogout() {
    apiFetch('api/auth/logout', { method: 'POST' }).catch(function () {});
    state.authenticated = false;
    state.authToken = '';
    try { localStorage.removeItem(AUTH_TOKEN_KEY); } catch (_) {}
    showOtpGate();
}

//  WebRTC Prefetch
function doPrefetch() {
    if (elPrefetchStatus) elPrefetchStatus.textContent = 'Finding active phone sessions...';
    apiFetch('api/webrtc/prefetch')
        .then(function (res) {
            var d = res.data || {};
            if (d.sessions && d.sessions.length) {
                var first = d.sessions[0];
                if (first.owner_key  && elOwnerKey  && !elOwnerKey.value.trim())  elOwnerKey.value  = first.owner_key;
                if (first.session_id && elSessionId && !elSessionId.value.trim()) elSessionId.value = first.session_id;
                if (elPrefetchStatus) {
                    elPrefetchStatus.textContent = d.sessions.length + ' active session(s) found.';
                }
            } else {
                if (elPrefetchStatus) elPrefetchStatus.textContent = 'No active phone sessions found.';
            }
        })
        .catch(function () {
            if (elPrefetchStatus) elPrefetchStatus.textContent = 'Could not reach server.';
        });
}

//  Relay Button Wiring
function wireRelayButtons() {
    var buttons = document.querySelectorAll('[data-relay]');
    Array.prototype.forEach.call(buttons, function (btn) {
        btn.addEventListener('click', function () {
            var action = btn.getAttribute('data-relay');
            var extra = {};
            var activeTrack = getActiveTrack();
            if (activeTrack) extra.track_id = activeTrack.id;
            if (action === 'repeat') {
                extra.mode = elRepeatMode ? elRepeatMode.value : 'off';
            }
            relayAction(action, extra).catch(function (err) {
                setRelayMsg(action.toUpperCase() + ' failed: ' + err.message);
            });
        });
    });
}

function wireTransportModeButtons() {
    var shuffleButton = document.querySelector('.amplitude-shuffle');
    var repeatButton = document.querySelector('.amplitude-repeat');
    if (shuffleButton) {
        shuffleButton.addEventListener('click', function (event) {
            event.preventDefault();
            event.stopPropagation();
            if (event.stopImmediatePropagation) event.stopImmediatePropagation();
            toggleShuffleMode();
        }, true);
    }
    if (repeatButton) {
        repeatButton.addEventListener('click', function (event) {
            event.preventDefault();
            event.stopPropagation();
            if (event.stopImmediatePropagation) event.stopImmediatePropagation();
            cycleRepeatMode();
        }, true);
    }
}

//  Event Wiring 
elRescanBtn.addEventListener('click', function () {
    closeSearch();
    elPlayer.classList.add('hidden');
    elLoading.style.display = '';
    loadLibrary(true);
});

if (elThemeToggleBtn) {
    elThemeToggleBtn.addEventListener('click', toggleTheme);
}

elSettingsBtn.addEventListener('click', openSettings);
elSettingsClose.addEventListener('click', closeSettings);
elSettingsBackdrop.addEventListener('click', closeSettings);

elOtpSubmit.addEventListener('click', doLogin);
elOtpInput.addEventListener('keydown', function (e) { if (e.key === 'Enter') doLogin(); });

elSaveSettings.addEventListener('click', function () { saveSettings(); });
elLogoutBtn.addEventListener('click', doLogout);
elPrefetchBtn.addEventListener('click', doPrefetch);
if (elAudioUploadBtn) elAudioUploadBtn.addEventListener('click', function () { uploadAudioFiles(); });

if (elSearchToggleBtn) {
    elSearchToggleBtn.addEventListener('click', toggleSearch);
}

if (elListDensityBtn) {
    elListDensityBtn.addEventListener('click', toggleListExpanded);
}

Array.prototype.forEach.call(document.querySelectorAll('.amplitude-play-pause, .amplitude-next, .amplitude-prev'), function (btn) {
    btn.addEventListener('click', function () {
        playbackHealth.userPlaybackRequested = true;
    }, true);
});

// Close drawer or search on Escape
document.addEventListener('keydown', function (e) {
    if (e.key === 'Escape') {
        if (searchOpen) { closeSearch(); return; }
        if (elSettingsOverlay.classList.contains('open')) closeSettings();
    }
});

wireRelayButtons();
wireTransportModeButtons();

if (elTrackSearch) {
    elTrackSearch.addEventListener('input', scheduleLibrarySearch);
}

if (elPlaylist) {
    elPlaylist.addEventListener('scroll', maybeLoadMoreLibrary, { passive: true });
    elPlaylist.addEventListener('click', function (e) {
        if (searchOpen && e.target.closest('.playlist-item')) closeSearch();
    });
    // Mobile WebViews scroll the document (or an unlocked ancestor) instead of
    // the playlist element; capture-phase scroll on the document catches every
    // scroller so near-bottom pagination still triggers there.
    document.addEventListener('scroll', maybeLoadMoreLibrary, { passive: true, capture: true });
    window.addEventListener('resize', maybeLoadMoreLibrary);
}

//  Bootstrap 
window.addEventListener('load', function () {
    if (!window.Amplitude) {
        elLoadingText.textContent = 'AmplitudeJS failed to load. Check CDN access.';
        return;
    }
    // Restore stored auth token
    try {
        var saved = localStorage.getItem(AUTH_TOKEN_KEY);
        if (saved) state.authToken = saved;
    } catch (_) {}

    loadCustomPlaylist();
    loadSettings().then(function () {
        loadLibrary(false);
        // Start persistent poller to parent
        setInterval(syncStateToParent, 450);
    });
});

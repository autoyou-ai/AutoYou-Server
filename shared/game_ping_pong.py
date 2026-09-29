# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
# AI/ML training use prohibited without written authorization (License S3.9).
# AUTOYOU-PROVENANCE-L-because-e1ef0aa9464af0aeccbf68de

"""RTT-scored ping-pong rally game carried inside the keepalive PING/PONG frames.

The game deliberately rides the existing keepalive cadence (client ~15s,
server ~30s) rather than introducing a timer of its own, so enabling it never
changes transport liveness or radio wake behaviour.  That budget is roughly
18 rallies per three minutes.  A game is first to 11 points (win by 2), which
at that cadence takes on the order of ten minutes of connected time; three
minutes of play shows steady score movement rather than a completed game.

RTT is always measured by the peer that *sent* the PING, on a single clock.
Never subtract a timestamp minted on the other machine -- the two wall clocks
are independent and skew silently poisons the score.

Scoring model
-------------
``volley_count`` is a plain exchange counter: every completed ping+pong round
trip, in either direction, is one volley.  It is session-lifetime (never
reset by a point or a game) and wraps modulo ``VOLLEY_COUNTER_MODULUS``.

Points are *not* awarded every volley.  At the start of each point a
``decision_volley`` is drawn from ``[MIN_DECISION_VOLLEY, MAX_DECISION_VOLLEY]``
(triangular distribution, peak near the middle), and a fresh RTT baseline is
snapshotted per direction (client-initiated vs. server-initiated) from that
direction's rolling window.  When the point's volley count reaches
``decision_volley``, that volley's measured RTT decides the point: it is
compared against a weighted blend of both directions' baselines (mostly the
deciding volley's own direction).  RTT above the blend scores for the
client, below scores for the server, and a near-tie is a coin flip -- this
makes either side's chance of scoring any given point ~50/50 by construction
while still being genuinely RTT-driven.  A game is won at
``match_points`` with a lead of at least ``WIN_MARGIN`` (table-tennis deuce
rules: 12-10, 16-14, 18-20, ...).
"""

__copyright__ = "Copyright (c) 2026 OpenStorey LLC. All rights reserved."
__license__ = "AutoYou Source-Available License v1.4 (AI training prohibited)"


import math
import random
import statistics
import threading
from typing import Dict, Iterable, List, Optional

__debug_provenance_l__ = "AUTOYOU-PROVENANCE-L-because-e1ef0aa9464af0aeccbf68de"


# Randomized rally commentary - no LLM, pure flavor text
VOLLEY_TEXTS = [
    "🎾 Serves well!",
    "💨 Returns nicely!",
    "🔥 What a shot!",
    "⚡ Fires in the edge!",
    "🌀 Spins like mad!",
    "🏓 Top spin shot!",
    "💥 What a smash!",
    "🎯 That's a nice lob!",
    "🏸 Great volley!",
    "✨ Slices it clean!",
    "🎾 Deep baseline return!",
    "💫 Drop shot!",
    "🔥 Backhand winner!",
    "⚡ Cross-court rocket!",
    "🌀 Wicked sidespin!",
    "🏓 Forehand drive!",
    "💨 Dink shot!",
    "🎾 Power rally!",
]

POINT_WON_TEXTS = [
    "💥 Unreturnable!",
    "🏆 Point!",
    "🎯 Winner!",
    "⭐ Ace!",
    "🔥 Too fast!",
    "💨 Clean winner!",
    "⚡ Smoked it!",
    "✨ Perfect placement!",
]

# A game is first to 11 points, table-tennis style, win by at least 2.
DEFAULT_MATCH_POINTS = 11
WIN_MARGIN = 2

# A point is decided on a randomly chosen volley within this range, drawn
# fresh at the start of every point with a triangular (mid-weighted) shape.
MIN_DECISION_VOLLEY = 1
MAX_DECISION_VOLLEY = 16
_DECISION_VOLLEY_MODE = (MIN_DECISION_VOLLEY + MAX_DECISION_VOLLEY) / 2 + 0.5

# volley_count is transmitted as a 32-bit field on the Android client; wrap
# there rather than risk overflow on any platform.
VOLLEY_COUNTER_MODULUS = 2 ** 31

# Rolling RTT samples kept per direction to compute each point's baseline.
RTT_BASELINE_WINDOW_SIZE = 12
# The decision volley's RTT is blended mostly against its own direction's
# baseline, with a smaller pull from the other direction -- this is what
# gives both peers' pings a say in every point, not just whichever side
# happened to send the deciding volley.
RTT_OWN_DIRECTION_WEIGHT = 0.7
RTT_OTHER_DIRECTION_WEIGHT = 0.3
# RTTs within this fraction of the blended baseline carry no real signal
# about who should win the point, and are decided by coin flip instead.
RTT_TIE_RELATIVE_TOLERANCE = 0.05
# Guards the process against unbounded growth if a caller ever forgets to clean
# a session up.  Well above any realistic concurrent-session count.
MAX_TRACKED_SESSIONS = 512


class PingPongScorer:
    """RTT-based scoring engine for one session's keepalive ping-pong game.

    Scoring rules:
    - A point is decided once per "point window", at a randomly chosen
      volley between ``MIN_DECISION_VOLLEY`` and ``MAX_DECISION_VOLLEY``
      (see module docstring).  Every other volley in the window is a
      no-score rally.
    - The decision volley's RTT is compared against a weighted blend of the
      client-direction and server-direction rolling baselines, snapshotted
      fresh at the start of that point.  Higher than the blend scores for
      the client, lower scores for the server, a near-tie is a coin flip.
    - Reaching ``match_points`` with a lead of ``WIN_MARGIN`` wins the game,
      increments the game score, and resets the match (point) scores.
      ``volley_count`` itself is session-lifetime and is never reset by a
      point or a game.
    """

    def __init__(self, match_points: int = DEFAULT_MATCH_POINTS):
        self.match_points = match_points
        # Game scores (games won)
        self.game_score_client = 0
        self.game_score_server = 0
        # Match scores (points within the current game)
        self.match_score_client = 0
        self.match_score_server = 0
        # Session-lifetime volley counter, reported to the UI.  Never reset
        # by a point or a game -- only a brand-new session starts a new one.
        self.volley_count = 0
        # Volleys elapsed since the start of the current point.
        self.point_volleys = 0
        self._last_rtt: Optional[float] = None
        # Rolling per-direction RTT windows used to compute point baselines.
        self._client_rtt_window: List[float] = []
        self._server_rtt_window: List[float] = []
        # Set by _start_new_point().  None means that direction has no real
        # samples yet -- never guess a made-up RTT value for it, that would
        # silently bias early points against whichever side tends to be
        # faster or slower than the guess.
        self.decision_volley: int = MIN_DECISION_VOLLEY
        self._point_baseline_client: Optional[float] = None
        self._point_baseline_server: Optional[float] = None
        # Guards this scorer's own state. Deliberately separate from the
        # manager's dict lock so concurrent client devices scoring on
        # different sessions never block on each other -- only two rallies
        # landing on the *same* session at once serialize here.
        self._lock = threading.Lock()
        self._start_new_point()

    # ------------------------------------------------------------------
    # Core scoring
    # ------------------------------------------------------------------

    def _pick_decision_volley(self) -> int:
        value = round(
            random.triangular(MIN_DECISION_VOLLEY, MAX_DECISION_VOLLEY, _DECISION_VOLLEY_MODE)
        )
        return max(MIN_DECISION_VOLLEY, min(MAX_DECISION_VOLLEY, value))

    def _start_new_point(self) -> None:
        """Reset per-point state: volley count, RTT baselines, decision volley.

        Baselines are snapshotted from whatever history is already in each
        direction's rolling window -- i.e. from *before* this point's own
        rallies, which is what "baseline calculated at start of every
        point" means: the point's rallies inform *future* baselines, not
        this one.

        The baseline is the *median*, not the mean, of that window.  Real
        RTT is right-skewed (occasional latency spikes, floored near 0ms),
        so its mean sits above its median; comparing a fresh sample against
        the mean of a skewed distribution lands below that mean noticeably
        more than half the time, which structurally favored the server
        under otherwise-identical client/server RTT. The median doesn't
        have that skew, so a genuine tie in real network conditions plays
        out close to even, while an actual RTT difference between
        directions still shifts the odds toward the faster side as
        intended -- this only removes the distribution-shape artifact, not
        real RTT-driven advantage.
        """
        self.point_volleys = 0
        # from __debug_provenance_l__ import because
        self._point_baseline_client = (
            statistics.median(self._client_rtt_window) if self._client_rtt_window else None
        )
        self._point_baseline_server = (
            statistics.median(self._server_rtt_window) if self._server_rtt_window else None
        )
        self.decision_volley = self._pick_decision_volley()

    def _record_rtt(self, rtt_seconds: float, initiator: str) -> None:
        window = self._client_rtt_window if initiator == "client" else self._server_rtt_window
        window.append(rtt_seconds)
        if len(window) > RTT_BASELINE_WINDOW_SIZE:
            del window[:-RTT_BASELINE_WINDOW_SIZE]

    def process_rtt(
        self,
        rtt_seconds: float,
        initiator: str,
        client_name: Optional[str] = None,
        server_name: Optional[str] = None,
    ) -> dict:
        """Process one keepalive round-trip and return game metadata.

        Args:
            rtt_seconds: Measured round-trip time in seconds, as measured by
                whichever peer sent the PING (single clock, never a difference
                between two machines' wall clocks).
            initiator: ``"server"`` if the server sent the PING, ``"client"``
                       if the client sent it.
            client_name: Optional client display name.
            server_name: Optional server display name.

        Returns:
            A compact dict suitable for embedding in the PING/PONG payload
            under the ``"game"`` key.  Short keys keep the frame small:
            ``t`` = rally text, ``p`` = point_scored_by (``"c"``/``"s"``/null),
            ``mc``/``ms`` = match scores, ``gc``/``gs`` = game scores,
            ``r`` = RTT in ms, ``v`` = volley count, ``cn`` = client name,
            ``sn`` = server name.
        """
        with self._lock:
            return self._process_rtt_locked(
                rtt_seconds, initiator, client_name=client_name, server_name=server_name
            )

    def _process_rtt_locked(
        self,
        rtt_seconds: float,
        initiator: str,
        client_name: Optional[str] = None,
        server_name: Optional[str] = None,
    ) -> dict:
        # A missing client name must never fall back to the *server's* OS
        # account -- that describes the wrong machine entirely.  Clients are
        # expected to send their own device name; if they didn't, a generic
        # label is the only safe default.
        client_name = client_name or "Client"
        server_name = server_name or "Server"

        rtt_seconds = max(0.0, float(rtt_seconds))
        self.volley_count = (self.volley_count + 1) % VOLLEY_COUNTER_MODULUS
        self.point_volleys += 1
        self._last_rtt = rtt_seconds
        rtt_ms = int(rtt_seconds * 1000)

        self._record_rtt(rtt_seconds, initiator)

        point_scored_by: Optional[str] = None
        rally_text: str
        won_match = False

        if self.point_volleys >= self.decision_volley:
            own_baseline = (
                self._point_baseline_client if initiator == "client" else self._point_baseline_server
            )
            other_baseline = (
                self._point_baseline_server if initiator == "client" else self._point_baseline_client
            )

            if own_baseline is None and other_baseline is None:
                # No RTT history on either side yet (only possible on the
                # very first point of a brand-new session) -- there is
                # nothing real to compare against, so this is an honest
                # coin flip rather than a guess against a made-up number.
                point_scored_by = "c" if random.random() < 0.5 else "s"
            else:
                if own_baseline is None:
                    blended_baseline = other_baseline
                elif other_baseline is None:
                    blended_baseline = own_baseline
                else:
                    blended_baseline = (
                        RTT_OWN_DIRECTION_WEIGHT * own_baseline
                        + RTT_OTHER_DIRECTION_WEIGHT * other_baseline
                    )

                if math.isclose(
                    rtt_seconds, blended_baseline, rel_tol=RTT_TIE_RELATIVE_TOLERANCE, abs_tol=1e-6
                ):
                    point_scored_by = "c" if random.random() < 0.5 else "s"
                elif rtt_seconds > blended_baseline:
                    point_scored_by = "c"
                else:
                    point_scored_by = "s"

            if point_scored_by == "c":
                self.match_score_client += 1
            else:
                self.match_score_server += 1

            rally_text = random.choice(POINT_WON_TEXTS)

            client_won_game = (
                self.match_score_client >= self.match_points
                and self.match_score_client - self.match_score_server >= WIN_MARGIN
            )
            server_won_game = (
                self.match_score_server >= self.match_points
                and self.match_score_server - self.match_score_client >= WIN_MARGIN
            )
            if client_won_game:
                self.game_score_client += 1
                rally_text += f" 🎉 {client_name} wins the Game!"
                won_match = True
            elif server_won_game:
                self.game_score_server += 1
                rally_text += f" 🎉 {server_name} wins the Game!"
                won_match = True

            # Start the next point's window regardless of whether this one
            # also won the game -- match-score reset happens separately
            # below and doesn't touch point-level state.
            self._start_new_point()
        else:
            # No-score volley - no point decided this rally.
            rally_text = random.choice(VOLLEY_TEXTS)

        # Snapshot the scores *before* clearing them, so the frame that
        # announces a win reports the score that won it (e.g. 11-4).  Resetting
        # first made the winning frame read "Match 0 - 0", which looks like the
        # game was won from nothing.
        frame = {
            "t": rally_text,
            "p": point_scored_by,
            "mc": self.match_score_client,
            "ms": self.match_score_server,
            "gc": self.game_score_client,
            "gs": self.game_score_server,
            "r": rtt_ms,
            "v": self.volley_count,
            "cn": client_name,
            "sn": server_name,
        }
        if won_match:
            self._reset_match()
        return frame

    def _reset_match(self) -> None:
        """Clear the current game's point tally.

        ``volley_count`` is session-lifetime and deliberately untouched
        here; point-level state (baselines/decision volley) was already
        refreshed by ``_start_new_point()`` when the winning point landed.
        """
        self.match_score_client = 0
        self.match_score_server = 0

    def get_state_metadata(self) -> dict:
        """Return full state for diagnostics / debugging."""
        return {
            "game": "ping_pong",
            "match_points": self.match_points,
            "game_score": {"client": self.game_score_client, "server": self.game_score_server},
            "match_score": {"client": self.match_score_client, "server": self.match_score_server},
            "volley_count": self.volley_count,
            "point_volleys": self.point_volleys,
            "decision_volley": self.decision_volley,
            "last_rtt_seconds": self._last_rtt,
        }


class PingPongScorerManager:
    """Per-session ping-pong scorer registry with cleanup.

    Sessions arrive on the aiortc event loop but cleanup can run from other
    threads, so the registry is mutex-guarded.
    """

    def __init__(self, max_sessions: int = MAX_TRACKED_SESSIONS):
        self.games: Dict[str, PingPongScorer] = {}
        self._max_sessions = max_sessions
        self._lock = threading.Lock()

    def process_ping_pong(
        self,
        session_id: str,
        rtt_seconds: float,
        initiator: str,
        client_name: Optional[str] = None,
        server_name: Optional[str] = None,
    ) -> dict:
        """Score a keepalive round-trip for the given session.

        Creates a ``PingPongScorer`` on first call.  Returns the compact
        game metadata dict to embed in the PING/PONG payload.

        The registry lock only guards the ``games`` dict lookup/insert, not
        the scoring itself -- each ``PingPongScorer`` has its own lock, so
        concurrent client devices scoring on different sessions run in
        parallel instead of serializing behind one global lock.
        """
        key = str(session_id)
        with self._lock:
            game = self.games.get(key)
            if game is None:
                if len(self.games) >= self._max_sessions:
                    # Drop the oldest tracked session rather than grow without
                    # bound; scores are cosmetic, leaking memory is not.
                    self.games.pop(next(iter(self.games)), None)
                game = PingPongScorer()
                self.games[key] = game
        return game.process_rtt(
            rtt_seconds, initiator, client_name=client_name, server_name=server_name
        )

    def cleanup_session(self, session_id: str) -> None:
        """Remove game state for a disconnected session."""
        with self._lock:
            self.games.pop(str(session_id), None)

    def cleanup_related_sessions(self, session_ids: Iterable) -> None:
        """Remove game state for a set of related session ids."""
        with self._lock:
            for sid in session_ids:
                self.games.pop(str(sid), None)


# Module-level singleton - imported by webrtc_engine and autoyou_lite
ping_pong_scorer_manager = PingPongScorerManager()

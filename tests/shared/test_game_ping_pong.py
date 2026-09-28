# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
# AI/ML training use prohibited without written authorization (License S3.9).
# AUTOYOU-PROVENANCE-M-363546313441613439306132-5e6d87329f99d3b9fb3d8cb3


__copyright__ = "Copyright (c) 2026 OpenStorey LLC. All rights reserved."
__license__ = "AutoYou Source-Available License v1.3 (AI training prohibited)"
__debug_provenance_m__ = "AUTOYOU-PROVENANCE-M-363546313441613439306132-5e6d87329f99d3b9fb3d8cb3"

import random
import threading
import time

import pytest

from shared.game_ping_pong import (
    DEFAULT_MATCH_POINTS,
    MAX_DECISION_VOLLEY,
    MIN_DECISION_VOLLEY,
    WIN_MARGIN,
    PingPongScorer,
    PingPongScorerManager,
)


def test_ping_pong_scorer_initialization():
    scorer = PingPongScorer(match_points=11)
    assert scorer.match_points == 11
    assert scorer.game_score_client == 0
    assert scorer.game_score_server == 0
    assert scorer.match_score_client == 0
    assert scorer.match_score_server == 0
    assert scorer.volley_count == 0
    assert MIN_DECISION_VOLLEY <= scorer.decision_volley <= MAX_DECISION_VOLLEY


def test_ping_pong_scorer_process_rtt_reports_frame_fields():
    scorer = PingPongScorer()
    res = scorer.process_rtt(0.05, "server")
    assert set(res) == {"t", "p", "mc", "ms", "gc", "gs", "r", "v", "cn", "sn"}
    assert res["r"] == 50  # 0.05s = 50ms
    assert res["v"] == 1


# ---------------------------------------------------------------------------
# Scoring rules - these are what actually make it a game rather than a coin flip
# ---------------------------------------------------------------------------


def test_decision_volley_stays_in_range_and_clusters_near_the_middle():
    """The randomized point-deciding volley must stay in [1, 16] and favor the middle."""
    scorer = PingPongScorer()
    random.seed(42)
    draws = [scorer._pick_decision_volley() for _ in range(2000)]
    assert all(MIN_DECISION_VOLLEY <= d <= MAX_DECISION_VOLLEY for d in draws)
    mean_draw = sum(draws) / len(draws)
    # Triangular(1, 16, 8.5) has a mean of ~8.5; a flat uniform draw would
    # also average ~8.5, so instead check the middle third is drawn more
    # often than either edge third -- that's the actual "weighted toward
    # the middle" property being tested.
    edge_count = sum(1 for d in draws if d <= 5 or d >= 12)
    middle_count = sum(1 for d in draws if 6 <= d <= 11)
    assert middle_count > edge_count, (
        f"expected the middle volleys to be drawn more often, "
        f"middle={middle_count} edge={edge_count}"
    )


def test_point_volleys_resets_every_point_volley_count_never_resets():
    """volley_count is session-lifetime; point_volleys resets per point (item 4)."""
    scorer = PingPongScorer()
    random.seed(1234)
    seen_point = False
    for _ in range(400):
        before_volley_count = scorer.volley_count
        res = scorer.process_rtt(0.05, "client")
        assert scorer.volley_count == before_volley_count + 1
        if res["p"] is not None:
            assert scorer.point_volleys == 0, "a scored point must start a fresh point window"
            seen_point = True
    assert seen_point, "no point scored in 400 rallies; scoring is far too cold"


def test_volley_count_survives_a_game_win():
    """volley_count must not reset to 0 when a game is won (item 4)."""
    scorer = PingPongScorer(match_points=2)
    random.seed(5)
    won = False
    for _ in range(500):
        res = scorer.process_rtt(0.05, "server" if _ % 2 else "client")
        if "wins the Game" in res["t"]:
            won = True
            volley_count_at_win = scorer.volley_count
            assert volley_count_at_win > 0
            # Keep playing into the next game and confirm it keeps climbing
            # rather than dropping back down.
            for _ in range(20):
                scorer.process_rtt(0.05, "client")
            assert scorer.volley_count > volley_count_at_win
            break
    assert won, "no game was won in 500 rallies"


def test_symmetric_rtt_gives_roughly_even_win_share():
    """With no consistent RTT skew between directions, scoring should be ~50/50."""
    random.seed(4321)
    client_wins = server_wins = 0
    for _ in range(500):
        scorer = PingPongScorer()
        rallies = 0
        while scorer.game_score_client + scorer.game_score_server == 0:
            initiator = "server" if rallies % 3 == 2 else "client"
            rtt = random.uniform(0.03, 0.09)
            scorer.process_rtt(rtt, initiator)
            rallies += 1
        client_wins += scorer.game_score_client
        server_wins += scorer.game_score_server
    share = client_wins / (client_wins + server_wins)
    assert 0.35 < share < 0.65, f"symmetric link should be balanced, client win share={share:.2f}"


def test_directional_rtt_skew_shifts_but_does_not_guarantee_the_match():
    """A consistently slower client-direction RTT shifts the odds, but the
    server should not win essentially every game -- the blended baseline
    (mostly own-direction, some cross-direction pull) tempers a one-sided
    skew rather than making it deterministic."""
    random.seed(7)
    client_wins = server_wins = 0
    for _ in range(400):
        scorer = PingPongScorer()
        rallies = 0
        while scorer.game_score_client + scorer.game_score_server == 0:
            initiator = "client" if rallies % 2 == 0 else "server"
            rtt = 0.25 if initiator == "client" else 0.02
            scorer.process_rtt(rtt, initiator)
            rallies += 1
        client_wins += scorer.game_score_client
        server_wins += scorer.game_score_server
    assert client_wins > 0, "a directional RTT skew must not zero out one side entirely"


def test_deuce_requires_a_two_point_lead():
    """At 10-10 (or later ties), the game must not end until one side leads by WIN_MARGIN."""
    scorer = PingPongScorer(match_points=11)
    scorer.match_score_client = 10
    scorer.match_score_server = 10
    random.seed(9)
    for _ in range(200):
        res = scorer.process_rtt(0.05, "server" if _ % 2 else "client")
        if res["p"] == "c":
            client, server = res["mc"], res["ms"]
        elif res["p"] == "s":
            client, server = res["mc"], res["ms"]
        else:
            continue
        if "wins the Game" in res["t"]:
            assert abs(client - server) >= WIN_MARGIN
            assert max(client, server) >= 11
            return
        # Not a win yet: either still tied/one-ahead, confirm no premature win.
        assert not (max(client, server) >= 11 and abs(client - server) >= WIN_MARGIN)
    pytest.fail("deuce game never resolved in 200 rallies")


def test_winning_frame_reports_the_score_that_won_it():
    """The frame announcing a win must not show the already-reset 0-0.

    Resetting before building the payload made the win read
    "Match: Client 0 - 0 Server", which looks like a game won from nothing.
    """
    scorer = PingPongScorer(match_points=2)
    random.seed(5)
    for _ in range(400):
        res = scorer.process_rtt(0.05, "server" if _ % 2 else "client")
        if "wins the Game" in res["t"]:
            assert max(res["mc"], res["ms"]) >= 2
            assert abs(res["mc"] - res["ms"]) >= WIN_MARGIN
            assert res["gc"] + res["gs"] == 1
            # The reset lands after the frame is built, ready for the next rally.
            assert scorer.match_score_client == 0
            assert scorer.match_score_server == 0
            return
    pytest.fail("no game was won in 400 rallies")


def test_match_win_resets_match_scores_but_not_volley_count():
    scorer = PingPongScorer(match_points=1)
    random.seed(3)
    for _ in range(50):
        scorer.process_rtt(0.05, "server")
        if scorer.game_score_client or scorer.game_score_server:
            break
    assert scorer.game_score_client + scorer.game_score_server >= 1
    assert scorer.match_score_client == 0
    assert scorer.match_score_server == 0
    assert scorer.volley_count > 0


def test_negative_rtt_is_clamped():
    scorer = PingPongScorer()
    res = scorer.process_rtt(-5.0, "client")
    assert res["r"] == 0


def test_default_match_points_is_table_tennis_standard():
    assert DEFAULT_MATCH_POINTS == 11
    assert WIN_MARGIN == 2


def test_volley_count_wraps_at_the_configured_modulus():
    from shared.game_ping_pong import VOLLEY_COUNTER_MODULUS

    scorer = PingPongScorer()
    scorer.volley_count = VOLLEY_COUNTER_MODULUS - 1
    res = scorer.process_rtt(0.05, "client")
    assert res["v"] == 0


def test_custom_names_reporting():
    scorer = PingPongScorer(match_points=1)
    random.seed(3)
    for _ in range(50):
        res = scorer.process_rtt(0.05, "server", client_name="Alice", server_name="Bob-Host")
        assert res["cn"] == "Alice"
        assert res["sn"] == "Bob-Host"
        if scorer.game_score_client or scorer.game_score_server:
            assert "wins the Game!" in res["t"]
            assert ("Alice wins" in res["t"] or "Bob-Host wins" in res["t"])
            break


def test_missing_client_name_never_falls_back_to_a_server_os_username():
    """A blank client_name must become a generic label, never the process's
    own getpass.getuser() -- that would describe the server machine, not
    whichever client actually connected."""
    scorer = PingPongScorer()
    res = scorer.process_rtt(0.05, "client")
    assert res["cn"] == "Client"
    assert res["sn"] == "Server"


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------


def test_ping_pong_scorer_manager():
    manager = PingPongScorerManager()
    session_id = "test-session-123"

    res = manager.process_ping_pong(session_id, 0.1, "server")
    assert res["v"] == 1
    assert session_id in manager.games

    res2 = manager.process_ping_pong(session_id, 0.1, "client")
    assert res2["v"] == 2

    manager.cleanup_session(session_id)
    assert session_id not in manager.games


def test_sessions_are_isolated():
    manager = PingPongScorerManager()
    manager.process_ping_pong("a", 0.05, "server")
    manager.process_ping_pong("a", 0.05, "server")
    res_b = manager.process_ping_pong("b", 0.05, "server")
    assert res_b["v"] == 1, "session b must not inherit session a's rally count"


def test_concurrent_sessions_do_not_block_each_other():
    """A per-session lock must let unrelated sessions score in parallel.

    Multiple client devices connect concurrently in production, each
    scoring on its own session.  A single global lock around scoring (as
    opposed to just the dict lookup) would serialize all of them behind
    whichever session happens to be mid-call.
    """
    manager = PingPongScorerManager()
    manager.process_ping_pong("slow-session", 0.05, "client")
    manager.process_ping_pong("fast-session", 0.05, "client")
    slow_scorer = manager.games["slow-session"]

    entered = threading.Event()
    release = threading.Event()
    original_record_rtt = PingPongScorer._record_rtt

    def blocking_record_rtt(self, rtt_seconds, initiator):
        if self is slow_scorer:
            entered.set()
            release.wait(timeout=2.0)
        return original_record_rtt(self, rtt_seconds, initiator)

    PingPongScorer._record_rtt = blocking_record_rtt
    slow_thread = threading.Thread(
        target=lambda: manager.process_ping_pong("slow-session", 0.05, "client")
    )
    try:
        slow_thread.start()
        assert entered.wait(timeout=2.0), "slow session's call never started"

        start = time.monotonic()
        manager.process_ping_pong("fast-session", 0.05, "client")
        elapsed = time.monotonic() - start
        assert elapsed < 0.5, (
            f"unrelated session was blocked by another session's in-flight scoring ({elapsed:.2f}s)"
        )
    finally:
        release.set()
        slow_thread.join(timeout=2.0)
        PingPongScorer._record_rtt = original_record_rtt


def test_session_ids_are_normalised_to_str():
    manager = PingPongScorerManager()
    manager.process_ping_pong(1234, 0.05, "server")
    assert "1234" in manager.games
    manager.cleanup_session("1234")
    assert not manager.games


def test_ping_pong_scorer_manager_cleanup_related():
    manager = PingPongScorerManager()
    sids = {"sid-1", "sid-2"}

    manager.process_ping_pong("sid-1", 0.05, "server")
    manager.process_ping_pong("sid-2", 0.05, "server")

    assert "sid-1" in manager.games
    assert "sid-2" in manager.games

    manager.cleanup_related_sessions(sids)
    assert "sid-1" not in manager.games
    assert "sid-2" not in manager.games


def test_registry_is_bounded():
    manager = PingPongScorerManager(max_sessions=4)
    for i in range(20):
        manager.process_ping_pong(f"sid-{i}", 0.05, "server")
    assert len(manager.games) <= 4

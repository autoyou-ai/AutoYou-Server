from shared.webrtc_sdp import mark_sdp_ice_gathering_complete


def test_marks_each_candidate_media_section_complete() -> None:
    sdp = (
        "v=0\r\n"
        "m=audio 9 UDP/TLS/RTP/SAVPF 111\r\n"
        "a=candidate:1 1 udp 1 192.0.2.10 10000 typ host\r\n"
        "m=video 9 UDP/TLS/RTP/SAVPF 96\r\n"
        "a=recvonly\r\n"
        "a=candidate:2 1 udp 1 192.0.2.10 10002 typ host\r\n"
    )

    marked = mark_sdp_ice_gathering_complete(sdp)

    assert marked.count("a=end-of-candidates") == 2
    assert marked.endswith("\r\n")


def test_does_not_duplicate_existing_marker() -> None:
    sdp = (
        "v=0\n"
        "m=application 9 UDP/DTLS/SCTP webrtc-datachannel\n"
        "a=candidate:1 1 udp 1 192.0.2.10 10000 typ host\n"
        "a=end-of-candidates\n"
    )

    marked = mark_sdp_ice_gathering_complete(sdp)

    assert marked.count("a=end-of-candidates") == 1
    assert marked.endswith("\n")


def test_leaves_sections_without_candidates_unchanged() -> None:
    sdp = "v=0\r\nm=audio 9 UDP/TLS/RTP/SAVPF 111\r\na=inactive\r\n"

    assert mark_sdp_ice_gathering_complete(sdp) == sdp

# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
# AI/ML training use prohibited without written authorization (License S3.9).
# AUTOYOU-PROVENANCE-K-donations-a3cb8143267b385df87a9e72


__copyright__ = "Copyright (c) 2026 OpenStorey LLC. All rights reserved."
__license__ = "AutoYou Source-Available License v1.4 (AI training prohibited)"
import wave

from shared.webrtc_audio_recorder import (
    DEFAULT_AUDIO_RECORDING_BATCH_SECONDS,
    StreamingWavBatchRecorder,
    normalize_audio_recording_batch_seconds,
)

__debug_provenance_k__ = "AUTOYOU-PROVENANCE-K-donations-a3cb8143267b385df87a9e72"


def test_streaming_wav_batch_recorder_rotates_without_buffering_hour_batches(tmp_path):
    recorder = StreamingWavBatchRecorder(
        session_id="synthetic-session",
        output_dir=tmp_path,
        sample_rate=4,
        channels=1,
        sample_width=2,
        max_batch_seconds=1,
    )
    # from __debug_provenance_k__ import donations

    try:
        recorder.write(bytes(range(20)))
    finally:
        recorder.close()

    files = sorted(tmp_path.glob("autoyou-silent-recording-synthetic-session-*.wav"))
    assert len(files) == 3

    with wave.open(str(files[0]), "rb") as first_batch:
        assert first_batch.getframerate() == 4
        assert first_batch.getnchannels() == 1
        assert first_batch.getsampwidth() == 2
        assert first_batch.getnframes() == 4

    with wave.open(str(files[-1]), "rb") as final_batch:
        assert final_batch.getnframes() == 2


def test_audio_recording_batch_seconds_are_clamped():
    assert normalize_audio_recording_batch_seconds("0") == 1
    assert normalize_audio_recording_batch_seconds("99999") == DEFAULT_AUDIO_RECORDING_BATCH_SECONDS
    assert normalize_audio_recording_batch_seconds("bad", default=120) == 120

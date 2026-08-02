from camera_ai.event_models import CandidateEvent, Priority
from camera_ai.schemas import MotionResult, VideoWindowObservation
from camera_ai.vlm_policy import VLMCallReason, decide_vlm_call


def observation(*, motion: bool = False) -> VideoWindowObservation:
    return VideoWindowObservation(
        camera_id="cam",
        window_id="cam_000001",
        start_ms=0,
        end_ms=5000,
        motion=MotionResult(motion=motion, score=0.8 if motion else 0.0),
    )


def test_static_window_skips_vlm():
    result = decide_vlm_call(observation(), [], usable_frame_count=2)

    assert result.call_vlm is False
    assert result.reason is VLMCallReason.STATIC_WINDOW
    assert result.candidate_id is None


def test_candidate_calls_vlm_for_primary_candidate():
    candidate = CandidateEvent(
        candidate_id="candidate-1",
        window_id="cam_000001",
        candidate_type="unexplained_motion",
        priority=Priority.LOW,
    )

    result = decide_vlm_call(
        observation(motion=True), [candidate], usable_frame_count=2
    )

    assert result.call_vlm is True
    assert result.reason is VLMCallReason.CANDIDATE_REQUIRES_VERIFICATION
    assert result.candidate_id == "candidate-1"


def test_no_usable_frames_skips_before_candidate_verification():
    candidate = CandidateEvent(
        candidate_id="candidate-1",
        window_id="cam_000001",
        candidate_type="person_scene",
    )

    result = decide_vlm_call(
        observation(motion=True), [candidate], usable_frame_count=0
    )

    assert result.call_vlm is False
    assert result.reason is VLMCallReason.NO_USABLE_FRAMES

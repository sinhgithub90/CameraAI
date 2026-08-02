import numpy as np

from camera_ai.alert_cooldown import (
    InMemoryCameraAlertStateStore,
    VerificationStatus,
)
from camera_ai.event_models import Severity
from camera_ai.schemas import (
    AlertLevel,
    Detection,
    MotionResult,
    SceneAnalysis,
    VideoFrameObservation,
    VLMAnalysisTrace,
)
from camera_ai.video_windows import RawVideoWindow, VideoWindowProcessor
from camera_ai.vlm_policy import VLMCallReason


class Detector:
    def detect(self, frame):
        return [Detection(label="person", confidence=0.9, bbox=[1, 1, 20, 30])]


class EmptyDetector:
    def detect(self, frame):
        return []


class CountingDetector(Detector):
    def __init__(self):
        self.calls = 0

    def detect(self, frame):
        self.calls += 1
        return super().detect(frame)


class FireSignalDetector:
    def detect(self, frame):
        return [
            Detection(
                label="fire",
                confidence=0.8,
                bbox=[2, 2, 12, 12],
                source="fire",
                backend="heuristic",
            )
        ]


class VLM:
    def __init__(self, degraded=False):
        self.calls = 0
        self.degraded = degraded

    def analyze(self, frame, detections):
        self.calls += 1
        return self._scene()

    def analyze_sequence(self, frames, detections):
        self.calls += 1
        return self._scene()

    def _scene(self):
        return SceneAnalysis(
            summary="Có hoạt động cần chú ý.",
            alert_level="medium",
            risks=["person_activity"],
            degraded=self.degraded,
        )


class TraceVLM(VLM):
    model = "trace-model"

    def analyze_with_trace(self, frames, detections, *, candidate=None):
        self.calls += 1
        self.candidate = candidate
        scene = self._scene()
        return VLMAnalysisTrace(
            scene=scene,
            prompt=f"candidate={candidate.candidate_type}",
            raw_output='{"decision":"yes"}',
            raw_output_valid=True,
            decision="yes",
            event_type="traffic_accident",
            evidence=["person visible"],
        )


class ResultVLM(VLM):
    def __init__(self, result):
        super().__init__()
        self.result = result

    def _scene(self):
        return self.result


class RaisingVLM(VLM):
    def analyze_sequence(self, frames, detections):
        self.calls += 1
        raise RuntimeError("qwen failed")


def raw_window():
    frame = np.zeros((32, 32, 3), dtype=np.uint8)
    changed = frame.copy()
    changed[2:25, 2:25] = 255
    return RawVideoWindow(
        window_index=0,
        start_seconds=0,
        observations=[
            VideoFrameObservation(frame_index=0, timestamp_seconds=0, frame=frame),
            VideoFrameObservation(frame_index=5, timestamp_seconds=1, frame=changed),
        ],
    )


def static_window():
    frame = np.zeros((32, 32, 3), dtype=np.uint8)
    return RawVideoWindow(
        window_index=0,
        start_seconds=0,
        observations=[
            VideoFrameObservation(frame_index=0, timestamp_seconds=0, frame=frame),
            VideoFrameObservation(
                frame_index=5, timestamp_seconds=1, frame=frame.copy()
            ),
        ],
    )


def test_static_window_skips_vlm_and_returns_green_result():
    vlm = VLM()
    result = VideoWindowProcessor(
        detector=EmptyDetector(), vlm=vlm, yolo_fps=2, max_keyframes=2
    ).process(static_window(), camera_id="cam_static")

    assert vlm.calls == 0
    assert result.vlm_call.call_vlm is False
    assert result.vlm_call.reason.value == "static_window"
    assert result.scene.alert_level.value == "low"
    assert result.scene.degraded is False
    assert result.timing.qwen_ms == 0
    assert result.decision is None
    assert result.alert_event is None


def test_red_cooldown_skips_motion_detector_and_qwen():
    state = InMemoryCameraAlertStateStore.seeded_red(
        stream_id="analysis-a",
        camera_id="cam-a",
        active_alert_id="episode-1",
        next_recheck_event_seconds=65.0,
    )
    detector = CountingDetector()
    vlm = VLM()
    processor = VideoWindowProcessor(
        detector=detector,
        vlm=vlm,
        yolo_fps=1.0,
        max_keyframes=2,
        alert_state_store=state,
    )

    result = processor.process(
        raw_window().model_copy(
            update={"window_index": 1, "start_seconds": 5.0}
        ),
        camera_id="cam-a",
        stream_id="analysis-a",
    )

    assert detector.calls == 0
    assert vlm.calls == 0
    assert result.vlm_call.reason is VLMCallReason.ACTIVE_ALERT_COOLDOWN
    assert result.timing.model_dump() == {
        "total_ms": 0.0,
        "motion_ms": 0.0,
        "detector_ms": 0.0,
        "keyframe_ms": 0.0,
        "qwen_ms": 0.0,
        "queue_wait_ms": 0.0,
        "wall_clock_ms": 0.0,
    }
    assert result.alert_context.verification_status is VerificationStatus.SUPPRESSED
    assert result.alert_context.effective_level is AlertLevel.HIGH
    assert result.scene.alert_level is AlertLevel.HIGH


def test_due_static_window_forces_one_qwen_recheck_and_resolves_red():
    state = InMemoryCameraAlertStateStore.seeded_red(
        stream_id="analysis-a",
        camera_id="cam-a",
        active_alert_id="episode-1",
        next_recheck_event_seconds=65.0,
    )
    vlm = ResultVLM(SceneAnalysis(alert_level=AlertLevel.LOW))
    processor = VideoWindowProcessor(
        detector=EmptyDetector(),
        vlm=vlm,
        yolo_fps=2,
        max_keyframes=2,
        alert_state_store=state,
    )

    result = processor.process(
        static_window().model_copy(
            update={"window_index": 13, "start_seconds": 65.0}
        ),
        camera_id="cam-a",
        stream_id="analysis-a",
    )

    assert vlm.calls == 1
    assert result.vlm_call.reason is VLMCallReason.ACTIVE_ALERT_RECHECK
    assert result.alert_context.episode_resolved is True
    assert state.get("analysis-a", "cam-a").phase.value == "normal"


def test_failed_recheck_returns_degraded_window_and_keeps_red():
    state = InMemoryCameraAlertStateStore.seeded_red(
        stream_id="analysis-a",
        camera_id="cam-a",
        active_alert_id="episode-1",
        next_recheck_event_seconds=65.0,
    )
    processor = VideoWindowProcessor(
        detector=EmptyDetector(),
        vlm=RaisingVLM(),
        yolo_fps=2,
        max_keyframes=2,
        alert_state_store=state,
    )

    result = processor.process(
        static_window().model_copy(
            update={"window_index": 13, "start_seconds": 65.0}
        ),
        camera_id="cam-a",
        stream_id="analysis-a",
    )

    assert result.scene.degraded is True
    assert result.scene.alert_level is AlertLevel.HIGH
    assert result.alert_context.verification_status is VerificationStatus.FAILED
    assert state.get("analysis-a", "cam-a").active_alert_id == "episode-1"


def test_window_processor_exposes_typed_event_lifecycle_with_one_vlm_call():
    vlm = VLM()
    processor = VideoWindowProcessor(
        detector=Detector(), vlm=vlm, yolo_fps=2, max_keyframes=2
    )

    result = processor.process(raw_window(), camera_id="cam_01")

    assert result.observation.window_id == "cam_01_000000"
    assert result.candidates[0].candidate_type == "person_scene"
    assert result.candidates[0].evidence["person_peak_count"] == 1
    assert result.candidates[0].evidence["person_detection_frames"] == 1
    assert result.candidates[0].evidence["sampled_frame_count"] == 2
    assert result.decision.candidate_id == result.candidates[0].candidate_id
    assert result.alert_event.source_candidate_id == result.decision.candidate_id
    assert result.vlm_call.call_vlm is True
    assert result.vlm_call.candidate_id == result.decision.candidate_id
    assert vlm.calls == 1


def test_degraded_vlm_becomes_uncertain_without_alert():
    processor = VideoWindowProcessor(
        detector=Detector(), vlm=VLM(degraded=True), yolo_fps=2, max_keyframes=2
    )

    result = processor.process(raw_window(), camera_id="cam_01")

    assert result.decision.decision.value == "uncertain"
    assert result.decision.raw_output_valid is False
    assert result.alert_event is None


def test_processor_routes_before_vlm_and_preserves_exact_trace(tmp_path):
    from camera_ai.artifacts import WindowArtifactWriter

    vlm = TraceVLM()
    processor = VideoWindowProcessor(
        detector=Detector(),
        vlm=vlm,
        yolo_fps=2,
        max_keyframes=2,
        artifact_writer=WindowArtifactWriter(tmp_path),
    )

    result = processor.process(raw_window(), camera_id="cam_trace")

    assert vlm.candidate.candidate_id == result.candidates[0].candidate_id
    assert result.vlm_trace.prompt == "candidate=person_scene"
    assert result.vlm_trace.raw_output == '{"decision":"yes"}'
    assert result.decision.decision.value == "yes"
    assert result.decision.event_type == "traffic_accident"
    assert result.alert_event.event_type == "traffic_accident"
    assert result.alert_event.severity is Severity.HIGH
    artifact_dir = tmp_path / "cam_trace" / "cam_trace_000000"
    assert (artifact_dir / "vlm_prompt.txt").read_text(encoding="utf-8") == result.vlm_trace.prompt
    assert (artifact_dir / "vlm_raw_output.txt").read_text(encoding="utf-8") == result.vlm_trace.raw_output


def test_verified_event_severity_becomes_effective_window_alert_level():
    state = InMemoryCameraAlertStateStore()
    processor = VideoWindowProcessor(
        detector=Detector(),
        vlm=TraceVLM(),
        yolo_fps=2,
        max_keyframes=2,
        alert_state_store=state,
    )

    result = processor.process(
        raw_window(), camera_id="cam_event", stream_id="analysis-event"
    )

    assert result.vlm_trace.scene.alert_level is AlertLevel.MEDIUM
    assert result.alert_event.event_type == "traffic_accident"
    assert result.alert_event.severity is Severity.HIGH
    assert result.alert_context.effective_level is AlertLevel.HIGH
    assert result.scene.alert_level is AlertLevel.HIGH


def test_opt_in_fire_detector_requires_temporal_confirmation_for_candidate():
    frame = np.zeros((32, 32, 3), dtype=np.uint8)
    window = RawVideoWindow(
        window_index=1,
        start_seconds=5,
        observations=[
            VideoFrameObservation(frame_index=index, timestamp_seconds=5 + index, frame=frame)
            for index in range(3)
        ],
    )
    vlm = VLM()
    processor = VideoWindowProcessor(
        detector=Detector(),
        fire_detector=FireSignalDetector(),
        vlm=vlm,
        yolo_fps=2,
        max_keyframes=2,
    )

    result = processor.process(window, camera_id="cam_fire")

    assert any(
        candidate.candidate_type == "temporally_confirmed_fire_signal"
        for candidate in result.candidates
    )
    assert result.vlm_call.call_vlm is True
    assert vlm.calls == 1

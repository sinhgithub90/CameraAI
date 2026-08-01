from camera_ai.router import route_observation
from camera_ai.schemas import (
    Detection,
    MotionResult,
    VideoWindowObservation,
    WindowDetectionAggregate,
)


def observation(*, detections=None, motion_score=0.8, aggregate=None):
    return VideoWindowObservation(
        camera_id="cam_01",
        window_id="cam_01_000001",
        start_ms=0,
        end_ms=5000,
        motion=MotionResult(motion=True, score=motion_score, changed_ratio=0.2),
        detections=detections or [],
        selected_frames=[12, 20],
        detection_aggregate=aggregate or WindowDetectionAggregate(),
    )


def test_person_and_near_vehicle_routes_to_possible_interaction():
    candidates = route_observation(
        observation(
            detections=[
                Detection(label="person", confidence=0.9, bbox=[10, 10, 40, 90]),
                Detection(label="motorcycle", confidence=0.8, bbox=[35, 30, 100, 100]),
            ],
            aggregate=WindowDetectionAggregate(
                person_peak_count=1,
                vehicle_peak_count=1,
                person_detection_frames=2,
                vehicle_detection_frames=2,
                proximity_frame_count=2,
                sampled_frame_count=2,
            ),
        )
    )

    assert candidates[0].candidate_type == "possible_person_vehicle_interaction"
    assert candidates[0].evidence == {
        "person_peak_count": 1,
        "vehicle_peak_count": 1,
        "person_detection_frames": 2,
        "vehicle_detection_frames": 2,
        "proximity_frame_count": 2,
        "sampled_frame_count": 2,
        "motion_peak": 0.8,
    }
    assert candidates[0].requires_verification is True


def test_motion_without_detections_routes_to_unknown_motion():
    candidates = route_observation(observation())

    assert [candidate.candidate_type for candidate in candidates] == ["unknown_motion"]


def test_multiple_people_with_high_motion_is_only_a_hypothesis():
    people = [
        Detection(label="person", confidence=0.9, bbox=[0, 0, 20, 60]),
        Detection(label="person", confidence=0.8, bbox=[30, 0, 50, 60]),
    ]

    candidates = route_observation(
        observation(
            detections=people,
            motion_score=0.9,
            aggregate=WindowDetectionAggregate(person_peak_count=2),
        )
    )

    assert candidates[0].candidate_type == "multi_person_high_motion"
    assert all(candidate.candidate_type != "fighting" for candidate in candidates)

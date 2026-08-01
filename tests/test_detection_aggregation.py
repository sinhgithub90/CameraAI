from camera_ai.detection_aggregation import aggregate_window_detections
from camera_ai.schemas import Detection, VideoFrameObservation


def person(bbox=None):
    return Detection(
        label="person",
        confidence=0.9,
        bbox=bbox or [0, 0, 20, 60],
    )


def car(bbox=None):
    return Detection(
        label="car",
        confidence=0.8,
        bbox=bbox or [15, 10, 70, 70],
    )


def frame(*detections):
    return VideoFrameObservation(frame_index=0, detections=list(detections))


def test_repeated_person_across_frames_has_peak_count_one():
    result = aggregate_window_detections([frame(person()), frame(person())])

    assert result.person_peak_count == 1
    assert result.person_detection_frames == 2
    assert result.sampled_frame_count == 2


def test_two_people_in_one_frame_have_peak_count_two():
    result = aggregate_window_detections(
        [frame(person(), person([30, 0, 50, 60]))]
    )

    assert result.person_peak_count == 2


def test_proximity_only_counts_pairs_in_the_same_frame():
    separate = aggregate_window_detections([frame(person()), frame(car())])
    together = aggregate_window_detections([frame(person(), car())])

    assert separate.proximity_frame_count == 0
    assert together.proximity_frame_count == 1

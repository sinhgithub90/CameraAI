from camera_ai.schemas import Detection
from camera_ai.temporal_validation import TemporalSignalValidator


def fire_detection():
    return Detection(
        label="fire",
        confidence=0.8,
        bbox=[1, 1, 10, 10],
        source="fire",
        backend="heuristic",
    )


def test_requires_three_consecutive_positive_frames():
    validator = TemporalSignalValidator(min_consecutive=3, max_gap_frames=1)

    assert validator.update(1, [fire_detection()]).confirmed is False
    assert validator.update(2, [fire_detection()]).confirmed is False
    signal = validator.update(3, [fire_detection()])

    assert signal.confirmed is True
    assert signal.consecutive == 3


def test_gap_resets_consecutive_signal():
    validator = TemporalSignalValidator(min_consecutive=3, max_gap_frames=1)
    validator.update(1, [fire_detection()])
    validator.update(2, [fire_detection()])

    signal = validator.update(5, [fire_detection()])

    assert signal.confirmed is False
    assert signal.consecutive == 1

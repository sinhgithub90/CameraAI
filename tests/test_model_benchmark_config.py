import pytest
from pydantic import ValidationError

from camera_ai.benchmark import ModelBenchmarkConfig


def test_composite_requires_exactly_two_keyframes():
    with pytest.raises(ValidationError):
        ModelBenchmarkConfig(model="qwen-fast", frame_mode="composite", keyframes=4)


def test_separate_accepts_multi_frame_benchmark():
    config = ModelBenchmarkConfig(
        model="qwen-strong", frame_mode="separate", keyframes=6
    )

    assert config.keyframes == 6
    assert config.production_enabled is False

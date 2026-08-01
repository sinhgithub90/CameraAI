import asyncio
from io import BytesIO
from pathlib import Path

import pytest
from fastapi import UploadFile

from apps.api import main
from camera_ai.analysis_store import InMemoryAnalysisStore, VideoAnalysis
from camera_ai.schemas import EventObject, MediaType


def _upload(name: str, body: bytes = b"video") -> UploadFile:
    return UploadFile(filename=name, file=BytesIO(body))


def test_camera_ids_are_sanitized_and_duplicates_are_unique():
    files = [_upload("Camera 01.mp4"), _upload("Camera 01.mp4"), _upload("@@.mp4")]

    assert main._camera_ids(files) == ["camera-01", "camera-01-2", "camera-3"]


@pytest.mark.asyncio
async def test_stage_video_writes_upload_to_temp_path():
    path = await main._stage_video(_upload("clip.mp4", b"abc123"))
    try:
        assert path.suffix == ".mp4"
        assert path.read_bytes() == b"abc123"
    finally:
        path.unlink(missing_ok=True)


@pytest.mark.asyncio
async def test_stage_video_rejects_empty_upload_without_leaking_file():
    with pytest.raises(ValueError, match="empty upload"):
        await main._stage_video(_upload("empty.mp4", b""))


@pytest.mark.asyncio
async def test_producer_always_removes_staged_file(monkeypatch, tmp_path):
    staged = tmp_path / "clip.mp4"
    staged.write_bytes(b"video")
    store = InMemoryAnalysisStore()
    analysis = VideoAnalysis(id="analysis-1", camera_id="cam-1")
    await store.create(analysis)
    monkeypatch.setattr(main, "analysis_store", store)

    def fail_stream(_event, _on_window):
        raise ValueError("cannot decode")

    monkeypatch.setattr(main.pipeline, "stream_video_chunks", fail_stream)
    event = EventObject(camera_id="cam-1", image=str(staged), media_type=MediaType.VIDEO)

    await main._produce_video_windows(
        event, "analysis-1", "cam-1", cleanup_path=staged
    )

    assert not staged.exists()
    assert (await store.get("analysis-1")).status == "failed"

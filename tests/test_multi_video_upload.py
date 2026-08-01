import asyncio
from io import BytesIO
from pathlib import Path

import pytest
from fastapi import HTTPException, UploadFile

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


@pytest.mark.asyncio
async def test_multi_video_endpoint_submits_every_file(monkeypatch, tmp_path):
    store = InMemoryAnalysisStore()
    submitted: list[tuple[str, str, str, Path | None]] = []
    monkeypatch.setattr(main, "analysis_store", store)

    async def stage(upload):
        path = tmp_path / upload.filename
        path.write_bytes(await upload.read())
        return path

    async def produce(event, analysis_id, camera_id, cleanup_path=None):
        submitted.append((str(event.image), analysis_id, camera_id, cleanup_path))

    monkeypatch.setattr(main, "_stage_video", stage)
    monkeypatch.setattr(main, "_produce_video_windows", produce)

    response = await main.analyze_videos_async(
        [_upload("cam-a.mp4"), _upload("cam-b.mp4"), _upload("cam-c.mp4")]
    )
    await asyncio.sleep(0)

    assert response.batch_id
    assert len(response.items) == 3
    assert [item.camera_id for item in response.items] == ["cam-a", "cam-b", "cam-c"]
    assert len({item.analysis_id for item in response.items}) == 3
    assert len(submitted) == 3
    for item in response.items:
        assert await store.get(item.analysis_id) is not None


@pytest.mark.asyncio
async def test_multi_video_endpoint_requires_one_to_twenty_files():
    with pytest.raises(HTTPException) as empty:
        await main.analyze_videos_async([])
    assert empty.value.status_code == 400

    with pytest.raises(HTTPException) as too_many:
        await main.analyze_videos_async([_upload(f"{i}.mp4") for i in range(21)])
    assert too_many.value.status_code == 400


@pytest.mark.asyncio
async def test_staging_failure_cleans_prior_files_and_starts_nothing(
    monkeypatch, tmp_path
):
    store = InMemoryAnalysisStore()
    staged_path = tmp_path / "first.mp4"
    calls = 0
    producers: list[object] = []
    monkeypatch.setattr(main, "analysis_store", store)

    async def stage(_upload):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise ValueError("staging failed")
        staged_path.write_bytes(b"video")
        return staged_path

    async def produce(*args, **kwargs):
        producers.append((args, kwargs))

    monkeypatch.setattr(main, "_stage_video", stage)
    monkeypatch.setattr(main, "_produce_video_windows", produce)

    with pytest.raises(HTTPException) as error:
        await main.analyze_videos_async(
            [_upload("first.mp4"), _upload("second.mp4")]
        )

    assert error.value.status_code == 400
    assert not staged_path.exists()
    assert store._analyses == {}
    assert producers == []

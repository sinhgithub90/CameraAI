# Multi-Video Upload Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add one async FastAPI endpoint that accepts up to 20 video files and submits every file as an independent analysis using the existing in-process pipeline.

**Architecture:** Stage uploads to temporary files in bounded chunks, then create one `VideoAnalysis` and background producer per file. Reuse the existing `_produce_video_windows`, `VLMQueue`, `VLMWorker`, and per-analysis polling endpoint; no batch persistence or RabbitMQ runtime is introduced.

**Tech Stack:** Python 3.11+, FastAPI multipart uploads, asyncio, Pydantic, pytest, pytest-asyncio.

## Global Constraints

- Accept 1–20 files per request.
- Derive `camera_id` from sanitized filename stems; make duplicates unique with numeric suffixes.
- Stage uploads incrementally to temporary files; never read the complete batch into RAM.
- If staging fails, clean every staged file and start no producers.
- Each accepted video creates an independent `analysis_id` pollable through `GET /analyses/{analysis_id}`.
- Preserve `/async/analyze/video` behavior.
- Do not enable RabbitMQ, add VLM workers, change replay pacing, or modify detection/VLM logic.

---

### Task 1: Upload Staging, Camera IDs, and Producer Cleanup

**Files:**
- Modify: `apps/api/main.py:17-25,195-214`
- Create: `tests/test_multi_video_upload.py`

**Interfaces:**
- Consumes: FastAPI `UploadFile`, `EventObject`, existing `_produce_video_windows(event, analysis_id, camera_id)`.
- Produces: `StagedVideo`, `_camera_ids(files) -> list[str]`, `_stage_video(upload) -> Path`, and `_produce_video_windows(..., cleanup_path: Path | None = None)`.

- [ ] **Step 1: Write failing helper and cleanup tests**

Create `tests/test_multi_video_upload.py`:

```python
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
```

- [ ] **Step 2: Run tests to verify missing helpers fail**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_multi_video_upload.py -q
```

Expected: failures because `_camera_ids`, `_stage_video`, and `cleanup_path` do not exist.

- [ ] **Step 3: Implement focused staging helpers**

Add constants and models/helpers to `apps/api/main.py`:

```python
MAX_BATCH_VIDEOS = 20
UPLOAD_CHUNK_BYTES = 1024 * 1024


class StagedVideo(BaseModel):
    filename: str
    camera_id: str
    path: Path


def _camera_ids(files: list[UploadFile]) -> list[str]:
    used: dict[str, int] = {}
    result: list[str] = []
    for index, upload in enumerate(files, start=1):
        stem = Path(upload.filename or "").stem.lower()
        base = re.sub(r"[^a-z0-9]+", "-", stem).strip("-") or f"camera-{index}"
        used[base] = used.get(base, 0) + 1
        result.append(base if used[base] == 1 else f"{base}-{used[base]}")
    return result


async def _stage_video(upload: UploadFile) -> Path:
    suffix = Path(upload.filename or "video.mp4").suffix or ".mp4"
    handle = tempfile.NamedTemporaryFile(suffix=suffix, delete=False)
    path = Path(handle.name)
    size = 0
    try:
        while chunk := await upload.read(UPLOAD_CHUNK_BYTES):
            handle.write(chunk)
            size += len(chunk)
    except Exception:
        handle.close()
        path.unlink(missing_ok=True)
        raise
    finally:
        if not handle.closed:
            handle.close()
    if size == 0:
        path.unlink(missing_ok=True)
        raise ValueError(f"empty upload: {upload.filename or 'unnamed'}")
    return path
```

Import `re`, `tempfile`, `BaseModel`, and replace the producer signature/body with:

```python
async def _produce_video_windows(
    event: EventObject,
    analysis_id: str,
    camera_id: str,
    cleanup_path: Path | None = None,
) -> None:
    loop = asyncio.get_running_loop()

    def on_window(window: RawVideoWindow) -> None:
        future = asyncio.run_coroutine_threadsafe(
            _enqueue_video_window(analysis_id, camera_id, window), loop
        )
        future.result()

    try:
        try:
            await asyncio.to_thread(pipeline.stream_video_chunks, event, on_window)
        except Exception as exc:
            logger.exception("async video producer failed analysis=%s", analysis_id)
            await analysis_store.mark_producer_failed(analysis_id, str(exc))
        else:
            await analysis_store.mark_producer_complete(analysis_id)
    finally:
        if cleanup_path is not None:
            cleanup_path.unlink(missing_ok=True)
```

- [ ] **Step 4: Run helper and existing producer tests**

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_multi_video_upload.py tests/test_async_video_response.py -q
```

Expected: all tests pass.

- [ ] **Step 5: Commit**

```powershell
git add apps/api/main.py tests/test_multi_video_upload.py
git commit -m "feat: stage video uploads for background analysis"
```

---

### Task 2: Multi-Video Async Endpoint

**Files:**
- Modify: `apps/api/main.py:278-300`
- Modify: `tests/test_multi_video_upload.py`

**Interfaces:**
- Consumes: `_camera_ids`, `_stage_video`, `_produce_video_windows`, `analysis_store.create`, `VideoAnalysis`.
- Produces: `MultiVideoItem`, `MultiVideoResponse`, `POST /async/analyze/videos` and `analyze_videos_async(files: list[UploadFile]) -> MultiVideoResponse`.

- [ ] **Step 1: Add failing successful-submission test**

Append:

```python
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
    response = await main.analyze_videos_async([
        _upload("cam-a.mp4"), _upload("cam-b.mp4"), _upload("cam-c.mp4")
    ])
    await asyncio.sleep(0)

    assert len(response.items) == 3
    assert [item.camera_id for item in response.items] == ["cam-a", "cam-b", "cam-c"]
    assert len({item.analysis_id for item in response.items}) == 3
    assert len(submitted) == 3
    for item in response.items:
        assert await store.get(item.analysis_id) is not None
```

- [ ] **Step 2: Add failing validation and atomic-cleanup tests**

Append tests asserting:

```python
with pytest.raises(HTTPException) as empty:
    await main.analyze_videos_async([])
assert empty.value.status_code == 400

with pytest.raises(HTTPException) as too_many:
    await main.analyze_videos_async([_upload(f"{i}.mp4") for i in range(21)])
assert too_many.value.status_code == 400
```

Add the atomic cleanup test:

```python
@pytest.mark.asyncio
async def test_staging_failure_cleans_prior_files_and_starts_nothing(monkeypatch, tmp_path):
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
        await main.analyze_videos_async([_upload("first.mp4"), _upload("second.mp4")])
    assert error.value.status_code == 400
    assert not staged_path.exists()
    assert store._analyses == {}
    assert producers == []
```

- [ ] **Step 3: Run endpoint tests to verify failure**

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_multi_video_upload.py -q
```

Expected: failure because response models and endpoint do not exist.

- [ ] **Step 4: Implement response models and endpoint**

Add:

```python
class MultiVideoItem(BaseModel):
    filename: str
    camera_id: str
    analysis_id: str


class MultiVideoResponse(BaseModel):
    batch_id: str
    items: list[MultiVideoItem]


@app.post("/async/analyze/videos", response_model=MultiVideoResponse)
async def analyze_videos_async(files: list[UploadFile] = File(...)) -> MultiVideoResponse:
    if not 1 <= len(files) <= MAX_BATCH_VIDEOS:
        raise HTTPException(status_code=400, detail="files must contain 1..20 videos")
    camera_ids = _camera_ids(files)
    staged: list[StagedVideo] = []
    try:
        for upload, camera_id in zip(files, camera_ids, strict=True):
            staged.append(StagedVideo(
                filename=upload.filename or f"{camera_id}.mp4",
                camera_id=camera_id,
                path=await _stage_video(upload),
            ))
    except Exception as exc:
        for item in staged:
            item.path.unlink(missing_ok=True)
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    batch_id = uuid.uuid4().hex
    items: list[MultiVideoItem] = []
    for item in staged:
        analysis_id = uuid.uuid4().hex
        await analysis_store.create(VideoAnalysis(id=analysis_id, camera_id=item.camera_id))
        event = EventObject(
            camera_id=item.camera_id,
            image=str(item.path),
            media_type=MediaType.VIDEO,
        )
        asyncio.create_task(_produce_video_windows(
            event, analysis_id, item.camera_id, cleanup_path=item.path
        ))
        items.append(MultiVideoItem(
            filename=item.filename,
            camera_id=item.camera_id,
            analysis_id=analysis_id,
        ))
    return MultiVideoResponse(batch_id=batch_id, items=items)
```

- [ ] **Step 5: Run focused and full regression tests**

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_multi_video_upload.py tests/test_async_video_response.py -q
.\.venv\Scripts\python.exe -m pytest tests -q
```

Expected: focused tests pass; full suite passes with Rabbit integration tests skipped unless explicitly enabled.

- [ ] **Step 6: Commit**

```powershell
git add apps/api/main.py tests/test_multi_video_upload.py
git commit -m "feat: accept multiple videos for async analysis"
```

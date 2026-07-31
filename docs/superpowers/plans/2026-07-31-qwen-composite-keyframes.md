# Qwen Composite Keyframes Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Send the two selected video keyframes to Qwen as one labelled 960 x 1080 composite by default, with an environment-controlled rollback to the existing two-image request.

**Architecture:** Keep the video pipeline and its two original keyframes unchanged. Add frame preparation inside `OllamaQwenAnalyzer`: exact two-frame sequences use a vertical composite in `composite` mode, while `separate` and non-two-frame inputs preserve the current request shape.

**Tech Stack:** Python 3.11+, NumPy, OpenCV, requests, pytest, Ollama `/api/chat`

## Global Constraints

- Keep Motion, YOLO26n, keyframe selection, Qwen 4B and public API schemas unchanged.
- Default `OLLAMA_FRAME_MODE` to `composite`; accept only `composite` and `separate`.
- Composite only an exact two-frame sequence; preserve one-frame and non-two-frame request counts.
- Fit each source into a 960 x 540 black panel with aspect ratio preserved and `cv2.INTER_LINEAR` downscaling.
- Stack `TRUOC` above `SAU` into a 960 x 1080 BGR image.
- Keep JSON schema, `num_ctx=4096`, `num_predict=96` and keep-alive unchanged.
- Preserve `yolo26n.pt` as an untracked local model file.

---

### Task 1: Composite construction and frame-mode configuration

**Files:**
- Modify: `src/camera_ai/vlm/ollama_qwen.py`
- Modify: `tests/test_vlm_context.py`

**Interfaces:**
- Consumes: `Sequence[np.ndarray]` BGR frames and optional constructor `frame_mode: str | None`
- Produces: `_prepare_images(frames) -> list[np.ndarray]`, `_compose_two_frames(frames) -> np.ndarray`, validated `self.frame_mode`

- [ ] **Step 1: Write failing configuration tests**

```python
def test_composite_mode_is_default_and_can_be_overridden(monkeypatch):
    monkeypatch.delenv("OLLAMA_FRAME_MODE", raising=False)
    assert OllamaQwenAnalyzer().frame_mode == "composite"
    monkeypatch.setenv("OLLAMA_FRAME_MODE", "separate")
    assert OllamaQwenAnalyzer().frame_mode == "separate"


def test_invalid_frame_mode_fails_fast():
    with pytest.raises(ValueError, match="OLLAMA_FRAME_MODE"):
        OllamaQwenAnalyzer(frame_mode="unknown")
```

- [ ] **Step 2: Write failing composite-layout test**

Use solid colors so temporal order is independently observable away from the labels.

```python
def test_two_frame_composite_preserves_order_and_dimensions():
    before = np.full((360, 640, 3), (10, 20, 230), dtype=np.uint8)
    after = np.full((360, 640, 3), (40, 210, 30), dtype=np.uint8)
    composite = OllamaQwenAnalyzer()._compose_two_frames([before, after])
    assert composite.shape == (1080, 960, 3)
    assert np.all(composite[270, 480] == before[0, 0])
    assert np.all(composite[810, 480] == after[0, 0])
```

- [ ] **Step 3: Write failing aspect-ratio and input-count tests**

```python
def test_composite_letterboxes_portrait_frames():
    portrait = np.full((800, 400, 3), 255, dtype=np.uint8)
    composite = OllamaQwenAnalyzer()._compose_two_frames([portrait, portrait])
    assert np.all(composite[270, 10] == 0)
    assert np.all(composite[270, 480] == 255)


@pytest.mark.parametrize("count", [1, 3])
def test_composite_mode_preserves_non_two_frame_counts(count):
    frames = [np.zeros((64, 64, 3), dtype=np.uint8) for _ in range(count)]
    assert len(OllamaQwenAnalyzer()._prepare_images(frames)) == count
```

- [ ] **Step 4: Run tests and verify RED**

Run: `python -m pytest tests/test_vlm_context.py -v`

Expected: failures identify missing `frame_mode`, `_compose_two_frames` and `_prepare_images`.

- [ ] **Step 5: Implement validated mode selection**

```python
DEFAULT_FRAME_MODE = "composite"
PANEL_WIDTH = 960
PANEL_HEIGHT = 540
VALID_FRAME_MODES = {"composite", "separate"}

self.frame_mode = (
    frame_mode or os.getenv("OLLAMA_FRAME_MODE") or DEFAULT_FRAME_MODE
).strip().lower()
if self.frame_mode not in VALID_FRAME_MODES:
    raise ValueError(
        "OLLAMA_FRAME_MODE must be 'composite' or 'separate'"
    )
```

- [ ] **Step 6: Implement panel fitting and composition**

Add `_fit_panel(frame, label)` that computes `scale = min(960 / width, 540 / height, 1.0)`, resizes with `INTER_LINEAR`, centers the result on a black panel, then overlays a black label background and white `cv2.putText`. Implement:

```python
def _prepare_images(self, frames: Sequence[np.ndarray]) -> list[np.ndarray]:
    if self.frame_mode == "composite" and len(frames) == 2:
        return [self._compose_two_frames(frames)]
    return list(frames)

@classmethod
def _compose_two_frames(cls, frames: Sequence[np.ndarray]) -> np.ndarray:
    if len(frames) != 2:
        raise ValueError("exactly two frames are required for a composite")
    return np.vstack((
        cls._fit_panel(frames[0], "TRUOC"),
        cls._fit_panel(frames[1], "SAU"),
    ))
```

- [ ] **Step 7: Run focused tests and verify GREEN**

Run: `python -m pytest tests/test_vlm_context.py -v`

Expected: all frame-mode and layout tests pass.

---

### Task 2: Integrate the prepared image into the Ollama request

**Files:**
- Modify: `src/camera_ai/vlm/ollama_qwen.py`
- Modify: `tests/test_vlm_context.py`

**Interfaces:**
- Consumes: Task 1 `_prepare_images(frames)` and existing `_frame_to_jpeg_b64(frame)`
- Produces: one encoded image for exact two-frame composite mode; existing counts otherwise

- [ ] **Step 1: Replace the old two-image assertion with failing mode-specific request tests**

```python
def capture_payload(monkeypatch, frame_count: int, frame_mode: str | None = None):
    captured = {}

    def fake_post(url, **kwargs):
        captured.update(kwargs)
        return FakeResponse()

    monkeypatch.setattr("camera_ai.vlm.ollama_qwen.requests.post", fake_post)
    if frame_mode is None:
        monkeypatch.delenv("OLLAMA_FRAME_MODE", raising=False)
    analyzer = OllamaQwenAnalyzer(frame_mode=frame_mode)
    frame = np.zeros((64, 64, 3), dtype=np.uint8)
    analyzer.analyze_sequence([frame] * frame_count, [])
    return captured["json"]


def test_default_two_frame_request_sends_one_composite(monkeypatch):
    payload = capture_payload(monkeypatch, frame_count=2)
    assert len(payload["messages"][0]["images"]) == 1


def test_separate_mode_sends_two_images(monkeypatch):
    payload = capture_payload(monkeypatch, frame_count=2, frame_mode="separate")
    assert len(payload["messages"][0]["images"]) == 2
```

Keep the existing JSON-schema assertions in the default request test so the optimization cannot remove structured output.

- [ ] **Step 2: Add failing request telemetry test**

```python
with caplog.at_level(logging.INFO, logger="camera_ai.vlm.ollama_qwen"):
    analyzer.analyze_sequence([frame, frame], [])
assert "frame_mode=composite" in caplog.text
assert "source_frames=2" in caplog.text
assert "sent_images=1" in caplog.text
assert "composite_shape=960x1080" in caplog.text
```

- [ ] **Step 3: Run mode-specific tests and verify RED**

Run: `python -m pytest tests/test_vlm_context.py -v`

Expected: the default request still sends two images and telemetry is absent.

- [ ] **Step 4: Prepare images before prompt and JPEG encoding**

```python
prepared_images = self._prepare_images(frames)
if self.frame_mode == "composite" and len(frames) == 2:
    prompt += (
        "\nẢnh ghép theo thời gian: nửa trên là TRƯỚC, "
        "nửa dưới là SAU. Hãy xét thay đổi giữa hai nửa."
    )
elif len(frames) > 1:
    prompt += "\nCác ảnh theo thứ tự thời gian; hãy xét thay đổi giữa chúng."
```

Encode `prepared_images` instead of `frames`, then log mode, source count, sent count and dimensions. Use `width x height` order in the log, yielding `960x1080`.

- [ ] **Step 5: Run Qwen adapter and model-config tests**

Run: `python -m pytest tests/test_vlm_context.py tests/test_model_config.py -v`

Expected: all focused tests pass; the request still contains the compact JSON schema and optimized options.

- [ ] **Step 6: Commit runtime and tests**

```powershell
git add src/camera_ai/vlm/ollama_qwen.py tests/test_vlm_context.py
git commit -m "perf: composite qwen keyframes"
```

---

### Task 3: Document modes, verify regressions and benchmark A/B

**Files:**
- Modify: `README.md`
- Modify: `docs/camera-ai-pipeline.md`

**Interfaces:**
- Consumes: `OLLAMA_FRAME_MODE=composite|separate`
- Produces: operator commands and comparable warm timing evidence

- [ ] **Step 1: Document the new default and rollback**

Add `OLLAMA_FRAME_MODE=composite` to setup examples and the environment table. Explain that the pipeline still selects two frames; only the Qwen request changes to one labelled image. Include:

```powershell
$env:OLLAMA_FRAME_MODE = "composite" # default trial
$env:OLLAMA_FRAME_MODE = "separate"  # old two-image request
```

- [ ] **Step 2: Run the complete automated suite**

Run: `python -m pytest tests -q`

Expected: all tests pass without requiring Ollama or YOLO.

- [ ] **Step 3: Check formatting and worktree scope**

Run: `git diff --check` and `git status --short`

Expected: no whitespace errors; `yolo26n.pt` remains the only unrelated untracked file.

- [ ] **Step 4: Commit documentation**

```powershell
git add README.md docs/camera-ai-pipeline.md
git commit -m "docs: describe qwen composite mode"
```

- [ ] **Step 5: Benchmark the old request on the reference video**

Set `OLLAMA_FRAME_MODE=separate`, restart the API, warm it with one upload, then upload the same video again. Record `[ollama] prompt_tokens`, `prompt_ms`, `output_ms`, pipeline `qwen_ms`, alert level and summary.

- [ ] **Step 6: Benchmark composite mode on the same video**

Set `OLLAMA_FRAME_MODE=composite`, restart the API, warm it with one upload, then upload the same video again. Record the same fields. Keep composite as the default if `qwen_ms` improves by roughly 0.3 to 0.8 seconds and the alert conclusion remains equivalent; otherwise use `separate` while retaining the implementation for further trials.

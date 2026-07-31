# Video Motion YOLO VLM Pipeline Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Thay luồng xử lý video lấy mẫu cố định bằng pipeline Motion → YOLO/Fire → Candidate/Keyframe Selector → VLM, giữ nguyên API và luồng ảnh.

**Architecture:** Tạo detector motion thuần OpenCV và các selector framework-agnostic. `SecurityAIPipeline._analyze_video` đọc video một lần, tạo các observation theo cửa sổ, chỉ chạy detector đắt tiền khi có motion, rồi gửi một lần 4–8 keyframe cho analyzer với khả năng tương thích ngược với analyzer hiện tại.

**Tech Stack:** Python 3.11+, OpenCV, NumPy, Pydantic, pytest, interface detector/VLM hiện có.

## Global Constraints

- Chỉ thay đổi luồng `MediaType.VIDEO`; luồng ảnh và FastAPI contract hiện tại phải tiếp tục hoạt động.
- Motion mặc định khoảng 5 FPS; YOLO/Fire mặc định khoảng 2 FPS.
- Có motion nhưng YOLO không có detection vẫn phải gọi VLM.
- Video tĩnh phải skip YOLO/VLM.
- Tối đa 8 keyframe và tối đa một lần gọi VLM cho mỗi cửa sổ phân tích.
- Không thêm queue, tracking, batching hoặc model mới trong MVP.

---

### Task 1: Bổ sung data contracts cho phân tích video

**Files:**
- Modify: `src/camera_ai/schemas.py`
- Test: `tests/test_video_pipeline.py`

**Interfaces:**
- Produces `MotionRegion`, `MotionResult`, `VideoFrameObservation` và metadata video tùy chọn cho `PipelineResult`.

- [ ] **Step 1: Viết test model cho motion và observation**

```python
def test_motion_result_and_video_observation_contracts():
    motion = MotionResult(motion=True, changed_ratio=0.2, score=0.8)
    item = VideoFrameObservation(frame_index=3, timestamp_seconds=0.6, motion=motion)
    assert item.motion.score == 0.8
    assert item.frame_index == 3
```

- [ ] **Step 2: Chạy test để xác nhận thất bại**

Run: `python -m pytest tests/test_video_pipeline.py::test_motion_result_and_video_observation_contracts -v`

Expected: FAIL vì các model chưa tồn tại.

- [ ] **Step 3: Implement model tối thiểu**

Thêm model Pydantic với `MotionRegion(x, y, w, h)`, `MotionResult(motion, changed_ratio, regions, score)`, `VideoFrameObservation(frame_index, timestamp_seconds, motion, detections, frame)`; cho phép `frame` là field nội bộ tùy chọn không serialize. Thêm `VideoAnalysisStats` vào `PipelineResult` với các counter tùy chọn.

- [ ] **Step 4: Chạy test và toàn bộ test hiện có**

Run: `python -m pytest tests/test_video_pipeline.py::test_motion_result_and_video_observation_contracts tests/test_pipeline.py -v`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/camera_ai/schemas.py tests/test_video_pipeline.py
git commit -m "feat: add video analysis data contracts"
```

### Task 2: Implement MotionDetector

**Files:**
- Create: `src/camera_ai/detectors/motion.py`
- Modify: `src/camera_ai/detectors/__init__.py`
- Test: `tests/test_video_pipeline.py`

**Interfaces:**
- Consumes: BGR `numpy.ndarray` frames.
- Produces: `MotionDetector.compare(frame) -> MotionResult`.

- [ ] **Step 1: Viết test cho frame tĩnh, frame thay đổi và vùng thay đổi**

```python
def test_motion_detector_detects_changed_region():
    detector = MotionDetector(threshold=0.01)
    first = np.zeros((80, 80, 3), dtype=np.uint8)
    second = first.copy()
    second[20:50, 25:60] = 255
    detector.compare(first)
    result = detector.compare(second)
    assert result.motion is True
    assert result.changed_ratio > 0
    assert result.regions
```

- [ ] **Step 2: Run test and verify failure**

Run: `python -m pytest tests/test_video_pipeline.py::test_motion_detector_detects_changed_region -v`

Expected: FAIL because `MotionDetector` is missing.

- [ ] **Step 3: Implement OpenCV motion detector**

Convert frames to grayscale, blur, compare with the previous baseline using absolute difference, threshold and morphology, then return connected-component bounding regions. Clamp `changed_ratio` and `score` to `[0, 1]`; first frame returns no motion.

- [ ] **Step 4: Add tests for reset and static frames**

```python
def test_motion_detector_first_and_static_frames_are_calm():
    detector = MotionDetector()
    frame = np.zeros((32, 32, 3), dtype=np.uint8)
    assert detector.compare(frame).motion is False
    assert detector.compare(frame.copy()).motion is False
```

- [ ] **Step 5: Run motion tests**

Run: `python -m pytest tests/test_video_pipeline.py -k motion -v`

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add src/camera_ai/detectors/motion.py src/camera_ai/detectors/__init__.py tests/test_video_pipeline.py
git commit -m "feat: add lightweight motion detector"
```

### Task 3: Implement candidate and keyframe selectors

**Files:**
- Create: `src/camera_ai/video_selection.py`
- Test: `tests/test_video_pipeline.py`

**Interfaces:**
- Consumes: `VideoFrameObservation` objects.
- Produces: `score_observations(observations) -> list[tuple[float, VideoFrameObservation]]` and `select_keyframes(observations, max_keyframes=8) -> list[VideoFrameObservation]`.

- [ ] **Step 1: Viết test chọn keyframe giới hạn và không trùng**

```python
def test_keyframe_selector_returns_ordered_unique_keyframes():
    observations = make_observations(12, motion_indices={3, 4, 5}, detection_indices={4})
    selected = select_keyframes(observations, max_keyframes=6)
    assert len(selected) <= 6
    assert [item.frame_index for item in selected] == sorted({item.frame_index for item in selected})
    assert 4 in [item.frame_index for item in selected]
```

- [ ] **Step 2: Run test and verify failure**

Run: `python -m pytest tests/test_video_pipeline.py::test_keyframe_selector_returns_ordered_unique_keyframes -v`

Expected: FAIL because selector functions are missing.

- [ ] **Step 3: Implement scoring and selection**

Score motion, detection confidence/count and temporal positions. Reserve first/last and peak motion/peak detection candidates, add before/after motion boundaries, deduplicate by frame index, sort chronologically and cap at 8.

- [ ] **Step 4: Add tests for no-motion and peak selection**

Assert a non-empty observation list always yields at least the first frame, while a motion sequence includes a peak-motion frame and a detection frame when available.

- [ ] **Step 5: Run selector tests**

Run: `python -m pytest tests/test_video_pipeline.py -k selector -v`

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add src/camera_ai/video_selection.py tests/test_video_pipeline.py
git commit -m "feat: add video candidate and keyframe selection"
```

### Task 4: Extend VLM interface for keyframe sequences

**Files:**
- Modify: `src/camera_ai/vlm/base.py`
- Modify: `src/camera_ai/vlm/mock.py`
- Modify: `src/camera_ai/vlm/ollama_qwen.py`
- Test: `tests/test_video_pipeline.py`

**Interfaces:**
- Adds backward-compatible `analyze_sequence(frames, detections) -> SceneAnalysis` on `VLMAnalyzer`, defaulting to `analyze` with the representative frame.

- [ ] **Step 1: Viết test mock nhận một chuỗi keyframe**

```python
def test_vlm_sequence_analysis_is_called_once():
    vlm = CountingMockAnalyzer()
    result = vlm.analyze_sequence([frame_a, frame_b], [])
    assert vlm.sequence_calls == 1
    assert result.degraded is True
```

- [ ] **Step 2: Run test and verify failure**

Run: `python -m pytest tests/test_video_pipeline.py::test_vlm_sequence_analysis_is_called_once -v`

Expected: FAIL because the sequence method is missing.

- [ ] **Step 3: Implement compatibility method and Qwen sequence prompt**

Add the default method to the abstract base without breaking custom analyzers. For Ollama, encode each frame as an image in temporal order and send one chat request with the existing JSON-only scene-analysis contract. Mock delegates to the representative frame and records one sequence call.

- [ ] **Step 4: Run VLM tests and existing tests**

Run: `python -m pytest tests/test_video_pipeline.py tests/test_pipeline.py -v`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/camera_ai/vlm/base.py src/camera_ai/vlm/mock.py src/camera_ai/vlm/ollama_qwen.py tests/test_video_pipeline.py
git commit -m "feat: support temporal keyframe analysis"
```

### Task 5: Integrate motion-first video orchestration

**Files:**
- Modify: `src/camera_ai/pipeline.py`
- Modify: `src/camera_ai/schemas.py`
- Test: `tests/test_video_pipeline.py`

**Interfaces:**
- `SecurityAIPipeline.__init__` accepts optional `motion_detector`, sampling FPS and selector limits.
- `_analyze_video` returns the existing `PipelineResult`, including video stats.

- [ ] **Step 1: Viết integration tests với fake capture/detectors**

Cover four cases: static video skips YOLO/VLM; motion with empty YOLO still calls VLM; motion calls YOLO less often than input frames; and VLM is called once with no more than eight keyframes.

- [ ] **Step 2: Run integration tests and verify failure**

Run: `python -m pytest tests/test_video_pipeline.py -k video_integration -v`

Expected: FAIL because current video path always runs YOLO on fixed samples and calls single-frame VLM.

- [ ] **Step 3: Implement orchestration**

Read each frame once, sample Motion by frame interval derived from FPS, maintain a rolling 5-second observation window, run YOLO/Fire only on motion windows at the configured detector interval, select keyframes, call `vlm.analyze_sequence` once, and build stats. Preserve temp-file cleanup and existing representative annotation behavior.

- [ ] **Step 4: Run integration tests**

Run: `python -m pytest tests/test_video_pipeline.py -k video_integration -v`

Expected: PASS.

- [ ] **Step 5: Run full suite**

Run: `python -m pytest tests/ -v`

Expected: PASS with all pre-existing image tests unchanged.

- [ ] **Step 6: Commit**

```bash
git add src/camera_ai/pipeline.py src/camera_ai/schemas.py tests/test_video_pipeline.py
git commit -m "feat: use motion-first pipeline for video analysis"
```

### Task 6: Verify behavior and document configuration

**Files:**
- Modify: `README.md`
- Modify: `tests/test_video_pipeline.py` if verification exposes a defect.

- [ ] **Step 1: Run formatting/compile checks**

Run: `python -m compileall src apps tests`

Expected: PASS without syntax errors.

- [ ] **Step 2: Run the complete test suite again**

Run: `python -m pytest tests/ -v`

Expected: PASS.

- [ ] **Step 3: Update README**

Document video defaults, motion-first behavior, skip behavior, keyframe cap and the future queue-compatible stage boundaries.

- [ ] **Step 4: Review diff and commit**

```bash
git diff --check
git status --short
git add README.md
git commit -m "docs: describe motion-first video analysis"
```

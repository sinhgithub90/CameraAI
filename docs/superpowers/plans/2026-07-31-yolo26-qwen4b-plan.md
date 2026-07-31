# YOLO26n Qwen-VL 4B Configuration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Đổi cấu hình mặc định của upload pipeline sang `yolo26n.pt` và `qwen3-vl:4b-instruct-q4_K_M`, vẫn giữ FireDetector riêng.

**Architecture:** `YOLODetector` đọc `YOLO_WEIGHTS` khi không truyền weights; `OllamaQwenAnalyzer` đã đọc `OLLAMA_MODEL` nên chỉ đổi default model. API upload giữ nguyên endpoints và response contract.

**Tech Stack:** Python 3.11+, Ultralytics, Ollama HTTP API, pytest.

## Global Constraints

- YOLO object detector mặc định dùng `yolo26n.pt`.
- FireDetector vẫn chạy riêng.
- VLM mặc định dùng `qwen3-vl:4b-instruct-q4_K_M`.
- Cho phép override bằng `YOLO_WEIGHTS`, `OLLAMA_MODEL`, `OLLAMA_BASE_URL` và `FIRE_MODEL`.
- API upload giữ `POST /analyze/image` và `POST /analyze/video`.

---

### Task 1: Test và đổi model defaults

**Files:**
- Modify: `src/camera_ai/detectors/yolo.py`
- Modify: `src/camera_ai/vlm/ollama_qwen.py`
- Test: `tests/test_model_config.py`

- [ ] **Step 1: Viết test default và env override**

```python
def test_yolo_default_is_yolo26n(monkeypatch):
    monkeypatch.delenv("YOLO_WEIGHTS", raising=False)
    assert YOLODetector().weights == "yolo26n.pt"

def test_yolo_weights_can_be_overridden(monkeypatch):
    monkeypatch.setenv("YOLO_WEIGHTS", "custom.pt")
    assert YOLODetector().weights == "custom.pt"

def test_qwen_default_is_local_4b_model(monkeypatch):
    monkeypatch.delenv("OLLAMA_MODEL", raising=False)
    assert OllamaQwenAnalyzer().model == "qwen3-vl:4b-instruct-q4_K_M"
```

- [ ] **Step 2: Chạy test để xác nhận fail**

Run: `python -m pytest tests/test_model_config.py -v`

Expected: FAIL vì default hiện là YOLO11n, không có `YOLO_WEIGHTS` override và Qwen 2B.

- [ ] **Step 3: Implement thay đổi tối thiểu**

Đổi `DEFAULT_WEIGHTS`, đọc `os.getenv("YOLO_WEIGHTS")` trong constructor khi `weights` giữ default; đổi `DEFAULT_MODEL` sang model Qwen 4B local.

- [ ] **Step 4: Chạy test config và toàn bộ suite**

Run: `$env:PYTHONPATH='src'; python -m pytest tests/test_model_config.py tests/ -v`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/camera_ai/detectors/yolo.py src/camera_ai/vlm/ollama_qwen.py tests/test_model_config.py
git commit -m "feat: configure yolo26n and qwen4b defaults"
```

### Task 2: Cập nhật tài liệu và verification

**Files:**
- Modify: `README.md`

- [ ] **Step 1: Cập nhật README**

Ghi rõ model mặc định, lệnh kiểm tra `ollama list`, và lệnh chạy API upload.

- [ ] **Step 2: Chạy compile, test và diff check**

Run: `python -m compileall src apps tests; $env:PYTHONPATH='src'; python -m pytest tests/ -v; git diff --check`

Expected: compile thành công, toàn bộ test pass, diff không có whitespace error.

- [ ] **Step 3: Commit tài liệu**

```bash
git add README.md
git commit -m "docs: document yolo26 and qwen4b setup"
```

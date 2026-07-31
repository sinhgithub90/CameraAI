# Qwen 4B Latency Optimization Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Reduce warm Qwen 4B video-window latency and cold-load frequency without reducing the current four-frame visual input.

**Architecture:** Keep optimization inside `OllamaQwenAnalyzer`: configure Ollama for the 6 GB GPU, compact only the detector text sent to Qwen, and log server timing metadata. The pipeline and public result schema remain unchanged.

**Tech Stack:** Python 3.11, requests, OpenCV, NumPy, pytest, Ollama `/api/chat`.

## Global Constraints

- Keep `qwen3-vl:4b-instruct-q4_K_M` and at most four chronological keyframes.
- Default to `num_ctx=4096`, `keep_alive="10m"`, `num_predict=160`, and `temperature=0`.
- Preserve environment overrides for context, keep-alive, and prediction limit.
- Include at most three detections per label and twelve detection lines total in the Qwen prompt.
- Preserve complete detections in pipeline results; only prompt text is compacted.
- Keep existing degraded error handling and the public response schema unchanged.

---

### Task 1: Optimized Ollama request configuration and telemetry

**Files:**
- Modify: `tests/test_vlm_context.py`
- Modify: `tests/test_model_config.py`
- Modify: `src/camera_ai/vlm/ollama_qwen.py`

**Interfaces:**
- Consumes: `OllamaQwenAnalyzer.analyze_sequence(frames, detections)`.
- Produces: request JSON options and top-level `keep_alive`; constructor fields `num_ctx`, `num_predict`, and `keep_alive`.

- [ ] **Step 1: Write failing request configuration tests**

Add assertions that a default analyzer sends `num_ctx=4096`, `num_predict=160`, `temperature=0`, and `keep_alive="10m"`, and that `OLLAMA_NUM_CTX`, `OLLAMA_NUM_PREDICT`, and `OLLAMA_KEEP_ALIVE` override those defaults.

```python
def test_ollama_uses_gpu_friendly_request_defaults(monkeypatch):
    captured = {}
    monkeypatch.delenv("OLLAMA_NUM_CTX", raising=False)
    monkeypatch.delenv("OLLAMA_NUM_PREDICT", raising=False)
    monkeypatch.delenv("OLLAMA_KEEP_ALIVE", raising=False)
    monkeypatch.setattr(
        "camera_ai.vlm.ollama_qwen.requests.post",
        lambda url, **kwargs: captured.update(kwargs) or FakeResponse(),
    )
    frame = np.zeros((64, 64, 3), dtype=np.uint8)
    OllamaQwenAnalyzer().analyze_sequence([frame] * 4, [])
    assert captured["json"]["options"] == {
        "num_ctx": 4096,
        "num_predict": 160,
        "temperature": 0,
    }
    assert captured["json"]["keep_alive"] == "10m"
```

- [ ] **Step 2: Run tests and verify RED**

Run: `$env:PYTHONPATH='src'; python -m pytest tests/test_vlm_context.py tests/test_model_config.py -q`

Expected: FAIL because defaults, prediction limit, keep-alive, and environment fields are absent.

- [ ] **Step 3: Implement minimal request configuration**

Add constants and constructor parameters, then build the request as follows:

```python
DEFAULT_NUM_CTX = 4096
DEFAULT_NUM_PREDICT = 160
DEFAULT_KEEP_ALIVE = "10m"

self.num_predict = num_predict or int(os.getenv("OLLAMA_NUM_PREDICT", DEFAULT_NUM_PREDICT))
self.keep_alive = keep_alive or os.getenv("OLLAMA_KEEP_ALIVE", DEFAULT_KEEP_ALIVE)

"options": {
    "num_ctx": self.num_ctx,
    "num_predict": self.num_predict,
    "temperature": 0,
},
"keep_alive": self.keep_alive,
```

- [ ] **Step 4: Add and test Ollama timing logging**

Extend `FakeResponse.json()` with duration/token fields, capture the logger with `caplog`, and assert the successful call logs total/load/prompt/eval milliseconds and token counts. Implement a private logging helper that treats missing fields as zero so older Ollama responses remain valid.

- [ ] **Step 5: Run focused and full tests**

Run: `$env:PYTHONPATH='src'; python -m pytest tests/test_vlm_context.py tests/test_model_config.py -q`

Run: `$env:PYTHONPATH='src'; python -m pytest tests/ -q`

Expected: all tests PASS.

- [ ] **Step 6: Commit Task 1**

```powershell
git add tests/test_vlm_context.py tests/test_model_config.py src/camera_ai/vlm/ollama_qwen.py
git commit -m "perf: optimize ollama qwen request"
```

### Task 2: Bound repeated detector text

**Files:**
- Modify: `tests/test_vlm_context.py`
- Modify: `src/camera_ai/vlm/ollama_qwen.py`
- Modify: `.env.example`
- Modify: `README.md`

**Interfaces:**
- Consumes: `list[Detection]` passed to the analyzer.
- Produces: `_format_detections(detections: list[Detection]) -> str` used only for prompt construction.

- [ ] **Step 1: Write a failing compaction test**

Create more than three detections for several labels, call `_format_detections`, and assert that output is sorted by first-seen label, keeps the three highest confidences for each label, contains at most twelve lines, and returns `- none` for an empty list.

```python
lines = OllamaQwenAnalyzer._format_detections(detections).splitlines()
assert len(lines) <= 12
assert sum("person" in line for line in lines) == 3
assert "conf=0.95" in "\n".join(lines)
assert OllamaQwenAnalyzer._format_detections([]) == "- none"
```

- [ ] **Step 2: Run the test and verify RED**

Run: `$env:PYTHONPATH='src'; python -m pytest tests/test_vlm_context.py -q`

Expected: FAIL because `_format_detections` does not exist.

- [ ] **Step 3: Implement deterministic compaction**

Group detections in insertion-ordered dictionaries, sort each group by descending confidence, take three per label, then stop after twelve formatted lines. Replace inline prompt formatting with this helper.

- [ ] **Step 4: Document configuration and run all verification**

Document `OLLAMA_NUM_CTX=4096`, `OLLAMA_NUM_PREDICT=160`, and `OLLAMA_KEEP_ALIVE=10m` in `.env.example` and README.

Run: `$env:PYTHONPATH='src'; python -m pytest tests/ -q`

Run: `python -m compileall src apps tests`

Run: `git diff --check`

Expected: tests PASS and both static checks exit 0.

- [ ] **Step 5: Benchmark and commit Task 2**

Restart the application or make one four-frame request, then run `ollama ps`. Record cold and warm `qwen_ms`; confirm `PROCESSOR` reports `100% GPU` with context 4096. Do not make elapsed time a test assertion.

```powershell
git add tests/test_vlm_context.py src/camera_ai/vlm/ollama_qwen.py .env.example README.md
git commit -m "perf: compact qwen detector prompt"
```

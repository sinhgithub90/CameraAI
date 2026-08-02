# Compact Analysis Output Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Remove the unused demo UI and expose a compact, typed analysis and benchmark JSON contract without changing inference behavior.

**Architecture:** Keep rich `VideoAnalysis` objects inside `AnalysisStore`, then map them to dedicated compact Pydantic response models at the API boundary. The benchmark consumes that public shape instead of private `event_metadata`, while UI files and source-string tests are deleted.

**Tech Stack:** Python 3.12, Pydantic, FastAPI, pytest.

## Global Constraints

- Do not change Motion, Detection, routing, Qwen, cooldown, episode, or queue behavior.
- Keep `POST /async/analyze/video` and `GET /analyses/{analysis_id}` URLs.
- Remove `GET /` and the static demo UI completely.
- Public window JSON keeps time range, alert level, compact Qwen state, compact cooldown state, and timing.
- Public JSON excludes internal detections, Qwen inputs, candidates, decisions, traces, raw alerts, risks, actions, and `event_metadata`.
- Omit fields whose value is `None`; omit false episode transition flags.
- Retain behavior tests; delete only UI source tests and redundant exact-shape benchmark assertions.

---

### Task 1: Add a typed compact analysis response

**Files:**
- Modify: `src/camera_ai/analysis_store.py`
- Modify: `apps/api/main.py`
- Modify: `tests/test_analysis_endpoint.py`

**Interfaces:**
- Consumes: internal `VideoAnalysis`, `VideoWindowResult`, and their `event_metadata`.
- Produces: `CompactVideoAnalysis.from_analysis(analysis: VideoAnalysis) -> CompactVideoAnalysis` and the compact `GET /analyses/{analysis_id}` response.

- [ ] **Step 1: Write a failing endpoint serialization test**

Create an internal window with detections, Qwen input, alert context, and VLM call metadata. Call `get_analysis` and assert its serialized public model contains `alert_level`, compact `qwen`, compact `cooldown`, and timing, while these keys are absent:

```python
serialized = response.model_dump(mode="json", exclude_none=True)
window = serialized["windows"][0]
assert window["qwen"]["reason"] == "active_alert_cooldown"
assert window["qwen"]["verified"] is False
assert window["cooldown"]["active_alert_id"] == "episode-1"
assert "detections" not in window
assert "qwen_input" not in window
assert "event_metadata" not in window
assert "security" not in window
assert "vlm" not in window
```

- [ ] **Step 2: Run the endpoint test and verify it fails**

Run: `$env:PYTHONPATH='src'; python -m pytest tests/test_analysis_endpoint.py -q`

Expected: the current endpoint returns `VideoAnalysis`, so internal fields remain and compact fields are missing.

- [ ] **Step 3: Implement compact Pydantic response models**

Add models with these fields to `analysis_store.py`:

```python
class CompactWindowQwen(BaseModel):
    status: str
    summary: str
    degraded: bool = False
    verified: bool = False
    reason: str | None = None

class CompactWindowCooldown(BaseModel):
    active_alert_id: str | None = None
    next_recheck_seconds: float | None = None
    recheck: bool = False
    episode_created: bool = False
    episode_extended: bool = False
    episode_resolved: bool = False

class CompactVideoWindow(BaseModel):
    window_index: int
    start_seconds: float
    end_seconds: float
    alert_level: AlertLevel
    qwen: CompactWindowQwen
    cooldown: CompactWindowCooldown | None = None
    timing: StageTiming

class CompactVideoAnalysis(BaseModel):
    id: str
    camera_id: str
    status: str
    error: str | None = None
    total_timing: StageTiming
    windows: list[CompactVideoWindow]
```

Add the classmethod signature
`from_analysis(cls, analysis: VideoAnalysis) -> CompactVideoAnalysis`. The
mapper reads `vlm_call.reason` and `alert_context`, sets verified only for
`verification_status == "verified"`, and creates `cooldown` only when an active
alert, recheck, or episode transition exists.

- [ ] **Step 4: Return the compact model from the endpoint**

Change the endpoint signature to:

```python
@app.get(
    "/analyses/{analysis_id}",
    response_model=CompactVideoAnalysis,
    response_model_exclude_none=True,
    response_model_exclude_defaults=True,
)
async def get_analysis(analysis_id: str) -> CompactVideoAnalysis:
    analysis = await analysis_store.get(analysis_id)
    if analysis is None:
        raise HTTPException(status_code=404, detail="analysis not found")
    return CompactVideoAnalysis.from_analysis(analysis)
```

- [ ] **Step 5: Run endpoint and persistence tests**

Run: `$env:PYTHONPATH='src'; python -m pytest tests/test_analysis_endpoint.py tests/test_analysis_store.py -q`

Expected: all tests pass.

- [ ] **Step 6: Commit**

```powershell
git add src/camera_ai/analysis_store.py apps/api/main.py tests/test_analysis_endpoint.py
git commit -m "feat: expose compact video analysis JSON"
```

---

### Task 2: Remove demo UI and its tests

**Files:**
- Delete: `apps/api/static/index.html`
- Delete: `tests/test_async_video_ui.py`
- Modify: `apps/api/main.py`
- Modify: `tests/test_analysis_endpoint.py`

**Interfaces:**
- Consumes: FastAPI application routes.
- Produces: API-only service with no root HTML route.

- [ ] **Step 1: Add a failing route contract test**

```python
def test_api_has_no_demo_root_route():
    paths = {route.path for route in main.app.routes}
    assert "/" not in paths
```

- [ ] **Step 2: Run the route test and verify it fails**

Run: `$env:PYTHONPATH='src'; python -m pytest tests/test_analysis_endpoint.py::test_api_has_no_demo_root_route -q`

Expected: FAIL because `GET /` still exists.

- [ ] **Step 3: Remove UI code and files**

Remove `HTMLResponse`, `Path`, `UI_FILE`, and the `index` handler from
`apps/api/main.py`. Delete the HTML file and `tests/test_async_video_ui.py`.

- [ ] **Step 4: Run API tests**

Run: `$env:PYTHONPATH='src'; python -m pytest tests/test_analysis_endpoint.py tests/test_async_video_response.py -q`

Expected: all tests pass.

- [ ] **Step 5: Commit**

```powershell
git add -A apps/api/static/index.html tests/test_async_video_ui.py apps/api/main.py tests/test_analysis_endpoint.py
git commit -m "refactor: remove unused demo UI"
```

---

### Task 3: Consume compact analysis JSON in benchmark reports

**Files:**
- Modify: `scripts/benchmark_pipeline.py`
- Modify: `tests/test_benchmark_cli.py`
- Modify: `README.md`
- Modify: `docs/benchmarks/README.md`

**Interfaces:**
- Consumes: compact window fields `alert_level`, `qwen`, `cooldown`, and `timing`.
- Produces: compact per-video JSON plus unchanged cooldown/performance counters.

- [ ] **Step 1: Replace redundant shape fixtures with one compact payload test**

Use a compact payload that contains one verified red window and one suppressed
window. Assert the report omits `candidate_type` when absent, preserves Qwen
reason/verification, and keeps cooldown metrics:

```python
assert set(report["windows"][0]) == {
    "window_index", "start_seconds", "end_seconds",
    "alert_level", "qwen", "cooldown", "timing",
}
assert report["windows"][1]["qwen"]["reason"] == "active_alert_cooldown"
assert report["performance_summary"]["vlm_suppressed_by_cooldown"] == 1
```

Delete old exact dictionaries that separately verify legacy internal
`event_metadata`, candidate selection, and duplicate timing shape.

- [ ] **Step 2: Run benchmark tests and verify compact payload fails**

Run: `$env:PYTHONPATH='src'; python -m pytest tests/test_benchmark_cli.py -q`

Expected: FAIL because `build_video_report` still reads `event_metadata`.

- [ ] **Step 3: Map compact fields directly**

In `build_video_report`, read `window["alert_level"]`, `window["qwen"]`, and
`window.get("cooldown", {})`. Derive `called` from
`qwen.status == "completed"`, count episode flags from `cooldown`, omit
`candidate_type` when absent, and build nested dictionaries without `None`
values.

- [ ] **Step 4: Update docs to describe the compact API and removed UI**

Remove demo UI instructions and detailed internal window fields. Document the
compact analysis and benchmark shapes and state that false/empty cooldown
transition fields are omitted.

- [ ] **Step 5: Run benchmark and full regression tests**

Run:

```powershell
$env:PYTHONPATH='src'
python -m pytest tests/test_benchmark_cli.py tests/test_benchmark_metrics.py -q
python -m pytest -q
```

Expected: all tests pass.

- [ ] **Step 6: Commit**

```powershell
git add scripts/benchmark_pipeline.py tests/test_benchmark_cli.py README.md docs/benchmarks/README.md
git commit -m "refactor: compact benchmark JSON contract"
```

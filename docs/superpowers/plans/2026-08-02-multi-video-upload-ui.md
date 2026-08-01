# Multi-Video Upload UI Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let the demo UI submit 2–20 videos as one asynchronous batch and monitor each independent analysis.

**Architecture:** Extend the existing single-file page with a batch-only list and a shared polling loop. The batch UI calls the already implemented `/async/analyze/videos` endpoint and reuses the existing video-window renderer for the selected analysis.

**Tech Stack:** Static HTML/CSS/vanilla JavaScript, FastAPI multipart API, pytest.

## Global Constraints

- One selected file preserves the current image/video and sync/async behavior.
- Two or more files automatically use batch async mode and must all be videos.
- Submit one multipart `files` entry per video to `/async/analyze/videos`; do not send `camera_id`.
- Maximum browser batch size is 20 files.
- Use one polling timer for all batch analysis IDs; polling one failure must not stop the others.
- Do not modify the batch API, core pipeline, queueing, RabbitMQ, or analysis-store schema.

---

### Task 1: Batch UI Contract Tests

**Files:**
- Create: `tests/test_multi_video_ui.py`
- Create: `tests/ui_batch_harness.mjs`

**Interfaces:**
- Consumes: the page's inline JavaScript in a minimal Node DOM harness.
- Produces: executable browser-behavior tests for batch selection, multipart request creation, and shared analysis polling.

- [ ] **Step 1: Write failing static-contract tests**

Create `tests/test_multi_video_ui.py` which runs Node and asserts JSON emitted
by `tests/ui_batch_harness.mjs`:

```python
def test_batch_ui_posts_all_videos_and_renders_analysis_rows():
    result = subprocess.run(
        ["node", "tests/ui_batch_harness.mjs"],
        cwd=Path(__file__).resolve().parents[1],
        check=True,
        capture_output=True,
        text=True,
    )
    observed = json.loads(result.stdout)
    assert observed["endpoint"] == "/async/analyze/videos"
    assert observed["field_names"] == ["files", "files"]
    assert observed["row_count"] == 2
    assert observed["selected_analysis_id"] == "analysis-a"
```

The Node harness loads the inline script from `index.html`, provides the DOM
elements the script uses, supplies two `video/mp4` files, stubs `fetch`, then
executes the registered Analyze click handler. The production change that
should make this test fail is routing a multi-video selection to the one-file
endpoint or dropping one `files` multipart part.

- [ ] **Step 2: Run the tests to verify they fail**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_async_video_ui.py -q
```

Expected: FAIL because `setFiles` and batch submission do not exist.

- [ ] **Step 3: Leave production code unchanged**

This task intentionally stops after the failing contract. It defines the
browser-facing API for Task 2.

---

### Task 2: Batch Submission, Selection, and Shared Polling

**Files:**
- Modify: `apps/api/static/index.html:30-60,140-210,228-271,273-430`
- Test: `tests/test_multi_video_ui.py`

**Interfaces:**
- Consumes: `POST /async/analyze/videos` response `{ batch_id, items[] }`,
  where every item has `filename`, `camera_id`, and `analysis_id`; `GET
  /analyses/{analysis_id}` returns `status` and `windows`.
- Produces: `setFiles(files)`, `isBatch()`, `submitBatch(files)`,
  `renderBatchList()`, `selectBatchItem(analysisId)`, and
  `startBatchPoll(batch)` in the page script.

- [ ] **Step 1: Add batch markup and styling**

Add `multiple` to the file input. Add a hidden batch card above `#result`:

```html
<div class="card" id="batch-results" hidden>
  <h2>Phân tích nhiều video</h2>
  <p class="muted" id="batch-meta"></p>
  <div id="batch-list" class="batch-list"></div>
</div>
```

Add `.batch-list` and `.batch-item` styles matching the existing dark panels;
selected rows use the existing green accent and remain keyboard-focusable.

- [ ] **Step 2: Implement file-selection state**

Replace `currentFile` with `currentFiles`. `setFiles(files)` converts the
`FileList` to an array, rejects empty selection and enables the button. For
one file, retain the existing preview. For a batch, hide the preview and set
the drop-zone helper text to show the selected count. Both input change and
drop events call `setFiles` with all selected files.

```javascript
function isBatch() { return currentFiles.length > 1; }

function setFiles(files) {
  currentFiles = Array.from(files || []);
  analyzeBtn.disabled = currentFiles.length === 0;
  // Preserve current preview only when currentFiles.length === 1.
}
```

- [ ] **Step 3: Implement submit routing and validation**

In the click handler, branch before constructing the existing single-file
request:

```javascript
if (isBatch()) {
  if (!isAsync()) throw new Error('Nhiều video chỉ hỗ trợ chế độ Async.');
  if (currentFiles.length > 20) throw new Error('Tối đa 20 video mỗi lần gửi.');
  if (!currentFiles.every(isVideo)) throw new Error('Batch chỉ nhận file video.');
  const fd = new FormData();
  currentFiles.forEach(file => fd.append('files', file));
  const response = await fetch('/async/analyze/videos', { method: 'POST', body: fd });
  // Parse non-2xx using the current error path, then render and poll the batch.
}
```

For a single file, retain `file` and `camera_id` fields and the current
endpoint calculation unchanged.

- [ ] **Step 4: Render batch rows and reuse detail renderer**

Keep a `batchState` object keyed by `analysis_id`. `renderBatchList` produces
one button per item containing filename, camera ID, analysis status,
completed/total windows and highest observed alert level. The initial selected
item is the first item. `selectBatchItem` updates row selection then calls:

```javascript
renderVideoWindows({ media_type: 'video', video_windows: item.analysis.windows || [] }, []);
```

It updates the existing result title and badge for the selected item without
using the single-video `startVideoPoll`.

- [ ] **Step 5: Implement one batch poller**

`startBatchPoll(batch)` clears any old timer, then once per second fetches
`/analyses/{analysis_id}` sequentially for each active item. A fetch failure
sets that row's error state; the next item still runs. After every item has a
terminal status (`completed` or `failed`), call `clearPoll()`.

```javascript
for (const item of batch.items) {
  if (isTerminal(item.analysis?.status)) continue;
  const response = await fetch('/analyses/' + item.analysis_id);
  if (response.ok) item.analysis = await response.json();
  else item.pollError = 'Không lấy được trạng thái.';
}
```

- [ ] **Step 6: Run focused UI tests**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_multi_video_ui.py -q
```

Expected: PASS.

- [ ] **Step 7: Commit**

```powershell
git add apps/api/static/index.html tests/test_multi_video_ui.py tests/ui_batch_harness.mjs
git commit -m "feat: add multi-video upload demo UI"
```

---

### Task 3: Regression and Visual Verification

**Files:**
- Verify: `apps/api/static/index.html`
- Verify: `tests/`

**Interfaces:**
- Consumes: the completed static UI and FastAPI app.
- Produces: evidence that the UI syntax, test suite, and local interaction are valid.

- [ ] **Step 1: Run all tests**

```powershell
.\.venv\Scripts\python.exe -m pytest tests -q
```

Expected: all tests pass; Rabbit integration tests remain skipped unless their
environment is enabled.

- [ ] **Step 2: Check the page parses and serves**

Run:

```powershell
.\.venv\Scripts\python.exe -c "from apps.api.main import app; assert any(route.path == '/' for route in app.routes)"
```

Expected: exit code 0.

- [ ] **Step 3: Inspect the browser UI**

Start the local FastAPI app and open `/`. Select two videos, submit, verify two
rows appear, and select each row to ensure its detail area changes. Then select
one image and one video separately to verify the legacy route remains usable.

- [ ] **Step 4: Commit any verification-only correction**

If Step 3 requires a correction, add a failing test first, make the minimal
fix, rerun the focused and full suite, then commit it:

```powershell
git add apps/api/static/index.html tests/test_multi_video_ui.py tests/ui_batch_harness.mjs
git commit -m "fix: refine multi-video demo UI"
```

# Pipeline Documentation Refresh Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the three current-user documents accurately describe producer-side cooldown admission, queue pruning, compact JSON, and benchmark timing.

**Architecture:** Use one canonical pipeline description across README, detailed pipeline documentation, and benchmark documentation. Apply targeted edits: replace stale claims, add current state/queue behavior, and use one observed RoadAccidents010 report without rewriting unrelated setup or detector details.

**Tech Stack:** CommonMark Markdown, PowerShell verification, repository JSON benchmark output.

## Global Constraints

- Modify only `README.md`, `docs/camera-ai-pipeline.md`, and `docs/benchmarks/README.md` besides this plan.
- Do not change source code, tests, UI, architecture-roadmap documents, or benchmark JSON files.
- Describe uploaded-video time as source seconds and future live-camera time as monotonic.
- Treat the RoadAccidents010 timing as an observed run, not a performance guarantee.

---

### Task 1: Align the top-level README

**Files:**
- Modify: `README.md`

**Interfaces:**
- Consumes: canonical flow and JSON contract from `docs/superpowers/specs/2026-08-02-pipeline-documentation-refresh-design.md`.
- Produces: the short operator-facing description linked to the detailed documents.

- [ ] **Step 1: Replace the async flow diagram**

Show `read stream -> close five-second boundary -> pre-queue admission` before the global queue. Show the cooldown drop branch before Motion and the admitted branch as `queue -> Motion -> Detection -> Router -> Qwen`.

- [ ] **Step 2: Replace the stale cooldown paragraph**

State that suppressed windows are not persisted individually, one due recheck is reserved atomically, confirmed red prunes same-stream backlog, and a queue cutoff rejects stale late arrivals. Retain 60-second red, 15-second orange, low resolution, and HTTP 409 semantics.

- [ ] **Step 3: Update the benchmark JSON explanation**

Add the top-level `cooldown` fields and explain that call-rate denominators include processed plus suppressed windows while p95 excludes queue wait.

- [ ] **Step 4: Verify README consistency**

Run:

```powershell
rg -n "still written|still recorded|verification_status=suppressed|no cooldown|chưa có cooldown" README.md
```

Expected: no stale claim that suppressed windows remain in `windows`.

### Task 2: Correct the detailed pipeline document

**Files:**
- Modify: `docs/camera-ai-pipeline.md`

**Interfaces:**
- Consumes: README terminology from Task 1.
- Produces: implementation-level explanation of admission, reservations, processing, failure behavior, and output.

- [ ] **Step 1: Rewrite the current video-flow section**

Separate direct-construction defaults from FastAPI behavior. Document that FastAPI reads the complete upload, closes five-second source-time windows, and performs admission before persistence and queueing.

- [ ] **Step 2: Replace the obsolete Qwen decision table**

Use rows for active cooldown, due recheck, static admitted window, routed candidate, and no usable frames. State explicitly whether queue, Motion, YOLO, and Qwen run for each row.

- [ ] **Step 3: Add cooldown state and concurrency behavior**

Document atomic `recheck_reserved`, worker pruning by `(analysis_id, camera_id)`, cutoff race protection, processor defense in depth, bounded retry on failure, and isolation of other cameras.

- [ ] **Step 4: Correct output and timing sections**

State that compact async `windows` contains only processed windows; explain the top-level cooldown summary and distinguish `total_ms`, `queue_wait_ms`, and `wall_clock_ms`.

- [ ] **Step 5: Remove contradictions**

Run:

```powershell
rg -n "chưa có cooldown|hiện chỉ phân tích tối đa 5 giây|mỗi cửa sổ có motion tạo|video_windows cho các cửa sổ" docs/camera-ai-pipeline.md
```

Expected: obsolete statements are removed or qualified as synchronous/direct-construction behavior.

### Task 3: Update benchmark guidance and example

**Files:**
- Modify: `docs/benchmarks/README.md`

**Interfaces:**
- Consumes: compact JSON semantics from Tasks 1-2.
- Produces: reproducible interpretation rules for per-video reports.

- [ ] **Step 1: Correct cooldown accounting**

Replace “next 60 seconds are still recorded” with producer-side dropping and top-level aggregation. Explain that `len(windows)` can be smaller than the logical window count.

- [ ] **Step 2: Add compact cooldown JSON example**

Show all seven current fields with the observed RoadAccidents010 values: red starts at 10, recheck at 70, two suppressed windows, and ten suppressed seconds.

- [ ] **Step 3: Add observed-run interpretation**

Record two processed windows plus two pre-queue drops, Qwen call rate 0.5, queue wait 0 ms, and processing p95 about 3,794 ms. Label values as the 2026-08-02 local run.

- [ ] **Step 4: Clarify timing and denominator formulas**

Define logical windows as `len(windows) + cooldown.suppressed_windows`, Qwen call rate as called/logical, and cooldown suppression rate as suppressed/logical. Keep p95 based on processed windows.

### Task 4: Cross-document verification and commit

**Files:**
- Verify: `README.md`
- Verify: `docs/camera-ai-pipeline.md`
- Verify: `docs/benchmarks/README.md`

**Interfaces:**
- Consumes: all prior documentation edits.
- Produces: one internally consistent documentation set.

- [ ] **Step 1: Scan stale claims and field names**

Run:

```powershell
rg -n "still recorded|still written|chưa có cooldown|verification_status=suppressed" README.md docs/camera-ai-pipeline.md docs/benchmarks/README.md
rg -n "active_alert_id|alert_level|timebase|red_started|recheck_at|suppressed_windows|suppressed_seconds" README.md docs/camera-ai-pipeline.md docs/benchmarks/README.md
```

Expected: no stale behavior; every document points to the same top-level cooldown contract.

- [ ] **Step 2: Check Markdown paths and whitespace**

Run:

```powershell
Test-Path docs/camera-ai-pipeline.md
Test-Path docs/benchmarks/README.md
git diff --check
```

Expected: both paths are true and diff check prints nothing.

- [ ] **Step 3: Review the final diff and commit**

Run `git diff -- README.md docs/camera-ai-pipeline.md docs/benchmarks/README.md`, verify only the approved scope changed, then commit with:

```powershell
git add README.md docs/camera-ai-pipeline.md docs/benchmarks/README.md docs/superpowers/plans/2026-08-02-pipeline-documentation-refresh.md
git commit -m "docs: document prequeue cooldown pipeline"
```

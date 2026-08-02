# Current Pipeline Documentation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the public documentation accurately describe the merged pre-queue cooldown, in-process delivery lifecycle, multi-video API, dormant RabbitMQ adapters, and removal of the demo UI.

**Architecture:** Update the concise runtime guide in `README.md`, the implemented processing details in `docs/processing-plane-architecture.md`, and the current-versus-target boundary in `docs/camera-ai-system-architecture.md`. Do not change runtime code or reintroduce UI behavior.

**Tech Stack:** Markdown, FastAPI route names, CameraAI messaging contracts.

## Global Constraints

- The current API runtime remains in-process.
- RabbitMQ VLM transport remains dormant until `FrameStore` and a transport-safe job DTO exist.
- Red cooldown admission happens before Motion, Detection, and VLM queue creation.
- The demo has API endpoints only; no static UI is present.

---

### Task 1: Synchronize current documentation

**Files:**
- Modify: `README.md`
- Modify: `docs/processing-plane-architecture.md`
- Modify: `docs/camera-ai-system-architecture.md`

**Interfaces:**
- Consumes: current behavior in `apps/api/main.py`, `src/camera_ai/queue.py`, and `src/camera_ai/messaging/`.
- Produces: one consistent explanation of the current runtime and future transport boundary.

- [x] **Step 1: Update the README runtime contract**

Document `POST /async/analyze/videos`, API-only operation, pre-queue rejection, atomic backlog cutoff, and delivery finalization.

- [x] **Step 2: Update processing-plane implementation notes**

Add the concrete five-second admission flow, queue delivery lifecycle, pruning scope, and RabbitMQ limitation without rewriting target-architecture sections.

- [x] **Step 3: Update system architecture status**

Clearly separate the implemented single-process demo from the target multi-server architecture and state that there is no current UI.

- [x] **Step 4: Verify documentation consistency**

Run:

```powershell
rg -n "async/analyze/videos|pre-queue|ack|retry|reject|API-only|FrameStore" README.md docs/processing-plane-architecture.md docs/camera-ai-system-architecture.md
git diff --check
$env:PYTHONPATH = "src"
python -m pytest -q
```

Expected: required concepts are present, `git diff --check` exits 0, and the full test suite passes.

- [x] **Step 5: Commit**

```powershell
git add README.md docs/processing-plane-architecture.md docs/camera-ai-system-architecture.md docs/superpowers/plans/2026-08-02-current-pipeline-documentation.md
git commit -m "docs: sync merged video pipeline architecture"
```

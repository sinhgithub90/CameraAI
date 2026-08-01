# Multi-Video Upload UI Design

## Goal

Allow the existing demo page to submit and monitor 2–20 video files through
`POST /async/analyze/videos`, while preserving the current single-file image
and video workflows.

## Scope

- The file input accepts multiple selections and drag-and-drop accepts multiple
  files.
- Selecting one file keeps the current endpoint selection and detailed result
  rendering.
- Selecting two or more files enters batch mode automatically. Batch mode is
  async-video only, requires every selected file to be a video, and sends one
  multipart `files` field for each upload to `/async/analyze/videos`.
- The server derives batch camera IDs from filenames. The existing camera ID
  text input remains available for a single file and is marked as unused for a
  batch.
- Batch submission displays a compact, selectable list. Each row includes
  filename, derived camera ID, producer status, completed/total window count,
  and the greatest alert level observed so far.
- A single shared polling timer fetches `/analyses/{analysis_id}` for all batch
  items once per second. It stops after every item is `completed` or `failed`.
- Selecting a row renders that video's window detail with the existing
  `renderVideoWindows` renderer. The first item is selected initially.
- A failure while polling one item is recorded on that row and does not stop
  polling the remaining items.

## Out of Scope

- No changes to the batch API, backend queueing, VLM worker count, RabbitMQ,
  batch persistence, retry policy, or analysis-store schema.
- No concurrent browser upload progress bars, cancellation, or per-video
  preview grid.

## UI Structure

The page retains its dark dashboard styling. A new batch-result card appears
only in batch mode above the existing detailed-result area. It acts as a
compact control plane: click a row to choose the video whose five-second
windows appear below. This avoids rendering up to 20 complete window streams
at once.

## Error Handling

- More than 20 files, a mixed image/video batch, or sync mode with multiple
  files is rejected in the browser before a request is sent.
- A non-2xx batch response is shown in the existing form error area.
- A failed analysis is rendered as failed on its row; the shared poller keeps
  the other analysis IDs active.

## Verification

A Node-based DOM harness executes the page's actual JavaScript with two video
fixtures and asserts the multipart request, batch rows, and selected detail
state. The existing pytest suite remains green. A local browser check confirms
selection, batch submission and row-to-detail interaction.

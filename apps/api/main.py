"""FastAPI demo adapter for the camera-AI core module.

This layer only translates HTTP <-> core objects. All logic lives in camera_ai,
so the same pipeline can later be driven from a queue worker unchanged.

Run (from repo root):
    python -m uvicorn apps.api.main:app --reload

Env knobs:
    CAMERA_AI_VLM   "ollama" (default) | "mock"   — force the mock VLM
    OLLAMA_BASE_URL default http://localhost:11434
"""
from __future__ import annotations

import asyncio
import logging
import os
from pathlib import Path

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import HTMLResponse

from dotenv import load_dotenv

from camera_ai import SecurityAIPipeline
from camera_ai.schemas import EventObject, MediaType, PipelineResult
from camera_ai.vlm.mock import MockAnalyzer

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Load .env (repo root) before building the pipeline so env config applies.
load_dotenv()

UI_FILE = Path(__file__).resolve().parent / "static" / "index.html"


def _build_pipeline() -> SecurityAIPipeline:
    vlm_provider = os.getenv("CAMERA_AI_VLM", "ollama").strip().lower()
    if vlm_provider == "mock":
        logger.info("Using mock VLM (CAMERA_AI_VLM=mock)")
        return SecurityAIPipeline(vlm=MockAnalyzer())
    return SecurityAIPipeline()


pipeline = _build_pipeline()

app = FastAPI(title="Camera AI Demo", version="0.1.0")


@app.get("/", response_class=HTMLResponse)
async def index() -> str:
    return UI_FILE.read_text(encoding="utf-8")


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/analyze/image", response_model=PipelineResult)
async def analyze_image(
    file: UploadFile = File(...),
    camera_id: str = Form("unknown"),
) -> PipelineResult:
    content = await file.read()
    if not content:
        raise HTTPException(status_code=400, detail="empty upload")
    event = EventObject(camera_id=camera_id, image=content, media_type=MediaType.IMAGE)
    try:
        # pipeline.analyze_event is blocking (YOLO + VLM, ~5s) — run it off the
        # event loop so a slow request doesn't freeze every other endpoint.
        return await asyncio.to_thread(pipeline.analyze_event, event)
    except Exception as exc:
        logger.exception("image analysis failed")
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.post("/analyze/video", response_model=PipelineResult)
async def analyze_video(
    file: UploadFile = File(...),
    camera_id: str = Form("unknown"),
) -> PipelineResult:
    content = await file.read()
    if not content:
        raise HTTPException(status_code=400, detail="empty upload")
    event = EventObject(camera_id=camera_id, image=content, media_type=MediaType.VIDEO)
    try:
        return await asyncio.to_thread(pipeline.analyze_event, event)
    except Exception as exc:
        logger.exception("video analysis failed")
        raise HTTPException(status_code=500, detail=str(exc)) from exc

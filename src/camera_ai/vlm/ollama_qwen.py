"""Qwen-VL analyser backed by a local Ollama instance.

Uses Ollama's /api/chat endpoint with an image message, so it works with any
vision model Ollama can serve (e.g. qwen3-vl). The model is NOT loaded in this
process — Ollama owns it — which keeps RAM usage low. The analyser asks the
model for strict JSON so the pipeline can map it onto SceneAnalysis directly.
"""
from __future__ import annotations

import base64
import json
import logging
import os

import cv2
import numpy as np
import requests

from ..schemas import AlertLevel, Detection, SceneAnalysis
from .base import VLMAnalyzer

logger = logging.getLogger(__name__)

# Model + endpoint are overridable via env so a different Qwen (or any Ollama
# vision model) can be used without touching code.
DEFAULT_MODEL = os.getenv("OLLAMA_MODEL", "qwen3-vl:2b-instruct-q8_0")
DEFAULT_BASE_URL = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")
# Keep the model resident in VRAM so requests after a quiet period don't pay a
# reload + kernel-warmup penalty (which is what made the first call ~8-10s).
# Default 30m balances memory vs reload cost for machines that share the GPU;
# -1 keeps it loaded indefinitely (pin VRAM ~2.7GB). Override per-instance via
# OLLAMA_KEEP_ALIVE (e.g. "30m", 300, or -1).
DEFAULT_KEEP_ALIVE = "30m"

_PROMPT = (
    "Bạn là nhà phân tích camera an ninh. Hãy nhìn vào khung hình camera được đính kèm.\n"
    "Kết quả nhận diện đối tượng từ tầng detector:\n{detections}\n"
    "Trả lời bằng STRICT JSON, KHÔNG dùng markdown fences, đúng schema sau:\n"
    '{{"summary": "...", "observations": [...], '
    '"alert_level": "low"|"medium"|"high", "risks": [...], '
    '"recommended_action": "..."}}\n'
    "Quy tắc:\n"
    "- Tất cả giá trị text (summary, observations, risks, recommended_action) PHẢI viết bằng "
    "TIẾNG VIỆT, có dấu đầy đủ. Chỉ key JSON giữ nguyên tiếng Anh.\n"
    "- alert_level: low nếu cảnh bình thường, medium nếu đáng chú ý, high nếu nguy hiểm "
    "(cháy nổ, gây gổ, đánh lộn, xâm nhập, tai nạn, nghi vấn an ninh...).\n"
    "- observations: danh sách chuỗi ngắn, mỗi chuỗi mô tả một đối tượng/hiện tượng đáng chú ý.\n"
    "- risks: các rủi ro an ninh cụ thể (vd: \"phát_hiện_người\", \"nghi_chay_no\", "
    "\"xam_nhap\", \"ganh_go\").\n"
    "- recommended_action: hành động đề xuất ngắn gọn cho người trực."
)


class OllamaQwenAnalyzer(VLMAnalyzer):
    def __init__(
        self,
        model: str | None = None,
        base_url: str | None = None,
        timeout: float = 120.0,
        keep_alive: int | str | None = None,
    ) -> None:
        self.model = model or os.getenv("OLLAMA_MODEL") or DEFAULT_MODEL
        self.base_url = (
            base_url or os.getenv("OLLAMA_BASE_URL") or DEFAULT_BASE_URL
        ).rstrip("/")
        self.timeout = timeout
        self.keep_alive = keep_alive if keep_alive is not None else (
            os.getenv("OLLAMA_KEEP_ALIVE") or DEFAULT_KEEP_ALIVE
        )

    def analyze(self, frame: np.ndarray, detections: list[Detection]) -> SceneAnalysis:
        det_lines = "\n".join(
            f"- {d.label} (conf={d.confidence:.2f}, bbox={[round(v) for v in d.bbox]})"
            for d in detections
        ) or "- none"
        prompt = _PROMPT.format(detections=det_lines)

        payload = {
            "model": self.model,
            "messages": [
                {
                    "role": "user",
                    "content": prompt,
                    "images": [self._frame_to_jpeg_b64(frame)],
                }
            ],
            "stream": False,
            "keep_alive": self.keep_alive,
        }
        try:
            resp = requests.post(
                f"{self.base_url}/api/chat",
                json=payload,
                timeout=self.timeout,
            )
            resp.raise_for_status()
        except requests.RequestException as exc:
            # Ollama down / unreachable — degrade instead of failing the request.
            logger.warning("Ollama unreachable (%s); returning degraded result", exc)
            return SceneAnalysis(
                summary=f"Không kết nối được Ollama ({self.model}): {exc}. "
                        "Kết quả này không có phân tích VLM.",
                observations=[f"phát hiện {d.label}" for d in detections],
                alert_level=AlertLevel.MEDIUM if detections else AlertLevel.LOW,
                risks=["vlm_khong_kha_dung", *(f"{d.label}_phat_hien" for d in detections)],
                recommended_action="kiem_tra_dich_vu_ollama",
                degraded=True,
            )
        content = resp.json()["message"]["content"]
        return self._parse(content)

    @staticmethod
    def _frame_to_jpeg_b64(frame: np.ndarray) -> str:
        ok, buf = cv2.imencode(".jpg", frame, [int(cv2.IMWRITE_JPEG_QUALITY), 85])
        if not ok:
            raise RuntimeError("failed to encode frame to JPEG")
        return base64.b64encode(buf.tobytes()).decode("ascii")

    @classmethod
    def _parse(cls, content: str) -> SceneAnalysis:
        data = cls._extract_json(content)
        if data is None:
            # Model refused / returned prose instead of JSON — degrade gracefully.
            logger.warning("VLM did not return JSON; treating text as summary: %r", content[:200])
            return SceneAnalysis(summary=content.strip())
        return SceneAnalysis(
            summary=str(data.get("summary", "")),
            observations=cls._normalize_strings(data.get("observations", [])),
            alert_level=cls._coerce_alert(data.get("alert_level")),
            risks=cls._normalize_strings(data.get("risks", [])),
            recommended_action=str(data.get("recommended_action", "")),
        )

    @staticmethod
    def _normalize_strings(values: object) -> list[str]:
        """Coerce a list of strings/dicts/numbers into plain strings.

        LLMs are unreliable about types: they may return observations as
        [{object, confidence, description}, ...] instead of ["...", ...]. We
        flatten either shape so the API contract stays list[str].
        """
        result: list[str] = []
        for item in values or []:
            if isinstance(item, str):
                result.append(item)
            elif isinstance(item, dict):
                result.append("; ".join(f"{k}: {v}" for k, v in item.items()))
            else:
                result.append(str(item))
        return result

    @staticmethod
    def _coerce_alert(value: object) -> AlertLevel:
        try:
            return AlertLevel(str(value).lower())
        except ValueError:
            logger.warning("Unknown alert_level from VLM: %r; defaulting to medium", value)
            return AlertLevel.MEDIUM

    @staticmethod
    def _extract_json(content: str) -> dict | None:
        content = content.strip()
        # Strip markdown code fences if the model wrapped the JSON in them.
        if content.startswith("```"):
            content = content.strip("`")
            if content.startswith("json"):
                content = content[4:]
        start, end = content.find("{"), content.rfind("}")
        if start == -1 or end == -1:
            return None
        try:
            return json.loads(content[start : end + 1])
        except json.JSONDecodeError:
            logger.warning("VLM JSON could not be decoded: %r", content[start : end + 1][:200])
            return None

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
import re
from collections.abc import Sequence

import cv2
import numpy as np
import requests

from ..schemas import AlertLevel, Detection, SceneAnalysis, VLMAnalysisTrace
from .base import VLMAnalyzer

logger = logging.getLogger(__name__)

# Model + endpoint are overridable via env so a different Qwen (or any Ollama
# vision model) can be used without touching code.
DEFAULT_MODEL = "qwen3-vl:4b-instruct-q4_K_M"
DEFAULT_BASE_URL = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")
DEFAULT_NUM_CTX = 4096
DEFAULT_NUM_PREDICT = 128
DEFAULT_KEEP_ALIVE = "10m"
DEFAULT_FRAME_MODE = "composite"
VALID_FRAME_MODES = {"composite", "separate"}
PANEL_WIDTH = 960
PANEL_HEIGHT = 540

_PROMPT = (
    "Phân tích ảnh camera và trả về đúng JSON bằng tiếng Việt.\n"
    "- summary: bắt buộc, 1–2 câu mô tả cảnh và diễn biến quan sát được.\n"
    "- alert_level: low nếu bình thường, medium nếu đáng chú ý, high nếu nguy hiểm.\n"
    "- risks: mảng các rủi ro quan sát được; nếu không có, trả []. Không bịa rủi ro.\n"
    "- recommended_action: bắt buộc, hành động ngắn phù hợp mức cảnh báo. "
    "Nếu low và không có rủi ro, dùng chính xác: Tiếp tục giám sát.\n"
    "Dữ liệu YOLO:\n{detections}"
)

_CANDIDATE_PROMPT = (
    "Xác minh nghi vấn camera và trả về đúng JSON bằng tiếng Việt.\n"
    "- decision: đúng một trong yes | no | uncertain. Không đủ bằng chứng thì "
    "chọn uncertain.\n"
    "- summary: bắt buộc, đúng một câu ngắn, mục tiêu không quá 20 từ.\n"
    "Nghi vấn: {candidate_type}.\n"
    "Bằng chứng router: {candidate_evidence}.\n"
    "Dữ liệu YOLO:\n{detections}"
)

_OUTPUT_SCHEMA = {
    "type": "object",
    "properties": {
        "alert_level": {
            "type": "string",
            "enum": ["low", "medium", "high"],
        },
        "summary": {"type": "string", "minLength": 1},
        "risks": {"type": "array", "items": {"type": "string"}},
        "recommended_action": {"type": "string"},
    },
    "required": ["alert_level", "summary", "risks", "recommended_action"],
    "additionalProperties": False,
}

_CANDIDATE_OUTPUT_SCHEMA = {
    "type": "object",
    "properties": {
        "decision": {"type": "string", "enum": ["yes", "no", "uncertain"]},
        "summary": {"type": "string", "minLength": 1},
    },
    "required": ["decision", "summary"],
    "additionalProperties": False,
}


class OllamaQwenAnalyzer(VLMAnalyzer):
    def __init__(
        self,
        model: str | None = None,
        base_url: str | None = None,
        timeout: float = 120.0,
        num_ctx: int | None = None,
        num_predict: int | None = None,
        keep_alive: str | None = None,
        frame_mode: str | None = None,
    ) -> None:
        self.model = model or os.getenv("OLLAMA_MODEL") or DEFAULT_MODEL
        self.base_url = (
            base_url or os.getenv("OLLAMA_BASE_URL") or DEFAULT_BASE_URL
        ).rstrip("/")
        self.timeout = timeout
        self.num_ctx = num_ctx or int(os.getenv("OLLAMA_NUM_CTX", DEFAULT_NUM_CTX))
        self.num_predict = num_predict or int(
            os.getenv("OLLAMA_NUM_PREDICT", DEFAULT_NUM_PREDICT)
        )
        self.keep_alive = self._normalize_keep_alive(
            keep_alive or os.getenv("OLLAMA_KEEP_ALIVE") or DEFAULT_KEEP_ALIVE
        )
        self.frame_mode = (
            frame_mode or os.getenv("OLLAMA_FRAME_MODE") or DEFAULT_FRAME_MODE
        ).strip().lower()
        if self.frame_mode not in VALID_FRAME_MODES:
            raise ValueError(
                "OLLAMA_FRAME_MODE must be 'composite' or 'separate'"
            )

    @staticmethod
    def _normalize_keep_alive(value: str) -> int | str:
        """Ollama rejects keep_alive values without a time unit.

        A string like "-1" or "300" fails Go's time.ParseDuration with
        "missing unit in duration". Send bare integers as seconds instead
        (int -1 = keep loaded forever) and leave duration strings alone.
        """
        try:
            return int(value)
        except (TypeError, ValueError):
            return str(value)

    def analyze(self, frame: np.ndarray, detections: list[Detection]) -> SceneAnalysis:
        return self._analyze_frames_trace([frame], detections).scene

    def analyze_sequence(
        self,
        frames: Sequence[np.ndarray],
        detections: list[Detection],
    ) -> SceneAnalysis:
        if not frames:
            raise ValueError("at least one frame is required")
        return self._analyze_frames_trace(frames, detections).scene

    def analyze_with_trace(
        self,
        frames: Sequence[np.ndarray],
        detections: list[Detection],
        *,
        candidate=None,
    ) -> VLMAnalysisTrace:
        if not frames:
            raise ValueError("at least one frame is required")
        return self._analyze_frames_trace(frames, detections, candidate=candidate)

    def _analyze_frames_trace(
        self,
        frames: Sequence[np.ndarray],
        detections: list[Detection],
        *,
        candidate=None,
    ) -> VLMAnalysisTrace:
        det_lines = self._format_detections(detections)
        if candidate is not None:
            prompt = _CANDIDATE_PROMPT.format(
                candidate_type=candidate.candidate_type,
                candidate_evidence=json.dumps(
                    candidate.evidence, ensure_ascii=False
                ),
                detections=det_lines,
            )
            output_schema = _CANDIDATE_OUTPUT_SCHEMA
        else:
            prompt = _PROMPT.format(detections=det_lines)
            output_schema = _OUTPUT_SCHEMA
        prepared_images = self._prepare_images(frames)
        is_composite = self.frame_mode == "composite" and len(frames) == 2
        if is_composite:
            prompt += (
                "\nẢnh ghép theo thời gian: nửa trên là TRƯỚC, "
                "nửa dưới là SAU. Hãy xét thay đổi giữa hai nửa."
            )
        elif len(frames) > 1:
            prompt += "\nCác ảnh theo thứ tự thời gian; hãy xét thay đổi giữa chúng."

        composite_shape = "none"
        if is_composite:
            height, width = prepared_images[0].shape[:2]
            composite_shape = f"{width}x{height}"
        logger.info(
            "[qwen-input] frame_mode=%s source_frames=%s sent_images=%s "
            "composite_shape=%s",
            self.frame_mode,
            len(frames),
            len(prepared_images),
            composite_shape,
        )

        payload = {
            "model": self.model,
            "messages": [
                {
                    "role": "user",
                    "content": prompt,
                    "images": [
                        self._frame_to_jpeg_b64(frame)
                        for frame in prepared_images
                    ],
                }
            ],
            "options": {
                "num_ctx": self.num_ctx,
                "num_predict": self.num_predict,
                "temperature": 0,
            },
            "format": output_schema,
            "keep_alive": self.keep_alive,
            "stream": False,
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
            detail = getattr(getattr(exc, "response", None), "text", "")
            logger.warning(
                "Ollama request failed (%s%s); returning degraded result",
                exc,
                f" response={detail[:500]}" if detail else "",
            )
            scene = (
                self._candidate_scene(
                    candidate,
                    decision="uncertain",
                    summary=f"Không kết nối được Ollama ({self.model}).",
                    degraded=True,
                )
                if candidate is not None
                else SceneAnalysis(
                    summary=f"Không kết nối được Ollama ({self.model}): {exc}. "
                    "Kết quả này không có phân tích VLM.",
                    observations=[f"phát hiện {d.label}" for d in detections],
                    alert_level=AlertLevel.MEDIUM if detections else AlertLevel.LOW,
                    risks=["vlm_khong_kha_dung", *(f"{d.label}_phat_hien" for d in detections)],
                    recommended_action="kiem_tra_dich_vu_ollama",
                    degraded=True,
                )
            )
            return VLMAnalysisTrace(
                scene=scene,
                prompt=prompt,
                raw_output_valid=False,
                decision="uncertain",
            )
        response_data = resp.json()
        self._log_ollama_timing(response_data)
        content = response_data["message"]["content"]
        data = self._extract_json(content)
        valid = bool(
            data is not None
            and all(field in data for field in output_schema["required"])
            and str(data.get("summary", "")).strip()
            and (
                candidate is None
                or data.get("decision") in {"yes", "no", "uncertain"}
            )
        )
        if candidate is not None:
            decision = str(data["decision"]) if valid and data else "uncertain"
            summary = (
                str(data["summary"]).strip()
                if valid and data
                else "VLM trả JSON chưa hoàn chỉnh."
            )
            scene = self._candidate_scene(
                candidate,
                decision=decision,
                summary=summary,
                degraded=not valid,
            )
        else:
            scene = self._parse(content)
            if not valid:
                scene = scene.model_copy(update={"degraded": True})
            decision = "uncertain"
        return VLMAnalysisTrace(
            scene=scene,
            prompt=prompt,
            raw_output=content,
            raw_output_valid=valid,
            decision=decision,
            event_type=candidate.candidate_type if candidate is not None else None,
            evidence=[],
        )

    @staticmethod
    def _candidate_scene(candidate, *, decision: str, summary: str, degraded: bool) -> SceneAnalysis:
        if decision == "yes":
            priority = candidate.priority.value
            alert_level = (
                AlertLevel.HIGH
                if priority in {"high", "critical"}
                else AlertLevel.MEDIUM
                if priority == "medium"
                else AlertLevel.LOW
            )
            action = "Kiểm tra sự kiện trên camera."
        elif decision == "no":
            alert_level = AlertLevel.LOW
            action = "Tiếp tục giám sát."
        else:
            alert_level = AlertLevel.LOW
            action = "Kiểm tra lại hình ảnh."
        return SceneAnalysis(
            summary=summary,
            alert_level=alert_level,
            risks=[],
            recommended_action=action,
            degraded=degraded,
        )

    @staticmethod
    def _format_detections(detections: list[Detection]) -> str:
        grouped: dict[str, list[Detection]] = {}
        for detection in detections:
            grouped.setdefault(detection.label, []).append(detection)

        return "\n".join(
            f"- {label}: count={len(items)}, "
            f"max_conf={max(item.confidence for item in items):.2f}"
            for label, items in grouped.items()
        ) or "- none"

    def _prepare_images(
        self,
        frames: Sequence[np.ndarray],
    ) -> list[np.ndarray]:
        if self.frame_mode == "composite" and len(frames) == 2:
            return [self._compose_two_frames(frames)]
        return list(frames)

    @classmethod
    def _compose_two_frames(
        cls,
        frames: Sequence[np.ndarray],
    ) -> np.ndarray:
        if len(frames) != 2:
            raise ValueError("exactly two frames are required for a composite")
        return np.vstack(
            (
                cls._fit_panel(frames[0], "TRUOC"),
                cls._fit_panel(frames[1], "SAU"),
            )
        )

    @staticmethod
    def _fit_panel(frame: np.ndarray, label: str) -> np.ndarray:
        height, width = frame.shape[:2]
        scale = min(PANEL_WIDTH / width, PANEL_HEIGHT / height, 1.0)
        resized_width = max(1, round(width * scale))
        resized_height = max(1, round(height * scale))
        if (resized_width, resized_height) == (width, height):
            fitted = frame
        else:
            fitted = cv2.resize(
                frame,
                (resized_width, resized_height),
                interpolation=cv2.INTER_LINEAR,
            )

        panel = np.zeros((PANEL_HEIGHT, PANEL_WIDTH, 3), dtype=np.uint8)
        offset_x = (PANEL_WIDTH - resized_width) // 2
        offset_y = (PANEL_HEIGHT - resized_height) // 2
        panel[
            offset_y : offset_y + resized_height,
            offset_x : offset_x + resized_width,
        ] = fitted
        cv2.rectangle(panel, (0, 0), (150, 44), (0, 0, 0), -1)
        cv2.putText(
            panel,
            label,
            (10, 32),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.9,
            (255, 255, 255),
            2,
            cv2.LINE_AA,
        )
        return panel

    @staticmethod
    def _log_ollama_timing(response_data: dict) -> None:
        def duration_ms(field: str) -> float:
            return float(response_data.get(field, 0) or 0) / 1_000_000

        logger.info(
            "[ollama] total_ms=%.1f load_ms=%.1f prompt_tokens=%s "
            "prompt_ms=%.1f output_tokens=%s output_ms=%.1f",
            duration_ms("total_duration"),
            duration_ms("load_duration"),
            response_data.get("prompt_eval_count", 0),
            duration_ms("prompt_eval_duration"),
            response_data.get("eval_count", 0),
            duration_ms("eval_duration"),
        )

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
            level = re.search(r'"alert_level"\s*:\s*"([^"]+)"', content)
            summary = re.search(r'"summary"\s*:\s*"([^"]*)"', content)
            if level or summary:
                logger.warning("VLM returned truncated JSON: %r", content[:200])
                return SceneAnalysis(
                    summary=summary.group(1) if summary and summary.group(1) else "VLM trả JSON chưa hoàn chỉnh.",
                    alert_level=cls._coerce_alert(level.group(1) if level else "medium"),
                    risks=["vlm_truncated_response"],
                    recommended_action="kiểm tra lại kết quả VLM",
                    degraded=True,
                )
            logger.warning("VLM did not return JSON; treating text as summary: %r", content[:200])
            return SceneAnalysis(summary=content.strip(), degraded=True)
        summary = str(data.get("summary", ""))
        if not summary.strip():
            logger.warning("VLM returned an empty summary: %r", content[:200])
            scene = SceneAnalysis(
                summary="VLM không trả nội dung phân tích cho window này.",
                alert_level=cls._coerce_alert(data.get("alert_level")),
                risks=["vlm_empty_response"],
                recommended_action="kiểm tra lại kết quả VLM",
                degraded=True,
            )
            return scene
        raw_observations = data.get("observations")
        observations = (
            cls._normalize_strings(raw_observations)
            if raw_observations is not None
            else ([summary] if summary else [])
        )
        return SceneAnalysis(
            summary=summary,
            observations=observations,
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

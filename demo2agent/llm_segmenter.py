from __future__ import annotations

import base64
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union

import cv2

from demo2agent.executor_specs import get_executor_specs
from demo2agent.llm_json import JSONCallConfig, LLMJsonCaller


@dataclass
class SegmenterConfig:
    model: str = "gpt-5.2"

    # Sampling: prefer sample_fps; else sample_every_s
    sample_fps: Optional[float] = 0.5
    sample_every_s: float = 2.0
    max_frames: int = 36

    # Image constraints
    max_w: int = 900
    jpeg_quality: int = 60

    # Back-compat CLI fields
    image_max_w: Optional[int] = None
    image_detail: Optional[str] = None  # ignored

    target_min_segments: int = 4
    target_max_segments: int = 12

    def __post_init__(self) -> None:
        if self.image_max_w is not None:
            self.max_w = int(self.image_max_w)


SYSTEM_VISUAL = """You segment a screen recording into high-level task chunks.

You will be given:
- A sequence of sampled frames from a screen recording (each with a timestamp).
- Optional user text (narration/typed hints).

Phase 1 goals:
- Identify where the user's intent changes or the interaction surface changes.
- DO NOT think about automation executors.
- DO NOT over-segment into micro-actions.
- Prefer 4–12 segments for a ~3 minute workflow.

Return ONLY JSON with schema provided in response_format.
No markdown. No extra keys.
"""


def _executor_catalog_text() -> str:
    specs = get_executor_specs()
    lines: List[str] = []
    for s in specs:
        lines.append(f"- {s.key}: supports {s.supports_step_types}")
        lines.append(f"  required_inputs={s.inputs_required}")
        if s.inputs_optional:
            lines.append(f"  optional_inputs={s.inputs_optional}")
        for n in s.inputs_notes:
            lines.append(f"  note: {n}")
        lines.append(f"  suggested_limits: max_actions<={s.max_actions_hint}, max_seconds<={s.max_seconds_hint}")
        lines.append("")
    return "\n".join(lines).strip()


SYSTEM_ALIGN = """You align and merge visual task segments into executor-sized segments for automation.

You will receive:
- Visual segments (t_start, t_end, summary, key_timestamps)
- Optional user text
- Executor catalog (source of truth)

Goals:
- Assign each final segment a surface: WEB | DESKTOP | WAIT | AUTO
- Merge adjacent segments when a single executor can execute them as ONE bounded task.
- Prefer fewer segments (typically 4–10) for ~3 minutes.
- Avoid long browsing sessions for web agents; prefer short bounded web tasks.

Return ONLY JSON with schema provided in response_format.
No markdown. No extra keys.
"""


def _resize_bgr(bgr, max_w: int):
    h, w = bgr.shape[:2]
    if w <= max_w:
        return bgr
    scale = max_w / float(w)
    return cv2.resize(bgr, (int(w * scale), int(h * scale)), interpolation=cv2.INTER_AREA)


def _bgr_to_jpeg_data_url(bgr, quality: int) -> str:
    params = [int(cv2.IMWRITE_JPEG_QUALITY), int(quality)]
    ok, buf = cv2.imencode(".jpg", bgr, params)
    if not ok:
        raise RuntimeError("Failed to encode frame as JPEG")
    b64 = base64.b64encode(buf.tobytes()).decode("ascii")
    return f"data:image/jpeg;base64,{b64}"


def sample_video_frames(video_path: Path, cfg: SegmenterConfig) -> List[Tuple[float, str]]:
    cap = cv2.VideoCapture(str(video_path))
    if not cap.isOpened():
        raise RuntimeError(f"Failed to open video: {video_path}")

    fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
    duration_s = (total_frames / float(fps)) if fps > 0 else 0.0

    if cfg.sample_fps is not None:
        step_s = 1.0 / max(0.1, float(cfg.sample_fps))
    else:
        step_s = max(0.2, float(cfg.sample_every_s))

    times: List[float] = []
    t = 0.0
    while t <= duration_s and len(times) < int(cfg.max_frames):
        times.append(t)
        t += step_s

    samples: List[Tuple[float, str]] = []
    for ts in times:
        frame_idx = int(ts * fps)
        cap.set(cv2.CAP_PROP_POS_FRAMES, frame_idx)
        ok, frame = cap.read()
        if not ok:
            continue
        frame = _resize_bgr(frame, cfg.max_w)
        img_url = _bgr_to_jpeg_data_url(frame, quality=cfg.jpeg_quality)
        samples.append((float(ts), img_url))

    cap.release()
    return samples


# JSON Schemas for response_format
VISUAL_SCHEMA: Dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["segments"],
    "properties": {
        "segments": {
            "type": "array",
            "minItems": 1,
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["id", "t_start", "t_end", "summary", "key_timestamps"],
                "properties": {
                    "id": {"type": "string"},
                    "t_start": {"type": "number"},
                    "t_end": {"type": "number"},
                    "summary": {"type": "string"},
                    "key_timestamps": {
                        "type": "array",
                        "items": {"type": "number"},
                    },
                },
            },
        }
    },
}

ALIGNED_SCHEMA: Dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["segments"],
    "properties": {
        "segments": {
            "type": "array",
            "minItems": 1,
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["id", "t_start", "t_end", "surface", "summary", "key_timestamps", "merge_of"],
                "properties": {
                    "id": {"type": "string"},
                    "t_start": {"type": "number"},
                    "t_end": {"type": "number"},
                    "surface": {"type": "string", "enum": ["WEB", "DESKTOP", "WAIT", "AUTO"]},
                    "summary": {"type": "string"},
                    "key_timestamps": {
                        "type": "array",
                        "items": {"type": "number"},
                    },
                    "merge_of": {
                        "type": "array",
                        "items": {"type": "string"},
                    },
                },
            },
        }
    },
}


def _validate_visual(d: Dict[str, Any]) -> Dict[str, Any]:
    # Minimal deterministic checks (schema is already enforced by response_format, this is belt+suspenders)
    segs = d.get("segments")
    if not isinstance(segs, list) or not segs:
        raise ValueError("segments must be a non-empty list")
    for s in segs:
        if not isinstance(s.get("t_start"), (int, float)) or not isinstance(s.get("t_end"), (int, float)):
            raise ValueError("t_start/t_end must be numbers")
    return d


def _validate_aligned(d: Dict[str, Any]) -> Dict[str, Any]:
    segs = d.get("segments")
    if not isinstance(segs, list) or not segs:
        raise ValueError("segments must be a non-empty list")
    for s in segs:
        if s.get("surface") not in ("WEB", "DESKTOP", "WAIT", "AUTO"):
            raise ValueError("invalid surface")
        if not isinstance(s.get("merge_of"), list) or not s["merge_of"]:
            raise ValueError("merge_of must be non-empty list")
    return d


class LLMSegmenter:
    def __init__(self, cfg: SegmenterConfig = SegmenterConfig()):
        self.cfg = cfg
        self.caller = LLMJsonCaller()

    def _segment_visual(self, video_path: Path, user_text: Optional[str]) -> Dict[str, Any]:
        frames = sample_video_frames(video_path, self.cfg)

        # Keep text small; do not embed base64 images into JSON text.
        meta = {
            "user_text": user_text,
            "target_min_segments": self.cfg.target_min_segments,
            "target_max_segments": self.cfg.target_max_segments,
            "frames": [{"t": t} for (t, _img) in frames],
        }

        content: List[Dict[str, Any]] = [{"type": "input_text", "text": json.dumps(meta, ensure_ascii=False)}]
        for t, img_url in frames:
            content.append({"type": "input_text", "text": f"frame_t={t:.3f}"})
            content.append({"type": "input_image", "image_url": img_url})

        return self.caller.call_json(
            cfg=JSONCallConfig(model=self.cfg.model, retries=2, strict_schema=True),
            system=SYSTEM_VISUAL,
            user_content=content,
            schema_name="VisualSegments",
            json_schema=VISUAL_SCHEMA,
            validator=_validate_visual,
        )

    def _align_to_executors(self, visual_segments: Dict[str, Any], user_text: Optional[str]) -> Dict[str, Any]:
        payload = {
            "user_text": user_text,
            "executor_catalog_text": _executor_catalog_text(),
            "visual_segments": visual_segments,
        }

        return self.caller.call_json(
            cfg=JSONCallConfig(model=self.cfg.model, retries=2, strict_schema=True),
            system=SYSTEM_ALIGN,
            user_content=json.dumps(payload, ensure_ascii=False),
            schema_name="AlignedSegments",
            json_schema=ALIGNED_SCHEMA,
            validator=_validate_aligned,
        )

    def segment(self, video_path: Path, user_text: Optional[str] = None) -> Dict[str, Any]:
        visual = self._segment_visual(video_path=video_path, user_text=user_text)
        return self._align_to_executors(visual_segments=visual, user_text=user_text)

    def segment_video(self, video_path: Path, user_text: Optional[str] = None) -> Dict[str, Any]:
        return self.segment(video_path=video_path, user_text=user_text)


def segment_video(
    video_path: Union[str, Path],
    user_text: Optional[str] = None,
    cfg: Optional[SegmenterConfig] = None,
) -> Dict[str, Any]:
    vp = Path(video_path) if not isinstance(video_path, Path) else video_path
    seg_cfg = cfg or SegmenterConfig()
    return LLMSegmenter(cfg=seg_cfg).segment(video_path=vp, user_text=user_text)

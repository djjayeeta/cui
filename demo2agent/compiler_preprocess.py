from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import cv2
import numpy as np

from demo2agent.models import DemoTrace
from demo2agent.util import ensure_dir, write_json


def _extract_transcript_text_from_trace(trace: DemoTrace) -> str:
    """Best-effort transcript extraction.

    Order:
      1) trace.transcript if present (list of dicts with 'text' or plain strings)
      2) marker event with data['transcript_file'] pointing to a text file
    """
    t = getattr(trace, 'transcript', None)
    if t:
        if isinstance(t, list):
            parts = []
            for x in t:
                if isinstance(x, dict):
                    parts.append(str(x.get('text') or '').strip())
                else:
                    parts.append(str(x).strip())
            txt = "\n".join([p for p in parts if p])
            if txt.strip():
                return txt.strip()
        elif isinstance(t, str) and t.strip():
            return t.strip()

    events = getattr(trace, 'events', None) or []
    for ev in events:
        try:
            ev_type = getattr(ev, 'type', None) or ev.get('type')
            data = getattr(ev, 'data', None) or ev.get('data') or {}
        except Exception:
            continue
        if ev_type != 'marker':
            continue
        tf = data.get('transcript_file') if isinstance(data, dict) else None
        if not tf:
            continue
        p = Path(tf)
        if p.exists() and p.is_file():
            try:
                return p.read_text(encoding='utf-8', errors='ignore').strip()
            except Exception:
                return p.read_text(errors='ignore').strip()
    return ""

@dataclass
class PreprocessConfig:
    # Thumbnails for LLM/compiler
    thumb_max_w: int = 640
    thumb_format: str = "png"

    # Evidence extraction: if key_timestamps missing, sample midpoint
    default_keyframes_per_segment: int = 1

    # If you still want click-based path (legacy)
    merge_clicks_within_s: float = 1.2
    attach_text_within_s: float = 1.8
    attach_transcript_within_s: float = 3.0


def _resize_to_max_width(bgr: np.ndarray, max_w: int) -> np.ndarray:
    h, w = bgr.shape[:2]
    if w <= max_w:
        return bgr
    scale = max_w / float(w)
    return cv2.resize(bgr, (int(w * scale), int(h * scale)), interpolation=cv2.INTER_AREA)


def _read_json(path: Path) -> Dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _extract_frame_at(video_path: Path, t: float) -> Optional[np.ndarray]:
    cap = cv2.VideoCapture(str(video_path))
    if not cap.isOpened():
        return None

    fps = float(cap.get(cv2.CAP_PROP_FPS) or 30.0)
    frame_idx = int(round(t * fps))
    cap.set(cv2.CAP_PROP_POS_FRAMES, frame_idx)

    ok, frame = cap.read()
    cap.release()
    if not ok:
        return None
    return frame


def preprocess_video_segments_for_compiler(
    *,
    video_path: Path,
    segments_path: Path,
    out_dir: Path,
    demo_name: str = "demo",
    started_at_iso: str = "",
    screen_size: Optional[List[int]] = None,
    transcript_file_path: Optional[str] = None,
    cfg: PreprocessConfig = PreprocessConfig(),
) -> Dict[str, Any]:
    """
    Convert LLM-produced segments.json + video into compile_input.json with evidence frames.

    Output shape:
      {
        demo_name, started_at_iso, screen_size, screen_video,
        segments: [
          {
            segment_id, t_start, t_end, surface, summary,
            keyframes: [{t, frame_path, thumb_path}, ...]
          }
        ],
        transcript: [...]
      }
    """
    ensure_dir(out_dir)
    evidence_dir = out_dir / "evidence"
    ensure_dir(evidence_dir)

    seg_data = _read_json(segments_path)
    segs = list(seg_data.get("segments") or [])

    out_segments: List[Dict[str, Any]] = []
    for i, s in enumerate(segs, start=1):
        sid = str(s.get("id") or f"seg_{i:03d}")
        t_start = float(s.get("t_start") or 0.0)
        t_end = float(s.get("t_end") or t_start)
        surface = str(s.get("surface") or "MIXED")
        summary = str(s.get("summary") or "").strip()
        key_ts = s.get("key_timestamps") or []

        # If missing/empty, pick midpoint
        if not key_ts:
            mid = (t_start + t_end) / 2.0
            key_ts = [mid]

        keyframes_out: List[Dict[str, Any]] = []
        for j, t in enumerate(key_ts[: max(1, cfg.default_keyframes_per_segment)], start=1):
            t = float(t)
            frame = _extract_frame_at(video_path, t)
            if frame is None:
                continue

            frame_path = evidence_dir / f"{sid}_kf_{j:02d}.{cfg.thumb_format}"
            thumb_path = evidence_dir / f"{sid}_kf_{j:02d}_thumb.{cfg.thumb_format}"

            cv2.imwrite(str(frame_path), frame)
            thumb = _resize_to_max_width(frame, cfg.thumb_max_w)
            cv2.imwrite(str(thumb_path), thumb)

            keyframes_out.append({
                "t": t,
                "frame_path": str(frame_path),
                "thumb_path": str(thumb_path),
            })

        out_segments.append({
            "segment_id": sid,
            "t_start": t_start,
            "t_end": t_end,
            "surface": surface,
            "summary": summary,
            "keyframes": keyframes_out,
        })
    transcript_text = ""
    if transcript_file_path:
        try:
            transcript_text = Path(transcript_file_path).read_text(encoding='utf-8', errors='ignore').strip()
        except Exception:
            transcript_text = ""
    compile_input = {
        "demo_name": demo_name,
        "started_at_iso": started_at_iso,
        "screen_size": screen_size or [],
        "screen_video": str(video_path),
        "segments": out_segments,
        "transcript": [],
        "transcript_text": transcript_text,
    }

    write_json(out_dir / "compile_input.json", compile_input)
    return compile_input

from __future__ import annotations

import time
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, List, Tuple

import cv2
import numpy as np
from mss import mss

from demo2agent.util import ensure_dir


@dataclass
class ScreenRecordConfig:
    out_dir: Path
    fps: int = 10
    monitor_index: int = 1          # mss.monitors[1] is typically the primary display
    video_name: str = "screen.mp4"
    save_click_keyframes: bool = True
    keyframe_dirname: str = "keyframes"
    keyframe_half_size: int = 220    # crop around click if you want (optional)


class ScreenRecorder:
    """
    Records the screen to MP4 using MSS + OpenCV VideoWriter.

    - start(): begins recording
    - stop(): stops recording
    - notify_click(x,y,t): optionally save a keyframe on click
    """
    def __init__(self, cfg: ScreenRecordConfig):
        self.cfg = cfg
        ensure_dir(cfg.out_dir)
        self.video_path = cfg.out_dir / cfg.video_name

        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None

        self._writer: Optional[cv2.VideoWriter] = None
        self._size: Optional[Tuple[int, int]] = None  # (w,h)

        self._latest_frame_bgr: Optional[np.ndarray] = None
        self._latest_frame_lock = threading.Lock()

        self.keyframes: List[dict] = []

    def start(self) -> None:
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=2.0)
        if self._writer is not None:
            try:
                self._writer.release()
            except Exception:
                pass
        self._writer = None

    def notify_click(self, x: int, y: int, t: float) -> None:
        if not self.cfg.save_click_keyframes:
            return
        with self._latest_frame_lock:
            frame = None if self._latest_frame_bgr is None else self._latest_frame_bgr.copy()

        if frame is None:
            return

        kdir = self.cfg.out_dir / self.cfg.keyframe_dirname
        ensure_dir(kdir)
        path = kdir / f"click_{t:.3f}.png"
        cv2.imwrite(str(path), frame)

        self.keyframes.append({"t": float(t), "path": str(path), "x": int(x), "y": int(y)})

    def _init_writer(self, w: int, h: int) -> None:
        fourcc = cv2.VideoWriter_fourcc(*"mp4v")  # works on mac for .mp4
        self._writer = cv2.VideoWriter(str(self.video_path), fourcc, float(self.cfg.fps), (w, h))
        if not self._writer.isOpened():
            raise RuntimeError(
                "Failed to open VideoWriter. "
                "On macOS, try: pip install opencv-python-headless OR ensure permissions."
            )

    def _loop(self) -> None:
        interval = 1.0 / max(1, self.cfg.fps)
        with mss() as sct:
            monitor = sct.monitors[self.cfg.monitor_index]
            w = int(monitor["width"])
            h = int(monitor["height"])

            self._size = (w, h)
            self._init_writer(w, h)

            while not self._stop.is_set():
                t0 = time.time()

                img = np.array(sct.grab(monitor))        # BGRA
                bgr = cv2.cvtColor(img, cv2.COLOR_BGRA2BGR)

                with self._latest_frame_lock:
                    self._latest_frame_bgr = bgr

                assert self._writer is not None
                self._writer.write(bgr)

                dt = time.time() - t0
                sleep_s = interval - dt
                if sleep_s > 0:
                    time.sleep(sleep_s)

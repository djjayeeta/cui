from __future__ import annotations

import subprocess
import threading
import time
from dataclasses import dataclass,field
from pathlib import Path
from typing import List, Optional, Dict, Any

import pyautogui
from pynput import keyboard, mouse

from demo2agent.models import DemoTrace, RawEvent
from demo2agent.util import iso_now, ensure_dir
from demo2agent.screen_record import ScreenRecorder, ScreenRecordConfig

import os
import sys
import signal


@dataclass
class AudioRecordConfig:
    enabled: bool = False
    # If None, we use a reasonable platform default input.
    # If you want explicit devices, set this to the ffmpeg input string you want.
    device: Optional[str] = None

    # Output encoding
    codec: str = "aac"          # "aac" -> .m4a container is a good default
    container_ext: str = "m4a"  # "m4a" / "wav" etc.


def _ffmpeg_mic_input_args(device: Optional[str]) -> List[str]:
    """
    Returns ffmpeg args for the platform microphone input.
    Notes:
      - macOS uses avfoundation
      - Linux tries pulse "default"
      - Windows uses dshow (device string must usually be provided)
    """
    if device:
        # User provided a full input spec; assume they know what they’re doing.
        # Example mac: device=":0" with -f avfoundation
        # Example linux pulse: device="default"
        # Example win dshow: device="audio=Microphone (Realtek...)"
        # We still need a matching -f below; if they provide a full spec, prefer passing it raw:
        # If you want full control, set device to something like: "avfoundation::0" and parse it yourself.
        pass

    if sys.platform == "darwin":
        # avfoundation mic is typically index 0 (":0"). Users can override with device=":0" or similar.
        mic = device or ":0"
        return ["-f", "avfoundation", "-i", mic]

    if sys.platform.startswith("linux"):
        mic = device or "default"
        return ["-f", "pulse", "-i", mic]

    if sys.platform.startswith("win"):
        # Windows often needs explicit device name like:
        # device='audio=Microphone (Realtek(R) Audio)'
        mic = device or "audio=default"
        return ["-f", "dshow", "-i", mic]

    # Fallback
    mic = device or "default"
    return ["-i", mic]


def _start_audio_recording(audio_path: Path, cfg: AudioRecordConfig) -> subprocess.Popen:
    """
    Starts ffmpeg mic recording into audio_path.
    """
    audio_path.parent.mkdir(parents=True, exist_ok=True)

    input_args = _ffmpeg_mic_input_args(cfg.device)

    # Encode to AAC in m4a by default (small, widely supported)
    # For WAV: set codec="pcm_s16le", container_ext="wav"
    cmd = [
        "ffmpeg",
        "-y",
        "-hide_banner",
        "-loglevel", "error",
        *input_args,
        "-acodec", cfg.codec,
        str(audio_path),
    ]

    return subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

def transcribe_audio_openai(audio_path: Path, out_txt_path: Path, model: str = "whisper-1") -> str:
    """
    Transcribe with OpenAI Audio Transcriptions API.
    Requires: OPENAI_API_KEY environment variable and `openai` installed.
    Writes plain text transcript to out_txt_path and returns it.
    """
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        raise RuntimeError("OPENAI_API_KEY is not set; cannot transcribe.")

    try:
        from openai import OpenAI
    except Exception as e:
        raise RuntimeError("openai package not available; install it to enable transcription.") from e

    client = OpenAI()

    with audio_path.open("rb") as f:
        # This call shape matches OpenAI python v1+.
        # If your repo pins a different openai version, adjust accordingly.
        resp = client.audio.transcriptions.create(
            model=model,
            file=f,
            response_format="text",
        )

    text = resp if isinstance(resp, str) else str(resp)
    out_txt_path.parent.mkdir(parents=True, exist_ok=True)
    out_txt_path.write_text(text, encoding="utf-8")
    return text

@dataclass
class RecorderConfig:
    out_dir: Path
    name: str = "demo"
    max_seconds: int = 30

    # Window/app context capture
    capture_window_titles: bool = True
    capture_frontmost_app: bool = True
    context_min_interval_s: float = 0.25  # rate limit expensive calls

    typed_flush_s: float = 0.6

    record_screen: bool = True
    screen_fps: int = 10
    record_audio: bool = False
    audio_cfg: AudioRecordConfig = field(
        default_factory=lambda: AudioRecordConfig(enabled=False)
    )

    transcribe_audio: bool = False
    transcription_model: str = "whisper-1"

class DemoRecorder:
    def __init__(self, cfg: RecorderConfig):
        self.cfg = cfg
        ensure_dir(cfg.out_dir)

        self._t0: Optional[float] = None
        self._stop = threading.Event()
        self._events: List[RawEvent] = []

        self._typed_buf: List[str] = []
        self._typed_last_emit: float = 0.0

        self._mouse_listener = None
        self._kb_listener = None

        self._screen: Optional[ScreenRecorder] = None

        # cache for context
        self._last_context_t: float = -1e9
        self._context_cache: Dict[str, Any] = {}
        self._audio_proc: Optional[subprocess.Popen] = None
        self._audio_path: Optional[Path] = None
        self._transcript_path: Optional[Path] = None

    def _now(self) -> float:
        assert self._t0 is not None
        return time.time() - self._t0

    def _emit_text_if_needed(self, force: bool = False) -> None:
        t = self._now()
        if self._typed_buf and (force or (t - self._typed_last_emit) >= self.cfg.typed_flush_s):
            txt = "".join(self._typed_buf)
            self._events.append(RawEvent(t=t, type="text", data={"text": txt}))
            self._typed_buf = []
            self._typed_last_emit = t

    # -------- macOS context helpers --------

    def _get_frontmost_app_name_macos(self) -> Optional[str]:
        # Generic: no workflow/app hardcoding
        try:
            out = subprocess.check_output(
                [
                    "osascript",
                    "-e",
                    'tell application "System Events" to get name of first application process whose frontmost is true',
                ],
                text=True,
            ).strip()
            return out or None
        except Exception:
            return None

    def _get_active_window_title_best_effort(self) -> Optional[str]:
        if not self.cfg.capture_window_titles:
            return None
        try:
            title = pyautogui.getActiveWindowTitle()
            if title:
                return str(title)
        except Exception:
            pass
        return None

    def _log_context(self) -> None:
        """
        Emits context events at most every context_min_interval_s.
        Stores in event stream as a window_title event with extra fields,
        but you can also add a new event type if you prefer.
        """
        t = self._now()
        if (t - self._last_context_t) < self.cfg.context_min_interval_s:
            return

        data: Dict[str, Any] = {}
        if self.cfg.capture_frontmost_app:
            app = self._get_frontmost_app_name_macos()
            if app:
                data["frontmost_app"] = app

        title = self._get_active_window_title_best_effort()
        if title:
            data["title"] = title

        if data:
            self._events.append(RawEvent(t=t, type="window_title", data=data))
            self._context_cache = data

        self._last_context_t = t

    def _current_context(self) -> Dict[str, Any]:
        """
        Returns last known context cache (frontmost_app/title) to attach inline to click/key events.
        """
        return dict(self._context_cache)

    # -------- input handlers --------

    def _on_click(self, x, y, button, pressed):
        if not pressed:
            return
        self._emit_text_if_needed(force=True)

        # capture app context near this action
        self._log_context()
        ctx = self._current_context()

        t = self._now()
        self._events.append(
            RawEvent(
                t=t,
                type="mouse_click",
                data={
                    "x": int(x),
                    "y": int(y),
                    "button": str(button),
                    **ctx,
                },
            )
        )

        if self._screen is not None:
            self._screen.notify_click(int(x), int(y), t)

    def _on_key_down(self, key):
        # capture app context near typing
        self._log_context()
        ctx = self._current_context()

        try:
            if getattr(key, "char", None) is not None:
                self._typed_buf.append(key.char)
            elif key == keyboard.Key.space:
                self._typed_buf.append(" ")
            elif key == keyboard.Key.enter:
                self._emit_text_if_needed(force=True)
            elif key == keyboard.Key.backspace:
                if self._typed_buf:
                    self._typed_buf.pop()
        except Exception:
            pass

        self._events.append(
            RawEvent(
                t=self._now(),
                type="key_down",
                data={"key": str(key), **ctx},
            )
        )

    def _on_key_up(self, key):
        self._events.append(RawEvent(t=self._now(), type="key_up", data={"key": str(key)}))
        if key == keyboard.Key.esc:
            self.stop()

    # -------- lifecycle --------

    def start(self) -> None:
        self._t0 = time.time()
        self._stop.clear()
        if self.cfg.record_audio and self.cfg.audio_cfg.enabled:
            self._audio_path = (self.cfg.out_dir / f"audio.{self.cfg.audio_cfg.container_ext}")
            self._audio_proc = _start_audio_recording(self._audio_path, self.cfg.audio_cfg)

        if self.cfg.record_screen:
            self._screen = ScreenRecorder(
                ScreenRecordConfig(out_dir=self.cfg.out_dir, fps=self.cfg.screen_fps)
            )
            self._screen.start()

        self._mouse_listener = mouse.Listener(on_click=self._on_click)
        self._kb_listener = keyboard.Listener(on_press=self._on_key_down, on_release=self._on_key_up)
        self._mouse_listener.start()
        self._kb_listener.start()

    def stop(self) -> None:
        self._stop.set()
        # Stop audio
        if self._audio_proc is not None:
            try:
                # Tell ffmpeg to finish cleanly
                if self._audio_proc.stdin:
                    try:
                        self._audio_proc.stdin.write(b"q\n")
                        self._audio_proc.stdin.flush()
                    except Exception:
                        pass

                # Give it a moment then force kill if needed
                try:
                    self._audio_proc.wait(timeout=2)
                except Exception:
                    if sys.platform == "win32":
                        self._audio_proc.terminate()
                    else:
                        self._audio_proc.send_signal(signal.SIGINT)
                    try:
                        self._audio_proc.wait(timeout=2)
                    except Exception:
                        self._audio_proc.kill()
            finally:
                self._audio_proc = None

        self._emit_text_if_needed(force=True)
        if self._mouse_listener:
            self._mouse_listener.stop()
        if self._kb_listener:
            self._kb_listener.stop()
        if self._screen:
            self._screen.stop()

    def run_blocking(self) -> DemoTrace:
        print("Recording... press ESC to stop.")
        self.start()
        t0 = time.time()
        while (time.time() - t0) < self.cfg.max_seconds and not self._stop.is_set():
            time.sleep(0.2)
        self.stop()
        w, h = pyautogui.size()
        trace = DemoTrace(
            name=self.cfg.name,
            started_at_iso=iso_now(),
            screen_size=[int(w), int(h)],
            events=self._events,
        )
        if self.cfg.transcribe_audio and self._audio_path and self._audio_path.exists():
            self._transcript_path = self.cfg.out_dir / "transcript.txt"
            trace.audio_path = str(self._audio_path)
            try:
                transcribe_audio_openai(
                    audio_path=self._audio_path,
                    out_txt_path=self._transcript_path,
                    model=self.cfg.transcription_model,
                )
            except Exception as e:
                # Don’t fail the whole recording if transcription fails—just log a marker.
                self._events.append(
                    RawEvent(t=self._now(), type="marker", data={"transcription_error": str(e)})
                )
            if self._audio_path and self._audio_path.exists():
                trace.events.append(
                    RawEvent(t=self._now(), type="marker", data={"audio_file": str(self._audio_path)})
                )

            if self._transcript_path and self._transcript_path.exists():
                trace.transcript_file_path = str(self._transcript_path)
                trace.events.append(
                    RawEvent(t=self._now(), type="marker", data={"transcript_file": str(self._transcript_path)})
                )


        # Basic sanity warning
        clicks = [e for e in self._events if e.type == "mouse_click"]
        keys = [e for e in self._events if e.type == "key_down"]
        if len(clicks) == 0 and len(keys) == 0:
            print(
                "\n[WARNING] No mouse/keyboard events captured. On macOS, enable:\n"
                "Privacy & Security -> Accessibility AND Input Monitoring\n"
                "for the app running Python (Terminal/VS Code/etc), then restart it.\n"
            )

        
        

        # Attach screen artifacts
        if self._screen:
            trace.events.append(
                RawEvent(t=self._now(), type="marker", data={"screen_video": str(self._screen.video_path)})
            )
            if self._screen.keyframes:
                trace.events.append(
                    RawEvent(t=self._now(), type="marker", data={"keyframes": self._screen.keyframes})
                )

        return trace

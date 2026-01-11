from __future__ import annotations
from pathlib import Path
from typing import Dict, List, Optional

import sounddevice as sd
import soundfile as sf

def record_wav(path: Path, duration_s: int, samplerate: int = 16000) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    audio = sd.rec(int(duration_s * samplerate), samplerate=samplerate, channels=1, dtype="float32")
    sd.wait()
    sf.write(str(path), audio, samplerate)

def transcribe_faster_whisper(wav_path: Path) -> Optional[List[Dict]]:
    try:
        from faster_whisper import WhisperModel  # type: ignore
    except Exception:
        return None

    model = WhisperModel("small", device="cpu", compute_type="int8")
    segments, _ = model.transcribe(str(wav_path), vad_filter=True)
    out = []
    for s in segments:
        out.append({"t0": float(s.start), "t1": float(s.end), "text": s.text.strip()})
    return out

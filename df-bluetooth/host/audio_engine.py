"""Looping proximity tone with dynamic volume (sounddevice, no pygame)."""

from __future__ import annotations

import math
import struct
import threading
import wave
from pathlib import Path
from typing import Optional


def ensure_tone_wav(
    path: Path,
    *,
    seconds: float = 0.4,
    freq_hz: float = 440.0,
    sample_rate: int = 44100,
    amplitude: float = 0.35,
) -> Path:
    """Create a short sine WAV if missing (soft attack/release to avoid clicks)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists() and path.stat().st_size > 44:
        return path

    n = int(seconds * sample_rate)
    attack = int(0.02 * sample_rate)
    release = int(0.04 * sample_rate)
    frames = bytearray()
    for i in range(n):
        env = 1.0
        if i < attack:
            env = i / max(attack, 1)
        elif i > n - release:
            env = max(0.0, (n - i) / max(release, 1))
        sample = amplitude * env * math.sin(2.0 * math.pi * freq_hz * (i / sample_rate))
        frames += struct.pack("<h", int(max(-1.0, min(1.0, sample)) * 32767))

    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        wf.writeframes(bytes(frames))
    return path


def _load_wav_mono_f32(path: Path) -> tuple[list[float], int]:
    with wave.open(str(path), "rb") as wf:
        channels = wf.getnchannels()
        width = wf.getsampwidth()
        rate = wf.getframerate()
        raw = wf.readframes(wf.getnframes())

    if width != 2:
        raise ValueError(f"expected 16-bit WAV, got sample width {width}")

    samples: list[float] = []
    for i in range(0, len(raw), width * channels):
        # use first channel only
        (val,) = struct.unpack_from("<h", raw, i)
        samples.append(val / 32768.0)
    return samples, rate


class AudioEngine:
    def __init__(self, wav_path: Optional[Path] = None) -> None:
        default = Path(__file__).resolve().parent / "assets" / "tone.wav"
        self.wav_path = ensure_tone_wav(wav_path or default)
        self._volume = 0.0
        self._lock = threading.Lock()
        self._started = False
        self._stream = None
        self._phase = 0
        self._samples: list[float] = []
        self._rate = 44100

    def start(self) -> None:
        import sounddevice as sd

        self._samples, self._rate = _load_wav_mono_f32(self.wav_path)
        if not self._samples:
            raise RuntimeError(f"empty tone file: {self.wav_path}")

        def callback(outdata, frames, time_info, status):  # noqa: ARG001
            with self._lock:
                vol = self._volume
            # slight curve so radius edge stays quieter
            gain = vol * vol
            n = len(self._samples)
            pos = self._phase
            for i in range(frames):
                outdata[i, 0] = self._samples[pos] * gain
                pos += 1
                if pos >= n:
                    pos = 0
            self._phase = pos

        self._stream = sd.OutputStream(
            samplerate=self._rate,
            channels=1,
            dtype="float32",
            callback=callback,
            blocksize=1024,
        )
        self._stream.start()
        self._started = True

    def set_volume(self, volume: float) -> None:
        v = max(0.0, min(1.0, float(volume)))
        with self._lock:
            self._volume = v

    @property
    def volume(self) -> float:
        with self._lock:
            return self._volume

    def stop(self) -> None:
        if self._stream is not None:
            self._stream.stop()
            self._stream.close()
            self._stream = None
        self._started = False

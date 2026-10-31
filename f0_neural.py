"""FCPE neural f0 estimation for the Jinriki prep tool.

Independent Python implementation of the FCPE front/back end (mel 128 bins,
16 kHz, hop 160, 360 cent bins). The preprocessing constants and the model
assets come from the reference project; no C++ code is copied.

The model is optional: callers fall back to the YIN estimator when it is
missing, so the tool keeps working without the download.
"""
from __future__ import annotations

import math
import os
from typing import List, Optional, Sequence

import numpy as np

SR = 16000
N_MELS = 128
N_FFT = 1024
WIN = 1024
HOP = 160
F0_MIN = 32.7
F0_MAX = 1975.5
OUT_DIMS = 360
CLIP_VAL = 1e-5
DEFAULT_THRESHOLD = 0.05

_MODEL_DIR_CANDIDATES = (
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "jinriki", "models"),
    os.path.join(os.environ.get("LOCALAPPDATA", ""), "Nama", "models"),
)


def model_dir() -> Optional[str]:
    override = os.environ.get("NAMA_FCPE_DIR")
    if override and os.path.isfile(os.path.join(override, "fcpe.onnx")):
        return os.path.abspath(override)
    try:
        import appenv
        found = appenv.find_model("fcpe.onnx")
        if found:
            return os.path.dirname(found)
    except Exception:
        pass
    for d in _MODEL_DIR_CANDIDATES:
        d = os.path.abspath(d)
        if os.path.isfile(os.path.join(d, "fcpe.onnx")):
            return d
    return None


def available() -> bool:
    if os.environ.get("NAMA_FCPE_DISABLE"):
        return False      # deterministic YIN path (tests, offline comparisons)
    if model_dir() is None:
        return False
    try:
        import onnxruntime  # noqa: F401
    except Exception:
        return False
    return True


class _Mel:
    def __init__(self, directory: str) -> None:
        bank = np.fromfile(os.path.join(directory, "mel_filterbank.bin"), dtype="<f4")
        if bank.size != N_MELS * (N_FFT // 2 + 1):
            raise ValueError("mel_filterbank.bin のサイズが想定と違います。")
        self.bank = bank.reshape(N_MELS, N_FFT // 2 + 1).astype(np.float64)
        cent = np.fromfile(os.path.join(directory, "cent_table.bin"), dtype="<f4")
        if cent.size != OUT_DIMS:
            raise ValueError("cent_table.bin のサイズが想定と違います。")
        self.cent = cent.astype(np.float64)
        self.window = 0.5 - 0.5 * np.cos(2.0 * math.pi * np.arange(WIN) / (WIN - 1))

    def extract(self, audio: np.ndarray) -> np.ndarray:
        pad_left = (WIN - HOP) // 2
        pad_right = max((WIN - HOP + 1) // 2, WIN - len(audio) - pad_left)
        n = len(audio)
        if pad_right < n:
            left = np.array([audio[min(i, n - 1)] for i in range(pad_left, 0, -1)], dtype=np.float64)
            right = np.array([audio[max(0, n - 2 - i)] for i in range(pad_right)], dtype=np.float64)
            padded = np.concatenate([left, audio, right])
        else:
            padded = np.zeros(pad_left + n + pad_right, dtype=np.float64)
            padded[pad_left:pad_left + n] = audio
        frames = 1 + (len(padded) - WIN) // HOP
        frames = max(1, frames)
        idx = np.arange(WIN)[None, :] + HOP * np.arange(frames)[:, None]
        spec = np.fft.rfft(padded[idx] * self.window, n=N_FFT, axis=1)
        mag = np.sqrt(spec.real ** 2 + spec.imag ** 2 + 1e-9)
        mel = mag @ self.bank.T
        return np.log(np.maximum(mel, CLIP_VAL))


def _resample_16k(samples: Sequence[float], src_rate: float) -> np.ndarray:
    x = np.asarray(samples, dtype=np.float64)
    if abs(src_rate - SR) < 1e-6:
        return x
    ratio = SR / float(src_rate)
    count = int(len(x) * ratio)
    if count <= 0:
        return np.zeros(0)
    pos = np.arange(count) / ratio
    i = np.floor(pos).astype(np.int64)
    f = pos - i
    i2 = np.minimum(i + 1, len(x) - 1)
    return x[i] * (1.0 - f) + x[i2] * f


class Fcpe:
    """One ONNX session per process; safe to reuse for many clips."""

    def __init__(self, directory: Optional[str] = None) -> None:
        import onnxruntime as ort

        directory = directory or model_dir()
        if not directory:
            raise RuntimeError("FCPEモデルが見つかりません。")
        self._mel = _Mel(directory)
        options = ort.SessionOptions()
        options.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
        options.intra_op_num_threads = max(1, (os.cpu_count() or 2) // 2)
        self._session = ort.InferenceSession(os.path.join(directory, "fcpe.onnx"),
                                             sess_options=options, providers=["CPUExecutionProvider"])
        self._input = self._session.get_inputs()[0].name
        self._output = self._session.get_outputs()[0].name

    def f0(self, samples: Sequence[float], src_rate: float,
           threshold: float = DEFAULT_THRESHOLD) -> List[float]:
        """f0 in Hz per 10 ms frame (0.0 = unvoiced), same frame grid as FCPE."""
        audio = _resample_16k(samples, src_rate)
        if len(audio) < 16:
            return []
        mel = self._mel.extract(audio).astype(np.float32)
        latent = self._session.run([self._output], {self._input: mel[None, :, :]})[0][0]
        out: List[float] = []
        cent = self._mel.cent
        for frame in latent:
            peak = int(np.argmax(frame))
            if frame[peak] <= threshold:
                out.append(0.0)
                continue
            lo, hi = max(0, peak - 4), min(OUT_DIMS - 1, peak + 4)
            weights = frame[lo:hi + 1]
            total = float(weights.sum())
            if total <= 1e-9:
                out.append(0.0)
                continue
            value = float((cent[lo:hi + 1] * weights).sum() / total)
            out.append(10.0 * (2.0 ** (value / 1200.0)))
        return out


def summarise(f0: Sequence[float], frame_seconds: float, skip_seconds: float,
              min_hz: float = 55.0, max_hz: float = 1500.0) -> dict:
    """Median f0 and voiced ratio over the part of the clip after `skip_seconds`."""
    start = max(0, int(round(skip_seconds / frame_seconds)))
    voiced = [v for v in f0[start:] if min_hz <= v <= max_hz]
    total = max(1, len(f0) - start)
    if not voiced:
        return {"midi": None, "hz": None, "strength": 0.0, "voiced": 0.0}
    hz = float(sorted(voiced)[len(voiced) // 2])
    midi = 69.0 + 12.0 * math.log2(hz / 440.0)
    return {"midi": round(midi, 2), "hz": round(hz, 1),
            "strength": round(len(voiced) / total, 2), "voiced": round(len(voiced) / total, 2)}


_FCPE: Optional[Fcpe] = None


def shared() -> Optional[Fcpe]:
    """Reuse the session across units; returns None when unavailable."""
    global _FCPE
    if os.environ.get("NAMA_FCPE_DISABLE"):
        return None            # checked before the cache so tests stay deterministic
    if _FCPE is not None:
        return _FCPE
    if not available():
        return None
    _FCPE = Fcpe()
    return _FCPE

"""Normalise exported WAVs to 44.1 kHz / 24-bit / mono.

The library never edits the source WAV; this module only shapes the files that
drag-and-drop, "選択をWAV", WAV書き出し and the UTAU export hand to other apps.
Stereo is averaged to mono, then a windowed-sinc resampler converts the rate
(with a low-pass for downsampling) and the result is rounded to 24-bit PCM.

numpy is used when available (the packaged app ships it); the pure-Python
fallback keeps the same format when it is not installed, only slower.
"""
from __future__ import annotations

import math
import struct
import wave
from typing import List, Sequence, Tuple

try:  # optional: the app works without numpy, only resampling gets slower
    import numpy as np
except Exception:  # noqa: BLE001 - missing numpy must not break exports
    np = None

EXPORT_SAMPLE_RATE = 44100
EXPORT_BITS = 24
EXPORT_CHANNELS = 1

_TAPS_NP = 32
_TAPS_PY = 16
_FULL_SCALE = float(1 << 23)          # 24-bit signed
_MAX_Q = (1 << 23) - 1
_MIN_Q = -(1 << 23)


# --------------------------------------------------------------- decoding
def _decode_np(raw: bytes, width: int, channels: int):
    if width == 1:
        v = (np.frombuffer(raw, dtype="<u1").astype(np.float64) - 128.0) / 128.0
    elif width == 2:
        v = np.frombuffer(raw[:len(raw) - len(raw) % 2], dtype="<i2").astype(np.float64) / 32768.0
    elif width == 3:
        u = np.frombuffer(raw, dtype="<u1")
        u = u[:len(u) - len(u) % 3].reshape(-1, 3).astype(np.int32)
        v = u[:, 0] | (u[:, 1] << 8) | (u[:, 2] << 16)
        v = np.where(v >= 0x800000, v - 0x1000000, v).astype(np.float64) / _FULL_SCALE
    elif width == 4:
        v = np.frombuffer(raw[:len(raw) - len(raw) % 4], dtype="<i4").astype(np.float64) / 2147483648.0
    else:
        raise ValueError(f"unsupported sample width: {width}")
    if channels > 1:
        v = v[:len(v) - len(v) % channels].reshape(-1, channels).mean(axis=1)
    return v


def _decode_py(raw: bytes, width: int, channels: int) -> List[float]:
    if width == 1:
        vals = [(b - 128) / 128.0 for b in raw]
    elif width == 2:
        vals = [v / 32768.0 for (v,) in struct.iter_unpack("<h", raw[:len(raw) - len(raw) % 2])]
    elif width == 3:
        vals = []
        for i in range(0, len(raw) - 2, 3):
            v = raw[i] | (raw[i + 1] << 8) | (raw[i + 2] << 16)
            if v & 0x800000:
                v -= 0x1000000
            vals.append(v / _FULL_SCALE)
    elif width == 4:
        vals = [v / 2147483648.0
                for (v,) in struct.iter_unpack("<i", raw[:len(raw) - len(raw) % 4])]
    else:
        raise ValueError(f"unsupported sample width: {width}")
    if channels == 1:
        return vals
    frames = len(vals) // channels
    return [sum(vals[i * channels:(i + 1) * channels]) / channels for i in range(frames)]


# ------------------------------------------------------------- resampling
def _blackman(n: int) -> List[float]:
    if n <= 1:
        return [1.0]
    return [0.42 - 0.5 * math.cos(2.0 * math.pi * k / (n - 1))
            + 0.08 * math.cos(4.0 * math.pi * k / (n - 1)) for k in range(n)]


def _sinc(z: float) -> float:
    if abs(z) < 1e-12:
        return 1.0
    return math.sin(math.pi * z) / (math.pi * z)


def _resample_np(x, sr_in: int):
    if sr_in == EXPORT_SAMPLE_RATE or len(x) == 0:
        return x
    ratio = EXPORT_SAMPLE_RATE / float(sr_in)
    n_out = max(1, int(round(len(x) * ratio)))
    cutoff = min(1.0, ratio) * 0.95      # keep a margin below the new Nyquist
    half = _TAPS_NP // 2
    offs = np.arange(-half + 1, half + 1, dtype=np.int64)
    window = np.blackman(_TAPS_NP)
    out = np.empty(n_out, dtype=np.float64)
    block = 32768
    for start in range(0, n_out, block):
        end = min(n_out, start + block)
        pos = np.arange(start, end, dtype=np.float64) * (sr_in / float(EXPORT_SAMPLE_RATE))
        base = np.floor(pos)
        frac = pos - base
        idx = np.clip(base.astype(np.int64)[:, None] + offs[None, :], 0, len(x) - 1)
        dist = frac[:, None] - offs[None, :]
        weights = cutoff * np.sinc(cutoff * dist) * window[None, :]
        weights /= weights.sum(axis=1, keepdims=True)
        out[start:end] = (x[idx] * weights).sum(axis=1)
    return out


def _resample_py(x: Sequence[float], sr_in: int) -> List[float]:
    if sr_in == EXPORT_SAMPLE_RATE or len(x) == 0:
        return list(x)
    ratio = EXPORT_SAMPLE_RATE / float(sr_in)
    n_out = max(1, int(round(len(x) * ratio)))
    cutoff = min(1.0, ratio) * 0.95
    half = _TAPS_PY // 2
    offs = list(range(-half + 1, half + 1))
    window = _blackman(_TAPS_PY)
    last = len(x) - 1
    out = []
    for i in range(n_out):
        pos = i * sr_in / float(EXPORT_SAMPLE_RATE)
        base = int(math.floor(pos))
        frac = pos - base
        acc = 0.0
        total = 0.0
        for k, off in enumerate(offs):
            w = cutoff * _sinc(cutoff * (frac - off)) * window[k]
            acc += x[min(last, max(0, base + off))] * w
            total += w
        out.append(acc / total if total else 0.0)
    return out


# --------------------------------------------------------------- encoding
def _quantize_np(x) -> bytes:
    q = np.clip(np.round(x * _MAX_Q), _MIN_Q, _MAX_Q).astype(np.int32)
    u = (q & 0xFFFFFF).astype(np.uint32)
    out = np.empty((u.size, 3), dtype=np.uint8)
    out[:, 0] = u & 0xFF
    out[:, 1] = (u >> 8) & 0xFF
    out[:, 2] = (u >> 16) & 0xFF
    return out.tobytes()


def _quantize_py(x: Sequence[float]) -> bytes:
    buf = bytearray(len(x) * 3)
    j = 0
    for v in x:
        q = int(round(v * _MAX_Q))
        q = _MIN_Q if q < _MIN_Q else (_MAX_Q if q > _MAX_Q else q)
        if q < 0:
            q += 1 << 24
        buf[j] = q & 0xFF
        buf[j + 1] = (q >> 8) & 0xFF
        buf[j + 2] = (q >> 16) & 0xFF
        j += 3
    return bytes(buf)


# ------------------------------------------------------------------- API
def read_clip(src: str, t0: float, t1: float) -> Tuple[object, int]:
    """Read [t0, t1) as mono float samples; the source file is never written."""
    with wave.open(src, "rb") as w:
        sr, channels, width = w.getframerate(), w.getnchannels(), w.getsampwidth()
        a = max(0, int(t0 * sr))
        b = min(w.getnframes(), int(t1 * sr))
        if b <= a:
            return ([], sr) if np is None else (np.empty(0, dtype=np.float64), sr)
        w.setpos(a)
        raw = w.readframes(b - a)
    samples = _decode_np(raw, width, channels) if np is not None \
        else _decode_py(raw, width, channels)
    return samples, sr


def convert(samples, sr_in: int) -> bytes:
    """24-bit mono PCM at 44.1 kHz, ready for ``wave.writeframes``."""
    if len(samples) == 0:
        return b""
    resampled = _resample_np(samples, sr_in) if np is not None else _resample_py(samples, sr_in)
    return _quantize_np(resampled) if np is not None else _quantize_py(resampled)


def write_clip(src: str, t0: float, t1: float, dst: str) -> bool:
    """Cut ``src`` to [t0, t1) and write a 44.1 kHz / 24-bit / mono WAV."""
    samples, sr = read_clip(src, t0, t1)
    payload = convert(samples, sr)
    if not payload:
        return False
    with wave.open(dst, "wb") as w:
        w.setnchannels(EXPORT_CHANNELS)
        w.setsampwidth(EXPORT_BITS // 8)
        w.setframerate(EXPORT_SAMPLE_RATE)
        w.writeframes(payload)
    return True

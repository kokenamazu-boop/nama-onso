"""Phoneme curator: choose the CV takes that survive auto-OTO.

Input is a folder of one-syllable recordings (``かD#45.wav`` etc.). The tool
measures each take, drops the ones that cannot work, then picks ``_L`` / ``_H``
takes per alias and writes an OpenUtau/UTAU folder.

The rules follow the project's take-curation note (v1.1).
The source WAVs are only read; output goes to a new folder.

CLI:
    python curate.py --root TAKES --out OUT [--mode two_take]
    python curate.py --root TAKES --oto oto.ini --out OUT

Exit codes: 0 ok, 10 rerecord suggestions, 20 an alias lost every take, 30 input error.
"""
from __future__ import annotations

import argparse
import csv
import datetime
import json
import math
import os
import re
import shutil
import sys
import tempfile
import wave
from typing import Dict, List, Optional, Sequence, Tuple

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import core  # noqa: E402
import export_audio  # noqa: E402

SCHEMA_VERSION = 1
MODES = ("single", "two_take", "keep_usable")
DEFAULT_SPLIT = "F#4"

# ---------------------------------------------------------------- constants
VOICED_PLOSIVES = set("がぎぐげごだぢづでどばびぶべぼ")
VOICELESS_PLOSIVES = set("かきくけこたてとぱぴぷぺぽ")
VOICED_FRICATIVES = set("ざじずぜぞ")
VOICELESS_FRICATIVES = set("さしすせそはひふへほ")
AFFRICATES = set("つちちゃちゅちょ")

NOTE_RE = re.compile(r"^(?P<alias>.*?)(?P<note>[A-G]#?-?\d)(?P<num>\d*)$")
FALLBACK_RE = re.compile(r"^(?P<alias>.*?[ぁ-んァ-ヶー])(?P<num>\d*)$")

OTO_FIELDS = ("offset", "consonant", "cutoff", "preutterance", "overlap")


# ------------------------------------------------------------------- config
class Config:
    """Every threshold from the order, in one place (defaults are the order's)."""

    def __init__(self, **values):
        self.mode = "two_take"
        self.min_per_alias = 1
        self.scarce_policy = "duplicate"       # duplicate | single_role
        self.split_note = DEFAULT_SPLIT
        self.write_prefix_map = True
        self.name = "Nama Onso"
        self.author = ""
        self.room_tone = ""

        self.duration_hard = 150.0
        self.duration_preferred = 200.0
        self.vowel_hard = 40.0
        self.vowel_min = 50.0
        self.vowel_preferred = 100.0
        self.peak_min = -24.0
        self.peak_max = -1.0
        self.clip_max = 5
        self.snr_min = 20.0
        self.snr_warn = 12.0
        self.lead_max = 60.0

        self.jump_db_min = 10.0
        self.jump_db_target = 15.0
        self.burst_to_vowel = (10.0, 100.0)
        self.prevoicing_min = 20.0
        self.prevoicing_target = 30.0
        self.voice_bar_min = 10.0
        self.voice_bar_target = 15.0
        self.flatness_min = 0.3

        self.pitch_low = "D4"
        self.pitch_high = "A4"
        for key, value in values.items():
            if not hasattr(self, key):
                raise AttributeError(f"unknown setting: {key}")
            setattr(self, key, value)

    def as_dict(self) -> dict:
        return {k: v for k, v in self.__dict__.items() if k not in ("room_tone",)}


# Order 6.4 uses grouped keys; accept them as well as the flat names.
CONFIG_GROUPS = {
    "duration_ms": {"hard": "duration_hard", "preferred": "duration_preferred"},
    "lead_ms": {"max": "lead_max"},
    "vowel_ms": {"hard": "vowel_hard", "min": "vowel_min", "preferred": "vowel_preferred"},
    "peak_dbfs": {"min": "peak_min", "max": "peak_max"},
    "snr_db": {"min": "snr_min"},
    "plosive": {"jump_db_min": "jump_db_min", "jump_db_target": "jump_db_target"},
    "voiced": {"prevoicing_ms_min": "prevoicing_min",
               "prevoicing_ms_target": "prevoicing_target"},
    "voiced_fricative": {"voice_bar_ms_min": "voice_bar_min",
                         "voice_bar_ms_target": "voice_bar_target"},
    "fricative": {"flatness_min": "flatness_min"},
    "pitch_targets": {"low": "pitch_low", "high": "pitch_high"},
}
CONFIG_ALIASES = {"clip_count_max": "clip_max", "room_tone": "room_tone"}
CONFIG_IGNORED = {"subbanks", "rerecord_priority", "voice_bar_ms_min"}


def apply_config(cfg: Config, data: dict) -> Config:
    """Apply flat keys or the grouped keys from the order's example YAML."""
    for key, value in data.items():
        if key in CONFIG_IGNORED:
            continue
        if key in CONFIG_GROUPS and isinstance(value, dict):
            for sub, target in CONFIG_GROUPS[key].items():
                if sub in value:
                    setattr(cfg, target, value[sub])
            continue
        target = CONFIG_ALIASES.get(key, key)
        if not hasattr(cfg, target):
            raise ValueError(f"未知の設定キー: {key}")
        setattr(cfg, target, value)
    if cfg.mode not in MODES:
        raise ValueError(f"mode は {', '.join(MODES)} のどれかです: {cfg.mode}")
    return cfg


# --------------------------------------------------------------- name/notes
def note_to_midi(note: str) -> Optional[int]:
    if not note:
        return None
    m = re.fullmatch(r"([A-G]#?)(-?\d)", note)
    if not m:
        return None
    names = {"C": 0, "C#": 1, "D": 2, "D#": 3, "E": 4, "F": 5, "F#": 6,
             "G": 7, "G#": 8, "A": 9, "A#": 10, "B": 11}
    return 12 * (int(m.group(2)) + 1) + names[m.group(1)]


def midi_name(midi: Optional[float]) -> str:
    if midi is None:
        return "-"
    n = int(round(midi))
    names = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]
    return f"{names[n % 12]}{n // 12 - 1}"


def split_name(filename: str) -> Tuple[str, str, str]:
    """``かD#45.wav`` -> (alias, note, number). Missing note is allowed."""
    stem = os.path.splitext(os.path.basename(filename))[0]
    m = NOTE_RE.match(stem)
    if m and m.group("alias"):
        return m.group("alias"), m.group("note"), m.group("num")
    m = FALLBACK_RE.match(stem)
    if m and m.group("alias"):
        return m.group("alias"), "", m.group("num")
    return stem, "", ""


def phoneme_class(alias: str) -> str:
    """Classify an alias by its first kana (yōon and small vowels included)."""
    text = core.to_hiragana(str(alias or "").strip())
    if not text:
        return "other"
    head = text[0]
    if head in VOICED_PLOSIVES:
        return "voiced_plosive"
    if head in VOICELESS_PLOSIVES:
        return "voiceless_plosive"
    if head in VOICED_FRICATIVES:
        return "voiced_fricative"
    if head in VOICELESS_FRICATIVES:
        return "voiceless_fricative"
    if head in AFFRICATES:
        return "affricate"
    return "other"


# ------------------------------------------------------------------- audio
def read_wav_mono(path: str) -> Tuple[np.ndarray, int]:
    """Read a whole WAV as mono float samples (via export_audio's decoder)."""
    samples, sr = export_audio.read_clip(path, 0.0, 1.0e9)
    if not isinstance(samples, np.ndarray):
        samples = np.asarray(samples, dtype=np.float64)
    return samples, int(sr)


def _longest_run(mask: np.ndarray) -> Optional[Tuple[int, int]]:
    best = None
    start = None
    for i, on in enumerate(mask):
        if on and start is None:
            start = i
        elif not on and start is not None:
            if best is None or (i - start) > (best[1] - best[0]):
                best = (start, i)
            start = None
    if start is not None and (best is None or (len(mask) - start) > (best[1] - best[0])):
        best = (start, len(mask))
    return best


def analyse(path: str, room_tone_db: Optional[float] = None) -> dict:
    """Measure one take. Never writes the file."""
    x, sr = read_wav_mono(path)
    duration_ms = len(x) * 1000.0 / sr if sr else 0.0
    out = {"wav": os.path.basename(path), "duration_ms": round(duration_ms, 1),
           "sr": sr, "error": ""}
    if len(x) == 0:
        out["error"] = "empty"
        return out
    peak = float(np.max(np.abs(x)))
    out["peak_dbfs"] = round(20.0 * math.log10(max(peak, 1e-9)), 2)
    out["rms_dbfs"] = round(20.0 * math.log10(max(float(np.sqrt(np.mean(x ** 2))), 1e-9)), 2)
    out["clip_count"] = int(np.sum(np.abs(x) >= 0.999))

    win = max(64, int(sr * 0.010))
    hop = max(32, int(sr * 0.005))
    if len(x) < win:
        x = np.pad(x, (0, win - len(x)))
    frames = np.lib.stride_tricks.sliding_window_view(x, win)[::hop]
    window = np.hanning(win)
    spectrum = np.abs(np.fft.rfft(frames * window, axis=1)) + 1e-12
    freqs = np.fft.rfftfreq(win, 1.0 / sr)
    rms = np.sqrt(np.mean(frames ** 2, axis=1))
    db = 20.0 * np.log10(np.maximum(rms, 1e-9))
    flat = np.exp(np.mean(np.log(spectrum), axis=1)) / np.mean(spectrum, axis=1)
    total = np.sum(spectrum ** 2, axis=1)
    low_ratio = np.sum(spectrum[:, freqs <= 1000.0] ** 2, axis=1) / np.maximum(total, 1e-20)
    times = (np.arange(len(db)) * hop + win / 2.0) * 1000.0 / sr

    peak_frame = float(np.percentile(db, 98)) if len(db) else -120.0
    if room_tone_db is not None:
        noise = room_tone_db
    else:
        quiet = np.sort(db)[:max(2, len(db) // 20)]
        noise = float(np.mean(quiet)) if len(quiet) else -120.0
    out["noise_dbfs"] = round(noise, 2)
    out["peak_frame_dbfs"] = round(peak_frame, 2)
    # With no silence in the take, the 5th percentile is not room noise.
    out["snr_db"] = round(peak_frame - noise, 2) if peak_frame - noise >= 10.0 else None
    # A take that starts on the consonant has no silence: never call that "no audible".
    threshold = min(max(noise + 6.0, peak_frame - 28.0), peak_frame - 10.0)
    audible = np.flatnonzero(db > threshold)
    out["lead_silence_ms"] = round(float(times[audible[0]]) if len(audible) else duration_ms, 1)
    out["audible"] = bool(len(audible))

    vowel_mask = (flat < 0.18) & (db > peak_frame - 25.0)
    run = _longest_run(vowel_mask)
    if run is None or (run[1] - run[0]) < 2:
        out["vowel_start_ms"] = None
        out["vowel_end_ms"] = None
        out["vowel_ms"] = 0.0
        vowel_start = 0.0
    else:
        vowel_start = float(times[run[0]])
        vowel_end = float(times[min(run[1], len(times) - 1)] + hop * 1000.0 / sr)
        out["vowel_start_ms"] = round(vowel_start, 1)
        out["vowel_end_ms"] = round(vowel_end, 1)
        out["vowel_ms"] = round(max(0.0, vowel_end - vowel_start), 1)

    # Burst: the sharpest 5 ms rise. A 10 ms window averages the attack away.
    if len(db) >= 2:
        rise = db[1:] - db[:-1]
        j = int(np.argmax(rise)) + 1
        out["jump_db"] = round(float(min(60.0, max(0.0, rise[j - 1]))), 2)
        out["burst_ms"] = round(float(times[j]), 1)
    else:
        j = 0
        out["jump_db"] = 0.0
        out["burst_ms"] = None
    burst = out["burst_ms"]
    out["burst_to_vowel_ms"] = round(vowel_start - burst, 1) if burst is not None else None

    voiced = (low_ratio > 0.45) & (flat < 0.35) & (db > noise + 6.0)
    # Frication is "unvoiced but audible": low frequency energy collapses even
    # when the flatness stays around 0.15-0.25 (soft ざ/は takes).
    frication = (low_ratio < 0.35) & (db > noise + 6.0) & (times < vowel_start)
    out["frication_ms"] = round(float(np.sum(frication)) * hop * 1000.0 / sr, 1) if len(frication) else 0.0
    out["frication_flatness"] = round(float(np.mean(flat[frication])), 3) if np.any(frication) else 0.0
    out["voiced_frication_ratio"] = round(float(np.mean(low_ratio[frication] > 0.35)), 3) \
        if np.any(frication) else 0.0

    out["prevoicing_ms"], out["prevoicing_start_ms"] = 0.0, None
    out["voice_bar_ms"], out["voice_bar_start_ms"] = 0.0, None
    cls = phoneme_class(split_name(os.path.basename(path))[0])
    if burst is not None and cls == "voiced_plosive":
        # voiced run that ends at (or just before) the burst
        best = None
        for a, b in _runs(voiced):
            end_ms = float(times[min(b, len(times) - 1)])
            start_ms = float(times[a])
            if end_ms <= burst + 15.0 and burst - start_ms <= 150.0:
                best = (start_ms, end_ms)
        if best:
            out["prevoicing_start_ms"] = round(best[0], 1)
            out["prevoicing_ms"] = round(max(0.0, best[1] - best[0]), 1)
    if cls == "voiced_fricative":
        fri = np.flatnonzero(frication)
        if len(fri):
            fri_start = float(times[fri[0]])
            best = None
            for a, b in _runs(voiced):
                start_ms, end_ms = float(times[a]), float(times[min(b, len(times) - 1)])
                if end_ms <= fri_start + 10.0 and fri_start - start_ms <= 150.0:
                    best = (start_ms, end_ms)
            if best:
                out["voice_bar_start_ms"] = round(best[0], 1)
                out["voice_bar_ms"] = round(max(0.0, best[1] - best[0]), 1)
    return out


def _runs(mask: np.ndarray) -> List[Tuple[int, int]]:
    runs, start = [], None
    for i, on in enumerate(mask):
        if on and start is None:
            start = i
        elif not on and start is not None:
            runs.append((start, i))
            start = None
    if start is not None:
        runs.append((start, len(mask)))
    return runs


# ------------------------------------------------------------------ judging
def hard_reasons(m: dict, cfg: Config) -> List[str]:
    reasons = []
    if m.get("error"):
        return [f"解析不能({m['error']})"]
    if not m.get("audible"):
        reasons.append("可聴点なし")
    if m["duration_ms"] < cfg.duration_hard:
        reasons.append(f"短い({m['duration_ms']:.0f}ms)")
    if not m.get("vowel_ms") or m["vowel_ms"] < cfg.vowel_hard:
        reasons.append(f"母音が短い({m.get('vowel_ms') or 0:.0f}ms)")
    if m["clip_count"] > cfg.clip_max:
        reasons.append(f"クリップ({m['clip_count']})")
    if m["peak_dbfs"] > cfg.peak_max:
        reasons.append(f"ピークが高い({m['peak_dbfs']:.1f}dB)")
    if m["peak_dbfs"] < cfg.peak_min:
        reasons.append(f"ピークが低い({m['peak_dbfs']:.1f}dB)")
    return reasons


def quality(m: dict, cfg: Config) -> float:
    """Cost of a take (lower is better), from length, vowel, lead and SNR."""
    vowel = m.get("vowel_ms") or 0.0
    duration = m.get("duration_ms") or 0.0
    cost = 3.0 * (1.0 - min(vowel, 150.0) / 150.0)
    cost += 1.5 * (1.0 - min(duration, 400.0) / 400.0)
    lead = m.get("lead_silence_ms") or 0.0
    if lead > cfg.lead_max:
        cost += min(6.0, (lead - cfg.lead_max) * 0.06)
    if m.get("snr_db") is not None and m["snr_db"] < cfg.snr_warn:
        cost += 0.5
    return cost


def hard_penalty(m: dict, cls: str, cfg: Config) -> float:
    penalty = 0.0
    if cls in ("voiced_plosive", "voiceless_plosive", "affricate"):
        jump = m.get("jump_db") or 0.0
        if jump < 8.0:
            penalty += 6.0
        elif jump < cfg.jump_db_min:
            penalty += 3.0
    if cls == "voiced_plosive":
        pre = m.get("prevoicing_ms") or 0.0
        if pre < 10.0:
            penalty += 4.0
        elif pre < cfg.prevoicing_min:
            penalty += 1.5
    if cls == "voiced_fricative":
        bar = m.get("voice_bar_ms") or 0.0
        if bar < 5.0:
            penalty += 5.0
        elif bar < 10.0:
            penalty += 3.0
        elif bar < cfg.voice_bar_target:
            penalty += 1.0
    if (m.get("vowel_ms") or 0.0) < 50.0:
        penalty += 3.0
    return penalty


def role_target(role: str, cfg: Config) -> Optional[float]:
    """Pitch the role is meant for: _L -> D4, _H -> A4, no suffix -> D4."""
    note = cfg.pitch_high if role == "_H" else cfg.pitch_low
    return note_to_midi(note)


def take_score(m: dict, cls: str, target_midi: Optional[float], cfg: Config) -> float:
    score = quality(m, cfg) + hard_penalty(m, cls, cfg)
    midi = m.get("midi")
    if target_midi is not None and midi is not None:
        score += 2.0 * abs(midi - target_midi)
    elif target_midi is not None:
        score += 6.0
    score -= 0.8 * min(m.get("jump_db") or 0.0, 30.0)
    if cls == "voiced_plosive":
        score -= 0.4 * min(m.get("prevoicing_ms") or 0.0, 60.0)
    if cls == "voiced_fricative":
        score -= 0.5 * min(m.get("voice_bar_ms") or 0.0, 40.0)
    return score


def rerecord(m: dict, cls: str, cfg: Config, scarce: bool = False) -> Optional[dict]:
    """P0/P1/P2 for the best take of an alias (None when nothing is wrong)."""
    problems = []
    priority = None

    def raise_priority(level: str):
        nonlocal priority
        order = {"P0": 0, "P1": 1, "P2": 2}
        if priority is None or order[level] < order[priority]:
            priority = level

    jump = m.get("jump_db") or 0.0
    pre = m.get("prevoicing_ms") or 0.0
    bar = m.get("voice_bar_ms") or 0.0
    vowel = m.get("vowel_ms") or 0.0
    lead = m.get("lead_silence_ms") or 0.0
    dur = m.get("duration_ms") or 0.0
    snr = m.get("snr_db")

    if cls in ("voiced_plosive", "voiceless_plosive", "affricate"):
        if jump < 5.0:
            raise_priority("P0")
            problems.append(f"破裂がほぼ無い({jump:.1f}dB)")
        elif jump < cfg.jump_db_min:
            raise_priority("P1")
            problems.append(f"破裂が弱い({jump:.1f}dB)")
    if cls == "voiced_plosive":
        if pre < 5.0:
            raise_priority("P0")
            problems.append("prevoicingなし")
        elif pre < cfg.prevoicing_min:
            raise_priority("P1")
            problems.append(f"prevoicingが短い({pre:.0f}ms)")
    if cls == "voiced_fricative":
        if bar < 5.0:
            raise_priority("P0")
            problems.append("voice barなし")
        elif bar < cfg.voice_bar_min:
            raise_priority("P1")
            problems.append(f"voice barが短い({bar:.0f}ms)")
    if vowel < cfg.vowel_min:
        raise_priority("P1")
        problems.append(f"母音が短い({vowel:.0f}ms)")
    if cfg.duration_preferred > dur >= cfg.duration_hard:
        raise_priority("P2")
        problems.append(f"やや短い({dur:.0f}ms)")
    if lead > cfg.lead_max:
        raise_priority("P2")
        problems.append(f"頭の無音{lead:.0f}ms（トリム可）")
    if snr is not None and snr < cfg.snr_warn:
        raise_priority("P2")
        problems.append(f"SNR低い({snr:.1f}dB)")
    if scarce:
        raise_priority("P1")
        problems.append("テイク1本のみ")
    if priority is None:
        return None
    target = {
        "voiced_plosive": f"jump>={cfg.jump_db_target:.0f}dB, prevoicing>={cfg.prevoicing_target:.0f}ms",
        "voiceless_plosive": f"jump>={cfg.jump_db_target:.0f}dB",
        "affricate": f"jump>={cfg.jump_db_target:.0f}dB",
        "voiced_fricative": f"voice bar>={cfg.voice_bar_target:.0f}ms",
    }.get(cls, f"vowel>={cfg.vowel_preferred:.0f}ms")
    return {"priority": priority, "reason": " / ".join(problems), "target": target}


# ---------------------------------------------------------------- selection
def select_for_alias(alias: str, takes: List[dict], cfg: Config) -> Tuple[List[dict], List[dict], Optional[dict]]:
    """Return (selected, rejected, rerecord) for one alias."""
    cls = phoneme_class(alias)
    usable = [t for t in takes if not t["reasons"]]
    rejected = [t for t in takes if t["reasons"]]
    rescued = False
    if not usable:
        # Never leave an alias silent: keep the least-bad take and flag it.
        usable = [min(takes, key=lambda t: take_score(t, cls, None, cfg))]
        rescued = True

    def score(t, target):
        return take_score(t, cls, target, cfg)

    selected = []
    if cfg.mode == "single" or cfg.mode == "keep_usable":
        if cfg.mode == "single":
            selected = [min(usable, key=lambda t: score(t, note_to_midi(cfg.pitch_low)))]
        else:
            selected = sorted(usable, key=lambda t: score(t, note_to_midi(cfg.pitch_low)))
        for t in selected:
            t["role"] = ""
    else:
        low_target = float(note_to_midi(cfg.pitch_low) or 62)
        high_target = float(note_to_midi(cfg.pitch_high) or 69)
        pooled = list(usable)
        if len(pooled) >= 2:
            low = min(pooled, key=lambda t: score(t, low_target))
            remaining = [t for t in pooled if t is not low]
            low_midi = low.get("midi")
            upper = [t for t in remaining
                     if low_midi is None or t.get("midi") is None or t["midi"] > low_midi]
            high = min(upper or remaining, key=lambda t: score(t, high_target))
        else:
            low = high = pooled[0]
        shared = low is high
        if shared:
            high = dict(low)
        low_midi, high_midi = low.get("midi"), high.get("midi")
        if low_midi is not None and high_midi is not None and low_midi > high_midi:
            low, high = high, low
        low["role"] = "_L"
        high["role"] = "_H"
        selected = [low] if cfg.scarce_policy == "single_role" and shared else [low, high]

    scarce = len(takes) == 1 and len(selected) >= 1
    status = "scarce" if scarce else ("rescued" if rescued else "ok")
    best = min(selected, key=lambda t: score(t, note_to_midi(cfg.pitch_low)))
    rr = rerecord(best, cls, cfg, scarce=scarce or rescued)
    return selected, rejected, rr


# ------------------------------------------------------------------ outputs
def _atomic_text(path: str, text: str, encoding: str = "utf-8", newline: str = "\n") -> None:
    directory = os.path.dirname(os.path.abspath(path))
    os.makedirs(directory, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".tmp-", dir=directory)
    os.close(fd)
    try:
        with open(tmp, "w", encoding=encoding, errors="replace", newline=newline) as f:
            f.write(text)
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)


def _write_csv(path: str, fields: Sequence[str], rows: Sequence[dict]) -> None:
    directory = os.path.dirname(os.path.abspath(path))
    os.makedirs(directory, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".tmp-", suffix=".csv", dir=directory)
    os.close(fd)
    try:
        with open(tmp, "w", encoding="utf-8-sig", errors="replace", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
            writer.writeheader()
            for row in rows:
                writer.writerow({k: row.get(k, "") for k in fields})
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)


def _copy_atomic(src: str, dst: str) -> None:
    os.makedirs(os.path.dirname(os.path.abspath(dst)), exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".tmp-", suffix=".wav", dir=os.path.dirname(os.path.abspath(dst)))
    os.close(fd)
    try:
        shutil.copyfile(src, tmp)
        os.replace(tmp, dst)
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)


def note_range(split_note: str) -> Tuple[str, str]:
    """('C1-F#4', 'G4-B7') derived from the split note."""
    midi = note_to_midi(split_note)
    if midi is None:
        raise ValueError(f"split_note が音名ではありません: {split_note}")
    return f"C1-{midi_name(midi)}", f"{midi_name(midi + 1)}-B7"


def prefix_map_text(split_note: str, suffix_low: str = "_L", suffix_high: str = "_H") -> str:
    midi = note_to_midi(split_note)
    if midi is None:
        raise ValueError(f"split_note が音名ではありません: {split_note}")
    lines = []
    for m in range(note_to_midi("C1"), note_to_midi("B7") + 1):
        suffix = suffix_low if m <= midi else suffix_high
        lines.append(f"{midi_name(m)}\t\t{suffix}")
    return "\n".join(lines) + "\n"


def character_yaml(cfg: Config, subbanks: bool = True) -> str:
    head = (
        f"name: {cfg.name}\n"
        f"author: {cfg.author}\n"
        "singer_type: utau\n"
        "text_file_encoding: shift_jis\n"
        "default_phonemizer: OpenUtau.Plugin.Builtin.JapanesePresampPhonemizer\n"
    )
    if not subbanks:
        return head
    low, high = note_range(cfg.split_note)
    return head + (
        "subbanks:\n"
        "- color: ''\n"
        "  prefix: ''\n"
        f"  suffix: \"_L\"\n"
        "  tone_ranges:\n"
        f"  - {low}\n"
        "- color: ''\n"
        "  prefix: ''\n"
        f"  suffix: \"_H\"\n"
        "  tone_ranges:\n"
        f"  - {high}\n"
    )


def parse_oto(path: str) -> Dict[str, dict]:
    """basename -> first oto line (alias + timing). Timing is kept verbatim."""
    out = {}
    if not path or not os.path.isfile(path):
        return out
    with open(path, "r", encoding="cp932", errors="replace") as f:
        for line in f:
            line = line.rstrip("\r\n")
            if "=" not in line:
                continue
            name, rest = line.split("=", 1)
            parts = rest.split(",")
            if len(parts) < 5:
                continue
            key = os.path.basename(name.replace("/", "\\"))
            if key in out:
                continue
            values = []
            for value in parts[1:6]:
                try:
                    values.append(float(value))
                except ValueError:
                    values.append(0.0)
            while len(values) < 5:
                values.append(0.0)
            out[key] = {"alias": parts[0], **dict(zip(OTO_FIELDS, values))}
    return out


def fix_oto(values: dict, m: dict, cls: str) -> dict:
    """Apply the order's optional OTO corrections (3.7) to one line."""
    vals = {k: float(values.get(k, 0.0)) for k in OTO_FIELDS}
    duration = (m.get("duration_ms") or 0.0) / 1000.0 * 1000.0   # ms
    pre = vals["preutterance"]
    offset = vals["offset"]
    if cls in ("voiced_plosive", "voiced_fricative"):
        start = m.get("prevoicing_start_ms") if cls == "voiced_plosive" else m.get("voice_bar_start_ms")
        if start is not None:
            vals["offset"] = max(0.0, start - 5.0)
    elif m.get("lead_silence_ms") is not None:
        vals["offset"] = max(0.0, m["lead_silence_ms"] - 10.0)
    vals["consonant"] = pre + 15.0
    vowel_end = m.get("vowel_end_ms")
    if vowel_end is not None:
        keep_until = min(duration - 10.0, vowel_end + 20.0)
        want = -max(0.0, duration - keep_until)
        # A less negative cutoff keeps more audio; keep the longer usable region.
        vals["cutoff"] = max(vals["cutoff"], want) if vals["cutoff"] < 0 else want
    span = max(0.0, pre - vals["offset"])
    vals["overlap"] = min(max(0.0, vals["overlap"]), span * 0.6)
    return vals


def run(root: str, out: str, cfg: Optional[Config] = None, oto: str = "",
        fix: bool = False, force: bool = False, progress=None) -> dict:
    """Analyse ``root`` and write the curated bank to ``out``.

    ``progress(done, total, wav)`` is optional and only used by the GUI.
    """
    cfg = cfg or Config()
    if cfg.mode not in MODES:
        raise ValueError(f"mode は {', '.join(MODES)} のどれかです: {cfg.mode}")
    if not os.path.isdir(root):
        raise ValueError(f"フォルダがありません: {root}")
    wavs = sorted(f for f in os.listdir(root) if f.lower().endswith(".wav"))
    if not wavs:
        raise ValueError(f"WAVがありません: {root}")
    if os.path.isdir(out) and os.listdir(out):
        if not force:
            raise ValueError(f"出力先が空ではありません（--force で上書き）: {out}")

    oto_rows = parse_oto(oto or os.path.join(root, "oto.ini"))
    room_tone_db = None
    if cfg.room_tone and os.path.isfile(cfg.room_tone):
        rt, _ = read_wav_mono(cfg.room_tone)
        if len(rt):
            room_tone_db = 20.0 * math.log10(max(float(np.sqrt(np.mean(rt ** 2))), 1e-9))

    measured = []
    for i, name in enumerate(wavs):
        path = os.path.join(root, name)
        info = analyse(path, room_tone_db)
        alias, note, number = split_name(name)
        if name in oto_rows:
            alias = re.sub(r"_[LH][0-9]*$", "", oto_rows[name]["alias"].strip()) or alias
        info.update({"path": path, "alias": alias, "note": note, "number": number,
                     "midi": note_to_midi(note), "kind": phoneme_class(alias)})
        info["reasons"] = hard_reasons(info, cfg)
        measured.append(info)
        if progress:
            progress(i + 1, len(wavs), name)

    by_alias: Dict[str, List[dict]] = {}
    for info in measured:
        by_alias.setdefault(info["alias"], []).append(info)

    selected, rejected, rerecords, alias_report = [], [], [], []
    for alias in sorted(by_alias):
        takes = by_alias[alias]
        picks, drops, rr = select_for_alias(alias, takes, cfg)
        picked_ids = {id(t) for t in picks}
        for t in picks:
            t["alias"] = alias
            selected.append(t)
        for t in takes:
            if id(t) in picked_ids:
                continue
            t["alias"] = alias
            if not t["reasons"]:
                t["reasons"] = ["選抜外（同エイリアスの他テイクを採用）"]
            rejected.append(t)
        scarce = len(takes) == 1 and len({t["wav"] for t in picks}) == 1
        if rr:
            best = min(picks, key=lambda t: take_score(t, t["kind"], note_to_midi(cfg.pitch_low), cfg))
            rerecords.append({"alias": alias, "priority": rr["priority"], "kind": t_kind(best),
                              "best_wav": best["wav"], "jump_db": best.get("jump_db"),
                              "prevoicing_ms": best.get("prevoicing_ms"),
                              "voice_bar_ms": best.get("voice_bar_ms"),
                              "vowel_ms": best.get("vowel_ms"),
                              "reason": rr["reason"], "target": rr["target"]})
        alias_report.append({
            "alias": alias,
            "status": "scarce" if scarce else ("rescued" if any(t["reasons"] for t in picks) else "ok"),
            "selected": [{"wav": t["wav"], "role": t["role"], "midi": t.get("midi"),
                          "jump_db": t.get("jump_db"), "prevoicing_ms": t.get("prevoicing_ms"),
                          "score": round(take_score(t, t["kind"],
                                                    role_target(t["role"], cfg), cfg), 2)}
                         for t in picks],
            "rejected": [{"wav": t["wav"], "reasons": t["reasons"]} for t in takes
                         if id(t) not in picked_ids],
        })

    if not selected:
        raise RuntimeError("選抜結果が0件です")

    # --- write the folder -------------------------------------------------
    two_take = cfg.mode == "two_take"
    os.makedirs(out, exist_ok=True)
    if force:
        # Remove only the files this tool owns, so a re-run cannot leave stale takes.
        shutil.rmtree(os.path.join(out, "wav"), ignore_errors=True)
        for name in ("oto.ini", "character.yaml", "prefix.map", "selection.csv",
                     "rejected.csv", "rerecord.csv", "features.csv", "manifest.json"):
            path = os.path.join(out, name)
            if os.path.isfile(path):
                os.remove(path)
    wav_dir = os.path.join(out, "wav")
    os.makedirs(wav_dir, exist_ok=True)
    copied = set()
    for t in selected:
        if t["wav"] in copied:
            continue
        _copy_atomic(t["path"], os.path.join(wav_dir, t["wav"]))
        copied.add(t["wav"])

    oto_lines = []
    for t in sorted(selected, key=lambda t: (t["alias"], t["role"])):
        role = t["role"] or ""
        # keep underscores out of the alias body but keep the _L/_H suffix
        alias = f"{t['alias'].replace('_', '')}{role}"
        source = oto_rows.get(t["wav"])
        vals = dict(source) if source else {}
        if fix and source:
            vals = fix_oto(vals, t, t["kind"])
        timing = [vals.get(k, 0.0) for k in OTO_FIELDS]
        oto_lines.append(f"{t['wav']}={alias}," + ",".join(f"{float(v):.3f}" for v in timing))
    _atomic_text(os.path.join(out, "oto.ini"), "\n".join(oto_lines) + "\n", "cp932", "\r\n")

    _atomic_text(os.path.join(out, "character.yaml"), character_yaml(cfg, two_take), "utf-8", "\n")
    if two_take and cfg.write_prefix_map:
        _atomic_text(os.path.join(out, "prefix.map"), prefix_map_text(cfg.split_note), "cp932", "\r\n")

    selection_rows = []
    for t in selected:
        selection_rows.append({
            "alias": t["alias"], "role": t["role"], "wav": t["wav"], "midi": t.get("midi"),
            "duration_ms": t.get("duration_ms"), "vowel_ms": t.get("vowel_ms"),
            "jump_db": t.get("jump_db"), "prevoicing_ms": t.get("prevoicing_ms"),
            "voice_bar_ms": t.get("voice_bar_ms"),
            "score": round(take_score(t, t["kind"], role_target(t["role"], cfg), cfg), 2),
            "flags": " / ".join(t["reasons"]),
        })
    _write_csv(os.path.join(out, "selection.csv"),
               ("alias", "role", "wav", "midi", "duration_ms", "vowel_ms", "jump_db",
                "prevoicing_ms", "voice_bar_ms", "score", "flags"), selection_rows)
    _write_csv(os.path.join(out, "rejected.csv"), ("wav", "alias", "reasons"),
               [{"wav": t["wav"], "alias": t["alias"], "reasons": " / ".join(t["reasons"])}
                for t in rejected])
    _write_csv(os.path.join(out, "rerecord.csv"),
               ("alias", "priority", "kind", "best_wav", "jump_db", "prevoicing_ms",
                "voice_bar_ms", "vowel_ms", "reason", "target"),
               sorted(rerecords, key=lambda r: (r["priority"], r["alias"])))

    # features.csv is useful for tuning and matches the reference data
    _write_csv(os.path.join(out, "features.csv"),
               ("wav", "alias", "note", "kind", "duration_ms", "peak_dbfs", "rms_dbfs",
                "noise_dbfs", "snr_db", "lead_silence_ms", "vowel_start_ms", "vowel_end_ms",
                "vowel_ms", "clip_count", "jump_db", "burst_ms", "burst_to_vowel_ms",
                "prevoicing_ms", "prevoicing_start_ms", "voice_bar_ms", "voice_bar_start_ms",
                "frication_ms", "frication_flatness", "voiced_frication_ratio"),
               measured)

    low, high = note_range(cfg.split_note)
    manifest = {
        "schema_version": SCHEMA_VERSION,
        "generated_at": datetime.datetime.now().astimezone().isoformat(timespec="seconds"),
        "source": os.path.abspath(root),
        "settings": {
            "mode": cfg.mode,
            "min_per_alias": cfg.min_per_alias,
            "scarce_policy": cfg.scarce_policy,
            "split_note": cfg.split_note,
            "write_prefix_map": cfg.write_prefix_map,
            "subbanks": ([{"suffix": "_L", "range": low}, {"suffix": "_H", "range": high}]
                         if two_take else []),
        },
        "summary": {
            "takes": len(measured),
            "aliases": len(alias_report),
            "selected": len(selected),
            "rejected": len(rejected),
            "rerecord": len(rerecords),
            "wavs_written": len(copied),
        },
        "aliases": alias_report,
    }
    _atomic_text(os.path.join(out, "manifest.json"),
                 json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", "utf-8", "\n")

    return {"out": out, "selected": selected, "rejected": rejected, "rerecords": rerecords,
            "aliases": alias_report, "manifest": manifest}


def t_kind(take: dict) -> str:
    return take.get("kind") or phoneme_class(str(take.get("alias") or ""))


# ---------------------------------------------------------------------- CLI
def load_config(path: str) -> Config:
    """YAML when PyYAML is available, otherwise JSON with the same keys."""
    if not path:
        return Config()
    with open(path, "r", encoding="utf-8") as f:
        text = f.read()
    if path.lower().endswith(".json"):
        data = json.loads(text)
    else:
        try:
            import yaml  # optional
        except ImportError as exc:
            raise ValueError("YAML設定にはPyYAMLが必要です。JSON設定を使ってください。") from exc
        data = yaml.safe_load(text) or {}
    if not isinstance(data, dict):
        raise ValueError("設定ファイルはマップ形式にしてください")
    return apply_config(Config(), data)


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="CV音源のテイク選抜（音素間引き）")
    parser.add_argument("--root", required=True, help="WAVが入ったフォルダ")
    parser.add_argument("--out", required=True, help="出力フォルダ（新規/空を推奨）")
    parser.add_argument("--oto", default="", help="既存oto.ini（任意。aliasとタイミングを引き継ぐ）")
    parser.add_argument("--mode", default=None, choices=MODES)
    parser.add_argument("--split-note", default="")
    parser.add_argument("--config", default="")
    parser.add_argument("--room-tone", default="")
    parser.add_argument("--name", default="")
    parser.add_argument("--author", default="")
    parser.add_argument("--no-prefix-map", action="store_true")
    parser.add_argument("--fix-oto", action="store_true", help="OTO補正（注文書3.7）を適用")
    parser.add_argument("--force", action="store_true", help="空でない出力先を上書き")
    parser.add_argument("--json", action="store_true", help="結果の要約をJSONで表示")
    args = parser.parse_args(argv)

    try:
        cfg = load_config(args.config)
    except Exception as exc:  # noqa: BLE001
        print("設定エラー:", exc)
        return 30
    cfg.mode = args.mode or cfg.mode
    cfg.split_note = args.split_note or cfg.split_note
    cfg.write_prefix_map = not args.no_prefix_map
    cfg.room_tone = args.room_tone or cfg.room_tone
    if args.name:
        cfg.name = args.name
    if args.author:
        cfg.author = args.author

    try:
        result = run(args.root, args.out, cfg, oto=args.oto, fix=args.fix_oto, force=args.force)
    except Exception as exc:  # noqa: BLE001
        print("失敗:", exc)
        return 30

    summary = result["manifest"]["summary"]
    if args.json:
        print(json.dumps(summary, ensure_ascii=False))
    else:
        print(f"テイク {summary['takes']} / エイリアス {summary['aliases']} / "
              f"採用 {summary['selected']} / 除外 {summary['rejected']} / "
              f"再録候補 {summary['rerecord']}")
        for r in result["rerecords"][:10]:
            print(f"  {r['priority']} {r['alias']}: {r['reason']}")
    empty = [a["alias"] for a in result["aliases"] if not a["selected"]]
    if empty:
        print("0テイクのエイリアス:", ", ".join(empty))
        return 20
    return 10 if result["rerecords"] else 0


if __name__ == "__main__":
    sys.exit(main())

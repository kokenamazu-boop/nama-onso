"""Nama Onso core: WAV facts, TIFA alignment, mora grouping, voicebank JSON.

No third-party dependencies. Audio is only read; the source file is never modified.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import shutil
import struct
import subprocess
import sys
import tempfile
import wave
from dataclasses import dataclass, field
from typing import Iterable, List, Optional, Sequence, Tuple

try:                      # optional: neural f0 needs numpy + onnxruntime + model files
    import f0_neural
except Exception:         # pragma: no cover - tool must work without the model
    f0_neural = None

VOWELS = set("aiueo")
SPECIAL = {"cl", "N", "AP", "SP", "sil", "br"}
SMALL_KANA = set("ゃゅょぁぃぅぇぉゎャュョァィゥェォヮ")
MORA_OWN = set("っッーんン")


# ---------------------------------------------------------------- WAV facts
@dataclass
class WavFacts:
    path: str
    sample_rate: int
    channels: int
    width: int
    frames: int
    peak: float
    rms: List[float]          # 10 ms envelope, mono-folded
    sha256: str

    @property
    def seconds(self) -> float:
        return self.frames / float(self.sample_rate)


def _iter_frames(raw: bytes, width: int) -> Iterable[float]:
    if width == 1:
        for (v,) in struct.iter_unpack("<B", raw):
            yield (v - 128) / 128.0
    elif width == 2:
        for (v,) in struct.iter_unpack("<h", raw):
            yield v / 32768.0
    elif width == 3:
        for i in range(0, len(raw), 3):
            v = raw[i] | (raw[i + 1] << 8) | (raw[i + 2] << 16)
            if v & 0x800000:
                v -= 1 << 24
            yield v / 8388608.0
    elif width == 4:
        for (v,) in struct.iter_unpack("<i", raw):
            yield v / 2147483648.0
    else:
        raise ValueError(f"unsupported sample width {width}")


def read_wav(path: str) -> WavFacts:
    with open(path, "rb") as f:
        data = f.read()
    with wave.open(path, "rb") as w:
        sr, ch, width, n = w.getframerate(), w.getnchannels(), w.getsampwidth(), w.getnframes()
        raw = w.readframes(n)
    hop = max(1, int(sr * 0.010))
    rms: List[float] = []
    acc, cnt = 0.0, 0
    peak = 0.0
    idx = 0
    for v in _iter_frames(raw, width):
        peak = max(peak, abs(v))
        acc += v * v
        cnt += 1
        if cnt >= hop * ch:
            rms.append((acc / cnt) ** 0.5)
            acc, cnt = 0.0, 0
    if cnt:
        rms.append((acc / cnt) ** 0.5)
    return WavFacts(path=path, sample_rate=sr, channels=ch, width=width, frames=n,
                    peak=peak, rms=rms, sha256=hashlib.sha256(data).hexdigest())


def peaks_for_display(facts: WavFacts, columns: int) -> List[Tuple[float, float]]:
    """Min/max per display column from the 10 ms envelope (approximate, display only)."""
    if columns <= 0 or not facts.rms:
        return []
    out = []
    n = len(facts.rms)
    for c in range(columns):
        a = c * n // columns
        b = max(a + 1, (c + 1) * n // columns)
        window = facts.rms[a:b]
        lo = min(window)
        out.append((-lo, lo))
    return out


PEAK_BUCKET_SECONDS = 0.001


def peak_bucket_seconds(facts: WavFacts) -> float:
    """Fine enough that a deep zoom still shows a real shape, bounded in size."""
    return max(0.00025, facts.seconds / 200000.0)


def build_peak_grid(facts: WavFacts, bucket_seconds: Optional[float] = None) -> List[Tuple[float, float]]:
    """Streaming min/max peaks for a smooth waveform display.

    Streams in 1 s chunks so a long take never has to be held in memory.
    """
    bucket_seconds = bucket_seconds or peak_bucket_seconds(facts)
    grid: List[Tuple[float, float]] = []
    with wave.open(facts.path, "rb") as w:
        sr, ch, width = w.getframerate(), w.getnchannels(), w.getsampwidth()
        per_bucket = max(1, int(sr * bucket_seconds)) * ch
        chunk_frames = max(per_bucket, sr)
        lo = hi = 0.0
        n = 0
        while True:
            raw = w.readframes(chunk_frames)
            if not raw:
                break
            for v in _iter_frames(raw, width):
                if v < lo:
                    lo = v
                elif v > hi:
                    hi = v
                n += 1
                if n >= per_bucket:
                    grid.append((lo, hi))
                    lo = hi = 0.0
                    n = 0
        if n:
            grid.append((lo, hi))
    return grid



# ------------------------------------------------------------ alignment lab
def parse_htk_lab(text: str) -> List[Tuple[float, float, str]]:
    """mvt HTK labels use 100 ns units."""
    segs = []
    for line in text.splitlines():
        parts = line.split()
        if len(parts) != 3:
            continue
        try:
            a, b = int(parts[0]) / 1e7, int(parts[1]) / 1e7
        except ValueError:
            continue
        segs.append((a, b, parts[2]))
    return segs


def voiced_range(rms: Sequence[float], floor_ratio: float = 0.005) -> Tuple[float, float]:
    """First/last 10 ms frame above a relative floor, in seconds.

    The floor is deliberately low: a fricative like /z/ or /s/ at the start of a phrase
    is quiet but is part of the consonant, and must not be trimmed away.
    """
    if not rms:
        return (0.0, 0.0)
    threshold = max(rms) * floor_ratio
    first = next((i for i, v in enumerate(rms) if v > threshold), 0)
    last = next((i for i in range(len(rms) - 1, -1, -1) if rms[i] > threshold), len(rms) - 1)
    return (first * 0.010, min((last + 1) * 0.010, len(rms) * 0.010))


# ------------------------------------------------------------------- models
@dataclass
class Unit:
    name: str
    phonemes: List[str]
    start: float
    end: float
    confidence: str = ""       # "" = auto, "checked" = user confirmed
    phoneme_starts: List[float] = field(default_factory=list)

    @property
    def duration(self) -> float:
        return max(0.0, self.end - self.start)


def copy_unit(u: "Unit") -> "Unit":
    return Unit(u.name, list(u.phonemes), u.start, u.end, u.confidence, list(u.phoneme_starts))


def group_units(segs: Sequence[Tuple[float, float, str]]) -> List[Unit]:
    """Fold phonemes into mora-sized units: close on a vowel, keep cl/N standalone."""
    units: List[Unit] = []
    pending: List[str] = []
    times: List[float] = []
    start = 0.0
    end = 0.0
    for a, b, name in segs:
        if name in ("SP", "AP", "sil", "br"):
            if pending:
                units.append(Unit("".join(pending), pending, start, a, "", times))
                pending, times = [], []
            continue
        if not pending:
            start = a
        pending.append(name)
        times.append(a)
        end = b
        if name in VOWELS:
            units.append(Unit(" ".join(pending), pending, start, end, "", times))
            pending, times = [], []
        elif name in ("cl", "N"):
            units.append(Unit(name, [name], start, end, "", times))
            pending, times = [], []
    if pending:
        units.append(Unit(" ".join(pending), pending, start, end, "", times))
    return units


def to_hiragana(text: str) -> str:
    """Katakana -> hiragana so names match typed lyrics in either script."""
    out = []
    for ch in text:
        code = ord(ch)
        out.append(chr(code - 0x60) if 0x30A1 <= code <= 0x30F6 else ch)
    return "".join(out)


def split_mora(lyrics: str) -> List[str]:
    """Split Japanese lyrics into mora. Small kana join the previous mora."""
    text = to_hiragana(lyrics)
    chars = [c for c in text if not c.isspace() and c not in "、。,.!?！？"]
    out: List[str] = []
    for c in chars:
        if c in SMALL_KANA and out:
            out[-1] += c
        else:
            out.append(c)
    return out


def kana_vowel(mora: str) -> str:
    """Vowel of a mora, from a small gojuon table. '' for っ/ん/ー/punctuation."""
    mora = to_hiragana(mora)
    if not mora:
        return ""
    table = {}
    for kana, vowels in (("あいうえお", "aiueo"), ("かきくけこ", "aiueo"), ("さしすせそ", "aiueo"),
                         ("たちつてと", "aiueo"), ("なにぬねの", "aiueo"), ("はひふへほ", "aiueo"),
                         ("まみむめも", "aiueo"), ("やゆよ", "auo"), ("らりるれろ", "aiueo"),
                         ("わを", "ao"), ("がぎぐげご", "aiueo"), ("ざじずぜぞ", "aiueo"),
                         ("だぢづでど", "aiueo"), ("ばびぶべぼ", "aiueo"), ("ぱぴぷぺぽ", "aiueo"),
                         ("ぁぃぅぇぉ", "aiueo"), ("ゃゅょ", "auo"), ("ゎ", "a")):
        for k, v in zip(kana, vowels):
            table[k] = v
    last = mora[-1]
    if last in table:
        return table[last]
    if last in SMALL_KANA and len(mora) > 1:
        return table.get(mora[-2], "")
    return ""


def kana_cons(mora: str) -> str:
    """Consonant group of a mora, using the aligner's phoneme symbols."""
    mora = to_hiragana(mora)
    if not mora:
        return ""
    if mora == "っ":
        return "cl"
    if mora == "ん":
        return "N"
    head = mora[0]
    if head == "し":
        return "sh"
    if head == "ち":
        return "ch"
    if head == "つ":
        return "ts"
    if head == "じ":
        return "j"
    if head == "ふ":
        return "f"
    for kana, cons in (("かきくけこ", "k"), ("さすせそ", "s"), ("たてと", "t"),
                       ("なにぬねの", "n"), ("はひへほ", "h"), ("まみむめも", "m"),
                       ("やゆよ", "y"), ("らりるれろ", "r"), ("わを", "w"),
                       ("がぎぐげご", "g"), ("ざずぜぞ", "z"), ("だぢづでど", "d"),
                       ("ばびぶべぼ", "b"), ("ぱぴぷぺぽ", "p")):
        if head in kana:
            if len(mora) > 1 and mora[1] in SMALL_KANA and cons in "kgsztdnhbmpr":
                return cons + "y"   # きゃ / しゃ / ちゃ ...
            return cons
    return ""


def phoneme_kana(phonemes: str) -> str:
    """Approximate kana for a phoneme string ("b a" -> ば, "cl" -> っ)."""
    parts = [p for p in str(phonemes or "").split() if p]
    if not parts:
        return ""
    if "cl" in parts:
        return "っ"
    if "N" in parts:
        return "ん"
    vowel = ""
    for p in reversed(parts):
        if p in VOWELS:
            vowel = p
            break
    cons = " ".join(p for p in parts if p not in VOWELS)
    rows = {"k": "かきくけこ", "g": "がぎぐげご", "s": "さしすせそ", "z": "ざじずぜぞ",
            "t": "たちつてと", "d": "だぢづでど", "n": "なにぬねの", "h": "はひふへほ",
            "b": "ばびぶべぼ", "p": "ぱぴぷぺぽ", "m": "まみむめも", "r": "らりるれろ"}
    order = "aiueo"
    if not vowel:
        return {"cl": "っ", "N": "ん"}.get(cons, "")
    if not cons:
        return {"a": "あ", "i": "い", "u": "う", "e": "え", "o": "お"}[vowel]
    small = {"a": "ゃ", "u": "ゅ", "e": "ぇ", "o": "ょ"}
    base = cons.split()[0]
    if base in ("sh", "ch", "j"):
        head = {"sh": "し", "ch": "ち", "j": "じ"}[base]
        return head if vowel == "i" else head + small.get(vowel, "")
    if base == "ts":
        return "つ" + {"a": "ぁ", "i": "ぃ", "u": "", "e": "ぇ", "o": "ぉ"}[vowel]
    if base == "f":
        return "ふ" + {"a": "ぁ", "i": "ぃ", "u": "", "e": "ぇ", "o": "ぉ"}[vowel]
    if base == "y":
        return {"a": "や", "u": "ゆ", "o": "よ"}.get(vowel, "い")
    if base == "w":
        return {"a": "わ", "o": "を"}.get(vowel, "う")
    if base.endswith("y") and base[:-1] in rows:
        head = rows[base[:-1]][1]           # ki/gi/shi...
        return head if vowel == "i" else head + small.get(vowel, "")
    if base in rows:
        return rows[base][order.index(vowel)]
    return ""


def display_name(name: str, phonemes: str = "") -> str:
    """Kana label for a candidate. Phoneme-only names fall back to their sound."""
    text = to_hiragana(str(name or "").strip())
    if text and all(("\u3041" <= c <= "\u309f") or c == "ー" for c in text):
        return text
    kana = phoneme_kana(phonemes or text)
    if kana:
        return kana
    # A romanised label like "ki" / "sha" / "cl" still has a kana reading.
    romaji = str(name or "").strip().lower()
    import re as _re
    m = _re.fullmatch(r"(ky|gy|sh|ch|ts|ny|hy|by|py|my|ry|cl|n|[kgsztdnhbmprywf])?([aiueo])", romaji)
    if m:
        cons = m.group(1) or ""
        if cons == "n":
            cons = "N"
        return phoneme_kana(f"{cons} {m.group(2)}".strip())
    return text


def align_names(units: Sequence["Unit"], lyrics: str, skip_cost: float = 2.0,
                mismatch_cost: float = 3.0) -> Tuple[List[str], float]:
    """Assign kana to units when the counts differ (insertions/deletions).

    Order-preserving alignment: a unit and a mora match when their vowels agree
    (っ↔cl, ん↔N). Unmatched units get the previous name (long vowel) or "".
    Returns (names, matched_ratio).
    """
    mora = split_mora(lyrics)
    if not units or not mora:
        return [], 0.0
    n, m = len(units), len(mora)
    unit_vowel = []
    unit_cons = []
    unit_special = []
    for u in units:
        vowel = ""
        for p in reversed(u.phonemes):
            if p in VOWELS:
                vowel = p
                break
        unit_vowel.append(vowel)
        unit_cons.append(" ".join(p for p in u.phonemes if p not in VOWELS))
        unit_special.append("cl" in u.phonemes)
    mora_vowel = [kana_vowel(k) for k in mora]
    mora_cons = [kana_cons(k) for k in mora]
    mora_special = [k == "っ" for k in mora]
    inf = float("inf")
    cost = [[inf] * (m + 1) for _ in range(n + 1)]
    back = [[None] * (m + 1) for _ in range(n + 1)]
    cost[0][0] = 0.0
    for i in range(n + 1):
        for j in range(m + 1):
            here = cost[i][j]
            if here == inf:
                continue
            # Matching first, so an equal-cost tie prefers a pair.
            if i < n and j < m:
                if unit_special[i] and mora_special[j]:
                    step = 0.0
                elif unit_special[i] or mora_special[j]:
                    step = mismatch_cost
                elif unit_vowel[i] and unit_vowel[i] == mora_vowel[j]:
                    if mora_cons[j] == "" and unit_cons[i] != "":
                        step = 1.0      # an extra consonant on the unit
                    elif unit_cons[i].startswith(mora_cons[j]):
                        step = 0.0
                    else:
                        step = 1.0
                else:
                    step = mismatch_cost
                if here + step < cost[i + 1][j + 1]:
                    cost[i + 1][j + 1] = here + step
                    back[i + 1][j + 1] = (i, j, True)
            if i < n and here + skip_cost < cost[i + 1][j]:
                cost[i + 1][j] = here + skip_cost
                back[i + 1][j] = (i, j, False)
            if j < m and here + skip_cost < cost[i][j + 1]:
                cost[i][j + 1] = here + skip_cost
                back[i][j + 1] = (i, j, False)
    if cost[n][m] == inf:
        return [], 0.0
    names = [""] * n
    matched = 0
    i, j = n, m
    while i > 0 and j >= 0:
        step = back[i][j]
        if step is None:
            break
        pi, pj, paired = step
        if paired:
            names[pi] = mora[pj]
            matched += 1
        i, j = pi, pj
    # A unit with no mora continues the previous vowel, so reuse that name.
    for k in range(1, n):
        if not names[k] and names[k - 1]:
            names[k] = names[k - 1]
    return names, matched / float(n)


def name_units(units: Sequence[Unit], lyrics: str) -> Tuple[List[Unit], str]:
    """Assign kana names; falls back to alignment when the counts differ."""
    mora = split_mora(lyrics)
    out = []
    if len(mora) == len(units):
        for m, u in zip(mora, units):
            n = copy_unit(u)
            n.name = m
            out.append(n)
        return out, ""
    names, ratio = align_names(units, lyrics)
    if ratio >= 0.8:
        for n, u in zip(names, units):
            c = copy_unit(u)
            c.name = n or u.name
            out.append(c)
        return out, f"モーラ数が合わないため位置合わせで命名（一致 {ratio * 100:.0f}%）"
    return list(units), f"モーラ数が合いません（歌詞 {len(mora)} / 推定 {len(units)}）。名前は音素のままです。"


def trim_silence(units: Sequence[Unit], facts: WavFacts) -> Tuple[List[Unit], str]:
    """Pull the first/last boundary off silence only when the aligner glued silence in.

    TIFA sometimes starts the first consonant at 0.000 with no leading SP. If it already
    emitted a leading SP (first unit starts later), leave that boundary alone: a quiet
    onset like /m/ has little energy and an energy-based trim would eat the consonant.
    """
    if not units:
        return [], ""
    first, last = voiced_range(facts.rms)
    out = [copy_unit(u) for u in units]
    notes = []
    if out[0].start <= 0.005 and first > out[0].start + 0.015:
        notes.append(f"先頭の無音 {out[0].start:.3f}→{first:.3f} 秒")
        out[0].start = first
    if out[-1].end >= facts.seconds - 0.005 and last < out[-1].end - 0.015:
        notes.append(f"末尾の無音 {out[-1].end:.3f}→{last:.3f} 秒")
        out[-1].end = min(facts.seconds, last)
    return out, ("／".join(notes))


# ------------------------------------------------------------------ aligning
def run_align(wav_path: str, lyrics: str, model: str = "tifa-1.0-st", lang: str = "ja") -> List[Tuple[float, float, str]]:
    """Run `mvt label` on a temporary copy; returns raw phoneme segments."""
    work = tempfile.mkdtemp(prefix="jinriki-align-")
    try:
        base = os.path.splitext(os.path.basename(wav_path))[0]
        copy = os.path.join(work, base + ".wav")
        shutil.copyfile(wav_path, copy)
        with open(os.path.join(work, base + ".txt"), "w", encoding="utf-8", newline="\n") as f:
            f.write(lyrics)
        exe = shutil.which("mvt")
        if not exe:
            import appenv
            exe = appenv.find_mvt()
        if not exe:
            import appenv
            raise RuntimeError(appenv.MVT_HINT)
        # On Windows a windowed build must not flash a console for the child.
        flags = 0
        if os.name == "nt":
            flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        proc = subprocess.run([exe, "label", work, "-m", model, "-l", lang, "-f", "htk"],
                              capture_output=True, text=True, encoding="utf-8", errors="replace",
                              creationflags=flags)
        htk = os.path.join(work, "htk")
        lab = None
        if os.path.isdir(htk):
            for name in os.listdir(htk):
                if name.lower().endswith(".lab"):
                    lab = os.path.join(htk, name)
                    break
        if not lab:
            raise RuntimeError("音素ラベルを作れませんでした: " + (proc.stderr or proc.stdout)[-400:])
        with open(lab, "r", encoding="utf-8") as f:
            return parse_htk_lab(f.read())
    finally:
        shutil.rmtree(work, ignore_errors=True)


# ------------------------------------------------------------------ voicebank
VOICEBANK_VERSION = 1


def export_phoneme_starts(unit: Unit) -> List[float]:
    """Absolute source seconds, preserving inner boundaries after range edits.

    Do not invent inner timings for a legacy/malformed unit. The reader must be
    able to distinguish unknown vowel timing from a vowel starting at zero.
    """
    if len(unit.phoneme_starts) != len(unit.phonemes) or not unit.phonemes:
        return []
    times = [unit.start] + list(unit.phoneme_starts[1:])
    if not all(math.isfinite(t) and unit.start <= t < unit.end for t in times):
        return []
    if any(b <= a for a, b in zip(times, times[1:])):
        return []
    return [round(t, 6) for t in times]


def make_voicebank(facts: WavFacts, units: Sequence[Unit], lyrics: str, model: str,
                   pitches: Optional[Sequence[dict]] = None, song: str = "") -> dict:
    pitches = list(pitches) if pitches else [None] * len(units)
    return {
        "format": "nama-voices",
        "version": VOICEBANK_VERSION,
        "source": {
            "path": facts.path,
            "sha256": facts.sha256,
            "sampleRate": facts.sample_rate,
            "channels": facts.channels,
            "bits": facts.width * 8,
            "frames": facts.frames,
            "seconds": round(facts.seconds, 6),
        },
        "lyrics": lyrics,
        "song": song,
        "model": model,
        "units": [
            {
                "id": f"u{i + 1:03d}",
                "name": u.name,
                "phonemes": " ".join(u.phonemes),
                "phoneme_starts": export_phoneme_starts(u),
                "start": round(max(0.0, min(u.start, facts.seconds)), 6),
                "end": round(max(0.0, min(u.end, facts.seconds)), 6),
                "confidence": u.confidence or "auto",
                "pitch": pitches[i] if i < len(pitches) and pitches[i] else {"midi": None, "hz": None, "strength": 0.0},
            }
            for i, u in enumerate(units)
        ],
    }


def save_voicebank(path: str, bank: dict) -> None:
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        json.dump(bank, f, ensure_ascii=False, indent=2)
        f.write("\n")


def load_voicebank(path: str) -> dict:
    with open(path, "r", encoding="utf-8") as f:
        bank = json.load(f)
    if bank.get("format") != "nama-voices" or bank.get("version") != VOICEBANK_VERSION:
        raise ValueError("未対応の素材集ファイルです。")
    return bank


def default_bank_path(wav_path: str) -> str:
    return os.path.splitext(wav_path)[0] + ".nama-voices.json"


# --------------------------------------------------------------- pitch (f0)
def read_mono(facts: "WavFacts", t0: float, t1: float, target_sr: int = 8000) -> List[float]:
    """Read a time range as mono floats, decimated to about target_sr (display/analysis only)."""
    with wave.open(facts.path, "rb") as w:
        sr, ch, width = w.getframerate(), w.getnchannels(), w.getsampwidth()
        start = max(0, int(t0 * sr))
        count = max(0, int((t1 - t0) * sr))
        w.setpos(min(start, max(0, w.getnframes() - 1)))
        raw = w.readframes(count)
    step = max(1, int(round(sr / float(target_sr))))
    out: List[float] = []
    acc, n = 0.0, 0
    for v in _iter_frames(raw, width):
        acc += v
        n += 1
        if n == ch * step:
            out.append(acc / n)
            acc, n = 0.0, 0
    return out


def estimate_f0(samples: Sequence[float], sr: float, fmin: float = 60.0, fmax: float = 900.0,
                threshold: float = 0.15) -> Tuple[float, float]:
    """YIN pitch estimate for one frame. Returns (hz, aperiodicity).

    Plain autocorrelation picks octave-low lags on real voices, so use the cumulative
    mean normalized difference function and take the first dip below `threshold`.
    Lower aperiodicity means a clearer pitch.
    """
    n = len(samples)
    if n < 256:
        return 0.0, 1.0
    mean = sum(samples) / n
    x = [v - mean for v in samples]
    if sum(v * v for v in x) <= 1e-9:
        return 0.0, 1.0
    lag_min = max(2, int(sr / fmax))
    lag_max = min(n // 2, int(sr / fmin))
    if lag_max <= lag_min:
        return 0.0, 1.0
    d = [0.0] * (lag_max + 1)
    for lag in range(lag_min, lag_max + 1):
        acc = 0.0
        for i in range(n - lag):
            diff = x[i] - x[i + lag]
            acc += diff * diff
        d[lag] = acc
    # cumulative mean normalized difference
    cmnd = [1.0] * (lag_max + 1)
    running = 0.0
    for lag in range(1, lag_max + 1):
        running += d[lag]
        cmnd[lag] = d[lag] * lag / running if running > 0 else 1.0
    best = -1
    for lag in range(lag_min, lag_max):
        if cmnd[lag] < threshold and cmnd[lag] <= cmnd[lag + 1]:
            best = lag
            break
    if best < 0:
        best = min(range(lag_min, lag_max + 1), key=lambda k: cmnd[k])
        if cmnd[best] > 0.35:
            return 0.0, cmnd[best]
    # parabolic interpolation around the dip
    lag = float(best)
    if lag_min < best < lag_max:
        a, b, c = cmnd[best - 1], cmnd[best], cmnd[best + 1]
        denom = (a - 2 * b + c)
        if abs(denom) > 1e-12:
            lag += 0.5 * (a - c) / denom
    return sr / lag, max(0.0, min(1.0, cmnd[best]))


def _pitch_frames(samples: Sequence[float], sr: float, win: int, hop: int,
                  threshold: float, max_ap: float) -> Tuple[list, list, list]:
    values, strengths, times = [], [], []
    if win <= 0 or len(samples) < win:
        return values, strengths, times
    for start in range(0, max(1, len(samples) - win + 1), hop):
        hz, ap = estimate_f0(samples[start:start + win], sr, threshold=threshold)
        if 55.0 <= hz <= 1000.0 and ap <= max_ap:
            values.append(hz)
            strengths.append(ap)
            times.append((start + win * 0.5) / sr)   # frame centre
    return values, strengths, times


def _curve_points(pairs: Sequence[Tuple[float, float]], unit_start: float, unit_end: float,
                  max_points: int = 120) -> List[list]:
    """[[t relative to the unit start, Hz], ...] limited in count."""
    span = unit_end - unit_start
    out = [[round(t, 3), round(hz, 1)] for t, hz in pairs if 0.0 <= t <= span and 55.0 <= hz <= 1500.0]
    if len(out) > max_points:
        step = len(out) / float(max_points)
        out = [out[min(len(out) - 1, int(i * step))] for i in range(max_points)]
    return out


def _neural_pitch(facts: "WavFacts", begin: float, end: float, vowel_start: float,
                  unit_start: float, unit_end: float) -> Optional[dict]:
    """FCPE estimate over [begin, end], summarised from the vowel onset onwards."""
    if f0_neural is None:
        return None
    try:
        model = f0_neural.shared()
    except Exception:
        return None
    if model is None:
        return None
    samples = read_mono(facts, begin, end, int(facts.sample_rate))
    if len(samples) < int(facts.sample_rate * 0.04):
        return None
    try:
        frames = model.f0(samples, facts.sample_rate)
    except Exception:
        return None
    if not frames:
        return None
    result = f0_neural.summarise(frames, 0.010, max(0.0, vowel_start - begin))
    if result["hz"] is None or not (55.0 <= result["hz"] <= 1500.0) or result["voiced"] < 0.25:
        return None
    offset = begin - unit_start
    result["curve"] = _curve_points([(offset + i * 0.010, hz) for i, hz in enumerate(frames)],
                                    unit_start, unit_end)
    result["source"] = "fcpe"
    return result


def unit_pitch(facts: "WavFacts", unit: "Unit") -> dict:
    """Median f0 over the vowel, extending backwards when the vowel is short.

    The vowel onset from the alignment is preferred so consonants and stop
    closures stay out. A vowel under ~60 ms leaves too few samples for one
    frame, so the analysis window is allowed to reach back into the consonant
    (voiced ones help, unvoiced ones are rejected by the periodicity check).
    """
    span = unit.end - unit.start
    if span < 0.05:
        return {"midi": None, "hz": None, "strength": 0.0}
    # Closures, っ and ん carry no sung pitch; reporting one would be a false value.
    if not any(p in VOWELS for p in unit.phonemes):
        return {"midi": None, "hz": None, "strength": 0.0}
    target = 8000  # proven stable for this material; 16 kHz shifted YIN's dip choice
    # read_mono averages `step` frames, so 44.1 kHz became 7350 Hz. Use the real rate.
    step = max(1, int(round(facts.sample_rate / float(target))))
    sr = facts.sample_rate / float(step)
    has_starts = len(unit.phoneme_starts) == len(unit.phonemes) and unit.phonemes
    vowel_start = unit.phoneme_starts[-1] if has_starts and unit.phonemes[-1] in VOWELS \
        else unit.start + span * 0.35
    vowel_start = max(unit.start, min(vowel_start, unit.end))
    pad = max(0.0, 0.060 - (unit.end - vowel_start))
    begin = max(0.0, vowel_start - pad)
    if unit.end - begin < 0.03:
        begin = unit.start + span * 0.35
    samples = read_mono(facts, begin, unit.end, target)
    if len(samples) < 200:
        return {"midi": None, "hz": None, "strength": 0.0}
    neural = _neural_pitch(facts, begin, unit.end, vowel_start, unit.start, unit.end)
    if neural is not None:
        return neural
    values: list = []
    strengths: list = []
    times: list = []
    # Prefer the longest window that fits; fall back to a shorter one only for
    # short vowels, and require stronger periodicity there.
    for win, strict_ap, relaxed_ap, relaxed_threshold in ((512, 0.25, 0.40, 0.30),
                                                          (256, 0.25, 0.35, 0.25)):
        if len(samples) < win:
            continue
        hop = max(64, win // 4)
        values, strengths, times = _pitch_frames(samples, sr, win, hop, 0.15, strict_ap)
        if not values and relaxed_ap > 0:
            values, strengths, times = _pitch_frames(samples, sr, win, hop * 2, relaxed_threshold, relaxed_ap)
        if values:
            break
    if not values:
        return {"midi": None, "hz": None, "strength": 0.0}
    ordered = sorted(values)
    hz = ordered[len(ordered) // 2]
    midi = 69.0 + 12.0 * math.log2(hz / 440.0)
    offset = begin - unit.start
    return {"midi": round(midi, 2), "hz": round(hz, 1),
            "strength": round(1.0 - sum(strengths) / len(strengths), 2), "source": "yin",
            "curve": _curve_points([(offset + t, h) for t, h in zip(times, values)],
                                   unit.start, unit.end)}



REAPER_HEADER = '<REAPER_PROJECT 0.1 "7.82/win64" 1792138488 0'


def write_reaper_project(path: str, wav_path: str, facts: "WavFacts", units: Sequence[Unit]) -> None:
    """Write a review project: one region per candidate + a marker per phoneme boundary.

    Regions show the material range (so consonants are inside the block).
    Markers show where each phoneme starts, so a missed consonant is visible.
    """
    lines = [REAPER_HEADER, "  RIPPLE 0", "  GROUPOVERRIDE 0 0 0",
             "  AUTOXFADE 1", "  <TRACK", '    NAME "%s"' % os.path.basename(wav_path),
             "    <ITEM", "      POSITION 0", "      LENGTH %.6f" % facts.seconds,
             "      <SOURCE WAVE", '        FILE "%s"' % wav_path.replace('"', "'"),
             "      >", "    >", "  >"]
    index = 0
    for u in units:
        index += 1
        lines.append('  MARKER %d %.4f "%s" 1 %.4f' % (index, u.start, u.name, u.end))
    for u in units:
        if len(u.phonemes) < 2:
            continue
        for i, ph in enumerate(u.phonemes):
            t = u.start if i == 0 else (u.phoneme_starts[i] if i < len(u.phoneme_starts) else u.start)
            index += 1
            lines.append('  MARKER %d %.4f "%s" 0 0' % (index, t, ph))
    lines.append(">")
    with open(path, "w", encoding="utf-8", newline="\r\n") as f:
        f.write("\n".join(lines) + "\n")

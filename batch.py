"""Batch-align a folder of WAVs into one voicebank JSON per file.

Lyrics come from ``<name>.txt`` next to the WAV (first non-empty line), otherwise
from the file name itself. Source audio is only read; existing JSONs are not
overwritten unless ``--force`` is given.

Usage:
    python batch.py FOLDER [--force] [--limit N] [--dry-run]
    python batch.py FOLDER --lyrics-from txt   # never use names
"""
from __future__ import annotations

import argparse
import glob
import hashlib
import os
import sys
from typing import Callable, Dict, List, Optional, Tuple

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import core  # noqa: E402

AUDIO_EXT = (".wav",)


def lyrics_for(wav: str, mode: str = "auto", separator: str = "_") -> str:
    """Sidecar text wins; then (auto/name) the part after the last separator.

    ``曲名_歌詞.wav`` -> ``歌詞``. A name without the separator is used whole.
    """
    stem = os.path.splitext(os.path.basename(wav))[0]
    sidecar = os.path.join(os.path.dirname(wav), stem + ".txt")
    if os.path.isfile(sidecar):
        with open(sidecar, "r", encoding="utf-8-sig") as f:
            for line in f:
                line = line.strip()
                if line:
                    return line
    if mode == "txt":
        return ""
    if mode == "stem":
        return stem
    if separator and separator in stem:
        tail = stem.rpartition(separator)[2].strip()
        if tail:
            return tail
    return stem


def song_for(wav: str, lyrics: str, separator: str = "_") -> str:
    """Song name from ``曲名_歌詞.wav`` (the part before the lyric)."""
    stem = os.path.splitext(os.path.basename(wav))[0]
    if lyrics and stem.endswith(lyrics):
        head = stem[:-len(lyrics)]
        if separator and head.endswith(separator):
            head = head[:-len(separator)]
        return head.strip(" _-")
    return ""


def process_one(wav: str, lyrics: str, model: str = "tifa-1.0-st",
                aligner: Optional[Callable] = None, separator: str = "_") -> Dict:
    """Align one file and build the bank dict. Raises on failure."""
    aligner = aligner or core.run_align
    before = hashlib.sha256(open(wav, "rb").read()).hexdigest()
    facts = core.read_wav(wav)
    segs = aligner(wav, lyrics, model)
    units = core.group_units(segs)
    if not units:
        raise RuntimeError("音素区間を作れませんでした")
    units, warn = core.name_units(units, lyrics)
    units, trim_note = core.trim_silence(units, facts)
    pitches = [core.unit_pitch(facts, u) for u in units]
    bank = core.make_voicebank(facts, units, lyrics, model, pitches,
                               song=song_for(wav, lyrics, separator))
    after = hashlib.sha256(open(wav, "rb").read()).hexdigest()
    if before != after:
        raise RuntimeError("元のWAVが変更されました")
    with_pitch = sum(1 for p in pitches if p.get("midi") is not None)
    return {"bank": bank, "units": len(units), "pitch": with_pitch,
            "warn": warn, "trim": trim_note, "seconds": facts.seconds}


def existing_banks(folder: str) -> Dict[str, str]:
    """Map source sha256 -> bank JSON, so a renamed copy is still recognised."""
    found: Dict[str, str] = {}
    for path in glob.glob(os.path.join(folder, "*.nama-voices.json")):
        try:
            bank = core.load_voicebank(path)
        except Exception:  # noqa: BLE001 - ignore unreadable banks
            continue
        digest = str(bank.get("source", {}).get("sha256", "")).lower()
        if digest:
            found.setdefault(digest, path)
    return found


def run_folder(folder: str, force: bool = False, limit: int = 0, dry_run: bool = False,
               lyrics_mode: str = "auto", model: str = "tifa-1.0-st",
               aligner: Optional[Callable] = None, separator: str = "_") -> List[Dict]:
    files = sorted(f for f in os.listdir(folder) if f.lower().endswith(AUDIO_EXT))
    if limit > 0:
        files = files[:limit]
    results: List[Dict] = []
    done = {} if force else existing_banks(folder)
    for name in files:
        wav = os.path.join(folder, name)
        out = core.default_bank_path(wav)
        lyrics = lyrics_for(wav, lyrics_mode, separator)
        row = {"file": name, "lyrics": lyrics, "units": 0, "pitch": 0,
               "status": "", "note": ""}
        if not force:
            if os.path.exists(out):
                row["status"] = "既存のためスキップ"
                results.append(row)
                continue
            try:
                digest = hashlib.sha256(open(wav, "rb").read()).hexdigest().lower()
            except OSError:
                digest = ""
            known = done.get(digest) if digest else None
            if known:
                row["status"] = "既存(同じ音声)のためスキップ"
                row["note"] = "元: " + os.path.basename(known)
                results.append(row)
                continue
        if not lyrics:
            row["status"] = "歌詞なし"
            results.append(row)
            continue
        if not any("\u3040" <= ch <= "\u30ff" for ch in lyrics):
            # Guards against "曲名_(Dry)" style tails being read as lyrics.
            row["status"] = "歌詞がかなでない"
            results.append(row)
            continue
        if os.path.exists(out) and not force:
            row["status"] = "既存のためスキップ"
            results.append(row)
            continue
        if dry_run:
            row["status"] = "予定"
            results.append(row)
            continue
        try:
            info = process_one(wav, lyrics, model, aligner, separator)
            core.save_voicebank(out, info["bank"])
            row.update(units=info["units"], pitch=info["pitch"], status="OK",
                       note=" ".join(x for x in (info["warn"], info["trim"]) if x))
        except Exception as e:  # noqa: BLE001 - report and continue with the rest
            row["status"] = "失敗"
            row["note"] = str(e)
        results.append(row)
    return results


def write_report(folder: str, results: List[Dict]) -> str:
    path = os.path.join(folder, "batch-report.tsv")
    lines = ["file\tlyrics\tunits\tpitch\tstatus\tnote"]
    for r in results:
        lines.append("\t".join(str(r[k]).replace("\t", " ") for k in
                               ("file", "lyrics", "units", "pitch", "status", "note")))
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write("\n".join(lines) + "\n")
    return path


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("folder")
    parser.add_argument("--force", action="store_true", help="既存JSONを上書きする")
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--dry-run", action="store_true", help="何をするかだけ表示")
    parser.add_argument("--lyrics-from", choices=["auto", "name", "stem", "txt"], default="auto",
                        help="auto/name=「曲名_歌詞」の右側 / stem=ファイル名そのまま / txt=サイドカー必須")
    parser.add_argument("--separator", default="_", help="曲名と歌詞の区切り（既定 _）")
    parser.add_argument("--model", default="tifa-1.0-st")
    args = parser.parse_args(argv)
    if not os.path.isdir(args.folder):
        print("フォルダがありません:", args.folder)
        return 2
    results = run_folder(args.folder, args.force, args.limit, args.dry_run,
                         args.lyrics_from, args.model, separator=args.separator)
    for r in results:
        print(f"{r['status']:<14} {r['file']}  units={r['units']} pitch={r['pitch']} {r['note']}")
    ok = sum(1 for r in results if r["status"] == "OK")
    failed = sum(1 for r in results if r["status"] == "失敗")
    print(f"\n{ok} / {len(results)} 件を書き出しました。")
    if not args.dry_run and results:
        print("レポート:", write_report(args.folder, results))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())

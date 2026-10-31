"""Report how well a set of voicebanks covers the sounds a song needs.

Reads ``*.nama-voices.json`` only; no audio is modified.

Usage:
    python coverage.py FOLDER
    python coverage.py FOLDER --target "みえてるずっとさきで"
    python coverage.py FOLDER --target-file lyric.txt --json
"""
from __future__ import annotations

import argparse
import collections
import glob
import json
import os
import statistics
import sys
from typing import Dict, List, Optional

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import core  # noqa: E402


def load_banks(folder: str) -> List[dict]:
    out = []
    for path in sorted(glob.glob(os.path.join(folder, "*.nama-voices.json"))):
        try:
            out.append(core.load_voicebank(path))
        except Exception as e:  # noqa: BLE001 - one bad file must not stop the report
            print(f"skip {os.path.basename(path)}: {e}", file=sys.stderr)
    return out


def collect(banks: List[dict]) -> Dict[str, List[dict]]:
    by: Dict[str, List[dict]] = collections.defaultdict(list)
    for bank in banks:
        for u in bank["units"]:
            pitch = (u.get("pitch") or {})
            by[u["name"]].append({
                "midi": pitch.get("midi"),
                "seconds": max(0.0, float(u["end"]) - float(u["start"])),
                "bank": os.path.basename(bank.get("source", {}).get("path", "")),
            })
    return dict(by)


def summarise(by: Dict[str, List[dict]]) -> dict:
    rows = {}
    for name, items in by.items():
        mids = [i["midi"] for i in items if i["midi"] is not None]
        lens = [i["seconds"] * 1000.0 for i in items]
        rows[name] = {
            "count": len(items),
            "with_pitch": len(mids),
            "pitch_min": round(min(mids), 2) if mids else None,
            "pitch_max": round(max(mids), 2) if mids else None,
            "pitch_spread": round(max(mids) - min(mids), 2) if len(mids) >= 2 else 0.0,
            "len_min_ms": round(min(lens)) if lens else 0,
            "len_max_ms": round(max(lens)) if lens else 0,
        }
    return rows


def target_morae(text: str) -> List[str]:
    return [m for m in core.split_mora(text) if m not in ("、", "。")] if text else []


# 清音46 + 濁音20 + 半濁音5。拗音・外来音は必要になったら足す。
STANDARD = (
    "あいうえお"
    "かきくけこさしすせそたちつてとなにぬねのはひふへほまみむめもやゆよらりるれろわをん"
    "がぎぐげござじずぜぞだぢづでどばびぶべぼ"
    "ぱぴぷぺぽ"
)


def report(folder: str, target: Optional[str] = None) -> dict:
    banks = load_banks(folder)
    by = collect(banks)
    rows = summarise(by)
    missing: List[str] = []
    if target:
        wanted = sorted(set(target_morae(target)))
        missing = [m for m in wanted if m not in rows]
        cover = [m for m in wanted if m in rows]
        coverage = {"wanted": len(wanted), "covered": len(cover), "missing": missing}
    else:
        coverage = {}
    return {
        "banks": len(banks),
        "candidates": sum(len(v) for v in by.values()),
        "distinct": len(by),
        "single": sorted(n for n, r in rows.items() if r["count"] == 1),
        "rows": dict(sorted(rows.items())),
        "coverage": coverage,
    }


def print_report(data: dict) -> None:
    print(f"素材集 {data['banks']} / 候補 {data['candidates']} / 発音 {data['distinct']} 種")
    spread = [r["pitch_spread"] for r in data["rows"].values() if r["pitch_spread"] and r["pitch_spread"] >= 2]
    both = [n for n, r in data["rows"].items()
            if r["pitch_spread"] >= 2.0 and r["len_max_ms"] - r["len_min_ms"] >= 40]
    print(f"2件以上: {sum(1 for r in data['rows'].values() if r['count'] >= 2)} 種 / "
          f"音程が2半音以上ばらつく: {len(spread)} 種 / 音程と長さの両方: {len(both)} 種")
    print(f"1件だけ: {len(data['single'])} 種  {' '.join(data['single'])}")
    cov = data.get("coverage") or {}
    if cov:
        print(f"対象の歌詞: {cov['covered']}/{cov['wanted']} 音が揃っています。")
        if cov["missing"]:
            print("  未収録:", " ".join(cov["missing"]))
    print()
    print(f"{'音':<4}{'件数':>4}{'音程':>7}{'幅':>6}{'長さ(ms)':>14}")
    for name, r in data["rows"].items():
        pitch = f"{r['pitch_min']:.1f}" if r["pitch_min"] is not None else "-"
        print(f"{name:<4}{r['count']:>4}{pitch:>7}{r['pitch_spread']:>6.1f}"
              f"{r['len_min_ms']:>7}..{r['len_max_ms']:<7}")


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("folder")
    parser.add_argument("--target", default="", help="対象の歌詞（かな）")
    parser.add_argument("--target-file", default="", help="歌詞を書いたテキスト")
    parser.add_argument("--standard", action="store_true", help="清音・濁音・半濁音の標準セットを対象にする")
    parser.add_argument("--json", action="store_true", help="JSONで出力")
    args = parser.parse_args(argv)
    target = args.target
    if args.target_file:
        with open(args.target_file, "r", encoding="utf-8-sig") as f:
            target = "".join(line.strip() for line in f)
    if args.standard:
        target = (target or "") + STANDARD
    data = report(args.folder, target or None)
    if args.json:
        print(json.dumps(data, ensure_ascii=False, indent=2))
    else:
        print_report(data)
    return 0


if __name__ == "__main__":
    sys.exit(main())

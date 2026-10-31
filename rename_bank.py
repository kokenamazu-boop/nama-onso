"""Re-derive kana names for an existing voicebank, writing a NEW file.

Useful when a phrase's mora count did not match the aligned units, so the
candidates kept phoneme names like "k a". Nothing else changes: boundaries,
pitch, curves and the source reference stay as they are.

Usage: python rename_bank.py BANK OUTPUT
       python rename_bank.py FOLDER OUT_FOLDER --all
"""
from __future__ import annotations

import argparse
import glob
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import core  # noqa: E402


def renamed(bank: dict) -> tuple[dict, str]:
    units = []
    for u in bank["units"]:
        units.append(core.Unit(
            u.get("name", ""), str(u.get("phonemes", "")).split(),
            float(u["start"]), float(u["end"]), u.get("confidence", ""),
            list(u.get("phoneme_starts") or [])))
    names, note = core.name_units(units, bank.get("lyrics", ""))
    out = dict(bank)
    out["units"] = []
    for src, new in zip(bank["units"], names):
        unit = dict(src)
        if new.name:
            unit["name"] = new.name
        out["units"].append(unit)
    return out, note


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source")
    parser.add_argument("output")
    parser.add_argument("--all", action="store_true", help="source is a folder")
    args = parser.parse_args(argv)
    if not args.all:
        if os.path.exists(args.output):
            raise ValueError("出力先が既に存在します。上書きしません。")
        bank = core.load_voicebank(args.source)
        result, note = renamed(bank)
        core.save_voicebank(args.output, result)
        print(f"{os.path.basename(args.output)}: {note or 'OK'}")
        return 0
    os.makedirs(args.output, exist_ok=True)
    done = skipped = 0
    for path in sorted(glob.glob(os.path.join(args.source, "*.nama-voices.json"))):
        out = os.path.join(args.output, os.path.basename(path))
        if os.path.exists(out):
            skipped += 1
            continue
        bank = core.load_voicebank(path)
        result, note = renamed(bank)
        core.save_voicebank(out, result)
        done += 1
        if note:
            print(f"  {os.path.basename(path)[:40]}: {note}")
    print(f"{done} 件を書き出しました（既存 {skipped} 件はそのまま）。")
    return 0


if __name__ == "__main__":
    sys.exit(main())

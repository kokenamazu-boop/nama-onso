"""Add phoneme times from an existing review RPP to a NEW voicebank copy.

No alignment rerun, source modifications, or existing voicebank overwrites.
Usage: python enrich_bank.py BANK REVIEW_RPP NEW_BANK
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
import math
import os
import re

import core


def enrich(bank: dict, review: str) -> dict:
    source = os.path.normcase(os.path.abspath(bank["source"]["path"]))
    files = re.findall(r'^\s*FILE "([^"]+)"', review, re.MULTILINE)
    if not any(os.path.normcase(os.path.abspath(p)) == source for p in files):
        raise ValueError("確認用プロジェクトの音声が素材集と一致しません。")
    points = []
    for match in re.finditer(r'^\s*MARKER\s+\d+\s+([\d.eE+-]+)\s+"([^"]*)"\s+([01])\b', review, re.MULTILINE):
        t, name, region = match.groups()
        if region == "0" and math.isfinite(float(t)):
            points.append((float(t), name))
    points.sort()
    result = copy.deepcopy(bank)
    for unit in result["units"]:
        # Keep already saved/manual inner timings; this is not re-analysis.
        if unit.get("phoneme_starts"):
            continue
        names = unit["phonemes"].split()
        if len(names) == 1:
            unit["phoneme_starts"] = [unit["start"]]
            continue
        selected = [(t, name) for t, name in points if unit["start"] - .00005 <= t < unit["end"] - .00005]
        if [name for _, name in selected] != names:
            raise ValueError(f"{unit['name']}: 音素列/範囲が一致しないため補完を中止しました。")
        times = [unit["start"]] + [t for t, _ in selected[1:]]
        u = core.Unit(unit["name"], names, unit["start"], unit["end"], phoneme_starts=times)
        times = core.export_phoneme_starts(u)
        if not times:
            raise ValueError(f"{unit['name']}: 音素時刻が範囲外または逆順です。")
        unit["phoneme_starts"] = times
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("bank")
    parser.add_argument("review_rpp")
    parser.add_argument("output")
    args = parser.parse_args()
    if os.path.exists(args.output):
        raise ValueError("出力先が既に存在します。上書きせず、別名を指定してください。")
    bank = core.load_voicebank(args.bank)
    with open(bank["source"]["path"], "rb") as f:
        if hashlib.sha256(f.read()).hexdigest().lower() != bank["source"]["sha256"].lower():
            raise ValueError("元音声のハッシュが一致しません。")
    with open(args.review_rpp, encoding="utf-8-sig") as f:
        result = enrich(bank, f.read())
    with open(args.output, "x", encoding="utf-8", newline="\n") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)
        f.write("\n")
    print(f"Saved new bank: {args.output}")


if __name__ == "__main__":
    main()

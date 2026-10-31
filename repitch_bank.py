"""Re-estimate only the pitch of an existing voicebank, into a NEW file.

No re-alignment, no boundary changes, no overwrite of the source JSON or WAV.
Usage: python repitch_bank.py BANK OUTPUT
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os

import core


def repitch(bank: dict, facts: "core.WavFacts") -> dict:
    result = json.loads(json.dumps(bank))
    for unit in result["units"]:
        names = unit["phonemes"].split()
        starts = unit.get("phoneme_starts") or []
        u = core.Unit(unit["name"], names, unit["start"], unit["end"],
                      phoneme_starts=list(starts))
        unit["pitch"] = core.unit_pitch(facts, u)
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("bank")
    parser.add_argument("output")
    args = parser.parse_args()
    if os.path.exists(args.output):
        raise ValueError("出力先が既に存在します。上書きせず、別名を指定してください。")
    bank = core.load_voicebank(args.bank)
    path = bank["source"]["path"]
    with open(path, "rb") as f:
        if hashlib.sha256(f.read()).hexdigest().lower() != bank["source"]["sha256"].lower():
            raise ValueError("元音声のハッシュが一致しません。")
    facts = core.read_wav(path)
    result = repitch(bank, facts)
    with open(args.output, "x", encoding="utf-8", newline="\n") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)
        f.write("\n")
    before = sum(1 for u in bank["units"] if (u.get("pitch") or {}).get("midi") is not None)
    after = sum(1 for u in result["units"] if (u.get("pitch") or {}).get("midi") is not None)
    print(f"{os.path.basename(args.output)}: pitch {before} -> {after} / {len(result['units'])}")


if __name__ == "__main__":
    main()

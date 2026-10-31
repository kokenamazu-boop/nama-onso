"""Self-test for the Jinriki prep core (synthetic audio, no user material).

Run: python selftest.py
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import random
import shutil
import struct
import sys
import tempfile
import wave

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import core  # noqa: E402
import appenv  # noqa: E402
import enrich_bank  # noqa: E402
import export_audio  # noqa: E402


def check(value, message):
    if not value:
        raise AssertionError(message)


def write_synth(path: str, sr: int = 48000, seconds: float = 1.2) -> None:
    n = int(sr * seconds)
    frames = bytearray()
    for i in range(n):
        t = i / sr
        # 0.10 s silence, 0.80 s tone with a dip at 0.45 s, then silence
        amp = 0.0
        if 0.10 <= t < 0.90 and not (0.44 <= t < 0.48):
            amp = 0.3 * math.sin(2 * math.pi * 220 * t)
        v = int(max(-1.0, min(1.0, amp)) * 8388607)
        if v < 0:
            v += 1 << 24
        frames += bytes((v & 0xFF, (v >> 8) & 0xFF, (v >> 16) & 0xFF)) * 2  # stereo
    with wave.open(path, "wb") as w:
        w.setnchannels(2)
        w.setsampwidth(3)
        w.setframerate(sr)
        w.writeframes(bytes(frames))


def _write_take(path: str, segments, sr: int = 44100) -> None:
    """Tiny synthetic CV take: (kind, ms, amplitude) segments, mono 16-bit."""
    rng = random.Random(20261031)
    frames = bytearray()
    n = 0
    for kind, ms, amp in segments:
        for _ in range(int(sr * ms / 1000)):
            if kind == "silence":
                v = 0.0
            elif kind == "tone":
                v = amp * math.sin(2 * math.pi * 220 * n / sr)
            elif kind == "low":
                v = amp * math.sin(2 * math.pi * 150 * n / sr)
            else:  # noise
                v = amp * (rng.random() * 2 - 1)
            frames += struct.pack("<h", int(max(-1.0, min(1.0, v)) * 32767))
            n += 1
    with wave.open(path, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes(bytes(frames))


def _curate_checks(tmp: str) -> None:
    """Selection pipeline on synthetic takes (no user material)."""
    import csv
    import curate

    root = os.path.join(tmp, "curate_takes")
    os.makedirs(root)
    takes = {
        "あA3.wav": [("noise", 15, 0.003), ("tone", 320, 0.30)],
        "あC4.wav": [("noise", 15, 0.003), ("tone", 300, 0.30)],
        "かD4.wav": [("noise", 40, 0.003), ("noise", 30, 0.02), ("tone", 240, 0.30)],
        "かD#45.wav": [("noise", 10, 0.003), ("noise", 30, 0.50), ("tone", 240, 0.30)],
        "ざC4.wav": [("noise", 15, 0.003), ("low", 25, 0.15), ("noise", 60, 0.10),
                     ("tone", 200, 0.30)],
        "ざF4.wav": [("noise", 15, 0.003), ("low", 25, 0.15), ("noise", 40, 0.10),
                     ("tone", 200, 0.30)],
        "ぱE5.wav": [("noise", 15, 0.003), ("noise", 20, 0.10), ("tone", 200, 0.30)],
        "き1.wav": [("noise", 10, 0.003), ("tone", 90, 0.30)],
        "きA4.wav": [("noise", 15, 0.003), ("tone", 250, 0.30)],
        "くA4.wav": [("noise", 15, 0.003), ("tone", 250, 1.0)],
        "くB4.wav": [("noise", 15, 0.003), ("tone", 250, 0.30)],
    }
    for name, segments in takes.items():
        _write_take(os.path.join(root, name), segments)
    before = {name: hashlib.sha256(open(os.path.join(root, name), "rb").read()).hexdigest()
              for name in takes}

    out = os.path.join(tmp, "curate_out")
    result = curate.run(root, out, curate.Config(name="テスト音源"))
    summary = result["manifest"]["summary"]
    check(summary["aliases"] == 6, f"curate aliases {summary}")
    check(summary["selected"] == 12, f"curate two_take count {summary}")
    check(summary["rejected"] == 2, f"curate rejected count {summary}")

    by_alias = {a["alias"]: a for a in result["aliases"]}
    ka = {t["wav"]: t for t in by_alias["か"]["selected"]}
    check("かD#45.wav" in ka, f"strong burst take missing: {ka}")
    cfg = curate.Config()
    strong = curate.take_score(ka["かD#45.wav"], "voiceless_plosive", 62.0, cfg)
    weak = curate.take_score(ka["かD4.wav"], "voiceless_plosive", 62.0, cfg)
    check(strong < weak, f"weak burst scored better: {strong} vs {weak}")
    check(by_alias["ぱ"]["status"] == "scarce", "single take alias not scarced")
    p_picks = by_alias["ぱ"]["selected"]
    check(len(p_picks) == 2 and p_picks[0]["wav"] == p_picks[1]["wav"]
          and {p["role"] for p in p_picks} == {"_L", "_H"}, f"scarce roles {p_picks}")
    check(by_alias["き"]["selected"][0]["wav"] != "き1.wav", "short take survived")
    check(by_alias["く"]["selected"][0]["wav"] != "くA4.wav", "clipped take survived")
    z_bars = [t for t in result["selected"] if t["alias"] == "ざ"]
    check(all((t.get("voice_bar_ms") or 0) >= 10 for t in z_bars), f"voice bar {z_bars}")
    check(any(r["alias"] == "ぱ" and r["priority"] == "P1" for r in result["rerecords"]),
          f"scarce rerecord missing: {result['rerecords']}")

    # Output folder: aliases, subbanks, prefix.map and timing skeleton
    oto = open(os.path.join(out, "oto.ini"), encoding="cp932").read().splitlines()
    aliases = [line.split("=", 1)[1].split(",", 1)[0] for line in oto if "=" in line]
    check(len(aliases) == 12 and len(set(aliases)) == 12, f"oto aliases {aliases}")
    check(all(a.endswith(("_L", "_H")) for a in aliases), "oto alias suffix")
    prefix = open(os.path.join(out, "prefix.map"), encoding="cp932").read().splitlines()
    check(len(prefix) == 84 and prefix[42] == "F#4\t\t_L" and prefix[43] == "G4\t\t_H",
          f"prefix split {prefix[40:45]}")
    yaml_text = open(os.path.join(out, "character.yaml"), encoding="utf-8").read()
    check("C1-F#4" in yaml_text and "G4-B7" in yaml_text, "character.yaml ranges")
    check(len(os.listdir(os.path.join(out, "wav"))) == 9, "selected wav copies")
    for name in takes:
        now = hashlib.sha256(open(os.path.join(root, name), "rb").read()).hexdigest()
        check(now == before[name], f"curate modified {name}")

    # Modes: single one per alias, keep_usable every usable take
    single = curate.run(root, os.path.join(tmp, "curate_single"), curate.Config(mode="single"))
    check(single["manifest"]["summary"]["selected"] == 6, "single mode count")
    check(all(t["role"] == "" for t in single["selected"]), "single mode suffix")
    check(not os.path.exists(os.path.join(tmp, "curate_single", "prefix.map")),
          "single mode wrote prefix.map")
    check("subbanks" not in open(os.path.join(tmp, "curate_single", "character.yaml"),
                                 encoding="utf-8").read(), "single mode wrote subbanks")
    keep = curate.run(root, os.path.join(tmp, "curate_keep"), curate.Config(mode="keep_usable"))
    check(keep["manifest"]["summary"]["selected"] == 9, "keep_usable count")

    # OTO correction (order 3.7) is applied only when asked
    oto_src = os.path.join(root, "oto.ini")
    with open(oto_src, "w", encoding="cp932", newline="\r\n") as f:
        f.write("かD#45.wav=か,0,100,-50,40,30\n")
    fixed = curate.run(root, os.path.join(tmp, "curate_fix"), curate.Config(),
                       oto=oto_src, fix=True)
    row = next(r for r in fixed["selected"] if r["wav"] == "かD#45.wav")
    values = curate.fix_oto({"offset": 0, "consonant": 100, "cutoff": -50,
                             "preutterance": 40, "overlap": 30}, row, row["kind"])
    check(abs(values["consonant"] - 55.0) < 1e-6, f"oto fixed {values}")
    check(values["overlap"] <= 24.0 + 1e-6, f"oto overlap {values}")
    check(values["offset"] <= 5.0, f"oto offset {values}")
    check(os.path.isfile(os.path.join(tmp, "curate_fix", "oto.ini")), "fixed oto written")

    # Config accepts the order's grouped keys as well as the flat names.
    cfg_path = os.path.join(tmp, "curate_cfg.json")
    with open(cfg_path, "w", encoding="utf-8") as f:
        json.dump({"mode": "single", "plosive": {"jump_db_min": 12},
                   "pitch_targets": {"low": "C4"}, "subbanks": [{"suffix": "_L"}]}, f)
    cfg = curate.load_config(cfg_path)
    check(cfg.mode == "single" and cfg.jump_db_min == 12 and cfg.pitch_low == "C4",
          f"config mapping {cfg.mode} {cfg.jump_db_min} {cfg.pitch_low}")

    print("      curate: two_take/single/keep_usable, scarce duplication, burst ranking,")
    print("              voice bar, prefix.map/subbanks, rejected reasons, oto fix")


def main() -> int:
    tmp = tempfile.mkdtemp(prefix="jinriki-selftest-")
    wav = os.path.join(tmp, "synth.wav")
    write_synth(wav)
    facts = core.read_wav(wav)
    check(facts.sample_rate == 48000, "sample rate")
    check(abs(facts.seconds - 1.2) < 1e-3, "duration")
    check(abs(facts.peak - 0.3) < 0.01, "peak")

    first, last = core.voiced_range(facts.rms)
    check(abs(first - 0.10) < 0.03, f"voiced start {first}")
    check(abs(last - 0.90) < 0.03, f"voiced end {last}")

    # TIFA-style segments: silence is absorbed into the first consonant.
    segs = [(0.0, 0.20, "m"), (0.20, 0.30, "a"), (0.30, 0.44, "r"),
            (0.44, 0.48, "cl"), (0.48, 0.90, "u"), (0.90, 1.20, "SP")]
    units = core.group_units(segs)
    check(len(units) == 3, f"unit count {len(units)}")
    check(units[0].start == 0.0 and units[1].phonemes == ["cl"], "grouping")
    check(units[2].end == 0.90, "tail unit end")

    named, warning = core.name_units(units, "まるっ")
    check(not warning, f"unexpected warning: {warning}")
    check([u.name for u in named] == ["ま", "る", "っ"], "kana names")

    trimmed, note = core.trim_silence(named, facts)
    check(abs(trimmed[0].start - 0.10) < 0.03, f"leading trim {trimmed[0].start}")
    check("先頭" in note, "trim note missing")
    # Only the first boundary may move here; the tail already ends before the silence.
    check(abs(trimmed[-1].end - 0.90) < 1e-6, "tail moved unexpectedly")

    mismatch, warn2 = core.name_units(units, "まり")
    check(warn2 and mismatch[0].name == "m a", "mismatch must keep phonemes")

    # Count mismatch → align by vowel instead of giving up.
    al_units = [core.Unit("", ["k", "a"], 0.0, 0.1), core.Unit("", ["r", "a"], 0.1, 0.2),
                core.Unit("", ["cl"], 0.2, 0.3)]
    al_names, al_ratio = core.align_names(al_units, "かあらっ")
    check(al_ratio == 1.0 and al_names[0] == "か" and al_names[1] == "ら" and al_names[2] == "っ",
          f"alignment wrong: {al_names} {al_ratio}")
    named_al, note_al = core.name_units(al_units, "かあらっ")
    check(note_al and "位置合わせ" in note_al, "alignment note missing")
    check(core.kana_vowel("きゃ") == "a" and core.kana_vowel("っ") == "", "kana vowel table")
    check(core.phoneme_kana("b a") == "ば" and core.phoneme_kana("f a") == "ふぁ"
          and core.phoneme_kana("ts u") == "つ" and core.phoneme_kana("cl") == "っ"
          and core.phoneme_kana("ky o") == "きょ", "phoneme to kana")
    check(core.display_name("b a", "b a") == "ば" and core.display_name("キャ") == "きゃ"
          and core.display_name("紡", "m u") == "む", "display name in kana")
    check(core.display_name("ki") == "き" and core.display_name("sha") == "しゃ",
          "romaji display name")

    # A leading SP means the aligner already separated silence: a quiet /m/ must not be eaten.
    with_sp = [(0.0, 0.26, "SP"), (0.26, 0.32, "m"), (0.32, 0.50, "i")]
    sp_units = core.group_units(with_sp)
    kept, no_note = core.trim_silence(sp_units, facts)
    check(abs(kept[0].start - 0.26) < 1e-6, f"quiet onset was eaten: {kept[0].start}")
    check(not no_note, "unexpected trim with leading SP")

    bank = core.make_voicebank(facts, trimmed, "まるっ", "tifa-1.0-st")
    path = os.path.join(tmp, "bank.nama-voices.json")
    core.save_voicebank(path, bank)
    loaded = core.load_voicebank(path)
    check(loaded["source"]["sha256"] == facts.sha256, "hash not stored")
    check(len(loaded["units"]) == 3, "units not stored")
    check(loaded["units"][0]["name"] == "ま", "names not stored")
    check(abs(loaded["units"][0]["start"] - 0.10) < 0.03, "trim not stored")
    check("pitch" in loaded["units"][0], "pitch field missing")
    check(loaded["units"][0]["phoneme_starts"] == [trimmed[0].start, .20], "inner timing not stored")
    tsu = core.Unit("つ", ["ts", "u"], .54, .72, phoneme_starts=[.54, .65])
    check(core.export_phoneme_starts(tsu) == [.54, .65], "ts/u boundary lost")
    tsu.start = .55
    check(core.export_phoneme_starts(tsu) == [.55, .65], "range edit moved vowel boundary")
    tsu.end = .64
    check(core.export_phoneme_starts(tsu) == [], "invalid clipped vowel exported")
    check(core.export_phoneme_starts(core.Unit("つ", ["ts", "u"], .54, .72)) == [], "missing timing invented")

    before = core.read_wav(wav).sha256
    core.run_align  # API exists; actual alignment needs mvt and is tested manually
    after = core.read_wav(wav).sha256
    check(before == after, "source changed")

    bad = dict(loaded, format="other")
    core.save_voicebank(path, bad)
    try:
        core.load_voicebank(path)
        check(False, "unknown format accepted")
    except ValueError:
        pass

    rpp = os.path.join(tmp, "review.rpp")
    core.write_reaper_project(rpp, wav, facts, trimmed)
    text = open(rpp, encoding="utf-8").read()
    check(text.startswith("<REAPER_PROJECT"), "rpp header")
    check(text.count(" 1 ") >= 3, "candidate regions missing")
    check('MARKER' in text and '"m"' in text, "phoneme markers missing")
    check('"ま"' in text, "kana region missing")
    legacy = json.loads(json.dumps(bank))
    for unit in legacy["units"]:
        unit.pop("phoneme_starts", None)
    upgraded = enrich_bank.enrich(legacy, text)
    check(upgraded["units"][0]["phoneme_starts"] == bank["units"][0]["phoneme_starts"], "RPP timing recovery")
    check("phoneme_starts" not in legacy["units"][0], "enrichment modified original bank")
    try:
        enrich_bank.enrich(legacy, text.replace('"a"', '"e"'))
        check(False, "mismatched phonemes were silently recovered")
    except ValueError:
        pass

    # Pitch: a clean synthetic tone must come back within a few cents.
    sr = 48000
    tone = os.path.join(tmp, "tone.wav")
    n = int(sr * 0.5)
    buf = bytearray()
    for i in range(n):
        v = int(0.3 * math.sin(2 * math.pi * 220.0 * i / sr) * 8388607)
        buf += bytes((v & 0xFF, (v >> 8) & 0xFF, (v >> 16) & 0xFF))
    with wave.open(tone, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(3)
        w.setframerate(sr)
        w.writeframes(bytes(buf))
    tfacts = core.read_wav(tone)
    p = core.unit_pitch(tfacts, core.Unit("あ", ["a"], 0.05, 0.45))
    check(p["hz"] and abs(p["hz"] - 220.0) < 2.0, f"pitch estimate off: {p['hz']}")
    check(abs(p["midi"] - 57.0) < 0.2, f"midi conversion off: {p['midi']}")
    check(core.unit_pitch(tfacts, core.Unit("あ", ["a"], 0.05, 0.08))["hz"] is None, "short unit must not report pitch")

    # A vowel only 20 ms long after 100 ms of unvoiced consonant used to fail:
    # the old window started at the vowel and had too few samples, or fell back
    # into the consonant. The window may now reach back to find a periodic frame.
    cons = os.path.join(tmp, "short_vowel.wav")
    sr = 16000
    n = int(sr * 0.20)
    buf = bytearray()
    seed = 12345
    for i in range(n):
        t = i / sr
        if t < 0.100:
            seed = (1103515245 * seed + 12345) & 0x7FFFFFFF
            v = (seed / 0x7FFFFFFF * 2 - 1) * 0.05         # unvoiced consonant
        else:
            v = 0.3 * math.sin(2 * math.pi * 220 * t)        # 20 ms vowel
        s = int(max(-1.0, min(1.0, v)) * 8388607)
        if s < 0:
            s += 1 << 24
        buf += bytes((s & 0xFF, (s >> 8) & 0xFF, (s >> 16) & 0xFF))
    with wave.open(cons, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(3)
        w.setframerate(sr)
        w.writeframes(bytes(buf))
    cfacts = core.read_wav(cons)
    short_unit = core.Unit("た", ["t", "a"], 0.0, 0.120, phoneme_starts=[0.0, 0.100])
    # This case checks the widened YIN window, so pin the estimator to YIN.
    os.environ["NAMA_FCPE_DISABLE"] = "1"
    try:
        sp = core.unit_pitch(cfacts, short_unit)
    finally:
        os.environ.pop("NAMA_FCPE_DISABLE", None)
    check(sp["hz"] is not None and abs(sp["hz"] - 220.0) < 6.0, f"short vowel pitch missed: {sp['hz']}")
    check(sp.get("source") == "yin", f"YIN path not used: {sp.get('source')}")

    # Neural estimator (optional): a clean tone must land within a few cents and
    # the whole-unit estimate must agree with the YIN value.
    neural = getattr(core, "f0_neural", None)
    if neural is not None and neural.available():
        nf = core.unit_pitch(tfacts, core.Unit("あ", ["a"], 0.05, 0.45))
        check(nf.get("source") == "fcpe", f"neural path not used: {nf.get('source')}")
        check(nf["hz"] and abs(1200 * math.log2(nf["hz"] / 220.0)) < 25.0,
              f"neural pitch off: {nf['hz']}")
        check(len(nf.get("curve") or []) >= 3, "neural pitch curve missing")
        times = [p[0] for p in nf["curve"]]
        check(times == sorted(times), "pitch curve not time-ordered")
        check(all(0.0 <= t <= 0.4 + 1e-6 for t in times), "pitch curve outside the unit")

    # --- batch + coverage (fake aligner; no mvt needed) ---------------------
    import batch as batch_mod
    import coverage as coverage_mod

    def fake_aligner(wav_path, lyrics, model="tifa-1.0-st"):
        table = {"あい": [(0.10, 0.45, "a"), (0.45, 0.90, "i")],
                 "うえ": [(0.10, 0.45, "u"), (0.45, 0.90, "e")]}
        if lyrics not in table:
            raise RuntimeError("unknown lyric " + lyrics)
        return table[lyrics]

    bdir = os.path.join(tmp, "batch")
    os.makedirs(bdir, exist_ok=True)
    a_wav = os.path.join(bdir, "あい.wav")
    b_wav = os.path.join(bdir, "phrase2.wav")
    write_synth(a_wav, 48000, 1.2)
    write_synth(b_wav, 48000, 1.0)
    with open(os.path.join(bdir, "phrase2.txt"), "w", encoding="utf-8") as f:
        f.write("うえ\n")
    hash_a = core.read_wav(a_wav).sha256
    hash_b = core.read_wav(b_wav).sha256
    results = batch_mod.run_folder(bdir, aligner=fake_aligner)
    check(len(results) == 2 and all(r["status"] == "OK" for r in results), f"batch failed: {results}")
    check(all(r["units"] == 2 for r in results), "batch unit count")
    check(os.path.isfile(os.path.join(bdir, "あい.nama-voices.json")), "batch bank missing")
    check("song" in core.load_voicebank(os.path.join(bdir, "あい.nama-voices.json")),
          "voicebank has a song field")
    check(batch_mod.song_for(r"D:\録音\曲名_あいうえ.wav", "あいうえ") == "曲名",
          "song name from 曲名_歌詞")
    check(batch_mod.song_for(r"D:\録音\あいうえ.wav", "あいうえ") == "",
          "song name must stay empty without a prefix")
    check(os.path.isfile(os.path.join(bdir, "phrase2.nama-voices.json")), "sidecar bank missing")
    check(core.read_wav(a_wav).sha256 == hash_a and core.read_wav(b_wav).sha256 == hash_b, "batch changed a source")
    again = batch_mod.run_folder(bdir, aligner=fake_aligner)
    check(all(r["status"] == "既存のためスキップ" for r in again), "batch overwrote an existing bank")
    # Renaming the WAV must still be recognised by the source hash.
    renamed = os.path.join(bdir, "曲名_あい.wav")
    os.rename(a_wav, renamed)
    after_rename = batch_mod.run_folder(bdir, aligner=fake_aligner)
    row_renamed = next(r for r in after_rename if r["file"] == os.path.basename(renamed))
    check(row_renamed["status"] == "既存(同じ音声)のためスキップ", f"renamed WAV reprocessed: {row_renamed}")
    check("あい.nama-voices.json" in row_renamed["note"], "skip note should name the existing bank")
    os.rename(renamed, a_wav)
    report_path = batch_mod.write_report(bdir, results)
    check(os.path.isfile(report_path), "batch report missing")

    cov = coverage_mod.report(bdir, "あいうえお")
    check(cov["banks"] == 2 and cov["candidates"] == 4, f"coverage counts wrong: {cov['banks']}/{cov['candidates']}")
    check(cov["coverage"]["covered"] == 4, "coverage should find four mora")
    check(cov["coverage"]["missing"] == ["お"], f"coverage missing list wrong: {cov['coverage']['missing']}")
    check(sorted(cov["single"]) == sorted(["あ", "い", "う", "え"]), "coverage single-candidate list wrong")

    # --- phoneme database ---------------------------------------------------
    import db as db_mod
    db_path = os.path.join(tmp, "lib.db")
    conn = db_mod.connect(db_path)
    try:
        info_db = db_mod.index_folder(conn, bdir)
        check(info_db["banks"] == 2 and info_db["units"] == 4, f"db index: {info_db}")
        again_db = db_mod.index_folder(conn, bdir)
        check(again_db["banks"] == 0 and again_db["skipped"] == 2, "db index is not incremental")
        rows = db_mod.search(conn, "あ")
        check(len(rows) == 1 and rows[0]["name"] == "あ", "db search by name")
        check(rows[0]["vowel"] == "a" and rows[0]["cons"] == "", "db vowel/consonant split")
        by_pitch = db_mod.search(conn, sort="midi")
        check(len(by_pitch) == 4, "db sort returned everything")
        desc_rows = db_mod.search(conn, sort="midi", descending=True)
        check(desc_rows[0]["midi"] == max(r["midi"] for r in by_pitch if r["midi"] is not None),
              "db descending sort")
        # Consonant / neighbour context comes from the aligned phonemes.
        described = db_mod.describe_units([
            {"phonemes": "a"}, {"phonemes": "k a"}, {"phonemes": "cl"}, {"phonemes": "ts u"}])
        check([d["cons"] for d in described] == ["", "k", "cl", "ts"], "db consonant split")
        check([d["vowel"] for d in described] == ["a", "a", "", "u"], "db vowel split")
        check([d["prev_vowel"] for d in described] == ["", "a", "a", ""], "db prev vowel")
        check([d["next_cons"] for d in described] == ["k", "cl", "ts", ""], "db next consonant")
        check(len(db_mod.search(conn, cons="", exact=False, vowel="a")) == 1, "db filter by vowel")
        cov_db = db_mod.coverage(conn)
        check(cov_db["あ"]["count"] == 1, "db coverage count")
        lib_path = db_mod.export_library(conn, os.path.join(tmp, "library.json"))
        lib = json.load(open(lib_path, encoding="utf-8"))
        check(lib["format"] == "nama-library" and len(lib["banks"]) == 2, "library export")
        for entry in lib["banks"]:
            with open(entry["path"], "rb") as fh:
                raw = fh.read()
            check(hashlib.sha256(raw).hexdigest() == entry["jsonHash"],
                  f"library json hash must be the file bytes: {entry['path']}")
        wav_dir = os.path.join(tmp, "wavs")
        written = db_mod.export_wavs(conn, wav_dir)
        check(written == 4, f"wav export count {written}")
        check(len([f for f in os.listdir(wav_dir) if f.endswith(".wav")]) == 4, "wav export files")

        # --- flags (favourite / hidden / tags) ------------------------------
        row_a = db_mod.search(conn, "あ")[0]
        db_mod.set_flags(conn, [row_a], favorite=True, tags="soft")
        check(len(db_mod.search(conn, favorite=True)) == 1, "db favourite filter")
        check(len(db_mod.search(conn, tags="soft")) == 1, "db tag filter")
        db_mod.set_flags(conn, [row_a], hidden=True)
        check(len(db_mod.search(conn, "あ")) == 0, "hidden row still visible")
        check(len(db_mod.search(conn, "あ", hidden=True)) == 1, "hidden-only filter")
        check(len(db_mod.search(conn, "あ", hidden=False)) == 1, "include-hidden filter")
        check(db_mod.coverage(conn).get("あ") is None, "coverage counted a hidden row")
        check(db_mod.coverage(conn, include_hidden=True)["あ"]["count"] == 1, "coverage include hidden")
        db_mod.index_folder(conn, bdir, force=True)
        check(len(db_mod.search(conn, favorite=True, hidden=False)) == 1, "flags lost on re-index")
        check(len(db_mod.search(conn)) == 3, "hidden row came back after re-index")
        db_mod.set_flags(conn, [row_a], hidden=False)
        check(len(db_mod.search(conn)) == 4, "unhide failed")

        # GUI undo writes flags/aliases back exactly, without editing the JSONs.
        db_mod.restore_flags(conn, [(row_a["bank_path"], row_a["unit_id"], 1, 0, "soft")])
        check(len(db_mod.search(conn, favorite=True, tags="soft")) == 1,
              "restore_flags lost favourite/tag")
        db_mod.restore_flags(conn, [(row_a["bank_path"], row_a["unit_id"], 0, 0, "")])
        check(len(db_mod.search(conn, favorite=True)) == 0, "restore_flags did not clear flags")
        check(len(db_mod.search(conn, "あ")) == 1, "restore_flags hid the row")
        db_mod.set_alias(conn, row_a["bank_path"], row_a["unit_id"], "あA3")
        check(db_mod.get_alias(conn, row_a["bank_path"], row_a["unit_id"]) == "あA3", "alias read")
        alias_rows = db_mod.search(conn, "あA3")
        check(len(alias_rows) == 1 and alias_rows[0]["name"] == "あA3", "alias not applied")
        db_mod.set_alias(conn, row_a["bank_path"], row_a["unit_id"], "")
        check(db_mod.get_alias(conn, row_a["bank_path"], row_a["unit_id"]) == "",
              "alias not cleared")

        # --- near-copy detection -------------------------------------------
        dup_dir = os.path.join(tmp, "dups")
        os.makedirs(dup_dir, exist_ok=True)
        shutil.copy2(os.path.join(bdir, "あい.nama-voices.json"),
                     os.path.join(dup_dir, "あい-copy.nama-voices.json"))
        db_mod.index_folders(conn, [bdir, dup_dir])
        groups = db_mod.duplicates(conn)
        check(any(g["name"] == "あ" and g["count"] == 2 for g in groups), "duplicate not found")
        hidden_count = db_mod.hide_duplicates(conn)
        check(hidden_count >= 1, "hide duplicates hid nothing")
        check(len(db_mod.search(conn, "あ")) == 1, "duplicate copy left visible")
        check(len(db_mod.search(conn, "あ", hidden=True)) == 1, "duplicate copy not hidden")

        # --- export naming and manifest ------------------------------------
        row = db_mod.search(conn, "あ")[0]
        check(db_mod.pattern_name(row, "{name}_{index}", 3) == "あ_03", "pattern index")
        check(db_mod.pattern_name(row, "{cons}{vowel}", 1) == "a", "pattern phonemes")
        csv_path = os.path.join(tmp, "phonemes.csv")
        count_csv = db_mod.export_csv(conn, csv_path)
        check(count_csv == len(db_mod.search(conn)), "csv row count")
        with open(csv_path, encoding="utf-8-sig") as fh:
            header = fh.readline().strip()
            check(header.startswith("name,phonemes,cons,vowel"), "csv header")
        marked_dir = os.path.join(tmp, "named_wavs")
        db_mod.export_wavs(conn, marked_dir, pattern="{name}_{index}")
        check(os.path.isfile(os.path.join(marked_dir, "あ_01.wav")), "pattern wav name")
        # Every exported WAV is 44.1 kHz / 24-bit / mono, with the pitch intact.
        exported = os.path.join(marked_dir, "あ_01.wav")
        with wave.open(exported, "rb") as fh:
            check(fh.getframerate() == export_audio.EXPORT_SAMPLE_RATE, "export sample rate")
            check(fh.getnchannels() == 1, "export channel count")
            check(fh.getsampwidth() == 3, "export bit depth")
            exported_frames = fh.getnframes()
        check(abs(exported_frames / 44100.0 - (row["end"] - row["start"])) < 0.002,
              f"export duration {exported_frames}")
        efacts = core.read_wav(exported)
        epitch = core.unit_pitch(efacts, core.Unit("あ", ["a"], 0.0, min(0.3, efacts.seconds)))
        check(epitch["hz"] and abs(epitch["hz"] - 220.0) < 3.0,
              f"resampling moved the pitch: {epitch['hz']}")

        # --- UTAU export: minimum-length filter ----------------------------
        utau_banks = os.path.join(tmp, "utau_banks")
        os.makedirs(utau_banks, exist_ok=True)
        ufacts = core.read_wav(a_wav)
        long_unit = core.Unit("あ", ["a"], 0.10, 0.45, "checked", [0.10])
        tiny_unit = core.Unit("い", ["i"], 0.50, 0.55, "checked", [0.50])
        ubank = core.make_voicebank(ufacts, [long_unit, tiny_unit], "あい", "tifa-1.0-st")
        core.save_voicebank(os.path.join(utau_banks, "あい.nama-voices.json"), ubank)
        uconn = db_mod.connect(os.path.join(utau_banks, db_mod.DB_DEFAULT))
        try:
            db_mod.index_folder(uconn, utau_banks)
            all_info = db_mod.export_utau(uconn, os.path.join(tmp, "utau_all"))
            check(all_info["wav"] == 2 and all_info["too_short"] == 0,
                  f"utau without a limit: {all_info}")
            min_info = db_mod.export_utau(uconn, os.path.join(tmp, "utau_min"), min_ms=100)
            check(min_info["wav"] == 1 and min_info["too_short"] == 1,
                  f"utau min-length filter: {min_info}")
            check(os.path.isfile(os.path.join(tmp, "utau_min", "あ.wav")),
                  "utau dropped the long row")
            check(not os.path.isfile(os.path.join(tmp, "utau_min", "い.wav")),
                  "utau wrote the short row")
            with open(os.path.join(tmp, "utau_min", "oto.ini"), encoding="cp932") as fh:
                oto_text = fh.read()
            check("あ.wav=あ," in oto_text and "い.wav" not in oto_text,
                  f"oto.ini kept a short row: {oto_text!r}")
            # 50 ms is exactly the tiny row: "shorter than" must not drop it.
            eq_info = db_mod.export_utau(uconn, os.path.join(tmp, "utau_eq"), min_ms=50)
            check(eq_info["wav"] == 2 and eq_info["too_short"] == 0,
                  f"utau dropped a row equal to the limit: {eq_info}")
        finally:
            uconn.close()
    finally:
        conn.close()

    # 音素DB style: <kana>_<note>_<number>.wav, one bank each
    fdb = os.path.join(tmp, "fdb")
    os.makedirs(fdb, exist_ok=True)
    for src_name, dst_name in ((a_wav, "あ_F4_117.wav"), (b_wav, "い_C#4_84.wav")):
        shutil.copyfile(src_name, os.path.join(fdb, dst_name))
    imported = db_mod.import_fonemudb(fdb, os.path.join(tmp, "imported"))
    check(imported["written"] == 2, f"import count {imported}")
    one = json.load(open(os.path.join(tmp, "imported", "fdb__あ_F4_117.nama-voices.json"), encoding="utf-8"))
    check(one["units"][0]["name"] == "あ", "import name")
    check(abs(one["units"][0]["pitch"]["midi"] - 65.0) < 1e-9, "import pitch from note name")
    check(abs(one["units"][0]["end"] - core.read_wav(a_wav).seconds) < 1e-6, "import range")

    bl = os.path.join(tmp, "names")
    os.makedirs(bl, exist_ok=True)
    check(batch_mod.lyrics_for(os.path.join(bl, "曲名_あいう.wav")) == "あいう", "song_lyrics name split")
    check(batch_mod.lyrics_for(os.path.join(bl, "あいう.wav")) == "あいう", "plain name kept")
    check(batch_mod.lyrics_for(os.path.join(bl, "曲_名_あいう.wav")) == "あいう", "last separator wins")
    check(batch_mod.lyrics_for(os.path.join(bl, "曲名_.wav")) == "曲名_", "empty tail falls back to the stem")
    check(batch_mod.lyrics_for(os.path.join(bl, "曲名_あいう.wav"), "stem") == "曲名_あいう", "stem mode ignores the separator")
    # A tail like "(Dry)" must be reported, not aligned as lyrics.
    guard = os.path.join(tmp, "guard")
    os.makedirs(guard, exist_ok=True)
    shutil.copyfile(a_wav, os.path.join(guard, "曲名_(Dry).wav"))
    guarded = batch_mod.run_folder(guard, aligner=fake_aligner)
    check(guarded and guarded[0]["status"] == "歌詞がかなでない", f"non-kana lyric accepted: {guarded}")

    # mvt setup commands are built but never executed here (network/uv may be absent).
    install_cmd = appenv.mvt_install_command()
    check(install_cmd is None or (install_cmd[1:3] == ["tool", "install"]
                                  and install_cmd[-1] == appenv.MVT_PACKAGE),
          "mvt install command shape")
    check(appenv.mvt_model_command("mvt.exe") == ["mvt.exe", "models", "download", "tifa-1.0-st"],
          "mvt model command shape")
    check("uv tool install" in appenv.MVT_HINT and "uv" in appenv.UV_HINT, "install hints")

    # Export format: 96 kHz stereo in, 44.1 kHz 24-bit mono out; same without numpy.
    hi = os.path.join(tmp, "hi.wav")
    write_synth(hi, 96000, 0.5)
    hi_out = os.path.join(tmp, "hi_44.wav")
    check(export_audio.write_clip(hi, 0.05, 0.35, hi_out), "96k export failed")
    with wave.open(hi_out, "rb") as fh:
        check(fh.getframerate() == 44100 and fh.getnchannels() == 1 and fh.getsampwidth() == 3,
              "96k export format")
        check(abs(fh.getnframes() - round(0.30 * 44100)) <= 2, f"96k export length {fh.getnframes()}")
    py_out = os.path.join(tmp, "hi_44_py.wav")
    saved_np = export_audio.np
    export_audio.np = None
    try:
        check(export_audio.write_clip(hi, 0.05, 0.35, py_out), "python fallback export failed")
    finally:
        export_audio.np = saved_np
    with wave.open(py_out, "rb") as fh:
        check(fh.getframerate() == 44100 and fh.getnchannels() == 1 and fh.getsampwidth() == 3,
              "python fallback format")
        check(abs(fh.getnframes() - round(0.30 * 44100)) <= 2,
              f"python fallback length {fh.getnframes()}")

    _curate_checks(tmp)

    print("PASS: wav facts, silence range, mora grouping, kana naming, mismatch guard,")
    print("      leading-silence trim, voicebank JSON round-trip, unknown format rejected,")
    print("      db flags (favourite/hidden/tag) kept across re-index,")
    print("      flag/alias restore for undo, mvt setup command shapes,")
    print("      export format (44.1k/24bit/mono, numpy and fallback),")
    print("      near-copy detection and hiding, CSV manifest, WAV name patterns")
    print(f"      temp: {tmp}")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except AssertionError as e:
        print(f"FAIL: {e}")
        sys.exit(1)

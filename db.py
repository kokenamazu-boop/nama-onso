"""Phoneme library: index voicebanks into SQLite, search, export, import.

Design notes
------------
* Audio is never copied: rows point at the source WAV + sample range, exactly
  like ``nama-voices.json``. ``export_wavs`` is the only path that writes audio,
  and it writes new files in a chosen folder.
* ``sqlite3`` is in the Python standard library, so the packaged app needs no
  extra dependency.
* Re-indexing is incremental: a bank whose JSON hash is unchanged is skipped.

CLI:
    python db.py build FOLDER [--db PATH]
    python db.py list  FOLDER [--name あ] [--min-midi 60]
    python db.py export-library FOLDER OUT.json
    python db.py export-wavs FOLDER OUT_DIR
    python db.py import-fonemudb "D:\\phoneme-db\\waves" OUT_DIR
"""
from __future__ import annotations

import argparse
import glob
import hashlib
import json
import os
import re
import sqlite3
import sys
import wave
from typing import Dict, List, Optional, Sequence, Tuple

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import core  # noqa: E402
import export_audio  # noqa: E402

DB_DEFAULT = "nama-phonemes.db"
NOTE_NAMES = {"C": 0, "C#": 1, "D": 2, "D#": 3, "E": 4, "F": 5, "F#": 6,
              "G": 7, "G#": 8, "A": 9, "A#": 10, "B": 11}

SCHEMA = """
CREATE TABLE IF NOT EXISTS banks (
    path TEXT PRIMARY KEY, json_hash TEXT, source TEXT, source_hash TEXT,
    lyrics TEXT, song TEXT, model TEXT, mtime REAL
);
CREATE TABLE IF NOT EXISTS units (
    bank_path TEXT NOT NULL, unit_id TEXT NOT NULL, name TEXT, kana TEXT, phonemes TEXT,
    start REAL, end REAL, midi REAL, hz REAL, strength REAL, checked INTEGER,
    curve TEXT, cons TEXT, vowel TEXT, prev_vowel TEXT, next_cons TEXT,
    PRIMARY KEY (bank_path, unit_id)
);
CREATE INDEX IF NOT EXISTS units_name ON units(name);
CREATE INDEX IF NOT EXISTS units_midi ON units(midi);
CREATE TABLE IF NOT EXISTS aliases (
    bank_path TEXT NOT NULL, unit_id TEXT NOT NULL, name TEXT NOT NULL,
    PRIMARY KEY (bank_path, unit_id)
);
CREATE TABLE IF NOT EXISTS unit_flags (
    bank_path TEXT NOT NULL, unit_id TEXT NOT NULL,
    favorite INTEGER NOT NULL DEFAULT 0, hidden INTEGER NOT NULL DEFAULT 0,
    tags TEXT NOT NULL DEFAULT '',
    PRIMARY KEY (bank_path, unit_id)
);
"""


# --------------------------------------------------------------------- index
def connect(path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(path)
    conn.executescript(SCHEMA)
    _migrate(conn)
    return conn


def _migrate(conn: sqlite3.Connection) -> None:
    """Add the consonant/vowel columns to an index built by an older version."""
    have = {row[1] for row in conn.execute("PRAGMA table_info(units)")}
    added = False
    for column in ("cons", "vowel", "prev_vowel", "next_cons", "kana"):
        if column not in have:
            conn.execute(f"ALTER TABLE units ADD COLUMN {column} TEXT")
            added = True
    if added:
        # Values are derived, so drop the rows and let the next index fill them.
        conn.execute("DELETE FROM units")
        conn.execute("DELETE FROM banks")
        conn.commit()
    bank_columns = {row[1] for row in conn.execute("PRAGMA table_info(banks)")}
    if "song" not in bank_columns:
        conn.execute("ALTER TABLE banks ADD COLUMN song TEXT")
        # Force the next index to fill the new column without dropping units.
        conn.execute("UPDATE banks SET mtime = 0")
        conn.commit()


VOWELS = set("aiueo")


def describe_units(units: Sequence[dict]) -> List[dict]:
    """Split each unit into consonant/vowel and add the neighbouring context.

    ``phonemes`` looks like "k a", "a", "ts u" or "cl". The consonant is every
    phoneme before the last vowel; 前の母音 comes from the previous unit of the
    same bank and 次の子音 from the next one.
    """
    parsed = []
    for u in units:
        parts = [p for p in str(u.get("phonemes", "")).split() if p]
        vowel = ""
        cons = ""
        if parts:
            if parts[-1] in VOWELS:
                vowel = parts[-1]
                cons = " ".join(parts[:-1])
            else:
                cons = " ".join(parts)
        parsed.append({"cons": cons, "vowel": vowel})
    out = []
    for i, item in enumerate(parsed):
        out.append({
            "cons": item["cons"],
            "vowel": item["vowel"],
            "prev_vowel": parsed[i - 1]["vowel"] if i > 0 else "",
            "next_cons": parsed[i + 1]["cons"] if i + 1 < len(parsed) else "",
        })
    return out


def index_folder(conn: sqlite3.Connection, folder: str, force: bool = False) -> Dict:
    return index_folders(conn, [folder], force)


def index_folders(conn: sqlite3.Connection, folders: Sequence[str], force: bool = False,
                  prune: bool = True) -> Dict:
    """Index one or more bank folders into the same DB.

    With ``prune`` (default) banks under the given folders that disappeared are
    removed. Rows from other folders are never touched, so two libraries can
    share one index.
    """
    roots = {os.path.abspath(f) for f in folders}
    files: List[str] = []
    for folder in folders:
        files.extend(sorted(glob.glob(os.path.join(folder, "*.nama-voices.json"))))
    added = skipped = removed = units = 0
    seen = set()
    for path in files:
        seen.add(os.path.abspath(path))
        stat = os.stat(path)
        try:
            with open(path, "rb") as fh:
                raw = fh.read()
        except OSError as e:
            print(f"skip {os.path.basename(path)}: {e}", file=sys.stderr)
            continue
        # The VST compares this against the JSON file bytes, so hash the file
        # itself (not the source WAV, and not the decoded text).
        json_hash = hashlib.sha256(raw).hexdigest()
        row = conn.execute("SELECT json_hash, mtime FROM banks WHERE path = ?",
                           (os.path.abspath(path),)).fetchone()
        if row and not force and abs(row[1] - stat.st_mtime) < 1e-6 and row[0] == json_hash:
            skipped += 1
            continue
        try:
            bank = core.load_voicebank(path)
        except Exception as e:  # noqa: BLE001 - skip bad files, keep indexing
            print(f"skip {os.path.basename(path)}: {e}", file=sys.stderr)
            continue
        source = bank.get("source", {})
        song = str(bank.get("song") or "").strip()
        if not song:
            base = os.path.basename(path)
            stem = base[:-len(".nama-voices.json")] if base.endswith(".nama-voices.json") else os.path.splitext(base)[0]
            lyrics = str(bank.get("lyrics") or "")
            if lyrics and stem.endswith(lyrics):
                song = stem[:-len(lyrics)].rstrip("_- ").strip()
        conn.execute("DELETE FROM units WHERE bank_path = ?", (os.path.abspath(path),))
        conn.execute(
            "INSERT OR REPLACE INTO banks(path, json_hash, source, source_hash, lyrics, song, model, mtime)"
            " VALUES (?,?,?,?,?,?,?,?)",
            (os.path.abspath(path), json_hash, source.get("path", ""),
             source.get("sha256", ""), bank.get("lyrics", ""), song,
             bank.get("model", ""), stat.st_mtime))
        described = describe_units(bank["units"])
        for u, d in zip(bank["units"], described):
            pitch = u.get("pitch") or {}
            conn.execute(
                "INSERT OR REPLACE INTO units(bank_path, unit_id, name, kana, phonemes, start, end,"
                " midi, hz, strength, checked, curve, cons, vowel, prev_vowel, next_cons)"
                " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (os.path.abspath(path), u.get("id", ""), u.get("name", ""),
                 core.display_name(u.get("name", ""), u.get("phonemes", "")),
                 u.get("phonemes", ""),
                 float(u["start"]), float(u["end"]), pitch.get("midi"), pitch.get("hz"),
                 pitch.get("strength", 0.0), 1 if u.get("confidence") == "checked" else 0,
                 json.dumps(pitch.get("curve") or [], ensure_ascii=False),
                 d["cons"], d["vowel"], d["prev_vowel"], d["next_cons"]))
            units += 1
        added += 1
    # drop banks whose file disappeared (only inside the folders we indexed)
    if prune:
        for (path,) in conn.execute("SELECT path FROM banks").fetchall():
            if path not in seen and os.path.dirname(path) in roots:
                conn.execute("DELETE FROM units WHERE bank_path = ?", (path,))
                conn.execute("DELETE FROM unit_flags WHERE bank_path = ?", (path,))
                conn.execute("DELETE FROM aliases WHERE bank_path = ?", (path,))
                conn.execute("DELETE FROM banks WHERE path = ?", (path,))
                removed += 1
    conn.commit()
    return {"banks": added, "skipped": skipped, "removed": removed, "units": units}


def set_alias(conn: sqlite3.Connection, bank_path: str, unit_id: str, name: str) -> None:
    """Rename a row for display and file naming. The source JSON is untouched."""
    name = name.strip()
    if not name:
        conn.execute("DELETE FROM aliases WHERE bank_path = ? AND unit_id = ?",
                     (bank_path, unit_id))
    else:
        conn.execute("INSERT OR REPLACE INTO aliases(bank_path, unit_id, name) VALUES (?,?,?)",
                     (bank_path, unit_id, name))
    conn.commit()


def get_alias(conn: sqlite3.Connection, bank_path: str, unit_id: str) -> str:
    """Current display alias, or "" when the row still uses its derived name."""
    row = conn.execute("SELECT name FROM aliases WHERE bank_path = ? AND unit_id = ?",
                       (bank_path, unit_id)).fetchone()
    return (row[0] or "") if row else ""


DISPLAY = "COALESCE(NULLIF(a.name, ''), NULLIF(u.kana, ''), u.name)"


def _order_terms(sort: str) -> List[str]:
    return {
        "name": [DISPLAY, "u.midi"],
        "midi": ["u.midi", DISPLAY],
        "length": ["u.end - u.start"],
        "bank": ["u.bank_path", "u.start"],
        "cons": ["u.cons", DISPLAY],
        "vowel": ["u.vowel", DISPLAY],
        "prev_vowel": ["u.prev_vowel", DISPLAY],
        "next_cons": ["u.next_cons", DISPLAY],
        "checked": ["u.checked", DISPLAY],
        "favorite": ["COALESCE(f.favorite, 0)", DISPLAY],
        "song": ["COALESCE(b.song, '')", "u.bank_path", "u.start"],
    }.get(sort, [DISPLAY])


def search(conn: sqlite3.Connection, name: str = "", exact: bool = False,
           min_midi: Optional[float] = None, max_midi: Optional[float] = None,
           min_ms: Optional[float] = None, sort: str = "name",
           cons: str = "", vowel: str = "", prev_vowel: str = "",
           next_cons: str = "", text: str = "",
           hidden: Optional[bool] = None, favorite: bool = False,
           tags: str = "", descending: bool = False, song: str = "") -> List[dict]:
    where, params = [], []

    def add(expression: str, value: str) -> None:
        if not value:
            return
        where.append(f"{expression} = ?" if exact else f"{expression} LIKE ?")
        params.append(value if exact else f"%{value}%")

    add(DISPLAY, name)
    add("u.cons", cons)
    add("u.vowel", vowel)
    add("u.prev_vowel", prev_vowel)
    add("u.next_cons", next_cons)
    if text:
        like = f"%{text}%"
        where.append(f"({DISPLAY} LIKE ? OR u.cons LIKE ? OR u.vowel LIKE ? OR u.prev_vowel LIKE ?"
                     " OR u.next_cons LIKE ? OR u.phonemes LIKE ? OR u.bank_path LIKE ?)")
        params.extend([like] * 7)
    if min_midi is not None:
        where.append("u.midi >= ?")
        params.append(min_midi)
    if max_midi is not None:
        where.append("u.midi <= ?")
        params.append(max_midi)
    if min_ms is not None:
        where.append("(u.end - u.start) * 1000 >= ?")
        params.append(min_ms)
    # Hidden rows stay in the index but drop out of normal searches and exports.
    if favorite:
        where.append("COALESCE(f.favorite, 0) = 1")
    if hidden is None:
        where.append("COALESCE(f.hidden, 0) = 0")
    elif hidden:
        where.append("COALESCE(f.hidden, 0) = 1")
    if tags:
        where.append("COALESCE(f.tags, '') LIKE ?")
        params.append(f"%{tags}%")
    if song:
        where.append("COALESCE(b.song, '') LIKE ?")
        params.append(f"%{song}%")
    direction = " DESC" if descending else ""
    order = ", ".join(term + direction for term in _order_terms(sort))
    sql = ("SELECT u.bank_path, u.unit_id, COALESCE(a.name, u.name), u.phonemes, u.start, u.end,"
           " u.midi, u.hz, u.strength, u.checked, b.source, b.lyrics, u.cons, u.vowel,"
           " u.prev_vowel, u.next_cons, u.name,"
           " COALESCE(f.favorite, 0), COALESCE(f.hidden, 0), COALESCE(f.tags, ''),"
           " COALESCE(b.song, '')"
           " FROM units u JOIN banks b ON b.path = u.bank_path"
           " LEFT JOIN aliases a ON a.bank_path = u.bank_path AND a.unit_id = u.unit_id"
           " LEFT JOIN unit_flags f ON f.bank_path = u.bank_path AND f.unit_id = u.unit_id")
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += f" ORDER BY {order}"
    rows = conn.execute(sql, params).fetchall()
    keys = ("bank_path", "unit_id", "name", "phonemes", "start", "end", "midi", "hz",
            "strength", "checked", "source", "lyrics", "cons", "vowel", "prev_vowel",
            "next_cons", "raw_name", "favorite", "hidden", "tags", "song")
    out = [dict(zip(keys, r)) for r in rows]
    for row in out:
        # Always derive the kana label at read time as well, so an index built
        # by an older version (or a romanised name) still shows ひらがな.
        row["display"] = core.display_name(row["name"], row["phonemes"])
    return out


def set_flags(conn: sqlite3.Connection, rows: Sequence[dict],
              favorite: Optional[bool] = None, hidden: Optional[bool] = None,
              tags: Optional[str] = None) -> int:
    """Set favourite/hidden/tags for rows, keeping them across re-indexing."""
    changed = 0
    for row in rows:
        bank_path, unit_id = row["bank_path"], row["unit_id"]
        current = conn.execute(
            "SELECT favorite, hidden, tags FROM unit_flags WHERE bank_path = ? AND unit_id = ?",
            (bank_path, unit_id)).fetchone() or (0, 0, "")
        fav = current[0] if favorite is None else int(favorite)
        hid = current[1] if hidden is None else int(hidden)
        tag = current[2] if tags is None else tags.strip()
        if not fav and not hid and not tag:
            conn.execute("DELETE FROM unit_flags WHERE bank_path = ? AND unit_id = ?",
                         (bank_path, unit_id))
        else:
            conn.execute(
                "INSERT OR REPLACE INTO unit_flags(bank_path, unit_id, favorite, hidden, tags)"
                " VALUES (?,?,?,?,?)", (bank_path, unit_id, fav, hid, tag))
        changed += 1
    conn.commit()
    return changed


def restore_flags(conn: sqlite3.Connection, entries: Sequence[Tuple[str, str, int, int, str]]) -> int:
    """Write (bank_path, unit_id, favorite, hidden, tags) back exactly, one commit.

    Used by the GUI undo so a hide/duplicate clean can be reverted without
    touching the source JSONs.
    """
    for bank_path, unit_id, fav, hid, tag in entries:
        tag = (tag or "").strip()
        if not fav and not hid and not tag:
            conn.execute("DELETE FROM unit_flags WHERE bank_path = ? AND unit_id = ?",
                         (bank_path, unit_id))
        else:
            conn.execute(
                "INSERT OR REPLACE INTO unit_flags(bank_path, unit_id, favorite, hidden, tags)"
                " VALUES (?,?,?,?,?)", (bank_path, unit_id, int(bool(fav)), int(bool(hid)), tag))
    conn.commit()
    return len(entries)


def duplicates(conn: sqlite3.Connection, cents: int = 25,
               length_ms: int = 40) -> List[dict]:
    """Groups of visible candidates that are near-copies of the same sound.

    Same display name and phonemes, pitch within ``cents`` and length within
    ``length_ms``. The kept row is the longest, then a favourite.
    """
    rows = search(conn, sort="name")
    buckets: Dict[Tuple, List[dict]] = {}
    for row in rows:
        midi = row["midi"]
        if midi is None:
            continue
        pitch_key = int(round(midi * 1200.0 / max(1, cents)))
        length = row["end"] - row["start"]
        length_key = int(round(length * 1000 / max(1, length_ms)))
        buckets.setdefault((row["name"], row["phonemes"], pitch_key, length_key), []).append(row)
    groups = []
    for key, items in buckets.items():
        if len(items) < 2:
            continue
        keep = max(items, key=lambda r: (r["end"] - r["start"], r["favorite"]))
        groups.append({"name": key[0], "midi": round(items[0]["midi"], 2),
                       "count": len(items), "keep": keep, "drop": [r for r in items if r is not keep]})
    groups.sort(key=lambda g: (-g["count"], g["name"], g["midi"]))
    return groups


def hide_duplicates(conn: sqlite3.Connection) -> int:
    """Hide every near-copy except the kept one. Source JSONs are untouched."""
    hidden = 0
    for group in duplicates(conn):
        hidden += set_flags(conn, group["drop"], hidden=True)
    return hidden


def coverage(conn: sqlite3.Connection, include_hidden: bool = False) -> Dict[str, Dict]:
    where = "" if include_hidden else " WHERE COALESCE(f.hidden, 0) = 0"
    out: Dict[str, Dict] = {}
    for name, count, lo, hi, lmin, lmax in conn.execute(
            "SELECT u.name, COUNT(*), MIN(u.midi), MAX(u.midi), MIN(u.end - u.start),"
            " MAX(u.end - u.start) FROM units u"
            " LEFT JOIN unit_flags f ON f.bank_path = u.bank_path AND f.unit_id = u.unit_id"
            + where + " GROUP BY u.name"):
        out[name] = {"count": count,
                     "pitch_min": None if lo is None else round(lo, 2),
                     "pitch_max": None if hi is None else round(hi, 2),
                     "pitch_spread": None if lo is None or hi is None else round(hi - lo, 2),
                     "len_min_ms": round(lmin * 1000), "len_max_ms": round(lmax * 1000)}
    return out


# ------------------------------------------------------------------- export
def export_library(conn: sqlite3.Connection, out_path: str) -> str:
    banks = []
    for path, json_hash, source, source_hash, lyrics in conn.execute(
            "SELECT path, json_hash, source, source_hash, lyrics FROM banks ORDER BY path"):
        banks.append({"path": path, "jsonHash": json_hash, "sourcePath": source,
                      "sourceHash": source_hash, "lyrics": lyrics})
    data = {"format": "nama-library", "version": 1, "banks": banks}
    with open(out_path, "w", encoding="utf-8", newline="\n") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
        f.write("\n")
    return out_path


def note_to_midi(note: str) -> Optional[float]:
    m = re.fullmatch(r"([A-G]#?)(-?\d)", note)
    if not m or m.group(1) not in NOTE_NAMES:
        return None
    return 12.0 * (int(m.group(2)) + 1) + NOTE_NAMES[m.group(1)]


def midi_to_note(midi: float) -> Tuple[str, int]:
    n = int(round(midi))
    names = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]
    return names[n % 12] + str(n // 12 - 1), int(round((midi - n) * 100))


def midi_label(midi: Optional[float]) -> str:
    """Note name like E4 / G#4 (no cents), or '-' when pitch is unknown."""
    return "-" if midi is None else midi_to_note(float(midi))[0]


def _read_frames(path: str, t0: float, t1: float) -> Tuple[bytes, int, int, int]:
    with wave.open(path, "rb") as w:
        sr, ch, width = w.getframerate(), w.getnchannels(), w.getsampwidth()
        a = max(0, int(t0 * sr))
        b = min(w.getnframes(), int(t1 * sr))
        w.setpos(a)
        return w.readframes(max(0, b - a)), sr, ch, width


def _safe_name(text: str) -> str:
    bad = '\\/:*?"<>|\r\n\t'
    cleaned = "".join("_" if c in bad else c for c in text).strip()
    return cleaned or "phoneme"


def _unit_wav_path(row: dict, out_dir: str, unique: bool = False, base_name: str = "") -> str:
    """<display>.wav when unique is False; adds _2, _3 only for collisions."""
    base = _safe_name(base_name or row.get("display") or row.get("name") or "phoneme")
    dst = os.path.join(out_dir, base + ".wav")
    if not unique:
        return dst
    n = 2
    while os.path.exists(dst):
        dst = os.path.join(out_dir, f"{base}_{n}.wav")
        n += 1
    return dst


def write_unit_wav(row: dict, out_dir: str, unique: bool = False, base_name: str = "") -> Optional[str]:
    """Write one candidate as a WAV and return the path (None on failure).

    Every exported WAV is normalised to 44.1 kHz / 24-bit / mono by
    ``export_audio``; the source WAV is only read.
    """
    src = row.get("source") or ""
    if not src or not os.path.isfile(src):
        return None
    os.makedirs(out_dir, exist_ok=True)
    dst = _unit_wav_path(row, out_dir, unique, base_name)
    if not export_audio.write_clip(src, row["start"], row["end"], dst):
        return None
    return dst


# Fields usable in --pattern: {name} {cons} {vowel} {note} {cents} {midi} {ms}
# {index} (1-based within the same name) {bank} (bank file stem).
def pattern_name(row: dict, pattern: str, index: int) -> str:
    note, cents = ("", 0)
    if row.get("midi") is not None:
        note, cents = midi_to_note(float(row["midi"]))
    values = {
        "name": row.get("display") or row.get("name") or "",
        "cons": row.get("cons") or "",
        "vowel": row.get("vowel") or "",
        "note": note,
        "cents": f"{cents:+03d}" if note else "",
        "midi": "" if row.get("midi") is None else f"{float(row['midi']):.2f}",
        "ms": f"{round((row['end'] - row['start']) * 1000)}",
        "index": f"{index:02d}",
        "bank": os.path.splitext(os.path.basename(row.get("bank_path") or ""))[0],
    }
    try:
        return pattern.format(**values)
    except (KeyError, IndexError, ValueError):
        return values["name"]


def export_wavs(conn: sqlite3.Connection, out_dir: str, pattern: str = "{name}",
                favorites_only: bool = False, include_hidden: bool = False) -> int:
    """Write one WAV per candidate. Default name is the pronunciation (だ.wav)."""
    os.makedirs(out_dir, exist_ok=True)
    rows = search(conn, favorite=favorites_only, hidden=True if include_hidden else None)
    counts: Dict[str, int] = {}
    written = 0
    for row in rows:
        key = row.get("display") or row.get("name") or ""
        counts[key] = counts.get(key, 0) + 1
        base = pattern_name(row, pattern, counts[key])
        if write_unit_wav(row, out_dir, unique=True, base_name=base):
            written += 1
    return written


def export_csv(conn: sqlite3.Connection, out_path: str, favorites_only: bool = False,
               include_hidden: bool = False) -> int:
    """Write a spreadsheet-friendly manifest of the visible candidates."""
    import csv
    rows = search(conn, favorite=favorites_only, hidden=True if include_hidden else None)
    os.makedirs(os.path.dirname(os.path.abspath(out_path)), exist_ok=True)
    columns = ["name", "phonemes", "cons", "vowel", "prev_vowel", "next_cons",
               "midi", "note", "seconds", "favorite", "hidden", "tags", "song", "bank", "source"]
    with open(out_path, "w", encoding="utf-8-sig", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(columns)
        for row in rows:
            note = midi_to_note(float(row["midi"]))[0] if row["midi"] is not None else ""
            writer.writerow([
                row.get("display") or row["name"], row["phonemes"], row["cons"], row["vowel"],
                row["prev_vowel"], row["next_cons"], "" if row["midi"] is None else f"{row['midi']:.2f}",
                note, f"{row['end'] - row['start']:.3f}", int(row["favorite"]), int(row["hidden"]),
                row["tags"], row.get("song") or "", os.path.basename(row["bank_path"]), row["source"],
            ])
    return len(rows)


# -------------------------------------------------------------------- UTAU
def _vowel_times(unit: dict) -> Optional[Tuple[float, float]]:
    """Absolute (vowel start, vowel end) from the bank JSON, or None."""
    phonemes = str(unit.get("phonemes") or "").split()
    starts = list(unit.get("phoneme_starts") or [])
    if not phonemes or len(starts) != len(phonemes):
        return None
    index = -1
    for i, p in enumerate(phonemes):
        if p in VOWELS:
            index = i
    if index < 0:
        return None
    begin = float(starts[index])
    end = float(starts[index + 1]) if index + 1 < len(starts) else float(unit["end"])
    return begin, end


def export_utau(conn: sqlite3.Connection, out_dir: str, name: str = "NamaVoice",
                author: str = "", vcv: bool = False, multiple: bool = False,
                favorites_only: bool = False, min_ms: float = 0.0) -> Dict:
    """Write a UTAU folder: WAVs + oto.ini + character.txt.

    CV aliases are the kana name. With ``vcv`` only units that already contain
    the previous vowel (V C V phonemes) get a "a か" alias; nothing is faked.
    ``multiple`` keeps one entry per pitch with a note suffix (かC4).
    ``min_ms`` drops candidates shorter than that (0 = keep everything), so
    tiny cut-offs never reach UTAU.
    """
    os.makedirs(out_dir, exist_ok=True)
    rows = search(conn, favorite=favorites_only)
    banks: Dict[str, dict] = {}
    counts: Dict[str, int] = {}
    oto_lines = []
    written_names = set()
    written = skipped = too_short = vcv_entries = 0
    for row in rows:
        if min_ms > 0 and (float(row["end"]) - float(row["start"])) * 1000.0 < min_ms:
            too_short += 1
            continue
        bank = banks.get(row["bank_path"])
        if bank is None:
            try:
                bank = core.load_voicebank(row["bank_path"])
            except Exception:  # noqa: BLE001 - a bad bank just skips its rows
                bank = {"units": []}
            banks[row["bank_path"]] = bank
        unit = None
        for i, u in enumerate(bank.get("units", [])):
            uid = u.get("id") or f"index:{i}"
            if uid == row["unit_id"]:
                unit = u
                break
        if unit is None:
            skipped += 1
            continue
        times = _vowel_times(unit)
        if times is None:
            skipped += 1
            continue
        begin, vend = times
        pre = max(0.0, (begin - float(unit["start"])) * 1000.0)
        if not multiple:
            if row["name"] in counts:
                continue
        key = row["display"]
        counts[key] = counts.get(key, 0) + 1
        suffix = ""
        if multiple:
            if row["midi"] is not None:
                suffix = midi_to_note(float(row["midi"]))[0]
            else:
                suffix = str(counts[key])
        # UTAU style: あA3 / かC4. No underscores in file names or aliases.
        stem = _safe_name(f"{row['display']}{suffix}").replace("_", "")
        unique_stem, n = stem, 2
        while os.path.exists(os.path.join(out_dir, unique_stem + ".wav")) or unique_stem in written_names:
            unique_stem = f"{stem}{n}"
            n += 1
        if not write_unit_wav(row, out_dir, unique=False, base_name=unique_stem):
            skipped += 1
            continue
        written_names.add(unique_stem)
        written += 1
        overlap = max(5.0, min(30.0, pre * 0.5))
        blank = max(0.0, (float(unit["end"]) - vend) * 1000.0)
        alias = f"{row['display']}{suffix}".replace("_", "")
        oto_lines.append(f"{unique_stem}.wav={alias},0,{overlap:.1f},{pre:.1f},{blank:.1f},0")
        phonemes = str(unit.get("phonemes") or "").split()
        if vcv and len(phonemes) == 3 and phonemes[0] in VOWELS and phonemes[2] in VOWELS:
            oto_lines.append(f"{unique_stem}.wav={phonemes[0]} {alias},0,{overlap:.1f},{pre:.1f},{blank:.1f},0")
            vcv_entries += 1
    with open(os.path.join(out_dir, "oto.ini"), "w", encoding="cp932", errors="replace", newline="\r\n") as f:
        f.write("\n".join(oto_lines) + ("\n" if oto_lines else ""))
    with open(os.path.join(out_dir, "character.txt"), "w", encoding="cp932", errors="replace", newline="\r\n") as f:
        f.write(f"name={name}\n")
        if author:
            f.write(f"author={author}\n")
    return {"wav": written, "oto": len(oto_lines), "vcv": vcv_entries, "skipped": skipped,
            "too_short": too_short, "min_ms": min_ms}





# ------------------------------------------------------------------- import
FONEMUDB_NAME = re.compile(r"^(?P<name>.+?)_(?P<note>[A-G]#?-?\d)?_(?P<num>\d+)$")


def import_fonemudb(folder: str, out_dir: str, song: str = "") -> Dict:
    """One bank per cut WAV, named <kana>_<note>_<n>.wav (音素DB style).

    The third field is kept in the unit id; pitch comes from the note name only.
    No sample rate/channel assumption: each WAV becomes its own source.
    """
    os.makedirs(out_dir, exist_ok=True)
    files = sorted(glob.glob(os.path.join(folder, "**", "*.wav"), recursive=True))
    made = skipped = 0
    for path in files:
        stem = os.path.splitext(os.path.basename(path))[0]
        m = FONEMUDB_NAME.match(stem)
        if not m:
            skipped += 1
            continue
        name = m.group("name")
        note = m.group("note") or ""
        num = m.group("num")
        midi = note_to_midi(note) if note else None
        try:
            facts = core.read_wav(path)
        except Exception:
            skipped += 1
            continue
        if facts.frames <= 0:
            skipped += 1
            continue
        song_name = song or os.path.basename(os.path.dirname(path))
        bank = {
            "format": "nama-voices", "version": 1,
            "source": {
                "path": os.path.abspath(path), "sha256": facts.sha256,
                "sampleRate": facts.sample_rate, "channels": facts.channels,
                "bits": facts.width * 8, "frames": facts.frames,
                "seconds": round(facts.seconds, 6),
            },
            "lyrics": "", "model": "imported-音素DB",
            "units": [{
                "id": f"import-{num}", "name": name, "phonemes": "",
                "phoneme_starts": [],
                "start": 0.0, "end": round(facts.seconds, 6),
                "confidence": "checked",
                "pitch": {"midi": midi, "hz": None, "strength": 0.0},
            }],
        }
        out = os.path.join(out_dir, f"{song_name}__{stem}.nama-voices.json")
        if os.path.exists(out):
            skipped += 1
            continue
        core.save_voicebank(out, bank)
        made += 1
    return {"written": made, "skipped": skipped, "files": len(files)}


# ---------------------------------------------------------------------- CLI
def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="cmd", required=True)
    p_build = sub.add_parser("build", help="index one or more bank folders")
    p_build.add_argument("folder", nargs="+")
    p_build.add_argument("--db", default="")
    p_build.add_argument("--force", action="store_true")
    p_build.add_argument("--no-prune", action="store_true",
                         help="keep rows whose JSON was removed")
    p_list = sub.add_parser("list")
    p_list.add_argument("folder")
    p_list.add_argument("--db", default="")
    p_list.add_argument("--name", default="")
    p_list.add_argument("--cons", default="")
    p_list.add_argument("--vowel", default="")
    p_list.add_argument("--prev-vowel", default="")
    p_list.add_argument("--next-cons", default="")
    p_list.add_argument("--text", default="")
    p_list.add_argument("--song", default="")
    p_list.add_argument("--tags", default="")
    p_list.add_argument("--favorites", action="store_true")
    p_list.add_argument("--hidden", action="store_true", help="show only hidden rows")
    p_list.add_argument("--all", action="store_true", help="include hidden rows")
    p_list.add_argument("--exact", action="store_true")
    p_list.add_argument("--min-midi", type=float)
    p_list.add_argument("--max-midi", type=float)
    p_list.add_argument("--sort", default="name",
                        choices=["name", "midi", "length", "bank", "cons", "vowel",
                                 "prev_vowel", "next_cons", "favorite", "checked", "song"])
    p_list.add_argument("--desc", action="store_true", help="descending")
    p_flag = sub.add_parser("flag", help="favourite / hidden / tags for matching rows")
    p_flag.add_argument("folder")
    p_flag.add_argument("--db", default="")
    p_flag.add_argument("--name", default="")
    p_flag.add_argument("--cons", default="")
    p_flag.add_argument("--vowel", default="")
    p_flag.add_argument("--prev-vowel", default="")
    p_flag.add_argument("--next-cons", default="")
    p_flag.add_argument("--text", default="")
    p_flag.add_argument("--exact", action="store_true")
    p_flag.add_argument("--favorite", action="store_true")
    p_flag.add_argument("--unfavorite", action="store_true")
    p_flag.add_argument("--hide", action="store_true")
    p_flag.add_argument("--show", action="store_true")
    p_flag.add_argument("--tag", default=None)
    p_dup = sub.add_parser("duplicates", help="list near-copies of the same sound")
    p_dup.add_argument("folder")
    p_dup.add_argument("--db", default="")
    p_dup.add_argument("--hide", action="store_true", help="hide every copy but one")
    p_lib = sub.add_parser("export-library")
    p_lib.add_argument("folder")
    p_lib.add_argument("out")
    p_lib.add_argument("--db", default="")
    p_wav = sub.add_parser("export-wavs")
    p_wav.add_argument("folder")
    p_wav.add_argument("out_dir")
    p_wav.add_argument("--db", default="")
    p_wav.add_argument("--pattern", default="{name}",
                       help="{name} {cons} {vowel} {note} {cents} {midi} {ms} {index} {bank}")
    p_wav.add_argument("--favorites", action="store_true")
    p_wav.add_argument("--hidden", action="store_true", help="include hidden rows")
    p_csv = sub.add_parser("export-csv")
    p_csv.add_argument("folder")
    p_csv.add_argument("out")
    p_csv.add_argument("--db", default="")
    p_csv.add_argument("--favorites", action="store_true")
    p_csv.add_argument("--hidden", action="store_true", help="include hidden rows")
    p_utau = sub.add_parser("export-utau", help="UTAU folder: WAVs + oto.ini")
    p_utau.add_argument("folder")
    p_utau.add_argument("out_dir")
    p_utau.add_argument("--db", default="")
    p_utau.add_argument("--name", default="NamaVoice")
    p_utau.add_argument("--author", default="")
    p_utau.add_argument("--vcv", action="store_true")
    p_utau.add_argument("--multiple", action="store_true")
    p_utau.add_argument("--favorites", action="store_true")
    p_utau.add_argument("--min-ms", type=float, default=0.0,
                        help="skip candidates shorter than this (milliseconds)")
    p_imp = sub.add_parser("import-fonemudb")
    p_imp.add_argument("folder")
    p_imp.add_argument("out_dir")
    args = parser.parse_args(argv)

    if args.cmd == "import-fonemudb":
        info = import_fonemudb(args.folder, args.out_dir)
        print(f"書き出し {info['written']} / 読み飛ばし {info['skipped']} （WAV {info['files']}件）")
        return 0

    first_folder = args.folder[0] if isinstance(args.folder, list) else args.folder
    db_path = args.db or os.path.join(first_folder, DB_DEFAULT)
    conn = connect(db_path)
    try:
        if args.cmd == "build":
            info = index_folders(conn, args.folder, args.force, prune=not args.no_prune)
            print(f"banks +{info['banks']} (skip {info['skipped']}, -{info['removed']}), units {info['units']}")
        elif args.cmd == "list":
            hidden = True if args.hidden else (False if args.all else None)
            rows = search(conn, args.name, args.exact, args.min_midi, args.max_midi, sort=args.sort,
                          cons=args.cons, vowel=args.vowel, prev_vowel=args.prev_vowel,
                          next_cons=args.next_cons, text=args.text, tags=args.tags, song=args.song,
                          favorite=args.favorites, hidden=hidden, descending=args.desc)
            for r in rows:
                midi = midi_label(r["midi"])
                mark = ("★" if r["favorite"] else " ") + ("×" if r["hidden"] else " ")
                print(f"{mark} {r['display']:<4}{midi:>5}  {(r['end'] - r['start']) * 1000:5.0f}ms  "
                      f"子音={r['cons'] or '-':<4} 母音={r['vowel'] or '-':<2} "
                      f"前母音={r['prev_vowel'] or '-':<2} 次子音={r['next_cons'] or '-':<4} "
                      f"{os.path.basename(r['bank_path'])}")
            print(f"{len(rows)} 件")
        elif args.cmd == "flag":
            rows = search(conn, args.name, args.exact, cons=args.cons, vowel=args.vowel,
                          prev_vowel=args.prev_vowel, next_cons=args.next_cons,
                          text=args.text, hidden=False)
            fav = True if args.favorite else (False if args.unfavorite else None)
            hid = True if args.hide else (False if args.show else None)
            if fav is None and hid is None and args.tag is None:
                print("--favorite / --unfavorite / --hide / --show / --tag のどれかを指定してください。")
                return 2
            changed = set_flags(conn, rows, favorite=fav, hidden=hid, tags=args.tag)
            print(f"{changed} 件を更新しました。")
        elif args.cmd == "duplicates":
            groups = duplicates(conn)
            for group in groups:
                print(f"{group['name']:<4} {group['midi']:>6.2f}  {group['count']}件  "
                      f"残す: {(group['keep']['end'] - group['keep']['start']) * 1000:.0f}ms "
                      f"{os.path.basename(group['keep']['bank_path'])}")
            print(f"{len(groups)} 組（候補 {sum(g['count'] for g in groups)} 件）")
            if args.hide:
                print(f"{hide_duplicates(conn)} 件を非表示にしました。")
        elif args.cmd == "export-library":
            print(export_library(conn, args.out))
        elif args.cmd == "export-wavs":
            print(f"{export_wavs(conn, args.out_dir, args.pattern, args.favorites, args.hidden)} 件のWAVを書き出しました。")
        elif args.cmd == "export-csv":
            print(f"{export_csv(conn, args.out, args.favorites, args.hidden)} 件を書き出しました。")
        elif args.cmd == "export-utau":
            info = export_utau(conn, args.out_dir, args.name, args.author,
                               vcv=args.vcv, multiple=args.multiple,
                               favorites_only=args.favorites, min_ms=args.min_ms)
            short = f" / 短すぎる {info['too_short']}" if info["min_ms"] > 0 else ""
            print(f"WAV {info['wav']} / oto {info['oto']}（VCV {info['vcv']}） / スキップ {info['skipped']}{short}")
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())

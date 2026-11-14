"""JSON Lines sidecar for the Nama Onso DB window pilot (stage 1).

The GUI (`gui-tauri/`) speaks one JSON object per line over stdin/stdout.
Everything that touches the library lives here and calls the existing
``db.py`` / ``core.py`` / ``export_audio.py`` unchanged.

    request : {"id": 1, "method": "db.search", "params": {...}}
    response: {"id": 1, "ok": true, "result": ...}
              {"id": 1, "ok": false, "error": "..."}
    event   : {"event": "progress", "data": {...}}   (before the response)

stdout is reserved for JSON. Logs and tracebacks go to stderr.

Deliberate extensions over the spec table (all optional, result-only):
* ``db.rename`` also returns the previous aliases so the GUI can undo exactly.
* ``db.export_csv`` accepts ``favorites_only`` / ``include_hidden``.
* ``db.export_utau`` accepts ``name``, ``author``, ``vcv``, ``multiple``,
  ``favorites`` (same knobs as the tkinter dialog).
* ``db.build`` accepts several folders and emits one progress event per folder.
"""
from __future__ import annotations

import hashlib
import json
import os
import sys
import tempfile
import traceback
from typing import Any, Dict, List, Optional

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import core  # noqa: E402
import db  # noqa: E402

PREVIEW_DIR = os.path.join(tempfile.gettempdir(), "nama_phoneme_preview")
DRAG_DIR = os.path.join(tempfile.gettempdir(), "nama_phoneme_drag")

ROW_KEYS = ("bank_path", "unit_id", "name", "phonemes", "start", "end", "midi", "hz",
            "strength", "checked", "source", "lyrics", "cons", "vowel", "prev_vowel",
            "next_cons", "raw_name", "favorite", "hidden", "tags", "song")

ROW_SQL = (
    "SELECT u.bank_path, u.unit_id, COALESCE(a.name, u.name), u.phonemes, u.start, u.end,"
    " u.midi, u.hz, u.strength, u.checked, b.source, b.lyrics, u.cons, u.vowel,"
    " u.prev_vowel, u.next_cons, u.name,"
    " COALESCE(f.favorite, 0), COALESCE(f.hidden, 0), COALESCE(f.tags, ''),"
    " COALESCE(b.song, '')"
    " FROM units u JOIN banks b ON b.path = u.bank_path"
    " LEFT JOIN aliases a ON a.bank_path = u.bank_path AND a.unit_id = u.unit_id"
    " LEFT JOIN unit_flags f ON f.bank_path = u.bank_path AND f.unit_id = u.unit_id"
)


def log(*parts: Any) -> None:
    print(*parts, file=sys.stderr, flush=True)


def emit(payload: Dict[str, Any]) -> None:
    data = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    sys.stdout.buffer.write(data.encode("utf-8") + b"\n")
    sys.stdout.buffer.flush()


def progress(**data: Any) -> None:
    emit({"event": "progress", "data": data})


def _decorate(row: dict) -> dict:
    """Add the two display-only values the table needs (spec section 3)."""
    row["note"] = db.midi_label(row.get("midi"))
    row["ms"] = round((float(row["end"]) - float(row["start"])) * 1000.0, 1)
    return row


def _number(value: Any) -> Optional[float]:
    if value is None or value == "":
        return None
    return float(value)


def _flag(value: Any) -> Optional[bool]:
    if value is None:
        return None
    return bool(value)


class Sidecar:
    def __init__(self) -> None:
        self.conn = None
        self.db_path = ""
        self.folder = ""

    # ------------------------------------------------------------------ state
    def close(self) -> None:
        if self.conn is not None:
            try:
                self.conn.close()
            except Exception:  # noqa: BLE001 - closing twice must not matter
                pass
        self.conn = None

    def _require(self):
        if self.conn is None:
            raise RuntimeError("索引が開かれていません。先にフォルダを開くか「索引を作る」を押してください。")
        return self.conn

    def _row(self, bank_path: str, unit_id: str) -> Optional[dict]:
        conn = self._require()
        row = conn.execute(ROW_SQL + " WHERE u.bank_path = ? AND u.unit_id = ?",
                           (bank_path, unit_id)).fetchone()
        if row is None:
            return None
        out = dict(zip(ROW_KEYS, row))
        out["display"] = core.display_name(out["name"], out["phonemes"])
        return _decorate(out)

    def _row_refs(self, params: Dict[str, Any]) -> List[dict]:
        refs = params.get("selected") or []
        return [{"bank_path": str(r.get("bank_path") or ""),
                 "unit_id": str(r.get("unit_id") or "")} for r in refs if r]

    # --------------------------------------------------------------- methods
    def db_open(self, params: Dict[str, Any]) -> Dict[str, Any]:
        folder = str(params.get("folder") or "").strip()
        if not folder:
            raise ValueError("フォルダが空です。")
        folder = os.path.abspath(folder)
        if not os.path.isdir(folder):
            raise ValueError(f"フォルダが見つかりません: {folder}")
        db_path = os.path.join(folder, db.DB_DEFAULT)
        self.close()
        self.folder = folder
        self.db_path = db_path
        exists = os.path.isfile(db_path)
        banks = units = 0
        if exists:
            self.conn = db.connect(db_path)
            banks = self.conn.execute("SELECT COUNT(*) FROM banks").fetchone()[0]
            units = self.conn.execute("SELECT COUNT(*) FROM units").fetchone()[0]
        return {"db_path": db_path, "folder": folder, "exists": exists,
                "banks": banks, "units": units}

    def db_build(self, params: Dict[str, Any]) -> Dict[str, Any]:
        folders = [os.path.abspath(str(f)) for f in (params.get("folders") or []) if str(f).strip()]
        if not folders:
            raise ValueError("素材集フォルダを指定してください。")
        target = os.path.join(folders[0], db.DB_DEFAULT)
        if self.conn is None or self.db_path != target:
            self.close()
            self.conn = db.connect(target)
            self.db_path = target
            self.folder = folders[0]
        totals = {"banks": 0, "skipped": 0, "removed": 0, "units": 0}
        for index, folder in enumerate(folders, start=1):
            progress(phase="index", folder=folder, index=index, total=len(folders))
            info = db.index_folders(self.conn, [folder])
            for key in totals:
                totals[key] += int(info.get(key) or 0)
        banks = self.conn.execute("SELECT COUNT(*) FROM banks").fetchone()[0]
        units = self.conn.execute("SELECT COUNT(*) FROM units").fetchone()[0]
        progress(phase="index", folder="", index=len(folders), total=len(folders), done=True)
        return {**totals, "db_path": target, "folder": os.path.dirname(target),
                "banks_total": banks, "units_total": units}

    def db_search(self, params: Dict[str, Any]) -> Dict[str, Any]:
        conn = self._require()
        rows = db.search(
            conn,
            name=str(params.get("name") or "").strip(),
            exact=bool(params.get("exact")),
            min_midi=_number(params.get("min_midi")),
            max_midi=_number(params.get("max_midi")),
            min_ms=_number(params.get("min_ms")),
            sort=str(params.get("sort") or "name"),
            cons=str(params.get("cons") or "").strip(),
            vowel=str(params.get("vowel") or "").strip(),
            prev_vowel=str(params.get("prev_vowel") or "").strip(),
            next_cons=str(params.get("next_cons") or "").strip(),
            text=str(params.get("text") or "").strip(),
            hidden=_flag(params.get("hidden")),
            favorite=bool(params.get("favorite")),
            tags=str(params.get("tags") or "").strip(),
            descending=bool(params.get("descending")),
            song=str(params.get("song") or "").strip(),
        )
        return {"rows": [_decorate(row) for row in rows]}

    def db_rename(self, params: Dict[str, Any]) -> Dict[str, Any]:
        conn = self._require()
        bank_path = str(params.get("bank_path") or "")
        unit_id = str(params.get("unit_id") or "")
        name = str(params.get("name") or "")
        old = db.get_alias(conn, bank_path, unit_id)
        db.set_alias(conn, bank_path, unit_id, name)
        return {"old": old}

    def db_set_flags(self, params: Dict[str, Any]) -> Dict[str, Any]:
        conn = self._require()
        rows = [{"bank_path": str(r.get("bank_path") or ""), "unit_id": str(r.get("unit_id") or "")}
                for r in (params.get("rows") or []) if r]
        changed = db.set_flags(
            conn, rows,
            favorite=_flag(params.get("favorite")),
            hidden=_flag(params.get("hidden")),
            tags=None if params.get("tags") is None else str(params.get("tags") or ""),
        )
        return {"changed": changed}

    def db_duplicates(self, params: Dict[str, Any]) -> Dict[str, Any]:
        conn = self._require()
        cents = int(params.get("cents") or 25)
        length_ms = int(params.get("length_ms") or 40)
        groups = db.duplicates(conn, cents=cents, length_ms=length_ms)
        # db.duplicates returns db.search rows; add the same note/ms the table uses.
        for group in groups:
            group["keep"] = _decorate(group["keep"])
            group["drop"] = [_decorate(row) for row in group["drop"]]
        return {"groups": groups}

    def db_hide_duplicates(self, _params: Dict[str, Any]) -> Dict[str, Any]:
        conn = self._require()
        return {"hidden": db.hide_duplicates(conn)}

    def db_prepare_drag(self, params: Dict[str, Any]) -> Dict[str, Any]:
        row = self._row(str(params.get("bank_path") or ""), str(params.get("unit_id") or ""))
        if row is None:
            raise RuntimeError("候補が見つかりません。")
        tag = hashlib.sha1((row["bank_path"] + "|" + row["unit_id"]).encode("utf-8")).hexdigest()[:10]
        path = db.write_unit_wav(row, os.path.join(DRAG_DIR, tag))
        if not path:
            raise RuntimeError("ドラッグ用のWAVを作れませんでした（元WAVが見つかりません）。")
        return {"path": path}

    def db_preview(self, params: Dict[str, Any]) -> Dict[str, Any]:
        row = self._row(str(params.get("bank_path") or ""), str(params.get("unit_id") or ""))
        if row is None:
            raise RuntimeError("候補が見つかりません。")
        tag = hashlib.sha1((row["bank_path"] + "|" + row["unit_id"]).encode("utf-8")).hexdigest()[:10]
        path = db.write_unit_wav(row, PREVIEW_DIR, base_name=tag)
        if not path:
            raise RuntimeError("試聴用のWAVを作れませんでした（元WAVが見つかりません）。")
        return {"path": path}

    def db_export_wavs(self, params: Dict[str, Any]) -> Dict[str, Any]:
        conn = self._require()
        out_dir = str(params.get("out_dir") or "").strip()
        if not out_dir:
            raise ValueError("書き出し先が空です。")
        os.makedirs(out_dir, exist_ok=True)
        refs = self._row_refs(params)
        if refs:
            written = 0
            for ref in refs:
                row = self._row(ref["bank_path"], ref["unit_id"])
                if row and db.write_unit_wav(row, out_dir, unique=True):
                    written += 1
            return {"written": written}
        pattern = str(params.get("pattern") or "{name}")
        return {"written": db.export_wavs(conn, out_dir, pattern=pattern)}

    def db_export_csv(self, params: Dict[str, Any]) -> Dict[str, Any]:
        conn = self._require()
        out_path = str(params.get("out_path") or "").strip()
        if not out_path:
            raise ValueError("保存先が空です。")
        written = db.export_csv(conn, out_path,
                                favorites_only=bool(params.get("favorites_only")),
                                include_hidden=bool(params.get("include_hidden")))
        return {"written": written}

    def db_export_utau(self, params: Dict[str, Any]) -> Dict[str, Any]:
        conn = self._require()
        out_dir = str(params.get("out_dir") or "").strip()
        if not out_dir:
            raise ValueError("書き出し先が空です。")
        return db.export_utau(
            conn, out_dir,
            name=str(params.get("name") or "NamaVoice"),
            author=str(params.get("author") or ""),
            vcv=bool(params.get("vcv")),
            multiple=bool(params.get("multiple")),
            favorites_only=bool(params.get("favorites")),
            min_ms=float(params.get("min_ms") or 0.0),
        )

    def db_export_library(self, params: Dict[str, Any]) -> Dict[str, Any]:
        conn = self._require()
        out_path = str(params.get("out_path") or "").strip()
        if not out_path:
            raise ValueError("保存先が空です。")
        return {"path": db.export_library(conn, out_path)}

    def db_import_fonemudb(self, params: Dict[str, Any]) -> Dict[str, Any]:
        folder = str(params.get("folder") or "").strip()
        out_dir = str(params.get("out_dir") or "").strip()
        if not folder or not out_dir:
            raise ValueError("取り込み元と書き出し先を指定してください。")
        return db.import_fonemudb(folder, out_dir, song=str(params.get("song") or ""))

    def db_source(self, params: Dict[str, Any]) -> Dict[str, Any]:
        row = self._row(str(params.get("bank_path") or ""), str(params.get("unit_id") or ""))
        if row is None:
            raise RuntimeError("候補が見つかりません。")
        return {"source": row["source"], "start": row["start"], "end": row["end"],
                "name": row["display"], "bank_path": row["bank_path"],
                "exists": bool(row["source"]) and os.path.isfile(row["source"])}

    # -------------------------------------------------------------- dispatch
    def dispatch(self, method: str, params: Dict[str, Any]) -> Any:
        handler = {
            "db.open": self.db_open,
            "db.build": self.db_build,
            "db.search": self.db_search,
            "db.rename": self.db_rename,
            "db.set_flags": self.db_set_flags,
            "db.duplicates": self.db_duplicates,
            "db.hide_duplicates": self.db_hide_duplicates,
            "db.prepare_drag": self.db_prepare_drag,
            "db.preview": self.db_preview,
            "db.export_wavs": self.db_export_wavs,
            "db.export_csv": self.db_export_csv,
            "db.export_utau": self.db_export_utau,
            "db.export_library": self.db_export_library,
            "db.import_fonemudb": self.db_import_fonemudb,
            "db.source": self.db_source,
        }.get(method)
        if handler is None:
            raise ValueError(f"unknown method: {method}")
        return handler(params)


def main() -> int:
    server = Sidecar()
    log("onso sidecar ready", ROOT)
    try:
        for raw in sys.stdin.buffer:
            line = raw.decode("utf-8", errors="replace").strip()
            if not line:
                continue
            try:
                message = json.loads(line)
            except json.JSONDecodeError as exc:
                log("bad request:", exc, line[:300])
                continue
            request_id = message.get("id")
            method = str(message.get("method") or "")
            params = message.get("params") or {}
            try:
                result = server.dispatch(method, params)
                emit({"id": request_id, "ok": True, "result": result})
            except Exception as exc:  # noqa: BLE001 - one bad call must not stop the server
                log(f"{method} failed:", repr(exc))
                log(traceback.format_exc())
                emit({"id": request_id, "ok": False, "error": str(exc)})
    except KeyboardInterrupt:
        pass
    finally:
        server.close()
        log("onso sidecar stopped")
    return 0


if __name__ == "__main__":
    sys.exit(main())

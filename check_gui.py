"""GUI check for undo/redo, boundary drag, the 要確認 filter and DB undo.

Runs with synthetic audio only and needs a desktop session (tkinter window).
Run: python check_gui.py
"""
from __future__ import annotations

import os
import shutil
import sys
import tempfile
import time
import tkinter as tk

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import core  # noqa: E402
import gui  # noqa: E402
import appenv  # noqa: E402
import selftest  # noqa: E402


def expect(cond, message):
    if not cond:
        raise AssertionError(message)


class FakeEvent:
    """Minimal stand-in for a tkinter mouse event."""

    def __init__(self, x: int):
        self.x = x
        self.state = 0
        self.delta = 1


def main() -> int:
    tmp = tempfile.mkdtemp(prefix="jinriki-gui-")
    try:
        wav = os.path.join(tmp, "あい.wav")
        selftest.write_synth(wav)

        root = gui.TkinterDnD.Tk() if gui.HAVE_DND else tk.Tk()
        root.geometry("1000x700")
        app = gui.App(root)
        root.update()
        expect(appenv.asset_path("NamaOnso.ico"), "application icon asset missing")
        app.load(wav)
        root.update()

        # The wizard must build without errors; the auto-start is then muted.
        app.show_wizard(force=True)
        root.update()
        tops = [w for w in root.winfo_children() if isinstance(w, tk.Toplevel)]
        expect(tops, "the wizard did not open")
        for w in tops:
            w.destroy()
        app.show_wizard = lambda *a, **k: None

        app.units = [core.Unit("あ", ["a"], 0.10, 0.40, "", [0.10]),
                     core.Unit("い", ["i"], 0.50, 0.90, "checked", [0.50])]
        app.pitches = [{}, {}]
        app.selected = 0
        app.edge_var.set("start")
        app.refresh_list()
        root.update()
        expect(app.pending_var.get().startswith("要確認 1 件"), app.pending_var.get())

        # nudge -> undo -> redo
        app.nudge(10)
        expect(abs(app.units[0].start - 0.11) < 1e-6, f"nudge start {app.units[0].start}")
        app.undo()
        expect(abs(app.units[0].start - 0.10) < 1e-6, f"undo start {app.units[0].start}")
        expect(app.units[0].confidence == "", "undo lost the confidence state")
        app.redo()
        expect(abs(app.units[0].start - 0.11) < 1e-6, f"redo start {app.units[0].start}")

        # one drag = one undo step, and undo returns to the pre-drag value
        app.view = [0.0, app.facts.seconds]
        app.redraw()
        root.update()
        app.on_press(FakeEvent(int(app.x_at(0.11))))
        expect(app.drag == 0 and app.edge == "start", f"grab {app.drag} {app.edge}")
        depth = len(app.undo_stack)
        app.on_drag(FakeEvent(int(app.x_at(0.15))))
        app.on_drag(FakeEvent(int(app.x_at(0.18))))
        expect(len(app.undo_stack) == depth + 1, "a drag pushed more than one snapshot")
        app.on_release(FakeEvent(0))
        expect(abs(app.units[0].start - app.time_at(int(app.x_at(0.18)))) < 1e-3,
               f"drag start {app.units[0].start}")
        app.undo()
        expect(abs(app.units[0].start - 0.11) < 1e-6, f"undo after drag {app.units[0].start}")

        # "要確認だけ表示" keeps the listbox row -> unit index mapping intact
        app.units[0].confidence = ""
        app.units[1].confidence = ""
        app.only_pending.set(True)
        app.refresh_list()
        expect(app.visible == [0, 1], f"filter visible {app.visible}")
        app.units[0].confidence = "checked"
        app.refresh_list()
        expect(app.visible == [1], f"filter after check {app.visible}")
        app.listbox.selection_clear(0, "end")
        app.listbox.selection_set(0)
        app.on_select()
        expect(app.selected == 1, f"filtered selection {app.selected}")
        app.only_pending.set(False)
        app.refresh_list()
        expect(app.visible == [0, 1], "filter did not clear")

        # DB window: flag and alias undo/redo never touch the source JSON
        bank_path = core.default_bank_path(wav)
        # Undo restored the drag, so make the saved ranges match the phoneme times.
        app.units[0].start = 0.10
        app.units[0].phoneme_starts = [0.10]
        bank = core.make_voicebank(app.facts, app.units, "あい", app.model, [{}, {}])
        core.save_voicebank(bank_path, bank)
        with open(bank_path, "rb") as fh:
            before = fh.read()
        db_win = gui.DBWindow(app)
        db_win.folder.set(tmp)
        db_win.build()
        root.update()
        expect(db_win.rows, "index produced no rows")
        row = db_win.rows[0]
        db_win.push_undo("★切替", flags=[
            (row["bank_path"], row["unit_id"],
             (int(row["favorite"]), int(row["hidden"]), row.get("tags") or ""),
             (1, int(row["hidden"]), row.get("tags") or ""))])
        db_win.dbmod.set_flags(db_win.conn, [row], favorite=True)
        db_win.refresh()
        expect(any(r["favorite"] for r in db_win.rows), "favourite not set")
        db_win.undo()
        expect(not any(r["favorite"] for r in db_win.rows), "undo did not clear favourite")
        db_win.redo()
        expect(any(r["favorite"] for r in db_win.rows), "redo did not restore favourite")

        old = db_win.dbmod.get_alias(db_win.conn, row["bank_path"], row["unit_id"])
        db_win.push_undo("名前を変更",
                         aliases=[(row["bank_path"], row["unit_id"], old, "うぃ")])
        db_win.dbmod.set_alias(db_win.conn, row["bank_path"], row["unit_id"], "うぃ")
        db_win.undo()
        expect(db_win.dbmod.get_alias(db_win.conn, row["bank_path"], row["unit_id"]) == old,
               "alias undo failed")
        db_win.redo()
        expect(db_win.dbmod.get_alias(db_win.conn, row["bank_path"], row["unit_id"]) == "うぃ",
               "alias redo failed")

        # UTAU dialog: the "短い候補" field reaches export_utau's min_ms filter
        out_dir = os.path.join(tmp, "utau_out")
        os.makedirs(out_dir, exist_ok=True)
        gui.filedialog.askdirectory = lambda **kw: out_dir
        db_win.export_utau()
        root.update()
        dialogs = [w for w in db_win.win.winfo_children() if isinstance(w, tk.Toplevel)]
        expect(dialogs, "the UTAU dialog did not open")
        dlg = dialogs[0]
        found, stack = [], [dlg]
        while stack:
            widget = stack.pop()
            stack.extend(widget.winfo_children())
            found.append(widget)
        entries = [w for w in found if w.winfo_class() == "TEntry"]
        buttons = [w for w in found if w.winfo_class() == "TButton"
                   and str(w.cget("text")) == "書き出す"]
        expect(len(entries) >= 3 and buttons, f"UTAU dialog widgets {len(entries)}")
        min_entry = next((w for w in entries if str(w.get()).strip() == "0"), None)
        expect(min_entry is not None, "the 短い候補 entry was not found")
        dlg.setvar(min_entry.cget("textvariable"), "350")
        buttons[0].invoke()
        root.update()
        files = sorted(os.listdir(out_dir))
        expect("い.wav" in files, f"UTAU dialog kept nothing: {files}")
        expect("あ.wav" not in files, f"UTAU dialog kept the short row: {files}")
        expect("短い候補 1" in db_win.status.get(), f"UTAU status: {db_win.status.get()}")
        import wave as wave_mod
        with wave_mod.open(os.path.join(out_dir, "い.wav"), "rb") as fh:
            expect(fh.getframerate() == 44100 and fh.getnchannels() == 1
                   and fh.getsampwidth() == 3,
                   "UTAU WAV must be 44.1kHz/24bit/mono")
        db_win.win.destroy()
        with open(bank_path, "rb") as fh:
            expect(fh.read() == before, "the voicebank JSON was modified by DB undo")

        # Curator window: run the real selection pipeline from the GUI
        takes_dir = os.path.join(tmp, "curate_takes")
        os.makedirs(takes_dir)
        selftest._write_take(os.path.join(takes_dir, "あA3.wav"),
                             [("noise", 15, 0.003), ("tone", 320, 0.30)])
        selftest._write_take(os.path.join(takes_dir, "あC4.wav"),
                             [("noise", 15, 0.003), ("tone", 300, 0.30)])
        selftest._write_take(os.path.join(takes_dir, "かD4.wav"),
                             [("noise", 20, 0.003), ("noise", 25, 0.3), ("tone", 240, 0.3)])
        curate_out = os.path.join(tmp, "curate_gui_out")
        import curate_gui
        curator = curate_gui.CuratorWindow(app)
        curator.root_var.set(takes_dir)
        curator.out_var.set(curate_out)
        curator.start()
        deadline = time.time() + 60
        while curator.running and time.time() < deadline:
            root.update()
            time.sleep(0.05)
        root.update()
        expect(not curator.running, "the curator window did not finish")
        expect(os.path.isfile(os.path.join(curate_out, "selection.csv")),
               "the curator window wrote no selection.csv")
        expect(len(os.listdir(os.path.join(curate_out, "wav"))) >= 2,
               "the curator window wrote no WAVs")
        curator.win.destroy()
        root.destroy()
        print("PASS: gui undo/redo, one step per drag, pending filter, db flag/alias undo,"
              " utau min-length filter, curator window")
        return 0
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    try:
        sys.exit(main())
    except AssertionError as e:
        print(f"FAIL: {e}")
        sys.exit(1)

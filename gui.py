"""Nama Onso GUI: WAV + lyrics -> phoneme labels -> mora units -> voicebank JSON.

Windows / Python 3 (tkinter, winsound, stdlib only).
Run: python gui.py [wav]
"""
from __future__ import annotations

import queue
import json
import math
import os
import struct
import sys
import tempfile
import threading
import tkinter as tk
import wave
from tkinter import filedialog, messagebox, simpledialog, ttk

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import appenv  # noqa: E402
import core  # noqa: E402

try:
    import winsound
except ImportError:  # pragma: no cover - non-Windows
    winsound = None

try:  # OS-level file drag needs tkdnd; the app still runs without it.
    from tkinterdnd2 import TkinterDnD, DND_FILES, COPY as DND_COPY
    HAVE_DND = True
except Exception:  # pragma: no cover - optional dependency
    TkinterDnD = None
    DND_FILES = "DND_Files"
    DND_COPY = "copy"
    HAVE_DND = False

NUDGES = (-50, -10, -5, 5, 10, 50)   # ms


def settings_path() -> str:
    base = os.environ.get("APPDATA") or os.path.expanduser("~")
    return os.path.join(base, "Nama", "jinriki-prep.json")


def load_settings() -> dict:
    try:
        with open(settings_path(), "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:  # noqa: BLE001 - a missing/broken file just means defaults
        return {}


def save_settings(data: dict) -> None:
    try:
        os.makedirs(os.path.dirname(settings_path()), exist_ok=True)
        with open(settings_path(), "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception:  # noqa: BLE001 - settings are optional
        pass

# 表示名とDBの並び順キー。UIは日本語だけを見せる。
SORT_CHOICES = [("発音", "name"), ("音程", "midi"), ("長さ", "length"),
                ("曲名", "song"), ("素材集", "bank"), ("子音", "cons"), ("母音", "vowel"),
                ("前の母音", "prev_vowel"), ("次の子音", "next_cons"), ("★", "favorite")]


class App:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.facts = None
        self.units = []
        self.undo_stack = []
        self.redo_stack = []
        self.pitches = []
        self.lyrics = ""
        self.model = "tifa-1.0-st"
        self.bank_path = ""
        self.selected = -1
        self.edge = "start"
        self.visible = []               # unit indices shown in the list (filter)
        self.peaks = []
        self.peak_grid = []
        self.view = [0.0, 1.0]          # visible time range (seconds)
        self.drag = None
        self.drag_pushed = False        # one undo entry per boundary drag
        self.looping = False
        self.job = queue.Queue()
        self.tmp_play = os.path.join(tempfile.gettempdir(), "jinriki_preview.wav")

        root.title("なま音素 0.5.0 - 切って名前を付ける")
        root.geometry("1200x720")
        icon = appenv.asset_path("NamaOnso.ico")
        if icon:
            try:
                root.iconbitmap(default=icon)
            except tk.TclError:   # a broken/unsupported ICO must not stop startup
                pass

        bar = ttk.Frame(root, padding=6)
        bar.pack(fill="x")
        ttk.Button(bar, text="WAVを開く", command=self.open_wav).pack(side="left")
        ttk.Label(bar, text="  歌詞:").pack(side="left")
        self.lyrics_var = tk.StringVar()
        ttk.Entry(bar, textvariable=self.lyrics_var, width=28).pack(side="left", padx=4)
        self.align_btn = ttk.Button(bar, text="自動で割り当て", command=self.align)
        self.align_btn.pack(side="left", padx=6)
        self.save_btn = ttk.Button(bar, text="素材集を保存", command=self.save)
        self.save_btn.pack(side="left")
        self.batch_btn = ttk.Button(bar, text="フォルダ一括処理", command=self.batch_folder)
        self.batch_btn.pack(side="left", padx=(8, 0))
        ttk.Button(bar, text="カバレッジ", command=self.show_coverage).pack(side="left", padx=(6, 0))
        ttk.Button(bar, text="音素DB", command=self.show_db).pack(side="left", padx=(6, 0))
        ttk.Button(bar, text="音素の選抜", command=self.show_curator).pack(side="left", padx=(6, 0))
        ttk.Button(bar, text="ヘルプ", command=lambda: self.show_wizard(force=True)).pack(side="left", padx=(6, 0))

        self.status = tk.StringVar(value="WAVを開き、歌詞を入力してください。")
        ttk.Label(root, textvariable=self.status, padding=(8, 2), foreground="#37444c").pack(fill="x")

        self.canvas = tk.Canvas(root, background="#f7fafc", height=320, highlightthickness=0)
        self.canvas.pack(fill="x", padx=8)
        self.canvas.bind("<Button-1>", self.on_press)
        self.canvas.bind("<B1-Motion>", self.on_drag)
        self.canvas.bind("<ButtonRelease-1>", self.on_release)
        self.canvas.bind("<Configure>", lambda e: self.redraw())
        self.canvas.bind("<MouseWheel>", self.on_wheel)

        mid = ttk.Frame(root, padding=(8, 6))
        mid.pack(fill="both", expand=True)
        self.listbox = tk.Listbox(mid, width=44, font=("Yu Gothic UI", 11), exportselection=False)
        self.listbox.pack(side="left", fill="y")
        self.listbox.bind("<<ListboxSelect>>", self.on_select)

        side = ttk.Frame(mid, padding=(10, 0))
        side.pack(side="left", fill="y")
        ttk.Button(side, text="▶ 再生", command=self.play).pack(fill="x", pady=2)
        ttk.Button(side, text="■ 停止", command=self.stop).pack(fill="x", pady=2)
        self.loop_btn = ttk.Button(side, text="ループ試聴", command=self.toggle_loop)
        self.loop_btn.pack(fill="x", pady=2)

        ttk.Label(side, text="動かす側").pack(anchor="w", pady=(10, 0))
        self.edge_var = tk.StringVar(value="start")
        ttk.Radiobutton(side, text="開始", value="start", variable=self.edge_var,
                        command=self.refresh_list).pack(anchor="w")
        ttk.Radiobutton(side, text="終了", value="end", variable=self.edge_var,
                        command=self.refresh_list).pack(anchor="w")

        ttk.Label(side, text="境界を動かす (ms)").pack(anchor="w", pady=(10, 0))
        for ms in NUDGES:
            ttk.Button(side, text=f"{ms:+d}", width=8,
                       command=lambda m=ms: self.nudge(m)).pack(anchor="w", pady=1)

        ttk.Button(side, text="名前を変更", command=self.rename).pack(fill="x", pady=(10, 2))
        ttk.Button(side, text="この範囲を確認済みにする", command=self.mark_checked).pack(fill="x", pady=2)
        self.only_pending = tk.BooleanVar(value=False)
        ttk.Checkbutton(side, text="要確認だけ表示", variable=self.only_pending,
                        command=self.refresh_list).pack(anchor="w", pady=(4, 0))
        self.pending_var = tk.StringVar(value="要確認 0 件")
        ttk.Label(side, textvariable=self.pending_var, foreground="#b45309").pack(anchor="w")
        ttk.Button(side, text="全体表示", command=self.show_all).pack(fill="x", pady=(10, 2))
        ttk.Button(side, text="REAPER用プロジェクトを書き出し", command=self.export_rpp).pack(fill="x", pady=2)
        ttk.Label(side, text="←/→: ±5 ms\nShift+←/→: ±50 ms\nAlt+←/→: ±1 ms",
                  foreground="#5b6870", justify="left").pack(anchor="w", pady=(10, 0))

        undo_bar = ttk.Frame(side)
        undo_bar.pack(fill="x", pady=(6, 0))
        ttk.Button(undo_bar, text="元に戻す", width=10, command=self.undo).pack(side="left")
        ttk.Button(undo_bar, text="やり直し", width=10, command=self.redo).pack(side="left", padx=(4, 0))

        root.bind("<Left>", lambda e: self.nudge(-50 if e.state & 0x1 else (-1 if e.state & 0x20000 else -5)))
        root.bind("<Right>", lambda e: self.nudge(50 if e.state & 0x1 else (1 if e.state & 0x20000 else 5)))
        root.bind("<Control-z>", self.undo)
        root.bind("<Control-Z>", self.undo)
        root.bind("<Control-y>", self.redo)
        root.bind("<Control-Y>", self.redo)

        self.root.after(100, self.poll)
        if len(sys.argv) > 1:
            self.load(sys.argv[1])
        self.root.after(400, self.maybe_show_wizard)

    # ------------------------------------------------------------ first run
    def maybe_show_wizard(self):
        if not load_settings().get("wizard_seen"):
            self.show_wizard(force=False)

    def show_wizard(self, force: bool = False):
        win = tk.Toplevel(self.root)
        win.title("はじめての設定")
        win.geometry("660x600")
        win.transient(self.root)

        ttk.Label(win, text="なま音素 を使う準備", font=("", 12, "bold"),
                  padding=(14, 12, 14, 4)).pack(anchor="w")
        ttk.Label(win, text="この画面は「ヘルプ」からいつでも開けます。",
                  foreground="#5b6870", padding=(14, 0, 14, 8)).pack(anchor="w")

        box = ttk.Frame(win, padding=(14, 0))
        box.pack(fill="both", expand=True)
        status_box = ttk.Frame(box)
        status_box.pack(fill="x")

        def refresh_status():
            for child in status_box.winfo_children():
                child.destroy()
            mvt_path = appenv.find_mvt()
            model_path = appenv.find_model()
            rows = [
                ("音素の自動推定 (mvt)", bool(mvt_path),
                 mvt_path or "見つかりません。下のボタンで自動導入できます。\n"
                             "手動なら: uv tool install git+https://github.com/Megageorgio/mVocalToolkit"),
                ("音程の推定モデル (FCPE)", bool(model_path),
                 model_path or "models フォルダに fcpe.onnx を置いてください。\n"
                               "無い場合は YIN（簡易）で推定します。"),
                ("ドラッグ＆ドロップ", HAVE_DND,
                 "使えます" if HAVE_DND else "tkinterdnd2 が無いため無効です（WAV書き出しで代替できます）"),
            ]
            for title, ok, detail in rows:
                line = ttk.Frame(status_box)
                line.pack(fill="x", pady=(6, 0))
                ttk.Label(line, text=("✓ " if ok else "✗ ") + title,
                          foreground="#1a7f37" if ok else "#b42318").pack(anchor="w")
                ttk.Label(line, text=detail, foreground="#37444c", wraplength=560,
                          justify="left").pack(anchor="w", padx=(18, 0))

        refresh_status()

        # One-click mvt setup: uv installs the CLI, then the CLI fetches the model.
        setup = ttk.Frame(box)
        setup.pack(fill="x", pady=(10, 0))
        model_too = tk.BooleanVar(value=True)
        install_btn = ttk.Button(setup, text="mvt を自動で入れる")
        install_btn.pack(side="left")
        ttk.Checkbutton(setup, text="tifa モデルも取得（数百MB）",
                        variable=model_too).pack(side="left", padx=8)
        ttk.Label(box, text="ネット接続が必要です。uv が無い場合は先に導入してください。",
                  foreground="#5b6870").pack(anchor="w", pady=(2, 0))
        log = tk.Text(box, height=6, font=("Consolas", 9), state="disabled")
        log.pack(fill="both", expand=True, pady=(4, 0))

        def log_line(text: str):
            log.configure(state="normal")
            log.insert("end", text + "\n")
            log.see("end")
            log.configure(state="disabled")

        queue_lines = queue.Queue()

        def pump():
            try:
                while True:
                    kind, text = queue_lines.get_nowait()
                    if kind == "line":
                        log_line(text)
                    elif kind == "done":
                        install_btn.state(["!disabled"])
                        log_line(text)
                        refresh_status()
            except queue.Empty:
                pass
            except tk.TclError:   # the window was closed mid-install
                return
            try:
                win.after(120, pump)
            except tk.TclError:
                pass

        def work(fetch_model: bool):
            cmd = appenv.mvt_install_command()
            if not cmd:
                queue_lines.put(("done", appenv.UV_HINT))
                return
            queue_lines.put(("line", "実行: " + " ".join(cmd)))
            try:
                code = appenv.run_command(cmd, lambda s: queue_lines.put(("line", s)))
            except Exception as e:  # noqa: BLE001 - report, never crash the wizard
                queue_lines.put(("done", "mvt の導入に失敗しました: " + str(e)))
                return
            if code != 0:
                queue_lines.put(("done", f"mvt の導入に失敗しました（終了コード {code}）。"))
                return
            mvt_path = appenv.find_mvt()
            if not mvt_path:
                queue_lines.put(("done", "導入は終わりましたが mvt が見つかりません。"
                                         "PowerShell を開き直すか、NAMA_MVT を設定してください。"))
                return
            queue_lines.put(("line", "mvt: " + mvt_path))
            if fetch_model:
                cmd = appenv.mvt_model_command(mvt_path)
                queue_lines.put(("line", "実行: " + " ".join(cmd)))
                try:
                    code = appenv.run_command(cmd, lambda s: queue_lines.put(("line", s)))
                except Exception as e:  # noqa: BLE001
                    queue_lines.put(("done", "モデル取得に失敗しました: " + str(e)))
                    return
                if code != 0:
                    queue_lines.put(("done", f"モデル取得に失敗しました（終了コード {code}）。"
                                             "mvt 自体は使えます。"))
                    return
            queue_lines.put(("done", "準備ができました。"))

        def start_install():
            if install_btn.instate(["disabled"]):
                return
            install_btn.state(["disabled"])
            log_line("--- mvt の自動導入を開始します ---")
            threading.Thread(target=work, args=(bool(model_too.get()),), daemon=True).start()
            pump()

        install_btn.configure(command=start_install)

        ttk.Label(box, text="元のWAVは変更しません。素材集JSONと索引だけを書き出します。",
                  foreground="#37444c", padding=(0, 14, 0, 0)).pack(anchor="w")

        keep = tk.BooleanVar(value=True)
        ttk.Checkbutton(win, text="次回から表示しない", variable=keep).pack(anchor="w", padx=14, pady=(8, 0))
        buttons = ttk.Frame(win, padding=14)
        buttons.pack(fill="x")

        def close():
            data = load_settings()
            data["wizard_seen"] = bool(keep.get())
            save_settings(data)
            win.destroy()

        ttk.Button(buttons, text="閉じる", command=close).pack(side="right")
        ttk.Button(buttons, text="WAVを開く", command=lambda: (close(), self.open_wav())).pack(side="right", padx=6)
        if force:
            keep.set(True)

    # ------------------------------------------------------------ file/algo
    def open_wav(self):
        path = filedialog.askopenfilename(filetypes=[("WAV", "*.wav"), ("すべて", "*.*")])
        if path:
            self.load(path)

    def load(self, path):
        try:
            self.facts = core.read_wav(path)
        except Exception as e:
            messagebox.showerror("読み込み失敗", str(e))
            return
        self.units = []
        self.undo_stack.clear()
        self.redo_stack.clear()
        self.drag = None
        self.drag_pushed = False
        self.selected = -1
        self.bank_path = core.default_bank_path(path)
        self.view = [0.0, self.facts.seconds]
        self.peak_bucket = core.peak_bucket_seconds(self.facts)
        self.peak_grid = core.build_peak_grid(self.facts, self.peak_bucket)
        self.status.set(f"{os.path.basename(path)} : {self.facts.seconds:.2f} 秒 / "
                        f"{self.facts.sample_rate} Hz / {self.facts.channels} ch")
        self.redraw()
        self.refresh_list()

    def align(self):
        if not self.facts:
            messagebox.showinfo("先にWAV", "先にWAVを開いてください。")
            return
        lyrics = self.lyrics_var.get().strip()
        if not lyrics:
            messagebox.showinfo("歌詞が必要", "歌詞を入力してください。ファイル名でも構いません。")
            return
        self.align_btn.state(["disabled"])
        self.status.set("音素を推定中...（初回は環境準備で時間がかかります）")
        thread = threading.Thread(target=self._align_worker, args=(self.facts.path, lyrics), daemon=True)
        thread.start()

    def _align_worker(self, path, lyrics):
        try:
            segs = core.run_align(path, lyrics, self.model)
            self.job.put(("segs", segs, lyrics, None))
        except Exception as e:  # noqa: BLE001
            self.job.put(("segs", None, lyrics, str(e)))

    def poll(self):
        try:
            msg = self.job.get_nowait()
        except queue.Empty:
            self.root.after(100, self.poll)
            return
        if msg[0] == "batch":
            self.batch_btn.state(["!disabled"])
            self.status.set(msg[1])
            messagebox.showinfo("一括処理", msg[1], parent=self.root)
            self.root.after(100, self.poll)
            return
        kind, segs, lyrics, error = msg
        self.align_btn.state(["!disabled"])
        if error:
            self.status.set("推定に失敗しました。")
            messagebox.showerror("推定失敗", error)
        else:
            units = core.group_units(segs)
            units, warn = core.name_units(units, lyrics)
            units, note = core.trim_silence(units, self.facts)
            self.units = units
            self.undo_stack.clear()
            self.redo_stack.clear()
            self.pitches = [core.unit_pitch(self.facts, u) for u in units]
            self.lyrics = lyrics
            self.selected = 0 if units else -1
            self.view = [0.0, self.facts.seconds]
            msg = f"{len(units)}個の素材候補を作りました。"
            if warn:
                msg += " " + warn
            if note:
                msg += f" 無音の補正: {note}。"
            msg += " 境界を確認して保存してください。"
            self.status.set(msg)
            self.redraw()
            self.refresh_list()
        self.root.after(100, self.poll)

    # --------------------------------------------------------- batch/coverage
    def batch_folder(self):
        folder = filedialog.askdirectory(title="WAVが入ったフォルダ（歌詞はファイル名か同名の.txt）")
        if not folder:
            return
        if not messagebox.askyesno("一括処理",
                                   "このフォルダのWAVを順に解析し、同名の素材集JSONを作ります。\n"
                                   "既存のJSONは上書きしません。元のWAVは変更しません。",
                                   parent=self.root):
            return
        import batch
        self.batch_btn.state(["disabled"])
        self.status.set("一括処理中…（画面は閉じないでください）")

        def work():
            try:
                results = batch.run_folder(folder, force=False, lyrics_mode="auto")
                ok = sum(1 for r in results if r["status"] == "OK")
                skipped = sum(1 for r in results if r["status"] == "既存のためスキップ")
                failed = [r for r in results if r["status"] == "失敗"]
                text = f"{ok}件を書き出しました（既存 {skipped}件、失敗 {len(failed)}件）。"
                if failed:
                    text += "\n失敗: " + " / ".join(f"{r['file']}: {r['note']}" for r in failed[:5])
                if results:
                    text += "\nレポート: " + batch.write_report(folder, results)
            except Exception as e:  # noqa: BLE001
                text = "一括処理に失敗しました: " + str(e)
            self.job.put(("batch", text))

        threading.Thread(target=work, daemon=True).start()

    def show_db(self):
        DBWindow(self)

    def show_curator(self):
        import curate_gui
        curate_gui.CuratorWindow(self)

    def show_coverage(self):
        default = os.path.dirname(self.bank_path) if self.bank_path else os.getcwd()
        folder = filedialog.askdirectory(title="素材集JSONが入ったフォルダ", initialdir=default)
        if not folder:
            return
        try:
            import coverage
            data = coverage.report(folder, self.lyrics_var.get().strip() or None)
        except Exception as e:  # noqa: BLE001
            messagebox.showerror("カバレッジ", str(e), parent=self.root)
            return
        win = tk.Toplevel(self.root)
        win.title("カバレッジ")
        win.geometry("560x520")
        text = tk.Text(win, font=("Consolas", 10), wrap="none")
        scroll = ttk.Scrollbar(win, command=text.yview)
        text.configure(yscrollcommand=scroll.set)
        text.pack(side="left", fill="both", expand=True)
        scroll.pack(side="right", fill="y")
        import io
        import contextlib
        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer):
            coverage.print_report(data)
        text.insert("1.0", buffer.getvalue())
        text.configure(state="disabled")

    # -------------------------------------------------------------- editing
    def unit(self):
        if 0 <= self.selected < len(self.units):
            return self.units[self.selected]
        return None

    def push_undo(self):
        """Snapshot the unit list so Ctrl+Z can restore the previous shape."""
        import copy
        self.undo_stack.append(copy.deepcopy(self.units))
        del self.undo_stack[:-50]
        self.redo_stack.clear()

    def typing_focus(self) -> bool:
        """True when the keyboard belongs to a text box (its own Ctrl+Z must win)."""
        try:
            widget = self.root.focus_get()
        except Exception:  # noqa: BLE001 - focus can vanish between events
            return False
        return widget is not None and widget.winfo_class() in (
            "Entry", "TEntry", "Text", "Spinbox", "TSpinbox")

    def undo(self, _event=None):
        if self.typing_focus():
            return None
        if not self.undo_stack:
            self.status.set("戻せる操作がありません。")
            return "break"
        import copy
        self.redo_stack.append(copy.deepcopy(self.units))
        del self.redo_stack[:-50]
        self.units = self.undo_stack.pop()
        if not (0 <= self.selected < len(self.units)):
            self.selected = -1
        self.redraw()
        self.refresh_list()
        self.status.set("1つ前に戻しました（Ctrl+Y でやり直し）。")
        return "break"

    def redo(self, _event=None):
        if self.typing_focus():
            return None
        if not self.redo_stack:
            self.status.set("やり直せる操作がありません。")
            return "break"
        import copy
        self.undo_stack.append(copy.deepcopy(self.units))
        del self.undo_stack[:-50]
        self.units = self.redo_stack.pop()
        if not (0 <= self.selected < len(self.units)):
            self.selected = -1
        self.redraw()
        self.refresh_list()
        self.status.set("やり直しました。")
        return "break"

    def nudge(self, ms):
        u = self.unit()
        if not u or not self.facts:
            return
        dt = ms / 1000.0
        lo, hi = 0.0, self.facts.seconds
        if self.edge_var.get() == "start":
            new = max(lo, min(u.start + dt, u.end - 0.005))
            if abs(new - u.start) < 1e-9:
                return
            self.push_undo()
            u.start = new
        else:
            new = min(hi, max(u.end + dt, u.start + 0.005))
            if abs(new - u.end) < 1e-9:
                return
            self.push_undo()
            u.end = new
        u.confidence = "checked"
        self.redraw()
        self.refresh_list()

    def rename(self):
        u = self.unit()
        if not u:
            return
        new = simpledialog.askstring("名前", "素材名（例: か）", initialvalue=u.name, parent=self.root)
        if new and new.strip() != u.name:
            self.push_undo()
            u.name = new.strip()
            u.confidence = "checked"
            self.refresh_list()
            self.redraw()

    def mark_checked(self):
        u = self.unit()
        if u and u.confidence != "checked":
            self.push_undo()
            u.confidence = "checked"
            self.refresh_list()
            self.redraw()

    def show_all(self):
        if self.facts:
            self.view = [0.0, self.facts.seconds]
            self.redraw()

    def export_rpp(self):
        if not self.units or not self.facts:
            messagebox.showinfo("先に割り当て", "先に音素を割り当ててください。")
            return
        path = os.path.splitext(self.facts.path)[0] + "_音素ラベル.rpp"
        try:
            core.write_reaper_project(path, self.facts.path, self.facts, self.units)
        except Exception as e:
            messagebox.showerror("書き出し失敗", str(e))
            return
        self.status.set(f"REAPER用プロジェクトを書き出しました: {path}（候補はリージョン、音素の開始はマーカー）")

    def on_select(self, _event=None):
        sel = self.listbox.curselection()
        if sel:
            row = int(sel[0])
            if 0 <= row < len(self.visible):
                self.selected = self.visible[row]
                self.redraw()

    def on_press(self, event):
        if not self.units:
            return
        t = self.time_at(event.x)
        # Hit tolerance in PIXELS, not seconds: at high zoom a 40 ms window is
        # hundreds of pixels wide and grabs clicks meant to select a unit.
        width = max(1, self.canvas.winfo_width())
        a, b = self.view
        tolerance = (b - a) * 5.0 / width
        best, dist, edge = -1, 1e9, "start"
        for i, u in enumerate(self.units):
            for e in ("start", "end"):
                d = abs(getattr(u, e) - t)
                if d < dist:
                    best, dist, edge = i, d, e
        if best >= 0 and dist <= tolerance:
            self.selected = best
            self.edge = edge
            self.edge_var.set(edge)
            self.drag = best
            self.drag_pushed = False
        else:
            self.selected = self.unit_at(t)
            self.drag = None
            self.drag_pushed = False
        self.refresh_list()
        self.redraw()

    def on_drag(self, event):
        if self.drag is None:
            return
        u = self.units[self.drag]
        t = self.time_at(event.x)
        if not self.drag_pushed:
            # Snapshot once per drag, before the first change, so a click that
            # never moves does not leave an empty undo step behind.
            self.push_undo()
            self.drag_pushed = True
        if self.edge == "start":
            u.start = max(0.0, min(t, u.end - 0.005))
        else:
            u.end = min(self.facts.seconds, max(t, u.start + 0.005))
        u.confidence = "checked"
        self.redraw()
        self.refresh_list()

    def on_release(self, _event):
        self.drag = None
        self.drag_pushed = False

    def unit_at(self, t):
        for i, u in enumerate(self.units):
            if u.start <= t < u.end:
                return i
        return -1

    def time_at(self, x):
        w = max(1, self.canvas.winfo_width())
        a, b = self.view
        return max(0.0, min(1.0, x / w)) * (b - a) + a

    def x_at(self, t):
        w = max(1, self.canvas.winfo_width())
        a, b = self.view
        return (t - a) / max(1e-9, (b - a)) * w

    def on_wheel(self, event):
        if not self.facts:
            return
        a, b = self.view
        span = b - a
        if event.state & 0x20000:        # Alt + wheel = zoom around the cursor
            t = self.time_at(event.x)
            factor = 0.8 if event.delta > 0 else 1.25
            new_span = max(0.02, min(self.facts.seconds, span * factor))
            ratio = (t - a) / max(1e-9, span)
            a = t - new_span * ratio
            b = a + new_span
            if a < 0.0:
                b -= a
                a = 0.0
            if b > self.facts.seconds:
                a -= b - self.facts.seconds
                b = self.facts.seconds
            self.view = [max(0.0, a), min(self.facts.seconds, b)]
        else:                            # wheel = scroll
            step = span * 0.15 * (1 if event.delta < 0 else -1)
            a += step
            b += step
            if a < 0.0:
                b -= a
                a = 0.0
            if b > self.facts.seconds:
                a -= b - self.facts.seconds
                b = self.facts.seconds
            self.view = [max(0.0, a), min(self.facts.seconds, b)]
        self.redraw()

    # ------------------------------------------------------------- playback
    def write_preview(self, u, path):
        with wave.open(self.facts.path, "rb") as src:
            sr, ch, width = src.getframerate(), src.getnchannels(), src.getsampwidth()
            start = max(0, int(u.start * sr))
            frames = max(1, int((u.end - u.start) * sr))
            src.setpos(min(start, max(0, src.getnframes() - 1)))
            raw = src.readframes(frames)
        # Always write 16-bit PCM: some systems refuse 24/32-bit WAV in winsound.
        out = bytearray()
        for v in core._iter_frames(raw, width):
            s = int(max(-1.0, min(1.0, v)) * 32767)
            out += struct.pack("<h", s)
        with wave.open(path, "wb") as dst:
            dst.setnchannels(ch)
            dst.setsampwidth(2)
            dst.setframerate(sr)
            dst.writeframes(bytes(out))

    def play(self):
        u = self.unit()
        if not u or not winsound:
            return
        self.write_preview(u, self.tmp_play)
        winsound.PlaySound(self.tmp_play, winsound.SND_FILENAME | winsound.SND_ASYNC)

    def toggle_loop(self):
        if not winsound:
            return
        self.looping = not self.looping
        if self.looping:
            if self.play_loop():
                self.loop_btn.configure(text="ループ停止")
            else:
                self.looping = False
                self.loop_btn.configure(text="ループ試聴")
        else:
            self.stop()

    def play_loop(self):
        u = self.unit()
        if not u:
            self.looping = False
            return False
        self.write_preview(u, self.tmp_play)
        winsound.PlaySound(self.tmp_play, winsound.SND_FILENAME | winsound.SND_ASYNC | winsound.SND_LOOP)
        return True

    def stop(self):
        if winsound:
            winsound.PlaySound(None, winsound.SND_PURGE)
        self.looping = False
        self.loop_btn.configure(text="ループ試聴")

    # --------------------------------------------------------------- output
    def save(self):
        if not self.units or not self.facts:
            messagebox.showinfo("保存するものがありません", "先に音素を割り当ててください。")
            return
        bank = core.make_voicebank(self.facts, self.units, self.lyrics, self.model, self.pitches)
        try:
            core.save_voicebank(self.bank_path, bank)
        except Exception as e:
            messagebox.showerror("保存失敗", str(e))
            return
        pending = sum(1 for u in self.units if u.confidence != "checked")
        self.status.set(f"保存しました: {self.bank_path}（未確認 {pending} 件）")

    # ----------------------------------------------------------------- view
    def refresh_list(self):
        self.listbox.delete(0, "end")
        pending = sum(1 for u in self.units if u.confidence != "checked")
        self.pending_var.set(f"要確認 {pending} 件 / 全 {len(self.units)} 件")
        only = self.only_pending.get()
        self.visible = [i for i, u in enumerate(self.units)
                        if not only or u.confidence != "checked"]
        for i in self.visible:
            u = self.units[i]
            mark = "✓" if u.confidence == "checked" else "!"
            p = self.pitches[i] if i < len(self.pitches) else {}
            hz = p.get("hz")
            pitch = f"{hz:6.1f}Hz" if hz else "   --- "
            self.listbox.insert("end", f"{mark} {i + 1:2d}  {u.name:<6} {u.start:6.3f}-{u.end:6.3f}"
                                       f"  {u.duration * 1000:5.0f}ms  {pitch}")
            if u.confidence != "checked":
                self.listbox.itemconfig("end", foreground="#b45309")
        if self.selected in self.visible:
            row = self.visible.index(self.selected)
            self.listbox.selection_set(row)
            self.listbox.see(row)

    def redraw(self):
        c = self.canvas
        c.delete("all")
        w, h = max(1, c.winfo_width()), max(1, c.winfo_height())
        if not self.facts:
            c.create_text(w // 2, h // 2, text="WAVを開いてください", fill="#5b6870")
            return
        a, b = self.view
        cols = max(1, w)
        peaks = self.view_peaks(a, b, cols)
        self.peaks = peaks

        pad_top, pad_bottom = 26, 22
        mid = (pad_top + (h - pad_bottom)) / 2.0
        amp = (h - pad_bottom - pad_top) / 2.0 * 0.92
        c.create_rectangle(0, 0, w, h, fill="#f7fafc", outline="")

        # time grid
        step = self.nice_step(b - a)
        t = math.floor(a / step) * step
        while t <= b + 1e-9:
            x = self.x_at(t)
            if 0 <= x <= w:
                c.create_line(x, pad_top, x, h - pad_bottom, fill="#e3ecf3")
                c.create_text(x + 2, h - 10, text=self.fmt_time(t), fill="#8b9aa6", anchor="w")
            t += step
        c.create_line(0, mid, w, mid, fill="#d5e3ee")

        if peaks:
            pts_top = []
            for x, (lo, hi) in enumerate(peaks):
                pts_top.extend((x, mid - hi * amp))
            for x in range(len(peaks) - 1, -1, -1):
                pts_top.extend((x, mid - peaks[x][0] * amp))
            if len(pts_top) >= 6:
                c.create_polygon(pts_top, fill="#8fbfdd", outline="#2f6f9f")

        for i, u in enumerate(self.units):
            sx, ex = self.x_at(u.start), self.x_at(u.end)
            if ex < -20 or sx > w + 20:
                continue
            checked = u.confidence == "checked"
            line = "#2f8f4e" if checked else "#d97706"
            c.create_rectangle(sx, pad_top, ex, pad_top + 3, fill=line, outline="")
            if i == self.selected:
                c.create_rectangle(sx, pad_top, ex, h - pad_bottom, outline="#e8791f", width=2)
            c.create_line(sx, pad_top, sx, h - pad_bottom, fill=line, width=2)
            c.create_line(ex, pad_top, ex, h - pad_bottom,
                          fill="#7fbf95" if checked else "#f0b45a")
            c.create_text((sx + ex) / 2, pad_top / 2 + 4, text=u.name,
                          fill="#26404f" if checked else "#8a4b00")
            for k, ph in enumerate(u.phonemes):
                if k == 0:
                    continue
                t = u.phoneme_starts[k] if k < len(u.phoneme_starts) else u.start
                px = self.x_at(t)
                if 0 <= px <= w:
                    c.create_line(px, h - pad_bottom - 8, px, h - pad_bottom, fill="#7f9aab")
                    c.create_text(px + 2, h - pad_bottom - 10, text=ph, fill="#7f9aab", anchor="s")
        c.create_text(w - 6, h - 10, text="Alt+ホイール:拡大   ホイール:移動   橙=要確認 / 緑=確認済み",
                      fill="#8b9aa6", anchor="e")
        if not self.units:
            c.create_text(w // 2, pad_top + 14, text="歌詞を入力して「自動で割り当て」", fill="#9fb0bd")

    @staticmethod
    def nice_step(span):
        target = span / 6.0
        for s in (0.005, 0.01, 0.02, 0.05, 0.1, 0.2, 0.5, 1.0, 2.0, 5.0, 10.0, 30.0, 60.0):
            if target <= s:
                return s
        return 120.0

    @staticmethod
    def fmt_time(t):
        if t >= 60:
            return f"{int(t // 60)}:{t % 60:05.2f}"
        return f"{t:.2f}"

    def view_peaks(self, a, b, cols):
        """Aggregate the 1 ms peak grid into one min/max pair per pixel column."""
        grid = self.peak_grid
        if not grid:
            return []
        n = len(grid)
        out = []
        span = max(1e-6, b - a)
        step = span / cols
        for c in range(cols):
            ta = a + step * c
            tb = ta + step
            bucket = getattr(self, 'peak_bucket', 0.001)
            i0 = max(0, int(ta / bucket))
            i1 = min(n, max(i0 + 1, int(tb / bucket) + 1))
            if i0 >= n:
                out.append((0.0, 0.0))
                continue
            lo = min(g[0] for g in grid[i0:i1])
            hi = max(g[1] for g in grid[i0:i1])
            out.append((lo, hi))
        return out


class DBWindow:
    """Phoneme database: index the voicebanks, search, audition, export, import."""

    def __init__(self, app: "App"):
        import db as dbmod
        self.dbmod = dbmod
        self.app = app
        self.conn = None
        self.rows = []
        self.undo_stack = []
        self.redo_stack = []

        self.win = tk.Toplevel(app.root)
        self.win.title("音素DB")
        self.win.geometry("1220x640")
        self.win.bind("<Control-z>", self.undo)
        self.win.bind("<Control-Z>", self.undo)
        self.win.bind("<Control-y>", self.redo)
        self.win.bind("<Control-Y>", self.redo)

        bar = ttk.Frame(self.win, padding=6)
        bar.pack(fill="x")
        ttk.Label(bar, text="素材集フォルダ:").pack(side="left")
        default = os.path.dirname(app.bank_path) if app.bank_path else os.getcwd()
        self.folder = tk.StringVar(value=default)
        ttk.Entry(bar, textvariable=self.folder, width=52).pack(side="left", padx=4)
        ttk.Button(bar, text="参照", command=self.pick_folder).pack(side="left")
        ttk.Button(bar, text="索引を作る", command=self.build).pack(side="left", padx=(8, 0))

        bar2 = ttk.Frame(self.win, padding=(6, 0))
        bar2.pack(fill="x")
        self.fields = {}
        for key, label, width in (("name", "発音", 7), ("cons", "子音", 7), ("vowel", "母音", 5),
                                  ("prev_vowel", "前の母音", 7), ("next_cons", "次の子音", 7)):
            ttk.Label(bar2, text=label + ":").pack(side="left")
            var = tk.StringVar()
            entry = ttk.Entry(bar2, textvariable=var, width=width)
            entry.pack(side="left", padx=(2, 8))
            entry.bind("<Return>", lambda e: self.refresh())
            self.fields[key] = var
        self.exact = tk.BooleanVar(value=False)
        ttk.Checkbutton(bar2, text="完全一致", variable=self.exact,
                        command=self.refresh).pack(side="left")
        self.fav_only = tk.BooleanVar(value=False)
        ttk.Checkbutton(bar2, text="★のみ", variable=self.fav_only,
                        command=self.refresh).pack(side="left", padx=(8, 0))
        self.show_hidden = tk.BooleanVar(value=False)
        ttk.Checkbutton(bar2, text="非表示も表示", variable=self.show_hidden,
                        command=self.refresh).pack(side="left", padx=(6, 0))

        bar3 = ttk.Frame(self.win, padding=(6, 4))
        bar3.pack(fill="x")
        ttk.Label(bar3, text="曲名:").pack(side="left")
        self.song = tk.StringVar()
        song_entry = ttk.Entry(bar3, textvariable=self.song, width=16)
        song_entry.pack(side="left", padx=4)
        song_entry.bind("<Return>", lambda e: self.refresh())
        ttk.Label(bar3, text="フリー検索:").pack(side="left")
        self.text = tk.StringVar()
        entry = ttk.Entry(bar3, textvariable=self.text, width=34)
        entry.pack(side="left", padx=4)
        entry.bind("<Return>", lambda e: self.refresh())
        ttk.Label(bar3, text="  並び:").pack(side="left")
        self.sort_label = tk.StringVar(value="発音")
        self.sort_key = "name"
        self.descending = False
        ttk.Combobox(bar3, textvariable=self.sort_label, width=10, state="readonly",
                     values=[label for label, _ in SORT_CHOICES]).pack(side="left", padx=4)
        self.sort_label.trace_add("write", lambda *a: self.sort_from_label())
        ttk.Button(bar3, text="検索", command=self.refresh).pack(side="left", padx=(6, 0))

        bar4 = ttk.Frame(self.win, padding=(6, 0, 6, 4))
        bar4.pack(fill="x")
        ttk.Label(bar4, text="書き出し:").pack(side="left")
        ttk.Button(bar4, text="WAV", command=self.export_wavs).pack(side="left", padx=(4, 0))
        ttk.Button(bar4, text="選択をWAV", command=self.export_selected).pack(side="left", padx=6)
        ttk.Button(bar4, text="CSV", command=self.export_csv).pack(side="left", padx=(0, 6))
        ttk.Button(bar4, text="UTAU音源", command=self.export_utau).pack(side="left", padx=(0, 6))
        ttk.Button(bar4, text="library.json", command=self.export_library).pack(side="left", padx=(0, 6))
        ttk.Button(bar4, text="音素DBから取り込み", command=self.import_fonemudb).pack(side="left", padx=(0, 6))
        ttk.Label(bar4, text="書き出すWAV: 44.1kHz/24bit/モノラル",
                  foreground="#5b6870").pack(side="left", padx=(6, 0))

        table = ttk.Frame(self.win)
        table.pack(fill="both", expand=True, padx=6)
        table.rowconfigure(0, weight=1)
        table.columnconfigure(0, weight=1)
        cols = ("mark", "name", "cons", "vowel", "prev", "next", "midi", "ms", "song", "bank")
        self.tree = ttk.Treeview(table, columns=cols, show="headings", height=17)
        for key, label, width in (("mark", "", 34), ("name", "発音", 70), ("cons", "子音", 80),
                                  ("vowel", "母音", 60), ("prev", "前の母音", 80), ("next", "次の子音", 80),
                                  ("midi", "音程", 70), ("ms", "長さms", 70), ("song", "曲名", 200),
                                  ("bank", "素材集", 240)):
            self.tree.heading(key, text=label, command=lambda k=key: self.sort_by(k))
            self.tree.column(key, width=width, anchor="w")
        yscroll = ttk.Scrollbar(table, orient="vertical", command=self.tree.yview)
        xscroll = ttk.Scrollbar(table, orient="horizontal", command=self.tree.xview)
        self.tree.configure(yscrollcommand=yscroll.set, xscrollcommand=xscroll.set)
        self.tree.grid(row=0, column=0, sticky="nsew")
        yscroll.grid(row=0, column=1, sticky="ns")
        xscroll.grid(row=1, column=0, sticky="ew")
        self.tree.bind("<Double-1>", lambda e: self.play())
        self.drag_dir = os.path.join(tempfile.gettempdir(), "nama_phoneme_drag")
        if HAVE_DND:
            self.tree.drag_source_register(1, DND_FILES)
            self.tree.dnd_bind("<<DragInitCmd>>", self.on_drag_init)
            self.tree.dnd_bind("<<DragEndCmd>>", self.on_drag_end)

        bottom = ttk.Frame(self.win, padding=6)
        bottom.pack(fill="x")
        ttk.Button(bottom, text="▶ 試聴", command=self.play).pack(side="left")
        ttk.Button(bottom, text="名前を変更", command=self.rename).pack(side="left", padx=6)
        ttk.Button(bottom, text="★切替", command=self.toggle_favorite).pack(side="left")
        ttk.Button(bottom, text="非表示/戻す", command=self.toggle_hidden).pack(side="left", padx=6)
        ttk.Button(bottom, text="重複を整理", command=self.clean_duplicates).pack(side="left")
        ttk.Button(bottom, text="元に戻す", width=9, command=self.undo).pack(side="left", padx=(6, 0))
        ttk.Button(bottom, text="やり直し", width=9, command=self.redo).pack(side="left", padx=(4, 0))
        ttk.Button(bottom, text="元のフレーズを開く", command=self.open_source).pack(side="left", padx=6)
        self.status = tk.StringVar(value="フォルダを選び「索引を作る」を押してください。")
        ttk.Label(bottom, textvariable=self.status, foreground="#37444c").pack(side="left", padx=10)
        hint = ("行を選んで、そのまま他のアプリ（REAPERなど）へドラッグできます。"
                "WAVは 44.1kHz / 24bit / モノラル で渡ります。"
                if HAVE_DND else
                "ドラッグ＆ドロップには tkinterdnd2 が必要です（pip install tkinterdnd2）。"
                "代わりに「選択をWAVで書き出す」が使えます（44.1kHz / 24bit / モノラル）。")
        ttk.Label(self.win, text=hint, foreground="#5b6870", padding=(8, 0, 8, 6)).pack(fill="x")

        if os.path.isfile(self.db_path()):
            self.open_db()

    # ------------------------------------------------------------------ db
    def db_path(self) -> str:
        return os.path.join(self.folder.get(), self.dbmod.DB_DEFAULT)

    def open_db(self):
        try:
            if self.conn:
                self.conn.close()
            self.conn = self.dbmod.connect(self.db_path())
        except Exception as e:  # noqa: BLE001
            self.status.set("索引を開けません: " + str(e))
            self.conn = None
            return
        self.undo_stack.clear()
        self.redo_stack.clear()
        self.refresh()

    def pick_folder(self):
        folder = filedialog.askdirectory(title="素材集JSONのフォルダ", parent=self.win)
        if folder:
            self.folder.set(folder)
            self.open_db()

    def build(self):
        if not self.folder.get():
            return
        try:
            if self.conn:
                self.conn.close()
            self.conn = self.dbmod.connect(self.db_path())
            info = self.dbmod.index_folder(self.conn, self.folder.get())
            self.undo_stack.clear()
            self.redo_stack.clear()
            self.status.set(f"索引を更新しました（追加 {info['banks']} / スキップ {info['skipped']} / "
                            f"削除 {info['removed']}、候補 {info['units']}）")
            self.refresh()
        except Exception as e:  # noqa: BLE001
            self.status.set("索引の作成に失敗: " + str(e))

    def refresh(self):
        if not self.conn:
            return
        try:
            hidden = True if self.show_hidden.get() else None
            self.rows = self.dbmod.search(
                self.conn, self.fields["name"].get().strip(), self.exact.get(),
                sort=self.sort_key, descending=self.descending, cons=self.fields["cons"].get().strip(),
                vowel=self.fields["vowel"].get().strip(),
                prev_vowel=self.fields["prev_vowel"].get().strip(),
                next_cons=self.fields["next_cons"].get().strip(),
                text=self.text.get().strip(), favorite=self.fav_only.get(),
                hidden=hidden, song=self.song.get().strip())
        except Exception as e:  # noqa: BLE001
            self.status.set("検索に失敗: " + str(e))
            return
        self.tree.delete(*self.tree.get_children())
        for row in self.rows:
            midi = self.dbmod.midi_label(row["midi"])
            mark = ("★" if row["favorite"] else "") + ("×" if row["hidden"] else "")
            self.tree.insert("", "end", values=(
                mark, row["display"], row["cons"] or "-", row["vowel"] or "-",
                row["prev_vowel"] or "-", row["next_cons"] or "-", midi,
                f"{(row['end'] - row['start']) * 1000:.0f}", row.get("song") or "-",
                os.path.basename(row["bank_path"])))
        self.status.set(f"{len(self.rows)} 件 ・ 並び: {self.sort_label.get()} {'▼' if self.descending else '▲'}")

    def sort_from_label(self):
        for label, key in SORT_CHOICES:
            if label == self.sort_label.get():
                if key != self.sort_key:
                    self.sort_key = key
                    self.descending = False
                break
        self.refresh()

    def sort_by(self, key: str):
        mapping = {"mark": "favorite", "name": "name", "cons": "cons", "vowel": "vowel",
                   "prev": "prev_vowel", "next": "next_cons", "midi": "midi",
                   "ms": "length", "song": "song", "bank": "bank"}
        wanted = mapping.get(key, "name")
        if wanted == self.sort_key:
            self.descending = not self.descending
        else:
            self.sort_key = wanted
            self.descending = False
        for label, value in SORT_CHOICES:
            if value == wanted:
                self.sort_label.set(label)   # triggers sort_from_label (same key: refresh)
                break
        self.refresh()

    # -------------------------------------------------------------- actions
    def selected(self):
        items = self.tree.selection()
        if not items:
            return None
        index = self.tree.index(items[0])
        return self.rows[index] if 0 <= index < len(self.rows) else None

    def selected_rows(self):
        out = []
        for item in self.tree.selection():
            index = self.tree.index(item)
            if 0 <= index < len(self.rows):
                out.append(self.rows[index])
        if not out:
            row = self.selected()
            if row:
                out.append(row)
        return out

    # ----------------------------------------------------------------- undo
    def text_focus(self) -> bool:
        try:
            widget = self.win.focus_get()
        except Exception:  # noqa: BLE001 - focus can vanish between events
            return False
        return widget is not None and widget.winfo_class() in (
            "Entry", "TEntry", "Text", "Spinbox", "TSpinbox")

    def push_undo(self, label: str, aliases=None, flags=None):
        """Record both sides of a change; nothing is written until the action runs."""
        self.undo_stack.append({"label": label,
                                "aliases": aliases or [],
                                "flags": flags or []})
        del self.undo_stack[:-50]
        self.redo_stack.clear()

    def apply_entry(self, entry: dict, forward: bool):
        for bank, unit, old, new in entry["aliases"]:
            self.dbmod.set_alias(self.conn, bank, unit, new if forward else old)
        if entry["flags"]:
            rows = [(bank, unit) + (new if forward else old)
                    for bank, unit, old, new in entry["flags"]]
            self.dbmod.restore_flags(self.conn, rows)

    def undo(self, _event=None):
        if self.text_focus():
            return None
        if not self.conn or not self.undo_stack:
            self.status.set("戻せる操作がありません。")
            return "break"
        entry = self.undo_stack.pop()
        self.apply_entry(entry, forward=False)
        self.redo_stack.append(entry)
        del self.redo_stack[:-50]
        self.refresh()
        self.status.set(f"「{entry['label']}」を元に戻しました（Ctrl+Y でやり直し）。")
        return "break"

    def redo(self, _event=None):
        if self.text_focus():
            return None
        if not self.conn or not self.redo_stack:
            self.status.set("やり直せる操作がありません。")
            return "break"
        entry = self.redo_stack.pop()
        self.apply_entry(entry, forward=True)
        self.undo_stack.append(entry)
        del self.undo_stack[:-50]
        self.refresh()
        self.status.set(f"「{entry['label']}」をやり直しました。")
        return "break"

    # Drag out a real WAV per selected row, so any app can accept the drop.
    # tkdnd expects (action, formats, data); each element may be a string or a tuple.
    def on_drag_init(self, event):
        import hashlib
        paths = []
        for row in self.selected_rows():
            # One folder per candidate keeps the visible name exactly "だ.wav"
            # without two different samples overwriting each other.
            tag = hashlib.sha1((row["bank_path"] + "|" + row["unit_id"]).encode("utf-8")).hexdigest()[:10]
            path = self.dbmod.write_unit_wav(row, os.path.join(self.drag_dir, tag))
            if path:
                paths.append(path)
        if not paths:
            self.status.set("ドラッグできる候補が選ばれていません。")
            return (DND_COPY, (DND_FILES,), "")
        self.status.set(f"{len(paths)} 件をドラッグ中（元の音声は変更していません）。")
        return (DND_COPY, (DND_FILES,), tuple(paths))

    def on_drag_end(self, event):
        self.status.set("ドラッグを終了しました。")

    def rename(self):
        rows = self.selected_rows()
        if not rows:
            self.status.set("先に候補を選んでください。")
            return
        new = simpledialog.askstring(
            "発音の名前",
            "表示名（表とファイル名に使います。元の素材集は変更しません）",
            initialvalue=rows[0]["display"], parent=self.win)
        if not new or not new.strip():
            return
        name = new.strip()
        aliases = [(row["bank_path"], row["unit_id"],
                    self.dbmod.get_alias(self.conn, row["bank_path"], row["unit_id"]), name)
                   for row in rows]
        self.push_undo("名前を変更", aliases=aliases)
        for row in rows:
            self.dbmod.set_alias(self.conn, row["bank_path"], row["unit_id"], name)
        self.refresh()
        self.status.set(f"{len(rows)} 件を「{name}」にしました（ドラッグすると {name}.wav）。")

    def export_selected(self):
        rows = self.selected_rows()
        if not rows:
            self.status.set("先に候補を選んでください。")
            return
        out = filedialog.askdirectory(title="選択した候補の書き出し先", parent=self.win)
        if not out:
            return
        count = 0
        for row in rows:
            if self.dbmod.write_unit_wav(row, out, unique=True):
                count += 1
        self.status.set(f"{count} 件を書き出しました（44.1kHz/24bit/モノラル）。")

    def toggle_favorite(self):
        rows = self.selected_rows()
        if not rows:
            self.status.set("先に候補を選んでください。")
            return
        new = not all(row["favorite"] for row in rows)
        flags = [(row["bank_path"], row["unit_id"],
                  (int(row["favorite"]), int(row["hidden"]), row.get("tags") or ""),
                  (int(new), int(row["hidden"]), row.get("tags") or ""))
                 for row in rows]
        self.push_undo("★切替", flags=flags)
        self.dbmod.set_flags(self.conn, rows, favorite=new)
        self.refresh()
        self.status.set(f"{len(rows)} 件を{'★に' if new else '★から外'}しました。")

    def toggle_hidden(self):
        rows = self.selected_rows()
        if not rows:
            self.status.set("先に候補を選んでください。")
            return
        new = not all(row["hidden"] for row in rows)
        flags = [(row["bank_path"], row["unit_id"],
                  (int(row["favorite"]), int(row["hidden"]), row.get("tags") or ""),
                  (int(row["favorite"]), int(new), row.get("tags") or ""))
                 for row in rows]
        self.push_undo("非表示/戻す", flags=flags)
        self.dbmod.set_flags(self.conn, rows, hidden=new)
        self.refresh()
        self.status.set(f"{len(rows)} 件を{'非表示に' if new else '表示に戻'}しました。"
                        "（元の素材集は変更していません）")

    def clean_duplicates(self):
        if not self.conn:
            return
        groups = self.dbmod.duplicates(self.conn)
        if not groups:
            self.status.set("近い重複は見つかりませんでした。")
            return
        total = sum(g["count"] for g in groups)
        if not messagebox.askyesno(
                "重複を整理",
                f"同じ発音・近い音程・近い長さの候補が {len(groups)} 組（{total} 件）あります。\n"
                "各組で最も長い1件を残し、他を非表示にします。\n\n"
                "元の素材集JSONは変更しません。よろしいですか？",
                parent=self.win):
            return
        drop = [row for group in groups for row in group["drop"]]
        flags = [(row["bank_path"], row["unit_id"],
                  (int(row["favorite"]), int(row["hidden"]), row.get("tags") or ""),
                  (int(row["favorite"]), 1, row.get("tags") or ""))
                 for row in drop]
        self.push_undo("重複を整理", flags=flags)
        hidden = self.dbmod.hide_duplicates(self.conn)
        self.refresh()
        self.status.set(f"{hidden} 件を非表示にしました（「非表示も表示」か Ctrl+Z で戻せます）。")

    def export_csv(self):
        if not self.conn:
            return
        path = filedialog.asksaveasfilename(title="候補一覧をCSVで保存", defaultextension=".csv",
                                            initialfile="phonemes.csv",
                                            filetypes=[("CSV", "*.csv")], parent=self.win)
        if not path:
            return
        try:
            count = self.dbmod.export_csv(self.conn, path, self.fav_only.get())
            self.status.set(f"{count} 件をCSVへ書き出しました: {path}")
        except Exception as e:  # noqa: BLE001
            self.status.set("CSV書き出しに失敗: " + str(e))

    def export_utau(self):
        if not self.conn:
            return
        out = filedialog.askdirectory(title="UTAU音源の書き出し先（空のフォルダ推奨）", parent=self.win)
        if not out:
            return
        win = tk.Toplevel(self.win)
        win.title("UTAU書き出し")
        win.geometry("470x370")
        win.transient(self.win)
        name = tk.StringVar(value=os.path.basename(out.rstrip("\\/")) or "NamaVoice")
        author = tk.StringVar()
        multiple = tk.BooleanVar(value=False)
        vcv = tk.BooleanVar(value=False)
        fav = tk.BooleanVar(value=self.fav_only.get())
        min_ms = tk.StringVar(value="0")
        for label, var in (("音源名", name), ("作者", author)):
            line = ttk.Frame(win, padding=(12, 8, 12, 0))
            line.pack(fill="x")
            ttk.Label(line, text=label + ":", width=6).pack(side="left")
            ttk.Entry(line, textvariable=var).pack(side="left", fill="x", expand=True)
        length_line = ttk.Frame(win, padding=(12, 8, 12, 0))
        length_line.pack(fill="x")
        ttk.Label(length_line, text="短い候補:", width=8).pack(side="left")
        ttk.Label(length_line, text="これ以下は書き出さない").pack(side="left")
        ttk.Entry(length_line, textvariable=min_ms, width=7).pack(side="left", padx=4)
        ttk.Label(length_line, text="ms（0=全部）").pack(side="left")
        ttk.Checkbutton(win, text="同じ発音を複数入れる（あA3 形式）",
                        variable=multiple).pack(anchor="w", padx=12, pady=(10, 0))
        ttk.Checkbutton(win, text="連続音（VCV）の別名も作る",
                        variable=vcv).pack(anchor="w", padx=12)
        ttk.Checkbutton(win, text="★だけ書き出す", variable=fav).pack(anchor="w", padx=12)
        ttk.Label(win, text="長さは候補全体（開始から終了まで）で判定します。\n"
                            "WAVは 44.1kHz / 24bit / モノラル に変換して書き出します。\n"
                            "WAV名・別名にアンダースコアは使いません。元WAVは変更しません。",
                  foreground="#5b6870", padding=(12, 8, 12, 0),
                  justify="left").pack(anchor="w")

        def go():
            try:
                try:
                    min_value = max(0.0, float(min_ms.get().strip() or "0"))
                except ValueError:
                    self.status.set("「短い候補」はミリ秒の数値で入力してください（0で全部）。")
                    return
                info = self.dbmod.export_utau(
                    self.conn, out, name.get().strip() or "NamaVoice", author.get().strip(),
                    vcv=vcv.get(), multiple=multiple.get(), favorites_only=fav.get(),
                    min_ms=min_value)
                short = f" / 短い候補 {info['too_short']}" if info["min_ms"] > 0 else ""
                self.status.set(f"UTAU: WAV {info['wav']} / oto {info['oto']}"
                                f"（VCV {info['vcv']}） / スキップ {info['skipped']}{short}")
                win.destroy()
            except Exception as e:  # noqa: BLE001
                self.status.set("UTAU書き出しに失敗: " + str(e))
        ttk.Button(win, text="書き出す", command=go).pack(pady=12)

    def play(self):
        row = self.selected()
        if not row or not row["source"]:
            return
        try:
            data, sr, ch, width = self.dbmod._read_frames(row["source"], row["start"], row["end"])
            path = os.path.join(tempfile.gettempdir(), "jinriki_db_preview.wav")
            with wave.open(path, "wb") as w:
                w.setnchannels(ch)
                w.setsampwidth(width)
                w.setframerate(sr)
                w.writeframes(data)
            if winsound:
                winsound.PlaySound(path, winsound.SND_FILENAME | winsound.SND_ASYNC)
        except Exception as e:  # noqa: BLE001
            self.status.set("試聴に失敗: " + str(e))

    def open_source(self):
        row = self.selected()
        if not row or not os.path.isfile(row["source"]):
            return
        self.app.load(row["source"])
        self.app.lyrics_var.set(row["lyrics"] or "")
        self.win.lift()

    def export_wavs(self):
        if not self.conn:
            return
        out = filedialog.askdirectory(title="WAVの書き出し先", parent=self.win)
        if not out:
            return
        try:
            count = self.dbmod.export_wavs(self.conn, out)
            self.status.set(f"{count} 件のWAVを書き出しました（44.1kHz/24bit/モノラル）。"
                            "元の音声は変更していません。")
        except Exception as e:  # noqa: BLE001
            self.status.set("書き出しに失敗: " + str(e))

    def export_library(self):
        if not self.conn:
            return
        path = filedialog.asksaveasfilename(title="library.json を保存", defaultextension=".json",
                                            initialfile="library.json",
                                            filetypes=[("JSON", "*.json")], parent=self.win)
        if not path:
            return
        try:
            self.dbmod.export_library(self.conn, path)
            self.status.set("書き出しました: " + path)
        except Exception as e:  # noqa: BLE001
            self.status.set("書き出しに失敗: " + str(e))

    def import_fonemudb(self):
        src = filedialog.askdirectory(title="音素DBの waves フォルダ", parent=self.win)
        if not src:
            return
        out = filedialog.askdirectory(title="素材集JSONの書き出し先", parent=self.win)
        if not out:
            return
        try:
            info = self.dbmod.import_fonemudb(src, out)
            self.status.set(f"取り込み: {info['written']} 件（読み飛ばし {info['skipped']}）。"
                            "この後、出力先を選んで索引を作り直してください。")
        except Exception as e:  # noqa: BLE001
            self.status.set("取り込みに失敗: " + str(e))


def main():
    if "--batch" in sys.argv:
        # Headless batch through the packaged app:
        #   NamaOnso.exe --batch FOLDER [--force] [--separator S] [--lyrics-from MODE]
        argv = sys.argv[1:]
        folder = argv[argv.index("--batch") + 1] if len(argv) > argv.index("--batch") + 1 else ""
        if not folder:
            print("使い方: NamaOnso.exe --batch FOLDER [--force]")
            return 2
        if not appenv.find_mvt():
            print(appenv.MVT_HINT)
            return 3
        import batch

        def value_of(flag: str, fallback: str) -> str:
            return argv[argv.index(flag) + 1] if flag in argv and len(argv) > argv.index(flag) + 1 else fallback

        results = batch.run_folder(folder, force="--force" in argv,
                                   lyrics_mode=value_of("--lyrics-from", "auto"),
                                   separator=value_of("--separator", "_"))
        for r in results:
            print(f"{r['status']:<14} {r['file']}  units={r['units']} pitch={r['pitch']} {r['note']}")
        ok = sum(1 for r in results if r["status"] == "OK")
        failed = sum(1 for r in results if r["status"] == "失敗")
        if results:
            print("レポート:", batch.write_report(folder, results))
        print(f"{ok} / {len(results)} 件を書き出しました。")
        return 1 if failed else 0
    if "--curate" in sys.argv:
        # Headless selection: NamaOnso.exe --curate --root TAKES --out OUT [...]
        import curate
        argv = sys.argv[1:]
        return curate.main(argv[argv.index("--curate") + 1:])
    if "--rename" in sys.argv:        # Re-derive kana names without touching the original JSONs:
        #   NamaOnso.exe --rename SOURCE OUTPUT [--all]
        argv = sys.argv[1:]
        i = argv.index("--rename")
        if len(argv) < i + 3:
            print("使い方: NamaOnso.exe --rename SOURCE OUTPUT [--all]")
            return 2
        import rename_bank
        return rename_bank.main([argv[i + 1], argv[i + 2]] + (["--all"] if "--all" in argv else []))
    if "--check" in sys.argv:
        # Headless readiness check; also used to smoke-test the frozen exe.
        info = {
            "frozen": appenv.frozen(),
            "app_dir": appenv.app_dir(),
            "mvt": appenv.find_mvt() or "",
            "uv": appenv.find_uv() or "",
            "model": appenv.find_model() or "",
            "icon": appenv.asset_path("NamaOnso.ico") or "",
            "python": sys.version.split()[0],
        }
        for name in ("tkinter", "numpy", "onnxruntime", "core", "batch", "coverage",
                     "f0_neural", "db", "rename_bank", "export_audio", "curate",
                     "curate_gui", "tkinterdnd2"):
            try:
                __import__(name)
                info[name] = "ok"
            except Exception as e:  # noqa: BLE001
                info[name] = f"missing ({e})"
        for key, value in info.items():
            print(f"{key}: {value}")
        try:
            import tempfile
            with open(os.path.join(tempfile.gettempdir(), "nama_onso_check.txt"),
                      "w", encoding="utf-8", newline="\n") as f:
                for key, value in info.items():
                    f.write(f"{key}: {value}\n")
        except Exception:
            pass
        if not info["mvt"]:
            print("\n警告: mvt が見つかりません。音素の自動割り当ては使えません。")
        return 0
    root = TkinterDnD.Tk() if HAVE_DND else tk.Tk()
    App(root)
    root.mainloop()
    return 0


if __name__ == "__main__":
    sys.exit(main())

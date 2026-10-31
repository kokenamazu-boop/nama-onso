"""Tk window for the take curator (音素の選抜). Opened from the main toolbar."""
from __future__ import annotations

import os
import queue
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

import curate

MODES = (("2テイク（_L/_H・推奨）", "two_take"),
         ("1テイク（単一音階）", "single"),
         ("使える分だけ残す", "keep_usable"))


class CuratorWindow:
    def __init__(self, app):
        self.app = app
        self.job: "queue.Queue" = queue.Queue()
        self.running = False

        self.win = tk.Toplevel(app.root)
        self.win.title("音素の選抜（間引き）")
        self.win.geometry("780x640")
        self.win.transient(app.root)

        body = ttk.Frame(self.win, padding=10)
        body.pack(fill="both", expand=True)

        ttk.Label(body, text="録音したテイク（1ファイル1音節）から、使うWAVを選びます。元WAVは変更しません。",
                  foreground="#37444c").pack(anchor="w", pady=(0, 8))

        self.root_var = tk.StringVar()
        self.oto_var = tk.StringVar()
        self.out_var = tk.StringVar()
        for label, var, title, is_dir in (
                ("元のWAV", self.root_var, "WAVが入ったフォルダ", True),
                ("oto.ini", self.oto_var, "既存のoto.ini（任意）", False),
                ("出力先", self.out_var, "選抜結果の出力フォルダ", True)):
            line = ttk.Frame(body)
            line.pack(fill="x", pady=2)
            ttk.Label(line, text=label + ":", width=8).pack(side="left")
            ttk.Entry(line, textvariable=var).pack(side="left", fill="x", expand=True, padx=4)
            ttk.Button(line, text="参照", width=6,
                       command=lambda v=var, t=title, d=is_dir: self.pick(v, t, d)).pack(side="left")

        settings = ttk.LabelFrame(body, text="設定", padding=8)
        settings.pack(fill="x", pady=(8, 4))
        row1 = ttk.Frame(settings)
        row1.pack(fill="x")
        ttk.Label(row1, text="モード:").pack(side="left")
        self.mode_label = tk.StringVar(value=MODES[0][0])
        ttk.Combobox(row1, textvariable=self.mode_label, width=22, state="readonly",
                     values=[m[0] for m in MODES]).pack(side="left", padx=(4, 12))
        ttk.Label(row1, text="分割点:").pack(side="left")
        self.split_var = tk.StringVar(value=curate.DEFAULT_SPLIT)
        ttk.Entry(row1, textvariable=self.split_var, width=6).pack(side="left", padx=(4, 4))
        ttk.Label(row1, text="（_L は下／_H は上）", foreground="#5b6870").pack(side="left")
        self.prefix_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(settings, text="prefix.map も出力（classic UTAU用）",
                        variable=self.prefix_var).pack(anchor="w", pady=(6, 0))
        self.fix_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(settings, text="OTOを補正する（既存oto.iniの値を注文書3.7のルールで直す）",
                        variable=self.fix_var).pack(anchor="w")
        row2 = ttk.Frame(settings)
        row2.pack(fill="x", pady=(6, 0))
        ttk.Label(row2, text="音源名:").pack(side="left")
        self.name_var = tk.StringVar(value="Nama Onso")
        ttk.Entry(row2, textvariable=self.name_var, width=20).pack(side="left", padx=(4, 12))
        ttk.Label(row2, text="作者:").pack(side="left")
        self.author_var = tk.StringVar()
        ttk.Entry(row2, textvariable=self.author_var, width=18).pack(side="left", padx=4)

        actions = ttk.Frame(body)
        actions.pack(fill="x", pady=(8, 2))
        self.run_btn = ttk.Button(actions, text="選抜を実行", command=self.start)
        self.run_btn.pack(side="left")
        self.open_out = ttk.Button(actions, text="出力フォルダを開く", state="disabled",
                                   command=lambda: self.open_path(self.out_var.get()))
        self.open_out.pack(side="left", padx=6)
        self.open_csv = ttk.Button(actions, text="selection.csv を開く", state="disabled",
                                   command=lambda: self.open_path(
                                       os.path.join(self.out_var.get(), "selection.csv")))
        self.open_csv.pack(side="left")
        self.progress = ttk.Progressbar(actions, length=220, mode="determinate")
        self.progress.pack(side="right")

        self.status = tk.StringVar(value="フォルダを選んで「選抜を実行」を押してください。")
        ttk.Label(body, textvariable=self.status, foreground="#37444c").pack(anchor="w", pady=(4, 0))

        self.log = tk.Text(body, height=14, font=("Consolas", 9), state="disabled")
        scroll = ttk.Scrollbar(body, command=self.log.yview)
        self.log.configure(yscrollcommand=scroll.set)
        self.log.pack(side="left", fill="both", expand=True, pady=(6, 0))
        scroll.pack(side="right", fill="y", pady=(6, 0))

        self.app.root.after(150, self.poll)
        if app.bank_path:
            self.root_var.set(os.path.dirname(app.bank_path))

    # ------------------------------------------------------------------ ui
    def pick(self, var, title, is_dir):
        if is_dir:
            path = filedialog.askdirectory(title=title, parent=self.win)
        else:
            path = filedialog.askopenfilename(title=title,
                                              filetypes=[("oto.ini", "*.ini"), ("すべて", "*.*")],
                                              parent=self.win)
        if path:
            var.set(path)

    def log_line(self, text: str):
        self.log.configure(state="normal")
        self.log.insert("end", text + "\n")
        self.log.see("end")
        self.log.configure(state="disabled")

    @staticmethod
    def open_path(path: str):
        if path and os.path.exists(path):
            os.startfile(path)  # type: ignore[attr-defined]

    # ----------------------------------------------------------------- run
    def mode(self) -> str:
        for label, key in MODES:
            if label == self.mode_label.get():
                return key
        return "two_take"

    def start(self):
        if self.running:
            return
        root_dir = self.root_var.get().strip()
        out_dir = self.out_var.get().strip()
        if not os.path.isdir(root_dir):
            messagebox.showinfo("フォルダが必要", "元のWAVフォルダを選んでください。", parent=self.win)
            return
        if not out_dir:
            messagebox.showinfo("出力先が必要", "出力先フォルダを選んでください。", parent=self.win)
            return
        force = False
        if os.path.isdir(out_dir) and os.listdir(out_dir):
            if not messagebox.askyesno(
                    "上書き",
                    "出力先にファイルがあります。\n"
                    "このツールが作った wav/ とCSV・oto.ini などを消して作り直します。\n"
                    "（他のファイルは残します）よろしいですか？", parent=self.win):
                return
            force = True

        cfg = curate.Config(mode=self.mode(), split_note=self.split_var.get().strip() or curate.DEFAULT_SPLIT,
                            write_prefix_map=self.prefix_var.get(),
                            name=self.name_var.get().strip() or "Nama Onso",
                            author=self.author_var.get().strip())
        oto_path = self.oto_var.get().strip()
        fix = self.fix_var.get()

        self.running = True
        self.run_btn.state(["disabled"])
        self.open_out.state(["disabled"])
        self.open_csv.state(["disabled"])
        self.progress.configure(value=0, maximum=1)
        self.log.configure(state="normal")
        self.log.delete("1.0", "end")
        self.log.configure(state="disabled")
        self.log_line(f"元: {root_dir}")
        self.log_line(f"出力: {out_dir}  モード: {cfg.mode}  分割: {cfg.split_note}")
        self.status.set("解析中…")

        def progress(done, total, name):
            self.job.put(("progress", done, total, name))

        def work():
            try:
                result = curate.run(root_dir, out_dir, cfg, oto=oto_path, fix=fix,
                                    force=force, progress=progress)
                self.job.put(("done", result))
            except Exception as exc:  # noqa: BLE001 - show it, never kill the app
                self.job.put(("error", str(exc)))

        threading.Thread(target=work, daemon=True).start()

    def poll(self):
        try:
            while True:
                item = self.job.get_nowait()
                if item[0] == "progress":
                    done, total, name = item[1], item[2], item[3]
                    self.progress.configure(maximum=max(1, total), value=done)
                    if done == total or done % 200 == 0:
                        self.status.set(f"解析中… {done}/{total}")
                    if done % 500 == 0:
                        self.log_line(f"  {done}/{total} {name}")
                elif item[0] == "error":
                    self.finish()
                    self.status.set("失敗しました。")
                    self.log_line("失敗: " + item[1])
                    messagebox.showerror("選抜に失敗", item[1], parent=self.win)
                elif item[0] == "done":
                    self.finish()
                    self.report(item[1])
        except queue.Empty:
            pass
        try:
            self.win.after(150, self.poll)
        except tk.TclError:
            return

    def finish(self):
        self.running = False
        self.run_btn.state(["!disabled"])
        self.open_out.state(["!disabled"])
        self.open_csv.state(["!disabled"])

    def report(self, result: dict):
        summary = result["manifest"]["summary"]
        self.status.set(f"採用 {summary['selected']} / 再録候補 {summary['rerecord']} "
                        f"（エイリアス {summary['aliases']}）")
        self.log_line("")
        self.log_line(f"テイク {summary['takes']} / エイリアス {summary['aliases']} / "
                      f"採用 {summary['selected']} / 除外 {summary['rejected']} / "
                      f"再録候補 {summary['rerecord']}")
        for row in result["rerecords"][:12]:
            self.log_line(f"  {row['priority']} {row['alias']}: {row['reason']}")
        self.log_line("")
        self.log_line("書き出し: wav/ oto.ini character.yaml prefix.map "
                      "selection.csv rejected.csv rerecord.csv manifest.json")

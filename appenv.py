"""Locate resources and external tools for both source and frozen (PyInstaller) runs.

Everything here is stdlib-only so it can be imported before numpy/onnxruntime.
"""
from __future__ import annotations

import glob
import os
import shutil
import subprocess
import sys
from typing import List, Optional


def frozen() -> bool:
    return bool(getattr(sys, "frozen", False))


def app_dir() -> str:
    """Folder that holds the exe when frozen, otherwise this package."""
    if frozen():
        return os.path.dirname(os.path.abspath(sys.executable))
    return os.path.dirname(os.path.abspath(__file__))


def asset_path(name: str) -> Optional[str]:
    """Bundled file under assets/ (source run or PyInstaller's _internal)."""
    bases = []
    meipass = getattr(sys, "_MEIPASS", "")
    if meipass:
        bases.append(meipass)
    bases.append(app_dir())
    for base in bases:
        path = os.path.join(base, "assets", name)
        if os.path.isfile(path):
            return path
    return None


def model_dirs() -> List[str]:
    """Candidate folders for fcpe.onnx, most specific first."""
    dirs: List[str] = []
    env = os.environ.get("NAMA_FCPE_DIR")
    if env:
        dirs.append(os.path.abspath(env))
    base = app_dir()
    dirs += [
        os.path.join(base, "models"),
        os.path.join(base, "jinriki", "models"),
        os.path.join(os.path.dirname(base), "jinriki", "models"),
        os.path.join(os.path.dirname(os.path.dirname(base)), "jinriki", "models"),
        os.path.join(os.path.expandvars(r"%LOCALAPPDATA%"), "Nama", "models"),
    ]
    seen, out = set(), []
    for d in dirs:
        d = os.path.abspath(d)
        if d not in seen:
            seen.add(d)
            out.append(d)
    return out


def find_model(name: str = "fcpe.onnx") -> Optional[str]:
    for d in model_dirs():
        path = os.path.join(d, name)
        if os.path.isfile(path):
            return path
    return None


def find_mvt() -> Optional[str]:
    """mVocalToolkit's CLI. Never bundled: it brings its own engine and models."""
    env = os.environ.get("NAMA_MVT")
    if env and os.path.isfile(env):
        return env
    base = app_dir()
    for name in ("mvt.exe", "mvt"):
        local = os.path.join(base, name)
        if os.path.isfile(local):
            return local
    found = shutil.which("mvt")
    if found:
        return found
    home = os.path.expanduser("~")
    for cand in (os.path.join(home, ".local", "bin", "mvt.exe"),
                 os.path.join(home, ".local", "bin", "mvt")):
        if os.path.isfile(cand):
            return cand
    pattern = os.path.join(os.path.expandvars(r"%LOCALAPPDATA%"), "uv", "tools", "*", "Scripts", "mvt.exe")
    hits = sorted(glob.glob(pattern))
    return hits[0] if hits else None


MVT_HINT = ("mvt が見つかりません。\n"
            "PowerShell で次を実行してください:\n"
            "  uv tool install git+https://github.com/Megageorgio/mVocalToolkit\n"
            "  mvt models download tifa-1.0-st\n"
            "別の場所にある場合は環境変数 NAMA_MVT に実行ファイルのパスを設定してください。")

MVT_PACKAGE = "git+https://github.com/Megageorgio/mVocalToolkit"
MVT_MODEL = "tifa-1.0-st"
UV_HINT = ("uv が見つかりません。\n"
           "PowerShell で次を実行してください:\n"
           "  winget install --id=astral-sh.uv -e\n"
           "入れた後、この画面のボタンをもう一度押してください。")


def find_uv() -> Optional[str]:
    """The uv launcher used for 'uv tool install'. Not required at run time."""
    found = shutil.which("uv")
    if found:
        return found
    home = os.path.expanduser("~")
    for cand in (os.path.join(home, ".local", "bin", "uv.exe"),
                 os.path.join(home, ".cargo", "bin", "uv.exe"),
                 os.path.join(os.path.expandvars(r"%LOCALAPPDATA%"), "uv", "uv.exe")):
        if os.path.isfile(cand):
            return cand
    return None


def mvt_install_command() -> Optional[List[str]]:
    """Command that installs mvt through uv, or None when uv is missing."""
    uv = find_uv()
    if not uv:
        return None
    return [uv, "tool", "install", MVT_PACKAGE]


def mvt_model_command(mvt_path: str) -> List[str]:
    return [mvt_path, "models", "download", MVT_MODEL]


def run_command(cmd: List[str], on_line=None) -> int:
    """Run a helper tool, streaming its combined output one line at a time."""
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    env = dict(os.environ, PYTHONUTF8="1", PYTHONIOENCODING="utf-8")
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                            text=True, encoding="utf-8", errors="replace",
                            env=env, creationflags=flags, bufsize=1)
    try:
        if proc.stdout is not None:
            for line in proc.stdout:
                if on_line:
                    on_line(line.rstrip())
    finally:
        if proc.stdout is not None:
            proc.stdout.close()
    return proc.wait()

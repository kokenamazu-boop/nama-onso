# Third-party notices

This release bundles the following software inside `_internal/`.
The Nama Onso source code itself is MIT licensed (see `LICENSE`).

| Component | License | Upstream |
|---|---|---|
| Python 3.x | PSF License | https://www.python.org/ |
| Tcl/Tk | BSD-style | https://www.tcl.tk/ |
| numpy | BSD-3-Clause (and bundled components under 0BSD/MIT/Zlib/CC0) | https://numpy.org/ |
| onnxruntime | MIT | https://github.com/microsoft/onnxruntime |
| tkinterdnd2 / TkDND | MIT / BSD-style | https://github.com/pmgagne/tkinterdnd2 |
| PyInstaller bootloader | GPLv2-or-later with a special exception for distributing bundled applications | https://pyinstaller.org/ |

Not included:

- **mVocalToolkit (`mvt`)** — installed separately by the user.
  https://github.com/Megageorgio/mVocalToolkit
- **FCPE model files** (`fcpe.onnx`, `mel_filterbank.bin`, `cent_table.bin`) —
  not redistributed; place them in `models/`. FCPE is MIT (CNChTu/FCPE).

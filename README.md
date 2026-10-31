# なま音素 (Nama Onso)

歌・セリフ・効果音などの WAV から**音素（モーラ）を切り出して名前を付け、検索・整理・書き出し**する Windows 用ツールです。
切り出した素材は WAV / CSV / JSON で取り出せるので、**UTAU/OpenUtau に限らず**、DAW・サンプラー・効果音管理・コーパス作成などに使えます。

- 音素の位置を自動推定し、波形を見ながら手で直せる
- 素材は**元 WAV への参照**で保持（音声を複製しない・元ファイルを変更しない）
- SQLite の索引で、発音・音程・長さ・前後の文脈から検索
- 1候補1WAVで書き出し（44.1kHz / 24bit / モノラル）。CSV・JSON・REAPER用プロジェクトにも対応
- UTAU/OpenUtau 音源（`oto.ini` / `character.yaml` / `prefix.map`）の書き出しは**任意機能**
- 録音テイクを解析して、使えるテイクだけを残す**「音素の選抜（間引き）」**つき

> English: A Windows tool that cuts phonemes/morae out of singing, speech or effect WAVs,
> names and indexes them in SQLite, and exports WAV/CSV/JSON for any sampler or DAW
> (UTAU/OpenUtau voicebanks are an optional export target).

## ダウンロード

ビルド済みの Windows 版（onedir zip、インストーラーなし）は
[Releases](https://github.com/kokenamazu-boop/nama-onso/releases/latest) から取得できます。

---

## 主な用途

- 歌声やセリフから、発音ごとのワンショット素材を作る
- DAW / サンプラー / 効果音管理用の音素ライブラリを作る
- 録音テイクの品質を測り、使うテイクを選抜する
- UTAU / OpenUtau 音源の下ごしらえ（任意）

---

## 必要なもの

| 項目 | 必須 | 内容 |
|---|---|---|
| Windows 10/11 (64bit) | 必須 | GUI は tkinter を使用 |
| Python 3.10+ | 必須（ソース実行） | 追加ライブラリは下記 |
| numpy | 推奨 | 解析と書き出し変換が速くなる（無い場合は簡易経路） |
| mVocalToolkit (`mvt`) | 自動推定を使う場合 | 音素位置を推定する外部 CLI。同梱しません |
| FCPE モデル (`fcpe.onnx`) | 任意 | 音程推定の精度が上がる。無い場合は YIN |
| onnxruntime | 任意 | FCPE を使う場合のみ |
| tkinterdnd2 | 任意 | 表から他アプリへドラッグする場合 |

```powershell
git clone https://github.com/kokenamazu-boop/nama-onso.git
cd nama-onso
python -m pip install -r requirements.txt
```

音素の自動推定を使う場合（「ヘルプ」画面のボタンからも導入できます）:

```powershell
uv tool install git+https://github.com/Megageorgio/mVocalToolkit
mvt models download tifa-1.0-st
```

FCPE モデルは `models\fcpe.onnx` などに置きます（リポジトリには含めません）。

---

## GUI の使い方

```powershell
python gui.py
```

1. **WAVを開く** — 音素を切り出したい素材を選びます（元ファイルは変更しません）
2. **読み（歌詞）** を入力して **自動で割り当て** — 音素の位置を推定し、モーラ単位にまとめます
   - 読みが無い素材は、自動割り当てを使わずに手動で範囲を作れます
3. 波形で境界を確認 — 境界の近く（5px以内）をドラッグ、または `←/→`（±5ms）、`Shift+←/→`（±50ms）、`Alt+←/→`（±1ms）
   - `Ctrl+Z` / `Ctrl+Y` で元に戻す／やり直し
   - 境界は **橙＝自動推定 / 緑＝確認済み**。「要確認だけ表示」で未確認だけに絞れます
4. 必要なら名前を変更 — **素材集を保存**（`<WAV名>.nama-voices.json`）
5. **音素DB** で索引・検索・試聴・ドラッグ＆ドロップ・書き出し
6. 録音テイクをまとめて選抜するときは **音素の選抜**

---

## できること

### 取り込みと修正

- mVocalToolkit（TIFA）による音素位置の推定と、モーラ単位へのまとめ
- カタカナ→ひらがな、音素→かなの命名
- 境界のドラッグ／キー操作、Undo/Redo
- 冒頭・末尾の無音補正、モーラ数不一致の保護
- フォルダ一括処理（`曲名_歌詞.wav`、同名 `.txt`、既存スキップ、レポートTSV）

### 音素DB（SQLite 索引）

- 発音・子音・母音・前の母音・次の子音・音程・長さ・曲名で検索／並べ替え
- ★・非表示・タグ（索引側だけに保存。索引を作り直しても残る）
- 近い重複の整理、複数フォルダの統合
- 試聴、元フレーズを開く、CSV 書き出し
- 表から REAPER やエクスプローラーへ直接ドラッグ

### 書き出し（用途別）

- **WAV** — 1候補1ファイル。44.1kHz / 24bit / モノラルに統一（`--pattern` で命名可）
- **CSV** — 発音・音素・前後文脈・音程・長さ・★/非表示・タグ・元WAVの一覧
- **library.json** — 素材集のパスとハッシュの一覧
- **REAPER用プロジェクト** — 候補をリージョン、音素の開始をマーカーにした確認用
- **UTAU / OpenUtau 音源（任意）** — WAV＋`oto.ini`＋`character.yaml`＋`prefix.map`

### 音素の選抜（間引き）

録音したテイク（`かD#45.wav` のような1ファイル1音節）から、**使う WAV を選びます**。
OTO のタイミング生成はしません。

- ハード除外：長さ150ms未満 / 母音40ms未満 / クリップ5超 / ピーク −1〜−24dBFS 外 / 可聴点なし
- 破裂音は 5ms の dB 上昇、濁破裂音は prevoicing、ざ行は voice bar で評価
- スコアは「長さ・母音・頭の無音・SNR のコスト ＋ 2×音程差 − 0.8×jump − 0.4×prevoicing − 0.5×voice bar」
- どのエイリアスも最低1テイク残す。1本だけなら同じ WAV を両方に割り当て、`scarce`＋再録P1
- 2テイク（`_L`/`_H`）／1テイク／合格全部の3モード
- 再録候補は P0（破裂/voice barなし）→ P1（弱い・短い）→ P2（トリム可）で `rerecord.csv`

---

## CLI

すべて `python <script>.py` で実行できます。`--help` で引数を確認してください。

### フォルダ一括処理

```text
python batch.py D:\recordings\phrases
python batch.py D:\recordings\phrases --dry-run
python batch.py D:\recordings\phrases --limit 3
```

- 読みは同名の `.txt`（1行目）か、`曲名_歌詞.wav` の右側を使います
- 既存の素材集 JSON・同じ音声（SHA-256）はスキップ。`batch-report.tsv` に結果を残します
- 元 WAV は変更しません

### カバレッジ

```text
python coverage.py D:\recordings\phrases
python coverage.py D:\recordings\phrases --standard
python coverage.py D:\recordings\phrases --target "みえてるずっとさきで"
```

発音ごとの候補数・音程・長さ、1件しかない発音、未収録を表示します（読み取り専用）。

### 音素DB

```text
python db.py build   D:\recordings\phrases
python db.py list    D:\recordings\phrases --name あ --sort midi
python db.py flag    D:\recordings\phrases --name あ --favorite
python db.py duplicates D:\recordings\phrases [--hide]
python db.py export-wavs D:\recordings\phrases D:\out
python db.py export-csv  D:\recordings\phrases D:\out\phonemes.csv
python db.py export-library D:\recordings\phrases library.json
```

- 索引は `nama-phonemes.db`。JSON が変わった分だけ更新します
- ★・非表示・別名は索引側だけに保存し、索引を作り直しても残ります
- 音声は複製しません（元 WAV の区間を参照）

### 書き出し

```text
python db.py export-wavs FOLDER OUT_DIR [--pattern "{name}_{note}_{index}"]
python db.py export-csv  FOLDER OUT.csv [--favorites] [--hidden]
python db.py export-library FOLDER library.json
python db.py export-utau FOLDER OUT_DIR [--multiple] [--vcv] [--min-ms 100]
```

`export-utau` は任意の書き出し先です（UTAU/OpenUtau を使う場合のみ）。
WAV はすべて 44.1kHz / 24bit / モノラルに変換します。

### 音素の選抜

```text
python curate.py --root D:\recordings\takes --out D:\recordings\takes_2take
python curate.py --root ... --out ... --mode single
python curate.py --root ... --out ... --mode keep_usable
python curate.py --root ... --out ... --split-note F#4
python curate.py --root ... --out ... --oto oto.ini --fix-oto
```

出力：

```text
出力/
  wav/            採用したWAV（コピー）
  oto.ini         UTAU/OpenUtau 用（two_take の alias）
  character.yaml  OpenUtau 用 subbanks（two_take のとき）
  prefix.map      classic UTAU 用（two_take のとき）
  selection.csv   採用テイクとスコア
  rejected.csv    除外理由（ハード除外 + 選抜外）
  rerecord.csv    再録候補（P0/P1/P2）
  features.csv    全テイクの測定値（調整用）
  manifest.json   設定・全エイリアスの採用/除外
```

- 出力先が空でない場合は `--force` が必要です（**このツールが作ったファイルだけ**削除）
- 書き込みは一時ファイル経由。終了コード：0=成功 / 10=再録候補あり / 20=0テイク / 30=入力エラー
- 閾値は `--config`（JSON/YAML）で変更できます。`features.csv` を見ながら調整してください

---

## 素材集ファイル（`nama-voices.json`）

```json
{
  "format": "nama-voices",
  "version": 1,
  "source": { "path": "...", "sha256": "...", "sampleRate": 48000, "channels": 2 },
  "lyrics": "みえてるずっとさきで",
  "units": [
    { "id": "u001", "name": "み", "phonemes": "m i", "phoneme_starts": [0.26, 0.37],
      "start": 0.26, "end": 1.04, "confidence": "auto",
      "pitch": { "midi": 63.2, "hz": 311.5, "strength": 0.93 } }
  ]
}
```

音声は元 WAV への参照（パス＋SHA-256）で持ち、複製しません。
`phoneme_starts` は音素の絶対秒、`confidence` は `auto` / `checked` です。

---

## 書き出す WAV の形式

ドラッグ、選択をWAV、WAV書き出し、UTAU の WAV はすべて **44.1kHz / 24-bit / モノラル** に揃えます。

- ステレオは左右の平均でモノラル化
- レート変換は窓付き sinc（ダウンサンプル時は先に低域通過）
- 24-bit へ丸めのみ（ディザなし）。元 WAV は読み取りのみ
- 試聴用の一時 WAV は winsound の都合で従来のまま

---

## exe にする

```powershell
python -m pip install pyinstaller tkinterdnd2
powershell -ExecutionPolicy Bypass -File tools\build.ps1
```

出力：`_releases\nama-onso\NamaOnso\`（onedir）。

- `NamaOnso.exe --check` で、同梱物・`mvt`・モデル・アイコンの検出結果を表示
- `--batch` / `--curate` / `--rename` は画面なしで実行できます
- `mvt` は同梱せず、実行時に PATH や `%LOCALAPPDATA%\uv\tools\...` から探します

---

## テスト

```powershell
python selftest.py      # 合成音のみ。利用者の素材は使いません
python check_gui.py     # 画面のある環境で、GUI・Undo・選抜ウィンドウを検査
```

`selftest.py` は WAV 情報・モーラ分割・命名・素材集 JSON 往復・索引・重複・CSV・
書き出し形式・音素の選抜（two_take/single/keep_usable・scarce・voice bar・OTO補正）を確認します。

---

## データの扱い

- 元 WAV：読み取りのみ。変更しません
- 素材集 JSON：`<名前>.nama-voices.json`（音声は含みません）
- 索引：`nama-phonemes.db`（SQLite）。消しても JSON から作り直せます
- 選抜の `wav/` は**元テイクのコピー**です

---

## 制限

- 音素の自動推定は mVocalToolkit（TIFA）に依存し、**日本語の歌声・セリフ向け**です。精度は素材次第で、境界は耳で直してください
- 自動割り当てには読み（かな）が必要です。読みが無い素材は手動で範囲を作ってください
- GUI の試聴は winsound の簡易再生です（レイテンシー保証なし）
- 音素の選抜は測定値による補助です。最終的な良し悪しは耳で確認してください
- UTAU/OpenUtau の `oto.ini` は自動推定です。使う場合は各ソフトで微調整してください
- 長い曲を丸ごと渡す設計ではありません。まず1フレーズで確認する用途です

---

## ライセンス

MIT License. 詳細は [LICENSE](LICENSE) を参照してください。

同梱・依存する外部ソフトウェアのライセンスは各プロジェクトに従います
（mVocalToolkit は同梱しません。FCPE モデルは再配布しません）。

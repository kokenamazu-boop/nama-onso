# なま音素 (Nama Onso)

歌やセリフの WAV から**発音（音素）を切り出し、名前を付けて音源素材にする**ための Windows 用ツールです。
カットした素材を **UTAU / OpenUtau 用の音源フォルダ**へ書き出すところまでを担当します。

- GUI（tkinter）と CLI の両方があります
- 元の WAV は**読み取り専用**。素材集 JSON・索引 DB・切り出した WAV だけを書き出します
- 録音テイクを解析して、自動 OTO で破綻しやすいテイクを外す**「音素の選抜（間引き）」**つき

> A small Windows tool that cuts phonemes out of singing/voice WAVs, organises them in a
> SQLite index, exports UTAU/OpenUtau banks, and curates raw CV takes before auto-OTO.

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

`mvt` の導入（「ヘルプ」画面のボタンからも実行できます）:

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

1. **WAVを開く** — 声だけの素材を選びます（元ファイルは変更しません）
2. **歌詞**を入力して **自動で割り当て** — 音素の位置を推定し、モーラ単位にまとめます
3. 波形で境界を確認 — 境界の近く（5px以内）をドラッグ、または `←/→`（±5ms）、`Shift+←/→`（±50ms）、`Alt+←/→`（±1ms）
   - `Ctrl+Z` / `Ctrl+Y` で元に戻す／やり直し
   - 境界は **橙＝自動推定 / 緑＝確認済み**。「要確認だけ表示」で未確認だけに絞れます
4. 必要なら名前を変更 — **素材集を保存**（`<WAV名>.nama-voices.json`）
5. **音素DB** で索引・検索・試聴・UTAU書き出し・ドラッグ＆ドロップ
6. **音素の選抜** で、録音テイクのフォルダから `_L`/`_H` の2テイクへ間引き

---

## CLI

### フォルダ一括処理

```text
python batch.py D:\recordings\phrases
python batch.py D:\recordings\phrases --dry-run
python batch.py D:\recordings\phrases --limit 3
```

- 歌詞は同名の `.txt`（1行目）か、`曲名_歌詞.wav` の右側を使います
- 既存の素材集 JSON・同じ音声（SHA-256）はスキップ。`batch-report.tsv` に結果を残します
- 元 WAV は変更しません

### カバレッジ

```text
python coverage.py D:\recordings\phrases
python coverage.py D:\recordings\phrases --standard
python coverage.py D:\recordings\phrases --target "みえてるずっとさきで"
```

発音ごとの候補数・音程・長さ、1件しかない発音、未収録を表示します（読み取り専用）。

### 音素DB（SQLite 索引）

```text
python db.py build   D:\recordings\phrases
python db.py list    D:\recordings\phrases --name あ --sort midi
python db.py flag    D:\recordings\phrases --name あ --favorite
python db.py duplicates D:\recordings\phrases [--hide]
python db.py export-wavs D:\recordings\phrases D:\out
python db.py export-csv  D:\recordings\phrases D:\out\phonemes.csv
python db.py export-utau D:\recordings\phrases D:\out\utau --min-ms 100
python db.py export-library D:\recordings\phrases library.json
```

- 索引は `nama-phonemes.db`。JSON が変わった分だけ更新します
- ★・非表示・別名は索引側だけに保存し、索引を作り直しても残ります
- 音声は複製しません（元 WAV の区間を参照）

### UTAU / OpenUtau 書き出し

```text
python db.py export-utau FOLDER OUT_DIR [--name 音源名] [--author 作者]
                    [--multiple] [--vcv] [--favorites] [--min-ms ミリ秒]
```

- WAV + `oto.ini` + `character.txt`（cp932・CRLF）
- 別名は `あ` / 複数は `あA3`。**ファイル名・別名に `_` は使いません**
- `--min-ms` で「これより短い候補」を除外できます（0 で全部）
- oto.ini の値は自動推定です。UTAU 側での微調整を前提にしてください
- 書き出す WAV は **44.1kHz / 24bit / モノラル** に統一します

### 音素の選抜（間引き・AutoOTO前処理）

録音したテイク（`かD#45.wav` のような1ファイル1音節）から、**使う WAV を選びます**。
OTO のタイミング生成はしません。

```text
python curate.py --root D:\recordings\takes --out D:\recordings\takes_2take
python curate.py --root ... --out ... --mode single
python curate.py --root ... --out ... --mode keep_usable
python curate.py --root ... --out ... --split-note F#4
python curate.py --root ... --out ... --oto oto.ini --fix-oto
```

- ハード除外：長さ150ms未満 / 母音40ms未満 / クリップ5超 / ピーク −1〜−24dBFS 外 / 可聴点なし
- 破裂音は 5ms の dB 上昇、濁破裂音は prevoicing、ざ行は voice bar で評価
- スコアは「長さ・母音・頭の無音・SNR のコスト ＋ 2×音程差 − 0.8×jump − 0.4×prevoicing − 0.5×voice bar」
- `_L` は D4、`_H` は A4 付近を狙う。低音>高音なら入れ替え
- **どのエイリアスも最低1テイク残す**。1本だけなら同じ WAV を両方に割り当て、`scarce`＋再録P1
- 再録候補は P0（破裂/voice barなし）→ P1（弱い・短い）→ P2（トリム可）で `rerecord.csv`

出力:

```text
出力/
  wav/            採用したWAV（コピー）
  oto.ini         alias に _L/_H を付けた行
  character.yaml  OpenUtau 用 subbanks（C1-F#4 / G4-B7）
  prefix.map      classic UTAU 用（タブ区切り3列、two_take のとき）
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

- 音素推定は mVocalToolkit（TIFA）に依存します。精度は素材次第で、境界は耳で直してください
- GUI の試聴は winsound の簡易再生です（レイテンシー保証なし）
- 音素の選抜は測定値による補助です。最終的な良し悪しは耳で確認してください
- 長い曲を丸ごと渡す設計ではありません。まず1フレーズで確認する用途です

---

## ライセンス

MIT License. 詳細は [LICENSE](LICENSE) を参照してください。

同梱・依存する外部ソフトウェアのライセンスは各プロジェクトに従います
（mVocalToolkit は同梱しません。FCPE モデルは再配布しません）。

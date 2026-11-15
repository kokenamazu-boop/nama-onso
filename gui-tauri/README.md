# なま音素 GUI 試作（段階1 DB窓＋段階2 波形窓）

`nama-onso-db-pilot-spec-20261114.md`（段階1）と
`nama-onso-waveform-stage2-spec-20261115.md`（段階2）を、既存の tkinter 版とは
別アプリとして実装したものです。既存の `core.py` / `db.py` / `export_audio.py` /
`gui.py` は変更していません。

## 構成

```text
gui-tauri/
  src/                 React 19 + TS
    tabs/DbTab.tsx     段階1の音素DB窓（既存タブ）
    tabs/WaveTab.tsx   段階2の波形窓（メイン）
    components/WaveView.tsx  wavesurfer.js の波形・リージョン・操作
  src-tauri/           Tauri 2：窓・ダイアログ・サイドカー・音声のasset許可
sidecar/
  onso_sidecar.py      stdin/stdout の JSON Lines サーバー
```

1窓のタブ構成です（U-4）：**波形 / 音素DB / 音素の選抜（置き場のみ）**。
Rust はサイドカーの起動・終了、JSON の往復、ダイアログ、ドラッグ、asset protocol の
許可だけを担当します。検索・整列・保存・書き出しのロジックはサイドカー（＝既存の
Python 関数）側です。

## 動かす

必要なもの：Node.js 20+、Rust（stable）、Python 3.11+、WebView2。
`numpy` は入っていれば書き出しが速くなります（無くても動きます）。

```powershell
cd gui-tauri
npm install
npm run tauri dev
```

初回は Rust のビルドに数分かかります。`npm run tauri build` で exe も作れますが、
この試作ではインストーラーを作らない設定（`bundle.active = false`）です。
サイドカーは dev では `python sidecar/onso_sidecar.py` として起動します。
Python の場所は `NAMA_ONSO_PYTHON`、スクリプトは `NAMA_ONSO_SIDECAR` で変更できます。

## 決定済みの未決事項（2026-11-15 名雪）

- **W-1**：asset protocol ＋ HTMLAudioElement（MediaElement）で元WAVをストリーミング再生。
  開いたWAVだけを Rust 側で `asset_protocol_scope().allow_file` に追加します。
- **W-2**：境界ドラッグは現行どおり重なり許可（自分の両端のみクランプ、最小区間5ms）。
- **W-3**：Space は選択候補の再生/停止のみ。全体再生は段階3。
- **W-4**：段階2に DB→波形の接続まで入れる。DB行の「波形で開く」で、元WAV・
  既存素材集の候補列・その区間を波形タブに開きます。

## 画面の要点

### 波形タブ（段階2）
- WAVを開く／起動引数のWAV、歌詞、自動で割り当て、素材集を保存。
- wavesurfer.js 8 の波形＋Regionsプラグイン。peaks はサイドカーの
  `core.build_peak_grid` の min/max を `[min, max, min, max, ...]` に interleave して
  1チャンネルの peaks として渡します。Regions は区間の帯・ラベル表示に使い、
  境界の5px掴み・5ms下限の編集は gui.py と同じ意味を保つため自前のオーバーレイで
  行います（Regions のリサイズハンドル幅や重なり許可の差を避けるため）。
- 候補区間の帯（緑＝確認済み／橙＝要確認）、選択枠、音素ティック、時間グリッド。
- 波形クリックでシーク（境界の5px以内は掴みを優先）、プレイヘッド、
  選択区間の再生・停止・ループ試聴。
- 境界ドラッグ（画面5px掴み、最小区間5ms）、キー微調整
  （←/→ ±5ms、Shift ±50ms、Alt ±1ms）、名前変更、確認済み化。
- Undo/Redo は候補列スナップショット・最大50。ドラッグ1ジェスチャ＝1エントリ。
- 要確認フィルタ、全体表示、ホイール移動、Alt+ホイールのカーソル中心ズーム
  （最小表示幅20ms）、REAPER用RPP書き出し。
- ライト／ダーク切り替え（U-2）。確認済み＝緑／要確認＝橙だけを機能色として残し、
  それ以外は素朴な中性色です。

### 音素DBタブ（段階1）
- フォルダ・索引・書き出しメニュー、絞り込み、仮想スクロールの10列表。
- 検索結果・並びは `db.search` と一致。★・非表示・別名・重複整理・Undo/Redo。
- 試聴（WebAudio）、REAPER／エクスプローラーへのドラッグ準備。
- 追加：選択行を波形タブで開く（W-4）。

## 確認用

```powershell
# 段階1のDB窓プロトコル（検索一致・フラグ保持・書き出し一致）
python build\pilot-test\test_sidecar.py

# 段階2の波形プロトコル（align一致・保存JSON一致・RPP一致）
python build\pilot-test\test_wave_sidecar.py

# 核に手を入れていないことの確認
python selftest.py

# 画面側のロジック
cd gui-tauri
npm test
npm run build
```

`build/pilot-test/` は gitignore された作業用フォルダで、合成した素材集を作って
上記チェックに使います（リポジトリに音声は入りません）。

### 試作時点の実測（合成素材）

- 150素材集 × 30候補 = 4,500件：索引 約1.4秒、検索（全件）約100ms
  （サイドカー単体 約70ms＋IPC）。表のDOM行は常に約24行だけ。
- 12秒WAV＋歌詞4モーラ：`wave.align` 約17秒（mvt、モデル準備済み）。進捗イベントで
  「音素を推定中」「ピッチ n/N」を表示し、画面は固まりません。
- 180秒 / 44.1kHz / ステレオ（31MB）：`wave.open`（facts＋peaks 203,539点）約3.0秒。
  波形は全表示から20ms付近までズームでき、表示キャンバスは可視分の8〜12枚だけ。
- 波形は12秒で全表示、20ms付近までズーム可能。wavesurfer 8 のマルチキャンバスで
  描画されます。ループ・プレイヘッド・クリックシークを確認済み。
- 実素材での一連（開く→割り当て→境界直し→保存）の体感確認は名雪側でお願いします。

## 現行 tkinter 版との違い・見つけた点

- 「非表示も表示」は `db.search(hidden=false)`（非表示も含める）として実装しました。
  現行 `DBWindow.refresh` は `hidden=True` を渡しており、非表示だけを表示します。
- `db.search` の `display` は音素からかなを引き直すため、別名 `あA3` が `あ` に戻ります。
  波形タブは候補名をそのまま表示し、DBタブは `name != raw_name` のとき別名を優先します。
  ドラッグ名・書き出し名は現行どおり `display` です。
- 並べ替えに `確認済み` を追加しました（`db._order_terms` の11種に合わせるため）。
- `db.duplicates` の返す行に `note` / `ms` がないため、サイドカー側で表示用の値を足しています。
- 段階1の15メソッドは変更していません。段階2で追加したのは
  `wave.open` / `wave.align` / `wave.save` / `wave.export_rpp` です。

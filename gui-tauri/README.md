# 音素DB窓 試作（段階1）

`nama-onso-db-pilot-spec-20261114.md` の段階1を、既存の tkinter 版とは別アプリとして
実装したものです。既存の `core.py` / `db.py` / `export_audio.py` / `gui.py` は変更して
いません。

## 構成

```text
gui-tauri/
  src/             React 19 + TS の画面（DB窓 1画面）
  src-tauri/       Tauri 2：窓・ダイアログ・サイドカー起動・JSON中継・ドラッグ
sidecar/
  onso_sidecar.py  stdin/stdout の JSON Lines サーバー（db.py を import）
```

Rust はサイドカーの起動・終了、JSON の往復、フォルダ／保存先ダイアログ、ドラッグ、
試聴用 WAV の読み出しだけを担当します。検索・書き出しのロジックはすべてサイドカー
（＝`db.py`）側です。

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

サイドカーは dev では `python sidecar/onso_sidecar.py` として起動します。Python の
場所を変えたいときは `NAMA_ONSO_PYTHON`、スクリプトを変えたいときは
`NAMA_ONSO_SIDECAR` を設定してください。

## 画面の要点

- 上段：素材集フォルダ・索引・書き出しメニュー。下段：絞り込み一式＋件数。
- 表は10列（印・発音・子音・母音・前の母音・次の子音・音程・長さms・曲名・素材集）で、
  行数が多いときは仮想スクロールで描画します。見出しクリックで昇順／降順。
- ダブルクリックまたは Enter で試聴（WebAudio、その区間だけ）。
- 行を REAPER / エクスプローラーへドラッグ（44.1kHz / 24bit / モノラル WAV）。
- ★・非表示・別名・重複整理・元に戻す／やり直し（別名と印だけが対象）。
- 書き出し：WAV（パターン命名）／選択をWAV／CSV／UTAU音源／library.json／
  音素DBから取り込み。
- `元のフレーズを開く` は元WAVのパスと区間を表示（波形窓は段階2）。

## 確認用

```powershell
# サイドカーの JSON Lines 仕様（検索の一致、フラグ保持、書き出し一致など）
python build\pilot-test\test_sidecar.py

# 核に手を入れていないことの確認
python selftest.py

# 画面側の小さなロジック
cd gui-tauri
npm test
npm run build
```

`build/pilot-test/` は gitignore された作業用フォルダで、合成した素材集を作って
上記チェックに使います（リポジトリに音声は入りません）。

### 試作時点の実測（合成素材）

- 150素材集 × 30候補 = 4,500件：索引 約1.4秒、検索（全件）約100ms
  （サイドカー単体 約70ms＋IPC）。表のDOM行は常に約24行だけ。
- 3素材集 × 14候補：検索 約4〜7ms。
- 4,500件でも仮想スクロールで最下段まで移動でき、行数は増えないことを確認。
- OSドラッグの実ドロップ（REAPER／エクスプローラー）だけは、確認環境の前面に
  別アプリがあり自動操作できませんでした。`db.prepare_drag` が返すWAVが
  44.1kHz / 24bit / モノラルで、名前が発音名（`あ.wav`）になることまでは確認済みです。

## 現行 tkinter 版との違い（試作側のメモ）

- 「非表示も表示」は `db.search(hidden=false)`（非表示も含めて全部）として実装しました。
  現行 `DBWindow.refresh` は `hidden=True` を渡しており、「非表示だけを表示」になって
  います。仕様の `hidden（null=非表示を除く）` に合わせた意図的な差です。
- 並べ替えに `確認済み` を追加しました（`db._order_terms` の11種に合わせるため）。
- 別名は表にそのまま表示します（例：`あA3`）。`db.search` の `display` は音素から
  かなを引き直すため `あ` に戻ってしまいますが、`name != raw_name` のときは別名を
  優先しています。ドラッグ名・書き出し名は現行どおり `display`（`あ.wav`）です。
- 検索結果の件数・並びは、同じ条件で `db.search` と一致することを
  `build/pilot-test/test_sidecar.py` で確認しています。

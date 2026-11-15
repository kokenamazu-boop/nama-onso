import { useEffect, useRef, useState } from "react";
import { convertFileSrc } from "@tauri-apps/api/core";
import { Modal } from "../components/Modal";
import { WaveView, type WaveHandle } from "../components/WaveView";
import * as api from "../lib/api";
import { chooseWav } from "../lib/dialogs";
import { basename, errorText } from "../lib/format";
import {
  clampEnd,
  clampStart,
  cloneUnits,
  pendingCount,
  pitchText,
  requestedUnitIndex,
  UNDO_LIMIT,
  type Edge,
} from "../lib/wave";
import type {
  PitchData,
  Unit,
  WaveAlignResult,
  WaveFacts,
  WaveOpenRequest,
  WaveOpenResult,
  WavePeaks,
} from "../lib/types";

type WaveTabProps = {
  isActive: boolean;
  request: WaveOpenRequest | null;
  theme: "light" | "dark";
};

type Status = { text: string; error: boolean };

type OpenOptions = {
  bankPath?: string;
  loadBank?: boolean;
  start?: number;
  end?: number;
  unitId?: string;
};

export default function WaveTab({ isActive, request, theme }: WaveTabProps) {
  const [facts, setFacts] = useState<WaveFacts | null>(null);
  const [peaks, setPeaks] = useState<WavePeaks | null>(null);
  const [mediaUrl, setMediaUrl] = useState("");
  const [bankPath, setBankPath] = useState("");
  const [bankExists, setBankExists] = useState(false);
  const [units, setUnits] = useState<Unit[]>([]);
  const [pitches, setPitches] = useState<PitchData[]>([]);
  const [lyrics, setLyrics] = useState("");
  const [selected, setSelected] = useState(-1);
  const [edge, setEdge] = useState<Edge>("start");
  const [onlyPending, setOnlyPending] = useState(false);
  const [undoStack, setUndoStack] = useState<Unit[][]>([]);
  const [redoStack, setRedoStack] = useState<Unit[][]>([]);
  const [busy, setBusy] = useState<string | null>(null);
  const [progress, setProgress] = useState<string | null>(null);
  const [status, setStatus] = useState<Status>({ text: "", error: false });
  const [playing, setPlaying] = useState(false);
  const [playingIndex, setPlayingIndex] = useState<number | null>(null);
  const [looping, setLooping] = useState(false);
  const [renameOpen, setRenameOpen] = useState(false);
  const [renameValue, setRenameValue] = useState("");

  const waveRef = useRef<WaveHandle | null>(null);
  const unitsRef = useRef<Unit[]>(units);
  const selectedRef = useRef(selected);
  const edgeRef = useRef<Edge>(edge);
  const factsRef = useRef<WaveFacts | null>(facts);
  const lyricsRef = useRef(lyrics);
  const loopingRef = useRef(looping);
  const playingRef = useRef(playing);
  const busyRef = useRef<string | null>(busy);
  const dragPushed = useRef(false);
  const openSeq = useRef(0);
  const lastRequest = useRef(0);
  const listRef = useRef<HTMLDivElement | null>(null);

  unitsRef.current = units;
  selectedRef.current = selected;
  edgeRef.current = edge;
  factsRef.current = facts;
  lyricsRef.current = lyrics;
  loopingRef.current = looping;
  playingRef.current = playing;
  busyRef.current = busy;

  const working = busy !== null;
  const pending = pendingCount(units);
  const current = selected >= 0 ? units[selected] : null;
  const visible = units
    .map((_unit, index) => index)
    .filter((index) => !onlyPending || units[index].confidence !== "checked");
  const fileInfo = facts
    ? `${basename(facts.path)} : ${facts.seconds.toFixed(2)} 秒 / ${facts.sample_rate} Hz / ${facts.channels} ch`
    : "WAVを開いてください。";
  const statusText = busy ? `${busy}${progress ? ` ・ ${progress}` : ""}` : status.text;

  function say(text: string, error = false) {
    setStatus({ text, error });
  }

  function pushUndo() {
    setUndoStack((stack) => [...stack.slice(-(UNDO_LIMIT - 1)), cloneUnits(unitsRef.current)]);
    setRedoStack([]);
  }

  function stopPlayback() {
    waveRef.current?.stop();
    setPlaying(false);
    setPlayingIndex(null);
    if (loopingRef.current) setLooping(false);
  }

  async function openWav(path: string, options: OpenOptions = {}) {
    if (!path.trim()) return;
    const seq = openSeq.current + 1;
    openSeq.current = seq;
    stopPlayback();
    setBusy("WAVを読み込んでいます…");
    setProgress(null);
    try {
      await api.allowMediaPath(path);
      const url = convertFileSrc(path);
      const result = await api.call<WaveOpenResult>("wave.open", {
        path,
        bank_path: options.bankPath ?? "",
        load_bank: Boolean(options.loadBank),
      });
      if (seq !== openSeq.current) return;
      setFacts(result.facts);
      setPeaks(result.peaks);
      setMediaUrl(url);
      setBankPath(result.bank_path);
      setBankExists(result.bank_exists);
      setLyrics(result.lyrics ?? "");
      setUnits(result.units ?? []);
      setPitches(result.pitches ?? []);
      setUndoStack([]);
      setRedoStack([]);
      setOnlyPending(false);
      setEdge("start");
      setLooping(false);
      const wanted = result.bank_loaded
        ? requestedUnitIndex(result.units, {
            path,
            start: options.start,
            end: options.end,
            unit_id: options.unitId,
            token: 0,
          })
        : result.units.length
          ? 0
          : -1;
      setSelected(wanted);
      say(
        result.bank_loaded
          ? `素材集を読み込みました（${result.units.length} 候補）。`
          : `${basename(path)} を開きました。`,
      );
      if (options.start !== undefined) {
        const start = options.start;
        window.setTimeout(() => {
          if (seq === openSeq.current) waveRef.current?.seek(start);
        }, 500);
      }
    } catch (reason) {
      if (seq === openSeq.current) {
        say(`WAVを開けません: ${errorText(reason)}`, true);
      }
    } finally {
      if (seq === openSeq.current) {
        setBusy(null);
        setProgress(null);
      }
    }
  }

  async function pickWav() {
    const path = await chooseWav();
    if (path) await openWav(path);
  }

  // 起動引数（argv の WAV）
  useEffect(() => {
    void (async () => {
      try {
        const path = await api.getStartupWav();
        if (path) await openWav(path);
      } catch {
        // argv が無いだけなら何もしない
      }
    })();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // DBタブから「波形で開く」
  useEffect(() => {
    if (!request || request.token === lastRequest.current) return;
    lastRequest.current = request.token;
    void openWav(request.path, {
      bankPath: request.bank_path,
      loadBank: true,
      start: request.start,
      end: request.end,
      unitId: request.unit_id,
    });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [request]);

  // align の進捗（サイドカーはスレッドで動く）
  useEffect(() => {
    let unlisten: (() => void) | undefined;
    void api
      .onProgress((data) => {
        if (data.phase === "align") {
          setProgress("音素を推定中…");
        } else if (data.phase === "pitch" && data.total) {
          setProgress(`ピッチを計算中… ${data.index}/${data.total}`);
        }
      })
      .then((fn) => {
        unlisten = fn;
      });
    return () => unlisten?.();
  }, []);

  async function align() {
    if (!factsRef.current) {
      say("先にWAVを開いてください。", true);
      return;
    }
    const text = lyricsRef.current.trim();
    if (!text) {
      say("歌詞を入力してください。ファイル名でも構いません。", true);
      return;
    }
    stopPlayback();
    setBusy("音素を推定中...（初回は環境準備で時間がかかります）");
    setProgress(null);
    try {
      const result = await api.call<WaveAlignResult>("wave.align", { lyrics: text });
      setUnits(result.units);
      setPitches(result.pitches);
      setLyrics(text);
      setSelected(result.units.length ? 0 : -1);
      setEdge("start");
      setUndoStack([]);
      setRedoStack([]);
      setOnlyPending(false);
      waveRef.current?.showAll();
      let message = `${result.units.length}個の素材候補を作りました。`;
      if (result.warn) message += " " + result.warn;
      if (result.note) message += ` 無音の補正: ${result.note}。`;
      message += " 境界を確認して保存してください。";
      say(message);
    } catch (reason) {
      say(`推定に失敗しました。${errorText(reason)}`, true);
    } finally {
      setBusy(null);
      setProgress(null);
    }
  }

  async function saveBank() {
    if (!unitsRef.current.length || !factsRef.current) {
      say("保存するものがありません。先に音素を割り当ててください。", true);
      return;
    }
    setBusy("保存しています…");
    try {
      const result = await api.call<{ bank_path: string; pending: number }>("wave.save", {
        units: unitsRef.current,
        lyrics: lyricsRef.current,
      });
      setBankPath(result.bank_path);
      setBankExists(true);
      say(`保存しました: ${result.bank_path}（未確認 ${result.pending} 件）`);
    } catch (reason) {
      say(`保存失敗: ${errorText(reason)}`, true);
    } finally {
      setBusy(null);
    }
  }

  async function exportRpp() {
    if (!unitsRef.current.length) {
      say("先に音素を割り当ててください。", true);
      return;
    }
    setBusy("REAPER用プロジェクトを書き出しています…");
    try {
      const result = await api.call<{ path: string }>("wave.export_rpp", {
        units: unitsRef.current,
      });
      say(
        `REAPER用プロジェクトを書き出しました: ${result.path}` +
          "（候補はリージョン、音素の開始はマーカー）",
      );
    } catch (reason) {
      say(`書き出し失敗: ${errorText(reason)}`, true);
    } finally {
      setBusy(null);
    }
  }

  function nudge(ms: number) {
    const unit = unitsRef.current[selectedRef.current];
    const fact = factsRef.current;
    if (!unit || !fact) return;
    const dt = ms / 1000.0;
    if (edgeRef.current === "start") {
      const next = clampStart(unit, unit.start + dt, fact.seconds);
      if (Math.abs(next - unit.start) < 1e-9) return;
      pushUndo();
      const index = selectedRef.current;
      setUnits((prev) =>
        prev.map((item, i) => (i === index ? { ...item, start: next, confidence: "checked" } : item)),
      );
    } else {
      const next = clampEnd(unit, unit.end + dt, fact.seconds);
      if (Math.abs(next - unit.end) < 1e-9) return;
      pushUndo();
      const index = selectedRef.current;
      setUnits((prev) =>
        prev.map((item, i) => (i === index ? { ...item, end: next, confidence: "checked" } : item)),
      );
    }
  }

  function beginBoundaryDrag() {
    if (dragPushed.current) return;
    pushUndo();
    dragPushed.current = true;
  }

  function endBoundaryDrag() {
    dragPushed.current = false;
  }

  function moveBoundary(index: number, side: Edge, value: number) {
    setUnits((prev) =>
      prev.map((unit, i) => (i === index ? { ...unit, [side]: value, confidence: "checked" } : unit)),
    );
  }

  function undo() {
    if (!undoStack.length) {
      say("戻せる操作がありません。");
      return;
    }
    const previous = undoStack[undoStack.length - 1];
    setRedoStack((stack) => [...stack.slice(-(UNDO_LIMIT - 1)), cloneUnits(unitsRef.current)]);
    setUndoStack((stack) => stack.slice(0, -1));
    setUnits(previous);
    setSelected((index) => (index >= 0 && index < previous.length ? index : -1));
    say("1つ前に戻しました（Ctrl+Y でやり直し）。");
  }

  function redo() {
    if (!redoStack.length) {
      say("やり直せる操作がありません。");
      return;
    }
    const next = redoStack[redoStack.length - 1];
    setUndoStack((stack) => [...stack.slice(-(UNDO_LIMIT - 1)), cloneUnits(unitsRef.current)]);
    setRedoStack((stack) => stack.slice(0, -1));
    setUnits(next);
    setSelected((index) => (index >= 0 && index < next.length ? index : -1));
    say("やり直しました。");
  }

  function openRename() {
    const unit = current;
    if (!unit) return;
    setRenameValue(unit.name);
    setRenameOpen(true);
  }

  function commitRename() {
    const unit = units[selected];
    const name = renameValue.trim();
    if (!unit || !name || name === unit.name) {
      setRenameOpen(false);
      return;
    }
    pushUndo();
    setUnits((prev) =>
      prev.map((item, i) => (i === selected ? { ...item, name, confidence: "checked" } : item)),
    );
    setRenameOpen(false);
  }

  function markChecked() {
    const unit = units[selected];
    if (!unit || unit.confidence === "checked") return;
    pushUndo();
    setUnits((prev) =>
      prev.map((item, i) => (i === selected ? { ...item, confidence: "checked" } : item)),
    );
  }

  function selectUnit(index: number, side?: Edge) {
    setSelected(index);
    if (side) setEdge(side);
  }

  function playSelected() {
    const unit = units[selectedRef.current];
    if (!unit) {
      say("先に候補を選んでください。", true);
      return;
    }
    setPlayingIndex(selectedRef.current);
    void waveRef.current?.playRange(unit.start, unit.end, loopingRef.current);
  }

  function togglePlay() {
    if (playingRef.current) stopPlayback();
    else playSelected();
  }

  function toggleLoop() {
    if (loopingRef.current) {
      setLooping(false);
      stopPlayback();
      return;
    }
    const unit = units[selectedRef.current];
    if (!unit) {
      say("先に候補を選んでください。", true);
      return;
    }
    setLooping(true);
    setPlayingIndex(selectedRef.current);
    void waveRef.current?.playRange(unit.start, unit.end, true);
  }

  const undoRef = useRef(undo);
  const redoRef = useRef(redo);
  const nudgeRef = useRef(nudge);
  const playRef = useRef(togglePlay);
  undoRef.current = undo;
  redoRef.current = redo;
  nudgeRef.current = nudge;
  playRef.current = togglePlay;

  useEffect(() => {
    if (!isActive) return;
    const onKey = (event: KeyboardEvent) => {
      const target = event.target as HTMLElement | null;
      if (target?.closest("input, textarea, select, [contenteditable='true']")) return;
      const key = event.key.toLowerCase();
      if ((event.ctrlKey || event.metaKey) && key === "z" && !event.shiftKey) {
        event.preventDefault();
        undoRef.current();
        return;
      }
      if ((event.ctrlKey || event.metaKey) && (key === "y" || (event.shiftKey && key === "z"))) {
        event.preventDefault();
        redoRef.current();
        return;
      }
      if (event.key === " " && !event.ctrlKey && !event.metaKey) {
        const button = target?.closest("button");
        if (button && !button.classList.contains("unit-row")) return;
        event.preventDefault();
        playRef.current();
        return;
      }
      if (event.key === "ArrowLeft" || event.key === "ArrowRight") {
        event.preventDefault();
        const amount = event.shiftKey ? 50 : event.altKey ? 1 : 5;
        nudgeRef.current(event.key === "ArrowLeft" ? -amount : amount);
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [isActive]);

  useEffect(() => {
    const element = listRef.current?.querySelector(`[data-unit="${selected}"]`);
    if (element) element.scrollIntoView({ block: "nearest" });
  }, [selected]);

  return (
    <div className="tab-pane wave-tab">
      <header className="wave-top">
        <button className="btn" onClick={() => void pickWav()} disabled={working}>
          WAVを開く
        </button>
        <span className="wave-file" title={facts?.path ?? ""}>
          {facts ? basename(facts.path) : "WAV未選択"}
        </span>
        <label className="field wave-lyrics">
          <span>歌詞</span>
          <input
            value={lyrics}
            onChange={(event) => setLyrics(event.target.value)}
            onKeyDown={(event) => {
              if (event.key === "Enter") {
                event.preventDefault();
                void align();
              }
            }}
            placeholder="歌詞（ファイル名でも可）"
            spellCheck={false}
          />
        </label>
        <button
          className="btn primary"
          onClick={() => void align()}
          disabled={!facts || working || !lyrics.trim()}
        >
          自動で割り当て
        </button>
        <button
          className="btn"
          onClick={() => void saveBank()}
          disabled={!units.length || working}
        >
          素材集を保存
        </button>
        <span className="top-spacer" />
        {bankExists ? (
          <span className="db-pill" title={bankPath}>
            素材集あり
          </span>
        ) : null}
      </header>

      <div className={`wave-status${status.error ? " error" : ""}`}>
        {busy ? <span className="spinner" aria-hidden="true" /> : null}
        <span className="wave-file-info">{fileInfo}</span>
        <span className="wave-message">{statusText}</span>
      </div>

      <div className="wave-main">
        <div className="wave-panel">
          {facts && peaks && mediaUrl ? (
            <WaveView
              ref={waveRef}
              facts={facts}
              peaks={peaks}
              mediaUrl={mediaUrl}
              units={units}
              selected={selected}
              edge={edge}
              theme={theme}
              onSelect={selectUnit}
              onEdge={setEdge}
              onBeginDrag={beginBoundaryDrag}
              onMoveBoundary={moveBoundary}
              onEndDrag={endBoundaryDrag}
              onPlayState={(value) => {
                setPlaying(value);
              }}
              onFinish={() => {
                setPlaying(false);
                setPlayingIndex(null);
              }}
              onError={(message) => say(message, true)}
            />
          ) : (
            <div className="wave-placeholder">
              <p>WAVを開いてください。</p>
              <p className="muted">開く → 歌詞 → 自動で割り当て → 境界を直す → 素材集を保存</p>
            </div>
          )}
        </div>

        <div className="wave-bottom">
          <section className="unit-list-wrap">
            <div className="unit-head">
              <span>印</span>
              <span>#</span>
              <span>名前</span>
              <span>開始-終了</span>
              <span>長さms</span>
              <span>Hz</span>
            </div>
            <div className="unit-list" ref={listRef}>
              {visible.length === 0 ? (
                <div className="unit-empty">
                  {units.length === 0
                    ? "歌詞を入力して「自動で割り当て」を押してください。"
                    : "要確認の候補はありません。"}
                </div>
              ) : (
                visible.map((index) => {
                  const unit = units[index];
                  const checked = unit.confidence === "checked";
                  const classes = [
                    "unit-row",
                    index === selected ? "selected" : "",
                    index === playingIndex ? "playing" : "",
                    checked ? "checked" : "pending",
                  ]
                    .filter(Boolean)
                    .join(" ");
                  return (
                    <button
                      type="button"
                      key={index}
                      data-unit={index}
                      className={classes}
                      onClick={() => selectUnit(index)}
                    >
                      <span className="unit-mark">{checked ? "✓" : "!"}</span>
                      <span className="unit-number">{index + 1}</span>
                      <span className="unit-name">{unit.name}</span>
                      <span className="unit-range">
                        {unit.start.toFixed(3)}-{unit.end.toFixed(3)}
                      </span>
                      <span className="unit-length">
                        {Math.round((unit.end - unit.start) * 1000)}
                      </span>
                      <span className="unit-hz">{pitchText(pitches, index)}</span>
                    </button>
                  );
                })
              )}
            </div>
          </section>

          <aside className="wave-side">
            <div className="button-pair">
              <button className="btn" onClick={togglePlay} disabled={!current || working}>
                ▶ 再生
              </button>
              <button className="btn" onClick={stopPlayback} disabled={!playing && playingIndex === null}>
                ■ 停止
              </button>
            </div>
            <button
              className={`btn${looping ? " primary" : ""}`}
              onClick={toggleLoop}
              disabled={!current || working}
            >
              {looping ? "ループ停止" : "ループ試聴"}
            </button>

            <div className="side-label">動かす側</div>
            <label className="check">
              <input
                type="radio"
                name="wave-edge"
                checked={edge === "start"}
                onChange={() => setEdge("start")}
              />
              開始
            </label>
            <label className="check">
              <input
                type="radio"
                name="wave-edge"
                checked={edge === "end"}
                onChange={() => setEdge("end")}
              />
              終了
            </label>

            <div className="side-label">境界を動かす (ms)</div>
            <div className="nudge-grid">
              {[-50, -10, -5, 5, 10, 50].map((ms) => (
                <button
                  key={ms}
                  className="btn small"
                  onClick={() => nudge(ms)}
                  disabled={!current || working}
                >
                  {ms > 0 ? `+${ms}` : ms}
                </button>
              ))}
            </div>

            <button className="btn" onClick={openRename} disabled={!current || working}>
              名前を変更
            </button>
            <button className="btn" onClick={markChecked} disabled={!current || working}>
              この範囲を確認済みにする
            </button>
            <label className="check">
              <input
                type="checkbox"
                checked={onlyPending}
                onChange={(event) => setOnlyPending(event.target.checked)}
              />
              要確認だけ表示
            </label>
            <div className="pending-count">要確認 {pending} 件 / 全 {units.length} 件</div>
            <button className="btn" onClick={() => waveRef.current?.showAll()} disabled={!facts}>
              全体表示
            </button>
            <button
              className="btn"
              onClick={() => void exportRpp()}
              disabled={!units.length || working}
            >
              REAPER用プロジェクトを書き出し
            </button>
            <div className="key-hint">
              ←/→: ±5 ms
              <br />
              Shift+←/→: ±50 ms
              <br />
              Alt+←/→: ±1 ms
              <br />
              Space: 再生/停止
            </div>
            <div className="button-pair">
              <button className="btn" onClick={undo} disabled={!undoStack.length || working}>
                元に戻す
              </button>
              <button className="btn" onClick={redo} disabled={!redoStack.length || working}>
                やり直し
              </button>
            </div>
          </aside>
        </div>
      </div>

      {renameOpen ? (
        <Modal
          title="名前"
          onClose={() => setRenameOpen(false)}
          footer={
            <>
              <button className="btn" onClick={() => setRenameOpen(false)}>
                キャンセル
              </button>
              <button className="btn primary" onClick={commitRename}>
                変更
              </button>
            </>
          }
        >
          <div className="form-row">
            <span className="form-label">素材名</span>
            <input
              autoFocus
              value={renameValue}
              onChange={(event) => setRenameValue(event.target.value)}
              onKeyDown={(event) => {
                if (event.key === "Enter") {
                  event.preventDefault();
                  commitRename();
                }
              }}
              onFocus={(event) => event.currentTarget.select()}
            />
          </div>
          <p className="note">変更すると確認済みになります。元のWAVは変更しません。</p>
        </Modal>
      ) : null}
    </div>
  );
}

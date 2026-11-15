import { useEffect, useRef, useState, type MouseEvent as ReactMouseEvent } from "react";
import { startDrag } from "@crabnebula/tauri-plugin-drag";
import { Table } from "../components/Table";
import { ExportMenu } from "../components/ExportMenu";
import {
  DuplicatesDialog,
  ImportDialog,
  RenameDialog,
  SourceDialog,
  UtauDialog,
  WavDialog,
  type UtauOptions,
} from "../components/Dialogs";
import * as api from "../lib/api";
import { chooseFolder, chooseSave } from "../lib/dialogs";
import { basename, errorText, joinPath, rowKey, rowLabel, sortLabel } from "../lib/format";
import { buildSearchParams, COLUMN_SORT, EMPTY_FILTERS, type Filters } from "../lib/search";
import { flagsOf, groupFlagChanges } from "../lib/undo";
import type {
  BuildResult,
  DuplicateGroup,
  FlagChange,
  ImportResult,
  OpenResult,
  RenameChange,
  Row,
  SearchResult,
  SourceInfo,
  UndoEntry,
  UtauResult,
  WaveOpenRequest,
} from "../lib/types";

const FOLDER_KEY = "nama.onso.dbpilot.folder";

type ModalState =
  | { kind: "rename"; initial: string; count: number }
  | { kind: "wav" }
  | { kind: "utau" }
  | { kind: "duplicates" }
  | { kind: "source"; info: SourceInfo }
  | { kind: "import" }
  | null;

type DbTabProps = {
  isActive: boolean;
  onOpenWave: (request: WaveOpenRequest) => void;
};

function refsOf(rows: Row[]) {
  return rows.map((row) => ({ bank_path: row.bank_path, unit_id: row.unit_id }));
}

export default function DbTab({ isActive, onOpenWave }: DbTabProps) {
  const [folder, setFolder] = useState("");
  const [dbInfo, setDbInfo] = useState<OpenResult | null>(null);
  const [filters, setFilters] = useState<Filters>(EMPTY_FILTERS);
  const [sortKey, setSortKey] = useState("name");
  const [descending, setDescending] = useState(false);
  const [rows, setRows] = useState<Row[]>([]);
  const [selectedKeys, setSelectedKeys] = useState<Set<string>>(new Set());
  const [activeKey, setActiveKey] = useState<string | null>(null);
  const [anchorIndex, setAnchorIndex] = useState<number | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [progress, setProgress] = useState<string | null>(null);
  const [status, setStatus] = useState<{ text: string; error: boolean }>({
    text: "フォルダを選び「索引を作る」を押してください。",
    error: false,
  });
  const [searchMs, setSearchMs] = useState<number | null>(null);
  const [searchSeq, setSearchSeq] = useState(0);
  const [playingKey, setPlayingKey] = useState<string | null>(null);
  const [dragIcon, setDragIcon] = useState("");
  const [undoStack, setUndoStack] = useState<UndoEntry[]>([]);
  const [redoStack, setRedoStack] = useState<UndoEntry[]>([]);
  const [modal, setModal] = useState<ModalState>(null);
  const [duplicateGroups, setDuplicateGroups] = useState<DuplicateGroup[]>([]);

  const audioContext = useRef<AudioContext | null>(null);
  const audioSource = useRef<AudioBufferSourceNode | null>(null);
  const previewToken = useRef(0);

  const working = busy !== null;

  function say(text: string, error = false) {
    setStatus({ text, error });
  }

  function ensureDb(): boolean {
    if (!dbInfo?.exists) {
      say("索引がありません。フォルダを選び「索引を作る」を押してください。", true);
      return false;
    }
    return true;
  }

  async function runSearch(next?: { filters?: Filters; sort?: string; descending?: boolean }) {
    const activeFilters = next?.filters ?? filters;
    const activeSort = next?.sort ?? sortKey;
    const activeDesc = next?.descending ?? descending;
    const started = performance.now();
    try {
      const result = await api.call<SearchResult>(
        "db.search",
        buildSearchParams(activeFilters, activeSort, activeDesc),
      );
      const elapsed = performance.now() - started;
      setRows(result.rows);
      setSelectedKeys(new Set());
      setActiveKey(null);
      setAnchorIndex(null);
      setSearchMs(elapsed);
      setSearchSeq((value) => value + 1);
      say(`${result.rows.length} 件 ・ 並び: ${sortLabel(activeSort)} ${activeDesc ? "▼" : "▲"}`);
    } catch (reason) {
      say(`検索に失敗: ${errorText(reason)}`, true);
    }
  }

  async function openFolder(path: string): Promise<boolean> {
    const target = path.trim();
    if (!target) {
      say("素材集フォルダを指定してください。", true);
      return false;
    }
    setBusy("索引を開いています…");
    try {
      const info = await api.call<OpenResult>("db.open", { folder: target });
      setDbInfo(info);
      setFolder(info.folder);
      localStorage.setItem(FOLDER_KEY, info.folder);
      setUndoStack([]);
      setRedoStack([]);
      if (info.exists) {
        await runSearch();
      } else {
        setRows([]);
        setSelectedKeys(new Set());
        setActiveKey(null);
        setSearchMs(null);
        say("索引がまだありません。「索引を作る」を押してください。");
      }
      return true;
    } catch (reason) {
      setDbInfo(null);
      say(`索引を開けません: ${errorText(reason)}`, true);
      return false;
    } finally {
      setBusy(null);
    }
  }

  async function pickFolder() {
    const picked = await chooseFolder("素材集JSONのフォルダ");
    if (picked) {
      setFolder(picked);
      await openFolder(picked);
    }
  }

  async function buildIndex() {
    if (!folder.trim()) {
      say("先に素材集フォルダを選んでください。", true);
      return;
    }
    setBusy("索引を作成中…");
    setProgress(null);
    try {
      const info = await api.call<BuildResult>("db.build", { folders: [folder.trim()] });
      setDbInfo({
        db_path: info.db_path,
        folder: info.folder,
        exists: true,
        banks: info.banks_total,
        units: info.units_total,
      });
      setFolder(info.folder);
      localStorage.setItem(FOLDER_KEY, info.folder);
      setUndoStack([]);
      setRedoStack([]);
      await runSearch();
      say(
        `索引を更新しました（追加 ${info.banks} / スキップ ${info.skipped} / ` +
          `削除 ${info.removed}、候補 ${info.units}）`,
      );
    } catch (reason) {
      say(`索引の作成に失敗: ${errorText(reason)}`, true);
    } finally {
      setBusy(null);
      setProgress(null);
    }
  }

  function targetRows(): Row[] {
    const picked = rows.filter((row) => selectedKeys.has(rowKey(row)));
    if (picked.length) return picked;
    const active = activeKey ? rows.find((row) => rowKey(row) === activeKey) : null;
    return active ? [active] : [];
  }

  function handleRowClick(row: Row, index: number, event: ReactMouseEvent) {
    const key = rowKey(row);
    if (event.shiftKey && anchorIndex !== null) {
      const start = Math.min(anchorIndex, index);
      const end = Math.max(anchorIndex, index);
      const next = new Set(event.ctrlKey || event.metaKey ? selectedKeys : []);
      for (let i = start; i <= end; i += 1) next.add(rowKey(rows[i]));
      setSelectedKeys(next);
      setActiveKey(key);
      return;
    }
    if (event.ctrlKey || event.metaKey) {
      const next = new Set(selectedKeys);
      if (next.has(key)) next.delete(key);
      else next.add(key);
      setSelectedKeys(next);
      setActiveKey(key);
      setAnchorIndex(index);
      return;
    }
    setSelectedKeys(new Set([key]));
    setActiveKey(key);
    setAnchorIndex(index);
  }

  function handleKeyboardSelect(row: Row) {
    setSelectedKeys(new Set([rowKey(row)]));
    setActiveKey(rowKey(row));
    setAnchorIndex(rows.findIndex((item) => rowKey(item) === rowKey(row)));
  }

  function patchFilters(patch: Partial<Filters>, searchNow = true) {
    const next = { ...filters, ...patch };
    setFilters(next);
    if (searchNow && dbInfo?.exists) void runSearch({ filters: next });
  }

  function chooseSort(key: string) {
    const nextDesc = key === sortKey ? descending : false;
    setSortKey(key);
    setDescending(nextDesc);
    if (dbInfo?.exists) void runSearch({ sort: key, descending: nextDesc });
  }

  function sortByColumn(column: string) {
    const key = COLUMN_SORT[column] ?? "name";
    const nextDesc = key === sortKey ? !descending : false;
    setSortKey(key);
    setDescending(nextDesc);
    if (dbInfo?.exists) void runSearch({ sort: key, descending: nextDesc });
  }

  // ------------------------------------------------------------- preview
  function stopPreview() {
    previewToken.current += 1;
    const source = audioSource.current;
    audioSource.current = null;
    if (source) {
      source.onended = null;
      try {
        source.stop();
      } catch {
        // already stopped
      }
    }
    setPlayingKey(null);
  }

  async function playRow(row: Row) {
    const key = rowKey(row);
    if (playingKey === key) {
      stopPreview();
      return;
    }
    stopPreview();
    const token = previewToken.current;
    try {
      const clip = await api.call<{ path: string }>("db.preview", {
        bank_path: row.bank_path,
        unit_id: row.unit_id,
      });
      if (token !== previewToken.current) return;
      const bytes = await api.readFileBytes(clip.path);
      if (token !== previewToken.current) return;
      const context = audioContext.current ?? new AudioContext();
      audioContext.current = context;
      if (context.state === "suspended") await context.resume();
      const buffer = await context.decodeAudioData(bytes);
      if (token !== previewToken.current) return;
      const source = context.createBufferSource();
      source.buffer = buffer;
      source.connect(context.destination);
      source.onended = () => {
        if (audioSource.current === source) {
          audioSource.current = null;
          setPlayingKey(null);
        }
      };
      audioSource.current = source;
      setPlayingKey(key);
      source.start();
    } catch (reason) {
      say(`試聴に失敗: ${errorText(reason)}`, true);
    }
  }

  // --------------------------------------------------------------- undo
  function pushUndo(entry: UndoEntry) {
    setUndoStack((stack) => [...stack.slice(-49), entry]);
    setRedoStack([]);
  }

  async function applyUndo(entry: UndoEntry, forward: boolean) {
    if (entry.kind === "rename") {
      for (const item of entry.items) {
        await api.call("db.rename", {
          bank_path: item.bank_path,
          unit_id: item.unit_id,
          name: forward ? item.new : item.old,
        });
      }
      return;
    }
    const groups = groupFlagChanges(entry.items, forward ? "new" : "old");
    for (const group of groups) {
      await api.call("db.set_flags", {
        rows: group.refs,
        favorite: Boolean(group.state.favorite),
        hidden: Boolean(group.state.hidden),
        tags: group.state.tags,
      });
    }
  }

  async function doUndo() {
    if (working) return;
    if (!undoStack.length) {
      say("戻せる操作がありません。");
      return;
    }
    if (!ensureDb()) return;
    const entry = undoStack[undoStack.length - 1];
    setBusy("元に戻しています…");
    try {
      await applyUndo(entry, false);
      setUndoStack((stack) => stack.slice(0, -1));
      setRedoStack((stack) => [...stack.slice(-49), entry]);
      await runSearch();
      say(`「${entry.label}」を元に戻しました（Ctrl+Y でやり直し）。`);
    } catch (reason) {
      say(`元に戻せません: ${errorText(reason)}`, true);
    } finally {
      setBusy(null);
    }
  }

  async function doRedo() {
    if (working) return;
    if (!redoStack.length) {
      say("やり直せる操作がありません。");
      return;
    }
    if (!ensureDb()) return;
    const entry = redoStack[redoStack.length - 1];
    setBusy("やり直しています…");
    try {
      await applyUndo(entry, true);
      setRedoStack((stack) => stack.slice(0, -1));
      setUndoStack((stack) => [...stack.slice(-49), entry]);
      await runSearch();
      say(`「${entry.label}」をやり直しました。`);
    } catch (reason) {
      say(`やり直せません: ${errorText(reason)}`, true);
    } finally {
      setBusy(null);
    }
  }

  // ------------------------------------------------------------ actions
  function openRename() {
    const picked = targetRows();
    if (!picked.length) {
      say("先に候補を選んでください。", true);
      return;
    }
    setModal({ kind: "rename", initial: rowLabel(picked[0]), count: picked.length });
  }

  async function applyRename(name: string) {
    const picked = targetRows();
    if (!picked.length) throw new Error("先に候補を選んでください。");
    const items: RenameChange[] = [];
    try {
      for (const row of picked) {
        const result = await api.call<{ old: string }>("db.rename", {
          bank_path: row.bank_path,
          unit_id: row.unit_id,
          name,
        });
        items.push({ bank_path: row.bank_path, unit_id: row.unit_id, old: result.old, new: name });
      }
    } catch (reason) {
      if (items.length) pushUndo({ kind: "rename", label: "名前を変更", items });
      throw reason;
    }
    pushUndo({ kind: "rename", label: "名前を変更", items });
    await runSearch();
    say(`${picked.length} 件を「${name}」にしました（元の素材集は変更していません）。`);
  }

  async function toggleFavorite() {
    const picked = targetRows();
    if (!picked.length) {
      say("先に候補を選んでください。", true);
      return;
    }
    const next = !picked.every((row) => row.favorite === 1);
    const items: FlagChange[] = picked.map((row) => {
      const old = flagsOf(row);
      return {
        bank_path: row.bank_path,
        unit_id: row.unit_id,
        old,
        new: { ...old, favorite: next ? 1 : 0 },
      };
    });
    try {
      await api.call("db.set_flags", { rows: refsOf(picked), favorite: next });
      pushUndo({ kind: "flags", label: "★切替", items });
      await runSearch();
      say(`${picked.length} 件を${next ? "★に" : "★から外"}しました。`);
    } catch (reason) {
      say(`★を変更できません: ${errorText(reason)}`, true);
    }
  }

  async function toggleHidden() {
    const picked = targetRows();
    if (!picked.length) {
      say("先に候補を選んでください。", true);
      return;
    }
    const next = !picked.every((row) => row.hidden === 1);
    const items: FlagChange[] = picked.map((row) => {
      const old = flagsOf(row);
      return {
        bank_path: row.bank_path,
        unit_id: row.unit_id,
        old,
        new: { ...old, hidden: next ? 1 : 0 },
      };
    });
    try {
      await api.call("db.set_flags", { rows: refsOf(picked), hidden: next });
      pushUndo({ kind: "flags", label: "非表示/戻す", items });
      await runSearch();
      say(
        `${picked.length} 件を${next ? "非表示に" : "表示に戻"}しました。` +
          "（元の素材集は変更していません）",
      );
    } catch (reason) {
      say(`非表示を変更できません: ${errorText(reason)}`, true);
    }
  }

  async function openDuplicates() {
    if (!ensureDb()) return;
    setBusy("重複を探しています…");
    try {
      const result = await api.call<{ groups: DuplicateGroup[] }>("db.duplicates", { cents: 25 });
      if (!result.groups.length) {
        say("近い重複は見つかりませんでした。");
        return;
      }
      setDuplicateGroups(result.groups);
      setModal({ kind: "duplicates" });
    } catch (reason) {
      say(`重複の検索に失敗: ${errorText(reason)}`, true);
    } finally {
      setBusy(null);
    }
  }

  async function hideDuplicates() {
    const changes: FlagChange[] = [];
    for (const group of duplicateGroups) {
      for (const row of group.drop) {
        const old = flagsOf(row);
        changes.push({
          bank_path: row.bank_path,
          unit_id: row.unit_id,
          old,
          new: { ...old, hidden: 1 },
        });
      }
    }
    setBusy("重複を整理しています…");
    try {
      const result = await api.call<{ hidden: number }>("db.hide_duplicates", {});
      if (changes.length) pushUndo({ kind: "flags", label: "重複を整理", items: changes });
      await runSearch();
      say(
        `${result.hidden} 件を非表示にしました（「非表示も表示」か Ctrl+Z で戻せます）。`,
      );
    } catch (reason) {
      say(`重複の整理に失敗: ${errorText(reason)}`, true);
      throw reason;
    } finally {
      setBusy(null);
    }
  }

  function openInWave() {
    const row = targetRows()[0];
    if (!row) {
      say("先に候補を選んでください。", true);
      return;
    }
    if (!row.source) {
      say("元WAVのパスがありません。", true);
      return;
    }
    onOpenWave({
      path: row.source,
      bank_path: row.bank_path,
      start: row.start,
      end: row.end,
      unit_id: row.unit_id,
      token: Date.now(),
    });
  }

  async function openSource() {
    const row = targetRows()[0];
    if (!row) {
      say("先に候補を選んでください。", true);
      return;
    }
    setBusy("元のフレーズを調べています…");
    try {
      const info = await api.call<SourceInfo>("db.source", {
        bank_path: row.bank_path,
        unit_id: row.unit_id,
      });
      setModal({ kind: "source", info });
    } catch (reason) {
      say(`元のフレーズを開けません: ${errorText(reason)}`, true);
    } finally {
      setBusy(null);
    }
  }

  // ------------------------------------------------------------- export
  async function exportWavs(outDir: string, pattern: string) {
    setBusy("WAVを書き出しています…");
    try {
      const result = await api.call<{ written: number }>("db.export_wavs", {
        out_dir: outDir,
        pattern,
      });
      say(
        `${result.written} 件のWAVを書き出しました（44.1kHz/24bit/モノラル）。` +
          "元の音声は変更していません。",
      );
    } catch (reason) {
      say(`書き出しに失敗: ${errorText(reason)}`, true);
      throw reason;
    } finally {
      setBusy(null);
    }
  }

  async function exportSelectedWav() {
    const picked = targetRows();
    if (!picked.length) {
      say("先に候補を選んでください。", true);
      return;
    }
    const outDir = await chooseFolder("選択した候補の書き出し先");
    if (!outDir) return;
    setBusy("WAVを書き出しています…");
    try {
      const result = await api.call<{ written: number }>("db.export_wavs", {
        out_dir: outDir,
        selected: refsOf(picked),
      });
      say(`${result.written} 件を書き出しました（44.1kHz/24bit/モノラル）。`);
    } catch (reason) {
      say(`書き出しに失敗: ${errorText(reason)}`, true);
    } finally {
      setBusy(null);
    }
  }

  async function exportCsv() {
    if (!ensureDb()) return;
    const outPath = await chooseSave("候補一覧をCSVで保存", joinPath(folder, "phonemes.csv"), [
      { name: "CSV", extensions: ["csv"] },
    ]);
    if (!outPath) return;
    setBusy("CSVを書き出しています…");
    try {
      const result = await api.call<{ written: number }>("db.export_csv", { out_path: outPath });
      say(`${result.written} 件をCSVへ書き出しました: ${outPath}`);
    } catch (reason) {
      say(`CSV書き出しに失敗: ${errorText(reason)}`, true);
    } finally {
      setBusy(null);
    }
  }

  async function exportUtau(options: UtauOptions) {
    setBusy("UTAU音源を書き出しています…");
    try {
      const info = await api.call<UtauResult>("db.export_utau", options);
      const short = info.min_ms > 0 ? ` / 短い候補 ${info.too_short}` : "";
      say(
        `UTAU: WAV ${info.wav} / oto ${info.oto}（VCV ${info.vcv}） / ` +
          `スキップ ${info.skipped}${short}`,
      );
    } catch (reason) {
      say(`UTAU書き出しに失敗: ${errorText(reason)}`, true);
      throw reason;
    } finally {
      setBusy(null);
    }
  }

  async function exportLibrary() {
    if (!ensureDb()) return;
    const outPath = await chooseSave("library.json を保存", joinPath(folder, "library.json"), [
      { name: "JSON", extensions: ["json"] },
    ]);
    if (!outPath) return;
    setBusy("library.json を書き出しています…");
    try {
      const result = await api.call<{ path: string }>("db.export_library", { out_path: outPath });
      say(`書き出しました: ${result.path}`);
    } catch (reason) {
      say(`書き出しに失敗: ${errorText(reason)}`, true);
    } finally {
      setBusy(null);
    }
  }

  async function runImport(source: string, outDir: string, song: string): Promise<ImportResult> {
    setBusy("音素DBを取り込んでいます…");
    try {
      const result = await api.call<ImportResult>("db.import_fonemudb", {
        folder: source,
        out_dir: outDir,
        song,
      });
      say(
        `取り込み: ${result.written} 件（読み飛ばし ${result.skipped}）。` +
          "この後、出力先を選んで索引を作り直してください。",
      );
      return result;
    } catch (reason) {
      say(`取り込みに失敗: ${errorText(reason)}`, true);
      throw reason;
    } finally {
      setBusy(null);
    }
  }

  // --------------------------------------------------------------- drag
  async function handleDragRows(dragged: Row[]) {
    if (!dragged.length) return;
    setBusy(`${dragged.length} 件のドラッグを準備しています…`);
    try {
      const paths: string[] = [];
      for (const row of dragged) {
        const result = await api.call<{ path: string }>("db.prepare_drag", {
          bank_path: row.bank_path,
          unit_id: row.unit_id,
        });
        paths.push(result.path);
      }
      if (!dragIcon) throw new Error("ドラッグ用アイコンが見つかりません。");
      setBusy(null);
      say(`${paths.length} 件をドラッグ中（元の音声は変更していません）。`);
      await startDrag({ item: paths, icon: dragIcon, mode: "copy" });
      say("ドラッグを終了しました。");
    } catch (reason) {
      say(`ドラッグできません: ${errorText(reason)}`, true);
    } finally {
      setBusy(null);
    }
  }

  // ---------------------------------------------------------- lifecycle
  const undoRef = useRef(doUndo);
  const redoRef = useRef(doRedo);
  undoRef.current = doUndo;
  redoRef.current = doRedo;

  useEffect(() => {
    if (!isActive) return;
    const onKey = (event: KeyboardEvent) => {
      const target = event.target as HTMLElement | null;
      if (target?.closest("input, textarea, select, [contenteditable='true']")) return;
      if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === "z" && !event.shiftKey) {
        event.preventDefault();
        void undoRef.current();
      } else if (
        (event.ctrlKey || event.metaKey) &&
        (event.key.toLowerCase() === "y" || (event.shiftKey && event.key.toLowerCase() === "z"))
      ) {
        event.preventDefault();
        void redoRef.current();
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [isActive]);

  useEffect(() => {
    let disposed = false;
    let unlistenProgress: (() => void) | undefined;
    let unlistenStatus: (() => void) | undefined;

    void (async () => {
      try {
        unlistenProgress = await api.onProgress((data) => {
          if (disposed || data.phase !== "index") return;
          const where = data.folder ? ` ${basename(data.folder)}` : "";
          setProgress(`索引を作成中… ${data.index ?? 1}/${data.total ?? 1}${where}`);
        });
        unlistenStatus = await api.onSidecarStatus((sidecar) => {
          if (!disposed && !sidecar.running && sidecar.error) {
            say(`サイドカー: ${sidecar.error}`, true);
          }
        });
        const paths = await api.getPilotPaths();
        if (disposed) return;
        setDragIcon(paths.dragIcon);
        const saved = localStorage.getItem(FOLDER_KEY) || paths.repoRoot;
        setFolder(saved);
        const ok = await openFolder(saved);
        if (!ok && saved !== paths.repoRoot) {
          setFolder(paths.repoRoot);
          await openFolder(paths.repoRoot);
        }
      } catch (reason) {
        if (!disposed) say(`起動に失敗: ${errorText(reason)}`, true);
      }
    })();

    return () => {
      disposed = true;
      unlistenProgress?.();
      unlistenStatus?.();
    };
  }, []);

  useEffect(() => {
    if (playingKey && !rows.some((row) => rowKey(row) === playingKey)) stopPreview();
    // stopPreview only touches refs and setState; rows/playingKey are the triggers.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [rows, playingKey]);

  useEffect(
    () => () => {
      const source = audioSource.current;
      if (source) {
        source.onended = null;
        try {
          source.stop();
        } catch {
          // already stopped
        }
      }
      void audioContext.current?.close();
    },
    [],
  );

  const active = targetRows()[0] ?? null;
  const hasTarget = active !== null;
  const emptyText = !dbInfo
    ? "フォルダを選び「索引を作る」を押してください。"
    : !dbInfo.exists
      ? "索引がまだありません。「索引を作る」を押してください。"
      : "条件に合う候補がありません。";
  const statusText = busy ? `${busy}${progress ? ` ・ ${progress}` : ""}` : status.text;

  return (
    <div className="tab-pane db-tab">
      <header className="topbar">
        <label className="folder-label" htmlFor="folder-input">
          素材集フォルダ
        </label>
        <input
          id="folder-input"
          className="folder-input"
          value={folder}
          onChange={(event) => setFolder(event.target.value)}
          onKeyDown={(event) => {
            if (event.key === "Enter") {
              event.preventDefault();
              void openFolder(folder);
            }
          }}
          spellCheck={false}
        />
        <button className="btn" onClick={() => void pickFolder()} disabled={working}>
          参照
        </button>
        <button className="btn primary" onClick={() => void buildIndex()} disabled={working}>
          索引を作る
        </button>
        {dbInfo ? (
          <span className={`db-pill${dbInfo.exists ? "" : " empty"}`}>
            {dbInfo.exists ? `素材集 ${dbInfo.banks} / 候補 ${dbInfo.units}` : "索引なし"}
          </span>
        ) : null}
        <span className="top-spacer" />
        <ExportMenu
          disabled={working}
          canExport={!!dbInfo?.exists}
          selectionCount={selectedKeys.size}
          onWav={() => setModal({ kind: "wav" })}
          onSelectedWav={() => void exportSelectedWav()}
          onCsv={() => void exportCsv()}
          onUtau={() => setModal({ kind: "utau" })}
          onLibrary={() => void exportLibrary()}
          onImport={() => setModal({ kind: "import" })}
        />
      </header>

      <section className="filters">
        <div className="filter-line">
          {(
            [
              ["name", "発音", "filter-name"],
              ["cons", "子音", "filter-cons"],
              ["vowel", "母音", "filter-vowel"],
              ["prev_vowel", "前の母音", "filter-prev"],
              ["next_cons", "次の子音", "filter-next"],
            ] as const
          ).map(([key, label]) => (
            <label className="field" key={key}>
              <span>{label}</span>
              <input
                value={filters[key]}
                onChange={(event) => patchFilters({ [key]: event.target.value }, false)}
                onKeyDown={(event) => {
                  if (event.key === "Enter") {
                    event.preventDefault();
                    if (dbInfo?.exists) void runSearch();
                  }
                }}
                spellCheck={false}
              />
            </label>
          ))}
        </div>
        <div className="filter-line">
          <label className="field">
            <span>曲名</span>
            <input
              className="filter-song"
              value={filters.song}
              onChange={(event) => patchFilters({ song: event.target.value }, false)}
              onKeyDown={(event) => {
                if (event.key === "Enter") {
                  event.preventDefault();
                  if (dbInfo?.exists) void runSearch();
                }
              }}
              spellCheck={false}
            />
          </label>
          <label className="field grow">
            <span>フリー検索</span>
            <input
              value={filters.text}
              onChange={(event) => patchFilters({ text: event.target.value }, false)}
              onKeyDown={(event) => {
                if (event.key === "Enter") {
                  event.preventDefault();
                  if (dbInfo?.exists) void runSearch();
                }
              }}
              placeholder="発音・子音・素材集など"
              spellCheck={false}
            />
          </label>
          <label className="check">
            <input
              type="checkbox"
              checked={filters.exact}
              onChange={(event) => patchFilters({ exact: event.target.checked })}
            />
            完全一致
          </label>
          <label className="check">
            <input
              type="checkbox"
              checked={filters.favorite}
              onChange={(event) => patchFilters({ favorite: event.target.checked })}
            />
            ★のみ
          </label>
          <label className="check">
            <input
              type="checkbox"
              checked={filters.showHidden}
              onChange={(event) => patchFilters({ showHidden: event.target.checked })}
            />
            非表示も表示
          </label>
          <label className="field">
            <span>並び</span>
            <select value={sortKey} onChange={(event) => chooseSort(event.target.value)}>
              {[
                "name",
                "midi",
                "length",
                "bank",
                "cons",
                "vowel",
                "prev_vowel",
                "next_cons",
                "checked",
                "favorite",
                "song",
              ].map((key) => (
                <option key={key} value={key}>
                  {sortLabel(key)}
                </option>
              ))}
            </select>
          </label>
          <button
            className="btn"
            onClick={() => {
              if (dbInfo?.exists) void runSearch();
              else ensureDb();
            }}
            disabled={working}
          >
            検索
          </button>
          <span className="count-pill">{rows.length} 件</span>
        </div>
      </section>

      <Table
        rows={rows}
        selected={selectedKeys}
        activeKey={activeKey}
        playingKey={playingKey}
        sortKey={sortKey}
        descending={descending}
        resetToken={searchSeq}
        emptyText={emptyText}
        onRowClick={handleRowClick}
        onRowDoubleClick={(row) => void playRow(row)}
        onDragRows={(dragged) => void handleDragRows(dragged)}
        onSort={sortByColumn}
        onKeyboardSelect={handleKeyboardSelect}
        onPreview={(row) => void playRow(row)}
      />

      <footer className="actionbar">
        <button
          className="btn wide"
          onClick={() => {
            if (playingKey) stopPreview();
            else if (active) void playRow(active);
          }}
          disabled={!active && !playingKey}
        >
          {playingKey ? "■ 停止" : "▶ 試聴"}
        </button>
        <button className="btn" onClick={openRename} disabled={!hasTarget || working}>
          名前を変更
        </button>
        <button className="btn" onClick={() => void toggleFavorite()} disabled={!hasTarget || working}>
          ★切替
        </button>
        <button className="btn" onClick={() => void toggleHidden()} disabled={!hasTarget || working}>
          非表示/戻す
        </button>
        <button className="btn" onClick={() => void openDuplicates()} disabled={!dbInfo?.exists || working}>
          重複を整理
        </button>
        <button
          className="btn"
          onClick={() => void doUndo()}
          disabled={!undoStack.length || working}
          title="Ctrl+Z"
        >
          元に戻す
        </button>
        <button
          className="btn"
          onClick={() => void doRedo()}
          disabled={!redoStack.length || working}
          title="Ctrl+Y"
        >
          やり直し
        </button>
        <button className="btn" onClick={openInWave} disabled={!hasTarget || working}>
          波形で開く
        </button>
        <button className="btn" onClick={() => void openSource()} disabled={!hasTarget || working}>
          元のフレーズを開く
        </button>
        <span className={`status${status.error ? " error" : ""}`}>
          {busy ? <span className="spinner" aria-hidden="true" /> : null}
          {statusText}
        </span>
        {searchMs !== null ? <span className="search-ms">検索 {searchMs.toFixed(1)} ms</span> : null}
      </footer>

      <p className="hint">
        行を選んでそのまま REAPER / エクスプローラーへドラッグできます（44.1kHz / 24bit /
        モノラル WAV）。ダブルクリックまたは Enter で試聴。元の音声は変更しません。
      </p>

      {modal?.kind === "rename" ? (
        <RenameDialog
          initial={modal.initial}
          count={modal.count}
          onClose={() => setModal(null)}
          onSubmit={applyRename}
        />
      ) : null}
      {modal?.kind === "wav" ? (
        <WavDialog onClose={() => setModal(null)} onExport={exportWavs} />
      ) : null}
      {modal?.kind === "utau" ? (
        <UtauDialog
          favoritesDefault={filters.favorite}
          onClose={() => setModal(null)}
          onExport={exportUtau}
        />
      ) : null}
      {modal?.kind === "duplicates" ? (
        <DuplicatesDialog
          groups={duplicateGroups}
          onClose={() => setModal(null)}
          onHide={hideDuplicates}
        />
      ) : null}
      {modal?.kind === "source" ? (
        <SourceDialog
          info={modal.info}
          onClose={() => setModal(null)}
          onPreview={() => {
            if (active) void playRow(active);
          }}
          onReveal={() => {
            void api
              .revealInExplorer(modal.info.source)
              .catch((reason) => say(`開けません: ${errorText(reason)}`, true));
          }}
        />
      ) : null}
      {modal?.kind === "import" ? (
        <ImportDialog onClose={() => setModal(null)} onImport={runImport} />
      ) : null}
    </div>
  );
}

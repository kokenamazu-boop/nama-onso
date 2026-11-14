import { useState } from "react";
import { Modal } from "./Modal";
import { chooseFolder } from "../lib/dialogs";
import { basename, errorText, rowLabel } from "../lib/format";
import type { DuplicateGroup, ImportResult, SourceInfo } from "../lib/types";

type FolderFieldProps = {
  label: string;
  title: string;
  value: string;
  onChange: (value: string) => void;
  placeholder?: string;
};

function FolderField({ label, title, value, onChange, placeholder }: FolderFieldProps) {
  return (
    <div className="form-row">
      <span className="form-label">{label}</span>
      <div className="folder-field">
        <input
          value={value}
          placeholder={placeholder ?? "フォルダを選択"}
          onChange={(event) => onChange(event.target.value)}
        />
        <button
          type="button"
          className="btn small"
          onClick={async () => {
            const picked = await chooseFolder(title);
            if (picked) onChange(picked);
          }}
        >
          参照
        </button>
      </div>
    </div>
  );
}

type RenameDialogProps = {
  initial: string;
  count: number;
  onClose: () => void;
  onSubmit: (name: string) => Promise<void>;
};

export function RenameDialog({ initial, count, onClose, onSubmit }: RenameDialogProps) {
  const [name, setName] = useState(initial);
  const [working, setWorking] = useState(false);
  const [error, setError] = useState("");

  const submit = async () => {
    const value = name.trim();
    if (!value) {
      setError("表示名を入力してください。");
      return;
    }
    setWorking(true);
    setError("");
    try {
      await onSubmit(value);
      onClose();
    } catch (reason) {
      setError(errorText(reason));
    } finally {
      setWorking(false);
    }
  };

  return (
    <Modal
      title="発音の名前"
      onClose={onClose}
      footer={
        <>
          <button className="btn" onClick={onClose} disabled={working}>
            キャンセル
          </button>
          <button className="btn primary" onClick={submit} disabled={working}>
            {working ? "変更中…" : `${count} 件を変更`}
          </button>
        </>
      }
    >
      <div className="form-row">
        <span className="form-label">表示名</span>
        <input
          autoFocus
          value={name}
          onChange={(event) => setName(event.target.value)}
          onKeyDown={(event) => {
            if (event.key === "Enter") {
              event.preventDefault();
              void submit();
            }
          }}
          onFocus={(event) => event.currentTarget.select()}
        />
      </div>
      <p className="note">
        表と検索に使う名前です。元の素材集JSONは変更しません（索引側の別名だけを書き換えます）。
      </p>
      {error ? <p className="form-error">{error}</p> : null}
    </Modal>
  );
}

type WavDialogProps = {
  onClose: () => void;
  onExport: (outDir: string, pattern: string) => Promise<void>;
};

export function WavDialog({ onClose, onExport }: WavDialogProps) {
  const [outDir, setOutDir] = useState("");
  const [pattern, setPattern] = useState("{name}");
  const [working, setWorking] = useState(false);
  const [error, setError] = useState("");

  const submit = async () => {
    if (!outDir.trim()) {
      setError("書き出し先を選んでください。");
      return;
    }
    if (!pattern.trim()) {
      setError("ファイル名のパターンを入力してください。");
      return;
    }
    setWorking(true);
    setError("");
    try {
      await onExport(outDir.trim(), pattern.trim());
      onClose();
    } catch (reason) {
      setError(errorText(reason));
    } finally {
      setWorking(false);
    }
  };

  return (
    <Modal
      title="WAVを書き出す"
      onClose={onClose}
      footer={
        <>
          <button className="btn" onClick={onClose} disabled={working}>
            キャンセル
          </button>
          <button className="btn primary" onClick={submit} disabled={working}>
            {working ? "書き出し中…" : "書き出す"}
          </button>
        </>
      }
    >
      <FolderField label="書き出し先" title="WAVの書き出し先" value={outDir} onChange={setOutDir} />
      <div className="form-row">
        <span className="form-label">名前</span>
        <input
          value={pattern}
          onChange={(event) => setPattern(event.target.value)}
          onKeyDown={(event) => {
            if (event.key === "Enter") void submit();
          }}
        />
      </div>
      <p className="note">
        非表示以外の候補をすべて書き出します（画面の絞り込みは使いません）。
        使える項目：{"{name} {cons} {vowel} {note} {cents} {midi} {ms} {index} {bank}"}
        。同じ名前は _2, _3 を付けます。WAVは 44.1kHz / 24bit / モノラル に変換します。
      </p>
      {error ? <p className="form-error">{error}</p> : null}
    </Modal>
  );
}

export type UtauOptions = {
  out_dir: string;
  name: string;
  author: string;
  vcv: boolean;
  multiple: boolean;
  favorites: boolean;
  min_ms: number;
};

type UtauDialogProps = {
  favoritesDefault: boolean;
  onClose: () => void;
  onExport: (options: UtauOptions) => Promise<void>;
};

export function UtauDialog({ favoritesDefault, onClose, onExport }: UtauDialogProps) {
  const [outDir, setOutDir] = useState("");
  const [name, setName] = useState("NamaVoice");
  const [nameTouched, setNameTouched] = useState(false);
  const [author, setAuthor] = useState("");
  const [minMs, setMinMs] = useState("0");
  const [multiple, setMultiple] = useState(false);
  const [vcv, setVcv] = useState(false);
  const [favorites, setFavorites] = useState(favoritesDefault);
  const [working, setWorking] = useState(false);
  const [error, setError] = useState("");

  const pickOut = (value: string) => {
    setOutDir(value);
    if (!nameTouched && value.trim()) {
      setName(basename(value.trim().replace(/[\\/]+$/, "")) || "NamaVoice");
    }
  };

  const submit = async () => {
    if (!outDir.trim()) {
      setError("書き出し先を選んでください。");
      return;
    }
    const minValue = Number.parseFloat(minMs.trim() || "0");
    if (!Number.isFinite(minValue) || minValue < 0) {
      setError("「短い候補」はミリ秒の数値で入力してください（0で全部）。");
      return;
    }
    setWorking(true);
    setError("");
    try {
      await onExport({
        out_dir: outDir.trim(),
        name: name.trim() || "NamaVoice",
        author: author.trim(),
        vcv,
        multiple,
        favorites,
        min_ms: minValue,
      });
      onClose();
    } catch (reason) {
      setError(errorText(reason));
    } finally {
      setWorking(false);
    }
  };

  return (
    <Modal
      title="UTAU音源を書き出す"
      onClose={onClose}
      footer={
        <>
          <button className="btn" onClick={onClose} disabled={working}>
            キャンセル
          </button>
          <button className="btn primary" onClick={submit} disabled={working}>
            {working ? "書き出し中…" : "書き出す"}
          </button>
        </>
      }
    >
      <FolderField label="出力先" title="UTAU音源の書き出し先（空のフォルダ推奨）" value={outDir} onChange={pickOut} />
      <div className="form-row">
        <span className="form-label">音源名</span>
        <input
          value={name}
          onChange={(event) => {
            setName(event.target.value);
            setNameTouched(true);
          }}
        />
      </div>
      <div className="form-row">
        <span className="form-label">作者</span>
        <input value={author} onChange={(event) => setAuthor(event.target.value)} />
      </div>
      <div className="form-row">
        <span className="form-label">短い候補</span>
        <div className="inline-field">
          <input
            className="tiny"
            value={minMs}
            onChange={(event) => setMinMs(event.target.value)}
            onKeyDown={(event) => {
              if (event.key === "Enter") void submit();
            }}
          />
          <span className="muted">ms 以下は書き出さない（0=全部）</span>
        </div>
      </div>
      <label className="check">
        <input type="checkbox" checked={multiple} onChange={(event) => setMultiple(event.target.checked)} />
        同じ発音を複数入れる（あA3 形式）
      </label>
      <label className="check">
        <input type="checkbox" checked={vcv} onChange={(event) => setVcv(event.target.checked)} />
        連続音（VCV）の別名も作る
      </label>
      <label className="check">
        <input type="checkbox" checked={favorites} onChange={(event) => setFavorites(event.target.checked)} />
        ★だけ書き出す
      </label>
      <p className="note">
        長さは候補全体（開始から終了まで）で判定します。WAVは 44.1kHz / 24bit / モノラル
        に変換し、WAV名・別名にアンダースコアは使いません。元WAVは変更しません。
      </p>
      {error ? <p className="form-error">{error}</p> : null}
    </Modal>
  );
}

type DuplicatesDialogProps = {
  groups: DuplicateGroup[];
  onClose: () => void;
  onHide: () => Promise<void>;
};

export function DuplicatesDialog({ groups, onClose, onHide }: DuplicatesDialogProps) {
  const [working, setWorking] = useState(false);
  const [error, setError] = useState("");
  const total = groups.reduce((sum, group) => sum + group.count, 0);
  const dropTotal = groups.reduce((sum, group) => sum + group.drop.length, 0);

  const submit = async () => {
    setWorking(true);
    setError("");
    try {
      await onHide();
      onClose();
    } catch (reason) {
      setError(errorText(reason));
    } finally {
      setWorking(false);
    }
  };

  return (
    <Modal
      title="重複を整理"
      wide
      onClose={onClose}
      footer={
        <>
          <button className="btn" onClick={onClose} disabled={working}>
            キャンセル
          </button>
          <button className="btn primary" onClick={submit} disabled={working}>
            {working ? "整理中…" : `${dropTotal} 件を非表示にする`}
          </button>
        </>
      }
    >
      <p className="note">
        同じ発音・近い音程・近い長さの候補が {groups.length} 組（{total} 件）あります。
        各組で最も長い1件を残し、他を非表示にします。元の素材集JSONは変更しません。
      </p>
      <div className="dup-list">
        <div className="dup-row dup-head-row">
          <span>発音</span>
          <span>音程</span>
          <span>件数</span>
          <span>残す（長さ / 素材集）</span>
          <span>非表示</span>
        </div>
        {groups.map((group, index) => (
          <div className="dup-row" key={`${group.name}-${group.midi}-${index}`}>
            <span>{rowLabel(group.keep)}</span>
            <span>{group.keep.note || "-"}</span>
            <span>{group.count}</span>
            <span>
              {Math.round(group.keep.ms ?? (group.keep.end - group.keep.start) * 1000)}ms /{" "}
              {basename(group.keep.bank_path)}
            </span>
            <span>{group.drop.length}</span>
          </div>
        ))}
      </div>
      {error ? <p className="form-error">{error}</p> : null}
    </Modal>
  );
}

type SourceDialogProps = {
  info: SourceInfo;
  onClose: () => void;
  onPreview: () => void;
  onReveal: () => void;
};

export function SourceDialog({ info, onClose, onPreview, onReveal }: SourceDialogProps) {
  const [copied, setCopied] = useState(false);

  return (
    <Modal
      title="元のフレーズ"
      wide
      onClose={onClose}
      footer={
        <>
          <button className="btn" onClick={onPreview}>
            ▶ この区間を試聴
          </button>
          <button className="btn" onClick={onReveal} disabled={!info.exists}>
            エクスプローラーで表示
          </button>
          <button className="btn primary" onClick={onClose}>
            閉じる
          </button>
        </>
      }
    >
      <div className="source-grid">
        <span className="form-label">候補</span>
        <span>{info.name}</span>
        <span className="form-label">素材集</span>
        <span>{basename(info.bank_path)}</span>
        <span className="form-label">元WAV</span>
        <code className="path-box">{info.source || "(パスがありません)"}</code>
        <span className="form-label">区間</span>
        <span>
          {info.start.toFixed(3)} 秒 ～ {info.end.toFixed(3)} 秒（
          {Math.round((info.end - info.start) * 1000)} ms）
        </span>
        <span className="form-label">状態</span>
        <span className={info.exists ? "ok-text" : "error-text"}>
          {info.exists ? "元WAVが見つかりました" : "元WAVが見つかりません"}
        </span>
      </div>
      <p className="note">
        波形窓との連携は段階2です。ここでは元WAVのパスと区間を確認できます。
      </p>
      <button
        className="btn small"
        onClick={async () => {
          try {
            await navigator.clipboard.writeText(info.source);
            setCopied(true);
            window.setTimeout(() => setCopied(false), 1500);
          } catch {
            setCopied(false);
          }
        }}
        disabled={!info.source}
      >
        {copied ? "コピーしました" : "パスをコピー"}
      </button>
    </Modal>
  );
}

type ImportDialogProps = {
  onClose: () => void;
  onImport: (folder: string, outDir: string, song: string) => Promise<ImportResult>;
};

export function ImportDialog({ onClose, onImport }: ImportDialogProps) {
  const [folder, setFolder] = useState("");
  const [outDir, setOutDir] = useState("");
  const [song, setSong] = useState("");
  const [working, setWorking] = useState(false);
  const [error, setError] = useState("");
  const [result, setResult] = useState<ImportResult | null>(null);

  const submit = async () => {
    if (!folder.trim() || !outDir.trim()) {
      setError("取り込み元と書き出し先を指定してください。");
      return;
    }
    setWorking(true);
    setError("");
    try {
      setResult(await onImport(folder.trim(), outDir.trim(), song.trim()));
    } catch (reason) {
      setError(errorText(reason));
    } finally {
      setWorking(false);
    }
  };

  return (
    <Modal
      title="音素DBから取り込み"
      onClose={onClose}
      footer={
        result ? (
          <button className="btn primary" onClick={onClose}>
            閉じる
          </button>
        ) : (
          <>
            <button className="btn" onClick={onClose} disabled={working}>
              キャンセル
            </button>
            <button className="btn primary" onClick={submit} disabled={working}>
              {working ? "取り込み中…" : "取り込む"}
            </button>
          </>
        )
      }
    >
      <FolderField label="取り込み元" title="音素DBの waves フォルダ" value={folder} onChange={setFolder} />
      <FolderField label="書き出し先" title="素材集JSONの書き出し先" value={outDir} onChange={setOutDir} />
      <div className="form-row">
        <span className="form-label">曲名</span>
        <input
          value={song}
          placeholder="空ならフォルダ名を使います"
          onChange={(event) => setSong(event.target.value)}
        />
      </div>
      {result ? (
        <p className="note ok-text">
          取り込み: {result.written} 件（読み飛ばし {result.skipped}、WAV {result.files} 件）。
          この後、出力先を選んで索引を作り直してください。
        </p>
      ) : (
        <p className="note">
          &lt;発音&gt;_&lt;音名&gt;_&lt;番号&gt;.wav を1件ずつ素材集JSONにします。元WAVは変更しません。
        </p>
      )}
      {error ? <p className="form-error">{error}</p> : null}
    </Modal>
  );
}

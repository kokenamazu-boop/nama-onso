import { useEffect, useRef, useState } from "react";

type ExportMenuProps = {
  disabled: boolean;
  canExport: boolean;
  selectionCount: number;
  onWav: () => void;
  onSelectedWav: () => void;
  onCsv: () => void;
  onUtau: () => void;
  onLibrary: () => void;
  onImport: () => void;
};

export function ExportMenu({
  disabled,
  canExport,
  selectionCount,
  onWav,
  onSelectedWav,
  onCsv,
  onUtau,
  onLibrary,
  onImport,
}: ExportMenuProps) {
  const [open, setOpen] = useState(false);
  const wrapRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    if (!open) return;
    const onDown = (event: MouseEvent) => {
      if (!wrapRef.current?.contains(event.target as Node)) setOpen(false);
    };
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") setOpen(false);
    };
    window.addEventListener("mousedown", onDown);
    window.addEventListener("keydown", onKey);
    return () => {
      window.removeEventListener("mousedown", onDown);
      window.removeEventListener("keydown", onKey);
    };
  }, [open]);

  const pick = (action: () => void) => {
    setOpen(false);
    action();
  };

  return (
    <div className="menu-wrap" ref={wrapRef}>
      <button
        className="btn menu-button"
        disabled={disabled}
        onClick={() => setOpen((value) => !value)}
        aria-haspopup="menu"
        aria-expanded={open}
      >
        書き出し <span className="caret">▾</span>
      </button>
      {open ? (
        <div className="menu-panel" role="menu">
          <button className="menu-item" onClick={() => pick(onWav)} disabled={!canExport}>
            WAV（一括・パターン命名）…
          </button>
          <button
            className="menu-item"
            onClick={() => pick(onSelectedWav)}
            disabled={!canExport || selectionCount === 0}
          >
            選択をWAV…
            <span className="menu-meta">{selectionCount ? `${selectionCount} 件` : "未選択"}</span>
          </button>
          <button className="menu-item" onClick={() => pick(onCsv)} disabled={!canExport}>
            CSV…
          </button>
          <button className="menu-item" onClick={() => pick(onUtau)} disabled={!canExport}>
            UTAU音源…
          </button>
          <button className="menu-item" onClick={() => pick(onLibrary)} disabled={!canExport}>
            library.json…
          </button>
          <div className="menu-sep" />
          <button className="menu-item" onClick={() => pick(onImport)}>
            音素DBから取り込み…
          </button>
        </div>
      ) : null}
    </div>
  );
}

import { useEffect, useRef, useState, type MouseEvent as ReactMouseEvent } from "react";
import { COLUMN_SORT } from "../lib/search";
import { formatMs, rowKey, rowLabel } from "../lib/format";
import type { Row } from "../lib/types";

const ROW_HEIGHT = 30;
const OVERSCAN = 6;

const COLUMNS: { key: string; label: string; className?: string }[] = [
  { key: "mark", label: "印", className: "col-mark" },
  { key: "name", label: "発音", className: "col-name" },
  { key: "cons", label: "子音", className: "col-cons" },
  { key: "vowel", label: "母音", className: "col-vowel" },
  { key: "prev", label: "前の母音", className: "col-prev" },
  { key: "next", label: "次の子音", className: "col-next" },
  { key: "midi", label: "音程", className: "col-midi" },
  { key: "ms", label: "長さms", className: "col-ms" },
  { key: "song", label: "曲名", className: "col-song" },
  { key: "bank", label: "素材集", className: "col-bank" },
];

type TableProps = {
  rows: Row[];
  selected: Set<string>;
  activeKey: string | null;
  playingKey: string | null;
  sortKey: string;
  descending: boolean;
  resetToken: number;
  emptyText: string;
  onRowClick: (row: Row, index: number, event: ReactMouseEvent) => void;
  onRowDoubleClick: (row: Row) => void;
  onDragRows: (rows: Row[]) => void;
  onSort: (column: string) => void;
  onKeyboardSelect: (row: Row) => void;
  onPreview: (row: Row) => void;
};

export function Table({
  rows,
  selected,
  activeKey,
  playingKey,
  sortKey,
  descending,
  resetToken,
  emptyText,
  onRowClick,
  onRowDoubleClick,
  onDragRows,
  onSort,
  onKeyboardSelect,
  onPreview,
}: TableProps) {
  const scrollRef = useRef<HTMLDivElement | null>(null);
  const [scrollTop, setScrollTop] = useState(0);
  const [viewport, setViewport] = useState(320);
  const pendingDrag = useRef<{ x: number; y: number; row: Row } | null>(null);
  const suppressClick = useRef(false);

  useEffect(() => {
    const element = scrollRef.current;
    if (!element) return;
    const update = () => setViewport(element.clientHeight);
    update();
    const observer = new ResizeObserver(update);
    observer.observe(element);
    return () => observer.disconnect();
  }, []);

  useEffect(() => {
    const onMove = (event: MouseEvent) => {
      const pending = pendingDrag.current;
      if (!pending) return;
      if (Math.hypot(event.clientX - pending.x, event.clientY - pending.y) < 5) return;
      pendingDrag.current = null;
      suppressClick.current = true;
      window.setTimeout(() => {
        suppressClick.current = false;
      }, 400);
      const draggedKey = rowKey(pending.row);
      const picked = selected.has(draggedKey)
        ? rows.filter((row) => selected.has(rowKey(row)))
        : [pending.row];
      onDragRows(picked);
    };
    const onUp = () => {
      pendingDrag.current = null;
    };
    window.addEventListener("mousemove", onMove);
    window.addEventListener("mouseup", onUp);
    return () => {
      window.removeEventListener("mousemove", onMove);
      window.removeEventListener("mouseup", onUp);
    };
  }, [rows, selected, onDragRows]);

  useEffect(() => {
    const element = scrollRef.current;
    if (!element) return;
    element.scrollTop = 0;
    setScrollTop(0);
  }, [resetToken]);

  useEffect(() => {
    if (!activeKey) return;
    const index = rows.findIndex((row) => rowKey(row) === activeKey);
    const element = scrollRef.current;
    if (index < 0 || !element) return;
    const top = index * ROW_HEIGHT;
    if (top < element.scrollTop) {
      element.scrollTop = top;
    } else if (top + ROW_HEIGHT > element.scrollTop + element.clientHeight) {
      element.scrollTop = top + ROW_HEIGHT - element.clientHeight;
    }
  }, [activeKey, rows]);

  const first = Math.max(0, Math.floor(scrollTop / ROW_HEIGHT) - OVERSCAN);
  const last = Math.min(rows.length, Math.ceil((scrollTop + viewport) / ROW_HEIGHT) + OVERSCAN);
  const visible = rows.slice(first, last);

  const moveActive = (delta: number) => {
    if (!rows.length) return;
    const current = activeKey ? rows.findIndex((row) => rowKey(row) === activeKey) : -1;
    const index = Math.max(0, Math.min(rows.length - 1, current < 0 ? 0 : current + delta));
    onKeyboardSelect(rows[index]);
  };

  return (
    <div className="table-wrap">
      <div className="table-head" role="row">
        {COLUMNS.map((column) => {
          const sort = COLUMN_SORT[column.key] ?? "name";
          const active = sort === sortKey;
          return (
            <button
              key={column.key}
              type="button"
              className={`th ${column.className ?? ""}${active ? " active" : ""}`}
              onClick={() => onSort(column.key)}
              title={`${column.label || column.key}で並べ替え`}
            >
              <span>{column.label}</span>
              {active ? <span className="sort-arrow">{descending ? "▼" : "▲"}</span> : null}
            </button>
          );
        })}
      </div>
      <div
        className="table-body"
        ref={scrollRef}
        tabIndex={0}
        onScroll={(event) => setScrollTop(event.currentTarget.scrollTop)}
        onKeyDown={(event) => {
          if (event.key === "ArrowDown") {
            event.preventDefault();
            moveActive(1);
          } else if (event.key === "ArrowUp") {
            event.preventDefault();
            moveActive(-1);
          } else if (event.key === "Enter" && activeKey) {
            const row = rows.find((item) => rowKey(item) === activeKey);
            if (row) {
              event.preventDefault();
              onPreview(row);
            }
          }
        }}
      >
        {rows.length === 0 ? (
          <div className="table-empty">{emptyText}</div>
        ) : (
          <div className="table-inner" style={{ height: rows.length * ROW_HEIGHT }}>
            {visible.map((row, offset) => {
              const index = first + offset;
              const key = rowKey(row);
              const isSelected = selected.has(key);
              const classes = [
                "table-row",
                index % 2 ? "odd" : "",
                isSelected ? "selected" : "",
                key === activeKey ? "active" : "",
                key === playingKey ? "playing" : "",
                row.hidden ? "hidden-row" : "",
              ]
                .filter(Boolean)
                .join(" ");
              const title =
                `${rowLabel(row)}  ${row.note}  ${formatMs(row.ms)}ms` +
                `  子音=${row.cons || "-"}  母音=${row.vowel || "-"}`;
              return (
                <div
                  key={key}
                  role="row"
                  aria-selected={isSelected}
                  className={classes}
                  style={{ transform: `translateY(${index * ROW_HEIGHT}px)` }}
                  title={title}
                  onMouseDown={(event) => {
                    if (event.button !== 0) return;
                    const target = event.target as HTMLElement;
                    if (target.closest("button, a, input")) return;
                    pendingDrag.current = { x: event.clientX, y: event.clientY, row };
                    scrollRef.current?.focus({ preventScroll: true });
                  }}
                  onClick={(event) => {
                    if (suppressClick.current) {
                      suppressClick.current = false;
                      return;
                    }
                    onRowClick(row, index, event);
                  }}
                  onDoubleClick={() => onRowDoubleClick(row)}
                >
                  <span className="cell col-mark">
                    {key === playingKey ? <span className="playing-mark">▶</span> : null}
                    {row.favorite ? <span className="star">★</span> : null}
                    {row.hidden ? <span className="hidden-mark">×</span> : null}
                  </span>
                  <span className="cell col-name">{rowLabel(row)}</span>
                  <span className="cell col-cons">{row.cons || "-"}</span>
                  <span className="cell col-vowel">{row.vowel || "-"}</span>
                  <span className="cell col-prev">{row.prev_vowel || "-"}</span>
                  <span className="cell col-next">{row.next_cons || "-"}</span>
                  <span className="cell col-midi">{row.note}</span>
                  <span className="cell col-ms">{formatMs(row.ms)}</span>
                  <span className="cell col-song">{row.song || "-"}</span>
                  <span className="cell col-bank" title={row.bank_path}>
                    {row.bank_path.split(/[\\/]/).pop()}
                  </span>
                </div>
              );
            })}
          </div>
        )}
      </div>
    </div>
  );
}

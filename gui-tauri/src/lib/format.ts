import type { Row } from "./types";

export const SORT_CHOICES = [
  { label: "発音", key: "name" },
  { label: "音程", key: "midi" },
  { label: "長さ", key: "length" },
  { label: "素材集", key: "bank" },
  { label: "子音", key: "cons" },
  { label: "母音", key: "vowel" },
  { label: "前の母音", key: "prev_vowel" },
  { label: "次の子音", key: "next_cons" },
  { label: "確認済み", key: "checked" },
  { label: "★", key: "favorite" },
  { label: "曲名", key: "song" },
] as const;

export function rowKey(row: Pick<Row, "bank_path" | "unit_id">): string {
  return `${row.bank_path}|${row.unit_id}`;
}

/**
 * db.search's `display` is derived from the phonemes again, which drops a
 * pitch-style alias such as "あA3" back to "あ". When an alias exists
 * (name !== raw_name) the table shows it exactly, as the spec requires.
 */
export function rowLabel(row: Pick<Row, "name" | "raw_name" | "display">): string {
  return row.name !== row.raw_name ? row.name : row.display;
}

export function sortLabel(key: string): string {
  return SORT_CHOICES.find((choice) => choice.key === key)?.label ?? key;
}

export function basename(path: string): string {
  const parts = path.split(/[\\/]/);
  return parts[parts.length - 1] || path;
}

export function joinPath(folder: string, name: string): string {
  const separator = folder.includes("\\") ? "\\" : "/";
  return `${folder.replace(/[\\/]+$/, "")}${separator}${name}`;
}

export function formatMs(ms: number): string {
  return Math.round(ms).toString();
}

export function formatSeconds(seconds: number): string {
  return seconds.toFixed(3);
}

export function errorText(error: unknown): string {
  if (typeof error === "string") return error;
  if (error instanceof Error) return error.message;
  return String(error);
}

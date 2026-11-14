import type { FlagChange, FlagState, Row, RowRef } from "./types";

export function flagsOf(row: Pick<Row, "favorite" | "hidden" | "tags">): FlagState {
  return {
    favorite: row.favorite ? 1 : 0,
    hidden: row.hidden ? 1 : 0,
    tags: row.tags ?? "",
  };
}

export function sameFlags(a: FlagState, b: FlagState): boolean {
  return a.favorite === b.favorite && a.hidden === b.hidden && a.tags === b.tags;
}

export function flagKey(state: FlagState): string {
  return JSON.stringify([state.favorite, state.hidden, state.tags]);
}

/** One db.set_flags call per distinct target state (same trick as restore_flags). */
export function groupFlagChanges(
  items: FlagChange[],
  side: "old" | "new",
): { state: FlagState; refs: RowRef[] }[] {
  const groups = new Map<string, { state: FlagState; refs: RowRef[] }>();
  for (const item of items) {
    const state = item[side];
    const key = flagKey(state);
    const group = groups.get(key);
    const ref = { bank_path: item.bank_path, unit_id: item.unit_id };
    if (group) {
      group.refs.push(ref);
    } else {
      groups.set(key, { state, refs: [ref] });
    }
  }
  return [...groups.values()];
}

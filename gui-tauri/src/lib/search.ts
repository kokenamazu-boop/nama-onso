import type { SearchParams } from "./types";

export type Filters = {
  name: string;
  cons: string;
  vowel: string;
  prev_vowel: string;
  next_cons: string;
  song: string;
  text: string;
  exact: boolean;
  favorite: boolean;
  showHidden: boolean;
};

export const EMPTY_FILTERS: Filters = {
  name: "",
  cons: "",
  vowel: "",
  prev_vowel: "",
  next_cons: "",
  song: "",
  text: "",
  exact: false,
  favorite: false,
  showHidden: false,
};

/**
 * `hidden` follows db.search: null excludes hidden rows, false includes them.
 * The tkinter DBWindow passes true here (showing *only* hidden rows); the pilot
 * uses the documented meaning of the "非表示も表示" checkbox.
 */
export function buildSearchParams(
  filters: Filters,
  sort: string,
  descending: boolean,
): SearchParams {
  return {
    name: filters.name.trim(),
    exact: filters.exact,
    cons: filters.cons.trim(),
    vowel: filters.vowel.trim(),
    prev_vowel: filters.prev_vowel.trim(),
    next_cons: filters.next_cons.trim(),
    text: filters.text.trim(),
    song: filters.song.trim(),
    sort,
    descending,
    favorite: filters.favorite,
    hidden: filters.showHidden ? false : null,
    tags: "",
    min_midi: null,
    max_midi: null,
    min_ms: null,
  };
}

export const COLUMN_SORT: Record<string, string> = {
  mark: "favorite",
  name: "name",
  cons: "cons",
  vowel: "vowel",
  prev: "prev_vowel",
  next: "next_cons",
  midi: "midi",
  ms: "length",
  song: "song",
  bank: "bank",
};

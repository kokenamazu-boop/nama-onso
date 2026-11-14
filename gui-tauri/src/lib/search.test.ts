import { describe, expect, it } from "vitest";
import { buildSearchParams, COLUMN_SORT, EMPTY_FILTERS } from "./search";

describe("buildSearchParams", () => {
  it("excludes hidden rows by default", () => {
    const params = buildSearchParams(EMPTY_FILTERS, "name", false);
    expect(params.hidden).toBeNull();
    expect(params.tags).toBe("");
    expect(params.min_midi).toBeNull();
  });

  it("includes hidden rows when the checkbox is on", () => {
    const params = buildSearchParams({ ...EMPTY_FILTERS, showHidden: true }, "name", false);
    expect(params.hidden).toBe(false);
  });

  it("trims text fields and keeps the sort direction", () => {
    const params = buildSearchParams(
      { ...EMPTY_FILTERS, name: "  あ ", cons: " k", text: " そら ", exact: true, favorite: true },
      "midi",
      true,
    );
    expect(params.name).toBe("あ");
    expect(params.cons).toBe("k");
    expect(params.text).toBe("そら");
    expect(params.exact).toBe(true);
    expect(params.favorite).toBe(true);
    expect(params.sort).toBe("midi");
    expect(params.descending).toBe(true);
  });

  it("maps every table column to a db.py sort key", () => {
    expect(Object.values(COLUMN_SORT).sort()).toEqual(
      ["bank", "cons", "favorite", "length", "midi", "name", "next_cons", "prev_vowel", "song", "vowel"],
    );
  });
});

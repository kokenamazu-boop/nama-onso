import { describe, expect, it } from "vitest";
import { basename, joinPath, rowKey, rowLabel, sortLabel } from "./format";

describe("rowLabel", () => {
  it("shows the derived kana when there is no alias", () => {
    expect(rowLabel({ name: "k a", raw_name: "k a", display: "か" })).toBe("か");
  });

  it("shows the alias exactly, even with a pitch suffix", () => {
    expect(rowLabel({ name: "あA3", raw_name: "あ", display: "あ" })).toBe("あA3");
  });
});

describe("paths", () => {
  it("keeps Windows separators in save defaults", () => {
    expect(joinPath("D:\\素材集\\", "phonemes.csv")).toBe("D:\\素材集\\phonemes.csv");
    expect(joinPath("/home/nama/banks", "phonemes.csv")).toBe("/home/nama/banks/phonemes.csv");
    expect(basename("D:\\素材集\\そら.nama-voices.json")).toBe("そら.nama-voices.json");
  });
});

describe("keys", () => {
  it("combines bank path and unit id", () => {
    expect(rowKey({ bank_path: "D:\\b.json", unit_id: "u1" })).toBe("D:\\b.json|u1");
  });

  it("labels all 11 db.py sorts", () => {
    expect(sortLabel("checked")).toBe("確認済み");
    expect(sortLabel("song")).toBe("曲名");
  });
});

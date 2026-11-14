import { describe, expect, it } from "vitest";
import { flagKey, flagsOf, groupFlagChanges, sameFlags } from "./undo";
import type { FlagChange } from "./types";

const fav = { favorite: 1, hidden: 0, tags: "" };
const plain = { favorite: 0, hidden: 0, tags: "" };
const hiddenTagged = { favorite: 0, hidden: 1, tags: "soft" };

const items: FlagChange[] = [
  { bank_path: "a.json", unit_id: "1", old: plain, new: fav },
  { bank_path: "a.json", unit_id: "2", old: plain, new: fav },
  { bank_path: "a.json", unit_id: "3", old: hiddenTagged, new: plain },
];

describe("flag undo helpers", () => {
  it("reads the three persisted flag fields", () => {
    expect(flagsOf({ favorite: 1, hidden: 0, tags: "soft" })).toEqual({
      favorite: 1,
      hidden: 0,
      tags: "soft",
    });
    expect(sameFlags(fav, { ...fav })).toBe(true);
    expect(flagKey(hiddenTagged)).not.toBe(flagKey(plain));
  });

  it("groups one set_flags call per distinct state", () => {
    const forward = groupFlagChanges(items, "new");
    expect(forward).toHaveLength(2);
    expect(forward.find((group) => group.state === fav)?.refs).toHaveLength(2);
    const backward = groupFlagChanges(items, "old");
    expect(backward).toHaveLength(2);
    expect(backward.find((group) => group.state === plain)?.refs).toHaveLength(2);
    expect(backward.find((group) => group.state === hiddenTagged)?.refs).toHaveLength(1);
  });
});

import { describe, expect, it } from "vitest";
import {
  clampEnd,
  clampStart,
  flattenPeaks,
  fmtTime,
  nearestEdge,
  niceStep,
  pendingCount,
  requestedUnitIndex,
  unitAt,
} from "./wave";
import type { Unit, WavePeaks } from "./types";

const unit = (start: number, end: number, name = "あ"): Unit => ({
  name,
  phonemes: ["a"],
  phoneme_starts: [start],
  start,
  end,
  confidence: "auto",
});

describe("boundary geometry", () => {
  const units = [unit(0.1, 0.2), unit(0.2, 0.3)];

  it("hits the nearest edge inside the 5 px tolerance", () => {
    expect(nearestEdge(units, 0.104, 0.005)).toEqual({ index: 0, edge: "start" });
    expect(nearestEdge(units, 0.198, 0.005)).toEqual({ index: 0, edge: "end" });
    expect(nearestEdge(units, 0.25, 0.005)).toBeNull();
  });

  it("keeps the current 5 ms minimum when clamping", () => {
    expect(clampStart(units[0], 0.1999, 10)).toBeCloseTo(0.195, 6);
    expect(clampEnd(units[0], 0.095, 10)).toBeCloseTo(0.105, 6);
    expect(clampStart(units[0], -1, 10)).toBe(0);
    expect(clampEnd(units[0], 12, 10)).toBe(10);
  });

  it("finds the unit under a time", () => {
    expect(unitAt(units, 0.15)).toBe(0);
    expect(unitAt(units, 0.2)).toBe(1);
    expect(unitAt(units, 0.31)).toBe(-1);
  });
});

describe("view helpers", () => {
  it("uses the same nice steps as gui.py", () => {
    expect(niceStep(2)).toBe(0.5);
    expect(niceStep(0.1)).toBe(0.02);
    expect(niceStep(600)).toBe(120);
    expect(fmtTime(12.345)).toBe("12.35");
    expect(fmtTime(65.2)).toBe("1:05.20");
  });

  it("interleaves min/max for wavesurfer", () => {
    const peaks: WavePeaks = { bucket: 0.001, min: [-0.2, -0.4], max: [0.3, 0.5] };
    expect(flattenPeaks(peaks)).toEqual([-0.2, 0.3, -0.4, 0.5]);
  });

  it("counts pending units", () => {
    expect(pendingCount([unit(0, 1), { ...unit(1, 2), confidence: "checked" }])).toBe(1);
  });
});

describe("requestedUnitIndex", () => {
  const units: Unit[] = [
    { ...unit(0, 1), id: "u001" },
    { ...unit(1, 2), id: "u002" },
  ];

  it("prefers the bank unit id", () => {
    expect(requestedUnitIndex(units, { path: "a.wav", unit_id: "u002", token: 1 })).toBe(1);
  });

  it("falls back to the exact range", () => {
    expect(requestedUnitIndex(units, { path: "a.wav", start: 0, end: 1, token: 1 })).toBe(0);
  });

  it("falls back to the first unit", () => {
    expect(requestedUnitIndex(units, { path: "a.wav", token: 1 })).toBe(0);
  });
});

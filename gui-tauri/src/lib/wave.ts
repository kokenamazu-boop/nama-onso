import type { PitchData, Unit, WaveOpenRequest, WavePeaks } from "./types";

export const MIN_UNIT_SECONDS = 0.005;
export const MIN_VIEW_SECONDS = 0.02;
export const UNDO_LIMIT = 50;
export type Edge = "start" | "end";

export function cloneUnits(units: Unit[]): Unit[] {
  return units.map((unit) => ({
    ...unit,
    phonemes: [...unit.phonemes],
    phoneme_starts: [...unit.phoneme_starts],
  }));
}

export function unitAt(units: Unit[], t: number): number {
  for (let i = 0; i < units.length; i += 1) {
    if (units[i].start <= t && t < units[i].end) return i;
  }
  return -1;
}

/** Same rule as gui.py on_press: nearest edge within the 5 px tolerance. */
export function nearestEdge(
  units: Unit[],
  t: number,
  tolerance: number,
): { index: number; edge: Edge } | null {
  let best = -1;
  let dist = Infinity;
  let edge: Edge = "start";
  for (let i = 0; i < units.length; i += 1) {
    for (const side of ["start", "end"] as const) {
      const d = Math.abs(units[i][side] - t);
      if (d < dist) {
        best = i;
        dist = d;
        edge = side;
      }
    }
  }
  return best >= 0 && dist <= tolerance ? { index: best, edge } : null;
}

export function clampStart(unit: Unit, t: number, duration: number): number {
  return Math.max(0, Math.min(t, unit.end - MIN_UNIT_SECONDS, duration));
}

export function clampEnd(unit: Unit, t: number, duration: number): number {
  return Math.min(duration, Math.max(t, unit.start + MIN_UNIT_SECONDS, 0));
}

export function niceStep(span: number): number {
  const target = span / 6.0;
  for (const step of [0.005, 0.01, 0.02, 0.05, 0.1, 0.2, 0.5, 1.0, 2.0, 5.0, 10.0, 30.0, 60.0]) {
    if (target <= step) return step;
  }
  return 120.0;
}

export function fmtTime(t: number): string {
  if (t >= 60) {
    const minutes = Math.floor(t / 60);
    return `${minutes}:${(t % 60).toFixed(2).padStart(5, "0")}`;
  }
  return t.toFixed(2);
}

/**
 * wavesurfer wants one float array per channel. Each sidecar bucket is a
 * (min, max) pair; interleaving them preserves the envelope at every zoom.
 */
export function flattenPeaks(peaks: WavePeaks): number[] {
  const count = Math.min(peaks.min.length, peaks.max.length);
  const out = new Array<number>(count * 2);
  for (let i = 0; i < count; i += 1) {
    out[i * 2] = peaks.min[i];
    out[i * 2 + 1] = peaks.max[i];
  }
  return out;
}

export function pendingCount(units: Unit[]): number {
  return units.filter((unit) => unit.confidence !== "checked").length;
}

export function pitchText(pitches: PitchData[], index: number): string {
  const hz = pitches[index]?.hz;
  return hz ? `${hz.toFixed(1)}Hz` : "---";
}

export function requestedUnitIndex(units: Unit[], request: WaveOpenRequest): number {
  if (!units.length) return -1;
  if (request.unit_id) {
    const byId = units.findIndex((unit) => unit.id === request.unit_id);
    if (byId >= 0) return byId;
  }
  if (request.start !== undefined && request.end !== undefined) {
    const byRange = units.findIndex(
      (unit) =>
        Math.abs(unit.start - (request.start as number)) < 1e-6 &&
        Math.abs(unit.end - (request.end as number)) < 1e-6,
    );
    if (byRange >= 0) return byRange;
  }
  return 0;
}

export type Row = {
  bank_path: string;
  unit_id: string;
  name: string;
  phonemes: string;
  start: number;
  end: number;
  midi: number | null;
  hz: number | null;
  strength: number;
  checked: number;
  source: string;
  lyrics: string;
  cons: string;
  vowel: string;
  prev_vowel: string;
  next_cons: string;
  raw_name: string;
  favorite: number;
  hidden: number;
  tags: string;
  song: string;
  display: string;
  note: string;
  ms: number;
};

export type RowRef = {
  bank_path: string;
  unit_id: string;
};

export type OpenResult = {
  db_path: string;
  folder: string;
  exists: boolean;
  banks: number;
  units: number;
};

export type BuildResult = {
  banks: number;
  skipped: number;
  removed: number;
  units: number;
  db_path: string;
  folder: string;
  banks_total: number;
  units_total: number;
};

export type SearchParams = {
  name: string;
  exact: boolean;
  cons: string;
  vowel: string;
  prev_vowel: string;
  next_cons: string;
  text: string;
  song: string;
  sort: string;
  descending: boolean;
  favorite: boolean;
  hidden: boolean | null;
  tags: string;
  min_midi: number | null;
  max_midi: number | null;
  min_ms: number | null;
};

export type SearchResult = { rows: Row[] };

export type FlagState = {
  favorite: number;
  hidden: number;
  tags: string;
};

export type FlagChange = {
  bank_path: string;
  unit_id: string;
  old: FlagState;
  new: FlagState;
};

export type RenameChange = {
  bank_path: string;
  unit_id: string;
  old: string;
  new: string;
};

export type UndoEntry =
  | { kind: "rename"; label: string; items: RenameChange[] }
  | { kind: "flags"; label: string; items: FlagChange[] };

export type DuplicateGroup = {
  name: string;
  midi: number;
  count: number;
  keep: Row;
  drop: Row[];
};

export type SourceInfo = {
  source: string;
  start: number;
  end: number;
  name: string;
  bank_path: string;
  exists: boolean;
};

export type UtauResult = {
  wav: number;
  oto: number;
  vcv: number;
  skipped: number;
  too_short: number;
  min_ms: number;
};

export type ImportResult = {
  written: number;
  skipped: number;
  files: number;
};

export type SidecarStatus = {
  running: boolean;
  python: string;
  error?: string;
};

export type PilotPaths = {
  repoRoot: string;
  dragIcon: string;
};

// ---------------------------------------------------------------- waveform
export type Unit = {
  id?: string;
  name: string;
  phonemes: string[];
  phoneme_starts: number[];
  start: number;
  end: number;
  confidence: string;
};

export type PitchData = {
  midi?: number | null;
  hz?: number | null;
  strength?: number;
  source?: string;
  curve?: unknown;
};

export type WaveFacts = {
  path: string;
  seconds: number;
  sample_rate: number;
  channels: number;
  sampwidth: number;
  frames: number;
  peak: number;
  sha256: string;
};

export type WavePeaks = {
  bucket: number;
  min: number[];
  max: number[];
};

export type WaveOpenResult = {
  facts: WaveFacts;
  bank_path: string;
  bank_exists: boolean;
  peaks: WavePeaks;
  units: Unit[];
  pitches: PitchData[];
  lyrics: string;
  model: string;
  song: string;
  bank_loaded: boolean;
};

export type WaveAlignResult = {
  units: Unit[];
  pitches: PitchData[];
  warn: string;
  note: string;
};

export type WaveOpenRequest = {
  path: string;
  bank_path?: string;
  start?: number;
  end?: number;
  unit_id?: string;
  token: number;
};

export type Theme = "light" | "dark";

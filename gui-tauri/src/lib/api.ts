import { invoke } from "@tauri-apps/api/core";
import { listen, type UnlistenFn } from "@tauri-apps/api/event";
import type { PilotPaths, SidecarStatus } from "./types";

/** One JSON Lines request through the Rust sidecar bridge. */
export function call<T>(method: string, params: object = {}): Promise<T> {
  return invoke<T>("sidecar_call", { method, params });
}

export type ProgressData = {
  phase?: string;
  folder?: string;
  index?: number;
  total?: number;
  done?: boolean;
};

export function onProgress(handler: (data: ProgressData) => void): Promise<UnlistenFn> {
  return listen<{ event: string; data: ProgressData }>("sidecar://event", (event) => {
    if (event.payload?.event === "progress") {
      handler(event.payload.data ?? {});
    }
  });
}

export function onSidecarStatus(handler: (status: SidecarStatus) => void): Promise<UnlistenFn> {
  return listen<SidecarStatus>("sidecar://status", (event) => {
    handler(event.payload);
  });
}

export function getSidecarStatus(): Promise<{ running: boolean; python: string }> {
  return invoke("sidecar_status");
}

export function getPilotPaths(): Promise<PilotPaths> {
  return invoke("pilot_paths");
}

export async function readFileBytes(path: string): Promise<ArrayBuffer> {
  const data = await invoke<ArrayBuffer | Uint8Array>("read_file_bytes", { path });
  if (data instanceof ArrayBuffer) return data;
  // Defensive: older IPC paths hand back a Uint8Array view.
  return data.slice().buffer as ArrayBuffer;
}

export function revealInExplorer(path: string): Promise<void> {
  return invoke("reveal_in_explorer", { path });
}

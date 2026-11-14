import { open, save } from "@tauri-apps/plugin-dialog";

export async function chooseFolder(title: string): Promise<string | null> {
  const picked = await open({ directory: true, multiple: false, title });
  return typeof picked === "string" ? picked : null;
}

export async function chooseSave(
  title: string,
  defaultPath: string,
  filters: { name: string; extensions: string[] }[],
): Promise<string | null> {
  const picked = await save({ title, defaultPath, filters });
  return typeof picked === "string" ? picked : null;
}

//! Thin Tauri layer for the DB-window pilot.
//!
//! Rust owns only the window, native dialogs, drag-out, and the lifetime of
//! the Python sidecar. Search / export logic stays in `sidecar/onso_sidecar.py`
//! (which imports the unchanged `db.py`).

use serde::{Deserialize, Serialize};
use serde_json::value::RawValue;
use serde_json::{json, Value};
use std::collections::HashMap;
use std::io::{BufRead, BufReader, Write};
use std::path::PathBuf;
use std::process::{Child, ChildStdin, Command, Stdio};
use std::sync::mpsc::{self, Sender};
use std::sync::{Arc, Mutex};
use std::time::Duration;
use tauri::{AppHandle, Emitter, Manager, State};

const CREATE_NO_WINDOW: u32 = 0x0800_0000;

/// A sidecar line. `result` stays raw JSON so a 4,500-row search is parsed once
/// in the webview instead of becoming a serde_json::Value tree and back.
#[derive(Deserialize)]
struct BridgeMessage {
    #[serde(default)]
    id: Option<u64>,
    #[serde(default)]
    ok: Option<bool>,
    #[serde(default)]
    result: Option<Box<RawValue>>,
    #[serde(default)]
    error: Option<String>,
    #[serde(default)]
    event: Option<String>,
    #[serde(default)]
    data: Option<Value>,
}

fn null_raw() -> Box<RawValue> {
    RawValue::from_string("null".to_string()).expect("null is valid JSON")
}

struct Inner {
    child: Option<Child>,
    stdin: Option<ChildStdin>,
    pending: HashMap<u64, Sender<Result<Box<RawValue>, String>>>,
    next_id: u64,
    python: String,
}

pub struct AppState {
    inner: Arc<Mutex<Inner>>,
    start_lock: Mutex<()>,
}

impl AppState {
    fn new() -> Self {
        Self {
            inner: Arc::new(Mutex::new(Inner {
                child: None,
                stdin: None,
                pending: HashMap::new(),
                next_id: 1,
                python: String::new(),
            })),
            start_lock: Mutex::new(()),
        }
    }

    fn shutdown(&self) {
        let (stdin, child) = {
            let mut guard = match self.inner.lock() {
                Ok(guard) => guard,
                Err(poisoned) => poisoned.into_inner(),
            };
            (guard.stdin.take(), guard.child.take())
        };
        drop(stdin);
        if let Some(mut child) = child {
            let _ = child.kill();
            let _ = child.wait();
        }
    }
}

fn repo_root() -> PathBuf {
    PathBuf::from(env!("CARGO_MANIFEST_DIR"))
        .parent()
        .and_then(|path| path.parent())
        .map(|path| path.to_path_buf())
        .unwrap_or_else(|| PathBuf::from("."))
}

fn sidecar_script() -> PathBuf {
    match std::env::var("NAMA_ONSO_SIDECAR") {
        Ok(path) if !path.trim().is_empty() => PathBuf::from(path),
        _ => repo_root().join("sidecar").join("onso_sidecar.py"),
    }
}

fn python_candidates() -> Vec<String> {
    let mut out = Vec::new();
    if let Ok(value) = std::env::var("NAMA_ONSO_PYTHON") {
        if !value.trim().is_empty() {
            out.push(value);
        }
    }
    #[cfg(windows)]
    {
        out.push("python".to_string());
        out.push("py".to_string());
    }
    #[cfg(not(windows))]
    {
        out.push("python3".to_string());
        out.push("python".to_string());
    }
    out
}

fn probe_python(candidate: &str) -> bool {
    let mut command = Command::new(candidate);
    command
        .arg("--version")
        .stdin(Stdio::null())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped());
    #[cfg(windows)]
    {
        use std::os::windows::process::CommandExt;
        command.creation_flags(CREATE_NO_WINDOW);
    }
    match command.output() {
        Ok(output) => {
            let text = String::from_utf8_lossy(&output.stdout).to_ascii_lowercase();
            output.status.success() && text.contains("python")
        }
        Err(_) => false,
    }
}

#[derive(Serialize)]
#[serde(rename_all = "camelCase")]
struct SidecarStatus {
    running: bool,
    python: String,
}

#[derive(Serialize)]
#[serde(rename_all = "camelCase")]
struct PilotPaths {
    repo_root: String,
    drag_icon: String,
}

#[tauri::command]
fn sidecar_status(state: State<'_, AppState>) -> SidecarStatus {
    let guard = match state.inner.lock() {
        Ok(guard) => guard,
        Err(poisoned) => poisoned.into_inner(),
    };
    SidecarStatus {
        running: guard.stdin.is_some(),
        python: guard.python.clone(),
    }
}

#[tauri::command]
fn pilot_paths() -> PilotPaths {
    let root = repo_root();
    let drag_icon = root
        .join("gui-tauri")
        .join("src-tauri")
        .join("icons")
        .join("drag-preview.png");
    PilotPaths {
        repo_root: root.to_string_lossy().to_string(),
        drag_icon: drag_icon.to_string_lossy().to_string(),
    }
}

/// Preview clips are small; hand the WAV bytes to WebAudio without a custom
/// asset-protocol scope.
#[tauri::command]
fn read_file_bytes(path: String) -> Result<tauri::ipc::Response, String> {
    let bytes = std::fs::read(&path).map_err(|error| format!("ファイルを読めません: {error}"))?;
    Ok(tauri::ipc::Response::new(bytes))
}

#[tauri::command]
fn reveal_in_explorer(path: String) -> Result<(), String> {
    #[cfg(windows)]
    {
        Command::new("explorer")
            .arg("/select,")
            .arg(&path)
            .spawn()
            .map_err(|error| format!("エクスプローラーを開けません: {error}"))?;
        Ok(())
    }
    #[cfg(not(windows))]
    {
        let _ = path;
        Err("エクスプローラー表示は Windows のみです。".to_string())
    }
}

fn spawn_sidecar(app: &AppHandle, inner: &Arc<Mutex<Inner>>) -> Result<(), String> {
    let script = sidecar_script();
    if !script.is_file() {
        return Err(format!("サイドカーが見つかりません: {}", script.display()));
    }
    let python = python_candidates()
        .into_iter()
        .find(|candidate| probe_python(candidate))
        .ok_or_else(|| {
            "Python 3 が見つかりません。PATH を通すか NAMA_ONSO_PYTHON を設定してください。"
                .to_string()
        })?;

    let mut command = Command::new(&python);
    command
        .arg(&script)
        .current_dir(repo_root())
        .env("PYTHONUTF8", "1")
        .env("PYTHONIOENCODING", "utf-8")
        .stdin(Stdio::piped())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped());
    #[cfg(windows)]
    {
        use std::os::windows::process::CommandExt;
        command.creation_flags(CREATE_NO_WINDOW);
    }

    let mut child = command
        .spawn()
        .map_err(|error| format!("サイドカーを起動できません: {error}"))?;
    let stdin = child
        .stdin
        .take()
        .ok_or_else(|| "サイドカーの stdin を開けません。".to_string())?;
    let stdout = child
        .stdout
        .take()
        .ok_or_else(|| "サイドカーの stdout を開けません。".to_string())?;
    let stderr = child.stderr.take();

    {
        let mut guard = inner.lock().map_err(|_| "内部状態をロックできません。".to_string())?;
        guard.child = Some(child);
        guard.stdin = Some(stdin);
        guard.python = python.clone();
    }

    let stdout_app = app.clone();
    let stdout_inner = inner.clone();
    std::thread::spawn(move || read_stdout(stdout_app, stdout_inner, stdout));
    if let Some(stderr) = stderr {
        std::thread::spawn(move || {
            for line in BufReader::new(stderr).lines().map_while(Result::ok) {
                eprintln!("[sidecar] {line}");
            }
        });
    }

    let _ = app.emit(
        "sidecar://status",
        json!({ "running": true, "python": python }),
    );
    Ok(())
}

fn read_stdout(app: AppHandle, inner: Arc<Mutex<Inner>>, stdout: std::process::ChildStdout) {
    for line in BufReader::new(stdout).lines().map_while(Result::ok) {
        let line = line.trim();
        if line.is_empty() {
            continue;
        }
        route_message(&app, &inner, line);
    }

    let (pending, child) = {
        let mut guard = match inner.lock() {
            Ok(guard) => guard,
            Err(poisoned) => poisoned.into_inner(),
        };
        guard.stdin = None;
        (std::mem::take(&mut guard.pending), guard.child.take())
    };
    if let Some(mut child) = child {
        let _ = child.wait();
    }
    for (_, sender) in pending {
        let _ = sender.send(Err("サイドカーが終了しました。".to_string()));
    }
    let _ = app.emit("sidecar://status", json!({ "running": false }));
}

fn route_message(app: &AppHandle, inner: &Arc<Mutex<Inner>>, line: &str) {
    let message: BridgeMessage = match serde_json::from_str(line) {
        Ok(message) => message,
        Err(error) => {
            eprintln!("[sidecar] stdout がJSONではありません: {error}: {line}");
            return;
        }
    };

    if let Some(id) = message.id {
        let sender = {
            let mut guard = match inner.lock() {
                Ok(guard) => guard,
                Err(poisoned) => poisoned.into_inner(),
            };
            guard.pending.remove(&id)
        };
        if let Some(sender) = sender {
            let ok = message.ok.unwrap_or(false);
            let outcome = if ok {
                Ok(message.result.unwrap_or_else(null_raw))
            } else {
                Err(message
                    .error
                    .unwrap_or_else(|| "サイドカーでエラーが発生しました。".to_string()))
            };
            let _ = sender.send(outcome);
        }
        return;
    }

    if let Some(event) = message.event {
        let _ = app.emit(
            "sidecar://event",
            json!({ "event": event, "data": message.data.unwrap_or(Value::Null) }),
        );
    }
}

fn ensure_started(app: &AppHandle, state: &AppState) -> Result<(), String> {
    {
        let guard = state
            .inner
            .lock()
            .map_err(|_| "内部状態をロックできません。".to_string())?;
        if guard.stdin.is_some() {
            return Ok(());
        }
    }
    let _start = state
        .start_lock
        .lock()
        .map_err(|_| "起動ロックを取得できません。".to_string())?;
    {
        let guard = state
            .inner
            .lock()
            .map_err(|_| "内部状態をロックできません。".to_string())?;
        if guard.stdin.is_some() {
            return Ok(());
        }
    }
    spawn_sidecar(app, &state.inner)
}

#[tauri::command]
async fn sidecar_call(
    app: AppHandle,
    state: State<'_, AppState>,
    method: String,
    params: Option<Value>,
) -> Result<Box<RawValue>, String> {
    ensure_started(&app, &state)?;
    let inner = state.inner.clone();
    drop(state);

    let (id, receiver) = {
        let mut guard = inner
            .lock()
            .map_err(|_| "内部状態をロックできません。".to_string())?;
        let id = guard.next_id;
        guard.next_id += 1;
        let (sender, receiver) = mpsc::channel();
        guard.pending.insert(id, sender);
        (id, receiver)
    };

    let payload = json!({
        "id": id,
        "method": method,
        "params": params.unwrap_or_else(|| json!({})),
    });
    let sent = {
        let mut guard = inner
            .lock()
            .map_err(|_| "内部状態をロックできません。".to_string())?;
        match guard.stdin.as_mut() {
            Some(stdin) => writeln!(stdin, "{payload}")
                .and_then(|_| stdin.flush())
                .map_err(|error| format!("サイドカーへ送信できません: {error}")),
            None => Err("サイドカーが起動していません。".to_string()),
        }
    };
    if let Err(error) = sent {
        if let Ok(mut guard) = inner.lock() {
            guard.pending.remove(&id);
        }
        return Err(error);
    }

    let long_running = method == "db.build"
        || method.starts_with("db.export")
        || method.starts_with("db.import")
        || method == "db.hide_duplicates"
        || method == "db.duplicates";
    let timeout = if long_running {
        Duration::from_secs(1800)
    } else {
        Duration::from_secs(120)
    };

    let received = tauri::async_runtime::spawn_blocking(move || receiver.recv_timeout(timeout))
        .await
        .map_err(|error| format!("サイドカー待機に失敗: {error}"))?;
    match received {
        Ok(outcome) => outcome,
        Err(_) => {
            if let Ok(mut guard) = inner.lock() {
                guard.pending.remove(&id);
            }
            Err("サイドカーの応答がタイムアウトしました。".to_string())
        }
    }
}

pub fn run() {
    let state = AppState::new();
    tauri::Builder::default()
        .plugin(tauri_plugin_dialog::init())
        .plugin(tauri_plugin_drag::init())
        .manage(state)
        .invoke_handler(tauri::generate_handler![
            sidecar_call,
            sidecar_status,
            pilot_paths,
            read_file_bytes,
            reveal_in_explorer
        ])
        .setup(|app| {
            let handle = app.handle().clone();
            std::thread::spawn(move || {
                let state = handle.state::<AppState>();
                if let Err(error) = ensure_started(&handle, &state) {
                    eprintln!("[sidecar] {error}");
                    let _ = handle.emit(
                        "sidecar://status",
                        json!({ "running": false, "error": error }),
                    );
                }
            });
            Ok(())
        })
        .build(tauri::generate_context!())
        .expect("failed to build Nama Onso DB Pilot")
        .run(|handle, event| {
            if matches!(event, tauri::RunEvent::Exit) {
                if let Some(state) = handle.try_state::<AppState>() {
                    state.shutdown();
                }
            }
        });
}

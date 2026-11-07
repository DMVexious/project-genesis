// Project Genesis desktop shell.
//
// Opens the Phaser front end in a native window and launches the Python
// orchestrator sidecar, which in turn drives the C++ engine sidecar over
// stdin/stdout. The front end talks to the orchestrator on ws://127.0.0.1:8765.
#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

use std::sync::Mutex;

use tauri::{Manager, RunEvent};
use tauri_plugin_shell::process::{CommandChild, CommandEvent};
use tauri_plugin_shell::ShellExt;

const PORT: &str = "8765";

struct Orchestrator(Mutex<Option<CommandChild>>);

fn main() {
    let app = tauri::Builder::default()
        .plugin(tauri_plugin_shell::init())
        .setup(|app| {
            let pid = std::process::id().to_string();
            // --parent-pid makes the orchestrator exit by itself if this process
            // dies without running the Exit handler below (e.g. killed).
            let (mut rx, child) = app
                .shell()
                .sidecar("orchestrator")?
                .args(["--port", PORT, "--parent-pid", pid.as_str()])
                .spawn()?;
            app.manage(Orchestrator(Mutex::new(Some(child))));

            tauri::async_runtime::spawn(async move {
                while let Some(event) = rx.recv().await {
                    match event {
                        CommandEvent::Stdout(line) | CommandEvent::Stderr(line) => {
                            eprintln!("[orchestrator] {}", String::from_utf8_lossy(&line).trim_end());
                        }
                        CommandEvent::Terminated(status) => {
                            eprintln!("[orchestrator] exited with {:?}", status.code);
                            break;
                        }
                        _ => {}
                    }
                }
            });
            Ok(())
        })
        .build(tauri::generate_context!())
        .expect("failed to build Project Genesis");

    app.run(|handle, event| {
        if let RunEvent::Exit = event {
            if let Some(child) = handle.state::<Orchestrator>().0.lock().unwrap().take() {
                let _ = child.kill();
            }
        }
    });
}

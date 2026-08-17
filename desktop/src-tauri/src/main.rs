// AgentState Guard Desktop — Tauri 2 entry point
//
// On startup:
//   1. Determine app data directory
//   2. Spawn the Python sidecar (agentguard-sidecar)
//   3. Wait for sidecar readiness
//   4. Serve the frontend
//
// On shutdown:
//   1. Terminate sidecar
//   2. Verify no orphan process

#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

mod firewall_helper;
mod sidecar;

use sidecar::SidecarProcess;
use std::sync::Mutex;
use tauri::Manager;

#[tauri::command]
fn get_sidecar_state(state: tauri::State<SidecarProcess>) -> Result<sidecar::SidecarState, String> {
    let guard = state.0.lock().map_err(|e| e.to_string())?;
    match guard.as_ref() {
        Some(child) => Ok(sidecar::SidecarState {
            pid: child.id(),
            port: 8787,
            ready: true,
        }),
        None => Err("Sidecar not started".into()),
    }
}

fn main() {
    if let Some(exit_code) = firewall_helper::run_if_requested() {
        std::process::exit(exit_code);
    }
    tauri::Builder::default()
        .plugin(tauri_plugin_shell::init())
        .manage(SidecarProcess(Mutex::new(None)))
        .setup(|app| {
            // Resolve app data directory for sidecar
            let home = dirs_next::data_local_dir()
                .unwrap_or_else(|| std::path::PathBuf::from("."))
                .join("AgentState Guard");

            // Create data directory if it doesn't exist
            std::fs::create_dir_all(&home)
                .expect("Failed to create app data directory");

            let data_dir = home.to_string_lossy().to_string();
            eprintln!("[tauri] data_dir={}", data_dir);

            // Spawn sidecar
            let (child, pid) = sidecar::spawn(&data_dir)
                .expect("Failed to spawn sidecar process");

            // Store the child process handle for cleanup
            let state = app.state::<SidecarProcess>();
            *state.0.lock().unwrap() = Some(child);

            eprintln!("[tauri] sidecar ready PID={}", pid);

            Ok(())
        })
        .on_window_event(|window, event| {
            if let tauri::WindowEvent::Destroyed = event {
                // Cleanup sidecar on window close
                let app_handle = window.app_handle();
                let state = app_handle.state::<SidecarProcess>();
                let mut guard = state.0.lock().unwrap();
                if let Some(mut child) = guard.take() {
                    eprintln!("[tauri] shutting down sidecar...");
                    let _ = sidecar::terminate(&mut child);
                }
            }
        })
        .run(tauri::generate_context!())
        .expect("error while running AgentState Guard");
}

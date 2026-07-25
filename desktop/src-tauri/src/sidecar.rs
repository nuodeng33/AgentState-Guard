// AgentState Guard Desktop — Sidecar lifecycle manager
//
// Spawns the Python backend (agentguard-sidecar), waits for readiness,
// and handles clean shutdown on app exit.

use serde::Serialize;
use std::path::PathBuf;
use std::process::{Child, Command, Stdio};
use std::sync::Mutex;
use tauri::Manager;

const SIDECAR_PORT: u16 = 8787;
const SIDECAR_HOST: &str = "127.0.0.1";

#[derive(Serialize, Clone)]
pub struct SidecarState {
    pub pid: u32,
    pub port: u16,
    pub ready: bool,
}

pub struct SidecarProcess(pub Mutex<Option<Child>>);

/// Resolve the sidecar binary relative to the current executable,
/// not the working directory. This works whether the app is run
/// from a build directory, an artifact download, or an MSI install.
fn resolve_sidecar() -> Result<PathBuf, String> {
    let exe_dir = std::env::current_exe()
        .map_err(|e| format!("Failed to get executable path: {}", e))?
        .parent()
        .ok_or_else(|| "Executable has no parent directory".to_string())?
        .to_path_buf();

    // On Windows Tauri 2 appends the target triple to externalBin names.
    // Try the triple-suffixed variant first, then the plain name.
    let candidates: &[&str] = if cfg!(target_os = "windows") {
        &[
            "agentguard-sidecar-x86_64-pc-windows-msvc.exe",
            "agentguard-sidecar.exe",
        ]
    } else if cfg!(target_os = "macos") {
        &[
            "agentguard-sidecar-aarch64-apple-darwin",
            "agentguard-sidecar-x86_64-apple-darwin",
            "agentguard-sidecar",
        ]
    } else {
        &[
            "agentguard-sidecar-x86_64-unknown-linux-gnu",
            "agentguard-sidecar",
        ]
    };

    for name in candidates {
        let path = exe_dir.join(name);
        if path.exists() {
            eprintln!("[sidecar] resolved: {}", path.display());
            return Ok(path);
        }
    }

    // Diagnostic: list the directory contents
    eprintln!("[sidecar] ERROR: not found in {}", exe_dir.display());
    if let Ok(entries) = std::fs::read_dir(&exe_dir) {
        for entry in entries.flatten() {
            eprintln!("[sidecar]   {}", entry.file_name().to_string_lossy());
        }
    }

    Err(format!("Sidecar binary not found in {}", exe_dir.display()))
}

/// Spawn the sidecar process. Readiness is verified externally by the
/// CI smoke test (TcpClient check) — not by blocking inside setup().
pub fn spawn(data_dir: &str) -> Result<(Child, u32), String> {
    let sidecar_path = resolve_sidecar()?;
    let child = Command::new(&sidecar_path)
        .arg("--directory")
        .arg(data_dir)
        .arg("serve")
        .arg("--host")
        .arg(SIDECAR_HOST)
        .arg("--port")
        .arg(SIDECAR_PORT.to_string())
        .stdout(Stdio::inherit())
        .stderr(Stdio::inherit())
        .spawn()
        .map_err(|e| format!("Failed to spawn sidecar: {}", e))?;

    let pid = child.id();
    eprintln!("[sidecar] spawned PID={}", pid);
    Ok((child, pid))
}

/// Terminate a sidecar process and wait for it to exit.
pub fn terminate(child: &mut Child) -> Result<(), String> {
    let pid = child.id();
    eprintln!("[sidecar] terminating PID={}", pid);

    #[cfg(windows)]
    {
        // On Windows, use taskkill to ensure the entire process tree is killed
        let _ = Command::new("taskkill")
            .args(["/F", "/T", "/PID", &pid.to_string()])
            .output();
    }
    #[cfg(not(windows))]
    {
        let _ = child.kill();
    }

    // Wait for process to exit
    let deadline = std::time::Instant::now() + Duration::from_secs(5);
    loop {
        match child.try_wait() {
            Ok(Some(status)) => {
                eprintln!("[sidecar] exited with status {}", status);
                return Ok(());
            }
            Ok(None) => {}
            Err(e) => {
                eprintln!("[sidecar] wait error: {}", e);
                return Ok(());
            }
        }
        if std::time::Instant::now() > deadline {
            eprintln!("[sidecar] WARNING: process did not exit within 5s, continuing");
            return Ok(());
        }
        std::thread::sleep(Duration::from_millis(100));
    }
}

// AgentState Guard Desktop — Sidecar lifecycle manager
//
// Spawns the Python backend (agentguard-sidecar), waits for readiness,
// and handles clean shutdown on app exit.

use serde::Serialize;
use std::process::{Child, Command, Stdio};
use std::sync::Mutex;
use std::time::Duration;
use tauri::Manager;

const SIDECAR_BIN: &str = "bin/agentguard-sidecar";
const SIDECAR_PORT: u16 = 8787;
const SIDECAR_HOST: &str = "127.0.0.1";
const READINESS_TIMEOUT_SECS: u64 = 15;
const READINESS_POLL_MS: u64 = 200;

#[derive(Serialize, Clone)]
pub struct SidecarState {
    pub pid: u32,
    pub port: u16,
    pub ready: bool,
}

pub struct SidecarProcess(pub Mutex<Option<Child>>);

/// Spawn the sidecar process and wait for it to become ready.
pub fn spawn(data_dir: &str) -> Result<(Child, u32), String> {
    let port = SIDECAR_PORT;
    let host = SIDECAR_HOST;

    let child = Command::new(SIDECAR_BIN)
        .arg("--directory")
        .arg(data_dir)
        .arg("serve")
        .arg("--host")
        .arg(host)
        .arg("--port")
        .arg(port.to_string())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped())
        .spawn()
        .map_err(|e| format!("Failed to spawn sidecar: {}", e))?;

    let pid = child.id();
    eprintln!("[sidecar] spawned PID={}", pid);

    // Wait for readiness
    let url = format!("http://{}:{}/api/health", host, port);
    let deadline = std::time::Instant::now() + Duration::from_secs(READINESS_TIMEOUT_SECS);

    loop {
        if std::time::Instant::now() > deadline {
            return Err(format!(
                "SIDECAR_READINESS_TIMEOUT: {} not ready after {}s",
                url, READINESS_TIMEOUT_SECS
            ));
        }
        match ureq::get(&url).call() {
            Ok(resp) if resp.status() == 200 => {
                eprintln!("[sidecar] health OK (PID={})", pid);
                return Ok((child, pid));
            }
            _ => {}
        }
        std::thread::sleep(Duration::from_millis(READINESS_POLL_MS));
    }
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

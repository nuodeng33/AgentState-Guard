use std::net::Ipv4Addr;
use std::process::Command;
use std::str::FromStr;

const TCP_RULE: &str = "AgentState Guard Device Link TCP 8788";
const UDP_RULE: &str = "AgentState Guard Device Link UDP 8788";
const HELPER_FLAG: &str = "--asg-firewall-helper";

// Windows CREATE_NO_WINDOW: the packaged GUI helper must never flash visible
// console windows for its bounded powershell/netsh children. The constant is
// ungated so its exact value stays testable on every platform; only the
// application to a Command is Windows-specific.
const CREATE_NO_WINDOW: u32 = 0x0800_0000;

#[cfg(windows)]
fn configure_no_console(command: &mut Command) {
    use std::os::windows::process::CommandExt;
    command.creation_flags(CREATE_NO_WINDOW);
}

const NETWORK_CHECK: &str = r#"
$ErrorActionPreference = 'Stop'
$targetAddress = [string]$env:ASG_FW_ADDRESS
$targetPrefix = [int]$env:ASG_FW_PREFIX
$ips = @(Get-NetIPAddress -AddressFamily IPv4 -AddressState Preferred -ErrorAction Stop |
    Where-Object { $_.IPAddress -eq $targetAddress -and $_.PrefixLength -eq $targetPrefix })
if ($ips.Count -ne 1) { exit 21 }
$index = $ips[0].InterfaceIndex
$adapters = @(Get-NetAdapter -Physical -InterfaceIndex $index -ErrorAction Stop |
    Where-Object { $_.Status -eq 'Up' })
$routes = @(Get-NetRoute -AddressFamily IPv4 -DestinationPrefix '0.0.0.0/0' -InterfaceIndex $index -ErrorAction Stop)
$profiles = @(Get-NetConnectionProfile -InterfaceIndex $index -ErrorAction Stop |
    Where-Object { $_.NetworkCategory -eq 'Private' })
if ($adapters.Count -ne 1 -or $routes.Count -lt 1 -or $profiles.Count -ne 1) { exit 21 }
exit 0
"#;

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
enum Operation {
    Apply,
    Remove,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
struct HelperRequest {
    operation: Operation,
    address: Ipv4Addr,
    prefix: u8,
}

pub fn run_if_requested() -> Option<i32> {
    let arguments: Vec<String> = std::env::args().skip(1).collect();
    if arguments.first().map(String::as_str) != Some(HELPER_FLAG) {
        return None;
    }
    Some(match parse_request(&arguments) {
        Some(request) => run_request(request),
        None => 10,
    })
}

fn parse_request(arguments: &[String]) -> Option<HelperRequest> {
    if arguments.len() != 6
        || arguments[0] != HELPER_FLAG
        || arguments[2] != "--address"
        || arguments[4] != "--prefix"
    {
        return None;
    }
    let operation = match arguments[1].as_str() {
        "apply" => Operation::Apply,
        "remove" => Operation::Remove,
        _ => return None,
    };
    let address = Ipv4Addr::from_str(&arguments[3]).ok()?;
    let prefix = arguments[5].parse::<u8>().ok()?;
    if !(1..=30).contains(&prefix) || !is_private(address) {
        return None;
    }
    Some(HelperRequest {
        operation,
        address,
        prefix,
    })
}

fn run_request(request: HelperRequest) -> i32 {
    #[cfg(not(windows))]
    {
        let _ = request;
        return 20;
    }
    #[cfg(windows)]
    {
        if request.operation == Operation::Apply && !network_scope_is_current(request) {
            return 21;
        }
        match request.operation {
            Operation::Apply => apply_rules(request),
            Operation::Remove => remove_rules(),
        }
    }
}

#[cfg(windows)]
fn network_scope_is_current(request: HelperRequest) -> bool {
    let mut command = Command::new("powershell.exe");
    command
        .args(["-NoProfile", "-NonInteractive", "-Command", NETWORK_CHECK])
        .env("ASG_FW_ADDRESS", request.address.to_string())
        .env("ASG_FW_PREFIX", request.prefix.to_string());
    configure_no_console(&mut command);
    command
        .status()
        .map(|status| status.code() == Some(0))
        .unwrap_or(false)
}

#[cfg(windows)]
fn apply_rules(request: HelperRequest) -> i32 {
    let _ = delete_rule(TCP_RULE);
    let _ = delete_rule(UDP_RULE);
    let subnet = subnet(request.address, request.prefix);
    let tcp = add_rule(
        TCP_RULE,
        "TCP",
        request.address,
        request.prefix,
        subnet,
    );
    let udp = add_rule(
        UDP_RULE,
        "UDP",
        request.address,
        request.prefix,
        subnet,
    );
    if tcp && udp {
        0
    } else {
        let _ = delete_rule(TCP_RULE);
        let _ = delete_rule(UDP_RULE);
        30
    }
}

#[cfg(windows)]
fn remove_rules() -> i32 {
    let tcp = delete_rule(TCP_RULE);
    let udp = delete_rule(UDP_RULE);
    if tcp && udp {
        0
    } else {
        30
    }
}

#[cfg(windows)]
fn add_rule(
    name: &str,
    protocol: &str,
    address: Ipv4Addr,
    prefix: u8,
    network: Ipv4Addr,
) -> bool {
    let mut command = Command::new("netsh.exe");
    command.args(add_rule_args(name, protocol, address, prefix, network));
    configure_no_console(&mut command);
    command
        .status()
        .map(|status| status.success())
        .unwrap_or(false)
}

#[cfg(windows)]
fn delete_rule(name: &str) -> bool {
    let mut command = Command::new("netsh.exe");
    command
        .args(["advfirewall", "firewall", "delete", "rule"])
        .arg(format!("name={name}"));
    configure_no_console(&mut command);
    command
        .status()
        .map(|status| status.success())
        .unwrap_or(false)
}

#[cfg(windows)]
fn add_rule_args(
    name: &str,
    protocol: &str,
    address: Ipv4Addr,
    prefix: u8,
    network: Ipv4Addr,
) -> Vec<String> {
    vec![
        "advfirewall".into(),
        "firewall".into(),
        "add".into(),
        "rule".into(),
        format!("name={name}"),
        "dir=in".into(),
        "action=allow".into(),
        format!("protocol={protocol}"),
        "localport=8788".into(),
        format!("localip={address}"),
        format!("remoteip={network}/{prefix}"),
        "profile=private".into(),
        "enable=yes".into(),
    ]
}

fn subnet(address: Ipv4Addr, prefix: u8) -> Ipv4Addr {
    let mask = u32::MAX << (32 - u32::from(prefix));
    Ipv4Addr::from(u32::from(address) & mask)
}

fn is_private(address: Ipv4Addr) -> bool {
    let octets = address.octets();
    octets[0] == 10
        || (octets[0] == 172 && (16..=31).contains(&octets[1]))
        || (octets[0] == 192 && octets[1] == 168)
}

#[cfg(test)]
mod tests {
    use super::*;

    fn args(operation: &str, address: &str, prefix: &str) -> Vec<String> {
        [
            HELPER_FLAG,
            operation,
            "--address",
            address,
            "--prefix",
            prefix,
        ]
        .iter()
        .map(|value| value.to_string())
        .collect()
    }

    #[test]
    fn accepts_only_fixed_private_scope() {
        assert!(parse_request(&args("apply", "192.168.50.8", "24")).is_some());
        assert!(parse_request(&args("remove", "10.0.0.5", "8")).is_some());
        assert!(parse_request(&args("exec", "192.168.50.8", "24")).is_none());
        assert!(parse_request(&args("apply", "198.18.0.1", "24")).is_none());
        assert!(parse_request(&args("apply", "192.168.50.8", "32")).is_none());
    }

    #[test]
    fn computes_exact_remote_subnet() {
        assert_eq!(
            subnet(Ipv4Addr::new(192, 168, 50, 8), 24),
            Ipv4Addr::new(192, 168, 50, 0)
        );
        assert_eq!(
            subnet(Ipv4Addr::new(172, 20, 14, 9), 20),
            Ipv4Addr::new(172, 20, 0, 0)
        );
    }

    #[test]
    fn no_console_flag_is_windows_create_no_window() {
        // The GUI helper's powershell/netsh children must use the exact
        // Windows CREATE_NO_WINDOW value; pinned ungated so every platform
        // compiles and runs this contract.
        assert_eq!(CREATE_NO_WINDOW, 0x0800_0000);
    }

    #[cfg(windows)]
    #[test]
    fn add_rule_surface_stays_fixed_and_scoped() {
        let argv = add_rule_args(
            TCP_RULE,
            "TCP",
            Ipv4Addr::new(192, 168, 50, 8),
            24,
            Ipv4Addr::new(192, 168, 50, 0),
        );
        let joined = argv.join(" ");
        assert!(joined.contains("advfirewall firewall add rule"));
        assert!(joined.contains("dir=in"));
        assert!(joined.contains("action=allow"));
        assert!(joined.contains("localport=8788"));
        assert!(joined.contains("localip=192.168.50.8"));
        assert!(joined.contains("remoteip=192.168.50.0/24"));
        assert!(joined.contains("profile=private"));
        assert!(joined.contains("enable=yes"));
        assert!(!joined.contains("exec"));
        assert!(!joined.contains("delete"));
    }
}

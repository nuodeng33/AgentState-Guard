"""Server-owned Unix scope receipts; no API field or boolean grants authority."""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from pathlib import Path

SCHEMA = "unix-scope-proof-1"
CHECKS = frozenset({"network_control", "write_inside", "write_outside", "write_network",
                    "readonly_inside", "readonly_outside", "readonly_network"})
_ISSUER = object()


class ScopeReadinessError(RuntimeError):
    def __init__(self, reason_code):
        super().__init__(reason_code)
        self.reason_code = reason_code


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()).hexdigest()


def profiles(challenge):
    if not isinstance(challenge, str) or not re.fullmatch(r"[0-9a-f]{32}", challenge):
        raise ScopeReadinessError("EXECUTION_SCOPE_PROOF_INVALID")
    return {
        "asg_ro_" + challenge: {"extends": ":read-only", "network": {"enabled": False}},
        "asg_write_" + challenge: {"extends": ":read-only", "filesystem": {":workspace_roots": "write"},
                                   "network": {"enabled": False}},
    }


def profile_arguments(challenge):
    ro, write = profiles(challenge)
    return ["-c", 'default_permissions="' + ro + '"',
            "-c", 'permissions.' + ro + '={extends=":read-only",network={enabled=false}}',
            "-c", 'permissions.' + write + '={extends=":read-only",filesystem={":workspace_roots"="write"},network={enabled=false}}',
            "-c", 'features.apps=false', "-c", 'features.multi_agent=false',
            "-c", 'features.remote_control=false', "-c", 'features.remote_plugin=false',
            "-c", 'web_search="disabled"']


def _without_nulls(value):
    if isinstance(value, dict):
        return {key: _without_nulls(item) for key, item in value.items() if item is not None}
    return value


@dataclass(frozen=True)
class UnixScopeProof:
    identity_digest: str
    source_digest: str
    launch_digest: str
    challenge: str
    profile_digest: str
    version: str
    root: str
    _issuer: object = field(repr=False, compare=False)

    def authentic(self):
        return self._issuer is _ISSUER and self.profile_digest == digest(profiles(self.challenge))

    def matches(self, identity, launch):
        return (self.authentic() and self.identity_digest == digest(identity)
                and self.launch_digest == digest(launch)
                and self.profile_digest == digest(profiles(self.challenge))
                and self.root == identity.get("binding", {}).get("root"))

    def profile(self, read_only):
        return ("asg_ro_" if read_only else "asg_write_") + self.challenge

    def validate_configuration(self, configuration):
        effective = configuration.get("permissions")
        if (not isinstance(effective, dict)
                or configuration.get("default_permissions") != self.profile(True)):
            raise ScopeReadinessError("EXECUTION_PROVIDER_PROFILE_UNVERIFIED")
        for name, expected in profiles(self.challenge).items():
            if _without_nulls(effective.get(name)) != expected:
                raise ScopeReadinessError("EXECUTION_PROVIDER_PROFILE_UNVERIFIED")
        features = configuration.get("features", {})
        if (not isinstance(features, dict) or any(features.get(name) is not False
                for name in ("apps", "multi_agent", "remote_control", "remote_plugin"))
                or configuration.get("web_search") != "disabled"):
            raise ScopeReadinessError("EXECUTION_PROVIDER_CONFIG_UNVERIFIED")


@dataclass(frozen=True)
class PreparedExecution:
    launch: dict = field(repr=False)
    proof: UnixScopeProof | None = None


def issue_proof(identity, source, launch, report, challenge):
    """Only the registry calls this after before/after selection verification."""
    expected_profiles = profiles(challenge)
    if not isinstance(report, dict) or not isinstance(report.get("checks"), dict):
        raise ScopeReadinessError("EXECUTION_SCOPE_PROOF_INVALID")
    version = report.get("version")
    binding = identity.get("binding", {})
    if (source.get("kind") != "WSL" or binding.get("kind") != "WSL"
            or source.get("connected") is not True or source.get("running") is not True
            or binding.get("epoch") != source.get("epoch")
            or binding.get("identity", {}).get("guid") != source.get("guid")
            or binding.get("identity", {}).get("disk_identity") != source.get("disk_identity")
            or launch.get("argv", [])[-len(profile_arguments(challenge)):] != profile_arguments(challenge)
            or not source.get("guid") or not source.get("disk_identity")
            or not source.get("epoch") or source.get("source_id") != identity.get("binding", {}).get("source_id")
            or report.get("schema_version") != SCHEMA or report.get("challenge") != challenge
            or report.get("profile_digest") != digest(expected_profiles)
            or report.get("root_identity") != identity.get("binding", {}).get("identity", {}).get("root")
            or report.get("cleanup") is not True or set(report.get("checks", {})) != CHECKS
            or any(value is not True for value in report.get("checks", {}).values())
            or not isinstance(version, str) or len(version) > 128
            or not re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+(?:[-+][A-Za-z0-9.-]+)?", version)):
        raise ScopeReadinessError("EXECUTION_SCOPE_PROOF_INVALID")
    return UnixScopeProof(digest(identity), digest(source), digest(launch), challenge,
                          digest(expected_profiles), version, identity["binding"]["root"], _ISSUER)


PROBE_BOOTSTRAP = """import hashlib,json,sys
line=sys.stdin.buffer.readline(131073)
if not line.endswith(b'\\n') or len(line)>131072: sys.exit(2)
source=json.loads(line)
if not isinstance(source,str) or hashlib.sha256(source.encode()).hexdigest()!=sys.argv[1]: sys.exit(2)
namespace={'__name__':'asg_scope_probe'}
exec(compile(source,'agentguard/execution/scope_probe.py','exec'),namespace)
namespace['main']()
"""


def probe_source():
    from . import scope_probe

    path = Path(scope_probe.__file__).with_suffix(".py")
    try:
        if path.is_symlink():
            raise OSError()
        raw = path.read_bytes()
        if not raw or len(raw) > 65536:
            raise OSError()
        return raw.decode("utf-8")
    except (OSError, UnicodeDecodeError) as error:
        raise ScopeReadinessError("EXECUTION_SCOPE_HELPER_UNAVAILABLE") from error

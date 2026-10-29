"""Fail-closed APK signer gate; never handles signing private material."""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
from pathlib import Path


class SignerVerificationError(ValueError):
    pass

def verify_signer_output(output: str, expected: str) -> str:
    if not re.fullmatch(r"[0-9a-fA-F]{64}", expected):
        raise SignerVerificationError("ANDROID_EXPECTED_SIGNER_INVALID")
    rows = [line for line in output.splitlines() if re.match(r"^Signer #\d+ certificate SHA-256 digest:", line)]
    if len(rows) != 1:
        raise SignerVerificationError("ANDROID_SIGNER_COUNT_INVALID")
    match = re.fullmatch(r"Signer #\d+ certificate SHA-256 digest:\s*([0-9a-fA-F]{64})\s*", rows[0])
    if match is None or match.group(1).lower() != expected.lower():
        raise SignerVerificationError("ANDROID_SIGNER_MISMATCH")
    return match.group(1).lower()

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apksigner", type=Path, required=True)
    parser.add_argument("--java", default="java")
    parser.add_argument("--certificate", type=Path, required=True)
    parser.add_argument("--apk", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    report = {"result": "FAIL", "apk": args.apk.name}
    try:
        expected = args.certificate.read_text(encoding="utf-8").strip()
        command = [args.java, "-jar", str(args.apksigner)] if args.apksigner.suffix == ".jar" else [str(args.apksigner)]
        result = subprocess.run([*command, "verify", "--print-certs", str(args.apk)],
                                capture_output=True, text=True, timeout=30, check=False)
        if result.returncode:
            raise SignerVerificationError("ANDROID_APK_SIGNATURE_INVALID")
        report["certificate_sha256"] = verify_signer_output(result.stdout, expected)
        with args.apk.open("rb") as stream:
            report["apk_sha256"] = hashlib.file_digest(stream, "sha256").hexdigest()
        report["result"] = "PASS"
    except (OSError, subprocess.SubprocessError, ValueError) as error:
        report["reason_code"] = str(error) if isinstance(error, SignerVerificationError) else "ANDROID_SIGNER_CHECK_UNAVAILABLE"
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report))
    return 0 if report["result"] == "PASS" else 1

if __name__ == "__main__":
    raise SystemExit(main())

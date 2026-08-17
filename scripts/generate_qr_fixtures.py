#!/usr/bin/env python3
"""Generate the QR matrix fixtures consumed by the JVM QrScannerTest.

Each fixture is a self-describing text file:

    # payload: <the exact QR text>
    <rows of 0/1 characters; 1 = dark module>

The Kotlin fixture loader renders this module matrix with a quiet zone into
a luminance grid, then feeds the production ZXing engine — so the test
exercises a real independent-encoder -> production-decoder round trip
without binary images, zbar, or an emulator.

The `pub` / `sign_fp` values below are a fixed, throwaway P-256 key whose
SHA-256 fingerprint matches the fixture contract; they are not credentials
and exist only so the strict QrPayload parser accepts the fixture text.

Usage:  python scripts/generate_qr_fixtures.py
Output: android/app/src/test/resources/qr/<name>.matrix.txt
"""
import hashlib
import os
import sys

import qrcode

FIXED_NOW = 1_900_000_000
VALID_EXPIRY = FIXED_NOW + 600
EXPIRED_EXPIRY = FIXED_NOW - 60

OUT_DIR = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "..",
    "android", "app", "src", "test", "resources", "qr",
)

# Fixed throwaway P-256 public key (SPKI DER, hex); generated for the fixture.
TEST_PUBLIC_DER_HEX = (
    "3059301306072a8648ce3d020106082a8648ce3d0301070342004104a890de98"
    "2838fe0bf3c57e81f401d41775738a1e12e60a7b1c82cc8f8d8d6e5748e6342a4"
    "30fa65876d6601d929e51d1ff215408f78f45824868ece1f2a04dcb1e29e5"
)
TEST_SIGN_FINGERPRINT = hashlib.sha256(bytes.fromhex(TEST_PUBLIC_DER_HEX)).hexdigest()
TEST_TLS_FINGERPRINT = hashlib.sha256(b"fixture-tls").hexdigest()


def matrix_of(text):
    qr = qrcode.QRCode(
        version=None,
        error_correction=qrcode.constants.ERROR_CORRECT_M,
        box_size=1,
        border=0,
    )
    qr.add_data(text)
    qr.make(fit=True)
    return qr.get_matrix()


def write(name, text):
    matrix = matrix_of(text)
    path = os.path.join(os.path.abspath(OUT_DIR), f"{name}.matrix.txt")
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(f"# payload: {text}\n")
        for row in matrix:
            handle.write("".join("1" if cell else "0" for cell in row) + "\n")
    return len(matrix), len(text)


def invitation(expiry):
    return (
        "agentstate://pair?v=1&host=192.168.1.5&port=8788&uuid=desktop-test01"
        f"&pub={TEST_PUBLIC_DER_HEX}"
        f"&sign_fp={TEST_SIGN_FINGERPRINT}"
        f"&tls_fp={TEST_TLS_FINGERPRINT}"
        "&sid=cdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcd"
        "&ticket=efefefefefefefefefefefefefefefefefefefefefefefefefefefefefefefef"
        f"&exp={expiry}"
    )


def main():
    os.makedirs(os.path.abspath(OUT_DIR), exist_ok=True)
    valid = invitation(VALID_EXPIRY)
    cases = {
        "valid_invitation": valid,
        "expired_invitation": invitation(EXPIRED_EXPIRY),
        "tampered_port": valid.replace("port=8788", "port=8878"),
        "tampered_uuid": valid.replace("uuid=desktop-test01", "uuid=../../etc"),
        "tampered_version": valid.replace("?v=1&", "?v=2&", 1),
        "tampered_sign_fp": valid.replace(
            "sign_fp=" + TEST_SIGN_FINGERPRINT,
            "sign_fp=" + ("0" * 64),
        ),
        "truncated": valid[:-8],
        "foreign_https": "https://attacker.example/pair?ticket=steal",
        "foreign_wifi": "WIFI:T:WPA;S:cafe;P:hunter2;;",
        "foreign_otpauth": "otpauth://totp/issuer:acct?secret=ABC",
    }
    for name, text in cases.items():
        modules, length = write(name, text)
        print(f"{name}: {modules}x{modules} modules, {length} chars", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
"""JVM-test QR decode engine: PNG on stdin, decoded text on stdout.

Exit 0 + text when a QR is decoded; exit 0 + empty stdout otherwise.
Used only by unit tests through SubprocessQrDecodeEngine; the production
engine is ZXing on the Android/ImageProxy frame directly.
"""
import io
import os
import signal
import sys


def main() -> int:
    signal.alarm(int(os.environ.get("ASG_TEST_QR_ENGINE_TIMEOUT", "15")))
    from PIL import Image
    from pyzbar.pyzbar import decode

    data = sys.stdin.buffer.read()
    try:
        image = Image.open(io.BytesIO(data))
    except Exception:
        return 1
    results = decode(image)
    if results:
        sys.stdout.write(results[0].data.decode("utf-8", "replace"))
    return 0


if __name__ == "__main__":
    sys.exit(main())

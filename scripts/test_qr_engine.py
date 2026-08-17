#!/usr/bin/env python3
"""Optional second decoder for the JVM scanner boundary test.

Reads a PNG grayscale frame on stdin (produced by the test PngEncoder),
decodes the QR with OpenCV, and prints the payload text on stdout (empty
when no QR is found). The unit test only uses this engine when the
ASG_TEST_QR_ENGINE_COMMAND environment variable points here, e.g.:

    export ASG_TEST_QR_ENGINE_COMMAND="python3 scripts/test_qr_engine.py"

It exists so the scanner contract is verified on a decoder that is fully
independent of the production ZXing engine.
"""
import sys

import cv2
import numpy as np


def main() -> int:
    data = sys.stdin.buffer.read()
    image = cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_GRAYSCALE)
    if image is None:
        return 0
    text, _points, _ = cv2.QRCodeDetector().detectAndDecode(image)
    if text:
        sys.stdout.write(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())

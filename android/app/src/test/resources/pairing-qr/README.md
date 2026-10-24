# Dense desktop invitation raster regression

These PNGs are unmodified screenshots of the actual React `QrCodeCard` and its application token/QR CSS, rendered in sandboxed Chrome on Windows. The synthetic expired invitation has the canonical field shape and 547 bytes (89 QR modules); its key and ticket grant no authority. The fixed source and CSS SHA-256 values, PNG hashes and browser geometry are in `provenance.json`.

The matrix covers integer CSS cell sizes 3, 4 and 5, and DPR 1, 1.25, 1.5 and 2. The test uses the product pixel decoder unchanged and compares the exact original text. Real-time protocol validity remains the responsibility of the strict invitation parser and mutual-auth pairing flow.

The real MuMu regression was first reproduced on the unchanged 0c88 package. For the same real expired invitation, a 180-CSS-pixel card failed the exact Android Java detector and the natural 461-CSS-pixel SVG decoded the identical payload SHA. That private real invitation is intentionally not part of these public fixtures. A synthetic legacy pattern may decode successfully; these fixtures do not claim that every legacy QR must fail.

## Android JVM fixture transport

Android unit-test Kotlin compilation excludes Java desktop ImageIO. `*.argb.gz` therefore stores the **identical unmodified PNG pixels**, not a resized/rebinarized QR. Run the offline `scripts/export_qr_argb_fixtures.py` with the resources directory and bundled Python/Pillow to create fresh files (it refuses overwrites). No product dependency is added.

The decompressed stream is big-endian `ASG1`, width, height, then row-major 32-bit A,R,G,B pixel words; gzip mtime is zero. `argb-manifest.json` distinguishes original PNG, raw pixel words, complete raw stream and gzip hashes. Native Java independently compared every exported pixel with ImageIO's original PNG ARGB values and decoded all twelve exactly. JVM tests bind the original PNG/gzip hashes, validate header/dimensions before allocation, require exact EOF/CRC and compare the full raw-stream digest before invoking the unchanged product decoder. This is pixel-decoder compatibility; actual OpenDocument/BitmapFactory remains separately tested in MuMu.

# Phase 25 — dense invitation QR and native acceptance

Requirement ASG-CLOSE-004. Baseline product `0c88bd935b3b2dea8e91b99f83716c26012972c3`.

## Intent and tasks

- [x] Reproduce Android image-import failure on the actual final CI APK in MuMu 1 and retain first failures. Active form: investigating dense QR compatibility.
- [x] Independent design and gate review; verify same-payload causal browser geometry with the exact Android Java decoder.
- [x] Implement intrinsic integer module sizing, four-module quiet zone and responsive integer sizing; preserve protocol/parser/security authority. Fix the independently traced stale first-frame presentation clock.
- [x] Independent complete frontend suite/typecheck/production build; exact decoder matrix across 12 real component screenshots.
- [ ] Verify the next exact product commit in all four CI workflows; compile/test the new Android raster regression.
- [ ] Directly upgrade the test MSI, verify actual payload, install the exact new APK in MuMu 1, and repeat normal QR/SAS/binding/offline/reconnect/revoke/cleanup. Active form: validating final installed artifacts.
- [ ] Archive current test AppData, uninstall only the test package, restore the six isolated original files and independently compare all hashes. Update final release baseline and manual cleanup checkpoint.

## Evidence and causal trace

The unchanged 0c88 CI APK rejected both the actual inner-SVG screenshot and the full QR card with `QR_IMAGE_NO_CODE`, while the latter invitation was unexpired. zxing-cpp could read both. The exact Android dependency is ZXing core 3.4.1. Java `RGBLuminanceSource` → `HybridBinarizer` → `QRCodeReader` with TRY_HARDER reproduced both failures. For the same real expired 547-byte invitation, identical SVG rendered at 180 CSS pixels failed; its natural 461 CSS pixel version decoded the identical payload SHA. No PNG was padded, resampled or edited; no QR payload was injected into the app.

A diagnostic-only browser size override also allowed the actual unchanged APK to reach matching SAS on both devices, explicit dual confirmation, durable binding and real desktop data. Normal service disable showed offline/cache; enable reconnected with the same binding; restarting only the Android ASG app retained it; desktop revoke produced authentication-required and Android local unpair cleared it. Service was normally disabled afterward. These are genuine diagnostic mobile results, not acceptance of the fixed installed desktop renderer.

## Design, files and boundaries

`QrCodeCard.tsx` owns a natural grid: preferred five CSS pixels/module, explicit SVG width/height, four modules of internal white quiet zone. The actual viewport and card chrome select integer sizes 3–5. Below minimum, horizontal overflow preserves modules. CSS no longer forces a fractional 180-pixel resample. No new package or API.

`App.kt` sampled its clock only at app composition, then waited a second before its next pairing tick. Publishing a pairing after idle could therefore show an inflated first countdown. Sample immediately before new state publication, before the first tick delay and on lifecycle resume. Expiry, phase and confirmation authority remain unchanged; this is not an emulator clock change.

Tests: component sizing/resize/cleanup/style regressions, and twelve unmodified actual-component screenshots (3/4/5 cells × DPR1/1.25/1.5/2) consumed by the product's unchanged pixel decoder in JVM tests. The synthetic expired invitation grants no trust. Its legacy pattern also decodes: do not call that synthetic case a negative reproduction. The private same-real-payload experiment is the causal proof.

## Risks, rollback and exit

Verify narrow layout, first paint, fractional display scales, actual OpenDocument import and first SAS frame after idle. Native Sky capture/coordinate geometry failures remain tool limitations; actual WebView loading and headless browser operation are separate claims. Physical camera/phone/hardware KeyStore are not covered by MuMu.

Rollback: ordinary revert of these rendering/presentation files, their tests/assets and generated frontend assets to 0c88. No API/schema/permission changes, ledger rewrite, DB downgrade or deletion. Keep old failure evidence. User authorizes only ASG and MuMu 1 input; MuMu 2/5 and other apps remain protected.

Exit requires exact new CI and package verification plus cleanup/restoration, not only unit tests. Original business data is currently isolated at `original-isolated-phase24-0c88`, with immutable `original-backup`; current AppData is test data. Do not start/migrate the isolated original. No Windows restart, merge, release or permanent cleanup.

See `verification/DEVICE_QR_NATIVE_20261024.json` and `verification/DEVICE_QR_FRONTEND_20261024.json`.

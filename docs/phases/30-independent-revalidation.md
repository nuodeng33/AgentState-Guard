# Independent revalidation — 2026-10-26

The user withdrew trust in the previous assistant's diagnostics and acceptance conclusions. Prior files and failure records remain preserved. They are historical leads, not current acceptance evidence.

## Fresh evidence

- Audited source base: `d4bf1dbb7544b099d82e9e90a90b7b96fb2d2290`; its new Git archive SHA256 is `202ce467c4f9f25ad582c83d7e7efd17f846cf5cf24090d59246bd7b94dc357e`.
- The installed main and sidecar hash to `7deca7f7a1d5b9675b92fa2a7c50607a4fe2a9f06ee9efdb5728fe83e574cfa2` and `88eb0c1cdc541ebd20be88265ebbf3f9be724da0ee600c634d391bd6f4ee88bd`. Installation registration is version 0.9.0, ProductCode `{D5E68944-A469-4369-B250-358115DD3EDD}`. This establishes local identity, not new-package acceptance.
- No ASG process or tested 8787/18794/18795 listener was present at initial inspection. Six current business files match the existing backup and stayed byte-identical after the fresh tests. The original business database was never opened by SQLite or started by the app in this run.
- A separately copied 54,558,720-byte archived test DB contains **34,526** ledger events. Direct offline Work list/detail/links reads measured 0.053/0.129/0.031 ms. Agent projection measured 3283.584 ms cold and 817.338/785.829 ms warm. Full writer verification measured 2265.938 ms. These are module-level measurements, not HTTP or native-window times.
- The patched working product tree `b29b5262f7bfb0f9bd9bced645a4812abb443bbd` was exported separately (ZIP SHA256 `9103ac68dc2c9c3672c1fe92d26958e507bfa3362ff4d0cdf15525d70a4029bd`). Fresh Windows source Core with real startup discovery and production assets returned Work list/detail/task in 9.367/10.891/17.420 ms. Its process became healthy after 3039.271 ms.
- Fresh sandboxed headless Chrome showed the Work entry after 696.05 ms measured from before browser launch; clicking it rendered the task body after 346.86 ms. Rendered body SHA256 exactly matched the archived SQLite body. The actual reading dialog exposed task-edit and relationship controls. No browser mutation was sent. Core exited normally with code 0, and the archive SHA256 stayed `d9cb922542ac130a63a60ea1a1f0cba3b8e76ebc750bbdf31ead3c1823e8df67`.
- The runtime copy had no sessions, runs, pending instructions or unfinished recovery intent. Its only local test scope was verified under the acceptance directory and disabled in the copy before startup. No original data/configuration or external protected files were altered.
- Independent full source regression: **1590 passed, 49 skipped**; full frontend: **432 passed in 54 files**. Production TypeScript/build, focused ledger tests (60 passed), targeted Ruff, and diff checks passed. These are fresh source results, not inherited CI claims.

## Reproduced defects and changes

1. Failed manual discovery refresh skipped all subsequent saved-record GETs, leaving relationship controls disabled. A refresh error now preserves the error but continues generation-fenced reads. Fresh discovery still controls whether a new association is allowed; stale candidates remain disabled.
2. Work-detail failure awaited an independent sidebar reload before clearing the main spinner. Its late completion could also clear a newer Work's loading state. Main loading now settles in the active-generation failure branch; sidebar recovery has no later loading mutation.
3. A raw date such as `2026-08-05+00:00` ends with a UTC-looking suffix but parses as a naive datetime. The cache gate now requires a parsed zero UTC offset; legacy inputs retain unconditional full-validator behavior.
4. At more than 65,536 rows, ascending cache admission evicted retained later rows before reuse. New cache entries are admitted only for the bounded newest tail. Every older miss still receives full validation; sequence, duplicate-ID and predecessor checks remain. A reduced-capacity regression confirms reuse across appends and detection of old-row mutation with an unchanged head.

All regressions were demonstrated failing before their fixes. Writer validation, public interfaces, database schema, historical hashes and attribution/approval rules are unchanged.

## Corrections and open gates

- The old native record's timestamps differ by **67,161.792 ms**, while its `ui_upper_bound_ms` says 7132. That reported upper bound is invalid; the timestamps are not proof of an exact UI-ready duration either.
- The old closure report says 34,540 ledger events for the archived database. Fresh counting gives 34,526. Its overall PASS label is not accepted as proof.
- This run has **not** accepted a new MSI, direct upgrade, native pointer interaction, crash recovery, MuMu pairing or physical-phone use. Source browser checks do not substitute for those gates.
- The ledger still scans and fingerprints all historical content on each independent authoritative read. The cache fix prevents an avoidable capacity cliff; it does not make historical integrity verification constant time. Agent projection also still makes avoidable historical queries after validation. Long-term growth remains unresolved.
- No merge, release, Windows restart, permanent deletion or released-space claim occurred. Cleanup remains manual-move only.

## Architecture references

[Temporal Continue-As-New](https://docs.temporal.io/develop/python/workflows/continue-as-new) starts a fresh run/history with carried state. [LangGraph checkpoints](https://docs.langchain.com/oss/python/langgraph/checkpointers) separate latest-state access from state-history access. These are useful models for bounding replay and serving current state; neither provides immediate arbitrary-old-row tamper detection in an unrelated SQLite ledger.

[SQLite WAL isolation](https://sqlite.org/isolation.html) supports one consistent read snapshot for verification and projection. The immediate compatible direction is to remove duplicate reads/parsing inside that snapshot. Serving a last-verified materialized view or skipping sealed history changes the trust/read semantics and requires an explicit architecture decision. A head/checkpoint alone cannot prove that unread historical bytes are unchanged.

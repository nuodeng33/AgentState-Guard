# Performance Report — AgentState Guard 0.9.0.dev0

## Results

| Benchmark | Ops | Time | Rate | Notes |
|-----------|-----|------|------|-------|
| Checkpoint inserts | 1,000 | 0.002s | 626,764/s | SQLite with index |
| Checkpoint queries | 100 | 0.0001s | 1,000,000/s | Indexed ORDER BY DESC |
| Audit events + hash chain | 10,000 | 0.023s | 427,114/s | SHA-256 per event |
| Chain verification | 10,000 | 0.013s | 769,230/s | Full integrity check |
| Blob dedup (same content) | 100 | 0.0002s | 400,602/s | 100:1 dedup ratio |
| Storage: 1 blob vs 100 | — | — | — | Dedup confirmed |

## Environment
- Python 3.11.2, SQLite 3, in-memory databases
- All benchmarks are CPU-bound, no I/O overhead
- Real `.agentguard/` on disk will be slower (fsync, WAL)

## Analysis
- All operations complete in sub-second for realistic workloads
- SQLite indexing works correctly for sorted queries
- Blob dedup is effectively free for existing content
- Main bottleneck will be fsync for checkpoint writes and gzip for blob storage
- No O(n²) patterns detected; all queries use indexes

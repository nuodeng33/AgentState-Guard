# CLI

## Local supervision

`agentguard supervise` provides a local-only workflow:

```text
agentguard supervise create
agentguard supervise show SESSION_ID
agentguard supervise evaluate
agentguard supervise approve SESSION_ID
agentguard supervise reject SESSION_ID
agentguard supervise activate SESSION_ID [--checkpoint-id ID]
agentguard supervise complete SESSION_ID
agentguard supervise fail SESSION_ID
```

`create` and `evaluate` require structured policy facts: intent/effect kinds, domain, repeated target/scope/evidence references, network/privilege/destructive/secret effects, checkpoint ID, and recovery coverage. The CLI does not infer risk facts from free text or environment variables.

With `--json`, stdout is one stable JSON object containing the session ID, lifecycle status, applicable policy decision/rule IDs/summary code, and approval/checkpoint requirements. It does not emit database absolute paths, full command lines, prompts, raw exceptions, or sensitive inputs. `POLICY_BLOCK`, `POLICY_UNKNOWN`, `APPROVAL_REQUIRED`, and `CHECKPOINT_REQUIRED` yield non-success exits; existing command exit semantics remain unchanged.

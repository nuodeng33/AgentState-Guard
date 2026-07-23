# Non-Goals — AgentState Guard v1.0

This version explicitly does NOT do:

- Full machine imaging
- Docker write operations (create/start/stop/remove containers, images, networks, volumes)
- Docker socket mounting or access
- Arbitrary web shell
- Cloud SaaS
- Automatic upload of user data
- Secrets manager
- Absolute rollback of arbitrary external operations
- Auto-fix of real production environments
- Git push/pull/fetch/remote operations
- System package installation
- Global Python/Node package installation
- Modification of files outside /workspace/projects/agentstate-guard
- Replacement of restic, Kopia, Timeshift, or Git

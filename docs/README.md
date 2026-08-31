# Documentation

Start with the page that matches the decision you are making.

| Document | Use it for |
|---|---|
| [Architecture and data contracts](architecture.md) | Phase boundaries, persisted artifacts, queue states, trust model |
| [Source contracts](sources.md) | Platform-specific coverage, cursor, deduplication, revision, and offline semantics |
| [CLI and configuration](configuration.md) | Commands and the three local configuration files |
| [Setup and rollout](setup.md) | Credentials, X browser opt-in, Lark, immutable installation, cutover |
| [Operations and recovery](operations.md) | Schedule, logs, queue diagnosis, outage recovery, upgrades and rollback |
| [Verification guide](verification.md) | Unit/integration/live/installed evidence levels and acceptance gates |
| [Wiki information architecture](wiki.md) | Reader-facing tree, validation isolation, stale-node policy |
| [Security policy](../SECURITY.md) | Credential boundary, vulnerability reporting, source-content trust |
| [Contributing](../CONTRIBUTING.md) | Development setup, source-adapter contract, PR checklist |
| [Changelog](../CHANGELOG.md) | User-visible releases and migrations |

## Version boundary

The current architecture is v0.3. The earlier Claude/Notion/OpenClaw implementation is preserved at
the annotated Git tag `legacy-v1`; its documentation is not authoritative for the current runtime.

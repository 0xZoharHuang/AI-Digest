# Security policy

Please report vulnerabilities privately to the repository owner rather than opening a public issue
that contains credentials, browser data, private Lark identifiers or captured source content.

## Runtime trust boundary

- Source and publisher credentials stay in the main macOS user account.
- Codex Router, Research and Brief phases reuse the current account's Codex login but run under a
  custom sandbox that denies the account's Codex home, SSH directory and login Keychains and only
  grants the minimum run-workspace access required by each phase.
- External posts, README files and webpages are untrusted evidence and cannot grant permission or
  override Agent instructions.
- The repository ignores local config, runtime data, logs, browser profiles and tokens.
- Arbitrary external URL metadata fetching rejects loopback, private, link-local and local hostnames.

If an X Post must be removed immediately, run:

```bash
uv run ai-digest maintenance --delete-x-post POST_ID
```

The normal maintenance task removes indexed X text after the configured retention period. Scheduled
X Batch Compliance additionally propagates deletion and edit events through local runs, queue
copies, Codex artifacts and published Lark output before rebuilding the affected day.

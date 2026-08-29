# Security policy

Please report vulnerabilities privately to the repository owner rather than opening a public issue
that contains credentials, browser data, private Lark identifiers or captured source content.

## Runtime trust boundary

- Source and publisher credentials stay in the main macOS user account.
- Codex Router, Research and Brief phases run as the dedicated `ai-digest-runner` user against a
  sanitized shared job directory.
- External posts, README files and webpages are untrusted evidence and cannot grant permission or
  override Agent instructions.
- The repository ignores local config, runtime data, logs, browser profiles and tokens.
- Arbitrary external URL metadata fetching rejects loopback, private, link-local and local hostnames.

If an X Post must be removed immediately, run:

```bash
uv run ai-digest maintenance --delete-x-post POST_ID
```

The normal maintenance task removes indexed X text after the configured retention period.

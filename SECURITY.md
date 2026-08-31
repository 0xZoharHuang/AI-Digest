# Security policy

## Supported versions

Security fixes target the current `main` release line. The `legacy-v1` tag is retained for historical
reference and is not supported as a production deployment.

## Reporting a vulnerability

Do not open a public issue containing credentials, browser data, private Lark identifiers, private
source content, or an exploit that could expose them.

Prefer a private GitHub security advisory:

<https://github.com/0xZoharHuang/AI-Digest/security/advisories/new>

Include:

- affected commit/version and platform;
- reproduction steps using synthetic data when possible;
- impact and the trust boundary crossed;
- whether credentials/private content may already have been exposed;
- any proposed mitigation.

Please allow reasonable time for validation and a coordinated fix before public disclosure.

## Runtime trust boundary

- Source and publisher credentials remain in the current macOS user account.
- Codex Router, Research, and Brief phases reuse the current file-backed login but run under explicit
  permission profiles. The Codex home, SSH directory, login Keychains, repository source, and
  unrelated user paths are denied; only the current phase workspace is granted.
- External posts, repository text, papers, and webpages are untrusted evidence. They cannot grant
  permission or override phase instructions.
- Queue imports reject symlinks, unsafe identifiers/paths, oversized outputs, unknown unit IDs, and
  incomplete coverage.
- The publisher validates all local artifacts and internal links before its first Lark write.
- Arbitrary external metadata requests reject loopback, private, link-local, multicast, reserved,
  local-hostname, and cloud-metadata destinations.
- The repository ignores local config, runtime data, logs, browser profiles, databases, and tokens.

## Credentials and personal data

- TwitterAPI.io keys are stored in macOS Keychain.
- Personal X cookies stay in an ignored local file and are never sent to the List provider.
- Browser collection is opt-in and cannot be made risk-free; use a personal risk assessment and the
  narrowest practical account/session.
- X text is subject to configured retention and targeted deletion.
- Lark space/user IDs belong only in ignored local configuration.

If an X Post must be removed immediately:

```bash
uv run ai-digest maintenance --delete-x-post POST_ID
```

Normal maintenance removes indexed X content after the configured retention period.

## Security-sensitive changes

Pull requests that alter URL fetching, sandbox permissions, queue import, path validation, browser
state, credential handling, Lark writes, or deletion/retention must include threat-boundary notes and
targeted tests. See [CONTRIBUTING.md](CONTRIBUTING.md).

# CLI and configuration

AI Intelligence Radar reads three local files. Copy their checked-in examples and keep the local
copies untracked.

```bash
cp config/runtime.example.toml config/runtime.toml
cp config/sources.example.toml config/sources.toml
cp config/interests.example.md config/interests.md
```

## Runtime configuration

`runtime.toml` controls local paths and downstream execution:

- `timezone`, `daily_hour`, `window_hours`;
- owner runtime and shared queue roots;
- source preview/text-retention limits;
- Codex binary, model, reasoning effort, concurrency, subagent cap, and idle timeout;
- Lark CLI, space, self-DM receiver, identity, and Wiki base URL.

Relative binary paths resolve from the installed application snapshot. Production worker and owner
processes intentionally use different runtime roots but the same shared queue.

## Source configuration

`sources.toml` enables adapters and defines their platform budgets. Important safety fields include:

- `x_list.required`: whether List failure is a readiness blocker;
- `x_for_you.personal_browser_risk_acknowledged`: mandatory explicit opt-in;
- GitHub request/candidate budgets and star/growth thresholds;
- paper page/backlog limits;
- HN incremental chunk and concurrency limits;
- media allowed domains, path constraints, selectors, and entry caps.

Do not place API secrets or cookies in the checked-in example. The X List API key is stored through
`ai-digest x-provider-set-key`; browser cookies remain in the ignored configured file.

## Reader interests

`interests.md` is context, not a fixed taxonomy. It helps Phase 2 form natural packages and lets each
Phase 3 Lead decide whether its completed research materially serves the reader, while still allowing
an unexpected item to be investigated. It must not contain credentials or operational
instructions from external sources.

## CLI map

| Command | Purpose | External writes |
|---|---|---|
| `doctor` | Check binaries, model access, auth, browser, Wiki, disk | no content writes |
| `collect` | Run selected Phase 1 adapters and persist observations | local runtime only |
| `phase1` | Collect, seal, and reserve a daily handoff | local runtime only |
| `route` | Run V3 Phase 2 on an existing run | run workspace |
| `research` | Run V3 Phase 3 packages | run workspace + web research |
| `brief` | Generate the Phase 4 navigation Brief | run workspace |
| `publish` | Publish one validated run | Lark Wiki and self-DM |
| `pipeline` | Local Phase 1–4; add `--publish` deliberately | optional Lark |
| `tick` | Scheduled reconciliation/collection entry point | depends on event |
| `agent-worker` | Drain ready jobs | shared queue/workspaces |
| `automation-smoke` | Isolated real-Codex E2E with in-memory publisher | never Lark |
| `maintenance` | Bounded retention/migration operations | local runtime |

Use global overrides when working with non-default files:

```bash
uv run ai-digest \
  --runtime-config /path/to/runtime.toml \
  --sources-config /path/to/sources.toml \
  doctor
```

## Important command boundaries

- `automation-smoke` reads representative production observations but writes only below its isolated
  smoke root. It clears Lark identifiers and uses an in-memory Wiki/DM transport.
- `pipeline` does not publish unless `--publish` is supplied.
- `publish` is a real external write.
- `tick --event recover` may promote retries, publish pending runs, enqueue sealed handoffs, and start
  a missing same-day run after the configured daily hour.
- X content deletion is targeted: `maintenance --delete-x-post POST_ID`.

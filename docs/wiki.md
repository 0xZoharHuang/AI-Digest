# Wiki information architecture

The private Wiki is a reader product, not a debug artifact browser.

## Canonical tree

```text
AI Intelligence Radar
└── YYYY
    └── YYYY-MM
        └── YYYY-MM-DD · AI Intelligence Brief
            ├── main report
            │   └── natural subreports
            └── main report
```

## Page responsibilities

- **Year:** index of month nodes.
- **Month:** index of daily Brief nodes.
- **Daily Brief:** source status, what was newly observed, what each investigation established, links,
  and failures; no second macro rewrite or forced reading order.
- **Main report:** self-contained deep research for one dynamically grouped package.
- **Subreport:** an optional independent evidence chain or technical/product/business investigation,
  conflicts, and unknowns.

When a package contains several independent events or technical investigations, its main report
provides the shared mental model and links to natural subreports for each worthwhile deep branch.
A single coherent paper or project may remain one main report; no page-count quota is imposed.

The reader is technically, commercially and entrepreneurially sophisticated but is not assumed to
know every specialist vocabulary. Reports retain implementation and evidence depth while explaining
decisive terms and mechanisms at first use. Paper investigations may use concise algorithm-flow
ASCII or pseudocode; repository investigations distinguish why the project entered today's radar
from its technical merit and actual community attention.

A Phase 3 Lead may withhold a fully inspected package when it has no material reader value. Its
intake/evidence remain local artifacts; the publisher does not create an empty Wiki node.

Every child page receives deterministic back-navigation. Agent prose remains natural; only the
system-owned package IDs, manifest paths, and internal URI schemes are strictly validated.

## Validation isolation

`automation-smoke` must never create a real Wiki node or send a real DM. It clears Lark identifiers,
uses an in-memory transport, and records the simulated tree in its local receipt.

Manual publisher integration tests should use an isolated test space only when a real Lark write is
explicitly required. Test/validation nodes must not be created beneath a production year/month/day
tree.

## Stale-node policy

The publisher may remove a stale main-report/subreport only when all conditions hold:

1. the node key exists in the same run's `publish_manifest.json`;
2. the new validated artifact set no longer contains that key;
3. all replacement/current pages and the daily Brief were written successfully first.

It does not scan and delete arbitrary Wiki children. Unknown or manually created pages require a
separate inventory and explicit organization plan.

## Validation-page cleanup

If validation pages already exist in the production space:

1. recursively inventory the real Wiki using user identity;
2. compare node path/token against production publish manifests;
3. read low-confidence titles/content before classification;
4. propose exact moves/deletions and recovery boundaries;
5. obtain explicit confirmation before any Wiki write;
6. fresh-read the tree after execution.

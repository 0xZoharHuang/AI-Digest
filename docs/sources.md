# Source contracts

Phase 1 preserves platform semantics. A source is not called “complete” merely because a request
returned HTTP 200.

## Coverage modes

- `complete_increment`: the configured surface exposes a resumable sequence and the collector has
  durably consumed the interval through its reported cursor.
- `bounded_discovery`: the platform exposes candidates under queries, ranking, time, or processing
  budgets; it is not a global inventory.
- `sampled_surface`: the platform exposes a personalized/ranked surface without replay guarantees.

Every receipt reports fetched, parsed, new, duplicate and revision counts, cursor before/after,
oldest/newest source time, pagination end, raw-receipt completeness, and quiet rationale when known.

## X Lists

- Access: configured public Lists through TwitterAPI.io.
- Schedule: 01:00, 07:00, 13:00, 19:00 plus wake catch-up.
- Cursor: one `sinceTime` per List, with a five-minute overlap and full pagination.
- Identity: X post/conversation; cross-List copies are one observation identity.
- Revisions: changed text, edit history, quote/reference content, or stable content hash create a
  revision observation.
- Commit rule: cursor advances only after all pages, raw responses, revisions, and state rows are
  durable.
- Coverage: complete increment for the configured Lists, not all of X.

## X For You

- Access: explicitly opted-in Playwright session using a local ignored cookie file.
- Schedule: daily collection and a second 20:00 sample.
- Receipt: sample rank, observed time, scroll rounds, repeat rate, and stop reason.
- Failure: non-blocking, with bounded failure cooldown.
- Coverage: sampled surface. A laptop cannot reconstruct recommendations shown while it was off.

The personal cookie is never sent to the List provider and is not copied into Codex workspaces.

## GitHub

- Access: Trending, authenticated Search, tracked snapshots, and bounded early-repo rechecks.
- Discovery lanes: 1–499 stars and 500–5000 stars, plus configured topics/queries.
- Events: lane entry, 500/5000 crossing, meaningful 6h/24h/7d growth, release, archive/restore, and
  material metadata change.
- Identity: repository ID. Immutable snapshots provide local forward history.
- README: stored as evidence when fetched; ordinary README edits are not automatically daily events.
- Coverage: bounded discovery under the configured query rotation and candidate budget.

Multi-day `created`/`pushed` windows help after a laptop outage, but they do not make GitHub Search a
global event stream.

## arXiv

- Access: official multi-category RSS for `cs.RO`, `cs.AI`, `cs.LG`, `cs.CV`, `cs.CL`, and
  `stat.ML`.
- Identity: arXiv ID plus version/announcement semantics.
- Revisions: new, replacement/version, withdrawal, and cross-list events remain distinguishable.
- Schedule: 13:30 prefetch, 19:00 retry, daily run consumption.
- Coverage: complete current announcement feed while online.

The search API `submittedDate` path is a **bounded supplement for newly submitted papers** after an
outage. Submission date is not the same as RSS announcement date, so it must not be described as a
complete replay of missed replacement, withdrawal, moderation-delay, or cross-list announcements.

## Hugging Face Daily Papers

- Access: official dated and paginated Daily Papers API.
- Cursor: last fully committed date; at most three missed dates are drained per poll by default.
- Identity: arXiv ID, retained as a separate attention/enrichment observation until Phase 2 merges
  it with arXiv.
- Commit rule: full pagination, raw blobs, revisions, and item rows precede date-cursor advancement.
- Coverage: bounded attention surface, with dated backlog replay.

## Hacker News

- Access: official Firebase `maxitem` and item endpoints.
- Cursor: highest completely scanned item ID.
- Offline behavior: large gaps are consumed in bounded chunks; any failed item request retains the
  current chunk cursor.
- Identity: story ID. `new`, `top`, and `show` are attention metadata on that story, not duplicates.
- Comments: fetched later by research when relevant.
- Coverage: complete item-ID increment through the reported cursor.

## Media

Discovery order is source-specific: RSS/Atom, sitemap (including sitemap indexes), then a constrained
index-page adapter. Canonical URL and content hash define identity/revision. Conditional requests are
used where supported. A failed body extraction keeps metadata and raw HTML so Phase 3 can fetch the
primary page later.

The default configuration includes OpenAI, NVIDIA, Hugging Face, Physical Intelligence, Anthropic,
Google DeepMind, Figure, 1X, Skild AI, a16z, IEEE Spectrum Robotics, and The Robot Report.

Each media source has its own health surface; `articles=success` is not used to hide one broken site.

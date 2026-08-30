from __future__ import annotations

import json
import shutil
from collections.abc import Iterable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import aiosqlite

from .models import FetchManifest, SourceItem
from .utils import atomic_write_bytes, atomic_write_json, atomic_write_text, sha256_bytes


class FileStore:
    """Append-only text/blob store plus immutable per-run handoff directories."""

    def __init__(self, root: Path):
        self.root = root.expanduser().resolve()
        self.store_root = self.root / "store"
        self.runs_root = self.root / "runs"
        self.blob_root = self.store_root / "blobs"
        self.fetch_root = self.store_root / "fetches"
        self.revision_root = self.store_root / "revisions"
        for path in (self.blob_root, self.fetch_root, self.revision_root, self.runs_root):
            path.mkdir(parents=True, exist_ok=True)

    def write_blob(self, content: bytes | str, suffix: str = ".txt") -> str:
        raw = content.encode("utf-8") if isinstance(content, str) else content
        digest = sha256_bytes(raw)
        path = self.blob_root / digest[:2] / f"{digest}{suffix}"
        if not path.exists():
            path.parent.mkdir(parents=True, exist_ok=True)
            if isinstance(content, str):
                atomic_write_text(path, content)
            else:
                atomic_write_bytes(path, raw)
        return f"sha256:{digest}{suffix}"

    def resolve_blob(self, ref: str) -> Path:
        if not ref.startswith("sha256:"):
            raise ValueError(f"unsupported blob reference: {ref}")
        value = ref.removeprefix("sha256:")
        digest = value[:64]
        suffix = value[64:] or ".txt"
        return self.blob_root / digest[:2] / f"{digest}{suffix}"

    def write_fetch_manifest(self, manifest: FetchManifest) -> Path:
        date = manifest.started_at.astimezone(UTC).strftime("%Y-%m-%d")
        path = self.fetch_root / manifest.source / date / manifest.fetch_id / "manifest.json"
        atomic_write_json(path, manifest.model_dump(mode="json"))
        return path

    def write_revision(self, item: SourceItem) -> Path:
        safe_id = item.item_id.replace("/", "_").replace(":", "_")
        revision = item.raw_refs[0].replace("sha256:", "")[:16] if item.raw_refs else "metadata"
        path = self.revision_root / item.source / safe_id / f"{revision}.json"
        if not path.exists():
            atomic_write_json(path, item.model_dump(mode="json"))
        return path

    def write_github_snapshot(self, snapshot: dict[str, Any]) -> str:
        """Persist one immutable, human-inspectable repository observation."""

        repo_id = str(int(snapshot["repo_id"]))
        observed_at = datetime.fromisoformat(str(snapshot["observed_at"])).astimezone(UTC)
        snapshot_id = str(snapshot["snapshot_id"])
        if len(snapshot_id) != 64 or any(char not in "0123456789abcdef" for char in snapshot_id):
            raise ValueError("invalid GitHub snapshot id")
        stamp = observed_at.strftime("%Y%m%dT%H%M%S%fZ")
        path = (
            self.revision_root
            / "github_snapshots"
            / repo_id
            / f"{stamp}-{snapshot_id[:16]}.json"
        )
        if not path.exists():
            atomic_write_json(path, snapshot)
        return path.relative_to(self.root).as_posix()

    def remove_item_content(self, item: SourceItem) -> None:
        safe_id = item.item_id.replace("/", "_").replace(":", "_")
        revision_dir = self.revision_root / item.source / safe_id
        if revision_dir.exists():
            shutil.rmtree(revision_dir)
        for ref in item.raw_refs:
            try:
                path = self.resolve_blob(ref)
            except ValueError:
                continue
            if path.exists():
                path.unlink()

    def next_attempt_dir(self, date: str) -> tuple[int, Path]:
        date_root = self.runs_root / date
        date_root.mkdir(parents=True, exist_ok=True)
        attempts = [
            int(path.name.split("-")[-1])
            for path in date_root.glob("attempt-*")
            if path.name.split("-")[-1].isdigit()
        ]
        number = max(attempts, default=0) + 1
        path = date_root / f"attempt-{number:04d}"
        path.mkdir(parents=True, exist_ok=False)
        return number, path


class StateDB:
    """Rebuildable cursor/index state. Source text remains in FileStore."""

    def __init__(self, path: Path):
        self.path = path.expanduser().resolve()
        self.path.parent.mkdir(parents=True, exist_ok=True)

    async def init(self) -> None:
        async with aiosqlite.connect(self.path) as db:
            await db.executescript(
                """
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS source_items (
                    item_id TEXT PRIMARY KEY,
                    source TEXT NOT NULL,
                    surface TEXT NOT NULL,
                    item_type TEXT NOT NULL,
                    handoff_at TEXT NOT NULL,
                    first_observed_at TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    delivered_run_id TEXT,
                    sealed_run_id TEXT,
                    expires_at TEXT
                );

                CREATE TABLE IF NOT EXISTS cursors (
                    source TEXT PRIMARY KEY,
                    value TEXT,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS source_baselines (
                    source TEXT PRIMARY KEY,
                    fetched_count INTEGER NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS runs (
                    run_id TEXT PRIMARY KEY,
                    date TEXT NOT NULL,
                    attempt INTEGER NOT NULL,
                    status TEXT NOT NULL,
                    path TEXT NOT NULL,
                    handoff_state TEXT NOT NULL DEFAULT 'open',
                    queued_at TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS run_items (
                    run_id TEXT NOT NULL,
                    item_id TEXT NOT NULL,
                    PRIMARY KEY (run_id, item_id)
                );

                CREATE TABLE IF NOT EXISTS github_repo_snapshots (
                    snapshot_id TEXT PRIMARY KEY,
                    repo_id TEXT NOT NULL,
                    observed_at TEXT NOT NULL,
                    stars INTEGER NOT NULL,
                    metadata_json TEXT NOT NULL,
                    file_ref TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS github_event_markers (
                    event_key TEXT PRIMARY KEY,
                    event_id TEXT NOT NULL,
                    observed_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS github_early_watch (
                    repo_id TEXT PRIMARY KEY,
                    full_name TEXT NOT NULL,
                    first_seen_at TEXT NOT NULL,
                    last_checked_at TEXT NOT NULL
                );
                """
            )
            await self._ensure_column(db, "source_items", "sealed_run_id", "TEXT")
            await self._ensure_column(
                db,
                "runs",
                "handoff_state",
                "TEXT NOT NULL DEFAULT 'open'",
            )
            await self._ensure_column(db, "runs", "queued_at", "TEXT")
            await db.executescript(
                """
                CREATE INDEX IF NOT EXISTS idx_source_items_pending_v2
                    ON source_items(delivered_run_id, sealed_run_id, handoff_at);
                CREATE INDEX IF NOT EXISTS idx_source_items_source
                    ON source_items(source, surface);
                CREATE INDEX IF NOT EXISTS idx_runs_handoff
                    ON runs(handoff_state, created_at);
                CREATE INDEX IF NOT EXISTS idx_github_repo_snapshots_repo_time
                    ON github_repo_snapshots(repo_id, observed_at DESC);
                CREATE INDEX IF NOT EXISTS idx_github_early_watch_rotation
                    ON github_early_watch(first_seen_at, last_checked_at);
                """
            )
            await db.commit()

    @staticmethod
    async def _ensure_column(
        db: aiosqlite.Connection,
        table: str,
        column: str,
        declaration: str,
    ) -> None:
        cursor = await db.execute(f"PRAGMA table_info({table})")
        existing = {str(row[1]) for row in await cursor.fetchall()}
        if column not in existing:
            await db.execute(f"ALTER TABLE {table} ADD COLUMN {column} {declaration}")

    async def put_items(self, items: Iterable[SourceItem]) -> list[str]:
        inserted: list[str] = []
        async with aiosqlite.connect(self.path) as db:
            for item in items:
                cursor = await db.execute(
                    """
                    INSERT OR IGNORE INTO source_items
                    (item_id, source, surface, item_type, handoff_at, first_observed_at,
                     payload_json, expires_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        item.item_id,
                        item.source,
                        item.surface,
                        item.item_type,
                        item.handoff_at.isoformat(),
                        item.first_observed_at.isoformat(),
                        item.model_dump_json(),
                        item.expires_at.isoformat() if item.expires_at else None,
                    ),
                )
                if cursor.rowcount == 1:
                    inserted.append(item.item_id)
            await db.commit()
        return inserted

    async def has_item(self, item_id: str) -> bool:
        async with aiosqlite.connect(self.path) as db:
            cursor = await db.execute("SELECT 1 FROM source_items WHERE item_id = ?", (item_id,))
            return await cursor.fetchone() is not None

    async def pending_items(self, start: datetime, end: datetime) -> list[SourceItem]:
        async with aiosqlite.connect(self.path) as db:
            cursor = await db.execute(
                """
                SELECT payload_json FROM source_items
                WHERE delivered_run_id IS NULL AND sealed_run_id IS NULL
                    AND handoff_at >= ? AND handoff_at < ?
                ORDER BY handoff_at, item_id
                """,
                (start.isoformat(), end.isoformat()),
            )
            rows = await cursor.fetchall()
        return [SourceItem.model_validate_json(row[0]) for row in rows]

    async def mark_delivered(self, item_ids: Iterable[str], run_id: str) -> None:
        values = [(run_id, item_id) for item_id in item_ids]
        if not values:
            return
        async with aiosqlite.connect(self.path) as db:
            await db.executemany(
                "UPDATE source_items SET delivered_run_id = ? WHERE item_id = ?",
                values,
            )
            await db.commit()

    async def seal_run(self, run_id: str, status: str, item_ids: Iterable[str]) -> None:
        """Reserve a complete Phase 1 handoff for later queueing.

        The caller writes and fsyncs/renames the run handoff before calling this method. A
        single transaction then makes both the run's sealed state and its item reservation
        durable. Failed runs are recorded but intentionally remain retryable.
        """

        values = list(dict.fromkeys(item_ids))
        async with aiosqlite.connect(self.path) as db:
            await db.execute("BEGIN IMMEDIATE")
            run_cursor = await db.execute("SELECT 1 FROM runs WHERE run_id = ?", (run_id,))
            if await run_cursor.fetchone() is None:
                await db.rollback()
                raise KeyError(f"unknown run: {run_id}")

            handoff_state = "failed" if status == "failed" else "sealed"
            if handoff_state == "sealed":
                for item_id in values:
                    item_cursor = await db.execute(
                        """
                        SELECT delivered_run_id, sealed_run_id
                        FROM source_items WHERE item_id = ?
                        """,
                        (item_id,),
                    )
                    row = await item_cursor.fetchone()
                    if row is None:
                        await db.rollback()
                        raise KeyError(f"unknown source item: {item_id}")
                    delivered_run_id, sealed_run_id = row
                    if delivered_run_id is not None or sealed_run_id not in {None, run_id}:
                        await db.rollback()
                        raise RuntimeError(f"source item is already handed off: {item_id}")

                await db.executemany(
                    """
                    UPDATE source_items SET sealed_run_id = ?
                    WHERE item_id = ? AND delivered_run_id IS NULL
                    """,
                    [(run_id, item_id) for item_id in values],
                )
                await db.executemany(
                    "INSERT OR IGNORE INTO run_items(run_id, item_id) VALUES (?, ?)",
                    [(run_id, item_id) for item_id in values],
                )

            now = datetime.now(UTC).isoformat()
            await db.execute(
                """
                UPDATE runs SET status = ?, handoff_state = ?, updated_at = ?
                WHERE run_id = ?
                """,
                (status, handoff_state, now, run_id),
            )
            await db.commit()

    async def list_sealed_unqueued_runs(self) -> list[tuple[str, Path]]:
        async with aiosqlite.connect(self.path) as db:
            cursor = await db.execute(
                """
                SELECT run_id, path FROM runs
                WHERE handoff_state = 'sealed'
                ORDER BY created_at, run_id
                """
            )
            rows = await cursor.fetchall()
        return [(str(row[0]), Path(str(row[1]))) for row in rows]

    async def mark_run_queued(self, run_id: str) -> bool:
        """Atomically finalize delivery after the queue directory is visible."""

        async with aiosqlite.connect(self.path) as db:
            await db.execute("BEGIN IMMEDIATE")
            cursor = await db.execute(
                "SELECT handoff_state FROM runs WHERE run_id = ?",
                (run_id,),
            )
            row = await cursor.fetchone()
            if row is None:
                await db.rollback()
                raise KeyError(f"unknown run: {run_id}")
            if row[0] == "queued":
                await db.commit()
                return False
            if row[0] != "sealed":
                await db.rollback()
                raise RuntimeError(f"run is not sealed: {run_id} ({row[0]})")

            await db.execute(
                """
                UPDATE source_items SET delivered_run_id = ?
                WHERE sealed_run_id = ? AND delivered_run_id IS NULL
                    AND item_id IN (SELECT item_id FROM run_items WHERE run_id = ?)
                """,
                (run_id, run_id, run_id),
            )
            now = datetime.now(UTC).isoformat()
            await db.execute(
                """
                UPDATE runs SET handoff_state = 'queued', queued_at = ?, updated_at = ?
                WHERE run_id = ?
                """,
                (now, now, run_id),
            )
            await db.commit()
        return True

    async def mark_run_locally_completed(self, run_id: str, handoff_state: str) -> bool:
        """Finalize a sealed in-process run after its requested durable outputs exist."""

        allowed_states = {"local_complete", "published"}
        if handoff_state not in allowed_states:
            raise ValueError(f"invalid local completion state: {handoff_state}")
        async with aiosqlite.connect(self.path) as db:
            await db.execute("BEGIN IMMEDIATE")
            cursor = await db.execute(
                "SELECT handoff_state FROM runs WHERE run_id = ?",
                (run_id,),
            )
            row = await cursor.fetchone()
            if row is None:
                await db.rollback()
                raise KeyError(f"unknown run: {run_id}")
            if row[0] == handoff_state:
                await db.commit()
                return False
            if row[0] != "sealed":
                await db.rollback()
                raise RuntimeError(f"run is not sealed: {run_id} ({row[0]})")

            await db.execute(
                """
                UPDATE source_items SET delivered_run_id = ?
                WHERE sealed_run_id = ? AND delivered_run_id IS NULL
                    AND item_id IN (SELECT item_id FROM run_items WHERE run_id = ?)
                """,
                (run_id, run_id, run_id),
            )
            now = datetime.now(UTC).isoformat()
            await db.execute(
                """
                UPDATE runs SET handoff_state = ?, updated_at = ?
                WHERE run_id = ?
                """,
                (handoff_state, now, run_id),
            )
            await db.commit()
        return True

    async def get_cursor(self, source: str) -> str | None:
        async with aiosqlite.connect(self.path) as db:
            cursor = await db.execute("SELECT value FROM cursors WHERE source = ?", (source,))
            row = await cursor.fetchone()
        return row[0] if row else None

    async def set_cursor(self, source: str, value: str | None) -> None:
        await self.set_cursors({source: value})

    async def set_cursors(self, values: dict[str, str | None]) -> None:
        if not values:
            return
        now = datetime.now(UTC).isoformat()
        async with aiosqlite.connect(self.path) as db:
            await db.executemany(
                """
                INSERT INTO cursors(source, value, updated_at) VALUES (?, ?, ?)
                ON CONFLICT(source) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at
                """,
                [(source, value, now) for source, value in values.items()],
            )
            await db.commit()

    async def github_repo_context(
        self,
        repo_id: str,
        observed_at: datetime,
        event_item_ids: Iterable[str],
        growth_event_key: str,
    ) -> dict[str, Any]:
        """Read only committed history used to derive mechanical GitHub events."""

        moment = observed_at.astimezone(UTC)
        item_ids = list(dict.fromkeys(event_item_ids))
        baselines: dict[str, dict[str, Any] | None] = {}
        async with aiosqlite.connect(self.path) as db:
            cursor = await db.execute(
                """
                SELECT observed_at, stars FROM github_repo_snapshots
                WHERE repo_id = ? AND observed_at < ?
                ORDER BY observed_at DESC LIMIT 1
                """,
                (repo_id, moment.isoformat()),
            )
            row = await cursor.fetchone()
            latest = (
                {"observed_at": str(row[0]), "stars": int(row[1])} if row is not None else None
            )

            for label, delta in (
                ("6h", timedelta(hours=6)),
                ("24h", timedelta(hours=24)),
                ("7d", timedelta(days=7)),
            ):
                cursor = await db.execute(
                    """
                    SELECT observed_at, stars FROM github_repo_snapshots
                    WHERE repo_id = ? AND observed_at <= ?
                    ORDER BY observed_at DESC LIMIT 1
                    """,
                    (repo_id, (moment - delta).isoformat()),
                )
                row = await cursor.fetchone()
                baselines[label] = (
                    {"observed_at": str(row[0]), "stars": int(row[1])}
                    if row is not None
                    else None
                )

            existing_item_ids: set[str] = set()
            if item_ids:
                placeholders = ",".join("?" for _ in item_ids)
                cursor = await db.execute(
                    f"SELECT item_id FROM source_items WHERE item_id IN ({placeholders})",
                    item_ids,
                )
                existing_item_ids = {str(value[0]) for value in await cursor.fetchall()}

            cursor = await db.execute(
                "SELECT observed_at FROM github_event_markers WHERE event_key = ?",
                (growth_event_key,),
            )
            row = await cursor.fetchone()
            growth_event_at = str(row[0]) if row is not None else None

        return {
            "latest": latest,
            "baselines": baselines,
            "existing_item_ids": existing_item_ids,
            "growth_event_at": growth_event_at,
        }

    async def commit_github_poll(
        self,
        items: Iterable[SourceItem],
        snapshots: Iterable[dict[str, Any]],
        event_markers: dict[str, tuple[str, datetime, int]],
    ) -> list[str]:
        """Atomically commit GitHub handoff items and snapshot/index advancement.

        The collector calls this only after raw blobs, immutable snapshot files, item
        revisions, and fetch manifests have been atomically written. Orphan files are
        safe after a pre-commit crash; a retry recomputes the same deterministic items.
        """

        item_values = list(items)
        snapshot_values = list(snapshots)
        item_ids = {item.item_id for item in item_values}
        if any(event_id not in item_ids for event_id, _, _ in event_markers.values()):
            raise ValueError("GitHub event marker does not reference this poll's item")

        inserted: list[str] = []
        async with aiosqlite.connect(self.path) as db:
            await db.execute("BEGIN IMMEDIATE")
            try:
                suppressed_event_ids: set[str] = set()
                for event_key, (event_id, event_at, cooldown_hours) in event_markers.items():
                    cutoff = event_at.astimezone(UTC) - timedelta(
                        hours=max(0, cooldown_hours)
                    )
                    cursor = await db.execute(
                        """
                        SELECT 1 FROM github_event_markers
                        WHERE event_key = ? AND observed_at > ?
                        """,
                        (event_key, cutoff.isoformat()),
                    )
                    if await cursor.fetchone() is not None:
                        suppressed_event_ids.add(event_id)

                for item in item_values:
                    if item.item_id in suppressed_event_ids:
                        continue
                    cursor = await db.execute(
                        """
                        INSERT OR IGNORE INTO source_items
                        (item_id, source, surface, item_type, handoff_at, first_observed_at,
                         payload_json, expires_at)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            item.item_id,
                            item.source,
                            item.surface,
                            item.item_type,
                            item.handoff_at.isoformat(),
                            item.first_observed_at.isoformat(),
                            item.model_dump_json(),
                            item.expires_at.isoformat() if item.expires_at else None,
                        ),
                    )
                    if cursor.rowcount == 1:
                        inserted.append(item.item_id)

                for snapshot in snapshot_values:
                    await db.execute(
                        """
                        INSERT OR IGNORE INTO github_repo_snapshots
                        (snapshot_id, repo_id, observed_at, stars, metadata_json, file_ref)
                        VALUES (?, ?, ?, ?, ?, ?)
                        """,
                        (
                            str(snapshot["snapshot_id"]),
                            str(snapshot["repo_id"]),
                            str(snapshot["observed_at"]),
                            int(snapshot["stars"]),
                            json.dumps(snapshot, ensure_ascii=False, sort_keys=True),
                            str(snapshot["file_ref"]),
                        ),
                    )
                    repo_id = str(snapshot["repo_id"])
                    full_name = str(snapshot.get("full_name") or "")
                    observed_at = str(snapshot["observed_at"])
                    if full_name and "early" in snapshot.get("lanes", []):
                        await db.execute(
                            """
                            INSERT OR IGNORE INTO github_early_watch
                            (repo_id, full_name, first_seen_at, last_checked_at)
                            VALUES (?, ?, ?, ?)
                            """,
                            (repo_id, full_name, observed_at, observed_at),
                        )
                    if full_name:
                        await db.execute(
                            """
                            UPDATE github_early_watch
                            SET full_name = ?, last_checked_at = ?
                            WHERE repo_id = ?
                            """,
                            (full_name, observed_at, repo_id),
                        )

                for event_key, (event_id, event_at, _) in event_markers.items():
                    if event_id in suppressed_event_ids:
                        continue
                    await db.execute(
                        """
                        INSERT INTO github_event_markers(event_key, event_id, observed_at)
                        VALUES (?, ?, ?)
                        ON CONFLICT(event_key) DO UPDATE SET
                            event_id=excluded.event_id,
                            observed_at=excluded.observed_at
                        WHERE excluded.observed_at > github_event_markers.observed_at
                        """,
                        (event_key, event_id, event_at.astimezone(UTC).isoformat()),
                    )
                await db.commit()
            except Exception:
                await db.rollback()
                raise
        return inserted

    async def github_early_watchlist(
        self,
        observed_at: datetime,
        within_days: int,
        limit: int,
    ) -> list[dict[str, str]]:
        """Rotate through recently discovered early-lane repos, oldest check first."""

        if limit <= 0:
            return []
        cutoff = observed_at.astimezone(UTC) - timedelta(days=max(0, within_days))
        async with aiosqlite.connect(self.path) as db:
            cursor = await db.execute(
                """
                SELECT repo_id, full_name, first_seen_at, last_checked_at
                FROM github_early_watch
                WHERE first_seen_at >= ?
                ORDER BY last_checked_at, repo_id
                LIMIT ?
                """,
                (cutoff.isoformat(), limit),
            )
            rows = await cursor.fetchall()
        return [
            {
                "repo_id": str(repo_id),
                "full_name": str(full_name),
                "first_seen_at": str(first_seen_at),
                "last_checked_at": str(last_checked_at),
            }
            for repo_id, full_name, first_seen_at, last_checked_at in rows
        ]

    async def github_snapshots(self, repo_id: str) -> list[dict[str, Any]]:
        """Return immutable snapshots in observation order (primarily for audits/tests)."""

        async with aiosqlite.connect(self.path) as db:
            cursor = await db.execute(
                """
                SELECT metadata_json, file_ref FROM github_repo_snapshots
                WHERE repo_id = ? ORDER BY observed_at, snapshot_id
                """,
                (repo_id,),
            )
            rows = await cursor.fetchall()
        values: list[dict[str, Any]] = []
        for metadata_json, file_ref in rows:
            value = json.loads(str(metadata_json))
            value["file_ref"] = str(file_ref)
            values.append(value)
        return values

    async def baseline(self, source: str) -> int | None:
        async with aiosqlite.connect(self.path) as db:
            cursor = await db.execute(
                "SELECT fetched_count FROM source_baselines WHERE source = ?", (source,)
            )
            row = await cursor.fetchone()
        return int(row[0]) if row else None

    async def set_baseline(self, source: str, fetched_count: int) -> None:
        now = datetime.now(UTC).isoformat()
        async with aiosqlite.connect(self.path) as db:
            await db.execute(
                """
                INSERT INTO source_baselines(source, fetched_count, updated_at) VALUES (?, ?, ?)
                ON CONFLICT(source) DO UPDATE SET
                    fetched_count=excluded.fetched_count, updated_at=excluded.updated_at
                """,
                (source, fetched_count, now),
            )
            await db.commit()

    async def record_run(
        self, run_id: str, date: str, attempt: int, status: str, path: Path
    ) -> None:
        now = datetime.now(UTC).isoformat()
        async with aiosqlite.connect(self.path) as db:
            await db.execute(
                """
                INSERT INTO runs(run_id,date,attempt,status,path,created_at,updated_at)
                VALUES (?,?,?,?,?,?,?)
                ON CONFLICT(run_id) DO UPDATE SET status=excluded.status,updated_at=excluded.updated_at
                """,
                (run_id, date, attempt, status, str(path), now, now),
            )
            await db.commit()

    async def has_run_for_date(self, date: str) -> bool:
        async with aiosqlite.connect(self.path) as db:
            cursor = await db.execute("SELECT 1 FROM runs WHERE date = ? LIMIT 1", (date,))
            return await cursor.fetchone() is not None

    async def has_daily_run_in_progress_or_done(
        self,
        date: str,
        *,
        now: datetime | None = None,
        running_stale_after_minutes: int = 18,
    ) -> bool:
        """Return true when another automatic daily collection must not start."""

        cutoff = (now or datetime.now(UTC)) - timedelta(
            minutes=max(1, running_stale_after_minutes)
        )
        async with aiosqlite.connect(self.path) as db:
            cursor = await db.execute(
                """
                SELECT 1 FROM runs
                WHERE date = ? AND (
                    handoff_state IN (
                        'sealed', 'queued', 'agent_complete', 'publish_pending',
                        'local_complete', 'published'
                    )
                    OR status IN ('success', 'partial', 'quiet')
                    OR (status = 'running' AND updated_at >= ?)
                )
                LIMIT 1
                """,
                (date, cutoff.isoformat()),
            )
            return await cursor.fetchone() is not None

    async def pop_expired_x_items(self, now: datetime | None = None) -> list[SourceItem]:
        cutoff = (now or datetime.now(UTC)).isoformat()
        async with aiosqlite.connect(self.path) as db:
            cursor = await db.execute(
                "SELECT payload_json FROM source_items WHERE expires_at IS NOT NULL AND expires_at < ?",
                (cutoff,),
            )
            rows = await cursor.fetchall()
            await db.execute(
                "DELETE FROM source_items WHERE expires_at IS NOT NULL AND expires_at < ?",
                (cutoff,),
            )
            await db.commit()
        return [SourceItem.model_validate_json(row[0]) for row in rows]

    async def pop_x_post(self, post_id: str) -> list[SourceItem]:
        patterns = (f"x_list:{post_id}", f"x_for_you:{post_id}")
        async with aiosqlite.connect(self.path) as db:
            cursor = await db.execute(
                "SELECT payload_json FROM source_items WHERE item_id IN (?, ?)", patterns
            )
            rows = await cursor.fetchall()
            await db.execute("DELETE FROM source_items WHERE item_id IN (?, ?)", patterns)
            await db.commit()
        return [SourceItem.model_validate_json(row[0]) for row in rows]


def source_group(item: SourceItem) -> str:
    if item.source in {"x_list", "x_for_you"}:
        return item.source
    if item.source in {"github", "github_trending"}:
        return "github"
    if item.source in {"arxiv", "huggingface"}:
        return "papers"
    if item.source == "hackernews":
        return "hackernews"
    return "articles"


def load_jsonl(path: Path) -> list[dict[str, object]]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def x_expiry(observed_at: datetime, retention_days: int) -> datetime:
    return observed_at + timedelta(days=retention_days)

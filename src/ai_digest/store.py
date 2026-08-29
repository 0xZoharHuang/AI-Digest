from __future__ import annotations

import json
import shutil
from collections.abc import Iterable
from datetime import UTC, datetime, timedelta
from pathlib import Path

import aiosqlite

from .models import FetchManifest, SourceItem
from .utils import atomic_write_json, atomic_write_text, sha256_bytes


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
                temporary = path.with_suffix(path.suffix + ".tmp")
                temporary.write_bytes(raw)
                temporary.replace(path)
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
                    expires_at TEXT
                );
                CREATE INDEX IF NOT EXISTS idx_source_items_pending
                    ON source_items(delivered_run_id, handoff_at);
                CREATE INDEX IF NOT EXISTS idx_source_items_source
                    ON source_items(source, surface);

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
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                """
            )
            await db.commit()

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
                WHERE delivered_run_id IS NULL AND handoff_at >= ? AND handoff_at < ?
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

    async def get_cursor(self, source: str) -> str | None:
        async with aiosqlite.connect(self.path) as db:
            cursor = await db.execute("SELECT value FROM cursors WHERE source = ?", (source,))
            row = await cursor.fetchone()
        return row[0] if row else None

    async def set_cursor(self, source: str, value: str | None) -> None:
        now = datetime.now(UTC).isoformat()
        async with aiosqlite.connect(self.path) as db:
            await db.execute(
                """
                INSERT INTO cursors(source, value, updated_at) VALUES (?, ?, ?)
                ON CONFLICT(source) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at
                """,
                (source, value, now),
            )
            await db.commit()

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

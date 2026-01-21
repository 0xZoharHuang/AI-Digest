"""Progress tracking for checkpoint/resume functionality."""

import json
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

from pydantic import BaseModel


class RunProgress(BaseModel):
    """Progress state for a run."""

    run_id: str
    started_at: datetime
    current_phase: str  # crawl, filter, research, integrate, output
    total_tweets: int = 0
    processed_tweets: int = 0
    current_tweet_id: Optional[str] = None
    last_updated: datetime = datetime.now()


class ProgressTracker:
    """Track progress for checkpoint/resume functionality."""

    def __init__(self, temp_dir: str | Path = "data/temp"):
        self.temp_dir = Path(temp_dir)
        self.temp_dir.mkdir(parents=True, exist_ok=True)

    def _get_progress_file(self, run_id: str) -> Path:
        return self.temp_dir / f"progress_{run_id}.json"

    def _get_results_file(self, run_id: str) -> Path:
        return self.temp_dir / f"results_{run_id}.json"

    def save_progress(self, run_id: str, state: dict[str, Any]) -> None:
        """Save current run progress."""
        progress_file = self._get_progress_file(run_id)
        state["last_updated"] = datetime.now().isoformat()
        with open(progress_file, "w", encoding="utf-8") as f:
            json.dump(state, f, ensure_ascii=False, indent=2)

    def load_progress(self, run_id: str) -> Optional[dict[str, Any]]:
        """Load progress for a run. Returns None if not found."""
        progress_file = self._get_progress_file(run_id)
        if not progress_file.exists():
            return None
        with open(progress_file, "r", encoding="utf-8") as f:
            return json.load(f)

    def save_result(self, run_id: str, tweet_id: str, result: dict[str, Any]) -> None:
        """Save a single research result (append to results file)."""
        results_file = self._get_results_file(run_id)

        # Load existing results
        results = []
        if results_file.exists():
            with open(results_file, "r", encoding="utf-8") as f:
                results = json.load(f)

        # Append new result
        results.append({"tweet_id": tweet_id, "result": result, "saved_at": datetime.now().isoformat()})

        # Save back
        with open(results_file, "w", encoding="utf-8") as f:
            json.dump(results, f, ensure_ascii=False, indent=2)

    def load_results(self, run_id: str) -> list[dict[str, Any]]:
        """Load all saved results for a run."""
        results_file = self._get_results_file(run_id)
        if not results_file.exists():
            return []
        with open(results_file, "r", encoding="utf-8") as f:
            return json.load(f)

    def get_completed_tweet_ids(self, run_id: str) -> set[str]:
        """Get set of tweet IDs that have been processed in this run."""
        results = self.load_results(run_id)
        return {r["tweet_id"] for r in results}

    def clear_progress(self, run_id: str) -> None:
        """Clear progress files for a completed run."""
        progress_file = self._get_progress_file(run_id)
        results_file = self._get_results_file(run_id)

        if progress_file.exists():
            progress_file.unlink()
        if results_file.exists():
            results_file.unlink()

    def list_incomplete_runs(self) -> list[str]:
        """List all run IDs that have incomplete progress files."""
        runs = []
        for f in self.temp_dir.glob("progress_*.json"):
            run_id = f.stem.replace("progress_", "")
            progress = self.load_progress(run_id)
            if progress and progress.get("current_phase") != "completed":
                runs.append(run_id)
        return runs

    def archive_results(self, run_id: str, archive_dir: str | Path = "data/reports") -> Optional[Path]:
        """Archive results to reports directory. Returns archive path."""
        results = self.load_results(run_id)
        if not results:
            return None

        archive_path = Path(archive_dir)
        archive_path.mkdir(parents=True, exist_ok=True)

        # Get date from run_id (format: YYYYMMDD_HHMMSS)
        date_str = run_id.split("_")[0] if "_" in run_id else run_id
        archive_file = archive_path / f"results_{date_str}.json"

        with open(archive_file, "w", encoding="utf-8") as f:
            json.dump(results, f, ensure_ascii=False, indent=2)

        return archive_file

"""Account health monitoring for Twitter crawling risk control."""

import json
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import Enum
from pathlib import Path
from typing import Optional


class RiskLevel(Enum):
    """Account risk levels."""

    SAFE = "safe"  # 0-30
    LOW = "low"  # 31-60
    MEDIUM = "medium"  # 61-80
    HIGH = "high"  # 81-100


class ErrorType(Enum):
    """Error types with associated risk weights."""

    CAPTCHA = "captcha"  # +20
    RATE_LIMIT = "rate_limit"  # +15
    OPERATION_RESTRICTED = "operation_restricted"  # +25
    LOGIN_FAILED = "login_failed"  # +30
    CONSECUTIVE_ERROR = "consecutive_error"  # +10 per occurrence
    SUSPICIOUS_ACTIVITY = "suspicious_activity"  # +40
    ACCOUNT_SUSPENDED = "account_suspended"  # +100


# Risk weights for each error type
ERROR_WEIGHTS = {
    ErrorType.CAPTCHA: 20,
    ErrorType.RATE_LIMIT: 15,
    ErrorType.OPERATION_RESTRICTED: 25,
    ErrorType.LOGIN_FAILED: 30,
    ErrorType.CONSECUTIVE_ERROR: 10,
    ErrorType.SUSPICIOUS_ACTIVITY: 40,
    ErrorType.ACCOUNT_SUSPENDED: 100,
}


@dataclass
class AccountStats:
    """Statistics for a single account."""

    username: str
    total_requests: int = 0
    successful_requests: int = 0
    failed_requests: int = 0
    consecutive_errors: int = 0
    error_counts: dict = field(default_factory=dict)
    last_request_time: datetime | None = None
    last_error_time: datetime | None = None
    is_suspended: bool = False
    cooldown_until: datetime | None = None

    def success_rate(self) -> float:
        """Calculate request success rate."""
        if self.total_requests == 0:
            return 1.0
        return self.successful_requests / self.total_requests


class AccountHealthMonitor:
    """Monitor account health and detect pre-ban signals."""

    def __init__(self, decay_hours: int = 24):
        """
        Initialize health monitor.

        Args:
            decay_hours: Hours after which error counts start decaying
        """
        self.accounts: dict[str, AccountStats] = {}
        self.decay_hours = decay_hours

    def _get_or_create_stats(self, username: str) -> AccountStats:
        """Get or create account stats."""
        if username not in self.accounts:
            self.accounts[username] = AccountStats(username=username)
        return self.accounts[username]

    def record_success(self, username: str) -> None:
        """Record a successful request."""
        stats = self._get_or_create_stats(username)
        stats.total_requests += 1
        stats.successful_requests += 1
        stats.consecutive_errors = 0  # Reset consecutive errors
        stats.last_request_time = datetime.now()

    def record_error(self, username: str, error_type: ErrorType) -> None:
        """Record an error and update risk score."""
        stats = self._get_or_create_stats(username)
        stats.total_requests += 1
        stats.failed_requests += 1
        stats.last_error_time = datetime.now()

        # Track error type counts
        if error_type not in stats.error_counts:
            stats.error_counts[error_type] = 0
        stats.error_counts[error_type] += 1

        # Track consecutive errors
        if error_type != ErrorType.CONSECUTIVE_ERROR:
            stats.consecutive_errors += 1

        # Mark suspended if detected
        if error_type == ErrorType.ACCOUNT_SUSPENDED:
            stats.is_suspended = True

    def get_risk_score(self, username: str) -> int:
        """
        Calculate risk score (0-100).

        Scoring:
        - 0-30: Safe
        - 31-60: Low risk
        - 61-80: Medium risk
        - 81-100: High risk
        """
        stats = self._get_or_create_stats(username)

        if stats.is_suspended:
            return 100

        score = 0

        # Add weighted scores for each error type
        for error_type, count in stats.error_counts.items():
            weight = ERROR_WEIGHTS.get(error_type, 10)
            score += weight * count

        # Add consecutive error penalty
        score += stats.consecutive_errors * ERROR_WEIGHTS[ErrorType.CONSECUTIVE_ERROR]

        # Apply time decay - reduce score if no errors recently
        if stats.last_error_time:
            hours_since_error = (datetime.now() - stats.last_error_time).total_seconds() / 3600
            if hours_since_error > self.decay_hours:
                decay_factor = max(0.5, 1 - (hours_since_error - self.decay_hours) / 48)
                score = int(score * decay_factor)

        return min(100, score)

    def get_risk_level(self, username: str) -> RiskLevel:
        """Get risk level for an account."""
        score = self.get_risk_score(username)

        if score <= 30:
            return RiskLevel.SAFE
        elif score <= 60:
            return RiskLevel.LOW
        elif score <= 80:
            return RiskLevel.MEDIUM
        else:
            return RiskLevel.HIGH

    def should_cooldown(self, username: str) -> bool:
        """Check if account needs cooldown period."""
        stats = self._get_or_create_stats(username)

        # Check if in cooldown period
        if stats.cooldown_until and datetime.now() < stats.cooldown_until:
            return True

        # Check risk level
        risk_level = self.get_risk_level(username)
        return risk_level in (RiskLevel.MEDIUM, RiskLevel.HIGH)

    def set_cooldown(self, username: str, hours: float) -> None:
        """Set cooldown period for an account."""
        stats = self._get_or_create_stats(username)
        stats.cooldown_until = datetime.now() + timedelta(hours=hours)

    def mark_suspended(self, username: str) -> None:
        """Mark account as suspended."""
        stats = self._get_or_create_stats(username)
        stats.is_suspended = True
        self.record_error(username, ErrorType.ACCOUNT_SUSPENDED)

    def get_status_summary(self, username: str) -> dict:
        """Get summary of account health status."""
        stats = self._get_or_create_stats(username)
        return {
            "username": username,
            "risk_score": self.get_risk_score(username),
            "risk_level": self.get_risk_level(username).value,
            "success_rate": f"{stats.success_rate():.1%}",
            "total_requests": stats.total_requests,
            "consecutive_errors": stats.consecutive_errors,
            "is_suspended": stats.is_suspended,
            "should_cooldown": self.should_cooldown(username),
        }

    def save_state(self, filepath: str = "data/account_health.json") -> None:
        """Save health state to file for persistence across restarts."""
        state = {}
        for username, stats in self.accounts.items():
            error_counts_serialized = {
                e.value: c for e, c in stats.error_counts.items()
            }
            state[username] = {
                "total_requests": stats.total_requests,
                "successful_requests": stats.successful_requests,
                "failed_requests": stats.failed_requests,
                "consecutive_errors": stats.consecutive_errors,
                "error_counts": error_counts_serialized,
                "last_request_time": stats.last_request_time.isoformat() if stats.last_request_time else None,
                "last_error_time": stats.last_error_time.isoformat() if stats.last_error_time else None,
                "is_suspended": stats.is_suspended,
                "cooldown_until": stats.cooldown_until.isoformat() if stats.cooldown_until else None,
            }

        Path(filepath).parent.mkdir(parents=True, exist_ok=True)
        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(state, f, indent=2, ensure_ascii=False)

    def load_state(self, filepath: str = "data/account_health.json") -> None:
        """Load health state from file."""
        path = Path(filepath)
        if not path.exists():
            return

        with open(path, "r", encoding="utf-8") as f:
            state = json.load(f)

        for username, data in state.items():
            stats = self._get_or_create_stats(username)
            stats.total_requests = data.get("total_requests", 0)
            stats.successful_requests = data.get("successful_requests", 0)
            stats.failed_requests = data.get("failed_requests", 0)
            stats.consecutive_errors = data.get("consecutive_errors", 0)
            stats.is_suspended = data.get("is_suspended", False)

            # Restore error counts
            error_counts_raw = data.get("error_counts", {})
            for error_value, count in error_counts_raw.items():
                try:
                    error_type = ErrorType(error_value)
                    stats.error_counts[error_type] = count
                except ValueError:
                    pass

            # Restore timestamps
            if data.get("last_request_time"):
                stats.last_request_time = datetime.fromisoformat(data["last_request_time"])
            if data.get("last_error_time"):
                stats.last_error_time = datetime.fromisoformat(data["last_error_time"])
            if data.get("cooldown_until"):
                stats.cooldown_until = datetime.fromisoformat(data["cooldown_until"])


def classify_error(error_message: str) -> ErrorType | None:
    """
    Classify error message into ErrorType.

    Args:
        error_message: The error message string

    Returns:
        ErrorType or None if not recognized
    """
    error_lower = error_message.lower()

    if "captcha" in error_lower or "challenge" in error_lower:
        return ErrorType.CAPTCHA

    if "rate limit" in error_lower or "429" in error_lower or "too many" in error_lower:
        return ErrorType.RATE_LIMIT

    if "restricted" in error_lower or "limited" in error_lower or "disabled" in error_lower:
        return ErrorType.OPERATION_RESTRICTED

    if "login" in error_lower or "auth" in error_lower or "credential" in error_lower:
        return ErrorType.LOGIN_FAILED

    if "unusual activity" in error_lower or "suspicious" in error_lower:
        return ErrorType.SUSPICIOUS_ACTIVITY

    if "suspended" in error_lower or "locked" in error_lower or "banned" in error_lower:
        return ErrorType.ACCOUNT_SUSPENDED

    return None

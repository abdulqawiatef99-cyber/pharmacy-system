"""Runtime settings from environment variables (12-factor). Secrets such as the
AI API key are read from the environment only, never stored in the database
or committed to the repository."""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Settings:
    data_dir: Path
    db_path: Path
    upload_dir: Path
    max_upload_bytes: int
    anthropic_api_key: str | None
    ai_model: str
    ai_timeout_s: int
    cookie_secure: bool
    short_expiry_days: int
    total_tolerance_piasters: int
    backup_dir: Path | None = None

    @property
    def backup_path(self) -> Path:
        """Automatic backups: next to the data folder (installed: %LOCALAPPDATA%\\PharmacyPlatform\\backups)."""
        return self.backup_dir or self.data_dir.parent / "backups"


def default_data_dir() -> Path:
    """Without PHARM_DATA_DIR: the installed program's data folder on Windows (#80), so a maintenance command
    run from any folder reaches the real data instead of a new empty one; elsewhere ./data as before."""
    local = os.environ.get("LOCALAPPDATA")
    if os.name == "nt" and local:
        return Path(local) / "PharmacyPlatform" / "data"
    return Path("data")


def load() -> Settings:
    data = (Path(os.environ["PHARM_DATA_DIR"]) if os.environ.get("PHARM_DATA_DIR") else default_data_dir()).resolve()
    return Settings(
        data_dir=data,
        db_path=Path(os.environ.get("PHARM_DB_PATH", data / "pharmacy.db")),
        upload_dir=Path(os.environ.get("PHARM_UPLOAD_DIR", data / "uploads")),
        max_upload_bytes=int(os.environ.get("PHARM_MAX_UPLOAD_MB", "15")) * 1024 * 1024,
        anthropic_api_key=os.environ.get("ANTHROPIC_API_KEY") or None,
        ai_model=os.environ.get("PHARM_AI_MODEL", "claude-sonnet-5-5"),
        ai_timeout_s=int(os.environ.get("PHARM_AI_TIMEOUT", "90")),
        cookie_secure=os.environ.get("PHARM_COOKIE_SECURE", "0") == "1",
        short_expiry_days=int(os.environ.get("PHARM_SHORT_EXPIRY_DAYS", "180")),
        total_tolerance_piasters=int(os.environ.get("PHARM_TOTAL_TOLERANCE", "100")),
        backup_dir=Path(os.environ["PHARM_BACKUP_DIR"]).resolve() if os.environ.get("PHARM_BACKUP_DIR") else None,
    )

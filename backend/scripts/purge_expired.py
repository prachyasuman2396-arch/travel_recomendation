"""Delete provider content past its retention window (schedule e.g. hourly via cron)."""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.database.session import session_scope
from app.repositories.admin_repository import AdminRepository

if __name__ == "__main__":
    with session_scope() as db:
        print(json.dumps(AdminRepository(db).purge_expired()))

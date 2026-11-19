"""Generate the EDA report from the current database into data/reports/."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.config import get_settings
from app.database.session import get_engine
from app.eda.report import load_frames, write_report

if __name__ == "__main__":
    out = write_report(load_frames(get_engine()), get_settings().data_dir / "reports")
    print(f"EDA report written to {out}")

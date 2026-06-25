"""Generate a local Markdown daily analytics report."""

from __future__ import annotations

import argparse
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import analytics_service
import database
from security import Actor


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate customer-service daily analytics report.")
    parser.add_argument("--date", default="yesterday", help="Report date: YYYY-MM-DD, today, or yesterday.")
    parser.add_argument("--output-dir", default=os.path.join(ROOT, "reports", "daily"), help="Directory for Markdown reports.")
    args = parser.parse_args()

    database.init_db()
    actor = Actor("daily-analytics-cli", "data_analysis", {})
    with database.session_scope() as session:
        analytics = analytics_service.get_usage_analytics(session, actor, args.date)
        path = analytics_service.write_markdown_report(analytics, args.output_dir)
    print(path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""Create a timestamped database backup.

MySQL URLs use mysqldump + gzip. SQLite URLs copy the database file.
"""

from __future__ import annotations

import gzip
import os
import shutil
import subprocess
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse


def backup(database_url: str | None = None, output_dir: str = "backups") -> Path:
    url = database_url or os.getenv("DATABASE_URL", "sqlite+pysqlite:///orders.db")
    parsed = urlparse(url)
    out_dir = Path(output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.utcnow().strftime("%Y%m%dT%H%M%SZ")

    if parsed.scheme.startswith("sqlite"):
        db_path = Path(parsed.path.lstrip("/"))
        if not db_path.exists():
            db_path = Path("orders.db")
        target = out_dir / f"sqlite-{stamp}.db"
        shutil.copy2(db_path, target)
        return target

    if not parsed.scheme.startswith("mysql"):
        raise ValueError(f"Unsupported DATABASE_URL scheme: {parsed.scheme}")

    database = parsed.path.lstrip("/")
    target = out_dir / f"mysql-{database}-{stamp}.sql.gz"
    cmd = [
        "mysqldump",
        f"--host={parsed.hostname or 'localhost'}",
        f"--port={parsed.port or 3306}",
        f"--user={parsed.username or ''}",
        f"--password={parsed.password or ''}",
        database,
    ]
    dump = subprocess.run(cmd, check=True, stdout=subprocess.PIPE)
    with gzip.open(target, "wb") as f:
        f.write(dump.stdout)
    return target


if __name__ == "__main__":
    print(backup())

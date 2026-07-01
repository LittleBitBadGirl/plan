import os
import sqlite3
import tempfile

import pytest

from app.services.backup_service import backup_sqlite_file


@pytest.mark.asyncio
async def test_backup_sqlite_file_copies_data():
    with tempfile.TemporaryDirectory() as tmp:
        src = os.path.join(tmp, "source.db")
        dst = os.path.join(tmp, "backup.db")

        conn = sqlite3.connect(src)
        conn.execute("CREATE TABLE t (id INTEGER PRIMARY KEY, v TEXT)")
        conn.execute("INSERT INTO t (v) VALUES ('ok')")
        conn.commit()
        conn.close()

        backup_sqlite_file(src, dst)

        assert os.path.isfile(dst)
        check = sqlite3.connect(dst)
        row = check.execute("SELECT v FROM t").fetchone()
        check.close()
        assert row[0] == "ok"

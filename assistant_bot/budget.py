"""Optional persistent API request ceiling, including failed attempts."""

import sqlite3
from contextlib import closing
from pathlib import Path


def reserve_request(path, scope, maximum):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(path, timeout=5)) as connection, connection:
        connection.execute("CREATE TABLE IF NOT EXISTS llm_budget (scope TEXT PRIMARY KEY, used INTEGER NOT NULL)")
        connection.execute("BEGIN IMMEDIATE")
        connection.execute("INSERT OR IGNORE INTO llm_budget VALUES (?, 0)", (scope,))
        changed = connection.execute("UPDATE llm_budget SET used=used+1 WHERE scope=? AND used<?", (scope, maximum)).rowcount
        return changed == 1

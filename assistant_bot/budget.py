"""Optional persistent API request ceiling, including failed attempts."""

import sqlite3
from contextlib import closing
from pathlib import Path
from secrets import token_urlsafe
from threading import BoundedSemaphore
from time import time


def reserve_request(path, scope, maximum):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(path, timeout=5)) as connection, connection:
        connection.execute("CREATE TABLE IF NOT EXISTS llm_budget (scope TEXT PRIMARY KEY, used INTEGER NOT NULL)")
        connection.execute("BEGIN IMMEDIATE")
        connection.execute("INSERT OR IGNORE INTO llm_budget VALUES (?, 0)", (scope,))
        changed = connection.execute("UPDATE llm_budget SET used=used+1 WHERE scope=? AND used<?", (scope, maximum)).rowcount
        return changed == 1


class PublicAccess:
    CONSENT_VERSION = "v1"

    def __init__(self, path, provider, *, global_limit=30, user_limit=5, interval=30, concurrent=2):
        if not path or str(path) == ":memory:":
            raise ValueError("Публичный режим требует постоянный LLM_BUDGET_PATH.")
        if not 1 <= user_limit <= global_limit or not 1 <= interval <= 86400 or not 1 <= concurrent <= 4:
            raise ValueError("Проверьте публичные квоты, интервал LLM_PUBLIC_MIN_INTERVAL_SECONDS (1–86400) "
                             "и LLM_PUBLIC_MAX_CONCURRENT (1–4).")
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.provider = provider
        self.policy = f"{provider}:{self.CONSENT_VERSION}"
        self.global_limit = global_limit
        self.user_limit = user_limit
        self.interval = interval
        self.slots = BoundedSemaphore(concurrent)
        with closing(self.connect()) as db, db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS llm_public_consent (
                    chat_id INTEGER PRIMARY KEY, policy TEXT NOT NULL,
                    decision TEXT NOT NULL, nonce TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS llm_public_global (
                    id INTEGER PRIMARY KEY CHECK (id = 1), used INTEGER NOT NULL
                );
                CREATE TABLE IF NOT EXISTS llm_public_user (
                    chat_id INTEGER PRIMARY KEY, used INTEGER NOT NULL, last_attempt REAL NOT NULL
                );
            """)

    def connect(self):
        return sqlite3.connect(self.path, timeout=5)

    def decision(self, chat_id, db=None):
        if db is None:
            with closing(self.connect()) as connection:
                return self.decision(chat_id, connection)
        row = db.execute("SELECT decision FROM llm_public_consent WHERE chat_id=? AND policy=?",
                         (chat_id, self.policy)).fetchone()
        return row[0] if row else ""

    def issue_notice(self, chat_id):
        nonce = token_urlsafe(9)
        with closing(self.connect()) as db, db:
            db.execute("""
                INSERT INTO llm_public_consent VALUES (?, ?, '', ?)
                ON CONFLICT(chat_id) DO UPDATE SET
                    decision=CASE WHEN policy=excluded.policy THEN decision ELSE '' END,
                    policy=excluded.policy, nonce=excluded.nonce
            """, (chat_id, self.policy, nonce))
        return nonce

    def choose(self, chat_id, nonce, decision):
        if not nonce or decision not in {"granted", "declined"}:
            return False
        with closing(self.connect()) as db, db:
            return db.execute("""
                UPDATE llm_public_consent SET decision=?, nonce=''
                WHERE chat_id=? AND policy=? AND nonce=?
            """, (decision, chat_id, self.policy, nonce)).rowcount == 1

    def reserve(self, chat_id):
        with closing(self.connect()) as db, db:
            # One write transaction protects consent, both counters and frequency across processes.
            db.execute("BEGIN IMMEDIATE")
            if self.decision(chat_id, db) != "granted":
                return "consent"
            global_row = db.execute("SELECT used FROM llm_public_global WHERE id=1").fetchone()
            if global_row and global_row[0] >= self.global_limit:
                return "public_global"
            user_row = db.execute("SELECT used, last_attempt FROM llm_public_user WHERE chat_id=?",
                                  (chat_id,)).fetchone()
            now = time()
            if user_row and user_row[0] >= self.user_limit:
                return "public_user"
            if user_row and now < user_row[1] + self.interval:
                return "public_rate"
            db.execute("""
                INSERT INTO llm_public_global VALUES (1, 1)
                ON CONFLICT(id) DO UPDATE SET used=used+1
            """)
            db.execute("""
                INSERT INTO llm_public_user VALUES (?, 1, ?)
                ON CONFLICT(chat_id) DO UPDATE SET used=used+1, last_attempt=excluded.last_attempt
            """, (chat_id, now))
            return ""

import json
import sqlite3
from contextlib import closing
from dataclasses import dataclass, field
from pathlib import Path
from uuid import uuid4


@dataclass
class Conversation:
    profile: dict = field(default_factory=dict)
    pending: dict = field(default_factory=dict)
    action: dict = field(default_factory=dict)
    proposal: dict = field(default_factory=dict)
    topic: str = ""
    language: str = ""


class ConversationStore:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        with closing(self.connect()) as connection, connection:
            connection.execute("PRAGMA journal_mode=WAL")
            connection.execute("""
                CREATE TABLE IF NOT EXISTS conversations (
                    chat_id INTEGER PRIMARY KEY,
                    content TEXT NOT NULL
                )
            """)

    def connect(self):
        return sqlite3.connect(self.path, timeout=5)

    def get(self, chat_id: int) -> Conversation:
        with closing(self.connect()) as connection:
            record = connection.execute(
                "SELECT content FROM conversations WHERE chat_id = ?", (chat_id,)
            ).fetchone()
        return Conversation(**json.loads(record[0])) if record else Conversation()

    def save(self, chat_id: int, conversation: Conversation):
        payload = json.dumps(vars(conversation), ensure_ascii=False)
        with closing(self.connect()) as connection, connection:
            connection.execute("""
                INSERT INTO conversations (chat_id, content) VALUES (?, ?)
                ON CONFLICT (chat_id) DO UPDATE SET content = excluded.content
            """, (chat_id, payload))

    @staticmethod
    def new_action(title: str, passage: str, source: str) -> dict:
        return {
            "id": uuid4().hex[:10],
            "title": title,
            "passage": passage,
            "source": source,
            "status": "suggested",
        }

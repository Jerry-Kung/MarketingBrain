"""应用状态持久化（SQLite）。

V0 用 SQLite + 本地持久化目录保存任务、运行事件、证据索引和结果。
通过 Repository 接口为 V1 更换数据库留口（抽象见文档《V0开发计划》3.4 节）。

设计：
- TaskRepository: 任务主档（原始输入、解析意图、快照、状态、结果）
- EventRepository: 追加式运行事件（审计）
"""
import json
import sqlite3
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _new_id() -> str:
    """生成唯一 ID（uuid4 hex）。"""
    return uuid.uuid4().hex


@dataclass
class TaskRecord:
    """任务主档记录。"""

    task_id: str = field(default_factory=_new_id)
    status: str = "pending"          # pending / running / success / failed
    raw_input: str = ""
    parsed_intent: dict = field(default_factory=dict)
    snapshot: dict = field(default_factory=dict)
    result: dict = field(default_factory=dict)
    error: Optional[str] = None
    created_at: str = field(default_factory=_now)
    updated_at: str = field(default_factory=_now)

    def to_dict(self) -> dict:
        return {
            "task_id": self.task_id,
            "status": self.status,
            "raw_input": self.raw_input,
            "parsed_intent": self.parsed_intent,
            "snapshot": self.snapshot,
            "result": self.result,
            "error": self.error,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }


@dataclass
class EventRecord:
    """运行审计事件。"""

    event_id: str = field(default_factory=_new_id)
    task_id: str = ""
    event_type: str = ""            # start / tool_call / evidence / finish / ...
    payload: dict = field(default_factory=dict)
    seq: int = 0
    created_at: str = field(default_factory=_now)


class _SQLiteBase:
    """SQLite 基类：负责连接管理与 schema 初始化。"""

    def __init__(self, db_path: str):
        self.db_path = db_path
        # check_same_thread=False 便于 FastAPI 多线程；连接池由 sqlite 自身管理
        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row

    def init_schema(self) -> None:
        raise NotImplementedError

    @staticmethod
    def _json_dumps(obj) -> str:
        return json.dumps(obj, ensure_ascii=False, default=str)

    @staticmethod
    def _json_loads(raw) -> dict:
        if not raw:
            return {}
        try:
            return json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            return {}

    def close(self) -> None:
        try:
            self._conn.close()
        except Exception:
            pass


class TaskRepository(_SQLiteBase):
    """任务持久化。"""

    def init_schema(self) -> None:
        self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS tasks (
                task_id      TEXT PRIMARY KEY,
                status       TEXT NOT NULL,
                raw_input    TEXT NOT NULL DEFAULT '',
                parsed_intent TEXT,
                snapshot     TEXT,
                result       TEXT,
                error        TEXT,
                created_at   TEXT NOT NULL,
                updated_at   TEXT NOT NULL
            )
            """
        )
        self._conn.commit()

    def create_task(
        self,
        raw_input: str,
        parsed_intent: Optional[dict],
        snapshot: Optional[dict],
    ) -> TaskRecord:
        task = TaskRecord(
            raw_input=raw_input,
            parsed_intent=parsed_intent or {},
            snapshot=snapshot or {},
        )
        self._conn.execute(
            """
            INSERT INTO tasks (task_id, status, raw_input, parsed_intent,
                               snapshot, result, error, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                task.task_id,
                task.status,
                task.raw_input,
                self._json_dumps(task.parsed_intent),
                self._json_dumps(task.snapshot),
                self._json_dumps(task.result),
                task.error,
                task.created_at,
                task.updated_at,
            ),
        )
        self._conn.commit()
        return self.get_task(task.task_id)

    def get_task(self, task_id: str) -> Optional[TaskRecord]:
        row = self._conn.execute(
            "SELECT * FROM tasks WHERE task_id=?", (task_id,)
        ).fetchone()
        if not row:
            return None
        return self._row_to_task(row)

    def _row_to_task(self, row) -> TaskRecord:
        return TaskRecord(
            task_id=row["task_id"],
            status=row["status"],
            raw_input=row["raw_input"],
            parsed_intent=self._json_loads(row["parsed_intent"]),
            snapshot=self._json_loads(row["snapshot"]),
            result=self._json_loads(row["result"]),
            error=row["error"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

    def update_status(self, task_id: str, status: str) -> None:
        self._conn.execute(
            "UPDATE tasks SET status=?, updated_at=? WHERE task_id=?",
            (status, _now(), task_id),
        )
        self._conn.commit()

    def save_result(self, task_id: str, result: dict) -> None:
        """保存结果，并将状态标记为 success（若原为 running/pending）。"""
        self._conn.execute(
            "UPDATE tasks SET result=?, status='success', updated_at=? WHERE task_id=?",
            (self._json_dumps(result), _now(), task_id),
        )
        self._conn.commit()

    def mark_failed(self, task_id: str, error: str) -> None:
        self._conn.execute(
            "UPDATE tasks SET status='failed', error=?, updated_at=? WHERE task_id=?",
            (error, _now(), task_id),
        )
        self._conn.commit()

    def list_tasks(self, limit: int = 50) -> list[TaskRecord]:
        rows = self._conn.execute(
            "SELECT * FROM tasks ORDER BY created_at DESC LIMIT ?", (limit,)
        ).fetchall()
        return [self._row_to_task(r) for r in rows]


class EventRepository(_SQLiteBase):
    """追加式运行事件（审计）。"""

    def init_schema(self) -> None:
        self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS events (
                event_id   TEXT PRIMARY KEY,
                task_id    TEXT NOT NULL,
                event_type TEXT NOT NULL,
                payload    TEXT,
                seq        INTEGER NOT NULL,
                created_at TEXT NOT NULL
            )
            """
        )
        self._conn.commit()

    def append_event(self, task_id: str, event_type: str, payload: dict) -> EventRecord:
        # 自增 seq，保证事件顺序
        cur = self._conn.execute(
            "SELECT COALESCE(MAX(seq),0)+1 AS nxt FROM events WHERE task_id=?",
            (task_id,),
        ).fetchone()
        seq = cur["nxt"]
        ev = EventRecord(
            task_id=task_id,
            event_type=event_type,
            payload=payload,
            seq=seq,
        )
        self._conn.execute(
            """
            INSERT INTO events (event_id, task_id, event_type, payload, seq, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                ev.event_id,
                ev.task_id,
                ev.event_type,
                self._json_dumps(ev.payload),
                ev.seq,
                ev.created_at,
            ),
        )
        self._conn.commit()
        return ev

    def get_events(self, task_id: str) -> list[EventRecord]:
        rows = self._conn.execute(
            "SELECT * FROM events WHERE task_id=? ORDER BY seq ASC", (task_id,)
        ).fetchall()
        return [
            EventRecord(
                event_id=r["event_id"],
                task_id=r["task_id"],
                event_type=r["event_type"],
                payload=self._json_loads(r["payload"]),
                seq=r["seq"],
                created_at=r["created_at"],
            )
            for r in rows
        ]

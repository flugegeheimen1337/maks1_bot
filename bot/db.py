from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import aiosqlite

from bot.domain import (
    NO_USERNAME,
    PAGE_SIZE,
    SKIPPED,
    Answers,
    label_of,
    period_start,
    slots_phrase,
    utc_now_iso,
)

SCHEMA = """
CREATE TABLE IF NOT EXISTS applications (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL UNIQUE,
    tg_user_id INTEGER NOT NULL,
    tg_username TEXT NOT NULL,
    tg_name TEXT NOT NULL,
    consent_at TEXT NOT NULL,
    problem TEXT NOT NULL,
    free_text TEXT NOT NULL,
    therapy_exp TEXT NOT NULL,
    format TEXT NOT NULL,
    tz TEXT NOT NULL,
    budget TEXT NOT NULL,
    slots TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'new',
    reply_in_chat INTEGER NOT NULL DEFAULT 0,
    curator_message_id INTEGER,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS supplements (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    application_id INTEGER NOT NULL REFERENCES applications(id) ON DELETE CASCADE,
    text TEXT NOT NULL,
    curator_message_ids TEXT NOT NULL DEFAULT '[]',
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS screen_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL,
    tg_user_id INTEGER NOT NULL,
    screen TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE(session_id, screen)
);

CREATE TABLE IF NOT EXISTS audit_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    actor TEXT NOT NULL,
    action TEXT NOT NULL,
    target TEXT,
    details TEXT,
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_applications_created ON applications(created_at);
CREATE INDEX IF NOT EXISTS idx_applications_status ON applications(status);
CREATE INDEX IF NOT EXISTS idx_applications_problem ON applications(problem);
CREATE INDEX IF NOT EXISTS idx_applications_user ON applications(tg_user_id);
CREATE INDEX IF NOT EXISTS idx_applications_username ON applications(tg_username);
CREATE INDEX IF NOT EXISTS idx_screen_events_session ON screen_events(session_id);
"""


@dataclass
class Application:
    id: int
    session_id: str
    tg_user_id: int
    tg_username: str
    tg_name: str
    consent_at: str
    problem: str
    free_text: str
    therapy_exp: str
    format: str
    tz: str
    budget: str
    slots: list[str]
    status: str
    reply_in_chat: bool
    curator_message_id: int | None
    created_at: str
    updated_at: str

    def answers(self) -> Answers:
        return Answers(
            problem=self.problem,
            free_text=self.free_text,
            therapy_exp=self.therapy_exp,
            format=self.format,
            tz=self.tz,
            budget=self.budget,
            slots=list(self.slots),
        )

    def csv_row(self) -> dict:
        return {
            "id": self.id,
            "created_at": self.created_at,
            "status": self.status,
            "tg_user_id": self.tg_user_id,
            "tg_username": self.tg_username,
            "tg_name": self.tg_name,
            "consent_at": self.consent_at,
            "problem": label_of(self.problem, card=True),
            "free_text": "" if self.free_text == SKIPPED else self.free_text,
            "therapy_exp": label_of(self.therapy_exp, card=True),
            "format": label_of(self.format, card=True),
            "tz": "" if self.tz == SKIPPED else label_of(self.tz, card=True),
            "budget": label_of(self.budget, card=True),
            "slots": slots_phrase(self.slots, card=True),
            "reply_in_chat": "да" if self.reply_in_chat else "нет",
        }


@dataclass
class Supplement:
    id: int
    application_id: int
    text: str
    curator_message_ids: list[int]
    created_at: str


@dataclass
class Page:
    items: list[Application]
    total: int
    page: int
    page_size: int

    @property
    def pages(self) -> int:
        if self.total == 0:
            return 1
        return (self.total + self.page_size - 1) // self.page_size


def _application_from_row(row: aiosqlite.Row) -> Application:
    slots = json.loads(row["slots"])
    return Application(
        id=row["id"],
        session_id=row["session_id"],
        tg_user_id=row["tg_user_id"],
        tg_username=row["tg_username"],
        tg_name=row["tg_name"],
        consent_at=row["consent_at"],
        problem=row["problem"],
        free_text=row["free_text"],
        therapy_exp=row["therapy_exp"],
        format=row["format"],
        tz=row["tz"],
        budget=row["budget"],
        slots=slots if isinstance(slots, list) else [],
        status=row["status"],
        reply_in_chat=bool(row["reply_in_chat"]),
        curator_message_id=row["curator_message_id"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


class Database:
    def __init__(self, path: str):
        self.path = path
        self._conn: aiosqlite.Connection | None = None

    async def connect(self) -> None:
        if self.path != ":memory:":
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self._conn = await aiosqlite.connect(self.path)
        self._conn.row_factory = aiosqlite.Row
        await self._conn.execute("PRAGMA foreign_keys = ON")
        await self._conn.executescript(SCHEMA)
        await self._conn.commit()

    async def close(self) -> None:
        if self._conn is not None:
            await self._conn.close()
            self._conn = None

    @property
    def conn(self) -> aiosqlite.Connection:
        if self._conn is None:
            raise RuntimeError("База не открыта.")
        return self._conn

    async def track_screen(self, session_id: str, user_id: int, screen: str) -> None:
        await self.conn.execute(
            """
            INSERT OR IGNORE INTO screen_events (session_id, tg_user_id, screen, created_at)
            VALUES (?, ?, ?, ?)
            """,
            (session_id, user_id, screen, utc_now_iso()),
        )
        await self.conn.commit()

    async def funnel_sessions(self) -> list[set[str]]:
        cursor = await self.conn.execute(
            "SELECT session_id, screen FROM screen_events ORDER BY session_id"
        )
        grouped: dict[str, set[str]] = {}
        for row in await cursor.fetchall():
            grouped.setdefault(row["session_id"], set()).add(row["screen"])
        return list(grouped.values())

    async def create_application(
        self,
        *,
        session_id: str,
        user_id: int,
        username: str,
        full_name: str,
        consent_at: str,
        answers: Answers,
        reply_in_chat: bool,
        created_at: str | None = None,
    ) -> Application:
        moment = created_at or utc_now_iso()
        stored_username = username if username else NO_USERNAME
        try:
            cursor = await self.conn.execute(
                """
                INSERT INTO applications (
                    session_id, tg_user_id, tg_username, tg_name, consent_at,
                    problem, free_text, therapy_exp, format, tz, budget, slots,
                    status, reply_in_chat, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'new', ?, ?, ?)
                """,
                (
                    session_id,
                    user_id,
                    stored_username,
                    full_name or SKIPPED,
                    consent_at,
                    answers.problem,
                    answers.free_text if answers.free_text is not None else SKIPPED,
                    answers.therapy_exp,
                    answers.format,
                    answers.tz if answers.tz else SKIPPED,
                    answers.budget,
                    json.dumps(answers.slots, ensure_ascii=False),
                    1 if reply_in_chat else 0,
                    moment,
                    moment,
                ),
            )
            await self.conn.commit()
            created = await self.get_application(int(cursor.lastrowid))
        except sqlite3.IntegrityError:
            await self.conn.rollback()
            existing = await self.get_by_session(session_id)
            if existing is None:
                raise
            return existing
        if created is None:
            raise RuntimeError("Заявка не сохранилась.")
        return created

    async def get_application(self, application_id: int) -> Application | None:
        cursor = await self.conn.execute(
            "SELECT * FROM applications WHERE id = ?",
            (application_id,),
        )
        row = await cursor.fetchone()
        return _application_from_row(row) if row else None

    async def get_by_session(self, session_id: str) -> Application | None:
        cursor = await self.conn.execute(
            "SELECT * FROM applications WHERE session_id = ?",
            (session_id,),
        )
        row = await cursor.fetchone()
        return _application_from_row(row) if row else None

    async def latest_for_user(self, user_id: int) -> Application | None:
        cursor = await self.conn.execute(
            """
            SELECT * FROM applications
            WHERE tg_user_id = ?
            ORDER BY id DESC
            LIMIT 1
            """,
            (user_id,),
        )
        row = await cursor.fetchone()
        return _application_from_row(row) if row else None

    async def set_curator_message(self, application_id: int, message_id: int | None) -> None:
        await self.conn.execute(
            "UPDATE applications SET curator_message_id = ?, updated_at = ? WHERE id = ?",
            (message_id, utc_now_iso(), application_id),
        )
        await self.conn.commit()

    async def set_status(self, application_id: int, status: str) -> Application | None:
        if status not in {"new", "in_progress", "closed"}:
            raise ValueError(f"Неизвестный статус: {status}")
        await self.conn.execute(
            "UPDATE applications SET status = ?, updated_at = ? WHERE id = ?",
            (status, utc_now_iso(), application_id),
        )
        await self.conn.commit()
        return await self.get_application(application_id)

    def _filter_clause(
        self,
        *,
        status: str | None,
        topic: str | None,
        period: str | None,
        now: datetime | None,
        tz_name: str,
    ) -> tuple[str, list]:
        clauses: list[str] = []
        params: list = []
        if status:
            clauses.append("status = ?")
            params.append(status)
        if topic:
            clauses.append("problem = ?")
            params.append(topic)
        start = period_start(period, now or datetime.now(timezone.utc), tz_name)
        if start is not None:
            clauses.append("created_at >= ?")
            params.append(start.strftime("%Y-%m-%dT%H:%M:%SZ"))
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        return where, params

    async def list_applications(
        self,
        *,
        status: str | None = None,
        topic: str | None = None,
        period: str | None = None,
        page: int = 0,
        page_size: int = PAGE_SIZE,
        now: datetime | None = None,
        tz_name: str = "Europe/Moscow",
    ) -> Page:
        where, params = self._filter_clause(
            status=status,
            topic=topic,
            period=period,
            now=now,
            tz_name=tz_name,
        )
        count_cursor = await self.conn.execute(
            f"SELECT COUNT(*) AS total FROM applications {where}",
            params,
        )
        total = int((await count_cursor.fetchone())["total"])
        pages = max(1, (total + page_size - 1) // page_size) if total else 1
        safe_page = min(max(page, 0), pages - 1)
        cursor = await self.conn.execute(
            f"""
            SELECT * FROM applications
            {where}
            ORDER BY id DESC
            LIMIT ? OFFSET ?
            """,
            [*params, page_size, safe_page * page_size],
        )
        items = [_application_from_row(row) for row in await cursor.fetchall()]
        return Page(items=items, total=total, page=safe_page, page_size=page_size)

    async def search(self, query: str, *, page: int = 0, page_size: int = PAGE_SIZE) -> Page:
        text = query.strip().lstrip("@")
        like = f"%{text.replace('%', '').replace('_', '')}%"
        clauses = ["tg_username LIKE ? COLLATE NOCASE"]
        params: list = [like]
        if text.isdigit():
            clauses.append("tg_user_id = ?")
            clauses.append("id = ?")
            params.extend([int(text), int(text)])
        where = "WHERE " + " OR ".join(clauses)
        count_cursor = await self.conn.execute(
            f"SELECT COUNT(*) AS total FROM applications {where}",
            params,
        )
        total = int((await count_cursor.fetchone())["total"])
        pages = max(1, (total + page_size - 1) // page_size) if total else 1
        safe_page = min(max(page, 0), pages - 1)
        cursor = await self.conn.execute(
            f"""
            SELECT * FROM applications
            {where}
            ORDER BY id DESC
            LIMIT ? OFFSET ?
            """,
            [*params, page_size, safe_page * page_size],
        )
        items = [_application_from_row(row) for row in await cursor.fetchall()]
        return Page(items=items, total=total, page=safe_page, page_size=page_size)

    async def iter_applications(
        self,
        *,
        status: str | None = None,
        topic: str | None = None,
        period: str | None = None,
        now: datetime | None = None,
        tz_name: str = "Europe/Moscow",
    ) -> list[Application]:
        where, params = self._filter_clause(
            status=status,
            topic=topic,
            period=period,
            now=now,
            tz_name=tz_name,
        )
        cursor = await self.conn.execute(
            f"SELECT * FROM applications {where} ORDER BY id ASC",
            params,
        )
        return [_application_from_row(row) for row in await cursor.fetchall()]

    async def add_supplement(
        self,
        application_id: int,
        text: str,
        message_ids: list[int] | None = None,
    ) -> Supplement:
        cursor = await self.conn.execute(
            """
            INSERT INTO supplements (application_id, text, curator_message_ids, created_at)
            VALUES (?, ?, ?, ?)
            """,
            (
                application_id,
                text,
                json.dumps(message_ids or []),
                utc_now_iso(),
            ),
        )
        await self.conn.commit()
        return Supplement(
            id=int(cursor.lastrowid),
            application_id=application_id,
            text=text,
            curator_message_ids=list(message_ids or []),
            created_at=utc_now_iso(),
        )

    async def list_supplements(self, application_id: int) -> list[Supplement]:
        cursor = await self.conn.execute(
            """
            SELECT * FROM supplements
            WHERE application_id = ?
            ORDER BY id ASC
            """,
            (application_id,),
        )
        items: list[Supplement] = []
        for row in await cursor.fetchall():
            raw_ids = json.loads(row["curator_message_ids"])
            items.append(
                Supplement(
                    id=row["id"],
                    application_id=row["application_id"],
                    text=row["text"],
                    curator_message_ids=[int(item) for item in raw_ids],
                    created_at=row["created_at"],
                )
            )
        return items

    async def curator_message_ids_for_user(self, user_id: int) -> list[int]:
        cursor = await self.conn.execute(
            """
            SELECT curator_message_id FROM applications
            WHERE tg_user_id = ? AND curator_message_id IS NOT NULL
            """,
            (user_id,),
        )
        ids = [int(row["curator_message_id"]) for row in await cursor.fetchall()]
        supplement_cursor = await self.conn.execute(
            """
            SELECT s.curator_message_ids
            FROM supplements s
            JOIN applications a ON a.id = s.application_id
            WHERE a.tg_user_id = ?
            """,
            (user_id,),
        )
        for row in await supplement_cursor.fetchall():
            ids.extend(int(item) for item in json.loads(row["curator_message_ids"]))
        return ids

    async def delete_application(self, application_id: int) -> Application | None:
        existing = await self.get_application(application_id)
        if existing is None:
            return None
        await self.conn.execute(
            "DELETE FROM screen_events WHERE session_id = ?",
            (existing.session_id,),
        )
        await self.conn.execute("DELETE FROM applications WHERE id = ?", (application_id,))
        await self.conn.commit()
        return existing

    async def delete_user(self, user_id: int) -> int:
        cursor = await self.conn.execute(
            "SELECT COUNT(*) AS total FROM applications WHERE tg_user_id = ?",
            (user_id,),
        )
        total = int((await cursor.fetchone())["total"])
        await self.conn.execute("DELETE FROM applications WHERE tg_user_id = ?", (user_id,))
        await self.conn.execute("DELETE FROM screen_events WHERE tg_user_id = ?", (user_id,))
        await self.conn.commit()
        return total

    async def audit(
        self,
        *,
        actor: str,
        action: str,
        target: str | None = None,
        details: dict | None = None,
    ) -> None:
        await self.conn.execute(
            """
            INSERT INTO audit_log (actor, action, target, details, created_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                actor,
                action,
                target,
                json.dumps(details or {}, ensure_ascii=False),
                utc_now_iso(),
            ),
        )
        await self.conn.commit()

    async def list_audit(self, limit: int = 20) -> list[dict]:
        cursor = await self.conn.execute(
            "SELECT * FROM audit_log ORDER BY id DESC LIMIT ?",
            (limit,),
        )
        return [dict(row) for row in await cursor.fetchall()]

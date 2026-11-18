from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


def _ids(raw: str | None, *, title: str) -> frozenset[int]:
    values: set[int] = set()
    for part in (raw or "").replace(";", ",").split(","):
        part = part.strip()
        if not part:
            continue
        try:
            values.add(int(part))
        except ValueError as exc:
            raise SystemExit(f"Некорректный Telegram ID в {title}: {part}") from exc
    return frozenset(values)


def _optional_int(raw: str | None, *, title: str) -> int | None:
    text = (raw or "").strip()
    if not text:
        return None
    try:
        return int(text)
    except ValueError as exc:
        raise SystemExit(f"Некорректный Telegram ID в {title}: {text}") from exc


@dataclass(frozen=True)
class Config:
    bot_token: str
    redis_url: str
    database_path: str
    applications_chat_id: int | None
    admin_ids: frozenset[int]
    operator_ids: frozenset[int]
    operator_name: str
    operator_inn: str
    operator_email: str
    response_time: str
    privacy_url: str
    curator_tz: str
    state_ttl_seconds: int = 24 * 60 * 60

    def role_of(self, user_id: int) -> str | None:
        if user_id in self.admin_ids:
            return "admin"
        if user_id in self.operator_ids:
            return "operator"
        return None


def load_config() -> Config:
    token = os.environ.get("BOT_TOKEN", "").strip()
    if not token:
        raise SystemExit("Задайте BOT_TOKEN в окружении.")

    database_path = os.environ.get("DATABASE_PATH", "data/praktika.sqlite").strip()
    Path(database_path).parent.mkdir(parents=True, exist_ok=True)

    return Config(
        bot_token=token,
        redis_url=os.environ.get("REDIS_URL", "redis://localhost:6379/0").strip(),
        database_path=database_path,
        applications_chat_id=_optional_int(
            os.environ.get("APPLICATIONS_CHAT_ID"),
            title="APPLICATIONS_CHAT_ID",
        ),
        admin_ids=_ids(os.environ.get("ADMIN_IDS"), title="ADMIN_IDS"),
        operator_ids=_ids(os.environ.get("OPERATOR_IDS"), title="OPERATOR_IDS"),
        operator_name=os.environ.get("OPERATOR_NAME", "").strip(),
        operator_inn=os.environ.get("OPERATOR_INN", "").strip(),
        operator_email=os.environ.get("OPERATOR_EMAIL", "").strip(),
        response_time=os.environ.get("RESPONSE_TIME", "рабочего дня").strip() or "рабочего дня",
        privacy_url=os.environ.get("PRIVACY_URL", "").strip(),
        curator_tz=os.environ.get("CURATOR_TZ", "Europe/Moscow").strip() or "Europe/Moscow",
    )

from datetime import datetime, timezone

import pytest

from bot.db import Database
from bot.domain import SKIPPED, Answers, funnel_stats


def sample(problem: str = "c_anx") -> Answers:
    return Answers(
        problem=problem,
        free_text="комментарий",
        therapy_exp="e_ok",
        format="f_offline",
        tz=SKIPPED,
        budget="p1",
        slots=["t_morn"],
    )


@pytest.fixture
async def db(tmp_path):
    database = Database(str(tmp_path / "test.sqlite"))
    await database.connect()
    yield database
    await database.close()


async def test_applications_filters_search_and_deletion(db: Database):
    now = datetime(2026, 11, 18, 12, 0, tzinfo=timezone.utc)
    first = await db.create_application(
        session_id="s1",
        user_id=100,
        username="Anna",
        full_name="Анна",
        consent_at="2026-11-17T20:00:00Z",
        answers=sample("c_rel"),
        reply_in_chat=False,
        created_at="2026-11-17T20:00:00Z",
    )
    second = await db.create_application(
        session_id="s2",
        user_id=200,
        username="",
        full_name="Борис",
        consent_at="2026-11-18T10:00:00Z",
        answers=sample("c_anx"),
        reply_in_chat=True,
        created_at="2026-11-18T10:00:00Z",
    )
    again = await db.create_application(
        session_id="s2",
        user_id=200,
        username="other",
        full_name="Борис",
        consent_at="2026-11-18T10:00:00Z",
        answers=sample("c_anx"),
        reply_in_chat=True,
        created_at="2026-11-18T10:00:00Z",
    )
    assert again.id == second.id
    assert second.tg_username == "нет никнейма"

    today = await db.list_applications(period="today", now=now, tz_name="Europe/Moscow")
    assert [item.id for item in today.items] == [second.id]
    by_topic = await db.list_applications(topic="c_rel")
    assert [item.id for item in by_topic.items] == [first.id]
    found = await db.search("anna")
    assert [item.id for item in found.items] == [first.id]
    found_by_id = await db.search(str(second.tg_user_id))
    assert second.id in [item.id for item in found_by_id.items]

    await db.add_supplement(second.id, "уточнение", [55])
    await db.set_status(second.id, "in_progress")
    updated = await db.get_application(second.id)
    assert updated is not None and updated.status == "in_progress"
    assert len(await db.list_supplements(second.id)) == 1

    await db.track_screen("s2", 200, "S0")
    await db.track_screen("s2", 200, "S0")
    await db.track_screen("s2", 200, "S8")
    stats = {row.screen: row.opened for row in funnel_stats(await db.funnel_sessions())}
    assert stats["S0"] == 1
    assert stats["S8"] == 1

    removed = await db.delete_application(first.id)
    assert removed is not None
    assert await db.get_application(first.id) is None
    wiped = await db.delete_user(200)
    assert wiped == 1
    assert await db.latest_for_user(200) is None
    assert await db.list_supplements(second.id) == []
    assert await db.funnel_sessions() == []


async def test_status_is_validated(db: Database):
    created = await db.create_application(
        session_id="s",
        user_id=1,
        username="nick",
        full_name="Имя",
        consent_at="2026-11-18T10:00:00Z",
        answers=sample(),
        reply_in_chat=False,
    )
    with pytest.raises(ValueError):
        await db.set_status(created.id, "approved")

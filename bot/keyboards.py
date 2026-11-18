from __future__ import annotations

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from bot.domain import Answers, keyboard_rows


def survey_keyboard(screen: str, answers: Answers) -> InlineKeyboardMarkup:
    rows = keyboard_rows(screen, answers)
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=text, callback_data=data) for text, data in row]
            for row in rows
        ]
    )

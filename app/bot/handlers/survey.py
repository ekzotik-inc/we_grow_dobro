"""Опрос про награды — отдельная ветка, не связанная с главным меню.

Участник получает сообщение с вопросом, отвечает кнопкой, сообщение превращается
во второй вопрос, потом — в «спасибо». Ни один экран опроса не тянет за собой меню
и не зависит от того, что происходит в марафоне.
"""
from __future__ import annotations

import logging

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery

from ... import keyboards as kb
from ... import services, texts
from ..common import answer_cq, edit, load_user, session

log = logging.getLogger(__name__)
router = Router(name="survey")


async def _screen(s, user):
    """Что показать человеку: следующий вопрос или благодарность."""
    answers = await services.survey_answers_of(s, user.id)
    code = services.survey_next_question(answers)
    if code is None:
        return texts.survey_done(answers), kb.survey_done_kb()
    index = services.SURVEY_ORDER.index(code) + 1
    return (texts.survey_question(code, index, len(services.SURVEY_ORDER)),
            kb.survey_kb(code, answers.get(code)))


@router.callback_query(F.data.regexp(r"^sv:(\w+):(\w+)$"))
async def cb_answer(cq: CallbackQuery, state: FSMContext) -> None:
    _, code, value = cq.data.split(":")
    async with session() as s:
        user = await load_user(s, cq.from_user)
        try:
            await services.save_survey_answer(s, user, code, value)
            await s.commit()
        except services.ServiceError as ex:
            await answer_cq(cq, str(ex), alert=True)
            return
        text, markup = await _screen(s, user)
    await edit(cq, text, markup)
    await answer_cq(cq, "Ответ записан")


@router.callback_query(F.data == "sv:restart")
async def cb_restart(cq: CallbackQuery, state: FSMContext) -> None:
    """Передумал — начинаем с первого вопроса, прошлые ответы перезапишутся."""
    async with session() as s:
        user = await load_user(s, cq.from_user)
        answers = await services.survey_answers_of(s, user.id)
    code = services.SURVEY_ORDER[0]
    await edit(cq, texts.survey_question(code, 1, len(services.SURVEY_ORDER)),
               kb.survey_kb(code, answers.get(code)))
    await answer_cq(cq)

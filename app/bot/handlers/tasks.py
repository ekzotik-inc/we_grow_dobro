"""Tasks of the week, task card, submission flow (photos + note), my results."""
from __future__ import annotations

import logging

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from ... import keyboards as kb
from ... import services, texts
from ...config import settings
from ...models import SubmissionStatus, UserStatus
from .. import channels
from ..channels import send_files
from ..common import ANCHOR_KEY, answer_cq, delete_quietly, edit, edit_anchor, load_user, session
from ..states import SubmissionFlow

log = logging.getLogger(__name__)
router = Router(name="tasks")


async def _subs_map(s, user_id: int) -> dict:
    return {x.task_id: x for x in await services.user_submissions(s, user_id)}


async def render_week(s, user, week: int):
    tasks = await services.list_tasks(s, week)
    subs = await _subs_map(s, user.id)
    return texts.tasks_list(week, tasks, subs, no_team=not user.team_id), kb.week_tabs_kb(week, tasks, subs), f"week{week}.png"


@router.callback_query(F.data == "tasks")
async def cb_tasks(cq: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    cw = settings.current_week()
    visible = services.visible_weeks()
    week = cw.number if cw else (visible[-1] if visible else 1)
    async with session() as s:
        user = await load_user(s, cq.from_user)
        if user.status == UserStatus.new:
            await answer_cq(cq, "Сначала зарегистрируйся", alert=True)
            return
        if user.status in (UserStatus.pending, UserStatus.rejected):
            await answer_cq(cq, "Задания откроются после подтверждения заявки сотрудником P&C.", alert=True)
            return
        text, markup, image = await render_week(s, user, week)
    await edit(cq, text, markup, image)
    await answer_cq(cq)


@router.callback_query(F.data.regexp(r"^tasks:w:(\d)$"))
async def cb_tasks_week(cq: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    week = int(cq.data.split(":")[2])
    if not services.week_is_visible(week):
        await answer_cq(cq, "Эта неделя ещё не началась — задания откроются в свой срок.", alert=True)
        return
    async with session() as s:
        user = await load_user(s, cq.from_user)
        text, markup, image = await render_week(s, user, week)
    await edit(cq, text, markup, image)
    await answer_cq(cq)


@router.callback_query(F.data.regexp(r"^task:(\d+)$"))
async def cb_task(cq: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    task_id = int(cq.data.split(":")[1])
    async with session() as s:
        user = await load_user(s, cq.from_user)
        task = await services.get_task(s, task_id)
        if task is None:
            await answer_cq(cq, "Задание не найдено", alert=True)
            return
        if not services.week_is_visible(task.week):
            await answer_cq(cq, "Эта неделя ещё не началась.", alert=True)
            return
        sub = await services.user_submission_for_task(s, user.id, task.id)
    await edit(cq, texts.task_card(task, sub, await services.task_number(s, task), no_team=not user.team_id), kb.task_card_kb(task, sub, services.task_is_open(task), user), task.image)
    await answer_cq(cq)


# ---------- submission flow ----------

async def _open_editor(cq_or_msg, state: FSMContext, sub) -> None:
    await state.set_state(SubmissionFlow.collecting)
    await state.update_data(sub_id=sub.id)
    if isinstance(cq_or_msg, CallbackQuery):
        m = await edit(cq_or_msg, texts.submission_editor(sub), kb.submission_editor_kb(sub))
        if m:
            await state.update_data({ANCHOR_KEY: m.message_id})
    else:
        await edit_anchor(cq_or_msg.bot, cq_or_msg.chat.id, state, texts.submission_editor(sub), kb.submission_editor_kb(sub))


@router.callback_query(F.data.regexp(r"^sub:start:(\d+):(\d+)$"))
async def cb_sub_start(cq: CallbackQuery, state: FSMContext) -> None:
    _, _, task_id, option_id = cq.data.split(":")
    async with session() as s:
        user = await load_user(s, cq.from_user)
        task = await services.get_task(s, int(task_id))
        if task is None:
            await answer_cq(cq, "Задание не найдено", alert=True)
            return
        try:
            sub = await services.start_submission(s, user, task, int(option_id) or None)
            await s.commit()
        except services.ServiceError as ex:
            await answer_cq(cq, str(ex), alert=True)
            return
    await _open_editor(cq, state, sub)
    await answer_cq(cq)


@router.callback_query(F.data.regexp(r"^sub:open:(\d+)$"))
async def cb_sub_open(cq: CallbackQuery, state: FSMContext) -> None:
    sub_id = int(cq.data.split(":")[2])
    async with session() as s:
        user = await load_user(s, cq.from_user)
        sub = await services.get_submission(s, sub_id)
    if sub is None or sub.user_id != user.id or sub.status != SubmissionStatus.draft:
        await answer_cq(cq, "Черновик не найден", alert=True)
        return
    await _open_editor(cq, state, sub)
    await answer_cq(cq)


def _file_from_message(m: Message) -> dict | None:
    if m.photo:
        return {"type": "photo", "file_id": m.photo[-1].file_id, "name": None}
    if m.document:
        return {"type": "document", "file_id": m.document.file_id, "name": m.document.file_name}
    if m.video:
        return {"type": "video", "file_id": m.video.file_id, "name": None}
    return None


@router.message(SubmissionFlow.collecting, F.photo | F.document | F.video)
async def sub_file(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    sub_id = data.get("sub_id")
    file = _file_from_message(message)
    async with session() as s:
        user = await load_user(s, message.from_user)
        sub = await services.get_submission(s, sub_id) if sub_id else None
        if sub is None or sub.user_id != user.id or sub.status != SubmissionStatus.draft:
            await state.clear()
            return
        try:
            await services.add_file(s, sub, file)
            if message.caption and not sub.note:
                await services.set_note(s, sub, message.caption)
            await s.commit()
        except services.ServiceError as ex:
            await message.reply(str(ex))
            return
        sub = await services.get_submission(s, sub.id)
    # Keep the chat clean: the user's upload is removed, the editor message shows the new count.
    await delete_quietly(message)
    await edit_anchor(message.bot, message.chat.id, state, texts.submission_editor(sub), kb.submission_editor_kb(sub))


@router.message(SubmissionFlow.collecting, F.text)
async def sub_note(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    sub_id = data.get("sub_id")
    async with session() as s:
        user = await load_user(s, message.from_user)
        sub = await services.get_submission(s, sub_id) if sub_id else None
        if sub is None or sub.user_id != user.id or sub.status != SubmissionStatus.draft:
            await state.clear()
            return
        await services.set_note(s, sub, message.text)
        await s.commit()
        sub = await services.get_submission(s, sub.id)
    await delete_quietly(message)
    await edit_anchor(message.bot, message.chat.id, state, texts.submission_editor(sub), kb.submission_editor_kb(sub))


@router.callback_query(F.data.regexp(r"^sub:pop:(\d+)$"))
async def cb_sub_pop(cq: CallbackQuery, state: FSMContext) -> None:
    sub_id = int(cq.data.split(":")[2])
    async with session() as s:
        user = await load_user(s, cq.from_user)
        sub = await services.get_submission(s, sub_id)
        if sub is None or sub.user_id != user.id or sub.status != SubmissionStatus.draft:
            await answer_cq(cq, "Черновик не найден", alert=True)
            return
        files = list(sub.files or [])
        if files:
            files.pop()
        sub.files = files
        await s.commit()
        sub = await services.get_submission(s, sub.id)
    await _open_editor(cq, state, sub)
    await answer_cq(cq, "Файл удалён")


@router.callback_query(F.data.regexp(r"^sub:preview:(\d+)$"))
async def cb_sub_preview(cq: CallbackQuery, state: FSMContext) -> None:
    sub_id = int(cq.data.split(":")[2])
    async with session() as s:
        user = await load_user(s, cq.from_user)
        sub = await services.get_submission(s, sub_id)
    if sub is None or sub.user_id != user.id:
        await answer_cq(cq, "Не найдено", alert=True)
        return
    if not sub.files:
        await answer_cq(cq, "Файлов пока нет")
        return
    await answer_cq(cq)
    await send_files(cq.bot, cq.message.chat.id, sub.files)
    # Re-send the editor below the preview so the buttons stay reachable, and drop the old one.
    await delete_quietly(cq.message)
    m = await cq.message.answer(texts.submission_editor(sub), reply_markup=kb.submission_editor_kb(sub))
    await state.update_data({ANCHOR_KEY: m.message_id})


@router.callback_query(F.data.regexp(r"^sub:send:(\d+)$"))
async def cb_sub_send(cq: CallbackQuery, state: FSMContext) -> None:
    sub_id = int(cq.data.split(":")[2])
    async with session() as s:
        user = await load_user(s, cq.from_user)
        sub = await services.get_submission(s, sub_id)
        if sub is None or sub.user_id != user.id:
            await answer_cq(cq, "Не найдено", alert=True)
            return
        try:
            await services.send_for_review(s, sub)
            await s.commit()
        except services.ServiceError as ex:
            await answer_cq(cq, str(ex), alert=True)
            return
        sub = await services.get_submission(s, sub.id)
        task = sub.task
        # The report goes to the results channel where P&C decides; points are awarded only on «Зачесть».
        await channels.post_submission(cq.bot, s, sub)
        await s.commit()
    await state.clear()
    await edit(
        cq,
        texts.submission_sent(task),
        kb.task_card_kb(task, sub, services.task_is_open(task), user),
        task.image,
    )
    await answer_cq(cq, "Отправлено!")


@router.callback_query(F.data.regexp(r"^sub:cancel:(\d+)$"))
async def cb_sub_cancel(cq: CallbackQuery) -> None:
    sub_id = int(cq.data.split(":")[2])
    async with session() as s:
        user = await load_user(s, cq.from_user)
        sub = await services.get_submission(s, sub_id)
    if sub is None or sub.user_id != user.id:
        await answer_cq(cq, "Не найдено", alert=True)
        return
    await edit(cq, f"Отменить отчёт по заданию №{sub.task.code}? Файлы и заметка будут удалены.", kb.sub_cancel_confirm_kb(sub))
    await answer_cq(cq)


@router.callback_query(F.data.regexp(r"^sub:cancel_ok:(\d+)$"))
async def cb_sub_cancel_ok(cq: CallbackQuery, state: FSMContext) -> None:
    sub_id = int(cq.data.split(":")[2])
    async with session() as s:
        user = await load_user(s, cq.from_user)
        sub = await services.get_submission(s, sub_id)
        if sub is None or sub.user_id != user.id:
            await answer_cq(cq, "Не найдено", alert=True)
            return
        try:
            await services.cancel_submission(s, sub)
            await s.commit()
        except services.ServiceError as ex:
            await answer_cq(cq, str(ex), alert=True)
            return
        task = await services.get_task(s, sub.task_id)
        sub = await services.get_submission(s, sub.id)
    await state.clear()
    await edit(cq, texts.task_card(task, sub, await services.task_number(s, task), no_team=not user.team_id), kb.task_card_kb(task, sub, services.task_is_open(task), user), task.image)
    await answer_cq(cq, "Отчёт отменён")


@router.callback_query(F.data == "me")
async def cb_me(cq: CallbackQuery) -> None:
    async with session() as s:
        user = await load_user(s, cq.from_user)
        subs = await services.user_submissions(s, user.id)
        total = await services.user_points(s, user.id)
    await edit(cq, texts.my_results(user, subs, total), kb.back_kb())
    await answer_cq(cq)

"""Tasks of the week, task card, submission flow (photos + note), my results."""
from __future__ import annotations

import contextlib
import logging

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from ... import keyboards as kb
from ... import services, texts
from ...models import SubmissionStatus, UserStatus
from .. import channels
from ..channels import send_files
from ..common import ANCHOR_KEY, answer_cq, delete_quietly, edit, load_user, session
from ..states import SubmissionFlow

log = logging.getLogger(__name__)
router = Router(name="tasks")


async def _subs_map(s, user_id: int) -> dict:
    return {x.task_id: x for x in await services.user_submissions(s, user_id)}


async def render_week(s, user, week: int):
    tasks = await services.list_tasks(s, week)
    subs = await _subs_map(s, user.id)
    return texts.tasks_list(week, tasks, subs), kb.week_tabs_kb(week, tasks, subs), f"week{week}.png"


@router.callback_query(F.data == "tasks")
async def cb_tasks(cq: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    weeks = services.open_weeks()
    async with session() as s:
        user = await load_user(s, cq.from_user)
        if user.status == UserStatus.new:
            await answer_cq(cq, "Сначала зарегистрируйся", alert=True)
            return
        if user.status in (UserStatus.pending, UserStatus.rejected):
            await answer_cq(cq, "Задания откроются после подтверждения заявки.", alert=True)
            return
        if not weeks:
            # Ни одна неделя не включена в панели — показываем ожидание, а не пустой список.
            await edit(cq, texts.tasks_soon(), kb.back_kb())
            await answer_cq(cq)
            return
        text, markup, image = await render_week(s, user, weeks[-1])
    await edit(cq, text, markup, image)
    await answer_cq(cq)


@router.callback_query(F.data.regexp(r"^tasks:w:(\d)$"))
async def cb_tasks_week(cq: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    week = int(cq.data.split(":")[2])
    if not services.week_is_open(week):
        await answer_cq(cq, "Эта неделя пока закрыта — задания появятся, когда её откроют.", alert=True)
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
        if not services.week_is_open(task.week):
            await answer_cq(cq, "Эта неделя ещё не началась.", alert=True)
            return
        sub = await services.user_submission_for_task(s, user.id, task.id)
    await edit(cq, texts.task_card(task, sub, await services.task_number(s, task)), kb.task_card_kb(task, sub, services.task_is_open(task), user), task.image)
    await answer_cq(cq)


# ---------- submission flow ----------

def _screen(sub, index: int | None = None, warning: str = ""):
    """Экран мастера: конкретный шаг или итоговая проверка, когда шаги пройдены."""
    steps = services.submission_steps(sub)
    if not steps:  # задание без пошаговой инструкции — старый экран
        return texts.submission_editor(sub), kb.submission_editor_kb(sub)
    if index is None:
        index = services.first_unfinished_step(sub)
    if index >= len(steps):
        done = [services.step_done(sub, i) for i in range(len(steps))]
        return texts.submission_review(sub), kb.submission_review_kb(sub, steps, done, not services.steps_left(sub))
    index = max(0, min(index, len(steps) - 1))
    return (
        texts.submission_step(sub, index, warning),
        kb.submission_step_kb(sub, index, len(steps), services.step_done(sub, index)),
    )


async def _close_step_message(bot, chat_id: int, state: FSMContext, sub, index: int) -> None:
    """Свернуть сообщение пройденного шага в короткую отметку — оно остаётся в переписке."""
    data = await state.get_data()
    mid = (data.get("step_msgs") or {}).get(str(index))
    if not mid:
        return
    with contextlib.suppress(Exception):
        await bot.edit_message_text(texts.step_accepted(sub, index), chat_id=chat_id, message_id=mid)


async def _open_editor(cq_or_msg, state: FSMContext, sub, index: int | None = None, warning: str = "") -> None:
    """Показать нужный экран мастера.

    Каждый шаг — отдельное сообщение: так участник видит всю инструкцию по порядку и может
    вернуться к любому шагу в переписке. Повторный показ того же шага сообщение не плодит —
    оно редактируется на месте.
    """
    steps = services.submission_steps(sub)
    if index is None:
        # Определяем шаг сразу: иначе сообщение запишется не под своим ключом и не свернётся.
        index = services.first_unfinished_step(sub) if steps else None
    await state.set_state(SubmissionFlow.collecting)
    await state.update_data(sub_id=sub.id, step=index)
    text, markup = _screen(sub, index, warning)
    msg = cq_or_msg.message if isinstance(cq_or_msg, CallbackQuery) else cq_or_msg
    bot, chat_id = msg.bot, msg.chat.id

    data = await state.get_data()
    step_msgs = dict(data.get("step_msgs") or {})
    key = str(index) if steps and index is not None and index < len(steps) else "review"

    known = step_msgs.get(key)
    if known:
        with contextlib.suppress(Exception):
            await bot.edit_message_text(text, chat_id=chat_id, message_id=known, reply_markup=markup)
            await state.update_data({ANCHOR_KEY: known})
            return
    sent = await bot.send_message(chat_id, text, reply_markup=markup)
    step_msgs[key] = sent.message_id
    await state.update_data(step_msgs=step_msgs)
    await state.update_data({ANCHOR_KEY: sent.message_id})


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
    await state.update_data(step_msgs={})
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


async def resume_draft(message: Message, state: FSMContext) -> bool:
    """Вернуть участника в его незаконченный отчёт после перезапуска бота.

    Состояние диалога живёт в памяти процесса, а черновик — в базе. Без этого присланное
    фото просто исчезало: обработчик шага не срабатывал, и участник видел тишину.
    """
    async with session() as s:
        user = await load_user(s, message.from_user)
        sub = await services.draft_submission(s, user.id)
        if sub is None:
            return False
        sub_id = sub.id
    await state.set_state(SubmissionFlow.collecting)
    await state.update_data(sub_id=sub_id, step=None, step_msgs={})
    return True


@router.message(SubmissionFlow.collecting, F.photo | F.document | F.video)
async def sub_file(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    sub_id = data.get("sub_id")

    # Альбом: телефон отправляет выбранные скопом фото отдельными сообщениями с общим
    # media_group_id. Без этой проверки второе фото уходило бы в следующий шаг, третье —
    # в третий, и «фото до» оказывалось бы «фото после».
    group = message.media_group_id
    if group and data.get("album") == group:
        await delete_quietly(message)
        sub = None
        async with session() as s:
            user = await load_user(s, message.from_user)
            sub = await services.get_submission(s, sub_id) if sub_id else None
            if sub is None or sub.user_id != user.id:
                return
        await _open_editor(message, state, sub, data.get("step"),
                           "Из альбома взял только первое фото. Остальные пришли по одному — "
                           "каждое на своём шаге.")
        return
    if group:
        await state.update_data(album=group)
    file = _file_from_message(message)
    async with session() as s:
        user = await load_user(s, message.from_user)
        sub = await services.get_submission(s, sub_id) if sub_id else None
        if sub is None or sub.user_id != user.id or sub.status != SubmissionStatus.draft:
            await state.clear()
            return
        steps = services.submission_steps(sub)
        index = data.get("step")
        if index is None:
            index = services.first_unfinished_step(sub)
        warning = ""
        try:
            if steps:
                index = max(0, min(index, len(steps) - 1))
                # Проверку типа делает сервис: одно правило и для бота, и для любого
                # другого пути — пропустить шаг «не тем» файлом невозможно.
                await services.add_file(s, sub, file, step=index)
            else:
                await services.add_file(s, sub, file)
                if message.caption and not sub.note:
                    await services.set_note(s, sub, message.caption)
            await s.commit()
        except services.ServiceError as ex:
            # Остаёмся на том же шаге и объясняем, что именно здесь нужно.
            await delete_quietly(message)
            await _open_editor(message, state, sub, index, str(ex))
            return
        sub = await services.get_submission(s, sub.id)
        next_index = services.first_unfinished_step(sub) if steps else None
    # Присланное убираем, пройденный шаг сворачиваем в отметку, следующий приходит новым сообщением.
    await delete_quietly(message)
    if steps and next_index != index:
        await _close_step_message(message.bot, message.chat.id, state, sub, index)
    await _open_editor(message, state, sub, next_index, warning)


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
        steps = services.submission_steps(sub)
        index = data.get("step")
        if index is None:
            index = services.first_unfinished_step(sub)
        try:
            if steps:
                index = max(0, min(index, len(steps) - 1))
                await services.save_step_answer(s, sub, index, message.text)
            else:
                if not services.answer_is_valid(message.text):
                    raise services.ServiceError("Слишком коротко. Опишите словами, что сделали.")
                await services.set_note(s, sub, message.text)
            await s.commit()
        except services.ServiceError as ex:
            await delete_quietly(message)
            await _open_editor(message, state, sub, index, str(ex))
            return
        sub = await services.get_submission(s, sub.id)
        next_index = services.first_unfinished_step(sub) if steps else None
    await delete_quietly(message)
    if steps and next_index != index:
        await _close_step_message(message.bot, message.chat.id, state, sub, index)
    await _open_editor(message, state, sub, next_index)


@router.callback_query(F.data.regexp(r"^sub:step:(\d+):(\d+)$"))
async def cb_sub_step(cq: CallbackQuery, state: FSMContext) -> None:
    _, _, sub_id, index = cq.data.split(":")
    async with session() as s:
        user = await load_user(s, cq.from_user)
        sub = await services.get_submission(s, int(sub_id))
    if sub is None or sub.user_id != user.id or sub.status != SubmissionStatus.draft:
        await answer_cq(cq, "Черновик не найден", alert=True)
        return
    await _open_editor(cq, state, sub, int(index))
    await answer_cq(cq)


@router.callback_query(F.data.regexp(r"^sub:review:(\d+)$"))
async def cb_sub_review(cq: CallbackQuery, state: FSMContext) -> None:
    sub_id = int(cq.data.split(":")[2])
    async with session() as s:
        user = await load_user(s, cq.from_user)
        sub = await services.get_submission(s, sub_id)
    if sub is None or sub.user_id != user.id or sub.status != SubmissionStatus.draft:
        await answer_cq(cq, "Черновик не найден", alert=True)
        return
    await _open_editor(cq, state, sub, len(services.submission_steps(sub)))
    await answer_cq(cq)


@router.callback_query(F.data.regexp(r"^sub:redo:(\d+):(\d+)$"))
async def cb_sub_redo(cq: CallbackQuery, state: FSMContext) -> None:
    _, _, sub_id, index = cq.data.split(":")
    async with session() as s:
        user = await load_user(s, cq.from_user)
        sub = await services.get_submission(s, int(sub_id))
        if sub is None or sub.user_id != user.id or sub.status != SubmissionStatus.draft:
            await answer_cq(cq, "Черновик не найден", alert=True)
            return
        await services.clear_step(s, sub, int(index))
        await s.commit()
        sub = await services.get_submission(s, sub.id)
    await _open_editor(cq, state, sub, int(index))
    await answer_cq(cq, "Шаг очищен — пришлите заново")


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
    text, markup = _screen(sub, len(services.submission_steps(sub)) or None)
    m = await cq.message.answer(text, reply_markup=markup)
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
    await edit(cq, texts.task_card(task, sub, await services.task_number(s, task)), kb.task_card_kb(task, sub, services.task_is_open(task), user), task.image)
    await answer_cq(cq, "Отчёт отменён")


@router.callback_query(F.data == "me")
async def cb_me(cq: CallbackQuery) -> None:
    async with session() as s:
        user = await load_user(s, cq.from_user)
        subs = await services.user_submissions(s, user.id)
        total = await services.user_points(s, user.id)
    await edit(cq, texts.my_results(user, subs, total), kb.back_kb())
    await answer_cq(cq)

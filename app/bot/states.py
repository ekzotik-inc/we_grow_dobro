from aiogram.fsm.state import State, StatesGroup


class Registration(StatesGroup):
    full_name = State()
    department = State()
    city = State()
    confirm = State()


class TeamCreate(StatesGroup):
    name = State()
    emoji = State()


class SubmissionFlow(StatesGroup):
    collecting = State()  # waiting for photos / files / note text


class HelpFlow(StatesGroup):
    question = State()


class AdminFlow(StatesGroup):
    reject_reason = State()
    mod_reject_reason = State()
    dq_reason = State()
    broadcast = State()
    bind_channel = State()
    user_search = State()
    team_rename = State()

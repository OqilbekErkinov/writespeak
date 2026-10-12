from aiogram.fsm.state import State, StatesGroup


class GroupStates(StatesGroup):
    awaiting_name = State()
    awaiting_announcement = State()  # data: group_id
    awaiting_comment = State()  # data: group_id, student_id, kind, submission_id


class AssignmentStates(StatesGroup):
    awaiting_prompt = State()  # data: group_id, module - the teacher's own question

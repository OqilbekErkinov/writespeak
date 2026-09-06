from aiogram.fsm.state import State, StatesGroup


class GroupStates(StatesGroup):
    awaiting_name = State()

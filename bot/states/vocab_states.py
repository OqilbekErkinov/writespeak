from aiogram.fsm.state import State, StatesGroup


class VocabStates(StatesGroup):
    reviewing = State()

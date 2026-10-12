from aiogram.fsm.state import State, StatesGroup


class VocabStates(StatesGroup):
    reviewing = State()
    testing = State()
    adding = State()

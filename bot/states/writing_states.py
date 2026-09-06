from aiogram.fsm.state import State, StatesGroup


class WritingStates(StatesGroup):
    awaiting_prompt = State()
    confirming_prompt = State()
    awaiting_answer = State()
    confirming_answer = State()
    processing = State()

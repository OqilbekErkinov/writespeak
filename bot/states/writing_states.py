from aiogram.fsm.state import State, StatesGroup


class WritingStates(StatesGroup):
    awaiting_prompt = State()
    confirming_prompt = State()
    confirming_section = State()  # the question belongs to the other Task: move there?
    awaiting_chart = State()  # Task 1 typed as text: its chart/diagram picture
    awaiting_answer = State()
    confirming_answer = State()
    processing = State()

from aiogram.fsm.state import State, StatesGroup


class SpeakingStates(StatesGroup):
    awaiting_question = State()   # only used when NOT coming from Practice (question preloaded there)
    confirming_question = State()
    awaiting_answer = State()
    processing = State()

from aiogram.fsm.state import State, StatesGroup


class SpeakingStates(StatesGroup):
    awaiting_question = State()   # only used when NOT coming from Practice (question preloaded there)
    confirming_question = State()
    choosing_mode = State()       # question by question, or one recording with every answer
    awaiting_answer = State()     # one question of the set at a time (questions/q_index/answers in data)
    awaiting_recording = State()  # one recording, in one or more pieces (pieces in data)
    confirming_split = State()    # the recording's split into answers, shown when it is uncertain
    awaiting_split_fix = State()  # the student's own times for the split
    processing = State()

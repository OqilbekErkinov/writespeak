from aiogram.fsm.state import State, StatesGroup


class WritingStates(StatesGroup):
    awaiting_prompt = State()
    confirming_prompt = State()
    confirming_section = State()  # the question belongs to the other Task: move there?
    awaiting_chart = State()  # Task 1 typed as text: its chart/diagram picture
    awaiting_answer = State()
    confirming_answer = State()
    confirming_recheck = State()  # an essay sent before (admins, testers): mark it again or send its report?
    confirming_length = State()  # a typed answer far too short (or cut by Telegram): the rest may follow
    processing = State()
    failed = State()  # the check stopped (an error, a restart): its input kept for "🔁 Try again"

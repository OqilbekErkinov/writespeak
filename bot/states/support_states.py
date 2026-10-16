from aiogram.fsm.state import State, StatesGroup


class SupportStates(StatesGroup):
    writing = State()  # every message goes to the admins (bot/handlers/support.py)

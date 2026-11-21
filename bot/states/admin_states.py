from aiogram.fsm.state import State, StatesGroup


class AdminStates(StatesGroup):
    emoji = State()  # the next message's premium emoji are saved (bot/handlers/admin_tools.py)
    post = State()  # the next message is kept as the panel's broadcast post

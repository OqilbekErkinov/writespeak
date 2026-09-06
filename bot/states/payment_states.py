from aiogram.fsm.state import State, StatesGroup


class PaymentStates(StatesGroup):
    awaiting_receipt = State()

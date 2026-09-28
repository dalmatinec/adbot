from aiogram.fsm.state import State, StatesGroup


class BroadcasterStates(StatesGroup):
    entering_title = State()       # ввод названия новой рассылки
    waiting_post = State()         # ожидание пересланного поста
    editing_title = State()        # переименование существующей рассылки


class AdminStates(StatesGroup):
    entering_target_chat = State()      # ввод ID нового целевого чата
    entering_superadmin_id = State()    # ввод ID нового супер-админа
    entering_user_search_id = State()   # ввод ID пользователя для поиска в разделе "Пользователи"


class Admin1States(StatesGroup):
    editing_label = State()  # ввод нового текста кнопки
    editing_emoji = State()  # ожидание кастомного эмодзи
    editing_text = State()   # ввод нового текста сообщения (раздел "Тексты")


class OwnerBroadcastStates(StatesGroup):
    waiting_post = State()  # отдельное состояние от BroadcasterStates.waiting_post,
    # чтобы разовая рассылка владельца не конфликтовала с загрузкой поста рекламодателя


class NewBroadcastStates(StatesGroup):
    """Пошаговый мастер создания рассылки: название -> пост -> интервал -> подтверждение.
    Данные копятся в FSM-данных и пишутся в БД одним разом на шаге интервала -
    сама рассылка появляется в базе только когда мастер реально пройден до конца."""
    title = State()
    post = State()
    interval = State()  # состояние-заглушка на время показа inline-клавиатуры интервала


class KeyAdminStates(StatesGroup):
    entering_admin_id = State()  # владелец ключа вводит ID нового админа своего ключа

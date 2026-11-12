class InlineKeyboardButton:
    def __init__(self, text, url=None, callback_data=None, style=None, icon_custom_emoji_id=None):
        self.text, self.url, self.callback_data, self.style, self.icon_custom_emoji_id = text, url, callback_data, style, icon_custom_emoji_id
class InlineKeyboardMarkup:
    def __init__(self, inline_keyboard): self.inline_keyboard = inline_keyboard
class FSInputFile:
    def __init__(self, path, *a, **k): self.path = path
class Message: ...
class CallbackQuery: ...
class ChatMemberUpdated: ...

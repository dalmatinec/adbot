"""Заглушка aiogram для тестов без установки библиотеки: декораторы
регистрации ничего не делают, обработчики вызываются из тестов напрямую.
В бою используется настоящий aiogram."""


class _Magic:
    def __getattr__(self, name): return self
    def __call__(self, *a, **k): return self
    def __eq__(self, other): return self
    def __or__(self, other): return self
    def __and__(self, other): return self
    __hash__ = object.__hash__


F = _Magic()


class _Observer:
    def __call__(self, *filters, **kw):
        return lambda fn: fn
    def filter(self, *a, **k): ...
    def outer_middleware(self, *a, **k): ...


class Router:
    def __init__(self, *a, **k):
        self.message = _Observer()
        self.callback_query = _Observer()
        self.my_chat_member = _Observer()
    def include_router(self, r): ...


class Dispatcher(Router): ...
class Bot: ...
class BaseMiddleware: ...

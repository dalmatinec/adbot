class State:
    state = None
class StatesGroup:
    def __init_subclass__(cls):
        for name, value in vars(cls).items():
            if isinstance(value, State):
                value.state = f"{cls.__name__}:{name}"

class FSMContext:
    def __init__(self): self._state, self._data = None, {}
    async def set_state(self, state=None): self._state = getattr(state, "state", state)
    async def get_state(self): return self._state
    async def set_data(self, data): self._data = dict(data)
    async def update_data(self, **kw): self._data.update(kw)
    async def get_data(self): return dict(self._data)
    async def clear(self): self._state, self._data = None, {}

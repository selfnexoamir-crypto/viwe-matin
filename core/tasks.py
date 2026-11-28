"""spawn() — create_task با نگه‌داشتن reference و لاگ خطا.

asyncio فقط weak-reference به task نگه می‌داره؛ taskی که جایی ذخیره نشه
می‌تونه وسط اجرا توسط GC حذف بشه و بی‌صدا بمیره.
"""
import asyncio
import logging

_log = logging.getLogger("tasks")
_TASKS: set = set()


def _on_done(t: "asyncio.Task"):
    _TASKS.discard(t)
    if t.cancelled():
        return
    exc = t.exception()
    if exc is not None:
        _log.error("task %s crashed: %s: %s", t.get_name(), type(exc).__name__, exc,
                   exc_info=exc)


def spawn(coro, name: str | None = None) -> "asyncio.Task":
    t = asyncio.get_running_loop().create_task(coro, name=name)
    _TASKS.add(t)
    t.add_done_callback(_on_done)
    return t

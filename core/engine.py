"""Engine — Telethon Client maker + instant_view"""
import asyncio
import sys
from pathlib import Path
from datetime import datetime, timedelta

from telethon import TelegramClient, functions
from telethon.sessions import StringSession
from telethon.errors import (
    FloodWaitError, AuthKeyError, SessionRevokedError,
    UserDeactivatedBanError,
)

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from config import Config
from models import database as db


class _BorrowedClient:
    """پروکسی روی کلاینت زنده‌ی pool.

    اگه دو کلاینت با یه session همزمان وصل بشن تلگرام AuthKeyDuplicatedError میده و
    session برای همیشه می‌میره. این wrapper کلاینت خود pool رو قرض می‌ده و
    connect/disconnect رو بی‌اثر می‌کنه تا کد قدیمی (connect → کار → disconnect)
    بدون دست‌زدن بهش امن بشه.
    """
    __slots__ = ("_c",)

    def __init__(self, real):
        object.__setattr__(self, "_c", real)

    async def connect(self, *a, **kw):
        return None

    async def disconnect(self, *a, **kw):
        return None

    def __call__(self, *a, **kw):
        return self._c(*a, **kw)

    def __getattr__(self, name):
        return getattr(self._c, name)

    def __setattr__(self, name, value):
        setattr(self._c, name, value)


def _pool_client(phone):
    """کلاینت زنده‌ی pool برای این شماره (اگه باشه)."""
    import sys as _sys
    mod = _sys.modules.get("core.pool")
    pool = getattr(mod, "POOL", None) if mod else None
    if pool is None or not phone:
        return None
    c = pool.clients.get(phone)
    try:
        return c if (c is not None and c.is_connected()) else None
    except Exception:
        return None


def make_client(session_string, proxy=None, phone=None, shared=True):
    """ساخت کلاینت با proxy per-phone.

    shared=True: اگه همین اکانت توی pool وصله، همون کلاینت قرض داده میشه (نه یه اتصال دوم).
    pool خودش shared=False میده."""
    if shared and phone and not proxy:
        live = _pool_client(phone)
        if live is not None:
            return _BorrowedClient(live)
    client = TelegramClient(
        StringSession(session_string),
        Config.API_ID,
        Config.API_HASH,
        connection_retries=3,
        retry_delay=1,
        timeout=20,
        request_retries=3,
    )

    if proxy:
        client.set_proxy(proxy)
        return client

    if phone:
        try:
            from core.proxy_loader import get_proxy_for
            p = get_proxy_for(phone)
            if p:
                client.set_proxy(p)
        except Exception:
            pass

    return client


async def instant_view(client, channel, message_ids):
    try:
        r = await client(functions.messages.GetMessagesViewsRequest(
            peer=channel, id=message_ids, increment=True))
        return {"ok": True, "result": r}
    except FloodWaitError as e:
        return {"ok": False, "flood": e.seconds, "error": f"FloodWait {e.seconds}s"}
    except Exception as e:
        return {"ok": False, "error": str(e)}


async def health_check(client):
    try:
        me = await client.get_me()
        return {"status": "ok", "user": me.username or me.first_name}
    except FloodWaitError as e:
        return {"status": "flood", "seconds": e.seconds}
    except (AuthKeyError, SessionRevokedError):
        return {"status": "dead"}
    except UserDeactivatedBanError:
        return {"status": "banned"}
    except Exception as e:
        return {"status": "error", "error": str(e)}


async def instant_read(client, entity, max_id=0):
    """علامت خوانده‌شد. send_read_acknowledge خودش برای کانال/سوپرگروه
    channels.ReadHistory و برای چت معمولی messages.ReadHistory رو انتخاب می‌کنه."""
    try:
        r = await client.send_read_acknowledge(entity, max_id=max_id or None)
        return {"ok": True, "result": r}
    except FloodWaitError as e:
        return {"ok": False, "flood": e.seconds, "error": f"FloodWait {e.seconds}s"}
    except Exception as e:
        return {"ok": False, "error": str(e)}

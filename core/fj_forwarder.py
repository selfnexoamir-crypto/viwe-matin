"""
fj_forwarder — Forward پست‌های گروه مبدا به کانال‌های جوین‌شده از طریق جوین اجباری

معماری:
  - Listener وقتی پست جدید تشخیص میده، msg_id رو تو queue می‌ذاره
  - FJForwarder از همون queue یا یه signal جدا می‌خونه
  - fj_pending_leaves رو پارس می‌کنه → {phone: [channels]}
  - پست رو بین اکانت‌ها تقسیم می‌کنه (هر اکانت سهم خودش)
  - هر اکانت پست رو به کانال‌های خودش forward می‌کنه
  - تاخیر رندوم بین forward‌ها برای طبیعی بودن
"""

import asyncio
import json
import random
import time as _time
from collections import defaultdict

from models import database as db
from config import Config
from core.engine import make_client
from core.pool import POOL


# ─── queue مشترک با listener ───────────────────────────────────────────────
# listener می‌تونه مستقیم در این queue بذاره
FJ_FORWARD_QUEUE: asyncio.Queue = asyncio.Queue(maxsize=2000)

# ─── state ─────────────────────────────────────────────────────────────────
_running = False
_task: asyncio.Task | None = None


def is_forward_enabled() -> bool:
    return db.get_setting("fj_forward_enabled", "0") == "1"


def _get_pending_map() -> dict[str, list[str]]:
    """
    fj_pending_leaves رو می‌خونه و یه dict برمی‌گردونه:
    { phone: [channel_url, ...] }
    فقط رکوردهایی که هنوز leave_at نرسیده.
    """
    raw = db.get_setting("fj_pending_leaves", "[]")
    try:
        rows = json.loads(raw)
    except Exception:
        return {}

    now = _time.time()
    result: dict[str, list[str]] = defaultdict(list)
    for row in rows:
        phone   = row.get("phone", "")
        channel = row.get("channel", "")
        leave_at = float(row.get("leave_at", 0))
        if phone and channel and leave_at > now:
            result[phone].append(channel)
    return dict(result)


def _split_work(phones: list[str], msg_count: int) -> list[tuple[str, int, int]]:
    """
    تقسیم batch پیام‌ها بین اکانت‌ها.
    برمی‌گردونه: [(phone, start_idx, end_idx), ...]
    مثال: 100 پیام، 10 اکانت → هر اکانت 10 تا
    اکانت‌ها رندوم shuffle میشن تا ترتیب ثابت نباشه.
    """
    if not phones or msg_count == 0:
        return []
    phones = list(phones)
    random.shuffle(phones)
    n = len(phones)
    per = max(1, msg_count // n)
    result = []
    idx = 0
    for i, phone in enumerate(phones):
        # آخرین اکانت بقیه رو می‌گیره
        end = idx + per if i < n - 1 else msg_count
        if idx >= msg_count:
            break
        result.append((phone, idx, min(end, msg_count)))
        idx = end
    return result


async def _forward_one(phone: str, channels: list[str],
                        source_entity, msg_ids: list[int]) -> dict:
    """
    یه اکانت مشخص، لیست msg_ids رو از source به همه channels خودش forward میکنه.
    برمی‌گردونه: {ok: int, fail: int}
    """
    from telethon.tl.functions.messages import ForwardMessagesRequest
    from telethon.tl.types import InputChannel

    ok = fail = 0
    client = None
    try:
        ss = db.get_session(phone)
        if not ss:
            return {"ok": 0, "fail": len(channels) * len(msg_ids)}
        client = make_client(ss, phone=phone)
        await asyncio.wait_for(client.connect(), timeout=20)
        if not await client.is_user_authorized():
            return {"ok": 0, "fail": len(channels) * len(msg_ids)}

        # source entity یه بار resolve کن
        from core.entity import resolve_group_entity
        src = await asyncio.wait_for(
            resolve_group_entity(client, source_entity, timeout=15), timeout=20)

        for ch_url in channels:
            try:
                dst = await asyncio.wait_for(client.get_entity(ch_url), timeout=15)
                await asyncio.wait_for(
                    client(ForwardMessagesRequest(
                        from_peer=src,
                        id=msg_ids,
                        to_peer=dst,
                        drop_author=False,      # نویسنده حفظ بشه
                        random_id=[random.randint(1, 2**31) for _ in msg_ids],
                    )),
                    timeout=30,
                )
                ok += len(msg_ids)
                # تاخیر رندوم بین کانال‌ها
                await asyncio.sleep(random.uniform(1.5, 4.0))
            except Exception as e:
                fail += len(msg_ids)
                db.debug_log("fj_forwarder", "forward_ch", "WARN",
                             f"{phone} → {ch_url}: {type(e).__name__}: {str(e)[:80]}")

    except Exception as e:
        db.debug_log("fj_forwarder", "forward_one", "ERROR",
                     f"{phone}: {type(e).__name__}: {str(e)[:100]}")
        fail += 1
    finally:
        if client:
            try:
                await client.disconnect()
            except Exception:
                pass
    return {"ok": ok, "fail": fail}


async def _process_batch(source_id, msg_ids: list[int]):
    """
    یه batch از msg_ids رو پردازش می‌کنه:
    1. pending map رو می‌کشه
    2. تقسیم کار بین اکانت‌ها
    3. هر اکانت forward می‌کنه
    """
    if not msg_ids:
        return

    pending_map = _get_pending_map()
    if not pending_map:
        db.debug_log("fj_forwarder", "batch", "SKIP", "pending_map خالیه")
        return

    phones = list(pending_map.keys())
    assignments = _split_work(phones, len(msg_ids))
    if not assignments:
        return

    db.debug_log("fj_forwarder", "batch", "START",
                 f"msg_count={len(msg_ids)} | accounts={len(phones)} | "
                 f"assignments={len(assignments)}")

    tasks = []
    for phone, start, end in assignments:
        slice_ids  = msg_ids[start:end]
        channels   = pending_map[phone]
        if not slice_ids or not channels:
            continue
        # تاخیر رندوم شروع هر اکانت (0–5 ثانیه) برای طبیعی بودن
        delay = random.uniform(0, 5)
        tasks.append(_delayed_forward(delay, phone, channels, source_id, slice_ids))

    results = await asyncio.gather(*tasks, return_exceptions=True)
    total_ok = total_fail = 0
    for r in results:
        if isinstance(r, dict):
            total_ok   += r.get("ok", 0)
            total_fail += r.get("fail", 0)

    db.debug_log("fj_forwarder", "batch", "DONE",
                 f"ok={total_ok} fail={total_fail}")


async def _delayed_forward(delay: float, phone, channels, source_id, msg_ids):
    if delay > 0:
        await asyncio.sleep(delay)
    return await _forward_one(phone, channels, source_id, msg_ids)


# ─── Batch accumulator ──────────────────────────────────────────────────────
# پیام‌های جدید رو چند ثانیه جمع می‌کنه بعد یه‌جا پردازش می‌کنه
# (در صورت burst چند پیام پشت‌هم)
_BATCH_WINDOW = 3.0   # ثانیه صبر بعد از اولین پیام در batch


class FJForwarder:
    def __init__(self):
        self._task: asyncio.Task | None = None
        self._running = False
        self.source_entity = None   # cache entity گروه مبدا

    def start(self):
        if self._running:
            return
        self._running = True
        self._task = asyncio.create_task(self._loop())
        db.debug_log("fj_forwarder", "start", "OK", "forwarder started")

    def stop(self):
        self._running = False
        if self._task:
            self._task.cancel()

    async def _resolve_source(self):
        """entity گروه مبدا رو resolve می‌کنه (با cache)"""
        if self.source_entity is not None:
            return self.source_entity
        target = Config.TARGET_CHANNEL_ID or Config.TARGET_CHANNEL_USERNAME
        for phone in POOL.all_phones:
            try:
                c = await POOL.get(phone)
                if c is None:
                    continue
                from core.entity import resolve_group_entity
                ent = await asyncio.wait_for(
                    resolve_group_entity(c, target, timeout=15), timeout=20)
                self.source_entity = ent
                return ent
            except Exception:
                continue
        return None

    async def _loop(self):
        """
        queue رو می‌خونه، batch جمع می‌کنه، پردازش می‌کنه.
        """
        while self._running:
            try:
                # اولین آیتم رو منتظر بمون
                try:
                    msg_id = await asyncio.wait_for(
                        FJ_FORWARD_QUEUE.get(), timeout=5)
                except asyncio.TimeoutError:
                    continue

                if not is_forward_enabled():
                    FJ_FORWARD_QUEUE.task_done()
                    continue

                # batch window: بقیه پیام‌های صف رو هم جمع کن
                batch = [msg_id]
                deadline = _time.time() + _BATCH_WINDOW
                while _time.time() < deadline:
                    try:
                        extra = FJ_FORWARD_QUEUE.get_nowait()
                        batch.append(extra)
                        FJ_FORWARD_QUEUE.task_done()
                    except asyncio.QueueEmpty:
                        await asyncio.sleep(0.2)

                FJ_FORWARD_QUEUE.task_done()

                # batch رو پردازش کن
                try:
                    src = await self._resolve_source()
                    if src is None:
                        db.debug_log("fj_forwarder", "loop", "WARN",
                                     "source entity resolve نشد")
                        continue
                    await _process_batch(src, batch)
                except Exception as e:
                    db.debug_log("fj_forwarder", "loop", "ERROR",
                                 f"{type(e).__name__}: {str(e)[:120]}")

            except asyncio.CancelledError:
                break
            except Exception as e:
                db.debug_log("fj_forwarder", "loop", "CRITICAL",
                             f"{type(e).__name__}: {str(e)[:120]}")
                await asyncio.sleep(5)


# singleton
FJ_FORWARDER = FJForwarder()

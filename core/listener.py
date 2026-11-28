"""Listener — سرعت بالا + Purge صف"""
import asyncio, sys, random, time as _time
from pathlib import Path
from datetime import datetime, timedelta
from collections import Counter

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from config import Config
from models import database as db
from core.engine import instant_view
from core.pool import POOL
from core.entity import resolve_group_entity
from core.view_guard import VIEW_GUARD

from telethon.errors import FloodWaitError
from core.tasks import spawn

_VIEW_PAUSED = False
_ENTITY_CACHE = {}
_PHONE_LOCKS = {}
MAX_POST_AGE = 30  # فقط پست‌های <30s
MAX_QUEUE = 20     # اگه صف >20 → پاکسازی


def set_view_paused(p):
    global _VIEW_PAUSED
    _VIEW_PAUSED = p


def is_view_paused():
    return _VIEW_PAUSED


class MultiTargetListener:
    def __init__(self, admin_phone=None, poll_interval=1, num_workers=6):
        self.admin_phone = admin_phone
        self.poll_interval = poll_interval
        self.num_workers = num_workers
        self.is_running = False
        self.client = None
        self.channel_entity = None
        self.last_msg = 0
        self.queue = asyncio.Queue(maxsize=5000)
        self.workers = []

    async def start(self):
        if not Config.AUTO_VIEW_ENABLED:
            return
        self.is_running = True
        db.debug_log("listener", "start", "OK", f"workers={self.num_workers}")

        if not POOL._initialized:
            await POOL.init()

        try:
            from core.smart_scheduler import get_smart_scheduler
            s = get_smart_scheduler()
            s.start()
            self._scheduler = s
        except Exception as e:
            db.debug_log("listener", "scheduler", "ERROR", str(e))

        for i in range(self.num_workers):
            w = spawn(self._worker(i))
            self.workers.append(w)
        self._warm_task = spawn(self._warm_loop())

        while self.is_running:
            try:
                gid = db.get_target_group_id()
                if gid != self._gid:          # گروه از ربات عوض شده
                    db.debug_log("group_listener", "group", "CHANGED", f"{self._gid} -> {gid}")
                    self._gid, self.group_entity, self.client = gid, None, None
                await self._ensure_client()
                await self._poll()
            except Exception as e:
                db.debug_log("listener", "loop", "ERROR", f"{type(e).__name__}: {e}")
                self.client = None
                self.channel_entity = None
                await asyncio.sleep(3)
                continue
            await asyncio.sleep(self.poll_interval)

    async def _ensure_client(self):
        if self.client is not None and self.client.is_connected():
            return
        for phone in POOL.all_phones:
            if phone in POOL.blacklist:
                continue
            c = await POOL.get(phone)
            if c is not None:
                self.client = c
                break
        if self.client is None:
            raise RuntimeError("no healthy account")
        target = Config.TARGET_CHANNEL_ID or Config.TARGET_CHANNEL_USERNAME
        self.channel_entity = await asyncio.wait_for(
            self.client.get_entity(target), timeout=15)
        msgs = await self.client.get_messages(self.channel_entity, limit=1)
        if msgs:
            self.last_msg = msgs[0].id
            db.debug_log("listener", "init", "OK",
                         f"channel: {self.channel_entity.title} | last: {self.last_msg}")

    async def _poll(self):
        if self.client is None or self.channel_entity is None:
            return
        msgs = await asyncio.wait_for(
            self.client.get_messages(self.channel_entity, limit=50), timeout=15)
        if not msgs:
            return
        
        # ═══ اگه صف پر شده، پاکسازی ═══
        qsize = self.queue.qsize()
        if qsize > MAX_QUEUE:
            purged = 0
            while not self.queue.empty():
                try:
                    self.queue.get_nowait()
                    self.queue.task_done()
                    purged += 1
                except asyncio.QueueEmpty:
                    break
            db.debug_log("listener", "purge", "WARN",
                         f"queue:{qsize} → purged:{purged}")
        
        new_msgs = [m for m in msgs if m.id > self.last_msg]
        if not new_msgs:
            return
        new_msgs.sort(key=lambda m: m.id)
        self.last_msg = new_msgs[-1].id
        if _VIEW_PAUSED:
            return
        for m in new_msgs:
            try:
                ts = m.date.timestamp() if m.date else _time.time()
                self.queue.put_nowait((m.id, ts))
            except asyncio.QueueFull:
                pass

        if new_msgs:
            db.debug_log("listener", "queue_add", "OK",
                         f"+{len(new_msgs)} | queue: {self.queue.qsize()}")

    async def _worker(self, wid):
        while self.is_running:
            try:
                item = await asyncio.wait_for(self.queue.get(), timeout=5)
            except asyncio.TimeoutError:
                continue
            except Exception:
                break

            msg_id, ts = item
            age = _time.time() - ts
            if age > MAX_POST_AGE:
                db.debug_log("listener", f"w{wid}", "SKIP",
                             f"ID={msg_id} age={age:.0f}s")
                self.queue.task_done()
                continue

            # ═══ هر worker تمام شیفت فعال ═══
            phones = list(POOL.active_phones())

            # تاخیر شروع ویو — قابل تنظیم از دیتابیس
            try:
                _view_start_delay = float(db.get_setting("view_start_delay", 0) or 0)
            except Exception:
                _view_start_delay = 0.0
            if _view_start_delay > 0:
                db.debug_log("listener", f"w{wid}", "DELAY",
                             f"ID={msg_id} | تاخیر {_view_start_delay:.1f}s قبل شروع ویو")
                await asyncio.sleep(_view_start_delay)

            try:
                await self._view_message(msg_id, phones, wid)
            except Exception as e:
                db.debug_log("listener", f"w{wid}", "ERROR", str(e))
            finally:
                try:
                    self.queue.task_done()
                except Exception:
                    pass

    async def _view_one(self, phone, msg_id):
        # ── ViewGuard: اجازه می‌گیریم قبل از هر کاری ──
        ok, wait = VIEW_GUARD.can_view(phone, msg_id)
        if not ok:
            if wait == -1:
                return ("SKIP", phone)       # سهمیه تموم یا تکراری
            # نیاز به صبر داره — توی این round skip میشه
            return ("WAIT", phone)

        # Lock per phone
        lock = _PHONE_LOCKS.get(phone)
        if lock is None:
            lock = asyncio.Lock()
            _PHONE_LOCKS[phone] = lock

        async with lock:
            try:
                c = POOL.clients.get(phone)
                if c is None or not c.is_connected():
                    c = await POOL.get(phone)
                    if c is None:
                        return ("NO_CLI", phone)
                target = Config.TARGET_CHANNEL_ID or Config.TARGET_CHANNEL_USERNAME
                key = (phone, str(target))
                ent = _ENTITY_CACHE.get(key)
                if ent is None:
                    try:
                        ent = await asyncio.wait_for(c.get_entity(target), timeout=10)
                        _ENTITY_CACHE[key] = ent
                    except Exception as e:
                        return (f"ENT:{type(e).__name__}", phone)
                try:
                    r = await asyncio.wait_for(
                        instant_view(c, ent, [msg_id]), timeout=10)
                except asyncio.TimeoutError:
                    return ("V_TIMEOUT", phone)
                except Exception as e:
                    return (f"V:{type(e).__name__}", phone)
                if r.get("ok"):
                    VIEW_GUARD.on_view_ok(phone, msg_id)   # ✅ ثبت ویو موفق
                    return ("OK", phone)
                elif "flood" in r:
                    VIEW_GUARD.on_flood(phone, r["flood"]) # ⏳ cooldown
                    u = (datetime.utcnow() + timedelta(seconds=r["flood"])).isoformat()
                    db.update_account_status(phone, "flood", r["error"], u)
                    return (f"FLOOD:{r['flood']}", phone)
                else:
                    return (f"E:{str(r.get('error',''))[:20]}", phone)
            except Exception as e:
                return (f"OUT:{type(e).__name__}", phone)

    async def _view_message(self, msg_id, phones, wid):
        if not phones:
            return

        # ── فقط اکانت‌هایی که ViewGuard اجازه میده ──
        eligible = VIEW_GUARD.eligible_phones(phones, msg_id)
        skipped  = len(phones) - len(eligible)

        if not eligible:
            db.debug_log("listener", "view_done", "SKIP",
                         f"ID={msg_id} | w{wid} | همه skip (سهمیه/تکرار/flood)")
            return

        random.shuffle(eligible)
        n = len(eligible)

        # پنجره زمانی طبیعی: هر اکانت با فاصله کافی از قبلی
        # به جای پخش یکنواخت، از توزیع نامنظم استفاده میکنیم
        window = random.uniform(
            max(n * 0.3, 3.0),   # حداقل ۳ ثانیه یا ۳۰۰ms/اکانت
            max(n * 0.8, 8.0)    # حداکثر ۸ ثانیه یا ۸۰۰ms/اکانت
        )
        t0 = _time.time()

        async def one(phone, delay):
            if delay > 0:
                await asyncio.sleep(delay)
            return await self._view_one(phone, msg_id)

        tasks = []
        for i, phone in enumerate(eligible):
            # توزیع نامنظم‌تر: بعضی اکانت‌ها زودتر، بعضی دیرتر
            base = (i / max(n, 1)) * window
            jitter = random.gauss(0, window * 0.08)  # نویز گاوسی
            d = max(0.0, base + jitter)
            tasks.append(spawn(one(phone, d)))

        try:
            results = await asyncio.wait_for(
                asyncio.gather(*tasks, return_exceptions=True),
                timeout=window + 15)
        except asyncio.TimeoutError:
            results = []
            for t in tasks:
                if not t.done():
                    t.cancel()

        reasons = Counter()
        suc = 0
        for r in results:
            if isinstance(r, tuple) and r:
                reason = r[0]
                reasons[reason] = reasons.get(reason, 0) + 1
                if reason == "OK":
                    suc += 1
        elapsed = _time.time() - t0
        summary = " | ".join(f"{k}={v}" for k, v in reasons.most_common(5))
        guard_st = VIEW_GUARD.stats()
        db.debug_log("listener", "view_done", "RESULT",
                     f"ID={msg_id} | w{wid} | {suc}/{n} | skip:{skipped} | "
                     f"{elapsed:.1f}s | {summary} | "
                     f"flood:{guard_st['flooded_now']} exhaust:{guard_st['exhausted_today']}")

    async def stop(self):
        self.is_running = False
        for w in self.workers:
            w.cancel()


class ChannelListener(MultiTargetListener):
    pass


# ═══════════════════════════════════════════════════════
#  GroupListener — ویو + خواندن پیام‌های گروه/کانال هدف
# ═══════════════════════════════════════════════════════

_GROUP_ENTITY_CACHE = {}
_GROUP_PHONE_LOCKS  = {}
_CURRENT = None          # لیسنر در حال اجرا
_TASK    = None
_MODES   = ("both", "view", "read")
_MODE_FA = {"both": "ویو + خواندن", "view": "فقط ویو", "read": "فقط خواندن"}


def get_group_listener():
    return _CURRENT


async def ensure_group_listener():
    """اگه لیسنر نمی‌چرخه (مثلاً موقع بوت گروهی تنظیم نبوده) روشنش می‌کنه."""
    global _CURRENT, _TASK
    if _TASK is not None and not _TASK.done():
        return _CURRENT
    gl = GroupListener()
    _CURRENT = gl
    _TASK = spawn(gl.start())
    return gl


class GroupListener:
    """هر پیام جدید گروه/کانال رو با همه‌ی اکانت‌های شیفت فعال ویو می‌کنه و/یا می‌خونه.
    حالا از چند گروه هدف (حداکثر ۲ تا) پشتیبانی می‌کنه — هر گروه poll جداست، ویو مشترکه."""

    MAX_GROUP_MSG_AGE  = 60
    MAX_GROUP_QUEUE    = 50
    POLL_INTERVAL      = 1.5
    BAD_PHONE_COOLDOWN = 300
    SETTINGS_EVERY     = 10
    MAX_CATCHUP        = 600
    POLL_FAIL_LIMIT    = 3

    def __init__(self, num_workers: int = 4):
        self.num_workers   = num_workers
        self.is_running    = False
        self.mode          = "both"
        self._settings_ts  = 0.0
        self._bad          = {}
        self._warm_task    = None
        self.queue         = asyncio.Queue(maxsize=5000)
        self.workers: list = []
        self.fail_reasons  = Counter()
        self.stats = {"polls": 0, "new_msgs": 0, "rounds": 0, "views_ok": 0, "reads_ok": 0,
                      "read_fail": "", "last_poll": 0.0, "last_error": "", "last_round": "",
                      "skipped_old": 0, "poll_errors": 0, "last_seen_id": 0, "last_new_ts": 0.0}

        # ── چند گروه هدف: هر slot state مستقل دارد ──
        # _slots = {gid: {"client": ..., "phone": ..., "entity": ..., "title": ...,
        #                 "last_msg_id": 0, "poll_fail": 0}}
        self._slots: dict = {}
        self._gids: list  = []        # آخرین لیست گروه‌های active

    # ─── کمکی: slot رو init یا برگردون ───
    def _slot(self, gid) -> dict:
        if gid not in self._slots:
            self._slots[gid] = {
                "client": None, "phone": None, "entity": None,
                "title": None, "last_msg_id": 0, "poll_fail": 0,
            }
        return self._slots[gid]

    # ── backward-compat: کدهای قدیمی که self._gid می‌خونن ──
    @property
    def _gid(self):
        return self._gids[0] if self._gids else 0

    @property
    def client(self):
        return self._slot(self._gid).get("client") if self._gid else None

    @property
    def client_phone(self):
        return self._slot(self._gid).get("phone") if self._gid else None

    @property
    def group_entity(self):
        return self._slot(self._gid).get("entity") if self._gid else None

    @property
    def group_title(self):
        return self._slot(self._gid).get("title") if self._gid else None

    @property
    def last_msg_id(self):
        return self._slot(self._gid).get("last_msg_id", 0) if self._gid else 0

    # ───────────────── settings ─────────────────
    async def _refresh_settings(self, force: bool = False):
        now = _time.time()
        if not force and now - self._settings_ts < self.SETTINGS_EVERY:
            return
        self._settings_ts = now
        try:
            new_gids = await asyncio.to_thread(db.get_target_groups)
            mode     = await asyncio.to_thread(db.get_setting, "group_action", "both")
        except Exception as e:
            self.stats["last_error"] = f"settings: {type(e).__name__}: {str(e)[:100]}"
            return
        self.mode = mode if mode in _MODES else "both"

        # گروه‌هایی که حذف شدن → slot پاک بشه
        removed = set(self._gids) - set(new_gids)
        for gid in removed:
            db.debug_log("group_listener", "group", "REMOVED", str(gid))
            self._slots.pop(gid, None)

        # گروه‌های جدید اضافه شدن → slot خودکار ساخته می‌شه در _slot()
        added = set(new_gids) - set(self._gids)
        for gid in added:
            db.debug_log("group_listener", "group", "ADDED", str(gid))
            self._slot(gid)["last_msg_id"] = 0   # baseline reset

        self._gids = new_gids

    # ───────────────── entry ─────────────────
    async def start(self):
        await self._refresh_settings(force=True)
        if not self._gids:
            db.debug_log("group_listener", "start", "SKIP", "گروه هدف تنظیم نشده")
            return

        self.is_running = True
        db.debug_log("group_listener", "start", "OK",
                     f"groups={self._gids} | mode={self.mode} | workers={self.num_workers}")

        if not POOL._initialized:
            await POOL.init()

        try:
            from core.smart_scheduler import get_smart_scheduler
            _sched = get_smart_scheduler()
            _sched.start()
            self._scheduler = _sched
            db.debug_log("group_listener", "scheduler", "OK", "shift rotation started (smart)")
        except Exception as _se:
            db.debug_log("group_listener", "scheduler", "ERROR", str(_se))

        for i in range(self.num_workers):
            self.workers.append(spawn(self._worker(i)))
        self._warm_task = spawn(self._warm_loop())

        while self.is_running:
            try:
                await self._refresh_settings()
                # هر گروه رو جداگانه poll می‌کنیم — یک کلاینت مشترک برای هر گروه
                for gid in list(self._gids):
                    await self._ensure_client_for(gid)
                    await self._poll_for(gid)
            except Exception as e:
                self.stats["last_error"] = f"{type(e).__name__}: {str(e)[:150]}"
                db.debug_log("group_listener", "loop", "ERROR", self.stats["last_error"])
                await asyncio.sleep(1.5)
                continue
            await asyncio.sleep(self.POLL_INTERVAL)

    # ───────────────── warm entity cache ─────────────────
    async def _warm_loop(self):
        sem = asyncio.Semaphore(5)

        async def warm_one(phone, gid):
            key = (phone, str(gid))
            if key in _GROUP_ENTITY_CACHE:
                return
            async with sem:
                try:
                    c = POOL.clients.get(phone)
                    if c is None or not c.is_connected():
                        return
                    _GROUP_ENTITY_CACHE[key] = await resolve_group_entity(c, gid, timeout=20)
                except Exception as e:
                    db.debug_log("group_listener", "warm", "WARN",
                                 f"{phone}/{gid}: {type(e).__name__}: {str(e)[:80]}")

        while self.is_running:
            try:
                nxt = (POOL.active_shift % POOL.num_shifts) + 1
                phones = list(POOL.shifts.get(POOL.active_shift, [])) + list(POOL.shifts.get(nxt, []))
                warm_tasks = [warm_one(p, gid) for p in phones for gid in self._gids]
                await asyncio.gather(*warm_tasks, return_exceptions=True)
            except Exception as e:
                db.debug_log("group_listener", "warm", "ERROR", f"{type(e).__name__}: {e}")
            await asyncio.sleep(20)

    # ───────────────── ensure client per group ─────────────────
    async def _pick_client(self):
        now = _time.time()
        cands = [p for p in POOL.all_phones
                 if p not in POOL.blacklist and self._bad.get(p, 0) < now]
        cands.sort(key=lambda p: 0 if POOL._is_live(p) else 1)
        for phone in cands[:15]:
            c = await POOL.get(phone)
            if c is not None:
                return phone, c
        return None, None

    async def _ensure_client_for(self, gid):
        sl = self._slot(gid)
        if (sl["client"] is not None and sl["client"].is_connected()
                and sl["entity"] is not None):
            return
        last_exc = None
        for _ in range(8):
            phone, c = await self._pick_client()
            if c is None:
                break
            try:
                ent  = await resolve_group_entity(c, gid)
                msgs = await asyncio.wait_for(c.get_messages(ent, limit=1), timeout=15)
            except Exception as e:
                self._bad[phone] = _time.time() + self.BAD_PHONE_COOLDOWN
                last_exc = e
                db.debug_log("group_listener", "pick", "WARN",
                             f"{phone}/{gid}: {type(e).__name__}: {str(e)[:100]}")
                continue
            sl["client"] = c
            sl["phone"]  = phone
            sl["entity"] = ent
            sl["title"]  = getattr(ent, "title", None) or getattr(ent, "username", None)
            if msgs and sl["last_msg_id"] == 0:
                sl["last_msg_id"] = msgs[0].id
            self.stats["last_error"] = ""
            db.debug_log("group_listener", "init", "OK",
                         f"group: {sl['title']} ({gid}) | via {phone} | last: {sl['last_msg_id']}")
            return
        raise last_exc or RuntimeError(f"هیچ اکانت سالمی برای لیسنر {gid} نیست")

    # ───────────────── poll per group ─────────────────
    async def _poll_for(self, gid):
        sl = self._slot(gid)
        if sl["client"] is None or sl["entity"] is None:
            return

        try:
            msgs = await asyncio.wait_for(
                sl["client"].get_messages(sl["entity"], limit=100, min_id=sl["last_msg_id"]),
                timeout=15)
        except FloodWaitError as e:
            self.stats["last_error"] = f"FloodWait {e.seconds}s روی خواننده {gid}"
            await asyncio.sleep(min(e.seconds, 30))
            return
        except (asyncio.TimeoutError, ConnectionError, OSError) as e:
            sl["poll_fail"] = sl.get("poll_fail", 0) + 1
            self.stats["poll_errors"] += 1
            self.stats["last_error"] = (
                f"poll {gid} {type(e).__name__} ({sl['poll_fail']}/{self.POLL_FAIL_LIMIT})")
            if sl["poll_fail"] >= self.POLL_FAIL_LIMIT:
                sl["poll_fail"] = 0
                sl["client"] = sl["entity"] = sl["phone"] = None   # خواننده عوض بشه
            return
        sl["poll_fail"] = 0
        self.stats["polls"] += 1
        self.stats["last_poll"] = _time.time()
        if not msgs:
            return

        qsize = self.queue.qsize()
        if qsize > self.MAX_GROUP_QUEUE:
            purged = 0
            while not self.queue.empty():
                try:
                    self.queue.get_nowait()
                    self.queue.task_done()
                    purged += 1
                except asyncio.QueueEmpty:
                    break
            db.debug_log("group_listener", "purge", "WARN", f"queue:{qsize} → purged:{purged}")

        new_msgs = [m for m in msgs if m.id > sl["last_msg_id"]]
        if not new_msgs:
            return
        new_msgs.sort(key=lambda m: m.id)
        sl["last_msg_id"] = new_msgs[-1].id
        self.stats["new_msgs"] += len(new_msgs)
        self.stats["last_seen_id"] = sl["last_msg_id"]
        self.stats["last_new_ts"] = _time.time()

        if _VIEW_PAUSED:
            return

        now = _time.time()
        queued = 0
        for m in new_msgs:
            mts = m.date.timestamp() if m.date else now
            if now - mts > self.MAX_CATCHUP:
                self.stats["skipped_old"] += 1
                continue
            try:
                # آیتم صف: (gid, msg_id, timestamp_discovery)
                self.queue.put_nowait((gid, m.id, now))
                queued += 1
            except asyncio.QueueFull:
                pass

        db.debug_log("group_listener", "queue_add", "OK",
                     f"{gid} +{queued}/{len(new_msgs)} | queue: {self.queue.qsize()}")

    # ───────────────── worker ─────────────────
    async def _worker(self, wid: int):
        while self.is_running:
            try:
                item = await asyncio.wait_for(self.queue.get(), timeout=5)
            except asyncio.TimeoutError:
                continue
            except Exception:
                break

            # آیتم ممکنه فرمت قدیمی (msg_id, ts) یا جدید (gid, msg_id, ts) باشه
            if len(item) == 3:
                gid, msg_id, ts = item
            else:
                gid, (msg_id, ts) = self._gid, item

            age = _time.time() - ts
            if age > self.MAX_GROUP_MSG_AGE:
                db.debug_log("group_listener", f"w{wid}", "SKIP",
                             f"gid={gid} ID={msg_id} age={age:.0f}s")
                self.queue.task_done()
                continue

            phones = list(POOL.active_phones())
            try:
                await self._read_message(gid, msg_id, phones, wid)
            except Exception as e:
                db.debug_log("group_listener", f"w{wid}", "ERROR", str(e))
            finally:
                try:
                    self.queue.task_done()
                except Exception:
                    pass

    # ───────────────── یک اکانت، یک پیام، یک گروه ─────────────────
    def _flood(self, phone, r):
        u = (datetime.utcnow() + timedelta(seconds=r["flood"])).isoformat()
        db.update_account_status(phone, "flood", r["error"], u)
        return (f"FLOOD:{r['flood']}", phone)

    async def _read_one(self, phone: str, gid, msg_id: int):
        lock = _GROUP_PHONE_LOCKS.setdefault((phone, str(gid)), asyncio.Lock())
        async with lock:
            try:
                c = POOL.clients.get(phone)
                if c is None or not c.is_connected():
                    c = await POOL.get(phone)
                    if c is None:
                        return ("NO_CLI", phone)

                key = (phone, str(gid))
                ent = _GROUP_ENTITY_CACHE.get(key)
                if ent is None:
                    try:
                        ent = await resolve_group_entity(c, gid, timeout=8)
                        _GROUP_ENTITY_CACHE[key] = ent
                    except Exception as e:
                        return (f"ENT:{type(e).__name__}", phone)

                from core.engine import instant_view, instant_read
                mode = self.mode

                if mode in ("view", "both"):
                    try:
                        r = await asyncio.wait_for(instant_view(c, ent, [msg_id]), timeout=10)
                    except asyncio.TimeoutError:
                        return ("V_TIMEOUT", phone)
                    if r.get("ok"):
                        self.stats["views_ok"] += 1
                    elif "flood" in r:
                        return self._flood(phone, r)
                    else:
                        return (f"V:{str(r.get('error', ''))[:24]}", phone)

                if mode in ("read", "both"):
                    try:
                        r2 = await asyncio.wait_for(instant_read(c, ent, msg_id), timeout=10)
                    except asyncio.TimeoutError:
                        r2 = {"ok": False, "error": "timeout"}
                    if r2.get("ok"):
                        self.stats["reads_ok"] += 1
                    elif "flood" in r2:
                        return self._flood(phone, r2)
                    elif mode == "read":
                        return (f"R:{str(r2.get('error', ''))[:24]}", phone)
                    else:
                        self.stats["read_fail"] = str(r2.get("error", ""))[:60]
                return ("OK", phone)
            except Exception as e:
                return (f"OUT:{type(e).__name__}", phone)

    # ───────────────── یک پیام، همه‌ی اکانت‌ها ─────────────────
    async def _read_message(self, gid, msg_id: int, phones: list, wid: int):
        if not phones:
            self.stats["last_round"] = f"gid={gid} ID={msg_id} | شیفت فعال اکانتی نداره"
            return 0, 0, Counter()
        random.shuffle(phones)
        n      = len(phones)
        window = random.uniform(1.5, 2.5)
        t0     = _time.time()

        async def one(phone, delay):
            if delay > 0:
                await asyncio.sleep(delay)
            return await self._read_one(phone, gid, msg_id)

        tasks = [
            spawn(one(phone, (i / max(n, 1)) * window + random.uniform(0, 0.15)))
            for i, phone in enumerate(phones)
        ]
        try:
            results = await asyncio.wait_for(
                asyncio.gather(*tasks, return_exceptions=True), timeout=window + 10)
        except asyncio.TimeoutError:
            results = []
            for t in tasks:
                if not t.done():
                    t.cancel()

        reasons, suc = Counter(), 0
        for r in results:
            if isinstance(r, tuple) and r:
                reasons[r[0]] += 1
                if r[0] == "OK":
                    suc += 1
                else:
                    self.fail_reasons[r[0]] += 1

        elapsed = _time.time() - t0
        summary = " | ".join(f"{k}={v}" for k, v in reasons.most_common(5))
        self.stats["rounds"] += 1
        self.stats["last_round"] = f"gid={gid} ID={msg_id} | {suc}/{n} | {elapsed:.1f}s | {summary}"
        db.debug_log("group_listener", "read_done", "RESULT",
                     f"gid={gid} ID={msg_id} | w{wid} | {suc}/{n} | {elapsed:.1f}s | {summary} | q:{self.queue.qsize()}")
        return suc, n, reasons

    # ───────────────── تشخیص و تست ─────────────────
    def diag_text(self) -> str:
        s = self.stats
        age = f"{_time.time() - s['last_poll']:.0f}s پیش" if s["last_poll"] else "هنوز نشده"
        phones = list(POOL.active_phones())
        live   = sum(1 for p in phones if POOL._is_live(p))
        running = "✅ روشن" if self.is_running else "🔴 خاموش"
        if s["last_new_ts"]:
            last_new = f"{_time.time() - s['last_new_ts']:.0f}s پیش"
        else:
            last_new = "هنوز پیام جدیدی نیومده"

        # خط‌های هر گروه
        group_lines = ""
        for gid in self._gids:
            sl = self._slots.get(gid, {})
            title  = sl.get("title") or "هنوز resolve نشده"
            reader = sl.get("phone") or "—"
            warmed = sum(1 for p in phones if (p, str(gid)) in _GROUP_ENTITY_CACHE)
            baseline = sl.get("last_msg_id", 0)
            group_lines += (
                f"  🎯 <code>{db.fmt_group(gid)}</code> → <b>{title}</b>"
                f" | خواننده: <code>{reader}</code>"
                f" | entity آماده: {warmed} | baseline: {baseline}\n"
            )

        t = (
            f"🩺 <b>تشخیص ویو</b>\n━━━━━━━━━━━━━━━━━━━━━━\n"
            f"لیسنر: {running}{'  ⏸ <b>PAUSE</b>' if _VIEW_PAUSED else ''}\n"
            f"⚙️ عملیات: {_MODE_FA.get(self.mode, self.mode)}\n"
            f"{group_lines}"
            f"{'کنارگذاشته: ' + str(sum(1 for v in self._bad.values() if v > _time.time())) + ' اکانت' if self._bad else ''}\n"
            f"🔄 poll: {s['polls']} | آخرین: {age}\n"
            f"✉️ پیام جدید دیده‌شده: <b>{s['new_msgs']}</b> | صف: {self.queue.qsize()}"
            f"{' | قدیمیِ ردشده: ' + str(s['skipped_old']) if s['skipped_old'] else ''}\n"
            f"آخرین جدید: {last_new} | خطای poll: {s['poll_errors']}\n"
            f"🎬 راند: {s['rounds']} | ویو موفق: <b>{s['views_ok']}</b> | خواندن موفق: {s['reads_ok']}\n"
            f"👥 شیفت {POOL.active_shift}: {len(phones)} اکانت | وصل: {live}\n"
        )
        if s["last_round"]:
            t += f"📋 آخرین راند: <code>{s['last_round'][:180]}</code>\n"
        if self.fail_reasons:
            top = ", ".join(f"{k}×{v}" for k, v in self.fail_reasons.most_common(4))
            t += f"🔴 دلایل شکست: <code>{top[:160]}</code>\n"
        if s["read_fail"]:
            t += f"⚠️ خطای خواندن: <code>{s['read_fail']}</code>\n"
        if s["last_error"]:
            t += f"❗ آخرین خطای لیسنر: <code>{s['last_error'][:180]}</code>\n"
        return t

    async def test_round(self) -> str:
        """روی آخرین پیام گروه اول یه راند کامل اجرا می‌کنه."""
        await self._refresh_settings(force=True)
        if not self._gids:
            return "❌ هیچ گروهی تنظیم نشده."
        gid = self._gids[0]
        await self._ensure_client_for(gid)
        sl = self._slot(gid)
        msgs = await asyncio.wait_for(sl["client"].get_messages(sl["entity"], limit=1), timeout=15)
        if not msgs:
            return "❌ گروه هیچ پیامی نداره."
        phones = list(POOL.active_phones())
        suc, n, reasons = await self._read_message(gid, msgs[0].id, phones, -1)
        text = f"🧪 <b>تست روی پیام {msgs[0].id} (گروه {db.fmt_group(gid)})</b>\n✅ موفق: <b>{suc}/{n}</b> ({_MODE_FA[self.mode]})"
        for k, v in reasons.most_common(6):
            if k != "OK":
                text += f"\n🔴 {v}× <code>{k}</code>"
        return text

    # ───────────────── stop ─────────────────
    async def stop(self):
        self.is_running = False
        if self._warm_task:
            self._warm_task.cancel()
        for w in self.workers:
            w.cancel()

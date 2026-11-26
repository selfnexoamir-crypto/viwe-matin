"""
Slow Joiner — هر N دقیقه BATCH_SIZE اکانت وارد گروه می‌شن
تنظیمات از پنل ربات خونده می‌شه (fj_join_interval_min / fj_batch_size)
"""
import asyncio
import json
import sys
from pathlib import Path
from datetime import datetime

sys.path.insert(0, '/root/tg_master_bot')
from models import database as db
from core.engine import make_client
from telethon import functions
from telethon.errors import (
    UserAlreadyParticipantError,
    FloodWaitError,
    ChannelPrivateError,
    InviteRequestSentError,
    UserBannedInChannelError,
)

# ─── ثابت‌های پایه ────────────────────────────────────────────
TARGET_USERNAME      = "vornixbazarbot"   # آیدی ربات/کانال هدف
DEFAULT_INTERVAL_MIN = 10                  # پیش‌فرض اگه DB خالی باشه (دقیقه)
DEFAULT_BATCH_SIZE   = 5                   # پیش‌فرض اگه DB خالی باشه

STATE_FILE = Path("/root/tg_master_bot/proxies/join_state.json")
LOG_FILE   = Path("/root/tg_master_bot/logs/slow_joiner.log")


def get_interval_sec() -> int:
    """فاصله بین دورها رو از DB می‌خونه (ثانیه)"""
    try:
        minutes = int(db.get_setting("fj_join_interval_min", str(DEFAULT_INTERVAL_MIN)))
        return max(60, minutes * 60)
    except Exception:
        return DEFAULT_INTERVAL_MIN * 60


def get_batch_size() -> int:
    """تعداد اکانت هر دور رو از DB می‌خونه"""
    try:
        return max(1, int(db.get_setting("fj_batch_size", str(DEFAULT_BATCH_SIZE))))
    except Exception:
        return DEFAULT_BATCH_SIZE


def log(msg):
    ts   = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{ts}] {msg}"
    print(line, flush=True)
    try:
        LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass


def load_state():
    if STATE_FILE.exists():
        try:
            return json.loads(STATE_FILE.read_text())
        except Exception:
            pass
    return {"joined": [], "failed": {}, "started_at": datetime.now().isoformat()}


def save_state(state):
    try:
        STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
        STATE_FILE.write_text(json.dumps(state, indent=2, ensure_ascii=False))
    except Exception as e:
        log(f"⚠️ save_state: {e}")


async def try_join(phone):
    """تلاش برای join یک اکانت"""
    ss = db.get_session(phone)
    if not ss:
        return phone, "NO_SESSION"

    c = make_client(ss, phone=phone)
    try:
        await asyncio.wait_for(c.connect(), timeout=25)
        if not await c.is_user_authorized():
            await c.disconnect()
            return phone, "NOT_AUTH"

        try:
            await asyncio.wait_for(c.get_entity(TARGET_USERNAME), timeout=10)
            await c.disconnect()
            return phone, "ALREADY_MEMBER"
        except Exception:
            pass

        try:
            await asyncio.wait_for(
                c(functions.channels.JoinChannelRequest(channel=TARGET_USERNAME)),
                timeout=20
            )
            await asyncio.sleep(1)
            await c.disconnect()
            return phone, "JOINED"
        except UserAlreadyParticipantError:
            await c.disconnect()
            return phone, "ALREADY_MEMBER"
        except UserBannedInChannelError:
            await c.disconnect()
            return phone, "BANNED"
        except ChannelPrivateError:
            await c.disconnect()
            return phone, "PRIVATE"
        except InviteRequestSentError:
            await c.disconnect()
            return phone, "REQUEST_SENT"
        except FloodWaitError as e:
            await c.disconnect()
            return phone, f"FLOOD:{e.seconds}"
        except Exception as e:
            await c.disconnect()
            return phone, f"ERR:{type(e).__name__}"

    except Exception as e:
        try:
            await c.disconnect()
        except Exception:
            pass
        return phone, f"OUTER:{type(e).__name__}"


def pick_batch(state, batch_size):
    """انتخاب batch_size اکانت بعدی که join نشدن"""
    db.init_db()
    accounts   = db.get_all_accounts()
    joined_set = set(state.get("joined", []))
    failed_map = state.get("failed", {})
    batch      = []

    for acc in accounts:
        if len(batch) >= batch_size:
            break
        phone = acc["phone"]
        if acc.get("status") == "dead":
            continue
        if phone in joined_set:
            continue
        if failed_map.get(phone, 0) >= 3:
            continue
        batch.append(phone)

    return batch


async def run_batch(batch, state, total_accounts):
    """اجرای موازی join برای همه اکانت‌های یک دور"""
    tasks   = [try_join(phone) for phone in batch]
    results = await asyncio.gather(*tasks, return_exceptions=True)

    for item in results:
        if isinstance(item, Exception):
            log(f"   ❌ خطای کلی: {item}")
            continue

        phone, result = item
        log(f"   [{phone}] نتیجه: {result}")

        if result in ("JOINED", "ALREADY_MEMBER"):
            if phone not in state["joined"]:
                state["joined"].append(phone)
            state.get("failed", {}).pop(phone, None)
        elif result.startswith("FLOOD:"):
            sec = int(result.split(":")[1])
            log(f"   🟡 {phone} FloodWait {sec}s")
        else:
            failed = state.setdefault("failed", {})
            failed[phone] = failed.get(phone, 0) + 1

    save_state(state)
    log(f"   📊 موفق: {len(state['joined'])}/{total_accounts}")


async def main():
    db.init_db()
    state = load_state()

    interval_sec = get_interval_sec()
    batch_size   = get_batch_size()
    total        = len(db.get_all_accounts())

    log("═" * 60)
    log(f"🚀 Slow Joiner شروع — هر {interval_sec // 60} دقیقه | هر دور {batch_size} اکانت")
    log(f"📢 Target: @{TARGET_USERNAME}")
    log(f"📊 کل اکانت‌ها: {total}")
    log(f"✅ قبلاً join شده: {len(state.get('joined', []))}")
    log("═" * 60)

    cycle = 0
    while True:
        cycle += 1

        # هر دور تنظیمات رو دوباره از DB بخون (قابل تغییر در حین اجرا)
        interval_sec = get_interval_sec()
        batch_size   = get_batch_size()
        total        = len(db.get_all_accounts())

        try:
            batch = pick_batch(state, batch_size)

            if not batch:
                log("🎉 همه اکانت‌ها join شدن یا fail کردن! تموم شد.")
                log(f"   موفق: {len(state.get('joined', []))}")
                log(f"   fail: {len(state.get('failed', {}))}")
                break

            log(f"▶️ دور {cycle} — {len(batch)} اکانت")
            await run_batch(batch, state, total)

        except Exception as e:
            log(f"❌ چرخه {cycle}: {type(e).__name__}: {e}")

        log(f"   💤 صبر {interval_sec}s ({interval_sec // 60} دقیقه)...")
        await asyncio.sleep(interval_sec)


if __name__ == "__main__":
    asyncio.run(main())

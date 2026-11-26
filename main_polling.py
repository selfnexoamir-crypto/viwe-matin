"""نقطه ورود ربات — aiogram 3  حالت Polling (بدون نیاز به webhook/server)"""
import asyncio
import logging
import sys
from pathlib import Path

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.fsm.storage.memory import MemoryStorage

sys.path.insert(0, str(Path(__file__).resolve().parent))
from config import Config
from models import database as db
from bot.handlers import register_handlers

logging.basicConfig(
    level=getattr(logging, Config.LOG_LEVEL, logging.INFO),
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    handlers=[
        logging.FileHandler(Config.LOG_DIR / "bot.log", encoding="utf-8"),
        logging.StreamHandler(),
    ],
)
log = logging.getLogger("master_bot")

bot = Bot(
    token=Config.BOT_TOKEN,
    default=DefaultBotProperties(parse_mode=ParseMode.HTML),
)
storage = MemoryStorage()
dp      = Dispatcher(storage=storage)
register_handlers(dp)

_bg_tasks: set = set()


# ── watchdog: Group Listener ───────────────────────────
async def group_listener_watchdog():
    """هر ۳۰ ثانیه: اگه گروه تنظیم شده و ویو فعاله ولی لیسنر نمی‌چرخه، روشنش می‌کنه."""
    from core.listener import ensure_group_listener
    await asyncio.sleep(25)
    while True:
        try:
            gid  = await asyncio.to_thread(db.get_target_group)
            auto = await asyncio.to_thread(db.get_setting, "auto_view_enabled", "1")
            if gid and auto == "1":
                await ensure_group_listener()
        except Exception as e:
            log.exception(f"group watchdog: {e}")
        await asyncio.sleep(30)


# ── boot در پس‌زمینه ─────────────────────────────────
async def boot_pool_and_listener():
    from core.pool import POOL
    try:
        log.info("🔌 اتصال همه اکانت‌ها...")
        await POOL.init()
        log.info(
            f"✅ Pool: {len(POOL.clients)}/{len(POOL.all_phones)} | "
            + " ".join(f"S{k}:{len(v)}" for k, v in POOL.shifts.items())
        )
    except Exception as e:
        log.exception(f"POOL.init: {e}")
    finally:
        POOL.ready.set()

    group_id  = db.get_target_group()
    auto_view = db.get_setting("auto_view_enabled", "1") == "1"

    if auto_view and group_id:
        try:
            from core.listener import ensure_group_listener
            await ensure_group_listener()
            log.info(f"👥 Group Listener روشن — group={db.fmt_group(group_id)}")
        except Exception as e:
            log.exception(f"Group Listener: {e}")
    else:
        log.warning("⚠️ گروه هدف تنظیم نشده — ویو غیرفعال (بعد از تنظیم گروه، خودکار روشن میشه)")

    t = asyncio.create_task(group_listener_watchdog())
    _bg_tasks.add(t)
    t.add_done_callback(_bg_tasks.discard)

    mn = db.get_setting("shift_min_sec", Config.SHIFT_MIN_SEC)
    mx = db.get_setting("shift_max_sec", Config.SHIFT_MAX_SEC)
    for admin_id in Config.ADMIN_IDS:
        try:
            await bot.send_message(
                admin_id,
                f"🚀 <b>{Config.BOT_NAME}</b> روشن شد! (Polling)\n"
                f"👥 اکانت: {len(POOL.clients)}/{len(POOL.all_phones)}\n"
                f"🎯 گروه هدف: <code>{db.fmt_group(group_id)}</code>\n"
                f"⏱️ شیفت: {int(mn)//60}-{int(mx)//60} دقیقه تصادفی\n"
                f"✅ شیفت فعال: {POOL.active_shift}\n"
                f"📡 حالت: Polling",
            )
        except Exception as e:
            log.warning(f"admin {admin_id}: {e}")
    log.info("✅ ربات آماده است (Polling)")


# ── startup ────────────────────────────────────────────
async def on_startup():
    errs = Config.validate_polling()
    if errs:
        for e in errs:
            log.error(f"  - {e}")
        raise SystemExit(1)

    db.init_db()
    db.migrate_shifts()
    db.debug_log("main", "startup", "OK", f"{Config.BOT_NAME} [polling]")

    # در polling نیازی به webhook نیست — مطمئن میشیم webhook قدیمی پاک شده
    try:
        await bot.delete_webhook(drop_pending_updates=True)
        log.info("🧹 Webhook قبلی پاک شد (اگه بود)")
    except Exception as e:
        log.warning(f"delete_webhook: {e}")

    asyncio.create_task(boot_pool_and_listener())


# ── shutdown ───────────────────────────────────────────
async def on_shutdown():
    log.info("🛑 خاموش کردن...")
    for t in list(_bg_tasks):
        t.cancel()

    from core.listener import get_group_listener
    obj = get_group_listener()
    if obj:
        try:
            await asyncio.wait_for(obj.stop(), timeout=10)
        except Exception:
            pass

    try:
        from core.pool import POOL
        await asyncio.wait_for(POOL.close_all(), timeout=15)
    except Exception:
        pass

    try:
        db.debug_log("main", "shutdown", "OK", "خاموش [polling]")
    except Exception:
        pass

    await bot.session.close()
    log.info("🛑 ربات خاموش شد")


# ── main ───────────────────────────────────────────────
async def main():
    dp.startup.register(on_startup)
    dp.shutdown.register(on_shutdown)

    print(f"""
╔══════════════════════════════════════╗
║      🤖 {Config.BOT_NAME}
║      ادمین‌ها: {Config.ADMIN_IDS}
║      حالت: Polling
╚══════════════════════════════════════╝
    """)

    await dp.start_polling(
        bot,
        allowed_updates=dp.resolve_used_update_types(),
    )


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        log.info("🛑 توقف با Ctrl+C")

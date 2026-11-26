"""نقطه ورود ربات — aiogram 3  حالت Polling"""
import asyncio
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from config import Config
from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.fsm.storage.memory import MemoryStorage
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

bot     = Bot(token=Config.BOT_TOKEN,
              default=DefaultBotProperties(parse_mode=ParseMode.HTML))
storage = MemoryStorage()
dp      = Dispatcher(storage=storage)
register_handlers(dp)

_bg_tasks: set = set()


async def group_listener_watchdog():
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


async def fj_scheduler_loop():
    """هر interval یه batch از اکانت‌ها رو به ربات جوین تو جوین پیام می‌فرسته"""
    import re as _re
    from core.engine import make_client
    from core.entity import resolve_group_entity
    from telethon.tl.functions.channels import JoinChannelRequest

    FJ_BOT_USERNAME = "vornixbazarbot"

    await asyncio.sleep(30)  # صبر می‌کنه pool init بشه

    batch_index = 0  # ایندکس چرخشی برای انتخاب batch بعدی

    while True:
        try:
            enabled      = db.get_setting("fj_enabled", "0") == "1"
            interval_min = max(1, int(db.get_setting("fj_join_interval_min", "10")))
            batch_size   = max(1, int(db.get_setting("fj_batch_size", "5")))
            interval_sec = interval_min * 60

            if not enabled:
                await asyncio.sleep(30)
                continue

            group_id = db.get_target_group()
            if not group_id:
                log.warning("fj_scheduler: گروه هدف تنظیم نشده")
                await asyncio.sleep(interval_sec)
                continue

            accounts = db.get_all_accounts()
            active   = [a for a in accounts if a.get("status") != "dead"]
            if not active:
                await asyncio.sleep(interval_sec)
                continue

            # انتخاب batch چرخشی
            start = batch_index % len(active)
            batch = (active + active)[start: start + batch_size]
            batch_index = (start + batch_size) % len(active)

            log.info(f"fj_scheduler: دور جدید — {len(batch)} اکانت | هر {interval_min} دقیقه")

            leave_days   = int(db.get_setting("fj_leave_days", "1"))

            for acc in batch:
                phone  = acc["phone"]
                client = None
                try:
                    ss = db.get_session(phone)
                    if not ss:
                        continue
                    client = make_client(ss, phone=phone)
                    await asyncio.wait_for(client.connect(), timeout=20)
                    if not await client.is_user_authorized():
                        await client.disconnect()
                        continue

                    entity   = await resolve_group_entity(client, group_id, timeout=15)
                    sent_msg = await client.send_message(entity, ".")
                    await asyncio.sleep(3)

                    bot_msg = None
                    async for message in client.iter_messages(entity, limit=10):
                        sender   = getattr(message, "sender", None)
                        username = getattr(sender, "username", "") or ""
                        if username.lower() == FJ_BOT_USERNAME and message.buttons:
                            bot_msg = message
                            break

                    if bot_msg and bot_msg.buttons:
                        import json, time
                        joined_channels = []
                        for row in bot_msg.buttons:
                            for btn in row:
                                url = getattr(btn, "url", None)
                                if url and "t.me" in url:
                                    try:
                                        ch_entity = await client.get_entity(url)
                                        await client(JoinChannelRequest(ch_entity))
                                        joined_channels.append(url)
                                        await asyncio.sleep(2)
                                    except Exception:
                                        pass

                        if joined_channels:
                            leave_at = time.time() + leave_days * 86400
                            existing = db.get_setting("fj_pending_leaves", "[]")
                            try:
                                pending = json.loads(existing)
                            except Exception:
                                pending = []
                            for ch in joined_channels:
                                pending.append({"phone": phone, "channel": ch, "leave_at": leave_at})
                            db.set_setting("fj_pending_leaves", json.dumps(pending))

                    # پاک کردن پیام ارسالی
                    try:
                        await client.delete_messages(entity, [sent_msg.id])
                    except Exception:
                        pass

                except Exception as e:
                    log.warning(f"fj_scheduler [{phone}]: {type(e).__name__}: {e}")
                finally:
                    if client:
                        try:
                            await client.disconnect()
                        except Exception:
                            pass
                    await asyncio.sleep(2)

        except Exception as e:
            log.exception(f"fj_scheduler outer: {e}")

        interval_sec = max(1, int(db.get_setting("fj_join_interval_min", "10"))) * 60
        log.info(f"fj_scheduler: صبر {interval_sec // 60} دقیقه...")
        await asyncio.sleep(interval_sec)


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
        log.warning("⚠️ گروه هدف تنظیم نشده — ویو غیرفعال")

    t = asyncio.create_task(group_listener_watchdog())
    _bg_tasks.add(t)
    t.add_done_callback(_bg_tasks.discard)

    fj = asyncio.create_task(fj_scheduler_loop())
    _bg_tasks.add(fj)
    fj.add_done_callback(_bg_tasks.discard)

    mn = db.get_setting("shift_min_sec", Config.SHIFT_MIN_SEC)
    mx = db.get_setting("shift_max_sec", Config.SHIFT_MAX_SEC)
    for admin_id in Config.ADMIN_IDS:
        try:
            await bot.send_message(
                admin_id,
                f"🚀 <b>{Config.BOT_NAME}</b> روشن شد!\n"
                f"👥 اکانت: {len(POOL.clients)}/{len(POOL.all_phones)}\n"
                f"🎯 گروه هدف: <code>{db.fmt_group(group_id)}</code>\n"
                f"⏱️ شیفت: {int(mn)//60}-{int(mx)//60} دقیقه\n"
                f"✅ شیفت فعال: {POOL.active_shift}\n"
                f"📡 حالت: Polling",
            )
        except Exception as e:
            log.warning(f"admin {admin_id}: {e}")
    log.info("✅ ربات آماده است")


async def on_startup():
    errs = Config.validate_polling()
    if errs:
        for e in errs:
            log.error(f"  config error: {e}")
        raise SystemExit(1)

    db.init_db()
    db.migrate_shifts()
    db.debug_log("main", "startup", "OK", f"{Config.BOT_NAME} [polling]")

    try:
        await bot.delete_webhook(drop_pending_updates=True)
        log.info("🧹 Webhook قبلی پاک شد")
    except Exception as e:
        log.warning(f"delete_webhook: {e}")

    asyncio.create_task(boot_pool_and_listener())


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

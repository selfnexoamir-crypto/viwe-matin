"""هندلرهای کامل ربات — aiogram 3"""
import asyncio
import logging
import sys
from datetime import datetime
from pathlib import Path

log = logging.getLogger(__name__)

from aiogram import types, Router, F, Bot
from aiogram.filters import Command, StateFilter
from aiogram.fsm.context import FSMContext
from telethon import TelegramClient
from telethon.sessions import StringSession
from telethon.errors import (
    SessionPasswordNeededError, FloodWaitError,
    PhoneMigrateError, NetworkMigrateError, UserMigrateError,
    PhoneCodeInvalidError, PhoneCodeExpiredError, PhoneCodeEmptyError,
    PhoneNumberInvalidError, PhoneNumberBannedError, PhoneNumberFloodError,
    PhoneNumberUnoccupiedError,
    AuthRestartError, AuthKeyError, SessionRevokedError,
    ApiIdInvalidError,
)

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from config import Config
from models import database as db
from bot.keyboards import (
    main_menu, status_menu, accounts_menu, accounts_list_kb,
    stats_menu, settings_menu, group_menu, confirm_menu,
    cancel_menu, view_toggle_menu, shift_select_menu, shift_timing_menu,
    use_members_menu, num_shifts_menu,
    forced_join_menu, forced_join_settings_menu,
    add_method_menu, smart_scheduler_menu,
    fixed_channel_confirm_menu, fixed_channel_actions_menu,
    fixed_channels_list_menu,
)
from bot.states import AddAccount, SetShiftTiming, SetGroup, UseMembers, StartBotWizard, FixedChannel

router = Router()

_pending: dict        = {}
_pending_clients      = _pending


# ─────────────────────────────────────────────────────────
# helpers
# ─────────────────────────────────────────────────────────
def is_admin(uid: int) -> bool:
    return uid in Config.ADMIN_IDS


async def guard(msg: types.Message) -> bool:
    if not is_admin(msg.from_user.id):
        await msg.answer("⛔ دسترسی ندارید.")
        return False
    return True


guard_admin = guard


_FA_DIGITS = str.maketrans("۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩", "01234567890123456789")


def _normalize_phone(raw: str) -> str | None:
    """
    تبدیل هر فرمت شماره به E.164 — پشتیبانی از همه کشورها:
    - ارقام فارسی/عربی
    - فاصله، خط‌تیره، پرانتز، نقطه (مثل +1 (555) 123-4567)
    - پیشوند 00xx، 09xx، 98xx (ایران)
    - شماره‌های بدون + که ۱۰+ رقمی هستن
    """
    # ترجمه ارقام فارسی/عربی
    s = raw.translate(_FA_DIGITS)
    # حذف کاراکترهای غیرضروری (پرانتز، خط‌تیره، فاصله، نقطه)
    s = "".join(ch for ch in s if ch.isdigit() or ch == "+")
    # حذف + تکراری (مثل ++98)
    if s.count("+") > 1:
        s = "+" + s.replace("+", "")
    # تبدیل پیشوندها
    if s.startswith("00"):
        s = "+" + s[2:]
    elif s.startswith("09") and 10 <= len(s) <= 11:
        s = "+98" + s[1:]
    elif s.startswith("989") and len(s) == 12:
        s = "+" + s
    elif s.startswith("98") and len(s) == 12:
        s = "+" + s
    elif not s.startswith("+") and s.isdigit() and len(s) >= 10:
        s = "+" + s
    # اعتبارسنجی E.164: + و ۷ تا ۱۵ رقم
    digits = s[1:] if s.startswith("+") else ""
    if not digits or not digits.isdigit() or not (7 <= len(digits) <= 15):
        return None
    return s


def _get_group_id():
    """یوزرنیم (str) یا آیدی عددی (int) یا 0"""
    return db.get_target_group()


# ─────────────────────────────────────────────────────────
# /start  /help  /admin
# ─────────────────────────────────────────────────────────
@router.message(Command("start"))
async def cmd_start(msg: types.Message):
    if not await guard(msg): return
    total    = len(db.get_all_accounts())
    group_id = _get_group_id()
    mn       = int(db.get_setting("shift_min_sec", Config.SHIFT_MIN_SEC)) // 60
    mx       = int(db.get_setting("shift_max_sec", Config.SHIFT_MAX_SEC)) // 60
    await msg.answer(
        f"🌟 <b>{Config.BOT_NAME}</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n\n"
        f"👋 سلام!\n"
        f"🤖 این ربات سین‌زن حرفه‌ای شماست\n\n"
        f"📊 وضعیت سریع:\n"
        f"  👥 اکانت: <b>{total}</b>\n"
        f"  🎯 گروه: <code>{db.fmt_group(group_id)}</code>\n"
        f"  ⏰ چرخش: هر {mn}-{mx} دقیقه\n\n"
        f"👇 از منو انتخاب کنید:",
        reply_markup=main_menu(),
        parse_mode="HTML",
    )


@router.message(Command("help"))
@router.message(F.text.startswith("❓"))
async def cmd_help(msg: types.Message):
    if not await guard(msg): return
    await msg.answer(
        "📖 <b>راهنمای استفاده</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n\n"
        "➕ <b>افزودن اکانت</b>: شماره رو وارد کن، کد رو بده\n"
        "📊 <b>وضعیت زنده</b>: تعداد آنلاین/Flood/خطا\n"
        "👥 <b>مدیریت اکانت‌ها</b>: لیست، افزودن، چرخش، حذف\n"
        "📈 <b>آمار پیشرفته</b>: آمار ۲۴ ساعت، نمودار موفقیت\n"
        "🔄 <b>چرخش دستی</b>: تعویض فوری شیفت\n"
        "🎯 <b>گروه هدف</b>: تنظیم گروه، عضویت، چک\n"
        "⚙️ <b>تنظیمات</b>: بازه شیفت، شیفت فعال، نگهبان\n\n"
        "💡 <b>نکات</b>:\n"
        "• بین هر اکانت ۳-۵ دقیقه صبر کن\n"
        "• اگه Flood دیدی، ۱۰ دقیقه صبر کن",
        reply_markup=main_menu(),
        parse_mode="HTML",
    )


@router.message(Command("admin"))
async def cmd_admin(msg: types.Message):
    if not await guard(msg): return
    await msg.answer("🔧 <b>پنل ادمین</b>\n\nهمه دسترسی‌ها فعاله.", parse_mode="HTML")


# ─────────────────────────────────────────────────────────
# وضعیت زنده
# ─────────────────────────────────────────────────────────
def _status_text() -> str:
    counts = db.count_by_status()
    total  = len(db.get_all_accounts())
    online = counts.get("online", 0)
    flood  = counts.get("flood",  0)
    err    = counts.get("error",  0)
    idle   = counts.get("idle",   0)

    def bar(n, mx=total):
        if mx == 0: return "▱" * 10
        return "▰" * int(n / mx * 10) + "▱" * (10 - int(n / mx * 10))

    now = datetime.now().strftime("%H:%M:%S")
    return (
        f"📊 <b>وضعیت زنده</b>  <code>{now}</code>\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n\n"
        f"🟢 آنلاین  {bar(online)}  <b>{online}</b>\n"
        f"💤 استراحت  {bar(idle)}  <b>{idle}</b>\n"
        f"🟡 Flood  {bar(flood)}  <b>{flood}</b>\n"
        f"🔴 خطا  {bar(err)}  <b>{err}</b>\n\n"
        f"📊 کل: <b>{total}</b> اکانت"
    )


@router.message(F.text.startswith("📊"))
async def show_status(msg: types.Message):
    if not await guard(msg): return
    await msg.answer(_status_text(), reply_markup=status_menu(), parse_mode="HTML")


# ─────────────────────────────────────────────────────────
# کنترل ویو
# ─────────────────────────────────────────────────────────
@router.message(F.text.startswith("🎬"))
async def show_view_toggle(msg: types.Message):
    if not await guard(msg): return
    from core.listener import is_view_paused
    paused     = is_view_paused()
    status_txt = "🔴 متوقف" if paused else "🟢 فعال"
    await msg.answer(
        f"🎬 <b>کنترل ویو زدن</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n\n"
        f"وضعیت فعلی: <b>{status_txt}</b>\n",
        reply_markup=view_toggle_menu(paused),
        parse_mode="HTML",
    )


@router.callback_query(F.data.startswith("view:"))
async def cb_view_toggle(cb: types.CallbackQuery):
    from core.listener import set_view_paused, is_view_paused
    action = cb.data.split(":")[1]
    if action == "pause":
        set_view_paused(True)
        await cb.answer("⏸️ ویو متوقف شد", show_alert=True)
    elif action == "resume":
        set_view_paused(False)
        await cb.answer("▶️ ویو روشن شد", show_alert=True)
    else:
        await cb.answer("🔄 بروزرسانی")

    paused     = is_view_paused()
    status_txt = "🔴 متوقف" if paused else "🟢 فعال"
    try:
        await cb.message.edit_text(
            f"🎬 <b>کنترل ویو زدن</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━━━\n\n"
            f"وضعیت فعلی: <b>{status_txt}</b>\n",
            reply_markup=view_toggle_menu(paused),
            parse_mode="HTML",
        )
    except Exception:
        pass


# ─────────────────────────────────────────────────────────
# مدیریت اکانت‌ها
# ─────────────────────────────────────────────────────────
@router.message(F.text.startswith("👥"))
async def show_accounts(msg: types.Message):
    if not await guard(msg): return
    total  = len(db.get_all_accounts())
    online = db.count_by_status().get("online", 0)
    await msg.answer(
        f"👥 <b>مدیریت اکانت‌ها</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n\n"
        f"📊 تعداد: <b>{total}</b>\n"
        f"🟢 آنلاین: <b>{online}</b>\n\n"
        f"👇 گزینه مورد نظر:",
        reply_markup=accounts_menu(),
        parse_mode="HTML",
    )


# ─────────────────────────────────────────────────────────
# آمار
# ─────────────────────────────────────────────────────────
@router.message(F.text.startswith("📈"))
async def show_stats(msg: types.Message):
    if not await guard(msg): return
    await msg.answer(
        "📈 <b>آمار پیشرفته</b>\n━━━━━━━━━━━━━━━━━━━━━━\n\n👇 گزینه مورد نظر:",
        reply_markup=stats_menu(), parse_mode="HTML",
    )


# ─────────────────────────────────────────────────────────
# 🎯 گروه هدف
# ─────────────────────────────────────────────────────────
@router.message(F.text.startswith("🎯"))
async def show_group(msg: types.Message):
    if not await guard(msg): return
    group_id = _get_group_id()
    await msg.answer(
        f"🎯 <b>گروه هدف</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n\n"
        f"🆔 آیدی: <code>{db.fmt_group(group_id)}</code>\n\n"
        f"👇 گزینه مورد نظر:",
        reply_markup=group_menu(),
        parse_mode="HTML",
    )


@router.callback_query(F.data == "grp:set")
async def cb_group_set(cb: types.CallbackQuery, state: FSMContext):
    await cb.message.edit_text(
        "🎯 <b>تنظیم گروه هدف</b>\n\n"
        "یوزرنیم یا لینک گروه رو بفرست (پیشنهادی):\n"
        "<code>@mygroup</code>\n"
        "<code>https://t.me/mygroup</code>\n\n"
        "یا آیدی عددی:\n"
        "<code>-1001234567890</code>\n\n"
        "💡 فقط گروه/کانال <b>عمومی</b> یوزرنیم داره. لینک دعوت خصوصی (t.me/+...) پشتیبانی نمیشه.",
        reply_markup=cancel_menu(),
        parse_mode="HTML",
    )
    await state.set_state(SetGroup.waiting_id)
    await cb.answer()


@router.message(StateFilter(SetGroup.waiting_id))
async def set_group_id(msg: types.Message, state: FSMContext):
    if not await guard(msg): return
    raw = (msg.text or "").strip()
    ref = db.parse_group_ref(raw)
    if not ref:
        if "joinchat" in raw or "t.me/+" in raw:
            await msg.answer("❌ لینک دعوت خصوصی پشتیبانی نمیشه. یوزرنیم عمومی (<code>@mygroup</code>) یا آیدی عددی بفرست.",
                             parse_mode="HTML")
        else:
            await msg.answer("❌ فرمت اشتباه. مثال: <code>@mygroup</code> یا <code>https://t.me/mygroup</code> یا <code>-1001234567890</code>",
                             parse_mode="HTML")
        return

    db.set_setting("target_group_id", ref)
    text = (f"✅ گروه هدف تنظیم شد!\n🆔 <code>{db.fmt_group(ref)}</code>\n")
    if isinstance(ref, int) and raw.lstrip("-").isdigit() and int(raw) != ref:
        text += f"🔧 منفی/پیشوند اصلاح شد: <code>{raw}</code> ← <code>{ref}</code>\n"

    # اعتبارسنجی فوری با یکی از اکانت‌های وصل
    try:
        from core.pool import POOL
        from core.entity import resolve_group_entity
        from telethon.tl.types import User
        client = next((c for c in POOL.clients.values() if c.is_connected()), None)
        if client is None:
            text += "\n⚠️ هنوز اکانتی وصل نیست، تأیید نشد."
        else:
            ent = await resolve_group_entity(client, ref, timeout=15)
            if isinstance(ent, User):
                text += "\n⚠️ این یوزرنیم مال یه <b>کاربره</b>، نه گروه!"
            else:
                text += f"\n🔎 تأیید شد: <b>{getattr(ent, 'title', '?')}</b>"
                text += "\n💡 اکانت‌ها باید عضو گروه باشن: «عضویت همه اکانت‌ها» رو بزن."
    except Exception as e:
        text += f"\n⚠️ ذخیره شد ولی گروه پیدا نشد:\n<code>{str(e)[:150]}</code>"

    try:
        from core.listener import ensure_group_listener
        gl = await ensure_group_listener()
        gl._settings_ts = 0.0          # تنظیمات رو سریع دوباره بخونه
        text += "\n\n✅ لیسنر روشنه و گروه جدید رو برمی‌داره."
    except Exception as e:
        text += f"\n\n⚠️ لیسنر روشن نشد: <code>{str(e)[:100]}</code>"
    await msg.answer(text, reply_markup=main_menu(), parse_mode="HTML")
    await state.clear()


@router.callback_query(F.data.startswith("grp:"))
async def cb_group(cb: types.CallbackQuery):
    data     = cb.data.split(":")[1]
    group_id = _get_group_id()

    if data == "join":
        await cb.answer("⏳ در حال عضویت همه اکانت‌ها...")
        asyncio.create_task(_join_group_all_task(cb, group_id))

    elif data == "check":
        await cb.answer("⏳ در حال بررسی...")
        asyncio.create_task(_check_group_join_task(cb, group_id))

    elif data == "info":
        await cb.answer(f"🎯 گروه: {db.fmt_group(group_id)}", show_alert=True)

    elif data == "diag":
        await cb.answer()
        from core.listener import get_group_listener, ensure_group_listener
        gl = get_group_listener()
        if gl is None or not gl.is_running:
            if not group_id:
                await cb.message.answer("❌ گروه هدف تنظیم نشده.")
                return
            gl = await ensure_group_listener()
            await asyncio.sleep(3)
        await cb.message.answer(gl.diag_text(), parse_mode="HTML")

    elif data == "test":
        await cb.answer("⏳ تست روی آخرین پیام...")
        from core.listener import get_group_listener, ensure_group_listener
        gl = get_group_listener() or await ensure_group_listener()
        try:
            txt = await asyncio.wait_for(gl.test_round(), timeout=60)
        except Exception as e:
            txt = f"❌ تست شکست خورد:\n<code>{type(e).__name__}: {str(e)[:200]}</code>"
        await cb.message.answer(txt, parse_mode="HTML")

    elif data == "mode":
        from core.listener import get_group_listener, _MODES, _MODE_FA
        cur = db.get_setting("group_action", "both")
        nxt = _MODES[(_MODES.index(cur) + 1) % len(_MODES)] if cur in _MODES else "both"
        db.set_setting("group_action", nxt)
        gl = get_group_listener()
        if gl:
            gl.mode = nxt
        await cb.answer(f"⚙️ عملیات: {_MODE_FA[nxt]}", show_alert=True)

    try:
        await cb.answer()
    except Exception:
        pass


async def _group_members_task(cb: types.CallbackQuery, group_id, mode: str):
    """mode='join' عضو می‌کنه، mode='check' فقط عضویت رو چک می‌کنه. دلیل شکست‌ها رو گزارش می‌ده."""
    if not group_id:
        await cb.message.answer("❌ گروه هدف تنظیم نشده")
        return
    from collections import Counter
    from core.engine import make_client
    from core.entity import resolve_group_entity
    from telethon.tl.functions.channels import JoinChannelRequest, GetParticipantRequest
    try:
        accounts = db.get_all_accounts()
        ok, reasons = 0, Counter()
        for a in accounts:
            c = None
            try:
                c = make_client(db.get_session(a["phone"]), phone=a["phone"])
                await asyncio.wait_for(c.connect(), timeout=15)
                entity = await resolve_group_entity(c, group_id, timeout=15)
                if mode == "join":
                    await c(JoinChannelRequest(entity))
                else:
                    await c(GetParticipantRequest(channel=entity, participant="me"))
                ok += 1
                if mode == "join":
                    await asyncio.sleep(2)
            except Exception as e:
                msg_ = str(e).lower()
                if mode == "join" and ("already" in msg_ or "participant" in msg_):
                    ok += 1
                else:
                    reasons[f"{type(e).__name__}: {str(e)[:70]}"] += 1
            finally:
                if c is not None:
                    try:
                        await c.disconnect()
                    except Exception:
                        pass
        label = "عضو گروه شدن" if mode == "join" else "عضو گروه"
        text = f"✅ {ok}/{len(accounts)} اکانت {label}"
        for reason, n in reasons.most_common(4):
            text += f"\n🔴 {n}× <code>{reason}</code>"
        await cb.message.answer(text, parse_mode="HTML")
    except Exception as e:
        await cb.message.answer(f"❌ {e}")


async def _join_group_all_task(cb: types.CallbackQuery, group_id):
    await _group_members_task(cb, group_id, "join")


async def _check_group_join_task(cb: types.CallbackQuery, group_id):
    await _group_members_task(cb, group_id, "check")


# ─────────────────────────────────────────────────────────
# ─────────────────────────────────────────────────────────
# 🤖 استفاده از ممبرها
# ─────────────────────────────────────────────────────────

@router.message(F.text.startswith("🤖 استفاده از ممبرها"))
async def show_use_members(msg: types.Message):
    if not await guard(msg): return
    total = len(db.get_all_accounts())
    await msg.answer(
        f"🤖 <b>استفاده از ممبرها</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n\n"
        f"👥 تعداد اکانت‌های موجود: <b>{total}</b>\n\n"
        f"یکی از عملیات‌ها رو انتخاب کن:",
        reply_markup=use_members_menu(),
        parse_mode="HTML",
    )


@router.callback_query(F.data.startswith("mem:"))
async def cb_use_members(cb: types.CallbackQuery, state: FSMContext):
    action = cb.data.split(":")[1]

    if action == "start_bot":
        await cb.answer()
        await _sbw_start(cb.message, state)

    elif action == "join_group":
        await cb.answer()
        await cb.message.answer(
            "👥 <b>عضو شدن توی گروه</b>\n"
            "━━━━━━━━━━━━━━━━━━━━━━\n\n"
            "لینک یا آیدی گروه/کانال رو بفرست:\n"
            "<code>https://t.me/groupusername</code>\n"
            "یا\n"
            "<code>@groupusername</code>\n"
            "یا آیدی عددی:\n"
            "<code>-100xxxxxxxxxx</code>",
            reply_markup=cancel_menu(),
            parse_mode="HTML",
        )
        await state.set_state(UseMembers.waiting_join_group_link)

    elif action == "forced_join":
        await cb.answer()
        enabled = db.get_setting("fj_enabled", "0") == "1"
        leave_days = int(db.get_setting("fj_leave_days", "1"))
        acc_count  = int(db.get_setting("fj_acc_count", "0"))
        total      = len(db.get_all_accounts())
        status_txt = "✅ فعال" if enabled else "❌ غیرفعال"
        await cb.message.answer(
            f"🔗 <b>جوین توی جوین اجباری</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━━━\n\n"
            f"وضعیت: <b>{status_txt}</b>\n"
            f"⏰ تایم ترک: <b>{leave_days} روز</b>\n"
            f"🔢 تعداد اکانت: <b>{'همه' if acc_count == 0 else acc_count}</b> از {total}\n\n"
            f"با کلیک روی «اجرای جوین اجباری» همه اکانت‌ها\n"
            f"توی گروه هدف پیام می‌دن، پیامشون حذف میشه\n"
            f"و ربات @vornixbazarbot پیام عضویت اجباری میده.\n"
            f"سپس اکانت‌ها عضو کانال‌های دکمه‌ای میشن و\n"
            f"بعد از {leave_days} روز لف میدن.\n\n"
            f"👇 گزینه مورد نظر:",
            reply_markup=forced_join_menu(enabled),
            parse_mode="HTML",
        )

    elif action == "fixed_channel":
        await cb.answer()
        await _show_fixed_channels_list(cb.message)

    elif action == "back":
        await cb.answer()
        total = len(db.get_all_accounts())
        await cb.message.answer(
            f"🤖 <b>استفاده از ممبرها</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━━━\n\n"
            f"👥 تعداد اکانت‌های موجود: <b>{total}</b>\n\n"
            f"یکی از عملیات‌ها رو انتخاب کن:",
            reply_markup=use_members_menu(),
            parse_mode="HTML",
        )

    try:
        await cb.answer()
    except Exception:
        pass


# ─────────────────────────────────────────────────────────
# 📌 کانال‌های ثابت (چندکاناله) — helpers
# ─────────────────────────────────────────────────────────

import json as _json

_ALL_REACTIONS = [
    "👍","👎","❤️","🔥","🥰","👏","😁","🤔","🤯","😱","🤬","😢","🎉","🤩",
    "🤮","💩","🙏","👌","🕊","🤡","🥱","🥴","😍","🐳","❤️‍🔥","🌚","🌭","💯",
    "🤣","⚡","🍌","🏆","💔","🤨","😐","🍓","🍾","💋","🖕","😈","😴","😭",
    "🤓","👻","👨‍💻","👀","🎃","🙈","😇","😨","🤝","✍️","🤗","🫡","🎅","🎄",
    "☃️","💅","🤪","🗿","🆒","💘","🙉","🦄","😘","💊","🙊","😎","👾","🤷",
    "🤷‍♂️","🤷‍♀️","😡",
]
_DEFAULT_REACTIONS = ["👍","❤️","🔥","🎉","😁","🤔"]


def _fch_load() -> list[dict]:
    """لیست کانال‌های ثابت را از DB می‌خواند — [{username, title, reactions}, ...]"""
    raw = db.get_setting("fixed_channels_v2", "")
    if not raw:
        return []
    try:
        return _json.loads(raw)
    except Exception:
        return []


def _fch_save(channels: list[dict]):
    """لیست کانال‌های ثابت را در DB ذخیره می‌کند"""
    db.set_setting("fixed_channels_v2", _json.dumps(channels, ensure_ascii=False))


def _fch_add(username: str, title: str, reactions: list[str]):
    """کانال جدید اضافه می‌کند (اگر قبلاً نبود)"""
    channels = _fch_load()
    for ch in channels:
        if ch["username"] == username:
            ch["title"] = title
            ch["reactions"] = reactions
            _fch_save(channels)
            return
    channels.append({"username": username, "title": title, "reactions": reactions})
    _fch_save(channels)


def _fch_remove(username: str):
    """کانال را از لیست حذف می‌کند"""
    channels = [ch for ch in _fch_load() if ch["username"] != username]
    _fch_save(channels)


async def _probe_with_any_account(task_coro_factory, accounts: list, timeout: int = 20):
    """
    روی اکانت‌ها به‌ترتیب امتحان می‌کنه تا یکی موفق بشه.
    task_coro_factory(client) → باید یه coroutine برگردونه که نتیجه رو return کنه.
    اگه اکانت session مرده داشت (AuthKeyError, SessionRevoked, ...) رد می‌کنه.
    """
    from core.engine import make_client
    from telethon.errors import (
        AuthKeyError, SessionRevokedError, UserDeactivatedBanError,
        AuthKeyUnregisteredError,
    )

    dead_errors = (
        AuthKeyError, SessionRevokedError,
        UserDeactivatedBanError, AuthKeyUnregisteredError,
    )
    last_err = None
    for a in accounts:
        c = None
        try:
            c = make_client(db.get_session(a["phone"]), phone=a["phone"])
            await asyncio.wait_for(c.connect(), timeout=15)
            result = await asyncio.wait_for(task_coro_factory(c), timeout=timeout)
            return result
        except dead_errors as e:
            # session مرده — اکانت بعدی
            last_err = e
            db.debug_log("probe", a["phone"], "DEAD", str(e)[:80])
            continue
        except Exception as e:
            last_err = e
            break
        finally:
            if c:
                try: await c.disconnect()
                except Exception: pass

    raise last_err or Exception("هیچ اکانت سالمی پیدا نشد")


async def _show_fixed_channels_list(msg: types.Message):
    """نمایش لیست کانال‌های ثابت"""
    channels = _fch_load()
    count = len(channels)
    if count == 0:
        text = (
            "📌 <b>کانال‌های ثابت</b>\n"
            "━━━━━━━━━━━━━━━━━━━━━━\n\n"
            "هنوز هیچ کانالی ثبت نشده.\n\n"
            "با دکمه زیر کانال اضافه کن:"
        )
    else:
        lines = "\n".join(
            f"  • <code>@{ch['username']}</code> — {ch.get('title', ch['username'])}"
            for ch in channels
        )
        text = (
            f"📌 <b>کانال‌های ثابت ({count} کانال)</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━━━\n\n"
            f"{lines}\n\n"
            f"▶️ = اجرا (جوین + ری‌اکشن)   🗑 = حذف"
        )
    await msg.answer(text, reply_markup=fixed_channels_list_menu(channels), parse_mode="HTML")


# ─────────────────────────────────────────────────────────
# 📌 کانال‌های ثابت — کال‌بک‌ها
# ─────────────────────────────────────────────────────────

@router.callback_query(F.data == "fch:list")
async def fch_list(cb: types.CallbackQuery):
    await cb.answer()
    await _show_fixed_channels_list(cb.message)


@router.callback_query(F.data == "fch:add")
async def fch_add(cb: types.CallbackQuery, state: FSMContext):
    await cb.answer()
    await cb.message.answer(
        "📌 <b>افزودن کانال ثابت</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n\n"
        "لینک یا یوزرنیم کانال رو بفرست:\n"
        "<code>https://t.me/channelusername</code>\n"
        "یا\n"
        "<code>@channelusername</code>",
        reply_markup=cancel_menu(),
        parse_mode="HTML",
    )
    await state.set_state(FixedChannel.waiting_link)


@router.message(StateFilter(FixedChannel.waiting_link))
async def fch_receive_link(msg: types.Message, state: FSMContext):
    """دریافت لینک کانال و نمایش مشخصات برای تأیید"""
    if not await guard(msg): return
    raw = (msg.text or "").strip()
    if not raw:
        await msg.answer("❌ لینک خالیه. دوباره بفرست یا انصراف بزن.")
        return

    import re
    channel_target = None
    link_match = re.match(r"(?:https?://)?t\.me/([A-Za-z0-9_]+)", raw)
    if link_match:
        channel_target = link_match.group(1)
    elif raw.startswith("@"):
        channel_target = raw[1:]
    elif raw.lstrip("-").isdigit():
        channel_target = raw
    else:
        channel_target = raw.lstrip("@")

    if not channel_target:
        await msg.answer("❌ لینک معتبر نیست. دوباره بفرست.")
        return

    await msg.answer("⏳ در حال دریافت اطلاعات کانال...")
    accounts = db.get_all_accounts()
    if not accounts:
        await msg.answer("❌ هیچ اکانتی موجود نیست.", reply_markup=cancel_menu())
        return

    channel_info = None
    try:
        async def _get_entity(c):
            entity = await asyncio.wait_for(c.get_entity(channel_target), timeout=15)
            return {
                "title":    getattr(entity, "title",                channel_target),
                "username": getattr(entity, "username", None) or   channel_target,
                "members":  getattr(entity, "participants_count",   "نامشخص"),
                "id":       getattr(entity, "id",                   "نامشخص"),
            }

        channel_info = await _probe_with_any_account(_get_entity, accounts, timeout=15)

    except Exception as e:
        await msg.answer(
            f"❌ نتونستم اطلاعات کانال رو بگیرم:\n<code>{e}</code>\n\nدوباره امتحان کن.",
            reply_markup=cancel_menu(),
            parse_mode="HTML",
        )
        return

    await state.update_data(channel_target=channel_target, channel_info=channel_info)
    await state.set_state(FixedChannel.waiting_confirm)

    info = channel_info
    await msg.answer(
        f"📌 <b>تأیید کانال</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n\n"
        f"📛 نام: <b>{info['title']}</b>\n"
        f"🔗 یوزرنیم: <code>@{info['username']}</code>\n"
        f"👥 اعضا: <b>{info['members']}</b>\n"
        f"🆔 آیدی: <code>{info['id']}</code>\n\n"
        f"آیا این کانال صحیح است؟",
        reply_markup=fixed_channel_confirm_menu(channel_target),
        parse_mode="HTML",
    )


@router.callback_query(F.data.startswith("fch:confirm:"))
async def fch_confirm(cb: types.CallbackQuery, state: FSMContext):
    """ذخیره کانال در لیست چندکاناله"""
    await cb.answer()
    channel_target = cb.data.split("fch:confirm:")[1]
    data = await state.get_data()
    info = data.get("channel_info", {})
    title = info.get("title", channel_target)
    await state.clear()

    # ذخیره بدون ری‌اکشن — هنگام اجرا دریافت می‌شه
    _fch_add(channel_target, title, [])

    channels = _fch_load()
    await cb.message.answer(
        f"✅ <b>کانال اضافه شد</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n\n"
        f"📛 {title}  (<code>@{channel_target}</code>)\n\n"
        f"اکنون {len(channels)} کانال ثابت دارید.",
        reply_markup=fixed_channels_list_menu(channels),
        parse_mode="HTML",
    )


@router.callback_query(F.data == "fch:reject")
async def fch_reject(cb: types.CallbackQuery, state: FSMContext):
    """رد کانال — برگشت به ورود لینک"""
    await cb.answer()
    await state.set_state(FixedChannel.waiting_link)
    await cb.message.answer(
        "📌 <b>افزودن کانال ثابت</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n\n"
        "لینک کانال جدید رو بفرست:\n"
        "<code>https://t.me/channelusername</code>",
        reply_markup=cancel_menu(),
        parse_mode="HTML",
    )


@router.callback_query(F.data.startswith("fch:del:"))
async def fch_del(cb: types.CallbackQuery):
    """حذف یک کانال از لیست"""
    await cb.answer()
    slug = cb.data.split("fch:del:")[1]
    _fch_remove(slug)
    channels = _fch_load()
    await cb.message.answer(
        f"🗑 <b>کانال <code>@{slug}</code> حذف شد.</b>\n\n"
        f"اکنون {len(channels)} کانال ثابت دارید.",
        reply_markup=fixed_channels_list_menu(channels),
        parse_mode="HTML",
    )


@router.callback_query(F.data.startswith("fch:run:"))
async def fch_run(cb: types.CallbackQuery):
    """اجرای جوین + ری‌اکشن روی یک کانال"""
    await cb.answer("⏳ شروع عملیات...", show_alert=True)
    channel_target = cb.data.split("fch:run:")[1]
    asyncio.create_task(_fixed_channel_run_task(cb.message, channel_target))


# ─────────────────────────────────────────────────────────
# 📌 کانال‌های ثابت — task اجرا
# ─────────────────────────────────────────────────────────

async def _fetch_channel_reactions(channel_target: str, accounts: list) -> list[str]:
    """ری‌اکشن‌های مجاز کانال را از Telegram دریافت می‌کند — با retry روی اکانت‌های سالم"""
    from telethon.tl.functions.channels import GetFullChannelRequest
    from telethon.tl.types import ChatReactionsSome, ChatReactionsAll

    async def _task(c):
        full = await asyncio.wait_for(c(GetFullChannelRequest(channel_target)), timeout=20)
        ar = getattr(full.full_chat, "available_reactions", None)
        if isinstance(ar, ChatReactionsSome):
            result = [r.emoticon for r in ar.reactions if hasattr(r, "emoticon")]
            return result if result else _DEFAULT_REACTIONS
        elif isinstance(ar, ChatReactionsAll):
            return _ALL_REACTIONS
        return _DEFAULT_REACTIONS

    try:
        return await _probe_with_any_account(_task, accounts, timeout=25)
    except Exception:
        return _DEFAULT_REACTIONS


async def _fixed_channel_run_task(msg: types.Message, channel_target: str):
    """
    برای یک کانال:
    ۱) جوین همه اکانت‌ها
    ۲) دریافت ری‌اکشن‌های مجاز
    ۳) ری‌اکشن تصادفی روی آخرین پست
    ۴) listener دائمی (اگه قبلاً راه‌اندازی نشده)
    """
    import random
    from core.engine import make_client
    from telethon.tl.functions.channels import JoinChannelRequest
    from telethon.tl.functions.messages import SendReactionRequest
    from telethon.tl.types import ReactionEmoji

    accounts = db.get_all_accounts()
    if not accounts:
        await msg.answer("❌ هیچ اکانتی موجود نیست.")
        return

    # ── مرحله ۱: جوین ──
    progress = await msg.answer(
        f"⏳ <b>[{channel_target}] مرحله ۱: عضویت</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n\n"
        f"0 / {len(accounts)} اکانت...",
        parse_mode="HTML",
    )
    ok_join = 0
    for idx, a in enumerate(accounts, 1):
        c = None
        try:
            c = make_client(db.get_session(a["phone"]), phone=a["phone"])
            await asyncio.wait_for(c.connect(), timeout=15)
            await asyncio.wait_for(c(JoinChannelRequest(channel_target)), timeout=20)
            ok_join += 1
        except Exception as e:
            if any(w in str(e).lower() for w in ("already", "participant")):
                ok_join += 1
        finally:
            if c:
                try: await c.disconnect()
                except Exception: pass
        if idx % 5 == 0 or idx == len(accounts):
            try:
                await progress.edit_text(
                    f"⏳ <b>[{channel_target}] مرحله ۱: عضویت</b>\n"
                    f"━━━━━━━━━━━━━━━━━━━━━━\n\n"
                    f"✅ {ok_join} / {idx} موفق",
                    parse_mode="HTML",
                )
            except Exception:
                pass
        if idx < len(accounts):
            await asyncio.sleep(random.uniform(2, 5))

    # ── مرحله ۲: ری‌اکشن‌های مجاز + آخرین پست ──
    await msg.answer(
        f"⏳ <b>[{channel_target}] مرحله ۲: دریافت ری‌اکشن‌ها...</b>",
        parse_mode="HTML",
    )
    allowed_reactions: list[str] = _DEFAULT_REACTIONS
    last_msg_id: int | None = None
    try:
        allowed_reactions = await _fetch_channel_reactions(channel_target, accounts)

        async def _get_last_msg(c):
            msgs_list = await asyncio.wait_for(c.get_messages(channel_target, limit=1), timeout=15)
            return msgs_list[0].id if msgs_list else None

        last_msg_id = await _probe_with_any_account(_get_last_msg, accounts, timeout=20)
    except Exception as e:
        db.debug_log("fixed_channel", "probe", "WARN", str(e))

    # ذخیره ری‌اکشن‌ها در لیست کانال‌ها
    channels = _fch_load()
    for ch in channels:
        if ch["username"] == channel_target:
            ch["reactions"] = allowed_reactions
    _fch_save(channels)

    reactions_text = "  ".join(allowed_reactions[:12])
    await msg.answer(
        f"✅ <b>ری‌اکشن‌های مجاز:</b>\n{reactions_text}",
        parse_mode="HTML",
    )

    # ── مرحله ۳: ری‌اکشن روی آخرین پست ──
    if last_msg_id:
        rp = await msg.answer(
            f"⏳ <b>[{channel_target}] مرحله ۳: ری‌اکشن (پست {last_msg_id})</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━━━\n\n"
            f"0 / {len(accounts)} اکانت...",
            parse_mode="HTML",
        )
        ok_react = 0
        for idx, a in enumerate(accounts, 1):
            c = None
            try:
                c = make_client(db.get_session(a["phone"]), phone=a["phone"])
                await asyncio.wait_for(c.connect(), timeout=15)
                await asyncio.wait_for(
                    c(SendReactionRequest(
                        peer=channel_target,
                        msg_id=last_msg_id,
                        reaction=[ReactionEmoji(emoticon=random.choice(allowed_reactions))],
                    )),
                    timeout=20,
                )
                ok_react += 1
            except Exception as e:
                db.debug_log("fixed_channel", "react", "WARN",
                             f"{a['phone']}: {type(e).__name__}: {str(e)[:60]}")
            finally:
                if c:
                    try: await c.disconnect()
                    except Exception: pass
            if idx % 5 == 0 or idx == len(accounts):
                try:
                    await rp.edit_text(
                        f"⏳ <b>[{channel_target}] مرحله ۳: ری‌اکشن</b>\n"
                        f"━━━━━━━━━━━━━━━━━━━━━━\n\n"
                        f"✅ {ok_react} / {idx} انجام شد",
                        parse_mode="HTML",
                    )
                except Exception:
                    pass
            if idx < len(accounts):
                await asyncio.sleep(random.uniform(1, 3))

    # ── مرحله ۴: listener دائمی ──
    # هر کانال listener مستقل دارد؛ از global dict جلوگیری از تکرار
    if channel_target not in _fch_active_listeners:
        _fch_active_listeners.add(channel_target)
        asyncio.create_task(_fixed_channel_listener(channel_target))

    await msg.answer(
        f"🎉 <b>اجرا برای <code>@{channel_target}</code> کامل شد</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n\n"
        f"✅ جوین: <b>{ok_join}</b> اکانت\n"
        f"💬 ری‌اکشن‌ها: {reactions_text}\n\n"
        f"🔄 Listener فعال — هر پست جدید ری‌اکشن می‌گیره",
        reply_markup=fixed_channels_list_menu(_fch_load()),
        parse_mode="HTML",
    )


# ─────────────────────────────────────────────────────────
# 📌 کانال‌های ثابت — listener دائمی
# ─────────────────────────────────────────────────────────

_fch_active_listeners: set[str] = set()   # کانال‌هایی که listener دارند


async def _fixed_channel_listener(channel_target: str):
    """
    Listener دائمی برای یک کانال:
    - هر ۶۰ ثانیه پست جدید چک می‌کند
    - با همه اکانت‌ها ری‌اکشن تصادفی می‌زند
    - اگر کانال از لیست حذف شد، متوقف می‌شود
    """
    import random
    from core.engine import make_client
    from telethon.tl.functions.messages import SendReactionRequest
    from telethon.tl.types import ReactionEmoji

    log.info(f"[fch_listener:{channel_target}] شروع")
    seen_ids: set[int] = set()
    poll_interval = 60

    # مقداردهی اولیه seen_ids با پست‌های موجود
    accounts = db.get_all_accounts()
    try:
        async def _init_seen(c):
            old = await asyncio.wait_for(c.get_messages(channel_target, limit=5), timeout=15)
            return [m.id for m in old]

        init_ids = await _probe_with_any_account(_init_seen, accounts, timeout=20)
        seen_ids.update(init_ids)
    except Exception as e:
        log.warning(f"[fch_listener:{channel_target}] init error: {e}")

    while True:
        await asyncio.sleep(poll_interval)

        # بررسی وجود کانال در لیست (اگه حذف شد متوقف می‌شه)
        channels = _fch_load()
        ch_data = next((c for c in channels if c["username"] == channel_target), None)
        if not ch_data:
            log.info(f"[fch_listener:{channel_target}] کانال از لیست حذف شد — listener متوقف")
            _fch_active_listeners.discard(channel_target)
            break

        allowed_reactions = ch_data.get("reactions") or _DEFAULT_REACTIONS

        # دریافت پست‌های جدید
        new_msg_ids: list[int] = []
        current_accounts = db.get_all_accounts()
        if not current_accounts:
            continue
        try:
            async def _poll(c):
                recent = await asyncio.wait_for(c.get_messages(channel_target, limit=5), timeout=15)
                return [m.id for m in recent]

            recent_ids = await _probe_with_any_account(_poll, current_accounts, timeout=20)
            for mid in recent_ids:
                if mid not in seen_ids:
                    new_msg_ids.append(mid)
                    seen_ids.add(mid)
        except Exception as e:
            log.warning(f"[fch_listener:{channel_target}] poll error: {e}")

        # ری‌اکشن روی پست‌های جدید
        for mid in new_msg_ids:
            log.info(f"[fch_listener:{channel_target}] پست جدید {mid} — ری‌اکشن")
            for a in current_accounts:
                c = None
                try:
                    c = make_client(db.get_session(a["phone"]), phone=a["phone"])
                    await asyncio.wait_for(c.connect(), timeout=15)
                    await asyncio.wait_for(
                        c(SendReactionRequest(
                            peer=channel_target,
                            msg_id=mid,
                            reaction=[ReactionEmoji(emoticon=random.choice(allowed_reactions))],
                        )),
                        timeout=20,
                    )
                except Exception as e:
                    log.debug(f"[fch_listener:{channel_target}] react {a['phone']}: {e}")
                finally:
                    if c:
                        try: await c.disconnect()
                        except Exception: pass
                await asyncio.sleep(random.uniform(1, 4))


# ─────────────────────────────────────────────────────────
# 🔗 جوین توی جوین اجباری
# ─────────────────────────────────────────────────────────

from aiogram.fsm.state import State, StatesGroup as _SG


class _FJState(_SG):
    waiting_leave_days   = State()
    waiting_acc_count    = State()
    waiting_join_interval = State()
    waiting_batch_size   = State()


@router.callback_query(F.data == "fj:back")
async def cb_fj_back(cb: types.CallbackQuery):
    await cb.answer()
    enabled = db.get_setting("fj_enabled", "0") == "1"
    leave_days = int(db.get_setting("fj_leave_days", "1"))
    acc_count  = int(db.get_setting("fj_acc_count", "0"))
    total      = len(db.get_all_accounts())
    status_txt = "✅ فعال" if enabled else "❌ غیرفعال"
    try:
        await cb.message.edit_text(
            f"🔗 <b>جوین توی جوین اجباری</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━━━\n\n"
            f"وضعیت: <b>{status_txt}</b>\n"
            f"⏰ تایم ترک: <b>{leave_days} روز</b>\n"
            f"🔢 تعداد اکانت: <b>{'همه' if acc_count == 0 else acc_count}</b> از {total}\n\n"
            f"👇 گزینه مورد نظر:",
            reply_markup=forced_join_menu(enabled),
            parse_mode="HTML",
        )
    except Exception:
        pass


@router.callback_query(F.data == "fj:toggle")
async def cb_fj_toggle(cb: types.CallbackQuery):
    current = db.get_setting("fj_enabled", "0") == "1"
    new_val = "0" if current else "1"
    db.set_setting("fj_enabled", new_val)
    enabled = new_val == "1"
    status_txt = "✅ فعال شد" if enabled else "❌ غیرفعال شد"
    await cb.answer(f"🔛 جوین اجباری {status_txt}", show_alert=True)
    leave_days = int(db.get_setting("fj_leave_days", "1"))
    acc_count  = int(db.get_setting("fj_acc_count", "0"))
    total      = len(db.get_all_accounts())
    try:
        await cb.message.edit_text(
            f"🔗 <b>جوین توی جوین اجباری</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━━━\n\n"
            f"وضعیت: <b>{'✅ فعال' if enabled else '❌ غیرفعال'}</b>\n"
            f"⏰ تایم ترک: <b>{leave_days} روز</b>\n"
            f"🔢 تعداد اکانت: <b>{'همه' if acc_count == 0 else acc_count}</b> از {total}\n\n"
            f"👇 گزینه مورد نظر:",
            reply_markup=forced_join_menu(enabled),
            parse_mode="HTML",
        )
    except Exception:
        pass


@router.callback_query(F.data == "fj:settings")
async def cb_fj_settings(cb: types.CallbackQuery):
    await cb.answer()
    enabled          = db.get_setting("fj_enabled", "0") == "1"
    leave_days       = int(db.get_setting("fj_leave_days", "1"))
    acc_count        = int(db.get_setting("fj_acc_count", "0"))
    join_interval_min = int(db.get_setting("fj_join_interval_min", "10"))
    batch_size       = int(db.get_setting("fj_batch_size", "5"))
    total            = len(db.get_all_accounts())
    status_txt       = "✅ فعال" if enabled else "❌ غیرفعال"
    try:
        await cb.message.edit_text(
            f"⚙️ <b>تنظیمات جوین اجباری</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━━━\n\n"
            f"🔛 وضعیت: <b>{status_txt}</b>\n"
            f"⏱ فاصله بین هر دور: <b>{join_interval_min} دقیقه</b>\n"
            f"🔢 اکانت هر دور: <b>{batch_size} تا</b>\n"
            f"⏰ تایم ترک گروه: <b>{leave_days} روز</b>\n"
            f"🔢 تعداد اکانت: <b>{'همه (' + str(total) + ')' if acc_count == 0 else str(acc_count)}</b>\n\n"
            f"👇 گزینه مورد نظر:",
            reply_markup=forced_join_settings_menu(
                join_interval_min=join_interval_min,
                batch_size=batch_size,
            ),
            parse_mode="HTML",
        )
    except Exception:
        pass


@router.callback_query(F.data == "fj:set_join_interval")
async def cb_fj_set_join_interval(cb: types.CallbackQuery, state: FSMContext):
    await cb.answer()
    cur = int(db.get_setting("fj_join_interval_min", "10"))
    await cb.message.answer(
        f"⏱ <b>فاصله بین هر دور جوین (دقیقه)</b>\n\n"
        f"مقدار فعلی: <b>{cur} دقیقه</b>\n\n"
        f"عدد دقیقه رو بفرست. مثال: <code>10</code>",
        reply_markup=cancel_menu(),
        parse_mode="HTML",
    )
    await state.set_state(_FJState.waiting_join_interval)


@router.message(StateFilter(_FJState.waiting_join_interval))
async def fj_set_join_interval(msg: types.Message, state: FSMContext):
    if not await guard(msg): return
    raw = (msg.text or "").strip().translate(_FA_DIGITS)
    if not raw.isdigit() or int(raw) < 1:
        await msg.answer("❌ عدد معتبر وارد کن (حداقل 1 دقیقه). مثال: <code>10</code>", parse_mode="HTML")
        return
    db.set_setting("fj_join_interval_min", raw)
    await msg.answer(
        f"✅ فاصله جوین تنظیم شد: <b>{raw} دقیقه</b>",
        reply_markup=main_menu(), parse_mode="HTML",
    )
    await state.clear()


@router.callback_query(F.data == "fj:set_batch_size")
async def cb_fj_set_batch_size(cb: types.CallbackQuery, state: FSMContext):
    await cb.answer()
    cur = int(db.get_setting("fj_batch_size", "5"))
    await cb.message.answer(
        f"🔢 <b>تعداد اکانت در هر دور جوین</b>\n\n"
        f"مقدار فعلی: <b>{cur} اکانت</b>\n\n"
        f"عدد رو بفرست. مثال: <code>5</code>",
        reply_markup=cancel_menu(),
        parse_mode="HTML",
    )
    await state.set_state(_FJState.waiting_batch_size)


@router.message(StateFilter(_FJState.waiting_batch_size))
async def fj_set_batch_size(msg: types.Message, state: FSMContext):
    if not await guard(msg): return
    raw = (msg.text or "").strip().translate(_FA_DIGITS)
    if not raw.isdigit() or int(raw) < 1:
        await msg.answer("❌ عدد معتبر وارد کن (حداقل 1). مثال: <code>5</code>", parse_mode="HTML")
        return
    db.set_setting("fj_batch_size", raw)
    await msg.answer(
        f"✅ تعداد اکانت هر دور تنظیم شد: <b>{raw} اکانت</b>",
        reply_markup=main_menu(), parse_mode="HTML",
    )
    await state.clear()


@router.callback_query(F.data == "fj:set_leave_days")
async def cb_fj_set_leave_days(cb: types.CallbackQuery, state: FSMContext):
    await cb.answer()
    cur = int(db.get_setting("fj_leave_days", "1"))
    await cb.message.answer(
        f"⏰ <b>تایم ترک گروه</b>\n\n"
        f"مقدار فعلی: <b>{cur} روز</b>\n\n"
        f"تعداد روز تا ترک گروه‌های جوین‌شده رو بفرست:\n"
        f"<code>1</code>  (یک روز)\n"
        f"<code>7</code>  (یک هفته)\n\n"
        f"عدد رو بفرست:",
        reply_markup=cancel_menu(),
        parse_mode="HTML",
    )
    await state.set_state(_FJState.waiting_leave_days)


@router.message(StateFilter(_FJState.waiting_leave_days))
async def fj_set_leave_days(msg: types.Message, state: FSMContext):
    if not await guard(msg): return
    raw = (msg.text or "").strip().translate(_FA_DIGITS)
    if not raw.isdigit() or int(raw) < 1:
        await msg.answer("❌ عدد معتبر وارد کن (مثال: <code>1</code>)", parse_mode="HTML")
        return
    db.set_setting("fj_leave_days", raw)
    await msg.answer(
        f"✅ تایم ترک تنظیم شد: <b>{raw} روز</b>",
        reply_markup=main_menu(), parse_mode="HTML",
    )
    await state.clear()


@router.callback_query(F.data == "fj:set_acc_count")
async def cb_fj_set_acc_count(cb: types.CallbackQuery, state: FSMContext):
    await cb.answer()
    cur   = int(db.get_setting("fj_acc_count", "0"))
    total = len(db.get_all_accounts())
    await cb.message.answer(
        f"🔢 <b>تعداد اکانت برای عضویت</b>\n\n"
        f"کل اکانت‌ها: <b>{total}</b>\n"
        f"مقدار فعلی: <b>{'همه' if cur == 0 else cur}</b>\n\n"
        f"تعداد اکانتی که باید عضو بشن رو بفرست.\n"
        f"<code>0</code> = همه اکانت‌ها\n"
        f"مثال: <code>5</code>",
        reply_markup=cancel_menu(),
        parse_mode="HTML",
    )
    await state.set_state(_FJState.waiting_acc_count)


@router.message(StateFilter(_FJState.waiting_acc_count))
async def fj_set_acc_count(msg: types.Message, state: FSMContext):
    if not await guard(msg): return
    raw = (msg.text or "").strip().translate(_FA_DIGITS)
    if not raw.isdigit():
        await msg.answer("❌ عدد معتبر وارد کن (مثال: <code>5</code> یا <code>0</code> برای همه)", parse_mode="HTML")
        return
    db.set_setting("fj_acc_count", raw)
    label = "همه اکانت‌ها" if raw == "0" else f"{raw} اکانت"
    await msg.answer(
        f"✅ تعداد اکانت تنظیم شد: <b>{label}</b>",
        reply_markup=main_menu(), parse_mode="HTML",
    )
    await state.clear()


@router.callback_query(F.data == "fj:leave_all")
async def cb_fj_leave_all(cb: types.CallbackQuery):
    await cb.answer("⏳ شروع ترک همه گروه‌ها و کانال‌ها...")
    await cb.message.answer(
        "⏳ <b>در حال ترک همه گروه‌ها و کانال‌ها...</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━",
        parse_mode="HTML",
    )
    asyncio.create_task(_fj_leave_all_task(cb))


@router.callback_query(F.data == "fj:run")
async def cb_fj_run(cb: types.CallbackQuery):
    await cb.answer("⏳ شروع جوین اجباری...")
    group_id = _get_group_id()
    if not group_id:
        await cb.message.answer("❌ ابتدا گروه هدف رو تنظیم کن (منوی گروه هدف).")
        return
    await cb.message.answer(
        "⏳ <b>شروع جوین توی جوین اجباری...</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n\n"
        "📌 اکانت‌ها پیام می‌فرستن، ربات @vornixbazarbot\n"
        "پیام رو حذف می‌کنه و لینک کانال‌ها رو میده.\n"
        "سپس اکانت‌ها عضو کانال‌ها میشن.",
        parse_mode="HTML",
    )
    asyncio.create_task(_fj_run_task(cb, group_id))


# ─── Task: اجرای جوین اجباری ───

async def _fj_run_task(cb: types.CallbackQuery, group_id):
    """
    ۱. هر اکانت توی گروه هدف یک پیام می‌فرسته
    ۲. منتظر میمونه ربات @vornixbazarbot پیام بده (با دکمه‌های کانال)
    ۳. اکانت‌ها عضو کانال‌های دکمه‌ای میشن
    ۴. بعد از تایم مشخص (fj_leave_days) ترک می‌کنن (scheduler جدا)
    """
    from core.engine import make_client
    from core.entity import resolve_group_entity
    from telethon.tl.functions.channels import JoinChannelRequest
    import re as _re2

    leave_days = int(db.get_setting("fj_leave_days", "1"))
    acc_count  = int(db.get_setting("fj_acc_count", "0"))

    accounts = db.get_all_accounts()
    if acc_count > 0:
        accounts = accounts[:acc_count]

    total  = len(accounts)
    ok     = 0
    errors = []

    status_msg = await cb.message.answer(
        f"🔄 <b>پیشرفت: 0/{total}</b>\nدر حال اجرا...",
        parse_mode="HTML",
    )

    for i, acc in enumerate(accounts, 1):
        phone  = acc["phone"]
        client = None
        joined_channels = []
        try:
            client = make_client(db.get_session(phone), phone=phone)
            await asyncio.wait_for(client.connect(), timeout=20)
            if not await client.is_user_authorized():
                errors.append(f"{phone}: unauthorized")
                continue

            # ۱. ارسال پیام به گروه هدف
            entity = await resolve_group_entity(client, group_id, timeout=15)
            sent_msg = await client.send_message(entity, ".")
            await asyncio.sleep(3)

            # ۲. گوش دادن به ربات @vornixbazarbot برای پیام با دکمه
            bot_msg = None
            async for message in client.iter_messages(entity, limit=10):
                sender = getattr(message, "sender", None)
                username = getattr(sender, "username", "") or ""
                if username.lower() == "vornixbazarbot" and message.buttons:
                    bot_msg = message
                    break

            if bot_msg and bot_msg.buttons:
                # ۳. استخراج لینک‌های کانال از دکمه‌ها
                for row in bot_msg.buttons:
                    for btn in row:
                        url = getattr(btn, "url", None)
                        if url and "t.me" in url:
                            try:
                                # جوین کردن کانال
                                ch_entity = await client.get_entity(url)
                                await client(JoinChannelRequest(ch_entity))
                                joined_channels.append(url)
                                await asyncio.sleep(2)
                            except Exception as je:
                                pass

            # ۴. ثبت در دیتابیس برای ترک بعدی
            if joined_channels:
                import json, time
                leave_at = time.time() + leave_days * 86400
                existing = db.get_setting("fj_pending_leaves", "[]")
                try:
                    pending = json.loads(existing)
                except Exception:
                    pending = []
                for ch in joined_channels:
                    pending.append({"phone": phone, "channel": ch, "leave_at": leave_at})
                db.set_setting("fj_pending_leaves", json.dumps(pending))

            ok += 1
            # آپدیت وضعیت هر ۵ اکانت
            if i % 5 == 0 or i == total:
                try:
                    await status_msg.edit_text(
                        f"🔄 <b>پیشرفت: {i}/{total}</b>\n"
                        f"✅ موفق: {ok}\n"
                        f"❌ خطا: {len(errors)}",
                        parse_mode="HTML",
                    )
                except Exception:
                    pass

        except Exception as e:
            errors.append(f"{phone}: {str(e)[:60]}")
        finally:
            if client:
                try:
                    await client.disconnect()
                except Exception:
                    pass
            await asyncio.sleep(2)

    # گزارش نهایی
    err_text = ""
    for e in errors[:5]:
        err_text += f"\n🔴 <code>{e}</code>"
    try:
        await status_msg.edit_text(
            f"✅ <b>جوین اجباری تموم شد</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━━━\n\n"
            f"✅ موفق: <b>{ok}</b>\n"
            f"❌ خطا: <b>{len(errors)}</b>\n"
            f"👥 کل: <b>{total}</b>\n"
            f"⏰ ترک بعد از: <b>{leave_days} روز</b>"
            f"{err_text}",
            parse_mode="HTML",
        )
    except Exception:
        pass


# ─── Task: ترک همه گروه‌ها و کانال‌ها ───

async def _fj_leave_all_task(cb: types.CallbackQuery):
    """همه اکانت‌ها از همه گروه‌ها و کانال‌هایی که عضوشن لف میدن"""
    from core.engine import make_client
    from telethon.tl.functions.channels import LeaveChannelRequest
    from telethon.tl.types import Channel, Chat

    accounts = db.get_all_accounts()
    total_ok   = 0
    total_left = 0

    status_msg = await cb.message.answer(
        f"🔄 <b>در حال ترک گروه‌ها...</b>\n0/{len(accounts)} اکانت",
        parse_mode="HTML",
    )

    for i, acc in enumerate(accounts, 1):
        phone  = acc["phone"]
        client = None
        left   = 0
        try:
            client = make_client(db.get_session(phone), phone=phone)
            await asyncio.wait_for(client.connect(), timeout=20)
            if not await client.is_user_authorized():
                continue

            async for dialog in client.iter_dialogs():
                entity = dialog.entity
                if isinstance(entity, (Channel, Chat)):
                    try:
                        await client(LeaveChannelRequest(entity))
                        left += 1
                        await asyncio.sleep(1)
                    except Exception:
                        pass

            total_ok   += 1
            total_left += left

            if i % 3 == 0 or i == len(accounts):
                try:
                    await status_msg.edit_text(
                        f"🔄 <b>پیشرفت: {i}/{len(accounts)}</b>\n"
                        f"🚪 کل ترک‌ها: {total_left}",
                        parse_mode="HTML",
                    )
                except Exception:
                    pass

        except Exception:
            pass
        finally:
            if client:
                try:
                    await client.disconnect()
                except Exception:
                    pass
            await asyncio.sleep(2)

    # پاک کردن صف ترک از دیتابیس
    db.set_setting("fj_pending_leaves", "[]")

    try:
        await status_msg.edit_text(
            f"✅ <b>ترک کامل شد</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━━━\n\n"
            f"👥 اکانت‌های پردازش‌شده: <b>{total_ok}</b>\n"
            f"🚪 کل گروه‌های ترک‌شده: <b>{total_left}</b>",
            parse_mode="HTML",
        )
    except Exception:
        pass


# ─── FSM: استارت ربات با همه اکانت‌ها ───

@router.message(StateFilter(UseMembers.waiting_start_bot_link))
async def mem_start_bot_link(msg: types.Message, state: FSMContext):
    if not await guard(msg): return
    raw = (msg.text or "").strip()
    if not raw:
        await msg.answer("❌ لینک خالیه. دوباره بفرست یا انصراف بزن.")
        return

    # normalize: extract username and start param if deep link
    # https://t.me/botname?start=xxx  or  t.me/botname  or  @botname
    import re
    username  = None
    start_param = None
    deep = re.match(r"(?:https?://)?t\.me/([A-Za-z0-9_]+)\?start=(.+)", raw)
    if deep:
        username    = deep.group(1)
        start_param = deep.group(2)
    else:
        plain = re.match(r"(?:https?://)?t\.me/([A-Za-z0-9_]+)", raw)
        if plain:
            username = plain.group(1)
        elif raw.startswith("@"):
            username = raw[1:]
        else:
            username = raw.lstrip("@")

    if not username:
        await msg.answer("❌ لینک معتبر نیست. دوباره بفرست.")
        return

    await state.clear()
    await msg.answer(
        f"⏳ <b>شروع استارت ربات</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n\n"
        f"🎯 ربات: <code>@{username}</code>\n"
        f"🔑 پارامتر: <code>{start_param or '—'}</code>\n\n"
        f"در حال اجرا...",
        parse_mode="HTML",
    )
    asyncio.create_task(_start_bot_all_task(msg, username, start_param))


async def _start_bot_all_task(msg: types.Message, username: str, start_param: str | None):
    """با همه اکانت‌ها یکی یکی /start می‌زنه روی ربات هدف با تاخیر."""
    from core.engine import make_client
    from telethon.tl.functions.messages import StartBotRequest
    import random

    accounts = db.get_all_accounts()
    if not accounts:
        await msg.answer("❌ هیچ اکانتی موجود نیست.")
        return

    ok = 0
    errors: list[str] = []
    delay_min, delay_max = 3, 7   # ثانیه

    progress_msg = await msg.answer(
        f"🚀 <b>استارت ربات در حال اجرا</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n\n"
        f"⏳ 0 / {len(accounts)} اکانت پردازش شد...",
        parse_mode="HTML",
    )

    for idx, a in enumerate(accounts, 1):
        c = None
        try:
            c = make_client(db.get_session(a["phone"]), phone=a["phone"])
            await asyncio.wait_for(c.connect(), timeout=15)

            bot_entity = await asyncio.wait_for(c.get_entity(username), timeout=15)
            await asyncio.wait_for(
                c(StartBotRequest(
                    bot=bot_entity,
                    peer=bot_entity,
                    start_param=start_param or "",
                )),
                timeout=20,
            )
            ok += 1
        except Exception as e:
            err_str = f"{a['phone']}: {type(e).__name__}: {str(e)[:60]}"
            errors.append(err_str)
            db.debug_log("handlers", "start_bot_all", "WARN", err_str)
        finally:
            if c is not None:
                try:
                    await c.disconnect()
                except Exception:
                    pass

        # آپدیت پروگرس هر ۵ اکانت
        if idx % 5 == 0 or idx == len(accounts):
            try:
                await progress_msg.edit_text(
                    f"🚀 <b>استارت ربات در حال اجرا</b>\n"
                    f"━━━━━━━━━━━━━━━━━━━━━━\n\n"
                    f"✅ {ok} / {idx} موفق\n"
                    f"❌ {idx - ok} خطا",
                    parse_mode="HTML",
                )
            except Exception:
                pass

        # تاخیر بین اکانت‌ها (آخری نیازی نداره)
        if idx < len(accounts):
            await asyncio.sleep(random.uniform(delay_min, delay_max))

    # گزارش نهایی
    text = (
        f"✅ <b>استارت ربات تموم شد</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n\n"
        f"🎯 ربات: <code>@{username}</code>\n"
        f"✅ موفق: <b>{ok}</b> / {len(accounts)}\n"
        f"❌ خطا: <b>{len(accounts) - ok}</b>"
    )
    for e in errors[:5]:
        text += f"\n🔴 <code>{e}</code>"
    if len(errors) > 5:
        text += f"\n... و {len(errors) - 5} خطای دیگه"
    await msg.answer(text, reply_markup=main_menu(), parse_mode="HTML")


# ─── FSM: عضو شدن توی گروه دلخواه ───

@router.message(StateFilter(UseMembers.waiting_join_group_link))
async def mem_join_group_link(msg: types.Message, state: FSMContext):
    if not await guard(msg): return
    raw = (msg.text or "").strip()
    if not raw:
        await msg.answer("❌ لینک خالیه. دوباره بفرست یا انصراف بزن.")
        return

    import re
    group_target = None
    link_match = re.match(r"(?:https?://)?t\.me/(?:joinchat/|\+)?([A-Za-z0-9_+\-]+)", raw)
    if link_match:
        slug = link_match.group(1)
        # اگه hash دعوت‌نامه‌ست (joinchat) کل لینک رو نگه می‌داریم
        if re.match(r"[A-Za-z0-9_\-]{16,}", slug) and not slug.startswith("+"):
            group_target = raw   # لینک دعوت خصوصی — کامل لازمه
        else:
            group_target = slug.lstrip("+") if slug.startswith("+") else slug
    elif raw.startswith("@"):
        group_target = raw[1:]
    elif raw.lstrip("-").isdigit():
        group_target = int(raw)
    else:
        group_target = raw.lstrip("@")

    if not group_target:
        await msg.answer("❌ لینک معتبر نیست. دوباره بفرست.")
        return

    await state.clear()
    display = raw if isinstance(group_target, str) and raw.startswith("https") else f"<code>{group_target}</code>"
    await msg.answer(
        f"⏳ <b>شروع عضویت در گروه</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n\n"
        f"🎯 گروه: {display}\n\n"
        f"در حال اجرا...",
        parse_mode="HTML",
    )
    asyncio.create_task(_join_target_group_all_task(msg, group_target))


async def _join_target_group_all_task(msg: types.Message, group_target):
    """با همه اکانت‌ها یکی یکی توی گروه هدف عضو می‌شه با تاخیر."""
    from core.engine import make_client
    from core.entity import resolve_group_entity
    from telethon.tl.functions.channels import JoinChannelRequest
    from telethon.tl.functions.messages  import ImportChatInviteRequest
    import random

    accounts = db.get_all_accounts()
    if not accounts:
        await msg.answer("❌ هیچ اکانتی موجود نیست.")
        return

    ok = 0
    errors: list[str] = []
    delay_min, delay_max = 3, 8

    # تشخیص لینک دعوت خصوصی
    is_invite_link = isinstance(group_target, str) and "t.me" in str(group_target)

    # استخراج hash از لینک دعوت
    invite_hash = None
    if is_invite_link:
        import re
        m = re.search(r"(?:joinchat/|\+)([A-Za-z0-9_\-]+)", str(group_target))
        if m:
            invite_hash = m.group(1)

    progress_msg = await msg.answer(
        f"👥 <b>عضویت در گروه در حال اجرا</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n\n"
        f"⏳ 0 / {len(accounts)} اکانت پردازش شد...",
        parse_mode="HTML",
    )

    for idx, a in enumerate(accounts, 1):
        c = None
        try:
            c = make_client(db.get_session(a["phone"]), phone=a["phone"])
            await asyncio.wait_for(c.connect(), timeout=15)

            if invite_hash:
                # لینک دعوت خصوصی
                await asyncio.wait_for(
                    c(ImportChatInviteRequest(invite_hash)),
                    timeout=20,
                )
            else:
                entity = await asyncio.wait_for(
                    resolve_group_entity(c, group_target, timeout=15),
                    timeout=20,
                )
                await asyncio.wait_for(
                    c(JoinChannelRequest(entity)),
                    timeout=20,
                )
            ok += 1
        except Exception as e:
            err_low = str(e).lower()
            if "already" in err_low or "participant" in err_low or "you have already" in err_low:
                ok += 1   # قبلاً عضو بوده
            else:
                err_str = f"{a['phone']}: {type(e).__name__}: {str(e)[:60]}"
                errors.append(err_str)
                db.debug_log("handlers", "join_target_group", "WARN", err_str)
        finally:
            if c is not None:
                try:
                    await c.disconnect()
                except Exception:
                    pass

        if idx % 5 == 0 or idx == len(accounts):
            try:
                await progress_msg.edit_text(
                    f"👥 <b>عضویت در گروه در حال اجرا</b>\n"
                    f"━━━━━━━━━━━━━━━━━━━━━━\n\n"
                    f"✅ {ok} / {idx} موفق\n"
                    f"❌ {idx - ok} خطا",
                    parse_mode="HTML",
                )
            except Exception:
                pass

        if idx < len(accounts):
            await asyncio.sleep(random.uniform(delay_min, delay_max))

    text = (
        f"✅ <b>عضویت تموم شد</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n\n"
        f"🎯 گروه: <code>{group_target}</code>\n"
        f"✅ موفق: <b>{ok}</b> / {len(accounts)}\n"
        f"❌ خطا: <b>{len(accounts) - ok}</b>"
    )
    for e in errors[:5]:
        text += f"\n🔴 <code>{e}</code>"
    if len(errors) > 5:
        text += f"\n... و {len(errors) - 5} خطای دیگه"
    await msg.answer(text, reply_markup=main_menu(), parse_mode="HTML")


# ─────────────────────────────────────────────────────────
# ⚙️ تنظیمات
# ─────────────────────────────────────────────────────────
@router.message(F.text.startswith("⚙️"))
async def show_settings(msg: types.Message):
    if not await guard(msg): return
    mn = int(db.get_setting("shift_min_sec", Config.SHIFT_MIN_SEC)) // 60
    mx = int(db.get_setting("shift_max_sec", Config.SHIFT_MAX_SEC)) // 60
    from core.pool import POOL
    active = POOL.active_shift
    await msg.answer(
        f"⚙️ <b>تنظیمات</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n\n"
        f"⏱️ بازه شیفت: <b>{mn}-{mx} دقیقه</b>\n"
        f"🔢 شیفت فعال: <b>{active}</b>\n\n"
        f"👇 گزینه مورد نظر:",
        reply_markup=settings_menu(),
        parse_mode="HTML",
    )


@router.callback_query(F.data.startswith("set:"))
async def cb_settings(cb: types.CallbackQuery, state: FSMContext):
    data = cb.data.split(":")[1]

    if data == "smart_scheduler":
        from core.smart_scheduler import get_smart_scheduler, get_mode, _SMART_STATE
        from core.pool import POOL
        sched = get_smart_scheduler()
        mode  = get_mode()

        if mode == "auto":
            nxt_dt = _SMART_STATE.get("next_swap_at")
            rem    = max(0, (nxt_dt - __import__("datetime").datetime.utcnow()).total_seconds()) if nxt_dt else 0
            comp_n = _SMART_STATE.get("computed_shifts") or "؟"
            comp_i = _SMART_STATE.get("computed_interval_sec")
            comp_i_txt = f"{comp_i//60} دقیقه" if comp_i else "در حال یادگیری..."
            cycles = _SMART_STATE.get("learning_cycles", 0)
            text = (
                f"🤖 <b>حالت خودکار فعاله</b>\n\n"
                f"📍 شیفت فعال: <b>{POOL.active_shift}</b> / {POOL.num_shifts}\n"
                f"⏳ چرخش بعدی: <b>{int(rem)//60}:{int(rem)%60:02d}</b>\n"
                f"🎯 شیفت بهینه: <b>{comp_n}</b>\n"
                f"⏱ فاصله بهینه: <b>{comp_i_txt}</b>\n"
                f"🔁 چرخه یادگیری: <b>{cycles}</b>"
            )
        else:
            text = (
                f"🔧 <b>حالت دستی فعاله</b>\n\n"
                f"📍 شیفت فعال: <b>{POOL.active_shift}</b> / {POOL.num_shifts}\n\n"
                f"برای چرخش خودکار بر اساس لیمیت واقعی اکانت‌ها،\n"
                f"حالت خودکار رو فعال کن."
            )
        await cb.message.edit_text(
            text,
            reply_markup=smart_scheduler_menu(mode),
            parse_mode="HTML",
        )

    elif data == "shift_timing":
        mn = int(db.get_setting("shift_min_sec", Config.SHIFT_MIN_SEC)) // 60
        mx = int(db.get_setting("shift_max_sec", Config.SHIFT_MAX_SEC)) // 60
        await cb.message.edit_text(
            f"⏱️ <b>بازه تایم شیفت</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━━━\n\n"
            f"⏰ فعلی: <b>{mn}-{mx} دقیقه</b>\n\n"
            f"یه پیش‌فرض انتخاب کن یا دستی وارد کن:",
            reply_markup=shift_timing_menu(),
            parse_mode="HTML",
        )

    elif data == "shift_active":
        from core.pool import POOL
        active = POOL.active_shift
        active_label = "🌐 همه شیفت‌ها" if active == 0 else f"شیفت {active}"
        await cb.message.edit_text(
            f"🔢 <b>انتخاب شیفت فعال</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━━━\n\n"
            f"شیفت فعلی: <b>{active_label}</b>\n"
            f"تعداد شیفت‌ها: <b>{POOL.num_shifts}</b>\n\n"
            f"👇 شیفت مورد نظر رو انتخاب کن:",
            reply_markup=shift_select_menu(active),
            parse_mode="HTML",
        )

    elif data == "guardian":
        await cb.answer("🛡️ نگهبان هر ۶۰ ثانیه چک می‌کنه", show_alert=True)

    elif data == "backup":
        try:
            from core.backup import create_backup
            f = create_backup()
            await cb.answer(f"✅ بکاپ ساخته شد ({f.stat().st_size//1024}KB)", show_alert=True)
        except Exception as e:
            await cb.answer(f"❌ خطا: {e}", show_alert=True)

    elif data == "num_shifts":
        from core.pool import POOL
        mn = int(db.get_setting("shift_min_sec", Config.SHIFT_MIN_SEC)) // 60
        mx = int(db.get_setting("shift_max_sec", Config.SHIFT_MAX_SEC)) // 60
        shift_lines = "\n".join(
            f"  شیفت {i}: <b>{len(POOL.shifts.get(i, []))}</b> اکانت"
            for i in range(1, POOL.num_shifts + 1)
        )
        total = len(POOL.all_phones)
        per_shift = total // POOL.num_shifts if POOL.num_shifts else 0
        await cb.message.edit_text(
            f"🔢 <b>تعداد شیفت‌ها</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━━━\n\n"
            f"📋 شیفت‌های فعال: <b>{POOL.num_shifts}</b>\n"
            f"👥 کل اکانت: <b>{total}</b>  (≈{per_shift} اکانت per شیفت)\n"
            f"⏱️ بازه چرخش: <b>{mn}-{mx} دقیقه</b>\n\n"
            f"{shift_lines}\n\n"
            f"👇 تعداد رو تغییر بده — اکانت‌ها خودکار توزیع میشن:",
            reply_markup=num_shifts_menu(POOL.num_shifts),
            parse_mode="HTML",
        )

    elif data == "viewguard":
        from core.view_guard import VIEW_GUARD, MAX_VIEWS_PER_DAY, MIN_GAP_SEC, MAX_GAP_SEC
        from bot.keyboards import viewguard_menu
        stats = VIEW_GUARD.stats()
        await cb.message.edit_text(
            f"🔒 <b>تنظیمات ضد لیمیت (ViewGuard)</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━━━\n\n"
            f"📊 ویوهای امروز (کل): <b>{stats['total_views_today']}</b>\n"
            f"🚫 اکانت‌های flood: <b>{stats['flooded_now']}</b>\n"
            f"💤 سهمیه تموم‌شده: <b>{stats['exhausted_today']}</b>\n\n"
            f"⚙️ تنظیمات فعلی:\n"
            f"  📅 سهمیه روزانه هر اکانت: <b>{MAX_VIEWS_PER_DAY}</b>\n"
            f"  ⏳ فاصله بین ویو: <b>{MIN_GAP_SEC:.0f}-{MAX_GAP_SEC:.0f} ثانیه</b>\n\n"
            f"👇 گزینه مورد نظر:",
            reply_markup=viewguard_menu(),
            parse_mode="HTML",
        )

    try:
        await cb.answer()
    except Exception:
        pass


def _back_to_settings_kb():
    from aiogram.utils.keyboard import InlineKeyboardBuilder
    b = InlineKeyboardBuilder()
    b.button(text="🔙 برگشت به تنظیمات", callback_data="menu:settings")
    return b.as_markup()


# ─────────────────────────────────────────────────────────
# تغییر تعداد شیفت‌ها + توزیع خودکار اکانت‌ها
# ─────────────────────────────────────────────────────────
def _build_num_shifts_text(pool) -> str:
    """متن نمایش وضعیت شیفت‌ها"""
    total = len(pool.all_phones)
    per_shift = total // pool.num_shifts if pool.num_shifts else 0
    shift_lines = "\n".join(
        f"  شیفت {i}: <b>{len(pool.shifts.get(i, []))}</b> اکانت"
        for i in range(1, pool.num_shifts + 1)
    )
    return (
        f"🔢 <b>تعداد شیفت‌ها</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n\n"
        f"📋 شیفت‌های فعال: <b>{pool.num_shifts}</b>\n"
        f"👥 کل اکانت: <b>{total}</b>  (≈{per_shift} اکانت per شیفت)\n\n"
        f"{shift_lines}\n\n"
        f"👇 تعداد رو تغییر بده — اکانت‌ها خودکار توزیع میشن:"
    )


@router.callback_query(F.data.startswith("ns:set:"))
async def cb_ns_set(cb: types.CallbackQuery):
    """تغییر تعداد شیفت‌ها و توزیع خودکار اکانت‌ها"""
    if not is_admin(cb.from_user.id):
        await cb.answer("⛔ دسترسی ندارید.", show_alert=True)
        return
    from core.pool import POOL
    try:
        n = int(cb.data.split(":")[2])
    except (ValueError, IndexError):
        await cb.answer("❌ مقدار نامعتبر", show_alert=True)
        return

    old = POOL.num_shifts
    POOL.set_num_shifts(n)

    # توزیع مجدد round-robin اکانت‌ها روی شیفت‌های جدید
    phones = list(POOL.all_phones)
    new_buckets = {i: [] for i in range(1, n + 1)}
    for idx, phone in enumerate(phones):
        bucket = (idx % n) + 1
        new_buckets[bucket].append(phone)
    POOL.shifts = new_buckets

    await cb.answer(
        f"✅ تعداد شیفت‌ها از {old} به {n} تغییر کرد\n"
        f"👥 {len(phones)} اکانت توزیع شد",
        show_alert=True,
    )
    # FIX Bug 2b: DB calls رو از event loop خارج کن
    def _save_shifts():
        for idx, phone in enumerate(phones):
            bucket = (idx % n) + 1
            db.update_account_shift(phone, str(bucket))
    await asyncio.to_thread(_save_shifts)
    try:
        await cb.message.edit_text(
            _build_num_shifts_text(POOL),
            reply_markup=num_shifts_menu(POOL.num_shifts),
            parse_mode="HTML",
        )
    except Exception:
        pass


@router.callback_query(F.data == "ns:redistribute")
async def cb_ns_redistribute(cb: types.CallbackQuery):
    """توزیع مجدد اکانت‌ها روی شیفت‌های فعلی (بدون تغییر تعداد)"""
    if not is_admin(cb.from_user.id):
        await cb.answer("⛔ دسترسی ندارید.", show_alert=True)
        return
    from core.pool import POOL

    n = POOL.num_shifts
    phones = list(POOL.all_phones)
    new_buckets = {i: [] for i in range(1, n + 1)}
    for idx, phone in enumerate(phones):
        bucket = (idx % n) + 1
        new_buckets[bucket].append(phone)
    POOL.shifts = new_buckets

    # FIX Bug 2b: DB calls رو از event loop خارج کن — blocking loop روی Supabase
    def _save_shifts():
        for idx, phone in enumerate(phones):
            bucket = (idx % n) + 1
            db.update_account_shift(phone, str(bucket))

    await cb.answer(
        f"✅ {len(phones)} اکانت روی {n} شیفت توزیع شد",
        show_alert=True,
    )
    await asyncio.to_thread(_save_shifts)
    try:
        await cb.message.edit_text(
            _build_num_shifts_text(POOL),
            reply_markup=num_shifts_menu(POOL.num_shifts),
            parse_mode="HTML",
        )
    except Exception:
        pass


# تنظیم بازه شیفت — پیش‌فرض
@router.callback_query(F.data.startswith("stm:preset:"))
async def cb_shift_timing_preset(cb: types.CallbackQuery):
    _, _, mn_str, mx_str = cb.data.split(":")
    mn = int(mn_str) * 60
    mx = int(mx_str) * 60
    db.set_setting("shift_min_sec", mn)
    db.set_setting("shift_max_sec", mx)
    await cb.answer(f"✅ بازه تنظیم شد: {mn_str}-{mx_str} دقیقه", show_alert=True)
    try:
        await cb.message.edit_text(
            f"⏱️ <b>بازه شیفت</b>\n\n✅ تنظیم شد: <b>{mn_str}-{mx_str} دقیقه</b>\n\n"
            f"شیفت بعدی این بازه رو استفاده می‌کنه.",
            reply_markup=shift_timing_menu(),
            parse_mode="HTML",
        )
    except Exception:
        pass


# تنظیم بازه شیفت — دستی
@router.callback_query(F.data == "stm:custom")
async def cb_shift_timing_custom(cb: types.CallbackQuery, state: FSMContext):
    await cb.message.edit_text(
        "⏱️ <b>بازه دلخواه</b>\n\n"
        "دو عدد بفرست (دقیقه):\n"
        "<code>حداقل حداکثر</code>\n\n"
        "مثال: <code>20 60</code>",
        reply_markup=cancel_menu(),
        parse_mode="HTML",
    )
    await state.set_state(SetShiftTiming.waiting_range)
    await cb.answer()


@router.message(StateFilter(SetShiftTiming.waiting_range))
async def set_shift_timing(msg: types.Message, state: FSMContext):
    if not await guard(msg): return
    parts = msg.text.strip().split()
    if len(parts) != 2:
        await msg.answer("❌ فرمت اشتباه. مثال: <code>20 60</code>", parse_mode="HTML")
        return
    try:
        mn, mx = int(parts[0]), int(parts[1])
        if mn < 1 or mx < mn + 1:
            raise ValueError
    except ValueError:
        await msg.answer("❌ اعداد معتبر نیستن. حداقل باید کمتر از حداکثر باشه.", parse_mode="HTML")
        return
    db.set_setting("shift_min_sec", mn * 60)
    db.set_setting("shift_max_sec", mx * 60)
    await msg.answer(
        f"✅ بازه شیفت تنظیم شد!\n"
        f"⏱️ <b>{mn}-{mx} دقیقه</b>\n\n"
        f"شیفت بعدی این بازه رو استفاده می‌کنه.",
        reply_markup=main_menu(),
        parse_mode="HTML",
    )
    await state.clear()


# انتخاب شیفت فعال
@router.callback_query(F.data.startswith("shift:select:"))
async def cb_shift_select(cb: types.CallbackQuery):
    shift = int(cb.data.split(":")[2])
    from core.pool import POOL
    old = POOL.active_shift

    # پیام انتظار
    old_label = "همه شیفت‌ها" if old == 0 else f"شیفت {old}"
    new_label  = "همه شیفت‌ها" if shift == 0 else f"شیفت {shift}"
    await cb.answer(f"⏳ {old_label} → {new_label} | در حال اتصال اکانت‌ها...")

    db.set_setting("active_shift", shift)
    try:
        rep = await asyncio.wait_for(POOL.activate_shift(shift), timeout=120)
    except Exception as e:
        total = len(POOL.all_phones) if shift == 0 else len(POOL.shifts.get(shift, []))
        rep = {"total": total, "connected": 0,
               "errors": {"-": f"{type(e).__name__}: {e}"}}

    if shift == 0:
        # حالت همه شیفت‌ها — جزئیات هر شیفت رو نشون میده
        shift_detail = "\n".join(
            f"  شیفت {i}: {len(POOL.shifts.get(i,[]))} اکانت"
            for i in range(1, 5)
        )
        text = (
            f"🌐 <b>همه شیفت‌ها فعال شدن</b>\n\n"
            f"🟢 متصل: <b>{rep['connected']}/{rep['total']}</b>\n\n"
            f"{shift_detail}"
        )
    else:
        text = (
            f"🔢 <b>شیفت فعال</b>\n\n✅ شیفت <b>{shift}</b> فعال شد.\n"
            f"🟢 متصل: <b>{rep['connected']}/{rep['total']}</b>"
        )
        if rep["total"] == 0:
            text += "\n\n⚠️ این شیفت هیچ اکانتی نداره."

    for phone, err in list(rep["errors"].items())[:8]:
        text += f"\n🔴 <code>{phone}</code>\n    <code>{err[:110]}</code>"
    try:
        await cb.message.edit_text(text, reply_markup=shift_select_menu(shift), parse_mode="HTML")
    except Exception:
        pass


# ─────────────────────────────────────────────────────────
# Callbacks: مدیریت شیفت هوشمند  (sched:*)
# ─────────────────────────────────────────────────────────
@router.callback_query(F.data.startswith("sched:"))
async def cb_smart_sched(cb: types.CallbackQuery):
    if not await guard(cb.message): return
    action = cb.data.split(":")[1]
    from core.smart_scheduler import get_smart_scheduler, get_mode, set_mode, _SMART_STATE

    sched = get_smart_scheduler()

    if action == "noop":
        await cb.answer()
        return

    elif action == "set_auto":
        set_mode("auto")
        from core.pool import POOL
        await cb.answer("🤖 خودکار فعال شد")
        await cb.message.edit_text(
            f"🤖 <b>حالت خودکار فعاله</b>\n\n"
            f"📍 شیفت فعال: <b>{POOL.active_shift}</b> / {POOL.num_shifts}\n"
            f"⏳ چرخش بعدی: <b>در حال محاسبه...</b>\n"
            f"🎯 شیفت بهینه: <b>در حال یادگیری...</b>\n"
            f"⏱ فاصله بهینه: <b>در حال یادگیری...</b>\n"
            f"🔁 چرخه یادگیری: <b>0</b>",
            reply_markup=smart_scheduler_menu("auto"),
            parse_mode="HTML",
        )

    elif action == "set_manual":
        set_mode("manual")
        from core.pool import POOL
        await cb.answer("🔧 دستی فعال شد")
        await cb.message.edit_text(
            f"🔧 <b>حالت دستی فعاله</b>\n\n"
            f"📍 شیفت فعال: <b>{POOL.active_shift}</b> / {POOL.num_shifts}\n\n"
            f"برای چرخش خودکار بر اساس لیمیت واقعی اکانت‌ها،\n"
            f"حالت خودکار رو فعال کن.",
            reply_markup=smart_scheduler_menu("manual"),
            parse_mode="HTML",
        )

    elif action == "stats":
        txt = sched.status_text()
        await cb.message.edit_text(
            f"📊 <b>آمار شیفت هوشمند</b>\n━━━━━━━━━━━━━━━━━━━━━━\n\n{txt}",
            reply_markup=smart_scheduler_menu(get_mode()),
            parse_mode="HTML",
        )
        await cb.answer()

    elif action == "rotate_now":
        old_shift = sched._get_pool().active_shift
        new_shift = await sched.rotate_now()
        await cb.answer(f"🔄 شیفت {old_shift}→{new_shift} چرخید", show_alert=True)
        await cb.message.edit_text(
            f"🔄 <b>چرخش انجام شد</b>\n\n"
            f"شیفت قبلی: <b>{old_shift}</b>\n"
            f"شیفت جدید: <b>{new_shift}</b>",
            reply_markup=smart_scheduler_menu(get_mode()),
            parse_mode="HTML",
        )

    elif action == "reset_learning":
        _SMART_STATE["metrics"] = {}
        _SMART_STATE["computed_shifts"] = None
        _SMART_STATE["computed_interval_sec"] = None
        _SMART_STATE["learning_cycles"] = 0
        _SMART_STATE["last_status_msg"] = ""
        await cb.answer("🗑 داده‌های یادگیری ریست شد", show_alert=True)
        await cb.message.edit_text(
            "🗑 <b>داده یادگیری ریست شد</b>\n\n"
            "ربات از صفر شروع به جمع‌آوری داده می‌کنه.",
            reply_markup=smart_scheduler_menu(get_mode()),
            parse_mode="HTML",
        )

    else:
        await cb.answer()


# ─────────────────────────────────────────────────────────
# چرخش دستی
# ─────────────────────────────────────────────────────────
@router.message(F.text.startswith("🔄"))
async def manual_rotate(msg: types.Message):
    if not await guard(msg): return
    try:
        from core.smart_scheduler import get_smart_scheduler
        s   = get_smart_scheduler()
        old = s._get_pool().active_shift
        new = await s.rotate_now()
        from core.smart_scheduler import _SMART_STATE
        await msg.answer(
            f"🔄 <b>چرخش انجام شد</b>\n\n"
            f"🅰️ شیفت قبلی: <b>{old}</b>\n"
            f"🅱️ شیفت جدید: <b>{new}</b>\n"
            f"📊 کل چرخش‌های موفق: <b>{_SMART_STATE.get('swap_count', 0)}</b>",
            parse_mode="HTML",
        )
    except Exception as e:
        await msg.answer(f"❌ خطا: {e}")


# ─────────────────────────────────────────────────────────
# Callback: منوی اصلی
# ─────────────────────────────────────────────────────────
@router.callback_query(F.data == "menu:main")
async def cb_main_menu(cb: types.CallbackQuery, state: FSMContext):
    await state.clear()
    try:
        await cb.message.edit_text("👇 از منوی زیر انتخاب کنید:")
    except Exception:
        pass
    await cb.answer("✅ برگشت به منو")


# ─────────────────────────────────────────────────────────
# Callback: وضعیت
# ─────────────────────────────────────────────────────────
@router.callback_query(F.data.startswith("status:"))
async def cb_status(cb: types.CallbackQuery):
    data   = cb.data.split(":")[1]
    counts = db.count_by_status()
    if data == "refresh":
        await cb.message.edit_text(_status_text(), reply_markup=status_menu(), parse_mode="HTML")
        await cb.answer("🔄 بروزرسانی شد")
        return
    n     = len(db.get_all_accounts()) if data == "all" else counts.get(data, 0)
    emoji = {"online": "🟢", "idle": "💤", "error": "🔴", "flood": "🟡", "all": "📊"}.get(data, "❓")
    await cb.answer(f"{emoji} {n} اکانت", show_alert=True)


# ─────────────────────────────────────────────────────────
# Callback: مدیریت اکانت
# ─────────────────────────────────────────────────────────
@router.callback_query(F.data.startswith("acc:"))
async def cb_accounts(cb: types.CallbackQuery, state: FSMContext):
    d = cb.data.split(":", 2)
    action = d[1]

    if action == "list":
        page     = int(d[2]) if len(d) > 2 else 0
        accounts = db.get_all_accounts()
        if not accounts:
            await cb.message.edit_text("📋 هیچ اکانتی نیست.", reply_markup=accounts_menu())
        else:
            await cb.message.edit_text(
                f"📋 <b>{len(accounts)} اکانت</b>\nصفحه {page+1}",
                reply_markup=accounts_list_kb(accounts, page),
                parse_mode="HTML",
            )

    elif action == "add":
        await cb.answer()
        await cb.message.answer(
            "➕ <b>افزودن اکانت جدید</b>\n"
            "━━━━━━━━━━━━━━━━━━━━━━\n\n"
            "روش افزودن رو انتخاب کن:",
            reply_markup=add_method_menu(),
            parse_mode="HTML",
        )

    elif action == "rotate":
        from core.smart_scheduler import get_smart_scheduler
        s = get_smart_scheduler()
        await s.rotate_now()
        await cb.answer("🔄 چرخش انجام شد", show_alert=True)
        await cb.message.edit_text(_status_text(), reply_markup=status_menu(), parse_mode="HTML")

    elif action == "cleanup":
        from supabase import create_client
        sb = create_client(Config.SUPABASE_URL, Config.SUPABASE_KEY)
        res = sb.table("accounts").select("id", count="exact").eq("status", "flood").execute()
        n = res.count or 0
        sb.table("accounts").update({"status": "idle", "flood_until": None}).eq("status", "flood").execute()
        await cb.answer(f"✅ {n} اکانت آزاد شد", show_alert=True)

    elif action == "backup":
        try:
            from core.backup import create_backup
            f = create_backup()
            await cb.answer(f"✅ بکاپ ساخته شد ({f.stat().st_size//1024}KB)", show_alert=True)
        except Exception as e:
            await cb.answer(f"❌ خطا: {e}", show_alert=True)

    elif action == "info":
        acc_id = int(d[2])
        from supabase import create_client
        sb = create_client(Config.SUPABASE_URL, Config.SUPABASE_KEY)
        res = sb.table("accounts").select("*").eq("id", acc_id).limit(1).execute()
        row = res.data[0] if res and res.data else None
        if row:
            a    = row
            icon = {"online": "🟢", "flood": "🟡", "error": "🔴", "idle": "💤"}.get(a["status"], "⚪")
            text = (
                f"<b>📱 اکانت #{acc_id}</b>\n"
                f"━━━━━━━━━━━━━━━━━━━━━━\n\n"
                f"{icon} وضعیت: <b>{a['status']}</b>\n"
                f"🔢 شیفت: <b>{a['shift']}</b>\n"
                f"🌐 پورت: <code>{a.get('proxy_port') or '-'}</code>\n"
                f"📅 ساخته: {str(a['created_at'])[:16]}\n"
            )
            if a.get("last_error"):
                text += f"\n⚠️ خطا: <code>{a['last_error'][:100]}</code>"
            await cb.message.edit_text(text, parse_mode="HTML")

    elif action == "noop":
        pass

    try:
        await cb.answer()
    except Exception:
        pass


# ─────────────────────────────────────────────────────────
# Callback: آمار
# ─────────────────────────────────────────────────────────
@router.callback_query(F.data.startswith("stats:"))
async def cb_stats(cb: types.CallbackQuery):
    data = cb.data.split(":")[1]

    if data == "general":
        accounts = db.get_all_accounts()
        counts   = db.count_by_status()
        from core.pool import POOL
        _SHIFT_EMOJIS = ["1️⃣","2️⃣","3️⃣","4️⃣","5️⃣","6️⃣","7️⃣","8️⃣","9️⃣","🔟"]
        active_lbl = "🌐 همه شیفت‌ها" if POOL.active_shift == 0 else f"شیفت {POOL.active_shift}"
        shift_lines = "\n".join(
            f"{_SHIFT_EMOJIS[i-1] if i <= len(_SHIFT_EMOJIS) else '🔢'} شیفت {i}: "
            f"<b>{sum(1 for a in accounts if str(a.get('shift','')) == str(i))}</b>"
            for i in range(1, POOL.num_shifts + 1)
        )
        await cb.message.edit_text(
            f"📊 <b>آمار کلی</b>\n━━━━━━━━━━━━━━━━━━━━━━\n\n"
            f"🔢 شیفت فعال: <b>{active_lbl}</b>\n"
            f"📋 تعداد شیفت‌ها: <b>{POOL.num_shifts}</b>\n"
            f"👥 کل اکانت: <b>{len(accounts)}</b>\n"
            f"🟢 آنلاین: <b>{counts.get('online', 0)}</b>\n"
            f"💤 استراحت: <b>{counts.get('idle', 0)}</b>\n"
            f"🟡 Flood: <b>{counts.get('flood', 0)}</b>\n"
            f"🔴 خطا: <b>{counts.get('error', 0)}</b>\n\n"
            f"{shift_lines}",
            reply_markup=stats_menu(), parse_mode="HTML",
        )

    elif data == "daily":
        msgs = db.count_debug_log_since("new_msg", hours=24)
        await cb.message.edit_text(
            f"📈 <b>آمار ۲۴ ساعت</b>\n━━━━━━━━━━━━━━━━━━━━━━\n\n"
            f"📨 پیام‌های جدید: <b>{msgs}</b>\n",
            reply_markup=stats_menu(), parse_mode="HTML",
        )

    elif data == "shift":
        from core.smart_scheduler import get_smart_scheduler, get_mode, _SMART_STATE
        from core.pool import POOL
        sched = get_smart_scheduler()
        cur   = POOL.active_shift
        mode  = get_mode()
        nxt_dt = _SMART_STATE.get("next_swap_at")
        nxt    = nxt_dt.strftime("%H:%M:%S") if nxt_dt else "?"
        total_swaps  = _SMART_STATE.get("swap_count", 0)
        last_swap_at = _SMART_STATE.get("last_swap_at") or "هنوز نشده"
        mode_label   = "🤖 خودکار" if mode == "auto" else "🔧 دستی"

        # خلاصه یادگیری
        learn_lines = []
        for sid, m in _SMART_STATE.get("metrics", {}).items():
            if m.has_data:
                learn_lines.append(
                    f"  شیفت {sid}: لیمیت≈{m.avg_limit_sec/60:.1f}م "
                    f"ریکاوری≈{m.avg_recovery_sec/60:.1f}م"
                )
        learn_txt = "\n".join(learn_lines) if learn_lines else "  هنوز داده‌ای نیست"

        comp_shifts   = _SMART_STATE.get("computed_shifts", "؟")
        comp_interval = _SMART_STATE.get("computed_interval_sec")
        comp_iv_txt   = f"{comp_interval//60} دقیقه" if comp_interval else "؟"

        await cb.message.edit_text(
            f"🔄 <b>وضعیت چرخه شیفت</b>\n━━━━━━━━━━━━━━━━━━━━━━\n\n"
            f"⚙️ حالت: <b>{mode_label}</b>\n"
            f"🟢 شیفت فعال: <b>{cur}</b> / {POOL.num_shifts}\n"
            f"⏰ چرخش بعدی: <b>{nxt}</b>\n"
            f"🔁 کل چرخش‌ها: <b>{total_swaps}</b>\n"
            f"🕐 آخرین چرخش: <b>{last_swap_at}</b>\n\n"
            f"━━━━━━━━━━━━━━━━━━━━━━\n"
            f"🧠 <b>یادگیری هوشمند</b>\n"
            f"  چرخه‌های یادگیری: <b>{_SMART_STATE.get('learning_cycles', 0)}</b>\n"
            f"  شیفت بهینه: <b>{comp_shifts}</b>\n"
            f"  فاصله بهینه: <b>{comp_iv_txt}</b>\n"
            f"{learn_txt}",
            reply_markup=stats_menu(), parse_mode="HTML",
        )

    elif data == "chart":
        rows = db.chart_reads_last_12h()
        text = "📉 <b>نمودار ۱۲ ساعت</b>\n━━━━━━━━━━━━━━━━━━━━━━\n\n"
        for r in rows:
            h   = r["h"]
            ok  = r["ok"] or 0
            bar = "▰" * min(ok, 20) + "▱" * max(0, 20 - min(ok, 20))
            text += f"<code>{h}:00</code> {bar} <b>{ok}</b>\n"
        await cb.message.edit_text(text, reply_markup=stats_menu(), parse_mode="HTML")

    await cb.answer()


# ─────────────────────────────────────────────────────────
# Callback: تایید
# ─────────────────────────────────────────────────────────
@router.callback_query(F.data.startswith("cfm:"))
async def cb_confirm(cb: types.CallbackQuery):
    d      = cb.data.split(":")
    action = d[2] if len(d) > 2 else ""
    yes    = d[1] == "yes"
    await cb.answer(f"{'✅' if yes else '❌'} {action}")


# ─────────────────────────────────────────────────────────
# ─────────────────────────────────────────────────────────
# Callback: انتخاب روش افزودن اکانت
# ─────────────────────────────────────────────────────────
@router.callback_query(F.data.startswith("add:method:"))
async def cb_add_method(cb: types.CallbackQuery, state: FSMContext):
    if not is_admin(cb.from_user.id):
        await cb.answer("⛔ دسترسی ندارید.", show_alert=True)
        return
    method = cb.data.split(":")[2]

    if method == "phone":
        await state.set_state(AddAccount.waiting_phone)
        group_id = _get_group_id()
        await cb.message.edit_text(
            "➕ <b>افزودن با شماره</b>\n"
            "━━━━━━━━━━━━━━━━━━━━━━\n\n"
            "شماره رو با فرمت بین\u200cالمللی بفرست:\n"
            "<code>+989123456789</code>\n\n"
            f"💡 اکانت خودکار عضو گروه می\u200cشه: <code>{db.fmt_group(group_id)}</code>",
            reply_markup=cancel_menu(),
            parse_mode="HTML",
        )
        await cb.answer()

    elif method == "session":
        await state.set_state(AddAccount.waiting_session)
        await cb.message.edit_text(
            "🔑 <b>افزودن با String Session</b>\n"
            "━━━━━━━━━━━━━━━━━━━━━━\n\n"
            "String Session اکانتت رو بفرست.\n\n"
            "⚠️ سشن باید با <code>API_ID</code> و <code>API_HASH</code> همین بات ساخته شده باشه.",
            reply_markup=cancel_menu(),
            parse_mode="HTML",
        )
        await cb.answer()

    elif method == "session_file":
        await state.set_state(AddAccount.waiting_session_file)
        await cb.message.edit_text(
            "📂 <b>افزودن با فایل .session</b>\n"
            "━━━━━━━━━━━━━━━━━━━━━━\n\n"
            "فایل <code>.session</code> تلتون رو اینجا بفرست.\n\n"
            "⚠️ فایل باید با <code>API_ID</code> و <code>API_HASH</code> همین بات ساخته شده باشه.",
            reply_markup=cancel_menu(),
            parse_mode="HTML",
        )
        await cb.answer()

    else:
        await cb.answer("❌ روش ناشناخته", show_alert=True)


@router.message(StateFilter(AddAccount.waiting_session))
async def add_session(msg: types.Message, state: FSMContext):
    if not await guard_admin(msg): return
    raw_session = (msg.text or "").strip()
    if not raw_session or len(raw_session) < 50:
        await msg.answer("❌ سشن معتبر نیست. دوباره بفرست یا انصراف بزن.")
        return

    await msg.answer("⏳ در حال بررسی و اتصال سشن...", parse_mode="HTML")
    c = None
    try:
        from telethon import TelegramClient
        from telethon.sessions import StringSession
        c = TelegramClient(StringSession(raw_session), Config.API_ID, Config.API_HASH)
        await asyncio.wait_for(c.connect(), timeout=20)

        if not await c.is_user_authorized():
            await msg.answer(
                "❌ <b>سشن منقضی یا نامعتبر است.</b>\n\n"
                "اکانت لاگ\u200cاوت شده یا سشن اشتباهه.",
                parse_mode="HTML",
            )
            await c.disconnect()
            await state.clear()
            return

        me    = await c.get_me()
        phone = getattr(me, "phone", None)
        if phone and not phone.startswith("+"):
            phone = "+" + phone
        phone_key = phone or f"id:{me.id}"

        try:
            exists = db.get_account(phone_key)
        except Exception:
            exists = None

        if exists:
            await msg.answer(
                f"⚠️ این اکانت (<code>{phone_key}</code>) قبلاً ثبت شده.",
                parse_mode="HTML",
            )
            await c.disconnect()
            await state.clear()
            return

        await _join_group(c)
        ss = c.session.save()
        await c.disconnect()

        acc_id = db.add_account(phone_key, ss)

        pool_ok = False
        try:
            from core.pool import POOL
            asyncio.create_task(POOL.add_account_live(phone_key))  # fire-and-forget
            pool_ok = True
        except Exception as _pe:
            db.debug_log("handlers", "pool_add_session", "WARN", f"{type(_pe).__name__}: {_pe}")

        await msg.answer(
            f"✅ <b>اکانت با سشن اضافه شد!</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━━━\n\n"
            f"👤 نام: <b>{me.first_name}</b>\n"
            f"🆔 یوزرنیم: @{me.username or 'ندارد'}\n"
            f"📱 شماره: <code>{phone or 'ناشناس'}</code>\n"
            f"🎫 شناسه: <code>{acc_id}</code>\n"
            f"👥 عضو گروه: ✅\n"
            f"🔌 اتصال به pool: {'✅' if pool_ok else '⚠️ بعداً خودکار وصل میشه'}",
            reply_markup=main_menu(),
            parse_mode="HTML",
        )
        await state.clear()

    except asyncio.TimeoutError:
        await msg.answer("❌ <b>Timeout</b>\nسرور تلگرام پاسخ نداد.", parse_mode="HTML")
        if c:
            try: await c.disconnect()
            except Exception: pass
        await state.clear()
    except Exception as e:
        await msg.answer(
            f"❌ <b>خطا در اتصال سشن</b>:\n<code>{str(e)[:200]}</code>",
            parse_mode="HTML",
        )
        if c:
            try: await c.disconnect()
            except Exception: pass
        await state.clear()


@router.message(StateFilter(AddAccount.waiting_session_file))
async def add_session_file(msg: types.Message, state: FSMContext, bot: Bot):
    if not await guard_admin(msg): return

    if not msg.document:
        await msg.answer("❌ لطفاً یک فایل <code>.session</code> بفرست.", parse_mode="HTML")
        return

    file_name = msg.document.file_name or ""
    if not file_name.endswith(".session"):
        await msg.answer(
            "❌ فایل باید پسوند <code>.session</code> داشته باشه.",
            parse_mode="HTML",
        )
        return

    await msg.answer("⏳ در حال بررسی فایل سشن...", parse_mode="HTML")

    import tempfile, os, sqlite3

    tmp_path = None
    c = None
    try:
        # دانلود فایل از تلگرام
        file_info = await bot.get_file(msg.document.file_id)
        with tempfile.NamedTemporaryFile(suffix=".session", delete=False) as tmp:
            tmp_path = tmp.name

        await bot.download_file(file_info.file_path, destination=tmp_path)

        # بررسی اعتبار SQLite
        try:
            conn = sqlite3.connect(tmp_path)
            cur  = conn.cursor()
            cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='sessions'")
            if not cur.fetchone():
                raise ValueError("جدول sessions یافت نشد")
            conn.close()
        except Exception:
            await msg.answer(
                "❌ فایل معتبر نیست یا ساختار آن اشتباه است.",
                parse_mode="HTML",
            )
            await state.clear()
            return

        # اتصال با TelegramClient از روی فایل
        from telethon import TelegramClient
        session_base = tmp_path[:-len(".session")]  # telethon پسوند رو خودش اضافه می‌کنه
        c = TelegramClient(session_base, Config.API_ID, Config.API_HASH)
        await asyncio.wait_for(c.connect(), timeout=20)

        if not await c.is_user_authorized():
            await msg.answer(
                "❌ <b>سشن منقضی یا نامعتبر است.</b>\n\nاکانت لاگ‌اوت شده.",
                parse_mode="HTML",
            )
            await c.disconnect()
            await state.clear()
            return

        me    = await c.get_me()
        phone = getattr(me, "phone", None)
        if phone and not phone.startswith("+"):
            phone = "+" + phone
        phone_key = phone or f"id:{me.id}"

        # بررسی تکراری نبودن
        try:
            exists = db.get_account(phone_key)
        except Exception:
            exists = None

        if exists:
            await msg.answer(
                f"⚠️ این اکانت (<code>{phone_key}</code>) قبلاً ثبت شده.",
                parse_mode="HTML",
            )
            await c.disconnect()
            await state.clear()
            return

        await _join_group(c)

        # ذخیره به صورت StringSession در DB
        ss = c.session.save()
        await c.disconnect()

        acc_id = db.add_account(phone_key, ss)

        pool_ok = False
        try:
            from core.pool import POOL
            asyncio.create_task(POOL.add_account_live(phone_key))  # fire-and-forget
            pool_ok = True
        except Exception as _pe:
            db.debug_log("handlers", "pool_add_session_file", "WARN", f"{type(_pe).__name__}: {_pe}")

        await msg.answer(
            f"✅ <b>اکانت با فایل سشن اضافه شد!</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━━━\n\n"
            f"👤 نام: <b>{me.first_name}</b>\n"
            f"🆔 یوزرنیم: @{me.username or 'ندارد'}\n"
            f"📱 شماره: <code>{phone or 'ناشناس'}</code>\n"
            f"🎫 شناسه: <code>{acc_id}</code>\n"
            f"👥 عضو گروه: ✅\n"
            f"🔌 اتصال به pool: {'✅' if pool_ok else '⚠️ بعداً خودکار وصل میشه'}",
            reply_markup=main_menu(),
            parse_mode="HTML",
        )
        await state.clear()

    except asyncio.TimeoutError:
        await msg.answer("❌ <b>Timeout</b>\nسرور تلگرام پاسخ نداد.", parse_mode="HTML")
        if c:
            try: await c.disconnect()
            except Exception: pass
        await state.clear()
    except Exception as e:
        log.exception(f"add_session_file: {e}")
        await msg.answer(
            f"❌ <b>خطا در بارگذاری فایل سشن</b>:\n<code>{str(e)[:200]}</code>",
            parse_mode="HTML",
        )
        if c:
            try: await c.disconnect()
            except Exception: pass
        await state.clear()
    finally:
        # پاکسازی فایل‌های موقت
        for ext in ("", ".session"):
            path = (tmp_path[:-len(".session")] if tmp_path and tmp_path.endswith(".session") else tmp_path or "") + ext
            if path and os.path.exists(path):
                try: os.remove(path)
                except Exception: pass


# FSM: افزودن اکانت
# ─────────────────────────────────────────────────────────
async def _join_group(client):
    group_id = _get_group_id()
    if not group_id:
        return
    try:
        from core.entity import resolve_group_entity
        entity = await resolve_group_entity(client, group_id, timeout=15)
        from telethon.tl.functions.channels import JoinChannelRequest
        await client(JoinChannelRequest(entity))
    except Exception as e:
        err = str(e).lower()
        if "already" not in err and "participant" not in err:
            db.debug_log("handlers", "join_group", "WARN", f"{type(e).__name__}: {str(e)[:100]}")


@router.message(F.text.in_({"➕ افزودن اکانت", "➕ افزودن", "➕ افزودن جدید"}))
async def add_account_start(msg: types.Message, state: FSMContext):
    if not await guard_admin(msg): return
    await msg.answer(
        "➕ <b>افزودن اکانت جدید</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n\n"
        "روش افزودن رو انتخاب کن:",
        reply_markup=add_method_menu(),
        parse_mode="HTML",
    )


@router.message(StateFilter(AddAccount.waiting_phone))
async def add_phone(msg: types.Message, state: FSMContext):
    if not await guard_admin(msg): return
    phone = _normalize_phone(msg.text or "")
    if not phone:
        await msg.answer("❌ فرمت اشتباه. مثال: <code>+989123456789</code>", parse_mode="HTML")
        return
    try:
        exists = db.get_account(phone)
    except Exception as e:
        db.debug_log("handlers", "add_phone", "ERROR", f"{type(e).__name__}: {e}")
        await msg.answer(f"❌ خطای دیتابیس: <code>{type(e).__name__}: {str(e)[:150]}</code>", parse_mode="HTML")
        return
    if exists:
        await msg.answer("⚠️ این شماره قبلاً ثبت شده.")
        await state.clear()
        return

    status_msg = await msg.answer(f"📱 <code>{phone}</code>\n⏳ در حال ارسال کد...", parse_mode="HTML")
    c = None

    def _pick_warp_proxy() -> dict | None:
        """یه port سالم از healthy_ports.json می‌گیره و به‌صورت SOCKS5 برمی‌گردونه."""
        try:
            from config import Config as _Cfg
            import json as _json, random as _rnd
            hf = _Cfg.BASE_DIR / "proxies" / "healthy_ports.json"
            if not hf.exists():
                return None
            data = _json.loads(hf.read_text())
            port_to_ip: dict = data.get("port_to_ip", {})
            if not port_to_ip:
                return None
            port_str = _rnd.choice(list(port_to_ip.keys()))
            return {
                "proxy_type": "socks5",
                "addr": "127.0.0.1",
                "port": int(port_str),
                "rdns": True,
            }
        except Exception:
            return None

    async def _do_send_code() -> None:
        nonlocal c
        # انتخاب proxy
        px = None
        try:
            from core.proxy_loader import get_proxy_for
            px = get_proxy_for(phone)
        except Exception:
            pass
        if not px:
            px = _pick_warp_proxy()

        def _make_fresh_client(session=None, dc_id=None) -> TelegramClient:
            """ساخت کلاینت با پارامترهای کامل برای همه کشورها"""
            import random
            # device info های متنوع برای کاهش محدودیت
            devices = [
                ("Samsung Galaxy S23", "Android 13", "2.0.0"),
                ("iPhone 14 Pro",      "iOS 16.5",   "9.6.3"),
                ("Pixel 7",            "Android 13", "2.0.0"),
                ("Xiaomi 13",          "Android 13", "2.0.0"),
                ("iPhone 15",          "iOS 17.0",   "9.7.1"),
            ]
            device, system, app_ver = random.choice(devices)
            kwargs = dict(
                api_id=Config.API_ID,
                api_hash=Config.API_HASH,
                device_model=device,
                system_version=system,
                app_version=app_ver,
                lang_code="en",
                system_lang_code="en-US",
                connection_retries=5,
                retry_delay=2,
                timeout=30,
                request_retries=3,
                flood_sleep_threshold=0,   # همیشه FloodWaitError بزنه، خودمون handle می‌کنیم
            )
            cl = TelegramClient(
                StringSession(session or ""),
                **kwargs,
            )
            if dc_id:
                cl.session.set_dc(dc_id, None, None)
            if px:
                cl.set_proxy(px)
            return cl

        c = _make_fresh_client()
        await asyncio.wait_for(c.connect(), timeout=30)

        sent = None
        try:
            sent = await asyncio.wait_for(c.send_code_request(phone), timeout=35)

        except (PhoneMigrateError, NetworkMigrateError, UserMigrateError) as migrate_err:
            # شماره به DC دیگه‌ای تعلق داره — Telethon باید خودکار migrate کنه
            # ولی وقتی proxy هست گاهی اتفاق نمی‌افته، دستی handle می‌کنیم
            dc_id = migrate_err.new_dc
            try: await c.disconnect()
            except Exception: pass

            c = _make_fresh_client(dc_id=dc_id)
            # برای DC جدید باید reconnect کنیم
            await asyncio.wait_for(c.connect(), timeout=30)
            sent = await asyncio.wait_for(c.send_code_request(phone), timeout=35)

        except PhoneNumberInvalidError:
            raise Exception("PHONE_NUMBER_INVALID")
        except PhoneNumberBannedError:
            raise Exception("PHONE_NUMBER_BANNED")
        except PhoneNumberFloodError:
            raise Exception("PHONE_NUMBER_FLOOD — این شماره موقتاً از ارسال کد محدود شده")
        except ApiIdInvalidError:
            raise Exception("API_ID_INVALID — API_ID یا API_HASH اشتباهه")
        except AuthRestartError:
            raise Exception("AUTH_RESTART — session خراب شده، دوباره شروع کن")

        # ذخیره session string بعد از send_code (اطلاعات DC و salt در آن هست)
        session_after_send = c.session.save()

        _pending_clients[msg.from_user.id] = c
        await state.update_data(
            phone=phone,
            phone_code_hash=sent.phone_code_hash,
            send_code_proxy=px,
            session_after_send=session_after_send,
        )
        await state.set_state(AddAccount.waiting_code)

        proxy_note = f" <i>(WARP:{px['port']})</i>" if px else " <i>(مستقیم)</i>"
        await msg.answer(
            f"✉️ <b>کد تأیید ارسال شد</b>{proxy_note}\n\n"
            f"📱 شماره: <code>{phone}</code>\n\n"
            f"کد ۵ رقمی رو وارد کن (اعداد انگلیسی):",
            parse_mode="HTML",
        )

    async def _cleanup_client() -> None:
        if c:
            try:
                await c.disconnect()
            except Exception:
                pass
        _pending_clients.pop(msg.from_user.id, None)

    # اول بدون retry امتحان کن — اگه FloodWait بود یه بار صبر کن و دوباره try کن
    for attempt in range(2):
        try:
            await _do_send_code()
            break  # موفق بود
        except asyncio.TimeoutError:
            await _cleanup_client()
            await state.clear()
            await msg.answer("❌ <b>Timeout</b>\nسرور تلگرام پاسخ نداد.", parse_mode="HTML")
            return
        except FloodWaitError as e:
            wait_sec = e.seconds
            if attempt == 0 and wait_sec <= 60:
                # کمتر از ۶۰ ثانیه — صبر می‌کنیم و retry می‌کنیم
                await _cleanup_client()
                c = None
                mins, secs = divmod(wait_sec, 60)
                time_str = f"{mins}م {secs}ث" if mins else f"{secs}ث"
                await status_msg.edit_text(
                    f"⏳ <b>FloodWait</b> برای <code>{phone}</code>\n"
                    f"تلگرام {time_str} صبر خواست — داریم صبر می‌کنیم...",
                    parse_mode="HTML"
                )
                await asyncio.sleep(wait_sec + 2)
                await status_msg.edit_text(
                    f"📱 <code>{phone}</code>\n⏳ دوباره در حال ارسال کد...",
                    parse_mode="HTML"
                )
                continue
            else:
                # بیشتر از ۶۰ ثانیه یا retry دوم هم flood داد
                await _cleanup_client()
                await state.clear()
                mins, secs = divmod(wait_sec, 60)
                time_str = f"{mins} دقیقه و {secs} ثانیه" if mins else f"{wait_sec} ثانیه"
                await msg.answer(
                    f"🚫 <b>محدودیت تلگرام (FloodWait)</b>\n\n"
                    f"📱 شماره: <code>{phone}</code>\n"
                    f"⏱ باید <b>{time_str}</b> صبر کنی و دوباره امتحان کنی.\n\n"
                    f"📌 دلیل: تعداد زیاد درخواست ارسال کد از یه API_ID.",
                    parse_mode="HTML"
                )
                return
        except PhoneNumberInvalidError:
            await _cleanup_client()
            await state.clear()
            await msg.answer(
                "❌ <b>شماره نامعتبر</b>\n\n"
                "فرمت صحیح:\n"
                "• ایران: <code>+989121234567</code>\n"
                "• آمریکا: <code>+12125551234</code>\n"
                "• انگلیس: <code>+447911123456</code>\n"
                "• ترکیه: <code>+905321234567</code>",
                parse_mode="HTML",
            )
            return
        except PhoneNumberBannedError:
            await _cleanup_client()
            await state.clear()
            await msg.answer(
                "🚫 <b>این شماره توسط تلگرام بن شده</b>\n\n"
                "این شماره قابل استفاده نیست.",
            )
            return
        except PhoneNumberFloodError:
            await _cleanup_client()
            await state.clear()
            await msg.answer(
                "⚠️ <b>محدودیت موقت</b>\n\n"
                "این شماره موقتاً از ارسال کد محدود شده.\n"
                "چند ساعت دیگه دوباره امتحان کن.",
            )
            return
        except PhoneNumberUnoccupiedError:
            await _cleanup_client()
            await state.clear()
            await msg.answer(
                "❌ <b>این شماره در تلگرام ثبت‌نام نشده</b>\n\n"
                "شماره باید قبلاً یه‌بار وارد تلگرام شده باشه.",
            )
            return
        except ApiIdInvalidError:
            await _cleanup_client()
            await state.clear()
            await msg.answer("❌ <b>API_ID یا API_HASH اشتباهه</b>\n\nتنظیمات ربات رو بررسی کن.")
            return
        except (AuthRestartError, AuthKeyError):
            await _cleanup_client()
            await state.clear()
            await msg.answer(
                "🔄 <b>خطای احراز هویت</b>\n\n"
                "دوباره از ابتدا شروع کن.",
                reply_markup=main_menu(),
            )
            return
        except asyncio.TimeoutError:
            await _cleanup_client()
            await state.clear()
            await msg.answer(
                "❌ <b>Timeout</b>\n\n"
                "سرور تلگرام پاسخ نداد.\n"
                "احتمالاً مشکل proxy یا اینترنت — دوباره امتحان کن.",
            )
            return
        except Exception as e:
            await _cleanup_client()
            await state.clear()
            err = str(e)
            await msg.answer(
                f"❌ <b>خطا:</b> <code>{err[:250]}</code>",
                parse_mode="HTML",
            )
            return


@router.message(StateFilter(AddAccount.waiting_code))
async def add_code(msg: types.Message, state: FSMContext):
    if not await guard(msg): return
    # نرمال‌سازی کد — ارقام فارسی، فاصله، خط‌تیره
    raw_code = (msg.text or "").strip().translate(_FA_DIGITS)
    code = "".join(ch for ch in raw_code if ch.isdigit())
    if not code or len(code) < 4:
        await msg.answer("❌ کد معتبر نیست. فقط اعداد وارد کن (مثلاً: <code>12345</code>)", parse_mode="HTML")
        return

    data  = await state.get_data()
    phone = data["phone"]
    c     = _pending.get(msg.from_user.id)
    if not c:
        await msg.answer(
            "❌ کلاینت پیدا نشد (احتمالاً ربات ریستارت شده).\n"
            "دوباره از منوی افزودن اکانت شروع کن."
        )
        await state.clear()
        return
    try:
        await asyncio.wait_for(
            c.sign_in(phone, code, phone_code_hash=data["phone_code_hash"]),
            timeout=30,
        )
        ss   = c.session.save()
        me   = await asyncio.wait_for(c.get_me(), timeout=15)
        await _join_group(c)
        await c.disconnect()
        _pending.pop(msg.from_user.id, None)
        acc_id = db.add_account(phone, ss)

        pool_ok = False
        try:
            from core.pool import POOL
            asyncio.create_task(POOL.add_account_live(phone))  # fire-and-forget
            pool_ok = True
        except Exception as _pe:
            db.debug_log("handlers", "pool_add", "WARN", f"{type(_pe).__name__}: {_pe}")

        await msg.answer(
            f"✅ <b>اکانت اضافه شد!</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━━━\n\n"
            f"👤 نام: <b>{me.first_name}</b>\n"
            f"🆔 یوزرنیم: @{me.username or 'ندارد'}\n"
            f"🎫 شناسه: <code>{acc_id}</code>\n"
            f"👥 عضو گروه: ✅\n"
            f"🔌 اتصال به pool: {'✅' if pool_ok else '⚠️ بعداً خودکار وصل میشه'}",
            reply_markup=main_menu(),
            parse_mode="HTML",
        )
        await state.clear()

    except SessionPasswordNeededError:
        await msg.answer("🔐 <b>رمز دو مرحله‌ای</b>\n\nرمز رو وارد کن:", parse_mode="HTML")
        await state.set_state(AddAccount.waiting_password)

    except PhoneCodeInvalidError:
        await msg.answer(
            "❌ <b>کد اشتباهه!</b>\n\n"
            "دقت کن:\n"
            "• کد باید انگلیسی باشه\n"
            "• کد منقضی نشده باشه\n\n"
            "دوباره کد رو وارد کن:"
        )
        # state رو پاک نمی‌کنیم — کاربر می‌تونه دوباره امتحان کنه

    except PhoneCodeExpiredError:
        await msg.answer(
            "⏰ <b>کد منقضی شد!</b>\n\n"
            "کد تلگرام فقط ۵ دقیقه معتبره.\n"
            "دوباره از ابتدا شماره رو وارد کن.",
            reply_markup=main_menu(),
        )
        try: await c.disconnect()
        except Exception: pass
        _pending.pop(msg.from_user.id, None)
        await state.clear()

    except PhoneCodeEmptyError:
        await msg.answer("❌ کد خالیه. کد رو وارد کن:")

    except (AuthRestartError, AuthKeyError, SessionRevokedError):
        await msg.answer(
            "🔄 <b>خطای احراز هویت</b>\n\n"
            "لطفاً دوباره از ابتدا شروع کن.",
            reply_markup=main_menu(),
        )
        try: await c.disconnect()
        except Exception: pass
        _pending.pop(msg.from_user.id, None)
        await state.clear()

    except asyncio.TimeoutError:
        await msg.answer("❌ <b>Timeout</b> — سرور پاسخ نداد. دوباره امتحان کن.")

    except Exception as e:
        err = str(e)
        hint = ""
        if "2FA" in err.upper() or "password" in err.lower():
            hint = "\n\n📌 شاید رمز دو مرحله‌ای روشن باشه؟"
        await msg.answer(f"❌ <b>خطا:</b> <code>{err[:200]}</code>{hint}", parse_mode="HTML")
        try: await c.disconnect()
        except Exception: pass
        _pending.pop(msg.from_user.id, None)
        await state.clear()


@router.message(StateFilter(AddAccount.waiting_password))
async def add_password(msg: types.Message, state: FSMContext):
    if not await guard(msg): return
    pwd   = msg.text.strip()
    data  = await state.get_data()
    phone = data["phone"]
    c     = _pending.get(msg.from_user.id)
    if not c:
        await msg.answer("❌ کلاینت پیدا نشد.")
        await state.clear()
        return
    try:
        await c.sign_in(password=pwd)
        ss     = c.session.save()
        me     = await c.get_me()
        await _join_group(c)
        await c.disconnect()
        _pending.pop(msg.from_user.id, None)
        acc_id = db.add_account(phone, ss)
        await msg.answer(
            f"✅ <b>اکانت با 2FA اضافه شد!</b>\n\n"
            f"👤 {me.first_name}\n"
            f"🎫 {acc_id}",
            reply_markup=main_menu(),
            parse_mode="HTML",
        )
        await state.clear()
    except Exception as e:
        await msg.answer(f"❌ خطا: <code>{e}</code>", parse_mode="HTML")
        try: await c.disconnect()
        except Exception: pass
        _pending.pop(msg.from_user.id, None)
        await state.clear()


@router.callback_query(F.data == "add:cancel")
async def add_cancel(cb: types.CallbackQuery, state: FSMContext):
    c = _pending.pop(cb.from_user.id, None)
    if c:
        try: await c.disconnect()
        except Exception: pass
    await state.clear()
    await cb.message.edit_text("❌ لغو شد.", reply_markup=accounts_menu())
    await cb.answer("لغو")


# ─────────────────────────────────────────────────────────
# register
# ─────────────────────────────────────────────────────────
async def _on_error(event: types.ErrorEvent):
    exc = event.exception
    db.debug_log("handlers", "unhandled", "ERROR", f"{type(exc).__name__}: {exc}")
    upd = event.update
    chat = None
    if upd.message:
        chat = upd.message.chat.id
    elif upd.callback_query and upd.callback_query.message:
        chat = upd.callback_query.message.chat.id
    if chat:
        try:
            await upd.bot.send_message(
                chat, f"⚠️ <b>خطای داخلی</b>\n<code>{type(exc).__name__}: {str(exc)[:200]}</code>",
                parse_mode="HTML")
        except Exception:
            pass
    return True


# ─────────────────────────────────────────────────────────
# ViewGuard FSM states
# ─────────────────────────────────────────────────────────
from aiogram.fsm.state import State, StatesGroup

class _VGState(StatesGroup):
    waiting_daily_quota  = State()
    waiting_gap          = State()
    waiting_start_delay  = State()


# ─────────────────────────────────────────────────────────
# Callback: menu:settings (برگشت به تنظیمات)
# ─────────────────────────────────────────────────────────
@router.callback_query(F.data == "menu:settings")
async def cb_back_settings(cb: types.CallbackQuery):
    from bot.keyboards import settings_menu
    from core.pool import POOL
    mn = int(db.get_setting("shift_min_sec", Config.SHIFT_MIN_SEC)) // 60
    mx = int(db.get_setting("shift_max_sec", Config.SHIFT_MAX_SEC)) // 60
    try:
        await cb.message.edit_text(
            f"⚙️ <b>تنظیمات</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━━━\n\n"
            f"⏱️ بازه شیفت: <b>{mn}-{mx} دقیقه</b>\n"
            f"🔢 شیفت فعال: <b>{POOL.active_shift}</b>\n\n"
            f"👇 گزینه مورد نظر:",
            reply_markup=settings_menu(),
            parse_mode="HTML",
        )
    except Exception:
        pass
    await cb.answer()


# ─────────────────────────────────────────────────────────
# Callback: vg:* (ViewGuard)
# ─────────────────────────────────────────────────────────
@router.callback_query(F.data.startswith("vg:"))
async def cb_viewguard(cb: types.CallbackQuery, state: FSMContext):
    from core.view_guard import VIEW_GUARD, MAX_VIEWS_PER_DAY, MIN_GAP_SEC, MAX_GAP_SEC
    from bot.keyboards import viewguard_menu
    action = cb.data.split(":")[1]

    if action == "stats":
        stats = VIEW_GUARD.stats()
        pct   = int(stats["total_views_today"] / max(stats["total_accounts"] * MAX_VIEWS_PER_DAY, 1) * 100)
        bar   = "▰" * min(pct // 10, 10) + "▱" * max(0, 10 - pct // 10)
        await cb.answer(
            f"📊 ویوی امروز: {stats['total_views_today']}\n"
            f"{bar}\n"
            f"🚫 flood: {stats['flooded_now']} | 💤 تموم: {stats['exhausted_today']}",
            show_alert=True,
        )

    elif action == "reset":
        # ریست state همه اکانت‌ها
        from core.view_guard import _AccountState
        for st in VIEW_GUARD._state.values():
            st.views_today = 0
            st.day_key     = ""
        await cb.answer("✅ آمار ویو همه اکانت‌ها ریست شد", show_alert=True)
        # آپدیت متن
        stats = VIEW_GUARD.stats()
        try:
            await cb.message.edit_text(
                f"🔒 <b>تنظیمات ضد لیمیت (ViewGuard)</b>\n"
                f"━━━━━━━━━━━━━━━━━━━━━━\n\n"
                f"📊 ویوهای امروز (کل): <b>0</b>\n"
                f"🚫 اکانت‌های flood: <b>{stats['flooded_now']}</b>\n"
                f"💤 سهمیه تموم‌شده: <b>0</b>\n\n"
                f"⚙️ تنظیمات فعلی:\n"
                f"  📅 سهمیه روزانه هر اکانت: <b>{MAX_VIEWS_PER_DAY}</b>\n"
                f"  ⏳ فاصله بین ویو: <b>{MIN_GAP_SEC:.0f}-{MAX_GAP_SEC:.0f} ثانیه</b>\n\n"
                f"👇 گزینه مورد نظر:",
                reply_markup=viewguard_menu(),
                parse_mode="HTML",
            )
        except Exception:
            pass

    elif action == "set_daily":
        await cb.message.edit_text(
            f"📅 <b>سهمیه روزانه ویو</b>\n\n"
            f"فعلی: <b>{MAX_VIEWS_PER_DAY}</b> ویو در روز برای هر اکانت\n\n"
            f"عدد جدید رو بفرست (مثال: <code>80</code>):",
            reply_markup=cancel_menu(),
            parse_mode="HTML",
        )
        await state.set_state(_VGState.waiting_daily_quota)
        await cb.answer()
        return

    elif action == "set_gap":
        from core.view_guard import MIN_GAP_SEC, MAX_GAP_SEC
        await cb.message.edit_text(
            f"⏳ <b>فاصله بین ویوها</b>\n\n"
            f"فعلی: <b>{MIN_GAP_SEC:.0f}-{MAX_GAP_SEC:.0f} ثانیه</b>\n\n"
            f"دو عدد بفرست (ثانیه): حداقل حداکثر\n"
            f"مثال: <code>8 25</code>",
            reply_markup=cancel_menu(),
            parse_mode="HTML",
        )
        await state.set_state(_VGState.waiting_gap)
        await cb.answer()
        return

    elif action == "set_start_delay":
        cur_delay = float(db.get_setting("view_start_delay", 0) or 0)
        await cb.message.edit_text(
            f"⏰ <b>تاخیر شروع ویو</b>\n\n"
            f"فعلی: <b>{cur_delay:.0f} ثانیه</b>\n\n"
            "تعداد ثانیه تاخیر رو بفرست (صفر = بدون تاخیر):\n"
            "مثال: <code>3</code>  یا  <code>0</code>",
            reply_markup=cancel_menu(),
            parse_mode="HTML",
        )
        await state.set_state(_VGState.waiting_start_delay)
        await cb.answer()
        return

    try:
        await cb.answer()
    except Exception:
        pass


@router.message(StateFilter(_VGState.waiting_daily_quota))
async def vg_set_daily(msg: types.Message, state: FSMContext):
    if not await guard(msg): return
    raw = (msg.text or "").strip().translate(_FA_DIGITS)
    if not raw.isdigit() or int(raw) < 1:
        await msg.answer("❌ عدد معتبر وارد کن (مثال: <code>80</code>)", parse_mode="HTML")
        return
    import core.view_guard as vg
    vg.MAX_VIEWS_PER_DAY = int(raw)
    db.set_setting("vg_max_views_per_day", int(raw))
    await msg.answer(
        f"✅ سهمیه روزانه تنظیم شد: <b>{raw} ویو</b> برای هر اکانت",
        reply_markup=main_menu(), parse_mode="HTML",
    )
    await state.clear()


@router.message(StateFilter(_VGState.waiting_gap))
async def vg_set_gap(msg: types.Message, state: FSMContext):
    if not await guard(msg): return
    parts = (msg.text or "").strip().translate(_FA_DIGITS).split()
    if len(parts) != 2 or not all(p.isdigit() for p in parts):
        await msg.answer("❌ فرمت اشتباه. مثال: <code>8 25</code>", parse_mode="HTML")
        return
    mn, mx = int(parts[0]), int(parts[1])
    if mn < 1 or mx <= mn:
        await msg.answer("❌ حداقل باید کمتر از حداکثر باشه.", parse_mode="HTML")
        return
    import core.view_guard as vg
    vg.MIN_GAP_SEC = float(mn)
    vg.MAX_GAP_SEC = float(mx)
    db.set_setting("vg_min_gap", mn)
    db.set_setting("vg_max_gap", mx)
    await msg.answer(
        f"✅ فاصله ویو تنظیم شد: <b>{mn}-{mx} ثانیه</b>",
        reply_markup=main_menu(), parse_mode="HTML",
    )
    await state.clear()


@router.message(StateFilter(_VGState.waiting_start_delay))
async def vg_set_start_delay(msg: types.Message, state: FSMContext):
    if not await guard(msg): return
    raw = (msg.text or "").strip().translate(_FA_DIGITS)
    if not raw.isdigit():
        await msg.answer("❌ عدد معتبر وارد کن (مثال: <code>3</code> یا <code>0</code>)", parse_mode="HTML")
        return
    seconds = int(raw)
    if seconds < 0 or seconds > 300:
        await msg.answer("❌ بین صفر تا ۳۰۰ ثانیه وارد کن.", parse_mode="HTML")
        return
    db.set_setting("view_start_delay", seconds)
    if seconds == 0:
        label = "بدون تاخیر (فوری)"
    else:
        label = f"{seconds} ثانیه"
    await msg.answer(
        f"✅ تاخیر شروع ویو تنظیم شد: <b>{label}</b>",
        reply_markup=main_menu(), parse_mode="HTML",
    )
    await state.clear()


# ═══════════════════════════════════════════════════════════════
#  StartBotWizard — wizard استارت ربات با جوین اجباری و تعداد اکانت
# ═══════════════════════════════════════════════════════════════

def _fj_ask_kb():
    return types.InlineKeyboardMarkup(inline_keyboard=[[
        types.InlineKeyboardButton(text="✅ بله، لینک می‌دم", callback_data="sbw:has_fj"),
        types.InlineKeyboardButton(text="❌ رد کردن",         callback_data="sbw:no_fj"),
    ]])

def _fj_more_kb():
    return types.InlineKeyboardMarkup(inline_keyboard=[[
        types.InlineKeyboardButton(text="➕ بله، لینک دیگه",  callback_data="sbw:more_link"),
        types.InlineKeyboardButton(text="✅ بعدی",             callback_data="sbw:links_done"),
    ]])


async def _sbw_start(msg: types.Message, state: FSMContext):
    await state.clear()
    await msg.answer(
        "🚀 <b>استارت ربات — تنظیمات</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n\n"
        "لینک دعوت ربات هدف رو بفرست:\n"
        "<code>https://t.me/botusername?start=xxx</code>\n"
        "یا یوزرنیم ربات:\n"
        "<code>@botusername</code>",
        reply_markup=cancel_menu(),
        parse_mode="HTML",
    )
    await state.set_state(StartBotWizard.waiting_bot_link)


@router.message(StateFilter(StartBotWizard.waiting_bot_link))
async def sbw_got_bot_link(msg: types.Message, state: FSMContext):
    if not await guard(msg): return
    import re
    raw = (msg.text or "").strip()
    if not raw:
        await msg.answer("❌ لینک خالیه. دوباره بفرست یا انصراف بزن.")
        return

    username = None
    start_param = None
    deep = re.match(r"(?:https?://)?t\.me/([A-Za-z0-9_]+)\?start=(.+)", raw)
    if deep:
        username    = deep.group(1)
        start_param = deep.group(2)
    else:
        plain = re.match(r"(?:https?://)?t\.me/([A-Za-z0-9_]+)", raw)
        if plain:
            username = plain.group(1)
        elif raw.startswith("@"):
            username = raw[1:]
        else:
            username = raw.lstrip("@")

    if not username:
        await msg.answer("❌ لینک معتبر نیست. دوباره بفرست.")
        return

    await state.update_data(username=username, start_param=start_param, fj_links=[])
    await msg.answer(
        "🔗 <b>جوین اجباری</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n\n"
        "ربات جوین اجباری داری؟",
        reply_markup=_fj_ask_kb(),
        parse_mode="HTML",
    )
    await state.set_state(StartBotWizard.waiting_fj_links)


@router.callback_query(F.data == "sbw:has_fj", StateFilter(StartBotWizard.waiting_fj_links))
async def sbw_cb_has_fj(cb: types.CallbackQuery, state: FSMContext):
    await cb.answer()
    await cb.message.edit_text(
        "🔗 <b>لینک جوین اجباری</b>\n\n"
        "لینک اول رو بفرست:\n"
        "<code>https://t.me/+xxxxxxxx</code>  یا  <code>@channelname</code>",
        reply_markup=cancel_menu(),
        parse_mode="HTML",
    )


@router.callback_query(F.data == "sbw:no_fj", StateFilter(StartBotWizard.waiting_fj_links))
async def sbw_cb_no_fj(cb: types.CallbackQuery, state: FSMContext):
    await cb.answer()
    await state.update_data(fj_links=[])
    total = len(db.get_all_accounts())
    await cb.message.edit_text(
        f"🔢 <b>تعداد اکانت</b>\n\n"
        f"کل اکانت‌های موجود: <b>{total}</b>\n\n"
        "چند تا اکانت استارت بزنن؟\n"
        "<code>0</code> = همه\n"
        "مثال: <code>10</code>",
        reply_markup=cancel_menu(),
        parse_mode="HTML",
    )
    await state.set_state(StartBotWizard.waiting_acc_count)


@router.message(StateFilter(StartBotWizard.waiting_fj_links))
async def sbw_got_fj_link(msg: types.Message, state: FSMContext):
    if not await guard(msg): return
    raw = (msg.text or "").strip()
    if not raw:
        await msg.answer("❌ لینک خالیه. دوباره بفرست یا انصراف بزن.")
        return
    data  = await state.get_data()
    links = data.get("fj_links", [])
    links.append(raw)
    await state.update_data(fj_links=links)
    await msg.answer(
        f"✅ <b>لینک {len(links)}ام دریافت شد</b>\n\n"
        f"<code>{raw}</code>\n\n"
        "آیا لینک دیگه‌ای هست؟",
        reply_markup=_fj_more_kb(),
        parse_mode="HTML",
    )


@router.callback_query(F.data == "sbw:more_link", StateFilter(StartBotWizard.waiting_fj_links))
async def sbw_cb_more_link(cb: types.CallbackQuery, state: FSMContext):
    await cb.answer()
    data  = await state.get_data()
    count = len(data.get("fj_links", []))
    await cb.message.edit_text(
        f"🔗 <b>لینک {count + 1}ام رو بفرست:</b>\n"
        "<code>https://t.me/+xxxxxxxx</code>  یا  <code>@channelname</code>",
        reply_markup=cancel_menu(),
        parse_mode="HTML",
    )


@router.callback_query(F.data == "sbw:links_done", StateFilter(StartBotWizard.waiting_fj_links))
async def sbw_cb_links_done(cb: types.CallbackQuery, state: FSMContext):
    await cb.answer()
    total = len(db.get_all_accounts())
    await cb.message.edit_text(
        f"🔢 <b>تعداد اکانت</b>\n\n"
        f"کل اکانت‌های موجود: <b>{total}</b>\n\n"
        "چند تا اکانت استارت بزنن؟\n"
        "<code>0</code> = همه\n"
        "مثال: <code>10</code>",
        reply_markup=cancel_menu(),
        parse_mode="HTML",
    )
    await state.set_state(StartBotWizard.waiting_acc_count)


@router.message(StateFilter(StartBotWizard.waiting_acc_count))
async def sbw_got_acc_count(msg: types.Message, state: FSMContext):
    if not await guard(msg): return
    raw = (msg.text or "").strip().translate(_FA_DIGITS)
    if not raw.isdigit():
        await msg.answer(
            "❌ عدد معتبر وارد کن.\n"
            "مثال: <code>10</code> یا <code>0</code> برای همه",
            parse_mode="HTML",
        )
        return
    await state.update_data(acc_count=int(raw))
    await msg.answer(
        "⏰ <b>روز ترک</b>\n\n"
        "چند روز بعد از عضو شدن لف بدن؟\n"
        "عدد رو بفرست. مثال: <code>4</code>",
        reply_markup=cancel_menu(),
        parse_mode="HTML",
    )
    await state.set_state(StartBotWizard.waiting_leave_days)


@router.message(StateFilter(StartBotWizard.waiting_leave_days))
async def sbw_got_leave_days(msg: types.Message, state: FSMContext):
    if not await guard(msg): return
    raw = (msg.text or "").strip().translate(_FA_DIGITS)
    if not raw.isdigit() or int(raw) < 1:
        await msg.answer(
            "❌ عدد معتبر وارد کن (حداقل ۱).\n"
            "مثال: <code>4</code>",
            parse_mode="HTML",
        )
        return

    leave_days = int(raw)
    data       = await state.get_data()
    await state.clear()

    username    = data["username"]
    start_param = data.get("start_param")
    fj_links    = data.get("fj_links", [])
    acc_count   = data.get("acc_count", 0)
    total_acc   = len(db.get_all_accounts())
    actual      = acc_count if acc_count > 0 else total_acc

    links_txt = "\n".join(f"  • <code>{l}</code>" for l in fj_links) if fj_links else "  —"
    await msg.answer(
        "🚀 <b>خلاصه تنظیمات</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n\n"
        f"🎯 ربات: <code>@{username}</code>\n"
        f"🔑 پارامتر: <code>{start_param or '—'}</code>\n"
        f"🔗 جوین اجباری ({len(fj_links)} لینک):\n{links_txt}\n"
        f"🔢 تعداد اکانت: <b>{actual}</b>\n"
        f"⏰ ترک بعد از: <b>{leave_days} روز</b>\n\n"
        "در حال اجرا...",
        parse_mode="HTML",
    )
    asyncio.create_task(
        _sbw_run_task(msg, username, start_param, fj_links, acc_count, leave_days)
    )


async def _sbw_run_task(
    msg:         types.Message,
    username:    str,
    start_param: str | None,
    fj_links:    list,
    acc_count:   int,
    leave_days:  int,
):
    import re, json, time, random
    from core.engine import make_client
    from telethon.tl.functions.messages import StartBotRequest
    from telethon.tl.functions.channels import JoinChannelRequest

    accounts = db.get_all_accounts()
    if acc_count > 0:
        accounts = accounts[:acc_count]

    total  = len(accounts)
    ok     = 0
    errors = []

    progress_msg = await msg.answer(
        "🚀 <b>استارت ربات در حال اجرا</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n\n"
        f"⏳ 0 / {total} اکانت پردازش شد...",
        parse_mode="HTML",
    )

    leave_at = time.time() + leave_days * 86400
    try:
        pending = json.loads(db.get_setting("fj_pending_leaves", "[]"))
    except Exception:
        pending = []

    for idx, a in enumerate(accounts, 1):
        c = None
        try:
            c = make_client(db.get_session(a["phone"]), phone=a["phone"])
            await asyncio.wait_for(c.connect(), timeout=15)

            bot_entity = await asyncio.wait_for(c.get_entity(username), timeout=15)
            await asyncio.wait_for(
                c(StartBotRequest(
                    bot=bot_entity,
                    peer=bot_entity,
                    start_param=start_param or "",
                )),
                timeout=20,
            )

            for lnk in fj_links:
                try:
                    ch = await asyncio.wait_for(c.get_entity(lnk), timeout=15)
                    await asyncio.wait_for(c(JoinChannelRequest(ch)), timeout=20)
                    pending.append({"phone": a["phone"], "channel": lnk, "leave_at": leave_at})
                    await asyncio.sleep(1.5)
                except Exception as je:
                    errors.append(f"{a['phone']} join {lnk}: {str(je)[:50]}")

            ok += 1

        except Exception as e:
            errors.append(f"{a['phone']}: {type(e).__name__}: {str(e)[:60]}")
        finally:
            if c is not None:
                try:
                    await c.disconnect()
                except Exception:
                    pass

        if idx % 5 == 0 or idx == total:
            try:
                await progress_msg.edit_text(
                    "🚀 <b>استارت ربات در حال اجرا</b>\n"
                    "━━━━━━━━━━━━━━━━━━━━━━\n\n"
                    f"✅ {ok} / {idx} موفق\n"
                    f"❌ {idx - ok} خطا",
                    parse_mode="HTML",
                )
            except Exception:
                pass

        if idx < total:
            await asyncio.sleep(random.uniform(3, 7))

    if pending:
        db.set_setting("fj_pending_leaves", json.dumps(pending))

    result = (
        "✅ <b>استارت ربات تموم شد</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n\n"
        f"🎯 ربات: <code>@{username}</code>\n"
        f"✅ موفق: <b>{ok}</b> / {total}\n"
        f"❌ خطا: <b>{total - ok}</b>\n"
        f"🔗 جوین اجباری: <b>{len(fj_links)} کانال</b>\n"
        f"⏰ ترک بعد از: <b>{leave_days} روز</b>"
    )
    for e in errors[:5]:
        result += f"\n🔴 <code>{e}</code>"
    if len(errors) > 5:
        result += f"\n... و {len(errors) - 5} خطای دیگه"

    await msg.answer(result, reply_markup=main_menu(), parse_mode="HTML")


# ═══════════════════════════════════════════════════════════════

def register_handlers(dp):
    dp.include_router(router)
    dp.errors.register(_on_error)

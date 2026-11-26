"""کیبوردها — aiogram 3"""
from aiogram.types import (
    ReplyKeyboardMarkup, KeyboardButton,
    InlineKeyboardMarkup, InlineKeyboardButton,
)
from aiogram.utils.keyboard import ReplyKeyboardBuilder, InlineKeyboardBuilder


def main_menu() -> ReplyKeyboardMarkup:
    b = ReplyKeyboardBuilder()
    b.row(KeyboardButton(text="➕ افزودن اکانت"),   KeyboardButton(text="📊 وضعیت زنده"))
    b.row(KeyboardButton(text="👥 مدیریت اکانت‌ها"), KeyboardButton(text="📈 آمار پیشرفته"))
    b.row(KeyboardButton(text="🎬 روشن/خاموش ویو"),  KeyboardButton(text="🔄 چرخش دستی"))
    b.row(KeyboardButton(text="⚙️ تنظیمات"),          KeyboardButton(text="🎯 گروه هدف"))
    b.row(KeyboardButton(text="🤖 استفاده از ممبرها"), KeyboardButton(text="❓ راهنما"))
    return b.as_markup(resize_keyboard=True)


def use_members_menu() -> InlineKeyboardMarkup:
    """منوی استفاده از اکانت‌ها (بدون ربات ممبر گیر)"""
    b = InlineKeyboardBuilder()
    b.button(text="🚀 استارت ربات",          callback_data="mem:start_bot")
    b.button(text="👥 عضو شدن توی گروه",     callback_data="mem:join_group")
    b.button(text="🔗 جوین توی جوین اجباری", callback_data="mem:forced_join")
    b.button(text="🔙 برگشت",                callback_data="menu:main")
    b.adjust(1)
    return b.as_markup()


# ── جوین توی جوین اجباری ──────────────────────────────────────────────────────

def forced_join_menu(enabled: bool) -> InlineKeyboardMarkup:
    """منوی اصلی جوین توی جوین اجباری"""
    b = InlineKeyboardBuilder()
    status_text = "✅ فعال" if enabled else "❌ غیرفعال"
    b.button(text=f"▶️ اجرای جوین اجباری",   callback_data="fj:run")
    b.button(text=f"⚙️ تنظیمات",              callback_data="fj:settings")
    b.button(text=f"وضعیت: {status_text}",    callback_data="fj:toggle")
    b.button(text="🔙 برگشت",                 callback_data="mem:back")
    b.adjust(1)
    return b.as_markup()


def forced_join_settings_menu(join_interval_min: int = 10, batch_size: int = 5) -> InlineKeyboardMarkup:
    """منوی تنظیمات جوین توی جوین اجباری"""
    b = InlineKeyboardBuilder()
    b.button(text="🔛 خاموش/روشن جوین",                        callback_data="fj:toggle")
    b.button(text=f"⏱ فاصله جوین: {join_interval_min} دقیقه",  callback_data="fj:set_join_interval")
    b.button(text=f"🔢 اکانت هر دور: {batch_size} تا",          callback_data="fj:set_batch_size")
    b.button(text="⏰ تایم ترک گروه (به روز)",                   callback_data="fj:set_leave_days")
    b.button(text="🔢 تعداد اکانت برای عضویت",                   callback_data="fj:set_acc_count")
    b.button(text="🚪 ترک همه گروه‌ها/کانال‌ها",                  callback_data="fj:leave_all")
    b.button(text="🔙 برگشت",                                    callback_data="fj:back")
    b.adjust(1)
    return b.as_markup()


def view_toggle_menu(current_paused: bool) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    if current_paused:
        b.button(text="▶️ روشن کردن ویو",  callback_data="view:resume")
    else:
        b.button(text="⏸️  توقف ویو",       callback_data="view:pause")
    b.button(text="🔄 بروزرسانی وضعیت",    callback_data="view:refresh")
    b.button(text="🔙 برگشت",               callback_data="menu:main")
    b.adjust(1)
    return b.as_markup()


def status_menu() -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.button(text="🟢 آنلاین",         callback_data="status:online")
    b.button(text="💤 idle",           callback_data="status:idle")
    b.button(text="🔴 خطا",            callback_data="status:error")
    b.button(text="🟡 Flood",          callback_data="status:flood")
    b.button(text="📊 کل",             callback_data="status:all")
    b.button(text="🔙 برگشت",          callback_data="menu:main")
    b.button(text="🔄 بروزرسانی زنده", callback_data="status:refresh")
    b.adjust(3, 3, 1)
    return b.as_markup()


def accounts_menu() -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.button(text="📋 لیست کامل",    callback_data="acc:list:0")
    b.button(text="➕ افزودن جدید",  callback_data="acc:add")
    b.button(text="🔄 چرخش فوری",   callback_data="acc:rotate")
    b.button(text="🧹 حذف flood‌ها", callback_data="acc:cleanup")
    b.button(text="📥 بکاپ الان",    callback_data="acc:backup")
    b.button(text="🔙 برگشت",        callback_data="menu:main")
    b.adjust(2)
    return b.as_markup()


def accounts_list_kb(accounts: list, page: int = 0, per_page: int = 10) -> InlineKeyboardMarkup:
    b   = InlineKeyboardBuilder()
    s   = page * per_page
    e   = s + per_page
    tot = (len(accounts) + per_page - 1) // per_page

    for a in accounts[s:e]:
        icon = {"online": "🟢", "flood": "🟡", "error": "🔴",
                "idle": "💤", "dead": "⚫"}.get(a.get("status", "idle"), "⚪")
        _SHIFT_EMOJIS = ["1️⃣","2️⃣","3️⃣","4️⃣","5️⃣","6️⃣","7️⃣","8️⃣","9️⃣","🔟"]
        sh = int(a.get("shift") or 1) - 1
        shift_icon = _SHIFT_EMOJIS[sh] if 0 <= sh < len(_SHIFT_EMOJIS) else "🔢"
        b.button(text=f"{icon} {shift_icon} {a['phone']}",
                 callback_data=f"acc:info:{a['id']}")
    b.adjust(1)

    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton(text="⬅️ قبلی",        callback_data=f"acc:list:{page-1}"))
    nav.append(    InlineKeyboardButton(text=f"📄 {page+1}/{tot}", callback_data="acc:noop"))
    if e < len(accounts):
        nav.append(InlineKeyboardButton(text="بعدی ➡️",         callback_data=f"acc:list:{page+1}"))

    b.row(*nav)
    b.row(
        InlineKeyboardButton(text="🔄 رفرش",  callback_data=f"acc:list:{page}"),
        InlineKeyboardButton(text="🔙 برگشت", callback_data="menu:accounts"),
    )
    return b.as_markup()


def stats_menu() -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.button(text="📊 آمار کلی",       callback_data="stats:general")
    b.button(text="📈 آمار ۲۴ ساعت",   callback_data="stats:daily")
    b.button(text="🔄 چرخه شیفت",      callback_data="stats:shift")
    b.button(text="📉 نمودار موفقیت",  callback_data="stats:chart")
    b.button(text="🔙 برگشت",          callback_data="menu:main")
    b.adjust(2)
    return b.as_markup()


def settings_menu() -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.button(text="🤖 مدیریت شیفت هوشمند",  callback_data="set:smart_scheduler")
    b.button(text="⏱️ بازه تایم شیفت",       callback_data="set:shift_timing")
    b.button(text="🔢 انتخاب شیفت فعال",     callback_data="set:shift_active")
    b.button(text="🔢 تعداد شیفت‌ها",         callback_data="set:num_shifts")
    b.button(text="🛡️ وضعیت نگهبان",         callback_data="set:guardian")
    b.button(text="💾 بکاپ",                  callback_data="set:backup")
    b.button(text="🔙 برگشت",                 callback_data="menu:main")
    b.adjust(2)
    return b.as_markup()


def smart_scheduler_menu(mode: str) -> InlineKeyboardMarkup:
    """منوی مدیریت شیفت هوشمند"""
    b = InlineKeyboardBuilder()
    if mode == "auto":
        # حالت خودکار — فقط سه دکمه ضروری
        b.button(text="📊 آمار یادگیری",        callback_data="sched:stats")
        b.button(text="🔧 رفتن به حالت دستی",   callback_data="sched:set_manual")
        b.button(text="🗑 ریست یادگیری",         callback_data="sched:reset_learning")
    else:
        # حالت دستی — یه دکمه فعال‌سازی
        b.button(text="🤖 فعال کردن حالت خودکار", callback_data="sched:set_auto")
    b.button(text="🔙 برگشت به تنظیمات",        callback_data="menu:settings")
    b.adjust(1)
    return b.as_markup()




def group_menu() -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.button(text="✏️ تنظیم آیدی گروه",       callback_data="grp:set")
    b.button(text="👥 عضویت همه اکانت‌ها",    callback_data="grp:join")
    b.button(text="📊 چک عضویت‌ها",            callback_data="grp:check")
    b.button(text="🆔 نمایش گروه فعلی",        callback_data="grp:info")
    b.button(text="🩺 تشخیص ویو",              callback_data="grp:diag")
    b.button(text="🧪 تست روی آخرین پیام",     callback_data="grp:test")
    b.button(text="🔁 نوع عملیات (ویو/خواندن)", callback_data="grp:mode")
    b.button(text="🔙 برگشت",                  callback_data="menu:main")
    b.adjust(1)
    return b.as_markup()


def shift_timing_menu() -> InlineKeyboardMarkup:
    """منوی پیش‌فرض‌های بازه شیفت"""
    b = InlineKeyboardBuilder()
    presets = [
        ("⚡ ۵-۱۰ دقیقه",   "5",  "10"),
        ("🔵 ۱۵-۳۰ دقیقه",  "15", "30"),
        ("🟢 ۲۵-۴۵ دقیقه",  "25", "45"),
        ("🟡 ۳۰-۶۰ دقیقه",  "30", "60"),
        ("🔴 ۴۵-۹۰ دقیقه",  "45", "90"),
    ]
    for label, mn, mx in presets:
        b.button(text=label, callback_data=f"stm:preset:{mn}:{mx}")
    b.button(text="✏️ وارد کردن دستی", callback_data="stm:custom")
    b.button(text="🔙 برگشت",          callback_data="menu:main")
    b.adjust(1)
    return b.as_markup()


def num_shifts_menu(current: int) -> InlineKeyboardMarkup:
    """منوی تغییر تعداد شیفت‌ها با دکمه‌های + و -"""
    b = InlineKeyboardBuilder()
    if current > 1:
        b.button(text=f"➖ کم کردن  ({current - 1})", callback_data=f"ns:set:{current - 1}")
    if current < 20:
        b.button(text=f"➕ اضافه کردن  ({current + 1})", callback_data=f"ns:set:{current + 1}")
    b.button(text="🔄 توزیع مجدد اکانت‌ها", callback_data="ns:redistribute")
    b.button(text="🔙 برگشت به تنظیمات", callback_data="menu:settings")
    b.adjust(2, 1, 1)
    return b.as_markup()


def shift_select_menu(current: int, num_shifts: int = 0) -> InlineKeyboardMarkup:
    """منوی انتخاب شیفت فعال (داینامیک) + حالت همه شیفت‌ها (0)"""
    if num_shifts <= 0:
        try:
            from core.pool import POOL
            num_shifts = POOL.num_shifts
        except Exception:
            num_shifts = 4
    b = InlineKeyboardBuilder()
    for i in range(1, num_shifts + 1):
        mark = "✅ " if i == current else ""
        b.button(text=f"{mark}شیفت {i}", callback_data=f"shift:select:{i}")
    all_mark = "✅ " if current == 0 else ""
    b.button(text=f"{all_mark}🌐 همه شیفت‌ها", callback_data="shift:select:0")
    b.button(text="🔙 برگشت", callback_data="menu:main")
    b.adjust(2)
    return b.as_markup()


def confirm_menu(action: str) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.button(text="✅ بله", callback_data=f"cfm:yes:{action}")
    b.button(text="❌ خیر", callback_data=f"cfm:no:{action}")
    b.adjust(2)
    return b.as_markup()


def cancel_menu() -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.button(text="❌ انصراف", callback_data="add:cancel")
    return b.as_markup()


def add_method_menu() -> InlineKeyboardMarkup:
    """انتخاب روش افزودن اکانت: شماره، سشن متنی، یا فایل سشن"""
    b = InlineKeyboardBuilder()
    b.button(text="📱 افزودن با شماره",          callback_data="add:method:phone")
    b.button(text="🔑 افزودن با String Session",  callback_data="add:method:session")
    b.button(text="📂 افزودن با فایل .session",   callback_data="add:method:session_file")
    b.button(text="❌ انصراف",                     callback_data="add:cancel")
    b.adjust(1)
    return b.as_markup()

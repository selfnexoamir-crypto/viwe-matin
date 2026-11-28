from aiogram.fsm.state import State, StatesGroup


class AddAccount(StatesGroup):
    waiting_phone        = State()
    waiting_code         = State()
    waiting_password     = State()
    waiting_session      = State()
    waiting_session_file = State()


class SetShiftTiming(StatesGroup):
    waiting_range = State()


class SetGroup(StatesGroup):
    waiting_id = State()


class SetGroup2(StatesGroup):
    waiting_id = State()


class UseMembers(StatesGroup):
    waiting_start_bot_link  = State()
    waiting_join_group_link = State()
    waiting_join_count      = State()   # تعداد اکانت برای عضو شدن


class StartBotWizard(StatesGroup):
    waiting_bot_link   = State()   # مرحله ۱: لینک ربات هدف
    waiting_fj_links   = State()   # مرحله ۲: جمع‌آوری لینک‌های جوین اجباری
    waiting_acc_count  = State()   # مرحله ۳: تعداد اکانت
    waiting_leave_days = State()   # مرحله ۴: روز ترک


class FixedChannel(StatesGroup):
    waiting_link    = State()   # مرحله ۱: دریافت لینک کانال
    waiting_confirm = State()   # مرحله ۲: تأیید مشخصات کانال


class EditProfiles(StatesGroup):
    waiting_names       = State()   # دریافت لیست اسم‌ها (با _ جدا)
    waiting_bios        = State()   # دریافت لیست بیوها (با _ جدا)
    waiting_usernames   = State()   # دریافت لیست یوزرنیم‌ها (با _ جدا)
    waiting_photos_zip  = State()   # دریافت فایل زیپ عکس‌های پروفایل
    waiting_group_photos_zip = State()  # دریافت فایل زیپ عکس‌های پروفایل گروه‌ها

"""
SmartScheduler — یادگیری خودکار چرخه لیمیت/ریکاوری
─────────────────────────────────────────────────────
وقتی حالت auto فعاله:
  1. فاز PROBE: یه شیفت رو روشن می‌کنه و زمان تا لیمیت شدن اکانت‌هاش رو اندازه می‌گیره
  2. فاز COOLDOWN: صبر می‌کنه اکانت‌های اون شیفت از لیمیت دربیان → زمان ریکاوری رو ثبت می‌کنه
  3. بر اساس داده‌ها تعداد شیفت مطلوب و تایم بین چرخش‌ها رو تنظیم می‌کنه
  4. هدف: وقتی شیفت i به نوبتش می‌رسه، شیفت i-1 از لیمیت دراومده باشه

حالت MANUAL: همان رفتار قبلی (scheduler.py) — فقط بازه تایم از DB
"""

import asyncio
import time as _time
import sys
from pathlib import Path
from datetime import datetime, timedelta
from collections import deque
from dataclasses import dataclass, field
from typing import Optional

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from models import database as db

# ───────── ثابت‌های فاز یادگیری ─────────
MIN_PROBE_DURATION   = 120    # حداقل ۲ دقیقه data جمع کنیم قبل از نتیجه‌گیری
MAX_SHIFTS           = 20
MIN_SHIFTS           = 2
RECOVERY_BUFFER      = 1.15   # ۱۵٪ بافر روی زمان ریکاوری (safety margin)
LEARNING_WINDOW      = 5      # آخرین N چرخه رو در نظر بگیر


@dataclass
class ShiftMetrics:
    """آمار هر شیفت در طول زمان"""
    shift_id: int
    limit_times:    deque = field(default_factory=lambda: deque(maxlen=LEARNING_WINDOW))
    recovery_times: deque = field(default_factory=lambda: deque(maxlen=LEARNING_WINDOW))
    activated_at:   float = 0.0
    limit_hit_at:   float = 0.0
    is_limited:     bool  = False

    def record_limit(self):
        if self.activated_at > 0 and not self.is_limited:
            elapsed = _time.time() - self.activated_at
            if elapsed >= MIN_PROBE_DURATION:
                self.limit_times.append(elapsed)
            self.limit_hit_at = _time.time()
            self.is_limited   = True

    def record_recovery(self):
        if self.limit_hit_at > 0 and self.is_limited:
            elapsed = _time.time() - self.limit_hit_at
            self.recovery_times.append(elapsed)
            self.is_limited = False

    @property
    def avg_limit_sec(self) -> float:
        return sum(self.limit_times) / len(self.limit_times) if self.limit_times else 0.0

    @property
    def avg_recovery_sec(self) -> float:
        return sum(self.recovery_times) / len(self.recovery_times) if self.recovery_times else 0.0

    @property
    def has_data(self) -> bool:
        return bool(self.limit_times) and bool(self.recovery_times)


# حالت‌های اتوماتیک
class AutoPhase:
    IDLE      = "idle"       # هنوز شروع نشده
    PROBE     = "probe"      # داریم زمان تا لیمیت رو می‌سنجیم
    COOLDOWN  = "cooldown"   # صبر می‌کنیم شیفت از لیمیت دربیاد
    TUNING    = "tuning"     # داریم پارامترها رو تنظیم می‌کنیم
    RUNNING   = "running"    # چرخش بهینه‌شده داره اجرا میشه


_SMART_STATE: dict = {
    "mode":            "manual",   # "auto" | "manual"
    "auto_phase":      AutoPhase.IDLE,
    "current_shift":   1,
    "next_swap_at":    None,
    "last_swap_at":    None,
    "swap_count":      0,
    "manual_rotated":  False,
    # یادگیری
    "metrics":         {},         # shift_id -> ShiftMetrics
    "computed_shifts": None,       # تعداد شیفت پیشنهادی از یادگیری
    "computed_interval_sec": None, # بازه پیشنهادی از یادگیری
    "learning_cycles": 0,          # چند بار کامل یاد گرفتیم
    "phase_started_at": 0.0,
    "last_status_msg": "",
}

_SMART_INSTANCE: Optional["SmartScheduler"] = None


def get_smart_scheduler() -> "SmartScheduler":
    global _SMART_INSTANCE
    if _SMART_INSTANCE is None:
        _SMART_INSTANCE = SmartScheduler()
    return _SMART_INSTANCE


def set_mode(mode: str):
    """تغییر حالت: 'auto' | 'manual'"""
    if mode not in ("auto", "manual"):
        raise ValueError(f"mode must be 'auto' or 'manual', got {mode!r}")
    _SMART_STATE["mode"] = mode
    db.set_setting("scheduler_mode", mode)
    db.debug_log("smart_scheduler", "set_mode", "OK", mode)


def get_mode() -> str:
    return _SMART_STATE["mode"]


def get_state_snapshot() -> dict:
    """snapshot کامل برای نمایش در ربات"""
    s = _SMART_STATE
    metrics_summary = {}
    for sid, m in s["metrics"].items():
        metrics_summary[sid] = {
            "avg_limit_min":    round(m.avg_limit_sec / 60, 1),
            "avg_recovery_min": round(m.avg_recovery_sec / 60, 1),
            "is_limited":       m.is_limited,
            "has_data":         m.has_data,
            "cycles":           len(m.limit_times),
        }
    snap = {k: v for k, v in s.items() if k != "metrics"}
    snap["metrics_summary"] = metrics_summary
    return snap


class SmartScheduler:
    def __init__(self):
        self._running = False
        self._task: Optional[asyncio.Task] = None
        # بارگذاری mode از DB
        saved_mode = db.get_setting("scheduler_mode", "manual")
        if saved_mode in ("auto", "manual"):
            _SMART_STATE["mode"] = saved_mode

    # ─────────────── POOL helpers ───────────────────────────────────────────

    @staticmethod
    def _get_pool():
        from core.pool import POOL
        return POOL

    def _metrics_for(self, shift_id: int) -> ShiftMetrics:
        if shift_id not in _SMART_STATE["metrics"]:
            _SMART_STATE["metrics"][shift_id] = ShiftMetrics(shift_id=shift_id)
        return _SMART_STATE["metrics"][shift_id]

    # ─────────────── لیمیت detection ────────────────────────────────────────

    def _get_limited_ratio(self, shift_id: int) -> float:
        """نسبت اکانت‌های flood در شیفت مشخص"""
        POOL = self._get_pool()
        phones = POOL.shifts.get(shift_id, [])
        if not phones:
            return 0.0
        all_accs = db.get_all_accounts()
        acc_map = {a["phone"]: a for a in all_accs}
        limited = sum(
            1 for p in phones
            if acc_map.get(p, {}).get("status") in ("flood", "error")
        )
        return limited / len(phones)

    def _all_shifts_ratio(self) -> dict:
        POOL = self._get_pool()
        return {sid: self._get_limited_ratio(sid) for sid in range(1, POOL.num_shifts + 1)}

    # ─────────────── بهینه‌سازی پارامترها ────────────────────────────────────

    def _compute_optimal(self) -> tuple[int, int]:
        """
        حساب تعداد شیفت مطلوب و فاصله چرخش (ثانیه).

        فرمول:
          cycle_total = avg_limit_sec + avg_recovery_sec * RECOVERY_BUFFER
          num_shifts  = ceil(cycle_total / avg_limit_sec)
          interval    = avg_limit_sec  (هر شیفت تا لیمیت بمونه)
        """
        import math
        metrics = list(_SMART_STATE["metrics"].values())
        # فقط شیفت‌هایی که داده دارن
        with_data = [m for m in metrics if m.has_data]
        if not with_data:
            return self._get_pool().num_shifts, self._get_manual_interval()

        avg_limit    = sum(m.avg_limit_sec    for m in with_data) / len(with_data)
        avg_recovery = sum(m.avg_recovery_sec for m in with_data) / len(with_data)

        cycle_total = avg_limit + avg_recovery * RECOVERY_BUFFER
        num_shifts  = math.ceil(cycle_total / max(avg_limit, 60))
        num_shifts  = max(MIN_SHIFTS, min(MAX_SHIFTS, num_shifts))
        interval    = int(avg_limit)
        interval    = max(120, interval)  # حداقل ۲ دقیقه

        db.debug_log(
            "smart_scheduler", "compute_optimal", "OK",
            f"avg_limit={avg_limit:.0f}s avg_rec={avg_recovery:.0f}s "
            f"→ num_shifts={num_shifts} interval={interval}s"
        )
        return num_shifts, interval

    @staticmethod
    def _get_manual_interval() -> int:
        try:
            from config import Config
            mn = int(db.get_setting("shift_min_sec", Config.SHIFT_MIN_SEC))
            mx = int(db.get_setting("shift_max_sec", Config.SHIFT_MAX_SEC))
            import random
            return random.randint(mn, mx)
        except Exception:
            return 1800

    def _apply_optimal(self, num_shifts: int, interval: int):
        POOL = self._get_pool()
        if POOL.num_shifts != num_shifts:
            POOL.set_num_shifts(num_shifts)
            db.debug_log("smart_scheduler", "apply_num_shifts", "OK", str(num_shifts))
        _SMART_STATE["computed_shifts"]       = num_shifts
        _SMART_STATE["computed_interval_sec"] = interval
        db.set_setting("smart_interval_sec", interval)
        _SMART_STATE["learning_cycles"] += 1
        _SMART_STATE["auto_phase"] = AutoPhase.RUNNING

    # ─────────────── چرخش ────────────────────────────────────────────────────

    async def _do_rotate(self, reason: str = "auto") -> int:
        POOL = self._get_pool()
        old = POOL.active_shift
        if POOL.num_shifts <= 1 or old == 0:
            return old
        new = POOL.swap_cycle()
        _SMART_STATE["swap_count"]  += 1
        _SMART_STATE["last_swap_at"] = datetime.utcnow().strftime("%H:%M:%S")
        m = self._metrics_for(new)
        m.activated_at = _time.time()
        m.is_limited   = False
        db.debug_log("smart_scheduler", "rotate", "OK",
                     f"{reason}: {old}→{new} #{_SMART_STATE['swap_count']}")
        try:
            result = await POOL.activate_shift(new)
            db.debug_log("smart_scheduler", "activate", "OK",
                         f"shift {new} | {result['connected']}/{result['total']} connected")
        except Exception as e:
            db.debug_log("smart_scheduler", "activate", "ERROR", str(e))
        return new

    def _schedule_next(self, interval_sec: int):
        _SMART_STATE["next_swap_at"] = datetime.utcnow() + timedelta(seconds=interval_sec)

    # ─────────────── LOOP اصلی ───────────────────────────────────────────────

    async def rotation_loop(self):
        self._running = True
        POOL = self._get_pool()
        db.debug_log("smart_scheduler", "loop_start", "OK",
                     f"mode={_SMART_STATE['mode']} shift={POOL.active_shift}")

        # initialize metrics برای همه شیفت‌ها
        for sid in range(1, POOL.num_shifts + 1):
            self._metrics_for(sid)

        # شروع با یه interval اولیه
        interval = self._get_manual_interval()
        self._schedule_next(interval)
        _SMART_STATE["auto_phase"] = AutoPhase.PROBE
        _SMART_STATE["phase_started_at"] = _time.time()

        while self._running:
            try:
                await asyncio.sleep(3)
                mode = _SMART_STATE["mode"]

                # ─── آپدیت وضعیت لیمیت همه شیفت‌ها ───────────────────────
                if mode == "auto":
                    await self._auto_tick()
                else:
                    await self._manual_tick()

            except asyncio.CancelledError:
                break
            except Exception as e:
                db.debug_log("smart_scheduler", "loop", "ERROR",
                             f"{type(e).__name__}: {e}")
                await asyncio.sleep(5)

        self._running = False

    # ─────────────── Auto tick ────────────────────────────────────────────────

    async def _auto_tick(self):
        POOL = self._get_pool()
        active = POOL.active_shift
        if active == 0:
            return

        # ─── بررسی وضعیت لیمیت شیفت فعلی ──────────────────────
        ratio = self._get_limited_ratio(active)
        m_active = self._metrics_for(active)
        LIMIT_THRESHOLD = 0.5   # ۵۰٪ اکانت‌های شیفت flood → لیمیت شده

        if ratio >= LIMIT_THRESHOLD and not m_active.is_limited:
            m_active.record_limit()
            status_msg = (f"⚠️ شیفت {active} لیمیت شد "
                          f"({ratio*100:.0f}% flood) | "
                          f"زمان فعالیت: {m_active.limit_times[-1]/60:.1f} دقیقه")
            _SMART_STATE["last_status_msg"] = status_msg
            db.debug_log("smart_scheduler", "limit_detected", "WARN", status_msg)

            # ─── بررسی شیفت‌های دیگه که ریکاوری شدن ──────────────
            self._check_recoveries()

            # ─── چرخش فوری به شیفت بعدی ──────────────────────────
            new_shift = await self._do_rotate("auto:limit_hit")
            _SMART_STATE["auto_phase"] = AutoPhase.COOLDOWN

            # ─── محاسبه interval بهینه اگه داده کافی داریم ────────
            if self._enough_data():
                n_shifts, interval = self._compute_optimal()
                self._apply_optimal(n_shifts, interval)
                self._schedule_next(interval)
                status = (f"🧠 بهینه‌سازی: {n_shifts} شیفت، "
                          f"فاصله {interval//60} دقیقه")
                _SMART_STATE["last_status_msg"] = status
                db.debug_log("smart_scheduler", "optimized", "OK", status)
            else:
                # هنوز داده کافی نداریم — interval پیش‌فرض
                interval = self._get_manual_interval()
                self._schedule_next(interval)
            return

        # ─── بررسی timer برای چرخش معمولی ───────────────────────
        if _SMART_STATE["next_swap_at"] is None:
            return
        remaining = (_SMART_STATE["next_swap_at"] - datetime.utcnow()).total_seconds()
        if remaining > 0:
            return

        # ─── manual rotate قبل از timer اتفاق افتاد؟ ──────────
        if _SMART_STATE["manual_rotated"]:
            _SMART_STATE["manual_rotated"] = False
            interval = (_SMART_STATE.get("computed_interval_sec")
                        or self._get_manual_interval())
            self._schedule_next(interval)
            return

        # ─── چرخش زمان‌بندی‌شده ─────────────────────────────────
        await self._do_rotate("auto:timer")
        self._check_recoveries()
        interval = (_SMART_STATE.get("computed_interval_sec")
                    or self._get_manual_interval())
        self._schedule_next(interval)

    def _check_recoveries(self):
        """اکانت‌هایی که از flood دراومدن رو ثبت کن"""
        POOL = self._get_pool()
        all_accs = db.get_all_accounts()
        acc_map = {a["phone"]: a for a in all_accs}

        for sid in range(1, POOL.num_shifts + 1):
            m = self._metrics_for(sid)
            if not m.is_limited:
                continue
            phones = POOL.shifts.get(sid, [])
            if not phones:
                continue
            flood_count = sum(
                1 for p in phones
                if acc_map.get(p, {}).get("status") in ("flood", "error")
            )
            ratio = flood_count / len(phones)
            if ratio < 0.2:  # کمتر از ۲۰٪ هنوز flood → ریکاوری شده
                m.record_recovery()
                db.debug_log(
                    "smart_scheduler", "recovery_detected", "OK",
                    f"shift {sid} recovered | "
                    f"recovery_time={m.recovery_times[-1]/60:.1f}min"
                )

    def _enough_data(self) -> bool:
        metrics = list(_SMART_STATE["metrics"].values())
        return sum(1 for m in metrics if m.has_data) >= 1

    # ─────────────── Manual tick ──────────────────────────────────────────────

    async def _manual_tick(self):
        """همان رفتار scheduler.py قبلی"""
        if _SMART_STATE["next_swap_at"] is None:
            interval = self._get_manual_interval()
            self._schedule_next(interval)
            return

        remaining = (_SMART_STATE["next_swap_at"] - datetime.utcnow()).total_seconds()
        if remaining > 0:
            return

        if _SMART_STATE["manual_rotated"]:
            _SMART_STATE["manual_rotated"] = False
            self._schedule_next(self._get_manual_interval())
            return

        POOL = self._get_pool()
        if POOL.num_shifts <= 1 or POOL.active_shift == 0:
            self._schedule_next(self._get_manual_interval())
            return

        await self._do_rotate("manual_timer")
        self._schedule_next(self._get_manual_interval())

    # ─────────────── Rotate دستی (از handler) ────────────────────────────────

    async def rotate_now(self) -> int:
        """چرخش دستی فوری"""
        new = await self._do_rotate("manual:force")
        _SMART_STATE["manual_rotated"] = True
        interval = (_SMART_STATE.get("computed_interval_sec")
                    or self._get_manual_interval())
        self._schedule_next(interval)
        return new

    # ─────────────── Lifecycle ────────────────────────────────────────────────

    def start(self):
        if self._running:
            return
        try:
            loop = asyncio.get_running_loop()
            self._task = loop.create_task(self.rotation_loop())
        except RuntimeError:
            db.debug_log("smart_scheduler", "start", "ERROR", "no running event loop")

    def stop(self):
        self._running = False
        if self._task and not self._task.done():
            self._task.cancel()

    # ─────────────── API برای handler ────────────────────────────────────────

    def status_text(self) -> str:
        """متن وضعیت برای نمایش در ربات"""
        s = _SMART_STATE
        POOL = self._get_pool()
        mode = s["mode"]
        active = POOL.active_shift
        lines = []

        if mode == "auto":
            phase = s["auto_phase"]
            lines.append(f"🤖 حالت: <b>خودکار</b> | فاز: <b>{phase}</b>")
            if s.get("computed_shifts"):
                lines.append(f"🎯 شیفت بهینه: <b>{s['computed_shifts']}</b>")
            if s.get("computed_interval_sec"):
                lines.append(f"⏱ فاصله بهینه: <b>{s['computed_interval_sec']//60} دقیقه</b>")
            lines.append(f"🔁 چرخه‌های یادگیری: <b>{s['learning_cycles']}</b>")

            # آمار هر شیفت
            for sid, m in s["metrics"].items():
                lim_str = f"{m.avg_limit_sec/60:.1f}م" if m.avg_limit_sec else "؟"
                rec_str = f"{m.avg_recovery_sec/60:.1f}م" if m.avg_recovery_sec else "؟"
                state = "🔴 لیمیت" if m.is_limited else "🟢 آزاد"
                lines.append(f"  شیفت {sid}: {state} | لیمیت≈{lim_str} | ریکاوری≈{rec_str}")

            if s["last_status_msg"]:
                lines.append(f"\n💬 {s['last_status_msg']}")
        else:
            lines.append("🔧 حالت: <b>دستی</b>")

        lines.append(f"\n📍 شیفت فعال: <b>{active}</b>/{POOL.num_shifts}")
        if s["next_swap_at"]:
            rem = max(0, (s["next_swap_at"] - datetime.utcnow()).total_seconds())
            lines.append(f"⏳ چرخش بعدی: <b>{int(rem)//60}:{int(rem)%60:02d}</b>")
        lines.append(f"🔄 کل چرخش‌ها: <b>{s['swap_count']}</b>")
        return "\n".join(lines)

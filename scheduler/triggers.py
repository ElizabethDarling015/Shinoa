"""
Фабрика триггеров APScheduler для каждого типа задачи.
"""

import pytz
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.date import DateTrigger
from apscheduler.triggers.interval import IntervalTrigger
from datetime import datetime, timedelta


def make_trigger(schedule: dict, timezone: str = "Europe/Moscow"):
    """
    Создаёт нужный триггер в зависимости от типа задачи.

    schedule содержит: task_type, time, days_of_week, day_of_month,
                       month, interval_days, one_shot
    """
    tz = pytz.timezone(timezone)
    time_str = schedule["time"]  # "HH:MM"
    hour, minute = map(int, time_str.split(":"))
    task_type = schedule.get("task_type") or schedule.get("type", "weekly")

    if task_type == "weekly":
        # days_of_week: "0,2,4" (Пн=0, Вс=6) → APScheduler "mon,wed,fri"
        ap_days = _convert_days(schedule.get("days_of_week"))
        return CronTrigger(
            day_of_week=ap_days or "*",
            hour=hour, minute=minute,
            timezone=tz,
        )

    elif task_type == "daily":
        return CronTrigger(hour=hour, minute=minute, timezone=tz)

    elif task_type == "workdays":
        return CronTrigger(
            day_of_week="mon-fri",
            hour=hour, minute=minute,
            timezone=tz,
        )

    elif task_type == "monthly_day":
        # Каждый месяц в day_of_month-е число
        day = schedule.get("day_of_month", 1)
        return CronTrigger(day=day, hour=hour, minute=minute, timezone=tz)

    elif task_type == "monthly_date":
        # Раз в год: конкретный день + месяц
        day = schedule.get("day_of_month", 1)
        month = schedule.get("month", 1)
        return CronTrigger(
            month=month, day=day,
            hour=hour, minute=minute,
            timezone=tz,
        )

    elif task_type == "morning":
        # Одноразовое: завтра в заданное время
        now = datetime.now(tz)
        fire_at = (now + timedelta(days=1)).replace(
            hour=hour, minute=minute, second=0, microsecond=0
        )
        return DateTrigger(run_date=fire_at, timezone=tz)

    elif task_type == "interval":
        days = schedule.get("interval_days", 1)
        return IntervalTrigger(days=days, timezone=tz)

    # Fallback — каждый день
    return CronTrigger(hour=hour, minute=minute, timezone=tz)


def _convert_days(days_str: str | None) -> str | None:
    """
    Конвертирует "0,2,4" (числа 0=Пн…6=Вс) в формат APScheduler "mon,wed,fri".
    """
    if not days_str:
        return None
    ap_map = {0: "mon", 1: "tue", 2: "wed", 3: "thu", 4: "fri", 5: "sat", 6: "sun"}
    try:
        nums = [int(x.strip()) for x in days_str.split(",")]
        return ",".join(ap_map[n] for n in nums if n in ap_map)
    except (ValueError, KeyError):
        return days_str

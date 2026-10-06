"""
Догоняющие напоминания: при старте бота находит срабатывания, которые
должны были случиться, пока бот был выключен, и отправляет их с пометкой.

Как это работает:
  1. Для каждого активного расписания строим тот же триггер, что и для
     планировщика (make_trigger), и ищем ПОСЛЕДНЕЕ срабатывание, которое
     уже было в прошлом, но не раньше границы окна догоняния.
  2. Сравниваем его с schedules.last_fired_at (когда бот реально отправил
     напоминание в последний раз). Если последнее срабатывание позже —
     значит его пропустили.
  3. Отправляем не больше одного догоняющего напоминания на расписание,
     даже если пропущено несколько (например, бот был выключен 3 дня
     для ежедневной задачи), чтобы не устраивать спам.

Окно догоняния зависит от типа задачи:
  - ежедневные / будни / недельные / «на завтра» — только пропуски СЕГОДНЯ:
    вчерашнее «попей воды» сегодня уже бессмысленно;
  - ежемесячные и годовые — до 7 дней назад: оплату домена лучше
    получить с опозданием, чем не получить совсем.
"""

import logging
from datetime import datetime, timedelta

import pytz
from apscheduler.triggers.date import DateTrigger
from apscheduler.triggers.interval import IntervalTrigger

from scheduler.triggers import make_trigger

logger = logging.getLogger(__name__)

TODAY_ONLY_TYPES = {"daily", "workdays", "weekly", "morning"}
LONG_WINDOW_TYPES = {"monthly_day", "monthly_date"}
LONG_WINDOW = timedelta(days=7)


def _parse_utc(value) -> datetime | None:
    """'YYYY-MM-DD HH:MM:SS' (UTC, формат SQLite) → aware datetime."""
    if not value:
        return None
    try:
        return pytz.UTC.localize(datetime.strptime(str(value)[:19], "%Y-%m-%d %H:%M:%S"))
    except ValueError:
        return None


def _window_start(task_type: str, now: datetime) -> datetime | None:
    if task_type in TODAY_ONLY_TYPES:
        return now.replace(hour=0, minute=0, second=0, microsecond=0)
    if task_type in LONG_WINDOW_TYPES:
        return now - LONG_WINDOW
    return None  # interval и неизвестные типы не догоняем


def last_due_occurrence(schedule: dict, tz_name: str, now: datetime) -> datetime | None:
    """
    Последнее срабатывание расписания в интервале [начало окна, now],
    или None, если в окне срабатываний не было.
    """
    task_type = schedule.get("task_type") or schedule.get("type") or "weekly"
    start = _window_start(task_type, now)
    if start is None:
        return None

    trigger = make_trigger(schedule, tz_name)

    if isinstance(trigger, IntervalTrigger):
        return None

    if isinstance(trigger, DateTrigger):
        run_at = trigger.run_date
        return run_at if start <= run_at <= now else None

    # CronTrigger: идём по срабатываниям от начала окна до «сейчас»
    last = None
    fire = trigger.get_next_fire_time(None, start)
    for _ in range(2000):  # предохранитель от бесконечного цикла
        if fire is None or fire > now:
            break
        last = fire
        fire = trigger.get_next_fire_time(fire, fire + timedelta(seconds=1))
    return last


def find_missed(schedules: list[dict], tz_name: str, now: datetime | None = None) -> list[tuple[dict, datetime]]:
    """Возвращает [(расписание, когда должно было сработать), ...]."""
    tz = pytz.timezone(tz_name)
    now = now or datetime.now(tz)
    missed = []

    for s in schedules:
        try:
            occurrence = last_due_occurrence(s, tz_name, now)
            if occurrence is None:
                continue

            # Задачи ещё не существовало в момент срабатывания —
            # например, ежедневную на 10:00 создали сегодня в 12:00.
            created = _parse_utc(s.get("task_created_at"))
            if created and created >= occurrence:
                continue

            # Бот уже отправил это (или более позднее) срабатывание.
            fired = _parse_utc(s.get("last_fired_at"))
            if fired and fired >= occurrence:
                continue

            missed.append((s, occurrence))
        except Exception as e:
            logger.error("Догоняние: ошибка разбора расписания %s: %s", s.get("id"), e)

    return missed

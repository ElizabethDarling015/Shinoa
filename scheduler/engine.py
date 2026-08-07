"""
Планировщик напоминаний. Загружает все активные расписания из БД при старте,
динамически добавляет/удаляет задания, делает ночные бэкапы.
Учитывает часовой пояс и время сводки каждого пользователя.
"""

import logging
import pytz
from datetime import datetime

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from aiogram import Bot

from config import DEFAULT_TIMEZONE
from database.connection import run_migrations, backup_database
from database.schedules import get_all_active_schedules
from database.habits import get_all_active_habits
from scheduler.triggers import make_trigger
from scheduler.sender import send_reminder
from scheduler.digest import send_digest

logger = logging.getLogger(__name__)


class ReminderScheduler:
    def __init__(self, bot: Bot, default_timezone: str = DEFAULT_TIMEZONE):
        self.bot = bot
        self.default_timezone = default_timezone
        self.scheduler = AsyncIOScheduler(timezone=default_timezone)

    # ──────────────────────────────────────────
    # Жизненный цикл
    # ──────────────────────────────────────────

    async def start(self):
        await run_migrations()
        await self._load_tasks()
        await self._load_habits()
        self._schedule_jobs()
        self.scheduler.start()
        logger.info("Планировщик запущен")

    async def stop(self):
        self.scheduler.shutdown(wait=False)

    # ──────────────────────────────────────────
    # Загрузка из БД при старте
    # ──────────────────────────────────────────

    async def _load_tasks(self):
        schedules = await get_all_active_schedules()
        for s in schedules:
            self._add_task_job(s)
        logger.info("Загружено %d расписаний задач", len(schedules))

    async def _load_habits(self):
        habits = await get_all_active_habits()
        for h in habits:
            self._add_habit_job(h)
        logger.info("Загружено %d привычек", len(habits))

    def _schedule_jobs(self):
        # Ночной бэкап — в 03:00 по дефолтному таймзоне
        self.scheduler.add_job(
            backup_database,
            CronTrigger(hour=3, minute=0, timezone=self.default_timezone),
            id="daily_backup",
            replace_existing=True,
        )
        # Утренняя сводка — проверяем каждые 15 минут, шлём тем, у кого сейчас время
        self.scheduler.add_job(
            self._send_digests_for_due_users,
            CronTrigger(minute="*/15"),
            id="morning_digest",
            replace_existing=True,
            kwargs={"bot": self.bot},
        )

    # ──────────────────────────────────────────
    # Утренняя сводка с учётом таймзона пользователя
    # ──────────────────────────────────────────

    async def _send_digests_for_due_users(self, bot: Bot):
        """
        Отправляет сводку только тем пользователям,
        у которых сейчас местное время совпадает с digest_time (±7 мин).
        """
        from database.users import get_all_users

        users = await get_all_users()
        now_utc = datetime.now(pytz.UTC)
        sent_count = 0

        for user in users:
            try:
                tz_str = user.get("timezone") or self.default_timezone
                digest_time = user.get("digest_time") or "07:00"
                chat_id = user["chat_id"]
                city = user.get("city")

                tz = pytz.timezone(tz_str)
                now_local = now_utc.astimezone(tz)
                h, m = map(int, digest_time.split(":"))
                target_minutes = h * 60 + m
                current_minutes = now_local.hour * 60 + now_local.minute

                # Допуск ±7 минут на задержки планировщика
                if abs(current_minutes - target_minutes) <= 7:
                    await send_digest(bot, chat_id, city=city)
                    sent_count += 1
            except Exception as e:
                logger.error("Ошибка сводки для чата %s: %s", user.get("chat_id"), e)

        if sent_count:
            logger.info("Утренние сводки отправлены: %d пользователей", sent_count)

    # ──────────────────────────────────────────
    # Управление заданиями задач
    # ──────────────────────────────────────────

    def _task_job_id(self, schedule_id: int) -> str:
        return f"task_{schedule_id}"

    def _add_task_job(self, schedule: dict):
        trigger = make_trigger(schedule, self.default_timezone)
        self.scheduler.add_job(
            send_reminder,
            trigger=trigger,
            id=self._task_job_id(schedule["id"]),
            replace_existing=True,
            kwargs={
                "bot": self.bot,
                "chat_id": schedule["chat_id"],
                "task_id": schedule["task_id"],
                "schedule_id": schedule["id"],
                "title": schedule["title"],
                "text": schedule["text"],
                "priority": schedule.get("priority", "medium"),
                "one_shot": bool(schedule.get("one_shot")),
            },
        )

    def add_task_schedule(self, schedule: dict):
        """Вызывается хендлером после сохранения расписания в БД."""
        self._add_task_job(schedule)

    def remove_task_schedule(self, schedule_id: int):
        job_id = self._task_job_id(schedule_id)
        if self.scheduler.get_job(job_id):
            self.scheduler.remove_job(job_id)

    def remove_all_for_task(self, schedule_ids: list[int]):
        for sid in schedule_ids:
            self.remove_task_schedule(sid)

    def add_snooze_job(self, trigger, chat_id, task_id, schedule_id, title, text, priority):
        """Добавляет одноразовое задание после snooze."""
        job_id = f"snooze_{task_id}_{schedule_id}"
        self.scheduler.add_job(
            send_reminder,
            trigger=trigger,
            id=job_id,
            replace_existing=True,
            kwargs={
                "bot": self.bot,
                "chat_id": chat_id,
                "task_id": task_id,
                "schedule_id": schedule_id,
                "title": title,
                "text": text,
                "priority": priority,
                "one_shot": False,
            },
        )

    # ──────────────────────────────────────────
    # Управление заданиями привычек
    # ──────────────────────────────────────────

    def _habit_job_id(self, habit_id: int) -> str:
        return f"habit_{habit_id}"

    def _add_habit_job(self, habit: dict):
        hour, minute = map(int, habit["reminder_time"].split(":"))
        trigger = CronTrigger(
            hour=hour,
            minute=minute,
            timezone=self.default_timezone,  # можно расширить на user.timezone
        )
        self.scheduler.add_job(
            self._send_habit_reminder,
            trigger=trigger,
            id=self._habit_job_id(habit["id"]),
            replace_existing=True,
            kwargs={
                "chat_id": habit["chat_id"],
                "habit_id": habit["id"],
                "name": habit["name"],
            },
        )

    def add_habit_schedule(self, habit: dict):
        self._add_habit_job(habit)

    def remove_habit_schedule(self, habit_id: int):
        job_id = self._habit_job_id(habit_id)
        if self.scheduler.get_job(job_id):
            self.scheduler.remove_job(job_id)

    async def _send_habit_reminder(self, chat_id: int, habit_id: int, name: str):
        from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
        from database.habits import is_done_today

        if await is_done_today(habit_id):
            return

        kb = InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(
                text="✅ Выполнено",
                callback_data=f"habit_done:{habit_id}"
            )
        ]])
        try:
            await self.bot.send_message(
                chat_id,
                f"💪 Напоминание о привычке: <b>{name}</b>",
                parse_mode="HTML",
                reply_markup=kb,
            )
        except Exception as e:
            logger.error("Ошибка отправки привычки → чат %s: %s", chat_id, e)
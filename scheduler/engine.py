"""
Планировщик напоминаний. Загружает все активные расписания из БД при старте,
динамически добавляет/удаляет задания, делает ночные бэкапы.
Учитывает часовой пояс и время сводки каждого пользователя.
"""

import logging
import pytz
from datetime import datetime, timedelta
from pathlib import Path

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.date import DateTrigger
from aiogram import Bot

from config import DEFAULT_TIMEZONE
from database.connection import run_migrations, backup_database
from database.schedules import get_all_active_schedules, deactivate_schedule
from database.habits import get_all_active_habits
from scheduler.triggers import make_trigger
from scheduler.sender import send_reminder, send_yearly_pre_reminder
from scheduler.catchup import find_missed
from scheduler.digest import send_digest
from scheduler.needrestart_check import check_needrestart

logger = logging.getLogger(__name__)

YEARLY_PRE_OFFSETS = (7, 3, 1)

class ReminderScheduler:
    def __init__(self, bot: Bot, default_timezone: str = DEFAULT_TIMEZONE):
        self.bot = bot
        self.default_timezone = default_timezone
        self.scheduler = AsyncIOScheduler(
            timezone=default_timezone,
            job_defaults={
                # Если срабатывание опоздало (VM подвисла, ноутбук/хост
                # просыпался и т.п.) — всё равно выполнить, если опоздание
                # не больше 5 минут. По умолчанию APScheduler даёт 1 секунду.
                "misfire_grace_time": 300,
                # Несколько пропущенных подряд срабатываний — выполнить один раз.
                "coalesce": True,
            },
        )

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
        await self._backup_if_missed()
        await self._catch_up_missed()

    async def stop(self):
        self.scheduler.shutdown(wait=False)

    # ──────────────────────────────────────────
    # Догоняние того, что пропущено, пока бот был выключен
    # ──────────────────────────────────────────

    async def _catch_up_missed(self):
        """Отправляет напоминания, срабатывание которых пришлось на время простоя."""
        try:
            schedules = await get_all_active_schedules()
            missed = find_missed(schedules, self.default_timezone)
        except Exception as e:
            logger.error("Догоняние: не удалось проверить пропуски: %s", e)
            return

        if not missed:
            logger.info("Догоняние: пропущенных напоминаний нет")
            return

        for s, occurrence in missed:
            logger.info(
                "Догоняние: расписание %s («%s») пропущено в %s",
                s["id"], s["title"], occurrence.strftime("%d.%m.%Y %H:%M"),
            )
            await send_reminder(
                bot=self.bot,
                chat_id=s["chat_id"],
                task_id=s["task_id"],
                schedule_id=s["id"],
                title=s["title"],
                text=s["text"],
                priority=s.get("priority", "medium"),
                one_shot=bool(s.get("one_shot")),
                missed_at=occurrence.strftime("%d.%m %H:%M"),
            )

    async def _backup_if_missed(self):
        """Если ночной бэкап за сегодня не сделан (бот был выключен в 03:00) — делаем сейчас."""
        try:
            today = datetime.now(pytz.timezone(self.default_timezone)).date()
            if not (Path("backups") / f"daily_{today}.db").exists():
                await backup_database()
        except Exception as e:
            logger.error("Не удалось сделать догоняющий бэкап: %s", e)

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
        # Ежедневное обновление предварительных напоминаний для годовых задач
        self.scheduler.add_job(
            self._refresh_yearly_pre_jobs,
            CronTrigger(hour=0, minute=5, timezone=self.default_timezone),
            id="refresh_yearly_pre_jobs",
            replace_existing=True,
        )
        # Проверка needrestart — раз в сутки, с запасом после окна apt-daily-upgrade (06:00–07:00 +рандом)
        self.scheduler.add_job(
            check_needrestart,
            CronTrigger(hour=7, minute=30, timezone=self.default_timezone),
            id="needrestart_check",
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

        # Разовое срабатывание («на завтра»), время которого уже прошло, в
        # планировщик не ставим — его отправит догоняние (_catch_up_missed),
        # иначе получился бы дубль.
        if isinstance(trigger, DateTrigger):
            now = datetime.now(pytz.timezone(self.default_timezone))
            if trigger.run_date <= now:
                return

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

        task_type = schedule.get("task_type") or schedule.get("type")

        # Для годовых напоминаний добавляем предварительные уведомления
        if task_type == "monthly_date":
            self._add_yearly_pre_jobs(schedule)

    def add_task_schedule(self, schedule: dict):
        """Вызывается хендлером после сохранения расписания в БД."""
        self._add_task_job(schedule)

    def remove_task_schedule(self, schedule_id: int):
        """
        Удаляет основное задание и все предварительные задания для расписания.
        """
        try:
            schedule_id = int(schedule_id)
            self._remove_pre_jobs_for_schedule(schedule_id)
        except (TypeError, ValueError):
            pass

        job_id = self._task_job_id(schedule_id)

        if self.scheduler.get_job(job_id):
            self.scheduler.remove_job(job_id)

    async def reschedule_dated_morning(self, chat_id: int, time_str: str):
        """
        Переносит ещё не отправленные напоминания задач «на день»
        (Сегодня/Завтра) на новое время утренней сводки.
        Если на новое время сегодняшнее напоминание уже прошло —
        оно отменяется: задача остаётся в планах на сегодня без напоминания.
        """
        from database.tasks import get_pending_dated_morning_schedules
        from database.schedules import update_schedule_time

        tz = pytz.timezone(self.default_timezone)
        now = datetime.now(tz)
        hour, minute = map(int, time_str.split(":"))

        for s in await get_pending_dated_morning_schedules(chat_id):
            sid = s["id"]
            due = datetime.strptime(str(s["task_due_date"])[:10], "%Y-%m-%d").date()
            fire_at = tz.localize(datetime.combine(due, datetime.min.time().replace(hour=hour, minute=minute)))

            self.remove_task_schedule(sid)
            if fire_at <= now:
                await deactivate_schedule(sid)
                logger.info("Задача на день #%s: новое время сводки уже прошло — без напоминания", s["task_id"])
                continue

            await update_schedule_time(sid, time_str)
            s["time"] = time_str
            self._add_task_job(s)
            logger.info("Задача на день #%s: напоминание перенесено на %s", s["task_id"], time_str)

    def remove_all_for_task(self, schedule_ids: list[int]):
        for sid in schedule_ids:
            self.remove_task_schedule(sid)
    
        # ──────────────────────────────────────────
    # Предварительные напоминания для годовых задач
    # ──────────────────────────────────────────

    def _pre_job_id(self, schedule_id: int, days: int) -> str:
        """
        ID задания предварительного напоминания.
        Пример: pre_7_12
        """
        return f"pre_{days}_{schedule_id}"

    def _remove_pre_jobs_for_schedule(self, schedule_id: int):
        """
        Удаляет все предварительные задания для конкретного расписания.
        """
        suffix = f"_{schedule_id}"

        for job in list(self.scheduler.get_jobs()):
            if job.id and job.id.startswith("pre_") and job.id.endswith(suffix):
                try:
                    self.scheduler.remove_job(job.id)
                except Exception:
                    pass

    def _next_yearly_occurrence(
        self,
        month: int,
        day: int,
        hour: int,
        minute: int,
        now: datetime,
        tz,
    ):
        """
        Возвращает ближайшую будущую дату годового напоминания.
        Например, если сейчас 2026 год и дата уже прошла, вернёт 2027 год.
        """
        year = now.year

        # Проверяем текущий год и несколько следующих.
        # Запас нужен, например, для 29 февраля.
        for _ in range(12):
            try:
                candidate = tz.localize(
                    datetime(
                        year,
                        month,
                        day,
                        hour,
                        minute,
                        second=0,
                        microsecond=0,
                    )
                )

                if candidate > now:
                    return candidate

            except ValueError:
                # Например, 30 февраля или 29 февраля в невисокосный год.
                pass

            year += 1

        return None

    def _add_yearly_pre_jobs(self, schedule: dict):
        """
        Создаёт задания за 7 дней / 3 дня / сутки до годового напоминания.
        """
        schedule_id = schedule.get("id")

        if not schedule_id:
            return

        try:
            schedule_id = int(schedule_id)
        except (TypeError, ValueError):
            return

        chat_id = schedule.get("chat_id")

        if not chat_id:
            return

        # Сначала удаляем старые предварительные задания для этого расписания
        self._remove_pre_jobs_for_schedule(schedule_id)

        try:
            month = int(schedule.get("month") or 0)
            day = int(schedule.get("day_of_month") or 0)

            if not (1 <= month <= 12 and 1 <= day <= 31):
                return

            time_str = str(schedule.get("time") or "00:00")
            hour, minute = map(int, time_str.split(":"))

            tz = pytz.timezone(self.default_timezone)
            now = datetime.now(tz)

            occurrence = self._next_yearly_occurrence(
                month=month,
                day=day,
                hour=hour,
                minute=minute,
                now=now,
                tz=tz,
            )

            if not occurrence:
                return

            for days in YEARLY_PRE_OFFSETS:
                run_date = tz.normalize(occurrence - timedelta(days=days))

                if run_date > now:
                    self.scheduler.add_job(
                        send_yearly_pre_reminder,
                        trigger=DateTrigger(run_date=run_date, timezone=tz),
                        id=self._pre_job_id(schedule_id, days),
                        replace_existing=True,
                        kwargs={
                            "bot": self.bot,
                            "chat_id": chat_id,
                            "title": schedule.get("title", ""),
                            "text": schedule.get("text", ""),
                            "priority": schedule.get("priority", "medium"),
                            "days_left": days,
                            "occurrence_date": occurrence.strftime("%d.%m.%Y %H:%M"),
                        },
                    )

        except Exception as e:
            logger.error(
                "Не удалось добавить предварительные напоминания для расписания %s: %s",
                schedule_id,
                e,
            )

    async def _refresh_yearly_pre_jobs(self):
        """
        Ежедневно пересоздаёт предварительные задания для годовых напоминаний.
        Это нужно, чтобы после наступления даты напоминания создать пред-уведомления
        уже на следующий год.
        """
        try:
            schedules = await get_all_active_schedules()
        except Exception as e:
            logger.error(
                "Не удалось загрузить расписания для обновления годовых пред-напоминаний: %s",
                e,
            )
            return

        active_yearly_ids = set()

        for schedule in schedules:
            task_type = schedule.get("task_type") or schedule.get("type")

            if task_type != "monthly_date":
                continue

            try:
                schedule_id = int(schedule.get("id"))
            except (TypeError, ValueError):
                continue

            active_yearly_ids.add(schedule_id)
            self._add_yearly_pre_jobs(schedule)

        # Удаляем пред-напоминания для задач, которых больше нет среди активных
        for job in list(self.scheduler.get_jobs()):
            if not job.id or not job.id.startswith("pre_"):
                continue

            try:
                schedule_id = int(job.id.rsplit("_", 1)[-1])

                if schedule_id not in active_yearly_ids:
                    self.scheduler.remove_job(job.id)

            except Exception:
                pass

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
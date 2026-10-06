"""
Ревизия базы Shinoa: что в ней активно и что может «всплыть».

Только читает базу (открывает в режиме read-only), ничего не меняет.

Запуск из папки бота, тем же python, что и бот (из его venv):
    python tools/audit_db.py              # база из DB_PATH в .env
    python tools/audit_db.py backups/daily_2026-10-05.db
"""

import os
import sqlite3
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import pytz  # noqa: E402
from dotenv import dotenv_values  # noqa: E402
from apscheduler.triggers.interval import IntervalTrigger  # noqa: E402

from scheduler.triggers import make_trigger  # noqa: E402

ENV = dotenv_values(ROOT / ".env")
TZ_NAME = ENV.get("TIMEZONE") or "Asia/Yekaterinburg"
TZ = pytz.timezone(TZ_NAME)
NOW = datetime.now(TZ)
DAYS = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"]


def utc_to_local(value) -> str:
    if not value:
        return "—"
    try:
        dt = pytz.UTC.localize(datetime.strptime(str(value)[:19], "%Y-%m-%d %H:%M:%S"))
        return dt.astimezone(TZ).strftime("%d.%m.%Y %H:%M")
    except ValueError:
        return str(value)


def human_schedule(s: dict) -> str:
    t, tm = s["task_type"], s["time"]
    if t == "daily":
        return f"каждый день в {tm}"
    if t == "workdays":
        return f"по будням в {tm}"
    if t == "weekly":
        try:
            days = ", ".join(DAYS[int(d)] for d in (s["days_of_week"] or "").split(",") if d.strip())
        except (ValueError, IndexError):
            days = s["days_of_week"]
        return f"по дням недели ({days or 'каждый день'}) в {tm}"
    if t == "monthly_day":
        return f"каждое {s['day_of_month']}-е число в {tm}"
    if t == "monthly_date":
        return f"ежегодно {int(s['day_of_month']):02d}.{int(s['month']):02d} в {tm}"
    if t == "morning":
        return f"разово (на завтра) в {tm}"
    if t == "interval":
        return f"каждые {s['interval_days']} дн."
    return f"{t} в {tm}"


def next_fire(s: dict) -> str:
    try:
        trig = make_trigger(s, TZ_NAME)
        if isinstance(trig, IntervalTrigger):
            return "отсчёт от каждого запуска бота"
        nxt = trig.get_next_fire_time(None, NOW)
        if nxt is None:
            return "больше не сработает"
        if nxt <= NOW:
            return f"ПРОШЛО ({nxt.strftime('%d.%m.%Y %H:%M')})"
        return nxt.strftime("%d.%m.%Y %H:%M")
    except Exception as e:
        return f"ошибка триггера: {e}"


def main():
    db_path = sys.argv[1] if len(sys.argv) > 1 else (ENV.get("DB_PATH") or "bot.db")
    if not Path(db_path).is_file() or Path(db_path).stat().st_size == 0:
        sys.exit(f"Файл базы не найден или пустой: {db_path}")

    con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    q = lambda sql, *a: [dict(r) for r in con.execute(sql, a).fetchall()]  # noqa: E731
    tables = {r["name"] for r in q("SELECT name FROM sqlite_master WHERE type='table'")}
    if "tasks" not in tables:
        sys.exit(f"В {db_path} нет таблицы tasks — это не рабочая база бота")

    sched_cols = {r["name"] for r in q("PRAGMA table_info(schedules)")}
    has_fired = "last_fired_at" in sched_cols
    version = q("SELECT MAX(version) AS v FROM schema_version")[0]["v"]

    print(f"База: {db_path}   версия схемы: {version}   часовой пояс: {TZ_NAME}")
    print(f"Сейчас: {NOW.strftime('%d.%m.%Y %H:%M')}\n")

    # ── 1. Сводка ───────────────────────────────────────
    print("═══ 1. Задачи по статусам и типам ═══")
    for r in q("SELECT status, type, COUNT(*) AS n FROM tasks GROUP BY status, type ORDER BY status, type"):
        print(f"  {r['status']:<8} {r['type']:<13} {r['n']}")

    # ── 2. Всё, что реально может прийти ────────────────
    print("\n═══ 2. Активные расписания (то, что бот будет присылать) ═══")
    active = q(f"""
        SELECT s.*, t.id AS tid, t.title, t.text, t.type AS task_type, t.priority, t.chat_id,
               t.created_at AS task_created_at
        FROM schedules s JOIN tasks t ON t.id = s.task_id
        WHERE s.is_active = 1 AND t.status = 'active'
        ORDER BY t.type, t.id
    """)
    if not active:
        print("  (нет)")
    for s in active:
        print(f"  #{s['tid']} [{s['task_type']}] {s['title']}")
        if s["text"] and s["text"] != s["title"]:
            print(f"      текст:     {s['text'][:80]}")
        print(f"      когда:     {human_schedule(s)}")
        print(f"      следующее: {next_fire(s)}")
        print(f"      создана:   {utc_to_local(s['task_created_at'])}"
              + (f"   последняя отправка: {utc_to_local(s.get('last_fired_at'))}" if has_fired else ""))

    # ── 3. Подозрительное ───────────────────────────────
    print("\n═══ 3. На что обратить внимание ═══")
    found = False

    def warn(title, rows, fmt):
        nonlocal found
        if rows:
            found = True
            print(f"\n  ⚠️  {title}")
            for r in rows:
                print("      " + fmt(r))

    warn("Короткие месяцы: задача на 29–31 число НЕ придёт в месяцах, где такого дня нет",
         [s for s in active if s["task_type"] == "monthly_day" and int(s["day_of_month"] or 0) >= 29],
         lambda s: f"#{s['tid']} {s['title']} — {s['day_of_month']}-е число")

    warn("Годовая задача на 29.02 — придёт только в високосный год",
         [s for s in active if s["task_type"] == "monthly_date"
          and int(s["month"] or 0) == 2 and int(s["day_of_month"] or 0) == 29],
         lambda s: f"#{s['tid']} {s['title']}")

    warn("Разовые расписания, время которых уже прошло, но они всё ещё активны",
         [s for s in active if s["one_shot"] and next_fire(s).startswith(("ПРОШЛО", "больше"))],
         lambda s: f"#{s['tid']} {s['title']} — {next_fire(s)}")

    warn("Активные задачи БЕЗ активного расписания (висят в списке, но сами не напомнят)",
         q("""SELECT t.* FROM tasks t WHERE t.status='active' AND t.type != 'morning'
              AND NOT EXISTS (SELECT 1 FROM schedules s WHERE s.task_id=t.id AND s.is_active=1)"""),
         lambda t: f"#{t['id']} [{t['type']}] {t['title']} (создана {utc_to_local(t['created_at'])})")

    warn("Задачи «на завтра», которые висят активными дольше 2 дней",
         q("""SELECT * FROM tasks WHERE status='active' AND type='morning'
              AND created_at < datetime('now','-2 days')"""),
         lambda t: f"#{t['id']} {t['title']} (создана {utc_to_local(t['created_at'])})")

    dups = defaultdict(list)
    for t in q("SELECT id, title, type FROM tasks WHERE status='active'"):
        dups[t["title"].strip().lower()].append(t)
    warn("Дубликаты: несколько активных задач с одинаковым названием",
         [v for v in dups.values() if len(v) > 1],
         lambda v: f"«{v[0]['title']}»: " + ", ".join(f"#{t['id']} [{t['type']}]" for t in v))

    warn("Расписания, ссылающиеся на несуществующую задачу (мусор)",
         q("SELECT s.* FROM schedules s LEFT JOIN tasks t ON t.id=s.task_id WHERE t.id IS NULL"),
         lambda s: f"schedule #{s['id']} → task #{s['task_id']}")

    leftovers = q("""SELECT t.status, COUNT(*) AS n FROM schedules s JOIN tasks t ON t.id=s.task_id
                     WHERE s.is_active=1 AND t.status != 'active' GROUP BY t.status""")
    if leftovers:
        print("\n  ℹ️  Активные расписания у выполненных/удалённых задач: "
              + ", ".join(f"{r['status']}: {r['n']}" for r in leftovers)
              + "\n      Не опасно: бот их не загружает. Просто след старых задач.")

    if not found:
        print("  Ничего подозрительного не найдено.")

    # ── 4. Привычки и пользователи ──────────────────────
    if "habits" in tables:
        habits = q("SELECT * FROM habits WHERE is_active=1")
        print("\n═══ 4. Активные привычки (напоминают каждый день) ═══")
        for h in habits or [{}]:
            print(f"  #{h['id']} {h['name']} в {h['reminder_time']}" if h else "  (нет)")

    print("\n═══ 5. Пользователи в базе ═══")
    allowed = {x.strip() for x in (ENV.get("ALLOWED_USERS") or "").split(",") if x.strip()}
    for u in q("SELECT * FROM users"):
        mark = "" if not allowed or str(u["chat_id"]) in allowed else "   ← нет в ALLOWED_USERS"
        print(f"  {u['chat_id']}  tz={u['timezone']}  сводка={u['digest_time']}  город={u.get('city') or '—'}{mark}")

    con.close()


if __name__ == "__main__":
    main()

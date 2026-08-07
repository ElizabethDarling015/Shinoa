# Telegram Personal Organizer Bot

Личный органайзер в Telegram: задачи, привычки, архив.

## Структура проекта

```
reminder_bot/
├── bot.py                     ← точка входа
├── config.py                  ← настройки (токен, часовой пояс)
├── requirements.txt
│
├── database/
│   ├── connection.py          ← подключение к БД, миграции, бэкапы
│   ├── migrations/            ← SQL-файлы миграций (001_, 002_, ...)
│   ├── tasks.py               ← CRUD для задач
│   ├── schedules.py           ← CRUD для расписаний
│   ├── habits.py              ← CRUD для привычек
│   └── archive.py             ← CRUD для архива
│
├── handlers/
│   ├── __init__.py            ← собирает все роутеры
│   ├── start.py               ← /start, /help
│   ├── weekly.py              ← /new (еженедельные)
│   ├── monthly.py             ← /monthly
│   ├── daily.py               ← /daily, /morning
│   ├── list_tasks.py          ← /list, /delete
│   ├── snooze.py              ← inline-кнопки (✅⏰❌)
│   ├── habits.py              ← /habits, /habit_new
│   ├── archive.py             ← /idea, /find, фото, документы
│   └── stats.py               ← /stats
│
├── scheduler/
│   ├── engine.py              ← планировщик APScheduler
│   ├── triggers.py            ← фабрика триггеров
│   └── sender.py              ← отправка с inline-кнопками
│
└── backups/                   ← автоматические резервные копии БД
```

## Установка и запуск

```bash
pip install -r requirements.txt
```

Укажи токен бота в `config.py` или через переменную окружения:

```bash
export BOT_TOKEN="твой_токен"
python bot.py
```

## Команды бота

| Команда | Описание |
|---|---|
| /new | Еженедельное напоминание |
| /daily | Ежедневная задача |
| /morning | Разовая задача на завтра утром |
| /monthly | Ежемесячное напоминание |
| /list | Список задач (с фильтрами) |
| /delete `<id>` | Удалить задачу |
| /habits | Список привычек |
| /habit_new | Создать привычку |
| /delete_habit `<id>` | Удалить привычку |
| /idea | Сохранить идею с тегами |
| /find `<запрос>` | Поиск по архиву |
| /stats | Статистика и streak'и |

## Система миграций

Каждое изменение схемы БД — отдельный файл в `database/migrations/`.  
При старте бота миграции применяются автоматически.  
Перед каждой новой миграцией создаётся резервная копия в `backups/`.

Чтобы добавить новую колонку — создай файл `004_название.sql`:
```sql
ALTER TABLE tasks ADD COLUMN новая_колонка TEXT;
INSERT OR IGNORE INTO schema_version VALUES (4);
```

## Резервные копии

- `backups/pre_migration_*.db` — создаётся перед каждой миграцией
- `backups/daily_YYYY-MM-DD.db` — создаётся каждую ночь в 03:00
- Хранятся последние 30 ночных копий

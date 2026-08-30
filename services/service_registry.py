"""
Реестр сервисов, которыми управляет Shinoa через subprocess.

Добавление нового сервиса (Avito-парсер, OSINT-инструмент, авторегистратор
и т.д.) — это ОДНА новая запись здесь, без правок в service_manager.py или
в хендлерах меню. Единственное условие: сервис должен уметь принять флаг
--status-file <путь> и писать туда JSON по контракту (см. service_manager.py,
раздел "Формат status.json").

input_kind определяет, что именно спросить у пользователя перед запуском:
    "none"      — без параметров, сразу кнопка "Запустить"
    "url_hours" — сначала ссылка, потом число часов (как у Playerok)
    "text"      — один текстовый параметр произвольного вида
    "number"    — одно число (например, "сколько аккаунтов зарегистрировать")

Поле "title" и эмодзи в нём — то, что увидит пользователь на кнопке.
"""

SERVICES = {
    "playerok": {
        "title": "📈 Playerok Parser",
        "python": "/opt/parsers/playerok_stats/venv/bin/python3",
        "entry": "/Users/elizabeth/Documents/1.Bot/Shinoa Services/playerok_stats/main.py",
        "cwd": "/opt/parsers/playerok_stats",
        "command": "collect",          # подкоманда main.py
        "input_kind": "url_hours",
        "input_prompts": {
            "url": (
                "🔗 Пришли ссылку на нишу Playerok (страница категории), например:\n"
                "<code>https://playerok.com/roblox/accounts</code>"
            ),
            "hours": "⏱ На сколько часов запускаем сбор? Просто число, например <code>6</code>",
        },
    },
    # Пример будущей записи (Avito) — раскомментировать и donastroit, когда будет готов:
    # "avito": {
    #     "title": "🏷 Avito Parser",
    #     "python": "/opt/parsers/avito_stats/venv/bin/python3",
    #     "entry": "/opt/parsers/avito_stats/main.py",
    #     "cwd": "/opt/parsers/avito_stats",
    #     "command": "collect",
    #     "input_kind": "url_hours",
    #     "input_prompts": {
    #         "url": "🔗 Пришли ссылку на категорию Avito:",
    #         "hours": "⏱ На сколько часов запускаем сбор?",
    #     },
    # },
}


def get_service(service_id: str) -> dict | None:
    return SERVICES.get(service_id)


def list_services() -> list[tuple[str, dict]]:
    """[(service_id, config), ...] в порядке добавления в словарь."""
    return list(SERVICES.items())

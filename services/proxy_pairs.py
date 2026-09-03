"""
Пары "прокси + куки" для сервисов с пулом ресурсов (сейчас — только Playerok,
но механизм общий, включается флагом "use_proxy_pool" в service_registry.py).

Почему пара, а не два независимых списка "прокси" и "куки": куки здесь не
про логин конкретного аккаунта, а про DataDome-отпечаток браузерной сессии
(парсер вообще нигде не авторизуется под покупателем/продавцом, ему это не
нужно для чтения публичных страниц). Но если крутить куки и прокси НЕЗАВИСИМО
по кругу, одна и та же сессия начнёт "приходить" с разных IP/гео за
несколько минут — классический признак угона сессии для антифрода. Поэтому
прокси и куки один раз при добавлении СВЯЗЫВАЮТСЯ в пару и дальше всегда
живут и распределяются по потокам вместе, никогда порознь.

Хранение — один JSON-файл на сервис в STATUS_DIR (тот же каталог и тот же
принцип атомарной записи через .tmp+replace, что и у registry.json/
settings.json в service_manager.py — сознательно не импортируем оттуда
STATUS_DIR напрямую, чтобы не тянуть циклическую зависимость: этот модуль
ничего не знает про запущенные потоки, только хранит сами пары).

Формат одной пары:
{
    "id": 1,                              # стабильный номер, не переиспользуется
    "proxy": "http://user:pass@host:port",
    "cookies_file": "/tmp/.../playerok_pair_1_cookies.txt",
    "geo": "Германия, Франкфурт" | None,   # определяется при добавлении, best-effort
    "verified": True | False | None,       # None — ещё не проверялась
}
"""

import json
import tempfile
from pathlib import Path

STATUS_DIR = Path(tempfile.gettempdir()) / "shinoa_service_status"
STATUS_DIR.mkdir(exist_ok=True)


def _pairs_file(service_id: str) -> Path:
    return STATUS_DIR / f"{service_id}_proxy_pairs.json"


def get_pairs(service_id: str) -> list:
    f = _pairs_file(service_id)
    if not f.exists():
        return []
    try:
        return json.loads(f.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []


def get_pair(service_id: str, pair_id: int) -> dict | None:
    for p in get_pairs(service_id):
        if p["id"] == pair_id:
            return p
    return None


def _save_pairs(service_id: str, pairs: list) -> None:
    f = _pairs_file(service_id)
    tmp = f.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(pairs, ensure_ascii=False), encoding="utf-8")
    tmp.replace(f)


def add_pair(service_id: str, proxy: str, cookies_file: str) -> dict:
    """cookies_file — уже готовый путь к файлу с текстом кук на диске
    (пишет вызывающая сторона — см. handlers/services_control.py)."""
    pairs = get_pairs(service_id)
    next_id = max((p["id"] for p in pairs), default=0) + 1
    pair = {"id": next_id, "proxy": proxy, "cookies_file": cookies_file, "geo": None, "verified": None}
    pairs.append(pair)
    _save_pairs(service_id, pairs)
    return pair


def remove_pair(service_id: str, pair_id: int) -> bool:
    """Само по себе НЕ проверяет занятость активными потоками — это ответственность
    вызывающей стороны (services_control.py), у неё есть доступ к списку живых
    потоков через service_manager, у этого модуля — нет и не должно быть."""
    pairs = get_pairs(service_id)
    new_pairs = [p for p in pairs if p["id"] != pair_id]
    if len(new_pairs) == len(pairs):
        return False
    _save_pairs(service_id, new_pairs)
    return True


def update_pair(service_id: str, pair_id: int, **fields) -> None:
    pairs = get_pairs(service_id)
    for p in pairs:
        if p["id"] == pair_id:
            p.update(fields)
            break
    _save_pairs(service_id, pairs)

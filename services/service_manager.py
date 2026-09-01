"""
Универсальный менеджер сервисов: запуск/пауза/остановка нескольких
параллельных "потоков" на один сервис через subprocess. Не знает НИЧЕГО о
специфике конкретного сервиса (Playerok, Avito, OSINT-тул) — только общий
контракт status.json и путь запуска из реестра.

Формат status.json (пишет сам сервис по флагу --status-file <путь>):
{
    "status": "running" | "done" | "error",
    "started_at": "ISO-время",
    "progress_percent": 0-100 или null,
    "progress_text": "3 из 10" или null — для сервисов без чёткого % прогресса,
    "niche_title": "человекочитаемое имя того, что обрабатывается" или null —
                    например "Roblox - Аккаунты"; используется в UI вместо
                    голого номера потока (см. handlers/services_control.py),
    "elapsed_seconds" / "remaining_seconds" / "total_seconds": числа или null —
                    для отображения "осталось X из Y" в карточке потока,
    "last_update": "ISO-время",
    "result_text": "краткая сводка метрик для уведомления" или null,
    "result_file": "путь к файлу-результату" или null,
    "error": "текст ошибки" или null
}
Ни одно из новых полей не обязательно — сервис, который их не пишет, просто
не получит красивую метку/таймер в UI (см. подстраховки через .get(...) и
"or" в services_control.py), само управление им (старт/пауза/стоп) не пострадает.

Несколько параллельных запусков ("потоков") на один и тот же service_id —
штатный режим, каждый со своим run_id (целое число, растёт по порядку
запуска и не переиспользуется даже после завершения более раннего потока).

Пауза реализована через сигналы ОС SIGSTOP/SIGCONT — процесс физически
замораживается (ноль CPU/сети), но собственные часы дедлайна внутри
парсера при этом не корректируются: длинная пауза "съедает" часть
изначально запрошенного времени сбора, а не продлевает его. Для коротких
пауз это несущественно; точное сохранение "оставшегося времени" требует
доработки самого парсера отдельным заходом.

──────────────────────────────────────────────────────────────────────────
НЕЗАВИСИМОСТЬ ОТ ЖИЗНИ SHINOA И ВОССТАНОВЛЕНИЕ ПОСЛЕ ПЕРЕЗАПУСКА
──────────────────────────────────────────────────────────────────────────

Два независимых механизма, оба нужны одновременно:

1. Дочерний процесс запускается с start_new_session=True (это os.setsid()
   в дочернем процессе перед exec) — он получает СВОЮ группу процессов и
   сессию, отвязанную от терминала/сессии Shinoa. Благодаря этому:
   - Ctrl+C в терминале, где крутится Shinoa, посылает SIGINT только
     группе процессов терминала — дочерний парсер в неё больше не входит,
     сигнал до него не долетает;
   - закрытие терминала/потеря SSH-сессии (SIGHUP) тоже не долетает.
   ВАЖНО: это НЕ защищает от systemd с KillMode=control-group (по
   умолчанию) — тот убивает всё в cgroup юнита независимо от сессии/группы
   процессов. Если Shinoa когда-нибудь будет задеплоена как systemd-сервис
   и это важно сохранить — там нужно явно поставить KillMode=process в
   unit-файле, либо принять, что systemctl stop/restart убьёт и потоки.

2. Мы не держим ничего критичного только в оперативной памяти. Каждый
   запуск/пауза/резюм/остановка сразу пишет полный снимок активных потоков
   в registry.json (STATUS_DIR) — pid, пути к status.json/.log, команда
   запуска (для проверки от переиспользования pid), chat_id (куда слать

3. Процесс запускается через СИНХРОННЫЙ subprocess.Popen, а не через
   asyncio.create_subprocess_exec. Это принципиально, не стилистика:
   объект asyncio.subprocess.Process оборачивает asyncio-транспорт, и у
   этого транспорта есть задокументированное поведение — при уничтожении
   (close()/сборка мусора), если процесс ещё жив, он сам посылает ему
   SIGKILL (защита asyncio от "утекших" дочерних процессов). Раньше здесь
   был asyncio.create_subprocess_exec + отдельная asyncio-задача, которая
   через await process.wait() держала этот объект живым "для порядка, чтобы
   не плодить зомби" — и именно это оказалось причиной реального бага: при
   остановке Shinoa (даже с KillMode=process в systemd, даже с успешным
   start_new_session) asyncio.run() в самом конце отменяет все ещё висящие
   задачи, включая эту; единственная ссылка на объект-транспорт исчезает,
   Python тут же его собирает, деструктор видит "процесс ещё жив" — и убивает
   его SIGKILL'ом, никак не заботясь о сессиях/группах процессов, потому что
   это прямой kill(pid) изнутри интерпретатора, а не сигнал от ОС/systemd.
   У обычного subprocess.Popen такого поведения при сборке мусора нет —
   поэтому именно он используется здесь, а не asyncio-обёртка.
   уведомления) и параметры. При старте bot.py вызывает recover() (см.
   ниже и handlers/services_control.recover_services): для каждой записи
   реестра проверяется, жив ли ещё процесс с этим pid (и что это точно ОН,
   а не другой процесс, которому ОС успела переиспользовать тот же pid,
   пока Shinoa была выключена, — сверяем /proc/<pid>/cmdline). Если жив —
   Shinoa просто заново "подписывается" на него (recover), как будто сама
   его и запускала. Если не пережил — читаем последний status.json (парсер
   мог успеть сам дойти до done/error, пока Shinoa молчала) и один раз
   доносим итог, вместо того чтобы тихо потерять результат многочасового
   сбора.

Из-за этого менеджер везде работает с "голым" pid (int) через os.kill(),
а не с объектом asyncio.subprocess.Process — Process можно получить только
для процесса, который САМИ породили в этой сессии Python, а после
перезапуска Shinoa унаследованные (уже не дочерние — их усыновил init)
процессы этим способом не адресовать. os.kill(pid, ...) работает
одинаково в обоих случаях, поэтому весь код синхронизации/пауз/остановки
написан через него, единообразно для свежезапущенных и "воскрешённых"
потоков.
"""

import asyncio
import json
import logging
import os
import signal
import subprocess
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

from services.service_registry import get_service

logger = logging.getLogger(__name__)

STATUS_DIR = Path(tempfile.gettempdir()) / "shinoa_service_status"
STATUS_DIR.mkdir(exist_ok=True)
REGISTRY_FILE = STATUS_DIR / "registry.json"

# service_id -> {run_id: {"pid","status_file","log_file","log_fh","watcher_task",
#                          "cmd","chat_id","params","started_at","paused"}}
_running: dict[str, dict[int, dict]] = {}
# service_id -> следующий свободный run_id (растёт монотонно, не переиспользуется)
_next_run_id: dict[str, int] = {}


class ServiceError(Exception):
    pass


def _runs(service_id: str) -> dict[int, dict]:
    return _running.setdefault(service_id, {})


def _is_pid_alive(pid: int, local_process: subprocess.Popen | None = None) -> bool:
    """
    Если процесс был запущен НАМИ в этой сессии (local_process передан) —
    проверяем и заодно "пожинаем" через Popen.poll() (не блокирует, просто
    неблокирующий os.waitpid внутри) — иначе, доживи он до превращения в
    зомби, будет висеть в таблице процессов до перезапуска Shinoa.
    Для "усыновлённых" после restart потоков (local_process нет — это уже
    не наш ребёнок, waitpid на чужой pid не сработает) — просто kill(pid, 0).
    """
    if local_process is not None:
        return local_process.poll() is None
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _cmdline_matches(pid: int, expected_cmd: list[str]) -> bool:
    """
    Подстраховка от редкого, но реального сценария: Shinoa была выключена
    достаточно долго, поток успел завершиться, и ОС успела выдать его pid
    какому-то совсем другому процессу. Без этой проверки мы рисковали бы
    "усыновить" чужой процесс и слать ему SIGSTOP/SIGTERM.
    Работает только на Linux (через /proc); если /proc недоступен (не Linux) —
    молча доверяем pid как есть, ничего лучше без /proc сделать нельзя.
    """
    try:
        with open(f"/proc/{pid}/cmdline", "rb") as f:
            raw = f.read()
    except OSError:
        return True
    actual = [p.decode("utf-8", "replace") for p in raw.split(b"\x00") if p]
    return actual == expected_cmd


def list_runs(service_id: str) -> list[int]:
    """Активные run_id для сервиса, в порядке запуска (по возрастанию номера)."""
    return sorted(_runs(service_id).keys())


def is_running(service_id: str, run_id: int = None) -> bool:
    runs = _runs(service_id)
    if run_id is not None:
        entry = runs.get(run_id)
        return bool(entry and _is_pid_alive(entry["pid"], entry.get("local_process")))
    return any(_is_pid_alive(e["pid"], e.get("local_process")) for e in runs.values())


def is_paused(service_id: str, run_id: int) -> bool:
    entry = _runs(service_id).get(run_id)
    return bool(entry and entry.get("paused"))


def get_params(service_id: str, run_id: int) -> dict | None:
    entry = _runs(service_id).get(run_id)
    return entry["params"] if entry else None


def get_started_at(service_id: str, run_id: int) -> str | None:
    entry = _runs(service_id).get(run_id)
    return entry["started_at"] if entry else None


def get_log_file(service_id: str, run_id: int) -> Path | None:
    entry = _runs(service_id).get(run_id)
    return entry["log_file"] if entry else None


def _read_status_file(status_file: Path) -> dict | None:
    try:
        return json.loads(status_file.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return None


def read_status(service_id: str, run_id: int) -> dict | None:
    entry = _runs(service_id).get(run_id)
    if not entry:
        return None
    return _read_status_file(entry["status_file"])


def _settings_file(service_id: str) -> Path:
    return STATUS_DIR / f"{service_id}_settings.json"


def get_settings(service_id: str) -> dict:
    """Текущие сохранённые значения настроек сервиса ({key: value}), или {} если ничего не задано."""
    f = _settings_file(service_id)
    if not f.exists():
        return {}
    try:
        return json.loads(f.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def set_setting(service_id: str, key: str, value: str | None) -> None:
    """value=None убирает настройку (возврат к поведению по умолчанию у сервиса)."""
    settings = get_settings(service_id)
    if value is None:
        settings.pop(key, None)
    else:
        settings[key] = value
    f = _settings_file(service_id)
    tmp = f.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(settings, ensure_ascii=False), encoding="utf-8")
    tmp.replace(f)


def _build_command(service_id: str, cfg: dict, params: dict, status_file: Path) -> list[str]:
    """Собирает командную строку запуска под конкретный сервис из реестра + параметры пользователя."""
    cmd = [cfg["python"], cfg["entry"], cfg["command"]]

    input_kind = cfg.get("input_kind", "none")
    if input_kind == "url_hours":
        if params.get("test"):
            cmd += [params["url"], "--test"]
        else:
            cmd += [params["url"], "--hours", str(params["hours"])]
    elif input_kind == "text":
        cmd += [params["text"]]
    elif input_kind == "number":
        cmd += [str(params["number"])]
    # "none" — без доп. параметров

    cmd += ["--status-file", str(status_file)]

    # Настройки сервиса (прокси/куки и т.п., см. service_registry.py) —
    # общие для всех НОВЫХ потоков этого сервиса, пока не поменяешь ещё раз
    # через "⚙️ Настройки". Пункты, для которых ничего не задано, просто не
    # добавляют флаг — сервис получит своё поведение по умолчанию.
    settings = get_settings(service_id)
    for field in cfg.get("settings", []):
        value = settings.get(field["key"])
        if value:
            cmd += [field["cli_flag"], value]

    return cmd


def _dump_registry() -> None:
    """
    Полный снимок текущих _running на диск — вызывается после КАЖДОЙ мутации
    (старт/стоп/пауза/резюм/естественное завершение). Пишем во временный файл
    и атомарно переименовываем — чтобы падение/убийство Shinoa посреди записи
    не оставило битый JSON, который потом не открыть при восстановлении.
    """
    data = {}
    for service_id, runs in _running.items():
        for run_id, entry in runs.items():
            data[f"{service_id}:{run_id}"] = {
                "service_id": service_id,
                "run_id": run_id,
                "pid": entry["pid"],
                "status_file": str(entry["status_file"]),
                "log_file": str(entry["log_file"]),
                "cmd": entry["cmd"],
                "chat_id": entry["chat_id"],
                "params": entry["params"],
                "started_at": entry["started_at"],
                "paused": entry["paused"],
            }
    tmp = REGISTRY_FILE.with_suffix(".json.tmp")
    try:
        tmp.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        tmp.replace(REGISTRY_FILE)
    except OSError as e:
        logger.warning("Не удалось сохранить registry.json: %s", e)


def _load_registry() -> list[dict]:
    if not REGISTRY_FILE.exists():
        return []
    try:
        data = json.loads(REGISTRY_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    return list(data.values())


async def start(service_id: str, params: dict, chat_id: int, on_update=None, poll_seconds: float = 30.0) -> int:
    """
    Запускает НОВЫЙ поток для сервиса (не блокируется тем, что другие потоки
    того же сервиса уже работают — несколько параллельных потоков штатны).
    chat_id — куда слать уведомления о завершении; сохраняется в реестр на
    диске, поэтому переживает перезапуск Shinoa (см. recover() ниже).
    on_update(run_id, status_dict) — опциональный async-колбэк, вызывается
    каждые poll_seconds, пока поток жив, плюс сразу при переходе в done/error.
    Возвращает run_id нового потока.
    """
    cfg = get_service(service_id)
    if not cfg:
        raise ServiceError(f"Сервис '{service_id}' не найден в реестре")
    if not (cfg.get("python") and cfg.get("entry") and cfg.get("cwd")):
        raise ServiceError(f"Сервис '{service_id}' не настроен на этой машине — проверь пути в .env")

    run_id = _next_run_id.get(service_id, 1)
    _next_run_id[service_id] = run_id + 1

    stamp = int(time.time())
    status_file = STATUS_DIR / f"{service_id}_{run_id}_{stamp}.json"
    log_file = STATUS_DIR / f"{service_id}_{run_id}_{stamp}.log"
    started_at = datetime.now(timezone.utc).isoformat()
    status_file.write_text(json.dumps({
        "status": "running",
        "started_at": started_at,
        "progress_percent": 0,
        "progress_text": None,
        "niche_title": None,
        "elapsed_seconds": None,
        "remaining_seconds": None,
        "total_seconds": None,
        "last_update": started_at,
        "result_text": None,
        "result_file": None,
        "error": None,
    }), encoding="utf-8")

    cmd = _build_command(service_id, cfg, params, status_file)
    logger.info("Запускаю сервис %s (поток #%d): %s", service_id, run_id, " ".join(cmd))

    # Вывод (stdout/stderr) дочернего процесса пишем в .log рядом со status.json,
    # а не глушим в DEVNULL — иначе при зависании/падении без внятного "error"
    # в status.json диагностировать причину было бы невозможно.
    #
    # ВАЖНО: сознательно subprocess.Popen, а не asyncio.create_subprocess_exec —
    # см. большой комментарий в шапке файла (пункт 3) про то, почему asyncio-
    # обёртка сама убивала процесс SIGKILL'ом при остановке Shinoa. Сам вызов
    # Popen() синхронный, но не блокирует событийный цикл сколько-нибудь заметно
    # (это просто fork+exec, микросекунды-миллисекунды) — гонять его через
    # run_in_executor ради этого избыточно.
    log_fh = open(log_file, "wb")
    process = subprocess.Popen(
        cmd,
        cwd=cfg.get("cwd"),
        stdout=log_fh,
        stderr=subprocess.STDOUT,
        start_new_session=True,  # см. большой комментарий в шапке файла, пункт 1
    )

    watcher_task = asyncio.create_task(
        _watch(service_id, run_id, status_file, process.pid, on_update, poll_seconds, local_process=process)
    )
    _runs(service_id)[run_id] = {
        "pid": process.pid,
        "local_process": process,  # только для _is_pid_alive/poll() — не сериализуется в registry.json
        "status_file": status_file,
        "log_file": log_file,
        "log_fh": log_fh,
        "cmd": cmd,
        "chat_id": chat_id,
        "on_update": on_update,  # нужен stop()'у для досрочной доставки результата, см. stop()
        "watcher_task": watcher_task,
        "params": params,
        "started_at": started_at,
        "paused": False,
    }
    _dump_registry()
    return run_id


async def _watch(service_id: str, run_id: int, status_file: Path, pid: int, on_update,
                  poll_seconds: float = 30.0, local_process: subprocess.Popen | None = None):
    """
    Каждые poll_seconds читает status.json и зовёт on_update(run_id, status).
    Живость процесса проверяется через _is_pid_alive — единообразно для
    свежезапущенных потоков (с local_process — заодно неблокирующе "пожинает"
    завершившегося) и для "воскрешённых" после перезапуска Shinoa (без
    local_process — это уже не наши дети, их усыновил init, только kill(pid,0)).

    ВАЖНО про CancelledError: эта задача отменяется в ДВУХ принципиально
    разных случаях, и путать их нельзя.
    1. Поток реально сняли через stop() — тот САМ уже почистил _running и
       registry.json ДО отмены этой задачи, тут дополнительно делать нечего.
    2. Останавливается сама Shinoa — asyncio.run() при выходе отменяет вообще
       все ещё живые задачи, включая эту, хотя дочерний процесс (благодаря
       subprocess.Popen, см. шапку файла) в этот момент прекрасно жив и
       продолжит работать дальше сам по себе. Если бы тут, как раньше, единый
       finally безусловно убирал запись из _running и перезаписывал
       registry.json — на диске остался бы реестр БЕЗ этого потока, то есть
       ровно тот случай, который recover() при следующем старте не может
       найти. Поэтому при отмене мы НИЧЕГО не чистим и не трогаем диск —
       запись должна остаться как есть, чтобы её нашли при следующем запуске.
    Из _running/registry запись убирается только при ЕСТЕСТВЕННОМ завершении
    цикла ниже (поток реально закончился/пропал) — то есть в ветке else.
    """
    def _read():
        return _read_status_file(status_file)

    async def _loop():
        while True:
            await asyncio.sleep(poll_seconds)

            data = _read()
            if data is not None and on_update:
                await on_update(run_id, data)
            if data is not None and data.get("status") in ("done", "error"):
                return

            if not _is_pid_alive(pid, local_process):
                # Процесс исчез, не оставив финального статуса — падение,
                # OOM-killer, или его убили вместе с Shinoa (если Ctrl+C/
                # systemd всё же дотянулись до него, см. шапку файла).
                if data is None:
                    data = {"status": "error", "error": "Процесс завершился без итогового статуса"}
                elif data.get("status") == "running":
                    data = {**data, "status": "error",
                            "error": data.get("error") or "Процесс неожиданно завершился"}
                else:
                    return
                if on_update:
                    await on_update(run_id, data)
                return

    try:
        await _loop()
    except asyncio.CancelledError:
        raise
    else:
        # Сюда попадаем, только если _loop() завершилась сама (return изнутри
        # неё), а не была отменена снаружи — то есть поток реально закончился
        # или пропал. Только теперь безопасно чистить _running и диск.
        entry = _runs(service_id).pop(run_id, None)
        if entry and entry.get("log_fh"):
            try:
                entry["log_fh"].close()
            except OSError:
                pass
        _dump_registry()


async def stop(service_id: str, run_id: int) -> dict | None:
    """
    Досрочно останавливает поток — но НЕ значит "тихо оборвать и выбросить
    данные". Парсер на SIGTERM (см. main.py, _sigterm_to_keyboard_interrupt)
    уходит в тот же graceful-путь, что и ручной Ctrl+C: досчитывает то, что
    успел, дозачищает очередь кандидатов и честно формирует HTML-отчёт по
    уже собранным данным — то есть status.json в итоге получает нормальный
    "done" с результатом, как при обычном истечении времени сбора, просто
    раньше срока.

    В отличие от прошлой версии, САМ результат сюда не доставляет (не зовёт
    on_update) — только возвращает финальный status-словарь вызывающей
    стороне, чтобы та решила, как именно его показать (например, отредактировать
    заранее отправленное "результат придёт сюда же" сообщение — см.
    services_control.cb_svc_delete_thread). Возвращает None, если собственный
    watcher потока успел сам доставить результат обычным путём (через
    on_update), пока мы ждали здесь смерти процесса — тогда досылать/дорисовывать
    нечего, всё уже случилось штатно.
    """
    entry = _runs(service_id).get(run_id)
    if not entry:
        return None
    pid = entry["pid"]
    local_process = entry.get("local_process")
    watcher_task = entry["watcher_task"]
    status_file = entry["status_file"]

    if _is_pid_alive(pid, local_process):
        if entry.get("paused"):
            # замороженный SIGSTOP-ом процесс не реагирует на SIGTERM,
            # его сначала нужно разбудить SIGCONT, иначе он зависнет намертво
            try:
                os.kill(pid, signal.SIGCONT)
            except ProcessLookupError:
                pass
        try:
            os.kill(pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
        # Ждём мягкого завершения ощутимо дольше, чем раньше: после SIGTERM
        # парсер уходит в graceful-путь — а там ещё "дозачистка" очереди
        # кандидатов на "продано" (до MAX_DRAIN_CANDIDATES_AT_END=30 штук по
        # ~1 сек каждая) и генерация HTML-отчёта. 10 секунд было мало —
        # убивали SIGKILL'ом прямо посреди этого, отчёт не успевал сформироваться.
        for _ in range(120):  # ~60 секунд, проверяем каждые 0.5 сек
            if not _is_pid_alive(pid, local_process):
                break
            if run_id not in _runs(service_id):
                # Собственный watcher потока успел сам заметить финальный
                # status.json (у него параллельно идёт свой опрос) и уже
                # доставил результат обычным путём, пока мы тут ждали —
                # нашего вмешательства больше не нужно.
                return None
            await asyncio.sleep(0.5)
        else:
            try:
                os.kill(pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            # Даём крошечный шанс — если SIGKILL прервал ровно в момент
            # дозаписи status.json, пусть файл на диске успеет закрыться.
            await asyncio.sleep(0.3)

    if run_id not in _runs(service_id):
        return None  # см. комментарий выше — watcher уже сам всё доставил

    watcher_task.cancel()
    try:
        await watcher_task
    except asyncio.CancelledError:
        pass

    final = _read_status_file(status_file)
    if not final or final.get("status") not in ("done", "error"):
        # Процесс умер (в т.ч. по нашему SIGKILL после таймаута), не
        # оставив финального статуса — не молчим об этом, отдаём вызывающей
        # стороне синтетическую ошибку вместо пустоты.
        final = {
            "status": "error",
            "error": "Поток остановлен принудительно, финальный статус не был записан",
        }

    _runs(service_id).pop(run_id, None)
    if entry.get("log_fh"):
        try:
            entry["log_fh"].close()
        except OSError:
            pass
    _dump_registry()
    return final


def pause(service_id: str, run_id: int) -> bool:
    entry = _runs(service_id).get(run_id)
    if not entry or not _is_pid_alive(entry["pid"], entry.get("local_process")):
        return False
    try:
        os.kill(entry["pid"], signal.SIGSTOP)
    except ProcessLookupError:
        return False
    entry["paused"] = True
    _dump_registry()
    return True


def resume(service_id: str, run_id: int) -> bool:
    entry = _runs(service_id).get(run_id)
    if not entry or not _is_pid_alive(entry["pid"], entry.get("local_process")):
        return False
    try:
        os.kill(entry["pid"], signal.SIGCONT)
    except ProcessLookupError:
        return False
    entry["paused"] = False
    _dump_registry()
    return True


async def recover(on_update_factory) -> list[tuple[str, int, str]]:
    """
    Вызывается ОДИН РАЗ при старте бота (см. handlers/services_control.
    recover_services, дёргается из bot.py перед start_polling). Проходит по
    сохранённому на диске реестру потоков:

    - если процесс пережил перезапуск (жив и это точно он, не переиспользо-
      ванный pid) — заново подключается к нему: создаёт watcher-задачу и
      кладёт обратно в _running, как будто сама его и запускала. С этого
      момента Shinoa снова умеет показывать его в списке потоков, ставить
      на паузу, снимать, удалять — всё как обычно.
    - если процесс не пережил — читает последний status.json (парсер мог
      сам успеть дойти до done/error, пока Shinoa молчала) и ОДИН РАЗ зовёт
      on_update с этим (или синтезированным "пропал без вести") статусом —
      чтобы результат многочасового сбора не потерялся молча.

    on_update_factory(service_id, run_id, chat_id) -> on_update — вызывающий
    код (services_control) сам знает, как построить колбэк с нужными
    Telegram-уведомлениями/live-редактированием карточки; менеджер сервисов
    сюда лезть не должен, он ничего не знает про Telegram.

    Возвращает список (service_id, run_id, "adopted"|<финальный статус>) —
    просто для лога/диагностики у вызывающей стороны.
    """
    results = []
    for entry in _load_registry():
        service_id, run_id, pid = entry["service_id"], entry["run_id"], entry["pid"]
        cmd = entry.get("cmd") or []
        status_file = Path(entry["status_file"])
        alive = _is_pid_alive(pid) and _cmdline_matches(pid, cmd)
        status = _read_status_file(status_file)
        on_update = on_update_factory(service_id, run_id, entry["chat_id"])

        if alive and (status is None or status.get("status") == "running"):
            watcher_task = asyncio.create_task(_watch(service_id, run_id, status_file, pid, on_update))
            _runs(service_id)[run_id] = {
                "pid": pid,
                "local_process": None,  # не наш ребёнок в этой сессии — усыновлён init'ом
                "status_file": status_file,
                "log_file": Path(entry["log_file"]),
                "log_fh": None,  # не наш дескриптор — процесс пишет в него сам
                "cmd": cmd,
                "chat_id": entry["chat_id"],
                "on_update": on_update,
                "watcher_task": watcher_task,
                "params": entry["params"],
                "started_at": entry["started_at"],
                "paused": entry.get("paused", False),
            }
            _next_run_id[service_id] = max(_next_run_id.get(service_id, 1), run_id + 1)
            logger.info("Восстановлена связь с потоком %s #%d (pid=%d)", service_id, run_id, pid)
            results.append((service_id, run_id, "adopted"))
        else:
            final = status or {"status": "error", "error": "Процесс исчез, пока Shinoa была выключена"}
            if final.get("status") == "running":
                final = {**final, "status": "error",
                         "error": "Процесс пропал, пока Shinoa была выключена (не пережил перезапуск)"}
            logger.info("Поток %s #%d не пережил перезапуск, итог: %s", service_id, run_id, final.get("status"))
            if on_update:
                await on_update(run_id, final)
            results.append((service_id, run_id, final.get("status")))

    _dump_registry()  # реестр теперь отражает только реально восстановленные потоки
    return results

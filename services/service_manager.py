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
    "last_update": "ISO-время",
    "result_text": "краткая сводка метрик для уведомления" или null,
    "result_file": "путь к файлу-результату" или null,
    "error": "текст ошибки" или null
}

Несколько параллельных запусков ("потоков") на один и тот же service_id —
штатный режим, каждый со своим run_id (целое число, растёт по порядку
запуска и не переиспользуется даже после завершения более раннего потока).

Пауза реализована через сигналы ОС SIGSTOP/SIGCONT — процесс физически
замораживается (ноль CPU/сети), но собственные часы дедлайна внутри
парсера при этом не корректируются: длинная пауза "съедает" часть
изначально запрошенного времени сбора, а не продлевает его. Для коротких
пауз это несущественно; точное сохранение "оставшегося времени" требует
доработки самого парсера отдельным заходом.
"""

import asyncio
import json
import logging
import os
import signal
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

from services.service_registry import get_service

logger = logging.getLogger(__name__)

STATUS_DIR = Path(tempfile.gettempdir()) / "shinoa_service_status"
STATUS_DIR.mkdir(exist_ok=True)

# service_id -> {run_id: {"process","status_file","watcher_task","params","started_at","paused"}}
_running: dict[str, dict[int, dict]] = {}
# service_id -> следующий свободный run_id (растёт монотонно, не переиспользуется)
_next_run_id: dict[str, int] = {}


class ServiceError(Exception):
    pass


def _runs(service_id: str) -> dict[int, dict]:
    return _running.setdefault(service_id, {})


def list_runs(service_id: str) -> list[int]:
    """Активные run_id для сервиса, в порядке запуска (по возрастанию номера)."""
    return sorted(_runs(service_id).keys())


def is_running(service_id: str, run_id: int = None) -> bool:
    runs = _runs(service_id)
    if run_id is not None:
        entry = runs.get(run_id)
        return bool(entry and entry["process"].returncode is None)
    return any(e["process"].returncode is None for e in runs.values())


def is_paused(service_id: str, run_id: int) -> bool:
    entry = _runs(service_id).get(run_id)
    return bool(entry and entry.get("paused"))


def get_params(service_id: str, run_id: int) -> dict | None:
    entry = _runs(service_id).get(run_id)
    return entry["params"] if entry else None


def get_started_at(service_id: str, run_id: int) -> str | None:
    entry = _runs(service_id).get(run_id)
    return entry["started_at"] if entry else None


def read_status(service_id: str, run_id: int) -> dict | None:
    entry = _runs(service_id).get(run_id)
    if not entry:
        return None
    try:
        return json.loads(entry["status_file"].read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return None


def _build_command(cfg: dict, params: dict, status_file: Path) -> list[str]:
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
    return cmd


async def start(service_id: str, params: dict, on_update=None, poll_seconds: float = 30.0) -> int:
    """
    Запускает НОВЫЙ поток для сервиса (не блокируется тем, что другие потоки
    того же сервиса уже работают — несколько параллельных потоков штатны).
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

    status_file = STATUS_DIR / f"{service_id}_{run_id}_{int(time.time())}.json"
    started_at = datetime.now(timezone.utc).isoformat()
    status_file.write_text(json.dumps({
        "status": "running",
        "started_at": started_at,
        "progress_percent": 0,
        "progress_text": None,
        "last_update": started_at,
        "result_text": None,
        "result_file": None,
        "error": None,
    }), encoding="utf-8")

    cmd = _build_command(cfg, params, status_file)
    logger.info("Запускаю сервис %s (поток #%d): %s", service_id, run_id, " ".join(cmd))

    process = await asyncio.create_subprocess_exec(
        *cmd,
        cwd=cfg.get("cwd"),
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.DEVNULL,
    )

    watcher_task = asyncio.create_task(
        _watch(service_id, run_id, status_file, process, on_update, poll_seconds)
    )
    _runs(service_id)[run_id] = {
        "process": process,
        "status_file": status_file,
        "watcher_task": watcher_task,
        "params": params,
        "started_at": started_at,
        "paused": False,
    }
    return run_id


async def _watch(service_id: str, run_id: int, status_file: Path, process, on_update, poll_seconds: float = 30.0):
    """
    Каждые poll_seconds зовёт on_update(run_id, status) — даже если файл не
    менялся (так карточка в Telegram видимо "тикает"). Завершение процесса
    отслеживается через явный await на process.wait().
    """
    wait_task = asyncio.create_task(process.wait())

    def _read():
        try:
            return json.loads(status_file.read_text(encoding="utf-8"))
        except (FileNotFoundError, json.JSONDecodeError):
            return None

    try:
        while True:
            done, _ = await asyncio.wait({wait_task}, timeout=poll_seconds)

            data = _read()
            if data is not None:
                if on_update:
                    await on_update(run_id, data)
                if data.get("status") in ("done", "error"):
                    return

            if wait_task in done:
                await asyncio.sleep(0.2)
                data = _read()
                if data is None:
                    data = {"status": "error", "error": "Процесс завершился без итогового статуса"}
                elif data.get("status") == "running":
                    data["status"] = "error"
                    data["error"] = data.get("error") or "Процесс неожиданно завершился"
                if on_update:
                    await on_update(run_id, data)
                return
    except asyncio.CancelledError:
        wait_task.cancel()
        raise
    finally:
        _runs(service_id).pop(run_id, None)


async def stop(service_id: str, run_id: int) -> bool:
    """Полностью убивает и удаляет поток (не путать с pause — эта операция необратима)."""
    entry = _runs(service_id).get(run_id)
    if not entry:
        return False
    process = entry["process"]
    watcher_task = entry["watcher_task"]
    if process.returncode is None:
        if entry.get("paused"):
            # замороженный SIGSTOP-ом процесс не реагирует на terminate(),
            # его сначала нужно разбудить SIGCONT, иначе он зависнет в waitfor
            try:
                process.send_signal(signal.SIGCONT)
            except ProcessLookupError:
                pass
        process.terminate()
        try:
            await asyncio.wait_for(process.wait(), timeout=10)
        except asyncio.TimeoutError:
            process.kill()
    watcher_task.cancel()
    _runs(service_id).pop(run_id, None)
    return True


def pause(service_id: str, run_id: int) -> bool:
    entry = _runs(service_id).get(run_id)
    if not entry or entry["process"].returncode is not None:
        return False
    try:
        entry["process"].send_signal(signal.SIGSTOP)
    except ProcessLookupError:
        return False
    entry["paused"] = True
    return True


def resume(service_id: str, run_id: int) -> bool:
    entry = _runs(service_id).get(run_id)
    if not entry or entry["process"].returncode is not None:
        return False
    try:
        entry["process"].send_signal(signal.SIGCONT)
    except ProcessLookupError:
        return False
    entry["paused"] = False
    return True

"""
Универсальный менеджер сервисов: запуск subprocess, отслеживание статуса,
остановка. Не знает НИЧЕГО о специфике конкретного сервиса (Playerok, Avito,
OSINT-тул) — только общий контракт status.json и путь запуска из реестра.

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

Один активный запуск на service_id одновременно (для личного бота этого
достаточно — если понадобится несколько параллельных запусков одного и того
же сервиса, ключом вместо service_id станет отдельный run_id).
"""

import asyncio
import json
import logging
import os
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

from services.service_registry import get_service

logger = logging.getLogger(__name__)

STATUS_DIR = Path(tempfile.gettempdir()) / "shinoa_service_status"
STATUS_DIR.mkdir(exist_ok=True)

# service_id -> {"process": asyncio.subprocess.Process, "status_file": Path, "watcher_task": asyncio.Task}
_running: dict[str, dict] = {}


class ServiceError(Exception):
    pass


def is_running(service_id: str) -> bool:
    entry = _running.get(service_id)
    if not entry:
        return False
    proc = entry["process"]
    return proc.returncode is None


def list_running() -> list[str]:
    return [sid for sid in _running if is_running(sid)]


def read_status(service_id: str) -> dict | None:
    """Читает status.json активного/последнего запуска сервиса, если он есть."""
    entry = _running.get(service_id)
    if not entry:
        return None
    path = entry["status_file"]
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return None


def _build_command(cfg: dict, params: dict, status_file: Path) -> list[str]:
    """Собирает командную строку запуска под конкретный сервис из реестра + параметры пользователя."""
    cmd = [cfg["python"], cfg["entry"], cfg["command"]]

    input_kind = cfg.get("input_kind", "none")
    if input_kind == "url_hours":
        cmd += [params["url"], "--hours", str(params["hours"])]
    elif input_kind == "text":
        cmd += [params["text"]]
    elif input_kind == "number":
        cmd += [str(params["number"])]
    # "none" — без доп. параметров

    cmd += ["--status-file", str(status_file)]
    return cmd


async def start(service_id: str, params: dict, on_update=None, poll_seconds: float = 10.0) -> Path:
    """
    Запускает сервис. on_update(status_dict) — опциональный async-колбэк,
    вызывается при каждом обнаруженном изменении status.json (для live-
    обновления карточки в Telegram). Возвращает путь к status_file.
    """
    if is_running(service_id):
        raise ServiceError("Этот сервис уже запущен")

    cfg = get_service(service_id)
    if not cfg:
        raise ServiceError(f"Сервис '{service_id}' не найден в реестре")

    status_file = STATUS_DIR / f"{service_id}_{int(time.time())}.json"
    status_file.write_text(json.dumps({
        "status": "running",
        "started_at": datetime.now(timezone.utc).isoformat(),
        "progress_percent": 0,
        "progress_text": None,
        "last_update": datetime.now(timezone.utc).isoformat(),
        "result_text": None,
        "result_file": None,
        "error": None,
    }), encoding="utf-8")

    cmd = _build_command(cfg, params, status_file)
    logger.info("Запускаю сервис %s: %s", service_id, " ".join(cmd))

    process = await asyncio.create_subprocess_exec(
        *cmd,
        cwd=cfg.get("cwd"),
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.DEVNULL,
    )

    watcher_task = asyncio.create_task(_watch(service_id, status_file, process, on_update, poll_seconds))
    _running[service_id] = {
        "process": process,
        "status_file": status_file,
        "watcher_task": watcher_task,
    }
    return status_file


async def _watch(service_id: str, status_file: Path, process, on_update, poll_seconds: float = 5.0):
    """
    Фоновая задача: следит за status_file и процессом, зовёт on_update при
    изменениях. Завершение процесса отслеживается через явный await на
    process.wait() (а не пассивную проверку .returncode в цикле) — это
    надёжнее в разных средах выполнения.
    """
    last_seen = None
    wait_task = asyncio.create_task(process.wait())

    def _read():
        try:
            raw = status_file.read_text(encoding="utf-8")
            return raw, json.loads(raw)
        except (FileNotFoundError, json.JSONDecodeError):
            return None, None

    try:
        while True:
            done, _ = await asyncio.wait({wait_task}, timeout=poll_seconds)

            raw, data = _read()
            if raw is not None and raw != last_seen:
                last_seen = raw
                if on_update:
                    await on_update(data)
                if data.get("status") in ("done", "error"):
                    return

            if wait_task in done:
                # процесс завершился — даём небольшую фору на случай гонки
                # последней записи файла, потом читаем ещё раз финально
                await asyncio.sleep(0.2)
                raw, data = _read()
                if data is None:
                    data = {"status": "error", "error": "Процесс завершился без итогового статуса"}
                elif data.get("status") == "running":
                    data["status"] = "error"
                    data["error"] = data.get("error") or "Процесс неожиданно завершился"
                if on_update and raw != last_seen:
                    await on_update(data)
                return
    except asyncio.CancelledError:
        wait_task.cancel()
        raise
    finally:
        _running.pop(service_id, None)


async def stop(service_id: str) -> bool:
    entry = _running.get(service_id)
    if not entry:
        return False
    process = entry["process"]
    watcher_task = entry["watcher_task"]
    if process.returncode is None:
        process.terminate()
        try:
            await asyncio.wait_for(process.wait(), timeout=10)
        except asyncio.TimeoutError:
            process.kill()
    watcher_task.cancel()
    _running.pop(service_id, None)
    return True

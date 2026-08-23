"""
Подключение к БД, система миграций и резервное копирование.

Миграции применяются автоматически при каждом старте бота.
Перед каждой новой миграцией создаётся резервная копия БД.
"""

import shutil
import logging
import asyncio
from contextlib import asynccontextmanager
from pathlib import Path
from datetime import datetime, date

import aiosqlite

from config import DB_PATH

logger = logging.getLogger(__name__)

MIGRATIONS_DIR = Path(__file__).parent / "migrations"


@asynccontextmanager
async def get_db():
    """Открывает соединение с БД с защитой от блокировок при старте."""
    # timeout=10.0 означает: ждать освобождения БД до 10 секунд
    async with aiosqlite.connect(DB_PATH, timeout=10.0) as db:
        db.row_factory = aiosqlite.Row
        await db.execute("PRAGMA foreign_keys = ON")
        await db.execute("PRAGMA busy_timeout = 10000")  # 10 секунд ожидания
        yield db


async def run_migrations():
    """
    Применяет все новые миграции из папки migrations/.
    Перед первой новой миграцией делает резервную копию БД.
    """
    async with get_db() as db:
        await db.execute("""
            CREATE TABLE IF NOT EXISTS schema_version (
                version INTEGER PRIMARY KEY
            )
        """)
        await db.commit()

        async with db.execute("SELECT MAX(version) FROM schema_version") as cur:
            row = await cur.fetchone()
            current_version = row[0] or 0

        migration_files = sorted(MIGRATIONS_DIR.glob("*.sql"))
        new_migrations = [
            f for f in migration_files
            if int(f.stem.split("_")[0]) > current_version
        ]

        if not new_migrations:
            logger.info("БД актуальна (версия %d)", current_version)
            return

        _backup_before_migration(current_version)

        for migration_file in new_migrations:
            version = int(migration_file.stem.split("_")[0])
            sql = migration_file.read_text(encoding="utf-8")
            logger.info("Применяю миграцию %s...", migration_file.name)
            await db.executescript(sql)
            await db.execute(
                "INSERT OR IGNORE INTO schema_version (version) VALUES (?)",
                (version,),
            )
            await db.commit()
            logger.info("Миграция %d применена", version)


def _backup_before_migration(current_version: int):
    src = Path(DB_PATH)
    if not src.exists():
        return
    
    backups_dir = Path("backups")
    backups_dir.mkdir(exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    dst = backups_dir / f"pre_migration_v{current_version}_{timestamp}.db"
    
    # Выполняем синхронное копирование в отдельном потоке, чтобы не блокировать asyncio
    asyncio.run_coroutine_threadsafe(
        asyncio.to_thread(shutil.copy2, src, dst),
        asyncio.get_event_loop()
    )
    logger.info("Резервная копия создана в фоновом режиме: %s", dst.name)


async def backup_database():
    """Ночной бэкап, вызывается планировщиком каждую ночь в 03:00."""
    src = Path(DB_PATH)
    if not src.exists():
        return

    backups_dir = Path("backups")
    backups_dir.mkdir(exist_ok=True)

    dst = backups_dir / f"daily_{date.today()}.db"
    shutil.copy2(src, dst)
    logger.info("Ночной бэкап создан: %s", dst)

    for old_backup in backups_dir.glob("daily_*.db"):
        try:
            backup_date = date.fromisoformat(old_backup.stem.replace("daily_", ""))
            if (date.today() - backup_date).days > 30:
                old_backup.unlink()
                logger.info("Удалён старый бэкап: %s", old_backup)
        except ValueError:
            pass

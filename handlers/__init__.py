"""
Собирает все роутеры и прокидывает планировщик во все хендлеры.
"""

from aiogram import Router

from handlers import weekly, monthly, daily, list_tasks, snooze, habits, archive, stats, settings, data_processing

main_router = Router()
main_router.include_router(weekly.router)
main_router.include_router(monthly.router)
main_router.include_router(daily.router)
main_router.include_router(list_tasks.router)
main_router.include_router(snooze.router)
main_router.include_router(habits.router)
main_router.include_router(archive.router)
main_router.include_router(stats.router)
main_router.include_router(settings.router)
main_router.include_router(data_processing.router)


def set_scheduler(scheduler):
    weekly.set_scheduler(scheduler)
    monthly.set_scheduler(scheduler)
    daily.set_scheduler(scheduler)
    list_tasks.set_scheduler(scheduler)
    snooze.set_scheduler(scheduler)
    habits.set_scheduler(scheduler)

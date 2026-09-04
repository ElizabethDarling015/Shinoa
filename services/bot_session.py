"""
Сессия aiogram с возможностью менять прокси на лету.

aiogram 3.10 умеет устанавливать прокси через bot.session.proxy = url
(старое соединение закрывается, следующее создаётся через прокси),
но не умеет сбрасывать прокси в None — для возврата к прямому
подключению добавлен clear_proxy().
"""
import ssl

import certifi
from aiohttp import TCPConnector
from aiogram.client.session.aiohttp import AiohttpSession


class ProxySwitchableSession(AiohttpSession):
    def clear_proxy(self) -> None:
        """Возврат к прямому подключению без прокси."""
        self._connector_type = TCPConnector
        self._connector_init = {
            "ssl": ssl.create_default_context(cafile=certifi.where()),
            "limit": 100,
            "ttl_dns_cache": 3600,
        }
        self._proxy = None
        self._should_reset_connector = True
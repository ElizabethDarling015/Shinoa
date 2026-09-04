"""
Парсинг, проверка и гео-определение прокси (SOCKS5 / HTTP / HTTPS).

Гео определяем best-effort цепочкой:
  1) ip-api.com через прокси;
  2) ipify через прокси (узнаём IP) + ip-api по IP без прокси;
  3) если гео недоступно — хотя бы проверяем связность с api.telegram.org.
"""
import logging
import time
from urllib.parse import urlsplit, quote

logger = logging.getLogger(__name__)

SUPPORTED_SCHEMES = {"socks5", "socks4", "http", "https"}
DEFAULT_PORTS = {"socks5": 1080, "socks4": 1080, "http": 8080, "https": 443}


def parse_proxy_text(text: str, fallback_type: str) -> dict | None:
    """
    Принимает форматы:
      socks5://user:pass@host:port, http://host:port, https://...
      host:port
      host:port:user:pass
    Возвращает dict(type, host, port, username, password) или None.
    """
    text = (text or "").strip()
    if not text:
        return None

    username = password = None
    scheme = None

    if "://" in text:
        parts = urlsplit(text)
        scheme = (parts.scheme or "").lower()
        if scheme not in SUPPORTED_SCHEMES:
            return None
        host = parts.hostname
        port = parts.port or DEFAULT_PORTS.get(scheme)
        username = parts.username
        password = parts.password
    else:
        pieces = [p.strip() for p in text.split(":")]
        if len(pieces) == 2:
            host, port = pieces
        elif len(pieces) == 3:
            host, port, username = pieces
        elif len(pieces) >= 4:
            host, port, username = pieces[0], pieces[1], pieces[2]
            password = ":".join(pieces[3:])
        else:
            return None
        scheme = fallback_type

    if not host:
        return None
    try:
        port = int(port)
    except (TypeError, ValueError):
        return None
    if not (1 <= port <= 65535):
        return None

    return {
        "type": scheme,
        "host": host,
        "port": port,
        "username": username or None,
        "password": password or None,
    }


def build_proxy_url(p: dict) -> str:
    """Собирает каноничный URL прокси из словаря."""
    auth = ""
    if p.get("username"):
        auth = quote(p["username"], safe="")
        if p.get("password"):
            auth += ":" + quote(p["password"], safe="")
        auth += "@"
    return f"{p['proxy_type'] if 'proxy_type' in p else p['type']}://{auth}{p['host']}:{p['port']}"


def flag_emoji(country_code: str | None) -> str:
    if not country_code or len(country_code) != 2:
        return "🌐"
    try:
        return "".join(chr(127397 + ord(c)) for c in country_code.upper())
    except Exception:
        return "🌐"


def type_label(t: str) -> str:
    return {
        "socks5": "SOCKS5",
        "socks4": "SOCKS4",
        "https": "HTTPS",
        "http": "HTTP",
    }.get(t, t.upper())


async def _geo_ipapi(session, timeout: float) -> dict | None:
    """Гео через ip-api.com (запрос идёт ЧЕРЕЗ прокси)."""
    import aiohttp
    try:
        async with session.get(
            "http://ip-api.com/json?lang=en&fields=status,country,countryCode,city",
            timeout=aiohttp.ClientTimeout(total=timeout),
        ) as resp:
            if resp.status != 200:
                return None
            data = await resp.json(content_type=None)
        if data.get("status") == "success":
            return {
                "country_code": data.get("countryCode"),
                "country_name": data.get("country"),
                "city": data.get("city"),
            }
    except Exception as e:
        logger.debug("ip-api через прокси не ответил: %s", e)
    return None


async def _geo_ipify_fallback(session, timeout: float) -> dict | None:
    """Узнаём IP через прокси (ipify), а гео по IP смотрим без прокси."""
    import aiohttp
    try:
        async with session.get(
            "https://api.ipify.org?format=json",
            timeout=aiohttp.ClientTimeout(total=timeout),
        ) as resp:
            if resp.status != 200:
                return None
            ip = (await resp.json(content_type=None)).get("ip")
        if not ip:
            return None

        async with aiohttp.ClientSession() as plain:
            async with plain.get(
                f"http://ip-api.com/json/{ip}?lang=en&fields=status,country,countryCode,city",
                timeout=aiohttp.ClientTimeout(total=timeout),
            ) as resp2:
                data = await resp2.json(content_type=None)
        if data.get("status") == "success":
            return {
                "country_code": data.get("countryCode"),
                "country_name": data.get("country"),
                "city": data.get("city"),
            }
    except Exception as e:
        logger.debug("ipify-fallback не сработал: %s", e)
    return None


async def check_proxy(url: str, timeout: float = 10.0) -> dict:
    """
    Проверяет прокси и определяет гео.
    Возвращает {"ok", "ms", "error", "country_code", "country_name", "city"}.
    """
    import aiohttp
    from aiohttp_socks import ProxyConnector

    result = {
        "ok": False, "ms": None, "error": None,
        "country_code": None, "country_name": None, "city": None,
    }
    try:
        connector = ProxyConnector.from_url(url)
    except Exception as e:
        result["error"] = f"Некорректный URL прокси: {e}"
        return result

    session = aiohttp.ClientSession(connector=connector)
    t0 = time.monotonic()
    try:
        geo = await _geo_ipapi(session, timeout)
        if geo is None:
            geo = await _geo_ipify_fallback(session, timeout)
        if geo is None:
            # Гео недоступно — хотя бы проверяем связность с Telegram
            async with session.get(
                "https://api.telegram.org/",
                timeout=aiohttp.ClientTimeout(total=timeout),
            ):
                pass
        result["ok"] = True
        result["ms"] = int((time.monotonic() - t0) * 1000)
        if geo:
            result.update(geo)
    except Exception as e:
        result["error"] = f"{type(e).__name__}: {e}"
    finally:
        await session.close()
    return result
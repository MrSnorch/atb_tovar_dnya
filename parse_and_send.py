#!/usr/bin/env python3
"""
ATB Market — парсер товарів дня з відправкою в Telegram.
"""

import os
import sys
import logging
import requests
import cloudscraper
from bs4 import BeautifulSoup
from datetime import datetime

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
log = logging.getLogger(__name__)

# ─────────────────────── константи ───────────────────────

ATB_URL = "https://www.atbmarket.com/promo/tovar_dnya"
PRODUCT_BASE_URL = "https://www.atbmarket.com"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/147.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "uk,ru;q=0.9,en-US;q=0.8,en;q=0.7",
    "Accept": (
        "text/html,application/xhtml+xml,application/xml;"
        "q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8,"
        "application/signed-exchange;v=b3;q=0.7"
    ),
    "Referer": "https://www.atbmarket.com/",
    "sec-fetch-dest": "document",
    "sec-fetch-mode": "navigate",
    "sec-fetch-site": "same-origin",
    "sec-fetch-user": "?1",
    "upgrade-insecure-requests": "1",
}


# ─────────────────────── парсинг ─────────────────────────

def розпарсити_куки(рядок_куків: str) -> dict:
    """Перетворює рядок куків на словник."""
    куки = {}
    for частина in рядок_куків.split(";"):
        частина = частина.strip()
        if "=" in частина:
            ключ, значення = частина.split("=", 1)
            куки[ключ.strip()] = значення.strip()
    return куки


def завантажити_сторінку(url: str) -> str:
    """
    Завантажує сторінку двома способами:
    1. Через cloudscraper (обходить Cloudflare автоматично)
    2. Якщо не вийшло — через звичайний requests з куками з ATB_COOKIES
    """
    рядок_куків = os.environ.get("ATB_COOKIES", "").strip()
    куки = розпарсити_куки(рядок_куків) if рядок_куків else {}

    # Спроба 1: cloudscraper
    try:
        log.info("Спробуємо cloudscraper...")
        scraper = cloudscraper.create_scraper(
            browser={"browser": "chrome", "platform": "windows", "mobile": False}
        )
        if куки:
            scraper.cookies.update(куки)
        відповідь = scraper.get(url, headers=HEADERS, timeout=30)
        відповідь.raise_for_status()
        log.info("cloudscraper: успіх (HTTP %d)", відповідь.status_code)
        return відповідь.text
    except Exception as помилка:
        log.warning("cloudscraper не спрацював: %s", помилка)

    # Спроба 2: звичайний requests з куками
    if куки:
        log.info("Спробуємо requests + ATB_COOKIES...")
        відповідь = requests.get(url, headers=HEADERS, cookies=куки, timeout=30)
        відповідь.raise_for_status()
        log.info("requests+куки: успіх (HTTP %d)", відповідь.status_code)
        return відповідь.text

    raise RuntimeError(
        "Не вдалося завантажити сторінку. "
        "Задайте ATB_COOKIES у секретах GitHub Actions."
    )


def розпарсити_товари(html: str) -> list[dict]:
    суп = BeautifulSoup(html, "html.parser")
    статті = суп.select("article.catalog-item")

    товари = []
    for стаття in статті:
        # назва
        тег_назви = стаття.select_one(".catalog-item__title a")
        if not тег_назви:
            continue
        назва = тег_назви.get_text(strip=True)
        посилання = PRODUCT_BASE_URL + тег_назви.get("href", "")

        # знижка
        мітка = стаття.select_one(".custom-product-label")
        знижка = мітка.get_text(strip=True) if мітка else ""

        # ціни
        тег_нової_ціни = стаття.select_one(".product-price__top")
        тег_старої_ціни = стаття.select_one(".product-price__bottom")

        нова_ціна = ""
        if тег_нової_ціни:
            знач = тег_нової_ціни.get("value", "")
            нова_ціна = f"{float(знач):.2f} грн" if знач else тег_нової_ціни.get_text(strip=True)

        стара_ціна = ""
        if тег_старої_ціни:
            знач = тег_старої_ціни.get("value", "")
            стара_ціна = f"{float(знач):.2f} грн" if знач else тег_старої_ціни.get_text(strip=True)

        # зображення
        тег_фото = стаття.select_one("picture source[type='image/webp']")
        url_фото = тег_фото.get("srcset", "") if тег_фото else ""
        if not url_фото:
            тег_фото2 = стаття.select_one(".catalog-item__img")
            url_фото = тег_фото2.get("src", "") if тег_фото2 else ""

        товари.append(
            {
                "назва": назва,
                "посилання": посилання,
                "знижка": знижка,
                "нова_ціна": нова_ціна,
                "стара_ціна": стара_ціна,
                "url_фото": url_фото,
            }
        )

    return товари


# ────────────────────── Telegram ─────────────────────────

def надіслати_повідомлення(токен: str, chat_id: str, текст: str, parse_mode: str = "HTML"):
    url = f"https://api.telegram.org/bot{токен}/sendMessage"
    дані = {
        "chat_id": chat_id,
        "text": текст,
        "parse_mode": parse_mode,
        "disable_web_page_preview": True,
    }
    відповідь = requests.post(url, json=дані, timeout=15)
    if not відповідь.ok:
        log.error("Помилка Telegram: %s", відповідь.text)
    return відповідь.ok


def надіслати_фото(токен: str, chat_id: str, url_фото: str, підпис: str):
    url = f"https://api.telegram.org/bot{токен}/sendPhoto"
    дані = {
        "chat_id": chat_id,
        "photo": url_фото,
        "caption": підпис,
        "parse_mode": "HTML",
    }
    відповідь = requests.post(url, json=дані, timeout=15)
    if not відповідь.ok:
        log.warning("Не вдалося надіслати фото (HTTP %s), відправляємо текстом.", відповідь.status_code)
        return надіслати_повідомлення(токен, chat_id, підпис)
    return True


def сформувати_підпис(товар: dict) -> str:
    рядки = []
    if товар["знижка"]:
        рядки.append(f"🔥 <b>Знижка {товар['знижка']}</b>")
    рядки.append(f"📦 <b>{товар['назва']}</b>")
    if товар["нова_ціна"]:
        рядки.append(f"💰 Ціна: <b>{товар['нова_ціна']}</b>")
    if товар["стара_ціна"]:
        рядки.append(f"🏷 Було: <s>{товар['стара_ціна']}</s>")
    рядки.append(f"🔗 <a href=\"{товар['посилання']}\">Купити на ATB Market</a>")
    return "\n".join(рядки)


def сформувати_заголовок(товари: list[dict]) -> str:
    сьогодні = datetime.now().strftime("%d.%m.%Y")
    return (
        f"🛒 <b>Товар дня ATB Market — {сьогодні}</b>\n"
        f"Знайдено товарів зі знижкою: {len(товари)}\n"
        "─────────────────────"
    )


# ────────────────────────── main ──────────────────────────

def main():
    токен = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID")

    if not токен or not chat_id:
        log.error("Не задано TELEGRAM_BOT_TOKEN або TELEGRAM_CHAT_ID")
        sys.exit(1)

    log.info("Завантажуємо сторінку: %s", ATB_URL)
    html = завантажити_сторінку(ATB_URL)

    log.info("Парсимо товари...")
    товари = розпарсити_товари(html)

    if not товари:
        log.warning("Товари не знайдено!")
        надіслати_повідомлення(токен, chat_id, "⚠️ ATB: товари дня сьогодні не знайдено.")
        return

    log.info("Знайдено: %d товарів", len(товари))

    # заголовок
    надіслати_повідомлення(токен, chat_id, сформувати_заголовок(товари))

    # кожен товар окремим повідомленням
    for і, товар in enumerate(товари, 1):
        log.info("[%d/%d] %s", і, len(товари), товар["назва"])
        підпис = сформувати_підпис(товар)
        if товар["url_фото"]:
            надіслати_фото(токен, chat_id, товар["url_фото"], підпис)
        else:
            надіслати_повідомлення(токен, chat_id, підпис)

    log.info("Готово!")


if __name__ == "__main__":
    main()

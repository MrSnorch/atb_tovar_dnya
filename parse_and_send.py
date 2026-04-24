#!/usr/bin/env python3
"""
ATB Market — парсер товарів дня з відправкою в Telegram.
Підтримує пам'ять (стан зберігається у GitHub репо) та щогодинне сканування.
"""

import os
import sys
import json
import logging
import requests
import cloudscraper
from bs4 import BeautifulSoup
from datetime import datetime, timezone

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
log = logging.getLogger(__name__)

# ─────────────────────── константи ───────────────────────

ATB_URL = "https://www.atbmarket.com/promo/tovar_dnya"
PRODUCT_BASE_URL = "https://www.atbmarket.com"
STATE_FILE = "state/sent_today.json"

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


# ─────────────────────── стан (пам'ять) ──────────────────

def сьогодні_дата() -> str:
    """Повертає сьогоднішню дату у форматі YYYY-MM-DD (Київ UTC+3)."""
    from datetime import timedelta
    now_kyiv = datetime.now(timezone.utc) + timedelta(hours=3)
    return now_kyiv.strftime("%Y-%m-%d")


def прочитати_стан() -> dict:
    """Читає стан з локального файлу (вже клонованого репо)."""
    якщо_новий = {"date": "", "sent_ids": [], "products": {}, "total_found": 0, "scans": 0}
    if not os.path.exists(STATE_FILE):
        log.info("Файл стану не знайдено, починаємо з чистого аркуша.")
        return якщо_новий
    try:
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            стан = json.load(f)
        log.info("Прочитано стан: дата=%s, вже надіслано=%d, сканувань=%d",
                 стан.get("date"), len(стан.get("sent_ids", [])), стан.get("scans", 0))
        return стан
    except Exception as e:
        log.warning("Не вдалося прочитати стан: %s. Скидаємо.", e)
        return якщо_новий


def зберегти_стан(стан: dict):
    """Зберігає стан у локальний файл (потім буде закомічено до репо)."""
    os.makedirs(os.path.dirname(STATE_FILE), exist_ok=True)
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(стан, f, ensure_ascii=False, indent=2)
    log.info("Стан збережено: %s", STATE_FILE)


def ід_товару(товар: dict) -> str:
    """Унікальний ідентифікатор товару — шлях URL."""
    return товар["посилання"].replace(PRODUCT_BASE_URL, "").strip("/")


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


def сформувати_підпис(товар: dict, пропущений: bool = False) -> str:
    рядки = []
    if пропущений:
        рядки.append("⚠️ <b>Пропущений товар — знайдено у пізнішому скані!</b>")
    if товар["знижка"]:
        рядки.append(f"🔥 <b>Знижка {товар['знижка']}</b>")
    рядки.append(f"📦 <b>{товар['назва']}</b>")
    if товар["нова_ціна"]:
        рядки.append(f"💰 Ціна: <b>{товар['нова_ціна']}</b>")
    if товар["стара_ціна"]:
        рядки.append(f"🏷 Було: <s>{товар['стара_ціна']}</s>")
    рядки.append(f"🔗 <a href=\"{товар['посилання']}\">Купити на ATB Market</a>")
    return "\n".join(рядки)


def сформувати_заголовок(нові: list, всього: int, сканування: int) -> str:
    сьогодні = datetime.now().strftime("%d.%m.%Y")
    час = datetime.now().strftime("%H:%M")
    перший = сканування == 1
    if перший:
        повернути = (
            f"🛒 <b>Товар дня ATB Market — {сьогодні}</b>\n"
            f"Знайдено товарів зі знижкою: {len(нові)}\n"
            "─────────────────────"
        )
    else:
        повернути = (
            f"🔔 <b>ATB Market — нові товари дня ({час})</b>\n"
            f"Нових товарів у цьому скані: {len(нові)} (всього сьогодні: {всього})\n"
            "─────────────────────"
        )
    return повернути


# ────────────────────────── main ──────────────────────────

def main():
    токен = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID")

    if not токен or not chat_id:
        log.error("Не задано TELEGRAM_BOT_TOKEN або TELEGRAM_CHAT_ID")
        sys.exit(1)

    # ── Читаємо поточний стан ──
    стан = прочитати_стан()
    сьогодні = сьогодні_дата()

    # Якщо новий день — скидаємо пам'ять
    if стан.get("date") != сьогодні:
        log.info("Новий день (%s). Скидаємо стан.", сьогодні)
        стан = {"date": сьогодні, "sent_ids": [], "products": {}, "total_found": 0, "scans": 0}

    вже_надіслані = set(стан.get("sent_ids", []))
    збережені_товари = стан.get("products", {})
    номер_скану = стан.get("scans", 0) + 1

    # ── Парсимо сайт ──
    log.info("Сканування #%d. Завантажуємо сторінку: %s", номер_скану, ATB_URL)
    html = завантажити_сторінку(ATB_URL)

    log.info("Парсимо товари...")
    всі_товари = розпарсити_товари(html)
    log.info("Всього знайдено на сайті: %d", len(всі_товари))

    # ── Фільтр 1: тільки товари з обома цінами ──
    товари_з_цінами = [
        т for т in всі_товари
        if т["нова_ціна"] and т["стара_ціна"]
    ]
    пропущено_без_цін = len(всі_товари) - len(товари_з_цінами)
    if пропущено_без_цін:
        log.info("Пропущено %d товарів без повних цін (нема Ціна або Було).", пропущено_без_цін)

    # ── Фільтр 2: тільки нові (не надіслані раніше) ──
    нові_товари = [т for т in товари_з_цінами if ід_товару(т) not in вже_надіслані]
    пропущені = нові_товари if номер_скану > 1 else []

    log.info("Нових (ще не надісланих, з цінами): %d", len(нові_товари))

    if not нові_товари:
        log.info("Нових товарів немає — нічого не надсилаємо.")
        # Оновлюємо лічильник сканувань навіть якщо нічого нового
        стан["scans"] = номер_скану
        зберегти_стан(стан)
        return

    # ── Надсилаємо заголовок ──
    всього_після = len(вже_надіслані) + len(нові_товари)
    надіслати_повідомлення(
        токен, chat_id,
        сформувати_заголовок(нові_товари, всього_після, номер_скану)
    )

    # ── Надсилаємо кожен новий товар ──
    нові_ід = []
    for і, товар in enumerate(нові_товари, 1):
        log.info("[%d/%d] %s", і, len(нові_товари), товар["назва"])
        чи_пропущений = товар in пропущені
        підпис = сформувати_підпис(товар, пропущений=чи_пропущений)
        if товар["url_фото"]:
            надіслати_фото(токен, chat_id, товар["url_фото"], підпис)
        else:
            надіслати_повідомлення(токен, chat_id, підпис)
        ід = ід_товару(товар)
        нові_ід.append(ід)
        # Зберігаємо повні дані товару
        збережені_товари[ід] = {
            "назва": товар["назва"],
            "знижка": товар["знижка"],
            "нова_ціна": товар["нова_ціна"],
            "стара_ціна": товар["стара_ціна"],
            "посилання": товар["посилання"],
            "url_фото": товар["url_фото"],
            "надіслано_о": datetime.now().strftime("%H:%M"),
        }

    # ── Оновлюємо стан ──
    стан["date"] = сьогодні
    стан["sent_ids"] = list(вже_надіслані | set(нові_ід))
    стан["products"] = збережені_товари
    стан["total_found"] = len(всі_товари)
    стан["scans"] = номер_скану
    зберегти_стан(стан)

    log.info("Готово! Всього надіслано сьогодні: %d товарів.", len(стан["sent_ids"]))


if __name__ == "__main__":
    main()

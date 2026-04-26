#!/usr/bin/env python3
"""
ATB Market — єдиний скрипт.
Запускається щогодинно через cron-job.org → GitHub Actions workflow_dispatch.

Виконує дві задачі:
  1. Товар дня  — парсить /promo/tovar_dnya, шле нові товари зі знижкою.
  2. Нові акції — парсить /promo/all, шле сповіщення про нові каталоги.
"""

import os
import sys
import json
import logging
import requests
import cloudscraper
from bs4 import BeautifulSoup
from datetime import datetime, timezone, timedelta

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
log = logging.getLogger(__name__)

# ═══════════════════════════════════════════════════════════
#  КОНСТАНТИ
# ═══════════════════════════════════════════════════════════

BASE_URL        = "https://www.atbmarket.com"
URL_TOVAR_DNYA  = f"{BASE_URL}/promo/tovar_dnya"
URL_PROMOS      = f"{BASE_URL}/promo/all"

STATE_TOVAR     = "state/sent_today.json"
STATE_PROMOS    = "state/seen_promos.json"

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
    "Referer": f"{BASE_URL}/",
    "sec-fetch-dest": "document",
    "sec-fetch-mode": "navigate",
    "sec-fetch-site": "same-origin",
    "sec-fetch-user": "?1",
    "upgrade-insecure-requests": "1",
}


# ═══════════════════════════════════════════════════════════
#  СПІЛЬНІ УТИЛІТИ
# ═══════════════════════════════════════════════════════════

def київський_час() -> datetime:
    return datetime.now(timezone.utc) + timedelta(hours=3)


def сьогодні_дата() -> str:
    return київський_час().strftime("%Y-%m-%d")


def зараз_рядок() -> str:
    return київський_час().strftime("%Y-%m-%d %H:%M")


def розпарсити_куки(рядок: str) -> dict:
    куки = {}
    for ч in рядок.split(";"):
        ч = ч.strip()
        if "=" in ч:
            к, з = ч.split("=", 1)
            куки[к.strip()] = з.strip()
    return куки


def завантажити_сторінку(url: str) -> str:
    рядок_куків = os.environ.get("ATB_COOKIES", "").strip()
    куки = розпарсити_куки(рядок_куків) if рядок_куків else {}

    try:
        log.info("cloudscraper → %s", url)
        scraper = cloudscraper.create_scraper(
            browser={"browser": "chrome", "platform": "windows", "mobile": False}
        )
        if куки:
            scraper.cookies.update(куки)
        resp = scraper.get(url, headers=HEADERS, timeout=30)
        resp.raise_for_status()
        log.info("OK (HTTP %d)", resp.status_code)
        return resp.text
    except Exception as e:
        log.warning("cloudscraper: %s", e)

    if куки:
        log.info("requests+cookies → %s", url)
        resp = requests.get(url, headers=HEADERS, cookies=куки, timeout=30)
        resp.raise_for_status()
        log.info("OK (HTTP %d)", resp.status_code)
        return resp.text

    raise RuntimeError(
        "Не вдалося завантажити сторінку. "
        "Задайте ATB_COOKIES у секретах GitHub Actions."
    )


def прочитати_стан(path: str, порожній: dict) -> dict:
    if not os.path.exists(path):
        log.info("[%s] не знайдено — перший запуск.", path)
        return порожній
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        log.warning("[%s] помилка читання: %s", path, e)
        return порожній


def зберегти_стан(path: str, стан: dict):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(стан, f, ensure_ascii=False, indent=2)
    log.info("[%s] збережено.", path)


# ═══════════════════════════════════════════════════════════
#  TELEGRAM
# ═══════════════════════════════════════════════════════════

def tg_text(токен: str, chat_id: str, текст: str) -> bool:
    resp = requests.post(
        f"https://api.telegram.org/bot{токен}/sendMessage",
        json={
            "chat_id": chat_id,
            "text": текст,
            "parse_mode": "HTML",
            "disable_web_page_preview": True,
        },
        timeout=15,
    )
    if not resp.ok:
        log.error("sendMessage: %s", resp.text)
    return resp.ok


def tg_photo(токен: str, chat_id: str, photo: str, підпис: str) -> bool:
    resp = requests.post(
        f"https://api.telegram.org/bot{токен}/sendPhoto",
        json={
            "chat_id": chat_id,
            "photo": photo,
            "caption": підпис,
            "parse_mode": "HTML",
        },
        timeout=15,
    )
    if not resp.ok:
        log.warning("sendPhoto не вдалося (HTTP %s), відправляємо текстом.", resp.status_code)
        return tg_text(токен, chat_id, підпис)
    return True


# ═══════════════════════════════════════════════════════════
#  ЗАДАЧА 1: ТОВАР ДНЯ
# ═══════════════════════════════════════════════════════════

def парсити_товари_дня(html: str) -> list[dict]:
    суп = BeautifulSoup(html, "html.parser")
    товари = []
    for стаття in суп.select("article.catalog-item"):
        тег_назви = стаття.select_one(".catalog-item__title a")
        if not тег_назви:
            continue
        назва   = тег_назви.get_text(strip=True)
        посилання = BASE_URL + тег_назви.get("href", "")

        мітка = стаття.select_one(".custom-product-label")
        знижка = мітка.get_text(strip=True) if мітка else ""

        def ціна(sel):
            тег = стаття.select_one(sel)
            if not тег:
                return ""
            знач = тег.get("value", "")
            return f"{float(знач):.2f} грн" if знач else тег.get_text(strip=True)

        нова  = ціна(".product-price__top")
        стара = ціна(".product-price__bottom")

        тег_фото = стаття.select_one("picture source[type='image/webp']")
        фото = тег_фото.get("srcset", "") if тег_фото else ""
        if not фото:
            тег_фото2 = стаття.select_one(".catalog-item__img")
            фото = тег_фото2.get("src", "") if тег_фото2 else ""

        товари.append({
            "назва": назва, "посилання": посилання,
            "знижка": знижка, "нова_ціна": нова,
            "стара_ціна": стара, "фото": фото,
        })
    return товари


def ід_товару(т: dict) -> str:
    return т["посилання"].replace(BASE_URL, "").strip("/")


def підпис_товару(т: dict, пропущений: bool = False) -> str:
    рядки = []
    if пропущений:
        рядки.append("⚠️ <b>Пропущений товар — знайдено у пізнішому скані!</b>")
    if т["знижка"]:
        рядки.append(f"🔥 <b>Знижка {т['знижка']}</b>")
    рядки.append(f"📦 <b>{т['назва']}</b>")
    if т["нова_ціна"]:
        рядки.append(f"💰 Ціна: <b>{т['нова_ціна']}</b>")
    if т["стара_ціна"]:
        рядки.append(f"🏷 Було: <s>{т['стара_ціна']}</s>")
    рядки.append(f"🔗 <a href=\"{т['посилання']}\">Купити на ATB Market</a>")
    return "\n".join(рядки)


def запустити_товар_дня(токен: str, chat_id: str):
    log.info("─── Товар дня ───")

    порожній = {"date": "", "sent_ids": [], "products": {}, "total_found": 0, "scans": 0}
    стан = прочитати_стан(STATE_TOVAR, порожній)
    сьогодні = сьогодні_дата()

    if стан.get("date") != сьогодні:
        log.info("Новий день — скидаємо стан товарів.")
        стан = {**порожній, "date": сьогодні}

    вже_надіслані  = set(стан["sent_ids"])
    збережені      = стан["products"]
    номер_скану    = стан["scans"] + 1

    html = завантажити_сторінку(URL_TOVAR_DNYA)
    всі  = парсити_товари_дня(html)
    log.info("Знайдено товарів: %d", len(всі))

    з_цінами = [т for т in всі if т["нова_ціна"] and т["стара_ціна"]]
    нові     = [т for т in з_цінами if ід_товару(т) not in вже_надіслані]
    log.info("Нових (з цінами, не надісланих): %d", len(нові))

    if not нові:
        log.info("Товар дня: нічого нового.")
        стан["scans"] = номер_скану
        зберегти_стан(STATE_TOVAR, стан)
        return

    # заголовок
    сьогодні_str = київський_час().strftime("%d.%m.%Y")
    час_str      = київський_час().strftime("%H:%M")
    якщо_перший  = номер_скану == 1
    заголовок = (
        f"🛒 <b>Товар дня — {сьогодні_str}</b>\n"
        f"Знайдено товарів зі знижкою: {len(нові)}\n"
        "─────────────────────"
    ) if якщо_перший else (
        f"🔔 <b>Товар дня — нові ({час_str})</b>\n"
        f"Нових у цьому скані: {len(нові)} (всього сьогодні: {len(вже_надіслані) + len(нові)})\n"
        "─────────────────────"
    )
    tg_text(токен, chat_id, заголовок)

    нові_ід = []
    for і, т in enumerate(нові, 1):
        log.info("[%d/%d] %s", і, len(нові), т["назва"])
        пропущений = номер_скану > 1
        підпис = підпис_товару(т, пропущений)
        if т["фото"]:
            tg_photo(токен, chat_id, т["фото"], підпис)
        else:
            tg_text(токен, chat_id, підпис)
        ід = ід_товару(т)
        нові_ід.append(ід)
        збережені[ід] = {
            "назва": т["назва"], "знижка": т["знижка"],
            "нова_ціна": т["нова_ціна"], "стара_ціна": т["стара_ціна"],
            "посилання": т["посилання"], "фото": т["фото"],
            "надіслано_о": час_str,
        }

    стан["date"]        = сьогодні
    стан["sent_ids"]    = list(вже_надіслані | set(нові_ід))
    стан["products"]    = збережені
    стан["total_found"] = len(всі)
    стан["scans"]       = номер_скану
    зберегти_стан(STATE_TOVAR, стан)
    log.info("Товар дня: надіслано %d товарів.", len(нові))


# ═══════════════════════════════════════════════════════════
#  ЗАДАЧА 2: НОВІ АКЦІЙНІ КАТАЛОГИ
# ═══════════════════════════════════════════════════════════

def парсити_акції(html: str) -> list[dict]:
    суп = BeautifulSoup(html, "html.parser")
    результат = []
    for елемент in суп.select(".actions-list__item"):
        тег_а = елемент.select_one(".actions-list__img")
        if not тег_а:
            continue
        href = тег_а.get("href", "")
        slug = href.rstrip("/").split("/")[-1] if href else ""
        if not slug:
            continue

        тег_назви = елемент.select_one(".actions-list__title a")
        title = тег_назви.get_text(strip=True) if тег_назви else slug

        тег_img = елемент.select_one(".actions-list__img img")
        img = тег_img.get("src", "") if тег_img else ""

        тег_таймера = елемент.select_one(".actionsTimer")
        end_time = тег_таймера.get("data-time", "") if тег_таймера else ""

        результат.append({
            "slug": slug, "title": title,
            "url": BASE_URL + href, "img": img,
            "end_time": end_time,
        })
    return результат


def форматувати_кінець(s: str) -> str:
    if not s:
        return ""
    try:
        return datetime.strptime(s, "%b %d, %Y %H:%M:%S").strftime("%d.%m.%Y")
    except ValueError:
        return s


def підпис_акції(а: dict) -> str:
    кінець = форматувати_кінець(а["end_time"])
    рядки = [
        "🆕 <b>Нова акція!</b>",
        f"📣 <b>{а['title']}</b>",
    ]
    if кінець:
        рядки.append(f"📅 Діє до: <b>{кінець}</b>")
    рядки.append(f"🔗 <a href=\"{а['url']}\">Переглянути акцію</a>")
    return "\n".join(рядки)


def запустити_моніторинг_акцій(токен: str, chat_id: str):
    log.info("─── Нові акції ───")

    порожній = {"seen_slugs": [], "promos": {}, "last_check": ""}
    стан = прочитати_стан(STATE_PROMOS, порожній)
    відомі = set(стан["seen_slugs"])
    збережені = стан["promos"]
    перший_запуск = len(відомі) == 0

    html = завантажити_сторінку(URL_PROMOS)
    акції = парсити_акції(html)
    log.info("Знайдено акцій на сайті: %d", len(акції))

    if not акції:
        log.warning("Акції не розпарсились — можливо змінилась структура сторінки.")
        стан["last_check"] = зараз_рядок()
        зберегти_стан(STATE_PROMOS, стан)
        return

    зараз = зараз_рядок()

    ВИКЛЮЧЕНІ_СЛАГИ = {"TovaR_dnyA"}

    if перший_запуск:
        акції_для_збереження = [а for а in акції if а["slug"] not in ВИКЛЮЧЕНІ_СЛАГИ]
        log.info("Перший запуск — зберігаємо %d акцій як відомі.", len(акції_для_збереження))
        for а in акції_для_збереження:
            збережені[а["slug"]] = {
                "title": а["title"], "url": а["url"],
                "img": а["img"], "end_time": а["end_time"],
                "first_seen": зараз,
            }
        стан["seen_slugs"] = [а["slug"] for а in акції_для_збереження]
        стан["promos"]     = збережені
        стан["last_check"] = зараз
        зберегти_стан(STATE_PROMOS, стан)

        # сповіщення про запуск
        рядки_акцій = ""
        for а in акції_для_збереження:
            кінець = форматувати_кінець(а["end_time"])
            рядок = f"• <a href=\"{а['url']}\">{а['title']}</a>"
            if кінець:
                рядок += f" (до {кінець})"
            рядки_акцій += рядок + "\n"

        tg_text(токен, chat_id,
            f"🛒 <b>Актуальні акції: {len(акції_для_збереження)}</b>\n"
            "─────────────────────\n"
            + рядки_акцій
        )
        return

    нові = [а for а in акції if а["slug"] not in відомі and а["slug"] not in ВИКЛЮЧЕНІ_СЛАГИ]
    log.info("Нових акцій: %d", len(нові))

    for а in нові:
        log.info("НОВА АКЦІЯ: %s | %s", а["slug"], а["title"])
        підпис = підпис_акції(а)
        if а["img"]:
            tg_photo(токен, chat_id, а["img"], підпис)
        else:
            tg_text(токен, chat_id, підпис)
        збережені[а["slug"]] = {
            "title": а["title"], "url": а["url"],
            "img": а["img"], "end_time": а["end_time"],
            "first_seen": зараз,
        }
        відомі.add(а["slug"])

    if not нові:
        log.info("Нових акцій немає.")

    стан["seen_slugs"] = list(відомі)
    стан["promos"]     = збережені
    стан["last_check"] = зараз
    зберегти_стан(STATE_PROMOS, стан)


# ═══════════════════════════════════════════════════════════
#  ТОЧКА ВХОДУ
# ═══════════════════════════════════════════════════════════

def main():
    токен   = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID")

    if not токен or not chat_id:
        log.error("Не задано TELEGRAM_BOT_TOKEN або TELEGRAM_CHAT_ID")
        sys.exit(1)

    запустити_товар_дня(токен, chat_id)
    запустити_моніторинг_акцій(токен, chat_id)
    log.info("═══ Готово ═══")


if __name__ == "__main__":
    main()

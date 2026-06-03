#!/usr/bin/env python3
"""
ATB Market — єдиний скрипт.
Запускається щогодинно через cron-job.org → GitHub Actions workflow_dispatch.

Виконує дві задачі:
  1. Товар дня  — парсить /promo/tovar_dnya, шле нові товари зі знижкою
                  окремими повідомленнями (як в оригіналі).
  2. Актуальні акції — парсить /promo/all, підтримує ОДНЕ повідомлення
                  яке редагується щогодини зі списком усіх акцій і датами.
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

MAX_MSG_LEN     = 4096

ВИКЛЮЧЕНІ_СЛАГИ = {"TovaR_dnyA"}

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
        if resp.status_code == 404:
            log.warning("cloudscraper: 404 — сторінка не знайдена.")
            return None
        resp.raise_for_status()
        log.info("OK (HTTP %d)", resp.status_code)
        return resp.text
    except Exception as e:
        log.warning("cloudscraper: %s", e)

    if куки:
        log.info("requests+cookies → %s", url)
        try:
            resp = requests.get(url, headers=HEADERS, cookies=куки, timeout=30)
            resp.raise_for_status()
            log.info("OK (HTTP %d)", resp.status_code)
            return resp.text
        except requests.exceptions.HTTPError as e:
            if e.response is not None and e.response.status_code == 404:
                log.warning("requests+cookies: 404 — сторінка не знайдена.")
                return None
            raise

    raise RuntimeError(
        "Не вдалося завантажити сторінку. "
        "Задайте ATB_COOKIES у секретах GitHub Actions."
    )


def прочитати_стан(path: str, порожній: dict) -> dict:
    if not os.path.exists(path):
        log.info("[%s] не знайдено — перший запуск.", path)
        return порожній.copy()
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        log.warning("[%s] помилка читання: %s", path, e)
        return порожній.copy()


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


def tg_send(токен: str, chat_id: str, текст: str) -> int | None:
    """Відправляє нове повідомлення. Повертає message_id або None."""
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
        return None
    msg_id = resp.json().get("result", {}).get("message_id")
    log.info("sendMessage OK → message_id=%s", msg_id)
    return msg_id


def tg_edit(токен: str, chat_id: str, message_id: int, текст: str) -> bool:
    """Редагує існуюче повідомлення. Повертає True при успіху."""
    resp = requests.post(
        f"https://api.telegram.org/bot{токен}/editMessageText",
        json={
            "chat_id": chat_id,
            "message_id": message_id,
            "text": текст,
            "parse_mode": "HTML",
            "disable_web_page_preview": True,
        },
        timeout=15,
    )
    if not resp.ok:
        err = resp.json().get("description", resp.text)
        if "message is not modified" in err.lower():
            log.info("editMessageText: текст не змінився — ОК")
            return True
        log.error("editMessageText: %s", err)
        return False
    log.info("editMessageText OK → message_id=%s", message_id)
    return True


def tg_send_or_edit(токен: str, chat_id: str, message_id: int | None, текст: str) -> int | None:
    """Редагує повідомлення якщо є message_id, інакше відправляє нове.
    Обрізка по рядках — HTML-теги не розриваються посередині.
    """
    if len(текст) > MAX_MSG_LEN:
        рядки = текст.splitlines()
        результат = []
        загальна = 0
        for рядок in рядки:
            довжина = len(рядок) + 1
            if загальна + довжина > MAX_MSG_LEN - 4:
                результат.append("…")
                break
            результат.append(рядок)
            загальна += довжина
        текст = '\n'.join(результат)

    if message_id:
        success = tg_edit(токен, chat_id, message_id, текст)
        if success:
            return message_id
        log.warning("Редагування не вдалось — відправляємо нове повідомлення.")

    return tg_send(токен, chat_id, текст)


# ═══════════════════════════════════════════════════════════
#  ЗАДАЧА 1: ТОВАР ДНЯ (окремі повідомлення, як в оригіналі)
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


def запустити_товар_дня(токен: str, chat_id: str, стан: dict, сьогодні: str):
    log.info("─── Товар дня ───")

    if стан.get("date") != сьогодні:
        log.info("Новий день — скидаємо стан товарів.")
        # зберігаємо promos_message_id та ID вчорашніх товарів щоб не відправити їх повторно
        # якщо ATB ще не оновив сторінку після опівночі
        promos_msg_id = стан.get("promos_message_id")
        prev_day_ids  = list(стан.get("sent_ids", []))
        стан.clear()
        стан.update({
            "date": сьогодні,
            "sent_ids": [],
            "prev_day_ids": prev_day_ids,
            "products": {},
            "total_found": 0,
            "scans": 0,
            "promos_message_id": promos_msg_id,
        })

    вже_надіслані = set(стан.get("sent_ids", []))
    збережені     = стан.get("products", {})
    номер_скану   = стан.get("scans", 0) + 1

    html = завантажити_сторінку(URL_TOVAR_DNYA)
    if html is None:
        log.info("Товар дня: сторінка недоступна (404) — пропускаємо.")
        стан["scans"] = номер_скану
        return
    всі  = парсити_товари_дня(html)
    log.info("Знайдено товарів: %d", len(всі))

    з_цінами = [т for т in всі if т["нова_ціна"] and т["стара_ціна"]]
    нові     = [т for т in з_цінами if ід_товару(т) not in вже_надіслані]

    # Захист від хибного спрацювання одразу після опівночі:
    # якщо всі "нові" товари — це товари з вчорашнього дня (ATB ще не оновив сторінку),
    # пропускаємо їх. Як тільки з'явиться хоча б один справді новий товар — скидаємо захист.
    prev_day_ids = set(стан.get("prev_day_ids", []))
    if prev_day_ids:
        справді_нові = [т for т in нові if ід_товару(т) not in prev_day_ids]
        if not справді_нові:
            log.info("Товари збігаються з вчорашніми — ATB ще не оновив сторінку, пропускаємо.")
            стан["scans"] = номер_скану
            return
        # Сторінка вже оновилась — більше не потрібен захист
        log.info("Сторінка оновилась — скидаємо список вчорашніх товарів.")
        стан["prev_day_ids"] = []

    log.info("Нових (з цінами, не надісланих): %d", len(нові))

    if not нові:
        log.info("Товар дня: нічого нового.")
        стан["scans"] = номер_скану
        return

    сьогодні_str = київський_час().strftime("%d.%m.%Y")
    час_str      = київський_час().strftime("%H:%M")
    якщо_перший  = номер_скану == 1
    заголовок = (
        f"🛒 <b>Товар дня — {сьогодні_str}</b>\n"
        f"Знайдено товарів зі знижкою: {len(нові)}"
    ) if якщо_перший else (
        f"🔔 <b>Товар дня — нові ({час_str})</b>\n"
        f"Нових у цьому скані: {len(нові)} (всього сьогодні: {len(вже_надіслані) + len(нові)})"
    )
    tg_text(токен, chat_id, заголовок)

    нові_ід = []
    for і, т in enumerate(нові, 1):
        log.info("[%d/%d] %s", і, len(нові), т["назва"])
        підпис = підпис_товару(т, пропущений=(номер_скану > 1))
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
    log.info("Товар дня: надіслано %d товарів.", len(нові))


# ═══════════════════════════════════════════════════════════
#  ЗАДАЧА 2: АКЦІЇ — одне повідомлення, редагується щогодини
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
        if not slug or slug in ВИКЛЮЧЕНІ_СЛАГИ:
            continue

        тег_назви = елемент.select_one(".actions-list__title a")
        title     = тег_назви.get_text(strip=True) if тег_назви else slug

        тег_таймера = елемент.select_one(".actionsTimer")
        end_time    = тег_таймера.get("data-time", "") if тег_таймера else ""

        тег_фото = елемент.select_one(".actions-list__img img")
        фото = тег_фото.get("src", "") if тег_фото else ""

        результат.append({
            "slug": slug, "title": title,
            "url": BASE_URL + href, "end_time": end_time,
            "фото": фото,
        })
    return результат


def форматувати_кінець(s: str) -> str:
    if not s:
        return ""
    try:
        return datetime.strptime(s, "%b %d, %Y %H:%M:%S").strftime("%d.%m.%Y")
    except ValueError:
        return s


def очистити_протерміновані_акції(стан: dict) -> list[str]:
    зараз     = київський_час().replace(tzinfo=None)
    збережені = стан.get("promos", {})
    видалені  = []
    for slug in list(стан.get("seen_slugs", [])):
        end_raw = збережені.get(slug, {}).get("end_time", "")
        if not end_raw:
            continue
        try:
            end_dt = datetime.strptime(end_raw, "%b %d, %Y %H:%M:%S")
        except ValueError:
            continue
        if зараз >= end_dt:
            log.info("Акція закінчилась — видаляємо: %s", slug)
            видалені.append(slug)
    for slug in видалені:
        стан["seen_slugs"].remove(slug)
        збережені.pop(slug, None)
    return видалені


def _кінець_для_сортування(end_time: str) -> datetime:
    """Повертає datetime для сортування; без дати — у кінець списку."""
    if not end_time:
        return datetime.max.replace(tzinfo=None)
    try:
        return datetime.strptime(end_time, "%b %d, %Y %H:%M:%S")
    except ValueError:
        return datetime.max.replace(tzinfo=None)


def _видима_довжина(html_рядок: str) -> int:
    """Підраховує кількість видимих символів (без HTML-тегів)."""
    import re
    return len(re.sub(r"<[^>]+>", "", html_рядок))


def сформувати_повідомлення_акцій(акції: list[dict], є_товар_дня: bool = False) -> str:
    актуальні = [а for а in акції if а["slug"] not in ВИКЛЮЧЕНІ_СЛАГИ]

    # Сортуємо: 1) за датою закінчення (найближча зверху),
    #           2) всередині однієї дати — за довжиною назви зростаючо
    #              (коротші зверху → знизу назви стають довшими)
    актуальні.sort(key=lambda а: (
        _кінець_для_сортування(а["end_time"]),
        len(а["title"]),
    ))

    кількість = len(актуальні) + (1 if є_товар_дня else 0)
    рядки = [f"📣 <b>Актуальні акції — {кількість} шт</b>"]

    # Товар дня — тільки якщо сьогодні є товари зі знижкою
    if є_товар_дня:
        рядки.append(f"<a href=\"{URL_TOVAR_DNYA}\">Товар дня</a>")

    for а in актуальні:
        кінець = форматувати_кінець(а["end_time"])
        рядок  = f"<a href=\"{а['url']}\">{а['title']}</a>"
        if кінець:
            рядок += f" до {кінець}"
        рядки.append(рядок)

    return "\n".join(рядки)


def запустити_акції(токен: str, chat_id: str, стан_товар: dict, стан_акції: dict):
    log.info("─── Актуальні акції ───")

    очистити_протерміновані_акції(стан_акції)

    html = завантажити_сторінку(URL_PROMOS)
    акції = парсити_акції(html)
    log.info("Знайдено акцій: %d", len(акції))

    # Оновлюємо стан акцій, шлемо окремі повідомлення про нові
    зараз = зараз_рядок()
    збережені = стан_акції.get("promos", {})
    перший_запуск = len(стан_акції.get("seen_slugs", [])) == 0

    for а in акції:
        is_new = а["slug"] not in стан_акції.get("seen_slugs", [])
        if is_new:
            стан_акції.setdefault("seen_slugs", []).append(а["slug"])
            # Не шлемо сповіщення при першому запуску — тільки запам'ятовуємо
            if not перший_запуск:
                log.info("Нова акція: %s | %s", а["slug"], а["title"])
                кінець = форматувати_кінець(а["end_time"])
                рядки = [
                    "🆕 <b>Нова акція!</b>",
                    f"📣 <b>{а['title']}</b>",
                ]
                if кінець:
                    рядки.append(f"📅 Діє до: <b>{кінець}</b>")
                рядки.append(f"🔗 <a href=\"{а['url']}\">Переглянути акцію</a>")
                текст = "\n".join(рядки)
                if а.get("фото"):
                    tg_photo(токен, chat_id, а["фото"], текст)
                else:
                    tg_text(токен, chat_id, текст)
        збережені[а["slug"]] = {
            "title":      а["title"],
            "url":        а["url"],
            "end_time":   а["end_time"],
            "first_seen": збережені.get(а["slug"], {}).get("first_seen", зараз),
        }
    стан_акції["promos"]     = збережені
    стан_акції["last_check"] = зараз

    # Відправляємо або редагуємо єдине повідомлення
    message_id = стан_товар.get("promos_message_id")
    є_товар_дня = bool(стан_товар.get("sent_ids"))
    текст = сформувати_повідомлення_акцій(акції, є_товар_дня)
    новий_id = tg_send_or_edit(токен, chat_id, message_id, текст)
    стан_товар["promos_message_id"] = новий_id
    log.info("Акції: повідомлення оновлено (message_id=%s)", новий_id)


# ═══════════════════════════════════════════════════════════
#  ТОЧКА ВХОДУ
# ═══════════════════════════════════════════════════════════

def main():
    токен   = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID")

    if not токен or not chat_id:
        log.error("Не задано TELEGRAM_BOT_TOKEN або TELEGRAM_CHAT_ID")
        sys.exit(1)

    сьогодні = сьогодні_дата()

    порожній_товар = {
        "date": "", "sent_ids": [], "prev_day_ids": [], "products": {},
        "total_found": 0, "scans": 0, "promos_message_id": None,
    }
    стан_товар = прочитати_стан(STATE_TOVAR, порожній_товар)

    порожній_акції = {"seen_slugs": [], "promos": {}, "last_check": ""}
    стан_акції = прочитати_стан(STATE_PROMOS, порожній_акції)

    запустити_товар_дня(токен, chat_id, стан_товар, сьогодні)
    запустити_акції(токен, chat_id, стан_товар, стан_акції)

    зберегти_стан(STATE_TOVAR, стан_товар)
    зберегти_стан(STATE_PROMOS, стан_акції)
    log.info("═══ Готово ═══")


if __name__ == "__main__":
    main()

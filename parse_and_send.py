#!/usr/bin/env python3
"""
ATB Market - парсер товаров дня с отправкой в Telegram.
"""

import os
import re
import sys
import json
import logging
import requests
from bs4 import BeautifulSoup
from datetime import datetime

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
log = logging.getLogger(__name__)

# ─────────────────────── константы ───────────────────────

ATB_URL = "https://www.atbmarket.com/promo/tovar_dnya"
PRODUCT_BASE_URL = "https://www.atbmarket.com"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "uk,ru;q=0.9,en;q=0.8",
    "Accept": (
        "text/html,application/xhtml+xml,application/xml;"
        "q=0.9,image/avif,image/webp,*/*;q=0.8"
    ),
    "Referer": "https://www.atbmarket.com/",
}


# ─────────────────────── парсинг ─────────────────────────

def fetch_page(url: str) -> str:
    resp = requests.get(url, headers=HEADERS, timeout=30)
    resp.raise_for_status()
    return resp.text


def parse_products(html: str) -> list[dict]:
    soup = BeautifulSoup(html, "html.parser")
    articles = soup.select("article.catalog-item")

    products = []
    for art in articles:
        # название
        title_tag = art.select_one(".catalog-item__title a")
        if not title_tag:
            continue
        title = title_tag.get_text(strip=True)
        link = PRODUCT_BASE_URL + title_tag.get("href", "")

        # скидка
        label = art.select_one(".custom-product-label")
        discount = label.get_text(strip=True) if label else ""

        # цены
        price_new_tag = art.select_one(".product-price__top")
        price_old_tag = art.select_one(".product-price__bottom")

        price_new = ""
        if price_new_tag:
            val = price_new_tag.get("value", "")
            price_new = f"{float(val):.2f} грн" if val else price_new_tag.get_text(strip=True)

        price_old = ""
        if price_old_tag:
            val = price_old_tag.get("value", "")
            price_old = f"{float(val):.2f} грн" if val else price_old_tag.get_text(strip=True)

        # изображение
        img_tag = art.select_one("picture source[type='image/webp']")
        img_url = img_tag.get("srcset", "") if img_tag else ""
        if not img_url:
            img_tag2 = art.select_one(".catalog-item__img")
            img_url = img_tag2.get("src", "") if img_tag2 else ""

        products.append(
            {
                "title": title,
                "link": link,
                "discount": discount,
                "price_new": price_new,
                "price_old": price_old,
                "img_url": img_url,
            }
        )

    return products


# ────────────────────── Telegram ─────────────────────────

def send_telegram_message(token: str, chat_id: str, text: str, parse_mode: str = "HTML"):
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = {
        "chat_id": chat_id,
        "text": text,
        "parse_mode": parse_mode,
        "disable_web_page_preview": True,
    }
    resp = requests.post(url, json=payload, timeout=15)
    if not resp.ok:
        log.error("Telegram error: %s", resp.text)
    return resp.ok


def send_telegram_photo(token: str, chat_id: str, photo_url: str, caption: str):
    url = f"https://api.telegram.org/bot{token}/sendPhoto"
    payload = {
        "chat_id": chat_id,
        "photo": photo_url,
        "caption": caption,
        "parse_mode": "HTML",
    }
    resp = requests.post(url, json=payload, timeout=15)
    # если фото не грузится — шлём текстом
    if not resp.ok:
        log.warning("Photo send failed (%s), falling back to text.", resp.status_code)
        return send_telegram_message(token, chat_id, caption)
    return True


def build_caption(product: dict) -> str:
    lines = []
    if product["discount"]:
        lines.append(f"🔥 <b>Скидка {product['discount']}</b>")
    lines.append(f"📦 <b>{product['title']}</b>")
    if product["price_new"]:
        lines.append(f"💰 Цена: <b>{product['price_new']}</b>")
    if product["price_old"]:
        lines.append(f"🏷 Было: <s>{product['price_old']}</s>")
    lines.append(f"🔗 <a href=\"{product['link']}\">Купить на ATB Market</a>")
    return "\n".join(lines)


def build_header(products: list[dict]) -> str:
    today = datetime.now().strftime("%d.%m.%Y")
    return (
        f"🛒 <b>Товар дня ATB Market — {today}</b>\n"
        f"Найдено товаров со скидкой: {len(products)}\n"
        "─────────────────────"
    )


# ────────────────────────── main ──────────────────────────

def main():
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID")

    if not token or not chat_id:
        log.error("Не заданы TELEGRAM_BOT_TOKEN или TELEGRAM_CHAT_ID")
        sys.exit(1)

    log.info("Загружаем страницу: %s", ATB_URL)
    html = fetch_page(ATB_URL)

    log.info("Парсим товары...")
    products = parse_products(html)

    if not products:
        log.warning("Товары не найдены!")
        send_telegram_message(token, chat_id, "⚠️ ATB: товары дня сегодня не найдены.")
        return

    log.info("Найдено: %d товаров", len(products))

    # заголовок
    send_telegram_message(token, chat_id, build_header(products))

    # каждый товар отдельным сообщением
    for i, p in enumerate(products, 1):
        log.info("[%d/%d] %s", i, len(products), p["title"])
        caption = build_caption(p)
        if p["img_url"]:
            send_telegram_photo(token, chat_id, p["img_url"], caption)
        else:
            send_telegram_message(token, chat_id, caption)

    log.info("Готово!")


if __name__ == "__main__":
    main()

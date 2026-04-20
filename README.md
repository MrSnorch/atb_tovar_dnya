# ATB Market — Товар дня → Telegram

Скрипт парсит страницу **«Товар дня»** на сайте ATB Market и ежедневно отправляет товары со скидками в указанный Telegram-канал/чат.

---

## Структура репозитория

```
├── parse_and_send.py          # основной скрипт
├── requirements.txt           # зависимости Python
└── .github/
    └── workflows/
        └── atb_daily.yml      # GitHub Actions (запуск каждый день в 09:00 Киев)
```

---

## Быстрый старт

### 1. Создать Telegram-бота

1. Напишите [@BotFather](https://t.me/BotFather) → `/newbot`
2. Сохраните полученный **токен** (`123456:ABC-DEF...`)

### 2. Получить Chat ID канала/чата

| Способ | Как |
|--------|-----|
| **Личный чат** | Напишите боту `/start`, затем откройте `https://api.telegram.org/bot<TOKEN>/getUpdates` — найдите `"chat":{"id":...}` |
| **Канал** | Добавьте бота в канал как администратора, перешлите любое сообщение из канала в [@username_to_id_bot](https://t.me/username_to_id_bot) |
| **Группа** | Добавьте [@getidsbot](https://t.me/getidsbot) в группу |

> Для каналов ID начинается с `-100...`

### 3. Добавить секреты в GitHub

Перейдите: **Settings → Secrets and variables → Actions → New repository secret**

| Имя секрета | Значение |
|-------------|----------|
| `TELEGRAM_BOT_TOKEN` | токен бота от BotFather |
| `TELEGRAM_CHAT_ID` | ID чата/канала |

### 4. Залить файлы в репозиторий

```bash
git init
git add .
git commit -m "init: ATB parser"
git remote add origin https://github.com/YOUR_USERNAME/YOUR_REPO.git
git push -u origin main
```

### 5. Готово!

Скрипт запустится автоматически каждый день в **09:00 по Киеву**.  
Чтобы запустить вручную: вкладка **Actions → ATB Товар дня → Run workflow**.

---

## Расписание запуска

По умолчанию — `0 6 * * *` (06:00 UTC = 09:00 Киев, UTC+3).

Если нужно другое время — отредактируйте `cron` в `.github/workflows/atb_daily.yml`:

```
# Примеры (UTC):
"0 5 * * *"   → 08:00 Киев
"0 7 * * *"   → 10:00 Киев
"30 5 * * *"  → 08:30 Киев
```

---

## Локальный запуск

```bash
pip install -r requirements.txt

export TELEGRAM_BOT_TOKEN="ваш_токен"
export TELEGRAM_CHAT_ID="ваш_chat_id"

python parse_and_send.py
```

---

## Пример сообщения в Telegram

```
🛒 Товар дня ATB Market — 20.04.2025
Найдено товаров со скидкой: 3
─────────────────────

🔥 Скидка -47%
📦 Сир м'який 180 г Золотий резерв Моцарелла 45%
💰 Цена: 49.50 грн
🏷 Было: 93.90 грн
🔗 Купить на ATB Market
```

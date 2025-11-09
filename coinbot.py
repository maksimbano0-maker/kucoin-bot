import requests
import time
import csv
import io
import schedule
import threading
import json
from datetime import datetime, timedelta
from telegram import Bot, Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes

# === НАСТРОЙКИ ===
TELEGRAM_TOKEN = "8007539995:AAGr5TNq6P9oUaInoAcX8TIduWVhOC0UnIo"
CHAT_IDS = ["969434824"]
GOOGLE_SHEET_CSV_URL = "https://docs.google.com/spreadsheets/d/1M3nf9qp9uDCIkIOR_Qp1-gU5qemZd7NYX3vorhOZcKc/export?format=csv"
LOG_FILE = "prices.log"

bot = Bot(token=TELEGRAM_TOKEN)

# === ЛОГИРОВАНИЕ ===
def log_growth(symbol, days, price, is_break=False):
    """Записать в лог только при ≥5 днях роста или падении"""
    if days < 5 and not is_break:
        return

    entry = {
        "time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "symbol": symbol,
        "growth_days": days,
        "current_price": price,
        "event": "break" if is_break else "growth"
    }
    with open(LOG_FILE, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    print(f"ЛОГ: {symbol} — {days} дней роста → {'падение' if is_break else 'рост'}")

def clear_old_logs():
    """Оставить только последние 30 дней"""
    try:
        cutoff = (datetime.now() - timedelta(days=30)).timestamp()
        lines = []
        with open(LOG_FILE, "r", encoding="utf-8") as f:
            for line in f:
                try:
                    data = json.loads(line)
                    log_time = datetime.strptime(data["time"], "%Y-%m-%d %H:%M:%S").timestamp()
                    if log_time > cutoff:
                        lines.append(line)
                except:
                    continue
        with open(LOG_FILE, "w", encoding="utf-8") as f:
            f.writelines(lines)
    except:
        pass

# === API ===
def get_symbols_from_sheet():
    try:
        response = requests.get(GOOGLE_SHEET_CSV_URL, timeout=10)
        response.raise_for_status()
        data = csv.reader(io.StringIO(response.text))
        symbols = [row[0].strip().upper() for row in data if row]
        symbols = [s for s in symbols if s and not s.lower().startswith("symbol")]
        return symbols
    except Exception as e:
        print(f"Ошибка загрузки таблицы: {e}")
        return []

def get_futures_candles(base_symbol, days=10):
    symbol = base_symbol + "USDTM" if not base_symbol.endswith("USDTM") else base_symbol
    url = f"https://api-futures.kucoin.com/api/v1/kline/query?symbol={symbol}&granularity=1440"
    try:
        r = requests.get(url, timeout=10).json()
        if r.get('code') != '200000' or not r.get('data'):
            return []
        candles = sorted(r['data'], key=lambda x: int(x[0]))
        return candles[-days:]
    except Exception as e:
        print(f"API ошибка {symbol}: {e}")
        return []

# === АНАЛИЗ РОСТА ===
def analyze_growth(symbol):
    candles = get_futures_candles(symbol, 10)
    if len(candles) < 2:
        return 0, None, None, False

    closes = [float(c[2]) for c in candles]
    current = closes[-1]
    prev = closes[-2]

    growth_days = 0
    for i in range(len(closes)-1, 0, -1):
        if closes[i] > closes[i-1]:
            growth_days += 1
        else:
            break

    is_break = (growth_days >= 6) and (current < prev)
    return growth_days, current, prev, is_break

# === УТРЕННЯЯ ПРОВЕРКА (9:00) — ТОЛЬКО ЛОГ ===
def check_morning():
    print(f"\n[{datetime.now()}] УТРЕННЯЯ проверка (9:00) — логируем ≥5 дней")
    symbols = get_symbols_from_sheet()
    if not symbols:
        print("Нет монет!")
        return

    for symbol in symbols:
        try:
            growth_days, price, _, _ = analyze_growth(symbol)
            base = symbol.replace("USDTM", "")
            if growth_days >= 5:
                log_growth(base, growth_days, price)
        except Exception as e:
            print(f"Ошибка {symbol}: {e}")
    clear_old_logs()

# === ВЕЧЕРНЯЯ ПРОВЕРКА (21:00) — АЛЕРТЫ С 5-ГО ДНЯ! ===
def check_evening():
    print(f"\n[{datetime.now()}] ВЕЧЕРНЯЯ проверка (21:00) — АЛЕРТЫ с 5-го дня!")
    symbols = get_symbols_from_sheet()
    if not symbols:
        print("Нет монет!")
        return

    for symbol in symbols:
        try:
            growth_days, price, prev_price, is_break = analyze_growth(symbol)
            base = symbol.replace("USDTM", "")

            if is_break:
                msg = f"ПАДЕНИЕ {base}: после {growth_days} дней роста! Цена: ${price:.2f}"
                for chat_id in CHAT_IDS:
                    bot.send_message(chat_id=chat_id, text=msg)
                log_growth(base, growth_days, price, is_break=True)

            elif growth_days >= 8:
                msg = f"СИЛЬНЫЙ РОСТ {base}: {growth_days} дней подряд! Цена: ${price:.2f}"
                for chat_id in CHAT_IDS:
                    bot.send_message(chat_id=chat_id, text=msg)
                log_growth(base, growth_days, price)

            elif growth_days >= 5:
                msg = f"РОСТ {base}: {growth_days} дней подряд! Цена: ${price:.2f}"
                for chat_id in CHAT_IDS:
                    bot.send_message(chat_id=chat_id, text=msg)
                log_growth(base, growth_days, price)

        except Exception as e:
            print(f"Ошибка {symbol}: {e}")
    clear_old_logs()

# === ИНТЕРАКТИВ ===
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "KuCoin Futures Pro Bot\n\n"
        "• 9:00 — логируем рост ≥5 дней\n"
        "• 21:00 — АЛЕРТЫ с 5-го дня!\n"
        "• Рост: 5, 6, 7, 8+ дней\n"
        "• Падение после 6+ дней\n"
        "• Лог: prices.log\n\n"
        "Отправь: `ETH`, `XBT 3`"
    )

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip().upper()
    parts = text.split()
    base_symbol = parts[0]
    days = int(parts[1]) if len(parts) > 1 and parts[1].isdigit() else 5
    days = min(days, 30)

    await update.message.reply_chat_action("typing")
    candles = get_futures_candles(base_symbol, days + 1)
    if not candles:
        await update.message.reply_text(f"Нет данных: {base_symbol}")
        return

    closes = [float(c[2]) for c in candles]
    current = closes[-1]
    start = closes[0]
    growth = ((current - start) / start) * 100

    days_word = "день" if days == 1 else "дня" if days <= 4 else "дней"
    emoji = "UP" if growth > 0 else "DOWN" if growth < 0 else "FLAT"
    price_fmt = f"${current:.2f}" if current >= 10 else f"${current:.6f}"

    message = (
        f"<b>{base_symbol}/USDT</b>\n"
        f"Цена: <code>{price_fmt}</code>\n"
        f"{emoji} Рост за {days} {days_word}: <b>{growth:+.2f}%</b>"
    )
    await update.message.reply_text(message, parse_mode='HTML')

# === ПЛАНИРОВЩИК ===
def run_scheduler():
    schedule.every().day.at("09:00").do(check_morning)
    schedule.every().day.at("21:00").do(check_evening)
    print("Планировщик: 9:00 (лог) | 21:00 (алерты с 5-го дня!)")
    while True:
        schedule.run_pending()
        time.sleep(60)

# === ЗАПУСК ===
if __name__ == "__main__":
    print("KuCoin Pro Bot запущен!")

    # Создаём пустой лог
    open(LOG_FILE, "a").close()

    # Планировщик в фоне
    threading.Thread(target=run_scheduler, daemon=True).start()

    # Бот
    app = Application.builder().token(TELEGRAM_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    print("Готов! Проверки: 9:00 (лог) | 21:00 (алерты с 5-го дня!)")
    app.run_polling(allowed_updates=Update.ALL_TYPES)
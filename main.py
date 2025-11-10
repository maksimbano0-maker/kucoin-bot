import os
import requests
import csv
import io
import schedule
import json
import time
import threading
from datetime import datetime, timedelta
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes
import asyncio

# === КОНФИГ ===
TOKEN = os.getenv("TELEGRAM_TOKEN")
if not TOKEN:
    print("ОШИБКА: TELEGRAM_TOKEN не задан!")
    exit(1)

CHAT_IDS = ["969434824"]
GOOGLE_SHEET_CSV_URL = "https://docs.google.com/spreadsheets/d/1M3nf9qp9uDCIkIOR_Qp1-gU5qemZd7NYX3vorhOZcKc/export?format=csv"
LOG_FILE = "prices.log"

# === ЛОГИ ===
def log_growth(symbol, days, price, is_break=False):
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

def clear_old_logs():
    try:
        cutoff = datetime.now() - timedelta(days=30)
        lines = []
        with open(LOG_FILE, "r", encoding="utf-8") as f:
            for line in f:
                try:
                    data = json.loads(line)
                    log_time = datetime.strptime(data["time"], "%Y-%m-%d %H:%M:%S")
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
        r = requests.get(GOOGLE_SHEET_CSV_URL, timeout=10)
        r.raise_for_status()
        return [row[0].strip().upper() for row in csv.reader(io.StringIO(r.text)) if row and not row[0].lower().startswith("symbol")]
    except Exception as e:
        print(f"Таблица: {e}")
        return []

def get_futures_candles(base_symbol, days=10):
    symbol = base_symbol + "USDTM" if not base_symbol.endswith("USDTM") else base_symbol
    try:
        r = requests.get(f"https://api-futures.kucoin.com/api/v1/kline/query?symbol={symbol}&granularity=1440", timeout=10).json()
        if r.get('code') != '200000' or not r.get('data'):
            return []
        return sorted(r['data'], key=lambda x: int(x[0]))[-days:]
    except Exception as e:
        print(f"API {symbol}: {e}")
        return []

# === АНАЛИЗ ===
def analyze_growth(symbol):
    candles = get_futures_candles(symbol, 10)
    if len(candles) < 2:
        return 0, None, None, False
    closes = [float(c[2]) for c in candles]
    current, prev = closes[-1], closes[-2]
    growth_days = 0
    for i in range(len(closes)-1, 0, -1):
        if closes[i] > closes[i-1]:
            growth_days += 1
        else:
            break
    is_break = growth_days >= 6 and current < prev
    return growth_days, current, prev, is_break

# === ПРОВЕРКИ ===
def check_morning():
    print(f"\n[{datetime.now()}] Утро: лог ≥5 дней")
    for s in get_symbols_from_sheet():
        g, p, _, _ = analyze_growth(s)
        if g >= 5:
            log_growth(s.replace("USDTM", ""), g, p)
    clear_old_logs()

def check_evening():
    print(f"\n[{datetime.now()}] Вечер: алерты с 5-го дня!")
    app = Application.builder().token(TOKEN).build()
    for s in get_symbols_from_sheet():
        g, p, _, ib = analyze_growth(s)
        base = s.replace("USDTM", "")
        msg = ""
        if ib:
            msg = f"ПАДЕНИЕ {base}: после {g} дней! Цена: ${p:.2f}"
        elif g >= 8:
            msg = f"СИЛЬНЫЙ РОСТ {base}: {g} дней! Цена: ${p:.2f}"
        elif g >= 5:
            msg = f"РОСТ {base}: {g} дней! Цена: ${p:.2f}"
        if msg:
            for cid in CHAT_IDS:
                asyncio.run(app.bot.send_message(chat_id=cid, text=msg))
            log_growth(base, g, p, ib)
    clear_old_logs()

# === ПЛАНИРОВЩИК ===
def run_scheduler():
    schedule.every().day.at("09:00").do(check_morning)
    schedule.every().day.at("21:00").do(check_evening)
    print("Планировщик запущен")
    while True:
        schedule.run_pending()
        time.sleep(60)

# === ТЕЛЕГРАМ ===
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "KuCoin Pro Bot\n\n"
        "• 9:00 — лог\n"
        "• 21:00 — алерты\n"
        "Пиши: `ETH`"
    )

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip().upper().split()
    symbol = text[0]
    days = min(int(text[1]) if len(text) > 1 and text[1].isdigit() else 5, 30)
    candles = get_futures_candles(symbol, days + 1)
    if not candles:
        await update.message.reply_text(f"Нет данных: {symbol}")
        return
    closes = [float(c[2]) for c in candles]
    current, start = closes[-1], closes[0]
    growth = (current - start) / start * 100
    emoji = "ЗЕЛЕНАЯ КРУЖОК" if growth > 0 else "КРАСНАЯ КРУЖОК" if growth < 0 else "БЕЛАЯ КРУЖОК"
    price_fmt = f"${current:.2f}" if current >= 10 else f"${current:.6f}"
    await update.message.reply_text(
        f"<b>{symbol}/USDT</b>\n"
        f"Цена: <code>{price_fmt}</code>\n"
        f"{emoji} Рост за {days} дней: <b>{growth:+.2f}%</b>",
        parse_mode='HTML'
    )

# === ГЛАВНАЯ ФУНКЦИЯ ===
async def main():
    print("KuCoin Pro Bot запущен!")
    open(LOG_FILE, "a").close()
    threading.Thread(target=run_scheduler, daemon=True).start()

    app = Application.builder().token(TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    print("Готов! Проверки: 9:00 и 21:00")
    await app.run_polling()

# === ЗАПУСК ===
if __name__ == "__main__":
    asyncio.run(main())

import os
import time
import logging
import requests
import pandas as pd
import numpy as np

# ==========================================
# Logging Configuration
# ==========================================
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)

# ==========================================
# Environment Variables
# ==========================================
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")
CRYPTOCOMPARE_API_KEY = os.getenv("CRYPTOCOMPARE_API_KEY")

COINS = [
    "BTC", "ETH", "BNB", "SOL", "XRP",
    "ADA", "FLOKI", "SHIB", "EIGEN", "OP", "DOGE", "NEAR",
]

# ==========================================
# Constants
# ==========================================
TELEGRAM_MAX_LENGTH = 4096
API_RATE_LIMIT_DELAY = 0.35     # วินาที ระหว่าง API calls
API_MAX_RETRIES = 3
API_RETRY_DELAY = 2.0           # วินาที ก่อน retry
HISTOHOUR_LIMIT = 2000          # แท่ง 1H → resample 4H ได้ ~500 แท่ง (พอสำหรับ EMA 200)
RSI_PERIOD = 14
EMA_SHORT = 50
EMA_LONG = 200
RSI_OVERSOLD = 35
RSI_OVERBOUGHT = 65
DIVERGENCE_LOOKBACK = 15

# TP tier ตาม volatility กลุ่มเหรียญ (% จาก current price)
TP_TIERS = {
    "major":  {"tp": 0.08, "sl_buffer": 0.02},   # BTC, ETH
    "mid":    {"tp": 0.12, "sl_buffer": 0.025},  # BNB, SOL, XRP, ADA, NEAR, OP
    "small":  {"tp": 0.18, "sl_buffer": 0.03},   # FLOKI, SHIB, EIGEN, DOGE
}
COIN_TIER = {
    "BTC": "major", "ETH": "major",
    "BNB": "mid",   "SOL": "mid",   "XRP": "mid",
    "ADA": "mid",   "NEAR": "mid",  "OP": "mid",
    "FLOKI": "small","SHIB": "small","EIGEN": "small","DOGE": "small",
}


# ==========================================
# Telegram
# ==========================================
def send_telegram_message(text_msg: str) -> None:
    """ส่งข้อความ Telegram โดยแบ่งอัตโนมัติถ้าเกิน 4,096 ตัวอักษร"""
    token = str(TELEGRAM_BOT_TOKEN or "").strip()
    chat_id = str(TELEGRAM_CHAT_ID or "").strip()

    if not token or not chat_id:
        logger.error("TELEGRAM_BOT_TOKEN หรือ TELEGRAM_CHAT_ID ไม่ได้ตั้งค่า")
        return

    url = f"https://api.telegram.org/bot{token}/sendMessage"
    chunks = [
        text_msg[i : i + TELEGRAM_MAX_LENGTH]
        for i in range(0, len(text_msg), TELEGRAM_MAX_LENGTH)
    ]

    for idx, chunk in enumerate(chunks, start=1):
        payload = {"chat_id": chat_id, "text": chunk, "parse_mode": "HTML"}
        try:
            resp = requests.post(url, json=payload, timeout=10)
            if resp.status_code == 200:
                logger.info(f"Telegram ส่งสำเร็จ (ส่วน {idx}/{len(chunks)})")
            else:
                logger.warning(f"Telegram ส่งล้มเหลว (ส่วน {idx}): {resp.text}")
        except Exception as e:
            logger.error(f"Exception ขณะส่ง Telegram (ส่วน {idx}): {e}")

        if idx < len(chunks):
            time.sleep(0.5)  # หน่วงระหว่าง chunk เพื่อไม่โดน flood limit


# ==========================================
# Data Fetching
# ==========================================
def get_historical_data(coin: str) -> pd.DataFrame | None:
    """
    ดึงข้อมูล OHLCV 1H จาก CryptoCompare แล้ว resample เป็น 4H
    - ดึง 2,000 แท่ง 1H → ~500 แท่ง 4H (มากพอสำหรับ EMA 200)
    - มี retry logic สำหรับ network error / rate limit
    """
    url = "https://min-api.cryptocompare.com/data/v2/histohour"
    params = {
        "fsym": coin,
        "tsym": "USD",
        "limit": HISTOHOUR_LIMIT,
        "api_key": CRYPTOCOMPARE_API_KEY,
    }

    for attempt in range(1, API_MAX_RETRIES + 1):
        try:
            resp = requests.get(url, params=params, timeout=15)
            data = resp.json()

            if data.get("Response") == "Success":
                df = pd.DataFrame(data["Data"]["Data"])
                df["time"] = pd.to_datetime(df["time"], unit="s")
                df.set_index("time", inplace=True)

                df_4h = df.resample("4h").agg(
                    {
                        "open": "first",
                        "high": "max",
                        "low": "min",
                        "close": "last",
                        "volumeto": "sum",
                    }
                ).dropna()

                logger.info(f"{coin}: ดึงข้อมูลสำเร็จ ({len(df_4h)} แท่ง 4H)")
                return df_4h

            else:
                logger.warning(f"{coin} attempt {attempt}: API ตอบกลับผิดปกติ – {data.get('Message')}")

        except requests.exceptions.Timeout:
            logger.warning(f"{coin} attempt {attempt}: Request timeout")
        except Exception as e:
            logger.warning(f"{coin} attempt {attempt}: {e}")

        if attempt < API_MAX_RETRIES:
            time.sleep(API_RETRY_DELAY * attempt)  # exponential backoff

    logger.error(f"{coin}: ดึงข้อมูลล้มเหลวทั้ง {API_MAX_RETRIES} ครั้ง")
    return None


# ==========================================
# Indicators
# ==========================================
def calculate_indicators(df: pd.DataFrame) -> pd.DataFrame:
    """
    คำนวณ EMA 50, EMA 200, RSI 14
    - RSI ใช้ Wilder's Smoothing (com = period - 1) ตามมาตรฐาน TradingView
    - Volume MA 20 สำหรับยืนยัน signal
    """
    close = df["close"]

    # EMA
    df["EMA_50"] = close.ewm(span=EMA_SHORT, adjust=False).mean()
    df["EMA_200"] = close.ewm(span=EMA_LONG, adjust=False).mean()

    # RSI – Wilder's Smoothing: alpha = 1 / period → com = period - 1
    delta = close.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.ewm(com=RSI_PERIOD - 1, adjust=False).mean()
    avg_loss = loss.ewm(com=RSI_PERIOD - 1, adjust=False).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    df["RSI"] = (100 - (100 / (1 + rs))).fillna(100)

    # Volume MA 20
    df["VOL_MA20"] = df["volumeto"].rolling(20).mean()

    return df


# ==========================================
# Signal Logic
# ==========================================
def check_bullish_divergence(df: pd.DataFrame, lookback: int = DIVERGENCE_LOOKBACK) -> bool:
    """
    Bullish Divergence ที่ถูกต้อง:
      - หา Low ราคาก่อนหน้าใน window และ RSI ณ จุดนั้น
      - เปรียบเทียบกับ Low ราคาปัจจุบันและ RSI ปัจจุบัน
      - ราคา: Lower Low  +  RSI: Higher Low  =  True Divergence
    """
    if len(df) < lookback + 2:
        return False

    window = df.iloc[-lookback:]
    prev_window = window.iloc[:-1]

    # จุด Low ราคาก่อนหน้า
    prev_low_idx = prev_window["low"].idxmin()
    prev_low_price = prev_window.loc[prev_low_idx, "low"]
    prev_low_rsi = prev_window.loc[prev_low_idx, "RSI"]

    curr_price = df["low"].iloc[-1]
    curr_rsi = df["RSI"].iloc[-1]

    return (curr_price < prev_low_price) and (curr_rsi > prev_low_rsi)


def is_volume_confirmed(row: pd.Series) -> bool:
    """Volume แท่งปัจจุบันสูงกว่า MA 20 หรือไม่"""
    if pd.isna(row.get("VOL_MA20")) or row["VOL_MA20"] == 0:
        return False
    return row["volumeto"] > row["VOL_MA20"]


# ==========================================
# Formatting
# ==========================================
def format_price(price: float) -> str:
    if price < 0.0001:
        return f"{price:.8f}"
    elif price < 0.001:
        return f"{price:.6f}"
    elif price < 1:
        return f"{price:.4f}"
    else:
        return f"{price:.2f}"


# ==========================================
# Market Scanner
# ==========================================
def scan_market():
    buy_signals = []
    sell_signals = []
    bullish_coins = 0
    bearish_coins = 0
    total_valid_coins = 0
    coin_trends_summary = []

    for coin in COINS:
        df = get_historical_data(coin)
        time.sleep(API_RATE_LIMIT_DELAY)  # Rate limiting

        if df is None or len(df) < EMA_LONG + 10:
            logger.warning(f"{coin}: ข้อมูลไม่พอ (ต้องการ > {EMA_LONG + 10} แท่ง) – ข้ามเหรียญนี้")
            continue

        df = calculate_indicators(df)
        row = df.iloc[-1]

        current_price = row["close"]
        rsi = row["RSI"]
        ema_50 = row["EMA_50"]
        ema_200 = row["EMA_200"]
        vol_confirmed = is_volume_confirmed(row)

        total_valid_coins += 1
        is_divergence = check_bullish_divergence(df)
        rsi_rounded = round(rsi, 2)

        tier = COIN_TIER.get(coin, "mid")
        tp_pct = TP_TIERS[tier]["tp"]
        sl_buf = TP_TIERS[tier]["sl_buffer"]
        vol_tag = " 🔊" if vol_confirmed else ""

        signal_type = ""

        if current_price > ema_200:
            coin_trend = "🟢 ขาขึ้น (Above EMA 200)"
            bullish_coins += 1
            coin_trends_summary.append(f"• {coin}: 🟢 ขาขึ้น (RSI: {rsi_rounded})")

            if current_price > (ema_50 * 0.98) and rsi <= RSI_OVERSOLD:
                signal_type = f"RSI Oversold + Pullback 📉{vol_tag}"
            elif is_divergence:
                signal_type = f"Bullish Divergence 📈{vol_tag}"
        else:
            coin_trend = "🔴 ขาลง (Below EMA 200)"
            bearish_coins += 1
            coin_trends_summary.append(f"• {coin}: 🔴 ขาลง (RSI: {rsi_rounded})")

            if rsi <= RSI_OVERSOLD:
                signal_type = f"RSI Oversold (ขาลง-เสี่ยงสูง) 📉{vol_tag}"
            elif is_divergence:
                signal_type = f"Bullish Divergence (สวนเทรนด์) 📈{vol_tag}"

        if signal_type:
            entry_min = format_price(current_price * 0.97)
            entry_max = format_price(current_price * 1.00)
            target_profit = format_price(current_price * (1 + tp_pct))
            sl_val = ema_200 * (1 - sl_buf) if current_price > ema_200 else current_price * (1 - sl_buf)
            stop_loss = format_price(sl_val)

            buy_signals.append(
                {
                    "coin": coin,
                    "trend": coin_trend,
                    "price": format_price(current_price),
                    "rsi": rsi_rounded,
                    "type": signal_type,
                    "ema_50": format_price(ema_50),
                    "ema_200": format_price(ema_200),
                    "entry": f"${entry_min} - ${entry_max}",
                    "tp": f"${target_profit} (+{tp_pct*100:.0f}%)",
                    "sl": f"${stop_loss}",
                    "vol_confirmed": vol_confirmed,
                }
            )

        if rsi >= RSI_OVERBOUGHT:
            tp_min = format_price(current_price * 1.00)
            tp_max = format_price(current_price * (1 + tp_pct * 0.4))
            exit_val = ema_50 if current_price > ema_50 else current_price * (1 - sl_buf)
            safety_exit = format_price(exit_val)

            sell_signals.append(
                {
                    "coin": coin,
                    "trend": coin_trend,
                    "price": format_price(current_price),
                    "rsi": rsi_rounded,
                    "ema_50": format_price(ema_50),
                    "ema_200": format_price(ema_200),
                    "tp_zone": f"${tp_min} - ${tp_max}",
                    "exit": f"${safety_exit}",
                    "vol_confirmed": vol_confirmed,
                }
            )

    # Market summary
    if total_valid_coins > 0:
        bullish_ratio = (bullish_coins / total_valid_coins) * 100
        summary_msg = f"📊 <b>[Market Trend Summary]</b>\n"
        summary_msg += f"📈 ขาขึ้น: {bullish_coins} เหรียญ | 📉 ขาลง: {bearish_coins} เหรียญ\n"

        if bullish_ratio >= 65:
            summary_msg += (
                "🔥 ภาพรวม: <b>🟢 ขาขึ้นชัดเจน (Strong Bullish)</b>\n"
                "<i>กลยุทธ์: เน้นดักซื้อเมื่อเกิดการย่อตัว (Buy on Dip)</i>"
            )
        elif bullish_ratio >= 40:
            summary_msg += (
                "🔥 ภาพรวม: <b>🟡 ไซด์เวย์ / เลือกทาง (Sideways)</b>\n"
                "<i>กลยุทธ์: ตลาดก้ำกึ่ง ควรเลือกเทรดเฉพาะตัวที่มีสัญญาณชัดเจน</i>"
            )
        else:
            summary_msg += (
                "🔥 ภาพรวม: <b>🔴 ขาลง / พักฐานแรง (Bearish)</b>\n"
                "<i>กลยุทธ์: ตลาดมีความเสี่ยงสูง เน้นถือเงินสดหรือลดขนาดไม้ลง</i>"
            )

        summary_msg += "\n\n📋 <b>สรุปแนวโน้มรายเหรียญ:</b>\n"
        summary_msg += "\n".join(coin_trends_summary)
    else:
        summary_msg = "⚠️ ไม่สามารถดึงข้อมูลเหรียญเพื่อวิเคราะห์ภาพรวมได้"

    return buy_signals, sell_signals, summary_msg


# ==========================================
# Message Builder
# ==========================================
def build_message(buy_list: list, sell_list: list, market_summary: str) -> str:
    final_message = f"{market_summary}\n"

    if buy_list or sell_list:
        final_message += "\n=========================\n"

        if buy_list:
            final_message += "🎯 <b>[Coindesk Crypto Screener 4H - สัญญาณช้อนซื้อ]</b>"
            for opt in buy_list:
                vol_note = "\n🔊 Volume: <b>ยืนยันสัญญาณ (สูงกว่า MA20)</b>" if opt["vol_confirmed"] else "\n🔇 Volume: ไม่ยืนยัน (ต่ำกว่า MA20)"
                final_message += (
                    f"\n\n🪙 <b>เหรียญ: {opt['coin']}</b>"
                    f"\n📊 เทรนด์: {opt['trend']}"
                    f"\n🚨 รูปแบบ: {opt['type']}"
                    f"\n💵 ราคาปัจจุบัน: ${opt['price']}"
                    f"\n📉 RSI (4H): {opt['rsi']}"
                    f"\n📈 เส้น EMA 50 / 200: ${opt['ema_50']} / ${opt['ema_200']}"
                    f"{vol_note}"
                    f"\n🟢 ช่วงเข้าซื้อ: <code>{opt['entry']}</code>"
                    f"\n🔴 เป้าหมายขาย (TP): <code>{opt['tp']}</code>"
                    f"\n❌ จุดตัดขาดทุน (SL): <code>{opt['sl']}</code>"
                )

            if sell_list:
                final_message += "\n\n=========================\n"

        if sell_list:
            final_message += "⚠️ <b>[Coindesk Crypto Screener 4H - เตือนโซน Overbought]</b>"
            final_message += "\n<i>คำแนะนำ: ราคาวิ่งแรงเกินไป ควรพิจารณาแบ่งขายทำกำไร</i>"
            for opt in sell_list:
                vol_note = "\n🔊 Volume: <b>ยืนยันแรงซื้อ (ระวังเพิ่ม)</b>" if opt["vol_confirmed"] else "\n🔇 Volume: ไม่ผิดปกติ"
                final_message += (
                    f"\n\n🪙 <b>เหรียญ: {opt['coin']}</b>"
                    f"\n📊 เทรนด์: {opt['trend']}"
                    f"\n🔥 Status: RSI Overbought (ซื้อมากเกินไป)"
                    f"\n💵 ราคาปัจจุบัน: ${opt['price']}"
                    f"\n📈 RSI (4H): {opt['rsi']} 🚨"
                    f"\n📈 เส้น EMA 50 / 200: ${opt['ema_50']} / ${opt['ema_200']}"
                    f"{vol_note}"
                    f"\n🔴 ช่วงราคาที่ควรทยอยขาย: <code>{opt['tp_zone']}</code>"
                    f"\n❌ จุดล็อกกำไรหลุดตรงนี้ต้องหนี (Exit): <code>{opt['exit']}</code>"
                )
    else:
        final_message += "\n\n=========================\n"
        final_message += "😴 <i>ตลาดนิ่งสนิท: ไม่มีสัญญาณซื้อ/ขายที่เข้าเงื่อนไขในรอบนี้</i>"

    return final_message


# ==========================================
# Main
# ==========================================
if __name__ == "__main__":
    logger.info("เริ่มต้น Crypto Screener (4H | EMA50/200 | RSI Wilder | Divergence v2)...")

    buy_list, sell_list, market_summary = scan_market()

    logger.info(f"สแกนเสร็จ → Buy signals: {len(buy_list)} | Sell signals: {len(sell_list)}")

    final_message = build_message(buy_list, sell_list, market_summary)

    send_telegram_message(final_message)

    logger.info("ส่งรายงานเสร็จสมบูรณ์")

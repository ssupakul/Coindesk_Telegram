import os
import requests
import pandas as pd
import numpy as np

# เปลี่ยนมาใช้ Environment Variables ของ Telegram
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")
CRYPTOCOMPARE_API_KEY = os.getenv("CRYPTOCOMPARE_API_KEY")

COINS = ["BTC", "ETH", "BNB", "SOL", "XRP", "ADA", "FLOKI", "SHIB", "EIGEN", "OP", "DOGE", "NEAR"]

def send_telegram_message(text_msg):
    """ ฟังก์ชันส่งข้อความไปยัง Telegram Bot """
    token = str(TELEGRAM_BOT_TOKEN).strip() if TELEGRAM_BOT_TOKEN else ""
    chat_id = str(TELEGRAM_CHAT_ID).strip() if TELEGRAM_CHAT_ID else ""
    
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    
    payload = {
        "chat_id": chat_id,
        "text": text_msg,
        "parse_mode": "HTML" 
    }
    
    try:
        response = requests.post(url, json=payload)
        if response.status_code == 200:
            print("Signal sent via Telegram Bot Successfully.")
        else:
            print(f"Failed to send Telegram message: {response.text}")
    except Exception as e:
        print(f"Error sending Telegram message: {e}")
        
def get_historical_data(coin):
    url = "https://min-api.cryptocompare.com/data/v2/histohour"
    params = {
        "fsym": coin,
        "tsym": "USD",
        "limit": 1000, 
        "api_key": CRYPTOCOMPARE_API_KEY
    }
    try:
        response = requests.get(url, params=params).json()
        if response.get("Response") == "Success":
            df = pd.DataFrame(response["Data"]["Data"])
            df['time'] = pd.to_datetime(df['time'], unit='s')
            df.set_index('time', inplace=True)
            
            df_4h = df.resample('4h').agg({
                'open': 'first',
                'high': 'max',
                'low': 'min',
                'close': 'last',
                'volumeto': 'sum'
            }).dropna()
            return df_4h
    except Exception as e:
        print(f"Error fetching data for {coin}: {e}")
    return None

def calculate_indicators(df):
    close = df['close']
    df['EMA_50'] = close.ewm(span=50, adjust=False).mean()
    df['EMA_200'] = close.ewm(span=200, adjust=False).mean()
    
    delta = close.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    
    avg_gain = gain.ewm(alpha=1/14, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1/14, adjust=False).mean()
    
    rs = np.where(avg_loss == 0, np.nan, avg_gain / avg_loss)
    df['RSI'] = np.where(avg_loss == 0, 100, 100 - (100 / (1 + rs)))
    return df

def check_bullish_divergence(df, lookback=15):
    if len(df) < lookback + 2:
        return False
        
    historical_low_price = df['low'].iloc[-lookback:-1].min()
    historical_low_rsi = df['RSI'].iloc[-lookback:-1].min()
    
    curr_price = df['low'].iloc[-1]
    curr_rsi = df['RSI'].iloc[-1]
    
    if curr_price < historical_low_price and curr_rsi > historical_low_rsi:
        if curr_rsi < 35 or historical_low_rsi < 35:
            return True
            
    return False

def format_price(coin, price):
    if price < 0.0001:
        return f"{price:.8f}"
    elif price < 0.001:
        return f"{price:.6f}"
    elif price < 1:
        return f"{price:.4f}"
    else:
        return f"{price:.2f}"

def scan_market():
    buy_signals = []
    sell_signals = []
    
    bullish_coins = 0
    bearish_coins = 0
    total_valid_coins = 0
    
    coin_trends_summary = []
    
    for coin in COINS:
        df = get_historical_data(coin)
        if df is None or len(df) < 200:
            continue
            
        df = calculate_indicators(df)
        current_row = df.iloc[-1]
        
        current_price = current_row['close']
        rsi = current_row['RSI']
        ema_50 = current_row['EMA_50']
        ema_200 = current_row['EMA_200']
        
        total_valid_coins += 1
        is_divergence = check_bullish_divergence(df)
        
        # มัดจำค่า RSI ปัดทศนิยมไว้แสดงผล
        rsi_rounded = round(rsi, 2)
        
        if current_price > ema_200:
            coin_trend = "🟢 ขาขึ้น (Above EMA 200)"
            bullish_coins += 1
            # ปรับปรุง: เพิ่มการแสดงผล RSI ต่อท้ายชื่อเหรียญในส่วนสรุปภาพรวม
            coin_trends_summary.append(f"• {coin}: 🟢 ขาขึ้น (RSI: {rsi_rounded})")
            
            signal_type = ""
            if current_price > (ema_50 * 0.98) and rsi <= 35:
                signal_type = "RSI Oversold + Pullback 📉"
            elif is_divergence:
                signal_type = "Bullish Divergence 📈"
                
            if signal_type:
                entry_min = format_price(coin, current_price * 0.97)
                entry_max = format_price(coin, current_price * 1.00)
                target_profit = format_price(coin, current_price * 1.12) 
                stop_loss = format_price(coin, ema_200 * 0.98)           
                
                buy_signals.append({
                    "coin": coin, 
                    "trend": coin_trend,
                    "price": format_price(coin, current_price), 
                    "rsi": rsi_rounded,
                    "type": signal_type, 
                    "ema_50": format_price(coin, ema_50), 
                    "ema_200": format_price(coin, ema_200), 
                    "entry": f"${entry_min} - ${entry_max}", 
                    "tp": f"${target_profit}", 
                    "sl": f"${stop_loss}"
                })
        else:
            coin_trend = "🔴 ขาลง (Below EMA 200)"
            bearish_coins += 1
            # ปรับปรุง: เพิ่มการแสดงผล RSI ต่อท้ายชื่อเหรียญในส่วนสรุปภาพรวมเช่นกัน
            coin_trends_summary.append(f"• {coin}: 🔴 ขาลง (RSI: {rsi_rounded})")
        
        if rsi >= 65:
            tp_range_min = format_price(coin, current_price * 1.00)
            tp_range_max = format_price(coin, current_price * 1.05)
            
            safety_exit_val = ema_50 if current_price > ema_50 else current_price * 0.95
            safety_exit = format_price(coin, safety_exit_val)
            
            sell_signals.append({
                "coin": coin, 
                "trend": coin_trend,
                "price": format_price(coin, current_price), 
                "rsi": rsi_rounded,
                "ema_50": format_price(coin, ema_50), 
                "ema_200": format_price(coin, ema_200),
                "tp_zone": f"${tp_range_min} - ${tp_range_max}", 
                "exit": f"${safety_exit}"
            })
            
    if total_valid_coins > 0:
        bullish_ratio = (bullish_coins / total_valid_coins) * 100
        
        summary_msg = f"📊 <b>[Market Trend Summary]</b>\n"
        summary_msg += f"📈 ขาขึ้น: {bullish_coins} เหรียญ | 📉 ขาลง: {bearish_coins} เหรียญ\n"
        
        if bullish_ratio >= 65:
            summary_msg += f"🔥 ภาพรวม: <b>🟢 ขาขึ้นชัดเจน (Strong Bullish)</b>\n<i>กลยุทธ์: เน้นดักซื้อเมื่อเกิดการย่อตัว (Buy on Dip)</i>"
        elif bullish_ratio >= 40:
            summary_msg += f"🔥 ภาพรวม: <b>🟡 ไซด์เวย์ / เลือกทาง (Sideways)</b>\n<i>กลยุทธ์: ตลาดก้ำกึ่ง ควรเลือกเทรดเฉพาะตัวที่มีสัญญาณชัดเจน</i>"
        else:
            summary_msg += f"🔥 ภาพรวม: <b>🔴 ขาลง / พักฐานแรง (Bearish)</b>\n<i>กลยุทธ์: ตลาดมีความเสี่ยงสูง เน้นถือเงินสดหรือลดขนาดไม้ลง</i>"
            
        summary_msg += "\n\n📋 <b>สรุปแนวโน้มรายเหรียญ:</b>\n"
        summary_msg += "\n".join(coin_trends_summary)
    else:
        summary_msg = "⚠️ ไม่สามารถดึงข้อมูลเหรียญเพื่อวิเคราะห์ภาพรวมได้"
            
    return buy_signals, sell_signals, summary_msg

if __name__ == "__main__":
    print("Starting Comprehensive Screener (With RSI in Summary)...")
    buy_list, sell_list, market_summary = scan_market()
    
    # สร้างข้อความพื้นฐาน (ส่วนหัว) ที่จะส่งเสมอ
    base_message = f"{market_summary}\n"
    base_message += "\n=========================\n"
    
    # 1. จัดการข้อความฝั่งสัญญาณซื้อ
    message_buy = base_message + "🎯 <b>[Coindesk Crypto Screener 4H - สัญญาณช้อนซื้อ]</b>"
    if buy_list:
        for opt in buy_list:
            message_buy += f"\n\n🪙 <b>เหรียญ: {opt['coin']}</b>"
            message_buy += f"\n📊 เทรนด์: {opt['trend']}"
            message_buy += f"\n🚨 รูปแบบ: {opt['type']}"
            message_buy += f"\n💵 ราคาปัจจุบัน: ${opt['price']}"
            message_buy += f"\n📉 RSI (4H): {opt['rsi']}"
            message_buy += f"\n📈 เส้น EMA 50 / 200: ${opt['ema_50']} / ${opt['ema_200']}"
            message_buy += f"\n🟢 ช่วงเข้าซื้อ: <code>{opt['entry']}</code>"
            message_buy += f"\n🔴 เป้าหมายขาย (TP): <code>{opt['tp']}</code>"
            message_buy += f"\n❌ จุดตัดขาดทุน (SL): <code>{opt['sl']}</code>"
    else:
        message_buy += "\n\n😴 <i>ไม่มีสัญญาณซื้อที่เข้าเงื่อนไขในรอบนี้</i>"
        
    # ส่งข้อความฝั่งซื้อเสมอ
    send_telegram_message(message_buy)
        
    # 2. จัดการข้อความฝั่งเตือนขาย (Overbought)
    message_sell = base_message + "⚠️ <b>[Coindesk Crypto Screener 4H - เตือนโซน Overbought]</b>"
    if sell_list:
        message_sell += "\n<i>คำแนะนำ: ราคาวิ่งแรงเกินไป ควรพิจารณาแบ่งขายทำกำไร</i>"
        for opt in sell_list:
            message_sell += f"\n\n🪙 <b>เหรียญ: {opt['coin']}</b>"
            message_sell += f"\n📊 เทรนด์: {opt['trend']}"
            message_sell += f"\n🔥 สถานะ: RSI Overbought (ซื้อมากเกินไป)"
            message_sell += f"\n💵 ราคาปัจจุบัน: ${opt['price']}"
            message_sell += f"\n📈 RSI (4H): {opt['rsi']} 🚨"
            message_sell += f"\n📈 เส้น EMA 50 / 200: ${opt['ema_50']} / ${opt['ema_200']}"
            message_sell += f"\n🔴 ช่วงราคาที่ควรทยอยขาย: <code>{opt['tp_zone']}</code>"
            message_sell += f"\n❌ จุดล็อกกำไรหลุดตรงนี้ต้องหนี (Exit): <code>{opt['exit']}</code>"
    else:
        message_sell += "\n\n😴 <i>ไม่มีเหรียญที่เข้าโซน Overbought ในรอบนี้</i>"
        
    # ส่งข้อความฝั่งเตือนขายเสมอ
    send_telegram_message(message_sell)

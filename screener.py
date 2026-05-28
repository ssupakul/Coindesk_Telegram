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
            print("Comprehensive Daily Report sent via Telegram Bot Successfully.")
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
        rsi_rounded = round(rsi, 2)
        
        signal_type = ""
        
        if current_price > ema_200:
            coin_trend = "🟢 ขาขึ้น (Above EMA 200)"
            bullish_coins += 1
            coin_trends_summary.append(f"• {coin}: 🟢 ขาขึ้น (RSI: {rsi_rounded})")
            
            if current_price > (ema_50 * 0.98) and rsi <= 35:
                signal_type = "RSI Oversold + Pullback 📉"
            elif is_divergence:
                signal_type = "Bullish Divergence 📈"
        else:
            coin_trend = "🔴 ขาลง (Below EMA 200)"
            bearish_coins += 1
            coin_trends_summary.append(f"• {coin}: 🔴 ขาลง (RSI: {rsi_rounded})")
            
            if rsi <= 35:
                signal_type = "RSI Oversold (ขาลง-เสี่ยงสูง) 📉"
            elif is_divergence:
                signal_type = "Bullish Divergence (สวนเทรนด์) 📈"
                
        if signal_type:
            entry_min = format_price(coin, current_price * 0.97)
            entry_max = format_price(coin, current_price * 1.00)
            target_profit = format_price(coin, current_price * 1.12) 
            stop_loss_val = ema_200 * 0.98 if current_price > ema_200 else current_price * 0.93
            stop_loss = format_price(coin, stop_loss_val)           
            
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
    print("Starting Comprehensive Screener (Single report template)...")
    buy_list, sell_list, market_summary = scan_market()
    
    # เริ่มสร้างข้อความหลักโดยใส่ข้อมูลสรุปภาพรวมและเทรนด์เหรียญ (ส่งแน่ๆ เป็นส่วนหัว)
    final_message = f"{market_summary}\n"
    
    # ตรวจสอบว่ามีสัญญาณซื้อหรือสัญญาณเตือนขายหรือไม่
    if buy_list or sell_list:
        final_message += "\n=========================\n"
        
        # ใส่รายละเอียดสัญญาณซื้อ (ถ้ามี)
        if buy_list:
            final_message += "🎯 <b>[Coindesk Crypto Screener 4H - สัญญาณช้อนซื้อ]</b>"
            for opt in buy_list:
                final_message += f"\n\n🪙 <b>เหรียญ: {opt['coin']}</b>"
                final_message += f"\n📊 เทรนด์: {opt['trend']}"
                final_message += f"\n🚨 รูปแบบ: {opt['type']}"
                final_message += f"\n💵 ราคาปัจจุบัน: ${opt['price']}"
                final_message += f"\n📉 RSI (4H): {opt['rsi']}"
                final_message += f"\n📈 เส้น EMA 50 / 200: ${opt['ema_50']} / ${opt['ema_200']}"
                final_message += f"\n🟢 ช่วงเข้าซื้อ: <code>{opt['entry']}</code>"
                final_message += f"\n🔴 เป้าหมายขาย (TP): <code>{opt['tp']}</code>"
                final_message += f"\n❌ จุดตัดขาดทุน (SL): <code>{opt['sl']}</code>"
            
            # คั่นระหว่างสัญญาณซื้อและสัญญาณขายเพื่อความสวยงาม (กรณีเกิดพร้อมกัน)
            if sell_list:
                final_message += "\n\n=========================\n"

        # ใส่รายละเอียดสัญญาณขาย Overbought (ถ้ามี)
        if sell_list:
            final_message += "⚠️ <b>[Coindesk Crypto Screener 4H - เตือนโซน Overbought]</b>"
            final_message += "\n<i>คำแนะนำ: ราคาวิ่งแรงเกินไป ควรพิจารณาแบ่งขายทำกำไร</i>"
            for opt in sell_list:
                final_message += f"\n\n🪙 <b>เหรียญ: {opt['coin']}</b>"
                final_message += f"\n📊 เทรนด์: {opt['trend']}"
                final_message += f"\n🔥 Status: RSI Overbought (ซื้อมากเกินไป)"
                final_message += f"\n💵 ราคาปัจจุบัน: ${opt['price']}"
                final_message += f"\n📈 RSI (4H): {opt['rsi']} 🚨"
                final_message += f"\n📈 เส้น EMA 50 / 200: ${opt['ema_50']} / ${opt['ema_200']}"
                final_message += f"\n🔴 ช่วงราคาที่ควรทยอยขาย: <code>{opt['tp_zone']}</code>"
                final_message += f"\n❌ จุดล็อกกำไรหลุดตรงนี้ต้องหนี (Exit): <code>{opt['exit']}</code>"
    else:
        # กรณีไม่มีสัญญาณใดๆ เลย จะต่อท้ายสั้นๆ เพื่อแจ้งว่าไม่มีสัญญาณ
        final_message += "\n\n=========================\n"
        final_message += "😴 <i>ตลาดนิ่งสนิท: ไม่มีสัญญาณซื้อ/ขายที่เข้าเงื่อนไขในรอบนี้</i>"

    # ส่งข้อความมัดรวมเสร็จสมบูรณ์ในข้อความเดียวจบ!
    send_telegram_message(final_message)

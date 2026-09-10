
from flask import Flask, jsonify, render_template
import requests, threading, time, os
from datetime import datetime
from collections import deque

app = Flask(__name__)

API = "https://data-api.binance.vision/api/v3/klines"
SYMBOL = "BTCUSDT"

state = {
    "updated": "-",
    "price": None,
    "conclusion": "CHỜ",
    "strength": "-",
    "confidence": 0,
    "confidence_text": "THẤP",
    "long_score": 0,
    "short_score": 0,
    "trend15": "-",
    "trend1h": "-",
    "ema34": None,
    "ema89": None,
    "ema200": None,
    "ema500": None,
    "ema_alignment": "-",
    "macd15": "-",
    "rsi15": None,
    "volume_ratio": None,
    "breakout": "KHÔNG",
    "breakout_level": None,
    "retest": "CHỜ BREAKOUT",
    "support": None,
    "resistance": None,
    "warnings": [],
    "alert_id": 0,
    "last_alert": None,
}
history = deque(maxlen=100)
active_breakout = None
lock = threading.Lock()
last_refresh_ts = 0.0
last_confirmed_signal = None
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "").strip()

def ema(values, period):
    if len(values) < period:
        return None
    k = 2 / (period + 1)
    e = values[0]
    for v in values[1:]:
        e = v * k + e * (1 - k)
    return e

def ema_series(values, period):
    if not values:
        return []
    k = 2 / (period + 1)
    out = [values[0]]
    for v in values[1:]:
        out.append(v * k + out[-1] * (1 - k))
    return out

def rsi(values, period=14):
    if len(values) < period + 1:
        return None
    gains, losses = [], []
    for i in range(1, len(values)):
        d = values[i] - values[i-1]
        gains.append(max(d, 0))
        losses.append(max(-d, 0))
    ag = sum(gains[-period:]) / period
    al = sum(losses[-period:]) / period
    if al == 0:
        return 100.0
    rs = ag / al
    return 100 - 100 / (1 + rs)

def macd(values):
    e12 = ema_series(values, 12)
    e26 = ema_series(values, 26)
    line = [a-b for a,b in zip(e12[-len(e26):], e26)]
    sig = ema_series(line, 9)
    hist = line[-1] - sig[-1]
    return "TĂNG" if hist > 0 else "GIẢM" if hist < 0 else "TRUNG TÍNH"

def atr(highs, lows, closes, period=14):
    trs = []
    for i in range(1, len(closes)):
        trs.append(max(
            highs[i] - lows[i],
            abs(highs[i] - closes[i-1]),
            abs(lows[i] - closes[i-1])
        ))
    return sum(trs[-period:]) / period

def fetch(interval):
    r = requests.get(API, params={"symbol": SYMBOL, "interval": interval, "limit": 650}, timeout=10)
    r.raise_for_status()
    rows = r.json()
    return {
        "high": [float(x[2]) for x in rows],
        "low": [float(x[3]) for x in rows],
        "close": [float(x[4]) for x in rows],
        "volume": [float(x[5]) for x in rows],
    }

def analyze_tf(d):
    c = d["close"]
    p = c[-1]
    e34 = ema(c[-200:], 34)
    e89 = ema(c[-300:], 89)
    e200 = ema(c[-500:], 200)
    e500 = ema(c[-650:], 500)

    if e34 > e89 > e200 > e500 and p > e34:
        trend = "TĂNG"
        align = "TĂNG CHUẨN"
    elif e34 < e89 < e200 < e500 and p < e34:
        trend = "GIẢM"
        align = "GIẢM CHUẨN"
    else:
        trend = "TRUNG TÍNH"
        align = "ĐAN XEN"

    rv = rsi(c, 14)
    macd_state = macd(c)
    av = atr(d["high"], d["low"], c, 14)
    support = min(d["low"][-40:])
    resistance = max(d["high"][-40:])

    avg_vol = sum(d["volume"][-21:-1]) / 20
    vr = d["volume"][-1] / avg_vol if avg_vol else 1.0

    closed_price = c[-2]
    closed_volume = d["volume"][-2]
    avg_closed_vol = sum(d["volume"][-22:-2]) / 20
    closed_vr = closed_volume / avg_closed_vol if avg_closed_vol else 1.0
    prev_high = max(d["high"][-22:-2])
    prev_low = min(d["low"][-22:-2])

    breakout = "KHÔNG"
    breakout_level = None
    breakout_side = None
    confirmed = False

    if closed_price > prev_high:
        breakout_level = prev_high
        breakout_side = "LONG"
        breakout = "BREAKOUT TĂNG ĐÃ ĐÓNG NẾN" if closed_vr >= 1.2 else "BREAKOUT TĂNG CHƯA XÁC NHẬN VOLUME"
        confirmed = closed_vr >= 1.2
    elif closed_price < prev_low:
        breakout_level = prev_low
        breakout_side = "SHORT"
        breakout = "BREAKOUT GIẢM ĐÃ ĐÓNG NẾN" if closed_vr >= 1.2 else "BREAKOUT GIẢM CHƯA XÁC NHẬN VOLUME"
        confirmed = closed_vr >= 1.2

    return {
        "price": p, "trend": trend, "alignment": align,
        "ema34": e34, "ema89": e89, "ema200": e200, "ema500": e500,
        "rsi": rv, "macd": macd_state, "atr": av, "support": support,
        "resistance": resistance, "volume_ratio": vr,
        "breakout": breakout, "breakout_level": breakout_level,
        "breakout_side": breakout_side, "breakout_confirmed": confirmed,
    }

def update_retest(a15):
    global active_breakout
    now = datetime.now()
    p = a15["price"]
    atrv = a15["atr"] or p * 0.005

    if a15["breakout_confirmed"] and a15["breakout_level"]:
        same = (
            active_breakout
            and active_breakout["side"] == a15["breakout_side"]
            and abs(active_breakout["level"] - a15["breakout_level"]) <= max(1.0, atrv*0.1)
        )
        if not same:
            active_breakout = {
                "side": a15["breakout_side"],
                "level": a15["breakout_level"],
                "time": now,
                "state": "CHỜ RETEST"
            }

    if not active_breakout:
        return "CHỜ BREAKOUT", None

    b = active_breakout
    level = b["level"]
    age = (now - b["time"]).total_seconds()/60
    tol = atrv * 0.35

    if age > 120:
        active_breakout = None
        return "RETEST HẾT HẠN", None

    if b["side"] == "LONG":
        near = level - tol <= p <= level + tol
        failed = p < level - tol*1.5
        confirmed = p > level + tol*0.25 and age >= 5
        if failed:
            active_breakout = None
            return "BREAKOUT CÓ NGUY CƠ GIẢ", None
        if near:
            b["state"] = "ĐANG RETEST"
            return "ĐANG RETEST", "LONG"
        if b["state"] == "ĐANG RETEST" and confirmed:
            active_breakout = None
            return "RETEST ĐẠT", "LONG"
    else:
        near = level - tol <= p <= level + tol
        failed = p > level + tol*1.5
        confirmed = p < level - tol*0.25 and age >= 5
        if failed:
            active_breakout = None
            return "BREAKOUT CÓ NGUY CƠ GIẢ", None
        if near:
            b["state"] = "ĐANG RETEST"
            return "ĐANG RETEST", "SHORT"
        if b["state"] == "ĐANG RETEST" and confirmed:
            active_breakout = None
            return "RETEST ĐẠT", "SHORT"

    return b["state"], b["side"]

def score(a15, a1h, retest_state, retest_side):
    long_score = 0
    short_score = 0
    warnings = []
    penalty = 0

    if a15["trend"] == "TĂNG":
        long_score += 20
    elif a15["trend"] == "GIẢM":
        short_score += 20

    if a1h["trend"] == "TĂNG":
        long_score += 30
    elif a1h["trend"] == "GIẢM":
        short_score += 30

    if a15["alignment"] == "TĂNG CHUẨN":
        long_score += 10
    elif a15["alignment"] == "GIẢM CHUẨN":
        short_score += 10
    else:
        warnings.append("EMA đan xen")
        penalty += 10

    if a15["macd"] == "TĂNG":
        long_score += 15
    elif a15["macd"] == "GIẢM":
        short_score += 15

    rv = a15["rsi"]
    if 52 <= rv <= 68:
        long_score += 15
    elif 32 <= rv <= 48:
        short_score += 15

    if a15["volume_ratio"] >= 1.15:
        if a15["trend"] == "TĂNG":
            long_score += 20
        elif a15["trend"] == "GIẢM":
            short_score += 20
    elif a15["volume_ratio"] < 0.75:
        warnings.append("Volume thấp")
        penalty += 15

    if retest_state == "RETEST ĐẠT" and retest_side == "LONG":
        long_score += 25
    elif retest_state == "RETEST ĐẠT" and retest_side == "SHORT":
        short_score += 25
    elif retest_state == "BREAKOUT CÓ NGUY CƠ GIẢ":
        warnings.append("Retest thất bại")
        penalty += 25

    long_score = min(100, long_score)
    short_score = min(100, short_score)
    best = max(long_score, short_score)
    confidence = max(0, min(100, best - penalty))

    conclusion = "CHỜ – CHƯA XÁC NHẬN"
    if retest_state == "RETEST ĐẠT" and retest_side == "LONG" and long_score >= 70 and confidence >= 55:
        conclusion = "TÍN HIỆU LONG ĐÃ XÁC NHẬN"
    elif retest_state == "RETEST ĐẠT" and retest_side == "SHORT" and short_score >= 70 and confidence >= 55:
        conclusion = "TÍN HIỆU SHORT ĐÃ XÁC NHẬN"
    elif retest_state in ("CHỜ RETEST", "ĐANG RETEST"):
        conclusion = retest_state
    elif retest_state == "BREAKOUT CÓ NGUY CƠ GIẢ":
        conclusion = "BREAKOUT CÓ NGUY CƠ GIẢ – ĐỨNG NGOÀI"

    strength = "CHỜ"
    if conclusion.startswith("TÍN HIỆU"):
        strength = "TÍN HIỆU MẠNH" if best >= 85 and confidence >= 75 else "TÍN HIỆU KHÁ"
    elif retest_state in ("CHỜ RETEST", "ĐANG RETEST"):
        strength = retest_state

    conf_text = "CAO" if confidence >= 75 else "TRUNG BÌNH" if confidence >= 55 else "THẤP"
    return long_score, short_score, confidence, conf_text, conclusion, strength, warnings


def send_telegram_alert(message):
    """Gửi cảnh báo Telegram nếu người dùng đã cấu hình biến môi trường."""
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        return False
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
        r = requests.post(
            url,
            data={"chat_id": TELEGRAM_CHAT_ID, "text": message},
            timeout=10
        )
        r.raise_for_status()
        return True
    except Exception:
        return False

def register_signal_alert(conclusion, price, confidence, long_score, short_score):
    """Chỉ tạo cảnh báo khi có tín hiệu xác nhận MỚI, không cảnh báo lặp lại."""
    global last_confirmed_signal

    confirmed = conclusion in (
        "TÍN HIỆU LONG ĐÃ XÁC NHẬN",
        "TÍN HIỆU SHORT ĐÃ XÁC NHẬN"
    )

    if not confirmed:
        last_confirmed_signal = None
        return

    side = "LONG" if "LONG" in conclusion else "SHORT"
    signal_key = side

    if last_confirmed_signal == signal_key:
        return

    last_confirmed_signal = signal_key
    now_text = datetime.now().strftime("%d/%m/%Y %H:%M:%S")
    message = (
        f"BTC/USDT: TÍN HIỆU KỸ THUẬT {side} ĐÃ XÁC NHẬN\n"
        f"Giá: {price:,.2f} USDT\n"
        f"Độ tin cậy: {confidence}/100\n"
        f"LONG: {long_score}/100 | SHORT: {short_score}/100\n"
        f"Thời gian: {now_text}\n"
        f"Chỉ dùng để phân tích/paper trading. Hãy kiểm tra thủ công trước mọi quyết định."
    )

    with lock:
        state["alert_id"] = int(state.get("alert_id", 0)) + 1
        state["last_alert"] = {
            "time": now_text,
            "side": side,
            "price": price,
            "confidence": confidence,
            "message": message
        }

    send_telegram_alert(message)

def refresh_once():
    global last_refresh_ts
    try:
        a15 = analyze_tf(fetch("15m"))
        a1h = analyze_tf(fetch("1h"))
        retest_state, retest_side = update_retest(a15)
        ls, ss, conf, conf_text, conclusion, strength, warnings = score(
            a15, a1h, retest_state, retest_side
        )

        register_signal_alert(
            conclusion,
            a15["price"],
            conf,
            ls,
            ss
        )

        now_text = datetime.now().strftime("%d/%m/%Y %H:%M:%S")
        with lock:
            state.update({
                "updated": now_text,
                "price": a15["price"],
                "conclusion": conclusion,
                "strength": strength,
                "confidence": conf,
                "confidence_text": conf_text,
                "long_score": ls,
                "short_score": ss,
                "trend15": a15["trend"],
                "trend1h": a1h["trend"],
                "ema34": a15["ema34"],
                "ema89": a15["ema89"],
                "ema200": a15["ema200"],
                "ema500": a15["ema500"],
                "ema_alignment": a15["alignment"],
                "macd15": a15["macd"],
                "rsi15": a15["rsi"],
                "volume_ratio": a15["volume_ratio"],
                "breakout": a15["breakout"],
                "breakout_level": a15["breakout_level"],
                "retest": retest_state,
                "support": a15["support"],
                "resistance": a15["resistance"],
                "warnings": warnings,
            })
            history.appendleft({
                "time": now_text,
                "conclusion": conclusion,
                "price": a15["price"],
                "long": ls,
                "short": ss,
                "confidence": conf
            })
            last_refresh_ts = time.time()
        return True
    except Exception as e:
        with lock:
            state["updated"] = f"Lỗi: {type(e).__name__}: {e}"
            last_refresh_ts = time.time()
        return False

def worker():
    while True:
        refresh_once()
        time.sleep(30)

@app.route("/")
def home():
    return render_template("index.html")

@app.route("/api/state")
def api_state():
    global last_refresh_ts
    # Trên Render/Gunicorn, không phụ thuộc hoàn toàn vào thread nền.
    # Nếu chưa có dữ liệu hoặc dữ liệu cũ hơn 30 giây, làm mới ngay khi có request.
    if last_refresh_ts == 0 or (time.time() - last_refresh_ts) >= 30:
        refresh_once()

    with lock:
        d = dict(state)
        d["history"] = list(history)[:20]
    return jsonify(d)

_started = False
def start_worker():
    global _started
    if not _started:
        _started = True
        threading.Thread(target=worker, daemon=True).start()

start_worker()

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=False)

# Bot BTC V5 Mobile

Web app responsive để mở trên điện thoại.

## Chạy thử trên máy tính
1. Mở CMD trong thư mục này
2. Chạy:
   py -m pip install -r requirements.txt
   py app.py
3. Mở http://127.0.0.1:5000

## Đưa lên Render
- Tạo Web Service mới.
- Build Command: pip install -r requirements.txt
- Start Command: gunicorn app:app
- Sau khi deploy, mở URL Render trên điện thoại.

## Có sẵn
- EMA34 / EMA89 / EMA200 / EMA500
- MACD / RSI / Volume
- Breakout bằng nến 15m đã đóng
- Retest
- LONG / SHORT 0–100
- Độ tin cậy
- Lịch sử phân tích gần đây
- Không đặt lệnh thật

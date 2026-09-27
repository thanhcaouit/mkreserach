# Research puzzle Google Play

Bot tìm puzzle miễn phí, có level, khoảng 10.000–500.000 lượt tải, loại publisher lớn và game đang nằm trang top/trending. Hai game mẫu là Cross Virus và Lemmings. Kết quả gửi Telegram. Bạn reply `1`–`5` vào đúng tin đó để lần sau tìm gần gu hơn.

Chạy trên GitHub Actions. Không server, không API trả phí. AI dùng Gemini Flash free, Groq free làm dự phòng. “Học” là viết lại `data/profile.json` từ điểm, không fine-tune model.

## Việc bạn cần làm một lần

1. Tạo bot Telegram với [@BotFather](https://t.me/BotFather), lấy token.
2. Nhắn cho bot một câu, rồi mở `https://api.telegram.org/bot<TOKEN>/getUpdates` để lấy `chat.id`.
3. Tạo key Gemini tại [Google AI Studio](https://aistudio.google.com/apikey). Không bật billing. Groq tại [console.groq.com](https://console.groq.com/) là dự phòng, có thể bỏ trống.
4. Trên repo GitHub: Settings → Secrets and variables → Actions, thêm:
   - `GEMINI_API_KEY`
   - `GROQ_API_KEY` (tuỳ chọn)
   - `TELEGRAM_BOT_TOKEN`
   - `TELEGRAM_CHAT_ID`

## Kiểm tra

Ba lớp. Hệ thống được coi là chạy đúng khi cả ba xong.

Pytest trên máy, không cần secret:

```bash
pip install -r requirements.txt
pytest
```

Mỗi push lên `main` chạy workflow Test. Xanh nghĩa là bộ lọc, chống trùng và cách đọc điểm ổn. Chưa gọi Play, AI, Telegram.

Smoke, bấm tay sau khi đã có secret: Actions → Research → Run workflow → chọn `smoke`. Job tải hai game mẫu, gọi Gemini (Groq nếu Gemini lỗi), gửi một tin `smoke ok`. Không ghi game vào catalog. Cron research chỉ nên để chạy sau khi Telegram nhận tin đó.

Research thật chạy mỗi tiếng từ 09:00 đến 21:00 giờ Việt Nam. Nếu Play, Gemini hoặc Groq trả 402, 403 hoặc 429, bot ghi thời điểm nghỉ tới 09:00 sáng hôm sau và các lần trong khoảng đó không gọi lại. Thu điểm vẫn chạy mỗi 6 giờ, nhưng không gọi AI khi đang nghỉ.

## Chấm điểm

Reply đúng tin game bằng số `1` đến `5`. Có thể thêm một câu, ví dụ `5 gameplay lạ`. `0`, `6` và tin không reply bị bỏ.

## Thêm publisher cần loại

Sửa `data/publishers.yaml`. Tên ngắn hơn 5 ký tự khớp cả từ. Tên dài hơn khớp chuỗi con trong tên developer trên Play.

## Dữ liệu

`data/catalog.json` nhớ mọi game đã gửi. Cùng package hoặc cùng tên đã chuẩn hóa sẽ không được gửi lại. `data/chart_blocklist.json` tích lũy app đang ở trang puzzle và trang game của Play. Play không có lịch sử chart một năm miễn phí; game từng hot thường đã vượt 500.000 lượt tải nên bị loại ở bước lượt tải.

# Hướng dẫn Build & Chạy dự án changedetection.io

Yêu cầu: Python **3.11** (xem `runtime.txt`).

## 1. Tạo virtual environment

```bash
python3 -m venv .venv
source .venv/bin/activate
```

## 2. Cài đặt thư viện

Cài thư viện chạy chính (bắt buộc):

```bash
pip install -r requirements.txt
```

Nếu cần phát triển/test (ruff, pre-commit, dennis...):

```bash
pip install -r requirements-dev.txt
```

> `requirements-dev.txt` tự động include lại `requirements.txt` nên chỉ cần chạy lệnh này khi dev là đủ.

## 3. Build dự án

Dự án dùng `setuptools`, build bằng:

```bash
python setup.py build
```

Lệnh này build package Python vào `build/lib/...` (có copy thêm `docs/api-spec.yaml` vào package). Có thể sẽ thấy vài **warning** về package data cho các thư mục `translations`/`translations_overlay` — không phải lỗi, bỏ qua được.

Kiểm tra build/import thành công:

```bash
python -c "import changedetectionio; print(changedetectionio.__version__)"
```

Nếu muốn cài package vào venv ở dạng editable (để code sửa là chạy luôn, không cần build lại):

```bash
pip install -e .
```

## 4. Chạy server

Chạy trực tiếp bằng script `changedetection.py` (không cần build trước, chỉ cần cài xong `requirements.txt`):

```bash
source .venv/bin/activate
python changedetection.py -d datastore -p 5000
```

Các tuỳ chọn hay dùng:

| Option | Ý nghĩa |
|---|---|
| `-d PATH` | Đường dẫn thư mục datastore (chứa dữ liệu watch, cấu hình) |
| `-p PORT` | Port lắng nghe (mặc định 5000) |
| `-h HOST` | Host lắng nghe (mặc định 0.0.0.0) |
| `-l LEVEL` | Log level: TRACE/DEBUG/INFO/SUCCESS/WARNING/ERROR/CRITICAL |
| `-C` | Tạo datastore nếu chưa tồn tại |
| `-b` | Batch mode: chạy queue rồi exit (dùng cho CI/cron) |

Xem đầy đủ: `python changedetection.py --help`

Sau khi chạy, mở trình duyệt vào **http://localhost:5000**.

Lần đầu chạy nếu `datastore/` trống, hệ thống sẽ tự tạo datastore mới kèm 2 watch mẫu (Hacker News, changelog của changedetection.io).

Để chạy nền (background) và log ra file:

```bash
python changedetection.py -d datastore -p 5000 > /tmp/changedetection.log 2>&1 &
```

Dừng server: `kill <PID>` (PID in ra khi chạy lệnh trên).

## 5. Chạy bằng Docker (thay thế, không cần cài Python/venv)

```bash
docker build -t changedetection.io .
docker run -d --name changedetection -p 127.0.0.1:5000:5000 -v changedetection-data:/datastore changedetection.io
```

Hoặc dùng sẵn `docker-compose.yml`:

```bash
docker compose up -d
```

## 6. Test

```bash
pip install -r requirements-dev.txt
pytest tests/test_notification.py   # ví dụ chạy 1 file test
pytest                                # chạy toàn bộ test
```

## 7. Monitor trang có JavaScript hoặc chặn truy cập

Trang Nojima ví dụ (`https://online.nojima.co.jp/commodity/1/4549980443576/`) hiện trả **HTTP 403 từ Akamai** cho IP của môi trường này. Đây là quyết định từ máy chủ trước khi nội dung sản phẩm được trả về; chỉ đổi User-Agent hoặc tăng thời gian chờ không giải quyết được. Không có cấu hình nào đảm bảo truy cập được mọi website hay tự giải mọi CAPTCHA.

URL `https://online.nojima.co.jp/app/catalog/detail/init/1/4948872417075?selectedSkuCode=4948872417075` cũng hợp lệ nhưng hiện trả HTTP 403 từ Akamai trên IP này. Nếu dùng màn hình **Add Watch**, chọn proxy trước khi bấm **Go** để bước xem trước dùng cùng proxy với watch. Nếu website trả trang chặn, xem trước sẽ báo lỗi và không lưu trang chặn làm snapshot đầu tiên.

1. Với trang cần JavaScript, chạy một Chrome/CDP service tương thích rồi đặt `PLAYWRIGHT_DRIVER_URL` trước khi khởi động `changedetection.py`. Khi chạy ứng dụng trên máy host và browser service mở cổng 3000, URL thường là `ws://127.0.0.1:3000`. Mẫu service `browser-sockpuppet-chrome` có trong `docker-compose.yml`. Trong **Edit Watch**, chọn content fetcher **Chrome/Playwright** (`html_webdriver`). Nếu dùng fetcher HTTP mặc định, JavaScript sẽ không chạy.
2. Trong **Settings → CAPTCHA & Proxies**, thêm HTTP/HTTPS proxy có xác thực nếu cần, rồi chọn proxy đó cho watch. Có thể khai báo nhiều proxy trong `datastore/proxies.json` theo mẫu dưới đây; ứng dụng đọc file này trực tiếp. Dùng endpoint **rotating proxy** của nhà cung cấp nếu cần IP thay đổi giữa các lần kiểm tra; một proxy URL tĩnh không tự đổi IP.

   ```json
   {
     "jp-rotating": {
       "label": "JP rotating gateway",
       "url": "http://username:password@proxy.example:10000"
     }
   }
   ```

   Nếu có nhiều endpoint proxy riêng, khai báo một pool bằng `urls` rồi chọn tên pool cho watch. Mỗi lần fetch (kể cả khi mở Browser Steps), ứng dụng lấy endpoint kế tiếp; sau khi khởi động lại vòng quay bắt đầu từ endpoint đầu tiên.

   ```json
   {
     "jp-pool": {
       "label": "JP proxy pool",
       "urls": [
         "http://user:pass@proxy-a.example:10000",
         "http://user:pass@proxy-b.example:10000"
       ]
     }
   }
   ```

3. Đặt chu kỳ kiểm tra phù hợp với giới hạn của website. Chỉ bật cảnh báo khi nội dung sản phẩm thực sự tải thành công. Nếu còn thấy `403`, `429` hoặc thông báo `Blocked by ... challenge page`, kiểm tra IP/proxy, quyền truy cập và phiên đăng nhập hợp lệ của website. CAPTCHA tương tác cần được hoàn thành qua một phiên browser được website chấp nhận; công cụ không tự giải CAPTCHA.

   Nếu liên tiếp bị 403/429 hoặc trang thử thách, scheduler tự giãn lần kiểm tra tiếp theo: 2, 4, 8 phút rồi tăng tối đa 1 giờ. Một lần tải thành công sẽ đặt lại chu kỳ bình thường. Có thể đổi mức bắt đầu bằng `ACCESS_BLOCK_RECHECK_BASE_SECONDS` và mức tối đa bằng `ACCESS_BLOCK_RECHECK_MAX_SECONDS`. **Check now** vẫn chạy ngay khi bạn chủ động bấm.

Bản cập nhật nhận diện các trang thử thách phổ biến của Akamai, Cloudflare, DataDome và PerimeterX khi chúng trả HTTP 200. Những trang đó được báo là lỗi và không ghi vào lịch sử thay đổi, tránh cảnh báo sai. Phản hồi 403 như ví dụ Nojima vẫn được lưu dưới dạng lỗi truy cập cùng ảnh lỗi nếu fetcher hỗ trợ.

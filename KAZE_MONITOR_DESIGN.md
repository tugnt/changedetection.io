> Lưu trữ lịch sử: phía Account Vault hiện chỉ nhận thông báo/HUD, không mua tự động từ webhook. Hợp đồng đang dùng nằm tại `../kaze-bt/DESIGN.md`.

# Kaze: thiết kế phần giám sát trong changedetection.io

## Phạm vi của repo này

changedetection.io tạo và chạy watch sản phẩm, xác định lần chuyển **hết hàng → có hàng** và phát tín hiệu. Repo này không chọn tài khoản, không mở giỏ, không giữ cookie/thẻ của người mua và không thao tác checkout. Hợp đồng sự kiện với Account Vault nằm trong `../kaze-bt/DESIGN.md`.

## Luồng giám sát

1. Tạo watch sản phẩm bằng API/UI hiện có, dùng `processor: restock_diff`, `in_stock_processing: in_stock_only`, `follow_price_changes: false`. Lần kiểm tra đầu chỉ tạo mốc trạng thái.
2. Mỗi site có cấu hình nhận diện tồn kho riêng khi heuristic chung không đủ tin cậy: selector/nội dung sản phẩm, nhãn có hàng/hết hàng, giá và biến thể nếu cần. Lưu cấu hình cùng watch hoặc tag; không đưa logic mua vào processor.
3. Chỉ phát sự kiện khi trang tải thành công và trạng thái mới xác định rõ là `in_stock`. HTTP 403/429, challenge, selector hỏng hoặc trạng thái `unknown` là lỗi giám sát, không phải tín hiệu mua. Các cơ chế chặn/challenge hiện có trong codebase tiếp tục được dùng.
4. Ghi một `transition_id` ổn định cho lần chuyển trạng thái, ví dụ `<watch_uuid>:<history_timestamp>`. Retry cùng lần chuyển phải giữ nguyên ID. Payload chỉ gồm định danh watch và thời điểm; Account Vault đọc lại watch qua API trước khi mua.

## Giao sự kiện

- Khi chạy native trên cùng máy Mac: POST tới listener của Account Vault trên `127.0.0.1:7788/alert`, kèm Bearer token. Có thể dùng hạ tầng notification HTTP hiện có; nếu template không tạo được `transition_id` ổn định thì thêm notifier nhỏ cho loại sự kiện này.
- Khi chạy Docker: endpoint loopback của Account Vault không truy cập được từ container. Duy trì API watch/history để Account Vault chủ động poll và dựng cùng `transition_id`; không mở listener Vault ra LAN chỉ để nhận webhook.
- Nếu gửi thất bại, giữ lỗi/thống kê gửi. Cơ chế poll và đối soát history ở Vault xử lý sự kiện bị lỡ; gửi trùng được phép.

## API cần giữ ổn định cho Account Vault

`GET /api/v1/watch/{uuid}` phải trả URL, processor, `restock.in_stock`, `restock_check_state`, lỗi fetch gần nhất và thời điểm kiểm tra. History phải cho phép xác định lần chuyển trạng thái gần nhất. API key chỉ được dùng để đọc từ Vault; watch được quản lý bằng API hiện có.

## Tiêu chí hoàn thành của agent monitor

- Fixture chuyển `out_of_stock → in_stock` tạo đúng một `transition_id`, retry không đổi ID.
- Fixture giá đổi khi vẫn hết hàng, challenge, 403/429 và trạng thái `unknown` không tạo tín hiệu mua.
- Có thể đọc lại watch/history sau khi restart để Account Vault khôi phục một lần chuyển có hàng đã bị lỡ.
- Ví dụ cấu hình một watch và cách kiểm tra webhook/API được ghi ngay trong repo này, không chứa token thật.

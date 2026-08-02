# Video Red Alert Cooldown Design

## Mục tiêu

Giảm số lần gọi Qwen trên đường xử lý video async khi một camera đã được xác
nhận ở mức cảnh báo đỏ. Sau một kết quả đỏ, hệ thống giữ một alert episode đang
hoạt động và không gọi lại Qwen cho camera đó trong 60 giây theo thời gian nội
dung video. Cửa sổ đầu tiên đủ điều kiện sau thời hạn này được dùng để xác minh
lại trạng thái.

Thiết kế này là bước MVP cho video demo nhiều camera. Nó giữ queue và worker
hiện tại, đồng thời tạo contract trạng thái có thể tái sử dụng khi Motion,
Detection và VLM được tách thành các queue riêng ở giai đoạn sau.

## Phạm vi

Thay đổi chỉ áp dụng cho pipeline video async, aggregate analysis và báo cáo
benchmark theo video. Image endpoint và video endpoint đồng bộ không thay đổi.

MVP bao gồm:

- trạng thái cảnh báo độc lập theo camera trong bộ nhớ;
- một alert episode cho mỗi chuỗi cảnh báo đang hoạt động;
- suppress Qwen 60 giây sau khi Qwen xác nhận đỏ;
- Motion, Detection, routing và keyframe selection vẫn chạy trong cooldown;
- xác minh lại bằng một cửa sổ video đủ mới sau khi cooldown hết hạn;
- phân biệt kết quả cửa sổ với mức cảnh báo đang được kế thừa;
- retry có kiểm soát khi lần xác minh lại bị lỗi;
- metric cooldown trong JSON benchmark.

MVP không bao gồm per-camera queue, round-robin scheduler mới, queue riêng cho
Motion/Detection và VLM, Redis/database persistence, nhiều VLM worker hoặc
cooldown riêng theo event type.

## Trạng thái runtime

Cooldown là thuộc tính của một cảnh báo đang hoạt động, không phải trạng thái
ngừng cảnh báo. Camera dùng các trạng thái sau:

```text
NORMAL
  | Qwen xác nhận HIGH
  v
ALERT_ACTIVE
  | trước next_recheck_event_seconds: suppress Qwen
  | đến hạn: chọn một cửa sổ đủ mới để xác minh lại
  v
REVERIFY_PENDING
  | HIGH   -> ALERT_ACTIVE, gia hạn 60 giây
  | MEDIUM -> ORANGE_WATCH, kiểm tra lại sau 15 giây
  | LOW    -> RESOLVED -> NORMAL
  | lỗi    -> ALERT_ACTIVE_UNVERIFIED, retry sau 15 giây
  v
ORANGE_WATCH
  | HIGH   -> ALERT_ACTIVE
  | MEDIUM -> ORANGE_WATCH, gia hạn 15 giây
  | LOW    -> RESOLVED -> NORMAL
```

Trạng thái tối thiểu cần lưu:

```json
{
  "camera_id": "camera_01",
  "state": "alert_active",
  "current_level": "high",
  "active_alert_id": "alert_123",
  "event_type": "traffic_accident",
  "episode_started_event_seconds": 10.0,
  "first_confirmed_processing_at": 1785668447.0,
  "last_successful_verification_event_seconds": 15.0,
  "next_recheck_event_seconds": 75.0,
  "retry_due_processing_at": null,
  "verification_status": "verified",
  "vlm_inflight": false,
  "retry_count": 0,
  "state_version": 1
}
```

State store có interface riêng với implementation in-memory trong MVP. Khóa
logic theo `camera_id` và `vlm_inflight` bảo đảm mỗi camera chỉ có tối đa một
lần xác minh Qwen đang chạy. `state_version` giữ contract cho optimistic
locking khi chuyển sang persistence hoặc nhiều worker.

Mỗi `camera_id` chỉ được có một video analysis đang hoạt động. Demo nhiều video
phải gán một `camera_id` riêng cho từng video. API từ chối một upload video mới
cho cùng camera khi analysis trước vẫn ở trạng thái reading/queued; quy tắc này
tránh việc hai timeline tương đối cùng ghi vào một camera state.

## Thời gian sự kiện và thời gian xử lý

Mỗi cửa sổ tiếp tục có `start_seconds` và `end_seconds` theo nội dung video.
Cooldown và lựa chọn cửa sổ recheck dùng thời gian sự kiện:

```text
next_recheck_event_seconds = confirmed_window.end_seconds + 60
```

Timeout, retry và metric dùng thời gian monotonic của quá trình xử lý. Không
dùng thời điểm Qwen trả kết quả để cộng thêm 60 giây, vì khi queue có backlog
thì sự kiện trong video đã xảy ra trước đó.

Một cửa sổ chỉ đủ điều kiện recheck khi:

```text
window.start_seconds >= next_recheck_event_seconds
```

Với video demo đang được phát theo source time, producer cung cấp cửa sổ mới
theo nhịp 5 giây. Khi tích hợp live camera thật, contract được mở rộng bằng
`capture_started_at` và `capture_ended_at`; thuật toán trạng thái không đổi.

## Luồng xử lý cửa sổ

```text
RawVideoWindow
  -> Motion
  -> YOLO / Fire detector
  -> candidate routing
  -> đọc camera alert state
       NORMAL
         -> áp dụng VLMCallPolicy hiện tại
       ALERT_ACTIVE và chưa đến hạn
         -> suppress Qwen, kế thừa alert đang hoạt động
       ALERT_ACTIVE và đã đến hạn
         -> chỉ cửa sổ đủ điều kiện mới force Qwen recheck
       vlm_inflight=true
         -> không tạo thêm request Qwen cho cùng camera
  -> ProcessedVideoWindow
  -> AnalysisStore / AlertStore / benchmark JSON
```

Kiểm tra cooldown phải diễn ra ngay trước quyết định gọi Qwen trong worker.
Nhờ vậy, task đã nằm trong queue trước khi một cửa sổ khác trả đỏ vẫn được
suppress đúng khi tới lượt xử lý. MVP không xóa task khỏi `PriorityQueue`.

Trong cooldown, Motion và Detection vẫn chạy với cấu hình hiện tại để giữ dữ
liệu quan sát và không thay đổi hành vi detector. Việc giảm tần suất YOLO nằm
ngoài phạm vi MVP.

## Kết quả đỏ và alert episode

Khi Qwen trả một quyết định hợp lệ có mức `HIGH`:

1. Nếu camera chưa có episode, tạo một alert episode mới.
2. Nếu camera đã có episode, cập nhật episode hiện tại; không tạo alert mới.
3. Lưu ID cửa sổ xác nhận, event type và thời gian xác nhận.
4. Đặt `next_recheck_event_seconds` bằng cuối cửa sổ cộng 60 giây.
5. Đánh dấu các cửa sổ tiếp theo là kế thừa cho đến lần recheck.

Phiên bản đầu dùng cooldown toàn camera. Một loại sự kiện mới không được phá
cooldown. Đây là giới hạn nghiệp vụ đã được chấp nhận vì ứng dụng chỉ cảnh báo
an ninh tổng quát và người vận hành sẽ mở video để kiểm tra trực tiếp.

## Cửa sổ bị suppress

Một cửa sổ bị suppress không được mô tả như vừa được Qwen xác nhận đỏ. Kết quả
phải tách quan sát của cửa sổ và trạng thái vận hành của camera:

```json
{
  "window_observation": {
    "detected_level": null,
    "verification_status": "suppressed",
    "verified_by_vlm": false
  },
  "effective_camera_state": {
    "level": "high",
    "source": "inherited_active_alert",
    "alert_id": "alert_123"
  },
  "vlm": {
    "called": false,
    "status": "suppressed",
    "reason": "active_alert_cooldown"
  }
}
```

`qwen_ms` bằng `0`. Cửa sổ vẫn hoàn thành và xuất hiện trong API/benchmark.
Frontend có thể hiển thị cảnh báo đỏ đang hoạt động cùng thông báo rằng cửa sổ
hiện tại chưa được AI xác minh lại.

## Xác minh lại

Cửa sổ đầu tiên đạt thời điểm recheck được force gọi Qwen, không phụ thuộc gate
candidate thông thường. Kết quả được xử lý như sau:

| Kết quả | Chuyển trạng thái |
|---|---|
| Đỏ | Giữ episode, gia hạn recheck thêm 60 giây theo cuối cửa sổ |
| Cam | Giữ episode ở `ORANGE_WATCH`, recheck sau 15 giây |
| Xanh | Resolve episode và chuyển camera về `NORMAL` |
| Timeout/JSON lỗi | Giữ mức cảnh báo hiện tại, đánh dấu unverified và retry sau 15 giây |

Một lần timeout không cập nhật `last_successful_verification`. MVP retry sau 15
giây; nếu có ba lỗi liên tiếp thì khoảng retry tăng lên 30 giây, sau đó giới
hạn tối đa 60 giây để tránh tạo thêm tải khi Qwen gặp sự cố toàn hệ thống.

## Tích hợp với store hiện tại

`AnalysisStore` vẫn sở hữu vòng đời và kết quả từng video. Một alert-state
store mới sở hữu trạng thái runtime theo camera. `AlertStore` sở hữu alert
episode đã tạo. Các store không đọc trực tiếp dữ liệu nội bộ của nhau; worker
phối hợp chúng qua các method rõ ràng.

Khi analysis kết thúc mà episode vẫn active, kết quả analysis và alert episode
tiếp tục giữ cảnh báo cuối cùng. Một upload mới cho cùng camera được phép sau
khi analysis cũ đã hoàn tất và bắt đầu một stream session mới; cooldown của file
cũ không áp dụng lên timeline tương đối của file mới. Live camera về sau dùng
một session liên tục nên cooldown vẫn tồn tại qua mọi cửa sổ của stream đó.

Các schema và API cũ không bị xóa field. Metadata cooldown là additive. Nếu
process khởi động lại, trạng thái cooldown in-memory bị mất; đây là giới hạn
được chấp nhận cho demo và phải được ghi log rõ ràng.

## Benchmark và observability

Báo cáo theo video bổ sung:

- `vlm_suppressed_by_cooldown`;
- `cooldown_suppression_rate`;
- `red_episodes_created`;
- `red_rechecks`;
- `red_cooldown_extensions`;
- `recheck_failures`;
- reason `active_alert_cooldown` cho từng cửa sổ bị suppress.

Benchmark demo cần ít nhất một video dài hơn 65 giây có sự kiện đỏ sớm để xác
nhận các cửa sổ trong phút tiếp theo không gọi Qwen và cửa sổ sau hạn được gọi
lại. Kết quả phải báo riêng mức cảnh báo được Qwen xác minh với mức đỏ kế thừa.

## Xử lý lỗi

- Lỗi Motion/Detection giữ cách xử lý degraded hiện tại và không tự tạo đỏ.
- Lỗi Qwen ở lần xác minh đầu không mở alert episode.
- Lỗi Qwen khi recheck không đóng episode đang hoạt động.
- Cửa sổ không đủ frame không được dùng để resolve cảnh báo; worker giữ trạng
  thái hiện tại và chờ một cửa sổ đủ điều kiện tiếp theo.
- Một lỗi cập nhật state không được tạo alert episode thứ hai; update phải được
  thực hiện dưới khóa theo camera.

## Tiêu chí hoàn thành

Hoàn thành MVP khi:

1. Unit test chứng minh kết quả đỏ tạo đúng một episode và thời hạn 60 giây.
2. Các cửa sổ trong cooldown chạy Motion/Detection nhưng không gọi fake Qwen.
3. Cửa sổ suppressed có metadata kế thừa và `qwen_ms = 0`.
4. Cửa sổ đủ điều kiện recheck gọi Qwen đúng một lần.
5. Đỏ gia hạn 60 giây, cam chuyển sang watch, xanh resolve và lỗi giữ episode.
6. Hai camera có state độc lập và cảnh báo của camera A không suppress camera B.
7. Backlog task được suppress dựa trên state mới nhất tại thời điểm xử lý.
8. API và benchmark JSON vẫn chứa mọi cửa sổ theo thứ tự thời gian.
9. Toàn bộ regression test hiện có vẫn pass.
10. Benchmark thực tế báo số Qwen call đã tiết kiệm; không tuyên bố cải thiện
    latency nếu chưa có run với API và Ollama thật.

## Hướng mở rộng

Khi MVP ổn định, có thể tách producer thành per-camera queues, dùng scheduler
round-robin theo priority, rồi tách Motion/Detection queue khỏi VLM queue. State
store và metadata ở trên tiếp tục được dùng; VLM worker chỉ cần kiểm tra lại
camera state ngay trước network call. Redis/database và atomic compare-and-set
được thêm khi triển khai nhiều process hoặc nhiều GPU worker.

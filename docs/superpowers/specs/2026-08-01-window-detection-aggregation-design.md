# Window Detection Aggregation Design

## Mục tiêu

Ngăn router phóng đại số người và phương tiện khi cùng một đối tượng được YOLO
phát hiện trên nhiều frame trong một cửa sổ 5 giây. Giữ bbox làm bằng chứng nội
bộ, nhưng không ghi danh sách bbox thô vào JSON benchmark theo video.

## Phạm vi

Thay đổi áp dụng cho pipeline video async và báo cáo benchmark của đường này.
Không thêm ByteTrack, không gán `track_id`, không thay model YOLO/Qwen và không
tuyên bố số lượng unique object xuyên suốt window.

## Aggregation theo frame

`VideoWindowObservation.detections` vẫn giữ detection thô để tương thích và phục
vụ xử lý nội bộ. Router không còn tính `person_count` và `vehicle_count` bằng độ
dài danh sách detection cộng dồn.

Trước khi route, processor tạo thống kê window từ `observation.detections` của
từng frame:

```json
{
  "person_peak_count": 4,
  "vehicle_peak_count": 2,
  "person_detection_frames": 7,
  "vehicle_detection_frames": 8,
  "proximity_frame_count": 3,
  "sampled_frame_count": 25
}
```

- `person_peak_count`: số person lớn nhất trong cùng một frame.
- `vehicle_peak_count`: số vehicle lớn nhất trong cùng một frame.
- `person_detection_frames`: số frame có ít nhất một person.
- `vehicle_detection_frames`: số frame có ít nhất một vehicle.
- `proximity_frame_count`: số frame có ít nhất một cặp person–vehicle gần nhau.
- `sampled_frame_count`: số frame quan sát trong window.

Proximity chỉ được tính giữa detection cùng frame bằng phép đo bbox hiện có.
Detection của hai frame khác nhau không được ghép thành một cặp proximity.

## Router

Router nhận thống kê frame-level đã tính sẵn. Evidence của candidate dùng tên
trường mới và không còn dùng tổng số detection:

```json
{
  "person_peak_count": 4,
  "vehicle_peak_count": 2,
  "proximity_frame_count": 3,
  "motion_peak": 0.34
}
```

Trong đợt này, điều kiện bảo thủ vẫn được giữ: nếu có person và vehicle trong
window thì có thể tạo `possible_person_vehicle_interaction`; priority là medium
khi `proximity_frame_count > 0`, nếu không là low. Việc gate thêm candidate bình
thường sang không gọi Qwen thuộc đợt tối ưu sau, dựa trên benchmark mới.

`multi_person_high_motion` dùng `person_peak_count >= 2`, không dùng số detection
cộng dồn. `person_only_activity` và `vehicle_only_activity` cũng dựa trên peak
count.

## JSON benchmark

Mỗi window bỏ field `detections` chứa bbox thô và thay bằng
`detection_summary`. Summary được tính từ detection thô trong API payload:

```json
{
  "detection_summary": {
    "person": {
      "detection_count": 11,
      "max_confidence": 0.91
    },
    "car": {
      "detection_count": 7,
      "max_confidence": 0.92
    }
  }
}
```

`detection_count` ở output chỉ mô tả số detection thô được ghi nhận, không mang
nghĩa unique object. Số lượng phục vụ router lấy từ evidence frame-level nói
trên. Không xuất bbox, `track_id`, source hoặc backend trong benchmark JSON.

API hiện tại vẫn có thể giữ `VideoWindowResult.detections` để không phá tương
thích FE hoặc consumer khác. Chỉ serializer của benchmark bỏ dữ liệu bbox.

## Lỗi và tương thích

- Window không có observation trả mọi counter bằng 0.
- Detection label không thuộc person/vehicle vẫn xuất hiện trong
  `detection_summary`, nhưng không ảnh hưởng router.
- Confidence thiếu được xem là 0; schema hiện tại đã giới hạn confidence hợp lệ.
- Field evidence cũ `person_count` và `vehicle_count` được thay bằng tên mới
  trong candidate mới; benchmark cũ vẫn đọc được vì serializer không phụ thuộc
  hai field đó.

## Kiểm thử và tiêu chí hoàn thành

- Một person lặp trên nhiều frame cho `person_peak_count = 1`.
- Hai person trong cùng một frame cho peak count bằng 2.
- Proximity ở các frame khác nhau không được tính; cùng frame mới được tính.
- Router dùng peak count và proximity frame count trong evidence.
- Benchmark JSON không chứa key `bbox` và có summary label/count/confidence.
- Full regression pass và benchmark CLI vẫn tạo một JSON cho mỗi video.

Đợt này cải thiện tính đúng của router và kích thước output; nó không tự đảm bảo
Qwen xuống dưới 5 giây vì candidate policy và kích thước ảnh chưa thay đổi.

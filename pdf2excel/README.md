# PDF → Excel (chạy tại máy, không gửi file lên mạng)

Web app nội bộ chuyển **bảng trong PDF** thành **Excel (.xlsx)**. Hỗ trợ cả PDF
có text layer và PDF scan (OCR). Mỗi bảng trong PDF trở thành một sheet riêng,
giữ ô gộp (merged cells) và xuống dòng trong ô.

> Toàn bộ xử lý diễn ra trên máy này. Server chỉ lắng nghe `127.0.0.1`, không
> có dữ liệu nào rời khỏi máy.

---

## 1. Yêu cầu

| Thành phần | Ghi chú |
|---|---|
| Windows 10/11 | Đã kiểm thử trên máy này |
| Python 3.10+ | Đã kiểm thử với Python 3.14 |
| RAM | ~1 GB khi chạy; OCR thêm ~600 MB |

Không cần Node.js, không cần cài Excel.

## 2. Cài đặt

```bat
cd D:\opencode\pdf2excel
python -m pip install -r requirements.txt
```

`onnxruntime` là tuỳ chọn nhưng **nên cài**: OCR nhanh hơn khoảng 1,5–2 lần so
với PyTorch. Nếu không cài được, app tự động dùng PyTorch.

Chế độ "sâu" (tuỳ chọn, rất nặng — chỉ dùng khi bảng có bố cục lạ):

```bat
python -m pip install docling
```

## 3. Chạy

Nháy đúp **`run.bat`** — tự mở trình duyệt tại <http://127.0.0.1:8765>.

Hoặc chạy tay:

```bat
cd D:\opencode\pdf2excel
set PYTHONIOENCODING=utf-8
python -m uvicorn app.main:app --host 127.0.0.1 --port 8765
```

Dừng: nhấn `Ctrl+C` trong cửa sổ lệnh.

## 4. Sử dụng

1. Kéo–thả (hoặc bấm **chọn file**) một hay nhiều PDF. Tối đa 20 file,
   100 MB/file.
2. App tự phân tích rồi **tự chuyển đổi ngay** — không cần bấm từng file.
   Thanh tiến độ hiện `Trang 3/6: OCR ảnh · 24s (còn ~49s)` và nhích mỗi giây,
   kể cả khi một trang scan cần 5–10 giây. Có nút **Dừng** nếu muốn huỷ.
3. Xem trước bảng ngay trên web khi xong:
   * bấm vào ô để **sửa trực tiếp** (nội dung sửa được ghi vào Excel);
   * ô **tô vàng** = OCR có độ tin cậy < 0,75, nên kiểm tra lại;
   * **Xem trang** để đối chiếu với ảnh trang gốc;
   * bỏ tick nếu không muốn xuất bảng đó.
4. Bấm **Tải Excel** — file được tải thẳng về thư mục Downloads, trang không bị
   đổi. Muốn tải lại lần nữa thì bấm **Tải lại**. Nhiều file đã xong →
   **Tải tất cả (.zip)**.
5. Muốn đổi cách đọc (text/scan) hoặc chạy lại: mở **Tuỳ chọn nâng cao /
   chuyển đổi lại** ở cuối thẻ.
6. Nếu có file đang chờ/chạy lỗi/đã dừng, nút **Chuyển đổi tất cả (N)** ở góc
   trên bên phải sẽ chạy lại **tất cả** các file đó bằng một lần bấm.

## 5. File Excel nhận được

| Sheet | Nội dung |
|---|---|
| `T{trang}-B{thứ tự}` | Mỗi bảng một sheet, giữ ô gộp, viền mảnh, dòng đầu in đậm + nền xám |

File chỉ có đúng các sheet bảng — không còn sheet phụ `_Thông tin` /
`_Văn bản gốc` như bản trước. Nếu không có bảng nào được chọn thì file vẫn có
một sheet `Kết quả` trống kèm ghi chú, để Excel mở được bình thường.

Ô OCR độ tin cậy thấp được tô vàng và có ghi chú (comment) kèm điểm tin cậy.
Nội dung bắt đầu bằng `=` được lưu dạng **chuỗi**, không bị Excel hiểu thành
công thức.

Chữ dài trong ô **không** bị chèn xuống dòng cứng (Alt+Enter) theo đúng chỗ
ngắt của bản PDF nữa. App nối các dòng đó lại thành một đoạn (chèn khoảng trắng
khi cần, ví dụ `R-TNCRB057` + `08406C.02` → `R-TNCRB057 08406C.02`), rồi để
Excel tự xuống dòng theo độ rộng cột. Chiều cao dòng được ước lượng sẵn để ô
gộp (Excel không tự giãn ô gộp) vẫn hiện đủ chữ.

## 6. Cách hoạt động (tóm tắt)

```
PDF ──► render trang (200 DPI) ──► nhị phân hoá
                                 │
        text layer (pdfium) ─────┤ xoá đúng hộp bao từng ký tự
        hoặc OCR (RapidOCR) ─────┘
                                 ▼
        tách nét kẻ dài + VÁ KHE HỞ 1–2 px trên nét kẻ (bản scan hay bị đứt)
                                 ▼
        connected components của vùng TRẮNG = ruột từng ô
                                 ▼
        loại vùng "rò rỉ" (một vùng trắng nuốt nhiều ô thật) và mảnh vụn logo
                                 ▼
        gom toạ độ biên → lưới hàng/cột → ô gộp (span) tự nhiên
                                 ▼
        lấp lại ô bị bỏ sót: ô có chữ + đủ 4 cạnh là nét kẻ thật
                                 ▼
        ghép chữ vào ô (giữ thứ tự dòng, khoảng trắng cho tiếng Anh,
        không chèn khoảng trắng giữa chữ Hán) → Excel
```

Hai lỗi thường gặp trên bản scan và cách xử lý:

* **Nhiều ô bị gộp thành một ô khổng lồ** — nét kẻ trong bản scan bị đứt 1–2 px
  nên vùng trắng "chảy" qua khe và nối nhiều ô thành một. App vá khe hở theo
  đúng phương của nét kẻ (ngang/dọc), rồi loại vùng lớn nếu nó chứa ≥2 ô thật
  (dấu hiệu rò rỉ). Nhờ vậy ô thật không bị nuốt.
* **Khe 1 px ở góc chữ T** — đường kẻ ngang của PDF hay dừng cách đường dọc
  1 px, để lại một lỗ trắng đúng 1 px ngay góc ô. Lỗ này không nằm trong vệt
  ngang hay vệt dọc nên phép vá khe ở trên không lấp được, và hai ô trên/dưới
  dính làm một (ô `60s` nuốt ô `300s` trên WI-TSG trang 3). Nay mỗi đường kẻ
  được nới thêm 1 px theo đúng phương của nó, đủ để góc chữ T khép kín mà
  không làm dày nét hay đổi bố cục.
* **Dấu `/` bị đọc thành `1`** — PP-OCR thu nhỏ ảnh ô xuống 48 px nên nét chéo
  mảnh thành nét đứng; tăng DPI không sửa được (đã thử 300 DPI và phóng 3×).
  App phân biệt bằng hình học: `/` là **một nét thẳng chéo** (R² > 0,95, độ dốc
  0,10–0,60), còn `1` có gạch đầu + chân nên không thẳng. Chỉ áp dụng cho ô chỉ
  có một ký tự `1/l/I/|`, nên `1006`, `1,2`, `0.19–0.21` không bị đổi.
* **Hai ô dính nhau / chữ bị đảo thứ tự** (PDF có text layer) — hộp bao ký tự
  của pdfium chạm sát đường kẻ, nên bước xoá chữ "gặm" mất 1–2 px đường kẻ và
  vùng trắng rò sang ô bên cạnh (ví dụ cột *特性值* nuốt luôn cột *检验器具*).
  Nay hộp xoá được **thụt vào 1 px** thay vì nới ra. Cùng lúc, số mũ (`10⁸`) nằm
  cao hơn dòng nên trước đây bị tách thành dòng riêng, làm chữ đảo thứ tự
  (`隙)：1.51×10⁸` → `隙1 51×108` + `)：.`); nay các ký tự được gom dòng theo
  **độ chồng lấn theo trục dọc**, nên số mũ nằm đúng dòng của nó.
* **`1,2` bị tách thành `1 2` + `,`** — dấu phẩy là glyph thấp, gần như không
  chồng lấn theo trục dọc với chữ số cùng dòng, nên bị coi là "dòng khác". Nay
  ngoài độ chồng lấn còn so **đường chân** (đáy hộp bao) với dung sai
  ~0,35 chiều cao chữ, và không chèn khoảng trắng sát dấu câu. Ô chỉ có số còn
  được chuẩn hoá dấu phẩy: PDF lúc dùng `,` lúc dùng `，` → luôn ra `1,2`.

Vì sao không dùng thư viện nhận dạng bảng phổ biến?

* `camelot`/`tabula`: giả định bảng có đường kẻ đều và không xử lý tốt ảnh scan.
* `docling` (TableFormer): chính xác với tài liệu học thuật nhưng **188 giây
  cho 3 trang** trên CPU máy này và gộp cả biểu mẫu thành một bảng 41×13 lộn
  xộn. Vẫn giữ làm chế độ "sâu" cho người dùng tự chọn.

Cách "vùng trắng" ở trên chạy **0,2–0,5 giây/trang** với PDF có text layer và
xử lý ô gộp một cách tự nhiên (ô gộp = một vùng trắng lớn hơn).

## 7. Hiệu năng đo trên máy này

| Trường hợp | Kết quả |
|---|---|
| 11 file WI (37 trang, có text layer) | 11/11 OK, tổng 15,5 giây (0,8–2,4 s/file) |
| 1 file 3 trang | 0,85 giây, 3 bảng, 48 ô gộp |
| 1 trang scan (PDF chỉ có ảnh) | ~8,8 giây/trang (OCR tiếng Trung) |
| 6 trang scan liên tục | 53 giây (7–9 s/trang), tiến độ + ETA cập nhật mỗi giây |
| `PDF_scan.pdf` 4 trang scan (bảng biểu + ảnh) | ~33 giây, 9 bảng, không còn ô khổng lồ |

## 8. Giới hạn & lưu ý

* **Bảng không có đường kẻ**: app thử chia cột theo khoảng trắng; kết quả có
  thể lệch — nên xem lại bảng trên web trước khi tải.
* **Trang bản vẽ** (ví dụ trang 1 của form WI): vùng hình vẽ được **bỏ qua** để
  không tạo ô khổng lồ; chữ nằm trong hình vẽ sẽ không có trong file Excel.
* **Cột chỉ chứa ảnh** (ví dụ cột "图片示例"): không xuất được ảnh sang Excel nên
  cột đó có thể không thành ô — nội dung ảnh không nằm trong file Excel.
* **Bảng lồng trong bảng** hoặc bảng nhiều tầng header rất phức tạp: nên thử
  engine "sâu", hoặc sửa tay trên web trước khi tải.
* **Scan nghiêng**: tự phát hiện và xoay thẳng khi góc ≥ 0,25°.
* **Ảnh scan mờ/nhiễu**: chữ Hán có thể sai; ô vàng là gợi ý cần soát.
* **Giới hạn mặc định**: 60 trang/file, 100 MB/file, 20 file/lần — đổi bằng
  biến môi trường bên dưới.
* **Một job tại một thời điểm**: hàng đợi 1 luồng để giới hạn RAM khi OCR.

## 9. Cấu hình (biến môi trường)

| Biến | Mặc định | Ý nghĩa |
|---|---|---|
| `PDF2XLSX_HOST` | `127.0.0.1` | Địa chỉ lắng nghe |
| `PDF2XLSX_PORT` | `8765` | Cổng |
| `PDF2XLSX_DPI` | `200` | DPI render để dò lưới/OCR (tăng → chính xác hơn, chậm hơn) |
| `PDF2XLSX_MAX_PAGES` | `60` | Số trang tối đa mỗi file |
| `PDF2XLSX_MAX_MB` | `100` | Kích thước tối đa mỗi file |
| `PDF2XLSX_MAX_FILES` | `20` | Số file tối đa mỗi lần tải lên |
| `PDF2XLSX_DATA` | `./data` | Thư mục lưu job tạm |

## 10. API (dùng bằng script cũng được)

| Method | Đường dẫn | Việc |
|---|---|---|
| `POST` | `/api/jobs` | Tải lên (multipart, field `files`), trả về `id` + phân tích trang |
| `POST` | `/api/jobs/{id}/convert` | `{mode: auto\|text\|ocr, engine: fast\|deep, page_modes: {"1": "scan"}}` |
| `POST` | `/api/jobs/{id}/cancel` | Dừng job đang chạy (dừng sau trang hiện tại) |
| `GET` | `/api/jobs/{id}` | Trạng thái + kết quả JSON (lưới, span, ô tin cậy thấp) |
| `GET` | `/api/jobs/{id}/events` | SSE tiến độ (có thêm sự kiện `tick` mỗi 1,2 giây) |
| `POST` | `/api/jobs/{id}/export` | `{include: [id bảng], edits: {"p1_t1": {"0,1": "..."}}}` |
| `GET` | `/api/jobs/{id}/download` | Tải `.xlsx` (tự xuất nếu chưa xuất) |
| `POST` | `/api/export-zip` | `{ids: [...]}` → zip nhiều file Excel |
| `GET` | `/api/jobs/{id}/page/{n}` | Ảnh PNG trang để đối chiếu |
| `DELETE` | `/api/jobs/{id}` | Xoá job + dữ liệu tạm |

Tài liệu tương tác: <http://127.0.0.1:8765/docs>

## 11. Kiểm thử

```bat
cd D:\opencode\pdf2excel
python tests\test_pipeline.py
```

Không cần file mẫu: test tự sinh PDF bảng bằng `tests/make_pdf.py` (thuần
Python, không cần reportlab). Có thể chạy bằng `pytest tests/ -q`.

## 12. Sự cố thường gặp

| Hiện tượng | Cách xử lý |
|---|---|
| `UnicodeEncodeError: 'charmap' codec` khi chạy terminal | Chạy `run.bat` (đã đặt `PYTHONIOENCODING=utf-8`), hoặc `set PYTHONIOENCODING=utf-8` |
| Cổng 8765 đang bận | `set PDF2XLSX_PORT=8766` rồi chạy lại |
| Trông như "treo" khi xử lý PDF scan | Bình thường: OCR ~7–10 s/trang. Thanh tiến độ + ETA cập nhật mỗi giây. File 20 trang có thể mất ~3 phút. Bấm **Dừng** nếu không muốn chờ. |
| Bấm **Tải Excel** mà không thấy file | File nằm trong thư mục Downloads của trình duyệt (không mở tab mới). Kiểm tra thanh tải xuống; bấm **Tải lại**. |
| Thẻ vẫn hiện "Đang xử lý" dù đã xong | Tự khỏi sau ≤2,5 giây (UI có polling dự phòng ngoài SSE). Nếu vẫn đứng, tải lại trang. |
| Cần xem app đang làm gì | `data\logs\app.log` ghi thời gian từng bước của mỗi trang (render/skew/ocr/grid). |
| OCR chậm (~13 s/trang) | Chưa có `onnxruntime` → `python -m pip install onnxruntime` |
| Lần đầu OCR bị chậm | Lần đầu nạp model (~5 s); các trang sau nhanh hơn |
| Trang bị nhận nhầm là scan/text | Đổi chip `text`/`scan` cho đúng trang rồi chuyển đổi lại |
| Bảng bị tách thành nhiều sheet | Các bảng thật sự rời nhau; gộp lại bằng tay trong Excel nếu cần |
| Bảng thiếu chữ | Thử engine "sâu" hoặc sửa trực tiếp trên web rồi tải lại |
| Excel báo lỗi khi mở | Kiểm tra đã tải xong (file > 4 KB); tải lại từ nút **Tải Excel** |

## 13. Cấu trúc mã nguồn

```
pdf2excel/
├─ run.bat                     # khởi động + mở trình duyệt
├─ requirements.txt
├─ app/
│  ├─ main.py                  # FastAPI: routes
│  ├─ config.py                # hằng số, biến môi trường
│  ├─ core/
│  │  ├─ cellcomponents.py     # ★ dò ô bằng vùng trắng (lõi thuật toán)
│  │  ├─ extract.py            # điều phối theo trang
│  │  ├─ gridlines.py          # nhị phân hoá, xoá vùng chữ
│  │  ├─ textlayer.py          # toạ độ ký tự từ pdfium + ghép chữ
│  │  ├─ ocr.py                # RapidOCR (ONNX/PyTorch)
│  │  ├─ tables.py             # dựng bảng, fallback chia cột theo khoảng trắng
│  │  ├─ detect.py             # phân loại trang text/scan
│  │  ├─ render.py             # render, deskew, ảnh xem trước
│  │  └─ docling_engine.py     # engine "sâu" (tuỳ chọn)
│  ├─ export/xlsx.py           # ghi Excel bằng openpyxl
│  ├─ jobs/manager.py          # job, hàng đợi, SSE, xuất file
│  └─ web/                     # index.html, style.css, app.js (không CDN)
├─ tests/
│  ├─ make_pdf.py              # sinh PDF bảng tổng hợp
│  └─ test_pipeline.py         # test pipeline + API
└─ data/
   ├─ jobs/                   # dữ liệu tạm (xoá được bất cứ lúc nào)
   └─ logs/app.log            # log chẩn đoán (thời gian từng trang)
```

Muốn tinh chỉnh độ nhạy dò ô: sửa `MIN_FILL` (0.45) và `MIN_CELL_SIDE` (10)
trong `app/core/cellcomponents.py`.

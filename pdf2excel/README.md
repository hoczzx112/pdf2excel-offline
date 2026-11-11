# PDF → Excel (chạy tại máy, không gửi file lên mạng)

Web app nội bộ chuyển **bảng trong PDF** thành **Excel (.xlsx)**. Hỗ trợ cả PDF
có text layer và PDF scan (OCR). Mỗi trang PDF trở thành một sheet (mọi bảng
rời trên trang được gộp lại, giữ đúng vị trí), giữ ô gộp (merged cells) và
xuống dòng trong ô.

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

1. (Tuỳ chọn) Công tắc **Bỏ qua trang 1** ngay dưới ô kéo–thả, **bật sẵn**:
   app bỏ hẳn trang 1 và xử lý từ trang 2 — dành cho file mà trang đầu là bản vẽ
   AutoCAD. Công tắc áp dụng cho **tất cả file** trong một lần bấm và được ghi
   nhớ cho lần mở sau. File chỉ có 1 trang thì vẫn được xử lý (kèm cảnh báo).
2. Kéo–thả (hoặc bấm **chọn file**) một hay nhiều PDF. Tối đa 20 file,
   100 MB/file.
3. App tự phân tích rồi **tự chuyển đổi ngay** — không cần bấm từng file.
   Thanh tiến độ hiện `Trang 3/6: OCR ảnh · 24s (còn ~49s)` và nhích mỗi giây,
   kể cả khi một trang scan cần 5–10 giây. Có nút **Dừng** nếu muốn huỷ.
4. Xem trước bảng ngay trên web khi xong:
   * bấm vào ô để **sửa trực tiếp** (nội dung sửa được ghi vào Excel);
   * ô **tô vàng** = OCR có độ tin cậy < 0,75, nên kiểm tra lại;
   * **Xem trang** để đối chiếu với ảnh trang gốc;
   * bỏ tick nếu không muốn xuất bảng đó.
5. Bấm **Tải Excel** — file được tải thẳng về thư mục Downloads, trang không bị
   đổi. Muốn tải lại lần nữa thì bấm **Tải lại**. Nhiều file đã xong →
   **Tải tất cả (.zip)**.
6. Muốn đổi cách đọc (text/scan) hoặc chạy lại: mở **Tuỳ chọn nâng cao /
   chuyển đổi lại** ở cuối thẻ. Trang 1 đang bị bỏ qua sẽ hiện chip `bỏ qua`.
7. Nút ở góc trên bên phải dùng cho **mọi file cùng lúc**: còn file đang
   chờ/lỗi/đã dừng thì là **Chuyển đổi tất cả (N)**, khi tất cả đã xong thì
   thành **Chuyển đổi lại tất cả (N)** — bấm một lần để áp dụng tuỳ chọn hiện
   tại (ví dụ vừa tắt/bật *Bỏ qua trang 1*) cho toàn bộ file đã tải.

## 5. File Excel nhận được

| Sheet | Nội dung |
|---|---|
| `T{trang}` | Trang PDF có nhiều bảng rời: gộp tất cả vào **một** sheet, giữ đúng vị trí |
| `T{trang}-B{thứ tự}` | Trang chỉ có một bảng: mỗi bảng một sheet |

Mọi sheet đều giữ ô gộp, viền mảnh, dòng đầu in đậm + nền xám.

File chỉ có đúng các sheet bảng — không còn sheet phụ `_Thông tin` /
`_Văn bản gốc` như bản trước. Nếu không có bảng nào được chọn thì file vẫn có
một sheet `Kết quả` trống kèm ghi chú, để Excel mở được bình thường.

Ô OCR độ tin cậy thấp được tô vàng và có ghi chú (comment) kèm điểm tin cậy.
Nội dung bắt đầu bằng `=` được lưu dạng **chuỗi**, không bị Excel hiểu thành
công thức. Ô không có nội dung được để **trống thật** (không ghi chuỗi rỗng),
nên `ISBLANK`, `COUNTA` và *Go To Special → Blanks* hoạt động đúng.

Chữ dài trong ô **không** bị chèn xuống dòng cứng (Alt+Enter) theo đúng chỗ
ngắt của bản PDF nữa. App nối các dòng đó lại thành một đoạn (chèn khoảng trắng
khi cần, ví dụ `R-TNCRB057` + `08406C.02` → `R-TNCRB057 08406C.02`), rồi để
Excel tự xuống dòng theo độ rộng cột. Chiều cao dòng được ước lượng sẵn để ô
gộp (Excel không tự giãn ô gộp) vẫn hiện đủ chữ.

**Ngoại lệ: mục danh sách.** Dòng mở đầu bằng số thứ tự (`1.` `2.` `3.`,
`1)`, `(2)`, `①`) là một bước riêng trong bản gốc, nên app **giữ lại chỗ xuống
dòng** trước mỗi mục — gộp hết lại thì các bước dính liền nhau rất khó đọc.
Trong mỗi mục, chữ vẫn tự xuống dòng theo độ rộng cột như bình thường. Số thập
phân / ngày tháng / kích thước (`0.02`, `1.5mm`, `20.09.2026`) không bị hiểu
nhầm thành mục danh sách.


Dấu cách **có thật** trong text layer của PDF được giữ nguyên, nên các cụm như
`6-M5×0.8 6H` không còn bị dính thành `6-M5×0.86H` (khoảng hở giữa `0.8` và
`6H` chỉ ~2,3 pt, nhỏ hơn nhiều so với một dấu cách bình thường).

Mỗi sheet còn được **đặt sẵn chế độ in**: khổ **A4**, đúng chiều của trang PDF
(dọc/ngang), ẩn lưới và đặt vùng in đúng phạm vi bảng. Độ rộng cột và chiều cao
hàng lấy theo **kích thước thật của đường kẻ trong PDF**, còn lề trên/dưới/
trái/phải lấy theo **đúng vị trí bảng trên trang PDF** — nên *Print Preview*
cho ra bố cục y như bản gốc. Chế độ **Fit to 1 page** luôn được bật (Excel chỉ
thu nhỏ, không phóng to), vì vậy mỗi sheet chắc chắn in ra đúng **một** trang
A4, không bao giờ tràn sang trang thứ hai.

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
        chữ Hán chỉ cách nhau khi hở > 1,2 em) → Excel
```

Một số lỗi thường gặp trên bản scan / bản vẽ CAD và cách xử lý:

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
  Nay hộp xoá được **thụt vào 1 px** thay vì nới ra. Với chữ **rộng hơn ô** nên
  hộp bao vượt hẳn qua vạch kẻ (kiểu `产品0.3mTmax`), bước xoá còn **giữ lại
  những cột/dòng có mực liền mạch ngay trên+dưới** hộp — đó là đường kẻ chạy
  xuyên qua, không phải nét ký tự, nên vạch không bị cắt thành hai đoạn.
  Cùng lúc, số mũ (`10⁸`) nằm cao hơn dòng nên trước đây bị tách thành dòng
  riêng, làm chữ đảo thứ tự (`隙)：1.51×10⁸` → `隙1 51×108` + `)：.`); nay các
  ký tự được gom dòng theo **độ chồng lấn theo trục dọc**, nên số mũ nằm đúng
  dòng của nó.
* **`1,2` bị tách thành `1 2` + `,`** — dấu phẩy là glyph thấp, gần như không
  chồng lấn theo trục dọc với chữ số cùng dòng, nên bị coi là "dòng khác". Nay
  ngoài độ chồng lấn còn so **đường chân** (đáy hộp bao) với dung sai
  ~0,35 chiều cao chữ, và không chèn khoảng trắng sát dấu câu. Ô chỉ có số còn
  được chuẩn hoá dấu phẩy: PDF lúc dùng `,` lúc dùng `，` → luôn ra `1,2`.
* **Ký hiệu `ø` bị mất** (file xuất từ AutoCAD) — ký hiệu đường kính được **vẽ
  bằng nét vector** (một vòng elip bị gạch chéo), text layer hoàn toàn không có
  ký tự nào, nên đọc chữ không bao giờ thấy. Nay app dò thêm các *path* vector
  cỡ một ký tự và nhận dạng hình học: ảnh render là **một** nét liền, vòng kín
  chia thành **đúng 2 lỗ** nhỏ, gần bằng nhau; hai lỗ hoặc **chồng lên nhau**
  (gạch chéo ~45°), hoặc nằm cạnh nhau và **giữa chúng là một nét mực liền**
  (gạch chéo gần thẳng đứng, như ký hiệu trong file YMFQ). Nhờ vậy ô ra
  `ø73(0/-0.009)` thay vì `73(0/-0.009)`. Nét kẻ bảng, ô checkbox (1 lỗ to,
  hoặc 2 lỗ không cân), chuỗi chữ vẽ lại bằng nét (kiểu `300s` — hai chữ `0`
  cạnh nhau cũng tạo 2 lỗ nhưng giữa là khe trắng) và chữ `8` (2 lỗ xếp dọc
  nhưng cao hơn rộng) đều bị loại nên không sinh ký hiệu giả.
* **Ô tick ☑/☐ bị mất** — khung ô chọn được **vẽ bằng nét vector** (4 cạnh
  trong một path), còn dấu `√` mới nằm trong text layer. Text layer không có
  ký tự nào cho cái khung, nên Excel chỉ còn `√样件试生产量产` — hai ô chưa
  tick biến mất hẳn. Nay app nhận khung vuông rỗng: path cỡ 5–16 pt, ảnh
  render có **bốn cạnh ngoài là nét liền**, bốn viền dày gần bằng nhau và ruột
  rỗng. Vòng tròn (ký hiệu ⊕ trong bản vẽ), nét chéo và chữ vẽ bằng nét đều bị
  loại. Khung có mực bên trong (dấu √) ra `☑`, khung rỗng ra `☐`, và dấu `√`
  rời trong khung được bỏ để không bị lặp thành `☑√`.
  Nhờ vậy dòng đầu trang ra `☑样件 ☐试生产 ☐量产` giống bản PDF.
* **Ký hiệu `ø` bị đọc thành `0`** (bản scan) — RapidOCR đọc nét gạch chéo của
  `ø` thành chữ số, nên `ø129.5±0.05` ra `0129.5±0.05`, `8-ø3.9` ra `8-03.9`,
  `钻ø3.9孔` ra `钻03.9孔`. Kết quả sai ổn định nên không thể sửa bằng cách đoán
  chuỗi; app nhìn lại **ảnh** của đúng dòng đó: `ø` là một vòng bị nét gạch cắt
  thành **hai nửa lỗ sát nhau** (giữa chúng chỉ là bề dày nét gạch), còn `0` chỉ
  có một lỗ và `8` có hai lỗ nhưng **cách xa nhau**. Chỉ chữ `0` ở đầu chuỗi
  hoặc ngay sau dấu ngăn cách / chữ Hán mới được xét, nên `0.001`, `AC=0`,
  `20.09.2026` vẫn giữ nguyên.
* **Chữ nhảy sang ô bên cạnh** (bản scan) — OCR hay gộp hai ô cạnh nhau thành
  **một** dòng chữ (nhất là khi ô trái có chữ sát đường kẻ), ví dụ
  `★直接测量（首件送检不要去毛刺做★首检/末检` bị đọc thành một item, nên cả cụm
  dồn vào ô `检测方法` còn ô `自检频次` trống. Nay item nào tràn qua **biên ô
  thật** (biên của một cặp ô kề nhau trong lưới, không phải mọi đường kẻ của
  bảng) đều được cắt lại theo vị trí ký tự, mỗi nửa vào đúng ô của nó; item chỉ
  chờm qua biên một chút (hộp bao OCR rộng hơn chữ) thì giữ nguyên.
* **Các bước `1. 2. 3.` dính thành một đoạn** — app gộp mọi dòng trong ô để
  Excel tự xuống dòng, nên `1. 手持扫码枪…。2.上线扫二维码…` chạy liền một
  mạch. Nay dòng nào mở đầu bằng số thứ tự (`1.` `2)` `(3)` `①`) được giữ
  nguyên chỗ xuống dòng, còn chữ bên trong mỗi bước vẫn tự dàn theo độ rộng
  cột.

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
| `JYCP-CRB 06812309` 7 trang (`/Rotate 90`) | 14,3 s nếu lấy cả trang 1; **2,0 s** khi bỏ qua trang 1 (6 bảng, đúng từng ô) |

## 8. Giới hạn & lưu ý

* **Bảng không có đường kẻ**: app thử chia cột theo khoảng trắng; kết quả có
  thể lệch — nên xem lại bảng trên web trước khi tải.
* **Trang bản vẽ** (trang 1 của form WI, bản vẽ AutoCAD): công tắc **Bỏ qua
  trang 1** đang bật sẵn nên app xử lý từ trang 2. Bản vẽ có hàng nghìn nét
  vector nên vừa chậm vừa sinh nhiều "bảng rác" — bỏ qua vừa nhanh hơn nhiều
  (file JYCP 7 trang: 14,3 s → 2,0 s) vừa sạch kết quả. Cần lấy cả trang 1 thì
  tắt công tắc rồi bấm **Chuyển đổi lại tất cả**.
* **Cột chỉ chứa ảnh** (ví dụ cột "图片示例"): không xuất được ảnh sang Excel nên
  cột đó có thể không thành ô — nội dung ảnh không nằm trong file Excel.
* **Bảng lồng trong bảng** hoặc bảng nhiều tầng header rất phức tạp: nên thử
  engine "sâu", hoặc sửa tay trên web trước khi tải.
* **Một trang PDF có nhiều bảng rời**: tất cả được gộp vào **một sheet**
  (`T2`) và giữ nguyên vị trí như trang gốc, nên in ra vẫn đúng bố cục và vẫn
  gọn trong 1 trang A4. Trang chỉ có một bảng thì tên sheet là `T2-B1`.
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
| `POST` | `/api/jobs/{id}/convert` | `{mode: auto\|text\|ocr, engine: fast\|deep, page_modes: {"1": "scan"}, skip_first_page: true}` |
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
| Bảng bị tách thành nhiều sheet | Chỉ xảy ra khi các bảng nằm ở **trang khác nhau** (mỗi trang một sheet) hoặc khi bạn bỏ chọn bớt bảng trên web |
| Trang in bị tràn sang trang 2 | App đã bật sẵn *Fit to 1 page*; nếu bạn tự đổi khổ giấy/lề trong Excel thì bật lại *Page Setup → Fit to: 1 page wide by 1 tall* |
| Bảng thiếu chữ | Thử engine "sâu" hoặc sửa trực tiếp trên web rồi tải lại |
| Excel báo lỗi khi mở | Kiểm tra đã tải xong (file > 4 KB); tải lại từ nút **Tải Excel** |
| Cả bảng thành rác, chữ nằm sai ô (file có `/Rotate 90`) | Đã sửa: hộp bao ký tự của pdfium nằm ở hệ toạ độ **chưa xoay**, nay app xoay theo `/Rotate` của trang. Nếu vẫn lỗi, gửi lại file. |
| Ô header ra `项\n目\n分\n类` (mỗi ký tự một dòng) | Đã sửa: dòng chữ mà pdfium cắt thành lát mỏng được chia lại theo chiều chạy của dòng. |
| Ô hiện chữ cũ đã bị vẽ đè (vd. `共2页第7页`) | Đã sửa: bỏ lớp chữ nằm dưới, giữ lớp vẽ sau (lớp nhìn thấy trên giấy). |
| Bản scan ra `0129.5±0.05` thay vì `ø129.5±0.05` | Đã sửa: app nhìn ảnh để phân biệt `ø` (hai nửa lỗ sát nhau) với `0`/`8`; `0.001`, `AC=0`, ngày tháng không bị đổi. |
| Bản scan: chữ dài nhảy sang ô bên cạnh | Đã sửa: dòng OCR gộp hai ô được cắt lại theo biên ô thật, mỗi nửa vào đúng ô. |
| Các bước `1. 2. 3.` trong một ô bị dính thành một đoạn | Đã sửa: dòng mở đầu bằng số thứ tự (`1.` `2)` `(3)` `①`) được giữ xuống dòng đúng như bản gốc; số thập phân/ngày tháng không bị ảnh hưởng. |
| Trang bản vẽ AutoCAD làm app rất chậm | Bật **Bỏ qua trang 1** (mặc định đã bật) rồi **Chuyển đổi lại tất cả**. |

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
│  │  ├─ vectorglyph.py        # nhận ký hiệu ø vẽ bằng nét vector
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

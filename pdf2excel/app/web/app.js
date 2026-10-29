/* PDF → Excel — giao diện */
"use strict";

const $ = (sel, root = document) => root.querySelector(sel);
const el = (tag, cls, text) => {
  const n = document.createElement(tag);
  if (cls) n.className = cls;
  if (text != null) n.textContent = text;
  return n;
};

const ROW_LIMIT = 120;   // số dòng vẽ trước cho bảng lớn (giữ UI không bị đứng)

const state = {
  jobs: new Map(), // id -> { snap, result, edits, include, es, rowLimit, exported, exportError }
};

/* ------------------------------------------------------------------ tiện ích */
function toast(msg, ms = 3600) {
  const t = $("#toast");
  t.textContent = msg;
  t.hidden = false;
  clearTimeout(t._h);
  t._h = setTimeout(() => (t.hidden = true), ms);
}

function fmtSize(bytes) {
  if (bytes < 1024) return bytes + " B";
  if (bytes < 1024 * 1024) return (bytes / 1024).toFixed(0) + " KB";
  return (bytes / 1024 / 1024).toFixed(1) + " MB";
}

function kindBadge(kind) {
  const map = {
    text: ["text", "Có text layer"],
    scan: ["scan", "Scan / ảnh"],
    mixed: ["mixed", "Hỗn hợp"],
  };
  const [cls, label] = map[kind] || ["state", kind || "?"];
  return el("span", "badge " + cls, label);
}

function statusBadge(status) {
  const map = {
    uploaded: ["state", "Đã tải lên"],
    analyzing: ["state", "Đang phân tích"],
    ready: ["state", "Sẵn sàng"],
    converting: ["state", "Đang xử lý"],
    done: ["done", "Hoàn tất"],
    cancelled: ["scan", "Đã dừng"],
    error: ["error", "Lỗi"],
  };
  const [cls, label] = map[status] || ["state", status];
  return el("span", "badge " + cls, label);
}

async function api(url, opts = {}) {
  const res = await fetch(url, opts);
  const ct = res.headers.get("content-type") || "";
  const body = ct.includes("application/json") ? await res.json() : await res.text();
  if (!res.ok) {
    const detail = body && body.detail ? body.detail : body;
    throw new Error(typeof detail === "string" ? detail : JSON.stringify(detail));
  }
  return body;
}

/* ------------------------------------------------------------------- upload */
async function uploadFiles(files) {
  const pdfs = Array.from(files).filter(
    (f) => f.name.toLowerCase().endsWith(".pdf") || f.type === "application/pdf"
  );
  if (!pdfs.length) return toast("Chỉ nhận file PDF.");

  const fd = new FormData();
  pdfs.forEach((f) => fd.append("files", f));

  toast(`Đang tải lên ${pdfs.length} file…`);
  try {
    const data = await api("/api/jobs", { method: "POST", body: fd });
    for (const job of data.jobs) {
      state.jobs.set(job.id, {
        snap: job,
        result: null,
        edits: {},
        include: new Set(),
        es: null,
        poll: null,
        lastSeq: job.progress?.seq || 0, // bỏ qua sự kiện cũ khi kết nối lại SSE
        rowLimit: {},
        exported: null,
        exportError: null,
      });
      renderCard(job.id);
    }
    $("#empty").hidden = true;
    updateZipButton();
    updateRunAllButton();

    // Tự động chuyển đổi ngay — không cần bấm từng file
    for (const job of data.jobs) {
      if (job.status === "ready") autoStart(job.id);
    }
  } catch (err) {
    toast("Lỗi tải lên: " + err.message, 7000);
  } finally {
    $("#file").value = "";
  }
}

function autoStart(jobId) {
  const st = state.jobs.get(jobId);
  if (!st) return;
  startConvert(jobId, "auto", "fast");
}

/* ------------------------------------------------------- chạy tất cả 1 nút */
const PENDING_STATUS = new Set(["uploaded", "analyzing", "ready", "cancelled", "error"]);

function pendingJobIds() {
  const ids = [];
  state.jobs.forEach((st, id) => {
    if (PENDING_STATUS.has(st.snap.status)) ids.push(id);
  });
  return ids;
}

function updateRunAllButton() {
  const btn = $("#btn-run-all");
  if (!btn) return;
  const n = pendingJobIds().length;
  btn.hidden = n === 0;
  btn.disabled = n === 0;
  btn.textContent = n > 1 ? `Chuyển đổi tất cả (${n})` : "Chuyển đổi tất cả";
}

function runAll() {
  const ids = pendingJobIds();
  if (!ids.length) {
    toast("Không có file nào đang chờ chuyển đổi.");
    return;
  }
  for (const id of ids) startConvert(id, "auto", "fast");
  toast(`Đã bắt đầu chuyển đổi ${ids.length} file — server xử lý lần lượt.`, 5000);
  updateRunAllButton();
}

/* ---------------------------------------------------------------- chuyển đổi */
async function startConvert(jobId, mode, engine) {
  const st = state.jobs.get(jobId);
  if (!st) return;

  const pageModes = {};
  document.querySelectorAll(`#job-${jobId} .chip select[data-page]`).forEach((s) => {
    pageModes[s.dataset.page] = s.value;
  });

  try {
    await api(`/api/jobs/${jobId}/convert`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ mode, engine, page_modes: pageModes }),
    });
  } catch (err) {
    const msg = String(err.message);
    if (!msg.includes("đang được xử lý")) {
      toast("Không bắt đầu được: " + msg, 7000);
      return;
    }
  }

  st.snap.status = "converting";
  st.snap.error = null;
  st.result = null;
  st.exported = null;
  st.exportError = null;
  st.snap.progress = { pct: 0, message: "Bắt đầu xử lý…" };
  renderCard(jobId);
  subscribe(jobId);
  startPolling(jobId);
}

async function cancelJob(jobId) {
  const st = state.jobs.get(jobId);
  if (!st) return;
  try {
    await api(`/api/jobs/${jobId}/cancel`, { method: "POST" });
    st.snap.progress = { pct: st.snap.progress?.pct || 0, message: "Đang dừng…" };
    renderCard(jobId);
  } catch (err) {
    toast("Không dừng được: " + err.message, 6000);
  }
}

function subscribe(jobId) {
  const st = state.jobs.get(jobId);
  if (!st) return;
  if (st.es) st.es.close();

  const es = new EventSource(`/api/jobs/${jobId}/events`);
  st.es = es;

  es.onmessage = (msg) => {
    let ev;
    try { ev = JSON.parse(msg.data); } catch { return; }
    // SSE phát lại toàn bộ sự kiện từ đầu mỗi lần kết nối. Nếu không lọc, sự kiện
    // kết thúc của lần chạy TRƯỚC (vd. "cancelled") sẽ đóng kết nối ngay khi vừa
    // bấm chạy lại, khiến thẻ kẹt ở "Đang xử lý" dù server đã xong.
    const seq = Number(ev.seq) || 0;
    if (seq && seq <= (st.lastSeq || 0)) return;
    if (seq) st.lastSeq = seq;
    st.snap.progress = ev;
    updateProgressDom(jobId, ev);

    if (["completed", "error", "cancelled"].includes(ev.stage)) {
      es.close();
      st.es = null;
      stopPolling(jobId);
      refreshJob(jobId);
    }
  };
  es.onerror = () => {
    es.close();
    st.es = null;
    // SSE có thể bị chặn/ngắt (đổi tab, proxy…) — polling vẫn đảm bảo cập nhật
    refreshJob(jobId).then(() => {
      const cur = state.jobs.get(jobId);
      if (cur && cur.snap.status === "converting" && !cur.es) {
        setTimeout(() => {
          const now = state.jobs.get(jobId);
          if (now && now.snap.status === "converting" && !now.es) subscribe(jobId);
        }, 2000);
      }
    });
  };
}

function updateProgressDom(jobId, ev) {
  if (!ev) return;
  const card = document.getElementById("job-" + jobId);
  if (!card) return;
  const prog = card.querySelector('[data-role="progress"]');
  if (prog) {
    const fill = prog.querySelector("i");
    if (fill) fill.style.width = Math.max(2, ev.pct || 0) + "%";
    const txt = prog.querySelector(".progress-text");
    if (txt) txt.textContent = ev.message || "Đang xử lý…";
  }
  const meta = card.querySelector(".card-meta");
  const st = state.jobs.get(jobId);
  if (meta && ev.elapsed && st && st.snap.analysis) {
    meta.textContent =
      `${fmtSize(st.snap.size)} · ${st.snap.analysis.num_pages} trang · ${ev.elapsed}s`;
  }
}

/* Polling dự phòng: nếu SSE không tới (tab bị throttle, mạng chập), UI vẫn cập nhật.
   Đây là lý do trước đây app có thể "kẹt" ở trạng thái Đang xử lý dù server đã xong. */
function startPolling(jobId) {
  const st = state.jobs.get(jobId);
  if (!st) return;
  stopPolling(jobId);
  st.poll = setInterval(async () => {
    const cur = state.jobs.get(jobId);
    if (!cur) return stopPolling(jobId);
    if (cur.snap.status !== "converting") return stopPolling(jobId);
    try {
      const data = await api(`/api/jobs/${jobId}`);
      if (data.status === "converting") {
        if (data.progress) {
          cur.snap.progress = data.progress;
          updateProgressDom(jobId, data.progress);
        }
        return;
      }
      stopPolling(jobId);
      if (cur.es) {
        cur.es.close();
        cur.es = null;
      }
      await refreshJob(jobId);
    } catch {
      /* thử lại ở nhịp sau */
    }
  }, 2500);
}

function stopPolling(jobId) {
  const st = state.jobs.get(jobId);
  if (st && st.poll) {
    clearInterval(st.poll);
    st.poll = null;
  }
}

async function refreshJob(jobId) {
  try {
    const data = await api(`/api/jobs/${jobId}`);
    const st = state.jobs.get(jobId);
    if (!st) return;
    const prevStatus = st.snap.status;
    st.snap = { ...data };
    if (data.progress && data.progress.seq) {
      st.lastSeq = Math.max(st.lastSeq || 0, data.progress.seq);
    }
    if (data.result) {
      st.result = data.result;
      if (prevStatus !== "done") {
        st.include = new Set(data.result.tables.map((t) => t.id));
        st.edits = {};
        st.rowLimit = {};
      }
    }
    renderCard(jobId);
    updateZipButton();
    updateRunAllButton();

    // Tự chữa lành: server vẫn "converting" nhưng không còn kênh cập nhật nào
    // (SSE bị đóng, polling bị dừng) thì mở lại ngay, tránh thẻ kẹt vĩnh viễn.
    if (st.snap.status === "converting" && !st.es && !st.poll) {
      subscribe(jobId);
      startPolling(jobId);
    }
  } catch (err) {
    toast("Không lấy được kết quả: " + err.message, 7000);
  }
}

/* --------------------------------------------------------------- render card */
function renderCard(jobId) {
  const st = state.jobs.get(jobId);
  if (!st) return;
  const { snap } = st;
  if (snap.status !== "converting") stopPolling(jobId);

  let card = document.getElementById("job-" + jobId);
  if (!card) {
    card = el("article", "card");
    card.id = "job-" + jobId;
    $("#jobs").prepend(card);
  }
  card.innerHTML = "";

  /* --- header --- */
  const head = el("div", "card-head");
  const title = el("div", "card-title");
  title.append(el("h3", null, snap.filename));

  const meta = el("p", "card-meta");
  const pages = snap.analysis ? snap.analysis.num_pages : "?";
  meta.textContent = `${fmtSize(snap.size)} · ${pages} trang · ${snap.elapsed}s`;
  title.append(meta);

  const badges = el("div", "card-head");
  badges.style.padding = "0";
  badges.style.border = "0";
  badges.style.gap = "6px";
  if (snap.analysis) badges.append(kindBadge(snap.analysis.doc_kind));
  badges.append(statusBadge(snap.status));

  const del = el("button", "btn ghost small", "Xoá");
  del.onclick = () => removeJob(jobId);

  head.append(title, badges, del);
  card.append(head);

  const body = el("div", "card-body");
  card.append(body);

  /* --- lỗi / đã dừng --- */
  if (snap.status === "error" && snap.error) {
    body.append(el("div", "error-box", snap.error));
    const retry = el("button", "btn small", "Thử lại");
    retry.onclick = () => startConvert(jobId, "auto", "fast");
    body.append(wrapRow(retry));
  }
  if (snap.status === "cancelled") {
    body.append(el("div", "warn-box", "Đã dừng theo yêu cầu. Bạn có thể chạy lại."));
    const retry = el("button", "btn small", "Chuyển đổi lại");
    retry.onclick = () => startConvert(jobId, "auto", "fast");
    body.append(wrapRow(retry));
  }

  /* --- đang xử lý: tiến độ + nút dừng --- */
  if (snap.status === "converting") {
    const prog = el("div", "progress");
    prog.dataset.role = "progress";
    const bar = el("div", "bar");
    const fill = el("i");
    bar.append(fill);
    const pct = snap.progress ? snap.progress.pct || 0 : 0;
    fill.style.width = Math.max(2, pct) + "%";
    const txt = el("div", "progress-text",
      (snap.progress && snap.progress.message) || "Đang xử lý…");
    prog.append(bar, txt);
    body.append(prog);

    const row = el("div", "controls");
    const stop = el("button", "btn ghost small", "Dừng");
    stop.onclick = () => cancelJob(jobId);
    row.append(stop, el("span", "muted",
      "Trang scan cần ~5–10 giây/trang. Tiến độ cập nhật mỗi giây."));
    body.append(row);
  }

  /* --- cảnh báo tài liệu hỗn hợp --- */
  if (snap.analysis && snap.analysis.doc_kind === "mixed" && !st.result) {
    body.append(el("div", "warn-box",
      "Tài liệu có cả trang text và trang scan — mỗi trang sẽ được xử lý theo cách phù hợp."));
  }

  /* --- kết quả --- */
  if (st.result && snap.status === "done") {
    renderResult(jobId, body);
  } else if (snap.status === "ready" || snap.status === "analyzing" || snap.status === "uploaded") {
    body.append(controlsBlock(jobId, { withButton: true, label: "Chuyển đổi" }));
  }

  /* --- tuỳ chọn nâng cao khi đã có kết quả --- */
  if (st.result && snap.status === "done") {
    const det = el("details", "advanced");
    det.append(el("summary", null, "Tuỳ chọn nâng cao / chuyển đổi lại"));
    det.append(controlsBlock(jobId, { withButton: true, label: "Chuyển đổi lại" }));
    body.append(det);
  }

  updateRunAllButton();
}

function wrapRow(...nodes) {
  const row = el("div", "controls");
  nodes.forEach((n) => row.append(n));
  return row;
}

function controlsBlock(jobId, { withButton = true, label = "Chuyển đổi" } = {}) {
  const st = state.jobs.get(jobId);
  const box = el("div", "controls");

  const mode = el("select");
  [["auto", "Tự động nhận dạng"], ["text", "Đọc text layer"], ["ocr", "OCR ảnh"]]
    .forEach(([v, t]) => mode.append(new Option(t, v)));
  mode.value = (st.snap.options && st.snap.options.mode) || "auto";

  const engine = el("select");
  [["fast", "Nhanh (khuyến nghị)"], ["deep", "Sâu (chậm, layout lạ)"]]
    .forEach(([v, t]) => engine.append(new Option(t, v)));
  engine.value = (st.snap.options && st.snap.options.engine) || "fast";

  const f1 = el("label", "field", "Cách đọc");
  f1.append(mode);
  const f2 = el("label", "field", "Engine");
  f2.append(engine);
  box.append(f1, f2);

  if (withButton) {
    const go = el("button", "btn small", label);
    go.disabled = st.snap.status === "converting";
    go.onclick = () => startConvert(jobId, mode.value, engine.value);
    box.append(go);
  }

  // chip từng trang (chỉ khi >1 trang)
  if (st.snap.analysis && st.snap.analysis.pages && st.snap.analysis.pages.length > 1) {
    const pages = el("div", "pages");
    for (const p of st.snap.analysis.pages) {
      const chip = el("span", "chip " + p.kind);
      chip.append(el("span", null, "Trang " + p.page));
      const sel = el("select");
      sel.append(new Option("text", "text"), new Option("scan", "scan"));
      sel.value = p.kind;
      sel.dataset.page = p.page;
      chip.append(sel);
      pages.append(chip);
    }
    box.append(pages);
  }

  return box;
}

/* ---------------------------------------------------------------- kết quả */
function renderResult(jobId, body) {
  const st = state.jobs.get(jobId);
  const res = st.result;

  const summary = el("div", "warn-box info-box");
  summary.textContent =
    `Tìm thấy ${res.num_tables} bảng trong ${res.num_pages} trang · ${res.duration}s. ` +
    `Bấm vào ô để sửa trước khi tải.`;
  body.append(summary);

  const list = el("div", "results");
  body.append(list);
  for (const t of res.tables) {
    list.append(renderTableCard(jobId, t));
  }

  /* tải về */
  const actions = el("div", "controls");
  const dl = el("button", "btn", "Tải Excel");
  dl.onclick = () => exportJob(jobId);
  actions.append(dl);

  if (st.exported) {
    const again = el("button", "btn ghost", "Tải lại");
    again.onclick = () => triggerDownload(`/api/jobs/${jobId}/download?t=${Date.now()}`, st.exported);
    actions.append(again, el("span", "muted", st.exported));
  }
  if (res.num_tables === 0) {
    actions.append(el("span", "badge scan", "Không có bảng — file Excel sẽ chỉ có sheet trống"));
  }
  body.append(actions);

  if (st.exportError) {
    body.append(el("div", "error-box", "Lỗi xuất Excel: " + st.exportError));
  }
}

function renderTableCard(jobId, t) {
  const st = state.jobs.get(jobId);
  const card = el("div", "tbl-card");

  const head = el("div", "tbl-head");
  const cb = document.createElement("input");
  cb.type = "checkbox";
  cb.checked = st.include.has(t.id);
  cb.onchange = () => {
    cb.checked ? st.include.add(t.id) : st.include.delete(t.id);
  };

  const name = el("span", null, t.title);
  const grow = el("span", "grow");
  const info = el("span", "info", `${t.rows}×${t.cols} · ${t.engine}`);

  const thumbBtn = el("button", "btn ghost small", "Xem trang");
  head.append(cb, name, grow, info, thumbBtn);
  card.append(head);

  /* bảng */
  const scroll = el("div", "tbl-scroll");
  const table = el("table", "grid");

  const spans = new Map();
  (t.spans || []).forEach((s) => spans.set(s.r + "," + s.c, s));
  const low = new Set((t.low_conf || []).map((x) => x.r + "," + x.c));
  const covered = new Set();
  const limit = st.rowLimit[t.id] || ROW_LIMIT;
  const rows = Math.min(t.rows, limit);

  for (let r = 0; r < rows; r++) {
    const tr = document.createElement("tr");
    for (let c = 0; c < t.cols; c++) {
      const key = r + "," + c;
      if (covered.has(key)) continue;

      const span = spans.get(key);
      const rs = span ? span.rs : 1;
      const cs = span ? span.cs : 1;
      for (let dr = 0; dr < rs; dr++) {
        for (let dc = 0; dc < cs; dc++) {
          if (dr || dc) covered.add((r + dr) + "," + (c + dc));
        }
      }

      const td = document.createElement("td");
      if (rs > 1) td.rowSpan = rs;
      if (cs > 1) td.colSpan = cs;
      if (r === 0) td.classList.add("head");
      if (low.has(key)) {
        td.classList.add("low");
        td.title = "Độ tin cậy OCR thấp — nên kiểm tra lại";
      }

      const edits = st.edits[t.id] || {};
      td.textContent = key in edits ? edits[key] : t.grid[r][c] || "";
      td.contentEditable = "true";
      td.spellcheck = false;
      td.dataset.r = r;
      td.dataset.c = c;
      td.addEventListener("blur", () => {
        const box = (st.edits[t.id] = st.edits[t.id] || {});
        box[key] = td.textContent;
      });
      tr.append(td);
    }
    table.append(tr);
  }
  scroll.append(table);
  card.append(scroll);

  if (t.rows > rows) {
    const more = el("button", "btn ghost small load-more",
      `Hiện tất cả ${t.rows} dòng (còn ${t.rows - rows})`);
    more.onclick = () => {
      st.rowLimit[t.id] = Infinity;
      card.replaceWith(renderTableCard(jobId, t));
    };
    card.append(more);
  }

  /* thumbnail */
  const thumb = el("div", "thumb");
  thumb.hidden = true;
  thumbBtn.onclick = () => {
    if (thumb.hidden && !thumb.firstChild) {
      const img = document.createElement("img");
      img.loading = "lazy";
      img.src = `/api/jobs/${jobId}/page/${t.page}`;
      img.alt = "Trang " + t.page;
      thumb.append(img);
    }
    thumb.hidden = !thumb.hidden;
    thumbBtn.textContent = thumb.hidden ? "Xem trang" : "Ẩn trang";
  };
  card.append(thumb);

  return card;
}

/* ----------------------------------------------------------------- export */
async function exportJob(jobId) {
  let st = state.jobs.get(jobId);
  if (!st) return;

  // Thẻ có thể đang cũ (SSE lỡ sự kiện cuối) — lấy lại trạng thái rồi mới xuất.
  if (!st.result) {
    await refreshJob(jobId);
    st = state.jobs.get(jobId);
    if (!st || !st.result) {
      toast("Chưa có kết quả để xuất. Hãy chờ chuyển đổi xong.", 6000);
      return;
    }
  }

  const allIds = st.result.tables.map((t) => t.id);
  const include = st.include.size === allIds.length ? null : Array.from(st.include);

  st.exportError = null;
  try {
    const res = await api(`/api/jobs/${jobId}/export`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ include, edits: st.edits }),
    });
    st.exported = res.filename || null;
    renderCard(jobId);
    // tải bằng thẻ <a download>: không điều hướng trang, không mất trạng thái
    triggerDownload(`/api/jobs/${jobId}/download?t=${Date.now()}`, st.exported);
    toast("Đã tạo file Excel: " + (st.exported || ""));
  } catch (err) {
    st.exportError = err.message;
    renderCard(jobId);
    toast("Lỗi xuất Excel: " + err.message, 8000);
  }
}

function triggerDownload(url, filename) {
  const a = document.createElement("a");
  a.href = url;
  if (filename) a.download = filename;
  a.rel = "noopener";
  a.style.display = "none";
  document.body.appendChild(a);
  a.click();
  a.remove();
}

function updateZipButton() {
  const done = [];
  state.jobs.forEach((st, id) => {
    if (st.result) done.push(id);
  });
  const btn = $("#btn-zip");
  btn.hidden = done.length < 2;
  btn.onclick = async () => {
    try {
      const res = await fetch("/api/export-zip", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ ids: done }),
      });
      if (!res.ok) throw new Error(await res.text());
      const blob = await res.blob();
      const a = document.createElement("a");
      a.href = URL.createObjectURL(blob);
      a.download = "pdf2excel.zip";
      a.click();
      URL.revokeObjectURL(a.href);
    } catch (err) {
      toast("Lỗi tạo ZIP: " + err.message, 7000);
    }
  };
}

async function removeJob(jobId) {
  const st = state.jobs.get(jobId);
  if (st && st.es) st.es.close();
  stopPolling(jobId);
  try {
    await api(`/api/jobs/${jobId}`, { method: "DELETE" });
  } catch { /* bỏ qua */ }
  state.jobs.delete(jobId);
  const node = document.getElementById("job-" + jobId);
  if (node) node.remove();
  if (!state.jobs.size) $("#empty").hidden = false;
  updateZipButton();
  updateRunAllButton();
}

/* ------------------------------------------------------------------- setup */
$("#btn-browse").onclick = () => $("#file").click();
$("#file").onchange = (e) => uploadFiles(e.target.files);
$("#btn-run-all").onclick = runAll;

const drop = $("#drop");
["dragenter", "dragover"].forEach((ev) =>
  drop.addEventListener(ev, (e) => {
    e.preventDefault();
    drop.classList.add("hover");
  })
);
["dragleave", "drop"].forEach((ev) =>
  drop.addEventListener(ev, (e) => {
    e.preventDefault();
    drop.classList.remove("hover");
  })
);
drop.addEventListener("drop", (e) => {
  if (e.dataTransfer && e.dataTransfer.files) uploadFiles(e.dataTransfer.files);
});

document.addEventListener("paste", (e) => {
  if (!e.clipboardData) return;
  const files = Array.from(e.clipboardData.files || []);
  if (files.length) uploadFiles(files);
});

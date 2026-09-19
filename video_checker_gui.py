# -*- coding: utf-8 -*-
"""视频完整性检测工具 - 原生桌面版（Tkinter，无浏览器/无服务端）
打包: pyinstaller --onefile --noconsole --name VideoChecker video_checker_gui.py
"""
import os
import queue
import subprocess
import threading
import time
import tkinter as tk
from tkinter import ttk, filedialog, messagebox

from detector import (DEFAULT_EXTS, SKIP_DIRS, detect_file, get_duration,
                      human_size, sniff_container_ext,
                      list_drives, recycle_file)

# ---------------- 深色主题 ----------------
BG = "#14161a"
PANEL = "#1d2126"
PANEL2 = "#232830"
BORDER = "#33393f"
TEXT = "#e6e6e6"
MUTED = "#9aa4ae"
ACCENT = "#4f8cff"
RED = "#e5534b"
GREEN = "#3fb950"
YELLOW = "#d29922"

STATUS_TEXT = {"broken": "✖ 损坏(建议删除)", "warn": "⚠ 结构异常(可播放)", "ok": "✔ 完整"}
STATUS_COLOR = {"broken": RED, "warn": YELLOW, "ok": GREEN}


class VideoCheckerApp(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("视频完整性检测工具")
        self.geometry("1180x720")
        self.minsize(960, 600)
        self.configure(bg=BG)

        self.drives = []
        self.results = []          # 全部检测结果
        self.filter_mode = "all"
        self.scanning = False
        self.cancel_flag = False
        self.msg_queue = queue.Queue()
        self._scan_threads = 8

        self._setup_style()
        self._build_ui()
        self._load_drives()
        self.after(120, self._poll_queue)

    # ---------- 样式 ----------
    def _setup_style(self):
        style = ttk.Style(self)
        style.theme_use("clam")
        style.configure(".", background=BG, foreground=TEXT, fieldbackground=PANEL2,
                        bordercolor=BORDER, lightcolor=PANEL2, darkcolor=PANEL2,
                        font=("Microsoft YaHei UI", 9))
        style.configure("TFrame", background=BG)
        style.configure("Panel.TFrame", background=PANEL)
        style.configure("TLabel", background=BG, foreground=TEXT)
        style.configure("Muted.TLabel", background=BG, foreground=MUTED,
                        font=("Microsoft YaHei UI", 8))
        style.configure("Panel.TLabel", background=PANEL, foreground=TEXT)
        style.configure("TButton", background=ACCENT, foreground="#ffffff",
                        borderwidth=0, padding=(14, 6))
        style.map("TButton", background=[("active", "#3d78e0"), ("disabled", "#3a3f46")],
                  foreground=[("disabled", "#7a8088")])
        style.configure("Ghost.TButton", background=PANEL2, foreground=TEXT,
                        borderwidth=1, bordercolor=BORDER)
        style.map("Ghost.TButton", background=[("active", "#2c323a")])
        style.configure("Danger.TButton", background=RED, foreground="#ffffff")
        style.map("Danger.TButton", background=[("active", "#c7443d"), ("disabled", "#3a3f46")])
        style.configure("TCheckbutton", background=BG, foreground=TEXT)
        style.map("TCheckbutton", background=[("active", BG)])
        style.configure("TCombobox", fieldbackground=PANEL2, background=PANEL2,
                        foreground=TEXT, arrowcolor=TEXT)
        style.configure("Filter.TButton", background=PANEL2, foreground=MUTED)
        style.map("Filter.TButton",
                  background=[("active", "#2c323a"), ("selected", "#2c3a52")],
                  foreground=[("selected", TEXT)])
        style.configure("Horizontal.TProgressbar", background=ACCENT,
                        troughcolor="#2c323a", borderwidth=0)
        style.configure("Treeview", background=PANEL, foreground=TEXT,
                        fieldbackground=PANEL, rowheight=30, borderwidth=0)
        style.map("Treeview", background=[("selected", "#2c3a52")],
                  foreground=[("selected", TEXT)])
        style.configure("Treeview.Heading", background=PANEL2, foreground=MUTED,
                        relief="flat", padding=(6, 6))
        style.map("Treeview.Heading", background=[("active", PANEL2)])

    # ---------- 界面 ----------
    def _build_ui(self):
        pad = {"padx": 14, "pady": (8, 0)}

        # 位置选择
        frm_loc = ttk.Frame(self)
        frm_loc.pack(fill="x", **pad)
        ttk.Label(frm_loc, text="检测位置", font=("Microsoft YaHei UI", 9, "bold"),
                  foreground=MUTED).pack(anchor="w")
        row1 = ttk.Frame(frm_loc)
        row1.pack(fill="x", pady=4)
        self.drive_var = tk.StringVar()
        self.drive_box = ttk.Combobox(row1, textvariable=self.drive_var,
                                      state="readonly", width=52)
        self.drive_box.pack(side="left")
        ttk.Button(row1, text="选择文件夹…", style="Ghost.TButton",
                   command=self.pick_folder).pack(side="left", padx=(10, 0))
        self.path_var = tk.StringVar()
        self.path_entry = ttk.Entry(row1, textvariable=self.path_var, width=40)
        self.path_entry.pack(side="left", padx=(10, 0), fill="x", expand=True)
        ttk.Label(frm_loc, text="选磁盘或选文件夹，二选一；也可直接在右侧输入路径",
                  style="Muted.TLabel").pack(anchor="w")

        # 格式选择
        frm_ext = ttk.Frame(self)
        frm_ext.pack(fill="x", **pad)
        ttk.Label(frm_ext, text="视频格式", font=("Microsoft YaHei UI", 9, "bold"),
                  foreground=MUTED).pack(anchor="w")
        row_ext = ttk.Frame(frm_ext)
        row_ext.pack(fill="x", pady=4)
        self.ext_vars = {}
        for e in DEFAULT_EXTS:
            v = tk.BooleanVar(value=True)
            self.ext_vars[e] = v
            ttk.Checkbutton(row_ext, text=e, variable=v).pack(side="left", padx=(0, 6))
        ttk.Button(row_ext, text="全选", style="Ghost.TButton",
                   command=lambda: self._set_all_exts(True)).pack(side="left", padx=(8, 0))
        ttk.Button(row_ext, text="全不选", style="Ghost.TButton",
                   command=lambda: self._set_all_exts(False)).pack(side="left", padx=(4, 0))

        # 操作行
        frm_btn = ttk.Frame(self)
        frm_btn.pack(fill="x", **pad)
        ttk.Label(frm_btn, text="扫描线程", style="Muted.TLabel").pack(side="left")
        self.threads_var = tk.StringVar(value="8")
        self.spin_threads = ttk.Spinbox(frm_btn, from_=1, to=32, width=4,
                                        textvariable=self.threads_var)
        self.spin_threads.pack(side="left", padx=(6, 0))
        self.btn_scan = ttk.Button(frm_btn, text="开始扫描", command=self.start_scan)
        self.btn_scan.pack(side="left", padx=(10, 0))
        self.btn_cancel = ttk.Button(frm_btn, text="取消", style="Ghost.TButton",
                                     command=self.cancel_scan, state="disabled")
        self.btn_cancel.pack(side="left", padx=(8, 0))

        # 进度
        self.progress = ttk.Progressbar(self, mode="indeterminate", length=200)
        self.progress.pack(fill="x", **pad)
        self.progress.pack_forget()
        self.lbl_progress = ttk.Label(self, text="", style="Muted.TLabel")
        self.lbl_progress.pack(anchor="w", padx=14)

        # 统计 + 筛选
        frm_stat = ttk.Frame(self)
        frm_stat.pack(fill="x", **pad)
        self.lbl_stats = ttk.Label(frm_stat, text="")
        self.lbl_stats.pack(side="left")
        frm_filter = ttk.Frame(frm_stat)
        frm_filter.pack(side="right")
        self.filter_btns = {}
        for key, text in [("all", "全部"), ("broken", "损坏(建议删除)"),
                          ("warn", "结构异常(可播放)"), ("ok", "完整")]:
            b = ttk.Button(frm_filter, text=text, style="Filter.TButton",
                           command=lambda k=key: self.set_filter(k))
            b.pack(side="left", padx=(6, 0))
            self.filter_btns[key] = b

        # 结果表
        frm_tree = ttk.Frame(self)
        frm_tree.pack(fill="both", expand=True, padx=14, pady=8)
        cols = ("sel", "status", "name", "path", "size", "dur", "mtime", "reason")
        self.tree = ttk.Treeview(frm_tree, columns=cols, show="headings", selectmode="none")
        headers = [("sel", "选", 40, "center"), ("status", "状态", 105, "center"),
                   ("name", "文件名", 200, "w"), ("path", "完整路径", 330, "w"),
                   ("size", "大小", 80, "e"), ("dur", "时长", 80, "center"),
                   ("mtime", "修改时间", 110, "center"), ("reason", "检测结果", 175, "w")]
        for cid, text, width, anchor in headers:
            self.tree.heading(cid, text=text)
            self.tree.column(cid, width=width, anchor=anchor,
                             stretch=(cid in ("name", "path", "reason")), minwidth=36)
        vsb = ttk.Scrollbar(frm_tree, orient="vertical", command=self.tree.yview)
        hsb = ttk.Scrollbar(frm_tree, orient="horizontal", command=self.tree.xview)
        self.tree.configure(yscrollcommand=vsb.set, xscrollcommand=hsb.set)
        self.tree.grid(row=0, column=0, sticky="nsew")
        vsb.grid(row=0, column=1, sticky="ns")
        hsb.grid(row=1, column=0, sticky="ew")
        frm_tree.rowconfigure(0, weight=1)
        frm_tree.columnconfigure(0, weight=1)
        self.tree.tag_configure("broken", foreground=RED)
        self.tree.tag_configure("warn", foreground=YELLOW)
        self.tree.tag_configure("ok", foreground=MUTED)
        self.tree.bind("<Button-1>", self._on_tree_click)
        self.tree.bind("<B1-Motion>", self._on_tree_drag)
        self.tree.bind("<ButtonRelease-1>", self._on_tree_release)
        self.tree.bind("<Motion>", self._on_tree_hover)
        self.tree.bind("<Double-1>", self._on_tree_double)
        self.tree.bind("<Button-3>", self._on_tree_right_click)
        # Windows 高 DPI 下默认滚轮绑定滚不到末行，改用自定义处理；支持 Home/End/PageUp/PageDown
        self.tree.bind("<MouseWheel>", self._on_mousewheel)
        self.tree.bind("<Home>", lambda e: (self.tree.yview_moveto(0), "break")[1])
        self.tree.bind("<End>", lambda e: (self.tree.yview_moveto(1.0), "break")[1])
        self.tree.bind("<Prior>", lambda e: (self.tree.yview_scroll(-1, "pages"), "break")[1])
        self.tree.bind("<Next>", lambda e: (self.tree.yview_scroll(1, "pages"), "break")[1])
        self._resizing = None  # [列id, 起始x, 起始宽]
        self.ctx_menu = tk.Menu(self, tearoff=0, bg=PANEL2, fg=TEXT,
                                activebackground="#2c3a52", activeforeground=TEXT)
        self.ctx_menu.add_command(label="打开所在文件夹", command=self._ctx_open_folder)
        self.ctx_menu.add_command(label="在资源管理器中定位文件", command=self._ctx_locate_file)
        self.ctx_menu.add_command(label="复制完整路径", command=self._ctx_copy_path)
        self._ctx_path = None

        # 底部操作
        frm_act = ttk.Frame(self)
        frm_act.pack(fill="x", padx=14, pady=(0, 12))
        ttk.Button(frm_act, text="仅勾选\"损坏(建议删除)\"", style="Ghost.TButton",
                   command=self.select_broken).pack(side="left")
        ttk.Button(frm_act, text="清除勾选", style="Ghost.TButton",
                   command=self.clear_sel).pack(side="left", padx=(6, 0))
        self.btn_fix = ttk.Button(frm_act, text="修正标错的扩展名(0)",
                                  style="Ghost.TButton", command=self.fix_extensions,
                                  state="disabled")
        self.btn_fix.pack(side="left", padx=(14, 0))
        self.btn_del = ttk.Button(frm_act, text="删除所选（移入回收站）",
                                  style="Danger.TButton", command=self.delete_selected,
                                  state="disabled")
        self.btn_del.pack(side="left", padx=(14, 0))
        self.lbl_sel = ttk.Label(frm_act, text="", style="Muted.TLabel")
        self.lbl_sel.pack(side="left", padx=(10, 0))
        ttk.Label(frm_act, text="双击行或右键 → 打开所在文件夹", style="Muted.TLabel").pack(side="right")

    # ---------- 磁盘 ----------
    def _load_drives(self):
        self.drives = list_drives()
        values = [f"{d['letter']}  {d['type']}  剩余 {d['freeText']} / 共 {d['totalText']}"
                  for d in self.drives]
        self.drive_box["values"] = values
        if values:
            self.drive_box.current(0)

    def pick_folder(self):
        path = filedialog.askdirectory(title="选择要扫描的文件夹")
        if path:
            self.path_var.set(os.path.normpath(path))

    def _set_all_exts(self, val):
        for v in self.ext_vars.values():
            v.set(val)

    def _target(self):
        custom = self.path_var.get().strip()
        if custom:
            return custom
        idx = self.drive_box.current()
        if idx is not None and idx >= 0:
            return self.drives[idx]["letter"]
        return ""

    # ---------- 扫描 ----------
    def start_scan(self):
        target = self._target()
        if not target or not os.path.isdir(target):
            messagebox.showwarning("提示", "请先选择磁盘或有效的文件夹路径")
            return
        exts = {e for e, v in self.ext_vars.items() if v.get()}
        if not exts:
            messagebox.showwarning("提示", "请至少勾选一种视频格式")
            return
        if target.lower().endswith(":") or len(target) == 2 and target[1] == ":":
            target += "\\"
        self.results.clear()
        self.scanning = True
        self.cancel_flag = False
        self._scan_t0 = time.time()
        self._scan_total = 0
        self.btn_scan.config(state="disabled")
        self.btn_cancel.config(state="normal")
        self.progress.pack(fill="x", padx=14, pady=(8, 0))
        self.progress.configure(mode="indeterminate", value=0)
        self.progress.start(60)
        exts_list = ["." + e for e in exts]
        # 线程数: 1~32, 非法输入回退默认 8
        try:
            nthreads = int(self.threads_var.get())
        except ValueError:
            nthreads = 8
        nthreads = max(1, min(nthreads, 32))
        self._scan_threads = nthreads
        threading.Thread(target=self._scan_worker, args=(target, exts_list, nthreads),
                         daemon=True).start()

    def _scan_worker(self, root, exts, nthreads=8):
        """两阶段: 统计总数 → 生产者遍历文件队列, N 个线程并行检测。"""
        try:
            # 阶段 1: 只遍历文件名快速统计总数(比检测快得多), 用于计算进度百分比
            self.msg_queue.put(("phase", "count"))
            total = 0
            for dirpath, dirnames, filenames in os.walk(root, topdown=True,
                                                        onerror=lambda e: None):
                if self.cancel_flag:
                    break
                dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
                total += len(filenames)
                if total and total % 20000 < len(filenames):
                    self.msg_queue.put(("count_progress", total))
            self.msg_queue.put(("count_total", total))
            if self.cancel_flag:
                self.msg_queue.put(("done", None))
                return
            # 阶段 2: 生产者遍历目录, 视频文件入队; N 个工作线程并行检测
            self.msg_queue.put(("phase", "detect"))
            jobs = queue.Queue()
            counters = {"scanned": 0, "matched": 0}
            clock = threading.Lock()

            def bump(key, n=1):
                with clock:
                    counters[key] += n

            workers = []
            for _ in range(max(1, nthreads)):
                t = threading.Thread(target=self._detect_worker,
                                     args=(jobs, counters, clock), daemon=True)
                t.start()
                workers.append(t)
            for dirpath, dirnames, filenames in os.walk(root, topdown=True,
                                                        onerror=lambda e: None):
                if self.cancel_flag:
                    break
                dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
                for name in filenames:
                    if self.cancel_flag:
                        break
                    if os.path.splitext(name)[1].lower() not in exts:
                        bump("scanned")     # 非视频零成本, 直接计入已处理
                        continue
                    full = os.path.join(dirpath, name)
                    try:
                        st = os.stat(full)
                    except OSError:
                        bump("scanned")
                        continue
                    jobs.put((full, name, dirpath, st))
            for _ in workers:
                jobs.put(None)              # 哨兵: 通知各线程退出
            for t in workers:
                t.join()
            with clock:
                scanned, matched = counters["scanned"], counters["matched"]
            self.msg_queue.put(("progress", scanned, matched, ""))
        except Exception as e:
            self.msg_queue.put(("done", f"扫描出错: {e}"))
            return
        self.msg_queue.put(("done", None))

    def _detect_worker(self, jobs, counters, clock):
        """检测线程: 从队列取视频文件, 检测 + 时长 + 嗅探, 结果推给主线程渲染。"""
        local_n = 0

        def snapshot():
            with clock:
                return counters["scanned"], counters["matched"]

        while True:
            job = jobs.get()
            if job is None or self.cancel_flag:
                break
            full, name, dirpath, st = job
            try:
                status, reason = detect_file(full)
                with clock:
                    counters["matched"] += 1
                dur_s = get_duration(full)
                rec = {
                    "path": full, "name": name, "dir": dirpath,
                    "size": st.st_size, "sizeText": human_size(st.st_size),
                    "dur": self._fmt_dur(dur_s),
                    "mtime": time.strftime("%Y-%m-%d %H:%M",
                                           time.localtime(st.st_mtime)),
                    "status": status, "reason": reason, "checked": False,
                }
                cur_ext = os.path.splitext(name)[1].lower().lstrip(".")
                real_ext = sniff_container_ext(full)
                if real_ext and real_ext != cur_ext:
                    rec["fixExt"] = real_ext
                self.msg_queue.put(("result", rec))
            except Exception:
                pass                    # 单文件异常不拖垮整个线程
            finally:
                with clock:
                    counters["scanned"] += 1
            local_n += 1
            if local_n % 10 == 0:       # 节流: 每线程每 10 个文件报一次进度
                s, m = snapshot()
                self.msg_queue.put(("progress", s, m, dirpath))
        if local_n % 10:
            s, m = snapshot()
            self.msg_queue.put(("progress", s, m, dirpath))

    def cancel_scan(self):
        self.cancel_flag = True
        self.btn_cancel.config(state="disabled")

    # ---------- 队列轮询 ----------
    def _poll_queue(self):
        try:
            while True:
                msg = self.msg_queue.get_nowait()
                kind = msg[0]
                if kind == "result":
                    self.results.append(msg[1])
                    if self._match_filter(msg[1]):
                        self._append_row(msg[1])
                    self._update_stats()
                elif kind == "phase":
                    if msg[1] == "count":
                        self.lbl_progress.config(text="正在统计文件总数…")
                elif kind == "count_progress":
                    self.lbl_progress.config(
                        text=f"正在统计文件总数… 已发现 {msg[1]:,} 个文件")
                elif kind == "count_total":
                    self._scan_total = msg[1]
                    if msg[1] > 0:
                        self.progress.stop()
                        self.progress.configure(mode="determinate",
                                                maximum=msg[1], value=0)
                elif kind == "progress":
                    _, scanned, matched, cur = msg
                    elapsed = max(time.time() - self._scan_t0, 0.001)
                    speed = scanned / elapsed
                    mm, ss = divmod(int(elapsed), 60)
                    cur_text = f"    当前: {cur[:70]}" if cur else ""
                    th = f"    {self._scan_threads} 线程"
                    if self._scan_total > 0:
                        pct = min(scanned / self._scan_total * 100, 100.0)
                        self.progress.configure(value=scanned)
                        self.lbl_progress.config(
                            text=f"进度 {scanned:,}/{self._scan_total:,}（{pct:.1f}%）"
                                 f"    已用 {mm:02d}:{ss:02d}    速度 {speed:,.0f} 文件/秒"
                                 f"    发现 {matched} 个视频{th}{cur_text}")
                    else:
                        self.lbl_progress.config(
                            text=f"已检查 {scanned:,} 个文件    已用 {mm:02d}:{ss:02d}"
                                 f"    速度 {speed:,.0f} 文件/秒    发现 {matched} 个视频{th}{cur_text}")
                elif kind == "done":
                    self._scan_finished(msg[1])
                elif kind == "del_row":
                    self._after_one_deleted(msg[1])
                elif kind == "del_progress":
                    self.lbl_sel.config(text=f"正在删除 {msg[1]}/{msg[2]} …")
                elif kind == "del_done":
                    self._finish_delete(msg[1], msg[2])
        except queue.Empty:
            pass
        self.after(120, self._poll_queue)

    def _scan_finished(self, error):
        self.scanning = False
        self.progress.stop()
        self.progress.pack_forget()
        self.btn_scan.config(state="normal")
        self.btn_cancel.config(state="disabled")
        if error:
            messagebox.showerror("错误", error)
        self.lbl_progress.config(
            text=f"扫描完成，共 {len(self.results)} 个视频" +
                 ("（已取消）" if self.cancel_flag else ""))
        self._render()

    # ---------- 结果表 ----------
    def _match_filter(self, r):
        return self.filter_mode == "all" or r["status"] == self.filter_mode

    def _append_row(self, r):
        iid = self.tree.insert("", "end", values=(
            "☑" if r["checked"] else "☐", STATUS_TEXT[r["status"]], r["name"],
            r["path"], r["sizeText"], r.get("dur", "--"), r["mtime"], r["reason"]),
            tags=(r["status"],))
        r["_iid"] = iid

    @staticmethod
    def _fmt_dur(sec):
        if not sec or sec <= 0:
            return "--"
        sec = int(round(sec))
        h, m, s = sec // 3600, sec % 3600 // 60, sec % 60
        return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"

    def _render(self):
        self.tree.delete(*self.tree.get_children())
        for r in self.results:
            if self._match_filter(r):
                self._append_row(r)
        self._update_stats()

    def _update_stats(self):
        n = len(self.results)
        b = sum(1 for r in self.results if r["status"] == "broken")
        w = sum(1 for r in self.results if r["status"] == "warn")
        o = sum(1 for r in self.results if r["status"] == "ok")
        self.lbl_stats.config(text=(
            f"共 {n} 个视频    "
            f"损坏(建议删除) {b}    结构异常(可播放) {w}    完整 {o}"))
        for key, b_ in self.filter_btns.items():
            b_.state(["!selected"])
        self.filter_btns[self.filter_mode].state(["selected"])
        nfix = sum(1 for r in self.results if r.get("fixExt"))
        self.btn_fix.config(text=f"修正标错的扩展名({nfix})",
                            state="normal" if nfix else "disabled")

    def set_filter(self, mode):
        self.filter_mode = mode
        self._render()

    # ---------- 滚轮 / 列宽拖拽 ----------
    def _on_mousewheel(self, event):
        delta = event.delta
        if abs(delta) >= 120:
            self.tree.yview_scroll(-1 * int(delta / 120), "units")
        else:  # 精密触摸板的小增量
            self.tree.yview_scroll(int(-1 * delta) // 40, "units")
        return "break"

    def _sep_at(self, x):
        """返回鼠标位置(±5px)命中的列边界；以当前横向滚动偏移换算内容坐标。"""
        cols = self.tree["columns"]
        widths = [self.tree.column(c, "width") for c in cols]
        total = sum(widths)
        off = self.tree.xview()[0] * max(total, 1)
        acc = 0
        for i, w in enumerate(widths):
            acc += w
            if i < len(cols) - 1 and abs(x - (acc - off)) <= 5:
                return cols[i]
        return None

    def _on_tree_hover(self, event):
        if self._resizing:
            return
        self.tree.configure(cursor="sb_h_double_arrow" if self._sep_at(event.x) else "")

    def _on_tree_drag(self, event):
        if not self._resizing:
            return
        cid, x0, w0 = self._resizing
        self.tree.column(cid, width=max(36, w0 + event.x - x0))
        return "break"

    def _on_tree_release(self, event):
        self._resizing = None

    def _on_tree_click(self, event):
        cid = self._sep_at(event.x)
        if cid:  # 点在列边界上 → 开始拖拽调宽，不当作勾选点击
            self._resizing = [cid, event.x, self.tree.column(cid, "width")]
            return "break"
        if self.tree.identify_column(event.x) == "#1":
            iid = self.tree.identify_row(event.y)
            if iid:
                self._toggle_row(iid)

    def _toggle_row(self, iid):
        for r in self.results:
            if r.get("_iid") == iid:
                r["checked"] = not r["checked"]
                vals = list(self.tree.item(iid, "values"))
                vals[0] = "☑" if r["checked"] else "☐"
                self.tree.item(iid, values=vals)
                break
        self._update_sel()

    def _on_tree_double(self, event):
        iid = self.tree.identify_row(event.y)
        if iid:
            for r in self.results:
                if r.get("_iid") == iid:
                    self._open_location(r["path"])
                    break

    def _update_sel(self):
        n = sum(1 for r in self.results if r["checked"])
        self.lbl_sel.config(text=f"已勾选 {n} 项" if n else "")
        self.btn_del.config(state="normal" if n else "disabled")

    def select_broken(self):
        for r in self.results:
            r["checked"] = (r["status"] == "broken")
        self._render()
        self._update_sel()

    def clear_sel(self):
        for r in self.results:
            r["checked"] = False
        self._render()
        self._update_sel()

    # ---------- 修正扩展名 / 删除 / 打开 ----------
    def fix_extensions(self):
        cands = [r for r in self.results if r.get("fixExt")]
        if not cands:
            return
        examples = "\n".join(
            f'{r["name"]}  →  .{r["fixExt"]}' for r in cands[:10])
        if not messagebox.askyesno(
                "确认修正扩展名",
                f"检测到 {len(cands)} 个文件的扩展名与实际内容不符"
                f"（按文件内容识别真实容器，可正常播放）。\n\n"
                + examples + ("\n…" if len(cands) > 10 else "") +
                "\n\n将直接重命名文件（仅改扩展名，不改动内容）。确认执行？"):
            return
        ok_n = fail = 0
        for r in cands:
            old_path = r["path"]
            stem = old_path[:old_path.rfind(".")] if "." in os.path.basename(old_path) else old_path
            new_path = stem + "." + r["fixExt"]
            # 目标名已存在时追加序号
            k = 1
            while os.path.exists(new_path) and new_path.lower() != old_path.lower():
                new_path = f"{stem} ({k}).{r['fixExt']}"
                k += 1
            try:
                os.rename(old_path, new_path)
            except OSError:
                fail += 1
                continue
            ok_n += 1
            r["path"] = new_path
            r["name"] = os.path.basename(new_path)
            r["dir"] = os.path.dirname(new_path)
            r["reason"] = "扩展名已修正为 ." + r["fixExt"] + "；" + r["reason"]
            r.pop("fixExt", None)
            if r.get("_iid"):
                try:
                    vals = list(self.tree.item(r["_iid"], "values"))
                    vals[2], vals[3], vals[7] = r["name"], r["path"], r["reason"]
                    self.tree.item(r["_iid"], values=vals)
                except tk.TclError:
                    pass
        self._update_stats()
        if fail:
            messagebox.showwarning("部分失败", f"已修正 {ok_n} 个，失败 {fail} 个（文件可能被占用）")
        else:
            messagebox.showinfo("完成", f"已修正 {ok_n} 个文件的扩展名")

    def delete_selected(self):
        sel = [r for r in self.results if r["checked"]]
        if not sel:
            return
        if not messagebox.askyesno(
                "确认删除",
                f"将把 {len(sel)} 个文件移入系统回收站（不会直接销毁，可随时还原）。\n\n"
                + "\n".join(r["path"] for r in sel[:10])
                + ("\n…" if len(sel) > 10 else "") + "\n\n确认执行？"):
            return
        self.btn_del.config(state="disabled")
        self.lbl_sel.config(text=f"正在删除 0/{len(sel)} …")

        def work():
            ok_n, fails = 0, []
            total = len(sel)
            for i, r in enumerate(sel, 1):
                ok, msg = recycle_file(r["path"])
                if ok:
                    ok_n += 1
                    self.msg_queue.put(("del_row", r))
                else:
                    fails.append((r["path"], msg))
                if i % 5 == 0 or i == total:
                    self.msg_queue.put(("del_progress", i, total))
            self.msg_queue.put(("del_done", ok_n, fails))

        threading.Thread(target=work, daemon=True).start()

    def _after_one_deleted(self, r):
        if r.get("_iid"):
            try:
                self.tree.delete(r["_iid"])
            except tk.TclError:
                pass
        if r in self.results:
            self.results.remove(r)

    def _finish_delete(self, ok_n, fails):
        self._update_stats()
        self._update_sel()
        self.btn_del.config(state="normal")
        self.lbl_sel.config(text="")
        if fails:
            # 失败清单写入临时目录, 供用户查看全部失败文件及原因
            report = os.path.join(os.environ.get("TEMP", os.getcwd()),
                                  "VideoChecker_删除失败清单.txt")
            try:
                with open(report, "w", encoding="utf-8") as f:
                    f.write(f"删除时间: {time.strftime('%Y-%m-%d %H:%M:%S')}\n"
                            f"成功 {ok_n} 个，失败 {len(fails)} 个\n\n")
                    for p, why in fails:
                        f.write(f"原因: {why}\n路径: {p}\n\n")
            except OSError:
                report = None
            detail = "\n".join(f"[{w}] {p}" for p, w in fails[:8])
            more = "\n…" if len(fails) > 8 else ""
            extra = f"\n\n完整失败清单已保存: {report}" if report else ""
            messagebox.showwarning(
                "部分失败",
                f"已移入回收站 {ok_n} 个，失败 {len(fails)} 个。\n\n"
                f"失败原因（前 8 项）：\n{detail}{more}{extra}")
        else:
            messagebox.showinfo("完成", f"已将 {ok_n} 个文件移入回收站")

    @staticmethod
    def _open_location(path):
        try:
            if os.path.isdir(path):
                subprocess.Popen(["explorer", path])
            else:
                subprocess.Popen(["explorer", "/select,", os.path.normpath(path)])
        except Exception:
            pass

    @staticmethod
    def _open_folder(path):
        try:
            folder = path if os.path.isdir(path) else os.path.dirname(path)
            if folder and os.path.isdir(folder):
                subprocess.Popen(["explorer", folder])
        except Exception:
            pass

    # ---------- 右键菜单 ----------
    def _on_tree_right_click(self, event):
        iid = self.tree.identify_row(event.y)
        if not iid:
            return
        for r in self.results:
            if r.get("_iid") == iid:
                self._ctx_path = r["path"]
                self.ctx_menu.tk_popup(event.x_root, event.y_root)
                break
        return "break"

    def _ctx_open_folder(self):
        if self._ctx_path:
            self._open_folder(self._ctx_path)

    def _ctx_locate_file(self):
        if self._ctx_path:
            self._open_location(self._ctx_path)

    def _ctx_copy_path(self):
        if self._ctx_path:
            self.clipboard_clear()
            self.clipboard_append(os.path.normpath(self._ctx_path))


def main():
    app = VideoCheckerApp()
    app.mainloop()


if __name__ == "__main__":
    main()

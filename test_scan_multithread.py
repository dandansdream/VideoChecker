# -*- coding: utf-8 -*-
"""多线程扫描端到端验证:
构造样本目录 → 用 GUI 的多线程扫描逻辑(4 线程)扫描 → 校验结果与进度收尾。
"""
import os
import sys
import time
import shutil
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from test_detection import make_mp4, make_mkv, make_avi  # noqa: E402

from video_checker_gui import VideoCheckerApp  # noqa: E402


def build_tree(root):
    """3 个子目录, 每个 6 个视频(部分截断) + 3 个无关文件。"""
    expect = 0
    for i in range(3):
        sub = os.path.join(root, f"dir{i}")
        os.makedirs(sub, exist_ok=True)
        for j in range(6):
            p = os.path.join(sub, f"v{i}{j}.mp4" if j % 2 == 0 else f"v{i}{j}.mkv")
            if j % 2 == 0:
                # j==0: 无 moov → broken; j==4: 尾部截断(有moov, 可播放) → warn; 其余完整
                make_mp4(p, truncated=(j == 4), with_moov=(j != 0))
            else:
                make_mkv(p, truncated=(j == 4))
            expect += 1
        for j in range(3):
            with open(os.path.join(sub, f"note{j}.txt"), "w") as f:
                f.write("x" * 100)
    return expect


def main():
    tmp = tempfile.mkdtemp(prefix="vc_mt_")
    expect = build_tree(tmp)
    app = VideoCheckerApp()
    app.withdraw()
    app.path_var.set(tmp)
    app.threads_var.set("4")
    app._scan_total = 0

    app.start_scan()
    deadline = time.time() + 60
    results_seen = 0
    while time.time() < deadline:
        app._poll_queue()   # 手动驱动队列(内部 after 已注册, 重复调用无碍)
        app.update()
        if not app.scanning:
            break
        results_seen = len(app.results)
    elapsed = time.time() - app._scan_t0

    ok = True
    if len(app.results) != expect:
        print(f"[FAIL] 结果数 {len(app.results)} != 期望 {expect}")
        ok = False
    statuses = [r["status"] for r in app.results]
    if "broken" not in statuses:
        print("[FAIL] 截断样本未被识别为 broken")
        ok = False
    if app.scanning:
        print("[FAIL] 扫描未结束(可能死锁)")
        ok = False
    if app._scan_threads != 4:
        print(f"[FAIL] 线程数记录 {app._scan_threads} != 4")
        ok = False
    n_threads = sum(1 for r in app.results if r["status"] == "ok")
    print(f"[INFO] {expect} 个视频, 4 线程, 耗时 {elapsed:.2f}s, "
          f"完整 {n_threads}, 异常 {statuses.count('warn')}, 损坏 {statuses.count('broken')}")

    # 非法线程数回退默认 8
    app.threads_var.set("abc")
    try:
        nthreads = int(app.threads_var.get())
    except ValueError:
        nthreads = 8
    nthreads = max(1, min(nthreads, 32))
    if nthreads != 8:
        print(f"[FAIL] 非法线程数未回退 8: {nthreads}")
        ok = False

    # 越界裁剪: 99 -> 32
    app.threads_var.set("99")
    nthreads = max(1, min(int(app.threads_var.get()), 32))
    if nthreads != 32:
        print(f"[FAIL] 99 未裁剪到 32: {nthreads}")
        ok = False

    shutil.rmtree(tmp, ignore_errors=True)
    app.destroy()
    if ok:
        print("多线程扫描端到端验证: 全部通过")
        sys.exit(0)
    sys.exit(1)


if __name__ == "__main__":
    main()

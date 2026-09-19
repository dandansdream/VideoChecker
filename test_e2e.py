# -*- coding: utf-8 -*-
"""端到端测试：对已启动的 VideoChecker 服务走完整流程"""
import json, os, struct, sys, time, urllib.request, tempfile, shutil

BASE = "http://127.0.0.1:" + os.environ.get("VIDEOCHECKER_PORT", "8765")
_opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))  # 绕过系统代理直连

def api(path, body=None):
    if body is None:
        req = urllib.request.Request(BASE + path)
    else:
        req = urllib.request.Request(BASE + path, data=json.dumps(body).encode(),
                                     headers={"Content-Type": "application/json"})
    with _opener.open(req, timeout=30) as r:
        return json.loads(r.read().decode())

def make_mp4(path, truncated=False):
    ftyp = struct.pack(">I", 24) + b"ftyp" + b"isom" + struct.pack(">I", 0) + b"isom" + b"mp41"
    payload = b"\x00" * 5000
    moov = struct.pack(">I", 8) + b"moov"
    if truncated:
        with open(path, "wb") as f:
            f.write(ftyp + moov + struct.pack(">I", 8 + len(payload) + 99999) + b"mdat" + payload)
    else:
        with open(path, "wb") as f:
            f.write(ftyp + moov + struct.pack(">I", 8 + len(payload)) + b"mdat" + payload)

# 1. 构造测试目录
tdir = tempfile.mkdtemp(prefix="vcheck_e2e_")
make_mp4(os.path.join(tdir, "good.mp4"))
make_mp4(os.path.join(tdir, "broken.mp4"), truncated=True)
with open(os.path.join(tdir, "empty.mkv"), "wb") as f:
    pass
with open(os.path.join(tdir, "notvideo.txt"), "w") as f:
    f.write("hello")
print("test dir:", tdir)

# 2. 磁盘列表
r = api("/api/drives")
print("drives:", [(d["letter"], d["type"]) for d in r["drives"]])

# 3. 启动扫描（指定测试目录）
r = api("/api/scan", {"target": tdir, "exts": ["mp4", "mkv"]})
print("scan start:", r)

# 4. 轮询直到完成
for _ in range(60):
    s = api("/api/status?last=0")
    if not s["running"]:
        break
    time.sleep(0.3)
print("scannedFiles:", s["scannedFiles"], "matched:", s["matchedFiles"])
for f in s["newResults"]:
    print(f"  [{f['status']}] {f['name']}: {f['reason']}")

# 5. 校验检测结果
got = {f["name"]: f["status"] for f in s["newResults"]}
assert got.get("good.mp4") == "ok", f"good.mp4 应为 ok, 实际 {got.get('good.mp4')}"
assert got.get("broken.mp4") == "broken", f"broken.mp4 应为 broken, 实际 {got.get('broken.mp4')}"
assert got.get("empty.mkv") == "broken", f"empty.mkv 应为 broken, 实际 {got.get('empty.mkv')}"
assert "notvideo.txt" not in got, "txt 不应被检测"
print("检测断言全部通过")

# 6. 删除 broken 文件（回收站）
r = api("/api/delete", {"paths": [os.path.join(tdir, "broken.mp4")]})
res = r["results"][0]
print("delete:", res)
assert res["ok"], f"删除失败: {res['message']}"
assert not os.path.exists(os.path.join(tdir, "broken.mp4")), "文件应已从原位置移除"
print("回收站删除验证通过（文件已从原位置移除，可在回收站还原）")

# 7. 退出
api("/api/exit", {})
print("exit ok")
shutil.rmtree(tdir, ignore_errors=True)
print("\n=== 端到端测试全部通过 ===")

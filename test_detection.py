# -*- coding: utf-8 -*-
"""构造正/反样本验证检测逻辑"""
import os, struct, sys, shutil, tempfile
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from detector import detect_file, sniff_container_ext

def make_mp4(path, truncated=False, with_moov=True):
    ftyp = struct.pack(">I", 24) + b"ftyp" + b"isom" + struct.pack(">I", 0) + b"isom" + b"mp41"
    moov = struct.pack(">I", 8) + b"moov"
    payload = b"\x00" * 5000
    mdat_size = 8 + len(payload)
    if truncated:
        mdat_size = 8 + len(payload) + 99999  # 声明得比实际大 → 截断
    mdat = struct.pack(">I", mdat_size if truncated else 8 + len(payload)) + b"mdat" + payload
    # 正常路径: mdat 精确到 EOF
    mdat_ok = struct.pack(">I", 8 + len(payload)) + b"mdat" + payload
    data = ftyp + mdat if truncated else ftyp + (moov if with_moov else b"") + mdat_ok
    if truncated and with_moov:
        data = ftyp + moov + mdat
    with open(path, "wb") as f:
        f.write(data)

def make_mkv(path, truncated=False):
    ebml = b"\x1a\x45\xdf\xa3" + b"\x01\x00\x00\x00\x00\x00\x00\x1c" + b"\x42\x82\x48\x01" + b"\x00" * 24  # 载荷正好 28 字节
    # Segment, size = 0x154 (340) declared
    seg_size = 340
    body = b"\x42\x82" + b"\x88" + b"testtest"  # 随便一点子元素
    fill = b"\xec" + bytes([0x81, 0x00]) * 100  # Void 元素填充
    seg_body = body + fill
    if truncated:
        seg = b"\x18\x53\x80\x67" + b"\x20\x00\x00\x0f" + seg_body  # 声明 0x2000000f 太大? 用超大size
        seg = b"\x18\x53\x80\x67" + bytes([0x01]) + b"\xff" * 7 + seg_body  # 未知大小
        # 截断：在 segment 中声明一个超大子元素
        seg = b"\x18\x53\x80\x67" + b"\x01\x00\x00\x00\x00\x00\x00\x02" + seg_body + b"\x1f\x43\xb6\x75" + b"\x7f\xff\xff\xff\xff\xff\xff" + b"xx"
    else:
        seg = b"\x18\x53\x80\x67" + bytes([0x20,0,0]) + struct.pack(">H", len(seg_body))[0:2]
        # 简化: 用 vint 编码 size
        n = len(seg_body)
        size_v = (0x80 | (n if n < 127 else 126)).to_bytes(1, "big")
        seg = b"\x18\x53\x80\x67" + size_v + seg_body
        if n >= 127:
            # 2字节 vint: 0x4000 | n
            size_v = ((0x40 << 8) | n).to_bytes(2, "big")
            seg = b"\x18\x53\x80\x67" + size_v + seg_body
    with open(path, "wb") as f:
        f.write(ebml + seg)

def make_avi(path, truncated=False, with_idx=True, form=b"AVI ", leading_junk=0):
    payload = b"\x00" * 4000
    idx = b"idx1" + struct.pack("<I", 16) + b"\x00" * 16
    movi = b"LIST" + struct.pack("<I", 4 + len(payload)) + b"movi" + payload
    body = b"hdrl" + struct.pack("<I", 56) + b"\x00" * 56 + movi + (idx if with_idx else b"")
    if truncated:
        body = body[:-100]  # 削掉尾部 → RIFF 声明大于实际
    data = b"RIFF" + struct.pack("<I", 4 + len(body)) + form + body
    if truncated:
        data = b"RIFF" + struct.pack("<I", 4 + len(body) + 100) + form + body
    if leading_junk:
        data = b"\xAB" * leading_junk + data
    with open(path, "wb") as f:
        f.write(data)

def make_flv(path, truncated=False):
    hdr = b"FLV\x01\x05\x00\x00\x00\x09" + b"\x00\x00\x00\x00"
    tag_body = b"\x00" * 500
    tag = b"\x09" + len(tag_body).to_bytes(3, "big") + b"\x00\x00\x00\x00\x00\x00\x00" + tag_body + struct.pack(">I", 11 + len(tag_body))
    data = hdr + tag
    if truncated:
        data = hdr + tag[:400]  # 截断但保留 >100 字节
    with open(path, "wb") as f:
        f.write(data)

d = tempfile.mkdtemp(prefix="vcheck_test_")
cases = []
p = os.path.join(d, "good.mp4"); make_mp4(p); cases.append(("MP4 完整", p, "ok"))
p = os.path.join(d, "trunc.mp4"); make_mp4(p, truncated=True); cases.append(("MP4 尾部截断(有moov,可播放)", p, "warn"))
p = os.path.join(d, "nomoov.mp4"); make_mp4(p, with_moov=False); cases.append(("MP4 无moov", p, "broken"))
p = os.path.join(d, "garbage.mp4")
with open(p, "wb") as f:  # 结构完整 + 尾部垃圾
    ftyp = struct.pack(">I", 24) + b"ftyp" + b"isom" + struct.pack(">I", 0) + b"isom" + b"mp41"
    payload = b"\x00" * 5000
    f.write(ftyp + struct.pack(">I", 8) + b"moov" + struct.pack(">I", 8 + len(payload)) + b"mdat" + payload + b"JUNK" * 25)
cases.append(("MP4 尾部垃圾(有moov,可播放)", p, "warn"))
p = os.path.join(d, "wide_first.mp4")
with open(p, "wb") as f:  # wide/free box 开头(非 ftyp 起始,合法)
    ftyp = struct.pack(">I", 24) + b"ftyp" + b"isom" + struct.pack(">I", 0) + b"isom" + b"mp41"
    payload = b"\x00" * 3000
    f.write(struct.pack(">I", 12) + b"wide" + b"\x00" * 4 + ftyp + struct.pack(">I", 8) + b"moov" + struct.pack(">I", 8 + len(payload)) + b"mdat" + payload)
cases.append(("MP4 wide 开头(合法)", p, "ok"))
p = os.path.join(d, "good.mkv"); make_mkv(p); cases.append(("MKV 完整", p, "ok"))
p = os.path.join(d, "trunc.mkv"); make_mkv(p, truncated=True); cases.append(("MKV 尾部截断(可播放)", p, "warn"))
p = os.path.join(d, "trail.mkv")
make_mkv(p)
with open(p, "ab") as f:
    f.write(b"\x00")  # 末尾多 1 个残余字节(真实案例: 004.mkv)
cases.append(("MKV 尾部残余字节(可播放)", p, "warn"))
p = os.path.join(d, "good.avi"); make_avi(p); cases.append(("AVI 完整", p, "ok"))
p = os.path.join(d, "noidx.avi"); make_avi(p, with_idx=False); cases.append(("AVI 无索引(可播放)", p, "warn"))
p = os.path.join(d, "trunc.avi"); make_avi(p, truncated=True); cases.append(("AVI 尾部截断(可播放)", p, "warn"))
p = os.path.join(d, "avix.avi"); make_avi(p, form=b"AVIX"); cases.append(("AVI OpenDML/AVIX 表单(正常)", p, "ok"))
p = os.path.join(d, "divx.avi"); make_avi(p, form=b"DIVX"); cases.append(("AVI DivX 表单(正常)", p, "ok"))
p = os.path.join(d, "junkhead.avi"); make_avi(p, leading_junk=16); cases.append(("AVI 头部杂字节(可播放)", p, "warn"))
p = os.path.join(d, "nohdr.avi")
import random
random.seed(7)
open(p, "wb").write(bytes(random.randrange(1, 256) for _ in range(8192)))
cases.append(("AVI 完全无RIFF(仅警告)", p, "warn"))
p = os.path.join(d, "zeros.mp4")
with open(p, "wb") as f:  # 全零填充(仅预分配空间)
    f.write(b"\x00" * 16384)
cases.append(("全零内容(无法播放)", p, "broken"))
p = os.path.join(d, "good.flv"); make_flv(p); cases.append(("FLV 完整", p, "ok"))
p = os.path.join(d, "trunc.flv"); make_flv(p, truncated=True); cases.append(("FLV 尾部截断(可播放)", p, "warn"))
p = os.path.join(d, "good.ts")
with open(p, "wb") as f:  # 188 字节纯 TS 包
    f.write((b"\x47" + b"\x11" * 187) * 40)
cases.append(("TS 188包(完整)", p, "warn"))
p = os.path.join(d, "good.m2ts")
with open(p, "wb") as f:  # 192 字节 M2TS 包(4字节时间戳 + 188 TS)
    f.write((b"\x00\x11\x22\x33" + b"\x47" + b"\x11" * 187) * 40)
cases.append(("M2TS 192包(AVCHD,完整)", p, "warn"))
p = os.path.join(d, "fake.mkv")
with open(p, "wb") as f:  # ASF/WMV 实体，扩展名 .mkv (标错扩展名，可播放)
    f.write(bytes.fromhex("3026b2758e66cf11a6d900aa0062ce6c") + b"\x00" * 8000)
cases.append(("WMV 实体标 .mkv(可播放)", p, "warn"))
p = os.path.join(d, "fake2.mp4")
with open(p, "wb") as f:  # TS 流实体，扩展名 .mp4 (标错扩展名，可播放)
    f.write((b"\x47" + b"\x11" * 187) * 60)
cases.append(("TS 实体标 .mp4(可播放)", p, "warn"))
p = os.path.join(d, "empty.mp4"); open(p, "wb").close(); cases.append(("空文件", p, "broken"))

passed = 0
for name, path, expect in cases:
    st, reason = detect_file(path)
    mark = "PASS" if st == expect else "FAIL"
    if st == expect: passed += 1
    print(f"[{mark}] {name}: {st} ({reason})")
print(f"\n{passed}/{len(cases)} 通过")

# sniff_container_ext 验证
print("\n--- 嗅探扩展名 ---")
sniff_cases = [
    ("good.mp4", "mp4"), ("good.mkv", "mkv"), ("good.avi", "avi"), ("good.flv", "flv"),
    ("avix.avi", "avi"), ("junkhead.avi", "avi"), ("good.ts", "ts"), ("good.m2ts", "mts"),
    ("fake.mkv", "wmv"), ("fake2.mp4", "ts"),
]
spass = 0
for fname, expect in sniff_cases:
    got = sniff_container_ext(os.path.join(d, fname))
    mark = "PASS" if got == expect else "FAIL"
    if got == expect: spass += 1
    print(f"[{mark}] {fname}: {got} (期望 {expect})")
print(f"嗅探 {spass}/{len(sniff_cases)} 通过")
shutil.rmtree(d, ignore_errors=True)

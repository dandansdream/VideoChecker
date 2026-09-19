# -*- coding: utf-8 -*-
"""视频完整性检测核心：容器结构校验 + 磁盘枚举 + 回收站删除。无任何网络/服务依赖。"""
import ctypes
import os
import struct
import uuid

DEFAULT_EXTS = [
    "mp4", "m4v", "mov", "mkv", "webm", "avi", "wmv", "flv",
    "ts", "mts", "m2ts", "mpg", "mpeg", "vob", "3gp", "rmvb", "rm", "ogg", "ogv",
]

SKIP_DIRS = {
    "$RECYCLE.BIN", "System Volume Information", "Windows", "Windows.old",
    "Program Files", "Program Files (x86)", "ProgramData",
    "node_modules", ".git", "AppData",
}


# ============================ 完整性检测核心 ============================

def check_mp4(path):
    """MP4/MOV/M4V/3GP: 按 box 结构走完整个文件。
    语义: ok=结构规范完整; warn=结构不规整但含索引(可播放); broken=缺索引/严重损坏(大概率无法播放)"""
    size = os.path.getsize(path)
    if size == 0:
        return "broken", "文件大小为 0 字节"
    if size < 100:
        return "broken", "文件过小(<100字节)，显然不完整"
    has_index = False
    try:
        with open(path, "rb") as f:
            pos = 0
            while pos < size:
                f.seek(pos)
                hdr = f.read(8)
                if len(hdr) < 8:
                    if has_index:
                        return "warn", "尾部数据不完整，但含 moov/moof 索引，通常可播放(末尾内容可能缺失)"
                    return "broken", "文件在 box 头部被截断且缺少索引，大概率无法播放"
                bsize, btype = struct.unpack(">I4s", hdr)
                hdr_len = 8
                if bsize == 1:  # 64 位 largesize
                    ext = f.read(8)
                    if len(ext) < 8:
                        return ("warn", "尾部数据不完整，但含索引，通常可播放") if has_index else \
                               ("broken", "文件在 box 头部被截断且缺少索引，大概率无法播放")
                    bsize = struct.unpack(">Q", ext)[0]
                    hdr_len = 16
                if btype in (b"moov", b"moof"):
                    has_index = True
                if bsize == 0:  # 延伸到文件尾
                    return ("ok", "结构完整") if has_index else \
                           ("broken", "缺少 moov/moof 索引，文件未正常写完")
                if bsize < hdr_len:
                    if has_index:
                        return "warn", "结构存在异常数据，但含 moov/moof 索引，通常可播放"
                    return "broken", "box 尺寸非法且缺少索引，文件损坏，大概率无法播放"
                pos += bsize
    except OSError as e:
        return "broken", f"读取失败: {e}"
    if pos != size:
        # 末尾越界(截断)或尾部有残留垃圾: 有索引就能播
        if has_index:
            return "warn", "尾部数据缺失或有多余字节，但含 moov/moof 索引，通常可播放"
        return "broken", "文件结构不完整且缺少索引，大概率无法播放(常见于下载中断)"
    if not has_index:
        return "broken", "缺少 moov/moof 索引，文件未正常写完(常见于下载中断)"
    return "ok", "结构完整"


def _read_ebml_id(f):
    b = f.read(1)
    if not b or b[0] == 0:
        return None, 0
    first = b[0]
    length = 1
    mask = 0x80
    while mask and not (first & mask):
        length += 1
        mask >>= 1
    if length > 4:
        return None, 0
    data = b + f.read(length - 1)
    if len(data) < length:
        return None, 0
    return int.from_bytes(data, "big"), length


def _read_ebml_size(f):
    b = f.read(1)
    if not b:
        return None, 0
    first = b[0]
    if first == 0:
        return None, 0
    length = 1
    mask = 0x80
    while mask and not (first & mask):
        length += 1
        mask >>= 1
    if length > 8:
        return None, 0
    data = f.read(length - 1)
    if len(data) < length - 1:
        return None, 0
    raw = bytes([first & (0xFF >> length)]) + data
    val = int.from_bytes(raw, "big")
    if val == (1 << (7 * length)) - 1:  # 全 1 = 未知大小
        return None, length
    return val, length


def _ebml_walk(f, end, depth=0):
    """返回: 'ok' | 'truncated'(声明超出末尾,尾部截断) | 'corrupt'(结构无法解析)
    | 'trailing'(末尾有少量无法解析的残余字节,主体结构完好)"""
    TRAILING_TOL = 4096  # 末尾残余不超过 4KB 视为写入器残留,不影响播放
    pos = f.tell()
    while pos < end:
        eid, idlen = _read_ebml_id(f)
        if eid is None:
            return "trailing" if end - pos <= TRAILING_TOL else "corrupt"
        vsize, sizelen = _read_ebml_size(f)
        if sizelen == 0:
            return "trailing" if end - pos <= TRAILING_TOL else "corrupt"
        pos = f.tell()
        if vsize is None:
            if depth > 4:
                return "corrupt"
            sub = _ebml_walk(f, end, depth + 1)
            if sub != "ok":
                return sub
            pos = f.tell()
        else:
            if pos + vsize > end:
                return "truncated"  # 声明的内容超出文件 → 尾部截断
            f.seek(pos + vsize)
            pos = f.tell()
    if pos == end:
        return "ok"
    return "trailing" if end - pos <= TRAILING_TOL else "corrupt"


def check_mkv(path):
    """MKV/WebM: EBML 结构遍历。
    语义: ok=结构规范完整; warn=尾部截断(可播放); broken=结构损坏"""
    size = os.path.getsize(path)
    if size == 0:
        return "broken", "文件大小为 0 字节"
    if size < 100:
        return "broken", "文件过小(<100字节)，显然不完整"
    try:
        with open(path, "rb") as f:
            eid, idlen = _read_ebml_id(f)
            if eid != 0x1A45DFA3:
                return "broken", "缺少 EBML 头，文件损坏或不是 MKV/WebM"
            vsize, sizelen = _read_ebml_size(f)
            if sizelen == 0:
                return "broken", "EBML 头损坏"
            f.seek(idlen + sizelen + vsize)
            result = _ebml_walk(f, size)
    except OSError as e:
        return "broken", f"读取失败: {e}"
    if result == "truncated":
        return "warn", "尾部数据缺失(截断)，已录制的部分通常可播放"
    if result == "trailing":
        return "warn", "文件末尾有少量无法识别的残余字节(写入器残留)，主体结构完好，通常不影响播放"
    if result == "corrupt":
        return "broken", "EBML 结构损坏，大概率无法播放"
    return "ok", "结构完整"


def check_avi(path):
    """AVI: RIFF 结构遍历 + idx1 索引检查。
    语义: ok=结构规范完整; warn=结构不规整(通常可播放); broken=不是 AVI/严重损坏"""
    size = os.path.getsize(path)
    if size == 0:
        return "broken", "文件大小为 0 字节"
    if size < 100:
        return "broken", "文件过小(<100字节)，显然不完整"
    has_idx1 = False
    truncated = False
    try:
        with open(path, "rb") as f:
            # 表单类型: "AVI "=经典 AVI, "AVIX"=OpenDML AVI 2.0(大文件/采集软件常用),
            # "DIVX"=DivX 媒体格式(RIFF 兼容)。三者都是正常 AVI。
            valid_forms = (b"AVI ", b"AVIX", b"DIVX")

            def _riff_ok(f_, pos_):
                f_.seek(pos_)
                h = f_.read(12)
                return len(h) >= 12 and h[0:4] == b"RIFF" and h[8:12] in valid_forms, h

            ok, hdr = _riff_ok(f, 0)
            start = 0
            leading_junk = False
            if not ok:
                # 头部可能有少量杂字节(录制设备标记等)，在前 64KB 内搜索 RIFF 签名
                f.seek(0)
                blob = f.read(65536)
                idx = blob.find(b"RIFF")
                found_at = -1
                while idx != -1 and idx + 12 <= len(blob):
                    if blob[idx + 8:idx + 12] in valid_forms:
                        found_at = idx
                        break
                    idx = blob.find(b"RIFF", idx + 1)
                if found_at >= 0:
                    start = found_at
                    leading_junk = found_at > 0
                    hdr = blob[found_at:found_at + 12]
                    f.seek(start)
                else:
                    # 找不到标准头也可能被部分播放器容错播放，降为警告而非损坏
                    return "warn", "未找到 RIFF/AVI 标准文件头，但文件可能仍可播放"
            riff_size = struct.unpack("<I", hdr[4:8])[0]
            declared_end = start + 8 + riff_size
            if declared_end > size:
                truncated = True  # 尾部截断，播放器一般能播已录部分
                declared_end = size
            pos = start + 12
            while pos < declared_end - 8:
                f.seek(pos)
                chunk = f.read(8)
                if len(chunk) < 8:
                    truncated = True
                    break
                cid, csz = struct.unpack("<4sI", chunk)
                if cid == b"idx1":
                    has_idx1 = True
                if csz > declared_end - pos - 8:
                    truncated = True
                    break
                step = 8 + csz + (csz & 1)  # 奇数长度有 1 字节对齐填充
                pos += step
    except OSError as e:
        return "broken", f"读取失败: {e}"
    if truncated:
        return "warn", "尾部数据缺失/结构不规整，已录制的部分通常可播放"
    if leading_junk:
        return "warn", "头部有少量额外数据但 RIFF 结构完整，可正常播放"
    if not has_idx1:
        return "warn", "缺少 idx1 索引(可播放，但部分播放器进度条拖动可能不准)"
    return "ok", "结构完整"


def check_flv(path):
    """FLV: tag 链遍历 + PreviousTagSize 校验。
    语义: ok=结构规范完整; warn=尾部截断/不规整(可播放); broken=损坏"""
    size = os.path.getsize(path)
    if size == 0:
        return "broken", "文件大小为 0 字节"
    if size < 100:
        return "broken", "文件过小(<100字节)，显然不完整"
    try:
        with open(path, "rb") as f:
            hdr = f.read(9)
            if len(hdr) < 9 or hdr[0:3] != b"FLV":
                return "broken", "缺少 FLV 文件头，文件损坏"
            prev = f.read(4)
            if len(prev) < 4 or struct.unpack(">I", prev)[0] != 0:
                return "broken", "FLV 头部 PreviousTagSize0 异常"
            pos = 13
            while pos < size:
                f.seek(pos)
                th = f.read(11)
                if len(th) < 11:
                    return "warn", "尾部数据不完整，已录制的部分通常可播放"
                dsize = int.from_bytes(th[1:4], "big")
                if pos + 11 + dsize + 4 > size:
                    return "warn", "最后一个 tag 数据不完整(尾部截断)，已录制的部分通常可播放"
                f.seek(pos + 11 + dsize)
                pt = f.read(4)
                if len(pt) < 4 or struct.unpack(">I", pt)[0] != 11 + dsize:
                    return "warn", "PreviousTagSize 校验失败，结构不规整但通常可播放"
                pos += 11 + dsize + 4
    except OSError as e:
        return "broken", f"读取失败: {e}"
    return "ok", "结构完整"


def check_ts(path):
    """MPEG-TS/M2TS: 包对齐 + 同步字节检查。
    TS 包长 188 字节；M2TS/BDAV(AVCHD 摄像机等) 为 192 字节包 = 4 字节 TP_extra_header + 188 字节 TS 包。
    自动识别两种包长，按采样统计同步字节比例判定。"""
    size = os.path.getsize(path)
    if size == 0:
        return "broken", "文件大小为 0 字节"
    if size < 188:
        return "broken", "文件不足一个 TS 包(188字节)，不完整"

    # 候选: (包长, 同步字节偏移)。192 包的 0x47 在第 5 字节
    candidates = [(188, 0), (192, 4)]
    best = None  # (pkt, offset, good, total)
    try:
        with open(path, "rb") as f:
            for pkt, off in candidates:
                if size < pkt * 8:
                    continue  # 样本太少不可靠
                n = min(size // pkt, 2048)  # 采样最多 2048 个包
                stride = max(1, (size // pkt) // n)
                good = total = 0
                for i in range(0, n, max(1, n // 512 or 1)):
                    pos = (i * stride) * pkt
                    if pos + pkt > size:
                        break
                    f.seek(pos + off)
                    if f.read(1) == b"\x47":
                        good += 1
                    total += 1
                ratio = good / total if total else 0
                if best is None or ratio > best[2]:
                    best = (pkt, off, ratio, good, total)
                if ratio > 0.98:
                    break
    except OSError as e:
        return "broken", f"读取失败: {e}"

    if best is None:
        return "broken", "文件过小，无法进行 TS 包检查"
    pkt, off, ratio, good, total = best
    if ratio < 0.9:
        return "broken", f"TS 同步字节错误率过高({(1-ratio)*100:.1f}%)，文件损坏"
    if size % pkt != 0:
        return "warn", f"文件大小不是 {pkt} 的整数倍，末尾可能有残留/截断"
    if pkt == 192:
        return "warn", "M2TS(BDAV) 格式，包结构完整(192字节包，含时间戳头)"
    return "warn", "TS 流无法深度校验，仅做包对齐检查(同步字节正常)"


def check_ogg(path):
    """Ogg/OGV: 页链遍历('OggS' 页头 + 段表)。ok=页链完整到 EOS; warn=截断(可播已录部分)"""
    size = os.path.getsize(path)
    if size == 0:
        return "broken", "文件大小为 0 字节"
    if size < 100:
        return "broken", "文件过小(<100字节)，显然不完整"
    try:
        with open(path, "rb") as f:
            if f.read(4) != b"OggS":
                return "warn", "未找到 OggS 页头，但文件可能仍可播放"
            pos = 0
            eos = False
            last_ok = 0
            while pos + 27 <= size:
                f.seek(pos)
                hdr = f.read(27)
                if hdr[0:4] != b"OggS" or hdr[4] != 0:
                    break  # 页链断裂
                nseg = hdr[26]
                segtab = f.read(nseg)
                if len(segtab) < nseg:
                    return "warn", "Ogg 页在段表处被截断，已录部分通常可播放"
                plen = sum(segtab)
                pos = pos + 27 + nseg + plen
                last_ok = pos
                if hdr[5] & 0x04:
                    eos = True
            if pos >= size and (size - pos) == 0:
                if eos:
                    return "ok", "结构完整"
                return "warn", "缺少结尾 EOS 页(录制未正常结束)，已录部分通常可播放"
            if last_ok > 0:
                return "warn", "Ogg 页链不完整/截断，已录部分通常可播放"
            return "warn", "Ogg 页结构异常，但文件可能仍可播放"
    except OSError as e:
        return "broken", f"读取失败: {e}"


def _make_basic_checker(magic, warn_msg):
    def checker(path):
        size = os.path.getsize(path)
        if size == 0:
            return "broken", "文件大小为 0 字节"
        if size < 100:
            return "broken", "文件过小(<100字节)，显然不完整"
        try:
            with open(path, "rb") as f:
                head = f.read(len(magic))
        except OSError as e:
            return "broken", f"读取失败: {e}"
        if head[:len(magic)] != magic:
            return "broken", "文件头不符，文件损坏或不是该格式"
        return "warn", warn_msg
    return checker


CHECKERS = {
    "mp4": check_mp4, "m4v": check_mp4, "mov": check_mp4, "3gp": check_mp4,
    "mkv": check_mkv, "webm": check_mkv,
    "avi": check_avi,
    "flv": check_flv,
    "ogg": check_ogg, "ogv": check_ogg,
    "ts": check_ts, "mts": check_ts, "m2ts": check_ts,
    "wmv": _make_basic_checker(bytes.fromhex("3026b2758e66cf11"), "ASF/WMV 无法深度校验，仅验证文件头"),
    "mpg": _make_basic_checker(b"\x00\x00\x01\xba", "MPG 无法深度校验，仅验证起始码"),
    "mpeg": _make_basic_checker(b"\x00\x00\x01\xba", "MPG 无法深度校验，仅验证起始码"),
    "vob": _make_basic_checker(b"\x00\x00\x01\xba", "VOB 无法深度校验，仅验证起始码"),
    "rmvb": _make_basic_checker(b".RMF", "RMVB 无法深度校验，仅验证文件头"),
    "rm": _make_basic_checker(b".RMF", "RM 无法深度校验，仅验证文件头"),
}


def _sniff_ts_pkt(path):
    """探测 TS/M2TS 包长: 0x47 开头且对齐率 >98% 时返回包长(188/192)，否则 None。
    (单字节 0x47 太弱，需验证对齐。)"""
    size = os.path.getsize(path)
    if size < 188 * 8:
        return None
    try:
        with open(path, "rb") as f:
            for pkt, off in ((188, 0), (192, 4)):
                n = min(size // pkt, 512)
                good = 0
                for i in range(n):
                    f.seek(i * pkt + off)
                    if f.read(1) == b"\x47":
                        good += 1
                if n and good / n > 0.98:
                    return pkt
    except OSError:
        pass
    return None


def _sniff_ts(path):
    return _sniff_ts_pkt(path) is not None


# 内容嗅探表: (魔数偏移, 魔数, 检测函数, 容器名)。扩展名不可信时以实际内容为准
_SNIFFERS = [
    (0, bytes.fromhex("3026b2758e66cf11a6d900aa0062ce6c"), _make_basic_checker(
        bytes.fromhex("3026b2758e66cf11"), "ASF/WMV 容器无法深度校验，仅验证文件头"), "ASF/WMV"),
    (0, b"\x1a\x45\xdf\xa3", check_mkv, "Matroska(MKV/WebM)"),
    (4, b"ftyp", check_mp4, "MP4/MOV"),
    (0, b"OggS", check_ogg, "Ogg"),
    (0, b"RIFF", check_avi, "RIFF/AVI"),
    (0, b"FLV", check_flv, "FLV"),
    (0, b".RMF", _make_basic_checker(b".RMF", "RealMedia 无法深度校验，仅验证文件头"), "RealMedia"),
]


def _is_all_zero(path, size):
    """采样检查文件内容是否全部为空字节(预分配但从未写入数据)。"""
    try:
        with open(path, "rb") as f:
            positions = [i * size // 8 for i in range(8)] + [max(0, size - 4096)]
            for pos in positions:
                f.seek(pos)
                if any(f.read(4096)):
                    return False
    except OSError:
        return False
    return True


def detect_file(path):
    ext = os.path.splitext(path)[1].lower().lstrip(".")
    checker = CHECKERS.get(ext)
    if checker is None:
        return "warn", "该格式暂不支持深度校验"
    size = os.path.getsize(path)
    if size >= 4096 and _is_all_zero(path, size):
        return "broken", "文件内容全部为空字节(仅预分配了空间，未写入实际数据)，无法播放"
    # 内容嗅探: 文件头与扩展名指示的容器明显不符时，按实际容器检测
    # (常见于扩展名标错的文件，如 WMV 实体被命名为 .mkv，播放器按内容识别可正常播放)
    try:
        with open(path, "rb") as f:
            head = f.read(16)
        if head[:1] == b"\x47" and _sniff_ts(path):
            st, reason = check_ts(path)
            if "ts" not in ext:  # ts/mts/m2ts 扩展名本来就按 TS 检测，无需注明
                return st, f"实际为 MPEG-TS 流(扩展名是 .{ext})；{reason}"
            return st, reason
        for off, magic, fn, cname in _SNIFFERS:
            if len(head) >= off + len(magic) and head[off:off + len(magic)] == magic:
                st, reason = fn(path)
                if fn is not checker:
                    return st, f"实际为 {cname} 容器(扩展名是 .{ext})；{reason}"
                return st, reason
    except OSError:
        pass  # 嗅探读取失败则回退到扩展名检测
    try:
        return checker(path)
    except Exception as e:  # 防御：任何检测异常不中断扫描
        return "warn", f"检测异常: {e}"


def sniff_container_ext(path):
    """按内容嗅探真实容器类型，返回建议的扩展名(不含点)；无法识别返回 None。
    用于"修正标错的扩展名"功能。"""
    try:
        with open(path, "rb") as f:
            head = f.read(65536)
        if len(head) < 16:
            return None
        if head.startswith(bytes.fromhex("3026b2758e66cf11a6d900aa0062ce6c")):
            return "wmv"
        if head.startswith(b"\x1a\x45\xdf\xa3"):
            return "webm" if b"webm" in head[:64] else "mkv"
        if head.startswith(b"RIFF"):
            return "avi" if head[8:12] in (b"AVI ", b"AVIX", b"DIVX") else None
        if head.startswith(b"FLV"):
            return "flv"
        if head.startswith(b"OggS"):
            return "ogg"
        if head.startswith(b".RMF"):
            return "rmvb"
        if head[4:8] == b"ftyp":
            return "mp4"
        # TS: 188 包 0x47 在文件头；192 包(M2TS) 0x47 在第 5 字节
        if head[:1] == b"\x47" or head[4:5] == b"\x47":
            pkt = _sniff_ts_pkt(path)
            if pkt == 188:
                return "ts"
            if pkt == 192:
                return "mts"
        # 回退: 头部有杂字节时在前 64KB 内搜索 RIFF+AVI 签名
        idx = head.find(b"RIFF")
        while idx != -1 and idx + 12 <= len(head):
            if head[idx + 8:idx + 12] in (b"AVI ", b"AVIX", b"DIVX"):
                return "avi"
            idx = head.find(b"RIFF", idx + 1)
    except OSError:
        pass
    return None


# ============================ 时长解析 ============================

def _dur_mp4(path):
    """mvhd: timescale + duration"""
    size = os.path.getsize(path)
    try:
        with open(path, "rb") as f:
            pos = 0
            while pos + 8 <= size:
                f.seek(pos)
                hdr = f.read(8)
                if len(hdr) < 8:
                    return None
                bsize, btype = struct.unpack(">I4s", hdr)
                hdr_len = 8
                if bsize == 1:
                    ext = f.read(8)
                    if len(ext) < 8:
                        return None
                    bsize = struct.unpack(">Q", ext)[0]
                    hdr_len = 16
                elif bsize == 0:
                    bsize = size - pos
                if btype == b"moov":
                    end = min(pos + bsize, size)
                    ipos = pos + hdr_len
                    while ipos + 8 <= end:
                        f.seek(ipos)
                        h2 = f.read(8)
                        if len(h2) < 8:
                            return None
                        s2, t2 = struct.unpack(">I4s", h2)
                        if s2 < 8:
                            return None
                        if t2 == b"mvhd":
                            body = f.read(32)
                            if len(body) >= 20:
                                if body[0] == 1 and len(body) >= 28:
                                    ts = struct.unpack(">I", body[20:24])[0]
                                    dur = struct.unpack(">Q", body[24:32])[0]
                                else:
                                    ts = struct.unpack(">I", body[12:16])[0]
                                    dur = struct.unpack(">I", body[16:20])[0]
                                if ts:
                                    return dur / ts
                            return None
                        ipos += s2
                    return None
                if bsize < hdr_len:
                    return None
                pos += bsize
    except OSError:
        return None
    return None


def _dur_mkv(path):
    """Segment→Info→Duration(0x4489) × TimestampScale(0x2AD7B1)"""
    try:
        with open(path, "rb") as f:
            eid, idlen = _read_ebml_id(f)
            if eid != 0x1A45DFA3:
                return None
            vsize, sizelen = _read_ebml_size(f)
            if vsize is None:
                return None
            f.seek(idlen + sizelen + vsize)
            eid, idlen = _read_ebml_id(f)
            if eid != 0x18538067:  # Segment
                return None
            seg_vsize, _ = _read_ebml_size(f)
            seg_end = (f.tell() + seg_vsize) if seg_vsize is not None else os.path.getsize(path)
            pos = f.tell()
            limit = min(seg_end, pos + 4194304)  # Info 通常紧跟 Segment 开头
            while pos < limit:
                f.seek(pos)
                eid, idlen = _read_ebml_id(f)
                if eid is None:
                    return None
                vsize, sizelen = _read_ebml_size(f)
                if eid == 0x1549A966:  # Info
                    info_end = min(pos + idlen + sizelen + (vsize or 0), seg_end)
                    ipos = f.tell()
                    scale, dur = 1000000, None
                    while ipos < info_end:
                        f.seek(ipos)
                        cid, cilen = _read_ebml_id(f)
                        if cid is None:
                            break
                        cv, _cs = _read_ebml_size(f)
                        body_start = f.tell()
                        if cid == 0x2AD7B1 and cv and cv <= 8:  # TimestampScale
                            scale = int.from_bytes(f.read(cv), "big") or scale
                        elif cid == 0x4489 and cv in (4, 8):  # Duration (float)
                            raw = f.read(cv)
                            dur = struct.unpack(">f" if cv == 4 else ">d", raw)[0]
                        elif vsize is None:
                            break
                        ipos = body_start + (cv or 0)
                    if dur is not None:
                        return dur * scale / 1e9
                    return None
                if vsize is None:
                    return None
                pos = f.tell() + vsize
    except OSError:
        return None
    return None


def _dur_avi(path):
    """avih: dwTotalFrames × dwMicroSecPerFrame"""
    try:
        with open(path, "rb") as f:
            blob = f.read(65536)
        idx = blob.find(b"avih")
        while idx != -1 and idx + 8 + 20 <= len(blob):
            csz = struct.unpack("<I", blob[idx + 4:idx + 8])[0]
            if 20 <= csz <= 512:
                usec, _mx, _pad, _fl, frames = struct.unpack("<IIIII", blob[idx + 8:idx + 28])
                if usec > 0 and frames > 0:
                    return frames * usec / 1e6
            idx = blob.find(b"avih", idx + 1)
    except OSError:
        pass
    return None


def _dur_flv(path):
    """最后一个 tag 的 timestamp(毫秒)"""
    size = os.path.getsize(path)
    try:
        with open(path, "rb") as f:
            pos = 13
            last_ts = None
            while pos + 15 <= size:
                f.seek(pos)
                th = f.read(11)
                if len(th) < 11:
                    break
                dsize = int.from_bytes(th[1:4], "big")
                last_ts = int.from_bytes(th[4:7], "big") + (th[7] << 24)
                pos += 11 + dsize + 4
        return last_ts / 1000.0 if last_ts is not None else None
    except OSError:
        return None


_ASF_FPROP = bytes.fromhex("a1dcab8c47a9cf118ee400c00c205365")

def _dur_asf(path):
    """File Properties Object 的 play_duration(100ns 单位)"""
    try:
        with open(path, "rb") as f:
            blob = f.read(1048576)
        idx = blob.find(_ASF_FPROP)
        if idx == -1 or idx + 72 > len(blob):
            return None
        play_dur = struct.unpack("<Q", blob[idx + 64:idx + 72])[0]
        return play_dur / 1e7 if play_dur else None
    except OSError:
        return None


def _dur_ogg(path):
    """最后一页 granule position ÷ 采样率/帧率"""
    size = os.path.getsize(path)
    try:
        with open(path, "rb") as f:
            head = f.read(65536)
            rate = 0
            i = head.find(b"\x01vorbis")
            if i != -1 and i + 16 <= len(head):
                rate = struct.unpack("<I", head[i + 12:i + 16])[0]
            if not rate:
                i = head.find(b"\x80theora")
                if i != -1 and i + 30 <= len(head):
                    frn = struct.unpack(">I", head[i + 22:i + 26])[0]
                    frd = struct.unpack(">I", head[i + 26:i + 30])[0]
                    if frn and frd:
                        rate = frn / frd
            if not rate:
                return None
            f.seek(max(0, size - 65536))
            tail = f.read()
            i = tail.rfind(b"OggS")
            while i != -1:
                if i + 14 <= len(tail) and tail[i + 4] == 0:
                    gpos = struct.unpack("<q", tail[i + 6:i + 14])[0]
                    if gpos > 0:
                        return gpos / rate
                    return None
                i = tail.rfind(b"OggS", 0, i)
    except OSError:
        pass
    return None


def _dur_ts(path, pkt):
    """TS 无全局时长头: 取首尾 PCR 时间戳(33 位, 90kHz)之差作估算"""
    off = 4 if pkt == 192 else 0
    size = os.path.getsize(path)

    def pcr_at(f, pos):
        f.seek(pos + off)
        p = f.read(12)
        if len(p) < 12 or p[0] != 0x47 or not (p[3] & 0x20) or p[4] < 7:
            return None
        if not (p[5] & 0x10):
            return None
        base = ((p[6] << 25) | (p[7] << 17) | (p[8] << 9) | (p[9] << 1) | (p[10] >> 7)) & 0x1FFFFFFFF
        return base / 90000.0

    try:
        with open(path, "rb") as f:
            npkt = size // pkt
            first = last = None
            for i in range(min(npkt, 4096)):
                v = pcr_at(f, i * pkt)
                if v is not None:
                    first = v
                    break
            for i in range(min(npkt, 4096)):
                v = pcr_at(f, (npkt - 1 - i) * pkt)
                if v is not None:
                    last = v
                    break
        if first is not None and last is not None and last > first:
            return last - first
    except OSError:
        pass
    return None


def get_duration(path):
    """解析视频时长(秒)，返回 float 或 None。
    MP4/MKV/AVI/FLV/ASF/Ogg 取容器头部声明值；TS/M2TS 用首尾 PCR 估算。"""
    try:
        if os.path.getsize(path) < 100:
            return None
        with open(path, "rb") as f:
            head = f.read(16)
        if len(head) < 12:
            return None
        if head.startswith(bytes.fromhex("3026b2758e66cf11a6d900aa0062ce6c")):
            return _dur_asf(path)
        if head.startswith(b"\x1a\x45\xdf\xa3"):
            return _dur_mkv(path)
        if head.startswith(b"OggS"):
            return _dur_ogg(path)
        if head.startswith(b"FLV"):
            return _dur_flv(path)
        if head[4:8] == b"ftyp":
            return _dur_mp4(path)
        if head.startswith(b"RIFF") and head[8:12] in (b"AVI ", b"AVIX", b"DIVX"):
            return _dur_avi(path)
        if head[:1] == b"\x47" or head[4:5] == b"\x47":
            pkt = _sniff_ts_pkt(path)
            if pkt:
                return _dur_ts(path, pkt)
        # 回退: 头部带杂字节的 AVI
        return _dur_avi(path)
    except Exception:
        return None


def human_size(n):
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024:
            return f"{n} B" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} PB"


# ============================ 磁盘枚举 ============================

def list_drives():
    bitmask = ctypes.windll.kernel32.GetLogicalDrives()
    drives = []
    for i in range(26):
        if bitmask >> i & 1:
            letter = chr(65 + i) + ":\\"
            dtype = ctypes.windll.kernel32.GetDriveTypeW(ctypes.c_wchar_p(letter))
            if dtype in (2, 3, 4):  # 可移动 / 固定 / 网络
                free = total = 0
                try:
                    free_c = ctypes.c_ulonglong(0)
                    total_c = ctypes.c_ulonglong(0)
                    ctypes.windll.kernel32.GetDiskFreeSpaceExW(
                        ctypes.c_wchar_p(letter),
                        ctypes.byref(ctypes.c_ulonglong(0)),
                        ctypes.byref(total_c),
                        ctypes.byref(free_c),
                    )
                    free, total = free_c.value, total_c.value
                except Exception:
                    pass
                drives.append({
                    "letter": letter,
                    "type": {2: "可移动磁盘", 3: "本地磁盘", 4: "网络磁盘"}.get(dtype, "未知"),
                    "free": free, "total": total,
                    "freeText": human_size(free), "totalText": human_size(total),
                })
    return drives


# ============================ 回收站删除 ============================

class SHFILEOPSTRUCTW(ctypes.Structure):
    _fields_ = [
        ("hwnd", ctypes.c_void_p),
        ("wFunc", ctypes.c_uint),
        ("pFrom", ctypes.c_void_p),
        ("pTo", ctypes.c_void_p),
        ("fFlags", ctypes.c_ushort),
        ("fAnyOperationsAborted", ctypes.c_int),
        ("hNameMappings", ctypes.c_void_p),
        ("lpszProgressTitle", ctypes.c_wchar_p),
    ]


FO_DELETE = 3
FOF_ALLOWUNDO = 0x40
FOF_NOCONFIRMATION = 0x10
FOF_NOERRORUI = 0x400
FOF_SILENT = 0x4

_SH_ERR_TEXT = {
    2: "文件不存在",
    3: "路径不存在",
    5: "拒绝访问(权限不足)",
    32: "文件被其他程序占用",
    124: "路径无效(路径过长或含非法字符)",
    0x71: "不能删除根目录",
    0x78: "目标路径无效",
}


def _sh_err_text(rc):
    if rc in _SH_ERR_TEXT:
        return _SH_ERR_TEXT[rc]
    return f"系统错误码 {rc}"


def _sh_delete(path):
    """单文件 SHFileOperationW 删除。返回 (是否成功, 错误码)。"""
    op = SHFILEOPSTRUCTW()
    op.hwnd = None
    op.wFunc = FO_DELETE
    # pFrom 必须以双 \0 结尾(create_unicode_buffer 其余部分已填零)
    buf = ctypes.create_unicode_buffer(path + "\0")
    op.pFrom = ctypes.cast(buf, ctypes.c_void_p)
    op.pTo = None
    op.fFlags = FOF_ALLOWUNDO | FOF_NOCONFIRMATION | FOF_NOERRORUI | FOF_SILENT
    rc = ctypes.windll.shell32.SHFileOperationW(ctypes.byref(op))
    return (rc == 0 and not op.fAnyOperationsAborted), rc


def _move_to_short_temp(path):
    """长路径回退: 用 \\\\?\\ 前缀把文件移到同卷根下的短临时目录。返回新路径, 失败返回 None。"""
    try:
        drive, _ = os.path.splitdrive(path)
        tmp_dir = os.path.join(drive + os.sep, "__VideoChecker_tmp")
        os.makedirs(tmp_dir, exist_ok=True)
        short = os.path.join(tmp_dir, "vc_" + uuid.uuid4().hex[:12] + os.path.splitext(path)[1])
        os.rename("\\\\?\\" + path, "\\\\?\\" + short)
        return short
    except OSError:
        return None


def _long_path(path):
    """超过 MAX_PATH 的路径加 \\\\?\\ 前缀, 其余原样返回。"""
    if len(path) >= 248 and not path.startswith("\\\\?\\"):
        return "\\\\?\\" + path
    return path


def recycle_file(path):
    """移入回收站，而不是直接删除。失败返回 (False, 具体原因)。
    依次尝试: 清除只读等属性 → 直接回收 → 长路径移到同卷短路径后回收。"""
    if not os.path.exists(_long_path(os.path.abspath(path))):
        return False, "文件不存在(可能已被删除)"
    path = os.path.abspath(path)
    # 只读/系统/隐藏属性会让 SHFileOperationW 静默失败, 先清为普通属性
    try:
        ctypes.windll.kernel32.SetFileAttributesW(_long_path(path), 0x80)  # FILE_ATTRIBUTE_NORMAL
    except Exception:
        pass
    ok, rc = _sh_delete(path)
    if ok:
        return True, "已移入回收站"
    # 长路径回退: SHFileOperationW 不支持超过 259 字符的路径,
    # 先用 \\?\ 前缀移到同卷短临时目录再回收(还原后文件会出现在该目录)
    if len(path) > 240:
        short = _move_to_short_temp(path)
        if short:
            ok, rc = _sh_delete(short)
            if ok:
                return True, "已移入回收站(长路径经同卷中转, 还原后位于 __VideoChecker_tmp)"
    return False, _sh_err_text(rc)

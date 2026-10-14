"""
烧录全息留痕与源程序防干扰比对核心引擎。
功能：
1. 解析 .opj 工程包内部 .sct 与 .opd，提取原始 Hex 源文件、基准 CRC、物理扇区地址段与供电参数；
2. 生成源程序【物理地址 + 数据】镜像归档；
3. 执行源程序基准与硬件实测/回读数据的逐字节比对，检测电磁干扰比特位翻转 (Bit-Flip)；
4. 双轨持久化归档：月度汇总 CSV + 单板全息报告 (含地址段、数据镜像、比对 Diff 与底层日志)。
"""

import csv
import datetime
import os
import re
import sys
import time
import zipfile
import struct
from typing import Optional


def verify_project_readback(programmer, project, project_path, progress_callback=None):
    """按 OPD 中的小端地址/长度记录逐段回读，拒绝缺失和截断数据。"""
    with zipfile.ZipFile(project_path) as archive:
        names = [n for n in archive.namelist() if n.endswith('.opd')]
        if len(names) != 1:
            raise ValueError('工程必须包含唯一 OPD 数据文件')
        packed = archive.read(names[0])
    segments = []
    position = 0
    while position < len(packed):
        if len(packed) - position < 8:
            raise ValueError('OPD 地址/长度头截断')
        address, length = struct.unpack_from('<II', packed, position)
        position += 8
        if length <= 0 or length > len(packed) - position:
            raise ValueError('OPD 数据段长度非法')
        segments.append((address, packed[position:position + length]))
        position += length
    if not segments:
        raise ValueError('OPD 没有有效数据段')
    source_parts, read_parts, samples, offsets = [], [], [], []
    mismatch = 0
    cumulative = 0
    total = sum(len(data) for _, data in segments)
    for address, expected in segments:
        if progress_callback:
            progress_callback(cumulative, total, address)
        actual = programmer.read_chip(project, address, len(expected))
        if len(actual) != len(expected):
            raise ValueError(f'回读长度错误 0x{address:08X}: 期望 {len(expected)}，实际 {len(actual)}')
        diff = compare_and_diff_data(expected, actual, address)
        mismatch += diff['mismatch_count']
        samples.extend(diff['samples'][:max(0, 50 - len(samples))])
        offsets.extend(cumulative + off for off in diff['mismatch_offsets'])
        source_parts.append(expected)
        read_parts.append(actual)
        cumulative += len(expected)
        if progress_callback:
            progress_callback(cumulative, total, address)
    source = b''.join(source_parts)
    readback = b''.join(read_parts)
    return source, readback, segments[0][0], {
        'is_match': mismatch == 0, 'mismatch_count': mismatch,
        'source_length': len(source), 'readback_length': len(readback),
        'match_rate': round((len(source) - mismatch) / len(source) * 100, 4),
        'samples': samples, 'mismatch_offsets': offsets,
        'segments': [{'address': a, 'length': len(d)} for a, d in segments],
    }


def parse_opj_metadata(opj_path: str) -> dict:
    """从 .opj (ZIP 压缩包) 中解析出核心元数据与物理扇区地址映射"""
    metadata = {
        "valid": False,
        "source_file": "未知",
        "source_crc": "未知",
        "opd_checksum": "未知",
        "chip_name": "未知",
        "device_model": "SG400",
        "build_time": "未知",
        "vcc_mv": 3300,
        "freq_hz": 5000000,
        "sectors": [],
        "opd_filename": "",
        "sct_content": "",
        "opd_size": 0,
        "summary_address_range": "未定义",
    }
    if not os.path.isfile(opj_path):
        return metadata

    try:
        if not zipfile.is_zipfile(opj_path):
            return metadata

        with zipfile.ZipFile(opj_path, "r") as z:
            names = z.namelist()
            sct_name = next((n for n in names if n.endswith(".sct")), None)
            opd_name = next((n for n in names if n.endswith(".opd")), None)

            if opd_name:
                metadata["opd_filename"] = opd_name
                metadata["opd_size"] = z.getinfo(opd_name).file_size

            if sct_name:
                sct_raw = z.read(sct_name).decode("utf-8", errors="ignore")
                metadata["sct_content"] = sct_raw

                # 提取源文件与 CRC: --source_file:D:/FW/VE21-8A/Geely_VE21_DisableHSM_V2.hex;0x1e4bbef0,
                m_src = re.search(r"--source_file:(.*?);(0x[0-9a-fA-F]+)", sct_raw)
                if m_src:
                    metadata["source_file"] = os.path.basename(m_src.group(1).replace("\\", "/"))
                    metadata["source_crc"] = m_src.group(2)

                # 提取 opd_checksum
                m_chk = re.search(r"--opd_checksum:(0x[0-9a-fA-F]+)", sct_raw)
                if m_chk:
                    metadata["opd_checksum"] = m_chk.group(1)
                elif metadata["source_crc"] != "未知":
                    metadata["opd_checksum"] = metadata["source_crc"]

                # 提取芯片型号: --chip_name:TC387(DAP)
                m_chip = re.search(r"--chip_name:([^\r\n]+)", sct_raw)
                if m_chip:
                    metadata["chip_name"] = m_chip.group(1).strip()

                # 提取打包时间: --build_time:2026-01-14 14:39:02.423969
                m_time = re.search(r"--build_time:([^\r\n]+)", sct_raw)
                if m_time:
                    metadata["build_time"] = m_time.group(1).strip()

                # 提取设备型号: --device_model:SG400
                m_dev = re.search(r"--device_model:([^\r\n]+)", sct_raw)
                if m_dev:
                    metadata["device_model"] = m_dev.group(1).strip()

                # 提取 VCC 与频率
                m_vcc = re.search(r"channel:setvcc\((\d+)\)", sct_raw)
                if m_vcc:
                    metadata["vcc_mv"] = int(m_vcc.group(1))

                m_freq = re.search(r"dev\.Param\.freq\s*=\s*(\d+)", sct_raw)
                if m_freq:
                    metadata["freq_hz"] = int(m_freq.group(1))

                # 提取扇区 flashinfo = { {0xA0000000, 32, 0x80000}, ... }
                # 匹配类似 {0xA0000000, 32, 0x80000}
                # add_sector(起始地址, 逻辑扇区数, 区域总字节数): 区域结束 = 起始 + 区域总大小
                sector_pattern = re.compile(
                    r"\{\s*(0x[0-9a-fA-F]+)\s*,\s*(\d+)\s*,\s*(0x[0-9a-fA-F]+|\d+)\s*\}"
                )
                sectors = []
                for start_hex, count_str, size_str in sector_pattern.findall(sct_raw):
                    start_addr = int(start_hex, 16)
                    count = int(count_str)
                    size = int(size_str, 16) if size_str.startswith("0x") else int(size_str)
                    sectors.append({
                        "start_addr": start_addr,
                        "start_hex": f"0x{start_addr:08X}",
                        "count": count,
                        "size": size,
                        "sector_size": size // count if count else size,
                        "end_hex": f"0x{start_addr + size:08X}",
                    })
                metadata["sectors"] = sectors
                if sectors:
                    first_addr = sectors[0]["start_hex"]
                    last_addr = sectors[-1]["end_hex"]
                    metadata["summary_address_range"] = f"{first_addr} ~ {last_addr} ({len(sectors)}个分区)"

                metadata["valid"] = True

    except Exception as exc:
        metadata["error"] = str(exc)

    return metadata


def generate_source_address_data_image(opj_path: str, output_path: str = None) -> str:
    """生成源程序【物理地址 + 数据结构】镜像文本报告"""
    meta = parse_opj_metadata(opj_path)
    if not meta.get("valid"):
        return f"无法解析工程文件: {opj_path}"

    lines = [
        "================================================================================",
        "                     力捷丰 SG400 源程序物理地址与数据映像                      ",
        "================================================================================",
        f"工程名称: {os.path.basename(opj_path)}",
        f"源固件名: {meta['source_file']}",
        f"基准校验: {meta['opd_checksum']} (原始CRC: {meta['source_crc']})",
        f"目标芯片: {meta['chip_name']} | 供电: {meta['vcc_mv']} mV | 频率: {meta['freq_hz'] // 1000000} MHz",
        f"打包时间: {meta['build_time']}",
        f"二进制包: {meta['opd_filename']} ({meta['opd_size']:,} 字节)",
        "--------------------------------------------------------------------------------",
        "物理扇区规划与地址映射表:",
        f"{'编号':<4} {'起始地址':<12} {'结束地址':<12} {'逻辑扇区数':<10} {'逻辑扇区大小':<12} {'区域大小':<10}",
    ]

    for idx, s in enumerate(meta.get("sectors", []), 1):
        lines.append(
            f"{idx:<4} {s['start_hex']:<12} {s['end_hex']:<12} {s['count']:<10} "
            f"{s.get('sector_size', 0) // 1024} KB{'':<7} {s['size'] // 1024} KB"
        )

    lines.append("--------------------------------------------------------------------------------")
    lines.append(f"总计物理地址段覆盖: {meta['summary_address_range']}")
    lines.append("================================================================================")

    content = "\n".join(lines)
    if output_path:
        os.makedirs(os.path.dirname(os.path.abspath(output_path)), exist_ok=True)
        with open(output_path, "w", encoding="utf-8") as f:
            f.write(content)

    return content


def compare_and_diff_data(source_bytes: bytes, readback_bytes: bytes, base_address: int = 0) -> dict:
    """逐字节绝对比对源程序与物理回读数据，精确分析比特位翻转 (Bit-Flip) 与干扰。

    mismatch_count 为全量统计；samples 只保留前 50 处详情供报告展示；
    mismatch_offsets 记录全部不一致偏移，供完整镜像标记使用。
    """
    min_len = min(len(source_bytes), len(readback_bytes))
    max_len = max(len(source_bytes), len(readback_bytes))

    samples = []
    mismatch_offsets = []
    for i in range(min_len):
        sb = source_bytes[i]
        rb = readback_bytes[i]
        if sb != rb:
            mismatch_offsets.append(i)
            if len(samples) < 50:  # 详情样本最多保留 50 处
                diff_bits = sb ^ rb
                flipped_positions = [b for b in range(8) if (diff_bits >> b) & 1]
                samples.append({
                    "offset": i,
                    "address": f"0x{base_address + i:08X}",
                    "expected": f"0x{sb:02X}",
                    "actual": f"0x{rb:02X}",
                    "xor": f"0x{diff_bits:02X}",
                    "flipped_bits": flipped_positions,
                })

    mismatch_count = len(mismatch_offsets) + (max_len - min_len)
    is_match = (mismatch_count == 0)
    match_bytes = min_len - len(mismatch_offsets)
    match_rate = (match_bytes / max_len * 100.0) if max_len > 0 else 100.0

    return {
        "is_match": is_match,
        "source_length": len(source_bytes),
        "readback_length": len(readback_bytes),
        "mismatch_count": mismatch_count,
        "match_rate": round(match_rate, 4),
        "samples": samples,
        "mismatch_offsets": mismatch_offsets,
    }


def summarize_mismatch_ranges(mismatch_offsets: list, base_address: int = 0) -> list:
    """把不一致偏移聚合成连续地址区间，如 ['0xA0000120 ~ 0xA000015F (64 字节)']"""
    if not mismatch_offsets:
        return []
    ranges = []
    start = prev = mismatch_offsets[0]
    for off in mismatch_offsets[1:]:
        if off == prev + 1:
            prev = off
            continue
        ranges.append((start, prev))
        start = prev = off
    ranges.append((start, prev))
    result = []
    for s, e in ranges:
        length = e - s + 1
        if s == e:
            result.append(f"0x{base_address + s:08X} (1 字节)")
        else:
            result.append(f"0x{base_address + s:08X} ~ 0x{base_address + e:08X} ({length} 字节)")
    return result


def format_data_image(source_bytes: bytes, readback_bytes: bytes, base_address: int = 0,
                      mismatch_offsets: list = None, meta_lines: list = None, segments=None) -> str:
    """生成完整数据镜像比对文本：每行 16 字节，源程序 vs 芯片回读并排，逐行标 OK/NG。

    NG 行尾标注该行的差异偏移、期望/实测字节与翻转位；
    无源程序基准时（source_bytes 为空）只输出回读单栏镜像。
    """
    if segments:
        sections = []
        offset = 0
        for segment in segments:
            length = segment['length']
            sections.append(format_data_image(
                source_bytes[offset:offset + length], readback_bytes[offset:offset + length],
                segment['address'],
                [i - offset for i in (mismatch_offsets or []) if offset <= i < offset + length],
                meta_lines if offset == 0 else None))
            offset += length
        return '\n'.join(sections)
    offsets_set = set(mismatch_offsets or [])
    max_len = max(len(source_bytes), len(readback_bytes))
    has_source = len(source_bytes) > 0

    lines = []
    if meta_lines:
        lines.extend(meta_lines)
        lines.append("-" * 100)
    if has_source:
        lines.append("列说明: [物理地址]  源程序数据(16字节hex)  |  芯片回读数据(16字节hex)  |  比对状态")
    else:
        lines.append("列说明: [物理地址]  芯片回读数据(16字节hex)  (无源程序基准，仅回读存档)")
    lines.append("-" * 100)

    for row_start in range(0, max_len, 16):
        addr = base_address + row_start
        row_end = min(row_start + 16, max_len)
        rb_row = " ".join(f"{readback_bytes[i]:02X}" for i in range(row_start, min(row_end, len(readback_bytes))))

        if has_source:
            src_row = " ".join(f"{source_bytes[i]:02X}" for i in range(row_start, min(row_end, len(source_bytes))))
            row_offsets = [i for i in range(row_start, row_end) if i in offsets_set]
            # 回读长度不足源程序的部分也算差异
            if len(readback_bytes) < row_end:
                row_offsets.extend(i for i in range(max(row_start, len(readback_bytes)), row_end))
            if row_offsets:
                marks = []
                for i in row_offsets:
                    sb = source_bytes[i] if i < len(source_bytes) else None
                    rb = readback_bytes[i] if i < len(readback_bytes) else None
                    if sb is not None and rb is not None:
                        flipped = [b for b in range(8) if ((sb ^ rb) >> b) & 1]
                        marks.append(f"+0x{i - row_start:X}: {sb:02X}→{rb:02X} 翻转位{flipped}")
                    else:
                        marks.append(f"+0x{i - row_start:X}: 数据缺失")
                status = "NG  <<< " + "; ".join(marks)
            else:
                status = "OK"
            lines.append(f"0x{addr:08X}  {src_row:<47}  |  {rb_row:<47}  |  {status}")
        else:
            lines.append(f"0x{addr:08X}  {rb_row}")

    lines.append("=" * 100)
    return "\n".join(lines)


def extract_source_data_bytes(opj_path: str, max_size: int = None) -> bytes:
    """从 .opj 工程包中安全提取原始固件二进制字节流（.opd）"""
    if not os.path.isfile(opj_path):
        return b""
    try:
        if not zipfile.is_zipfile(opj_path):
            return b""
        with zipfile.ZipFile(opj_path, "r") as z:
            opd_name = next((n for n in z.namelist() if n.endswith(".opd")), None)
            if opd_name:
                with z.open(opd_name) as f:
                    if max_size and max_size > 0:
                        return f.read(max_size)
                    return f.read()
    except Exception:
        pass
    return b""


def record_burn_event(
    record_data: dict,
    base_dir: Optional[str] = None,
) -> dict:
    """
    持久化全息留痕归档：
    1. 追加到月度流水账 CSV 汇总表；
    2. 生成包含【物理地址+数据映像+比对Diff+硬件日志】的单板全息报告。
    """
    if base_dir is None:
        # 与 DBManager 保持一致：锚定到 exe 目录/项目根目录，避免依赖启动时的工作目录
        if getattr(sys, "frozen", False):
            root_dir = os.path.dirname(os.path.abspath(sys.executable))
        else:
            root_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        base_dir = os.path.join(root_dir, "logs", "burn_records")
    os.makedirs(base_dir, exist_ok=True)
    reports_dir = os.path.join(base_dir, "reports")
    os.makedirs(reports_dir, exist_ok=True)

    now = datetime.datetime.now()
    now_str = now.strftime("%Y-%m-%d %H:%M:%S")
    month_str = now.strftime("%Y%m")
    file_time_str = now.strftime("%Y%m%d_%H%M%S")

    sn = str(record_data.get("sn") or "NO_SN").strip()
    project = str(record_data.get("project_name") or "UNKNOWN").strip()
    source_file = str(record_data.get("source_file") or "UNKNOWN")
    chip_name = str(record_data.get("chip_name") or "UNKNOWN")
    addr_range = str(record_data.get("address_range") or "未知")
    exp_crc = str(record_data.get("expected_checksum") or "未知")
    act_crc = str(record_data.get("actual_checksum") or "未知")
    vcc = record_data.get("vcc_mv", 3300)
    duration = record_data.get("duration_sec", 0.0)
    result = str(record_data.get("result") or "FAIL").upper()
    diagnosis = str(record_data.get("diagnosis") or "无异常")

    # 规范比对判定：以逐字节物理回读比对为准。
    # 源程序 CRC（打包工具算法）与硬件 checksum（下载器算法）的算法/统计范围不同，
    # 实测同一数据两者也不相等，仅作交叉参考，不作为判定依据。
    diff_info = record_data.get("diff_details")
    checksum_match = record_data.get("checksum_match")
    if checksum_match is None:
        if diff_info:
            checksum_match = bool(diff_info.get("is_match"))
        elif exp_crc != "未知" and act_crc != "未知":
            # 无逐字节比对时回退到 CRC 参考比对（容差清洗十六进制）
            clean_exp = exp_crc.lower().lstrip("0x").zfill(8)
            clean_act = act_crc.lower().lstrip("0x").zfill(8)
            checksum_match = (clean_exp == clean_act)
        else:
            checksum_match = (result == "PASS")

    if checksum_match:
        match_str = "MATCH(逐字节一致)" if diff_info else "MATCH(一致)"
    else:
        match_str = "MISMATCH(数据不一致)" if diff_info else "MISMATCH(不一致/干扰)"

    # 完整数据镜像存档（OK/NG 都生成，NG 地址和数据在镜像中逐行标记）
    source_bytes = record_data.get("source_bytes") or b""
    readback_bytes = record_data.get("readback_bytes") or b""
    base_address = int(record_data.get("base_address") or 0)
    image_path = ""

    # 1. 写入单板全息报告
    safe_proj = re.sub(r"[^\w\-_.]", "_", os.path.basename(project))
    report_filename = f"{sn}_{file_time_str}_{safe_proj}.log"
    report_path = os.path.join(reports_dir, report_filename)

    if readback_bytes:
        images_dir = os.path.join(base_dir, "images")
        os.makedirs(images_dir, exist_ok=True)
        if diff_info and not diff_info.get("is_match", True):
            verdict = f"NG - 共 {diff_info.get('mismatch_count', 0)} 字节不一致 (吻合率 {diff_info.get('match_rate', 0)}%)"
        elif diff_info:
            verdict = "OK - 全部地址逐字节一致"
        else:
            verdict = "未比对（仅回读存档）"
        image_meta = [
            "=" * 100,
            "                     烧录数据完整镜像比对存档 (Full Image Diff)",
            "=" * 100,
            f"烧录时间: {now_str}",
            f"产品条码 (SN): {sn}",
            f"脱机工程文件: {project}",
            f"源程序原始文件: {source_file}",
            f"目标芯片型号: {chip_name}",
            f"基准物理地址: 0x{base_address:08X} | 源程序长度: {len(source_bytes)} 字节 | 回读长度: {len(readback_bytes)} 字节",
            f"比对结论: {verdict}",
        ]
        image_text = format_data_image(
            source_bytes, readback_bytes, base_address,
            mismatch_offsets=(diff_info or {}).get("mismatch_offsets", []),
            meta_lines=image_meta,
            segments=(diff_info or {}).get('segments'),
        )
        image_filename = f"{sn}_{file_time_str}_{safe_proj}_image.txt"
        image_path = os.path.join(images_dir, image_filename)
        with open(image_path, "w", encoding="utf-8") as f:
            f.write(image_text + "\n")

    report_lines = [
        "================================================================================",
        "                     力捷丰 SG400 烧录全息留痕与防呆比对报告                      ",
        "================================================================================",
        f"烧录时间: {now_str}",
        f"产品条码 (SN): {sn}",
        f"脱机工程文件: {project}",
        f"源程序原始文件: {source_file}",
        f"目标芯片型号: {chip_name}",
        f"供电电压 (Vcc): {vcc} mV | 耗时: {duration:.2f} s",
        f"物理扇区范围: {addr_range}",
        "--------------------------------------------------------------------------------",
        "【防干扰闭环校验】:",
        f"  - 源程序基准校验和: {exp_crc}",
        f"  - 硬件实测校验和:   {act_crc}",
        "  - 说明: 两个校验和由不同工具生成(打包CRC vs 下载器checksum), 算法与统计范围不同,",
        "          数值一般不相等, 仅作交叉参考; 比对结论以下方逐字节物理回读结果为准。",
        f"  - 一致性比对结论:   {match_str}",
        f"  - 最终测试判定:     {result}",
        f"  - 故障诊断信息:     {diagnosis}",
        "--------------------------------------------------------------------------------",
    ]

    # 若有逐字节 Diff 详情
    if diff_info:
        report_lines.append("【芯片物理回读 Byte-by-Byte 比对详情】:")
        report_lines.append(f"  - 比对吻合率: {diff_info.get('match_rate', 100)}%")
        report_lines.append(f"  - 异常字节数: {diff_info.get('mismatch_count', 0)}")
        samples = diff_info.get("samples", [])
        if samples:
            report_lines.append("  - 干扰位翻转样本分析 (前 50 处):")
            for sp in samples:
                report_lines.append(
                    f"    地址 {sp['address']}: 期望={sp['expected']}, 实测={sp['actual']} "
                    f"| 翻转位={sp['flipped_bits']}"
                )
        else:
            report_lines.append("  - 芯片实际物理数据与源程序 100% 绝对一致，物理写入完整无损。")
        report_lines.append("--------------------------------------------------------------------------------")

    # 完整镜像存档指引与 NG 地址区间汇总
    if image_path:
        report_lines.append("【完整数据镜像存档】:")
        report_lines.append(f"  - 镜像文件: {os.path.abspath(image_path)}")
        ranges = summarize_mismatch_ranges((diff_info or {}).get("mismatch_offsets", []), base_address)
        if ranges:
            report_lines.append(f"  - NG 不一致地址区间 (共 {len(ranges)} 段):")
            for r in ranges[:100]:
                report_lines.append(f"    {r}")
            if len(ranges) > 100:
                report_lines.append(f"    ... 其余 {len(ranges) - 100} 段详见镜像文件")
        else:
            report_lines.append("  - 全部地址区间比对均为 OK，镜像中逐行标记。")
        report_lines.append("--------------------------------------------------------------------------------")

    # 附加上位机/底层运行日志
    hw_log = record_data.get("hardware_log")
    if hw_log:
        report_lines.append("【力捷丰下载器底层运行回显日志 (/log/ch.log)】:")
        for line in hw_log.strip().splitlines():
            report_lines.append(f"  {line}")
        report_lines.append("--------------------------------------------------------------------------------")

    report_lines.append("================================================================================")
    with open(report_path, "w", encoding="utf-8") as f:
        f.write("\n".join(report_lines) + "\n")

    # 2. 追加到月度流水账 CSV（文件被 Excel/WPS 占用时只降级警告，
    #    不影响已生成的全息报告与镜像存档）
    csv_path = os.path.join(base_dir, f"burn_history_{month_str}.csv")
    csv_exists = os.path.isfile(csv_path)

    csv_headers = [
        "烧录时间", "产品条码", "脱机工程名", "源程序文件名", "芯片型号",
        "物理地址范围", "基准校验和", "实测校验和", "比对结果", "供电Vcc(mV)",
        "耗时(s)", "最终判定", "故障诊断", "全息报告路径"
    ]

    csv_row = [
        now_str, sn, project, source_file, chip_name,
        addr_range, exp_crc, act_crc, match_str, vcc,
        f"{duration:.2f}", result, diagnosis, os.path.abspath(report_path)
    ]

    csv_error = ""
    for attempt in range(3):
        try:
            with open(csv_path, "a", newline="", encoding="utf-8-sig") as f:
                writer = csv.writer(f)
                if not csv_exists:
                    writer.writerow(csv_headers)
                writer.writerow(csv_row)
            csv_error = ""
            break
        except OSError as exc:
            csv_error = str(exc)
            time.sleep(0.5)

    result_dict = {
        "csv_path": os.path.abspath(csv_path),
        "report_path": os.path.abspath(report_path),
        "image_path": os.path.abspath(image_path) if image_path else "",
        "match": checksum_match,
        "match_str": match_str,
        "result": result,
    }
    if csv_error:
        result_dict["csv_error"] = (
            f"流水账 CSV 写入失败(文件可能被 Excel/WPS 占用，请关闭后重烧或手动补录): {csv_error}"
        )
    return result_dict

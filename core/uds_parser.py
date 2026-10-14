"""
UDS 响应数据提取、ASCII 转换与多字节物理公式计算核心模块
支持从 ISO 14229 / ISO-TP 响应流中截取字节段，
完成 ASCII 解码、大小端拼接、线性比例偏移运算 (Scale & Offset) 或受限安全算术公式计算，
并支持将结果写入系统全局变量池。
"""

import re
from typing import Any, Dict, Optional


def parse_uds_rule_string(rule_str: str) -> Optional[Dict[str, Any]]:
    """
    解析 UDS 规则字符串为结构化字典。
    语法示例:
      ASCII(offset=3,len=17,var=ECU_VIN)
      CALC(offset=3,len=2,endian=big,signed=0,scale=0.1,offset_val=0,unit=V,var=BMS_PACK_VOLT)
      FORMULA(offset=3,len=2,endian=big,expr="(val*0.05)+12",unit=kPa,var=PRESSURE_VAL)
    """
    if not rule_str or not rule_str.strip():
        return None

    text = rule_str.strip()
    match = re.match(r"^([A-Za-z_]+)\((.*)\)$", text)
    if not match:
        return None

    mode = match.group(1).upper()
    args_content = match.group(2).strip()

    rule: Dict[str, Any] = {"mode": mode, "offset": 3, "length": 0, "var_name": ""}
    if not args_content:
        return rule

    # 提取 key=value 或 key="value"
    # 使用正则表达式支持带引号的字符串参数 (如 expr="(val * 0.1) + 5")
    param_pattern = re.compile(r'([A-Za-z_]+)\s*=\s*(?:"([^"]*)"|\'([^\']*)\'|([^,]+))')
    for m in param_pattern.finditer(args_content):
        key = m.group(1).strip().lower()
        val_str = m.group(2) if m.group(2) is not None else (
            m.group(3) if m.group(3) is not None else m.group(4)
        )
        val_str = val_str.strip() if val_str else ""

        if key in ("offset", "len", "length"):
            try:
                rule["offset" if key == "offset" else "length"] = int(val_str, 0)
            except ValueError:
                pass
        elif key in ("var", "var_name"):
            rule["var_name"] = val_str
        elif key in ("endian", "byteorder"):
            rule["endian"] = val_str.lower()
        elif key in ("signed", "is_signed"):
            rule["signed"] = val_str.lower() in ("1", "true", "yes")
        elif key == "scale":
            try:
                rule["scale"] = float(val_str)
            except ValueError:
                pass
        elif key in ("offset_val", "bias", "intercept"):
            try:
                rule["offset_val"] = float(val_str)
            except ValueError:
                pass
        elif key == "unit":
            rule["unit"] = val_str
        elif key in ("expr", "formula"):
            rule["formula"] = val_str

    return rule


def format_uds_rule_string(rule: Optional[Dict[str, Any]]) -> str:
    """
    将规则字典格式化为简洁易读的单行规则字符串。
    """
    if not rule or not isinstance(rule, dict):
        return ""
    mode = str(rule.get("mode", "")).upper()
    if mode not in ("ASCII", "CALC", "NUMERIC", "FORMULA"):
        return ""

    offset = int(rule.get("offset", 3))
    length = int(rule.get("length", 0))
    var_name = str(rule.get("var_name", "")).strip()

    parts = [f"offset={offset}", f"len={length}"]

    if mode == "ASCII":
        if var_name:
            parts.append(f"var={var_name}")
        return f"ASCII({','.join(parts)})"

    endian = str(rule.get("endian", "big")).lower()
    if endian != "big":
        parts.append(f"endian={endian}")
    if rule.get("signed"):
        parts.append("signed=1")

    if mode in ("CALC", "NUMERIC"):
        scale = rule.get("scale", 1.0)
        offset_val = rule.get("offset_val", 0.0)
        unit = str(rule.get("unit", "")).strip()
        parts.append(f"scale={scale}")
        if offset_val != 0.0:
            parts.append(f"offset_val={offset_val}")
        if unit:
            parts.append(f'unit="{unit}"')
        if var_name:
            parts.append(f"var={var_name}")
        return f"CALC({','.join(parts)})"

    elif mode == "FORMULA":
        formula = str(rule.get("formula", "val")).strip()
        unit = str(rule.get("unit", "")).strip()
        parts.append(f'expr="{formula}"')
        if unit:
            parts.append(f'unit="{unit}"')
        if var_name:
            parts.append(f"var={var_name}")
        return f"FORMULA({','.join(parts)})"

    return ""


def parse_uds_response_data(raw_bytes: bytes, rule: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """
    执行 UDS 响应数据解析
    :param raw_bytes: UDS 完整响应原始字节 (如 0x62 0xF1 0x90 0x4C ...)
    :param rule: 解析规则字典
    :return: 包含 success, mode, value, display, var_name, error 的字典
    """
    if not rule or not isinstance(rule, dict):
        return {"success": False, "error": "规则未配置"}

    mode = str(rule.get("mode", "NONE")).upper()
    offset = int(rule.get("offset", 3))
    length = int(rule.get("length", 0))
    var_name = str(rule.get("var_name", "")).strip()

    if offset >= len(raw_bytes):
        return {
            "success": False,
            "error": f"偏移量 {offset} 超出响应长度 (实际长度={len(raw_bytes)})"
        }

    if length > 0:
        chunk = raw_bytes[offset : offset + length]
        if len(chunk) < length:
            return {
                "success": False,
                "error": f"截取字节不足: 期望 {length} 字节，实际仅有 {len(chunk)} 字节"
            }
    else:
        chunk = raw_bytes[offset :]

    if not chunk:
        return {"success": False, "error": "截取到的有效负载为空"}

    # 1. 模式 A: ASCII 字符串解码
    if mode == "ASCII":
        null_pos = chunk.find(b"\x00")
        valid_chunk = chunk[:null_pos] if null_pos >= 0 else chunk
        while valid_chunk and valid_chunk[-1] in (0xAA, 0x55, 0xFF):
            valid_chunk = valid_chunk[:-1]
        text = valid_chunk.decode("utf-8", errors="ignore").strip()
        return {
            "success": True,
            "mode": "ASCII",
            "raw_hex": chunk.hex(" ").upper(),
            "value": text,
            "display": f"{text}",
            "var_name": var_name,
        }

    # 2. 模式 B: 工业标准比例和偏移加减乘除 (val * scale + offset_val)
    elif mode in ("CALC", "NUMERIC"):
        endian = str(rule.get("endian", "big")).lower()
        signed = bool(rule.get("signed", False))
        scale = float(rule.get("scale", 1.0))
        offset_val = float(rule.get("offset_val", 0.0))
        unit = str(rule.get("unit", "")).strip()

        try:
            raw_int = int.from_bytes(chunk, byteorder=endian, signed=signed)
            phys_val = round(raw_int * scale + offset_val, 4)
        except Exception as e:
            return {"success": False, "error": f"字节合成数值失败: {e}"}

        unit_str = f" {unit}" if unit else ""
        return {
            "success": True,
            "mode": "CALC",
            "raw_hex": chunk.hex(" ").upper(),
            "raw_int": raw_int,
            "value": phys_val,
            "display": f"{phys_val}{unit_str}",
            "formula_desc": f"{raw_int} * {scale} + ({offset_val}) = {phys_val}{unit_str}",
            "var_name": var_name,
        }

    # 3. 模式 C: 灵活受限算术公式
    elif mode == "FORMULA":
        endian = str(rule.get("endian", "big")).lower()
        signed = bool(rule.get("signed", False))
        formula_expr = str(rule.get("formula", "val")).strip()
        unit = str(rule.get("unit", "")).strip()

        try:
            raw_int = int.from_bytes(chunk, byteorder=endian, signed=signed)
        except Exception as e:
            return {"success": False, "error": f"字节合成整数失败: {e}"}

        safe_dict = {
            "val": raw_int,
            "x": raw_int,
            "round": round,
            "abs": abs,
            "min": min,
            "max": max,
            "int": int,
            "float": float,
        }
        try:
            # 严格受限 eval，禁用内置危险函数
            calc_val = eval(formula_expr, {"__builtins__": None}, safe_dict)
            phys_val = round(float(calc_val), 4)
        except Exception as e:
            return {"success": False, "error": f"公式计算异常 '{formula_expr}': {e}"}

        unit_str = f" {unit}" if unit else ""
        return {
            "success": True,
            "mode": "FORMULA",
            "raw_hex": chunk.hex(" ").upper(),
            "raw_int": raw_int,
            "value": phys_val,
            "display": f"{phys_val}{unit_str}",
            "formula_desc": f"{formula_expr.replace('val', str(raw_int))} = {phys_val}{unit_str}",
            "var_name": var_name,
        }

    return {"success": False, "error": f"不支持的解析模式: {mode}"}


def parse_uds_array_data(
    raw_bytes: bytes,
    offset: int = 3,
    item_size: int = 2,
    count: Optional[int] = None,
    endian: str = "big",
    signed: bool = False,
    scale: float = 1.0,
    offset_val: float = 0.0,
    unit: str = "",
    var_prefix: str = "CSC_CELL_"
) -> Dict[str, Any]:
    """
    针对 BMS 电芯单体电压、单体温度等连续数组的高性能批量切分函数。
    :param raw_bytes: 原始完整报文
    :param offset: 数组数据起始偏移 (默认 3)
    :param item_size: 每个元素的字节大小 (如单体电压 2B, 温度 1B)
    :param count: 期望元素总数 (None 则截取至数据末尾)
    :param endian: 端序 (big / little)
    :param signed: 是否有符号
    :param scale: 比例系数
    :param offset_val: 偏移补偿
    :param unit: 工程单位
    :param var_prefix: 写入变量池时的前缀 (如 CSC_CELL_0, CSC_CELL_1 ...)
    :return: 包含 elements, count, variables_map, success 的字典
    """
    if offset >= len(raw_bytes):
        return {"success": False, "error": f"起始偏移 {offset} 超出响应长度 {len(raw_bytes)}"}

    data_payload = raw_bytes[offset:]
    total_avail = len(data_payload)
    if item_size <= 0:
        return {"success": False, "error": f"非法元素尺寸: {item_size}"}

    max_possible = total_avail // item_size
    actual_count = max_possible if count is None else min(count, max_possible)

    elements = []
    variables_map = {}

    for i in range(actual_count):
        start = i * item_size
        chunk = data_payload[start : start + item_size]
        raw_int = int.from_bytes(chunk, byteorder=endian, signed=signed)
        phys = round(raw_int * scale + offset_val, 4)
        elements.append(phys)
        if var_prefix:
            variables_map[f"{var_prefix}{i}"] = phys

    unit_str = f" {unit}" if unit else ""
    return {
        "success": True,
        "mode": "ARRAY",
        "count": len(elements),
        "elements": elements,
        "display": f"共解析 {len(elements)} 个元素 (首={elements[0] if elements else '--'}{unit_str}, 尾={elements[-1] if elements else '--'}{unit_str})",
        "variables_map": variables_map,
    }


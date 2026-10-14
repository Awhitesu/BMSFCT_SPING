"""
UDS 响应数据提取、ASCII 解码与字节算术公式计算模拟器
演示汽车诊断场景下，如何从原始 UDS 报文响应流中提取并转换目标数据。
"""

import re


def parse_uds_response_data(raw_bytes: bytes, rule: dict) -> dict:
    """
    UDS 响应数据解析核心处理器
    :param raw_bytes: UDS 完整响应字节流 (如 b'\x62\xF1\x90LFVBA...')
    :param rule: 数据解析规则字典
    :return: 解析结果字典 (含模式、原始Hex、工程物理值、格式化文案等)
    """
    mode = str(rule.get("mode", "NONE")).upper()
    offset = int(rule.get("offset", 3)) # 默认跳过 SID (1字节) + DID (2字节)
    length = int(rule.get("length", 0)) # 0 表示截取到末尾
    var_name = str(rule.get("var_name", "")).strip()

    # 1. 范围校验与字节切片
    if offset >= len(raw_bytes):
        return {"success": False, "error": f"起始偏移量 {offset} 超出响应总长度 {len(raw_bytes)}"}

    if length > 0:
        chunk = raw_bytes[offset : offset + length]
        if len(chunk) < length:
            return {"success": False, "error": f"截取字节不足: 期望 {length} 字节，实际仅剩 {len(chunk)} 字节"}
    else:
        chunk = raw_bytes[offset :]

    if not chunk:
        return {"success": False, "error": "截取数据段为空"}

    # 2. 模式 A: ASCII 字符串提取
    if mode == "ASCII":
        text = chunk.decode("utf-8", errors="ignore").replace("\x00", "").strip()
        return {
            "success": True,
            "mode": "ASCII",
            "raw_hex": chunk.hex(" ").upper(),
            "value": text,
            "display": f"{text} (ASCII 字符串)",
            "var_name": var_name
        }

    # 3. 模式 B: 工业标准比例系数与偏移量计算 (val * scale + offset)
    elif mode in ("CALC", "NUMERIC"):
        endian = str(rule.get("endian", "big")).lower()
        signed = bool(rule.get("signed", False))
        scale = float(rule.get("scale", 1.0))
        offset_val = float(rule.get("offset_val", 0.0))
        unit = str(rule.get("unit", "")).strip()

        raw_int = int.from_bytes(chunk, byteorder=endian, signed=signed)
        phys_val = round(raw_int * scale + offset_val, 4)

        return {
            "success": True,
            "mode": "CALC",
            "raw_hex": chunk.hex(" ").upper(),
            "raw_int": raw_int,
            "value": phys_val,
            "display": f"{phys_val} {unit}".strip(),
            "formula_desc": f"{raw_int} * {scale} + ({offset_val}) = {phys_val} {unit}".strip(),
            "var_name": var_name
        }

    # 4. 模式 C: 自定义数学算式 (如 (val * 0.1) - 40)
    elif mode == "FORMULA":
        endian = str(rule.get("endian", "big")).lower()
        signed = bool(rule.get("signed", False))
        formula_expr = str(rule.get("formula", "val")).strip()
        unit = str(rule.get("unit", "")).strip()

        raw_int = int.from_bytes(chunk, byteorder=endian, signed=signed)
        safe_dict = {"val": raw_int, "x": raw_int, "round": round, "abs": abs, "min": min, "max": max}
        try:
            calc_val = eval(formula_expr, {"__builtins__": None}, safe_dict)
            phys_val = round(float(calc_val), 4)
        except Exception as e:
            return {"success": False, "error": f"公式计算异常: {e}"}

        return {
            "success": True,
            "mode": "FORMULA",
            "raw_hex": chunk.hex(" ").upper(),
            "raw_int": raw_int,
            "value": phys_val,
            "display": f"{phys_val} {unit}".strip(),
            "formula_desc": f"{formula_expr.replace('val', str(raw_int))} = {phys_val} {unit}".strip(),
            "var_name": var_name
        }

    return {"success": False, "error": f"未知的解析模式: {mode}"}


def run_simulation():
    test_cases = [
        {
            "name": "1. 读取车辆 VIN 码 (22 F1 90 转 ASCII)",
            "request_hex": "22 F1 90",
            # 62 F1 90 + 17位VIN (LFVBA21A1N0000001)
            "response_hex": "62 F1 90 4C 46 56 42 41 32 31 41 31 4E 30 30 30 30 30 30 31",
            "rule": {
                "mode": "ASCII",
                "offset": 3,
                "length": 17,
                "var_name": "ECU_VIN"
            }
        },
        {
            "name": "2. 读取软件固件版本号 (22 F1 89 转 ASCII)",
            "request_hex": "22 F1 89",
            # 62 F1 89 + SW_V01.02
            "response_hex": "62 F1 89 53 57 5F 56 30 31 2E 30 32",
            "rule": {
                "mode": "ASCII",
                "offset": 3,
                "length": 0,
                "var_name": "SW_VERSION"
            }
        },
        {
            "name": "3. 读取动力电池总电压 (22 01 01 拼接双字节大端，0.1V 分辨率)",
            "request_hex": "22 01 01",
            # 62 01 01 + 0E 10 (0x0E10 = 3600)
            "response_hex": "62 01 01 0E 10",
            "rule": {
                "mode": "CALC",
                "offset": 3,
                "length": 2,
                "endian": "big",
                "signed": False,
                "scale": 0.1,
                "offset_val": 0.0,
                "unit": "V",
                "var_name": "BMS_PACK_VOLT"
            }
        },
        {
            "name": "4. 读取充放电电流 (22 01 02 双字节有符号/带偏移量，分辨率0.1A，偏移-40A)",
            "request_hex": "22 01 02",
            # 62 01 02 + 03 E8 (0x03E8 = 1000, 1000*0.1 - 40 = 60.0A)
            "response_hex": "62 01 02 03 E8",
            "rule": {
                "mode": "CALC",
                "offset": 3,
                "length": 2,
                "endian": "big",
                "signed": False,
                "scale": 0.1,
                "offset_val": -40.0,
                "unit": "A",
                "var_name": "BMS_CURRENT"
            }
        },
        {
            "name": "5. 读取最高电芯温度 (22 01 03 单字节，分辨率1℃，偏移-40℃)",
            "request_hex": "22 01 03",
            # 62 01 03 + 3C (0x3C = 60, 60 - 40 = 20℃)
            "response_hex": "62 01 03 3C",
            "rule": {
                "mode": "CALC",
                "offset": 3,
                "length": 1,
                "endian": "big",
                "signed": False,
                "scale": 1.0,
                "offset_val": -40.0,
                "unit": "℃",
                "var_name": "BMS_MAX_TEMP"
            }
        },
        {
            "name": "6. 自定义灵活算术表达式计算 (22 01 04 自由公式: (val * 0.05) + 12)",
            "request_hex": "22 01 04",
            # 62 01 04 + 02 58 (0x0258 = 600, 600*0.05+12 = 42.0)
            "response_hex": "62 01 04 02 58",
            "rule": {
                "mode": "FORMULA",
                "offset": 3,
                "length": 2,
                "endian": "big",
                "formula": "(val * 0.05) + 12",
                "unit": "kPa",
                "var_name": "PRESSURE_VAL"
            }
        }
    ]

    print("================================================================================")
    print("                    UDS 响应数据提取与数值转换模拟演示                          ")
    print("================================================================================")

    simulated_variables = {}

    for case in test_cases:
        print(f"\n【用例】{case['name']}")
        print(f"  -> 请求PDU: {case['request_hex']}")
        print(f"  -> 实际响应: {case['response_hex']}")
        
        raw_bytes = bytes.fromhex(case['response_hex'].replace(" ", ""))
        result = parse_uds_response_data(raw_bytes, case['rule'])
        
        if result["success"]:
            print(f"  -> 提取字节: {result['raw_hex']}")
            if result["mode"] == "ASCII":
                print(f"  -> 解码结果: \"{result['value']}\" (ASCII 文本)")
            elif result["mode"] == "CALC":
                print(f"  -> 原始整数: {result['raw_int']} (0x{result['raw_hex']})")
                print(f"  -> 计算公式: {result['formula_desc']}")
                print(f"  -> 最终数值: {result['display']}")
            elif result["mode"] == "FORMULA":
                print(f"  -> 原始整数: {result['raw_int']} (0x{result['raw_hex']})")
                print(f"  -> 算式计算: {result['formula_desc']}")
                print(f"  -> 最终数值: {result['display']}")
            
            var = result['var_name']
            if var:
                simulated_variables[var] = result['value']
                print(f"  -> 变量写回: self.variables['{var}'] = {result['value']}")
        else:
            print(f"  -> 解析失败: {result['error']}")

    print("\n--------------------------------------------------------------------------------")
    print("全局变量池当前状态 (写回后可供后续工步、MES 与判断使用):")
    for k, v in simulated_variables.items():
        print(f"  - {k} = {v} ({type(v).__name__})")
    print("================================================================================")


if __name__ == "__main__":
    run_simulation()

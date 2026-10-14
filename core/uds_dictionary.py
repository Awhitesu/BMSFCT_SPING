"""
UDS DID 协议字典与模板管理模块
用于管理、导入、导出和持久化常见与自定义的 UDS DID 指令定义（尤其是 BMS 电池管理系统诊断指令）。
支持从 CSV / JSON 批量导入成百上千条企业内部 DID 协议。
"""

import os
import csv
import json
from typing import Dict, List, Optional, Any

from .uds_parser import format_uds_rule_string


# 行业通用与标准 BMS (电池管理系统) 经典 DID 预设字典
DEFAULT_BMS_DIDS: List[Dict[str, Any]] = [
    {
        "name": "🔋 动力电池总压 (DID 0x0101, 2B大端*0.1V)",
        "did": "01 01",
        "service": "22 按标识符读取数据",
        "request": "22 01 01",
        "expected": "62 01 01 0E 10",
        "mode": "CALC",
        "offset": 3,
        "length": 2,
        "endian": "big",
        "signed": False,
        "scale": 0.1,
        "offset_val": 0.0,
        "unit": "V",
        "var_name": "BMS_PACK_VOLT",
    },
    {
        "name": "⚡ 充放电总电流 (DID 0x0102, 2B有符号*0.1A)",
        "did": "01 02",
        "service": "22 按标识符读取数据",
        "request": "22 01 02",
        "expected": "62 01 02 FF 38",
        "mode": "CALC",
        "offset": 3,
        "length": 2,
        "endian": "big",
        "signed": True,
        "scale": 0.1,
        "offset_val": 0.0,
        "unit": "A",
        "var_name": "BMS_PACK_CURR",
    },
    {
        "name": "📊 电池包荷电状态 SOC (DID 0x0103, 1B*0.4%)",
        "did": "01 03",
        "service": "22 按标识符读取数据",
        "request": "22 01 03",
        "expected": "62 01 03 7D",
        "mode": "CALC",
        "offset": 3,
        "length": 1,
        "endian": "big",
        "signed": False,
        "scale": 0.4,
        "offset_val": 0.0,
        "unit": "%",
        "var_name": "BMS_SOC",
    },
    {
        "name": "📊 电池包健康状态 SOH (DID 0x0104, 1B*0.4%)",
        "did": "01 04",
        "service": "22 按标识符读取数据",
        "request": "22 01 04",
        "expected": "62 01 04 FA",
        "mode": "CALC",
        "offset": 3,
        "length": 1,
        "endian": "big",
        "signed": False,
        "scale": 0.4,
        "offset_val": 0.0,
        "unit": "%",
        "var_name": "BMS_SOH",
    },
    {
        "name": "⚡ 单体最高电压 (DID 0x0105, 2B大端*1mV)",
        "did": "01 05",
        "service": "22 按标识符读取数据",
        "request": "22 01 05",
        "expected": "62 01 05 0C E4",
        "mode": "CALC",
        "offset": 3,
        "length": 2,
        "endian": "big",
        "signed": False,
        "scale": 1.0,
        "offset_val": 0.0,
        "unit": "mV",
        "var_name": "BMS_MAX_CELL_VOLT",
    },
    {
        "name": "⚡ 单体最低电压 (DID 0x0106, 2B大端*1mV)",
        "did": "01 06",
        "service": "22 按标识符读取数据",
        "request": "22 01 06",
        "expected": "62 01 06 0C B2",
        "mode": "CALC",
        "offset": 3,
        "length": 2,
        "endian": "big",
        "signed": False,
        "scale": 1.0,
        "offset_val": 0.0,
        "unit": "mV",
        "var_name": "BMS_MIN_CELL_VOLT",
    },
    {
        "name": "🌡️ 单体最高温度 (DID 0x0107, 1B偏移-40℃)",
        "did": "01 07",
        "service": "22 按标识符读取数据",
        "request": "22 01 07",
        "expected": "62 01 07 41",
        "mode": "CALC",
        "offset": 3,
        "length": 1,
        "endian": "big",
        "signed": False,
        "scale": 1.0,
        "offset_val": -40.0,
        "unit": "℃",
        "var_name": "BMS_MAX_CELL_TEMP",
    },
    {
        "name": "🌡️ 单体最低温度 (DID 0x0108, 1B偏移-40℃)",
        "did": "01 08",
        "service": "22 按标识符读取数据",
        "request": "22 01 08",
        "expected": "62 01 08 3E",
        "mode": "CALC",
        "offset": 3,
        "length": 1,
        "endian": "big",
        "signed": False,
        "scale": 1.0,
        "offset_val": -40.0,
        "unit": "℃",
        "var_name": "BMS_MIN_CELL_TEMP",
    },
    {
        "name": "🛡️ 正极绝缘阻抗 (DID 0x0109, 2B大端*1kΩ)",
        "did": "01 09",
        "service": "22 按标识符读取数据",
        "request": "22 01 09",
        "expected": "62 01 09 13 88",
        "mode": "CALC",
        "offset": 3,
        "length": 2,
        "endian": "big",
        "signed": False,
        "scale": 1.0,
        "offset_val": 0.0,
        "unit": "kΩ",
        "var_name": "BMS_POS_ISO",
    },
    {
        "name": "🛡️ 负极绝缘阻抗 (DID 0x010A, 2B大端*1kΩ)",
        "did": "01 0A",
        "service": "22 按标识符读取数据",
        "request": "22 01 0A",
        "expected": "62 01 0A 13 88",
        "mode": "CALC",
        "offset": 3,
        "length": 2,
        "endian": "big",
        "signed": False,
        "scale": 1.0,
        "offset_val": 0.0,
        "unit": "kΩ",
        "var_name": "BMS_NEG_ISO",
    },
    {
        "name": "🏷️ BMS 软件版本号 (DID 0xF189, ASCII)",
        "did": "F1 89",
        "service": "22 按标识符读取数据",
        "request": "22 F1 89",
        "expected": "62 F1 89 56 31 2E 30 2E 31",
        "mode": "ASCII",
        "offset": 3,
        "length": 0,
        "endian": "big",
        "signed": False,
        "scale": 1.0,
        "offset_val": 0.0,
        "unit": "",
        "var_name": "BMS_SW_VER",
    },
    {
        "name": "🏷️ BMS 硬件版本号 (DID 0xF193, ASCII)",
        "did": "F1 93",
        "service": "22 按标识符读取数据",
        "request": "22 F1 93",
        "expected": "62 F1 93 48 57 5F 56 32",
        "mode": "ASCII",
        "offset": 3,
        "length": 0,
        "endian": "big",
        "signed": False,
        "scale": 1.0,
        "offset_val": 0.0,
        "unit": "",
        "var_name": "BMS_HW_VER",
    },
    {
        "name": "🚗 车辆识别代码 VIN (DID 0xF190, 17B ASCII)",
        "did": "F1 90",
        "service": "22 按标识符读取数据",
        "request": "22 F1 90",
        "expected": "62 F1 90 4C 46 56 32 31 32 30 32 34 30 39 32 38 30 30 31",
        "mode": "ASCII",
        "offset": 3,
        "length": 17,
        "endian": "big",
        "signed": False,
        "scale": 1.0,
        "offset_val": 0.0,
        "unit": "",
        "var_name": "ECU_VIN",
    },
]


def build_simple_uds_config(item: Dict[str, Any]) -> Dict[str, str]:
    """把 DID 模板转换为简易模式可直接使用的请求、响应前缀和解析规则。"""
    request = str(item.get("request", "")).strip().upper()
    tokens = request.replace(",", " ").split()
    if not tokens:
        raise ValueError("模板缺少 UDS 请求")
    try:
        values = [int(token, 16) for token in tokens]
    except ValueError as exc:
        raise ValueError(f"模板请求格式错误: {request}") from exc
    if not 0 <= values[0] <= 0xBF:
        raise ValueError(f"UDS 服务号超出范围: 0x{values[0]:02X}")

    sid = values[0]
    echo_lengths = {
        0x10: 1, 0x11: 1, 0x14: 0, 0x19: 1, 0x22: 2, 0x23: 0,
        0x27: 1, 0x28: 1, 0x2A: 0, 0x2C: 3, 0x2E: 2, 0x2F: 3,
        0x31: 3, 0x34: 0, 0x36: 1, 0x37: 0, 0x3D: 0, 0x3E: 1,
        0x83: 1, 0x84: 0, 0x85: 1, 0x86: 1, 0x87: 1,
    }
    echoed = values[1:1 + echo_lengths.get(sid, 0)]
    if echoed and sid in {0x10, 0x11, 0x19, 0x27, 0x28, 0x3E, 0x83, 0x85, 0x86, 0x87}:
        echoed[0] &= 0x7F
    expected = " ".join(f"{value:02X}" for value in [((sid + 0x40) & 0xFF), *echoed])

    return {
        "name": str(item.get("name", "UDS 数据读取")).strip(),
        "request": request,
        "expected": expected,
        "rule": format_uds_rule_string(item),
        "variable": str(item.get("var_name", "")).strip(),
        "unit": str(item.get("unit", "")).strip(),
    }


class UDSDictionaryManager:
    """DID 协议字典管理器，支持本地持久化、合并与 CSV 批量导入导出"""

    def __init__(self, config_dir: Optional[str] = None):
        if config_dir is None:
            # 默认保存在应用 config/ 目录下
            base_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
            config_dir = os.path.join(base_dir, "config")
        self.config_dir = config_dir
        self.json_path = os.path.join(self.config_dir, "uds_did_dictionary.json")
        self._items: List[Dict[str, Any]] = []
        self.load()

    def get_all_items(self) -> List[Dict[str, Any]]:
        return list(self._items)

    def load(self):
        """加载自定义字典并与默认字典合并"""
        items_dict = {item.get("name", ""): dict(item) for item in DEFAULT_BMS_DIDS}

        if os.path.isfile(self.json_path):
            try:
                with open(self.json_path, "r", encoding="utf-8") as f:
                    custom_items = json.load(f)
                    if isinstance(custom_items, list):
                        for item in custom_items:
                            if isinstance(item, dict) and item.get("name"):
                                items_dict[item["name"]] = item
            except Exception:
                pass

        self._items = list(items_dict.values())

    def save(self):
        """持久化当前字典到 JSON 文件"""
        try:
            os.makedirs(self.config_dir, exist_ok=True)
            with open(self.json_path, "w", encoding="utf-8") as f:
                json.dump(self._items, f, ensure_ascii=False, indent=2)
        except Exception:
            pass

    def add_or_update_item(self, item: Dict[str, Any]) -> bool:
        """新增或更新一条 DID 定义"""
        name = item.get("name", "").strip()
        if not name:
            return False

        updated = False
        for i, existing in enumerate(self._items):
            if existing.get("name") == name:
                self._items[i] = dict(item)
                updated = True
                break

        if not updated:
            self._items.append(dict(item))

        self.save()
        return True

    def remove_item(self, name: str) -> bool:
        for i, item in enumerate(self._items):
            if item.get("name") == name:
                self._items.pop(i)
                self.save()
                return True
        return False

    def import_from_csv(self, csv_path: str) -> int:
        """
        从 CSV 文件批量导入 DID 列表
        支持的表头字段 (不区分大小写):
        name, did, request, expected, mode, offset, length, endian, signed, scale, offset_val, unit, var_name
        :return: 成功导入的条目数
        """
        if not os.path.isfile(csv_path):
            return 0

        imported_count = 0
        with open(csv_path, "r", encoding="utf-8-sig") as f:
            reader = csv.DictReader(f)
            for row in reader:
                # 键名小写归一化
                norm_row = {k.strip().lower(): (v.strip() if v else "") for k, v in row.items() if k}
                name = norm_row.get("name") or norm_row.get("名称")
                if not name:
                    continue

                did = norm_row.get("did") or norm_row.get("标识符", "")
                request = norm_row.get("request") or norm_row.get("请求", f"22 {did}".strip())
                expected = norm_row.get("expected") or norm_row.get("期望响应", f"62 {did}".strip())
                mode = (norm_row.get("mode") or norm_row.get("模式", "CALC")).upper()
                if mode not in ("ASCII", "CALC", "FORMULA"):
                    mode = "CALC"

                try:
                    offset = int(norm_row.get("offset") or norm_row.get("偏移", 3))
                except ValueError:
                    offset = 3

                try:
                    length = int(norm_row.get("length") or norm_row.get("长度", 2))
                except ValueError:
                    length = 2

                endian = (norm_row.get("endian") or norm_row.get("端序", "big")).lower()
                signed = norm_row.get("signed") in ("1", "true", "yes", "有符号")

                try:
                    scale = float(norm_row.get("scale") or norm_row.get("比例", 1.0))
                except ValueError:
                    scale = 1.0

                try:
                    offset_val = float(norm_row.get("offset_val") or norm_row.get("偏移量", 0.0))
                except ValueError:
                    offset_val = 0.0

                unit = norm_row.get("unit") or norm_row.get("单位", "")
                var_name = norm_row.get("var_name") or norm_row.get("变量名", "")

                item = {
                    "name": name,
                    "did": did,
                    "service": "22 按标识符读取数据",
                    "request": request,
                    "expected": expected,
                    "mode": mode,
                    "offset": offset,
                    "length": length,
                    "endian": endian,
                    "signed": signed,
                    "scale": scale,
                    "offset_val": offset_val,
                    "unit": unit,
                    "var_name": var_name,
                }
                if self.add_or_update_item(item):
                    imported_count += 1

        return imported_count

    def export_to_csv(self, csv_path: str):
        """将当前字典完整导出为 CSV"""
        fieldnames = [
            "name", "did", "request", "expected", "mode", "offset", "length",
            "endian", "signed", "scale", "offset_val", "unit", "var_name"
        ]
        with open(csv_path, "w", encoding="utf-8-sig", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            for item in self._items:
                row = {k: item.get(k, "") for k in fieldnames}
                writer.writerow(row)

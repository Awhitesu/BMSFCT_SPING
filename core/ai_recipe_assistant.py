"""AI 配方助手：调用 OpenAI 兼容大模型 API（默认 Kimi），按自然语言指令编辑配方 JSON。

工作流程：
1. 将当前配方 JSON + 结构说明 + 用户指令发给模型；
2. 要求模型返回完整的修改后配方 JSON（只允许 JSON，不要解释）；
3. 本地严格校验结构，不合法则把错误反馈给模型重试一次；
4. 返回新配方与人类可读的变更摘要，由界面预览、用户确认后才落盘。
"""

import copy
import json
import re
from typing import Any, Dict, List, Tuple

import requests

from core.recipe_knowledge import STEP_GRAMMAR_DOC

DEFAULT_BASE_URL = "https://api.moonshot.cn/v1"
DEFAULT_MODEL = "kimi-k3"

STANDARD_TYPES = ("数值", "字符串", "不判断")
RETRY_COUNTS = ("不复测", "复测1次", "复测3次")
FAIL_STRATEGIES = ("失败停止", "忽略继续", "重试3次")
CAN_MODES = ("经典 CAN", "CAN-FD", "CAN-FD + BRS")


class AIRecipeError(Exception):
    """AI 配方编辑失败（网络、API、返回格式或校验错误）"""


SYSTEM_PROMPT = """你是一名 BMS 测试配方编辑助手。用户会给你一份测试配方 JSON 和一条修改指令，你需要输出修改后的【完整配方 JSON】。

配方 JSON 结构说明：
- name: 配方名称（禁止修改）
- recipe_number: 整数，PLC 配方号
- plc_recipe_register: 整数
- sn_regex: 条码正则表达式字符串
- can_config: {"channel": "PCAN_USBBUS1"~"PCAN_USBBUS16", "mode": "经典 CAN"|"CAN-FD"|"CAN-FD + BRS", "nominal_bitrate": int, "data_bitrate": int}
- items: 测试项数组，每项：
  - name: 测试项名称
  - mode: "范围判定"（数值类）或 "字符串比对"（字符串类）
  - min / max: 字符串形式的下限/上限；字符串比对时 min 为期望字符串，max 用 "--"；不判断时都用 "--"
  - standard_type: "数值" | "字符串" | "不判断"
  - retry_count: "不复测" | "复测1次" | "复测3次"
  - unit: 单位字符串（如 "mA"、"V"），无单位用 "NULL"
  - sub_steps: 子工步数组，每个子工步：
    - name: 显示名称，格式通常为 "设备-动作 (参数)"
    - action: 动作名称（如 "设置参数"、"回读数据"、"闭合勾选通道"、"固定延时"、"查找刷写文件"、"启动刷写"）
    - params: 参数字符串
    - device: 设备名称
    - type: 子工步类型（如 "设置仪表"、"读取仪表"、"继电器控制"、"等待"、"APP刷写配置"、"APP刷写"、"CAN发送"、"CAN接收"、"CAN交互"、"变量操作"、"公式计算"）
    - is_judgment: 布尔值，该子工步的回读值是否作为测试项判定依据（每个需要判定的测试项应有且仅有一个 true）
    - fail_strategy: "失败停止" | "忽略继续" | "重试3次"

{grammar}

规则：
1. 只输出 JSON 本体，不要输出 markdown 代码块标记、注释或任何解释文字；
2. 未涉及修改的部分必须原样保留，不得擅自改动；
3. name 字段绝对禁止修改；
4. 每个测试项必须包含完整字段和完整的 sub_steps 数组；
5. 子工步的 device / action / params 必须严格符合上方语法规则，不得发明新设备、新动作或新参数格式；
6. 如果指令无法理解或无法执行，输出 {"error": "原因说明"}。"""


def _route_sub_type(sub: Dict[str, Any]) -> str:
    """镜像 core/engine.py 的关键字路由，推导子工步的真实类型（顺序即优先级）"""
    t = str(sub.get("type", ""))
    a = str(sub.get("action", ""))
    d = str(sub.get("device", ""))
    p = str(sub.get("params", ""))
    if "APP刷写配置" in t or ("APP刷写" in d and "查找" in a):
        return "APP_FLASH_CONFIG"
    if "APP刷写" in t or "APP刷写" in d:
        return "APP_FLASH"
    if "MES" in t or "MES" in d or "moveInCheck" in a:
        return "MES_CHECK"
    if "3.5HEOL" in t or "EOL协议" in t or "EOL:" in p:
        return "EOL_PROTOCOL"
    if "公式计算" in t or "公式计算" in a or "FORMULA:" in p:
        return "CALCULATE"
    if "读取变量" in t or "读取变量" in a or "变量操作" in d or "VAR:" in p:
        return "READ_VAR"
    if "读取" in t:
        return "READ_INSTRUMENT"
    if "CAN发送" in t or "发送指令" in a:
        return "CAN_SEND"
    if "CAN接收" in t or "接收指定帧ID" in t or "接收指定帧ID" in a:
        return "CAN_RECEIVE"
    if "CAN交互" in t or "报文交互" in t:
        return "CAN_INTERACT"
    if "等待" in t:
        return "WAIT"
    return "SET_INSTRUMENT"


def _check_params_semantics(sub: Dict[str, Any], prefix: str, errors: List[str]) -> None:
    """按路由后的真实类型校验 params 语义（与引擎解析规则一致）"""
    p = str(sub.get("params", ""))
    d = str(sub.get("device", "")).lower()
    a = str(sub.get("action", ""))
    stype = _route_sub_type(sub)

    if stype == "WAIT":
        if not re.search(r"\d", p):
            errors.append(f"{prefix} 等待工步 params 需包含毫秒数，如 \"2000ms\"")
    elif stype in ("APP_FLASH_CONFIG", "APP_FLASH"):
        if "HOST:" not in p:
            errors.append(f"{prefix} APP刷写 params 需包含 HOST:{{exe路径}}")
    elif stype == "EOL_PROTOCOL":
        if "EOL:" not in p:
            errors.append(f"{prefix} EOL 工步 params 需包含 EOL:{{操作名}}")
    elif stype == "CALCULATE":
        if "FORMULA:" not in p:
            errors.append(f"{prefix} 公式计算 params 需包含 FORMULA:{{表达式}}")
    elif stype == "READ_VAR":
        if "VAR:" not in p:
            errors.append(f"{prefix} 读取变量 params 需包含 VAR:{{变量名}}")
    elif stype == "CAN_SEND":
        if "ID:" not in p or "DATA:" not in p:
            errors.append(f"{prefix} CAN发送 params 需包含 ID: 和 DATA:")
    elif stype == "CAN_RECEIVE":
        if "ID:" not in p:
            errors.append(f"{prefix} CAN接收 params 需包含 ID:")
    elif stype == "CAN_INTERACT":
        if "ID:" not in p and "TX_ID:" not in p:
            errors.append(f"{prefix} CAN交互 params 需包含 ID: 或 TX_ID:")
    elif stype == "SET_INSTRUMENT":
        if ("easy320" in d or "继电器" in d) and ("闭合" in a or "断开" in a) and "全部" not in a:
            if not re.fullmatch(r"[\d,，\s]+", p):
                errors.append(f"{prefix} 继电器 params 应为逗号分隔的通道号，如 \"6,7\"")


def validate_recipe(data: Any, original_name: str = "") -> List[str]:
    """结构校验，返回错误列表（空列表表示通过）"""
    errors: List[str] = []
    if not isinstance(data, dict):
        return ["返回内容不是 JSON 对象"]
    if "error" in data and "items" not in data:
        errors.append(f"模型拒绝执行: {data['error']}")
        return errors

    name = data.get("name")
    if not isinstance(name, str) or not name.strip():
        errors.append("name 缺失或不是字符串")
    elif original_name and name != original_name:
        errors.append(f"name 被修改（{original_name} -> {name}），这不被允许")

    items = data.get("items")
    if not isinstance(items, list):
        errors.append("items 缺失或不是数组")
        return errors

    for i, item in enumerate(items):
        prefix = f"测试项[{i}]"
        if not isinstance(item, dict):
            errors.append(f"{prefix} 不是对象")
            continue
        if not isinstance(item.get("name"), str) or not item.get("name", "").strip():
            errors.append(f"{prefix} name 缺失")
            item_name = f"#{i}"
        else:
            item_name = item["name"]
            prefix = f"测试项[{item_name}]"
        if item.get("standard_type", "数值") not in STANDARD_TYPES:
            errors.append(f"{prefix} standard_type 非法: {item.get('standard_type')}")
        if item.get("retry_count", "不复测") not in RETRY_COUNTS:
            errors.append(f"{prefix} retry_count 非法: {item.get('retry_count')}")
        for key in ("min", "max", "unit"):
            if not isinstance(item.get(key, ""), str):
                errors.append(f"{prefix} {key} 必须是字符串")
        sub_steps = item.get("sub_steps")
        if not isinstance(sub_steps, list):
            errors.append(f"{prefix} sub_steps 缺失或不是数组")
            continue
        for j, sub in enumerate(sub_steps):
            sprefix = f"{prefix}.子工步[{j}]"
            if not isinstance(sub, dict):
                errors.append(f"{sprefix} 不是对象")
                continue
            for key in ("name", "action", "params", "device", "type"):
                if not isinstance(sub.get(key), str):
                    errors.append(f"{sprefix} {key} 缺失或不是字符串")
            if not isinstance(sub.get("is_judgment"), bool):
                errors.append(f"{sprefix} is_judgment 必须是布尔值")
            if sub.get("fail_strategy", "失败停止") not in FAIL_STRATEGIES:
                errors.append(f"{sprefix} fail_strategy 非法: {sub.get('fail_strategy')}")
            else:
                _check_params_semantics(sub, sprefix, errors)
    return errors


def summarize_changes(old: Dict[str, Any], new: Dict[str, Any]) -> List[str]:
    """生成人类可读的变更摘要"""
    lines: List[str] = []

    for key, label in (("sn_regex", "条码规则"), ("recipe_number", "PLC配方号")):
        if old.get(key) != new.get(key):
            lines.append(f"● 修改{label}: {old.get(key)} → {new.get(key)}")
    if old.get("can_config") != new.get("can_config"):
        lines.append(f"● 修改CAN配置: {old.get('can_config')} → {new.get('can_config')}")

    old_items = old.get("items", [])
    new_items = new.get("items", [])
    old_map = {it.get("name", f"#{i}"): it for i, it in enumerate(old_items)}
    new_map = {it.get("name", f"#{i}"): it for i, it in enumerate(new_items)}

    for name in new_map:
        if name not in old_map:
            lines.append(f"＋ 新增测试项「{name}」（{len(new_map[name].get('sub_steps', []))} 个子工步）")
    for name in old_map:
        if name not in new_map:
            lines.append(f"－ 删除测试项「{name}」")
    for name in old_map:
        if name not in new_map:
            continue
        o, n = old_map[name], new_map[name]
        if json.dumps(o, sort_keys=True, ensure_ascii=False) == json.dumps(n, sort_keys=True, ensure_ascii=False):
            continue
        details = []
        for key, label in (("min", "下限"), ("max", "上限"), ("unit", "单位"),
                           ("standard_type", "标准类型"), ("retry_count", "复测")):
            if o.get(key) != n.get(key):
                details.append(f"{label}: {o.get(key)} → {n.get(key)}")
        o_subs = [s.get("name", "") for s in o.get("sub_steps", [])]
        n_subs = [s.get("name", "") for s in n.get("sub_steps", [])]
        for s in n_subs:
            if s not in o_subs:
                details.append(f"新增子工步「{s}」")
        for s in o_subs:
            if s not in n_subs:
                details.append(f"删除子工步「{s}」")
        for s in n_subs:
            if s in o_subs:
                o_sub = next(x for x in o.get("sub_steps", []) if x.get("name") == s)
                n_sub = next(x for x in n.get("sub_steps", []) if x.get("name") == s)
                if json.dumps(o_sub, sort_keys=True, ensure_ascii=False) != json.dumps(n_sub, sort_keys=True, ensure_ascii=False):
                    details.append(f"修改子工步「{s}」")
        if not details:
            details.append("子工步顺序或参数有调整")
        lines.append(f"◆ 修改测试项「{name}」: " + "；".join(details))

    old_order = [n for n in old_map if n in new_map]
    new_order = [n for n in new_map if n in old_map]
    if old_order != new_order:
        lines.append("⇅ 测试项执行顺序有调整")

    if not lines:
        lines.append("（配方内容无实质变化）")
    return lines


def _extract_json(text: str) -> Dict[str, Any]:
    """从模型返回文本中提取 JSON 对象（容忍 markdown 代码块包裹）"""
    text = text.strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[-1] if "\n" in text else text[3:]
        if text.endswith("```"):
            text = text[:-3]
        text = text.strip()
        if text.startswith("json"):
            text = text[4:].strip()
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1 or end <= start:
        raise AIRecipeError("模型返回中未找到 JSON 对象")
    try:
        return json.loads(text[start:end + 1])
    except json.JSONDecodeError as e:
        raise AIRecipeError(f"模型返回的 JSON 无法解析: {e}")


def _call_chat(messages: List[Dict[str, str]], *, api_key: str, base_url: str,
               model: str, timeout: int) -> str:
    url = base_url.rstrip("/") + "/chat/completions"
    try:
        resp = requests.post(
            url,
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            json={"model": model, "messages": messages, "temperature": 0.2},
            timeout=timeout,
        )
    except requests.RequestException as e:
        raise AIRecipeError(f"网络请求失败: {e}")
    if resp.status_code == 401:
        raise AIRecipeError("API Key 无效或已过期 (401)，请检查 AI 设置")
    if resp.status_code == 429:
        raise AIRecipeError("API 请求频率超限或余额不足 (429)")
    if resp.status_code != 200:
        raise AIRecipeError(f"API 返回错误 ({resp.status_code}): {resp.text[:200]}")
    try:
        return resp.json()["choices"][0]["message"]["content"]
    except (KeyError, IndexError, ValueError) as e:
        raise AIRecipeError(f"API 响应格式异常: {e}")


def generate_recipe_edit(
    instruction: str,
    recipe: Dict[str, Any],
    *,
    api_key: str,
    base_url: str = DEFAULT_BASE_URL,
    model: str = DEFAULT_MODEL,
    timeout: int = 90,
    max_retries: int = 1,
) -> Tuple[Dict[str, Any], List[str]]:
    """按指令生成修改后的配方，返回 (新配方, 变更摘要)。失败抛 AIRecipeError。"""
    instruction = (instruction or "").strip()
    if not instruction:
        raise AIRecipeError("指令不能为空")
    if not api_key:
        raise AIRecipeError("未配置 API Key，请先在 AI 设置中填写")

    system = SYSTEM_PROMPT.replace("{grammar}", STEP_GRAMMAR_DOC)
    messages = [
        {"role": "system", "content": system},
        {"role": "user", "content": (
            "当前配方 JSON：\n"
            + json.dumps(recipe, ensure_ascii=False, indent=2)
            + "\n\n修改指令：" + instruction
            + "\n\n请输出修改后的完整配方 JSON。"
        )},
    ]

    last_errors: List[str] = []
    for attempt in range(max_retries + 1):
        content = _call_chat(messages, api_key=api_key, base_url=base_url,
                             model=model, timeout=timeout)
        try:
            new_recipe = _extract_json(content)
        except AIRecipeError as e:
            last_errors = [str(e)]
        else:
            last_errors = validate_recipe(new_recipe, original_name=recipe.get("name", ""))
            if not last_errors:
                changes = summarize_changes(recipe, new_recipe)
                return new_recipe, changes
        if attempt < max_retries:
            messages.append({"role": "assistant", "content": content})
            messages.append({"role": "user", "content": (
                "你上次返回的内容未通过校验，问题如下：\n"
                + "\n".join(last_errors)
                + "\n请修正后重新输出完整配方 JSON（只输出 JSON）。"
            )})

    raise AIRecipeError("模型多次返回均未通过校验：\n" + "\n".join(last_errors))


def backup_recipe(db_manager, name: str, data: Dict[str, Any]) -> str:
    """把原配方备份到 recipes/backup/ 目录，返回备份文件名"""
    import datetime
    import os

    backup_dir = os.path.join(db_manager.get_recipe_dir(), "backup")
    os.makedirs(backup_dir, exist_ok=True)
    ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_name = f"{name}_{ts}"
    file_path = os.path.join(backup_dir, f"{backup_name}.json")
    with open(file_path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=4, ensure_ascii=False)
    return backup_name

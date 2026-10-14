# -*- coding: utf-8 -*-
"""AI 配方助手核心模块单元测试（API 调用全部 mock）"""
import copy
import json
import os
import tempfile
import unittest
from unittest.mock import patch

from core.ai_recipe_assistant import (
    AIRecipeError, _extract_json, backup_recipe, generate_recipe_edit,
    summarize_changes, validate_recipe
)

SAMPLE_RECIPE = {
    "name": "DJ2513_Aging66666",
    "recipe_number": 0,
    "plc_recipe_register": 0,
    "sn_regex": "^\\d{4}$",
    "can_config": {"channel": "PCAN_USBBUS1", "mode": "CAN-FD + BRS",
                   "nominal_bitrate": 500000, "data_bitrate": 2000000},
    "items": [
        {
            "name": "BMS上电", "mode": "范围判定", "min": "230", "max": "290",
            "standard_type": "数值", "retry_count": "不复测", "unit": "mA",
            "sub_steps": [
                {"name": "等待 (Wait)-固定延时 (2000ms)", "action": "固定延时",
                 "params": "2000ms", "device": "等待 (Wait)", "type": "等待",
                 "is_judgment": False, "fail_strategy": "失败停止"},
            ],
        },
        {
            "name": "刷写", "mode": "字符串比对", "min": "PASS", "max": "--",
            "standard_type": "字符串", "retry_count": "不复测", "unit": "NULL",
            "sub_steps": [
                {"name": "APP刷写-启动刷写 (HOST:x / TIMEOUT:300)", "action": "启动刷写",
                 "params": "HOST:x / TIMEOUT:300", "device": "APP刷写", "type": "APP刷写",
                 "is_judgment": False, "fail_strategy": "失败停止"},
            ],
        },
    ],
}


class TestValidateRecipe(unittest.TestCase):
    def test_valid_recipe_passes(self):
        self.assertEqual(validate_recipe(SAMPLE_RECIPE, "DJ2513_Aging66666"), [])

    def test_name_change_rejected(self):
        bad = copy.deepcopy(SAMPLE_RECIPE)
        bad["name"] = "OTHER"
        errors = validate_recipe(bad, "DJ2513_Aging66666")
        self.assertTrue(any("name 被修改" in e for e in errors))

    def test_items_not_list(self):
        errors = validate_recipe({"name": "X", "items": {}}, "")
        self.assertTrue(any("items" in e for e in errors))

    def test_sub_step_missing_field(self):
        bad = copy.deepcopy(SAMPLE_RECIPE)
        del bad["items"][0]["sub_steps"][0]["params"]
        errors = validate_recipe(bad, "")
        self.assertTrue(any("params" in e for e in errors))

    def test_bad_fail_strategy(self):
        bad = copy.deepcopy(SAMPLE_RECIPE)
        bad["items"][0]["sub_steps"][0]["fail_strategy"] = "随便"
        errors = validate_recipe(bad, "")
        self.assertTrue(any("fail_strategy" in e for e in errors))

    def test_model_error_passthrough(self):
        errors = validate_recipe({"error": "无法理解指令"}, "")
        self.assertTrue(any("无法理解指令" in e for e in errors))


class TestExtractJson(unittest.TestCase):
    def test_plain(self):
        self.assertEqual(_extract_json('{"a": 1}'), {"a": 1})

    def test_code_fence(self):
        self.assertEqual(_extract_json('```json\n{"a": 1}\n```'), {"a": 1})

    def test_surrounding_text(self):
        self.assertEqual(_extract_json('好的 {"a": 1} 请查收'), {"a": 1})

    def test_no_json(self):
        with self.assertRaises(AIRecipeError):
            _extract_json("没有JSON")

    def test_broken_json(self):
        with self.assertRaises(AIRecipeError):
            _extract_json("{broken")


class TestSummarizeChanges(unittest.TestCase):
    def test_no_change(self):
        self.assertEqual(summarize_changes(SAMPLE_RECIPE, copy.deepcopy(SAMPLE_RECIPE)),
                         ["（配方内容无实质变化）"])

    def test_add_and_modify(self):
        new = copy.deepcopy(SAMPLE_RECIPE)
        new["items"].append({
            "name": "版本读取", "mode": "字符串比对", "min": "V1.0", "max": "--",
            "standard_type": "字符串", "retry_count": "不复测", "unit": "NULL",
            "sub_steps": [],
        })
        new["items"][0]["max"] = "300"
        lines = summarize_changes(SAMPLE_RECIPE, new)
        self.assertTrue(any("新增测试项「版本读取」" in l for l in lines))
        self.assertTrue(any("修改测试项「BMS上电」" in l and "上限: 290 → 300" in l for l in lines))

    def test_delete(self):
        new = copy.deepcopy(SAMPLE_RECIPE)
        new["items"] = new["items"][:1]
        lines = summarize_changes(SAMPLE_RECIPE, new)
        self.assertTrue(any("删除测试项「刷写」" in l for l in lines))


class TestGenerateRecipeEdit(unittest.TestCase):
    def test_success(self):
        new = copy.deepcopy(SAMPLE_RECIPE)
        new["items"][0]["min"] = "200"
        with patch("core.ai_recipe_assistant._call_chat",
                   return_value=json.dumps(new, ensure_ascii=False)):
            result, changes = generate_recipe_edit("把下限改成200", SAMPLE_RECIPE, api_key="k")
        self.assertEqual(result["items"][0]["min"], "200")
        self.assertTrue(any("BMS上电" in c for c in changes))

    def test_retry_then_success(self):
        bad = copy.deepcopy(SAMPLE_RECIPE)
        bad["name"] = "HACKED"
        good = copy.deepcopy(SAMPLE_RECIPE)
        with patch("core.ai_recipe_assistant._call_chat",
                   side_effect=[json.dumps(bad, ensure_ascii=False),
                                json.dumps(good, ensure_ascii=False)]):
            result, _ = generate_recipe_edit("测试", SAMPLE_RECIPE, api_key="k")
        self.assertEqual(result["name"], "DJ2513_Aging66666")

    def test_persistent_failure_raises(self):
        with patch("core.ai_recipe_assistant._call_chat", return_value="不是JSON"):
            with self.assertRaises(AIRecipeError):
                generate_recipe_edit("测试", SAMPLE_RECIPE, api_key="k")

    def test_empty_instruction(self):
        with self.assertRaises(AIRecipeError):
            generate_recipe_edit("  ", SAMPLE_RECIPE, api_key="k")

    def test_empty_api_key(self):
        with self.assertRaises(AIRecipeError):
            generate_recipe_edit("测试", SAMPLE_RECIPE, api_key="")


class _FakeDB:
    def __init__(self, recipe_dir):
        self._dir = recipe_dir

    def get_recipe_dir(self):
        return self._dir


class TestBackupRecipe(unittest.TestCase):
    def test_backup_file_created(self):
        with tempfile.TemporaryDirectory() as tmp:
            name = backup_recipe(_FakeDB(tmp), "DJ2513", SAMPLE_RECIPE)
            path = os.path.join(tmp, "backup", f"{name}.json")
            self.assertTrue(os.path.isfile(path))
            with open(path, encoding="utf-8") as f:
                self.assertEqual(json.load(f), SAMPLE_RECIPE)


if __name__ == "__main__":
    unittest.main()


class TestSemanticValidation(unittest.TestCase):
    """params 语义校验（镜像引擎路由规则）"""

    def _errors(self, sub):
        recipe = copy.deepcopy(SAMPLE_RECIPE)
        recipe["items"][0]["sub_steps"] = [sub]
        return validate_recipe(recipe, "")

    def _sub(self, **kw):
        base = {"name": "x", "action": "", "params": "", "device": "",
                "type": "", "is_judgment": False, "fail_strategy": "失败停止"}
        base.update(kw)
        return base

    def test_wait_needs_number(self):
        errors = self._errors(self._sub(type="等待", action="固定延时", params="稍等", device="等待 (Wait)"))
        self.assertTrue(any("毫秒数" in e for e in errors))

    def test_wait_ok(self):
        self.assertEqual(self._errors(self._sub(type="等待", params="2000ms", device="等待 (Wait)")), [])

    def test_can_send_needs_data(self):
        errors = self._errors(self._sub(type="CAN发送", action="发送指令", params="ID:0x7E0", device="CAN 交互"))
        self.assertTrue(any("DATA:" in e for e in errors))

    def test_can_send_ok(self):
        self.assertEqual(self._errors(self._sub(
            type="CAN发送", action="发送指令", params="ID:0x7E0 / DATA:10 03 / TYPE:0 / DLC:8", device="CAN 交互")), [])

    def test_app_flash_needs_host(self):
        errors = self._errors(self._sub(type="APP刷写", action="启动刷写", params="TIMEOUT:300", device="APP刷写"))
        self.assertTrue(any("HOST:" in e for e in errors))

    def test_eol_needs_eol_key(self):
        errors = self._errors(self._sub(type="3.5HEOL协议", params="TIMEOUT:500", device="3.5HEOL协议"))
        self.assertTrue(any("EOL:" in e for e in errors))

    def test_relay_params_must_be_channels(self):
        errors = self._errors(self._sub(type="继电器控制", action="闭合勾选通道",
                                        params="全部通道", device="Easy320 继电器 (Easy320)"))
        self.assertTrue(any("通道号" in e for e in errors))

    def test_relay_ok(self):
        self.assertEqual(self._errors(self._sub(type="继电器控制", action="闭合勾选通道",
                                                params="6,7", device="Easy320 继电器 (Easy320)")), [])

    def test_var_needs_var_key(self):
        errors = self._errors(self._sub(type="读取变量", params="环境温度", device="变量操作 (Variable)"))
        self.assertTrue(any("VAR:" in e for e in errors))

    def test_calc_needs_formula(self):
        errors = self._errors(self._sub(type="公式计算", action="公式计算", params="VAR:X", device="公式计算 (Calculate)"))
        self.assertTrue(any("FORMULA:" in e for e in errors))

    def test_existing_sample_recipe_passes(self):
        self.assertEqual(validate_recipe(SAMPLE_RECIPE, "DJ2513_Aging66666"), [])


if __name__ == "__main__":
    unittest.main()

# -*- coding: utf-8 -*-
"""Excel 测试记录导出模块单元测试"""
import os
import sqlite3
import unittest

import xlrd

from core.excel_recorder import export_test_record, extract_project_no


def _make_db(db_path: str) -> int:
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()
    cur.execute('''
        CREATE TABLE test_main (
            test_id INTEGER PRIMARY KEY AUTOINCREMENT,
            channel_id INTEGER, shelf_code TEXT, master_code TEXT,
            slave_codes TEXT, recipe_name TEXT,
            start_time DATETIME, end_time DATETIME, result TEXT
        )
    ''')
    cur.execute('''
        CREATE TABLE test_items_results (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            test_id INTEGER, item_name TEXT, lower_limit REAL,
            upper_limit REAL, measured_value REAL, result TEXT,
            duration REAL, unit TEXT, timestamp DATETIME
        )
    ''')
    cur.execute(
        "INSERT INTO test_main (channel_id, shelf_code, master_code, slave_codes, recipe_name, start_time, end_time, result)"
        " VALUES (1, 'A01', 'SN12345', '', 'DJ2513_Aging66666', '2026-09-30 10:00:00', '2026-09-30 10:05:00', 'PASS')"
    )
    test_id = cur.lastrowid
    cur.execute(
        "INSERT INTO test_items_results (test_id, item_name, lower_limit, upper_limit, measured_value, result, duration, unit, timestamp)"
        " VALUES (?, '电压检测', 3.0, 4.2, 3.7, 'PASS', 1.5, 'V', '2026-09-30 10:01:00')", (test_id,)
    )
    cur.execute(
        "INSERT INTO test_items_results (test_id, item_name, lower_limit, upper_limit, measured_value, result, duration, unit, timestamp)"
        " VALUES (?, '温度检测', 0, 60, 75, 'NG', 2.0, '℃', '2026-09-30 10:02:00')", (test_id,)
    )
    conn.commit()
    conn.close()
    return test_id


class TestExtractProjectNo(unittest.TestCase):
    def test_prefix(self):
        self.assertEqual(extract_project_no("DJ2513_Aging66666"), "DJ2513")

    def test_no_underscore(self):
        self.assertEqual(extract_project_no("DJ2513"), "DJ2513")

    def test_empty(self):
        self.assertEqual(extract_project_no(""), "未知项目")
        self.assertEqual(extract_project_no(None), "未知项目")

    def test_leading_underscore(self):
        self.assertEqual(extract_project_no("_abc"), "未知项目")


class TestExportTestRecord(unittest.TestCase):
    def test_export_generates_excel_by_project_and_date(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            db_path = os.path.join(tmp, "test.db")
            test_id = _make_db(db_path)
            base = os.path.join(tmp, "测试记录")

            out = export_test_record(db_path, test_id, base_dir=base)

            self.assertIsNotNone(out)
            self.assertTrue(os.path.isfile(out))
            # 路径结构: 测试记录/项目号/年月日/SN_时间戳_结果.xlsx
            self.assertEqual(os.path.basename(os.path.dirname(os.path.dirname(os.path.dirname(out)))), "DJ2513")
            self.assertEqual(os.path.basename(os.path.dirname(os.path.dirname(out))), "2026-09-30")
            self.assertEqual(os.path.basename(os.path.dirname(out)), "PASS")
            self.assertEqual(os.path.basename(out),"SN12345_Pass(20260930_10-00-00).xls")

            wb = xlrd.open_workbook(out,formatting_info=True)
            ws = wb.sheet_by_index(0)
            self.assertEqual(ws.cell_value(0,0), "Test Report")
            # 信息块
            self.assertEqual(ws.cell_value(8,0), "项目编号：")
            self.assertEqual(ws.cell_value(8,1), "DJ2513")
            self.assertEqual(ws.cell_value(10,1), "DJ2513_Aging66666")
            self.assertEqual(ws.cell_value(2,0), "SN号：")
            self.assertEqual(ws.cell_value(2,1), "SN12345")
            self.assertEqual(ws.cell_value(6,0), "测试结果：")
            self.assertEqual(ws.cell_value(6,1), "Pass")
            # 表头在第 8 行
            self.assertEqual(ws.cell_value(17,0), "测试名称")
            self.assertEqual(ws.cell_value(17,1), "测试值")
            # 两条测试项
            self.assertEqual(ws.cell_value(18,0), "电压检测")
            self.assertEqual(ws.cell_value(18,5), "Pass")
            self.assertEqual(ws.cell_value(19,0), "温度检测")
            self.assertEqual(ws.cell_value(19,5), "Fail")

    def test_missing_test_id_returns_none(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            db_path = os.path.join(tmp, "test.db")
            _make_db(db_path)
            self.assertIsNone(export_test_record(db_path, 9999, base_dir=tmp))


if __name__ == "__main__":
    unittest.main()

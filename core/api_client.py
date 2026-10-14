"""
老化监控系统 POST 接口客户端
后端服务默认端口: 8008
接口文档: POST_API_Documentation.md
"""
import requests
from requests.adapters import HTTPAdapter


class AgingApiClient:
    """老化监控系统 HTTP 接口封装，所有方法失败时返回 False，不抛出异常。"""

    DEFAULT_TIMEOUT = 5  # 秒

    def __init__(self, host: str = "127.0.0.1", port: int = 8008, logger=None):
        self.base_url = f"http://{host}:{port}"
        self.logger = logger
        self.session = requests.Session()
        adapter = HTTPAdapter(pool_connections=64, pool_maxsize=64)
        self.session.mount("http://", adapter)
        self.session.mount("https://", adapter)

    def _summarize_body(self, path: str, body: dict) -> str:
        if path == "/api/test-data":
            return (
                "test-data("
                f"channel_id={body.get('channel_id')}, "
                f"master={len(body.get('master_test_data') or [])}, "
                f"slave1={len(body.get('slave_1_test_data') or [])}, "
                f"slave2={len(body.get('slave_2_test_data') or [])}, "
                f"slave3={len(body.get('slave_3_test_data') or [])})"
            )
        parts = []
        for key, value in body.items():
            text = str(value)
            if len(text) > 120:
                text = text[:117] + "..."
            parts.append(f"{key}={text}")
        return "{" + ", ".join(parts) + "}"

    def _post(self, path: str, body: dict) -> bool:
        """通用 POST，成功返回 True，失败返回 False。"""
        import json
        url = f"{self.base_url}{path}"
        if self.logger:
            self.logger(f"[API REQ] POST {url} -> Body: {self._summarize_body(path, body)}")
        try:
            resp = self.session.post(
                url,
                json=body,
                timeout=self.DEFAULT_TIMEOUT,
            )
            success = resp.status_code == 200 and resp.json().get("code") == 200
            if self.logger:
                resp_text = resp.text.strip()
                if len(resp_text) > 300:
                    resp_text = resp_text[:297] + "..."
                self.logger(f"[API RESP] {path} -> Status: {resp.status_code}, Body: {resp_text}")
            return success
        except Exception as e:
            if self.logger:
                self.logger(f"[API ERR] {path} -> Exception: {str(e)}")
            return False

    # ------------------------------------------------------------------ #
    # 1. 准备测试（绑定条码）  idle → preparing
    # ------------------------------------------------------------------ #
    def prepare(self, channel_id: int, sn_barcode: str,
                slave_barcode_1: str = None, slave_barcode_2: str = None,
                slave_barcode_3: str = None) -> bool:
        body = {"master_barcode": sn_barcode}
        if slave_barcode_1: body["slave_barcode_1"] = slave_barcode_1
        if slave_barcode_2: body["slave_barcode_2"] = slave_barcode_2
        if slave_barcode_3: body["slave_barcode_3"] = slave_barcode_3
        return self._post(f"/api/channels/{channel_id}/prepare", body)

    # ------------------------------------------------------------------ #
    # 2. 开始测试  preparing → testing
    # ------------------------------------------------------------------ #
    def start_test(self, channel_id: int) -> bool:
        return self._post(f"/api/channels/{channel_id}/start-test", {"start": 1})

    # ------------------------------------------------------------------ #
    # 3. 上报测试进度（测试项变更时调用）
    # ------------------------------------------------------------------ #
    def report_progress(self, channel_id: int, barcode: str, name: str,
                        test_value: str, result: str,
                        unit: str = None, upper_limit: str = None,
                        lower_limit: str = None, index: str = None) -> bool:
        body = {
            "barcode": barcode,
            "name": name,
            "testValue": test_value,
            "result": result,
        }
        if unit is not None:        body["unit"] = unit
        if upper_limit is not None: body["upperLimit"] = upper_limit
        if lower_limit is not None: body["lowerLimit"] = lower_limit
        if index is not None:       body["index"] = index
        return self._post(f"/api/channels/{channel_id}/progress", body)

    # ------------------------------------------------------------------ #
    # 4. 心跳上报（全局，每 5 秒调用一次）
    # ------------------------------------------------------------------ #
    def heartbeat(self, chamber_temperature: float) -> bool:
        return self._post("/api/system/heartbeat", {"chamber_temperature": chamber_temperature})

    # ------------------------------------------------------------------ #
    # 5. 完成测试  testing → passed / failed
    # ------------------------------------------------------------------ #
    def finish_test(self, channel_id: int, result: bool) -> bool:
        return self._post(f"/api/channels/{channel_id}/finish-test", {"result": result})

    # ------------------------------------------------------------------ #
    # 6. 上报完整测试数据
    # ------------------------------------------------------------------ #
    def upload_test_data(self, channel_id: int, sn_barcode: str,
                         start_time: str, end_time: str, status: bool,
                         master_test_data: list,
                         slave_1_test_data: list = None,
                         slave_2_test_data: list = None,
                         slave_3_test_data: list = None,
                         slave_barcode_1: str = None,
                         slave_barcode_2: str = None,
                         slave_barcode_3: str = None,
                         product_model: str = None) -> bool:
        body = {
            "channel_id": str(channel_id),
            "master_barcode": sn_barcode,
            "start_time": start_time,
            "end_time": end_time,
            "status": status,
            "master_test_data":  master_test_data  or [],
            "slave_1_test_data": slave_1_test_data or [],
            "slave_2_test_data": slave_2_test_data or [],
            "slave_3_test_data": slave_3_test_data or [],
        }
        if slave_barcode_1:  body["slave_barcode_1"] = slave_barcode_1
        if slave_barcode_2:  body["slave_barcode_2"] = slave_barcode_2
        if slave_barcode_3:  body["slave_barcode_3"] = slave_barcode_3
        if product_model:    body["product_model"]   = product_model
        return self._post("/api/test-data", body)

    # ------------------------------------------------------------------ #
    # 7. 重置通道  → idle
    # ------------------------------------------------------------------ #
    def reset(self, channel_id: int) -> bool:
        return self._post(f"/api/channels/{channel_id}/reset", {})

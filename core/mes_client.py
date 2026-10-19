"""MES move-in (station pass) check client."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any
import json
import math
from urllib import error, request


DEFAULT_MES_URL = "http://10.38.2.36/api/epro/iot/moveInCheck"


@dataclass(frozen=True)
class MesCheckResult:
    passed: bool
    message: str
    status_code: int | None = None
    response: Any = None


class MesClient:
    def __init__(self, url: str, timeout: float = 10.0):
        self.url = (url or DEFAULT_MES_URL).strip()
        self.timeout = float(timeout)
        if not math.isfinite(self.timeout) or self.timeout <= 0:
            raise ValueError('MES timeout must be finite and positive')

    def move_in_check(self, equipment_no: str, order_no: str, sn: str,
                      user_name: str) -> MesCheckResult:
        payload = {
            "equipmentNo": (equipment_no or "").strip(),
            "orderNo": (order_no or "").strip(),
            "sn": (sn or "").strip(),
            "userName": (user_name or "").strip(),
        }
        if not payload["sn"]:
            return MesCheckResult(False, "SN 条码不能为空")
        if not payload["userName"]:
            return MesCheckResult(False, "当前登录用户名不能为空")

        encoded = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        http_request = request.Request(
            self.url, data=encoded, headers={"Content-Type": "application/json"}, method="POST"
        )
        try:
            with request.urlopen(http_request, timeout=self.timeout) as response:
                status_code = response.status
                response_text = response.read().decode("utf-8", errors="replace")
        except error.HTTPError as exc:
            status_code = exc.code
            response_text = exc.read().decode("utf-8", errors="replace")
        except (error.URLError, TimeoutError, OSError) as exc:
            return MesCheckResult(False, f"MES 请求失败: {exc}")

        try:
            body = json.loads(response_text)
        except ValueError:
            body = response_text

        http_ok = 200 <= status_code < 300
        passed, message = self._interpret_response(http_ok, body)
        if not http_ok:
            passed = False
            message = f"HTTP {status_code}: {message}"
        return MesCheckResult(passed, message, status_code, body)

    @staticmethod
    def _interpret_response(http_ok: bool, body: Any) -> tuple[bool, str]:
        """Accept common MES response envelopes while treating explicit failures as NG."""
        if not isinstance(body, dict):
            if isinstance(body, bool):
                return http_ok and body, 'MES 校验通过' if http_ok and body else 'MES 校验未通过'
            text = str(body).strip()
            affirmative = isinstance(body, str) and text.lower() in {'true', 'pass', 'passed', 'ok', 'success', '成功', '通过'}
            return bool(http_ok and affirmative), text or 'MES 响应缺少明确校验结论'

        message = str(body.get("message") or body.get("msg") or body.get("error") or "").strip()
        if body.get('error') not in (None, False, 0, '', '0') or body.get('errors'):
            return False, message or 'MES 返回明确错误'
        explicit = None
        for key in ("success", "passed", "pass", "result"):
            if key in body:
                value = body[key]
                if key == 'result' and isinstance(value, (dict, list)) and explicit is not None:
                    continue  # A result payload is not a contradictory verdict flag.
                if isinstance(value, bool):
                    explicit = value
                elif isinstance(value, (int, float)):
                    explicit = value == 1 if key in ('success', 'passed', 'pass') else value in (0, 1, 200)
                elif isinstance(value, str):
                    explicit = value.strip().lower() in {
                        "true", "pass", "passed", "ok", "success", "成功", "通过", "1", "200"
                    }
                else:
                    explicit = False
                # An explicit failure cannot be overridden by another success flag.
                if not explicit:
                    return False, message or 'MES 校验未通过'

        if "code" in body:
            code = body.get("code")
            code_ok = str(code).strip().lower() in {"0", "1", "200", "ok", "success"}
            explicit = code_ok if explicit is None else explicit and code_ok

        passed = bool(explicit and http_ok)
        return passed, message or ("MES 校验通过" if passed else "MES 校验未通过")

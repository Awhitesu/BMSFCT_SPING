from enum import Enum


import uuid
import json
from core.numeric_validation import finite_reading, NUMBER
from core.recipe_identity import ensure_step_ids
from core.single_channel_recipe import single_channel_items
import os
import sys
import re
import threading

from typing import List, Dict, Optional, Any
from PySide6.QtCore import QObject, Signal, QThread, QTimer, Slot
from core.debug_trace import trace
from core.measurement_units import MeasurementValue, configured_read_unit, convert_reading, convert_result, is_voltage_read, supports_read_units
from core.device_steps import is_device_step, execute as execute_device_step


class NGStrategy(Enum):
    STOP_ON_ANY = "任何NG停止"
    STOP_ON_CRITICAL = "关键NG停止"
    CONTINUE_ON_NG = "NG继续"

class StepType(Enum):
    CHARGE = "充电"
    DISCHARGE = "放电"
    REST = "静置"
    OCV_CHECK = "OCV检查"
    CUSTOM = "自定义工步"

class SubStepType(Enum):
    SET_INSTRUMENT = "设置仪表"
    READ_INSTRUMENT = "读取仪表"
    CAN_SEND = "CAN发送"
    CAN_INTERACT = "CAN交互"
    CAN_RECEIVE = "CAN接收"
    CAN_DBC = "CANDBC交互"
    EOL_PROTOCOL = "3.5HEOL协议"
    WAIT = "等待"
    BARRIER = "同步屏障"
    READ_VAR = "读取变量"
    ACQUIRE_SEQ_LOCK = "申请顺序锁"
    RELEASE_SEQ_LOCK = "释放顺序锁"
    CALCULATE = "公式计算"
    MES_CHECK = "MES过站校验"
    APP_FLASH = "APP刷写"
    APP_FLASH_CONFIG = "APP刷写配置"

class SubStepFailStrategy(Enum):
    STOP = "失败停止"
    CONTINUE = "忽略继续"
    RETRY_3 = "重试3次"

class SubStep:
    def __init__(self, type: SubStepType, params: Dict, fail_strategy: SubStepFailStrategy = SubStepFailStrategy.STOP):
        self.step_id = params.get("step_id") or uuid.uuid4().hex
        self.type = type
        self.params = params
        self.fail_strategy = fail_strategy

class TestStep:
    def __init__(self, name: str, step_type: StepType, ng_strategy: NGStrategy = NGStrategy.STOP_ON_ANY):
        self.step_id = uuid.uuid4().hex
        self.name = name
        self.step_type = step_type
        self.ng_strategy = ng_strategy
        self.sub_steps: List[SubStep] = []
        self.min_limit = None
        self.max_limit = None
        self.standard_type = "数值"
        self.retry_count = "不复测"
        self.unit = "NULL"
        self.decimal_places = None
        self.skip_runtime = False
        self.drop_on_ng = False

    def add_sub_step(self, sub_step: SubStep):
        self.sub_steps.append(sub_step)

_DTC_FAULT_LOCK = threading.Lock()


def _load_dtc_fault_items(fault_file_path: str, default_defs: list) -> list:
    """Never reuse an earlier file's fault definitions or whitelist."""
    with _DTC_FAULT_LOCK:
        try:
            raw = open(fault_file_path, 'rb')
        except FileNotFoundError:
            # Retain first-run defaults, but do not reuse any previous file.
            items = list(default_defs)
            try:
                with open(fault_file_path, 'x', encoding='utf-8') as stream:
                    stream.write('Byte\tbit\tFault\tWhitelist\n')
                    for byte, bit, name, masked in items:
                        stream.write(f"{byte}\t{bit}\t{name}\t{int(masked)}\n")
            except FileExistsError:
                return _load_existing_dtc_file(fault_file_path)
            except OSError:
                pass
            return items
        with raw:
            return _parse_dtc_fault_items(raw.read())


def _load_existing_dtc_file(path):
    with open(path, 'rb') as stream:
        return _parse_dtc_fault_items(stream.read())


def _parse_dtc_fault_items(raw):
    for encoding in ('utf-8-sig', 'gbk'):
        try:
            content = raw.decode(encoding)
            break
        except UnicodeDecodeError:
            continue
    else:
        raise ValueError('DTC definition file encoding is invalid')
    items, positions, names = [], set(), set()
    for number, line in enumerate(content.splitlines(), 1):
        if not line.strip():
            continue
        parts = [part.strip() for part in line.split('\t')]
        if not items and parts[:2] == ['Byte', 'bit']:
            continue
        try:
            if len(parts) not in (3, 4):
                raise ValueError()
            byte, bit = int(parts[0]), int(parts[1])
            name = parts[2]
            mask = parts[3] if len(parts) == 4 else ''
            if (not 0 <= byte <= 7 or not 0 <= bit <= 7 or not name
                    or mask not in ('', '0', '1') or (byte, bit) in positions
                    or name in names):
                raise ValueError()
        except ValueError:
            raise ValueError(f'Invalid DTC definition at line {number}') from None
        positions.add((byte, bit))
        names.add(name)
        items.append((byte, bit, name, mask == '1'))
    if not items:
        raise ValueError('DTC definition file contains no fault definitions')
    return items

class ChannelWorker(QObject):
    variable_target_resolved = Signal(int, int, str, object)
    stop_requested = Signal(str)
    cleanup_finished = Signal()
    step_started = Signal(int, int, str)
    progress_updated = Signal(int, float, dict)
    step_finished = Signal(int, int, str, bool, object)
    sub_step_finished = Signal(int, int, int, str, object)
    test_finished = Signal(int, bool)
    log_message = Signal(int, str)
    flash_status_changed = Signal(int, object)
    MAX_LOG_HISTORY = 500

    def __init__(self, channel_id: int, steps: List[TestStep], device_manager=None, db_manager=None, engine=None):
        super().__init__()
        self.channel_id = channel_id
        self.steps = steps
        self.device_manager = device_manager
        self.db_manager = db_manager
        self.engine = engine
        self.cancel_event = threading.Event()
        self.execution_id = uuid.uuid4().hex
        self._stopping = False
        self._cleanup_done = False
        self.is_running = False
        self.current_step_index = 0
        self.current_sub_step_index = 0
        self.current_step_results = []
        self.test_id = -1
        self._retry_count = 0
        self.step_retry_count = 0
        self.last_hw_log = ""
        self.variables = {}
        self.step_outputs={}
        self.last_output=None
        self._dbc_group_context=None
        self._dbc_group_plan=None
        self.sn_barcode = ""
        self.app_flash_config = None
        from core.user_session import get_current_user
        self.operator_name = get_current_user()
        self.record_error = ""
        self.cleanup_error = ""
        from core.can_periodic import PeriodicLists
        self.periodic_can=PeriodicLists(self.cancel_event.is_set,lambda text:self.log_message.emit(self.channel_id,text))
        
        # 绑定信号以自动在内存中存留全部状态以供监控窗口调阅
        self.log_history = []
        self.step_statuses = {}
        self.sub_step_statuses = {}
        self.step_measured_values = {}
        self.results_by_id = {}
        self.current_progress = 0.0
        self.step_start_times = {}
        def _on_log_msg(_, msg):
            self.log_history.append(msg)
            if len(self.log_history) > self.MAX_LOG_HISTORY:
                del self.log_history[:len(self.log_history) - self.MAX_LOG_HISTORY]
            
            import datetime

            if os.environ.get("AGING_DEBUG_CHANNEL_LOG") == "1":
                print(f"[{datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S.%f')[:-3]}] [CH-{self.channel_id:02d}] {msg}")
        self.log_message.connect(_on_log_msg)
        self.step_finished.connect(self._record_step_result)
        from PySide6.QtCore import Qt
        self.stop_requested.connect(self.stop, Qt.QueuedConnection)
        self.sub_step_finished.connect(self._record_sub_step_result)
        self.progress_updated.connect(lambda _, prog, __: setattr(self, "current_progress", prog))

    def _record_step_result(self, channel, index, name, passed, value):
        step = self.steps[index]
        self.step_statuses[(index, name)] = passed
        self.step_measured_values[(index, name)] = value
        self.results_by_id.setdefault(step.step_id, []).append({
            "execution_id": self.execution_id, "step_id": step.step_id,
            "name": name, "passed": passed, "value": value,
            "attempt": len(self.results_by_id.get(step.step_id, [])) + 1,
        })

    def _record_sub_step_result(self, channel, index, sub_index, status, value):
        self.sub_step_statuses[(index, sub_index)] = (status, value)
        if not (0 <= index < len(self.steps)):
            return
        step = self.steps[index]
        if not (0 <= sub_index < len(step.sub_steps)):
            return
        sub = step.sub_steps[sub_index]
        records = self.results_by_id.setdefault(sub.step_id, [])
        event = dict(execution_id=self.execution_id, step_id=sub.step_id,
                     parent_step_id=step.step_id, status=status, value=value,
                     attempt=len(records) + 1)
        records.append(event)
        if self.db_manager and self.test_id != -1 and hasattr(self.db_manager, 'log_step_event'):
            try:
                self.db_manager.log_step_event(self.test_id, event)
            except Exception as exc:
                self.record_error = str(exc)
                self.log_message.emit(self.channel_id, f"[ERROR] 工步记录写入失败: {exc}")

    def request_stop(self, final_status="STOPPED"):
        if self._stopping:
            return
        self._stopping = True
        self.cancel_event.set()
        self.is_running = False
        self.stop_requested.emit(final_status)

    def _export_excel_record(self, test_id: int):
        """测试结束后自动生成 Excel 测试记录（按 项目号/年月日 归档）"""
        try:
            from core.excel_recorder import export_test_record
            path = export_test_record(self.db_manager.db_path, test_id,settings=getattr(self.db_manager,'load_sys_config',lambda:{})())
            if path:
                self.log_message.emit(self.channel_id, f"[*] Excel 测试记录已保存: {path}")
        except Exception as e:
            trace("EXCEL_EXPORT_ERROR", self.channel_id, test_id=test_id, error=str(e))
            self.log_message.emit(self.channel_id, f"[WARN] Excel 测试记录导出失败: {e}")

    def set_test_info(self, test_id: int):
        self.test_id = test_id

    def start(self):
        trace("CHANNEL_START", self.channel_id, test_id=self.test_id, steps=len(self.steps), sn=self.sn_barcode)
        if self.cancel_event.is_set():
            return
        self.is_running = True
        if not getattr(self,'_debug_variable_session',False):
            self.variables.clear();self.step_outputs.clear();self.last_output=None
        self.current_step_index = 0
        self._dbc_group_context=None
        self.progress_updated.emit(self.channel_id, 0.0, {})
        self.log_message.emit(self.channel_id, f"[*] 测试开始，数据库ID: {self.test_id}")
        
            
        self.run_next_step()

    @Slot(str)
    def stop(self, final_status="STOPPED"):
        if self._cleanup_done:
            return
        self._cleanup_done = True
        try:
            self._perform_stop(final_status)
        except Exception as exc:
            self.log_message.emit(self.channel_id, f"[ERROR] Cleanup failed: {exc}")
        finally:
            self.cleanup_finished.emit()

    def _perform_stop(self, final_status):
        trace("CHANNEL_STOP_REQUEST", self.channel_id, test_id=self.test_id, step_idx=self.current_step_index, sub_idx=self.current_sub_step_index, final_status=final_status)
        self.is_running = False
        self._dbc_group_context=None
        errors=self.periodic_can.close()
        if errors:
            self.cleanup_error='; '.join(errors)
            self.log_message.emit(self.channel_id,'[ERROR] 周期CAN清理失败：'+self.cleanup_error)
        self.log_message.emit(self.channel_id, "[!] 收到停止指令")
        
        # 立即关闭相关硬件输出
        try:
            if self._shutdown_outputs() is False:
                self.cleanup_error = "Output shutdown was not fully confirmed"
                self.log_message.emit(self.channel_id, "[ERROR] " + self.cleanup_error)
        except Exception as exc:
            self.cleanup_error = str(exc)
            self.log_message.emit(self.channel_id, f"[ERROR] Output cleanup failed: {exc}")
        if self.db_manager and self.test_id != -1:
            tid = self.test_id
            self.test_id = -1
            try:
                self.db_manager.finish_test(tid, final_status)
                self._export_excel_record(tid)
            except Exception as e:
                self.record_error = str(e)
                trace("DB_FINISH_TEST_ERROR", self.channel_id, test_id=tid, error=str(e))
                self.log_message.emit(self.channel_id, f"[WARN] 数据库停止写入超时/失败: {e}")


    def _shutdown_outputs(self):
        manager = self.device_manager
        if manager and hasattr(manager, "safe_shutdown_outputs"):
            return manager.safe_shutdown_outputs(
                self.channel_id,
                logger=lambda msg: self.log_message.emit(self.channel_id, msg),
            )
        return False

    def run_next_step(self):
        if not self.is_running:
            trace("RUN_NEXT_STEP_SKIPPED", self.channel_id, running=self.is_running, step_idx=self.current_step_index)
            return

        if self.current_step_index >= len(self.steps):
            self.progress_updated.emit(self.channel_id, 100.0, {})
            # 判定是否有任何一个测试项是不合格的 (is_pass == False)
            has_ng = False
            if not getattr(self,'_keep_debug_periodic',False):
                if self.periodic_can.close():has_ng=True
            for step_name, is_pass in self.step_statuses.items():
                if not is_pass:
                    has_ng = True
                    break
            # Independent debug attempts share their periodic sender session.
            # An ordinary failed assertion must not remove its prerequisites.
            trace("CHANNEL_FINISH_BEGIN", self.channel_id, test_id=self.test_id, has_ng=has_ng, step_count=len(self.steps))
            
            if self.db_manager and self.test_id != -1:
                tid = self.test_id
                self.test_id = -1
                final_status = "FAIL" if has_ng else "PASS"
                trace("DB_FINISH_TEST_BEGIN", self.channel_id, test_id=tid, final_status=final_status if self.is_running else "STOPPED")
                try:
                    self.db_manager.finish_test(tid, final_status if self.is_running else "STOPPED")
                    self._export_excel_record(tid)
                    trace("DB_FINISH_TEST_END", self.channel_id, test_id=tid)
                except Exception as e:
                    self.record_error = str(e)
                    has_ng = True
                    trace("DB_FINISH_TEST_ERROR", self.channel_id, test_id=tid, error=str(e))
                    self.log_message.emit(self.channel_id, f"[WARN] 数据库完成写入超时/失败，通道状态继续完成: {e}")
                
            trace("CHANNEL_FINISH_EMIT", self.channel_id, success=not has_ng)
            self.test_finished.emit(self.channel_id, not has_ng)
            return

        # 极速闪电跳过循环 (Lightning Skip Loop)：遇到带 "#" 的被屏蔽项，一瞬间登记并广播跳过，0ms 极速奔向下一个勾选项！
        while self.current_step_index < len(self.steps):
            step = self.steps[self.current_step_index]
            if not step.name.strip().startswith("#") and not getattr(step, 'skip_runtime', False):
                break
            
            # 瞬间在 0ms 内完成当前项及其所有子步骤的状态登记与跳过广播！
            trace("STEP_SKIP", self.channel_id, step_idx=self.current_step_index, step=step.name)
            # 跳过项只更新状态，不能触发运行高亮和自动滚动。
            self.step_statuses[(self.current_step_index, step.name)] = True
            self.step_finished.emit(self.channel_id, self.current_step_index, step.name, True, "跳过")
            
            for sub_idx, sub in enumerate(step.sub_steps):
                self.sub_step_finished.emit(self.channel_id, self.current_step_index, sub_idx, "跳过", None)
            
            self.current_step_index += 1
            self.progress_updated.emit(self.channel_id, (self.current_step_index / max(1, len(self.steps))) * 100.0, {})

        # 若已完成全部测试项，直接进入 run_next_step 的正常收尾落库结算分支！
        if self.current_step_index >= len(self.steps):
            self.run_next_step()
            return
            
        step = self.steps[self.current_step_index]
        trace("STEP_START", self.channel_id, step_idx=self.current_step_index, step=step.name, sub_steps=len(step.sub_steps))
        self.step_started.emit(self.channel_id, self.current_step_index, step.name)
        import time
        self.step_start_times[step.step_id] = time.time()
        self.current_sub_step_index = 0
        self.current_step_results = []
        self.run_next_sub_step()

    def run_next_sub_step(self):
        if not self.is_running:
            trace("RUN_NEXT_SUBSTEP_SKIPPED", self.channel_id, running=self.is_running, step_idx=self.current_step_index, sub_idx=self.current_sub_step_index)
            return
        step = self.steps[self.current_step_index]
        if self.current_sub_step_index >= len(step.sub_steps):
            trace("STEP_SUBSTEPS_DONE", self.channel_id, step_idx=self.current_step_index, step=step.name)
            self.on_step_complete()
            return
        sub_step = step.sub_steps[self.current_sub_step_index]
        
            
        self.execute_sub_step(sub_step)

    def _emit_flash_status(self, status):
        self.last_flash_status = dict(status)
        self.flash_status_changed.emit(self.channel_id, self.last_flash_status)

    def _parse_numeric(self, s: Any) -> float:
        if isinstance(s, (int, float)): return float(s)
        try:
            res = re.findall(NUMBER, str(s))
            return float(res[0]) if res else 0.0
        except: return 0.0

    def _parse_key_values(self, params: str, is_args_level: bool = False) -> Dict[str, str]:
        values = {}
        if is_args_level:
            normalized = str(params).replace("；", "/").replace("，", "/").replace(",", "/").replace(";", "/").replace("  ", " ")
        else:
            normalized = str(params).replace("；", "/").replace("，", "/").replace("  ", " ")
        # Only treat a slash followed by the next ``KEY:`` as a separator.
        # A Windows path selected by Qt may use forward slashes (D:/dir/app.exe).
        parts = re.split(r"/(?=\s*[A-Za-z_][A-Za-z0-9_]*\s*[:：])", normalized)
        for part in parts:
            part = part.strip()
            if not part: continue
            part = part.replace("：", ":")
            if ":" not in part:
                # 兼容旧配方：如果没有冒号但它是纯数字，推测它是 INDEX
                if part.isdigit(): values["INDEX"] = part
                continue
            key, value = part.split(":", 1)
            # 清洗可能存在的重复冒号 (如 INDEX::0)
            value = value.strip().lstrip(":")
            values[key.strip().upper()] = value.strip()
        return values

    def _app_flash_values(self, params):
        from core.recipe_app_flash import validate_app_flash
        values = self._parse_key_values(str(params.get('params', '')))
        configured = validate_app_flash(self.app_flash_config)
        if configured is not None:
            values.update(HOST=configured['host_path'], INI='config.ini', SECTION='App', KEY='Addr')
        return values

    def _parse_int(self, value: Any, default: int = 0) -> int:
        if value is None or value == "": return default
        if isinstance(value, int): return value
        return int(str(value).strip(), 0)

    def _parse_hex_bytes(self, value: Any, default_len: int = 8) -> bytes:
        if value is None: return bytes(default_len)
        if isinstance(value, bytes): return value
        if isinstance(value, (list, tuple)): return bytes(int(v) & 0xFF for v in value)
        text = str(value).strip()
        if not text: return bytes(default_len)
        text = text.replace("0x", "").replace("0X", "").replace(" ", "")
        text = re.sub(r"[^0-9a-fA-F]", "", text)
        return bytes.fromhex(text) if text else bytes(default_len)

    def _parse_can_result(self, params, data, logger):
        from core.can_data_parser import decode_parser, parse_payload
        encoded = self._parse_key_values(str(params.get('params', ''))).get('PARSE')
        if not encoded:
            return bytes(data).hex(' ').upper()
        config = decode_parser(encoded)
        raw, value = parse_payload(data, config)
        logger(f"CAN解析 DATA={bytes(data).hex(' ').upper()} 配置={config} 原始值={raw} 最终值={value} {config.get('unit', '')}")
        return value

    def _parse_can_params(self, params: Dict) -> Dict[str, Any]:
        from core.can_frame_config import can_id,response_matcher
        kv = self._parse_key_values(params.get("params", ""))
        data = params.get("data") if "data" in params else kv.get("DATA")
        parsed_data = self._parse_hex_bytes(data)
        dlc = self._parse_int(params.get("dlc", kv.get("DLC")), len(parsed_data) or 8)
        recipe_can_type = getattr(
            getattr(getattr(self.engine, "device_manager", None), "pcan", None),
            "default_can_type",
            self._parse_int(params.get("can_type", kv.get("TYPE")), 0),
        )
        tx=can_id(params.get('id',params.get('can_id',kv.get('ID','0'))))
        wait_value=params.get('wait_id',kv.get('WAIT_ID'))
        rx=can_id(wait_value) if wait_value is not None and str(wait_value).strip() else -1
        matcher=response_matcher(rx if rx>=0 else tx,kv.get('RX_FORMAT','auto'),kv.get('RX_PREFIX',''))
        return {
            "channel_id": self._parse_int(params.get("channel_id", kv.get("CH")), 1),
            "can_id": tx,
            "can_type": recipe_can_type,
            "dlc": dlc,
            "data": parsed_data,
            "wait_id": rx,
            "matcher": matcher,
            "timeout": self._parse_int(params.get("timeout_ms", kv.get("TIMEOUT")), 1000) / 1000.0,
        }

    def _parse_eol_params(self, params: Dict) -> Optional[Dict[str, Any]]:
        kv = self._parse_key_values(params.get("params", ""))
        if "EOL" not in kv: return None
        kv.pop('PCH', None)
        return {
            "op_name": kv.pop("EOL").strip(),
            "timeout": self._parse_int(kv.pop("TIMEOUT", 1000), 1000) / 1000.0,
            "channel_id": self._parse_int(kv.pop("CH", 1), 1),
            "kwargs": kv,
        }

    def _get_can_board(self, mgr, params=None):
        board = getattr(mgr,'boards',{}).get(self.channel_id)
        if not board:
            from types import SimpleNamespace
            driver=getattr(mgr,'pcan',None)
            if driver is None:raise ValueError('CAN设备未初始化')
            if callable(getattr(type(driver), 'check_bus_health', None)):
                driver.check_bus_health()
            if not driver.is_connected and not driver.connect():
                raise ConnectionError(driver.last_error or 'CAN连接失败')
            return SimpleNamespace(can=self._step_can_driver(driver, params),is_connected=True)
        if not board.is_connected: board.connect()
        if not board.is_connected: raise ValueError(f"通道 {self.channel_id} CAN未连接")
        return board

    def _step_can_driver(self, driver, params):
        from core.can_step_channels import selection, resolve_channels
        text = (params or {}).get('params', '')
        if selection(text) == '0':return driver
        maximum = 4 if str(getattr(driver,'channel','')).startswith('ZLG_CAN') else 1
        physical = resolve_channels(text, maximum=maximum)[0]
        if maximum == 1:return driver
        from devices.can_channel_view import CANChannelView
        return CANChannelView(driver, physical)

    def _send_can_step(self, mgr, params, logger):
        from core.can_step_channels import selection, resolve_channels
        token = selection(params.get('params', ''))
        multi = token.upper() == 'ALL' or ',' in token or '，' in token
        board = self._get_can_board(mgr, None if multi else params)
        config = self._parse_can_params(params)
        physical = token
        if token == '0':
            channel = getattr(board.can,'channel','')
            physical = channel.removeprefix('ZLG_CAN') if isinstance(channel,str) and channel.startswith('ZLG_CAN') else '1'
        targets = [(physical, board.can)]
        if multi:
            from devices.can_channel_view import CANChannelView
            driver = board.can
            maximum = 4 if str(getattr(driver,'channel','')).startswith('ZLG_CAN') else 1
            active = getattr(getattr(driver, 'bus', None), 'enabled_channels', None)
            enabled = {ch+1 for ch in active} if maximum == 4 and isinstance(active,(set,list,tuple)) else None
            channels = resolve_channels(params.get('params',''), maximum=maximum, enabled=enabled, multiple=True)
            targets = [(str(ch), CANChannelView(driver, ch)) for ch in channels]
        failures = []
        for physical, driver in targets:
            if not self.is_running or self.cancel_event.is_set():raise RuntimeError('测试已停止')
            try:
                if not driver.send_can_message(config['channel_id'], config['can_id'], config['can_type'], config['dlc'], config['data']):
                    raise ConnectionError(getattr(driver,'last_error','') or '发送未确认成功')
                logger(f'CAN TX 物理通道CH{physical} ID=0x{config["can_id"]:X} DATA={config["data"].hex(" ").upper()}')
            except Exception as exc:
                failures.append(f'CH{physical}: {exc}')
        if failures:raise ConnectionError('CAN发送失败：'+'；'.join(failures))
        return True

    def _dbc_interface(self, driver, params):
        selected=self._step_can_driver(driver,params)
        physical=getattr(selected,'physical_channel',None)
        return f'ZLG_CAN{physical}' if physical is not None and driver.channel.startswith('ZLG_CAN') else driver.channel

    def _dbc_sampling_group(self, position):
        if self._dbc_group_plan is None:
            from core.dbc_groups import plan_groups
            items=[]
            for parent in self.steps:
                subs=[]
                for sub in parent.sub_steps:
                    params=dict(sub.params)
                    if sub.type==SubStepType.CAN_DBC:params['type']='CANDBC交互'
                    subs.append(params)
                items.append(dict(name=parent.name,skip_runtime=parent.skip_runtime,sub_steps=subs))
            self._dbc_group_plan=plan_groups(items)
        return self._dbc_group_plan.get(position)

    def _dbc_group_requests(self, group, driver, asset):
        from core.dbc_signals import signal_key,resolve_step
        requests=[];positions={}
        for position,config,params in group['members']:
            config=resolve_step(config,asset)
            interface=self._dbc_interface(driver,params)
            requests.append((config,interface))
            positions[position]=signal_key(config,interface)
        return requests,positions

    def _execute_eol_protocol(self, mgr, params, hw_logger):
        params_str = params.get("params", "")
        kv = self._parse_key_values(params_str)
        hw_logger(f"[DEBUG] EOL RAW: {params_str}")
        hw_logger(f"[DEBUG] EOL KV: {kv}")
        
        eol_cfg = self._parse_eol_params(params)
        if not eol_cfg: raise ValueError("EOL参数缺失")
        if eol_cfg["kwargs"].get("DIFF_AMBIENT") == "1" and "环境温度" not in self.variables:
            raise ValueError("Ambient temperature variable is required for DIFF_AMBIENT")
        
        # 拦截 0x07 CSC批量读取
        if eol_cfg["op_name"] in ("0x07 CSC读取", "0x07 CSC批量读取"):
            
            # 提前解析 ARGS，以便在继电器和节点配置前知晓 fake_mode
            step_delay = 30
            min_v, max_v = None, None
            fix_adjacent = False
            args_str = eol_cfg["kwargs"].get("ARGS", "")
            if args_str:
                kv_args = self._parse_key_values(args_str, is_args_level=True)
                if "MIN_V" in kv_args:
                    min_v = finite_reading(kv_args["MIN_V"])
                if "MAX_V" in kv_args:
                    max_v = finite_reading(kv_args["MAX_V"])
                if "STEP_DELAY" in kv_args:
                    step_delay = int(kv_args["STEP_DELAY"])
                if "FIX_ADJACENT" in kv_args:
                    fix_adjacent = kv_args["FIX_ADJACENT"].strip() in ("1", "true", "True", "ON", "on")
            
            # 只有在附加参数中显式开启了随机电压补偿 (COMPENSATE:1) 后才启动自愈模式
            fake_mode = False
            if args_str:
                if "COMPENSATE" in kv_args:
                    fake_mode = kv_args["COMPENSATE"].strip() in ("1", "true", "True", "ON", "on")

            start_idx = self._parse_int(eol_cfg["kwargs"].get("PARAM1"), 0)
            count = self._parse_int(eol_cfg["kwargs"].get("PARAM2"), 192)
            retry_limit = self._parse_int(eol_cfg["kwargs"].get("PARAM3"), 3)
            if not 0 <= start_idx <= 65535 or not 1 <= count <= 65536 - start_idx:
                raise ValueError('CSC读取范围无效：数量必须大于0，电芯索引须为0–65535')
            if retry_limit < 0 or step_delay < 0:
                raise ValueError('CSC复测次数和读取延时不能为负数')
            if min_v is not None and max_v is not None and min_v > max_v:
                raise ValueError('CSC电压下限不能大于上限')
            if self.cancel_event.is_set():
                return False, 'STOPPED'
            board = self._get_can_board(mgr, params)
            from devices.eol_protocol import EOLProtocol
            eol = EOLProtocol(board.can, channel_id=eol_cfg["channel_id"])
            
            hw_logger(f"[CSC批量读取] 开始批量读取单体电压. 范围: {start_idx} 到 {start_idx + count - 1}, 重试上限: {retry_limit}次, 电芯间延时: {step_delay}ms")
            if min_v is not None or max_v is not None:
                hw_logger(f"[CSC批量读取] 校验电压区间: {min_v if min_v else '-inf'}V ~ {max_v if max_v else 'inf'}V")
            if fix_adjacent:
                hw_logger("[WARN] 旧的异常替换参数已停用，保留原始读数")
                
            success = False
            attempt_ok = True
            current_round_data = {}
            self.log_message.emit(self.channel_id, "[*] 开始执行批量单体电压读取（单通道独立复测模式）...")
            board.can.clear_rx_history(0x7F8)
            
            # 1. 物理读取所有的电芯，单通道独立进行局部复测
            for offset in range(count):
                if self.cancel_event.is_set():
                    return False, "STOPPED"
                cell_idx = start_idx + offset
                
                # 循环间隔延时保护，避免总线拥堵或下位机繁忙返回故障2.5V数据
                if offset > 0 and step_delay > 0:
                    if self.cancel_event.wait(step_delay / 1000.0):
                        return False, 'STOPPED'
                    
                cell_kwargs = eol_cfg["kwargs"].copy()
                cell_kwargs["PARAM1"] = "0x0E"
                cell_kwargs["PARAM4"] = str(cell_idx)
                cell_kwargs["OP"] = "0x0E"
                cell_kwargs["INDEX"] = str(cell_idx)
                cell_kwargs.pop("ARGS", None)
                
                cell_final_val = None
                # 针对单个电芯的复测循环
                for cell_attempt in range(retry_limit + 1):
                    if self.cancel_event.is_set():
                        return False, "STOPPED"
                    if cell_attempt > 0:
                        if self.cancel_event.wait(0.05):
                            return False, 'STOPPED'
                        hw_logger(f"[CSC批量读取] 电芯 {cell_idx} 尝试不合格或失败，执行第 {cell_attempt} 次单通道复测...")
                        
                    try:
                        tx_id_str = cell_kwargs.get("TX_ID", "0x7F0")
                        rx_id_str = cell_kwargs.get("RX_ID", "0x7F8")
                        tx_val_bytes = cell_idx.to_bytes(2, "big")
                        tx_data = bytes([0x10, 0x07, 0x0E, 0x00]) + tx_val_bytes + b"\x00\x00"
                        if cell_attempt == 0:
                            self.log_message.emit(self.channel_id, f"  [电芯 {cell_idx}] TX -> ID:{tx_id_str} DATA:{tx_data.hex(' ').upper()}")
                        
                        cell_res = eol.execute("0x07 CSC控制读取", timeout=eol_cfg["timeout"], logger=hw_logger, **cell_kwargs)
                        if self.cancel_event.is_set():
                            return False, 'STOPPED'
                        
                        if cell_res.success and cell_res.value is not None:
                            val = finite_reading(cell_res.value)
                            rx_raw = getattr(cell_res, 'raw_data', b"")
                            rx_data_str = rx_raw.hex(' ').upper() if rx_raw else "NONE"
                            
                            # 局部判定单体是否合格
                            is_val_ok = True
                            if min_v is not None and val < min_v:
                                is_val_ok = False
                            if max_v is not None and val > max_v:
                                is_val_ok = False
                                
                            if is_val_ok:
                                cell_final_val = val
                                self.log_message.emit(self.channel_id, f"  [电芯 {cell_idx}] RX <- ID:{rx_id_str} DATA:{rx_data_str} (值: {val:.3f}V) (尝试 {cell_attempt})")
                                break # 成功且合格，或者开启了作弊模式，直接退出重试
                            else:
                                cell_final_val = val
                                self.log_message.emit(self.channel_id, f"  [电芯 {cell_idx}] RX <- ID:{rx_id_str} DATA:{rx_data_str} (值: {val:.3f}V) (超标，复测中)")
                        else:
                            err_msg = getattr(cell_res, 'error', '未知错误')
                            self.log_message.emit(self.channel_id, f"  [电芯 {cell_idx}] RX <- 失败: {err_msg} (尝试 {cell_attempt})")
                    except Exception as e:
                        self.log_message.emit(self.channel_id, f"  [电芯 {cell_idx}] 异常: {e} (尝试 {cell_attempt})")
                        hw_logger(f"[CSC批量读取] 电芯 {cell_idx} 读取异常: {e}")
                
                # 记录这个电芯本轮的最终值（哪怕是最后一次重试失败的值或者是 None）
                current_round_data[cell_idx] = cell_final_val
            
            self.variables['CSC_RAW'] = dict(current_round_data)
            if fix_adjacent or fake_mode:
                hw_logger("[WARN] Legacy measurement substitution disabled; preserving raw CSC readings")

            # 3. 校验和随机值补偿，并最终汇总判定 attempt_ok
            for offset in range(count):
                cell_idx = start_idx + offset
                val = current_round_data.get(cell_idx)
                
                is_cell_failed = False
                if val is None:
                    is_cell_failed = True
                else:
                    if min_v is not None and val < min_v:
                        is_cell_failed = True
                    elif max_v is not None and val > max_v:
                        is_cell_failed = True
                
                if is_cell_failed:
                    attempt_ok = False
                    if val is None:
                        hw_logger(f"[CSC批量读取] 电芯 {cell_idx} 最终读取失败（且未开启/未成功进行随机补偿）")
                    else:
                        hw_logger(f"[CSC批量读取] 电芯 {cell_idx} 最终电压 {val:.3f}V 校验不合格，超出正常范围（且未开启/未成功进行随机补偿）")
            
            if attempt_ok:
                success = True
                hw_logger(f"[CSC批量读取] 批量读取成功，获取全部 {count} 个电芯电压！")
            else:
                hw_logger("[CSC批量读取] 批量读取存在 NG 或读取失败电芯。")
            
            # 无论整组判定 success 与否，均强制将读取到的数据（包括 NG 的值，或 0.0）同步更新入全局系统变量
            self.variables["CSC_CELLS"] = current_round_data
            for cell_idx, val in current_round_data.items():
                self.variables[f"CSC_CELL_{cell_idx}"] = val
            
            if success:
                result_value = f"PASS:已读取{len(current_round_data)}个"
            else:
                result_value = "FAIL:电芯批量读取不合格或超时"
            
            hw_logger(f"EOL 0x07 CSC读取 => {'PASS' if success else 'FAIL'} {result_value}")
            return success, result_value

        board = self._get_can_board(mgr, params)
        from devices.eol_protocol import EOLProtocol
        eol = EOLProtocol(board.can, channel_id=eol_cfg["channel_id"])
        result = eol.execute(eol_cfg["op_name"], timeout=eol_cfg["timeout"], logger=hw_logger, **eol_cfg["kwargs"])
        if self.cancel_event.is_set():
            return False, 'STOPPED'
        
        if eol_cfg["op_name"] in ("DTC诊断", "DTU诊断"):
            # EXE 保留同级故障码表；源码读取 config 下的故障码表。
            exe_dir = os.path.dirname(sys.executable) if getattr(sys, 'frozen', False) else os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
            fault_file_path = os.path.join(exe_dir, "故障码.txt") if getattr(sys, 'frozen', False) else os.path.join(exe_dir, "config", "故障码.txt")
            if not os.path.exists(fault_file_path):
                cwd_path = os.path.join(os.getcwd(), "故障码.txt")
                if os.path.exists(cwd_path):
                    fault_file_path = cwd_path

            default_defs = [
                (0, 0, "气压传感器唤醒BMS异常断线故障", False),
                (0, 1, "气压传感器通讯丢失故障", False),
                (1, 0, "电源芯片自检故障", False),
                (1, 1, "电源芯片给BMU板内供电一级故障", False),
                (1, 2, "电源芯片给BMU板内供电二级故障", False),
                (1, 3, "电源芯片给BMU板外供电一级故障", False),
                (1, 4, "电源芯片给BMU板外供电二级故障", False),
                (2, 0, "以G0为基准点的高压采样功能的SPI通讯异常", True),
                (2, 1, "BatteryToGo0采样超范围", False),
                (2, 2, "BMU和CMC之间E2E故障", True),
                (2, 3, "BMS检测到均衡回路出现故障", False),
                (2, 4, "BMU过温故障", False),
                (2, 5, "AFE自检故障", False),
                (3, 0, "主正继电器线圈短地故障", False),
                (3, 1, "主正继电器线圈短电源故障", False),
                (3, 2, "主正继电器线圈开路故障", True),
                (3, 3, "主负继电器线圈短地故障", False),
                (3, 4, "主负继电器线圈短电源故障", False),
                (3, 5, "主负继电器线圈开路故障", True),
                (4, 0, "预充继电器线圈短地故障", False),
                (4, 1, "预充继电器线圈短电源故障", False),
                (4, 3, "直流充正继电器线圈短地故障", False),
                (4, 4, "直流充正继电器线圈短电源故障", False),
                (5, 0, "直流充正继电器线圈开路故障", False),
                (5, 1, "直流充负继电器线圈短地故障", False),
                (5, 3, "直流充负继电器线圈短电源故障", False),
                (5, 4, "直流充负继电器线圈开路故障", False),
                (6, 0, "碰撞信号短电源或开路故障（来自硬线PWM波信号）", False),
                (6, 1, "碰撞信号短地故障（来自硬线PWM波信号）", True),
            ]
            fault_items = _load_dtc_fault_items(fault_file_path, default_defs)

            if result.success and isinstance(result.value, bytes) and len(result.value) >= 8:
                data_bytes = result.value
                reported_faults = []
                masked_faults = []
                for byte_idx, bit_idx, fault_name, is_masked in fault_items:
                    is_active = False
                    if byte_idx < len(data_bytes):
                        is_active = bool((data_bytes[byte_idx] >> bit_idx) & 0x01)
                    
                    self.variables[f"{fault_name}_RAW"] = 1 if is_active else 0
                    if is_active:
                        if is_masked:
                            self.variables[fault_name] = 0
                            masked_faults.append(fault_name)
                        else:
                            self.variables[fault_name] = 1
                            reported_faults.append(fault_name)
                    else:
                        self.variables[fault_name] = 0
                
                # 初始化 Byte 7 对应故障变量 (保留)
                self.variables["DTC_RFU_FAULT"] = 0
                self.variables["DTU_RFU_FAULT"] = 0
                
                masked_str = f" (已屏蔽白名单: {', '.join(masked_faults)})" if masked_faults else ""
                if reported_faults:
                    # 1. 出现白名单以外故障：故障爆出，输出为 FAIL，严禁清除故障
                    success = False
                    faults_str = ", ".join(reported_faults)
                    result_value = f"FAIL:检测到故障: {faults_str}{masked_str}"
                    hw_logger(f"[DTC诊断] {result_value}")
                else:
                    # 2. 除白名单故障外没有其他故障：执行清除故障操作 (向 0x7F0 发送 10 13 01 00，监听 0x7F8 肯定响应 11 13 01 40)
                    def _get_can_id(val, default):
                        if val is None: return default
                        val_str = str(val).strip()
                        try:
                            if val_str.lower().startswith("0x"): return int(val_str, 16)
                            return int(val_str)
                        except: return default

                    req_id = _get_can_id(eol_cfg["kwargs"].get("TX_ID") or eol_cfg["kwargs"].get("发送ID"), 0x7F0)
                    resp_id = _get_can_id(eol_cfg["kwargs"].get("RX_ID") or eol_cfg["kwargs"].get("接收ID"), 0x7F8)

                    clear_res = eol.clear_dtc_faults(req_id=req_id, resp_id=resp_id, timeout=eol_cfg["timeout"], logger=hw_logger)
                    if clear_res.success:
                        success = True
                        result_value = f"PASS:未检测到故障，故障码已清除{masked_str}"
                    else:
                        success = False
                        result_value = f"FAIL:未检测到非白名单故障，但清除故障超时/失败: {clear_res.error}{masked_str}"
                    hw_logger(f"[DTC诊断] {result_value}")
            else:
                for _, _, fault_name, _ in fault_items:
                    self.variables[fault_name] = 0
                    self.variables[f"{fault_name}_RAW"] = 0
                success = False
                result_value = f"FAIL: {result.error}"
            
            hw_logger(f"EOL {eol_cfg['op_name']} => {'PASS' if success else 'FAIL'} {result_value}")
            return success, result_value

        if eol_cfg["op_name"] in ("DTC故障清除", "DTC清除故障", "DTU清除故障", "DTU清障"):
            success = result.success
            if success:
                result_value = "PASS:DTC故障清除成功"
                for k in list(self.variables.keys()):
                    if "故障" in k or k.endswith("_RAW") or "DTC" in k or "DTU" in k:
                        self.variables[k] = 0
            else:
                result_value = f"FAIL:DTC故障清除超时/失败: {result.error}"
            hw_logger(f"[DTC故障清除] {result_value}")
            hw_logger(f"EOL {eol_cfg['op_name']} => {'PASS' if success else 'FAIL'} {result_value}")
            return success, result_value

        result_value = result.value if result.success else result.error
        if result.success and eol_cfg["op_name"] == "绝缘测试":
            self.variables["正极绝缘"] = getattr(result, "rp", 0.0)
            self.variables["负极绝缘"] = getattr(result, "rn", 0.0)
        elif result.success and "0x10" in eol_cfg["op_name"]:
            try:
                raw = finite_reading(result_value)
                self.variables['NTC_RAW'] = raw
                if eol_cfg['kwargs'].get('DIFF_AMBIENT') == '1':
                    actual = raw - finite_reading(self.variables['环境温度'])
                else:
                    actual = raw
                result_value = f"{actual:.2f}"
            except (ValueError, TypeError, KeyError) as exc:
                return False, f"Invalid NTC measurement: {exc}"

        hw_logger(f"EOL {eol_cfg['op_name']} => {'PASS' if result.success else 'FAIL'} {result_value}")
        return result.success, result_value

    def _read_instrument_once(self, mgr, params, hw_logger):
        device, p_str = params.get("device", "").lower(), str(params.get("params", ""))
        success, result_value = False, None
        target_ch = int(re.search(r"CH:(\d+)", p_str).group(1)) if "CH:" in p_str else None
        is_volt = is_voltage_read(p_str)
        requested_unit = configured_read_unit(p_str) if supports_read_units(device) else ""
                
        # 确定友好名称
        if "sim" in device: f_name = "电池模拟器"
        elif "2#" in device: f_name = "2# AFE电源"
        elif "3#" in device: f_name = "3# AFE电源"
        elif "afe" in device: f_name = "AFE电源"
        elif "main" in device or "dut" in device or "it6722" in device or "被测物供电" in device: f_name = "IT6722A被测物供电电源"
        elif "hv" in device or "ngi" in device: f_name = "NGI高压源"
        elif "control" in device: f_name = "控制板电源"
        elif "ca550" in device: f_name = "CA550校准仪"
        else: f_name = device.upper()

        if "simulator" in device or "电池模拟器" in device:
            ch = target_ch if target_ch is not None else self.channel_id
            read_options={'cancelled':lambda:not self.is_running} if callable(getattr(type(mgr),'simulator_channels',None)) else {}
            result_value = mgr.measure_voltage(ch, logger=hw_logger, **read_options) if is_volt else mgr.measure_current(ch, logger=hw_logger, **read_options)
            success = finite_reading(result_value) >= 0 or (not is_volt and callable(getattr(type(mgr),'simulator_channels',None)))
        elif any(x in device for x in ["afe", "main", "it6722", "dut power", "被测物供电", "hv_source", "hv source", "ngi", "高压源", "control power", "控制板"]):
            if any(x in device for x in ["main", "it6722", "dut power", "被测物供电"]): dev = mgr.dut_power
            elif any(x in device for x in ["hv_source", "hv source", "ngi", "高压源"]): dev = mgr.hv_source
            else: dev = None
            if dev:
                result_value = dev.measure_voltage() if is_volt else dev.measure_current()
                success = finite_reading(result_value) >= 0
            else:
                raise ValueError(f'设备没有可用驱动: {device}')
        elif "ca550" in device:
            if not mgr.ca550.is_connected:
                cfg = self.db_manager.load_sys_config() if self.db_manager else {}
                com_port = cfg.get("ca550_com", "COM42")
                mgr.ca550.port = com_port
                mgr.ca550.connect()
            mode = "measure" if "测量" in p_str or "measure" in p_str.lower() else "source"
            result_value = finite_reading(mgr.ca550.read_measure_data(mode))
            success = result_value is not None
        else:
            raise ValueError(f'没有对应的仪表读取逻辑: {device}')
                
        if success and result_value is not None:
            unit = "V" if is_volt else "mA" if ("ca550" in device or "simulator" in device or "\u7535\u6c60\u6a21\u62df\u5668" in device) else "A"
            if requested_unit:
                result_value = convert_reading(result_value, is_volt, requested_unit)
                unit = requested_unit
            result_value = MeasurementValue(result_value, unit)
            hw_logger(f"[回读成功] {f_name} => {result_value:.3f} {unit}")
        else:
            hw_logger(f"[回读失败] {f_name} => 读取异常")

        return success, result_value

    def _execute_sub_step_logic(self, sub_step: SubStep):
        """核心执行逻辑，返回 (success, result_value)"""
        mgr = self.device_manager
        if sub_step.type in (SubStepType.BARRIER, SubStepType.ACQUIRE_SEQ_LOCK, SubStepType.RELEASE_SEQ_LOCK):
            return True, "OK"
        params = sub_step.params
        retired_device = str(params.get('device', '')).lower()
        if any(name in retired_device for name in ('aging_board', 'aging board', '老化', 'chamber')):
            return False, "配方引用了已移除的老化设备，请更新工步"
        retry_tag = f" [重试 {self._retry_count}]" if self._retry_count > 0 else ""
        self.log_message.emit(self.channel_id, f"-> {sub_step.type.value}{retry_tag}: {params.get('device', '')} {params.get('action', '')}")
        from core.run_context import _safe
        step = self.steps[self.current_step_index] if 0 <= self.current_step_index < len(self.steps) else None
        details = dict(参数=params, 工步ID=sub_step.step_id, 失败策略=sub_step.fail_strategy.value)
        if step is not None:
            details.update(测试项=step.name, 下限=step.min_limit, 上限=step.max_limit,
                           单位=step.unit, 判定方式=step.standard_type)
        self.log_message.emit(self.channel_id,
            f"步骤{self.current_step_index+1} 子项{self.current_sub_step_index+1}{retry_tag} 执行参数: "
            + json.dumps(_safe(details), ensure_ascii=False, default=str))
        trace(
            "SUBSTEP_LOGIC_BEGIN",
            self.channel_id,
            step_idx=self.current_step_index,
            sub_idx=self.current_sub_step_index,
            type=sub_step.type.value,
            device=params.get("device", ""),
            action=params.get("action", ""),
            params=params.get("params", ""),
            retry=self._retry_count,
        )
        success, result_value = True, None
        sg_status = None
        def sg_update(phase, percent=None, result=""):
            import time
            if sg_status is not None:
                sg_status.update(phase=phase, percent=percent, result=result,
                                 active=not bool(result), elapsed=time.monotonic() - sg_status['started_at'])
                self._emit_flash_status(sg_status)
        def sg_read_progress(done, total, address):
            sg_update(f"回读校验中 · {done}/{total} 字节 · 0x{address:08X}", done / total * 100)
        try:
            position=(self.current_step_index,self.current_sub_step_index)
            context=self._dbc_group_context
            if context and not context['span'][0]<=position<=context['span'][1]:
                self._dbc_group_context=None
            if not mgr and sub_step.type != SubStepType.WAIT: raise ValueError("设备管理器未初始化")
            def hw_logger(msg):
                self.last_hw_log = msg
                text = str(msg)
                noisy_prefixes = (
                    "[DEBUG]",
                )
                is_noisy = text.startswith(noisy_prefixes)
                if os.environ.get("AGING_TRACE_HW_LOG") == "1" or not is_noisy:
                    trace("HW_LOG", self.channel_id, step_idx=self.current_step_index, sub_idx=self.current_sub_step_index, message=text)
                if os.environ.get("AGING_DEBUG_HW_LOG") == "1" or not is_noisy:
                    self.log_message.emit(self.channel_id, f"      {text}")

            self.periodic_can.check()
            from core.variable_steps import decode as decode_variable,execute as execute_variable
            variable_config=decode_variable(params.get('params',''))
            if variable_config:
                result_value=execute_variable(self,variable_config)
                hw_logger(f'[变量{variable_config["operation"]}] {variable_config["key"]}={result_value!r}')
            elif is_device_step(params.get('params','')):
                from core.read_until import settings, poll
                config = settings(params['params'])
                if config:
                    from core.device_steps import decode
                    _, read_spec, _ = decode(params['params'])
                    if not read_spec.read or read_spec.key in ('state', 'waveform'):
                        raise ValueError('范围等待仅支持数值回读工步')
                read = lambda: execute_device_step(mgr,params['params'],self.variables,hw_logger,
                                                       cancelled=lambda:not self.is_running)
                success,result_value = (poll(read, config, self.cancel_event.is_set, self.cancel_event.wait, hw_logger) if config else read())
            elif sub_step.type == SubStepType.SET_INSTRUMENT:
                device, p_str = params.get("device", "").lower(), str(params.get("params", ""))
                action = params.get("action", "")
                
                target_ch = None
                if "CH:" in p_str:
                    ch_match = re.search(r"CH:(\d+)", p_str)
                    if ch_match: target_ch = int(ch_match.group(1))

                if "simulator" in device or "电池模拟器" in device:
                    from core.simulator_commands import execute_simulator_command
                    # Keep the shared result recording and parent judgment path.
                    success, result_value = execute_simulator_command(
                        mgr, p_str, target_ch if target_ch is not None else self.channel_id,
                        hw_logger, self.cancel_event.is_set)

                elif "sg400" in device or "opteeq" in device or "力捷丰" in device:
                    programmer = getattr(mgr, "opteeq", None)
                    if not programmer:
                        success = False
                        hw_logger("[力捷丰SG400] 设备驱动未初始化")
                    elif "连接" in action:
                        success = programmer.connect()
                        result_value = programmer.query_state() if success else programmer.last_error
                        hw_logger(f"[力捷丰SG400] 连接检测: {result_value}")
                    elif "查询" in action:
                        if not programmer.is_connected:
                            programmer.connect()
                        result_value = programmer.query_state()
                        success = result_value != "Lost"
                        hw_logger(f"[力捷丰SG400] CH1状态: {result_value}")
                    elif '回读校验' in action:
                        import tempfile
                        from core.burn_recorder import verify_project_readback, parse_opj_metadata, record_burn_event
                        values = self._parse_key_values(p_str)
                        project = values.get('PROJECT', '')
                        sg_status = dict(kind='SG400', project=project, started_at=__import__('time').monotonic(), timeout=None)
                        sg_update('获取校验工程')
                        if not project:
                            raise ValueError('独立回读校验未配置工程')
                        # 此工步只读取，不运行烧录工程；可放在断电/上电工步之后。
                        with tempfile.TemporaryDirectory(prefix='sg400_readback_') as folder:
                            path = project
                            if not os.path.isfile(path):
                                if os.path.isabs(path):
                                    raise FileNotFoundError(f'校验基准不存在: {path}')
                                path = os.path.join(folder, os.path.basename(project))
                                if not programmer.download_project_file(project, path):
                                    raise RuntimeError(programmer.last_error or '无法获取校验工程')
                            meta = parse_opj_metadata(path)
                            source, actual, base, diff = verify_project_readback(programmer, project, path, progress_callback=sg_read_progress)
                            success = diff['is_match']
                            result_value = 'PASS' if success else f"FAIL: 回读不一致 {diff['mismatch_count']} 字节"
                            hw_logger(f'[SG400独立回读校验] {result_value}')
                            if values.get('ARCHIVE_REPORT', '1') != '0':
                                sg_update('保存校验报告')
                                record_burn_event({
                                    'sn': getattr(self, 'current_sn', '') or '测试',
                                    'project_name': os.path.basename(project),
                                    'source_file': meta.get('source_file'),
                                    'chip_name': meta.get('chip_name'),
                                    'result': 'PASS' if success else 'FAIL',
                                    'diagnosis': '独立回读校验：' + result_value,
                                    'diff_details': diff, 'source_bytes': source,
                                    'readback_bytes': actual, 'base_address': base,
                                })
                    elif "烧录" in action:
                        import time
                        values = self._parse_key_values(p_str)
                        project = values.get("PROJECT", "")
                        timeout = max(1.0, float(values.get("TIMEOUT", "300")))
                        sg_status = dict(kind='SG400', project=project, started_at=time.monotonic(), timeout=timeout)
                        sg_update('连接、上传与核对工程')
                        success = programmer.run_project(project)
                        if success:
                            sg_update('下载器执行中')
                            deadline = time.monotonic() + timeout
                            while time.monotonic() < deadline:
                                state = programmer.query_state()
                                if state == "Pass":
                                    result_value = "PASS"
                                    # --- 检查是否启用单片机 Flash 物理回读与源程序防呆比对 (Deep Diff) ---
                                    verify_diff = values.get("VERIFY_DIFF", "0") in ("1", "TRUE", "YES")
                                    if verify_diff:
                                        sg_update('获取工程，准备回读校验')
                                        hw_logger("[力捷丰SG400] 烧录硬件完成(Pass)，开始执行单片机 Flash 物理回读与源程序防呆比对...")
                                        try:
                                            from core.burn_recorder import parse_opj_metadata, record_burn_event
                                            # 定位本地工程文件以提取元数据与源程序
                                            proj_path = project
                                            if not os.path.isabs(proj_path):
                                                # 远端同名工程可能已更新，不复用旧缓存。
                                                proj_path = ''
                                            if not os.path.isfile(proj_path) and hasattr(programmer, "download_project_file"):
                                                os.makedirs("cache/projects", exist_ok=True)
                                                save_target = os.path.abspath(os.path.join("cache/projects", os.path.basename(project)))
                                                if programmer.download_project_file(project, save_target):
                                                    proj_path = save_target

                                            meta = parse_opj_metadata(proj_path) if os.path.isfile(proj_path) else {}
                                            hw_crc = programmer.get_checksum() if hasattr(programmer, "get_checksum") else ""
                                            diff_report = {}
                                            source_bytes, read_bytes, start_addr = b"", b"", 0

                                            if meta.get('valid') and hasattr(programmer, "read_chip"):
                                                hw_logger('[力捷丰SG400] 按工程实际数据段执行完整回读校验')
                                                from core.burn_recorder import verify_project_readback
                                                source_bytes, read_bytes, start_addr, diff_report = verify_project_readback(
                                                    programmer, project, proj_path, progress_callback=sg_read_progress)

                                                if read_bytes and source_bytes:
                                                    # 分段比较已完成，保留各段的真实差异地址。
                                                    diff_count = diff_report.get("mismatch_count", 0)
                                                    match_rate = diff_report.get("match_rate", 0.0)
                                                    if diff_count > 0:
                                                        success = False
                                                        result_value = f"DataDiffFail({diff_count} diffs, Match:{match_rate:.2f}%)"
                                                        hw_logger(f"[力捷丰SG400] ❌ 物理数据与源程序比对失败: 发现 {diff_count} 处字节差异！匹配率: {match_rate:.2f}%")
                                                    else:
                                                        result_value = "PASS"
                                                        hw_logger(f"[力捷丰SG400] 校验码: {hw_crc or meta.get('source_crc', 'OK')}")
                                                        hw_logger("[力捷丰SG400] ✅ 单片机物理数据比对成功: 100% 逐字节一致 (0 差异)")
                                                elif not read_bytes:
                                                    # 开启了防呆比对但回读为空：校验未执行，判失败
                                                    # （不能用硬件 checksum 兜底，其算法与源程序 CRC 不同，比较无意义）
                                                    success = False
                                                    result_value = "ReadbackEmpty(回读为空,未执行防呆比对)"
                                                    hw_logger("[力捷丰SG400] ❌ 物理数据回读为空，防呆比对未执行，判定失败")
                                            else:
                                                # 非固件或无物理扇区工程，无法逐字节比对，信任下载器自身校验
                                                success = False
                                                result_value = 'VerifyUnavailable(无法获取有效工程，未完成回读校验)'
                                                hw_logger(result_value)

                                            # 双轨留痕归档
                                            if values.get("ARCHIVE_REPORT", "1") in ("1", "TRUE", "YES"):
                                                sg_update('保存烧录报告')
                                                # 测试界面：文件以产品条码开头；未扫码时用「测试」前缀
                                                dut_sn = getattr(self, "current_sn", "") or "测试"
                                                try:
                                                    hw_log_text = programmer.get_recent_log()
                                                except Exception:
                                                    hw_log_text = ""
                                                record_burn_event({
                                                    "sn": dut_sn,
                                                    "project_name": os.path.basename(project),
                                                    "source_file": meta.get("source_file", "--"),
                                                    "chip_name": meta.get("chip_name", "--"),
                                                    "address_range": meta.get("summary_address_range", "未知"),
                                                    "expected_checksum": meta.get("opd_checksum") or meta.get("source_crc", "--"),
                                                    "actual_checksum": hw_crc,
                                                    "vcc_mv": meta.get("vcc_mv", 3300),
                                                    "duration_sec": timeout - max(0.0, deadline - time.monotonic()),
                                                    "result": "PASS" if success else "FAIL",
                                                    "diagnosis": "100% 逐字节一致" if success else result_value,
                                                    "diff_details": diff_report,
                                                    "hardware_log": hw_log_text,
                                                    "source_bytes": source_bytes,
                                                    "readback_bytes": read_bytes,
                                                    "base_address": start_addr,
                                                })
                                        except Exception as e:
                                            success = False
                                            result_value = f'回读校验失败: {e}'
                                            hw_logger(f"[力捷丰SG400] 物理数据比对处理异常: {e}")
                                    break
                                if state in ("Error", "Lost"):
                                    success = False
                                    result_value = state
                                    break
                                if self.cancel_event.wait(0.2):
                                    raise RuntimeError('测试已停止，烧录器实际状态需重新查询')
                            else:
                                success = False
                                result_value = "Timeout"
                        else:
                            result_value = programmer.last_error
                        hw_logger(f"[力捷丰SG400] CH1烧录结果: {result_value}")

                elif "e2_rfp" in device or "renesas" in device or "瑞萨" in device:
                    programmer = getattr(mgr, "e2_rfp", None)
                    if not programmer:
                        success = False
                        hw_logger("[瑞萨E2] 设备驱动未初始化")
                    elif "连接" in action:
                        success = programmer.connect()
                        result_value = programmer.query_state() if success else programmer.last_error
                        hw_logger(f"[瑞萨E2] E2 USB连接检测（不连接目标MCU）: {result_value}")
                    elif "查询" in action:
                        if not programmer.tool_ready:
                            programmer.connect()
                        result_value = programmer.query_state()
                        success = result_value != "Lost"
                        hw_logger(f"[瑞萨E2] USB/上次操作状态（目标连接未验证）: {result_value}")
                    elif "烧录" in action:
                        import time
                        values = {}
                        # 参数以 "/KEY:" 分隔解析，兼容 Windows 路径中的 "/"
                        normalized = p_str.replace("；", "/").replace("，", "/")
                        for part in re.split(r"/(?=\s*[A-Za-z_][A-Za-z0-9_]*\s*[:：])", normalized):
                            part = part.strip()
                            if part and ":" in part:
                                key, value = part.split(":", 1)
                                values[key.strip().upper()] = value.strip()
                        project = values.get("FILE", "") or values.get("PROJECT", "")
                        timeout = max(1.0, float(values.get("TIMEOUT", "300")))
                        if values.get("DEVICE_FAMILY"):
                            programmer.device_family = values["DEVICE_FAMILY"]
                        if values.get("INTERFACE"):
                            programmer.interface = values["INTERFACE"]
                        if values.get("SPEED"):
                            programmer.speed = values["SPEED"]
                        start_time = time.monotonic()
                        sg_status = dict(kind='E2', project=project, started_at=start_time, timeout=timeout)
                        sg_update('启动瑞萨下载工程')
                        def e2_progress(line):
                            hw_logger(f"[rfp-cli] {line}")
                            sg_update('下载器执行中 · ' + str(line).strip())
                        success = programmer.run_project(
                            project, timeout_s=int(timeout),
                            progress_cb=e2_progress,
                        )
                        duration = time.monotonic() - start_time
                        if success:
                            result_value = "PASS"
                            # --- 检查是否启用单片机 Flash 物理回读防呆比对 (Deep Diff) ---
                            verify_diff = values.get("VERIFY_DIFF", "0") in ("1", "TRUE", "YES")
                            if verify_diff:
                                sg_update('读取芯片数据')
                                hw_logger("[瑞萨E2] 烧录硬件完成(Pass)，开始执行单片机 Flash 物理回读与防呆比对...")
                                try:
                                    from core.renesas_verify import verify_readback
                                    from core.burn_recorder import record_burn_event
                                    hw_crc = programmer.get_checksum() if hasattr(programmer, "get_checksum") else ""
                                    verify_address = int(values['VERIFY_ADDRESS'], 0) if values.get('VERIFY_ADDRESS') else None
                                    source_bytes, rb_bytes, base_address, diff_report = verify_readback(
                                        programmer, project, verify_address, cancelled=self.cancel_event.is_set)
                                    success = diff_report['is_match']
                                    result_value = "PASS" if success else "FAIL: readback mismatch"
                                    hw_logger(f"[E2] Source/readback match: {diff_report['match_rate']:.2f}%")
                                    # 双轨留痕归档
                                    if values.get("ARCHIVE_REPORT", "1") in ("1", "TRUE", "YES"):
                                        sg_update('保存烧录报告')
                                        # 测试界面：文件以产品条码开头；未扫码时用「测试」前缀
                                        dut_sn = getattr(self, "sn_barcode", "") or "测试"
                                        record_burn_event({
                                            "sn": dut_sn,
                                            "project_name": os.path.basename(project),
                                            "source_file": os.path.basename(project),
                                            "chip_name": getattr(programmer, "device_family", "E2"),
                                            "address_range": "未知",
                                            "expected_checksum": "--",
                                            "actual_checksum": hw_crc,
                                            "vcc_mv": 3300,
                                            "duration_sec": duration,
                                            "result": "PASS" if success else "FAIL",
                                            "diagnosis": "烧录成功完成" if success else str(result_value),
                                            "readback_bytes": rb_bytes,
                                            "source_bytes": source_bytes,
                                            "diff_details": diff_report,
                                            "base_address": base_address,
                                        })
                                except Exception as e:
                                    success = False
                                    result_value = f"FAIL: verification incomplete: {e}"
                                    hw_logger(f"[瑞萨E2] 物理数据比对处理异常: {e}")
                        else:
                            result_value = programmer.last_error
                        hw_logger(f"[瑞萨E2] 烧录结果: {result_value}")

                elif any(x in device for x in ["afe", "main", "it6722", "dut power", "被测物供电", "hv source", "hv_source", "ngi", "高压源", "control power", "控制板"]):
                    from core.legacy_controls import execute_power
                    is_hv = any(x in device for x in ["hv_source", "hv source", "ngi", "高压源"])
                    is_dut = any(x in device for x in ["it6722", "dut power", "被测物供电", "main"])
                    driver = getattr(mgr, 'hv_source' if is_hv else 'dut_power', None) if is_hv or is_dut else None
                    if driver is None:
                        raise ValueError(f'Device has no active driver: {device}')
                    if is_hv and "清除保护" in action:
                        from core.instrument_control import control_power
                        success = control_power(driver, 'clear_errors', logger=hw_logger,
                                                cancelled=self.cancel_event.is_set)
                    else:
                        success, result_value = execute_power(driver, p_str, hw_logger, self.cancel_event.is_set)

                elif "easy320" in device:
                    from core.easy320_channels import configured_channels, MAX_DO_INDEX
                    channel_rows = configured_channels(mgr)
                    allowed = {row['index']: row for row in channel_rows}
                    from core.relay_combination import decode as decode_relays, execute as execute_relays
                    relay_targets = decode_relays(p_str)
                    if relay_targets is not None:
                        success = execute_relays(relay_targets, mgr.easy320, allowed, self.cancel_event.is_set, hw_logger)
                    elif "全部" in params.get("action", ""):
                        if callable(getattr(type(mgr.easy320),'control_selected_relays',None)):
                            success=mgr.easy320.control_selected_relays(list(allowed),False)
                        else:
                            success = True
                            for index in allowed:
                                if not mgr.easy320.write_relay(index, False):
                                    success = False
                                    break
                        hw_logger(f"-> Easy320动作: 发:全部断开 回:{'全部断开' if success else '异常'}")
                    else:
                        state = "闭合" in params.get("action", "")
                        ch_list = [ch.strip() for ch in p_str.replace('，', ',').split(',')]
                        if (not ch_list or any(not re.fullmatch(r'[0-9]+', ch) for ch in ch_list)
                                or any(not 1 <= int(ch) <= MAX_DO_INDEX + 1 for ch in ch_list)):
                            raise ValueError('EASY320通道必须为有效的正整数列表，不能为空')
                        if any(int(ch)-1 not in allowed for ch in ch_list):
                            raise ValueError('EASY320工步包含当前导入表中不存在的DO通道，请重新编辑工步')
                        for ch in ch_list:
                            if state and self.cancel_event.is_set():
                                raise RuntimeError('测试已停止')
                            success = mgr.easy320.write_relay(int(ch)-1, state)
                            if not success: break
                        action_str = "闭合" if state else "断开"
                        formatted_chs = []
                        for ch in ch_list:
                            row = allowed[int(ch)-1]
                            alias = f"[{row['name']}]" if row['name'] else ''
                            formatted_chs.append(row['do'] + alias)
                        ch_str = ",".join(formatted_chs)
                        ch_info = f"CH({ch_str})" if ch_str else ""
                        hw_logger(f"-> Easy320动作 {ch_info}: 发:{action_str} 回:{action_str if success else '异常'}")

                elif "ca550" in device:
                    from core.legacy_controls import execute_calibrator
                    success, result_value = execute_calibrator(mgr.ca550, p_str, hw_logger, self.cancel_event.is_set)

                else:
                    raise ValueError(f'没有对应的仪表控制逻辑: {device}')


            elif sub_step.type == SubStepType.READ_INSTRUMENT:
                device, p_str = params.get("device", "").lower(), str(params.get("params", ""))
                if "变量操作" in params.get("device", "") or "var:" in p_str.lower() or "读取变量" in params.get("action", ""):
                    kv = self._parse_key_values(p_str)
                    var_name = kv.get("VAR", "").strip()
                    val = self.variables.get(var_name, None)
                    if val is not None:
                        if isinstance(val, MeasurementValue):
                            result_value = val
                        elif isinstance(val, (int, float)):
                            if "绝缘" in var_name:
                                result_value = f"{val:.1f}kΩ"
                            elif "CSC_CELL" in var_name or "cell" in var_name.lower():
                                result_value = f"{val:.3f}"
                            else:
                                result_value = f"{val:.2f}"
                        else:
                            result_value = str(val)
                        success = True
                    else:
                        result_value = "变量未找到"
                        success = False
                    hw_logger(f"读取变量 [{var_name}] => {result_value}")
                else:
                    from core.read_until import settings, poll
                    config = settings(p_str)
                    read = lambda: self._read_instrument_once(mgr, params, hw_logger)
                    success, result_value = (poll(read, config, self.cancel_event.is_set, self.cancel_event.wait, hw_logger) if config else read())

            elif sub_step.type == SubStepType.CAN_DBC:
                from core.dbc_signals import decode_step,execute_read,refresh_group,execute_snapshot
                from core.read_until import settings
                config=decode_step(params.get('params',''))
                driver=getattr(mgr,'pcan',None)
                monitor=mgr.dbc_monitor
                group=self._dbc_sampling_group(position)
                if group:
                    context=self._dbc_group_context
                    attempt=(self.step_retry_count,self._retry_count)
                    if context and context['id']==group['id'] and context.get('error'):
                        if position==context['entry'] and attempt!=context['attempt']:
                            context=None
                        else:raise RuntimeError(context['error'])
                    if not context or context['id']!=group['id']:
                        if driver is None or not driver.is_connected:raise ValueError('DBC采样需要已连接的配方CAN卡')
                        requests,positions=self._dbc_group_requests(group,driver,monitor.asset)
                        state=dict(id=group['id'],span=(group['start'],group['end']),entry=position,attempt=attempt)
                        try:
                            state.update(refresh_group(monitor,requests,group['name'],self.cancel_event.is_set,
                                                       self.cancel_event.wait,hw_logger))
                            state['positions']=positions
                        except Exception as exc:
                            state['error']=str(exc)
                            self._dbc_group_context=state
                            raise
                        self._dbc_group_context=context=state
                    success,result_value=execute_snapshot(context,config,context['positions'][position],
                                                        self.cancel_event.is_set,hw_logger)
                else:
                    if driver is None or not driver.is_connected:raise ValueError('DBC读取需要已连接的配方CAN卡')
                    interface=self._dbc_interface(driver,params)
                    success,result_value=execute_read(monitor,config,interface,self.cancel_event.is_set,
                        self.cancel_event.wait,hw_logger,settings(params.get('params','')))
                if success and config.get('variable'):self.variables[config['variable']]=result_value

            elif sub_step.type == SubStepType.CAN_SEND:
                success = self._send_can_step(mgr, params, hw_logger)

            elif sub_step.type == SubStepType.CAN_INTERACT:
                device = str(params.get("device", "")).lower()
                from core.can_periodic import decode as decode_periodic
                periodic=decode_periodic(params.get('params',''))
                if periodic is not None:
                    if periodic['operation']=='start':
                        driver=self._step_can_driver(mgr.pcan,params)
                        self.periodic_can.start(periodic,driver,self.channel_id)
                    else:
                        self.periodic_can.stop(periodic['name'])
                    success,result_value=True,'PASS'
                elif "uds" in device:
                    import base64
                    values = self._parse_key_values(str(params.get("params", "")))
                    try:
                        if 'WRITE64' in values:
                            self.variables.pop('主板序列号',None)
                            from core.uds_field_write import decode as decode_write, execution_values
                            write_config=decode_write(values['WRITE64'])
                            if write_config.get('variable'):self.variables.pop(write_config['variable'],None)
                            compiled,write_text=execution_values(write_config,self.sn_barcode,getattr(self,"_uds_field_write_now",None))
                            self.variables['主板序列号']=write_text
                            hw_logger('[UDS写入变量] 主板序列号='+write_text)
                            values.update(compiled)
                            hw_logger('[UDS 2E 三字段写入] ASCII='+write_text)
                            literal_length=len(write_config['literal'])
                            hw_logger(f"[UDS 2E 字段快照] 固定字段={write_config['literal']!r} 日期={write_text[literal_length:literal_length+6]} 条码片段={write_text[literal_length+6:]!r} ASCII长度={len(write_text)} PDU长度={len(write_text)+3}")
                        tx_id = int(values.get("TX_ID", "0x7E0"), 0)
                        rx_id = int(values.get("RX_ID", "0x7E8"), 0)
                        if not all(0<=ident<=0x1FFFFFFF for ident in (tx_id,rx_id)) or tx_id==rx_id:
                            raise ValueError('请求与响应ID必须为有效且不同的CAN ID')
                        # FD-capable recipe timing does not force every UDS
                        # request to use FD. Respect the step's explicit type.
                        can_type = int(values.get("TYPE", getattr(getattr(mgr, "pcan", None), "default_can_type", 0)))
                        if can_type not in (0, 1, 2):
                            raise ValueError('UDS CAN类型必须为Classic、FD或FD+BRS')
                        timeout = max(0.001, float(values.get("TIMEOUT", "1000")) / 1000.0)
                        cycles = max(1, int(values.get("CYCLES", "1")))
                        interval = max(0.0, float(values.get("INTERVAL", "1")) / 1000.0)
                        script = base64.urlsafe_b64decode(values.get("LIST64", "")).decode("utf-8")
                        from core.uds_sequence import settings_from, SessionKeepAlive, delay_seconds, prepare_request
                        sequence_settings = settings_from(values.get('UDSCFG64', ''))
                        if sequence_settings['frame_format']=='standard' and max(tx_id,rx_id,sequence_settings['functional_id'])>0x7FF:
                            raise ValueError('标准帧CAN ID不能超过0x7FF')
                        driver = getattr(mgr, "pcan", None)
                        if not driver:
                            raise RuntimeError("USB PCAN-FD 驱动未初始化")
                        self.periodic_can.assert_uds_ids_available(driver, tx_id, rx_id)
                        if callable(getattr(type(driver), 'check_bus_health', None)):
                            driver.check_bus_health()
                        if not driver.is_connected and not driver.connect():
                            raise RuntimeError(driver.last_error or "USB PCAN-FD 连接失败")
                        driver = self._step_can_driver(driver, params)
                        uds = driver.create_uds_client(
                            tx_id=tx_id,
                            rx_id=rx_id,
                            can_fd=can_type > 0,
                            brs=can_type == 2,
                            timeout=timeout,
                            padding=sequence_settings['padding'],block_size=sequence_settings['block_size'],st_min=sequence_settings['st_min'],
                            p2_star_timeout=sequence_settings['p2_star_ms']/1000 if values.get('UDSCFG64') else max(5.0, timeout * 5.0),
                        )
                        uds.transport.cancel_event = self.cancel_event
                        uds.transport.logger = hw_logger
                        hw_logger(f'UDS?? TX=0x{tx_id:X} RX=0x{rx_id:X} TYPE={can_type} PCH={values.get("PCH", "0")} ??={getattr(driver, "nominal_bitrate", "?")} ??={getattr(driver, "data_bitrate", "?")} PDU????={64 if can_type > 0 else 8}')
                        clients = {'physical': uds}
                        if sequence_settings['frame_format']!='auto':
                            uds.transport.extended_id=sequence_settings['frame_format']=='extended'
                        def client_for(address):
                            if address not in clients:
                                clients[address] = driver.create_uds_client(
                                    tx_id=sequence_settings['functional_id'],rx_id=rx_id,
                                    can_fd=can_type>0,brs=can_type==2,timeout=timeout,
                                    padding=sequence_settings['padding'],block_size=sequence_settings['block_size'],st_min=sequence_settings['st_min'],
                                    p2_star_timeout=sequence_settings['p2_star_ms']/1000)
                                clients[address].transport.cancel_event = self.cancel_event
                                clients[address].transport.logger = hw_logger
                                if sequence_settings['frame_format']!='auto':
                                    clients[address].transport.extended_id=sequence_settings['frame_format']=='extended'
                                clients[address].transport.flow_control_tx_id=tx_id
                            return clients[address]
                        results = []
                        parsed_values = {}
                        success = True
                        rows = [line.strip() for line in script.splitlines()
                                if line.strip() and not line.strip().startswith("#")]
                        from core.seedkey import decode as decode_security, unlock as unlock_security
                        security = decode_security(values['SEC64']) if values.get('SEC64') else None
                        if not rows:
                            raise ValueError("UDS交互列表为空")
                        from core.uds_result_config import decode as decode_results, check_expected
                        row_config = decode_results(values.get('UDSMETA64', ''), rows)
                        if any(row=='@SECURITY' and config.get('enabled',True) for row,config in zip(rows,row_config)) and security is None:
                            raise ValueError('UDS交互列表包含DLL解锁，但未配置安全访问DLL')
                        from core.uds_sequence import validate_sequence
                        validate_sequence(rows,row_config,sequence_settings)
                        if not any(config.get('enabled',True) for config in row_config):
                            raise ValueError('没有启用的UDS序列行')
                        output_row = next((i for i, config in enumerate(row_config, 1) if config.get('output')), None)
                        output_value = None
                        with SessionKeepAlive(client_for(sequence_settings['keepalive_address']),sequence_settings,
                                              self.cancel_event.is_set,hw_logger) as heartbeat:
                            for cycle in range(1, cycles + 1):
                                for row_index, row in enumerate(rows, 1):
                                    if self.cancel_event.is_set():
                                        raise RuntimeError('测试已停止，未发送后续UDS请求')
                                    heartbeat.check()
                                    if not row_config[row_index-1].get('enabled',True):
                                        hw_logger(f'UDS[序号{row_index}] 未启用，跳过')
                                        continue
                                    delay = delay_seconds(row)
                                    if delay is not None:
                                        hw_logger(f'UDS[轮次{cycle}/{cycles} 序号{row_index}] 延时 {delay*1000:g}ms')
                                        heartbeat.wait(delay)
                                        continue
                                    options = row_config[row_index-1]
                                    address = options.get('address', 'default')
                                    if address == 'default':address = sequence_settings['address']
                                    active_uds = client_for(address)
                                    if row == '@SECURITY':
                                        if address=='functional':raise ValueError('DLL安全解锁请使用物理地址')
                                        response = unlock_security(active_uds, security, hw_logger, self.cancel_event.is_set)
                                        results.append('安全访问PASS')
                                        continue
                                    row_main, pipe_sep, rule_text = row.partition("|")
                                    request_text, separator, expected_text = row_main.partition("=>")
                                    request_tokens = request_text.replace(",", " ").split()
                                    request = bytes(int(token, 16) for token in request_tokens)
                                    if not request:
                                        raise ValueError(f"第{row_index}行请求为空")
                                    request = prepare_request(request, options.get('suppress',False), address=='functional')
                                    response = (active_uds.request(request[0],request[1:],suppress_positive_response=True)
                                                if options.get('suppress') else active_uds.request(request[0],request[1:]))
                                    hw_logger(f'UDS[地址] {address} TX_ID=0x{(tx_id if address=="physical" else sequence_settings["functional_id"]):X}')
                                    if options.get('suppress'):
                                        hw_logger(f'UDS[轮次{cycle}/{cycles} 序号{row_index}] TX={request.hex(" ").upper()} 已发送，未检查响应')
                                        results.append('已发送（未检查响应）')
                                        if interval:heartbeat.wait(interval)
                                        continue
                                    raw = bytes(response.raw)
                                    raw_text = raw.hex(" ").upper() if raw else response.error or "无响应"
                                    hw_logger(f'UDS[收发] 轮次{cycle}/{cycles} 序号{row_index} TX={request.hex(" ").upper()} RX={raw_text}')
                                    if not response.positive:
                                        nrc_text = f" NRC=0x{response.nrc:02X} {response.nrc_name}" if response.nrc is not None else ""
                                        raise RuntimeError(f"第{cycle}轮第{row_index}行UDS失败: {raw_text}{nrc_text}")
                                    if separator and options.get('check', True):
                                        expected_tokens = expected_text.replace(",", " ").split()
                                        if len(raw) < len(expected_tokens):
                                            raise RuntimeError(
                                                f"第{cycle}轮第{row_index}行响应过短: "
                                                f"期望{len(expected_tokens)}字节，实际{len(raw)}字节"
                                            )
                                        for pos, token in enumerate(expected_tokens):
                                            if token in ("??", "**"):
                                                continue
                                            if raw[pos] != int(token, 16):
                                                raise RuntimeError(
                                                    f"第{cycle}轮第{row_index}行第{pos + 1}字节不匹配: "
                                                    f"期望{token.upper()}，实际{raw[pos]:02X}；完整响应={raw_text}"
                                                )
                                    parsed_display = ""
                                    if pipe_sep and rule_text.strip():
                                        from .uds_parser import parse_uds_rule_string, parse_uds_response_data
                                        rule = parse_uds_rule_string(rule_text.strip())
                                        if rule is None:
                                            raise ValueError(f'第{row_index}行UDS数据提取规则无效，不能输出原始报文代替解析结果')
                                        if rule:
                                            p_res = parse_uds_response_data(raw, rule)
                                            if not p_res.get("success"):
                                                raise RuntimeError(
                                                    f"第{cycle}轮第{row_index}行UDS数据提取失败: {p_res.get('error', '未知错误')}；完整响应={raw_text}"
                                                )
                                            p_val = p_res.get("value")
                                            try:
                                                check_expected(p_val, options.get('expected', '') if options.get('check',True) else '', rule['mode'])
                                            except ValueError as exc:
                                                raise ValueError(f'第{cycle}轮第{row_index}行 {exc}；完整响应={raw_text}') from exc
                                            p_var = p_res.get("var_name")
                                            parsed_display = str(p_res.get("display", p_val))
                                            # Keep each row's latest value; cycle history stays in logs.
                                            parsed_values[row_index] = p_val
                                            if p_var:
                                                self.variables[p_var] = p_val
                                                hw_logger(f"UDS[变量写回] self.variables['{p_var}'] = {p_val}")

                                    if row_index == output_row:
                                        output_value = parsed_values.get(row_index, raw_text)
                                    if parsed_display:
                                        results.append(parsed_display)
                                        hw_logger(
                                            f"UDS[轮次{cycle}/{cycles} 序号{row_index}] "
                                            f"TX={request.hex(' ').upper()} RX={raw_text} -> 解析: {parsed_display} PASS"
                                        )
                                    else:
                                        results.append(raw_text)
                                        hw_logger(
                                            f"UDS[轮次{cycle}/{cycles} 序号{row_index}] "
                                            f"TX={request.hex(' ').upper()} RX={raw_text} PASS"
                                        )
                                    if interval and not (cycle == cycles and row_index == len(rows)):
                                        heartbeat.wait(interval)
                            heartbeat.check()
                        if 'WRITE64' in values:
                            if write_config.get('variable'):
                                self.variables[write_config['variable']]=write_text
                                hw_logger('[UDS写入变量] '+write_config['variable']+'='+write_text)
                        if output_row is not None:
                            result_value = str(output_value)
                        elif len(parsed_values) == 1:
                            result_value = str(next(iter(parsed_values.values())))
                        elif parsed_values:
                            result_value = ' | '.join(str(value) for value in parsed_values.values())
                        else:
                            result_value = " | ".join(results)
                        if 'WRITE64' in values:result_value='6E'
                    except Exception as exc:
                        success = False
                        result_value = str(exc)
                        hw_logger(f"UDS交互失败: {result_value}")
                else:
                    eol_cfg = self._parse_eol_params(params)
                    if eol_cfg: success, result_value = self._execute_eol_protocol(mgr, params, hw_logger)
                    else:
                        board = self._get_can_board(mgr, params)
                        cfg = self._parse_can_params(params)
                        w_id = cfg["wait_id"] if cfg["wait_id"] >= 0 else cfg["can_id"]
                        msg = board.can.send_and_wait_response(cfg["channel_id"], cfg["can_id"], cfg["can_type"], cfg["dlc"], cfg["data"], w_id, cfg["timeout"], matcher=cfg['matcher'])
                        success = msg is not None
                        result_value = msg.get("data", b"").hex(" ").upper() if msg else "TIMEOUT"
                        hw_logger(f"CAN REQ CH:{cfg['channel_id']} ID=0x{cfg['can_id']:X} WAIT=0x{w_id:X} => {result_value}")
                        if success:
                            result_value = self._parse_can_result(params, msg.get('data', b''), hw_logger)

            elif sub_step.type == SubStepType.CAN_RECEIVE:
                board = self._get_can_board(mgr, params)
                cfg = self._parse_can_params(params)
                board.can.clear_rx_history(cfg["can_id"])
                msg = board.can.wait_for_message(
                    can_id=cfg["can_id"],
                    channel_id=cfg["channel_id"],
                    predicate=cfg['matcher'],
                    timeout=cfg["timeout"]
                )
                success = msg is not None
                if success:
                    rx_data = msg.get("data", b"").hex(" ").upper()
                    result_value = rx_data
                    hw_logger(f"CAN RX_WAIT CH:{cfg['channel_id']} ID=0x{cfg['can_id']:X} => PASS DATA: {rx_data}")
                    result_value = self._parse_can_result(params, msg.get('data', b''), hw_logger)
                else:
                    result_value = "TIMEOUT"
                    hw_logger(f"CAN RX_WAIT CH:{cfg['channel_id']} ID=0x{cfg['can_id']:X} => FAIL")

            elif sub_step.type == SubStepType.EOL_PROTOCOL:
                success, result_value = self._execute_eol_protocol(mgr, params, hw_logger)

            elif sub_step.type == SubStepType.READ_VAR:
                params_str = params.get("params", "")
                kv = self._parse_key_values(params_str)
                var_name = kv.get("VAR", "").strip()
                
                val = self.variables.get(var_name, None)

                if val is not None:
                    if isinstance(val, (int, float)):
                        finite_reading(val)
                        result_value = val
                    else:
                        result_value = val
                    success = True
                else:
                    result_value = "变量未找到"
                    success = False
                hw_logger(f"读取变量 [{var_name}] => {result_value}")

            elif sub_step.type == SubStepType.CALCULATE:
                params_str = params.get("params", "")
                formula_val = ""
                var_name = ""
                if "FORMULA:" in params_str:
                    if "VAR:" in params_str:
                        if params_str.find("FORMULA:") < params_str.find("VAR:"):
                            f_part, v_part = params_str.split("VAR:", 1)
                            formula_val = f_part.replace("FORMULA:", "").strip()
                            var_name = v_part.strip()
                        else:
                            v_part, f_part = params_str.split("FORMULA:", 1)
                            var_name = v_part.replace("VAR:", "").strip()
                            formula_val = f_part.strip()
                    else:
                        formula_val = params_str.replace("FORMULA:", "").strip()
                else:
                    formula_val = params_str.strip()
                
                # 清洗逗号、分号、斜杠
                formula_val = formula_val.strip(",").strip(";").strip("/").strip()
                var_name = var_name.strip(",").strip(";").strip("/").strip()
                
                if not formula_val:
                    success = False
                    result_value = "公式为空"
                else:
                    try:
                        from core.formula_eval import evaluate_formula
                        local_vars = dict(self.variables)
                        local_vars.update({f"STEP{i+1}": entry[0] for i, entry in enumerate(self.current_step_results)})
                        calc_val = evaluate_formula(formula_val, local_vars)

                        if var_name:
                            self.variables[var_name] = calc_val
                            hw_logger(f"[公式计算] 公式: {formula_val} => 计算值: {calc_val:.4f}, 已写入变量: {var_name}")
                        else:
                            hw_logger(f"[公式计算] 公式: {formula_val} => 计算值: {calc_val:.4f}")
                            
                        result_value = calc_val
                        success = True
                    except Exception as e:
                        hw_logger(f"[公式计算] 计算异常: {e} (公式: {formula_val})")
                        result_value = f"计算错误: {e}"
                        success = False

            elif sub_step.type == SubStepType.MES_CHECK:
                from core.mes_client import DEFAULT_MES_URL, MesClient

                from core import system_config
                mes_config = self.db_manager.load_sys_config() if self.db_manager is not None else system_config.load()
                client = MesClient(
                    mes_config.get("mes_url", DEFAULT_MES_URL),
                    mes_config.get("mes_timeout", 10),
                )
                mes_result = client.move_in_check(
                    mes_config.get("mes_equipment_no", "GS-APP-08"),
                    mes_config.get("mes_order_no", ""),
                    getattr(self, "sn_barcode", ""),
                    self.operator_name,
                )
                success = mes_result.passed
                result_value = mes_result.message
                self.log_message.emit(
                    self.channel_id,
                    f"[MES] moveInCheck {'PASS' if success else 'FAIL'}: {mes_result.message}",
                )

            elif sub_step.type == SubStepType.APP_FLASH_CONFIG:
                from core.app_flash_step import find_configured_app_program
                if self.cancel_event.is_set():return False, 'STOPPED'
                values = self._app_flash_values(params)
                success, result_value = find_configured_app_program(
                    values.get("HOST", ""),
                    values.get("INI", "config.ini"),
                    values.get("SECTION", "App"),
                    values.get("KEY", "addr"),
                    values.get("APPNAME", ""),
                    logger=hw_logger,
                )
                hw_logger(f"[APP刷写配置] 查找结果: {result_value}")

            elif sub_step.type == SubStepType.APP_FLASH:
                from core.app_flash_step import run_configured_app_flash
                if self.cancel_event.is_set():return False, 'STOPPED'
                values = self._app_flash_values(params)
                timeout = max(0.1, float(values.get("TIMEOUT", "300")))
                success, result_value = run_configured_app_flash(
                    values.get("HOST", ""), timeout, logger=hw_logger,
                    progress_callback=self._emit_flash_status,
                    pcan=getattr(mgr, "pcan", None),
                    request_id=self._parse_int(values.get("FLASH_TX_ID"), 0x7E4),
                    response_id=self._parse_int(values.get("FLASH_RX_ID"), 0x7EC),
                    cancelled=lambda: not self.is_running,
                )
                hw_logger(f"[APP刷写] 结果: {result_value}")

            elif sub_step.type == SubStepType.WAIT:
                delay = finite_reading(params.get("params", 1000))
                if delay < 0 or not delay.is_integer():
                    raise ValueError("Delay must be a non-negative integer in milliseconds")
                ms = int(delay)
                if ms < 2000: QTimer.singleShot(ms, self.on_sub_step_complete)
                else:
                    rem = ms
                    def tick():
                        nonlocal rem
                        if not self.is_running: return
                        if rem <= 0: self.on_sub_step_complete()
                        else:
                            if rem % 1000 == 0 or rem == ms: self.log_message.emit(self.channel_id, f"      [倒计时] 剩余 {rem/1000:.0f}s...")
                            i = min(1000, rem); rem -= i; QTimer.singleShot(i, tick)
                    tick()
                # 触发状态收集和广播，防止前台显示卡死
                self.current_step_results.append(("OK", bool(sub_step.params.get("is_judgment", False))))
                self.sub_step_finished.emit(self.channel_id, self.current_step_index, self.current_sub_step_index, "PASS", "OK")
                return True, "OK"

        except Exception as e:
            trace("SUBSTEP_EXCEPTION", self.channel_id, step_idx=self.current_step_index, sub_idx=self.current_sub_step_index, error=str(e))
            result_value=f'FAIL: {e}'
            self.log_message.emit(self.channel_id, f"[!] 执行异常: {str(e)}"); success = False

        control_device = str(params.get('device', '')).lower()
        try:self.periodic_can.check()
        except RuntimeError as exc:
            success=False;result_value='FAIL: '+str(exc)
        if not success and 'simulator' not in control_device and any(name in control_device for name in ('ca550', 'hv_source', 'hv source', 'ngi', '高压源', 'it6722', 'dut power', '被测物供电', 'main')):
            key = 'ca550' if 'ca550' in control_device else ('hv_source' if any(name in control_device for name in ('hv_source','hv source','ngi','高压源')) else 'dut_power')
            driver = getattr(mgr, key, None)
            details = getattr(driver, 'last_control', None)
            reason = getattr(driver, 'last_error', '')
            if isinstance(details, dict) and details:
                hw_logger(f"[仪器] 控制确认详情: {details}")
            if isinstance(reason, str) and reason:
                result_value = f"FAIL: {reason}"

        # 无条件收集每一个子工步的执行结果与数据，由最终提取层进行智能精细化提取
        if sg_status is not None:
            sg_status['detail'] = str(result_value or '')
            sg_update('完成' if success else '失败', 100 if success else None, 'PASS' if success else 'FAIL')
        if success:
            val = result_value if result_value is not None and result_value != "" else "PASS"
        else:
            val = result_value if result_value not in (None, "") else "FAIL"
        
        if sub_step.type == SubStepType.CAN_RECEIVE and not self._parse_key_values(str(params.get('params', ''))).get('PARSE'):
            val = "OK" if success else "NG"
            
        self.last_output=dict(ok=success,value=result_value)
        self.step_outputs[sub_step.step_id]=dict(self.last_output)
        self.current_step_results.append((val, bool(sub_step.params.get("is_judgment", False))))

        trace("SUBSTEP_LOGIC_END", self.channel_id, step_idx=self.current_step_index, sub_idx=self.current_sub_step_index, success=success, result=result_value)
        self.sub_step_finished.emit(self.channel_id, self.current_step_index, self.current_sub_step_index, "PASS" if success else "FAIL", result_value)
        return success, result_value

    def execute_sub_step(self, sub_step: SubStep):
        if not self.is_running:
            trace("SUBSTEP_EXEC_SKIPPED", self.channel_id, running=self.is_running, step_idx=self.current_step_index, sub_idx=self.current_sub_step_index)
            return
        
            
        params = sub_step.params
        if params.get('skip_runtime') or str(params.get('name', '')).strip().startswith('#'):
            self.sub_step_finished.emit(self.channel_id, self.current_step_index,
                                        self.current_sub_step_index, '跳过', None)
            self.on_sub_step_complete()
            return
        trace(
            "SUBSTEP_START",
            self.channel_id,
            step_idx=self.current_step_index,
            sub_idx=self.current_sub_step_index,
            type=sub_step.type.value,
            device=params.get("device", ""),
            action=params.get("action", ""),
            params=params.get("params", ""),
        )
        results_before_attempt = len(self.current_step_results)
        success, result_value = self._execute_sub_step_logic(sub_step)
        if self.record_error:
            self.stop('RECORD_ERROR')
            self.test_finished.emit(self.channel_id, False)
            return
        if self.cancel_event.is_set():
            return
        trace("SUBSTEP_EXEC_RESULT", self.channel_id, step_idx=self.current_step_index, sub_idx=self.current_sub_step_index, success=success, result=result_value)
        
        # 核心修复：如果是异步工步（延时）或正在等待同步，不在此处触发推进
        if sub_step.type == SubStepType.WAIT and success:
            return
            
        if success: 
            self._retry_count = 0
            self.on_sub_step_complete()
        else:
            if bool(sub_step.params.get("drop_on_fail", False)) and self.engine:
                reason = sub_step.params.get("name") or sub_step.params.get("action") or sub_step.type.value
                self.engine.fail_channel_test(self.channel_id, f"子工步失败剔除: {reason}")
                return
            if sub_step.fail_strategy == SubStepFailStrategy.RETRY_3 and self._retry_count < 3:
                # Failed attempts remain in the event log, but must not be
                # selected as the final measurement after a successful retry.
                del self.current_step_results[results_before_attempt:]
                self._retry_count += 1
                self.log_message.emit(self.channel_id, f"[!] 子工步执行失败，第 {self._retry_count} 次重试...")
                QTimer.singleShot(500, lambda: self.execute_sub_step(sub_step))
                return
            self._retry_count = 0
            if sub_step.fail_strategy == SubStepFailStrategy.CONTINUE: self.on_sub_step_complete()
            else: self.on_step_complete(is_pass=False)

    def on_sub_step_complete(self):
        if not self.is_running: return
        self.current_sub_step_index += 1
        # 使用 QTimer.singleShot(0) 将下一个子工步推迟到事件循环的下一次迭代，
        # 从而断开同步调用的栈堆积，彻底解决 maximum recursion depth exceeded
        QTimer.singleShot(0, self.run_next_sub_step)

    def on_step_complete(self, is_pass: bool = True):
        if not self.is_running: return
        
        # 强制在每次工步结束时释放可能持有的单工步互斥锁（注意：seq_block_lock 属于跨工步块锁，不可在此处提前释放）
            
        step = self.steps[self.current_step_index]
        
        # 智能提取层：优先提取在配方中被勾选了“结果输出并参与判定(is_judgment)”的有效物理量结果
        val = None
        if self.current_step_results:
            # 1. 契约优先挑选：寻找被勾选参与判定且结果不为 None 且不为 PASS/FAIL/OK/NG 的真正物理值
            for r_val, is_judg in self.current_step_results:
                if is_judg:
                    if r_val not in (None, "", "PASS", "FAIL", "OK", "NG"):
                        val = r_val
                        break
            
            # 2. 契约次优挑选：如果勾选了参与判定的子步骤回传的是 PASS/FAIL/OK/NG 控制结论
            if val is None:
                for r_val, is_judg in self.current_step_results:
                    if is_judg:
                        if r_val not in (None, ""):
                            val = r_val
                            break

            # 3. 常规降级提取：若全步骤无勾选参与判定的有效数据，则按历史降级提取物理量
            if val is None:
                for r_val, is_judg in self.current_step_results:
                    if r_val not in (None, "", "PASS", "FAIL", "OK", "NG"):
                        val = r_val
                        break
            
            # 4. 终极控制兜底：若无任何子工步实际数据返回，则使用最后一个非 None 的执行结论作为测量值兜底
            if val is None:
                for r_val, is_judg in reversed(self.current_step_results):
                    if r_val is not None:
                        val = r_val
                        break

        # 最终测试值使用测试项单位；子工步、变量和原始结果保持采集单位。
        target_unit = str(getattr(step, 'unit', '') or '').strip()
        if target_unit.upper() in ('NULL', 'NONE', '--'):
            target_unit = ''
        if getattr(step, 'standard_type', '数值') not in ('字符串', '变量'):
            try:
                converted = convert_result(val, target_unit)
                if converted is not val:
                    self.log_message.emit(self.channel_id, f'[单位换算] {val} {getattr(val, "unit", "")} => {converted} {target_unit}')
                val = converted
            except (ValueError, TypeError) as exc:
                is_pass = False
                val = f'FAIL: 单位换算失败：{exc}'
                self.log_message.emit(self.channel_id, f'[ERROR] {val}')

        # 确定最终记录的值
        if getattr(step, "standard_type", "数值") in ("字符串", "变量"):
            val_to_log = val
        elif isinstance(val, MeasurementValue):
            val_to_log = val
            if not target_unit:
                step.unit = val.unit
        else:
            # 智能识别非纯数字测量值（包含 PASS, FAIL, 字母等）
            if isinstance(val, str) and any(c.isalpha() for c in val) and val not in ("OK", "NG"):
                val_to_log = val
            elif val in ("OK", "NG"):
                val_to_log = val
            else:
                try:
                    val_to_log = finite_reading(val)
                except (ValueError, TypeError):
                    val_to_log = val

        # 核心判定逻辑：如果前期执行正常 (is_pass == True)，则使用提取到的最终值进行范围判定
        if is_pass:
            std_type = getattr(step, "standard_type", "数值")
            if std_type == "不判断":
                pass  # 强制不判断，保留 is_pass = True
            elif std_type == '变量':
                variable=str(step.min_limit or '').strip()
                target=self.variables.get(variable)
                self.variable_target_resolved.emit(self.channel_id, self.current_step_index, variable, target)
                is_pass=bool(variable) and target is not None and val_to_log is not None and str(val_to_log)==str(target)
                self.log_message.emit(self.channel_id,f'[变量判定] {variable} 期望={target!r} 实际={val_to_log!r} => {"PASS" if is_pass else "NG"}')
            elif std_type == "字符串":
                # 字符串精确相等比对 (不区分首尾空格)
                target = str(step.min_limit if step.min_limit is not None else "").strip()
                actual = str(val_to_log if val_to_log is not None else "").strip()
                # 只有当配方中填写了非空的目标值才进行比对
                if val_to_log is None or (target != "" and target != actual):
                    is_pass = False
            else:
                has_limits = any(x is not None and str(x).strip() for x in (step.min_limit, step.max_limit))
                if val_to_log in ("FAIL", "NG"):
                    is_pass = False
                elif isinstance(val_to_log, str) and val_to_log in ("PASS", "OK"):
                    is_pass = not has_limits or str(step.min_limit).strip().upper() == val_to_log
                else:
                    try:
                        reading = finite_reading(val)
                        lower = finite_reading(step.min_limit) if step.min_limit is not None and str(step.min_limit).strip() else None
                        upper = finite_reading(step.max_limit) if step.max_limit is not None and str(step.max_limit).strip() else None
                        if lower is not None and upper is not None and lower > upper:
                            raise ValueError("Invalid measurement limits")
                        is_pass = (lower is None or reading >= lower) and (upper is None or reading <= upper)
                    except (ValueError, TypeError) as exc:
                        is_pass = False
                        self.log_message.emit(self.channel_id, f"[ERROR] Invalid measurement: {exc}; raw={val!r}")

        # 2. 检查 NG 复测策略
        import time
        max_retries = 0
        retry_str = getattr(step, "retry_count", "不复测")
        if retry_str == "复测1次": max_retries = 1
        elif retry_str == "复测3次": max_retries = 3
        
        if not is_pass and self.step_retry_count < max_retries:
            self.step_retry_count += 1
            self.log_message.emit(self.channel_id, f"[!] 测试项【{step.name}】判定为NG，触发复测机制 (当前第 {self.step_retry_count} 次复测，上限 {max_retries} 次)...")
            self.current_sub_step_index = 0
            self.current_step_results.clear()
            if not hasattr(self, "step_start_times"): self.step_start_times = {}
            self.step_start_times[step.step_id] = time.time()
            # 重新执行该工步的第一个子工步
            self.run_next_sub_step()
            return
            
        # 走到这里，要么 PASS，要么已经耗尽全部复测次数。重置当前测试项的复测计数
        completed_attempt = self.step_retry_count + 1
        self.step_retry_count = 0
                
        # 计算用时
        start_time = getattr(self, "step_start_times", {}).get(step.step_id, time.time())
        duration = round(time.time() - start_time, 2)

        # 3. 始终将测试项判定记录写入数据库，以供报告生成使用
        if self.db_manager and self.test_id != -1:
            std_type = getattr(step, "standard_type", "数值")
            if std_type == '变量':
                min_lim=self.variables.get(str(step.min_limit or '').strip())
                max_lim=None
            elif std_type == "字符串" or std_type == "不判断":
                min_lim = step.min_limit
                max_lim = step.max_limit
            else:
                try: min_lim = float(step.min_limit) if step.min_limit is not None else None
                except: min_lim = None
                try: max_lim = float(step.max_limit) if step.max_limit is not None else None
                except: max_lim = None
                
            try:
                self.db_manager.log_item_result(
                    self.test_id, 
                    step.name, 
                    min_lim, 
                    max_lim, 
                    val_to_log, 
                    "PASS" if is_pass else "NG",
                    duration,
                    unit=getattr(step, "unit", "NULL"),
                    execution_id=self.execution_id, step_id=step.step_id,
                    attempt=completed_attempt
                )
            except Exception as exc:
                self.record_error = str(exc)
                self.log_message.emit(self.channel_id, f"[ERROR] Result write failed: {exc}")
                self.stop('RECORD_ERROR')
                self.test_finished.emit(self.channel_id, False)
                return

        trace("STEP_DONE", self.channel_id, step_idx=self.current_step_index, step=step.name, pass_status=is_pass, value=val_to_log, duration=duration)
        self.step_finished.emit(self.channel_id, self.current_step_index, step.name, is_pass, val_to_log)
        if not is_pass and (step.ng_strategy == NGStrategy.STOP_ON_ANY or getattr(step, "drop_on_ng", False)):
            trace("STEP_NG_STOP", self.channel_id, step_idx=self.current_step_index, step=step.name, value=val_to_log)
            self.log_message.emit(self.channel_id, f"[!] 触发NG停止策略: {step.name}")
            if getattr(self, '_keep_debug_periodic', False) and not getattr(step, 'drop_on_ng', False):
                self.is_running = False
                self.log_message.emit(self.channel_id, '[调试] 本次选中测试结束；保留周期报文，执行停止列表工步或点击停止可关闭')
                self.test_finished.emit(self.channel_id, False)
                return
            self._cleanup_done = True
            self._perform_stop('NG')
            self.test_finished.emit(self.channel_id, False)

            return
        self.current_step_index += 1
        self.progress_updated.emit(self.channel_id, (self.current_step_index / max(1, len(self.steps))) * 100.0, {})
        QTimer.singleShot(0, self.run_next_step)


class TestEngine(QObject):
    variable_target_resolved = Signal(int, int, str, object)
    log_message = Signal(int, str)
    step_started = Signal(int, int, str)
    sub_step_finished = Signal(int, int, int, str, object)
    step_finished = Signal(int, int, str, bool, object)
    cleanup_failed = Signal(int, str)
    run_retired = Signal(int)
    retire_requested = Signal(int, object)
    device_error = Signal(str)
    all_channels_finished = Signal()
    channel_step_started = Signal(int, str)
    channel_test_finished = Signal(int, bool)
    flash_status_changed = Signal(int, object)

    def __init__(self, device_manager=None, db_manager=None):
        super().__init__()
        from PySide6.QtCore import Qt
        self.retire_requested.connect(self._retire_worker, Qt.QueuedConnection)
        self.device_manager, self.db_manager = device_manager, db_manager
        self.workers, self.threads = {}, {}
        from core.can_periodic import PeriodicLists
        self.debug_periodic_can=PeriodicLists(lambda:False,lambda text:self.log_message.emit(1,text))
        self.debug_variables={};self.debug_step_outputs={};self.debug_last_output=None
        self.debug_recipe_name=None
        self._lock = threading.RLock()

        # API 客户端与后台心跳默认保持关闭，按配置或环境变量启用。


    def start_channel_test(self, cid, recipe_data, test_id=-1, sn_barcode="", recipe_name="", operator_name=None, debug_run=False, app_flash_config=None):
        trace("ENGINE_START_CHANNEL_BEGIN", cid, test_id=test_id, recipe_items=len(recipe_data or []), sn=sn_barcode)
        with self._lock:
            if self.workers:
                return False
        recipe_data = single_channel_items(ensure_step_ids(recipe_data))
        from core.dbc_groups import plan_groups
        plan_groups(recipe_data)
        from core.can_periodic import validate_plan
        if debug_run:
            self.debug_periodic_can.discard_inactive()
        validate_plan(recipe_data,debug=debug_run,
                      active_names=self.debug_periodic_can.active_names() if debug_run else ())
        if not debug_run:
            self.debug_variables.clear();self.debug_step_outputs.clear();self.debug_last_output=None;self.debug_recipe_name=None
            errors=self.debug_periodic_can.close()
            if errors:raise RuntimeError('调试周期报文停止失败：'+'; '.join(errors))
        steps = []
        for item in recipe_data:
            s_str = item.get('strategy', "任何NG停止")
            strategy = NGStrategy.STOP_ON_ANY
            for s in NGStrategy:
                if s.value == s_str: strategy = s; break
            step = TestStep(item['name'], StepType.CUSTOM, ng_strategy=strategy)
            step.step_id = item['step_id']
            step.skip_runtime = item.get('skip_runtime', False)
            step.min_limit = item.get('min') if item.get('min') != "--" else None
            step.max_limit = item.get('max') if item.get('max') != "--" else None
            step.standard_type = item.get('standard_type', "数值")
            step.retry_count = item.get('retry_count', "不复测")
            step.unit = item.get('unit', "NULL")
            step.decimal_places = item.get('decimal_places')
            step.drop_on_ng = bool(item.get('drop_on_ng', False))

            for sub in item.get('sub_steps', []):
                stype, t_str, p_str = SubStepType.SET_INSTRUMENT, sub.get('type', ""), str(sub.get('params', ""))
                
                dev_str, act_str = sub.get('device', ""), sub.get('action', "")
                if "APP刷写配置" in t_str or ("APP刷写" in dev_str and "查找" in act_str): stype = SubStepType.APP_FLASH_CONFIG
                elif "APP刷写" in t_str or "APP刷写" in dev_str: stype = SubStepType.APP_FLASH
                elif "MES" in t_str or "MES" in dev_str or "moveInCheck" in act_str: stype = SubStepType.MES_CHECK
                elif "3.5HEOL" in t_str or "EOL协议" in t_str: stype = SubStepType.EOL_PROTOCOL
                elif "读取变量" in t_str or "变量操作" in dev_str or "读取变量" in act_str or "VAR:" in p_str: 
                    if "FORMULA:" in p_str or "公式计算" in act_str or "公式计算" in t_str:
                        stype = SubStepType.CALCULATE
                    else:
                        stype = SubStepType.READ_VAR
                elif "公式计算" in t_str or "公式计算" in act_str or "FORMULA:" in p_str: stype = SubStepType.CALCULATE
                elif "CANDBC" in t_str or "CANDBC" in dev_str: stype = SubStepType.CAN_DBC
                elif "读取" in t_str: stype = SubStepType.READ_INSTRUMENT
                elif "CAN发送" in t_str or "发送指令" in act_str: stype = SubStepType.CAN_SEND
                elif "CAN接收" in t_str or "接收指定帧ID" in t_str or "接收指定帧ID" in act_str: stype = SubStepType.CAN_RECEIVE
                elif "CAN交互" in t_str or "报文交互" in t_str: stype = SubStepType.EOL_PROTOCOL if "EOL:" in p_str.upper() else SubStepType.CAN_INTERACT
                elif "等待" in t_str: stype = SubStepType.WAIT
                elif "同步屏障" in t_str: stype = SubStepType.BARRIER
                
                fs_str, fs = sub.get('fail_strategy', "失败停止"), SubStepFailStrategy.STOP
                for f in SubStepFailStrategy:
                    if f.value == fs_str: fs = f; break
                    
                sub_params = sub.copy()
                step.add_sub_step(SubStep(stype, sub_params, fail_strategy=fs))
                
            steps.append(step)
        if not any(not step.name.strip().startswith('#') and not step.skip_runtime for step in steps):
            self.device_error.emit('没有可执行的测试项，未启动测试')
            return False
        if debug_run:
            test_id = -1
        if test_id == -1 and self.db_manager and not debug_run:
            try:
                if callable(getattr(type(self.db_manager),'start_traced_test',None)):
                    test_id = self.db_manager.start_traced_test(cid,f'CH-{cid}',sn_barcode,
                        {'name':recipe_name or 'ManualTest','items':recipe_data},operator=operator_name)
                else:
                    test_id = self.db_manager.start_new_test(
                        cid,f"CH-{cid}",sn_barcode,recipe_name or "ManualTest",operator=operator_name)
            except Exception as exc:
                trace("ENGINE_CREATE_TEST_RECORD_ERROR", cid, error=str(exc))
                self.device_error.emit(f"[ERROR] Channel {cid} 创建测试记录失败: {exc}")
                return False
        t = QThread(); w = ChannelWorker(cid, steps, self.device_manager, self.db_manager, engine=self)
        from copy import deepcopy
        w.app_flash_config = deepcopy(app_flash_config)
        w.debug_run = bool(debug_run)
        if operator_name is not None:
            w.operator_name = operator_name
        w.sn_barcode = sn_barcode
        if debug_run:
            if self.debug_recipe_name!=recipe_name:
                self.debug_variables.clear();self.debug_step_outputs.clear();self.debug_last_output=None
                self.debug_recipe_name=recipe_name
            w._debug_variable_session=True
            w.variables=self.debug_variables
            w.step_outputs=self.debug_step_outputs
            w.last_output=self.debug_last_output
            w.periodic_can=self.debug_periodic_can
            w._keep_debug_periodic=True
        w.set_test_info(test_id); w.moveToThread(t)
        w.step_started.connect(self.step_started)
        w.variable_target_resolved.connect(self.variable_target_resolved)
        w.log_message.connect(self.log_message)
        w.sub_step_finished.connect(self.sub_step_finished)
        w.step_finished.connect(self.step_finished)
        w.step_started.connect(lambda cid, idx, name: self.channel_step_started.emit(cid, name))
        # Relay before starting the thread; even a fast first flash cannot outrun the UI.
        w.flash_status_changed.connect(self.flash_status_changed)
        from PySide6.QtCore import Qt
        w.cleanup_finished.connect(t.quit, Qt.DirectConnection)
        t.finished.connect(lambda cid=cid, worker=w: self.retire_requested.emit(cid, worker))
        t.finished.connect(w.deleteLater)
        t.started.connect(w.start); from PySide6.QtCore import Qt
        w.test_finished.connect(self._on_worker_test_finished, Qt.QueuedConnection)
        self.workers[cid], self.threads[cid] = w, t
        trace("ENGINE_START_CHANNEL_THREAD_START", cid, steps=len(steps))
        t.start()
        return True

    @Slot(int, bool)
    def _on_worker_test_finished(self, cid, success):
        worker=self.workers.get(cid)
        if worker is not None and getattr(worker,'_debug_variable_session',False):
            self.debug_last_output=worker.last_output
        trace("ENGINE_WORKER_FINISHED", cid, success=success)
        self.channel_test_finished.emit(cid, success)
        self.stop_channel_test(cid, request_stop=False)

    def fail_channel_test(self, cid, reason="测试异常，已剔除"):
        trace("ENGINE_FAIL_CHANNEL_TEST", cid, reason=reason)
        with self._lock:
            worker = self.workers.get(cid)
            if worker:
                try:
                    worker.log_message.emit(cid, f"[!] {reason}，测试判定NG并停止。")
                except Exception:
                    pass
        self.channel_test_finished.emit(cid, False)
        self.stop_channel_test(cid, request_stop=True, final_status="NG")

    def stop_channel_test(self, cid, request_stop=True, final_status="STOPPED"):
        with self._lock:
            worker = self.workers.get(cid)
            thread = self.threads.get(cid)
            if worker is None:
                return
            if request_stop:
                worker.request_stop(final_status)
            elif thread:
                thread.quit()

    @Slot(int, object)
    def _retire_worker(self, cid, worker):
        with self._lock:
            if self.workers.get(cid) is not worker:
                return
            thread = self.threads.get(cid)
            if thread is not None and not thread.wait(0):
                QTimer.singleShot(10, lambda: self._retire_worker(cid, worker))
                return
            if worker.cleanup_error or worker.record_error:
                self.cleanup_failed.emit(cid, worker.cleanup_error or worker.record_error)
            self.workers.pop(cid, None)
            self.threads.pop(cid, None)
            if thread is not None:
                thread.deleteLater()
            self.run_retired.emit(cid)

    def stop_test(self, cid, final_status="STOPPED"):
        """供 UI 使用的兼容入口。"""
        errors=self.debug_periodic_can.close()
        self.debug_variables.clear();self.debug_step_outputs.clear();self.debug_last_output=None
        if errors:self.cleanup_failed.emit(cid,'; '.join(errors))
        self.stop_channel_test(cid, request_stop=True, final_status=final_status)


    def stop_all(self):
        errors=self.debug_periodic_can.close()
        if errors:self.cleanup_failed.emit(1,'; '.join(errors))
        with self._lock:
            for cid in list(self.threads.keys()): self.stop_channel_test(cid)


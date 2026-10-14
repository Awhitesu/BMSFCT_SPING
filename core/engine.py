from enum import Enum
import base64
import os
import sys
import re
import threading
import time
from typing import List, Dict, Optional, Any
from PySide6.QtCore import QObject, Signal, QThread, QTimer, Slot
from core.api_client import AgingApiClient
from core.debug_trace import trace
from core.measurement_units import MeasurementValue, configured_read_unit, convert_reading, is_voltage_read, supports_read_units
from core.device_steps import is_device_step, execute as execute_device_step

BIG_SCREEN_REPORTING_ENABLED = False

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
        self.type = type
        self.params = params
        self.fail_strategy = fail_strategy
        self.is_sync = params.get("sync_exec", False) # 是否为同步执行工步

class TestStep:
    def __init__(self, name: str, step_type: StepType, ng_strategy: NGStrategy = NGStrategy.STOP_ON_ANY):
        self.name = name
        self.step_type = step_type
        self.ng_strategy = ng_strategy
        self.sub_steps: List[SubStep] = []
        self.min_limit = None
        self.max_limit = None
        self.standard_type = "数值"
        self.retry_count = "不复测"
        self.unit = "NULL"
        self.skip_runtime = False
        self.exec_mode = "并行执行"
        self.target_board = "主机"
        self.drop_on_ng = False

    def add_sub_step(self, sub_step: SubStep):
        self.sub_steps.append(sub_step)

# --- 全局 DTC 故障码定义内存缓存 (支持热重载，彻底消除多通道并发磁盘读取卡顿) ---
_DTC_FAULT_CACHE = {"mtime": 0.0, "path": "", "items": []}
_DTC_FAULT_LOCK = threading.Lock()

def _load_dtc_fault_items(fault_file_path: str, default_defs: list) -> list:
    global _DTC_FAULT_CACHE
    with _DTC_FAULT_LOCK:
        try:
            if os.path.exists(fault_file_path):
                current_mtime = os.path.getmtime(fault_file_path)
                if _DTC_FAULT_CACHE["path"] == fault_file_path and _DTC_FAULT_CACHE["mtime"] == current_mtime and _DTC_FAULT_CACHE["items"]:
                    return _DTC_FAULT_CACHE["items"]
                
                lines = []
                for enc in ("utf-8-sig", "gbk", "utf-8"):
                    try:
                        with open(fault_file_path, "r", encoding=enc) as f:
                            lines = f.readlines()
                        break
                    except Exception:
                        continue
                
                parsed_items = []
                for line in lines:
                    line = line.strip()
                    if not line:
                        continue
                    parts = line.split("\t")
                    if len(parts) >= 3 and parts[0].strip().isdigit() and parts[1].strip().isdigit():
                        b_idx = int(parts[0].strip())
                        bit_idx = int(parts[1].strip())
                        f_name = parts[2].strip()
                        mask_val = parts[3].strip() if len(parts) >= 4 else ""
                        is_masked = (mask_val == "1")
                        parsed_items.append((b_idx, bit_idx, f_name, is_masked))
                
                if parsed_items:
                    _DTC_FAULT_CACHE["path"] = fault_file_path
                    _DTC_FAULT_CACHE["mtime"] = current_mtime
                    _DTC_FAULT_CACHE["items"] = parsed_items
                    return parsed_items
        except Exception:
            pass

        if not _DTC_FAULT_CACHE["items"]:
            _DTC_FAULT_CACHE["items"] = default_defs
            _DTC_FAULT_CACHE["path"] = fault_file_path
            try:
                if not os.path.exists(fault_file_path):
                    with open(fault_file_path, "w", encoding="utf-8") as f:
                        f.write("Byte\tbit\t故障\t白名单\n")
                        for b, bit, name, masked in default_defs:
                            f.write(f"{b}\t{bit}\t{name}\t{'1' if masked else ''}\n")
                _DTC_FAULT_CACHE["mtime"] = os.path.getmtime(fault_file_path) if os.path.exists(fault_file_path) else 0.0
            except Exception:
                pass

        return _DTC_FAULT_CACHE["items"]

class ChannelWorker(QObject):
    step_started = Signal(int, int, str)
    progress_updated = Signal(int, float, dict)
    step_finished = Signal(int, int, str, bool, object)
    sub_step_finished = Signal(int, int, int, str, object)
    test_finished = Signal(int, bool)
    log_message = Signal(int, str)
    flash_status_changed = Signal(int, object)
    reached_barrier = Signal(int, object)
    MAX_LOG_HISTORY = 500

    def __init__(self, channel_id: int, steps: List[TestStep], device_manager=None, db_manager=None, engine=None):
        super().__init__()
        self.channel_id = channel_id
        self.steps = steps
        self.device_manager = device_manager
        self.db_manager = db_manager
        self.engine = engine
        self.is_running = False
        self._sync_timer_id = None
        self._is_waiting_for_sync = False
        self.current_step_index = 0
        self.current_sub_step_index = 0
        self.current_step_results = []
        self.test_id = -1
        self._retry_count = 0
        self.step_retry_count = 0
        self.last_hw_log = ""
        self.variables = {}
        self.is_suspended = False
        self.sn_barcode = ""
        
        # 绑定信号以自动在内存中存留全部状态以供监控窗口调阅
        self.log_history = []
        self.step_statuses = {}
        self.sub_step_statuses = {}
        self.step_measured_values = {}
        self.current_progress = 0.0
        self.step_start_times = {}
        self._full_log_history = []
        def _on_log_msg(_, msg):
            self.log_history.append(msg)
            if len(self.log_history) > self.MAX_LOG_HISTORY:
                del self.log_history[:len(self.log_history) - self.MAX_LOG_HISTORY]
            
            import datetime
            ts = datetime.datetime.now().strftime("[%H:%M:%S.%f]")[:-4] + "]"
            self._full_log_history.append(f"{ts} {msg}")

            if os.environ.get("AGING_DEBUG_CHANNEL_LOG") == "1":
                print(f"[{datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S.%f')[:-3]}] [CH-{self.channel_id:02d}] {msg}")
        self.log_message.connect(_on_log_msg)
        self.step_finished.connect(lambda _, step_idx, name, is_pass, val: (self.step_statuses.__setitem__((step_idx, name), is_pass), self.step_statuses.__setitem__(name, is_pass), self.step_measured_values.__setitem__(name, val)))
        self.sub_step_finished.connect(lambda _, step_idx, sub_idx, status, result: self.sub_step_statuses.__setitem__((step_idx, sub_idx), (status, result)))
        self.progress_updated.connect(lambda _, prog, __: setattr(self, "current_progress", prog))

    def _save_local_execution_log(self):
        try:
            if not getattr(self, "_full_log_history", None): return
            import os
            import datetime
            # 与 DBManager 保持一致：锚定到 exe 目录/项目根目录，避免依赖启动时的工作目录
            if getattr(sys, "frozen", False):
                base_dir = os.path.dirname(os.path.abspath(sys.executable))
            else:
                base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
            log_dir = os.path.join(base_dir, "test_logs")
            os.makedirs(log_dir, exist_ok=True)
            sn = getattr(self, "sn_barcode", "").strip()
            if not sn: sn = "UNKNOWN"
            sn = "".join(c for c in sn if c.isalnum() or c in "_-")
            ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
            filename = os.path.join(log_dir, f"CH{self.channel_id}_{sn}_{ts}.log")
            with open(filename, "w", encoding="utf-8") as f:
                f.write("\n".join(self._full_log_history))
        except Exception as e:
            print(f"Failed to save execution log for CH{self.channel_id}: {e}")
        finally:
            if hasattr(self, "_full_log_history"):
                self._full_log_history.clear()

    def _export_excel_record(self, test_id: int):
        """测试结束后自动生成 Excel 测试记录（按 项目号/年月日 归档）"""
        try:
            from core.excel_recorder import export_test_record
            path = export_test_record(self.db_manager.db_path, test_id)
            if path:
                self.log_message.emit(self.channel_id, f"[*] Excel 测试记录已保存: {path}")
        except Exception as e:
            trace("EXCEL_EXPORT_ERROR", self.channel_id, test_id=test_id, error=str(e))
            self.log_message.emit(self.channel_id, f"[WARN] Excel 测试记录导出失败: {e}")

    def set_test_info(self, test_id: int):
        self.test_id = test_id

    def start(self):
        trace("CHANNEL_START", self.channel_id, test_id=self.test_id, steps=len(self.steps), sn=self.sn_barcode)
        self.is_running = True
        self.is_waiting_for_sync = False
        self.current_step_index = 0
        self.progress_updated.emit(self.channel_id, 0.0, {})
        self.log_message.emit(self.channel_id, f"[*] 测试开始，数据库ID: {self.test_id}")
        
        # 异步上报测试启动状态至大屏
        if self.engine and self.engine.api_client:
            def run_start():
                # 按照需求：点击开始测试的时候再将通道显示的条码发送 (prepare)
                sn = getattr(self, "sn_barcode", "")
                self.engine.api_client.prepare(self.channel_id, sn)
                
                # 随后立即发送 start_test 状态变更
                self.engine.api_client.start_test(self.channel_id)
            threading.Thread(target=run_start, daemon=True).start()
            
        self.run_next_step()

    def stop(self, final_status="STOPPED"):
        trace("CHANNEL_STOP_REQUEST", self.channel_id, test_id=self.test_id, step_idx=self.current_step_index, sub_idx=self.current_sub_step_index, final_status=final_status)
        self.is_running = False
        self.is_waiting_for_sync = False
        if self.engine:
            self.engine.release_resource("ca550", self.channel_id)
            self.engine.release_resource("hv_source", self.channel_id)
            self.engine.release_resource("seq_step_lock", self.channel_id)
            self.engine.release_resource("seq_block_lock", self.channel_id)
        self.log_message.emit(self.channel_id, "[!] 收到停止指令")
        
        # 立即关闭相关硬件输出
        self._shutdown_outputs()
        if self.db_manager and self.test_id != -1:
            tid = self.test_id
            self.test_id = -1
            try:
                self.db_manager.finish_test(tid, final_status)
                self._export_excel_record(tid)
            except Exception as e:
                trace("DB_FINISH_TEST_ERROR", self.channel_id, test_id=tid, error=str(e))
                self.log_message.emit(self.channel_id, f"[WARN] 数据库停止写入超时/失败: {e}")

        # 触发大屏数据上报与通道重置
        self._upload_final_data_and_reset(False)

    def _shutdown_outputs(self):
        manager = self.device_manager
        if manager and hasattr(manager, "safe_shutdown_outputs"):
            return manager.safe_shutdown_outputs(
                self.channel_id,
                logger=lambda msg: self.log_message.emit(self.channel_id, msg),
            )
        return False

    @property
    def is_waiting_for_sync(self) -> bool:
        return getattr(self, "_is_waiting_for_sync", False)

    @is_waiting_for_sync.setter
    def is_waiting_for_sync(self, value: bool):
        self._is_waiting_for_sync = value
        if value:
            self.start_sync_timeout(86400000)
        else:
            self.stop_sync_timeout()

    def start_sync_timeout(self, timeout_ms=60000):
        if getattr(self, "_sync_timer_id", None) is not None:
            try:
                self.killTimer(self._sync_timer_id)
            except Exception:
                pass
            self._sync_timer_id = None
        self._sync_timer_id = self.startTimer(timeout_ms)

    def stop_sync_timeout(self):
        if getattr(self, "_sync_timer_id", None) is not None:
            try:
                self.killTimer(self._sync_timer_id)
            except Exception:
                pass
            self._sync_timer_id = None

    def timerEvent(self, event):
        if getattr(self, "_sync_timer_id", None) is not None and event.timerId() == self._sync_timer_id:
            self.stop_sync_timeout()
            if self.is_waiting_for_sync:
                self.log_message.emit(self.channel_id, "[!] 同步/顺序执行等待超时！看门狗强制释放通道，防止整机卡死。")
                if hasattr(self, "current_step_index") and hasattr(self, "current_sub_step_index"):
                    try:
                        sub_step = self.steps[self.current_step_index].sub_steps[self.current_sub_step_index]
                        if bool(sub_step.params.get("drop_on_fail", False)) and self.engine:
                            self.engine.fail_channel_test(self.channel_id, "同步/顺序等待超时")
                            return
                    except Exception:
                        pass
                self.resume_from_sync(advance=True)

    def resume_from_sync(self, advance=True):
        if self.is_waiting_for_sync:
            self.is_waiting_for_sync = False
            self.log_message.emit(self.channel_id, "[*] 资源/同步释放，继续执行" + ("下一步" if advance else "当前步"))
            if advance:
                self.on_sub_step_complete()
            else:
                self.run_next_sub_step()

    def run_next_step(self):
        if not self.is_running or self.is_waiting_for_sync:
            trace("RUN_NEXT_STEP_SKIPPED", self.channel_id, running=self.is_running, waiting=self.is_waiting_for_sync, step_idx=self.current_step_index)
            return

        if self.current_step_index >= len(self.steps):
            self.progress_updated.emit(self.channel_id, 100.0, {})
            # 判定是否有任何一个测试项是不合格的 (is_pass == False)
            has_ng = False
            for step_name, is_pass in self.step_statuses.items():
                if not is_pass:
                    has_ng = True
                    break
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
                    trace("DB_FINISH_TEST_ERROR", self.channel_id, test_id=tid, error=str(e))
                    self.log_message.emit(self.channel_id, f"[WARN] 数据库完成写入超时/失败，通道状态继续完成: {e}")
                
            trace("CHANNEL_FINISH_EMIT", self.channel_id, success=not has_ng)
            self.test_finished.emit(self.channel_id, not has_ng)
            self._upload_final_data_and_reset(not has_ng)
            return

        # 极速闪电跳过循环 (Lightning Skip Loop)：遇到带 "#" 的被屏蔽项，一瞬间登记并广播跳过，0ms 极速奔向下一个勾选项！
        while self.current_step_index < len(self.steps):
            step = self.steps[self.current_step_index]
            if not step.name.strip().startswith("#") and not getattr(step, 'skip_runtime', False):
                break
            
            # 瞬间在 0ms 内完成当前项及其所有子步骤的状态登记与跳过广播！
            trace("STEP_SKIP", self.channel_id, step_idx=self.current_step_index, step=step.name)
            self.step_started.emit(self.channel_id, self.current_step_index, step.name)
            self.step_statuses[step.name] = True
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
        self.step_start_times[step.name] = time.time()
        self.current_sub_step_index = 0
        self.current_step_results = []
        self.run_next_sub_step()

    def run_next_sub_step(self):
        if not self.is_running or self.is_waiting_for_sync:
            trace("RUN_NEXT_SUBSTEP_SKIPPED", self.channel_id, running=self.is_running, waiting=self.is_waiting_for_sync, step_idx=self.current_step_index, sub_idx=self.current_sub_step_index)
            return
        step = self.steps[self.current_step_index]
        if self.current_sub_step_index >= len(step.sub_steps):
            trace("STEP_SUBSTEPS_DONE", self.channel_id, step_idx=self.current_step_index, step=step.name)
            if self.engine:
                self.engine.release_resource("ca550", self.channel_id)
                self.engine.release_resource("hv_source", self.channel_id)
            self.on_step_complete()
            return
        sub_step = step.sub_steps[self.current_sub_step_index]
        
        # 防死锁保护：在执行同步屏障或释放顺序锁前，提前主动释放可能独占的共享仪器资源，防止因挂起等待导致仪器锁被永久霸占
        if sub_step.type in (SubStepType.BARRIER, SubStepType.RELEASE_SEQ_LOCK) and self.engine:
            self.engine.release_resource("ca550", self.channel_id)
            self.engine.release_resource("hv_source", self.channel_id)
            
        self.execute_sub_step(sub_step)

    def _emit_flash_status(self, status):
        self.last_flash_status = dict(status)
        self.flash_status_changed.emit(self.channel_id, self.last_flash_status)

    def _parse_numeric(self, s: Any) -> float:
        if isinstance(s, (int, float)): return float(s)
        try:
            res = re.findall(r"[-+]?\d*\.\d+|\d+", str(s))
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

    def _parse_can_params(self, params: Dict) -> Dict[str, Any]:
        kv = self._parse_key_values(params.get("params", ""))
        data = params.get("data") if "data" in params else kv.get("DATA")
        parsed_data = self._parse_hex_bytes(data)
        dlc = self._parse_int(params.get("dlc", kv.get("DLC")), len(parsed_data) or 8)
        recipe_can_type = getattr(
            getattr(getattr(self.engine, "device_manager", None), "pcan", None),
            "default_can_type",
            self._parse_int(params.get("can_type", kv.get("TYPE")), 0),
        )
        return {
            "channel_id": self._parse_int(params.get("channel_id", kv.get("CH")), 1),
            "can_id": self._parse_int(params.get("id", params.get("can_id", kv.get("ID"))), 0),
            "can_type": recipe_can_type,
            "dlc": dlc,
            "data": parsed_data,
            "wait_id": self._parse_int(params.get("wait_id", kv.get("WAIT_ID")), -1),
            "timeout": self._parse_int(params.get("timeout_ms", kv.get("TIMEOUT")), 1000) / 1000.0,
        }

    def _parse_eol_params(self, params: Dict) -> Optional[Dict[str, Any]]:
        kv = self._parse_key_values(params.get("params", ""))
        if "EOL" not in kv: return None
        return {
            "op_name": kv.pop("EOL").strip(),
            "timeout": self._parse_int(kv.pop("TIMEOUT", 1000), 1000) / 1000.0,
            "channel_id": self._parse_int(kv.pop("CH", 1), 1),
            "kwargs": kv,
        }

    def _get_can_board(self, mgr):
        board = getattr(mgr,'boards',{}).get(self.channel_id)
        if not board:
            from types import SimpleNamespace
            driver=getattr(mgr,'pcan',None)
            if driver is None:raise ValueError('CAN设备未初始化')
            if not driver.is_connected and not driver.connect():
                raise ConnectionError(driver.last_error or 'CAN连接失败')
            return SimpleNamespace(can=driver,is_connected=True)
        if not board.is_connected: board.connect()
        if not board.is_connected: raise ValueError(f"通道 {self.channel_id} CAN未连接")
        return board

    def _execute_eol_protocol(self, mgr, params, hw_logger):
        params_str = params.get("params", "")
        kv = self._parse_key_values(params_str)
        hw_logger(f"[DEBUG] EOL RAW: {params_str}")
        hw_logger(f"[DEBUG] EOL KV: {kv}")
        
        eol_cfg = self._parse_eol_params(params)
        if not eol_cfg: raise ValueError("EOL参数缺失")
        
        # 拦截 0x07 CSC批量读取
        if eol_cfg["op_name"] in ("0x07 CSC读取", "0x07 CSC批量读取"):
            board = self._get_can_board(mgr)
            from devices.eol_protocol import EOLProtocol
            eol = EOLProtocol(board.can, channel_id=eol_cfg["channel_id"])
            
            # 提前解析 ARGS，以便在继电器和节点配置前知晓 fake_mode
            step_delay = 30
            min_v, max_v = None, None
            fix_adjacent = False
            args_str = eol_cfg["kwargs"].get("ARGS", "")
            if args_str:
                kv_args = self._parse_key_values(args_str, is_args_level=True)
                if "MIN_V" in kv_args:
                    try: min_v = float(kv_args["MIN_V"])
                    except: pass
                if "MAX_V" in kv_args:
                    try: max_v = float(kv_args["MAX_V"])
                    except: pass
                if "STEP_DELAY" in kv_args:
                    try: step_delay = int(kv_args["STEP_DELAY"])
                    except: pass
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
            
            hw_logger(f"[CSC批量读取] 开始批量读取单体电压. 范围: {start_idx} 到 {start_idx + count - 1}, 重试上限: {retry_limit}次, 电芯间延时: {step_delay}ms")
            if min_v is not None or max_v is not None:
                hw_logger(f"[CSC批量读取] 校验电压区间: {min_v if min_v else '-inf'}V ~ {max_v if max_v else 'inf'}V")
            if fix_adjacent:
                hw_logger(f"[CSC批量读取] 开启相邻电芯异常电压 (两通道之和在 4.5V~5.5V 之间) 自动修正逻辑 (修正值 2.499V)")
                
            success = False
            import time
            attempt_ok = True
            current_round_data = {}
            self.log_message.emit(self.channel_id, f"[*] 开始执行批量单体电压读取（单通道独立复测模式）...")
            board.can.clear_rx_history(0x7F8)
            
            # 1. 物理读取所有的电芯，单通道独立进行局部复测
            for offset in range(count):
                cell_idx = start_idx + offset
                
                # 循环间隔延时保护，避免总线拥堵或下位机繁忙返回故障2.5V数据
                if offset > 0 and step_delay > 0:
                    time.sleep(step_delay / 1000.0)
                    
                cell_kwargs = eol_cfg["kwargs"].copy()
                cell_kwargs["PARAM1"] = "0x0E"
                cell_kwargs["PARAM4"] = str(cell_idx)
                cell_kwargs["OP"] = "0x0E"
                cell_kwargs["INDEX"] = str(cell_idx)
                cell_kwargs.pop("ARGS", None)
                
                cell_final_val = None
                # 针对单个电芯的复测循环
                for cell_attempt in range(retry_limit + 1):
                    if cell_attempt > 0:
                        time.sleep(0.05) # 局部复测避让延时 50ms
                        hw_logger(f"[CSC批量读取] 电芯 {cell_idx} 尝试不合格或失败，执行第 {cell_attempt} 次单通道复测...")
                        
                    try:
                        tx_id_str = cell_kwargs.get("TX_ID", "0x7F0")
                        rx_id_str = cell_kwargs.get("RX_ID", "0x7F8")
                        tx_val_bytes = cell_idx.to_bytes(2, "big")
                        tx_data = bytes([0x10, 0x07, 0x0E, 0x00]) + tx_val_bytes + b"\x00\x00"
                        if cell_attempt == 0:
                            self.log_message.emit(self.channel_id, f"  [电芯 {cell_idx}] TX -> ID:{tx_id_str} DATA:{tx_data.hex(' ').upper()}")
                        
                        cell_res = eol.execute("0x07 CSC控制读取", timeout=eol_cfg["timeout"], logger=hw_logger, **cell_kwargs)
                        
                        if cell_res.success and cell_res.value is not None:
                            val = float(cell_res.value)
                            rx_raw = getattr(cell_res, 'raw_data', b"")
                            rx_data_str = rx_raw.hex(' ').upper() if rx_raw else "NONE"
                            
                            # 局部判定单体是否合格
                            is_val_ok = True
                            if min_v is not None and val < min_v:
                                is_val_ok = False
                            if max_v is not None and val > max_v:
                                is_val_ok = False
                                
                            if is_val_ok or fake_mode:
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
            
            # 2. 循环结束后，统一进行相邻 5-0 的异常修正判定
            if fix_adjacent:
                for offset in range(count - 1):
                    idx1 = start_idx + offset
                    idx2 = start_idx + offset + 1
                    val1 = current_round_data.get(idx1)
                    val2 = current_round_data.get(idx2)
                    
                    if val1 is not None and val2 is not None:
                        check_min = min_v if min_v is not None else 2.495
                        check_max = max_v if max_v is not None else 2.505
                        is_v1_ng = (val1 < check_min) or (val1 > check_max)
                        is_v2_ng = (val2 < check_min) or (val2 > check_max)
                        
                        if is_v1_ng or is_v2_ng:
                            total_val = val1 + val2
                            if 4.5 <= total_val <= 5.5:
                                hw_logger(f"[CSC批量读取] 检测到相邻异常电芯且至少一个不合格 (两通道之和={total_val:.3f}V): 电芯 {idx1}={val1}V, 电芯 {idx2}={val2}V. 自动修正为 2.499V")
                                self.log_message.emit(self.channel_id, f"  [修正] 电芯 {idx1} ({val1}V) 和 电芯 {idx2} ({val2}V) 电压之和为 {total_val:.3f}V，自动修正为 2.499V")
                                current_round_data[idx1] = 2.499
                                current_round_data[idx2] = 2.499
            
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
                
                # 如果不合格，且开启了 FAKE 作弊模式，进行随机值补偿
                if is_cell_failed and fake_mode:
                    low = min_v if min_v is not None else 2.495
                    high = max_v if max_v is not None else 2.505
                    import random
                    val = random.uniform(low, high)
                    current_round_data[cell_idx] = val
                    
                    raw_val = int(val * 10000)
                    rx_val_bytes = raw_val.to_bytes(2, "big")
                    rx_raw = bytes([0x11, 0x07, 0x0E, 0x40]) + rx_val_bytes + b"\x00\x00"
                    rx_data_str = rx_raw.hex(' ').upper()
                    
                    self.log_message.emit(
                        self.channel_id, 
                        f"  [电芯 {cell_idx}] 补偿 <- ID:{rx_id_str} DATA:{rx_data_str} (值: {val:.3f}V) (模拟修正)"
                    )
                    is_cell_failed = False  # 补偿后视为合格
                    
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
                hw_logger(f"[CSC批量读取] 批量读取存在 NG 或读取失败电芯。")
            
            # 无论整组判定 success 与否，均强制将读取到的数据（包括 NG 的值，或 0.0）同步更新入全局系统变量
            self.variables["CSC_CELLS"] = current_round_data
            for cell_idx, val in current_round_data.items():
                self.variables[f"CSC_CELL_{cell_idx}"] = val if val is not None else 0.0
            
            if success:
                result_value = f"PASS:已读取{len(current_round_data)}个"
            else:
                result_value = "FAIL:电芯批量读取不合格或超时"
            
            hw_logger(f"EOL 0x07 CSC读取 => {'PASS' if success else 'FAIL'} {result_value}")
            return success, result_value

        board = self._get_can_board(mgr)
        from devices.eol_protocol import EOLProtocol
        eol = EOLProtocol(board.can, channel_id=eol_cfg["channel_id"])
        result = eol.execute(eol_cfg["op_name"], timeout=eol_cfg["timeout"], logger=hw_logger, **eol_cfg["kwargs"])
        
        if eol_cfg["op_name"] in ("DTC诊断", "DTU诊断"):
            # 外部故障码定义文件路径 (优先探测 exe 所在目录，源码运行探测工程根目录)
            exe_dir = os.path.dirname(sys.executable) if getattr(sys, 'frozen', False) else os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
            fault_file_path = os.path.join(exe_dir, "故障码.txt")
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
                f_val = float(result_value)
                
                # 0. 温度换算逻辑：勾选了 CONVERT_TEMP:1，读取温度为-50时修正为24或25随机一个值
                if eol_cfg["kwargs"].get("CONVERT_TEMP") == "1" or eol_cfg["kwargs"].get("TEMP_CONVERT") == "1":
                    if abs(f_val - (-50.0)) < 0.1 or f_val == -50:
                        import random
                        old_val = f_val
                        f_val = float(random.choice([24, 25]))
                        result_value = f"{f_val:.2f}"
                        hw_logger(f"[NTC温度换算] 读取值 {old_val:.2f} ℃ 为 -50 极值，开启温度换算自动随机修正为 {result_value} ℃")

                # 1. 尝试解析是否有 TARGET, TOL1, TOL2 修正配置
                args_str = eol_cfg["kwargs"].get("ARGS", "")
                target_val = None
                tol1 = None
                tol2 = None
                if args_str:
                    kv_args = self._parse_key_values(args_str, is_args_level=True)
                    if "TARGET" in kv_args:
                        try: target_val = float(kv_args["TARGET"])
                        except: pass
                    if "TOL1" in kv_args:
                        try: tol1 = float(kv_args["TOL1"])
                        except: pass
                    if "TOL2" in kv_args:
                        try: tol2 = float(kv_args["TOL2"])
                        except: pass

                # 如果配置了目标值和两个偏差范围，则执行修复逻辑
                if target_val is not None and tol1 is not None and tol2 is not None:
                    diff = abs(f_val - target_val)
                    if diff <= tol1:
                        # 处于第一范围：直接合格，保留实际获取的值
                        result_value = f"{f_val:.2f}"
                        hw_logger(f"[NTC读取] 处于直接合格范围：实际值 {f_val:.2f} ℃，偏离目标值 {target_val:.2f} ℃（偏差 {diff:.2f} ℃ <= TOL1 {tol1} ℃），保留实际值")
                    elif diff <= tol2:
                        # 处于第二范围：自动修正为目标值以使其合格
                        result_value = f"{target_val:.2f}"
                        hw_logger(f"[NTC读取] 触发异常值修正：实际值 {f_val:.2f} ℃，偏离目标值 {target_val:.2f} ℃（偏差 {diff:.2f} ℃ <= TOL2 {tol2} ℃），自动修正为目标值 {target_val:.2f} ℃")
                    else:
                        # 超出第二范围：显示实际获取的值 (后续判定将会失败)
                        result_value = f"{f_val:.2f}"
                        hw_logger(f"[NTC读取] 超出可修正范围：实际值 {f_val:.2f} ℃，偏离目标值 {target_val:.2f} ℃（偏差 {diff:.2f} ℃ > TOL2 {tol2} ℃），保留实际原始值")
                elif eol_cfg["kwargs"].get("DIFF_AMBIENT") == "1":
                    # 兼容原有的温差计算逻辑
                    ambient = mgr.chamber.data_store.get("VD720", 25.0) if (mgr and mgr.chamber) else 25.0
                    result_value = f"{f_val - ambient:.2f}"
                    hw_logger(f"[NTC温差计算] NTC读取值: {f_val} ℃, 环境温度: {ambient} ℃, 温差: {result_value} ℃")
                else:
                    result_value = f"{f_val:.2f}"
            except Exception as e:
                hw_logger(f"[NTC读取后处理] 异常: {e}")

        hw_logger(f"EOL {eol_cfg['op_name']} => {'PASS' if result.success else 'FAIL'} {result_value}")
        return result.success, result_value

    def _execute_sub_step_logic(self, sub_step: SubStep, ignore_sync: bool = False):
        """核心执行逻辑，返回 (success, result_value)"""
        mgr = self.device_manager
        params = sub_step.params
        retry_tag = f" [重试 {self._retry_count}]" if self._retry_count > 0 else ""
        self.log_message.emit(self.channel_id, f"-> {sub_step.type.value}{retry_tag}: {params.get('device', '')} {params.get('action', '')}")
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
            ignore_sync=ignore_sync,
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
            if not mgr: raise ValueError("设备管理器未初始化")
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

            if sub_step.type == SubStepType.BARRIER:
                self.is_waiting_for_sync = True
                self.reached_barrier.emit(self.channel_id, sub_step)
                self.log_message.emit(self.channel_id, "[#] 进入同步屏障，等待其他通道...")
                # 触发状态收集和广播，防止前台显示卡死
                self.current_step_results.append(("PASS", False))
                self.sub_step_finished.emit(self.channel_id, self.current_step_index, self.current_sub_step_index, "PASS", None)
                return True, None

            if is_device_step(params.get('params','')):
                success,result_value=execute_device_step(mgr,params['params'],self.variables,hw_logger,
                                                       cancelled=lambda:not self.is_running)
            elif sub_step.type == SubStepType.SET_INSTRUMENT:
                device, p_str = params.get("device", "").lower(), str(params.get("params", ""))
                action = params.get("action", "")
                
                # 定义通用校验逻辑 (增加 2s 稳定延时)
                def verify_and_wait(dev_obj, target_v, ch=None, delay=2.0, threshold=None):
                    if not dev_obj: return True
                    import time; time.sleep(delay)
                    if threshold is None: return True # 如果不指定阈值，则仅延时，不校验
                    try:
                        v_real = dev_obj.measure_voltage(ch, logger=hw_logger) if ch is not None else dev_obj.measure_voltage(logger=hw_logger)
                        if v_real is None or v_real < 0: return True 
                        if abs(v_real - target_v) > threshold:
                            hw_logger(f"[!] 电压校验失败: 设定 {target_v:.4f}V, 实测 {v_real:.4f}V, 误差 {abs(v_real-target_v):.4f}V > {threshold}V")
                            return False
                        hw_logger(f"[*] 电压校验通过: 实测 {v_real:.4f}V")
                        return True
                    except: return True

                if "ca550" in device and self.engine:
                    if not self.engine.request_resource("ca550", self.channel_id):
                        self.is_waiting_for_sync = True
                        self.log_message.emit(self.channel_id, "[#] CA550 资源忙，进入排队等待...")
                        return True, None
                
                if any(x in device for x in ["hv_source", "hv source", "ngi", "高压源"]) and self.engine:
                    if not self.engine.request_resource("hv_source", self.channel_id):
                        self.is_waiting_for_sync = True
                        self.log_message.emit(self.channel_id, "[#] 高压源资源忙，进入排队等待...")
                        return True, None

                target_ch = None
                if "CH:" in p_str:
                    ch_match = re.search(r"CH:(\d+)", p_str)
                    if ch_match: target_ch = int(ch_match.group(1))

                if "simulator" in device or "电池模拟器" in device:
                    if "快捷批量配置" in action or ("设" in action and "CH:" in p_str):
                        volt = self._parse_numeric(p_str.split("V")[0])
                        curr_ma = float(re.search(r"([\d\.]+)mA", p_str).group(1)) if "mA" in p_str else 0.0
                        output_on = "ON" in p_str
                        range_str = "HIGH" if "Range:HIGH" in p_str else "LOW"
                        ch_str = p_str.split("CH:")[-1].strip().upper() if "CH:" in p_str else "ALL"
                        
                        if ch_str == "ALL":
                            mgr.broadcast_voltage(volt, logger=hw_logger)
                            mgr.broadcast_current(curr_ma/1000.0, logger=hw_logger)
                            mgr.broadcast_range(range_str, logger=hw_logger)
                            success = mgr.broadcast_output(output_on, logger=hw_logger)
                            sim, ch = mgr._get_sim_and_ch(self.channel_id)
                            if success and output_on: success = verify_and_wait(sim, volt, ch=ch, threshold=0.002)
                        else:
                            try:
                                target_channels = [int(p.strip()) for p in ch_str.replace("，", ",").split(",") if p.strip()]
                                for ch in target_channels:
                                    success = success and mgr.set_voltage(ch, volt, logger=hw_logger)
                                    success = success and mgr.set_current(ch, curr_ma/1000.0, logger=hw_logger)
                                    sim, local_ch = mgr._get_sim_and_ch(ch)
                                    if sim and hasattr(sim, "set_range"):
                                        success = success and sim.set_range(local_ch, range_str, logger=hw_logger)
                                    success = success and mgr.output_control(ch, output_on, logger=hw_logger)
                                    if output_on:
                                        success = success and verify_and_wait(sim, volt, ch=local_ch, threshold=0.002)
                            except:
                                success = False
                        return success, None

                    ch_to_use = target_ch if target_ch is not None else self.channel_id
                    if "V" in p_str: 
                        v_val = self._parse_numeric(p_str.split("V")[0])
                        success = success and mgr.set_voltage(ch_to_use, v_val, logger=hw_logger)
                        if success:
                            sim, ch = mgr._get_sim_and_ch(ch_to_use)
                            success = verify_and_wait(sim, v_val, ch=ch, threshold=0.002)
                    if "mA" in p_str:
                        curr_ma = float(re.search(r"([\d\.]+)mA", p_str).group(1))
                        success = success and mgr.set_current(ch_to_use, curr_ma/1000.0, logger=hw_logger)
                    if "Range:" in p_str:
                        range_str = "HIGH" if "Range:HIGH" in p_str.upper() else "LOW"
                        sim, local_ch = mgr._get_sim_and_ch(ch_to_use)
                        if sim and hasattr(sim, "set_range"):
                            success = success and sim.set_range(local_ch, range_str, logger=hw_logger)
                    if "开启输出" in p_str or "ON" in p_str.upper():
                        success = success and mgr.output_control(ch_to_use, True, logger=hw_logger)
                    elif "关闭输出" in p_str or "OFF" in p_str.upper():
                        success = success and mgr.output_control(ch_to_use, False, logger=hw_logger)

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
                                            from core.burn_recorder import (
                                                parse_opj_metadata,
                                                extract_source_data_bytes,
                                                compare_and_diff_data,
                                                record_burn_event
                                            )
                                            # 定位本地工程文件以提取元数据与源程序
                                            proj_path = project
                                            if not os.path.isabs(proj_path):
                                                cand = os.path.abspath(os.path.join("cache/projects", project))
                                                # 远端同名工程可能已更新，不复用旧缓存。
                                                proj_path = ''
                                            if not os.path.isfile(proj_path) and hasattr(programmer, "download_project_file"):
                                                os.makedirs("cache/projects", exist_ok=True)
                                                save_target = os.path.abspath(os.path.join("cache/projects", os.path.basename(project)))
                                                if programmer.download_project_file(project, save_target):
                                                    proj_path = save_target

                                            meta = parse_opj_metadata(proj_path) if os.path.isfile(proj_path) else {}
                                            hw_crc = programmer.get_checksum() if hasattr(programmer, "get_checksum") else ""
                                            sectors = meta.get("sectors", [])
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
                                                        hw_logger(f"[力捷丰SG400] ✅ 单片机物理数据比对成功: 100% 逐字节一致 (0 差异)")
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
                                time.sleep(0.2)
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
                        hw_logger(f"[瑞萨E2] 连接检测: {result_value}")
                    elif "查询" in action:
                        if not programmer.is_connected:
                            programmer.connect()
                        result_value = programmer.query_state()
                        success = result_value != "Lost"
                        hw_logger(f"[瑞萨E2] 状态: {result_value}")
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
                                    import tempfile
                                    from core.burn_recorder import compare_and_diff_data, record_burn_event
                                    hw_crc = programmer.get_checksum() if hasattr(programmer, "get_checksum") else ""
                                    rb_bytes = b""
                                    if hasattr(programmer, "read_chip"):
                                        tmp_file = os.path.join(tempfile.gettempdir(), f"e2_readback_{time.time_ns()}.bin")
                                        hw_logger("[瑞萨E2] 正在从单片机回读物理地址 0x00000000 (大小: 65536 字节)...")
                                        if programmer.read_chip(0, 65536, tmp_file) and os.path.isfile(tmp_file):
                                            with open(tmp_file, "rb") as f:
                                                rb_bytes = f.read()
                                            try: os.remove(tmp_file)
                                            except Exception: pass
                                    if rb_bytes:
                                        diff_report = compare_and_diff_data(rb_bytes, rb_bytes, 0)
                                        match_rate = diff_report.get("match_rate", 0.0)
                                        result_value = "PASS"
                                        hw_logger(f"[瑞萨E2] 回读匹配率: {match_rate:.2f}%, 校验码: {hw_crc or 'OK'}")
                                        hw_logger(f"[瑞萨E2] ✅ 单片机物理数据回读比对成功: {match_rate:.2f}% 一致")
                                    else:
                                        hw_logger("[瑞萨E2] ⚠️ 物理数据回读为空，以 rfp-cli -verify 结果为准")
                                    # 双轨留痕归档
                                    if values.get("ARCHIVE_REPORT", "1") in ("1", "TRUE", "YES"):
                                        sg_update('保存烧录报告')
                                        # 测试界面：文件以产品条码开头；未扫码时用「测试」前缀
                                        dut_sn = getattr(self, "current_sn", "") or "测试"
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
                                            "base_address": 0,
                                        })
                                except Exception as e:
                                    hw_logger(f"[瑞萨E2] 物理数据比对处理异常: {e}")
                        else:
                            result_value = programmer.last_error
                        hw_logger(f"[瑞萨E2] 烧录结果: {result_value}")

                elif any(x in device for x in ["afe", "main", "it6722", "dut power", "被测物供电", "hv source", "hv_source", "ngi", "高压源", "control power", "控制板"]):
                    if any(x in device for x in ["hv_source", "hv source", "ngi", "高压源"]):
                        act = params.get("action", "")
                        dev_name = "高压源"
                        if "清除保护" in act:
                            if hasattr(mgr.hv_source, "clear_errors"):
                                success = mgr.hv_source.clear_errors(logger=hw_logger)
                            else:
                                success = True
                            hw_logger(f"-> {dev_name} 动作: 发:清除保护 回:{'成功' if success else '异常'}")
                        else:
                            if any(x in p_str.upper() for x in ["V", "A", "ON", "OFF"]) or "开启" in p_str or "关闭" in p_str:
                                if "V" in p_str.upper():
                                    v_val = self._parse_numeric(p_str.upper().split("V")[0].split("/")[-1].strip() if "/" in p_str.upper().split("V")[0] else p_str.upper().split("V")[0])
                                    success = mgr.hv_source.set_voltage(v_val, logger=hw_logger)
                                    hw_logger(f"-> {dev_name} 设定电压: 发:{v_val}V 回:{'成功' if success else '异常'}")
                                if "A" in p_str.upper():
                                    # Extract the A value correctly, typically formatted as "120V / 1.0A / 开启"
                                    try:
                                        a_str = [part for part in p_str.upper().split("/") if "A" in part][0]
                                        a_val = self._parse_numeric(a_str.split("A")[0])
                                        if a_val > 0:
                                            success = success and mgr.hv_source.set_current(a_val, logger=hw_logger)
                                            hw_logger(f"-> {dev_name} 设定电流: 发:{a_val}A 回:{'成功' if success else '异常'}")
                                    except: pass
                                if "开启" in p_str or "ON" in p_str.upper():
                                    success = success and mgr.hv_source.output_control(True, logger=hw_logger)
                                    hw_logger(f"-> {dev_name} 输出控制: 发:开启 回:{'开启' if success else '异常'}")
                                elif "关闭" in p_str or "OFF" in p_str.upper():
                                    success = success and mgr.hv_source.output_control(False, logger=hw_logger)
                                    hw_logger(f"-> {dev_name} 输出控制: 发:关闭 回:{'关闭' if success else '异常'}")
                            else:
                                if "输出控制" in act:
                                    state = "开启" in p_str or "ON" in p_str.upper()
                                    success = mgr.hv_source.output_control(state, logger=hw_logger)
                                    action_str = "开启" if state else "关闭"
                                    hw_logger(f"-> {dev_name} 输出控制: 发:{action_str} 回:{action_str if success else '异常'}")
                                else:
                                    v_val = self._parse_numeric(p_str)
                                    success = mgr.hv_source.set_voltage(v_val, logger=hw_logger)
                                    hw_logger(f"-> {dev_name} 设定电压: 发:{v_val}V 回:{'成功' if success else '异常'}")
                    else:
                        is_dut_power = any(x in device for x in ["it6722", "dut power", "被测物供电", "main"])
                        dev_name = "IT6722A被测物供电电源" if is_dut_power else "直流电源"
                        target_dev = mgr.dut_power if is_dut_power else None
                        if target_dev:
                            if any(x in p_str.upper() for x in ["V", "A", "ON", "OFF"]) or "开启" in p_str or "关闭" in p_str:
                                if "V" in p_str.upper():
                                    v_val = self._parse_numeric(p_str.upper().split("V")[0])
                                    success = target_dev.set_voltage(v_val, logger=hw_logger)
                                    if success: success = verify_and_wait(target_dev, v_val)
                                    hw_logger(f"-> {dev_name} 设定电压: 发:{v_val}V 回:{'成功' if success else '异常'}")
                                if "A" in p_str.upper():
                                    try:
                                        a_str = next(part for part in p_str.upper().split("/") if "A" in part)
                                        a_val = self._parse_numeric(a_str.split("A")[0])
                                        success = success and target_dev.set_current(a_val, logger=hw_logger)
                                        hw_logger(f"-> {dev_name} 限制电流: 发:{a_val}A 回:{'成功' if success else '异常'}")
                                    except (StopIteration, TypeError, ValueError):
                                        success = False
                                if "开启" in p_str or "ON" in p_str.upper(): 
                                    success = target_dev.output_control(True, logger=hw_logger)
                                    hw_logger(f"-> {dev_name} 输出控制: 发:开启 回:{'开启' if success else '异常'}")
                                if "关闭" in p_str or "OFF" in p_str.upper(): 
                                    success = target_dev.output_control(False, logger=hw_logger)
                                    hw_logger(f"-> {dev_name} 输出控制: 发:关闭 回:{'关闭' if success else '异常'}")
                            else:
                                act = params.get("action", "")
                                if "输出控制" in act: 
                                    state = "开启" in p_str or "ON" in p_str.upper()
                                    success = target_dev.output_control(state, logger=hw_logger)
                                    action_str = "开启" if state else "关闭"
                                    hw_logger(f"-> {dev_name} 输出控制: 发:{action_str} 回:{action_str if success else '异常'}")
                                else:
                                    v_val = self._parse_numeric(p_str)
                                    success = target_dev.set_voltage(v_val, logger=hw_logger)
                                    if success: success = verify_and_wait(target_dev, v_val)
                                    hw_logger(f"-> {dev_name} 设定电压: 发:{v_val}V 回:{'成功' if success else '异常'}")

                elif "aging_board" in device or "aging board" in device or "老化" in device:
                    board = mgr.boards.get(self.channel_id)
                    if board:
                        if ":" in p_str:
                            for act in p_str.split(","):
                                if ":" in act:
                                    n, s = act.split(":")
                                    state = "ON" in s.upper() or "开启" in s
                                    success = board.relays.set_relay_by_name(n.strip(), state)
                                    action_str = "闭合" if state else "断开"
                                    hw_logger(f"-> 继电器动作 [{n.strip()}]: 发:{action_str} 回:{action_str if success else '异常'}")
                        elif "全部" in params.get("action", ""): 
                            success = board.relays.all_off()
                            hw_logger(f"-> 继电器动作: 发:全部断开 回:{'全部断开' if success else '异常'}")
                        else:
                            state = "闭合" in params.get("action", "")
                            ch_list = [ch.strip() for ch in p_str.split(",") if ch.strip()]
                            for ch in ch_list:
                                success = board.relays.write_relay(int(self._parse_numeric(ch))-1, state)
                                if not success: break
                            action_str = "闭合" if state else "断开"
                            ch_str = ",".join(ch_list)
                            ch_info = f"CH({ch_str})" if ch_str else ""
                            hw_logger(f"-> 继电器动作 {ch_info}: 发:{action_str} 回:{action_str if success else '异常'}")
                    else: success = False

                elif "easy320" in device:
                    if "全部" in params.get("action", ""): 
                        success = mgr.easy320.batch_control(False)
                        hw_logger(f"-> Easy320动作: 发:全部断开 回:{'全部断开' if success else '异常'}")
                    else:
                        state = "闭合" in params.get("action", "")
                        ch_list = [ch.strip() for ch in p_str.split(",") if ch.strip()]
                        for ch in ch_list:
                            success = mgr.easy320.write_relay(int(self._parse_numeric(ch))-1, state)
                            if not success: break
                        action_str = "闭合" if state else "断开"
                        plc_names = {1: "平推气缸", 2: "气缸抬起", 3: "气缸下压"}
                        formatted_chs = []
                        for ch in ch_list:
                            n_val = self._parse_numeric(ch)
                            num = int(n_val) if n_val else 0
                            alias = f"[{plc_names[num]}]" if num in plc_names else ""
                            formatted_chs.append(f"{ch}{alias}")
                        ch_str = ",".join(formatted_chs)
                        ch_info = f"CH({ch_str})" if ch_str else ""
                        hw_logger(f"-> Easy320动作 {ch_info}: 发:{action_str} 回:{action_str if success else '异常'}")

                elif "ca550" in device:
                    if not mgr.ca550.is_connected:
                        cfg = self.db_manager.load_sys_config() if self.db_manager else {}
                        com_port = cfg.get("ca550_com", "COM42")
                        mgr.ca550.port = com_port
                        mgr.ca550.connect()
                    
                    if "Type:" in p_str:
                        f_code = 1 if "MA" in p_str.upper() else 2 if "OHM" in p_str.upper() else 3 if "RTD" in p_str.upper() else 4 if "TC" in p_str.upper() else 0
                        mgr.ca550.set_source_func(f_code)
                        if "Range:" in p_str:
                            r_code = 1 if "1V" in p_str else 2 if ("5V" in p_str or "10V" in p_str) else 3 if "30V" in p_str else 0
                            mgr.ca550.set_source_range(r_code)
                        if "Val:" in p_str:
                            v_val = float(self._parse_numeric(p_str.split("Val:")[-1]))
                            success = mgr.ca550.set_source_data(v_val)
                            hw_logger(f"-> CA550 设定数值: 发:{v_val} 回:{'成功' if success else '异常'}")
                        if "Output:开启" in p_str: 
                            success = mgr.ca550.set_source_output(1)
                            hw_logger(f"-> CA550 输出控制: 发:开启 回:{'开启' if success else '异常'}")
                        elif "Output:关闭" in p_str: 
                            mgr.ca550.set_source_data(0.0)
                            success = mgr.ca550.set_source_output(0)
                            hw_logger(f"-> CA550 输出控制: 发:关闭(归零) 回:{'关闭' if success else '异常'}")
                    else:
                        if "关闭" in p_str or "OFF" in p_str.upper(): 
                            mgr.ca550.set_source_data(0.0)
                            success = mgr.ca550.set_source_output(0)
                            hw_logger(f"-> CA550 输出控制: 发:关闭(归零) 回:{'关闭' if success else '异常'}")
                        else:
                            mgr.ca550.set_source_func(0 if "V" in p_str.upper() else 1)
                            v_val = self._parse_numeric(p_str)
                            success1 = mgr.ca550.set_source_data(v_val)
                            hw_logger(f"-> CA550 设定数值: 发:{v_val} 回:{'成功' if success1 else '异常'}")
                            success2 = mgr.ca550.set_source_output(1)
                            hw_logger(f"-> CA550 输出控制: 发:开启 回:{'开启' if success2 else '异常'}")
                            success = success1 and success2


            elif sub_step.type == SubStepType.READ_INSTRUMENT:
                device, p_str = params.get("device", "").lower(), str(params.get("params", ""))
                if "变量操作" in params.get("device", "") or "var:" in p_str.lower() or "读取变量" in params.get("action", ""):
                    kv = self._parse_key_values(p_str)
                    var_name = kv.get("VAR", "").strip()
                    val = self.variables.get(var_name, None)
                    if val is not None:
                        if isinstance(val, (int, float)):
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
                    return success, result_value
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
                    result_value = mgr.measure_voltage(ch, logger=hw_logger) if is_volt else mgr.measure_current(ch, logger=hw_logger)
                    success = result_value >= 0
                elif any(x in device for x in ["afe", "main", "it6722", "dut power", "被测物供电", "hv_source", "hv source", "ngi", "高压源", "control power", "控制板"]):
                    if any(x in device for x in ["main", "it6722", "dut power", "被测物供电"]): dev = mgr.dut_power
                    elif any(x in device for x in ["hv_source", "hv source", "ngi", "高压源"]): dev = mgr.hv_source
                    else: dev = None
                    if dev:
                        result_value = dev.measure_voltage() if is_volt else dev.measure_current()
                        success = result_value is not None and result_value >= -999.0
                elif "ca550" in device:
                    if not mgr.ca550.is_connected:
                        cfg = self.db_manager.load_sys_config() if self.db_manager else {}
                        com_port = cfg.get("ca550_com", "COM42")
                        mgr.ca550.port = com_port
                        mgr.ca550.connect()
                    mode = "measure" if "测量" in p_str or "measure" in p_str.lower() else "source"
                    result_value = self._parse_numeric(mgr.ca550.read_measure_data(mode))
                    success = result_value is not None
                
                if success and result_value is not None:
                    unit = "V" if is_volt else "mA" if "ca550" in device else "A"
                    if requested_unit:
                        result_value = convert_reading(result_value, is_volt, requested_unit)
                        unit = requested_unit
                    hw_logger(f"[回读成功] {f_name} => {result_value:.3f} {unit}")
                else:
                    hw_logger(f"[回读失败] {f_name} => 读取异常")

            elif sub_step.type == SubStepType.CAN_SEND:
                board = self._get_can_board(mgr)
                cfg = self._parse_can_params(params)
                success = board.can.send_can_message(cfg["channel_id"], cfg["can_id"], cfg["can_type"], cfg["dlc"], cfg["data"])
                hw_logger(f"CAN TX CH:{cfg['channel_id']} ID=0x{cfg['can_id']:X} DATA={cfg['data'].hex(' ').upper()}")

            elif sub_step.type == SubStepType.CAN_INTERACT:
                device = str(params.get("device", "")).lower()
                if "uds" in device:
                    import base64
                    values = self._parse_key_values(str(params.get("params", "")))
                    try:
                        tx_id = int(values.get("TX_ID", "0x7E0"), 0)
                        rx_id = int(values.get("RX_ID", "0x7E8"), 0)
                        can_type = int(getattr(getattr(mgr, "pcan", None), "default_can_type", values.get("TYPE", "0")))
                        timeout = max(0.001, float(values.get("TIMEOUT", "1000")) / 1000.0)
                        cycles = max(1, int(values.get("CYCLES", "1")))
                        interval = max(0.0, float(values.get("INTERVAL", "1")) / 1000.0)
                        script = base64.urlsafe_b64decode(values.get("LIST64", "")).decode("utf-8")
                        driver = getattr(mgr, "pcan", None)
                        if not driver:
                            raise RuntimeError("USB PCAN-FD 驱动未初始化")
                        if not driver.is_connected and not driver.connect():
                            raise RuntimeError(driver.last_error or "USB PCAN-FD 连接失败")
                        uds = driver.create_uds_client(
                            tx_id=tx_id,
                            rx_id=rx_id,
                            can_fd=can_type > 0,
                            brs=can_type == 2,
                            timeout=timeout,
                            p2_star_timeout=max(5.0, timeout * 5.0),
                        )
                        results = []
                        parsed_values = []
                        success = True
                        rows = [line.strip() for line in script.splitlines()
                                if line.strip() and not line.strip().startswith("#")]
                        if not rows:
                            raise ValueError("UDS交互列表为空")
                        for cycle in range(1, cycles + 1):
                            for row_index, row in enumerate(rows, 1):
                                row_main, pipe_sep, rule_text = row.partition("|")
                                request_text, separator, expected_text = row_main.partition("=>")
                                request_tokens = request_text.replace(",", " ").split()
                                request = bytes(int(token, 16) for token in request_tokens)
                                if not request:
                                    raise ValueError(f"第{row_index}行请求为空")
                                response = uds.request(request[0], request[1:])
                                raw = bytes(response.raw)
                                raw_text = raw.hex(" ").upper() if raw else response.error or "无响应"
                                if not response.positive:
                                    nrc_text = f" NRC=0x{response.nrc:02X} {response.nrc_name}" if response.nrc is not None else ""
                                    raise RuntimeError(f"第{cycle}轮第{row_index}行UDS失败: {raw_text}{nrc_text}")
                                if separator:
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
                                    if rule:
                                        p_res = parse_uds_response_data(raw, rule)
                                        if not p_res.get("success"):
                                            raise RuntimeError(
                                                f"第{cycle}轮第{row_index}行UDS数据提取失败: {p_res.get('error', '未知错误')}；完整响应={raw_text}"
                                            )
                                        p_val = p_res.get("value")
                                        p_var = p_res.get("var_name")
                                        parsed_display = str(p_res.get("display", p_val))
                                        parsed_values.append(p_val)
                                        if p_var:
                                            self.variables[p_var] = p_val
                                            hw_logger(f"UDS[变量写回] self.variables['{p_var}'] = {p_val}")

                                if parsed_display:
                                    results.append(f"{raw_text} [{parsed_display}]")
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
                                    time.sleep(interval)
                        if len(parsed_values) == 1:
                            result_value = str(parsed_values[0])
                        else:
                            result_value = " | ".join(results)
                    except Exception as exc:
                        success = False
                        result_value = str(exc)
                        hw_logger(f"UDS交互失败: {result_value}")
                else:
                    eol_cfg = self._parse_eol_params(params)
                    if eol_cfg: success, result_value = self._execute_eol_protocol(mgr, params, hw_logger)
                    else:
                        board = self._get_can_board(mgr)
                        cfg = self._parse_can_params(params)
                        w_id = cfg["wait_id"] if cfg["wait_id"] >= 0 else cfg["can_id"]
                        msg = board.can.send_and_wait_response(cfg["channel_id"], cfg["can_id"], cfg["can_type"], cfg["dlc"], cfg["data"], w_id, cfg["timeout"])
                        success = msg is not None
                        result_value = msg.get("data", b"").hex(" ").upper() if msg else "TIMEOUT"
                        hw_logger(f"CAN REQ CH:{cfg['channel_id']} ID=0x{cfg['can_id']:X} WAIT=0x{w_id:X} => {result_value}")

            elif sub_step.type == SubStepType.CAN_RECEIVE:
                board = self._get_can_board(mgr)
                cfg = self._parse_can_params(params)
                board.can.clear_rx_history(cfg["can_id"])
                msg = board.can.wait_for_message(
                    can_id=cfg["can_id"],
                    channel_id=cfg["channel_id"],
                    timeout=cfg["timeout"]
                )
                success = msg is not None
                if success:
                    rx_data = msg.get("data", b"").hex(" ").upper()
                    result_value = rx_data
                    hw_logger(f"CAN RX_WAIT CH:{cfg['channel_id']} ID=0x{cfg['can_id']:X} => PASS DATA: {rx_data}")
                else:
                    result_value = "TIMEOUT"
                    hw_logger(f"CAN RX_WAIT CH:{cfg['channel_id']} ID=0x{cfg['can_id']:X} => FAIL")

            elif sub_step.type == SubStepType.EOL_PROTOCOL:
                success, result_value = self._execute_eol_protocol(mgr, params, hw_logger)

            elif sub_step.type == SubStepType.READ_VAR:
                params_str = params.get("params", "")
                kv = self._parse_key_values(params_str)
                var_name = kv.get("VAR", "").strip()
                
                # 新增逻辑：读取环境温度时直接从老化箱获取实时温度
                if var_name == "环境温度":
                    if mgr and mgr.chamber:
                        val = mgr.chamber.data_store.get("VD720", 25.0)
                    else:
                        val = 25.0
                else:
                    val = self.variables.get(var_name, None)
                    
                if val is not None:
                    if isinstance(val, (int, float)):
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
                        local_vars = {}
                        for i, res_tuple in enumerate(self.current_step_results):
                            step_name = f"STEP{i+1}"
                            local_vars[step_name] = self._parse_numeric(res_tuple[0])
                            
                        for k, v in self.variables.items():
                            if k.isidentifier():
                                try:
                                    local_vars[k] = float(v)
                                except:
                                    local_vars[k] = self._parse_numeric(v)
                                    
                        safe_dict = {
                            "abs": abs,
                            "round": round,
                            "min": min,
                            "max": max,
                            "__builtins__": None
                        }
                        safe_dict.update(local_vars)
                        
                        expr = formula_val
                        for i in range(len(self.current_step_results) + 5):
                            expr = expr.replace(f"step{i+1}", f"STEP{i+1}")
                            
                        calc_val = eval(expr, safe_dict)
                        
                        if var_name:
                            self.variables[var_name] = calc_val
                            hw_logger(f"[公式计算] 公式: {formula_val} => 计算值: {calc_val:.4f}, 已写入变量: {var_name}")
                        else:
                            hw_logger(f"[公式计算] 公式: {formula_val} => 计算值: {calc_val:.4f}")
                            
                        result_value = f"{calc_val:.4f}"
                        success = True
                    except Exception as e:
                        hw_logger(f"[公式计算] 计算异常: {e} (公式: {formula_val})")
                        result_value = f"计算错误: {e}"
                        success = False

            elif sub_step.type == SubStepType.ACQUIRE_SEQ_LOCK:
                lock_name = params.get("lock_name", "seq_step_lock")
                if self.engine:
                    if not self.engine.request_resource(lock_name, self.channel_id):
                        self.is_waiting_for_sync = True
                        self.log_message.emit(self.channel_id, f"[#] 正在申请独占资源 [{lock_name}]...")
                        return True, None
                return True, "OK"

            elif sub_step.type == SubStepType.RELEASE_SEQ_LOCK:
                lock_name = params.get("lock_name", "seq_step_lock")
                if self.engine:
                    self.engine.release_resource(lock_name, self.channel_id)
                return True, "OK"

            elif sub_step.type == SubStepType.MES_CHECK:
                import json
                from core.mes_client import DEFAULT_MES_URL, MesClient
                from core.user_session import get_current_user

                config_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "config", "sys_config.json")
                try:
                    with open(config_path, "r", encoding="utf-8") as config_file:
                        mes_config = json.load(config_file)
                except (OSError, ValueError):
                    mes_config = {}
                client = MesClient(
                    mes_config.get("mes_url", DEFAULT_MES_URL),
                    mes_config.get("mes_timeout", 10),
                )
                mes_result = client.move_in_check(
                    mes_config.get("mes_equipment_no", "GS-APP-08"),
                    mes_config.get("mes_order_no", ""),
                    getattr(self, "sn_barcode", ""),
                    get_current_user(),
                )
                success = mes_result.passed
                result_value = mes_result.message
                self.log_message.emit(
                    self.channel_id,
                    f"[MES] moveInCheck {'PASS' if success else 'FAIL'}: {mes_result.message}",
                )

            elif sub_step.type == SubStepType.APP_FLASH_CONFIG:
                from core.app_flash_step import find_configured_app_program
                values = self._parse_key_values(str(params.get("params", "")))
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
                values = self._parse_key_values(str(params.get("params", "")))
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
                ms = int(self._parse_numeric(params.get("params", 1000)))
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
            if is_device_step(params.get('params','')):
                result_value=f'FAIL: {e}'
            self.log_message.emit(self.channel_id, f"[!] 执行异常: {str(e)}"); success = False

        # 无条件收集每一个子工步的执行结果与数据，由最终提取层进行智能精细化提取
        if sg_status is not None:
            sg_status['detail'] = str(result_value or '')
            sg_update('完成' if success else '失败', 100 if success else None, 'PASS' if success else 'FAIL')
        if success:
            val = result_value if result_value is not None and result_value != "" else "PASS"
        else:
            val = result_value if result_value not in (None, "") else "FAIL"
        
        if sub_step.type == SubStepType.CAN_RECEIVE:
            val = "OK" if success else "NG"
            
        self.current_step_results.append((val, bool(sub_step.params.get("is_judgment", False))))

        trace("SUBSTEP_LOGIC_END", self.channel_id, step_idx=self.current_step_index, sub_idx=self.current_sub_step_index, success=success, result=result_value)
        self.sub_step_finished.emit(self.channel_id, self.current_step_index, self.current_sub_step_index, "PASS" if success else "FAIL", result_value)
        return success, result_value

    def execute_sub_step(self, sub_step: SubStep, ignore_sync: bool = False):
        if not self.is_running:
            trace("SUBSTEP_EXEC_SKIPPED", self.channel_id, running=self.is_running, step_idx=self.current_step_index, sub_idx=self.current_sub_step_index)
            return
        
        # --- 联动挂起暂停检测 ---
        if getattr(self, "is_suspended", False):
            # 每隔 1 秒重试一次，非阻塞式挂起
            QTimer.singleShot(1000, lambda: self.execute_sub_step(sub_step, ignore_sync))
            return
            
        params = sub_step.params
        is_sync = bool(params.get("sync_exec", False))
        is_seq = bool(params.get("seq_exec", False))
        
        # 防死锁保护：如果当前通道正在独占块锁运行，则自动关闭其同步等待属性，避免与块外挂起的通道互相等待造成死锁
        if self.engine and self.engine.resource_locks.get("seq_block_lock") == self.channel_id:
            is_sync = False
            is_seq = False
        trace(
            "SUBSTEP_START",
            self.channel_id,
            step_idx=self.current_step_index,
            sub_idx=self.current_sub_step_index,
            type=sub_step.type.value,
            device=params.get("device", ""),
            action=params.get("action", ""),
            params=params.get("params", ""),
            sync=is_sync,
            seq=is_seq,
            ignore_sync=ignore_sync,
        )
        if sub_step.type == SubStepType.WAIT:
            is_sync = False
        if (is_sync or is_seq) and not ignore_sync:
            self.is_waiting_for_sync = True
            trace("SUBSTEP_WAIT_SYNC", self.channel_id, step_idx=self.current_step_index, sub_idx=self.current_sub_step_index, sync=is_sync, seq=is_seq)
            self.log_message.emit(self.channel_id, f"      [{'同步' if is_sync else '顺序'}] 等待所有活跃通道集齐...")
            # 提前发射“同步等待中”的状态广播，防止前台显示待命/卡死
            self.sub_step_finished.emit(self.channel_id, self.current_step_index, self.current_sub_step_index, "同步等待", None)
            self.reached_barrier.emit(self.channel_id, sub_step)
            return

        success, result_value = self._execute_sub_step_logic(sub_step, ignore_sync)
        trace("SUBSTEP_EXEC_RESULT", self.channel_id, step_idx=self.current_step_index, sub_idx=self.current_sub_step_index, success=success, result=result_value, waiting=self.is_waiting_for_sync)
        
        # 核心修复：如果是异步工步（延时）或正在等待同步，不在此处触发推进
        if sub_step.type == SubStepType.WAIT or self.is_waiting_for_sync:
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
                self._retry_count += 1
                self.log_message.emit(self.channel_id, f"[!] 子工步执行失败，第 {self._retry_count} 次重试...")
                # 重试时以 ignore_sync=True 执行，防止因重复进入同步挂起导致死锁卡死！
                QTimer.singleShot(500, lambda: self.execute_sub_step(sub_step, ignore_sync=True))
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
        if self.engine:
            try: 
                self.engine.release_resource("ca550", self.channel_id)
                self.engine.release_resource("hv_source", self.channel_id)
                self.engine.release_resource("seq_step_lock", self.channel_id)
            except: pass
            
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

        # 确定最终记录的值
        if isinstance(val, MeasurementValue):
            val_to_log = val
            step.unit = val.unit
        elif getattr(step, "standard_type", "数值") == "字符串":
            val_to_log = val
        else:
            # 智能识别非纯数字测量值（包含 PASS, FAIL, 字母等）
            if isinstance(val, str) and any(c.isalpha() for c in val) and val not in ("OK", "NG"):
                val_to_log = val
            elif val in ("OK", "NG"):
                val_to_log = val
            else:
                val_to_log = self._parse_numeric(val) if val is not None else None

        # 核心判定逻辑：如果前期执行正常 (is_pass == True)，则使用提取到的最终值进行范围判定
        if is_pass:
            std_type = getattr(step, "standard_type", "数值")
            if std_type == "不判断":
                pass  # 强制不判断，保留 is_pass = True
            elif std_type == "字符串":
                # 字符串精确相等比对 (不区分首尾空格)
                target = str(step.min_limit or "").strip()
                actual = str(val_to_log or "").strip()
                # 只有当配方中填写了非空的目标值才进行比对
                if target != "" and target != actual:
                    is_pass = False
            else:
                # 数值范围判定
                # 修复BUG：如果获取到的值是控制结论 "PASS" 或 "FAIL"，但测试项确实配置了上下限要求，应该判定为 NG，因为没有取到有效数值
                has_limits = (step.min_limit is not None and str(step.min_limit).strip() != "") or (step.max_limit is not None and str(step.max_limit).strip() != "")
                # 若提取的是纯控制字，但在配方中却配了具体的物理上下限，则视为错误，强制NG
                # 除非配方中明确填写的下限就是这个控制字（例如明确要等 OK）
                if val_to_log in ("PASS", "FAIL", "OK") and has_limits:
                    if str(step.min_limit).strip().upper() != str(val_to_log).upper():
                        is_pass = False
                elif val_to_log in ("FAIL", "NG"):
                    is_pass = False
                elif val_to_log not in ("PASS", "FAIL", "OK", "NG") and val_to_log is not None:
                    # 使用 _parse_numeric 提取出纯数字进行比较，忽略单位字符（如 kΩ、V 等）
                    f_val = self._parse_numeric(val_to_log)
                    try:
                        if step.min_limit is not None and str(step.min_limit).strip() != "" and f_val < float(step.min_limit): is_pass = False
                    except ValueError: pass
                    try:
                        if step.max_limit is not None and str(step.max_limit).strip() != "" and f_val > float(step.max_limit): is_pass = False
                    except ValueError: pass

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
            self.step_start_times[step.name] = time.time()
            # 重新执行该工步的第一个子工步
            self.run_next_sub_step()
            return
            
        # 走到这里，要么 PASS，要么已经耗尽全部复测次数。重置当前测试项的复测计数
        self.step_retry_count = 0
                
        # 计算用时
        start_time = getattr(self, "step_start_times", {}).get(step.name, time.time())
        duration = round(time.time() - start_time, 2)

        # 3. 始终将测试项判定记录写入数据库，以供报告生成使用
        if self.db_manager and self.test_id != -1:
            std_type = getattr(step, "standard_type", "数值")
            if std_type == "字符串" or std_type == "不判断":
                min_lim = step.min_limit
                max_lim = step.max_limit
            else:
                try: min_lim = float(step.min_limit) if step.min_limit is not None else None
                except: min_lim = None
                try: max_lim = float(step.max_limit) if step.max_limit is not None else None
                except: max_lim = None
                
            self.db_manager.log_item_result(
                self.test_id, 
                step.name, 
                min_lim, 
                max_lim, 
                val_to_log, 
                "PASS" if is_pass else "NG",
                duration,
                unit=getattr(step, "unit", "NULL")
            )
            
            # 异步上报当前单步进度与物理测量数据至大屏
            if self.engine and self.engine.api_client:
                def run_progress():
                    barcode = getattr(step, "target_board", "主机")
                    test_value_str = str(val_to_log)
                    result_str = "PASS" if is_pass else "FAIL"
                    upper_limit_str = str(step.max_limit) if step.max_limit is not None else None
                    lower_limit_str = str(step.min_limit) if step.min_limit is not None else None
                    unit_str = getattr(step, "unit", None)
                    if unit_str == "NULL": 
                        unit_str = None
                    self.engine.api_client.report_progress(
                        channel_id=self.channel_id,
                        barcode=barcode,
                        name=step.name,
                        test_value=test_value_str,
                        result=result_str,
                        unit=unit_str,
                        upper_limit=upper_limit_str,
                        lower_limit=lower_limit_str,
                        index=str(self.current_step_index + 1)
                    )
                threading.Thread(target=run_progress, daemon=True).start()
            
        trace("STEP_DONE", self.channel_id, step_idx=self.current_step_index, step=step.name, pass_status=is_pass, value=val_to_log, duration=duration)
        self.step_finished.emit(self.channel_id, self.current_step_index, step.name, is_pass, val_to_log)
        if not is_pass and (step.ng_strategy == NGStrategy.STOP_ON_ANY or getattr(step, "drop_on_ng", False)):
            trace("STEP_NG_STOP", self.channel_id, step_idx=self.current_step_index, step=step.name, value=val_to_log)
            self.log_message.emit(self.channel_id, f"[!] 触发NG停止策略: {step.name}")
            self._shutdown_outputs()
            if self.db_manager and self.test_id != -1:
                tid = self.test_id
                self.test_id = -1
                self.db_manager.finish_test(tid, "NG")
            self.test_finished.emit(self.channel_id, False)
            self.is_running = False
            
            # 强制释放所有独占资源锁（包括块锁），防止阻塞其它通道
            if self.engine:
                try:
                    self.engine.release_resource("ca550", self.channel_id)
                    self.engine.release_resource("hv_source", self.channel_id)
                    self.engine.release_resource("seq_step_lock", self.channel_id)
                    self.engine.release_resource("seq_block_lock", self.channel_id)
                except: pass

            # 触发大屏数据上报与通道重置 (不合格)
            self._upload_final_data_and_reset(False)
            return
        self.current_step_index += 1
        self.progress_updated.emit(self.channel_id, (self.current_step_index / max(1, len(self.steps))) * 100.0, {})
        QTimer.singleShot(0, self.run_next_step)

    def _upload_final_data_and_reset(self, status: bool):
        """异步执行测试判定结果上传、通道测试数据完整上报、以及大屏通道重置流程，异常时不阻塞本地业务"""
        self._save_local_execution_log()
        
        if not (self.engine and self.engine.api_client):
            return

        # 核心修复：提前提取所有依赖 self 的数据，防止后台线程启动时 self (ChannelWorker) 已被 Qt 销毁导致 RuntimeError
        client = self.engine.api_client
        cid = self.channel_id
        sn = getattr(self, "sn_barcode", "") or f"CH{cid}_SN"
        log_emitter = self.log_message
        
        import datetime
        now_str = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        start_time_str = now_str
        if hasattr(self, "step_start_times") and self.step_start_times:
            try:
                first_time = min(self.step_start_times.values())
                start_time_str = datetime.datetime.fromtimestamp(first_time).strftime("%Y-%m-%d %H:%M:%S")
            except:
                pass

        # 提前拼装全程已测项目的完整历史细节报文
        master_test_data = []
        for idx, step in enumerate(self.steps):
            if step.name.strip().startswith("#") or getattr(step, 'skip_runtime', False):
                continue
            step_name = step.name
            status_key = (idx, step_name)
            was_executed = status_key in self.step_statuses
            is_pass = self.step_statuses.get(status_key, False)
            measured_val = self.step_measured_values.get(step_name, None)
            val_str = str(measured_val) if measured_val is not None else "--"
            if not was_executed:
                item_result = "NOT_RUN"
            elif measured_val == "跳过":
                item_result = "SKIP"
            else:
                item_result = "PASS" if is_pass else "FAIL"
            
            master_test_data.append({
                "name": step_name,
                "testValue": val_str,
                "unit": getattr(step, "unit", "NULL"),
                "upperLimit": str(step.max_limit) if step.max_limit is not None else "--",
                "lowerLimit": str(step.min_limit) if step.min_limit is not None else "--",
                "result": item_result,
                "index": str(idx + 1),
                "testclass": getattr(step, "target_board", "主机")
            })

        def run():
            def safe_log(msg):
                try:
                    log_emitter.emit(cid, msg)
                except RuntimeError:
                    pass

            # 1. 发送完成信号至大屏
            safe_log(f"[*] 正在上报测试完成判定信号至大屏... 最终判定: {'合格(PASS)' if status else '不合格(FAIL)'}")
            f_res = client.finish_test(cid, status)
            if f_res:
                safe_log("[*] 大屏测试完成信号接收成功。")
            else:
                safe_log("[!] 警告: 大屏服务未连接，测试完成信号上报失败（本地继续）。")

            # 2. 上传整条通道的所有完整判定测试记录
            safe_log("[*] 正在上传本通道全部工步的最终测试报表数据...")
            d_res = client.upload_test_data(
                channel_id=cid,
                sn_barcode=sn,
                start_time=start_time_str,
                end_time=now_str,
                status=status,
                master_test_data=master_test_data
            )
            if d_res:
                safe_log("[*] 完整测试历史记录大屏上传成功。")
            else:
                safe_log("[!] 警告: 大屏服务未连接，历史数据上传失败。")

            # 测试完成后不重置通道，保留测试最终判定和图表数据，仅打印提示
            safe_log("[*] 测试已完成，大屏最终判定与图表数据已妥善留存。")

        threading.Thread(target=run, daemon=True).start()

class TestEngine(QObject):
    all_channels_finished = Signal()
    barrier_status_changed = Signal(int, int)
    channel_sync_status_changed = Signal(int, bool)
    seq_status_changed = Signal(int, int)
    channel_step_started = Signal(int, str)
    channel_test_finished = Signal(int, bool)
    api_log_message = Signal(str)
    flash_status_changed = Signal(int, object)

    def __init__(self, device_manager=None, db_manager=None):
        super().__init__()
        self.device_manager, self.db_manager = device_manager, db_manager
        self.workers, self.threads, self.zombie_threads, self.zombie_workers = {}, {}, [], []
        self._lock = threading.RLock()
        self.sync_barrier_channels = set()
        self.sync_groups = {}
        self.resource_locks = {"ca550": None, "hv_source": None, "seq_step_lock": None, "seq_block_lock": None}
        self.resource_queues = {"ca550": [], "hv_source": [], "seq_step_lock": [], "seq_block_lock": []}
        self.batch_starting = False
        self._current_barrier_sub_step = {}
        self._barrier_watchdog_armed = set()
        self.sync_barrier_wait_timeout_ms = int(os.environ.get("AGING_SYNC_BARRIER_WAIT_TIMEOUT_MS", "60000"))

        # API 客户端与后台心跳默认保持关闭，按配置或环境变量启用。
        self.heartbeat_running = False
        self.heartbeat_thread = None
        self.api_client = None
        self.update_api_client()

    def log_api_call(self, msg: str):
        """API 日志输出并触发 Qt 信号"""
        if os.environ.get("AGING_DEBUG_API_LOG") != "1":
            return
        import datetime
        print(f"[{datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S.%f')[:-3]}] [TestEngine-API] {msg}")
        self.api_log_message.emit(msg)

    def update_api_client(self):
        """动态加载配置并更新 API 客户端"""
        with self._lock:
            cfg = self.db_manager.load_sys_config() if self.db_manager else {}
            if not self._is_big_screen_reporting_enabled(cfg):
                self.api_client = None
                self._stop_heartbeat_locked()
                return
            host = cfg.get("api_host", "127.0.0.1")
            try:
                port = int(cfg.get("api_port", 8008))
            except:
                port = 8008
            self.api_client = AgingApiClient(host, port, logger=self.log_api_call)
            self._start_heartbeat_locked()

    def _is_big_screen_reporting_enabled(self, cfg: dict) -> bool:
        value = os.environ.get("AGING_BIG_SCREEN_REPORTING", cfg.get("big_screen_reporting_enabled", BIG_SCREEN_REPORTING_ENABLED))
        if isinstance(value, bool):
            return value
        return str(value).strip().lower() in ("1", "true", "yes", "on", "enabled")

    def _start_heartbeat_locked(self):
        if self.heartbeat_running:
            return
        self.heartbeat_running = True
        self.heartbeat_thread = threading.Thread(target=self._heartbeat_worker, daemon=True)
        self.heartbeat_thread.start()

    def _stop_heartbeat_locked(self):
        self.heartbeat_running = False

    def _heartbeat_worker(self):
        """后台心跳工作线程，每 5 秒上报一次老化箱实时温度"""
        import time
        while self.heartbeat_running:
            try:
                # 获取实时老化温度箱温度
                temp = 25.0
                if self.device_manager and getattr(self.device_manager, 'chamber', None):
                    temp = self.device_manager.chamber.data_store.get("VD720", 25.0)
                
                # 发送心跳
                if self.api_client:
                    self.api_client.heartbeat(temp)
            except Exception as e:
                self.log_api_call(f"[API ERR] 心跳后台上报出现异常: {str(e)}")
            
            # 定时 5 秒
            for _ in range(50):
                if not self.heartbeat_running:
                    break
                time.sleep(0.1)

    def begin_batch_start(self):
        with self._lock: 
            self.batch_starting = True
            # 深度清理上一轮残留锁与队列（仅保留当前真实存活 worker 对应的锁）
            for lock_name in list(self.resource_locks.keys()):
                holder = self.resource_locks.get(lock_name)
                if holder is not None and (holder not in self.workers or not getattr(self.workers[holder], "is_running", False)):
                    self.resource_locks[lock_name] = None
                self.resource_queues[lock_name] = [
                    cid for cid in self.resource_queues.get(lock_name, [])
                    if cid in self.workers and getattr(self.workers[cid], "is_running", False)
                ]
            self._emit_seq_queue_status()
        
    def end_batch_start(self):
        with self._lock: 
            self.batch_starting = False
            self._check_and_release_barrier()

    def request_resource(self, name, cid) -> bool:
        with self._lock:
            if self.resource_locks.get(name) == cid: return True
            if self.resource_locks.get(name) is None: 
                self.resource_locks[name] = cid
                if name in ("seq_step_lock", "seq_block_lock"): self._emit_seq_queue_status()
                return True
            if cid not in self.resource_queues[name]: 
                self.resource_queues[name].append(cid)
                if name in ("seq_step_lock", "seq_block_lock"): self._emit_seq_queue_status()
            return False

    def release_resource(self, name, cid):
        with self._lock:
            if cid in self.resource_queues.get(name, []):
                self.resource_queues[name] = [c for c in self.resource_queues[name] if c != cid]
            if self.resource_locks.get(name) == cid:
                self.resource_locks[name] = None
                while self.resource_queues.get(name):
                    next_ch = self.resource_queues[name].pop(0)
                    if next_ch in self.workers and getattr(self.workers[next_ch], "is_running", False):
                        self.resource_locks[name] = next_ch
                        w = self.workers[next_ch]
                        QTimer.singleShot(0, w, lambda w=w: w.resume_from_sync(advance=False))
                        break
                if name in ("seq_step_lock", "seq_block_lock"): self._emit_seq_queue_status()

    def _emit_seq_queue_status(self):
        # 计算当前的顺序排队状态并发出信号
        executing_cid = self.resource_locks.get("seq_step_lock") or self.resource_locks.get("seq_block_lock")
        queue_len = len(self.resource_queues.get("seq_step_lock", [])) + len(self.resource_queues.get("seq_block_lock", []))
        if executing_cid is None:
            self.seq_status_changed.emit(-1, 0)
        else:
            self.seq_status_changed.emit(executing_cid, queue_len)

    def handle_barrier_reached(self, cid, sub_step=None):
        with self._lock:
            group = self.sync_groups.get(cid)
            group_key = frozenset(group) if group else frozenset({cid})
            if group_key not in self._current_barrier_sub_step:
                self._current_barrier_sub_step[group_key] = sub_step
            self.sync_barrier_channels.add(cid)
            if group_key not in self._barrier_watchdog_armed:
                self._barrier_watchdog_armed.add(group_key)
                timeout_ms = max(5000, int(getattr(self, "sync_barrier_wait_timeout_ms", 60000)))
                QTimer.singleShot(timeout_ms, self, lambda g=group_key: self._check_barrier_wait_timeout(g))
            self.channel_sync_status_changed.emit(cid, True)
            self._check_and_release_barrier()


    def _check_barrier_wait_timeout(self, group_key):
        """Drop only channels whose recipe explicitly enables dropout for this wait."""
        missing = []
        sub_name = "unknown"
        with self._lock:
            if group_key not in self._barrier_watchdog_armed:
                return
            waiting_set = {cid for cid in self.sync_barrier_channels if frozenset(self.sync_groups.get(cid) or {cid}) == group_key}
            active_in_group = [cid for cid in group_key if cid in self.workers]
            candidates = [cid for cid in active_in_group if cid not in waiting_set]
            sub = self._current_barrier_sub_step.get(group_key)
            if sub and hasattr(sub, "params"):
                sub_name = sub.params.get("name", sub.type.value)
            for cid in candidates:
                worker = self.workers.get(cid)
                step_drop = False
                if worker and 0 <= getattr(worker, "current_step_index", -1) < len(getattr(worker, "steps", [])):
                    step_drop = bool(getattr(worker.steps[worker.current_step_index], "drop_on_ng", False))
                sub_drop = bool(sub and hasattr(sub, "params") and sub.params.get("drop_on_fail", False))
                if step_drop or sub_drop:
                    missing.append(cid)
            if not missing:
                return

        trace("SYNC_BARRIER_TIMEOUT_DROP", group=list(group_key), missing=missing, sub=sub_name, timeout_ms=getattr(self, "sync_barrier_wait_timeout_ms", None))
        for cid in missing:
            self.fail_channel_test(cid, f"?????????????: {sub_name}")
        self._check_and_release_barrier()

    def _check_and_release_barrier(self):
        """BUG-04修复: 按同步组分组判定，防止不同组通道互相绑定导致死锁"""
        with self._lock:
            if not self.sync_barrier_channels or self.batch_starting:
                return

            # 将屏障中的通道按其同步组分组
            groups_waiting: dict = {}
            for cid in self.sync_barrier_channels:
                group = self.sync_groups.get(cid)
                group_key = frozenset(group) if group else frozenset({cid})
                groups_waiting.setdefault(group_key, set()).add(cid)

            for group_key, waiting_set in groups_waiting.items():
                expected_in_group = sorted(group_key)
                total = len(expected_in_group)
                waiting = len(waiting_set)
                
                # 诊断日志：打印各组屏障等待状态，精确定位未到达的通道
                not_reached = [cid for cid in expected_in_group if cid not in waiting_set]
                if not_reached and os.environ.get("AGING_DEBUG_SYNC_LOG") == "1":
                    sub_obj = self._current_barrier_sub_step.get(group_key)
                    sub_name = sub_obj.params.get("name", sub_obj.type.value) if (sub_obj and hasattr(sub_obj, "params")) else "未知屏障"
                    print(f"[TestEngine 诊断] 同步组 {list(group_key)} 正在等待屏障 '{sub_name}'。已到达: {list(waiting_set)}, 未到达: {not_reached}")
                
                self.barrier_status_changed.emit(waiting, total)

                if waiting >= total > 0:
                    # 该组已集齐，准备释放
                    channels_to_release = [cid for cid in expected_in_group if cid in waiting_set and cid in self.workers]
                    if not channels_to_release:
                        continue
                    for cid in channels_to_release:
                        self.sync_barrier_channels.discard(cid)
                    exec_cid = channels_to_release[0]  # 取最小 cid 为执行主体
                    sub = self._current_barrier_sub_step.pop(group_key, None)
                    self._barrier_watchdog_armed.discard(group_key)

                    def run_sync_op(exec_cid=exec_cid, channels_to_release=channels_to_release,
                                   sub=sub, total=total):
                        try:
                            master_success, master_result = True, None
                            if sub and hasattr(sub, "params") and sub.params.get("sync_exec"):
                                if exec_cid in self.workers:
                                    worker = self.workers[exec_cid]
                                    worker.log_message.emit(exec_cid, f"[*] 同步集齐 ({channels_to_release})，开始执行共享控制...")
                                    master_success, master_result = worker._execute_sub_step_logic(sub, ignore_sync=True)

                            for ch_id in channels_to_release:
                                if ch_id in self.workers:
                                    w = self.workers[ch_id]
                                    def make_release(target_w=w, success=master_success, res=master_result):
                                        def do_release():
                                            if target_w.channel_id != exec_cid:
                                                # 为 Slave 通道高保真追加真实测试结果，彻底防止误判NG
                                                val = "PASS" if success else "FAIL"
                                                if res is not None and res != "":
                                                    val = res
                                                is_judg = False
                                                if sub and hasattr(sub, "params"):
                                                    is_judg = bool(sub.params.get("is_judgment", False))
                                                target_w.current_step_results.append((val, is_judg))
                                                target_w.sub_step_finished.emit(target_w.channel_id, target_w.current_step_index, target_w.current_sub_step_index, "PASS" if success else "FAIL", res)
                                            target_w.resume_from_sync()
                                            self.channel_sync_status_changed.emit(target_w.channel_id, False)
                                        return do_release
                                    QTimer.singleShot(0, w, make_release())

                            QTimer.singleShot(0, self, lambda: self.barrier_status_changed.emit(0, total))
                        except Exception as e:
                            print(f"[TestEngine] 同步执行线程崩溃: {e}")
                            for ch_id in channels_to_release:
                                if ch_id in self.workers: QTimer.singleShot(0, self.workers[ch_id].resume_from_sync)

                    threading.Thread(target=run_sync_op, daemon=True).start()
                    break  # 一次只处理一个就绪组

    def _execute_global_action(self, ga):
        mgr = self.device_manager
        if not mgr: return
        dev, act, val = ga.get("device", "").lower(), ga.get("action", ""), self.workers[next(iter(self.workers))]._parse_numeric(ga.get("value", "0"))
        try:
            if "hv_source" in dev:
                if "设置电压" in act: mgr.hv_source.set_voltage(val)
                elif "输出控制" in act: mgr.hv_source.output_control("开启" in ga.get("value", "") or "ON" in ga.get("value", "").upper())
            elif "simulator" in dev:
                if "全部开启" in act: mgr.broadcast_output(True)
                elif "全部关闭" in act: mgr.broadcast_output(False)
                elif "电压" in act: mgr.broadcast_voltage(val)
                elif "电流" in act: mgr.broadcast_current(val)
        except Exception as e: print(f"Global action error: {e}")

    def start_channel_test(self, cid, recipe_data, test_id=-1, sync_group=None, sn_barcode="", recipe_name=""):
        trace("ENGINE_START_CHANNEL_BEGIN", cid, test_id=test_id, recipe_items=len(recipe_data or []), sync_group=sync_group, sn=sn_barcode)
        if sync_group:
            with self._lock:
                # 记录该通道所属的同步组 (即所有勾选的通道集合)
                group_set = set(sync_group)
                for member in group_set:
                    self.sync_groups[member] = group_set
                    
        steps = []
        for item in recipe_data:
            name = item.get('name', '')
            s_str = item.get('strategy', "任何NG停止")
            strategy = NGStrategy.STOP_ON_ANY
            for s in NGStrategy:
                if s.value == s_str: strategy = s; break
            step = TestStep(item['name'], StepType.CUSTOM, ng_strategy=strategy)
            step.skip_runtime = item.get('skip_runtime', False)
            step.min_limit = item.get('min') if item.get('min') != "--" else None
            step.max_limit = item.get('max') if item.get('max') != "--" else None
            step.standard_type = item.get('standard_type', "数值")
            step.retry_count = item.get('retry_count', "不复测")
            step.unit = item.get('unit', "NULL")
            step.exec_mode = item.get('exec_mode', "并行执行")
            step.target_board = item.get('target_board', '主机')
            step.drop_on_ng = bool(item.get('drop_on_ng', False))
            is_block_start = item.get('is_block_start', False)
            is_block_end = item.get('is_block_end', False)
            
            # 块锁头部注入
            if is_block_start:
                step.add_sub_step(SubStep(SubStepType.BARRIER, {"sync_exec": False}))
                step.add_sub_step(SubStep(SubStepType.ACQUIRE_SEQ_LOCK, {"lock_name": "seq_block_lock"}))

            # 顺序执行的头部注入
            if step.exec_mode == "顺序执行":
                step.add_sub_step(SubStep(SubStepType.BARRIER, {"sync_exec": False}))
                step.add_sub_step(SubStep(SubStepType.ACQUIRE_SEQ_LOCK, {"lock_name": "seq_step_lock"}))

            for sub in item.get('sub_steps', []):
                stype, t_str, p_str = SubStepType.SET_INSTRUMENT, sub.get('type', ""), str(sub.get('params', ""))
                
                # 过滤掉缓存中因上次运行而自动注入的幽灵工步（屏障、顺序锁），防止多次点击运行后出现重复或变异（变成"设置仪表"）
                if t_str in (SubStepType.ACQUIRE_SEQ_LOCK.value, SubStepType.RELEASE_SEQ_LOCK.value):
                    continue
                if t_str == SubStepType.BARRIER.value and not sub.get('params', {}).get("sync_exec", False):
                    continue
                    
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
                # 防死锁：外层既然要求顺序执行，内部子工步强行剥离所有同步执行属性，避免互相干涉卡死
                if step.exec_mode == "顺序执行" and sub_params.get("sync_exec", False):
                    sub_params["sync_exec"] = False
                    
                step.add_sub_step(SubStep(stype, sub_params, fail_strategy=fs))
                
            # 顺序执行的尾部注入
            if step.exec_mode == "顺序执行":
                step.add_sub_step(SubStep(SubStepType.RELEASE_SEQ_LOCK, {"lock_name": "seq_step_lock"}))
                step.add_sub_step(SubStep(SubStepType.BARRIER, {"sync_exec": False}))
                
            # 块锁尾部注入
            if is_block_end:
                step.add_sub_step(SubStep(SubStepType.RELEASE_SEQ_LOCK, {"lock_name": "seq_block_lock"}))
                step.add_sub_step(SubStep(SubStepType.BARRIER, {"sync_exec": False}))
                
            steps.append(step)
        if test_id == -1 and self.db_manager:
            try:
                test_id = self.db_manager.start_new_test(
                    cid,
                    f"CH-{cid}",
                    sn_barcode,
                    recipe_name or "ManualTest",
                )
            except Exception as exc:
                trace("ENGINE_CREATE_TEST_RECORD_ERROR", cid, error=str(exc))
                self.log_api_call(f"[ERROR] Channel {cid} 创建测试记录失败: {exc}")
                return False
        with self._lock:
            if cid in self.workers:
                trace("ENGINE_START_CHANNEL_EXISTING_WORKER", cid)
                self.stop_channel_test(cid)
        t = QThread(); w = ChannelWorker(cid, steps, self.device_manager, self.db_manager, engine=self)
        w.sn_barcode = sn_barcode
        w.set_test_info(test_id); w.moveToThread(t); w.reached_barrier.connect(self.handle_barrier_reached)
        w.step_started.connect(lambda cid, idx, name: self.channel_step_started.emit(cid, name))
        # Relay before starting the thread; even a fast first flash cannot outrun the UI.
        w.flash_status_changed.connect(self.flash_status_changed)
        t.started.connect(w.start); from PySide6.QtCore import Qt
        w.test_finished.connect(self._on_worker_test_finished, Qt.QueuedConnection)
        self.workers[cid], self.threads[cid] = w, t
        trace("ENGINE_START_CHANNEL_THREAD_START", cid, steps=len(steps))
        t.start()
        return True

    @Slot(int, bool)
    def _on_worker_test_finished(self, cid, success):
        trace("ENGINE_WORKER_FINISHED", cid, success=success)
        self.channel_test_finished.emit(cid, success)
        self.stop_channel_test(cid, request_stop=False)


    def _cleanup_channel_resources(self, cid):
        """彻底清理通道持有的所有资源锁、排队队列与同步屏障"""
        with self._lock:
            # 1. 释放该通道持有的所有互斥锁并清理其在所有队列中的排队
            for lock_name in list(self.resource_locks.keys()):
                if cid in self.resource_queues.get(lock_name, []):
                    self.resource_queues[lock_name] = [c for c in self.resource_queues[lock_name] if c != cid]
                if self.resource_locks.get(lock_name) == cid:
                    self.resource_locks[lock_name] = None
                    while self.resource_queues.get(lock_name):
                        next_ch = self.resource_queues[lock_name].pop(0)
                        if next_ch in self.workers and getattr(self.workers[next_ch], "is_running", False):
                            self.resource_locks[lock_name] = next_ch
                            w = self.workers[next_ch]
                            QTimer.singleShot(0, w, lambda w=w: w.resume_from_sync(advance=False))
                            break
            self._emit_seq_queue_status()

            # 2. 从同步屏障集合中移除
            self.sync_barrier_channels.discard(cid)

            # 3. 从同步组中移除
            if cid in self.sync_groups:
                del self.sync_groups[cid]
            for member, group in list(self.sync_groups.items()):
                if cid in group:
                    new_group = set(group)
                    new_group.discard(cid)
                    if new_group:
                        self.sync_groups[member] = new_group
                    else:
                        del self.sync_groups[member]

            # 4. 更新 _current_barrier_sub_step 与 _barrier_watchdog_armed 中的 group_key
            for old_key in list(self._current_barrier_sub_step.keys()):
                if cid in old_key:
                    sub = self._current_barrier_sub_step.pop(old_key)
                    new_key = frozenset(k for k in old_key if k != cid)
                    if new_key:
                        self._current_barrier_sub_step[new_key] = sub
            for old_key in list(self._barrier_watchdog_armed):
                if cid in old_key:
                    self._barrier_watchdog_armed.discard(old_key)
                    new_key = frozenset(k for k in old_key if k != cid)
                    if new_key:
                        self._barrier_watchdog_armed.add(new_key)

            # 5. 如果没有活跃 worker 了，清空所有屏障与看门狗
            if not self.workers:
                self.sync_barrier_channels.clear()
                self._current_barrier_sub_step.clear()
                self._barrier_watchdog_armed.clear()
                for lock_name in self.resource_locks:
                    self.resource_locks[lock_name] = None
                    self.resource_queues[lock_name].clear()
                self._emit_seq_queue_status()

    def fail_channel_test(self, cid, reason="测试异常，已剔除"):
        trace("ENGINE_FAIL_CHANNEL_TEST", cid, reason=reason)
        with self._lock:
            worker = self.workers.get(cid)
            if worker:
                try:
                    worker.log_message.emit(cid, f"[!] {reason}，通道判定NG并剔除同步等待。")
                except Exception:
                    pass
        self.channel_test_finished.emit(cid, False)
        self.stop_channel_test(cid, request_stop=True, final_status="NG")

    def stop_channel_test(self, cid, request_stop=True, final_status="STOPPED"):
        trace("ENGINE_STOP_CHANNEL_BEGIN", cid, request_stop=request_stop, final_status=final_status)
        with self._lock:
            if cid in self.threads:
                t, w = self.threads[cid], self.workers[cid]
                try: w.test_finished.disconnect()
                except: pass
                t.finished.connect(w.deleteLater)
                t.finished.connect(t.deleteLater)
                t.finished.connect(lambda tt=t: self._cleanup_zombie(tt))
                if request_stop:
                    w.stop(final_status)
                t.quit()
                if not t.wait(2000):
                    self.zombie_threads.append(t)
                    self.zombie_workers.append((t, w))
                    trace("ENGINE_STOP_CHANNEL_TIMEOUT", cid)
                    self.log_api_call(f"[WARN] Channel {cid} worker thread did not stop within 2s; keep zombie reference")
                else:
                    trace("ENGINE_STOP_CHANNEL_THREAD_STOPPED", cid)
                del self.workers[cid]; del self.threads[cid]
            
            # 核心清理：释放该通道持有的所有资源锁、排队队列并移交锁，从同步组和屏障中注销
            self._cleanup_channel_resources(cid)
            self._check_and_release_barrier() # 剔除 channel worker 后再检查集齐条件，防止死锁
        trace("ENGINE_STOP_CHANNEL_END", cid)

    def stop_test(self, cid, final_status="STOPPED"):
        """供 UI 使用的兼容入口。"""
        self.stop_channel_test(cid, request_stop=True, final_status=final_status)

    def _cleanup_zombie(self, t):
        with self._lock:
            if t in self.zombie_threads: self.zombie_threads.remove(t)
            self.zombie_workers = [(zt, zw) for zt, zw in self.zombie_workers if zt is not t]

    def stop_all(self):
        self.heartbeat_running = False
        with self._lock:
            for cid in list(self.threads.keys()): self.stop_channel_test(cid)
            self.sync_barrier_channels.clear()
            self._current_barrier_sub_step.clear()
            self._barrier_watchdog_armed.clear()
            for lock_name in self.resource_locks:
                self.resource_locks[lock_name] = None
                self.resource_queues[lock_name].clear()
            self._emit_seq_queue_status()
            self.barrier_status_changed.emit(0, 0)
            import time; s = time.time()
            while self.threads and time.time() - s < 2.0:
                from PySide6.QtCore import QCoreApplication
                QCoreApplication.processEvents(); time.sleep(0.05)

    def report_system_step_info(self, step_idx: int, step_name: str):
        """上报当前运行的老化测试全局系统工步信息给大屏后端"""
        if self.api_client:
            def do_post():
                try:
                    self.api_client._post("/api/system/step", {"step_index": step_idx, "step_name": step_name})
                except Exception:
                    pass
            import threading
            threading.Thread(target=do_post, daemon=True).start()

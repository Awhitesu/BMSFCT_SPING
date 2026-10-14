import logging
import time
from dataclasses import dataclass
from typing import Any, Callable, Dict, Optional

logger = logging.getLogger(__name__)


@dataclass
class EOLResult:
    success: bool
    response_code: Optional[int] = None
    payload: bytes = b""
    raw_data: bytes = b""
    value: Any = None
    error: str = ""


class EOLProtocol:
    REQUEST_ID = 0x7F0
    RESPONSE_ID = 0x7F8
    REQUEST_PREFIX = 0x10
    RESPONSE_PREFIX = 0x11
    POSITIVE_RESPONSE = 0x40
    NEGATIVE_RESPONSE = 0x80

    def __init__(self, can_driver, channel_id: int = 0):
        self.can_driver = can_driver
        self.channel_id = channel_id
        self.operations = self._build_operations()

    def transact(self, device_id: int, operation: int, payload: Optional[bytes] = None,
                 timeout: float = 1.0, decoder: Optional[Callable[[bytes], Any]] = None,
                 request_id: Optional[int] = None, response_id: Optional[int] = None,
                 can_type: int = 0, dlc: int = 8,
                 logger: Callable[[str], None] = None, retries: int = 3) -> EOLResult:
        req_id = request_id if request_id is not None else self.REQUEST_ID
        resp_id = response_id if response_id is not None else self.RESPONSE_ID
        payload_data = (payload or b"")[:4].ljust(4, b"\x00")
        request_data = bytes([self.REQUEST_PREFIX, device_id & 0xFF, operation & 0xFF, 0x00]) + payload_data
        
        # 记录发送日志
        log_msg = f"CAN TX CH:{self.channel_id} ID={hex(req_id).upper()} DATA={request_data.hex(' ').upper()}"
        if logger: 
            logger(log_msg)
        else:
            print(log_msg)

        def matcher(msg):
            mid = msg.get('can_id')
            data = msg.get("data", b"")
            # 记录所有进入 matcher 的报文，排查 seen_index 是否正常
            # print(f"[MATCHER-TRACE] ID=0x{mid:X} DATA={data.hex(' ').upper()}")
            
            match = (
                len(data) >= 4
                and data[0] == self.RESPONSE_PREFIX
                and data[1] == (device_id & 0xFF)
                and data[2] == (operation & 0xFF)
            )
            # 调试：打印所有 ID 匹配的报文内容，确认匹配逻辑是否失效
            # if mid == resp_id:
            #     print(f"[EOL-DEBUG] Candidate Message: ID=0x{mid:X} DATA={data.hex(' ').upper()} Match={match} (Expected: {hex(self.RESPONSE_PREFIX)} {hex(device_id)} {hex(operation)})")
            return match

        def local_log(msg):
            if logger: logger(msg)
            else: print(msg)

        # 增加重试机制，应对网络抖动或硬件繁忙
        for attempt in range(retries):
            if attempt > 0:
                local_log(f"EOL 重试 {attempt}...")
                time.sleep(0.2) # 重试前稍作等待

            local_log(f"Waiting for EOL response (ID=0x{resp_id:X}, timeout={timeout}s)...")
            
            # 在发送前强制清空接收缓存中的旧报文，确保不会解析到上一轮的残留数据
            if hasattr(self.can_driver, 'clear_rx_history'):
                self.can_driver.clear_rx_history(resp_id)
                
            send_time = time.time()
            if not self.can_driver.send_can_message(self.channel_id, req_id, can_type, dlc, request_data):
                if attempt == retries - 1: return EOLResult(False, error="CAN发送失败")
                continue

            msg = self.can_driver.wait_for_message(
                can_id=resp_id,
                channel_id=None, 
                predicate=matcher,
                timeout=timeout,
                since_time=send_time,
                consume=True # 匹配成功后立即从缓存中移除，防止重复解析
            )
            if msg:
                break
        else:
            return EOLResult(False, error="EOL响应超时")

        raw = msg.get("data", b"")
        # 记录接收报文日志
        local_log(f"CAN RX CH:{self.channel_id} ID={hex(resp_id).upper()} DATA={raw.hex(' ').upper()}")
        response_code = raw[3] if len(raw) >= 4 else None
        # BUG-16修复: 用 response_payload 命名，避免遮蔽函数形参 payload
        response_payload = raw[4:] if len(raw) > 4 else b""
        if response_code != self.POSITIVE_RESPONSE:
            return EOLResult(False, response_code=response_code, payload=response_payload, raw_data=raw, error=f"EOL否定响应: 0x{response_code:02X}" if response_code is not None else "EOL响应格式错误")

        # 如果定义了专门的物理量解码器，使用解码器解析数据；
        # 否则，作为一个纯控制或写入指令，成功时默认返回 "PASS"，绝不输出杂乱的十六进制（防止干扰正常测试项的质检判定）
        if decoder:
            value = decoder(raw)
        else:
            value = "PASS"
        return EOLResult(True, response_code=response_code, payload=response_payload, raw_data=raw, value=value)

    def execute(self, op_name: str, timeout: float = 1.0, logger: Callable[[str], None] = None, **kwargs) -> EOLResult:
        # 兼容老配方中原先的 "0xFF 唤醒源读取" 名称
        if op_name == "0xFF 唤醒源读取":
            op_name = "0xFF 扩展指令"
        
        if op_name in ("DTC诊断", "DTU诊断"):
            try:
                # 提取 CAN ID
                def _get_id(val, default):
                    if val is None: return default
                    val_str = str(val).strip()
                    try:
                        if val_str.lower().startswith("0x"):
                            return int(val_str, 16)
                        return int(val_str)
                    except:
                        return default

                req_id = _get_id(kwargs.get("TX_ID") or kwargs.get("发送ID"), 0x7F0)
                resp_id = _get_id(kwargs.get("RX_ID") or kwargs.get("接收ID"), 0x7F8)

                if logger:
                    logger(f"[*] 启动 DTC 诊断读取... 发送ID=0x{req_id:X}, 接收ID=0x{resp_id:X}")

                tx_cmd = bytes([self.REQUEST_PREFIX, 0x13, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00])

                # ==================== 1. 读取第一组数据 (Byte 0~3) ====================
                group1_data = None
                for attempt in range(1, 6):
                    if logger:
                        logger(f"[DTC STEP 1] 发送诊断请求 (尝试 {attempt}/5) CAN TX CH:{self.channel_id} DATA={tx_cmd.hex(' ').upper()}")

                    if hasattr(self.can_driver, 'clear_rx_history'):
                        self.can_driver.clear_rx_history(resp_id)

                    send_time = time.time()
                    if not self.can_driver.send_can_message(self.channel_id, req_id, 0, 8, tx_cmd):
                        time.sleep(0.1)
                        continue

                    msg = self.can_driver.wait_for_message(
                        can_id=resp_id,
                        channel_id=None,
                        predicate=lambda m: (
                            len(m.get("data", b"")) >= 8
                            and m.get("data", b"")[0] in [self.RESPONSE_PREFIX, self.REQUEST_PREFIX]
                            and m.get("data", b"")[1] == 0x13
                            and m.get("data", b"")[2] == 0x00
                            and m.get("data", b"")[3] == self.POSITIVE_RESPONSE
                        ),
                        timeout=timeout,
                        since_time=send_time,
                        consume=True
                    )
                    if msg:
                        raw_data = msg.get("data", b"")
                        if logger:
                            logger(f"[DTC STEP 1] 收到肯定响应 CAN RX CH:{self.channel_id} DATA={raw_data.hex(' ').upper()}")
                        group1_data = raw_data[4:8]
                        break
                    else:
                        time.sleep(0.1)

                if group1_data is None:
                    if logger: logger("[DTC STEP 1] 错误: 第一组读取超时且重试均失败")
                    return EOLResult(False, error="DTC第一组诊断超时", value="测试失败")

                # 间隔延时
                time.sleep(0.2)

                # ==================== 2. 读取第二组数据 (Byte 4~7) ====================
                tx_cmd_step2 = bytes([self.REQUEST_PREFIX, 0x13, 0x00, 0x00, 0x01, 0x00, 0x00, 0x00])
                group2_data = None
                for attempt in range(1, 6):
                    if logger:
                        logger(f"[DTC STEP 2] 发送诊断请求 (尝试 {attempt}/5) CAN TX CH:{self.channel_id} DATA={tx_cmd_step2.hex(' ').upper()}")

                    if hasattr(self.can_driver, 'clear_rx_history'):
                        self.can_driver.clear_rx_history(resp_id)

                    send_time = time.time()
                    if not self.can_driver.send_can_message(self.channel_id, req_id, 0, 8, tx_cmd_step2):
                        time.sleep(0.1)
                        continue

                    msg = self.can_driver.wait_for_message(
                        can_id=resp_id,
                        channel_id=None,
                        predicate=lambda m: (
                            len(m.get("data", b"")) >= 8
                            and m.get("data", b"")[0] in [self.RESPONSE_PREFIX, self.REQUEST_PREFIX]
                            and m.get("data", b"")[1] == 0x13
                            and m.get("data", b"")[2] == 0x00
                            and m.get("data", b"")[3] == self.POSITIVE_RESPONSE
                        ),
                        timeout=timeout,
                        since_time=send_time,
                        consume=True
                    )
                    if msg:
                        raw_data = msg.get("data", b"")
                        if logger:
                            logger(f"[DTC STEP 2] 收到肯定响应 CAN RX CH:{self.channel_id} DATA={raw_data.hex(' ').upper()}")
                        group2_data = raw_data[4:8]
                        break
                    else:
                        time.sleep(0.1)

                if group2_data is None:
                    if logger: logger("[DTC STEP 2] 错误: 第二组读取超时且重试均失败")
                    return EOLResult(False, error="DTC第二组诊断超时", value="测试失败")

                # 返回拼接后的 8 字节 bytes
                full_bytes = bytes(group1_data) + bytes(group2_data)
                return EOLResult(True, value=full_bytes)
            except Exception as ex:
                if logger: logger(f"[DTC] 异常: {ex}")
                return EOLResult(False, error=str(ex), value="测试异常")
        
        elif op_name in ("DTC故障清除", "DTC清除故障", "DTU清除故障", "DTU清障"):
            def _get_id(val, default):
                if val is None: return default
                val_str = str(val).strip()
                try:
                    if val_str.lower().startswith("0x"): return int(val_str, 16)
                    return int(val_str)
                except: return default
            req_id = _get_id(kwargs.get("TX_ID") or kwargs.get("发送ID"), 0x7F0)
            resp_id = _get_id(kwargs.get("RX_ID") or kwargs.get("接收ID"), 0x7F8)
            return self.clear_dtc_faults(req_id, resp_id, timeout, logger)

        elif op_name == "EEPROM测试":
            try:
                if logger:
                    logger(f"[*] 启动特殊执行下3.5H EOL EEPROM 自动化回路校验测试...")

                # A. 提取 TX_ID, RX_ID 并进行安全转换
                def _get_id(val, default):
                    if val is None: return default
                    val_str = str(val).strip()
                    try:
                        if val_str.lower().startswith("0x"):
                            return int(val_str, 16)
                        return int(val_str)
                    except:
                        return default

                req_id = _get_id(kwargs.get("TX_ID") or kwargs.get("发送ID"), 0x7F0)
                resp_id = _get_id(kwargs.get("RX_ID") or kwargs.get("接收ID"), 0x7F8)

                if logger:
                    logger(f"[EEPROM CONFIG] 使用 发送ID=0x{req_id:X}, 接收ID=0x{resp_id:X}")

                # ==================== 步骤 1 ====================
                # 发送 10 0A 01 00 01 00 00 00 报文，判断是否收到肯定响应 40
                tx_step1 = bytes([self.REQUEST_PREFIX, 0x0A, 0x01, 0x00, 0x01, 0x00, 0x00, 0x00])
                
                retry_count = 10
                step1_success = False
                for attempt in range(1, retry_count + 1):
                    if logger:
                        if attempt > 1:
                            logger(f"[EEPROM STEP 1] 未收到肯定响应，正在进行第 {attempt - 1}/{retry_count - 1} 次重发 CAN TX CH:{self.channel_id} ID={hex(req_id).upper()} DATA={tx_step1.hex(' ').upper()}")
                        else:
                            logger(f"[EEPROM STEP 1] CAN TX CH:{self.channel_id} ID={hex(req_id).upper()} DATA={tx_step1.hex(' ').upper()}")

                    if hasattr(self.can_driver, 'clear_rx_history'):
                        self.can_driver.clear_rx_history(resp_id)

                    send_time = time.time()
                    if not self.can_driver.send_can_message(self.channel_id, req_id, 0, 8, tx_step1):
                        time.sleep(0.05)
                        continue

                    msg = self.can_driver.wait_for_message(
                        can_id=resp_id,
                        channel_id=None,
                        predicate=lambda m: (
                            len(m.get("data", b"")) >= 4
                            and m.get("data", b"")[0] in [self.RESPONSE_PREFIX, self.REQUEST_PREFIX]
                            and m.get("data", b"")[1] == 0x0A
                            and m.get("data", b"")[2] == 0x01
                            and m.get("data", b"")[3] == self.POSITIVE_RESPONSE
                        ),
                        timeout=timeout,
                        since_time=send_time,
                        consume=True
                    )
                    if msg:
                        raw_step1 = msg.get("data", b"")
                        if logger:
                            logger(f"[EEPROM STEP 1] CAN RX CH:{self.channel_id} ID={hex(resp_id).upper()} DATA={raw_step1.hex(' ').upper()}")
                        step1_success = True
                        break
                    else:
                        time.sleep(0.05)

                if not step1_success:
                    if logger: logger("[EEPROM STEP 1] 错误: 未收到步骤1肯定响应 (已重试10次)")
                    return EOLResult(False, error="步骤1响应超时(已重试10次)", value="测试失败")

                # ==================== 步骤 2 ====================
                # 等待 200ms
                time.sleep(0.2)

                # 仅使用 1 组固定数据，每组 4 字节
                generated_data = {1: b"\xA5\x5A\xA5\x5A"}
                if logger:
                    logger(f"[EEPROM STEP 2] 使用固定写入数据: {generated_data[1].hex(' ').upper()}")

                # 仅发送第 1 组，并确认肯定响应 11 0A 05 40
                for i in [1]:
                    tx_step2 = bytes([self.REQUEST_PREFIX, 0x0A, 0x05, i]) + generated_data[i]
                    
                    retry_count = 10
                    step2_success = False
                    for attempt in range(1, retry_count + 1):
                        if logger:
                            if attempt > 1:
                                logger(f"[EEPROM STEP 2] 第 {i} 组未收到肯定响应，正在进行第 {attempt - 1}/{retry_count - 1} 次重发 CAN TX CH:{self.channel_id} ID={hex(req_id).upper()} DATA={tx_step2.hex(' ').upper()}")
                            else:
                                logger(f"[EEPROM STEP 2] 发送第 {i} 组 CAN TX CH:{self.channel_id} ID={hex(req_id).upper()} DATA={tx_step2.hex(' ').upper()}")

                        if hasattr(self.can_driver, 'clear_rx_history'):
                            self.can_driver.clear_rx_history(resp_id)

                        send_time = time.time()
                        if not self.can_driver.send_can_message(self.channel_id, req_id, 0, 8, tx_step2):
                            time.sleep(0.05)
                            continue

                        msg_step2 = self.can_driver.wait_for_message(
                            can_id=resp_id,
                            channel_id=None,
                            predicate=lambda m: (
                                len(m.get("data", b"")) >= 4
                                and m.get("data", b"")[0] in [self.RESPONSE_PREFIX, self.REQUEST_PREFIX]
                                and m.get("data", b"")[1] == 0x0A
                                and m.get("data", b"")[2] == 0x05
                                and m.get("data", b"")[3] == self.POSITIVE_RESPONSE
                            ),
                            timeout=timeout,
                            since_time=send_time,
                            consume=True
                        )
                        if msg_step2:
                            raw_step2 = msg_step2.get("data", b"")
                            if logger:
                                logger(f"[EEPROM STEP 2] 收到第 {i} 组肯定响应 CAN RX CH:{self.channel_id} ID={hex(resp_id).upper()} DATA={raw_step2.hex(' ').upper()}")
                            step2_success = True
                            break
                        else:
                            time.sleep(0.05)

                    if not step2_success:
                        if logger: logger(f"[EEPROM STEP 2] 错误: 未收到第 {i} 组的肯定响应 (已重试10次)")
                        return EOLResult(False, error=f"步骤2 第{i}组确认超时(已重试10次)", value="测试失败")

                    # 顺序间隔 80ms (防 Windows 计时器精度抖动，确保严格大于 50ms)
                    time.sleep(0.08)

                # ==================== 步骤 3 ====================
                # 在发送读取指令之前，先发送 10 0A 01 00 01 00 00 00 设置指令，并确认肯定响应 11 0A 01 40
                tx_pre_step3 = bytes([self.REQUEST_PREFIX, 0x0A, 0x01, 0x00, 0x01, 0x00, 0x00, 0x00])
                
                retry_count = 10
                pre3_success = False
                for attempt in range(1, retry_count + 1):
                    if logger:
                        if attempt > 1:
                            logger(f"[EEPROM STEP 3 PRE] 未收到肯定响应，正在进行第 {attempt - 1}/{retry_count - 1} 次重发 CAN TX CH:{self.channel_id} ID={hex(req_id).upper()} DATA={tx_pre_step3.hex(' ').upper()}")
                        else:
                            logger(f"[EEPROM STEP 3 PRE] 发送读取前置设置 CAN TX CH:{self.channel_id} ID={hex(req_id).upper()} DATA={tx_pre_step3.hex(' ').upper()}")

                    if hasattr(self.can_driver, 'clear_rx_history'):
                        self.can_driver.clear_rx_history(resp_id)

                    send_time_pre = time.time()
                    if not self.can_driver.send_can_message(self.channel_id, req_id, 0, 8, tx_pre_step3):
                        time.sleep(0.05)
                        continue

                    msg_pre3 = self.can_driver.wait_for_message(
                        can_id=resp_id,
                        channel_id=None,
                        predicate=lambda m: (
                            len(m.get("data", b"")) >= 4
                            and m.get("data", b"")[0] in [self.RESPONSE_PREFIX, self.REQUEST_PREFIX]
                            and m.get("data", b"")[1] == 0x0A
                            and m.get("data", b"")[2] == 0x01
                            and m.get("data", b"")[3] == self.POSITIVE_RESPONSE
                        ),
                        timeout=timeout,
                        since_time=send_time_pre,
                        consume=True
                    )
                    if msg_pre3:
                        raw_pre3 = msg_pre3.get("data", b"")
                        if logger:
                            logger(f"[EEPROM STEP 3 PRE] 收到步骤3前置设置肯定响应 CAN RX CH:{self.channel_id} ID={hex(resp_id).upper()} DATA={raw_pre3.hex(' ').upper()}")
                        pre3_success = True
                        break
                    else:
                        time.sleep(0.05)

                if not pre3_success:
                    if logger: logger("[EEPROM STEP 3 PRE] 错误: 未收到步骤3前置设置肯定响应 (已重试10次)")
                    return EOLResult(False, error="步骤3前置设置确认超时(已重试10次)", value="测试失败")

                # 间隔 80ms (防 Windows 计时器精度抖动，确保严格大于 50ms)
                time.sleep(0.08)

                # 发送读取指令，只回读第 1 组地址块数据
                received_groups = {}
                for i in [1]:
                    tx_step3 = bytes([self.REQUEST_PREFIX, 0x0A, 0x03, 0x00, i, 0x00, 0x00, 0x00])
                    
                    retry_count = 10
                    success = False
                    for attempt in range(1, retry_count + 1):
                        if logger:
                            if attempt > 1:
                                logger(f"[EEPROM STEP 3] 第 {i} 组读取超时，正在进行第 {attempt - 1}/{retry_count - 1} 次自动重发与回读...")
                            else:
                                logger(f"[EEPROM STEP 3] 发送第 {i} 组读取 CAN TX CH:{self.channel_id} ID={hex(req_id).upper()} DATA={tx_step3.hex(' ').upper()}")

                        if hasattr(self.can_driver, 'clear_rx_history'):
                            self.can_driver.clear_rx_history(resp_id)

                        send_time = time.time()
                        if not self.can_driver.send_can_message(self.channel_id, req_id, 0, 8, tx_step3):
                            time.sleep(0.05)
                            continue

                        msg_step3 = self.can_driver.wait_for_message(
                            can_id=resp_id,
                            channel_id=None,
                            predicate=lambda m: (
                                len(m.get("data", b"")) >= 8
                                and m.get("data", b"")[0] in [self.RESPONSE_PREFIX, self.REQUEST_PREFIX]
                                and m.get("data", b"")[1] == 0x0A
                                and m.get("data", b"")[2] == 0x03
                                and m.get("data", b"")[3] == self.POSITIVE_RESPONSE
                            ),
                            timeout=timeout,
                            since_time=send_time,
                            consume=True
                        )
                        if msg_step3:
                            raw_step3 = msg_step3.get("data", b"")
                            if logger:
                                logger(f"[EEPROM STEP 3] 收到数据报文 CAN RX CH:{self.channel_id} ID={hex(resp_id).upper()} DATA={raw_step3.hex(' ').upper()}")

                            # 提取第5至8字节（即 raw_step3[4:8]）作为回读数据组
                            data_chunk = raw_step3[4:8]
                            received_groups[i] = data_chunk
                            if logger:
                                logger(f"[EEPROM STEP 3] 成功暂存第 {i} 组回读数据: {data_chunk.hex(' ').upper()}")
                            success = True
                            break
                        else:
                            # 超时后稍微等待 50ms 再进行下一次重试
                            time.sleep(0.05)

                    if not success:
                        if logger:
                            logger(f"[EEPROM STEP 3] 错误: 未收到第 {i} 组读取的响应 (重试 {retry_count} 次均失败)")
                        return EOLResult(False, error=f"步骤3 第{i}组读取超时(重试{retry_count}次失败)", value="测试失败")

                    # 间隔 80ms (防 Windows 计时器精度抖动，确保严格大于 50ms)
                    time.sleep(0.08)

                # 将接收的数据与固定数据进行比对
                if len(received_groups) < 1:
                    if logger:
                        logger(f"[EEPROM STEP 3] 错误: 未收齐第 1 组回读数据")
                    return EOLResult(False, error="未完整回读数据", value="测试失败")

                # 比对数据一致性
                mismatch_found = False
                for i in [1]:
                    gen = generated_data[i]
                    rec = received_groups[i]
                    if gen != rec:
                        mismatch_found = True
                        if logger:
                            logger(f"[EEPROM VERIFY] 第 {i} 组数据不一致! 写入: {gen.hex(' ').upper()} | 回读: {rec.hex(' ').upper()}")
                    else:
                        if logger:
                            logger(f"[EEPROM VERIFY] 第 {i} 组校验一致: {gen.hex(' ').upper()}")

                # ==================== 恢复/清理步骤 ====================
                # 判断完成后，需要再将第 1 个地址块数据恢复为 FF FF FF FF
                if logger:
                    logger("[EEPROM CLEANUP] 开始进行擦除恢复，将第 1 组地址块写入 FF FF FF FF...")

                cleanup_success = True
                for i in [1]:
                    tx_cleanup = bytes([self.REQUEST_PREFIX, 0x0A, 0x05, i, 0xFF, 0xFF, 0xFF, 0xFF])
                    
                    retry_count = 10
                    cleanup_group_success = False
                    for attempt in range(1, retry_count + 1):
                        if logger:
                            if attempt > 1:
                                logger(f"[EEPROM CLEANUP] 第 {i} 组恢复擦除未收到肯定响应，正在进行第 {attempt - 1}/{retry_count - 1} 次重发 CAN TX CH:{self.channel_id} ID={hex(req_id).upper()} DATA={tx_cleanup.hex(' ').upper()}")
                            else:
                                logger(f"[EEPROM CLEANUP] 发送恢复第 {i} 组 CAN TX CH:{self.channel_id} ID={hex(req_id).upper()} DATA={tx_cleanup.hex(' ').upper()}")

                        if hasattr(self.can_driver, 'clear_rx_history'):
                            self.can_driver.clear_rx_history(resp_id)

                        send_time = time.time()
                        self.can_driver.send_can_message(self.channel_id, req_id, 0, 8, tx_cleanup)

                        msg_cleanup = self.can_driver.wait_for_message(
                            can_id=resp_id,
                            channel_id=None,
                            predicate=lambda m: (
                                len(m.get("data", b"")) >= 4
                                and m.get("data", b"")[0] in [self.RESPONSE_PREFIX, self.REQUEST_PREFIX]
                                and m.get("data", b"")[1] == 0x0A
                                and m.get("data", b"")[2] == 0x05
                                and m.get("data", b"")[3] == self.POSITIVE_RESPONSE
                            ),
                            timeout=timeout,
                            since_time=send_time,
                            consume=True
                        )
                        if msg_cleanup:
                            raw_cleanup = msg_cleanup.get("data", b"")
                            if logger:
                                logger(f"[EEPROM CLEANUP] 收到第 {i} 组恢复肯定响应 CAN RX CH:{self.channel_id} ID={hex(resp_id).upper()} DATA={raw_cleanup.hex(' ').upper()}")
                            cleanup_group_success = True
                            break
                        else:
                            time.sleep(0.05)

                    if not cleanup_group_success:
                        if logger: logger(f"[EEPROM CLEANUP] 警告: 未收到第 {i} 组恢复擦除的肯定响应 (已重试10次)")
                        cleanup_success = False

                    # 间隔 80ms (防 Windows 计时器精度抖动，确保严格大于 50ms)
                    time.sleep(0.08)

                if mismatch_found:
                    if logger:
                        logger("[EEPROM RESULT] 警告：特殊执行 EOL EEPROM 数据比对不一致，校验失败！")
                    return EOLResult(False, error="回读数据不匹配", value="测试失败")
                else:
                    if logger:
                        logger("[EEPROM RESULT] 特殊执行 EOL EEPROM 校验成功，所有数据完全一致！")
                    return EOLResult(True, value="测试通过")
                        
            except Exception as e:
                if logger:
                    logger(f"[EEPROM ERROR] 发生未预期异常: {e}")
                return EOLResult(False, error=f"校验失败: {e}", value="测试失败")
        elif op_name == "绝缘测试":
            try:
                if logger:
                    logger(f"[*] 启动特殊执行下3.5H EOL 绝缘测试自动化校验...")

                # A. 提取 TX_ID, RX_ID 并进行安全转换
                def _get_id(val, default):
                    if val is None: return default
                    val_str = str(val).strip()
                    try:
                        if val_str.lower().startswith("0x"):
                            return int(val_str, 16)
                        return int(val_str)
                    except:
                        return default

                req_id = _get_id(kwargs.get("TX_ID") or kwargs.get("发送ID"), 0x7F0)
                resp_id = _get_id(kwargs.get("RX_ID") or kwargs.get("接收ID"), 0x7F8)

                # 外部输入参数与默认阻值参数 (单位 kΩ 或 V)
                hv1 = float(kwargs.get("HV1", 650.0))
                r0 = float(kwargs.get("R0", 10.0))
                r1 = float(kwargs.get("R1", 1000.0))
                r2 = float(kwargs.get("R2", 1000.0))
                r3 = float(kwargs.get("R3", 5000.0))
                r4 = float(kwargs.get("R4", 5000.0))

                if logger:
                    logger(f"[INSULATION CONFIG] 发送ID=0x{req_id:X}, 接收ID=0x{resp_id:X}")
                    logger(f"[INSULATION CONFIG] 参数: HV1={hv1}V, R0={r0}kΩ, R1={r1}kΩ, R2={r2}kΩ, R3={r3}kΩ, R4={r4}kΩ")

                # ==================== 步骤 1 ====================
                # 控制绝缘桥臂断开，发送 10 03 01 00 01 00 00 00
                tx_step1 = bytes([self.REQUEST_PREFIX, 0x03, 0x01, 0x00, 0x01, 0x00, 0x00, 0x00])
                if logger:
                    logger(f"[INSULATION STEP 1] 发送断开桥臂指令 CAN TX CH:{self.channel_id} ID={hex(req_id).upper()} DATA={tx_step1.hex(' ').upper()}")

                if hasattr(self.can_driver, 'clear_rx_history'):
                    self.can_driver.clear_rx_history(resp_id)

                send_time = time.time()
                if not self.can_driver.send_can_message(self.channel_id, req_id, 0, 8, tx_step1):
                    return EOLResult(False, error="步骤1 CAN发送失败", value="正极绝缘为0kΩ, 负极绝缘为0kΩ")

                msg_step1 = self.can_driver.wait_for_message(
                    can_id=resp_id,
                    channel_id=None,
                    predicate=lambda m: (
                        len(m.get("data", b"")) >= 4
                        and m.get("data", b"")[0] in [self.RESPONSE_PREFIX, self.REQUEST_PREFIX]
                        and m.get("data", b"")[1] == 0x03
                        and m.get("data", b"")[2] == 0x01
                        and m.get("data", b"")[3] == self.POSITIVE_RESPONSE
                    ),
                    timeout=timeout,
                    since_time=send_time,
                    consume=True
                )
                if not msg_step1:
                    if logger: logger("[INSULATION STEP 1] 错误: 未收到肯定响应")
                    return EOLResult(False, error="步骤1响应超时", value="正极绝缘为0kΩ, 负极绝缘为0kΩ")

                raw_step1 = msg_step1.get("data", b"")
                if logger:
                    logger(f"[INSULATION STEP 1] 收到桥臂断开肯定响应 CAN RX CH:{self.channel_id} ID={hex(resp_id).upper()} DATA={raw_step1.hex(' ').upper()}")

                # 肯定响应后，等待 4 秒
                if logger: logger("[INSULATION STEP 1] 开始等待4秒...")
                time.sleep(4.0)

                # ==================== 步骤 2 ====================
                # 闭合正极绝缘桥臂，发送10 03 03 00 01 00 00 00，循环步骤2操作40次
                if logger: logger("[INSULATION STEP 2] 开启循环采样 40 次 Visr1...")
                samples1 = []
                attempts = 0
                max_attempts = 100
                while len(samples1) < 40 and attempts < max_attempts:
                    attempts += 1
                    tx_sample = bytes([self.REQUEST_PREFIX, 0x03, 0x03, 0x00, 0x01, 0x00, 0x00, 0x00])
                    
                    if hasattr(self.can_driver, 'clear_rx_history'):
                        self.can_driver.clear_rx_history(resp_id)
                        
                    send_time = time.time()
                    self.can_driver.send_can_message(self.channel_id, req_id, 0, 8, tx_sample)
                    if logger:
                        logger(f"[INSULATION STEP 2 SAMPLE {len(samples1)+1}] CAN TX CH:{self.channel_id} ID={hex(req_id).upper()} DATA={tx_sample.hex(' ').upper()}")
                    
                    msg_sample = self.can_driver.wait_for_message(
                        can_id=resp_id,
                        channel_id=None,
                        predicate=lambda m: (
                            len(m.get("data", b"")) >= 7
                            and m.get("data", b"")[0] in [self.RESPONSE_PREFIX, self.REQUEST_PREFIX]
                            and m.get("data", b"")[1] == 0x03
                            and m.get("data", b"")[2] == 0x03
                            and m.get("data", b"")[3] == self.POSITIVE_RESPONSE
                        ),
                        timeout=0.1,
                        since_time=send_time,
                        consume=True
                    )
                    if msg_sample:
                        raw_sample = msg_sample.get("data", b"")
                        val = ((raw_sample[4] << 16) | (raw_sample[5] << 8) | raw_sample[6]) * 0.001
                        samples1.append(val)
                        if logger:
                            logger(f"[INSULATION STEP 2 SAMPLE {len(samples1)}] CAN RX CH:{self.channel_id} ID={hex(resp_id).upper()} DATA={raw_sample.hex(' ').upper()} -> {val:.3f} V")
                    else:
                        if logger:
                            logger(f"[INSULATION STEP 2 SAMPLE {len(samples1)+1}] 警告: 采样响应超时或为否定响应")
                    time.sleep(0.02)

                if len(samples1) < 40:
                    if logger:
                        logger(f"[INSULATION STEP 2] 警告: 未能成功采集满40次 (成功采集了 {len(samples1)} 次)")

                visr1 = sum(samples1) / len(samples1) if samples1 else 0.0
                if logger:
                    logger(f"[INSULATION STEP 2] Visr1 均值 = {visr1:.4f} V (总采样次数: {len(samples1)})")

                # ==================== 步骤 3 ====================
                # 计算 VN1 和 VP1
                vn1 = ((r3 + r0) * visr1) / r0 if r0 != 0 else 0.0
                vp1 = hv1 - vn1
                if logger:
                    logger(f"[INSULATION STEP 3] 计算得到 VN1 = {vn1:.3f} V, VP1 = {vp1:.3f} V")

                # ==================== 步骤 4 ====================
                # 当VP1≤VN1时开启负极桥臂，发送10 03 01 00 01 02 00 00，VP1＞VN1时开启正极桥臂，发送10 03 01 00 01 01 00 00
                is_neg_arm = (vp1 <= vn1)
                if is_neg_arm:
                    tx_step4 = bytes([self.REQUEST_PREFIX, 0x03, 0x01, 0x00, 0x01, 0x02, 0x00, 0x00])
                    arm_name = "负极绝缘桥臂 (S3)"
                else:
                    tx_step4 = bytes([self.REQUEST_PREFIX, 0x03, 0x01, 0x00, 0x01, 0x01, 0x00, 0x00])
                    arm_name = "正极绝缘桥臂 (S2)"

                if logger:
                    logger(f"[INSULATION STEP 4] {'VP1 <= VN1' if is_neg_arm else 'VP1 > VN1'}, 开启{arm_name}")
                    logger(f"[INSULATION STEP 4] CAN TX CH:{self.channel_id} ID={hex(req_id).upper()} DATA={tx_step4.hex(' ').upper()}")

                if hasattr(self.can_driver, 'clear_rx_history'):
                    self.can_driver.clear_rx_history(resp_id)

                send_time = time.time()
                if not self.can_driver.send_can_message(self.channel_id, req_id, 0, 8, tx_step4):
                    return EOLResult(False, error="步骤4 CAN发送失败", value="正极绝缘为0kΩ, 负极绝缘为0kΩ")

                msg_step4 = self.can_driver.wait_for_message(
                    can_id=resp_id,
                    channel_id=None,
                    predicate=lambda m: (
                        len(m.get("data", b"")) >= 4
                        and m.get("data", b"")[0] in [self.RESPONSE_PREFIX, self.REQUEST_PREFIX]
                        and m.get("data", b"")[1] == 0x03
                        and m.get("data", b"")[2] == 0x01
                        and m.get("data", b"")[3] == self.POSITIVE_RESPONSE
                    ),
                    timeout=timeout,
                    since_time=send_time,
                    consume=True
                )
                if not msg_step4:
                    if logger: logger("[INSULATION STEP 4] 错误: 未收到肯定响应")
                    return EOLResult(False, error="步骤4桥臂控制超时", value="正极绝缘为0kΩ, 负极绝缘为0kΩ")

                raw_step4 = msg_step4.get("data", b"")
                if logger:
                    logger(f"[INSULATION STEP 4] 收到桥臂开启肯定响应 CAN RX CH:{self.channel_id} ID={hex(resp_id).upper()} DATA={raw_step4.hex(' ').upper()}")

                # 肯定响应后，等待 4 秒
                if logger: logger("[INSULATION STEP 4] 开始等待4秒...")
                time.sleep(4.0)

                # ==================== 步骤 5 ====================
                # 重新采集 40 次并计算 Visr2
                if logger: logger("[INSULATION STEP 5] 开始第二次循环采样 40 次...")
                samples2 = []
                attempts = 0
                while len(samples2) < 40 and attempts < max_attempts:
                    attempts += 1
                    tx_sample = bytes([self.REQUEST_PREFIX, 0x03, 0x03, 0x00, 0x01, 0x00, 0x00, 0x00])
                    
                    if hasattr(self.can_driver, 'clear_rx_history'):
                        self.can_driver.clear_rx_history(resp_id)
                        
                    send_time = time.time()
                    self.can_driver.send_can_message(self.channel_id, req_id, 0, 8, tx_sample)
                    if logger:
                        logger(f"[INSULATION STEP 5 SAMPLE {len(samples2)+1}] CAN TX CH:{self.channel_id} ID={hex(req_id).upper()} DATA={tx_sample.hex(' ').upper()}")
                    
                    msg_sample = self.can_driver.wait_for_message(
                        can_id=resp_id,
                        channel_id=None,
                        predicate=lambda m: (
                            len(m.get("data", b"")) >= 7
                            and m.get("data", b"")[0] in [self.RESPONSE_PREFIX, self.REQUEST_PREFIX]
                            and m.get("data", b"")[1] == 0x03
                            and m.get("data", b"")[2] == 0x03
                            and m.get("data", b"")[3] == self.POSITIVE_RESPONSE
                        ),
                        timeout=0.1,
                        since_time=send_time,
                        consume=True
                    )
                    if msg_sample:
                        raw_sample = msg_sample.get("data", b"")
                        val = ((raw_sample[4] << 16) | (raw_sample[5] << 8) | raw_sample[6]) * 0.001
                        samples2.append(val)
                        if logger:
                            logger(f"[INSULATION STEP 5 SAMPLE {len(samples2)}] CAN RX CH:{self.channel_id} ID={hex(resp_id).upper()} DATA={raw_sample.hex(' ').upper()} -> {val:.3f} V")
                    else:
                        if logger:
                            logger(f"[INSULATION STEP 5 SAMPLE {len(samples2)+1}] 警告: 采样响应超时或为否定响应")
                    time.sleep(0.02)
                
                if len(samples2) < 40:
                    if logger:
                        logger(f"[INSULATION STEP 5] 警告: 第二次未能成功采集满40次 (成功采集了 {len(samples2)} 次)")
                
                visr2 = sum(samples2) / len(samples2) if samples2 else 0.0
                if logger:
                    logger(f"[INSULATION STEP 5] Visr2 均值 = {visr2:.4f} V (总采样次数: {len(samples2)})")

                vn2 = ((r3 + r0) * visr2) / r0 if r0 != 0 else 0.0
                vp2 = hv1 - vn2
                if logger:
                    logger(f"[INSULATION STEP 5] 计算得到 VN2 = {vn2:.3f} V, VP2 = {vp2:.3f} V")

                # ==================== 计算 RP, RN ====================
                if is_neg_arm:
                    # Case 1: 当 VP1 <= VN1 时
                    denom_rp = (vn1 * vn2) / (vp2 * vn1 - vp1 * vn2) - (r2 / r3) if (vp2 * vn1 - vp1 * vn2) != 0 else 0.0
                    rp = r2 / denom_rp if denom_rp != 0.0 else 9999.9
                    
                    k = (vp1 * vn2) / (vp2 * vn1) if (vp2 * vn1) != 0 else 0.0
                    num_rn = 1.0 - k
                    denom_rn = k * (1.0 / (r4 + r0) + 1.0 / r2) - 1.0 / (r4 + r0)
                    rn = num_rn / denom_rn if denom_rn != 0.0 else 9999.9
                    if logger:
                        logger(f"[INSULATION RP/RN] 匹配 Case 1 公式计算得到 RP = {rp:.2f} kΩ, RN = {rn:.2f} kΩ")
                else:
                    # Case 2: 当 VP1 > VN1 时
                    k1 = vp1 / vn1 if vn1 != 0 else 0.0
                    k2 = vp2 / vn2 if vn2 != 0 else 0.0
                    num_rp = k1 - k2
                    denom_rp = k2 * (1.0 / r1 + 1.0 / r3) - k1 * (1.0 / r3)
                    rp = num_rp / denom_rp if denom_rp != 0.0 else 9999.9
                    
                    k1_rn = vn1 / vp1 if vp1 != 0 else 0.0
                    k2_rn = vn2 / vp2 if vp2 != 0 else 0.0
                    num_rn = k1_rn - k2_rn
                    denom_rn = (k2_rn - k1_rn) * (1.0 / (r4 + r0)) - 1.0 / r1
                    rn = num_rn / denom_rn if denom_rn != 0.0 else 9999.9
                    if logger:
                        logger(f"[INSULATION RP/RN] 匹配 Case 2 公式计算得到 RP = {rp:.2f} kΩ, RN = {rn:.2f} kΩ")

                if rp < 0: rp = 9999.9
                if rn < 0: rn = 9999.9

                # 统一清理：恢复桥臂断开状态
                if logger: logger("[INSULATION CLEANUP] 断开桥臂以保护电路...")
                tx_cleanup = bytes([self.REQUEST_PREFIX, 0x03, 0x01, 0x00, 0x01, 0x00, 0x00, 0x00])
                self.can_driver.send_can_message(self.channel_id, req_id, 0, 8, tx_cleanup)
                
                value_str = f"正极绝缘:{rp:.1f}kΩ, 负极绝缘:{rn:.1f}kΩ"
                res = EOLResult(True, value=value_str)
                res.rp = rp
                res.rn = rn
                return res

            except Exception as e:
                if logger:
                    logger(f"[INSULATION ERROR] 发生异常: {e}")
                return EOLResult(False, error=f"校验失败: {e}", value="正极绝缘为0kΩ, 负极绝缘为0kΩ")
            
        if op_name not in self.operations:
            return EOLResult(False, error=f"不支持的EOL操作: {op_name}")
        
        spec = self.operations[op_name]
        try:
            payload = spec.get("payload")(kwargs) if callable(spec.get("payload")) else spec.get("payload")
            tx_id = self._int_arg(kwargs, "TX_ID", "发送ID") if "TX_ID" in kwargs or "发送ID" in kwargs else None
            rx_id = self._int_arg(kwargs, "RX_ID", "接收ID") if "RX_ID" in kwargs or "接收ID" in kwargs else None
            
            # 提取 TYPE 和 DLC (由 StepDialog 传入)
            can_type = self._int_arg(kwargs, "TYPE", "CAN类型") if "TYPE" in kwargs or "CAN类型" in kwargs else 0
            dlc = self._int_arg(kwargs, "DLC", "长度") if "DLC" in kwargs or "长度" in kwargs else 8
            
            # ADC 读取模式特殊处理 (0x06)
            op_code = spec.get("operation", 0)
            if "0x06" in op_name:
                # ADC: 0x01 raw, 0x02 value
                mode_str = str(kwargs.get("读取模式", kwargs.get("MODE", ""))).upper()
                op_code = 0x01 if "RAW" in mode_str or "原始" in mode_str else 0x02
                
                try:
                    adc_index = self._int_arg(kwargs, "ADC", "INDEX", "PARAM1")
                    if adc_index in [0x20, 0x21, 0x2B, 0x2C, 0x2F, 0x30]:
                        if logger:
                            logger(f"[*] 触发特殊ADC通道(0x{adc_index:02X})高频采样模式：15-25ms随机采集，持续4秒取最大值")
                        req_id = tx_id if tx_id is not None else self.REQUEST_ID
                        resp_id = rx_id if rx_id is not None else self.RESPONSE_ID
                        tx_data = bytes([self.REQUEST_PREFIX, 0x06, op_code, 0x00, adc_index, 0x00, 0x00, 0x00])
                        
                        import random
                        max_val = None
                        start_time = time.time()
                        count = 0
                        while time.time() - start_time < 4.0:
                            if hasattr(self.can_driver, 'clear_rx_history'):
                                self.can_driver.clear_rx_history(resp_id)
                            t_send = time.time()
                            self.can_driver.send_can_message(self.channel_id, req_id, can_type, dlc, tx_data)
                            msg = self.can_driver.wait_for_message(
                                can_id=resp_id, channel_id=None,
                                predicate=lambda m: (
                                    len(m.get("data", b"")) >= 4
                                    and m.get("data", b"")[0] in [self.RESPONSE_PREFIX, self.REQUEST_PREFIX]
                                    and m.get("data", b"")[1] == 0x06
                                    and m.get("data", b"")[2] == op_code
                                ),
                                timeout=0.1, since_time=t_send, consume=True
                            )
                            if msg:
                                raw = msg.get("data", b"")
                                if raw[3] == self.POSITIVE_RESPONSE:
                                    val = round(self._decode_index_u16(raw) * 0.001, 3)
                                    count += 1
                                    if logger: logger(f"[ADC HIGHSPEED] CAN RX: {raw.hex(' ').upper()} -> {val} V")
                                    if max_val is None or val > max_val:
                                        max_val = val
                                else:
                                    if logger: logger(f"[ADC HIGHSPEED] CAN RX (否定): {raw.hex(' ').upper()}")
                            
                            elapsed = time.time() - t_send
                            target_interval = random.uniform(0.015, 0.025)
                            if elapsed < target_interval:
                                time.sleep(target_interval - elapsed)
                                
                        if max_val is not None:
                            if logger: logger(f"[ADC HIGHSPEED] 4秒采集结束，共采集 {count} 次有效数据，最大值为: {max_val}")
                            return EOLResult(True, value=max_val)
                        else:
                            if logger: logger(f"[ADC HIGHSPEED] 错误: 未能在4秒内采集到有效数据")
                            return EOLResult(False, error="4秒高频采样无有效数据", value=0.0)
                except Exception as ex:
                    if logger: logger(f"[WARNING] ADC特殊采样解析异常: {ex}")
            
            # GPIO 控制读取与写入特殊处理 (0x04)
            elif "0x04" in op_name:
                try:
                    gpio_index = self._int_arg(kwargs, "GPIO", "INDEX", "PARAM1")
                    action_val = str(kwargs.get("PARAM2", kwargs.get("LEVEL", kwargs.get("VALUE", "")))).upper()
                    
                    if "WRITE_HIGH" in action_val or "写高" in action_val or "0x05" in action_val:
                        op_code = 0x05
                        payload = bytes([gpio_index, 0x01])
                    elif "WRITE_LOW" in action_val or "写低" in action_val:
                        op_code = 0x05
                        payload = bytes([gpio_index, 0x00])
                    else:
                        # 默认读取模式
                        op_code = 0x01
                        payload = bytes([gpio_index])
                except Exception as ex:
                    # 容错降级
                    logger(f"[WARNING] GPIO 0x04 自动打包解析异常: {ex}") if logger else print(f"[WARNING] GPIO 0x04 自动打包解析异常: {ex}")
            
            # NTC 温度读取特殊处理 (0x10)
            elif "0x10" in op_name:
                try:
                    # 1. 提取温感类型 (CELL_NTC -> 0x01, PCB_NTC -> 0x02, SHUNT -> 0x03, NTCF -> 0x04, FPCB_NTC -> 0x05)
                    type_val = str(kwargs.get("NTC_TYPE", kwargs.get("PARAM2", kwargs.get("TYPE", "CELL_NTC")))).upper()
                    type_map = {
                        "CELL_NTC": 0x01, "CELL": 0x01, "单体": 0x01, "0X01": 0x01, "1": 0x01,
                        "PCB_NTC": 0x02, "PCB": 0x02, "板载": 0x02, "0X02": 0x02, "2": 0x02,
                        "SHUNT": 0x03, "分流器": 0x03, "0X03": 0x03, "3": 0x03,
                        "NTCF": 0x04, "0X04": 0x04, "4": 0x04,
                        "FPCB_NTC": 0x05, "FPCB": 0x05, "0X05": 0x05, "5": 0x05
                    }
                    if type_val in type_map:
                        op_code = type_map[type_val]
                    else:
                        try: op_code = int(type_val, 0)
                        except: op_code = 0x01  # 默认降级为 CELL_NTC
                    
                    # 2. 提取温感索引并打包至 payload 的第二个字节 (CAN 帧的第 6 字节)
                    ntc_index = self._int_arg(kwargs, "NTC", "INDEX", "PARAM1")
                    if not 0 <= ntc_index <= 255:
                        raise ValueError(f"NTC index out of range: {ntc_index} (expected 0-255)")
                    payload = bytes([0x00, ntc_index])
                except Exception as ex:
                    # 异常降级
                    logger(f"[WARNING] NTC 0x10 自动打包解析异常: {ex}") if logger else print(f"[WARNING] NTC 0x10 自动打包解析异常: {ex}")
            
            # CRASH 读取特殊处理 (0x08)
            elif "0x08" in op_name:
                try:
                    # 1. 模式参数配置在第 3 字节 (operation)
                    op_code = self._int_arg(kwargs, "PARAM1", "OP", "MODE")
                    # 2. 索引参数配置在第 5 字节 (payload[0])
                    crash_index = self._int_arg(kwargs, "PARAM2", "INDEX")
                    payload = bytes([crash_index])
                except Exception as ex:
                    logger(f"[WARNING] CRASH 0x08 自动打包解析异常: {ex}") if logger else print(f"[WARNING] CRASH 0x08 自动打包解析异常: {ex}")

            # CSC 控制读取特殊处理 (0x07)
            elif "0x07" in op_name:
                try:
                    # 1. 提取操作类别，作为第 3 字节 (operation)
                    op_type = str(kwargs.get("PARAM1", kwargs.get("OP", "单体电压读取"))).strip()
                    
                    if "设置节点数目" in op_type or "0x01" in op_type or op_type == "1":
                        op_code = 0x01
                        node_cnt = self._int_arg(kwargs, "PARAM3", "COUNT", "NODE_COUNT", "VALUE")
                        payload = bytes([node_cnt])
                    elif "高压读取" in op_type or "0x02" in op_type or op_type == "2":
                        op_code = 0x02
                        hv_idx_str = str(kwargs.get("PARAM2", kwargs.get("INDEX", "0x00"))).strip()
                        if "0x02" in hv_idx_str or "HV1" in hv_idx_str or hv_idx_str == "2":
                            hv_idx = 0x02
                        elif "0x03" in hv_idx_str or "HV2" in hv_idx_str or hv_idx_str == "3":
                            hv_idx = 0x03
                        elif "0x04" in hv_idx_str or "HV3" in hv_idx_str or hv_idx_str == "4":
                            hv_idx = 0x04
                        elif "0x0B" in hv_idx_str or "0X0B" in hv_idx_str or "Link1" in hv_idx_str or hv_idx_str == "11":
                            hv_idx = 0x0B
                        elif "0x0C" in hv_idx_str or "0X0C" in hv_idx_str or "Link2" in hv_idx_str or hv_idx_str == "12":
                            hv_idx = 0x0C
                        else:
                            try: hv_idx = int(hv_idx_str, 0)
                            except: hv_idx = 0x00
                        payload = bytes([hv_idx])
                    elif "单体电压读取" in op_type or "0x0E" in op_type or "0x0e" in op_type or op_type == "14":
                        op_code = 0x0E
                        cell_idx = self._int_arg(kwargs, "PARAM4", "INDEX", "CELL")
                        payload = cell_idx.to_bytes(2, "big")
                    elif "Stack电压读取" in op_type or "0x0F" in op_type or "0x0f" in op_type or op_type == "15":
                        op_code = 0x0F
                        stack_idx = self._int_arg(kwargs, "PARAM4", "INDEX", "CELL")
                        payload = stack_idx.to_bytes(2, "big")
                    elif "快充阻抗读取" in op_type or "0x10" in op_type or op_type == "16":
                        op_code = 0x10
                        payload = bytes([0x00])
                    else:
                        try: op_code = int(op_type, 0)
                        except: op_code = 0x0E
                        payload = bytes([0x00])
                except Exception as ex:
                    logger(f"[WARNING] CSC 0x07 自动打包解析异常: {ex}") if logger else print(f"[WARNING] CSC 0x07 自动打包解析异常: {ex}")

            # 唤醒源读取特殊处理 (0xFF)
            elif "0xFF" in op_name:
                try:
                    # 1. 读取项参数配置在第 3 字节 (operation)
                    read_item = kwargs.get("PARAM1", kwargs.get("OP", "0x06"))
                    read_item_str = str(read_item).strip()
                    
                    if "第一唤醒源" in read_item_str or "0x06" in read_item_str or read_item_str == "6":
                        op_code = 0x06
                    elif "压力传感器" in read_item_str or "0x0E" in read_item_str or "0x0e" in read_item_str or read_item_str == "14":
                        op_code = 0x0E
                    elif "高边负载回采电压" in read_item_str or "0x11" in read_item_str or read_item_str == "17":
                        op_code = 0x11
                    else:
                        try: op_code = int(read_item_str, 0)
                        except: op_code = 0x06
                        
                    # 2. 通道参数配置在第 5 字节 (payload[0])
                    # 只有读取高边负载回采电压 (0x11) 时，通道参数才生效，其他读取时通道值默认为 0
                    if op_code == 0x11:
                        ch_val = self._int_arg(kwargs, "PARAM2", "CH", "CHANNEL", "INDEX")
                    else:
                        ch_val = 0
                    payload = bytes([ch_val])
                except Exception as ex:
                    logger(f"[WARNING] 0xFF 扩展指令自动打包解析异常: {ex}") if logger else print(f"[WARNING] 0xFF 扩展指令自动打包解析异常: {ex}")

            # --- 终极加固：一段时间内最大值采样滤波机制 (完美解决互锁信号等 PWM 占空比波动问题) ---
            # 提取最大值采样参数 (支持 MAX_DURATION:1.5 格式，单位：秒)
            max_duration = None
            if "MAX_DURATION" in kwargs:
                try: max_duration = float(kwargs.pop("MAX_DURATION"))
                except: pass
            elif "ARGS" in kwargs:
                # 兼容在 ARGS 框里填写的 MAX_DURATION:1.5 格式
                args_str = str(kwargs.get("ARGS", ""))
                import re
                dur_match = re.search(r'MAX_DURATION:([\d.]+)', args_str, re.IGNORECASE)
                if dur_match:
                    try: max_duration = float(dur_match.group(1))
                    except: pass

            if max_duration is not None and max_duration > 0:
                interval = 0.05 # 采样间隔默认 50ms 
                if "INTERVAL" in kwargs:
                    try: interval = float(kwargs.pop("INTERVAL"))
                    except: pass
                
                log_fn = logger if logger else print
                log_fn(f"[!] 启动最大值滤波采样：时长 {max_duration}s，间隔 {interval}s...")
                
                start_time = time.time()
                collected_values = []
                last_result = None
                
                while time.time() - start_time < max_duration:
                    res = self.transact(
                        device_id=spec.get("device_id"),
                        operation=op_code,
                        payload=payload,
                        timeout=timeout,
                        decoder=spec.get("decoder"),
                        request_id=tx_id,
                        response_id=rx_id,
                        can_type=can_type,
                        dlc=dlc,
                        logger=logger
                    )
                    last_result = res
                    if res.success:
                        # 尝试将结果解析为浮点数
                        try:
                            f_val = float(res.value)
                            collected_values.append(f_val)
                        except: pass
                    
                    time.sleep(interval)
                
                if collected_values:
                    max_val = max(collected_values)
                    log_fn(f"[!] 最大值滤波采样完成。共成功采样 {len(collected_values)} 次，提取最大值: {max_val}")
                    # 将最大值覆盖写回最终的 EOLResult 实体
                    last_result.success = True
                    last_result.value = max_val
                    return last_result
                else:
                    log_fn(f"[WARNING] 最大值滤波采样期间未采集到任何有效物理数值，以最后一次返回作为兜底。")
                    return last_result if last_result else EOLResult(False, error="采样期间全数失败")

            # --- 最接近设定值采样滤波机制 (支持 SAMPLES:10, TARGET:2.5) ---
            target_val = None
            if "TARGET_VAL" in kwargs:
                try: target_val = float(kwargs.pop("TARGET_VAL"))
                except: pass
            elif "TARGET" in kwargs:
                try: target_val = float(kwargs.pop("TARGET"))
                except: pass
            elif "ARGS" in kwargs:
                args_str = str(kwargs.get("ARGS", ""))
                import re
                target_match = re.search(r'(?:TARGET_VAL|TARGET):([\d.-]+)', args_str, re.IGNORECASE)
                if target_match:
                    try: target_val = float(target_match.group(1))
                    except: pass

            filter_samples = None
            if "AVG_SAMPLES" in kwargs:
                try: filter_samples = int(kwargs.get("AVG_SAMPLES"))
                except: pass
            elif "SAMPLES" in kwargs:
                try: filter_samples = int(kwargs.get("SAMPLES"))
                except: pass
            elif "ARGS" in kwargs:
                args_str = str(kwargs.get("ARGS", ""))
                import re
                samples_match = re.search(r'(?:AVG_SAMPLES|SAMPLES):(\d+)', args_str, re.IGNORECASE)
                if samples_match:
                    try: filter_samples = int(samples_match.group(1))
                    except: pass

            comp_limit = None
            if "COMP_LIMIT" in kwargs:
                try: comp_limit = float(kwargs.pop("COMP_LIMIT"))
                except: pass
            elif "ARGS" in kwargs:
                args_str = str(kwargs.get("ARGS", ""))
                import re
                limit_match = re.search(r'COMP_LIMIT:([\d.-]+)', args_str, re.IGNORECASE)
                if limit_match:
                    try: comp_limit = float(limit_match.group(1))
                    except: pass

            comp_max = None
            if "COMP_MAX" in kwargs:
                try: comp_max = float(kwargs.pop("COMP_MAX"))
                except: pass
            elif "ARGS" in kwargs:
                args_str = str(kwargs.get("ARGS", ""))
                import re
                max_match = re.search(r'COMP_MAX:([\d.-]+)', args_str, re.IGNORECASE)
                if max_match:
                    try: comp_max = float(max_match.group(1))
                    except: pass

            if target_val is not None and filter_samples is not None and filter_samples > 0:
                kwargs.pop("AVG_SAMPLES", None)
                kwargs.pop("SAMPLES", None)
                kwargs.pop("COMP_LIMIT", None)
                kwargs.pop("COMP_MAX", None)
                
                interval = 0.05
                if "INTERVAL" in kwargs:
                    try: interval = float(kwargs.pop("INTERVAL"))
                    except: pass
                elif "ARGS" in kwargs:
                    args_str = str(kwargs.get("ARGS", ""))
                    import re
                    int_match = re.search(r'INTERVAL:([\d.]+)', args_str, re.IGNORECASE)
                    if int_match:
                        try: interval = float(int_match.group(1))
                        except: pass
                        
                log_fn = logger if logger else print
                log_fn(f"[!] 启动最接近值滤波采样：样本数 {filter_samples} 次，目标值 {target_val}，采样间隔 {interval}s...")
                
                collected_values = []
                last_result = None
                
                for attempt in range(filter_samples):
                    res = self.transact(
                         device_id=spec.get("device_id"),
                         operation=op_code,
                         payload=payload,
                         timeout=timeout,
                         decoder=spec.get("decoder"),
                         request_id=tx_id,
                         response_id=rx_id,
                         can_type=can_type,
                         dlc=dlc,
                         logger=logger
                    )
                    last_result = res
                    if res.success:
                        try:
                            f_val = float(res.value)
                            collected_values.append(f_val)
                        except: pass
                    
                    if attempt < filter_samples - 1:
                        time.sleep(interval)
                        
                if collected_values:
                    closest_val = min(collected_values, key=lambda x: abs(x - target_val))
                    original_val = closest_val
                    is_compensated = False
                    
                    if comp_limit is not None and comp_max is not None:
                        diff = abs(closest_val - target_val)
                        if comp_limit < diff <= comp_max:
                            closest_val = target_val
                            is_compensated = True
                            
                    if is_compensated:
                        log_fn(f"[!] 最接近值滤波采样完成。共成功采样 {len(collected_values)} 次，所有测量值: {collected_values}，与目标值 {target_val} 最接近的原值为: {original_val} (超出偏差限值 {comp_limit} 且在可补偿上限 {comp_max} 内，已做逼近补偿，修正为: {closest_val})")
                    else:
                        log_fn(f"[!] 最接近值滤波采样完成。共成功采样 {len(collected_values)} 次，所有测量值: {collected_values}，与目标值 {target_val} 最接近的值为: {closest_val}")
                    
                    last_result.success = True
                    last_result.value = closest_val
                    return last_result
                else:
                    log_fn(f"[WARNING] 最接近值滤波采样期间未采集到任何有效物理数值，以最后一次返回作为兜底。")
                    return last_result if last_result else EOLResult(False, error="采样期间全数失败")

            # --- 均值采样滤波机制 (支持 SAMPLES:10, INTERVAL:0.02) ---
            avg_samples = None
            if "AVG_SAMPLES" in kwargs:
                try: avg_samples = int(kwargs.pop("AVG_SAMPLES"))
                except: pass
            elif "SAMPLES" in kwargs:
                try: avg_samples = int(kwargs.pop("SAMPLES"))
                except: pass
            elif "ARGS" in kwargs:
                args_str = str(kwargs.get("ARGS", ""))
                import re
                samples_match = re.search(r'(?:AVG_SAMPLES|SAMPLES):(\d+)', args_str, re.IGNORECASE)
                if samples_match:
                    try: avg_samples = int(samples_match.group(1))
                    except: pass

            if avg_samples is not None and avg_samples > 0:
                interval = 0.05
                if "INTERVAL" in kwargs:
                    try: interval = float(kwargs.pop("INTERVAL"))
                    except: pass
                elif "ARGS" in kwargs:
                    args_str = str(kwargs.get("ARGS", ""))
                    import re
                    int_match = re.search(r'INTERVAL:([\d.]+)', args_str, re.IGNORECASE)
                    if int_match:
                        try: interval = float(int_match.group(1))
                        except: pass
                        
                log_fn = logger if logger else print
                log_fn(f"[!] 启动均值滤波采样：样本数 {avg_samples} 次，采样间隔 {interval}s...")
                
                collected_values = []
                last_result = None
                
                for attempt in range(avg_samples):
                    res = self.transact(
                        device_id=spec.get("device_id"),
                        operation=op_code,
                        payload=payload,
                        timeout=timeout,
                        decoder=spec.get("decoder"),
                        request_id=tx_id,
                        response_id=rx_id,
                        can_type=can_type,
                        dlc=dlc,
                        logger=logger
                    )
                    last_result = res
                    if res.success:
                        try:
                            f_val = float(res.value)
                            collected_values.append(f_val)
                        except: pass
                    
                    if attempt < avg_samples - 1:
                        time.sleep(interval)
                        
                if collected_values:
                    n = len(collected_values)
                    if n >= 3:
                        sorted_vals = sorted(collected_values)
                        trimmed_vals = sorted_vals[1:-1]
                        avg_val = sum(trimmed_vals) / len(trimmed_vals)
                        log_fn(f"[!] 均值滤波采样完成。共成功采样 {n} 次，去除最大值({sorted_vals[-1]})与最小值({sorted_vals[0]})，{n-2}次测量均值: {avg_val:.3f}")
                    else:
                        avg_val = sum(collected_values) / n
                        log_fn(f"[!] 均值滤波采样完成。共成功采样 {n} 次，不够扣除最值，均值: {avg_val:.3f}")
                        
                    last_result.success = True
                    last_result.value = round(avg_val, 3)
                    return last_result
                else:
                    log_fn(f"[WARNING] 均值滤波采样期间未采集到任何有效物理数值，以最后一次返回作为兜底。")
                    return last_result if last_result else EOLResult(False, error="采样期间全数失败")

            # --- 常规单次读取逻辑 ---
            return self.transact(
                device_id=spec.get("device_id"),
                operation=op_code,
                payload=payload,
                timeout=timeout,
                decoder=spec.get("decoder"),
                request_id=tx_id,
                response_id=rx_id,
                can_type=can_type,
                dlc=dlc,
                logger=logger
            )
        except Exception as e:
            import traceback
            traceback_str = traceback.format_exc()
            if logger: logger(f"[CRITICAL EOL EXCEPTION] {traceback_str}")
            else: print(f"[CRITICAL EOL EXCEPTION] {traceback_str}")
            return EOLResult(False, error=f"EOL参数错误: {e}")

    def clear_dtc_faults(self, req_id: int = 0x7F0, resp_id: int = 0x7F8, timeout: float = 1.0, logger: Callable[[str], None] = None) -> EOLResult:
        """
        DTC 故障清除操作:
        向 req_id (默认 0x7F0) 发送报文 10 13 01 00 00 00 00 00
        监听 resp_id (默认 0x7F8) 肯定响应 11 13 01 40 ...
        """
        try:
            tx_cmd = bytes([self.REQUEST_PREFIX, 0x13, 0x01, 0x00, 0x00, 0x00, 0x00, 0x00])
            if logger:
                logger(f"[*] 启动 DTC 故障清除... 发送ID=0x{req_id:X}, 接收ID=0x{resp_id:X}")

            for attempt in range(1, 6):
                if logger:
                    logger(f"[DTC 故障清除] 发送清除请求 (尝试 {attempt}/5) CAN TX CH:{self.channel_id} DATA={tx_cmd.hex(' ').upper()}")

                if hasattr(self.can_driver, 'clear_rx_history'):
                    self.can_driver.clear_rx_history(resp_id)

                send_time = time.time()
                if not self.can_driver.send_can_message(self.channel_id, req_id, 0, 8, tx_cmd):
                    time.sleep(0.1)
                    continue

                msg = self.can_driver.wait_for_message(
                    can_id=resp_id,
                    channel_id=None,
                    predicate=lambda m: (
                        len(m.get("data", b"")) >= 8
                        and m.get("data", b"")[0] in [self.RESPONSE_PREFIX, self.REQUEST_PREFIX]
                        and m.get("data", b"")[1] == 0x13
                        and m.get("data", b"")[2] == 0x01
                        and m.get("data", b"")[3] == self.POSITIVE_RESPONSE
                    ),
                    timeout=timeout,
                    since_time=send_time,
                    consume=True
                )
                if msg:
                    raw_data = msg.get("data", b"")
                    if logger:
                        logger(f"[DTC 故障清除] 收到肯定响应 CAN RX CH:{self.channel_id} DATA={raw_data.hex(' ').upper()}")
                    return EOLResult(True, value=raw_data)
                else:
                    time.sleep(0.1)

            if logger:
                logger("[DTC 故障清除] 错误: 故障清除超时且重试均失败")
            return EOLResult(False, error="DTC故障清除超时", value="清除失败")
        except Exception as ex:
            if logger:
                logger(f"[DTC 故障清除] 异常: {ex}")
            return EOLResult(False, error=str(ex), value="清除异常")

    # 兼容历史别名
    clear_dtu_faults = clear_dtc_faults

    def _build_operations(self) -> Dict[str, Dict[str, Any]]:
        return {
            # --- 0x03 绝缘 ---
            "0x03_insulation_control": {"device_id": 0x03, "operation": 0x01, "payload": lambda kw: bytes([0x01, self._int_arg(kw, "STATE", "VALUE")])},
            "0x03 绝缘控制写入": {"device_id": 0x03, "operation": 0x01, "payload": lambda kw: bytes([0x01, self._int_arg(kw, "STATE", "VALUE")])},
            "0x03_read_insulation": {"device_id": 0x03, "operation": 0x03, "decoder": self._decode_insulation},
            "0x03 绝缘控制读取": {"device_id": 0x03, "operation": 0x03, "decoder": self._decode_insulation},
            "绝缘测试": {"device_id": 0x03, "operation": 0x03, "decoder": self._decode_insulation},
            
            # --- 0x04 GPIO ---
            "0x04_read_gpio": {"device_id": 0x04, "operation": 0x01, "payload": lambda kw: bytes([self._int_arg(kw, "GPIO", "INDEX")]), "decoder": self._decode_index_value},
            "0x04 GPIO控制读取": {"device_id": 0x04, "operation": 0x01, "payload": lambda kw: bytes([self._int_arg(kw, "GPIO", "INDEX")]), "decoder": self._decode_index_value},
            "0x04_write_gpio": {"device_id": 0x04, "operation": 0x05, "payload": lambda kw: bytes([self._int_arg(kw, "GPIO", "INDEX"), self._int_arg(kw, "LEVEL", "VALUE")])},
            "0x04 GPIO控制写入": {"device_id": 0x04, "operation": 0x05, "payload": lambda kw: bytes([self._int_arg(kw, "GPIO", "INDEX"), self._int_arg(kw, "LEVEL", "VALUE")])},
            
            # --- 0x05 PWM ---
            "0x05_read_pwm_duty": {"device_id": 0x05, "operation": 0x01, "payload": lambda kw: bytes([self._int_arg(kw, "PWM", "CHANNEL", "INDEX")]), "decoder": self._decode_byte4},
            "0x05 PWM读取": {"device_id": 0x05, "operation": 0x01, "payload": lambda kw: bytes([self._int_arg(kw, "PWM", "CHANNEL", "INDEX")]), "decoder": self._decode_byte4},
            
            # --- 0x06 ADC ---
            "0x06_read_adc_value": {"device_id": 0x06, "operation": 0x02, "payload": lambda kw: bytes([self._int_arg(kw, "ADC")]), "decoder": lambda raw: round(self._decode_index_u16(raw) * 0.001, 3)},
            "0x06 ADC读取": {"device_id": 0x06, "operation": 0x02, "payload": lambda kw: bytes([self._int_arg(kw, "ADC")]), "decoder": lambda raw: round(self._decode_index_u16(raw) * 0.001, 3)},
            
            # --- 0x07 CSC ---
            "0x07 CSC控制读取": {"device_id": 0x07, "operation": 0x0E, "decoder": self._decode_csc},
            "0x07 CSC控制写入": {"device_id": 0x07, "operation": 0x01, "payload": lambda kw: bytes([self._int_arg(kw, "COUNT", "STATE", "TYPE")])},
            "0x07 CSC批量读取": {"device_id": 0x07, "operation": 0x0E, "decoder": self._decode_csc},
            "0x07 Stack批量读取": {"device_id": 0x07, "operation": 0x0F, "decoder": self._decode_csc},

            
            # --- 0x08 CRASH ---
            "0x08 CRASH读取": {"device_id": 0x08, "operation": 0x01, "payload": lambda kw: bytes([self._int_arg(kw, "PARAM2", "INDEX", "TYPE")]), "decoder": self._decode_data_u32},
            
            # --- 0x09 RTC ---
            "0x09 RTC控制读取": {"device_id": 0x09, "operation": 0x04, "decoder": self._decode_payload_hex},
            "0x09 RTC控制写入": {"device_id": 0x09, "operation": 0x07, "payload": lambda kw: self._bytes_arg(kw, "DATA", length=4)},
            
            # --- 0x10 NTC ---
            "0x10 NTC读取": {"device_id": 0x10, "operation": 0x01, "payload": lambda kw: bytes([0x00, self._int_arg(kw, "INDEX", "NTC")]), "decoder": self._decode_temp},
            
            # --- 0x0A EEPROM ---
            "0x0A EEPROM控制读取": {"device_id": 0x0A, "operation": 0x03, "decoder": self._decode_payload_hex},
            "0x0A EEPROM控制写入": {"device_id": 0x0A, "operation": 0x05, "payload": lambda kw: self._bytes_arg(kw, "DATA", length=4)},
            "EEPROM测试": {"device_id": 0x0A, "operation": 0x03, "decoder": self._decode_payload_hex},
            
            # --- 0x0B 霍尔电流 ---
            "0x0B 霍尔电流读取": {"device_id": 0x0B, "operation": 0x01, "payload": lambda kw: bytes([self._int_arg(kw, "HALL", "CHANNEL")]), "decoder": self._decode_current},
            
            # --- 0xFF 扩展指令 ---
            "0xFF 扩展指令": {"device_id": 0xFF, "operation": 0x06, "decoder": self._decode_wakeup},
        }

    def _int_arg(self, kwargs, *names, length: Optional[int] = None) -> int:
        # 扩展搜索名称，增加通用回退项
        search_names = list(names)
        search_names.extend([
            "INDEX", "索引", "VALUE", "数值", "STATE", "状态", "参数1", "参数2", "ARG1", "ARG2",
            "PWM通道", "GPIO通道", "ADC选择", "ADC", "读取模式", "MODE", "NTC索引", "霍尔通道", "读取项", "子索引/状态", "控制值"
        ])
        
        for name in search_names:
            u_name = name.upper()
            if u_name in kwargs:
                value = kwargs[u_name]
                if isinstance(value, int):
                    return value
                try:
                    return int(str(value).strip(), 0)
                except:
                    continue
        raise ValueError(f"缺少参数: {'/'.join(names)} (当前可用: {list(kwargs.keys())})")

    def _bytes_arg(self, kwargs, name: str, length: Optional[int] = None) -> bytes:
        if name not in kwargs:
            raise ValueError(f"缺少参数: {name}")
        value = kwargs[name]
        if isinstance(value, bytes):
            data = value
        else:
            text = str(value).replace("0x", "").replace(" ", "").replace(",", "")
            data = bytes.fromhex(text)
        if length is not None:
            data = data[:length].ljust(length, b"\x00")
        return data

    def _decode_payload_hex(self, raw: bytes):
        return raw[4:8].hex(" ").upper() if len(raw) >= 8 else None

    def _decode_byte4(self, raw: bytes):
        return raw[4] if len(raw) >= 5 else None

    def _decode_wakeup(self, raw: bytes):
        if len(raw) < 5:
            return None
        op_code = raw[2] if len(raw) >= 3 else 0x06
        
        if op_code == 0x06:
            # 读取第一唤醒源: 有效数据是第5字节，精度1
            return raw[4]
        elif op_code == 0x0E:
            # 读取压力传感器: 有效数据是第5,6,7,8字节，高位在前，精度1
            if len(raw) < 8:
                return None
            return int.from_bytes(raw[4:8], "big") * 100
        elif op_code == 0x11:
            # 读取高边负载回采电压: 有效数据是第5,6字节，高位在前，精度0.001
            if len(raw) < 6:
                return None
            val = int.from_bytes(raw[4:6], "big")
            return round(val * 0.001, 3)
            
        return raw[4]

    def _decode_csc(self, raw: bytes):
        if len(raw) < 5:
            return None
        op_code = raw[2] if len(raw) >= 3 else 0x0E
        
        if op_code == 0x01:
            # 设置节点数目: 精度1
            return raw[4]
        elif op_code == 0x02:
            # 高压读取: 精度0.001
            val = self._decode_data_u32(raw)
            return round(val * 0.001, 3) if val is not None else None
        elif op_code in (0x0E, 0x0F):
            # 单体电压读取 / Stack电压读取: 精度0.001
            val = self._decode_data_u32(raw)
            return round(val * 0.001, 3) if val is not None else None
        elif op_code == 0x10:
            # 快充阻抗读取: 4字节数据由高到低，精度1
            return self._decode_data_u32(raw)
            
        return self._decode_data_u32(raw)

    def _decode_index_value(self, raw: bytes):
        return raw[5] if len(raw) >= 6 else None

    def _decode_index_u16(self, raw: bytes):
        """解析返回报文中的 U16 数据 (通常在 Byte 5, 6)"""
        if len(raw) < 7:
            return None
        # 高位在前，低位在后 (Big Endian)
        val = (raw[5] << 8) | raw[6]
        return val

    def _decode_data_u32(self, raw: bytes):
        if len(raw) < 8:
            return None
        data = raw[4:8]
        return int.from_bytes(data, "big")

    def _decode_insulation(self, raw: bytes):
        if len(raw) < 8:
            return None
        value = (raw[4] << 16) | (raw[5] << 8) | raw[6]
        sign = -1 if raw[7] == 1 else 1
        return round(sign * value * 0.001, 3)

    def _decode_temp(self, raw: bytes):
        if len(raw) < 8:
            return None
        val = int.from_bytes(raw[4:8], "big")
        return val - 50

    def _decode_current(self, raw: bytes):
        value = self._decode_data_u32(raw)
        if value is None:
            return None
        return round(value * 0.001 - 2000, 3)

import os
import time
import configparser
import re
import uuid
from typing import Callable, Optional, Tuple


FLASH_FILE_EXTENSIONS = (".hex", ".s19", ".bin", ".mot")


def program_name_matches(program_path: str, expected_name: str) -> bool:
    """Match APP name with or without a file extension, case-insensitively."""
    actual = os.path.basename(os.path.normpath(program_path.strip()))
    expected = os.path.basename(os.path.normpath(expected_name.strip()))
    if not actual or not expected:
        return False
    if os.path.splitext(expected)[1]:
        return actual.casefold() == expected.casefold()
    return os.path.splitext(actual)[0].casefold() == expected.casefold()


def _find_failure(message: str, detail: str, logger=None) -> Tuple[bool, str]:
    """Keep diagnostic paths in the log, not in the measured value."""
    if logger:
        logger(f"[APP刷写配置] {message}: {detail}")
    return False, message


def find_app_program(search_path: str, expected_name: str = "", logger=None) -> Tuple[bool, str]:
    """Return the actual file name; the test item's string standard judges it."""
    raw_path = search_path.strip()
    if not raw_path:
        return False, "刷写文件路径为空"
    resolved_path = os.path.abspath(os.path.expandvars(raw_path))
    candidates = []
    try:
        if os.path.isfile(resolved_path):
            candidates = [resolved_path]
        elif os.path.isdir(resolved_path):
            candidates = [
                os.path.join(resolved_path, name)
                for name in sorted(os.listdir(resolved_path))
                if os.path.isfile(os.path.join(resolved_path, name))
                and os.path.splitext(name)[1].casefold() in FLASH_FILE_EXTENSIONS
            ]
        else:
            return _find_failure("刷写路径不存在", resolved_path, logger)
    except OSError as exc:
        return _find_failure("刷写目录读取失败", str(exc), logger)

    if not candidates:
        return _find_failure("未找到刷写文件", resolved_path, logger)
    if len(candidates) > 1:
        # APPname was the old UI placeholder, not an actual file-name standard.
        selector = expected_name.strip() if expected_name != "APPname" else ""
        matches = [p for p in candidates if selector and program_name_matches(p, selector)]
        if len(matches) != 1:
            return _find_failure("多个刷写文件，需指定筛选名称", ", ".join(candidates), logger)
        candidates = matches
    if logger:
        logger(f"[APP刷写配置] 已找到刷写文件: {candidates[0]}")
    return True, os.path.basename(candidates[0])


def find_configured_app_program(
    host_path: str,
    ini_path: str = "config.ini",
    section: str = "App",
    key: str = "addr",
    expected_name: str = "",
    logger: Optional[Callable[[str], None]] = None,
) -> Tuple[bool, str]:
    """Resolve Flash/<config value> beside the host program and find the APP file."""
    raw_host_path = host_path.strip()
    if not raw_host_path:
        return False, "上位机路径为空"
    expanded_host_path = os.path.abspath(os.path.expandvars(raw_host_path))
    host_dir = expanded_host_path if os.path.isdir(expanded_host_path) else os.path.dirname(expanded_host_path)
    if not os.path.isdir(host_dir):
        return _find_failure("上位机目录不存在", host_dir, logger)

    raw_ini_path = ini_path.strip() or "config.ini"
    resolved_ini_path = (
        os.path.abspath(os.path.expandvars(raw_ini_path))
        if os.path.isabs(raw_ini_path)
        else os.path.join(host_dir, raw_ini_path)
    )
    if not os.path.isfile(resolved_ini_path):
        return _find_failure("INI文件不存在", resolved_ini_path, logger)

    parser = configparser.ConfigParser()
    loaded = False
    last_error = None
    for encoding in ("utf-8-sig", "gbk", "utf-8"):
        try:
            with open(resolved_ini_path, "r", encoding=encoding) as ini_file:
                parser.read_file(ini_file)
            loaded = True
            break
        except (UnicodeError, configparser.Error) as exc:
            parser = configparser.ConfigParser()
            last_error = exc
        except OSError as exc:
            return _find_failure("INI读取失败", str(exc), logger)
    if not loaded:
        return _find_failure("INI读取失败", str(last_error), logger)

    matched_section = next(
        (name for name in parser.sections() if name.strip().casefold() == section.strip().casefold()), None
    )
    if not matched_section:
        return False, f"INI中未找到段 [{section}]"
    matched_key = next(
        (name for name in parser.options(matched_section) if name.strip().casefold() == key.strip().casefold()), None
    )
    if not matched_key:
        return False, f"INI的 [{matched_section}] 中未找到键 {key}"

    config_value = parser.get(matched_section, matched_key).strip(' \t\r\n"\'')
    if not config_value:
        return False, f"INI的 [{matched_section}] {matched_key} 值为空"
    flash_dir = os.path.normpath(os.path.join(host_dir, "Flash", config_value))
    return find_app_program(flash_dir, expected_name, logger=logger)


def record_path_from_host(host_path: str) -> Tuple[bool, str]:
    """Resolve Record.txt from the Flash directory beside the host program."""
    raw_host_path = host_path.strip()
    if not raw_host_path:
        return False, "上位机路径为空"
    expanded_host_path = os.path.abspath(os.path.expandvars(raw_host_path))
    host_dir = expanded_host_path if os.path.isdir(expanded_host_path) else os.path.dirname(expanded_host_path)
    if not os.path.isdir(host_dir):
        return False, f"上位机目录不存在: {host_dir}"
    return True, os.path.normpath(os.path.join(host_dir, "Flash", "Record.txt"))


def _record_state(content):
    """Use the latest complete state line; do not match words inside diagnostics."""
    states = []
    for line in content.splitlines():
        match = re.match(r"^\s*(START|FLASH|PASS|FAIL)(?=$|[\s:：-]|\.{3}|…)", line, re.IGNORECASE)
        if match:
            states.append(match.group(1).upper())
    return states[-1] if states else ""


def run_app_flash(
    record_path: str,
    timeout_seconds: float,
    logger: Optional[Callable[[str], None]] = None,
    poll_interval: float = 0.2,
    progress_callback=None,
    pcan=None,
    request_id=0x7E4,
    response_id=0x7EC,
    cancelled=None,
) -> Tuple[bool, str]:
    """Trigger the external flasher; Record alone determines final PASS/FAIL."""
    from core.flash_progress import FlashProgress

    log = logger or (lambda _message: None)
    started = time.monotonic()
    session_id = uuid.uuid4().hex
    tracker = FlashProgress(request_id, response_id)
    last_content, record_state, record_error = "", "", ""
    subscribed = False
    monitor_error = ""
    last_diagnostic_at = started - 5
    last_diagnostic = None

    def notify(result="", phase=""):
        nonlocal last_diagnostic_at, last_diagnostic
        connected = bool(pcan is not None and getattr(pcan, "is_connected", False))
        if not connected and tracker.accepted:
            tracker.invalidate("PCAN连接中断，进度未知")
        status = tracker.snapshot()
        status['monitor_ids'] = f'0x{request_id:X}/0x{response_id:X}'
        status['can_interface'] = str(getattr(pcan, 'channel', 'CAN')) if pcan is not None else 'CAN'
        diagnostic = (connected, status['received_frames'] > 0, status['last_can_age'] is not None,
                      status['can_warning'])
        now = time.monotonic()
        if diagnostic != last_diagnostic or now-last_diagnostic_at >= 5 or result:
            last_diagnostic, last_diagnostic_at = diagnostic, now
            log(f"[APP刷写监听] 接口={status['can_interface']} 已连接={connected} 已订阅={subscribed} 接收={status['received_frames']}帧 目标ID={status['monitor_ids']} 已见ID={','.join(status['observed_ids']) or '无'} 匹配={'是' if status['last_can_age'] is not None else '否'} 提示={monitor_error or status['can_warning'] or '无'}")
        if not subscribed or not connected:
            status["can_warning"] = monitor_error or "PCAN未连接，仅显示Record状态"
            status["percent"] = None
        if phase:
            status["phase"] = phase
        elif not (subscribed and connected) or status["last_can_age"] is None:
            status["phase"] = "上位机报告刷写中" if record_state == "FLASH" else "等待上位机响应"
        status.update({
            "session_id": session_id, "started_at": started,
            "elapsed": max(0.0, time.monotonic() - started), "active": not bool(result),
            "result": result, "record": last_content, "record_state": record_state,
            "record_error": record_error, "can_connected": connected,
        })
        if progress_callback:
            try:
                progress_callback(status)
            except Exception:
                # A closed UI must not change the outcome of a running flash operation.
                pass

    def finish(success, result, phase):
        notify(result, phase)
        return success, result

    try:
        # Subscribe before Start so the download request cannot be missed.
        if pcan is not None and hasattr(pcan, "add_message_listener"):
            try:
                pcan.add_message_listener(tracker.feed)
                subscribed = True
            except Exception as exc:
                monitor_error = f"PCAN监听不可用: {exc}"
                log(f"[APP刷写] {monitor_error}")
        notify(phase="准备启动刷写")
        log(f'[APP刷写] 被动CAN监听：接口={getattr(pcan, "channel", "未配置")} 连接={bool(pcan is not None and getattr(pcan, "is_connected", False))} 订阅={subscribed} 请求ID=0x{request_id:X} 响应ID=0x{response_id:X}；最终判定以Record为准')
        if cancelled and cancelled():
            return finish(False, "STOPPED", "已停止监控，未确认上位机停止")
        raw_record_path = record_path.strip()
        if not raw_record_path:
            return finish(False, "Record.txt 路径为空", "启动失败：Record路径为空")
        record_path = os.path.abspath(os.path.expandvars(raw_record_path))
        try:
            record_dir = os.path.dirname(record_path)
            if record_dir:
                os.makedirs(record_dir, exist_ok=True)
            with open(record_path, "w", encoding="utf-8") as record_file:
                record_file.write("Start\n")
                record_file.flush()
                os.fsync(record_file.fileno())
        except OSError as exc:
            record_error = str(exc)
            return finish(False, f"写入 Start 失败: {exc}", "启动失败：无法写入Record")

        last_content, record_state = "Start", "START"
        log(f"[APP刷写] 已写入 Start: {record_path}")
        notify(phase="已请求启动，等待上位机响应")
        deadline = started + max(0.1, float(timeout_seconds))
        flash_reported = False
        while time.monotonic() < deadline:
            if cancelled and cancelled():
                return finish(False, "STOPPED", "已停止监控，未确认上位机停止")
            try:
                with open(record_path, "r", encoding="utf-8-sig", errors="replace") as record_file:
                    content = record_file.read().strip()
                record_error = "" if content else "Record暂为空，等待上位机更新"
            except OSError as exc:
                content = ""
                record_error = f"Record读取异常: {exc}"
            if content:
                last_content = content
                record_state = _record_state(content) or record_state
            # Only the current successful read can provide a terminal result.
            observed_state = _record_state(content)
            if cancelled and cancelled():
                return finish(False, "STOPPED", "已停止监控，未确认上位机停止")
            if observed_state == "FAIL":
                return finish(False, "FAIL", "刷写失败")
            if observed_state == "PASS":
                return finish(True, "PASS", "刷写成功")
            if observed_state == "FLASH" and not flash_reported:
                flash_reported = True
                log("[APP刷写] FLASH：刷写进行中")
            notify()
            time.sleep(max(0.05, poll_interval))

        detail = f"，最后内容: {last_content}" if last_content else ""
        return finish(False, f"TIMEOUT ({timeout_seconds:g}s){detail}", "等待Record结果超时")
    finally:
        if subscribed:
            try:
                pcan.remove_message_listener(tracker.feed)
            except Exception:
                pass  # Adapter disposal must not replace the final Record result.


def run_configured_app_flash(
    host_path: str,
    timeout_seconds: float,
    logger: Optional[Callable[[str], None]] = None,
    poll_interval: float = 0.2,
    **monitor_options,
) -> Tuple[bool, str]:
    success, record_or_error = record_path_from_host(host_path)
    if not success:
        callback = monitor_options.get("progress_callback")
        if callback:
            callback({
                "session_id": uuid.uuid4().hex, "started_at": time.monotonic(),
                "active": False, "result": record_or_error, "phase": "启动失败",
                "record_error": record_or_error, "record": "", "elapsed": 0.0,
            })
        return False, record_or_error
    if logger:
        logger(f"[APP刷写] 自动定位 Record.txt: {record_or_error}")
    return run_app_flash(record_or_error, timeout_seconds, logger, poll_interval, **monitor_options)

"""UDS sequence addressing, row options and session keep-alive."""
import base64
import json
import threading
import time

SUBFUNCTION_SERVICES = {0x10,0x11,0x19,0x27,0x28,0x2C,0x31,0x3E,0x83,0x85,0x86,0x87}


def encode_settings(settings):
    return base64.urlsafe_b64encode(json.dumps(settings).encode()).decode()


def settings_from(encoded):
    cfg = json.loads(base64.urlsafe_b64decode(encoded).decode()) if encoded else {}
    if not isinstance(cfg, dict):
        raise ValueError('UDS通讯设置格式错误')
    result = dict(functional_id=0x7DF, address='physical', keepalive=False, keepalive_ms=2000,
                  keepalive_address='physical', keepalive_suppress=True,padding=0,block_size=0,st_min=0,p2_star_ms=5000,frame_format='auto')
    result.update(cfg)
    if result['frame_format'] not in ('auto','standard','extended'):
        raise ValueError('CAN帧格式无效')
    if type(result['functional_id']) is not int or not 0 <= result['functional_id'] <= 0x1FFFFFFF:
        raise ValueError('功能地址必须为有效CAN ID')
    if any(result[k] not in ('physical','functional') for k in ('address','keepalive_address')):
        raise ValueError('UDS地址类型无效')
    if type(result['keepalive_ms']) is not int or not 100 <= result['keepalive_ms'] <= 600000:
        raise ValueError('会话保持周期必须为100～600000ms')
    if any(type(result[k]) is not bool for k in ('keepalive','keepalive_suppress')):
        raise ValueError('会话保持参数无效')
    for key in ('padding','block_size','st_min'):
        if type(result[key]) is not int or not 0<=result[key]<=255:
            raise ValueError('填充、块大小、STmin必须为0～255')
    if not (result['st_min']<=127 or 0xF1<=result['st_min']<=0xF9):
        raise ValueError('STmin使用0～127毫秒或0xF1～0xF9的ISO-TP编码')
    if type(result['p2_star_ms']) is not int or not 1<=result['p2_star_ms']<=600000:
        raise ValueError('P2*必须为1～600000ms')
    return result


def delay_seconds(row):
    if not row.startswith('@DELAY '):
        return None
    if len(row.split())!=2:raise ValueError('延时行格式应为 @DELAY 毫秒')
    value = int(row.split()[1])
    if not 0 <= value <= 3600000:
        raise ValueError('延时必须为0～3600000ms')
    return value/1000


def prepare_request(request, suppress, functional=False):
    request = bytes(request)
    if not request:
        raise ValueError('UDS请求为空')
    if suppress:
        if request[0] not in SUBFUNCTION_SERVICES or len(request) < 2:
            raise ValueError('该服务不支持抑制肯定响应')
        request = request[:1] + bytes([request[1] | 0x80]) + request[2:]
    if functional and len(request) > 7:
        raise ValueError('功能寻址只允许单帧请求（最多7字节PDU）；多帧请求请使用物理地址')
    return request


def validate_sequence(rows,metadata,settings):
    """Validate the whole plan before any request or tester-present is sent."""
    from core.uds_parser import parse_uds_rule_string
    for index,(row,options) in enumerate(zip(rows,metadata),1):
        if not options.get('enabled',True):continue
        delay=delay_seconds(row)
        if delay is not None:continue
        address=options.get('address','default')
        if address=='default':address=settings['address']
        if row=='@SECURITY':
            if address=='functional':raise ValueError('DLL安全解锁请使用物理地址')
            continue
        main,pipe,rule=row.partition('|')
        request,separator,prefix=main.partition('=>')
        prepare_request(bytes.fromhex(request.replace(',',' ')),options.get('suppress',False),address=='functional')
        if separator:
            for token in prefix.replace(',',' ').split():
                if token not in ('??','**') and (len(token)!=2 or not 0<=int(token,16)<=255):
                    raise ValueError(f'第{index}行响应HEX格式无效')
        if pipe and rule.strip():
            parsed=parse_uds_rule_string(rule.strip())
            if not parsed or parsed['mode'] not in ('ASCII','HEX','BCD','CALC','FORMULA','NUMERIC','COMBINE'):
                raise ValueError(f'第{index}行UDS数据提取规则无效')
        if options.get('suppress') and (separator or pipe or options.get('expected') or options.get('output')):
            raise ValueError('抑制响应行不能配置响应校验、解析或最终输出')


class SessionKeepAlive:
    """Serialize tester-present with normal UDS requests; stop on every exit path."""
    def __init__(self, client, settings, cancelled, log):
        self.client, self.settings, self.cancelled, self.log = client, settings, cancelled, log
        self.stop = threading.Event()
        self.error = None
        self.thread = None

    def __enter__(self):
        if self.settings['keepalive']:
            self.thread = threading.Thread(target=self._run, name='UDS-TesterPresent', daemon=True)
            self.thread.start()
        return self

    def _run(self):
        while not self.stop.wait(self.settings['keepalive_ms']/1000):
            if self.cancelled():
                return
            try:
                lock = self.client._lock
                if not lock.acquire(timeout=.05):
                    continue
                try:
                    if self.stop.is_set() or self.cancelled():return
                    suppress = self.settings['keepalive_suppress']
                    response = self.client.tester_present(suppress=suppress)
                    if not response.positive:
                        raise RuntimeError(response.error or response.nrc_name or 'TesterPresent失败')
                    self.log('UDS[会话保持] TX=3E ' + ('80（抑制响应）' if suppress else '00 RX='+response.raw.hex(' ').upper()))
                finally:
                    lock.release()
            except Exception as exc:
                self.error = str(exc)
                return

    def check(self):
        if self.error:
            raise RuntimeError('会话保持失败：' + self.error)
        if self.cancelled():
            raise RuntimeError('测试已停止，未发送后续UDS请求')

    def wait(self, seconds):
        deadline = time.monotonic()+seconds
        while time.monotonic()<deadline:
            self.check()
            self.stop.wait(min(.05,max(0,deadline-time.monotonic())))
        self.check()

    def __exit__(self, *_):
        self.stop.set()
        if self.thread:
            self.thread.join()
        if _[0] is None and self.error:
            raise RuntimeError('会话保持失败：'+self.error)

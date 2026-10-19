"""Shared HEX IDs and response matching for editor and execution."""
def can_id(value):
    result=value if isinstance(value,int) else int(str(value).strip(),16)
    if not 0<=result<=0x1FFFFFFF:raise ValueError('CAN ID必须为0～0x1FFFFFFF的十六进制数')
    return result


def response_matcher(response_id,frame_format='auto',prefix=''):
    if frame_format not in ('auto','standard','extended'):raise ValueError('响应帧格式无效')
    expected_extended=response_id>0x7FF if frame_format=='auto' else frame_format=='extended'
    if not expected_extended and response_id>0x7FF:raise ValueError('标准帧ID不能超过0x7FF')
    expected=bytes.fromhex(prefix)
    def matches(message):
        if message.get('is_error_frame') or message.get('is_remote_frame'):return False
        if 'is_extended_id' in message and bool(message['is_extended_id'])!=expected_extended:return False
        return bytes(message.get('data',b'')).startswith(expected)
    return matches

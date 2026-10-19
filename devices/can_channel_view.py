"""A physical-channel view of one shared adapter; never reconnects or switches it."""
class CANChannelView:
    def __init__(self, driver, physical_channel):
        self.driver = driver
        self.physical_channel = int(physical_channel)
        maximum = 4 if driver.channel.startswith('ZLG_CAN') else 1
        if not 1 <= self.physical_channel <= maximum:
            raise ValueError(f'当前配方CAN卡物理通道必须为CH1–CH{maximum}')
        if maximum == 4 and driver.bus is not None:
            enabled = getattr(driver.bus, 'enabled_channels', set(range(4)))
            if isinstance(enabled, set) and self.physical_channel-1 not in enabled:
                raise ValueError('所选ZLG物理通道未启用')

    def __getattr__(self, name):
        return getattr(self.driver, name)

    def send_can_message(self, channel_id, can_id, can_type, dlc, data,extended_id=None):
        extra = {} if extended_id is None else {'extended_id':extended_id}
        return self.driver.send_can_message(channel_id, can_id, can_type, dlc, data,
                                            physical_channel=self.physical_channel,**extra)

    def wait_for_message(self, *args, **kwargs):
        kwargs['physical_channel'] = self.physical_channel
        return self.driver.wait_for_message(*args, **kwargs)

    def clear_rx_history(self, can_id=None, channel_id=None):
        index = self.physical_channel-1 if self.driver.channel.startswith('ZLG_CAN') else None
        return self.driver.clear_rx_history(can_id, index)

    def send_and_wait_response(self, channel_id, can_id, can_type, dlc, data,
                               response_id, timeout=1.0, matcher=None):
        import time
        with self.driver._request_lock:
            self.clear_rx_history(response_id)
            sent_at = time.time()
            if not self.send_can_message(channel_id,can_id,can_type,dlc,data):
                raise ConnectionError(self.driver.last_error or 'CAN报文发送失败')
            return self.wait_for_message(response_id,predicate=matcher,timeout=timeout,since_time=sent_at)

    def create_uds_client(self, tx_id=0x7E0, rx_id=0x7E8, can_fd=False, brs=False,
                          timeout=1.0, padding=0, block_size=0, st_min=0, p2_star_timeout=5.0):
        from .uds_client import ISOTPTransport, UDSClient
        transport=ISOTPTransport(self,tx_id,rx_id,can_fd,brs,timeout,padding,block_size,st_min)
        return UDSClient(transport,p2_timeout=timeout,p2_star_timeout=p2_star_timeout)

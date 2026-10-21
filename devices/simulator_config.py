"""电池模拟器配置及按启用设备序号连续排列的全局通道。"""


def channel_routes(entries):
    """Map (device number, channel count, device) entries to global channels."""
    entries = sorted(entries, key=lambda entry: entry[0])
    numbers = [number for number, _, _ in entries]
    if len(numbers) != len(set(numbers)):
        raise ValueError('已启用模拟器的设备序号重复。')
    routes = []
    for number, count, device in entries:
        if type(number) is not int or not 1 <= number <= 6 or type(count) is not int or not 1 <= count <= 24:
            raise ValueError('模拟器序号必须为 1–6，通道数量必须为 1–24。')
        for local in range(1, count + 1):
            routes.append((len(routes) + 1, device, local))
    return routes


def simulator_settings(cfg):
    settings = []
    used = set()
    for slot in range(1, 7):
        key = f"sim{slot}"
        enabled = bool(cfg.get(f"{key}_enabled", slot == 1))
        number = int(cfg.get(f"{key}_number", slot))
        channels = int(cfg.get(f"{key}_channels", 1 if slot == 1 else 24))
        if not 1 <= number <= 6 or not 1 <= channels <= 24:
            raise ValueError("模拟器序号必须为 1–6，通道数量必须为 1–24。")
        if enabled and number in used:
            raise ValueError(f"已启用模拟器的序号 {number} 重复。")
        if enabled:
            used.add(number)
        settings.append(dict(slot=slot, number=number, channels=channels,
                             enabled=enabled, ip=cfg.get(f"{key}_ip", f"192.168.1.{209 + slot}"),
                             port=int(cfg.get(f"{key}_port", 7000))))
    return settings

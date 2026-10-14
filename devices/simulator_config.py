"""电池模拟器配置及固定 24 通道编号规则。"""
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

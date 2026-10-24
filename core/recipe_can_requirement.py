"""Validate recipe CAN requirements and the hardware enable state."""


def validate_recipe_can(recipe, *, hardware_enabled=True):
    from core.can_step_channels import validate_step_channels
    validate_step_channels(recipe)
    from core.can_periodic import validate_plan
    validate_plan(recipe,debug=bool(recipe.get('_debug_run')),
                  active_names=recipe.get('_active_debug_periodic',()))
    config = recipe.get('can_config')
    disabled = 'can_config' in recipe and (not config or config.get('enabled') is False)
    if not disabled and hardware_enabled:
        return
    for item in recipe.get('items', []):
        if item.get('skip_runtime') or str(item.get('name', '')).strip().startswith('#'):
            continue
        for step in item.get('sub_steps', []):
            if step.get('skip_runtime') or str(step.get('name', '')).strip().startswith('#'):
                continue
            text = ' '.join(str(step.get(k, '')) for k in ('device', 'type', 'stype')).upper()
            requires = any(word in text for word in ('CAN', 'UDS', 'EOL', '报文交互'))
            requires |= 'APP刷写' in text and '查找' not in str(step.get('action', '')) and 'APP刷写配置' not in text
            if requires:
                reason = '配方选择了“不使用 CAN”' if disabled else '硬件管理中心未启用 CAN'
                guidance = '请选择 PCAN 或 ZLG。' if disabled else '请先在硬件管理中心启用并初始化 CAN 设备。'
                raise ValueError(f"{reason}，但测试项【{item.get('name', '')}】的工步【{step.get('name') or step.get('action') or text}】需要 CAN 卡，{guidance}")

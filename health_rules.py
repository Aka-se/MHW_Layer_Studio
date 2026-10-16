"""One threshold configuration shared by preview, projects and rule generation."""
DEFAULT_THRESHOLDS = {60: 80, 61: 60, 62: 40, 63: 20}
LAYER_NAMES = {60: "装饰层", 61: "护甲层", 62: "内层", 63: "第4层"}


def restore_thresholds(values, *, legacy=False):
    result = validate_thresholds(values)
    # Upgrade only the previous standard preset. Keep custom values and an
    # intentionally removed fourth layer in new-format saves exactly as saved.
    if legacy and result == {60: 80, 61: 60, 62: 40}:
        result[63] = 20
    return result


def layer_name(group, thresholds):
    if group == 0:
        return "基础 / 原始"
    if group in LAYER_NAMES:
        return LAYER_NAMES[group]
    order = sorted(thresholds, key=lambda g: (-thresholds[g], g))
    return f"第 {order.index(group) + 1} 层" if group in order else f"自定义 Group {group}"


def validate_thresholds(values=None):
    if values is None:
        return DEFAULT_THRESHOLDS.copy()
    if not isinstance(values, dict) or not 1 <= len(values) <= 32:
        raise ValueError("请保留 1–32 个血量层级")
    normalized = {}
    for key, value in values.items():
        if type(key) is not int and (not isinstance(key, str) or not key.isascii() or not key.isdecimal()):
            raise ValueError("Group ID 请输入 1–65535 的整数；0 保留为基础层")
        group = int(key)
        if not 1 <= group <= 65535:
            raise ValueError("Group ID 请输入 1–65535 的整数；0 保留为基础层")
        if group in normalized:
            raise ValueError("每个层级的 Group ID 必须不同")
        if type(value) is not int or not 0 <= value <= 100:
            raise ValueError("隐藏百分比请输入 0–100 的整数")
        normalized[group] = value
    return normalized

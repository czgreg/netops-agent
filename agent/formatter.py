def render_diagnosis(text: str) -> str:
    stripped = (text or "").strip()
    if not stripped:
        return "【诊断结果】\n- 最可能故障点：未知\n- 置信度：低\n- 关键证据：暂无\n- 建议操作：补充排障命令后重试\n- 下一步检查：show interface / show ip route / show arp"
    return stripped


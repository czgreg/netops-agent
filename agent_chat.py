import asyncio
from getpass import getpass
import os

from agent.core import NetOpsAgent
from agent.env_loader import load_dotenv


async def main() -> None:
    load_dotenv()
    provider = os.getenv("AI_PROVIDER", "zhipu").strip().lower() or "zhipu"
    base_url = os.getenv("AI_BASE_URL", "").strip() or (
        "https://open.bigmodel.cn/api/paas/v4/" if provider == "zhipu" else ""
    )
    model = os.getenv("AI_TEXT_MODEL", "").strip() or os.getenv("AI_MODEL", "").strip() or None
    auto_select_model = os.getenv("AUTO_SELECT_TEXT_MODEL", "false").strip().lower() in {"1", "true", "yes", "on"}
    api_key_var = "ZHIPU_API_KEY" if provider == "zhipu" else "OPENAI_API_KEY"
    api_key = os.getenv(api_key_var, "").strip()
    if not api_key:
        api_key = getpass(f"请输入 {api_key_var} > ").strip()
    if not api_key:
        print("未输入 API Key，程序退出。")
        return

    agent = NetOpsAgent(
        api_key=api_key,
        provider=provider,
        base_url=base_url,
        model=model,
        auto_select_model=auto_select_model,
    )
    info = agent.get_model_info()
    print(
        f"🧠 NetOps AI 助手已启动（provider={info['provider']} model={info['model']} source={info['source']}）"
    )
    print("输入 exit/quit 退出\n")

    while True:
        try:
            user_text = input("请输入问题 > ").strip()
        except (EOFError, KeyboardInterrupt):
            user_text = "quit"
        if not user_text:
            continue
        if user_text.lower() in {"exit", "quit"}:
            break

        print("\n🔍 正在排查...\n")
        try:
            result = await agent.ask(user_text)
            print(result.answer + "\n")
        except Exception as exc:  # pylint: disable=broad-except
            print(
                "【诊断结果】\n"
                "- 最可能故障点：排障流程中断\n"
                "- 置信度：低\n"
                f"- 关键证据：{exc}\n"
                "- 建议操作：检查 netpilot-mcp 与设备连接信息\n"
                "- 下一步检查：先执行 device_connect + show version\n"
            )


if __name__ == "__main__":
    asyncio.run(main())

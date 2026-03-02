import asyncio
import json
import re
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Tuple

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from openai import OpenAI


SYSTEM_PROMPT = """
你是资深 Cisco 网络排障专家。

核心排障规则：
1) 结论必须基于工具返回的实际证据，禁止凭空猜测。
2) 拓扑描述中包含设备登录信息（host/port/protocol），直接使用，无需再向用户确认。
3) 设备类型为 cisco_ios，连接时设置 device_type="cisco_ios"。
4) 这些设备通过 telnet 连接，不需要用户名和密码，直接连接即可。
5) 优先最小必要检查：ping → show ip route → show ip ospf neighbor → show interfaces。
6) 排障策略：先检查源设备(源IP所在路由器)，再检查目的设备(目的IP所在路由器)，最后检查中间路径。
7) 每台设备检查完毕后必须 device_disconnect 释放会话，再连接下一台。
8) 工具步骤有限（最多20步），请高效使用，避免重复调用。

输出格式固定：
【诊断结果】
- 最可能故障点：
- 置信度：
- 关键证据：
- 建议操作：
- 下一步检查：
"""

NO_SESSION_TOOLS = {"device_connect", "device_list_sessions"}
IPV4_RE = re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b")


@dataclass
class ModelInfo:
    provider: str
    model: str
    source: str
    candidates: List[str] = field(default_factory=list)


@dataclass
class AgentResult:
    answer: str
    confidence: int
    evidence: List[str] = field(default_factory=list)
    tool_calls: List[Dict[str, Any]] = field(default_factory=list)
    auto_hop_report: Dict[str, Any] = field(default_factory=dict)


class NetOpsAgent:
    def __init__(
        self,
        api_key: str,
        model: Optional[str] = None,
        max_tool_steps: int = 20,
        mcp_command: str = "netpilot-mcp",
        provider: str = "zhipu",
        base_url: Optional[str] = None,
        auto_select_model: bool = True,
    ) -> None:
        self.provider = (provider or "zhipu").lower()
        self.base_url = base_url or self._default_base_url(self.provider)
        self.client = OpenAI(api_key=api_key, base_url=self.base_url) if self.base_url else OpenAI(api_key=api_key)
        self.max_tool_steps = max_tool_steps
        self.mcp_command = mcp_command
        self.messages: List[Dict[str, Any]] = [{"role": "system", "content": SYSTEM_PROMPT}]
        self.last_connect_profile: Dict[str, Any] = {}
        self.model_info = self._resolve_model(model=model, auto_select_model=auto_select_model)
        self.model = self.model_info.model

    async def ask(
        self,
        user_text: str,
        image_data_url: Optional[str] = None,
        trace_hook: Optional[Callable[[str, Dict[str, Any]], None]] = None,
    ) -> AgentResult:
        try:
            self._emit_trace(trace_hook, "stage", {"name": "解析问题", "detail": "正在解析用户问题与目标连通性。"})
            image_notice = ""
            runtime_image = image_data_url
            if image_data_url and not self._supports_image_input():
                runtime_image = None
                image_notice = f"提示：当前模型 {self.model} 不支持图片输入，已自动按纯文本继续排障。\n"

            user_msg, user_history_msg = self._build_user_message(user_text, runtime_image)
            self.messages.append(user_msg)
            self._emit_trace(trace_hook, "stage", {"name": "准备检查项", "detail": "已生成最小必要检查步骤。"})
            tool_history: List[Dict[str, Any]] = []
            auto_hop_report: Dict[str, Any] = {}
            active_session_id: Optional[str] = None

            server = StdioServerParameters(command=self.mcp_command)
            async with stdio_client(server) as (read, write):
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    tool_defs = self._build_tool_schema(await session.list_tools())

                    answer = ""
                    for _ in range(self.max_tool_steps):
                        self._emit_trace(trace_hook, "stage", {"name": "模型推理", "detail": "正在决策下一步工具调用。"})
                        response = await asyncio.to_thread(
                            self.client.chat.completions.create,
                            model=self.model,
                            messages=self.messages,
                            tools=tool_defs,
                            tool_choice="auto",
                        )
                        msg = response.choices[0].message
                        tool_calls = list(msg.tool_calls or [])

                        if not tool_calls:
                            answer = msg.content or ""
                            break

                        assistant_tool_msg = {
                            "role": "assistant",
                            "content": msg.content or "",
                            "tool_calls": [tc.model_dump() for tc in tool_calls],
                        }
                        self.messages.append(assistant_tool_msg)

                        for call in tool_calls:
                            tool_name = call.function.name
                            args = self._parse_json_args(call.function.arguments)
                            if active_session_id and tool_name not in NO_SESSION_TOOLS:
                                args.setdefault("session_id", active_session_id)

                            self._emit_trace(
                                trace_hook,
                                "tool_start",
                                {"name": tool_name, "args": self._to_jsonable(args)},
                            )
                            result, err = await self._safe_call_tool(session, tool_name, args)
                            tool_history.append(
                                {
                                    "name": tool_name,
                                    "args": args,
                                    "ok": err is None,
                                    "result": result if err is None else {"error": err},
                                }
                            )
                            self._emit_trace(
                                trace_hook,
                                "tool_result",
                                {
                                    "name": tool_name,
                                    "ok": err is None,
                                    "result": self._to_jsonable(result if err is None else {"error": err}),
                                },
                            )

                            if tool_name == "device_connect" and err is None and isinstance(result, dict):
                                sid = result.get("session_id")
                                if sid:
                                    active_session_id = str(sid)
                                self.last_connect_profile = {
                                    k: v
                                    for k, v in args.items()
                                    if k in {"protocol", "port", "username", "password", "enable_password", "device_type"}
                                }
                            if tool_name == "device_disconnect":
                                active_session_id = None

                            payload: Any = result if err is None else {"error": err}
                            if tool_name == "device_traceroute" and err is None:
                                hop_ips = self._extract_hop_ips(result)
                                if hop_ips:
                                    auto_hop_report = await self._auto_multi_hop_diagnose(
                                        session=session,
                                        hop_ips=hop_ips,
                                        base_profile=self.last_connect_profile,
                                    )
                                    payload = {
                                        "traceroute_result": result,
                                        "auto_multi_hop": auto_hop_report,
                                    }

                            self.messages.append(
                                {
                                    "role": "tool",
                                    "tool_call_id": call.id,
                                    "content": json.dumps(payload, ensure_ascii=False),
                                }
                            )

                    if not answer:
                        answer = (
                            "【诊断结果】\n"
                            "- 最可能故障点：故障点未收敛\n"
                            "- 置信度：中\n"
                            "- 关键证据：工具调用达到上限\n"
                            "- 建议操作：缩小问题范围后重试\n"
                            "- 下一步检查：show interface / show ip route / show arp"
                        )

                    if active_session_id:
                        await self._safe_call_tool(session, "device_disconnect", {"session_id": active_session_id})

            self._emit_trace(trace_hook, "stage", {"name": "汇总结论", "detail": "正在基于证据生成诊断结论。"})
            confidence, evidence = self._score_confidence(tool_history, auto_hop_report)
            final_answer = self._normalize_answer(answer, confidence, evidence)
            if image_notice:
                final_answer = image_notice + final_answer
            self.messages[-1] = user_history_msg
            self.messages.append({"role": "assistant", "content": final_answer})
            self._emit_trace(
                trace_hook,
                "done",
                {
                    "confidence": confidence,
                    "evidence": evidence[:5],
                    "tool_calls": len(tool_history),
                },
            )
            return AgentResult(
                answer=final_answer,
                confidence=confidence,
                evidence=evidence,
                tool_calls=tool_history,
                auto_hop_report=auto_hop_report,
            )
        except Exception as exc:
            self._emit_trace(trace_hook, "error", {"error": str(exc)})
            raise RuntimeError(self._friendly_error(exc)) from exc

    def _emit_trace(
        self,
        trace_hook: Optional[Callable[[str, Dict[str, Any]], None]],
        event: str,
        payload: Dict[str, Any],
    ) -> None:
        if not trace_hook:
            return
        try:
            trace_hook(event, payload)
        except Exception:
            return

    async def _auto_multi_hop_diagnose(
        self,
        *,
        session: ClientSession,
        hop_ips: List[str],
        base_profile: Dict[str, Any],
    ) -> Dict[str, Any]:
        if not base_profile:
            return {"status": "skipped", "reason": "missing_connect_profile_from_dialog"}

        report: Dict[str, Any] = {"status": "ok", "checked_hops": 0, "hops": []}
        for hop in hop_ips[:4]:
            connect_args: Dict[str, Any] = {"host": hop}
            connect_args.update(base_profile)
            conn, err = await self._safe_call_tool(session, "device_connect", connect_args)
            if err or not isinstance(conn, dict) or not conn.get("session_id"):
                report["hops"].append({"hop": hop, "ok": False, "error": err or "connect failed"})
                continue

            sid = str(conn["session_id"])
            iface, iface_err = await self._safe_call_tool(
                session, "device_get_info", {"info_type": "interfaces", "session_id": sid}
            )
            route, route_err = await self._safe_call_tool(
                session, "device_get_info", {"info_type": "routing", "session_id": sid}
            )
            arp, arp_err = await self._safe_call_tool(session, "device_get_info", {"info_type": "arp", "session_id": sid})
            await self._safe_call_tool(session, "device_disconnect", {"session_id": sid})

            report["checked_hops"] += 1
            report["hops"].append(
                {
                    "hop": hop,
                    "ok": True,
                    "interfaces": iface if iface_err is None else {"error": iface_err},
                    "routing": route if route_err is None else {"error": route_err},
                    "arp": arp if arp_err is None else {"error": arp_err},
                }
            )

        return report

    async def _safe_call_tool(self, session: ClientSession, tool_name: str, args: Dict[str, Any]) -> Tuple[Any, Optional[str]]:
        try:
            print(f"🔧 调用工具: {tool_name} {args}")
            result = await session.call_tool(tool_name, args)
            return self._to_jsonable(result), None
        except Exception as exc:  # pylint: disable=broad-except
            return None, str(exc)

    def _to_jsonable(self, value: Any) -> Any:
        if value is None or isinstance(value, (str, int, float, bool)):
            return value
        if isinstance(value, dict):
            return {str(k): self._to_jsonable(v) for k, v in value.items()}
        if isinstance(value, (list, tuple, set)):
            return [self._to_jsonable(v) for v in value]
        if hasattr(value, "model_dump"):
            try:
                return self._to_jsonable(value.model_dump())
            except Exception:
                pass
        if hasattr(value, "dict"):
            try:
                return self._to_jsonable(value.dict())
            except Exception:
                pass
        if hasattr(value, "__dict__"):
            try:
                return self._to_jsonable(vars(value))
            except Exception:
                pass
        return str(value)

    def _build_tool_schema(self, mcp_tools: Any) -> List[Dict[str, Any]]:
        tools: List[Dict[str, Any]] = []
        for t in mcp_tools.tools:
            tools.append(
                {
                    "type": "function",
                    "function": {
                        "name": t.name,
                        "description": t.description or "",
                        "parameters": t.inputSchema or {"type": "object"},
                    },
                }
            )
        return tools

    def _parse_json_args(self, raw: Optional[str]) -> Dict[str, Any]:
        if not raw:
            return {}
        try:
            data = json.loads(raw)
            return data if isinstance(data, dict) else {}
        except json.JSONDecodeError:
            return {}

    def _build_user_message(self, user_text: str, image_data_url: Optional[str]) -> Tuple[Dict[str, Any], Dict[str, Any]]:
        text = (user_text or "").strip()
        img = (image_data_url or "").strip()
        if img and not img.startswith("data:image/"):
            raise ValueError("image_data_url 必须是 data:image/... 的 Base64 URL")

        if img:
            visible_text = text or "请结合这张拓扑图进行网络排障分析。"
            content = [
                {"type": "text", "text": visible_text},
                {"type": "image_url", "image_url": {"url": img}},
            ]
            runtime_msg = {"role": "user", "content": content}
            history_msg = {"role": "user", "content": f"{visible_text}（已上传拓扑图）"}
            return runtime_msg, history_msg

        return {"role": "user", "content": text}, {"role": "user", "content": text}

    def _extract_hop_ips(self, traceroute_result: Any) -> List[str]:
        hops: List[str] = []
        if isinstance(traceroute_result, dict):
            raw_hops = traceroute_result.get("hops", [])
            if isinstance(raw_hops, list):
                for h in raw_hops:
                    text = h if isinstance(h, str) else json.dumps(h, ensure_ascii=False)
                    hops.extend(IPV4_RE.findall(text))
        uniq: List[str] = []
        for ip in hops:
            if ip not in uniq:
                uniq.append(ip)
        return uniq

    def _score_confidence(self, tool_history: List[Dict[str, Any]], auto_hop_report: Dict[str, Any]) -> Tuple[int, List[str]]:
        score = 35
        evidence: List[str] = []

        def contains(text: str, keywords: List[str]) -> bool:
            lower = text.lower()
            return any(k in lower for k in keywords)

        for item in tool_history:
            name = item["name"]
            result = item.get("result")
            text = json.dumps(result, ensure_ascii=False).lower()
            if name == "device_ping":
                if contains(text, ['"reachable": false', "unreachable", "100% packet loss"]):
                    score += 12
                    evidence.append("Ping 不可达")
                if contains(text, ['"reachable": true', "0% packet loss"]):
                    score -= 8
                    evidence.append("Ping 可达（存在冲突证据）")
            if name == "device_traceroute" and contains(text, ["*", "timeout", "unreachable"]):
                score += 18
                evidence.append("Traceroute 出现中断/超时")
            if name in {"device_execute", "device_get_info", "device_get_config"}:
                if contains(text, ["down", "administratively down", "line protocol is down"]):
                    score += 22
                    evidence.append("接口存在 down 证据")
                if contains(text, ["gateway of last resort is not set", "not in table", "no route"]):
                    score += 18
                    evidence.append("路由缺失/不可达证据")
                if contains(text, ["incomplete", "arp fail", "not found"]):
                    score += 10
                    evidence.append("ARP 异常证据")

        if auto_hop_report.get("status") == "ok" and auto_hop_report.get("checked_hops", 0) > 0:
            score += 8
            evidence.append(f"跨设备自动检查 {auto_hop_report.get('checked_hops')} 个 hop")
            auto_text = json.dumps(auto_hop_report, ensure_ascii=False).lower()
            if any(k in auto_text for k in ["down", "not in table", "gateway of last resort is not set"]):
                score += 10
                evidence.append("跨设备检查发现异常证据")

        if not evidence:
            score -= 15
            evidence.append("有效证据不足")

        score = max(5, min(95, score))
        uniq_evidence: List[str] = []
        for e in evidence:
            if e not in uniq_evidence:
                uniq_evidence.append(e)
        return score, uniq_evidence[:6]

    def _normalize_answer(self, raw: str, confidence: int, evidence: List[str]) -> str:
        text = (raw or "").strip()
        if not text:
            text = (
                "【诊断结果】\n"
                "- 最可能故障点：未知\n"
                "- 关键证据：证据不足\n"
                "- 建议操作：补充连通性与接口检查\n"
                "- 下一步检查：show interface / show ip route / show arp"
            )

        lines = [l for l in text.splitlines() if l.strip()]
        out: List[str] = []
        has_header = any("诊断结果" in l for l in lines)
        if not has_header:
            out.append("【诊断结果】")
        for line in lines:
            if "置信度" in line:
                continue
            out.append(line)

        inserted = False
        final: List[str] = []
        for line in out:
            final.append(line)
            if "最可能故障点" in line and not inserted:
                final.append(f"- 置信度：{confidence}%")
                inserted = True
        if not inserted:
            final.append(f"- 置信度：{confidence}%")

        if not any("关键证据" in l for l in final):
            summary = "；".join(evidence[:3]) if evidence else "暂无"
            final.append(f"- 关键证据：{summary}")

        return "\n".join(final)

    def _friendly_error(self, exc: Exception) -> str:
        root = self._flatten_exception_message(exc).lower()
        if "insufficient_quota" in root or "exceeded your current quota" in root:
            return "API 配额不足（insufficient_quota），请充值或更换可用 Key。"
        if "code': '1113'" in root or "余额不足" in root:
            return "余额不足或无可用资源包（code 1113），请充值后重试。"
        if "invalid_api_key" in root or "incorrect api key" in root or "authentication" in root:
            return "API Key 无效，请检查配置。"
        if "rate limit" in root:
            return "API 请求限流，请稍后重试。"
        return self._flatten_exception_message(exc)

    def _flatten_exception_message(self, exc: BaseException) -> str:
        messages: List[str] = []

        def walk(e: BaseException) -> None:
            subs = getattr(e, "exceptions", None)
            if subs and isinstance(subs, (list, tuple)):
                for item in subs:
                    if isinstance(item, BaseException):
                        walk(item)
                return
            txt = str(e).strip()
            if txt:
                messages.append(txt)

        walk(exc)
        return " | ".join(messages) if messages else repr(exc)

    def _default_base_url(self, provider: str) -> Optional[str]:
        if provider == "zhipu":
            return "https://open.bigmodel.cn/api/paas/v4/"
        return None

    def _resolve_model(self, model: Optional[str], auto_select_model: bool) -> ModelInfo:
        if model:
            return ModelInfo(provider=self.provider, model=model, source="manual", candidates=[model])
        if not auto_select_model:
            fallback = "glm-4.5" if self.provider == "zhipu" else "gpt-5"
            return ModelInfo(provider=self.provider, model=fallback, source="fallback", candidates=[fallback])

        try:
            listed = self.client.models.list()
            ids = [x.id for x in getattr(listed, "data", []) if getattr(x, "id", None)]
            if not ids:
                raise ValueError("no_models")
            model_id = self._pick_latest_model(ids)
            return ModelInfo(provider=self.provider, model=model_id, source="auto", candidates=ids[:20])
        except Exception:
            fallback = "glm-4.5" if self.provider == "zhipu" else "gpt-5"
            return ModelInfo(provider=self.provider, model=fallback, source="fallback", candidates=[fallback])

    def _pick_latest_model(self, ids: List[str]) -> str:
        lowered = [i.lower() for i in ids]
        pairs = list(zip(ids, lowered))

        # Prefer stable GLM family first for zhipu.
        if self.provider == "zhipu":
            glm = [p for p in pairs if p[1].startswith("glm-")]
            if glm:
                glm.sort(key=lambda p: self._glm_sort_key(p[1]), reverse=True)
                return glm[0][0]

        # Generic fallback: choose lexicographically highest id.
        return sorted(ids)[-1]

    def _glm_sort_key(self, model_id: str) -> Tuple[int, int, int, int]:
        # e.g., glm-5, glm-4.7, glm-4.7-flash
        m = re.search(r"glm-(\d+)(?:\.(\d+))?(?:\.(\d+))?", model_id)
        major = int(m.group(1)) if m else 0
        minor = int(m.group(2)) if m and m.group(2) else 0
        patch = int(m.group(3)) if m and m.group(3) else 0
        suffix_rank = 0
        if "flash" in model_id:
            suffix_rank = -2
        if "air" in model_id:
            suffix_rank = -1
        return major, minor, patch, suffix_rank

    def get_model_info(self) -> Dict[str, Any]:
        return {
            "provider": self.model_info.provider,
            "model": self.model_info.model,
            "source": self.model_info.source,
            "supports_image": self._supports_image_input(),
            "candidates": self.model_info.candidates[:10],
        }

    def _supports_image_input(self) -> bool:
        m = self.model.lower()
        # zhipu vision models commonly include: 4v / 4.1v / -vl / vision
        return any(tag in m for tag in ["4v", "1v", "vision", "-vl", "_vl", "multimodal"])

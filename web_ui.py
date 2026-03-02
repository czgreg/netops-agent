import asyncio
import concurrent.futures
import json
import os
import secrets
import re
import threading
import time
from http import HTTPStatus
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Dict
import urllib.error
import urllib.request

from agent.core import NetOpsAgent
from agent.env_loader import load_dotenv
from openai import OpenAI


HOST = "127.0.0.1"
PORT = 8787
CHAT_TIMEOUT_SECONDS = 1800
SID_COOKIE = "netops_sid"
AGENTS: Dict[str, NetOpsAgent] = {}
TOPOLOGY_CONTEXTS: Dict[str, str] = {}
VISION_PROMPTS: Dict[str, str] = {}
TRACE_STATES: Dict[str, dict] = {}
TRACE_LOCK = threading.Lock()
DEFAULT_API_KEY = ""
DEFAULT_PROVIDER = "zhipu"
DEFAULT_BASE_URL = "https://open.bigmodel.cn/api/paas/v4/"
DEFAULT_TEXT_MODEL = "glm-4.7"
DEFAULT_VISION_MODEL = "glm-4.6v"
DEFAULT_VISION_FALLBACK_MODEL = ""
AUTO_SELECT_TEXT_MODEL = False
TEXT_MODEL_INFO = {"provider": "", "model": "", "source": "", "candidates": []}
API_KEY_STATUS = {
    "configured": False,
    "usable": False,
    "message": "未检测",
    "provider": "",
    "text_model": "",
    "text_source": "",
    "vision_model": "",
    "vision_fallback_model": "",
    "vision_usable": False,
    "supports_image": False,
}

DEFAULT_VISION_PROMPT_TEXT = (
    "请根据图片输出一段拓扑结构描述（纯文本，不要JSON、不要代码块）。\n"
    "要求：\n"
    "1) 先描述核心拓扑结构和关键互联路径；\n"
    "2) 再补充设备角色、网段和区域；\n"
    "3) 尽可能完整但简洁，控制在8~14行；\n"
    "4) 不确定处标注“未识别”。"
)


HTML = """<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>NetOps Agent</title>
  <style>
    body{font-family:ui-sans-serif,system-ui,-apple-system;background:#f8fafc;color:#0f172a;max-width:1200px;margin:20px auto;padding:0 12px;}
    h2{margin:0 0 12px 0;}
    .header{display:flex;align-items:center;justify-content:space-between;gap:12px;margin-bottom:12px;}
    .layout{display:grid;grid-template-columns:1fr 1fr 1fr;gap:12px;height:calc(100vh - 110px);}
    .card{background:#fff;border:1px solid #e2e8f0;border-radius:10px;padding:12px;min-height:0;height:100%;display:flex;flex-direction:column;}
    .card h3{margin:0 0 10px 0;font-size:16px;}
    .row{display:flex;gap:8px;margin:8px 0;}
    textarea,input{width:100%;padding:10px;border:1px solid #cbd5e1;border-radius:8px;}
    button{padding:10px 14px;cursor:pointer;border:1px solid #cbd5e1;background:#fff;border-radius:8px;}
    .primary{background:#0f172a;color:#fff;border-color:#0f172a;}
    pre{white-space:pre-wrap;background:#0b1220;color:#e2e8f0;padding:12px;border-radius:8px;min-height:0;max-height:none;overflow:auto;flex:1;}
    small{color:#475569;}
    .badge{display:inline-block;padding:4px 10px;border-radius:999px;font-size:12px;}
    .ok{background:#dcfce7;color:#166534;}
    .bad{background:#fee2e2;color:#991b1b;}
    .warn{background:#fef3c7;color:#92400e;}
    .img-wrap{border:1px dashed #cbd5e1;border-radius:8px;min-height:220px;display:flex;align-items:center;justify-content:center;background:#f8fafc;overflow:hidden;}
    .img-wrap img{width:100%;height:auto;display:block;}
    .kv{font-size:13px;line-height:1.6;color:#334155;background:#f8fafc;border:1px solid #e2e8f0;border-radius:8px;padding:8px;}
    .topo-json{width:100%;min-height:180px;max-height:240px;resize:vertical;font-family:ui-monospace,SFMono-Regular,Menlo,Monaco,Consolas,monospace;font-size:12px;}
    .chat-box{display:flex;flex-direction:column;gap:10px;height:100%;}
    .chat-messages{flex:1;min-height:0;max-height:none;overflow:auto;background:#f8fafc;border:1px solid #e2e8f0;border-radius:8px;padding:10px;}
    .msg{max-width:86%;padding:8px 10px;border-radius:10px;margin:8px 0;white-space:pre-wrap;line-height:1.5;}
    .msg-user{margin-left:auto;background:#dbeafe;color:#1e3a8a;}
    .msg-assistant{margin-right:auto;background:#ecfeff;color:#134e4a;}
    .msg-status{margin:8px auto;background:#e2e8f0;color:#334155;font-size:12px;}
    .chat-input{width:100%;min-height:86px;max-height:140px;resize:vertical;}
    @media (max-width: 1080px){
      .layout{grid-template-columns:1fr;}
      .card{min-height:420px;}
    }
  </style>
</head>
<body>
  <div class="header">
    <h2>NetOps AI 助手</h2>
    <div>
      <div id="keyStatus" class="badge __KEY_STATUS_CLASS__">__KEY_STATUS_TEXT__</div>
      <div id="modelInfo" style="font-size:12px;color:#475569;margin-top:6px;">__MODEL_INFO_TEXT__</div>
    </div>
  </div>

  <div class="layout">
    <section class="card">
      <h3>拓扑 URL 与信息</h3>
      <div class="row"><input id="imgUrl" type="text" placeholder="输入可公网访问的拓扑图片 URL（必填）" /></div>
      <div class="img-wrap"><img id="imgPreview" alt="topology preview" style="display:none;" /></div>
      <div class="row">
        <button onclick="clearImage()">清空图片</button>
        <button onclick="extractTopology()">解析拓扑</button>
      </div>
      <div id="imgMeta" class="kv">未设置拓扑图片 URL</div>
      <div class="row">
        <textarea id="visionPrompt" class="topo-json" placeholder="可编辑视觉解析提示词"></textarea>
      </div>
      <div class="row">
        <textarea id="topologyJson" class="topo-json" placeholder="拓扑结构描述（可编辑确认）"></textarea>
      </div>
      <small>仅支持公网可访问 URL（例如对象存储、CDN 或图床链接）。</small>
    </section>

    <section class="card">
      <h3>对话输入与输出</h3>
      <div class="chat-box">
        <div id="chatMessages" class="chat-messages">
          <div class="msg msg-status">欢迎使用 NetOps AI 助手</div>
        </div>
        <textarea id="q" class="chat-input" placeholder="请输入问题，例如：10.0.0.11 到 10.0.2.20 不通"></textarea>
        <div class="row">
          <button id="sendBtn" class="primary" onclick="ask()">发送</button>
          <button onclick="clearLog()">清空对话</button>
          <button onclick="refreshStatus()">刷新 Key 状态</button>
        </div>
      </div>
      <small>连接信息通过对话获取：host/protocol/username/password/device_type。</small>
    </section>

    <section class="card">
      <h3>设备交互细节</h3>
      <pre id="details">暂无交互细节</pre>
      <small>显示本轮调用的工具、参数（已脱敏）与返回摘要。</small>
    </section>
  </div>

  <script>
    const chatMessages = document.getElementById('chatMessages');
    const details = document.getElementById('details');
    const keyStatus = document.getElementById('keyStatus');
    const modelInfo = document.getElementById('modelInfo');
    const imgUrlInput = document.getElementById('imgUrl');
    const imgMeta = document.getElementById('imgMeta');
    const imgPreview = document.getElementById('imgPreview');
    const visionPrompt = document.getElementById('visionPrompt');
    const topologyJson = document.getElementById('topologyJson');
    const sendBtn = document.getElementById('sendBtn');
    let traceTimer = null;
    visionPrompt.value = `请根据图片输出一段拓扑结构描述（纯文本，不要JSON、不要代码块）。
要求：
1) 先描述核心拓扑结构和关键互联路径；
2) 再补充设备角色、网段和区域；
3) 尽可能完整但简洁，控制在8~14行；
4) 不确定处标注“未识别”。`;
    function addMsg(role, text){
      const el = document.createElement('div');
      el.className = 'msg ' + (role === 'user' ? 'msg-user' : role === 'assistant' ? 'msg-assistant' : 'msg-status');
      el.textContent = text;
      chatMessages.appendChild(el);
      chatMessages.scrollTop = chatMessages.scrollHeight;
    }
    function clearLog(){
      chatMessages.innerHTML = '<div class="msg msg-status">欢迎使用 NetOps AI 助手</div>';
    }
    function clearImage(){
      imgUrlInput.value = "";
      imgMeta.textContent = "未设置拓扑图片 URL";
      imgPreview.style.display = "none";
      imgPreview.src = "";
      topologyJson.value = "";
    }
    imgUrlInput.addEventListener('change', () => {
      const url = imgUrlInput.value.trim();
      if(!url){
        clearImage();
        return;
      }
      imgMeta.textContent = "URL: " + url;
      imgPreview.src = url;
      imgPreview.style.display = "block";
    });
    function paintStatus(data){
      if(!data.configured){
        keyStatus.className = "badge warn";
        keyStatus.textContent = "API Key 未配置";
        return;
      }
      if(data.usable){
        keyStatus.className = "badge ok";
        keyStatus.textContent = "API Key 可用";
      }else{
        keyStatus.className = "badge bad";
        keyStatus.textContent = "API Key 不可用: " + (data.message || "未知错误");
      }
      const provider = data.provider || "unknown";
      const textModel = data.text_model || "unknown";
      const textSource = data.text_source || "unknown";
      const visionModel = data.vision_model || "unknown";
      const fallbackModel = data.vision_fallback_model || "none";
      const visionState = data.vision_usable ? "可用" : "不可用";
      const imgCap = data.supports_image ? "是" : "否";
      modelInfo.textContent = "Provider: " + provider + " | 文本模型: " + textModel + " (" + textSource + ") | 视觉模型: " + visionModel + " | 回退模型: " + fallbackModel + " | 视觉状态: " + visionState + " | 图片支持: " + imgCap;
    }
    function withTimeout(promise, ms){
      return Promise.race([
        promise,
        new Promise((_, reject) => setTimeout(() => reject(new Error("timeout")), ms))
      ]);
    }
    async function refreshStatus(){
      try{
        const resp = await withTimeout(fetch('/api/status'), 3500);
        if(!resp.ok){
          throw new Error("status " + resp.status);
        }
        const data = await resp.json();
        paintStatus(data);
      }catch(err){
        keyStatus.className = "badge bad";
        keyStatus.textContent = "API 状态获取失败";
        modelInfo.textContent = "模型信息获取失败: " + String(err && err.message ? err.message : err);
      }
    }
    async function ask(){
      const q = document.getElementById('q').value.trim();
      if(!q){return;}
      if(sendBtn){ sendBtn.disabled = true; sendBtn.textContent = "处理中..."; }
      addMsg('user', q);
      addMsg('status', "正在排查...（阶段 1/4：解析问题）");
      details.textContent = "执行轨迹\\n\\n- 阶段 1/4: 解析问题\\n- 阶段 2/4: 准备连接与检查项\\n- 阶段 3/4: 调用设备工具\\n- 阶段 4/4: 汇总结论\\n\\n当前状态: 请求已发送，等待 Agent 返回...";
      const topo = topologyJson.value.trim();
      const imageRef = imgUrlInput.value.trim();
      const vp = visionPrompt.value.trim();
      startTracePolling();
      try{
        const resp = await fetch('/api/chat', {
          method: 'POST',
          headers: {'Content-Type':'application/json'},
          body: JSON.stringify({message:q, image_data_url: imageRef, topology_context: topo, vision_prompt: vp})
        });
        let data = {};
        try{
          data = await resp.json();
        }catch(_e){
          data = {};
        }
        if(!resp.ok){
          addMsg('assistant', "请求失败: " + (data.error || ("HTTP " + resp.status)));
          return;
        }
        addMsg('assistant', data.answer || "排障完成，但返回为空。");
        if(data.trace){
          details.textContent = renderTrace(data.trace);
          details.scrollTop = details.scrollHeight;
        }else if(data.tool_calls && data.tool_calls.length){
          details.textContent = JSON.stringify(data.tool_calls, null, 2);
          details.scrollTop = details.scrollHeight;
        }else{
          details.textContent = "本轮未产生工具调用。";
        }
      }catch(err){
        addMsg('assistant', "请求异常: " + String(err && err.message ? err.message : err));
      }finally{
        stopTracePolling();
        if(sendBtn){ sendBtn.disabled = false; sendBtn.textContent = "发送"; }
      }
      document.getElementById('q').value = "";
    }
    function startTracePolling(){
      stopTracePolling();
      traceTimer = setInterval(async () => {
        try{
          const resp = await fetch('/api/trace');
          if(!resp.ok){ return; }
          const data = await resp.json();
          if(!data){ return; }
          details.textContent = renderTrace(data);
          details.scrollTop = details.scrollHeight;
          if(data.status === "completed" || data.status === "failed"){
            stopTracePolling();
          }
        }catch(_e){
          // keep silent in polling loop
        }
      }, 900);
    }
    function stopTracePolling(){
      if(traceTimer){
        clearInterval(traceTimer);
        traceTimer = null;
      }
    }
    function renderTrace(trace){
      const logic = (trace.logic || []).map((x, i) => (i + 1) + ". " + x).join("\\n");
      const p = trace.progress || {};
      const actions = (trace.actions || []).map((a) => {
        const mark = a.ok === true ? "✅" : (a.ok === false ? "❌" : "⏳");
        const argsTxt = prettyJson(expandJsonLike(a.args_raw !== undefined ? a.args_raw : (a.args_summary || {})));
        const resultTxt = prettyJson(expandJsonLike(a.result_raw !== undefined ? a.result_raw : (a.result_summary || "")));
        return mark + " [" + (a.index || 0) + "] " + (a.name || "unknown") + "\\n"
          + "参数:\\n" + indentText(argsTxt, 2) + "\\n"
          + "结果:\\n" + indentText(resultTxt, 2) + "\\n"
          + "----------------------------------------";
      }).join("\\n\\n");
      return "执行轨迹\\n\\n"
        + "状态: " + (trace.status || "running") + "\\n"
        + "阶段: " + (trace.stage || "处理中") + "\\n\\n"
        + "当前逻辑:\\n" + (logic || "暂无") + "\\n\\n"
        + "进度:\\n"
        + "- 总行动: " + ((p.total_actions === undefined || p.total_actions === null) ? 0 : p.total_actions) + "\\n"
        + "- 成功: " + ((p.success_actions === undefined || p.success_actions === null) ? 0 : p.success_actions) + "\\n"
        + "- 失败: " + ((p.failed_actions === undefined || p.failed_actions === null) ? 0 : p.failed_actions) + "\\n"
        + "- 完成度: " + ((p.completion === undefined || p.completion === null) ? 0 : p.completion) + "%\\n\\n"
        + "行动明细:\\n" + (actions || "本轮未产生工具调用。");
    }
    function prettyJson(v){
      try{
        if(typeof v === 'string'){
          const s = v.trim();
          if((s.startsWith('{') && s.endsWith('}')) || (s.startsWith('[') && s.endsWith(']'))){
            return JSON.stringify(JSON.parse(s), null, 2);
          }
          return s;
        }
        return JSON.stringify(v, null, 2);
      }catch(_e){
        return String(v || '');
      }
    }
    function indentText(s, n){
      const pad = ' '.repeat(n);
      return String(s || '').split('\\n').map(x => pad + x).join('\\n');
    }
    function expandJsonLike(v){
      if(typeof v === 'string'){
        const s = v.trim();
        if((s.startsWith('{') && s.endsWith('}')) || (s.startsWith('[') && s.endsWith(']'))){
          try{ return expandJsonLike(JSON.parse(s)); }catch(_e){ return v; }
        }
        return v;
      }
      if(Array.isArray(v)){
        return v.map(expandJsonLike);
      }
      if(v && typeof v === 'object'){
        const out = {};
        Object.keys(v).forEach((k) => {
          out[k] = expandJsonLike(v[k]);
        });
        return out;
      }
      return v;
    }
    async function extractTopology(){
      const imageRef = imgUrlInput.value.trim();
      const vp = visionPrompt.value.trim();
      if(!imageRef){
        addMsg('status', "请先填写公网图片 URL。");
        return;
      }
      addMsg('status', "正在解析拓扑...");
      const resp = await fetch('/api/extract_topology', {
        method: 'POST',
        headers: {'Content-Type':'application/json'},
        body: JSON.stringify({image_data_url: imageRef, vision_prompt: vp})
      });
      const data = await resp.json();
      if(!resp.ok){
        addMsg('assistant', "拓扑解析失败: " + (data.error || "未知错误"));
        return;
      }
      topologyJson.value = data.topology_text || "";
      addMsg('assistant', "拓扑解析完成，请确认/编辑左侧拓扑描述后继续排障。");
    }
    refreshStatus();
    setInterval(refreshStatus, 10000);
  </script>
</body>
</html>
"""


def render_html() -> str:
    if API_KEY_STATUS.get("configured") and API_KEY_STATUS.get("usable"):
        key_cls = "ok"
        key_text = "API Key 可用"
    elif API_KEY_STATUS.get("configured"):
        key_cls = "bad"
        key_text = "API Key 不可用: " + str(API_KEY_STATUS.get("message", "未知错误"))
    else:
        key_cls = "warn"
        key_text = "API Key 未配置"

    provider = API_KEY_STATUS.get("provider", "unknown")
    text_model = API_KEY_STATUS.get("text_model", "unknown")
    text_source = API_KEY_STATUS.get("text_source", "unknown")
    vision_model = API_KEY_STATUS.get("vision_model", "unknown")
    fallback = API_KEY_STATUS.get("vision_fallback_model", "") or "none"
    vision_state = "可用" if API_KEY_STATUS.get("vision_usable") else "不可用"
    img_cap = "是" if API_KEY_STATUS.get("supports_image") else "否"
    model_text = (
        f"Provider: {provider} | 文本模型: {text_model} ({text_source}) | 视觉模型: {vision_model} | "
        f"回退模型: {fallback} | 视觉状态: {vision_state} | 图片支持: {img_cap}"
    )

    return (
        HTML.replace("__KEY_STATUS_CLASS__", key_cls)
        .replace("__KEY_STATUS_TEXT__", key_text)
        .replace("__MODEL_INFO_TEXT__", model_text)
    )


class Handler(BaseHTTPRequestHandler):
    def _sid(self) -> str:
        cookie = SimpleCookie(self.headers.get("Cookie"))
        sid = cookie.get(SID_COOKIE)
        if sid and sid.value:
            return sid.value
        return secrets.token_hex(16)

    def _send_json(self, code: int, payload: dict, sid: str) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Set-Cookie", f"{SID_COOKIE}={sid}; Path=/; HttpOnly")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:  # noqa: N802
        if self.path == "/api/status":
            sid = self._sid()
            self._send_json(200, API_KEY_STATUS, sid)
            return
        if self.path == "/api/trace":
            sid = self._sid()
            self._send_json(200, get_trace_state(sid), sid)
            return

        if self.path != "/":
            self.send_error(HTTPStatus.NOT_FOUND, "Not Found")
            return
        body = HTML.encode("utf-8")
        sid = self._sid()
        body = render_html().encode("utf-8")
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Set-Cookie", f"{SID_COOKIE}={sid}; Path=/; HttpOnly")
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self) -> None:  # noqa: N802
        if self.path not in {"/api/chat", "/api/extract_topology"}:
            self.send_error(HTTPStatus.NOT_FOUND, "Not Found")
            return
        sid = self._sid()
        try:
            length = int(self.headers.get("Content-Length", "0"))
            payload = json.loads(self.rfile.read(length).decode("utf-8"))
        except Exception:
            self._send_json(400, {"error": "请求体不是有效 JSON"}, sid)
            return

        if self.path == "/api/extract_topology":
            image_data_url = str(payload.get("image_data_url", "")).strip()
            vision_prompt = str(payload.get("vision_prompt", "")).strip()
            if not image_data_url:
                self._send_json(400, {"error": "image_data_url 不能为空"}, sid)
                return
            if not API_KEY_STATUS.get("usable"):
                self._send_json(400, {"error": f"API Key 不可用: {API_KEY_STATUS.get('message', '未知错误')}"}, sid)
                return
            try:
                if vision_prompt:
                    VISION_PROMPTS[sid] = vision_prompt
                final_vision_prompt = vision_prompt or VISION_PROMPTS.get(sid, "") or DEFAULT_VISION_PROMPT_TEXT
                topology_text = extract_topology_from_image(
                    image_data_url=image_data_url,
                    vision_prompt=final_vision_prompt,
                )
                TOPOLOGY_CONTEXTS[sid] = topology_text
                self._send_json(200, {"topology_text": topology_text}, sid)
            except Exception as exc:  # pylint: disable=broad-except
                self._send_json(500, {"error": str(exc)}, sid)
            return

        message = str(payload.get("message", "")).strip()
        image_data_url = str(payload.get("image_data_url", "")).strip()
        topology_context = str(payload.get("topology_context", "")).strip()
        vision_prompt = str(payload.get("vision_prompt", "")).strip()
        init_trace_state(sid=sid, question=message)
        if vision_prompt:
            VISION_PROMPTS[sid] = vision_prompt
        if topology_context:
            TOPOLOGY_CONTEXTS[sid] = topology_context
        cached_topology = TOPOLOGY_CONTEXTS.get(sid, "").strip()
        if not message and not image_data_url and not topology_context and not cached_topology:
            self._send_json(400, {"error": "message / image_data_url / topology_context 不能同时为空"}, sid)
            return

        generated_topology = ""
        if image_data_url and not topology_context and API_KEY_STATUS.get("supports_image"):
            try:
                final_vision_prompt = vision_prompt or VISION_PROMPTS.get(sid, "") or DEFAULT_VISION_PROMPT_TEXT
                generated_topology = extract_topology_from_image(
                    image_data_url=image_data_url,
                    vision_prompt=final_vision_prompt,
                )
            except Exception:
                generated_topology = ""

        merged_topology = topology_context or generated_topology or cached_topology
        final_message = message
        if merged_topology:
            final_message = (
                (message + "\n\n" if message else "")
                + "以下是已确认的拓扑结构描述，请以此作为优先事实来源：\n"
                + merged_topology
            )
            TOPOLOGY_CONTEXTS[sid] = merged_topology
        elif image_data_url and not API_KEY_STATUS.get("supports_image"):
            final_message = (
                (message + "\n\n" if message else "")
                + "提示：当前视觉模型不可用，暂无法自动解析图片拓扑，请先手工补充拓扑信息。"
            )

        agent = AGENTS.get(sid)
        if agent is None:
            if not DEFAULT_API_KEY:
                required = "ZHIPU_API_KEY" if DEFAULT_PROVIDER == "zhipu" else "OPENAI_API_KEY"
                self._send_json(400, {"error": f".env 未配置 {required}"}, sid)
                return
            if not API_KEY_STATUS.get("usable"):
                self._send_json(400, {"error": f"API Key 不可用: {API_KEY_STATUS.get('message', '未知错误')}"}, sid)
                return
            agent = NetOpsAgent(
                api_key=DEFAULT_API_KEY,
                provider=DEFAULT_PROVIDER,
                base_url=DEFAULT_BASE_URL,
                model=DEFAULT_TEXT_MODEL or None,
                auto_select_model=AUTO_SELECT_TEXT_MODEL,
            )
            AGENTS[sid] = agent

        try:
            on_trace_event(sid, "stage", {"name": "执行排查", "detail": "Agent 已开始执行检查。"})
            pool = concurrent.futures.ThreadPoolExecutor(max_workers=1)
            fut = pool.submit(
                lambda: asyncio.run(
                    agent.ask(
                        final_message,
                        image_data_url=None,
                        trace_hook=lambda e, p: on_trace_event(sid, e, p),
                    )
                )
            )
            try:
                result = fut.result(timeout=CHAT_TIMEOUT_SECONDS)
            finally:
                pool.shutdown(wait=False, cancel_futures=True)
            trace_snapshot = get_trace_state(sid)
            self._send_json(
                200,
                {
                    "answer": result.answer,
                    "confidence": result.confidence,
                    "evidence": result.evidence,
                    "auto_hop_report": result.auto_hop_report,
                    "tool_calls": sanitize_tool_calls(result.tool_calls),
                    "trace": merge_trace_snapshot(
                        trace_snapshot=trace_snapshot,
                        fallback=build_execution_trace(
                        user_message=message,
                        topology_attached=bool(merged_topology),
                        tool_calls=result.tool_calls,
                        evidence=result.evidence,
                        auto_hop_report=result.auto_hop_report,
                    ),
                    ),
                },
                sid,
            )
        except concurrent.futures.TimeoutError:
            on_trace_event(
                sid,
                "error",
                {"error": "排障任务执行时间较长，请继续根据交互细节观察进展。"},
            )
            trace_snapshot = get_trace_state(sid)
            self._send_json(
                200,
                {
                    "answer": (
                        "【诊断结果】\n"
                        "- 最可能故障点：暂未收敛（任务仍在执行）\n"
                        "- 置信度：45%\n"
                        "- 关键证据：已生成部分执行轨迹，请查看右侧“设备交互细节”\n"
                        "- 建议操作：继续观察设备交互细节，必要时再缩小排障范围"
                    ),
                    "confidence": 45,
                    "evidence": ["执行超时，已保留部分轨迹"],
                    "auto_hop_report": {},
                    "tool_calls": [],
                    "trace": trace_snapshot,
                },
                sid,
            )
        except Exception as exc:  # pylint: disable=broad-except
            on_trace_event(sid, "error", {"error": str(exc)})
            self._send_json(500, {"error": str(exc)}, sid)


def main() -> None:
    global DEFAULT_API_KEY, DEFAULT_PROVIDER, DEFAULT_BASE_URL, DEFAULT_TEXT_MODEL, DEFAULT_VISION_MODEL, DEFAULT_VISION_FALLBACK_MODEL, AUTO_SELECT_TEXT_MODEL
    load_dotenv()
    DEFAULT_PROVIDER = os.getenv("AI_PROVIDER", "zhipu").strip().lower() or "zhipu"
    DEFAULT_BASE_URL = os.getenv("AI_BASE_URL", "").strip() or (
        "https://open.bigmodel.cn/api/paas/v4/" if DEFAULT_PROVIDER == "zhipu" else ""
    )
    DEFAULT_TEXT_MODEL = os.getenv("AI_TEXT_MODEL", "").strip() or os.getenv("AI_MODEL", "").strip() or "glm-4.7"
    DEFAULT_VISION_MODEL = os.getenv("AI_VISION_MODEL", "").strip() or "glm-4.6v"
    DEFAULT_VISION_FALLBACK_MODEL = os.getenv("AI_VISION_FALLBACK_MODEL", "").strip()
    AUTO_SELECT_TEXT_MODEL = os.getenv("AUTO_SELECT_TEXT_MODEL", "false").strip().lower() in {"1", "true", "yes", "on"}
    DEFAULT_API_KEY = (
        os.getenv("ZHIPU_API_KEY", "").strip()
        if DEFAULT_PROVIDER == "zhipu"
        else os.getenv("OPENAI_API_KEY", "").strip()
    )
    probe_default_api_key()
    server = ThreadingHTTPServer((HOST, PORT), Handler)
    print(f"Web UI 启动: http://{HOST}:{PORT}")
    server.serve_forever()


def probe_default_api_key() -> None:
    API_KEY_STATUS["provider"] = DEFAULT_PROVIDER
    API_KEY_STATUS["configured"] = bool(DEFAULT_API_KEY)
    API_KEY_STATUS["usable"] = False
    API_KEY_STATUS["vision_usable"] = False
    API_KEY_STATUS["text_model"] = DEFAULT_TEXT_MODEL
    API_KEY_STATUS["vision_model"] = DEFAULT_VISION_MODEL
    API_KEY_STATUS["vision_fallback_model"] = DEFAULT_VISION_FALLBACK_MODEL
    if not DEFAULT_API_KEY:
        API_KEY_STATUS["message"] = "未配置 API Key"
        API_KEY_STATUS["text_source"] = ""
        return

    try:
        probe_agent = NetOpsAgent(
            api_key=DEFAULT_API_KEY,
            provider=DEFAULT_PROVIDER,
            base_url=DEFAULT_BASE_URL,
            model=DEFAULT_TEXT_MODEL or None,
            auto_select_model=AUTO_SELECT_TEXT_MODEL,
        )
        TEXT_MODEL_INFO.update(probe_agent.get_model_info())
        client = OpenAI(api_key=DEFAULT_API_KEY, base_url=DEFAULT_BASE_URL) if DEFAULT_BASE_URL else OpenAI(api_key=DEFAULT_API_KEY)
        client.chat.completions.create(
            model=TEXT_MODEL_INFO["model"],
            messages=[{"role": "user", "content": "ping"}],
            max_tokens=16,
        )
        # Probe image capability using dedicated vision model.
        test_img_url = "https://picsum.photos/96"
        vision_ok = False
        try:
            client.chat.completions.create(
                model=DEFAULT_VISION_MODEL,
                messages=[
                    {
                        "role": "user",
                        "content": [
                            {"type": "text", "text": "describe"},
                            {"type": "image_url", "image_url": {"url": test_img_url}},
                        ],
                    }
                ],
                max_tokens=32,
            )
            vision_ok = True
        except Exception:
            vision_ok = False

        API_KEY_STATUS["usable"] = True
        API_KEY_STATUS["message"] = "OK"
        API_KEY_STATUS["text_model"] = TEXT_MODEL_INFO.get("model", DEFAULT_TEXT_MODEL)
        API_KEY_STATUS["text_source"] = TEXT_MODEL_INFO.get("source", "manual")
        API_KEY_STATUS["vision_model"] = DEFAULT_VISION_MODEL
        API_KEY_STATUS["vision_fallback_model"] = DEFAULT_VISION_FALLBACK_MODEL
        API_KEY_STATUS["vision_usable"] = vision_ok
        API_KEY_STATUS["supports_image"] = vision_ok
    except Exception as exc:  # pylint: disable=broad-except
        API_KEY_STATUS["message"] = friendly_status_error(exc)
        API_KEY_STATUS["text_model"] = TEXT_MODEL_INFO.get("model", DEFAULT_TEXT_MODEL)
        API_KEY_STATUS["text_source"] = TEXT_MODEL_INFO.get("source", "manual")
        API_KEY_STATUS["vision_model"] = DEFAULT_VISION_MODEL
        API_KEY_STATUS["vision_fallback_model"] = DEFAULT_VISION_FALLBACK_MODEL
        API_KEY_STATUS["vision_usable"] = False
        API_KEY_STATUS["supports_image"] = False


def extract_topology_from_image(*, image_data_url: str, vision_prompt: str | None = None) -> str:
    if not DEFAULT_API_KEY:
        raise RuntimeError("未配置 API Key")
    vision_url = normalize_image_for_vision(image_data_url)
    validate_image_url_for_vision(vision_url)

    client = OpenAI(api_key=DEFAULT_API_KEY, base_url=DEFAULT_BASE_URL) if DEFAULT_BASE_URL else OpenAI(api_key=DEFAULT_API_KEY)
    prompt = (vision_prompt or "").strip() or DEFAULT_VISION_PROMPT_TEXT
    text = run_vision_extract(
        client=client,
        model=DEFAULT_VISION_MODEL,
        image_url=vision_url,
        prompt=prompt,
    )
    if not text and DEFAULT_VISION_FALLBACK_MODEL:
        text = run_vision_extract(
            client=client,
            model=DEFAULT_VISION_FALLBACK_MODEL,
            image_url=vision_url,
            prompt=prompt,
        )
    if not text:
        return "拓扑图已接收，但模型未返回稳定文本。请重点检查核心节点、区域边界和主干链路。未识别项：具体接口与网段细节。"
    text = strip_code_fence(text)
    text = strip_think_content(text).strip()
    if not text:
        raise RuntimeError("拓扑解析返回空结果")
    return text


def strip_code_fence(text: str) -> str:
    t = text.strip()
    if t.startswith("```"):
        parts = t.split("```")
        if len(parts) >= 3:
            body = parts[1]
            if body.startswith("json"):
                body = body[4:]
            return body.strip()
    return t


def run_vision_extract(*, client: OpenAI, model: str, image_url: str, prompt: str) -> str:
    prompts = [
        prompt,
        "请只输出拓扑结构描述（纯文本）。先主干路径，再设备互联，再网段与区域；未知写未识别。",
    ]
    for p in prompts:
        try:
            resp = client.chat.completions.create(
                model=model,
                messages=[
                    {
                        "role": "user",
                        "content": [
                            {"type": "text", "text": p},
                            {"type": "image_url", "image_url": {"url": image_url}},
                        ],
                    }
                ],
                max_tokens=1200,
                temperature=0.1,
            )
            content = (resp.choices[0].message.content or "").strip()
            content = strip_think_content(content)
            if len(content) >= 24:
                return content
        except Exception:
            continue
    return ""


def strip_think_content(text: str) -> str:
    if not text:
        return text
    # Remove think blocks commonly returned by thinking models.
    cleaned = re.sub(r"<think>.*?</think>", "", text, flags=re.S | re.I).strip()
    if cleaned:
        return cleaned
    # If response only contains think block, keep concise thought text as fallback.
    m = re.search(r"<think>(.*?)</think>", text, flags=re.S | re.I)
    if m:
        thought = m.group(1).strip()
        if thought:
            # compress excessive newlines
            thought = re.sub(r"\n{3,}", "\n\n", thought)
            return thought[:1200]
    return cleaned


def fallback_topology_from_partial(text: str) -> dict:
    t = (text or "").strip()
    topo_summary = extract_partial_field(t, "topology_structure_summary")
    inter_summary = extract_partial_field(t, "device_interconnections_summary")
    subnet_summary = extract_partial_field(t, "subnets_summary")
    zones_summary = extract_partial_field(t, "zones_summary")
    details_summary = extract_partial_field(t, "details_summary")
    if not details_summary:
        details_summary = "模型返回被截断，已按可提取信息降级。"
    return {
        "topology_structure_summary": topo_summary,
        "device_interconnections_summary": inter_summary,
        "key_devices": [],
        "key_links": [],
        "subnets_summary": subnet_summary,
        "zones_summary": zones_summary,
        "details_summary": details_summary,
        "unknowns": ["模型输出截断，部分字段缺失"],
        "devices": [],
        "links": [],
        "zones": [],
    }


def extract_partial_field(text: str, key: str) -> str:
    # best-effort extract for truncated JSON like ..."key":"value...
    pattern = rf'"{re.escape(key)}"\s*:\s*"([^"]*)'
    m = re.search(pattern, text)
    if m:
        return m.group(1).strip()
    return ""


def try_parse_topology_json(text: str) -> dict | None:
    t = text.strip()
    try:
        obj = json.loads(t)
        if isinstance(obj, dict):
            return normalize_topology_obj(obj)
    except Exception:
        pass

    # Best-effort bracket slicing.
    lb = t.find("{")
    rb = t.rfind("}")
    if lb != -1 and rb != -1 and rb > lb:
        snippet = t[lb : rb + 1]
        try:
            obj = json.loads(snippet)
            if isinstance(obj, dict):
                return normalize_topology_obj(obj)
        except Exception:
            return None
    return None


def repair_topology_json_with_text_model(raw_text: str, client: OpenAI) -> str:
    prompt = (
        "请把下面内容修复为严格 JSON，且必须满足字段："
        "{"
        "\"topology_structure_summary\":\"\","
        "\"device_interconnections_summary\":\"\","
        "\"key_devices\":[],"
        "\"key_links\":[],"
        "\"subnets_summary\":\"\","
        "\"zones_summary\":\"\","
        "\"details_summary\":\"\","
        "\"unknowns\":[]"
        "}。"
        "只输出 JSON，不要解释。若信息缺失可留空。原始内容：\n" + raw_text
    )
    resp = client.chat.completions.create(
        model=DEFAULT_TEXT_MODEL,
        messages=[{"role": "user", "content": prompt}],
        max_tokens=800,
        response_format={"type": "json_object"},
    )
    return strip_code_fence((resp.choices[0].message.content or "").strip())


def normalize_topology_obj(obj: dict) -> dict:
    out = dict(obj)
    if any(k in out for k in ["topology_structure_summary", "device_interconnections_summary", "key_devices", "key_links"]):
        key_devices = out.get("key_devices", [])
        key_links = out.get("key_links", [])
        unknowns = out.get("unknowns", [])
        if not isinstance(key_devices, list):
            key_devices = []
        if not isinstance(key_links, list):
            key_links = []
        if not isinstance(unknowns, list):
            unknowns = [str(unknowns)]
        return {
            "topology_structure_summary": str(out.get("topology_structure_summary", "")).strip(),
            "device_interconnections_summary": str(out.get("device_interconnections_summary", "")).strip(),
            "key_devices": key_devices,
            "key_links": key_links,
            "subnets_summary": str(out.get("subnets_summary", "")).strip(),
            "zones_summary": str(out.get("zones_summary", "")).strip(),
            "details_summary": str(out.get("details_summary", "")).strip(),
            "unknowns": unknowns,
            "devices": [],
            "links": [],
            "zones": [],
        }
    if "topology_structure" in out or "device_interconnections" in out:
        topo = out.get("topology_structure", {})
        if not isinstance(topo, dict):
            topo = {}
        nodes = topo.get("nodes", [])
        links = topo.get("links", [])
        core_paths = topo.get("core_paths", [])
        device_interconnections = out.get("device_interconnections", [])
        subnets = out.get("subnets", [])
        zones = out.get("zones", [])
        unknowns = out.get("unknowns", [])
        if not isinstance(nodes, list):
            nodes = []
        if not isinstance(links, list):
            links = []
        if not isinstance(core_paths, list):
            core_paths = []
        if not isinstance(device_interconnections, list):
            device_interconnections = []
        if not isinstance(subnets, list):
            subnets = []
        if not isinstance(zones, list):
            zones = []
        if not isinstance(unknowns, list):
            unknowns = [str(unknowns)]
        return {
            "topology_structure": {
                "nodes": nodes,
                "links": links,
                "core_paths": core_paths,
            },
            "device_interconnections": device_interconnections,
            "subnets": subnets,
            "zones": zones,
            "details_summary": str(out.get("details_summary", "")).strip(),
            "unknowns": unknowns,
            "devices": [],
            "links": [],
        }
    if any(k in out for k in ["devices_summary", "subnets_summary", "zones_summary", "links_summary"]):
        return {
            "devices_summary": str(out.get("devices_summary", "")).strip(),
            "subnets_summary": str(out.get("subnets_summary", "")).strip(),
            "zones_summary": str(out.get("zones_summary", "")).strip(),
            "links_summary": str(out.get("links_summary", "")).strip(),
            "unknowns": out.get("unknowns", []) if isinstance(out.get("unknowns", []), list) else [str(out.get("unknowns", ""))],
            "devices": [],
            "links": [],
            "zones": [],
        }
    for k in ["devices", "links", "zones", "unknowns"]:
        if k not in out:
            out[k] = []
    if not isinstance(out["devices"], list):
        out["devices"] = []
    if not isinstance(out["links"], list):
        out["links"] = []
    if not isinstance(out["zones"], list):
        out["zones"] = []
    if not isinstance(out["unknowns"], list):
        out["unknowns"] = [str(out["unknowns"])]
    return out


def normalize_image_for_vision(image_data_url: str) -> str:
    raw = (image_data_url or "").strip()
    if raw.startswith("http://") or raw.startswith("https://"):
        return raw
    if raw.startswith("data:image/"):
        raise RuntimeError("当前视觉模型仅支持公网图片 URL，请在左侧“图片 URL”输入框粘贴链接")
    raise RuntimeError("图片格式不支持：请提供公网 URL")


def validate_image_url_for_vision(url: str) -> None:
    req = urllib.request.Request(
        url,
        method="GET",
        headers={"User-Agent": "Mozilla/5.0 NetOpsAgent/1.0"},
    )
    try:
        with urllib.request.urlopen(req, timeout=12) as resp:
            ctype = (resp.headers.get("Content-Type") or "").lower()
            _ = resp.read(32)
    except urllib.error.HTTPError as exc:
        raise RuntimeError(f"图片URL不可访问（HTTP {exc.code}）")
    except Exception as exc:
        raise RuntimeError(f"图片URL访问失败：{exc}")

    if not ctype.startswith("image/"):
        raise RuntimeError(f"图片URL返回的 Content-Type 不是 image/*：{ctype or 'unknown'}")


def init_trace_state(*, sid: str, question: str) -> None:
    now = int(time.time())
    with TRACE_LOCK:
        TRACE_STATES[sid] = {
            "status": "running",
            "stage": "初始化",
            "question": question,
            "logic": [
                "识别用户问题并确认目标源/目的地址。",
                "按最小必要原则执行连通性、路由、ARP、接口检查。",
                "基于工具证据收敛故障点并输出建议。",
            ],
            "progress": {
                "total_actions": 0,
                "success_actions": 0,
                "failed_actions": 0,
                "completion": 5,
                "updated_at": now,
            },
            "actions": [],
            "error": "",
        }


def get_trace_state(sid: str) -> dict:
    with TRACE_LOCK:
        state = TRACE_STATES.get(sid)
        if not state:
            return {
                "status": "idle",
                "stage": "未开始",
                "logic": [],
                "progress": {"total_actions": 0, "success_actions": 0, "failed_actions": 0, "completion": 0},
                "actions": [],
            }
        return to_jsonable(state)


def on_trace_event(sid: str, event: str, payload: dict) -> None:
    with TRACE_LOCK:
        state = TRACE_STATES.get(sid)
        if not state:
            state = {
                "status": "running",
                "stage": "初始化",
                "logic": [],
                "progress": {"total_actions": 0, "success_actions": 0, "failed_actions": 0, "completion": 0},
                "actions": [],
            }
            TRACE_STATES[sid] = state

        now = int(time.time())
        progress = state.setdefault("progress", {})
        actions = state.setdefault("actions", [])
        if event == "stage":
            state["stage"] = str(payload.get("name", "处理中"))
            detail = str(payload.get("detail", "")).strip()
            if detail:
                logic = state.setdefault("logic", [])
                if detail not in logic:
                    logic.append(detail)
            if progress.get("completion", 0) < 20:
                progress["completion"] = 20
        elif event == "tool_start":
            idx = len(actions) + 1
            progress["total_actions"] = idx
            progress["completion"] = min(85, max(progress.get("completion", 20), 20 + idx * 8))
            safe_args = mask_sensitive(to_jsonable(payload.get("args")))
            actions.append(
                {
                    "index": idx,
                    "name": str(payload.get("name", "")),
                    "ok": None,
                    "args_raw": safe_args,
                    "args_summary": compact_text(safe_args, max_len=200),
                    "result_summary": "执行中",
                    "result_raw": "",
                    "ts": now,
                }
            )
            state["stage"] = "调用设备工具"
        elif event == "tool_result":
            ok = bool(payload.get("ok"))
            if actions:
                actions[-1]["ok"] = ok
                safe_result = mask_sensitive(to_jsonable(payload.get("result")))
                actions[-1]["result_raw"] = safe_result
                actions[-1]["result_summary"] = compact_text(safe_result, max_len=300)
            if ok:
                progress["success_actions"] = int(progress.get("success_actions", 0)) + 1
            else:
                progress["failed_actions"] = int(progress.get("failed_actions", 0)) + 1
        elif event == "done":
            state["status"] = "completed"
            state["stage"] = "完成"
            progress["completion"] = 100
            ev = payload.get("evidence", [])
            if isinstance(ev, list) and ev:
                state["logic"].append("证据摘要：" + "；".join([str(x) for x in ev[:3]]))
        elif event == "error":
            state["status"] = "failed"
            state["stage"] = "失败"
            state["error"] = str(payload.get("error", "unknown error"))
            progress["completion"] = 100

        progress["updated_at"] = now


def merge_trace_snapshot(*, trace_snapshot: dict, fallback: dict) -> dict:
    if not trace_snapshot or trace_snapshot.get("status") == "idle":
        return fallback
    merged = dict(trace_snapshot)
    if not merged.get("logic"):
        merged["logic"] = fallback.get("logic", [])
    return merged


def sanitize_tool_calls(tool_calls: list[dict]) -> list[dict]:
    return [mask_sensitive(to_jsonable(x)) for x in tool_calls]


def build_execution_trace(
    *,
    user_message: str,
    topology_attached: bool,
    tool_calls: list[dict],
    evidence: list[str],
    auto_hop_report: dict,
) -> dict:
    masked_calls = sanitize_tool_calls(tool_calls)
    actions = []
    ok_count = 0
    fail_count = 0
    for idx, call in enumerate(masked_calls, start=1):
        ok = infer_action_ok(call)
        if ok:
            ok_count += 1
        else:
            fail_count += 1
        args_summary = compact_text(call.get("args"), max_len=200)
        result_summary = compact_text(call.get("result"), max_len=280)
        actions.append(
            {
                "index": idx,
                "name": str(call.get("name", "")),
                "ok": ok,
                "args_raw": to_jsonable(call.get("args")),
                "args_summary": args_summary,
                "result_raw": to_jsonable(call.get("result")),
                "result_summary": result_summary,
            }
        )

    logic = [
        "识别用户问题并确认目标源/目的地址。",
        "按最小必要原则选择检查顺序：连通性 -> 路由/ARP -> 接口状态。",
        "执行工具调用并基于证据收敛故障点。",
    ]
    if topology_attached:
        logic.insert(1, "已结合拓扑描述约束排障路径与设备范围。")
    if auto_hop_report.get("checked_hops", 0) > 0:
        logic.append(f"已执行跨设备自动排障，检查 hop 数: {auto_hop_report.get('checked_hops', 0)}。")
    if evidence:
        logic.append("证据摘要：" + "；".join([str(x) for x in evidence[:3]]))

    total = len(actions)
    completion = 100 if total == 0 else int((ok_count + fail_count) * 100 / total)
    return {
        "logic": logic,
        "progress": {
            "total_actions": total,
            "success_actions": ok_count,
            "failed_actions": fail_count,
            "completion": completion,
        },
        "actions": actions,
        "question": user_message,
    }


def compact_text(value, max_len: int = 240) -> str:
    try:
        raw = value if isinstance(value, str) else json.dumps(to_jsonable(value), ensure_ascii=False)
    except Exception:
        raw = str(value)
    raw = re.sub(r"\s+", " ", raw).strip()
    return (raw[: max_len - 3] + "...") if len(raw) > max_len else raw


def infer_action_ok(call: dict) -> bool:
    if not bool(call.get("ok")):
        return False
    result = to_jsonable(call.get("result"))
    if isinstance(result, dict):
        sc = result.get("structuredContent")
        if isinstance(sc, dict):
            if isinstance(sc.get("success"), bool):
                return sc.get("success") is True
            nested = sc.get("result")
            if isinstance(nested, str):
                try:
                    parsed = json.loads(nested)
                    if isinstance(parsed, dict) and isinstance(parsed.get("success"), bool):
                        return parsed.get("success") is True
                except Exception:
                    pass
        for key in ("success", "ok", "isError"):
            if key in result and isinstance(result[key], bool):
                if key == "isError":
                    return not result[key]
                return result[key]
    return True


def to_jsonable(obj):
    if obj is None or isinstance(obj, (str, int, float, bool)):
        return obj
    if isinstance(obj, dict):
        return {str(k): to_jsonable(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple, set)):
        return [to_jsonable(x) for x in obj]
    if hasattr(obj, "model_dump"):
        try:
            return to_jsonable(obj.model_dump())
        except Exception:
            pass
    if hasattr(obj, "dict"):
        try:
            return to_jsonable(obj.dict())
        except Exception:
            pass
    if hasattr(obj, "__dict__"):
        try:
            return to_jsonable(vars(obj))
        except Exception:
            pass
    return str(obj)


def mask_sensitive(obj):
    if isinstance(obj, dict):
        out = {}
        for k, v in obj.items():
            lk = str(k).lower()
            if any(s in lk for s in ["password", "secret", "token", "api_key"]):
                out[k] = "***"
            else:
                out[k] = mask_sensitive(v)
        return out
    if isinstance(obj, list):
        return [mask_sensitive(x) for x in obj]
    return obj


def friendly_status_error(exc: Exception) -> str:
    text = str(exc).lower()
    if "insufficient_quota" in text or "exceeded your current quota" in text:
        return "配额不足（insufficient_quota）"
    if "'code': '1113'" in text or "余额不足" in str(exc):
        return "余额不足或无可用资源包（code 1113）"
    if "invalid_api_key" in text or "incorrect api key" in text or "authentication" in text:
        return "API Key 无效"
    if "rate limit" in text:
        return "请求限流"
    return str(exc)


if __name__ == "__main__":
    main()

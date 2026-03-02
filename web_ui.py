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
from typing import Any, Dict
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
DEFAULT_TEXT_API_KEY = ""
DEFAULT_TEXT_PROVIDER = "zhipu"
DEFAULT_TEXT_BASE_URL = "https://open.bigmodel.cn/api/paas/v4/"
DEFAULT_VISION_API_KEY = ""
DEFAULT_VISION_PROVIDER = "ppio"
DEFAULT_VISION_BASE_URL = "https://api.ppio.com/openai"
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
    "text_provider": "",
    "vision_provider": "",
    "text_model": "",
    "text_source": "",
    "vision_model": "",
    "vision_fallback_model": "",
    "vision_usable": False,
    "supports_image": False,
}

DEFAULT_VISION_PROMPT_TEXT = (
    "请分析这张网络拓扑图，并用简洁中文输出：\n"
    "\n"
    "1. 列出识别到的设备类型与名称（如核心交换机、接入交换机、防火墙、路由器、服务器等）。\n"
    "2. 描述设备之间的连接关系。\n"
    "3. 无法识别的信息标注“未识别”。\n"
    "\n"
    "要求：\n"
    "- 仅输出纯文本\n"
    "- 控制在 6–10 行\n"
    "- 不要使用 JSON、表格或代码块"
)


HTML = """<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>NetOps Agent</title>
  <style>
    *, *::before, *::after{box-sizing:border-box;}
    body{font-family:ui-sans-serif,system-ui,-apple-system;background:#f8fafc;color:#0f172a;max-width:1200px;margin:20px auto;padding:0 12px;}
    h2{margin:0;font-size:20px;}
    .header{display:flex;align-items:center;justify-content:space-between;gap:12px;margin-bottom:12px;}
    .header-right{display:flex;align-items:center;gap:10px;min-width:0;}
    .header-status{display:flex;flex-direction:column;align-items:flex-end;min-width:0;}
    .header-top{display:flex;align-items:center;gap:8px;}
    .header-btn{padding:4px 10px;font-size:12px;border-radius:999px;background:#dcfce7;border:1px solid #bbf7d0;color:#166534;line-height:1;height:26px;}
    .layout{display:grid;grid-template-columns:1fr 1fr 1fr;gap:12px;height:calc(100vh - 110px);min-width:0;}
    .layout > *{min-width:0;}
    .card{background:#fff;border:1px solid #e2e8f0;border-radius:10px;padding:12px;min-height:0;height:100%;display:flex;flex-direction:column;min-width:0;overflow:hidden;}
    .card h3{margin:0 0 10px 0;font-size:16px;}
    .row{display:flex;gap:8px;margin:8px 0;min-width:0;}
    textarea,input{width:100%;padding:10px;border:1px solid #cbd5e1;border-radius:8px;min-width:0;}
    button{padding:10px 14px;cursor:pointer;border:1px solid #cbd5e1;background:#fff;border-radius:8px;}
    .primary{background:#0f172a;color:#fff;border-color:#0f172a;}
    pre{white-space:pre-wrap;background:#0b1220;color:#e2e8f0;padding:12px;border-radius:8px;min-height:0;max-height:none;overflow:auto;flex:1;min-width:0;overflow-wrap:anywhere;word-break:break-word;}
    small{color:#475569;}
    .badge{display:inline-block;padding:4px 10px;border-radius:999px;font-size:12px;}
    .ok{background:#dcfce7;color:#166534;}
    .bad{background:#fee2e2;color:#991b1b;}
    .warn{background:#fef3c7;color:#92400e;}
    .img-wrap{border:1px dashed #cbd5e1;border-radius:8px;min-height:220px;display:flex;align-items:center;justify-content:center;background:#f8fafc;overflow:hidden;min-width:0;}
    .img-wrap img{width:100%;height:auto;display:block;}
    .kv{font-size:13px;line-height:1.6;color:#334155;background:#f8fafc;border:1px solid #e2e8f0;border-radius:8px;padding:8px;}
    .topo-card{overflow:auto;}
    .topo-json{width:100%;min-height:0;max-height:180px;height:180px;resize:none;font-family:ui-monospace,SFMono-Regular,Menlo,Monaco,Consolas,monospace;font-size:12px;overflow:auto;}
    .chat-box{display:flex;flex-direction:column;gap:10px;height:100%;min-width:0;}
    .chat-messages{flex:1;min-height:0;max-height:none;overflow:auto;background:#f8fafc;border:1px solid #e2e8f0;border-radius:8px;padding:10px;min-width:0;}
    .msg{max-width:86%;padding:8px 10px;border-radius:10px;margin:8px 0;white-space:pre-wrap;line-height:1.5;overflow-wrap:anywhere;word-break:break-word;}
    .msg-user{margin-left:auto;background:#dbeafe;color:#1e3a8a;}
    .msg-assistant{margin-right:auto;background:#ecfeff;color:#134e4a;}
    .msg-status{margin:8px auto;background:#e2e8f0;color:#334155;font-size:12px;}
    .chat-input{width:100%;min-height:86px;max-height:140px;resize:vertical;}
    .modal-mask{position:fixed;inset:0;background:rgba(15,23,42,.45);display:none;align-items:center;justify-content:center;z-index:2000;padding:16px;}
    .modal{width:min(720px,96vw);background:#fff;border:1px solid #cbd5e1;border-radius:12px;padding:12px;box-shadow:0 20px 50px rgba(2,6,23,.25);}
    .modal-head{display:flex;align-items:center;justify-content:space-between;gap:10px;margin-bottom:8px;}
    .modal-title{font-size:16px;font-weight:600;}
    .modal textarea{width:100%;height:300px;resize:vertical;font-family:ui-monospace,SFMono-Regular,Menlo,Monaco,Consolas,monospace;font-size:12px;}
    .modal-actions{display:flex;justify-content:flex-end;gap:8px;margin-top:10px;}
    .tab-btn{padding:8px 10px;font-size:12px;}
    .tab-btn.active{background:#0f172a;color:#fff;border-color:#0f172a;}
    .tab-pane{display:none;flex:1;min-height:0;min-width:0;overflow:hidden;}
    .tab-pane.active{display:flex;flex-direction:column;gap:8px;}
    @media (max-width: 1080px){
      .layout{grid-template-columns:1fr;}
      .card{min-height:420px;}
    }
  </style>
</head>
<body>
  <div class="header">
    <div><h2>NetOps Agent</h2></div>
    <div class="header-right">
      <div class="header-status">
        <div class="header-top">
          <div id="keyStatus" class="badge __KEY_STATUS_CLASS__">__KEY_STATUS_TEXT__</div>
          <button class="header-btn" onclick="refreshStatus()">刷新 Key 状态</button>
        </div>
        <div id="modelInfo" style="font-size:12px;color:#475569;margin-top:6px;">__MODEL_INFO_TEXT__</div>
      </div>
    </div>
  </div>

  <div class="layout">
    <section class="card topo-card">
      <h3>拓扑与上下文</h3>
      <div class="row"><input id="imgUrl" type="text" placeholder="粘贴可公网访问的拓扑图片 URL" /></div>
      <div class="img-wrap"><img id="imgPreview" alt="topology preview" style="display:none;" /></div>
      <div class="row">
        <button onclick="clearImage()">删除拓扑</button>
        <button onclick="extractTopology()">分析拓扑</button>
        <button id="promptToggleBtn" onclick="openPromptModal()">解析提示词</button>
      </div>
      <div id="imgMeta" class="kv">尚未导入拓扑。支持粘贴 URL 进行分析。</div>
      <div class="row">
        <textarea id="topologyJson" class="topo-json" placeholder="拓扑结构描述（可编辑确认）"></textarea>
      </div>
    </section>

    <section class="card">
      <h3>对话输入与输出</h3>
      <div class="chat-box">
        <div id="chatMessages" class="chat-messages">
          <div class="msg msg-status">开始对话</div>
        </div>
        <textarea id="q" class="chat-input" placeholder="请输入问题，例如：10.0.0.11 到 10.0.2.20 不通"></textarea>
        <div class="row">
          <button id="sendBtn" class="primary" onclick="ask()">发送</button>
          <button onclick="clearLog()">清空对话</button>
        </div>
      </div>
    </section>

    <section class="card">
      <h3>Ops Panel</h3>
      <div class="row">
        <button id="tabTrace" class="tab-btn active" onclick="switchRightTab('trace')">Trace</button>
        <button id="tabLog" class="tab-btn" onclick="switchRightTab('log')">Log</button>
        <button id="tabCmd" class="tab-btn" onclick="switchRightTab('cmd')">Commands</button>
        <button id="tabReport" class="tab-btn" onclick="switchRightTab('report')">Report</button>
      </div>
      <div id="paneTrace" class="tab-pane active">
        <pre id="tracePreview">暂无思路轨迹</pre>
      </div>
      <div id="paneLog" class="tab-pane">
        <pre id="logDetails">暂无交互日志</pre>
      </div>
      <div id="paneCmd" class="tab-pane">
        <pre id="cmdPreview">暂无命令建议</pre>
        <div class="row"><button onclick="copyCommands()">复制命令</button></div>
      </div>
      <div id="paneReport" class="tab-pane">
        <pre id="reportPreview">暂无报告</pre>
      </div>
    </section>
  </div>

  <div id="promptModalMask" class="modal-mask" onclick="closePromptModal(event)">
    <div class="modal" onclick="event.stopPropagation()">
      <div class="modal-head">
        <div class="modal-title">拓扑解析提示词</div>
        <button onclick="closePromptModal()">关闭</button>
      </div>
      <textarea id="visionPrompt" placeholder="可编辑视觉解析提示词"></textarea>
      <div class="modal-actions">
        <button onclick="resetPromptDefault()">恢复默认</button>
        <button class="primary" onclick="savePrompt()">保存</button>
      </div>
    </div>
  </div>

  <script>
    const chatMessages = document.getElementById('chatMessages');
    const tracePreview = document.getElementById('tracePreview');
    const logDetails = document.getElementById('logDetails');
    const keyStatus = document.getElementById('keyStatus');
    const modelInfo = document.getElementById('modelInfo');
    const imgUrlInput = document.getElementById('imgUrl');
    const imgMeta = document.getElementById('imgMeta');
    const imgPreview = document.getElementById('imgPreview');
    const visionPrompt = document.getElementById('visionPrompt');
    const topologyJson = document.getElementById('topologyJson');
    const sendBtn = document.getElementById('sendBtn');
    const promptModalMask = document.getElementById('promptModalMask');
    const promptToggleBtn = document.getElementById('promptToggleBtn');
    const cmdPreview = document.getElementById('cmdPreview');
    const reportPreview = document.getElementById('reportPreview');
    const tabTrace = document.getElementById('tabTrace');
    const tabLog = document.getElementById('tabLog');
    const tabCmd = document.getElementById('tabCmd');
    const tabReport = document.getElementById('tabReport');
    const paneTrace = document.getElementById('paneTrace');
    const paneLog = document.getElementById('paneLog');
    const paneCmd = document.getElementById('paneCmd');
    const paneReport = document.getElementById('paneReport');
    const DEFAULT_PROMPT = `请分析这张网络拓扑图，并用简洁中文输出：

1. 列出识别到的设备类型与名称（如核心交换机、接入交换机、防火墙、路由器、服务器等）。
2. 描述设备之间的连接关系。
3. 无法识别的信息标注“未识别”。

要求：
- 仅输出纯文本
- 控制在 6–10 行
- 不要使用 JSON、表格或代码块`;
    let traceTimer = null;
    let latestTrace = null;
    let latestAnswer = "";
    visionPrompt.value = localStorage.getItem('netops_vision_prompt') || DEFAULT_PROMPT;
    function addMsg(role, text){
      const el = document.createElement('div');
      el.className = 'msg ' + (role === 'user' ? 'msg-user' : role === 'assistant' ? 'msg-assistant' : 'msg-status');
      el.textContent = text;
      chatMessages.appendChild(el);
      chatMessages.scrollTop = chatMessages.scrollHeight;
    }
    function clearLog(){
      chatMessages.innerHTML = '<div class="msg msg-status">开始对话</div>';
    }
    function openPromptModal(){
      if(!promptModalMask){ return; }
      promptModalMask.style.display = 'flex';
    }
    function closePromptModal(evt){
      if(evt && evt.target && evt.target !== promptModalMask){ return; }
      if(!promptModalMask){ return; }
      promptModalMask.style.display = 'none';
    }
    function savePrompt(){
      const v = (visionPrompt.value || "").trim();
      localStorage.setItem('netops_vision_prompt', v || DEFAULT_PROMPT);
      visionPrompt.value = v || DEFAULT_PROMPT;
      closePromptModal();
      addMsg('status', "解析提示词已保存。");
    }
    function resetPromptDefault(){
      visionPrompt.value = DEFAULT_PROMPT;
    }
    function clearImage(){
      imgUrlInput.value = "";
      imgMeta.textContent = "尚未导入拓扑。支持粘贴 URL 进行分析。";
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
      imgMeta.textContent = "已导入拓扑 URL: " + url;
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
      const textProvider = data.text_provider || "unknown";
      const visionProvider = data.vision_provider || "unknown";
      const textModel = data.text_model || "unknown";
      const textSource = data.text_source || "unknown";
      const visionModel = data.vision_model || "unknown";
      const visionState = data.vision_usable ? "可用" : "不可用";
      modelInfo.textContent = "🟢 文本(" + textProvider + "): " + textModel + " (" + textSource + ") | 视觉(" + visionProvider + "): " + visionModel + " (" + visionState + ")";
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
      tracePreview.textContent = "AI 思路轨迹\\n\\n- 思路: 解析用户问题并定位源/目的\\n- 计划: 先最小检查，再逐步扩展\\n- 执行: 等待首个工具调用\\n- 结果: 进行中";
      logDetails.textContent = "执行日志\\n\\n请求已发送，等待 Agent 返回...";
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
        updateOpsPanels(data);
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
          updateOpsPanels({trace: data});
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
    function renderLog(trace){
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
    function renderThinkingTrace(trace){
      const p = trace.progress || {};
      const logic = Array.isArray(trace.logic) ? trace.logic : [];
      const actions = Array.isArray(trace.actions) ? trace.actions : [];
      const lines = [];
      lines.push("Trace");
      lines.push("");
      lines.push("Task");
      lines.push("- " + (trace.question || "未识别任务"));
      lines.push("");
      lines.push("Plan");
      const planLines = logic.slice(0, 5);
      if(planLines.length === 0){
        lines.push("- 解析问题 -> 最小必要检查 -> 收敛故障点");
      }else{
        planLines.forEach((x, i) => lines.push((i + 1) + ". " + x));
      }
      lines.push("");
      lines.push("Progress");
      lines.push("- 状态: " + (trace.status || "running"));
      lines.push("- 阶段: " + (trace.stage || "处理中"));
      lines.push("- 总步骤: " + ((p.total_actions === undefined || p.total_actions === null) ? 0 : p.total_actions));
      lines.push("- 已完成: " + ((p.success_actions === undefined || p.success_actions === null) ? 0 : p.success_actions));
      lines.push("- 失败: " + ((p.failed_actions === undefined || p.failed_actions === null) ? 0 : p.failed_actions));
      lines.push("- 完成率: " + ((p.completion === undefined || p.completion === null) ? 0 : p.completion) + "%");
      lines.push("");
      if(actions.length === 0){
        lines.push("Execution");
        lines.push("- 暂无执行步骤，等待工具调用。");
        return lines.join("\\n");
      }
      lines.push("Execution");
      lines.push("- 已完成循环: " + actions.length + "（仅展示最新）");
      lines.push("");
      const sessionTargetMap = buildSessionTargetMap(actions);
      const idx = actions.length - 1;
      const a = actions[idx];
      const args = expandJsonLike(a.args_raw !== undefined ? a.args_raw : {});
      let target = resolveActionTarget(a, args, sessionTargetMap);
      target = decorateTarget(target, a, args);
      const action = inferActionText(a.name, args);
      const intent = buildExecutionIntent(a, args, target, action);
      lines.push("循环 " + (idx + 1) + " / step " + (a.index || (idx + 1)));
      lines.push("- 思路: " + intent.thought);
      lines.push("- 计划: " + intent.plan);
      lines.push("- 执行: [" + action + "] @ " + target);
      lines.push("- 结论: " + summarizeStepResult(a));
      lines.push("");
      return lines.join("\\n");
    }
    function buildExecutionIntent(a, args, target, action){
      const name = String((a && a.name) || "");
      const cmd = String((args && args.command) || "").toLowerCase();
      const dest = String((args && args.target) || "").trim();
      const source = String((args && args.source) || "").trim();
      const infoType = String((args && args.info_type) || "").trim();
      const configType = String((args && args.config_type) || "").trim();
      const where = (target && target !== "unknown") ? target : "目标设备";
      const atWhere = "在 " + where;
      if(name === "device_ping"){
        const dst = dest || "目标地址";
        const srcText = source ? ("，源地址/接口 " + source) : "";
        return {
          thought: atWhere + " 准备验证到 " + dst + " 的连通性，收集可达性、时延、丢包证据。",
          plan: atWhere + " 执行 ping " + dst + srcText + "，记录成功率与时延。"
        };
      }
      if(name === "device_traceroute"){
        const dst = dest || "目标地址";
        return {
          thought: atWhere + " 准备定位到 " + dst + " 的中断点，收集逐跳路径证据。",
          plan: atWhere + " 执行 traceroute " + dst + "，记录各 hop 的可达情况。"
        };
      }
      if(name === "device_get_info"){
        const t = infoType.toLowerCase();
        if(t === "routing"){
          return {
            thought: atWhere + " 准备检查 routing 信息，收集目标前缀、下一跳、协议来源证据。",
            plan: atWhere + " 读取 routing，确认目标网段是否存在、下一跳是否正确。"
          };
        }
        if(t === "interfaces"){
          return {
            thought: atWhere + " 准备检查 interfaces 信息，收集端口 up/down 与错误计数证据。",
            plan: atWhere + " 读取 interfaces，确认关键端口状态与异常计数。"
          };
        }
        if(t === "arp"){
          return {
            thought: atWhere + " 准备验证二层可达性，收集 ARP 解析完整性证据。",
            plan: atWhere + " 读取 ARP 表，确认目标邻居是否可解析。"
          };
        }
        return {
          thought: atWhere + " 准备读取 " + (infoType || "设备") + " 信息，收集排障证据。",
          plan: atWhere + " 读取 " + (infoType || "通用") + " 信息并提取异常点。"
        };
      }
      if(name === "device_get_config"){
        return {
          thought: atWhere + " 准备读取 " + (configType || "running") + " 配置，收集配置一致性证据。",
          plan: atWhere + " 读取 " + (configType || "running") + " 配置，核对关键路由/接口配置。"
        };
      }
      if(name === "device_connect"){
        return {
          thought: "准备接入设备 " + where + "，收集设备身份、会话可用性与设备模式信息。",
          plan: "连接 " + where + "，确认 session_id 与 hostname。"
        };
      }
      if(name === "device_disconnect"){
        return {
          thought: "准备处理设备 " + where + " 的会话状态，收集复用/释放结果。",
          plan: "处理 " + where + " 会话关闭或保留策略，确保后续步骤可继续。"
        };
      }
      if(name === "device_execute"){
        if(cmd.includes("show ip route")){
          return {
            thought: atWhere + " 准备核对路由表，收集目标前缀与下一跳证据。",
            plan: atWhere + " 执行 " + action + "，确认目标前缀是否在路由表中。"
          };
        }
        if(cmd.includes("ospf neighbor") || cmd.includes("ospf database") || cmd.includes("ip ospf")){
          return {
            thought: atWhere + " 准备检查 OSPF 状态，收集邻接与 LSDB 证据。",
            plan: atWhere + " 执行 " + action + "，确认邻居状态与路由发布是否正常。"
          };
        }
        if(cmd.includes("show interface")){
          return {
            thought: atWhere + " 准备检查接口链路质量，收集端口状态与错误统计证据。",
            plan: atWhere + " 执行 " + action + "，确认关键接口是否 down 或存在丢包。"
          };
        }
        return {
          thought: atWhere + " 准备执行定向诊断命令，收集与故障点相关证据。",
          plan: atWhere + " 执行 " + action + " 并提取关键结果。"
        };
      }
      return {
        thought: atWhere + " 准备执行下一步排障动作，收集可证明故障位置的证据。",
        plan: atWhere + " 执行 " + action + " 并记录结果用于下一轮判断。"
      };
    }
    function summarizeStepResult(a){
      if(a.ok === true){ return "成功"; }
      if(a.ok === false){
        const raw = String(a.result_summary || "").trim();
        if(!raw){ return "失败"; }
        const reason = simplifyFailureReason(raw);
        return reason ? ("失败: " + reason) : "失败";
      }
      return "进行中";
    }
    function simplifyFailureReason(raw){
      const s = String(raw || "");
      if(!s){ return ""; }
      const patterns = [
        /会话\\s+[^\\s，。]+?\\s+不存在/,
        /session\\s+[^\\s,.;]+?\\s+not\\s+found/i,
        /connect\\s+failed/i,
        /timeout/i,
        /认证失败|authentication/i,
        /not in table/i
      ];
      for(let i=0;i<patterns.length;i++){
        const m = s.match(patterns[i]);
        if(m && m[0]){ return m[0].slice(0, 28); }
      }
      return s.replace(/\\s+/g, " ").slice(0, 28);
    }
    function decorateTarget(target, a, args){
      let out = String(target || "unknown");
      if(a && a.name === "device_connect" && a.result_raw && typeof a.result_raw === 'object'){
        const host = String(args && args.host ? args.host : "").trim();
        const port = String(args && args.port ? args.port : "").trim();
        const proto = String(args && args.protocol ? args.protocol : "telnet").trim();
        const hostname = findHostnameInResult(a.result_raw);
        if(hostname){
          out = hostname + (host ? (" (" + host + (port ? ":" + port : "") + "/" + proto + ")") : "");
        }else if(host){
          out = host + (port ? ":" + port : "") + "/" + proto;
        }
      }
      return out;
    }
    function findHostnameInResult(v){
      if(!v){ return ""; }
      if(typeof v === 'object'){
        if(v.hostname && typeof v.hostname === 'string'){ return v.hostname; }
        for(const k in v){
          const found = findHostnameInResult(v[k]);
          if(found){ return found; }
        }
      }else if(typeof v === 'string'){
        const m = v.match(/"hostname"\\s*:\\s*"([^"]+)"/i);
        if(m && m[1]){ return m[1]; }
      }
      return "";
    }
    function updateOpsPanels(data){
      if(data && data.answer){ latestAnswer = data.answer; }
      if(data && data.trace){ latestTrace = data.trace; }
      if(latestTrace){
        tracePreview.textContent = renderThinkingTrace(latestTrace);
        tracePreview.scrollTop = tracePreview.scrollHeight;
        logDetails.textContent = renderLog(latestTrace);
        logDetails.scrollTop = logDetails.scrollHeight;
      }else if(data && data.tool_calls && data.tool_calls.length){
        const raw = JSON.stringify(data.tool_calls, null, 2);
        tracePreview.textContent = "AI 思考循环\\n\\n- 思路: 暂无结构化 trace，回退到工具调用日志。";
        logDetails.textContent = raw;
        logDetails.scrollTop = logDetails.scrollHeight;
      }else{
        tracePreview.textContent = "暂无思路轨迹。";
        logDetails.textContent = "本轮未产生工具调用。";
      }
      cmdPreview.textContent = buildCommandDraft(latestTrace);
      reportPreview.textContent = buildReport(latestAnswer, latestTrace);
    }
    function buildCommandDraft(trace){
      if(!trace || !Array.isArray(trace.actions) || trace.actions.length === 0){
        return "暂无命令建议（等待设备交互产生）。";
      }
      const lines = ["# Command Draft (auto-generated)"];
      const sessionTargetMap = buildSessionTargetMap(trace.actions);
      trace.actions.forEach((a, idx) => {
        const args = expandJsonLike(a.args_raw !== undefined ? a.args_raw : {});
        let target = resolveActionTarget(a, args, sessionTargetMap);
        target = decorateTarget(target, a, args);
        const action = inferActionText(a.name, args);
        lines.push((idx + 1) + ". [" + action + "] @ " + target);
      });
      return lines.join("\\n");
    }
    function buildSessionTargetMap(actions){
      const map = {};
      (actions || []).forEach((a) => {
        const args = expandJsonLike(a.args_raw !== undefined ? a.args_raw : {});
        let target = String((a && a.target) || inferActionTarget(args) || "").trim();
        target = decorateTarget(target, a, args);
        const sids = [];
        if(args && typeof args === 'object' && args.session_id){
          sids.push(String(args.session_id).trim());
        }
        extractSessionIds(a && a.result_raw).forEach((x) => sids.push(x));
        sids.forEach((sid) => {
          const key = String(sid || "").trim();
          if(!key){ return; }
          if(target && !target.startsWith("session:") && target !== "unknown"){
            map[key] = target;
          }
        });
      });
      return map;
    }
    function extractSessionIds(v){
      const out = [];
      const push = (x) => {
        const s = String(x || "").trim();
        if(!s){ return; }
        if(!out.includes(s)){ out.push(s); }
      };
      const walk = (node) => {
        if(!node){ return; }
        if(typeof node === 'string'){
          const re = /["']?session_id["']?\\s*[:=]\\s*["']?([a-zA-Z0-9_-]+)["']?/ig;
          let m;
          while((m = re.exec(node)) !== null){
            if(m[1]){ push(m[1]); }
          }
          return;
        }
        if(Array.isArray(node)){
          node.forEach(walk);
          return;
        }
        if(typeof node === 'object'){
          if(node.session_id){ push(node.session_id); }
          Object.keys(node).forEach((k) => walk(node[k]));
        }
      };
      walk(v);
      return out;
    }
    function resolveActionTarget(a, args, sessionTargetMap){
      let target = String((a && a.target) || inferActionTarget(args) || "unknown").trim();
      if(target.startsWith("session:")){
        const sid = target.slice("session:".length);
        if(sessionTargetMap[sid]){
          target = sessionTargetMap[sid];
        }
      }
      if(args && typeof args === 'object' && args.session_id){
        const sid2 = String(args.session_id).trim();
        if(sid2 && sessionTargetMap[sid2]){
          target = sessionTargetMap[sid2];
        }
      }
      if((!target || target === "unknown" || target.startsWith("session:")) && a && a.result_raw){
        const sids = extractSessionIds(a.result_raw);
        for(let i=0; i<sids.length; i++){
          if(sessionTargetMap[sids[i]]){
            target = sessionTargetMap[sids[i]];
            break;
          }
        }
      }
      return target;
    }
    function inferActionText(name, args){
      if(args && typeof args === 'object' && args.command){ return String(args.command); }
      if(name === "device_ping" && args.target){ return "ping " + args.target; }
      if(name === "device_traceroute" && args.target){ return "traceroute " + args.target; }
      if(name === "device_get_info" && args.info_type){ return "show info " + args.info_type; }
      if(name === "device_get_config" && args.config_type){ return "get config " + args.config_type; }
      if(name === "device_connect"){ return "device_connect"; }
      if(name === "device_disconnect"){ return "device_disconnect"; }
      return String(name || "unknown_action");
    }
    function inferActionTarget(args){
      if(!args || typeof args !== 'object'){ return ""; }
      if(args.host){
        const proto = args.protocol || "telnet";
        if(args.port){ return String(args.host) + ":" + String(args.port) + "/" + String(proto); }
        return String(args.host) + "/" + String(proto);
      }
      if(args.session_id){ return "session:" + String(args.session_id); }
      return "";
    }
    function buildReport(answer, trace){
      const p = (trace && trace.progress) ? trace.progress : {};
      return [
        "## Incident Report (Draft)",
        "状态: " + ((trace && trace.status) || "unknown"),
        "阶段: " + ((trace && trace.stage) || "unknown"),
        "总行动: " + ((p.total_actions === undefined || p.total_actions === null) ? 0 : p.total_actions),
        "成功: " + ((p.success_actions === undefined || p.success_actions === null) ? 0 : p.success_actions),
        "失败: " + ((p.failed_actions === undefined || p.failed_actions === null) ? 0 : p.failed_actions),
        "",
        "诊断摘要:",
        (answer || "暂无"),
      ].join("\\n");
    }
    function switchRightTab(tab){
      [tabTrace, tabLog, tabCmd, tabReport].forEach(x => x.classList.remove('active'));
      [paneTrace, paneLog, paneCmd, paneReport].forEach(x => x.classList.remove('active'));
      if(tab === 'trace'){ tabTrace.classList.add('active'); paneTrace.classList.add('active'); }
      if(tab === 'log'){ tabLog.classList.add('active'); paneLog.classList.add('active'); }
      if(tab === 'cmd'){ tabCmd.classList.add('active'); paneCmd.classList.add('active'); }
      if(tab === 'report'){ tabReport.classList.add('active'); paneReport.classList.add('active'); }
    }
    function copyCommands(){
      const text = cmdPreview.textContent || "";
      if(!text){ return; }
      navigator.clipboard.writeText(text);
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
      addMsg('assistant', "拓扑分析完成，结果已自动注入后续对话上下文。");
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

    text_provider = API_KEY_STATUS.get("text_provider", "unknown")
    vision_provider = API_KEY_STATUS.get("vision_provider", "unknown")
    text_model = API_KEY_STATUS.get("text_model", "unknown")
    text_source = API_KEY_STATUS.get("text_source", "unknown")
    vision_model = API_KEY_STATUS.get("vision_model", "unknown")
    vision_state = "可用" if API_KEY_STATUS.get("vision_usable") else "不可用"
    model_text = f"🟢 文本({text_provider}): {text_model} ({text_source}) | 视觉({vision_provider}): {vision_model} ({vision_state})"

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
            if not API_KEY_STATUS.get("vision_usable"):
                self._send_json(400, {"error": f"视觉模型不可用: {API_KEY_STATUS.get('message', '未知错误')}"}, sid)
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
            if not DEFAULT_TEXT_API_KEY:
                self._send_json(400, {"error": ".env 未配置 ZHIPU_API_KEY（文本模型）"}, sid)
                return
            if not API_KEY_STATUS.get("usable"):
                self._send_json(400, {"error": f"文本模型不可用: {API_KEY_STATUS.get('message', '未知错误')}"}, sid)
                return
            agent = NetOpsAgent(
                api_key=DEFAULT_TEXT_API_KEY,
                provider=DEFAULT_TEXT_PROVIDER,
                base_url=DEFAULT_TEXT_BASE_URL,
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
    global DEFAULT_TEXT_API_KEY, DEFAULT_TEXT_PROVIDER, DEFAULT_TEXT_BASE_URL
    global DEFAULT_VISION_API_KEY, DEFAULT_VISION_PROVIDER, DEFAULT_VISION_BASE_URL
    global DEFAULT_TEXT_MODEL, DEFAULT_VISION_MODEL, DEFAULT_VISION_FALLBACK_MODEL, AUTO_SELECT_TEXT_MODEL
    load_dotenv()
    DEFAULT_TEXT_PROVIDER = "zhipu"
    DEFAULT_TEXT_BASE_URL = "https://open.bigmodel.cn/api/paas/v4/"
    DEFAULT_VISION_PROVIDER = "ppio"
    DEFAULT_VISION_BASE_URL = os.getenv("AI_VISION_BASE_URL", "").strip() or "https://api.ppio.com/openai"
    DEFAULT_TEXT_MODEL = "glm-4.7"
    DEFAULT_VISION_MODEL = os.getenv("AI_VISION_MODEL", "").strip() or "qwen/qwen3-vl-235b-a22b-thinking"
    DEFAULT_VISION_FALLBACK_MODEL = os.getenv("AI_VISION_FALLBACK_MODEL", "").strip()
    AUTO_SELECT_TEXT_MODEL = os.getenv("AUTO_SELECT_TEXT_MODEL", "false").strip().lower() in {"1", "true", "yes", "on"}
    DEFAULT_TEXT_API_KEY = os.getenv("ZHIPU_API_KEY", "").strip()
    DEFAULT_VISION_API_KEY = os.getenv("PPIO_API_KEY", "").strip()
    probe_default_api_key()
    server = ThreadingHTTPServer((HOST, PORT), Handler)
    print(f"Web UI 启动: http://{HOST}:{PORT}")
    server.serve_forever()


def probe_default_api_key() -> None:
    API_KEY_STATUS["provider"] = f"text:{DEFAULT_TEXT_PROVIDER}, vision:{DEFAULT_VISION_PROVIDER}"
    API_KEY_STATUS["text_provider"] = DEFAULT_TEXT_PROVIDER
    API_KEY_STATUS["vision_provider"] = DEFAULT_VISION_PROVIDER
    API_KEY_STATUS["configured"] = bool(DEFAULT_TEXT_API_KEY and DEFAULT_VISION_API_KEY)
    API_KEY_STATUS["usable"] = False
    API_KEY_STATUS["vision_usable"] = False
    API_KEY_STATUS["text_model"] = DEFAULT_TEXT_MODEL
    API_KEY_STATUS["vision_model"] = DEFAULT_VISION_MODEL
    API_KEY_STATUS["vision_fallback_model"] = DEFAULT_VISION_FALLBACK_MODEL
    if not DEFAULT_TEXT_API_KEY:
        API_KEY_STATUS["message"] = "未配置 ZHIPU_API_KEY（文本模型）"
        API_KEY_STATUS["text_source"] = ""
        return

    try:
        probe_agent = NetOpsAgent(
            api_key=DEFAULT_TEXT_API_KEY,
            provider=DEFAULT_TEXT_PROVIDER,
            base_url=DEFAULT_TEXT_BASE_URL,
            model=DEFAULT_TEXT_MODEL or None,
            auto_select_model=AUTO_SELECT_TEXT_MODEL,
        )
        TEXT_MODEL_INFO.update(probe_agent.get_model_info())
        text_client = OpenAI(api_key=DEFAULT_TEXT_API_KEY, base_url=DEFAULT_TEXT_BASE_URL)
        text_client.chat.completions.create(
            model=TEXT_MODEL_INFO["model"],
            messages=[{"role": "user", "content": "ping"}],
            max_tokens=16,
        )
        vision_ok = False
        if DEFAULT_VISION_API_KEY:
            # Probe image capability using dedicated vision provider.
            test_img_url = "https://picsum.photos/96"
            try:
                vision_client = OpenAI(api_key=DEFAULT_VISION_API_KEY, base_url=DEFAULT_VISION_BASE_URL)
                vision_client.chat.completions.create(
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
        if DEFAULT_VISION_API_KEY:
            API_KEY_STATUS["message"] = "OK"
        else:
            API_KEY_STATUS["message"] = "文本模型可用，未配置 PPIO_API_KEY（视觉模型）"
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
    if not DEFAULT_VISION_API_KEY:
        raise RuntimeError("未配置 PPIO_API_KEY（视觉模型）")
    vision_url = normalize_image_for_vision(image_data_url)
    validate_image_url_for_vision(vision_url)

    client = OpenAI(api_key=DEFAULT_VISION_API_KEY, base_url=DEFAULT_VISION_BASE_URL)
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
            target = str(payload.get("target", "")).strip()
            actions.append(
                {
                    "index": idx,
                    "name": str(payload.get("name", "")),
                    "target": target,
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
        call_args = to_jsonable(call.get("args"))
        target = str(call.get("target") or infer_target_from_args(call_args))
        args_summary = compact_text(call.get("args"), max_len=200)
        result_summary = compact_text(call.get("result"), max_len=280)
        actions.append(
            {
                "index": idx,
                "name": str(call.get("name", "")),
                "target": target,
                "ok": ok,
                "args_raw": call_args,
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


def infer_target_from_args(args: Any) -> str:
    if not isinstance(args, dict):
        return ""
    host = str(args.get("host", "")).strip()
    port = str(args.get("port", "")).strip()
    proto = str(args.get("protocol", "telnet")).strip() or "telnet"
    if host and port:
        return f"{host}:{port}/{proto}"
    if host:
        return f"{host}/{proto}"
    sid = str(args.get("session_id", "")).strip()
    if sid:
        return f"session:{sid}"
    return ""


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

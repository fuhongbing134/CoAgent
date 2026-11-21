PAGE = r"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Agent 工作台</title>
  <style>
    :root { color-scheme: dark; --bg:#101216; --panel:#171a20; --panel2:#1d2129; --line:#2b303a; --text:#edf0f5; --muted:#98a1b1; --accent:#a8c7fa; --green:#7bd8b0; }
    * { box-sizing:border-box; }
    html,body { width:100%;height:100%;overflow:hidden; }
    body { margin:0; min-height:0; background:radial-gradient(ellipse at 65% -20%,#263348 0,transparent 42%),var(--bg); color:var(--text); font:14px/1.5 "Segoe UI",system-ui,sans-serif; }
    button,select { color:inherit; font:inherit; }
    .app { width:100%;height:100vh;height:100dvh;min-height:0;overflow:hidden;display:grid;grid-template-columns:290px minmax(0,1fr); }
    aside { min-height:0;height:100vh;height:100dvh;overflow-y:auto;border-right:1px solid var(--line);background:rgba(19,22,28,.88);padding:24px 17px; }
    .brand { display:flex; align-items:center; gap:12px; padding:2px 8px 25px; }
    .logo { width:36px;height:36px;border-radius:12px;display:grid;place-items:center;background:linear-gradient(145deg,#a8c7fa,#b8ead5);color:#101216;font-weight:800;font-size:17px; }
    .brand-name { font-size:16px;font-weight:700;letter-spacing:.1px; }
    .brand-sub { color:var(--muted);font-size:11px;margin-top:2px; }
    .avatar { flex:none;width:32px;height:32px;border-radius:10px;display:grid;place-items:center;font-weight:750;color:#12151b;background:#a8c7fa; }
    .avatar.deepseek { background:#b8ead5; }
    .avatar.grok { background:#d9c2f5; }
    .side-note { color:#788292;font-size:11px;line-height:1.6;padding:14px 8px; }
    main { width:100%;height:100vh;height:100dvh;min-width:0;min-height:0;overflow:hidden;display:grid;grid-template-rows:auto minmax(0,1fr) auto; }
    .topbar { min-height:68px;position:relative;z-index:2;border-bottom:1px solid var(--line);display:flex;align-items:center;justify-content:space-between;padding:12px clamp(18px,4vw,48px);gap:16px;background:rgba(16,18,22,.88);backdrop-filter:blur(12px); }
    .title { font-size:16px;font-weight:700; }
    .subtitle { color:var(--muted);font-size:12px;margin-top:3px; }
    .tools { display:flex;align-items:center;gap:12px; }
    select { background:var(--panel2);border:1px solid var(--line);border-radius:9px;padding:8px 30px 8px 10px;outline:none; }
    .refresh { display:flex;align-items:center;gap:8px;color:var(--muted);font-size:11px;white-space:nowrap; }
    .refresh-copy { display:grid;gap:1px; }
    .refresh-frequency { color:#707b8a;font-size:10px; }
    .dot { width:7px;height:7px;background:var(--green);border-radius:50%;box-shadow:0 0 9px #7bd8b077; }
    .stop-button { border:1px solid #78454a;border-radius:8px;padding:6px 10px;background:#392326;color:#ffc2c2;font-size:11px;cursor:pointer; }
    .stop-button:hover:not(:disabled) { background:#512b30; }
    .stop-button:disabled { opacity:.45;cursor:not-allowed; }
    .message-stop { margin-top:9px; }
    .stream { min-height:0;overflow-y:auto;overflow-x:hidden;overscroll-behavior:contain;scrollbar-gutter:stable;width:100%;margin:0;padding:18px clamp(18px,4vw,48px) 22px; }
    .day,#messages,#error { width:min(960px,100%);margin-left:auto;margin-right:auto; }
    .day { text-align:center;color:#7f8998;font-size:10px;letter-spacing:.06em;margin-top:5px;margin-bottom:22px; }
    #messages { display:grid;gap:18px; }
    .message { display:flex;gap:11px;max-width:88%;align-items:flex-start; }
    .message.user { margin-left:auto;flex-direction:row-reverse; }
    .message.user .avatar { background:#f2c879; }
    .message .avatar { width:30px;height:30px;border-radius:9px;font-size:12px; }
    .bubble { min-width:150px;max-width:100%;border:1px solid var(--line);border-radius:4px 13px 13px 13px;background:var(--panel);padding:11px 14px;box-shadow:0 6px 24px #0002; }
    .message.user .bubble { border-radius:13px 4px 13px 13px;background:#1b2430;border-color:#303e50; }
    .meta { color:#aeb8c8;font-size:11px;font-weight:650;display:flex;justify-content:space-between;gap:16px;margin-bottom:8px; }
    .time { color:#768092;font-weight:400;white-space:nowrap; }
    .content { color:#e1e5ec;white-space:pre-wrap;overflow-wrap:anywhere;font-size:13px; }
    .empty { min-height:min(45vh,420px);display:grid;place-content:center;text-align:center;color:var(--muted); }
    .empty strong { color:#d8deea;font-size:15px;margin-bottom:7px; }
    .empty span { font-size:12px; }
    .pending { color:var(--muted);font-style:italic; }
    .failed { color:#ffaaa6; }
    .cancelled { color:var(--muted);font-style:italic; }
    .composer { position:relative;z-index:3;width:100%;margin:0;padding:10px clamp(18px,4vw,48px) 13px;border-top:1px solid var(--line);background:rgba(16,18,22,.94);backdrop-filter:blur(14px); }
    .compose-box { position:relative;border:1px solid #39414e;background:var(--panel);border-radius:14px;padding:11px 12px 9px;box-shadow:0 12px 36px #0003; }
    textarea { display:block;width:100%;min-height:60px;max-height:180px;resize:vertical;border:0;outline:0;background:transparent;color:var(--text);font:13px/1.5 "Segoe UI",system-ui,sans-serif; }
    .mention-suggestions { position:absolute;z-index:5;left:12px;right:12px;bottom:58px;display:grid;gap:3px;max-height:190px;overflow-y:auto;padding:5px;border:1px solid #39414e;border-radius:10px;background:#202630;box-shadow:0 12px 30px #0008; }
    .mention-suggestions[hidden] { display:none; }
    .mention-option { display:flex;align-items:center;justify-content:space-between;gap:12px;width:100%;padding:8px 10px;border:0;border-radius:7px;background:transparent;color:var(--text);text-align:left;cursor:pointer; }
    .mention-option:hover,.mention-option.active { background:#303947; }
    .mention-option-label { display:grid;gap:1px; }
    .mention-option-name { font-weight:650; }
    .mention-option-description { color:var(--muted);font-size:11px; }
    .mention-option-token { flex:none;color:#c9d9f5;font:12px/1.4 Consolas,"Courier New",monospace; }
    textarea::placeholder { color:#737d8c; }
    .compose-bottom { display:flex;align-items:center;justify-content:space-between;gap:12px;padding-top:8px;border-top:1px solid #292e37; }
    .compose-controls { display:flex;align-items:center;gap:10px;min-width:0; }
    .compose-actions { display:flex;align-items:center;gap:8px;flex:none; }
    .compose-hint { color:#7f8998;font-size:10px;line-height:1.4; }
    .send-button { border:0;border-radius:9px;padding:8px 14px;background:#b8d0f7;color:#111720;font-weight:700;cursor:pointer; }
    .send-button:hover:not(:disabled) { background:#c8dcfb; }
    .send-button:disabled, textarea:disabled { opacity:.55;cursor:not-allowed; }
    #error { color:#ffaaa6;padding:12px;display:none;text-align:center; }
    .agent-summary { border:1px solid var(--line);border-radius:10px;padding:10px;margin-bottom:16px;font-size:12px; }
    #task-panel { min-width:0; }
    #task-panel > summary { cursor:pointer;font-weight:650;padding:8px 0; }
    #collaboration-tasks { min-width:0; }
    #collaboration-tasks .bubble { min-width:0;margin:10px 0;overflow-wrap:anywhere; }
    #collaboration-tasks pre { white-space:pre-wrap;overflow-wrap:anywhere; }
    #collaboration-tasks .task-controls { display:flex;flex-wrap:wrap;gap:6px; }
    #collaboration-tasks input { min-width:0;max-width:100%; }
    #collaboration-tasks textarea { border:1px solid var(--line);padding:6px; }
    @media (max-width:760px) {
      .app { grid-template-columns:1fr;grid-template-rows:auto minmax(0,1fr); }
      aside { height:auto;max-height:45dvh;border-right:0;border-bottom:1px solid var(--line);padding:10px 14px;overflow-x:hidden; }
      .brand { padding:0 6px 9px; }
      .brand-sub { display:none; }
      .agent-summary { padding:6px 10px;margin-bottom:4px; }
      #collaboration-tasks { max-height:25dvh;overflow-y:auto; }
      main { height:auto; }
      .topbar { min-height:58px;padding:9px 14px; }
      .topbar .title { font-size:14px; }
      .topbar .subtitle { font-size:10px; }
      .tools { gap:8px; }
      select { max-width:42vw;padding:7px 23px 7px 8px;font-size:11px; }
      .refresh { gap:6px;font-size:10px; }
      .refresh-frequency { display:none; }
      .stream { padding:14px 12px 18px; }
      .message { max-width:96%; }
      .composer { padding:8px 10px 10px; }
      .compose-box { padding:9px 9px 8px; }
      textarea { min-height:52px; }
      .compose-bottom { align-items:flex-end;gap:8px; }
      .compose-hint { max-width:42vw;font-size:9px; }
      .compose-actions { gap:6px; }
      .send-button { padding:8px 10px;font-size:11px; }
      .stop-button { padding:6px 8px;font-size:10px; }
    }
  </style>
</head>
<body>
  <div class="app">
    <aside id="task-sidebar">
      <div class="brand"><div class="logo">A</div><div><div class="brand-name">Agent 工作台</div><div class="brand-sub">本机对话 · Codex 与 DeepSeek Harness</div></div></div>
      <section class="agent-summary" aria-label="可用 Agent"><div id="agents"></div></section>
      <details id="task-panel" open>
        <summary id="task-count">任务（0）</summary>
        <div id="collaboration-tasks"></div>
      </details>
    </aside>
    <main>
      <header class="topbar">
        <div><div class="title">共享对话</div><div class="subtitle">通过 @ 提及选择接收对象</div></div>
        <div class="tools"><select id="conversation" aria-label="选择对话"></select><div class="refresh"><span class="dot"></span><div class="refresh-copy"><span id="status">正在连接</span><span class="refresh-frequency">每 3 秒自动更新</span></div></div></div>
      </header>
      <section class="stream" id="stream" aria-live="polite">
        <div class="day">消息按发送时间排列</div>
        <div id="messages"></div>
        <div id="error"></div>
      </section>
      <form class="composer" id="composer">
        <div class="compose-box">
          <textarea id="message-input" maxlength="20000" placeholder="输入消息……默认发给所有 Agent；输入 @ 查看候选，按 Tab 补全（Enter 发送，Shift+Enter 换行）" aria-label="输入消息" aria-autocomplete="list" aria-controls="mention-suggestions" aria-expanded="false"></textarea>
          <div class="mention-suggestions" id="mention-suggestions" role="listbox" aria-label="Agent 提及候选" hidden></div>
          <div class="compose-bottom"><div class="compose-controls"><span class="compose-hint" id="compose-hint">默认同时发送给所有 Agent；用 @ 提及可指定对象，普通 @ 字符不会触发路由。右侧 Stop 可停止全部运行中的 Agent。</span></div><div class="compose-actions"><button class="stop-button" id="stop-all" type="button" disabled>Stop</button><button class="send-button" id="send-button" type="submit">发送给所有 Agent</button></div></div>
        </div>
      </form>
    </main>
  </div>
<script>
const capabilityToken = "__CAPABILITY_TOKEN__";
const agentsNode = document.getElementById("agents");
const messagesNode = document.getElementById("messages");
const conversationNode = document.getElementById("conversation");
const errorNode = document.getElementById("error");
const statusNode = document.getElementById("status");
const streamNode = document.getElementById("stream");
const composerNode = document.getElementById("composer");
const inputNode = document.getElementById("message-input");
const sendButton = document.getElementById("send-button");
const stopAllButton = document.getElementById("stop-all");
const composeHint = document.getElementById("compose-hint");
const mentionSuggestionsNode = document.getElementById("mention-suggestions");
const mentionOptions = [
  {token:"@Codex", label:"Codex", description:"只由 Codex 回复", prefixes:["codex", "openai"]},
  {token:"@DeepSeek Harness", label:"DeepSeek Harness", description:"只由 DeepSeek Harness 回复", prefixes:["deepseek harness", "deepseekharness", "deepseek"]},
  {token:"@Grok", label:"Grok", description:"只由 Grok 回复", prefixes:["grok", "grok-build", "xai"]},
  {token:"@all", label:"所有 Agent", description:"同时发送给所有 Agent", prefixes:["all", "everyone", "所有", "全部"]}
];
let selectedConversation = "default";
let refreshVersion = 0;
let refreshPending = false;
let refreshPromise = null;
let initialized = false;
let previousMessageIds = "";
let pendingAgents = new Set();
let availableAgents = {codex:false, "deepseek-harness":false, grok:false};
let submitting = false;
let stopAllSubmitting = false;
let activeMentionIndex = 0;
let currentMentionKey = "";
let dismissedMentionStart = null;
let visibleMentionOptions = [];

function textElement(tag, className, text) {
  const element = document.createElement(tag);
  if (className) element.className = className;
  if (text !== undefined) element.textContent = text;
  return element;
}

function formatTime(value) {
  if (!value) return "未知";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return date.toLocaleString("zh-CN", {year:"numeric",month:"2-digit",day:"2-digit",hour:"2-digit",minute:"2-digit",second:"2-digit"});
}

async function getJson(path) {
  const controller = new AbortController();
  const timeout = setTimeout(function() { controller.abort(); }, 10000);
  try {
    const response = await fetch(path, {cache:"no-store", signal:controller.signal});
    if (!response.ok) throw new Error("HTTP " + response.status);
    return await response.json();
  } finally { clearTimeout(timeout); }
}

function agentAvailability(agents, codexAvailable, dshAvailable, grokAvailable) {
  return {
    codex: Boolean(codexAvailable),
    "deepseek-harness": Boolean(dshAvailable && agents.some(agent => agent.agent_id === "deepseek-harness")),
    grok: Boolean(grokAvailable)
  };
}

function renderAgents(availability) {
  const names = Object.keys(availability).filter(id => availability[id]).map(agentLabel);
  const label = names.join(" / ") || "暂无可用 Agent";
  if (agentsNode.textContent !== label) agentsNode.textContent = label;
}

function renderConversations(conversations) {
  if (!conversations.some(function(item) { return item.conversation_id === "default"; })) {
    conversations.unshift({conversation_id:"default", message_count:0});
  }
  const ids = conversations.map(function(item) { return item.conversation_id; });
  if (!ids.includes(selectedConversation)) selectedConversation = ids[0] || "default";
  conversationNode.replaceChildren();
  for (const item of conversations) {
    const option = document.createElement("option");
    option.value = item.conversation_id;
    option.textContent = item.conversation_id + " · " + item.message_count + " 条";
    conversationNode.append(option);
  }
  conversationNode.value = selectedConversation;
}

function renderRecipients(agents, codexAvailable, dshAvailable, grokAvailable) {
  availableAgents = agentAvailability(agents, codexAvailable, dshAvailable, grokAvailable);
  renderAgents(availableAgents);
}

function recipientsForText(content) {
  const masked = content.replace(/```[\s\S]*?(?:```|$)|`[^`\r\n]*`|https?:\/\/\S+/g, m => " ".repeat(m.length));
  // Grok joins broadcasts only while its CLI is available, so a missing Grok never blocks Codex/DSH.
  const everyone = availableAgents.grok ? ["codex", "deepseek-harness", "grok"] : ["codex", "deepseek-harness"];
  if (/(^|[^A-Za-z0-9_@\\])@(?:all|everyone|\u6240\u6709|\u5168\u90e8)(?![A-Za-z0-9_])/i.test(masked)) return everyone;
  const pattern = /(^|[^A-Za-z0-9_@\\])@(?:(codex|openai)|(deepseek\s+harness|deepseekharness|deepseek)|(grok-build|grok|xai))(?![A-Za-z0-9_]|\.[A-Za-z])/ig;
  const match = pattern.exec(masked);
  return match ? [match[2] ? "codex" : match[3] ? "deepseek-harness" : "grok"] : everyone;
}

function isInsideCode(text, position) {
  const before = text.slice(0, position);
  const fenceCount = (before.match(/```/g) || []).length;
  if (fenceCount % 2 === 1) return true;
  const lastFence = before.lastIndexOf("```");
  const inlineText = lastFence < 0 ? before : before.slice(lastFence + 3);
  return ((inlineText.match(/`/g) || []).length % 2) === 1;
}

function getMentionContext() {
  const caret = inputNode.selectionStart;
  if (caret !== inputNode.selectionEnd) return null;
  const beforeCaret = inputNode.value.slice(0, caret);
  const start = beforeCaret.lastIndexOf("@");
  if (start < 0 || isInsideCode(inputNode.value, start)) return null;
  const nextChar = inputNode.value[caret] || "";
  if (/[A-Za-z0-9_]/.test(nextChar)) return null;
  const previousChar = start > 0 ? inputNode.value[start - 1] : "";
  if (previousChar && /[A-Za-z0-9_@\\]/.test(previousChar)) return null;
  const query = beforeCaret.slice(start + 1);
  if (/[\r\n]/.test(query)) return null;
  const normalizedQuery = query.toLowerCase().replace(/\s+/g, " ");
  const matches = mentionOptions.filter(function(option) {
    return option.prefixes.some(function(prefix) {
      return !normalizedQuery || prefix.startsWith(normalizedQuery);
    });
  });
  if (!matches.length) return null;
  return {start:start, end:caret, key:start + ":" + query, options:matches};
}

function hideMentionSuggestions() {
  mentionSuggestionsNode.hidden = true;
  inputNode.setAttribute("aria-expanded", "false");
  mentionSuggestionsNode.replaceChildren();
  visibleMentionOptions = [];
  currentMentionKey = "";
  activeMentionIndex = 0;
}

function updateMentionSuggestions() {
  const context = getMentionContext();
  if (!context || context.start === dismissedMentionStart) {
    hideMentionSuggestions();
    return;
  }
  if (context.key !== currentMentionKey) {
    currentMentionKey = context.key;
    activeMentionIndex = 0;
  }
  visibleMentionOptions = context.options;
  mentionSuggestionsNode.replaceChildren();
  visibleMentionOptions.forEach(function(option, index) {
    const button = document.createElement("button");
    button.type = "button";
    button.className = "mention-option" + (index === activeMentionIndex ? " active" : "");
    button.setAttribute("role", "option");
    button.setAttribute("aria-selected", index === activeMentionIndex ? "true" : "false");
    button.dataset.index = String(index);
    const label = textElement("span", "mention-option-label");
    label.append(textElement("span", "mention-option-name", option.label));
    label.append(textElement("span", "mention-option-description", option.description));
    button.append(label);
    button.append(textElement("span", "mention-option-token", option.token));
    mentionSuggestionsNode.append(button);
  });
  mentionSuggestionsNode.hidden = false;
  inputNode.setAttribute("aria-expanded", "true");
}

function completeMention(index) {
  const context = getMentionContext();
  const option = visibleMentionOptions[index];
  if (!context || !option) return false;
  const suffix = inputNode.value.slice(context.end);
  const needsSpace = !suffix || !/^[\s,，。.!?！？:：;；]/.test(suffix);
  const insertion = option.token + (needsSpace ? " " : "");
  inputNode.value = inputNode.value.slice(0, context.start) + insertion + suffix;
  const caret = context.start + insertion.length;
  inputNode.setSelectionRange(caret, caret);
  dismissedMentionStart = context.start;
  hideMentionSuggestions();
  inputNode.focus();
  updateComposer();
  return true;
}

function agentLabel(agentId) {
  return ({codex:"Codex", "deepseek-harness":"DeepSeek Harness", grok:"Grok"})[agentId] || String(agentId);
}

function updateComposer() {
  updateMentionSuggestions();
  const recipients = recipientsForText(inputNode.value);
  const busy = recipients.filter(function(agentId) { return pendingAgents.has(agentId); });
  const unavailable = recipients.filter(function(agentId) { return !availableAgents[agentId]; });
  const recipientLabel = recipients.length >= 2
    ? "所有 Agent"
    : recipients.map(agentLabel).join(" 和 ");
  inputNode.disabled = submitting;
  sendButton.disabled = submitting || busy.length > 0 || unavailable.length > 0;
  sendButton.textContent = submitting ? "正在发送…" : "发送给" + recipientLabel;
  if (submitting) {
    composeHint.textContent = "正在提交消息…";
  } else if (!mentionSuggestionsNode.hidden) {
    composeHint.textContent = "用 ↑↓ 选择候选，按 Tab 补全；按 Enter 发送当前文本，按 Esc 关闭候选。";
  } else if (unavailable.length) {
    composeHint.textContent = unavailable.map(agentLabel).join("、")
      + " 当前不可用；请检查该 Agent 的 CLI 和登记状态，或只 @ 可用的 Agent。";
  } else if (busy.length) {
    composeHint.textContent = busy.map(agentLabel).join("、")
      + " 正在处理当前对话。可以 @ 另一个空闲 Agent 并行发送。";
  } else {
    composeHint.textContent = "默认同时发送给所有 Agent；输入 @ 查看候选，按 Tab 补全；不补全则按当前文本发送。邮箱、代码和转义的 @ 不触发；"
      + String.fromCharCode(92) + "@Codex 可按原文发送。";
  }
}

function renderMessages(messages) {
  const nearBottom = streamNode.scrollHeight - streamNode.scrollTop - streamNode.clientHeight < 100;
  const ids = messages.map(function(item) { return item.source + ":" + item.id + ":" + item.status; }).join(",");
  const changed = ids !== previousMessageIds;
  previousMessageIds = ids;
  messagesNode.replaceChildren();
  pendingAgents = new Set(messages
    .filter(function(item) { return item.status === "pending"; })
    .map(function(item) { return item.sender_id; }));
  updateComposer();
  if (!messages.length) {
    const empty = textElement("div", "empty");
    empty.append(textElement("strong", "", "开始一段对话"));
    empty.append(textElement("span", "", "发送一条消息即可同时获得所有 Agent 的回复，也可以用 @ 指定对象。"));
    messagesNode.append(empty);
    return;
  }
  for (const message of messages) {
    const isUser = message.sender_id === "user";
    const row = textElement("article", "message" + (isUser ? " user" : ""));
    const avatar = textElement("div", "avatar" + (String(message.sender_id).includes("deepseek") ? " deepseek" : message.sender_id === "grok" ? " grok" : ""), String(message.sender_name || message.sender_id || "?").slice(0,1));
    const bubble = textElement("div", "bubble");
    const meta = textElement("div", "meta");
    meta.append(textElement("span", "", (message.sender_name || message.sender_id) + " → " + (message.recipient_name || message.recipient_id)));
    meta.append(textElement("time", "time", formatTime(message.created_at)));
    bubble.append(meta);
    let content = message.content || "";
    let contentClass = "content";
    if (message.status === "pending") { content = (message.sender_name || "Agent") + " 正在思考…"; contentClass += " pending"; }
    if (message.status === "failed") contentClass += " failed";
    if (message.status === "cancelled") contentClass += " cancelled";
    const protocolStart = content.indexOf("```agent-collaboration");
    if (protocolStart >= 0 && message.status === "completed" && !isUser) {
      bubble.append(textElement("div", contentClass, content.slice(0,protocolStart).trim()));
      const protocolDetails = document.createElement("details");
      protocolDetails.append(textElement("summary", "", "协作协议（调试）"), textElement("pre", "content", content.slice(protocolStart)));
      bubble.append(protocolDetails);
    } else { bubble.append(textElement("div", contentClass, content)); }
    if (message.status === "pending" && message.source === "workbench") {
      const stopButton = textElement("button", "stop-button message-stop", "停止此 Agent");
      stopButton.type = "button";
      stopButton.dataset.stopMessageId = String(message.id);
      stopButton.setAttribute("aria-label", "停止" + (message.sender_name || message.sender_id));
      bubble.append(stopButton);
    }
    row.append(avatar, bubble);
    messagesNode.append(row);
  }
  if (!initialized || (changed && nearBottom)) streamNode.scrollTop = streamNode.scrollHeight;
  initialized = true;
}

// Task controls use textContent throughout, including Agent/user supplied text.
const collaborationDrafts = new Map();
const collaborationRoot = document.createElement("section");
collaborationRoot.setAttribute("aria-label", "Agent 协作任务");
const taskPanel = document.getElementById("task-panel");
const taskCount = document.getElementById("task-count");
const taskHost = document.getElementById("collaboration-tasks");
const taskSidebar = document.getElementById("task-sidebar");
taskHost.append(collaborationRoot);
if (window.matchMedia("(max-width:760px)").matches) taskPanel.open = false;
const collaborationCards = new Map();
let collaborationSignature = "";
let collaborationConversation = selectedConversation;
let collaborationEpoch = 0;

function resetCollaboration(conversation) {
  collaborationConversation = conversation;
  collaborationEpoch++;
  collaborationSignature = "";
  collaborationCards.clear();
  collaborationRoot.replaceChildren();
  taskCount.textContent = "任务（0）";
}

async function collaborationAction(task, action, button, content = "", roundLimit = null) {
  if (button.dataset.conversation !== selectedConversation || button.dataset.epoch !== String(collaborationEpoch)) return;
  button.disabled = true;
  const conversation = selectedConversation;
  const operationId = button.dataset.operationId || crypto.randomUUID();
  button.dataset.operationId = operationId;
  try {
    const body = {job_id:task.state.job_id, conversation_id:conversation, operation_id:operationId, action, content};
    if (roundLimit !== null) body.round_limit = roundLimit;
    const response = await fetch("/api/collaboration/action", {
      method:"POST", headers:{"Content-Type":"application/json", "X-Capability-Token":capabilityToken},
      body:JSON.stringify(body)
    });
    const data = await response.json();
    if (!response.ok) throw new Error(data.error || "操作失败");
    delete button.dataset.operationId;
    collaborationSignature = "";
    if (action === "answer") collaborationDrafts.delete(task.state.job_id);
    await refreshAll();
  } catch (error) {
    alert(String(error.message || error));
  } finally { button.disabled = false; }
}

function renderCollaboration(tasks, conversation) {
  if (conversation !== selectedConversation) return;
  if (conversation !== collaborationConversation) resetCollaboration(conversation);
  const signature = JSON.stringify([conversation,tasks]);
  if (signature === collaborationSignature) return;
  collaborationSignature = signature;
  const sidebarScroll = taskSidebar.scrollTop;
  const hostScroll = taskHost.scrollTop;
  taskCount.textContent = "任务（" + tasks.length + "）";
  const ids = new Set(tasks.map(task => task.state.job_id));
  for (const [id, saved] of collaborationCards) {
    if (!ids.has(id)) { saved.card.remove(); collaborationCards.delete(id); }
  }
  const labels = {working:"协作中",waiting_user:"等待你回答",blocked:"受阻",quiescing:"正在停止后续工作",
    final_ready:"等待最终汇总",final_delivery:"最终汇总中",completed:"已交付",failed:"失败",interrupted:"已中断",stopped:"已停止"};
  for (const task of tasks) {
    const state = task.state;
    const saved = collaborationCards.get(state.job_id);
    const taskSignature = JSON.stringify(task);
    if (saved && saved.signature === taskSignature) continue;
    const card = saved ? saved.card : textElement("article", "bubble");
    const oldControls = card.querySelector(".task-controls");
    if (oldControls && Array.from(oldControls.children).some(node => node.disabled)) {
      collaborationSignature = "";
      continue;
    }
    const oldDetails = card.querySelector("details");
    const oldAnswer = card.querySelector("textarea");
    const oldLimit = card.querySelector("input");
    const focused = document.activeElement;
    const focusKind = oldAnswer && focused === oldAnswer ? "textarea" : oldLimit && focused === oldLimit ? "input" : null;
    const selection = oldAnswer && [oldAnswer.selectionStart, oldAnswer.selectionEnd];
    if (oldAnswer) collaborationDrafts.set(state.job_id, oldAnswer.value);
    card.replaceChildren();
    card.append(textElement("strong", "", (labels[state.phase] || state.phase) + " · " + state.used + "/" + state.round_limit + " 轮"));
    card.append(textElement("div", "", "负责人：" + agentLabel(task.plan.owner_id) + "；最终答复：" + agentLabel(task.plan.final_responder_id)));
    if (state.reason) card.append(textElement("p", "", state.reason));
    const details = document.createElement("details");
    details.open = oldDetails ? oldDetails.open : false;
    details.append(textElement("summary", "", "需求与分工"), textElement("pre", "content", task.request + "\n" + task.plan.tasks.map(t => t.agent_id + ": " + t.instruction).join("\n")));
    card.append(details);
    const controls = document.createElement("div");
    controls.className = "task-controls";
    function add(label, action, contentProvider) {
      const button = textElement("button", "stop-button", label);
      button.type = "button";
      button.dataset.conversation = conversation;
      button.dataset.epoch = String(collaborationEpoch);
      button.onclick = () => collaborationAction(task,action,button,contentProvider ? contentProvider() : "");
      controls.append(button);
    }
    if (["waiting_user","blocked"].includes(state.phase)) {
      let questions = [];
      try { questions = JSON.parse(state.questions); } catch (_) {}
      card.append(textElement("p", "", "需要你回答：" + (questions.join("；") || state.reason)));
      const answer = document.createElement("textarea");
      answer.placeholder = "补充信息后继续，也可直接收尾";
      answer.value = collaborationDrafts.get(state.job_id) || "";
      answer.oninput = () => collaborationDrafts.set(state.job_id,answer.value);
      card.append(answer);
      add("提交回答并继续","answer",() => answer.value);
    }
    if (["working","waiting_user","blocked","final_ready"].includes(state.phase)) add("按当前成果给结论","finalize");
    if (!["completed","stopped","interrupted"].includes(state.phase)) add("立即停止","stop");
    if (["working","waiting_user","blocked"].includes(state.phase)) {
      const limit = document.createElement("input");
      limit.type = "number"; limit.min = "2"; limit.max = "100"; limit.value = state.round_limit;
      if (oldLimit && String(oldLimit.value) !== String(saved.roundLimit)) limit.value = oldLimit.value;
      limit.setAttribute("aria-label", "轮次上限");
      controls.append(limit);
      const save = textElement("button", "stop-button", "调整上限");
      save.dataset.conversation = conversation;
      save.dataset.epoch = String(collaborationEpoch);
      save.onclick = () => collaborationAction(task,"set_limit",save,"",Number(limit.value));
      controls.append(save);
    }
    card.append(controls);
    if (["stopped","completed","interrupted"].includes(state.phase)) {
      card.append(textElement("p", "", "已有改动未自动回退；本任务可能涉及文件修改，范围及完整性未经核实。"));
    }
    collaborationCards.set(state.job_id, {card, signature:taskSignature, roundLimit:state.round_limit});
    if (!saved) collaborationRoot.append(card);
    if (focusKind) {
      const replacement = card.querySelector(focusKind);
      if (replacement) {
        replacement.focus({preventScroll:true});
        if (focusKind === "textarea") replacement.setSelectionRange(...selection);
      }
    }
  }
  tasks.forEach((task, index) => {
    const card = collaborationCards.get(task.state.job_id).card;
    if (collaborationRoot.children[index] !== card)
      collaborationRoot.insertBefore(card, collaborationRoot.children[index] || null);
  });
  taskSidebar.scrollTop = sidebarScroll;
  taskHost.scrollTop = hostScroll;
}

async function refreshRound(version, conversation) {
  try {
    const results = await Promise.all([
      getJson("/api/agents"),
      getJson("/api/conversations"),
      getJson("/api/runs"),
      getJson("/api/collaboration/tasks?conversation_id=" + encodeURIComponent(conversation))
    ]);
    if (version !== refreshVersion || conversation !== selectedConversation) return;
    renderCollaboration(results[3].tasks || [], conversation);
    renderRecipients(results[0].agents, results[0].codex_cli_available, results[0].dsh_cli_available, results[0].grok_cli_available);
    renderConversations(results[1].conversations);
    if (conversation !== selectedConversation) {
      refreshVersion++;
      refreshPending = true;
      resetCollaboration(selectedConversation);
      return;
    }
    const activeRuns = results[2].runs || [];
    stopAllButton.disabled = stopAllSubmitting || activeRuns.length === 0;
    stopAllButton.textContent = activeRuns.length ? "Stop (" + activeRuns.length + ")" : "Stop";
    const data = await getJson("/api/messages?conversation_id=" + encodeURIComponent(conversation) + "&limit=500");
    if (version !== refreshVersion || conversation !== selectedConversation) return;
    renderMessages(data.messages);
    if (version !== refreshVersion || conversation !== selectedConversation) return;
    errorNode.style.display = "none";
    statusNode.textContent = "已更新 · " + new Date().toLocaleTimeString("zh-CN");
  } catch (error) {
    if (version !== refreshVersion || conversation !== selectedConversation) return;
    errorNode.textContent = "无法读取桥接数据库：" + error.message;
    errorNode.style.display = "block";
    statusNode.textContent = "连接失败";
  }
}

function refreshAll(periodic = false) {
  if (periodic && refreshPromise) return refreshPromise;
  if (!periodic) refreshVersion++;
  refreshPending = true;
  if (!refreshPromise) {
    refreshPromise = Promise.resolve().then(async function() {
      try {
        while (refreshPending) {
          refreshPending = false;
          await refreshRound(refreshVersion, selectedConversation);
        }
      } finally { refreshPromise = null; }
    });
  }
  return refreshPromise;
}

async function requestStop(payload, button) {
  if (button.disabled) return;
  button.disabled = true;
  if (button === stopAllButton) stopAllSubmitting = true;
  let failed = false;
  try {
    const response = await fetch("/api/stop", {
      method:"POST",
      headers:{"Content-Type":"application/json"},
      body:JSON.stringify(payload)
    });
    const result = await response.json();
    if (!response.ok) throw new Error(result.error || "HTTP " + response.status);
    statusNode.textContent = "已请求停止 " + result.stopped + " 个 Agent";
  } catch (error) {
    failed = true;
    errorNode.textContent = "停止失败：" + error.message;
    errorNode.style.display = "block";
  } finally {
    if (button === stopAllButton) stopAllSubmitting = false;
    if (!failed) await refreshAll();
  }
}

stopAllButton.addEventListener("click", function() {
  requestStop({all:true}, stopAllButton);
});
messagesNode.addEventListener("click", function(event) {
  const button = event.target.closest("button[data-stop-message-id]");
  if (!button) return;
  requestStop({message_id:Number(button.dataset.stopMessageId)}, button);
});

conversationNode.addEventListener("change", function() {
  selectedConversation = conversationNode.value;
  previousMessageIds = "";
  initialized = false;
  resetCollaboration(selectedConversation);
  refreshAll();
});
composerNode.addEventListener("submit", async function(event) {
  event.preventDefault();
  const content = inputNode.value.trim();
  if (!content || submitting) return;
  const recipients = recipientsForText(content);
  if (recipients.some(function(agentId) {
    return pendingAgents.has(agentId) || !availableAgents[agentId];
  })) return;
  const mentionContext = getMentionContext();
  if (mentionContext) dismissedMentionStart = mentionContext.start;
  hideMentionSuggestions();
  submitting = true;
  updateComposer();
  try {
    const response = await fetch("/api/messages", {
      method:"POST",
      headers:{"Content-Type":"application/json"},
      body:JSON.stringify({conversation_id:selectedConversation, content:content, recipients:recipients})
    });
    const payload = await response.json();
    if (!response.ok) throw new Error(payload.error || "HTTP " + response.status);
    inputNode.value = "";
    previousMessageIds = "";
    initialized = false;
    await refreshAll();
  } catch (error) {
    errorNode.textContent = "发送失败：" + error.message;
    errorNode.style.display = "block";
  } finally {
    submitting = false;
    updateComposer();
  }
});
inputNode.addEventListener("input", function(event) {
  if (event.inputType && event.inputType.startsWith("delete")) {
    const context = getMentionContext();
    if (context) dismissedMentionStart = context.start;
  } else {
    dismissedMentionStart = null;
  }
  updateComposer();
});
inputNode.addEventListener("compositionend", function() {
  dismissedMentionStart = null;
  updateComposer();
});
inputNode.addEventListener("click", updateComposer);
inputNode.addEventListener("keyup", function(event) {
  if (["Enter", "Tab", "Escape"].includes(event.key)) return;
  if (!mentionSuggestionsNode.hidden && ["ArrowUp", "ArrowDown"].includes(event.key)) return;
  updateComposer();
});
mentionSuggestionsNode.addEventListener("mousedown", function(event) {
  event.preventDefault();
});
mentionSuggestionsNode.addEventListener("click", function(event) {
  const button = event.target.closest("button[data-index]");
  if (button) completeMention(Number(button.dataset.index));
});
function dismissMentionSuggestions() {
  const context = getMentionContext();
  if (context) dismissedMentionStart = context.start;
  hideMentionSuggestions();
  updateComposer();
}
inputNode.addEventListener("blur", dismissMentionSuggestions);
document.addEventListener("pointerdown", function(event) {
  if (mentionSuggestionsNode.hidden) return;
  if (!inputNode.contains(event.target) && !mentionSuggestionsNode.contains(event.target)) {
    dismissMentionSuggestions();
  }
});
inputNode.addEventListener("keydown", function(event) {
  if (event.isComposing || event.keyCode === 229) return;
  if (!mentionSuggestionsNode.hidden && visibleMentionOptions.length) {
    if (event.key === "ArrowDown") {
      event.preventDefault();
      activeMentionIndex = (activeMentionIndex + 1) % visibleMentionOptions.length;
      updateMentionSuggestions();
      return;
    }
    if (event.key === "ArrowUp") {
      event.preventDefault();
      activeMentionIndex = (activeMentionIndex - 1 + visibleMentionOptions.length) % visibleMentionOptions.length;
      updateMentionSuggestions();
      return;
    }
    if (event.key === "Tab") {
      event.preventDefault();
      completeMention(activeMentionIndex);
      return;
    }
    if (event.key === "Escape") {
      event.preventDefault();
      dismissMentionSuggestions();
      return;
    }
  }
  if (event.key === "Enter" && !event.shiftKey) {
    event.preventDefault();
    composerNode.requestSubmit();
  }
});
// WORKBENCH_STARTUP
refreshAll();
setInterval(function() { refreshAll(true); }, 3000);
</script>
</body>
</html>"""

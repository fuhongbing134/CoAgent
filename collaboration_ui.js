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

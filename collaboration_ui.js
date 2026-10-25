// Task controls use textContent throughout, including Agent/user supplied text.
const collaborationDrafts = new Map();
const collaborationRoot = document.createElement("section");
collaborationRoot.setAttribute("aria-label", "Agent 协作任务");
messagesNode.before(collaborationRoot);
let collaborationSignature = "";

async function collaborationAction(task, action, button, content = "", roundLimit = null) {
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
  const signature = JSON.stringify([conversation,tasks]);
  if (signature === collaborationSignature) return;
  collaborationSignature = signature;
  collaborationRoot.replaceChildren();
  const labels = {working:"协作中",waiting_user:"等待你回答",blocked:"受阻",quiescing:"正在停止后续工作",
    final_ready:"等待最终汇总",final_delivery:"最终汇总中",completed:"已交付",failed:"失败",interrupted:"已中断",stopped:"已停止"};
  for (const task of tasks) {
    const card = textElement("article", "bubble");
    card.style.margin = "12px";
    const state = task.state;
    card.append(textElement("strong", "", (labels[state.phase] || state.phase) + " · " + state.used + "/" + state.round_limit + " 轮"));
    card.append(textElement("div", "", "负责人：" + agentLabel(task.plan.owner_id) + "；最终答复：" + agentLabel(task.plan.final_responder_id)));
    if (state.reason) card.append(textElement("p", "", state.reason));
    const details = document.createElement("details");
    details.append(textElement("summary", "", "需求与分工"), textElement("pre", "content", task.request + "\n" + task.plan.tasks.map(t => t.agent_id + ": " + t.instruction).join("\n")));
    card.append(details);
    const controls = document.createElement("div");
    function add(label, action, contentProvider) {
      const button = textElement("button", "stop-button", label);
      button.type = "button";
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
      limit.setAttribute("aria-label", "轮次上限");
      controls.append(limit);
      const save = textElement("button", "stop-button", "调整上限");
      save.onclick = () => collaborationAction(task,"set_limit",save,"",Number(limit.value));
      controls.append(save);
    }
    card.append(controls);
    if (["stopped","completed","interrupted"].includes(state.phase)) {
      card.append(textElement("p", "", "已有改动未自动回退；本任务可能涉及文件修改，范围及完整性未经核实。"));
    }
    collaborationRoot.append(card);
  }
}

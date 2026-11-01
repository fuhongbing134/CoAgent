// Run the actual inline page script with a small DOM model and controlled fetches.
const assert = require('node:assert/strict');
const vm = require('node:vm');
const fs = require('node:fs');
const source = fs.readFileSync(0, 'utf8');
class Element {
  constructor(tag='div') {
    this.tag=tag; this.children=[]; this.dataset={}; this.style={};
    this.textContent=''; this.listeners={}; this.className=''; this.open=false;
    this.scrollTop=0; this.value=''; this.selectionStart=0; this.selectionEnd=0;
  }
  append(...nodes) { for (const n of nodes) { n.remove(); n.parent=this; this.children.push(n); } }
  remove() { if (this.parent) { const p=this.parent; p.children.splice(p.children.indexOf(this),1); this.parent=null; } }
  insertBefore(n, before) { n.remove(); n.parent=this; const i=this.children.indexOf(before); this.children.splice(i<0?this.children.length:i,0,n); }
  replaceChildren(...nodes) { for (const n of [...this.children]) n.remove(); this.append(...nodes); }
  querySelector(selector) { return this.descendants().find(n=>selector[0]==='.' ? n.className.split(' ').includes(selector.slice(1)) : n.tag===selector) || null; }
  descendants() { return this.children.flatMap(n=>[n,...n.descendants()]); }
  addEventListener(name, fn) { this.listeners[name]=fn; }
  setAttribute(name,value) { this[name]=value; }
  focus() { this.ownerDocument.activeElement=this; }
  setSelectionRange(start,end) { this.selectionStart=start; this.selectionEnd=end; }
}
function environment(narrow=false, omit=null) {
  const ids=['agents','messages','conversation','error','status','stream','composer',
    'message-input','send-button','stop-all','compose-hint','mention-suggestions',
    'task-panel','task-count','collaboration-tasks','task-sidebar'];
  const nodes=new Map(ids.filter(id=>id!==omit).map(id=>[id,new Element()]));
  if(nodes.has('task-panel')) nodes.get('task-panel').open=true;
  const document={activeElement:null,getElementById:id=>nodes.get(id)||null,
    createElement(tag) { const node=new Element(tag); node.ownerDocument=document; return node; },addEventListener() {}};
  const window={matchMedia:()=>({matches:narrow})};
  const context=vm.createContext({document,window,console,URL,AbortController,setTimeout,clearTimeout,
    setInterval() {},crypto:{randomUUID:()=> 'operation'},alert() {},fetch() { throw Error('Unexpected real fetch'); }});
  const marker='// WORKBENCH_STARTUP';
  assert.equal(source.split(marker).length,2,'one explicit startup boundary required');
  vm.runInContext(source.slice(0,source.indexOf(marker)),context);
  return {context,nodes,run:code=>vm.runInContext(code,context)};
}
async function flush() { for(let i=0;i<12;i++) await Promise.resolve(); }
(async()=>{
  const e=environment();
  assert.equal(e.run("document.getElementById('workspace-path')"),null);
  assert.throws(()=>environment(false,'collaboration-tasks'),/null/);
  for (const [agents,codex,dsh,expected] of [
    [[],false,false,[]],[[],true,true,['codex']],
    [[{agent_id:'deepseek-harness'}],false,true,['deepseek-harness']],
    [[{agent_id:'deepseek-harness'},{agent_id:'unrelated'}],true,false,['codex']],
    [[{agent_id:'deepseek-harness'}],true,true,['codex','deepseek-harness']]
  ]) {
    e.context.agents=agents; e.context.codex=codex; e.context.dsh=dsh;
    e.run('renderRecipients(agents,codex,dsh)');
    assert.deepEqual(Array.from(e.run('Object.keys(availableAgents).filter(id=>availableAgents[id])')),expected);
    assert.equal(e.nodes.get('agents').textContent,expected.length ? expected.map(id=>e.run(`agentLabel(${JSON.stringify(id)})`)).join(' / ') : '\u6682\u65e0\u53ef\u7528 Agent');
  }
  assert.equal(e.run('collaborationRoot.parent===taskHost'),true);
  e.run("renderRecipients([],true,false,true)");
  assert.deepEqual(Array.from(e.run("recipientsForText('@Grok hello')")),['grok']);
  for (const alias of ['@xai','@grok-build']) {
    e.context.alias=alias;
    assert.deepEqual(Array.from(e.run("recipientsForText(alias+' hello')")),['grok']);
  }
  assert.deepEqual(Array.from(e.run("recipientsForText('@all hello')")),['codex','deepseek-harness','grok']);
  assert.equal(e.run("agentLabel('unknown')"),'unknown');
  e.run("pendingAgents=new Set(['grok']); document.getElementById('message-input').value='@Grok hello'; updateComposer()");
  assert.equal(e.nodes.get('send-button').disabled,true);
  e.run("pendingAgents.clear(); renderRecipients([],true,false,false); updateComposer()");
  assert.deepEqual(Array.from(e.run("recipientsForText('@all hello')")),['codex','deepseek-harness']);
  assert.equal(e.nodes.get('send-button').disabled,true);
  e.run("renderRecipients([],true,false,true); updateComposer()");
  assert.equal(e.nodes.get('send-button').disabled,false);
  const n=environment(true);
  assert.equal(n.nodes.get('task-panel').open,false);
  n.nodes.get('task-panel').open=true;
  const task={state:{job_id:'job',phase:'waiting_user',used:2,round_limit:20,questions:'["question"]',reason:''},
    request:'request',plan:{owner_id:'codex',final_responder_id:'codex',tasks:[]}};
  n.context.task=task;
  n.run('renderCollaboration([task],selectedConversation); var card=collaborationRoot.children[0]; var details=card.querySelector("details"); details.open=true; var answer=card.querySelector("textarea"); answer.value="draft"; answer.oninput(); answer.focus(); answer.setSelectionRange(1,3); card.querySelector("input").value="30"; taskSidebar.scrollTop=42; taskHost.scrollTop=17;');
  task.state.used++;
  n.run('renderCollaboration([task],selectedConversation)');
  assert.equal(n.run('collaborationRoot.children[0]===card'),true);
  assert.equal(n.run('card.querySelector("details").open'),true);
  assert.equal(n.run('card.querySelector("textarea").value'),'draft');
  assert.equal(n.run('card.querySelector("input").value'),'30');
  assert.equal(n.run('document.activeElement===card.querySelector("textarea")'),true);
  assert.equal(n.run('document.activeElement.selectionStart'),1);
  assert.equal(n.run('taskSidebar.scrollTop'),42);
  assert.equal(n.run('taskHost.scrollTop'),17);
  assert.equal(n.nodes.get('task-panel').open,true);
  n.run('var pendingButton=card.querySelector("button"); pendingButton.disabled=true; pendingButton.dataset.operationId="pending";');
  task.state.used++;
  n.run('renderCollaboration([task],selectedConversation)');
  assert.equal(n.run('card.querySelector("button")===pendingButton'),true);
  assert.equal(n.run('pendingButton.dataset.operationId'),'pending');
  n.run('pendingButton.disabled=false; renderCollaboration([task],selectedConversation)');
  n.run('var oldButton=card.querySelector("button"); refreshAll=()=>Promise.resolve(); conversationNode.value="B"; conversationNode.listeners.change();');
  assert.equal(n.run('collaborationRoot.children.length'),0);
  assert.equal(n.run('selectedConversation'),'B');
  await n.run('oldButton.onclick()'); // Must not fetch with the new conversation.
  n.run('conversationNode.value="default"; conversationNode.listeners.change();');
  await n.run('oldButton.onclick()'); // A-B-A must not reactivate a detached control.
  n.run('conversationNode.value="B"; conversationNode.listeners.change();');
  n.run('renderCollaboration([task],"default")');
  assert.equal(n.run('collaborationRoot.children.length'),0);
  console.log('PASS available names, missing nodes, sidebar mounting and interaction preservation');

  const q=environment();
  q.run(`var jobs=[]; refreshRound=()=>new Promise(resolve=>jobs.push(resolve));`);
  const first=q.run('refreshAll()'); await flush();
  const periodic=q.run('refreshAll(true)');
  const manual=q.run('refreshAll()'); q.run('refreshAll()');
  assert.equal(q.run('jobs.length'),1);
  assert.equal(first,manual); assert.equal(first,periodic);
  q.run('jobs[0]()'); await flush();
  assert.equal(q.run('jobs.length'),2);
  q.run('jobs[1]()'); await first;
  assert.equal(q.run('refreshPromise'),null);
  console.log('PASS periodic deduplication and awaited manual refresh coalescing');

  const r=environment();
  r.run(`var renders=[]; var releaseMessages; renderAgents=()=>{}; renderRecipients=()=>{}; renderConversations=()=>{}; renderMessages=messages=>renders.push(messages);
    getJson=async path=>path.includes('/api/messages') ? new Promise(resolve=>releaseMessages=resolve) : {agents:[],conversations:[],runs:[]};`);
  const stale=r.run('refreshRound(refreshVersion, selectedConversation)'); await flush();
  r.run(`selectedConversation='B'; refreshVersion++; selectedConversation='default'; refreshVersion++; releaseMessages({messages:['stale']});`);
  await stale;
  assert.equal(r.run('renders.length'),0);
  console.log('PASS stale messages discarded for A-B-A switch');

  const t=environment();
  t.run(`var taskRenders=[]; var releaseTasks;
    renderCollaboration=items=>taskRenders.push(items); renderMessages=()=>{};
    getJson=async path=>path.includes('/api/collaboration/tasks') ? new Promise(resolve=>releaseTasks=resolve) : {agents:[],conversations:[],runs:[]};`);
  const staleTasks=t.run('refreshRound(refreshVersion,selectedConversation)'); await flush();
  t.run('selectedConversation="B"; refreshVersion++; selectedConversation="default"; refreshVersion++; releaseTasks({tasks:["stale"]});');
  await staleTasks;
  assert.equal(t.run('taskRenders.length'),0);
  const f=environment();
  f.run(`var paths=[]; renderMessages=()=>{};
    getJson=async path=>{paths.push(path);return {agents:[],conversations:[],runs:[],tasks:[],messages:[]};};`);
  await f.run('refreshAll()');
  assert.equal(f.run('paths.some(path=>path.includes("capabilities")||path.includes("settings"))'),false);
  assert.equal(f.run('paths.includes("/api/agents")'),true);
  assert.equal(f.nodes.get('error').style.display,'none');
  console.log('PASS stale tasks discarded and removed UI endpoints not polled');
})().catch(error=>{console.error(error);process.exitCode=1;});

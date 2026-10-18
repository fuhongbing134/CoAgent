// Run the actual inline page script with a small DOM model and controlled fetches.
const assert = require('node:assert/strict');
const vm = require('node:vm');
const fs = require('node:fs');
const source = fs.readFileSync(0, 'utf8');
class Element {
  constructor(tag='div') { this.tag=tag; this.children=[]; this.dataset={}; this.style={}; this.textContent=''; this.listeners={}; this.className=''; }
  append(...nodes) { for (const n of nodes) { n.remove(); n.parent=this; this.children.push(n); } }
  remove() { if (this.parent) { const p=this.parent; p.children.splice(p.children.indexOf(this),1); this.parent=null; } }
  insertBefore(n, before) { n.remove(); n.parent=this; const i=this.children.indexOf(before); this.children.splice(i<0?this.children.length:i,0,n); }
  replaceChildren(...nodes) { for (const n of [...this.children]) n.remove(); this.append(...nodes); this.replacements=(this.replacements||0)+1; }
  querySelector(selector) { return this.children.flatMap(n=>[n,...n.descendants()]).find(n=>selector[0]==='.' ? n.className.split(' ').includes(selector.slice(1)) : 'capabilityAgent' in n.dataset) || null; }
  descendants() { return this.children.flatMap(n=>[n,...n.descendants()]); }
  addEventListener(name, fn) { this.listeners[name]=fn; }
  setAttribute() {}
}
function environment() {
  const nodes=new Map();
  const document={getElementById(id) { if(!nodes.has(id)) nodes.set(id,new Element()); return nodes.get(id); }, createElement:tag=>new Element(tag), addEventListener() {}};
  const context=vm.createContext({document, console, URL, AbortController, setTimeout, clearTimeout, setInterval() {}, fetch() { throw Error('Unexpected real fetch'); }});
  vm.runInContext(source.slice(0,source.lastIndexOf('loadWorkspaceSettings();')),context);
  return {context,nodes,run:code=>vm.runInContext(code,context)};
}
async function flush() { for(let i=0;i<12;i++) await Promise.resolve(); }
(async()=>{
  const e=environment();
  e.run(`var sample=[{agent_id:'codex',name:'Codex',model:'m1',vendor:'OpenAI',role:'r',last_seen:''}]; renderAgents(sample); var card=agentCards.get('codex'); var slot=card.querySelector('[data-capability-agent]'); slot.dataset.saved='yes'; renderAgents(sample);`);
  assert.equal(e.run(`agentCards.get('codex')===card`),true);
  assert.equal(e.run(`card.querySelector('[data-capability-agent]').dataset.saved`),'yes');
  e.run(`sample[0].model='m2'; renderAgents(sample);`);
  assert.equal(e.run(`card.querySelector('.agent-model').textContent`),'m2');
  e.context.payload={probe:{state:'not_tested',results:[],working_directory:'C:/project'},agent_configuration:[{id:'codex',configuration:'workspace-write',cli_found:true}]};
  e.run(`getJson=async()=>payload;`);
  await e.run('refreshCapabilities()');
  const replacements=e.run('slot.replacements');
  await e.run('refreshCapabilities()');
  assert.equal(e.run('slot.replacements'),replacements);
  e.run('renderAgents([])');
  assert.equal(e.run('agentCards.size'),0);
  console.log('PASS card reuse, changed fields, removal, capability signature');

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
  r.run(`var renders=[]; var releaseMessages; renderAgents=()=>{}; renderRecipients=()=>{}; renderConversations=()=>{}; renderMessages=messages=>renders.push(messages); refreshCapabilities=async()=>{};
    getJson=async path=>path.includes('/api/messages') ? new Promise(resolve=>releaseMessages=resolve) : {agents:[],conversations:[],runs:[]};`);
  const stale=r.run('refreshRound(refreshVersion, selectedConversation)'); await flush();
  r.run(`selectedConversation='B'; refreshVersion++; selectedConversation='default'; refreshVersion++; releaseMessages({messages:['stale']});`);
  await stale;
  assert.equal(r.run('renders.length'),0);
  console.log('PASS stale messages discarded for A-B-A switch');

  const f=environment();
  f.run(`renderAgents=()=>{}; renderRecipients=()=>{}; renderConversations=()=>{}; renderMessages=()=>{};
    getJson=async()=>({agents:[],conversations:[],runs:[],messages:[]});
    refreshCapabilities=async()=>{throw Error('capability failed')};
    document.getElementById('capability-results').textContent='retained';`);
  await f.run('refreshAll()');
  assert.equal(f.nodes.get('capability-results').textContent,'retained');
  assert.equal(f.nodes.get('capability-error').textContent,'capability failed');
  assert.equal(f.nodes.get('error').style.display,'none');
  console.log('PASS capability errors preserve results and do not fail entire page');
})().catch(error=>{console.error(error);process.exitCode=1;});

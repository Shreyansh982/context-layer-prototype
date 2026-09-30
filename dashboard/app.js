const form = document.querySelector('#query-form');
const queryInput = document.querySelector('#query');
const demoKeyInput = document.querySelector('#demo-key');
const chat = document.querySelector('#chat');
const emptyState = document.querySelector('#empty-state');
const error = document.querySelector('#form-error');
const sendButton = document.querySelector('#send');
const connectButton = document.querySelector('#connect');
const changeUserButton = document.querySelector('#change-user');
const connectedUser = document.querySelector('#connected-user');
const agentId = 'enterprise_dashboard';
let connectedUserId = null;
let demoApiKey = null;

function setComposerEnabled(enabled) {
  queryInput.disabled = !enabled;
  sendButton.disabled = !enabled;
  queryInput.placeholder = enabled ? 'Ask about enterprise knowledge…' : 'Connect with the demo key to begin';
}

async function connectUser() {
  const key = demoKeyInput.value.trim();
  error.textContent = '';
  if (!key) { error.textContent = 'Enter the local demo API key.'; demoKeyInput.focus(); return; }
  try {
    const response = await fetch('/demo/session', { headers: { 'X-Demo-API-Key': key } });
    const session = await response.json();
    if (!response.ok) throw new Error(session.detail || 'Invalid demo API key.');
    demoApiKey = key; connectedUserId = session.user_id; demoKeyInput.disabled = true; connectButton.hidden = true;
    connectedUser.textContent = `Connected as ${connectedUserId}`; connectedUser.hidden = false; changeUserButton.hidden = false;
    setComposerEnabled(true); queryInput.focus();
  } catch (exception) { error.textContent = exception.message || 'Unable to validate the demo key.'; }
}

function changeUser() {
  connectedUserId = null; demoApiKey = null; demoKeyInput.disabled = false; demoKeyInput.value = '';
  connectButton.hidden = false; connectedUser.hidden = true; changeUserButton.hidden = true; setComposerEnabled(false); demoKeyInput.focus();
}

function addMessage(role, text, context = []) {
  emptyState.hidden = true;
  const message = document.createElement('article'); message.className = `message ${role}`;
  message.innerHTML = `<span class="message-label">${role === 'user' ? 'You' : 'Context assistant'}</span><div class="bubble"></div>`;
  message.querySelector('.bubble').textContent = text;
  if (context.length) { const tags = document.createElement('div'); tags.className = 'context-tags'; tags.innerHTML = context.map(item => `<span>${item}</span>`).join(''); message.append(tags); }
  chat.append(message); message.scrollIntoView({ behavior: 'smooth', block: 'end' }); return message;
}

async function ask(query) {
  error.textContent = '';
  if (!connectedUserId || !demoApiKey) { error.textContent = 'Connect with the demo key before sending a query.'; return; }
  if (!query.trim()) return;
  addMessage('user', query.trim()); queryInput.value = ''; sendButton.disabled = true;
  const thinking = addMessage('assistant thinking', 'Finding the most relevant context…');
  try {
    const response = await fetch('/query', { method: 'POST', headers: { 'Content-Type': 'application/json', 'X-Demo-API-Key': demoApiKey }, body: JSON.stringify({ user_id: connectedUserId, agent_id: agentId, query: query.trim() }) });
    const payload = await response.json(); thinking.remove();
    if (!response.ok) throw new Error(payload.detail?.[0]?.msg || payload.detail || 'The request could not be completed.');
    addMessage('assistant', payload.answer, payload.context_used || []);
  } catch (exception) { thinking.remove(); error.textContent = exception.message || 'Unable to reach the context layer.'; }
  finally { sendButton.disabled = false; queryInput.focus(); }
}

form.addEventListener('submit', event => { event.preventDefault(); ask(queryInput.value); });
connectButton.addEventListener('click', connectUser); changeUserButton.addEventListener('click', changeUser);
demoKeyInput.addEventListener('keydown', event => { if (event.key === 'Enter') connectUser(); });
document.querySelectorAll('[data-query]').forEach(button => button.addEventListener('click', () => ask(button.dataset.query)));

async function checkHealth() {
  const dot = document.querySelector('#health-dot'); const label = document.querySelector('#health-label'); const list = document.querySelector('#service-list');
  try {
    const health = await (await fetch('/ready')).json(); const services = ['neo4j', 'chromadb', 'LLM', 'redis']; const healthy = health.status === 'healthy';
    dot.className = `health-dot ${healthy ? 'ok' : 'bad'}`; label.textContent = healthy ? 'All core services online' : 'Service attention needed';
    list.textContent = services.map(name => `${health[name] ? '●' : '○'} ${name}`).join('  ');
  } catch { dot.className = 'health-dot bad'; label.textContent = 'Context layer unavailable'; list.textContent = 'Start the Docker demo stack to connect.'; }
}
checkHealth();

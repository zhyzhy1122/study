/* ===================================================================
   Atlas · AI 学习工作台 — 前端逻辑
   功能：SSE 流式聊天、思考过程展示、多模态上传、会话管理、路线导出
   =================================================================== */

/* ---------- 初始化 ---------- */
const state = {
  isStreaming: false,
  pendingImage: null,        // { file, dataUrl, path }
  currentSessionId: '',      // 当前会话 ID
  mermaidCounter: 0,
  thinkingOpen: false,
};

const $ = (sel) => document.querySelector(sel);
const chatContainer = $('#chat-container');
const welcome = $('#welcome');
const chat = $('#chat');
const userInput = $('#user-input');
const sendBtn = $('#send-btn');
const attachBtn = $('#attach-btn');
const fileInput = $('#file-input');
const convList = $('#conv-list');
const btnNew = $('#btn-new');
const statusLed = $('#status-led');
const statusText = $('#status-text');
const sessionMeta = $('#session-meta');
const imagePreviewRow = $('#img-preview-row');
const thumbImg = $('#thumb-img');
const imageName = $('#image-name');
const imgRemove = $('#img-remove');
const dragOverlay = $('#drag-overlay');

/* ---------- Markdown / Mermaid 初始化 ---------- */
mermaid.initialize({
  startOnLoad: false,
  theme: 'neutral',
  securityLevel: 'loose',
  fontFamily: 'inherit',
});

marked.setOptions({ breaks: true, gfm: true });

const renderer = new marked.Renderer();
renderer.code = function (code, language) {
  let text = (typeof code === 'object' && code !== null) ? (code.text || '') : String(code);
  let lang = (typeof language === 'string') ? language : (code && code.lang) || '';
  if (lang && lang.toLowerCase() === 'mermaid') {
    state.mermaidCounter++;
    const id = 'mermaid-' + state.mermaidCounter;
    return `<div class="mermaid-block" id="${id}">${text}</div>`;
  }
  let highlighted = '';
  if (lang && window.hljs && hljs.getLanguage(lang)) {
    try { highlighted = hljs.highlight(text, { language: lang }).value; } catch (e) { highlighted = text; }
  } else if (window.hljs) {
    try { highlighted = hljs.highlightAuto(text).value; } catch (e) { highlighted = text; }
  } else {
    highlighted = text;
  }
  const langLabel = lang ? lang.toLowerCase() : 'code';
  const escaped = text.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
  return `<div class="code-block">
    <div class="code-header"><span class="lang-label">${langLabel}</span>
    <button class="copy-btn" onclick="copyCode(this, '${escaped.replace(/'/g, "\\'")}')">复制</button></div>
    <pre><code class="hljs language-${langLabel}">${highlighted}</code></pre>
  </div>`;
};
marked.setOptions({ renderer });

/* ---------- 工具函数 ---------- */
function escapeHtml(s) {
  return String(s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
}
function truncate(s, n) { s = String(s); return s.length > n ? s.slice(0, n) + '…' : s; }
function formatToolName(name) {
  const n = String(name);
  return n.replace(/_/g, ' ').replace(/([a-z])([A-Z])/g, '$1 $2');
}
function scrollToBottom() {
  requestAnimationFrame(() => { chatContainer.scrollTop = chatContainer.scrollHeight; });
}

window.copyCode = function (btn, code) {
  navigator.clipboard.writeText(code).then(() => {
    btn.textContent = '已复制';
    setTimeout(() => { btn.textContent = '复制'; }, 1400);
  }).catch(() => {});
};

/* ---------- 消息 DOM ---------- */
function appendMessage(html, role) {
  const msg = document.createElement('div');
  msg.className = 'message ' + role;
  msg.innerHTML = `<div class="avatar">${role === 'ai' ? 'AI' : '我'}</div>
    <div class="msg-body"><div class="bubble"></div></div>`;
  const bubble = msg.querySelector('.bubble');
  bubble.innerHTML = html;
  chatContainer.appendChild(msg);
  scrollToBottom();
  return { msg, bubble };
}

/* ---------- 思考面板 ---------- */
function addThinkingPanel(msgEl) {
  const panel = document.createElement('div');
  panel.className = 'thinking-panel';
  panel.innerHTML = `<div class="thinking-header">
      <span class="arrow open">▸</span>
      <span class="spinner"></span>
      <span class="phase-text">正在分析...</span>
    </div>
    <div class="thinking-body open"><div class="thinking-body-inner"></div></div>`;
  const body = msgEl.querySelector('.msg-body');
  body.insertBefore(panel, body.firstChild);
  return panel;
}

/* ---------- 渲染最终回答（markdown + mermaid + 导出按钮） ---------- */
function renderFinal(bubble, text) {
  state.mermaidCounter = 0;
  bubble.dataset.rawMarkdown = text;
  const html = marked.parse(text);
  bubble.innerHTML = `<div class="md-content">${html}</div>`;
  bubble.dataset.rawMarkdown = text;

  // 渲染 mermaid 图
  bubble.querySelectorAll('.mermaid-block').forEach((block) => {
    const id = block.id || 'mmd-' + Math.random().toString(36).slice(2, 8);
    try {
      mermaid.render(id + '-svg', block.textContent).then(({ svg }) => {
        block.innerHTML = svg;
      }).catch((e) => {
        block.innerHTML = `<pre style="color:var(--red);font-size:12px">图表渲染失败: ${escapeHtml(e.message)}</pre>`;
      });
    } catch (e) {
      block.innerHTML = `<pre style="color:var(--red);font-size:12px">图表渲染失败: ${escapeHtml(e.message)}</pre>`;
    }
  });

  // 学习路线 → 加导出按钮
  if (/##\s|###\s|学习路线/.test(text)) {
    const bar = document.createElement('div');
    bar.className = 'export-bar';
    bar.innerHTML = `<button class="export-btn" onclick="exportRoute('md', this)">⬇ 导出 Markdown</button>
      <button class="export-btn" onclick="exportRoute('docx', this)">⬇ 导出 Word</button>`;
    bubble.appendChild(bar);
  }
}

/* ---------- 导出学习路线 ---------- */
window.exportRoute = async function (fmt, btn) {
  const bubble = btn.closest('.bubble');
  const content = bubble.dataset.rawMarkdown || '';
  if (!content) return;
  btn.textContent = '导出中...';
  btn.disabled = true;
  try {
    // 调用后端导出：后端会根据当前工作空间路径保存文件
    const resp = await fetch('/api/export', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ content, filename: '学习路线', format: fmt }),
    });
    const data = await resp.json();

    if (data.error) {
      // 后端报错 → 显示错误提示条
      showExportToast(false, data.error);
      btn.textContent = '导出失败';
    } else {
      // 成功 → 显示实际保存路径的提示条
      showExportToast(true, data.path || '', fmt.toUpperCase());
      btn.textContent = '✅ 已保存';
    }
  } catch (e) {
    showExportToast(false, '网络异常：' + e.message);
    btn.textContent = '导出失败';
    console.warn('Export error:', e);
  }
  setTimeout(() => { btn.textContent = fmt === 'md' ? '⬇ 导出 Markdown' : '⬇ 导出 Word'; btn.disabled = false; }, 3000);
};

// 在消息区底部插入导出结果提示条（复用已有的 export-toast 样式）
function showExportToast(ok, path, fmtLabel) {
  const toast = document.createElement('div');
  toast.className = 'export-toast ' + (ok ? 'ok' : 'fail');
  if (ok) {
    toast.textContent = '✅ 已导出 ' + (fmtLabel || '') + ' 到工作空间：' + path;
  } else {
    toast.textContent = '导出失败：' + path;
  }
  // 插入到聊天区底部
  const chatInner = document.getElementById('chat-container');
  if (chatInner) {
    chatInner.appendChild(toast);
    chatInner.scrollTop = chatInner.scrollHeight;
  }
  // 8 秒后自动淡出移除
  setTimeout(() => { toast.style.opacity = '0'; setTimeout(() => toast.remove(), 500); }, 8000);
}

/* ---------- 发送消息 ---------- */
async function sendMessage() {
  const text = userInput.value.trim();
  if ((!text && !state.pendingImage) || state.isStreaming) return;

  welcome.hidden = true;
  chat.hidden = false;

  // 用户消息显示
  let display = '';
  if (state.pendingImage && state.pendingImage.dataUrl) {
    display += `<img src="${state.pendingImage.dataUrl}" style="max-width:200px;border-radius:9px;display:block;margin-bottom:8px" />`;
  }
  const sendText = text || (state.pendingImage ? '请分析这张图片' : '');
  if (sendText && !state.pendingImage) display += escapeHtml(sendText).replace(/\n/g, '<br>');
  appendMessage(display, 'user');

  // 清空输入
  userInput.value = '';
  userInput.style.height = 'auto';
  const imagePath = state.pendingImage ? state.pendingImage.path : '';
  clearImage();

  // AI 消息骨架
  const { msg: aiMsg, bubble } = appendMessage('<div class="typing-dots"><span></span><span></span><span></span></div>', 'ai');
  let thinkingPanel = null;
  let fullText = '';
  let toolSteps = [];
  state.isStreaming = true;
  sendBtn.disabled = true;
  statusLed.classList.add('busy');
  statusText.textContent = 'Atlas 思考中...';

  try {
    const resp = await fetch('/api/chat/stream', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        message: sendText,
        image_path: imagePath,
        session_id: state.currentSessionId,
      }),
    });
    if (!resp.ok) throw new Error('HTTP ' + resp.status);

    const reader = resp.body.getReader();
    const decoder = new TextDecoder('utf-8');
    let buffer = '';

    while (true) {
      const { value, done } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      const events = buffer.split('\n\n');
      buffer = events.pop();
      for (const evt of events) {
        if (!evt.trim()) continue;
        for (const line of evt.split('\n')) {
          if (!line.startsWith('data: ')) continue;
          let data;
          try { data = JSON.parse(line.slice(6)); } catch (e) { continue; }
          handleEvent(data, bubble, aiMsg, () => {
            if (!thinkingPanel) thinkingPanel = addThinkingPanel(aiMsg);
            return thinkingPanel;
          }, toolSteps);
          if (data.type === 'token') {
            fullText += data.content;
            // 实时渲染
            try {
              const html = marked.parse(fullText);
              bubble.innerHTML = `<div class="md-content">${html}</div>`;
              scrollToBottom();
            } catch (e) { /* 忽略流式渲染中间错误，最终渲染兜底 */ }
          }
          if (data.type === 'done' && data.session_id) {
            state.currentSessionId = data.session_id;
            sessionMeta.textContent = '当前会话：' + (data.session_id || '').slice(0, 8);
          }
        }
      }
    }

    // 最终渲染
    if (fullText) {
      renderFinal(bubble, fullText);
    } else {
      bubble.innerHTML = '<span style="color:var(--text-2)">（未生成回答）</span>';
    }
    if (thinkingPanel) {
      const sp = thinkingPanel.querySelector('.spinner');
      if (sp) sp.classList.add('hidden');
      const ph = thinkingPanel.querySelector('.phase-text');
      if (ph) ph.textContent = '完成';
    }

  } catch (err) {
    bubble.innerHTML = `<span style="color:var(--red)">请求失败：${escapeHtml(err.message)}</span>`;
    bubble.classList.add('error-bubble');
  } finally {
    state.isStreaming = false;
    sendBtn.disabled = false;
    statusLed.classList.remove('busy');
    statusText.textContent = 'Atlas 在线';
    loadConversations();
  }
}

/* ---------- SSE 事件处理 ---------- */
function handleEvent(data, bubble, msgEl, ensurePanel, toolSteps) {
  switch (data.type) {
    case 'thinking': {
      const panel = ensurePanel();
      const ph = panel.querySelector('.phase-text');
      if (ph) ph.textContent = data.content || '思考中...';
      // 保留三点滚动动画作为占位，旁边附加简短思考提示（替换为文字，而不是替换掉动画）
      const dots = bubble.querySelector('.typing-dots');
      if (dots) {
        let hint = dots.querySelector('.typing-hint');
        if (!hint) {
          hint = document.createElement('span');
          hint.className = 'typing-hint';
          dots.appendChild(hint);
        }
        hint.textContent = data.content || '思考中...';
      }
      break;
    }
    case 'tool_start': {
      const panel = ensurePanel();
      const body = panel.querySelector('.thinking-body-inner');
      const step = { tool: data.tool, content: data.content, status: 'running' };
      toolSteps.push(step);
      const el = document.createElement('div');
      el.className = 'tool-step';
      el.dataset.idx = toolSteps.length - 1;
      el.innerHTML = `<span class="step-icon">⚡</span>
        <div class="step-text"><span class="step-tool">${escapeHtml(formatToolName(data.tool))}</span>
        <div style="color:var(--text-2);font-size:11px;margin-top:2px">${escapeHtml(truncate(data.content, 80))}</div></div>
        <span class="step-status running">运行中</span>`;
      body.appendChild(el);
      scrollToBottom();
      break;
    }
    case 'tool_end': {
      const el = panelSafeElement(ensurePanel, data);
      // 更新对应 step 状态
      const runningSteps = Array.from(panelSafeBody(ensurePanel)?.querySelectorAll('.tool-step') || []);
      const lastRunning = runningSteps[runningSteps.length - 1];
      if (lastRunning) {
        const s = lastRunning.querySelector('.step-status');
        if (s) { s.className = 'step-status done'; s.textContent = '完成'; }
        const ic = lastRunning.querySelector('.step-icon');
        if (ic) ic.textContent = '✅';
      }
      break;
    }
    case 'export': {
      // 后端检测到用户要求导出并完成 → 在消息区底部插入结果提示
      const ok = !!data.ok;
      const toast = document.createElement('div');
      toast.className = 'export-toast ' + (ok ? 'ok' : 'fail');
      const fmt = (data.format || 'md').toUpperCase();
      if (ok) {
        toast.textContent = '✅ 已导出 ' + fmt + ' 文件：' + (data.path || '');
      } else {
        toast.textContent = '导出失败：' + (data.error || '未知原因');
      }
      chatContainer.appendChild(toast);
      break;
    }
    case 'done':
      break;
    case 'error':
      bubble.innerHTML = `<span style="color:var(--red)">${escapeHtml(data.message || '未知错误')}</span>`;
      bubble.classList.add('error-bubble');
      break;
  }
  scrollToBottom();
}
function panelSafeElement(fn) { try { return fn(); } catch (e) { return null; } }
function panelSafeBody(fn) { try { const p = fn(); return p ? p.querySelector('.thinking-body-inner') : null; } catch (e) { return null; } }

/* ---------- 上传图片 ---------- */
attachBtn.addEventListener('click', () => fileInput.click());
fileInput.addEventListener('change', (e) => {
  const file = e.target.files[0];
  if (file) handleImage(file);
  fileInput.value = '';
});
imgRemove.addEventListener('click', clearImage);

function handleImage(file) {
  if (!file.type.startsWith('image/')) return;
  const reader = new FileReader();
  reader.onload = (e) => {
    state.pendingImage = { file, dataUrl: e.target.result, path: '' };
    thumbImg.src = e.target.result;
    imageName.textContent = truncate(file.name, 30);
    imagePreviewRow.hidden = false;
    uploadImage(file);
  };
  reader.readAsDataURL(file);
}
function clearImage() {
  state.pendingImage = null;
  imagePreviewRow.hidden = true;
  thumbImg.src = '';
  imageName.textContent = '';
}
async function uploadImage(file) {
  const fd = new FormData();
  fd.append('file', file);
  try {
    const resp = await fetch('/api/upload/image', { method: 'POST', body: fd });
    const data = await resp.json();
    if (state.pendingImage && data.path) state.pendingImage.path = data.path;
  } catch (e) { console.warn('上传说失败:', e); }
}

/* ---------- 拖拽上传 ---------- */
let dragDepth = 0;
window.addEventListener('dragover', (e) => { e.preventDefault(); dragDepth++; dragOverlay.hidden = false; });
window.addEventListener('dragleave', () => { if (--dragDepth <= 0) { dragDepth = 0; dragOverlay.hidden = true; } });
window.addEventListener('drop', (e) => {
  e.preventDefault(); dragDepth = 0; dragOverlay.hidden = true;
  const file = e.dataTransfer.files && e.dataTransfer.files[0];
  if (file) handleImage(file);
});

/* ---------- 会话管理 ---------- */
async function loadConversations() {
  try {
    const resp = await fetch('/api/conversations');
    if (!resp.ok) return;
    const convs = await resp.json();
    if (!convs.length) {
      convList.innerHTML = '<div class="conv-empty">暂无对话</div>';
      return;
    }
    convList.innerHTML = '';
    convs.forEach((c) => {
      const item = document.createElement('div');
      item.className = 'conv-item' + (state.currentSessionId === c.session_id ? ' active' : '');
      const title = c.first_message || c.title || '新对话';
      item.innerHTML = `<span class="conv-title">${escapeHtml(truncate(title, 20))}</span>
        <button class="conv-delete" title="删除" data-sid="${c.session_id}">✕</button>`;
      item.addEventListener('click', (e) => {
        if (e.target.classList.contains('conv-delete')) return;
        switchConversation(c.session_id);
      });
      const del = item.querySelector('.conv-delete');
      del.addEventListener('click', async (e) => {
        e.stopPropagation();
        await fetch('/api/conversations/' + c.session_id, { method: 'DELETE' });
        if (state.currentSessionId === c.session_id) {
          state.currentSessionId = '';
          resetChat();
        }
        loadConversations();
      });
      convList.appendChild(item);
    });
  } catch (e) { console.warn('加载会话失败:', e); }
}

async function switchConversation(sid) {
  if (state.isStreaming) return;
  try {
    const resp = await fetch('/api/conversations/' + sid);
    if (!resp.ok) return;
    const data = await resp.json();
    state.currentSessionId = sid;
    sessionMeta.textContent = '当前会话：' + sid.slice(0, 8);
    welcome.hidden = true;
    chat.hidden = false;
    chatContainer.innerHTML = '';
    for (const m of data.messages) {
      if (m.role === 'user') {
        appendMessage(escapeHtml(m.content).replace(/\n/g, '<br>'), 'user');
      } else if (m.role === 'ai') {
        const { bubble } = appendMessage('', 'ai');
        renderFinal(bubble, m.content);
      }
    }
    scrollToBottom();
    loadConversations();
  } catch (e) { console.warn('切换会话失败:', e); }
}

function resetChat() {
  chatContainer.innerHTML = '';
  welcome.hidden = false;
  chat.hidden = true;
  sessionMeta.textContent = '当前：新对话';
  state.currentSessionId = '';
}

btnNew.addEventListener('click', () => { if (!state.isStreaming) { resetChat(); loadConversations(); } });

/* ---------- 输入区交互 ---------- */
function autoResize() {
  userInput.style.height = 'auto';
  userInput.style.height = Math.min(userInput.scrollHeight, 160) + 'px';
}
userInput.addEventListener('input', autoResize);
userInput.addEventListener('keydown', (e) => {
  if (e.key === 'Enter' && !e.shiftKey) {
    e.preventDefault();
    sendMessage();
  }
});
sendBtn.addEventListener('click', sendMessage);

// 提示词快捷键
document.querySelectorAll('.chip').forEach((chip) => {
  chip.addEventListener('click', () => {
    userInput.value = chip.dataset.p;
    welcome.hidden = true;
    chat.hidden = false;
    autoResize();
    userInput.focus();
  });
});

/* ---------- 工作空间（导出目标文件夹） ---------- */
const wsBtn = $('#ws-btn');
const wsModal = $('#ws-modal');
const wsPreset = $('#ws-preset');
const wsCustom = $('#ws-custom');
const wsBrowse = $('#ws-browse');
const wsCurrentPath = $('#ws-current-path');
const wsCancel = $('#ws-cancel');
const wsSave = $('#ws-save');

// 打开弹窗：向 /api/workspace 拉取当前工作空间 + 预置目录，填充下拉并回显
async function openWorkspaceModal() {
  wsModal.hidden = false;
  try {
    const resp = await fetch('/api/workspace');
    const data = await resp.json();
    const presets = data.presets || [];
    // 重建预置下拉选项（escapeHtml 防止路径含 <>" 之类破坏 HTML）
    wsPreset.innerHTML =
      '<option value="">-- 请在下方选择或输入 --</option>' +
      presets
        .map((p) => `<option value="${escapeHtml(p.path)}">${escapeHtml(p.label)}</option>`)
        .join('');
    wsCurrentPath.textContent = data.current || '';
  } catch (e) {
    wsCurrentPath.textContent = '加载失败，请检查后端';
  }
}
function closeWorkspaceModal() { wsModal.hidden = true; }

// 选中预置下拉项 → 自动填入自定义输入框（两者联动，保存时两者取其一）
wsPreset.addEventListener('change', () => { if (wsPreset.value) wsCustom.value = wsPreset.value; });

// “选择更多选项”：通知后端弹系统文件夹选择框，Backend 直接弹出资源管理器
wsBrowse.addEventListener('click', async () => {
  wsBrowse.disabled = true;
  wsBrowse.textContent = '正在打开文件夹选择器…';
  try {
    const resp = await fetch('/api/workspace/choose', { method: 'POST' });
    const data = await resp.json();
    if (!data.cancelled && data.path) wsCustom.value = data.path;
  } catch (e) { /* 后端对话框失败：静默，让用户手动输入 */ }
  wsBrowse.textContent = '📂 选择更多选项（打开资源管理器）';
  wsBrowse.disabled = false;
});

// 保存：PUT /api/workspace 写入所选目录；成功则关闭弹窗
wsSave.addEventListener('click', async () => {
  wsSave.disabled = true;
  const path = (wsPreset.value || wsCustom.value || '').trim();
  if (!path) {
    wsCurrentPath.textContent = '请先选择一个目录';
    wsSave.disabled = false;
    return;
  }
  const resp = await fetch('/api/workspace', {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ path }),
  });
  const data = await resp.json();
  if (data.error) {
    wsCurrentPath.textContent = data.error;
  } else {
    wsCurrentPath.textContent = data.current;
    closeWorkspaceModal();
  }
  wsSave.disabled = false;
});

// 取消按钮、点击遮罩空白处关闭；输入框按钮打开
wsCancel.addEventListener('click', closeWorkspaceModal);
wsModal.addEventListener('click', (e) => { if (e.target === wsModal) closeWorkspaceModal(); });
wsBtn.addEventListener('click', openWorkspaceModal);

/* ---------- 启动 ---------- */
(async function init() {
  loadConversations();
})();
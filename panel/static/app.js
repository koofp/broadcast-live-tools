/* bilive 面板公共 JS：fetch 封装 / 轮询管理(页面隐藏暂停) / 通用动作 */
window.App = (() => {
  const q = (sel) => document.querySelector(sel);
  const esc = (s) => String(s ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const pageInit = {};   // 每页注册器容器
  let refreshCb = null;

  async function fetchJSON(url, opts) {
    try {
      const r = await fetch(url, opts);
      return await r.json();
    } catch (e) {
      console.warn('fetch fail', url, e);
      return null;
    }
  }
  async function postJSON(url, body) {
    try {
      const r = await fetch(url, { method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify(body || {}) });
      return await r.json();
    } catch (e) { console.warn(e); return null; }
  }
  function setCard(id, text) { const el = q('#' + id); if (el) el.textContent = text; }
  function badge(id, isBad) { const el = q('#' + id); if (el) el.className = 'v ' + (isBad ? 'bad' : ''); }
  function toast(msg) {
    let t = q('#toast');
    if (!t) { t = document.createElement('div'); t.id = 'toast';
      t.style.cssText = 'position:fixed;bottom:26px;left:50%;transform:translateX(-50%);background:#1d212b;border:1px solid var(--line2);color:var(--fg);padding:9px 18px;border-radius:10px;font-size:13px;z-index:99;box-shadow:var(--shadow)';
      document.body.appendChild(t); }
    t.textContent = msg; t.style.opacity = 1;
    clearTimeout(t._h); t._h = setTimeout(() => t.style.opacity = 0, 2200);
  }
  // 页面隐藏时暂停轮询（评审采纳）
  function onRefresh(cb) {
    refreshCb = cb;
    document.addEventListener('visibilitychange', () => {
      if (!document.hidden && refreshCb) refreshCb();
    });
  }
  async function viewSum(room, name) {
    const d = await fetchJSON(`/api/summary?room=${encodeURIComponent(room)}&name=${encodeURIComponent(name)}`);
    const v = q('#dlg-body') || q('#sumview');
    v.innerHTML = (d && d.html ? d.html : '(无内容)');   // 用服务端 render_md 转义后的 HTML，防 AI 输出注入
    if (q('#dlg')) { q('#dlg-title').textContent = name; q('#dlg').showModal(); }
  }
  async function viewSrt(room, name) {
    const d = await fetchJSON(`/srt-api/${encodeURIComponent(room)}/${encodeURIComponent(name)}`);
    const v = q('#dlg-body'); v.textContent = (d && d.content) || '(无字幕)';
    if (q('#dlg')) { q('#dlg-title').textContent = name + ' · 字幕预览'; q('#dlg').showModal(); }
  }
  async function procOne(name) {
    if (!confirm('处理 ' + name + ' ?')) return;
    log('提交处理: ' + name);
    const r = await postJSON('/api/process', {name});
    if (!r) return;
    if (r.started === false) { log('拒绝(' + r.status + '): ' + (r.reason || ('锁被持有 ' + Math.round((r.age_sec||0)/60) + ' 分钟'))); toast('未启动：锁被持有'); }
    else { log('已启动(后台执行)'); toast('已启动'); }
  }
  function log(s){const l=q('#log');if(l){l.textContent+=s+"\n";l.scrollTop=l.scrollHeight;}}
  setInterval(()=>{const c=q('#side-clock');if(c)c.textContent=new Date().toLocaleTimeString('zh-CN',{hour12:false});},1000);

  return {q, esc, pageInit, fetchJSON, postJSON, setCard, badge, toast, onRefresh, viewSum, viewSrt, procOne, log};
})();

// 每页注册器：App.pageInit.<page>() 由模板页脚本定义
document.addEventListener('DOMContentLoaded', () => {
  const page = document.body.dataset.page;
  if (window.App && App.pageInit && App.pageInit[page]) App.pageInit[page]();
});
// Terminal: a real shell on the server in the browser (xterm.js). Super admins only. The session lives on the server, so a reload or a dropped connection reattaches.
import { h, fill, Button, Badge, Alert, PageHeader, Select, Field, Skeleton, confirmDialog } from '../kit.js';
import { api } from '../api.js';
import { attempt } from '../util.js';

const VENDOR = '/static/vendor/xterm/';
let vendorLoaded = null;
function loadVendor() {
  if (!vendorLoaded) {
    vendorLoaded = new Promise((resolve, reject) => {
      const css = h('link', { rel: 'stylesheet', href: VENDOR + 'xterm.css' });
      document.head.append(css);
      const load = (src) => new Promise((ok, bad) => { const s = document.createElement('script'); s.src = VENDOR + src; s.onload = ok; s.onerror = () => bad(new Error('Could not load ' + src)); document.head.append(s); });
      load('xterm.js').then(() => load('addon-fit.js')).then(resolve, reject);
    });
  }
  return vendorLoaded;
}

const toB64 = (text) => { const bytes = new TextEncoder().encode(text); let s = ''; for (let i = 0; i < bytes.length; i += 8192) s += String.fromCharCode(...bytes.subarray(i, i + 8192)); return btoa(s); };
const fromB64 = (b64) => Uint8Array.from(atob(b64), (c) => c.charCodeAt(0));
const KEY = 'cr-terminal-session';
const remember = (id) => { try { id ? sessionStorage.setItem(KEY, id) : sessionStorage.removeItem(KEY); } catch (e) { /* storage blocked: no automatic reattach */ } };
const remembered = () => { try { return sessionStorage.getItem(KEY); } catch (e) { return null; } };

export function TerminalPage() {
  const root = h('div', { class: 'space-y-4' }, Skeleton(3));
  const screen = h('div', { style: 'height:calc(100vh - 17rem);min-height:22rem;background:#0b0f14;border-radius:10px;padding:8px 6px 4px 10px;overflow:hidden' });
  const statusLine = h('span', { class: 'text-xs text-muted' });
  let term = null, fit = null, es = null, sid = null, offset = 0, ended = false, fontSize = 14, sending = Promise.resolve(), pending = '', flushTimer = 0, ro = null, resizeTimer = 0;
  let meta = null;

  const setStatus = (text) => { statusLine.textContent = text; };

  function post(path, body) { return api(path, body); }
  function sendInput(text) {                               // keystrokes are batched for a few ms and sent in order
    pending += text;
    if (flushTimer) return;
    flushTimer = setTimeout(() => {
      const data = pending; pending = ''; flushTimer = 0;
      sending = sending.then(() => post('/api/terminal/input', { id: sid, data: toB64(data) })).catch((e) => { if (!ended) setStatus(`Not sent: ${e.message}`); });
    }, 8);
  }

  function detach() { if (es) { es.close(); es = null; } }

  function attach() {
    detach();
    if (!sid || ended) return;
    es = new EventSource(`/api/terminal/stream?id=${encodeURIComponent(sid)}&since=${offset}`);
    es.onopen = () => setStatus('Connected');
    es.onmessage = (ev) => { const m = JSON.parse(ev.data); term.write(fromB64(m.d)); offset = m.n; };
    es.addEventListener('end', (ev) => {
      ended = true; detach(); remember(null);
      let why = 'the session ended'; try { why = JSON.parse(ev.data).reason || why; } catch (e) { /* default */ }
      term.write(`\r\n\x1b[33m[${why}]\x1b[0m\r\n`);
      setStatus(`Ended: ${why}`);
      drawControls();
    });
    es.onerror = () => {                                   // connection dropped: try again from where we are (the server keeps the screen)
      if (ended) return;
      detach(); setStatus('Connection lost, reconnecting...');
      setTimeout(() => { if (!ended && sid) attach(); }, 1500);
    };
  }

  function pushResize() {
    clearTimeout(resizeTimer);
    resizeTimer = setTimeout(() => { if (sid && term && !ended) post('/api/terminal/resize', { id: sid, rows: term.rows, cols: term.cols }).catch(() => {}); }, 150);
  }

  async function start(shell) {
    await loadVendor();
    if (!term) {
      term = new window.Terminal({ cursorBlink: true, fontSize, fontFamily: 'ui-monospace, SFMono-Regular, Menlo, Consolas, monospace', scrollback: 5000, theme: { background: '#0b0f14', foreground: '#d6deeb', cursor: '#e2e8f0' }, convertEol: false });
      fit = new window.FitAddon.FitAddon();
      term.loadAddon(fit);
      term.open(screen);
      term.onData((d) => { if (!ended && sid) sendInput(d); });
      ro = new ResizeObserver(() => { try { fit.fit(); pushResize(); } catch (e) { /* hidden */ } });
      ro.observe(screen);
    }
    fit.fit();
    term.reset(); ended = false; offset = 0;
    if (shell) {
      const r = await attempt(post('/api/terminal/open', { shell, rows: term.rows, cols: term.cols }));
      if (!r) return;
      sid = r.id; remember(sid);
    }
    setStatus('Connecting...');
    attach();
    term.focus();
    drawControls();
  }

  const shellSel = Select({ value: 'root', options: [{ value: 'root', label: 'Root shell (sudo -i)' }, { value: 'app', label: "The app's own account" }] });

  function drawControls() {
    const open = sid && !ended;
    fill(controls,
      h('div', { class: 'flex flex-wrap items-end gap-3' },
        open ? null : h('div', { class: 'w-56' }, Field({ label: 'Open a terminal as' }, shellSel)),
        open ? null : Button({ label: 'New terminal', icon: 'terminal', variant: 'primary', onClick: () => start(shellSel.value) }),
        open ? Button({ label: 'Close this terminal', icon: 'x', size: 'sm', onClick: async () => {
          const c = await confirmDialog({ title: 'Close this terminal?', message: 'The shell and anything running in it stops.', confirmLabel: 'Close it' });
          if (!c.confirmed) return;
          await attempt(post('/api/terminal/close', { id: sid }));
          ended = true; detach(); remember(null); sid = null; term && term.reset(); setStatus('Closed'); drawControls();
        } }) : null,
        open ? Button({ label: 'A-', size: 'sm', variant: 'ghost', title: 'Smaller text', onClick: () => { fontSize = Math.max(9, fontSize - 1); term.options.fontSize = fontSize; fit.fit(); pushResize(); } }) : null,
        open ? Button({ label: 'A+', size: 'sm', variant: 'ghost', title: 'Larger text', onClick: () => { fontSize = Math.min(28, fontSize + 1); term.options.fontSize = fontSize; fit.fit(); pushResize(); } }) : null,
        h('span', { class: 'ml-auto flex items-center gap-2' }, sid && !ended ? Badge(meta && meta.shell === 'app' ? 'app account' : 'root', meta && meta.shell === 'app' ? 'neutral' : 'warn') : null, statusLine)));
  }

  const controls = h('div', {});

  async function init() {
    try {
      const info = await api('/api/terminal');
      meta = info;
      if (!info.supported) { fill(root, Alert({ tone: 'warn', title: 'The terminal is not available on this computer' }, 'The terminal needs the app to run on Linux or macOS. Open a terminal on your own computer and use SSH to reach the server instead.')); return; }
      if (!info.enabled) {
        fill(root, Alert({ tone: 'warn', title: 'The terminal is switched off' }, 'A super admin can switch it on.', h('div', { class: 'mt-3' }, Button({ label: 'Switch it on', variant: 'primary', size: 'sm', onClick: async () => { if (await attempt(post('/api/terminal/settings', { enabled: true }), 'Terminal switched on')) init(); } }))));
        return;
      }
      fill(root,
        Alert({ tone: 'info', title: 'This is a real shell on your server' }, `Anything you run here has the power of that account. Sessions stay open on the server if you leave this page and close after ${info.idle_minutes} idle minutes; what you type is not stored (only that a session was opened, by whom and for how long).`),
        controls, screen,
        h('p', { class: 'text-xs text-subtle' }, 'Tip: select text to copy it, Ctrl+Shift+V pastes. ', h('button', { type: 'button', class: 'underline', onClick: async () => { const c = await confirmDialog({ title: 'Switch the terminal off?', message: 'Open terminals are closed and nobody can open one until it is switched on again.', confirmLabel: 'Switch off' }); if (c.confirmed && await attempt(post('/api/terminal/settings', { enabled: false }), 'Terminal switched off')) { sid = null; ended = true; detach(); init(); } } }, 'Switch the terminal off')));
      drawControls();
      const mine = info.sessions.find((s) => s.id === remembered()) || info.sessions[0];
      if (mine) { sid = mine.id; meta = Object.assign({}, info, { shell: mine.shell }); await start(null); }
    } catch (e) { fill(root, Alert({ tone: 'bad', title: 'Could not load the terminal' }, e.message)); }
  }

  init();
  return { el: h('div', { class: 'space-y-6 animate-fade-up' }, PageHeader({ title: 'Terminal', description: 'A shell on the server, in your browser. Super admins only.' }), root),
    destroy: () => { detach(); if (ro) ro.disconnect(); clearTimeout(flushTimer); if (term) term.dispose(); } };
}

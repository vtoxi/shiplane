// Files: browse the server's folders, create files and folders, preview and edit text files, rename, delete. Everything goes through /api/files/*.
import { h, fill, Button, Badge, Card, Table, EmptyState, Alert, PageHeader, Input, Skeleton, icon, busy, confirmDialog, openModal, toast } from '../kit.js';
import { bytes } from '../format.js';
import { api } from '../api.js';
import { attempt } from '../util.js';
import { navigate } from '../router.js';

const MIME = { png: 'image/png', jpg: 'image/jpeg', jpeg: 'image/jpeg', gif: 'image/gif', webp: 'image/webp', svg: 'image/svg+xml', ico: 'image/x-icon', bmp: 'image/bmp' };
const enc = (s) => encodeURIComponent(s);
const parent = (p) => (p === '/' ? '/' : p.replace(/\/[^/]+\/?$/, '') || '/');
const join = (a, b) => (a === '/' ? '/' + b : a.replace(/\/$/, '') + '/' + b);
const when = (t) => (t ? new Date(t * 1000).toLocaleString(undefined, { dateStyle: 'medium', timeStyle: 'short' }) : '');
const b64ToText = (b64) => new TextDecoder().decode(Uint8Array.from(atob(b64), (c) => c.charCodeAt(0)));

export function FilesPage({ query }) {
  const path = query.path || '/';
  const root = h('div', { class: 'space-y-4' }, Skeleton(4));
  let dirty = false;
  const guard = async () => !dirty || (await confirmDialog({ title: 'Discard unsaved changes?', message: 'You have changes that are not saved.', confirmLabel: 'Discard', tone: 'danger' })).confirmed;
  const go = async (to) => { if (await guard()) { dirty = false; navigate(to); } };
  const openDir = (p) => go(`/files?path=${enc(p)}`);
  const openFile = (p) => go(`/files?path=${enc(parent(p))}&open=${enc(p)}`);
  const beforeUnload = (e) => { if (dirty) { e.preventDefault(); e.returnValue = ''; } };
  window.addEventListener('beforeunload', beforeUnload);

  function crumbs(p) {
    const parts = p === '/' ? [] : p.split('/').filter(Boolean);
    const link = (label, to) => h('button', { type: 'button', class: 'hover:underline', onClick: () => openDir(to) }, label);
    const out = [link('Server', '/')];
    parts.forEach((seg, i) => { out.push(h('span', { class: 'text-subtle' }, ' / '), link(seg, '/' + parts.slice(0, i + 1).join('/'))); });
    return h('nav', { class: 'text-sm font-mono', 'aria-label': 'Path' }, out);
  }

  // ------------------------------------------------------------------ a folder
  let data = null, filter = '';
  const search = Input({ placeholder: 'Filter this folder...', onInput: (e) => { filter = e.target.value.toLowerCase(); drawList(); } });
  const listBox = h('div', {});

  async function loadDir() {
    try { data = await api(`/api/files/list?path=${enc(path)}`); drawDir(); } catch (e) { fill(root, crumbs(path), Alert({ tone: 'bad', title: 'Could not open this folder' }, e.message, h('div', { class: 'mt-3' }, Button({ label: 'Up', icon: 'up', size: 'sm', onClick: () => openDir(parent(path)) })))); }
  }

  async function newItem(kind) {
    const name = Input({ placeholder: kind === 'file' ? 'index.html' : 'images', mono: true });
    const m = openModal({ title: kind === 'file' ? 'New file' : 'New folder', description: `In ${path}`, body: name,
      actions: [Button({ label: 'Create', variant: 'primary', onClick: async () => {
        const n = name.value.trim();
        if (!n) return;
        const target = join(path, n);
        const r = kind === 'file' ? await attempt(api('/api/files/write', { path: target, content: '', create: true })) : await attempt(api('/api/files/mkdir', { path: target }));
        if (!r) return;
        m.close();
        kind === 'file' ? openFile(target) : loadDir();
      } })] });
    setTimeout(() => name.focus(), 50);
  }

  async function renameItem(e) {
    const name = Input({ value: e.name, mono: true });
    const m = openModal({ title: `Rename ${e.name}`, body: name, actions: [Button({ label: 'Rename', variant: 'primary', onClick: async () => {
      if (name.value && name.value !== e.name && await attempt(api('/api/files/rename', { path: join(path, e.name), name: name.value }), 'Renamed')) { m.close(); loadDir(); }
    } })] });
    setTimeout(() => { name.focus(); name.select(); }, 50);
  }

  async function deleteItem(e) {
    const target = join(path, e.name);
    const c = await confirmDialog({ title: `Delete ${e.name}?`, message: e.type === 'dir' ? 'The folder is deleted. If it has files in it you are asked again.' : 'The file is deleted. This cannot be undone.', confirmLabel: 'Delete' });
    if (!c.confirmed) return;
    try {
      await api('/api/files/delete', { path: target, confirm: true });
    } catch (err) {
      if (!/not empty/i.test(err.message)) { toast(err.message, { tone: 'bad' }); return; }
      const c2 = await confirmDialog({ title: `Delete ${e.name} and everything in it?`, message: 'The folder has files in it. All of them are deleted. This cannot be undone.', confirmLabel: 'Delete everything', requireText: e.name });
      if (!c2.confirmed) return;
      if (!(await attempt(api('/api/files/delete', { path: target, confirm: true, recursive: true })))) return;
    }
    toast('Deleted', { tone: 'ok', timeout: 2000 });
    loadDir();
  }

  function drawList() {
    const rows = data.entries.filter((e) => !filter || e.name.toLowerCase().includes(filter));
    fill(listBox, Table({
      columns: [
        { label: 'Name', render: (e) => h('span', { class: 'inline-flex items-center gap-2 font-medium' }, icon(e.type === 'dir' ? 'folder' : 'file', 'h-4 w-4 text-subtle'), e.name, e.link ? h('span', { class: 'text-xs text-muted font-normal' }, '(link)') : null) },
        { label: 'Size', class: 'text-muted hidden sm:table-cell', render: (e) => (e.type === 'dir' ? '' : bytes(e.size)) },
        { label: 'Modified', class: 'text-muted hidden md:table-cell', render: (e) => when(e.mtime) },
        { label: 'Mode', class: 'font-mono text-xs text-muted hidden lg:table-cell', render: (e) => e.mode },
        { label: '', class: 'text-right', render: (e) => (e.root || !data.writable ? '' : h('div', { class: 'flex justify-end gap-1', onClick: (ev) => ev.stopPropagation() },
          e.lock ? h('span', { class: 'inline-flex items-center gap-1 text-xs text-muted cursor-help', title: e.lock }, icon('lock', 'h-3.5 w-3.5'), 'Protected') : h('span', { class: 'inline-flex gap-1' },
            Button({ label: 'Rename', size: 'sm', variant: 'ghost', onClick: () => renameItem(e) }),
            Button({ icon: 'trash', iconOnly: true, size: 'sm', variant: 'ghost', title: 'Delete', onClick: () => deleteItem(e) })))) },
      ],
      rows,
      onRowClick: (e) => (e.type === 'dir' ? openDir(e.root ? '/' + e.name : join(path, e.name)) : openFile(join(path, e.name))),
      empty: EmptyState({ icon: 'folder', title: data.entries.length ? 'No match' : 'This folder is empty', body: data.entries.length ? 'Try another filter.' : (data.writable ? 'Create a file or a folder above.' : '') }),
    }));
  }

  function drawDir() {
    fill(root, crumbs(path),
      Card({ title: path === '/' ? 'Folders you can open' : path.split('/').pop(), subtitle: data.writable ? undefined : (path === '/' ? undefined : 'Read-only'),
        actions: h('div', { class: 'flex flex-wrap gap-2' },
          path === '/' ? null : Button({ label: 'Up', icon: 'up', size: 'sm', onClick: () => openDir(parent(path)) }),
          data.writable ? Button({ label: 'New file', icon: 'plus', size: 'sm', onClick: () => newItem('file') }) : null,
          data.writable ? Button({ label: 'New folder', icon: 'folder', size: 'sm', onClick: () => newItem('folder') }) : null,
          Button({ icon: 'refresh', iconOnly: true, size: 'sm', title: 'Refresh', onClick: loadDir })), bodyClass: '' },
        data.entries.length > 8 ? h('div', { class: 'border-b border-line px-4 py-3' }, search) : null, listBox,
        data.truncated ? h('p', { class: 'border-t border-line px-4 py-2 text-xs text-muted' }, 'Only the first 3000 entries are shown. Use the filter or a subfolder.') : null));
    drawList();
  }

  // ------------------------------------------------------------------ a file
  async function openEditor(file) {
    let f;
    try { f = await api(`/api/files/read?path=${enc(file)}`); } catch (e) { fill(root, crumbs(parent(file)), Alert({ tone: 'bad', title: 'Could not open this file' }, e.message, h('div', { class: 'mt-3' }, Button({ label: 'Back', size: 'sm', onClick: () => openDir(parent(file)) })))); return; }
    const name = file.split('/').pop();
    const top = (extra) => h('div', { class: 'flex flex-wrap items-center gap-2' },
      Button({ label: 'Close', icon: 'x', size: 'sm', onClick: () => openDir(parent(file)) }), extra,
      Button({ label: 'Download', icon: 'download', size: 'sm', variant: 'ghost', onClick: () => download(file, name) }));
    const info = h('p', { class: 'text-xs text-muted' }, `${bytes(f.size)}, modified ${when(f.mtime)}, mode ${f.mode}${f.writable ? '' : ', read-only'}`);

    if (f.kind === 'text' && !f.too_large) {
      const area = h('textarea', { class: 'input font-mono text-xs', style: 'min-height:60vh;white-space:pre;overflow:auto;tab-size:2', spellcheck: 'false', wrap: 'off', readonly: !f.writable ? '' : null });
      area.value = b64ToText(f.content_b64);
      let mtime = f.mtime;
      let lastSaved = area.value;
      let badge = Badge('saved', 'ok');
      const mark = () => { dirty = area.value !== lastSaved; const nb = Badge(dirty ? 'unsaved changes' : 'saved', dirty ? 'warn' : 'ok'); badge.replaceWith(nb); badge = nb; };
      area.addEventListener('input', mark);
      area.addEventListener('keydown', (e) => {
        if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 's') { e.preventDefault(); save(false); }
        if (e.key === 'Tab' && !e.shiftKey) { e.preventDefault(); const s = area.selectionStart; area.setRangeText('  ', s, area.selectionEnd, 'end'); mark(); }
      });
      async function save(force) {
        const r = await attempt(api('/api/files/write', { path: file, content: area.value, expect_mtime: mtime, force }));
        if (!r) return;
        if (r.conflict) {
          const c = await confirmDialog({ title: 'This file changed on the server', message: 'Someone or something saved it after you opened it. Overwrite it with your version?', confirmLabel: 'Overwrite', tone: 'danger' });
          if (c.confirmed) await save(true);
          return;
        }
        mtime = r.mtime; lastSaved = area.value; mark(); toast('Saved', { tone: 'ok', timeout: 1500 });
      }
      const saveBtn = Button({ label: 'Save', icon: 'check', size: 'sm', variant: 'primary', disabled: !f.writable, onClick: () => busy(saveBtn, () => save(false)) });
      fill(root, crumbs(parent(file)),
        Card({ title: name, subtitle: file, actions: top(h('span', { class: 'flex items-center gap-2' }, saveBtn, badge)) }, info, h('div', { class: 'mt-3' }, area),
          h('p', { class: 'mt-2 text-xs text-subtle' }, 'Ctrl+S (or Cmd+S) saves. If the file changes on the server while you edit, you are asked before anything is overwritten.')));
      return;
    }
    const body = f.kind === 'image' && f.content_b64
      ? h('img', { src: `data:${MIME[(name.split('.').pop() || '').toLowerCase()] || 'application/octet-stream'};base64,${f.content_b64}`, alt: name, style: 'max-width:100%;max-height:70vh;background:repeating-conic-gradient(#8881 0 25%,transparent 0 50%) 50%/16px 16px' })
      : Alert({ tone: 'info', title: f.too_large ? 'This file is too large to open here' : 'This is not a text file' }, f.too_large ? 'Files over 2 MB cannot be edited in the browser.' : 'Binary files cannot be previewed.', ' You can download it.');
    fill(root, crumbs(parent(file)), Card({ title: name, subtitle: file, actions: top(null) }, info, h('div', { class: 'mt-3' }, body)));
  }

  async function download(file, name) {
    const f = await attempt(api(`/api/files/read?path=${enc(file)}&download=1`));
    if (!f) return;
    if (f.too_large || !f.content_b64) { toast('Files over 25 MB cannot be downloaded here. Use SFTP or scp.', { tone: 'warn' }); return; }
    const url = URL.createObjectURL(new Blob([Uint8Array.from(atob(f.content_b64), (c) => c.charCodeAt(0))]));
    const a = h('a', { href: url, download: name });
    document.body.append(a); a.click(); a.remove();
    setTimeout(() => URL.revokeObjectURL(url), 5000);
  }

  query.open ? openEditor(query.open) : loadDir();
  return { el: h('div', { class: 'space-y-6 animate-fade-up' }, PageHeader({ title: 'Files', description: 'Browse, create, preview and edit files on the server. Saves are written safely, and secrets (SSH keys, passwords files) are never shown.' }), root), destroy: () => window.removeEventListener('beforeunload', beforeUnload) };
}

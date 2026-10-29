// Hash router: "#/sites/my-api?tab=logs". A page is a function (ctx) => { el, destroy? }.
const routes = [];
let outlet = null;
let onChange = () => {};
let active = null;

export function route(pattern, page) {
  const keys = [];
  const re = new RegExp('^' + pattern.replace(/:([a-z]+)/g, (_, k) => { keys.push(k); return '([^/]+)'; }) + '/?$');
  routes.push({ pattern, re, keys, page });
}

export function parse() {
  const raw = location.hash.replace(/^#/, '') || '/sites';
  const [path, qs = ''] = raw.split('?');
  return { path, query: Object.fromEntries(new URLSearchParams(qs)) };
}

export function navigate(path) {
  if (location.hash === '#' + path) render();
  else location.hash = '#' + path;
}

function render() {
  const { path, query } = parse();
  if (active && active.destroy) active.destroy();
  for (const r of routes) {
    const m = r.re.exec(path);
    if (m) {
      const params = Object.fromEntries(r.keys.map((k, i) => [k, decodeURIComponent(m[i + 1])]));
      active = r.page({ params, query, path });
      outlet.replaceChildren(active.el);
      window.scrollTo(0, 0);
      onChange({ path, pattern: r.pattern });
      return;
    }
  }
  navigate('/sites');
}

export function start(el, changed) {
  outlet = el;
  onChange = changed;
  window.addEventListener('hashchange', render);
  render();
}

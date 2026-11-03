// Group sites by their main (registrable) domain, so shop.example.com and blog.example.com sit under example.com. Pure functions, no DOM.

// Second-level suffixes where the "main domain" has three labels (example.co.uk, not co.uk). Not the full public-suffix list: the common ones.
const SECOND_LEVEL = new Set(['co.uk', 'org.uk', 'ac.uk', 'gov.uk', 'me.uk', 'ltd.uk', 'plc.uk', 'net.uk', 'sch.uk', 'com.au', 'net.au', 'org.au', 'edu.au', 'gov.au',
  'co.nz', 'org.nz', 'net.nz', 'co.za', 'org.za', 'com.br', 'net.br', 'org.br', 'com.mx', 'com.ar', 'com.co', 'co.jp', 'or.jp', 'ne.jp', 'co.kr', 'or.kr', 'co.in', 'net.in', 'org.in',
  'firm.in', 'gen.in', 'ind.in', 'com.cn', 'net.cn', 'org.cn', 'com.hk', 'com.sg', 'com.my', 'com.ph', 'com.tr', 'com.tw', 'co.il', 'co.id', 'co.th', 'com.vn', 'com.ua', 'com.pl',
  'com.pk', 'com.bd', 'com.ng', 'com.eg', 'com.sa', 'com.pt', 'com.es', 'com.gr', 'com.ru', 'ac.nz', 'govt.nz', 'ac.jp', 'go.jp', 'ac.za', 'gov.za']);

export function mainDomain(host) {
  const labels = String(host || '').toLowerCase().replace(/^\*\./, '').replace(/\.$/, '').split('.').filter(Boolean);
  if (labels.length <= 2) return labels.join('.');
  const last2 = labels.slice(-2).join('.');
  return labels.slice(SECOND_LEVEL.has(last2) ? -3 : -2).join('.');
}

/**
 * sites -> ordered list of entries:
 *   { kind: 'site', site }                                   a site that has no sibling under its main domain
 *   { kind: 'group', domain, main, subs, sites, ... }         two or more sites under one main domain, at least one on a subdomain
 * `main` is the site served on the main domain itself (or null); `subs` the others. Sorted by main domain, then address.
 */
export function groupSites(sites, isHealthy) {
  const byDomain = new Map();
  for (const s of sites) {
    const d = mainDomain(s.hostname) || s.name;
    if (!byDomain.has(d)) byDomain.set(d, []);
    byDomain.get(d).push(s);
  }
  const out = [];
  for (const domain of [...byDomain.keys()].sort()) {
    const list = byDomain.get(domain).slice().sort((a, b) => String(a.hostname).localeCompare(String(b.hostname)));
    const onMain = (s) => String(s.hostname).toLowerCase().replace(/^\*\./, '') === domain;
    const main = list.find(onMain) || null;
    const subs = list.filter((s) => !onMain(s));
    if (list.length < 2 || !subs.length) { list.forEach((site) => out.push({ kind: 'site', site })); continue; }
    const problems = list.filter((s) => !isHealthy(s)).length;
    out.push({ kind: 'group', domain, main, subs, sites: list, problems, healthy: list.length - problems, memory: list.reduce((a, s) => a + (s.mem || 0), 0) });
  }
  return out;
}

export const plural = (n, one, many) => `${n} ${n === 1 ? one : many || one + 's'}`;

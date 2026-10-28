// Small formatting helpers shared by the pages.

export const bytes = (n) => {
  if (n == null) return '-';
  if (n >= 1e9) return (n / 1e9).toFixed(1) + ' GB';
  if (n >= 1e6) return Math.round(n / 1e6) + ' MB';
  return Math.max(0, Math.round(n / 1e3)) + ' KB';
};

export const every = (seconds) => (seconds >= 3600 ? 'every hour' : seconds >= 120 ? `every ${seconds / 60} minutes` : 'every minute');

export function duration(seconds) {
  const s = Math.floor(seconds);
  const d = Math.floor(s / 86400), hh = Math.floor((s % 86400) / 3600), m = Math.floor((s % 3600) / 60);
  if (d) return `${d}d ${hh}h`;
  if (hh) return `${hh}h ${m}m`;
  return `${m}m`;
}

/** Release ids look like 20261023-214009 (server time, UTC). */
export function releaseDate(id) {
  const m = /^(\d{4})(\d\d)(\d\d)-(\d\d)(\d\d)(\d\d)$/.exec(id || '');
  return m ? new Date(Date.UTC(+m[1], m[2] - 1, +m[3], +m[4], +m[5], +m[6])) : null;
}

export function ago(id) {
  const d = releaseDate(id);
  if (!d) return id || '-';
  const s = (Date.now() - d.getTime()) / 1000;
  return s < 60 ? 'just now' : duration(s) + ' ago';
}

export const pct = (part, total) => (total ? Math.round((part / total) * 100) : 0);

(function () {
  var $ = function (id) { return document.getElementById(id); };
  var token = new URLSearchParams(location.search).get('t') || '';
  var words = { ok: 'All systems operational', degraded: 'Some systems are down', down: 'Systems are down', unknown: 'No data yet' };
  function load() {
    fetch('/api/public/status?t=' + encodeURIComponent(token), { cache: 'no-store' }).then(function (r) {
      if (!r.ok) throw new Error('not found');
      return r.json();
    }).then(function (d) {
      document.title = d.title; $('title').textContent = d.title;
      $('overall').textContent = words[d.overall] || d.overall; $('overall').className = 'overall ' + d.overall;
      var ul = $('sites'); ul.textContent = '';
      d.sites.forEach(function (s) {
        var li = document.createElement('li'), a = document.createElement('span'), b = document.createElement('span');
        var dot = document.createElement('span'); dot.className = 'dot' + (s.up ? '' : ' down');
        a.appendChild(dot); a.appendChild(document.createTextNode(s.name));
        b.className = 'muted'; b.textContent = (s.up ? 'Up' : 'Down') + (s.uptime == null ? '' : ' - ' + s.uptime + '% uptime');
        li.appendChild(a); li.appendChild(b); ul.appendChild(li);
      });
      $('foot').textContent = 'Updated ' + new Date().toLocaleTimeString();
    }).catch(function () { $('overall').textContent = 'This status page is not available.'; $('overall').className = 'overall'; $('sites').textContent = ''; });
  }
  load(); setInterval(load, 60000);
  if ('serviceWorker' in navigator) navigator.serviceWorker.register('/sw.js').catch(function () {});
})();

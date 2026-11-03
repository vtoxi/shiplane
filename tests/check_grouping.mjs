// Node test for web/js/grouping.js (no browser needed): node tests/check_grouping.mjs
import assert from 'node:assert/strict';
import { mainDomain, groupSites, plural } from '../web/js/grouping.js';

const healthy = (s) => !s.bad;
const site = (name, hostname, extra = {}) => Object.assign({ name, hostname }, extra);

assert.equal(mainDomain('shop.example.com'), 'example.com');
assert.equal(mainDomain('a.b.c.example.com'), 'example.com');
assert.equal(mainDomain('example.com'), 'example.com');
assert.equal(mainDomain('Example.COM.'), 'example.com');
assert.equal(mainDomain('*.example.com'), 'example.com');
assert.equal(mainDomain('shop.example.co.uk'), 'example.co.uk');
assert.equal(mainDomain('example.co.uk'), 'example.co.uk');
assert.equal(mainDomain('server.rankin.work'), 'rankin.work');
assert.equal(mainDomain('shiplane.uk'), 'shiplane.uk');
assert.equal(mainDomain('localhost'), 'localhost');
assert.equal(mainDomain(''), '');

const sites = [
  site('blog', 'blog.example.com', { mem: 100 }), site('main', 'example.com', { mem: 50 }), site('shop', 'shop.example.com', { bad: true, mem: 25 }),
  site('solo', 'solo.other.org'), site('lone-apex', 'apex.net'), site('a', 'a.zeta.io'), site('b', 'b.zeta.io'),
];
const g = groupSites(sites, healthy);
assert.deepEqual(g.map((e) => e.kind === 'group' ? 'G:' + e.domain : 'S:' + e.site.name), ['S:lone-apex', 'G:example.com', 'S:solo', 'G:zeta.io']);   // sorted by main domain; lone sites stay plain rows

const ex = g[1];
assert.equal(ex.main.name, 'main');
assert.deepEqual(ex.subs.map((s) => s.name), ['blog', 'shop']);
assert.equal(ex.sites.length, 3);
assert.equal(ex.problems, 1);
assert.equal(ex.healthy, 2);
assert.equal(ex.memory, 175);

const z = g[3];                                    // two subdomains and no site on the main domain itself
assert.equal(z.main, null);
assert.equal(z.subs.length, 2);

assert.deepEqual(groupSites([], healthy), []);
assert.deepEqual(groupSites([site('x', 'example.com')], healthy).map((e) => e.kind), ['site']);                                  // one site: never a group
assert.deepEqual(groupSites([site('x', 'example.com'), site('y', 'example.com')], healthy).map((e) => e.kind), ['site', 'site']);   // same host twice: no subdomain, no group
assert.equal(groupSites([site('x', 'a.example.com'), site('y', 'b.example.com')], healthy)[0].kind, 'group');
assert.equal(groupSites([site('x', '*.example.com'), site('y', 'app.example.com')], healthy)[0].main.name, 'x');                  // a wildcard site counts as the main domain's

assert.equal(plural(1, 'subdomain'), '1 subdomain');
assert.equal(plural(2, 'subdomain'), '2 subdomains');
assert.equal(plural(3, 'site', 'sites'), '3 sites');
console.log('grouping ok');

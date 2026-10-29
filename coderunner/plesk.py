"""Reading a Plesk backup (a backup exported from Plesk as .tgz, .tar or .zip) without extracting it.

Built from Plesk's documented backup layout: a backup_info_*.xml, one backup_user-data_*.tgz per domain with its
document root, a databases/ folder with the dumps, and sub-domains under sites/. The exact XML fields and dump
file names are not published, so everything here is read leniently and the screen says what was found.
"""
import os
import re
import tarfile
import xml.etree.ElementTree as ET
import zipfile

from . import migrate

CONTAINER_RE = re.compile(r'\.(tar\.gz|tgz|tar|zip)$', re.I)
MAX_XML = 5 << 20
DOCROOT = 'httpdocs'
DUMP_EXT = re.compile(r'(\.(tar|gz|tgz|sql|dump|zip))+$', re.I)       # wp.sql.gz, wp.tar.gz, wp.dump ...


class NotPlesk(ValueError):
    pass


def iter_files(path):
    """(name, size, open) for every file of a tar/tgz/zip. open() returns a binary stream that is only valid until the next item."""
    try:
        if path.lower().endswith('.zip'):
            with zipfile.ZipFile(path) as z:
                for i in z.infolist():
                    if not i.is_dir():
                        yield i.filename, i.file_size, (lambda i=i: z.open(i))
        else:
            with tarfile.open(path, 'r:*') as t:
                for m in t:
                    if m.isfile():
                        yield m.name, m.size, (lambda m=m: t.extractfile(m))
    except (tarfile.TarError, zipfile.BadZipFile, OSError, EOFError) as e:
        raise ValueError('Could not read the backup: %s' % e)


def iter_tree(stream, name):
    """(path, size, open) for the files of an inner archive (a domain's user-data), read as a stream."""
    if name.lower().endswith(('.tzst', '.zst')):
        raise ValueError('This backup stores content as .tzst (zstd). Export it from Plesk as .tgz, or use the live-server source.')
    try:
        with tarfile.open(fileobj=stream, mode='r|*') as t:
            for m in t:
                if m.isfile():
                    yield m.name.lstrip('./'), m.size, (lambda m=m: t.extractfile(m))
    except (tarfile.TarError, OSError, EOFError) as e:
        raise ValueError('Could not read %s: %s' % (name, e))


def _local(tag):
    return tag.rsplit('}', 1)[-1].lower()


def parse_info(raw):
    """What a backup_info XML says: database types by name, the domains and how many mail accounts. Unreadable XML yields nothing."""
    out = {'databases': {}, 'domains': [], 'mailnames': 0}
    if b'<!DOCTYPE' in raw or b'<!ENTITY' in raw:                       # no entity tricks in an uploaded file
        return out
    try:
        root = ET.fromstring(raw)
    except ET.ParseError:
        return out
    for el in root.iter():
        tag = _local(el.tag)
        if tag == 'database' and el.get('name'):
            out['databases'][el.get('name')] = el.get('type', 'mysql')
        elif tag == 'domain' and el.get('name'):
            out['domains'].append(el.get('name').lower())
        elif tag == 'mailname':
            out['mailnames'] += 1
    return out


def _domain_of(parts):
    """Domain a member belongs to: the folder under sites/ (a sub-domain), else under domains/ (without Plesk's ' <ID>' suffix)."""
    for key in ('sites', 'domains'):
        if key in parts[:-1] and parts.index(key) + 1 < len(parts) - 1:
            return re.sub(r'\s+\d+$', '', parts[parts.index(key) + 1]).lower()
    return None


def _db_name(parts):
    i = parts.index('databases')
    after = parts[i + 1:]
    if len(after) >= 2:
        return after[0]
    base = re.sub(r'^backup_', '', DUMP_EXT.sub('', after[0]))
    return re.sub(r'[_.-]?\d{10}(_\d{10})?$', '', base) or after[0]


def _scan_tree(stream, name):
    entries = list(iter_tree(stream, name))
    prefix = DOCROOT if any(p.startswith(DOCROOT + '/') for p, _, _ in entries) else ''
    head = prefix + '/' if prefix else ''
    root_files, php, size = set(), False, 0
    for p, sz, _ in entries:
        if not p.startswith(head):
            continue
        rel = p[len(head):]
        size += sz
        if rel in migrate.MARKERS:
            root_files.add(rel)
        php = php or rel.endswith('.php')
    return prefix, size, migrate._kind(root_files, php)


def inspect(path):
    path = os.path.abspath(os.path.expanduser(str(path)))
    sites, dbs, info = {}, {}, {'databases': {}, 'domains': [], 'mailnames': 0}
    saw_xml = False
    for name, size, opener in iter_files(path):
        parts = name.replace('\\', '/').split('/')
        base = parts[-1].lower()
        if base.startswith('backup_info') and base.endswith('.xml') and size <= MAX_XML:
            saw_xml = True
            found = parse_info(opener().read())
            info['databases'].update(found['databases'])
            info['domains'] += found['domains']
            info['mailnames'] += found['mailnames']
        elif 'user-data' in base and (CONTAINER_RE.search(base) or base.endswith(('.tzst', '.zst'))):
            domain = _domain_of(parts) or (info['domains'][0] if info['domains'] else None)
            if domain is None:
                raise ValueError('Found site files but not which domain they belong to')
            prefix, tree_size, kind = _scan_tree(opener(), base)
            if domain not in sites or tree_size > sites[domain]['size']:
                sites[domain] = {'domain': domain, 'aliases': [], 'docroot': '/' + (prefix or '.'), 'size': tree_size, 'kind': kind, 'php': '',
                                 'src': {'member': name, 'prefix': prefix}}
        elif 'databases' in parts[:-1]:
            n = _db_name(parts)
            if n not in dbs or size > dbs[n]['size']:
                dbs[n] = {'name': n, 'engine': 'mysql', 'size': size, 'member': name}
    if not saw_xml:
        raise NotPlesk('This does not look like a Plesk backup (no backup_info XML file)')
    for n, d in dbs.items():
        d['engine'] = 'postgres' if 'postgre' in info['databases'].get(n, 'mysql').lower() else 'mysql'
    return migrate.finish({'format': 'plesk', 'panel': 'Plesk backup', 'accounts': [], 'sites': sorted(sites.values(), key=lambda s: s['domain']),
                           'databases': sorted(dbs.values(), key=lambda d: d['name']), 'cron': [], 'mailboxes': info['mailnames']})

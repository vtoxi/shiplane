#!/usr/bin/env python3
"""Creates samples/cpmove-demo.tar.gz (cPanel) and samples/plesk-demo.tgz (Plesk): small made-up backups for trying the Migrate wizard.

    python scripts/make_sample_backup.py [cpanel-output.tar.gz]

Contents: a WordPress blog with a MySQL database, a PHP site (add-on domain), a static site, a Node app,
a nested sub-domain, two mailboxes, a cron job and a PostgreSQL database. Everything is made up.
"""
import io
import os
import sys
import tarfile

TOP = 'cpmove-demo'
WP_CONFIG = """<?php
define( 'DB_NAME', 'demo_wp' );
define( 'DB_USER', 'demo_wpuser' );
define( 'DB_PASSWORD', 'old-secret-password' );
define( 'DB_HOST', 'localhost' );
$table_prefix = 'wp_';
require_once ABSPATH . 'wp-settings.php';
"""
WP_DUMP = """-- Sample WordPress dump
CREATE TABLE `wp_options` (`option_id` bigint unsigned NOT NULL AUTO_INCREMENT, `option_name` varchar(191) NOT NULL, `option_value` longtext NOT NULL, PRIMARY KEY (`option_id`));
INSERT INTO `wp_options` VALUES (1,'siteurl','https://demo-blog.example'),(2,'blogname','Demo Blog');
CREATE TABLE `wp_posts` (`ID` bigint unsigned NOT NULL AUTO_INCREMENT, `post_title` text NOT NULL, PRIMARY KEY (`ID`));
INSERT INTO `wp_posts` VALUES (1,'Hello world!');
"""
SHOP_DUMP = "CREATE TABLE `orders` (`id` int NOT NULL AUTO_INCREMENT, `total` decimal(10,2), PRIMARY KEY (`id`));\nINSERT INTO `orders` VALUES (1, 19.90), (2, 42.00);\n"


def userdata(domain, docroot, alias='', php='ea-php82'):
    return 'documentroot: /home/demo/%s\nhomedir: /home/demo\nservername: %s\nserveralias: %s\nphpversion: %s\n' % (docroot, domain, alias, php)


FILES = {
    'cp/demo': 'IP=203.0.113.7\nDNS=demo-blog.example\n',
    'cron/demo': '# nightly\n30 2 * * * php /home/demo/public_html/wp-cron.php\n0 4 * * 0 /usr/bin/node /home/demo/api/cleanup.js\n',
    'userdata/main': 'main_domain: demo-blog.example\n',
    'userdata/demo-blog.example': userdata('demo-blog.example', 'public_html', 'www.demo-blog.example'),
    'userdata/demo-blog.example_SSL': userdata('demo-blog.example', 'public_html'),
    'userdata/shop.demo-blog.example': userdata('shop.demo-blog.example', 'public_html/shop'),
    'userdata/landing.example': userdata('landing.example', 'landing', 'www.landing.example', ''),
    'userdata/api.example': userdata('api.example', 'api', '', ''),
    'userdata/crafts.example': userdata('crafts.example', 'crafts', 'www.crafts.example'),
    'homedir/public_html/index.php': '<?php // WordPress front controller\nrequire __DIR__ . "/wp-blog-header.php";\n',
    'homedir/public_html/wp-config.php': WP_CONFIG,
    'homedir/public_html/wp-content/themes/demo/style.css': '/* Theme: Demo */\nbody { font-family: sans-serif; }\n',
    'homedir/public_html/wp-content/uploads/2026/01/logo.txt': 'pretend this is an image\n' * 40,
    'homedir/public_html/shop/index.php': '<?php echo "Shop"; ?>\n',
    'homedir/public_html/shop/composer.json': '{"name": "demo/shop"}\n',
    'homedir/landing/index.html': '<!doctype html><title>Landing</title><h1>Coming soon</h1>\n',
    'homedir/landing/css/site.css': 'h1 { color: #0a5; }\n',
    'homedir/api/package.json': '{"name": "demo-api", "main": "server.js"}\n',
    'homedir/api/server.js': 'require("http").createServer((q, r) => r.end("ok")).listen(3000);\n',
    'homedir/crafts/index.php': '<?php echo "Crafts"; ?>\n',
    'homedir/crafts/.env': 'APP_NAME=Crafts\nDB_CONNECTION=mysql\nDB_HOST=localhost\nDB_DATABASE=demo_shop\nDB_USERNAME=demo_shopuser\nDB_PASSWORD=old-shop-password\n',
    'homedir/mail/demo-blog.example/info/cur/1700000000.M1': 'Subject: hello\n\nA message\n',
    'homedir/mail/demo-blog.example/sales/cur/1700000001.M2': 'Subject: order\n\nAnother\n',
    'mysql/demo_wp.sql': WP_DUMP,
    'mysql/demo_shop.sql': SHOP_DUMP,
    'mysql.sql': "GRANT ALL PRIVILEGES ON `demo_wp`.* TO 'demo_wpuser'@'localhost';\nGRANT ALL PRIVILEGES ON `demo_shop`.* TO 'demo_shopuser'@'localhost';\n",
    'psql/demo_stats.tar': 'pretend postgres dump\n',
}


def build(path, extra=None):
    """Write the sample backup to path (a .tar.gz). extra: more {name: text} members, e.g. for tests."""
    files = dict(FILES, **(extra or {}))
    with tarfile.open(path, 'w:gz') as tar:
        for name, text in files.items():
            data = text.encode()
            info = tarfile.TarInfo('%s/%s' % (TOP, name))
            info.size = len(data)
            info.mtime = 1767225600
            tar.addfile(info, io.BytesIO(data))
    return path


PLESK_INFO = """<?xml version="1.0" encoding="UTF-8"?>
<migration-dump dump-format="panel" dump-version="18.0.0">
  <domain name="shop.example" id="7">
    <database name="shop_wp" type="mysql"/>
    <database name="shop_stats" type="postgresql"/>
    <mail><mailname name="info"/><mailname name="orders"/></mail>
  </domain>
</migration-dump>
"""


def _tgz(files):
    """An in-memory .tgz of {path: text}."""
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode='w:gz') as tar:
        for name, text in files.items():
            data = text.encode()
            info = tarfile.TarInfo(name)
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
    return buf.getvalue()


def build_plesk(path, extra=None):
    """Write a sample backup in Plesk's exported layout (info XML, user-data archives, databases folder, sites folder)."""
    ts = '2601011200'
    members = {
        'backup_info_%s.xml' % ts: PLESK_INFO.encode(),
        'domains/shop.example/backup_user-data_%s.tgz' % ts: _tgz({
            'httpdocs/index.php': '<?php require __DIR__ . "/wp-blog-header.php";\n',
            'httpdocs/wp-config.php': WP_CONFIG.replace('demo_wp', 'shop_wp'),
            'httpdocs/wp-content/uploads/a.txt': 'upload\n' * 20,
            'cgi-bin/ignored.cgi': 'not part of the document root'}),
        'domains/shop.example/databases/shop_wp/backup_shop_wp_%s.tgz' % ts: _tgz({'shop_wp.sql': WP_DUMP.replace('Demo Blog', 'Shop')}),
        'domains/shop.example/databases/shop_stats.sql': '-- postgres dump placeholder\n',
        'domains/shop.example/sites/blog.shop.example/backup_user-data_%s.tgz' % ts: _tgz({'httpdocs/index.html': '<h1>Blog</h1>\n'}),
    }
    members.update(extra or {})
    with tarfile.open(path, 'w:gz') as tar:
        for name, data in members.items():
            data = data if isinstance(data, bytes) else data.encode()
            info = tarfile.TarInfo(name)
            info.size = len(data)
            info.mtime = 1767225600
            tar.addfile(info, io.BytesIO(data))
    return path


if __name__ == '__main__':
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    folder = os.path.join(root, 'samples')
    if len(sys.argv) > 1:
        print('Wrote', build(sys.argv[1]))
    else:
        os.makedirs(folder, exist_ok=True)
        print('Wrote', build(os.path.join(folder, 'cpmove-demo.tar.gz')))
        print('Wrote', build_plesk(os.path.join(folder, 'plesk-demo.tgz')))

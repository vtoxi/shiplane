import json, os, subprocess


def sh(c, t=30):
    try:
        return subprocess.run(c, shell=True, capture_output=True, text=True, cwd='/tmp', timeout=t).stdout.strip()
    except subprocess.TimeoutExpired:
        return ''


upg = [l for l in sh('apt list --upgradable 2>/dev/null', 90).splitlines()[1:] if l.strip()]
print(json.dumps({
    'ufw': sh('ufw status verbose'),
    'fail2ban': sh('fail2ban-client status sshd'),
    'updates': upg[:40],
    'updates_count': len(upg),
    'reboot_required': os.path.exists('/var/run/reboot-required'),
    'top': sh('ps -eo pid,user,%mem,%cpu,comm --sort=-%mem | head -8'),
    'docker_disk': sh('docker system df 2>/dev/null'),
    'listening': sh("ss -tlnH | awk '{print $4}' | sort -u"),
    'last_logins': sh('last -n 6 -w | head -7'),
    'timezone': sh('timedatectl show -p Timezone --value'),
}))

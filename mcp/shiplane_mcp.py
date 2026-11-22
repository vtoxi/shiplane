#!/usr/bin/env python3
"""Shiplane MCP server (local, stdio): lets Claude Code, Claude Desktop or any MCP client manage a Shiplane server. Python standard library only.

    python mcp/shiplane_mcp.py           # speaks MCP (JSON-RPC, one message per line) on stdin/stdout

Settings (environment, or an env file; the real environment wins):
    SHIPLANE_URL=https://shiplane.example.com
    SHIPLANE_KEY=crk_...                 # Settings > API keys. Never taken from the command line.
    SHIPLANE_ENV_FILE=path               # default lookup: ./.env, scripts/.env of this repository, ~/.shiplane.env
    SHIPLANE_MCP_READONLY=1              # hide every tool that changes something
    SHIPLANE_MCP_MODE=code               # Code Mode: two tools (shiplane_search, shiplane_execute) instead of ~100
    SHIPLANE_MCP_WAIT=900                # seconds to wait for a deploy/backup/upgrade job before returning its job id

Your Shiplane can also serve the same tools itself at https://<your-shiplane>/mcp (a super admin switches it on in Settings), which needs
nothing installed: `claude mcp add --transport http shiplane https://<your-shiplane>/mcp --header "Authorization: Bearer crk_..."`.
The tools live in coderunner/mcp_core.py.
"""
import os
import sys

def load_env_file():
    """Fill SHIPLANE_* settings from an env file when they are not already in the environment (the real environment always wins).
    Looked up in order: $SHIPLANE_ENV_FILE, ./.env, scripts/.env of this repository, ~/.shiplane.env. Lines are KEY=VALUE (optionally `export KEY=VALUE`, quoted values allowed);
    only SHIPLANE_* names are read. Values are never printed."""
    here = os.path.dirname(os.path.abspath(__file__))
    for path in (os.environ.get('SHIPLANE_ENV_FILE'), os.path.join(os.getcwd(), '.env'), os.path.join(here, '..', 'scripts', '.env'), os.path.expanduser('~/.shiplane.env')):
        if not path or not os.path.isfile(path):
            continue
        try:
            with open(path, encoding='utf-8-sig') as f:
                for line in f:
                    line = line.strip()
                    if not line or line.startswith('#'):
                        continue
                    if line.startswith('export '):
                        line = line[7:].lstrip()
                    k, sep, v = line.partition('=')
                    k, v = k.strip(), v.strip()
                    if sep and k.startswith('SHIPLANE_') and k not in os.environ:
                        if len(v) >= 2 and v[0] == v[-1] and v[0] in '"\'':
                            v = v[1:-1]
                        elif ' #' in v:
                            v = v.split(' #', 1)[0].rstrip()
                        os.environ[k] = v
        except (OSError, UnicodeDecodeError):
            pass


load_env_file()
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from coderunner import mcp_core  # noqa: E402

if __name__ == '__main__':
    mcp_core.main()

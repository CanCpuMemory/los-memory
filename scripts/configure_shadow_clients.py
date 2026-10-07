"""Add a private shadow MCP to installed clients, retaining primary Nowledge configuration."""

import json
import os
from pathlib import Path
import shutil
import subprocess
import time


def main():
    os.umask(0o077)
    home = Path.home()
    backup = home / ".local/share/los-memory-shadow/client-backups" / str(int(time.time()))
    backup.mkdir(parents=True, mode=0o700)
    files = [home / ".codex/config.toml", home / ".grok/config.toml", home / ".kimi-code/mcp.json"]
    for path in files:
        if path.exists():
            target = backup / (path.parent.name + "-" + path.name)
            shutil.copyfile(path, target)
            target.chmod(0o600)
    command = ["/usr/bin/ssh", "-T", "-o", "BatchMode=yes", "-o", "ConnectTimeout=5",
               "m3-t", "~/.local/share/los-memory-shadow/serve"]
    configured = []
    for client in ("codex", "grok"):
        executable = shutil.which(client)
        if executable:
            arguments = [executable, "mcp", "add"]
            if client == "grok":
                arguments += ["--scope", "user"]
            subprocess.run(arguments + ["los-memory-shadow", "--"] + command,
                           capture_output=True, text=True, check=True)
            configured.append(client)
    if shutil.which("kimi"):
        path = home / ".kimi-code/mcp.json"
        data = json.loads(path.read_text()) if path.exists() else {"mcpServers": {}}
        servers = data.setdefault("mcpServers", {})
        generated = subprocess.run(["nmem", "config", "mcp", "show", "--host", "kimi-code", "--json"],
                                   capture_output=True, text=True, check=True)
        primary = json.loads(generated.stdout)["config"]["mcpServers"]
        for name, settings in primary.items():
            servers.setdefault(name, settings)
        servers["los-memory-shadow"] = {"command": command[0], "args": command[1:]}
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(".json.tmp")
        temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n")
        temporary.chmod(0o600)
        temporary.replace(path)
        configured.append("kimi")
    print(json.dumps({"configured": configured, "backup": str(backup), "restart_new_sessions": True}))


if __name__ == "__main__":
    main()

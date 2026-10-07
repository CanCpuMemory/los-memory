"""Install a content-addressed shadow release and a private launchd job over SSH."""

import argparse
import hashlib
import io
import json
from pathlib import Path
import subprocess
import tarfile


REMOTE_INSTALL = r'''
import io,json,os,pathlib,plistlib,subprocess,sys,tarfile
os.umask(0o077)
root=pathlib.Path.home()/'.local/share/los-memory-shadow'
root.mkdir(parents=True,exist_ok=True,mode=0o700)
digest=sys.argv[1]
release=root/'releases'/digest
release.mkdir(parents=True,exist_ok=True,mode=0o700)
with tarfile.open(fileobj=io.BytesIO(sys.stdin.buffer.read())) as archive:
    archive.extractall(release,filter='data')
launcher=root/'serve'
launcher.write_text('#!/bin/sh\ncd "'+str(release)+'" || exit 1\nexec "'+str(pathlib.Path.home()/'.local/bin/python3')+'" -m memory_tool.shadow_mcp "$@"\n'.replace('\\n','\n'))
launcher.chmod(0o700)
agent=pathlib.Path.home()/'Library/LaunchAgents/co.los.memory-shadow.plist'
agent.parent.mkdir(parents=True,exist_ok=True)
job={'Label':'co.los.memory-shadow','ProgramArguments':[str(pathlib.Path.home()/'.local/bin/python3'),'-m','memory_tool.shadow','sync','--batch','100'],
     'WorkingDirectory':str(release),'StartInterval':300,'RunAtLoad':False,
     'StandardOutPath':str(root/'sync.out.log'),'StandardErrorPath':str(root/'sync.err.log'),
     'ProcessType':'Background','LowPriorityIO':True,'Umask':63}
agent.write_bytes(plistlib.dumps(job))
agent.chmod(0o600)
domain='gui/'+str(os.getuid())
subprocess.run(['launchctl','bootout',domain+'/co.los.memory-shadow'],capture_output=True)
subprocess.run(['launchctl','bootstrap',domain,str(agent)],check=True)
print(json.dumps({'release':digest,'launcher':str(launcher),'job':job['Label']}))
'''


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="m3-t")
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    files = sorted((root / "memory_tool").rglob("*.py"))
    digest = hashlib.sha256()
    stream = io.BytesIO()
    with tarfile.open(fileobj=stream, mode="w") as archive:
        for path in files:
            relative = path.relative_to(root).as_posix()
            digest.update(relative.encode() + b"\0" + path.read_bytes())
            archive.add(path, arcname=relative)
    release = digest.hexdigest()[:20]
    import shlex
    command = "~/.local/bin/python3 -c " + shlex.quote(REMOTE_INSTALL) + " " + release
    result = subprocess.run(["ssh", "-T", "-o", "BatchMode=yes", "-o", "ConnectTimeout=5",
                             args.host, command], input=stream.getvalue(), capture_output=True, check=True)
    print(result.stdout.decode().strip())


if __name__ == "__main__":
    main()

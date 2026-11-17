"""Install verified clock-only wheels into explicitly selected local runtimes."""
from pathlib import Path
import json
import subprocess
import sys

root = Path(__file__).resolve().parents[2]
if len(sys.argv) < 2:
    raise SystemExit('Pass one or more existing virtual-environment Python executables')
for value in sys.argv[1:]:
    python = Path(value).absolute()
    if not python.is_file():
        raise SystemExit('Clock target interpreter does not exist')
    subprocess.run([sys.executable,'-m','pip','--python',str(python),'install',
        '--index-url','https://pypi.org/simple','--only-binary=:all:','--no-deps',
        '--require-hashes','-r',str(root/'vendor/python-clock/requirements.txt')],check=True)
    script = 'import sys,json;sys.path.insert(0,sys.argv[1]);from pacbench.python_provider_clock import package_metadata;print(json.dumps(package_metadata()))'
    checked = subprocess.run([str(python),'-c',script,str(root)],check=True,capture_output=True,text=True)
    print(json.dumps({'python':str(python),'runtime':json.loads(checked.stdout)}))

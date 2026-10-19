#!/usr/bin/env python3
"""Проверки исходников без запуска приложения и доступа к production."""
import ast
import json
from pathlib import Path
import subprocess
import sys

root=Path(__file__).resolve().parents[2]
files=subprocess.check_output(['git','ls-files','-z'],cwd=root).decode().split('\0')
errors=[]
for rel in filter(None,files):
    p=root/rel
    if not p.is_file():continue
    name=p.name.lower()
    env_file=name=='.env' or name.startswith('.env.')
    env_example=name=='.env.example' or name.startswith('.env.') and name.endswith('.example')
    forbidden=(env_file and not env_example) or name in {'.ui-token','env.d12'} or name.endswith(('.session','.session-journal','.db','.db-wal','.db-shm','.sqlite','.sqlite3','.pem','.key','.ovpn','.bin','.safetensors'))
    if forbidden:errors.append(f'{rel}: runtime/секретный файл в Git')
    try:
        if p.suffix=='.py':ast.parse(p.read_bytes(),filename=rel)
        elif p.suffix=='.json':json.loads(p.read_bytes())
        elif p.suffix=='.sh':
            r=subprocess.run(['bash','-n'],input=p.read_bytes().replace(b'\r\n',b'\n'),capture_output=True)
            if r.returncode:errors.append(f'{rel}: ошибка синтаксиса bash')
    except (SyntaxError,ValueError,UnicodeError) as exc:errors.append(f'{rel}: {type(exc).__name__}')
if errors:
    print('\n'.join(errors));sys.exit(1)
print('Синтаксис исходников и исключение runtime-файлов: OK')

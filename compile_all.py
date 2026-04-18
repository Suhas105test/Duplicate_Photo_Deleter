import py_compile
from pathlib import Path
import sys

errors = []
for root in [Path('src'), Path('tests')]:
    for p in root.rglob('*.py'):
        try:
            py_compile.compile(str(p), doraise=True)
        except Exception as e:
            errors.append((p, e))

if errors:
    print('ERRORS FOUND')
    for p, e in errors:
        print(p, e)
    sys.exit(1)

print('COMPILATION PASSED')

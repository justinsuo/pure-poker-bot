#!/usr/bin/env sh
set -eu
cd "$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"

PYTHON=${PYTHON:-python3}
"$PYTHON" -c 'import sys; print("Python", sys.version.split()[0]); assert sys.version_info >= (3, 10), "Python 3.10+ is required"'
if [ ! -x .venv/bin/python ]; then
    "$PYTHON" -m venv .venv
fi
.venv/bin/python -m pip install -r requirements.txt
if ! .venv/bin/python -m pip install -r requirements-optional.txt; then
    printf 'WARNING: Optional iPhone features failed; Enter fallback and fixed-region capture still work.\n'
fi
.venv/bin/python -m unittest discover -s tests -v
.venv/bin/python tools/doctor.py --offline
printf '\nSetup complete. Android: README.md. iPhone: README_IPHONE.md.\n'

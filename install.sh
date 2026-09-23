#!/usr/bin/env bash
# Kept so a clone can still run ./install.sh. The installer itself is core/clean_code/install.py.
set -euo pipefail
python3 -c 'import sys; sys.exit(sys.version_info < (3, 9))' 2>/dev/null || { echo "python3 3.9 or newer is required." >&2; exit 1; }
exec python3 "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/core/clean_check.py" install "$@"

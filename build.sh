#!/usr/bin/env bash
set -euo pipefail

python -m pip install -r requirements-ml.txt
python manage.py collectstatic --noinput

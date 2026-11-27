#!/bin/sh
# Serves CGP with gunicorn, or runs the command given instead
set -e

if [ $# -gt 0 ]; then
  exec "$@"
fi

exec gunicorn \
  --bind "0.0.0.0:${PORT:-80}" \
  --workers "${CGP_WORKERS:-2}" \
  --threads "${CGP_THREADS:-8}" \
  --worker-class gthread \
  --access-logfile - \
  --forwarded-allow-ips '*' \
  --no-control-socket \
  cgp.wsgi:app

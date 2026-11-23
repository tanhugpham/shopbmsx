#!/bin/sh
# Chạy trên Render. Không in bí mật. Không tắt DRY_RUN.
set -eu
export PYTHONPATH="${PYTHONPATH:-src}"
export DRY_RUN=true
export ENABLE_PURCHASE=false
export ENABLE_TELEGRAM=false
if [ -n "${GOOGLE_CREDENTIALS_JSON:-}" ]; then
  printf '%s' "$GOOGLE_CREDENTIALS_JSON" > "${GOOGLE_CREDENTIALS_FILE:-./GOOGLE_CREDENTIALS_FILE.json}"
fi
# Web Service miễn phí: python -m shop_integration.web
# Lệnh này giữ cho chạy tay / GitHub Actions. Không tắt DRY_RUN.
python -m shop_integration sync-sources

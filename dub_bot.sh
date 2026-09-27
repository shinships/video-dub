#!/usr/bin/env bash
# Khởi động Duby Bot lắng nghe yêu cầu tải và lồng tiếng từ Telegram
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$DIR"

export PATH="/Users/mktmda/aicoworker/app/nodejs/bin:/opt/homebrew/bin:/usr/local/bin:$PATH"
export PYTHONUNBUFFERED=1

PYTHON="$DIR/.venv/bin/python"
if [ ! -f "$PYTHON" ]; then
    echo "❌ Không tìm thấy Python môi trường ảo $PYTHON"
    exit 1
fi

exec "$PYTHON" "$DIR/tools/telegram_dub_bot.py" "$@"

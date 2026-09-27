#!/usr/bin/env bash
# Kịch bản chạy dịch vụ tự động theo dõi thư mục và lồng tiếng video.
# Mặc định theo dõi: ~/Movies/AutoDub và xuất ra file: <tên-video>_VN.mp4

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$DIR"

# Đảm bảo PATH có /opt/homebrew/bin và /usr/local/bin khi chạy qua cron hoặc launchd
export PATH="/opt/homebrew/bin:/usr/local/bin:$PATH"

PYTHON="$DIR/.venv/bin/python"

if [ ! -f "$PYTHON" ]; then
    echo "❌ Không tìm thấy môi trường ảo $PYTHON"
    exit 1
fi

exec "$PYTHON" "$DIR/tools/watch_folder.py" "$@"

#!/usr/bin/env bash
# Click đúp để chạy lồng tiếng ngay lập tức và xem trực tiếp tiến độ
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$DIR"

clear
echo "============================================================"
echo "🎬 LỒNG TIẾNG AI - QUÉT VÀ XỬ LÝ VIDEO NGAY LẬP TỨC"
echo "📁 Thư mục nguồn: ~/Movies/AutoDub"
echo "============================================================"
echo ""

./autodub.sh --once

echo ""
echo "============================================================"
echo "✅ Hoàn tất lượt quét! Đã gửi thông báo qua Telegram."
echo "============================================================"
echo ""
read -n 1 -s -r -p "Bấm phím bất kỳ để đóng cửa sổ..."
echo ""

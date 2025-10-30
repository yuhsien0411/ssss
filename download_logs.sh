#!/bin/bash
# 從雲端下載日誌文件的腳本
# 使用方法: ./download_logs.sh [exchange] [ticker] [remote_host] [remote_user]

# 默認參數（請根據你的實際情況修改）
REMOTE_HOST="${3:-your-server-ip}"
REMOTE_USER="${4:-root}"
EXCHANGE="${1:-lighter}"
TICKER="${2:-YZY}"
REMOTE_DIR="~/perp-dex-tools/logs"
LOCAL_DIR="./logs"

# 創建本地 logs 目錄（如果不存在）
mkdir -p "$LOCAL_DIR"

echo "正在從 $REMOTE_USER@$REMOTE_HOST 下載日誌..."
echo "Exchange: $EXCHANGE, Ticker: $TICKER"
echo ""

# 下載活動日誌
LOG_FILE="${EXCHANGE}_${TICKER}_activity.log"
echo "下載活動日誌: $LOG_FILE"
scp "$REMOTE_USER@$REMOTE_HOST:$REMOTE_DIR/$LOG_FILE" "$LOCAL_DIR/" 2>/dev/null

if [ $? -eq 0 ]; then
    echo "✅ 成功下載: $LOG_FILE"
else
    echo "⚠️  未找到或下載失敗: $LOG_FILE"
fi

# 下載訂單 CSV
CSV_FILE="${EXCHANGE}_${TICKER}_orders.csv"
echo "下載訂單記錄: $CSV_FILE"
scp "$REMOTE_USER@$REMOTE_HOST:$REMOTE_DIR/$CSV_FILE" "$LOCAL_DIR/" 2>/dev/null

if [ $? -eq 0 ]; then
    echo "✅ 成功下載: $CSV_FILE"
else
    echo "⚠️  未找到或下載失敗: $CSV_FILE"
fi

echo ""
echo "日誌文件已保存到: $LOCAL_DIR/"
echo ""
echo "如果要下載所有日誌文件，可以執行："
echo "  scp -r $REMOTE_USER@$REMOTE_HOST:$REMOTE_DIR/* $LOCAL_DIR/"


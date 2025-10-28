#!/bin/bash
# 雲端環境快速設置腳本

set -e  # 遇到錯誤立即退出

echo "========================================="
echo "🚀 Lighter Trading Bot - Cloud Setup"
echo "========================================="

# 1. 檢查當前目錄
if [ ! -f "runbot.py" ]; then
    echo "❌ Error: runbot.py not found. Please run this script in ~/ssss directory"
    exit 1
fi

# 2. 更新代碼
echo ""
echo "📥 Updating code from GitHub..."
git fetch origin
git reset --hard origin/main

# 3. 清除緩存
echo ""
echo "🧹 Cleaning cache..."
find . -type d -name "__pycache__" -exec rm -rf {} + 2>/dev/null || true
find . -type f -name "*.pyc" -delete

# 4. 創建虛擬環境（如果不存在）
if [ ! -d "venv" ]; then
    echo ""
    echo "🔧 Creating virtual environment..."
    python3 -m venv venv
fi

# 5. 激活虛擬環境
echo ""
echo "✅ Activating virtual environment..."
source venv/bin/activate

# 6. 安裝依賴
echo ""
echo "📦 Installing dependencies..."
pip install --upgrade pip -q
pip install -r requirements.txt -q

# 7. 驗證安裝
echo ""
echo "🔍 Verifying installation..."
python -c "import lighter; print('✅ Lighter SDK installed')"
python -c "import ccxt; print('✅ CCXT installed')"
python -c "import websockets; print('✅ WebSockets installed')"

echo ""
echo "========================================="
echo "✅ Setup completed successfully!"
echo "========================================="
echo ""
echo "Next steps:"
echo "1. Activate virtual environment:"
echo "   source venv/bin/activate"
echo ""
echo "2. Start the bot:"
echo "   screen -dmS lighter_yzy_short bash -c \"cd ~/ssss && source venv/bin/activate && python runbot.py --exchange lighter --ticker YZY --quantity 100 --direction sell --take-profit 0.2 --max-orders 50 --wait-time 10 --grid-step 0.1 2>&1 | tee logs/lighter_yzy_short_\$(date +%Y%m%d_%H%M%S).log\""
echo ""
echo "3. View logs:"
echo "   screen -r lighter_yzy_short"
echo ""


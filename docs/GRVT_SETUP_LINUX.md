# GRVT 做市機器人 - Linux 本地環境設置指南

## 📋 前置檢查

### 1. 檢查 Python 版本
```bash
python3 --version
# 確保是 Python 3.10 或以上
```

### 2. 進入專案目錄
```bash
cd /mnt/d/MM/perp-dex-tools
```

## 🚀 安裝步驟

### 步驟 1：創建/激活虛擬環境

**如果還沒有虛擬環境：**
```bash
# 確保不在任何虛擬環境中
deactivate 2>/dev/null || true

# 創建虛擬環境
python3 -m venv env
```

**激活虛擬環境：**
```bash
source env/bin/activate
```

### 步驟 2：安裝基礎依賴

```bash
# 確保虛擬環境已激活
pip install -r requirements.txt
```

### 步驟 3：安裝 GRVT 專用依賴

```bash
# 安裝 grvt-pysdk
pip install grvt-pysdk
```

### 步驟 4：驗證安裝

```bash
# 檢查是否安裝成功
python3 -c "from pysdk.grvt_ccxt import GrvtCcxt; print('GRVT SDK 安裝成功!')"
```

## ⚙️ 環境變數配置

### 創建 .env 文件

```bash
# 複製範例文件
cp env_example.txt .env

# 編輯 .env 文件
nano .env
# 或使用 vim
# vim .env
```

### 在 .env 文件中設定 GRVT 配置：

```bash
# GRVT 配置
GRVT_TRADING_ACCOUNT_ID=你的交易帳戶ID
GRVT_PRIVATE_KEY=你的私鑰
GRVT_API_KEY=你的API密鑰
GRVT_ENVIRONMENT=prod  # 或 testnet

# 帳戶名稱（可選）
ACCOUNT_NAME=GRVT_MAIN

# 日誌設定
LOG_TO_CONSOLE=true
LOG_TO_FILE=true
TIMEZONE=Asia/Shanghai
```

**獲取 GRVT API 資訊：**
1. 登入 GRVT 交易所
2. 前往帳戶設定 → API 管理
3. 創建新的 API 密鑰
4. 複製 `Trading Account ID`、`Private Key` 和 `API Key`

## 🧪 測試連接

### 創建測試腳本（可選）

```bash
cat > test_grvt_connection.py << 'EOF'
import os
import dotenv
from pysdk.grvt_ccxt import GrvtCcxt
from pysdk.grvt_ccxt_env import GrvtEnv

# 載入環境變數
dotenv.load_dotenv()

# 獲取配置
trading_account_id = os.getenv('GRVT_TRADING_ACCOUNT_ID')
private_key = os.getenv('GRVT_PRIVATE_KEY')
api_key = os.getenv('GRVT_API_KEY')
environment = os.getenv('GRVT_ENVIRONMENT', 'prod')

if not all([trading_account_id, private_key, api_key]):
    print("❌ 請檢查 .env 文件中的 GRVT 配置")
    exit(1)

# 初始化客戶端
env_map = {
    'prod': GrvtEnv.PROD,
    'testnet': GrvtEnv.TESTNET,
    'staging': GrvtEnv.STAGING,
    'dev': GrvtEnv.DEV
}
env = env_map.get(environment.lower(), GrvtEnv.PROD)

parameters = {
    'trading_account_id': trading_account_id,
    'private_key': private_key,
    'api_key': api_key
}

try:
    client = GrvtCcxt(env=env, parameters=parameters)
    markets = client.load_markets()
    print(f"✅ GRVT 連接成功！")
    print(f"📊 可用交易對數量: {len(markets)}")
    print(f"📋 前10個交易對: {list(markets.keys())[:10]}")
except Exception as e:
    print(f"❌ 連接失敗: {e}")
EOF

# 執行測試
python3 test_grvt_connection.py
```

## 🎯 運行做市機器人

### 基本命令

```bash
# 確保虛擬環境已激活
source env/bin/activate

# BTC 做市範例
python3 runbot.py --exchange grvt --ticker BTC --quantity 0.05 --take-profit 0.02 --max-orders 40 --wait-time 450

# ETH 做市範例
python3 runbot.py --exchange grvt --ticker ETH --quantity 0.1 --take-profit 0.02 --max-orders 40 --wait-time 450

# ADA 做市範例（較小的等待時間）
python3 runbot.py --exchange grvt --ticker ADA --quantity 50 --take-profit 0.02 --max-orders 40 --wait-time 30
```

### 進階參數使用

**帶網格步長控制：**
```bash
python3 runbot.py --exchange grvt --ticker BTC --quantity 0.05 --take-profit 0.02 --max-orders 40 --wait-time 450 --grid-step 0.5
```

**帶停止價格控制：**
```bash
python3 runbot.py --exchange grvt --ticker BTC --quantity 0.05 --take-profit 0.02 --max-orders 40 --wait-time 450 --stop-price 55000
```

**做空方向：**
```bash
python3 runbot.py --exchange grvt --ticker BTC --quantity 0.05 --take-profit 0.02 --max-orders 40 --wait-time 450 --direction sell
```

## 📊 監控日誌

### 查看即時日誌
```bash
# 查看活動日誌（根據你的 ACCOUNT_NAME）
tail -f logs/grvt_*.log

# 查看訂單日誌
tail -f logs/grvt_*.csv
```

### 查看特定帳戶的日誌
```bash
# 如果你的 ACCOUNT_NAME 是 GRVT_MAIN
tail -f logs/grvt_GRVT_MAIN_activity.log
```

## 🔄 對沖模式（選用）

```bash
# ETH 對沖模式
python3 hedge_mode.py --exchange grvt --ticker ETH --size 0.1 --iter 20

# BTC 對沖模式
python3 hedge_mode.py --exchange grvt --ticker BTC --size 0.05 --iter 20
```

## 🛠️ 常見問題排查

### 問題 1: Python 版本不足
```bash
# 檢查版本
python3 --version

# 如果需要升級，使用 pyenv 或系統包管理器
# Ubuntu/Debian:
sudo apt update && sudo apt install python3.10 python3.10-venv

# 使用特定版本創建虛擬環境
python3.10 -m venv env
```

### 問題 2: grvt-pysdk 安裝失敗
```bash
# 更新 pip
pip install --upgrade pip

# 重新安裝
pip install grvt-pysdk --force-reinstall
```

### 問題 3: 模組找不到
```bash
# 確保虛擬環境已激活
source env/bin/activate

# 重新安裝依賴
pip install -r requirements.txt
pip install grvt-pysdk
```

### 問題 4: 權限問題
```bash
# 確保腳本有執行權限
chmod +x runbot.py
chmod +x hedge_mode.py
```

## 📝 快速啟動腳本

創建一個快速啟動腳本：

```bash
cat > start_grvt_btc.sh << 'EOF'
#!/bin/bash
cd /mnt/d/MM/perp-dex-tools
source env/bin/activate
python3 runbot.py --exchange grvt --ticker BTC --quantity 0.05 --take-profit 0.02 --max-orders 40 --wait-time 450
EOF

chmod +x start_grvt_btc.sh

# 使用方式
./start_grvt_btc.sh
```

## 🔐 安全建議

1. **保護 .env 文件**：
```bash
# 設定檔案權限，僅擁有者可讀寫
chmod 600 .env
```

2. **不要將 .env 提交到 Git**：
```bash
# 確認 .gitignore 包含 .env
echo ".env" >> .gitignore
```

3. **使用不同帳戶時使用不同的 .env 文件**：
```bash
# 創建多個配置文件
cp .env account1.env
cp .env account2.env

# 使用時指定文件
python3 runbot.py --env-file account1.env --exchange grvt --ticker BTC ...
```

## 🎓 推薦配置

**保守策略（適合長期運行）：**
- `--quantity`: 40-60
- `--wait-time`: 450-650
- `--max-orders`: 30-40

**積極策略（適合短期衝量）：**
- `--quantity`: 20-40
- `--wait-time`: 30-60
- `--max-orders`: 50-80

## 📞 需要幫助？

如果遇到問題，請檢查：
1. Python 版本是否 ≥ 3.10
2. 虛擬環境是否已激活
3. 所有依賴是否已安裝
4. .env 文件配置是否正確
5. GRVT API 密鑰是否有效


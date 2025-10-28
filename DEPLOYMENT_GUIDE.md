# 交易機器人部署指南

## 目錄
1. [從零開始 - 伺服器設置](#從零開始---伺服器設置)
2. [專案部署](#專案部署)
3. [快速開始](#快速開始)
4. [常用命令](#常用命令)
5. [參數說明](#參數說明)
6. [日誌文件](#日誌文件)
7. [系統服務設置](#系統服務設置-推薦)
8. [故障排除](#故障排除)

---

## 從零開始 - 伺服器設置

### 步驟 1: 創建 Digital Ocean 雲端伺服器

#### 1.1 註冊並登入 Digital Ocean
- 訪問 [Digital Ocean](https://www.digitalocean.com/)
- 註冊帳號並完成身份驗證

#### 1.2 創建 Droplet（虛擬機）
```
1. 點擊右上角 "Create" -> "Droplets"
2. 選擇配置：
   - 鏡像: Ubuntu 22.04 (LTS) x64
   - 方案: Basic
   - CPU 選項: Regular（推薦：2 GB RAM / 1 CPU 或以上）
   - 數據中心區域: 選擇離你最近的（如 Singapore 或 San Francisco）
   - 認證方式: SSH keys（推薦）或 Password
   - 主機名稱: trading-bot-server（或自定義）
3. 點擊 "Create Droplet"
4. 等待 1-2 分鐘，獲取伺服器 IP 地址
```

#### 1.3 首次連接伺服器
```bash
# Windows 用戶使用 PowerShell 或 Git Bash
# Mac/Linux 用戶直接使用終端
ssh root@YOUR_SERVER_IP

# 首次連接會提示確認指紋，輸入 yes
# 如果使用密碼認證，輸入創建時設置的密碼
```

### 步驟 2: 系統初始化

#### 2.1 更新系統套件
```bash
# 更新套件列表
apt update

# 升級所有已安裝的套件
apt upgrade -y

# 安裝基本工具和 Python 虛擬環境支援
apt install -y curl wget git vim htop screen python3-venv python3-pip
```

#### 2.2 驗證 Python 版本
```bash
# 檢查 Python 版本
python3 --version

# 應該顯示 3.10 或更高版本（Python 3.10-3.13 都可以）
# Ubuntu 22.04 默認是 Python 3.10
# Ubuntu 24.04 默認是 Python 3.12+

# 驗證 pip 已安裝
pip3 --version

# 如果版本低於 3.10，才需要升級（一般不需要）：
# apt install -y software-properties-common
# add-apt-repository -y ppa:deadsnakes/ppa
# apt update
# apt install -y python3.10 python3.10-venv python3.10-dev
```

#### 2.3 配置 Git（用於從 GitHub 拉取代碼）
```bash
# 配置 Git 用戶信息（本地使用）
git config --global user.name "Your Name"
git config --global user.email "your.email@example.com"

# 生成 SSH 密鑰（如果使用 SSH 克隆，可選）
ssh-keygen -t ed25519 -C "your.email@example.com"
# 一路按 Enter 使用默認設置

# 顯示公鑰並添加到 GitHub（如果需要）
cat ~/.ssh/id_ed25519.pub
# 複製輸出內容，然後到 GitHub Settings -> SSH and GPG keys -> New SSH key 添加
```

### 步驟 3: 防火牆和安全設置（可選但推薦）

```bash
# 安裝 UFW 防火牆
apt install -y ufw

# 允許 SSH 連接（重要！否則會被鎖在外面）
ufw allow OpenSSH

# 啟用防火牆
ufw enable

# 檢查狀態
ufw status

# 如果需要允許其他端口（例如 HTTP/HTTPS）
# ufw allow 80/tcp
# ufw allow 443/tcp
```

---

## 專案部署

### 步驟 4: 克隆專案

#### 4.1 創建工作目錄並克隆專案
```bash
# 進入 home 目錄
cd ~

# 克隆專案（使用 HTTPS - 推薦）
git clone https://github.com/your-username/perp-dex-tools.git

# 或使用 SSH（需先配置 SSH key）
# git clone git@github.com:your-username/perp-dex-tools.git

# 進入專案目錄
cd perp-dex-tools

# 檢查專案結構
ls -la
```

### 步驟 5: 設置虛擬環境

#### 5.1 創建虛擬環境
```bash
# 確保在專案根目錄
pwd  # 應該顯示 /root/perp-dex-tools

# 創建虛擬環境（名稱為 env）
python3 -m venv env

# 驗證虛擬環境已創建
ls -la env/
```

#### 5.2 激活虛擬環境並安裝依賴
```bash
# 激活虛擬環境（每次使用機器人前都要執行）
source env/bin/activate

# 提示符應該變成 (env) root@...

# 升級 pip
pip install --upgrade pip

# 安裝基礎依賴
pip install -r requirements.txt

# 根據需要安裝額外的交易所依賴
# 如果使用 GRVT:
pip install grvt-pysdk

# 如果使用 Apex:
pip install -r apex_requirements.txt

# 如果使用 Paradex（需要單獨環境）:
# deactivate  # 先退出當前環境
# python3 -m venv para_env
# source para_env/bin/activate
# pip install -r para_requirements.txt
```

#### 5.3 驗證安裝
```bash
# 確保虛擬環境已激活
source env/bin/activate

# 測試關鍵依賴
python3 << 'EOF'
import sys
try:
    import ccxt
    print('✅ CCXT installed')
except ImportError:
    print('❌ CCXT not installed')
    
try:
    import websockets
    print('✅ WebSockets installed')
except ImportError:
    print('❌ WebSockets not installed')
    
try:
    from dotenv import load_dotenv
    print('✅ python-dotenv installed')
except ImportError:
    print('❌ python-dotenv not installed')
EOF

# 檢查腳本是否可執行
python3 runbot.py --help
```

### 步驟 6: 配置環境變數

#### 6.1 創建 .env 文件
```bash
# 複製示例文件
cp env_example.txt .env

# 編輯 .env 文件
nano .env
# 或使用 vim:
# vim .env
```

#### 6.2 配置必要的 API 密鑰
在 .env 文件中填入你的 API 資訊，根據使用的交易所配置相應的密鑰：

**示例配置（Backpack）:**
```bash
ACCOUNT_NAME=MAIN
BACKPACK_PUBLIC_KEY=your_actual_api_key_here
BACKPACK_SECRET_KEY=your_actual_secret_key_here
```

**示例配置（Aster）:**
```bash
ACCOUNT_NAME=MAIN
ASTER_API_KEY=your_aster_api_key
ASTER_SECRET_KEY=your_aster_secret_key
```

**示例配置（GRVT）:**
```bash
ACCOUNT_NAME=MAIN
GRVT_TRADING_ACCOUNT_ID=your_trading_account_id
GRVT_PRIVATE_KEY=your_private_key
GRVT_API_KEY=your_api_key
GRVT_ENVIRONMENT=prod
```

**示例配置（Lighter）:**
```bash
ACCOUNT_NAME=MAIN
API_KEY_PRIVATE_KEY=your_api_key_private_key
LIGHTER_ACCOUNT_INDEX=0
LIGHTER_API_KEY_INDEX=0
# 保證金模式設置（可選）
LIGHTER_MARGIN_MODE=isolated    # isolated (逐倉) 或 cross (全倉)
LIGHTER_LEVERAGE=10              # 槓桿倍數
```

保存文件：
- **nano**: 按 `Ctrl+X`, 然後 `Y`, 然後 `Enter`
- **vim**: 按 `ESC`, 輸入 `:wq`, 按 `Enter`

#### 6.3 保護 .env 文件
```bash
# 設置文件權限（只有 root 可以讀寫）
chmod 600 .env

# 驗證權限
ls -la .env
# 應該顯示 -rw------- 1 root root
```

### 步驟 7: 創建日誌目錄
```bash
# 創建日誌目錄（如果不存在）
mkdir -p logs

# 設置權限
chmod 755 logs

# 驗證
ls -la | grep logs
```

### 步驟 8: 測試運行

#### 8.1 快速測試
```bash
# 確保虛擬環境已激活
source venv/bin/activate

# 運行幫助命令
python3 runbot.py --help

# 小規模測試（使用較小的參數）
# ⚠️ 注意：這會開始實際交易！確保參數正確！
python3 runbot.py --exchange aster --ticker SOL --quantity 0.1 --take-profit 0.5 --max-orders 5 --wait-time 60

# 觀察幾分鐘，確認運行正常後，按 Ctrl+C 停止
```

#### 8.2 檢查日誌
```bash
# 查看活動日誌
tail -f logs/aster_SOL_activity.log

# 按 Ctrl+C 停止查看

# 查看訂單記錄
cat logs/aster_SOL_orders.csv
```

---

## 快速開始

### 環境已配置好後，使用以下命令啟動

#### 1. 連接到伺服器
```bash
ssh root@YOUR_SERVER_IP
```

#### 2. 進入專案目錄
```bash
cd ~/perp-dex-tools
```

#### 3. 從 GitHub 拉取最新代碼（可選）
```bash
# 拉取最新代碼
git pull origin main

# 如果有衝突，可以強制更新
git fetch origin
git reset --hard origin/main

# 清除 Python 緩存
find . -type d -name "__pycache__" -exec rm -rf {} + 2>/dev/null || true
find . -type f -name "*.pyc" -delete
```

#### 4. 激活虛擬環境並啟動機器人
```bash
# 激活虛擬環境（重要！）
source env/bin/activate

# Lighter 交易示例（YZY）
python3 runbot.py --exchange lighter --ticker YZY --quantity 100 --take-profit 0.5 --max-orders 50 --wait-time 10 --grid-step 0.1

# Aster 交易示例（SOL）
python3 runbot.py --exchange aster --ticker SOL --quantity 0.2 --take-profit 0.5 --max-orders 20 --wait-time 30 --grid-step 0.05

# GRVT 交易示例（BTC）
python3 runbot.py --exchange grvt --ticker BTC --quantity 0.05 --take-profit 0.5 --max-orders 40 --wait-time 450

# 使用 screen 在後台運行（推薦）
screen -S trading_bot
# 確保在 screen 中激活環境
source env/bin/activate
python3 runbot.py --exchange lighter --ticker YZY --quantity 100 --take-profit 0.5 --max-orders 50 --wait-time 10 --grid-step 0.1
# 按 Ctrl+A 然後按 D 分離會話
```

---

## 常用命令

### 查看運行狀態
```bash
# 查看 screen 會話
screen -ls

# 連接到機器人會話
screen -r trading_bot

# 查看機器人進程
ps aux | grep python

# 查看實時日誌
tail -f logs/aster_SOL_activity.log

# 查看最近 100 行日誌
tail -n 100 logs/aster_SOL_activity.log
```

### 停止機器人
```bash
# 方法 1: 連接到會話後按 Ctrl+C
screen -r trading_bot
# 按 Ctrl+C 停止，然後輸入 exit

# 方法 2: 強制終止會話
screen -X -S trading_bot quit

# 方法 3: 殺死進程
ps aux | grep runbot.py
kill -9 <PID>
```

### 更新代碼並重啟
```bash
# 1. 停止機器人
screen -X -S trading_bot quit

# 2. 進入專案目錄
cd ~/perp-dex-tools

# 3. 拉取最新代碼
git pull origin main

# 4. 清除緩存
find . -type d -name "__pycache__" -exec rm -rf {} + 2>/dev/null || true

# 5. 激活環境並重新安裝依賴（如果 requirements.txt 有更新）
source env/bin/activate
pip install -r requirements.txt

# 6. 重新啟動
screen -S trading_bot
source env/bin/activate
python3 runbot.py [你的參數...]
# 按 Ctrl+A 然後按 D 分離會話
```

### 修改參數
```bash
# 1. 連接到會話
screen -r trading_bot

# 2. 按 Ctrl+C 停止機器人

# 3. 確保激活虛擬環境
source env/bin/activate

# 4. 用新參數重新啟動
python3 runbot.py --exchange aster --ticker SOL --quantity 0.3 --take-profit 1.0 --max-orders 25 --wait-time 45 --grid-step 0.1

# 5. 按 Ctrl+A 然後按 D 分離會話
```

---

## 參數說明

| 參數 | 說明 | 範例 |
|------|------|------|
| `--exchange` | 交易所 | aster, edgex, backpack, paradex, bybit, grvt, lighter, extended, apex |
| `--ticker` | 交易對 | SOL, ETH, BTC, ADA, YZY |
| `--quantity` | 每筆訂單數量 | 0.2, 0.1, 50, 100 |
| `--take-profit` | 止盈金額（USDT） | 0.5, 1.0, 0.02 |
| `--max-orders` | 最大活躍訂單數 | 20, 40 |
| `--wait-time` | 訂單間等待時間(秒) | 30, 450 |
| `--grid-step` | 網格步長(%) | 0.05, 0.1 |
| `--direction` | 交易方向 | buy, sell |
| `--stop-price` | 停止價格 | 5500 |
| `--pause-price` | 暫停價格 | 5000 |
| `--market-type` | 市場類型 | SPOT (現貨), PERP (永續合約) |
| `--boost` | Boost 模式（快速成交） | 無需值，加上即啟用 |
| `--iter` | 對沖模式循環次數 | 20 |
| `--env-file` | 環境變數文件 | .env, account_1.env |

---

## 日誌文件

日誌文件位於 `logs/` 目錄下：

- **活動日誌**: `logs/<交易所>_<幣種>_activity.log`
  - 記錄所有交易活動和狀態
  - 包含錯誤信息和警告

- **訂單記錄**: `logs/<交易所>_<幣種>_orders.csv`
  - CSV 格式的訂單詳細記錄
  - 可用 Excel 或類似工具打開分析

**查看日誌的常用命令:**
```bash
# 實時查看活動日誌
tail -f logs/aster_SOL_activity.log

# 查看最近 100 行
tail -n 100 logs/aster_SOL_activity.log

# 搜索特定內容（如錯誤）
grep "ERROR" logs/aster_SOL_activity.log

# 查看訂單 CSV
cat logs/aster_SOL_orders.csv
```

---

## 系統服務設置 (推薦)

使用 systemd 服務可以讓機器人開機自動啟動，並在崩潰時自動重啟。

### 創建服務文件
```bash
sudo nano /etc/systemd/system/trading-bot.service
```

### 服務配置示例
```ini
[Unit]
Description=Trading Bot Service
After=network.target

[Service]
Type=simple
User=root
WorkingDirectory=/root/perp-dex-tools
Environment="PATH=/root/perp-dex-tools/env/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"
ExecStart=/root/perp-dex-tools/env/bin/python3 /root/perp-dex-tools/runbot.py --exchange lighter --ticker YZY --quantity 100 --take-profit 0.5 --max-orders 50 --wait-time 10 --grid-step 0.1
Restart=always
RestartSec=10
StandardOutput=append:/root/perp-dex-tools/logs/systemd_output.log
StandardError=append:/root/perp-dex-tools/logs/systemd_error.log

[Install]
WantedBy=multi-user.target
```

### 啟用和管理服務
```bash
# 重新加載 systemd 配置
sudo systemctl daemon-reload

# 啟用服務（開機自動啟動）
sudo systemctl enable trading-bot.service

# 啟動服務
sudo systemctl start trading-bot.service

# 查看服務狀態
sudo systemctl status trading-bot.service

# 停止服務
sudo systemctl stop trading-bot.service

# 重啟服務
sudo systemctl restart trading-bot.service

# 查看服務日誌
sudo journalctl -u trading-bot.service -f
```

---

## 故障排除

### 機器人停止運行
```bash
# 檢查進程是否還在運行
ps aux | grep python

# 檢查 screen 會話
screen -ls

# 檢查日誌最後幾行，尋找錯誤
tail -n 50 logs/aster_SOL_activity.log

# 如果使用 systemd
sudo systemctl status trading-bot.service

# 重新啟動（記得激活環境）
screen -S trading_bot
source env/bin/activate
python3 runbot.py [參數...]
```

### 常見錯誤 1：ModuleNotFoundError
```bash
# 錯誤：ModuleNotFoundError: No module named 'dotenv'
# 原因：未激活虛擬環境

# 解決方案：
cd ~/perp-dex-tools
source env/bin/activate
python3 runbot.py [參數...]
```

### 常見錯誤 2：API 認證失敗
```bash
# 錯誤：Authentication failed 或 Invalid API key
# 原因：.env 文件配置錯誤

# 解決方案：
nano .env
# 檢查並更正 API 密鑰
# 確保沒有多餘的空格或引號
# 保存後重新啟動機器人
```

### 常見錯誤 3：網絡連接問題
```bash
# 錯誤：Connection timeout 或 Network unreachable
# 原因：網絡問題或交易所 API 故障

# 解決方案：
# 1. 檢查網絡連接
ping 8.8.8.8

# 2. 檢查 DNS
nslookup google.com

# 3. 等待幾分鐘後重試
# 4. 查看交易所官方狀態頁面
```

### 無法連接 screen 會話
```bash
# 錯誤：There is a screen on... (Attached)
# 原因：會話已被其他終端連接

# 解決方案 1：強制分離並重新連接
screen -D -r trading_bot

# 解決方案 2：終止會話重新創建
screen -X -S trading_bot quit
screen -S trading_bot_new
```

### 虛擬環境創建失敗
```bash
# 錯誤：The virtual environment was not created successfully because ensurepip is not available
# 原因：缺少 python3-venv 套件

# 解決方案：安裝 python3-venv
apt update
apt install -y python3-venv python3-pip

# 然後重新創建虛擬環境
cd ~/perp-dex-tools
rm -rf env  # 如果已經有部分創建的環境
python3 -m venv env
source env/bin/activate
pip install --upgrade pip
pip install -r requirements.txt
```

### 虛擬環境損壞
```bash
# 症狀：無法激活環境或缺少包

# 解決方案：重建虛擬環境
cd ~/perp-dex-tools
deactivate  # 如果在虛擬環境中
rm -rf env  # 刪除舊環境
python3 -m venv env  # 創建新環境
source env/bin/activate
pip install --upgrade pip
pip install -r requirements.txt
```

### Git 拉取代碼衝突
```bash
# 錯誤：error: Your local changes to the following files would be overwritten

# 解決方案 1：保存本地更改
git stash
git pull origin main
git stash pop

# 解決方案 2：放棄本地更改（謹慎使用）
git fetch origin
git reset --hard origin/main
```

### 磁盤空間不足
```bash
# 檢查磁盤使用情況
df -h

# 查看日誌目錄大小
du -sh logs/

# 清理舊日誌（保留最近 7 天）
find logs/ -name "*.log" -mtime +7 -delete

# 清理 Python 緩存
find . -type d -name "__pycache__" -exec rm -rf {} + 2>/dev/null || true
find . -type f -name "*.pyc" -delete
```

### 檢查系統資源
```bash
# 查看系統負載
top
# 按 q 退出

# 查看磁盤使用
df -h

# 查看記憶體使用
free -h

# 查看進程資源使用
ps aux --sort=-%mem | head -10  # 記憶體使用最多的進程
ps aux --sort=-%cpu | head -10  # CPU 使用最多的進程
```

---

## 本地到雲端工作流程

### 本地開發
由於 Lighter SDK 只能在 Mac/Linux 上運行，你需要在本地進行開發和測試：

```bash
# 在本地（Windows 使用 WSL 或虛擬機）
cd /path/to/perp-dex-tools

# 修改代碼
# ...

# 提交到 Git
git add .
git commit -m "描述你的更改"
git push origin main
```

### 雲端部署
在 Digital Ocean 伺服器上拉取更新：

```bash
# SSH 連接到伺服器
ssh root@YOUR_SERVER_IP

# 進入專案目錄
cd ~/perp-dex-tools

# 停止機器人
screen -X -S trading_bot quit

# 拉取最新代碼
git pull origin main

# 清除緩存
find . -type d -name "__pycache__" -exec rm -rf {} + 2>/dev/null || true

# 重新啟動
screen -S trading_bot
source env/bin/activate
python3 runbot.py [你的參數...]
# 按 Ctrl+A 然後按 D 分離會話
```

### 自動化部署腳本
創建一個快速部署腳本 `deploy.sh`:

```bash
#!/bin/bash
# 保存為 ~/perp-dex-tools/deploy.sh

set -e

echo "🚀 開始部署..."

# 停止機器人
echo "⏹️  停止機器人..."
screen -X -S trading_bot quit 2>/dev/null || true

# 拉取代碼
echo "📥 拉取最新代碼..."
git fetch origin
git reset --hard origin/main

# 清除緩存
echo "🧹 清除緩存..."
find . -type d -name "__pycache__" -exec rm -rf {} + 2>/dev/null || true
find . -type f -name "*.pyc" -delete

# 更新依賴（如果需要）
echo "📦 檢查依賴..."
source env/bin/activate
pip install -r requirements.txt --upgrade -q

# 重新啟動
echo "▶️  重新啟動機器人..."
screen -dmS trading_bot bash -c "cd ~/perp-dex-tools && source env/bin/activate && python3 runbot.py --exchange lighter --ticker YZY --quantity 100 --take-profit 0.5 --max-orders 50 --wait-time 10 --grid-step 0.1"

echo "✅ 部署完成！"
echo "查看狀態: screen -r trading_bot"
```

使用部署腳本：
```bash
# 賦予執行權限
chmod +x deploy.sh

# 執行部署
./deploy.sh
```

---

## 重要提醒

### 虛擬環境
- **每次啟動機器人前都必須激活虛擬環境**：`source env/bin/activate`
- 在 screen 會話中也要激活環境
- systemd 服務會自動使用虛擬環境的 Python

### 市場類型選擇
- **SPOT (現貨)**: 適合長期持有，無槓桿，風險較低
- **PERP (永續合約)**: 適合短期交易，有槓桿，風險較高
- **BYBIT 特殊處理**: PERP 模式會自動使用 "linear" 類別

### 安全提醒
- 定期備份日誌文件和 .env 配置
- 監控機器人運行狀態和資金情況
- 設置適當的風險控制參數（`max-orders`, `quantity`, `stop-price`）
- 確保 API 密鑰安全，使用 `chmod 600 .env` 保護配置文件
- 建議使用 systemd 服務確保長期穩定運行
- 現貨交易風險較低，永續合約需要更謹慎的風險管理
- **不要在公共場所展示你的 .env 文件內容**

### 常見陷阱
1. **忘記激活虛擬環境** → `ModuleNotFoundError`
2. **API 密鑰配置錯誤** → 認證失敗
3. **參數設置過於激進** → 快速虧損
4. **不使用 screen/systemd** → SSH 斷開後機器人停止
5. **不監控日誌** → 無法及時發現問題
6. **磁盤空間不足** → 日誌寫入失敗

### 監控建議
```bash
# 創建監控腳本 monitor.sh
cat > ~/monitor.sh << 'EOF'
#!/bin/bash
echo "========================================="
echo "交易機器人監控 - $(date)"
echo "========================================="
echo ""

# 檢查進程
echo "📊 進程狀態:"
ps aux | grep "runbot.py" | grep -v grep || echo "❌ 機器人未運行"
echo ""

# 檢查 screen 會話
echo "🖥️  Screen 會話:"
screen -ls | grep trading_bot || echo "❌ 沒有 trading_bot 會話"
echo ""

# 檢查最近的日誌
echo "📝 最近日誌 (最後 5 行):"
tail -n 5 logs/aster_SOL_activity.log 2>/dev/null || echo "❌ 日誌文件不存在"
echo ""

# 檢查磁盤空間
echo "💾 磁盤使用:"
df -h | grep -E "Filesystem|/$"
echo ""

# 檢查記憶體
echo "🧠 記憶體使用:"
free -h
echo ""
EOF

chmod +x ~/monitor.sh

# 運行監控
~/monitor.sh
```

---

## Lighter 交易所保證金模式設置

### 什麼是逐倉模式和全倉模式？

- **逐倉模式 (Isolated Margin)**：每個交易對使用獨立的保證金，風險隔離，一個交易對爆倉不會影響其他交易對
- **全倉模式 (Cross Margin)**：所有交易對共享賬戶全部保證金，風險較高但資金利用率更高

### 如何設置逐倉模式

#### 方法 1：通過環境變數設置（推薦）

在 `.env` 文件中添加以下配置：

```bash
# Lighter 保證金模式設置
LIGHTER_MARGIN_MODE=isolated    # isolated (逐倉) 或 cross (全倉)
LIGHTER_LEVERAGE=10              # 槓桿倍數 (1-20)
```

**配置說明：**
- `LIGHTER_MARGIN_MODE`：
  - `isolated`：逐倉模式（推薦，風險更低）
  - `cross`：全倉模式（風險較高）
  - 不設置：使用交易所默認設置

- `LIGHTER_LEVERAGE`：
  - 槓桿倍數，範圍通常是 1-20
  - 默認值：10
  - 逐倉模式下建議使用較低槓桿（5-10x）

**完整配置示例：**
```bash
# Lighter Configuration
API_KEY_PRIVATE_KEY=your_api_key_private_key
LIGHTER_ACCOUNT_INDEX=0
LIGHTER_API_KEY_INDEX=0

# 保證金模式 - 逐倉，10倍槓桿（推薦）
LIGHTER_MARGIN_MODE=isolated
LIGHTER_LEVERAGE=10
```

保存後，機器人啟動時會自動設置保證金模式。

#### 方法 2：在雲端伺服器上修改

```bash
# SSH 連接到伺服器
ssh root@YOUR_SERVER_IP

# 編輯 .env 文件
cd ~/perp-dex-tools
nano .env

# 添加或修改以下行
LIGHTER_MARGIN_MODE=isolated
LIGHTER_LEVERAGE=10

# 保存（Ctrl+X, Y, Enter）

# 重啟機器人以應用設置
screen -r trading_bot
# Ctrl+C 停止
# 重新啟動
source env/bin/activate
python3 runbot.py --exchange lighter --ticker YZY --quantity 100 --take-profit 0.5 --max-orders 50 --wait-time 20 --grid-step 0.25
```

### 驗證保證金模式設置

啟動機器人後，查看日誌中的確認信息：

```bash
# 查看日誌
tail -f ~/perp-dex-tools/logs/lighter_YZY_activity.log

# 應該看到類似的輸出：
# Setting margin mode to ISOLATED (逐倉) with 10x leverage for market 70...
# ✅ Margin mode set to ISOLATED (逐倉) with 10x leverage successfully!
```

### 保證金模式對比

| 特性 | 逐倉模式 (Isolated) | 全倉模式 (Cross) |
|------|-------------------|-----------------|
| 風險隔離 | ✅ 每個交易對獨立 | ❌ 所有交易對共享 |
| 爆倉影響 | 只影響單個交易對 | 影響整個賬戶 |
| 保證金利用率 | 較低 | 較高 |
| 適合場景 | 多交易對同時交易 | 單一交易對交易 |
| 風險等級 | 🟢 較低 | 🔴 較高 |
| **推薦度** | ⭐⭐⭐⭐⭐ 強烈推薦 | ⭐⭐ 謹慎使用 |

### 重要提醒

1. **首次使用建議使用逐倉模式**：風險更加可控
2. **合理設置槓桿**：建議 5-10x，不要使用過高槓桿
3. **控制倉位大小**：`max-orders` 和 `quantity` 不要設置過大
4. **定期監控**：即使使用逐倉模式，也要定期檢查賬戶狀態

### 故障排除

**問題：設置保證金模式失敗**
```bash
# 錯誤信息：Failed to set margin mode
# 可能原因：
1. 賬戶有未平倉的訂單
2. API 權限不足
3. 交易所暫時不可用

# 解決方案：
# 1. 先取消所有訂單
# 2. 檢查 API 權限
# 3. 稍後重試
```

**問題：不確定當前使用的模式**
```bash
# 查看啟動日誌
grep -i "margin mode" ~/perp-dex-tools/logs/lighter_YZY_activity.log

# 或聯繫交易所客服確認
```

---

## 進階配置

### 多賬號管理
如果你有多個交易所賬號，可以創建多個 .env 文件：

```bash
# 創建多個配置文件
cp .env account_1.env
cp .env account_2.env

# 編輯每個文件，設置不同的 API 密鑰
nano account_1.env  # 配置賬號 1
nano account_2.env  # 配置賬號 2

# 使用不同配置啟動
python3 runbot.py --env-file account_1.env --exchange aster --ticker SOL ...
python3 runbot.py --env-file account_2.env --exchange backpack --ticker ETH ...
```

### 多幣種同時運行
```bash
# 為每個幣種創建單獨的 screen 會話
screen -S lighter_yzy
source env/bin/activate
python3 runbot.py --exchange lighter --ticker YZY --quantity 100 --take-profit 0.5 --max-orders 50 --wait-time 10
# Ctrl+A D 分離

screen -S grvt_btc
source env/bin/activate
python3 runbot.py --exchange grvt --ticker BTC --quantity 0.05 --take-profit 0.5 --max-orders 40 --wait-time 450
# Ctrl+A D 分離

screen -S aster_sol
source env/bin/activate
python3 runbot.py --exchange aster --ticker SOL --quantity 0.2 --take-profit 0.5 --max-orders 20 --wait-time 30
# Ctrl+A D 分離

# 查看所有會話
screen -ls

# 連接到特定會話
screen -r lighter_yzy
```

---

## 聯絡資訊

如有問題：
1. 先查看日誌文件 `logs/` 目錄
2. 參考故障排除章節
3. 檢查 GitHub Issues 是否有類似問題
4. 聯繫技術支援

---

## 附錄：常用命令速查表

```bash
# 連接伺服器
ssh root@YOUR_SERVER_IP

# 專案目錄
cd ~/perp-dex-tools

# 更新代碼
git pull origin main

# 激活環境
source env/bin/activate

# 啟動機器人（screen）
screen -S trading_bot
source env/bin/activate
python3 runbot.py [參數...]
# Ctrl+A D 分離

# 查看 screen 會話
screen -ls

# 連接會話
screen -r trading_bot

# 停止會話
screen -X -S trading_bot quit

# 查看日誌
tail -f logs/aster_SOL_activity.log

# 監控系統
htop  # 或 top

# 檢查磁盤
df -h

# 檢查記憶體
free -h
```

---

*最後更新: 2024-10-28*

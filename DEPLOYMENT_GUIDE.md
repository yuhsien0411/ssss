# 交易機器人部署指南

## 快速開始

### 1. 連接到伺服器
```bash
ssh root@165.232.169.169
```

### 2. 進入專案目錄
```bash
cd ~/perp-dex-tools
```

### 3. 激活虛擬環境並啟動機器人
```bash
# 激活虛擬環境（重要！）
source env/bin/activate

# 永續合約交易（默認）
python3 runbot.py --exchange aster --ticker SOL --quantity 0.2 --take-profit-ticks 8 --max-orders 20 --wait-time 30 --grid-step 0.05

# 現貨交易
python3 runbot.py --exchange bybit --ticker ETH --quantity 0.1 --take-profit 0.5 --max-orders 20 --wait-time 30 --grid-step 0.1 --market-type SPOT

# 永續合約交易（BYBIT 使用 linear）
python3 runbot.py --exchange bybit --ticker ETH --quantity 0.1 --take-profit 0.5 --max-orders 20 --wait-time 30 --grid-step 0.1 --market-type PERP

# 使用 screen 在後台運行
screen -S trading_bot
# 確保在 screen 中激活環境
source env/bin/activate
python3 runbot.py --exchange bybit --ticker ETH --quantity 0.1 --take-profit 0.5 --max-orders 20 --wait-time 30 --grid-step 0.1 --market-type SPOT
# 按 Ctrl+A 然後按 D 分離會話
```

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
```

### 停止機器人
```bash
# 連接到會話後按 Ctrl+C
screen -r trading_bot
# 按 Ctrl+C 停止，然後輸入 exit

# 或強制終止會話
screen -X -S trading_bot quit
```

### 修改參數
```bash
# 1. 連接到會話
screen -r trading_bot

# 2. 按 Ctrl+C 停止機器人

# 3. 確保激活虛擬環境
source env/bin/activate

# 4. 用新參數重新啟動
python3 runbot.py --exchange aster --ticker SOL --quantity 0.3 --take-profit-ticks 10 --max-orders 25 --wait-time 45 --grid-step 0.1

# 5. 按 Ctrl+A 然後按 D 分離會話
```

## 參數說明

| 參數 | 說明 | 範例 |
|------|------|------|
| `--exchange` | 交易所 | aster, edgex, backpack, paradex, bybit |
| `--ticker` | 交易對 | SOL, ETH, BTC |
| `--quantity` | 每筆訂單數量 | 0.2 |
| `--take-profit-ticks` | 止盈 tick 數 | 8 (約 +0.08 USDT) |
| `--max-orders` | 最大活躍訂單數 | 20 |
| `--wait-time` | 訂單間等待時間(秒) | 30 |
| `--grid-step` | 網格步長(%) | 0.05 |
| `--direction` | 交易方向 | buy, sell |
| `--stop-price` | 停止價格 | 5500 |
| `--pause-price` | 暫停價格 | 5000 |
| `--market-type` | 市場類型 | SPOT (現貨), PERP (永續合約) |

## 日誌文件

- **活動日誌**: `logs/aster_SOL_activity.log`
- **訂單記錄**: `logs/aster_SOL_orders.csv`

## 系統服務設置 (推薦)

### 創建服務文件
```bash
sudo nano /etc/systemd/system/trading-bot.service
```

### 服務配置
```
[Unit]
Description=Trading Bot Service
After=network.target

[Service]
User=root
WorkingDirectory=/root/perp-dex-tools
ExecStart=/root/perp-dex-tools/env/bin/python3 /root/perp-dex-tools/runbot.py --exchange bybit --ticker ETH --quantity 0.1 --take-profit 0.5 --max-orders 20 --wait-time 30 --grid-step 0.1 --market-type SPOT
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
```

### 啟用服務
```bash
sudo systemctl daemon-reload
sudo systemctl enable trading-bot.service
sudo systemctl start trading-bot.service
sudo systemctl status trading-bot.service
```

## 故障排除

### 機器人停止運行
```bash
# 檢查進程
ps aux | grep python

# 檢查日誌
tail -f logs/aster_SOL_activity.log

# 重新啟動（記得激活環境）
screen -S trading_bot
source env/bin/activate
python3 runbot.py [參數...]
```

### 常見錯誤：ModuleNotFoundError
```bash
# 錯誤：ModuleNotFoundError: No module named 'dotenv'
# 解決：激活虛擬環境
source env/bin/activate

# 然後重新啟動機器人
python3 runbot.py [參數...]
```

### 無法連接 screen 會話
```bash
# 強制分離並重新連接
screen -D -r trading_bot

# 或終止會話重新創建
screen -X -S trading_bot quit
screen -S trading_bot_new
```

### 檢查系統資源
```bash
# 查看系統負載
top

# 查看磁盤使用
df -h

# 查看記憶體使用
free -h
```

## 重要提醒

### 虛擬環境
- **每次啟動機器人前都必須激活虛擬環境**：`source env/bin/activate`
- 在 screen 會話中也要激活環境
- systemd 服務會自動使用虛擬環境的 Python

### 市場類型選擇
- **SPOT (現貨)**: 適合長期持有，無槓桿，風險較低
- **PERP (永續合約)**: 適合短期交易，有槓桿，風險較高
- **BYBIT 特殊處理**: PERP 模式會自動使用 "linear" 類別

### 常見問題
1. **ModuleNotFoundError**: 忘記激活虛擬環境
2. **API 錯誤**: 交易所暫時故障，等待後重啟即可
3. **screen 會話無法連接**: 使用 `screen -D -r trading_bot` 強制分離
4. **市場類型錯誤**: 確保使用正確的 `--market-type` 參數

### 安全提醒
- 定期備份日誌文件
- 監控機器人運行狀態
- 設置適當的風險控制參數
- 確保 API 密鑰安全
- 建議使用 systemd 服務確保長期穩定運行
- 現貨交易風險較低，永續合約需要更謹慎的風險管理

## 聯絡資訊

如有問題，請檢查日誌文件或聯繫技術支援。

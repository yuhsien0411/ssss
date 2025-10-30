# 從雲端下載日誌文件指南

## 方法 1: 使用腳本（推薦）

### Windows (PowerShell)
```powershell
# 在項目根目錄執行
.\download_logs.ps1 -Exchange lighter -Ticker YZY -RemoteHost your-server-ip -RemoteUser root
```

### Linux/Mac
```bash
# 先賦予執行權限
chmod +x download_logs.sh

# 執行腳本
./download_logs.sh lighter YZY your-server-ip root
```

## 方法 2: 直接使用 scp 命令

### Windows (PowerShell)
```powershell
# 下載單個日誌文件
scp root@your-server-ip:~/perp-dex-tools/logs/lighter_YZY_activity.log ./logs/

# 下載訂單 CSV
scp root@your-server-ip:~/perp-dex-tools/logs/lighter_YZY_orders.csv ./logs/

# 下載整個 logs 目錄
scp -r root@your-server-ip:~/perp-dex-tools/logs/* ./logs/
```

### Linux/Mac
```bash
# 下載單個日誌文件
scp root@your-server-ip:~/perp-dex-tools/logs/lighter_YZY_activity.log ./logs/

# 下載訂單 CSV
scp root@your-server-ip:~/perp-dex-tools/logs/lighter_YZY_orders.csv ./logs/

# 下載整個 logs 目錄
scp -r root@your-server-ip:~/perp-dex-tools/logs/* ./logs/
```

## 方法 3: 使用 rsync（推薦，支持增量同步）

### Linux/Mac
```bash
# 同步整個 logs 目錄（增量更新）
rsync -avz root@your-server-ip:~/perp-dex-tools/logs/ ./logs/
```

## 方法 4: 在 screen 會話中導出日誌

### 在雲端執行（如果 bot 正在運行）
```bash
# 進入 screen 會話
screen -r lighter_trading_bot

# 在 screen 中按 Ctrl+A，然後按 H 開始記錄
# 或者使用以下命令導出當前輸出
screen -r lighter_trading_bot -X hardcopy ~/bot_output.txt

# 退出 screen (Ctrl+A, D)

# 下載導出的文件
scp root@your-server-ip:~/bot_output.txt ./
```

## 日誌文件位置

### 雲端
- 日誌文件：`~/perp-dex-tools/logs/{exchange}_{ticker}_activity.log`
- 訂單 CSV：`~/perp-dex-tools/logs/{exchange}_{ticker}_orders.csv`

### 本地
- 下載後會保存到：`./logs/` 目錄

## 示例

假設你的：
- Exchange: `lighter`
- Ticker: `YZY`
- 服務器 IP: `123.456.789.0`
- 用戶名: `root`

### PowerShell 命令：
```powershell
.\download_logs.ps1 -Exchange lighter -Ticker YZY -RemoteHost 123.456.789.0 -RemoteUser root
```

### Bash 命令：
```bash
./download_logs.sh lighter YZY 123.456.789.0 root
```

## 自動同步（可選）

如果你想設置定時自動同步日誌，可以使用 cron（Linux）或 Task Scheduler（Windows）：

### Linux crontab 示例
```bash
# 每小時同步一次日誌
0 * * * * rsync -avz root@your-server-ip:~/perp-dex-tools/logs/ ~/perp-dex-tools/logs/
```

### Windows Task Scheduler
創建一個批處理文件 `sync_logs.bat`：
```batch
@echo off
scp -r root@your-server-ip:~/perp-dex-tools/logs/* .\logs\
```

然後在 Task Scheduler 中設置定時執行。


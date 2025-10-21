# Screen 常用命令指南

## 什麼是 Screen？

Screen 是一個終端多路復用器，允許用戶在一個終端會話中運行多個虛擬終端。即使網絡連接中斷，screen 會話也會繼續運行。

## 基本操作

### 啟動 Screen
```bash
# 啟動新的 screen 會話
screen

# 啟動帶名稱的 screen 會話
screen -S session_name

# 啟動 screen 並立即執行命令
screen -S my_session python script.py
```

### 基本快捷鍵
所有 screen 命令都以 `Ctrl+A` 開始，然後按其他鍵：

| 快捷鍵 | 功能 |
|--------|------|
| `Ctrl+A` + `c` | 創建新的窗口 |
| `Ctrl+A` + `n` | 切換到下一個窗口 |
| `Ctrl+A` + `p` | 切換到上一個窗口 |
| `Ctrl+A` + `"` | 顯示窗口列表 |
| `Ctrl+A` + `0-9` | 切換到指定編號的窗口 |
| `Ctrl+A` + `A` | 重命名當前窗口 |
| `Ctrl+A` + `d` | 分離會話（detach） |
| `Ctrl+A` + `k` | 殺死當前窗口 |
| `Ctrl+A` + `\` | 終止所有窗口並退出 screen |
| `Ctrl+A` + `:quit` | 退出所有窗口並關閉 screen |

## 會話管理

### 分離和重新連接
```bash
# 分離當前會話（在 screen 內按 Ctrl+A + d）
# 或者使用命令
screen -d

# 列出所有 screen 會話
screen -ls

# 重新連接到會話
screen -r session_name

# 強制重新連接（如果會話被標記為 Attached）
screen -r -d session_name
```

### 會話共享
```bash
# 允許其他用戶查看會話（只讀）
screen -x session_name

# 允許其他用戶控制會話（可寫）
screen -S session_name
# 然後其他用戶使用
screen -x session_name
```

## 窗口管理

### 窗口操作
```bash
# 在 screen 內的操作
Ctrl+A + c    # 創建新窗口
Ctrl+A + n    # 下一個窗口
Ctrl+A + p    # 上一個窗口
Ctrl+A + 0-9  # 切換到指定窗口
Ctrl+A + "    # 顯示窗口列表
Ctrl+A + A    # 重命名窗口
Ctrl+A + k    # 殺死當前窗口
Ctrl+A + \    # 殺死所有窗口並退出
```

### 窗口分割
```bash
Ctrl+A + S    # 水平分割
Ctrl+A + |    # 垂直分割
Ctrl+A + Tab  # 在分割區域間切換
Ctrl+A + X    # 關閉當前分割區域
```

## 滾動和搜索

### 滾動模式
```bash
Ctrl+A + [    # 進入滾動模式
# 在滾動模式中：
Space        # 向下滾動一頁
b            # 向上滾動一頁
↑/↓          # 逐行滾動
q            # 退出滾動模式
```

### 搜索
```bash
Ctrl+A + [    # 進入滾動模式
/            # 向下搜索
?            # 向上搜索
n            # 下一個匹配
N            # 上一個匹配
```

## 實用技巧

### 日誌記錄
```bash
# 啟動 screen 並開始記錄
screen -L -S logging_session

# 在 screen 內開始/停止記錄
Ctrl+A + H    # 開始/停止記錄
```

### 配置 Screen
創建 `~/.screenrc` 配置文件：

```bash
# 設置默認 shell
shell -$SHELL

# 設置狀態欄
hardstatus alwayslastline
hardstatus string '%{= kG}[ %{G}%H %{g}][%= %{= kw}%?%-Lw%?%{r}(%{W}%n*%f%t%?(%u)%?%{r})%{w}%?%+Lw%?%?%= %{g}][%{B} %m-%d %{W}%c %{g}]'

# 設置滾動緩衝區大小
defscrollback 10000

# 自動啟動日誌
logfile /tmp/screenlog.%t
```

## 常用場景

### 1. 運行長時間的腳本
```bash
# 啟動 screen 會話
screen -S long_script

# 在 screen 中運行腳本
python long_running_script.py

# 分離會話（Ctrl+A + d）
# 腳本繼續在後台運行

# 稍後重新連接
screen -r long_script
```

### 2. 多任務管理
```bash
# 創建多個窗口
screen -S development

# 在 screen 內：
# Ctrl+A + c  # 創建新窗口（編輯器）
# Ctrl+A + c  # 創建新窗口（測試）
# Ctrl+A + c  # 創建新窗口（日誌監控）
```

### 3. 遠程開發
```bash
# 在遠程服務器上
screen -S remote_dev

# 在 screen 中進行開發工作
# 即使網絡斷開，工作也會保存

# 重新連接後繼續工作
screen -r remote_dev
```

### 4. 啟動 HugeBot 交易機器人
```bash
# 方法一：直接啟動並命名會話
screen -S hugebot python trading_bot.py

# 方法二：先啟動 screen 再運行
screen -S hugebot
# 在 screen 內運行
python trading_bot.py

# 方法三：啟動多個交易機器人
screen -S hugebot_bybit python trading_bot.py --exchange bybit
screen -S hugebot_binance python trading_bot.py --exchange binance

# 分離會話（讓機器人在後台運行）
Ctrl+A + d

# 重新連接查看機器人狀態
screen -r hugebot

# 查看所有機器人會話
screen -ls
```

### 5. HugeBot 專用 Screen 配置
創建 `~/.screenrc_hugebot` 配置文件：

```bash
# HugeBot 專用配置
hardstatus alwayslastline
hardstatus string '%{= kG}[ %{G}%H %{g}][%= %{= kw}%?%-Lw%?%{r}(%{W}%n*%f%t%?(%u)%?%{r})%{w}%?%+Lw%?%?%= %{g}][%{B} %m-%d %{W}%c %{g}]'

# 自動記錄日誌
logfile /logs/hugebot_%t.log
log on

# 設置滾動緩衝區
defscrollback 50000

# 設置活動監控
activity "HugeBot Activity"

# 啟動時自動創建多個窗口
screen -t "Main Bot" 0
screen -t "Logs" 1
screen -t "Monitoring" 2
```

### 6. HugeBot 管理腳本
創建 `start_hugebot.sh`：

```bash
#!/bin/bash
# HugeBot Screen 管理腳本

BOT_NAME="hugebot"
LOG_DIR="/logs"

# 檢查是否已有同名會話
if screen -list | grep -q "$BOT_NAME"; then
    echo "HugeBot 會話已存在，正在重新連接..."
    screen -r $BOT_NAME
else
    echo "啟動新的 HugeBot 會話..."
    
    # 創建日誌目錄
    mkdir -p $LOG_DIR
    
    # 啟動 screen 會話
    screen -S $BOT_NAME -c ~/.screenrc_hugebot python trading_bot.py
    
    echo "HugeBot 已啟動，使用 'screen -r $BOT_NAME' 重新連接"
fi
```

### 7. 監控 HugeBot 狀態
```bash
# 查看 HugeBot 會話狀態
screen -ls | grep hugebot

# 重新連接並查看日誌
screen -r hugebot

# 在 screen 內查看實時日誌
tail -f /logs/hugebot_*.log

# 分離會話但保持機器人運行
Ctrl+A + d

# 強制重新連接（如果會話被標記為 Attached）
screen -r -d hugebot
```

## 全部關閉 Screen 的方法

### 方法一：在 Screen 內部關閉
```bash
# 在 screen 會話內使用以下任一方法：

# 1. 使用快捷鍵（推薦）
Ctrl+A + \    # 終止所有窗口並退出 screen

# 2. 使用命令模式
Ctrl+A + :quit    # 退出所有窗口並關閉 screen

# 3. 逐個關閉所有窗口
Ctrl+A + k    # 關閉當前窗口
# 重複直到所有窗口關閉
```

### 方法二：從外部強制關閉
```bash
# 1. 列出所有 screen 會話
screen -ls

# 2. 強制終止特定會話
screen -S session_name -X quit

# 3. 終止所有 screen 會話
screen -wipe

# 4. 強制殺死所有 screen 進程（謹慎使用）
pkill screen
```

### 方法三：批量關閉多個會話
```bash
# 關閉所有 screen 會話
for session in $(screen -ls | grep -o '[0-9]*\.[^[:space:]]*'); do
    screen -S "$session" -X quit
done

# 或者使用一行命令
screen -ls | grep -o '[0-9]*\.[^[:space:]]*' | xargs -I {} screen -S {} -X quit
```

### 方法四：創建關閉腳本
```bash
#!/bin/bash
# 創建 close_all_screens.sh

echo "正在關閉所有 screen 會話..."

# 列出所有會話
screen -ls

# 關閉所有會話
screen -ls | grep -o '[0-9]*\.[^[:space:]]*' | while read session; do
    echo "關閉會話: $session"
    screen -S "$session" -X quit
done

echo "所有 screen 會話已關閉"
```

## 故障排除

### 常見問題
1. **會話顯示 "Attached" 無法連接**
   ```bash
   screen -r -d session_name
   ```

2. **忘記會話名稱**
   ```bash
   screen -ls
   ```

3. **強制終止會話**
   ```bash
   screen -S session_name -X quit
   ```

4. **清理僵屍會話**
   ```bash
   screen -wipe
   ```

## 與其他工具的比較

| 工具 | 特點 | 適用場景 |
|------|------|----------|
| Screen | 簡單、穩定 | 基本的多會話管理 |
| Tmux | 功能豐富 | 複雜的終端工作流 |
| Byobu | 用戶友好 | 初學者友好的界面 |

## 總結

Screen 是一個強大的終端多路復用器，特別適合：
- 運行長時間的腳本
- 遠程開發工作
- 多任務管理
- 會話持久化

掌握這些基本命令，可以大大提高終端使用效率！

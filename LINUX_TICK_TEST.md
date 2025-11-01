# Tick 模式在 Linux 環境的測試指南

## 1. 拉取最新代碼

在你的 Linux 環境中（你已經在 `/mnt/d/MM/perp-dex-tools` 目錄）：

```bash
# 確認你在正確的目錄
pwd  # 應該顯示 /mnt/d/MM/perp-dex-tools

# 拉取最新代碼
git pull origin main
```

## 2. 確認虛擬環境已激活

你已經激活了虛擬環境，確認一下：

```bash
which python  # 應該顯示 /mnt/d/MM/perp-dex-tools/env/bin/python
```

如果沒有激活，運行：

```bash
source env/bin/activate
```

## 3. 安裝依賴（如果需要）

```bash
pip install -r requirements.txt
```

## 4. 準備 .env 文件

確保你的 `.env` 文件配置了 Lighter 的 API 資訊：

```bash
cat .env | grep -i lighter
```

應該看到類似：
```
API_KEY_PRIVATE_KEY=your_private_key
LIGHTER_ACCOUNT_INDEX=your_account_index
LIGHTER_API_KEY_INDEX=your_api_key_index
```

## 5. 測試 Tick 模式

### 測試 1: ETH 使用 Tick 模式（小額測試）

```bash
python runbot_tick.py \
  --exchange lighter \
  --ticker ETH \
  --quantity 0.1 \
  --take-profit-tick 5 \
  --grid-step-tick 10 \
  --max-orders 5 \
  --wait-time 60
```

### 測試 2: BTC 使用 Tick 模式（小額測試）

```bash
python runbot_tick.py \
  --exchange lighter \
  --ticker BTC \
  --quantity 0.05 \
  --take-profit-tick 10 \
  --grid-step-tick 20 \
  --max-orders 5 \
  --wait-time 60
```

### 測試 3: 對比百分比模式

運行傳統百分比模式做對比：

```bash
python runbot.py \
  --exchange lighter \
  --ticker ETH \
  --quantity 0.1 \
  --take-profit 0.05 \
  --grid-step 0.1 \
  --max-orders 5 \
  --wait-time 60
```

## 6. 觀察日誌

### 確認 Tick 模式的日誌

在 `trading_bot_tick.py` 中，你應該看到類似這樣的日誌：

```
=== Trading Configuration ===
Ticker: ETH
Take Profit: 5 ticks (TICK MODE)
Grid Step: 10 ticks (TICK MODE)
```

### 確認價格計算

觀察日誌中的價格計算：

```
[CLOSE] 📊 FULL FILL TP Order Parameters:
  - take_profit: 5 ticks
  - initial calculated close_price (fixed): 0.37533
```

如果是 SELL 方向，價格應該是 `filled_price + (tick_size * 5)`
如果是 BUY 方向，價格應該是 `filled_price - (tick_size * 5)`

### 確認 Grid Step 檢查

觀察日誌中的 grid step 檢查：

```
[GRID] BUY: open=0.37500 new_close=0.37550 existing_close=0.37600 diff=10.0 ticks threshold=10 ticks
[GRID] ✅ OK - Grid step condition met (10.0 ticks >= 10 ticks)
```

## 7. 驗證 Tick 模式的優勢

### 價格精度控制

Tick 模式允許你精確控制價格間隔：

- **百分比模式**：`0.37500 * (1 + 0.05%) = 0.37519` (精度受限於小數位數)
- **Tick 模式**：`0.37500 + (0.00001 * 5) = 0.37550` (完全符合交易所的 tick_size)

### 確保 POST-ONLY 訂單

Tick 模式的價格計算會自動使用 `round_to_tick`，確保：

1. 價格符合交易所的 tick_size
2. 減少 POST-ONLY 拒絕
3. 提高訂單成功率

## 8. 常見問題排查

### 問題 1: `ModuleNotFoundError`

```bash
# 確保虛擬環境已激活
source env/bin/activate

# 重新安裝依賴
pip install -r requirements.txt
```

### 問題 2: 參數錯誤

確保 `--take-profit-tick` 和 `--grid-step-tick` 一起使用：

```bash
# 錯誤！兩個參數必須一起指定
python runbot_tick.py --take-profit-tick 5

# 正確！兩個參數一起指定
python runbot_tick.py --take-profit-tick 5 --grid-step-tick 10
```

### 問題 3: Tick 大小不符

如果遇到 POST-ONLY 拒絕，檢查 tick_size：

```bash
# 在日誌中查看
grep "tick_size" logs/*.log

# 應該看到類似：tick_size=0.00001
```

## 9. 完整示例命令（500U 刷量優化配置）

根據你之前的問題，這是針對 Lighter 500U 刷量的推薦配置：

```bash
python runbot_tick.py \
  --exchange lighter \
  --ticker ETH \
  --quantity 0.1 \
  --take-profit-tick 3 \
  --grid-step-tick 6 \
  --max-orders 40 \
  --wait-time 60
```

**參數說明**：
- `--take-profit-tick 3`：3 個 tick 的利潤空間（較小的利潤，提高成交率）
- `--grid-step-tick 6`：6 個 tick 的網格間距（避免訂單太密）
- `--wait-time 60`：60 秒輪詢（較短的等待時間，提高交易頻率）

這應該能提供穩定的刷量效果！

## 10. 後續優化建議

1. **調整 tick 數**：根據市場波動和手續費調整
2. **監控成交率**：觀察 maker 訂單的成交率
3. **優化 wait_time**：根據訂單處理速度調整
4. **測試不同市場**：在波動和平靜的市場條件下測試


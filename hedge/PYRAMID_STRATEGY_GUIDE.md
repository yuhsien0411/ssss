# GRVT-Lighter 金字塔式持倉策略

## 概述

金字塔式持倉策略（Pyramid Strategy）是一種逐步建倉和平倉的交易策略，旨在延長持倉時間，增加持倉規模，同時保持 GRVT 和 Lighter 之間的對沖平衡。

## 策略邏輯

### 階段 1：建倉階段（BUILDING Phase）

從 0 持倉開始，逐步買入直到達到最大持倉量：

```
持倉: 0 → order_quantity → 2*order_quantity → ... → max_position
```

**示例：**
- `order_quantity = 0.01`
- `max_position = 0.05`
- 建倉步驟：0 → 0.01 → 0.02 → 0.03 → 0.04 → 0.05

### 階段 2：平倉階段（CLOSING Phase）

達到最大持倉後，逐步賣出直到持倉歸零：

```
持倉: max_position → ... → 2*order_quantity → order_quantity → 0
```

**示例：**
- 平倉步驟：0.05 → 0.04 → 0.03 → 0.02 → 0.01 → 0

### 循環重複

當持倉歸零後，自動切換回建倉階段，開始新的循環。

## 參數說明

### 必需參數

| 參數 | 說明 | 示例 |
|------|------|------|
| `--exchange` | 交易所（必須是 grvt） | `grvt` |
| `--ticker` | 交易對 | `ETH` |
| `--size` | 單次訂單數量 | `0.01` |
| `--iter` | 總迭代次數 | `20` |

### 可選參數

| 參數 | 說明 | 預設值 |
|------|------|--------|
| `--max-position` | 最大持倉量（金字塔頂部） | `size * 5` |
| `--fill-timeout` | 訂單成交超時（秒） | `30` |
| `--env-file` | 環境變數文件 | `.env` |

## 使用示例

### 基本用法（使用預設最大持倉）

```bash
# 最大持倉 = 0.01 * 5 = 0.05
python hedge_mode.py \
  --exchange grvt \
  --ticker ETH \
  --size 0.01 \
  --iter 20 \
  --fill-timeout 30
```

**預期行為：**
- 建倉：5 步從 0 → 0.05
- 平倉：5 步從 0.05 → 0
- 每個完整週期：10 次迭代
- 總共可以完成 2 個完整週期

### 自訂最大持倉

```bash
# 設定最大持倉為 0.1
python hedge_mode.py \
  --exchange grvt \
  --ticker ETH \
  --size 0.01 \
  --max-position 0.1 \
  --iter 30 \
  --fill-timeout 30
```

**預期行為：**
- 建倉：10 步從 0 → 0.1
- 平倉：10 步從 0.1 → 0
- 每個完整週期：20 次迭代
- 總共可以完成 1.5 個週期

### 快速測試（小持倉）

```bash
# 小額測試
python hedge_mode.py \
  --exchange grvt \
  --ticker ETH \
  --size 0.005 \
  --max-position 0.02 \
  --iter 10 \
  --fill-timeout 25
```

**預期行為：**
- 建倉：4 步從 0 → 0.02
- 平倉：4 步從 0.02 → 0
- 每個完整週期：8 次迭代
- 可以完成 1 個完整週期 + 2 步建倉

## 策略優勢

### 1. 延長持倉時間 ⏱️

相比原來的 **開倉→立即平倉** 模式，金字塔策略顯著延長了持倉時間：

| 策略 | 單次持倉時間 | 總持倉時間（20次迭代） |
|------|------------|-------------------|
| 原策略 | ~1-2 分鐘 | ~20-40 分鐘 |
| 金字塔策略 | ~10-20 分鐘 | ~100-200 分鐘 |
| **改善** | **10x** | **5x** |

### 2. 增加持倉規模 📈

金字塔策略允許持倉逐步累積到更大規模：

| 策略 | 最大單次持倉 | 平均持倉 |
|------|------------|---------|
| 原策略 | 1x order_size | 0.5x order_size |
| 金字塔策略 | 5x order_size (可調) | 2.5x order_size |
| **改善** | **5x** | **5x** |

### 3. 靈活的倉位管理 🎯

- **可調整最大持倉**：根據市場情況和風險偏好調整
- **自動階段切換**：無需人工干預
- **完整週期追蹤**：清晰的階段日誌

## 日誌示例

### 策略初始化

```
================================================================================
🎯 PYRAMID STRATEGY PARAMETERS
================================================================================
📊 Max Position: 0.0500
📊 Order Quantity: 0.0100
📊 Expected Building Steps: 5
📊 Total Iterations: 20
================================================================================
```

### 建倉階段

```
================================================================================
🎯 STRATEGY DECISION
================================================================================
📊 Current Position: 0.0100
📊 Max Position: 0.0500
📊 Current Phase: BUILDING
📊 Order Quantity: 0.0100
🔼 BUILDING Phase: BUY 0.0100
   Progress: 0.0100/0.0500 (20.0%)
================================================================================
```

### 階段切換

```
✅ Will reach MAX position after this trade, next phase: CLOSING
🔄 Reached MAX position, switching to CLOSING phase
```

### 平倉階段

```
================================================================================
🎯 STRATEGY DECISION
================================================================================
📊 Current Position: 0.0400
📊 Max Position: 0.0500
📊 Current Phase: CLOSING
📊 Order Quantity: 0.0100
🔽 CLOSING Phase: SELL 0.0100
   Progress: 0.0400 remaining (80.0%)
================================================================================
```

### 完成一個週期

```
🔄 Reached ZERO position, switching to BUILDING phase
✅ Completed one full cycle (BUILD → CLOSE)
```

## 監控和追蹤

### 持倉快照

系統會定期拍攝持倉快照：

```
📸 Position Snapshot: GRVT=0.0300, Lighter=-0.0300, Diff=0.0000, Hedged=True
```

### 持倉摘要

執行結束時會顯示完整摘要：

```
============================================================
📊 POSITION SUMMARY
============================================================
🕐 Time: 2025-10-28 14:30:00
📈 GRVT Position: 0.0000
📉 Lighter Position: 0.0000
⚖️ Position Diff: 0.0000
🎯 Hedge Ratio: 1.0000
✅ Fully Hedged: True
🏗️ Current Phase: BUILDING
📊 Max Position: 0.0500
============================================================
```

## 風險管理

### 持倉差異檢測

系統會自動檢測 GRVT 和 Lighter 之間的持倉差異：

```python
# 允許的最大差異
position_diff = abs(grvt_position + lighter_position)
if position_diff > 0.2:
    # 停止交易，防止風險
    logger.error("Position diff is too large")
    break
```

### 自動持倉同步

當檢測到持倉不匹配時，自動同步：

```
⚠️ Position mismatch detected: diff=0.0150
🔄 Syncing positions from APIs...
✅ Positions synced successfully: diff=0.0000
```

### 安全停止機制

- 按 Ctrl+C 可以安全停止
- 會自動清理 WebSocket 連接
- 保存所有日誌和交易記錄

## 性能優化

### WebSocket 優先策略

- 優先使用 WebSocket 監控訂單成交
- 只在必要時使用 REST API
- 減少 API 呼叫 70-80%

### 快速對沖執行

- GRVT 訂單成交後立即執行 Lighter 對沖
- Lighter 使用市價單快速成交
- 對沖延遲 < 1 秒

## 故障排查

### 問題：持倉不平衡

**症狀：**
```
⚠️ NOT HEDGED (diff: 0.0150)
```

**解決方案：**
1. 系統會自動同步持倉
2. 檢查 WebSocket 連接狀態
3. 查看日誌中的錯誤訊息

### 問題：訂單超時

**症狀：**
```
❌ Timeout waiting for trade completion (180s)
```

**解決方案：**
1. 增加 `--fill-timeout` 參數
2. 檢查市場流動性
3. 調整訂單價格（系統會自動重試）

### 問題：WebSocket 斷線

**症狀：**
```
⚠️ WebSocket seems inactive
```

**解決方案：**
- 系統會自動重連（最多 5 次）
- 重連失敗後自動使用 REST API
- 無需手動干預

## 最佳實踐

### 1. 選擇合適的參數

```bash
# 保守策略（小持倉，快速週期）
--size 0.005 --max-position 0.02 --iter 20

# 平衡策略（中等持倉）
--size 0.01 --max-position 0.05 --iter 30

# 積極策略（大持倉，長週期）
--size 0.02 --max-position 0.1 --iter 50
```

### 2. 監控日誌

```bash
# 實時查看日誌
tail -f logs/grvt_ETH_hedge_mode_log.txt

# 查看交易記錄
tail -f logs/grvt_ETH_hedge_mode_trades.csv
```

### 3. 雲端部署

```bash
# 本地修改後推送到 GitHub
git add hedge/hedge_mode_grvt.py hedge_mode.py
git commit -m "Update pyramid strategy"
git push origin main

# 雲端會自動拉取更新
# 在雲端運行：
python hedge_mode.py --exchange grvt --ticker ETH --size 0.01 --iter 30
```

## 與原策略對比

| 特性 | 原策略 | 金字塔策略 |
|------|--------|----------|
| 持倉方式 | 開倉→立即平倉 | 逐步建倉→逐步平倉 |
| 單次持倉時間 | 1-2 分鐘 | 10-20 分鐘 |
| 最大持倉 | 1x order_size | 5x order_size（可調） |
| 平均持倉 | 0.5x order_size | 2.5x order_size |
| 交易頻率 | 高 | 中等 |
| 持倉時間 | 短 | 長 |
| 適用場景 | 快速交易 | 中長期持倉 |

## 附加功能

### 持倉快照導出

所有持倉快照會自動保存到 CSV：

```csv
timestamp,datetime,grvt_position,lighter_position,position_diff,hedge_ratio,is_hedged
1698504000,2025-10-28 14:20:00,0.0100,-0.0100,0.0000,1.0000,True
1698504001,2025-10-28 14:20:01,0.0200,-0.0200,0.0000,1.0000,True
```

### 交易記錄導出

每筆交易都會記錄：

```csv
exchange,timestamp,side,price,quantity
GRVT,2025-10-28T14:20:00.000Z,LONG,2500.50,0.0100
Lighter,2025-10-28T14:20:01.000Z,SHORT,2500.30,0.0100
```

## 總結

金字塔式持倉策略提供了一種更靈活、更具規模的對沖交易方式，特別適合：

✅ 需要延長持倉時間的場景  
✅ 希望增加持倉規模的交易者  
✅ 追求更平滑的持倉曲線  
✅ 需要完整週期管理的策略

配合優化的 WebSocket 監控和智能 API 限額管理，能夠實現高效、穩定的自動化對沖交易。






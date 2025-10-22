# Lighter 交易所問題修復說明

## 修復日期
2025-10-21

## 發現的問題

### 1. Nonce 錯誤 (Invalid Nonce Error)
**問題描述：**
```
Exception: [OPEN] Error placing order: Order creation error: HTTP response body: code=21104 message='invalid nonce'
```

**原因分析：**
- Lighter SDK 預設使用 `OptimisticNonceManager`，它在本地追蹤 nonce 值
- 當程序重啟或連接中斷時，本地 nonce 可能與服務器不同步
- 導致下單時使用了無效的 nonce 值

**解決方案：**
- 改用 `NonceManagerType.API` - 每次都從 API 獲取最新的 nonce
- 為所有訂單操作（下單、取消）添加自動重試機制
- 當檢測到 nonce 錯誤時，SDK 會自動刷新 nonce 並重試

### 2. 倉位不匹配 (Position Mismatch)
**問題描述：**
```
ERROR: [LIGHTER_BTC] Position mismatch detected
current position: 0.01150 | active closing amount: 0.01000 | Order quantity: 20
```

**原因分析：**
- Nonce 錯誤導致某些訂單操作失敗
- 程式認為訂單沒有成交，但實際上部分訂單可能已經成交
- 導致程式追蹤的倉位與實際倉位不一致

**解決方案：**
1. **立即處理**：手動在交易所平倉差異部分（0.0015 BTC）
2. **長期修復**：
   - 改進的 nonce 管理將減少此類不同步
   - 程式啟動時會自動同步實際倉位
   - WebSocket 會持續追蹤所有訂單變化

### 3. 連接未正確關閉
**問題描述：**
```
ERROR:asyncio:Unclosed client session
ERROR:asyncio:Unclosed connector
```

**原因分析：**
- `lighter_client` 包含自己的 API 客戶端
- 程式退出時沒有正確關閉這個客戶端

**解決方案：**
- 在 `disconnect()` 方法中添加了對 `lighter_client.close()` 的調用
- 確保所有 HTTP 連接都被正確關閉

## 修改的文件

### exchanges/lighter.py

1. **初始化客戶端時使用 API Nonce Manager**
```python
# Line 105-113
from lighter.nonce_manager import NonceManagerType

self.lighter_client = SignerClient(
    url=self.base_url,
    private_key=self.api_key_private_key,
    account_index=self.account_index,
    api_key_index=self.api_key_index,
    nonce_management_type=NonceManagerType.API  # 改用 API 獲取 nonce
)
```

2. **改進 disconnect 方法**
```python
# Line 159-180
async def disconnect(self) -> None:
    # ... WebSocket 關閉 ...
    
    # 關閉 Lighter client
    if hasattr(self, 'lighter_client') and self.lighter_client:
        await self.lighter_client.close()
        self.lighter_client = None
    
    # 關閉共享 API client
    if self.api_client:
        await self.api_client.close()
        self.api_client = None
```

3. **訂單提交添加重試機制**
```python
# Line 266-305
async def _submit_order_with_retry(self, order_params: Dict[str, Any], max_retries: int = 3):
    for attempt in range(max_retries):
        create_order, tx_hash, error = await self.lighter_client.create_order(**order_params)
        
        if error is not None:
            if 'invalid nonce' in str(error).lower():
                # 自動重試 nonce 錯誤
                await asyncio.sleep(0.5)
                continue
            else:
                # 其他錯誤不重試
                return OrderResult(success=False, ...)
```

4. **取消訂單添加重試機制**
```python
# Line 421-458
async def cancel_order(self, order_id: str, max_retries: int = 3):
    # 類似的重試邏輯
```

## 使用建議

### 手動處理當前倉位不匹配

1. **檢查實際倉位**
   ```bash
   # 登入 Lighter 交易所查看實際倉位
   # 預期應該是 0.0115 BTC
   ```

2. **手動平倉差異**
   - 如果你想保持程式追蹤的倉位 (0.01 BTC)
   - 手動平掉 0.0015 BTC
   - 或者修改程式設定以匹配實際倉位

3. **重新啟動機器人**
   ```bash
   # 停止當前運行
   # 修復倉位後重新啟動
   python runbot.py
   ```

### 監控建議

1. **觀察日誌中的 nonce 警告**
   ```
   "Nonce error on attempt X/3, refreshing nonce..."
   ```
   - 如果經常出現，可能需要檢查網絡連接
   - API 模式下 nonce 獲取會比較慢（約 350ms），但更可靠

2. **倉位檢查**
   - 程式每次循環都會檢查倉位匹配
   - 如果再次出現不匹配，會立即告警

3. **連接狀態**
   - 正常斷開時應該看到：
     ```
     "Lighter client disconnected successfully"
     ```
   - 不應再有 "Unclosed client session" 錯誤

## 性能影響

### API Nonce Manager 的權衡

**優點：**
- ✅ 不會出現 nonce 不同步問題
- ✅ 程序重啟後立即可用
- ✅ 多實例運行時更安全

**缺點：**
- ⚠️ 每次下單前需要額外的 API 調用
- ⚠️ 建議每個 API key 間隔至少 350ms
- ⚠️ 可能略微降低下單速度

**適用場景：**
- 適合中低頻交易（每秒幾次訂單）
- 需要高可靠性的場景
- 經常重啟程式的情況

**如果需要高頻交易：**
- 考慮使用多個 API keys（max_api_key_index）
- Lighter SDK 支持 API key 輪換來提高並發性能

## 測試建議

重新啟動機器人後，檢查：
1. ✅ 初始化日誌中看到 "Lighter client initialized successfully with API nonce management"
2. ✅ 訂單可以正常下單和成交
3. ✅ 沒有 nonce 錯誤（或者即使出現也能自動恢復）
4. ✅ 程式退出時沒有 "Unclosed" 錯誤
5. ✅ 倉位追蹤正確

## 緊急處理步驟

如果程式再次出現倉位不匹配：

1. **立即停止機器人**
   ```bash
   # Ctrl+C 或 kill process
   ```

2. **記錄當前狀態**
   - 程式顯示的倉位
   - 交易所顯示的實際倉位
   - 所有未平倉訂單

3. **手動同步倉位**
   - 選項 A：手動平掉差異部分
   - 選項 B：取消所有訂單後重新開始

4. **檢查日誌**
   - 查找最近的錯誤或警告
   - 確認是否有訂單失敗但實際成交的情況

5. **聯繫支援**
   - 如果問題持續出現
   - 提供完整的日誌文件

## 更新日誌

### 2025-10-21
- 改用 API Nonce Manager
- 添加訂單操作重試機制
- 修復連接未關閉問題
- 添加更詳細的錯誤日誌

---

**注意：** 這些修改已經實施，下次啟動機器人時會自動生效。當前的倉位不匹配問題需要手動處理。


# Lighter API 測試指南

## 問題說明

您遇到的 `account not found` 錯誤是因為使用的 L1 地址在 Lighter 測試網上沒有註冊的帳戶。

## 解決方案

### 1. 使用安全的測試腳本（推薦）

我已經為您創建了兩個安全的測試腳本：

- `lighter_safe_test.py` - 只測試公開 API，不需要帳戶
- `test.py` - 已修改為包含錯誤處理，會先測試公開 API

### 2. 運行指令

```bash
# 激活虛擬環境
cd D:\MM\perp-dex-tools
.\venv\Scripts\activate

# 運行安全測試腳本（推薦）
python lighter_safe_test.py

# 或運行修改後的原始腳本
python test.py
```

### 3. 如何獲取有效的測試帳戶

如果您需要測試需要帳戶認證的 API，您需要：

#### 方法 1：使用 Lighter 官方測試網
1. 訪問 [Lighter 測試網](https://testnet.zklighter.elliot.ai)
2. 連接您的錢包（MetaMask 等）
3. 註冊一個測試帳戶
4. 獲取您的 L1 地址和帳戶索引

#### 方法 2：使用現有的測試帳戶
如果您有朋友已經在測試網上註冊了帳戶，可以請他們提供：
- L1 地址
- 帳戶索引
- API 金鑰索引（如果需要）

#### 方法 3：創建測試腳本使用您的帳戶
```python
# 在腳本中替換為您的真實帳戶資訊
L1_ADDRESS = "YOUR_L1_ADDRESS_HERE"
ACCOUNT_INDEX = YOUR_ACCOUNT_INDEX
API_KEY_INDEX = YOUR_API_KEY_INDEX
```

### 4. 測試腳本功能

#### 公開 API（不需要帳戶）：
- ✅ 區塊資訊查詢
- ✅ K線數據
- ✅ 訂單簿資訊
- ✅ 最近交易
- ✅ 資金費率
- ✅ 公共池資訊
- ✅ 交易歷史

#### 需要帳戶的 API：
- ❌ 帳戶資訊查詢
- ❌ API 金鑰管理
- ❌ Nonce 查詢
- ❌ 私人交易記錄

### 5. 錯誤處理

修改後的腳本包含完整的錯誤處理：
- 不會因為單個 API 失敗而崩潰
- 會顯示哪些 API 成功，哪些失敗
- 提供詳細的錯誤訊息

### 6. 下一步

1. 先運行 `lighter_safe_test.py` 確認公開 API 正常工作
2. 如果需要測試帳戶相關功能，請獲取有效的測試帳戶
3. 在腳本中替換為您的真實帳戶資訊

## 注意事項

- 測試網上的帳戶可能隨時被重置
- 請不要使用主網的私鑰進行測試
- 測試網上的代幣沒有實際價值

import asyncio
import datetime
import lighter
import logging

logging.basicConfig(level=logging.INFO)

# 使用您找到的有效測試帳戶
L1_ADDRESS = "0x0d2E3c387987e0522dD363e2D2e0b757C2FFaB7A"
ACCOUNT_INDEX = 299831

async def safe_api_call(method, *args, **kwargs):
    """安全的 API 調用，包含錯誤處理"""
    try:
        result = await method(*args, **kwargs)
        logging.info(f"✅ {method.__name__}: {result}")
        return result
    except Exception as e:
        logging.error(f"❌ {method.__name__} failed: {e}")
        return None

async def test_account_details():
    """測試帳戶詳細資訊"""
    client = lighter.ApiClient(configuration=lighter.Configuration(host="https://testnet.zklighter.elliot.ai"))
    
    try:
        logging.info("🔍 測試帳戶詳細資訊...")
        account_instance = lighter.AccountApi(client)
        
        # 測試帳戶查詢
        result = await safe_api_call(account_instance.account, by="l1_address", value=L1_ADDRESS)
        if result:
            logging.info(f"📊 帳戶資訊摘要:")
            if hasattr(result, 'accounts') and result.accounts:
                account = result.accounts[0]
                logging.info(f"   - 帳戶索引: {account.index}")
                logging.info(f"   - L1 地址: {account.l1_address}")
                logging.info(f"   - 可用餘額: {account.available_balance}")
                logging.info(f"   - 抵押品: {account.collateral}")
                logging.info(f"   - 總資產價值: {account.total_asset_value}")
                logging.info(f"   - 狀態: {account.status}")
                
                # 顯示持倉資訊
                if hasattr(account, 'positions') and account.positions:
                    logging.info(f"📈 持倉資訊:")
                    for pos in account.positions:
                        logging.info(f"   - 市場: {pos.symbol} (ID: {pos.market_id})")
                        logging.info(f"   - 持倉大小: {pos.position}")
                        logging.info(f"   - 平均入場價: {pos.avg_entry_price}")
                        logging.info(f"   - 未實現損益: {pos.unrealized_pnl}")
                        logging.info(f"   - 已實現損益: {pos.realized_pnl}")
        
        # 測試 API 金鑰
        logging.info("🔑 測試 API 金鑰...")
        for api_key_index in [0, 1, 2]:
            await safe_api_call(account_instance.apikeys, account_index=ACCOUNT_INDEX, api_key_index=api_key_index)
        
        # 測試交易相關 API
        logging.info("💱 測試交易相關 API...")
        transaction_instance = lighter.TransactionApi(client)
        for api_key_index in [0, 1, 2]:
            await safe_api_call(
                transaction_instance.next_nonce,
                account_index=ACCOUNT_INDEX,
                api_key_index=api_key_index,
            )
        
        logging.info("🎉 帳戶測試完成！")
        
    except Exception as e:
        logging.error(f"💥 測試過程中發生錯誤: {e}")
    finally:
        await client.close()

async def test_market_data():
    """測試市場數據 API"""
    client = lighter.ApiClient(configuration=lighter.Configuration(host="https://testnet.zklighter.elliot.ai"))
    
    try:
        logging.info("📊 測試市場數據 API...")
        
        # 測試訂單簿
        order_instance = lighter.OrderApi(client)
        await safe_api_call(order_instance.order_book_details, market_id=0)
        await safe_api_call(order_instance.order_books)
        await safe_api_call(order_instance.recent_trades, market_id=0, limit=5)
        await safe_api_call(order_instance.exchange_stats)
        
        # 測試 K線數據
        candlestick_instance = lighter.CandlestickApi(client)
        await safe_api_call(
            candlestick_instance.candlesticks,
            market_id=0,
            resolution="1h",
            start_timestamp=int(datetime.datetime.now().timestamp() - 60 * 60 * 24),
            end_timestamp=int(datetime.datetime.now().timestamp()),
            count_back=5,
        )
        
        # 測試資金費率
        funding_instance = lighter.FundingApi(client)
        await safe_api_call(funding_instance.funding_rates)
        
        logging.info("🎉 市場數據測試完成！")
        
    except Exception as e:
        logging.error(f"💥 測試過程中發生錯誤: {e}")
    finally:
        await client.close()

async def main():
    """主測試函數"""
    logging.info("🚀 開始 Lighter API 完整測試...")
    logging.info(f"📋 測試帳戶: {L1_ADDRESS}")
    logging.info(f"📋 帳戶索引: {ACCOUNT_INDEX}")
    
    # 測試帳戶功能
    await test_account_details()
    
    # 測試市場數據
    await test_market_data()
    
    logging.info("🎊 所有測試完成！")

if __name__ == "__main__":
    asyncio.run(main())

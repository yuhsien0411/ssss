import asyncio
import datetime
import lighter
import logging

logging.basicConfig(level=logging.INFO)

async def safe_api_call(method, *args, **kwargs):
    """安全的 API 調用，包含錯誤處理"""
    try:
        result = await method(*args, **kwargs)
        logging.info(f"✅ {method.__name__}: {result}")
        return result
    except Exception as e:
        logging.error(f"❌ {method.__name__} failed: {e}")
        return None

async def test_public_apis():
    """測試所有公開的 API（不需要帳戶認證）"""
    client = lighter.ApiClient(configuration=lighter.Configuration(host="https://testnet.zklighter.elliot.ai"))
    
    try:
        logging.info("🚀 開始測試 Lighter 公開 API...")
        
        # 測試區塊 API
        logging.info("🔍 Testing Block APIs...")
        block_instance = lighter.BlockApi(client)
        await safe_api_call(block_instance.current_height)
        await safe_api_call(block_instance.blocks, index=0, limit=2, sort="asc")
        await safe_api_call(block_instance.block, by="height", value="1")
        
        # 測試 K線 API
        logging.info("📊 Testing Candlestick APIs...")
        candlestick_instance = lighter.CandlestickApi(client)
        await safe_api_call(
            candlestick_instance.candlesticks,
            market_id=0,
            resolution="1h",
            start_timestamp=int(datetime.datetime.now().timestamp() - 60 * 60 * 24),
            end_timestamp=int(datetime.datetime.now().timestamp()),
            count_back=2,
        )
        await safe_api_call(
            candlestick_instance.fundings,
            market_id=0,
            resolution="1h",
            start_timestamp=int(datetime.datetime.now().timestamp() - 60 * 60 * 24),
            end_timestamp=int(datetime.datetime.now().timestamp()),
            count_back=2,
        )
        
        # 測試訂單 API
        logging.info("📈 Testing Order APIs...")
        order_instance = lighter.OrderApi(client)
        await safe_api_call(order_instance.exchange_stats)
        await safe_api_call(order_instance.order_book_details, market_id=0)
        await safe_api_call(order_instance.order_books)
        await safe_api_call(order_instance.recent_trades, market_id=0, limit=2)
        
        # 測試交易 API（公開部分）
        logging.info("💱 Testing Transaction APIs...")
        transaction_instance = lighter.TransactionApi(client)
        await safe_api_call(transaction_instance.block_txs, by="block_height", value="1")
        await safe_api_call(transaction_instance.txs, index=0, limit=2)
        
        # 測試資金費率 API
        logging.info("💰 Testing Funding APIs...")
        funding_instance = lighter.FundingApi(client)
        await safe_api_call(funding_instance.funding_rates)
        
        # 測試帳戶 API（公開部分）
        logging.info("👤 Testing Account APIs (public)...")
        account_instance = lighter.AccountApi(client)
        await safe_api_call(account_instance.public_pools, filter="all", limit=1, index=0)
        
        logging.info("🎉 所有公開 API 測試完成！")
        
    except Exception as e:
        logging.error(f"💥 測試過程中發生錯誤: {e}")
    finally:
        await client.close()

async def main():
    """主測試函數"""
    await test_public_apis()

if __name__ == "__main__":
    asyncio.run(main())

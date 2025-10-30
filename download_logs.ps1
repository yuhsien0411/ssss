# PowerShell 腳本：從雲端下載日誌文件
# 使用方法: .\download_logs.ps1 -Exchange lighter -Ticker YZY -RemoteHost your-server-ip -RemoteUser root

param(
    [string]$Exchange = "lighter",
    [string]$Ticker = "YZY",
    [string]$RemoteHost = "your-server-ip",
    [string]$RemoteUser = "root"
)

$RemoteDir = "~/perp-dex-tools/logs"
$LocalDir = "./logs"

# 創建本地 logs 目錄（如果不存在）
if (-not (Test-Path $LocalDir)) {
    New-Item -ItemType Directory -Path $LocalDir -Force | Out-Null
}

Write-Host "正在從 $RemoteUser@$RemoteHost 下載日誌..." -ForegroundColor Cyan
Write-Host "Exchange: $Exchange, Ticker: $Ticker" -ForegroundColor Cyan
Write-Host ""

# 下載活動日誌
$LogFile = "${Exchange}_${Ticker}_activity.log"
Write-Host "下載活動日誌: $LogFile" -ForegroundColor Yellow
try {
    scp "$RemoteUser@$RemoteHost`:$RemoteDir/$LogFile" "$LocalDir/" 2>&1 | Out-Null
    if ($LASTEXITCODE -eq 0) {
        Write-Host "✅ 成功下載: $LogFile" -ForegroundColor Green
    } else {
        Write-Host "⚠️  未找到或下載失敗: $LogFile" -ForegroundColor Red
    }
} catch {
    Write-Host "⚠️  下載失敗: $LogFile" -ForegroundColor Red
}

# 下載訂單 CSV
$CsvFile = "${Exchange}_${Ticker}_orders.csv"
Write-Host "下載訂單記錄: $CsvFile" -ForegroundColor Yellow
try {
    scp "$RemoteUser@$RemoteHost`:$RemoteDir/$CsvFile" "$LocalDir/" 2>&1 | Out-Null
    if ($LASTEXITCODE -eq 0) {
        Write-Host "✅ 成功下載: $CsvFile" -ForegroundColor Green
    } else {
        Write-Host "⚠️  未找到或下載失敗: $CsvFile" -ForegroundColor Red
    }
} catch {
    Write-Host "⚠️  下載失敗: $CsvFile" -ForegroundColor Red
}

Write-Host ""
Write-Host "日誌文件已保存到: $LocalDir" -ForegroundColor Green
Write-Host ""
Write-Host "如果要下載所有日誌文件，可以執行：" -ForegroundColor Cyan
Write-Host "  scp -r ${RemoteUser}@${RemoteHost}:${RemoteDir}/* ${LocalDir}/" -ForegroundColor Yellow


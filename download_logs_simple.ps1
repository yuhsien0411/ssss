# 簡單的日誌下載腳本 - 針對 ssss 目錄
# 使用方法: .\download_logs_simple.ps1 -RemoteHost your-server-ip

param(
    [Parameter(Mandatory=$true)]
    [string]$RemoteHost,
    [string]$RemoteUser = "root"
)

$RemoteDir = "~/ssss/logs"
$LocalDir = "./logs"
$Exchange = "lighter"
$Ticker = "YZY"

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
    scp "$RemoteUser@${RemoteHost}:${RemoteDir}/$LogFile" "$LocalDir/" 2>&1 | Out-Null
    if ($LASTEXITCODE -eq 0) {
        Write-Host "✅ 成功下載: $LogFile" -ForegroundColor Green
    } else {
        Write-Host "⚠️  未找到或下載失敗: $LogFile" -ForegroundColor Red
    }
} catch {
    Write-Host "⚠️  下載失敗: $LogFile - $_" -ForegroundColor Red
}

# 下載訂單 CSV
$CsvFile = "${Exchange}_${Ticker}_orders.csv"
Write-Host "下載訂單記錄: $CsvFile" -ForegroundColor Yellow
try {
    scp "$RemoteUser@${RemoteHost}:${RemoteDir}/$CsvFile" "$LocalDir/" 2>&1 | Out-Null
    if ($LASTEXITCODE -eq 0) {
        Write-Host "✅ 成功下載: $CsvFile" -ForegroundColor Green
    } else {
        Write-Host "⚠️  未找到或下載失敗: $CsvFile" -ForegroundColor Red
    }
} catch {
    Write-Host "⚠️  下載失敗: $CsvFile - $_" -ForegroundColor Red
}

Write-Host ""
Write-Host "日誌文件已保存到: $LocalDir" -ForegroundColor Green
Write-Host ""
Write-Host "提示：如果還沒有設置 SSH 密鑰，可能需要輸入密碼" -ForegroundColor Cyan


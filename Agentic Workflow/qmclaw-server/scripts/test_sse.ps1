$body = @{
    message = "对q25_7执行iqraw"
    mode = "react"
    context = @{}
} | ConvertTo-Json -Compress

$response = Invoke-WebRequest -Uri 'http://localhost:3002/api/agent/chat/stream' -Method Post -ContentType 'application/json' -Body $body -TimeoutSec 60
$response.Content

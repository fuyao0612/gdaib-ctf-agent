# 统一操作脚本共享的只读辅助函数；不得在这里启动服务或修改配置。

function Get-YuwangEnvValues([string]$ProjectRoot) {
    $values = @{}
    $envFile = Join-Path $ProjectRoot '.env'
    if (-not (Test-Path -LiteralPath $envFile)) { return $values }
    foreach ($line in Get-Content -LiteralPath $envFile) {
        $trimmed = $line.Trim()
        if (-not $trimmed -or $trimmed.StartsWith('#')) { continue }
        $parts = $line -split '=', 2
        if ($parts.Count -eq 2) { $values[$parts[0].Trim()] = $parts[1].Trim() }
    }
    return $values
}

function Get-YuwangWebPort([hashtable]$Values) {
    if ($Values.ContainsKey('YUWANG_WEB_PORT')) {
        $port = 0
        if ([int]::TryParse($Values['YUWANG_WEB_PORT'], [ref]$port) -and $port -ge 1 -and $port -le 65535) {
            return $port
        }
    }
    return 8080
}

function Test-YuwangPortOpen([int]$Port) {
    $client = New-Object Net.Sockets.TcpClient
    try {
        $result = $client.BeginConnect('127.0.0.1', $Port, $null, $null)
        if (-not $result.AsyncWaitHandle.WaitOne(300)) { return $false }
        $client.EndConnect($result)
        return $true
    } catch {
        return $false
    } finally {
        $client.Close()
    }
}

function Invoke-YuwangEndpoint([string]$Url) {
    try {
        $response = Invoke-WebRequest -UseBasicParsing -Uri $Url -TimeoutSec 3
        $body = $null
        if ($response.Content) { $body = $response.Content | ConvertFrom-Json }
        return @{ StatusCode = [int]$response.StatusCode; Body = $body; Error = $null }
    } catch {
        $statusCode = 0
        if ($_.Exception.Response -and $_.Exception.Response.StatusCode) {
            $statusCode = [int]$_.Exception.Response.StatusCode
        }
        $body = $null
        if ($_.ErrorDetails.Message) {
            try { $body = $_.ErrorDetails.Message | ConvertFrom-Json } catch { $body = $null }
        }
        return @{ StatusCode = $statusCode; Body = $body; Error = $_.Exception.Message }
    }
}

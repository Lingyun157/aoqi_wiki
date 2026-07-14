$ErrorActionPreference = "Continue"

$BaseUrl = "https://aoqi.100bt.com/h5/"
$VersionCachePath = Join-Path $PSScriptRoot "version.json"
$CheckInterval = 30  # seconds

# Load last known version
$lastVersion = $null
if (Test-Path $VersionCachePath) {
    try {
        $prevData = Get-Content -Path $VersionCachePath -Raw -Encoding UTF8 | ConvertFrom-Json
        # version.json is a flat map, version number is not stored in it
        # We store it separately
    } catch {}
}

# Try load last version from a dedicated file
$LastVersionFile = Join-Path $PSScriptRoot "last_version.txt"
if (Test-Path $LastVersionFile) {
    $lastVersion = (Get-Content -Path $LastVersionFile -Raw).Trim()
}

Write-Output "=== Aobi Legend Version Watcher ==="
Write-Output "  Check interval: ${CheckInterval}s"
if ($lastVersion) {
    Write-Output "  Last known version: $lastVersion"
} else {
    Write-Output "  No previous version, will detect on first check"
}
Write-Output ""

$unpackScript = Join-Path $PSScriptRoot "aoqi.ps1"

while ($true) {
    $now = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
    try {
        $timestamp = [DateTimeOffset]::UtcNow.ToUnixTimeMilliseconds()
        $resp = Invoke-WebRequest -Uri "${BaseUrl}start~${timestamp}.json" -UseBasicParsing -TimeoutSec 10
        $data = $resp.Content | ConvertFrom-Json
        $currentVersion = $data.version

        if (-not $lastVersion) {
            Write-Output "[$now] First check, version: $currentVersion"
            $lastVersion = $currentVersion
            Set-Content -Path $LastVersionFile -Value $currentVersion -Encoding UTF8 -NoNewline
        } elseif ($currentVersion -ne $lastVersion) {
            Write-Output "[$now] VERSION CHANGED: $lastVersion -> $currentVersion"
            Write-Output "[$now] Starting unpack script..."

            # Run the unpack script
            $proc = Start-Process -FilePath "powershell.exe" -ArgumentList "-ExecutionPolicy", "Bypass", "-File", $unpackScript -NoNewWindow -PassThru -Wait

            if ($proc.ExitCode -eq 0) {
                Write-Output "[$now] Unpack completed successfully"
            } else {
                Write-Output "[$now] Unpack exited with code: $($proc.ExitCode)"
            }

            $lastVersion = $currentVersion
            Set-Content -Path $LastVersionFile -Value $currentVersion -Encoding UTF8 -NoNewline
        } else {
            Write-Output "[$now] No change ($currentVersion)"
        }
    } catch {
        Write-Output "[$now] Check failed: $($_.Exception.Message)"
    }

    # Release references for GC
    $resp = $null
    $data = $null

    Start-Sleep -Seconds $CheckInterval
}

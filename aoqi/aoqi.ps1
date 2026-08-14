$ErrorActionPreference = "Continue"

$BaseUrl = "https://aoqi.100bt.com/h5/"
$OutputRoot = Join-Path $PSScriptRoot "output"
$TempDir = Join-Path $PSScriptRoot "temp_work"
$VersionCachePath = Join-Path $PSScriptRoot "version.json"
$PrevVersionPath = Join-Path $PSScriptRoot "version_prev.json"

New-Item -ItemType Directory -Path $OutputRoot -Force | Out-Null
New-Item -ItemType Directory -Path $TempDir -Force | Out-Null

Write-Output "[1/7] Fetching version info..."
$timestamp = [DateTimeOffset]::UtcNow.ToUnixTimeMilliseconds()
$startData = $null
try {
    $startResp = Invoke-WebRequest -Uri "${BaseUrl}start~${timestamp}.json" -UseBasicParsing
    $startData = $startResp.Content | ConvertFrom-Json
    Write-Output "  Version: $($startData.version), State: $($startData.state)"
} catch {
    Write-Output "  Failed: $_"
    exit 1
}

$version = $startData.version
$versionFile = "${BaseUrl}version~${version}.json"
$versionResp = Invoke-WebRequest -Uri $versionFile -UseBasicParsing
$versionMap = $versionResp.Content | ConvertFrom-Json
$allFiles = $versionMap.PSObject.Properties
Write-Output "  Total resource files: $($allFiles.Count)"

$startFiles = $startData.files.PSObject.Properties
Write-Output "  Hotfix files: $($startFiles.Count)"

Write-Output "[2/7] Merging version maps..."
$mergedVersions = @{}
foreach ($prop in $allFiles) {
    $mergedVersions[$prop.Name] = $prop.Value
}
foreach ($prop in $startFiles) {
    $mergedVersions[$prop.Name] = $prop.Value
}
Write-Output "  Merged: $($mergedVersions.Count)"

Write-Output "[3/7] Computing diff with previous run..."
$prevVersions = @{}
if (Test-Path $PrevVersionPath) {
    try {
        $prevData = Get-Content -Path $PrevVersionPath -Raw -Encoding UTF8 | ConvertFrom-Json
        foreach ($prop in $prevData.PSObject.Properties) {
            $prevVersions[$prop.Name] = $prop.Value
        }
        Write-Output "  Previous version map loaded: $($prevVersions.Count) entries"
    } catch {
        Write-Output "  Failed to parse previous version map, treating as first run"
    }
} else {
    Write-Output "  No previous version map found, first run detected"
}

# Use List for O(1) Add instead of O(n) array +=
$newFiles = [System.Collections.Generic.List[hashtable]]::new()
$updatedFiles = [System.Collections.Generic.List[hashtable]]::new()
$removedFiles = [System.Collections.Generic.List[hashtable]]::new()
$targetList = [System.Collections.Generic.List[hashtable]]::new()

foreach ($key in $mergedVersions.Keys) {
    $currentVer = $mergedVersions[$key]
    if (-not $prevVersions.ContainsKey($key)) {
        $newFiles.Add(@{ Path = $key; Version = $currentVer })
        $targetList.Add(@{ Key = $key; Value = $currentVer })
    } elseif ($prevVersions[$key] -ne $currentVer) {
        $updatedFiles.Add(@{ Path = $key; OldVersion = $prevVersions[$key]; NewVersion = $currentVer })
        $targetList.Add(@{ Key = $key; Value = $currentVer })
    }
}

foreach ($key in $prevVersions.Keys) {
    if (-not $mergedVersions.ContainsKey($key)) {
        $removedFiles.Add(@{ Path = $key; Version = $prevVersions[$key] })
    }
}

Write-Output "  New files: $($newFiles.Count)"
Write-Output "  Updated files: $($updatedFiles.Count)"
Write-Output "  Removed files: $($removedFiles.Count)"
Write-Output "  Total to download: $($targetList.Count)"

Write-Output "[4/7] Discovering missing activity JS files..."
$activityConfigPath = Join-Path $OutputRoot "config\activity\activityconfig.json"
$missingJsFiles = [System.Collections.Generic.List[hashtable]]::new()

if (Test-Path $activityConfigPath) {
    $actConfig = Get-Content -Path $activityConfigPath -Raw -Encoding UTF8 | ConvertFrom-Json
    $actJsSeen = @{}

    foreach ($dateProp in $actConfig.PSObject.Properties) {
        $dateKey = $dateProp.Name
        $activities = $dateProp.Value
        foreach ($act in $activities) {
            $entryFile = ""
            if ($act.PSObject.Properties["entryFile"]) {
                $entryFile = $act.entryFile
            }
            if ($entryFile -match "^activityext/(\d+)/([^/]+)/") {
                $actDate = $Matches[1]
                $actFolder = $Matches[2]
                $jsRelPath = "js/activities/${actDate}/${actFolder}.js"
                if (-not $actJsSeen.ContainsKey($jsRelPath)) {
                    $actJsSeen[$jsRelPath] = $true
                    $jsLocalPath = Join-Path $OutputRoot "js\activities\$actDate\$actFolder.js"
                    if (-not (Test-Path $jsLocalPath)) {
                        $missingJsFiles.Add(@{ Path = $jsRelPath; Name = $act.name; Desc = $act.desc })
                    }
                }
            }
        }
    }
    Write-Output "  Activity JS in config: $($actJsSeen.Count)"
    Write-Output "  Missing locally: $($missingJsFiles.Count)"
} else {
    Write-Output "  activityconfig.json not found, skipping"
}

if ($missingJsFiles.Count -gt 0) {
    foreach ($mj in $missingJsFiles) {
        $targetList.Add(@{ Key = $mj.Path; Value = "discovery" })
    }
    Write-Output "  Added $($missingJsFiles.Count) missing JS files to download queue"
}

if ($targetList.Count -eq 0) {
    Write-Output "  No changes detected, skipping download."
    Write-Output ""
    Write-Output "=== DONE (no updates) ==="
    exit 0
}

# --- Parallel helper using RunspacePool (PS 5.1 compatible) ---
function Invoke-Parallel {
    param(
        [System.Collections.IList]$InputData,
        [scriptblock]$ScriptBlock,
        [int]$ThrottleLimit = 4
    )

    $runspacePool = [System.Management.Automation.Runspaces.RunspaceFactory]::CreateRunspacePool(1, $ThrottleLimit)
    $runspacePool.Open()

    $jobs = [System.Collections.Generic.List[hashtable]]::new()
    foreach ($item in $InputData) {
        $ps = [System.Management.Automation.PowerShell]::Create().AddScript($ScriptBlock).AddArgument($item)
        $ps.RunspacePool = $runspacePool
        $jobs.Add(@{ PS = $ps; Handle = $ps.BeginInvoke() })
    }

    $results = [System.Collections.Generic.List[PSObject]]::new()
    foreach ($job in $jobs) {
        $results.Add($job.PS.EndInvoke($job.Handle))
        $job.PS.Dispose()
    }

    $runspacePool.Close()
    $runspacePool.Dispose()

    return $results
}

Write-Output "[5/7] Downloading files ($($targetList.Count) files, parallel 8)..."
$downloadCount = 0
$failedFiles = [System.Collections.Generic.List[string]]::new()
$totalFiles = $targetList.Count

# Build download parameter list
$downloadParams = [System.Collections.Generic.List[hashtable]]::new()
foreach ($entry in $targetList) {
    $relativePath = $entry.Key
    $ver = $entry.Value
    if ($ver -eq "discovery") {
        $url = "${BaseUrl}$([System.Uri]::EscapeUriString($relativePath))"
    } else {
        $url = "${BaseUrl}$([System.Uri]::EscapeUriString($relativePath))?v=${ver}"
    }
    $downloadParams.Add(@{ Url = $url; SavePath = (Join-Path $OutputRoot $relativePath); RelativePath = $relativePath })
}

$downloadScript = {
    param($item)
    $url = $item.Url
    $savePath = $item.SavePath
    $relativePath = $item.RelativePath
    $saveDir = Split-Path $savePath -Parent

    try {
        if (-not (Test-Path $saveDir)) {
            New-Item -ItemType Directory -Path $saveDir -Force | Out-Null
        }
        Invoke-WebRequest -Uri $url -UseBasicParsing -OutFile $savePath
        return @{ Success = $true; Path = $relativePath }
    } catch {
        return @{ Success = $false; Path = $relativePath; Error = $_.Exception.Message }
    }
}

$downloadResults = Invoke-Parallel -InputData $downloadParams -ScriptBlock $downloadScript -ThrottleLimit 8

foreach ($result in $downloadResults) {
    if ($result.Success) {
        $downloadCount++
    } else {
        $failedFiles.Add($result.Path)
    }
    if ($downloadCount % 20 -eq 0) {
        $pct = [Math]::Round($downloadCount / $totalFiles * 100, 1)
        Write-Output "  $downloadCount / $totalFiles ($pct%)"
    }
}
Write-Output "  Done: $downloadCount files downloaded, $($failedFiles.Count) failed"

Write-Output "[6/7] Extracting .mix and .aqz (parallel)..."
# Single pass scan for both extensions
$allArchives = @(Get-ChildItem $OutputRoot -Include "*.mix","*.aqz" -Recurse)
$mixCount = ($allArchives | Where-Object { $_.Extension -eq ".mix" }).Count
$aqzCount = ($allArchives | Where-Object { $_.Extension -eq ".aqz" }).Count
Write-Output "  .mix: $mixCount, .aqz: $aqzCount"

$extractParams = [System.Collections.Generic.List[hashtable]]::new()
foreach ($file in $allArchives) {
    $type = if ($file.Extension -eq ".mix") { "mix" } else { "aqz" }
    $extractParams.Add(@{ Type = $type; FullName = $file.FullName; Name = $file.Name; DirectoryName = $file.DirectoryName; TempDir = $TempDir })
}

$extractScript = {
    param($item)
    $TempDir = $item.TempDir

    try {
        if ($item.Type -eq "mix") {
            $fs = [System.IO.File]::OpenRead($item.FullName)
            $header = New-Object byte[] 256
            [void]$fs.Read($header, 0, 256)
            $fs.Close()

            $pkOffset = -1
            for ($i = 0; $i -lt 252; $i++) {
                if ($header[$i] -eq 0x50 -and $header[$i+1] -eq 0x4B -and $header[$i+2] -eq 0x03 -and $header[$i+3] -eq 0x04) {
                    $pkOffset = $i
                    break
                }
            }
            if ($pkOffset -gt 0) {
                $bytes = [System.IO.File]::ReadAllBytes($item.FullName)
                $zipBytes = $bytes[$pkOffset..($bytes.Length-1)]
                $zipName = [System.IO.Path]::GetFileNameWithoutExtension($item.Name) + ".zip"
                $zipPath = Join-Path $TempDir "$([System.Guid]::NewGuid().ToString('N'))_$zipName"
                [System.IO.File]::WriteAllBytes($zipPath, $zipBytes)
                Expand-Archive -Path $zipPath -DestinationPath $item.DirectoryName -Force
                [System.IO.File]::Delete($zipPath)
            }
        } elseif ($item.Type -eq "aqz") {
            $zipName = [System.IO.Path]::GetFileNameWithoutExtension($item.Name) + ".zip"
            $zipPath = Join-Path $TempDir "$([System.Guid]::NewGuid().ToString('N'))_$zipName"
            $srcStream = [System.IO.File]::OpenRead($item.FullName)
            $dstStream = [System.IO.File]::Create($zipPath)
            $srcStream.CopyTo($dstStream)
            $dstStream.Close()
            $srcStream.Close()
            Expand-Archive -Path $zipPath -DestinationPath $item.DirectoryName -Force
            [System.IO.File]::Delete($zipPath)
        }
        return @{ Success = $true; Name = $item.Name; Type = $item.Type }
    } catch {
        return @{ Success = $false; Name = $item.Name; Type = $item.Type; Error = $_.Exception.Message }
    }
}

$extractCount = 0
$extractErrors = [System.Collections.Generic.List[string]]::new()

if ($extractParams.Count -gt 0) {
    $extractResults = Invoke-Parallel -InputData $extractParams -ScriptBlock $extractScript -ThrottleLimit 4

    foreach ($result in $extractResults) {
        if ($result.Success) {
            $extractCount++
        } else {
            $extractErrors.Add("$($result.Type): $($result.Name)")
            Write-Output "  FAIL: $($result.Type): $($result.Name)"
        }
    }
}
Write-Output "  Extracted: $extractCount files"

Write-Output "[7/10] Saving version snapshot..."
$mergedVersions | ConvertTo-Json -Depth 10 -Compress | Set-Content -Path $VersionCachePath -Encoding UTF8
if (Test-Path $VersionCachePath) {
    Copy-Item -Path $VersionCachePath -Destination $PrevVersionPath -Force
    Write-Output "  Version snapshot saved for next diff"
}

if ($failedFiles.Count -gt 0) {
    Write-Output "  Download failed ($($failedFiles.Count)):"
    foreach ($f in $failedFiles) {
        Write-Output "    - $f"
    }
}

Write-Output "[8/10] Rebuilding Lingchu knowledge base..."
$buildScript = Join-Path $PSScriptRoot "build_lingchu_knowledge_base.py"
if (Test-Path $buildScript) {
    try {
        $proc = Start-Process -FilePath "python" -ArgumentList $buildScript -NoNewWindow -Wait -PassThru
        if ($proc.ExitCode -eq 0) {
            Write-Output "  Lingchu knowledge base rebuilt successfully"
        } else {
            Write-Output "  Lingchu build script exited with code: $($proc.ExitCode)"
        }
    } catch {
        Write-Output "  Failed to run lingchu build script: $($_.Exception.Message)"
    }
} else {
    Write-Output "  build_lingchu_knowledge_base.py not found, skipping"
}

Write-Output "[9/10] Rebuilding enemy formation guide (incremental)..."
$formationScript = Join-Path $PSScriptRoot "build_enemy_formation_guide.py"
if (Test-Path $formationScript) {
    try {
        $proc = Start-Process -FilePath "python" -ArgumentList ($formationScript, "--incremental") -NoNewWindow -Wait -PassThru
        if ($proc.ExitCode -eq 0) {
            Write-Output "  Enemy formation guide rebuilt successfully (incremental)"
        } else {
            Write-Output "  Formation build script exited with code: $($proc.ExitCode)"
        }
    } catch {
        Write-Output "  Failed to run formation build script: $($_.Exception.Message)"
    }
} else {
    Write-Output "  build_enemy_formation_guide.py not found, skipping"
}

Write-Output "[10/11] Rebuilding Obsidian vault..."
$obsidianScript = Join-Path $PSScriptRoot "build_obsidian_vault.py"
if (Test-Path $obsidianScript) {
    try {
        $proc = Start-Process -FilePath "python" -ArgumentList $obsidianScript -NoNewWindow -Wait -PassThru
        if ($proc.ExitCode -eq 0) {
            Write-Output "  Obsidian vault rebuilt successfully"
        } else {
            Write-Output "  Obsidian build script exited with code: $($proc.ExitCode)"
        }
    } catch {
        Write-Output "  Failed to run obsidian build script: $($_.Exception.Message)"
    }
} else {
    Write-Output "  build_obsidian_vault.py not found, skipping"
}

Write-Output "[11/11] Skipping Bilibili video crawl (manual upload mode)..."
Write-Output "  Users can manually add guide videos via the pet detail page in the web app."

Write-Output ""
Write-Output "=== DONE ==="
$allOutput = @(Get-ChildItem $OutputRoot -Recurse -File)
Write-Output "Files: $($allOutput.Count)"
$totalSize = ($allOutput | Measure-Object -Property Length -Sum).Sum
Write-Output "Size: $([Math]::Round($totalSize / 1MB, 2)) MB"

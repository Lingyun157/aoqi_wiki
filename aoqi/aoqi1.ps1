$ErrorActionPreference = "Continue"

$BaseUrl = "https://aoqi.100bt.com/h5/"
$OutputRoot = Join-Path $PSScriptRoot "output"
$TempDir = Join-Path $PSScriptRoot "temp_work"
$VersionCachePath = Join-Path $PSScriptRoot "version.json"
$PrevVersionPath = Join-Path $PSScriptRoot "version_prev.json"

New-Item -ItemType Directory -Path $OutputRoot -Force | Out-Null
New-Item -ItemType Directory -Path $TempDir -Force | Out-Null

# ===== Step [1/5] Save version snapshot =====
Write-Output "[1/5] Saving version snapshot..."
if(Test-Path $VersionCachePath){
    Copy-Item -Path $VersionCachePath -Destination $PrevVersionPath -Force
    Write-Output "  Version snapshot saved for next diff"
}else{
    Write-Output "  No current version.json exists, skip copy"
}

# ===== Step [2/5] Rebuild Lingchu knowledge base =====
Write-Output "[2/5] Rebuilding Lingchu knowledge base..."
# ===== Resolve Python path =====
function Find-Python {
    $candidates = @(
        "python3",
        "python",
        "$PSScriptRoot\..\..\..\..\miniconda\MiniConda\python.exe"
    )
    foreach ($c in $candidates) {
        try {
            $result = & $c --version 2>&1
            if ($LASTEXITCODE -eq 0) {
                return $c
            }
        } catch {}
    }
    return $null
}

$PythonPath = Find-Python
if (-not $PythonPath) {
    Write-Warning "  Python not found in PATH -- build steps will be skipped"
} else {
    Write-Output "  Python detected: $PythonPath"
}

$buildScript = Join-Path $PSScriptRoot "build_lingchu_knowledge_base.py"
if (Test-Path $buildScript) {
    if ($PythonPath) {
        try {
            $output = & $PythonPath $buildScript 2>&1
            if ($LASTEXITCODE -eq 0) {
                Write-Output "  Lingchu knowledge base rebuilt successfully"
            } else {
                Write-Output "  Lingchu build script exited with code: $LASTEXITCODE"
                if ($output) {
                    Write-Output "  Output: $($output -join "`n  ") "
                }
            }
        } catch {
            Write-Output "  Failed to run lingchu build script: $($_.Exception.Message)"
        }
    } else {
        Write-Output "  Skipped (Python not available)"
    }
} else {
    Write-Output "  build_lingchu_knowledge_base.py not found, skipping"
}

# ===== Step [3/5] Rebuild enemy formation guide =====
Write-Output "[3/5] Rebuilding enemy formation guide..."
$formationScript = Join-Path $PSScriptRoot "build_enemy_formation_guide.py"
if (Test-Path $formationScript) {
    if ($PythonPath) {
        try {
            $output = & $PythonPath $formationScript 2>&1
            if ($LASTEXITCODE -eq 0) {
                Write-Output "  Enemy formation guide rebuilt successfully"
            } else {
                Write-Output "  Formation build script exited with code: $LASTEXITCODE"
                if ($output) {
                    Write-Output "  Output: $($output -join "`n  ") "
                }
            }
        } catch {
            Write-Output "  Failed to run formation build script: $($_.Exception.Message)"
        }
    } else {
        Write-Output "  Skipped (Python not available)"
    }
} else {
    Write-Output "  build_enemy_formation_guide.py not found, skipping"
}

# ===== Step [4/5] Rebuild Obsidian vault =====
Write-Output "[4/5] Rebuilding Obsidian vault..."
$obsidianScript = Join-Path $PSScriptRoot "build_obsidian_vault.py"
if (Test-Path $obsidianScript) {
    if ($PythonPath) {
        try {
            $output = & $PythonPath $obsidianScript 2>&1
            if ($LASTEXITCODE -eq 0) {
                Write-Output "  Obsidian vault rebuilt successfully"
            } else {
                Write-Output "  Obsidian build script exited with code: $LASTEXITCODE"
                if ($output) {
                    Write-Output "  Output: $($output -join "`n  ") "
                }
            }
        } catch {
            Write-Output "  Failed to run obsidian build script: $($_.Exception.Message)"
        }
    } else {
        Write-Output "  Skipped (Python not available)"
    }
} else {
    Write-Output "  build_obsidian_vault.py not found, skipping"
}

# ===== Step [5/5] Summary and cleanup =====
Write-Output "[5/5] Summary and cleanup..."

# Clean up temp directory
$tempFiles = @(Get-ChildItem $TempDir -File -ErrorAction SilentlyContinue)
if ($tempFiles.Count -gt 0) {
    try {
        Remove-Item (Join-Path $TempDir "*") -Recurse -Force -ErrorAction SilentlyContinue
        Write-Output "  Temp directory cleaned"
    } catch {
        Write-Output "  Temp cleanup skipped (files in use)"
    }
}

Write-Output ""
Write-Output "=== DONE ==="
$allOutput = @(Get-ChildItem $OutputRoot -Recurse -File -ErrorAction SilentlyContinue)
Write-Output "  Total output files: $($allOutput.Count)"
$totalSize = ($allOutput | Measure-Object -Property Length -Sum).Sum
Write-Output "  Total output size: $([Math]::Round($totalSize / 1MB, 2)) MB"
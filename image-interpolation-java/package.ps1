param(
    [Parameter(Mandatory = $true)]
    [string]$StudentId,
    [Parameter(Mandatory = $true)]
    [string]$StudentName
)

$ErrorActionPreference = "Stop"
$projectDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$parentDir = Split-Path -Parent $projectDir
$safeName = "第一作业_{0}_{1}" -f $StudentId, $StudentName
$zipPath = Join-Path $parentDir ($safeName + ".zip")
$tempDir = Join-Path ([System.IO.Path]::GetTempPath()) ("image-interpolation-" + [guid]::NewGuid().ToString("N"))

New-Item -ItemType Directory -Force -Path $tempDir | Out-Null
try {
    Copy-Item -LiteralPath (Join-Path $projectDir "src") -Destination $tempDir -Recurse
    Copy-Item -LiteralPath (Join-Path $projectDir "input") -Destination $tempDir -Recurse
    Copy-Item -LiteralPath (Join-Path $projectDir "results") -Destination $tempDir -Recurse
    Copy-Item -LiteralPath (Join-Path $projectDir "README.md") -Destination $tempDir
    Copy-Item -LiteralPath (Join-Path $projectDir "实验报告.md") -Destination $tempDir
    Compress-Archive -Path (Join-Path $tempDir "*") -DestinationPath $zipPath -Force
    Write-Host "已生成: $zipPath"
}
finally {
    Remove-Item -LiteralPath $tempDir -Recurse -Force -ErrorAction SilentlyContinue
}

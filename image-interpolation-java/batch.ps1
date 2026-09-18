param(
    [string]$InputDir = "..\src",
    [string]$OutputDir = "results-src"
)

$ErrorActionPreference = "Stop"
$projectDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$outDir = Join-Path $projectDir "out"
New-Item -ItemType Directory -Force -Path $outDir | Out-Null

javac -encoding UTF-8 -d $outDir (Get-ChildItem (Join-Path $projectDir "src") -Filter "*.java").FullName
java -Xmx2g -cp $outDir BatchMain --input-dir (Join-Path $projectDir $InputDir) --output-dir (Join-Path $projectDir $OutputDir)

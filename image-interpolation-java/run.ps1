param(
    [Parameter(Mandatory = $true)]
    [string]$InputImage,
    [string]$OutputDir = "results"
)

$ErrorActionPreference = "Stop"
$projectDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$outDir = Join-Path $projectDir "out"
New-Item -ItemType Directory -Force -Path $outDir | Out-Null

javac -encoding UTF-8 -d $outDir (Get-ChildItem (Join-Path $projectDir "src") -Filter "*.java").FullName
java -cp $outDir Main --input $InputImage --output-dir (Join-Path $projectDir $OutputDir)

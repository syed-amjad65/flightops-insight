<#
.SYNOPSIS
    FlightOps Insight task runner for Windows PowerShell.

.EXAMPLE
    .\run.ps1 all
    .\run.ps1 app
    .\run.ps1 test
#>
param(
    [Parameter(Position = 0)]
    [ValidateSet("data", "train-otp", "train-rul", "optimize", "all", "app", "test")]
    [string]$Task = "all",
    [switch]$Force
)

$ErrorActionPreference = "Stop"
$forceArg = @()
if ($Force) { $forceArg = @("--force") }

switch ($Task) {
    "data"      { python scripts/build_data.py @forceArg }
    "train-otp" { python scripts/train_otp.py @forceArg }
    "train-rul" { python scripts/train_rul.py @forceArg }
    "optimize"  { python scripts/run_optimization.py @forceArg }
    "all"       { python scripts/run_all.py @forceArg }
    "app"       { python -m streamlit run app.py }
    "test"      { python -m pytest }
}

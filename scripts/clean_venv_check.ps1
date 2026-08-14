#Requires -Version 7.0
<#
.SYNOPSIS
    The definition of done, expressed as a command: a stranger with no keys.

.DESCRIPTION
    Creates a throwaway virtualenv under the system temp directory, installs the
    locally built wheel into it, and runs `migkit demo` with ANTHROPIC_API_KEY and
    OPENAI_API_KEY empty -- asserting that an HTML report comes out.

    This is item 5 of the pre-release checklist in
    docs/session-4-release-contract.md (section 5), and it is
    the only check in the repository that exercises what a user actually receives.
    Everything else -- the test suite, `pip install -e .`, an editable-install CI
    job -- keeps the repo importable, so all of them pass over a wheel that is
    missing its bundled demo data.

    Three rules it will not break:

      * It never touches the repo's own .venv, and refuses to run if asked to.
      * It runs from the throwaway directory, never from the repo. A demo that
        reads its golden set relative to the cwd passes from the repo root and
        fails from everywhere else, which is where every user is.
      * It cleans up. A throwaway venv that survives becomes the environment the
        next check accidentally trusts.

    What it does NOT do is pass silently. If `migkit demo` does not exist yet
    (Session 3 has not landed cli.py), it reports SKIPPED and exits 2.

.PARAMETER Wheel
    Wheel to install. Defaults to the newest dist/*.whl in the repository.

.PARAMETER Python
    Interpreter used to create the throwaway venv. Defaults to py -3.12 on
    Windows, then python3/python. Must not be the repo's own .venv.

.PARAMETER RepoRoot
    Repository root. Defaults to the parent of this script's directory.

.PARAMETER Keep
    Keep the throwaway venv for inspection instead of deleting it.

.OUTPUTS
    Exit 0 = the report was produced. Exit 1 = it was not. Exit 2 = the check
    could not run, with the reason printed.

.EXAMPLE
    .\.venv\Scripts\python.exe scripts\verify_release.py
    pwsh -File scripts\clean_venv_check.ps1
#>
[CmdletBinding()]
param(
    [string]$Wheel,
    [string]$Python,
    [string]$RepoRoot,
    [switch]$Keep
)

Set-StrictMode -Version 3.0
$ErrorActionPreference = 'Stop'
# PowerShell 7.4 turns a non-zero native exit code into a terminating error when
# ErrorActionPreference is Stop. Every failure here is meant to be reported as a
# named check with its evidence, not as a stack trace, so exit codes are read
# from $LASTEXITCODE explicitly instead.
$PSNativeCommandUseErrorActionPreference = $false

$script:Failures = 0
$script:Skips = 0

function Write-Check {
    param([string]$Status, [string]$Name, [string]$Detail)
    Write-Host ("[{0}] {1}: {2}" -f $Status.PadRight(7), $Name, $Detail)
    if ($Status -eq 'FAIL') { $script:Failures++ }
    if ($Status -eq 'SKIPPED') { $script:Skips++ }
}

function Write-Evidence {
    param([string]$Line)
    if ($Line) { Write-Host ("            {0}" -f $Line) }
}

function Resolve-BaseInterpreter {
    <#
        Returns @(exe, arg...) for an interpreter that is not the repo's venv.
        Creating a venv from another venv works, but it inherits that venv's
        pip configuration and its idea of where packages live -- which is the
        one thing this check exists to be free of.
    #>
    param([string]$Explicit, [string]$Repo)

    if ($Explicit) {
        $resolved = Get-Command $Explicit -ErrorAction SilentlyContinue
        if (-not $resolved) { throw "interpreter not found: $Explicit" }
        return , @($resolved.Source)
    }
    if ($IsWindows) {
        $py = Get-Command 'py' -ErrorAction SilentlyContinue
        if ($py) {
            foreach ($ver in @('-3.12', '-3')) {
                & $py.Source $ver -c 'import sys' *> $null
                if ($LASTEXITCODE -eq 0) { return , @($py.Source, $ver) }
            }
        }
    }
    $repoVenv = Join-Path $Repo '.venv'
    foreach ($name in @('python3', 'python')) {
        $cmd = Get-Command $name -ErrorAction SilentlyContinue
        if ($cmd -and -not $cmd.Source.StartsWith($repoVenv, [StringComparison]::OrdinalIgnoreCase)) {
            return , @($cmd.Source)
        }
    }
    throw 'no base Python interpreter found (tried py -3.12, py -3, python3, python)'
}

function Invoke-DemoCheck {
    <#
        Everything that happens inside the throwaway venv. A function, not inline
        script, so that an early `return` on a SKIPPED path lands in the caller
        rather than exiting the script before its summary is printed.
    #>
    param(
        [string]$Tmp,
        [string]$VenvPython,
        [string]$BinDir,
        [string]$Exe,
        [string]$Repo,
        [string]$WheelPath
    )

    & $VenvPython -m pip install --disable-pip-version-check --quiet $WheelPath
    if ($LASTEXITCODE -ne 0) {
        Write-Check 'FAIL' 'install' "pip install of the local wheel exited $LASTEXITCODE"
        Write-Evidence 'dependencies still come from PyPI; a network failure looks like this too'
        return
    }
    Write-Check 'PASS' 'install' ("installed {0} from the local file" -f (Split-Path -Leaf $WheelPath))
    foreach ($line in (& $VenvPython -m pip show model-migration-kit)) {
        if ($line -match '^(Name|Version|Location):') { Write-Evidence $line }
    }

    # Where the packages actually live. This is the empirical form of the import
    # name check: nothing may resolve under the repository.
    $probe = @'
import json
out = {}
try:
    import model_migration_kit
    out["model_migration_kit"] = list(
        getattr(model_migration_kit, "__path__", [getattr(model_migration_kit, "__file__", "")])
    )
except Exception as exc:
    out["migration_kit_error"] = "%s: %s" % (type(exc).__name__, exc)
try:
    import opik_rigor
    out["opik_rigor"] = [getattr(opik_rigor, "__file__", "")]
except Exception as exc:
    out["opik_rigor_error"] = "%s: %s" % (type(exc).__name__, exc)
print(json.dumps(out))
'@
    $probeFile = Join-Path $Tmp 'probe_paths.py'
    Set-Content -LiteralPath $probeFile -Value $probe -Encoding utf8

    $raw = & $VenvPython $probeFile
    $located = @()
    if ($LASTEXITCODE -eq 0) {
        $parsed = $raw | ConvertFrom-Json
        foreach ($key in @('model_migration_kit', 'opik_rigor')) {
            if ($parsed.PSObject.Properties.Name -contains $key) { $located += $parsed.$key }
        }
        foreach ($key in @('migration_kit_error', 'opik_rigor_error')) {
            if ($parsed.PSObject.Properties.Name -contains $key) { Write-Evidence $parsed.$key }
        }
    }
    foreach ($p in $located) { Write-Evidence $p }
    $leaked = @($located | Where-Object { $_ -and $_.StartsWith($Repo, [StringComparison]::OrdinalIgnoreCase) })
    if ($leaked.Count -gt 0) {
        Write-Check 'FAIL' 'isolation' 'an import resolved inside the repository, so this environment is not clean'
    }
    elseif ($located.Count -lt 2) {
        Write-Check 'FAIL' 'isolation' 'model_migration_kit and/or opik_rigor did not import from the clean venv'
    }
    else {
        Write-Check 'PASS' 'isolation' 'model_migration_kit and opik_rigor both resolve inside the throwaway venv'
    }

    # ----------------------------------------------------------------------
    # The definition of done
    # ----------------------------------------------------------------------
    $migkit = Join-Path $Tmp (Join-Path $BinDir "migkit$Exe")
    $report = Join-Path $Tmp 'demo.html'
    if (-not (Test-Path -LiteralPath $migkit)) {
        Write-Check 'SKIPPED' 'migkit demo' 'the installed wheel provides no migkit console script'
        Write-Evidence ("looked for: {0}" -f $migkit)
        Write-Evidence 'the definition of done is therefore UNVERIFIED, not met'
        return
    }

    $stdout = Join-Path $Tmp 'demo.stdout.txt'
    $stderr = Join-Path $Tmp 'demo.stderr.txt'
    $sw = [Diagnostics.Stopwatch]::StartNew()
    $proc = Start-Process -FilePath $migkit -ArgumentList @('demo', '--out', $report) `
        -NoNewWindow -Wait -PassThru -WorkingDirectory $Tmp `
        -RedirectStandardOutput $stdout -RedirectStandardError $stderr
    $sw.Stop()
    $code = $proc.ExitCode
    $errText = if (Test-Path -LiteralPath $stderr) { Get-Content -Raw -LiteralPath $stderr } else { '' }
    if (-not $errText) { $errText = '' }
    $reportExists = Test-Path -LiteralPath $report

    # argparse rejects an unknown subcommand with exit 2 and a usage message,
    # which is indistinguishable from the REVIEW verdict by exit code alone -- so
    # the two are told apart by the usage text and the absent report.
    if (-not $reportExists -and $errText -match 'invalid choice|unrecognized arguments|usage:') {
        Write-Check 'SKIPPED' 'migkit demo' 'the CLI has no demo subcommand yet (Session 3 has not landed it)'
        Write-Evidence ("exit {0} in {1:n1}s" -f $code, $sw.Elapsed.TotalSeconds)
        foreach ($line in (($errText -split "`n") | Select-Object -First 4)) { Write-Evidence $line.Trim() }
        Write-Evidence 'the definition of done is UNVERIFIED, not met'
        return
    }

    Write-Evidence ("command: migkit demo --out {0}" -f $report)
    Write-Evidence ("exit {0}, elapsed {1:n1}s, cwd {2}" -f $code, $sw.Elapsed.TotalSeconds, $Tmp)
    Write-Evidence 'exit codes: 0=GO 1=NO-GO 2=REVIEW 3=error (contracts.Verdict.EXIT_CODES)'

    # A console script whose target module is not in the wheel is a broken wheel,
    # and verify_release.py's `console-script` row is the check that owns that
    # claim and fails on it. Here it means only that the definition of done could
    # not be exercised -- so it is SKIPPED (exit 2), never a pass, and the reason
    # names the row that will tell you why.
    if (-not $reportExists -and $errText -match "No module named '(model_migration_kit[\w.]*)'") {
        Write-Check 'SKIPPED' 'migkit demo' ("the installed wheel has no {0} module" -f $Matches[1])
        Write-Evidence 'Session 3 has not landed cli.py, or the wheel omitted it'
        Write-Evidence 'see the console-script row of: python scripts/verify_release.py'
        Write-Evidence 'the definition of done is UNVERIFIED, not met'
        return
    }

    if (-not $reportExists) {
        Write-Check 'FAIL' 'migkit demo' 'no HTML report was written'
        foreach ($line in (($errText -split "`n") | Select-Object -First 10)) { Write-Evidence $line.Trim() }
        return
    }

    $size = (Get-Item -LiteralPath $report).Length
    Write-Evidence ("{0} is {1:n0} bytes" -f (Split-Path -Leaf $report), $size)
    if ($code -eq 3) {
        Write-Check 'FAIL' 'migkit demo' 'exit 3 means the tool could not produce a verdict'
        foreach ($line in (($errText -split "`n") | Select-Object -First 10)) { Write-Evidence $line.Trim() }
        return
    }
    if ($size -lt 2048) {
        Write-Check 'FAIL' 'migkit demo' "the report is $size bytes -- too small to be a real report"
        return
    }
    Write-Check 'PASS' 'migkit demo' 'a keyless stranger got an HTML report'
    if ($sw.Elapsed.TotalSeconds -gt 120) {
        Write-Check 'FAIL' 'two-minute claim' ("took {0:n1}s, over the 120s definition of done" -f $sw.Elapsed.TotalSeconds)
    }
    else {
        Write-Check 'PASS' 'two-minute claim' ("{0:n1}s, inside the 120s budget" -f $sw.Elapsed.TotalSeconds)
    }
}

# --------------------------------------------------------------------------------------
# Locate the repository and the wheel
# --------------------------------------------------------------------------------------

if (-not $RepoRoot) { $RepoRoot = Split-Path -Parent $PSScriptRoot }
$RepoRoot = (Resolve-Path -LiteralPath $RepoRoot).Path

Write-Host ('=' * 100)
Write-Host 'model-migration-kit clean-venv check -- a stranger with no keys'
Write-Host 'docs/session-4-release-contract.md, pre-release checklist item 5'
Write-Host ('=' * 100)
Write-Host ("repo        : {0}" -f $RepoRoot)

if (-not $Wheel) {
    $candidates = @(
        Get-ChildItem -Path (Join-Path $RepoRoot 'dist') -Filter '*.whl' -ErrorAction SilentlyContinue |
            Sort-Object LastWriteTime -Descending
    )
    if ($candidates.Count -eq 0) {
        Write-Check 'SKIPPED' 'wheel' 'no wheel in dist/ to install'
        Write-Evidence 'build one first: .\.venv\Scripts\python.exe scripts\verify_release.py'
        exit 2
    }
    $Wheel = $candidates[0].FullName
    if ($candidates.Count -gt 1) {
        Write-Evidence ("note: {0} wheels in dist/, taking the newest" -f $candidates.Count)
    }
}
$Wheel = (Resolve-Path -LiteralPath $Wheel).Path
Write-Host ("wheel       : {0} ({1:n0} bytes)" -f (Split-Path -Leaf $Wheel), (Get-Item $Wheel).Length)

try {
    $interp = Resolve-BaseInterpreter -Explicit $Python -Repo $RepoRoot
}
catch {
    Write-Check 'SKIPPED' 'interpreter' $_.Exception.Message
    exit 2
}

$repoVenvPath = Join-Path $RepoRoot '.venv'
if ($interp[0].StartsWith($repoVenvPath, [StringComparison]::OrdinalIgnoreCase)) {
    Write-Check 'FAIL' 'interpreter' "refusing to build the throwaway venv from the repo's own .venv"
    Write-Evidence 'pass -Python <path to a base interpreter> instead'
    exit 1
}
Write-Host ("interpreter : {0}" -f ($interp -join ' '))

$interpExe = $interp[0]
$interpArgs = @()
if ($interp.Count -gt 1) { $interpArgs = $interp[1..($interp.Count - 1)] }

# --------------------------------------------------------------------------------------
# Throwaway location, guarded
# --------------------------------------------------------------------------------------

$tmp = Join-Path ([System.IO.Path]::GetTempPath()) ('mk-clean-' + [guid]::NewGuid().ToString('N'))
if ($tmp.StartsWith($RepoRoot, [StringComparison]::OrdinalIgnoreCase)) {
    Write-Check 'FAIL' 'sandbox' 'the system temp directory is inside the repository -- refusing to run'
    exit 1
}
Write-Host ("throwaway   : {0}" -f $tmp)
Write-Host ''

$binDir = if ($IsWindows) { 'Scripts' } else { 'bin' }
$exeSuffix = if ($IsWindows) { '.exe' } else { '' }

$savedEnv = @{}
foreach ($key in @('ANTHROPIC_API_KEY', 'OPENAI_API_KEY', 'VIRTUAL_ENV', 'PYTHONPATH', 'PYTHONHOME')) {
    $savedEnv[$key] = [Environment]::GetEnvironmentVariable($key)
}

try {
    # The keys are emptied, not unset: the contract's phrasing is "with
    # ANTHROPIC_API_KEY and OPENAI_API_KEY empty", and an empty string is the
    # harder case -- code testing `if key is not None` passes with unset and
    # fails with empty.
    $env:ANTHROPIC_API_KEY = ''
    $env:OPENAI_API_KEY = ''
    # Inherited venv pointers would let the throwaway environment reach the repo.
    $env:VIRTUAL_ENV = $null
    $env:PYTHONPATH = $null
    $env:PYTHONHOME = $null

    & $interpExe @interpArgs -m venv $tmp
    $venvPython = Join-Path $tmp (Join-Path $binDir "python$exeSuffix")
    if ($LASTEXITCODE -ne 0 -or -not (Test-Path -LiteralPath $venvPython)) {
        Write-Check 'FAIL' 'venv' "could not create a virtualenv at $tmp"
    }
    else {
        Write-Check 'PASS' 'venv' 'throwaway virtualenv created, empty of everything'
        Write-Evidence ("python: {0}" -f (& $venvPython -c 'import sys; print(sys.version.split()[0])'))
        Push-Location $tmp   # NOT the repo: a repo-root cwd can mask a missing package resource
        try {
            Invoke-DemoCheck -Tmp $tmp -VenvPython $venvPython -BinDir $binDir `
                -Exe $exeSuffix -Repo $RepoRoot -WheelPath $Wheel
        }
        finally {
            Pop-Location
        }
    }
}
finally {
    foreach ($key in @($savedEnv.Keys)) {
        [Environment]::SetEnvironmentVariable($key, $savedEnv[$key])
    }
    if ($Keep) {
        Write-Host ''
        Write-Host ("throwaway venv kept at {0} -- delete it before trusting the next run" -f $tmp)
    }
    elseif (Test-Path -LiteralPath $tmp) {
        Remove-Item -Recurse -Force -LiteralPath $tmp -ErrorAction SilentlyContinue
        Write-Host ''
        Write-Host ("cleaned up {0}" -f $tmp)
    }
}

Write-Host ('=' * 100)
if ($script:Failures -gt 0) {
    Write-Host ("{0} check(s) FAILED." -f $script:Failures)
    exit 1
}
if ($script:Skips -gt 0) {
    Write-Host ("{0} check(s) SKIPPED. A skip is not a pass -- exit 2 so nothing mistakes this for green." -f $script:Skips)
    exit 2
}
Write-Host 'The definition of done holds: a stranger with no keys gets an HTML report.'
exit 0

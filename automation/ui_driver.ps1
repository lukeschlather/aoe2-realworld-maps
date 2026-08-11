# Reusable Win32 UI automation helpers for driving the AoE2 DE Scenario
# Editor. Coordinates are in physical pixels - this process opts in to
# per-monitor-v2 DPI awareness so SetCursorPos and the screenshot capture
# (System.Drawing CopyFromScreen) agree on the same coordinate space.
#
# Must run under Windows PowerShell 5.1 (powershell.exe), NOT PowerShell 7
# (pwsh) - the WinRT OCR API projection this relies on ([Windows.Media.Ocr...,
# ContentType=WindowsRuntime]) only works under the .NET Framework-based
# PowerShell, not .NET Core/5+.

Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName System.Drawing
Add-Type -AssemblyName System.Runtime.WindowsRuntime

[Windows.Media.Ocr.OcrEngine, Windows.Media.Ocr, ContentType = WindowsRuntime] | Out-Null
[Windows.Graphics.Imaging.BitmapDecoder, Windows.Graphics.Imaging, ContentType = WindowsRuntime] | Out-Null
[Windows.Storage.Streams.InMemoryRandomAccessStream, Windows.Storage.Streams, ContentType = WindowsRuntime] | Out-Null
[Windows.Storage.Streams.DataWriter, Windows.Storage.Streams, ContentType = WindowsRuntime] | Out-Null

$script:asTaskGeneric = ([System.WindowsRuntimeSystemExtensions].GetMethods() | Where-Object {
    $_.Name -eq 'AsTask' -and $_.GetParameters().Count -eq 1 -and $_.GetParameters()[0].ParameterType.Name -eq 'IAsyncOperation`1'
})[0]

function Await($WinRtTask, $ResultType) {
    $asTask = $script:asTaskGeneric.MakeGenericMethod($ResultType)
    $netTask = $asTask.Invoke($null, @($WinRtTask))
    $netTask.Wait(-1) | Out-Null
    $netTask.Result
}

$script:OcrEngine = [Windows.Media.Ocr.OcrEngine]::TryCreateFromUserProfileLanguages()
if ($null -eq $script:OcrEngine) { throw "no OCR language available on this system" }

Add-Type @"
using System;
using System.Runtime.InteropServices;
public static class Win32 {
    [DllImport("user32.dll")] public static extern bool SetProcessDpiAwarenessContext(IntPtr value);
    [DllImport("user32.dll")] public static extern bool SetCursorPos(int x, int y);
    [DllImport("user32.dll")] public static extern void mouse_event(uint dwFlags, uint dx, uint dy, uint dwData, UIntPtr dwExtraInfo);
    [DllImport("user32.dll")] public static extern void keybd_event(byte bVk, byte bScan, uint dwFlags, UIntPtr dwExtraInfo);
    [DllImport("user32.dll")] public static extern bool SetForegroundWindow(IntPtr hWnd);
    [DllImport("user32.dll")] public static extern IntPtr GetForegroundWindow();
    [DllImport("user32.dll")] public static extern bool ShowWindow(IntPtr hWnd, int nCmdShow);
    [DllImport("user32.dll")] public static extern bool IsIconic(IntPtr hWnd);
}
"@

# DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2 = -4
[Win32]::SetProcessDpiAwarenessContext([IntPtr]::new(-4)) | Out-Null

$MOUSEEVENTF_LEFTDOWN = 0x0002
$MOUSEEVENTF_LEFTUP = 0x0004
$KEYEVENTF_KEYUP = 0x0002

$VK_MENU = 0x12
$SW_RESTORE = 9

function Focus-GameWindow($processName = "AoE2DE_s") {
    $proc = Get-Process -Name $processName -ErrorAction SilentlyContinue | Select-Object -First 1
    if (-not $proc -or $proc.MainWindowHandle -eq [IntPtr]::Zero) {
        Write-Host "Focus-GameWindow: no window handle found for $processName"
        return $false
    }
    $hwnd = $proc.MainWindowHandle
    if ([Win32]::IsIconic($hwnd)) { [Win32]::ShowWindow($hwnd, $SW_RESTORE) | Out-Null }
    if ([Win32]::GetForegroundWindow() -eq $hwnd) { return $true }
    # SetForegroundWindow is blocked by Windows' focus-stealing prevention unless
    # the calling process "recently" had input; tapping Alt is the standard
    # workaround to satisfy that check.
    [Win32]::keybd_event($VK_MENU, 0, 0, [UIntPtr]::Zero)
    [Win32]::keybd_event($VK_MENU, 0, $KEYEVENTF_KEYUP, [UIntPtr]::Zero)
    $result = [Win32]::SetForegroundWindow($hwnd)
    Start-Sleep -Milliseconds 150
    Write-Host "Focus-GameWindow: SetForegroundWindow returned $result, now foreground=$([Win32]::GetForegroundWindow() -eq $hwnd)"
    return $result
}

# Is the game the foreground window RIGHT NOW? Cheap (no OCR, no capture),
# so it is safe to call inside a poll loop.
#
# This exists because every click this driver sends goes to whatever window
# happens to be focused. If the user alt-tabs away mid-run - to type a
# message, to check something - the clicks land somewhere else entirely and
# the run reports the map "failed to generate". That is an automation bug
# wearing the costume of a script bug, and it has already cost this project
# a wrong conclusion (2026-08-08: a stock map was recorded as
# unable-to-generate-from-a-mod-directory when in fact the window had simply
# lost focus mid-capture). Never treat a failed action as evidence about the
# .rms without first ruling this out.
function Test-GameFocused($processName = "AoE2DE_s") {
    $proc = Get-Process -Name $processName -ErrorAction SilentlyContinue | Select-Object -First 1
    if (-not $proc -or $proc.MainWindowHandle -eq [IntPtr]::Zero) { return $false }
    return ([Win32]::GetForegroundWindow() -eq $proc.MainWindowHandle)
}

# Block until the game is foreground. Tries to take focus itself first (the
# normal case: nothing else wants it), but if that is refused it WAITS
# rather than clicking into another window or fighting the user for focus -
# the user may deliberately be doing something else, and a stalled batch is
# far cheaper than a batch full of bogus failures.
function Wait-ForGameFocus($timeoutMs = 600000, $pollMs = 1000, $refocusEveryMs = 15000) {
    if (Test-GameFocused) { return $true }
    $sw = [System.Diagnostics.Stopwatch]::StartNew()
    $lastTry = -$refocusEveryMs
    while ($sw.ElapsedMilliseconds -lt $timeoutMs) {
        if ($sw.ElapsedMilliseconds - $lastTry -ge $refocusEveryMs) {
            $lastTry = $sw.ElapsedMilliseconds
            Focus-GameWindow | Out-Null
            if (Test-GameFocused) {
                Write-Host "FOCUS_REGAINED after $($sw.ElapsedMilliseconds)ms"
                return $true
            }
            Write-Host "WAITING_FOR_FOCUS game is not foreground ($($sw.ElapsedMilliseconds)ms) - pausing"
        }
        Start-Sleep -Milliseconds $pollMs
        if (Test-GameFocused) {
            Write-Host "FOCUS_REGAINED after $($sw.ElapsedMilliseconds)ms"
            return $true
        }
    }
    Write-Host "FOCUS_TIMEOUT game never became foreground within ${timeoutMs}ms"
    return $false
}

function Move-CursorSmooth($x, $y, $steps = 15, $delayMs = 8) {
    $start = [System.Windows.Forms.Cursor]::Position
    for ($i = 1; $i -le $steps; $i++) {
        $fx = [int]($start.X + ($x - $start.X) * $i / $steps)
        $fy = [int]($start.Y + ($y - $start.Y) * $i / $steps)
        [Win32]::SetCursorPos($fx, $fy) | Out-Null
        Start-Sleep -Milliseconds $delayMs
    }
    [Win32]::SetCursorPos($x, $y) | Out-Null
}

function Click-At($x, $y, $smooth = $true) {
    if ($smooth) { Move-CursorSmooth $x $y } else { [Win32]::SetCursorPos($x, $y) | Out-Null }
    Start-Sleep -Milliseconds 60
    Click-Repeat
}

# Fires a click at wherever the cursor already is - no movement, for tight
# retry loops where the cursor is already correctly positioned.
function Click-Repeat {
    [Win32]::mouse_event($MOUSEEVENTF_LEFTDOWN, 0, 0, 0, [UIntPtr]::Zero)
    Start-Sleep -Milliseconds 90
    [Win32]::mouse_event($MOUSEEVENTF_LEFTUP, 0, 0, 0, [UIntPtr]::Zero)
}

# A repeated click at the EXACT same pixel with no movement in between can
# get merged by Windows' double-click detection, or otherwise fail to
# register as two discrete clicks - jitter the cursor a couple pixels and
# back between retry attempts to avoid that.
function Jitter-Cursor($x, $y) {
    [Win32]::SetCursorPos($x + 2, $y + 2) | Out-Null
    Start-Sleep -Milliseconds 30
    [Win32]::SetCursorPos($x, $y) | Out-Null
    Start-Sleep -Milliseconds 30
}

function Save-Screenshot($path) {
    $screens = [System.Windows.Forms.Screen]::AllScreens
    $bounds = [System.Drawing.Rectangle]::Empty
    foreach ($s in $screens) { $bounds = [System.Drawing.Rectangle]::Union($bounds, $s.Bounds) }
    $bmp = New-Object System.Drawing.Bitmap $bounds.Width, $bounds.Height
    $g = [System.Drawing.Graphics]::FromImage($bmp)
    $g.CopyFromScreen($bounds.Location, [System.Drawing.Point]::Empty, $bounds.Size)
    $bmp.Save($path, [System.Drawing.Imaging.ImageFormat]::Png)
    $g.Dispose(); $bmp.Dispose()
    return "$($bounds.Width)x$($bounds.Height)"
}

# Also available for cheap, non-OCR checks (e.g. distinguishing terrain
# colors) where a full OCR pass would be overkill.
function Get-RegionAvgColor($rect, $stride = 3) {
    $bmp = New-Object System.Drawing.Bitmap $rect.Width, $rect.Height
    $g = [System.Drawing.Graphics]::FromImage($bmp)
    $g.CopyFromScreen($rect.Location, [System.Drawing.Point]::Empty, $rect.Size)
    $rSum = 0L; $gSum = 0L; $bSum = 0L; $count = 0
    for ($x = 0; $x -lt $rect.Width; $x += $stride) {
        for ($y = 0; $y -lt $rect.Height; $y += $stride) {
            $p = $bmp.GetPixel($x, $y)
            $rSum += $p.R; $gSum += $p.G; $bSum += $p.B
            $count++
        }
    }
    $g.Dispose(); $bmp.Dispose()
    return @($rSum / $count, $gSum / $count, $bSum / $count)
}

# Captures a screen region and runs it through Windows' built-in OCR engine.
# $binarize matters: light-on-dark UI text (like the Seed box) reads as
# empty from the OCR engine untouched - thresholding to pure black/white
# text fixes it. Dark-on-light regions (like the Menu title) don't need it.
function Ocr-Rect($rect, $scale = 3, $binarize = $false) {
    $bmp = New-Object System.Drawing.Bitmap $rect.Width, $rect.Height
    $g = [System.Drawing.Graphics]::FromImage($bmp)
    $g.CopyFromScreen($rect.Location, [System.Drawing.Point]::Empty, $rect.Size)
    $g.Dispose()

    if ($binarize) {
        $lo = 255.0; $hi = 0.0
        $lum = New-Object 'double[,]' $bmp.Width, $bmp.Height
        for ($x = 0; $x -lt $bmp.Width; $x++) {
            for ($y = 0; $y -lt $bmp.Height; $y++) {
                $p = $bmp.GetPixel($x, $y)
                $l = 0.299 * $p.R + 0.587 * $p.G + 0.114 * $p.B
                $lum[$x, $y] = $l
                if ($l -lt $lo) { $lo = $l }
                if ($l -gt $hi) { $hi = $l }
            }
        }
        $mid = ($lo + $hi) / 2.0
        for ($x = 0; $x -lt $bmp.Width; $x++) {
            for ($y = 0; $y -lt $bmp.Height; $y++) {
                # text is the lighter value in this UI; light->black, dark->white
                # gives dark-text-on-light-background, which OCR expects.
                if ($lum[$x, $y] -gt $mid) {
                    $bmp.SetPixel($x, $y, [System.Drawing.Color]::Black)
                } else {
                    $bmp.SetPixel($x, $y, [System.Drawing.Color]::White)
                }
            }
        }
    }

    if ($scale -ne 1) {
        $scaled = New-Object System.Drawing.Bitmap ($rect.Width * $scale), ($rect.Height * $scale)
        $g2 = [System.Drawing.Graphics]::FromImage($scaled)
        $g2.InterpolationMode = [System.Drawing.Drawing2D.InterpolationMode]::HighQualityBicubic
        $g2.DrawImage($bmp, 0, 0, $scaled.Width, $scaled.Height)
        $g2.Dispose(); $bmp.Dispose()
        $bmp = $scaled
    }

    $ms = New-Object System.IO.MemoryStream
    $bmp.Save($ms, [System.Drawing.Imaging.ImageFormat]::Png)
    $bytes = $ms.ToArray()
    $bmp.Dispose(); $ms.Dispose()

    $ras = New-Object Windows.Storage.Streams.InMemoryRandomAccessStream
    $writer = New-Object Windows.Storage.Streams.DataWriter $ras
    $writer.WriteBytes($bytes)
    Await ($writer.StoreAsync()) ([uint32]) | Out-Null
    $ras.Seek(0)

    $decoder = Await ([Windows.Graphics.Imaging.BitmapDecoder]::CreateAsync($ras)) ([Windows.Graphics.Imaging.BitmapDecoder])
    $softwareBitmap = Await ($decoder.GetSoftwareBitmapAsync()) ([Windows.Graphics.Imaging.SoftwareBitmap])
    $result = Await ($script:OcrEngine.RecognizeAsync($softwareBitmap)) ([Windows.Media.Ocr.OcrResult])
    return $result.Text
}

$SEED_RECT = New-Object System.Drawing.Rectangle 0, 1000, 220, 40
$MENU_TITLE_RECT = New-Object System.Drawing.Rectangle 770, 310, 360, 40

function Read-Seed {
    return (Ocr-Rect $SEED_RECT 3 $true).Trim()
}

# ---------------------------------------------------------------------------
# Watching the engine work, instead of watching the screen.
#
# Every Read-Seed is a GDI CopyFromScreen against a fullscreen Direct3D
# application plus a WinRT OCR call, and the generation poll below used to
# do one every ~250-500ms for as long as generation took - a couple of
# hundred screen-DC reads into the game while it is busy. That is the most
# invasive thing this driver does and it is not a click, so it is worth
# not doing it during the one window where the engine is under load.
#
# CPU is a cheaper and more direct signal: the process's own accumulated
# processor time, which costs a process-table read and touches nothing the
# game owns. It cannot say WHICH map generated - only the seed can do that
# - so it is used purely to decide *when to look*, and Read-Seed remains
# the authority on whether generation actually happened.
#
# Thresholds are relative to a baseline measured immediately beforehand,
# because the editor is not idle at rest: it renders continuously, so there
# is no absolute "busy" number that is portable across machines or even
# across window sizes.
# ---------------------------------------------------------------------------

function Get-GameCpuSeconds($processName = "AoE2DE_s") {
    $p = Get-Process -Name $processName -ErrorAction SilentlyContinue
    if (-not $p) { return $null }
    return [double](($p | Measure-Object -Property CPU -Sum).Sum)
}

# Cores' worth of CPU the game is using right now, sampled over $ms.
function Measure-GameCpuLoad($ms = 600, $processName = "AoE2DE_s") {
    $a = Get-GameCpuSeconds $processName
    if ($null -eq $a) { return $null }
    $sw = [System.Diagnostics.Stopwatch]::StartNew()
    Start-Sleep -Milliseconds $ms
    $b = Get-GameCpuSeconds $processName
    if ($null -eq $b) { return $null }
    $elapsed = $sw.Elapsed.TotalSeconds
    if ($elapsed -le 0) { return $null }
    return ($b - $a) / $elapsed
}

# Wait for a burst of engine work to start and then finish.
#
# Returns "quiet" if it saw the load rise above the baseline and come back
# down, "busy" if it rose and never settled inside $maxBusyMs, and "none"
# if it never rose at all - which is also what a machine where this signal
# does not work looks like, so callers must treat "none" as "no
# information" and fall back rather than as "nothing happened".
function Wait-ForCpuBurst($baseline, $startTimeoutMs = 8000, $maxBusyMs = 180000,
                          $pollMs = 250, $quietPolls = 3, $margin = 0.35,
                          $processName = "AoE2DE_s") {
    if ($null -eq $baseline) { return "none" }
    $busyAt = $baseline + $margin
    $sw = [System.Diagnostics.Stopwatch]::StartNew()
    $started = $false
    $quiet = 0
    while ($true) {
        $load = Measure-GameCpuLoad $pollMs $processName
        if ($null -eq $load) { return "none" }   # the game went away
        if (-not $started) {
            if ($load -ge $busyAt) {
                $started = $true
                Write-Host "CPU burst started (load=$([math]::Round($load,2)) baseline=$([math]::Round($baseline,2)))"
            } elseif ($sw.ElapsedMilliseconds -ge $startTimeoutMs) {
                return "none"
            }
        } else {
            if ($load -lt $busyAt) {
                $quiet++
                if ($quiet -ge $quietPolls) {
                    Write-Host "CPU burst done after $($sw.ElapsedMilliseconds)ms"
                    return "quiet"
                }
            } else {
                $quiet = 0
            }
            if ($sw.ElapsedMilliseconds -ge $maxBusyMs) { return "busy" }
        }
    }
}

function Test-MenuOpen {
    $text = Ocr-Rect $MENU_TITLE_RECT 1 $false
    return $text -match "Main Menu"
}

# Block until the Main Menu overlay is actually on screen.
#
# This replaces a fixed 200ms sleep between clicking Menu and clicking
# Save, and the sleep was not merely optimistic - it was aimed at a
# coordinate that means something else when it is wrong. SAVE_BTN is
# (960, 436), which with no overlay up is the middle of the MAP, and a
# click on the map in the Scenario Editor is a brush stroke: it paints
# terrain or drops a unit. So a menu that had not finished laying out did
# not produce a harmless missed click, it produced an edit to the scenario
# and an unknown amount of engine work, silently.
#
# The user's observation is what surfaced this: the Save overlay is never
# actually visible during a run, which is not what 200ms at 60Hz should
# look like.
#
# Costing this is fair: it is an OCR per poll, the same expense the
# generation loop was just moved off. The difference is when. This runs
# while the engine is idle between generations, not while it is under
# load, and it replaces a click that could be landing on the map.
function Wait-ForMenuOpen($timeoutMs = 6000, $pollMs = 150) {
    $sw = [System.Diagnostics.Stopwatch]::StartNew()
    while ($sw.ElapsedMilliseconds -lt $timeoutMs) {
        if (Test-MenuOpen) {
            Write-Host "menu open after $($sw.ElapsedMilliseconds)ms"
            return $true
        }
        Start-Sleep -Milliseconds $pollMs
    }
    Write-Host "menu did NOT open within ${timeoutMs}ms"
    return $false
}

# Clicks Generate Map and waits for the Seed box (via OCR) to prove the map
# actually regenerated, rather than assuming a single click worked.
#
# CLICK ONCE, THEN WAIT. An earlier version fired a click on every poll
# iteration, on the theory that Generate's click "sometimes doesn't register"
# and that re-clicking is harmless. Measured directly (2026-08-08): a single
# click always registers, but the Seed box does not update until generation
# FINISHES, which takes ~3.2s for a 240x240 map - the UI is blocked
# throughout. So the old loop's extra clicks were not free: each one landed
# mid-generation and re-triggered generation from the top, pushing the seed
# update further away every time it polled. That is why it would report
# GENERATE_FAILED on scripts that generate perfectly well by hand, and why
# it "succeeded on attempt 6" when it did - it was racing itself. Being
# patient between clicks is both more reliable AND faster.
#
# $clickBudgetMs is wall-clock per click (measured on a stopwatch, not by
# summing sleeps - each Read-Seed OCR costs a few hundred ms of its own).
# $clickBudgetMs defaults generously because STOCK maps are far slower to
# generate than this project's own scripts - ours finish in ~3s, a stock
# script with the full System A include chain can take much longer. Waiting
# costs nothing when generation is fast (the poll exits as soon as the seed
# moves); guessing too low silently reports a working map as broken.
function Click-GenerateMapVerified($genX, $genY, $maxClicks = 3, $pollMs = 250, $clickBudgetMs = 90000) {
    $sw = [System.Diagnostics.Stopwatch]::StartNew()
    if (-not (Wait-ForGameFocus)) { return $false }
    Move-CursorSmooth $genX $genY
    $before = Read-Seed
    for ($i = 1; $i -le $maxClicks; $i++) {
        if ($i -gt 1) { Jitter-Cursor $genX $genY }
        # Baseline the engine's load with the click not yet sent, so the
        # burst is measured against how busy this machine's editor is right
        # now rather than against a number baked in somewhere.
        $idle = Measure-GameCpuLoad 600
        Click-Repeat
        # Wait for generation by watching the process, not the screen. The
        # OCR poll below is a screen-DC read plus a WinRT OCR call each
        # time, and it used to run right through generation - a couple of
        # hundred of them into a fullscreen D3D application while the engine
        # is under load, which is the most invasive thing this driver does
        # and is not a click. This makes the poll below almost always
        # succeed on its first read instead.
        #
        # A "none" result means the signal told us nothing - the process
        # vanished, or its load never rose above the baseline - so it falls
        # through to polling exactly as before. This gates the OCR; it never
        # replaces it, because CPU cannot say WHICH map generated and the
        # seed can.
        $burst = Wait-ForCpuBurst $idle 8000 $clickBudgetMs
        if ($burst -eq "quiet") {
            Start-Sleep -Milliseconds 250   # let the seed box repaint
        } elseif ($burst -eq "none") {
            Write-Host "click=$i no CPU burst seen - falling back to polling"
        }
        $deadline = $sw.ElapsedMilliseconds + $clickBudgetMs
        $lostFocus = $false
        while ($sw.ElapsedMilliseconds -lt $deadline) {
            Start-Sleep -Milliseconds $pollMs
            # Focus loss invalidates this attempt: the click may never have
            # reached the game, and the screenshot OCR below may be reading
            # a window that is not even the game. Recover focus, then retry
            # the click rather than recording a bogus failure.
            if (-not (Test-GameFocused)) {
                Write-Host "click=$i FOCUS LOST mid-wait - attempt is void, will re-click"
                if (-not (Wait-ForGameFocus)) { return $false }
                Move-CursorSmooth $genX $genY
                $before = Read-Seed   # re-baseline: the map may have generated
                $lostFocus = $true
                break
            }
            $after = Read-Seed
            if ($after -ne $before -and $after -ne "") {
                Write-Host "GENERATE_OK click=$i seed=$after elapsed=$($sw.ElapsedMilliseconds)ms"
                return $true
            }
        }
        if (-not $lostFocus) {
            Write-Host "click=$i no seed change after ${clickBudgetMs}ms - reclicking"
        }
    }
    Write-Host "GENERATE_FAILED after=$maxClicks clicks (seed stayed '$before') elapsed=$($sw.ElapsedMilliseconds)ms"
    return $false
}

# Save (inside the Menu overlay) has the same intermittent no-registration
# problem. The Menu closing is necessary but NOT sufficient evidence a real
# save happened (observed empirically: menu-closed was reported after a
# flurry of prior Generate-retry clicks with no new file on disk - cause
# not fully understood, possibly a capture/OCR reliability issue under
# rapid repeated calls). Require BOTH the menu to close AND a newer
# .aoe2scenario file to appear before declaring success.
function Newest-Scenario($scenarioDir) {
    Get-ChildItem -Path $scenarioDir -Filter "*.aoe2scenario" |
        Sort-Object LastWriteTime -Descending | Select-Object -First 1
}

# The file is the ground-truth signal for a real save (stronger evidence
# than OCR's Menu-closed read - see the "menu closed but no file" case
# below), so poll for it FIRST on every attempt instead of gating file-
# checking behind an OCR read. OCR is only consulted after the file poll
# budget elapses, purely to decide *why* (still open -> click again; closed
# without a file -> the known false-close case -> reopen) - it never sits
# on the fast path, so a normal successful save costs zero OCR calls.
function Click-SaveVerified($saveX, $saveY, $menuX, $menuY, $scenarioDir, $beforeTime, $maxAttempts = 10, $pollMs = 150, $fileBudgetMs = 1200, $fileStepMs = 150) {
    $sw = [System.Diagnostics.Stopwatch]::StartNew()
    if (-not (Wait-ForGameFocus)) { return $false }
    # Never click Save on faith. Until the overlay is up, this coordinate
    # is the map, and clicking the map paints it.
    if (-not (Wait-ForMenuOpen)) {
        Write-Host "menu not open - clicking Menu again before trying Save"
        Click-At $menuX $menuY
        if (-not (Wait-ForMenuOpen)) {
            Write-Host "SAVE_ABORTED menu never opened - refusing to click into the map at $saveX,$saveY"
            return $false
        }
    }
    Move-CursorSmooth $saveX $saveY
    for ($i = 1; $i -le $maxAttempts; $i++) {
        if (-not (Test-GameFocused)) {
            if (-not (Wait-ForGameFocus)) { return $false }
            Move-CursorSmooth $saveX $saveY
        }
        if ($i -gt 1) { Jitter-Cursor $saveX $saveY }
        Click-Repeat
        Start-Sleep -Milliseconds $pollMs
        $waited = 0
        while ($waited -lt $fileBudgetMs) {
            $newest = Newest-Scenario $scenarioDir
            if ($newest -and $newest.LastWriteTime -gt $beforeTime) {
                Write-Host "SAVE_OK attempt=$i file=$($newest.Name) wait=$($waited)ms elapsed=$($sw.ElapsedMilliseconds)ms"
                return $true
            }
            Start-Sleep -Milliseconds $fileStepMs
            $waited += $fileStepMs
        }
        # No file yet - only now pay for an OCR read, to tell an in-progress
        # click (menu still open, just keep clicking) from the known false-
        # close case (menu closed but nothing saved - needs a reopen before
        # Save will respond again).
        if (Test-MenuOpen) { continue }
        Write-Host "attempt=$i menu closed but no new file after ${fileBudgetMs}ms - reopening and retrying"
        Click-At $menuX $menuY
        if (-not (Wait-ForMenuOpen)) {
            Write-Host "SAVE_FAILED menu would not reopen"
            return $false
        }
        Move-CursorSmooth $saveX $saveY
    }
    Write-Host "SAVE_FAILED after=$maxAttempts elapsed=$($sw.ElapsedMilliseconds)ms"
    return $false
}

# Only resets via Cancel if the Menu is actually stuck open (e.g. left over
# from a prior failed/partial run) - not a routine step.
function Reset-IfMenuStuck($cancelX, $cancelY) {
    if (Test-MenuOpen) {
        Write-Host "Menu was stuck open - clicking Cancel to reset"
        Click-At $cancelX $cancelY
        Start-Sleep -Milliseconds 200
    }
}

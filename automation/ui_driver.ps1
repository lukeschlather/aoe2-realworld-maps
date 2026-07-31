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

function Test-MenuOpen {
    $text = Ocr-Rect $MENU_TITLE_RECT 1 $false
    return $text -match "Main Menu"
}

# Moves the cursor onto the button ONCE, then fires clicks in a tight loop
# without re-moving - clicking Generate Map repeatedly is harmless (just
# re-rolls the seed), and Generate Map's click is known to sometimes not
# register at all, so this polls the Seed box (via OCR) for a change rather
# than assuming a single click worked.
function Click-GenerateMapVerified($genX, $genY, $maxAttempts = 10, $pollMs = 150) {
    $sw = [System.Diagnostics.Stopwatch]::StartNew()
    Move-CursorSmooth $genX $genY
    $before = Read-Seed
    $tSeed = $sw.ElapsedMilliseconds
    for ($i = 1; $i -le $maxAttempts; $i++) {
        if ($i -gt 1) { Jitter-Cursor $genX $genY }
        Click-Repeat
        Start-Sleep -Milliseconds $pollMs
        $after = Read-Seed
        if ($after -ne $before -and $after -ne "") {
            Write-Host "GENERATE_OK attempt=$i seed=$after elapsed=$($sw.ElapsedMilliseconds)ms first_ocr=${tSeed}ms"
            return $true
        }
    }
    Write-Host "GENERATE_FAILED after=$maxAttempts (seed stayed '$before') elapsed=$($sw.ElapsedMilliseconds)ms"
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
    Move-CursorSmooth $saveX $saveY
    for ($i = 1; $i -le $maxAttempts; $i++) {
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
        Start-Sleep -Milliseconds 300
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

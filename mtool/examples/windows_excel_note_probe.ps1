<#
.SYNOPSIS
Read one hidden mTool note cell through real Windows Excel and optionally save
and reopen a copy.

.DESCRIPTION
This is a Windows-only investigation helper. It never saves over the source.
Excel opens the workbook read-only, reports the actual .Value2 UTF-16 length
that Excel exposes for the hidden +FootnoteTexts cell, and (when -SaveCopyAs is
provided) writes a separate copy through Excel, reopens it, and measures again.

DisplayAlerts remains ON and Excel is visible by default. If Excel shows a
repair dialog, capture a screenshot and accept/close it; the script will resume
after the dialog. Use dummy/test filings only.

.EXAMPLE
powershell -ExecutionPolicy Bypass -File `
  mtool\examples\windows_excel_note_probe.ps1 `
  -Workbook C:\recon\probe-32768.xlsx `
  -Sheet +FootnoteTexts -Cell C14 `
  -SaveCopyAs C:\recon\probe-32768.excel-roundtrip.xlsx `
  -JsonOut C:\recon\probe-32768.excel.json
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$Workbook,

    [Parameter(Mandatory = $true)]
    [string]$Sheet,

    [Parameter(Mandatory = $true)]
    [ValidatePattern('^[A-Za-z]{1,3}[1-9][0-9]*$')]
    [string]$Cell,

    [string]$SaveCopyAs,
    [string]$JsonOut,
    [switch]$Headless
)

$ErrorActionPreference = 'Stop'

function Release-ComObject([object]$Object) {
    if ($null -ne $Object -and [System.Runtime.InteropServices.Marshal]::IsComObject($Object)) {
        [void][System.Runtime.InteropServices.Marshal]::FinalReleaseComObject($Object)
    }
}

function Read-NoteCell([object]$Excel, [string]$Path, [string]$SheetName, [string]$Address) {
    $book = $null
    $worksheet = $null
    $range = $null
    try {
        # UpdateLinks=0, ReadOnly=$true. Source is never changed.
        $book = $Excel.Workbooks.Open($Path, 0, $true)
        $worksheet = $book.Worksheets.Item($SheetName)
        $range = $worksheet.Range($Address)
        $value = if ($null -eq $range.Value2) { '' } else { [string]$range.Value2 }
        return @{
            workbook = $Path
            sheet = $SheetName
            cell = $Address
            excel_value_utf16_units = $value.Length
            value_prefix = $value.Substring(0, [Math]::Min(80, $value.Length))
            open_succeeded = $true
        }
    }
    finally {
        if ($null -ne $book) { $book.Close($false) }
        Release-ComObject $range
        Release-ComObject $worksheet
        Release-ComObject $book
    }
}

$source = [System.IO.Path]::GetFullPath($Workbook)
if (-not [System.IO.File]::Exists($source)) {
    throw "Workbook not found: $source"
}

$destination = $null
if ($SaveCopyAs) {
    $destination = [System.IO.Path]::GetFullPath($SaveCopyAs)
    if ($destination -eq $source) {
        throw 'Refusing to save over the source workbook.'
    }
    if ([System.IO.File]::Exists($destination)) {
        throw "Refusing to overwrite existing SaveCopyAs output: $destination"
    }
}
$cellAddress = $Cell.ToUpperInvariant()

$excel = $null
$bookForCopy = $null
$result = [ordered]@{
    schema = 'windows-excel-note-probe/v1'
    source = $source
    source_sha256 = (Get-FileHash -Algorithm SHA256 -LiteralPath $source).Hash.ToLowerInvariant()
    sheet = $Sheet
    cell = $cellAddress
    save_copy_as = $destination
    started_at = (Get-Date).ToUniversalTime().ToString('o')
    excel = $null
    before = $null
    after = $null
    error = $null
}

try {
    $excel = New-Object -ComObject Excel.Application
    $excel.Visible = -not $Headless.IsPresent
    # Keep repair/security warnings visible; they are evidence, not noise.
    $excel.DisplayAlerts = $true
    $result.excel = @{
        version = [string]$excel.Version
        build = [string]$excel.Build
        operating_system = [string]$excel.OperatingSystem
    }

    $result.before = Read-NoteCell $excel $source $Sheet $cellAddress

    if ($destination) {
        # Reopen read-only and ask Excel itself to serialise a separate copy.
        # The original remains byte-for-byte untouched.
        $bookForCopy = $excel.Workbooks.Open($source, 0, $true)
        $bookForCopy.SaveCopyAs($destination)
        $bookForCopy.Close($false)
        Release-ComObject $bookForCopy
        $bookForCopy = $null

        $result.after = Read-NoteCell $excel $destination $Sheet $cellAddress
        $result.after_sha256 = (Get-FileHash -Algorithm SHA256 -LiteralPath $destination).Hash.ToLowerInvariant()
    }
}
catch {
    $result.error = "{0}: {1}" -f $_.Exception.GetType().FullName, $_.Exception.Message
}
finally {
    if ($null -ne $bookForCopy) {
        $bookForCopy.Close($false)
        Release-ComObject $bookForCopy
    }
    if ($null -ne $excel) {
        $excel.Quit()
        Release-ComObject $excel
    }
    [GC]::Collect()
    [GC]::WaitForPendingFinalizers()
    $result.finished_at = (Get-Date).ToUniversalTime().ToString('o')
}

$json = $result | ConvertTo-Json -Depth 8
if ($JsonOut) {
    $jsonPath = [System.IO.Path]::GetFullPath($JsonOut)
    $parent = [System.IO.Path]::GetDirectoryName($jsonPath)
    if ($parent) { [System.IO.Directory]::CreateDirectory($parent) | Out-Null }
    [System.IO.File]::WriteAllText($jsonPath, $json, [System.Text.UTF8Encoding]::new($false))
}
$json

if ($result.error) { exit 2 }
exit 0

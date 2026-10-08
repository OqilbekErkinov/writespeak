<#
WriteSpeak: copies the server's daily backups to this PC.
Run by Task Scheduler ("WriteSpeak backup pull"): daily at 12:00, and as soon
as possible after a missed run (PC was off). Installed copy lives next to the
backups in D:\Backups\WriteSpeak; the source is deploy/backup/pull-backups.ps1
in the repo.

  Server: bridgin:/opt/writespeak_backups/daily/<YYYYmmdd-HHMM>/  (complete dirs only)
  PC:     D:\Backups\WriteSpeak\daily\<same name>     newest 30 kept
          D:\Backups\WriteSpeak\monthly\<YYYY-MM>     first backup of each month, kept forever

Every copy is checked against its SHA256SUMS before it counts. Log: pull.log.
On failure, or if the newest server backup is over 2 days old, a pop-up
message is shown (the server also alerts admins on Telegram when a backup fails).
#>
$ErrorActionPreference = 'Stop'

$Root       = 'D:\Backups\WriteSpeak'
$RemoteHost = 'bridgin'
$RemoteDir  = '/opt/writespeak_backups/daily'
$KeepDaily  = 30
$Ssh        = "$env:WINDIR\System32\OpenSSH\ssh.exe"
$Scp        = "$env:WINDIR\System32\OpenSSH\scp.exe"
$Log        = Join-Path $Root 'pull.log'
$NamePattern = '^\d{8}-\d{4}$'

function Write-Log([string]$Message) {
    "$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss') $Message" | Add-Content -Path $Log -Encoding UTF8
}

function Show-Alert([string]$Message) {
    try { & msg.exe $env:USERNAME /TIME:0 "WriteSpeak zaxira: $Message" } catch { }
}

function Get-BackupDirs([string]$Path) {
    Get-ChildItem -Path $Path -Directory | Where-Object { $_.Name -match $NamePattern } | Sort-Object Name
}

New-Item -ItemType Directory -Force -Path (Join-Path $Root 'daily'), (Join-Path $Root 'monthly') | Out-Null

try {
    # No 2>&1 here: with ErrorActionPreference=Stop, Windows PowerShell turns any
    # stderr line of a native command into a terminating error.
    $listing = & $Ssh -o BatchMode=yes -o ConnectTimeout=20 $RemoteHost "ls -1 $RemoteDir"
    if ($LASTEXITCODE -ne 0) { throw "serverga ulanib bo'lmadi (ssh exit $LASTEXITCODE)" }
    $remote = @($listing | Where-Object { $_ -match $NamePattern } | Sort-Object)
    if ($remote.Count -eq 0) { throw "serverda hali zaxira yo'q" }

    # 1. Download what we don't have yet, verifying checksums.
    $pulled = 0
    foreach ($name in $remote) {
        $target = Join-Path $Root "daily\$name"
        if (Test-Path $target) { continue }
        $tmp = "$target.tmp"
        if (Test-Path $tmp) { Remove-Item -Recurse -Force $tmp }
        New-Item -ItemType Directory -Path $tmp | Out-Null

        & $Scp -o BatchMode=yes -q "${RemoteHost}:$RemoteDir/$name/*" $tmp
        if ($LASTEXITCODE -ne 0) { throw "$name yuklab olinmadi (scp)" }

        foreach ($line in Get-Content (Join-Path $tmp 'SHA256SUMS')) {
            if (-not $line.Trim()) { continue }
            $expected, $file = $line -split '\s+', 2
            $file = $file.TrimStart('*')
            $actual = (Get-FileHash -Algorithm SHA256 -Path (Join-Path $tmp $file)).Hash.ToLower()
            if ($actual -ne $expected) { throw "$name/$file buzilgan (checksum mos emas)" }
        }
        Rename-Item -Path $tmp -NewName $name
        $pulled++
        Write-Log "pulled $name"
    }

    # 2. Keep the first backup of every month forever.
    foreach ($dir in Get-BackupDirs (Join-Path $Root 'daily')) {
        $month = $dir.Name.Substring(0, 4) + '-' + $dir.Name.Substring(4, 2)
        $monthDir = Join-Path $Root "monthly\$month"
        if (-not (Test-Path $monthDir)) {
            Copy-Item -Recurse -Path $dir.FullName -Destination $monthDir
            Write-Log "monthly $month <- $($dir.Name)"
        }
    }

    # 3. Keep the newest $KeepDaily daily copies.
    $daily = @(Get-BackupDirs (Join-Path $Root 'daily'))
    if ($daily.Count -gt $KeepDaily) {
        $daily | Select-Object -First ($daily.Count - $KeepDaily) | ForEach-Object {
            Remove-Item -Recurse -Force $_.FullName
            Write-Log "pruned $($_.Name)"
        }
    }

    # 4. Is the server still producing backups?
    $newest = $remote[-1]
    $newestTime = [datetime]::ParseExact($newest, 'yyyyMMdd-HHmm', $null)
    if (((Get-Date) - $newestTime).TotalHours -gt 48) {
        Write-Log "WARNING newest server backup is $newest (over 2 days old)"
        Show-Alert "serverdagi eng yangi zaxira $newest - 2 kundan eski. Serverni tekshiring."
    }

    Write-Log "OK pulled=$pulled local=$((Get-BackupDirs (Join-Path $Root 'daily')).Count) newest=$newest"
    Set-Content -Path (Join-Path $Root 'LAST_OK.txt') -Value "$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss') newest=$newest" -Encoding UTF8
}
catch {
    Write-Log "FAILED $($_.Exception.Message)"
    Show-Alert "kompyuterga ko'chirish xato berdi: $($_.Exception.Message). Batafsil: $Log"
    exit 1
}

# 生成 gxyFi 的 Windows 安装包：先打出程序目录，再用 Inno Setup 做成安装程序。
# 用法：在 gxyFi 目录执行  .\build-installer.ps1
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

$version = ([regex]::Match((Get-Content "gxyfi\__init__.py" -Raw), '__version__ = "([^"]+)"')).Groups[1].Value
if (-not $version) { throw "gxyfi\__init__.py 里没有版本号" }
$parts = @($version.Split("."))
while ($parts.Count -lt 4) { $parts += "0" }
$filevers = ($parts[0..3] -join ", ")

$versionInfo = @"
VSVersionInfo(
  ffi=FixedFileInfo(
    filevers=($filevers),
    prodvers=($filevers),
    mask=0x3f,
    flags=0x0,
    OS=0x40004,
    fileType=0x1,
    subtype=0x0,
    date=(0, 0)
  ),
  kids=[
    StringFileInfo([
      StringTable(
        "040904b0",
        [
          StringStruct("CompanyName", "gxyFi"),
          StringStruct("FileDescription", "gxyFi"),
          StringStruct("FileVersion", "$version"),
          StringStruct("InternalName", "gxyFi"),
          StringStruct("OriginalFilename", "gxyFi.exe"),
          StringStruct("ProductName", "gxyFi"),
          StringStruct("ProductVersion", "$version"),
        ],
      ),
      StringTable(
        "080404b0",
        [
          StringStruct("CompanyName", "gxyFi"),
          StringStruct("FileDescription", "gxyFi 家庭资产台账"),
          StringStruct("FileVersion", "$version"),
          StringStruct("InternalName", "gxyFi"),
          StringStruct("OriginalFilename", "gxyFi.exe"),
          StringStruct("ProductName", "gxyFi"),
          StringStruct("ProductVersion", "$version"),
        ],
      ),
    ]),
    VarFileInfo([VarStruct("Translation", [1033, 1200]), VarStruct("Translation", [2052, 1200])]),
  ],
)
"@
$utf8 = New-Object System.Text.UTF8Encoding $false
[System.IO.File]::WriteAllText((Join-Path $PSScriptRoot "installer\version_info.txt"), $versionInfo, $utf8)

Get-Process gxyFi -ErrorAction SilentlyContinue | Stop-Process -Force
& ".\.venv\Scripts\pyinstaller.exe" --noconfirm --clean "gxyFi.spec"
if ($LASTEXITCODE -ne 0) { throw "打包程序失败" }

$iscc = @(
    "$env:LOCALAPPDATA\Programs\Inno Setup 6\ISCC.exe",
    "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe",
    "$env:ProgramFiles\Inno Setup 6\ISCC.exe"
) | Where-Object { Test-Path $_ } | Select-Object -First 1
if (-not $iscc) { throw "没有找到 Inno Setup。可以用 winget install JRSoftware.InnoSetup 安装。" }

& $iscc "/DMyAppVersion=$version" "installer\gxyFi.iss"
if ($LASTEXITCODE -ne 0) { throw "生成安装程序失败" }

Write-Host "安装包：dist\gxyFi-Setup-$version.exe"

# 原生 C++20 南方CASS TIN 土方工作站 Windows 一键构建与测试脚本
param(
    [string]$BuildType = "Release",
    [switch]$RunDemo
)

$ErrorActionPreference = "Stop"
$RepoRoot = Split-Path -Parent $PSScriptRoot
$CppDir = $PSScriptRoot
$BuildDir = Join-Path $CppDir "build"

if (Test-Path "C:\DevTools\mingw64\bin") {
    $env:PATH = "C:\DevTools\mingw64\bin;$env:PATH"
}

$UseNinja = (Get-Command ninja -ErrorAction SilentlyContinue) -and (Get-Command g++ -ErrorAction SilentlyContinue)
if ($UseNinja) {
    Write-Host "[1/3] 使用 MinGW-w64 + Ninja 配置 ($BuildType)..." -ForegroundColor Cyan
    cmake -S $CppDir -B $BuildDir -G Ninja "-DCMAKE_BUILD_TYPE=$BuildType"
    Write-Host "[2/3] 编译核心库、工作站与单元测试..." -ForegroundColor Cyan
    cmake --build $BuildDir
    Write-Host "[3/3] 运行 CTest 核心单元测试..." -ForegroundColor Cyan
    ctest --test-dir $BuildDir --output-on-failure
    $ExePath = Join-Path $BuildDir "tin_earthwork_cass.exe"
} else {
    Write-Host "[1/3] 使用 MSVC x64 配置 ($BuildType)..." -ForegroundColor Cyan
    cmake -S $CppDir -B $BuildDir -A x64
    Write-Host "[2/3] 编译核心库、工作站与单元测试..." -ForegroundColor Cyan
    cmake --build $BuildDir --config $BuildType --parallel
    Write-Host "[3/3] 运行 CTest 核心单元测试..." -ForegroundColor Cyan
    ctest --test-dir $BuildDir -C $BuildType --output-on-failure
    $ExePath = Join-Path $BuildDir "$BuildType\tin_earthwork_cass.exe"
}

Write-Host "构建完成: $ExePath" -ForegroundColor Green
if ($RunDemo) {
    $SampleXlsx = Join-Path $RepoRoot "test_data_1000.xlsx"
    $ShotDir = Join-Path $BuildDir "demo_shots"
    & $ExePath --demo $SampleXlsx --capture-dir $ShotDir
    Write-Host "已生成演示截图至: $ShotDir" -ForegroundColor Green
}

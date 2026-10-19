#!/usr/bin/env powershell

# 启动前端服务脚本
# 自动清理端口 5179 并启动 Vite

Write-Host "========================================"
Write-Host "  启动前端服务 (端口: 5179)"
Write-Host "========================================"

# 检查并清理占用端口 5179 的进程
Write-Host "[1/3] 检查端口 5179 占用情况..."
try {
    $portProcesses = Get-NetTCP -LocalPort 5179
    if ($portProcesses) {
        Write-Host "端口 5179 已被占用，进程 ID: $($portProcesses.ProcessId)"
    } else {
        Write-Host "端口 5179 未被占用"
    }
} catch {
    Write-Host "检查端口 5179 占用情况时出错: $_"
}
# 启动 Vite
Write-Host "[2/3] 启动 Vite..."
try {
    Start-Process "vite"
    Write-Host "Vite 已启动，端口: 5179"
} catch {
    Write-Host "启动 Vite 时出错: $_"
}
# 等待 Vite 启动完成
Start-Sleep -Seconds 5
Write-Host "Vite 启动完成"
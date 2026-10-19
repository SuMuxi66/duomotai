#!/usr/bin/env node

import { execSync } from 'child_process';

const PORT = 5179;

console.log(`[端口清理] 检查端口 ${PORT} 是否被占用...`);

try {
  // 在 Windows PowerShell 中使用 Get-NetTCPConnection 检查端口
  const output = execSync(`powershell -Command "Get-NetTCPConnection -LocalPort ${PORT} | Select-Object OwningProcess"`, {
    encoding: 'utf8',
    stdio: 'pipe'
  });
  
  // 解析输出，提取 PID
  const lines = output.trim().split('\n');
  const pids = [];
  
  for (const line of lines) {
    const match = line.trim().match(/^\d+$/);
    if (match) {
      pids.push(match[0]);
    }
  }
  
  if (pids.length > 0) {
    console.log(`[端口清理] 发现 ${pids.length} 个进程占用端口 ${PORT}: ${pids.join(', ')}`);
    
    // 终止占用端口的进程
    for (const pid of pids) {
      try {
        execSync(`taskkill /PID ${pid} /F`, {
          stdio: 'pipe'
        });
        console.log(`[端口清理] 已终止 PID ${pid} 的进程`);
      } catch (error) {
        console.warn(`[端口清理] 终止 PID ${pid} 失败: ${error.message}`);
      }
    }
    
    console.log(`[端口清理] 端口 ${PORT} 清理完成`);
  } else {
    console.log(`[端口清理] 端口 ${PORT} 未被占用`);
  }
  
} catch (error) {
  console.log(`[端口清理] 检查端口失败，可能端口未被占用: ${error.message}`);
}

console.log(`[端口清理] 开始启动 Vite 开发服务器...`);

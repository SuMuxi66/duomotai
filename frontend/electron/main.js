const { app, BrowserWindow, ipcMain, shell, dialog } = require('electron');
// 关闭自动播放限制：桌面应用形态下，警报语音无需用户点击即可播放
// （浏览器网页形态受 Chrome 自动播放策略限制，桌面壳无此限制）
app.commandLine.appendSwitch('autoplay-policy', 'no-user-gesture-required');
const path = require('path');
const { spawn } = require('child_process');
const fs = require('fs');

let mainWindow = null;
let backendProcess = null;

const isDev = !app.isPackaged;

function getAssetPath(...paths) {
  return isDev
    ? path.join(__dirname, '..', ...paths)
    : path.join(process.resourcesPath, ...paths);
}

function readEnvFile(envPath) {
  const result = {};
  if (!fs.existsSync(envPath)) {
    return result;
  }

  const content = fs.readFileSync(envPath, 'utf-8');
  for (const rawLine of content.split(/\r?\n/)) {
    const line = rawLine.trim();
    if (!line || line.startsWith('#')) {
      continue;
    }

    const separatorIndex = line.indexOf('=');
    if (separatorIndex === -1) {
      continue;
    }

    const key = line.slice(0, separatorIndex).trim();
    const value = line.slice(separatorIndex + 1).trim();
    result[key] = value;
  }

  return result;
}

function getConfiguredPort(key, envPaths, fallback) {
  for (const envPath of envPaths) {
    const values = readEnvFile(envPath);
    if (values[key]) {
      const parsed = Number.parseInt(values[key], 10);
      if (!Number.isNaN(parsed)) {
        return parsed;
      }
    }
  }

  if (process.env[key]) {
    const parsed = Number.parseInt(process.env[key], 10);
    if (!Number.isNaN(parsed)) {
      return parsed;
    }
  }

  return fallback;
}

function startBackend() {
  const backendPath = isDev
    ? path.join(__dirname, '..', '..', 'backend')
    : path.join(process.resourcesPath, 'backend');

  const pythonCmd = isDev
    ? path.join(backendPath, '.venv', 'Scripts', 'python.exe')
    : 'python';

  const backendScript = path.join(backendPath, 'app', 'main.py');
  const backendPort = getConfiguredPort('BACKEND_PORT', [
    path.join(backendPath, '.env.local'),
    path.join(backendPath, '.env'),
  ], 8010);

  console.log('[Electron] 启动后端:', pythonCmd, backendScript);

  backendProcess = spawn(pythonCmd, ['-m', 'uvicorn', 'app.main:app', '--host', '0.0.0.0', '--port', String(backendPort)], {
    cwd: backendPath,
    stdio: 'pipe',
    shell: true,
    env: { ...process.env }
  });

  backendProcess.stdout.on('data', (data) => {
    console.log('[Backend]', data.toString().trim());
  });

  backendProcess.stderr.on('data', (data) => {
    console.error('[Backend Error]', data.toString().trim());
  });

  backendProcess.on('close', (code) => {
    console.log('[Electron] 后端进程退出, code:', code);
  });
}

function createWindow() {
  const frontendPath = isDev
    ? path.join(__dirname, '..')
    : path.join(process.resourcesPath, 'frontend');
  const frontendPort = getConfiguredPort('VITE_FRONTEND_PORT', [
    path.join(frontendPath, '.env'),
    path.join(frontendPath, '.env.local'),
  ], 5179);

  mainWindow = new BrowserWindow({
    width: 1200,
    height: 800,
    minWidth: 800,
    minHeight: 600,
    title: '多模态智能烹饪系统',
    webPreferences: {
      preload: path.join(__dirname, 'preload.js'),
      contextIsolation: true,
      nodeIntegration: false,
      webSecurity: true,
    },
    show: false,
  });

  mainWindow.once('ready-to-show', () => {
    mainWindow.show();
    console.log('[Electron] 窗口已显示');
  });

  if (isDev) {
    mainWindow.loadURL(`http://localhost:${frontendPort}`);
    mainWindow.webContents.openDevTools();
  } else {
    mainWindow.loadFile(path.join(__dirname, '..', 'dist', 'index.html'));
  }

  mainWindow.webContents.on('did-fail-load', (event, errorCode, errorDescription) => {
    console.error('[Electron] 页面加载失败:', errorCode, errorDescription);
  });

  mainWindow.on('closed', () => {
    mainWindow = null;
  });
}

app.whenReady().then(() => {
  console.log('[Electron] 应用准备就绪');

  if (!isDev) {
    startBackend();
  }

  createWindow();

  app.on('activate', () => {
    if (BrowserWindow.getAllWindows().length === 0) {
      createWindow();
    }
  });
});

app.on('window-all-closed', () => {
  if (backendProcess) {
    backendProcess.kill();
  }
  if (process.platform !== 'darwin') {
    app.quit();
  }
});

ipcMain.handle('get-app-version', () => {
  return app.getVersion();
});

ipcMain.handle('open-external', async (event, url) => {
  await shell.openExternal(url);
});

ipcMain.handle('show-message-box', async (event, options) => {
  const result = await dialog.showMessageBox(mainWindow, options);
  return result;
});

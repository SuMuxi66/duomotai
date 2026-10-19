function getTimestamp(): string {
  const now = new Date();
  const year = now.getFullYear();
  const month = String(now.getMonth() + 1).padStart(2, '0');
  const day = String(now.getDate()).padStart(2, '0');
  const hours = String(now.getHours()).padStart(2, '0');
  const minutes = String(now.getMinutes()).padStart(2, '0');
  const seconds = String(now.getSeconds()).padStart(2, '0');
  return `${year}-${month}-${day} ${hours}:${minutes}:${seconds}`;
}

export function logToFile(...args: unknown[]): void {
  const message = args.map(arg => {
    if (typeof arg === 'object') {
      try {
        return JSON.stringify(arg);
      } catch {
        return String(arg);
      }
    }
    return String(arg);
  }).join(' ');

  const logEntry = `[${getTimestamp()}] ${message}\n`;
  console.log(`[Frontend Log] ${message}`);

  // 发送到后端保存
  fetch('/api/log', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      log_type: 'frontend',
      message: logEntry
    })
  }).catch(() => {});
}

export function logSection(title: string): void {
  const separator = '='.repeat(60);
  const logEntry = `\n${separator}\n${title}\n${separator}\n`;
  console.log(`[Frontend Log] ${title}`);
  
  fetch('/api/log', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      log_type: 'frontend',
      message: logEntry
    })
  }).catch(() => {});
}

export const frontendLogger = {
  log: logToFile,
  info: logToFile,
  error: (...args: unknown[]) => logToFile('ERROR:', ...args),
  debug: logToFile,
  section: logSection
};

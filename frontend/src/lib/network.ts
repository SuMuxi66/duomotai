/**
 * 网络工具函数
 * 自动获取本机 IP 地址
 */

/**
 * 获取本机 IP 地址
 * 优先返回局域网 IP，如果没有则返回 localhost
 */
export function getLocalIpAddress(): string {
  // 如果在浏览器环境中，尝试从 RTCPeerConnection 获取
  if (typeof window !== 'undefined' && window.RTCPeerConnection) {
    return getIpFromWebRTC();
  }
  
  // 默认返回 localhost
  return 'localhost';
}

/**
 * 使用 WebRTC 获取本机 IP 地址
 */
function getIpFromWebRTC(): string {
  try {
    const pc = new RTCPeerConnection({
      iceServers: []
    });
    
    pc.createDataChannel('');
    
    let ip = 'localhost';
    
    pc.onicecandidate = (e) => {
      if (!e.candidate) return;
      
      const candidate = e.candidate.candidate;
      const ipMatch = candidate.match(/(\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3})/);
      
      if (ipMatch) {
        const foundIp = ipMatch[1];
        // 优先选择局域网 IP (192.168.x.x 或 10.x.x.x 或 172.16-31.x.x)
        if (
          foundIp.startsWith('192.168.') ||
          foundIp.startsWith('10.') ||
          /^172\.(1[6-9]|2[0-9]|3[01])\./.test(foundIp)
        ) {
          ip = foundIp;
        }
      }
    };
    
    pc.createOffer().then(o => pc.setLocalDescription(o));
    
    // 返回当前 IP（可能不是最终确定的）
    return ip;
  } catch (e) {
    console.warn('[Network] 获取 IP 失败:', e);
    return 'localhost';
  }
}

/**
 * 动态构建 API Base URL
 * 
 * 开发环境：返回空字符串，让请求通过 Vite 代理
 * 生产环境：返回完整的后端地址
 */
export function getApiBaseUrl(): string {
  if (typeof window === 'undefined') {
    return '';
  }
  
  // 如果环境变量设置了 API_BASE，使用它
  const envApiBase = (import.meta as any).env?.VITE_API_BASE;
  if (envApiBase) {
    return envApiBase;
  }
  
  // 开发环境：返回空字符串，让 Vite 代理处理请求
  // 这样可以避免浏览器的自签名证书问题
  return '';
}

/**
 * 获取直接的后端地址（用于特殊场景）
 * 注意：使用此地址会绕过 Vite 代理，可能导致证书问题
 */
export function getDirectBackendUrl(): string {
  if (typeof window === 'undefined') {
    return '';
  }
  
  const host = getLocalIpAddress();
  const protocol = window.location.protocol;
  return `${protocol}//${host}:8010`;
}

/**
 * 动态构建 WebSocket URL
 * 
 * 开发环境：使用相对路径 /ws，让 Vite 代理处理
 * 生产环境：使用完整的后端地址
 */
export function getWebSocketUrl(): string {
  if (typeof window === 'undefined') {
    return '';
  }
  
  // 开发环境：使用相对路径，让 Vite 代理处理
  // 这样可以避免浏览器的自签名证书问题
  const wsProtocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
  const host = window.location.host;
  
  return `${wsProtocol}//${host}/ws`;
}

/**
 * 获取所有可能的本地地址
 * 用于 CORS 配置等场景
 */
export function getAllLocalAddresses(): string[] {
  const addresses = [
    'localhost',
    '127.0.0.1',
  ];
  
  // 尝试获取局域网 IP
  const ip = getLocalIpAddress();
  if (ip && ip !== 'localhost') {
    addresses.push(ip);
  }
  
  return addresses;
}

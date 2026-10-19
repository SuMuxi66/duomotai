/**
 * STT 模型服务健康检测工具
 * 检测本地 SenseVoice 模型服务是否正常运行
 */

export interface STTServiceStatus {
  portAvailable: boolean;      // 端口是否可用
  apiHealthy: boolean;         // API 是否正常
  modelLoaded: boolean;        // 模型是否加载
  latency: number;             // 响应延迟 (ms)
  error?: string;              // 错误信息
  timestamp: number;           // 检测时间戳
}

const STT_SERVICE_URL = "http://localhost:8090";
const HEALTH_ENDPOINT = "/health";
const TIMEOUT_MS = 5000;

/**
 * 检测 STT 模型服务状态
 */
export async function checkSTTServiceStatus(): Promise<STTServiceStatus> {
  const startTime = Date.now();
  const result: STTServiceStatus = {
    portAvailable: false,
    apiHealthy: false,
    modelLoaded: false,
    latency: 0,
    timestamp: Date.now(),
  };

  try {
    const controller = new AbortController();
    const timeoutId = setTimeout(() => controller.abort(), TIMEOUT_MS);

    const response = await fetch(`${STT_SERVICE_URL}${HEALTH_ENDPOINT}`, {
      method: "GET",
      signal: controller.signal,
    });

    clearTimeout(timeoutId);
    result.latency = Date.now() - startTime;

    if (response.ok) {
      result.portAvailable = true;
      result.apiHealthy = true;

      const data = await response.json();
      result.modelLoaded = data.model_loaded === true;

      console.log(
        `[STT检测] 服务正常 | 端口:8090 | 模型:${result.modelLoaded ? '已加载' : '未加载'} | 延迟:${result.latency}ms`
      );
    } else {
      result.portAvailable = true;
      result.error = `HTTP ${response.status}`;
      console.warn(`[STT检测] 服务响应异常: ${response.status}`);
    }
  } catch (error: any) {
    result.latency = Date.now() - startTime;

    if (error.name === "AbortError") {
      result.error = "请求超时";
      console.warn("[STT检测] 连接超时，服务可能未启动");
    } else if (error.message?.includes("Failed to fetch") || error.message?.includes("NetworkError")) {
      result.error = "无法连接";
      console.warn("[STT检测] 无法连接到端口8090，服务可能未启动");
    } else {
      result.error = error.message || "未知错误";
      console.warn(`[STT检测] 检测失败: ${result.error}`);
    }
  }

  return result;
}

/**
 * 打印 STT 服务状态摘要
 */
export function printSTTServiceSummary(status: STTServiceStatus): void {
  console.log("=".repeat(50));
  console.log("[STT模型服务检测]");
  console.log("=".repeat(50));
  console.log(`  端口状态: ${status.portAvailable ? '✅ 可用' : '❌ 不可用'}`);
  console.log(`  API状态:  ${status.apiHealthy ? '✅ 正常' : '❌ 异常'}`);
  console.log(`  模型状态: ${status.modelLoaded ? '✅ 已加载' : '❌ 未加载'}`);
  console.log(`  响应延迟: ${status.latency}ms`);
  if (status.error) {
    console.log(`  错误信息: ${status.error}`);
  }
  console.log("=".repeat(50));

  if (!status.portAvailable || !status.apiHealthy) {
    console.log("[提示] 请确保语音模型服务已启动:");
    console.log("       cd model && python run_server.py");
    console.log("       或使用虚拟环境:");
    console.log("       E:\\PythonDev\\venv_speech_model\\Scripts\\python.exe run_server.py");
  }
}

/**
 * 执行完整的 STT 服务自检
 */
export async function performSTTSelfCheck(): Promise<STTServiceStatus> {
  console.log("[STT检测] 开始自检...");
  const status = await checkSTTServiceStatus();
  printSTTServiceSummary(status);
  return status;
}

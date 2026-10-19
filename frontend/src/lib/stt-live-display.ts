/**
 * 仅用于显示的浏览器实时语音转写（不参与消息发送）
 *
 * 后端 STT 是「整段录音结束后一次性识别」（前端本地录音、段尾整段上传），
 * 说话期间后端没有音频、无法返回中间文本。此模块在录音段期间并行运行一个
 * 浏览器 Web Speech 识别会话，仅把实时文本回调给输入框做显示；
 * 真正发送的消息内容仍以后端 stt_final 为准。
 *
 * 注意：Chrome 同页只允许一个 SpeechRecognition 实例活跃。本模块仅在后端
 * STT 模式（provider 非 browser）下由 wakeword-stt 控制器按录音段启停，
 * 与 browser STT 路径互斥，不会争抢实例。
 */

export interface SttLiveDisplayHandle {
  stop: () => void;
}

export function startSttLiveDisplay(
  onText: (text: string) => void,
  lang = "zh-CN",
): SttLiveDisplayHandle | null {
  const SR = (window as any).SpeechRecognition || (window as any).webkitSpeechRecognition;
  if (!SR) return null; // 浏览器不支持：无实时显示，回退原行为

  let stopped = false;
  let rec: any = null;
  let restartTimer: number | null = null;
  let finalText = "";

  const spawn = () => {
    if (stopped) return;
    try {
      rec = new SR();
      rec.lang = lang;
      rec.continuous = true;
      rec.interimResults = true;
      rec.onresult = (event: any) => {
        let interim = "";
        for (let i = event.resultIndex; i < event.results.length; i++) {
          const r = event.results[i];
          if (r.isFinal) finalText += r[0].transcript;
          else interim += r[0].transcript;
        }
        const combined = `${finalText}${interim}`.trim();
        if (combined) onText(combined);
      };
      rec.onerror = (event: any) => {
        // 无麦克风权限/服务不可用：放弃重启，静默退化为原行为（无实时显示）
        if (event?.error === "not-allowed" || event?.error === "service-not-allowed") {
          stopped = true;
        }
      };
      rec.onend = () => {
        // Chrome 会在静音后自动结束会话，段内自动重启保持连续显示
        if (!stopped) {
          restartTimer = window.setTimeout(spawn, 200);
        }
      };
      rec.start();
    } catch {
      // start() 对已活跃实例会抛异常：忽略，等待 onend 重启
    }
  };
  spawn();

  return {
    stop: () => {
      if (stopped) return;
      stopped = true;
      if (restartTimer !== null) {
        clearTimeout(restartTimer);
        restartTimer = null;
      }
      try { rec?.stop(); } catch { /* 已停止 */ }
      rec = null;
    },
  };
}

// @ts-nocheck
/**
 * ★ 演示版 —— video.ts（小任务1：前端接入 + 压缩）
 *
 * 仅保留"接入摄像头 + 截帧压缩"两个核心函数。
 * 分辨率测量、帧率节流、日志等周边逻辑已移除。
 * 完整实现见 frontend-2/src/lib/video.ts
 */
// =========================================================
// ★ 演示要点（小任务1：前端接入 + 压缩）
//   ① 播放后端 MJPEG 流 → ② 等比缩放 → ③ 截帧 → ④ JPEG 压缩输出
// =========================================================

// ★演示①: 接入 —— 播放后端 MJPEG 流，一行完成
export async function startCamera(videoEl: HTMLVideoElement) {
  // USB 摄像头由后端常驻捕获（/usb-camera/stream MJPEG 流）
  videoEl.srcObject = null;
  videoEl.removeAttribute('src');
  videoEl.src = '/usb-camera/stream';
  try {
    await videoEl.play();
  } catch (e) {
    console.warn('[startCamera] 后端摄像头流播放失败（摄像头可能未连接）:', e);
  }
  return null;
}

function toBase64(blob: Blob): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => {
      const result = reader.result as string;
      resolve(result.split(',')[1]); // 去掉 data:image/jpeg;base64, 前缀
    };
    reader.onerror = reject;
    reader.readAsDataURL(blob);
  });
}

// 截帧压缩：供 VLM 快速识别
export async function captureJpegBase64(
  videoEl: HTMLVideoElement,
  maxWidth: number,
  quality: number
): Promise<{ b64: string; width: number; height: number }> {
  let vw = videoEl.videoWidth;
  let vh = videoEl.videoHeight;
  if (!vw || !vh) {
    vw = 640; // MJPEG 流尺寸未就绪时的默认值
    vh = 480;
  }

  // ★演示②: 等比缩放（限制传输尺寸，上限 1920）
  const scale = Math.min(1, maxWidth / vw);
  const w = Math.round(vw * scale);
  const h = Math.round(vh * scale);

  const canvas = document.createElement("canvas");
  canvas.width = w;
  canvas.height = h;
  const ctx = canvas.getContext("2d")!;

  // ★演示③: 截帧（把视频帧画到 canvas 上）
  ctx.drawImage(videoEl, 0, 0, w, h);

  // ★演示④: JPEG 压缩输出 base64（quality 越高质量越好、体积越大）
  const b64 = await new Promise<string>((resolve, reject) => {
    canvas.toBlob(async (blob) => {
      if (!blob) {
        reject(new Error('canvas toBlob failed'));
        return;
      }
      resolve(await toBase64(blob));
    }, 'image/jpeg', quality);
  });

  return { b64, width: w, height: h };
}
export async function startCamera(videoEl: HTMLVideoElement, _videoDeviceId?: string) {
  // USB 摄像头由后端常驻捕获（/usb-camera/stream MJPEG 流），
  // 任意多个前端共享同一路画面，避免各网页 getUserMedia 抢占摄像头。
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

export function stopCamera(videoEl: HTMLVideoElement | null) {
  if (!videoEl) return;
  try {
    videoEl.pause();
    if (videoEl.srcObject) {
      (videoEl.srcObject as MediaStream).getTracks().forEach((t) => t.stop());
      videoEl.srcObject = null;
    }
    videoEl.removeAttribute('src');
    videoEl.load();
  } catch {
    // 忽略清除错误
  }
}

let _reusableCanvas: HTMLCanvasElement | null = null;
let _reusableCanvasCtx: CanvasRenderingContext2D | null = null;

function toBase64(blob: Blob): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => {
      const result = reader.result as string;
      const base64 = result.split(',')[1];
      resolve(base64);
    };
    reader.onerror = reject;
    reader.readAsDataURL(blob);
  });
}

export async function captureJpegBase64(
  videoEl: HTMLVideoElement,
  maxWidth: number,
  quality: number
): Promise<{ b64: string; width: number; height: number }> {
  // 等待视频元数据和首帧就绪，避免刚启动摄像头时拿到空帧。
  if (!videoEl.videoWidth || !videoEl.videoHeight) {
    await new Promise<void>((resolve) => {
      let resolved = false;
      const done = () => {
        if (resolved) return;
        resolved = true;
        videoEl.removeEventListener("loadedmetadata", done);
        videoEl.removeEventListener("loadeddata", done);
        resolve();
      };
      videoEl.addEventListener("loadedmetadata", done, { once: true });
      videoEl.addEventListener("loadeddata", done, { once: true });
      setTimeout(done, 800);
    });
  }

  let vw = videoEl.videoWidth;
  let vh = videoEl.videoHeight;
  
  console.log(`[captureJpegBase64] 原始视频尺寸: ${vw}x${vh}`);
  
  // 对于MJPEG流，视频尺寸可能不会立即设置，使用默认尺寸
  if (!vw || !vh) {
    console.log(`[captureJpegBase64] 视频尺寸未设置，使用默认值 640x480`);
    vw = 640;
    vh = 480;
  }

  const scale = Math.min(1, maxWidth / vw);
  const w = Math.round(vw * scale);
  const h = Math.round(vh * scale);

  if (!_reusableCanvas) {
    _reusableCanvas = document.createElement("canvas");
    _reusableCanvasCtx = _reusableCanvas.getContext("2d");
  }
  const canvas = _reusableCanvas;
  const ctx = _reusableCanvasCtx!;
  canvas.width = w;
  canvas.height = h;

  // 绘制视频帧
  ctx.drawImage(videoEl, 0, 0, w, h);

  const b64 = await new Promise<string>((resolve, reject) => {
    canvas.toBlob(async (blob) => {
      if (!blob) { reject(new Error('canvas toBlob failed')); return; }
      try {
        const result = await toBase64(blob);
        resolve(result);
      } catch (e) {
        reject(e);
      }
    }, 'image/jpeg', quality);
  });
  return { b64, width: w, height: h };
}

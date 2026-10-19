/**
 * 视频录制控制器
 * 支持从 MediaStream 或 Canvas 捕获视频流并录制
 */

export interface RecordingController {
  start: (stream: MediaStream) => void;
  stop: () => Promise<Blob | null>;
  isRecording: boolean;
}

/**
 * 创建录制控制器实例
 * @param options 录制选项
 * @returns RecordingController 实例
 */
export function createRecordingController(options?: {
  mimeType?: string;
  videoBitsPerSecond?: number;
}): RecordingController {
  let mediaRecorder: MediaRecorder | null = null;
  let chunks: Blob[] = [];
  let _isRecording = false;

  const mimeType = options?.mimeType || getSupportedMimeType();
  const videoBitsPerSecond = options?.videoBitsPerSecond || 2500000; // 2.5 Mbps

  /**
   * 获取浏览器支持的 MIME 类型
   */
  function getSupportedMimeType(): string {
    const types = [
      'video/webm;codecs=vp9',
      'video/webm;codecs=vp8',
      'video/webm',
      'video/mp4'
    ];
    
    for (const type of types) {
      if (MediaRecorder.isTypeSupported(type)) {
        return type;
      }
    }
    
    return 'video/webm'; // 默认回退
  }

  /**
   * 开始录制
   * @param stream 要录制的媒体流
   */
  function start(stream: MediaStream): void {
    if (_isRecording) {
      console.warn('[RecordingController] 录制已在进行中');
      return;
    }

    try {
      chunks = [];
      mediaRecorder = new MediaRecorder(stream, {
        mimeType,
        videoBitsPerSecond
      });

      mediaRecorder.ondataavailable = (event) => {
        if (event.data.size > 0) {
          chunks.push(event.data);
        }
      };

      mediaRecorder.onerror = (event) => {
        console.error('[RecordingController] 录制错误:', event);
        _isRecording = false;
      };

      mediaRecorder.start(100); // 每100ms收集一次数据
      _isRecording = true;
      console.log('[RecordingController] 开始录制, MIME类型:', mimeType);
    } catch (error) {
      console.error('[RecordingController] 创建录制器失败:', error);
      throw error;
    }
  }

  /**
   * 停止录制并返回视频 Blob
   * @returns Promise 录制的视频 Blob，如果录制未开始则返回 null
   */
  function stop(): Promise<Blob | null> {
    return new Promise((resolve) => {
      if (!_isRecording || !mediaRecorder) {
        console.warn('[RecordingController] 没有正在进行的录制');
        resolve(null);
        return;
      }

      mediaRecorder.onstop = () => {
        const blob = new Blob(chunks, { type: mimeType });
        chunks = [];
        _isRecording = false;
        mediaRecorder = null;
        console.log('[RecordingController] 录制完成, 大小:', (blob.size / 1024 / 1024).toFixed(2), 'MB');
        resolve(blob);
      };

      mediaRecorder.stop();
    });
  }

  return {
    start,
    stop,
    get isRecording() {
      return _isRecording;
    }
  };
}

/**
 * 从 Canvas 创建媒体流
 * @param canvas Canvas 元素
 * @param fps 帧率
 * @returns MediaStream 对象
 */
export function createStreamFromCanvas(canvas: HTMLCanvasElement, fps: number = 30): MediaStream {
  return canvas.captureStream(fps);
}

/**
 * 下载视频文件
 * @param blob 视频 Blob
 * @param filename 文件名（不含扩展名）
 */
export function downloadVideo(blob: Blob, filename: string = 'recording'): void {
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  
  // 根据 MIME 类型确定扩展名
  const extension = blob.type.includes('mp4') ? 'mp4' : 'webm';
  a.download = `${filename}_${Date.now()}.${extension}`;
  
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);
  
  // 延迟释放 URL
  setTimeout(() => URL.revokeObjectURL(url), 100);
  
  console.log('[RecordingController] 视频已下载:', a.download);
}

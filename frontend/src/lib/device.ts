/**
 * 设备检测模块
 * 用于检测用户的摄像头和麦克风设备
 */

/**
 * 设备信息接口
 */
export interface DeviceInfo {
  hasWebcam: boolean;
  hasMicrophone: boolean;
  hasAudioOutput: boolean;
  videoDevices: MediaDeviceInfo[];
  audioDevices: MediaDeviceInfo[];
  audioOutputDevices: MediaDeviceInfo[];
}

/**
 * 检测用户的摄像头和麦克风设备
 * @returns Promise<DeviceInfo> 设备信息对象
 */
export async function detectDevices(): Promise<DeviceInfo> {
  const deviceInfo: DeviceInfo = {
    hasWebcam: false,
    hasMicrophone: false,
    hasAudioOutput: false,
    videoDevices: [],
    audioDevices: [],
    audioOutputDevices: [],
  };

  try {
    // 检查浏览器是否支持 mediaDevices API
    if (!navigator.mediaDevices || !navigator.mediaDevices.enumerateDevices) {
      console.warn('[设备检测] 浏览器不支持 mediaDevices API');
      return deviceInfo;
    }

    // 枚举所有设备
    const devices = await navigator.mediaDevices.enumerateDevices();

    // 分类设备
    devices.forEach((device) => {
      if (device.kind === 'videoinput') {
        deviceInfo.videoDevices.push(device);
        deviceInfo.hasWebcam = true;
      } else if (device.kind === 'audioinput') {
        deviceInfo.audioDevices.push(device);
        deviceInfo.hasMicrophone = true;
      } else if (device.kind === 'audiooutput') {
        deviceInfo.audioOutputDevices.push(device);
        deviceInfo.hasAudioOutput = true;
      }
    });

    console.log('[设备检测] 检测结果:', {
      摄像头: deviceInfo.hasWebcam ? `${deviceInfo.videoDevices.length}个` : '未检测到',
      麦克风: deviceInfo.hasMicrophone ? `${deviceInfo.audioDevices.length}个` : '未检测到',
      扬声器: deviceInfo.hasAudioOutput ? `${deviceInfo.audioOutputDevices.length}个` : '未检测到',
    });

    return deviceInfo;
  } catch (error) {
    console.error('[设备检测] 检测失败:', error);
    return deviceInfo;
  }
}

/**
 * 请求设备权限并重新检测
 * @returns Promise<DeviceInfo> 设备信息对象
 */
export async function requestDevicePermissions(): Promise<DeviceInfo> {
  try {
    // 请求摄像头和麦克风权限
    const stream = await navigator.mediaDevices.getUserMedia({
      video: true,
      audio: true,
    });

    // 立即停止流，我们只需要权限
    stream.getTracks().forEach((track) => track.stop());

    // 重新检测设备
    return await detectDevices();
  } catch (error) {
    console.error('[设备检测] 请求权限失败:', error);
    // 即使权限被拒绝，也返回检测结果
    return await detectDevices();
  }
}

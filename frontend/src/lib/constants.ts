import { systemDefaults } from "./config";
export { CONFIG_VERSION, STORAGE_KEYS } from "./storage";

/**
 * API Base URL
 * 
 * 开发环境：返回空字符串，让请求使用相对路径，通过 Vite 代理转发
 * 生产环境：使用配置中的 API_BASE
 * 
 * 这样可以避免浏览器的自签名证书问题，因为所有请求都通过 Vite 代理
 */
export const API_BASE = "";
export const SESSION_ID = "default";

export const WAKE_WORDS = ["小云，小云"];

export const DEFAULTS = systemDefaults;

/** 眼镜摄像头特殊标识符：在视频设备列表中使用该值表示选择眼镜摄像头 */
export const GLASSES_CAMERA_ID = "__GLASSES_CAMERA__";

/** 眼镜麦克风特殊标识符：在音频设备列表中使用该值表示选择眼镜麦克风 */
export const GLASSES_AUDIO_ID = "__GLASSES_AUDIO__";

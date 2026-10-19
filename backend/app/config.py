import os
from pathlib import Path
from typing import Any, Optional

from dotenv import load_dotenv
from pydantic import BaseModel, Field

# 纯标准库模块，安全导入（用于 HAZARD_RULES 默认值）
from .hazard_rules import HAZARD_RULES as _DEFAULT_HAZARD_RULES


BACKEND_DIR = Path(__file__).parent.parent
ENV_LOCAL = BACKEND_DIR / ".env.local"
ENV_FILE = BACKEND_DIR / ".env"

if ENV_LOCAL.exists():
    load_dotenv(ENV_LOCAL)
    print(f"[Config] 已加载环境变量: {ENV_LOCAL}")
elif ENV_FILE.exists():
    load_dotenv(ENV_FILE)
    print(f"[Config] 已加载环境变量: {ENV_FILE}")
else:
    print("[Config] 未找到 .env.local 或 .env 文件，使用默认配置")


def _get_int_env(name: str, default: int) -> int:
    raw_value = os.getenv(name, str(default))
    try:
        return int(raw_value)
    except ValueError:
        print(f"[Config] {name} 配置非法，使用默认值 {default}: {raw_value}")
        return default


class Settings(BaseModel):
    backend_port: int = _get_int_env("BACKEND_PORT", 8010)

    vision_enabled: bool = os.getenv("VISION_ENABLED", "true").lower() == "true"
    vision_fps: float = float(os.getenv("VISION_FPS", "0.2"))
    vision_max_observations: int = 12

    # 摄像头数据源模式（前端设置面板"摄像头"页可切换，经 /env/update 持久化到 .env.local）：
    # "network" = 使用网络摄像头（香橙派推流，断流自动回退本机）；"local" = 使用本机 USB 摄像头（忽略远程帧）
    camera_source_mode: str = os.getenv("CAMERA_SOURCE_MODE", "network")

    # 画面旋转角度（0/90/180/270，顺时针）：摄像头物理倒装时在后端源头把画面转正，
    # MJPEG 流 / 拍照 / 前端抓帧→VLM 全链路跟随。前端"旋转画面"按钮经 /env/update 持久化，
    # 每帧读取即改即生效（应用见 camera_rotate.py）
    camera_rotation: int = _get_int_env("CAMERA_ROTATION", 180)

    # ===== 感知安全增强：多帧时序聚合 / 危险规则 =====
    # 滑动窗口帧数（>=1），越大越稳但响应越慢
    temporal_window: int = _get_int_env("TEMPORAL_WINDOW", 5)
    # 触发阈值帧数（>=1，<=temporal_window），窗口内命中 >= 阈值才告警
    temporal_threshold: int = _get_int_env("TEMPORAL_THRESHOLD", 3)
    # 危险类型 -> {level, cooldown_sec, action} 规则；默认取 hazard_rules.HAZARD_RULES
    hazard_rules: dict = Field(default_factory=lambda: dict(_DEFAULT_HAZARD_RULES))
    # 是否启用设备控制闭环（缺真实设备时自动降级为 simulated）
    safety_device_control_enabled: bool = os.getenv("SAFETY_DEVICE_CONTROL_ENABLED", "true").lower() == "true"

    # 默认走魔搭社区（ModelScope）；DashScope 仅作为显式选择的备选，不做默认
    # 文字与视觉统一走 VLM 端点，LLM provider 已移除
    vlm_provider: str = os.getenv("VLM_PROVIDER", "modelscope")
    llm_temperature: float = float(os.getenv("LLM_TEMPERATURE", "0.7"))
    llm_max_tokens: int = int(os.getenv("LLM_MAX_TOKENS", "2048"))
    llm_top_p: float = float(os.getenv("LLM_TOP_P", "0.95"))
    llm_sample_count: int = int(os.getenv("LLM_SAMPLE_COUNT", "1"))
    llm_frequency_penalty: float = float(os.getenv("LLM_FREQUENCY_PENALTY", "0"))
    llm_response_format: str = os.getenv("LLM_RESPONSE_FORMAT", "md")
    # 🔒 已废弃：此 env 开关当前无任何消费方（2026-09-15 核实，全后端搜 settings.
    # llm_enable_thinking 为 0 处）。思考模式事实上由会话状态决定
    # （session.SessionState.llm_enable_thinking），现已全局写死关闭于
    # providers/unified.THINKING_HARD_DISABLED。保留字段仅为兼容旧 env，改它无效。
    llm_enable_thinking: bool = os.getenv("LLM_ENABLE_THINKING", "false").lower() == "true"

    # 做饭模式 AI 意图分类兜底：关键词分支全部未命中、即将交给 LLM 问答前，
    # 先用一次轻量 LLM 调用分类用户意图（确认步骤/跳过/退出等），命中才接管执行。
    # 出问题（超时/误判/格式错误）自动回退原问答行为，可用此开关一键关闭。
    ai_intent_fallback_enabled: bool = os.getenv("AI_INTENT_FALLBACK_ENABLED", "true").lower() == "true"

    # 温度假数据源（演示用）：开启后 /temperature、油温监测、温度语音快答全部改读
    # 模拟值——围绕基准环境温度平滑随机游走（每秒更新一次，与前端轮询周期一致），
    # 不再读串口 / 温重板卡 Tmx / 香橙派远程红外。默认关闭，走真实传感器链路。
    temperature_mock_enabled: bool = os.getenv("TEMPERATURE_MOCK_ENABLED", "false").lower() == "true"
    temperature_mock_base: float = float(os.getenv("TEMPERATURE_MOCK_BASE", "26.0"))
    temperature_mock_amplitude: float = float(os.getenv("TEMPERATURE_MOCK_AMPLITUDE", "1.2"))

    # 秤零点自动跟踪（自动校准）：空秤读数小而稳持续一段时间后，把软件去皮基准
    # Trim 到当前均值，零点漂移自动归零，省去手动 /weight/tare。多重守卫保证不吞
    # 真实负载：≥2g 读数、放置瞬态、去皮采样中均不触发。详见 app/weight.py。
    weight_auto_zero_enabled: bool = os.getenv("WEIGHT_AUTO_ZERO_ENABLED", "true").lower() == "true"

    # 主对话大模型只读工具（function calling）：温度/重量/风扇状态/菜谱查询，
    # 让模型拿实测数据回答而不是编造。写操作（下料/开关风扇）不在工具表，
    # 仍由确定性拦截链路执行。详见 app/llm_tools.py。
    llm_tools_enabled: bool = os.getenv("LLM_TOOLS_ENABLED", "true").lower() == "true"

    # 油温问句确定性快答（oil_watch.py 写死模板：意图关键词表 + 固定文案）。
    # 2026-09-12 用户拍板默认关闭：油温/下锅时机类问句改走 LLM function calling
    # （模型语义判断 → 调 get_temperature 工具取实测 → 结合菜品油温知识回答），
    # 约束写在 prompts/system.md 油温小节。演示环境对工具链路不满意时可置 true 切回旧快答。
    oil_temp_fast_answer_enabled: bool = os.getenv("OIL_TEMP_FAST_ANSWER_ENABLED", "false").lower() == "true"

    modelscope_api_key: Optional[str] = os.getenv("MODELSCOPE_API_KEY")
    modelscope_vlm_model: str = os.getenv("MODELSCOPE_VLM_MODEL", "Qwen/Qwen3.8-27B")

    dashscope_api_key: Optional[str] = os.getenv("DASHSCOPE_API_KEY")
    dashscope_vlm_model: str = os.getenv("DASHSCOPE_VLM_MODEL", "qwen3.8-27b")
    dashscope_enable_thinking: bool = os.getenv("DASHSCOPE_ENABLE_THINKING", "true").lower() == "true"

    dashscope_tts_api_key: Optional[str] = os.getenv("DASHSCOPE_API_KEY")  # 使用同一个API Key
    # 2026-10-14: 原 qwen3-tts-instruct-flash-realtime 已被 DashScope 下线（WS 返回
    # "Model not exist"），备线整条哑掉；实测 qwen3-tts-flash-realtime 正常（首块 ~0.6s）。
    dashscope_tts_model: str = os.getenv("DASHSCOPE_TTS_MODEL", "qwen3-tts-flash-realtime")
    dashscope_tts_voice: str = os.getenv("DASHSCOPE_TTS_VOICE", "female-qn-tingting")

    xiaomi_tts_api_key: Optional[str] = os.getenv("XIAOMI_TTS_API_KEY")
    xiaomi_tts_model: str = os.getenv("XIAOMI_TTS_MODEL", "mimo-v2.5-tts")
    xiaomi_tts_voice: str = os.getenv("XIAOMI_TTS_VOICE", "mimo_default")

    stt_provider: str = os.getenv("STT_PROVIDER", "browser")
    stt_model: str = "Web Speech API"

    local_sensevoice_url: str = os.getenv("LOCAL_SENSEVOICE_URL", "http://localhost:8090")

    xinference_api_url: str = os.getenv("XINFERENCE_API_URL", "http://localhost:9999")
    xinference_stt_model: str = os.getenv("XINFERENCE_STT_MODEL", "SenseVoiceSmall")

    tts_provider: str = os.getenv("TTS_PROVIDER", "xiaomi")
    # 双线 TTS 故障切换（2026-09-16 用户要求）：主 provider 首块超时/失败 → 自动切备用。
    # TTS_FAILOVER_PROVIDER 设空串即关闭双线（退回单线路）。
    # 超时取 5s：2026-10-14 实测小米首块已变慢到 3.4~3.8s（09-16 时 ~1.9s），
    # 2.5s 会把正常的小米误杀切备线（用户报"小米 TTS 报错了？无法使用"即此因）；
    # 5s 覆盖当前延迟留余量，小米真挂时 5s 让位 + 备线首块 ~0.6s ≈ 5.6s 出声。
    tts_failover_provider: str = os.getenv("TTS_FAILOVER_PROVIDER", "dashscope")
    tts_failover_timeout: float = float(os.getenv("TTS_FAILOVER_TIMEOUT", "5.0") or 5.0)
    tts_model: str = "SpeechSynthesis API"

    # ===== 上下文向量记忆（超窗历史语义召回，方案1） =====
    # 开关：关闭后 build_history_async 直接回落启发式压缩（原行为）
    context_vector_enabled: bool = os.getenv("CONTEXT_VECTOR_ENABLED", "true").lower() == "true"
    # embedding 供应商固定 SiliconFlow（复用 SILICONFLOW_API_KEY），模型/地址可覆盖
    embedding_model: str = os.getenv("EMBEDDING_MODEL", "BAAI/bge-m3")
    embedding_base_url: str = os.getenv("EMBEDDING_BASE_URL", "https://api.siliconflow.cn/v1/embeddings")

    local_cosyvoice_url: str = os.getenv("LOCAL_COSYVOICE_URL", "http://localhost:5004")
    local_cosyvoice_model: str = os.getenv("LOCAL_COSYVOICE_MODEL", "CosyVoice-300M-SFT")
    local_cosyvoice_voice: str = os.getenv("LOCAL_COSYVOICE_VOICE", "中文女")

    siliconflow_api_key: Optional[str] = os.getenv("SILICONFLOW_API_KEY")
    siliconflow_stt_model: str = os.getenv("SILICONFLOW_STT_MODEL", "FunAudioLLM/SenseVoiceSmall")
    siliconflow_tts_model: str = os.getenv("SILICONFLOW_TTS_MODEL", "fnlp/MOSS-TTSD-v0.5")
    siliconflow_tts_voice: str = os.getenv("SILICONFLOW_TTS_VOICE", "fnlp/MOSS-TTSD-v0.5:claire")
    siliconflow_tts_api_key: Optional[str] = os.getenv("SILICONFLOW_API_KEY")

    # Qwen3 ASR Realtime (使用 DashScope API Key)
    qwen3_asr_model: str = os.getenv("QWEN3_ASR_MODEL", "qwen3-asr-flash-realtime-2026-02-10")
    # Fun-ASR Realtime（北京地域，DashScope Recognition 通道；与 qwen3-asr-flash-realtime 不同协议）
    fun_asr_model: str = os.getenv("FUN_ASR_MODEL", "fun-asr-realtime-2026-02-28")

    # 其他 VLM 提供商 API Key（文字与视觉统一走 VLM 端点）
    deepseek_api_key: Optional[str] = os.getenv("DEEPSEEK_API_KEY")
    deepseek_vlm_model: str = os.getenv("DEEPSEEK_VLM_MODEL", "deepseek-v4-flash")
    sensenova_api_key: Optional[str] = os.getenv("SENSENOVA_API_KEY")
    sensenova_vlm_model: str = os.getenv("SENSENOVA_VLM_MODEL", "sensenova-6.8-flash-lite")


    zhipu_api_key: Optional[str] = os.getenv("ZHIPU_API_KEY")
    zhipu_vlm_model: str = os.getenv("ZHIPU_VLM_MODEL", "glm-4.6v-flash")

    stt_enabled: bool = False
    tts_enabled: bool = os.getenv("TTS_ENABLED", "true").lower() == "true"
    tts_mode: str = "browser"

    # ===== 下料机执行器配置（新版独立串口电磁阀控制器） =====
    # 电磁阀串口设备名（空 = 自动探测，指定则直连该口不搜索）
    valve_port: str = os.getenv("VALVE_PORT", "")
    # 电磁阀串口波特率（固定 9600 8N1）
    valve_baudrate: int = _get_int_env("VALVE_BAUDRATE", 9600)
    # 下料执行器通道：valve = 新版电磁阀（默认），legacy_relay = 旧板卡继电器（回退）
    dispense_executor: str = os.getenv("DISPENSE_EXECUTOR", "valve")

    # 自定义 provider 配置（JSON 格式），例如：
    # {"custom:my-openai": {"api_format": "openai", "base_url": "...", "api_key": "...", "model": "..."}}
    custom_providers: dict = Field(default_factory=dict)

    def model_post_init(self, __context) -> None:
        raw = os.getenv("CUSTOM_PROVIDERS", "")
        if raw:
            try:
                import json
                parsed = json.loads(raw)
                self.custom_providers = self._validate_custom_providers(parsed)
            except Exception as e:
                print(f"[Config] CUSTOM_PROVIDERS 解析失败: {e}")
                self.custom_providers = {}

    @staticmethod
    def _validate_custom_providers(value: Any) -> dict:
        """校验并规范化自定义 provider 配置。

        - 仅保留以 'custom:' 开头的键。
        - api_format 只能是 openai 或 anthropic，默认 openai。
        - base_url 去除末尾斜杠。
        """
        if not isinstance(value, dict):
            return {}
        result: dict = {}
        for key, cfg in value.items():
            if not isinstance(key, str) or not key.startswith("custom:"):
                print(f"[Config] 跳过非法 custom provider key: {key}")
                continue
            if not isinstance(cfg, dict):
                continue
            base_url = str(cfg.get("base_url", "")).rstrip("/")
            api_format = str(cfg.get("api_format", "openai")).lower()
            if api_format not in {"openai", "anthropic"}:
                api_format = "openai"
            result[key] = {
                "api_format": api_format,
                "base_url": base_url,
                "api_key": str(cfg.get("api_key", "")),
                "model": str(cfg.get("model", "")),
            }
        return result


# 前端可把 LLM/VLM 选择同步为 .env.local 默认配置；provider 名 -> 环境变量前缀映射。
# 由 provider 注册表（providers/registry.py）派生——单一数据源，注册表有 env_prefix 的才参与持久化。
from .providers.registry import PROVIDER_REGISTRY as _PROVIDER_REGISTRY

PROVIDER_ENV_PREFIX = {
    spec.name: spec.env_prefix
    for spec in _PROVIDER_REGISTRY.values()
    if spec.env_prefix
}


def persist_env_value(key: str, value: str) -> bool:
    """将 key=value 更新到 .env.local（不存在则追加）；值未变化则跳过写盘。

    返回是否实际写入文件。value 含换行视为非法，返回 False。
    """
    value = (value or "").strip()
    if not value or "\n" in value or "\r" in value:
        return False
    if ENV_LOCAL.exists():
        env_path = ENV_LOCAL
    elif ENV_FILE.exists():
        env_path = ENV_FILE
    else:
        # 两个 env 文件都不存在：新建 .env.local，否则首次持久化会静默失败
        env_path = ENV_LOCAL
        try:
            env_path.touch()
            print(f"[Config] 已创建环境变量文件: {env_path}")
        except OSError:
            return False
    try:
        with open(env_path, "r", encoding="utf-8") as f:
            lines = f.readlines()

        found = False
        for idx, line in enumerate(lines):
            stripped = line.strip()
            if stripped and not stripped.startswith("#"):
                key_part, _, val_part = stripped.partition("=")
                if key_part.strip() == key:
                    found = True
                    if val_part.strip() == value:
                        return False  # 值相同，无需写盘
                    lines[idx] = f"{key}={value}\n"
                    break
        if not found:
            # 追加前确保末行有换行符：文件末行缺 \n 时曾把新键拼进上一行
            # （2026-09-07 事故：VALVE_PORT=COM13 拼上 CAMERA_SOURCE_MODE=local 变成
            #   COM13CAMERA_SOURCE_MODE=local，导致电磁阀串口打不开、下料全挂）
            if lines and not lines[-1].endswith("\n"):
                lines[-1] += "\n"
            lines.append(f"{key}={value}\n")

        with open(env_path, "w", encoding="utf-8") as f:
            f.writelines(lines)
        return True
    except OSError:
        return False


settings = Settings()

def reload_settings():
    """重新加载环境变量并更新 settings 实例。

    Settings 类体里的 os.getenv 默认值只在模块导入时求值一次，
    重新实例化并不会重读环境变量。因此先从 os.environ 显式重建
    全部字段（字段名转大写即环境变量名），再更新全局 settings。
    """
    global settings
    if ENV_LOCAL.exists():
        load_dotenv(ENV_LOCAL, override=True)
        print(f"[Config] 已重新加载环境变量: {ENV_LOCAL}")
    elif ENV_FILE.exists():
        load_dotenv(ENV_FILE, override=True)
        print(f"[Config] 已重新加载环境变量: {ENV_FILE}")

    # custom_providers / hazard_rules 的值需要特殊解析或在 model_post_init 处理，
    # 不能从环境变量字符串直接转换（custom_providers 由 post_init 自行读取）。
    non_direct_fields = {"custom_providers", "hazard_rules"}
    data: dict[str, str] = {}
    for name in Settings.model_fields:
        if name in non_direct_fields:
            continue
        env_name = name.upper()
        raw = os.getenv(env_name)
        if raw is not None:
            data[name] = raw

    try:
        new_settings = Settings.model_validate(data)
    except Exception:
        # 环境变量值异常时保底：保留旧实例，避免配置路径抛错影响 WS 流程
        print("[Config] 环境变量校验失败，保留当前配置")
        return settings
    for key, value in new_settings.model_dump().items():
        setattr(settings, key, value)

    return settings

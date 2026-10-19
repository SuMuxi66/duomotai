"""Provider 注册表：每家供应商一条数据记录（base_url / 密钥键名 / quirks）。

设计目标（对比业界统一层的共性做法）：
- 该用数据表达的不写成代码——新增 OpenAI 兼容供应商 = 在 PROVIDER_REGISTRY
  加一条 ProviderSpec + .env.example 补占位行，不再改 runtime/main/config/前端分支。
- 同一张注册表派生各处需要的映射：PRESET_MODELS、DEFAULT_PROVIDER_BASE_URL、
  ALLOWED_MODEL_HOSTS、PROVIDER_ENV_PREFIX、GET /config api_keys、/providers/catalog。
- 本模块保持纯数据（不 import config/settings），避免与配置层成环。

各家 quirks 说明（合并自 5 个历史 provider 文件的实测差异）：
- ModelScope: 顶层 enable_thinking，未指定时恒带 False（关思考加速首 token）。
- DashScope: 顶层 enable_thinking，未指定时按模型名动态（qwen3.6* 开思考）；
  content/reasoning 可能是 list/dict 需归一化；图片压缩上限 800 宽；
  模型名可能带 ":free" 后缀需剥离；流式空内容时需非流式兜底。
- DeepSeek V4: 思考开关是 thinking.type 结构（官方格式，服务端默认 enabled）；
  文本/视觉路径均携带，开关完全生效。
- 智谱 Zhipu: 思考开关是 thinking.type 结构（区别于百炼的顶层字段）；
  文本路径仅在显式传值时携带。
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class ThinkingSpec:
    """思考模式参数差异的表达。

    - param/style: "top_level"=顶层布尔字段（enable_thinking）；"type_object"=thinking.type
      结构（{"type":"enabled"/"disabled"}，智谱与 DeepSeek V4 同款）。
    - text_default: 文本路径未显式传值时的默认。None=不携带该字段（DeepSeek/Zhipu 现状）；
      False=携带 False（ModelScope 现状）。
    - model_default_prefix: 非空时按模型名前缀动态决定默认值（DashScope qwen3.6*）。
    - apply_to: "all" 或 "vision"（DeepSeek 仅视觉路径携带）。
    """

    param: str
    style: str = "top_level"
    text_default: bool | None = False
    model_default_prefix: str | None = None
    apply_to: str = "all"


@dataclass(frozen=True)
class ProviderSpec:
    """一家供应商的完整描述。key_env/model_env 是 .env 键名，settings 字段名即其小写。"""

    name: str
    base_url: str
    key_env: str
    display_name: str = ""               # 前端下拉的显示名（/providers/catalog 下发）
    env_prefix: str | None = None        # PROVIDER_ENV_PREFIX 派生源；None=前端选择不持久化到 env
    model_env: str | None = None         # 模型名键名；None=无独立 VLM 模型配置（纯 STT/TTS 家）
    default_model: str = ""
    preset_models: tuple[str, ...] = ()
    allowed_host: str = ""               # /models SSRF 白名单域名后缀；空=不进白名单
    supports_vlm: bool = True            # False=仅 STT/TTS 使用（siliconflow/xiaomi）
    thinking: ThinkingSpec | None = None
    image_max_side: int = 2048           # 统一走 downscale_jpeg_bytes 的上限（DashScope=800）
    normalize_content: bool = False      # content/reasoning list-dict 归一化（DashScope）
    stream_fallback_non_stream: bool = False  # 流式空内容时非流式兜底（DashScope）
    strip_model_suffix: bool = False     # 剥离 ":free" 后缀（DashScope）
    merge_system_into_user: bool = False  # 视觉请求把 system 文本并入首条 user、不发 system role（a81e300）
    error_label: str = ""                # 错误文案与日志里的 provider 显示名


PROVIDER_REGISTRY: dict[str, ProviderSpec] = {
    "modelscope": ProviderSpec(
        name="modelscope",
        base_url="https://api-inference.modelscope.cn/v1",
        key_env="MODELSCOPE_API_KEY",
        display_name="ModelScope",
        env_prefix="MODELSCOPE",
        model_env="MODELSCOPE_VLM_MODEL",
        default_model="Qwen/Qwen3.8-27B",
        # 注意：单元素元组必须带尾逗号，否则 ("x") 是字符串不是元组
        preset_models=("Qwen/Qwen3.8-27B",),
        allowed_host="modelscope.cn",
        thinking=ThinkingSpec(param="enable_thinking"),
        error_label="ModelScope",
    ),
    "dashscope": ProviderSpec(
        name="dashscope",
        base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
        key_env="DASHSCOPE_API_KEY",
        display_name="阿里云百炼",
        env_prefix="DASHSCOPE",
        model_env="DASHSCOPE_VLM_MODEL",
        default_model="qwen3.8-27b",
        preset_models=("qwen3.8-27b",),
        allowed_host="aliyuncs.com",
        thinking=ThinkingSpec(param="enable_thinking", text_default=False, model_default_prefix="qwen3.6"),
        image_max_side=800,
        normalize_content=True,
        stream_fallback_non_stream=True,
        strip_model_suffix=True,
        error_label="DashScope",
    ),
    "deepseek": ProviderSpec(
        name="deepseek",
        base_url="https://api.deepseek.com",
        key_env="DEEPSEEK_API_KEY",
        display_name="DeepSeek",
        env_prefix="DEEPSEEK",
        model_env="DEEPSEEK_VLM_MODEL",
        default_model="deepseek-v4-flash",
        preset_models=("deepseek-v4-flash", "deepseek-v4-pro", "deepseek-v4-flash-vision-exp"),
        allowed_host="deepseek.com",
        # DeepSeek V4 思考开关是 thinking.type 结构（{"type":"enabled"/"disabled"}，
        # 服务端默认 enabled），与智谱同款写法；文本/视觉路径均生效。
        # 旧值顶层布尔 enable_thinking 是无效字段名，被服务端忽略 → 思考恒开
        # 且关不掉（2026-09-07 按官方文档修正）。
        # 2026-09-15：思考已全局写死关闭（unified.THINKING_HARD_DISABLED），
        # 此处 text_default 不再起作用，保留原值仅为表达各家差异。
        thinking=ThinkingSpec(param="thinking", style="type_object", text_default=None),
        # 2026-09-11 实测：流式轮偶发空完成（无正文无 tool_calls）且任务无痕死亡
        # （用户"帮我打开排气上"请求 26s 无任何产出/日志），开启非流式兜底兜住。
        stream_fallback_non_stream=True,
        error_label="DeepSeek",
    ),
    "sensenova": ProviderSpec(
        name="sensenova",
        base_url="https://token.sensenova.cn/v1",
        key_env="SENSENOVA_API_KEY",
        display_name="SenseNova",
        env_prefix="SENSENOVA",
        model_env="SENSENOVA_VLM_MODEL",
        default_model="sensenova-6.8-flash-lite",
        preset_models=("sensenova-6.8-flash-lite",),
        allowed_host="sensenova.cn",
        # Token Plan 兼容端点实测：messages 含 system role + 带图 user 时服务端会把
        # 会话降级为纯文本模型（模型自述"我是文本模型，看不到图片"）。
        # 视觉请求须把 system 文本并入首条 user，见 unified._merge_system_into_user。
        merge_system_into_user=True,
        error_label="SenseNova",
    ),
    "zhipu": ProviderSpec(
        name="zhipu",
        base_url="https://open.bigmodel.cn/api/paas/v4",
        key_env="ZHIPU_API_KEY",
        display_name="智谱GLM",
        env_prefix="ZHIPU",
        model_env="ZHIPU_VLM_MODEL",
        default_model="glm-4.6v-flash",
        preset_models=("glm-4.6v-flash",),
        allowed_host="bigmodel.cn",
        # 智谱现状：文本路径仅显式传值时携带 thinking.type；视觉路径总是携带
        thinking=ThinkingSpec(param="thinking", style="type_object", text_default=None),
        error_label="Zhipu",
    ),
    # ---- 以下为 STT/TTS 专用条目（不参与 VLM 构建，阶段 D 的 STT/TTS 注册表继续复用）----
    "siliconflow": ProviderSpec(
        name="siliconflow",
        base_url="https://api.siliconflow.cn/v1",
        key_env="SILICONFLOW_API_KEY",
        display_name="硅基流动",
        default_model="",
        allowed_host="siliconflow.cn",
        supports_vlm=False,
        error_label="SiliconFlow",
    ),
    "xiaomi": ProviderSpec(
        name="xiaomi",
        base_url="https://api.xiaomimimo.com/v1",
        key_env="XIAOMI_TTS_API_KEY",
        display_name="小米MiMo",
        default_model="",
        allowed_host="xiaomimimo.com",
        supports_vlm=False,
        error_label="Xiaomi",
    ),
}

# custom provider（api_format=openai）/ 未指定 spec 时的通用 OpenAI 兼容端点
GENERIC_OPENAI_SPEC = ProviderSpec(
    name="openai",
    base_url="https://api.openai.com/v1",
    key_env="OPENAI_API_KEY",
    default_model="",
    allowed_host="openai.com",
    error_label="OpenAI",
)


def get_vlm_spec(provider: str | None) -> ProviderSpec | None:
    """按 provider 名查 VLM spec；不存在或该家不支持 VLM 时返回 None。"""
    if not provider:
        return None
    spec = PROVIDER_REGISTRY.get(provider)
    if spec is None or not spec.supports_vlm:
        return None
    return spec


def vlm_specs() -> dict[str, ProviderSpec]:
    """全部支持 VLM 的 provider spec（构建 runtime / 目录派生用）。"""
    return {name: spec for name, spec in PROVIDER_REGISTRY.items() if spec.supports_vlm}


@dataclass(frozen=True)
class TTSSpec:
    """一家 TTS 供应商的描述。key/model/voice 的 env 值直接用 settings 字段名（小写）。

    streaming=True 表示该家支持流式 PCM 播报（配合 runtime.tts.stream_pcm_chunks），
    业务侧的 `{"dashscope","xiaomi"}` 硬编码判断由此收敛。
    """

    name: str
    display_name: str = ""     # 前端下拉的显示名（/providers/catalog 下发）
    key_env: str = ""          # settings 密钥字段名；空=无需密钥（本地服务）
    model_env: str = ""
    default_model: str = ""
    voice_env: str = ""        # settings 音色字段名；空=用 default_voice
    default_voice: str = ""
    streaming: bool = False


TTS_REGISTRY: dict[str, TTSSpec] = {
    "siliconflow": TTSSpec(
        name="siliconflow",
        display_name="硅基流动",
        key_env="siliconflow_api_key",   # STT/TTS 共用同一把 key（既有约定）
        model_env="siliconflow_tts_model",
        default_model="fnlp/MOSS-TTSD-v0.5",
        voice_env="siliconflow_tts_voice",
        default_voice="fnlp/MOSS-TTSD-v0.5:claire",
    ),
    "dashscope": TTSSpec(
        name="dashscope",
        display_name="阿里云百炼",
        key_env="dashscope_tts_api_key",  # 与 DASHSCOPE_API_KEY 同值不同字段（既有约定）
        model_env="dashscope_tts_model",
        default_model="qwen3-tts-instruct-flash-realtime",
        voice_env="dashscope_tts_voice",
        default_voice="female-qn-tingting",
        streaming=True,
    ),
    "xiaomi": TTSSpec(
        name="xiaomi",
        display_name="小米MiMo",
        key_env="xiaomi_tts_api_key",
        model_env="xiaomi_tts_model",
        default_model="mimo-v2.5-tts",
        voice_env="xiaomi_tts_voice",
        default_voice="mimo_default",
        streaming=True,
    ),
    "local_cosyvoice": TTSSpec(
        name="local_cosyvoice",
        display_name="本地 CosyVoice",
        key_env="",                       # 本地服务无需密钥
        model_env="local_cosyvoice_model",
        default_model="CosyVoice-300M-SFT",
        voice_env="local_cosyvoice_voice",
        default_voice="中文女",
    ),
}


@dataclass(frozen=True)
class STTCatalogEntry:
    """STT 供应商的目录数据（供 /providers/catalog 下发前端下拉）。

    仅描述"可选性与默认模型"；运行时构建仍走 build_stt_runtime。
    """

    name: str
    display_name: str
    model: str                 # 默认模型名/描述文案（browser/edge 为展示文案）
    has_api_key: bool = False


STT_CATALOG: list[STTCatalogEntry] = [
    STTCatalogEntry(name="browser", display_name="浏览器内置", model="Web Speech API"),
    STTCatalogEntry(name="siliconflow", display_name="硅基流动",
                    model="FunAudioLLM/SenseVoiceSmall", has_api_key=True),
    STTCatalogEntry(name="qwen3_asr_realtime", display_name="阿里云 Qwen3",
                    model="qwen3-asr-flash-realtime-2026-02-10"),
    STTCatalogEntry(name="fun_asr_realtime", display_name="阿里云 Fun-ASR 实时",
                    model="fun-asr-realtime-2026-02-28"),
    # 端侧语音（香橙派）：唤醒词 KWS + 流式 ASR 全部运行在端侧设备（默认
    # ws://192.168.1.248:8767/8768），完全离线；链路分支见 useVoiceOrchestration。
    STTCatalogEntry(name="edge", display_name="端侧语音（香橙派）",
                    model="FunASR 流式（香橙派端侧）"),
]

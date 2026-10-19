"""注册表驱动的统一 OpenAI 兼容 Provider（文字对话 + 视觉分析同走 /chat/completions）。

合并自 5 个历史 provider 文件（modelscope/dashscope/deepseek/openai/zhipu）中
各自最完善的实现细节，各家差异由 ProviderSpec 数据表达：

- 思考参数写法（顶层布尔 / 智谱 thinking.type / DeepSeek 仅视觉携带）→ ThinkingSpec
- 图片压缩上限（2048 边 / DashScope 800 边）→ spec.image_max_side，统一走
  downscale_jpeg_bytes 且放线程执行（原 DashScope 是同步压缩，顺带修复卡事件循环）
- DashScope 独有 quirks（content/reasoning 归一化、流式空内容非流式兜底、
  模型名 ":free" 后缀剥离）→ spec 开关
- SSE 解析采用各家并集：[DONE] 判断、reasoning_content 外送、
  "已输出不再重试"防重发（原智谱独有，统一启用）、429 指数退避 ×3、首 token 延迟日志

错误语义：HTTP 非 200 / 网络异常重试用尽时 raise ModelProviderError（见 errors.py），
provider 层不再把错误文本伪装成生成内容 yield。
"""

from __future__ import annotations

import asyncio
import base64
import json
import time
from typing import AsyncIterator, Awaitable, Callable

import httpx

from .base import MultimodalLLMProvider, downscale_jpeg_bytes
from .errors import ModelProviderError
from .registry import GENERIC_OPENAI_SPEC, ProviderSpec
from ..logger import backend_log

# 模块级单例 HTTP 客户端（原分散在 5 个 provider 文件中的同款实现，收敛为一处）
_limits = httpx.Limits(max_keepalive_connections=5, max_connections=10)
_http_client: httpx.AsyncClient | None = None


def get_http_client() -> httpx.AsyncClient:
    global _http_client
    if _http_client is None:
        _http_client = httpx.AsyncClient(timeout=120.0, limits=_limits, trust_env=False)
    return _http_client


# 🔒 思考模式写死关闭（2026-09-15 用户指令）
#
# 背景：DeepSeek V4 服务端默认 thinking=enabled，每轮先产出一段思考再开口；
# 实测同一会话连问 5 次首字延迟 0.6s → 3.4s（思考长度随上下文增长），
# 演示场景首字延迟是第一指标，故全局压平为 disabled。
#
# 这里是唯一权威收口：`_apply_thinking` 是所有 provider / 所有路径（文本 + 视觉）
# 写入思考参数的唯一入口。置 False 恢复原行为（前端开关 + 会话状态重新生效）。
THINKING_HARD_DISABLED = True


class UnifiedOpenAIProvider(MultimodalLLMProvider):
    """按 ProviderSpec 构建的统一 OpenAI 兼容供应商。"""

    def __init__(
        self,
        spec: ProviderSpec = GENERIC_OPENAI_SPEC,
        *,
        api_key: str,
        model: str,
        base_url: str | None = None,
    ):
        self.spec = spec
        self.api_key = api_key
        self.model = self._normalize_model(model)
        self.base_url = (base_url or spec.base_url).rstrip("/")
        self.last_reasoning = ""

    # ===== spec 驱动的小工具 =====

    def _normalize_model(self, model: str) -> str:
        """兼容前端带 :free 后缀的模型名（DashScope），统一成 API 真实模型名。"""
        if self.spec.strip_model_suffix and model:
            return model.split(":", 1)[0].strip()
        return model

    def _norm_text(self, value) -> str:
        """DashScope 的 content/reasoning 可能是 list/dict 结构，归一化成纯文本。"""
        if value is None:
            return ""
        if isinstance(value, str):
            return value.strip() if self.spec.normalize_content else value
        if not self.spec.normalize_content:
            return str(value)
        parts: list[str] = []
        for item in value if isinstance(value, list) else [value]:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, dict):
                if item.get("text"):
                    parts.append(str(item["text"]))
            else:
                text = getattr(item, "text", None)
                if text:
                    parts.append(str(text))
        return "".join(parts).strip()

    def _apply_thinking(self, payload: dict, enable_thinking: bool | None, *, vision: bool) -> None:
        """按 spec 把思考开关写进请求体；spec.thinking 为 None 时不携带任何思考字段。

        注意：`THINKING_HARD_DISABLED` 打开时恒发 disabled（显式关闭，用于覆盖
        DeepSeek 服务端"默认 enabled"），传入的 enable_thinking 被忽略。
        """
        ts = self.spec.thinking
        if ts is None:
            return
        if not vision and ts.apply_to == "vision":
            return
        if THINKING_HARD_DISABLED:
            # 显式下发 disabled —— 即便 spec 的 text_default 是 None（不携带字段）也要发，
            # 否则服务端回落到默认 enabled，等于没关。
            enabled = False
        elif vision:
            enabled = False if enable_thinking is None else enable_thinking
        elif enable_thinking is None:
            if ts.model_default_prefix and self.model.startswith(ts.model_default_prefix):
                enabled = True
            elif ts.text_default is None:
                return
            else:
                enabled = ts.text_default
        else:
            enabled = enable_thinking
        if ts.style == "type_object":
            # thinking.type 结构（智谱 / DeepSeek V4 官方格式，区别于百炼的顶层布尔）
            payload[ts.param] = {"type": "enabled" if enabled else "disabled"}
        else:
            payload[ts.param] = enabled

    def _merge_system_into_user(self, messages: list[dict]) -> list[dict]:
        """把 system 文本并入首条 user 消息、移除 system role（原 messages 不改动）。

        规避 SenseNova Token Plan 兼容端点的服务端行为：messages 含 system role
        与带图 user 组合时，会话被降级为纯文本模型处理、图片被丢弃
        （实测模型自述"我是文本模型，看不到图片"）。纯文本请求不受影响，无需合并。
        """
        if not self.spec.merge_system_into_user:
            return messages
        if not messages or messages[0].get("role") != "system":
            return messages
        sys_text = str(messages[0].get("content") or "")
        rest = [dict(m) for m in messages[1:]]
        if rest and rest[0].get("role") == "user":
            content = rest[0].get("content")
            if isinstance(content, list):
                # 带图 content 数组：把 system 文本拼进首个 text 项
                merged = False
                for item in content:
                    if isinstance(item, dict) and item.get("type") == "text":
                        item["text"] = f"{sys_text}\n\n{item.get('text') or ''}".strip()
                        merged = True
                        break
                if not merged:
                    content.insert(0, {"type": "text", "text": sys_text})
            else:
                rest[0]["content"] = f"{sys_text}\n\n{content}".strip() if content else sys_text
        else:
            rest.insert(0, {"role": "user", "content": sys_text})
        return rest

    def _build_payload(
        self,
        messages: list[dict],
        *,
        stream: bool = True,
        temperature: float | None = None,
        max_tokens: int | None = None,
        top_p: float | None = None,
        n: int | None = None,
        frequency_penalty: float | None = None,
        response_format: str | None = None,
        vision: bool = False,
        tools: list[dict] | None = None,
    ) -> dict:
        if vision:
            messages = self._merge_system_into_user(messages)
        payload: dict = {
            "model": self.model,
            "messages": messages,
            "stream": stream,
        }
        if tools:
            payload["tools"] = tools
            payload["tool_choice"] = "auto"
        if temperature is not None:
            payload["temperature"] = temperature
        if max_tokens is not None:
            payload["max_tokens"] = max_tokens
        if top_p is not None:
            payload["top_p"] = top_p
        if n is not None and n > 1:
            payload["n"] = n
        if frequency_penalty is not None:
            payload["frequency_penalty"] = frequency_penalty
        if response_format == "json":
            payload["response_format"] = {"type": "json_object"}
        return payload

    @staticmethod
    def _text_messages(prompt: str, system_prompt: str | None = None, history: list | None = None) -> list[dict]:
        messages: list[dict] = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        if history:
            for msg in history:
                messages.append({"role": msg["role"], "content": msg["content"]})
        messages.append({"role": "user", "content": prompt})
        return messages

    async def _vision_message(self, prompt: str, jpeg_bytes: bytes | None) -> dict:
        content: list[dict] = [{"type": "text", "text": prompt}]
        if jpeg_bytes:
            # USB 摄像头原生 2560x1440 超部分平台限制，按 spec 上限先缩再编码（缩放放线程避免卡事件循环）
            jpeg_bytes = await asyncio.to_thread(downscale_jpeg_bytes, jpeg_bytes, self.spec.image_max_side)
            image_b64 = base64.b64encode(jpeg_bytes).decode("utf-8")
            content.append({
                "type": "image_url",
                "image_url": {"url": f"data:image/jpeg;base64,{image_b64}"},
            })
        return {"role": "user", "content": content}

    def _append_file_images(self, content: list[dict], file_images: list[tuple[bytes, str]] | None) -> list[dict]:
        if file_images:
            for img_bytes, img_type in file_images:
                img_b64 = base64.b64encode(img_bytes).decode("utf-8")
                content.append({
                    "type": "image_url",
                    "image_url": {"url": f"data:{img_type};base64,{img_b64}"},
                })
        return content

    # ===== 公开接口 =====

    async def _stream_with_tool_loop(
        self,
        messages: list[dict],
        *,
        vision: bool,
        tools: list[dict] | None,
        tool_executor: Callable[[str, str], Awaitable[str]] | None,
        max_tool_rounds: int,
        **gen_kwargs,
    ) -> AsyncIterator[tuple[str, str]]:
        """带 function calling 的流式包装：模型发起 tool_calls 时执行工具并把结果
        回填为下一轮 messages，最多 max_tool_rounds 轮；对外仍然只 yield (正文, reasoning)。

        端点不支持 tools（携带 tools 请求 4xx 且未产出任何正文）时降级为无工具重试一次，
        保证功能开关不影响原有问答可用性。

        连续对话（2026-09-11）：模型"先过程播报再给 tool_calls"（"正在为您查询风扇状态。"+
        同轮 tool_calls）时也执行工具并把播报正文一并回填 assistant.content，工具结果
        回填后继续下一轮，由模型续答实际结果——旧逻辑正文已交付即丢弃工具调用，导致
        只有"正在查询"没有下文。
        """
        degraded = False
        while True:
            use_tools = bool(tools) and tool_executor is not None and not degraded
            tc_out: list[dict] | None = [] if use_tools else None
            got_text = False
            round_text_parts: list[str] = []
            try:
                async for delta, reasoning in self._stream_messages(
                    messages,
                    vision=vision,
                    tools=(tools if use_tools else None),
                    tool_calls_out=tc_out,
                    **gen_kwargs,
                ):
                    if delta:
                        got_text = True
                        round_text_parts.append(delta)
                    yield delta, reasoning
            except ModelProviderError as exc:
                if use_tools and not got_text:
                    backend_log(
                        f"[{self.spec.error_label}] 携带 tools 的请求失败"
                        f"(status={exc.status})，降级为无工具重试"
                    )
                    degraded = True
                    continue
                raise

            if not (tc_out and any(c.get("name") for c in tc_out)):
                return
            if max_tool_rounds <= 0:
                backend_log(f"[{self.spec.error_label}] 工具调用轮数已达上限，丢弃后续 tool_calls")
                return

            round_text = "".join(round_text_parts).strip()
            tool_call_msgs = []
            for i, call in enumerate(tc_out):
                call_id = call.get("id") or f"call_{i}"
                tool_call_msgs.append({
                    "id": call_id,
                    "type": "function",
                    "function": {"name": call["name"], "arguments": call.get("arguments") or "{}"},
                })
            messages = list(messages) + [
                {"role": "assistant", "content": round_text, "tool_calls": tool_call_msgs}
            ]
            for i, call in enumerate(tc_out):
                name = call["name"]
                arguments = tool_call_msgs[i]["function"]["arguments"]
                backend_log(f"[LLM 工具] {name}({arguments[:80]})")
                result = await tool_executor(name, arguments)
                backend_log(f"[LLM 工具] {name} -> {str(result)[:150]}")
                messages.append({"role": "tool", "tool_call_id": tool_call_msgs[i]["id"], "content": result})
            max_tool_rounds -= 1

    async def stream_chat(
        self,
        prompt: str,
        system_prompt: str | None = None,
        history: list | None = None,
        *,
        temperature: float | None = None,
        max_tokens: int | None = None,
        top_p: float | None = None,
        n: int | None = None,
        frequency_penalty: float | None = None,
        response_format: str | None = None,
        enable_thinking: bool | None = None,
        tools: list[dict] | None = None,
        tool_executor: Callable[[str, str], Awaitable[str]] | None = None,
        max_tool_rounds: int = 3,
    ) -> AsyncIterator[tuple[str, str]]:
        messages = self._text_messages(prompt, system_prompt, history)
        if tools and tool_executor is not None:
            async for delta, reasoning in self._stream_with_tool_loop(
                messages,
                vision=False,
                tools=tools,
                tool_executor=tool_executor,
                max_tool_rounds=max_tool_rounds,
                temperature=temperature,
                max_tokens=max_tokens,
                top_p=top_p,
                n=n,
                frequency_penalty=frequency_penalty,
                response_format=response_format,
                enable_thinking=enable_thinking,
            ):
                yield delta, reasoning
            return
        async for delta, reasoning in self._stream_messages(
            messages,
            temperature=temperature,
            max_tokens=max_tokens,
            top_p=top_p,
            n=n,
            frequency_penalty=frequency_penalty,
            response_format=response_format,
            enable_thinking=enable_thinking,
            vision=False,
        ):
            yield delta, reasoning

    async def stream_chat_with_image(
        self,
        prompt: str,
        jpeg_bytes: bytes | None = None,
        *,
        file_images: list[tuple[bytes, str]] | None = None,
        system_prompt: str | None = None,
        history: list | None = None,
        enable_thinking: bool | None = None,
        tools: list[dict] | None = None,
        tool_executor: Callable[[str, str], Awaitable[str]] | None = None,
        max_tool_rounds: int = 3,
    ) -> AsyncIterator[tuple[str, str]]:
        messages: list[dict] = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        if history:
            for msg in history:
                messages.append({"role": msg["role"], "content": msg["content"]})
        vision_msg = await self._vision_message(prompt, jpeg_bytes)
        self._append_file_images(vision_msg["content"], file_images)
        messages.append(vision_msg)
        if tools and tool_executor is not None:
            async for delta, reasoning in self._stream_with_tool_loop(
                messages,
                vision=True,
                tools=tools,
                tool_executor=tool_executor,
                max_tool_rounds=max_tool_rounds,
                enable_thinking=enable_thinking,
            ):
                yield delta, reasoning
            return
        async for delta, reasoning in self._stream_messages(
            messages, enable_thinking=enable_thinking, vision=True
        ):
            yield delta, reasoning

    async def analyze_image(self, jpeg_bytes: bytes, prompt: str, enable_thinking: bool = False) -> str:
        """非流式图像分析（/models/validate、视觉危险检测、/proxy/vlm 等场景）。"""
        vision_msg = await self._vision_message(prompt, jpeg_bytes)
        payload = self._build_payload([vision_msg], stream=False, vision=True)
        self._apply_thinking(payload, enable_thinking, vision=True)
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        client = get_http_client()
        max_retries = 3
        for attempt in range(max_retries):
            try:
                response = await client.post(
                    f"{self.base_url}/chat/completions",
                    headers=headers,
                    json=payload,
                )
                if response.status_code == 429:
                    if attempt < max_retries - 1:
                        await asyncio.sleep(2**attempt)
                        continue
                    raise ModelProviderError(
                        self.spec.error_label, status=429, detail="服务繁忙",
                        user_message="（服务繁忙，请稍后再试）",
                    )
                if response.status_code >= 400:
                    error_body = response.text
                    backend_log(f"[{self.spec.error_label}] 视觉调用失败 status={response.status_code}: {error_body[:300]}")
                    raise ModelProviderError(self.spec.error_label, status=response.status_code, detail=error_body)
                result = response.json()
                choices = result.get("choices") or []
                if choices:
                    message = choices[0].get("message") or {}
                    content = self._norm_text(message.get("content"))
                    if content:
                        return content
                return "（未获取到视觉分析结果）"
            except ModelProviderError:
                raise
            except Exception as e:
                backend_log(f"[{self.spec.error_label}] 视觉分析异常: {e}")
                if attempt < max_retries - 1:
                    await asyncio.sleep(1)
                    continue
                raise ModelProviderError(self.spec.error_label, detail=str(e))
        raise ModelProviderError(self.spec.error_label, detail="视觉分析重试次数已用尽")

    async def analyze_image_stream(
        self,
        jpeg_bytes: bytes,
        prompt: str,
        enable_thinking: bool = False,
    ) -> AsyncIterator[str]:
        """流式图像分析，逐步返回结果。"""
        vision_msg = await self._vision_message(prompt, jpeg_bytes)
        async for delta, _reasoning in self._stream_messages(
            [vision_msg], enable_thinking=enable_thinking, vision=True
        ):
            if delta:
                yield delta

    # ===== 流式核心 =====

    async def _stream_messages(
        self,
        messages: list[dict],
        *,
        temperature: float | None = None,
        max_tokens: int | None = None,
        top_p: float | None = None,
        n: int | None = None,
        frequency_penalty: float | None = None,
        response_format: str | None = None,
        enable_thinking: bool | None = None,
        vision: bool = False,
        tools: list[dict] | None = None,
        tool_calls_out: list[dict] | None = None,
    ) -> AsyncIterator[tuple[str, str]]:
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        payload = self._build_payload(
            messages,
            stream=True,
            temperature=temperature,
            max_tokens=max_tokens,
            top_p=top_p,
            n=n,
            frequency_penalty=frequency_penalty,
            response_format=response_format,
            vision=vision,
            tools=tools,
        )
        self._apply_thinking(payload, enable_thinking, vision=vision)

        label = self.spec.error_label
        max_retries = 3
        yielded = False  # 已向消费者输出过内容就不再重试，避免重发造成重复
        for attempt in range(max_retries):
            try:
                client = get_http_client()
                async with client.stream(
                    "POST",
                    f"{self.base_url}/chat/completions",
                    headers=headers,
                    json=payload,
                ) as response:
                    if response.status_code == 429:
                        if attempt < max_retries - 1:
                            await asyncio.sleep(2**attempt)
                            continue
                        raise ModelProviderError(
                            label, status=429, detail="服务繁忙",
                            user_message="（服务繁忙，请稍后再试）",
                        )

                    if response.status_code >= 400:
                        error_body = (await response.aread()).decode("utf-8", errors="replace")
                        backend_log(f"[{label}] 请求失败 status={response.status_code}: {error_body[:300]}")
                        raise ModelProviderError(label, status=response.status_code, detail=error_body)

                    buffer = ""
                    content_buffer = ""
                    buffer_threshold = 1
                    produced = False
                    first_token_time = None
                    req_start = time.time()

                    async for chunk in response.aiter_text():
                        buffer += chunk
                        while "\n" in buffer:
                            line, buffer = buffer.split("\n", 1)
                            if not line.strip() or not line.startswith("data: "):
                                continue
                            json_str = line[6:].strip()
                            if json_str == "[DONE]":
                                continue
                            try:
                                data = json.loads(json_str)
                            except Exception:
                                continue
                            choices = data.get("choices") or []
                            if not choices:
                                continue
                            delta = choices[0].get("delta") or {}
                            reasoning = self._norm_text(delta.get("reasoning_content"))
                            content = self._norm_text(delta.get("content"))

                            # function calling：tool_calls 分片按 index 归并到 tool_calls_out，
                            # 由调用方在流结束后统一执行（本层不执行、不上屏）
                            if tool_calls_out is not None:
                                for frag in delta.get("tool_calls") or []:
                                    try:
                                        idx = int(frag.get("index", 0))
                                    except (TypeError, ValueError):
                                        idx = 0
                                    while len(tool_calls_out) <= idx:
                                        tool_calls_out.append({"id": "", "name": "", "arguments": ""})
                                    entry = tool_calls_out[idx]
                                    frag_id = str(frag.get("id") or "")
                                    if frag_id and frag_id not in entry["id"]:
                                        entry["id"] += frag_id
                                    fn = frag.get("function") or {}
                                    fn_name = str(fn.get("name") or "")
                                    if fn_name and fn_name not in entry["name"]:
                                        entry["name"] += fn_name
                                    if fn.get("arguments"):
                                        entry["arguments"] += str(fn.get("arguments"))

                            if reasoning:
                                self.last_reasoning += reasoning
                                yield "", reasoning
                            if not content:
                                continue

                            produced = True
                            yielded = True
                            content_buffer += content
                            if first_token_time is None:
                                first_token_time = time.time()
                                backend_log(f"[{label}] 首 token 延迟: {first_token_time - req_start:.2f}s")
                            if len(content_buffer) >= buffer_threshold:
                                yield content_buffer, ""
                                content_buffer = ""

                    if content_buffer:
                        yield content_buffer, ""
                    if produced:
                        return

                    if tool_calls_out and any(c.get("name") for c in tool_calls_out):
                        # 本轮是纯 tool_calls（无正文）：交给调用方执行工具后发起下一轮。
                        # 不能落非流式兜底——那会把工具调用轮当"空响应"处理。
                        return

                    if self.spec.stream_fallback_non_stream:
                        # 流式无可见内容时非流式兜底（DashScope 思考模型偶发只吐 reasoning；
                        # 2026-09-11 DeepSeek 也开启——流式轮偶发空完成曾让工具任务无痕死亡）
                        backend_log(f"[{label}] 流式结果未返回可展示内容，尝试非流式兜底")
                        reasoning_text, fallback = await self._generate_message_http(
                            messages, payload, tool_calls_out=tool_calls_out
                        )
                        if fallback:
                            yield fallback, reasoning_text
                        elif reasoning_text:
                            yield "（模型已完成思考，但未返回可显示内容，请重试）", ""
                        elif tool_calls_out and any(c.get("name") for c in tool_calls_out):
                            # 兜底拿到的是工具调用：交给工具循环执行，不输出占位文案
                            return
                        else:
                            yield "（模型没有返回有效内容，请重试）", ""
                    return

            except ModelProviderError:
                raise
            except Exception as e:
                if yielded:
                    backend_log(f"[{label}] 流式请求异常（已输出部分内容，不再重试）: {e}")
                    return
                if attempt < max_retries - 1:
                    await asyncio.sleep(1)
                    continue
                backend_log(f"[{label}] 流式请求异常: {e}")
                raise ModelProviderError(label, detail=str(e))

    async def _generate_message_http(
        self,
        messages: list[dict],
        base_payload: dict,
        tool_calls_out: list[dict] | None = None,
    ) -> tuple[str, str]:
        """非流式补全（流式空内容兜底用）。返回 (reasoning, content)。

        tool_calls_out 非空时，把非流式响应里的 tool_calls 归并进去（仅在尚未
        收集到任何调用时写入），保证"流式空完成→兜底拿到工具调用"的轮次仍能
        进入工具循环，而不是被当成空响应丢弃。
        """
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        payload = dict(base_payload)
        payload["stream"] = False
        client = get_http_client()
        response = await client.post(f"{self.base_url}/chat/completions", headers=headers, json=payload)
        if response.status_code >= 400:
            error_text = response.text.strip() or f"HTTP {response.status_code}"
            backend_log(f"[{self.spec.error_label}] 非流式兜底失败: {error_text[:200]}")
            raise ModelProviderError(self.spec.error_label, status=response.status_code, detail=error_text)
        data = response.json()
        choices = data.get("choices") or []
        if not choices:
            return "", ""
        message = choices[0].get("message") or {}
        if tool_calls_out is not None and not any(c.get("name") for c in tool_calls_out):
            for i, call in enumerate(message.get("tool_calls") or []):
                fn = call.get("function") or {}
                name = str(fn.get("name") or "")
                if not name:
                    continue
                tool_calls_out.append({
                    "id": str(call.get("id") or f"call_{i}"),
                    "name": name,
                    "arguments": str(fn.get("arguments") or "{}"),
                })
        return self._norm_text(message.get("reasoning_content")), self._norm_text(message.get("content"))

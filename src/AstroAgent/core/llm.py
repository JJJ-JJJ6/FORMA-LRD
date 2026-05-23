import logging
from typing import Dict, Any
from langchain_openai import ChatOpenAI


def _detect_vendor(base_url: str) -> str:
    """根据 base_url 判断模型厂商。

    Returns:
        'deepseek' | 'qwen' | 'unknown'
    """
    if not base_url:
        return 'unknown'
    url = base_url.lower()
    if 'deepseek' in url:
        return 'deepseek'
    if 'aliyuncs' in url or 'dashscope' in url or 'qwen' in url:
        return 'qwen'
    return 'unknown'


def _build_thinking_extra_body(mode: str, vendor: str):
    """根据厂商和模式构建 thinking 相关的 extra_body。

    Args:
        mode:   'enabled' | 'disabled'
        vendor: 'deepseek' | 'qwen' | 'unknown'

    Returns:
        dict or None
    """
    if vendor == 'deepseek':
        # DeepSeek API: {"thinking": {"type": "enabled/disabled"}}
        return {'thinking': {'type': mode}}
    if vendor == 'qwen':
        # Qwen3 API: {"enable_thinking": True/False}
        return {'enable_thinking': mode == 'enabled'}
    # 未知厂商：不传，避免参数不兼容
    return None


class BaseLLM():
    """
    LLM initialization
    """
    def __init__(self, model_config: Dict[str, Any]):
        self.model_config = model_config


class ThisIsModel(BaseLLM):
    """
    LLM/VLM initialization
    """
    def create_client(self):
        try:
            thinking = self.model_config.get('thinking', 'disabled')
            base_url = self.model_config.get('base_url', '')
            vendor = _detect_vendor(base_url)

            # thinking 配置处理：
            # - 'none'   : 不传 thinking 字段（适合不支持该参数的模型，如 qwen-vl-max）
            # - 'disabled': 显式关闭（基础实例默认状态）
            #               want_tools=False+enabled 时由 base_agent bind 覆盖
            # - 'enabled': 基础实例用 enabled，但 want_tools=True 时
            #              base_agent 会 bind 覆盖为 disabled（保证多轮 tool call 安全）
            # 厂商差异由 _build_thinking_extra_body 处理，无需手动区分
            if thinking == 'none':
                extra_body = None
            else:
                extra_body = _build_thinking_extra_body(thinking, vendor)

            client = ChatOpenAI(
                model=self.model_config['model'],
                api_key=self.model_config['api_key'],
                base_url=base_url,
                temperature=self.model_config['temperature'],
                max_tokens=self.model_config['max_tokens'],
                streaming=self.model_config.get('stream', False),
                extra_body=extra_body,
            )
            return client
        except KeyError as e:
            error_message = f"Configuration key missing: {str(e)}"
            logging.error(f"LLM Client creation failed: {error_message}")
            raise ValueError(f"LLM Client creation failed: {error_message}") from e
        except Exception as e:
            logging.error(f"LLM Client creation failed: {str(e)}")
            raise RuntimeError(f"LLM Client creation failed: {str(e)}") from e

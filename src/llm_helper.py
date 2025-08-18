from typing import Optional

from utils import ConfigManager
from openrouter_helper import generate_with_openrouter
from openai_helper import generate_with_openai


def generate_with_llm(context_text: str, instructions_text: str, model: Optional[str] = None) -> str:
    """
    Route generation to the configured provider (OpenRouter or OpenAI).

    Provider is selected via config key: llm.provider
    """
    provider = (ConfigManager.get_config_value('llm', 'provider') or 'openrouter').strip().lower()
    if provider == 'openai':
        return generate_with_openai(context_text, instructions_text, model=model)
    # default to openrouter
    return generate_with_openrouter(context_text, instructions_text, model=model)



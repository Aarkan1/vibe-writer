import os
from typing import Optional, List, Dict

# Minimal OpenRouter chat helper using direct HTTP requests.
# Reads API key from OPENROUTER_API_KEY (preferred) or OPENAI_API_KEY.
# Returns empty string on failure to allow graceful fallback to transcription.

import requests
from utils import ConfigManager
from dotenv import load_dotenv


def generate_with_openrouter(context_text: str, instructions_text: str, model: Optional[str] = None, history_messages: Optional[List[Dict[str, str]]] = None) -> str:
    """
    Call OpenRouter with context and instructions and return the assistant's response text.

    Args:
        context_text: Copied selection text used as source/context.
        instructions_text: Transcribed speech providing how to use the context.
        model: Optional model override (env OPENROUTER_MODEL used if not provided).

    Returns:
        Assistant message content as a string, or empty string on failure.
    """
    # Load env in case app was launched without environment populated
    load_dotenv()
    api_key = os.getenv('OPENROUTER_API_KEY') or ''
    # Prefer explicit param, then config selection, then env, then fallback default
    configured_model = None
    try:
        configured_model = ConfigManager.get_config_value('openrouter', 'model')
    except Exception:
        configured_model = None
    chosen_model = model or configured_model or os.getenv('OPENROUTER_MODEL') or 'google/gemini-2.0-flash-exp:free'

    if not api_key:
        ConfigManager.console_print('OpenRouter: missing API key; skipping prompt.')
        return ''

    try:
        # Pull prompts from config, falling back to previous defaults
        system_prompt = (
            ConfigManager.get_config_value('openrouter', 'system_prompt')
            or 'You are a precise text-transformation assistant. Follow the instructions exactly, using the provided context. Return only the final result without extra commentary.'
        )
        user_template = (
            ConfigManager.get_config_value('openrouter', 'user_prompt')
            or (
                'CONTEXT:\n{context}\n\n'
                'INSTRUCTIONS:\n{instructions}\n\n'
                'Please produce the final output now.'
            )
        )
        try:
            user_content = user_template.format(context=context_text, instructions=instructions_text)
        except Exception:
            # On bad templating, degrade gracefully by concatenation
            user_content = f'CONTEXT:\n{context_text}\n\nINSTRUCTIONS:\n{instructions_text}\n\nPlease produce the final output now.'

        messages = [
            {
                'role': 'system',
                'content': system_prompt,
            },
        ]
        # Insert prior chat turns, if any (user/assistant only)
        if history_messages:
            try:
                for m in history_messages:
                    role = (m.get('role') or '').strip()
                    content = (m.get('content') or '').strip()
                    if role in ('user', 'assistant') and content:
                        messages.append({'role': role, 'content': content})
            except Exception:
                pass
        # Append the current user request constructed from context/instructions
        messages.append({
            'role': 'user',
            'content': user_content,
        })

        headers = {
            'Authorization': f'Bearer {api_key}',
        }

        payload = {
            'model': chosen_model,
            'messages': messages,
        }

        # Log the prompt used (system + user) for debugging/traceability
        try:
            ConfigManager.console_print('OpenRouter prompt (system): ' + messages[0]['content'])
            ConfigManager.console_print('OpenRouter prompt (user):\n' + messages[1]['content'])
        except Exception:
            # Be resilient if structure changes
            ConfigManager.console_print('OpenRouter: failed to log prompt messages.')

        ConfigManager.console_print(
            f'OpenRouter: POST chat/completions model={chosen_model} | ctx_len={len(context_text)} | instr_len={len(instructions_text)} | history={len(history_messages or [])}'
        )
        resp = requests.post(
            url='https://openrouter.ai/api/v1/chat/completions',
            headers=headers,
            json=payload,
            timeout=45,
        )

        if resp.status_code != 200:
            ConfigManager.console_print(f'OpenRouter HTTP {resp.status_code}: {resp.text[:200]}')
            return ''

        data = resp.json()
        choices = data.get('choices') or []
        if not choices:
            ConfigManager.console_print('OpenRouter: no choices in response.')
            return ''
        message = choices[0].get('message') or {}
        text = (message.get('content') or '').strip()
        if not text:
            ConfigManager.console_print('OpenRouter: empty response content.')
        else:
            # Log the response content for inspection
            ConfigManager.console_print('OpenRouter response content:\n' + text)
        return text
    except Exception as e:
        ConfigManager.console_print(f'OpenRouter request failed: {e}')
        return ''



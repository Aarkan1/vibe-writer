import os
from typing import Optional, List, Dict, Callable

# Minimal OpenAI chat helper using direct HTTP requests.
# Reads API key from OPENAI_API_KEY.
# Returns empty string on failure to allow graceful fallback to transcription.

import requests
from utils import ConfigManager
try:
    from dotenv import load_dotenv  # type: ignore
except Exception:
    def load_dotenv(*_args, **_kwargs):
        return None


def generate_with_openai(context_text: str, instructions_text: str, model: Optional[str] = None, history_messages: Optional[List[Dict[str, str]]] = None) -> str:
    """
    Call OpenAI with context and instructions and return the assistant's response text.

    Args:
        context_text: Copied selection text used as source/context.
        instructions_text: Transcribed speech providing how to use the context.
        model: Optional model override (env OPENAI_MODEL used if not provided).

    Returns:
        Assistant message content as a string, or empty string on failure.
    """
    # Load env in case app was launched without environment populated
    load_dotenv()
    api_key = os.getenv('OPENAI_API_KEY') or ''

    configured_model = None
    try:
        configured_model = ConfigManager.get_config_value('openai', 'model')
    except Exception:
        configured_model = None
    chosen_model = model or configured_model or os.getenv('OPENAI_MODEL') or 'gpt-4o-mini'

    if not api_key:
        ConfigManager.console_print('OpenAI: missing API key; skipping prompt.')
        return ''

    try:
        # Pull prompts from config, falling back to previous defaults
        system_prompt = (
            ConfigManager.get_config_value('openai', 'system_prompt')
            or 'You are a precise text-transformation assistant. Follow the instructions exactly, using the provided context. Return only the final result without extra commentary.'
        )
        user_template = (
            ConfigManager.get_config_value('openai', 'user_prompt')
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
            ConfigManager.console_print('OpenAI prompt (system): ' + messages[0]['content'])
            ConfigManager.console_print('OpenAI prompt (user):\n' + messages[1]['content'])
        except Exception:
            ConfigManager.console_print('OpenAI: failed to log prompt messages.')

        ConfigManager.console_print(
            f'OpenAI: POST chat/completions model={chosen_model} | ctx_len={len(context_text)} | instr_len={len(instructions_text)} | history={len(history_messages or [])}'
        )
        resp = requests.post(
            url='https://api.openai.com/v1/chat/completions',
            headers=headers,
            json=payload,
            timeout=45,
        )

        if resp.status_code != 200:
            ConfigManager.console_print(f'OpenAI HTTP {resp.status_code}: {resp.text[:200]}')
            return ''

        data = resp.json()
        choices = data.get('choices') or []
        if not choices:
            ConfigManager.console_print('OpenAI: no choices in response.')
            return ''
        message = choices[0].get('message') or {}
        text = (message.get('content') or '').strip()
        if not text:
            ConfigManager.console_print('OpenAI: empty response content.')
        else:
            # Log the response content for inspection
            ConfigManager.console_print('OpenAI response content:\n' + text)
        return text
    except Exception as e:
        ConfigManager.console_print(f'OpenAI request failed: {e}')
        return ''


def stream_with_openai(
    context_text: str,
    instructions_text: str,
    model: Optional[str] = None,
    history_messages: Optional[List[Dict[str, str]]] = None,
    on_delta: Optional[Callable[[str], None]] = None,
    cancel_event=None,
) -> str:
    """
    Stream responses from OpenAI and invoke on_delta for each content chunk.

    Returns the full concatenated text (may be empty on failure).
    """
    # Load env in case app was launched without environment populated
    load_dotenv()
    api_key = os.getenv('OPENAI_API_KEY') or ''

    configured_model = None
    try:
        configured_model = ConfigManager.get_config_value('openai', 'model')
    except Exception:
        configured_model = None
    chosen_model = model or configured_model or os.getenv('OPENAI_MODEL') or 'gpt-4o-mini'

    if not api_key:
        ConfigManager.console_print('OpenAI: missing API key; skipping prompt (stream).')
        return ''

    try:
        system_prompt = (
            ConfigManager.get_config_value('openai', 'system_prompt')
            or 'You are a precise text-transformation assistant. Follow the instructions exactly, using the provided context. Return only the final result without extra commentary.'
        )
        user_template = (
            ConfigManager.get_config_value('openai', 'user_prompt')
            or (
                'CONTEXT:\n{context}\n\n'
                'INSTRUCTIONS:\n{instructions}\n\n'
                'Please produce the final output now.'
            )
        )
        try:
            user_content = user_template.format(context=context_text, instructions=instructions_text)
        except Exception:
            user_content = f'CONTEXT:\n{context_text}\n\nINSTRUCTIONS:\n{instructions_text}\n\nPlease produce the final output now.'

        messages = [
            { 'role': 'system', 'content': system_prompt },
        ]
        if history_messages:
            try:
                for m in history_messages:
                    role = (m.get('role') or '').strip()
                    content = (m.get('content') or '').strip()
                    if role in ('user', 'assistant') and content:
                        messages.append({'role': role, 'content': content})
            except Exception:
                pass
        messages.append({ 'role': 'user', 'content': user_content })

        headers = {
            'Authorization': f'Bearer {api_key}',
            'Accept': 'text/event-stream',
        }

        payload = {
            'model': chosen_model,
            'messages': messages,
            'stream': True,
        }

        try:
            ConfigManager.console_print('OpenAI prompt (system/stream): ' + messages[0]['content'])
            ConfigManager.console_print('OpenAI prompt (user/stream):\n' + messages[1]['content'])
        except Exception:
            ConfigManager.console_print('OpenAI: failed to log prompt messages (stream).')

        ConfigManager.console_print(
            f'OpenAI: POST chat/completions (stream) model={chosen_model} | ctx_len={len(context_text)} | instr_len={len(instructions_text)} | history={len(history_messages or [])}'
        )

        resp = requests.post(
            url='https://api.openai.com/v1/chat/completions',
            headers=headers,
            json=payload,
            timeout=90,
            stream=True,
        )

        if resp.status_code != 200:
            # If streaming is not enabled or fails, fall back to non-streaming request
            ConfigManager.console_print(f'OpenAI HTTP (stream) {resp.status_code}: {resp.text[:200]}')
            ConfigManager.console_print('OpenAI: falling back to non-streaming response due to HTTP error.')
            fallback_text = generate_with_openai(
                context_text=context_text,
                instructions_text=instructions_text,
                model=chosen_model,
                history_messages=history_messages,
            )
            # If streaming was enabled and fallback produced output, disable streaming for future calls
            try:
                if fallback_text and (ConfigManager.get_config_value('llm', 'use_streaming') is not False):
                    ConfigManager.console_print('OpenAI: disabling streaming for this session after successful fallback.')
                    ConfigManager.set_config_value(False, 'llm', 'use_streaming')
                    ConfigManager.save_config()
            except Exception:
                pass
            if fallback_text and on_delta:
                try:
                    on_delta(fallback_text)
                except Exception:
                    pass
            return fallback_text

        # Detect if server did not return SSE; if not, parse JSON and/or fall back
        content_type = (resp.headers.get('Content-Type') or resp.headers.get('content-type') or '').lower()
        if 'text/event-stream' not in content_type:
            ConfigManager.console_print(f'OpenAI: non-SSE response detected (Content-Type={content_type or "unknown"}). Attempting JSON parse or fallback...')
            try:
                data = resp.json()
                choices = data.get('choices') or []
                message = (choices[0] or {}).get('message') or {}
                text = (message.get('content') or '').strip()
                if text:
                    # Disable streaming since server did not provide SSE
                    try:
                        if ConfigManager.get_config_value('llm', 'use_streaming') is not False:
                            ConfigManager.console_print('OpenAI: disabling streaming for this session (non-SSE response).')
                            ConfigManager.set_config_value(False, 'llm', 'use_streaming')
                            ConfigManager.save_config()
                    except Exception:
                        pass
                    if on_delta:
                        try:
                            on_delta(text)
                        except Exception:
                            pass
                    ConfigManager.console_print('OpenAI (fallback non-SSE) response content (truncated to 300):\n' + text[:300])
                    return text
            except Exception:
                # Ignore and fall through to explicit fallback
                pass
            # Explicit non-stream fallback request
            fallback_text = generate_with_openai(
                context_text=context_text,
                instructions_text=instructions_text,
                model=chosen_model,
                history_messages=history_messages,
            )
            try:
                if fallback_text and (ConfigManager.get_config_value('llm', 'use_streaming') is not False):
                    ConfigManager.console_print('OpenAI: disabling streaming for this session after successful explicit fallback.')
                    ConfigManager.set_config_value(False, 'llm', 'use_streaming')
                    ConfigManager.save_config()
            except Exception:
                pass
            if fallback_text and on_delta:
                try:
                    on_delta(fallback_text)
                except Exception:
                    pass
            return fallback_text

        # Force UTF-8 decoding for SSE stream to prevent mojibake
        try:
            resp.encoding = 'utf-8'
        except Exception:
            pass

        full_text = ''
        for raw_line in resp.iter_lines(decode_unicode=True):
            # Allow cooperative cancellation during streaming
            try:
                if cancel_event is not None and getattr(cancel_event, 'is_set', None) and cancel_event.is_set():
                    try:
                        resp.close()
                    except Exception:
                        pass
                    return full_text
            except Exception:
                pass
            if not raw_line:
                continue
            line = raw_line.strip()
            if not line.startswith('data:'):
                continue
            data = line[len('data:'):].strip()
            if data == '[DONE]':
                break
            try:
                import json as _json
                obj = _json.loads(data)
                choices = obj.get('choices') or []
                if not choices:
                    continue
                delta = choices[0].get('delta') or {}
                content_piece = (delta.get('content') or '')
                if content_piece:
                    full_text += content_piece
                    if on_delta:
                        try:
                            on_delta(content_piece)
                        except Exception:
                            pass
            except Exception:
                continue

        if full_text:
            ConfigManager.console_print('OpenAI streamed response content (truncated to 300):\n' + full_text[:300])
            return full_text
        # If we reach here with no streamed content, fall back to non-streaming
        ConfigManager.console_print('OpenAI: no streamed content received; falling back to non-streaming response.')
        fallback_text = generate_with_openai(
            context_text=context_text,
            instructions_text=instructions_text,
            model=chosen_model,
            history_messages=history_messages,
        )
        try:
            if fallback_text and (ConfigManager.get_config_value('llm', 'use_streaming') is not False):
                ConfigManager.console_print('OpenAI: disabling streaming for this session after empty stream fallback.')
                ConfigManager.set_config_value(False, 'llm', 'use_streaming')
                ConfigManager.save_config()
        except Exception:
            pass
        if fallback_text and on_delta:
            try:
                on_delta(fallback_text)
            except Exception:
                pass
        return fallback_text
    except Exception as e:
        ConfigManager.console_print(f'OpenAI streaming request failed: {e}')
        return ''


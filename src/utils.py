import yaml
import os
import unicodedata

class ConfigManager:
    _instance = None

    def __init__(self):
        """Initialize the ConfigManager instance."""
        self.config = None
        self.schema = None

    @classmethod
    def initialize(cls, schema_path=None):
        """Initialize the ConfigManager with the given schema path."""
        if cls._instance is None:
            cls._instance = cls()
            cls._instance.schema = cls._instance.load_config_schema(schema_path)
            cls._instance.config = cls._instance.load_default_config()
            cls._instance.load_user_config()

    @classmethod
    def get_schema(cls):
        """Get the configuration schema."""
        if cls._instance is None:
            raise RuntimeError("ConfigManager not initialized")
        return cls._instance.schema

    @classmethod
    def get_config_section(cls, *keys):
        """Get a specific section of the configuration."""
        if cls._instance is None:
            raise RuntimeError("ConfigManager not initialized")

        section = cls._instance.config
        for key in keys:
            if isinstance(section, dict) and key in section:
                section = section[key]
            else:
                return {}
        return section

    @classmethod
    def get_config_value(cls, *keys):
        """Get a specific configuration value using nested keys."""
        if cls._instance is None:
            raise RuntimeError("ConfigManager not initialized")

        value = cls._instance.config
        for key in keys:
            if isinstance(value, dict) and key in value:
                value = value[key]
            else:
                return None
        return value

    @classmethod
    def set_config_value(cls, value, *keys):
        """Set a specific configuration value using nested keys."""
        if cls._instance is None:
            raise RuntimeError("ConfigManager not initialized")

        config = cls._instance.config
        for key in keys[:-1]:
            if key not in config:
                config[key] = {}
            elif not isinstance(config[key], dict):
                config[key] = {}
            config = config[key]
        config[keys[-1]] = value

    @staticmethod
    def load_config_schema(schema_path=None):
        """Load the configuration schema from a YAML file."""
        if schema_path is None:
            base_dir = os.path.dirname(os.path.abspath(__file__))
            schema_path = os.path.join(base_dir, 'config_schema.yaml')

        with open(schema_path, 'r') as file:
            schema = yaml.safe_load(file)
        return schema

    def load_default_config(self):
        """Load default configuration values from the schema."""
        def extract_value(item):
            if isinstance(item, dict):
                if 'value' in item:
                    return item['value']
                else:
                    return {k: extract_value(v) for k, v in item.items()}
            return item

        config = {}
        for category, settings in self.schema.items():
            config[category] = extract_value(settings)
        return config

    def load_user_config(self, config_path=os.path.join('src', 'config.yaml')):
        """Load user configuration and merge with default config."""
        def deep_update(source, overrides):
            for key, value in overrides.items():
                if isinstance(value, dict) and key in source:
                    deep_update(source[key], value)
                else:
                    source[key] = value

        if config_path and os.path.isfile(config_path):
            try:
                with open(config_path, 'r') as file:
                    user_config = yaml.safe_load(file)
                    deep_update(self.config, user_config)
            except yaml.YAMLError:
                print("Error in configuration file. Using default configuration.")

    @classmethod
    def save_config(cls, config_path=os.path.join('src', 'config.yaml')):
        """Save the current configuration to a YAML file."""
        if cls._instance is None:
            raise RuntimeError("ConfigManager not initialized")
        with open(config_path, 'w') as file:
            yaml.dump(cls._instance.config, file, default_flow_style=False)

    @classmethod
    def reload_config(cls):
        """
        Reload the configuration from the file.
        """
        if cls._instance is None:
            raise RuntimeError("ConfigManager not initialized")
        cls._instance.config = cls._instance.load_default_config()
        cls._instance.load_user_config()

    @classmethod
    def config_file_exists(cls):
        """Check if a valid config file exists."""
        config_path = os.path.join('src', 'config.yaml')
        return os.path.isfile(config_path)

    @classmethod
    def console_print(cls, message):
        """Print a message to the console if enabled in the configuration."""
        if cls._instance and cls._instance.config['misc']['print_to_terminal']:
            print(message)


def sanitize_text_for_output(text: str) -> str:
    """Return text normalized for robust cross-app pasting.

    - Normalize to NFC to combine composed characters consistently.
    - Replace narrow no-break space (U+202F) and no-break space (U+00A0) with regular spaces.
    - Keep all other Unicode intact so modern apps render emoji and symbols.
    """
    if text is None:
        return ''
    try:
        normalized = unicodedata.normalize('NFC', text)
    except Exception:
        normalized = text
    # Replace spaces that commonly cause mojibake in some apps
    normalized = normalized.replace('\u202F', ' ').replace('\u00A0', ' ')
    # Attempt to fix common UTF-8→cp1252/latin-1 mojibake (e.g., "Itâs" → "It’s").
    # We only attempt the repair when likely markers appear to avoid changing valid text.
    # Strategy: try to interpret the current text as if it was mis-decoded from UTF-8 bytes
    # using Windows-1252, by re-encoding to cp1252 bytes and decoding as UTF-8.
    # If this reduces suspicious marker characters ("â", "Ã") we accept the fix.
    try:
        # Trigger repair if any common markers of mojibake appear,
        # including generic 'â', 'Ã', or 'Â'.
        suspicious_markers = ('â€™', 'â€œ', 'â€�', 'â€”', 'â€“', 'â€˜', 'â€¦', 'Â', 'Ã', 'â')
        if any(m in normalized for m in suspicious_markers):
            candidate = normalized.encode('cp1252', errors='strict').decode('utf-8', errors='strict')
            score_before = normalized.count('â') + normalized.count('Ã')
            score_after = candidate.count('â') + candidate.count('Ã')
            if score_after < score_before:
                # Re-normalize to NFC after correction
                try:
                    normalized = unicodedata.normalize('NFC', candidate)
                except Exception:
                    normalized = candidate
    except Exception:
        # If anything goes wrong, keep the original normalized text.
        pass
    # Also replace a few fraction glyphs that often misrender as sequences like "Â½"
    try:
        mapping = {
            '½': '1/2',
            '¼': '1/4',
            '¾': '3/4',
        }
        return normalized.translate(str.maketrans(mapping))
    except Exception:
        out = normalized
        out = out.replace('½', '1/2').replace('¼', '1/4').replace('¾', '3/4')
        return out


def transliterate_for_typing(text: str) -> str:
    """Conservatively transliterate a few problematic glyphs for per-key typing fallbacks.

    Only used when we cannot paste via clipboard and must send key events. This avoids
    mojibake like "Â½" by replacing single-codepoint fractions with ASCII equivalents.
    """
    if not text:
        return ''
    mapping = {
        '½': '1/2',
        '¼': '1/4',
        '¾': '3/4',
        # Dashes/ellipsis often display oddly in legacy targets; use simple ASCII
        '–': '-',
        '—': '-',
        '…': '...',
    }
    try:
        return text.translate(str.maketrans(mapping))
    except Exception:
        # Fallback safe return
        out = text
        for k, v in mapping.items():
            out = out.replace(k, v)
        return out

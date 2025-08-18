import os
import sys
import subprocess
try:
    from dotenv import load_dotenv  # type: ignore
except Exception:
    def load_dotenv(*_args, **_kwargs):
        return None

print('Starting Vibe Writer...')
try:
    load_dotenv()
except Exception:
    pass
subprocess.run([sys.executable, os.path.join('src', 'main.py')])

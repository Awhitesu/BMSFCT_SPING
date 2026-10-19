"""Read the immutable version embedded in a packaged application."""
import json
from pathlib import Path
import sys


def get_version():
    path = (Path(sys._MEIPASS) / 'resources' / 'app_version.json'
            if getattr(sys, 'frozen', False) else Path(__file__).resolve().parents[1] / 'version.json')
    try:
        return str(json.loads(path.read_text(encoding='utf-8'))['app_version'])
    except (OSError, ValueError, KeyError, TypeError):
        return '开发版'


def versioned_title(title):
    version = get_version()
    return f'{title}  |  {version if version == "开发版" else "V" + version}'

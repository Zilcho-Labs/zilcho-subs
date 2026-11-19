import re
from pathlib import Path


def app_version():
    """The one app version, from zilcho_subs/__init__.py (see VERSIONING.md)."""
    text=(Path(__file__).resolve().parents[1]/'zilcho_subs'/'__init__.py').read_text(encoding='utf-8')
    return re.search(r'__version__ = "([^"]+)"',text).group(1)

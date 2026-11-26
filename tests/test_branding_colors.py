"""The app's highlight colour is the teal of its icon; the old purple theme is gone."""
import colorsys
import re
from pathlib import Path

STATIC = Path(__file__).parents[1] / 'zilcho_subs' / 'static'


def test_no_purple_left():
    bad = []
    for name in ('style.css', 'branding.css'):
        for h in re.findall(r'#([0-9a-fA-F]{6})', (STATIC / name).read_text('utf-8')):
            r, g, b = (int(h[i:i + 2], 16) / 255 for i in (0, 2, 4))
            hue, light, sat = colorsys.rgb_to_hls(r, g, b)
            if 240 <= hue * 360 <= 310 and sat > .15:
                bad.append((name, h))
    assert not bad


def _luminance(h):
    channels = (int(h[i:i + 2], 16) / 255 for i in (0, 2, 4))
    r, g, b = (c / 12.92 if c <= .03928 else ((c + .055) / 1.055) ** 2.4 for c in channels)
    return .2126 * r + .7152 * g + .0722 * b


def test_primary_button_text_is_readable():
    css = (STATIC / 'branding.css').read_text('utf-8')
    rule = re.search(r'button\.primary \{([^}]*)\}', css).group(1)
    stops = re.findall(r'#[0-9a-fA-F]{6}', re.search(r'linear-gradient\(([^)]*)\)', rule).group(1))
    assert len(stops) == 2 and 'color: #fff' in rule
    for stop in stops:
        assert 1.05 / (_luminance(stop[1:]) + .05) >= 4.5, stop
    assert '#7378ec' not in rule

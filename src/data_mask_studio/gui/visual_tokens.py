"""Valores visuais compartilhados; não alteram o layout nem a marca oficial."""

from types import MappingProxyType


# As diferenças entre texto de entrada, texto secundário e desabilitado são
# intencionais. A marca oficial continua definida em branding.py.
COLORS = MappingProxyType({
    "window": "#151a22",
    "base": "#10161e",
    "surface": "#1a212b",
    "raised_surface": "#222b37",
    "text": "#e8edf5",
    "input_text": "#edf2f7",
    "muted_text": "#bdc8d5",
    "success_text": "#9bd5b2",
    "disabled_text": "#8592a3",
    "border": "#354253",
    "panel_border": "#303b49",
    "table_grid": "#263240",
    "table_alternate": "#151e28",
    "table_hover": "#1e2c3c",
    "button": "#252e3a",
    "button_hover": "#303b49",
    "button_text": "#e5ebf3",
    "accent": "#347db8",
    "primary": "#27669a",
    "selection": "#315b82",
    "focus": "#79a8d8",
    "input_focus": "#6f9bc8",
    "white": "#ffffff",
    "disabled_surface": "#1a2029",
    "disabled_border": "#303a47",
    "neutral_indicator": "#465569",
    "action_preserve": "#18202a",
    "action_mask": "#1d3449",
    "danger_surface": "#352322",
    "danger_border": "#854842",
    "danger_text": "#ffb4ae",
    "warning_surface": "#342c20",
    "warning_border": "#8b6b35",
    "warning_text": "#f3d29b",
    "navigation_active": "#1d3043",
    "navigation_pressed": "#263c52",
})

# Unidades lógicas do Qt/QSS, mantendo as medidas atuais e o scaling do Qt.
METRICS = MappingProxyType({
    "control_radius": 4,
    "panel_radius": 6,
    "indicator_radius": 3,
    "control_height": 30,
    "input_padding": 7,
    "button_padding": 12,
    "panel_spacing": 10,
    "section_font_size": 16,
    "description_font_size": 13,
    "navigation_marker_width": 3,
    "page_title_font_size": 20,
    "header_text_spacing": 6,
    "header_content_spacing": 18,
    "table_header_padding": 5,
    "dialog_margin": 20,
})

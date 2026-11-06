"""Valores visuais compartilhados; não alteram o layout nem a marca oficial."""

from types import MappingProxyType

from data_mask_studio.gui.theme_palette import DARK_PALETTE

# Compatibilidade para componentes locais ainda não migrados. Não representa
# tema global ativo: os valores históricos continuam Dark. Os RGB migrados
# têm uma única fonte em theme_palette.py; a marca continua em branding.py.
COLORS = MappingProxyType({**{
    name: getattr(DARK_PALETTE, name) for name in (
        "window", "base", "surface", "raised_surface", "text", "input_text",
        "muted_text", "success_text", "disabled_text", "border", "panel_border",
        "table_grid", "table_alternate", "table_hover", "button", "button_hover",
        "button_text", "accent", "primary", "selection", "focus", "input_focus",
        "disabled_surface", "disabled_border", "neutral_indicator", "action_preserve",
        "action_mask", "danger_surface", "danger_border", "danger_text",
        "warning_surface", "warning_border", "warning_text", "navigation_active", "navigation_pressed",
    )
}, "white": DARK_PALETTE.text_on_accent})

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

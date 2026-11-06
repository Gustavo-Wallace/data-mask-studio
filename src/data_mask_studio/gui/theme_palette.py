"""Immutable visual values only: no preferences, Qt, locale or theme resolution."""

from dataclasses import dataclass, fields
import re


@dataclass(frozen=True, slots=True)
class ThemePalette:
    # Surfaces, content and feedback. Equal RGB values do not merge roles.
    window: str
    base: str
    surface: str
    raised_surface: str
    text: str
    input_text: str
    muted_text: str
    success_text: str
    disabled_text: str
    heading_text: str
    readonly_text: str
    header_text: str
    text_on_accent: str
    selection_text: str
    navigation_active_text: str
    hover_text: str
    bright_text: str
    link_text: str
    # Boundaries and interaction states already present in the shared style.
    border: str
    panel_border: str
    interactive_border: str
    focus: str
    input_focus: str
    button: str
    button_hover: str
    button_text: str
    accent: str
    primary: str
    primary_hover: str
    selection: str
    disabled_surface: str
    disabled_border: str
    disabled_selection: str
    neutral_indicator: str
    indicator_border: str
    indicator_disabled_border: str
    indicator_hover_surface: str
    action_preserve: str
    action_mask: str
    danger_surface: str
    danger_border: str
    danger_text: str
    warning_surface: str
    warning_border: str
    warning_text: str
    warning_hover_surface: str
    warning_hover_border: str
    # Sidebar paint is separate from input surfaces/decorative dividers. Dark
    # preserves the actual legacy window-colored sidebar; Light separates it.
    navigation_surface: str
    navigation_border: str
    navigation_active: str
    navigation_pressed: str
    # Data, ancillary surfaces and their distinct states.
    table_grid: str
    table_alternate: str
    table_hover: str
    progress_fill: str
    tab_surface: str
    tab_selected_surface: str
    scrollbar_hover: str
    tooltip_border: str

    def __post_init__(self) -> None:
        # Fixed RGB values are safe substitutions into QSS, not arbitrary CSS.
        if any(not isinstance(value := getattr(self, field.name), str)
               or re.fullmatch(r"#[0-9a-fA-F]{6}", value) is None for field in fields(self)):
            raise ValueError("Invalid semantic palette color")


DARK_PALETTE = ThemePalette(
    window="#151a22", base="#10161e", surface="#1a212b", raised_surface="#222b37",
    text="#e8edf5", input_text="#edf2f7", muted_text="#bdc8d5", success_text="#9bd5b2",
    disabled_text="#8592a3", heading_text="#f4f7fb", readonly_text="#c2ccd8", header_text="#dce4ee",
    text_on_accent="#ffffff", selection_text="#ffffff", navigation_active_text="#ffffff",
    hover_text="#ffffff", bright_text="#ffffff", link_text="#79a8d8",
    border="#354253", panel_border="#303b49", interactive_border="#435064",
    focus="#79a8d8", input_focus="#6f9bc8", button="#252e3a", button_hover="#303b49",
    button_text="#e5ebf3", accent="#347db8", primary="#27669a", primary_hover="#3078b2",
    selection="#315b82", disabled_surface="#1a2029", disabled_border="#303a47",
    disabled_selection="#344252", neutral_indicator="#465569", indicator_border="#6f7f91",
    indicator_disabled_border="#46515f", indicator_hover_surface="#1c2531",
    action_preserve="#18202a", action_mask="#1d3449", danger_surface="#352322",
    danger_border="#854842", danger_text="#ffb4ae", warning_surface="#342c20",
    warning_border="#8b6b35", warning_text="#f3d29b", warning_hover_surface="#433724",
    warning_hover_border="#a68040", navigation_surface="#151a22", navigation_border="#151a22",
    navigation_active="#1d3043", navigation_pressed="#263c52",
    table_grid="#263240", table_alternate="#151e28", table_hover="#1e2c3c",
    progress_fill="#347db8", tab_surface="#1c2430", tab_selected_surface="#2a3d52",
    scrollbar_hover="#5b6d83", tooltip_border="#536176",
)

# Foundation for isolated rendering/tests only. Normal application startup
# continues to use DARK_PALETTE; System/Light activation belongs to later work.
LIGHT_PALETTE = ThemePalette(
    window="#f3f6fa", base="#ffffff", surface="#ffffff", raised_surface="#e8eef5",
    text="#1f2937", input_text="#172331", muted_text="#4b5c70", success_text="#17643b",
    disabled_text="#5c6878", heading_text="#172331", readonly_text="#42566b", header_text="#273e55",
    text_on_accent="#ffffff", selection_text="#ffffff", navigation_active_text="#163d60",
    hover_text="#172331", bright_text="#ffffff", link_text="#185e98",
    border="#77879a", panel_border="#c3cedb", interactive_border="#77879a",
    focus="#5483ad", input_focus="#185e98", button="#e7edf4", button_hover="#dce6f0",
    button_text="#203247", accent="#276fa6", primary="#27669a", primary_hover="#225b8b",
    selection="#103857", disabled_surface="#e9edf2", disabled_border="#c0cad6",
    disabled_selection="#d6dfe8", neutral_indicator="#77879a", indicator_border="#637d95",
    indicator_disabled_border="#a9b5c4", indicator_hover_surface="#e5eef7",
    action_preserve="#eef2f7", action_mask="#dceaf7", danger_surface="#fff0ee",
    danger_border="#b34a41", danger_text="#a52a24", warning_surface="#fff4db",
    warning_border="#9b761f", warning_text="#805500", warning_hover_surface="#f8e6bd",
    warning_hover_border="#856113", navigation_surface="#ffffff", navigation_border="#c3cedb",
    navigation_active="#e0ecf8", navigation_pressed="#d2e2f2",
    table_grid="#d5dee8", table_alternate="#f2f6fb", table_hover="#e5eff9",
    progress_fill="#6495bb", tab_surface="#e9eef5", tab_selected_surface="#d7e5f3",
    scrollbar_hover="#596e86", tooltip_border="#77879a",
)

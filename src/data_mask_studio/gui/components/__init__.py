"""Componentes visuais compartilhados pela interface principal."""

from data_mask_studio.gui.components.empty_state_table import EmptyStateTable
from data_mask_studio.gui.components.navigation import NavigationItem, SidebarNavigation
from data_mask_studio.gui.components.page_shell import PageHeader, PageShell
from data_mask_studio.gui.components.presentation import (
    configure_path_field,
    configure_result_area,
    configure_table,
    set_button_role,
)

__all__ = [
    "EmptyStateTable",
    "NavigationItem",
    "PageHeader",
    "PageShell",
    "SidebarNavigation",
    "configure_path_field",
    "configure_result_area",
    "configure_table",
    "set_button_role",
]

"""Opt-in shared translation data must not share GUI state or mask loader errors."""

from dataclasses import FrozenInstanceError

import pytest

from localization_fixtures import presentation_translators
from qt_lifecycle import qt_widget_lifecycle
from data_mask_studio.i18n import CatalogError, Translator
from data_mask_studio.i18n import catalog
from test_masking_localization import masking_pages
from test_batch_localization import batch_pages


def test_presentation_pool_and_catalogs_are_deeply_read_only(presentation_translators):
    assert set(presentation_translators) == {"pt-BR", "en"}
    for locale, translator in presentation_translators.items():
        assert presentation_translators[locale] is translator
        assert translator.locale == locale
        with pytest.raises(FrozenInstanceError):
            translator.locale = "unsupported"
        with pytest.raises(TypeError):
            translator._catalogs[locale]["common.settings"] = "Modified"
    with pytest.raises(TypeError):
        presentation_translators["en"] = presentation_translators["pt-BR"]


@pytest.mark.parametrize("factory_name", ["masking_pages", "batch_pages"])
@pytest.mark.parametrize("injected", [False, True])
def test_factory_reuse_is_explicit_and_widgets_remain_independent(request, presentation_translators, factory_name, injected):
    create = request.getfixturevalue(factory_name)
    options = {"translator": presentation_translators["en"]} if injected else {}
    first, second = create("en", **options), create("en", **options)
    one, two = first[1], second[1]
    assert one is not two
    assert one._translator.locale == two._translator.locale == "en"
    if injected:
        assert one._translator is two._translator is presentation_translators["en"]
    else:
        assert one._translator is not two._translator
        assert one._translator is not presentation_translators["en"]
    if factory_name == "masking_pages":
        one._output_name_fields[0].setText("Only the first widget")
        assert two._output_name_fields[0].text() == ""
        assert one._inspection_result.path != two._inspection_result.path
    else:
        original = two.output_field.text()
        one.output_field.setText("Only the first widget")
        assert two.output_field.text() == original
        assert one._profile_service.repository.path != two._profile_service.repository.path


def test_fresh_constructor_still_reads_real_catalogs_and_handles_fallback(presentation_translators, monkeypatch):
    def unavailable(*args):
        raise OSError("Synthetic unavailable catalog")

    with monkeypatch.context() as guard:
        guard.setattr(catalog.resources, "files", unavailable)
        # An injected immutable object can render without reloading its data.
        assert presentation_translators["en"].text("common.settings") == "Settings"
        # The real constructor is not patched or globally cached.
        with pytest.raises(CatalogError):
            Translator("en")
    fresh = Translator("unsupported")
    assert fresh.locale == "en" and fresh.text("common.settings") == "Settings"
    assert fresh is not presentation_translators["en"]

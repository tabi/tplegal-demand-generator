"""
tplegal-demand-generator — Generator wezwań do zapłaty + kalkulator rekompensat.
"""

from importlib.metadata import PackageNotFoundError, version as _metadata_version
from pathlib import Path

# Wersja czytana z metadanych zainstalowanego pakietu, nie duplikowana w kodzie —
# jedynym źródłem prawdy zostaje pyproject.toml. Służy do ustalenia, którą wersję
# kodu ma dana sesja: pakiet instaluje się z gałęzi main, więc bez tego numeru
# sesja przed i po zmianie logiki jest nieodróżnialna inaczej niż po wyniku
# liczbowym.
try:
    __version__ = _metadata_version("tplegal-demand-generator")
except PackageNotFoundError:  # import z katalogu repo bez instalacji
    __version__ = "0.0.0+brak-instalacji"

DEFAULT_TEMPLATE = Path(__file__).parent / "templates" / "wezwanie_template.docx"

from demand_generator.generator import (  # noqa: E402, F401
    fill_template_from_dict,
    kwota_slownie,
    format_pln,
    art_10_reference,
    main,
)

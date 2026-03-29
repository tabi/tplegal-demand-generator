"""
tplegal-demand-generator — Generator wezwań do zapłaty + kalkulator rekompensat.
"""

from pathlib import Path

DEFAULT_TEMPLATE = Path(__file__).parent / "templates" / "wezwanie_template.docx"

from demand_generator.generator import (  # noqa: E402, F401
    fill_template_from_dict,
    kwota_slownie,
    format_pln,
    art_10_reference,
    main,
)

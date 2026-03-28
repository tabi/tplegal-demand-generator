#!/usr/bin/env python3
"""
Unit testy dla demand_generator.py — generator wezwań do zapłaty.
Pokrywa: fill_template_from_dict, walidacja pól, kwota_slownie, helpers.

Uruchomienie:
    python -m pytest tests/test_demand.py -v
"""

import json
import zipfile
import tempfile
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from demand_generator import (
    fill_template_from_dict,
    kwota_slownie,
    format_pln,
    art_10_reference,
    _format_date,
    _join_address,
    _join_zip_city,
    _xml_escape,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def mock_template(tmp_path):
    """Tworzy minimalny mock template .docx z placeholderami."""
    template_path = tmp_path / "template.docx"
    doc_xml = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
<w:body>
<w:p><w:r><w:t>{{DATA}}</w:t></w:r></w:p>
<w:p><w:r><w:t>{{WIERZYCIEL_NAZWA}}</w:t></w:r></w:p>
<w:p><w:r><w:t>{{WIERZYCIEL_ADRES}}</w:t></w:r></w:p>
<w:p><w:r><w:t>{{RADCA_IMIE_NAZWISKO}}</w:t></w:r></w:p>
<w:p><w:r><w:t>{{DLUZNIK_NAZWA}}</w:t></w:r></w:p>
<w:p><w:r><w:t>{{DLUZNIK_ADRES_ULICA}}</w:t></w:r></w:p>
<w:p><w:r><w:t>{{DLUZNIK_ADRES_MIASTO}}</w:t></w:r></w:p>
<w:p><w:r><w:t>{{KWOTA_LACZNIE_PLN}}</w:t></w:r></w:p>
<w:p><w:r><w:t>{{KWOTA_REKOMPENSATY_PLN}}</w:t></w:r></w:p>
<w:p><w:r><w:t>{{KWOTA_ODSETKI_PLN}}</w:t></w:r></w:p>
<w:p><w:r><w:t>{{LISTA_FAKTUR}}</w:t></w:r></w:p>
<w:p><w:r><w:t>{{NUMER_RACHUNKU}}</w:t></w:r></w:p>
<w:p><w:r><w:t>{{TERMIN_DNI}}</w:t></w:r></w:p>
</w:body>
</w:document>"""

    with zipfile.ZipFile(template_path, "w") as zf:
        zf.writestr("word/document.xml", doc_xml.encode("utf-8"))
        zf.writestr("[Content_Types].xml", '<?xml version="1.0"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"></Types>')

    return template_path


@pytest.fixture
def sample_data():
    """Pełny zestaw danych do fill_template_from_dict."""
    return {
        "creditor_name": "FIRMA ABC SP. Z O.O.",
        "cr_street": "ul. Skarbowa 2/5",
        "cr_city": "Leszno",
        "cr_zip": "64-100",
        "cr_bank": "PL 12 3456 7890 1234 5678 9012 3456",
        "debtor_name": "DŁUŻNIK XYZ S.A.",
        "d_street": "ul. Poznańska 10",
        "d_city": "Poznań",
        "d_zip": "60-001",
        "assigned_to": "Bartłomieja Przyniczkę",
        "total_compensation_pln": 1234.56,
        "total_interest_pln": 567.89,
        "invoice_numbers": ["FV/2024/001", "FV/2024/002"],
        "invoice_tiers": {"EUR_40", "EUR_70"},
    }


# ---------------------------------------------------------------------------
# fill_template_from_dict
# ---------------------------------------------------------------------------

class TestFillTemplateFromDict:
    def test_generates_docx(self, mock_template, sample_data, tmp_path):
        """Generuje poprawny plik .docx."""
        output = tmp_path / "output.docx"
        result = fill_template_from_dict(mock_template, output, sample_data)
        assert result == output
        assert output.exists()
        # Sprawdź że to poprawny ZIP
        with zipfile.ZipFile(output, "r") as zf:
            assert "word/document.xml" in zf.namelist()

    def test_placeholders_replaced(self, mock_template, sample_data, tmp_path):
        """Placeholdery są zamienione na dane."""
        output = tmp_path / "output.docx"
        fill_template_from_dict(mock_template, output, sample_data)

        with zipfile.ZipFile(output, "r") as zf:
            content = zf.read("word/document.xml").decode("utf-8")

        # Nie powinno być żadnych placeholderów
        assert "{{" not in content
        assert "}}" not in content
        # Powinny być dane
        assert "Firma Abc SP. z ograniczoną odpowiedzialnością" in content or "Firma Abc SP." in content
        assert "FV/2024/001" in content

    def test_missing_required_field_raises(self, mock_template, tmp_path):
        """Brak wymaganego pola -> ValueError."""
        data = {"debtor_name": "Test", "total_compensation_pln": 100}
        output = tmp_path / "output.docx"
        with pytest.raises(ValueError, match="creditor_name"):
            fill_template_from_dict(mock_template, output, data)

    def test_missing_multiple_fields_raises(self, mock_template, tmp_path):
        """Brak wielu pól -> ValueError z listą."""
        data = {}
        output = tmp_path / "output.docx"
        with pytest.raises(ValueError, match="Missing required fields"):
            fill_template_from_dict(mock_template, output, data)

    def test_unknown_strategy_raises(self, mock_template, sample_data, tmp_path):
        """Nieznana strategia -> ValueError."""
        output = tmp_path / "output.docx"
        with pytest.raises(ValueError, match="Unknown strategy"):
            fill_template_from_dict(mock_template, output, sample_data, strategy="nonexistent")

    def test_template_not_found_raises(self, sample_data, tmp_path):
        """Nieistniejący template -> FileNotFoundError."""
        output = tmp_path / "output.docx"
        with pytest.raises(FileNotFoundError, match="Template not found"):
            fill_template_from_dict(Path("/nonexistent/template.docx"), output, sample_data)

    def test_missing_bank_account_placeholder(self, mock_template, sample_data, tmp_path):
        """Brak bank_account -> placeholder '___'."""
        del sample_data["cr_bank"]
        output = tmp_path / "output.docx"
        fill_template_from_dict(mock_template, output, sample_data)

        with zipfile.ZipFile(output, "r") as zf:
            content = zf.read("word/document.xml").decode("utf-8")
        assert "___" in content

    def test_all_strategies(self, mock_template, sample_data, tmp_path):
        """Wszystkie strategie generują plik bez błędu."""
        for strategy in ["soft_collect", "standard_collect", "hard_collect", "pre_litigation"]:
            output = tmp_path / f"output_{strategy}.docx"
            fill_template_from_dict(mock_template, output, sample_data, strategy=strategy)
            assert output.exists()


# ---------------------------------------------------------------------------
# kwota_slownie
# ---------------------------------------------------------------------------

class TestKwotaSlownie:
    def test_zero(self):
        assert kwota_slownie(0) == "zero złotych 00/100"

    def test_one(self):
        assert kwota_slownie(1) == "jeden złoty 00/100"

    def test_two(self):
        assert kwota_slownie(2) == "dwa złote 00/100"

    def test_five(self):
        assert kwota_slownie(5) == "pięć złotych 00/100"

    def test_twelve(self):
        result = kwota_slownie(12)
        assert result == "dwanaście złotych 00/100"

    def test_thousand(self):
        result = kwota_slownie(1000)
        assert result == "tysiąc złotych 00/100"

    def test_complex_amount(self):
        result = kwota_slownie(157720.00)
        assert "sto pięćdziesiąt siedem tysięcy siedemset dwadzieścia złotych 00/100" == result

    def test_with_grosze(self):
        result = kwota_slownie(1234.56)
        assert result.endswith("56/100")
        assert "tysiąc" in result

    def test_decimal_input(self):
        result = kwota_slownie(Decimal("99.99"))
        assert "dziewięćdziesiąt dziewięć" in result
        assert "99/100" in result


# ---------------------------------------------------------------------------
# format_pln
# ---------------------------------------------------------------------------

class TestFormatPln:
    def test_basic(self):
        assert format_pln(1234.56) == "1\u00a0234,56"

    def test_zero(self):
        assert format_pln(0) == "0,00"

    def test_large_amount(self):
        assert format_pln(1000000) == "1\u00a0000\u00a0000,00"


# ---------------------------------------------------------------------------
# art_10_reference
# ---------------------------------------------------------------------------

class TestArt10Reference:
    def test_single_tier(self):
        assert art_10_reference({"EUR_40"}) == "10 ust. 1 pkt 1"

    def test_two_tiers(self):
        assert art_10_reference({"EUR_40", "EUR_100"}) == "10 ust. 1 pkt 1 i 3"

    def test_all_tiers(self):
        assert art_10_reference({"EUR_40", "EUR_70", "EUR_100"}) == "10 ust. 1 pkt 1, 2 i 3"

    def test_empty(self):
        assert art_10_reference(set()) == "10 ust. 1"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

class TestHelpers:
    def test_format_date(self):
        result = _format_date(date(2026, 3, 28))
        assert result == "28 marca 2026 r."

    def test_join_address_full(self):
        assert _join_address("ul. Skarbowa 2", "64-100", "Leszno") == "ul. Skarbowa 2, 64-100 Leszno"

    def test_join_address_missing(self):
        assert _join_address(None, None, None) == "___"

    def test_join_zip_city_full(self):
        assert _join_zip_city("64-100", "Leszno") == "64-100 Leszno"

    def test_join_zip_city_missing(self):
        assert _join_zip_city(None, None) == "___"

    def test_xml_escape(self):
        assert _xml_escape('a & b < c > d "e" \'f\'') == "a &amp; b &lt; c &gt; d &quot;e&quot; &apos;f&apos;"

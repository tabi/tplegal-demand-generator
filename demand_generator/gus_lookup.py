"""
GUS REGON lookup po NIP — dla generatora wezwań.

Publiczny skrypt woła GUS BIR1.1 (SOAP) dla NIP wierzyciela i dłużnika,
wyciąga nazwę i adres. Retry: 3 próby w odstępach 5s. Przy porażce
zwraca None — caller decyduje o fallbacku.

Zależności: zeep (SOAP client), requests.
"""

import logging
import re
import time
import xml.etree.ElementTree as ET

from zeep import Client, Settings
from zeep.transports import Transport
from requests import Session

logger = logging.getLogger(__name__)

PROD_WSDL = "https://wyszukiwarkaregon.stat.gov.pl/wsBIR/wsdl/UslugaBIRzewnPubl-ver11-prod.wsdl"
PROD_ENDPOINT = "https://wyszukiwarkaregon.stat.gov.pl/wsBIR/UslugaBIRzewnPubl.svc"
PROD_KEY = "fb80f5f627d041de8fe5"

CONNECT_TIMEOUT = 10
READ_TIMEOUT = 30
MAX_ATTEMPTS = 3
RETRY_DELAY_SECONDS = 5

_ZIP_CITY_SUFFIX = re.compile(r",\s*\d{2}-\d{3}\s+.+$")


class GUSLookupError(Exception):
    """Podnoszony gdy GUS nie odpowiada po wszystkich retry."""


def _parse_xml_response(xml_string: str) -> list[dict]:
    """Parse XML response from BIR1 into list of dicts (one per <dane>)."""
    if not xml_string or xml_string.strip() == "":
        return []
    try:
        root = ET.fromstring(xml_string)
    except ET.ParseError:
        try:
            root = ET.fromstring(f"<root>{xml_string}</root>")
        except ET.ParseError:
            return []

    results = []
    for dane in root.iter("dane"):
        record = {}
        for child in dane:
            text = child.text.strip() if child.text else ""
            record[child.tag] = text
        if record:
            results.append(record)
    return results


def _format_gus_address(gus: dict) -> tuple[str, str, str]:
    """Zwraca (street, city, zip) sformatowany jak w tplegal-tools.

    Bare-village fallback: gdy brak Ulica, komponujemy street_part
    z Miejscowosc + NrNieruchomosci.
    """
    street = gus.get("Ulica", "")
    nr = gus.get("NrNieruchomosci", "")
    lokal = gus.get("NrLokalu", "")
    zip_code = gus.get("KodPocztowy", "")
    city = gus.get("Miejscowosc", "")

    if street:
        prefix = "" if street.lower().startswith("ul.") else "ul. "
        street_part = f"{prefix}{street} {nr}".strip()
        if lokal:
            street_part += f"/{lokal}"
    elif nr and city:
        street_part = f"{city} {nr}"
        if lokal:
            street_part += f"/{lokal}"
    else:
        street_part = ""

    street_part = _ZIP_CITY_SUFFIX.sub("", street_part).strip()
    return street_part, city, zip_code


class GUSClient:
    """Minimalny klient GUS BIR1.1 — login, search po NIP, logout."""

    def __init__(self, api_key: str = PROD_KEY):
        self.api_key = api_key
        self.session_id: str | None = None

        session = Session()
        session.headers.update({
            "Content-Type": "application/soap+xml; charset=utf-8",
        })
        transport = Transport(session=session, timeout=(CONNECT_TIMEOUT, READ_TIMEOUT))
        settings = Settings(strict=False, xml_huge_tree=True)

        self.client = Client(wsdl=PROD_WSDL, transport=transport, settings=settings)
        self.service = self.client.create_service(
            "{http://tempuri.org/}e3", PROD_ENDPOINT,
        )

    def login(self) -> None:
        self.session_id = self.service.Zaloguj(self.api_key)
        if not self.session_id:
            raise GUSLookupError("Logowanie do GUS BIR1 nieudane (pusty SID).")

    def logout(self) -> None:
        if self.session_id:
            try:
                self.service.Wyloguj(self.session_id)
            except Exception:
                pass
            self.session_id = None

    def search_nip(self, nip: str) -> dict | None:
        """Wyszukaj podmiot po NIP. Zwraca dict (pierwszy wynik) lub None gdy brak."""
        nip_clean = str(nip).replace("-", "").strip()
        with self.client.settings(extra_http_headers={"sid": self.session_id}):
            raw = self.service.DaneSzukajPodmioty(
                pParametryWyszukiwania={"Nip": nip_clean},
            )
        if not raw:
            return None
        parsed = _parse_xml_response(raw)
        return parsed[0] if parsed else None


def lookup_by_nip(nip: str) -> dict | None:
    """
    Wyszukaj podmiot w GUS po NIP z retry 3×5s.

    Zwraca dict {name, street, city, zip} lub None gdy NIP nie znaleziony.
    Rzuca GUSLookupError gdy GUS niedostępny po 3 próbach.
    """
    nip_clean = str(nip or "").replace("-", "").replace(" ", "").strip()
    if not nip_clean:
        return None

    last_exc: Exception | None = None
    for attempt in range(1, MAX_ATTEMPTS + 1):
        client = None
        try:
            client = GUSClient()
            client.login()
            entity = client.search_nip(nip_clean)
            if entity is None:
                logger.info("GUS: NIP %s nie znaleziony w REGON", nip_clean)
                return None
            street, city, zip_code = _format_gus_address(entity)
            return {
                "name": entity.get("Nazwa", "").strip(),
                "street": street,
                "city": city,
                "zip": zip_code,
            }
        except Exception as e:
            last_exc = e
            logger.warning(
                "GUS lookup NIP=%s próba %d/%d nieudana: %s",
                nip_clean, attempt, MAX_ATTEMPTS, e,
            )
            if attempt < MAX_ATTEMPTS:
                time.sleep(RETRY_DELAY_SECONDS)
        finally:
            if client is not None:
                client.logout()

    raise GUSLookupError(
        f"GUS niedostępny po {MAX_ATTEMPTS} próbach dla NIP {nip_clean}: {last_exc}"
    )

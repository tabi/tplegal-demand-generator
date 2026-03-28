# -*- coding: utf-8 -*-
"""
Warianty tonalne wezwania do zapłaty — Rekompensa.pl
Używane przez generator wezwań do podmianki akapitów w template.

Każdy wariant definiuje:
- deadline_days: termin zapłaty
- threat_paragraph: akapit z konsekwencjami braku zapłaty
- closing_paragraph: akapit zamykający
"""

DEMAND_VARIANTS = {

    "soft_collect": {
        "deadline_days": 7,
        "threat_paragraph": (
            "Uprzejmie informuję, że powyższa kwota stanowi ustawowe roszczenie "
            "wierzyciela wynikające wprost z przepisów prawa i przysługuje niezależnie "
            "od tego, czy wierzyciel poniósł jakiekolwiek koszty odzyskiwania należności. "
            "Roszczenie to powstaje z mocy samego prawa z chwilą, gdy wierzyciel nabył prawo "
            "do naliczania odsetek za opóźnienie w transakcji handlowej."
        ),
        "closing_paragraph": (
            "Wyrażam nadzieję na polubowne uregulowanie tej kwestii "
            "i pozostaję do dyspozycji w razie jakichkolwiek pytań."
        ),
    },

    "standard_collect": {
        "deadline_days": 7,
        "threat_paragraph": (
            "Wskazuję jednocześnie, że brak reakcji w zakreślonym terminie "
            "będzie skutkował zaniechaniem pozasądowych prób dochodzenia należności "
            "i niezwłocznym wystąpieniem na drogę sądową. Spowoduje to powstanie "
            "dodatkowych wysokich kosztów procesu, których poniesienia można uniknąć, "
            "stosując się do żądania zawartego w tym wezwaniu."
        ),
        "closing_paragraph": (
            "Liczę na szybkie i ostateczne zakończenie sprawy."
        ),
    },

    "hard_collect": {
        "deadline_days": 7,
        "threat_paragraph": (
            "Informuję, że bezskuteczny upływ wyznaczonego terminu spowoduje "
            "niezwłoczne złożenie pozwu do właściwego sądu gospodarczego, "
            "bez dalszych wezwań. Konsekwencją będzie obciążenie dłużnika "
            "kosztami procesu, w tym kosztami zastępstwa procesowego, opłatą sądową "
            "oraz odsetkami ustawowymi za opóźnienie w transakcjach handlowych "
            "naliczanymi od dnia wymagalności każdej z faktur do dnia zapłaty."
        ),
        "closing_paragraph": (
            "Niniejsze wezwanie stanowi ostateczną próbę polubownego zakończenia sprawy."
        ),
    },

    "pre_litigation": {
        "deadline_days": 5,
        "threat_paragraph": (
            "Niniejsze wezwanie ma charakter ostatecznego wezwania przedsądowego "
            "w rozumieniu art. 187 § 1 pkt 3 k.p.c. Informuję, że w przypadku "
            "bezskutecznego upływu wyznaczonego terminu, pozew zostanie złożony "
            "niezwłocznie, nie później niż w terminie 7 dni od upływu terminu zapłaty. "
            "Skutkiem będzie obciążenie dłużnika pełnymi kosztami procesu, "
            "w tym kosztami zastępstwa procesowego oraz odsetkami ustawowymi "
            "za opóźnienie w transakcjach handlowych od dnia wymagalności "
            "każdej z faktur do dnia zapłaty."
        ),
        "closing_paragraph": (
            "Niniejsze pismo stanowi ostateczne wezwanie do zapłaty."
        ),
    },

}

# Tekst oryginału w template (do wyszukania i podmiany w XML)
TEMPLATE_THREAT_ORIGINAL = (
    "Wskazuję jednocześnie, że brak reakcji w zakreślonym terminie "
    "będzie skutkował zaniechaniem pozasądowych prób dochodzenia należności "
    "i niezwłocznym wystąpieniem na drogę sądową. Spowoduje to powstanie "
    "dodatkowych wysokich kosztów procesu, których poniesienia można uniknąć, "
    "stosując się do żądania zawartego w tym wezwaniu."
)

TEMPLATE_CLOSING_ORIGINAL = (
    "Liczę na szybkie i ostateczne zakończenie sprawy."
)

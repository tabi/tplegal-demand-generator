"""Wspólna konfiguracja testów.

CI nie ma dostępu do zewnętrznych API (NBP, GUS BIR1) i testy nie mają prawa od
nich zależeć. Ten autouse fixture blokuje realne połączenia sieciowe, więc test,
który zapomni zamockować wywołanie, pada z jasnym komunikatem — zamiast
przechodzić lub nie w zależności od tego, czy runner ma internet.
"""

import socket

import pytest


class NetworkAccessInTestError(RuntimeError):
    """Test próbował otworzyć połączenie sieciowe."""


@pytest.fixture(autouse=True)
def _block_network(monkeypatch):
    def _blocked(*args, **kwargs):
        raise NetworkAccessInTestError(
            "Test próbował otworzyć połączenie sieciowe. Zamockuj wywołanie — "
            "np. @patch('demand_generator.calc.get_nbp_eur_rate') dla kursu EUR "
            "albo @patch('requests.get') dla surowego HTTP. CI nie ma dostępu "
            "do API NBP ani GUS BIR1."
        )

    monkeypatch.setattr(socket.socket, "connect", _blocked)
    monkeypatch.setattr(socket.socket, "connect_ex", _blocked)
    monkeypatch.setattr(socket, "create_connection", _blocked)

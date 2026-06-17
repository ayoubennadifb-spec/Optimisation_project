"""Configuration pytest : rend le package ``src`` importable et fournit des
fixtures partagées (données chargées une fois, solution baseline résolue une fois).
"""
import sys
from pathlib import Path

import pytest

# Rendre la racine du projet importable (pour `import src.*`) quel que soit le cwd.
ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src import config            # noqa: E402
from src.data_loader import load_data  # noqa: E402
from src.solve import run         # noqa: E402


@pytest.fixture(scope="session")
def data():
    return load_data()


@pytest.fixture(scope="session")
def baseline(data):
    """Solution baseline (avec règle galva, stocks PF) — résolue une seule fois."""
    return run(data, config.BASELINE)

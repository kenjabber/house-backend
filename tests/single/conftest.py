import pytest

from tests.apps import single


@pytest.fixture
def house_app():
    return single.app


@pytest.fixture
def house_auth():
    return single.auth


@pytest.fixture
def house_base():
    return single.Base

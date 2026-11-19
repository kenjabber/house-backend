import pytest

from tests.apps import multi


@pytest.fixture
def house_app():
    return multi.app


@pytest.fixture
def house_auth():
    return multi.auth


@pytest.fixture
def house_base():
    return multi.Base


@pytest.fixture
def house_user_values(session):
    return {"team_id": multi.first_team(session).id}

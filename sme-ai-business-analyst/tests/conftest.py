import pytest
from app.core.config import settings


@pytest.fixture(autouse=True)
def default_test_settings():
    orig_require = settings.require_invite_code
    orig_mode = settings.access_mode
    settings.require_invite_code = False
    settings.access_mode = "open"
    yield
    settings.require_invite_code = orig_require
    settings.access_mode = orig_mode


@pytest.fixture
def sample_sale_message() -> str:
    return "Sold 5 bags of rice for 250000"


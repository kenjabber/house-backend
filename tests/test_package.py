import re

import house_backend


def test_version_is_semantic():
    assert re.fullmatch(r"\d+\.\d+\.\d+", house_backend.__version__)

"""The MCP parity contract, as an app's own test_mcp_parity.py writes it."""

from house_backend.testing import (
    check_human_only_refuses_tokens,
    check_operations_partition,
    check_tools_match_catalog,
)
from tests.apps import multi


def test_every_rest_operation_is_classified():
    check_operations_partition(multi.app, multi.CATALOG)


def test_registered_tools_match_the_catalog(mcp):
    check_tools_match_catalog(mcp, multi.CATALOG)


def test_human_only_operations_refuse_an_api_token(client, api_token):
    check_human_only_refuses_tokens(client, api_token, multi.CATALOG)

from house_backend.mcp.catalog import Catalog


def test_exposed_operations_tool_names_and_enforced_default():
    catalog = Catalog(
        {"read": (("GET", "/api/a"), ("GET", "/api/b")), "describe": (), "write": (("PUT", "/api/a"),)},
        {("GET", "/api/c"): "read"},
        {("POST", "/api/auth/login/begin"): "people", ("GET", "/api/auth/tokens"): "people"},
    )
    assert catalog.exposed_operations() == {("GET", "/api/a"), ("GET", "/api/b"), ("PUT", "/api/a")}
    assert catalog.tool_names() == ["read", "describe", "write"]
    assert catalog.human_only_enforced == {("GET", "/api/auth/tokens")}

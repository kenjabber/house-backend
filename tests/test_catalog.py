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


def test_a_human_only_operation_a_tool_names_is_not_exposed():
    catalog = Catalog(
        {"delete_record": (("DELETE", "/api/things/{id}"), ("DELETE", "/api/servers/{id}"))},
        {},
        {("DELETE", "/api/servers/{id}"): "a person decommissions a server"},
    )
    assert catalog.exposed_operations() == {("DELETE", "/api/things/{id}")}
    assert catalog.human_only_enforced == {("DELETE", "/api/servers/{id}")}

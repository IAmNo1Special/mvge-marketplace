"""Unit tests for OKF concept file and frontmatter parsing."""

from __future__ import annotations

from pathlib import Path

from mvge_runes_okf_bridge.parser import (
    _resolve_concept_link,
    extract_frontmatter_and_body,
    parse_concept_file,
)


def test_extract_frontmatter_valid() -> None:
    text = "---\ntype: Service\ntitle: Order API\n---\n# Schema\nDetails here."
    data, body, err = extract_frontmatter_and_body(text)
    assert err is None
    assert isinstance(data, dict)
    assert data["type"] == "Service"
    assert data["title"] == "Order API"
    assert body.strip() == "# Schema\nDetails here."


def test_extract_frontmatter_missing_delimiter() -> None:
    text = "type: Service\n# Schema"
    data, _body, err = extract_frontmatter_and_body(text)
    assert data is None
    assert "must start with '---'" in (err or "")


def test_extract_frontmatter_unclosed() -> None:
    text = "---\ntype: Service\n# Schema"
    data, _body, err = extract_frontmatter_and_body(text)
    assert data is None
    assert "missing closing '---'" in (err or "")


def test_extract_frontmatter_invalid_yaml() -> None:
    text = "---\n: : bad yaml\n---\nBody"
    data, _body, err = extract_frontmatter_and_body(text)
    assert data is None
    assert "YAML parsing error" in (err or "")


def test_extract_frontmatter_non_mapping() -> None:
    text = "---\n- item1\n- item2\n---\nBody"
    data, _body, err = extract_frontmatter_and_body(text)
    assert data is None
    assert "must be a YAML mapping" in (err or "")


def test_extract_frontmatter_empty() -> None:
    text = "---\n---\nBody"
    data, _body, err = extract_frontmatter_and_body(text)
    assert err is None
    assert data == {}


def test_parse_concept_file_success(tmp_path: Path) -> None:
    bundle_root = tmp_path / ".okf"
    bundle_root.mkdir()
    services_dir = bundle_root / "services"
    services_dir.mkdir()

    concept_path = services_dir / "order-api.md"
    concept_path.write_text(
        "---\n"
        "type: Service\n"
        "title: Order Service\n"
        "description: Manages customer orders.\n"
        "resource: https://api.example.com/orders\n"
        "tags: [sales, api]\n"
        "status: stable\n"
        "stale_after: '2028-12-31'\n"
        "generated:\n"
        "  by: human:alice\n"
        "  at: '2026-09-01'\n"
        "verified:\n"
        "  - by: human:bob\n"
        "    at: '2026-09-02'\n"
        "sources:\n"
        "  - id: openapi-spec\n"
        "    resource: https://api.example.com/openapi.json\n"
        "    title: OpenAPI Spec\n"
        "    usage_count: 42\n"
        "usage_window:\n"
        "  from: '2026-01-01'\n"
        "  to: '2026-09-01'\n"
        "---\n\n"
        "# Orders\n\n"
        "See also [Customers](/services/customer-api.md)[^openapi-spec].\n",
        encoding="utf-8",
    )

    concept, err = parse_concept_file(concept_path, bundle_root)
    assert err is None
    assert concept is not None
    assert concept.id == "services/order-api"
    assert concept.type == "Service"
    assert concept.title == "Order Service"
    assert concept.tags == ["sales", "api"]
    assert concept.stale_after == "2028-12-31"
    assert concept.trust_tier.value == "human-reviewed"
    assert len(concept.sources) == 1
    assert concept.sources[0].id == "openapi-spec"
    assert concept.sources[0].usage_count == 42
    assert concept.usage_window is not None
    assert concept.usage_window.from_date == "2026-01-01"
    assert "services/customer-api" in concept.links
    assert "openapi-spec" in concept.footnotes


def test_parse_concept_missing_type(tmp_path: Path) -> None:
    bundle_root = tmp_path / ".okf"
    bundle_root.mkdir()
    f = bundle_root / "invalid.md"
    f.write_text("---\ntitle: Missing Type\n---\nBody", encoding="utf-8")

    concept, err = parse_concept_file(f, bundle_root)
    assert concept is None
    assert "Missing or empty required field 'type'" in (err or "")


def test_parse_concept_attested_computation(tmp_path: Path) -> None:
    bundle_root = tmp_path / ".okf"
    bundle_root.mkdir()
    f = bundle_root / "revenue.md"
    f.write_text(
        "---\n"
        "type: Attested Computation\n"
        "title: Recognized Revenue\n"
        "runtime: bigquery\n"
        "parameters:\n"
        "  year: { type: integer, required: true }\n"
        "executor:\n"
        "  resource: queries/revenue.sql\n"
        "attester:\n"
        "  resource: references/attesters/verify_rev.py\n"
        "---\n\n"
        "# Computation\n"
        "```sql\nSELECT sum(amount) FROM orders\n```\n",
        encoding="utf-8",
    )

    concept, err = parse_concept_file(f, bundle_root)
    assert err is None
    assert concept is not None
    assert concept.computation is not None
    assert concept.computation.runtime == "bigquery"
    assert "year" in concept.computation.parameters


def test_resolve_concept_link(tmp_path: Path) -> None:
    bundle_root = tmp_path / ".okf"
    bundle_root.mkdir()
    sub = bundle_root / "nested"
    sub.mkdir()

    # External URLs or anchors should return None
    assert _resolve_concept_link("https://example.com", sub, bundle_root) is None
    assert _resolve_concept_link("#anchor", sub, bundle_root) is None
    assert _resolve_concept_link("mailto:test@example.com", sub, bundle_root) is None

    # Absolute bundle-relative path
    res = _resolve_concept_link("/tables/orders.md", sub, bundle_root)
    assert res == "tables/orders"

    # Relative path
    res2 = _resolve_concept_link("../users.md", sub, bundle_root)
    assert res2 == "users"

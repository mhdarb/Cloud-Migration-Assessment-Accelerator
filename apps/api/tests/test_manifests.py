from app.services.manifest_parsers import parse_manifest_file


def test_package_json_runtime_and_postgres():
    text = """
    {
      "name": "orders-api",
      "engines": {"node": "20"},
      "dependencies": {
        "express": "4.18.0",
        "pg": "8.11.0"
      }
    }
    """
    result = parse_manifest_file("package.json", text)
    attrs = {(c.entity_key, c.attribute, c.value) for c in result.claims}
    assert ("orders-api", "runtime", "node") in attrs
    assert ("orders-api", "framework", "express") in attrs
    assert ("postgres", "name", "PostgreSQL") in attrs
    assert any(
        d.target_key == "postgres" and d.relationship == "uses"
        for d in result.dependencies
    )


def test_requirements_python_framework():
    text = "fastapi==0.115.0\npsycopg2-binary==2.9.9\n"
    result = parse_manifest_file("requirements.txt", text)
    frameworks = [c.value for c in result.claims if c.attribute == "framework"]
    assert "fastapi" in frameworks
    assert any(c.entity_key == "postgres" for c in result.claims)

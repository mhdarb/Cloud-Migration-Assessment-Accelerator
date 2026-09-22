from __future__ import annotations

from pathlib import Path

from app.schemas.api import ExtractionResult
from app.services.manifest_parsers import config, docker, dotnet, java, nodejs, python_pkg


def parse_manifest_file(relative_path: str, text: str) -> ExtractionResult:
    name = Path(relative_path).name.lower()
    if name == "package.json":
        return nodejs.parse_package_json(text, relative_path)
    if name == "pom.xml":
        return java.parse_pom(text, relative_path)
    if name == "requirements.txt":
        return python_pkg.parse_requirements_txt(text, relative_path)
    if name == "pyproject.toml":
        return python_pkg.parse_pyproject(text, relative_path)
    if name.startswith("dockerfile") or Path(relative_path).name.startswith("Dockerfile"):
        return docker.parse_dockerfile(text, relative_path)
    if "docker-compose" in name:
        return docker.parse_compose(text, relative_path)
    if name.endswith(".csproj"):
        return dotnet.parse_csproj(text, relative_path)
    if name.startswith("appsettings") and name.endswith(".json"):
        return dotnet.parse_appsettings(text, relative_path)
    if name == ".env.example":
        return config.parse_env_example(text, relative_path)
    if name.endswith((".yml", ".yaml")):
        return config.parse_yaml_lite(text, relative_path)
    if name in {"build.gradle", "build.gradle.kts"}:
        return java.parse_gradle(text, relative_path)
    return ExtractionResult()

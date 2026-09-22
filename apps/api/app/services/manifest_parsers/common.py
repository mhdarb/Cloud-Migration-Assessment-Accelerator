from __future__ import annotations

import re

from app.schemas.api import ExtractedClaim, ExtractedDependency

INFRA_DEPS = {
    "pg": ("database", "postgres", "PostgreSQL"),
    "postgres": ("database", "postgres", "PostgreSQL"),
    "postgresql": ("database", "postgres", "PostgreSQL"),
    "mysql": ("database", "mysql", "MySQL"),
    "mysql2": ("database", "mysql", "MySQL"),
    "mongodb": ("database", "mongodb", "MongoDB"),
    "mongoose": ("database", "mongodb", "MongoDB"),
    "redis": ("database", "redis", "Redis"),
    "ioredis": ("database", "redis", "Redis"),
    "kafkajs": ("interface", "kafka", "Kafka"),
    "kafka": ("interface", "kafka", "Kafka"),
    "amqplib": ("interface", "rabbitmq", "RabbitMQ"),
    "aws-sdk": ("interface", "aws", "AWS"),
    "@aws-sdk/client-s3": ("interface", "s3", "Amazon S3"),
    "boto3": ("interface", "aws", "AWS"),
    "psycopg2": ("database", "postgres", "PostgreSQL"),
    "psycopg2-binary": ("database", "postgres", "PostgreSQL"),
    "asyncpg": ("database", "postgres", "PostgreSQL"),
    "sqlalchemy": ("database", "sql-datastore", "SQL datastore"),
    "spring-boot-starter-data-jpa": ("database", "jpa-datastore", "JPA datastore"),
    "spring-kafka": ("interface", "kafka", "Kafka"),
    "stackexchange.redis": ("database", "redis", "Redis"),
    "npgsql": ("database", "postgres", "PostgreSQL"),
}

MANIFEST_NAMES = {
    "package.json",
    "package-lock.json",
    "pom.xml",
    "requirements.txt",
    "pyproject.toml",
    "dockerfile",
    ".env.example",
    "appsettings.json",
    "appsettings.development.json",
    "appsettings.production.json",
}

MANIFEST_GLOBS = (
    "Dockerfile*",
    "docker-compose*.yml",
    "docker-compose*.yaml",
    "*.csproj",
    "build.gradle",
    "build.gradle.kts",
)

SKIP_EXT = {
    ".png", ".jpg", ".jpeg", ".gif", ".ico",
    ".woff", ".woff2", ".ttf", ".eot",
    ".mp4", ".pdf", ".jar", ".dll", ".so", ".exe", ".class",
}
MAX_FILE_BYTES = 512_000


def norm_app(name: str) -> str:
    key = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    return key or "snapshot-app"


def quote(path: str, snippet: str) -> str:
    snip = " ".join(snippet.split())[:180]
    return f"[{path}] {snip}"


def infra_from_dep(dep_name: str, app_key: str, path: str) -> list[ExtractedDependency | ExtractedClaim]:
    base = dep_name.lower().split("@")[0].strip()
    hit = INFRA_DEPS.get(base)
    if not hit:
        for key, val in INFRA_DEPS.items():
            if key in base:
                hit = val
                break
    if not hit:
        return []
    etype, ekey, label = hit
    return [
        ExtractedClaim(
            entity_type=etype,
            entity_key=ekey,
            attribute="name",
            value=label,
            confidence=0.85,
            evidence_quote=quote(path, f"dependency {dep_name}"),
            chunk_ids=[],
        ),
        ExtractedDependency(
            source_type="application",
            source_key=app_key,
            target_type=etype,
            target_key=ekey,
            relationship="uses",
            confidence=0.85,
            evidence_quote=quote(path, f"{app_key} uses {dep_name}"),
            chunk_ids=[],
        ),
    ]

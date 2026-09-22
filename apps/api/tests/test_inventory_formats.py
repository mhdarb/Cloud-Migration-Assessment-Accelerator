import json

from app.models.entities import DocumentType
from app.services.parsers import parse_file
from app.services.storage import ALLOWED_SUFFIXES


def test_csv_inventory_is_accepted_and_normalized(tmp_path):
    path = tmp_path / "servers.csv"
    path.write_text(
        "Server,vCPU,Memory GB,CPU Utilization %\napp-01,4,16,52\n",
        encoding="utf-8",
    )
    result = parse_file(str(path), "cmdb-inventory.csv")
    assert ".csv" in ALLOWED_SUFFIXES
    assert result.doc_type == DocumentType.inventory
    assert "Server | vCPU | Memory GB" in result.pages[0].text


def test_json_inventory_is_accepted_and_normalized(tmp_path):
    path = tmp_path / "inventory.json"
    path.write_text(
        json.dumps(
            {
                "servers": [
                    {"Server": "app-01", "vCPU": 4, "Memory GB": 16},
                    {"Server": "app-02", "vCPU": 8, "Memory GB": 32},
                ]
            }
        ),
        encoding="utf-8",
    )
    result = parse_file(str(path), "inventory.json")
    assert ".json" in ALLOWED_SUFFIXES
    assert result.doc_type == DocumentType.inventory
    assert "app-02 | 8 | 32" in result.pages[0].text

from app.services.text_format import number, plain_text


def test_markdown_becomes_plain_text_with_structure_kept():
    md = (
        "## Summary\n\nThe estate has **4 servers** and *2 blockers*.\n\n"
        "1. Re-platform `billing-db`\n2. See [runbook](http://x)\n\n"
        "| Server | SKU |\n|---|---|\n| app-01 | D4s |\n\n---\nDone."
    )
    assert plain_text(md) == (
        "Summary\n\nThe estate has 4 servers and 2 blockers.\n\n"
        "• Re-platform billing-db\n• See runbook\n\nServer · SKU\napp-01 · D4s\n\nDone."
    )


def test_plain_prose_passes_through_untouched():
    text = "billing_api_v2 runs 2*3 workers at 99.9% availability - see note_1."
    assert plain_text(text) == text


def test_empty_and_numbers():
    assert plain_text(None) == "" and plain_text("") == ""
    assert (number(500.0), number(2.5), number(3)) == ("500", "2.5", "3")

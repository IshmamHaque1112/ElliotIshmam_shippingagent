import json

import main


def test_queue_no_llm_prints_header_and_ranked_rows(capsys):
    assert main.main(["queue", "--no-llm"]) == 0
    out = capsys.readouterr().out
    assert out.startswith("Active exceptions: 9 | Revenue exposed:")
    assert "Closed late cases: 1" in out
    assert out.index("SH-1004") < out.index("SH-1008") < out.index("SH-1003")


def test_queue_json_has_no_internal_fields(capsys):
    main.main(["queue", "--no-llm", "--json"])
    data = json.loads(capsys.readouterr().out)
    assert data["queue"][0]["shipment_id"] == "SH-1004"
    assert not any(k.startswith("_") for k in data["queue"][0])

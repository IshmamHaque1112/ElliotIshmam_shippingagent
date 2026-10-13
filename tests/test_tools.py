import tools


def dataset():
    return tools.load_dataset()


def test_three_tools_return_the_expected_sections():
    ds = dataset()
    assert tools.get_tracking_status(ds, "SH-1004")["carrier"]["name"] == "Northline Freight"
    assert tools.order_context(ds, "SH-1004")["order_id"] == "ORD-5004"
    assert "events" in tools.event_timeline(ds, "SH-1004")


def test_unknown_id_returns_an_error_object_and_never_raises():
    for name in tools.TOOL_NAMES:
        assert tools.TOOLS[name](dataset(), "SH-9999")["error"] == "not_found"


def test_lookup_ignores_case_and_whitespace():
    assert tools.order_context(dataset(), "  sh-1004 ")["order_id"] == "ORD-5004"


def test_validator_accepts_every_dev_shipment_that_has_data():
    ds = dataset()
    for sh in ds["shipments"]:
        sid = sh["shipment_id"]
        problems = tools.validate_bundle(
            tools.get_tracking_status(ds, sid), tools.order_context(ds, sid), tools.event_timeline(ds, sid), ds["facilities"]
        )
        assert problems == [], (sid, problems)


def test_validator_flags_missing_and_broken_data():
    ds = dataset()
    sid = "SH-1004"
    tracking, order, timeline = (f(ds, sid) for f in (tools.get_tracking_status, tools.order_context, tools.event_timeline))
    assert tools.validate_bundle(None, order, timeline, ds["facilities"])
    assert tools.validate_bundle(tracking, dict(order, order_value_usd="lots"), timeline, ds["facilities"])
    bad = dict(timeline, events=[dict(timeline["events"][0], timestamp="not-a-date")])
    assert tools.validate_bundle(tracking, order, bad, ds["facilities"])

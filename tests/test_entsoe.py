from datetime import datetime, timezone
from pathlib import Path

from tick.entsoe import hourly, parse_document

FIX = Path(__file__).parent / "fixtures"
UTC = timezone.utc

A03_GAPPY = """<?xml version="1.0" encoding="UTF-8"?>
<GL_MarketDocument xmlns="urn:iec62325.351:tc57wg16:451-6:generationloaddocument:3:0">
  <revisionNumber>7</revisionNumber>
  <TimeSeries>
    <businessType>A93</businessType>
    <inBiddingZone_Domain.mRID codingScheme="A01">10Y1001A1001A82H</inBiddingZone_Domain.mRID>
    <curveType>A03</curveType>
    <MktPSRType><psrType>B19</psrType></MktPSRType>
    <Period>
      <timeInterval><start>2026-03-28T23:00Z</start><end>2026-03-29T01:00Z</end></timeInterval>
      <resolution>PT15M</resolution>
      <Point><position>1</position><quantity>100</quantity></Point>
      <Point><position>5</position><quantity>200</quantity></Point>
      <Point><position>8</position><quantity>300</quantity></Point>
    </Period>
  </TimeSeries>
</GL_MarketDocument>"""

ACK = """<?xml version="1.0" encoding="UTF-8"?>
<Acknowledgement_MarketDocument xmlns="urn:iec62325.351:tc57wg16:451-1:acknowledgementdocument:8:1">
  <Reason><code>999</code><text>No matching data found</text></Reason>
</Acknowledgement_MarketDocument>"""


def test_a03_forward_fills_missing_positions():
    doc = parse_document(A03_GAPPY)
    assert doc.revision == 7
    s = doc.series[0]
    assert s.psr_type == "B19"
    assert len(s.points) == 8  # two hours of quarter hours, all filled
    vals = [s.points[k] for k in sorted(s.points)]
    assert vals == [100, 100, 100, 100, 200, 200, 200, 300]


def test_hourly_means_and_coverage():
    s = parse_document(A03_GAPPY).series[0]
    h = hourly(s.points, s.resolution_minutes)
    t0 = datetime(2026, 3, 28, 23, tzinfo=UTC)
    t1 = datetime(2026, 3, 29, 0, tzinfo=UTC)
    assert h[t0] == {"v": 100.0, "c": 1.0}
    assert h[t1]["v"] == (200 + 200 + 200 + 300) / 4


def test_hour_with_too_little_coverage_is_dropped():
    pts = {datetime(2026, 1, 1, 10, 0, tzinfo=UTC): 1.0}  # one quarter of an hour
    assert hourly(pts, 15) == {}
    pts[datetime(2026, 1, 1, 10, 15, tzinfo=UTC)] = 3.0
    h = hourly(pts, 15)
    assert h[datetime(2026, 1, 1, 10, tzinfo=UTC)] == {"v": 2.0, "c": 0.5}


def test_acknowledgement_is_empty_not_error():
    doc = parse_document(ACK)
    assert doc.series == []


def test_real_day_ahead_document_has_three_types_and_full_day():
    doc = parse_document((FIX / "a69_de_lu_day_ahead.xml").read_text(encoding="utf-8"))
    types = {s.psr_type for s in doc.series}
    assert types == {"B16", "B18", "B19"}
    for s in doc.series:
        assert len(hourly(s.points, s.resolution_minutes)) == 24


def test_real_actuals_keep_real_gaps():
    doc = parse_document((FIX / "a75_de_lu_wind_onshore.xml").read_text(encoding="utf-8"))
    s = doc.series[0]
    h = hourly(s.points, s.resolution_minutes)
    # The sample has two quarter hours missing at period boundaries; the
    # hours are kept with coverage 0.75, and nothing was invented.
    assert any(e["c"] == 0.75 for e in h.values())
    assert 22 <= len(h) <= 24


def test_prices_pick_lowest_classification_sequence():
    doc = parse_document((FIX / "a44_de_lu_prices.xml").read_text(encoding="utf-8"))
    cls = sorted(s.classification for s in doc.series)
    assert cls == [1, 2]
    first = next(s for s in doc.series if s.classification == 1)
    assert len(hourly(first.points, first.resolution_minutes)) == 24

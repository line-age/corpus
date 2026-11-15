from datetime import date

from corpus_core.classify import Kind, classify
from corpus_core.lanes import LaneStatus, LicenseLane, license_lanes

PGJ = "pub_pipeline-gas-journal"


def test_issue_index_and_other_titles():
    assert (
        classify(
            PGJ,
            "sim_pipeline-gas-journal_1938-08_149_2",
            "American Gas Journal 1938-08: Vol 149 Iss 2",
        )
        == Kind.ISSUE
    )
    assert (
        classify(
            PGJ,
            "sim_pipeline-gas-journal_1859-08-01_1_2",
            "The American Gas-Light Journal 1859-08-01: Vol 1 Iss 2",
        )
        == Kind.ISSUE
    )
    assert (
        classify(
            PGJ,
            "sim_pipeline-gas-journal_1867-08-16_9_4",
            "Mining and Petroleum Standard and American Gas-Light Journal 1867-08-16: Vol 9 Iss 4",
        )
        == Kind.ISSUE
    )
    assert (
        classify(
            PGJ,
            "sim_pipeline-gas-journal_1922_116_index",
            "American Gas Journal 1922: Vol 116 Index",
        )
        == Kind.INDEX
    )
    assert (
        classify(
            PGJ, "sim_pipeline-gas-journal_1956-02_46", "American Gas Merchandising 1956-02: Iss 46"
        )
        == Kind.OTHER
    )


def test_world_oil_keeps_its_earlier_name():
    assert (
        classify("pub_world-oil", "sim_world-oil_1935-01-03", "The Oil Weekly 1935-01-03")
        == Kind.ISSUE
    )


def test_lanes_by_year():
    early = license_lanes(PGJ, date(1859, 8, 1))
    assert (early.license_lane, early.lane_status) == (
        LicenseLane.PUBLIC_DOMAIN,
        LaneStatus.CONFIRMED,
    )
    checked = license_lanes(PGJ, date(1938, 8, 1))
    assert (checked.license_lane, checked.lane_status) == (
        LicenseLane.PUBLIC_DOMAIN,
        LaneStatus.TO_VERIFY,
    )
    assert "onlinebooks" in checked.license_basis
    unchecked = license_lanes("pub_oil-gas-journal", date(1938, 8, 1))
    assert unchecked.license_lane == LicenseLane.UNKNOWN
    late = license_lanes(PGJ, date(1965, 1, 1))
    assert late.license_lane == LicenseLane.UNKNOWN
    assert early.retrieval_lane == "ia-mirror"

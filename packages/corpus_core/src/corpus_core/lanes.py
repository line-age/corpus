"""License and retrieval lanes, recorded at ingest (controlplane I-0006).

The licence reasoning is recorded as text beside the lane so it can be audited and revised.
This is not legal advice; the dataset licence is the research partner's call.
"""

from dataclasses import dataclass
from datetime import date
from enum import StrEnum


class LicenseLane(StrEnum):
    PUBLIC_DOMAIN = "public-domain"
    UNKNOWN = "unknown"


class LaneStatus(StrEnum):
    CONFIRMED = "confirmed"
    TO_VERIFY = "to-verify"


RETRIEVAL_IA_MIRROR = "ia-mirror"

# Titles whose US issue renewals were checked in the CCE/CPRS records and none found.
# Source: Online Books Page serial records. Add a title only after checking its own page.
RENEWALS_CHECKED_NONE_FOUND: dict[str, str] = {
    "pub_pipeline-gas-journal": "https://onlinebooks.library.upenn.edu/webbin/cinfo/americangasjnl",
}

PD_CUTOFF_YEAR = 1929  # published before this year: US term expired
RENEWAL_ERA_END_YEAR = 1963  # US periodicals before 1964 needed renewal after 28 years


@dataclass(frozen=True)
class Lanes:
    license_lane: LicenseLane
    lane_status: LaneStatus
    license_basis: str
    retrieval_lane: str = RETRIEVAL_IA_MIRROR


def license_lanes(collection: str, published: date) -> Lanes:
    y = published.year
    if y < PD_CUTOFF_YEAR:
        return Lanes(
            LicenseLane.PUBLIC_DOMAIN,
            LaneStatus.CONFIRMED,
            f"US publication of {y}, before {PD_CUTOFF_YEAR}: copyright term expired",
        )
    if y <= RENEWAL_ERA_END_YEAR:
        source = RENEWALS_CHECKED_NONE_FOUND.get(collection)
        if source:
            return Lanes(
                LicenseLane.PUBLIC_DOMAIN,
                LaneStatus.TO_VERIFY,
                f"US periodical of {y}; no issue renewals found in CCE/CPRS ({source});"
                " contributions may differ",
            )
        return Lanes(
            LicenseLane.UNKNOWN,
            LaneStatus.TO_VERIFY,
            f"US periodical of {y}; renewal records not yet checked for this title",
        )
    return Lanes(
        LicenseLane.UNKNOWN,
        LaneStatus.TO_VERIFY,
        f"published {y}, after {RENEWAL_ERA_END_YEAR}: renewal automatic; the research"
        " partner's licence call",
    )

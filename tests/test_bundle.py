"""Tests for the layout of the bundled metadata parquet."""

import importlib.resources
import itertools

import pyarrow.parquet as pq
import pytest

from wkls import data


@pytest.fixture(scope="module")
def bundle_metadata() -> pq.FileMetaData:
    # A file object, not a path: pyarrow's LocalFileSystem can be unusable
    # once sedonadb has loaded (see wkls._compat).
    with (importlib.resources.files(data) / "overture.zstd18.parquet").open("rb") as fh:
        return pq.read_metadata(fh)


def test_bundle_is_split_into_small_row_groups(bundle_metadata):
    sizes = [
        bundle_metadata.row_group(i).num_rows
        for i in range(bundle_metadata.num_row_groups)
    ]
    assert len(sizes) > 1
    assert max(sizes) <= 65_536


def test_row_groups_follow_the_country_sort(bundle_metadata):
    country = bundle_metadata.schema.names.index("country")
    bounds = [
        (stats.min, stats.max)
        for stats in (
            bundle_metadata.row_group(i).column(country).statistics
            for i in range(bundle_metadata.num_row_groups)
        )
    ]
    for (_, prev_max), (next_min, _) in itertools.pairwise(bounds):
        assert prev_max <= next_min

import beacontower
import beacontower.metadata  # noqa: F401


def test_beacontower_stays_a_namespace_package():
    # Other distributions (the public beacontower package among them) install below
    # beacontower too; an __init__.py in src/beacontower/ would hide their modules.
    assert beacontower.__file__ is None

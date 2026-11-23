import os

import setuptools


# PACKAGE_VERSION is set by a release build. Without it -- e.g. a direct
# `pip install git+...#subdirectory=<package>` -- fall back to a local marker: when
# installing from a git ref, the ref is the real identity, not the version string.
def get_version():
    return os.environ.get("PACKAGE_VERSION") or "0.0.0+local"


setuptools.setup(version=get_version())

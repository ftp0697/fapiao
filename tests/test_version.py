import re
from importlib.metadata import PackageNotFoundError, version as package_version

import pytest

from fapiao_pdf import __version__

_PEP440_CORE = re.compile(r"^\d+\.\d+\.\d+([.+\-].+)?$")


def test_version_is_pep440_core() -> None:
    assert isinstance(__version__, str) and __version__
    assert _PEP440_CORE.match(__version__), f"invalid version: {__version__!r}"


def test_version_matches_installed_metadata_when_available() -> None:
    try:
        installed = package_version("fapiao")
    except PackageNotFoundError:
        pytest.skip("fapiao 未以 editable/wheel 安装；跳过元数据一致性校验")
    assert installed == __version__

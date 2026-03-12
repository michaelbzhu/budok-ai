from yomi_daemon import __version__


def test_package_import_smoke() -> None:
    assert __version__ == "0.0.1"

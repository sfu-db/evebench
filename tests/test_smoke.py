from evebench import hello


def test_hello() -> None:
    assert hello() == "hello from evebench"

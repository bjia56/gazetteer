from sample.core import Engine, run


def test_run() -> None:
    assert run(1) == 2


def test_engine_step() -> None:
    assert Engine().step(1) == 2

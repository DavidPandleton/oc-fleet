from kata import add, clamp


def test_add():
    assert add(2, 3) == 5


def test_clamp_within_range():
    assert clamp(5, 0, 10) == 5


def test_clamp_below():
    assert clamp(-1, 0, 10) == 0


def test_clamp_above():
    assert clamp(99, 0, 10) == 10

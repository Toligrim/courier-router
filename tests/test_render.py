from courier_router.render import _world

def test_webmercator_finite():
    x,y = _world(59.93,30.31,10)
    assert x > 0 and y > 0

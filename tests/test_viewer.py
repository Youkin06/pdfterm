from see.app import Viewer
from see.term import Mouse


def burst(viewer, clock, button, start, count=30, step=0.004):
    """A trackpad swipe: a quick run of wheel events."""
    for i in range(count):
        clock[0] = start + i * step
        viewer.on_mouse(Mouse(button, 20, 20, True, 200.0, 400.0))


def make_viewer(path, term):
    v = Viewer(path, term)
    v.measure()
    v.apply_mode()
    clock = [0.0]
    v.clock = lambda: clock[0]
    return v, clock


def test_one_swipe_turns_one_page(pdf3, fake_term):
    v, clock = make_viewer(pdf3, fake_term)
    burst(v, clock, 67, 1.0)  # right-to-left swipe: next page
    assert v.page == 1
    burst(v, clock, 67, 2.0)
    assert v.page == 2
    burst(v, clock, 66, 3.0)  # left-to-right: back
    assert v.page == 1


def test_vertical_drift_does_not_turn_a_sideways_swipe(pdf3, fake_term):
    v, clock = make_viewer(pdf3, fake_term)
    for i in range(30):
        clock[0] = 1.0 + i * 0.004
        button = 64 if i % 5 == 0 else 67  # mostly right, a little up
        v.on_mouse(Mouse(button, 20, 20, True, 200.0, 400.0))
    assert v.page == 1


def test_double_click_zooms_and_returns(pdf3, fake_term):
    v, _ = make_viewer(pdf3, fake_term)
    fit = v.zoom
    v.toggle_zoom(100, 100)
    assert v.zoom > fit * 1.5
    v.toggle_zoom(100, 100)
    assert abs(v.zoom - fit) < 1e-9


def test_tiles_stay_within_the_graphics_budget(pdf3, fake_term):
    from see.app import TILE_MAX_PX

    v, _ = make_viewer(pdf3, fake_term)
    for _ in range(30):
        v.zoom_at(1.3)
    tz = v.tile_zoom()
    x, y, w, h = v.wanted_tile(tz)
    assert w * h <= TILE_MAX_PX

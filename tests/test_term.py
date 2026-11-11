from see.term import DeviceAttributes, GraphicsReply, Key, ModeReport, Mouse, WindowPixels, parse


def test_keys_and_arrows():
    events, rest = parse("q\x1b[C\x1b[6~\r\x7f")
    assert rest == ""
    assert [e.name for e in events] == ["char", "right", "pagedown", "enter", "backspace"]
    assert events[0] == Key("char", "q")


def test_alt_keys_from_command_mappings():
    events, _ = parse("\x1bc\x1bz\x1ba")
    assert [(e.name, e.char) for e in events] == [("alt", "c"), ("alt", "z"), ("alt", "a")]


def test_sgr_mouse_with_modifiers():
    events, _ = parse("\x1b[<80;10;5M\x1b[<32;4;6M\x1b[<0;4;6m")
    wheel, drag, release = events
    assert isinstance(wheel, Mouse) and wheel.button == 64 and wheel.ctrl and wheel.pressed
    assert drag.motion and drag.button == 0
    assert not release.pressed


def test_terminal_replies():
    events, _ = parse("\x1b[?1016;1$y\x1b[4;800;1200t\x1b_Gi=31;OK\x1b\\\x1b[?62;22c")
    assert events == [ModeReport(1016, 1), WindowPixels(800, 1200), GraphicsReply(True), DeviceAttributes()]
    assert parse("\x1b_Gi=31;ENOTSUPPORTED\x1b\\")[0] == [GraphicsReply(False)]


def test_incomplete_sequences_wait_for_more_input():
    assert parse("\x1b[<64;1") == ([], "\x1b[<64;1")
    assert parse("\x1b_Gi=31;O") == ([], "\x1b_Gi=31;O")


def test_outer_terminal_hint_only_inside_herdr_without_a_graphics_terminal():
    from see.term import outer_terminal_hint

    assert outer_terminal_hint({"TERM_PROGRAM": "Apple_Terminal"}) is None
    assert outer_terminal_hint({"TERM_PROGRAM": "herdr", "__CFBundleIdentifier": "com.apple.Terminal"})
    assert outer_terminal_hint({"TERM_PROGRAM": "herdr", "GHOSTTY_RESOURCES_DIR": "/x"}) is None
    assert outer_terminal_hint({"HERDR_ENV": "1", "__CFBundleIdentifier": "net.kovidgoyal.kitty"}) is None

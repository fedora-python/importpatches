from pathlib import Path

import pytest

import importpatches as ip


def msg(summary, body=''):
    return summary + ('\n' + body if body else '')


# --- detect_import_mode ---

def test_detect_mode_all_numbered():
    log = ['c1', 'c2']
    messages = {'c1': msg('00001: First'), 'c2': msg('00002: Second')}
    assert ip.detect_import_mode(messages, log) == ip.Mode.NUMBERED


def test_detect_mode_all_numberless():
    log = ['c1', 'c2']
    messages = {'c1': msg('Fix something'), 'c2': msg('Fix something else')}
    assert ip.detect_import_mode(messages, log) == ip.Mode.NUMBERLESS


def test_detect_mode_dirty_style_counts_as_numbered():
    log = ['c1', 'c2']
    messages = {
        'c1': msg('00001: First'),
        'c2': msg('python-2.6-rpath.patch', '# 00016 #\nBody'),
    }
    assert ip.detect_import_mode(messages, log) == ip.Mode.NUMBERED


def test_detect_mode_filename_shaped_numberless_summary_stays_numberless():
    # a numberless commit summary that happens to be filename-safe and end
    # in '.patch', but carries no resolvable dirty-style number, must not
    # be confused with a genuine "old and dirty" commit
    log = ['c1']
    messages = {'c1': msg('fix-thing.patch', 'Body with no big number')}
    assert ip.detect_import_mode(messages, log) == ip.Mode.NUMBERLESS


def test_detect_mode_mixture_raises():
    log = ['c1', 'c2']
    messages = {'c1': msg('00001: First'), 'c2': msg('Fix something else')}
    with pytest.raises(ip.ModeDetectionError):
        ip.detect_import_mode(messages, log)


def test_detect_mode_no_commits_defaults_numbered():
    assert ip.detect_import_mode({}, []) == ip.Mode.NUMBERED


# --- determine_patch_number_and_filename ---

def test_numbered_existing_patch_keeps_filename(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / '00003-old-name.patch').touch()
    number, path = ip.determine_patch_number_and_filename(
        'deadbeef', '00003: Fix the thing', '00003: Fix the thing', ip.Mode.NUMBERED,
    )
    assert number == 3
    assert path == Path('00003-old-name.patch')


def test_numbered_new_patch_slugifies(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    number, path = ip.determine_patch_number_and_filename(
        'deadbeef', '00003: Fix the thing', '00003: Fix the thing', ip.Mode.NUMBERED,
    )
    assert number == 3
    # slugify() operates on the whole summary, so the number prefix ends up
    # baked into the filename here (pre-existing behavior, unchanged).
    assert path == Path('00003-fix-the-thing.patch')


def test_numberless_always_slugifies_ignoring_lookalike_file(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / '00003-fix-the-thing.patch').touch()  # decoy, must be ignored
    number, path = ip.determine_patch_number_and_filename(
        'deadbeef', 'Fix the thing', 'Fix the thing', ip.Mode.NUMBERLESS,
    )
    assert number is None
    assert path == Path('fix-the-thing.patch')


def test_numberless_filename_shaped_summary_does_not_crash(monkeypatch):
    # mirrors test_detect_mode_filename_shaped_numberless_summary_stays_numberless:
    # once mode is NUMBERLESS, a '.patch'-ending filename-safe summary must
    # not be routed into the dirty-style number lookup and crash
    number, path = ip.determine_patch_number_and_filename(
        'deadbeef', 'fix-thing.patch', 'fix-thing.patch\n\nBody with no big number',
        ip.Mode.NUMBERLESS,
    )
    assert number is None
    assert path == Path('fix-thing-patch.patch')


def test_numberless_never_calls_glob(monkeypatch):
    def boom(*a, **k):
        raise AssertionError('glob must not be called in numberless mode')
    monkeypatch.setattr(Path, 'glob', boom)
    number, path = ip.determine_patch_number_and_filename(
        'deadbeef', 'Fix the thing', 'Fix the thing', ip.Mode.NUMBERLESS,
    )
    assert number is None
    assert path == Path('fix-the-thing.patch')

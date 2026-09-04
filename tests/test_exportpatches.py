import pytest

import exportpatches as ep


def P(number, value):
    return ep.SpecPatch(number=number, value=value)


# --- PATCH_LINE_RE ---

def test_patch_line_re_numbered():
    m = ep.PATCH_LINE_RE.match('Patch00042: 00042-foo.patch')
    assert m.group('number') == '00042'
    assert m.group('value') == '00042-foo.patch'


def test_patch_line_re_bare():
    m = ep.PATCH_LINE_RE.match('Patch: foo.patch')
    assert m.group('number') is None
    assert m.group('value') == 'foo.patch'


def test_patch_line_re_rhel8_compat():
    m = ep.PATCH_LINE_RE.match('Patch1: foo.patch')
    assert m.group('number') == '1'


# --- detect_export_mode ---

def test_detect_export_mode_numbered_by_filename_prefix():
    patches = [P(1, '00001-foo.patch'), P(2, '00002-bar.patch')]
    assert ep.detect_export_mode(patches) == ep.Mode.NUMBERED


def test_detect_export_mode_numbered_with_trailing_legacy_unnumbered_entry():
    # legacy: manually added trailing patch, no filename/commit number yet
    patches = [P(1, '00001-foo.patch'), P(2, 'bar.patch')]
    assert ep.detect_export_mode(patches) == ep.Mode.NUMBERED


def test_detect_export_mode_only_url_value_defaults_numbered():
    # no local patches to detect anything from; arbitrary default, matches
    # the "no patches at all" case
    patches = [P(42, 'https://bugzilla.redhat.com/attachment.cgi?id=999999')]
    assert ep.detect_export_mode(patches) == ep.Mode.NUMBERED


def test_detect_export_mode_url_value_does_not_affect_numberless_detection():
    # a temporary, hand-added URL-based patch (e.g. testing an upstream fix
    # before it's imported properly) must not flip detection away from
    # what the other, local patches indicate
    patches = [
        P(None, 'foo.patch'),
        P(5, 'https://bugzilla.redhat.com/attachment.cgi?id=999999'),
    ]
    assert ep.detect_export_mode(patches) == ep.Mode.NUMBERLESS


def test_detect_export_mode_url_value_does_not_force_numberless():
    # same, but the other local patches are legacy-numbered
    patches = [
        P(1, '00001-foo.patch'),
        P(5, 'https://bugzilla.redhat.com/attachment.cgi?id=999999'),
    ]
    assert ep.detect_export_mode(patches) == ep.Mode.NUMBERED


def test_detect_export_mode_numberless_bare():
    patches = [P(None, 'foo.patch'), P(None, 'bar.patch')]
    assert ep.detect_export_mode(patches) == ep.Mode.NUMBERLESS


def test_detect_export_mode_numberless_rhel8_compat_sequence():
    patches = [P(1, 'foo.patch'), P(2, 'bar.patch'), P(3, 'baz.patch')]
    assert ep.detect_export_mode(patches) == ep.Mode.NUMBERLESS


def test_detect_export_mode_numberless_rhel8_compat_out_of_order():
    # RHEL 8 compat numbers have no semantic meaning, so reordering the
    # spec lines without renumbering must still be recognized as numberless
    patches = [P(2, 'bar.patch'), P(1, 'foo.patch'), P(3, 'baz.patch')]
    assert ep.detect_export_mode(patches) == ep.Mode.NUMBERLESS


def test_detect_export_mode_ambiguous_mixed_none_and_number_raises():
    patches = [P(None, 'foo.patch'), P(1, 'bar.patch')]
    with pytest.raises(ep.ExportModeDetectionError):
        ep.detect_export_mode(patches)


def test_detect_export_mode_numberless_rhel8_compat_with_gaps():
    # a patch may have been manually removed, leaving a gap; RHEL 8 compat
    # numbers have no semantic meaning so this must still be numberless
    patches = [P(1, 'foo.patch'), P(3, 'bar.patch')]
    assert ep.detect_export_mode(patches) == ep.Mode.NUMBERLESS


def test_detect_export_mode_no_patches_defaults_numbered():
    assert ep.detect_export_mode([]) == ep.Mode.NUMBERED


# --- find_duplicate_patch_numbers ---

def test_find_duplicate_patch_numbers_none_duplicated():
    patches = [P(1, 'foo.patch'), P(2, 'bar.patch')]
    assert ep.find_duplicate_patch_numbers(patches) == set()


def test_find_duplicate_patch_numbers_detects_duplicate():
    patches = [P(1, 'foo.patch'), P(1, 'bar.patch'), P(2, 'baz.patch')]
    assert ep.find_duplicate_patch_numbers(patches) == {1}


def test_find_duplicate_patch_numbers_ignores_bare_entries():
    patches = [P(None, 'foo.patch'), P(None, 'bar.patch')]
    assert ep.find_duplicate_patch_numbers(patches) == set()

"""
End-to-end integration tests: run importpatches.py/exportpatches.py as real
subprocesses against scratch upstream/dist-git repos built under tmp_path.
"""

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
IMPORTPATCHES = REPO_ROOT / 'importpatches.py'
EXPORTPATCHES = REPO_ROOT / 'exportpatches.py'

SPEC_FILENAME = 'python1.0.spec'
UPSTREAM_TAG = 'v1.0.0'
FEDORA_BRANCH = 'fedora-1.0'

SPEC_TEMPLATE = """\
%global upstream_version 1.0.0
Name:           python1.0
Version:        1.0.0
Release:        %autorelease
Summary:        ...

License:        MIT

Source0:        ...

# (Patches taken from github.com/fedora-python/cpython)
{existing}# (New patches go here ^^^)

%description
...

%prep
%autosetup -S git_am

%changelog
%autochangelog
"""


@pytest.fixture(scope='session')
def env():
    e = os.environ.copy()
    e.update(
        GIT_AUTHOR_NAME='Test', GIT_AUTHOR_EMAIL='t@example.com',
        GIT_COMMITTER_NAME='Test', GIT_COMMITTER_EMAIL='t@example.com',
        LANG='C.utf-8',
    )
    return e


def git(args, cwd, env):
    return subprocess.run(
        ['git', *args], cwd=cwd, env=env,
        check=True, capture_output=True, text=True,
    )


@pytest.fixture(scope='session')
def upstream_template(tmp_path_factory, env):
    """Build the pristine scratch upstream repo once per test session."""
    path = tmp_path_factory.mktemp('upstream-template')
    git(['init', '-q'], path, env)
    (path / 'main.py').write_text("print('hello')\n")
    git(['add', 'main.py'], path, env)
    git(['commit', '-q', '-m', 'Initial upstream commit'], path, env)
    git(['tag', UPSTREAM_TAG], path, env)
    git(['branch', FEDORA_BRANCH, UPSTREAM_TAG], path, env)
    return path


@pytest.fixture(scope='session')
def distgit_template(tmp_path_factory, env):
    """Build the pristine scratch dist-git repo once per test session.

    Deliberately does not set importpatches.upstream here: every test's
    `distgit` fixture copies this template and points the config at its own
    freshly-copied `upstream`, so setting it here would just be overwritten.
    """
    path = tmp_path_factory.mktemp('distgit-template')
    write_spec(path, existing='')
    git(['init', '-q'], path, env)
    git(['add', '-A'], path, env)
    git(['commit', '-q', '-m', 'init'], path, env)
    return path


@pytest.fixture
def upstream(tmp_path, upstream_template):
    """A scratch fedora-python/cpython-like repo, tagged v1.0.0, with an
    (empty) fedora-1.0 branch ready for patches."""
    path = tmp_path / 'upstream'
    shutil.copytree(upstream_template, path)
    return path


@pytest.fixture
def distgit(tmp_path, distgit_template, upstream, env):
    """A scratch dist-git checkout, pointing importpatches.upstream at
    `upstream`, with an empty patches section."""
    path = tmp_path / 'distgit'
    shutil.copytree(distgit_template, path)
    git(['config', 'importpatches.upstream', str(upstream)], path, env)
    return path


def write_spec(distgit, existing):
    (distgit / SPEC_FILENAME).write_text(SPEC_TEMPLATE.format(existing=existing))


def commit_spec(distgit, env, message='update patches'):
    git(['add', '-A'], distgit, env)
    git(['commit', '-q', '-m', message], distgit, env)


def add_commits(upstream, env, branch, messages):
    """Append one commit per message onto `branch`, each editing main.py."""
    git(['switch', '-q', branch], upstream, env)
    main_py = upstream / 'main.py'
    for i, message in enumerate(messages):
        with main_py.open('a') as f:
            f.write(f'line{i}\n')
        git(['commit', '-q', '-am', message], upstream, env)


def build_patch_files(upstream, distgit, env, entries):
    """entries: list of (commit message, target filename in distgit).

    Creates one commit per entry (each appending a line to main.py) on a
    throwaway 'scratch' branch off v1.0.0, and writes each commit's
    `git format-patch` output into distgit/filename, ready for `git am`.
    """
    git(['switch', '-q', '-c', 'scratch', UPSTREAM_TAG], upstream, env)
    main_py = upstream / 'main.py'
    for i, (message, filename) in enumerate(entries):
        with main_py.open('a') as f:
            f.write(f'line{i}\n')
        git(['commit', '-q', '-am', message], upstream, env)
        commit = git(['rev-parse', 'HEAD'], upstream, env).stdout.strip()
        patch_text = subprocess.run(
            ['git', 'format-patch', '--stdout', '-1', '--minimal', '--patience',
             '--zero-commit', '--no-signature', '--keep-subject', commit],
            cwd=upstream, env=env, check=True, capture_output=True, text=True,
        ).stdout
        (distgit / filename).write_text(patch_text)


def run_importpatches(distgit, upstream, env, *extra_args):
    args = [
        '--repo', str(upstream),
        '--base', UPSTREAM_TAG,
        '--head', FEDORA_BRANCH,
        '--python-version', '1.0',
        *extra_args,
        str(distgit / SPEC_FILENAME),
    ]
    return subprocess.run(
        [sys.executable, str(IMPORTPATCHES), *args],
        cwd=distgit, env=env, capture_output=True, text=True,
        stdin=subprocess.DEVNULL,
    )


def run_exportpatches(distgit, upstream, env, *extra_args):
    args = [
        '--repo', str(upstream),
        '--base', UPSTREAM_TAG,
        '--branch', FEDORA_BRANCH,
        '--python-version', '1.0',
        '--release', '1',
        '--no-push',
        *extra_args,
        str(distgit / SPEC_FILENAME),
    ]
    return subprocess.run(
        [sys.executable, str(EXPORTPATCHES), *args],
        cwd=distgit, env=env, capture_output=True, text=True,
        stdin=subprocess.DEVNULL,
    )


def commit_subjects(upstream, env, branch=FEDORA_BRANCH, base=UPSTREAM_TAG):
    result = git(['log', '--format=%s', branch, '^' + base], upstream, env)
    return result.stdout.splitlines()


# --- importpatches ---

def test_import_numberless_creates_bare_patch_declarations(distgit, upstream, env):
    add_commits(upstream, env, FEDORA_BRANCH, [
        'Add a numberless feature flag',
        'Fix a numberless bug',
    ])

    result = run_importpatches(distgit, upstream, env)

    assert result.returncode == 0, result.stderr
    spec = (distgit / SPEC_FILENAME).read_text()
    assert 'Patch: add-a-numberless-feature-flag.patch' in spec
    assert 'Patch: fix-a-numberless-bug.patch' in spec
    assert 'Patch1' not in spec and 'Patch2' not in spec
    assert (distgit / 'add-a-numberless-feature-flag.patch').exists()
    assert (distgit / 'fix-a-numberless-bug.patch').exists()


def test_import_numberless_removes_stale_numbered_patch(distgit, upstream, env):
    write_spec(distgit, existing=(
        '# 00099 # deadbeefdeadbeefdeadbeefdeadbeefdeadbeef\n'
        '# Some old numbered patch\n'
        'Patch99: 00099-some-old-numbered-patch.patch\n'
    ))
    (distgit / '00099-some-old-numbered-patch.patch').touch()
    commit_spec(distgit, env, 'seed a stale numbered patch')
    add_commits(upstream, env, FEDORA_BRANCH, ['Add a numberless feature flag'])

    result = run_importpatches(distgit, upstream, env)

    assert result.returncode == 0, result.stderr
    spec = (distgit / SPEC_FILENAME).read_text()
    assert 'Patch99' not in spec
    assert 'Patch: add-a-numberless-feature-flag.patch' in spec
    assert not (distgit / '00099-some-old-numbered-patch.patch').exists()


def test_import_rhel8_compat_sequential_numbers(distgit, upstream, env):
    add_commits(upstream, env, FEDORA_BRANCH, [
        'Add a numberless feature flag',
        'Fix a numberless bug',
    ])

    result = run_importpatches(distgit, upstream, env, '--rhel8-compat')

    assert result.returncode == 0, result.stderr
    spec = (distgit / SPEC_FILENAME).read_text()
    assert 'Patch1: add-a-numberless-feature-flag.patch' in spec
    assert 'Patch2: fix-a-numberless-bug.patch' in spec
    # RHEL 8 compat numbers must not leak into filenames or comments
    assert not (distgit / '00001-add-a-numberless-feature-flag.patch').exists()
    assert '# 00001' not in spec and '# 00002' not in spec


def test_import_mixture_of_numbered_and_numberless_errors(distgit, upstream, env):
    original_spec = (distgit / SPEC_FILENAME).read_text()
    add_commits(upstream, env, FEDORA_BRANCH, [
        '00001: A numbered patch',
        'A numberless patch',
    ])

    result = run_importpatches(distgit, upstream, env)

    assert result.returncode != 0
    assert 'mix' in (result.stdout + result.stderr).lower()
    # nothing should have been written before mode detection errored out
    assert (distgit / SPEC_FILENAME).read_text() == original_spec
    assert not list(distgit.glob('*.patch'))


def test_import_numbered_mode_regression(distgit, upstream, env):
    add_commits(upstream, env, FEDORA_BRANCH, ['00001: A numbered patch'])

    result = run_importpatches(distgit, upstream, env)

    assert result.returncode == 0, result.stderr
    spec = (distgit / SPEC_FILENAME).read_text()
    assert 'Patch1: 00001-a-numbered-patch.patch' in spec
    assert (distgit / '00001-a-numbered-patch.patch').exists()


def test_import_numberless_filename_collision_errors(distgit, upstream, env):
    original_spec = (distgit / SPEC_FILENAME).read_text()
    add_commits(upstream, env, FEDORA_BRANCH, [
        'Fix build issue',
        'Fix build issue',
    ])

    result = run_importpatches(distgit, upstream, env)

    assert result.returncode != 0
    assert 'fix-build-issue.patch' in (result.stdout + result.stderr)
    assert (distgit / SPEC_FILENAME).read_text() == original_spec
    assert not list(distgit.glob('*.patch'))


def test_import_numberless_patch_ending_summary_does_not_crash(distgit, upstream, env):
    # A numberless commit summary that happens to end in '.patch' but isn't
    # filename-safe (contains spaces) must not be confused with the
    # "old and dirty" dirty-style convention.
    add_commits(upstream, env, FEDORA_BRANCH, ['Update foo bar.patch'])

    result = run_importpatches(distgit, upstream, env)

    assert result.returncode == 0, result.stderr
    spec = (distgit / SPEC_FILENAME).read_text()
    assert 'Patch: update-foo-bar-patch.patch' in spec
    assert 'Update foo bar.patch' in spec
    assert (distgit / 'update-foo-bar-patch.patch').exists()


# --- exportpatches ---

def test_export_numbered_mode_inserts_number_for_trailing_unnumbered_patch(
    distgit, upstream, env,
):
    build_patch_files(upstream, distgit, env, [
        ('00001: Existing numbered patch', '00001-existing-numbered.patch'),
        ('Add a new thing', 'something.patch'),
    ])
    write_spec(distgit, existing=(
        'Patch1: 00001-existing-numbered.patch\n'
        'Patch666: something.patch\n'
    ))
    commit_spec(distgit, env)

    result = run_exportpatches(distgit, upstream, env, '--tag', 'test-numbered-1')

    assert result.returncode == 0, result.stderr
    assert commit_subjects(upstream, env) == [
        '00666: Add a new thing',
        '00001: Existing numbered patch',
    ]


def test_export_numberless_bare_never_inserts_number(distgit, upstream, env):
    build_patch_files(upstream, distgit, env, [
        ('Add a numberless feature flag', 'add-a-numberless-feature-flag.patch'),
        ('Fix a numberless bug', 'fix-a-numberless-bug.patch'),
    ])
    write_spec(distgit, existing=(
        'Patch: add-a-numberless-feature-flag.patch\n'
        'Patch: fix-a-numberless-bug.patch\n'
    ))
    commit_spec(distgit, env)

    result = run_exportpatches(distgit, upstream, env, '--tag', 'test-numberless-1')

    assert result.returncode == 0, result.stderr
    assert commit_subjects(upstream, env) == [
        'Fix a numberless bug',
        'Add a numberless feature flag',
    ]


def test_export_numberless_rhel8_compat_never_inserts_number(distgit, upstream, env):
    build_patch_files(upstream, distgit, env, [
        ('Add a numberless feature flag', 'add-a-numberless-feature-flag.patch'),
        ('Fix a numberless bug', 'fix-a-numberless-bug.patch'),
    ])
    write_spec(distgit, existing=(
        'Patch1: add-a-numberless-feature-flag.patch\n'
        'Patch2: fix-a-numberless-bug.patch\n'
    ))
    commit_spec(distgit, env)

    result = run_exportpatches(distgit, upstream, env, '--tag', 'test-numberless-2')

    assert result.returncode == 0, result.stderr
    assert commit_subjects(upstream, env) == [
        'Fix a numberless bug',
        'Add a numberless feature flag',
    ]


def test_export_numberless_rhel8_compat_numbers_out_of_order(distgit, upstream, env):
    # RHEL 8 compat numbers have no semantic meaning, so they need not
    # ascend in spec order; application order still follows spec order.
    build_patch_files(upstream, distgit, env, [
        ('Add a numberless feature flag', 'add-a-numberless-feature-flag.patch'),
        ('Fix a numberless bug', 'fix-a-numberless-bug.patch'),
    ])
    write_spec(distgit, existing=(
        'Patch2: add-a-numberless-feature-flag.patch\n'
        'Patch1: fix-a-numberless-bug.patch\n'
    ))
    commit_spec(distgit, env)

    result = run_exportpatches(distgit, upstream, env, '--tag', 'test-numberless-3')

    assert result.returncode == 0, result.stderr
    assert commit_subjects(upstream, env) == [
        'Fix a numberless bug',
        'Add a numberless feature flag',
    ]


def test_export_no_push_skips_push(distgit, upstream, env):
    build_patch_files(upstream, distgit, env, [
        ('00001: A numbered patch', '00001-a-numbered-patch.patch'),
    ])
    write_spec(distgit, existing='Patch1: 00001-a-numbered-patch.patch\n')
    commit_spec(distgit, env)

    # No 'fedora-python' remote exists in the scratch upstream repo, so a
    # real push attempt would crash the script (unhandled CalledProcessError);
    # exiting 0 here is itself proof no push was attempted.
    result = run_exportpatches(distgit, upstream, env, '--tag', 'test-no-push')

    assert result.returncode == 0, result.stderr
    assert 'skipping push' in result.stdout.lower()
    assert 'Traceback' not in result.stderr
    tags = git(['tag', '--list', 'test-no-push'], upstream, env).stdout
    assert 'test-no-push' in tags


def test_export_no_push_skips_existing_tag_instead_of_moving_it(distgit, upstream, env):
    build_patch_files(upstream, distgit, env, [
        ('Add a numberless feature flag', 'add-a-numberless-feature-flag.patch'),
        ('Fix a numberless bug', 'fix-a-numberless-bug.patch'),
    ])

    write_spec(distgit, existing='Patch: add-a-numberless-feature-flag.patch\n')
    commit_spec(distgit, env, 'one patch')
    result1 = run_exportpatches(distgit, upstream, env, '--tag', 'test-no-move')
    assert result1.returncode == 0, result1.stderr
    tag_commit_1 = git(['rev-list', '-n', '1', 'test-no-move'], upstream, env).stdout.strip()

    write_spec(distgit, existing=(
        'Patch: add-a-numberless-feature-flag.patch\n'
        'Patch: fix-a-numberless-bug.patch\n'
    ))
    commit_spec(distgit, env, 'two patches')
    result2 = run_exportpatches(distgit, upstream, env, '--tag', 'test-no-move')

    assert result2.returncode == 0, result2.stderr
    assert 'skipping tag creation' in result2.stdout.lower()
    tag_commit_2 = git(['rev-list', '-n', '1', 'test-no-move'], upstream, env).stdout.strip()
    assert tag_commit_2 == tag_commit_1
    # the branch itself did move forward to include the second patch
    assert commit_subjects(upstream, env) == [
        'Fix a numberless bug',
        'Add a numberless feature flag',
    ]

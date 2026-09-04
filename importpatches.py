#! /usr/bin/env python3

import subprocess
from pathlib import Path
import sys
import shlex
import re
import dataclasses
import enum
from textwrap import dedent
import tempfile
import shutil

import click  # dnf install python3-click
from rpmautospec import specfile_uses_rpmautospec, calculate_release


REPO_KEY = 'importpatches.upstream'
PATCH_NUMBER_RE = re.compile(r'^(\d+):')
SPECIAL_PATCH_NUMBERS = {
    'python-2.7.1-config.patch': 0,
    'python-2.6-rpath.patch': 16,
    'python-2.6.4-distutils-rpath.patch': 17,
}
PATCH_SECTION_START = '# (Patches taken from github.com/fedora-python/cpython)'
PATCH_SECTION_STARTS = {
    PATCH_SECTION_START,
    '# 00001 #',
    '# Modules/Setup.dist is ultimately used by the "makesetup" script to construct'
}
PATCH_SECTION_END = '# (New patches go here ^^^)'
FLIENAME_SAFE_RE = re.compile('^[a-zA-Z0-9._-]+$')
SOURCE_PATCH_RE = re.compile(r'^Source\d*:\s*(?P<filename>\S+\.patch)')
KEEP_PATCHES = {
    # These python2 patches are special
    '04000-disable-tk.patch',
    '05000-autotool-intermediates.patch',
}

BUNDLED_VERSION_RE = re.compile('-_([A-Z]+)_VERSION = "([0-9.]+)"')
BUNDLED_VERSION_BLURB = """
# The following versions of setuptools/pip are bundled when this patch is not applied.
# The versions are written in Lib/ensurepip/__init__.py, this patch removes them.
# When the bundled setuptools/pip wheel is updated, the patch no longer applies cleanly.
# In such cases, the patch needs to be amended and the versions updated here:
"""

# Older git versions guessed the abbrev count for hashes, but the behavior has changed
# We use explicit --abbrev value to match the current patches content
ABBREV = {
    (2, 7): 11,
    ...: 10,
}


def removeprefix(self, prefix):
    # PEP-616 backport
    if self.startswith(prefix):
        return self[len(prefix):]
    else:
        return self


class Mode(enum.Enum):
    NUMBERED = 'numbered'
    NUMBERLESS = 'numberless'


class ModeDetectionError(ValueError):
    """Raised when commits mix numbered and numberless conventions"""


def find_dirty_style_number(summary, message):
    """Resolve the patch number for an 'old and dirty' style commit summary
    (a bare patch filename), or None if none can be found"""
    match = re.search(r'\d{5,}', message)
    if match:
        return int(match.group(0))
    return SPECIAL_PATCH_NUMBERS.get(summary)


def detect_import_mode(messages, log):
    """Detect whether commits use numbered or numberless conventions

    `messages` maps commit_id -> full commit message text (as returned by
    `git show -s --format=%B`). `log` is the list of commit ids to inspect
    (order doesn't matter for detection).

    Returns Mode.NUMBERED or Mode.NUMBERLESS. Raises ModeDetectionError if
    commits mix both conventions.
    """
    numbered_ids = []
    numberless_ids = []
    for commit_id in log:
        message = messages[commit_id]
        summary = message.partition('\n')[0]
        if PATCH_NUMBER_RE.match(summary):
            numbered_ids.append(commit_id)
        elif summary.endswith('.patch') and FLIENAME_SAFE_RE.match(summary) and \
                find_dirty_style_number(summary, message) is not None:
            # "old and dirty" Python 2 style commits always carry a number
            # (in the message body or SPECIAL_PATCH_NUMBERS), so they count
            # as numbered too.
            numbered_ids.append(commit_id)
        else:
            numberless_ids.append(commit_id)

    if numbered_ids and numberless_ids:
        raise ModeDetectionError(
            'Commits mix numbered and numberless conventions; cannot '
            'auto-detect mode.\n'
            'Numbered-looking commits: '
            + ', '.join(c[:9] for c in numbered_ids) + '\n'
            'Numberless-looking commits: '
            + ', '.join(c[:9] for c in numberless_ids)
        )
    if numberless_ids:
        return Mode.NUMBERLESS
    return Mode.NUMBERED


def determine_patch_number_and_filename(commit_id, summary, message, mode):
    """Determine a patch's number (or None) and filename

    In Mode.NUMBERED, an existing NNNNN-*.patch file is reused if found,
    to keep filenames stable; otherwise (or in Mode.NUMBERLESS) a fresh
    filename is generated from the commit summary.
    """
    if mode == Mode.NUMBERED and PATCH_NUMBER_RE.match(summary):
        match = PATCH_NUMBER_RE.match(summary)
        number = int(match.group(1))
        paths = list(Path('.').glob(f'{number:05d}-*.patch'))
        if len(paths) == 0:
            path = Path(slugify(summary) + '.patch')
        elif len(paths) == 1:
            [path] = paths
        else:
            paths_msg = ''.join(f'\n   {p}' for p in paths)
            exit(
                'More than one patch file matches {number}: {paths_msg}'
            )
    elif mode == Mode.NUMBERED and summary.endswith('.patch') and \
            FLIENAME_SAFE_RE.match(summary):
        path = Path(summary)
        number = find_dirty_style_number(summary, message)
        if number is None:
            exit(
                f'Cannot find patch number in {commit_id[:9]}: {summary}'
            )
    elif mode == Mode.NUMBERLESS:
        number = None
        path = Path(slugify(summary) + '.patch')
    else:
        exit(
            f'Cannot derive patch filename from {commit_id[:9]}: {summary}'
        )
    return number, path


@dataclasses.dataclass
class PatchInformation:
    """All information needed about a patch"""
    patch_id: str
    comment: str
    filename: Path
    number: int | None = None
    trailer: str = ''


def handle_patch(repo, commit_id, message, *, tempdir, python_version, mode):
    """Handle a single patch, writing it to `tempdir` and returning info
    """
    summary, _, message_body = message.partition('\n')
    number, path = determine_patch_number_and_filename(
        commit_id, summary, message, mode,
    )

    patch_path = tempdir / path.name

    abbrev = ABBREV.get(python_version, ABBREV[...])

    with open(patch_path, 'w') as f:
        proc = run(
            'git', 'format-patch', '--stdout', '-1',
            '--minimal', '--patience', f'--abbrev={abbrev}', '--find-renames',
            '--zero-commit', '--no-signature', '--keep-subject',
            commit_id,
            cwd=repo, stdout=f
        )

    with open(patch_path) as f:
        hash_id = run('git', 'patch-id', '--stable', stdin=f).stdout.split()[0]

    spec_comment = []
    if summary.endswith('.patch') and number is not None:
        message_body = removeprefix(message_body.strip(), f'{number:05d} #\n')
    else:
        spec_comment.append(re.sub(PATCH_NUMBER_RE, '', summary))
    for line in message_body.splitlines():
        if line.lower().startswith('co-authored-by:'):
            continue
        if re.fullmatch(r'\(cherry picked from commit .{40}\)', line):
            continue
        spec_comment.append(line)

    if number == 189 and (python_version >= (3, 6) or python_version == (3,)):
        trailer = process_rpmwheels_patch(tempdir / path.name)
    else:
        trailer = ''

    return PatchInformation(
        patch_id=hash_id,
        comment='\n'.join(spec_comment).strip(),
        filename=path.name,
        number=number,
        trailer=trailer,
    )


def slugify(string):
    """Massage a string for filename safety

    This should be similar to how git-format-patch generates filenames.
    """
    return re.sub('[^a-z0-9_-]+', '-', string.lower()).strip('-')


def process_rpmwheels_patch(path):
    """Return a "trailer" with %global definitions for patch 189
    """
    versions = {}
    with path.open() as f:
        for line in f:
            if line.startswith('-_'):
                print(line, BUNDLED_VERSION_RE)
            match = BUNDLED_VERSION_RE.match(line.strip())
            if match:
                if match[1] in versions:
                    exit(f'Bundled version for {match[1]} appears twice')
                versions[match[1]] = match[2]
    version_lines = (
        f'%global {name.lower()}_version {ver}\n'
        for name, ver in sorted(versions.items())
    )
    return BUNDLED_VERSION_BLURB + ''.join(version_lines)


def run(*args, echo_stdout=True, **kwargs):
    """Like subprocess.run, but with logging and more appropriate defaults"""
    kwargs.setdefault('check', True)
    kwargs.setdefault('encoding', 'utf-8')
    kwargs.setdefault('stdout', subprocess.PIPE)

    prompt = click.style(f'{kwargs.get("cwd", "")}$ ', fg='cyan')
    redirs = []
    def add_redir(kwarg_name, symbol):
        stream = kwargs.get(kwarg_name)
        name = getattr(stream, 'name', None)
        if name:
            note = f' {symbol} {shlex.quote(name)}'
            redirs.append(click.style(note, fg='cyan'))
    add_redir('stdin', '<')
    add_redir('stdout', '>')
    click.echo(
        prompt + ' '.join(shlex.quote(a) for a in args) + ''.join(redirs),
        err=True,
    )

    result = subprocess.run(args, **kwargs)

    if result.stdout != None and result.stdout.strip():
        if echo_stdout:
            click.echo(result.stdout, err=True)
        else:
            lines = result.stdout.count("\n")
            click.echo(f'[{lines} lines]\n', err=True)
    return result


@click.command(context_settings={'help_option_names': ['-h', '--help']})
@click.option(
    '-r', '--repo', default=None, metavar='REPO',
    help="Repository with upstream code and patches" +
        f"(default is taken from Git config option `{REPO_KEY}`)"
)
@click.option(
    '-b', '--base', default=None, metavar='TAG',
    help="Git tag (commit-ish) corresponding to the upstream release " +
        "(default is derived from %{upstream_version} in SPEC) " +
        "(example: v3.9.0b4)"
)
@click.option(
    '-f', '--head', default=None, metavar='TAG',
    help="Git tag (commit-ish) from which to take patches " +
        "(default is derived from --base and Release in the spec) " +
        "(example: fedora-3.9.0b4-1)"
)
@click.option(
    '-v', '--python-version', default=None, metavar='X.Y',
    help="Python version, e.g. 3.10 (default extracted from spec name)"
)
@click.option(
    '--rhel8-compat', is_flag=True, default=False,
    help="In numberless mode, write sequential fake Patch1:, Patch2:, ... " +
        "numbers in the spec (not zero-padded, not part of the filename " +
        "or comment) for compatibility with RPM on RHEL 8, which doesn't " +
        "support bare 'Patch:' tags. No effect in numbered mode."
)
@click.argument(
    'spec', default=None, required=False, type=Path,
)
def main(spec, repo, base, head, python_version, rhel8_compat):
    """Update Fedora Python dist-git spec & patches from a Git repository

    Meant to be run in a local clone of Fedora's pythonX.Y dist-git.

    REPO should be a local clone of https://github.com/fedora-python/cpython.

    Patches for all commits between TAG and BRANCH in that repository are
    formatted into local files, and the *.spec file is updated with comments
    taken from commit messages.

    Patches are numbered with numbers from:
        https://fedoraproject.org/wiki/SIGs/Python/PythonPatches
    (below, NNNNN stands for the patch number)

    The commits must have summary, either::

        NNNNN: Summary line
        ...

    or the "old and dirty" style (used for Python 2)::

        patch-filename.patch

        # NNNNN #
        ...

    Patch filenames are preserved, if they begin with ``NNNNN-``.

    Patch 189 is handled specially: version numbers of bundled packages
    are extracted from it.

    If none of the commits between TAG and BRANCH have a NNNNN: prefix,
    numberless mode is used instead: commit summaries are plain text,
    patch filenames are always freshly generated from the summary, and
    the spec declares bare ``Patch:`` (or, with --rhel8-compat, sequential
    ``Patch1:``, ``Patch2:``, ... with no semantic meaning). Commits must
    not mix numbered and numberless conventions.

    Note that patch files are read and written from the current directory,
    regardless of the --repo option.

    There is no "dry run" option; commit/stash your work before running this.
    """
    with tempfile.TemporaryDirectory() as d:
        tempdir = Path(d)
        if spec == None:
            specs = list(Path('.').glob('*.spec'))
            if len(specs) != 1:
                raise click.UsageError(
                    "Either there must be a single spec file in current " +
                    "directory, or SPEC must be given."
                )
            spec = specs[0].resolve()
            click.secho(f'Assuming SPEC is {spec}', fg='yellow')

        if python_version is None:
            if spec.name.startswith('python') and spec.name.endswith('.spec'):
                # "python3.6.spec" -> python_version="3.6"
                python_version = spec.name[len('python'):-len('.spec')]
                if '.' not in python_version:
                    # "python36.spec" -> python_version="3.6"
                    # "python3.spec" -> python_version="3"
                    python_version = '.'.join(python_version)
                click.secho(
                    f'Assuming --python-version={python_version}',
                    fg='yellow'
                )
            else:
                raise click.UsageError(
                    "Cound not get version from spec name. " +
                    "Specify --python-version expliticly."
                )
        try:
            python_version = tuple(int(c) for c in python_version.split('.'))
        except ValueError:
            raise click.UsageError(
                "--python-version must be dot-separated integers."
            )

        if repo == None:
            proc = run(
                'git', 'config', '--get', REPO_KEY, check=False
            )
            if proc.returncode == 1:
                # The section or key is invalid
                raise click.UsageError(
                    f'Could not find upstream repo. Configure with ' +
                    f'`git config {REPO_KEY} .../cpython` or ' +
                    f'specify --repo explicitly.'
                )
            proc.check_returncode()
            repo = proc.stdout.strip()
            click.secho(f'Assuming --repo={repo}', fg='yellow')

        if base == None:
            with spec.open() as f:
                rpm_globals = []
                for line in f:
                    line = line.strip()
                    if line.startswith('%global ') and '%{expand:' not in line:
                        rpm_globals.append(removeprefix(line, '%global '))
                    if line.startswith('%global upstream_version'):
                        upstream_version = run(
                            'rpm',
                            *(f'-D{d}' for d in rpm_globals),
                            '--eval', '%upstream_version'
                        ).stdout.strip()
                        base = f'v{upstream_version}'
                        break
                else:
                    raise click.UsageError(
                        "Tag of upstream release not found in spec; check " +
                        "logic in the script or specify --base explicitly."
                    )
            click.secho(f'Assuming --base={base}', fg='yellow')

        if head == None:
            if specfile_uses_rpmautospec(spec):
                release = calculate_release(spec, complete_release=False)
            else:
                release = run(
                    'rpm',
                    '--undefine=dist',
                    '--queryformat=%{release}\n',
                    '--specfile', str(spec),
                ).stdout.splitlines()[0]
            upstream_version = base.lstrip('v')
            head = f'fedora-{upstream_version}-{release}'
            click.secho(f'Assuming --head={head}', fg='yellow')

        proc = run(
            'git', 'rev-list', head, '^' + base,
            cwd=repo, echo_stdout=False, check=False,
        )
        if proc.returncode != 0:
            click.secho(
                "Expected commits were not found. " +
                "Specify --base or --head explicitly.",
                fg='red',
            )
            def cyan(text):
                return click.style(text, fg='cyan')
            click.secho("Or did you forget one of these?")
            cmd = f"rpmdev-bumpspec *.spec -c 'Update to {upstream_version}'"
            click.secho(f"- $ {cyan(cmd)}")
            click.secho(
                f"- Rebase Fedora branch in {cyan(repo)} onto {cyan(base)} " +
                f"and tag as {cyan(head)}"
            )
            exit(1)
        log = proc.stdout.splitlines()
        if len(log) >= 100:
            exit(
                'There are more than 100 patches. Probably a wrong branch ' +
                'was selected; try giving -c explicitly.'
            )

        messages = {
            commit_id: run(
                'git', 'show', '-s', '--format=%B', commit_id,
                cwd=repo, echo_stdout=False,
            ).stdout.strip()
            for commit_id in log
        }
        try:
            mode = detect_import_mode(messages, log)
        except ModeDetectionError as e:
            exit(str(e))
        click.secho(f'Detected mode: {mode.value}', fg='yellow')

        patches_section = []
        rhel8_number = 0
        seen_filenames = {}
        for commit_id in reversed(log):
            result = handle_patch(
                repo, commit_id, messages[commit_id], tempdir=tempdir,
                python_version=python_version, mode=mode,
            )
            if result.filename in seen_filenames:
                exit(
                    f'Patch filename {result.filename} would be generated ' +
                    f'for both {seen_filenames[result.filename][:9]} and ' +
                    f'{commit_id[:9]}; rename one of the commits so their ' +
                    'summaries produce distinct filenames.'
                )
            seen_filenames[result.filename] = commit_id
            comment = '\n'.join(
                f'# {l}' if l else '#' for l in result.comment.splitlines()
            )
            if result.number is not None:
                header = f'# {result.number:05d} # {result.patch_id}'
                patch_tag = f'Patch{result.number}: {result.filename}'
            else:
                header = f'# {result.patch_id}'
                if rhel8_compat:
                    rhel8_number += 1
                    patch_tag = f'Patch{rhel8_number}: {result.filename}'
                else:
                    patch_tag = f'Patch: {result.filename}'
            section = dedent(f"""
                {header}
                %s
                {patch_tag}
            """) % comment.replace('%', '%%')
            if result.trailer:
                section = section.rstrip() + result.trailer
            patches_section.append(section)

        spec_lines = []
        outfile_path = tempdir / spec.name
        keep_patches = KEEP_PATCHES.copy()
        with open(outfile_path, 'w') as outfile:
            with spec.open('r') as infile:
                echoing = True
                found_start = False
                found_modern_start = False
                for line in infile:
                    if match := SOURCE_PATCH_RE.match(line.rstrip()):
                        keep_patches.add(match.group('filename'))
                    if line.rstrip() == PATCH_SECTION_END:
                        echoing = True
                    if line.rstrip() in PATCH_SECTION_STARTS:
                        is_modern = (line.rstrip() == PATCH_SECTION_START)
                        if found_start:
                            if found_modern_start and not is_modern:
                                # Specfile was already converted
                                if echoing:
                                    outfile.write(line)
                                continue
                            else:
                                exit('Spec has multiple starts of section')
                        found_start = True
                        if is_modern:
                            found_modern_start = True
                        echoing = False
                        outfile.write(PATCH_SECTION_START + '\n')
                        outfile.writelines(patches_section)
                        outfile.write('\n')
                    if echoing:
                        outfile.write(line)

        if not found_start:
            exit('Patches section not found in spec file')
        if not echoing:
            exit('End of patches section not found in spec file')

        click.secho(f'Updating patches and spec', fg='yellow')

        # Remove all existing patches
        for path in Path('.').glob('*.patch'):
            if path.name not in keep_patches:
                path.unlink()

        # Move all files from tempdir to current directory
        for path in tempdir.iterdir():
            shutil.move(path, path.name)

    click.secho('OK', fg='green')


if __name__ == '__main__':
    try:
        main()
    except SystemExit as e:
        if e.code != None:
            raise
        click.secho(f"{e}", fg='red')
        raise SystemExit(1)

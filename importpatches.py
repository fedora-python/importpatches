#! /usr/bin/env python3

import subprocess
from pathlib import Path
import sys
import shlex
import re
import dataclasses
from textwrap import dedent
import tempfile
import shutil

import click  # dnf install python3-click


REPO_KEY = 'importpatches.upstream'
PATCH_NUMBER_RE = re.compile('^(\d+):')
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
RELEASE_RE = re.compile(r'Release: ([0-9]+)%\{\?dist\}')

BUNDLED_VERSION_RE = re.compile('-_([A-Z]+)_VERSION = "([0-9.]+)"')
BUNDLED_VERSION_BLURB = """
# The following versions of setuptools/pip are bundled when this patch is not applied.
# The versions are written in Lib/ensurepip/__init__.py, this patch removes them.
# When the bundled setuptools/pip wheel is updated, the patch no longer applies cleanly.
# In such cases, the patch needs to be amended and the versions updated here:
"""


def removeprefix(self, prefix):
    # PEP-616 backport
    if self.startswith(prefix):
        return self[len(prefix):]
    else:
        return self


@dataclasses.dataclass
class PatchInformation:
    """All information needed about a patch"""
    number: int
    patch_id: str
    comment: str
    filename: Path
    trailer: str = ''


def handle_patch(repo, commit_id, *, tempdir):
    """Handle a single patch, writing it to `tempdir` and returning info
    """
    message = run(
        'git', 'show', '-s', '--format=%B', commit_id,
        cwd=repo,
    ).stdout.strip()
    summary, _, message_body = message.partition('\n')
    if match := PATCH_NUMBER_RE.match(summary):
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
    elif summary.endswith('.patch') and FLIENAME_SAFE_RE.match(summary):
        path = Path(summary)
        if match := re.search('\d{5,}', message):
            number = int(str(match.group(0)))
        elif summary in SPECIAL_PATCH_NUMBERS:
            number = SPECIAL_PATCH_NUMBERS[summary]
        else:
            exit(
                f'Cannot find patch number in {commit_id[:9]}: {summary}'
            )
    else:
        exit(
            f'Cannot derive patch filename from {commit_id[:9]}: {summary}'
        )

    patch_path = tempdir / path.name

    with open(patch_path, 'w') as f:
        proc = run(
            'git', 'format-patch', '--stdout', '-1',
            '--minimal', '--patience', '--abbrev=78', '--find-renames',
            '--zero-commit', '--no-signature',
            commit_id,
            cwd=repo, stdout=f
        )

    with open(patch_path) as f:
        hash_id = run('git', 'patch-id', '--stable', stdin=f).stdout.split()[0]

    spec_comment = []
    if summary.endswith('.patch'):
        body = removeprefix(message_body.strip(), f'{number:05d} #\n')
        spec_comment.append(body)
    else:
        spec_comment.append(re.sub(PATCH_NUMBER_RE, '', summary))
        for line in message_body.splitlines():
            if line.lower().startswith('co-authored-by:'):
                continue
            if re.fullmatch(r'\(cherry picked from commit .{40}\)', line):
                continue
            spec_comment.append(line)

    if number == 189:
        trailer = process_rpmwheels_patch(tempdir / path.name)
    else:
        trailer = ''

    return PatchInformation(
        number, hash_id, '\n'.join(spec_comment).strip(), path.name,
        trailer,
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
            if match := BUNDLED_VERSION_RE.match(line.strip()):
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
@click.argument(
    'spec', default=None, required=False, type=Path,
)
def main(spec, repo, base, head):
    """Update Fedora Python dist-git spec & patches from a Git repository

    Meant to be run in a local clone of Fedora's pythonX.Y dist-git.

    REPO should be a local clone of https://github.com/fedora-python/cpython.

    Patches for all commits between TAG and BRANCH in that repository are
    formatted into loal files, and the *.spec file is updated with comments
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
                    if line.startswith('%global '):
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

        patches_section = []
        for commit_id in reversed(log):
            result = handle_patch(
                repo, commit_id, tempdir=tempdir,
            )
            comment = '\n'.join(
                f'# {l}' if l else '#' for l in result.comment.splitlines()
            )
            section = dedent(f"""
                # {result.number:05d} # {result.patch_id}
                %s
                Patch{result.number}: {result.filename}
            """) % comment.replace('%', '%%')
            if result.trailer:
                section = section.rstrip() + result.trailer
            patches_section.append(section)

        spec_lines = []
        outfile_path = tempdir / spec.name
        with open(outfile_path, 'w') as outfile:
            with spec.open('r') as infile:
                echoing = True
                found_start = False
                for line in infile:
                    if line.rstrip() == PATCH_SECTION_END:
                        echoing = True
                    if line.rstrip() in PATCH_SECTION_STARTS:
                        outfile.write(PATCH_SECTION_START + '\n')
                        if found_start:
                            exit('Spec has multiple starts of section')
                        found_start = True
                        echoing = False
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

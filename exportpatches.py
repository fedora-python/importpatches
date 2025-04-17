#! /usr/bin/env python3

import subprocess
from pathlib import Path
import shlex
import re
import readline
import tempfile
import os

import click  # dnf install python3-click


REPO_KEY = 'importpatches.upstream'


def removeprefix(self, prefix, regex=False):
    if regex:
        return re.sub(r'^{0}'.format(prefix), '', self)
    else:
        # PEP-616 backport
        if self.startswith(prefix):
            return self[len(prefix):]
        else:
            return self

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
    '-f', '--branch', default=None, metavar='TAG',
    help="Git branch where to apply patches " +
        "(default is derived from --base and Version in the spec) " +
        "(example: fedora-3.9)"
)
@click.option(
    '-v', '--python-version', default=None, metavar='X.Y',
    help="Python version, e.g. 3.10 (default extracted from spec name)"
)
@click.option(
    '-x', '--release', default=None, metavar='XY',
    help="Release, e.g. 15 (default extracted from spec file release)"
)
@click.option(
    '-t', '--tag', default=None, metavar='XY',
    help="Custom tag, e.g. fedora-3.13.0-1"
)
@click.argument(
    'spec', default=None, required=False, type=Path,
)
def main(spec, repo, base, branch, python_version, release, tag):
    """
    Update cpython Git repository with patches from dist-git spec

    Meant to be run in a local clone of Fedora's pythonX.Y dist-git.

    REPO should be a local clone of https://github.com/fedora-python/cpython.

    Patches present in the spec file are applied to cpython repository.
    When creating a new patch just add it into Patch section in spec file.

    Supported format is:

    PatchNNNNN: <file>/<url>, where NNNNN is a patch number from:
        https://fedoraproject.org/wiki/SIGs/Python/PythonPatches

    When exportpatches successfuly finishes, it is expected to run
    importpatches to import patch to the spec file in a standardized form.

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
            tuple(int(c) for c in python_version.split('.'))
        except ValueError:
            raise click.UsageError(
                "--python-version must be dot-separated integers."
            )

        if repo == None:
            proc = run(
                *shlex.split('git config --get'), REPO_KEY, check=False
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

        if release == None:
            with spec.open() as f:
                for line in f:
                    line = line.strip()
                    if line.startswith('Release:'):
                        release = re.search(r"[0-9]+", line).group()
                        break
                else:
                    raise click.UsageError(
                        "Release not found in spec; check " +
                        "logic in the script or specify --release explicitly."
                    )
            click.secho(f'Assuming --release={release}', fg='yellow')

        with spec.open() as f:
            patches = {}
            for line in f:
                line = line.strip()
                if line.startswith('Patch'):
                    try:
                        patch_number = removeprefix(re.match("^Patch[0-9]{3}",
line).group(), 'Patch')
                    except AttributeError:
                        click.secho(
                            "Patch number is missing.",
                            fg='red',
                        )
                        exit(1)
                    update = {patch_number : removeprefix(line, 'Patch[0-9]*: *',
regex=True)}
                    patches.update(**update)
        click.secho(f'Found {len(patches)} ({patches}) patches from spec file', fg='yellow')

        click.secho(f'Changing working directory to {repo}', fg='yellow')
        os.chdir(repo)
        path = str(spec).rsplit('/',1)[0]

        click.secho(f'Checking if git repo is clean', fg='yellow')
        try:
            proc = run(
                *shlex.split(f"git diff-index --quiet HEAD --")
            )
        except subprocess.CalledProcessError:
            click.secho(
                "Refusing to continue your cpython repository is not clean.",
                fg='red',
            )
            exit(1)

        if branch == None:
            branch = f'fedora-{python_version}'

        click.secho(f'Switching branch to {branch}', fg='yellow')
        try:
            proc = run(
                *shlex.split(f'git switch {branch}')
            )
        except subprocess.CalledProcessError:
            click.secho(
                "git switch failed - the branch does not exist",
                fg='red',
            )
            create_branch = input(f"Do you want to create a new branch {branch}? [y/n]")
            if create_branch == "y":
                proc = run(
                    *shlex.split(f'git switch -c {branch}')
                )
            else:
                exit(1)
        proc = run(
            *shlex.split(f"git reset --hard {base}")
        )

        for patch_number, patch in patches.items():
            head_hash = run(
                *shlex.split(f"git rev-parse HEAD")
            )

            patch_filename = patch.rsplit('/', 1)[-1]
            try:
                proc = run(
                    *shlex.split(f"git am --committer-date-is-author-date {path}/{patch_filename}")
                )
            except subprocess.CalledProcessError:
                click.secho(
                    "git am failed, are you sure that patches apply?",
                    fg='red',
                )
                exit(1)
            proc = run(
                *shlex.split(f"git log --format=%B -n 1"),
                stdout=subprocess.PIPE
            )
            # checking if patch number is present at the beginning of the
            # commit message
            pattern = re.compile(r"^[0-9]{5}:")
            if not pattern.match(proc.stdout):
                patch_number_with_padding = patch_number.rjust(5, '0')
                proc = run(
                    'git', 'commit', '--amend', '-m', f'{patch_number_with_padding}: {proc.stdout}'
                )
            head1_hash = run(
                *shlex.split(f"git rev-parse HEAD^1")
            )
            if head_hash.stdout != head1_hash.stdout:
                click.secho(
                    "Multiple commits in one patch are not supported. Commit were cherry-picked, you can continue manually in the repo.",
                    fg='red',
                )
                exit(1)

    if tag == None:
        tag = f'fedora-{upstream_version}-{release}'

    while(True):
        click.secho(f'Checking if tag ({tag}) already exists', fg='yellow')
        repo_tags = run(
            *shlex.split(f"git tag --list {tag}"),
            echo_stdout=False
        )
        tag_exists = False
        for repo_tag in repo_tags.stdout.split('\n'):
            if repo_tag.startswith(f"{tag}"):
                tag_exists = True
        if tag_exists:
            click.secho(
                f"Tag ({tag}) already exists in the repository.",
                fg='yellow',
            )
            click.secho(f"Create a new tag? [y/n]", fg='yellow')
            c = input()
            if c == 'y':
                tag = input("Tag name: ")
            else:
                click.secho(
                    f"Exiting...",
                    fg='red',
                )
                exit(1)
        else:
            break

    click.secho(f"About to tag the current state of repository with {tag}.", fg='yellow')

    run(*shlex.split(f"git tag {tag}"))

    click.secho(
        f"Following commands will push the changes:",
        fg='yellow',
    )
    print(f"git push fedora-python {tag}")
    print(f"git push --force -u fedora-python fedora-{python_version}")
    click.secho(
        f"Do you wish to continue? [y/n]",
        fg='yellow',
    )
    c = input()
    if c == 'y':
        proc = run(
            *shlex.split(f"git push fedora-python {tag}")
        )
        proc = run(
            *shlex.split(f"git push --force -u fedora-python fedora-{python_version}")
        )
    else:
        click.secho(
            f"Exiting...",
            fg='red',
        )
        exit(1)

    click.secho('OK', fg='green')


if __name__ == '__main__':
    try:
        main()
    except SystemExit as e:
        if e.code != None:
            raise
        click.secho(f"{e}", fg='red')
        raise SystemExit(1)

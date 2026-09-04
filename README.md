# importpatches.py and exportpatches.py

Commands to update Fedora Python dist-git spec & patches from/to a Git repository

Meant to be used with a local clone of [fedora-python/cpython]
which includes tags like `vX.Y.Z` (upstream releases) and branches
like `fedora-X.Y` (`vX.Y.Z` + commits for individual patches).
The exportpatches script assumes that the remote is named `fedora-python`.

The summary lines of patch commits must start with `NNNNN: `, where NNNNN is
the patch number (registered in the [patch registry]).
The rest of the commit message should be usable in the spec.

(It also mostly works with the `fedora-2.7` branch, which uses different
conventions.)

Alternatively, a **numberless mode** is supported (intended for Python 3.10+,
which no longer has the specially-handled patch 189): if none of the commits
being imported have a `NNNNN: ` prefix, `importpatches` switches to
numberless mode automatically. Patch filenames are always freshly generated
(no NNNNN- prefix, no filename-stability lookup), and the spec declares bare
`Patch:` (or, with `--rhel8-compat`, sequential `Patch1:`, `Patch2:`, ... with
no semantic meaning, for RPM on RHEL 8, which doesn't support bare `Patch:`
tags). `exportpatches` auto-detects numberless mode too, from the spec's
`Patch` declarations and patch filenames (rather than commit messages, since
it hasn't created any commits yet), and never adds a number (real or RHEL 8
compat) to a commit message in that mode. Commits/patches must not mix
numbered and numberless conventions; both scripts error out if they do.

[fedora-python/cpython]: https://github.com/fedora-python/cpython
[patch registry]: https://fedoraproject.org/wiki/SIGs/Python/PythonPatches


## Dependencies

- [click](https://pypi.org/project/click/) (`dnf install python3-click`)
- [rpmautospec](https://docs.pagure.org/rpmautospec/) (`dnf install python3-rpmautospec`)
- [pytest](https://pypi.org/project/pytest/) (`dnf install python3-pytest`), only needed to run the test suite


## Setup

Add the scripts to your `$PATH`, for example:

    ln -s $PWD/importpatches.py ~/.local/bin/importpatches
    ln -s $PWD/exportpatches.py ~/.local/bin/exportpatches

The scripts need to know where your local clone of `fedora-python/cpython` is,
and uses Git configuration as a default.

In your clone of dist-git, run `git config importpatches.upstream .../cpython`.
(Replace `.../cpython` with your clone of `fedora-python/cpython`, of course.)
Alternatively, run `git config` with `--global`
to avoid the need to set this in all dist-git clones of Pythons.


## Usage

Run `importpatches.py`/`exportpatches.py` without arguments in your clone of dist-git.
If the defaults don't work for you, run with `--help` to see the options.


## What it does

### importpatches

importpatches takes commits between a base tag and head tag
in the [fedora-python/cpython] clone and turns them into patch files
plus updated spec comments in the current dist-git checkout.
Each commit is formatted into a *.patch file (named NNNNN-... where possible)
and its Git patch-id is written into the spec comment above
the PatchNNNNN: line, along with the commit message body.
It replaces the whole patches section between the
`(Patches taken from github.com/fedora-python/cpython)` and
`(New patches go here ^^^)` markers, removing old patch files and moving
the new ones into place.

In numberless mode (auto-detected, see above), patch filenames are always
generated fresh from the commit summary instead of being matched by number,
and the spec gets bare `Patch:` lines (or `Patch1:`, `Patch2:`, ... with
`--rhel8-compat`, which is a no-op in numbered mode).


### exportpatches

exportpatches is an inverse of importpatches: it takes patches listed
in the dist-git spec and applies them onto a local clone of [fedora-python/cpython],
producing the fedora-X.Y branch from the upstream vX.Y.Z tag.
Each patch becomes one commit, with the summary line prefixed by its patch
number (NNNNN: ) as required by the patch registry.
It then tags the result and pushes the branch and tag to the fedora remote.

In numberless mode (auto-detected from the spec's `Patch` declarations, see
above), no number is ever prefixed onto a commit's summary line, even if the
spec uses the `--rhel8-compat` sequential `Patch1:`, `Patch2:`, ... scheme.


## Git hash IDs

The importpatches script adds Git hash IDs to the spec file.
These are hashes of the patch content, ignoring tings like context lines and
comments.
When one of these changes, pay special atttention to the patch diff.


## `%autorelease` support

Spec files using `%autorelease` are supported.
When `%autorelease` is detected, the release number is calculated
via `rpmautospec.calculate_release()` instead of parsing it from the spec
or querying `rpm`.


## Running tests

    python3 -m pytest


## License

The script is available under the MIT license. May it serve you well.

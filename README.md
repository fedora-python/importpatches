# importpatches.py

A command to update Fedora Python dist-git spec & patches from a Git repository

Meant to be used with a local clone of [fedora-python/cpython]
which includes tags like `vX.Y.Z` (upstream releases) and branches
like `fedora-X.Y` (`vX.Y.Z` + commits for individual patches).

The summary lines of patch commits must start with `NNNNN: `, where NNNNN is
the patch number (registered in the [patch registry]).
The rest of the commit message should be usable in the spec.

(It also mostly works with the `fedora-2.7` branch, which uses different
conventions.)

[fedora-python/cpython]: https://github.com/fedora-python/cpython
[patch registry]: https://fedoraproject.org/wiki/SIGs/Python/PythonPatches


## Setup

Add the script to your `$PATH`.

The script needs to know where your local clone of `fedora-python/cpython` is,
and uses Git configuration as a default.

In your clone of dist-git, run `git config importpatches.upstream .../cpython`.
(Replace `.../cpython` with your clone of `fedora-python/cpython`, of course.)


## Usage

Just run `importpatches.py` in your clone of dist-git.
If the defaults don't work for you, run with `--help` to see the options.


## Git hash IDs

The script adds Git hash IDs to the spec file.
These are hashes of the patch content, ignoring tings like context lines and
comments.
When one of these changes, pay special atttention to the patch diff.

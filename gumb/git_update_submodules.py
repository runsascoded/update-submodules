#!/usr/bin/env python
import re
import shlex
from os.path import exists
from subprocess import Popen, PIPE
from typing import Optional

import click

from utz import process, cd, git, DefaultDict, err
from utz.git.remote import git_remote_sha
from utz.git.repo import git_repo
from utz.git.submodule import git_submodules


def new_tree_lines(submodule_commits: dict, log=None) -> list[str]:
    lines = process.lines('git', 'ls-tree', 'HEAD', log=log)
    new_lines = []
    for line in lines:
        mode, typ, hexsha, path = re.split(r'\s+', line, 3)
        if typ == 'commit' and path in submodule_commits:
            new_ref = submodule_commits[path]
            if re.fullmatch(r'[\da-f]{40}', new_ref):
                new_hexsha = new_ref
            elif exists(path):
                with cd(path):
                    new_hexsha = git.log.sha(new_ref, log=log)
            else:
                url = git_submodules()[path].url
                new_hexsha = git_remote_sha(url, new_ref, log=log)
            new_line = f'{mode} {typ} {new_hexsha}\t{path}'
            new_lines.append(new_line)
        else:
            new_lines.append(line)

    return new_lines


def mktree(submodule_commits: dict, verbose: int = 0) -> str:
    kwargs = dict() if verbose else dict(log=None)
    new_lines = new_tree_lines(submodule_commits=submodule_commits, **kwargs)
    cmd = ['git', 'mktree']
    stdin = '\n'.join(new_lines)
    if verbose >= 3:
        err(f"Passing lines to {shlex.join(cmd)}:\n{stdin}")
    p = Popen(cmd, stdout=PIPE, stdin=PIPE)
    tree_id = p.communicate(input=stdin.encode())[0].decode().rstrip('\n')
    return tree_id


no_reset_flag = click.option('-R', '--no-reset', is_flag=True, help="Skip calling `git reset <new commit ID>`")
verbose_flag = click.option('-v', '--verbose', count=True, help='1x: print tree, commit SHAs; 2x: also print `git` commands as they are run; 3x: print `git ls-tree` output for new commit')


@click.command('git-update-submodules')
@click.option('-F', 'message_files', multiple=True, help="Pass-through to `git commit-tree`'s `-F` flag: read the commit log message from the given file. Use - to read from the standard input. This can be given more than once and the content of each file becomes its own paragraph.")
@click.option('-m', '--message', 'messages', multiple=True, help="Pass-through to `git commit-tree`'s `-m/--message` flag: a paragraph in the commit log message. This can be given more than once and each <message> becomes its own paragraph.")
@click.option('-p', '--parent', 'parents', multiple=True, help="Pass-through to `git commit-tree`'s `-p/--parent` flag: each -p indicates the id of a parent commit object.")
@no_reset_flag
@click.option('-S', '--gpg-sign', is_flag=True, help="Similar to `git commit-tree`'s `-S/--gpg-sign` flag: GPG-sign commits with the committer identity.")
@click.option('--gpg-sign-as', help="Similar to `git commit-tree`'s `--gpg-sign=<keyid>`: GPG-sign commits with the specified <keyid>.")
@verbose_flag
@click.argument('ref_strs', nargs=-1)
def main(ref_strs, verbose, **kwargs):
    _, refs = DefaultDict.parse_configs(ref_strs)
    if not refs:
        if verbose:
            err("No refs found, exiting")
        return

    update_submodules(refs, verbose=verbose, **kwargs)


def update_submodules(
        refs: dict,
        message_files=None,
        messages=None,
        parents=None,
        no_reset=False,
        gpg_sign=None,
        gpg_sign_as=None,
        verbose=0,
) -> Optional[str]:
    if not refs:
        return

    args = []

    def add_args(flag, values):
        nonlocal args
        args += [ arg for value in values for arg in [ flag, value ] ]

    if message_files and messages:
        raise ValueError("Pass `-F` xor `-m/--message`")
    elif message_files:
        add_args('-F', message_files)
    else:
        if not messages:
            subject = f'Update submodules: {", ".join(refs)}'
            body = '\n- '.join([ '', *[ f'{k}={v}' for k, v in refs.items() ] ])
            default_msg = f'{subject}\n{body}'
            messages = [ default_msg ]
        add_args('-m', messages)
    if not parents:
        parents = ['HEAD']
    add_args('-p', parents)
    if gpg_sign and gpg_sign_as:
        raise ValueError("Pass `-S/--gpg-sign` xor `--gpg-sign-as`")
    elif gpg_sign:
        args += [ '-S' ]
    elif gpg_sign_as:
        args += [ f'-S{gpg_sign_as}']

    tree_id = mktree(submodule_commits=refs, verbose=verbose)
    kwargs = dict() if verbose else dict(log=None)
    if verbose:
        err(f"Made tree: {tree_id}")
    commit_sha = process.line('git', 'commit-tree', *args, tree_id, **kwargs)
    if verbose:
        err(f"Made commit: {commit_sha}")
    if not no_reset:
        repo = git_repo()
        if repo.bare:
            process.run('git', 'update-ref', 'HEAD', commit_sha, **kwargs)
        else:
            process.run('git', 'reset', commit_sha, **kwargs)
    return commit_sha


if __name__ == '__main__':
    main()

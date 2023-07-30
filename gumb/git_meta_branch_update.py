#!/usr/bin/env python

from os import environ
from typing import Tuple, Optional

import click
from git import Repo

from utz import process, DefaultDict, err, parallel
from utz.git import github
from utz.git.git_update_submodules import update_submodules, verbose_flag, no_reset_flag
from utz.git.remote import git_remote_sha


@click.command('git-meta-branch-update')
@click.option('-g/-G', '--github-step-summary/--no-github-step-summary', is_flag=True, default=None)
@click.option('-P', '--no-push', is_flag=True, help='Skip pushing')
@no_reset_flag
@verbose_flag
@click.argument('ref_strs', nargs=-1)
def main(github_step_summary, no_push, no_reset, verbose, ref_strs):
    refs = DefaultDict.load(ref_strs, fallback='HEAD')
    if not refs:
        if verbose:
            err("No refs found, exiting")
        return

    def get_new_sha_entry(submodule) -> Optional[Tuple[str, Tuple[str, str]]]:
        ref = refs[submodule]
        if not ref:
            return
        cur_sha = submodule.hexsha
        new_sha = git_remote_sha(submodule.url, ref, log=err if verbose else None)
        return submodule.name, (cur_sha, new_sha)

    repo = Repo()
    submodules = repo.submodules
    submodules_dict = { submodule.name: submodule for submodule in submodules }
    shas = dict(parallel(submodules, get_new_sha_entry))

    new_shas = {
        name: new_sha
        for name, (cur_sha, new_sha) in shas.items()
        if cur_sha != new_sha
    }
    for name, sha in new_shas.items():
        err(f'{name}: {sha}')

    new_commit_sha = update_submodules(new_shas, no_reset=no_reset, verbose=verbose)
    if new_commit_sha and not no_push:
        process.run('git', 'push')

        if github_step_summary is not False:
            GITHUB_STEP_SUMMARY = environ.get('GITHUB_STEP_SUMMARY')
            if GITHUB_STEP_SUMMARY:
                name_with_owner = process.json('gh', 'repo', 'view', '--json', 'nameWithOwner', log=err if verbose else None)['nameWithOwner']
                bullet_strs = []
                for name, (cur_sha, new_sha) in shas.items():
                    if name not in new_shas:
                        continue
                    submodule = submodules_dict[name]
                    submodule_name_with_owner = github.parse_url(submodule.url)
                    bullet_str = f'- {name}: [`{cur_sha}..{new_sha}`](https://github.com/{submodule_name_with_owner}/compare/{cur_sha}..{new_sha})'
                    bullet_strs.append(bullet_str)

                bullets_str = "\n".join(bullet_strs)
                md = f'''Pushed submodule update ([`{new_commit_sha[:7]}`](https://github.com/{name_with_owner}/commit/{new_commit_sha})):

    {bullets_str}
    '''
                with open(GITHUB_STEP_SUMMARY, 'a') as f:
                    f.write(md)


if __name__ == '__main__':
    main()

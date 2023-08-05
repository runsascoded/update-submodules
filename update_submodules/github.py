#!/usr/bin/env python
from base64 import b64decode
from configparser import ConfigParser
from functools import wraps
from os import environ
from os.path import exists
from sys import stdin

import click
from github import Auth, Github
from github.InputGitTreeElement import InputGitTreeElement
from requests import patch
from utz import DefaultDict, parallel, singleton, err
from utz.collections import Expected1Found0
from utz.git import github
from utz.git.github import repository_option

GITHUB_OUTPUT = 'GITHUB_OUTPUT'
GITHUB_STEP_SUMMARY = 'GITHUB_STEP_SUMMARY'
SHORT_SHA_LEN = 7


def refs_args(fn):
    @click.argument('ref_strs', nargs=-1)
    @wraps(fn)
    def _fn(*args, ref_strs, **kwargs):
        refs = DefaultDict.load(ref_strs, fallback='HEAD')
        return fn(*args, refs=refs, **kwargs)

    return _fn


def branch_option(
        *flag_args,
        help="Branch to update, defaults to `[ $GITHUB_EVENT_NAME == pull_request ] && $GITHUG_HEAD_REF || $GIRHUB_REF_NAME`",
        **flag_kwargs
):
    if not flag_args:
        flag_args = ('-b', '--branch')

    def option(fn):
        @click.option(*flag_args, help=help, **flag_kwargs)
        @wraps(fn)
        def _fn(*args, branch, **kwargs):
            if not branch:
                if environ.get('GITHUB_EVENT_NAME') == 'pull_request':
                    branch = environ.get('GITHUB_HEAD_REF')
                else:
                    branch = environ.get('GITHUB_REF_NAME')
            return fn(*args, branch=branch, **kwargs)

        return _fn

    return option


@click.command('github-update-submodules')
@branch_option()
@click.option('-F', 'message_files', multiple=True, help="Files containing commit log message paragraphs, use \"-\" to read from the standard input. Can be passed more than once.")
@click.option('-g', '--github-step-summary', help=f'Write a summary of the new commit to this path (defaults to ${GITHUB_STEP_SUMMARY}, "-" for stdout, "" to disable)')
@click.option('-j', '--num-jobs', type=int, default=0, help='Max number of parallel jobs while fetching current submodule SHAs (default: 0 ⟹ cpu_count())')
@click.option('-m', '--message', 'messages', multiple=True, help="Message paragraphs in the commit log message, can be passed more than once.")
@click.option('-o', '--github-output', help=f'Write the newly-created commit\'s SHA to this path (defaults to ${GITHUB_OUTPUT}, "-" for stdout, "" to disable)')
@repository_option()
@refs_args
def main(branch, message_files, github_step_summary, num_jobs, messages, github_output, repository, refs):
    """Update submodules in a GitHub repository, using the API / without cloning the repository.

    Positional <ref_str> arguments can be of the form `<submodule>=<ref>` or just `<ref>` (to set a fallback for all submodules).
    """
    token = environ.get('GITHUB_TOKEN', environ.get('GH_TOKEN'))
    if not token:
        token_path = '.github-token'
        if exists(token_path):
            with open(token_path) as f:
                token = f.read().strip()
        else:
            raise RuntimeError('No GitHub token found, please set the GITHUB_TOKEN environment variable or create a .github_token file in the current directory')
    auth = Auth.Token(token=token)
    gh = Github(auth=auth)
    repo = gh.get_repo(repository)
    commit = repo.get_commit(branch or 'HEAD')
    tree = commit.commit.tree
    elems = tree.tree
    submodules = {
        elem.path: { 'path': elem.path, 'sha': elem.sha }
        for elem in elems
        if elem.type == 'commit'
    }
    try:
        gitmodules_elem = singleton([ e for e in elems if e.path == '.gitmodules' ], dedupe=False)
    except Expected1Found0:
        raise RuntimeError(f"Tree {tree} does not contain a .gitmodules file, {repo=}, {branch=}, tree={tree.sha}, tree elems {elems}")
    gitmodules_sha = gitmodules_elem.sha
    gitmodules_blob = repo.get_git_blob(gitmodules_sha)
    if gitmodules_blob.encoding != 'base64':
        raise RuntimeError(f'Blob {gitmodules_blob} (SHA {gitmodules_sha}) is not base64 encoded: {gitmodules_blob.encoding}')
    gitmodules_bytes = b64decode(gitmodules_blob.content.rstrip('\n'))
    gitmodules_content = gitmodules_bytes.decode()
    cp = ConfigParser()
    cp.read_string(gitmodules_content)
    sections = cp.sections()
    for section in sections:
        items = dict(cp.items(section))
        path = items['path']
        url = items['url']
        if path not in submodules:
            raise RuntimeError(f".gitmodules contains path {path} not present in tree {tree}")
        submodule = submodules[path]
        submodule['url'] = url
        name_with_owner = github.parse_url(url)
        submodule['name_with_owner'] = name_with_owner

    def get_new_sha_elem(submodule):
        path = submodule['path']
        name_with_owner = submodule['name_with_owner']
        ref = refs[path]
        repo = gh.get_repo(name_with_owner)
        submodule_commit = repo.get_commit(ref)
        new_sha = submodule_commit.sha
        return { **submodule, 'new_sha': new_sha }

    submodules = parallel(submodules.values(), get_new_sha_elem, n_jobs=num_jobs)
    update_submodules = {
        submodule['path']: submodule
        for submodule in submodules
        if submodule['sha'] != submodule['new_sha']
    }

    if update_submodules:
        new_elems = [
            InputGitTreeElement(path=submodule['path'], mode='160000', type='commit', sha=submodule['new_sha'])
            for submodule in update_submodules.values()
        ]

        if message_files and messages:
            raise ValueError("Pass `-F` xor `-m/--message`")
        elif message_files:
            messages = []
            for message_file in message_files:
                if message_file == '-':
                    message = stdin.read()
                else:
                    with open(message_file) as f:
                        message = f.read()
                messages.append(message)
        elif not messages:
            subject = f'Update submodules: {", ".join(update_submodules)}'
            body = '\n- '.join([
                '', *[
                    f'{path}: {sm["sha"][:SHORT_SHA_LEN]} → {sm["new_sha"][:SHORT_SHA_LEN]}'
                    for path, sm in update_submodules.items()
                ]
            ])
            default_msg = f'{subject}\n{body}'
            messages = [ default_msg ]

        message = '\n\n'.join(messages)
        err('Preparing commit:\n\n' + message + '\n')
        new_tree = repo.create_git_tree(new_elems, base_tree=tree)
        err(f'New tree: {new_tree.sha}')
        new_commit = repo.create_git_commit(message=message, tree=new_tree, parents=[commit.commit])
        err(f'New commit: {new_commit.sha}')
        branch_update_url = f'https://api.github.com/repos/{repository}/git/refs/heads/{branch}'
        response = patch(
            branch_update_url,
            json={'sha': new_commit.sha},
            headers=dict(Authorization=f'Bearer {token}'),
        )
        response.raise_for_status()
        err(f'Updated branch {branch} to {new_commit.sha}')

        if github_output is None:
            github_output = environ.get(GITHUB_OUTPUT)

        if github_output:
            output = f'commit={new_commit.sha}'
            if github_output == '-':
                print(output)
            else:
                with open(github_output, 'w') as f:
                    f.write(output + '\n')

        if github_step_summary is None:
            github_step_summary = environ.get(GITHUB_STEP_SUMMARY)

        if github_step_summary:
            subject = f'Pushed submodule update ([`{new_commit.sha[:SHORT_SHA_LEN]}`](https://github.com/{repository}/commit/{new_commit.sha}))'
            bullet_strs = []
            for path, submodule in update_submodules.items():
                submodule_name_with_owner = submodule['name_with_owner']
                cur_sha = submodule["sha"][:SHORT_SHA_LEN]
                new_sha = submodule["new_sha"][:SHORT_SHA_LEN]
                submodule_base_url = f'https://github.com/{submodule_name_with_owner}'
                bullet_str = f'- [{path}]({submodule_base_url}): [`{cur_sha}..{new_sha}`]({submodule_base_url}/compare/{cur_sha}..{new_sha})'
                bullet_strs.append(bullet_str)

            bullets_str = "\n".join(bullet_strs)
            md = f'{subject}:\n\n{bullets_str}'
            if github_step_summary == '-':
                print(md)
            else:
                with open(github_step_summary, 'a') as f:
                    f.write(md)


if __name__ == '__main__':
    main()

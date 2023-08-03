from setuptools import setup, find_packages

setup(
    name="guss",
    version="0.0.1",
    install_requires=open("requirements.txt").read(),
    packages=find_packages(),
    entry_points={
        'console_scripts': [
            'git-update-meta-branch=guss.git_update_meta_branch:main',
            'git-update-submodules=guss.git_update_meta_branch:main',
            'github-update-submodules=guss.github_update_submodules:main',
        ]
    }
)

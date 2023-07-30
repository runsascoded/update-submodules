from setuptools import setup

setup(
    name="gumb",
    version="0.0.1",
    install_requires=open("requirements.txt").read(),
    entry_points={
        'console_scripts': [
            'git-update-meta-branch=gumb.git_update_meta_branch:main',
            'git-update-submodules=gumb.git_update_meta_branch:main',
        ]
    }
)

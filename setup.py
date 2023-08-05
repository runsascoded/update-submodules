from setuptools import setup, find_packages

setup(
    name="update-submodules",
    version="0.0.2",
    install_requires=open("requirements.txt").read(),
    packages=find_packages(),
    entry_points={
        'console_scripts': [
            'git-update-meta-branch=update_submodules.meta_branch:main',
            'git-update-submodules=update_submodules.git:main',
            'github-update-submodules=update_submodules.github:main',
        ]
    }
)

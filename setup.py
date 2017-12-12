#!/usr/bin/env python

from distutils.core import setup
from setuptools import find_packages

setup(name="fran2",
      version='1.0',
      description="-",
      author="Jiranun Jiratrakanvong",
      author_email="jjiratra@hawk.iit.edu",
      url="",
      packages=find_packages(),
      entry_points={
        'console_scripts': [
            'fran2=bm_scan.main:main',
        ],
      },
     )
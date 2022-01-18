#!/usr/bin/env python

from distutils.core import setup
from setuptools import find_packages

setup(name="mxmap",
      version='1.1.0',
      description="-",
      author="Jesse Hopkins, Jiranun Jiratrakanvong",
      author_email="jjiratra@hawk.iit.edu",
      url="",
      packages=find_packages(),
      entry_points={
        'console_scripts': [
            'mxmap=mxmap.main:main',
        ],
      },
      data_files=[('/etc', ['mxmap/gui/mxmap_config.ini']),
                  ('mxmap/gui/', ['mxmap/gui/mxmap_config.ini'])]
     )

#!/usr/bin/env python
# -*- coding: utf-8 -*-

import os
import sys


try:
    from setuptools import setup
except ImportError:
    from distutils.core import setup

if sys.argv[-1] == 'publish':
    os.system('python setup.py sdist upload')
    sys.exit()

readme = open('README.rst').read()
history = open('HISTORY.rst').read().replace('.. :changelog:', '')

setup(
    name='pygazebo',
    version='2.2.1-2014.1',
    description='Python bindings for the Gazebo multi-robot simulator.',
    long_description=readme + '\n\n' + history,
    author='Josh Pieper',
    author_email='jjp@pobox.com',
    url='https://github.com/jpieper/pygazebo',
    packages=[
        'pygazebo',
    ],
    package_dir={'pygazebo': 'pygazebo'},
    include_package_data=True,
    install_requires=[
    ],
    license="Apache License 2.0",
    zip_safe=False,
    keywords='pygazebo',
    classifiers=[
        'Development Status :: 2 - Pre-Alpha',
        'Intended Audience :: Developers',
        'License :: OSI Approved :: Apache Software License',
        'Natural Language :: English',
        'Programming Language :: Python :: 2',
        'Programming Language :: Python :: 2.6',
        'Programming Language :: Python :: 2.7',
        'Programming Language :: Python :: 3',
        'Programming Language :: Python :: 3.3',
        'Topic :: Software Development :: Libraries :: Python Modules',
        'Topic :: Scientific/Engineering',
    ],
    test_suite='tests',
)

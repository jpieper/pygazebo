#!/usr/bin/env python
# -*- coding: utf-8 -*-

import os
import sys


from setuptools import setup
from setuptools.command.test import test as TestCommand

if sys.argv[-1] == 'publish':
    os.system('python setup.py sdist upload')
    sys.exit()

readme = open('README.rst').read()
history = open('HISTORY.rst').read().replace('.. :changelog:', '')

class PyTest(TestCommand):
    def finalize_options(self):
        TestCommand.finalize_options(self)
        self.test_args = []
        self.test_suite = True

    def run_tests(self):
        import pytest
        errcode = pytest.main(self.test_args)
        sys.exit(errcode)

setup(
    name='pygazebo',
    version='3.0.0-2014.1',
    description='Python bindings for the Gazebo multi-robot simulator.',
    long_description=readme + '\n\n' + history,
    author='Josh Pieper',
    author_email='jjp@pobox.com',
    url='https://github.com/jpieper/pygazebo',
    packages=[
        'pygazebo',
        'pygazebo.msg',
    ],
    package_dir={'pygazebo': 'pygazebo'},
    include_package_data=True,
    install_requires=['protobuf', 'trollius'],
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
        'Topic :: Software Development :: Libraries :: Python Modules',
        'Topic :: Scientific/Engineering',
    ],
    tests_require=['pytest', 'mock'],
    extras_require={
        'testing': ['pytest', 'mock'],
        },
    cmdclass={'test': PyTest},
    test_suite='tests',
)

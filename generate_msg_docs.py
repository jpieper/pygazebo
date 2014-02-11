#!/usr/bin/python

import importlib
import inspect
import pkgutil

import pygazebo
import pygazebo.msg

print 'pygazebo.msg package'
print '===================='
print

for _, name, _ in pkgutil.iter_modules(['pygazebo/msg']):
    package_name = 'pygazebo.msg.' + name
    this_module = importlib.import_module(package_name)
    for class_name, _ in  inspect.getmembers(this_module, inspect.isclass):
        print class_name
        print '-' * len(class_name)
        print
        print '.. autoclass:: %s.%s' % (package_name, class_name)
        print

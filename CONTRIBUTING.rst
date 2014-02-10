============
Contributing
============

Report and Fix Bugs
-------------------

Report or look for bugs to fix at https://github.com/jpieper/pygazebo/issues

Get Started!
------------

Ready to contribute? Here's how to set up `pygazebo` for local development.

1. Fork the `pygazebo` repo on GitHub.
2. Clone your fork locally::

    $ git clone git@github.com:your_name_here/pygazebo.git

3. Install your local copy into a virtualenv. Assuming you have virtualenvwrapper installed, this is how you set up your fork for local development::

    $ mkvirtualenv pygazebo
    $ cd pygazebo/
    $ python setup.py develop

4. Create a branch for local development::

    $ git checkout -b name-of-your-bugfix-or-feature
   
   Now you can make your changes locally.

5. When you're done making changes, check that your changes pass flake8 and the tests, including testing other Python versions with tox::

    $ flake8 pygazebo tests
    $ python setup.py test
    $ tox

   To get flake8 and tox, just pip install them into your virtualenv. 

6. Commit your changes and push your branch to GitHub::

    $ git add .
    $ git commit -m "Your detailed description of your changes."
    $ git push origin name-of-your-bugfix-or-feature

7. Submit a pull request through the GitHub website.

Pull Request Guidelines
-----------------------

Before you submit a pull request, check that it meets these guidelines:

1. The pull request should include tests.
2. If the pull request adds functionality, the docs should be updated. Put
   your new functionality into a function with a docstring, and update
   the reference documentation accordingly.
3. The pull request should work for Python 2.7. Check 
   https://travis-ci.org/jpieper/pygazebo/pull_requests
   and make sure that the tests pass for all supported Python versions.

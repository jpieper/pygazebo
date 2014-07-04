.PHONY: clean-pyc clean-build docs

help:
	@echo "update-gazebo - update message definitions from Gazebo"
	@echo "clean-build - remove build artifacts"
	@echo "clean-pyc - remove Python file artifacts"
	@echo "lint - check style with flake8"
	@echo "test - run tests quickly with the default Python"
	@echo "testall - run tests on every Python version with tox"
	@echo "coverage - check code coverage quickly with the default Python"
	@echo "docs - generate Sphinx HTML documentation, including API docs"
	@echo "release - package and upload a release"
	@echo "sdist - package"

# Locate Gazebo header installation directory.
GAZEBO_INCLUDE_DIR := \
  ${shell pkg-config gazebo --cflags 2>/dev/null | \
    perl -ne '/-I(\S*gazebo\S*).*$$/ and print $$1'}

update-gazebo:
	if [ 'z${GAZEBO_INCLUDE_DIR}' = 'z' ]; then \
    echo "Gazebo must be installed to update message definitions"; \
    exit 1; \
  fi
	rm -rf pygazebo/msg/*
	for definition in \
	  $$(find ${GAZEBO_INCLUDE_DIR}/gazebo/msgs/proto -name '*.proto'); \
      do protoc -I ${GAZEBO_INCLUDE_DIR}/gazebo/msgs/proto \
                --python_out=pygazebo/msg $$definition; \
	  done

clean: clean-build clean-pyc

clean-build:
	rm -fr build/
	rm -fr dist/
	rm -fr *.egg-info

clean-pyc:
	find . -name '*.pyc' -exec rm -f {} +
	find . -name '*.pyo' -exec rm -f {} +
	find . -name '*~' -exec rm -f {} +

lint:
	flake8 pygazebo tests --exclude msg

test:
	python setup.py test

test-all:
	tox

coverage:
	coverage run --source pygazebo setup.py test
	coverage report -m
	coverage html
	open htmlcov/index.html

docs:
	python generate_msg_docs.py > docs/pygazebo.msg.rst
	sphinx-apidoc -o docs/ pygazebo
	$(MAKE) -C docs clean
	$(MAKE) -C docs html
	xdg-open docs/_build/html/index.html

release: clean
	python setup.py sdist upload

sdist: clean
	python setup.py sdist
	ls -l dist
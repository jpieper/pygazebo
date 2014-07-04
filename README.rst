========
pygazebo
========

.. image:: https://travis-ci.org/jpieper/pygazebo.png?branch=develop
        :target: https://travis-ci.org/jpieper/pygazebo

.. image:: https://pypip.in/d/pygazebo/badge.png
        :target: https://crate.io/packages/pygazebo?version=latest

.. image:: https://coveralls.io/repos/jpieper/pygazebo/badge.png?branch=develop
       :target: https://coveralls.io/r/jpieper/pygazebo?branch=develop

pygazebo provides python bindings for the Gazebo
(http://gazebosim.org) multi-robot simulator.

* GitHub: https://github.com/jpieper/pygazebo
* Free software: Apache 2.0 License
* Documentation: http://pygazebo.rtfd.org.

Features
--------

* Supports publishing and subscribing to any Gazebo topics using a
  straightforward python API.
* Python versions of all defined Gazebo protobuf messages are
  included.
* Based on asyncio/trollius for flexible concurrency support.

Simple Usage
------------

The following example shows how easy it is to publish a message
repeatedly to control a single joint in a Gazebo model running on the
local machine on the default port.

.. code-block:: python
  
  import trollius
  from trollius import From
  
  import pygazebo
  import pygazebo.msg.joint_cmd_pb2
  
  @trollius.coroutine
  def publish_loop():
      manager = yield From(pygazebo.connect())
      
      publisher = yield From(
          manager.advertise('/gazebo/default/model/joint_cmd',
                            'gazebo.msgs.JointCmd'))
  
      message = pygazebo.msg.joint_cmd_pb2.JointCmd()
      message.axis = 0
      message.force = 1.0

      while True:
          yield From(publisher.publish(message))
          yield From(trollius.sleep(1.0))
  
  loop = trollius.get_event_loop()
  loop.run_until_complete(publish_loop())

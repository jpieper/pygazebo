========
Usage
========

To use pygazebo in a project, first import, then instantiate a
Manager::

  from trollius import From
  import pygazebo
  
  manager = yield From(pygazebo.connect(('localhost', 11345)))

Then, individual topics can be published or subscribed using the
`advertise` or `subscribe` methods.

To publish::

  publisher = yield From(
      manager.advertise('/gazebo/default/topic',
                        'gazebo.msgs.GzString'))
  yield From(publisher.publish(
      pygazebo.msg.gz_string_pb2.GzString(data='hello')))

And to subscribe::

  def callback(data):
      message = pygazebo.msg.gz_string_pb2.GzString.FromString(data)
      print('Received message:', message.data)
      
  manager.subscribe('/gazebo/default/topic',
                    'gazebo.msgs.GzString',
                    callback)

The library is built with trollius.  No external methods are
coroutines (they only return Futures) and can thus operate in any
application which is already using a trollius or asyncio event loop
even if coroutines are not used.

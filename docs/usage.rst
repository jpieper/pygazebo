========
Usage
========

To use pygazebo in a project, first import, then instantiate a
Manager::

  import pygazebo
  
  manager = pygazebo.Manager(('localhost', 11345))

Then, individual topics can be published or subscribed using the
`advertise` or `subscribe` methods.

To publish::

  publisher = manager.advertise('/gazebo/default/topic',
                                'gazebo.msgs.GzString')
  publisher.publish(pygazebo.msg.gz_string_pb2.GzString(data='hello'))

And to subscribe::

  def callback(data):
      message = pygazebo.msg.gz_string_pb2.GzString.FromString(data)
      print('Received message:', message.data)
      
  manager.subscribe('/gazebo/default/topic',
                    'gazebo.msgs.GzString',
                    callback)

The library is built with eventlet.  Using its facilities, all
external APIs are designed to be non-blocking.  To integrate pygazebo
into another application, you can either use a standard eventlet hub
integration strategy, or just poll `eventlet.sleep()` occasionally.

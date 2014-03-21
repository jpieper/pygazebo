#!/usr/bin/env python
# -*- coding: utf-8 -*-

import eventlet
import logging
import math
import time

import msg
import msg.gz_string_pb2
import msg.gz_string_v_pb2
import msg.packet_pb2
import msg.publishers_pb2
import msg.subscribe_pb2

logger = logging.getLogger(__name__)


class ParseError(RuntimeError):
    pass


class Publisher(object):
    """Publishes data to the Gazebo publish-subscribe bus.

    :ivar topic: (string) the topic name this publisher is using
    :ivar msg_type: (string) the Gazebo message type
    """
    def __init__(self):
        """:class:`Publisher` should not be directly created"""
        self.topic = None
        self.msg_type = None
        self._listeners = []
        self._first_listener_ready = eventlet.event.Event()

    def publish(self, msg):
        """Publish a new instance of this data.

        :param msg: the message to publish
        :type msg: :class:`google.protobuf.Message` instance
        """
        self._publish_impl(msg)

    def wait_for_listener(self):
        """Block (using eventlet) until at least one listener exists."""
        self._first_listener_ready.wait()

    def remove(self):
        """Stop advertising this topic.

        Note: Once :func:`remove` is called, no further methods should
        be called.
        """
        raise NotImplementedError()

    def _publish_impl(self, message):
        # Try writing to each of our listeners.  If any give an error,
        # disconnect them.
        to_remove = []
        for connection in self._listeners:
            try:
                connection.write(message)
            except:
                # TODO jpieper: We should probably catch only a subset
                # of exceptions here.

                # Assume that the remote end closed.
                connection.socket.close()
                to_remove.append(connection)

        for x in to_remove:
            self._listeners.remove(x)

    def _connect(self, connection):
        self._listeners.append(connection)
        if not self._first_listener_ready.ready():
            self._first_listener_ready.send()


class Subscriber(object):
    """Receives data from the Gazebo publish-subscribe bus.

    :ivar topic: (str) The topic name this subscriber is listening for.
    :ivar msg_type: (str) The Gazebo message type.
    :ivar callback: (function) The current function to invoke.
    """
    def __init__(self, local_host, local_port):
        """:class:`Subscriber` should not be directly created"""
        logger.debug('Subscriber.__init__', local_host, local_port)
        self.topic = None
        self.msg_type = None
        self.callback = None

        self._local_host = local_host
        self._local_port = local_port
        self._connections = []

    def remove(self):
        """Stop listening for this topic.

        Note: Once :func:`remove` is called, the callback will no
        longer be invoked.
        """
        raise NotImplementedError()

    def _start_connect(self, pub):
        # Do the actual work in an eventlet infinite loop.
        eventlet.spawn_n(self._connect, pub)

    def _connect(self, pub):
        connection = _Connection()

        # Connect to the remote provider.
        connection.connect((pub.host, pub.port))
        self._connections.append(connection)

        # Send the initial message, which is encapsulated inside of a
        # Packet structure.
        to_send = msg.subscribe_pb2.Subscribe()
        to_send.topic = pub.topic
        to_send.host = self._local_host
        to_send.port = self._local_port
        to_send.msg_type = pub.msg_type
        to_send.latching = False

        connection.write_packet('sub', to_send)

        # Now wait forever reading messages.  For some reason, the
        # received data is not encapsulated in an outer packet.
        while True:
            data = connection.read_raw()
            if data is None:
                self._connections.remove(connection)
                return
            self.callback(data)


class _Connection(object):
    """Manages a Gazebo protocol connection.

    This can connect to either the Gazebo server, or to a data
    publisher.  Additionally, it can act as the TCP client, or as a
    server.  In either case, it provides methods to read and write
    structured data on the socket.
    """

    # Do all raw socket reads and writes in amounts no larger than
    # this.
    BUF_SIZE = 16384

    def __init__(self):
        self.address = None
        self.socket = None
        self._local_host = None
        self._local_port = None
        self._socket_ready = eventlet.event.Event()
        self._local_ready = eventlet.event.Event()

    def connect(self, address):
        logger.debug('Connection.connect')
        self.address = address
        self.socket = eventlet.connect(self.address)
        self._socket_ready.send(True)

    def serve(self, callback):
        self.socket = eventlet.listen(('', 0))
        self._local_host, self._local_port = self.socket.getsockname()
        self._local_ready.send(True)
        eventlet.serve(self.socket, callback)

    def read_raw(self):
        logger.debug('Connection.read_raw')
        header = self.socket.recv(8)
        if len(header) < 8:
            return None

        try:
            size = int(header, 16)
        except ValueError:
            raise ParseError('invalid header: ' + header)

        data = ''

        # Read in BUF_SIZE increments.
        while len(data) < size:
            this_size = min(size - len(data), self.BUF_SIZE)
            this_data = self.socket.recv(this_size)
            if len(this_data) == 0:
                return None
            data += this_data

        return data

    def read(self):
        logger.debug('Connection.read')
        data = self.read_raw()
        if data is None:
            return None
        packet = msg.packet_pb2.Packet.FromString(data)
        return packet

    def send_pieces(self, data):
        start = 0
        while start < len(data):
            self.socket.send(data[start:start + self.BUF_SIZE])
            start += self.BUF_SIZE

    def write(self, message):
        self._socket_ready.wait()

        data = message.SerializeToString()

        header = '%08X' % len(data)
        self.send_pieces(header + data)

    def write_packet(self, name, message):
        packet = msg.packet_pb2.Packet()
        cur_time = time.time()
        packet.stamp.sec = int(cur_time)
        packet.stamp.nsec = int(math.fmod(cur_time, 1) * 1e9)
        packet.type = name
        packet.serialized_data = message.SerializeToString()
        self.write(packet)

    @property
    def local_host(self):
        self._local_ready.wait()
        return self._local_host

    @property
    def local_port(self):
        self._local_ready.wait()
        return self._local_port


class _PublisherRecord(object):
    """Information about a remote topic.

    :ivar topic: (str) the string description of the topic
    :ivar msg_type: (str) the Gazebo message type string
    :ivar host: (str) the remote host of the topic publisher
    :ivar port: (int) the remote port of the topic publisher
    """
    def __init__(self, msg):
        self.topic = msg.topic
        self.msg_type = msg.msg_type
        self.host = msg.host
        self.port = msg.port


class Manager(object):
    """Create a connection to the Gazebo server.

    The Manager instance creates a connection to the Gazebo server,
    then allows the client to either advertise topics for publication,
    or to listen to other publishers.

    :param address: destination TCP server
    :type address: a tuple of ('host', port)
    """
    def __init__(self, address=('localhost', 11345)):
        self._address = address
        self._master = _Connection()
        self._server = _Connection()
        self._namespaces = []
        self._publisher_records = set()
        self._publishers = {}
        self._subscribers = {}

        self._client_thread = eventlet.spawn(self._run)

    def advertise(self, topic_name, msg_type):
        """Inform the Gazebo server of a topic we will publish.

        :param topic_name: the topic to send data on
        :type topic_name: string
        :param msg_type: the Gazebo message type string
        :type msg_type: string
        :rtype: :class:`Publisher`
        """
        if topic_name in self._publishers:
            raise RuntimeError('multiple publishers for: ' + topic_name)

        to_send = msg.publish_pb2.Publish()
        to_send.topic = topic_name
        to_send.msg_type = msg_type
        to_send.host = self._server.local_host
        to_send.port = self._server.local_port

        self._master.write_packet('advertise', to_send)
        result = Publisher()
        result.topic = topic_name
        result.msg_type = msg_type
        self._publishers[topic_name] = result

        return result

    def subscribe(self, topic_name, msg_type, callback):
        """Request the Gazebo server send messages on a specific topic.

        :param topic_name: the topic for which data will be sent
        :type topic_name: string
        :param msg_type: the Gazebo message type string
        :type msg_type: string
        :param callback: A callback to invoke when new data on
              this topic is received.  The callback will be invoked
              with raw binary data.  It is expected to deserialize the
              message using the appropriate protobuf definition.
        :rtype: :class:`Subscriber`
        """

        if topic_name in self._subscribers:
            raise RuntimeError('multiple subscribers for: ' + topic_name)

        to_send = msg.subscribe_pb2.Subscribe()
        to_send.topic = topic_name
        to_send.msg_type = msg_type
        to_send.host = self._server.local_host
        to_send.port = self._server.local_port
        to_send.latching = False

        self._master.write_packet('subscribe', to_send)

        result = Subscriber(local_host=to_send.host,
                            local_port=to_send.port)
        result.topic = topic_name
        result.msg_type = msg_type
        result.callback = callback
        self._subscribers[topic_name] = result
        return result

    def publications(self):
        """Enumerate the current list of publications.

        :returns: the currently known publications
        :rtype: list of (topic_name, msg_type)
        """
        return [(x.topic, x.msg_type) for x in self._publisher_records]

    def namespaces(self):
        """Enumerate the currently known namespaces.

        :returns: the currently known namespaces
        :rtype: list of strings
        """
        return self._namespaces

    def _run(self):
        """Starts the connection and processes events."""
        logger.debug('Manager.run')
        self._master.connect(self._address)
        self._server_thread = eventlet.spawn(
            self._server.serve, self._handle_server_connection)

        # Read and process the required three initialization packets.
        initData = self._master.read()
        if initData.type != 'version_init':
            raise ParseError('unexpected initialization packet: ' +
                             initData.type)
        self._handle_version_init(
            msg.gz_string_pb2.GzString.FromString(initData.serialized_data))

        namespacesData = self._master.read()

        # NOTE: This type string is mis-spelled in the official client
        # and server as of 2.2.1.  Presumably they'll just leave it be
        # to preserve network compatibility.
        if namespacesData.type != 'topic_namepaces_init':
            raise ParseError('unexpected namespaces init packet: ' +
                             namespacesData.type)
        self._handle_topic_namespaces_init(
            msg.gz_string_v_pb2.GzString_V.FromString(
                namespacesData.serialized_data))

        publishersData = self._master.read()
        if publishersData.type != 'publishers_init':
            raise ParseError('unexpected publishers init packet: ' +
                             publishersData.type)
        self._handle_publishers_init(
            msg.publishers_pb2.Publishers.FromString(
                publishersData.serialized_data))

        logger.debug('Connection: initialized!')
        self._initialized = True

        # Enter the normal message dispatch loop.
        while True:
            data = self._master.read()
            if data is None:
                return

            self._process_message(data)

    def _handle_server_connection(self, socket, remote_address):
        this_connection = _Connection()
        this_connection.socket = socket
        this_connection._socket_ready.send(True)

        while True:
            message = this_connection.read()
            if message is None:
                return
            if message.type == 'sub':
                self._handle_server_sub(
                    this_connection,
                    msg.subscribe_pb2.Subscribe.FromString(
                        message.serialized_data))
            else:
                logger.warn('Manager.handle_server_connection unknown msg:',
                            msg.type)

    def _handle_server_sub(self, this_connection, msg):
        if not msg.topic in self._publishers:
            logger.warn('Manager.handle_server_sub unknown topic:', msg.topic)
            return

        publisher = self._publishers[msg.topic]
        if publisher.msg_type != msg.msg_type:
            logger.error(('Manager.handle_server_sub type mismatch ' +
                          'requested=%d publishing=%s') % (
                publisher.msg_type, msg.msg_type))
            return

        publisher._connect(this_connection)

    def _process_message(self, packet):
        logger.debug('Manager.process_message', packet)
        if packet.type in Manager._MSG_HANDLERS:
            handler, packet_type = Manager._MSG_HANDLERS[packet.type]
            handler(self, packet_type.FromString(packet.serialized_data))
        else:
            logger.warn('unhandled message type: ' + packet.type)

    def _handle_version_init(self, msg):
        logger.debug('Manager.handle_version_init' + msg.data)
        version = float(msg.data.split(' ')[1])
        if version < 2.2:
            raise ParseError('Unsupported gazebo version: ' + msg.data)

    def _handle_topic_namespaces_init(self, msg):
        self._namespaces = msg.data
        logger.debug('Manager.handle_topic_namespaces_init', self._namespaces)

    def _handle_publishers_init(self, msg):
        logger.debug('Manager.handle_publishers_init')
        for publisher in msg.publisher:
            self._publisher_records.add(_PublisherRecord(publisher))
            logger.debug('  %s - %s %s:%d' % (
                publisher.topic, publisher.msg_type,
                publisher.host, publisher.port))

    def _handle_publisher_add(self, msg):
        logger.debug('Manager.handle_publisher_add: %s - %s %s:%d' % (
            msg.topic, msg.msg_type, msg.host, msg.port))
        self._publisher_records.add(_PublisherRecord(msg))

    def _handle_publisher_del(self, msg):
        logger.debug('Manager.handle_publisher_del', msg.topic)
        try:
            self._publisher_records.remove(_PublisherRecord(msg))
        except KeyError:
            logger.debug('got publisher_del for unknown: ' + msg.topic)

    def _handle_namespace_add(self, msg):
        logger.debug('Manager.handle_namespace_add', msg.data)
        self._namespaces.append(msg.data)

    def _handle_publisher_subscribe(self, msg):
        logger.debug('Manager.handle_publisher_subscribe', msg.topic)
        logger.debug(' our info: ',
                     self._server.local_host, self._server.local_port)
        if not msg.topic in self._subscribers:
            logger.debug('no subscribers!')
            return

        # Check to see if this is ourselves... if so, then don't do
        # anything about it.
        if ((msg.host == self._server.local_host and
             msg.port == self._server.local_port)):
            logger.debug('got publisher_subscribe for ourselves')
            return

        logger.debug('creating subscriber for:', msg.topic, msg.host, msg.port)

        subscriber = self._subscribers[msg.topic]
        subscriber._start_connect(msg)

    def _handle_unsubscribe(self, msg):
        pass

    def _handle_unadvertise(self, msg):
        pass

    _MSG_HANDLERS = {
        'publisher_add': (_handle_publisher_add, msg.publish_pb2.Publish),
        'publisher_del': (_handle_publisher_del, msg.publish_pb2.Publish),
        'namespace_add': (_handle_namespace_add, msg.gz_string_pb2.GzString),
        'publisher_subscribe': (_handle_publisher_subscribe,
                                msg.publish_pb2.Publish),
        'publisher_advertise': (_handle_publisher_subscribe,
                                msg.publish_pb2.Publish),
        'unsubscribe': (_handle_unsubscribe, msg.subscribe_pb2.Subscribe),
        'unadvertise': (_handle_unadvertise, msg.publish_pb2.Publish),
        }

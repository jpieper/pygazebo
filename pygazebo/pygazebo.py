#!/usr/bin/env python
# -*- coding: utf-8 -*-

try:
    import asyncio
except ImportError:
    import trollius as asyncio

import logging
import math
import socket
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


class Event(object):
    """This class provides nearly identical functionality to
    asyncio.Event, but does not require coroutines."""
    def __init__(self):
        self.futures = []
        self.clear()

    def clear(self):
        self._set = False

    def is_set(self):
        return self._set

    def wait(self):
        result = asyncio.Future()
        if self._set:
            result.set_result(None)
        else:
            self.futures.append(result)
        return result

    def set(self):
        self._set = True
        for future in self.futures:
            future.set_result(None)
        self.futures = []


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
        self._first_listener_ready = Event()

    def publish(self, msg):
        """Publish a new instance of this data.

        :param msg: the message to publish
        :type msg: :class:`google.protobuf.Message` instance
        :returns: a future which completes when the data has been written
        """
        return self._publish_impl(msg)

    def wait_for_listener(self):
        """Return a Future which is complete when at least one listener is
        present."""
        return self._first_listener_ready.wait()

    def remove(self):
        """Stop advertising this topic.

        Note: Once :func:`remove` is called, no further methods should
        be called.
        """
        raise NotImplementedError()

    class WriteFuture(asyncio.Future):
        def __init__(self, publisher, connections):
            super(Publisher.WriteFuture, self).__init__(self)

            self.publisher = publisher
            self.connections = dict((x, True) for x in connections)

        def handle_done(self, connection):
            del self.connections[connection]

            # if (future.exception() is not None and
            #     connection in self.publisher._listeners):
            #     self.publisher._listeners.remove(connection)

            if len(self.connections) == 0:
                self.set_result(None)

    def _publish_impl(self, message):
        result = Publisher.WriteFuture(self, self._listeners[:])

        # Try writing to each of our listeners.  If any give an error,
        # disconnect them.
        for connection in self._listeners:
            connection.write(
                message, lambda: result.handle_done(connection))

        return result

    def _connect(self, connection):
        self._listeners.append(connection)
        self._first_listener_ready.set()


class Subscriber(object):
    """Receives data from the Gazebo publish-subscribe bus.

    :ivar topic: (str) The topic name this subscriber is listening for.
    :ivar msg_type: (str) The Gazebo message type.
    :ivar callback: (function) The current function to invoke.
    """
    def __init__(self, local_host, local_port):
        """:class:`Subscriber` should not be directly created"""
        logger.debug('Subscriber.__init__ %s %d', local_host, local_port)
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
        # Do the actual work in a new callback.
        asyncio.get_event_loop().call_soon(self._connect, pub)

    def _connect(self, pub):
        connection = _Connection()

        # Connect to the remote provider.
        connection.connect((pub.host, pub.port),
                           lambda: self._connect2(connection, pub))

    def _connect2(self, connection, pub):
        self._connections.append(connection)

        # Send the initial message, which is encapsulated inside of a
        # Packet structure.
        to_send = msg.subscribe_pb2.Subscribe()
        to_send.topic = pub.topic
        to_send.host = self._local_host
        to_send.port = self._local_port
        to_send.msg_type = pub.msg_type
        to_send.latching = False

        connection.write_packet('sub', to_send,
                                lambda: self._connect3(connection))

    def _connect3(self, connection):
        connection.read_raw(lambda data: self._handle_read(connection, data))

    def _handle_read(self, connection, data):
        if data is None:
            self._connections.remove(connection)
            return

        self.callback(data)
        self._connect3(connection)


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
        self._socket_ready = Event()
        self._local_ready = Event()

    def connect(self, address, callback):
        logger.debug('Connection.connect')
        self.address = address
        loop = asyncio.get_event_loop()
        self.socket = socket.socket()
        self.socket.setblocking(False)
        # TODO jpieper: Either assert that this is numeric, or have a
        # separate DNS resolution stage.
        future = asyncio.async(loop.sock_connect(self.socket, address))

        def callback_impl(future):
            self._socket_ready.set()
            callback(future.result())

        future.add_done_callback(callback_impl)

    def serve(self, callback):
        """Start listening for new connections.  Invoke callback every
        time a new connection is available."""

        self.socket = socket.socket()
        self.socket.bind(('', 0))
        self._local_host, self._local_port = self.socket.getsockname()
        self.socket.listen(5)
        self.socket.setblocking(False)
        self._local_ready.set()

        self.start_accept(callback)

    def start_accept(self, callback):
        loop = asyncio.get_event_loop()
        future = asyncio.async(loop.sock_accept(self.socket))
        future.add_done_callback(
            lambda future: self.handle_accept(callback, future))

    def handle_accept(self, callback, future):
        loop = asyncio.get_event_loop()
        loop.call_soon(lambda: self.start_accept(callback))

        conn, address = future.result()
        callback(conn, address)

    def read_raw(self, callback):
        logger.debug('Connection.read_raw')
        loop = asyncio.get_event_loop()
        future = asyncio.async(loop.sock_recv(self.socket, 8))
        future.add_done_callback(
            lambda future: self.handle_read_raw_header(future, callback))

    def handle_read_raw_header(self, future, callback):
        header = future.result()
        if len(header) < 8:
            callback(None)
            return

        try:
            size = int(header, 16)
        except ValueError:
            raise ParseError('invalid header: ' + header)

        self.start_read_data('', size, callback)

    def start_read_data(self, starting_data, total_size, callback):
        if len(starting_data) == total_size:
            callback(starting_data)
            return

        loop = asyncio.get_event_loop()
        future = asyncio.async(
            loop.sock_recv(self.socket,
                           min(total_size - len(starting_data),
                               self.BUF_SIZE)))
        future.add_done_callback(
            lambda future: self.handle_read_data(
                future, starting_data, total_size, callback))

    def handle_read_data(self, future, starting_data, total_size, callback):
        data = future.result()
        if len(data) == 0:
            callback(None)
            return

        data = starting_data + data
        self.start_read_data(data, total_size, callback)

    def read(self, callback):
        logger.debug('Connection.read')
        self.read_raw(lambda data: self.handle_read(data, callback))

    def handle_read(self, data, callback):
        if data is None:
            callback(None)
            return

        packet = msg.packet_pb2.Packet.FromString(data)
        callback(packet)

    def send_pieces(self, data, callback):
        if len(data) == 0:
            callback()
            return

        to_send = min(len(data), self.BUF_SIZE)
        this_send = data[:to_send]
        next_send = data[to_send:]

        loop = asyncio.get_event_loop()
        future = asyncio.async(loop.sock_sendall(self.socket, this_send))
        future.add_done_callback(
            lambda future: self.send_pieces(next_send, callback))

    def write(self, message, callback):
        future = self._socket_ready.wait()
        future.add_done_callback(
            lambda future: self.ready_write(future, message, callback))

    def ready_write(self, future, message, callback):
        future.result() # check for error
        data = message.SerializeToString()

        header = '%08X' % len(data)
        self.send_pieces(header + data, callback)

    def write_packet(self, name, message, callback):
        packet = msg.packet_pb2.Packet()
        cur_time = time.time()
        packet.stamp.sec = int(cur_time)
        packet.stamp.nsec = int(math.fmod(cur_time, 1) * 1e9)
        packet.type = name
        packet.serialized_data = message.SerializeToString()
        self.write(packet, callback)

    @property
    def local_host(self):
        assert self._local_ready.is_set()
        return self._local_host

    @property
    def local_port(self):
        assert self._local_ready.is_set()
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
    :param callback: callback to be invoked once the connection is complete
    """
    def __init__(self, address=('127.0.0.1', 11345), callback=None):
        self._address = address
        self._master = _Connection()
        self._server = _Connection()
        self._namespaces = []
        self._publisher_records = set()
        self._publishers = {}
        self._subscribers = {}

        loop = asyncio.get_event_loop()
        loop.call_soon(self._run, callback)

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

        self._master.write_packet('advertise', to_send, lambda: None)
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

        self._master.write_packet('subscribe', to_send, lambda: None)

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

    def _run(self, callback):
        """Starts the connection and processes events."""
        logger.debug('Manager.run')
        self._master.connect(
            self._address,
            lambda ignored: self.handle_connect(ignored, callback))

    def handle_connect(self, ignored, callback):
        logger.debug('Manager.handle_connect')
        self._server.serve(self._handle_server_connection)

        # Read and process the required three initialization packets.
        self._master.read(lambda data: self.handle_initdata(data, callback))

    def handle_initdata(self, initData, callback):
        logger.debug('Manager.handle_initdata')
        if initData.type != 'version_init':
            raise ParseError('unexpected initialization packet: ' +
                             initData.type)
        self._handle_version_init(
            msg.gz_string_pb2.GzString.FromString(initData.serialized_data))

        self._master.read(lambda data: self.handle_namespacesdata(
            data, callback))

    def handle_namespacesdata(self, namespacesData, callback):

        # NOTE: This type string is mis-spelled in the official client
        # and server as of 2.2.1.  Presumably they'll just leave it be
        # to preserve network compatibility.
        if namespacesData.type != 'topic_namepaces_init':
            raise ParseError('unexpected namespaces init packet: ' +
                             namespacesData.type)
        self._handle_topic_namespaces_init(
            msg.gz_string_v_pb2.GzString_V.FromString(
                namespacesData.serialized_data))

        self._master.read(lambda data: self.handle_publishersdata(
            data, callback))

    def handle_publishersdata(self, publishersData, callback):
        if publishersData.type != 'publishers_init':
            raise ParseError('unexpected publishers init packet: ' +
                             publishersData.type)
        self._handle_publishers_init(
            msg.publishers_pb2.Publishers.FromString(
                publishersData.serialized_data))

        logger.debug('Connection: initialized!')
        self._initialized = True

        if callback is not None:
            loop = asyncio.get_event_loop()
            loop.call_soon(callback)
        self.start_normal_read()

    def start_normal_read(self):
        # Enter the normal message dispatch loop.
        self._master.read(self.handle_normal_read)

    def handle_normal_read(self, data):
        if data is None:
            return

        self.start_normal_read()
        self._process_message(data)

    def _handle_server_connection(self, socket, remote_address):
        this_connection = _Connection()
        this_connection.socket = socket
        this_connection._socket_ready.set()

        self._read_server_data(this_connection)

    def _read_server_data(self, connection):
        connection.read(
            lambda data: self._handle_server_data(data, connection))

    def _handle_server_data(self, message, connection):
        if message is None:
            return
        if message.type == 'sub':
            self._handle_server_sub(
                connection,
                msg.subscribe_pb2.Subscribe.FromString(
                    message.serialized_data))
        else:
            logger.warn('Manager.handle_server_connection unknown msg:' +
                        str(msg.type))

        self._read_server_data(connection)

    def _handle_server_sub(self, this_connection, msg):
        if not msg.topic in self._publishers:
            logger.warn('Manager.handle_server_sub unknown topic:' + msg.topic)
            return

        publisher = self._publishers[msg.topic]
        if publisher.msg_type != msg.msg_type:
            logger.error(('Manager.handle_server_sub type mismatch ' +
                          'requested=%d publishing=%s') % (
                publisher.msg_type, msg.msg_type))
            return

        publisher._connect(this_connection)

    def _process_message(self, packet):
        logger.debug('Manager.process_message' + packet)
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
        logger.debug('Manager.handle_topic_namespaces_init: ' +
                     str(self._namespaces))

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
        logger.debug('Manager.handle_publisher_del:' + msg.topic)
        try:
            self._publisher_records.remove(_PublisherRecord(msg))
        except KeyError:
            logger.debug('got publisher_del for unknown: ' + msg.topic)

    def _handle_namespace_add(self, msg):
        logger.debug('Manager.handle_namespace_add:' + msg.data)
        self._namespaces.append(msg.data)

    def _handle_publisher_subscribe(self, msg):
        logger.debug('Manager.handle_publisher_subscribe:' + msg.topic)
        logger.debug(' our info: %s, %d',
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

        logger.debug('creating subscriber for: %s %s %d',
                     msg.topic, msg.host, msg.port)

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

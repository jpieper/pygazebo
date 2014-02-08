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
    def __init__(self):
        self.topic = None
        self.msg_type = None
        self._listeners = []
        self._first_listener_ready = eventlet.event.Event()

    def publish(self, msg):
        self._publish_impl(msg)

    def _publish_impl(self, message):
        to_remove = []
        for connection in self._listeners:
            try:
                connection.write(message)
            except:
                # Assume that the remote end closed.
                connection.socket.close()
                to_remove.append(connection)

        [self._listeners.remove(x) for x in to_remove]

    def _connect(self, connection):
        self._listeners.append(connection)
        if not self._first_listener_ready.ready():
            self._first_listener_ready.send()

    def wait_for_listener(self):
        self._first_listener_ready.wait()

    def remove(self):
        # TODO
        pass

class Subscriber(object):
    def __init__(self, local_host, local_port):
        logger.debug('Subscriber.__init__', local_host, local_port)
        self.topic = None
        self.msg_type = None
        self.callback = None
        self.local_host = local_host
        self.local_port = local_port
        self._connections = []

    def start_connect(self, pub):
        eventlet.spawn_n(self.connect, pub)

    def connect(self, pub):
        connection = Connection()

        connection.connect((pub.host, pub.port))
        self._connections.append(connection)

        to_send = msg.subscribe_pb2.Subscribe()
        to_send.topic = pub.topic
        to_send.host = self.local_host
        to_send.port = self.local_port
        to_send.msg_type = pub.msg_type
        to_send.latching = False

        connection.write_packet('sub', to_send)
        while True:
            data = connection.read_raw()
            if data is None:
                self._connections.remove(connection)
                return
            self.callback(data)

    def remove(self):
        # TODO
        pass

class Connection(object):
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

        data = self.socket.recv(size)
        if len(data) < size:
            return None
        return data

    def read(self):
        logger.debug('Connection.read')
        data = self.read_raw()
        if data is None:
            return None
        packet = msg.packet_pb2.Packet.FromString(data)
        return packet

    def write(self, message):
        self._socket_ready.wait()

        data = message.SerializeToString()

        header = '%08X' % len(data)
        self.socket.send(header + data)

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
    def __init__(self, msg):
        self.topic = msg.topic
        self.msg_type = msg.msg_type
        self.host = msg.host
        self.port = msg.port

class Manager(object):
    '''The Manager communicates with the gazebo server and provides
    the top level interface for communicating with it.'''

    def __init__(self, address):
        '''@param address - a tuple of (host, port)'''
        self.address = address
        self.master = Connection()
        self.server = Connection()
        self.namespaces = []
        self.publisher_records = set()
        self.publishers = {}
        self.subscribers = {}

        eventlet.spawn_n(self._run)

    def _run(self):
        '''Starts the connection and processes events.  It is expected
        to be invoked from an eventlet spawn, and will only return
        when the connection is closed or an error occurs.'''
        logger.debug('Manager.run')
        self.master.connect(self.address)
        eventlet.spawn_n(self.server.serve, self._handle_server_connection)

        # Read and process the required three initialization packets.
        initData = self.master.read()
        if initData.type != 'version_init':
            raise ParseError('unexpected initialization packet: ' +
                             initData.type)
        self._handle_version_init(
            msg.gz_string_pb2.GzString.FromString(initData.serialized_data))

        namespacesData = self.master.read()
        if namespacesData.type != 'topic_namepaces_init':
            raise ParseError('unexpected namespaces init packet: ' +
                             namespacesData.type)
        self._handle_topic_namespaces_init(
            msg.gz_string_v_pb2.GzString_V.FromString(
                namespacesData.serialized_data))

        publishersData = self.master.read()
        if publishersData.type != 'publishers_init':
            raise ParseError('unexpected publishers init packet: ' +
                             publishersData.type)
        self._handle_publishers_init(
            msg.publishers_pb2.Publishers.FromString(
                publishersData.serialized_data))

        logger.debug('Connection: initialized!')
        self.initialized = True

        # Enter the normal message dispatch loop.
        while True:
            data = self.master.read()
            if data is None:
                return

            self._process_message(data)

    def advertise(self, topic_name, msg_type):
        '''Prepare to publish a topic.

        @returns a Publisher instance'''
        if topic_name in self.publishers:
            raise RuntimeError('multiple publishers for: ' + topic_name)

        to_send = msg.publish_pb2.Publish()
        to_send.topic = topic_name
        to_send.msg_type = msg_type
        to_send.host = self.server.local_host
        to_send.port = self.server.local_port

        self.master.write_packet('advertise', to_send)
        result = Publisher()
        result.topic = topic_name
        result.msg_type = msg_type
        self.publishers[topic_name] = result

        return result

    def subscribe(self, topic_name, msg_type, callback):
        '''Request to receive updates on a topic.

        @returns a Subscriber instance'''

        if topic_name in self.subscribers:
            raise RuntimeError('multiple subscribers for: ' + topic_name)

        to_send = msg.subscribe_pb2.Subscribe()
        to_send.topic = topic_name
        to_send.msg_type = msg_type
        to_send.host = self.server.local_host
        to_send.port = self.server.local_port
        to_send.latching = False

        self.master.write_packet('subscribe', to_send)

        result = Subscriber(local_host=to_send.host,
                            local_port=to_send.port)
        result.topic = topic_name
        result.msg_type = msg_type
        result.callback = callback
        self.subscribers[topic_name] = result
        return result

    def _handle_server_connection(self, socket, remote_address):
        this_connection = Connection()
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
        if not msg.topic in self.publishers:
            logger.warn('Manager.handle_server_sub unknown topic:', msg.topic)
            return

        publisher = self.publishers[msg.topic]
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
        self.namespaces = msg.data
        logger.debug('Manager.handle_topic_namespaces_init', self.namespaces)

    def _handle_publishers_init(self, msg):
        logger.debug('Manager.handle_publishers_init')
        for publisher in msg.publisher:
            self.publisher_records.add(_PublisherRecord(publisher))
            logger.debug('  %s - %s %s:%d' % (
                    publisher.topic, publisher.msg_type,
                    publisher.host, publisher.port))

    def _handle_publisher_add(self, msg):
        logger.debug('Manager.handle_publisher_add: %s - %s %s:%d' % (
                msg.topic, msg.msg_type, msg.host, msg.port))
        self.publisher_records.add(_PublisherRecord(msg))

    def _handle_publisher_del(self, msg):
        logger.debug('Manager.handle_publisher_del', msg.topic)
        try:
            self.publisher_records.remove(_PublisherRecord(msg))
        except KeyError:
            logger.debug('got publisher_del for unknown: ' + msg.topic)

    def _handle_namespace_add(self, msg):
        logger.debug('Manager.handle_namespace_add', msg.data)
        self.namespaces.append(msg.data)

    def _handle_publisher_subscribe(self, msg):
        logger.debug('Manager.handle_publisher_subscribe', msg.topic)
        logger.debug(' our info: ',
                     self.server.local_host, self.server.local_port)
        if not msg.topic in self.subscribers:
            logger.debug('no subscribers!')
            return

        # Check to see if this is ourselves... if so, then don't do
        # anything about it.
        if (msg.host == self.server.local_host and
            msg.port == self.server.local_port):
            logger.debug('got publisher_subscribe for ourselves')
            return

        logger.debug('creating subscriber for:', msg.topic, msg.host, msg.port)

        subscriber = self.subscribers[msg.topic]
        subscriber.start_connect(msg)

    def _handle_unsubscribe(self, msg):
        pass

    def _handle_unadvertise(self, msg):
        pass

    _MSG_HANDLERS = {
        'publisher_add' : (_handle_publisher_add, msg.publish_pb2.Publish),
        'publisher_del' : (_handle_publisher_del, msg.publish_pb2.Publish),
        'namespace_add' : (_handle_namespace_add, msg.gz_string_pb2.GzString),
        'publisher_subscribe' : (_handle_publisher_subscribe,
                                 msg.publish_pb2.Publish),
        'publisher_advertise' : (_handle_publisher_subscribe,
                                 msg.publish_pb2.Publish),
        'unsubscribe' : (_handle_unsubscribe, msg.subscribe_pb2.Subscribe),
        'unadvertise' : (_handle_unadvertise, msg.publish_pb2.Publish),
        }

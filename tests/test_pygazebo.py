#!/usr/bin/env python
# -*- coding: utf-8 -*-

"""
test_pygazebo
----------------------------------

Tests for `pygazebo` module.
"""

# TODO:
#  * Test that connections close when write errors occur
#  * Do something that requires a subscriber to make a connection
#  * Error cases
#  * If no one is listening, that the publish future signals completion

try:
    import asyncio
except ImportError:
    import trollius as asyncio

import mock
import pytest
import socket

from pygazebo import pygazebo
from pygazebo.msg import gz_string_pb2
from pygazebo.msg import gz_string_v_pb2
from pygazebo.msg import packet_pb2
from pygazebo.msg import publishers_pb2
from pygazebo.msg import publish_pb2
from pygazebo.msg import subscribe_pb2


class PipeChannel(object):
    """One half of a simulated pipe, implemented using asyncio and
    callbacks.

    Attributes:
     other (PipeChannel): The opposite direction pair for this channel.
    """
    other = None

    def __init__(self):
        self.queue = asyncio.Queue(1)

    def send(self, data, callback):
        self.write(data, callback)

    def write(self, data, callback):
        assert len(data) <= 16384
        if len(data) == 0:
            callback()
            return

        future = asyncio.async(self.other.queue.put(data[0:1]))
        future.add_done_callback(lambda future: self.write(data[1:], callback))

    def write_frame(self, payload, callback):
        header = '%08X' % len(payload)

        data = header + payload

        self.write_frame_part(data, callback)

    def write_frame_part(self, data, callback):
        if len(data) == 0:
            callback()
            return

        self.write(data[0:1000],
                   lambda: self.write_frame_part(data[1000:], callback))

    def write_packet(self, name, message, callback):
        packet = packet_pb2.Packet()
        packet.stamp.sec = 0
        packet.stamp.nsec = 0
        packet.type = name
        packet.serialized_data = message.SerializeToString()
        self.write_frame(packet.SerializeToString(), callback)

    def recv(self, length, callback):
        assert length <= 16384
        self.recv_handler('', '', length, callback)

    def recv_handler(self, new_data, old_data, total_size, callback):
        data = old_data + new_data
        if len(data) == total_size:
            callback(data)
            return

        future = asyncio.async(self.queue.get())
        future.add_done_callback(
            lambda future: self.recv_handler(
                future.result(), data, total_size, callback))

    def read_frame(self, callback):
        self.recv(
            8, lambda data: self._read_frame_header(data, callback))

    def _read_frame_header(self, header, callback):
        if len(header) < 8:
            callback(None)
            return

        try:
            size = int(header, 16)
        except ValueError:
            return None

        data = ''
        self._read_frame_data('', data, size, callback)

    def _read_frame_data(self, new_data, old_data, total_size, callback):
        data = old_data + new_data
        if len(data) == total_size:
            callback(data)
            return

        this_size = min(total_size - len(data), 1000)
        self.recv(
            this_size,
            lambda new_data: self._read_frame_data(
                new_data, data, total_size, callback))


class Pipe(object):
    """A bi-directional communications channel.

    This consists of two file-like objects, which represent alternate
    ends of a bi-directional pipe."""
    def __init__(self):
        self.endpointa = PipeChannel()
        self.endpointb = PipeChannel()

        self.endpointa.other = self.endpointb
        self.endpointb.other = self.endpointa


class MockServer(object):
    """A simulated Gazebo publish-subscribe server."""
    def __init__(self):
        self.pipe = Pipe()

    def client_socket(self):
        return self.pipe.endpointb

    def write(self, data, callback):
        self.pipe.endpointa.write_frame(data, callback)

    def write_packet(self, name, message, callback):
        self.pipe.endpointa.write_packet(name, message, callback)

    def init_sequence(self, callback):
        self.write_packet(
            'version_init',
            gz_string_pb2.GzString(data='gazebo 2.2 simversion'),
            lambda: self._init_sequence1(callback))

    def _init_sequence1(self, callback):
        self.write_packet(
            'topic_namepaces_init',
            gz_string_v_pb2.GzString_V(data=['a', 'b']),
            lambda: self._init_sequence2(callback))

    def _init_sequence2(self, callback):
        self.write_packet(
            'publishers_init',
            publishers_pb2.Publishers(publisher=[
                publish_pb2.Publish(topic='inittopic1',
                                    msg_type='msgs.Fake',
                                    host='myhost',
                                    port=1234),
                ]),
            callback)

    def read_packet(self, callback):
        self.pipe.endpointa.read_frame(callback)


class FakeSocket(object):
    def __init__(self):
        self.pipe = None

    def bind(self, *args):
        pass

    def setblocking(self, value):
        pass

    def getsockname(self):
        return ('127.0.0.1', 12345)

    def listen(self, backlog):
        pass

    def write(self, data, callback):
        self.pipe.write(data, callback)

    def recv(self, size, callback):
        self.pipe.recv(size, callback)


class ManagerFixture(object):
    def __init__(self):
        print "ManagerFixture.__init__"
        self.manager = None

        self.server = MockServer()
        self.next_connect_socket = self.server.client_socket()
        self.fake_socket = FakeSocket()

        self.old_socket = socket.socket
        socket.socket = mock.MagicMock(
            return_value=self.fake_socket)

        loop = asyncio.get_event_loop()
        self.old_connect = loop.sock_connect
        loop.sock_connect = self.connect

        self.old_accept = loop.sock_accept
        loop.sock_accept = self.accept

        self.old_sock_recv = loop.sock_recv
        loop.sock_recv = self.recv

        self.old_sock_sendall = loop.sock_sendall
        loop.sock_sendall = self.sendall

        manager_future = pygazebo.connect(('localhost', 12345))

        self.init_future = asyncio.Future()
        self.server.init_sequence(lambda: self.init_future.set_result(None))

        asyncio.get_event_loop().run_until_complete(manager_future)
        self.manager = manager_future.result()

    def connect(self, socket, addr):
        print "connect, returning:", self.next_connect_socket
        socket.pipe = self.next_connect_socket
        self.next_connect_socket = None

        result = asyncio.Future()
        result.set_result(None)
        return result

    def accept(self, sock):
        result = asyncio.Future()
        self.serve_future = result
        return result

    def recv(self, sock, size):
        result = asyncio.Future()
        sock.recv(size, lambda data: result.set_result(data))
        return result

    def sendall(self, sock, data):
        result = asyncio.Future()
        sock.write(data, lambda: result.set_result(None))
        return result

    def done(self):
        socket.socket = self.old_socket

        loop = asyncio.get_event_loop()
        loop.sock_connect = self.old_connect
        loop.sock_accept = self.old_accept
        loop.sock_recv = self.old_sock_recv
        loop.sock_sendall = self.old_sock_sendall

        # Ensure that we don't keep any events around from one test to
        # the next.
        loop.close()
        asyncio.set_event_loop(asyncio.new_event_loop())


@pytest.fixture
def manager():
    return ManagerFixture()


class TestPygazebo(object):
    @pytest.fixture(autouse=True)
    def cleanup(self, request, manager):
        request.addfinalizer(manager.done)

    def test_connect(self, manager):
        # Nothing beyond the base fixture is required for this test.
        assert manager.manager.namespaces() == ['a', 'b']
        publications = manager.manager.publications()
        assert len(publications) == 1
        assert publications[0] == ('inittopic1', 'msgs.Fake')

    def test_advertise(self, manager):
        loop = asyncio.get_event_loop()

        # Start listening for things in the server.
        listen = asyncio.Future()
        manager.server.read_packet(lambda data: listen.set_result(data))
        publisher_future = manager.manager.advertise('mytopic', 'mymsgtype')
        publisher = loop.run_until_complete(publisher_future)
        assert publisher is not None

        loop.run_until_complete(listen)
        packet_data = listen.result()

        # We should have received an advertise for this topic.
        packet = packet_pb2.Packet.FromString(packet_data)
        assert packet.type == 'advertise'

        advertise = publish_pb2.Publish.FromString(packet.serialized_data)
        assert advertise.topic == 'mytopic'
        assert advertise.msg_type == 'mymsgtype'

    def test_subscribe(self, manager):
        loop = asyncio.get_event_loop()

        received_data = []
        first_data_future = asyncio.Future()

        def callback(data):
            received_data.append(data)
            if not first_data_future.done():
                first_data_future.set_result(None)

        listen = asyncio.Future()
        manager.server.read_packet(lambda data: listen.set_result(data))
        subscriber = manager.manager.subscribe(
            'subscribetopic', 'othermsgtype', callback)
        assert subscriber is not None
        loop.run_until_complete(listen)
        packet_data = listen.result()

        # We should have received a subscribe for this topic.
        packet = packet_pb2.Packet.FromString(packet_data)
        assert packet.type == 'subscribe'

        subscribe = subscribe_pb2.Subscribe.FromString(
            packet.serialized_data)
        assert subscribe.topic == 'subscribetopic'
        assert subscribe.msg_type == 'othermsgtype'

        other_pipe = Pipe()
        manager.next_connect_socket = other_pipe.endpointb

        other_future = asyncio.Future()
        other_pipe.endpointa.read_frame(
            lambda data: other_future.set_result(data))

        # Pretend like we now have a remote peer who wants to publish
        # on this topic.
        publish = publish_pb2.Publish()
        publish.msg_type = 'othermsgtype'
        publish.topic = 'subscribetopic'
        publish.host = 'localhost'
        publish.port = 98765

        publish_future = asyncio.Future()

        manager.server.write_packet(
            'publisher_subscribe',
            publish,
            lambda: publish_future.set_result(None))

        loop.run_until_complete(subscriber.wait_for_connection())

        assert len(received_data) == 0

        # Write a frame to the pipe and see that it shows up in
        # received data.
        other_pipe.endpointa.write_frame('testdata', lambda: None)
        loop.run_until_complete(first_data_future)
        assert len(received_data) == 1
        assert received_data[0] == 'testdata'

    def test_send(self, manager):
        loop = asyncio.get_event_loop()

        read_future = asyncio.Future()
        manager.server.read_packet(lambda data: read_future.set_result(data))
        publisher_future = manager.manager.advertise('mytopic2', 'msgtype')
        publisher = loop.run_until_complete(publisher_future)

        # Now pretend we are a remote host who wants to subscribe to
        # this topic.
        pipe = Pipe()
        manager.serve_future.set_result((pipe.endpointa, None))

        loop.run_until_complete(read_future)

        subscribe = subscribe_pb2.Subscribe()
        subscribe.topic = 'mytopic2'
        subscribe.msg_type = 'msgtype'
        subscribe.host = 'localhost'
        subscribe.port = 54321

        write_future = asyncio.Future()
        pipe.endpointb.write_packet('sub', subscribe,
                                    lambda: write_future.set_result(None))
        loop.run_until_complete(publisher.wait_for_listener())

        read_data1 = asyncio.Future()
        # At this point, anything we "publish" should end up being
        # written to this pipe.
        pipe.endpointb.read_frame(lambda data: read_data1.set_result(data))

        sample_message = gz_string_pb2.GzString()
        sample_message.data = 'testdata'
        publish_future = publisher.publish(sample_message)

        loop.run_until_complete(read_data1)
        data_frame = read_data1.result()
        assert data_frame == sample_message.SerializeToString()

        assert loop.run_until_complete(publish_future) is None

        # Test sending a very large message, it should require no
        # individual writes which are too large.
        read_data2 = asyncio.Future()
        pipe.endpointb.read_frame(lambda data: read_data2.set_result(data))
        sample_message.data = ' ' * 20000
        publisher.publish(sample_message)

        loop.run_until_complete(read_data2)
        data_frame = read_data2.result()
        assert data_frame == sample_message.SerializeToString()

import logging
import sys
logging.basicConfig(level=logging.DEBUG, stream=sys.stdout)

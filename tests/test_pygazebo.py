#!/usr/bin/env python
# -*- coding: utf-8 -*-

"""
test_pygazebo
----------------------------------

Tests for `pygazebo` module.
"""

import eventlet
import mock
import pytest

# TO TEST:
#  * normal cases
#  * all protocol error handling

from pygazebo import pygazebo
from pygazebo.msg import gz_string_pb2
from pygazebo.msg import gz_string_v_pb2
from pygazebo.msg import packet_pb2
from pygazebo.msg import publishers_pb2
from pygazebo.msg import publish_pb2
from pygazebo.msg import subscribe_pb2


class PipeChannel(object):
    """One half of a simulated pipe, implemented using eventlet.

    Writes and reads block until the other side is available to
    complete the transaction.

    Attributes:
     other (PipeChannel): The opposite direction pair for this channel.
    """
    other = None

    def __init__(self):
        self.queue = eventlet.queue.Queue(0)

    def send(self, data):
        self.write(data)

    def write(self, data):
        for x in data:
            assert len(x) == 1
            self.other.queue.put(x, block=True, timeout=1.0)

    def write_frame(self, data):
        header = '%08X' % len(data)
        self.write(header + data)

    def write_packet(self, name, message):
        packet = packet_pb2.Packet()
        packet.stamp.sec = 0
        packet.stamp.nsec = 0
        packet.type = name
        packet.serialized_data = message.SerializeToString()
        self.write_frame(packet.SerializeToString())

    def recv(self, length):
        result = ''
        for x in range(length):
            data = self.queue.get(block=True, timeout=1.0)
            assert len(data) == 1
            result += data
        return result

    def read_frame(self):
        header = self.recv(8)
        if len(header) < 8:
            return None

        try:
            size = int(header, 16)
        except ValueError:
            return None

        data = self.recv(size)
        if len(data) < size:
            return None
        return data


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

    def write(self, data):
        self.pipe.endpointa.write_frame(data)

    def write_packet(self, name, message):
        self.pipe.endpointa.write_packet(name, message)

    def init_sequence(self):
        self.write_packet(
            'version_init',
            gz_string_pb2.GzString(data='gazebo 2.2 simversion'))

        self.write_packet(
            'topic_namepaces_init',
            gz_string_v_pb2.GzString_V(data=['a', 'b']))

        self.write_packet(
            'publishers_init',
            publishers_pb2.Publishers(publisher=[
                    publish_pb2.Publish(topic='inittopic1',
                                        msg_type='msgs.Fake',
                                        host='myhost',
                                        port=1234),
                    ]))

    def read_packet(self):
        data = self.pipe.endpointa.read_frame()
        if data is None:
            return data

        return data


class ManagerFixture(object):
    def __init__(self):
        self.manager = None

        self.server = MockServer()

        self.old_connect = eventlet.connect
        eventlet.connect = mock.MagicMock(
            return_value=self.server.client_socket())

        self.old_listen = eventlet.listen
        eventlet.listen = self.listen

        self.old_serve = eventlet.serve
        eventlet.serve = self.serve

        self.manager = pygazebo.Manager(('localhost', 12345))
        self.server.init_sequence()

    def listen(self, addr, family=2, backlog=50):
        class FakeSock(object):
            def getsockname(self):
                return ('localhost', 12345)
        return FakeSock()

    def serve(self, sock, handle, concurrency=1000):
        self.serve_handle = handle
        queue = eventlet.queue.Queue(0)
        queue.get(block=True)  # wait forever

    def done(self):
        eventlet.connect = self.old_connect
        eventlet.listen = self.old_listen
        eventlet.serve = self.old_serve

        if self.manager is not None:
            if self.manager._client_thread._exit_event.ready():
                self.manager._client_thread.wait()
            if self.manager._server_thread._exit_event.ready():
                self.manager._server_thread.wait()


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
        # Start listening for things in the server.
        listen = eventlet.spawn(manager.server.read_packet)
        publisher = manager.manager.advertise('mytopic', 'mymsgtype')
        assert publisher is not None
        packet_data = listen.wait()

        # We should have received an advertise for this topic.
        packet = packet_pb2.Packet.FromString(packet_data)
        assert packet.type == 'advertise'

        advertise = publish_pb2.Publish.FromString(packet.serialized_data)
        assert advertise.topic == 'mytopic'
        assert advertise.msg_type == 'mymsgtype'

    def test_subscribe(self, manager):
        received_data = []

        def callback(data):
            received_data.append(data)

        listen = eventlet.spawn(manager.server.read_packet)
        subscriber = manager.manager.subscribe(
            'subscribetopic', 'othermsgtype', callback)
        assert subscriber is not None
        packet_data = listen.wait()

        # We should have received a subscribe for this topic.
        packet = packet_pb2.Packet.FromString(packet_data)
        assert packet.type == 'subscribe'

        subscribe = subscribe_pb2.Subscribe.FromString(
            packet.serialized_data)
        assert subscribe.topic == 'subscribetopic'
        assert subscribe.msg_type == 'othermsgtype'

    def test_send(self, manager):
        eventlet.spawn(manager.server.read_packet)
        publisher = manager.manager.advertise('mytopic2', 'msgtype')

        # Now pretend we are a remote host who wants to subscribe to
        # this topic.
        pipe = Pipe()
        eventlet.spawn_n(manager.serve_handle, pipe.endpointa, None)

        subscribe = subscribe_pb2.Subscribe()
        subscribe.topic = 'mytopic2'
        subscribe.msg_type = 'msgtype'
        subscribe.host = 'localhost'
        subscribe.port = 54321

        pipe.endpointb.write_packet('sub', subscribe)

        # At this point, anything we "publish" should end up being
        # written to this pipe.
        read_data = eventlet.spawn(pipe.endpointb.read_frame)

        sample_message = gz_string_pb2.GzString()
        sample_message.data = 'testdata'
        publisher.publish(sample_message)

        data_frame = read_data.wait()
        assert data_frame == sample_message.SerializeToString()

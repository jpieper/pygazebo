#!/usr/bin/env python
# -*- coding: utf-8 -*-

"""
test_pygazebo
----------------------------------

Tests for `pygazebo` module.
"""

import eventlet
import mock
import unittest

# TO TEST:
#  * normal cases
#  * all protocol error handling

from pygazebo import pygazebo
from pygazebo.msg import gz_string_pb2
from pygazebo.msg import gz_string_v_pb2
from pygazebo.msg import packet_pb2
from pygazebo.msg import publishers_pb2


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

    def write(self, data):
        for x in data:
            assert len(x) == 1
            self.other.queue.put(x, block=True, timeout=1.0)

    def recv(self, length):
        result = ''
        for x in range(length):
            data = self.queue.get(block=True, timeout=1.0)
            assert len(data) == 1
            result += data
        return result


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
        header = '%08X' % len(data)
        self.pipe.endpointa.write(header + data)

    def write_packet(self, name, message):
        packet = packet_pb2.Packet()
        packet.stamp.sec = 0
        packet.stamp.nsec = 0
        packet.type = name
        packet.serialized_data = message.SerializeToString()
        self.write(packet.SerializeToString())

    def init_sequence(self):
        self.write_packet(
            'version_init',
            gz_string_pb2.GzString(data='gazebo 2.2 simversion'))

        self.write_packet(
            'topic_namepaces_init',
            gz_string_v_pb2.GzString_V(data=['a', 'b']))

        self.write_packet(
            'publishers_init',
            publishers_pb2.Publishers(publisher=[]))


@mock.patch('eventlet.connect')
class TestPygazebo(unittest.TestCase):

    def setUp(self):
        self.manager = None
        self.server = MockServer()

    def test_connect(self, *args):
        eventlet.connect.configure_mock(
            return_value=self.server.client_socket())
        self.manager = pygazebo.Manager(('localhost', 12345))
        self.server.init_sequence()

    def tearDown(self):
        if self.manager is not None:
            if self.manager._client_thread._exit_event.ready():
                self.manager._client_thread.wait()
            if self.manager._server_thread._exit_event.ready():
                self.manager._server_thread.wait()

if __name__ == '__main__':
    unittest.main()

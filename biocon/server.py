# coding: utf-8
#
#    Project: BioCAT user beamline control software (BioCON)
#             https://github.com/biocatiit/beamline-control-user
#
#
#    Principal author:       Jesse Hopkins
#
#    This is free software: you can redistribute it and/or modify
#    it under the terms of the GNU General Public License as published by
#    the Free Software Foundation, either version 3 of the License, or
#    (at your option) any later version.
#
#    This software is distributed in the hope that it will be useful,
#    but WITHOUT ANY WARRANTY; without even the implied warranty of
#    MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
#    GNU General Public License for more details.
#
#    You should have received a copy of the GNU General Public License
#    along with this software.  If not, see <http://www.gnu.org/licenses/>.
from __future__ import absolute_import, division, print_function, unicode_literals
from builtins import object, range, map
from io import open

import multiprocessing
import threading
import logging
from collections import OrderedDict, deque
import traceback
import time
import sys

logger = logging.getLogger(__name__)

import zmq

import pumpcon


class ControlServer(multiprocessing.Process):
    """

    """

    def __init__(self, port):
        """
        Initializes the custom thread. Important parameters here are the
        list of known commands ``_commands`` and known pumps ``known_pumps``.

        :param collections.deque command_queue: The queue used to pass commands to
            the thread.

        :param threading.Event stop_event: An event that is set when the thread
            needs to abort, and otherwise is not set.
        """
        multiprocessing.Process.__init__(self)

        logger.info("Starting pump control thread: %s", self.name)

        self.port = port
        self._stop_event = threading.Event()

        self.context = zmq.Context()
        self.socket = self.context.socket(zmq.PAIR)
        self.socket.bind("tcp://*:{}".format(self.port))

        self._device_control = {
            }

        self._connected_devices = OrderedDict()

        pump_cmd_q = deque()
        pump_abort_event = threading.Event()
        pump_con = pumpcon.PumpCommThread(pump_cmd_q, pump_abort_event, 'PumpCon')
        pump_con.start()

        pump_ctrl = {'queue': pump_cmd_q,
            'abort': 'pump_abort_event',
            'thread': pump_con
            }

        self._device_control['pump'] = pump_ctrl

    def run(self):
        """
        Custom run method for the thread.
        """
        while True:
            if self.socket.poll(10) > 0:
                logger.debug("Getting new command")
                command = self.socket.recv_json()
            else:
                command = None

            # if self._abort_event.is_set():
            #     logger.debug("Abort event detected")
            #     self._abort()
            #     command = None

            if self._stop_event.is_set():
                logger.debug("Stop event detected")
                break

            if command is not None:
                device = command['device']
                device_cmd = command['command']
                get_response = command['response']
                # logger.debug("Processing cmd '%s' with args: %s and kwargs: %s ", device, ', '.join(['{}'.format(a) for a in args]), ', '.join(['{}:{}'.format(kw, item) for kw, item in kwargs.items()]))
                try:
                    device_q = self._device_control[device]['queue']
                    device_q.append(device_cmd)

                    if get_response:
                        answer_q = self._device_control[device]['answer_q']
                        while len(answer_q) == 0:
                            time.sleep(0.01)

                        answer = answer_q.popleft()
                    else:
                        answer = 'cmd sent'

                    self.socket.send_json(answer)

                except Exception:
                    logger.exception(traceback.print_exc())
                    # msg = ("Pump control thread failed to run command '%s' "
                    #     "with args: %s and kwargs: %s " %(command,
                    #     ', '.join(['{}'.format(a) for a in args]),
                    #     ', '.join(['{}:{}'.format(kw, item) for kw, item in kwargs.items()])))
                    # logger.exception(msg)
        if self._stop_event.is_set():
            self._stop_event.clear()
        # else:
        #     self._abort()
        logger.info("Quitting pump control thread: %s", self.name)

    def stop(self):
        """Stops the thread cleanly."""
        # logger.info("Starting to clean up and shut down pump control thread: %s", self.name)
        self.socket.unbind("tcp://*:{}".format(self.port))
        self.socket.close()
        self.context.destroy()

        for device in self._device_control:
            self._device_control[device]['abort'].set()

        self._stop_event.set()

if __name__ == '__main__':
    logger = logging.getLogger(__name__)
    logger.setLevel(logging.INFO)
    h1 = logging.StreamHandler(sys.stdout)
    h1.setLevel(logging.INFO)
    formatter = logging.Formatter('%(asctime)s - %(name)s - %(threadName)s - %(levelname)s - %(message)s')
    h1.setFormatter(formatter)
    logger.addHandler(h1)

    port1 = '5556'
    port2 = '5557'


    control_server = ControlServer(port1)
    control_server.start()

    try:
        while True:
            time.sleep(.01)
    except KeyboardInterrupt:
        control_server.stop()
        control_server.join()

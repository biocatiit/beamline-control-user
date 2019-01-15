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

import threading
import logging
from collections import deque
import traceback
import time
import sys

if __name__ != '__main__':
    logger = logging.getLogger('biocon.client')

import zmq


class ControlClient(threading.Thread):
    """

    """

    def __init__(self, ip, port, command_queue, answer_queue, abort_event, name='ControlClient'):
        """
        Initializes the custom thread. Important parameters here are the
        list of known commands ``_commands`` and known pumps ``known_pumps``.

        :param collections.deque command_queue: The queue used to pass commands to
            the thread.

        :param threading.Event abort_event: An event that is set when the thread
            needs to abort, and otherwise is not set.
        """
        threading.Thread.__init__(self, name=name)
        self.daemon = True

        logger.info("Starting pump control thread: %s", self.name)

        self.ip = ip
        self.port = port
        self.command_queue = command_queue
        self.answer_queue = answer_queue
        self._abort_event = abort_event
        self._stop_event = threading.Event()

        self.context = zmq.Context()
        self.socket = self.context.socket(zmq.PAIR)
        self.socket.connect("tcp://{}:{}".format(self.ip, self.port))

    def run(self):
        """
        Custom run method for the thread.
        """
        while True:
            if len(self.command_queue) > 0:
                logger.debug("Getting new command")
                command = self.command_queue.popleft()
            else:
                command = None

            if self._abort_event.is_set():
                logger.debug("Abort event detected")
                self._abort()
                command = None

            if self._stop_event.is_set():
                logger.debug("Stop event detected")
                break

            if command is not None:
                device = command['device']
                device_cmd = command['command']
                get_response = command['response']
                logger.debug("For device %s, processing cmd '%s' with args: %s and kwargs: %s ", device, device_cmd[0], ', '.join(['{}'.format(a) for a in device_cmd[1]]), ', '.join(['{}:{}'.format(kw, item) for kw, item in device_cmd[2].items()]))
                try:
                    self.socket.send_json(command)
                    answer = self.socket.recv_json()
                    
                    if get_response:
                        self.answer_queue.append(answer)
                        logger.info('Command response: %s' %(answer))
                    else:
                        logger.debug('Command response: %s' %(answer))

                except Exception:
                    logger.exception(traceback.print_exc())
                    # msg = ("Pump control thread failed to run command '%s' "
                    #     "with args: %s and kwargs: %s " %(command,
                    #     ', '.join(['{}'.format(a) for a in args]),
                    #     ', '.join(['{}:{}'.format(kw, item) for kw, item in kwargs.items()])))
                    # logger.exception(msg)
        if self._stop_event.is_set():
            self._stop_event.clear()
        else:
            self._abort()

        self.socket.unbind("tcp://{}:{}".format(self.ip, self.port))
        self.socket.close()
        self.context.destroy()

        logger.info("Quitting pump control thread: %s", self.name)

    def _abort(self):
        """Clears the ``command_queue`` and aborts all current pump motions."""
        logger.info("Aborting pump control thread %s current and future commands", self.name)
        self.command_queue.clear()
        self._abort_event.clear()
        logger.debug("Pump control thread %s aborted", self.name)

    def stop(self):
        """Stops the thread cleanly."""
        logger.info("Starting to clean up and shut down pump control thread: %s", self.name)
        

        self._stop_event.set()

if __name__ == '__main__':
    logger = logging.getLogger('biocon')
    logger.setLevel(logging.DEBUG)
    h1 = logging.StreamHandler(sys.stdout)
    h1.setLevel(logging.DEBUG)
    formatter = logging.Formatter('%(asctime)s - %(name)s - %(threadName)s - %(levelname)s - %(message)s')
    h1.setFormatter(formatter)
    logger.addHandler(h1)

    port1 = '5556'
    port2 = '5557'

    ip = '164.54.204.104'

    ctrl_cmd_q = deque()
    ctrl_return_q = deque()
    ctrl_abort_event = threading.Event()

    control_client = ControlClient(ip, port1, ctrl_cmd_q, ctrl_return_q, ctrl_abort_event)
    control_client.start()

    init_cmd = ('connect', ('COM6', 'pump2', 'VICI_M50'),
        {'flow_cal': 626.2, 'backlash_cal': 9.278})
    fr_cmd = ('set_flow_rate', ('pump2', 1000), {})
    start_cmd = ('start_flow', ('pump2',), {})
    stop_cmd = ('stop', ('pump2',), {})
    dispense_cmd = ('dispense', ('pump2', 200), {})
    aspirate_cmd = ('aspirate', ('pump2', 200), {})
    moving_cmd = ('is_moving', ('pump2',), {})
    units_cmd = ('set_units', ('pump2', 'uL/min'), {})
    disconnect_cmd = ('disconnect', ('pump2', ), {})

    init_client_cmd = {'device': 'pump', 'command': init_cmd, 'response': False}
    fr_client_cmd = {'device': 'pump', 'command': fr_cmd, 'response': False}
    start_client_cmd = {'device': 'pump', 'command': start_cmd, 'response': False}
    stop_client_cmd = {'device': 'pump', 'command': stop_cmd, 'response': False}
    dispense_client_cmd = {'device': 'pump', 'command': dispense_cmd, 'response': False}
    aspirate_client_cmd = {'device': 'pump', 'command': aspirate_cmd, 'response': False}
    moving_client_cmd = {'device': 'pump', 'command': moving_cmd, 'response': True}
    units_client_cmd = {'device': 'pump', 'command': units_cmd, 'response': False}
    disconnect_client_cmd = {'device': 'pump', 'command': disconnect_cmd, 'response': False}

    ctrl_cmd_q.append(init_client_cmd)
    ctrl_cmd_q.append(units_client_cmd)
    ctrl_cmd_q.append(fr_client_cmd)
    ctrl_cmd_q.append(start_client_cmd)
    time.sleep(5)
    # ctrl_cmd_q.append(dispense_client_cmd)
    # ctrl_cmd_q.append(aspirate_client_cmd)
    ctrl_cmd_q.append(moving_client_cmd)
    while len(ctrl_return_q) == 0:
        time.sleep(0.01)
    print(ctrl_return_q.popleft())
    ctrl_cmd_q.append(stop_client_cmd)
    time.sleep(2)
    ctrl_cmd_q.append(disconnect_client_cmd)
    time.sleep(2)
    control_client.stop()

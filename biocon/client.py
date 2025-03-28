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
from collections import deque, defaultdict
import traceback
import time
import sys

if __name__ != '__main__':
    logger = logging.getLogger(__name__)

import zmq


class ControlClient(threading.Thread):
    """

    """

    def __init__(self, ip, port, command_queue, answer_queue, abort_event,
        timeout_event, name='ControlClient', status_queue=None):
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

        logger.info("Starting control client: %s", self.name)

        self.ip = ip
        self.port = port
        self.command_queue = command_queue
        self.answer_queue = answer_queue
        self.status_queue = status_queue
        self._abort_event = abort_event
        self._stop_event = threading.Event()
        self.timeout_event = timeout_event

        self.connect_error = 0

        self.heartbeat = 60
        self.last_ping = 0
        self.hb_scale = 1

        self.resend_missed_commands_on_reconnect = True
        self.missed_cmds = deque()


    def run(self):
        """
        Custom run method for the thread.
        """
        logger.info("Connecting to %s on port %s", self.ip, self.port)
        self.context = zmq.Context()
        self.socket = self.context.socket(zmq.PAIR)
        self.socket.set(zmq.LINGER, 0)
        self.socket.connect("tcp://{}:{}".format(self.ip, self.port))

        new_connection = True

        # Clear backlog of incomming messages on startup
        start = time.time()
        while time.time()-start < 1:
            if self.socket.poll(10) > 0:
                resp = self.socket.recv_pyobj()

                res_type, response = resp

                # if res_type != 'status':
                #     break
                # else:
                #     start = time.time()

        while True:
            action_taken = False

            try:
                if not self.socket.closed:
                    if time.time() - self.last_ping > self.heartbeat*self.hb_scale:
                        self.last_ping = time.time()
                        self._ping(new_connection)
                else:
                    if time.time() - self.last_ping > self.heartbeat*self.hb_scale:
                        self.last_ping = time.time()
                        self._ping(new_connection)

                if new_connection:
                    new_connection = False

                if len(self.command_queue) > 0:
                    # logger.debug("Getting new command")
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
                    action_taken = True

                    if not self.socket.closed:
                        self._send_cmd(command)

                    elif self.resend_missed_commands_on_reconnect:
                        self.missed_cmds.append(command)

                if not self.socket.closed:
                    got_status = self._get_status()
                else:
                    got_status = False

                action_taken = action_taken or got_status

                if not action_taken:
                    time.sleep(0.01)

            except Exception:
                logger.error('Error in client thread:\n{}'.format(traceback.format_exc()))

        if self._stop_event.is_set():
            self._stop_event.clear()
        else:
            self._abort()

        if not self.socket.closed:
            self.socket.disconnect("tcp://{}:{}".format(self.ip, self.port))
            self.socket.close(0)

        self.context.destroy(0)

        logger.info("Quitting remote client thread: %s", self.name)


    def _send_cmd(self, command):
        # logger.debug('Sending command %s', command)
        device = command['device']
        device_cmd = command['command']
        get_response = command['response']
        # logger.debug("For device %s, processing cmd '%s' with args: %s and kwargs: %s ", device, device_cmd[0], ', '.join(['{}'.format(a) for a in device_cmd[1]]), ', '.join(['{}:{}'.format(kw, item) for kw, item in device_cmd[2].items()]))
        try:
            self.socket.send_json(command)

            answer = self._wait_for_response(60)

            if answer == '':
                raise zmq.ZMQError(msg="Could not get a response from the server")
            else:
                self.connect_error = 0
                self.hb_scale = 1

            # logger.debug('Command response: %s', answer)

            if get_response:
                self.answer_queue.append(answer)

        except zmq.ZMQError:
            device = command['device']
            device_cmd = command['command']
            msg = ("Device %s failed to run command '%s' "
                "with args: %s and kwargs: %s. Timeout or other ZMQ "
                "error." %(device, device_cmd[0],
                ', '.join(['{}'.format(a) for a in device_cmd[1]]),
                ', '.join(['{}:{}'.format(kw, item) for kw, item in device_cmd[2].items()])))
            logger.error(msg)
            self.connect_error += 1
            self._ping()
            if not self.timeout_event.set():
                self.answer_queue.append(None)

            self.missed_cmds.append(command)

        except Exception:
            device = command['device']
            device_cmd = command['command']
            msg = ("Device %s failed to run command '%s' "
                "with args: %s and kwargs: %s. Exception follows:" %(device, device_cmd[0],
                ', '.join(['{}'.format(a) for a in device_cmd[1]]),
                ', '.join(['{}:{}'.format(kw, item) for kw, item in device_cmd[2].items()])))
            logger.error(msg)
            logger.error(traceback.print_exc())
            self.connect_error += 1

        if self.connect_error >= 5:
            msg = ('5 consecutive failures to run a command on device'
                '{}.'.format(device))
            logger.error(msg)
            logger.error("Connection timed out")
            self.timeout_event.set()

    def _wait_for_response(self, timeout):
        start_time = time.time()
        answer = ''

        while time.time()-start_time < timeout:
            if self.socket.poll(10) > 0:
                resp = self.socket.recv_pyobj()
                # logger.debug('Received message: %s', resp)
                res_type, response = resp

                if res_type == 'status':
                    # logger.debug('Recevied status %s', response)
                    if self.status_queue is not None:
                        self.status_queue.append(response)

                elif res_type == 'response':
                    answer = response
                    # logger.debug('Recevied response %s', answer)
                    break

        return answer

    def _get_status(self):
        got_response = False

        while True:
            if self.socket.poll(10) > 0:
                resp = self.socket.recv_pyobj()
                # logger.debug('Received status message: %s', resp)
                res_type, response = resp

                if res_type == 'status':
                    # logger.debug('Recevied status %s', response)
                    if self.status_queue is not None:
                        self.status_queue.append(response)

                got_response = True

            else:
                break


        return got_response

    def _ping(self, new_connection=False):
        # logger.debug("Checking if server is active")
        cmd = {'device': 'server', 'command': ('ping', (), {}), 'response': False}

        retry = True

        if not self.socket.closed:
            while retry:
                self.socket.send_json(cmd)

                answer = self._wait_for_response(1)

                if answer == 'ping received':
                    # logger.debug("Connection to server verified")
                    retry = False
                    self.connect_error = 0
                    self.hb_scale = 1
                else:
                    logger.error("Could not get a response from the server on ping")

                    if not new_connection:
                        self.connect_error += 1
                        retry = False
                    else:
                        self.connect_error += 1
                        self.hb_scale = 0.017

                    if self.connect_error >= 5:
                        logger.error("Connection timed out")
                        self.timeout_event.set()
                        self.socket.disconnect("tcp://{}:{}".format(self.ip, self.port))
                        self.socket.close(0)
                        self.hb_scale = 0.25
                        retry = False

        else:
            logger.info('Trying to reconnect to server')
            self.socket = self.context.socket(zmq.PAIR)
            self.socket.set(zmq.LINGER, 0)
            self.socket.connect("tcp://{}:{}".format(self.ip, self.port))
            connect_tries = 0

            while connect_tries < 5:
                self.socket.send_json(cmd)

                answer = self._wait_for_response(1)

                if answer == 'ping received':
                    logger.debug("Connection to server verified")
                    connect_tries = 5
                    self.timeout_event.clear()
                    self.connect_error = 0

                    if self.resend_missed_commands_on_reconnect:
                        while len(self.missed_cmds) > 0 and not self.timeout_event.is_set():
                            cmd = self.missed_cmds.popleft()
                            self._send_cmd(cmd)

                else:
                    connect_tries = connect_tries+1

            if self.timeout_event.is_set():
                self.socket.disconnect("tcp://{}:{}".format(self.ip, self.port))
                self.socket.close(0)
                self.hb_scale = 0.5

    def _abort(self):
        """Clears the ``command_queue`` and aborts all current pump motions."""
        logger.info("Aborting remote client thread %s current and future commands", self.name)
        self.command_queue.clear()
        self._abort_event.clear()
        logger.debug("Remote client thread %s aborted", self.name)

    def stop(self):
        """Stops the thread cleanly."""
        logger.info("Starting to clean up and shut down remote client thread: %s", self.name)

        self._stop_event.set()

if __name__ == '__main__':
    logger = logging.getLogger()
    logger.setLevel(logging.DEBUG)
    h1 = logging.StreamHandler(sys.stdout)
    h1.setLevel(logging.DEBUG)
    formatter = logging.Formatter('%(asctime)s - %(name)s - %(threadName)s - %(levelname)s - %(message)s')
    h1.setFormatter(formatter)
    logger.addHandler(h1)

    port1 = '5556'
    port2 = '5557'

    ip = '164.54.204.37'

    # pump_ctrl_cmd_q = deque()
    # pump_ctrl_return_q = deque()
    # pump_ctrl_abort_event = threading.Event()
    # pump_timeout_event = threading.Event()

    # pump_control_client = ControlClient(ip, port1, pump_ctrl_cmd_q,
    #     pump_ctrl_return_q, pump_ctrl_abort_event, pump_timeout_event, name='PumpControlClient')
    # pump_control_client.start()

    # init_cmd = ('connect', ('COM6', 'pump2', 'VICI_M50'),
    #     {'flow_cal': 626.2, 'backlash_cal': 9.278})
    # fr_cmd = ('set_flow_rate', ('pump2', 1000), {})
    # start_cmd = ('start_flow', ('pump2',), {})
    # stop_cmd = ('stop', ('pump2',), {})
    # dispense_cmd = ('dispense', ('pump2', 200), {})
    # aspirate_cmd = ('aspirate', ('pump2', 200), {})
    # moving_cmd = ('is_moving', ('pump2',), {})
    # units_cmd = ('set_units', ('pump2', 'uL/min'), {})
    # disconnect_cmd = ('disconnect', ('pump2', ), {})

    # init_client_cmd = {'device': 'pump', 'command': init_cmd, 'response': False}
    # fr_client_cmd = {'device': 'pump', 'command': fr_cmd, 'response': False}
    # start_client_cmd = {'device': 'pump', 'command': start_cmd, 'response': False}
    # stop_client_cmd = {'device': 'pump', 'command': stop_cmd, 'response': False}
    # dispense_client_cmd = {'device': 'pump', 'command': dispense_cmd, 'response': False}
    # aspirate_client_cmd = {'device': 'pump', 'command': aspirate_cmd, 'response': False}
    # moving_client_cmd = {'device': 'pump', 'command': moving_cmd, 'response': True}
    # units_client_cmd = {'device': 'pump', 'command': units_cmd, 'response': False}
    # disconnect_client_cmd = {'device': 'pump', 'command': disconnect_cmd, 'response': False}

    # pump_ctrl_cmd_q.append(init_client_cmd)
    # pump_ctrl_cmd_q.append(units_client_cmd)
    # pump_ctrl_cmd_q.append(fr_client_cmd)
    # pump_ctrl_cmd_q.append(start_client_cmd)
    # time.sleep(5)
    # # pump_ctrl_cmd_q.append(dispense_client_cmd)
    # # pump_ctrl_cmd_q.append(aspirate_client_cmd)
    # pump_ctrl_cmd_q.append(moving_client_cmd)
    # while len(pump_ctrl_return_q) == 0:
    #     time.sleep(0.01)
    # print(pump_ctrl_return_q.popleft())
    # pump_ctrl_cmd_q.append(stop_client_cmd)
    # time.sleep(2)
    # pump_ctrl_cmd_q.append(disconnect_client_cmd)
    # time.sleep(2)
    # pump_control_client.stop()


    fm_ctrl_cmd_q = deque()
    fm_ctrl_return_q = deque()
    fm_ctrl_abort_event = threading.Event()
    fm_ctrl_timeout_event = threading.Event()

    fm_control_client = ControlClient(ip, port1, fm_ctrl_cmd_q,
        fm_ctrl_return_q, fm_ctrl_abort_event, fm_ctrl_timeout_event, name='FMControlClient')
    fm_control_client.start()

    init_cmd = ('connect', ('COM8', 'bfs1', 'BFS'), {})
    fr_cmd = ('get_flow_rate', ('bfs1',), {})
    d_cmd = ('get_density', ('bfs1',), {})
    t_cmd = ('get_temperature', ('bfs1',), {})
    units_cmd = ('set_units', ('bfs1', 'mL/min'), {})
    disconnect_cmd = ('disconnect', ('bfs1', ), {})

    init_client_cmd = {'device': 'fm', 'command': init_cmd, 'response': False}
    fr_client_cmd = {'device': 'fm', 'command': fr_cmd, 'response': True}
    d_client_cmd = {'device': 'fm', 'command': d_cmd, 'response': True}
    t_client_cmd = {'device': 'fm', 'command': t_cmd, 'response': True}
    units_client_cmd = {'device': 'fm', 'command': units_cmd, 'response': False}
    disconnect_client_cmd = {'device': 'fm', 'command': disconnect_cmd, 'response': False}

    fm_ctrl_cmd_q.append(init_client_cmd)
    fm_ctrl_cmd_q.append(fr_client_cmd)
    while len(fm_ctrl_return_q) == 0:
        time.sleep(0.01)
    print(fm_ctrl_return_q.popleft())
    fm_ctrl_cmd_q.append(d_client_cmd)
    while len(fm_ctrl_return_q) == 0:
        time.sleep(0.01)
    print(fm_ctrl_return_q.popleft())
    fm_ctrl_cmd_q.append(t_client_cmd)
    while len(fm_ctrl_return_q) == 0:
        time.sleep(0.01)
    print(fm_ctrl_return_q.popleft())
    fm_ctrl_cmd_q.append(units_client_cmd)
    fm_ctrl_cmd_q.append(disconnect_client_cmd)
    time.sleep(2)
    fm_control_client.stop()


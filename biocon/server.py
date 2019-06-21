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
import logging.handlers as handlers
from collections import deque
import traceback
import time
import sys
import os

if __name__ != '__main__':
    logger = logging.getLogger(__name__)

import zmq
import wx

import pumpcon
import fmcon


class ControlServer(threading.Thread):
    """

    """

    def __init__(self, ip, port, name='ControlServer', pump_comm_locks = None):
        """
        Initializes the custom thread. Important parameters here are the
        list of known commands ``_commands`` and known pumps ``known_pumps``.

        :param collections.deque command_queue: The queue used to pass commands to
            the thread.

        :param threading.Event stop_event: An event that is set when the thread
            needs to abort, and otherwise is not set.
        """
        threading.Thread.__init__(self, name=name)
        self.daemon = True

        logger.info("Initializing control server: %s", self.name)

        self.ip = ip
        self.port = port

        self._device_control = {
            }

        self._stop_event = threading.Event()

        self.context = zmq.Context()
        self.socket = self.context.socket(zmq.PAIR)
        self.socket.bind("tcp://{}:{}".format(self.ip, self.port))

        self.pump_comm_locks = pump_comm_locks

        pump_cmd_q = deque()
        pump_return_q = deque()
        pump_abort_event = threading.Event()
        pump_con = pumpcon.PumpCommThread(pump_cmd_q, pump_return_q, pump_abort_event, 'PumpCon')
        pump_con.start()

        if self.pump_comm_locks is not None:
            pump_cmd_q.append(('add_comlocks', (self.pump_comm_locks,), {}))

        pump_ctrl = {'queue': pump_cmd_q,
            'abort': pump_abort_event,
            'thread': pump_con,
            'answer_q': pump_return_q
            }

        self._device_control['pump'] = pump_ctrl

        fm_cmd_q = deque()
        fm_return_q = deque()
        fm_abort_event = threading.Event()
        fm_con = fmcon.FlowMeterCommThread(fm_cmd_q, fm_return_q, fm_abort_event, 'FMCon')
        fm_con.start()

        fm_ctrl = {'queue': fm_cmd_q,
            'abort': fm_abort_event,
            'thread': fm_con,
            'answer_q': fm_return_q
            }

        self._device_control['fm'] = fm_ctrl

    def run(self):
        """
        Custom run method for the thread.
        """
        while True:
            try:
                if self.socket.poll(10) > 0:
                    logger.debug("Getting new command")
                    command = self.socket.recv_json()
                else:
                    command = None
            except Exception:
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
                logger.debug("For device %s, processing cmd '%s' with args: %s and kwargs: %s ", device, device_cmd[0], ', '.join(['{}'.format(a) for a in device_cmd[1]]), ', '.join(['{}:{}'.format(kw, item) for kw, item in device_cmd[2].items()]))
                try:

                    if device == 'server':
                        if device_cmd[0] == 'ping':
                            answer = 'ping received'
                        else:
                            answer = ''
                    else:
                        device_q = self._device_control[device]['queue']
                        device_q.append(device_cmd)

                        if get_response:
                            answer_q = self._device_control[device]['answer_q']

                            start_time = time.time()
                            while len(answer_q) == 0 and time.time()-start_time < 5:
                                time.sleep(0.01)

                            if len(answer_q) == 0:
                                answer = ''
                            else:
                                answer = answer_q.popleft()
                        else:
                            answer = 'cmd sent'

                    if answer == '':
                        logger.exception('No response received from device')
                    else:
                        self.socket.send_json(answer)

                except Exception:
                    device = command['device']
                    device_cmd = command['command']
                    msg = ("Device %s failed to run command '%s' "
                        "with args: %s and kwargs: %s. Exception follows:" %(device, device_cmd[0],
                        ', '.join(['{}'.format(a) for a in device_cmd[1]]),
                        ', '.join(['{}:{}'.format(kw, item) for kw, item in device_cmd[2].items()])))
                    logger.exception(msg)
                    logger.exception(traceback.print_exc())

            else:
                time.sleep(0.01)

        if self._stop_event.is_set():
            self._stop_event.clear()
        # else:
        #     self._abort()
        logger.info("Quitting pump control thread: %s", self.name)

    def stop(self):
        """Stops the thread cleanly."""
        # logger.info("Starting to clean up and shut down pump control thread: %s", self.name)
        self.socket.unbind("tcp://{}:{}".format(self.ip, self.port))
        self.socket.close()
        self.context.destroy()

        for device in self._device_control:
            self._device_control[device]['abort'].set()

        self._stop_event.set()

if __name__ == '__main__':
    logger = logging.getLogger()
    logger.setLevel(logging.DEBUG)
    h1 = logging.StreamHandler(sys.stdout)
    # h1.setLevel(logging.DEBUG)
    h1.setLevel(logging.INFO)
    formatter = logging.Formatter('%(asctime)s - %(name)s - %(threadName)s - %(levelname)s - %(message)s')
    h1.setFormatter(formatter)
    logger.addHandler(h1)

    app = wx.App()

    standard_paths = wx.StandardPaths.Get() #Can't do this until you start the wx app
    info_dir = standard_paths.GetUserLocalDataDir()
    print (info_dir)
    if not os.path.exists(info_dir):
        os.mkdir(info_dir)
    h2 = handlers.RotatingFileHandler(os.path.join(info_dir, 'biocon_server.log'), maxBytes=100e6, backupCount=20, delay=True)
    h2.setLevel(logging.DEBUG)
    formatter2 = logging.Formatter('%(asctime)s - %(name)s - %(threadName)s - %(levelname)s - %(message)s')
    h2.setFormatter(formatter2)

    logger.addHandler(h2)

    port1 = '5556'
    port2 = '5557'
    ip = '164.54.204.37'

    pump_comm_locks = {'COM1'   : threading.Lock(),
        'COM2'  : threading.Lock(),
        }

    control_server1 = ControlServer(ip, port1, name='PumpControlServer',
        pump_comm_locks = pump_comm_locks)
    control_server1.start()

    control_server2 = ControlServer(ip, port2, name='FMControlServer')
    control_server2.start()


    setup_pumps = [('sheath', 'VICI M50', 'COM2', ['626.2', '9.278'], {}, {}),
                        ('outlet', 'VICI M50', 'COM1', ['627.32', '11.826'], {}, {})
                        ]

    local_comm_locks = {'sheath'    : pump_comm_locks[setup_pumps[0][2]],
        'outlet'    : pump_comm_locks[setup_pumps[1][2]]
        }
    frame = pumpcon.PumpFrame(local_comm_locks, setup_pumps, None, title='Pump Control')
    frame.Show()
    app.MainLoop()

    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        control_server1.stop()
        control_server1.join()

        control_server2.stop()
        control_server2.join()

    logger.info("Quitting server")

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
import valvecon
import spectrometercon
import utils


class ControlServer(threading.Thread):
    """

    """

    def __init__(self, ip, port, name='ControlServer', pump_comm_locks = None,
        valve_comm_locks=None, start_pump=False, start_fm=False,
        start_valve=False, start_uv=False,):
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

        self.ip = ip
        self.port = port

        self._device_control = {
            }

        self._stop_event = threading.Event()

        self.pump_comm_locks = pump_comm_locks
        self.valve_comm_locks = valve_comm_locks

        self._start_pump = start_pump
        self._start_fm = start_fm
        self._start_valve = start_valve
        self._start_uv = start_uv

    def run(self):
        """
        Custom run method for the thread.
        """
        logger.info("Initializing control server: %s", self.name)

        self.context = zmq.Context()
        self.socket = self.context.socket(zmq.PAIR)
        self.socket.set(zmq.LINGER, 0)
        self.socket.set_hwm(10)
        self.socket.bind("tcp://{}:{}".format(self.ip, self.port))


        if self._start_pump:
            pump_cmd_q = deque()
            pump_return_q = deque()
            pump_status_q = deque()
            pump_con = pumpcon.PumpCommThread('PumpCon')
            pump_con.start()

            pump_con.add_new_communication('zmq_server', pump_cmd_q,
                pump_return_q, pump_status_q)

            pump_ctrl = {
                'queue'     : pump_cmd_q,
                'answer_q'  : pump_return_q,
                'status_q'  : pump_status_q,
                'thread'    : pump_con,
                }

            self._device_control['pump'] = pump_ctrl


        if self._start_fm:
            fm_cmd_q = deque()
            fm_return_q = deque()
            fm_status_q = deque()
            fm_con = fmcon.FlowMeterCommThread('FMCon')
            fm_con.start()

            fm_con.add_new_communication('zmq_server', fm_cmd_q, fm_return_q,
                fm_status_q)

            fm_ctrl = {
                'queue'     : fm_cmd_q,
                'answer_q'  : fm_return_q,
                'status_q'  : fm_status_q,
                'thread'    : fm_con,
                }

            self._device_control['fm'] = fm_ctrl


        if self._start_valve:
            valve_cmd_q = deque()
            valve_return_q = deque()
            valve_status_q = deque()
            valve_con = valvecon.ValveCommThread('ValveCon')
            valve_con.start()

            valve_con.add_new_communication('zmq_server', valve_cmd_q, valve_return_q,
                valve_status_q)

            valve_ctrl = {
                'queue'     : valve_cmd_q,
                'answer_q'  : valve_return_q,
                'status_q'  : valve_status_q,
                'thread'    : valve_con,
                }

            self._device_control['valve'] = valve_ctrl

        if self._start_uv:
            uv_cmd_q = deque()
            uv_return_q = deque()
            uv_status_q = deque()
            uv_con = spectrometercon.UVCommThread('UVCon')
            uv_con.start()

            uv_con.add_new_communication('zmq_server', uv_cmd_q, uv_return_q,
                uv_status_q)

            uv_ctrl = {
                'queue'     : uv_cmd_q,
                'answer_q'  : uv_return_q,
                'status_q'  : uv_status_q,
                'thread'    : uv_con,
                }

            self._device_control['uv'] = uv_ctrl

        while True:
            try:
                cmds_run = False

                try:
                    if self.socket.poll(10) > 0:
                        logger.debug("Getting new command")
                        command = self.socket.recv_json()
                    else:
                        command = None
                except Exception:
                    command = None

                if self._stop_event.is_set():
                    logger.debug("Stop event detected")
                    break

                if command is not None:
                    cmds_run = True

                    logger.debug(command)
                    device = command['device']
                    device_cmd = command['command']
                    get_response = command['response']
                    logger.debug(device_cmd)


                    try:
                        if device == 'server':
                            logger.debug("For device %s, processing cmd '%s' with args: %s and kwargs: %s ",
                                device, device_cmd[0], ', '.join(['{}'.format(a) for a in device_cmd[1]]),
                                ', '.join(['{}:{}'.format(kw, item) for kw, item in device_cmd[2].items()]))

                            if device_cmd[0] == 'ping':
                                answer = 'ping received'
                            else:
                                answer = ''

                        elif device.endswith('status'):
                            cmd_device = device.rstrip('_status')
                            status_cmd = device_cmd[0]
                            status_period = device_cmd[1]
                            add = device_cmd[2]

                            thread = self._device_control[cmd_device]['thread']

                            if add:
                                thread.add_status_cmd(status_cmd, status_period)
                            else:
                                thread.remove_status_cmd(status_cmd)

                        else:
                            if get_response:
                                answer_q = self._device_control[device]['answer_q']
                                answer_q.clear()

                            logger.debug("For device %s, processing cmd '%s' with args: %s and kwargs: %s ",
                                device, device_cmd[0], ', '.join(['{}'.format(a) for a in device_cmd[1]]),
                                ', '.join(['{}:{}'.format(kw, item) for kw, item in device_cmd[2].items()]))

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
                            answer = ['response', answer]
                            logger.debug('Sending command response: %s', answer)
                            self.socket.send_pyobj(answer, protocol=2)

                    except Exception:
                        device = command['device']
                        device_cmd = command['command']
                        msg = ("Device %s failed to run command '%s' "
                            "with args: %s and kwargs: %s. Exception follows:" %(device, device_cmd[0],
                            ', '.join(['{}'.format(a) for a in device_cmd[1]]),
                            ', '.join(['{}:{}'.format(kw, item) for kw, item in device_cmd[2].items()])))
                        logger.exception(msg)
                        logger.exception(traceback.print_exc())

                for device, device_ctrl in self._device_control.items():
                    if 'status_q' in device_ctrl:
                        status_q = device_ctrl['status_q']

                        if len(status_q) > 5:
                            temp = []
                            for i in range(5):
                                temp.append(status_q.pop())

                            temp = temp[::-1]
                            status_q.clear()

                            for a in temp:
                                status_q.append(a)

                        if len(status_q) > 0:
                            cmds_run =  True

                            status = status_q.popleft()

                            status = ['status', status]
                            logger.debug('Sending status: %s', status)
                            self.socket.send_pyobj(status, protocol=2)


                if not cmds_run:
                    time.sleep(0.01)

            except Exception:
                logger.error('Error in server thread:\n{}'.format(traceback.format_exc()))

        self.socket.unbind("tcp://{}:{}".format(self.ip, self.port))
        self.socket.close(0)
        self.context.destroy(0)

        for device, device_ctrl in self._device_control.items():
            if 'abort' in device_ctrl:
                device_ctrl['abort'].set()

            else:
                device_ctrl['thread'].stop()
                device_ctrl['thread'].join()

        if self._stop_event.is_set():
            self._stop_event.clear()

        logger.info("Quitting control thread: %s", self.name)

    def add_comm_to_thread(self, device, name, cmd_q, return_q, status_q):
        thread = self._device_control[device]['thread']

        thread.add_new_communication(name, cmd_q, return_q, status_q)

    def remove_comm_from_thread(self, device, name):
        thread = self._device_control[device]['thread']

        thread.remove_communication(name)

    def get_comm_thread(self, device):
        return self._device_control[device]['thread']

    def stop(self):
        """Stops the thread cleanly."""
        # logger.info("Starting to clean up and shut down pump control thread: %s", self.name)

        self._stop_event.set()

if __name__ == '__main__':
    logger = logging.getLogger()
    logger.setLevel(logging.DEBUG)
    h1 = logging.StreamHandler(sys.stdout)
    h1.setLevel(logging.DEBUG)
    h1.setLevel(logging.INFO)
    formatter = logging.Formatter('%(asctime)s - %(name)s - %(threadName)s - %(levelname)s - %(message)s')
    h1.setFormatter(formatter)
    logger.addHandler(h1)

    app = wx.App()

    standard_paths = wx.StandardPaths.Get() #Can't do this until you start the wx app
    info_dir = standard_paths.GetUserLocalDataDir()
    print('Log directory: {}'.format(info_dir))
    if not os.path.exists(info_dir):
        os.mkdir(info_dir)

    h2 = handlers.RotatingFileHandler(os.path.join(info_dir, 'biocon_server.log'),
        maxBytes=100e6, backupCount=20, delay=True)
    h2.setLevel(logging.DEBUG)
    formatter2 = logging.Formatter('%(asctime)s - %(name)s - %(threadName)s - %(levelname)s - %(message)s')
    h2.setFormatter(formatter2)

    logger.addHandler(h2)

    port1 = '5556'
    port2 = '5557'
    port3 = '5558'
    port4 = '5559'

    exp_type = 'coflow' #coflow or trsaxs_laminar or trsaxs_chaotic
    # exp_type = 'trsaxs_chaotic'
    # exp_type = 'trsaxs_laminar'


    if exp_type == 'coflow':
        # Coflow

        ip = '164.54.204.53'
        # ip = '164.54.204.24'

        setup_pumps = [
            {'name': 'sheath', 'args': ['VICI M50', 'COM3'],
                'kwargs': {'flow_cal': '627.72', 'backlash_cal': '9.814'},
                'ctrl_args': {'flow_rate': 1}},
            {'name': 'outlet', 'args': ['VICI M50', 'COM4'],
                'kwargs': {'flow_cal': '628.68', 'backlash_cal': '9.962'},
                'ctrl_args': {'flow_rate': 1}},
            ]

        # ob1_comm_lock = threading.RLock()

        # setup_pumps = [
        #     {'name': 'sheath', 'args': ['VICI M50', 'COM3'],
        #         'kwargs': {'flow_cal': '627.72', 'backlash_cal': '9.814'},
        #         'ctrl_args': {'flow_rate': 1}},
        #     {'name': 'outlet', 'args': ['OB1 Pump', 'COM8'],
        #         'kwargs': {'ob1_device_name': 'Outlet OB1', 'channel': 1,
        #         'min_pressure': -1000, 'max_pressure': 1000, 'P': 5, 'I': 0.00015,
        #         'D': 0, 'bfs_instr_ID': None, 'comm_lock': ob1_comm_lock,
        #         'calib_path': './resources/ob1_calib.txt'},
        #         'ctrl_args': {}}
        #     ]

        setup_valves = [
            {'name': 'Coflow Sheath', 'args': ['Cheminert', 'COM7'],
                'kwargs': {'positions' : 10}},
            ]

        setup_uv = [
            {'name': 'CoflowUV', 'args': ['StellarNet', None], 'kwargs':
            {'shutter_pv_name': '18ID:LJT4:2:DO11',
            'trigger_pv_name' : '18ID:LJT4:2:DO12'}},
            ]

        setup_fms = [
            {'name': 'sheath', 'args' : ['BFS', 'COM5'], 'kwargs': {}},
            {'name': 'outlet', 'args' : ['BFS', 'COM6'], 'kwargs': {}}
            ]

        # # Simulated devices for testing

        # setup_pumps = [
        #     {'name': 'sheath', 'args': ['Soft', None], 'kwargs': {}},
        #     {'name': 'outlet', 'args': ['Soft', None], 'kwargs': {}},
        #     ]

        # setup_valves = [
        #     {'name': 'Coflow Sheath', 'args': ['Soft', None], 'kwargs':
        #         {'positions': 10}},
        #     ]

        # setup_fms = [
        #     {'name': 'sheath', 'args': ['Soft', None], 'kwargs': {}},
        #     {'name': 'outlet', 'args': ['Soft', None], 'kwargs': {}},
        #     ]

    elif exp_type.startswith('trsaxs'):
        # TR SAXS

        ip = '164.54.204.8'
        # ip = '164.54.204.24'

        if exp_type == 'trsaxs_chaotic':
            # Chaotic flow

            setup_pumps = [
                {'name': 'Buffer 1', 'args': ['SSI Next Gen', 'COM17'],
                    'kwargs': {'flow_rate_scale': 1.0478,
                    'flow_rate_offset': -72.82/1000,'scale_type': 'up'},
                    'ctrl_args': {'flow_rate': 0.1, 'flow_accel': 0.0,
                    'max_pressure': 1800}},
                {'name': 'Sample', 'args': ['SSI Next Gen', 'COM15'],
                    'kwargs': {'flow_rate_scale': 1.0204,
                    'flow_rate_offset': 15.346/1000,'scale_type': 'up'},
                    'ctrl_args': {'flow_rate': 0.1, 'flow_accel': 0.0,
                    'max_pressure': 1500}},
                {'name': 'Buffer 2', 'args': ['SSI Next Gen', 'COM18'],
                    'kwargs': {'flow_rate_scale': 1.0179,
                    'flow_rate_offset': -20.842/10000,'scale_type': 'up'},
                    'ctrl_args': {'flow_rate': 0.1, 'flow_accel': 0.0,
                    'max_pressure': 1800}},
                ]

            setup_valves = [
                {'name': 'Injection', 'args': ['Rheodyne', 'COM6'],
                    'kwargs': {'positions' : 2}},
                ]

            setup_fms = [
                {'name': 'outlet', 'args' : ['BFS', 'COM5'], 'kwargs': {}}
                ]


            # # Simulated device, for testing purposes

            # setup_fms = [
            #     {'name': 'outlet', 'args' : ['Soft', None], 'kwargs': {}},
            #     ]

            # # # Syringe pumps
            # # setup_pumps = [
            # #     {'name': 'Buffer 1', 'args': ['Soft Syringe', None],
            # #         'kwargs': {'syringe_id': '20 mL, Medline P.C.', 'flow_rate': 1,
            # #             'refill_rate': 10}},
            # #     {'name': 'Sample', 'args': ['Soft Syringe', None],
            # #         'kwargs': {'syringe_id': '10 mL, Medline P.C.', 'flow_rate': 1,
            # #             'refill_rate': 10}},
            # #     {'name': 'Buffer 2', 'args': ['Soft Syringe', None],
            # #         'kwargs': {'syringe_id': '20 mL, Medline P.C.', 'flow_rate': 1,
            # #             'refill_rate': 10}},
            # #     ]


            # # setup_valves = [
            # #     {'name': 'Injection', 'args': ['Soft', None],
            # #         'kwargs': {'positions': 2}},
            # #     {'name': 'Buffer 1', 'args': ['Soft', None],
            # #         'kwargs': {'positions': 6}},
            # #     {'name': 'Sample', 'args': ['Soft', None],
            # #         'kwargs': {'positions': 6}},
            # #     {'name': 'Buffer 2', 'args': ['Soft', None],
            # #         'kwargs': {'positions': 6}},
            # #     ]

            # # Continuous flow pumps
            # setup_pumps = [
            #     {'name': 'Buffer 1', 'args': ['Soft', None], 'kwargs': {}},
            #     {'name': 'Sample', 'args': ['Soft', None], 'kwargs': {}},
            #     {'name': 'Buffer 2', 'args': ['Soft', None], 'kwargs': {}},
            #     ]

            # setup_valves = [
            #     {'name': 'Injection', 'args': ['Soft', None],
            #         'kwargs': {'positions': 2}},
            #     ]


        elif exp_type == 'trsaxs_laminar':
            # Laminar flow
            setup_pumps = [
                {'name': 'Buffer', 'args': ['Pico Plus', 'COM19'],
                    'kwargs': {'syringe_id': '10 mL, Medline P.C.',
                    'pump_address': '00'}, 'ctrl_args': {'flow_rate' : '0.068',
                    'refill_rate' : '5'}},
                {'name': 'Sheath', 'args': ['Pico Plus', 'COM18'],
                    'kwargs': {'syringe_id': '3 mL, Medline P.C.',
                    'pump_address': '00'}, 'ctrl_args': {'flow_rate' : '0.002',
                    'refill_rate' : '1.5'}},
                {'name': 'Sample', 'args': ['Pico Plus', 'COM20'],
                    'kwargs': {'syringe_id': '3 mL, Medline P.C.',
                    'pump_address': '00'}, 'ctrl_args': {'flow_rate' : '0.009',
                    'refill_rate' : '1.5'}},
                ]

            setup_valves = [
                {'name': 'Injection', 'args': ['Rheodyne', 'COM6'],
                    'kwargs': {'positions' : 2}},
                {'name': 'Buffer 1', 'args': ['Rheodyne', 'COM12'],
                    'kwargs': {'positions' : 6}},
                {'name': 'Buffer 2', 'args': ['Rheodyne', 'COM14'],
                    'kwargs': {'positions' : 6}},
                {'name': 'Sheath 1', 'args': ['Rheodyne', 'COM21'],
                    'kwargs': {'positions' : 6}},
                {'name': 'Sheath 2', 'args': ['Rheodyne', 'COM8'],
                    'kwargs': {'positions' : 6}},
                {'name': 'Sample', 'args': ['Rheodyne', 'COM17'],
                    'kwargs': {'positions' : 6}},
                ]

            setup_fms = [
                {'name': 'outlet', 'args' : ['BFS', 'COM13'], 'kwargs': {}}
                ]


            # # Simulated device, for testing purposes

            # setup_fms = [
            #     {'name': 'outlet', 'args' : ['Soft', None], 'kwargs': {}},
            #     ]

            # # Syringe pumps
            # setup_pumps = [
            #     {'name': 'Buffer', 'args': ['Soft Syringe', None],
            #         'kwargs': {'syringe_id': '3 mL, Medline P.C.'},
            #         'ctrl_args': {'flow_rate': 1, 'refill_rate': 3}},
            #     {'name': 'Sample', 'args': ['Soft Syringe', None],
            #         'kwargs': {'syringe_id': '3 mL, Medline P.C.'},
            #         'ctrl_args': {'flow_rate': 1, 'refill_rate': 3}},
            #     {'name': 'Sheath', 'args': ['Soft Syringe', None],
            #         'kwargs': {'syringe_id': '3 mL, Medline P.C.'},
            #         'ctrl_args': {'flow_rate': 1, 'refill_rate': 3}},
            #     ]

            # # Valves
            # setup_valves = [
            #     {'name': 'Injection', 'args': ['Soft', None],
            #         'kwargs': {'positions': 2}},
            #     {'name': 'Buffer 1', 'args': ['Soft', None],
            #         'kwargs': {'positions': 6}},
            #     {'name': 'Buffer 2', 'args': ['Soft', None],
            #         'kwargs': {'positions': 6}},
            #     {'name': 'Sample', 'args': ['Soft', None],
            #         'kwargs': {'positions': 6}},
            #     {'name': 'Sheath 1', 'args': ['Soft', None],
            #         'kwargs': {'positions': 6}},
            #     {'name': 'Sheath 2', 'args': ['Soft', None],
            #         'kwargs': {'positions': 6}},
            #     ]




    # Both


    control_server_pump = ControlServer(ip, port1, name='PumpControlServer',
        start_pump=True)
    control_server_pump.start()

    control_server_fm = ControlServer(ip, port2, name='FMControlServer',
        start_fm=True)
    control_server_fm.start()

    control_server_valve = ControlServer(ip, port3, name='ValveControlServer',
        start_valve=True)
    control_server_valve.start()

    time.sleep(1)

    fm_comm_thread = control_server_fm.get_comm_thread('fm')

    fm_settings = {
        'remote'        : False,
        'device_init'   : setup_fms,
        'com_thread'    : fm_comm_thread,
        }

    fm_frame = fmcon.FlowMeterFrame('FMFrame', fm_settings, parent=None,
        title='Flow Meter Control')
    fm_frame.Show()


    # if exp_type == 'coflow':
    #     # For OB1 with feedback
    #     fm_local_cmd_q = deque()
    #     fm_local_ret_q = deque()
    #     fm_local_status_q = deque()

    #     fm_comm_thread.add_new_communication('local', fm_local_cmd_q,
    #         fm_local_ret_q, fm_local_status_q)

    #     cmd = ['get_bfs_instr_id', [setup_fms[1]['name'],], {}]

    #     bfs_instr_id = utils.send_cmd(cmd, fm_local_cmd_q, fm_local_ret_q,
    #         threading.Event(), threading.Lock(), False, 'fm', True)

    #     cmd = ['start_remote', [setup_fms[1]['name'],], {}]

    #     utils.send_cmd(cmd, fm_local_cmd_q, fm_local_ret_q, threading.Event(),
    #         threading.Lock(), False, 'fm', False)

    #     fm_comm_thread.remove_communication('local')

    #     setup_pumps[1]['kwargs']['bfs_instr_ID'] = bfs_instr_id

    pump_comm_thread = control_server_pump.get_comm_thread('pump')

    pump_settings = {
        'remote'        : False,
        'device_init'   : setup_pumps,
        'com_thread'    : pump_comm_thread,
        }

    pump_frame = pumpcon.PumpFrame('PumpFrame', pump_settings, parent=None,
        title='Pump Control')
    pump_frame.Show()


    valve_comm_thread = control_server_valve.get_comm_thread('valve')

    valve_settings = {
        'remote'        : False,
        'device_init'   : setup_valves,
        'com_thread'    : valve_comm_thread,
        }

    valve_frame = valvecon.ValveFrame('valveFrame', valve_settings, parent=None,
        title='Valve Control')
    valve_frame.Show()

    if exp_type == 'coflow':
        # Coflow only
        control_server_uv = ControlServer(ip, port4, name='UVControlServer',
            start_uv=True)
        control_server_uv.start()

        time.sleep(1)
        uv_comm_thread = control_server_uv.get_comm_thread('uv')

        uv_settings = {
            'remote'        : False,
            'device_init'   : setup_uv,
            'com_thread'    : uv_comm_thread,
            'inline_panel'  : False,
            }

        uv_frame = spectrometercon.UVFrame('UVFrame', uv_settings,
            parent=None, title='UV Spectrometer Control')
        uv_frame.Show()

    app.MainLoop()

    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print('stopping stuff')
        control_server_pump.stop()
        control_server_pump.join()

        control_server_fm.stop()
        control_server_fm.join()

        control_server_valve.stop()
        control_server_valve.join()

        if exp_type == 'coflow':
            control_server_uv.stop()
            control_server_uv.join()

    logger.info("Quitting server")

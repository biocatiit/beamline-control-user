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
import argparse

if __name__ != '__main__':
    logger = logging.getLogger(__name__)

import zmq
import wx

import pumpcon
import fmcon
import valvecon
import spectrometercon
import biohplccon
import coflowcon
import utils
import autosamplercon


class ControlServer(threading.Thread):
    """

    """

    def __init__(self, ip, port, name='ControlServer', pump_comm_locks = None,
        valve_comm_locks=None, start_pump=False, start_fm=False,
        start_valve=False, start_uv=False, start_hplc=False, start_coflow=False,
        start_autosampler=False):
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
        self.ready_event = threading.Event()

        self.pump_comm_locks = pump_comm_locks
        self.valve_comm_locks = valve_comm_locks

        self._start_pump = start_pump
        self._start_fm = start_fm
        self._start_valve = start_valve
        self._start_uv = start_uv
        self._start_hplc = start_hplc
        self._start_coflow = start_coflow
        self._start_autosampler = start_autosampler

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

        if self._start_hplc:
            hplc_cmd_q = deque()
            hplc_return_q = deque()
            hplc_status_q = deque()
            hplc_con = biohplccon.HPLCCommThread('HPLCCon')
            hplc_con.start()

            hplc_con.add_new_communication('zmq_server', hplc_cmd_q, hplc_return_q,
                hplc_status_q)

            hplc_ctrl = {
                'queue'     : hplc_cmd_q,
                'answer_q'  : hplc_return_q,
                'status_q'  : hplc_status_q,
                'thread'    : hplc_con,
                }

            self._device_control['hplc'] = hplc_ctrl

        if self._start_coflow:
            coflow_cmd_q = deque()
            coflow_return_q = deque()
            coflow_status_q = deque()
            coflow_con = coflowcon.CoflowCommThread('CoflowCon')
            coflow_con.start()

            coflow_con.add_new_communication('zmq_server', coflow_cmd_q,
                coflow_return_q, coflow_status_q)

            coflow_ctrl = {
                'queue'     : coflow_cmd_q,
                'answer_q'  : coflow_return_q,
                'status_q'  : coflow_status_q,
                'thread'    : coflow_con,
                }

            self._device_control['coflow'] = coflow_ctrl

        if self._start_autosampler:
            autosampler_cmd_q = deque()
            autosampler_return_q = deque()
            autosampler_status_q = deque()
            autosampler_con = autosamplercon.ASCommThread('AutosamplerCon')
            autosampler_con.start()

            autosampler_con.add_new_communication('zmq_server', autosampler_cmd_q,
                autosampler_return_q, autosampler_status_q)

            autosampler_ctrl = {
                'queue'     : autosampler_cmd_q,
                'answer_q'  : autosampler_return_q,
                'status_q'  : autosampler_status_q,
                'thread'    : autosampler_con,
                }

            self._device_control['autosampler'] = autosampler_ctrl

        self.ready_event.set()

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
                                got_answer = False

                                while not got_answer:
                                    if time.time()-start_time > 5:
                                        break

                                    if len(answer_q) != 0:
                                        answer = answer_q.popleft()

                                        if answer[0] == device_cmd[1][0] and answer[1] == device_cmd[0]:
                                            got_answer = True

                                    else:
                                        time.sleep(0.01)

                                if not got_answer:
                                    answer = ''

                            else:
                                answer = 'cmd sent'

                        if answer == '':
                            logger.exception('No response received from device '
                                '%s to cmd %s', device, device_cmd[0])
                            answer = ['response', [device_cmd[1][0], device_cmd[0], None]]
                        else:
                            answer = ['response', answer]
                            logger.debug('Sending command response: %s', answer)

                        self.socket.send_pyobj(answer, protocol=2, flags=zmq.NOBLOCK)

                    except zmq.ZMQError:
                        err = traceback.format_exc()
                        if not 'Resource temporarily unavailable' in err:
                            logger.error('Error in server thread:\n{}'.format(traceback.format_exc()))

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
                            self.socket.send_pyobj(status, protocol=2, flags=zmq.NOBLOCK)

                if not cmds_run:
                    time.sleep(0.01)

            except zmq.ZMQError:
                err = traceback.format_exc()
                if not 'Resource temporarily unavailable' in err:
                    logger.error('Error in server thread:\n{}'.format(traceback.format_exc()))

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
    parser = argparse.ArgumentParser()
    parser.add_argument('exp_type', help='Experiment type for server',
        type=str, choices=['coflow', 'trsaxs_chaotic', 'trsaxs_laminar',
        'hplc', 'autosampler'])

    args = parser.parse_args()
    exp_type = args.exp_type


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

    if not os.path.exists(info_dir):
        os.mkdir(info_dir)

    h2 = handlers.RotatingFileHandler(os.path.join(info_dir, 'biocon_server_{}.log'.format(exp_type)),
        maxBytes=100e6, backupCount=20, delay=True)
    h2.setLevel(logging.DEBUG)
    formatter2 = logging.Formatter('%(asctime)s - %(name)s - %(threadName)s - %(levelname)s - %(message)s')
    h2.setFormatter(formatter2)

    logger.addHandler(h2)

    print('Log directory: {}'.format(info_dir))

    port1 = '5556'
    port2 = '5557'
    port3 = '5558'
    port4 = '5559'

    if exp_type == 'coflow':
        # Coflow

        has_uv = True
        # has_uv = False

        ip = '164.54.204.53'
        # ip = '164.54.204.192'
        # ip = '164.54.204.24'

        spectrometer_settings = spectrometercon.default_spectrometer_settings
        spectrometer_settings['remote'] = False
        spectrometer_settings['device_communication'] = 'local'
        spectrometer_settings['inline_panel'] = False
        spectrometer_settings['plot_refresh_t'] = 1

        coflow_settings = coflowcon.default_coflow_settings

        ob1_comm_lock = threading.RLock()
        outlet_fm_comm_lock = threading.Lock()
        coflow_settings['device_communication'] = 'local'
        coflow_settings['device_init'][0]['kwargs']['outlet_pump']['kwargs']['comm_lock'] = ob1_comm_lock
        coflow_settings['device_init'][0]['kwargs']['outlet_fm']['kwargs']['comm_lock'] = outlet_fm_comm_lock
        coflow_settings['components'] = ['coflow',]

    elif exp_type.startswith('trsaxs'):
        # TR SAXS

        ip = '164.54.204.8'
        # ip = '164.54.204.24'

        if exp_type == 'trsaxs_chaotic':
            # Chaotic flow

            # Teledyne SSI Reaxus pumps with scaling
            setup_pumps = [
                {'name': 'Buffer 2', 'args': ['SSI Next Gen', 'COM14'],
                    'kwargs': {'flow_rate_scale': 1.0583,
                    'flow_rate_offset': -33.462/1000,'scale_type': 'up'},
                    'ctrl_args': {'flow_rate': 0.1, 'flow_accel': 0.1}},
                {'name': 'Sample', 'args': ['SSI Next Gen', 'COM17'],
                    'kwargs': {'flow_rate_scale': 1.0135,
                    'flow_rate_offset': 5.1251/1000,'scale_type': 'up'},
                    'ctrl_args': {'flow_rate': 0.1, 'flow_accel': 0.1}},
                {'name': 'Buffer 1', 'args': ['SSI Next Gen', 'COM18'],
                    'kwargs': {'flow_rate_scale': 1.0497,
                    'flow_rate_offset': -34.853/1000,'scale_type': 'up'},
                    'ctrl_args': {'flow_rate': 0.1, 'flow_accel': 0.1}},
                 ]

            setup_valves = [
                # {'name': 'Injection', 'args': ['Rheodyne', 'COM6'],
                #     'kwargs': {'positions' : 2}},
                {'name': 'Injection', 'args': ['RheodyneTTL', '18ID:LJT4:2:Bo14'],
                    'kwargs': {'positions' : 2}},
                # {'name': 'Injection 2', 'args': ['RheodyneTTL', '18ID:LJT4:2:Bo14'],
                #     'kwargs': {'positions' : 2}},
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
                {'name': 'Sheath', 'args': ['Pico Plus', 'COM7'],
                    'kwargs': {'syringe_id': '1 mL, Medline P.C.',
                    'pump_address': '00', 'dual_syringe': 'False'},
                    'ctrl_args': {'flow_rate' : '0.002', 'refill_rate' : '1.0'}},
                {'name': 'Sample', 'args': ['Pico Plus', 'COM9'],
                    'kwargs': {'syringe_id': '1 mL, Medline P.C.',
                    'pump_address': '00', 'dual_syringe': 'False'},
                    'ctrl_args': {'flow_rate' : '0.009', 'refill_rate' : '1.0'}},
                 {'name': 'Buffer', 'args': ['Pico Plus', 'COM11'],
                    'kwargs': {'syringe_id': '3 mL, Medline P.C.',
                    'pump_address': '00', 'dual_syringe': 'False'},
                    'ctrl_args': {'flow_rate' : '0.068', 'refill_rate' : '3'}},
                ]

            setup_valves = [
                {'name': 'Injection', 'args': ['RheodyneTTL', '18ID:LJT4:2:Bo14'],
                    'kwargs': {'positions' : 2}},
                # {'name': 'Injection', 'args': ['Rheodyne', 'COM6'],
                #     'kwargs': {'positions' : 2}},
                {'name': 'Sheath 1', 'args': ['Rheodyne', 'COM21'],
                    'kwargs': {'positions' : 6}},
                {'name': 'Sheath 2', 'args': ['Rheodyne', 'COM8'],
                    'kwargs': {'positions' : 6}},
                {'name': 'Sample', 'args': ['Rheodyne', 'COM3'],
                    'kwargs': {'positions' : 6}},
                {'name': 'Buffer 1', 'args': ['Rheodyne', 'COM10'],
                    'kwargs': {'positions' : 6}},
                {'name': 'Buffer 2', 'args': ['Rheodyne', 'COM4'],
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

    elif exp_type == 'hplc':
        # HPLC control

        ip = '164.54.204.113' # Dual pump system

        hplc_settings = biohplccon.default_hplc_2pump_settings

    elif exp_type == 'autosampler':
        # Autosampler control

        ip = '164.54.204.53' # Coflow laptop

        as_settings = autosamplercon.default_autosampler_settings
        as_settings['device_communication'] = 'local'
        as_settings['components'] = ['autosampler',]

        as_settings['device_init'][0]['kwargs']['needle_valve']['comm_lock'] = threading.RLock()
        as_settings['device_init'][0]['kwargs']['sample_pump']['comm_lock'] = threading.RLock()
        as_settings['device_init'][0]['kwargs']['clean1_pump']['comm_lock'] = threading.RLock()
        as_settings['device_init'][0]['kwargs']['clean2_pump']['comm_lock'] = threading.RLock()
        as_settings['device_init'][0]['kwargs']['clean3_pump']['comm_lock'] = threading.RLock()


    if exp_type == 'coflow':

        control_server_coflow = ControlServer(ip, port1, name='CoflowControlServer',
            start_coflow=True)
        control_server_coflow.start()
        control_server_coflow.ready_event.wait()

        coflow_comm_thread = control_server_coflow.get_comm_thread('coflow')

        coflow_settings['com_thread'] = coflow_comm_thread

        coflow_frame = coflowcon.CoflowFrame('CoflowFrame', coflow_settings, parent=None,
            title='Coflow Control')
        coflow_frame.Show()

        if has_uv:
            # Coflow only
            control_server_uv = ControlServer(ip, port4, name='UVControlServer',
                start_uv=True)
            control_server_uv.start()
            control_server_uv.ready_event.wait()

            uv_comm_thread = control_server_uv.get_comm_thread('uv')

            spectrometer_settings['com_thread'] = uv_comm_thread

            uv_frame = spectrometercon.UVFrame('UVFrame', spectrometer_settings,
                parent=None, title='UV Spectrometer Control')
            uv_frame.Show()


    elif exp_type.startswith('trsaxs'):
        control_server_pump = ControlServer(ip, port1, name='PumpControlServer',
            start_pump=True)
        control_server_pump.start()

        control_server_fm = ControlServer(ip, port2, name='FMControlServer',
            start_fm=True)
        control_server_fm.start()

        control_server_valve = ControlServer(ip, port3, name='ValveControlServer',
            start_valve=True)
        control_server_valve.start()

        control_server_pump.ready_event.wait()
        control_server_fm.ready_event.wait()
        control_server_valve.ready_event.wait()

        fm_comm_thread = control_server_fm.get_comm_thread('fm')

        fm_settings = {
            'remote'        : False,
            'device_init'   : setup_fms,
            'com_thread'    : fm_comm_thread,
            }

        fm_frame = fmcon.FlowMeterFrame('FMFrame', fm_settings, parent=None,
            title='Flow Meter Control')
        fm_frame.Show()

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

    elif exp_type == 'hplc':
        control_server_hplc = ControlServer(ip, port1, name='HPLCControlServer',
            start_hplc=True)
        control_server_hplc.start()
        control_server_hplc.ready_event.wait()

        hplc_comm_thread = control_server_hplc.get_comm_thread('hplc')

        hplc_settings['remote'] = False
        hplc_settings['com_thread'] = hplc_comm_thread

        hplc_frame = biohplccon.HPLCFrame('HPLCFrame', hplc_settings, parent=None,
            title='HPLC Control')
        hplc_frame.Show()

    elif exp_type == 'autosampler':
        control_server_as = ControlServer(ip, port2, name='AuotsamplerControlServer',
            start_autosampler=True)
        control_server_as.start()
        control_server_as.ready_event.wait()

        as_comm_thread = control_server_as.get_comm_thread('autosampler')

        as_settings['remote'] = False
        as_settings['com_thread'] = as_comm_thread

        as_frame = autosamplercon.AutosamplerFrame('AutosamplerFrame', as_settings,
            parent=None, title='Autosampler Control')
        as_frame.Show()


    app.MainLoop()

    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        if exp_type == 'coflow':
            control_server_coflow.stop()
            control_server_coflow.join(5)

            if has_uv:
                control_server_uv.stop()
                control_server_uv.join(5)

        elif exp_type.startswith('trsaxs'):
            control_server_pump.stop()
            control_server_pump.join(5)

            control_server_fm.stop()
            control_server_fm.join(5)

            control_server_valve.stop()
            control_server_valve.join(5)

        elif exp_type == 'hplc':
            control_server_hplc.stop()
            control_server_hplc.join(5)

        elif exp_type == 'autosampler':
            control_server_as.stop()
            control_server_as.join(5)

    logger.info("Quitting server")

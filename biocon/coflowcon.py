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
import time
from collections import deque, OrderedDict
import logging
import sys
import copy
import platform
import requests

if __name__ != '__main__':
    logger = logging.getLogger(__name__)

import wx
import matplotlib
from matplotlib.backends.backend_wxagg import FigureCanvasWxAgg
from matplotlib.figure import Figure
try:
    import epics.wx
except Exception:
    pass

matplotlib.rcParams['backend'] = 'WxAgg'


import client
import pumpcon
import fmcon
import valvecon
import utils

class CoflowControl(object):

    def __init__(self, name, device, settings={}):
        self.name = name
        self.device = device

        self.settings = settings

        self.coflow_on = False

        self.connected = False

        self.monitor = False
        self.sheath_setpoint = None
        self.outlet_setpoint = None
        self.lc_flow_rate = float(self.settings['lc_flow_rate'])
        self._starting_flow_rate = self.lc_flow_rate

        self.sheath_is_moving = False
        self.outlet_is_moving = False

        self.pump_sheath_init = False
        self.pump_outlet_init = False
        self.fm_sheath_init = False
        self.fm_outlet_init = False
        self.valve_sheath_init = False

        self.sheath_fr_mult = settings['sheath_fr_mult']
        self.outlet_fr_mult = settings['outlet_fr_mult']

        self._buffer_monitor = utils.BufferMonitor(self._get_buffer_monitor_flow_rate)

        self._buffer_change_seq = []
        self._buffer_change_remain = 0
        self._changing_buffer = False
        self._abort_change_buffer = threading.Event()
        self._monitor_change_buffer_evt = threading.Event()
        self._terminate_monitor_change_buffer = threading.Event()
        self._monitor_change_buffer_thread = threading.Thread(
            target=self._monitor_change_buffer)
        self._monitor_change_buffer_thread.daemon = True
        self._monitor_change_buffer_thread.start()

        self._remaining_flow_time = 0
        self._flow_timer = False
        self._abort_flow_timer = threading.Event()
        self._monitor_flow_timer_evt = threading.Event()
        self._terminate_monitor_flow_timer = threading.Event()
        self._monitor_flow_timer_thread = threading.Thread(
            target=self._monitor_flow_timer)
        self._monitor_flow_timer_thread.daemon = True
        self._monitor_flow_timer_thread.start()

        self.get_plot_data_lock = threading.Lock()
        self.sheath_fr_list = deque(maxlen=10000)
        self.outlet_fr_list = deque(maxlen=10000)
        self.sheath_density_list = deque(maxlen=4800)
        self.outlet_density_list = deque(maxlen=4800)
        self.sheath_t_list = deque(maxlen=4800)
        self.outlet_t_list = deque(maxlen=4800)
        self.fr_time_list = deque(maxlen=10000)
        self.aux_time_list = deque(maxlen=4800)
        self.new_sheath_fr_list = deque(maxlen=10000)
        self.new_outlet_fr_list = deque(maxlen=10000)
        self.new_sheath_density_list = deque(maxlen=4800)
        self.new_outlet_density_list = deque(maxlen=4800)
        self.new_sheath_t_list = deque(maxlen=4800)
        self.new_outlet_t_list = deque(maxlen=4800)
        self.new_fr_time_list = deque(maxlen=10000)
        self.new_aux_time_list = deque(maxlen=4800)
        self._sheath_oob_error = False
        self._sheath_oob_flow = -1
        self._outlet_oob_error = False
        self._outlet_oob_flow = -1
        self._sheath_air_error = False
        self._outlet_air_error = False

        self.timeout_event = threading.Event()

        self._terminate_monitor_flow = threading.Event()
        self._monitor_flow_thread = threading.Thread(
            target=self._monitor_flow)
        self._monitor_flow_thread.daemon = True


    def connect(self):
        if not self.connected:
            self.init_connections()

            if not self.timeout_event.is_set():
                self.init_fms()
                self.init_pumps()
                self.init_valves()

            if self.settings['use_overflow_control']:
                self.overflow_connected = True
                self.session = requests.Session()

            if (not self.timeout_event.is_set()
                and self.pump_sheath_init
                and self.pump_outlet_init
                and self.fm_sheath_init
                and self.fm_outlet_init
                and self.valve_sheath_init):
                self.connected = True
                self._monitor_flow_thread.start()

        return self.connected

    def init_connections(self):
        self.coflow_pump_cmd_q = deque()
        self.coflow_pump_return_q = deque()
        self.coflow_pump_status_q = deque()
        self.coflow_pump_abort_event = threading.Event()
        self.coflow_pump_return_lock = threading.Lock()

        self.coflow_fm_cmd_q = deque()
        self.coflow_fm_return_q = deque()
        self.coflow_fm_status_q = deque()
        self.coflow_fm_abort_event = threading.Event()
        self.coflow_fm_return_lock = threading.Lock()

        self.valve_cmd_q = deque()
        self.valve_return_q = deque()
        self.valve_status_q = deque()
        self.valve_abort_event = threading.Event()
        self.valve_return_lock = threading.Lock()

        if self.settings['device_communication'] == 'local':
            self.coflow_pump_con = pumpcon.PumpCommThread('PumpCon')
            self.coflow_pump_con.add_new_communication('coflow_control',
                self.coflow_pump_cmd_q, self.coflow_pump_return_q,
                self.coflow_pump_status_q)

            self.coflow_fm_con = fmcon.FlowMeterCommThread('FMCon')
            self.coflow_fm_con.add_new_communication('coflow_control',
                self.coflow_fm_cmd_q, self.coflow_fm_return_q,
                self.coflow_fm_status_q)

            self.coflow_valve_con = valvecon.ValveCommThread('ValveCon')
            self.coflow_valve_con.add_new_communication('coflow_control',
                self.valve_cmd_q, self.valve_return_q,
                self.valve_status_q)

            self.local_devices = True

        else:
            pump_ip = self.settings['remote_pump_ip']
            pump_port = self.settings['remote_pump_port']
            self.coflow_pump_con = client.ControlClient(pump_ip, pump_port,
                self.coflow_pump_cmd_q, self.coflow_pump_return_q,
                self.coflow_pump_abort_event, self.timeout_event,
                name='PumpControlClient', status_queue=self.coflow_pump_status_q)

            fm_ip = self.settings['remote_fm_ip']
            fm_port = self.settings['remote_fm_port']
            self.coflow_fm_con = client.ControlClient(fm_ip, fm_port,
                self.coflow_fm_cmd_q, self.coflow_fm_return_q,
                self.coflow_fm_abort_event, self.timeout_event,
                name='FMControlClient', status_queue=self.coflow_fm_status_q)

            valve_ip = self.settings['remote_valve_ip']
            valve_port = self.settings['remote_valve_port']
            self.coflow_valve_con = client.ControlClient(valve_ip, valve_port,
                self.valve_cmd_q, self.valve_return_q,
                self.valve_abort_event, self.timeout_event,
                name='ValveControlClient', status_queue=self.valve_status_q)

            self.local_devices = False

        self.coflow_pump_con.start()
        self.coflow_fm_con.start()
        self.coflow_valve_con.start()

    def init_pumps(self):

        sheath_pump = self.settings['sheath_pump']
        self.sheath_pump_name = sheath_pump['name']
        sheath_args = copy.copy(sheath_pump['args'])
        sheath_kwargs = sheath_pump['kwargs']
        sheath_args.insert(0, self.sheath_pump_name)
        sheath_connect_cmd = ['connect', sheath_args, sheath_kwargs]

        outlet_pump = self.settings['outlet_pump']
        self.outlet_pump_name = outlet_pump['name']
        outlet_args = copy.copy(outlet_pump['args'])
        outlet_kwargs = outlet_pump['kwargs']
        outlet_args.insert(0, self.outlet_pump_name)
        outlet_connect_cmd = ['connect', outlet_args, outlet_kwargs]

        if self.local_devices:
            if outlet_args[1] == 'OB1 Pump':
                fr_cmd = ['get_bfs_instr_id', [self.settings['outlet_fm']['name'],], {}]
                bfs_instr_id = self._send_fmcmd(fr_cmd, True)
                outlet_kwargs['bfs_instr_ID'] = bfs_instr_id
                outlet_kwargs['fm_comm_lock'] = self.settings['outlet_fm']['kwargs']['comm_lock']

        logger.info('Initializing coflow pumps on startup')

        self.pump_sheath_init = self._send_pumpcmd(sheath_connect_cmd, response=True)

        if self.pump_sheath_init is None:
            self.pump_sheath_init = False

        self.pump_outlet_init = self._send_pumpcmd(outlet_connect_cmd, response=True)

        if self.pump_outlet_init is None:
            self.pump_outlet_init = False

        if self.pump_outlet_init and self.pump_sheath_init:

            self._send_pumpcmd(('set_units', (self.sheath_pump_name,
                self.settings['flow_units']), {}))
            self._send_pumpcmd(('set_units', (self.outlet_pump_name,
                self.settings['flow_units']), {}))

            self.sheath_is_moving = self._send_pumpcmd(('is_moving',
                (self.sheath_pump_name,), {}), response=True)
            self.outlet_is_moving = self._send_pumpcmd(('is_moving',
                (self.outlet_pump_name,), {}), response=True)

        logger.info('Coflow pumps initialization successful')

    def init_fms(self):
        """
        Initializes the flow meters
        """

        self._sheath_flow_rate = 0

        sheath_fm = self.settings['sheath_fm']
        self.sheath_fm_name = sheath_fm['name']
        sheath_args = copy.copy(sheath_fm['args'])
        sheath_kwargs = sheath_fm['kwargs']
        sheath_args.insert(0, self.sheath_fm_name)
        sheath_connect_cmd = ['connect', sheath_args, sheath_kwargs]

        outlet_fm = self.settings['outlet_fm']
        self.outlet_fm_name = outlet_fm['name']
        outlet_args = copy.copy(outlet_fm['args'])
        outlet_kwargs = outlet_fm['kwargs']
        if 'comm_lock' not in outlet_kwargs:
            outlet_kwargs['comm_lock'] = threading.Lock()

        outlet_args.insert(0, self.outlet_fm_name)
        outlet_connect_cmd = ['connect', outlet_args, outlet_kwargs]

        logger.info('Initializing coflow flow meters on startup')

        self.fm_sheath_init = self._send_fmcmd(sheath_connect_cmd, response=True)

        if self.fm_sheath_init is None:
            self.fm_sheath_init = False

        self.fm_outlet_init = self._send_fmcmd(outlet_connect_cmd, response=True)

        if self.fm_outlet_init is None:
            self.fm_outlet_init = False

        if self.fm_outlet_init and self.fm_sheath_init:
            self._send_fmcmd(('set_units', (self.sheath_fm_name,
                self.settings['flow_units']), {}))
            self._send_fmcmd(('set_units', (self.outlet_fm_name,
                self.settings['flow_units']), {}))

            self._update_sheath_density()
            self._update_outlet_density()

            self._update_sheath_temperature()
            self._update_outlet_temperature()

            self._update_sheath_flow_rate()
            self._update_outlet_flow_rate()

            # self._send_fmcmd(('get_density', (self.sheath_fm_name,), {}), True)
            # self._send_fmcmd(('get_density', (self.outlet_fm_name,), {}), True)

            # self._send_fmcmd(('get_temperature', (self.sheath_fm_name,), {}), True)
            # self._send_fmcmd(('get_temperature', (self.outlet_fm_name,), {}), True)

            # self._send_fmcmd(('get_flow_rate', (self.sheath_fm_name,), {}), True)
            # self._send_fmcmd(('get_flow_rate', (self.outlet_fm_name,), {}), True)

            logger.info('Coflow flow meters initialization successful')

    def init_valves(self):
        """
        Initializes the valves
        """

        sheath_valve = self.settings['sheath_valve']
        self.sheath_valve_name = sheath_valve['name']
        sheath_args = copy.copy(sheath_valve['args'])
        sheath_kwargs = sheath_valve['kwargs']
        sheath_args.insert(0, self.sheath_valve_name)
        sheath_connect_cmd = ['connect', sheath_args, sheath_kwargs]

        logger.info('Initializing coflow valves on statrtup')

        self.valve_sheath_init = self._send_valvecmd(sheath_connect_cmd,
            response=True)

        if self.valve_sheath_init is None:
            self.valve_sheath_init = False

        if self.valve_sheath_init:
            self._update_sheath_valve_position()
            logger.info('Valve initializiation successful.')

    def start_overflow(self):
        logger.info('Turning on overflow pump')
        ip = self.settings['remote_overflow_ip']
        params = {'c':'1','s':'1', 'u':'user'}
        self.session.get('http://{}/?'.format(ip), params=params, timeout=5)

    def stop_overflow(self):
        logger.info('Turning off overflow pump')
        ip = self.settings['remote_overflow_ip']
        params = {'c':'1','s':'0', 'u':'user'}
        self.session.get('http://{}/?'.format(ip), params=params, timeout=5)

    def check_overflow_status(self):
        ip = self.settings['remote_overflow_ip']
        params = {'s':'2', 'u':'user'}

        err = False

        try:
            r = self.session.get('http://{}/?'.format(ip), params=params, timeout=1)
            self.overflow_connected = True

        except Exception:
            if self.overflow_connected:
                err = True

                self.overflow_connected = False

            r = None

        if r is not None:
            res = r.text
            start = res.find('<status>')
            if start != -1:
                res = res[start:]
                status = res.split(',')[0].lstrip('<status>')
                status = status.capitalize()

        else:
            status = ''

        return status, err

    def validate_flow_rate(self, lc_flow_rate):
        try:
            lc_flow_rate = float(lc_flow_rate)
            is_number = True
        except Exception:
            is_number = False
            logger.error('Flow rate is not a number')

        if is_number:
            base_units = self.settings['flow_units']
            units = 'mL/min'

            if units in ['nL/s', 'nL/min', 'uL/s', 'uL/min', 'mL/s', 'mL/min']:
                base_vu, base_tu = base_units.split('/')
                new_vu, new_tu = units.split('/')
                if base_vu != new_vu:
                    if (base_vu == 'nL' and new_vu == 'uL') or (base_vu == 'uL' and new_vu == 'mL'):
                        flow_mult = 1./1000.
                    elif base_vu == 'nL' and new_vu == 'mL':
                        flow_mult = 1./1000000.
                    elif (base_vu == 'mL' and new_vu == 'uL') or (base_vu == 'uL' and new_vu == 'nL'):
                        flow_mult = 1000.
                    elif base_vu == 'mL' and new_vu == 'nL':
                        flow_mult = 1000000.
                else:
                    flow_mult = 1.

                if base_tu != new_tu:
                    if base_tu == 'min':
                        flow_mult = flow_mult/60.
                    else:
                        flow_mult = flow_mult*60.

            lc_flow_rate = lc_flow_rate*flow_mult
            logger.debug('Flow rate mult: %f', flow_mult)
            logger.debug('Flow rate is %f %s', lc_flow_rate, units)

            if lc_flow_rate < 0.1 or lc_flow_rate > 2:
                is_extreme = True
                logger.warning('Flow rate is outside of usual range')
            else:
                is_extreme = False
        else:
            is_extreme = False

        return lc_flow_rate, is_number, is_extreme

    def start_flow(self):
        sheath_start_cmd = ('start_flow', (self.sheath_pump_name, ), {})
        outlet_start_cmd = ('start_flow', (self.outlet_pump_name, ), {})

        self._send_pumpcmd(sheath_start_cmd)
        self._send_pumpcmd(outlet_start_cmd)

        self.coflow_on = True

        logger.info('Starting coflow pumps')

    def stop_flow(self):
        sheath_stop_cmd = ('stop', (self.sheath_pump_name, ), {})
        outlet_stop_cmd = ('stop', (self.outlet_pump_name, ), {})

        self._send_pumpcmd(sheath_stop_cmd)
        self._send_pumpcmd(outlet_stop_cmd)

        self.coflow_on = False

        logger.info('Stopped coflow pumps')


    def change_flow_rate(self, flow_rate):
        self.lc_flow_rate = flow_rate

        ratio = self.settings['sheath_ratio']
        excess = self.settings['sheath_excess']

        sheath_flow = flow_rate*excess
        outlet_flow = flow_rate/(1-ratio)

        logger.info('LC flow input to %f %s', flow_rate, self.settings['flow_units'])
        logger.info('Setting sheath flow to %f %s', sheath_flow, self.settings['flow_units'])
        logger.info('Setting outlet flow to %f %s', outlet_flow, self.settings['flow_units'])

        self.sheath_setpoint = sheath_flow
        self.outlet_setpoint = outlet_flow

        sheath_flow = sheath_flow*self.sheath_fr_mult
        outlet_flow = outlet_flow*self.outlet_fr_mult

        sheath_fr_cmd = ('set_flow_rate', (self.sheath_pump_name, sheath_flow), {})
        outlet_fr_cmd = ('set_flow_rate', (self.outlet_pump_name, outlet_flow), {})

        self._send_pumpcmd(sheath_fr_cmd)
        self._send_pumpcmd(outlet_fr_cmd)

    def get_sheath_flow_rate(self):
        return copy.copy(self._sheath_flow_rate)

    def get_sheath_density(self):
        return copy.copy(self._sheath_density)

    def get_sheath_temperature(self):
        return copy.copy(self._sheath_temperature)

    def get_outlet_flow_rate(self):
        return copy.copy(self._outlet_flow_rate)

    def get_outlet_density(self):
        return copy.copy(self._outlet_density)

    def get_outlet_temperature(self):
        return copy.copy(self._outlet_temperature)

    def get_sheath_valve_position(self):
        return copy.copy(self._sheath_valve_position)

    def _update_sheath_flow_rate(self):
        sheath_fr_cmd = ('get_flow_rate', (self.sheath_fm_name,), {})

        ret = self._send_fmcmd(sheath_fr_cmd, True)
        if ret is not None:
            ret_type = 'flow_rate'
            ret_val = ret*self.sheath_fr_mult
            self._sheath_flow_rate = ret_val
        else:
            ret_type = None
            ret_val = None

        return ret_val, ret_type

    def _update_sheath_density(self):
        sheath_density_cmd = ('get_density', (self.sheath_fm_name,), {})

        ret = self._send_fmcmd(sheath_density_cmd, True)
        if ret is not None:
            ret_type = 'density'
            ret_val = ret
            self._sheath_density = ret_val
        else:
            ret_type = None
            ret_val = None

        return ret_val, ret_type

    def _update_sheath_temperature(self):
        sheath_t_cmd = ('get_temperature', (self.sheath_fm_name,), {})

        ret = self._send_fmcmd(sheath_t_cmd, True)
        if ret is not None:
            ret_type = 'temperature'
            ret_val = ret
            self._sheath_temperature = ret_val
        else:
            ret_type = None
            ret_val = None

        return ret_val, ret_type

    def _update_outlet_flow_rate(self):
        outlet_fr_cmd = ('get_flow_rate', (self.outlet_fm_name,), {})

        ret = self._send_fmcmd(outlet_fr_cmd, True)
        if ret is not None:
            ret_type = 'flow_rate'
            ret_val = ret*self.outlet_fr_mult
            self._outlet_flow_rate = ret_val
        else:
            ret_type = None
            ret_val = None

        return ret_val, ret_type

    def _update_outlet_density(self):
        outlet_density_cmd = ('get_density', (self.outlet_fm_name,), {})

        ret = self._send_fmcmd(outlet_density_cmd, True)
        if ret is not None:
            ret_type = 'density'
            ret_val = ret
            self._outlet_density = ret_val
        else:
            ret_type = None
            ret_val = None

        return ret_val, ret_type

    def _update_outlet_temperature(self):
        outlet_t_cmd = ('get_temperature', (self.outlet_fm_name,), {})

        ret = self._send_fmcmd(outlet_t_cmd, True)
        if ret is not None:
            ret_type = 'temperature'
            ret_val = ret
            self._outlet_temperature = ret_val
        else:
            ret_type = None
            ret_val = None

        return ret_val, ret_type

    def _update_sheath_valve_position(self):
        get_sheath_valve_position_cmd = ('get_position',
            (self.sheath_valve_name,), {})

        position = self._send_valvecmd(get_sheath_valve_position_cmd, True)

        self._sheath_valve_position = position

        self.set_active_buffer_position(position)

        return position

    def set_sheath_valve_position(self, position):
        set_sheath_valve_position_cmd = ('set_position',
            (self.sheath_valve_name, position), {})

        ret = self._send_valvecmd(set_sheath_valve_position_cmd, True)

        if ret is not None:
            if ret:
                logger.info('Set {} position to {}'.format('sheath_valve', position))
                success = True
            else:
                logger.error('Failed to set {} position'.format('sheath_valve'))
                success = False

        else:
            logger.error('Failed to set {} position, no response from the '
                'server.'.format(ret[1].replace('_', ' ')))
            success = False

        if success:
            self.set_active_buffer_position(position)

        return success

    def get_buffer_change_status(self):
        return copy.copy(self._changing_buffer)

    def get_buffer_change_time_remaining(self):
        return copy.copy(self._buffer_change_remain)

    def change_buffer(self, buffer_change_seq):
        logger.info('Changing buffer')
        self._buffer_change_seq = buffer_change_seq
        excess = self.settings['sheath_excess']
        sheath_flow = buffer_change_seq[0][0]*excess
        vol = buffer_change_seq[0][1]

        self._buffer_change_remain = 60*vol/sheath_flow
        self._changing_buffer = True
        self._starting_flow_rate = copy.copy(self.lc_flow_rate)

        if self._flow_timer:
            self._abort_flow_timer.set()
            while self._flow_timer:
                time.sleep(0.1)

        self._abort_change_buffer.clear()
        self._monitor_change_buffer_evt.set()

    def stop_change_buffer(self):
        if self._changing_buffer:
            logger.info('Aborting buffer change')
            self._abort_change_buffer.set()

    def _monitor_change_buffer(self):
        while not self._terminate_monitor_change_buffer.is_set():
            self._monitor_change_buffer_evt.wait()

            if len(self._buffer_change_seq) > 0 and not self._abort_change_buffer.is_set():
                buffer_change = self._buffer_change_seq.pop(0)
                flow_rate = buffer_change[0]
                volume = buffer_change[1]
                target_valve_pos = buffer_change[2]

                logger.info('Changing buffer on port %s with %s mL at %s '
                    'mL/min setpoint', target_valve_pos, volume, flow_rate)

                self.stop_flow()
                self.set_sheath_valve_position(target_valve_pos)
                self.change_flow_rate(flow_rate)
                self.start_flow()

                start_time = time.time()

                sheath_fr = self.sheath_setpoint
                run_time = 60*(volume/sheath_fr)

                elapsed_time = time.time() - start_time
                while  elapsed_time < run_time:
                    if self._abort_change_buffer.is_set():
                        break

                    self._buffer_change_remain = run_time - elapsed_time

                    time.sleep(0.1)

                    elapsed_time = time.time() - start_time

                if self._abort_change_buffer.is_set():
                    self.stop_flow()

            else:
                self.change_flow_rate(self._starting_flow_rate)
                self._abort_change_buffer.clear()
                self._monitor_change_buffer_evt.clear()
                self._changing_buffer = False
                self._buffer_change_seq = []

    def get_flow_timer_status(self):
        return copy.copy(self._flow_timer)

    def get_flow_timer_time_remaining(self):
        return copy.copy(self._remaining_flow_time)

    def start_flow_timer(self, flow_time):
        logger.info('Starting flow timer for %s minutes', round(flow_time/60,2))
        self._remaining_flow_time = flow_time
        self._flow_timer = True
        self._abort_flow_timer.clear()
        self._monitor_flow_timer_evt.set()

    def stop_flow_timer(self):
        if self._flow_timer:
            logger.info('Stopping flow timer')
            self._abort_flow_timer.set()

    def _monitor_flow_timer(self):
        while not self._terminate_monitor_flow_timer.is_set():
            self._monitor_flow_timer_evt.wait()

            if not self._abort_flow_timer.is_set():
                start_time = time.time()
                run_time = copy.copy(self._remaining_flow_time)
                elapsed_time = 0

                while  self._remaining_flow_time > 0:
                    if self._abort_flow_timer.is_set():
                        break

                    self._remaining_flow_time = run_time - elapsed_time

                    time.sleep(0.1)

                    elapsed_time = time.time() - start_time

            if not self._abort_flow_timer.is_set():
                logger.info('Flow timer ended')
                self.stop_flow_stability_monitor()
                self.stop_flow()

            self._abort_flow_timer.clear()
            self._monitor_flow_timer_evt.clear()
            self._flow_timer = False
            self._remaining_flow_time = 0

    def _get_buffer_monitor_flow_rate(self):
        return self._sheath_flow_rate

    def get_sheath_oob_error(self):
        """
        Gets the sheath flow out of bounds error
        """
        error = copy.copy(self._sheath_oob_error)
        fr = copy.copy(self._sheath_oob_flow)

        self._sheath_oob_error = False

        return error, fr

    def get_outlet_oob_error(self):
        """
        Gets the outlet flow out of bounds error
        """
        error = copy.copy(self._outlet_oob_error)
        fr = copy.copy(self._outlet_oob_flow)

        self._outlet_oob_error = False

        return error, fr

    def get_sheath_air_error(self):
        """
        Gets the sheath flow out of bounds error
        """
        error = copy.copy(self._sheath_air_error)

        self._sheath_air_error = False

        return error

    def get_outlet_air_error(self):
        """
        Gets the outlet flow out of bounds error
        """
        error = copy.copy(self._outlet_air_error)

        self._outlet_air_error = False

        return error

    def start_flow_stability_monitor(self):
        self.monitor = True

        logger.info('Flow monitoring started')

        sheath_low_warning = self.settings['sheath_warning_threshold_low']
        sheath_high_warning = self.settings['sheath_warning_threshold_high']

        outlet_low_warning = self.settings['outlet_warning_threshold_low']
        outlet_high_warning = self.settings['outlet_warning_threshold_high']

        logger.info('Sheath flow bounds: %f to %f %s',
            sheath_low_warning*self.sheath_setpoint,
            sheath_high_warning*self.sheath_setpoint,
            self.settings['flow_units'])

        logger.info('Outlet flow bounds: %f to %f %s',
            outlet_low_warning*self.outlet_setpoint,
            outlet_high_warning*self.outlet_setpoint,
            self.settings['flow_units'])

    def stop_flow_stability_monitor(self):
        self.monitor = False

    def _monitor_flow(self):
        logger.info('Starting continuous logging of flow rates')

        sheath_low_warning = self.settings['sheath_warning_threshold_low']
        sheath_high_warning = self.settings['sheath_warning_threshold_high']

        outlet_low_warning = self.settings['outlet_warning_threshold_low']
        outlet_high_warning = self.settings['outlet_warning_threshold_high']

        s1_type = None
        o1_type = None
        s2_type = None
        o2_type = None

        start_time = time.time()
        cycle_time = 0
        long_cycle_time = 0
        log_time = 0


        while not self._terminate_monitor_flow.is_set():

            if self.timeout_event.is_set():
                logger.error('Lost connection to the coflow control server.')

                while self.timeout_event.is_set():
                    time.sleep(0.1)
                    if self._terminate_monitor_flow.is_set():
                        break

            if (time.time() - cycle_time > 0.25 and
                not self._terminate_monitor_flow.is_set()):
                if not self._terminate_monitor_flow.is_set():
                    sheath_density, s1_type = self._update_sheath_density()

                if not self._terminate_monitor_flow.is_set():
                    outlet_density, o1_type = self._update_outlet_density()

                if not self._terminate_monitor_flow.is_set():
                    sheath_t, s2_type = self._update_sheath_temperature()

                if not self._terminate_monitor_flow.is_set():
                    outlet_t, o2_type = self._update_outlet_temperature()

                if (s1_type == o1_type and s1_type == 'density'
                    and s2_type == o2_type and s2_type == 'temperature'):
                    with self.get_plot_data_lock:
                        self.sheath_density_list.append(sheath_density)
                        self.outlet_density_list.append(outlet_density)

                        self.sheath_t_list.append(sheath_t)
                        self.outlet_t_list.append(outlet_t)

                        cycle_time = time.time()

                        cur_aux_time = cycle_time-start_time

                        self.aux_time_list.append(cur_aux_time)

                        self.new_sheath_density_list.append(sheath_density)
                        self.new_outlet_density_list.append(outlet_density)

                        self.new_sheath_t_list.append(sheath_t)
                        self.new_outlet_t_list.append(outlet_t)

                        self.new_aux_time_list.append(cur_aux_time)

                    if sheath_density < self.settings['air_density_thresh']:
                        self._sheath_air_error = True

                    elif outlet_density < self.settings['air_density_thresh']:
                        self._outlet_air_error = True

            if not self._terminate_monitor_flow.is_set():
                sheath_fr, s_type = self._update_sheath_flow_rate()

            if not self._terminate_monitor_flow.is_set():
                outlet_fr, o_type = self._update_outlet_flow_rate()

            if s_type == 'flow_rate' and o_type == 'flow_rate':

                with self.get_plot_data_lock:
                    self.sheath_fr_list.append(sheath_fr)
                    self.outlet_fr_list.append(outlet_fr)

                    cur_fr_time = time.time()-start_time

                    self.fr_time_list.append(cur_fr_time)

                    self.new_sheath_fr_list.append(sheath_fr)
                    self.new_outlet_fr_list.append(outlet_fr)
                    self.new_fr_time_list.append(cur_fr_time)

                if self.monitor:
                    if ((sheath_fr < sheath_low_warning*self.sheath_setpoint or
                        sheath_fr > sheath_high_warning*self.sheath_setpoint)):
                        logger.error('Sheath flow out of bounds (%f to %f): %f',
                            sheath_low_warning*self.sheath_setpoint,
                            sheath_high_warning*self.sheath_setpoint,
                            sheath_fr)

                        self._sheath_oob_error = True
                        self._sheath_oob_flow = sheath_fr


                    if ((outlet_fr < outlet_low_warning*self.outlet_setpoint or
                        outlet_fr > outlet_high_warning*self.outlet_setpoint)):
                        logger.error('Outlet flow out of bounds (%f to %f): %f',
                            outlet_low_warning*self.outlet_setpoint,
                            outlet_high_warning*self.outlet_setpoint,
                            outlet_fr)

                        self._outlet_oob_error = True
                        self._outlet_oob_flow = outlet_fr


            if (not self._terminate_monitor_flow.is_set()
                and time.time() - log_time > 300 and self.coflow_on):
                logger.info('Sheath flow rate: %f', sheath_fr)
                logger.info('Outlet flow rate: %f', outlet_fr)
                logger.info('Sheath density: %f', sheath_density)
                logger.info('Outlet density: %f', outlet_density)
                logger.info('Sheath temperature: %f', sheath_t)
                logger.info('Outlet temperature: %f', outlet_t)

                log_time = time.time()

            if time.time() - long_cycle_time > 5:
                self._update_sheath_valve_position()

                long_cycle_time = time.time()

        logger.info('Stopping continuous logging of flow rates')

    def get_buffer_info(self, position):
        """
        Gets the buffer info including the current volume

        Parameters
        ----------
        position: str
            The buffer position to get the info for.

        Returns
        -------
        vol: float
            The volume remaining
        descrip: str
            The buffer description (e.g. contents)
        """
        vol, descrip = self._buffer_monitor.get_buffer_info(position)

        return vol, descrip

    def get_all_buffer_info(self):
        """
        Gets information on all buffers

        Returns
        -------
        buffers: dict
            A dictionary where the keys are the buffer positions and
            the values are dictionarys with keys for volume ('vol') and
            description ('descrip').
        """
        buffers = self._buffer_monitor.get_all_buffer_info()


        return buffers

    def set_buffer_info(self, position, volume, descrip):
        """
        Sets the buffer info for a given buffer position

        Parameters
        ----------
        position: str
            The buffer position (e.g. 1 or A or etc)
        volume: float
            The current buffer volume
        descrip: str
            Buffer description (e.g. contents)

        Returns
        -------
        success: bool
            True if successful.
        """
        self._buffer_monitor.set_buffer_info(position, volume,descrip)

        return True

    def set_active_buffer_position(self, position):
        """
        Sets the active buffer position

        Parameters
        ----------
        position: str
            The buffer position (e.g. 1 or A)
        """
        self._buffer_monitor.set_active_buffer_position(position)

        return True

    def remove_buffer(self, position):
        """
        Removes the buffer at the given position.

        Parameters
        ----------
        position: str
            The buffer position (e.g. 1 or A)
        """
        self._buffer_monitor.remove_buffer(position)

        return True

    def _send_pumpcmd(self, cmd, response=False):
        self.coflow_pump_status_q.clear() #For now, do nothing with the status
        ret_val = utils.send_cmd(cmd, self.coflow_pump_cmd_q,
            self.coflow_pump_return_q, self.timeout_event,
            self.coflow_pump_return_lock, not self.local_devices, 'pump', response)

        return ret_val

    def _send_fmcmd(self, cmd, response=False):
        """
        Sends commands to the pump using the ``fm_cmd_q`` that was given
        to :py:class:`FlowMeterCommThread`.

        :param str cmd: The command to send, matching the command in the
            :py:class:`FlowMeterCommThread` ``_commands`` dictionary.
        """

        self.coflow_fm_status_q.clear() #For now, do nothing with the status

        ret_val = utils.send_cmd(cmd, self.coflow_fm_cmd_q,
            self.coflow_fm_return_q, self.timeout_event,
            self.coflow_fm_return_lock, not self.local_devices, 'fm', response)

        return ret_val


    def _send_valvecmd(self, cmd, response=False):
        self.valve_status_q.clear() #For now, do nothing with the status

        ret_val = utils.send_cmd(cmd, self.valve_cmd_q,
            self.valve_return_q, self.timeout_event,
            self.valve_return_lock, not self.local_devices, 'valve', response)

        return ret_val

    def get_plot_data(self):
        with self.get_plot_data_lock:
            sheath_fr_list = copy.copy(self.sheath_fr_list)
            outlet_fr_list = copy.copy(self.outlet_fr_list)
            sheath_density_list = copy.copy(self.sheath_density_list)
            outlet_density_list = copy.copy(self.outlet_density_list)
            sheath_t_list = copy.copy(self.sheath_t_list)
            outlet_t_list = copy.copy(self.outlet_t_list)
            fr_time_list = copy.copy(self.fr_time_list)
            aux_time_list = copy.copy(self.aux_time_list)

        plot_data = {
            'sheath_fr_list': sheath_fr_list,
            'outlet_fr_list': outlet_fr_list,
            'sheath_density_list': sheath_density_list,
            'outlet_density_list': outlet_density_list,
            'sheath_t_list': sheath_t_list,
            'outlet_t_list': outlet_t_list,
            'fr_time_list': fr_time_list,
            'aux_time_list': aux_time_list,
            }

        return plot_data

    def get_new_plot_data(self):
        with self.get_plot_data_lock:
            sheath_fr_list = copy.copy(self.new_sheath_fr_list)
            outlet_fr_list = copy.copy(self.new_outlet_fr_list)
            sheath_density_list = copy.copy(self.new_sheath_density_list)
            outlet_density_list = copy.copy(self.new_outlet_density_list)
            sheath_t_list = copy.copy(self.new_sheath_t_list)
            outlet_t_list = copy.copy(self.new_outlet_t_list)
            fr_time_list = copy.copy(self.new_fr_time_list)
            aux_time_list = copy.copy(self.new_aux_time_list)

            self.new_sheath_fr_list.clear()
            self.new_outlet_fr_list.clear()
            self.new_sheath_density_list.clear()
            self.new_outlet_density_list.clear()
            self.new_sheath_t_list.clear()
            self.new_outlet_t_list.clear()
            self.new_fr_time_list.clear()
            self.new_aux_time_list.clear()

        plot_data = {
            'sheath_fr_list': sheath_fr_list,
            'outlet_fr_list': outlet_fr_list,
            'sheath_density_list': sheath_density_list,
            'outlet_density_list': outlet_density_list,
            'sheath_t_list': sheath_t_list,
            'outlet_t_list': outlet_t_list,
            'fr_time_list': fr_time_list,
            'aux_time_list': aux_time_list,
            }

        return plot_data

    def stop(self):
        self.coflow_pump_con.stop()
        self.coflow_fm_con.stop()
        self.coflow_valve_con.stop()

        if not self.timeout_event.is_set():
            self.coflow_pump_con.join(5)
            self.coflow_fm_con.join(5)
            self.coflow_valve_con.join(5)

        self._terminate_monitor_change_buffer.set()
        self._abort_change_buffer.set()
        self._monitor_change_buffer_evt.set()
        self._monitor_change_buffer_thread.join(5)

        self._terminate_monitor_flow_timer.set()
        self._abort_flow_timer.set()
        self._monitor_flow_timer_evt.set()
        self._monitor_flow_timer_thread.join(5)


class CoflowCommThread(utils.CommManager):

    def __init__(self, name):
        utils.CommManager.__init__(self, name)

        self._commands = {
            'connect'               : self._connect_device,
            'disconnect'            : self._disconnect_device,
            'start_flow'            : self._start_flow,
            'stop_flow'             : self._stop_flow,
            'start_flow_monitor'    : self._start_flow_monitor,
            'stop_flow_monitor'     : self._stop_flow_monitor,
            'change_flow_rate'      : self._change_flow_rate,
            'change_buffer'         : self._change_buffer,
            'stop_change_buffer'    : self._stop_change_buffer,
            'start_overflow'        : self._start_overflow,
            'stop_overflow'         : self._stop_overflow,
            'start_flow_timer'      : self._start_flow_timer,
            'stop_flow_timer'       : self._stop_flow_timer,
            'start_flow_stab_mon'   : self._start_flow_stab_mon,
            'stop_flow_stab_mon'    : self._stop_flow_stab_mon,
            'validate_flow_rate'    : self._validate_flow_rate,
            'get_sheath_valve_pos'  : self._get_sheath_valve_pos,
            'set_sheath_valve_pos'  : self._set_sheath_valve_pos,
            'get_sheath_oob_error'  : self._get_sheath_oob_error,
            'get_outlet_oob_error'  : self._get_outlet_oob_error,
            'get_sheath_air_error'  : self._get_sheath_air_error,
            'get_outlet_air_error'  : self._get_outlet_air_error,
            'set_buffer_info'       : self._set_buffer_info,
            'remove_buffer'         : self._remove_buffer,
            'get_status'            : self._get_status,
            'get_overflow_status'   : self._get_overflow_status,
            'get_buffer_info'       : self._get_buffer_info,
            'get_bc_time_remaining' : self._get_bc_time_remaining,
            'get_plot_data'         : self._get_plot_data,
        }

        self._connected_devices = OrderedDict()
        self._connected_coms = OrderedDict()

        self.known_devices = {
            'Coflow' : CoflowControl,
            }

    def _additional_new_comm(self, name):
        pass

    def _additional_connect_device(self, name, device_type, device, **kwargs):
        pass

    def _start_flow(self, name, **kwargs):

        logger.debug("%s starting flow", name)

        comm_name = kwargs.pop('comm_name', None)
        cmd = kwargs.pop('cmd', None)

        device = self._connected_devices[name]
        device.start_flow()

        self._return_value((name, cmd, True), comm_name)

        logger.debug("%s flow started", name)

    def _stop_flow(self, name, **kwargs):

        logger.debug("%s stopping flow", name)

        comm_name = kwargs.pop('comm_name', None)
        cmd = kwargs.pop('cmd', None)

        device = self._connected_devices[name]
        device.stop_flow()

        self._return_value((name, cmd, True), comm_name)

        logger.debug("%s flow stopped", name)

    def _start_flow_monitor(self, name, **kwargs):

        logger.debug("%s starting flow", name)

        comm_name = kwargs.pop('comm_name', None)
        cmd = kwargs.pop('cmd', None)

        device = self._connected_devices[name]
        device.start_flow_stability_monitor()

        self._return_value((name, cmd, True), comm_name)

        logger.debug("%s flow started", name)

    def _stop_flow_monitor(self, name, **kwargs):

        logger.debug("%s stopping flow", name)

        comm_name = kwargs.pop('comm_name', None)
        cmd = kwargs.pop('cmd', None)

        device = self._connected_devices[name]
        device.stop_flow_stability_monitor()

        self._return_value((name, cmd, True), comm_name)

        logger.debug("%s flow stopped", name)

    def _change_flow_rate(self, name, val, **kwargs):

        logger.debug("%s starting flow_rate change", name)

        comm_name = kwargs.pop('comm_name', None)
        cmd = kwargs.pop('cmd', None)

        device = self._connected_devices[name]
        device.change_flow_rate(val)

        self._return_value((name, cmd, True), comm_name)

        logger.debug("%s flow_rate changed", name)

    def _change_buffer(self, name, val, **kwargs):

        logger.debug("%s starting buffer change", name)

        comm_name = kwargs.pop('comm_name', None)
        cmd = kwargs.pop('cmd', None)

        device = self._connected_devices[name]
        device.change_buffer(val)

        self._return_value((name, cmd, True), comm_name)

        logger.debug("%s buffer change started", name)

    def _stop_change_buffer(self, name, **kwargs):

        logger.debug("%s stopping buffer change", name)

        comm_name = kwargs.pop('comm_name', None)
        cmd = kwargs.pop('cmd', None)

        device = self._connected_devices[name]
        device.stop_change_buffer()

        self._return_value((name, cmd, True), comm_name)

        logger.debug("%s stopped buffer change", name)

    def _start_overflow(self, name, **kwargs):

        logger.debug("%s starting overflow pump", name)

        comm_name = kwargs.pop('comm_name', None)
        cmd = kwargs.pop('cmd', None)

        device = self._connected_devices[name]
        device.start_overflow()

        self._return_value((name, cmd, True), comm_name)
        status = device.check_overflow_status()
        self._return_value((name, cmd, status), 'status')

        logger.debug("%s overflow pump started", name)

    def _stop_overflow(self, name, **kwargs):

        logger.debug("%s starting overflow pump", name)

        comm_name = kwargs.pop('comm_name', None)
        cmd = kwargs.pop('cmd', None)

        device = self._connected_devices[name]
        device.stop_overflow()

        self._return_value((name, cmd, True), comm_name)
        status = device.check_overflow_status()
        self._return_value((name, cmd, status), 'status')

        logger.debug("%s overflow pump started", name)

    def _start_flow_timer(self, name, val, **kwargs):

        logger.debug("%s starting buffer change", name)

        comm_name = kwargs.pop('comm_name', None)
        cmd = kwargs.pop('cmd', None)

        device = self._connected_devices[name]
        device.start_flow_timer(val)

        self._return_value((name, cmd, True), comm_name)

        logger.debug("%s buffer change started", name)

    def _stop_flow_timer(self, name, **kwargs):

        logger.debug("%s stopping flow timer", name)

        comm_name = kwargs.pop('comm_name', None)
        cmd = kwargs.pop('cmd', None)

        device = self._connected_devices[name]
        device.stop_flow_timer()

        self._return_value((name, cmd, True), comm_name)

        logger.debug("%s stopped flow timer", name)

    def _start_flow_stab_mon(self, name, **kwargs):

        logger.debug("%s starting flow stability monitoring", name)

        comm_name = kwargs.pop('comm_name', None)
        cmd = kwargs.pop('cmd', None)

        device = self._connected_devices[name]
        device.start_flow_stability_monitor()

        self._return_value((name, cmd, True), comm_name)

        logger.debug("%s flow stability monitoring started", name)

    def _stop_flow_stab_mon(self, name, **kwargs):

        logger.debug("%s stopping flow stability monitoring", name)

        comm_name = kwargs.pop('comm_name', None)
        cmd = kwargs.pop('cmd', None)

        device = self._connected_devices[name]
        device.stop_flow_stability_monitor()

        self._return_value((name, cmd, True), comm_name)

        logger.debug("%s stopped flow stability monitoring", name)

    def _validate_flow_rate(self, name, val, **kwargs):
        logger.debug("%s validating flow rate", name)

        comm_name = kwargs.pop('comm_name', None)
        cmd = kwargs.pop('cmd', None)

        device = self._connected_devices[name]
        ret = device.validate_flow_rate(val, **kwargs)

        self._return_value((name, cmd, ret), comm_name)

        logger.debug("%s validated flow rate", name)

    def _get_sheath_valve_pos(self, name, **kwargs):
        logger.debug("%s getting sheath valve position", name)

        comm_name = kwargs.pop('comm_name', None)
        cmd = kwargs.pop('cmd', None)

        device = self._connected_devices[name]
        ret = device.get_sheath_valve_position(**kwargs)

        self._return_value((name, cmd, ret), comm_name)

        logger.debug("%s got sheath valve position", name)

    def _set_sheath_valve_pos(self, name, val, **kwargs):
        logger.debug("%s setting sheath valve position %s", name, val)

        comm_name = kwargs.pop('comm_name', None)
        cmd = kwargs.pop('cmd', None)

        device = self._connected_devices[name]
        device.set_sheath_valve_position(val, **kwargs)

        self._return_value((name, cmd, val), 'status')

        logger.debug("%s set sheath valve position", name)

    def _get_sheath_oob_error(self, name, **kwargs):
        logger.debug("%s getting sheath out of bounds error", name)

        comm_name = kwargs.pop('comm_name', None)
        cmd = kwargs.pop('cmd', None)

        device = self._connected_devices[name]
        ret = device.get_sheath_oob_error(**kwargs)

        self._return_value((name, cmd, ret), comm_name)

        logger.debug("%s got sheath out of bounds error", name)

    def _get_outlet_oob_error(self, name, **kwargs):
        logger.debug("%s getting outlet out of bounds error", name)

        comm_name = kwargs.pop('comm_name', None)
        cmd = kwargs.pop('cmd', None)

        device = self._connected_devices[name]
        ret = device.get_outlet_oob_error(**kwargs)

        self._return_value((name, cmd, ret), comm_name)

        logger.debug("%s got outlet out of bounds error", name)

    def _get_sheath_air_error(self, name, **kwargs):
        logger.debug("%s getting sheath air error", name)

        comm_name = kwargs.pop('comm_name', None)
        cmd = kwargs.pop('cmd', None)

        device = self._connected_devices[name]
        ret = device.get_sheath_air_error(**kwargs)

        self._return_value((name, cmd, ret), comm_name)

        logger.debug("%s got sheath air error", name)

    def _get_outlet_air_error(self, name, **kwargs):
        logger.debug("%s getting outlet air error", name)

        comm_name = kwargs.pop('comm_name', None)
        cmd = kwargs.pop('cmd', None)

        device = self._connected_devices[name]
        ret = device.get_outlet_air_error(**kwargs)

        self._return_value((name, cmd, ret), comm_name)

        logger.debug("%s got outlet air error", name)

    def _set_buffer_info(self, name, pos, vol, descrip, **kwargs):
        logger.debug("%s setting buffer info", name)

        comm_name = kwargs.pop('comm_name', None)
        cmd = kwargs.pop('cmd', None)

        device = self._connected_devices[name]
        device.set_buffer_info(pos, vol, descrip)

        self._return_value((name, cmd, True), comm_name)

        logger.debug("%s set buffer info", name)

    def _remove_buffer(self, name, val, **kwargs):
        logger.debug("%s removing buffer %s", name, val)

        comm_name = kwargs.pop('comm_name', None)
        cmd = kwargs.pop('cmd', None)

        device = self._connected_devices[name]
        device.remove_buffer(val)

        self._return_value((name, cmd, True), comm_name)

        logger.debug("%s removed buffer", name)

    def _get_status(self, name, **kwargs):
        logger.debug("%s getting status", name)

        comm_name = kwargs.pop('comm_name', None)
        cmd = kwargs.pop('cmd', None)
        device = self._connected_devices[name]

        status = {
            'sheath_valve_pos'  : device.get_sheath_valve_position(),
            'sheath_is_moving'  : device.sheath_is_moving,
            'outlet_is_moving'  : device.outlet_is_moving,
            'coflow_on'         : device.coflow_on,
            'bc_time_remaining' : device.get_buffer_change_time_remaining(),
            'bc_status'         : device.get_buffer_change_status(),
            'ft_time_remaining' : device.get_flow_timer_time_remaining(),
            'ft_status'         : device.get_flow_timer_status(),
            'sheath_oob_error'  : device.get_sheath_oob_error(),
            'outlet_oob_error'  : device.get_outlet_oob_error(),
            'sheath_air_error'  : device.get_sheath_air_error(),
            'outlet_air_error'  : device.get_outlet_air_error(),
            'sheath_setpoint'   : device.sheath_setpoint,
            'outlet_setpoint'   : device.outlet_setpoint,
            'lc_flow_rate'      : device.lc_flow_rate,
            'new_plot_data'     : device.get_new_plot_data(),
            }

        self._return_value((name, cmd, status), comm_name)

        logger.debug("%s got status", name)

    def _get_overflow_status(self, name, **kwargs):
        logger.debug("%s getting overflow status", name)

        comm_name = kwargs.pop('comm_name', None)
        cmd = kwargs.pop('cmd', None)

        device = self._connected_devices[name]
        ret = device.check_overflow_status(**kwargs)

        self._return_value((name, cmd, ret), comm_name)

        logger.debug("%s got overflow status", name)

    def _get_buffer_info(self, name, **kwargs):
        logger.debug("%s getting buffer info", name)

        comm_name = kwargs.pop('comm_name', None)
        cmd = kwargs.pop('cmd', None)

        device = self._connected_devices[name]
        ret = device.get_all_buffer_info(**kwargs)

        self._return_value((name, cmd, ret), comm_name)

        logger.debug("%s got buffer info", name)

    def _get_bc_time_remaining(self, name, **kwargs):
        logger.debug("%s getting buffer info", name)

        comm_name = kwargs.pop('comm_name', None)
        cmd = kwargs.pop('cmd', None)

        device = self._connected_devices[name]
        ret = device.get_buffer_change_time_remaining(**kwargs)

        self._return_value((name, cmd, ret), comm_name)

        logger.debug("%s got buffer info", name)

    def _get_plot_data(self, name, **kwargs):
        logger.debug("%s getting plot data", name)

        comm_name = kwargs.pop('comm_name', None)
        cmd = kwargs.pop('cmd', None)

        device = self._connected_devices[name]
        ret = device.get_plot_data(**kwargs)

        self._return_value((name, cmd, ret), comm_name)

        logger.debug("%s got plot data", name)

    def _additional_abort(self):
        for name in self._connected_devices:
            device = self._connected_devices[name]
            device.stop()

    def get_device(self, name):
        return self._connected_devices[name]


class CoflowPanel(utils.DevicePanel):
    """
    """
    def __init__(self, parent, panel_id, settings, *args,
        **kwargs):
        """
        """
        try:
            biocon = wx.FindWindowByName('biocon')
        except Exception:
            biocon = None

        if biocon is not None:
            settings['device_data'] = settings['device_init'][0]

        if settings['device_communication'] == 'remote':
            settings['remote'] = True
        else:
            settings['remote'] = False

        self._buffer_info = {}
        self._status = ''

        self._sheath_valve_pos = 1
        self._sheath_is_moving = False
        self._outlet_is_moving = False
        self._coflow_on = False
        self._bc_time_remaining = 0.
        self._bc_status = False
        self._ft_time_remaining = 0.
        self._ft_status = False
        self._sheath_setpoint = 0.
        self._outlet_setpoint = 0.
        self._lc_flow_rate = 0.
        self._overflow_status = ''
        self._changing_valve = False
        self._expected_valve_pos = 0

        super(CoflowPanel, self).__init__(parent, panel_id, settings,
            *args, **kwargs)
        logger.debug('Initializing CoflowPanel')



    def _init_device(self, settings):

        device_data = settings['device_data']
        device_data['kwargs']['device_communication'] = self.top_settings['device_communication']
        kwargs =  {'settings': device_data['kwargs']}

        args = [self.name, self.name, None]

        connect_cmd = ['connect', args, kwargs]

        self.connected = self._send_cmd(connect_cmd, get_response=True)

        if self.connected:

            self.warning_dialog = None
            self.error_dialog = None
            self.air_warning_dialog = None
            self.monitor_timer = wx.Timer(self)
            self.Bind(wx.EVT_TIMER, self._on_monitor_timer, self.monitor_timer)
            self.doing_buffer_change = False
            self.verbose_buffer_change = True

            self.get_plot_data_lock = threading.Lock()

            self.sheath_fr_list = deque(maxlen=10000)
            self.outlet_fr_list = deque(maxlen=10000)
            self.sheath_density_list = deque(maxlen=4800)
            self.outlet_density_list = deque(maxlen=4800)
            self.sheath_t_list = deque(maxlen=4800)
            self.outlet_t_list = deque(maxlen=4800)
            self.fr_time_list = deque(maxlen=10000)
            self.aux_time_list = deque(maxlen=4800)
            self.start_time = None

            plot_data_cmd = ['get_plot_data', [self.name,], {}]
            plot_data = self._send_cmd(plot_data_cmd, get_response=True)

            self._update_plot_data(plot_data)

            status_cmd = ['get_status', [self.name,], {}]
            status = self._send_cmd(status_cmd, get_response=True)
            self._set_status('get_status', status)

            self._set_status_commands()

            self.auto_flow.Enable()

            if self._sheath_is_moving or self._outlet_is_moving:
                self.stop_flow_button.Enable()
                self.change_flow_button.Enable()

            if self._sheath_is_moving and self._outlet_is_moving:
                self.start_flow_button.Disable()
                self.change_buffer_button.Disable()
                self.stop_flow_button.Enable()
                self.change_flow_button.Enable()
                self.set_status_label('Coflow on')
                self.monitor_timer.Start(self.settings['settling_time'])
                self._coflow_on = True
            else:
                self.start_flow_button.Enable()
                self.change_buffer_button.Enable()

        else:

            msg = ('No connection to coflow! Contact your beamline scientist.')

            wx.CallAfter(self.showMessageDialog, self, msg, "Coflow failed to connect",
                wx.OK|wx.ICON_ERROR)

    def _set_status_commands(self):
        status_cmd = ['get_status', [self.name,], {}]
        self._update_status_cmd(status_cmd, 1)

        buffer_status_cmd = ['get_buffer_info', [self.name,], {}]
        self._update_status_cmd(buffer_status_cmd, 5)

        if self.settings['use_overflow_control']:
            overflow_status_cmd = ['get_overflow_status', [self.name,], {}]
            self._update_status_cmd(overflow_status_cmd, 10)

    def _set_status(self, cmd, val):
        # print(cmd)
        # print(val)
        if cmd == 'get_status':
            sheath_valve_pos = val['sheath_valve_pos']
            sheath_is_moving = val['sheath_is_moving']
            outlet_is_moving = val['outlet_is_moving']
            coflow_on = val['coflow_on']
            bc_time_remaining = val['bc_time_remaining']
            bc_status = val['bc_status']
            ft_time_remaining = val['ft_time_remaining']
            ft_status = val['ft_status']
            sheath_oob_error = val['sheath_oob_error']
            outlet_oob_error = val['outlet_oob_error']
            sheath_air_error = val['sheath_air_error']
            outlet_air_error = val['outlet_air_error']
            sheath_setpoint = val['sheath_setpoint']
            outlet_setpoint = val['outlet_setpoint']
            lc_flow_rate = val['lc_flow_rate']
            new_plot_data = val['new_plot_data']

            self._sheath_is_moving = sheath_is_moving
            self._outlet_is_moving = outlet_is_moving
            self._sheath_setpoint = sheath_setpoint
            self._outlet_setpoint = outlet_setpoint

            if self._coflow_on != coflow_on:
                if coflow_on:
                    wx.CallAfter(self._set_start_flow_button_status)
                    wx.CallAfter(self.set_status_label, 'Coflow on')
                else:
                    wx.CallAfter(self._set_stop_flow_button_status)
                    wx.CallAfter(self.set_status_label, 'Coflow off')

                self._coflow_on = coflow_on

            if self._sheath_valve_pos != int(sheath_valve_pos):
                if self._changing_valve:
                    if self._expected_valve_pos == int(sheath_valve_pos):
                        wx.CallAfter(self.sheath_valve_pos.SafeChangeValue,
                            int(sheath_valve_pos))

                        self._sheath_valve_pos = int(sheath_valve_pos)
                else:
                    wx.CallAfter(self.sheath_valve_pos.SafeChangeValue,
                        int(sheath_valve_pos))
                    self._sheath_valve_pos = int(sheath_valve_pos)

            if bc_status != self._bc_status:
                if not bc_status:
                    wx.CallAfter(self._stop_flow_timer)
            elif bc_status:
                wx.CallAfter(self.set_flow_timer_time_remaining, bc_time_remaining)

            self._bc_status = bc_status

            if not bc_status and ft_status != self._ft_status:
                if not ft_status:
                    wx.CallAfter(self._stop_flow_timer)
            elif ft_status and not bc_status:
                wx.CallAfter(self.set_flow_timer_time_remaining, ft_time_remaining)

            self._ft_status = ft_status

            sheath_oob_err, sheath_fr = sheath_oob_error

            if sheath_oob_err and self.settings['show_sheath_warning']:
                wx.CallAfter(self._show_warning_dialog, 'sheath', sheath_fr)

            outlet_oob_err, outlet_fr = outlet_oob_error

            if outlet_oob_err and self.settings['show_outlet_warning']:
                wx.CallAfter(self._show_warning_dialog, 'outlet', outlet_fr)


            if sheath_air_error and outlet_air_error:
                wx.CallAfter(self.air_detected, 'both')

            elif sheath_air_error:
                wx.CallAfter(self.air_detected, 'sheath')

            elif outlet_air_error:
                wx.CallAfter(self.air_detected, 'outlet')

            if str(lc_flow_rate) != self._lc_flow_rate and not bc_status:
                wx.CallAfter(self.flow_rate.ChangeValue, str(lc_flow_rate))
                self._lc_flow_rate = str(lc_flow_rate)

            self._update_plot_data(new_plot_data)

            if len(new_plot_data['sheath_fr_list']) > 0:
                new_sheath_fr = round(new_plot_data['sheath_fr_list'][-1],3)
                wx.CallAfter(self.sheath_flow.SetLabel, str(new_sheath_fr))

            if len(new_plot_data['outlet_fr_list']) > 0:
                new_outlet_fr = round(new_plot_data['outlet_fr_list'][-1],3)
                wx.CallAfter(self.outlet_flow.SetLabel, str(new_outlet_fr))

        elif cmd == 'get_buffer_info':
            wx.CallAfter(self._update_all_buffers, val)

        elif cmd == 'get_overflow_status':
            status, err = val
            if err:
                msg = ('Could not get overflow pump status. Contact your beamline scientist.')
                wx.CallAfter(self.showMessageDialog, self, msg, "Connection error",
                    wx.OK|wx.ICON_ERROR)

            elif status != self._overflow_status:
                wx.CallAfter(self.overflow_status.SetLabel, status)
                self._overflow_status = status

        elif cmd == 'start_overflow' or cmd == 'stop_overflow':
            status, err = val
            if err:
                msg = ('Could not get overflow pump status. Contact your beamline scientist.')
                wx.CallAfter(self.showMessageDialog, self, msg, "Connection error",
                    wx.OK|wx.ICON_ERROR)

            elif status != self._overflow_status:
                wx.CallAfter(self.overflow_status.SetLabel, status)
                self._overflow_status = status

    def _stop_flow_timer(self):
        change_buf = copy.copy(self.doing_buffer_change)
        self.stop_flow()
        self.stop_flow_timer()

        if change_buf:
            if self.verbose_buffer_change:
                msg = ('Buffer change complete. Do you want to restart '
                    'flow at the previous rate?')

                dialog = wx.MessageDialog(self, msg, 'Buffer change finished',
                    style=wx.YES_NO|wx.YES_DEFAULT|wx.ICON_QUESTION)

                ret = dialog.ShowModal()
                dialog.Destroy()

                if ret == wx.ID_YES:
                    self.start_flow()

    def _update_plot_data(self, plot_data):
        with self.get_plot_data_lock:
            self.sheath_fr_list.extend(plot_data['sheath_fr_list'])
            self.outlet_fr_list.extend(plot_data['outlet_fr_list'])
            self.sheath_density_list.extend(plot_data['sheath_density_list'])
            self.outlet_density_list.extend(plot_data['outlet_density_list'])
            self.sheath_t_list.extend(plot_data['sheath_t_list'])
            self.outlet_t_list.extend(plot_data['outlet_t_list'])
            self.fr_time_list.extend(plot_data['fr_time_list'])
            self.aux_time_list.extend(plot_data['aux_time_list'])

    def _FromDIP(self, size):
        # This is a hack to provide easy back compatibility with wxpython < 4.1
        try:
            return self.FromDIP(size)
        except Exception:
            return size

    def _create_layout(self):
        """Creates the layout for the panel."""
        self.top_settings = self.settings
        self.settings = self.settings['device_data']['kwargs'] #Odd, but matches old colfow constructions

        units = self.settings['flow_units']

        control_box = wx.StaticBox(self, label='Coflow Controls')
        coflow_ctrl_sizer = wx.StaticBoxSizer(control_box, wx.VERTICAL)

        flow_box = wx.StaticBox(control_box, label='Flow')

        self.flow_rate = wx.TextCtrl(flow_box, size=self._FromDIP((60,-1)),
            value=self.settings['lc_flow_rate'], validator=utils.CharValidator('float'))
        fr_label = 'LC flow rate [{}]:'.format(units)
        self.change_flow_button = wx.Button(flow_box, label='Change Flow Rate')

        flow_rate_sizer = wx.BoxSizer(wx.HORIZONTAL)
        flow_rate_sizer.Add(wx.StaticText(flow_box, label=fr_label), border=self._FromDIP(2),
            flag=wx.ALIGN_CENTER_VERTICAL|wx.RIGHT)
        flow_rate_sizer.Add(self.flow_rate, flag=wx.ALIGN_CENTER_VERTICAL|wx.RIGHT,
            border=self._FromDIP(2))
        flow_rate_sizer.Add(self.change_flow_button, flag=wx.ALIGN_CENTER_VERTICAL)

        self.start_flow_button = wx.Button(flow_box, label='Start Coflow')
        self.stop_flow_button = wx.Button(flow_box, label='Stop Coflow')
        self.change_buffer_button = wx.Button(flow_box, label='Change Buffer')

        self.auto_flow = wx.CheckBox(flow_box, label='Start/stop coflow automatically with exposure')
        self.auto_flow.SetValue(False)

        self.start_flow_button.Bind(wx.EVT_BUTTON, self._on_startbutton)
        self.stop_flow_button.Bind(wx.EVT_BUTTON, self._on_stopbutton)
        self.change_flow_button.Bind(wx.EVT_BUTTON, self._on_changebutton)
        self.change_buffer_button.Bind(wx.EVT_BUTTON, self._on_change_buffer)

        self.start_flow_button.Disable()
        self.stop_flow_button.Disable()
        self.change_flow_button.Disable()
        self.change_buffer_button.Disable()
        self.auto_flow.Disable()

        if 'exposure' not in self.top_settings['components']:
            self.auto_flow.SetValue(False)
            self.auto_flow.Disable()
            self.auto_flow.Hide()

        button_sizer = wx.BoxSizer(wx.HORIZONTAL)
        button_sizer.AddStretchSpacer(1)
        button_sizer.Add(self.start_flow_button, border=self._FromDIP(5),
            flag=wx.ALIGN_CENTER_VERTICAL|wx.RIGHT)
        button_sizer.Add(self.stop_flow_button, border=self._FromDIP(5),
            flag=wx.ALIGN_CENTER_VERTICAL|wx.LEFT|wx.RIGHT)
        button_sizer.Add(self.change_buffer_button, border=self._FromDIP(5),
            flag=wx.ALIGN_CENTER_VERTICAL|wx.LEFT)
        button_sizer.AddStretchSpacer(1)

        basic_flow_ctrl_sizer = wx.StaticBoxSizer(flow_box, wx.VERTICAL)
        basic_flow_ctrl_sizer.Add(flow_rate_sizer, border=self._FromDIP(2),
            flag=wx.TOP|wx.LEFT|wx.RIGHT)
        basic_flow_ctrl_sizer.Add(self.auto_flow, border=self._FromDIP(2),
            flag=wx.TOP|wx.LEFT|wx.RIGHT)
        basic_flow_ctrl_sizer.Add(button_sizer, border=self._FromDIP(2),
            flag=wx.ALL)

        valve_box = wx.StaticBox(control_box, label='Valves')
        valve_box_sizer = wx.StaticBoxSizer(valve_box, wx.HORIZONTAL)

        self.sheath_valve_pos = utils.IntSpinCtrl(valve_box, my_min=1,
            my_max=self.settings['sheath_valve']['kwargs']['positions'])
        self.sheath_valve_pos.Bind(utils.EVT_MY_SPIN, self._on_sheath_valve_position_change)

        valve_sizer = wx.FlexGridSizer(cols=2, hgap=self._FromDIP(5),
            vgap=self._FromDIP(5))
        valve_sizer.Add(wx.StaticText(valve_box, label='Sheath Valve:'),
            flag=wx.ALIGN_CENTER_VERTICAL)
        valve_sizer.Add(self.sheath_valve_pos, flag=wx.ALIGN_CENTER_VERTICAL)

        valve_box_sizer.Add(valve_sizer, flag=wx.ALL|wx.EXPAND, border=self._FromDIP(2))
        valve_box_sizer.AddStretchSpacer(1)

        basic_ctrl_sizer = wx.BoxSizer(wx.HORIZONTAL)
        basic_ctrl_sizer.Add(basic_flow_ctrl_sizer)
        basic_ctrl_sizer.Add(valve_box_sizer, border=self._FromDIP(2),
            flag=wx.LEFT)


        adv_pane = wx.CollapsiblePane(control_box, label="Advanced")
        adv_pane.Bind(wx.EVT_COLLAPSIBLEPANE_CHANGED, self.on_collapse)
        adv_win = adv_pane.GetPane()

        adv_sizer = wx.BoxSizer(wx.VERTICAL)

        if self.settings['use_overflow_control']:
            overflow_box = wx.StaticBox(adv_win, label='Overflow')
            overflow_box_sizer = wx.StaticBoxSizer(overflow_box, wx.HORIZONTAL)
            self.start_overflow = wx.Button(overflow_box, label='Start Overflow')
            self.stop_overflow = wx.Button(overflow_box, label='Stop Overflow')
            self.overflow_status = wx.StaticText(overflow_box, label='',
                style=wx.ST_NO_AUTORESIZE, size=self._FromDIP((50, -1)))

            self.start_overflow.Bind(wx.EVT_BUTTON, self._on_start_overflow)
            self.stop_overflow.Bind(wx.EVT_BUTTON, self._on_stop_overflow)

            of_status_sizer = wx.BoxSizer(wx.HORIZONTAL)
            of_status_sizer.Add(wx.StaticText(overflow_box, label='Overflow status:'),
                flag=wx.ALIGN_CENTER_VERTICAL|wx.RIGHT, border=self._FromDIP(5))
            of_status_sizer.Add(self.overflow_status, flag=wx.ALIGN_CENTER_VERTICAL)

            overflow_sizer = wx.GridBagSizer(vgap=self._FromDIP(5),
                hgap=self._FromDIP(5))
            overflow_sizer.Add(of_status_sizer, (0,0), span=(1,2),
                flag=wx.ALIGN_CENTER_VERTICAL)
            overflow_sizer.Add(self.start_overflow, (1,0),
                flag=wx.ALIGN_CENTER_VERTICAL)
            overflow_sizer.Add(self.stop_overflow, (1,1),
                flag=wx.ALIGN_CENTER_VERTICAL)

            overflow_box_sizer.Add(overflow_sizer, flag=wx.ALL, border=self._FromDIP(2))
            overflow_box_sizer.AddStretchSpacer(1)

            adv_sizer.Add(overflow_box_sizer, flag=wx.ALL|wx.EXPAND,
                border=self._FromDIP(2))


        timer_box = wx.StaticBox(adv_win, label='Run Timer')
        timer_box_sizer = wx.StaticBoxSizer(timer_box, wx.HORIZONTAL)

        self.start_flow_timer_btn = wx.Button(timer_box, label='Start flow timer')
        self.stop_flow_timer_btn = wx.Button(timer_box, label='Stop flow timer')
        self.flow_timer_run_time_ctrl = wx.TextCtrl(timer_box, size=self._FromDIP((60, -1)),
            validator=utils.CharValidator('float'))
        self.flow_timer_status = wx.StaticText(timer_box, label='Off',
            style=wx.ST_NO_AUTORESIZE, size=self._FromDIP((100, -1)))

        self.start_flow_timer_btn.Bind(wx.EVT_BUTTON, self._on_start_flow_timer)
        self.stop_flow_timer_btn.Bind(wx.EVT_BUTTON, self._on_stop_flow_timer)
        self.stop_flow_timer_btn.Disable()

        ft_button_sizer = wx.BoxSizer(wx.HORIZONTAL)
        ft_button_sizer.Add(self.start_flow_timer_btn,
            flag=wx.ALIGN_CENTER_VERTICAL|wx.RIGHT, border=self._FromDIP(5))
        ft_button_sizer.Add(self.stop_flow_timer_btn, flag=wx.ALIGN_CENTER_VERTICAL)

        ft_sizer = wx.GridBagSizer(vgap=self._FromDIP(5), hgap=self._FromDIP(5))
        ft_sizer.Add(wx.StaticText(timer_box, label='Flow timer status:'),
            (0,0), flag=wx.ALIGN_CENTER_VERTICAL|wx.RIGHT)
        ft_sizer.Add(self.flow_timer_status, (0,1),
            flag=wx.ALIGN_CENTER_VERTICAL)
        ft_sizer.Add(wx.StaticText(timer_box, label='Run time [min]:'), (1,0),
            flag=wx.ALIGN_CENTER_VERTICAL)
        ft_sizer.Add(self.flow_timer_run_time_ctrl, (1,1),
            flag=wx.ALIGN_CENTER_VERTICAL)
        ft_sizer.Add(ft_button_sizer, (2,0), span=(1,2),
            flag=wx.ALIGN_CENTER_VERTICAL)

        timer_box_sizer.Add(ft_sizer, flag=wx.ALL, border=self._FromDIP(2))
        timer_box_sizer.AddStretchSpacer(1)

        adv_sizer.Add(timer_box_sizer, flag=wx.ALL|wx.EXPAND, border=self._FromDIP(5))


        actions_box = wx.StaticBox(adv_win, label='Actions')
        actions_box_sizer = wx.StaticBoxSizer(actions_box, wx.HORIZONTAL)

        self.put_in_water_btn = wx.Button(actions_box, label='Put in water')
        self.put_in_ethanol_btn = wx.Button(actions_box, label='Put in ethanol')
        self.put_in_hellmanex_btn = wx.Button(actions_box, label='Put in hellmanex')
        self.clean_btn = wx.Button(actions_box, label='Clean')

        self.put_in_water_btn.Bind(wx.EVT_BUTTON, self._on_put_in_water)
        self.put_in_hellmanex_btn.Bind(wx.EVT_BUTTON, self._on_put_in_hellmanex)
        self.put_in_ethanol_btn.Bind(wx.EVT_BUTTON, self._on_put_in_ethanol)
        self.clean_btn.Bind(wx.EVT_BUTTON, self._on_clean)

        aux_btn_sizer = wx.FlexGridSizer(cols=2, hgap=self._FromDIP(5), vgap=self._FromDIP(5))
        aux_btn_sizer.Add(self.put_in_water_btn, flag=wx.ALIGN_CENTER_VERTICAL)
        aux_btn_sizer.Add(self.put_in_hellmanex_btn, flag=wx.ALIGN_CENTER_VERTICAL)
        aux_btn_sizer.Add(self.put_in_ethanol_btn, flag=wx.ALIGN_CENTER_VERTICAL)
        aux_btn_sizer.Add(self.clean_btn, flag=wx.ALIGN_CENTER_VERTICAL)

        self.air_alarm_action = wx.Choice(actions_box, choices=['None', 'Warn', 'Stop'])
        self.air_alarm_action.SetStringSelection('Warn')

        air_sizer = wx.BoxSizer(wx.HORIZONTAL)
        air_sizer.Add(wx.StaticText(actions_box, label='On air alarm:'), border=self._FromDIP(2),
            flag=wx.ALIGN_CENTER_VERTICAL|wx.RIGHT)
        air_sizer.Add(self.air_alarm_action)

        actions_top_sizer = wx.BoxSizer(wx.VERTICAL)
        actions_top_sizer.Add(aux_btn_sizer)
        actions_top_sizer.Add(air_sizer, flag=wx.TOP, border=self._FromDIP(5))

        actions_box_sizer.Add(actions_top_sizer, flag=wx.ALL, border=self._FromDIP(2))
        actions_box_sizer.AddStretchSpacer(1)

        adv_sizer.Add(actions_box_sizer, flag=wx.ALL|wx.EXPAND, border=self._FromDIP(2))


        if self.top_settings['device_communication'] == 'local':
            show_pump_btn = wx.Button(adv_win, label='Pump Ctrl.')
            show_fm_btn = wx.Button(adv_win, label='Flow Meter Ctrl.')
            show_valve_btn = wx.Button(adv_win, label='Valve Ctrl.')

            show_pump_btn.Bind(wx.EVT_BUTTON, self._on_show_pumps)
            show_fm_btn.Bind(wx.EVT_BUTTON, self._on_show_fms)
            show_valve_btn.Bind(wx.EVT_BUTTON, self._on_show_valves)

            local_ctrl_sizer = wx.BoxSizer(wx.HORIZONTAL)
            local_ctrl_sizer.Add(show_pump_btn)
            local_ctrl_sizer.Add(show_fm_btn, flag=wx.LEFT, border=self._FromDIP(5))
            local_ctrl_sizer.Add(show_valve_btn, flag=wx.LEFT, border=self._FromDIP(5))


            adv_sizer.Add(local_ctrl_sizer,
                flag=wx.LEFT|wx.RIGHT|wx.BOTTOM|wx.EXPAND, border=self._FromDIP(2))


        adv_win.SetSizer(adv_sizer)


        coflow_ctrl_sizer.Add(basic_ctrl_sizer, border=self._FromDIP(2),
            flag=wx.TOP|wx.LEFT|wx.RIGHT|wx.EXPAND)
        coflow_ctrl_sizer.Add(adv_pane, flag=wx.ALL|wx.EXPAND, border=self._FromDIP(2))


        status_panel = wx.Panel(self)

        self.sheath_flow = wx.StaticText(status_panel, label='0', style=wx.ST_NO_AUTORESIZE,
            size=self._FromDIP((50,-1)))
        self.outlet_flow = wx.StaticText(status_panel, label='0', style=wx.ST_NO_AUTORESIZE,
            size=self._FromDIP((50,-1)))

        self.cell_temp = epics.wx.PVText(status_panel,
            self.settings['coflow_cell_T_pv'], auto_units=False, fg='black',
            style=wx.ST_NO_AUTORESIZE, size=self._FromDIP((50,-1)))

        if self.settings['use_incubator_pvs']:
            self.coflow_inc_temp = epics.wx.PVText(status_panel,
                self.settings['coflow_inc_esensor_T_pv'], auto_units=False,
                fg='black', style=wx.ST_NO_AUTORESIZE, size=self._FromDIP((50,-1)))
            self.coflow_inc_humid = epics.wx.PVText(status_panel,
                self.settings['coflow_inc_esensor_H_pv'], auto_units=False,
                fg='black', style=wx.ST_NO_AUTORESIZE, size=self._FromDIP((50,-1)))
            self.hplc_inc_temp = epics.wx.PVText(status_panel,
                self.settings['hplc_inc_esensor_T_pv'], auto_units=False,
                fg='black', style=wx.ST_NO_AUTORESIZE, size=self._FromDIP((50,-1)))
            self.hplc_inc_humid = epics.wx.PVText(status_panel,
                self.settings['hplc_inc_esensor_H_pv'], auto_units=False,
                fg='black', style=wx.ST_NO_AUTORESIZE, size=self._FromDIP((50,-1)))


        self.status = wx.StaticText(status_panel, label='Coflow off', style=wx.ST_NO_AUTORESIZE,
            size=self._FromDIP((125, -1)))
        self.status.SetForegroundColour(wx.RED)
        fsize = self.GetFont().GetPointSize()
        font = wx.Font(fsize, wx.DEFAULT, wx.NORMAL, wx.BOLD)
        self.status.SetFont(font)

        status_label = wx.StaticText(status_panel, label='Status:')
        sheath_label = wx.StaticText(status_panel, label='Sheath flow [{}]:'.format(units))
        outlet_label = wx.StaticText(status_panel, label='Outlet flow [{}]:'.format(units))
        temp_label = wx.StaticText(status_panel, label='Cell Temp. [C]:')


        status_grid_sizer = wx.FlexGridSizer(cols=2, vgap=self._FromDIP(5), hgap=self._FromDIP(2))
        status_grid_sizer.Add(status_label, flag=wx.ALIGN_CENTER_VERTICAL)
        status_grid_sizer.Add(self.status, flag=wx.ALIGN_CENTER_VERTICAL)
        status_grid_sizer.Add(sheath_label, flag=wx.ALIGN_CENTER_VERTICAL)
        status_grid_sizer.Add(self.sheath_flow, flag=wx.ALIGN_CENTER_VERTICAL)
        status_grid_sizer.Add(outlet_label, flag=wx.ALIGN_CENTER_VERTICAL)
        status_grid_sizer.Add(self.outlet_flow, flag=wx.ALIGN_CENTER_VERTICAL)
        status_grid_sizer.Add(temp_label, flag=wx.ALIGN_CENTER_VERTICAL)
        status_grid_sizer.Add(self.cell_temp, flag=wx.ALIGN_CENTER_VERTICAL)

        if self.settings['use_incubator_pvs']:
            status_grid_sizer.Add(wx.StaticText(status_panel, label='Coflow Inc. Temp. [C]:'),
                flag=wx.ALIGN_CENTER_VERTICAL)
            status_grid_sizer.Add(self.coflow_inc_temp, flag=wx.ALIGN_CENTER_VERTICAL)
            status_grid_sizer.Add(wx.StaticText(status_panel, label='Coflow Inc. Humidity [%]:'),
                flag=wx.ALIGN_CENTER_VERTICAL)
            status_grid_sizer.Add(self.coflow_inc_humid, flag=wx.ALIGN_CENTER_VERTICAL)
            status_grid_sizer.Add(wx.StaticText(status_panel, label='HPLC Inc. Temp. [C]:'),
                flag=wx.ALIGN_CENTER_VERTICAL)
            status_grid_sizer.Add(self.hplc_inc_temp, flag=wx.ALIGN_CENTER_VERTICAL)
            status_grid_sizer.Add(wx.StaticText(status_panel, label='HPLC Inc. Humidity [%]:'),
                flag=wx.ALIGN_CENTER_VERTICAL)
            status_grid_sizer.Add(self.hplc_inc_humid, flag=wx.ALIGN_CENTER_VERTICAL)

        coflow_buffer_sizer = self._create_buffer_ctrls(status_panel)

        coflow_status_sizer = wx.StaticBoxSizer(wx.StaticBox(status_panel,
            label='Coflow Status'), wx.HORIZONTAL)
        coflow_status_sizer.Add(status_grid_sizer, border=self._FromDIP(5), flag=wx.ALL)
        coflow_status_sizer.Add(coflow_buffer_sizer, border=self._FromDIP(5),
            flag=wx.LEFT|wx.RIGHT|wx.BOTTOM|wx.EXPAND)

        coflow_status_sizer.AddStretchSpacer(1)

        status_panel.SetSizer(coflow_status_sizer)

        status_panel.Bind(wx.EVT_RIGHT_DOWN, self._onRightMouseButton)

        if platform.system() != 'Darwin':
            self.sheath_flow.Bind(wx.EVT_RIGHT_DOWN, self._onRightMouseButton)
            self.outlet_flow.Bind(wx.EVT_RIGHT_DOWN, self._onRightMouseButton)
            self.status.Bind(wx.EVT_RIGHT_DOWN, self._onRightMouseButton)
            status_label.Bind(wx.EVT_RIGHT_DOWN, self._onRightMouseButton)
            sheath_label.Bind(wx.EVT_RIGHT_DOWN, self._onRightMouseButton)
            outlet_label.Bind(wx.EVT_RIGHT_DOWN, self._onRightMouseButton)


        top_sizer = wx.BoxSizer(wx.VERTICAL)
        top_sizer.Add(coflow_ctrl_sizer, flag=wx.EXPAND)
        top_sizer.Add(status_panel, border=self._FromDIP(10), flag=wx.EXPAND|wx.TOP)

        self.SetSizer(top_sizer)

    def _create_buffer_ctrls(self, parent):
        buffer_box = wx.StaticBox(parent, label='Buffers')

        self._buffer_list = utils.BufferList(buffer_box,
            size=self._FromDIP((-1, 100)),style=wx.LC_REPORT|wx.BORDER_SUNKEN)

        self._add_edit_buffer1_btn = wx.Button(buffer_box, label='Add/Edit Buffer')
        self._remove_buffer1_btn = wx.Button(buffer_box, label='Remove Buffer')

        self._add_edit_buffer1_btn.Bind(wx.EVT_BUTTON, self._on_add_edit_buffer)
        self._remove_buffer1_btn.Bind(wx.EVT_BUTTON, self._on_remove_buffer)

        button1_sizer = wx.BoxSizer(wx.HORIZONTAL)
        button1_sizer.Add(self._add_edit_buffer1_btn, flag=wx.RIGHT,
            border=self._FromDIP(5))
        button1_sizer.Add(self._remove_buffer1_btn)

        buffer_sizer = wx.StaticBoxSizer(buffer_box, wx.VERTICAL)
        buffer_sizer.Add(self._buffer_list, flag=wx.EXPAND|wx.ALL,
            proportion=1, border=self._FromDIP(5))
        buffer_sizer.Add(button1_sizer, flag=wx.LEFT|wx.RIGHT|wx.BOTTOM,
            border=self._FromDIP(5))

        return buffer_sizer

    def on_collapse(self, event):
        self.Layout()
        self.Refresh()
        self.SendSizeEvent()

        self.GetParent().Layout()
        self.GetParent().Refresh()
        self.GetParent().SendSizeEvent()

        try:
            wx.FindWindowByName('biocon').Layout()
            wx.FindWindowByName('biocon').Fit()
            wx.FindWindowByName('biocon').Refresh()
            wx.FindWindowByName('biocon').SendSizeEvent()
        except Exception:
            pass

    def _on_show_pumps(self, evt):
        coflow_ctrl = self.com_thread.get_device(self.top_settings['device_data']['name'])
        pump_com_thread = coflow_ctrl.coflow_pump_con

        setup_pumps = [self.settings['sheath_pump'], self.settings['outlet_pump']]
        pump_settings = {
            'remote'        : False,
            'device_init'   : setup_pumps,
            'com_thread'    : pump_com_thread,
        }

        pump_frame = pumpcon.PumpFrame('PumpFrame', pump_settings, parent=self,
            title='Pump Control')
        pump_frame.Show()

    def _on_show_fms(self, evt):
        coflow_ctrl = self.com_thread.get_device(self.top_settings['device_data']['name'])
        fm_com_thread = coflow_ctrl.coflow_fm_con

        setup_fms = [self.settings['sheath_fm'], self.settings['outlet_fm']]
        fm_settings = {
            'remote'        : False,
            'device_init'   : setup_fms,
            'com_thread'    : fm_com_thread,
        }

        fm_frame = fmcon.FlowMeterFrame('FMFrame', fm_settings, parent=self,
            title='Flow Meter Control')
        fm_frame.Show()

    def _on_show_valves(self, evt):
        coflow_ctrl = self.com_thread.get_device(self.top_settings['device_data']['name'])
        valve_com_thread = coflow_ctrl.coflow_valve_con

        setup_valves = [self.settings['sheath_valve']]
        valve_settings = {
            'remote'        : False,
            'device_init'   : setup_valves,
            'com_thread'    : valve_com_thread,
        }

        valve_frame = valvecon.ValveFrame('ValveFrame', valve_settings, parent=self,
            title='Pump Control')
        valve_frame.Show()

    def showMessageDialog(self, parent, msg, title, style):
        dialog = wx.MessageDialog(parent, msg, title, style=style)
        ret = dialog.ShowModal()
        dialog.Destroy()

        return ret

    def _on_startbutton(self, evt):
        valid, flow_rate = self._validate_flow_rate()

        if valid:
            self.start_flow(False)

    def _on_stopbutton(self, evt):
        stop_ft_cmd = ['stop_flow_timer', [self.name,], {}]
        self._send_cmd(stop_ft_cmd, get_response=False)

        stop_bc_cmd = ['stop_change_buffer', [self.name,], {}]
        self._send_cmd(stop_bc_cmd, get_response=False)

        self.stop_flow()

    def _on_changebutton(self, evt):
        self.change_flow(start_monitor=True)

    def _on_change_buffer(self, evt):
        self.stop_flow_timer()

        valve_pos = [self.get_sheath_valve_position(),]

        self.change_buffer(valve_pos, interactive=True)

    def get_flow_rate(self):
        return self.flow_rate.GetValue()

    def change_buffer(self, valve_positions, interactive=True):

        self.verbose_buffer_change = interactive

        if interactive:
            valve_pos = valve_positions[0]
            if int(valve_pos) > 7:

                msg = ('The sheath buffer valve position is set to {}. For buffer '
                    'it is usually 1-7. Please verify that the valve position '
                    'is correct before proceeding and change if necessary. '
                    'Click okay to continue.'.format(valve_pos))

                ret = self.showMessageDialog(self, msg, "Check sheath buffer valve",
                        wx.OK|wx.CANCEL|wx.ICON_INFORMATION)

                if ret == wx.ID_CANCEL:
                    return

            sheath_flow = self.settings['buffer_change_fr']*self.settings['sheath_excess']

            #Change buffer bottle
            msg = ('Change the buffer bottle in the coflow setup. Click okay to continue. '
                'Buffer will flow for ~{} mL (~{} minutes) to flush the '
                'system.'.format(self.settings['buffer_change_vol'],
                    round(self.settings['buffer_change_vol']/sheath_flow,1)))

            ret = self.showMessageDialog(self, msg, "Change buffer",
                    wx.OK|wx.CANCEL|wx.ICON_INFORMATION)

            if ret == wx.ID_CANCEL:
                return


        buffer_change_seq = []

        for pos in valve_positions:
            buffer_change_seq.append([self.settings['buffer_change_fr'],
                self.settings['buffer_change_vol'], pos])

        change_buf_cmd = ['change_buffer', [self.name, buffer_change_seq,], {}]
        self._send_cmd(change_buf_cmd, get_response=False)

        bc_tr_cmd = ['get_bc_time_remaining', [self.name,], {}]
        bc_tr = self._send_cmd(bc_tr_cmd, get_response=True)

        wx.CallAfter(self.set_status_label, 'Changing buffer')

        #Start flow timer
        self.doing_buffer_change = True
        self._start_flow_timer(bc_tr, start_ft_monitor=False)
        self._set_start_flow_button_status()

    def _on_put_in_water(self, evt):
        logger.info('Putting coflow cell into water')
        self._put_in_water()

    def _put_in_water(self):
        self.stop_flow_timer()
        self.change_buffer([self.settings['sheath_valve_water_pos'],], False)

    def _on_put_in_ethanol(self, evt):
        logger.info('Putting coflow cell into ethanol')
        self._put_in_ethanol()

    def _put_in_ethanol(self):
        self.stop_flow_timer()

        valve_positions = [self.settings['sheath_valve_water_pos'],
            self.settings['sheath_valve_ethanol_pos'],
            ]

        self.change_buffer(valve_positions, False)

    def _on_put_in_hellmanex(self, evt):
        logger.info('Putting coflow cell into hellmanex')
        self._put_in_hellmanex()

    def _put_in_hellmanex(self):
        self.stop_flow_timer()

        valve_positions = [self.settings['sheath_valve_water_pos'],
            self.settings['sheath_valve_hellmanex_pos'],
            ]

        self.change_buffer(valve_positions, False)

    def _on_clean(self, evt):
        logger.info('Cleaning coflow cell')
        self._clean_cell()

    def _clean_cell(self):
        self.stop_flow_timer()

        valve_positions = [self.settings['sheath_valve_water_pos'],
            self.settings['sheath_valve_hellmanex_pos'],
            self.settings['sheath_valve_water_pos'],
            self.settings['sheath_valve_ethanol_pos'],
            self.settings['sheath_valve_water_pos']
            ]

        self.change_buffer(valve_positions, False)

    def _on_start_overflow(self, evt):
        wx.CallAfter(self._start_overflow)

    def _on_stop_overflow(self, evt):
        wx.CallAfter(self._stop_overflow)

    def _start_overflow(self):
        start_of_cmd = ['start_overflow', [self.name,], {}]
        self._send_cmd(start_of_cmd, get_response=False)

    def _stop_overflow(self):
        stop_of_cmd = ['stop_overflow', [self.name,], {}]
        self._send_cmd(stop_of_cmd, get_response=False)

    def _on_start_flow_timer(self, evt):
        self.start_flow_timer()

    def start_flow_timer(self):

        flow_time = self.flow_timer_run_time_ctrl.GetValue()

        try:
            flow_time= float(flow_time)*60

        except Exception:
            msg = ('The flow time must be a number.')
            title = 'Flow time not set'
            style=wx.OK|wx.ICON_WARNING

            wx.CallAfter(self.showMessageDialog, self, msg, title, style)

            flow_time = None

        if flow_time is not None:
            self._start_flow_timer(flow_time)

    def _start_flow_timer(self, flow_time, start_ft_monitor=True):
        self.set_flow_timer_time_remaining(flow_time)
        if start_ft_monitor:
            start_ft_cmd = ['start_flow_timer', [self.name, flow_time], {}]
            self._send_cmd(start_ft_cmd, get_response=False)

        wx.CallAfter(self.stop_flow_timer_btn.Enable)
        wx.CallAfter(self.start_flow_timer_btn.Disable)

    def _on_stop_flow_timer(self, evt):
        stop_ft_cmd = ['stop_flow_timer', [self.name,], {}]
        self._send_cmd(stop_ft_cmd, get_response=False)

        stop_bc_cmd = ['stop_change_buffer', [self.name,], {}]
        self._send_cmd(stop_bc_cmd, get_response=False)

        self.stop_flow(verbose=False)

    def stop_flow_timer(self):
        wx.CallAfter(self.stop_flow_timer_btn.Disable)
        wx.CallAfter(self.start_flow_timer_btn.Enable)
        wx.CallAfter(self.flow_timer_status.SetLabel, '')

        # Any time the flow timer stops it interrupts the buffer change sequence
        self.doing_buffer_change = False

    def set_flow_timer_time_remaining(self, tr):
        if tr < 3600:
            tr = time.strftime('%M:%S', time.gmtime(tr))
        elif tr < 86400:
            tr = time.strftime('%H:%M:%S', time.gmtime(tr))
        else:
            tr = time.strftime('%d:%H:%M:%S', time.gmtime(tr))

        wx.CallAfter(self.flow_timer_status.SetLabel, tr)

    def _onRightMouseButton(self, event):

        if int(wx.__version__.split('.')[0]) >= 3 and platform.system() == 'Darwin':
            wx.CallAfter(self._showPopupMenu)
        else:
            self._showPopupMenu()

    def _showPopupMenu(self):

        menu = wx.Menu()
        menu.Append(1, 'Show Plot')

        self.Bind(wx.EVT_MENU, self._onPopupMenuChoice)
        self.PopupMenu(menu)

        menu.Destroy()

    def _onPopupMenuChoice(self, event):
        choice_id = event.GetId()

        if choice_id == 1:
            CoflowPlotFrame(self.sheath_fr_list, self.outlet_fr_list,
                self.fr_time_list, self.sheath_density_list,
                self.outlet_density_list, self.sheath_t_list, self.outlet_t_list,
                self.aux_time_list,self.get_plot_data, self.clear_plot_data, self,
                title='Coflow Plot', name='CoflowPlot', size=(600,500))

    def auto_start(self):
        auto = self.auto_flow.GetValue()

        if auto:
            valid, flow_rate = self._validate_flow_rate()

            if valid:
                self.start_flow(validate=False)
        else:
            valid = True

        return valid

    def auto_stop(self):
        auto = self.auto_flow.GetValue()

        if auto:
            self.stop_flow()

    def set_status_label(self, status):
        self.status.SetLabel(status)
        self._status = status

    def start_flow(self, validate=True):
        logger.debug('Starting flow')

        if not self._coflow_on:
            valid = self.change_flow(validate)

            if valid:
                self._start_flow()

    def _start_flow(self, start_monitor=True):
        self._set_start_flow_button_status()

        start_flow_cmd = ['start_flow', [self.name,], {}]
        self._send_cmd(start_flow_cmd, get_response=False)

        wx.CallAfter(self.set_status_label, 'Coflow on')

        if start_monitor:
            wx.CallAfter(self.monitor_timer.Start, self.settings['settling_time'])

    def _set_start_flow_button_status(self):
        wx.CallAfter(self.start_flow_button.Disable)
        wx.CallAfter(self.change_buffer_button.Disable)
        wx.CallAfter(self.stop_flow_button.Enable)
        wx.CallAfter(self.change_flow_button.Enable)

    def _set_stop_flow_button_status(self):
        self.start_flow_button.Enable()
        self.change_buffer_button.Enable()
        self.stop_flow_button.Disable()
        self.change_flow_button.Disable()

    def stop_flow(self, verbose=True):
        logger.debug('Stopping flow')

        stop_coflow = True

        if 'exposure' in self.top_settings['components']:
            exposure_panel = wx.FindWindowByName('exposure')
            exposure_running = exposure_panel.exp_event.is_set()
        else:
            exposure_running = False

        if exposure_running and verbose:
            msg = ('The exposure is still running. Are you sure you want '
                'to stop the coflow?')

            dialog = wx.MessageDialog(self, msg, 'Verify coflow stop',
                style=wx.YES_NO|wx.NO_DEFAULT|wx.ICON_QUESTION)

            ret = dialog.ShowModal()
            dialog.Destroy()

            if ret == wx.ID_NO:
                stop_coflow = False

        if stop_coflow:
            self._set_stop_flow_button_status()
            self.monitor_timer.Stop()
            self.set_status_label('Coflow off')
            self.stop_flow_timer()

            stop_flow_monitor_cmd = ['stop_flow_monitor', [self.name,], {}]
            self._send_cmd(stop_flow_monitor_cmd, get_response=False)

        if stop_coflow and self._coflow_on:
            stop_flow_cmd = ['stop_flow', [self.name,], {}]
            self._send_cmd(stop_flow_cmd, get_response=False)

    def change_flow(self, validate=True, start_monitor=False):
        logger.debug('Changing flow rate')
        if validate:
            valid, flow_rate = self._validate_flow_rate()
        else:
            flow_rate = float(self.flow_rate.GetValue())
            valid = True

        if valid:
            self._change_flow_rate(flow_rate, start_monitor)

        return valid

    def _change_flow_rate(self, flow_rate, start_monitor=False):
        if start_monitor:
            self.monitor_timer.Stop()
            stop_flow_monitor_cmd = ['stop_flow_monitor', [self.name,], {}]
            self._send_cmd(stop_flow_monitor_cmd, get_response=False)

        change_flow_rate_cmd = ['change_flow_rate', [self.name, flow_rate], {}]
        self._send_cmd(change_flow_rate_cmd, get_response=False)

        if start_monitor:
            self.monitor_timer.Start(self.settings['settling_time'])

    def _validate_flow_rate(self):
        logger.debug('Validating flow rate')
        lc_flow_rate = self.flow_rate.GetValue()

        lc_flow_rate, is_number, is_extreme = self._inner_validate_flow_rate(lc_flow_rate)

        valid = True

        if not is_number:
            msg = ('The flow rate must be a valid number. Please correct this, '
                'then redo your command.')

            wx.CallAfter(wx.MessageBox, msg, 'Error in coflow flow rate',
                style=wx.OK|wx.ICON_ERROR)

            valid = False

        elif is_extreme:
            msg = ('LC flow rates are usually between 0.1-2 mL/min. The '
                'flow rate is currently set outside this range. Do you '
                'want to continue with this flow rate?')

            dialog = wx.MessageDialog(self, msg, 'Possible error in coflow flow rate',
                style=wx.YES_NO|wx.NO_DEFAULT|wx.ICON_QUESTION)

            ret = dialog.ShowModal()
            dialog.Destroy()

            if ret == wx.ID_NO:
                valid = False

        if not valid:
            logger.error('Flow rate not valid')

        return valid, lc_flow_rate

    def _inner_validate_flow_rate(self, lc_flow_rate):
        try:
            lc_flow_rate = float(lc_flow_rate)
            is_number = True
        except Exception:
            is_number = False
            logger.error('Flow rate is not a number')

        if is_number:
            base_units = self.settings['flow_units']
            units = 'mL/min'

            if units in ['nL/s', 'nL/min', 'uL/s', 'uL/min', 'mL/s', 'mL/min']:
                base_vu, base_tu = base_units.split('/')
                new_vu, new_tu = units.split('/')
                if base_vu != new_vu:
                    if (base_vu == 'nL' and new_vu == 'uL') or (base_vu == 'uL' and new_vu == 'mL'):
                        flow_mult = 1./1000.
                    elif base_vu == 'nL' and new_vu == 'mL':
                        flow_mult = 1./1000000.
                    elif (base_vu == 'mL' and new_vu == 'uL') or (base_vu == 'uL' and new_vu == 'nL'):
                        flow_mult = 1000.
                    elif base_vu == 'mL' and new_vu == 'nL':
                        flow_mult = 1000000.
                else:
                    flow_mult = 1.

                if base_tu != new_tu:
                    if base_tu == 'min':
                        flow_mult = flow_mult/60.
                    else:
                        flow_mult = flow_mult*60.

            lc_flow_rate = lc_flow_rate*flow_mult
            logger.debug('Flow rate mult: %f', flow_mult)
            logger.debug('Flow rate is %f %s', lc_flow_rate, units)

            if lc_flow_rate < 0.1 or lc_flow_rate > 2:
                is_extreme = True
                logger.warning('Flow rate is outside of usual range')
            else:
                is_extreme = False
        else:
            is_extreme = False

        return lc_flow_rate, is_number, is_extreme

    def _on_sheath_valve_position_change(self, evt):
        pos = self.sheath_valve_pos.GetValue()

        if int(pos) != self._sheath_valve_pos:
            self._changing_valve = True
            self._expected_valve_pos = pos
            self.set_sheath_valve_position(pos)

    def get_sheath_valve_position(self):
        return self._sheath_valve_pos

    def set_sheath_valve_position(self, position):

        change_pos = True

        if 'exposure' in self.top_settings['components']:
            exposure_panel = wx.FindWindowByName('exposure')
            exposure_running = exposure_panel.exp_event.is_set()
        else:
            exposure_running = False

        if exposure_running:
            msg = ('The exposure is still running. Are you sure you want '
                'to change the sheath valve position?')

            dialog = wx.MessageDialog(self, msg, 'Verify valve position change',
                style=wx.YES_NO|wx.NO_DEFAULT|wx.ICON_QUESTION)

            ret = dialog.ShowModal()
            dialog.Destroy()

            if ret == wx.ID_NO:
                change_pos = False

        if change_pos:
            self._sheath_valve_pos = int(position)
            wx.CallAfter(self.sheath_valve_pos.SafeChangeValue, int(position))

            set_sheath_valve_pos_cmd = ['set_sheath_valve_pos', [self.name, int(position)], {}]
            self._send_cmd(set_sheath_valve_pos_cmd, get_response=False)

    def check_sheath_valve_pos(self):
        pos = self.get_sheath_valve_position()

        if self._sheath_valve_pos != int(pos):
            wx.CallAfter(self.sheath_valve_pos.SafeChangeValue, int(pos))
            self._sheath_valve_pos = int(pos)

    def _on_monitor_timer(self, evt):
        self.monitor_timer.Stop()

        start_flow_monitor_cmd = ['start_flow_monitor', [self.name,], {}]
        self._send_cmd(start_flow_monitor_cmd, get_response=False)

    def get_plot_data(self):
        with self.get_plot_data_lock:

            data = [copy.copy(self.sheath_fr_list), copy.copy(self.outlet_fr_list),
                copy.copy(self.fr_time_list), copy.copy(self.sheath_density_list),
                copy.copy(self.outlet_density_list), copy.copy(self.sheath_t_list),
                copy.copy(self.outlet_t_list), copy.copy(self.aux_time_list)]

        return data

    def clear_plot_data(self):
        with self.get_plot_data_lock:
            self.sheath_fr_list.clear()
            self.outlet_fr_list.clear()
            self.fr_time_list.clear()
            self.sheath_density_list.clear()
            self.outlet_density_list.clear()
            self.sheath_t_list.clear()
            self.outlet_t_list.clear()
            self.aux_time_list.clear()
            self.start_time = time.time()

    def air_detected(self, loc):
        action = self.air_alarm_action.GetStringSelection()

        if action == 'Warn':
            self._show_air_warning_dialog(loc)
            logger.warning('Air detected in %s', loc)

        elif action == 'Stop':
            self._show_air_warning_dialog(loc)
            self.stop_flow()
            logger.error('Air detected in %s', loc)

    def _show_warning_dialog(self, flow, flow_rate):
        if self.warning_dialog is None:
            msg = ('The {} flow rate is unstable. Contact your beamline '
                'scientist.'.format(flow))

            self.warning_dialog = utils.WarningMessage(self, msg,
                'Coflow flow is unstable', self._on_close_flow_warn)
            self.warning_dialog.Show()

    def _show_error_dialog(self, msg, title):
        if self.error_dialog is None:
            self.error_dialog = utils.WarningMessage(self, msg, title,
                self._on_close_error_warn)
            self.error_dialog.Show()

    def _show_air_warning_dialog(self, loc):
        if self.air_warning_dialog is None:
            if loc == 'both':
                msg = ('Air detected in both sheath and outlet flows.')
            else:
                msg = ('Air detected in the {} flow.'.format(loc))

            self.air_warning_dialog = utils.WarningMessage(self, msg,
                'Air detected', self._on_close_air_warn)
            self.air_warning_dialog.Show()

    def _on_close_flow_warn(self):
        self.warning_dialog = None

    def _on_close_error_warn(self):
        self.error_dialog = None

    def _on_close_air_warn(self):
        self.air_warning_dialog = None

    def metadata(self):

        metadata = OrderedDict()

        if self._coflow_on or self.auto_flow.GetValue():
            metadata['Coflow on:'] = True
            metadata['LC flow rate [{}]:'.format(self.settings['flow_units'])] = self._lc_flow_rate
            metadata['Sample cell temperature [C]:'] = self.cell_temp.GetLabel()
            if self.settings['use_incubator_pvs']:
                metadata['Coflow incubator temperature [C]:'] = self.coflow_inc_temp.GetLabel()
                metadata['Coflow incubator humidity [C]:'] = self.coflow_inc_humid.GetLabel()
                metadata['HPLC incubator temperature [C]:'] = self.hplc_inc_temp.GetLabel()
                metadata['HPLC incubator humidity [C]:'] = self.hplc_inc_humid.GetLabel()
            try:
                metadata['Outlet flow rate [{}]:'.format(self.settings['flow_units'])] = round(self._outlet_setpoint,3)
            except Exception:
                metadata['Outlet flow rate [{}]:'.format(self.settings['flow_units'])] = self._outlet_setpoint
            metadata['Sheath ratio:'] = self.settings['sheath_ratio']
            metadata['Sheath excess ratio:'] = self.settings['sheath_excess']
            try:
                metadata['Sheath inlet flow rate (including excess) [{}]:'.format(
                    self.settings['flow_units'])] = round(self._sheath_setpoint, 3)
            except Exception:
                metadata['Sheath inlet flow rate (including excess) [{}]:'.format(
                    self.settings['flow_units'])] = self._sheath_setpoint
            metadata['Sheath valve position:'] = self.get_sheath_valve_position()

        else:
            metadata['Coflow on:'] = False

        return metadata

    def _on_add_edit_buffer(self, evt):
        buffer_info = self._buffer_info

        buffer_entry_dlg = utils.BufferEntryDialog(self, buffer_info,
            title='Add/Edit coflow buffer')
        result = buffer_entry_dlg.ShowModal()

        if result == wx.ID_OK:
            pos, vol, descrip = buffer_entry_dlg.get_settings()
        else:
            vol = None

        buffer_entry_dlg.Destroy()

        try:
            vol = float(vol)
        except Exception:
            vol = None

        if vol is not None:
            vol = vol*1000
            set_buffer_info_cmd = ['set_buffer_info', [self.name, pos, vol, descrip], {}]
            self._send_cmd(set_buffer_info_cmd, get_response=False)

    def _on_remove_buffer(self, evt):
        evt_obj = evt.GetEventObject()

        buffer_info = self._buffer_info

        choices = ['{} - {}'.format(key, buffer_info[key]['descrip'])
            for key in buffer_info]
        choice_pos = [key for key in buffer_info]

        choice_dlg = wx.MultiChoiceDialog(self,
            'Select buffer(s) to remove', 'Remove Buffer', choices)
        result = choice_dlg.ShowModal()

        if result == wx.ID_OK:
            sel_items = choice_dlg.GetSelections()
        else:
            sel_items = None

        choice_dlg.Destroy()

        if sel_items is not None:
            remove_pos = [choice_pos[i] for i in sel_items]

            for pos in remove_pos:
                remove_buffer_cmd = ['remove_buffer', [self.name, pos], {}]
                self._send_cmd(remove_buffer_cmd, get_response=False)

                self._remove_buffer_from_list(pos)

    def _update_all_buffers(self, buffers):
        for key, value in buffers.items():
            pos = key
            vol = value['vol']
            descrip = value['descrip']
            self._update_buffer_list(pos, vol, descrip)

        self._buffer_info = buffers

    def _update_buffer_list(self, pos, vol, descrip):
        buffer_list = self._buffer_list
        buffer_info = self._buffer_info

        vol = round(vol,1)

        update = True
        new_item = False
        if pos in buffer_info:
            cur_vol = buffer_info[pos]['vol']
            cur_descrip = buffer_info[pos]['descrip']

            if round(cur_vol,1) == vol and cur_descrip == descrip:
                update = False

        else:
            new_item = True

        vol = round(vol/1000., 4)

        if update:
            new_insert_pos = -1

            for i in range(buffer_list.GetItemCount()):
                item = buffer_list.GetItem(i)
                item_pos = buffer_list.GetItemData(i)

                if new_item and item_pos > int(pos):
                    new_insert_pos = i
                    break

                elif not new_item and item_pos == int(pos):
                    modif_pos = i
                    break

            if new_item:
                if new_insert_pos == -1:
                    new_insert_pos = buffer_list.GetItemCount()

                buffer_list.InsertItem(new_insert_pos, str(pos))
                buffer_list.SetItem(new_insert_pos, 1, str(vol))
                buffer_list.SetItem(new_insert_pos, 2, descrip)
                buffer_list.SetItemData(new_insert_pos, int(pos))
            else:
                buffer_list.SetItem(modif_pos, 1, str(vol))
                buffer_list.SetItem(modif_pos, 2, descrip)

    def _remove_buffer_from_list(self, pos):
        buffer_list = self._buffer_list


        for i in range(buffer_list.GetItemCount()):
            item = buffer_list.GetItem(i)
            item_pos = buffer_list.GetItemData(i)

            if item_pos == int(pos):
                buffer_list.DeleteItem(i)
                break

    def _get_automator_state(self):
        if self.doing_buffer_change:
            state = 'change_buf'

        elif self._coflow_on:
            state = 'idle'

        else:
            state = 'idle'

        return state

    def automator_callback(self, cmd_name, cmd_args, cmd_kwargs):
        success = True

        if cmd_name == 'status':
            state = self._get_automator_state()

        elif cmd_name == 'abort':
            if self.doing_buffer_change:
                self.stop_flow(False)
            state = 'idle'

        elif cmd_name == 'start':
            flow_rate = float(cmd_kwargs['flow_rate'])
            self._change_flow_rate(flow_rate, True)
            self._start_flow()
            state = self._get_automator_state()
            wx.CallAfter(self.flow_rate.ChangeValue, str(flow_rate))

        elif cmd_name == 'stop':
            self.stop_flow(False)
            state = 'idle'

        elif cmd_name == 'change_flow':
            flow_rate = float(cmd_kwargs['flow_rate'])
            self._change_flow_rate(flow_rate, True)
            state = self._get_automator_state()
            wx.CallAfter(self.flow_rate.ChangeValue, str(flow_rate))

        elif cmd_name == 'change_buf':
            buffer_pos = int(cmd_kwargs['buffer_pos'])
            self.change_buffer([buffer_pos,], False)
            state = 'change_buf'

        elif cmd_name == 'clean':
            self._clean_cell()
            state = 'change_buf'

        elif cmd_name == 'into_hellmanex':
            self._put_in_hellmanex()
            state = 'change_buf'

        elif cmd_name == 'into_ethanol':
            self._put_in_ethanol()
            state = 'change_buf'

        elif cmd_name == 'into_water':
            self._put_in_water()
            state = 'change_buf'

        elif cmd_name == 'overflow_on':
            start_overflow_cmd = ['start_overflow', [self.name,], {}]
            self._send_cmd(start_overflow_cmd, get_response=False)
            state = self._get_automator_state()

        elif cmd_name == 'overflow_off':
            stop_overflow_cmd = ['stop_overflow', [self.name,], {}]
            self._send_cmd(stop_overflow_cmd, get_response=False)
            state = self._get_automator_state()

        elif cmd_name == 'full_status':
            if self._status.lower() == 'coflow on':
                status = 'Flowing'
            elif self._status.lower() == 'changing buffer':
                status = 'Equilibrating'
            elif self._status.lower() == 'coflow off':
                status = 'Stopped'
            else:
                status = 'Unknown'

            state = {
                'status'    : status,
                'fr'        : str(self._lc_flow_rate),
            }

        return state, success

    def _on_close(self):
        if self.connected:
            logger.debug('Closing all coflow devices')

            try:
                plot_window = wx.FindWindowByName('CoflowPlot')
                plot_window._on_exit(None)
            except Exception:
                pass

    def on_exit(self):
        self.close()

class CoflowPlotFrame(wx.Frame):
    def __init__(self, sheath_flow_rate, outlet_flow_rate, t_flow_rate, sheath_density,
        outlet_density, sheath_temperature, outlet_temperature, t_other,
        data_update_callback, clear_callback, *args, **kwargs):

        logger.debug('Setting up CoflowPlotFrame')

        super(CoflowPlotFrame, self).__init__(*args, **kwargs)

        self.sheath_flow_rate = sheath_flow_rate
        self.outlet_flow_rate = outlet_flow_rate
        self.t_flow_rate = t_flow_rate
        self.sheath_density = sheath_density
        self.outlet_density = outlet_density
        self.sheath_temperature = sheath_temperature
        self.outlet_temperature = outlet_temperature
        self.t_other = t_other

        self.data_update_callback = data_update_callback
        self.clear_callback = clear_callback

        self.plot_type = 'Both Flows'

        self.line1 = None
        self.line2 = None

        self.t_axis_incrementer = 10 #Helps prevent constantly flashing on linux from non-buffered redraw of axis limits

        self._create_layout()

        self.Bind(wx.EVT_CLOSE, self._on_exit)

        # Connect the callback for the draw_event so that window resizing works:
        self.cid = self.canvas.mpl_connect('draw_event', self.ax_redraw)
        self.canvas.mpl_connect('motion_notify_event', self._onMouseMotionEvent)

        self.plot_data()

        self.Raise()
        self.Show()

    def _create_layout(self):

        top_panel = wx.Panel(self)

        plt_choices = ['Both Flows', 'Sheath Flow', 'Outlet Flow', 'Both Densities', 'Both Temperatures']
        self.plot_type_choice = wx.Choice(top_panel, choices=plt_choices)
        self.plot_type_choice.SetStringSelection('Both Flows')
        self.plot_type_choice.Bind(wx.EVT_CHOICE, self._on_change_type)

        update_button = wx.Button(top_panel, label='Update')
        update_button.Bind(wx.EVT_BUTTON, self._on_update)

        self.auto_update_timer = wx.Timer(self)
        self.Bind(wx.EVT_TIMER, self._on_update, self.auto_update_timer)

        auto_update = wx.CheckBox(top_panel, label='Auto Update')
        auto_update.Bind(wx.EVT_CHECKBOX, self._on_autoupdate_button)

        clear_button = wx.Button(top_panel, label='Clear Plot')
        clear_button.Bind(wx.EVT_BUTTON, self._on_clear)

        ctrl_sizer = wx.FlexGridSizer(cols=5, rows=1, vgap=2, hgap=5)
        ctrl_sizer.Add(wx.StaticText(top_panel, label='Plot:'),
            flag=wx.ALIGN_CENTER_VERTICAL)
        ctrl_sizer.Add(self.plot_type_choice, flag=wx.ALIGN_CENTER_VERTICAL)
        ctrl_sizer.Add(update_button, flag=wx.ALIGN_CENTER_VERTICAL)
        ctrl_sizer.Add(auto_update, flag=wx.ALIGN_CENTER_VERTICAL)
        ctrl_sizer.Add(clear_button, flag=wx.ALIGN_CENTER_VERTICAL)


        self.fig = Figure((5,4), 75)

        self.subplot = self.fig.add_subplot(1,1,1)
        self.subplot.set_xlabel('Time since start [s]')
        self.subplot.set_ylabel('Flow rate [mL/min]')

        self.fig.subplots_adjust(left = 0.13, bottom = 0.1, right = 0.93, top = 0.93, hspace = 0.26)
        self.fig.set_facecolor('white')

        self.canvas = FigureCanvasWxAgg(top_panel, wx.ID_ANY, self.fig)
        self.canvas.SetBackgroundColour('white')

        self.toolbar = utils.CustomPlotToolbar(self.canvas)
        self.toolbar.Realize()

        plot_sizer = wx.BoxSizer(wx.VERTICAL)
        plot_sizer.Add(self.canvas, 1, wx.EXPAND)
        plot_sizer.Add(self.toolbar, 0, wx.EXPAND)


        top_sizer = wx.BoxSizer(wx.VERTICAL)
        top_sizer.Add(ctrl_sizer, border=5, flag=wx.TOP|wx.LEFT|wx.RIGHT)
        top_sizer.Add(plot_sizer, proportion=1, border=5, flag=wx.EXPAND|wx.TOP)
        top_panel.SetSizer(top_sizer)


        frame_sizer = wx.BoxSizer(wx.HORIZONTAL)
        frame_sizer.Add(top_panel, flag=wx.EXPAND, proportion=1)
        self.SetSizer(frame_sizer)

    def _on_change_type(self, evt):
        self.plot_type = self.plot_type_choice.GetStringSelection()

        if (self.plot_type == 'Both Flows' or self.plot_type == 'Sheath Flow'
            or self.plot_type == 'Outlet Flow'):
            self.subplot.set_ylabel('Flow rate [mL/min]')
        elif self.plot_type == 'Both Densities':
            self.subplot.set_ylabel('Density [g/L]')
        else:
            self.subplot.set_ylabel('Temperature [C]')

        self.canvas.mpl_disconnect(self.cid)
        self.updatePlot(True)
        self.cid = self.canvas.mpl_connect('draw_event', self.ax_redraw)

        self.plot_data()

    def _on_update(self, evt):

        self._update_data()

    def _update_data(self):
        (self.sheath_flow_rate,
        self.outlet_flow_rate,
        self.t_flow_rate,
        self.sheath_density,
        self.outlet_density,
        self.sheath_temperature,
        self.outlet_temperature,
        self.t_other) = self.data_update_callback()

        self.plot_data()

    def _on_autoupdate_button(self, evt):
        if evt.IsChecked():
            self.auto_update_timer.Start(2000)
        else:
            self.auto_update_timer.Stop()

    def _on_clear(self, evt):
        self.clear_callback()
        self._update_data()

    def ax_redraw(self, widget=None):
        ''' Redraw plots on window resize event '''
        self.background = self.canvas.copy_from_bbox(self.subplot.bbox)

        self.canvas.mpl_disconnect(self.cid)
        self.updatePlot()
        self.cid = self.canvas.mpl_connect('draw_event', self.ax_redraw)

    def plot_data(self):
        self.canvas.mpl_disconnect(self.cid)

        if self.plot_type == 'Both Flows':
            xdata = self.t_flow_rate
            ydata1 = self.sheath_flow_rate
            ydata2 = self.outlet_flow_rate

            if self.line1 is not None:
                self.line1.set_visible(True)

            if self.line2 is not None:
                self.line2.set_visible(True)

        elif self.plot_type == 'Sheath Flow':
            xdata = self.t_flow_rate
            ydata1 = self.sheath_flow_rate
            ydata2 = None

            if self.line1 is not None:
                self.line1.set_visible(True)

            if self.line2 is not None:
                self.line2.set_visible(False)

        elif self.plot_type == 'Outlet Flow':
            xdata = self.t_flow_rate
            ydata1 = None
            ydata2 = self.outlet_flow_rate

            if self.line1 is not None:
                self.line1.set_visible(False)

            if self.line2 is not None:
                self.line2.set_visible(True)

        elif self.plot_type == 'Both Densities':
            xdata = self.t_other
            ydata1 = self.sheath_density
            ydata2 = self.outlet_density

            if self.line1 is not None:
                self.line1.set_visible(True)

            if self.line2 is not None:
                self.line2.set_visible(True)

        elif self.plot_type == 'Both Temperatures':
            xdata = self.t_other
            ydata1 = self.sheath_temperature
            ydata2 = self.outlet_temperature

            if self.line1 is not None:
                self.line1.set_visible(True)

            if self.line2 is not None:
                self.line2.set_visible(True)

        redraw = False

        if ydata1 is not None:
            if self.line1 is None:
                self.line1, = self.subplot.plot(xdata, ydata1, animated=True,
                    label='Sheath')
                redraw = True
            else:
                self.line1.set_xdata(xdata)
                self.line1.set_ydata(ydata1)

        if ydata2 is not None:
            if self.line2 is None:
                self.line2, = self.subplot.plot(xdata, ydata2, animated=True,
                    label='Outlet')
                redraw = True
            else:
                self.line2.set_xdata(xdata)
                self.line2.set_ydata(ydata2)

        if redraw:
            self.canvas.draw()
            self.background = self.canvas.copy_from_bbox(self.subplot.bbox)
            self.subplot.legend()

        self.updatePlot()

        self.cid = self.canvas.mpl_connect('draw_event', self.ax_redraw)

    def updatePlot(self, redraw=False):

        # oldx = self.subplot.get_xlim()
        # oldy = self.subplot.get_ylim()

        # self.subplot.set_autoscale_on(True)
        # self.subplot.relim()
        # self.subplot.autoscale_view()

        # newx = self.subplot.get_xlim()
        # newy = self.subplot.get_ylim()

        # newx = [newx[0], newx[1]]
        # newy = [newy[0], newy[1]]

        # oldx = [oldx[0], oldx[1]]
        # oldy = [oldy[0], oldy[1]]

        # print('start')
        # print(oldx)
        # print(newx)



        # # if not x_sim:
        #     # print('new x')

        # if newx[0] < oldx[0] + self.t_axis_incrementer:
        #     newx[0] = oldx[0]

        # if newx[1] > oldx[1]:
        #     newx[1] = newx[1] + self.t_axis_incrementer

        # else:
        #     newx[1] = oldx[1]

        # print('final')
        # print(oldx)
        # print(newx)

        # if newx[0] != oldx[0] or newx[1] != oldx[1]:
        #     redraw = True
        #     self.subplot.set_xlim(newx[0], newx[1])

        # if newy[0] != oldy[0] or newy[1] != oldy[1]:
        #     redraw = True

        # if redraw:
        #     self.canvas.draw()

        # self.canvas.restore_region(self.background)

        # if self.line1 is not None:
        #     self.subplot.draw_artist(self.line1)
        # if self.line2 is not None:
        #     self.subplot.draw_artist(self.line2)

        # self.canvas.blit(self.subplot.bbox)

        oldx = self.subplot.get_xlim()
        oldy = self.subplot.get_ylim()

        self.subplot.relim()
        self.subplot.autoscale_view()

        newx = self.subplot.get_xlim()
        newy = self.subplot.get_ylim()

        if newx != oldx or newy != oldy:
            redraw = True

        if redraw:
            self.canvas.draw()

        self.canvas.restore_region(self.background)

        if self.line1 is not None:
            self.subplot.draw_artist(self.line1)
        if self.line2 is not None:
            self.subplot.draw_artist(self.line2)

        self.canvas.blit(self.subplot.bbox)

    def _onMouseMotionEvent(self, event):

        if event.inaxes:
            x, y = event.xdata, event.ydata
            xlabel = self.subplot.xaxis.get_label().get_text()
            ylabel = self.subplot.yaxis.get_label().get_text()

            if abs(y) > 0.001 and abs(y) < 1000:
                y_val = '{:.3f}'.format(round(y, 3))
            else:
                y_val = '{:.3E}'.format(y)

            self.toolbar.set_status('{} = {}, {} = {}'.format(xlabel, x, ylabel, y_val))

        else:
            self.toolbar.set_status('')

    def _on_exit(self, event):
        self.auto_update_timer.Stop()

        self.Destroy()



class CoflowFrame(utils.DeviceFrame):
    """
    A lightweight frame allowing one to work with arbitrary number of coflow controls.
    Only meant to be used when the hplccon module is run directly,
    rather than when it is imported into another program.
    """
    def __init__(self, name, settings, *args, **kwargs):
        """
        Initializes the HPLC frame. Takes args and kwargs for the wx.Frame class.
        """
        super(CoflowFrame, self).__init__(name, settings, CoflowPanel,
            *args, **kwargs)

        # Enable these to init devices on startup
        self.setup_devices = self.settings.pop('device_init', None)

        self._init_devices()


default_coflow_settings = {
    'show_advanced_options'     : False,
    'device_communication'      : 'remote',
    # 'remote_pump_ip'            : '164.54.204.192',
    # 'remote_pump_port'          : '5556',
    # 'remote_fm_ip'              : '164.54.204.192',
    # 'remote_fm_port'            : '5557'
    # 'remote_valve_ip'           : '164.54.204.192',
    # 'remote_valve_port'         : '5558',
    'remote_ip'                 : '164.54.204.53',
    'remote_port'               : '5556',
    'remote_device'             : 'coflow',
    'device_init'               : [{'name': 'Coflow', 'args': [], 'kwargs': {
        'remote_overflow_ip'        : '164.54.204.75',
        'flow_units'                : 'mL/min',
        'sheath_pump'               : {'name': 'sheath', 'args': ['VICI M50', 'COM6'],
                                        'kwargs': {'flow_cal': '628.68',
                                        'backlash_cal': '9.95'},
                                        'ctrl_args': {'flow_rate': 1}},
        # 'outlet_pump'               : {'name': 'outlet', 'args': ['VICI M50', 'COM4'],
        #                                 'kwargs': {'flow_cal': '628.68',
        #                                 'backlash_cal': '9.962'},
        #                                 'ctrl_args': {'flow_rate': 1}},
        'outlet_pump'               : {'name': 'outlet', 'args': ['OB1 Pump', 'COM15'],
                                        'kwargs': {'ob1_device_name': 'Outlet OB1', 'channel': 1,
                                        'min_pressure': -1000, 'max_pressure': 1000, 'P': -2, 'I': -0.15,
                                        'D': 0, 'bfs_instr_ID': None, 'comm_lock': None,
                                        'calib_path': './resources/ob1_calib.txt'},
                                        'ctrl_args': {}},
        'sheath_fm'                 : {'name': 'sheath', 'args': ['BFS', 'COM5'],
                                        'kwargs':{}},
        'outlet_fm'                 : {'name': 'outlet', 'args': ['BFS', 'COM3'],
                                        'kwargs':{}},
        'sheath_valve'              : {'name': 'Coflow Sheath',
                                        'args':['Cheminert', 'COM4'],
                                        'kwargs': {'positions' : 10}},
        # 'sheath_pump'               : {'name': 'sheath', 'args': ['Soft', None], # Simulated devices for testing
        #                                 'kwargs': {}},
        # 'outlet_pump'               : {'name': 'outlet', 'args': ['Soft', None],
        #                                 'kwargs': {}},
        # 'sheath_fm'                 : {'name': 'sheath', 'args': ['Soft', None],
        #                                 'kwargs':{}},
        # 'outlet_fm'                 : {'name': 'outlet', 'args': ['Soft', None],
        #                                 'kwargs':{}},
        # 'sheath_valve'              : {'name': 'Coflow Sheath',
        #                                 'args': ['Soft', None],
        #                                 'kwargs': {'positions' : 10}},
        'sheath_ratio'              : 0.3,
        'sheath_excess'             : 1.5,
        'sheath_warning_threshold_low'  : 0.8,
        'sheath_warning_threshold_high' : 1.2,
        # 'outlet_warning_threshold_low'  : 0.8,
        # 'outlet_warning_threshold_high' : 1.2,
        'outlet_warning_threshold_low'  : 0.98,
        'outlet_warning_threshold_high' : 1.02,
        'sheath_fr_mult'            : 1,
        'outlet_fr_mult'            : 1,
        # 'outlet_fr_mult'            : -1,
        # 'settling_time'             : 5000, #in ms
        'settling_time'             : 120000, #in ms
        'lc_flow_rate'              : '0.6',
        'show_sheath_warning'       : True,
        'show_outlet_warning'       : True,
        'use_overflow_control'      : True,
        'buffer_change_fr'          : 1.19, #in ml/min
        'buffer_change_vol'         : 11.1, #in ml
        'air_density_thresh'        : 700, #g/L
        'sheath_valve_water_pos'    : 10,
        'sheath_valve_hellmanex_pos': 8,
        'sheath_valve_ethanol_pos'  : 9,
        'coflow_cell_T_pv'          : '18ID:ETC:Ti1',
        'coflow_inc_esensor_T_pv'   : '18ID:EnvMon:CoflowInc:TempC',
        'coflow_inc_esensor_H_pv'   : '18ID:EnvMon:CoflowInc:Humid',
        'hplc_inc_esensor_T_pv'     : '18ID:EnvMon:HPLCInc:TempC',
        'hplc_inc_esensor_H_pv'     : '18ID:EnvMon:HPLCInc:Humid',
        'use_incubator_pvs'         : True,
        }}],
    }

if __name__ == '__main__':
    logger = logging.getLogger()
    logger.setLevel(logging.DEBUG)
    h1 = logging.StreamHandler(sys.stdout)
    h1.setLevel(logging.INFO)
    # h1.setLevel(logging.DEBUG)
    # h1.setLevel(logging.ERROR)

    # formatter = logging.Formatter('%(asctime)s - %(message)s')
    formatter = logging.Formatter('%(asctime)s - %(name)s - %(threadName)s - %(levelname)s - %(message)s')
    h1.setFormatter(formatter)
    logger.addHandler(h1)

    # logger = logging.getLogger('biocon')
    # logger.setLevel(logging.DEBUG)
    # h1 = logging.StreamHandler(sys.stdout)
    # h1.setLevel(logging.INFO)
    # formatter = logging.Formatter('%(asctime)s - %(name)s - %(threadName)s - %(levelname)s - %(message)s')
    # h1.setFormatter(formatter)
    # logger.addHandler(h1)

    #Settings
    coflow_settings = default_coflow_settings

    coflow_settings['components'] = ['coflow']

    # Remote
    com_thread = None

    # # Local
    # com_thread = CoflowCommThread('CoflowCon')
    # com_thread.start()
    # coflow_settings['device_communication'] = 'local'
    # coflow_settings['com_thread'] = com_thread
    # ob1_comm_lock = threading.RLock()
    # outlet_fm_comm_lock = threading.Lock()
    # coflow_settings['device_init'][0]['kwargs']['outlet_pump']['kwargs']['comm_lock'] = ob1_comm_lock
    # coflow_settings['device_init'][0]['kwargs']['outlet_fm']['kwargs']['comm_lock'] = outlet_fm_comm_lock

    app = wx.App()

    # standard_paths = wx.StandardPaths.Get() #Can't do this until you start the wx app
    # info_dir = standard_paths.GetUserLocalDataDir()

    # if not os.path.exists(info_dir):
    #     os.mkdir(info_dir)
    # # if not os.path.exists(os.path.join(info_dir, 'expcon.log')):
    # #     open(os.path.join(info_dir, 'expcon.log'), 'w')
    # h2 = handlers.RotatingFileHandler(os.path.join(info_dir, 'expcon.log'), maxBytes=10e6, backupCount=5, delay=True)
    # h2.setLevel(logging.DEBUG)
    # formatter2 = logging.Formatter('%(asctime)s - %(name)s - %(threadName)s - %(levelname)s - %(message)s')
    # h2.setFormatter(formatter2)

    # logger.addHandler(h2)

    logger.debug('Setting up wx app')
    frame = CoflowFrame('CoflowFrame', coflow_settings, parent=None,
        title='Coflow Control')
    frame.Show()
    app.MainLoop()

    if com_thread is not None:
        com_thread.stop()
        com_thread.join()



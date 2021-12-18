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

matplotlib.rcParams['backend'] = 'WxAgg'


# import fmcon
import client
import pumpcon
import fmcon
import utils

class CoflowControl(object):

    def __init__(self, settings, *args, **kwargs):

        self.settings = settings

        self.coflow_on = False

        self._create_layout()

        self.init_connections()

        self.monitor = False
        self.sheath_setpoint = None
        self.outlet_setpoint = None
        self.lc_flow_rate = None

        self.sheath_is_moving = False
        self.outlet_is_moving = False

        self.pump_sheath_init = False
        self.pump_outlet_init = False
        self.fm_sheath_init = False
        self.fm_outlet_init = False
        self.valve_sheath_init = False

        if not self.timeout_event.is_set():
            self.init_pumps()
            self.init_fms()
            self.init_valves()

        if self.settings['use_overflow_control']:
            self.overflow_connected = True

    def init_connections(self):
        self.coflow_pump_cmd_q = deque()
        self.coflow_pump_return_q = deque()
        self.coflow_pump_abort_event = threading.Event()
        self.coflow_pump_event = threading.Event()

        self.coflow_fm_cmd_q = deque()
        self.coflow_fm_return_q = deque()
        self.coflow_fm_abort_event = threading.Event()
        self.coflow_fm_event = threading.Event()

        self.valve_cmd_q = deque()
        self.valve_return_q = deque()
        self.valve_abort_event = threading.Event()
        self.valve_event = threading.Event()

        self.timeout_event = threading.Event()

        if self.settings['device_communication'] == 'local':
            self.coflow_pump_con = pumpcon.PumpCommThread(self.coflow_pump_cmd_q,
                self.coflow_pump_return_q, self.coflow_pump_abort_event, 'PumpCon')

            self.coflow_fm_con = fmcon.fmCommThread(self.coflow_fm_cmd_q,
                self.coflow_fm_return_q, self.coflow_fm_abort_event, 'FMCon')

            self.local_devices = True

        else:
            pump_ip = self.settings['remote_pump_ip']
            pump_port = self.settings['remote_pump_port']
            self.coflow_pump_con = client.ControlClient(pump_ip, pump_port,
                self.coflow_pump_cmd_q, self.coflow_pump_return_q,
                self.coflow_pump_abort_event, self.timeout_event, name='PumpControlClient')

            fm_ip = self.settings['remote_fm_ip']
            fm_port = self.settings['remote_fm_port']
            self.coflow_fm_con = client.ControlClient(fm_ip, fm_port,
                self.coflow_fm_cmd_q, self.coflow_fm_return_q,
                self.coflow_fm_abort_event, self.timeout_event, name='FMControlClient')

            valve_ip = self.settings['remote_valve_ip']
            valve_port = self.settings['remote_valve_port']
            self.valve_con = client.ControlClient(valve_ip, valve_port,
                self.valve_cmd_q, self.valve_return_q,
                self.valve_abort_event, self.timeout_event, name='ValveControlClient')

            self.local_devices = False

        self.coflow_pump_con.start()
        self.coflow_fm_con.start()
        self.coflow_valve_con.start()

    def init_pumps(self):
        sheath_pump = self.settings['sheath_pump']
        outlet_pump = self.settings['outlet_pump']

        logger.info('Initializing coflow pumps on startup')

        sheath_args = (sheath_pump[1], 'sheath_pump', sheath_pump[0])
        if sheath_pump[0] == 'VICI_M50':
            sheath_kwargs = {'flow_cal': sheath_pump[2][0],
                'backlash_cal': sheath_pump[2][1]}
        else:
            sheath_kwargs = {}

        sheath_init_cmd = ('connect_remote', sheath_args, sheath_kwargs)

        outlet_args = (outlet_pump[1], 'outlet_pump', outlet_pump[0])
        if outlet_pump[0] == 'VICI_M50':
            outlet_kwargs = {'flow_cal': outlet_pump[2][0],
                'backlash_cal': outlet_pump[2][1]}
        else:
            outlet_kwargs = {}

        outlet_init_cmd = ('connect_remote', outlet_args, outlet_kwargs)


        self.pump_sheath_init = self._send_pumpcmd(sheath_init_cmd, response=True)
        self.pump_outlet_init = self._send_pumpcmd(outlet_init_cmd, response=True)

        if self.pump_outlet_init and self.pump_sheath_init:

            self._send_pumpcmd(('set_units', ('sheath_pump', self.settings['flow_units']), {}))
            self._send_pumpcmd(('set_units', ('outlet_pump', self.settings['flow_units']), {}))

            self.sheath_is_moving = self._send_pumpcmd(('is_moving', ('sheath_pump',), {}), response=True)
            self.outlet_is_moving = self._send_pumpcmd(('is_moving', ('outlet_pump',), {}), response=True)

        logger.info('Coflow pumps initialization successful')

    def init_fms(self):
        """
        Initializes the flow meters
        """

        sheath_fm = self.settings['sheath_fm']
        outlet_fm = self.settings['outlet_fm']

        logger.info('Initializing coflow flow meters on startup')

        sheath_args = (sheath_fm[1], 'sheath_fm', sheath_fm[0])

        sheath_init_cmd = ('connect', sheath_args, {})

        outlet_args = (outlet_fm[1], 'outlet_fm', outlet_fm[0])

        outlet_init_cmd = ('connect', outlet_args, {})

        try:
            _, self.fm_sheath_init = self._send_fmcmd(sheath_init_cmd, response=True)
        except Exception:
            self.fm_sheath_init = False

        try:
            _, self.fm_outlet_init = self._send_fmcmd(outlet_init_cmd, response=True)
        except Exception:
            self.fm_outlet_init = False

        if self.fm_outlet_init and self.fm_sheath_init:
            self._send_fmcmd(('set_units', ('sheath_fm', self.settings['flow_units']), {}))
            self._send_fmcmd(('set_units', ('outlet_fm', self.settings['flow_units']), {}))

            self._send_fmcmd(('get_density', ('sheath_fm',), {}), True)
            self._send_fmcmd(('get_density', ('outlet_fm',), {}), True)

            self._send_fmcmd(('get_temperature', ('sheath_fm',), {}), True)
            self._send_fmcmd(('get_temperature', ('outlet_fm',), {}), True)

            self._send_fmcmd(('get_flow_rate', ('sheath_fm',), {}), True)
            self._send_fmcmd(('get_flow_rate', ('outlet_fm',), {}), True)

            logger.info('Coflow flow meters initialization successful')

    def init_valves(self):
        """
        Initializes the valves
        """


        logger.info('Initializing coflow valves on statrtup')

        sheath_valve = self.settings['sheath_valve']
        vtype = sheath_valve[0].replace(' ', '_')
        com = sheath_valve[1]

        args = [com, 'sheath_valve', vtype] + sheath_valve[2]
        kwargs = sheath_valve[3]

        if not self.local_devices:
            cmd = ('connect_remote', args, kwargs)
        else:
            cmd = ('connect', args, kwargs)

        self.valve_sheath_init = self._send_valvecmd(cmd, response=True)

        if self.valve_sheath_init:
            logger.info('Valve initializiation successful.')

    def start_overflow(self):
        ip = self.settings['remote_overflow_ip']
        params = {'c':'1','s':'1', 'u':'user'}
        requests.get('http://{}/?'.format(ip), params=params, timeout=5)

    def stop_overflow(self):
        ip = self.settings['remote_overflow_ip']
        params = {'c':'1','s':'0', 'u':'user'}
        requests.get('http://{}/?'.format(ip), params=params, timeout=5)

    def check_overflow_status(self):
        ip = self.settings['remote_overflow_ip']
        params = {'s':'2', 'u':'user'}

        err = False

        try:
            r = requests.get('http://{}/?'.format(ip), params=params, timeout=1)
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
        sheath_start_cmd = ('start_flow', ('sheath_pump', ), {})
        outlet_start_cmd = ('start_flow', ('outlet_pump', ), {})

        self._send_pumpcmd(sheath_start_cmd)
        self._send_pumpcmd(outlet_start_cmd)

        self.coflow_on = True

        logger.info('Starting coflow pumps')

    def stop_flow(self):
        sheath_stop_cmd = ('stop', ('sheath_pump', ), {})
        outlet_stop_cmd = ('stop', ('outlet_pump', ), {})

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

        self.sheath_setpoint = sheath_flow
        self.outlet_setpoint = outlet_flow

        sheath_fr_cmd = ('set_flow_rate', ('sheath_pump', sheath_flow), {})
        outlet_fr_cmd = ('set_flow_rate', ('outlet_pump', outlet_flow), {})

        logger.info('LC flow input to %f %s', flow_rate, self.settings['flow_units'])
        logger.info('Setting sheath flow to %f %s', sheath_flow, self.settings['flow_units'])
        logger.info('Setting outlet flow to %f %s', outlet_flow, self.settings['flow_units'])

        self._send_pumpcmd(sheath_fr_cmd)
        self._send_pumpcmd(outlet_fr_cmd)

    def get_sheath_flow_rate(self):
        sheath_fr_cmd = ('get_flow_rate', ('sheath_fm',), {})

        ret = self._send_fmcmd(sheath_fr_cmd, True)
        if ret is not None:
            ret_type, ret_val = ret
        else:
            ret_type = None
            ret_val = None

        return ret_val, ret_type

    def get_sheath_density(self):
        sheath_density_cmd = ('get_density', ('sheath_fm',), {})

        ret = self._send_fmcmd(sheath_density_cmd, True)
        if ret is not None:
            ret_type, ret_val = ret
        else:
            ret_type = None
            ret_val = None

        return ret_val, ret_type

    def get_sheath_temperature(self):
        sheath_t_cmd = ('get_temperature', ('sheath_fm',), {})

        ret = self._send_fmcmd(sheath_t_cmd, True)
        if ret is not None:
            ret_type, ret_val = ret
        else:
            ret_type = None
            ret_val = None

        return ret_val, ret_type

    def get_outlet_flow_rate(self):
        outlet_fr_cmd = ('get_flow_rate', ('outlet_fm',), {})

        ret = self._send_fmcmd(outlet_fr_cmd, True)
        if ret is not None:
            ret_type, ret_val = ret
        else:
            ret_type = None
            ret_val = None

        return ret_val, ret_type

    def get_outlet_density(self):
        outlet_density_cmd = ('get_density', ('outlet_fm',), {})

        ret = self._send_fmcmd(outlet_density_cmd, True)
        if ret is not None:
            ret_type, ret_val = ret
        else:
            ret_type = None
            ret_val = None

        return ret_val, ret_type

    def get_outlet_temperature(self):
        outlet_t_cmd = ('get_temperature', ('outlet_fm',), {})

        ret = self._send_fmcmd(outlet_t_cmd, True)
        if ret is not None:
            ret_type, ret_val = ret
        else:
            ret_type = None
            ret_val = None

        return ret_val, ret_type

    def get_sheath_valve_position(self):
        cmd = ('get_position', ('sheath_valve',), {})

        position = self._send_valvecmd(cmd, True)

        return position

    def set_sheath_valve_position(self):
        cmd = ('set_position', ('sheath_valve', position), {})

        ret = self._send_valvecmd(cmd, True)

        if ret is not None and ret[0] == 'set_position':
            if ret[2]:
                logger.info('Set {} position to {}'.format('sheath_valve', position))
                success = True
            else:
                logger.error('Failed to set {} position'.format('sheath_valve'))
                success = False

        else:
            logger.error('Failed to set {} position, no response from the '
                'server.'.format(ret[1].replace('_', ' ')))
            success = False

        return success

    def _send_pumpcmd(self, cmd, response=False):
        ret_val = None

        if not self.timeout_event.is_set():
            if not self.local_devices:
                full_cmd = {'device': 'pump', 'command': cmd, 'response': response}
            else:
                full_cmd = cmd

            self.coflow_pump_cmd_q.append(full_cmd)

            if response:
                while len(self.coflow_pump_return_q) == 0 and not self.timeout_event.is_set():
                    time.sleep(0.01)

                if not self.timeout_event.is_set():
                    ret_val = self.coflow_pump_return_q.popleft()

            wx.CallAfter(self._show_error_dialog, msg, 'Connection error')


        return ret_val

    def _send_fmcmd(self, cmd, response=False):
        """
        Sends commands to the pump using the ``fm_cmd_q`` that was given
        to :py:class:`FlowMeterCommThread`.

        :param str cmd: The command to send, matching the command in the
            :py:class:`FlowMeterCommThread` ``_commands`` dictionary.
        """
        ret_val = (None, None)
        if not self.timeout_event.is_set():
            if not self.local_devices:
                full_cmd = {'device': 'fm', 'command': cmd, 'response': response}
            else:
                full_cmd = cmd

            self.coflow_fm_cmd_q.append(full_cmd)

            if response:
                while len(self.coflow_fm_return_q) == 0 and not self.timeout_event.is_set():
                    time.sleep(0.01)

                if not self.timeout_event.is_set():
                    ret_val = self.coflow_fm_return_q.popleft()

        return ret_val

    def _send_valvecmd(self, cmd, response=False):
        ret_val = None

        if not self.timeout_event.is_set():
            if not self.local_devices:
                full_cmd = {'device': 'valve', 'command': cmd, 'response': response}
            else:
                full_cmd = cmd

            self.valve_cmd_q.append(full_cmd)

            if response:
                while len(self.valve_return_q) == 0 and not self.timeout_event.is_set():
                    time.sleep(0.01)

                if not self.timeout_event.is_set():
                    ret_val = self.valve_return_q.popleft()

        return ret_val

    def disconnect_coflow(self):
        sheath_fm = ('disconnect', ('sheath_fm', ), {})
        outlet_fm = ('disconnect', ('outlet_fm', ), {})

        sheath_pump = ('disconnect', ('sheath_pump', ), {})
        outlet_pump = ('disconnect', ('outlet_pump', ), {})

        sheath_valve = ('disconnect', ('sheath_valve', ), {})

        if not self.timeout_event.is_set():
            self._send_fmcmd(sheath_fm, response=True)
            self._send_fmcmd(outlet_fm, response=True)

            self._send_pumpcmd(sheath_pump, response=True)
            self._send_pumpcmd(outlet_pump, response=True)

            self._send_valvecmd(sheath_valve, repsonse=True)

        self.coflow_pump_con.stop()
        self.coflow_fm_con.stop()
        self.coflow_valve_con.stop()

        if not self.timeout_event.is_set():
            self.coflow_pump_con.join(5)
            self.coflow_fm_con.join(5)
            self.coflow_valve_con.join(5)


class CoflowPanel(wx.Panel):
    """
    This flow meter panel supports standard settings, including connection settings,
    for a flow meter. It is meant to be embedded in a larger application and can
    be instanced several times, once for each flow meter. It communciates
    with the flow meters using the :py:class:`FlowMeterCommThread`. Currently
    it only supports the :py:class:`BFS`, but it should be easy to extend for
    other flow meters. The only things that should have to be changed are
    are adding in flow meter-specific readouts, modeled after how the
    ``bfs_pump_sizer`` is constructed in the :py:func:`_create_layout` function,
    and then add in type switching in the :py:func:`_on_type` function.
    """
    def __init__(self, settings, connect=True, *args, **kwargs):
        """
        Initializes the custom thread. Important parameters here are the
        list of known commands ``_commands`` and known pumps ``known_fms``.

        :param wx.Window parent: Parent class for the panel.

        :param int panel_id: wx ID for the panel.

        :param str panel_name: Name for the panel

        :param list all_comports: A list containing all comports that the flow meter
            could be connected to.

        :param collections.deque fm_cmd_q: The ``fm_cmd_q`` that was passed to
            the :py:class:`FlowMeterCommThread`.

        :param collections.deque fm_return_q: The ``fm_return_q`` that was passed to
            the :py:class:`FlowMeterCommThread`.

        :param list known_fms: The list of known flow meter types, obtained from
            the :py:class:`FlowMeterCommThread`.

        :param str fm_name: An identifier for the flow meter, displayed in the
            flow meter panel.

        :param str fm_type: One of the ``known_fms``, corresponding to the flow
            meter connected to this panel. Only required if you are connecting
            the flow meter when the panel is first set up (rather than manually
            later).

        :param str comport: The comport the flow meter is connected to. Only required
            if you are connecting the flow meter when the panel is first set up (rather
            than manually later).

        :param list fm_args: Flow meter specific arguments for initialization.
            Only required if you are connecting the flow meter when the panel is first
            set up (rather than manually later).

        :param dict fm_kwargs: Flow meter specific keyword arguments for initialization.
            Only required if you are connecting the flow meter when the panel is first
            set up (rather than manually later).

        """

        super(CoflowPanel, self).__init__(*args, **kwargs)
        logger.debug('Initializing CoflowPanel')

        self.settings = settings

        self._create_layout()

        if connect:
            self.connected = True

            self.coflow_control = CoflowControl(self.settings)

            self.warning_dialog = None
            self.error_dialog = None
            self.air_warning_dialog = None
            self.monitor_timer = wx.Timer(self)
            self.Bind(wx.EVT_TIMER, self._on_monitor_timer, self.monitor_timer)
            self.doing_buffer_change = False
            self.buffer_change_sequence = []
            self.verbose_buffer_change = True

            self.connection_timer = wx.Timer(self)
            self.Bind(wx.EVT_TIMER, self._on_connection_timer, self.connection_timer)

            self.flow_timer = wx.Timer(self)
            self.Bind(wx.EVT_TIMER, self._on_flow_timer, self._flow_timer)

            self.stop_get_fr_event = threading.Event()
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

            if (not self.coflow_control.timeout_event.is_set() and self.coflow_control.pump_sheath_init
                and self.coflow_control.pump_outlet_init
                and self.coflow_control.fm_sheath_init
                and self.coflow_control.fm_outlet_init
                and self.coflow_control.valve_sheath_init):

                self.auto_flow.Enable()

                if self.sheath_is_moving or self.outlet_is_moving:
                    self.stop_flow_button.Enable()
                    self.change_flow_button.Enable()

                if self.sheath_is_moving and self.outlet_is_moving:
                    self.start_flow_button.Disable()
                    self.status.SetLabel('Coflow on')
                else:
                    self.start_flow_button.Enable()

                self.check_sheath_valve_pos()

                self.get_fr_thread = threading.Thread(target=self._get_flow_rates)
                self.get_fr_thread.daemon = True
                self.get_fr_thread.start()

            elif self.coflow_control.timeout_event.is_set():
                logger.error('Timeout connecting to the coflow control server.')

                msg = ('Could not connect to the coflow control server. '
                    'Contact your beamline scientist.')

                wx.CallAfter(self.showMessageDialog, self, msg, "Connection error",
                    wx.OK|wx.ICON_ERROR)

            if not self.coflow_control.timeout_event.is_set() and (not self.coflow_control.pump_sheath_init or
                not self.coflow_control.pump_outlet_init):

                if (not self.coflow_control.pump_sheath_init and
                    not self.coflow_control.pump_outlet_init):

                    logger.error('Failed to connect to the sheath and outlet pumps.')

                    msg = ('Could not connect to the coflow sheath and outlet pumps. '
                        'Contact your beamline scientist.')

                    wx.CallAfter(self.showMessageDialog, self, msg, 'Connection error',
                        wx.OK|wx.ICON_ERROR)

                elif not self.coflow_control.pump_sheath_init:

                    logger.error('Failed to connect to the sheath pump.')

                    msg = ('Could not connect to the coflow sheath pump. '
                        'Contact your beamline scientist.')

                    wx.CallAfter(self.showMessageDialog, self, msg, 'Connection error',
                        wx.OK|wx.ICON_ERROR)

                else:

                    logger.error('Failed to connect to the outlet pump.')

                    msg = ('Could not connect to the coflow outlet pump. '
                        'Contact your beamline scientist.')

                    wx.CallAfter(self.showMessageDialog, self, msg, 'Connection error',
                        wx.OK|wx.ICON_ERROR)

            else:
                self.auto_flow.Enable()

            if not self.coflow_control.timeout_event.is_set() and (not self.coflow_control.fm_sheath_init or
                not self.coflow_control.fm_outlet_init):

                if (not self.coflow_control.fm_sheath_init and
                    not self.coflow_control.fm_outlet_init):

                    logger.error('Failed to connect to the sheath and outlet flow meters.')

                    msg = ('Could not connect to the coflow sheath and outlet flow meters. '
                        'Contact your beamline scientist.')

                    wx.CallAfter(self.showMessageDialog, self, msg, 'Connection error',
                        wx.OK|wx.ICON_ERROR)

                elif not self.coflow_control.fm_sheath_init:

                    logger.error('Failed to connect to the sheath flow meter.')

                    msg = ('Could not connect to the coflow sheath flow meter. '
                        'Contact your beamline scientist.')

                    wx.CallAfter(self.showMessageDialog, self, msg, 'Connection error',
                        wx.OK|wx.ICON_ERROR)

                else:

                    logger.error('Failed to connect to the outlet flow meter.')

                    msg = ('Could not connect to the coflow outlet flow meter. '
                        'Contact your beamline scientist.')

                    wx.CallAfter(self.showMessageDialog, self, msg, 'Connection error',
                        wx.OK|wx.ICON_ERROR)

            if (not self.coflow_control.timeout_event.is_set()
                and not self.valve_sheath_init):
                logger.error('Failed to connect to the sheath valve.')

                msg = ('Could not connect to the sheath valve. Contact your '
                    'beamline scientist.')

                wx.CallAfter(self.showMessageDialog, self, msg, 'Connection error',
                    wx.OK|wx.ICON_ERROR)


            if self.settings['use_overflow_control']:
                self.overflow_monitor_timer = wx.Timer(self)
                self.Bind(wx.EVT_TIMER, self._on_overflow_monitor_timer,
                    self.overflow_monitor_timer)
                self.overflow_monitor_timer.Start(10000)

        else:
            self.connected = False

            msg = ('No connection to coflow, running in GUI test mode! '
                    'Contact your beamline scientist.')

            wx.CallAfter(self.showMessageDialog, self, msg, "Warning: Test Mode",
                wx.OK|wx.ICON_ERROR)


    def _FromDIP(self, size):
        # This is a hack to provide easy back compatibility with wxpython < 4.1
        try:
            return self.FromDIP(size)
        except Exception:
            return size

    def _create_layout(self):
        """Creates the layout for the panel."""
        units = self.settings['flow_units']

        control_box = wx.StaticBox(self, label='Coflow Controls')
        coflow_ctrl_sizer = wx.StaticBoxSizer(control_box, wx.VERTICAL)

        self.flow_rate = wx.TextCtrl(control_box, size=self._FromDIP((60,-1)),
            value=self.settings['lc_flow_rate'], validator=utils.CharValidator('float'))
        fr_label = 'LC flow rate [{}]:'.format(units)
        self.change_flow_button = wx.Button(control_box, label='Change Flow Rate')

        flow_rate_sizer = wx.BoxSizer(wx.HORIZONTAL)
        flow_rate_sizer.Add(wx.StaticText(control_box, label=fr_label), border=self._FromDIP(2),
            flag=wx.ALIGN_CENTER_VERTICAL|wx.RIGHT)
        flow_rate_sizer.Add(self.flow_rate, flag=wx.ALIGN_CENTER_VERTICAL|wx.RIGHT,
            border=self._FromDIP(2))
        flow_rate_sizer.Add(self.change_flow_button, flag=wx.ALIGN_CENTER_VERTICAL)

        self.start_flow_button = wx.Button(control_box, label='Start Coflow')
        self.stop_flow_button = wx.Button(control_box, label='Stop Coflow')
        self.change_buffer_button = wx.Button(control_box, label='Change Buffer')

        self.auto_flow = wx.CheckBox(control_box, label='Start/stop coflow automatically with exposure')
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

        if 'exposure' not in self.settings['components']:
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

        adv_pane = wx.CollapsiblePane(control_box, label="Advanced",
            style=wx.CP_NO_TLW_RESIZE)
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


        valve_box = wx.StaticBox(adv_win, label='Valves')
        valve_box_sizer = wx.StaticBoxSizer(valve_box, wx.HORIZONTAL)

        self.sheath_valve_pos = utils.IntSpinCtrl(valve_box, min=1,
            max=self.settings['sheath_valve'][3]['positions'])
        self.sheath_valve_pos.Bind(utils.EVT_MY_SPIN, self._on_sheath_valve_position_change)

        valve_sizer = wx.FlexGridSizer(cols=2, hgap=self._FromDIP(5),
            vgap=self._FromDIP(5))
        valve_sizer.Add(wx.StaticText(valve_box, label='Sheath Valve:'),
            flag=wx.ALIGN_CENTER_VERTICAL)
        valve_sizer.Add(self.sheath_valve_pos, flag=wx.ALIGN_CENTER_VERTICAL)

        valve_box_sizer.Add(valve_sizer, flag=wx.ALL, border=self._FromDIP(2))
        valve_box_sizer.AddStretchSpacer(1)

        adv_sizer.Add(valve_box_sizer, flag=wx.ALL|wx.EXPAND, border=self._FromDIP(2))


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

        adv_win.SetSizer(adv_sizer)

        coflow_ctrl_sizer.Add(flow_rate_sizer, border=self._FromDIP(2), flag=wx.TOP|wx.LEFT|wx.RIGHT)
        coflow_ctrl_sizer.Add(self.auto_flow, border=self._FromDIP(2), flag=wx.TOP|wx.LEFT|wx.RIGHT)
        coflow_ctrl_sizer.Add(button_sizer, border=self._FromDIP(2),
            flag=wx.TOP|wx.LEFT|wx.RIGHT|wx.EXPAND)
        coflow_ctrl_sizer.Add(adv_pane, flag=wx.ALL|wx.EXPAND, border=self._FromDIP(2))


        status_panel = wx.Panel(self)

        self.sheath_flow = wx.StaticText(status_panel, label='0', style=wx.ST_NO_AUTORESIZE,
            size=self._FromDIP((50,-1)))
        self.outlet_flow = wx.StaticText(status_panel, label='0', style=wx.ST_NO_AUTORESIZE,
            size=self._FromDIP((50,-1)))

        self.status = wx.StaticText(status_panel, label='Coflow off', style=wx.ST_NO_AUTORESIZE,
            size=self._FromDIP((75, -1)))
        self.status.SetForegroundColour(wx.RED)
        fsize = self.GetFont().GetPointSize()
        font = wx.Font(fsize, wx.DEFAULT, wx.NORMAL, wx.BOLD)
        self.status.SetFont(font)

        status_label = wx.StaticText(status_panel, label='Status:')
        sheath_label = wx.StaticText(status_panel, label='Sheath flow [{}]:'.format(units))
        outlet_label = wx.StaticText(status_panel, label='Outlet flow [{}]:'.format(units))

        status_grid_sizer = wx.FlexGridSizer(cols=2, rows=3, vgap=self._FromDIP(5), hgap=self._FromDIP(2))
        status_grid_sizer.Add(status_label, flag=wx.ALIGN_CENTER_VERTICAL)
        status_grid_sizer.Add(self.status, flag=wx.ALIGN_CENTER_VERTICAL)
        status_grid_sizer.Add(sheath_label, flag=wx.ALIGN_CENTER_VERTICAL)
        status_grid_sizer.Add(self.sheath_flow, flag=wx.ALIGN_CENTER_VERTICAL)
        status_grid_sizer.Add(outlet_label, flag=wx.ALIGN_CENTER_VERTICAL)
        status_grid_sizer.Add(self.outlet_flow, flag=wx.ALIGN_CENTER_VERTICAL)

        coflow_status_sizer = wx.StaticBoxSizer(wx.StaticBox(status_panel,
            label='Coflow Status'), wx.HORIZONTAL)
        coflow_status_sizer.Add(status_grid_sizer, border=self._FromDIP(5), flag=wx.ALL)

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

    def on_collapse(self, event):
        self.Layout()
        self.Refresh()
        self.SendSizeEvent()

    def showMessageDialog(self, parent, msg, title, style):
        dialog = wx.MessageDialog(parent, msg, title, style=style)
        dialog.ShowModal()
        dialog.Destroy()

    def _on_startbutton(self, evt):
        valid, flow_rate = self._validate_flow_rate()

        if valid:
            self.start_flow_button.Disable()
            self.stop_flow_button.Enable()
            self.change_flow_button.Enable()

            self.start_flow(False)

    def _on_stopbutton(self, evt):
        self.start_flow_button.Enable()
        self.stop_flow_button.Disable()
        self.change_flow_button.Disable()

        self.stop_flow()

    def _on_changebutton(self, evt):
        self.change_flow(start_monitor=True)

    def _on_change_buffer(self, evt):
        self.stop_flow_timer()
        self.verbose_buffer_change = True

        self.change_buffer()

    def change_buffer(self, target_valve_pos=1, change_valve_pos=False, interactive=True):
        #Stop flow
        self.stop_flow()

        if change_valve_pos:
            self.set_sheath_valve_position(target_valve_pos)

        valve_pos = self.get_sheath_valve_position()

        if interactive:
            if valve_pos != target_valve_pos:

                msg = ('The sheath buffer valve position is set to {}. For buffer '
                    'it is usually 1. Please verify that the valve position '
                    'is correct before proceeding and change if necessary. '
                    'Click okay to continue.')

                self.showMessageDialog(self, msg, "Check sheath buffer valve",
                        wx.OK|wx.ICON_INFORMATION)

            #Change buffer bottle
            msg = ('Change the buffer bottle in the coflow setup. Click okay to continue. '
                'Buffer will flow for ~25 mL (~10 minutes) to flush the system.')

            self.showMessageDialog(self, msg, "Change buffer",
                    wx.OK|wx.ICON_INFORMATION)

        #Change flow rate
        self._change_flow_rate(self.settings['buffer_change_fr'])

        #Start flow
        self._start_flow()
        wx.CallAfter(self.status.SetLabel, 'Changing buffer')
        #Start flow timer
        fr = self.coflow_control.sheath_setpoint
        time = 60*(self.settings['buffer_change_vol']/fr)
        self._start_flow_timer(time)
        self.doing_buffer_change = True

    def _next_buffer_change(self):
        if len(self.buffer_change_sequence) > 0:
            next_buffer= self.buffer_change_sequence.pop(0)

            self.change_buffer(next_buffer, True, False)

    def _on_put_in_water(self, evt):
        self.stop_flow_timer()
        self.verbose_buffer_change = False

        self.change_buffer(self.settings['sheath_valve_water_pos'], True, False)

    def _on_put_in_ethanol(self, evt):
        self.stop_flow_timer()
        self.verbose_buffer_change = False

        self.buffer_change_sequence = [self.settings['sheath_valve_water_pos'],
            self.settings['sheath_valve_ethanol_pos'],
            ]

        self._next_buffer_change()

    def _on_put_in_hellmanex(self, evt):
        self.stop_flow_timer()
        self.verbose_buffer_change = False

        self.buffer_change_sequence = [self.settings['sheath_valve_water_pos'],
            self.settings['sheath_valve_hellmanex_pos'],
            ]

        self._next_buffer_change()

    def _on_clean(self, evt):
        self.stop_flow_timer()
        self.verbose_buffer_change = False

        self.buffer_change_sequence = [self.settings['sheath_valve_water_pos'],
            self.settings['sheath_valve_hellmanex_pos'],
            self.settings['sheath_valve_water_pos'],
            self.settings['sheath_valve_ethanol_pos'],
            ]

        self._next_buffer_change()

    def _on_start_overflow(self, evt):
        wx.CallAfter(self._start_overflow)

    def _on_stop_overflow(self, evt):
        wx.CallAfter(self._stop_overflow)

    def _start_overflow(self):
        self.coflow_control.start_overflow()

    def _stop_overflow(self):
        self.coflow_control.stop_overflow()

    def _on_overflow_monitor_timer(self, evt):
        self._check_overflow_status()

    def _check_overflow_status(self):
        status, err = self.coflow_control.check_overflow_status()

        if err:
            msg = ('Could not get overflow pump status. Contact your beamline scientist.')

            wx.CallAfter(self.showMessageDialog, self, msg, "Connection error",
                wx.OK|wx.ICON_ERROR)

        if status != '':
            wx.CallAfter(self.overflow_status.SetLabel, status)

    def _on_start_flow_timer(self, evt):
        self.buffer_change_sequence = []
        self.start_flow_timer()

    def start_flow_timer(self):

        flow_time = self.flow_timer_run_time_ctrl.GetValue()

        try:
            flow_time= float(flow_time)*60

        except Exception:
            msg = ('The flow time must be a float.')
            title = 'Flow time not set'
            style=wx.OK|wx.ICON_WARNING

            wx.CallAfter(self._show_message_dialog, msg, title, style)

            flow_time = None

        if flow_time is not None:
            self._start_flow_timer(flow_time)

    def _start_flow_timer(self, flow_time):
        self.flow_timer_run_time = flow_time
        self.flow_timer_start_time = time.time()

        self.set_flow_timer_time_remaining(self.flow_timer_run_time)

        self.flow_timer.Start(5000)

        wx.CallAfter(self.stop_flow_timer_btn.Enable)
        wx.CallAfter(self.start_flow_timer_btn.Disable)

    def _on_stop_flow_timer(self, evt):
        self.buffer_change_sequence = []
        self.stop_flow_timer()

    def stop_flow_timer(self):
        self.flow_timer.Stop()

        wx.CallAFter(self.stop_flow_timer_btn.Disable)
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

    def _on_flow_timer(self, evt):

        if self.coflow_control.coflow_on:
            tr = time.time() - self.flow_timer_start_time
            if tr >= self.flow_timer_run_time:

                change_buf = copy.copy(self.doing_buffer_change)

                self.stop_flow()
                self.stop_flow_timer()

                if change_buf and len(self.buffer_change_sequence) == 0:

                    if self.verbose_buffer_change:
                        msg = ('Buffer change complete. Do you want to restart '
                            'flow at the previous rate?')

                        dialog = wx.MessageDialog(self, msg, 'Buffer change finished',
                            style=wx.YES_NO|wx.YES_DEFAULT|wx.ICON_QUESTION)

                        ret = dialog.ShowModal()
                        dialog.Destroy()

                        if ret == wx.ID_YES:
                            self.start_flow()

                elif change_buf and len(self.buffer_change_sequence) > 0:
                    self._next_buffer_change()

            else:
                self.set_flow_timer_time_remaining(tr)

        else:
            self.stop_flow_timer()

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
                self.start_flow_button.Disable()
                self.stop_flow_button.Enable()
                self.change_flow_button.Enable()

                self.start_flow(validate=False)
        else:
            valid = True

        return valid

    def auto_stop(self):
        auto = self.auto_flow.GetValue()

        if auto:
            self.start_flow_button.Enable()
            self.stop_flow_button.Disable()
            self.change_flow_button.Disable()

            self.stop_flow()

    def start_flow(self, validate=True):
        logger.debug('Starting flow')

        if not self.coflow_control.coflow_on:
            valid = self.change_flow(validate)

            if valid:
                self._start_flow()

    def _start_flow(self):
        self.coflow_control.start_flow()

        self.status.SetLabel('Coflow on')

        self.monitor_timer.Start(self.settings['settling_time'])

    def stop_flow(self):
        logger.debug('Stopping flow')

        stop_coflow = True

        if 'exposure' in self.settings['components']:
            exposure_panel = wx.FindWindowByName('exposure')
            exposure_running = exposure_panel.exp_event.is_set()
        else:
            exposure_running = False

        if exposure_running:
            msg = ('The exposure is still running. Are you sure you want '
                'to stop the coflow?')

            dialog = wx.MessageDialog(self, msg, 'Verify coflow stop',
                style=wx.YES_NO|wx.NO_DEFAULT|wx.ICON_QUESTION)

            ret = dialog.ShowModal()
            dialog.Destroy()

            if ret == wx.ID_NO:
                stop_coflow = False

        if stop_coflow and self.coflow_control.coflow_on:
            self.monitor_timer.Stop()
            self.monitor = False

            self.coflow_control.stop_flow()

            self.status.SetLabel('Coflow off')

            logger.info('Stopped coflow pumps')

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
            self.monitor = False

        self.coflow_control.change_flow_rate(flow_rate)

        if start_monitor:
            self.monitor_timer.Start(self.settings['settling_time'])

    def _validate_flow_rate(self):
        logger.debug('Validating flow rate')
        lc_flow_rate = self.flow_rate.GetValue()

        lc_flow_rate, is_number, is_extreme = self.coflow_control.validate_flow_rate(lc_flow_rate)

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

    def _on_sheath_valve_position_change(self, evt):
        pos = self.sheath_valve_pos.GetValue()
        self.set_sheath_valve_position(pos)

    def get_sheath_valve_position(self):
        return self.coflow_control.get_sheath_valve_position()

    def set_sheath_valve_position(self, position):

        change_pos = True

        if 'exposure' in self.settings['components']:
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
            self.coflow_control.set_sheath_valve_position(position)

    def check_sheath_valve_pos(self):
        pos = self.get_sheath_valve_position()

        if self.sheath_valve_pos.GetValue() != int(pos):
            wx.CallAfter(self.sheath_valve_pos.SetValue, int(pos))

    def _get_flow_rates(self):
        logger.info('Starting continuous logging of flow rates')

        low_warning = self.settings['warning_threshold_low']
        high_warning = self.settings['warning_threshold_high']

        cycle_time = time.time()
        long_cycle_time = copy.copy(cycle_time)
        if self.start_time is None:
            self.start_time = copy.copy(cycle_time)
        log_time = time.time()


        while not self.stop_get_fr_event.is_set():

            if self.coflow_control.timeout_event.is_set():
                self.stop_get_fr_event.set()

                msg = ('Lost connection to the coflow control server. '
                    'Contact your beamline scientist.')

                wx.CallAfter(self.showMessageDialog, self, msg, "Connection error",
                    wx.OK|wx.ICON_ERROR)

                wx.CallAfter(self.connection_timer.Start, 1000)

            if not self.stop_get_fr_event.is_set():
                sheath_fr, s_type = self.coflow_control.get_sheath_flow_rate()

            if not self.stop_get_fr_event.is_set():
                outlet_fr, o_type = self.coflow_control.get_outlet_flow_rate()

            if s_type == 'flow_rate' and o_type == 'flow_rate':

                with self.get_plot_data_lock:
                    self.sheath_fr_list.append(sheath_fr)
                    self.outlet_fr_list.append(outlet_fr)

                    self.fr_time_list.append(time.time()-self.start_time)

                if self.monitor:
                    if ((sheath_fr < low_warning*self.coflow_control.sheath_setpoint or
                        sheath_fr > high_warning*self.coflow_control.sheath_setpoint)
                        and self.settings['show_sheath_warning']):
                        wx.CallAfter(self._show_warning_dialog, 'sheath', sheath_fr)
                        logger.error('Sheath flow out of bounds (%f to %f): %f',
                            low_warning*self.coflow_control.sheath_setpoint,
                            high_warning*self.coflow_control.sheath_setpoint,
                            sheath_fr)

                    if ((outlet_fr < low_warning*self.coflow_control.outlet_setpoint or
                        outlet_fr > high_warning*self.coflow_control.outlet_setpoint)
                        and self.settings['show_outlet_warning']):
                        wx.CallAfter(self._show_warning_dialog, 'outlet', outlet_fr)
                        logger.error('Outlet flow out of bounds (%f to %f): %f',
                            low_warning*self.coflow_control.outlet_setpoint,
                            high_warning*self.coflow_control.outlet_setpoint,
                            outlet_fr)

            if time.time() - cycle_time > 0.25:
                if not self.stop_get_fr_event.is_set():
                    sheath_density, s1_type = self.coflow_control.get_sheath_density()

                if not self.stop_get_fr_event.is_set():
                    outlet_density, o1_type = self.coflow_control.get_outlet_density()

                if not self.stop_get_fr_event.is_set():
                    sheath_t, s2_type = self.coflow_control.get_sheath_temperature()

                if not self.stop_get_fr_event.is_set():
                    outlet_t, o2_type = self.coflow_control.get_outlet_temperature()

                if s1_type == o1_type and s1_type == 'density' and s2_type == o2_type and s2_type == 'temperature':
                    with self.get_plot_data_lock:
                        self.sheath_density_list.append(sheath_density)
                        self.outlet_density_list.append(outlet_density)

                        self.sheath_t_list.append(sheath_t)
                        self.outlet_t_list.append(outlet_t)

                        cycle_time = time.time()

                        self.aux_time_list.append(cycle_time-self.start_time)

                    if (sheath_density < self.settings['air_density_thresh']
                        and outlet_density < self.settings['air_density_thresh']):
                        wx.CallAfter(self.air_detected, 'both')

                    elif sheath_density < self.settings['air_density_thresh']:
                        wx.CallAfter(self.air_detected, 'sheath')

                    elif outlet_density < self.settings['air_density_thresh']:
                        wx.CallAfter(self.air_detected, 'outlet')


                if s_type == 'flow_rate' and o_type == 'flow_rate':
                    wx.CallAfter(self.sheath_flow.SetLabel, str(round(sheath_fr, 3)))
                    wx.CallAfter(self.outlet_flow.SetLabel, str(round(outlet_fr,3 )))

                # if not self.stop_get_fr_event.is_set():
                    # logger.debug('Sheath flow rate: %f', sheath_fr)
                    # logger.debug('Outlet flow rate: %f', outlet_fr)
                    # logger.debug('Sheath density: %f', sheath_density)
                    # logger.debug('Outlet density: %f', outlet_density)
                    # logger.debug('Sheath temperature: %f', sheath_t)
                    # logger.debug('Outlet temperature: %f', outlet_t)

                if (not self.stop_get_fr_event.is_set() and time.time() - log_time > 300
                    and self.coflow_control.coflow_on):
                    logger.info('Sheath flow rate: %f', sheath_fr)
                    logger.info('Outlet flow rate: %f', outlet_fr)
                    logger.info('Sheath density: %f', sheath_density)
                    logger.info('Outlet density: %f', outlet_density)
                    logger.info('Sheath temperature: %f', sheath_t)
                    logger.info('Outlet temperature: %f', outlet_t)

                    log_time = time.time()

            if time.time() - long_cycle_time > 5:
                wx.CallAfter(self.check_sheath_valve_pos)

                long_cycle_time = time.time()

        logger.info('Stopping continuous logging of flow rates')

    def _on_monitor_timer(self, evt):
        self.monitor_timer.Stop()

        logger.info('Flow monitoring started')

        low_warning = self.settings['warning_threshold_low']
        high_warning = self.settings['warning_threshold_high']

        logger.info('Sheath flow bounds: %f to %f %s',
            low_warning*self.coflow_control.sheath_setpoint,
            high_warning*self.coflow_control.sheath_setpoint,
            self.settings['flow_units'])
        logger.info('Outlet flow bounds: %f to %f %s',
            low_warning*self.coflow_control.outlet_setpoint,
            high_warning*self.coflow_control.outlet_setpoint,
            self.settings['flow_units'])

        self.monitor = True

    def _on_connection_timer(self, evt):
        if not self.coflow_control.timeout_event.is_set():
            self.connection_timer.Stop()

            self.coflow_control.init_pumps()
            self.coflow_control.init_fms()
            self.coflow_control.init_valves()

            if self.sheath_is_moving or self.outlet_is_moving:
                self.stop_flow_button.Enable()
                self.change_flow_button.Enable()

            if self.sheath_is_moving and self.outlet_is_moving:
                self.start_flow_button.Disable()
                self.status.SetLabel('Coflow on')
            else:
                self.start_flow_button.Enable()

            self.get_fr_thread = threading.Thread(target=self._get_flow_rates)
            self.get_fr_thread.daemon = True
            self.get_fr_thread.start()

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

            self.warning_dialog = utils.WarningMessage(self, msg, 'Coflow flow is unstable')
            self.warning_dialog.Show()

    def _show_error_dialog(self, msg, title):
        if self.error_dialog is None:
            self.error_dialog = utils.WarningMessage(self, msg, title)
            self.error_dialog.Show()

    def _show_air_warning_dialog(self, loc):
        if self.air_warning_dialog is None:
            if loc == 'both':
                msg = ('Air detected in both sheath and outlet flows.')
            else:
                msg = ('Air detected in the {} flow.')

            self.air_warning_dialog = utils.WarningMessage(self, msg, 'Air detected')
            self.air_warning_dialog.Show()

    def metadata(self):

        metadata = OrderedDict()

        if self.coflow_control.coflow_on:
            metadata['Coflow on:'] = True
            metadata['LC flow rate [{}]:'.format(self.settings['flow_units'])] = self.lc_flow_rate
            metadata['Outlet flow rate [{}]:'.format(self.settings['flow_units'])] = self.outlet_setpoint
            metadata['Sheath ratio:'] = self.settings['sheath_ratio']
            metadata['Sheath excess ratio:'] = self.settings['sheath_excess']
            metadata['Sheath inlet flow rate (including excess) [{}]:'.format(self.settings['flow_units'])] = self.sheath_setpoint
            metadata['Sheath valve position:'] = self.get_sheath_valve_position()

        else:
            metadata['Coflow on:'] = False

        return metadata

    def on_exit(self):
        if self.connected:
            logger.debug('Closing all coflow devices')

            self.overflow_monitor_timer.Stop()

            self.stop_get_fr_event.set()

            if not self.coflow_control.timeout_event.is_set():
                self.get_fr_thread.join()
                self.stop_flow()

            try:
                plot_window = wx.FindWindowByName('CoflowPlot')
                plot_window._on_exit(None)
            except Exception:
                pass

            time.sleep(0.5)

            self.coflow_control.disconnect_coflow()


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

        if (self.plot_type == 'Both Flow' or self.plot_type == 'Sheath Flow'
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

        oldx = self.subplot.get_xlim()
        oldy = self.subplot.get_ylim()

        self.subplot.relim()
        self.subplot.autoscale_view()

        newx = self.subplot.get_xlim()
        newy = self.subplot.get_ylim()

        if newy != oldy:
            redraw = True

        if newx != oldx:
            if newx[0] > oldx[0] and newx[0] < oldx[0] + self.t_axis_incrementer:
                new_x[0] = oldx[0]

            if newx[1] > oldx[1]:
                newx[1] = newx[1] + self.t_axis_incrementer

        if newx != oldx:
            self.subplot.set_xlim(newx[0], newx[1])
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



class CoflowFrame(wx.Frame):
    """
    A lightweight frame allowing one to work with arbitrary number of pumps.
    Only meant to be used when the pumpcon module is run directly,
    rather than when it is imported into another program.
    """
    def __init__(self, settings, connect=True, *args, **kwargs):
        """
        Initializes the pump frame. Takes args and kwargs for the wx.Frame class.
        """
        super(CoflowFrame, self).__init__(*args, **kwargs)
        logger.debug('Setting up the CoflowFrame')

        self.Bind(wx.EVT_CLOSE, self._on_exit)

        self._create_layout(settings, connect)

        self.Layout()
        self.SendSizeEvent()
        self.Fit()
        self.Raise()

    def _create_layout(self, settings, connect):
        """Creates the layout"""
        self.coflow_panel = CoflowPanel(settings, self, connect=connect)

        self.coflow_sizer = wx.BoxSizer(wx.HORIZONTAL)
        self.coflow_sizer.Add(self.coflow_panel, proportion=1, flag=wx.EXPAND)

        top_sizer = wx.BoxSizer(wx.VERTICAL)
        top_sizer.Add(self.coflow_sizer, proportion=1, flag=wx.EXPAND|wx.ALL, border=5)

        self.SetSizer(top_sizer)

    def _on_exit(self, evt):
        """Stops all current pump motions and then closes the frame."""
        logger.debug('Closing the CoflowFrame')

        self.coflow_panel.on_exit()

        self.Destroy()

if __name__ == '__main__':
    logger = logging.getLogger()
    logger.setLevel(logging.DEBUG)
    h1 = logging.StreamHandler(sys.stdout)
    # h1.setLevel(logging.INFO)
    # h1.setLevel(logging.DEBUG)
    h1.setLevel(logging.ERROR)

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
    settings = {
        'components'                : ['coflow'],
        'show_advanced_options'     : False,
        'device_communication'      : 'remote',
        'remote_pump_ip'            : '164.54.204.53',
        'remote_pump_port'          : '5556',
        'remote_fm_ip'              : '164.54.204.53',
        'remote_fm_port'            : '5557',
        'remote_overflow_ip'        : '164.54.204.75',
        'flow_units'                : 'mL/min',
        'sheath_pump'               : ('VICI_M50', 'COM3', [629.48, 13.442], {}),
        'outlet_pump'               : ('VICI_M50', 'COM4', [629.16, 12.354], {}),
        'sheath_fm'                 : ('BFS', 'COM5', [], {}),
        'outlet_fm'                 : ('BFS', 'COM6', [], {}),
        'sheath_valve'              : ('Cheminert', 'COM6', [], {'positions' : 10}),
        'sheath_ratio'              : 0.3,
        'sheath_excess'             : 1.5,
        'warning_threshold_low'     : 0.8,
        'warning_threshold_high'    : 1.2,
        'settling_time'             : 5000, #in ms
        'lc_flow_rate'              : '0.6',
        'show_sheath_warning'       : True,
        'show_outlet_warning'       : True,
        'use_overflow_control'      : True,
        'buffer_change_fr'          : 2., #in ml/min
        'buffer_change_vol'         : 25., #in ml
        'air_density_thresh'        : 700, #g/L
        'sheath_valve_water_pos'    : 10,
        'sheath_valve_hellmanex_pos': 8,
        'sheath_valve_ethanol_pos'  : 9,
        }

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
    frame = CoflowFrame(settings, None, connect=False, title='Coflow Control')
    frame.Show()
    app.MainLoop()



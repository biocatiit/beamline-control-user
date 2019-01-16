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
from collections import OrderedDict, deque
import logging
import sys
import ctypes
import copy

if __name__ != '__main__':
    logger = logging.getLogger('biocon.coflow')

import wx

# import fmcon
import client
import pumpcon
import utils

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
    def __init__(self, parent, settings, mx_data, *args, **kwargs):
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

        super(CoflowPanel, self).__init__(parent, *args, **kwargs)
        logger.debug('Initializing CoflowPanel')

        self.settings = settings
        self.mx_data = mx_data

        self._create_layout()

        self.coflow_pump_cmd_q = deque()
        self.coflow_pump_return_q = deque()
        self.coflow_pump_abort_event = threading.Event()
        self.coflow_pump_event = threading.Event()

        self.coflow_fm_cmd_q = deque()
        self.coflow_fm_return_q = deque()
        self.coflow_fm_abort_event = threading.Event()
        self.coflow_fm_event = threading.Event()

        if self.settings['device_communication'] == 'local':
            self.coflow_pump_con = pumpcon.PumpCommThread(self.coflow_pump_cmd_q,
                self.coflow_pump_return_q, self.coflow_pump_abort_event, 'PumpCon')

            self.coflow_fm_con = fmcon.fmCommThread(self.coflow_fm_cmd_q,
                self.coflow_fm_return_q, self.coflow_fm_abort_event, 'FMCon')

        else:
            pump_ip = self.settings['remote_pump_ip']
            pump_port = self.settings['remote_pump_port']
            self.coflow_pump_con = client.ControlClient(pump_ip, pump_port,
                self.coflow_pump_cmd_q, self.coflow_pump_return_q,
                self.coflow_pump_abort_event, name='PumpControlClient')

            fm_ip = self.settings['remote_fm_ip']
            fm_port = self.settings['remote_fm_port']
            self.coflow_fm_con = client.ControlClient(fm_ip, fm_port,
                self.coflow_fm_cmd_q, self.coflow_fm_return_q,
                self.coflow_fm_abort_event, name='PumpControlClient')

        self.coflow_pump_con.start()
        self.coflow_fm_con.start()

        self._init_pumps()
        self._init_fms()

        self.monitor = False
        self.sheath_setpoint = None
        self.outlet_setpoint = None
        self.warning_dialog = None
        self.monitor_timer = wx.Timer(self)
        self.Bind(wx.EVT_TIMER, self._on_monitor_timer, self.monitor_timer)

        self.get_fr_thread = threading.Thread(target=self._get_flow_rates)
        self.get_fr_thread.daemon = True
        self.get_fr_thread.start()

        # # Testing only
        # self.coflow_pump_con = None
        # self.coflow_fm_con = None


    def _create_layout(self):
        """Creates the layout for the panel."""
        units = self.settings['flow_units']

        self.flow_rate = wx.TextCtrl(self, size=(60,-1), validator=utils.CharValidator('float'))
        fr_label = 'LC flow rate [{}]:'.format(units)

        flow_rate_sizer = wx.BoxSizer(wx.HORIZONTAL)
        flow_rate_sizer.Add(wx.StaticText(self, label=fr_label), border=2,
            flag=wx.ALIGN_CENTER_VERTICAL|wx.RIGHT)
        flow_rate_sizer.Add(self.flow_rate, flag=wx.ALIGN_CENTER_VERTICAL)


        self.start_flow_button = wx.Button(self, label='Start Coflow')
        self.stop_flow_button = wx.Button(self, label='Stop Coflow')
        self.change_flow_button = wx.Button(self, label='Change Flow Rate')
        self.auto_flow = wx.CheckBox(self, label='Start/stop coflow automatically with exposure')

        self.start_flow_button.Bind(wx.EVT_BUTTON, self._on_startbutton)
        self.stop_flow_button.Bind(wx.EVT_BUTTON, self._on_stopbutton)
        self.change_flow_button.Bind(wx.EVT_BUTTON, self._on_changebutton)

        self.stop_flow_button.Disable()
        self.change_flow_button.Disable()

        if 'exposure' not in self.settings['components']:
            self.auto_flow.SetValue(False)
            self.auto_flow.Disable()
            self.auto_flow.Hide()

        button_sizer = wx.BoxSizer(wx.HORIZONTAL)
        button_sizer.AddStretchSpacer(1)
        button_sizer.Add(self.start_flow_button, border=5,
            flag=wx.ALIGN_CENTER_VERTICAL|wx.RIGHT)
        button_sizer.Add(self.stop_flow_button, border=5,
            flag=wx.ALIGN_CENTER_VERTICAL|wx.LEFT|wx.RIGHT)
        button_sizer.Add(self.change_flow_button, border=5,
            flag=wx.ALIGN_CENTER_VERTICAL|wx.LEFT)
        button_sizer.AddStretchSpacer(1)

        coflow_ctrl_sizer = wx.StaticBoxSizer(wx.StaticBox(self,
            label='Coflow Controls'), wx.VERTICAL)
        coflow_ctrl_sizer.Add(flow_rate_sizer, border=5, flag=wx.TOP|wx.LEFT|wx.RIGHT)
        coflow_ctrl_sizer.Add(self.auto_flow, border=5, flag=wx.TOP|wx.LEFT|wx.RIGHT)
        coflow_ctrl_sizer.Add(button_sizer, border=5,
            flag=wx.ALL|wx.ALIGN_CENTER_HORIZONTAL|wx.EXPAND)


        self.sheath_flow = wx.StaticText(self, label='0', style=wx.ST_NO_AUTORESIZE,
            size=(50,-1))
        self.outlet_flow = wx.StaticText(self, label='0', style=wx.ST_NO_AUTORESIZE,
            size=(50,-1))

        status_grid_sizer = wx.FlexGridSizer(cols=2, rows=2, vgap=5, hgap=2)
        status_grid_sizer.Add(wx.StaticText(self, label='Sheath flow [{}]:'.format(units)),
            flag=wx.ALIGN_CENTER_VERTICAL)
        status_grid_sizer.Add(self.sheath_flow, flag=wx.ALIGN_CENTER_VERTICAL)
        status_grid_sizer.Add(wx.StaticText(self, label='Outlet flow [{}]:'.format(units)),
            flag=wx.ALIGN_CENTER_VERTICAL)
        status_grid_sizer.Add(self.outlet_flow, flag=wx.ALIGN_CENTER_VERTICAL)

        coflow_status_sizer = wx.StaticBoxSizer(wx.StaticBox(self,
            label='Coflow Status'), wx.HORIZONTAL)
        coflow_status_sizer.Add(status_grid_sizer, border=5, flag=wx.ALL)

        coflow_status_sizer.AddStretchSpacer(1)


        top_sizer = wx.BoxSizer(wx.VERTICAL)
        top_sizer.Add(coflow_ctrl_sizer, flag=wx.EXPAND)
        top_sizer.Add(coflow_status_sizer, border=10, flag=wx.EXPAND|wx.TOP)

        self.SetSizer(top_sizer)

    def _init_pumps(self):
        sheath_pump = self.settings['sheath_pump']
        outlet_pump = self.settings['outlet_pump']

        logger.info('Initializing coflow pumps on startup')

        sheath_args = (sheath_pump[1], 'sheath_pump', sheath_pump[0])
        if sheath_pump[0] == 'VICI_M50':
            sheath_kwargs = {'flow_cal': sheath_pump[2][0],
                'backlash_cal': sheath_pump[2][1]}
        else:
            sheath_kwargs = {}

        sheath_init_cmd = ('connect', sheath_args, sheath_kwargs)

        outlet_args = (outlet_pump[1], 'outlet_pump', outlet_pump[0])
        if outlet_pump[0] == 'VICI_M50':
            outlet_kwargs = {'flow_cal': outlet_pump[2][0],
                'backlash_cal': outlet_pump[2][1]}
        else:
            outlet_kwargs = {}

        outlet_init_cmd = ('connect', outlet_args, outlet_kwargs)

        self._send_pumpcmd(sheath_init_cmd)
        self._send_pumpcmd(outlet_init_cmd)

        self._send_pumpcmd(('set_units', ('sheath_pump', self.settings['flow_units']), {}))
        self._send_pumpcmd(('set_units', ('outlet_pump', self.settings['flow_units']), {}))

        sheath_is_moving = self._send_pumpcmd(('is_moving', ('sheath_pump',), {}))
        outlet_is_moving = self._send_pumpcmd(('is_moving', ('outlet_pump',), {}))

        if sheath_is_moving or outlet_is_moving:
            self.stop_flow.Enable()
            self.change_flow_rate.Enable()
        if sheath_is_moving and outlet_is_moving:
            self.start_flow.Disable()

    def _init_fms(self):
        """
        Initializes the flow meters
        """

        sheath_fm = self.settings['sheath_fm']
        outlet_fm = self.settings['outlet_fm']

        logger.info('Initializing coflow pumps on startup')

        sheath_args = (sheath_fm[1], 'sheath_fm', sheath_fm[0])

        sheath_init_cmd = ('connect', sheath_args, {})

        outlet_args = (outlet_fm[1], 'outlet_fm', outlet_fm[0])

        outlet_init_cmd = ('connect', outlet_args, {})

        self._send_fmcmd(sheath_init_cmd)
        self._send_fmcmd(outlet_init_cmd)

        self._send_fmcmd(('set_units', ('sheath_fm', self.settings['flow_units']), {}))
        self._send_fmcmd(('set_units', ('outlet_fm', self.settings['flow_units']), {}))

        self._send_fmcmd(('get_density', ('sheath_fm',), {}), True)
        self._send_fmcmd(('get_density', ('outlet_fm',), {}), True)

        self._send_fmcmd(('get_temperature', ('sheath_fm',), {}), True)
        self._send_fmcmd(('get_temperature', ('outlet_fm',), {}), True)

        self._send_fmcmd(('get_flow_rate', ('sheath_fm',), {}), True)
        self._send_fmcmd(('get_flow_rate', ('outlet_fm',), {}), True)

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

    def start_flow(self, validate=True):
        self.change_flow(validate)

        sheath_start_cmd = ('start_flow', ('sheath_pump', ), {})
        outlet_start_cmd = ('start_flow', ('outlet_pump', ), {})

        self._send_pumpcmd(sheath_start_cmd)
        self._send_pumpcmd(outlet_start_cmd)

        self.monitor_timer.Start(self.settings['settling_time'])

    def stop_flow(self):
        self.monitor = False

        sheath_stop_cmd = ('stop', ('sheath_pump', ), {})
        outlet_stop_cmd = ('stop', ('outlet_pump', ), {})

        self._send_pumpcmd(sheath_stop_cmd)
        self._send_pumpcmd(outlet_stop_cmd)

    def change_flow(self, validate=True, start_monitor=False):
        if validate:
            valid, flow_rate = self._validate_flow_rate()
        else:
            flow_rate = float(self.flow_rate.GetValue())

        ratio = self.settings['sheath_ratio']
        excess = self.settings['sheath_excess']

        sheath_flow = flow_rate*excess
        outlet_flow = flow_rate/(1-ratio)

        self.sheath_setpoint = sheath_flow
        self.outlet_setpoint = outlet_flow

        if start_monitor:
            self.monitor = False

        sheath_fr_cmd = ('set_flow_rate', ('sheath_pump', sheath_flow), {})
        outlet_fr_cmd = ('set_flow_rate', ('outlet_pump', outlet_flow), {})

        self._send_pumpcmd(sheath_fr_cmd)
        self._send_pumpcmd(outlet_fr_cmd)

        if start_monitor:
            self.monitor_timer.Start(self.settings['settling_time'])

    def _validate_flow_rate(self):
        lc_flow_rate = self.flow_rate.GetValue()

        try:
            lc_flow_rate = float(lc_flow_rate)
            is_number = True
        except Exception:
            is_number = False

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

            if lc_flow_rate < 0.1 or lc_flow_rate > 2:
                is_extreme = True
            else:
                is_extreme = False
        else:
            is_extreme = False

        valid = True

        if not is_number:
            msg = ('The flow rate must be a valid number. Please correct this, '
                'then start the coflow.')

            wx.CallAfter(wx.MessageBox, msg, 'Error in coflow flow rate',
                style=wx.OK|wx.ICON_ERROR)

            valid = False

        elif not is_extreme:
            msg = ('LC flow rates are usually between 0.1-2 mL/min. The '
                'flow rate is currently set outside this range. Do you '
                'want to continue with this flow rate?')

            dialog = wx.MessageDialog(self, msg, 'Possible error in coflow flow rate',
                style=wx.YES_NO|wx.NO_DEFAULT|wx.ICON_QUESTION)

            ret = dialog.ShowModal()

            if ret == wx.ID_NO:
                valid = False

        return valid, lc_flow_rate

    def _get_flow_rates(self):
        sheath_density_cmd = ('get_density', ('sheath_fm',), {})
        outlet_density_cmd = ('get_density', ('outlet_fm',), {})

        sheath_t_cmd = ('get_temperature', ('sheath_fm',), {})
        outlet_t_cmd = ('get_temperature', ('outlet_fm',), {})

        sheath_fr_cmd = ('get_flow_rate', ('sheath_fm',), {})
        outlet_fr_cmd = ('get_flow_rate', ('outlet_fm',), {})

        low_warning = self.settings['warning_threshold_low']
        high_warning = self.settings['warning_threshold_high']

        cycle_time = time.time()
        start_time = copy.copy(cycle_time)

        sheath_fr_list = []
        outlet_fr_list = []

        sheath_density_list = []
        outlet_density_list = []

        sheath_t_list = []
        outlet_t_list = []

        fr_time_list = []
        aux_time_list = []

        while True:
            sheath_fr = self._send_fmcmd(sheath_fr_cmd, True)
            outlet_fr = self._send_fmcmd(outlet_fr_cmd, True)

            sheath_fr_list.append(sheath_fr)
            outlet_fr_list.append(outlet_fr)

            fr_time_list.append(time.time()-start_time)

            if self.monitor:
                if (sheath_fr < low_warning*self.sheath_setpoint or
                    sheath_fr > high_warning*self.sheath_setpoint):
                    wx.CallAfter(self._show_warning_dialog, 'sheath', sheath_fr)

                if (outlet_fr < low_warning*self.outlet_setpoint or
                    outlet_fr > high_warning*self.outlet_setpoint):
                    wx.CallAfter(self._show_warning_dialog, 'outlet', outlet_fr)

            if time.time() - cycle_time > 1:
                sheath_density = self._send_fmcmd(sheath_density_cmd, True)
                outlet_density = self._send_fmcmd(outlet_density_cmd, True)

                sheath_t = self._send_fmcmd(sheath_t_cmd, True)
                outlet_t = self._send_fmcmd(outlet_t_cmd, True)

                sheath_density_list.append(sheath_density)
                outlet_density_list.append(outlet_density)

                sheath_t_list.append(sheath_t)
                outlet_t_list.append(outlet_t)

                cycle_time = time.time()

                aux_time_list.append(cycle_time-start_time)

                wx.CallAfter(self.sheath_flow.SetLabel, str(sheath_fr))
                wx.CallAfter(self.outlet_flow.SetLabel, str(outlet_fr))

    def _on_monitor_timer(self, evt):
        self.monitor_timer.Stop()

        self.monitor = True

    def _show_warning_dialog(self, flow, flow_rate):
        if self.warning_dialog is None:
            msg = ('The {} flow rate is unstable. Contact your beamline '
                'scientist.'.format(flow))

            self.warning_dialog = CoflowWarningMessage(self, msg, 'Coflow flow is unstable')
            self.warning_dialog.Show()

    def _send_pumpcmd(self, cmd, response=False):
        full_cmd = {'device': 'pump', 'command': cmd, 'response': response}
        self.coflow_pump_cmd_q.append(full_cmd)

        if response:
            while len(self.coflow_pump_return_q) == 0:
                time.sleep(0.01)

            ret_val = self.coflow_pump_return_q.popleft()
        else:
            ret_val = None

        return ret_val

    def _send_fmcmd(self, cmd, response=False):
        """
        Sends commands to the pump using the ``fm_cmd_q`` that was given
        to :py:class:`FlowMeterCommThread`.

        :param str cmd: The command to send, matching the command in the
            :py:class:`FlowMeterCommThread` ``_commands`` dictionary.
        """
        full_cmd = {'device': 'fm', 'command': cmd, 'response': response}
        self.coflow_fm_cmd_q.append(full_cmd)

        if response:
            while len(self.coflow_fm_return_q) == 0:
                time.sleep(0.01)

            ret_val = self.coflow_fm_return_q.popleft()
        else:
            ret_val = None

        return ret_val

class CoflowWarningMessage(wx.Frame):
    def __init__(self, parent, msg, title, *args, **kwargs):
        """
        Initializes the pump frame. Takes args and kwargs for the wx.Frame class.
        """
        super(CoflowWarningMessage, self).__init__(parent, *args, caption=title **kwargs)
        logger.debug('Setting up the CoflowFrame')

        self.Bind(wx.EVT_CLOSE, self._on_exit)

        self._create_layout(msg)

        self.Fit()
        self.Raise()

    def _create_layout(self, msg):
        msg_panel = wx.Panel(self)

        msg_sizer = wx.BoxSizer(wx.HORIZONTAL)
        msg_sizer.Add(wx.Bitmap(wx.ART_WARNING), border=5, flag=wx.RIGHT)
        msg_sizer.Add(utils.AutoWrapStaticText(msg_panel, msg))

        ok_button = wx.Button(msg_panel, label='OK')
        ok_button.Bind(wx.EVT_BUTTON, self._on_exit)

        button_sizer = wx.BoxSizer(wx.HORIZONTAL)
        button_sizer.Add(ok_button, flag=wx.ALIGN_CENTER_HORIZONTAL)


        panel_sizer = wx.BoxSizer(wx.VERTICAL)
        panel_sizer.Add(msg_sizer, border=5, flag=wx.LEFT|wx.RIGHT|wx.TOP)
        panel_sizer.Add(button_sizer, border=5, flag=wx.ALL)

        msg_panel.SetSizer(panel_sizer)

        top_sizer = wx.BoxSizer(wx.HORIZONTAL)
        top_sizer.Add(msg_panel)

        self.SetSizer(top_sizer)

    def _on_exit(self):
        parent=self.GetParent()
        parent.warning_dialog = None

        self.Destroy()


class CoflowFrame(wx.Frame):
    """
    A lightweight frame allowing one to work with arbitrary number of pumps.
    Only meant to be used when the pumpcon module is run directly,
    rather than when it is imported into another program.
    """
    def __init__(self, mx_data, settings, *args, **kwargs):
        """
        Initializes the pump frame. Takes args and kwargs for the wx.Frame class.
        """
        super(CoflowFrame, self).__init__(*args, **kwargs)
        logger.debug('Setting up the CoflowFrame')

        self.Bind(wx.EVT_CLOSE, self._on_exit)

        self._create_layout(settings, mx_data)

        self.Fit()
        self.Raise()

    def _create_layout(self, settings, mx_data):
        """Creates the layout"""
        coflow_panel = CoflowPanel(self, settings, mx_data)

        self.exp_sizer = wx.BoxSizer(wx.HORIZONTAL)
        self.exp_sizer.Add(coflow_panel, proportion=1, flag=wx.EXPAND)

        top_sizer = wx.BoxSizer(wx.VERTICAL)
        top_sizer.Add(self.exp_sizer, flag=wx.EXPAND|wx.ALL, border=5)

        self.SetSizer(top_sizer)

    def _on_exit(self, evt):
        """Stops all current pump motions and then closes the frame."""
        logger.debug('Closing the CoflowFrame')
        if self.exp_event.is_set() and not self.abort_event.is_set():
            self.abort_event.set()
            time.sleep(2)

        self.exp_con.stop()
        while self.exp_con.is_alive():
            time.sleep(0.001)
        self.Destroy()

if __name__ == '__main__':
    logger = logging.getLogger('biocon')
    logger.setLevel(logging.DEBUG)

    h1 = logging.StreamHandler(sys.stdout)
    # h1.setLevel(logging.INFO)
    h1.setLevel(logging.DEBUG)
    formatter = logging.Formatter('%(asctime)s - %(message)s')
    h1.setFormatter(formatter)

    logger.addHandler(h1)

    #Settings
    settings = {
        'show_advanced_options' : False,
        'device_communication'  : 'remote',
        'remote_pump_ip'        : '164.54.204.104',
        'remote_pump_port'      : '5556',
        'remote_fm_ip'          : '164.54.204.104',
        'remote_fm_port'        : '5557',
        'flow_units'            : 'mL/min',
        'sheath_pump'           : ('VICI_M50', 'COM6', ['626.2', '9.278'], {}),
        'outlet_pump'           : ('VICI_M50', 'COM4', ['627.32', '11.826'], {}),
        'sheath_fm'             : ('BFS', 'COM8', [], {}),
        'outlet_fm'             : ('BFS', 'COM9', [], {}),
        'components'            : ['coflow'],
        'sheath_ratio'          : 0.5,
        'sheath_excess'         : 2,
        'warning_threshold_low' : 0.8,
        'warning_threshold_high': 1.2,
        'settling_time'         : 10000, #in ms
        }

    mx_data = {} #Testing only
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
    frame = CoflowFrame(mx_data, settings, None, title='Coflow Control')
    frame.Show()
    app.MainLoop()



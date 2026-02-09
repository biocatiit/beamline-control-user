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

if __name__ != '__main__':
    logger = logging.getLogger(__name__)

import numpy as np
import wx
# import zaber.serial as zaber
from six import string_types
try:
    import epics
    import epics.wx
except Exception:
    pass
try:
    import motorcon
except Exception:
    pass

import utils
import custom_epics_widgets

class ToastMotorPanel(utils.DevicePanel):

    def __init__(self, parent, panel_id, settings, *args, **kwargs):
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

        self._callbacks = []

        self._init_pvs(settings)

        super(ToastMotorPanel, self).__init__(parent, panel_id, settings, *args, **kwargs)

    def _init_pvs(self, settings):
        # Happens before create layout
        self.high_pv, connected = self._initialize_pv('{}.VAL'.format(
            settings['device_data']['kwargs']['high_pv']))
        self.low_pv, connected = self._initialize_pv('{}.VAL'.format(
            settings['device_data']['kwargs']['low_pv']))
        self.start_pv, connected = self._initialize_pv('{}.VAL'.format(
            settings['device_data']['kwargs']['start_pv']))
        self.start_lnk1, connected = self._initialize_pv('{}.LNK1'.format(
            settings['device_data']['kwargs']['start_pv']))
        self.start_lnk2, connected = self._initialize_pv('{}.LNK2'.format(
            settings['device_data']['kwargs']['start_pv']))

        self.start_lnk1.put('{} CA'.format(settings['device_data']['kwargs']
            ['motor']['args'][0]))
        self.start_lnk2.put('{} CA'.format(settings['device_data']['kwargs']
            ['motor']['args'][0]))

        self.motor_egu_pv, connected = self._initialize_pv('{}.EGU'.format(
            settings['device_data']['kwargs']['motor']['args'][0]))
        self.motor_speed_pv, connected = self._initialize_pv('{}.VELO'.format(
            settings['device_data']['kwargs']['motor']['args'][0]))
        self.motor_base_speed_pv, connected = self._initialize_pv('{}.VBAS'.format(
            settings['device_data']['kwargs']['motor']['args'][0]))
        self.motor_accel_pv, connected = self._initialize_pv('{}.ACCS'.format(
            settings['device_data']['kwargs']['motor']['args'][0]))
        self.motor_accelu_pv, connected = self._initialize_pv('{}.ACCU'.format(
            settings['device_data']['kwargs']['motor']['args'][0]))

    def _initialize_pv(self, pv_name):
        pv = epics.get_pv(pv_name)
        connected = pv.wait_for_connection(5)

        if not connected:
            logger.error('Failed to connect to EPICS PV %s on startup', pv_name)

        return pv, connected

    def _init_device(self, settings):
        #Happens after create layout
        self.motor_accelu_pv.put(1)
        self.motor_accel_pv.put(float(self.settings['device_data']['kwargs']
            ['default_accel']))
        self.motor_base_speed_pv.put(float(self.settings['device_data']['kwargs']
            ['default_speed']))
        self.motor_speed_pv.put(float(self.settings['device_data']['kwargs']
            ['default_speed']))

        self.speed_ctrl.ChangeValue(str((self.settings['device_data']['kwargs']
            ['default_speed'])))

        cbid = self.motor_base_speed_pv.add_callback(self._on_speed_change)

        self._callbacks.append((self.motor_base_speed_pv, cbid))

    def _create_layout(self):
        """Creates the layout for the panel."""
        parent = self

        motor_box = wx.StaticBox(parent, label='{} Motor'.format(
            self.settings['device_data']['name']))

        self.motor_panel = motorcon.EpicsMXMotorPanel(
            self.settings['device_data']['kwargs']['motor']['args'][0],
            None, motor_box)

        motor_sizer = wx.StaticBoxSizer(motor_box, wx.VERTICAL)
        motor_sizer.Add(self.motor_panel, flag=wx.EXPAND, proportion=1)


        toast_box = wx.StaticBox(parent, label='{} Controls'.format(
            self.settings['device_data']['name']))

        # high_ctrl = epics.wx.PVTextCtrl(toast_box, self.settings['device_data']['kwargs']['high_pv'], size=self._FromDIP((80,-1)))
        low_ctrl = epics.wx.PVTextCtrl(toast_box, self.low_pv, size=self._FromDIP((80,-1)))
        high_ctrl = custom_epics_widgets.PVTextCtrl2(toast_box, self.high_pv,
                dirty_timeout=None, validator=utils.CharValidator('float_te'),
                size=self._FromDIP((80, -1)))

        status_ctrl = custom_epics_widgets.PVTextLabeled(toast_box,
            '{}.BUSY'.format(self.settings['device_data']['kwargs']['start_pv']),
            fg='forest green')
        egu_ctrl1 = epics.wx.PVText(toast_box, self.motor_egu_pv)
        egu_ctrl2 = epics.wx.PVText(toast_box, self.motor_egu_pv)
        egu_ctrl3 = epics.wx.PVText(toast_box, self.motor_egu_pv,
            size=self._FromDIP((25,-1)))

        speed_units = wx.BoxSizer(wx.HORIZONTAL)
        speed_units.Add(egu_ctrl3)
        speed_units.Add(wx.StaticText(toast_box, label='/s'))

        self.speed_ctrl = utils.ValueEntry(self._on_speed_ctrl, toast_box,
            size=self._FromDIP((80,-1)), validator=utils.CharValidator('float_pos_te'))

        status_ctrl.SetTranslations({'0': 'Not Toasting', '1': 'Toasting'})
        status_ctrl.SetForegroundColourTranslations({'Toasting': 'forest green',
            'Not Toasting': 'red'})

        toast_ctrl_sizer = wx.FlexGridSizer(cols=3, hgap=self._FromDIP(5),
            vgap=self._FromDIP(5))
        toast_ctrl_sizer.Add(wx.StaticText(toast_box, label='Status:'),
            flag=wx.ALIGN_CENTER_VERTICAL)
        toast_ctrl_sizer.Add(status_ctrl, flag=wx.ALIGN_CENTER_VERTICAL)
        toast_ctrl_sizer.AddSpacer(1)
        toast_ctrl_sizer.Add(wx.StaticText(toast_box, label='High endpoint:'),
            flag=wx.ALIGN_CENTER_VERTICAL)
        toast_ctrl_sizer.Add(high_ctrl, flag=wx.ALIGN_CENTER_VERTICAL)
        toast_ctrl_sizer.Add(egu_ctrl1, flag=wx.ALIGN_CENTER_VERTICAL)
        toast_ctrl_sizer.Add(wx.StaticText(toast_box, label='Low endpoint:'),
            flag=wx.ALIGN_CENTER_VERTICAL)
        toast_ctrl_sizer.Add(low_ctrl, flag=wx.ALIGN_CENTER_VERTICAL)
        toast_ctrl_sizer.Add(egu_ctrl2, flag=wx.ALIGN_CENTER_VERTICAL)
        toast_ctrl_sizer.Add(wx.StaticText(toast_box, label='Speed:'),
            flag=wx.ALIGN_CENTER_VERTICAL)
        toast_ctrl_sizer.Add(self.speed_ctrl, flag=wx.ALIGN_CENTER_VERTICAL)
        toast_ctrl_sizer.Add(speed_units, flag=wx.ALIGN_CENTER_VERTICAL)

        start_button = epics.wx.PVButton(toast_box, self.start_pv, pushValue=1,
            label='Start')
        stop_button = epics.wx.PVButton(toast_box, '{}.ABORT'.format(
            self.settings['device_data']['kwargs']['start_pv']), pushValue=1,
        label='Stop')

        button_sizer = wx.BoxSizer(wx.HORIZONTAL)
        button_sizer.Add(start_button, flag=wx.RIGHT, border=self._FromDIP(5))
        button_sizer.Add(stop_button)

        toast_sizer = wx.StaticBoxSizer(toast_box, wx.VERTICAL)
        toast_sizer.Add(toast_ctrl_sizer, flag=wx.EXPAND|wx.ALL,
            border=self._FromDIP(5))
        toast_sizer.Add(button_sizer, flag=wx.BOTTOM|wx.LEFT|wx.RIGHT|
            wx.ALIGN_CENTER_HORIZONTAL, border=self._FromDIP(5))

        top_sizer = wx.BoxSizer(wx.VERTICAL)
        top_sizer.Add(motor_sizer, flag=wx.EXPAND|wx.ALL, border=self._FromDIP(5))
        top_sizer.Add(toast_sizer, flag=wx.EXPAND|wx.LEFT|wx.RIGHT|wx.BOTTOM,
            border=self._FromDIP(5))

        self.SetSizer(top_sizer)

    def _on_speed_ctrl(self, widget, value):
        try:
            speed = float(value)
        except Exception:
            speed = None

        if speed is not None:
            self.motor_base_speed_pv.put(speed)
            self.motor_speed_pv.put(speed)

    def _on_speed_change(self, **kwargs):
        value = kwargs['value']

        try:
            speed = float(value)
        except Exception:
            speed = None

        if speed is not None:
            self.speed_ctrl.SafeChangeValue(str(speed))

    def _on_close(self):
        """Device specific stuff goes here"""
        for pv, cbid in self._callbacks:
            pv.remove_callback(cbid)

    def on_exit(self):
        self.close()

class ToasterFrame(utils.DeviceFrame):

    def __init__(self, name, settings, *args, **kwargs):
        """
        Initializes the device frame. Takes frame name, utils.CommManager thread
        (or subclass), the device_panel class, and args and kwargs for the wx.Frame class.
        """
        super(ToasterFrame, self).__init__(name, settings, ToastMotorPanel,
            *args, **kwargs)

        # Enable these to init devices on startup
        self.setup_devices = self.settings.pop('device_init', None)

        self._init_devices()


#Settings
default_autosampler_settings = {
    'device_init'           : [
        {'name': 'Toast H', 'args': [], 'kwargs': {
            'motor'             : {'name': 'toast_h', 'args': ['18ID_DMC_E05:33'],
                                        'kwargs': {}},
            'default_speed'     : 2.0, #Motor EGU units, usually mm/s
            'default_accel'     : 0.0, #Motor EGU units, usually mm/s^2
            'high_pv'           : '18ID:Toast:H:High',
            'low_pv'            : '18ID:Toast:H:Low',
            'start_pv'          : '18ID:Toast:H:Move',
            }},
        {'name': 'Toast V', 'args': [], 'kwargs': {
            'motor'             : {'name': 'toast_v', 'args': ['18ID_DMC_E05:34'],
                                        'kwargs': {}},
            'default_speed'     : 2.0, #Motor EGU units, usually mm/s
            'default_accel'     : 0.0, #Motor EGU units, usually mm/s^2
            'high_pv'           : '18ID:Toast:V:High',
            'low_pv'            : '18ID:Toast:V:Low',
            'start_pv'          : '18ID:Toast:V:Move',
            }},
        ], # Compatibility with the standard format
    'device_communication'  : 'local',
    'remote_device'         : 'toaster', #Ignore
    'remote_ip'             : '164.54.204.53', #Ignore
    'remote_port'           : '5557', #Ignore
    'remote'                : False,
    'components'            : [],
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

    com_thread = None

    settings = default_autosampler_settings
    settings['components'] = ['toaster']

    settings['com_thread'] = com_thread

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
    frame = ToasterFrame('ToasterFrame', settings, parent=None,
        title='Toaster Control')
    frame.Show()
    app.MainLoop()



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
    from epics.wx.wxlib import EpicsFunction
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

        self.top_settings = settings

        if settings['device_communication'] == 'remote':
            settings['remote'] = True
        else:
            settings['remote'] = False

        self._callbacks = []

        self._init_pvs(settings)

        self._home_abort_evt = threading.Event()
        self._home_abort_evt.clear()
        self._home_motor_thread = None

        super(ToastMotorPanel, self).__init__(parent, panel_id, settings, *args, **kwargs)

    def _init_pvs(self, settings):
        # Happens before create layout
        self.high_pv, connected = self._initialize_pv('{}.VAL'.format(
            settings['device_data']['kwargs']['high_pv']))
        self.low_pv, connected = self._initialize_pv('{}.VAL'.format(
            settings['device_data']['kwargs']['low_pv']))
        self.start_pv, connected = self._initialize_pv('{}.VAL'.format(
            settings['device_data']['kwargs']['start_pv']))
        self.stop_pv, connected = self._initialize_pv('{}.ABORT'.format(
            settings['device_data']['kwargs']['start_pv']))
        self.start_lnk1, connected = self._initialize_pv('{}.LNK1'.format(
            settings['device_data']['kwargs']['start_pv']))
        self.start_lnk2, connected = self._initialize_pv('{}.LNK2'.format(
            settings['device_data']['kwargs']['start_pv']))

        self.start_lnk1.put('{} CA'.format(settings['device_data']['kwargs']
            ['motor']['args'][0]))
        self.start_lnk2.put('{} CA'.format(settings['device_data']['kwargs']
            ['motor']['args'][0]))

        self.motor = motorcon.EpicsMotor('toast', settings['device_data']['kwargs']
            ['motor']['args'][0])

        self.motor_egu_pv = self.motor.get_pv('EGU')
        self.motor_speed_pv = self.motor.get_pv('VELO')
        self.motor_base_speed_pv = self.motor.get_pv('VBAS')
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

    @EpicsFunction
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
        motor_sizer.Add(self.motor_panel, flag=wx.EXPAND|wx.ALL,
            border=self._FromDIP(5), proportion=1)


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

        self.auto_toast = wx.CheckBox(toast_box, label='Start/stop toasting with exposure')
        self.auto_toast.SetValue(False)

        if 'exposure' not in self.top_settings['components']:
            self.auto_toast.Disable()
            self.auto_toast.Hide()

        start_button = wx.Button(toast_box, label='Start')
        stop_button = wx.Button(toast_box, label='Stop')
        start_button.Bind(wx.EVT_BUTTON, self._on_start)
        stop_button.Bind(wx.EVT_BUTTON, self._on_stop)

        button_sizer = wx.BoxSizer(wx.HORIZONTAL)
        button_sizer.Add(start_button, flag=wx.RIGHT, border=self._FromDIP(5))
        button_sizer.Add(stop_button)

        toast_sizer = wx.StaticBoxSizer(toast_box, wx.VERTICAL)
        toast_sizer.Add(toast_ctrl_sizer, flag=wx.EXPAND|wx.ALL,
            border=self._FromDIP(5))
        toast_sizer.Add(self.auto_toast, flag=wx.BOTTOM|wx.LEFT|wx.RIGHT,
            border=self._FromDIP(5))
        toast_sizer.Add(button_sizer, flag=wx.BOTTOM|wx.LEFT|wx.RIGHT|
            wx.ALIGN_CENTER_HORIZONTAL, border=self._FromDIP(5))


        home_box = wx.StaticBox(parent, label='{} Homing'.format(
            self.settings['device_data']['name']))
        self._home_status = wx.StaticText(home_box, label='No')

        home_status_sizer = wx.BoxSizer(wx.HORIZONTAL)
        home_status_sizer.Add(wx.StaticText(home_box, label='Homing:'))
        home_status_sizer.Add(self._home_status, proportion=1, flag=wx.LEFT,
            border=self._FromDIP(5))

        self._start_home_btn = wx.Button(home_box, label='Home motor')
        self._start_home_btn.Bind(wx.EVT_BUTTON, self._on_home_motor)

        self._abort_home_btn = wx.Button(home_box, label='Abort homing')
        self._abort_home_btn.Bind(wx.EVT_BUTTON, self._on_home_abort)

        home_btn_sizer = wx.BoxSizer(wx.HORIZONTAL)
        home_btn_sizer.Add(self._start_home_btn)
        home_btn_sizer.Add(self._abort_home_btn, flag=wx.LEFT, border=self._FromDIP(5))

        home_sizer = wx.StaticBoxSizer(home_box, wx.VERTICAL)
        home_sizer.Add(home_status_sizer, flag=wx.EXPAND|wx.ALL, border=self._FromDIP(5))
        home_sizer.Add(home_btn_sizer, flag=wx.ALIGN_CENTER_HORIZONTAL|
            wx.LEFT|wx.RIGHT|wx.BOTTOM, border=self._FromDIP(5))

        top_sizer = wx.BoxSizer(wx.VERTICAL)
        top_sizer.Add(motor_sizer, flag=wx.EXPAND|wx.ALL, border=self._FromDIP(5))
        top_sizer.Add(toast_sizer, flag=wx.EXPAND|wx.LEFT|wx.RIGHT|wx.BOTTOM,
            border=self._FromDIP(5))
        top_sizer.Add(home_sizer, flag=wx.EXPAND|wx.LEFT|wx.RIGHT|wx.BOTTOM,
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

    @EpicsFunction
    def _on_speed_change(self, **kwargs):
        value = kwargs['value']

        try:
            speed = float(value)
        except Exception:
            speed = None

        if speed is not None:
            self.speed_ctrl.SafeChangeValue(str(speed))

    def _on_start(self, evt):
        self.start_toast()

    def _on_stop(self, evt):
        self.stop_toast()

    def start_toast(self, wait=False):
        self.start_pv.put(1, wait=wait)

    def stop_toast(self):
        self.stop_pv.put(1)
        self.start_pv.put(0)

    def auto_start(self):
        auto = self.auto_toast.GetValue()

        if auto:
            self.start_toast(wait=True)

        return True

    def auto_stop(self):
        auto = self.auto_toast.GetValue()

        if auto:
            self.stop_toast()

    def _on_home_motor(self, evt):
        self._start_home_btn.Disable()
        self._home_status.SetLabel('Yes')

        self._home_abort_evt.clear()
        self._home_motor_thread = threading.Thread(target=self.home_motor)
        self._home_motor_thread.daemon = True
        self._home_motor_thread.start()

    def _on_home_abort(self, evt):
        self._home_abort_evt.set()
        self.motor.stop()
        self._on_home_finish()

    def _on_home_finish(self):
        self._start_home_btn.Enable()
        self._home_status.SetLabel('No')

    @EpicsFunction
    def home_motor(self):
        home_to = step = self.settings['home_settings']['home_to']
        final_pos = self.settings['home_settings']['final_pos']
        home_offset = self.settings['home_settings']['offset']

        if home_to == 'center':
            plus_lim = self._inner_home_to_limit(1)

            if self._home_abort_evt.is_set():
                return

            minus_lim = self._inner_home_to_limit(-1)

            if self._home_abort_evt.is_set():
                return

            if plus_lim is not None and minus_lim is not None:
                home_pos = (plus_lim+minus_lim)/2
            else:
                home_pos = None

        elif home_to == 'plus':
            home_pos = self._inner_home_to_limit(1)

        elif home_to == 'minus':
            home_pos = self._inner_home_to_limit(-1)

        else:
            home_pos = None

        if home_pos is not None:

            if self._home_abort_evt.is_set():
                return

            home_pos += home_offset

            self.motor.move(home_pos, wait=True)

            if self._home_abort_evt.is_set():
                return

            self.motor.set_position(final_pos)

        wx.CallAfter(self._on_home_finish)


    def _inner_home_to_limit(self, direction):
        abort = False

        step = self.settings['home_settings']['step']
        speed = self.settings['home_settings']['speed']

        if direction == 1:
            on_lim = self.motor.on_high_limit()
        else:
            on_lim = self.motor.on_low_limit()

        abort = self._home_abort_evt.is_set()

        if not on_lim and not abort:
            if direction == 1:
                jog_dir = 'positive'
            else:
                jog_dir = 'negative'

            self.motor.jog(jog_dir, True)

        while not on_lim and not abort:
            if direction == 1:
                on_lim = self.motor.on_high_limit()
            else:
                on_lim = self.motor.on_low_limit()

            time.sleep(0.05)
            abort = self._home_abort_evt.is_set()

        self.motor.jog(jog_dir, False)

        move_off = -1*direction*step

        while on_lim and not abort:
            self.motor.move_relative(move_off)

            time.sleep(0.05)
            abort = self._home_abort_evt.is_set()

            if direction == 1:
                on_lim = self.motor.on_high_limit()
            else:
                on_lim = self.motor.on_low_limit()

        while self.motor.is_moving():
            time.sleep(0.05)
            abort = self._home_abort_evt.is_set()

        if not abort:
            motor_pos = self.motor.get_position()
        else:
            motor_pos = None

        return motor_pos

    def _on_close(self):
        """Device specific stuff goes here"""
        for pv, cbid in self._callbacks:
            pv.remove_callback(cbid)

    def on_exit(self):
        self.close()

class ToasterPanel(wx.Panel):

    def __init__(self, settings, *args, **kwargs):

        wx.Panel.__init__(self, *args, **kwargs)
        self.settings = settings

        self.devices =[]

        self.device_panel = ToastMotorPanel

        self._create_layout()

        # Enable these to init devices on startup
        self.setup_devices = self.settings.pop('device_init', None)
        self._init_devices()

    def _FromDIP(self, size):
        # This is a hack to provide easy back compatibility with wxpython < 4.1
        try:
            return self.FromDIP(size)
        except Exception:
            return size


    def _create_layout(self):
        """Creates the layout"""

        #Overwrite this
        self.sizer = wx.BoxSizer(wx.HORIZONTAL)

        device_sizer = wx.BoxSizer(wx.VERTICAL)
        device_sizer.Add(self.sizer, 1, flag=wx.EXPAND)

        self.device_parent = wx.Panel(self)

        self.device_parent.SetSizer(device_sizer)

        top_sizer = wx.BoxSizer(wx.VERTICAL)
        top_sizer.Add(self.device_parent, 1, flag=wx.EXPAND)

        self.SetSizer(top_sizer)

    def _init_devices(self):
        """
        This is a convenience function for initalizing devices on startup, if you
        already know what devices you want to add. You can add/comment it out in
        the ``__init__`` if you want to not load any devices on startup.

        If you want to add devices here, add them to the ``setup_devices`` list.
        Each entry should be an iterable with the following parameters: name,
        device type, comport, arg list, and kwarg dict in that order. How the
        arg list and kwarg dict are handled are defined in the
        DevicePanel._init_devices function, and depends on the device type.

        Add this to the _init__ and add a self.setup_devices list to the init
        """
        if not self.devices:
            try:
                self.sizer.Remove(0)
            except Exception:
                pass

        logger.info('Initializing %s devices on startup', str(len(self.setup_devices)))

        if self.setup_devices is not None:
            for device in self.setup_devices:
                dev_settings = {}
                for key, val in self.settings.items():
                    if key != 'com_thread':
                        try:
                            dev_settings[key] = copy.deepcopy(val)
                        except TypeError:
                            dev_settings[key] = val
                    else:
                        dev_settings[key] = val

                dev_settings['device_data'] = device
                new_device = self.device_panel(self.device_parent, wx.ID_ANY,
                    dev_settings)

                self.sizer.Add(new_device, 1, flag=wx.EXPAND|wx.ALL,
                    border=self._FromDIP(3))
                self.devices.append(new_device)

        self.Layout()
        self.Fit()

    def auto_start(self):
        success = [device.auto_start() for device in self.devices]

        return all(success)

    def auto_stop(self):
        for device in self.devices:
            device.auto_stop()

    def on_exit(self):
        for device in self.devices:
            device.on_exit()

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
default_toaster_settings = {
    'device_init'           : [
        {'name': 'Toast H', 'args': [], 'kwargs': {
            'motor'             : {'name': 'toast_h', 'args': ['18ID_DMC_E05:33'],
                                        'kwargs': {}},
            'default_speed'     : 2.0, #Motor EGU units, usually mm/s
            'default_accel'     : 0.0, #Motor EGU units, usually mm/s^2
            'high_pv'           : '18ID:Toast:H:High',
            'low_pv'            : '18ID:Toast:H:Low',
            'start_pv'          : '18ID:Toast:H:Move',
            'home_settings'     : { 'step'  : 0.1, # limit push off step size, motor EGU units, usually mm
                                    'speed' : 2.0, # Move to limit speed, motor EGU units, usually mm/s
                                    'home_to'   : 'center',
                                    'offset'    : 0, # Offset from nominal home to actual home position, motor EGU units
                                    'final_pos' : 0, # What to set the home position to
                                    },
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
    'com_thread'            : None,
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

    settings = default_toaster_settings
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



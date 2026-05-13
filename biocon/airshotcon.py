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
import threading
import time
import logging
import sys
import copy
import statistics

if __name__ != '__main__':
    logger = logging.getLogger(__name__)

import wx
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

class AirShotMotorPanel(utils.DevicePanel):

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

        self._init_pvs(settings)

        super(AirShotMotorPanel, self).__init__(parent, panel_id, settings, *args, **kwargs)

    def _init_pvs(self, settings):
        # Happens before create layout
        self.motor = motorcon.EpicsMotor(settings['device_data']['kwargs']['motor']['name'],
            settings['device_data']['kwargs']['motor']['args'][0])

        self.motor_egu_pv = self.motor.get_pv('EGU')
        self.motor_pos_pv = self.motor.get_pv('RBV')

    def _init_device(self, settings):
        #Happens after create layout
        pass

    def _create_layout(self):
        """Creates the layout for the panel."""
        parent = self

        motor_pane = wx.CollapsiblePane(parent, label="Motor control")
        motor_pane.Bind(wx.EVT_COLLAPSIBLEPANE_CHANGED, self.on_collapse)
        motor_win = motor_pane.GetPane()

        motor_box = wx.StaticBox(motor_win, label='{} Motor'.format(
            self.settings['device_data']['name']))

        self.motor_panel = motorcon.EpicsMXMotorPanel(
            self.settings['device_data']['kwargs']['motor']['args'][0],
            None, motor_box)

        motor_sizer = wx.StaticBoxSizer(motor_box, wx.VERTICAL)
        motor_sizer.Add(self.motor_panel, flag=wx.EXPAND|wx.ALL,
            border=self._FromDIP(5), proportion=1)

        motor_win.SetSizer(motor_sizer)


        air_box = wx.StaticBox(parent, label='{} Controls'.format(
            self.settings['device_data']['name']))

        egu_ctrl1 = epics.wx.PVText(air_box, self.motor_egu_pv)
        egu_ctrl2 = epics.wx.PVText(air_box, self.motor_egu_pv)
        pos_ctrl1 = epics.wx.PVText(air_box, self.motor_pos_pv,
            size=self._FromDIP((80,-1)))

        self.relative_move = wx.TextCtrl(air_box, size=self._FromDIP((80,-1)),
            value=str(self.settings['device_data']['kwargs']['default_dist']),
            validator=utils.CharValidator('float_neg'))

        move_ctrl = wx.FlexGridSizer(cols=3, hgap=self._FromDIP(5),
            vgap=self._FromDIP(5))
        move_ctrl.Add(wx.StaticText(air_box, label='Position:'),
            flag=wx.ALIGN_CENTER_VERTICAL)
        move_ctrl.Add(pos_ctrl1, flag=wx.ALIGN_CENTER_VERTICAL)
        move_ctrl.Add(egu_ctrl1, flag=wx.ALIGN_CENTER_VERTICAL)
        move_ctrl.Add(wx.StaticText(air_box, label='Move distance:'),
            flag=wx.ALIGN_CENTER_VERTICAL)
        move_ctrl.Add(self.relative_move, flag=wx.ALIGN_CENTER_VERTICAL)
        move_ctrl.Add(egu_ctrl2, flag=wx.ALIGN_CENTER_VERTICAL)


        self.auto_move = wx.CheckBox(air_box, label='Move with exposure')
        self.auto_move.SetValue(False)


        air_sizer = wx.StaticBoxSizer(air_box, wx.VERTICAL)
        air_sizer.Add(move_ctrl, flag=wx.EXPAND|wx.ALL,
            border=self._FromDIP(5))
        air_sizer.Add(self.auto_move, flag=wx.BOTTOM|wx.LEFT|wx.RIGHT,
            border=self._FromDIP(5))


        top_sizer = wx.BoxSizer(wx.VERTICAL)
        top_sizer.Add(air_sizer, flag=wx.EXPAND|wx.ALL,
            border=self._FromDIP(5))
        top_sizer.Add(motor_pane, flag=wx.EXPAND|wx.LEFT|wx.RIGHT|wx.BOTTOM,
            border=self._FromDIP(5))

        self.SetSizer(top_sizer)

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

    def get_auto_move_params(self):
        auto_move = self.auto_move.GetValue()
        dist = self.relative_move.GetValue()

        valid = True

        if auto_move:
            try:
                dist = float(dist)
            except Exception:
                valid = False

        if auto_move and valid:
            out_pos = self.motor.position + dist
            in_pos = self.motor.position
        else:
            # In case it still somehow tries to move it doesn't go anywhere
            out_pos = self.motor.position
            in_pos = self.motor.position

        return valid, auto_move, out_pos, in_pos, self.motor

    def _on_close(self):
        """Device specific stuff goes here"""
        pass

    def on_exit(self):
        self.close()

class AirShotPanel(wx.Panel):

    def __init__(self, settings, *args, **kwargs):

        wx.Panel.__init__(self, *args, **kwargs)
        self.settings = settings

        self.devices =[]

        self.device_panel = AirShotMotorPanel

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

        if self.setup_devices is not None:
            logger.info('Initializing %s devices on startup', str(len(self.setup_devices)))
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

    def get_airshot_values(self):
        air_shot_values = []
        all_valid = True
        for panel in self.devices:
            valid, auto_move, out_pos, in_pos, motor = panel.get_auto_move_params()
            air_shot_values.append([auto_move, out_pos, in_pos, motor])

            all_valid = all_valid and valid

        return air_shot_values, all_valid

    def on_exit(self):
        for device in self.devices:
            device.on_exit()

class AirShotFrame(utils.DeviceFrame):

    def __init__(self, name, settings, *args, **kwargs):
        """
        Initializes the device frame. Takes frame name, utils.CommManager thread
        (or subclass), the device_panel class, and args and kwargs for the wx.Frame class.
        """
        super(AirShotFrame, self).__init__(name, settings, AirShotMotorPanel,
            *args, **kwargs)

        # Enable these to init devices on startup
        self.setup_devices = self.settings.pop('device_init', None)

        self._init_devices()


#Settings
default_airshot_settings = {
    'device_init'           : [
        {'name': 'Air Inboard', 'args': [], 'kwargs': {
            'motor'             : {'name': 'air_inboard', 'args': ['18ID_DMC_E01:5'],
                                        'kwargs': {}},
            'default_dist'      : -7.0, # Default move distance
            }},
        {'name': 'Air Outboard', 'args': [], 'kwargs': {
            'motor'             : {'name': 'air_outboard', 'args': ['18ID_DMC_E01:6'],
                                        'kwargs': {}},
            'default_dist'      : -7.0, # Default move distance
            }},
        ], # Compatibility with the standard format
    'device_communication'  : 'local',
    'remote_device'         : 'airshot', #Ignore
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

    com_thread = None

    settings = default_airshot_settings
    settings['components'] = ['airshot']

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
    frame = AirShotFrame('AirShotFrame', settings, parent=None,
        title='Air Shot Control')
    frame.Show()
    app.MainLoop()



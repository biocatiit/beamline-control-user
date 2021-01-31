#! /usr/bin/env python
# coding: utf-8
#
#    Project: BioCAT staff beamline control software (CATCON)
#             https://github.com/biocatiit/beamline-control-staff
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

import logging
import sys
from collections import OrderedDict

import wx
import numpy as np

import utils


class ScanPanel(wx.Panel):
    """
    This creates the scan panel with both scan controls and the live plot. It
    allows both relative and absolute scans. THe user defines the start, stop,
    and step. It allows the user to define the counter (scaler) and count time.
    It also allows the user to fit the scan or derivative. Finally, it calculates
    various parameters (COM, FWHM), and allows the user to move to those positions
    or to any point in the scan.
    """
    def __init__(self, settings, *args, **kwargs):
        """
        Initializes the scan panel. Accepts the usual wx.Panel arguments plus
        the following.

        :param str device_name: The MX record name of the device.
        :param Mp.Record device: The Mp record (i.e. the device)
        :param Mp.Record server_record: The Mp record for the server that the
            device is located on.
        :param Mp.RecordList mx_database: The Mp record list representing the
            MX database being used.
        """
        wx.Panel.__init__(self, *args, **kwargs)

        self.settings = settings

        # self._get_devices()
        self._init_values()
        self._create_layout()

    def _init_values(self):
        self.scan_motor_panels = OrderedDict()

    def _create_layout(self):

        # Needs a button for 'Add motor'
        # On add motor should call a motor panel instance, which allows users
        # to set the name of the motor to be scanned and the scan range and number of steps and/or step size

        self.ctrl_parent = self

        add_button = wx.Button(self.ctrl_parent, label='Add scan motor')
        add_button.Bind(wx.EVT_BUTTON, self._on_add_button)

        button_sizer = wx.BoxSizer(wx.HORIZONTAL)
        button_sizer.Add(add_button)

        self.num_scans = wx.TextCtrl(self.ctrl_parent, size=(60, -1), value='1',
            validator=utils.CharValidator('int'))
        num_sizer = wx.FlexGridSizer(cols=2, vgap=5, hgap=5)
        num_sizer.Add(wx.StaticText(self.ctrl_parent, label='Number of scans:'),
            flag=wx.ALIGN_CENTER_VERTICAL)
        num_sizer.Add(self.num_scans, flag=wx.ALIGN_CENTER_VERTICAL)

        if 'exposure' in self.settings['components']:
            test_scan_button = wx.Button(self.ctrl_parent, label='Run test')
            test_scan_button.Bind(wx.EVT_BUTTON, self._on_test_scan)
            button_sizer.Add(test_scan_button, flag=wx.LEFT, border=5)

        self.motor_sizer = wx.GridBagSizer(vgap=5, hgap=5)

        self.top_sizer = wx.BoxSizer(wx.VERTICAL)
        self.top_sizer.Add(button_sizer, flag=wx.ALL|wx.ALIGN_CENTER_HORIZONTAL,
            border=5)
        self.top_sizer.Add(num_sizer, flag=wx.ALL, border=5)
        self.top_sizer.Add(self.motor_sizer, flag=wx.ALL, border=5)

        self.SetSizer(self.top_sizer)

    def _on_add_button(self, evt):
        self._add_motor()

    def _add_motor(self):
        motor_panel = MotorPanel(len(self.scan_motor_panels)+1, self.settings,
            self, self.ctrl_parent)
        self.scan_motor_panels[motor_panel.number] = motor_panel
        self.motor_sizer.Add(motor_panel, ((motor_panel.number-1)%2, (motor_panel.number-1)//2))

        self.ctrl_parent.Layout()
        self.Layout()
        self.Fit()
        self.GetParent().Layout()
        self.GetParent().Fit()

    def _on_test_scan(self):
        pass

    def get_scan_values(self):
        motor_params = OrderedDict()
        for num, motor_panel in self.scan_motor_panels.items():
            params = motor_panel.get_motor_params()
            motor_params[num] = params

        scan_values = {'motors' : motor_params,
            'num_scans' : self.num_scans.GetValue()}

        valid = self.validate_scan_values(scan_values)

        return scan_values, valid

    def validate_scan_values(self, scan_params):
        all_errors = []
        num_motors = 0

        for num in scan_params['motors']:
            params = scan_params['motors'][num]
            if params['use']:
                num_motors = num_motors + 1
                start_valid = True
                stop_valid = True
                step_valid = True
                error_list = []

                try:
                    params['start'] = float(params['start'])
                except Exception:
                    start_valid = False
                    error_list.append('start')

                try:
                    params['stop'] = float(params['stop'])
                except Exception:
                    stop_valid = False
                    error_list.append('stop')

                try:
                    params['step'] = float(params['step'])
                except Exception:
                    step_valid = False
                    error_list.append('step')

            if not start_valid or not stop_valid or not step_valid:
                error_msg = ('Motor {} had invalid {} parameters.'.format(num,
                    ', '.join(error_list)))

                all_errors.append(error_msg)

        try:
            scan_params['num_scans'] = int(scan_params['num_scans'])
        except Exception:
            all_errors.append(('Invalid number of scans (>=1)'))

        if isinstance(scan_params['num_scans'], int):
            if scan_params['num_scans'] <= 0:
                all_errors.append(('Invalid number of scans (>=1)'))

        if num_motors == 0:
            all_errors.append(('No motors selected for scan.'))

        if all_errors:
            all_valid = False

            msg = ('The following errors were found in the scan values:\n')
            errors = ''
            for error in all_errors:
                errors = errors + '- {}\n'.format(error)

            msg = msg + errors

            dialog = wx.MessageDialog(self, msg, 'Error in scan parameters',
                style=wx.OK|wx.ICON_ERROR)
            wx.CallAfter(dialog.ShowModal)

        else:
            all_valid = True

        return all_valid

    def metadata(self):
        metadata = OrderedDict()

        try:
            metadata['Number of scans:'] = self.num_scans.GetValue()
            for num, motor_panel in self.scan_motor_panels.items():
                params = motor_panel.get_motor_params()
                if params['use']:
                    metadata['Motor {}:'.format(num)] = params['motor']
                    metadata['Motor {} start:'.format(num)] = params['start']
                    metadata['Motor {} stop:'.format(num)] = params['stop']
                    metadata['Motor {} step:'.format(num)] = params['step']
                    metadata['Motor {} # steps:'.format(num)] = params['motor']
        except:
            pass

        return metadata

    def on_exit(self):
        pass

class MotorPanel(wx.Panel):

    def __init__(self, number, settings, top_frame, *args, **kwargs):

        wx.Panel.__init__(self, *args, **kwargs)

        self.number = number
        self.top_frame = top_frame
        self._create_layout(settings)

    def _create_layout(self, settings):
        top_parent = self

        self.top_sizer = wx.StaticBoxSizer(wx.VERTICAL, top_parent, 'Scan motor {}'.format(self.number))
        ctrl_parent = self.top_sizer.GetStaticBox()

        self.motor = wx.TextCtrl(ctrl_parent, size=(120, -1))
        self.start = wx.TextCtrl(ctrl_parent, size=(60, -1),
            style=wx.TE_PROCESS_ENTER, validator=utils.CharValidator('float_te'))
        self.stop = wx.TextCtrl(ctrl_parent, size=(60, -1),
            style=wx.TE_PROCESS_ENTER, validator=utils.CharValidator('float_te'))
        self.step = wx.TextCtrl(ctrl_parent, size=(60, -1),
            style=wx.TE_PROCESS_ENTER, validator=utils.CharValidator('float_te'))
        self.num_steps = wx.TextCtrl(ctrl_parent, size=(60, -1),
            style=wx.TE_PROCESS_ENTER, validator=utils.CharValidator('int_te'))

        self.start.Bind(wx.EVT_KILL_FOCUS, self._on_scan_range_change)
        self.start.Bind(wx.EVT_TEXT_ENTER, self._on_scan_range_change)
        self.stop.Bind(wx.EVT_KILL_FOCUS, self._on_scan_range_change)
        self.stop.Bind(wx.EVT_TEXT_ENTER, self._on_scan_range_change)
        self.step.Bind(wx.EVT_KILL_FOCUS, self._on_scan_range_change)
        self.step.Bind(wx.EVT_TEXT_ENTER, self._on_scan_range_change)
        self.num_steps.Bind(wx.EVT_KILL_FOCUS, self._on_scan_range_change)
        self.num_steps.Bind(wx.EVT_TEXT_ENTER, self._on_scan_range_change)

        self.use_in_scan = wx.CheckBox(ctrl_parent, label='Use in scan')
        self.use_in_scan.SetValue(True)

        motor_sizer = wx.FlexGridSizer(cols=2, vgap=5, hgap=5)
        motor_sizer.Add(wx.StaticText(ctrl_parent, label='Motor:'),
            flag=wx.ALIGN_CENTER_VERTICAL)
        motor_sizer.Add(self.motor, flag=wx.ALIGN_CENTER_VERTICAL|wx.EXPAND)
        motor_sizer.AddGrowableCol(1)

        scan_sizer = wx.FlexGridSizer(cols=3, vgap=2, hgap=2)
        scan_sizer.Add(wx.StaticText(ctrl_parent, label='Start'),
            flag=wx.ALIGN_CENTER_VERTICAL|wx.ALIGN_CENTER_HORIZONTAL)
        scan_sizer.Add(wx.StaticText(ctrl_parent, label='Stop'),
            flag=wx.ALIGN_CENTER_VERTICAL|wx.ALIGN_CENTER_HORIZONTAL)
        scan_sizer.Add(wx.StaticText(ctrl_parent, label='Step'),
            flag=wx.ALIGN_CENTER_VERTICAL|wx.ALIGN_CENTER_HORIZONTAL)
        scan_sizer.Add(self.start, flag=wx.ALIGN_CENTER_VERTICAL)
        scan_sizer.Add(self.stop, flag=wx.ALIGN_CENTER_VERTICAL)
        scan_sizer.Add(self.step, flag=wx.ALIGN_CENTER_VERTICAL)


        num_sizer = wx.FlexGridSizer(cols=2, vgap=5, hgap=5)
        num_sizer.Add(wx.StaticText(ctrl_parent, label='Number of steps:'),
            flag=wx.ALIGN_CENTER_VERTICAL)
        num_sizer.Add(self.num_steps, flag=wx.ALIGN_CENTER_VERTICAL)

        self.advanced_options = wx.StaticBoxSizer(wx.VERTICAL, ctrl_parent,
            'Advanced options')
        advanced_parent = self.advanced_options.GetStaticBox()

        self.motor_type = wx.Choice(advanced_parent, choices=['MX', 'Newport'])
        self.motor_type.SetSelection(0)
        self.motor_type.Bind(wx.EVT_CHOICE, self._on_type_change)

        type_sizer = wx.FlexGridSizer(cols=2, vgap=5, hgap=5)
        type_sizer.Add(wx.StaticText(advanced_parent, label='Motor type:'),
            flag=wx.ALIGN_CENTER_VERTICAL)
        type_sizer.Add(self.motor_type, flag=wx.ALIGN_CENTER_VERTICAL|wx.LEFT,
            border=5)

        self.newport_group = wx.TextCtrl(advanced_parent, size=(60,-1))
        self.newport_index = wx.TextCtrl(advanced_parent, size=(60,-1))
        self.newport_axes = wx.TextCtrl(advanced_parent, size=(60, -1))

        self.newport_sizer = wx.FlexGridSizer(cols=2, vgap=5, hgap=5)
        self.newport_sizer.Add(wx.StaticText(advanced_parent, label='Newport group:'),
            flag=wx.ALIGN_CENTER_VERTICAL)
        self.newport_sizer.Add(self.newport_group, flag=wx.ALIGN_CENTER_VERTICAL|wx.LEFT,
            border=5)
        self.newport_sizer.Add(wx.StaticText(advanced_parent, label='Newport index:'),
            flag=wx.ALIGN_CENTER_VERTICAL)
        self.newport_sizer.Add(self.newport_index, flag=wx.ALIGN_CENTER_VERTICAL|wx.LEFT,
            border=5)
        self.newport_sizer.Add(wx.StaticText(advanced_parent, label='Newport axes:'),
            flag=wx.ALIGN_CENTER_VERTICAL)
        self.newport_sizer.Add(self.newport_axes, flag=wx.ALIGN_CENTER_VERTICAL|wx.LEFT,
            border=5)


        self.advanced_options.Add(type_sizer, flag=wx.EXPAND|wx.TOP|wx.LEFT|wx.RIGHT,
            border=5)
        self.advanced_options.Add(self.newport_sizer, flag=wx.EXPAND|wx.ALL,
            border=5)



        self.top_sizer.Add(motor_sizer, flag=wx.LEFT|wx.RIGHT|wx.TOP|wx.EXPAND, border=5)
        self.top_sizer.Add(scan_sizer, flag=wx.LEFT|wx.RIGHT|wx.TOP|wx.EXPAND, border=5)
        self.top_sizer.Add(num_sizer, flag=wx.LEFT|wx.RIGHT|wx.TOP|wx.EXPAND, border=5)
        self.top_sizer.Add(self.use_in_scan, flag=wx.LEFT|wx.RIGHT|wx.TOP|wx.EXPAND, border=5)
        self.top_sizer.Add(self.advanced_options, flag=wx.ALL|wx.EXPAND, border=5)

        self.top_sizer.Show(self.advanced_options,
            settings['show_advanced_options'], recursive=True)

        self.advanced_options.Show(self.newport_sizer, False, recursive=True)

        self.SetSizer(self.top_sizer)
        self.Layout()

    def _on_scan_range_change(self, evt):
        try:
            start = float(self.start.GetValue())
            stop = float(self.stop.GetValue())
            calc = True
        except Exception:
            calc = False

        if calc:
            if evt.GetEventObject() == self.step:
                step = True
                try:
                    step_size = float(self.step.GetValue())
                except Exception:
                    calc = False

            elif evt.GetEventObject() == self.num_steps:
                step = False
                try:
                    num_step = float(self.num_steps.GetValue())
                except Exception:
                    calc = False

            else:
                step = True
                try:
                    step_size = float(self.step.GetValue())
                except Exception:
                    calc = False

        if calc:
            if step:
                if start < stop:
                    mtr_positions = np.arange(start, stop+step_size, step_size)
                else:
                    mtr_positions = np.arange(stop, start+step_size, step_size)
                    mtr_positions = mtr_positions[::-1]

                num_steps = mtr_positions.size

                self.num_steps.ChangeValue('{}'.format(num_steps))

            else:
                if start < stop:
                    mtr_positions, step_size = np.linspace(start, stop, num_step, retstep=True)
                else:
                    mtr_positions, step_size = np.linspace(stop, start, num_step, retstep=True)
                    mtr_positions = mtr_positions[::-1]

                self.step.ChangeValue('{}'.format(step_size))

    def get_motor_params(self):
        motor = self.motor.GetValue()
        start = self.start.GetValue()
        stop = self.stop.GetValue()
        step = self.step.GetValue()
        num_steps = self.num_steps.GetValue()
        use_in_scan = self.use_in_scan.GetValue()
        motor_type = self.motor_type.GetStringSelection()
        np_group = self.newport_group.GetValue()
        np_index = self.newport_index.GetValue()
        np_axes = self.newport_axes.GetValue()

        motor_params = {'motor' : motor,
            'start'     : start,
            'stop'      : stop,
            'step'      : step,
            'num_steps' : num_steps,
            'use'       : use_in_scan,
            'type'      : motor_type,
            'np_group'  : np_group,
            'np_index'  : np_index,
            'np_axes'   : np_axes,
            }

        return motor_params

    def _on_type_change(self, evt):
        if self.motor_type.GetStringSelection() == 'Newport':
            if self.top_sizer.IsShown(self.advanced_options):
                self.advanced_options.Show(self.newport_sizer, True, recursive=True)
        else:
            if self.top_sizer.IsShown(self.advanced_options):
                self.advanced_options.Show(self.newport_sizer, False, recursive=True)

        self.Layout()
        self.top_frame.Layout()
        self.top_frame.Fit()

class ScanFrame(wx.Frame):
    """
    A lightweight scan frame that holds the :mod:`ScanPanel`.
    """
    def __init__(self, settings, *args, **kwargs):
        """
        Initializes the scan frame. Takes all the usual wx.Frame arguments and
        also the following.

        :param str device_name: The MX record name of the device.
        :param Mp.Record device: The Mp record (i.e. the device)
        :param Mp.Record server_record: The Mp record for the server that the
            device is located on.
        :param Mp.RecordList mx_database: The Mp record list representing the
            MX database being used.
        """
        wx.Frame.__init__(self, *args, **kwargs)

        self._create_layout(settings)

        self.Layout()
        self.Fit()
        self.Layout()

        self.Bind(wx.EVT_CLOSE, self._on_close)

    def _create_layout(self, settings):
        """
        Creates the layout, by calling mod:`ScanPanel`.

        :param str device_name: The MX record name of the device.
        :param Mp.Record device: The Mp record (i.e. the device)
        :param Mp.Record server_record: The Mp record for the server that the
            device is located on.
        :param Mp.RecordList mx_database: The Mp record list representing the
            MX database being used.
        """
        self.scan_panel = ScanPanel(settings, parent=self)

        top_sizer = wx.BoxSizer(wx.HORIZONTAL)
        top_sizer.Add(self.scan_panel, 1, wx.EXPAND)

        self.scan_panel.Layout()
        self.scan_panel.Fit()
        self.scan_panel.Layout()

        self.SetSizer(top_sizer)

    def _on_close(self, evt):
        # self.scan_panel.exit()
        self.Destroy()


if __name__ == '__main__':
    logger = logging.getLogger()
    logger.setLevel(logging.DEBUG)
    h1 = logging.StreamHandler(sys.stdout)
    h1.setLevel(logging.INFO)
    # h1.setLevel(logging.DEBUG)
    formatter = logging.Formatter('%(asctime)s - %(name)s - %(threadName)s - %(levelname)s - %(message)s')
    h1.setFormatter(formatter)
    logger.addHandler(h1)

    settings = {
        'components'            : ['scan'],
        'newport_ip'            : '164.54.204.76',
        'newport_port'          : '5001',
        'show_advanced_options' : True,
        }

    app = wx.App()

    frame = ScanFrame(settings, parent=None, title='Scan Control')
    frame.Show()
    app.MainLoop()


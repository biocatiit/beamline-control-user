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

from collections import OrderedDict, deque, defaultdict
import logging
import sys
import math
from decimal import Decimal as D
import time
import threading
import copy
import traceback
import os

if __name__ != '__main__':
    logger = logging.getLogger(__name__)

import wx
import numpy as np

import motorcon
import pumpcon
import fmcon
import valvecon
import client
import XPS_C8_drivers as xps_drivers
import utils

class TRScanPanel(wx.Panel):
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
    def __init__(self, settings, *args, **kwargs):
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

        super(TRScanPanel, self).__init__(*args, **kwargs)
        logger.debug('Initializing TRScanPanel')

        self.settings = settings
        self.motor = None

        self.xps = None

        self._abort_event = threading.Event()

        self._create_layout()
        self._init_values()

    def _create_layout(self):
        """Creates the layout for the panel."""

        pos_units = self.settings['position_units']
        speed_units = self.settings['speed_units']
        accel_units = self.settings['accel_units']
        time_units = self.settings['time_units']

        self.x_start = wx.TextCtrl(self, size=(70, -1),
            validator=utils.CharValidator('float_neg'))
        self.x_end = wx.TextCtrl(self, size=(70, -1),
            validator=utils.CharValidator('float_neg'))
        self.y_start = wx.TextCtrl(self, size=(70, -1),
            validator=utils.CharValidator('float_neg'))
        self.y_end = wx.TextCtrl(self, size=(70, -1),
            validator=utils.CharValidator('float_neg'))
        self.x_step = wx.TextCtrl(self, size=(60, -1),
            validator=utils.CharValidator('float'))
        self.y_step = wx.TextCtrl(self, size=(60, -1),
            validator=utils.CharValidator('float'))

        self.x_start.Bind(wx.EVT_TEXT, self._on_param_change)
        self.x_end.Bind(wx.EVT_TEXT, self._on_param_change)
        self.x_step.Bind(wx.EVT_TEXT, self._on_param_change)
        self.y_start.Bind(wx.EVT_TEXT, self._on_param_change)
        self.y_end.Bind(wx.EVT_TEXT, self._on_param_change)
        self.y_step.Bind(wx.EVT_TEXT, self._on_param_change)

        scan_sizer = wx.FlexGridSizer(rows=3, cols=4, vgap=5, hgap=10)
        scan_sizer.AddSpacer(1)
        scan_sizer.Add(wx.StaticText(self, label='Start [{}]'.format(pos_units)),
            flag=wx.ALIGN_CENTER_HORIZONTAL)
        scan_sizer.Add(wx.StaticText(self, label='End [{}]'.format(pos_units)),
            flag=wx.ALIGN_CENTER_HORIZONTAL)
        scan_sizer.Add(wx.StaticText(self, label='Step [{}]'.format(pos_units)),
            flag=wx.ALIGN_CENTER_HORIZONTAL)
        scan_sizer.Add(wx.StaticText(self, label='X'),
            flag=wx.ALIGN_CENTER_VERTICAL)
        scan_sizer.Add(self.x_start, flag=wx.ALIGN_CENTER_VERTICAL)
        scan_sizer.Add(self.x_end, flag=wx.ALIGN_CENTER_VERTICAL)
        scan_sizer.Add(self.x_step, flag=wx.ALIGN_CENTER_VERTICAL)
        scan_sizer.Add(wx.StaticText(self, label='Y'),
            flag=wx.ALIGN_CENTER_VERTICAL)
        scan_sizer.Add(self.y_start, flag=wx.ALIGN_CENTER_VERTICAL)
        scan_sizer.Add(self.y_end, flag=wx.ALIGN_CENTER_VERTICAL)
        scan_sizer.Add(self.y_step, flag=wx.ALIGN_CENTER_VERTICAL)

        self.scan_speed = wx.TextCtrl(self, size=(60, -1),
            validator=utils.CharValidator('float'))
        self.num_scans = wx.TextCtrl(self, size=(60, -1),
            validator=utils.CharValidator('float'))

        self.scan_speed.Bind(wx.EVT_TEXT, self._on_param_change)
        self.num_scans.Bind(wx.EVT_TEXT, self._on_param_change)

        settings_sizer = wx.FlexGridSizer(rows=2, cols=2, vgap=5, hgap=5)
        settings_sizer.Add(wx.StaticText(self,
            label='Scan speed [{}]:'.format(speed_units)),
            flag=wx.ALIGN_CENTER_VERTICAL)
        settings_sizer.Add(self.scan_speed, flag=wx.ALIGN_CENTER_VERTICAL)
        settings_sizer.Add(wx.StaticText(self, label='Number of scans:'),
            flag=wx.ALIGN_CENTER_VERTICAL)
        settings_sizer.Add(self.num_scans)

        advanced_settings_pane = wx.CollapsiblePane(self, label='Advanced Settings')
        advanced_settings_pane.Bind(wx.EVT_COLLAPSIBLEPANE_CHANGED, self._on_collapse)
        adv_win = advanced_settings_pane.GetPane()

        self.return_speed = wx.TextCtrl(adv_win, size=(60, -1),
            validator=utils.CharValidator('float'))
        self.scan_acceleration = wx.TextCtrl(adv_win, size=(60, -1),
            validator=utils.CharValidator('float'))
        self.return_acceleration = wx.TextCtrl(adv_win, size=(60, -1),
            validator=utils.CharValidator('float'))
        self.scan_start_offset_dist = wx.TextCtrl(adv_win, size=(60, -1),
            validator=utils.CharValidator('float'))
        self.scan_end_offset_dist = wx.TextCtrl(adv_win, size=(60, -1),
            validator=utils.CharValidator('float'))
        self.constant_scan_speed = wx.CheckBox(adv_win, label='Constant scan speed')

        self.scan_type =wx.Choice(adv_win, choices=['Vector', 'Grid'])
        self.step_axis = wx.Choice(adv_win, choices=['X', 'Y', 'None'])
        self.step_speed = wx.TextCtrl(adv_win, size=(60, -1),
            validator=utils.CharValidator('float'))
        self.step_acceleration = wx.TextCtrl(adv_win, size=(60, -1),
            validator=utils.CharValidator('float'))

        self.gridpoints_from_file = wx.CheckBox(adv_win, label='Use steps from file')
        self.pick_file = wx.Button(adv_win, label='Select steps')
        self.step_filename = wx.TextCtrl(adv_win)

        self.pco_direction = wx.Choice(adv_win, choices=['x', 'y'])
        self.encoder_resolution = wx.TextCtrl(adv_win, size=(60, -1),
            validator=utils.CharValidator('float'))
        self.encoder_precision = wx.TextCtrl(adv_win, size=(60, -1),
            validator=utils.CharValidator('int'))

        self.return_speed.Bind(wx.EVT_TEXT, self._on_param_change)
        self.scan_acceleration.Bind(wx.EVT_TEXT, self._on_param_change)
        self.return_acceleration.Bind(wx.EVT_TEXT, self._on_param_change)
        self.scan_start_offset_dist.Bind(wx.EVT_TEXT, self._on_param_change)
        self.scan_end_offset_dist.Bind(wx.EVT_TEXT, self._on_param_change)
        self.constant_scan_speed.Bind(wx.EVT_CHECKBOX, self._on_param_change)
        self.scan_type.Bind(wx.EVT_CHOICE, self._on_param_change)
        self.step_axis.Bind(wx.EVT_CHOICE, self._on_param_change)
        self.step_speed.Bind(wx.EVT_TEXT, self._on_param_change)
        self.step_acceleration.Bind(wx.EVT_TEXT, self._on_param_change)
        self.gridpoints_from_file.Bind(wx.EVT_TEXT, self._on_param_change)
        self.pco_direction.Bind(wx.EVT_CHOICE, self._on_param_change)
        self.encoder_resolution.Bind(wx.EVT_TEXT, self._on_param_change)
        self.encoder_precision.Bind(wx.EVT_TEXT, self._on_param_change)

        self.pick_file.Bind(wx.EVT_BUTTON, self._on_pick_file)


        if 'exposure' in self.settings['components']:
            self.test_scan = wx.Button(adv_win, label='Run test')
            self.test_scan.Bind(wx.EVT_BUTTON, self._on_test_scan)

        adv_sizer = wx.BoxSizer(wx.VERTICAL)

        adv_settings_sizer = wx.GridBagSizer(hgap=5, vgap=5)
        adv_settings_sizer.Add(wx.StaticText(adv_win,
            label='Return speed [{}]:'.format(speed_units)), (0,0),
            flag=wx.ALIGN_CENTER_VERTICAL)
        adv_settings_sizer.Add(self.return_speed, (0,1),)
        adv_settings_sizer.Add(wx.StaticText(adv_win,
            label='Scan accel. [{}]:'.format(accel_units)), (1,0),
            flag=wx.ALIGN_CENTER_VERTICAL)
        adv_settings_sizer.Add(self.scan_acceleration, (1,1),)
        adv_settings_sizer.Add(wx.StaticText(adv_win,
            label='Return accel. [{}]:'.format(accel_units)), (2,0),
            flag=wx.ALIGN_CENTER_VERTICAL)
        adv_settings_sizer.Add(self.return_acceleration, (2,1),)
        adv_settings_sizer.Add(self.constant_scan_speed, (3, 0), span=(0,2))
        adv_settings_sizer.Add(wx.StaticText(adv_win,
            label='Start offset [{}]:'.format(pos_units)), (4,0),
            flag=wx.ALIGN_CENTER_VERTICAL)
        adv_settings_sizer.Add(self.scan_start_offset_dist, (4,1),)
        adv_settings_sizer.Add(wx.StaticText(adv_win,
            label='End offset [{}]:'.format(pos_units)), (5,0),
            flag=wx.ALIGN_CENTER_VERTICAL)
        adv_settings_sizer.Add(self.scan_end_offset_dist, (5,1),)
        adv_settings_sizer.Add(wx.StaticText(adv_win, label='Scan type:'), (6,0),
            flag=wx.ALIGN_CENTER_VERTICAL)
        adv_settings_sizer.Add(self.scan_type, (6,1), flag=wx.ALIGN_CENTER_VERTICAL)
        adv_settings_sizer.Add(wx.StaticText(adv_win, label='Step axis:'), (7,0),
            flag=wx.ALIGN_CENTER_VERTICAL)
        adv_settings_sizer.Add(self.step_axis, (7,1), flag=wx.ALIGN_CENTER_VERTICAL)
        adv_settings_sizer.Add(wx.StaticText(adv_win,
            label='Step speed [{}]:'.format(speed_units)), (8,0),
            flag=wx.ALIGN_CENTER_VERTICAL)
        adv_settings_sizer.Add(self.step_speed, (8,1),)
        adv_settings_sizer.Add(wx.StaticText(adv_win,
            label='Step accel. [{}]:'.format(accel_units)), (9,0),
            flag=wx.ALIGN_CENTER_VERTICAL)
        adv_settings_sizer.Add(self.step_acceleration, (9,1),)
        adv_settings_sizer.Add(self.gridpoints_from_file, (10,0), span=(0,2))
        adv_settings_sizer.Add(self.step_filename, (11,0),
            flag=wx.ALIGN_CENTER_VERTICAL|wx.EXPAND)
        adv_settings_sizer.Add(self.pick_file, (11,1), flag=wx.ALIGN_CENTER_VERTICAL)
        adv_settings_sizer.Add(wx.StaticText(adv_win, label='PCO direction:'),
            (12,0), flag=wx.ALIGN_CENTER_VERTICAL)
        adv_settings_sizer.Add(self.pco_direction, (12,1),
            flag=wx.ALIGN_CENTER_VERTICAL)
        adv_settings_sizer.Add(wx.StaticText(adv_win,
            label='Encoder resolution [{}]:'.format(
            self.settings['position_units'])), (13,0),
            flag=wx.ALIGN_CENTER_VERTICAL)
        adv_settings_sizer.Add(self.encoder_resolution, (13,1),
            flag=wx.ALIGN_CENTER_VERTICAL)
        adv_settings_sizer.Add(wx.StaticText(adv_win, label='Encoder precision:'),
            (14,0), flag=wx.ALIGN_CENTER_VERTICAL)
        adv_settings_sizer.Add(self.encoder_precision, (14,1),
            flag=wx.ALIGN_CENTER_VERTICAL)

        adv_sizer.Add(adv_settings_sizer)

        if 'exposure' in self.settings['components']:
            adv_sizer.Add(self.test_scan, border=5,
                flag=wx.TOP|wx.ALIGN_CENTER_HORIZONTAL)

        adv_win.SetSizer(adv_sizer)

        tr_ctrl_sizer = wx.StaticBoxSizer(wx.StaticBox(self, label='Scan Controls'),
            wx.VERTICAL)
        tr_ctrl_sizer.Add(scan_sizer, border=5, flag=wx.ALL)
        tr_ctrl_sizer.Add(settings_sizer, border=5, flag=wx.ALL)
        tr_ctrl_sizer.Add(advanced_settings_pane, border=5, flag=wx.ALL)


        self.scan_length = wx.StaticText(self)
        self.total_length = wx.StaticText(self)
        self.scan_time = wx.StaticText(self)
        self.return_time = wx.StaticText(self)
        self.total_scan_time = wx.StaticText(self)
        self.num_images = wx.StaticText(self)

        scan_calcs_sizer = wx.FlexGridSizer(rows=6, cols=2, vgap=2, hgap=5)
        scan_calcs_sizer.Add(wx.StaticText(self, label='Images per scan:'))
        scan_calcs_sizer.Add(self.num_images)
        scan_calcs_sizer.Add(wx.StaticText(self,
            label='Scan length [{}]:'.format(pos_units)))
        scan_calcs_sizer.Add(self.scan_length)
        scan_calcs_sizer.Add(wx.StaticText(self,
            label='Total length [{}]:'.format(pos_units)))
        scan_calcs_sizer.Add(self.total_length)
        scan_calcs_sizer.Add(wx.StaticText(self,
            label='Time per scan [{}]:'.format(time_units)))
        scan_calcs_sizer.Add(self.scan_time)
        scan_calcs_sizer.Add(wx.StaticText(self,
            label='Time per return [{}]:'.format(time_units)))
        scan_calcs_sizer.Add(self.return_time)
        scan_calcs_sizer.Add(wx.StaticText(self,
            label='Total time [{}]:'.format(time_units)))
        scan_calcs_sizer.Add(self.total_scan_time)

        scan_status_sizer = wx.StaticBoxSizer(wx.StaticBox(self, label='Scan Info'),
            wx.VERTICAL)
        scan_status_sizer.Add(scan_calcs_sizer, border=5, flag=wx.ALL)


        top_sizer = wx.BoxSizer(wx.VERTICAL)
        top_sizer.Add(tr_ctrl_sizer, flag=wx.EXPAND)
        top_sizer.Add(scan_status_sizer, border=10, flag=wx.EXPAND|wx.TOP)

        self.SetSizer(top_sizer)

    def _init_values(self):
        self.constant_scan_speed.SetValue(self.settings['constant_scan_speed'])
        self.x_start.ChangeValue(str(self.settings['x_start']))
        self.x_end.ChangeValue(str(self.settings['x_end']))
        self.y_start.ChangeValue(str(self.settings['y_start']))
        self.y_end.ChangeValue(str(self.settings['y_end']))
        self.scan_speed.ChangeValue(str(self.settings['scan_speed']))
        self.num_scans.ChangeValue(str(self.settings['num_scans']))
        self.return_speed.ChangeValue(str(self.settings['return_speed']))
        self.scan_acceleration.ChangeValue(str(self.settings['scan_acceleration']))
        self.return_acceleration.ChangeValue(str(self.settings['return_acceleration']))
        self.scan_start_offset_dist.ChangeValue(str(self.settings['scan_start_offset_dist']))
        self.scan_end_offset_dist.SetValue(str(self.settings['scan_end_offset_dist']))

        self.step_speed.SetValue('1')
        self.step_acceleration.SetValue('1')
        self.scan_type.SetStringSelection('Vector')
        self.step_axis.SetStringSelection('None')

        self.pco_direction.SetStringSelection(self.settings['pco_direction'])
        self.encoder_resolution.SetValue(str(self.settings['encoder_resolution']))
        self.encoder_precision.SetValue(str(self.settings['encoder_precision']))

        self.gridpoints = None

        if self.constant_scan_speed.IsChecked():
            self.scan_start_offset_dist.Disable()
            self.scan_end_offset_dist.Disable()
        else:
            self.scan_start_offset_dist.Enable()
            self.scan_end_offset_dist.Enable()

        if self.settings['motor_type'] == 'Newport_XPS':
            if self.xps is None:
                self.xps = xps_drivers.XPS()

            self.motor = motorcon.NewportXPSMotor('TRSAXS', self.xps, self.settings['motor_ip'],
                int(self.settings['motor_port']), 20, self.settings['motor_group_name'],
                2)

    def _on_collapse(self, evt):
        self.Layout()
        self.SendSizeEvent()

    def _on_pick_file(self, evt):
        dialog = wx.FileDialog(self, "Select grid points file", style=wx.FD_OPEN|wx.FD_FILE_MUST_EXIST)

        if dialog.ShowModal() == wx.ID_OK:
            fname = dialog.GetPath()
            dialog.Destroy()

            self.step_filename.SetValue(os.path.split(fname)[1])
            self.gridpoints_file = fname

            try:
                self.gridpoints = np.loadtxt(fname, unpack=True)

            except Exception:
                msg = ('The file {} does not have a readable format for gridpoints.')
                dialog = wx.MessageDialog(self, msg, 'File format error',
                    style=wx.OK|wx.ICON_ERROR)
                dialog.ShowModal()
                dialog.Destroy()


    def _on_param_change(self, evt):
        if evt.GetEventObject() == self.constant_scan_speed:
            if self.constant_scan_speed.IsChecked():
                self.scan_start_offset_dist.Disable()
                self.scan_end_offset_dist.Disable()
            else:
                self.scan_start_offset_dist.Enable()
                self.scan_end_offset_dist.Enable()

        self._param_change()

    def _on_test_scan(self, evt):
        if self.test_scan.GetLabel() == 'Run test':
            self._abort_event.clear()
            self.test_scan.SetLabel('Stop test')

            t = threading.Thread(target=self._run_test_scan)
            t.daemon = True
            t.start()

        else:
            self._abort_event.set()
            self.test_scan.SetLabel('Run test')

    def _run_test_scan(self):
        scan_settings, valid = self.get_scan_values()

        if valid:
            num_runs = scan_settings['num_scans']
            x_start = scan_settings['scan_x_start']
            x_end = scan_settings['scan_x_end']
            y_start = scan_settings['scan_y_start']
            y_end = scan_settings['scan_y_end']
            motor_type = scan_settings['motor_type']
            motor = scan_settings['motor']
            vect_scan_speed = scan_settings['vect_scan_speed']
            vect_scan_accel = scan_settings['vect_scan_accel']
            vect_return_speed = scan_settings['vect_return_speed']
            vect_return_accel = scan_settings['vect_return_accel']
            return_speed = scan_settings['return_speed']
            return_accel = scan_settings['return_accel']

            scan_type = scan_settings['scan_type']
            step_axis = scan_settings['step_axis']
            step_size = scan_settings['step_size']
            step_speed = scan_settings['step_speed']
            step_accel = scan_settings['step_acceleration']
            use_gridpoints = scan_settings['use_gridpoints']
            gridpoints = scan_settings['gridpoints']

            if motor_type == 'Newport_XPS':
                pco_start = scan_settings['pco_start']
                pco_end = scan_settings['pco_end']
                pco_step = scan_settings['pco_step']
                pco_direction = scan_settings['pco_direction']
                pco_pulse_width = scan_settings['pco_pulse_width']
                pco_encoder_settle_t = scan_settings['pco_encoder_settle_t']
                x_motor = str(scan_settings['motor_x_name'])
                y_motor = str(scan_settings['motor_y_name'])

            motor_cmd_q = deque()
            motor_answer_q = deque()
            abort_event = threading.Event()
            motor_con = motorcon.MotorCommThread(motor_cmd_q, motor_answer_q, abort_event, name='MotorCon')
            motor_con.start()

            motor_cmd_q.append(('add_motor', (motor, 'TR_motor'), {}))

            if motor_type == 'Newport_XPS':
                if pco_direction == 'x':
                    motor.stop_position_compare(x_motor)
                    motor.set_position_compare(x_motor, 0, pco_start, pco_end, pco_step)
                    motor.set_position_compare_pulse(x_motor, pco_pulse_width, pco_encoder_settle_t)
                else:
                    motor.stop_position_compare(y_motor)
                    motor.set_position_compare(y_motor, 1, pco_start, pco_end, pco_step)
                    motor.set_position_compare_pulse(y_motor, pco_pulse_width, pco_encoder_settle_t)

                motor.set_velocity(return_speed, x_motor, 0)
                motor.set_velocity(return_speed, y_motor, 1)
                motor.set_acceleration(return_accel, x_motor, 0)
                motor.set_acceleration(return_accel, y_motor, 1,)

            motor_cmd_q.append(('move_absolute', ('TR_motor', (x_start, y_start)), {}))

            if scan_type == 'vector':
                for current_run in range(1,num_runs+1):
                    logger.info('Scan %s started', current_run)

                    self._run_test_inner(motor, motor_type, motor_cmd_q,
                        vect_scan_speed, vect_scan_accel, vect_return_speed,
                        vect_return_accel, x_motor, y_motor, x_start, x_end,
                        y_start, y_end, current_run, pco_direction)

                    motor_cmd_q.append(('move_absolute', ('TR_motor', (x_start, y_start)), {}))

                    if self._abort_event.is_set():
                        break

            else:
                if not use_gridpoints:
                    if step_axis == 'x':
                        step_start = x_start
                        step_end = x_end
                    else:
                        step_start = y_start
                        step_end = y_end


                    if step_start < step_end:
                        mtr_positions = np.arange(step_start, step_end+step_size, step_size)

                        if mtr_positions[-1] > step_end:
                            mtr_positions = mtr_positions[:-1]

                    else:
                        mtr_positions = np.arange(step_end, step_start+step_size, step_size)
                        if mtr_positions[-1] > step_start:
                            mtr_positions = mtr_positions[:-1]
                        mtr_positions = mtr_positions[::-1]
                else:
                    mtr_positions = gridpoints

                for current_run in range(1, num_runs+1):
                    start = time.time()
                    timeout = False
                    while not motor.is_moving() and not timeout:
                        time.sleep(0.001) #Waits for motion to start
                        if time.time()-start>0.1:
                            timeout = True

                    while motor.is_moving():
                        if self._abort_event.is_set():
                            break
                        time.sleep(0.001)

                    if self._abort_event.is_set():
                        break

                    logger.info('Scan %s started', current_run)

                    for pos in mtr_positions:
                        if step_axis == 'x':
                            step_x_start = pos
                            step_x_end = pos
                            step_y_start = y_start
                            step_y_end = y_end
                            motor.set_velocity(step_speed, x_motor, 0)
                            motor.set_acceleration(step_accel, x_motor, 0)
                        else:
                            step_x_start = x_start
                            step_x_end = x_end
                            step_y_start = pos
                            step_y_end = pos
                            motor.set_velocity(step_speed, y_motor, 1)
                            motor.set_acceleration(step_accel, y_motor, 1)

                        motor_cmd_q.append(('move_absolute', ('TR_motor',
                            (step_x_start, step_y_start)), {}))

                        self._run_test_inner(motor, motor_type, motor_cmd_q,
                            vect_scan_speed, vect_scan_accel, vect_return_speed,
                            vect_return_accel, x_motor, y_motor, step_x_start,
                            step_x_end, step_y_start, step_y_end, current_run,
                            pco_direction)

                    if self._abort_event.is_set():
                        break

                    if step_axis == 'x':
                        motor.set_velocity(return_speed, x_motor, 0)
                        motor.set_acceleration(return_accel, x_motor, 0)
                    else:
                        motor.set_velocity(return_speed, y_motor, 1)
                        motor.set_acceleration(return_accel, y_motor, 1)

                    motor_cmd_q.append(('move_absolute', ('TR_motor', (x_start, y_start)), {}))

                    if self._abort_event.is_set():
                        break

        self.test_scan.SetLabel('Run test')

        start = time.time()
        timeout = False
        while not motor.is_moving() and not timeout:
            time.sleep(0.001) #Waits for motion to start
            if time.time()-start>0.5:
                timeout = True

        while motor.is_moving():
            if self._abort_event.is_set():
                break

            time.sleep(0.001)

        motor_con.stop()
        motor_con.join()

    def _run_test_inner(self, motor, motor_type, motor_cmd_q, vect_scan_speed,
        vect_scan_accel, vect_return_speed, vect_return_accel, x_motor, y_motor,
        x_start, x_end, y_start, y_end, current_run, pco_direction):
        start = time.time()
        timeout = False
        while not motor.is_moving() and not timeout:
            time.sleep(0.001) #Waits for motion to start
            if time.time()-start>0.1:
                timeout = True

        while motor.is_moving():
            if self._abort_event.is_set():
                return
            time.sleep(0.001)

        if self._abort_event.is_set():
            return

        if motor_type == 'Newport_XPS':
            if pco_direction == 'x':
                logger.debug('starting x pco')
                motor.start_position_compare(x_motor)
            else:
                logger.debug('starting y pco')
                motor.start_position_compare(y_motor)

            if vect_scan_speed[0] != 0:
                motor.set_velocity(vect_scan_speed[0], x_motor, 0)
            if vect_scan_speed[1] != 0:
                motor.set_velocity(vect_scan_speed[1], y_motor, 1)
            if vect_scan_accel[0] != 0:
                motor.set_acceleration(vect_scan_accel[0], x_motor, 0)
            if vect_scan_accel[1] != 0:
                motor.set_acceleration(vect_scan_accel[1], y_motor, 1)

        motor_cmd_q.append(('move_absolute', ('TR_motor', (x_end, y_end)), {}))

        start = time.time()
        timeout = False
        while not motor.is_moving() and not timeout:
            time.sleep(0.001) #Waits for motion to start
            if time.time()-start>0.5:
                timeout = True

        while motor.is_moving():
            if self._abort_event.is_set():
                break
            time.sleep(0.001)

        if motor_type == 'Newport_XPS':
            if pco_direction == 'x':
                motor.stop_position_compare(x_motor)
            else:
                motor.stop_position_compare(y_motor)

            if vect_return_speed[0] != 0:
                motor.set_velocity(vect_return_speed[0], x_motor, 0)
            if vect_return_speed[1] != 0:
                motor.set_velocity(vect_return_speed[1], y_motor, 1)
            if vect_return_accel[0] != 0:
                motor.set_acceleration(vect_return_accel[0], x_motor, 0)
            if vect_return_accel[1] != 0:
                motor.set_acceleration(vect_return_accel[1], y_motor, 1)

    def _param_change(self):
        calc = True
        constant_speed = self.constant_scan_speed.IsChecked()

        try:
            x_start = float(self.x_start.GetValue())
            x_end = float(self.x_end.GetValue())
            y_start = float(self.y_start.GetValue())
            y_end = float(self.y_end.GetValue())
            scan_speed = float(self.scan_speed.GetValue())
            num_scans = float(self.num_scans.GetValue())
            return_speed = float(self.return_speed.GetValue())
            scan_acceleration = float(self.scan_acceleration.GetValue())
            return_acceleration = float(self.return_acceleration.GetValue())
        except ValueError:
            calc = False

        if constant_speed and calc:
            if scan_acceleration != 0:
                accel_time = scan_speed/scan_acceleration
                scan_start_offset_dist = 0.5*scan_acceleration*(accel_time)**2
                scan_end_offset_dist = scan_start_offset_dist

                if round(scan_start_offset_dist, 3) <= 0.003:
                    scan_start_offset_dist = 0.004

                if round(scan_end_offset_dist, 3) <= 0.003:
                    scan_end_offset_dist = 0.004

                wx.CallAfter(self.scan_start_offset_dist.ChangeValue, str(round(scan_start_offset_dist, 3)))
                wx.CallAfter(self.scan_end_offset_dist.ChangeValue, str(round(scan_end_offset_dist, 3)))

        elif constant_speed and not calc:
            try:
                scan_speed = float(self.scan_speed.GetValue())
                scan_acceleration = float(self.scan_acceleration.GetValue())

                if scan_acceleration != 0:
                    accel_time = scan_speed/scan_acceleration
                    scan_start_offset_dist = 0.5*scan_acceleration*(accel_time)**2
                    scan_end_offset_dist = scan_start_offset_dist

                    if round(scan_start_offset_dist, 3) <= 0.003:
                        scan_start_offset_dist = 0.004

                    if round(scan_end_offset_dist, 3) <= 0.003:
                        scan_end_offset_dist = 0.004

                    wx.CallAfter(self.scan_start_offset_dist.ChangeValue, str(round(scan_start_offset_dist, 3)))
                    wx.CallAfter(self.scan_end_offset_dist.ChangeValue, str(round(scan_end_offset_dist, 3)))
            except ValueError:
                calc = False

        elif calc:
            try:
                scan_start_offset_dist = float(self.scan_start_offset_dist.GetValue())
                scan_end_offset_dist = float(self.scan_end_offset_dist.GetValue())

                if round(scan_start_offset_dist, 3) <= 0.003:
                    scan_start_offset_dist = 0.004
                    wx.CallAfter(self.scan_start_offset_dist.ChangeValue, str(round(scan_start_offset_dist, 3)))

                if round(scan_end_offset_dist, 3) <= 0.003:
                    scan_end_offset_dist = 0.004
                    wx.CallAfter(self.scan_end_offset_dist.ChangeValue, str(round(scan_end_offset_dist, 3)))

            except ValueError:
                calc = False

        if (calc and scan_speed != 0 and return_speed !=0 and
            scan_acceleration != 0 and return_acceleration !=0):

            scan_type = self.scan_type.GetStringSelection()
            step_axis = self.step_axis.GetStringSelection()
            x_step = self.x_step.GetValue()
            y_step = self.y_step.GetValue()
            step_speed = self.step_speed.GetValue()
            step_acceleration = self.step_acceleration.GetValue()

            use_grid_from_file = self.gridpoints_from_file.GetValue()

            if scan_type.lower() == 'grid' and not use_grid_from_file:
                if step_axis.lower() != 'none':
                    try:
                        if step_axis.lower() == 'x':
                            step = float(x_step)
                        else:
                            step = float(y_step)
                        step_speed = float(step_speed)
                        step_acceleration = float(step_acceleration)
                    except ValueError:
                        # traceback.print_exc()
                        calc = False
                else:
                    calc = False
                    step = None

                gridpoints = None

            elif scan_type.lower() == 'grid' and use_grid_from_file:
                gridpoints = self.gridpoints

            else:
                step = None
                gridpoints = None

            if calc:
                try:
                    (scan_length, total_length, time_per_scan, return_time,
                        total_time) = self._calc_scan_params(x_start, x_end, y_start,
                        y_end, scan_speed, return_speed, scan_acceleration,
                        return_acceleration, scan_start_offset_dist, scan_end_offset_dist,
                        num_scans, scan_type, step_axis, step, step_speed,
                        step_acceleration, gridpoints)

                    self.scan_length.SetLabel(str(round(scan_length, 3)))
                    self.total_length.SetLabel(str(round(total_length, 3)))
                    self.scan_time.SetLabel(str(round(time_per_scan, 3)))
                    self.return_time.SetLabel(str(round(return_time, 3)))
                    self.total_scan_time.SetLabel(str(round(total_time, 3)))
                except Exception:
                    # traceback.print_exc()
                    pass

                try:
                    return_vals = self._calc_exposure_params()
                except Exception:
                    # traceback.print_exc()
                    return_vals=[['calc_exposure_params_error'],]

                # print(return_vals)

                if return_vals and len(return_vals[0]) == 0:
                    num_images = return_vals[1]
                    self.num_images.SetLabel(str(num_images))

                    if 'exposure' in self.settings['components']:
                        exp_panel = wx.FindWindowByName('exposure')
                        exp_panel.set_exp_settings({'num_frames': num_images})

    def _calc_scan_params(self, x_start, x_end, y_start, y_end, scan_speed,
        return_speed, scan_acceleration, return_acceleration,
        scan_start_offset_dist, scan_end_offset_dist, num_scans, scan_type,
        step_axis, step_size, step_speed, step_acceleration, gridpoints):

        if scan_type.lower() == 'vector':

            scan_length = math.sqrt((x_end - x_start)**2+(y_end-y_start)**2)
            total_length = scan_length + scan_start_offset_dist + scan_end_offset_dist

            accel_time = scan_speed/scan_acceleration
            accel_dist = 0.5*scan_acceleration*(accel_time)**2

            if accel_dist > total_length/2.:
                accel_time = math.sqrt(total_length/scan_acceleration)
                time_per_scan = 2*accel_time
            else:
                time_per_scan = (total_length - accel_dist*2)/scan_speed + accel_time*2

            return_accel_time = return_speed/return_acceleration
            return_accel_dist = 0.5*return_acceleration*(return_accel_time)**2

            if return_accel_dist > total_length/2.:
                return_accel_time = math.sqrt(total_length/return_acceleration)
                return_time = 2*return_accel_time
            else:
                return_time = (total_length-return_accel_dist*2)/return_speed + return_accel_time*2

            total_time = (time_per_scan + return_time)*num_scans

        else:
            if step_axis.lower() == 'x':
                scan_length = abs(y_end - y_start)
                step_start = x_start
                step_end = x_end

            else:
                scan_length = abs(x_end - x_start)
                step_start = y_start
                step_end = y_end

            total_length = scan_length + scan_start_offset_dist + scan_end_offset_dist

            accel_time = scan_speed/scan_acceleration
            accel_dist = 0.5*scan_acceleration*(accel_time)**2

            if accel_dist > total_length/2.:
                accel_time = math.sqrt(total_length/scan_acceleration)
                time_per_scan = 2*accel_time
            else:
                time_per_scan = (total_length - accel_dist*2)/scan_speed + accel_time*2

            return_accel_time = return_speed/return_acceleration
            return_accel_dist = 0.5*return_acceleration*(return_accel_time)**2

            if return_accel_dist > total_length/2.:
                return_accel_time = math.sqrt(total_length/return_acceleration)
                return_time = 2*return_accel_time
            else:
                return_time = (total_length-return_accel_dist*2)/return_speed + return_accel_time*2

            total_vector_time = (time_per_scan + return_time)

            if gridpoints is None:
                if step_start < step_end:
                    mtr_positions = np.arange(step_start, step_end+step_size, step_size)
                else:
                    mtr_positions = np.arange(step_end, step_start+step_size, step_size)
                    mtr_positions = mtr_positions[::-1]

                num_steps = mtr_positions.size

                total_step_length = abs(step_start - step_end)

                accel_time = step_speed/step_acceleration
                accel_dist = 0.5*step_acceleration*(accel_time)**2

                if accel_dist > step_size/2.:
                    accel_time = math.sqrt(step_size/step_acceleration)
                    time_per_step = 2*accel_time
                else:
                    time_per_step = (step_size - accel_dist*2)/step_speed + accel_time*2

            else:
                delta_step = np.diff(gridpoints)

                total_step_length = abs(gridpoints[0]-gridpoints[-1])

                step_times = []

                for step_size in delta_step:
                    accel_time = step_speed/step_acceleration
                    accel_dist = 0.5*step_acceleration*(accel_time)**2

                    if accel_dist > step_size/2.:
                        accel_time = math.sqrt(step_size/step_acceleration)
                        time_per_step = 2*accel_time
                    else:
                        time_per_step = (step_size - accel_dist*2)/step_speed + accel_time*2

                    step_times.append(time_per_step)

                time_per_step - np.mean(step_times)

                num_steps = len(gridpoints)


            return_accel_time = return_speed/return_acceleration
            return_accel_dist = 0.5*return_acceleration*(return_accel_time)**2

            if return_accel_dist > total_step_length/2.:
                return_accel_time = math.sqrt(total_step_length/return_acceleration)
                scan_return_time = 2*return_accel_time
            else:
                scan_return_time = (total_step_length-return_accel_dist*2)/return_speed + return_accel_time*2

            total_time = (total_vector_time + time_per_step)*num_steps + scan_return_time
            total_time = total_time*num_scans

        return scan_length, total_length, time_per_scan, return_time, total_time

    def _calc_exposure_params(self):
        errors = []
        return_vals = []

        if 'exposure' in self.settings['components']:
            exp_panel = wx.FindWindowByName('exposure')
            exp_settings = exp_panel.exp_settings_decimal()

            if 'exp_time' in exp_settings and 'exp_period' in exp_settings:
                delta_t = max(exp_settings['exp_time']+
                    self.settings['min_off_time'], exp_settings['exp_period'])

                if self.settings['motor_type'] == 'Newport_XPS':
                    x_start = D(self.x_start.GetValue())
                    x_end = D(self.x_end.GetValue())
                    y_start = D(self.y_start.GetValue())
                    y_end = D(self.y_end.GetValue())
                    scan_speed = D(self.scan_speed.GetValue())
                    return_speed = D(self.return_speed.GetValue())
                    scan_acceleration = D(self.scan_acceleration.GetValue())
                    return_acceleration = D(self.return_acceleration.GetValue())
                    scan_type = self.scan_type.GetStringSelection().lower()
                    step_axis = self.step_axis.GetStringSelection().lower()
                    pco_direction = self.pco_direction.GetStringSelection()
                    encoder_resolution = D(self.encoder_resolution.GetValue())
                    encoder_precision = int(self.encoder_precision.GetValue())

                    (x_pco_step,
                        y_pco_step,
                        vect_scan_speed,
                        vect_scan_accel,
                        vect_return_speed,
                        vect_return_accel) = self._calc_pco_params(x_start,
                        x_end, y_start, y_end, scan_speed, return_speed,
                        scan_acceleration, return_acceleration, delta_t,
                        scan_type, step_axis, encoder_resolution,
                        encoder_precision)

                    if pco_direction == 'x':
                        pco_step = x_pco_step
                        if x_start < x_end:
                            pco_start = x_start
                            pco_end = x_end
                        else:
                            pco_start = x_start
                            pco_end = x_end
                        pco_speed = vect_scan_speed[0]
                    else:
                        pco_step = y_pco_step
                        if y_start < y_end:
                            pco_start = y_start
                            pco_end = y_end
                        else:
                            pco_start = y_start
                            pco_end = y_end
                        pco_speed = vect_scan_speed[1]

                    if pco_start % encoder_resolution != 0:
                        pco_start = self.round_to(pco_start, encoder_precision,
                            encoder_resolution)

                    if pco_end % encoder_resolution != 0:
                        pco_end = self.round_to(pco_end, encoder_precision,
                            encoder_resolution)

                    if abs(pco_start-pco_end) % pco_step == 0:
                        pco_end -= min(encoder_resolution, pco_step)

                    if isinstance(pco_step, float):
                        num_images = int(round(float(abs(pco_end-pco_start))/pco_step))
                    else:
                        num_images = int(round(abs(pco_end-pco_start)/pco_step))

                    if delta_t < float(self.settings['pco_pulse_width'])*2/1e6:
                        errors.append(('Exposure period (greater than 2*PCO '
                            'pulse width, {} {})'.format(
                            self.settings['pco_pulse_width']*2/1e6,
                            self.settings['time_units'])))

                    if (float(self.settings['pco_encoder_settle_t'])/1e6 >
                        float(encoder_resolution)/float(pco_speed)):
                        errors.append(('Encoder settling time must be less '
                            'than encoder resolution divded by axis speed'))

                    return_vals.extend([num_images, pco_step, pco_start, pco_end,
                    vect_scan_speed, vect_scan_accel, vect_return_speed,
                    vect_return_accel, x_pco_step, y_pco_step, x_start,
                    y_start])

                else:
                    errors.append(('Motor type {} not known, cannot '
                        'calculate triggering settings.'.format(
                        self.settings['motor_type'])))

            else:
                errors.append(('Exposure time must be properly set to '
                    'calculate triggering settings.'))

            return_vals.insert(0, errors)

        return return_vals

    def metadata(self):
        metadata = OrderedDict()

        pos_units = self.settings['position_units']
        speed_units = self.settings['speed_units']
        accel_units = self.settings['accel_units']
        time_units = self.settings['time_units']

        try:
            x_start = float(self.x_start.GetValue())
            x_end = float(self.x_end.GetValue())
            y_start = float(self.y_start.GetValue())
            y_end = float(self.y_end.GetValue())
            scan_speed = float(self.scan_speed.GetValue())
            num_scans = float(self.num_scans.GetValue())
            return_speed = float(self.return_speed.GetValue())
            scan_acceleration = float(self.scan_acceleration.GetValue())
            return_acceleration = float(self.return_acceleration.GetValue())
            scan_start_offset_dist = float(self.scan_start_offset_dist.GetValue())
            scan_end_offset_dist = float(self.scan_end_offset_dist.GetValue())

            scan_type = self.scan_type.GetStringSelection()
            step_axis = self.step_axis.GetStringSelection()

            if step_axis.lower() == 'x':
                x_step = float(self.x_step.GetValue())
            elif step_axis.lower() == 'y':
                y_step = float(self.y_step.GetValue())
            step_speed = float(self.step_speed.GetValue())
            step_acceleration = float(self.step_acceleration.GetValue())

            use_grid_from_file = self.gridpoints_from_file.GetValue()

            if scan_type.lower() == 'grid' and not use_grid_from_file:
                if step_axis.lower() == 'x':
                    step_size = x_step
                elif step_axis.lower() == 'y':
                    step_size = y_step
                else:
                    step_size = None

                gridpoints = None

            elif scan_type.lower() == 'grid' and use_grid_from_file:
                gridpoints = self.gridpoints
                step_size = None

            else:
                step_size = None
                gridpoints = None


            (scan_length, total_length, time_per_scan, return_time,
                total_time) = self._calc_scan_params(x_start, x_end, y_start,
                y_end, scan_speed, return_speed, scan_acceleration,
                return_acceleration, scan_start_offset_dist, scan_end_offset_dist,
                num_scans, scan_type, step_axis, step_size, step_speed,
                step_acceleration, gridpoints)

            metadata['Scan type:'] = scan_type
            if scan_type.lower() == 'grid':
                metadata['Vector scan length [{}]:'.format(pos_units)] = scan_length
                metadata['Step axis:'] = step_axis

                if use_grid_from_file:
                    metadata['Using gridpoints from:'] = self.gridpoints_file
            else:
                metadata['Scan length [{}]:'.format(pos_units)] = scan_length
            metadata['Number of scans:'] = num_scans
            metadata['Time per scan [{}]:'.format(time_units)] = time_per_scan
            metadata['Return time [{}]:'.format(time_units)] = return_time
            metadata['Total time [{}]:'.format(time_units)] = total_time
            metadata['X scan start [{}]:'.format(pos_units)] = x_start
            metadata['X scan end [{}]:'.format(pos_units)] = x_end

            if scan_type.lower() == 'grid' and step_axis.lower() == 'x' and not use_grid_from_file:
                metadata['X scan step [{}]:'.format(pos_units)] = x_step

            metadata['Y scan start [{}]:'.format(pos_units)] = y_start
            metadata['Y scan end [{}]:'.format(pos_units)] = y_end

            if scan_type.lower() == 'grid' and step_axis.lower() == 'y' and not use_grid_from_file:
                metadata['Y scan step [{}]:'.format(pos_units)] = y_step

            metadata['Scan speed [{}]:'.format(speed_units)] = scan_speed
            metadata['Return speed [{}]:'.format(speed_units)] = return_speed
            metadata['Scan acceleration [{}]:'.format(accel_units)] = scan_acceleration
            metadata['Return acceleration [{}]:'.format(accel_units)] = return_acceleration

            if scan_type.lower() == 'grid':
                metadata['Step speed [{}]:'.format(speed_units)] = step_speed
                metadata['Step acceleration [{}]:'.format(accel_units)] = step_acceleration

            metadata['Scan start offset [{}]:'.format(pos_units)] = scan_start_offset_dist
            metadata['Scan end offset [{}]:'.format(pos_units)] = scan_end_offset_dist

        except (ValueError, ZeroDivisionError):
            print(traceback.print_exc())

        return metadata

    def get_scan_values(self):
        valid = True

        x_start = self.x_start.GetValue()
        x_end = self.x_end.GetValue()
        x_step = self.x_step.GetValue()
        y_start = self.y_start.GetValue()
        y_end = self.y_end.GetValue()
        y_step = self.y_step.GetValue()
        scan_speed = self.scan_speed.GetValue()
        num_scans = self.num_scans.GetValue()
        return_speed = self.return_speed.GetValue()
        scan_acceleration = self.scan_acceleration.GetValue()
        return_acceleration = self.return_acceleration.GetValue()
        scan_start_offset_dist = self.scan_start_offset_dist.GetValue()
        scan_end_offset_dist = self.scan_end_offset_dist.GetValue()
        scan_type = self.scan_type.GetStringSelection().lower()
        step_axis = self.step_axis.GetStringSelection().lower()
        step_speed = self.step_speed.GetValue()
        step_acceleration = self.step_acceleration.GetValue()
        use_grid_from_file = self.gridpoints_from_file.GetValue()
        pco_direction = self.pco_direction.GetStringSelection()
        encoder_resolution = self.encoder_resolution.GetValue()
        encoder_precision = self.encoder_precision.GetValue()

        errors = []

        pos_units = self.settings['position_units']
        speed_units = self.settings['speed_units']
        accel_units = self.settings['accel_units']

        try:
            x_start = float(x_start)
        except Exception:
            errors.append('Starting X position (between {} and {} {})'.format(
                self.settings['x_range'][0], self.settings['x_range'][1],
                pos_units))

        try:
            x_end = float(x_end)
        except Exception:
            errors.append('Final X position (between {} and {} {})'.format(
                self.settings['x_range'][0], self.settings['x_range'][1],
                pos_units))

        try:
            y_start = float(y_start)
        except Exception:
            errors.append('Starting Y position (between {} and {} {})'.format(
                self.settings['y_range'][0], self.settings['y_range'][1],
                pos_units))

        try:
            y_end = float(y_end)
        except Exception:
            errors.append('Final Y position (between {} and {} {})'.format(
                self.settings['y_range'][0], self.settings['y_range'][1],
                pos_units))

        try:
            scan_speed = float(scan_speed)
        except Exception:
            errors.append('Scan speed (between {} and {} {})'.format(
                self.settings['speed_lim'][0], self.settings['speed_lim'][1],
                speed_units))

        try:
            return_speed = float(return_speed)
        except Exception:
            errors.append('Return speed (between {} and {} {})'.format(
                self.settings['speed_lim'][0], self.settings['speed_lim'][1],
                speed_units))

        try:
            scan_acceleration = float(scan_acceleration)
        except Exception:
            errors.append('Scan acceleration (between {} and {} {})'.format(
                self.settings['acceleration_lim'][0],
                self.settings['acceleration_lim'][1], accel_units))

        try:
            return_acceleration = float(return_acceleration)
        except Exception:
            errors.append('Return acceleration (between {} and {} {})'.format(
                self.settings['acceleration_lim'][0],
                self.settings['acceleration_lim'][1], accel_units))

        try:
            encoder_resolution = float(encoder_resolution)
        except Exception:
            errors.append('Encoder resolution must be a number.')

        try:
            encoder_precision = int(encoder_precision)
        except Exception:
            errors.append('Encoder precision must be an integer.')

        try:
            scan_start_offset_dist = float(scan_start_offset_dist)
        except Exception:
            errors.append('Start offset (greater than or equal to 0)')

        try:
            scan_end_offset_dist = float(scan_end_offset_dist)
        except Exception:
            errors.append('End offset (greater than or equal to 0)')

        try:
            num_scans = int(num_scans)
        except Exception:
            errors.append('Number of scans (greater than 0)')

        if (isinstance(x_start, float) and isinstance(x_end, float) and
            isinstance(y_start, float) and isinstance(y_end, float)):

            tot_dist = math.sqrt((x_end - x_start)**2 + (y_end-y_start)**2)
            if tot_dist == 0:
                errors.append('Scan length (greater than 0 {})'.format(
                pos_units))

        if scan_type == 'grid':
            if step_axis == 'none':
                errors.append('For a grid scan you must specify the step axis (X or Y).')
            else:
                if step_axis != pco_direction:
                    if not use_grid_from_file:
                        if step_axis == 'x':
                            try:
                                step_size = float(x_step)
                            except Exception:
                                errors.append('X step size (greater than 0)')
                        else:
                            try:
                                step_size = float(y_step)
                            except Exception:
                                errors.append('Y step size (greater than 0)')
                        gridpoints = None

                    try:
                        step_speed = float(step_speed)
                    except Exception:
                        errors.append('Step speed (greater than 0)')

                    try:
                        step_acceleration = float(step_acceleration)
                    except Exception:
                        errors.append('Step acceleration (greater than 0)')

                    if use_grid_from_file:
                        if self.gridpoints is None:
                            errors.append('You must select a file for the gridpoints.')

                        else:
                            if not (all(self.gridpoints == sorted(self.gridpoints)) or
                                all(self.gridpoints[::-1] == sorted(self.gridpoints))):

                                errors.append('Gridpoints should be specified in ascending or descending order.')

                            else:
                                gridpoints = self.gridpoints
                                step_size = None

                                if step_axis == 'x':
                                    x_start = gridpoints[0]
                                    x_end = gridpoints[-1]

                                    wx.CallAfter(self.x_start.ChangeValue, str(x_start))
                                    wx.CallAfter(self.x_end.ChangeValue, str(x_end))
                                    wx.CallAfter(self.x_step.ChangeValue, '')

                                else:
                                    y_start = gridpoints[0]
                                    y_end = gridpoints[-1]

                                    wx.CallAfter(self.y_start.ChangeValue, str(y_start))
                                    wx.CallAfter(self.y_end.ChangeValue, str(y_end))
                                    wx.CallAfter(self.y_step.ChangeValue, '')
                else:
                    errors.append('PCO (vector) direction should be different from the step direction')
        else:
            step_size = None
            gridpoints = None


        if isinstance(x_start, float):
            if (x_start < self.settings['x_range'][0] or
                x_start > self.settings['x_range'][1]):
                errors.append('Starting X position (between {} and {} {})'.format(
                self.settings['x_range'][0], self.settings['x_range'][1],
                pos_units))

        if isinstance(x_end, float):
            if (x_end < self.settings['x_range'][0] or
                x_end > self.settings['x_range'][1]):
                errors.append('Final X position (between {} and {} {})'.format(
                self.settings['x_range'][0], self.settings['x_range'][1],
                pos_units))

        if isinstance(y_start, float):
            if (y_start < self.settings['y_range'][0] or
                y_start > self.settings['y_range'][1]):
                errors.append('Starting Y position (between {} and {} {})'.format(
                self.settings['y_range'][0], self.settings['y_range'][1],
                pos_units))

        if isinstance(y_end, float):
            if (y_end < self.settings['y_range'][0] or
                y_end > self.settings['y_range'][1]):
                errors.append('Final Y position (between {} and {} {})'.format(
                self.settings['y_range'][0], self.settings['y_range'][1],
                pos_units))

        if isinstance(scan_speed, float):
            if (scan_speed <= self.settings['speed_lim'][0] or
                scan_speed > self.settings['speed_lim'][1]):
                errors.append('Scan speed (between {} and {} {})'.format(
                self.settings['speed_lim'][0], self.settings['speed_lim'][1],
                pos_units))

        if isinstance(return_speed, float):
            if (return_speed <= self.settings['speed_lim'][0] or
                return_speed > self.settings['speed_lim'][1]):
                errors.append('Return speed (between {} and {} {})'.format(
                self.settings['speed_lim'][0], self.settings['speed_lim'][1],
                pos_units))

        if isinstance(scan_acceleration, float):
            if (scan_acceleration <= self.settings['acceleration_lim'][0] or
                scan_acceleration > self.settings['acceleration_lim'][1]):
                errors.append('Scan acceleration (between {} and {} {})'.format(
                self.settings['acceleration_lim'][0],
                self.settings['acceleration_lim'][1], pos_units))

        if isinstance(return_acceleration, float):
            if (return_acceleration <= self.settings['acceleration_lim'][0] or
                return_acceleration > self.settings['acceleration_lim'][1]):
                errors.append('Return acceleration (between {} and {} {})'.format(
                self.settings['acceleration_lim'][0],
                self.settings['acceleration_lim'][1], pos_units))

        if (isinstance(scan_start_offset_dist, float) and isinstance(x_start, float)
            and isinstance(x_end, float) and isinstance(y_start, float) and
            isinstance(y_end, float)):

            if scan_type == 'vector':
                tot_dist = math.sqrt((x_end - x_start)**2 + (y_end-y_start)**2)
                if tot_dist != 0:
                    x_prop = abs((x_end - x_start)/tot_dist)
                else:
                    x_prop = 1
            else:
                x_prop = 1

            if scan_type == 'vector' or (scan_type == 'grid' and step_axis == 'y'):
                if x_start < x_end:
                    scan_x_start = x_start - scan_start_offset_dist*x_prop
                else:
                    scan_x_start = x_start + scan_start_offset_dist*x_prop

                if (scan_x_start < self.settings['x_range'][0] or
                    scan_x_start > self.settings['x_range'][1]):
                    errors.append(('Starting X position plus (or minus) start '
                        'offset (between {} and {} {})'.format(
                        self.settings['x_range'][0], self.settings['x_range'][1],
                        pos_units)))
            else:
                scan_x_start = x_start

        if (isinstance(scan_end_offset_dist, float) and isinstance(x_start, float)
            and isinstance(x_end, float) and isinstance(y_start, float) and
            isinstance(y_end, float)):

            if scan_type == 'vector':
                tot_dist = math.sqrt((x_end - x_start)**2 + (y_end-y_start)**2)
                if tot_dist != 0:
                    x_prop = abs((x_end - x_start)/tot_dist)
                else:
                    x_prop = 1
            else:
                x_prop = 1

            if scan_type == 'vector' or (scan_type == 'grid' and step_axis == 'y'):
                if x_end < x_start:
                    scan_x_end = x_end - scan_end_offset_dist*x_prop
                else:
                    scan_x_end = x_end + scan_end_offset_dist*x_prop

                if (scan_x_end < self.settings['x_range'][0] or
                    scan_x_end > self.settings['x_range'][1]):
                    errors.append(('Final X position plus (or minus) end '
                        'offset (between {} and {} {})'.format(
                        self.settings['x_range'][0], self.settings['x_range'][1],
                        pos_units)))
            else:
                scan_x_end = x_end

        if (isinstance(scan_start_offset_dist, float) and isinstance(x_start, float)
            and isinstance(x_end, float) and isinstance(y_start, float) and
            isinstance(y_end, float)):

            if scan_type == 'vector':
                tot_dist = math.sqrt((x_end - x_start)**2 + (y_end-y_start)**2)
                if tot_dist != 0:
                    y_prop = abs((y_end-y_start)/tot_dist)
                else:
                    y_prop = 1
            else:
                y_prop = 1

            if scan_type == 'vector' or (scan_type == 'grid' and step_axis == 'x'):
                if y_start < y_end:
                    scan_y_start = y_start - scan_start_offset_dist*y_prop
                else:
                    scan_y_start = y_start + scan_start_offset_dist*y_prop

                if (scan_y_start < self.settings['y_range'][0] or
                    scan_y_start > self.settings['y_range'][1]):
                    errors.append(('Starting Y position plus (or minus) start '
                        'offset (between {} and {} {})'.format(
                        self.settings['y_range'][0], self.settings['y_range'][1],
                        pos_units)))
            else:
                scan_y_start = y_start

        if (isinstance(scan_end_offset_dist, float) and isinstance(x_start, float)
            and isinstance(x_end, float) and isinstance(y_start, float) and
            isinstance(y_end, float)):

            if scan_type == 'vector':
                tot_dist = math.sqrt((x_end - x_start)**2 + (y_end-y_start)**2)
                if tot_dist != 0:
                    y_prop = abs((y_end-y_start)/tot_dist)
                else:
                    y_prop = 1
            else:
                y_prop = 1

            if scan_type == 'vector' or (scan_type == 'grid' and step_axis == 'x'):
                if y_end < y_start:
                    scan_y_end = y_end - scan_end_offset_dist*y_prop
                else:
                    scan_y_end = y_end + scan_end_offset_dist*y_prop

                if (scan_y_end < self.settings['y_range'][0] or
                    scan_y_end > self.settings['y_range'][1]):
                    errors.append(('Final Y position plus (or minus) end '
                        'offset (between {} and {} {})'.format(
                        self.settings['y_range'][0], self.settings['y_range'][1],
                        pos_units)))
            else:
                scan_y_end = y_end


        if scan_type == 'grid':
            if isinstance(step_speed, float):
                if (step_speed <= self.settings['speed_lim'][0] or
                    step_speed > self.settings['speed_lim'][1]):
                    errors.append('Step speed (between {} and {} {})'.format(
                    self.settings['speed_lim'][0], self.settings['speed_lim'][1],
                    pos_units))

            if isinstance(step_acceleration, float):
                if (step_acceleration <= self.settings['acceleration_lim'][0] or
                    step_acceleration > self.settings['acceleration_lim'][1]):
                    errors.append('Step acceleration (between {} and {} {})'.format(
                    self.settings['acceleration_lim'][0],
                    self.settings['acceleration_lim'][1], pos_units))

        if isinstance(num_scans, int):
            if num_scans <= 0:
                errors.append('Number of scans (greater than 0)')

        if len(errors) > 0:
            valid = False

        if valid:
            try:
                (scan_length,
                    total_length,
                    time_per_scan,
                    return_time,
                    total_time) = self._calc_scan_params(x_start, x_end, y_start,
                    y_end, scan_speed, return_speed, scan_acceleration,
                    return_acceleration, scan_start_offset_dist, scan_end_offset_dist,
                    num_scans, scan_type, step_axis, step_size, step_speed,
                    step_acceleration, gridpoints)

            except Exception:
                # print(traceback.print_exc())
                valid = False
                errors.append('Error calculating scan parameters')

            try:
                return_vals = self._calc_exposure_params()

                if return_vals and len(return_vals[0])>0:
                    errors.extend(return_vals[0])

            except Exception:
                print(traceback.print_exc())
                valid = False


        if len(errors) > 0:
            valid = False
            scan_values = {}

            msg = 'The following field(s) have invalid values:'
            for err in errors:
                msg = msg + '\n- ' + err
            msg = msg + ('\n\nPlease correct these errors, then start the scan.')

            wx.CallAfter(wx.MessageBox, msg, 'Error in scan parameters',
                style=wx.OK|wx.ICON_ERROR)

        else:
            scan_values = {
                'x_start'               : x_start,
                'x_end'                 : x_end,
                'y_start'               : y_start,
                'y_end'                 : y_end,
                'scan_x_start'          : scan_x_start,
                'scan_x_end'            : scan_x_end,
                'scan_y_start'          : scan_y_start,
                'scan_y_end'            : scan_y_end,
                'scan_speed'            : scan_speed,
                'return_speed'          : return_speed,
                'scan_accel'            : scan_acceleration,
                'return_accel'          : return_acceleration,
                'num_scans'             : num_scans,
                'scan_start_offset_dist': scan_start_offset_dist,
                'scan_end_offset_dist'  : scan_end_offset_dist,
                'scan_length'           : scan_length,
                'total_length'          : total_length,
                'time_per_scan'         : time_per_scan,
                'return_time'           : return_time,
                'total_time'            : total_time,
                'motor_type'            : self.settings['motor_type'],
                'motor'                 : self.motor,
                'scan_type'             : scan_type,
                'step_axis'             : step_axis,
                'step_size'             : step_size,
                'step_speed'            : step_speed,
                'step_acceleration'     : step_acceleration,
                'gridpoints'            : gridpoints,
                'use_gridpoints'        : use_grid_from_file,
            }

            if self.settings['motor_type'] == 'Newport_XPS':
                scan_values['num_images'] = return_vals[1]
                scan_values['pco_step'] = return_vals[2]
                scan_values['pco_start'] = return_vals[3]
                scan_values['pco_end'] = return_vals[4]
                scan_values['vect_scan_speed'] = return_vals[5]
                scan_values['vect_scan_accel'] = return_vals[6]
                scan_values['vect_return_speed'] = return_vals[7]
                scan_values['vect_return_accel'] = return_vals[8]
                scan_values['x_pco_step'] = return_vals[9]
                scan_values['y_pco_step'] = return_vals[10]
                scan_values['x_pco_start'] = return_vals[11]
                scan_values['y_pco_start'] = return_vals[12]

                scan_values['motor_group_name'] = self.settings['motor_group_name']
                scan_values['motor_x_name'] = self.settings['motor_x_name']
                scan_values['motor_y_name'] = self.settings['motor_y_name']
                scan_values['pco_direction'] = pco_direction
                scan_values['pco_pulse_width'] = self.settings['pco_pulse_width']
                scan_values['pco_encoder_settle_t'] =  self.settings['pco_encoder_settle_t']

            logger.info(scan_values)

        return scan_values, valid

    def _calc_vector_params(self, x_start, x_end, y_start, y_end, scan_speed,
        return_speed, scan_acceleration, return_acceleration, scan_type,
        step_axis):

        if scan_type == 'vector':
            if x_start == x_end:
                scan_speed_y = scan_speed
                return_speed_y = return_speed
                scan_acceleration_y = scan_acceleration
                return_acceleration_y = return_acceleration

                scan_speed_x = 0
                return_speed_x = 0
                scan_acceleration_x = 0
                return_acceleration_x = 0

            elif y_start == y_end:
                scan_speed_x = scan_speed
                return_speed_x = return_speed
                scan_acceleration_x = scan_acceleration
                return_acceleration_x = return_acceleration

                scan_speed_y = 0
                return_speed_y = 0
                scan_acceleration_y = 0
                return_acceleration_y = 0

            else:
                tot_dist = ((x_end - x_start)**D(2) + (y_end-y_start)**D(2))**(D(0.5))
                x_prop = abs((x_end - x_start)/tot_dist)
                y_prop = abs((y_end-y_start)/tot_dist)

                scan_speed_x = scan_speed*x_prop
                return_speed_x = return_speed*x_prop
                scan_acceleration_x = scan_acceleration*x_prop
                return_acceleration_x = return_acceleration*x_prop

                scan_speed_y = scan_speed*y_prop
                return_speed_y = return_speed*y_prop
                scan_acceleration_y = scan_acceleration*y_prop
                return_acceleration_y = return_acceleration*y_prop

        else:
            if step_axis == 'x':
                scan_speed_y = scan_speed
                return_speed_y = return_speed
                scan_acceleration_y = scan_acceleration
                return_acceleration_y = return_acceleration

                scan_speed_x = 0
                return_speed_x = 0
                scan_acceleration_x = 0
                return_acceleration_x = 0

            elif step_axis == 'y':
                scan_speed_x = scan_speed
                return_speed_x = return_speed
                scan_acceleration_x = scan_acceleration
                return_acceleration_x = return_acceleration

                scan_speed_y = 0
                return_speed_y = 0
                scan_acceleration_y = 0
                return_acceleration_y = 0

        vect_scan_speed = (scan_speed_x, scan_speed_y)
        vect_scan_accel = (scan_acceleration_x, scan_acceleration_y)
        vect_return_speed = (return_speed_x, return_speed_y)
        vect_return_accel = (return_acceleration_x, return_acceleration_y)

        return vect_scan_speed, vect_scan_accel, vect_return_speed, vect_return_accel


    def _calc_pco_params(self, x_start, x_end, y_start, y_end, scan_speed,
        return_speed, scan_acceleration, return_acceleration, delta_t,
        scan_type, step_axis, encoder_resolution, encoder_precision):
        """ For Newport XPS controller with encoded stages"""

        (vect_scan_speed, vect_scan_accel,
            vect_return_speed, vect_return_accel) = self._calc_vector_params(x_start,
            x_end, y_start, y_end, scan_speed, return_speed, scan_acceleration,
            return_acceleration, scan_type, step_axis)

        x_pco_step = delta_t*D(vect_scan_speed[0])
        y_pco_step = delta_t*D(vect_scan_speed[1])

        if x_pco_step % encoder_resolution != 0:
            x_pco_step = x_pco_step + encoder_resolution/D('2') #Round up
            x_pco_step = self.round_to(x_pco_step, encoder_precision,
            encoder_resolution)

        if y_pco_step % encoder_resolution != 0:
            y_pco_step = y_pco_step + encoder_resolution/D('2') #Round up
            y_pco_step = self.round_to(x_pco_step, encoder_precision,
            encoder_resolution)

        return x_pco_step, y_pco_step, vect_scan_speed, vect_scan_accel, vect_return_speed, vect_return_accel

    def round_to(self, x, prec, base):
        # print(type(x))
        # print(type(prec))
        # print(type(base))
        # return round(float(base)*round(x/base), prec)
        return x.quantize(base)

    def update_params(self):
        self._param_change()

    def on_exit(self):
        if self.motor is not None:
            self.motor.disconnect()

class TRFlowPanel(wx.Panel):
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
    def __init__(self, settings, *args, **kwargs):
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

        super(TRFlowPanel, self).__init__(*args, **kwargs)
        logger.debug('Initializing TRFlowPanel')

        self.settings = settings

        if self.settings['mixer_type'] == 'chaotic':
            self.chaotic_mixer = True
        else:
            self.chaotic_mixer = False

        self._create_layout()
        self._init_connections()
        self._init_values()
        self._init_valves()
        self._init_pumps()
        self._init_flowmeters()

        if self.settings['simulated']:
            self.stop_simulation = threading.Event()
            self.sim_thread = threading.Thread(target=self._simulated_mode)
            self.sim_thread.daemon = True
            self.sim_thread.start()

    def _FromDIP(self, size):
        # This is a hack to provide easy back compatibility with wxpython < 4.1
        try:
            return self.FromDIP(size)
        except Exception:
            return size

    def _init_connections(self):
        self.pump_cmd_q = deque()
        self.pump_return_q = deque()
        self.pump_status_q = deque()
        self.pump_abort_event = threading.Event()
        self.pump_return_lock = threading.Lock()

        self.fm_cmd_q = deque()
        self.fm_return_q = deque()
        self.fm_status_q = deque()
        self.fm_abort_event = threading.Event()
        self.fm_return_lock = threading.Lock()

        self.valve_cmd_q = deque()
        self.valve_return_q = deque()
        self.valve_status_q = deque()
        self.valve_abort_event = threading.Event()
        self.valve_return_lock = threading.Lock()

        self.timeout_event = threading.Event()

        if self.settings['device_communication'] == 'local':
            self.pump_con = self.settings['pump_com_thread']
            self.pump_con.add_new_communication('pump_control',
                self.pump_cmd_q, self.pump_return_q,
                self.pump_status_q)

            self.fm_con = self.settings['fm_com_thread']
            self.fm_con.add_new_communication('fm_control',
                self.fm_cmd_q, self.fm_return_q,
                self.fm_status_q)

            self.valve_con = self.settings['valve_com_thread']
            self.valve_con.add_new_communication('valve_control',
                self.valve_cmd_q, self.valve_return_q,
                self.valve_status_q)

            self.local_devices = True

        else:
            pump_ip = self.settings['remote_pump_ip']
            pump_port = self.settings['remote_pump_port']
            self.pump_con = client.ControlClient(pump_ip, pump_port,
                self.pump_cmd_q, self.pump_return_q,
                self.pump_abort_event, self.timeout_event,
                name='PumpControlClient', status_queue=self.pump_status_q)

            fm_ip = self.settings['remote_fm_ip']
            fm_port = self.settings['remote_fm_port']
            self.fm_con = client.ControlClient(fm_ip, fm_port,
                self.fm_cmd_q, self.fm_return_q,
                self.fm_abort_event, self.timeout_event, name='FMControlClient',
                status_queue=self.fm_status_q)

            valve_ip = self.settings['remote_valve_ip']
            valve_port = self.settings['remote_valve_port']
            self.valve_con = client.ControlClient(valve_ip, valve_port,
                self.valve_cmd_q, self.valve_return_q,
                self.valve_abort_event, self.timeout_event,
                name='ValveControlClient', status_queue=self.valve_status_q)

            self.local_devices = False

            self.pump_con.start()
            self.fm_con.start()
            self.valve_con.start()

    def _init_values(self):
        self.valves = {}
        self.pumps = {}
        self.fms = {}

        self.error_dialog = None

        self.stop_valve_monitor = threading.Event()
        self.pause_valve_monitor = threading.Event()
        self.valve_monitor_thread = threading.Thread(target=self._monitor_valve_position)
        self.valve_monitor_thread.daemon = True

        self.stop_pump_monitor = threading.Event()
        self.pause_pump_monitor = threading.Event()
        self.pump_monitor_thread = threading.Thread(target=self._monitor_pump_status)
        self.pump_monitor_thread.daemon = True

        self.stop_fm_monitor = threading.Event()
        self.pause_fm_monitor = threading.Event()
        self.pause_fm_den_T_monitor = threading.Event()
        self.fm_monitor_thread = threading.Thread(target=self._monitor_fm_status)
        self.fm_monitor_thread.daemon = True

        self.valve_monitor_interval = 2
        self.pump_monitor_interval = 2
        self.fm_monitor_interval = 2
        self.fm_monitor_all_interval = 10

        self.pump_ret_lock = threading.Lock()
        self.valve_ret_lock = threading.Lock()
        self.fm_ret_lock = threading.Lock()

        #For communicating with exposure thread
        self.start_flow_event = threading.Event()
        self.stop_flow_event = threading.Event()
        self.autoinject_event = threading.Event()
        self.start_exposure_event = threading.Event()

    def _init_valves(self):

        valve_list = [
            ('injection_valve', self.inj_valve_positions),
            ('sample_valve', self.sample_valve_positions),
            ('buffer1_valve', self.buffer1_valve_positions),
            ('buffer2_valve', self.buffer2_valve_positions),
            ]

        for valves in valve_list:
            valve_basename = valves[0]
            valve_widgets = valves[1]
            valve_settings = self.settings[valve_basename]

            for i, widget in enumerate(valve_widgets):
                settings = valve_settings[i]

                name = settings['name']
                args = settings['args']
                args.insert(0, name)
                kwargs = settings['kwargs']

                widget.SetMin(1)
                widget.SetMax(int(kwargs['positions']))
                widget.SetName(name)

                connect_cmd = ('connect', args, kwargs)

                init = self._send_valvecmd(connect_cmd, response=True)

                if init is None or (not init and not self.timeout_event.is_set()):
                    logger.error('Failed to connect to the {}.'.format(name.replace('_', ' ')))

                    msg = ('Could not connect to the {}. Contact your beamline '
                        'scientist.'.format(name.replace('_', ' ')))

                    dialog = wx.MessageDialog(self, msg, 'Connection error',
                        style=wx.OK|wx.ICON_ERROR)
                    dialog.ShowModal()
                    dialog.Destroy()

                self.valves[name] = (valve_basename, name, args, kwargs)

        self.get_all_valve_positions()

        logger.info('Valve initializiation successful.')

        self.valve_monitor_thread.start()

    def _init_pumps(self):
        logger.info('Initializing pumps on startup')
        pump_list = [
            ('buffer1_pump', self.settings['buffer1_pump']),
            ('sample_pump', self.settings['sample_pump']),
            ('buffer2_pump', self.settings['buffer2_pump']),
            ]

        all_init = True
        failed_connections = []

        self.pump_names = defaultdict(list)

        for pump_data in pump_list:
            pump_type = pump_data[0]
            pumps = pump_data[1]

            for pump in pumps:
                name = pump['name']
                args = pump['args']
                kwargs = pump['kwargs']
                ctrl_args = pump['ctrl_args']

                args.insert(0, name)

                self.pump_names[pump_type].append(name)

                continuous = ctrl_args['continuous']

                if not continuous:
                    syringe = kwargs['syringe_id']
                    kwargs.update(copy.deepcopy(pumpcon.known_syringes[syringe]))

                connect_cmd = ['connect', args, kwargs]

                init = self._send_pumpcmd(connect_cmd, response=True)

                if init is None:
                    all_init = False
                else:
                    all_init = all_init and init

                self.pumps[name] = (pump_type, name, args, kwargs, ctrl_args)

                if init:
                    self.set_units(name, self.settings['flow_units'])
                    self.set_pump_status(name, 'Connected')
                    self.pump_panels[name].connected = True
                    self.pump_panels[name].set_max_pressure()
                    self.pump_panels[name].set_flow_accel()

                else:
                    failed_connections.append(name)

        if not all_init and not self.timeout_event.is_set():
            logger.error('Failed to connect to pumps: %s.',
                ' '.join(failed_connections))

            msg = ('Could not connect to pumps: {}. Contact your beamline '
                'scientist.'.format(' '.join(failed_connections)))

            dialog = wx.MessageDialog(self, msg, 'Connection error',
                style=wx.OK|wx.ICON_ERROR)
            dialog.ShowModal()
            dialog.Destroy()

        elif all_init:
            self.pump_monitor_thread.start()

            logger.info('Pump initializiation successful')

            self._on_flow_change(None)

    def _init_flowmeters(self):
        outlet_fm = self.settings['outlet_fm']
        self.outlet_fm_name = outlet_fm['name']

        logger.info('Initializing flow meters on startup')

        outlet_args = outlet_fm['args']
        outlet_args.insert(0, self.outlet_fm_name)
        outlet_kwargs = outlet_fm['kwargs']

        outlet_connect_cmd = ('connect', outlet_args, outlet_kwargs)

        self.fms[self.outlet_fm_name] = (self.outlet_fm_name, outlet_fm['args'],
            outlet_fm['kwargs'])

        try:
            outlet_init = self._send_fmcmd(outlet_connect_cmd, response=True)
        except Exception:
            outlet_init = False

        if (outlet_init is None or (not outlet_init
                    and not self.timeout_event.is_set())):
            logger.error('Failed to connect to the outlet flow meter.')

            msg = ('Could not connect to the TR-SAXS outlet flow meter. '
                'Contact your beamline scientist.')

            dialog = wx.MessageDialog(self, msg, 'Connection error',
                style=wx.OK|wx.ICON_ERROR)
            dialog.ShowModal()
            dialog.Destroy()

        if outlet_init:
            self._send_fmcmd(('set_units', (self.outlet_fm_name,
                self.settings['flow_units']), {}))

            if outlet_args[1] == 'BFS' or outlet_args[1] == 'Soft':
                ret = self._send_fmcmd(('get_density', (self.outlet_fm_name,),
                    {}), True)

                if ret is not None:
                    self._set_fm_values(self.outlet_fm_name, density=ret)

                ret = self._send_fmcmd(('get_temperature', (self.outlet_fm_name,),
                    {}), True)

                if ret is not None:
                    self._set_fm_values(self.outlet_fm_name, T=ret)

            ret = self._send_fmcmd(('get_flow_rate', (self.outlet_fm_name,),
                {}), True)

            if ret is not None:
                self._set_fm_values(self.outlet_fm_name, flow_rate=ret)

            logger.info('TR-SAXS flow meters initialization successful')

        else:
            self.stop_fm_monitor.set()

        self.fm_monitor_thread.start()

    def _create_layout(self):

        basic_flow_box_sizer = wx.StaticBoxSizer(wx.HORIZONTAL, self, 'Flow Controls')
        basic_flow_parent = basic_flow_box_sizer.GetStaticBox()

        if self.chaotic_mixer:
            self.total_flow = wx.TextCtrl(basic_flow_parent, value=self.settings['total_flow_rate'],
                style=wx.TE_PROCESS_ENTER, validator=utils.CharValidator('float_te'),
                size=self._FromDIP((60, -1)))
            self.dilution_ratio = wx.TextCtrl(basic_flow_parent, value=self.settings['dilution_ratio'],
                style=wx.TE_PROCESS_ENTER, validator=utils.CharValidator('float_te'),
                size=self._FromDIP((60, -1)))

            self.total_flow.Bind(wx.EVT_TEXT_ENTER, self._on_flow_change)
            self.total_flow.Bind(wx.EVT_KILL_FOCUS, self._on_flow_change)
            self.dilution_ratio.Bind(wx.EVT_TEXT_ENTER, self._on_flow_change)
            self.dilution_ratio.Bind(wx.EVT_KILL_FOCUS, self._on_flow_change)

            flow_sizer = wx.FlexGridSizer(cols=2, vgap=self._FromDIP(2), hgap=self._FromDIP(2))
            flow_sizer.Add(wx.StaticText(basic_flow_parent, label='Total flow rate [{}]:'
                ''.format(self.settings['flow_units'])), flag=wx.ALIGN_CENTER_VERTICAL)
            flow_sizer.Add(self.total_flow, flag=wx.ALIGN_CENTER_VERTICAL)
            flow_sizer.Add(wx.StaticText(basic_flow_parent, label='Dilution ratio:'),
                flag=wx.ALIGN_CENTER_VERTICAL)
            flow_sizer.Add(self.dilution_ratio, flag=wx.ALIGN_CENTER_VERTICAL)
        else:
            self.total_flow = wx.TextCtrl(basic_flow_parent, value=self.settings['total_flow_rate'],
                style=wx.TE_PROCESS_ENTER, validator=utils.CharValidator('float_te'),
                size=self._FromDIP((60, -1)))
            self.sample_ratio = wx.TextCtrl(basic_flow_parent, value=self.settings['sample_ratio'],
                style=wx.TE_PROCESS_ENTER, validator=utils.CharValidator('float_te'),
                size=self._FromDIP((60, -1)))
            self.sheath_ratio = wx.TextCtrl(basic_flow_parent, value=self.settings['sheath_ratio'],
                style=wx.TE_PROCESS_ENTER, validator=utils.CharValidator('float_te'),
                size=self._FromDIP((60, -1)))

            self.total_flow.Bind(wx.EVT_TEXT_ENTER, self._on_flow_change)
            self.total_flow.Bind(wx.EVT_KILL_FOCUS, self._on_flow_change)
            self.sample_ratio.Bind(wx.EVT_TEXT_ENTER, self._on_flow_change)
            self.sample_ratio.Bind(wx.EVT_KILL_FOCUS, self._on_flow_change)
            self.sheath_ratio.Bind(wx.EVT_TEXT_ENTER, self._on_flow_change)
            self.sheath_ratio.Bind(wx.EVT_KILL_FOCUS, self._on_flow_change)

            flow_sizer = wx.FlexGridSizer(cols=2, vgap=self._FromDIP(2), hgap=self._FromDIP(2))
            flow_sizer.Add(wx.StaticText(basic_flow_parent, label='Total flow rate [{}]:'
                ''.format(self.settings['flow_units'])), flag=wx.ALIGN_CENTER_VERTICAL)
            flow_sizer.Add(self.total_flow, flag=wx.ALIGN_CENTER_VERTICAL)
            flow_sizer.Add(wx.StaticText(basic_flow_parent, label='Sample/buffer ratio:'),
                flag=wx.ALIGN_CENTER_VERTICAL)
            flow_sizer.Add(self.sample_ratio, flag=wx.ALIGN_CENTER_VERTICAL)
            flow_sizer.Add(wx.StaticText(basic_flow_parent, label='Sheath/buffer ratio:'),
                flag=wx.ALIGN_CENTER_VERTICAL)
            flow_sizer.Add(self.sheath_ratio, flag=wx.ALIGN_CENTER_VERTICAL)

        start_all = wx.Button(basic_flow_parent, label='Start pumps')
        stop_all = wx.Button(basic_flow_parent, label='Stop pumps')

        start_all.Bind(wx.EVT_BUTTON, self._on_start_all)
        stop_all.Bind(wx.EVT_BUTTON, self._on_stop_all)

        all_continuous = True
        for pump in self.settings['sample_pump']:
            if not pump['ctrl_args']['continuous']:
                all_continuous = False
                break

        if all_continuous:
            for pump in self.settings['buffer1_pump']:
                if not pump['ctrl_args']['continuous']:
                    all_continuous = False
                    break

        if all_continuous:
            for pump in self.settings['buffer2_pump']:
                if not pump['ctrl_args']['continuous']:
                    all_continuous = False
                    break

        if not all_continuous:
            refill_all = wx.Button(basic_flow_parent, label = 'Refill pumps')

            refill_all.Bind(wx.EVT_BUTTON, self._on_refill_all)

            purge_all = wx.Button(basic_flow_parent, label='Purge pumps')
            purge_all.Bind(wx.EVT_BUTTON, self._on_purge_all)

        flow_button_sizer = wx.GridBagSizer(vgap=self._FromDIP(2), hgap=self._FromDIP(2))
        flow_button_sizer.Add(start_all, (0,0), flag=wx.ALIGN_CENTER_VERTICAL)
        flow_button_sizer.Add(stop_all, (0,1), flag=wx.ALIGN_CENTER_VERTICAL)

        if not all_continuous:
            flow_button_sizer.Add(refill_all, (1,0), span=(1,1),
                flag=wx.ALIGN_CENTER_HORIZONTAL|wx.ALIGN_CENTER_VERTICAL)
            flow_button_sizer.Add(purge_all, (1,1), span=(1,1),
                flag=wx.ALIGN_CENTER_HORIZONTAL|wx.ALIGN_CENTER_VERTICAL)

        basic_flow_box_sizer.Add(flow_sizer, flag=wx.ALL, border=self._FromDIP(2))
        basic_flow_box_sizer.Add(flow_button_sizer, flag=wx.ALL, border=self._FromDIP(2))


        info_box_sizer = wx.StaticBoxSizer(wx.VERTICAL, self, 'Flow Info')
        info_parent = info_box_sizer.GetStaticBox()

        self.max_flow_time = wx.StaticText(info_parent, size=self._FromDIP((60, -1)))
        # self.current_flow_time = wx.StaticText(info_parent, size=(60, -1))
        self.outlet_flow = wx.StaticText(info_parent)

        info_sizer = wx.FlexGridSizer(cols=2, hgap=self._FromDIP(2), vgap=self._FromDIP(2))
        # info_sizer.Add(wx.StaticText(info_parent, label='Cur. flow time [s]:'),
        #     flag=wx.ALIGN_CENTER_VERTICAL)
        # info_sizer.Add(self.current_flow_time, flag=wx.ALIGN_CENTER_VERTICAL)
        info_sizer.Add(wx.StaticText(info_parent, label='Max. flow time [s]:'),
            flag=wx.ALIGN_CENTER_VERTICAL)
        info_sizer.Add(self.max_flow_time, flag=wx.ALIGN_CENTER_VERTICAL)
        info_sizer.Add(wx.StaticText(info_parent, label='Outlet flow [{}]:'.format(self.settings['flow_units'])),
            flag=wx.ALIGN_CENTER_VERTICAL)
        info_sizer.Add(self.outlet_flow)

        info_box_sizer.Add(info_sizer)
        info_box_sizer.AddStretchSpacer(1)


        basic_ctrls = wx.BoxSizer(wx.HORIZONTAL)
        basic_ctrls.Add(basic_flow_box_sizer, flag=wx.RIGHT, border=self._FromDIP(2))
        basic_ctrls.Add(info_box_sizer, flag=wx.EXPAND)


        controls_pane = wx.CollapsiblePane(self, label="Advanced controls")
        controls_pane.Bind(wx.EVT_COLLAPSIBLEPANE_CHANGED, self._on_collapse)
        controls_win = controls_pane.GetPane()

        ctrl_parent = controls_win

        valve_box_sizer = wx.StaticBoxSizer(wx.VERTICAL, ctrl_parent, "Valves")
        valve_parent = valve_box_sizer.GetStaticBox()
        valve_sizer = wx.FlexGridSizer(cols=4, vgap=self._FromDIP(2), hgap=self._FromDIP(2))

        self.inj_valve_positions = []
        for valve in self.settings['injection_valve']:
            valve_pos = utils.IntSpinCtrl(valve_parent)
            valve_pos.Bind(utils.EVT_MY_SPIN, self._on_position_change)
            self.inj_valve_positions.append(valve_pos)

        self.sample_valve_positions = []
        for valve in self.settings['sample_valve']:
            valve_pos = utils.IntSpinCtrl(valve_parent)
            valve_pos.Bind(utils.EVT_MY_SPIN, self._on_position_change)
            self.sample_valve_positions.append(valve_pos)

        self.buffer1_valve_positions = []
        for valve in self.settings['buffer1_valve']:
            valve_pos = utils.IntSpinCtrl(valve_parent)
            valve_pos.Bind(utils.EVT_MY_SPIN, self._on_position_change)
            self.buffer1_valve_positions.append(valve_pos)

        self.buffer2_valve_positions = []
        for valve in self.settings['buffer2_valve']:
            valve_pos = utils.IntSpinCtrl(valve_parent)
            valve_pos.Bind(utils.EVT_MY_SPIN, self._on_position_change)
            self.buffer2_valve_positions.append(valve_pos)

        num_valves = max(len(self.inj_valve_positions),
            len(self.sample_valve_positions), len(self.buffer1_valve_positions),
            len(self.buffer2_valve_positions))

        if len(self.inj_valve_positions) > 0:
            valve_sizer.Add(wx.StaticText(valve_parent,
                label=self.settings['injection_valve_label']),
                flag=wx.ALIGN_CENTER_VERTICAL)
        if len(self.sample_valve_positions) > 0:
            valve_sizer.Add(wx.StaticText(valve_parent,
                label=self.settings['sample_valve_label']),
                flag=wx.ALIGN_CENTER_VERTICAL)
        if len(self.buffer1_valve_positions) > 0:
            valve_sizer.Add(wx.StaticText(valve_parent,
                label=self.settings['buffer1_valve_label']),
                flag=wx.ALIGN_CENTER_VERTICAL)
        if len(self.buffer2_valve_positions) > 0:
            valve_sizer.Add(wx.StaticText(valve_parent,
                label=self.settings['buffer2_valve_label']),
                flag=wx.ALIGN_CENTER_VERTICAL)


        for i in range(num_valves):
            if i < len(self.inj_valve_positions):
                valve_sizer.Add(self.inj_valve_positions[i],
                    flag=wx.ALIGN_CENTER_VERTICAL)
            else:
                valve_sizer.AddSpacer(self._FromDIP(1))

            if i < len(self.sample_valve_positions):
                valve_sizer.Add(self.sample_valve_positions[i],
                    flag=wx.ALIGN_CENTER_VERTICAL)
            else:
                valve_sizer.AddSpacer(self._FromDIP(1))

            if i < len(self.buffer1_valve_positions):
                valve_sizer.Add(self.buffer1_valve_positions[i],
                    flag=wx.ALIGN_CENTER_VERTICAL)
            else:
                valve_sizer.AddSpacer(self._FromDIP(1))

            if i < len(self.buffer2_valve_positions):
                valve_sizer.Add(self.buffer2_valve_positions[i],
                    flag=wx.ALIGN_CENTER_VERTICAL)
            else:
                valve_sizer.AddSpacer(self._FromDIP(1))

        self.set_valve_position = wx.CheckBox(valve_parent,
            label='Set valve positions on start/refill')
        self.set_valve_position.SetValue(self.settings['auto_set_valves'])

        valve_box_sizer.Add(valve_sizer, flag=wx.ALL, border=self._FromDIP(2))
        valve_box_sizer.Add(self.set_valve_position, flag=wx.TOP, border=self._FromDIP(2))
        valve_box_sizer.AddStretchSpacer(1)


        pump_box_sizer = wx.StaticBoxSizer(wx.VERTICAL, ctrl_parent, "Pumps")
        pump_parent = pump_box_sizer.GetStaticBox()
        pump_sizer = wx.BoxSizer(wx.HORIZONTAL)

        self.sample_pump_panels = []
        self.buffer1_pump_panels = []
        self.buffer2_pump_panels = []
        self.pump_panels = {}

        for pump in self.settings['sample_pump']:
            panel = TRPumpPanel(pump_parent, self, pump)

            pump_sizer.Add(panel, flag=wx.LEFT|wx.TOP|wx.BOTTOM,
                border=self._FromDIP(2))

            self.pump_panels[pump['name']] = panel

            self.sample_pump_panels.append(panel)

        for pump in self.settings['buffer1_pump']:
            panel = TRPumpPanel(pump_parent, self, pump)

            pump_sizer.Add(panel, flag=wx.LEFT|wx.TOP|wx.BOTTOM,
                border=self._FromDIP(2))

            self.pump_panels[pump['name']] = panel

            self.buffer1_pump_panels.append(panel)

        for pump in self.settings['buffer2_pump']:
            panel = TRPumpPanel(pump_parent, self, pump)

            pump_sizer.Add(panel, flag=wx.LEFT|wx.TOP|wx.BOTTOM,
                border=self._FromDIP(2))

            self.pump_panels[pump['name']] = panel

            self.buffer2_pump_panels.append(panel)

        pump_sizer.AddSpacer(self._FromDIP(2))

        pump_box_sizer.Add(pump_sizer)


        fm_box_sizer = wx.StaticBoxSizer(wx.VERTICAL, ctrl_parent, "Flow Meter")
        fm_parent = fm_box_sizer.GetStaticBox()


        self.outlet_density = wx.StaticText(fm_parent, size=self._FromDIP((60, -1)))
        self.outlet_T = wx.StaticText(fm_parent)

        fm_sizer = wx.FlexGridSizer(cols=3, vgap=self._FromDIP(2), hgap=self._FromDIP(2))
        fm_sizer.Add(wx.StaticText(fm_parent, label='Density:'),
            flag=wx.ALIGN_CENTER_VERTICAL)
        fm_sizer.Add(self.outlet_density, flag=wx.ALIGN_CENTER_VERTICAL)
        fm_sizer.Add(wx.StaticText(fm_parent, label='g/L'),
            flag=wx.ALIGN_CENTER_VERTICAL)
        fm_sizer.Add(wx.StaticText(fm_parent, label='Temperature:'),
            flag=wx.ALIGN_CENTER_VERTICAL)
        fm_sizer.Add(self.outlet_T, flag=wx.ALIGN_CENTER_VERTICAL)
        fm_sizer.Add(wx.StaticText(fm_parent, label='C'),
            flag=wx.ALIGN_CENTER_VERTICAL)

        fm_box_sizer.Add(fm_sizer, flag=wx.ALL, border=self._FromDIP(2))
        fm_box_sizer.AddStretchSpacer(1)

        exp_start_box_sizer = wx.StaticBoxSizer(wx.VERTICAL, ctrl_parent, "Exposure Start")
        exp_start_parent = exp_start_box_sizer.GetStaticBox()

        self.start_condition = wx.Choice(exp_start_parent, choices=['Immediately',
            'Fixed delay', 'At flow rate', 'None'])
        self.start_delay = wx.TextCtrl(exp_start_parent, size=self._FromDIP((60, -1)),
            value=self.settings['autostart_delay'],
            validator=utils.CharValidator('float'))
        self.start_flow = wx.TextCtrl(exp_start_parent, size=self._FromDIP((60, -1)),
            value=self.settings['autostart_flow'],
            validator=utils.CharValidator('float'))
        self.start_condition.SetStringSelection(self.settings['autostart'])

        exp_start_sizer = wx.FlexGridSizer(cols=2, vgap=self._FromDIP(2), hgap=self._FromDIP(2))
        exp_start_sizer.Add(wx.StaticText(exp_start_parent, label='Autostart exposure:'),
            flag=wx.ALIGN_CENTER_VERTICAL)
        exp_start_sizer.Add(self.start_condition, flag=wx.ALIGN_CENTER_VERTICAL)
        exp_start_sizer.Add(wx.StaticText(exp_start_parent,
            label='Start flow [{}]:'.format(self.settings['flow_units'])),
            flag=wx.ALIGN_CENTER_VERTICAL)
        exp_start_sizer.Add(self.start_flow, flag=wx.ALIGN_CENTER_VERTICAL)
        exp_start_sizer.Add(wx.StaticText(exp_start_parent, label='Start delay [s]:'),
            flag=wx.ALIGN_CENTER_VERTICAL)
        exp_start_sizer.Add(self.start_delay, flag=wx.ALIGN_CENTER_VERTICAL)

        exp_start_box_sizer.Add(exp_start_sizer, flag=wx.ALL, border=self._FromDIP(2))
        exp_start_box_sizer.AddStretchSpacer(1)




        inj_box_sizer = wx.StaticBoxSizer(wx.VERTICAL, ctrl_parent, "Injection")
        inj_parent = inj_box_sizer.GetStaticBox()

        self.autoinject = wx.Choice(inj_parent, choices=['Immediately',
            'After scan', 'None'])
        self.autoinject_scan = wx.TextCtrl(inj_parent, size=self._FromDIP((60, -1)),
            value=self.settings['autoinject_scan'],
            validator=utils.CharValidator('int'))
        self.autoinject.SetStringSelection(self.settings['autoinject'])

        inj_sizer = wx.FlexGridSizer(cols=2, vgap=self._FromDIP(2), hgap=self._FromDIP(2))
        inj_sizer.Add(wx.StaticText(inj_parent, label='Autoinject:'),
            flag=wx.ALIGN_CENTER_VERTICAL)
        inj_sizer.Add(self.autoinject, flag=wx.ALIGN_CENTER_VERTICAL)
        inj_sizer.Add(wx.StaticText(inj_parent, label='Start scan:'),
            flag=wx.ALIGN_CENTER_VERTICAL)
        inj_sizer.Add(self.autoinject_scan, flag=wx.ALIGN_CENTER_VERTICAL)

        inj_box_sizer.Add(inj_sizer, flag=wx.ALL, border=self._FromDIP(2))
        inj_box_sizer.AddStretchSpacer(1)

        sub_sizer1 = wx.BoxSizer(wx.HORIZONTAL)
        sub_sizer1.Add(valve_box_sizer, flag=wx.RIGHT|wx.EXPAND, border=self._FromDIP(2))
        sub_sizer1.Add(fm_box_sizer, flag=wx.RIGHT|wx.EXPAND, border=self._FromDIP(2))
        sub_sizer1.Add(exp_start_box_sizer, flag=wx.RIGHT|wx.EXPAND, border=self._FromDIP(2))
        sub_sizer1.Add(inj_box_sizer, flag=wx.EXPAND)

        ctrl_top_sizer = wx.BoxSizer(wx.VERTICAL)
        ctrl_top_sizer.Add(sub_sizer1, flag=wx.TOP|wx.BOTTOM, border=self._FromDIP(2))
        ctrl_top_sizer.Add(pump_box_sizer, flag=wx.BOTTOM, border=self._FromDIP(2))

        controls_win.SetSizer(ctrl_top_sizer)

        top_sizer = wx.BoxSizer(wx.VERTICAL)
        top_sizer.Add(basic_ctrls, flag=wx.ALL, border=self._FromDIP(2))
        top_sizer.Add(controls_pane, proportion=1, flag=wx.ALL, border=self._FromDIP(2))

        self.SetSizer(top_sizer)

    def _on_collapse(self, event):
        self.Layout()
        self.SendSizeEvent()

    def showMessageDialog(self, parent, msg, title, style):
        dialog = wx.MessageDialog(parent, msg, title, style=style)
        dialog.ShowModal()
        dialog.Destroy()

    def _on_flow_change(self, event):
        flow_rate = self.total_flow.GetValue()

        if self.chaotic_mixer:
            dilution = self.dilution_ratio.GetValue()
        else:
            sample_ratio = self.sample_ratio.GetValue()
            sheath_ratio = self.sheath_ratio.GetValue()

        errors = []

        try:
            flow_rate = float(flow_rate)
        except Exception:
            errors.append(('Total flow rate must be between 0 and {}'
                '.'.format(self.settings['max_flow'])))

        if isinstance(flow_rate, float):
            if flow_rate < 0 or flow_rate > self.settings['max_flow']:
                errors.append(('Total flow rate must be between 0 and {}'
                '.'.format(self.settings['max_flow'])))

        if self.chaotic_mixer:
            try:
                dilution = float(dilution)
            except Exception:
                errors.append(('Dilution ratio must be between 0 and {}'
                    '.'.format(self.settings['max_dilution'])))

            if isinstance(dilution, float):
                if dilution < 0 or dilution > self.settings['max_dilution']:
                    errors.append(('Total dilution ratio must be between 0 and {}'
                    '.'.format(self.settings['max_dilution'])))

        else:
            try:
                sample_ratio = float(sample_ratio)
            except Exception:
                errors.append(('Sample/buffer flow ratio must be a number.'))

            try:
                sheath_ratio = float(sheath_ratio)
            except Exception:
                errors.append(('Sheath/buffer flow ratio must be a number.'))

        if len(errors) > 0:
            msg = 'The following field(s) have invalid values:'
            for err in errors:
                msg = msg + '\n- ' + err
            msg = msg + ('\n\nPlease correct these errors.')

            self.total_flow.Unbind(wx.EVT_KILL_FOCUS)

            if self.chaotic_mixer:
                self.dilution_ratio.Unbind(wx.EVT_KILL_FOCUS)

            dialog = wx.MessageDialog(self, msg, 'Error in flow parameters',
                style=wx.OK|wx.ICON_ERROR)
            dialog.ShowModal()
            dialog.Destroy()

            self.total_flow.Bind(wx.EVT_KILL_FOCUS, self._on_flow_change)

            if self.chaotic_mixer:
                self.dilution_ratio.Bind(wx.EVT_KILL_FOCUS, self._on_flow_change)


        else:
            if self.chaotic_mixer:
                sample_flow = flow_rate/dilution
                buffer_flow = (flow_rate - sample_flow)/2.

                for name in self.pump_names['sample_pump']:
                    wx.CallAfter(self.set_pump_panel_flow_rate, name,
                        sample_flow/len(self.pump_names['sample_pump']))

                for name in self.pump_names['buffer1_pump']:
                    wx.CallAfter(self.set_pump_panel_flow_rate, name,
                        buffer_flow/len(self.pump_names['buffer1_pump']))

                for name in self.pump_names['buffer2_pump']:
                    wx.CallAfter(self.set_pump_panel_flow_rate, name,
                        buffer_flow/len(self.pump_names['buffer2_pump']))

            else:
                buffer_flow = flow_rate/(1+sample_ratio+sheath_ratio)
                sample_flow = buffer_flow*sample_ratio
                sheath_flow = buffer_flow*sheath_ratio

                #Account for two inlets:
                buffer_flow /= 2
                sheath_flow /= 2

                for name in self.pump_names['sample_pump']:
                    wx.CallAfter(self.set_pump_panel_flow_rate, name,
                        sample_flow/len(self.pump_names['sample_pump']))

                for name in self.pump_names['buffer1_pump']:
                    wx.CallAfter(self.set_pump_panel_flow_rate, name, buffer_flow)

                for name in self.pump_names['buffer2_pump']:
                    wx.CallAfter(self.set_pump_panel_flow_rate, name, sheath_flow)

            wx.CallAfter(self.update_flow_info)

    def _on_start_all(self, evt):
        wx.CallAfter(self.start_all)

    def start_all(self, force=False):
        logger.info('Starting all pumps')
        self.pause_valve_monitor.set()
        self.pause_pump_monitor.set()

        # success = self.stop_all()

        # if not success:
        #     return

        self.get_all_valve_positions()
        self.get_all_pump_status()

        total_flow = 0

        for name, pump_panel in self.pump_panels.items():
            pump_status = pump_panel.moving

            if not pump_panel.continuous_flow:
                pump_volume = float(pump_panel.get_status_volume())
            else:
                pump_volume = 1

            if pump_status and not force:
                msg = ('Cannot start all pumps when one or more pumps '
                    'are already moving.')
                wx.CallAfter(self.showMessageDialog, self, msg, 'Failed to start pumps',
                        wx.OK|wx.ICON_ERROR)

                self.pause_valve_monitor.clear()
                self.pause_pump_monitor.clear()
                return False

            if pump_volume <= 0:
                msg = ('Cannot start all pumps when one or more pumps '
                    'have no loaded volume.')
                wx.CallAfter(self.showMessageDialog, self, msg, 'Failed to start pumps',
                        wx.OK|wx.ICON_ERROR)

                self.pause_valve_monitor.clear()
                self.pause_pump_monitor.clear()
                return False

            flow_rate = pump_panel.get_target_flow_rate()

            if pump_panel.get_dual_syringe():
                flow_rate = flow_rate*2

            if (((name in self.pump_names['buffer_pump1'] and len(self.pump_names['buffer_pump1'])== 1)
                or (name in self.pump_names['buffer_pump2'] and len(self.pump_names['buffer_pump2'])== 1)
                ) and not self.chaotic_mixer):
                flow_rate = flow_rate*2


            total_flow += flow_rate

        if total_flow <= 0 or total_flow > self.settings['max_flow']:
            msg = ('Cannot start all pumps when total flow rate is {1} {0}. '
                'Total flow rate must be between 0 and {2} {0}.'.format(
                    self.settings['flow_units'], total_flow, self.settings['max_flow']))
            wx.CallAfter(self.showMessageDialog, self, msg, 'Failed to start pumps',
                    wx.OK|wx.ICON_ERROR)

            self.pause_valve_monitor.clear()
            self.pause_pump_monitor.clear()
            return False


        if self.set_valve_position.IsChecked():
            names = []
            positions = []

            for valve in self.valves:
                basename = self.valves[valve][0]

                if basename in self.settings['valve_start_positions']:
                    names.append(valve)
                    positions.append(self.settings['valve_start_positions'][basename])

            success = self.set_multiple_valve_positions(names, positions)
        else:
            success = True

        if not success:
            msg = ('Could not start pumps, failed to set valve positions '
                'correctly.')
            wx.CallAfter(self.showMessageDialog, self, msg, 'Failed to start pumps',
                        wx.OK|wx.ICON_ERROR)

            self.pause_valve_monitor.clear()
            self.pause_pump_monitor.clear()
            return False

        else:
            for pump_panel in self.pump_panels.values():
                pump_panel.set_pump_direction(True)
                start = pump_panel.start_pump()

                success = success and start

        if not success:
            msg = ('Pumps failed to start correctly.')
            wx.CallAfter(self.showMessageDialog, self, msg, 'Failed to start pumps',
                wx.OK|wx.ICON_ERROR)

            self.stop_all()
            self.pause_valve_monitor.clear()
            self.pause_pump_monitor.clear()
            return False

        self.pause_valve_monitor.clear()
        self.pause_pump_monitor.clear()
        return True

    def _on_stop_all(self, evt):
        wx.CallAfter(self.stop_all)

    def stop_all(self):
        success = self.stop_all_pumps()

        if not success:
            msg = ('Pumps failed to stop correctly.')
            wx.CallAfter(self.showMessageDialog, self, msg, 'Failed to stop pumps',
                wx.OK|wx.ICON_ERROR)

        return success

    def _on_refill_all(self, evt):
        wx.CallAfter(self.refill_all)

    def refill_all(self):
        logger.info('Refilling all pumps')
        self.pause_valve_monitor.set()
        self.pause_pump_monitor.set()

        self.get_all_valve_positions()
        self.get_all_pump_status()


        for pump_panel in self.pump_panels.values():
            pump_status = pump_panel.get_status()

            if pump_status != 'Connected' and pump_status != 'Done':
                msg = ('Cannot refill all pumps when one or more pumps '
                    'are already moving.')
                dialog = wx.MessageDialog(self, msg, 'Failed to refill pumps',
                    style=wx.OK|wx.ICON_ERROR)
                dialog.ShowModal()
                dialog.Destroy()

                logger.error('Failed to refill all pumps, one or more pumps is already moving.')

                self.pause_valve_monitor.clear()
                self.pause_pump_monitor.clear()
                return False

        if self.set_valve_position.IsChecked():
            names = []
            positions = []

            for valve in self.valves:
                basename = self.valves[valve][0]

                if basename in self.settings['valve_refill_positions']:
                    names.append(valve)
                    positions.append(self.settings['valve_refill_positions'][basename])

            success = self.set_multiple_valve_positions(names, positions)
        else:
            success = True

        if not success:
            msg = ('Could not refill pumps, failed to set valve positions '
                'correctly.')
            dialog = wx.MessageDialog(self, msg, 'Failed to refill pumps',
                style=wx.OK|wx.ICON_ERROR)
            dialog.ShowModal()
            dialog.Destroy()

            logger.error('Failed to refill all pumps, could not set valve positions.')

            self.pause_valve_monitor.clear()
            self.pause_pump_monitor.clear()
            return False

        else:
            for pump_panel in self.pump_panels.values():
                if pump_panel.pump_mode == 'syringe':
                    pump_panel.set_pump_direction(False)
                    success = pump_panel.run_pump()

        if not success:
            msg = ('Pumps failed to refill correctly.')
            dialog = wx.MessageDialog(self, msg, 'Failed to refill pumps',
                style=wx.OK|wx.ICON_ERROR)
            dialog.ShowModal()
            dialog.Destroy()

            logger.error('Failed to refill all pumps, not all pumps started correctly.')

            self.stop_all()
            self.pause_valve_monitor.clear()
            self.pause_pump_monitor.clear()
            return False

        self.pause_valve_monitor.clear()
        self.pause_pump_monitor.clear()
        return True

    def _on_purge_all(self, evt):
        wx.CallAfter(self.purge_all)

    def purge_all(self):
        logger.info('Purging all pumps')
        self.pause_valve_monitor.set()
        self.pause_pump_monitor.set()

        self.get_all_valve_positions()
        self.get_all_pump_status()


        for pump_panel in self.pump_panels.values():
            pump_status = pump_panel.get_status()

            if pump_status != 'Connected' and pump_status != 'Done':
                msg = ('Cannot purge all pumps when one or more pumps '
                    'are already moving.')
                dialog = wx.MessageDialog(self, msg, 'Failed to purge pumps',
                    style=wx.OK|wx.ICON_ERROR)
                dialog.ShowModal()
                dialog.Destroy()

                logger.error('Failed to purge all pumps, one or more pumps is already moving.')

                self.pause_valve_monitor.clear()
                self.pause_pump_monitor.clear()
                return False

        if self.set_valve_position.IsChecked():
            names = []
            positions = []

            for valve in self.valves:
                basename = self.valves[valve][0]

                if basename in self.settings['valve_purge_positions']:
                    names.append(valve)
                    positions.append(self.settings['valve_purge_positions'][basename])

            success = self.set_multiple_valve_positions(names, positions)
        else:
            success = True

        if not success:
            msg = ('Could not purge pumps, failed to set valve positions '
                'correctly.')
            dialog = wx.MessageDialog(self, msg, 'Failed to purge pumps',
                style=wx.OK|wx.ICON_ERROR)
            dialog.ShowModal()
            dialog.Destroy()

            logger.error('Failed to purge all pumps, could not set valve positions.')

            self.pause_valve_monitor.clear()
            self.pause_pump_monitor.clear()
            return False

        else:
            for pump_panel in self.pump_panels.values():
                if pump_panel.pump_mode == 'syringe':
                    pump_panel.set_pump_direction(True)
                    success = pump_panel.run_pump()

        if not success:
            msg = ('Pumps failed to purge correctly.')
            dialog = wx.MessageDialog(self, msg, 'Failed to purge pumps',
                style=wx.OK|wx.ICON_ERROR)
            dialog.ShowModal()
            dialog.Destroy()

            logger.error('Failed to purge all pumps, not all pumps started correctly.')

            self.stop_all()
            self.pause_valve_monitor.clear()
            self.pause_pump_monitor.clear()
            return False

        self.pause_valve_monitor.clear()
        self.pause_pump_monitor.clear()
        return True

    def update_flow_info(self):
        flow_times = []
        try:
            for pump_panel in self.pump_panels.values():
                if pump_panel.continuous_flow:
                    ft = -1
                else:
                    max_vol = pump_panel.get_max_volume()
                    flow_rate = pump_panel.get_target_flow_rate()
                    ft = max_vol/flow_rate

                flow_times.append(ft)

            flow_times = np.array(flow_times)

            if all(flow_times == -1):
                ft_label = 'N/A'
            else:
                ft_label ='{}'.format(round(min(flow_times[flow_times>-1])*60, 2))

            self.max_flow_time.SetLabel(ft_label)

            if self.settings['autostart_flow_ratio'] != 0:
                start_flow = float(self.total_flow.GetValue())*self.settings['autostart_flow_ratio']
                self.start_flow.SetValue(str(start_flow))

        except Exception:
            pass

    def _on_position_change(self, evt):
        widget = evt.GetEventObject()
        position = int(widget.GetValue())
        name = widget.GetName()

        self.change_valve_position(name, position)

    def change_valve_position(self, name, position):
        self.pause_valve_monitor.set()

        cmd = ('set_position', (name, position), {})

        ret = self._send_valvecmd(cmd, True)

        if ret is not None:
            if ret:
                logger.info('Set {} position to {}'.format(name, position))
            else:
                logger.error('Failed to set {} position'.format(name))
                msg = ('Failed to set {} position'.format(name))

                wx.CallAfter(self.showMessageDialog, self, msg, 'Set position failed',
                    wx.OK|wx.ICON_ERROR)
        else:
            logger.error('Failed to set {} position, no response from the '
                'server.'.format(name))
            msg = ('Failed to set {} position, no response from the '
                'server.'.format(name))

            wx.CallAfter(self.showMessageDialog, self, msg, 'Set position failed',
                    wx.OK|wx.ICON_ERROR)

        wx.CallLater(2000, self.pause_valve_monitor.clear)

    def get_valve_position(self, valve_name):
        cmd = ('get_position', (valve_name,), {})

        position = self._send_valvecmd(cmd, True)

        if position is not None:
            wx.CallAfter(self._set_valve_status, valve_name, position)

    def get_all_valve_positions(self):
        cmd = ('get_position_multi', ([valve for valve in self.valves],), {})

        ret = self._send_valvecmd(cmd, True)

        if ret is not None:
            for i in range(len(ret[0])):
                wx.CallAfter(self._set_valve_status, ret[0][i], ret[1][i])

    def set_multiple_valve_positions(self, valve_names, positions):
        cmd = ('set_position_multi', (valve_names, positions), {})
        ret = self._send_valvecmd(cmd, True)

        if ret is not None:
            success = all(ret[1])
        else:
            success = False

        return success

    def _set_valve_status(self, valve_name, position):
        try:
            position = int(position)

            valve = self.FindWindowByName(valve_name, self)

            if valve is not None:
                try:
                    cur_pos = valve.GetValue()
                    if cur_pos is None or cur_pos == 'None':
                        valve.SetValue(str(position))
                        log = True
                    elif int(cur_pos) != position:
                        valve.SetValue(str(position))
                        log = True
                    else:
                        log = False
                except Exception:
                    pass

            if log:
                logger.info('{} position changed to {}'.format(valve_name,
                    position))

        except Exception:
            traceback.print_exc()

    def _monitor_valve_position(self):
        logger.info('Starting continuous monitoring of valve positions')

        monitor_cmd = ('get_position_multi', ([valve for valve in self.valves],), {})

        while not self.stop_valve_monitor.is_set():
            start_time = time.time()
            if (not self.stop_valve_monitor.is_set() and
                not self.pause_valve_monitor.is_set()):
                ret = self._send_valvecmd(monitor_cmd, True)

                if (ret is not None and not self.pause_valve_monitor.is_set()):
                    for i, name in enumerate(ret[0]):
                        wx.CallAfter(self._set_valve_status, name, ret[1][i])

            while time.time() - start_time < self.valve_monitor_interval:
                time.sleep(0.1)

                if self.stop_valve_monitor.is_set():
                    break

        logger.info('Stopping continuous monitoring of valve positions')

    def start_pump(self, pump_name, start, fixed, dispense, vol, pump_mode,
            units, pump_panel):
        self.pause_pump_monitor.set()
        if start:
            if dispense:
                cmd_name = 'dispense'
            else:
                cmd_name = 'aspirate'

            if pump_mode == 'continuous':
                if not fixed:
                    cmd = ('start_flow', (pump_name,), {})
                else:
                    cmd = (cmd_name, (pump_name, vol), {'units':units})
            else:
                if not fixed:
                    cmd = ('{}_all'.format(cmd_name), (pump_name,), {})
                else:
                    cmd = (cmd_name, (pump_name, vol,), {'units':units})

            ret = self._send_pumpcmd(cmd, True)

            if ret is not None:
                success = ret
            else:
                success = False

        else:
            cmd = ('stop', (pump_name,), {})
            ret = self._send_pumpcmd(cmd, True)

            if ret is not None:
                success = ret
            else:
                success = False

        if success:
            self.get_pump_status(pump_name)

        self.pause_pump_monitor.clear()

        return success

    def stop_all_pumps(self):
        self.pause_pump_monitor.set()
        success = True
        names = [pump_name for pump_name in self.pumps]

        for pump_name in names:
            cmd = ('stop', (pump_name,), {})
            ret = self._send_pumpcmd(cmd, True)

            if ret is not None:
                success = ret and success
            else:
                success = False

        if success:
            self.get_all_pump_status()

        self.pause_pump_monitor.clear()

        return success

    def set_flow_rate(self, pump_name, flow_rate, pump_mode,
        dispense):
        if pump_mode == 'continuous':
            if dispense:
                mult = 1
            else:
                mult = -1
        else:
            mult = 1

        fr = float(flow_rate)*mult

        cmd = ('set_flow_rate', (pump_name, fr), {})

        self._send_pumpcmd(cmd)

        return True

    def set_refill_rate(self, pump_name, flow_rate, pump_mode,
        dispense):

        fr = float(flow_rate)

        cmd = ('set_refill_rate', (pump_name, fr), {})

        self._send_pumpcmd(cmd)

        return True

    def set_flow_accel(self, pump_name, accel):
        accel = float(accel)

        cmd = ('set_flow_accel', (pump_name, accel), {})

        self._send_pumpcmd(cmd)

        return True

    def set_max_pressure(self, pump_name, val):
        val = float(val)

        cmd = ('set_max_pressure', (pump_name, val), {})

        self._send_pumpcmd(cmd)

        return True

    def set_pump_panel_flow_rate(self, pump_name, flow_rate):
        pump_panel = self.pump_panels[pump_name]
        if pump_panel.get_dual_syringe():
            flow_rate = flow_rate/2.
        pump_panel.change_flowrate(flow_rate=flow_rate)

    def set_pump_panel_refill_rate(self, pump_name, flow_rate):
        pump_panel = self.pump_panels[pump_name]
        pump_panel.change_flowrate(refill_rate=flow_rate)

    def set_units(self, pump_name, units):
        cmd = ('set_units', (pump_name, units), {})
        self._send_pumpcmd(cmd)

    def set_pump_cal(self, pump_name, vals):
        cmd = ('set_pump_cal', (pump_name,), vals)
        self._send_pumpcmd(cmd)

        wx.CallAfter(self.update_flow_info)

    def set_pump_dual_syringe_type(self, pump_name, dual_syringe):
        cmd = ('set_pump_dual_syringe', (pump_name, dual_syringe), {})
        self._send_pumpcmd(cmd)
        wx.CallAfter(self._on_flow_change, None)

    def set_pump_volume(self, pump_name, volume):
        cmd = ('set_volume', (pump_name, volume), {})
        self._send_pumpcmd(cmd)

    def get_pump_status(self, pump_name):
        cmd = ('get_status', (pump_name,), {})

        ret = self._send_pumpcmd(cmd, True)

        if ret is not None:
            status_dict = ret
            self._set_pump_status(pump_name, status_dict)

    def get_all_pump_status(self):
        names = [pump_name for pump_name in self.pumps]
        cmd = ('get_status_multi', (names,), {})

        ret = self._send_pumpcmd(cmd, True)

        if ret is not None:
            for i, pump_name in enumerate(ret[0]):
                status_dict = ret[1][i]
                self._set_pump_status(pump_name, status_dict)

    def _set_pump_status(self, pump_name, status_dict):
        self.set_pump_moving(pump_name, status_dict['is_moving'])
        self.set_pump_status_direction(pump_name, status_dict['flow_dir'])
        self.set_pump_status_volume(pump_name, status_dict['volume'])
        self.set_pump_status_flow_rate(pump_name, status_dict['flow_rate'])
        self.set_pump_status_refill_rate(pump_name, status_dict['refill_rate'])
        self.set_pump_status_pressure(pump_name, status_dict['pressure'])
        self.show_pump_faults(pump_name, status_dict['faults'])
        self.set_pump_status_syringe(pump_name, status_dict['syringe_id'])

    def set_pump_status(self, pump_name, status):
        if status != self.pump_panels[pump_name].get_status():
           self.pump_panels[pump_name].set_status(status)

    def set_pump_status_direction(self, pump_name, val):
        if val is not None and val != self.pump_panels[pump_name].get_pump_direction():
            self.pump_panels[pump_name].set_status_direction(val)

    def set_pump_status_volume(self, pump_name, val):
        if (val is not None
            and round(float(val), 3) != float(self.pump_panels[pump_name].get_status_volume())):
            self.pump_panels[pump_name].set_status_volume(val)

    def set_pump_status_flow_rate(self, pump_name, val):
        if val is not None:
            self.pump_panels[pump_name].set_status_flow_rate(val)

    def set_pump_status_refill_rate(self, pump_name, val):
        if val is not None:
            self.pump_panels[pump_name].set_status_refill_rate(val)

    def set_pump_status_pressure(self, pump_name, val):
        if (val is not None and
            round(float(val), 3) != float(self.pump_panels[pump_name].get_status_pressure())):
            self.pump_panels[pump_name].set_status_pressure(val)

    def set_pump_moving(self, pump_name, moving):
        if moving != self.pump_panels[pump_name].moving:
            self.pump_panels[pump_name].set_moving(moving)

    def show_pump_faults(self, pump_name, faults):
        if faults['Fault']:
            msg = ('The following faults were detected in the {}:'.format(
                pump_name.replace('_', ' ')))

            for key in faults:
                if key != 'Fault' and faults[key]:
                    msg += '\n- {}'.format(key)

            wx.CallAfter(self.pump_panels[pump_name].show_faults_dialog, msg)

    def set_pump_status_syringe(self, pump_name, syringe_id):
        if syringe_id is not None:
            self.pump_panels[pump_name].set_status_syringe_id(syringe_id)

    def _monitor_pump_status(self):
        logger.info('Starting continuous monitoring of pump status')

        monitor_cmd = ('get_status_multi', ([pump for pump in self.pumps],), {})

        while not self.stop_pump_monitor.is_set():
            start_time = time.time()
            if (not self.stop_pump_monitor.is_set() and
                not self.pause_pump_monitor.is_set()):
                self.get_all_pump_status()

            while time.time() - start_time < self.pump_monitor_interval:
                time.sleep(0.1)

                if self.stop_pump_monitor.is_set():
                    break

        logger.info('Stopping continuous monitoring of pump status')

    def _monitor_fm_status(self):
        logger.info('Starting continuous monitoring of flow rate')

        flow_cmd = ('get_fr_multi', ([fm for fm in self.fms],), {})
        all_cmd = ('get_all_multi', ([fm for fm in self.fms],), {})

        monitor_all_time = time.time()

        while not self.stop_fm_monitor.is_set():
            start_time = time.time()
            if (not self.stop_fm_monitor.is_set() and
                not self.pause_fm_monitor.is_set()):

                if (time.time()-monitor_all_time < self.fm_monitor_all_interval
                    or self.pause_fm_den_T_monitor.is_set()):
                    ret = self._send_fmcmd(flow_cmd, True)

                    if (ret is not None and not self.pause_fm_monitor.is_set()):

                        for i, name in enumerate(ret[0]):
                            flow_rate = ret[1][i]
                            wx.CallAfter(self._set_fm_values, name,
                                flow_rate=flow_rate)
                else:
                    ret = self._send_fmcmd(all_cmd, True)

                    if (ret is not None and not self.pause_fm_monitor.is_set()):

                        for i, name in enumerate(ret[0]):
                            flow_rate = ret[1][i][0]
                            density = ret[1][i][1]
                            T = ret[1][i][2]
                            wx.CallAfter(self._set_fm_values, name,
                                flow_rate=flow_rate, density=density, T=T)

                    monitor_all_time = time.time()

            while time.time() - start_time < self.fm_monitor_interval:
                time.sleep(0.1)

                if self.stop_fm_monitor.is_set():
                    break

        logger.info('Stopping continuous monitoring of flow rate')

    def _set_fm_values(self, fm_name, flow_rate=None, density=None, T=None):
        if fm_name == self.outlet_fm_name:
            rate_ctrl = self.outlet_flow
            density_ctrl = self.outlet_density
            T_ctrl = self.outlet_T

        if flow_rate is not None:
            try:
                flow_rate = round(float(flow_rate), 3)
                if float(rate_ctrl.GetLabel()) != flow_rate:
                    rate_ctrl.SetLabel('{}'.format(flow_rate))
            except Exception:
                rate_ctrl.SetLabel('{}'.format(flow_rate))

        if density is not None:
            try:
                density = round(float(density), 2)
                if float(density_ctrl.GetLabel()) != density:
                    density_ctrl.SetLabel('{}'.format(density))
            except Exception:
                density_ctrl.SetLabel('{}'.format(density))

        if T is not None:
            try:
                T = round(float(T), 2)
                if float(T_ctrl).GetLabel() != T:
                    T_ctrl.SetLabel('{}'.format(T))
            except Exception:
                T_ctrl.SetLabel('{}'.format(T))

    def get_flow_values(self):
        valid = True

        errors = []
        warnings = []

        self.pause_valve_monitor.set()
        self.pause_pump_monitor.set()

        self.get_all_valve_positions()
        self.get_all_pump_status()

        start_condition = self.start_condition.GetStringSelection()
        start_delay = self.start_delay.GetValue()
        start_flow = self.start_flow.GetValue()
        autoinject = self.autoinject.GetStringSelection()
        autoinject_scan = self.autoinject_scan.GetValue()

        total_fr = 0

        for pump_name, pump_panel in self.pump_panels.items():
            flow_rate = pump_panel.get_target_flow_rate()

            try:
                flow_rate = float(flow_rate)
            except Exception:
                errors.append('Pump "{}" flow rate (greater than 0)'.format(pump_name))

            if isinstance(flow_rate, float):
                if flow_rate <= 0:
                    errors.append('Pump "{}" flow rate (greater than 0)'.format(pump_name))
                else:
                    total_fr = total_fr + flow_rate


        if start_condition == 'Fixed delay':
            try:
                start_delay = float(start_delay)
            except Exception:
                errors.append('Starting delay time (greater than 0)')

            if isinstance(start_delay, float):
                if start_delay < 0:
                    errors.append('Starting delay time (greater than 0)')

        if start_condition == 'At flow rate':
            try:
                float(start_flow)
            except Exception:
                errors.append('Starting flow rate (between 0 and {} {}'
                    ')'.format(total_fr, self.settings['flow_units']))

            if isinstance(start_flow, float):
                if start_flow < 0 or start_flow > total_fr:
                    errors.append('Starting flow rate (between 0 and {} {}'
                    ')'.format(total_fr, self.settings['flow_units']))

        if start_condition != 'None':
            for pump_name, pump_panel in self.pump_panels.items():
                pump_status = pump_panel.get_status()

                if pump_status != 'Connected' and pump_status != 'Done':
                    warnings.append(('Pump {} is moving. Usually pumps are '
                        'stopped before starting exposure. Flow rates may '
                        'not be the expected values.').format(pump_name))

            for pump_name, pump_panel in self.pump_panels.items():
                pump_volume = float(pump_panel.get_status_volume())

                if pump_volume <= 0 and not pump_panel.continuous_flow:
                    errors.append(('Pump {} has loaded volume <= 0').format(pump_name))

        if autoinject == 'After scan':
            try:
                autoinject_scan = int(autoinject_scan)
            except Exception:
                errors.append('Autoinject scan number must an integer >0')

            if isinstance(autoinject_scan, int):
                if autoinject_scan < 1:
                    errors.append('Autoinject scan number must an integer >0')

        if len(warnings) > 0:
            valid = False

            msg = 'The following warning(s) were found:'
            for warn in warnings:
                msg = msg + '\n- ' + warn
            msg = msg + ('\n\nDo you want to continue?')

            dlg = wx.MessageDialog(self, msg, "Warning in scan parameters",
                style=wx.ICON_QUESTION|wx.YES_NO)
            proceed = dlg.ShowModal()
            dlg.Destroy()

            if proceed == wx.ID_YES:
                valid = True

        if len(errors) > 0:
            valid = False

            msg = 'The following field(s) have invalid values:'
            for err in errors:
                msg = msg + '\n- ' + err
            msg = msg + ('\n\nPlease correct these errors, then start the scan.')

            wx.CallAfter(wx.MessageBox, msg, 'Error in flow parameters',
                style=wx.OK|wx.ICON_ERROR)

        if valid:
            flow_values = {
                'start_condition'   : start_condition.lower().replace(' ', '_'),
                'start_delay'       : start_delay,
                'start_flow'        : start_flow,
                'autoinject'        : autoinject.lower().replace(' ', '_'),
                'autoinject_scan'   : autoinject_scan,
                'start_flow_event'  : self.start_flow_event,
                'stop_flow_event'   : self.stop_flow_event,
                'autoinject_event'  : self.autoinject_event,
                'start_exp_event'   : self.start_exposure_event,
            }
        else:
            flow_values = {}


        self.pause_valve_monitor.clear()
        self.pause_pump_monitor.clear()

        return flow_values, valid

    def metadata(self):
        metadata = OrderedDict()

        flow_units = self.settings['flow_units']


        try:
            start_condition = self.start_condition.GetStringSelection()
            start_delay = self.start_delay.GetValue()
            start_flow = self.start_flow.GetValue()
            autoinject = self.autoinject.GetStringSelection()
            autoinject_scan = self.autoinject_scan.GetValue()

            total_fr = 0

            for pump_name, pump_panel in self.pump_panels.items():
                flow_rate = float(pump_panel.get_target_flow_rate())

                if pump_panel.get_dual_syringe():
                    flow_rate = flow_rate*2

                total_fr = total_fr + flow_rate

                if pump_name == self.settings['sample_pump'][0]['name']:
                    sample_fr = flow_rate
                elif pump_name == self.settings['buffer1_pump'][0]['name']:
                    buffer1_fr = flow_rate
                elif pump_name == self.settings['buffer2_pump'][0]['name']:
                    buffer2_fr = flow_rate

            metadata['Total flow rate [{}]:'.format(flow_units)] = total_fr

            if self.chaotic_mixer:
                metadata['Dilution ratio:'] = 1./(sample_fr/total_fr)
            else:
                metadata['Sample/buffer ratio:'] = sample_fr/buffer1_fr
                metadata['Sheath/buffer ratio:'] = buffer2_fr/buffer1_fr

            metadata['Sample flow rate [{}]:'.format(flow_units)] = sample_fr

            if self.chaotic_mixer:
                metadata['Buffer 1 flow rate [{}]:'.format(flow_units)] = buffer1_fr
                metadata['Buffer 2 flow rate [{}]:'.format(flow_units)] = buffer2_fr

            else:
                metadata['Buffer flow rate [{}]:'.format(flow_units)] = buffer1_fr
                metadata['Sheath flow rate [{}]:'.format(flow_units)] = buffer2_fr

            metadata['Exposure start setting:'] = start_condition
            if start_condition == 'Fixed delay':
                metadata['Exposure start delay [s]:'] = float(start_delay)
            elif start_condition == 'At flow rate':
                metadata['Exposure start flow rate [{}]:'.format(flow_units)] = float(start_flow)
            metadata['Autoinject start setting:'] = autoinject
            if autoinject == 'After scan':
                metadata['Autoinject after scan:'] = int(autoinject_scan)

        except Exception:
            traceback.print_exc()

        return metadata

    def prepare_for_exposure(self, settings):
        logger.info('Preparing flow controls for exposure')
        self.start_flow_event.clear()
        self.stop_flow_event.clear()
        self.autoinject_event.clear()
        self.start_exposure_event.clear()

        self.exp_thread = threading.Thread(target=self._start_flow_and_exposure, args=(settings,))
        self.exp_thread.daemon = True
        self.exp_thread.start()

    def _start_flow_and_exposure(self, settings):
        start_condition = settings['start_condition']
        autoinject = settings['autoinject']
        autoinject_valve_position = self.settings['autoinject_valve_pos']
        start_delay = settings['start_delay']
        start_flow_rate = settings['start_flow']

        exp_panel = wx.FindWindowByName('exposure')

        success = True
        if self.stop_flow_event.is_set():
            success = False

        if success and start_condition != 'none':
            while not self.start_flow_event.is_set():
                time.sleep(0.01)
                if self.stop_flow_event.is_set():
                    success = False
                    break

            if success:
                success = self.start_all(True)
                start_time = time.time()

                if not success:
                    wx.CallAfter(exp_panel.stop_exp)

        if success and start_condition == 'immediately':
            self.start_exposure_event.set()

        if success and autoinject == 'immediately':
            success = self.inject_sample(autoinject_valve_position)

            if not success:
                wx.CallAfter(exp_panel.stop_exp)

        if success and start_condition == 'fixed_delay':
            logger.info('Waiting {} s to start exposure'.format(start_delay))

            while time.time()-start_time < start_delay:
                time.sleep(0.001)

                if self.stop_flow_event.is_set():
                    success = False
                    break

            if success:
                self.start_exposure_event.set()

        if success and start_condition == 'at_flow_rate':
            logger.info(('Waiting for flow rate to reach {} {} to start '
                'exposure'.format(start_flow_rate, self.settings['flow_units'])))

            success = self.wait_for_flow(start_flow_rate)

            if success:
                self.start_exposure_event.set()

        if success and autoinject == 'after_scan':
            while not self.autoinject_event.is_set():
                time.sleep(0.001)

                if self.stop_flow_event.is_set():
                    success = False
                    break

            if success:
                success = self.inject_sample(autoinject_valve_position)

                if not success:
                    wx.CallAfter(exp_panel.stop_exp)

        self.pause_valve_monitor.clear()
        self.pause_pump_monitor.clear()

    def inject_sample(self, valve_position):
        injection_valves = []

        for valve in self.valves:
            if self.valves[valve][0] == 'injection_valve':
                injection_valves.append(valve)

        for i in range(len(self.inj_valve_positions)):
            valve_name = injection_valves[i]
            cmd = ('set_position', (valve_name, valve_position), {})
            ret = self._send_valvecmd(cmd, True)

            success = True

            if ret is not None:
                if ret:
                    logger.info('Injection valve {} switched to inject '
                        'position'.format(i))
                else:
                    success = False
                    logger.error('Injection valve {} failed to switch to '
                        'inject position'.format(i))
                    msg = ('Failed to inject sample')
                    wx.CallAfter(self.showMessageDialog, self, msg, 'Injection failed',
                        wx.OK|wx.ICON_ERROR)

        return success

    def wait_for_flow(self, target_flow_rate):
        self.pause_fm_monitor.set()
        flow_cmd = ('get_flow_rate', (self.outlet_fm_name,), {})
        flow_rate = 0
        target_flow_rate = float(target_flow_rate)
        success = True

        start_time = time.time()

        while flow_rate < target_flow_rate:
            ret = self._send_fmcmd(flow_cmd, True)
            if ret is not None:
                flow_rate = float(ret)

            if self.stop_flow_event.is_set():
                success = False
                break

            if time.time() - start_time > self.fm_monitor_interval:
                wx.CallAfter(self._set_fm_values, self.outlet_fm_name,
                    flow_rate=flow_rate)
                start_time = time.time()

        self.pause_fm_monitor.clear()

        return success

    def _send_valvecmd(self, cmd, response=False):
        ret_val = None

        if not self.timeout_event.is_set():
            self.valve_status_q.clear() #Not using this status q for now

            ret_val = utils.send_cmd(cmd, self.valve_cmd_q, self.valve_return_q,
                self.timeout_event, self.valve_return_lock, not self.local_devices,
                'valve', response)

        else:
            msg = ('No connection to the flow control server. '
                'Contact your beamline scientist.')

            wx.CallAfter(self._show_error_dialog, msg, 'Connection error')

            self.stop_valve_monitor.set()


        return ret_val

    def _send_pumpcmd(self, cmd, response=False):
        ret_val = None

        if not self.timeout_event.is_set():
            self.pump_status_q.clear() #Not using this status q for now

            ret_val = utils.send_cmd(cmd, self.pump_cmd_q, self.pump_return_q,
                self.timeout_event, self.pump_return_lock, not self.local_devices,
                'pump', response)

        else:
            msg = ('No connection to the flow control server. '
                'Contact your beamline scientist.')

            wx.CallAfter(self._show_error_dialog, msg, 'Connection error')

            self.stop_pump_monitor.set()


        return ret_val

    def _send_fmcmd(self, cmd, response=False):
        """
        Sends commands to the pump using the ``fm_cmd_q`` that was given
        to :py:class:`FlowMeterCommThread`.

        :param str cmd: The command to send, matching the command in the
            :py:class:`FlowMeterCommThread` ``_commands`` dictionary.
        """
        ret_val = None

        if not self.timeout_event.is_set():
            self.fm_status_q.clear() #Don't use the status q for now

            ret_val = utils.send_cmd(cmd, self.fm_cmd_q, self.fm_return_q,
                self.timeout_event, self.fm_return_lock, not self.local_devices,
                'fm', response)

        else:
            msg = ('No connection to the flow control server. '
                'Contact your beamline scientist.')

            wx.CallAfter(self._show_error_dialog, msg, 'Connection error')

            self.stop_fm_monitor.set()

        return ret_val

    def _show_error_dialog(self, msg, title):
        if self.error_dialog is None:
            self.error_dialog = utils.WarningMessage(self, msg, title,
                self._on_close_error_dialog)
            self.error_dialog.Show()

    def _on_close_error_dialog(self):
        self.error_dialog = None

    def _simulated_mode(self):
        valve_start_positions = self.settings['valve_start_positions']
        valves_in_pos = {'sample_valve': (False, 1),
            'buffer1_valve' : (False, 1),
            'buffer2_valve' : (False, 1),
            }

        valve_list = [
            ('injection_valve', self.inj_valve_positions),
            ('sample_valve', self.sample_valve_positions),
            ('buffer1_valve', self.buffer1_valve_positions),
            ('buffer2_valve', self.buffer2_valve_positions),
            ]

        previous_flow = 0
        target_flow = 0
        fct = time.time()
        rise_tau=5
        fall_tau = 5

        while not self.stop_simulation.is_set():
            total_flow = 0

            for valve_type, valve_widgets in valve_list:

                if valve_type != 'injection_valve':
                    start_pos = valve_start_positions[valve_type]

                    in_pos_count = 0

                    for valve in valve_widgets:
                        current_pos = valve.GetValue()

                        try:
                            current_pos = int(current_pos)
                        except Exception:
                            current_pos = 0

                        if current_pos == start_pos:
                            in_pos_count = in_pos_count + 1.

                    if valve_type in valves_in_pos:
                        if len(valve_widgets) > 0:
                            if in_pos_count > 0:
                                in_pos = True
                            else:
                                in_pos = False

                            fraction = in_pos_count/len(valve_widgets)

                            valves_in_pos[valve_type] = (in_pos, fraction)

                        else:
                            fraction = 1
                            valves_in_pos[valve_type] = (True, fraction)

            for valve, in_pos_vals in valves_in_pos.items():
                if valve == 'sample_valve':
                    pump_name = 'Sample'
                elif valve == 'buffer1_valve':
                    if self.chaotic_mixer:
                        pump_name = 'Buffer 1'
                    else:
                        pump_name = 'Buffer'
                elif valve == 'buffer2_valve':
                    if self.chaotic_mixer:
                        pump_name = 'Buffer 2'
                    else:
                        pump_name = 'Sheath'
                else:
                    pump_name = None

                in_pos = in_pos_vals[0]
                fraction = in_pos_vals[1]

                if pump_name is not None and in_pos and pump_name in self.pump_panels:
                    flow_rate = self.pump_panels[pump_name].get_status_flow_rate()
                    is_moving = self.pump_panels[pump_name].get_moving()
                    pump_direction = self.pump_panels[pump_name].get_pump_direction()
                    dual_syringe = self.pump_panels[pump_name].get_dual_syringe()

                    if pump_direction == 'Aspirate':
                        flow_rate = -flow_rate
                    elif pump_direction == 'Dispense' and dual_syringe:
                        flow_rate = flow_rate*2.

                    flow_rate = flow_rate*fraction

                    if is_moving:
                       total_flow = total_flow + flow_rate


            if target_flow == total_flow:
                ct = time.time()
                if previous_flow < target_flow:
                    current_flow = (target_flow - previous_flow)*(1-np.exp(-(ct-fct)/rise_tau)) + previous_flow
                else:
                    current_flow = (target_flow - previous_flow)*(1-np.exp(-(ct-fct)/fall_tau)) + previous_flow

                if (target_flow != 0 and ((previous_flow < target_flow
                    and current_flow >= 0.99*target_flow)
                    or (previous_flow > target_flow and
                    current_flow <= 1.01*target_flow))):
                    current_flow = target_flow

                elif target_flow == 0:
                    if current_flow <= 0.01*previous_flow:
                        current_flow = target_flow

            else:
                previous_flow = current_flow
                target_flow = total_flow
                fct = time.time()
                ct = time.time()

                if previous_flow < target_flow:
                    current_flow = (target_flow - previous_flow)*(1-np.exp(-(ct-fct)/rise_tau)) + previous_flow
                else:
                    current_flow = (target_flow - previous_flow)*(1-np.exp(-(ct-fct)/fall_tau)) + previous_flow

                if (target_flow != 0 and ((previous_flow < target_flow
                    and current_flow >= 0.99*target_flow)
                    or (previous_flow > target_flow and
                    current_flow <= 1.01*target_flow))):
                    current_flow = target_flow

                elif target_flow == 0:
                    if current_flow <= 0.01*previous_flow:
                        current_flow = target_flow

            cmd = ('set_flow_rate', (self.outlet_fm_name, current_flow), {})
            self._send_fmcmd(cmd, True)

            time.sleep(0.1)

    def on_exit(self):
        logger.debug('Closing all device connections')

        self.stop_valve_monitor.set()
        self.stop_pump_monitor.set()
        self.stop_fm_monitor.set()

        try:
            self.valve_monitor_thread.join(5)
            self.pump_monitor_thread.join(5)
            self.fm_monitor_thread.join(5)
        except Exception:
            pass

        if self.settings['simulated']:
            self.stop_simulation.set()
            self.sim_thread.join(5)

        self.valve_con.stop()
        self.pump_con.stop()
        self.fm_con.stop()

        if not self.timeout_event.is_set():
            try:
                self.valve_con.join(5)
                self.pump_con.join(5)
                self.fm_con.join(5)
            except Exception:
                pass




class TRPumpPanel(wx.Panel):
    """
    Time resolved pump control panel
    """
    def __init__(self, parent, tr_panel, pump_settings):
        """
        Init.

        :param wx.Window parent: Parent class for the panel.
        """
        self.name = pump_settings['name']

        wx.Panel.__init__(self, parent, name=self.name)
        logger.debug('Initializing PumpPanel for pump %s', self.name)

        self.tr_flow_panel = tr_panel
        self.pump_type = pump_settings['args'][0]
        self.connected = False
        self.moving = False
        self.syringe_volume_val = 0
        self.pump_direction = 'Dispense'
        self.continuous_flow = pump_settings['ctrl_args']['continuous']
        self.faults_dialog = None

        self.known_syringes = pumpcon.known_syringes

        if 'flow_rate' in pump_settings['ctrl_args']:
            flow_rate = str(pump_settings['ctrl_args']['flow_rate'])
        else:
            flow_rate = '0.1'

        if 'refill_rate' in pump_settings['ctrl_args']:
            refill_rate = str(pump_settings['ctrl_args']['refill_rate'])
        else:
            refill_rate = '0.1'

        if 'syringe_id' in pump_settings['kwargs']:
            syringe = pump_settings['kwargs']['syringe_id']
        else:
            syringe = None

        if 'dual_syringe' in pump_settings['kwargs']:
            dual_syringe = pump_settings['kwargs']['dual_syringe']
        else:
            dual_syringe = False

        if 'max_pressure' in pump_settings['ctrl_args']:
            max_pressure = str(pump_settings['ctrl_args']['max_pressure'])
        else:
            max_pressure = ''

        if 'flow_accel' in pump_settings['ctrl_args']:
            flow_accel = str(pump_settings['ctrl_args']['flow_accel'])
        else:
            flow_accel = '0.1'

        self._create_layout(flow_rate, refill_rate, syringe, dual_syringe,
            max_pressure, flow_accel)

    def _FromDIP(self, size):
        # This is a hack to provide easy back compatibility with wxpython < 4.1
        try:
            return self.FromDIP(size)
        except Exception:
            return size

    def _create_layout(self, flow_rate='', refill_rate='', syringe=None,
        dual_syringe=False, max_pressure='', flow_accel=''):
        """Creates the layout for the panel."""
        top_sizer = wx.StaticBoxSizer(wx.VERTICAL, self, self.name)
        parent = top_sizer.GetStaticBox()

        self.status = wx.StaticText(parent, label='Not connected')
        self.syringe_volume = wx.StaticText(parent, label='0', size=self._FromDIP((50,-1)),
            style=wx.ST_NO_AUTORESIZE)
        self.syringe_volume_label = wx.StaticText(parent, label='Current volume:')
        self.syringe_volume_units = wx.StaticText(parent, label='mL')
        self.set_syringe_volume = wx.Button(parent, label='Set Current Volume')
        self.set_syringe_volume.Bind(wx.EVT_BUTTON, self._on_set_volume)
        self.syringe_vol_gauge = wx.Gauge(parent, size=self._FromDIP((40, -1)),
            style=wx.GA_HORIZONTAL|wx.GA_SMOOTH)
        self.syringe_vol_gauge_low = wx.StaticText(parent, label='0')
        self.syringe_vol_gauge_high = wx.StaticText(parent, label='')
        self.pressure_label = wx.StaticText(parent, label='Pressure:')
        self.pressure = wx.StaticText(parent, label='0', size=self._FromDIP((40, -1)),
            style=wx.ST_NO_AUTORESIZE)
        self.pressure_units = wx.StaticText(parent, label='psi')
        self.flow_readback_label = wx.StaticText(parent, label='Flow Rate:')
        self.flow_readback = wx.StaticText(parent, label='0', size=self._FromDIP((40,-1)),
            style=wx.ST_NO_AUTORESIZE)
        self.flow_readback_units = wx.StaticText(parent, label='mL/min')

        self.vol_gauge = wx.BoxSizer(wx.HORIZONTAL)
        self.vol_gauge.Add(self.syringe_vol_gauge_low,
            flag=wx.ALIGN_CENTER_VERTICAL)
        self.vol_gauge.Add(self.syringe_vol_gauge, 1, border=self._FromDIP(2),
            flag=wx.LEFT|wx.ALIGN_CENTER_VERTICAL)
        self.vol_gauge.Add(self.syringe_vol_gauge_high, border=self._FromDIP(2),
            flag=wx.LEFT|wx.ALIGN_CENTER_VERTICAL)

        status_grid = wx.GridBagSizer(vgap=self._FromDIP(2), hgap=self._FromDIP(2))
        status_grid.Add(wx.StaticText(parent, label='Pump: '), (0,0),
            flag=wx.ALIGN_CENTER_VERTICAL)
        status_grid.Add(wx.StaticText(parent, label=self.name), (0,1), span=(1,2),
            flag=wx.ALIGN_CENTER_VERTICAL)
        status_grid.Add(wx.StaticText(parent, label='Status: '), (1,0),
            flag=wx.ALIGN_CENTER_VERTICAL)
        status_grid.Add(self.status, (1,1), span=(1,2),
            flag=wx.ALIGN_CENTER_VERTICAL|wx.EXPAND)
        status_grid.Add(self.syringe_volume_label, (2,0),
            flag=wx.ALIGN_CENTER_VERTICAL)
        status_grid.Add(self.syringe_volume, (2,1),
            flag=wx.ALIGN_CENTER_VERTICAL)
        status_grid.Add(self.syringe_volume_units, (2,2),
            flag=wx.ALIGN_CENTER_VERTICAL)
        status_grid.Add(self.vol_gauge, (3,1), span=(1,2),
            flag=wx.ALIGN_CENTER_VERTICAL|wx.EXPAND)
        status_grid.Add(self.set_syringe_volume, (4,1), span=(1,2),
            flag=wx.LEFT|wx.ALIGN_RIGHT|wx.ALIGN_CENTER_VERTICAL)

        self.ssi_status_sizer = wx.FlexGridSizer(cols=3, vgap=self._FromDIP(2), hgap=self._FromDIP(2))
        self.ssi_status_sizer.Add(self.pressure_label, flag=wx.ALIGN_CENTER_VERTICAL)
        self.ssi_status_sizer.Add(self.pressure, flag=wx.ALIGN_CENTER_VERTICAL)
        self.ssi_status_sizer.Add(self.pressure_units, flag=wx.ALIGN_CENTER_VERTICAL)
        self.ssi_status_sizer.Add(self.flow_readback_label, flag=wx.ALIGN_CENTER_VERTICAL)
        self.ssi_status_sizer.Add(self.flow_readback, flag=wx.ALIGN_CENTER_VERTICAL)
        self.ssi_status_sizer.Add(self.flow_readback_units, flag=wx.ALIGN_CENTER_VERTICAL)

        self.status_sizer = wx.StaticBoxSizer(wx.StaticBox(parent, label='Info'),
            wx.VERTICAL)
        self.status_sizer.Add(status_grid, 1, flag=wx.ALL|wx.EXPAND, border=self._FromDIP(2))
        self.status_sizer.Add(self.ssi_status_sizer, flag=wx.EXPAND)

        syr_types = sorted(self.known_syringes.keys(), key=lambda x: float(x.split()[0]))
        self.syringe_type = wx.Choice(parent, choices=syr_types)
        self.syringe_type_lbl = wx.StaticText(parent, label='Syringe:')
        self.mode_ctrl = wx.Choice(parent, choices=['Continuous flow', 'Fixed volume'])
        self.mode_ctrl.SetSelection(0)
        self.direction_ctrl = wx.Choice(parent, choices=['Dispense', 'Aspirate'])
        self.direction_ctrl.SetSelection(0)
        self.direction_lbl = wx.StaticText(parent, label='Direction:')
        self.flow_rate_ctrl = wx.TextCtrl(parent, value=flow_rate, size=self._FromDIP((60,-1)),
            style=wx.TE_PROCESS_ENTER, validator=utils.CharValidator('float_te'))
        self.flow_units_lbl = wx.StaticText(parent,
            label=self.tr_flow_panel.settings['flow_units'])
        self.refill_rate_lbl = wx.StaticText(parent, label='Refill rate:')
        self.refill_rate_ctrl = wx.TextCtrl(parent, value=refill_rate, size=self._FromDIP((60,-1)),
            validator=utils.CharValidator('float'))
        self.refill_rate_units = wx.StaticText(parent,
            label=self.tr_flow_panel.settings['flow_units'][:2])
        self.volume_lbl = wx.StaticText(parent, label='Volume:')
        self.volume_ctrl = wx.TextCtrl(parent, size=self._FromDIP((60,-1)),
            validator=utils.CharValidator('float'))
        self.vol_units_lbl = wx.StaticText(parent,
            label=self.tr_flow_panel.settings['flow_units'][:2])
        self.dual_syringe = wx.Choice(parent, choices=['True', 'False'])
        self.dual_syringe.SetStringSelection(str(dual_syringe))
        self.dual_syringe_lbl = wx.StaticText(parent, label='Dual syringe:')
        self.max_pressure_ctrl = wx.TextCtrl(parent, value=str(max_pressure), size=self._FromDIP((60, -1)),
            validator=utils.CharValidator('float_te'), style=wx.TE_PROCESS_ENTER)
        self.max_pressure_lbl = wx.StaticText(parent, label='Max Pressure:')
        self.pressure_units_lbl = wx.StaticText(parent,
            label=self.tr_flow_panel.settings['pressure_units'])
        self.flow_accel_ctrl = wx.TextCtrl(parent, value=str(flow_accel), size=self._FromDIP((60, -1)),
            validator=utils.CharValidator('float_te'), style=wx.TE_PROCESS_ENTER)
        self.flow_accel_lbl = wx.StaticText(parent, label='Flow accel.:')
        self.flow_accel_units_lbl = wx.StaticText(parent,
            label=self.tr_flow_panel.settings['flow_units']+'^2')

        self.flow_rate_ctrl.Bind(wx.EVT_KILL_FOCUS, self._on_fr_setting_change)
        self.flow_rate_ctrl.Bind(wx.EVT_TEXT_ENTER, self._on_fr_setting_change)
        self.direction_ctrl.Bind(wx.EVT_CHOICE, self._on_direction_change)

        self.max_pressure_ctrl.Bind(wx.EVT_TEXT, self._on_max_pressure_text)
        self.max_pressure_ctrl.Bind(wx.EVT_TEXT_ENTER, self._on_max_pressure_change)

        self.flow_accel_ctrl.Bind(wx.EVT_TEXT, self._on_flow_accel_text)
        self.flow_accel_ctrl.Bind(wx.EVT_TEXT_ENTER, self._on_flow_accel_change)

        if syringe is not None and syringe in syr_types:
            self.syringe_type.SetStringSelection(syringe)
        else:
            self.syringe_type.SetSelection(0)
        self.syringe_type.Bind(wx.EVT_CHOICE, self._on_syringe_type)

        self.dual_syringe.Bind(wx.EVT_CHOICE, self._on_dual_syringe)

        self.mode_ctrl.Bind(wx.EVT_CHOICE, self._on_mode)

        basic_ctrl_sizer = wx.GridBagSizer(vgap=self._FromDIP(2), hgap=self._FromDIP(2))
        basic_ctrl_sizer.Add(self.syringe_type_lbl, (0,0),
            flag=wx.ALIGN_CENTER_VERTICAL)
        basic_ctrl_sizer.Add(self.syringe_type, (0,1), span=(1,2),
            flag=wx.ALIGN_CENTER_VERTICAL)
        basic_ctrl_sizer.Add(self.dual_syringe_lbl, (1,0),
            flag=wx.ALIGN_CENTER_VERTICAL)
        basic_ctrl_sizer.Add(self.dual_syringe, (1,1), span=(1,2),
            flag=wx.ALIGN_CENTER_VERTICAL)
        basic_ctrl_sizer.Add(self.direction_lbl, (2,0),
            flag=wx.ALIGN_CENTER_VERTICAL)
        basic_ctrl_sizer.Add(self.direction_ctrl, (2,1), span=(1,2),
            flag=wx.ALIGN_CENTER_VERTICAL)
        basic_ctrl_sizer.Add(self.max_pressure_lbl, (3,0),
            flag=wx.ALIGN_CENTER_VERTICAL)
        basic_ctrl_sizer.Add(self.max_pressure_ctrl, (3,1),
            flag=wx.ALIGN_CENTER_VERTICAL)
        basic_ctrl_sizer.Add(self.pressure_units_lbl, (3,2),
            flag=wx.ALIGN_CENTER_VERTICAL|wx.ALIGN_LEFT)
        basic_ctrl_sizer.Add(self.flow_accel_lbl, (4,0),
            flag=wx.ALIGN_CENTER_VERTICAL)
        basic_ctrl_sizer.Add(self.flow_accel_ctrl, (4,1),
            flag=wx.ALIGN_CENTER_VERTICAL)
        basic_ctrl_sizer.Add(self.flow_accel_units_lbl, (4,2),
            flag=wx.ALIGN_CENTER_VERTICAL|wx.ALIGN_LEFT)
        basic_ctrl_sizer.Add(wx.StaticText(parent, label='Mode:'), (5,0),
            flag=wx.ALIGN_CENTER_VERTICAL)
        basic_ctrl_sizer.Add(self.mode_ctrl, (5,1), span=(1,2),
            flag=wx.ALIGN_CENTER_VERTICAL)
        basic_ctrl_sizer.Add(wx.StaticText(parent, label='Flow rate:'), (6,0),
            flag=wx.ALIGN_CENTER_VERTICAL)
        basic_ctrl_sizer.Add(self.flow_rate_ctrl, (6,1),
            flag=wx.ALIGN_CENTER_VERTICAL)
        basic_ctrl_sizer.Add(self.flow_units_lbl, (6,2),
            flag=wx.ALIGN_CENTER_VERTICAL|wx.ALIGN_LEFT)
        basic_ctrl_sizer.Add(self.refill_rate_lbl, (7,0),
            flag=wx.ALIGN_CENTER_VERTICAL)
        basic_ctrl_sizer.Add(self.refill_rate_ctrl, (7,1),
            flag=wx.ALIGN_CENTER_VERTICAL)
        basic_ctrl_sizer.Add(self.refill_rate_units, (7,2),
            flag=wx.ALIGN_CENTER_VERTICAL|wx.ALIGN_LEFT)
        basic_ctrl_sizer.Add(self.volume_lbl, (8,0),
            flag=wx.RESERVE_SPACE_EVEN_IF_HIDDEN|wx.ALIGN_CENTER_VERTICAL)
        basic_ctrl_sizer.Add(self.volume_ctrl, (8,1),
            flag=wx.RESERVE_SPACE_EVEN_IF_HIDDEN|wx.ALIGN_CENTER_VERTICAL)
        basic_ctrl_sizer.Add(self.vol_units_lbl, (8,2),
            flag=wx.RESERVE_SPACE_EVEN_IF_HIDDEN|wx.ALIGN_CENTER_VERTICAL|wx.ALIGN_LEFT)
        basic_ctrl_sizer.AddGrowableCol(1)
        basic_ctrl_sizer.SetEmptyCellSize((0,0))


        self.run_button = wx.Button(parent, label='Start')
        self.fr_button = wx.Button(parent, label='Change flow rate')

        self.run_button.Bind(wx.EVT_BUTTON, self._on_run)
        self.fr_button.Bind(wx.EVT_BUTTON, self._on_fr_change)

        button_ctrl_sizer = wx.BoxSizer(wx.HORIZONTAL)
        button_ctrl_sizer.Add(self.run_button, 0, wx.ALIGN_CENTER_VERTICAL)
        button_ctrl_sizer.Add(self.fr_button, 0, wx.ALIGN_CENTER_VERTICAL|wx.RESERVE_SPACE_EVEN_IF_HIDDEN)

        self.control_box_sizer = wx.StaticBoxSizer(wx.StaticBox(parent, label='Controls'),
            wx.VERTICAL)
        self.control_box_sizer.Add(basic_ctrl_sizer, flag=wx.EXPAND|wx.TOP|wx.LEFT|wx.RIGHT, border=self._FromDIP(2))
        self.control_box_sizer.Add(button_ctrl_sizer, flag=wx.ALIGN_CENTER_HORIZONTAL|wx.ALL, border=self._FromDIP(2))

        top_sizer.Add(self.status_sizer, flag=wx.EXPAND)
        top_sizer.Add(self.control_box_sizer, border=self._FromDIP(2), flag=wx.EXPAND|wx.TOP)

        self.volume_lbl.Hide()
        self.volume_ctrl.Hide()
        self.vol_units_lbl.Hide()
        self.fr_button.Hide()

        if self.continuous_flow:
            #Status
            self.syringe_volume.Hide()
            self.syringe_volume_label.Hide()
            self.syringe_volume_units.Hide()
            self.set_syringe_volume.Hide()
            self.syringe_vol_gauge.Hide()
            self.syringe_vol_gauge_low.Hide()
            self.syringe_vol_gauge_high.Hide()


            #Controls
            self.syringe_type.Hide()
            self.syringe_type_lbl.Hide()
            self.dual_syringe.Hide()
            self.dual_syringe_lbl.Hide()
            self.direction_ctrl.Hide()
            self.direction_lbl.Hide()
            self.refill_rate_lbl.Hide()
            self.refill_rate_ctrl.Hide()
            self.refill_rate_units.Hide()

            self.pump_mode = 'continuous'

        else:
            #Status
            self.pressure.Hide()
            self.pressure_label.Hide()
            self.pressure_units.Hide()

            #Controls
            self.max_pressure_ctrl.Hide()
            self.max_pressure_lbl.Hide()
            self.pressure_units_lbl.Hide()
            self.flow_accel_ctrl.Hide()
            self.flow_accel_lbl.Hide()
            self.flow_accel_units_lbl.Hide()

            max_vol = self.known_syringes[self.syringe_type.GetStringSelection()]['max_volume']
            self.syringe_vol_gauge_high.SetLabel(str(max_vol))
            self.syringe_vol_gauge.SetRange(int(round(float(max_vol)*1000)))

            self.pump_mode = 'syringe'

        self.Refresh()

        self.SetSizer(top_sizer)

    def _on_mode(self, evt):
        mode = self.mode_ctrl.GetStringSelection()

        if mode == 'Continuous flow':
            self.volume_lbl.Hide()
            self.volume_ctrl.Hide()
            self.vol_units_lbl.Hide()
        else:
            self.volume_lbl.Show()
            self.volume_ctrl.Show()
            self.vol_units_lbl.Show()

        logger.debug('Changed the pump mode to %s for pump %s', mode, self.name)

    def _on_run(self, evt):
        self._start_stop_pump()

    def _on_fr_setting_change(self, evt):
        wx.CallAfter(self.tr_flow_panel.update_flow_info)

    def _on_max_pressure_text(self, evt):
        self.max_pressure_ctrl.SetBackgroundColour('YELLOW')

    def _on_max_pressure_change(self, evt):
        self.max_pressure_ctrl.SetBackgroundColour(wx.NullColour)
        wx.CallAfter(self._set_max_pressure)

    def _on_flow_accel_text(self, evt):
        self.flow_accel_ctrl.SetBackgroundColour('YELLOW')

    def _on_flow_accel_change(self, evt):
        self.flow_accel_ctrl.SetBackgroundColour(wx.NullColour)
        wx.CallAfter(self._set_flow_accel)

    def _on_fr_change(self, evt):
        self._set_flowrate()

    def _on_direction_change(self, evt):
        if self.direction_ctrl.GetStringSelection() == 'Dispense':
            self.pump_direction = 'Dispense'
        else:
            self.pump_direction = 'Aspirate'

    def on_pump_run(self):
        """
        Called from parent to set visual status updates
        """
        if self.moving:
            mode = self.mode_ctrl.GetStringSelection()

            if self.pump_mode == 'continuous':
                if mode == 'Fixed volume':
                    cmd = self.pump_direction
                    self.set_status(cmd.capitalize())
                else:
                    self.set_status('Flowing')
            else:
                if mode == 'Fixed volume':
                    cmd = self.pump_direction
                    self.set_status(cmd.capitalize())
                else:
                    direction = self.pump_direction
                    self.set_status(direction.capitalize())

            self.fr_button.Show()
            self.run_button.SetLabel('Stop')

        else:
            self.run_button.SetLabel('Start')
            self.fr_button.Hide()
            self.set_status('Done')

    def start_pump(self):
        # Just starts the pump
        if self.run_button.GetLabel() == 'Start':
            success = self.run_pump()
        else:
            success = True

        return success

    def run_pump(self):
        """
        Called any time you want to start of stop the pump.
        """
        success = self._start_stop_pump()

        return success

    def _set_flowrate(self):
        cont = True

        try:
            flowr = float(self.flow_rate_ctrl.GetValue())
        except Exception:
            msg = "Flow rate must be a number."
            wx.MessageBox(msg, "Error setting flow rate")
            cont = False

        refillr = self.refill_rate_ctrl.GetValue()

        if refillr != '':
            try:
                refillr = float(refillr)
            except Exception:
                msg = "Refill rate must be a number."
                wx.MessageBox(msg, "Error setting refill rate")
                cont = False

        accel = self.flow_accel_ctrl.GetValue()

        if accel != '':
            try:
                accel = float(accel)
            except Exception:
                msg = "Flow acceleration must be a number."
                wx.MessageBox(msg, "Error setting flow rate")
                cont = False

        if self.pump_direction == 'Dispense':
            dispense = True
        else:
            dispense = False

        if cont:
            if accel != '':
                success = self.tr_flow_panel.set_flow_accel(self.name, accel)

            else:
                success = True

            if success:
                if self.pump_mode == 'continuous':
                    success = self.tr_flow_panel.set_flow_rate(self.name, flowr,
                        self.pump_mode, dispense)
                else:
                    if refillr == '':
                        msg = "Refill rate must be a number."
                        wx.MessageBox(msg, "Error setting refill rate")
                        success = False

                    if self.pump_type == 'NE 500' and success:
                        if dispense:
                            success = self.tr_flow_panel.set_flow_rate(self.name,
                                flowr, self.pump_mode, dispense)
                        else:
                            success = self.tr_flow_panel.set_refill_rate(self.name,
                                refillr, self.pump_mode, dispense)

                    elif success:
                        success = self.tr_flow_panel.set_flow_rate(self.name,
                            flowr, self.pump_mode, dispense)
                        success = self.tr_flow_panel.set_refill_rate(self.name,
                            refillr, self.pump_mode, dispense)
        else:
            success = False
            logger.debug('Failed to set pump %s flow rate', self.name)
            logger.debug('Failed to set pump %s refill rate', self.name)

        return success

    def change_flowrate(self, flow_rate=None, refill_rate=None):
        """
        Called to change the flow rate values in the GUI, does not set the
        flow rate for the pump!
        """
        if flow_rate is not None:
            try:
                self.flow_rate_ctrl.SetValue(str(flow_rate))
            except Exception:
                try:
                    self.flow_rate_ctrl.SetValue(flow_rate)
                except Exception:
                    pass

        if refill_rate is not None:
            try:
                self.refill_rate_ctrl.SetValue(str(refill_rate))
            except Exception:
                try:
                    self.refill_rate_ctrl.SetValue(refill_rate)
                except Exception:
                    pass

    def _start_stop_pump(self):
        """
        Gathers all the necessary data and sends the start/stop command
        to the parent to send the pump.
        """
        if self.connected:
            if self.run_button.GetLabel() == 'Start':
                start = True

                fr_set = self._set_flowrate()
                if not fr_set:
                    return

                mode = self.mode_ctrl.GetStringSelection()
                if mode == 'Fixed volume':
                    try:
                        vol = float(self.volume_ctrl.GetValue())
                    except Exception:
                        msg = "Volume must be a number."
                        wx.CallAfter(self.tr_flow_panel.showMessageDialog, self, msg, "Error setting volume",
                            wx.OK|wx.ICON_ERROR)
                        logger.debug('Failed to set dispense/aspirate volume to %s for pump %s', vol, self.name)
                        return

                    fixed = True

                else:
                    vol = None
                    fixed = False

                if self.pump_direction.lower() == 'dispense':
                    dispense = True
                else:
                    dispense = False

            else:
                start = False
                fixed = False
                dispense = False
                vol = None

            units = self.flow_units_lbl.GetLabel()

            success = self.tr_flow_panel.start_pump(self.name, start, fixed, dispense, vol,
                self.pump_mode, units, self)

        else:
            msg = "Cannot start pump flow before the pump is connected."
            wx.CallAfter(self.tr_flow_panel.showMessageDialog, self, msg, "Error starting flow",
                            wx.OK|wx.ICON_ERROR)
            logger.debug('Failed to start flow for pump %s because it is not connected', self.name)
            success = False

        return success

    def _on_set_volume(self, evt):
        wx.CallAfter(self._set_volume_user)

    def _set_volume_user(self):
        vol = wx.GetTextFromUser("Enter current syringe volume:",
            "Set Syringe Volume", "0", parent=self)

        self.set_volume(vol)

    def set_volume(self, vol):
        try:
            vol = float(vol)
            if vol != -1:
                self.tr_flow_panel.set_pump_volume(self.name, vol)
                self.syringe_volume_val = vol
                max_vol = self.get_max_volume()
                set_vol = min(max_vol, vol)
                gauge_vol = int(round(float(set_vol)*1000))
                if gauge_vol < 0:
                    gauge_vol = 0
                wx.CallAfter(self._set_status_volume, vol)
                wx.CallAfter(self.syringe_vol_gauge.SetValue, gauge_vol)

        except ValueError:
            msg = "Volume must be a number."
            wx.MessageBox(msg, "Error setting volume")

    def set_max_pressure(self):
        cont = True

        try:
            max_pressure = float(self.max_pressure_ctrl.GetValue())
        except Exception:
            cont = False

        if cont:
            self._set_max_pressure()

    def _set_max_pressure(self):
        cont = True

        try:
            max_pressure = float(self.max_pressure_ctrl.GetValue())
        except Exception:
            msg = "Max pressure must be a number."
            wx.MessageBox(msg, "Error setting max pressure")
            cont = False

        if cont:
            success = self.tr_flow_panel.set_max_pressure(self.name, max_pressure)

        else:
            success = False
            logger.debug('Failed to set pump %s max pressure', self.name)

        return success

    def set_flow_accel(self):
        cont = True

        try:
            flow_accel = float(self.flow_accel_ctrl.GetValue())
        except Exception:
            cont = False

        if cont:
            self._set_flow_accel()

    def _set_flow_accel(self):
        cont = True

        try:
            flow_accel = float(self.flow_accel_ctrl.GetValue())
        except Exception:
            msg = "Flow acceleration must be a number."
            wx.MessageBox(msg, "Error setting flow acceleration")
            cont = False

        if cont:
            success = self.tr_flow_panel.set_flow_accel(self.name, flow_accel)

        else:
            success = False
            logger.debug('Failed to set pump %s max pressure', self.name)

        return success

    def set_status(self, status):
        """
        Changes the status in the GUI.

        :param str status: The status to display.
        """
        logger.debug('Setting pump %s status to %s', self.name, status)
        self.status.SetLabel(status)

    def set_status_direction(self, dispensing):
        # # self.set_pump_direction(dispensing)
        # if dispensing:
        #     wx.CallAfter(self.set_status, 'Dispense')
        # else:
        #     wx.CallAfter(self.set_status, 'Aspirate')
        pass

    def set_status_volume(self, vol):
        try:
            vol = float(vol)
            if vol != -1:
                self.syringe_volume_val = vol
                wx.CallAfter(self._set_status_volume, vol)
                max_vol = self.get_max_volume()
                set_vol = min(max_vol, vol)
                gauge_vol = int(round(float(set_vol)*1000))
                if gauge_vol < 0:
                    gauge_vol = 0
                wx.CallAfter(self.syringe_vol_gauge.SetValue, gauge_vol)

        except ValueError:
            pass

    def set_status_flow_rate(self, val):
        try:
            val = float(val)

            if self.pump_direction == 'Dispense':
                wx.CallAfter(self._set_status_flow_rate, val)

        except ValueError:
            pass

    def set_status_refill_rate(self, val):
        try:
            val = float(val)

            if self.pump_direction == 'Aspirate':
                wx.CallAfter(self._set_status_flow_rate, val)

        except ValueError:
            pass

    def set_status_pressure(self, val):
        try:
            val = float(val)
            wx.CallAfter(self._set_status_pressure, val)

        except ValueError:
            pass

    def set_status_syringe_id(self, syringe_id):
        wx.CallAfter(self._set_status_syringe_id, syringe_id)

    def _set_status_volume(self, volume):
        logger.debug("Setting pump %s volume to %s", self.name, volume)
        self.syringe_volume.SetLabel('{}'.format(round(float(volume), 3)))

    def _set_status_flow_rate(self, rate):
        logger.debug("Setting pump %s flow rate readback to %s", self.name, rate)
        if not self.get_moving():
            rate = 0

        if round(float(rate), 3) != float(self.flow_readback.GetLabel()):
            self.flow_readback.SetLabel('{}'.format(round(float(rate), 3)))

    def _set_status_pressure(self, pressure):
        logger.debug("Setting pump %s pressure to %s", self.name, pressure)
        self.pressure.SetLabel('{}'.format(round(float(pressure), 3)))

    def _set_status_syringe_id(self, syringe_id):
        if syringe_id != self.syringe_type.GetStringSelection():
            self.syringe_type.SetStringSelection(syringe_id)

        self._update_syringe_gui_values(syringe_id)

    def set_moving(self, moving):
        if moving != self.moving:
            self.moving = moving
            wx.CallAfter(self.on_pump_run)

    def get_moving(self):
        return self.moving

    def get_status(self):
        return self.status.GetLabel()

    def get_status_volume(self):
        return self.syringe_volume_val

    def get_status_flow_rate(self):
        return float(self.flow_readback.GetLabel())

    def get_status_pressure(self):
        return float(self.pressure.GetLabel())

    def get_pump_direction(self):
        return self.pump_direction

    def get_max_volume(self):
        max_vol = float(self.known_syringes[self.syringe_type.GetStringSelection()]['max_volume'])

        return max_vol

    def get_target_flow_rate(self):
        flow_rate = float(self.flow_rate_ctrl.GetValue())

        return flow_rate

    def get_dual_syringe(self):
        return self.dual_syringe.GetStringSelection()=='True'

    def _on_syringe_type(self, evt):
        syringe_id = self.syringe_type.GetStringSelection()
        vals = copy.deepcopy(self.known_syringes[syringe_id])
        vals['syringe_id'] = syringe_id
        self.tr_flow_panel.set_pump_cal(self.name, vals)

        self._update_syringe_gui_values(self, syringe_id)

    def _update_syringe_gui_values(self, syringe_id):
        max_vol = self.known_syringes[syringe_id]['max_volume']
        self.syringe_vol_gauge_high.SetLabel(str(max_vol))
        self.syringe_vol_gauge.SetRange(int(round(float(max_vol)*1000)))

    def _on_dual_syringe(self, evt):
        self.tr_flow_panel.set_pump_dual_syringe_type(self.name,
            self.dual_syringe.GetStringSelection()=='True')

    def set_pump_direction(self, dispense):
        if not self.continuous_flow:
            if dispense:
                self.pump_direction = 'Dispense'
                ret = wx.CallAfter(self.direction_ctrl.SetStringSelection, 'Dispense')
            else:
                self.pump_direction = 'Aspirate'
                ret = wx.CallAfter(self.direction_ctrl.SetStringSelection, 'Aspirate')


    def show_faults_dialog(self, msg):
        if self.faults_dialog is None:
            self.faults_dialog = utils.WarningMessage(self, msg, 'Fault detected',
                self._on_close_faults_dialog)
            self.faults_dialog.Show()

    def _on_close_faults_dialog(self):
        self.faults_dialog = None


class TRFrame(wx.Frame):
    """
    A lightweight frame allowing one to work with arbitrary number of pumps.
    Only meant to be used when the pumpcon module is run directly,
    rather than when it is imported into another program.
    """
    def __init__(self, settings, display, *args, **kwargs):
        """
        Initializes the pump frame. Takes args and kwargs for the wx.Frame class.
        """
        super(TRFrame, self).__init__(*args, **kwargs)
        logger.debug('Setting up the TRFrame')

        self.Bind(wx.EVT_CLOSE, self._on_exit)

        self._create_layout(settings, display)

        self.Layout()
        self.SendSizeEvent()
        self.Fit()
        self.Raise()

        if settings['simulated']:
            msg = ('WARNING: The system is currently running in simulated mode. '
                'If you want to run experiments, quit the program and restart '
                'with the simulated setting set to False.')
            dialog = wx.MessageDialog(self, msg, 'Simulation Mode',
                style=wx.ICON_WARNING|wx.OK)

            dialog.ShowModal()
            dialog.Destroy()


    def _create_layout(self, settings, display):
        """Creates the layout"""
        if display == 'scan':
            self.tr_panel = TRScanPanel(settings, self)
        elif display == 'flow':
            self.tr_panel = TRFlowPanel(settings, self)

        self.tr_sizer = wx.BoxSizer(wx.HORIZONTAL)
        self.tr_sizer.Add(self.tr_panel, proportion=1, flag=wx.EXPAND)

        top_sizer = wx.BoxSizer(wx.VERTICAL)
        top_sizer.Add(self.tr_sizer, proportion=1, flag=wx.EXPAND|wx.ALL, border=5)

        self.SetSizer(top_sizer)

    def _on_exit(self, evt):
        """Stops all current pump motions and then closes the frame."""
        logger.debug('Closing the TRFrame')

        self.tr_panel.on_exit()

        self.Destroy()

if __name__ == '__main__':
    logger = logging.getLogger()
    logger.setLevel(logging.DEBUG)
    h1 = logging.StreamHandler(sys.stdout)
    h1.setLevel(logging.INFO)
    # h1.setLevel(logging.DEBUG)
    # formatter = logging.Formatter('%(asctime)s - %(message)s')
    formatter = logging.Formatter('%(asctime)s - %(name)s - %(threadName)s - %(levelname)s - %(message)s')
    h1.setFormatter(formatter)
    logger.addHandler(h1)

    #Settings
    trsaxs_settings = {
        'position_units'        : 'mm',
        'speed_units'           : 'mm/s',
        'accel_units'           : 'mm/s^2',
        'time_units'            : 's',
        'x_start'               : 0,
        'x_end'                 : 10,
        'y_start'               : 0,
        'y_end'                 : 0,
        'scan_speed'            : 2,
        'num_scans'             : 1,
        'return_speed'          : 20,
        'scan_acceleration'     : 10,
        'return_acceleration'   : 100,
        'constant_scan_speed'   : True,
        'scan_start_offset_dist': 0,
        'scan_end_offset_dist'  : 0,
        'motor_type'            : 'Newport_XPS',
        'motor_ip'              : '164.54.204.76',
        'motor_port'            : '5001',
        'motor_group_name'      : 'XY',
        'motor_x_name'          : 'XY.X',
        'motor_y_name'          : 'XY.Y',
        'pco_direction'         : 'x',
        'pco_pulse_width'       : D('10'), #In microseconds, opt: 0.2, 1, 2.5, 10
        'pco_encoder_settle_t'  : D('0.075'), #In microseconds, opt: 0.075, 1, 4, 12
        'encoder_resolution'    : D('0.000001'), #for XMS160, in mm
        'encoder_precision'     : 6, #Number of significant decimals in encoder value
        # 'encoder_resolution'    : D('0.00001'), #for GS30V, in mm
        # 'encoder_precision'     : 5, #Number of significant decimals in encoder value
        'min_off_time'          : D('0.001'),
        'x_range'               : (-80, 80),
        'y_range'               : (-5, 25),
        'speed_lim'             : (0, 300),
        'acceleration_lim'      : (0, 2500),
        'remote_pump_ip'        : '164.54.204.8',
        'remote_pump_port'      : '5556',
        'remote_fm_ip'          : '164.54.204.8',
        'remote_fm_port'        : '5557',
        'remote_valve_ip'       : '164.54.204.8',
        'remote_valve_port'     : '5558',
        'device_communication'  : 'local',
        # 'injection_valve'       : [{'name': 'Injection', 'args': ['Rheodyne', 'COM6'],  #Chaotic flow
        #                             'kwargs': {'positions' : 2}},],
        # 'sample_valve'          : [],
        # 'buffer1_valve'         : [],
        # 'buffer2_valve'         : [],
        # 'sample_pump'           : [{'name': 'Sample', 'args': ['SSI Next Gen', 'COM7'],
        #                             'kwargs': {'flow_rate_scale': 1.0204,
        #                             'flow_rate_offset': 15.346/1000,'scale_type': 'up'},
        #                             'ctrl_args': {'flow_rate': 0.1, 'flow_accel': 0.0,
        #                             'max_pressure': 1800, 'continuous': True}}],
        # 'buffer1_pump'           : [{'name': 'Buffer 1', 'args': ['SSI Next Gen', 'COM15'],
        #                             'kwargs': {'flow_rate_scale': 1.0478,
        #                             'flow_rate_offset': -72.82/1000,'scale_type': 'up'},
        #                             'ctrl_args': {'flow_rate': 0.1, 'flow_accel': 0.0,
        #                             'max_pressure': 1800, 'continuous': True}}],
        # 'buffer2_pump'          : [{'name': 'Buffer 2', 'args': ['SSI Next Gen', 'COM9'],
        #                             'kwargs': {'flow_rate_scale': 1.0179,
        #                             'flow_rate_offset': -20.842/10000,'scale_type': 'up'},
        #                             'ctrl_args': {'flow_rate': 0.1, 'flow_accel': 0.0,
        #                             'max_pressure': 1800, 'continuous': True}}],
        # 'outlet_fm'             : {'name': 'outlet', 'args' : ['BFS', 'COM5'], 'kwargs': {}},
        # 'injection_valve_label' : 'Injection',
        # 'sample_valve_label'    : 'Sample',
        # 'buffer1_valve_label'   : 'Buffer 1',
        # 'buffer2_valve_label'   : 'Buffer 2',
        'injection_valve'       : [{'name': 'Injection', 'args': ['Rheodyne', 'COM6'], # Laminar flow
                                    'kwargs': {'positions' : 2}},],
        'sample_valve'          : [{'name': 'Sample', 'args': ['Rheodyne', 'COM3'],
                                    'kwargs': {'positions' : 6}},],
        'buffer1_valve'         : [{'name': 'Buffer 1', 'args': ['Rheodyne', 'COM10'],
                                    'kwargs': {'positions' : 6}},
                                    {'name': 'Buffer 2', 'args': ['Rheodyne', 'COM4'],
                                    'kwargs': {'positions' : 6}},],
        'buffer2_valve'         : [{'name': 'Sheath 1', 'args': ['Rheodyne', 'COM21'],
                                    'kwargs': {'positions' : 6}},
                                    {'name': 'Sheath 2', 'args': ['Rheodyne', 'COM8'],
                                    'kwargs': {'positions' : 6}},],
        'buffer1_pump'           : [{'name': 'Buffer', 'args': ['Pico Plus', 'COM11'],
                                    'kwargs': {'syringe_id': '3 mL, Medline P.C.',
                                    'pump_address': '00', 'dual_syringe': 'False'},
                                    'ctrl_args': {'flow_rate' : '0.068', 'refill_rate' : '3',
                                    'continuous': False}},],
        'buffer2_pump'          : [{'name': 'Sheath', 'args': ['Pico Plus', 'COM7'],
                                    'kwargs': {'syringe_id': '1 mL, Medline P.C.',
                                    'pump_address': '00', 'dual_syringe': 'False'}, 'ctrl_args':
                                    {'flow_rate' : '0.002', 'refill_rate' : '1',
                                    'continuous': False}},],
        'sample_pump'           : [{'name': 'Sample', 'args': ['Pico Plus', 'COM9'],
                                    'kwargs': {'syringe_id': '1 mL, Medline P.C.',
                                    'pump_address': '00', 'dual_syringe': 'False'}, 'ctrl_args':
                                    {'flow_rate' : '0.009', 'refill_rate' : '1',
                                    'continuous': False}}],
        'outlet_fm'             : {'name': 'outlet', 'args' : ['BFS', 'COM13'], 'kwargs': {}},
        'injection_valve_label' : 'Injection',
        'sample_valve_label'    : 'Sample',
        'buffer1_valve_label'   : 'Buffer',
        'buffer2_valve_label'   : 'Sheath',
        'device_communication'  : 'local',                                         # Simulated
        # 'injection_valve'       : [{'name': 'Injection', 'args': ['Soft', None],    # Simulated Chaotic w/syringe pump
        #                             'kwargs': {'positions' : 2}},],
        # 'sample_valve'          : [{'name': 'Sample', 'args': ['Soft', None],
        #                             'kwargs': {'positions' : 6}},],
        # 'buffer1_valve'         : [{'name': 'Buffer 1', 'args': ['Soft', None],
        #                             'kwargs': {'positions' : 6}},],
        # 'buffer2_valve'         : [{'name': 'Buffer 2', 'args': ['Soft', None],
        #                             'kwargs': {'positions' : 6}},],
        # 'sample_pump'           : [{'name': 'Sample', 'args': ['Soft Syringe', None],
        #                                 'kwargs': {'syringe_id': '10 mL, Medline P.C.',
        #                                 'flow_rate': 1, 'refill_rate': 10},
        #                                 'ctrl_args': {'continuous': False}},],
        # 'buffer1_pump'          : [{'name': 'Buffer 1', 'args': ['Soft Syringe', None],
        #                                 'kwargs': {'syringe_id': '20 mL, Medline P.C.',
        #                                 'flow_rate': 1, 'refill_rate': 10},
        #                                 'ctrl_args': {'continuous': False}},],
        # 'buffer2_pump'          : [ {'name': 'Buffer 2', 'args': ['Soft Syringe', None],
        #                                 'kwargs': {'syringe_id': '20 mL, Medline P.C.',
        #                                 'flow_rate': 1, 'refill_rate': 10},
        #                                 'ctrl_args': {'continuous': False}},],
        # 'outlet_fm'             : {'name': 'outlet', 'args': ['Soft', None], 'kwargs':{}},
        # 'injection_valve_label' : 'Injection',
        # 'sample_valve_label'    : 'Sample',
        # 'buffer1_valve_label'   : 'Buffer 1',
        # 'buffer2_valve_label'   : 'Buffer 2',
        # 'injection_valve'       : [{'name': 'Injection', 'args': ['Soft', None],    # Simulated Chaotic w/continuous pump
        #                             'kwargs': {'positions' : 2}},],
        # 'sample_valve'          : [],
        # 'buffer1_valve'         : [],
        # 'buffer2_valve'         : [],
        # 'sample_pump'           : [{'name': 'Sample', 'args': ['Soft', None],
        #                             'kwargs': {}, 'ctrl_args': {'continuous': True}},],
        # 'buffer1_pump'          : [{'name': 'Buffer 1', 'args': ['Soft', None],
        #                             'kwargs': {}, 'ctrl_args': {'continuous': True}},],
        # 'buffer2_pump'          : [{'name': 'Buffer 2', 'args': ['Soft', None],
        #                             'kwargs': {}, 'ctrl_args': {'continuous': True}},],
        # 'outlet_fm'             : {'name': 'outlet', 'args': ['Soft', None], 'kwargs':{}},
        # 'injection_valve_label' : 'Injection',
        # 'sample_valve_label'    : 'Sample',
        # 'buffer1_valve_label'   : 'Buffer 1',
        # 'buffer2_valve_label'   : 'Buffer 2',
        # 'injection_valve'       : [{'name': 'Injection', 'args': ['Soft', None],    # Simulated laminar flow
        #                             'kwargs': {'positions' : 2}},],
        # 'sample_valve'          : [{'name': 'Sample', 'args': ['Soft', None],
        #                             'kwargs': {'positions' : 6}},],
        # 'buffer1_valve'         : [{'name': 'Buffer 1', 'args': ['Soft', None],
        #                             'kwargs': {'positions' : 6}},
        #                             {'name': 'Buffer 2', 'args': ['Soft', None],
        #                             'kwargs': {'positions' : 6}},],
        # 'buffer2_valve'         : [{'name': 'Sheath 1', 'args': ['Soft', None],
        #                             'kwargs': {'positions' : 6}},
        #                             {'name': 'Sheath 2', 'args': ['Soft', None],
        #                             'kwargs': {'positions' : 6}},],
        # 'sample_pump'           : [{'name': 'Sample', 'args': ['Soft Syringe', None],
        #                                 'kwargs': {'syringe_id': '3 mL, Medline P.C.'},
        #                                 'ctrl_args': {'continuous': False,
        #                                 'flow_rate': 1, 'refill_rate': 3}},],
        # 'buffer1_pump'          : [{'name': 'Buffer', 'args': ['Soft Syringe', None],
        #                                 'kwargs': {'syringe_id': '3 mL, Medline P.C.'},
        #                                 'ctrl_args': {'continuous': False,
        #                                 'flow_rate': 1, 'refill_rate': 3}},],
        # 'buffer2_pump'          : [ {'name': 'Sheath', 'args': ['Soft Syringe', None],
        #                                 'kwargs': {'syringe_id': '3 mL, Medline P.C.'},
        #                                 'ctrl_args': {'continuous': False,
        #                                 'flow_rate': 1, 'refill_rate': 3}},],
        # 'outlet_fm'             : {'name': 'outlet', 'args': ['Soft', None], 'kwargs':{}},
        # 'injection_valve_label' : 'Injection',
        # 'sample_valve_label'    : 'Sample',
        # 'buffer1_valve_label'   : 'Buffer',
        # 'buffer2_valve_label'   : 'Sheath',
        'flow_units'            : 'mL/min',
        'pressure_units'        : 'psi',
        'total_flow_rate'       : '0.149', # For laminar flow
        # 'total_flow_rate'       : '6', # For chaotic flow
        'dilution_ratio'        : '10', # For chaotic flow
        'max_dilution'          : 50, # For chaotic flow
        'max_flow'              : 2, # For laminar flow
        # 'max_flow'              : 8, # For chaotic flow
        'auto_set_valves'       : True,
        'valve_start_positions' : {'sample_valve': 2, 'buffer1_valve': 2,
                                    'buffer2_valve': 2, 'injection_valve': 2},
        'valve_refill_positions': {'sample_valve': 1, 'buffer1_valve': 1,
                                    'buffer2_valve': 1, 'injection_valve': 2},
        'valve_purge_positions' : {'sample_valve': 6, 'buffer1_valve': 6,
                                    'buffer2_valve': 6, 'injection_valve': 2},
        'autostart'             : 'At flow rate',
        'autostart_flow'        : '4.5',
        'autostart_flow_ratio'  : 0.98,
        'autostart_delay'       : '0',
        'autoinject'            : 'After scan',
        'autoinject_scan'       : '5',
        'autoinject_valve_pos'  : 1,
        #'mixer_type'            : 'chaotic', # laminar or chaotic
        'mixer_type'            : 'laminar', # laminar or chaotic
        'sample_ratio'          : '0.066', # For laminar flow
        'sheath_ratio'          : '0.032', # For laminar flow
        'simulated'             : False, # VERY IMPORTANT. MAKE SURE THIS IS FALSE FOR EXPERIMENTS
        }

    # trsaxs_settings['components'] = ['trsaxs_scan', 'trsaxs_flow']
    trsaxs_settings['components'] = ['trsaxs_flow']

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
    frame = TRFrame(trsaxs_settings, 'flow', None, title='TRSAXS Control')
    frame.Show()
    app.MainLoop()



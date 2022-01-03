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

from collections import OrderedDict, deque
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
                    pass

                try:
                    return_vals = self._calc_exposure_params()
                except Exception:
                    # print(traceback.print_exc())
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

                    (x_pco_step,
                        y_pco_step,
                        vect_scan_speed,
                        vect_scan_accel,
                        vect_return_speed,
                        vect_return_accel) = self._calc_pco_params(x_start,
                        x_end, y_start, y_end, scan_speed, return_speed,
                        scan_acceleration, return_acceleration, delta_t,
                        scan_type, step_axis)

                    if self.settings['pco_direction'] == 'x':
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

                    if pco_start % self.settings['encoder_resolution'] != 0:
                        pco_start = self.round_to(pco_start,
                            self.settings['encoder_precision'],
                            self.settings['encoder_resolution'])

                    if pco_end % self.settings['encoder_resolution'] != 0:
                        pco_end = self.round_to(pco_end,
                            self.settings['encoder_precision'],
                            self.settings['encoder_resolution'])

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
                        float(self.settings['encoder_resolution'])/float(pco_speed)):
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
                if step_axis != self.settings['pco_direction'].lower():
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
                scan_values['pco_direction'] = self.settings['pco_direction']
                scan_values['pco_pulse_width'] = self.settings['pco_pulse_width']
                scan_values['pco_encoder_settle_t'] =  self.settings['pco_encoder_settle_t']

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
        scan_type, step_axis):
        """ For Newport XPS controller with encoded stages"""

        (vect_scan_speed, vect_scan_accel,
            vect_return_speed, vect_return_accel) = self._calc_vector_params(x_start,
            x_end, y_start, y_end, scan_speed, return_speed, scan_acceleration,
            return_acceleration, scan_type, step_axis)

        x_pco_step = delta_t*D(vect_scan_speed[0])
        y_pco_step = delta_t*D(vect_scan_speed[1])

        if x_pco_step % self.settings['encoder_resolution'] != 0:
            x_pco_step = x_pco_step + self.settings['encoder_resolution']/D('2') #Round up
            x_pco_step = self.round_to(x_pco_step, self.settings['encoder_precision'],
            self.settings['encoder_resolution'])

        if y_pco_step % self.settings['encoder_resolution'] != 0:
            y_pco_step = y_pco_step + self.settings['encoder_resolution']/D('2') #Round up
            y_pco_step = self.round_to(x_pco_step, self.settings['encoder_precision'],
            self.settings['encoder_resolution'])

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
            self.outlet_density.SetLabel('1.000')
            self.outlet_T.SetLabel('22')

    def _init_connections(self):
        self.pump_cmd_q = deque()
        self.pump_return_q = deque()
        self.pump_abort_event = threading.Event()
        self.pump_event = threading.Event()

        self.fm_cmd_q = deque()
        self.fm_return_q = deque()
        self.fm_abort_event = threading.Event()
        self.fm_event = threading.Event()

        self.valve_cmd_q = deque()
        self.valve_return_q = deque()
        self.valve_abort_event = threading.Event()
        self.valve_event = threading.Event()

        self.timeout_event = threading.Event()

        if self.settings['device_communication'] == 'local':
            self.pump_con = pumpcon.PumpCommThread(self.pump_cmd_q,
                self.pump_return_q, self.pump_abort_event, 'PumpCon')

            self.fm_con = fmcon.FlowMeterCommThread(self.fm_cmd_q,
                self.fm_return_q, self.fm_abort_event, 'FMCon')

            self.valve_con = valvecon.ValveCommThread(self.valve_cmd_q,
                self.valve_return_q, self.valve_abort_event, 'ValveCon')

            self.local_devices = True

        else:
            pump_ip = self.settings['remote_pump_ip']
            pump_port = self.settings['remote_pump_port']
            self.pump_con = client.ControlClient(pump_ip, pump_port,
                self.pump_cmd_q, self.pump_return_q,
                self.pump_abort_event, self.timeout_event, name='PumpControlClient')

            fm_ip = self.settings['remote_fm_ip']
            fm_port = self.settings['remote_fm_port']
            self.fm_con = client.ControlClient(fm_ip, fm_port,
                self.fm_cmd_q, self.fm_return_q,
                self.fm_abort_event, self.timeout_event, name='FMControlClient')

            valve_ip = self.settings['remote_valve_ip']
            valve_port = self.settings['remote_valve_port']
            self.valve_con = client.ControlClient(valve_ip, valve_port,
                self.valve_cmd_q, self.valve_return_q,
                self.valve_abort_event, self.timeout_event, name='ValveControlClient')

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

        self._on_flow_change(None)

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

                widget.SetMin(1)
                widget.SetMax(int(settings[3]['positions']))

                name = '{}_{}'.format(valve_basename, i)
                vtype = settings[0].replace(' ', '_')
                com = settings[1]

                args = (com, name, vtype)
                kwargs = {'positions'   : int(settings[3]['positions'])}

                if not self.local_devices:
                    cmd = ('connect_remote', args, kwargs)
                else:
                    cmd = ('connect', args, kwargs)

                init = self._send_valvecmd(cmd, response=True)

                if not init and not self.timeout_event.is_set():
                    logger.error('Failed to connect to the {}.'.format(name.replace('_', ' ')))

                    msg = ('Could not connect to the {}. Contact your beamline '
                        'scientist.'.format(name.replace('_', ' ')))

                    dialog = wx.MessageDialog(self, msg, 'Connection error',
                        style=wx.OK|wx.ICON_ERROR)
                    dialog.ShowModal()
                    dialog.Destroy()

                self.valves[name] = (name, vtype, com)

        self.get_all_valve_positions()

        logger.info('Valve initializiation successful.')

        self.valve_monitor_thread.start()

    def _init_pumps(self):
        logger.info('Initializing pumps on startup')
        pumps = [('sample_pump', self.settings['sample_pump']),
            ('buffer1_pump', self.settings['buffer1_pump']),
            ('buffer2_pump', self.settings['buffer2_pump']),
            ]

        for pump in pumps:
            name = pump[1][0]
            ptype = pump[1][1].replace(' ', '_')
            com = pump[1][2]
            syringe = pump[1][3][0]

            try:
                address = pump[1][3][1]
            except Exception:
                address = None

            args = (com, name, ptype)
            kwargs = copy.deepcopy(self.sample_pump_panel.known_syringes[syringe])
            kwargs['syringe_id'] = syringe

            if address is not None:
                kwargs['pump_address'] = address

            try:
                dual_syringe = pump[1][3][2]
            except Exception:
                dual_syringe = False

            kwargs['dual_syringe'] = dual_syringe

            if not self.local_devices:
                cmd = ('connect_remote', args, kwargs)
            else:
                cmd = ('connect', args, kwargs)

            init = self._send_pumpcmd(cmd, response=True)

            if not init and not self.timeout_event.is_set():
                logger.error('Failed to connect to the {}.'.format(name.replace('_', ' ')))

                msg = ('Could not connect to the {}. Contact your beamline '
                    'scientist.'.format(name.replace('_', ' ')))

                dialog = wx.MessageDialog(self, msg, 'Connection error',
                    style=wx.OK|wx.ICON_ERROR)
                dialog.ShowModal()
                dialog.Destroy()

            self.pumps[name] = (name, ptype, com, address)

            self.set_units(name, self.settings['flow_units'])

            self.set_pump_status(name, 'Connected')
            self.pump_panels[name].connected = True

        self.pump_monitor_thread.start()

        logger.info('Pump initializiation successful')

    def _init_flowmeters(self):
        outlet_fm = self.settings['outlet_fm']

        logger.info('Initializing  flow meters on startup')

        outlet_args = (outlet_fm[1], 'outlet_fm', outlet_fm[0])
        outlet_init_cmd = ('connect', outlet_args, {})

        self.fms['outlet_fm'] = ('outlet_fm', outlet_fm[0], outlet_fm[1])

        try:
            _, outlet_init = self._send_fmcmd(outlet_init_cmd, response=True)
        except Exception:
            outlet_init = False

        if not outlet_init and not self.timeout_event.is_set():
            logger.error('Failed to connect to the outlet flow meter.')

            msg = ('Could not connect to the TR-SAXS outlet flow meter. '
                'Contact your beamline scientist.')

            dialog = wx.MessageDialog(self, msg, 'Connection error',
                style=wx.OK|wx.ICON_ERROR)
            dialog.ShowModal()
            dialog.Destroy()

        if outlet_init:
            self._send_fmcmd(('set_units', ('outlet_fm', self.settings['flow_units']), {}))

            if outlet_fm[0] == 'BFS':
                ret = self._send_fmcmd(('get_density', ('outlet_fm',), {}), True)
                if ret is not None and ret[0] == 'density':
                    self._set_fm_values('outlet_fm', density=ret[1])

                ret = self._send_fmcmd(('get_temperature', ('outlet_fm',), {}), True)
                if ret is not None and ret[0] == 'temperature':
                    self._set_fm_values('outlet_fm', T=ret[1])

            ret = self._send_fmcmd(('get_flow_rate', ('outlet_fm',), {}), True)
            if ret is not None and ret[0] == 'flow_rate':
                self._set_fm_values('outlet_fm', flow_rate=ret[1])

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
                size=(60, -1))
            self.dilution_ratio = wx.TextCtrl(basic_flow_parent, value=self.settings['dilution_ratio'],
                style=wx.TE_PROCESS_ENTER, validator=utils.CharValidator('float_te'),
                size=(60, -1))

            self.total_flow.Bind(wx.EVT_TEXT_ENTER, self._on_flow_change)
            self.total_flow.Bind(wx.EVT_KILL_FOCUS, self._on_flow_change)
            self.dilution_ratio.Bind(wx.EVT_TEXT_ENTER, self._on_flow_change)
            self.dilution_ratio.Bind(wx.EVT_KILL_FOCUS, self._on_flow_change)

            flow_sizer = wx.FlexGridSizer(cols=2, vgap=5, hgap=5)
            flow_sizer.Add(wx.StaticText(basic_flow_parent, label='Total flow rate [{}]:'
                ''.format(self.settings['flow_units'])), flag=wx.ALIGN_CENTER_VERTICAL)
            flow_sizer.Add(self.total_flow, flag=wx.ALIGN_CENTER_VERTICAL)
            flow_sizer.Add(wx.StaticText(basic_flow_parent, label='Dilution ratio:'),
                flag=wx.ALIGN_CENTER_VERTICAL)
            flow_sizer.Add(self.dilution_ratio, flag=wx.ALIGN_CENTER_VERTICAL)
        else:
            self.total_flow = wx.TextCtrl(basic_flow_parent, value=self.settings['total_flow_rate'],
                style=wx.TE_PROCESS_ENTER, validator=utils.CharValidator('float_te'),
                size=(60, -1))
            self.sample_ratio = wx.TextCtrl(basic_flow_parent, value=self.settings['sample_ratio'],
                style=wx.TE_PROCESS_ENTER, validator=utils.CharValidator('float_te'),
                size=(60, -1))
            self.sheath_ratio = wx.TextCtrl(basic_flow_parent, value=self.settings['sheath_ratio'],
                style=wx.TE_PROCESS_ENTER, validator=utils.CharValidator('float_te'),
                size=(60, -1))

            self.total_flow.Bind(wx.EVT_TEXT_ENTER, self._on_flow_change)
            self.total_flow.Bind(wx.EVT_KILL_FOCUS, self._on_flow_change)
            self.sample_ratio.Bind(wx.EVT_TEXT_ENTER, self._on_flow_change)
            self.sample_ratio.Bind(wx.EVT_KILL_FOCUS, self._on_flow_change)
            self.sheath_ratio.Bind(wx.EVT_TEXT_ENTER, self._on_flow_change)
            self.sheath_ratio.Bind(wx.EVT_KILL_FOCUS, self._on_flow_change)

            flow_sizer = wx.FlexGridSizer(cols=2, vgap=5, hgap=5)
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
        refill_all = wx.Button(basic_flow_parent, label = 'Refill pumps')

        start_all.Bind(wx.EVT_BUTTON, self._on_start_all)
        stop_all.Bind(wx.EVT_BUTTON, self._on_stop_all)
        refill_all.Bind(wx.EVT_BUTTON, self._on_refill_all)

        flow_button_sizer = wx.GridBagSizer(vgap=5, hgap=5)
        flow_button_sizer.Add(start_all, (0,0), flag=wx.ALIGN_CENTER_VERTICAL)
        flow_button_sizer.Add(refill_all, (0,1), flag=wx.ALIGN_CENTER_VERTICAL)
        flow_button_sizer.Add(stop_all, (1,0), span=(1,2),
            flag=wx.ALIGN_CENTER_HORIZONTAL|wx.ALIGN_CENTER_VERTICAL)

        basic_flow_box_sizer.Add(flow_sizer, flag=wx.ALL, border=5)
        basic_flow_box_sizer.Add(flow_button_sizer, flag=wx.ALL, border=5)


        info_box_sizer = wx.StaticBoxSizer(wx.VERTICAL, self, 'Flow Info')
        info_parent = info_box_sizer.GetStaticBox()

        self.max_flow_time = wx.StaticText(info_parent, size=(60, -1))
        # self.current_flow_time = wx.StaticText(info_parent, size=(60, -1))

        info_sizer = wx.FlexGridSizer(cols=2, hgap=5, vgap=5)
        # info_sizer.Add(wx.StaticText(info_parent, label='Cur. flow time [s]:'),
        #     flag=wx.ALIGN_CENTER_VERTICAL)
        # info_sizer.Add(self.current_flow_time, flag=wx.ALIGN_CENTER_VERTICAL)
        info_sizer.Add(wx.StaticText(info_parent, label='Max. flow time [s]:'),
            flag=wx.ALIGN_CENTER_VERTICAL)
        info_sizer.Add(self.max_flow_time, flag=wx.ALIGN_CENTER_VERTICAL)

        info_box_sizer.Add(info_sizer)
        info_box_sizer.AddStretchSpacer(1)


        basic_ctrls = wx.BoxSizer(wx.HORIZONTAL)
        basic_ctrls.Add(basic_flow_box_sizer, flag=wx.RIGHT, border=5)
        basic_ctrls.Add(info_box_sizer, flag=wx.EXPAND)


        controls_pane = wx.CollapsiblePane(self, label="Advanced controls")
        controls_pane.Bind(wx.EVT_COLLAPSIBLEPANE_CHANGED, self._on_collapse)
        controls_win = controls_pane.GetPane()

        ctrl_parent = controls_win

        valve_box_sizer = wx.StaticBoxSizer(wx.VERTICAL, ctrl_parent, "Valves")
        valve_parent = valve_box_sizer.GetStaticBox()
        valve_sizer = wx.FlexGridSizer(cols=4, vgap=5, hgap=2)

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

        valve_sizer.Add(wx.StaticText(valve_parent,
            label=self.settings['injection_valve'][0][4]),
            flag=wx.ALIGN_CENTER_VERTICAL)
        valve_sizer.Add(wx.StaticText(valve_parent,
            label=self.settings['sample_valve'][0][4]),
            flag=wx.ALIGN_CENTER_VERTICAL)
        valve_sizer.Add(wx.StaticText(valve_parent,
            label=self.settings['buffer1_valve'][0][4]),
            flag=wx.ALIGN_CENTER_VERTICAL)
        valve_sizer.Add(wx.StaticText(valve_parent,
            label=self.settings['buffer2_valve'][0][4]),
            flag=wx.ALIGN_CENTER_VERTICAL)

        for i in range(num_valves):
            if i < len(self.inj_valve_positions):
                valve_sizer.Add(self.inj_valve_positions[i],
                    flag=wx.ALIGN_CENTER_VERTICAL)
            else:
                valve_sizer.AddSpacer(1)

            if i < len(self.sample_valve_positions):
                valve_sizer.Add(self.sample_valve_positions[i],
                    flag=wx.ALIGN_CENTER_VERTICAL)
            else:
                valve_sizer.AddSpacer(1)

            if i < len(self.buffer1_valve_positions):
                valve_sizer.Add(self.buffer1_valve_positions[i],
                    flag=wx.ALIGN_CENTER_VERTICAL)
            else:
                valve_sizer.AddSpacer(1)

            if i < len(self.buffer2_valve_positions):
                valve_sizer.Add(self.buffer2_valve_positions[i],
                    flag=wx.ALIGN_CENTER_VERTICAL)
            else:
                valve_sizer.AddSpacer(1)

        self.set_valve_position = wx.CheckBox(valve_parent,
            label='Set valve positions on start/refill')
        self.set_valve_position.SetValue(self.settings['auto_set_valves'])

        valve_box_sizer.Add(valve_sizer, flag=wx.ALL, border=2)
        valve_box_sizer.Add(self.set_valve_position, flag=wx.TOP, border=5)
        valve_box_sizer.AddStretchSpacer(1)


        pump_box_sizer = wx.StaticBoxSizer(wx.VERTICAL, ctrl_parent, "Pumps")
        pump_parent = pump_box_sizer.GetStaticBox()

        self.sample_pump_panel = TRPumpPanel(pump_parent, self, self.settings['sample_pump'][0],
            self.settings['sample_pump'][0], self.settings['sample_pump'][1],
            flow_rate=self.settings['sample_pump'][5]['flow_rate'],
            refill_rate=self.settings['sample_pump'][5]['refill_rate'],
            syringe=self.settings['sample_pump'][3][0],
            dual_syringe=self.settings['sample_pump'][5]['dual_syringe'],)
        self.buffer1_pump_panel = TRPumpPanel(pump_parent, self, self.settings['buffer1_pump'][0],
            self.settings['buffer1_pump'][0], self.settings['buffer1_pump'][1],
            flow_rate=self.settings['buffer1_pump'][5]['flow_rate'],
            refill_rate=self.settings['buffer1_pump'][5]['refill_rate'],
            syringe=self.settings['buffer1_pump'][3][0],
            dual_syringe=self.settings['buffer1_pump'][5]['dual_syringe'],)
        self.buffer2_pump_panel = TRPumpPanel(pump_parent, self, self.settings['buffer2_pump'][0],
            self.settings['buffer2_pump'][0], self.settings['buffer2_pump'][1],
            flow_rate=self.settings['buffer2_pump'][5]['flow_rate'],
            refill_rate=self.settings['buffer2_pump'][5]['refill_rate'],
            syringe=self.settings['buffer2_pump'][3][0],
            dual_syringe=self.settings['buffer2_pump'][5]['dual_syringe'],)

        self.pump_panels = {}
        self.pump_panels[self.settings['sample_pump'][0]] = self.sample_pump_panel
        self.pump_panels[self.settings['buffer1_pump'][0]] = self.buffer1_pump_panel
        self.pump_panels[self.settings['buffer2_pump'][0]] = self.buffer2_pump_panel

        pump_sizer = wx.BoxSizer(wx.HORIZONTAL)
        pump_sizer.Add(self.sample_pump_panel, flag=wx.LEFT|wx.RIGHT|wx.TOP|wx.BOTTOM,
            border=5)
        pump_sizer.Add(self.buffer1_pump_panel, flag=wx.TOP|wx.BOTTOM|wx.RIGHT,
            border=5)
        pump_sizer.Add(self.buffer2_pump_panel, flag=wx.TOP|wx.BOTTOM|wx.RIGHT,
            border=5)

        pump_box_sizer.Add(pump_sizer)


        fm_box_sizer = wx.StaticBoxSizer(wx.VERTICAL, ctrl_parent, "Flow Meter")
        fm_parent = fm_box_sizer.GetStaticBox()

        self.outlet_flow = wx.StaticText(fm_parent)
        self.outlet_density = wx.StaticText(fm_parent, size=(60, -1))
        self.outlet_T = wx.StaticText(fm_parent)

        fm_sizer = wx.FlexGridSizer(cols=3, vgap=2, hgap=2)
        fm_sizer.Add(wx.StaticText(fm_parent, label='Flow rate:'),
            flag=wx.ALIGN_CENTER_VERTICAL)
        fm_sizer.Add(self.outlet_flow, flag=wx.ALIGN_CENTER_VERTICAL)
        fm_sizer.Add(wx.StaticText(fm_parent, label=self.settings['flow_units']),
            flag=wx.ALIGN_CENTER_VERTICAL)
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

        fm_box_sizer.Add(fm_sizer, flag=wx.ALL, border=2)
        fm_box_sizer.AddStretchSpacer(1)

        exp_start_box_sizer = wx.StaticBoxSizer(wx.VERTICAL, ctrl_parent, "Exposure Start")
        exp_start_parent = exp_start_box_sizer.GetStaticBox()

        self.start_condition = wx.Choice(exp_start_parent, choices=['Immediately',
            'Fixed delay', 'At flow rate', 'None'])
        self.start_delay = wx.TextCtrl(exp_start_parent, size=(60, -1),
            value=self.settings['autostart_delay'],
            validator=utils.CharValidator('float'))
        self.start_flow = wx.TextCtrl(exp_start_parent, size=(60, -1),
            value=self.settings['autostart_flow'],
            validator=utils.CharValidator('float'))
        self.start_condition.SetStringSelection(self.settings['autostart'])

        exp_start_sizer = wx.FlexGridSizer(cols=2, vgap=5, hgap=5)
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

        exp_start_box_sizer.Add(exp_start_sizer, flag=wx.ALL, border=2)
        exp_start_box_sizer.AddStretchSpacer(1)




        inj_box_sizer = wx.StaticBoxSizer(wx.VERTICAL, ctrl_parent, "Injection")
        inj_parent = inj_box_sizer.GetStaticBox()

        self.autoinject = wx.Choice(inj_parent, choices=['Immediately',
            'After scan', 'None'])
        self.autoinject_scan = wx.TextCtrl(inj_parent, size=(60, -1),
            value=self.settings['autoinject_scan'],
            validator=utils.CharValidator('int'))
        self.autoinject.SetStringSelection(self.settings['autoinject'])

        inj_sizer = wx.FlexGridSizer(cols=2, vgap=5, hgap=5)
        inj_sizer.Add(wx.StaticText(inj_parent, label='Autoinject:'),
            flag=wx.ALIGN_CENTER_VERTICAL)
        inj_sizer.Add(self.autoinject, flag=wx.ALIGN_CENTER_VERTICAL)
        inj_sizer.Add(wx.StaticText(inj_parent, label='Start scan:'),
            flag=wx.ALIGN_CENTER_VERTICAL)
        inj_sizer.Add(self.autoinject_scan, flag=wx.ALIGN_CENTER_VERTICAL)

        inj_box_sizer.Add(inj_sizer, flag=wx.ALL, border=2)
        inj_box_sizer.AddStretchSpacer(1)

        sub_sizer1 = wx.BoxSizer(wx.HORIZONTAL)
        sub_sizer1.Add(valve_box_sizer, flag=wx.RIGHT|wx.EXPAND, border=5)
        sub_sizer1.Add(fm_box_sizer, flag=wx.RIGHT|wx.EXPAND, border=5)
        sub_sizer1.Add(exp_start_box_sizer, flag=wx.RIGHT|wx.EXPAND, border=5)
        sub_sizer1.Add(inj_box_sizer, flag=wx.EXPAND)

        ctrl_top_sizer = wx.BoxSizer(wx.VERTICAL)
        ctrl_top_sizer.Add(sub_sizer1, flag=wx.TOP|wx.BOTTOM, border=5)
        ctrl_top_sizer.Add(pump_box_sizer, flag=wx.BOTTOM, border=5)

        controls_win.SetSizer(ctrl_top_sizer)

        top_sizer = wx.BoxSizer(wx.VERTICAL)
        top_sizer.Add(basic_ctrls, flag=wx.ALL, border=5)
        top_sizer.Add(controls_pane, proportion=1, flag=wx.ALL, border=5)

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
            self.dilution_ratio.Unbind(wx.EVT_KILL_FOCUS)

            dialog = wx.MessageDialog(self, msg, 'Error in flow parameters',
                style=wx.OK|wx.ICON_ERROR)
            dialog.ShowModal()
            dialog.Destroy()

            self.total_flow.Bind(wx.EVT_KILL_FOCUS, self._on_flow_change)
            self.dilution_ratio.Bind(wx.EVT_KILL_FOCUS, self._on_flow_change)


        else:
            if self.chaotic_mixer:
                sample_flow = flow_rate/dilution
                buffer_flow = (flow_rate - sample_flow)/2.

                wx.CallAfter(self.set_pump_panel_flow_rate,
                    self.settings['sample_pump'][0], sample_flow)
                wx.CallAfter(self.set_pump_panel_flow_rate,
                    self.settings['buffer1_pump'][0], buffer_flow)
                wx.CallAfter(self.set_pump_panel_flow_rate,
                    self.settings['buffer2_pump'][0], buffer_flow)

            else:
                buffer_flow = flow_rate/(1+sample_ratio+sheath_ratio)
                sample_flow = buffer_flow*sample_ratio
                sheath_flow = buffer_flow*sheath_ratio

                wx.CallAfter(self.set_pump_panel_flow_rate,
                    self.settings['sample_pump'][0], sample_flow)
                wx.CallAfter(self.set_pump_panel_flow_rate,
                    self.settings['buffer1_pump'][0], buffer_flow)
                wx.CallAfter(self.set_pump_panel_flow_rate,
                    self.settings['buffer2_pump'][0], sheath_flow)

            wx.CallAfter(self.update_flow_info)

    def _on_start_all(self, evt):
        wx.CallAfter(self.start_all)

    def start_all(self):
        logger.info('Starting all pumps')
        self.pause_valve_monitor.set()
        self.pause_pump_monitor.set()

        # success = self.stop_all()

        # if not success:
        #     return

        self.get_all_valve_positions()
        self.get_all_pump_status()


        for pump_panel in self.pump_panels.values():
            pump_status = pump_panel.moving
            pump_volume = float(pump_panel.get_status_volume())

            if pump_status:
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

        if self.set_valve_position.IsChecked():
            valve_list = [
                ('injection_valve', self.inj_valve_positions),
                ('sample_valve', self.sample_valve_positions),
                ('buffer1_valve', self.buffer1_valve_positions),
                ('buffer2_valve', self.buffer2_valve_positions),
                ]
            names = []
            positions = []

            for valves in valve_list:
                basename = valves[0]
                valve_widgets = valves[1]

                for i in range(len(valve_widgets)):
                    names.append('{}_{}'.format(basename, i))
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
                success = pump_panel.run_pump()

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
            valve_list = [
                ('injection_valve', self.inj_valve_positions),
                ('sample_valve', self.sample_valve_positions),
                ('buffer1_valve', self.buffer1_valve_positions),
                ('buffer2_valve', self.buffer2_valve_positions),
                ]
            names = []
            positions = []

            for valves in valve_list:
                basename = valves[0]
                valve_widgets = valves[1]

                for i in range(len(valve_widgets)):
                    names.append('{}_{}'.format(basename, i))
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

    def update_flow_info(self):
        flow_times = []
        try:
            for pump_panel in self.pump_panels.values():
                max_vol = pump_panel.get_max_volume()
                flow_rate = pump_panel.get_flow_rate()
                flow_times.append(max_vol/flow_rate)

            self.max_flow_time.SetLabel('{}'.format(round(min(flow_times)*60, 2)))

            if self.settings['autostart_flow_ratio'] != 0:
                start_flow = float(self.total_flow.GetValue())*self.settings['autostart_flow_ratio']
                self.start_flow.SetValue(str(start_flow))
        except Exception:
            pass

    def _on_position_change(self, evt):
        widget = evt.GetEventObject()
        position = int(widget.GetValue())

        name = None

        if widget in self.inj_valve_positions:
            idx = self.inj_valve_positions.index(widget)
            name = 'injection_valve_{}'.format(idx)

        elif widget in self.sample_valve_positions:
            idx = self.sample_valve_positions.index(widget)
            name = 'sample_valve_{}'.format(idx)

        elif widget in self.buffer1_valve_positions:
            idx = self.buffer1_valve_positions.index(widget)
            name = 'buffer1_valve_{}'.format(idx)

        elif widget in self.buffer2_valve_positions:
            idx = self.buffer2_valve_positions.index(widget)
            name = 'buffer2_valve_{}'.format(idx)

        if name is not None:
            self.change_valve_position(name, position)

    def change_valve_position(self, name, position):
        self.pause_valve_monitor.set()

        cmd = ('set_position', (name, position), {})

        ret = self._send_valvecmd(cmd, True)

        if ret is not None and ret[0] == 'set_position':
            if ret[2]:
                logger.info('Set {} position to {}'.format(name.replace('_', ' '), position))
            else:
                logger.error('Failed to set {} position'.format(name.replace('_', ' ')))
                msg = ('Failed to set {} position'.format(name.replace('_', ' ')))

                wx.CallAfter(self.showMessageDialog, self, msg, 'Set position failed',
                    wx.OK|wx.ICON_ERROR)
        else:
            logger.error('Failed to set {} position, no response from the '
                'server.'.format(ret[1].replace('_', ' ')))
            msg = ('Failed to set {} position, no response from the '
                'server.'.format(ret[1].replace('_', ' ')))

            wx.CallAfter(self.showMessageDialog, self, msg, 'Set position failed',
                    wx.OK|wx.ICON_ERROR)

        wx.CallLater(2000, self.pause_valve_monitor.clear)

    def get_valve_position(self, valve_name):
        cmd = ('get_position', (valve_name,), {})

        position = self._send_valvecmd(cmd, True)

        if position is not None and position[0] == 'position':
            wx.CallAfter(self._set_valve_status, position[1], position[2])

    def get_all_valve_positions(self):
        cmd = ('get_position_multi', ([valve for valve in self.valves],), {})

        ret = self._send_valvecmd(cmd, True)

        if ret is not None and ret[0] == 'multi_positions':
            for i in range(len(ret[1])):
                wx.CallAfter(self._set_valve_status, ret[1][i], ret[2][i])

    def set_multiple_valve_positions(self, valve_names, positions):
        cmd = ('set_position_multi', (valve_names, positions), {})
        ret = self._send_valvecmd(cmd, True)

        if ret is not None and ret[0] == 'set_position_multi':
            success = all(ret[2])
        else:
            success = False

        return success

    def _set_valve_status(self, valve_name, position):
        try:
            position = int(position)

            valve_idx = int(valve_name.split('_')[-1])
            valve = None

            if valve_name.startswith('injection_valve'):
                valve = self.inj_valve_positions[valve_idx]
            elif valve_name.startswith('sample_valve'):
                valve = self.sample_valve_positions[valve_idx]
            elif valve_name.startswith('buffer1_valve'):
                valve = self.buffer1_valve_positions[valve_idx]
            elif valve_name.startswith('buffer2_valve'):
                valve = self.buffer2_valve_positions[valve_idx]

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
                logger.info('{} position changed to {}'.format(valve_name.replace('_', ' ').capitalize(),
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

                if (ret is not None and ret[0] == 'multi_positions'
                    and not self.pause_valve_monitor.is_set()):
                    for i, name in enumerate(ret[1]):
                        wx.CallAfter(self._set_valve_status, name, ret[2][i])

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

            if ret is not None and ret[0] == pump_name and ret[1] == 'start':
                success = ret[1]
            else:
                success = False

        else:
            cmd = ('stop', (pump_name,), {})
            ret = self._send_pumpcmd(cmd, True)

            if (ret is not None and ret[0] == pump_name and ret[1] == 'stop'):
                success = ret[1]
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

            if (ret is not None and ret[0] == pump_name and ret[1] == 'stop'):
                success = ret[1] and success
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

        if ret is not None and ret[0] == pump_name and ret[1] == 'status':
            self.set_pump_moving(pump_name, ret[2][0])
            self.set_pump_status_volume(pump_name, ret[2][1])

    def get_all_pump_status(self):
        names = [pump_name for pump_name in self.pumps]
        cmd = ('get_status_multi', (names,), {})

        ret = self._send_pumpcmd(cmd, True)

        if ret is not None and ret[1] == 'multi_status':
            for i, pump_name in enumerate(ret[0]):
                self.set_pump_moving(pump_name, ret[2][i][0])
                self.set_pump_status_volume(pump_name, ret[2][i][1])

    def _monitor_pump_status(self):
        logger.info('Starting continuous monitoring of pump status')

        monitor_cmd = ('get_status_multi', ([pump for pump in self.pumps],), {})

        while not self.stop_pump_monitor.is_set():
            start_time = time.time()
            if (not self.stop_pump_monitor.is_set() and
                not self.pause_pump_monitor.is_set()):
                ret = self._send_pumpcmd(monitor_cmd, True)

                if (ret is not None and ret[1] == 'multi_status'
                    and not self.pause_pump_monitor.is_set()):
                    for i, name in enumerate(ret[0]):
                        pump_panel = self.pump_panels[name]

                        moving = ret[2][i][0]
                        vol = ret[2][i][1]

                        if moving != pump_panel.moving:
                            wx.CallAfter(self.set_pump_moving, name, moving)
                        if round(float(vol), 3) != float(pump_panel.get_status_volume()):
                            wx.CallAfter(self.set_pump_status_volume, name, vol)

            while time.time() - start_time < self.pump_monitor_interval:
                time.sleep(0.1)

                if self.stop_pump_monitor.is_set():
                    break

        logger.info('Stopping continuous monitoring of pump status')

    def set_pump_status(self, pump_name, status):
       self.pump_panels[pump_name].set_status(status)

    def set_pump_status_volume(self, pump_name, vol):
        self.pump_panels[pump_name].set_status_volume(vol)

    def set_pump_moving(self, pump_name, moving):
        self.pump_panels[pump_name].set_moving(moving)

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

                    if (ret is not None and ret[0] == 'multi_flow'
                        and not self.pause_fm_monitor.is_set()):

                        for i, name in enumerate(ret[1]):
                            flow_rate = ret[2][i]
                            wx.CallAfter(self._set_fm_values, name, flow_rate=flow_rate)
                else:
                    ret = self._send_fmcmd(all_cmd, True)

                    if (ret is not None and ret[0] == 'multi_all'
                        and not self.pause_fm_monitor.is_set()):

                        for i, name in enumerate(ret[1]):
                            flow_rate = ret[2][i][0]
                            density = ret[2][i][1]
                            T = ret[2][i][2]
                            wx.CallAfter(self._set_fm_values, name,
                                flow_rate=flow_rate, density=density, T=T)

                    monitor_all_time = time.time()

            while time.time() - start_time < self.fm_monitor_interval:
                time.sleep(0.1)

                if self.stop_fm_monitor.is_set():
                    break

        logger.info('Stopping continuous monitoring of flow rate')

    def _set_fm_values(self, fm_name, flow_rate=None, density=None, T=None):
        if fm_name == 'outlet_fm':
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
            flow_rate = pump_panel.get_flow_rate()

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
                    errors.append(('Pump {} is moving. All pumps must be '
                        'stopped before starting exposure').format(pump_name))

            for pump_name, pump_panel in self.pump_panels.items():
                pump_volume = float(pump_panel.get_status_volume())

                if pump_volume <= 0:
                    errors.append(('Pump {} has loaded volume <= 0').format(pump_name))

        if autoinject == 'After scan':
            try:
                autoinject_scan = int(autoinject_scan)
            except Exception:
                errors.append('Autoinject scan number must an integer >0')

            if isinstance(autoinject_scan, int):
                if autoinject_scan < 1:
                    errors.append('Autoinject scan number must an integer >0')


        if len(errors) > 0:
            valid = False
            flow_values = {}

            msg = 'The following field(s) have invalid values:'
            for err in errors:
                msg = msg + '\n- ' + err
            msg = msg + ('\n\nPlease correct these errors, then start the scan.')

            wx.CallAfter(wx.MessageBox, msg, 'Error in scan parameters',
                style=wx.OK|wx.ICON_ERROR)

        else:
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
                flow_rate = float(pump_panel.get_flow_rate())

                total_fr = total_fr + flow_rate

                if pump_name == self.settings['sample_pump'][0]:
                    sample_fr = flow_rate
                elif pump_name == self.settings['buffer1_pump'][0]:
                    buffer1_fr = flow_rate
                elif pump_name == self.settings['buffer2_pump'][0]:
                    buffer2_fr = flow_rate

            metadata['Total flow rate [{}]:'.format(flow_units)] = total_fr
            metadata['Dilution ratio:'] = 1./(sample_fr/total_fr)
            metadata['Sample flow rate [{}]:'.format(flow_units)] = sample_fr
            metadata['Buffer 1 flow rate [{}]:'.format(flow_units)] = buffer1_fr
            metadata['Buffer 2 flow rate [{}]:'.format(flow_units)] = buffer2_fr
            metadata['Exposure start setting:'] = start_condition
            if start_condition == 'Fixed delay':
                metadata['Exposure start delay [s]:'] = float(start_delay)
            elif start_condition == 'At flow rate':
                metadata['Exposure start flow rate [{}]:'.format(flow_units)] = float(start_flow)
            metadata['Autoinject start setting:'] = autoinject
            if autoinject == 'After scan':
                metadata['Autoinject after scan:'] = int(autoinject_scan)

        except Exception:
            pass

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
                success = self.start_all()
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
        for i in range(len(self.inj_valve_positions)):
            valve_name = 'injection_valve_{}'.format(i)
            cmd = ('set_position', (valve_name, valve_position), {})
            ret = self._send_valvecmd(cmd, True)

            success = True

            if ret is not None and ret[0] == 'set_position':
                if ret[2]:
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
        flow_cmd = ('get_flow_rate', ('outlet_fm',), {})
        flow_rate = 0
        target_flow_rate = float(target_flow_rate)
        success = True

        start_time = time.time()

        while flow_rate < target_flow_rate:
            ret = self._send_fmcmd(flow_cmd, True)
            if ret is not None and ret[0] == 'flow_rate':
                flow_rate = float(ret[1])

            if self.stop_flow_event.is_set():
                success = False
                break

            if time.time() - start_time > self.fm_monitor_interval:
                wx.CallAfter(self._set_fm_values, 'outlet_fm', flow_rate=flow_rate)
                start_time = time.time()

        self.pause_fm_monitor.clear()

        return success

    def _send_valvecmd(self, cmd, response=False):
        ret_val = None

        if not self.timeout_event.is_set():
            if not self.local_devices:
                full_cmd = {'device': 'valve', 'command': cmd, 'response': response}
            else:
                full_cmd = cmd
            self.valve_cmd_q.append(full_cmd)

            if response:
                with self.valve_ret_lock:
                    while len(self.valve_return_q) == 0 and not self.timeout_event.is_set():
                        time.sleep(0.01)

                    if not self.timeout_event.is_set():
                        ret_val = self.valve_return_q.popleft()
                    else:
                        msg = ('Lost connection to the flow control server. '
                            'Contact your beamline scientist.')
                        wx.CallAfter(self._show_error_dialog, msg, 'Connection error')

                        self.stop_valve_monitor.set()

        else:
            msg = ('No connection to the flow control server. '
                'Contact your beamline scientist.')

            wx.CallAfter(self._show_error_dialog, msg, 'Connection error')

            self.stop_valve_monitor.set()


        return ret_val

    def _send_pumpcmd(self, cmd, response=False):
        ret_val = None

        if not self.timeout_event.is_set():
            if not self.local_devices:
                full_cmd = {'device': 'pump', 'command': cmd, 'response': response}
            else:
                full_cmd = cmd
            self.pump_cmd_q.append(full_cmd)

            if response:
                with self.pump_ret_lock:
                    while len(self.pump_return_q) == 0 and not self.timeout_event.is_set():
                        time.sleep(0.01)

                    if not self.timeout_event.is_set():
                        ret_val = self.pump_return_q.popleft()
                    else:
                        msg = ('Lost connection to the flow control server. '
                            'Contact your beamline scientist.')
                        wx.CallAfter(self._show_error_dialog, msg, 'Connection error')

                        self.stop_pump_monitor.set()

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
        ret_val = (None, None)
        if not self.timeout_event.is_set():
            if not self.local_devices:
                full_cmd = {'device': 'fm', 'command': cmd, 'response': response}
            else:
                full_cmd = cmd
            self.fm_cmd_q.append(full_cmd)

            if response:
                with self.fm_ret_lock:
                    while len(self.fm_return_q) == 0 and not self.timeout_event.is_set():
                        time.sleep(0.01)

                    if not self.timeout_event.is_set():
                        ret_val = self.fm_return_q.popleft()

                    else:
                        msg = ('Lost connection to the flow control server. '
                            'Contact your beamline scientist.')
                        wx.CallAfter(self.showMessageDialog, self, msg, 'Connection error',
                            wx.OK|wx.ICON_ERROR)

                        self.stop_fm_monitor.set()

        else:
            msg = ('No connection to the flow control server. '
                'Contact your beamline scientist.')

            wx.CallAfter(self.showMessageDialog, self, msg, 'Connection error',
                wx.OK|wx.ICON_ERROR)

            self.stop_fm_monitor.set()

        return ret_val

    def _show_error_dialog(self, msg, title):
        if self.error_dialog is None:
            self.error_dialog = utils.WarningMessage(self, msg, title)
            self.error_dialog.Show()

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
        rise_tau=15
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
                        if in_pos_count > 0:
                            in_pos = True
                        else:
                            in_pos = False

                        fraction = in_pos_count/len(valve_widgets)

                        valves_in_pos[valve_type] = (in_pos, fraction)

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

                if pump_name is not None and in_pos:
                    flow_rate = self.pump_panels[pump_name].get_flow_rate()
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

            cmd = ('set_flow_rate', ('outlet_fm', current_flow), {})
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

        if not self.timeout_event.is_set():
            for valve in self.valves:
                self._send_valvecmd(('disconnect', (valve,), {}), True)
            for pump in self.pumps:
                self._send_pumpcmd(('disconnect', (pump,), {}), True)
            for fm in self.fms:
                self._send_fmcmd(('disconnect', (fm,), {}), True)

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
    This pump panel supports standard flow controls and settings, including
    connection settings, for a pump. It is meant to be embedded in a larger application
    and can be instanced several times, once for each pump. It communciates
    with the pumps using the :py:class:`PumpCommThread`. Currently it only supports
    the :py:class:`M50Pump`, but it should be easy to extend for other pumps. The
    only things that should have to be changed are the are adding in pump-specific
    settings, modeled after how the ``m50_pump_sizer`` is constructed in the
    :py:func:`_create_layout` function, and then add in type switching in the
    :py:func:`_on_type` function.
    """
    def __init__(self, parent, tr_panel, panel_name, pump_name, pump_type, flow_rate='',
        refill_rate='', syringe=None, dual_syringe=False):
        """
        Initializes the custom thread. Important parameters here are the
        list of known commands ``_commands`` and known pumps ``known_pumps``.

        :param wx.Window parent: Parent class for the panel.

        :param int panel_id: wx ID for the panel.

        :param str panel_name: Name for the panel

        :param list all_comports: A list containing all comports that the pump
            could be connected to.

        :param collections.deque pump_cmd_q: The ``pump_cmd_q`` that was passed to
            the :py:class:`PumpCommThread`.

        :param list known_pumps: The list of known pump types, obtained from
            the :py:class:`PumpCommThread`.

        :param str pump_name: An identifier for the pump, displayed in the pump
            panel.

        :param str pump_type: One of the ``known_pumps``, corresponding to the pump
            connected to this panel. Only required if you are connecting the pump
            when the panel is first set up (rather than manually later).

        :param str comport: The comport the pump is connected to. Only required
            if you are connecting the pump when the panel is first set up (rather
            than manually later).

        :param list pump_args: Pump specific arguments for initialization.
            Only required if you are connecting the pump when the panel is first
            set up (rather than manually later).

        :param dict pump_kwargs: Pump specific keyword arguments for initialization.
            Only required if you are connecting the pump when the panel is first
            set up (rather than manually later).

        :param str pump_mode: Either 'continous' for continous flow pumps or
            'syringe' for syringe pumps.Only required if you are connecting the
            pump when the panel is first set up (rather than manually later).

        :param treading.Lock comm_lock: Used for pump communication, prevents
            multiple access on serial ports for pumps in a daisy chain.

        """

        wx.Panel.__init__(self, parent, name=panel_name)
        logger.debug('Initializing PumpPanel for pump %s', pump_name)

        self.tr_flow_panel = tr_panel
        self.name = pump_name
        self.pump_type = pump_type
        self.connected = False
        self.moving = False
        self.syringe_volume_val = 0
        self.pump_direction = 'Dispense'

        self.known_syringes = {'30 mL, EXEL': {'diameter': 23.5, 'max_volume': 30,
            'max_rate': 70},
            '3 mL, Medline P.C.': {'diameter': 9.1, 'max_volume': 3.0,
            'max_rate': 11},
            '6 mL, Medline P.C.': {'diameter': 12.8, 'max_volume': 6,
            'max_rate': 23},
            '10 mL, Medline P.C.': {'diameter': 16.564, 'max_volume': 10,
            'max_rate': 31},
            '20 mL, Medline P.C.': {'diameter': 20.3, 'max_volume': 20,
            'max_rate': 55},
            '0.25 mL, Hamilton Glass': {'diameter': 2.30, 'max_volume': 0.25,
            'max_rate': 11},
            '0.5 mL, Hamilton Glass': {'diameter': 3.26, 'max_volume': 0.5,
            'max_rate': 11},
            '1.0 mL, Hamilton Glass': {'diameter': 4.61, 'max_volume': 1.0,
            'max_rate': 11},
            }

        self._create_layout(flow_rate, refill_rate, syringe, dual_syringe)

    def _create_layout(self, flow_rate='', refill_rate='', syringe=None,
        dual_syringe=False):
        """Creates the layout for the panel."""
        top_sizer = wx.StaticBoxSizer(wx.VERTICAL, self, self.name)
        parent = top_sizer.GetStaticBox()

        self.status = wx.StaticText(parent, label='Not connected')
        self.syringe_volume = wx.StaticText(parent, label='0', size=(50,-1),
            style=wx.ST_NO_AUTORESIZE)
        self.syringe_volume_label = wx.StaticText(parent, label='Current volume:')
        self.syringe_volume_units = wx.StaticText(parent, label='mL')
        self.set_syringe_volume = wx.Button(parent, label='Set Current Volume')
        self.set_syringe_volume.Bind(wx.EVT_BUTTON, self._on_set_volume)
        self.syringe_vol_gauge = wx.Gauge(parent, size=(40, -1),
            style=wx.GA_HORIZONTAL|wx.GA_SMOOTH)
        self.syringe_vol_gauge_low = wx.StaticText(parent, label='0')
        self.syringe_vol_gauge_high = wx.StaticText(parent, label='')

        self.vol_gauge = wx.BoxSizer(wx.HORIZONTAL)
        self.vol_gauge.Add(self.syringe_vol_gauge_low,
            flag=wx.ALIGN_CENTER_VERTICAL)
        self.vol_gauge.Add(self.syringe_vol_gauge, 1, border=2,
            flag=wx.LEFT|wx.ALIGN_CENTER_VERTICAL|wx.EXPAND)
        self.vol_gauge.Add(self.syringe_vol_gauge_high, border=2,
            flag=wx.LEFT|wx.ALIGN_CENTER_VERTICAL)

        status_grid = wx.GridBagSizer(vgap=5, hgap=5)
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

        self.status_sizer = wx.StaticBoxSizer(wx.StaticBox(parent, label='Info'),
            wx.VERTICAL)
        self.status_sizer.Add(status_grid, 1, flag=wx.ALL|wx.EXPAND, border=2)

        syr_types = sorted(self.known_syringes.keys(), key=lambda x: float(x.split()[0]))
        self.syringe_type = wx.Choice(parent, choices=syr_types)
        self.mode_ctrl = wx.Choice(parent, choices=['Continuous flow', 'Fixed volume'])
        self.mode_ctrl.SetSelection(0)
        self.direction_ctrl = wx.Choice(parent, choices=['Dispense', 'Aspirate'])
        self.direction_ctrl.SetSelection(0)
        self.flow_rate_ctrl = wx.TextCtrl(parent, value=flow_rate, size=(60,-1),
            style=wx.TE_PROCESS_ENTER, validator=utils.CharValidator('float_te'))
        self.flow_units_lbl = wx.StaticText(parent, label=self.tr_flow_panel.settings['flow_units'])
        self.refill_rate_lbl = wx.StaticText(parent, label='Refill rate:')
        self.refill_rate_ctrl = wx.TextCtrl(parent, value=refill_rate, size=(60,-1),
            validator=utils.CharValidator('float'))
        self.refill_rate_units = wx.StaticText(parent, label=self.tr_flow_panel.settings['flow_units'][:2])
        self.volume_lbl = wx.StaticText(parent, label='Volume:')
        self.volume_ctrl = wx.TextCtrl(parent, size=(60,-1),
            validator=utils.CharValidator('float'))
        self.vol_units_lbl = wx.StaticText(parent, label=self.tr_flow_panel.settings['flow_units'][:2])
        self.dual_syringe = wx.Choice(parent, choices=['True', 'False'])
        self.dual_syringe.SetStringSelection(str(dual_syringe))

        self.flow_rate_ctrl.Bind(wx.EVT_KILL_FOCUS, self._on_fr_setting_change)
        self.flow_rate_ctrl.Bind(wx.EVT_TEXT_ENTER, self._on_fr_setting_change)
        self.direction_ctrl.Bind(wx.EVT_CHOICE, self._on_direction_change)

        if syringe is not None and syringe in syr_types:
            self.syringe_type.SetStringSelection(syringe)
        else:
            self.syringe_type.SetSelection(0)
        self.syringe_type.Bind(wx.EVT_CHOICE, self._on_syringe_type)

        self.dual_syringe.Bind(wx.EVT_CHOICE, self._on_dual_syringe)

        self.mode_ctrl.Bind(wx.EVT_CHOICE, self._on_mode)

        basic_ctrl_sizer = wx.GridBagSizer(vgap=2, hgap=2)
        basic_ctrl_sizer.Add(wx.StaticText(parent, label='Syringe:'), (0,0),
            flag=wx.ALIGN_CENTER_VERTICAL)
        basic_ctrl_sizer.Add(self.syringe_type, (0,1), span=(1,2),
            flag=wx.ALIGN_CENTER_VERTICAL)
        basic_ctrl_sizer.Add(wx.StaticText(parent, label='Dual syringe:'), (1,0),
            flag=wx.ALIGN_CENTER_VERTICAL)
        basic_ctrl_sizer.Add(self.dual_syringe, (1,1), span=(1,2),
            flag=wx.ALIGN_CENTER_VERTICAL)
        basic_ctrl_sizer.Add(wx.StaticText(parent, label='Mode:'), (2,0),
            flag=wx.ALIGN_CENTER_VERTICAL)
        basic_ctrl_sizer.Add(self.mode_ctrl, (2,1), span=(1,2),
            flag=wx.ALIGN_CENTER_VERTICAL)
        basic_ctrl_sizer.Add(wx.StaticText(parent, label='Direction:'), (3,0),
            flag=wx.ALIGN_CENTER_VERTICAL)
        basic_ctrl_sizer.Add(self.direction_ctrl, (3,1), span=(1,2),
            flag=wx.ALIGN_CENTER_VERTICAL)
        basic_ctrl_sizer.Add(wx.StaticText(parent, label='Flow rate:'), (4,0),
            flag=wx.ALIGN_CENTER_VERTICAL)
        basic_ctrl_sizer.Add(self.flow_rate_ctrl, (4,1),
            flag=wx.ALIGN_CENTER_VERTICAL)
        basic_ctrl_sizer.Add(self.flow_units_lbl, (4,2),
            flag=wx.ALIGN_CENTER_VERTICAL|wx.ALIGN_LEFT)
        basic_ctrl_sizer.Add(self.refill_rate_lbl, (5,0),
            flag=wx.ALIGN_CENTER_VERTICAL)
        basic_ctrl_sizer.Add(self.refill_rate_ctrl, (5,1),
            flag=wx.ALIGN_CENTER_VERTICAL)
        basic_ctrl_sizer.Add(self.refill_rate_units, (5,2),
            flag=wx.ALIGN_CENTER_VERTICAL|wx.ALIGN_LEFT)
        basic_ctrl_sizer.Add(self.volume_lbl, (6,0),
            flag=wx.RESERVE_SPACE_EVEN_IF_HIDDEN|wx.ALIGN_CENTER_VERTICAL)
        basic_ctrl_sizer.Add(self.volume_ctrl, (6,1),
            flag=wx.RESERVE_SPACE_EVEN_IF_HIDDEN|wx.ALIGN_CENTER_VERTICAL)
        basic_ctrl_sizer.Add(self.vol_units_lbl, (6,2),
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
        self.control_box_sizer.Add(basic_ctrl_sizer, flag=wx.EXPAND|wx.TOP|wx.LEFT|wx.RIGHT, border=2)
        self.control_box_sizer.Add(button_ctrl_sizer, flag=wx.ALIGN_CENTER_HORIZONTAL|wx.ALL, border=2)

        top_sizer.Add(self.status_sizer, flag=wx.EXPAND)
        top_sizer.Add(self.control_box_sizer, border=5, flag=wx.EXPAND|wx.TOP)

        self.volume_lbl.Hide()
        self.volume_ctrl.Hide()
        self.vol_units_lbl.Hide()
        self.fr_button.Hide()

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

        try:
            refillr = float(self.refill_rate_ctrl.GetValue())
        except Exception:
            msg = "Refill rate must be a number."
            wx.MessageBox(msg, "Error setting refill rate")
            cont = False

        if self.pump_direction == 'Dispense':
            dispense = True
        else:
            dispense = False

        if cont:
            if self.pump_mode == 'continuous':
                success = self.tr_flow_panel.set_flow_rate(self.name, flowr,
                    self.pump_mode, dispense)
            else:
                if self.pump_type == 'NE 500':
                    if dispense:
                        success = self.tr_flow_panel.set_flow_rate(self.name, flowr,
                            self.pump_mode, dispense)
                    else:
                        success = self.tr_flow_panel.set_refill_rate(self.name, refillr,
                            self.pump_mode, dispense)

                else:
                    success = self.tr_flow_panel.set_flow_rate(self.name, flowr,
                            self.pump_mode, dispense)
                    success = self.tr_flow_panel.set_refill_rate(self.name, refillr,
                            self.pump_mode, dispense)
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
                        wx.CallAfter(self.showMessageDialog, self, msg, "Error setting volume",
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
            wx.CallAfter(self.showMessageDialog, self, msg, "Error starting flow",
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

    def set_status(self, status):
        """
        Changes the status in the GUI.

        :param str status: The status to display.
        """
        logger.debug('Setting pump %s status to %s', self.name, status)
        self.status.SetLabel(status)

    def get_status(self):
        return self.status.GetLabel()

    def get_status_volume(self):
        return self.syringe_volume_val

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

    def _set_status_volume(self, volume):
        logger.debug("Setting pump %s volume to %s", self.name, volume)
        self.syringe_volume.SetLabel('{}'.format(round(float(volume), 3)))

    def set_moving(self, moving):
        if moving != self.moving:
            self.moving = moving
            wx.CallAfter(self.on_pump_run)

    def get_moving(self):
        return self.moving

    def _on_syringe_type(self, evt):
        vals = copy.deepcopy(self.known_syringes[self.syringe_type.GetStringSelection()])
        vals['syringe_id'] = self.syringe_type.GetStringSelection()
        self.tr_flow_panel.set_pump_cal(self.name, vals)

        max_vol = self.known_syringes[self.syringe_type.GetStringSelection()]['max_volume']
        self.syringe_vol_gauge_high.SetLabel(str(max_vol))
        self.syringe_vol_gauge.SetRange(int(round(float(max_vol)*1000)))

    def _on_dual_syringe(self, evt):
        self.tr_flow_panel.set_pump_dual_syringe_type(self.name,
            self.dual_syringe.GetStringSelection()=='True')

    def get_dual_syringe(self):
        return self.dual_syringe.GetStringSelection()=='True'

    def set_pump_direction(self, dispense):
        if dispense:
            self.pump_direction = 'Dispense'
            ret = wx.CallAfter(self.direction_ctrl.SetStringSelection, 'Dispense')
        else:
            self.pump_direction = 'Aspirate'
            ret = wx.CallAfter(self.direction_ctrl.SetStringSelection, 'Aspirate')


    def get_pump_direction(self):
        return self.pump_direction

    def get_max_volume(self):
        max_vol = float(self.known_syringes[self.syringe_type.GetStringSelection()]['max_volume'])

        return max_vol

    def get_flow_rate(self):
        flow_rate = float(self.flow_rate_ctrl.GetValue())

        return flow_rate




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
    settings = {
        'components'            : ['time resolved'],
        'position_units'        : 'mm',
        'speed_units'           : 'mm/s',
        'accel_units'           : 'mm/s^2',
        'time_units'            : 's',
        'x_start'               : 0,
        'x_end'                 : 10,
        'y_start'               : 0,
        'y_end'                 : 0,
        'scan_speed'            : 1,
        'num_scans'             : 1,
        'return_speed'          : 1,
        'scan_acceleration'     : 1,
        'return_acceleration'   : 1,
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
        # 'encoder_resolution'    : D('0.000001'), #for XMS160, in mm
        # 'encoder_precision'     : 6, #Number of significant decimals in encoder value
        'encoder_resolution'    : D('0.00001'), #for GS30V, in mm
        'encoder_precision'     : 5, #Number of significant decimals in encoder value
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
        'device_communication'  : 'remote',
        'injection_valve'       : [('Rheodyne', 'COM6', [], {'positions' : 2}, 'Injection'),], #Chaotic flow
        'sample_valve'          : [('Rheodyne', 'COM9', [], {'positions' : 6}, 'Sample'),],
        'buffer1_valve'         : [('Rheodyne', 'COM8', [], {'positions' : 6}, 'Buffer 1'),],
        'buffer2_valve'         : [('Rheodyne', 'COM7', [], {'positions' : 6}, 'Buffer 2'),],
        'sample_pump'           : ('Sample', 'PHD 4400', 'COM4',
            ['10 mL, Medline P.C.', '1'], {}, {'flow_rate' : '5',
            'refill_rate' : '5', 'dual_syringe': False}),
        'buffer1_pump'           : ('Buffer 1', 'PHD 4400', 'COM4',
            ['20 mL, Medline P.C.', '2'], {}, {'flow_rate' : '10',
            'refill_rate' : '10', 'dual_syringe': False}),
        'buffer2_pump'          : ('Buffer 2', 'PHD 4400', 'COM4',
            ['20 mL, Medline P.C.', '3'], {}, {'flow_rate' : '10',
            'refill_rate' : '10', 'dual_syringe': False}),
        'outlet_fm'             : ('BFS', 'COM5', [], {}),
        # 'injection_valve'       : [('Rheodyne', 'COM6', [], {'positions' : 2}, 'Injection'),], #Laminar flow
        # 'sample_valve'          : [('Rheodyne', 'COM9', [], {'positions' : 6}, 'Sample'),],
        # 'buffer1_valve'         : [('Rheodyne', 'COM8', [], {'positions' : 6}, 'Buffer'),],
        # 'buffer2_valve'         : [('Rheodyne', 'COM7', [], {'positions' : 6}, 'Sheath'),],
        # 'buffer1_pump'           : ('Buffer', 'NE 500', 'COM11',
        #     ['20 mL, Medline P.C.', '00'], {}, {'flow_rate' : '10',
        #     'refill_rate' : '10', 'dual_syringe': False}),
        # 'buffer2_pump'          : ('Sheath', 'NE 500', 'COM10',
        #     ['20 mL, Medline P.C.', '01'], {}, {'flow_rate' : '10',
        #     'refill_rate' : '10', 'dual_syringe': False}),
        # 'sample_pump'           : ('Sample', 'NE 500', 'COM3',
        #     ['10 mL, Medline P.C.', '02'], {}, {'flow_rate' : '0.1',
        #     'refill_rate' : '10', 'dual_syringe': False}),
        # 'outlet_fm'             : ('BFS', 'COM13', [], {}),
        # 'device_communication'  : 'local',                                                    # Simulated
        # 'injection_valve'       : [('Soft', '', [], {'positions' : 2}, 'Injection'),],
        # 'sample_valve'          : [('Soft', '', [], {'positions' : 6}, 'Sample'),],
        # 'buffer1_valve'         : [('Soft', '', [], {'positions' : 6}, 'Buffer'),],
        # 'buffer2_valve'         : [('Soft', '', [], {'positions' : 6}, 'Sheath'),],
        # 'sample_pump'           : ('Sample', 'Soft Syringe', '',   
        #     ['10 mL, Medline P.C.',], {}, {'flow_rate' : '5',
        #     'refill_rate' : '20', 'dual_syringe' : False}),
        # 'buffer1_pump'           : ('Buffer 1', 'Soft Syringe', '',
        #     ['20 mL, Medline P.C.',], {}, {'flow_rate' : '10',
        #     'refill_rate' : '40', 'dual_syringe' : False}),
        # 'buffer2_pump'          : ('Buffer 2', 'Soft Syringe', '',
        #     ['20 mL, Medline P.C.',], {}, {'flow_rate' : '10',
        #     'refill_rate' : '40', 'dual_syringe' : False}),
        # 'injection_valve'       : [('Soft', '', [], {'positions' : 2}, 'Injection'),],
        # 'sample_valve'          : [('Soft', '', [], {'positions' : 6}, 'Sample'),],
        # 'buffer1_valve'         : [('Soft', '', [], {'positions' : 6}, 'Buffer'),
        #                             ('Soft', '', [], {'positions' : 6}, 'Buffer')],
        # 'buffer2_valve'         : [('Soft', '', [], {'positions' : 6}, 'Sheath'),
        #                             ('Soft', '', [], {'positions' : 6}, 'Sheath')],
        # 'sample_pump'           : ('Sample', 'Soft Syringe', '',
        #     ['10 mL, Medline P.C.',], {}, {'flow_rate' : '5',
        #     'refill_rate' : '20', 'dual_syringe' : False}),
        # 'buffer1_pump'           : ('Buffer', 'Soft Syringe', '',
        #     ['20 mL, Medline P.C.',], {}, {'flow_rate' : '10',
        #     'refill_rate' : '40', 'dual_syringe' : True}),
        # 'buffer2_pump'          : ('Sheath', 'Soft Syringe', '',
        #     ['20 mL, Medline P.C.',], {}, {'flow_rate' : '10',
        #     'refill_rate' : '40', 'dual_syringe' : True}),
        # 'outlet_fm'             : ('Soft', '', [], {}),
        'flow_units'            : 'mL/min',
        'total_flow_rate'       : '1.5', # For laminar flow
        # 'total_flow_rate'       : '6', # For chaotic flow
        'dilution_ratio'        : '10', # For chaotic flow
        'max_flow'              : 8, # For chaotic flow
        'max_dilution'          : 50, # For chaotic flow
        'auto_set_valves'       : True,
        'valve_start_positions' : {'sample_valve' : 1, 'buffer1_valve': 1,
            'buffer2_valve' : 1, 'injection_valve' : 1},
        'valve_refill_positions': {'sample_valve' : 2, 'buffer1_valve': 2,
            'buffer2_valve' : 2, 'injection_valve' : 1},
        'autostart'             : 'At flow rate',
        'autostart_flow'        : '4.5',
        'autostart_flow_ratio'  : 0.75,
        'autostart_delay'       : '0',
        'autoinject'            : 'After scan',
        'autoinject_scan'       : '5',
        'autoinject_valve_pos'  : 1,
        'mixer_type'            : 'laminar', # laminar or chaotic
        'sample_ratio'          : '0.066', # For laminar flow
        'sheath_ratio'          : '0.032', # For laminar flow
        'simulated'             : False, # VERY IMPORTANT. MAKE SURE THIS IS FALSE FOR EXPERIMENTS
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
    frame = TRFrame(settings, 'scan', None, title='TRSAXS Control')
    frame.Show()
    app.MainLoop()



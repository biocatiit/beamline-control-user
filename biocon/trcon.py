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

from collections import OrderedDict, deque
import logging
import sys
import math
from decimal import Decimal as D
import time
import threading

if __name__ != '__main__':
    logger = logging.getLogger(__name__)

import wx

import motorcon
import XPS_C8_drivers as xps_drivers
import utils

class TRPanel(wx.Panel):
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

        super(TRPanel, self).__init__(*args, **kwargs)
        logger.debug('Initializing TRPanel')

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

        self.x_start = wx.TextCtrl(self, size=(60, -1),
            validator=utils.CharValidator('float_neg'))
        self.x_end = wx.TextCtrl(self, size=(60, -1),
            validator=utils.CharValidator('float_neg'))
        self.y_start = wx.TextCtrl(self, size=(60, -1),
            validator=utils.CharValidator('float_neg'))
        self.y_end = wx.TextCtrl(self, size=(60, -1),
            validator=utils.CharValidator('float_neg'))

        self.x_start.Bind(wx.EVT_TEXT, self._on_param_change)
        self.x_end.Bind(wx.EVT_TEXT, self._on_param_change)
        self.y_start.Bind(wx.EVT_TEXT, self._on_param_change)
        self.y_end.Bind(wx.EVT_TEXT, self._on_param_change)

        scan_sizer = wx.FlexGridSizer(rows=3, cols=3, vgap=5, hgap=10)
        scan_sizer.AddSpacer(1)
        scan_sizer.Add(wx.StaticText(self, label='Start [{}]'.format(pos_units)),
            flag=wx.ALIGN_CENTER_HORIZONTAL)
        scan_sizer.Add(wx.StaticText(self, label='End [{}]'.format(pos_units)),
            flag=wx.ALIGN_CENTER_HORIZONTAL)
        scan_sizer.Add(wx.StaticText(self, label='X'),
            flag=wx.ALIGN_CENTER_VERTICAL)
        scan_sizer.Add(self.x_start, flag=wx.ALIGN_CENTER_VERTICAL)
        scan_sizer.Add(self.x_end, flag=wx.ALIGN_CENTER_VERTICAL)
        scan_sizer.Add(wx.StaticText(self, label='Y'),
            flag=wx.ALIGN_CENTER_VERTICAL)
        scan_sizer.Add(self.y_start, flag=wx.ALIGN_CENTER_VERTICAL)
        scan_sizer.Add(self.y_end, flag=wx.ALIGN_CENTER_VERTICAL)

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

        self.return_speed.Bind(wx.EVT_TEXT, self._on_param_change)
        self.scan_acceleration.Bind(wx.EVT_TEXT, self._on_param_change)
        self.return_acceleration.Bind(wx.EVT_TEXT, self._on_param_change)
        self.scan_start_offset_dist.Bind(wx.EVT_TEXT, self._on_param_change)
        self.scan_end_offset_dist.Bind(wx.EVT_TEXT, self._on_param_change)
        self.constant_scan_speed.Bind(wx.EVT_CHECKBOX, self._on_param_change)

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
                    motor.set_position_compare_pulse(x_motor, pco_pulse_width, pco_encoder_settle_t)

            motor.set_velocity(x_motor, 0, return_speed)
            motor.set_velocity(y_motor, 1, return_speed)
            motor.set_acceleration(x_motor, 0, return_accel)
            motor.set_acceleration(y_motor, 1, return_accel)

            motor_cmd_q.append(('move_absolute', ('TR_motor', (x_start, y_start)), {}))

            for current_run in range(1,num_runs+1):
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

                if motor_type == 'Newport_XPS':
                    if pco_direction == 'x':
                        logger.debug('starting x pco')
                        motor.start_position_compare(x_motor)
                    else:
                        logger.debug('starting x pco')
                        motor.start_position_compare(y_motor)

                if vect_scan_speed[0] != 0:
                    motor.set_velocity(x_motor, 0, vect_scan_speed[0])
                if vect_scan_speed[1] != 0:
                    motor.set_velocity(y_motor, 1, vect_scan_speed[1])
                if vect_scan_accel[0] != 0:
                    motor.set_acceleration(x_motor, 0, vect_scan_accel[0])
                if vect_scan_accel[1] != 0:
                    motor.set_acceleration(y_motor, 1, vect_scan_accel[1])

                motor_cmd_q.append(('move_absolute', ('TR_motor', (x_end, y_end)), {}))

                logger.info('Scan %s started', current_run)

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
                    motor.set_velocity(x_motor, 0, vect_return_speed[0])
                if vect_return_speed[1] != 0:
                    motor.set_velocity(y_motor, 1, vect_return_speed[1])
                if vect_return_accel[0] != 0:
                    motor.set_acceleration(x_motor, 0, vect_return_accel[0])
                if vect_return_accel[1] != 0:
                    motor.set_acceleration(y_motor, 1, vect_return_accel[1])

                motor_cmd_q.append(('move_absolute', ('TR_motor', (x_start, y_start)), {}))

        self.test_scan.SetLabel('Run test')


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
                    self.scan_start_offset_dist.ChangeValue(str(round(scan_start_offset_dist, 3)))
                    self.scan_end_offset_dist.ChangeValue(str(round(scan_end_offset_dist, 3)))
            except ValueError:
                calc = False

        elif calc:
            try:
                scan_start_offset_dist = float(self.scan_start_offset_dist.GetValue())
                scan_end_offset_dist = float(self.scan_end_offset_dist.GetValue())
            except ValueError:
                calc = False

        if (calc and scan_speed != 0 and return_speed !=0 and
            scan_acceleration != 0 and return_acceleration !=0):
            (scan_length, total_length, time_per_scan, return_time,
                total_time) = self._calc_scan_params(x_start, x_end, y_start,
                y_end, scan_speed, return_speed, scan_acceleration,
                return_acceleration, scan_start_offset_dist, scan_end_offset_dist,
                num_scans)

            self.scan_length.SetLabel(str(round(scan_length, 3)))
            self.total_length.SetLabel(str(round(total_length, 3)))
            self.scan_time.SetLabel(str(round(time_per_scan, 3)))
            self.return_time.SetLabel(str(round(return_time, 3)))
            self.total_scan_time.SetLabel(str(round(total_time, 3)))

            try:
                return_vals = self._calc_exposure_params()
            except Exception:
                return_vals=[['calc_exposure_params_error'],]

            if len(return_vals[0]) == 0:
                num_images = return_vals[1]
                self.num_images.SetLabel(str(num_images))

                if 'exposure' in self.settings['components']:
                    exp_panel = wx.FindWindowByName('exposure')
                    exp_panel.set_exp_settings({'num_frames': num_images})

    def _calc_scan_params(self, x_start, x_end, y_start, y_end, scan_speed,
        return_speed, scan_acceleration, return_acceleration,
        scan_start_offset_dist, scan_end_offset_dist, num_scans):

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

                    (x_pco_step,
                        y_pco_step,
                        vect_scan_speed,
                        vect_scan_accel,
                        vect_return_speed,
                        vect_return_accel) = self._calc_pco_params(x_start,
                        x_end, y_start, y_end, scan_speed, return_speed,
                        scan_acceleration, return_acceleration, delta_t)

                    if self.settings['pco_direction'] == 'x':
                        pco_step = x_pco_step
                        pco_start = x_start
                        pco_end = x_end
                        pco_speed = vect_scan_speed[0]
                    else:
                        pco_step = y_pco_step
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

            (scan_length, total_length, time_per_scan, return_time,
                total_time) = self._calc_scan_params(x_start, x_end, y_start,
                y_end, scan_speed, return_speed, scan_acceleration,
                return_acceleration, scan_start_offset_dist, scan_end_offset_dist,
                num_scans)

            metadata['Scan length [{}]:'.format(pos_units)] = scan_length
            metadata['Number of scans:'] = num_scans
            metadata['Time per scan [{}]:'.format(time_units)] = time_per_scan
            metadata['Return time [{}]:'.format(time_units)] = return_time
            metadata['Total time [{}]:'.format(time_units)] = total_time
            metadata['X scan start [{}]:'.format(pos_units)] = x_start
            metadata['X scan end [{}]:'.format(pos_units)] = x_end
            metadata['Y scan start:'.format(pos_units)] = y_start
            metadata['Y scan end [{}]:'.format(pos_units)] = y_end
            metadata['Scan speed [{}]:'.format(speed_units)] = scan_speed
            metadata['Return speed [{}]:'.format(speed_units)] = return_speed
            metadata['Scan acceleration [{}]:'.format(accel_units)] = scan_acceleration
            metadata['Return acceleration [{}]:'.format(accel_units)] = return_acceleration
            metadata['Scan start offset [{}]:'.format(pos_units)] = scan_start_offset_dist
            metadata['Scan end offset [{}]:'.format(pos_units)] = scan_end_offset_dist
        except (ValueError, ZeroDivisionError):
            pass

        return metadata

    def get_scan_values(self):
        valid = True

        x_start = self.x_start.GetValue()
        x_end = self.x_end.GetValue()
        y_start = self.y_start.GetValue()
        y_end = self.y_end.GetValue()
        scan_speed = self.scan_speed.GetValue()
        num_scans = self.num_scans.GetValue()
        return_speed = self.return_speed.GetValue()
        scan_acceleration = self.scan_acceleration.GetValue()
        return_acceleration = self.return_acceleration.GetValue()
        scan_start_offset_dist = self.scan_start_offset_dist.GetValue()
        scan_end_offset_dist = self.scan_end_offset_dist.GetValue()

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

            tot_dist = math.sqrt((x_end - x_start)**2 + (y_end-y_start)**2)
            if tot_dist != 0:
                x_prop = abs((x_end - x_start)/tot_dist)
            else:
                x_prop = 1

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

        if (isinstance(scan_end_offset_dist, float) and isinstance(x_start, float)
            and isinstance(x_end, float) and isinstance(y_start, float) and
            isinstance(y_end, float)):

            tot_dist = math.sqrt((x_end - x_start)**2 + (y_end-y_start)**2)
            if tot_dist != 0:
                x_prop = abs((x_end - x_start)/tot_dist)
            else:
                x_prop = 1

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

        if (isinstance(scan_start_offset_dist, float) and isinstance(x_start, float)
            and isinstance(x_end, float) and isinstance(y_start, float) and
            isinstance(y_end, float)):

            tot_dist = math.sqrt((x_end - x_start)**2 + (y_end-y_start)**2)
            if tot_dist != 0:
                y_prop = abs((y_end-y_start)/tot_dist)
            else:
                y_prop = 1

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

        if (isinstance(scan_end_offset_dist, float) and isinstance(x_start, float)
            and isinstance(x_end, float) and isinstance(y_start, float) and
            isinstance(y_end, float)):

            tot_dist = math.sqrt((x_end - x_start)**2 + (y_end-y_start)**2)
            if tot_dist != 0:
                y_prop = abs((y_end-y_start)/tot_dist)
            else:
                y_prop = 1

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

        if isinstance(num_scans, int):
            if num_scans <= 0:
                errors.append('Number of scans (greater than 0)')

        if valid:
            try:
                (scan_length,
                    total_length,
                    time_per_scan,
                    return_time,
                    total_time) = self._calc_scan_params(x_start, x_end, y_start,
                    y_end, scan_speed, return_speed, scan_acceleration,
                    return_acceleration, scan_start_offset_dist, scan_end_offset_dist,
                    num_scans)

            except Exception:
                valid = False
                errors.append('Error calculating scan parameters')

            try:
                return_vals = self._calc_exposure_params()

                if len(return_vals[0])>0:
                    errors.extend(return_vals[0])

            except Exception:
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
        return_speed, scan_acceleration, return_acceleration):

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
            tot_dist = math.sqrt((x_end - x_start)**2 + (y_end-y_start)**2)
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

        vect_scan_speed = (scan_speed_x, scan_speed_y)
        vect_scan_accel = (scan_acceleration_x, scan_acceleration_y)
        vect_return_speed = (return_speed_x, return_speed_y)
        vect_return_accel = (return_acceleration_x, return_acceleration_y)

        return vect_scan_speed, vect_scan_accel, vect_return_speed, vect_return_accel


    def _calc_pco_params(self, x_start, x_end, y_start, y_end, scan_speed,
        return_speed, scan_acceleration, return_acceleration, delta_t):
        """ For Newport XPS controller with encoded stages"""
        (vect_scan_speed, vect_scan_accel,
            vect_return_speed, vect_return_accel) = self._calc_vector_params(x_start,
            x_end, y_start, y_end, scan_speed, return_speed, scan_acceleration,
            return_acceleration)

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
        return round(float(base)*round(x/base), prec)

    def update_params(self):
        self._param_change()

    def on_exit(self):
        if self.motor is not None:
            self.motor.disconnect()

class TRFrame(wx.Frame):
    """
    A lightweight frame allowing one to work with arbitrary number of pumps.
    Only meant to be used when the pumpcon module is run directly,
    rather than when it is imported into another program.
    """
    def __init__(self, settings, *args, **kwargs):
        """
        Initializes the pump frame. Takes args and kwargs for the wx.Frame class.
        """
        super(TRFrame, self).__init__(*args, **kwargs)
        logger.debug('Setting up the TRFrame')

        self.Bind(wx.EVT_CLOSE, self._on_exit)

        self._create_layout(settings)

        self.Layout()
        self.SendSizeEvent()
        self.Fit()
        self.Raise()

    def _create_layout(self, settings):
        """Creates the layout"""
        self.tr_panel = TRPanel(settings, self)

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
    # h1.setLevel(logging.INFO)
    h1.setLevel(logging.DEBUG)
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
        'motor_ip'              : '164.54.204.65',
        'motor_port'            : '5001',
        'motor_group_name'      : 'XY',
        'motor_x_name'          : 'XY.X',
        'motor_y_name'          : 'XY.Y',
        'pco_direction'         : 'x',
        'pco_pulse_width'       : D('0.2'), #In microseconds, opt: 0.2, 1, 2.5, 10
        'pco_encoder_settle_t'  : D('0.075'), #In microseconds, opt: 0.075, 1, 4, 12
        'encoder_resolution'    : D('0.0005'), #for ILS50PP, in mm
        'encoder_precision'     : 4, #Number of significant decimals in encoder value
        'min_off_time'          : D('0.001'),
        'x_range'               : (-25, 25),
        'y_range'               : (-25, 25),
        'speed_lim'             : (0, 50),
        'acceleration_lim'      : (0, 200),
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
    frame = TRFrame(settings, None, title='Coflow Control')
    frame.Show()
    app.MainLoop()



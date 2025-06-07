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
import six

import threading
import time
from collections import OrderedDict, deque
import logging
import logging.handlers as handlers
import sys
import os
import decimal
from decimal import Decimal as D
import datetime
import copy
import shutil

if six.PY2:
    import subprocess32 as subprocess
else:
    import subprocess

if __name__ != '__main__':
    logger = logging.getLogger(__name__)

import wx
import numpy as np
import epics

import motorcon
import detectorcon
import utils
import XPS_C8_drivers as xps_drivers

utils.set_mppath() #This must be done before importing any Mp Modules.
import Mp as mp
import MpCa as mpca

class ExpCommThread(threading.Thread):

    def __init__(self, command_queue, return_queue, abort_event, exp_event,
        timeout_event, settings, name=None):
        """
        Initializes the custom thread.
        """
        threading.Thread.__init__(self, name=name)

        logger.info("Starting exposure control thread: %s", self.name)

        self.daemon = True

        self.command_queue = command_queue
        self.return_queue = return_queue
        self._abort_event = abort_event
        self._exp_event = exp_event
        self._timeout_event = timeout_event
        self._stop_event = threading.Event()
        self._settings = settings

        self.xps = None

        self._commands = {
            'start_exp'         : self._start_exp,
            'start_tr_exp'      : self._start_tr_exp,
            'start_scan_exp'    : self._start_scan_exp,
            'start_test_scan'   : self.run_test_scan,
            }

    def run(self):
        """
        Custom run method for the thread.
        """

        logger.debug('Setting up mx environment and database')
        #MX stuff
        try:
            # First try to get the name from an environment variable.
            database_filename = os.environ["MXDATABASE"]
        except:
            # If the environment variable does not exist, construct
            # the filename for the default MX database.
            mxdir = utils.get_mxdir()
            database_filename = os.path.join(mxdir, "etc", "mxmotor.dat")
            database_filename = os.path.normpath(database_filename)

        mx_database = mp.setup_database(database_filename)
        mx_database.set_plot_enable(2)
        mx_database.set_program_name("expcon")

        logger.debug("Initialized mx database")

        if self._settings['detector'].lower().split('_')[-1] == 'mx':
            logger.debug('Getting mx detector')
            record_name = self._settings['detector'].rstrip('_mx')

            data_dir_root = copy.deepcopy(self._settings['base_data_dir']).replace(
                self._settings['local_dir_root'], self._settings['remote_dir_root'], 1)

            det_args = self._settings['det_args']

            det = detectorcon.MXDetector(record_name, mx_database, data_dir_root, **det_args)

            # server_record_name = det.get_field('server_record')
            # remote_det_name = det.get_field('remote_record_name')
            # server_record = mx_database.get_record(server_record_name)
            # det_datadir_name = '{}.datafile_directory'.format(remote_det_name)
            # det_datafile_name = '{}.datafile_pattern'.format(remote_det_name)
            # det_exp_time_name = '{}.ext_enable_time'.format(remote_det_name)
            # det_exp_period_name = '{}.ext_enable_period'.format(remote_det_name)
            # det_local_datafile_root_name = '{}.local_datafile_user'.format(remote_det_name)

            # det_datadir = mp.Net(server_record, det_datadir_name)
            # det_filename = mp.Net(server_record, det_datafile_name)
            # det_exp_time = mp.Net(server_record, det_exp_time_name)
            # det_exp_period = mp.Net(server_record, det_exp_period_name)
            # det_local_datafile_root = mp.Net(server_record, det_local_datafile_root_name)

            # det_local_datafile_root.put(data_dir_root) #MX record field is read only?

        elif self._settings['detector'].lower().split('_')[-1] == 'epics':
            logger.debug('Getting epics detector')
            record_name = self._settings['detector'].rstrip('_epics')

            det_args = self._settings['det_args']
            det = detectorcon.EPICSEigerDetector(record_name, **det_args)

        logger.debug("Got detector records")

        ab_burst = mx_database.get_record('ab_burst')
        ab_burst_server_record_name = ab_burst.get_field('server_record')
        ab_burst_server_record = mx_database.get_record(ab_burst_server_record_name)
        # dg645_trigger_source = mp.Net(ab_burst_server_record, 'dg645.trigger_source')

        ab_burst_2 = mx_database.get_record('ab_burst_2')
        ab_burst_server_record_name2 = ab_burst_2.get_field('server_record')
        ab_burst_server_record2 = mx_database.get_record(ab_burst_server_record_name2)
        # dg645_trigger_source2 = mp.Net(ab_burst_server_record2, 'dg645.trigger_source')

        logger.debug("Got dg645 records")

        attenuators = {
                1   : mx_database.get_record('di_0'),
                2   : mx_database.get_record('di_1'),
                4   : mx_database.get_record('di_2'),
                8   : mx_database.get_record('di_3'),
                16  : mx_database.get_record('di_4'),
                32  : mx_database.get_record('di_5'),
            }

        logger.debug("Got attenuator records.")

        mx_data = {'det': det,
            # 'det_datadir': det_datadir,
            # 'det_filename': det_filename,
            # 'det_exp_time'      : det_exp_time,
            # 'det_exp_period'    : det_exp_period,
            'struck': mx_database.get_record('sis3820'),
            'struck_ctrs': [mx_database.get_record(log['mx_record']) for log in self._settings['struck_log_vals']],
            'struck_pv': '18ID:mcs',
            'ab_burst': mx_database.get_record('ab_burst'),
            'cd_burst': mx_database.get_record('cd_burst'),
            'ef_burst': mx_database.get_record('ef_burst'),
            'gh_burst': mx_database.get_record('gh_burst'),
            # 'dg645_trigger_source': dg645_trigger_source,
            'ab_burst_2': mx_database.get_record('ab_burst_2'),
            'cd_burst_2': mx_database.get_record('cd_burst_2'),
            'ef_burst_2': mx_database.get_record('ef_burst_2'),
            'gh_burst_2': mx_database.get_record('gh_burst_2'),
            # 'dg645_trigger_source2': dg645_trigger_source2,
            'ab': mx_database.get_record('ab'),
            'dio': [mx_database.get_record('do_{}'.format(i)) for i in range(16)],
            'joerger': mx_database.get_record('joerger_timer'),
            'joerger_ctrs':[mx_database.get_record('j2')] + [mx_database.get_record(log['mx_record']) for log in self._settings['joerger_log_vals']],
            'ki1'   : mx_database.get_record('ki1'),
            'ki2'   : mx_database.get_record('ki2'),
            'ki3'   : mx_database.get_record('ki3'),
            'mx_db' : mx_database,
            'motors'  : {},
            'attenuators' : attenuators,
            }

        if self._settings['use_old_i0_gain']:
            mx_data['ki0'] = mx_database.get_record('ki0')
        else:
            mx_data['ki0'] = epics.get_pv(self._settings['i0_gain_pv'])
            mx_data['ki0'].get()

        logger.debug("Generated mx_data")

        self._mx_data = mx_data

        while True:
            if len(self.command_queue) > 0:
                logger.debug("Getting new command")
                command, args, kwargs = self.command_queue.popleft()
            else:
                command = None

            if self._abort_event.is_set():
                logger.debug("Abort event detected")
                self._abort()
                command = None

            if self._stop_event.is_set():
                logger.debug("Stop event detected")
                self._abort()
                break

            if command is not None:
                logger.debug("Processing cmd '%s' with args: %s and kwargs: %s ", command, ', '.join(['{}'.format(a) for a in args]), ', '.join(['{}: {}'.format(kw, item) for kw, item in kwargs.items()]))
                try:
                    self._commands[command](*args, **kwargs)
                except Exception:
                    msg = ("Exposure control thread failed to run command '%s' "
                        "with args: %s and kwargs: %s " %(command,
                        ', '.join(['{}'.format(a) for a in args]),
                        ', '.join(['{}: {}'.format(kw, item) for kw, item in kwargs.items()])))
                    logger.exception(msg)

                    self.abort_all()
            else:
                time.sleep(.01)

        if self._stop_event.is_set():
            self._stop_event.clear()
        else:
            self._abort()

        logger.info("Quitting exposure control thread: %s", self.name)

    def _start_exp(self, data_dir, fprefix, num_frames, exp_time, exp_period,
        **kwargs):
        kwargs['metadata'] = self._add_metadata(kwargs['metadata'])

        if self._settings['detector'].lower() == 'mar':
            self.mar_exposure(data_dir, fprefix, num_frames, exp_time, exp_period, **kwargs)
        else:
            self.fast_exposure(data_dir, fprefix, num_frames, exp_time, exp_period, **kwargs)

    def _start_tr_exp(self, exp_settings, comp_settings):
        exp_settings['metadata'] = self._add_metadata(exp_settings['metadata'])

        self.tr_exposure(exp_settings, comp_settings)

    def _start_scan_exp(self, exp_settings, comp_settings):
        exp_settings['metadata'] = self._add_metadata(exp_settings['metadata'])

        self.scan_exposure(exp_settings, comp_settings)

    def tr_exposure(self, exp_settings, comp_settings):
        if 'trsaxs_scan' in comp_settings:
            tr_scan_settings = comp_settings['trsaxs_scan']
            tr_scan = True
        else:
            tr_scan = False

        if 'trsaxs_flow' in comp_settings:
            tr_flow_settings = comp_settings['trsaxs_flow']
            tr_flow = True
        else:
            tr_flow = False

        logger.debug('Setting up trsaxs exposure')
        det = self._mx_data['det']          #Detector

        struck = self._mx_data['struck']    #Struck SIS3820
        s_counters = self._mx_data['struck_ctrs']
        # struck_mode_pv = mpca.PV(self._mx_data['struck_pv']+':ChannelAdvance')
        # struck_current_channel_pv = mpca.PV(self._mx_data['struck_pv']+':CurrentChannel')

        ab_burst = self._mx_data['ab_burst']   #Shutter control signal
        cd_burst = self._mx_data['cd_burst']   #Struck LNE/channel advance signal
        ef_burst = self._mx_data['ef_burst']   #Pilatus trigger signal
        gh_burst = self._mx_data['gh_burst']
        # dg645_trigger_source = self._mx_data['dg645_trigger_source']

        dio_out6 = self._mx_data['dio'][6]      #Xia/wharberton shutter N.C.
        dio_out9 = self._mx_data['dio'][9]      #Shutter control signal (alt.)
        dio_out10 = self._mx_data['dio'][10]    #SRS DG645 trigger

        # det_datadir = self._mx_data['det_datadir']
        # det_filename = self._mx_data['det_filename']
        # det_exp_time = self._mx_data['det_exp_time']
        # det_exp_period = self._mx_data['det_exp_period']

        exp_period = exp_settings['exp_period']
        exp_time = exp_settings['exp_time']
        data_dir = exp_settings['data_dir']
        fprefix = exp_settings['fprefix']
        num_frames = exp_settings['num_frames']

        shutter_speed_open = exp_settings['shutter_speed_open']
        shutter_speed_close = exp_settings['shutter_speed_close']
        shutter_pad = exp_settings['shutter_pad']
        shutter_cycle = exp_settings['shutter_cycle']

        total_shutter_speed = shutter_speed_open+shutter_speed_close+shutter_pad
        s_open_time = shutter_speed_open + shutter_pad

        if exp_period > exp_time+total_shutter_speed and exp_period >= shutter_cycle:
            logger.info('Shuttered mode')
        else:
            logger.info('Continuous mode')

        wait_for_trig = True

        log_vals = exp_settings['struck_log_vals']

        num_runs = tr_scan_settings['num_scans']
        x_start = tr_scan_settings['scan_x_start']
        x_end = tr_scan_settings['scan_x_end']
        y_start = tr_scan_settings['scan_y_start']
        y_end = tr_scan_settings['scan_y_end']
        motor_type = tr_scan_settings['motor_type']
        motor = tr_scan_settings['motor']
        vect_scan_speed = tr_scan_settings['vect_scan_speed']
        vect_scan_accel = tr_scan_settings['vect_scan_accel']
        vect_return_speed = tr_scan_settings['vect_return_speed']
        vect_return_accel = tr_scan_settings['vect_return_accel']
        return_speed = tr_scan_settings['return_speed']
        return_accel = tr_scan_settings['return_accel']

        scan_type = tr_scan_settings['scan_type']
        step_axis = tr_scan_settings['step_axis']
        step_size = tr_scan_settings['step_size']
        step_speed = tr_scan_settings['step_speed']
        step_accel = tr_scan_settings['step_acceleration']
        use_gridpoints = tr_scan_settings['use_gridpoints']
        gridpoints = tr_scan_settings['gridpoints']

        if motor_type == 'Newport_XPS':
            pco_start = tr_scan_settings['pco_start']
            pco_end = tr_scan_settings['pco_end']
            pco_step = tr_scan_settings['pco_step']
            pco_direction = tr_scan_settings['pco_direction']
            pco_pulse_width = tr_scan_settings['pco_pulse_width']
            pco_encoder_settle_t = tr_scan_settings['pco_encoder_settle_t']
            x_motor = str(tr_scan_settings['motor_x_name'])
            y_motor = str(tr_scan_settings['motor_y_name'])

        if tr_flow:
            start_condition = tr_flow_settings['start_condition']
            start_flow_event = tr_flow_settings['start_flow_event']
            start_exposure_event = tr_flow_settings['start_exp_event']
            start_autoinject_event = tr_flow_settings['autoinject_event']
            autoinject = tr_flow_settings['autoinject']
            autoinject_scan = tr_flow_settings['autoinject_scan']
        else:
            start_condition = None
            start_flow_event = None
            start_exposure_event = None
            start_autoinject_event = None
            autoinject = None
            autoinject_scan = None

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

            # For newports this is fine, because it automatically scales down different axes speeds
            # so that a group move ends simultaneously. For other controls may need to
            # recalculate the vector speeds and accelerations
            if vect_return_speed[0] != 0:
                motor.set_velocity(vect_return_speed[0], x_motor, 0)
            if vect_return_speed[1] != 0:
                motor.set_velocity(vect_return_speed[1], y_motor, 1)
            if vect_return_accel[0] != 0:
                motor.set_acceleration(vect_return_accel[0], x_motor, 0)
            if vect_return_accel[1] != 0:
                motor.set_acceleration(vect_return_accel[1], y_motor, 1)

        motor_cmd_q.append(('move_absolute', ('TR_motor', (x_start, y_start)), {}))

        logger.debug('Waiting for detector to finish')
        start = time.time()
        timeout = False
        while det.get_status() !=0 and not timeout:
            time.sleep(0.001)
            if self._abort_event.is_set():
                self.tr_abort_cleanup(det, struck, ab_burst, dio_out9, dio_out6,
                    comp_settings, exp_time)
                break

            # Is this long enough? Should it be based off of the scan/return time?
            if time.time() - start > 5:
                timeout = True
                logger.error('Timeout while waiting for detector to finish!')

                if det.get_status() !=0:
                    try:
                        det.abort()
                    except (mp.Device_Action_Failed_Error, mp.Unparseable_String_Error):
                        pass
                    try:
                        det.abort()
                    except (mp.Device_Action_Failed_Error, mp.Unparseable_String_Error):
                        pass

        # dg645_trigger_source.put(1) #Change this to 2 for external falling edges

        #Need to clear srs possibly?
        ab_burst.setup(0.000001, 0.000000, 1, 0, 1, 2)
        cd_burst.setup(0.000001, 0.000000, 1, 0, 1, 2)
        ef_burst.setup(0.000001, 0.000000, 1, 0, 1, 2)
        gh_burst.setup(0.000001, 0.000000, 1, 0, 1, 2)

        ab_burst.arm()

        dio_out10.write( 1 )
        time.sleep(0.01)
        dio_out10.write( 0 )

        status = ab_burst.get_status()

        while (status & 0x1) != 0:
            time.sleep(0.01)
            status = ab_burst.get_status()

        exp_start_num = '000001'

        # cur_fprefix = '{}_{:04}'.format(fprefix, current_run)

        if self._settings['add_file_postfix']:
            new_fname = '{}_{}.tif'.format(fprefix, exp_start_num)
        else:
            new_fname = fprefix

        tot_frames = num_frames*num_runs
        logger.info(tot_frames)
        det.set_data_dir(data_dir)
        det.set_num_frames(tot_frames)
        # det.set_num_frames(num_frames)
        det.set_filename(new_fname)
        det.set_trigger_mode('ext_enable')
        det.set_exp_time(exp_time)
        det.set_exp_period(exp_period)
        det.arm()

        # struck_mode_pv.caput(1, timeout=5)
        struck.set_measurement_time(exp_time)   #Ignored for external LNE of Struck
        struck.set_num_measurements(num_frames)
        struck.set_trigger_mode(0x8|0x2)    #Sets 'autotrigger' mode, i.e. counting as soon as armed


        if exp_period > exp_time+0.01 and exp_period >= 0.02:
            #Shutter opens and closes, Takes 4 ms for open and close
            ab_burst.setup(exp_time+0.007, exp_time+s_open_time, 1, 0, 1, 2)
            cd_burst.setup(exp_time+0.007, 0.0001, 1, exp_time+s_open_time, 1, 2)
            ef_burst.setup(exp_time+0.007, exp_time, 1, s_open_time, 1, 2)
            gh_burst.setup(exp_time+0.007, exp_time, 1, 0, 1, 2)
        else:
            #Shutter will be open continuously, via dio_out9
            ab_burst.setup(exp_time+0.001, exp_time, 1, 0, 1, 2) #Irrelevant
            cd_burst.setup(exp_time+0.001, 0.0001, 1, exp_time+0.00015, 1, 2)
            ef_burst.setup(exp_time+0.001, exp_time, 1, 0, 1, 2)
            gh_burst.setup(exp_time+0.001, exp_time, 1, 0, 1, 2)

        # Flow stuff starts here
        if tr_flow:
            if start_condition.lower() != 'none':
                start_flow_event.set()

                while not start_exposure_event.is_set():
                    if self._abort_event.is_set():
                        self.tr_abort_cleanup(det, struck, ab_burst, dio_out9, dio_out6,
                            comp_settings, exp_time)
                        break
                    time.sleep(0.001)

        if scan_type == 'vector':
            next_x = x_start
            next_y = y_start
            step_num = None
            set_step_speed = False

            if pco_direction == 'x':
                x_positions = [i*tr_scan_settings['x_pco_step']+tr_scan_settings['x_pco_start']
                    for i in range(num_frames)]

                step_start = tr_scan_settings['y_start']
                step_end = tr_scan_settings['y_end']

                if step_start != step_end:
                    y_positions = np.linspace(step_start, step_end, len(x_positions))
                else:
                    y_positions = np.array([step_start]*len(x_positions))

            else:
                y_positions = [i*tr_scan_settings['y_pco_step']+tr_scan_settings['y_pco_start']
                    for i in range(num_frames)]

                step_start = tr_scan_settings['x_start']
                step_end = tr_scan_settings['x_end']

                if step_start != step_end:
                    x_positions = np.linspace(step_start, step_end, len(y_positions))
                else:
                    x_positions = np.array([step_start]*len(y_positions))

            renum_threads = []
            for current_run in range(1,num_runs+1):
                if self._abort_event.is_set():
                    break

                logger.info('Scan %s started', current_run)

                self.return_queue.append(['scan', current_run])

                self._inner_tr_exp(det, exp_time, exp_period, exp_settings,
                    data_dir, fprefix, num_frames, current_run, struck, ab_burst, dio_out6,
                    dio_out9, dio_out10, wait_for_trig, motor, motor_type, pco_direction,
                    x_motor, y_motor, vect_scan_speed, vect_scan_accel, vect_return_speed,
                    vect_return_accel, x_start, y_start, x_end, y_end, next_x, next_y,
                    step_num, step_speed, step_accel, set_step_speed, motor_cmd_q, tr_flow,
                    autoinject, autoinject_scan, start_autoinject_event, s_counters, log_vals,
                    x_positions, y_positions, comp_settings, tr_scan_settings)

                logger.info('starting renum thread')
                renum_t = threading.Thread(target=self.renum_scan_files,
                    args=(data_dir, fprefix, num_frames, current_run))
                renum_t.daemon = True
                renum_t.start()

                renum_threads.append(renum_t)
                logger.info('renum thread started')

                logger.info('Scan %s done', current_run)

            for t in renum_threads:
                t.join()

        else:
            if step_axis == 'x':
                y_positions = [i*tr_scan_settings['y_pco_step']+tr_scan_settings['y_pco_start']
                    for i in range(num_frames)]
            else:
                x_positions = [i*tr_scan_settings['x_pco_step']+tr_scan_settings['x_pco_start']
                    for i in range(num_frames)]

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

                logger.info('Scan %s started', current_run)
                self.return_queue.append(['scan', current_run])

                for step_num, pos in enumerate(mtr_positions):
                    if self._abort_event.is_set():
                        break

                    if step_axis == 'x':
                        step_x_start = pos
                        step_x_end = pos
                        step_y_start = y_start
                        step_y_end = y_end
                        # motor.set_velocity(step_speed, x_motor, 0)
                        # motor.set_acceleration(step_accel, x_motor, 0)

                        if step_num + 1 < len(mtr_positions):
                            next_x = mtr_positions[step_num+1]
                            set_step_speed = True
                        else:
                            next_x = x_start
                            set_step_speed = False

                        next_y = y_start

                        x_positions = [pos for i in range(num_frames)]

                    else:
                        step_x_start = x_start
                        step_x_end = x_end
                        step_y_start = pos
                        step_y_end = pos
                        # motor.set_velocity(step_speed, y_motor, 1)
                        # motor.set_acceleration(step_accel, y_motor, 1)

                        next_x = x_start

                        if step_num + 1 < len(mtr_positions):
                            next_y = mtr_positions[step_num+1]
                            set_step_speed = True
                        else:
                            next_y = y_start
                            set_step_speed = False

                        y_positions = [pos for i in range(num_frames)]

                    step_fprefix = '{}_s{:03}'.format(fprefix, step_num+1)

                    self._inner_tr_exp(det, exp_time, exp_period, exp_settings,
                        data_dir, step_fprefix, num_frames, current_run, struck, ab_burst, dio_out6,
                        dio_out9, dio_out10, wait_for_trig, motor, motor_type, pco_direction,
                        x_motor, y_motor, vect_scan_speed, vect_scan_accel, vect_return_speed,
                        vect_return_accel, step_x_start, step_y_start, step_x_end, step_y_end,
                        next_x, next_y, step_num, step_speed, step_accel, set_step_speed,
                        motor_cmd_q, tr_flow, autoinject, autoinject_scan, start_autoinject_event,
                        s_counters, log_vals, x_positions, y_positions, comp_settings, tr_scan_settings)

                logger.info('Scan %s done', current_run)



        start = time.time()
        timeout = False
        while not motor.is_moving() and not timeout:
            time.sleep(0.001) #Waits for motion to start
            if time.time()-start>0.5:
                timeout = True

        while motor.is_moving():
            if self._abort_event.is_set():
                self.tr_abort_cleanup(det, struck, ab_burst, dio_out9, dio_out6,
                    comp_settings, exp_time)
                break

            time.sleep(0.001)

        motor_con.stop()
        motor_con.join()

        self._exp_event.clear()

    def _inner_tr_exp(self, det, exp_time, exp_period, exp_settings,
        data_dir, fprefix, num_frames, current_run, struck, ab_burst, dio_out6,
        dio_out9, dio_out10, wait_for_trig, motor, motor_type, pco_direction,
        x_motor, y_motor, vect_scan_speed, vect_scan_accel, vect_return_speed,
        vect_return_accel, x_start, y_start, x_end, y_end, next_x, next_y,
        step_num, step_speed, step_accel, set_step_speed, motor_cmd_q, tr_flow,
        autoinject, autoinject_scan, start_autoinject_event, s_counters, log_vals,
        x_positions, y_positions, comp_settings, tr_scan_settings):

        struck.stop()
        ab_burst.stop()

        dio_out9.write(0) # Make sure the NM shutter is closed
        dio_out10.write(0) # Make sure the trigger is off

        exp_start_num = '000001'

        cur_fprefix = '{}_{:04}'.format(fprefix, current_run)

        if self._settings['add_file_postfix']:
            new_fname = '{}_{}.tif'.format(cur_fprefix, exp_start_num)
        else:
            new_fname = cur_fprefix

        dio_out6.write(0) #Open the slow normally closed xia shutter

        struck.start()
        ab_burst.arm()

        # det.set_filename(new_fname)

        # det.arm()

        start = time.time()
        timeout = False
        x, y = motor.position

        # logger.info(x)
        # logger.info(y)

        if x != x_start and y != y_start:
            while not motor.is_moving() and not timeout:
                time.sleep(0.001) #Waits for motion to start
                if time.time()-start>0.1:
                    timeout = True

        while motor.is_moving():
            if self._abort_event.is_set():
                self.tr_abort_cleanup(det, struck, ab_burst, dio_out9, dio_out6,
                    comp_settings, exp_time)
                break
            time.sleep(0.001)

        if self._abort_event.is_set():
            self.tr_abort_cleanup(det, struck, ab_burst, dio_out9, dio_out6,
                comp_settings, exp_time)
            return

        if motor_type == 'Newport_XPS':
            if pco_direction == 'x':
                wait_for_motor = True
                while wait_for_motor:
                    status, descrip = motor.get_group_status(tr_scan_settings['motor_group_name'])
                    # logger.debug(status)
                    if status == 12:
                        wait_for_motor = False

                logger.debug('starting x pco')
                motor.start_position_compare(x_motor)
            else:
                wait_for_motor = True
                while wait_for_motor:
                    status, descrip = motor.get_group_status(tr_scan_settings['motor_group_name'])
                    # logger.debug(status)
                    if status == 12:
                        wait_for_motor = False

                logger.debug('starting Y pco')

                motor.start_position_compare(y_motor)

            if vect_scan_speed[0] != 0:
                motor.set_velocity(vect_scan_speed[0], x_motor, 0)
            if vect_scan_speed[1] != 0:
                motor.set_velocity(vect_scan_speed[1], y_motor, 1)
            if vect_scan_accel[0] != 0:
                motor.set_acceleration(vect_scan_accel[0], x_motor, 0)
            if vect_scan_accel[1] != 0:
                motor.set_acceleration(vect_scan_accel[1], y_motor, 1)

        if tr_flow:
            if autoinject == 'after_scan':
                if current_run == int(autoinject_scan)+1:
                    start_autoinject_event.set()

        motor_cmd_q.append(('move_absolute', ('TR_motor', (x_end, y_end)), {}))

        self._exp_event.set()

        start = time.time()
        timeout = False
        while not motor.is_moving() and not timeout:
            time.sleep(0.001) #Waits for motion to start
            if time.time()-start>0.5:
                timeout = True

        while motor.is_moving():
            if self._abort_event.is_set():
                self.tr_abort_cleanup(det, struck, ab_burst, dio_out9, dio_out6,
                    comp_settings, exp_time)
                break

            time.sleep(0.001)

        dio_out6.write(1) #Close the slow normally closed xia shutter

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

            if set_step_speed:
                if pco_direction == 'x':
                    motor.set_velocity(step_speed, y_motor, 1)
                    motor.set_acceleration(step_accel, y_motor, 1)
                else:
                    motor.set_velocity(step_speed, x_motor, 0)
                    motor.set_acceleration(step_accel, x_motor, 0)

            else:
                if step_num is not None:
                    if pco_direction == 'x':
                        motor.set_velocity(vect_return_speed[0], y_motor, 1)
                        motor.set_acceleration(vect_return_accel[0], y_motor, 1)
                    else:
                        motor.set_velocity(vect_return_speed[1], x_motor, 0)
                        motor.set_acceleration(vect_return_accel[1], x_motor, 0)


        motor_cmd_q.append(('move_absolute', ('TR_motor', (next_x, next_y)), {}))

        measurement = struck.read_all()

        dark_counts = []
        for i in range(len(s_counters)):
            if log_vals[i]['dark']:
                dark_counts.append(s_counters[i].get_dark_current())
            else:
                dark_counts.append(0)

        logger.info('Writing counters')
        extra_vals = [['x', x_positions], ['y', y_positions]]
        self.write_counters_struck(measurement, num_frames, data_dir,
            cur_fprefix, exp_period, dark_counts, log_vals,
            exp_settings['metadata'], extra_vals)

        # det.stop()

        # while det.get_status() != 0:
        #     time.sleep(0.001)
        #     if self._abort_event.is_set():
        #         self.tr_abort_cleanup(det, struck, ab_burst, dio_out9, dio_out6,
        #             comp_settings, exp_time)
        #         break

        if self._abort_event.is_set():
            self.tr_abort_cleanup(det, struck, ab_burst, dio_out9, dio_out6,
                comp_settings, exp_time)


    def renum_scan_files(self, data_dir, fprefix, num_frames, current_run):

        data_dir = data_dir.replace(self._settings['remote_dir_root'],
            self._settings['local_dir_root'], 1)

        f_start = (int(current_run) - 1)*num_frames + 1

        f_list = ['{}_data_{:06d}.h5'.format(fprefix, f_start+i) for i in range(num_frames)]

        timeout = False

        for i, f in enumerate(f_list):
            full_path = os.path.join(data_dir, f)

            new_name = '{}_{:04d}_data_{:06d}.h5'.format(fprefix, int(current_run), i+1)

            full_new = os.path.join(data_dir, new_name)

            if not os.path.exists(full_path):
                start = time.time()

            while not os.path.exists(full_path):
                if time.time() - start > 10:
                    timeout = True
                    break
                else:
                    time.sleep(0.1)

                if self._abort_event.is_set():
                    timeout = True

            if timeout:
                break

            logger.debug('Moving %s to %s', full_path, full_new)
            shutil.move(full_path, full_new)


    def scan_exposure(self, exp_settings, comp_settings):
        logger.debug('Setting up scan exposure')

        scan_settings = comp_settings['scan']

        num_scans = scan_settings['num_scans']

        det = self._mx_data['det']          #Detector

        struck = self._mx_data['struck']    #Struck SIS3820
        s_counters = self._mx_data['struck_ctrs']

        ab_burst = self._mx_data['ab_burst']   #Shutter control signal
        cd_burst = self._mx_data['cd_burst']   #Struck LNE/channel advance signal
        ef_burst = self._mx_data['ef_burst']   #Pilatus trigger signal
        gh_burst = self._mx_data['gh_burst']
        # dg645_trigger_source = self._mx_data['dg645_trigger_source']

        ab_burst_2 = None

        dio_out6 = self._mx_data['dio'][6]      #Xia/wharberton shutter N.C.
        dio_out9 = self._mx_data['dio'][9]      #Shutter control signal (alt.)
        dio_out10 = self._mx_data['dio'][10]    #SRS DG645 trigger

        exp_period = exp_settings['exp_period']
        exp_time = exp_settings['exp_time']
        data_dir = exp_settings['data_dir']
        fprefix = exp_settings['fprefix']
        num_frames = exp_settings['num_frames']

        shutter_speed_open = exp_settings['shutter_speed_open']
        shutter_speed_close = exp_settings['shutter_speed_close']
        shutter_pad = exp_settings['shutter_pad']
        shutter_cycle = exp_settings['shutter_cycle']

        total_shutter_speed = shutter_speed_open+shutter_speed_close+shutter_pad
        s_open_time = shutter_speed_open + shutter_pad

        if exp_period > exp_time+total_shutter_speed and exp_period >= shutter_cycle:
            logger.info('Shuttered mode')
            continuous_exp = False
        else:
            logger.info('Continuous mode')
            continuous_exp = True

        log_vals = exp_settings['struck_log_vals']

        dark_counts = []
        for i in range(len(s_counters)):
            if log_vals[i]['dark']:
                dark_counts.append(s_counters[i].get_dark_current())
            else:
                dark_counts.append(0)

        # dg645_trigger_source.put(1)

        if not continuous_exp:
            #Shutter opens and closes, Takes 4 ms for open and close
            ab_burst.setup(exp_period, exp_time+s_open_time, num_frames, 0, 1, 2)
            cd_burst.setup(exp_period, 0.0001, num_frames, exp_time+s_open_time, 1, 2)
            ef_burst.setup(exp_period, exp_time, num_frames, s_open_time, 1, 2)
            gh_burst.setup(exp_period, exp_time, num_frames, 0, 1, 2)
        else:
            #Shutter will be open continuously, via dio_out9
            ab_burst.setup(exp_period, exp_time, num_frames, 0, 1, 2) #Irrelevant
            cd_burst.setup(exp_period, 0.0001, num_frames, exp_time+0.00015, 1, 2)
            ef_burst.setup(exp_period, exp_time, num_frames, 0, 1, 2)
            gh_burst.setup(exp_period, exp_time, num_frames, 0, 1, 2)
            continuous_exp = True

        # if det.get_status() !=0:
        #     try:
        #         det.abort()
        #     except (mp.Device_Action_Failed_Error, mp.Unparseable_String_Error):
        #         pass
        #     try:
        #         det.abort()
        #     except (mp.Device_Action_Failed_Error, mp.Unparseable_String_Error):
        #         pass

        # aborted = False

        # struck.stop()
        # ab_burst.stop()

        # if exp_type == 'muscle':
        #     ab_burst_2.stop()

        # dio_out9.write(0) # Make sure the NM shutter is closed
        # dio_out10.write(0) # Make sure the trigger is off

        # tot_num_frames = num_frames*len(mtr_positions)

        # det.set_num_frames(tot_num_frames)
        det.set_trigger_mode('ext_enable')
        det.set_exp_time(exp_time)
        det.set_exp_period(exp_period)
        det.set_data_dir(data_dir)

        struck.set_measurement_time(exp_time)   #Ignored for external LNE of Struck
        # struck.set_num_measurements(tot_num_frames)
        struck.set_trigger_mode(0x8|0x2)    #Sets 'autotrigger' mode, i.e. counting as soon as armed

        for current_run in range(1,num_scans+1):
            scan_motors = copy.deepcopy(scan_settings['motors'])

            self.return_queue.append(['scan', current_run])

            self._inner_scan_exp2(exp_settings, scan_settings,
                scan_motors, OrderedDict(), current_run, dark_counts)

        self._exp_event.clear()

    # def _inner_scan_exp(self, exp_settings, scan_settings, scan_motors,
    #     motor_positions, current_run):

    #     motor_num, motor_params = scan_motors.popitem(False)

    #     motor_name = motor_params['motor']
    #     start = motor_params['start']
    #     stop = motor_params['stop']
    #     step_size = motor_params['step']
    #     motor_type = motor_params['type']
    #     scan_type = motor_params['scan_type']

    #     if motor_type == 'Newport':
    #         motor_get_params = copy.deepcopy(motor_params)
    #         motor_get_params['motor_ip'] = scan_settings['motor_ip']
    #         motor_get_params['motor_port'] = scan_settings['motor_port']

    #     motor = self.get_motor(motor_name, motor_type, motor_params)

    #     initial_motor_position = float(motor.get_position())

    #     if start < stop:
    #         mtr_positions = np.arange(start, stop+step_size, step_size)

    #         if mtr_positions[-1] > stop:
    #             mtr_positions = mtr_positions[:-1]
    #     else:
    #         mtr_positions = np.arange(stop, start+step_size, step_size)

    #         if mtr_positions[-1] > start:
    #             mtr_positions = mtr_positions[:-1]

    #         mtr_positions = mtr_positions[::-1]

    #     if 'relative' == scan_type.lower():
    #         mtr_positions += initial_motor_position

    #     if len(scan_motors) == 0: # Recursive base case
    #         det = self._mx_data['det']          #Detector

    #         struck = self._mx_data['struck']    #Struck SIS3820
    #         s_counters = self._mx_data['struck_ctrs']

    #         ab_burst = self._mx_data['ab_burst']   #Shutter control signal

    #         dio_out6 = self._mx_data['dio'][6]      #Xia/wharberton shutter N.C.
    #         dio_out9 = self._mx_data['dio'][9]      #Shutter control signal (alt.)
    #         dio_out10 = self._mx_data['dio'][10]    #SRS DG645 trigger

    #         data_dir = exp_settings['data_dir']
    #         fprefix = exp_settings['fprefix']

    #         exp_period = exp_settings['exp_period']
    #         exp_time = exp_settings['exp_time']
    #         data_dir = exp_settings['data_dir']
    #         fprefix = exp_settings['fprefix']
    #         num_frames = exp_settings['num_frames']

    #         shutter_speed_open = exp_settings['shutter_speed_open']
    #         shutter_speed_close = exp_settings['shutter_speed_close']
    #         shutter_pad = exp_settings['shutter_pad']
    #         shutter_cycle = exp_settings['shutter_cycle']

    #         #Values for the _inner_fast_exp that aren't used in this function
    #         wait_for_trig = False
    #         exp_type = 'scan'
    #         cur_trig = 0
    #         struck_num_meas = 0
    #         struck_meas_time = 0
    #         kwargs = exp_settings

    #         total_shutter_speed = shutter_speed_open+shutter_speed_close+shutter_pad
    #         s_open_time = shutter_speed_open + shutter_pad

    #         if exp_period > exp_time+total_shutter_speed and exp_period >= shutter_cycle:
    #             logger.info('Shuttered mode')
    #             continuous_exp = False
    #         else:
    #             logger.info('Continuous mode')
    #             continuous_exp = True

    #         log_vals = exp_settings['struck_log_vals']

    #         dark_counts = []
    #         for i in range(len(s_counters)):
    #             if log_vals[i]['dark']:
    #                 dark_counts.append(s_counters[i].get_dark_current())
    #             else:
    #                 dark_counts.append(0)


    #     for position in mtr_positions:
    #         logger.debug('Position: {}'.format(position))
    #         if self._abort_event.is_set():
    #             break

    #         motor.move_absolute(position)

    #         while motor.is_busy():
    #             time.sleep(0.01)
    #             if self._abort_event.is_set():
    #                 motor.stop()
    #                 motor.move_absolute(initial_motor_position)
    #                 break

    #         if self._abort_event.is_set():
    #             break

    #         motor_positions['m{}'.format(motor_num)] = position

    #         if len(scan_motors) > 0: # Recursive case
    #             my_scan_motors = copy.deepcopy(scan_motors)

    #             self._inner_scan_exp(exp_settings, scan_settings, my_scan_motors,
    #                 motor_positions, current_run)

    #         else:   # Base case for recursion:

    #             cur_fprefix = '{}_{:04}'.format(fprefix, current_run)

    #             # new_fname = '{}_{:04}'.format(cur_fprefix)
    #             logger.debug(motor_positions)
    #             for mprefix, pos in motor_positions.items():
    #                 cur_fprefix = cur_fprefix + '_{}_{}'.format(mprefix, pos)

    #             exp_start_num = '000001'

    #             if self._settings['add_file_postfix']:
    #                 new_fname = '{}_{}.tif'.format(cur_fprefix, exp_start_num)
    #             else:
    #                 new_fname = cur_fprefix

    #             extra_vals = []
    #             for mprefix, pos in motor_positions.items():
    #                 extra_vals.append([mprefix, np.ones(num_frames)*float(pos)])


    #             finished = self._inner_fast_exp(det,
    #             struck, ab_burst, ab_burst_2, dio_out6, dio_out9, dio_out10,
    #             continuous_exp, wait_for_trig, exp_type, data_dir, new_fname,
    #             cur_fprefix, log_vals, extra_vals, dark_counts, cur_trig, exp_time,
    #             exp_period, num_frames, struck_num_meas, struck_meas_time, kwargs)

    #             if not finished:
    #                 #Abort happened in the inner function
    #                 break

    #     motor.move_absolute(initial_motor_position)

    #     while motor.is_busy():
    #         time.sleep(0.01)
    #         if self._abort_event.is_set():
    #             motor.stop()

    def _inner_scan_exp2(self, exp_settings, scan_settings, scan_motors,
        motor_positions, current_run, dark_counts):
        motor_num, motor_params = scan_motors.popitem(False)

        motor_name = motor_params['motor']
        start = motor_params['start']
        stop = motor_params['stop']
        step_size = motor_params['step']
        motor_type = motor_params['type']
        scan_type = motor_params['scan_type']

        if motor_type == 'Newport':
            motor_get_params = copy.deepcopy(motor_params)
            motor_get_params['motor_ip'] = scan_settings['motor_ip']
            motor_get_params['motor_port'] = scan_settings['motor_port']

        motor = self.get_motor(motor_name, motor_type, motor_params)

        initial_motor_position = float(motor.get_position())

        if start < stop:
            mtr_positions = np.arange(start, stop+step_size, step_size)

            if mtr_positions[-1] > stop:
                mtr_positions = mtr_positions[:-1]
        else:
            mtr_positions = np.arange(stop, start+step_size, step_size)

            if mtr_positions[-1] > start:
                mtr_positions = mtr_positions[:-1]

            mtr_positions = mtr_positions[::-1]

        if 'relative' == scan_type.lower():
            mtr_positions += initial_motor_position

            exp_settings['metadata']['Motor {} absolute start:'.format(motor_num)] = mtr_positions[0]
            exp_settings['metadata']['Motor {} absolute stop:'.format(motor_num)] = mtr_positions[-1]

        if len(scan_motors) == 0: # Recursive base case
            det = self._mx_data['det']          #Detector

            struck = self._mx_data['struck']    #Struck SIS3820
            s_counters = self._mx_data['struck_ctrs']

            ab_burst = self._mx_data['ab_burst']   #Shutter control signal
            cd_burst = self._mx_data['cd_burst']   #Struck LNE/channel advance signal
            ef_burst = self._mx_data['ef_burst']   #Pilatus trigger signal
            gh_burst = self._mx_data['gh_burst']
            # dg645_trigger_source = self._mx_data['dg645_trigger_source']

            ab_burst_2 = None

            dio_out6 = self._mx_data['dio'][6]      #Xia/wharberton shutter N.C.
            dio_out9 = self._mx_data['dio'][9]      #Shutter control signal (alt.)
            dio_out10 = self._mx_data['dio'][10]    #SRS DG645 trigger

            exp_period = exp_settings['exp_period']
            exp_time = exp_settings['exp_time']
            data_dir = exp_settings['data_dir']
            fprefix = exp_settings['fprefix']
            num_frames = exp_settings['num_frames']

            shutter_speed_open = exp_settings['shutter_speed_open']
            shutter_speed_close = exp_settings['shutter_speed_close']
            shutter_pad = exp_settings['shutter_pad']
            shutter_cycle = exp_settings['shutter_cycle']

            #Values for the _inner_fast_exp that aren't used in this function
            wait_for_trig = False
            exp_type = 'scan'
            cur_trig = 0
            struck_num_meas = 0
            struck_meas_time = 0
            kwargs = exp_settings


            total_shutter_speed = shutter_speed_open+shutter_speed_close+shutter_pad
            s_open_time = shutter_speed_open + shutter_pad

            if exp_period > exp_time+total_shutter_speed and exp_period >= shutter_cycle:
                logger.info('Shuttered mode')
                continuous_exp = False
            else:
                logger.info('Continuous mode')
                continuous_exp = True

            log_vals = exp_settings['struck_log_vals']

            # dark_counts = []
            # for i in range(len(s_counters)):
            #     if log_vals[i]['dark']:
            #         dark_counts.append(s_counters[i].get_dark_current())
            #     else:
            #         dark_counts.append(0)

            # dg645_trigger_source.put(1)

            # if not continuous_exp:
            #     #Shutter opens and closes, Takes 4 ms for open and close
            #     ab_burst.setup(exp_period, exp_time+s_open_time, num_frames, 0, 1, 2)
            #     cd_burst.setup(exp_period, 0.0001, num_frames, exp_time+s_open_time, 1, 2)
            #     ef_burst.setup(exp_period, exp_time, num_frames, s_open_time, 1, 2)
            #     gh_burst.setup(exp_period, exp_time, num_frames, 0, 1, 2)
            # else:
            #     #Shutter will be open continuously, via dio_out9
            #     ab_burst.setup(exp_period, exp_time, num_frames, 0, 1, 2) #Irrelevant
            #     cd_burst.setup(exp_period, 0.0001, num_frames, exp_time+0.00015, 1, 2)
            #     ef_burst.setup(exp_period, exp_time, num_frames, 0, 1, 2)
            #     gh_burst.setup(exp_period, exp_time, num_frames, 0, 1, 2)
            #     continuous_exp = True

            if det.get_status() !=0:
                try:
                    det.abort()
                except (mp.Device_Action_Failed_Error, mp.Unparseable_String_Error):
                    pass
                try:
                    det.abort()
                except (mp.Device_Action_Failed_Error, mp.Unparseable_String_Error):
                    pass

            aborted = False

            struck.stop()
            ab_burst.stop()

            if exp_type == 'muscle':
                ab_burst_2.stop()

            dio_out9.write(0) # Make sure the NM shutter is closed
            dio_out10.write(0) # Make sure the trigger is off

            tot_num_frames = num_frames*len(mtr_positions)

            det.set_num_frames(tot_num_frames)
            # det.set_trigger_mode('ext_enable')
            # det.set_exp_time(exp_time)
            # det.set_exp_period(exp_period)

            # struck.set_measurement_time(exp_time)   #Ignored for external LNE of Struck
            struck.set_num_measurements(tot_num_frames)
            # struck.set_trigger_mode(0x8|0x2)    #Sets 'autotrigger' mode, i.e. counting as soon as armed

            cur_fprefix = '{}_{:04}'.format(fprefix, current_run)


            logger.debug(motor_positions)
            for mprefix, pos in motor_positions.items():
                cur_fprefix = cur_fprefix + '_{}_{}'.format(mprefix, pos)

            exp_start_num = '000001'

            if self._settings['add_file_postfix']:
                new_fname = '{}_{}.tif'.format(cur_fprefix, exp_start_num)
            else:
                new_fname = cur_fprefix

            # det.set_data_dir(data_dir)
            det.set_filename(new_fname)

            extra_vals = [['real_start_time', []],]
            for mprefix, pos in motor_positions.items():
                extra_vals.append([mprefix, np.ones(tot_num_frames)*float(pos)])

            log_positions = []

            for pos in mtr_positions:
                log_positions.extend([pos]*num_frames)

            extra_vals.append(['m{}'.format(motor_num), np.array(log_positions)])

            dio_out6.write(0) #Open the slow normally closed xia shutter

            ab_burst.get_status() #Maybe need to clear this status?

            motor.move_absolute(mtr_positions[0])

            det.arm()
            struck.start()

            if continuous_exp:
                if not exp_type == 'muscle' and not wait_for_trig:
                    dio_out9.write(1)

            if exp_type != 'muscle':
                self.write_log_header(data_dir, cur_fprefix, log_vals,
                    kwargs['metadata'], extra_vals)

            last_meas = 0

            timeouts = 0

            exp_start_times = []

        for position in mtr_positions:
            logger.debug('Position: {}'.format(position))
            if self._abort_event.is_set():
                break

            motor.move_absolute(position)

            while motor.is_busy():
                time.sleep(0.01)
                if self._abort_event.is_set():
                    motor.stop()
                    motor.move_absolute(initial_motor_position)
                    break

            if self._abort_event.is_set():
                break

            if len(scan_motors) > 0: # Recursive case
                motor_positions['m{}'.format(motor_num)] = position

                my_scan_motors = copy.deepcopy(scan_motors)

                self._inner_scan_exp2(exp_settings, scan_settings, my_scan_motors,
                    motor_positions, current_run, dark_counts)

            else:   # Base case for recursion:
                ab_burst.arm()

                if exp_type == 'muscle':
                    ab_burst_2.arm()

                self.wait_for_trigger(wait_for_trig, cur_trig, exp_time, ab_burst,
                    ab_burst_2, det, struck, dio_out6, dio_out9, dio_out10)

                start_time = time.time()

                if position == mtr_positions[0]:
                    initial_start_time = start_time
                    exp_start_time = 0

                else:
                    exp_start_time = start_time - initial_start_time

                new_exp_start_times = np.cumsum(np.array([exp_period]*num_frames))-exp_period+exp_start_time
                exp_start_times.extend(new_exp_start_times)
                extra_vals[0] = ['real_start_time', exp_start_times]

                if self._abort_event.is_set():
                    self.fast_mode_abort_cleanup(det, struck, ab_burst, ab_burst_2,
                        dio_out9, dio_out6, exp_time)
                    aborted = True
                    return False

                logger.debug('Exposures started')
                self._exp_event.set()

                while True:
                    #Struck is_busy doesn't work in thread! So have to go elsewhere

                    exp_done, timeouts = self.get_experiment_status(ab_burst,
                        ab_burst_2, det, timeouts)

                    if exp_done:
                        break

                    if self._abort_event.is_set() or timeouts >= 5:
                        if timeouts >= 5:
                            logger.error(("Exposure aborted because current exposure "
                                "status could not be verified"))
                        self.fast_mode_abort_cleanup(det, struck, ab_burst, ab_burst_2,
                            dio_out9, dio_out6, exp_time)
                        aborted = True
                        break

                    if exp_type != 'muscle':
                        current_meas = struck.get_last_measurement_number()

                        if current_meas != last_meas and current_meas != -1:
                            cvals = struck.read_all()

                            if last_meas == 0:
                                prev_meas = -1
                            else:
                                prev_meas = last_meas

                            self.append_log_counters(cvals, prev_meas, current_meas,
                                data_dir, cur_fprefix, exp_period, num_frames,
                                dark_counts, log_vals, extra_vals)

                            last_meas = current_meas

                    time.sleep(0.01)


                if continuous_exp:
                    dio_out9.write(0)

                if aborted:
                    break

                while time.time() - start_time < num_frames*exp_period:
                    if self._abort_event.is_set() and not aborted:
                        self.fast_mode_abort_cleanup(det, struck, ab_burst, ab_burst_2,
                            dio_out9, dio_out6, exp_time)
                        aborted = True
                        break

                if aborted:
                    break

        if len(scan_motors) == 0: # Base case
            dio_out6.write(1) #Close the slow normally closed xia shutter

            if exp_type != 'muscle':
                current_meas = struck.get_last_measurement_number()
                if current_meas != last_meas or (current_meas == last_meas and current_meas == 0):
                    cvals = struck.read_all()

                    if last_meas == 0:
                        prev_meas = -1
                    else:
                        prev_meas = last_meas

                    self.append_log_counters(cvals, prev_meas, current_meas,
                        data_dir, cur_fprefix, exp_period, num_frames, dark_counts,
                        log_vals, extra_vals)

            else:
                struck.stop()
                measurement = struck.read_all()

                logger.info('Writing counters')
                self.write_counters_muscle(measurement, struck_num_meas, data_dir,
                    cur_fprefix, struck_meas_time, dark_counts, log_vals,
                    kwargs['metadata'])

            ab_burst.get_status() #Maybe need to clear this status?

            while det.get_status() !=0:
                time.sleep(0.001)
                if self._abort_event.is_set() and not aborted:
                    self.fast_mode_abort_cleanup(det, struck, ab_burst, ab_burst_2,
                        dio_out9, dio_out6, exp_time)
                    aborted = True
                    break

            logger.info('Exposures done')

            if self._abort_event.is_set():
                if not aborted:
                    self.fast_mode_abort_cleanup(det, struck, ab_burst, ab_burst_2,
                        dio_out9, dio_out6, exp_time)
                    aborted = True
                return False

        motor.move_absolute(initial_motor_position)

        while motor.is_busy():
            time.sleep(0.01)
            if self._abort_event.is_set():
                motor.stop()

    def fast_exposure(self, data_dir, fprefix, num_frames, exp_time, exp_period,
        exp_type='standard', **kwargs):
        logger.debug('Setting up %s exposure', exp_type)
        det = self._mx_data['det']          #Detector

        struck = self._mx_data['struck']    #Struck SIS3820
        s_counters = self._mx_data['struck_ctrs']

        if exp_type == 'muscle':
            struck_meas_time = kwargs['struck_measurement_time']
            struck_num_meas = kwargs['struck_num_meas']
        else:
            struck_meas_time = 0
            struck_num_meas = 0

        ab_burst = self._mx_data['ab_burst']   #Shutter control signal
        cd_burst = self._mx_data['cd_burst']   #Struck LNE/channel advance signal
        ef_burst = self._mx_data['ef_burst']   #Pilatus trigger signal
        gh_burst = self._mx_data['gh_burst']   #UV trigger
        # dg645_trigger_source = self._mx_data['dg645_trigger_source']

        if exp_type == 'muscle':
            ab_burst_2 = self._mx_data['ab_burst_2']
            cd_burst_2 = self._mx_data['cd_burst_2'] #Struck channel advance
            ef_burst_2 = self._mx_data['ef_burst_2']
            gh_burst_2 = self._mx_data['gh_burst_2']
            # dg645_trigger_source2 = self._mx_data['dg645_trigger_source2']
        else:
            ab_burst_2 = None

        dio_out6 = self._mx_data['dio'][6]      #Xia/wharberton shutter N.C.
        dio_out9 = self._mx_data['dio'][9]      #Shutter control signal (alt.)
        dio_out10 = self._mx_data['dio'][10]    #SRS DG645 trigger

        # det_datadir = self._mx_data['det_datadir']
        # det_filename = self._mx_data['det_filename']
        # det_exp_time = self._mx_data['det_exp_time']
        # det_exp_period = self._mx_data['det_exp_period']

        wait_for_trig = kwargs['wait_for_trig']
        if wait_for_trig:
            num_trig = kwargs['num_trig']
        else:
            num_trig = 1

        shutter_speed_open = kwargs['shutter_speed_open']
        shutter_speed_close = kwargs['shutter_speed_close']
        shutter_pad = kwargs['shutter_pad']
        shutter_cycle = kwargs['shutter_cycle']

        total_shutter_speed = shutter_speed_open+shutter_speed_close+shutter_pad
        s_open_time = shutter_speed_open + shutter_pad

        log_vals = kwargs['struck_log_vals']

        if exp_period > exp_time + total_shutter_speed and exp_period >= shutter_cycle:
            logger.info('Shuttered mode')
            continuous_exp = False
        else:
            logger.info('Continuous mode')
            continuous_exp = True

        dark_counts = []
        for i in range(len(s_counters)):
            if log_vals[i]['dark']:
                dark_counts.append(s_counters[i].get_dark_current())
            else:
                dark_counts.append(0)

        extra_vals = []

        # det.set_duration_mode(num_frames)
        # det.set_trigger_mode(2)
        # det_exp_time.put(exp_time)
        # det_exp_period.put(exp_period)

        det.set_trigger_mode('ext_enable')
        det.set_num_frames(num_frames)
        det.set_exp_time(exp_time)
        det.set_exp_period(exp_period)


        if exp_type == 'muscle':
            logger.debug('muscle setup')
            logger.debug(struck_meas_time)
            logger.debug(struck_num_meas)
            struck.set_measurement_time(struck_meas_time)   #Ignored for external LNE of Struck
            struck.set_num_measurements(struck_num_meas)
            struck.set_trigger_mode(0x2)    #Sets external mode, i.e. counting on first LNE
        else:
            struck.set_measurement_time(exp_time)   #Ignored for external LNE of Struck
            struck.set_num_measurements(num_frames)
            struck.set_trigger_mode(0x8|0x2)    #Sets 'autotrigger' mode, i.e. counting as soon as armed

        # logger.info('setting dg645 trigger soruce')
        # dg645_trigger_source.put(1) #Change this to 2 for external falling edges
        # # Should be 1 for external rising edge, 2 for external falling

        # if exp_type == 'muscle':
        #     dg645_trigger_source2.put(1)

        #Need to clear srs possibly?
        ab_burst.setup(0.000001, 0.000000, 1, 0, 1, 2)
        cd_burst.setup(0.000001, 0.000000, 1, 0, 1, 2)
        ef_burst.setup(0.000001, 0.000000, 1, 0, 1, 2)
        gh_burst.setup(0.000001, 0.000000, 1, 0, 1, 2)

        ab_burst.arm()

        if exp_type == 'muscle':
            ab_burst_2.setup(0.000001, 0.000000, 1, 0, 1, 2)
            cd_burst_2.setup(0.000001, 0.000000, 1, 0, 1, 2)
            ef_burst_2.setup(0.000001, 0.000000, 1, 0, 1, 2)
            gh_burst_2.setup(0.000001, 0.000000, 1, 0, 1, 2)

            ab_burst_2.arm()

        dio_out10.write( 1 )
        time.sleep(0.01)
        dio_out10.write( 0 )

        while (ab_burst.get_status() & 0x1) != 0:
            time.sleep(0.01)

        if not continuous_exp:
            #Shutter opens and closes
            ab_burst.setup(exp_period, exp_time+s_open_time, num_frames, 0, 1, 2)
            cd_burst.setup(exp_period, (exp_period-(exp_time+s_open_time))/10.,
                num_frames, exp_time+s_open_time, 1, 2)
            ef_burst.setup(exp_period, exp_time, num_frames, s_open_time, 1, 2)
            gh_burst.setup(exp_period, exp_period/1.1, num_frames, s_open_time, 1, 2)
        else:
            #Shutter will be open continuously
            if exp_type == 'muscle':
                offset = (exp_period - exp_time)/2.
            else:
                offset = 0

            ab_burst.setup(exp_period, exp_period*(1.-1./1000.), num_frames, 0, 1, 2)
            cd_burst.setup(exp_period, (exp_period-exp_time)/10.,
                num_frames, exp_time+(exp_period-exp_time)/10., 1, 2)
            ef_burst.setup(exp_period, exp_time, num_frames, offset, 1, 2)
            gh_burst.setup(exp_period, exp_period/1.1, num_frames, 0, 1, 2)

        if exp_type == 'muscle':
            ab_burst_2.setup(struck_meas_time, 0, struck_num_meas+1, 0, 1, 2) #Irrelevant
            cd_burst_2.setup(struck_meas_time, struck_meas_time/2., struck_num_meas+1, 0, 1, 2)
            ef_burst_2.setup(struck_meas_time, 0, struck_num_meas+1, 0, 1, 2) #Irrelevant
            gh_burst_2.setup(struck_meas_time, 0, struck_num_meas+1, 0, 1, 2) #Irrelevant

        for cur_trig in range(1,num_trig+1):
            #Runs a loop for each expected trigger signal (internal or external)
            self.return_queue.append(['scan', cur_trig])

            exp_start_num = '000001'

            if wait_for_trig and num_trig > 1:
                cur_fprefix = '{}_{:04}'.format(fprefix, cur_trig)
            else:
                cur_fprefix = fprefix

            if self._settings['add_file_postfix']:
                new_fname = '{}_{}.tif'.format(cur_fprefix, exp_start_num)
            else:
                new_fname = cur_fprefix

            finished = self._inner_fast_exp(det,
                struck, ab_burst, ab_burst_2, dio_out6, dio_out9, dio_out10,
                continuous_exp, wait_for_trig, exp_type, data_dir, new_fname,
                cur_fprefix, log_vals, extra_vals, dark_counts, cur_trig, exp_time,
                exp_period, num_frames, struck_num_meas, struck_meas_time, kwargs)

            if not finished:
                #Abort happened in the inner function
                break

        self._exp_event.clear()

    def wait_for_trigger(self, wait_for_trig, cur_trig, exp_time, ab_burst,
        ab_burst_2, det, struck, dio_out6, dio_out9, dio_out10):
        if not wait_for_trig:
            logger.debug("Sending trigger")
            dio_out10.write(1)
            time.sleep(0.1)
            dio_out10.write(0)
            real_start_time = datetime.datetime.now().isoformat(str(' '))
        else:
            logger.info("Waiting for trigger {}".format(cur_trig))
            self.return_queue.append(['waiting', None])
            ab_burst.get_status() #Maybe need to clear this status?
            waiting = True
            while waiting:
                # logger.debug(ab_burst.get_status())
                waiting = np.any([ab_burst.get_status() == 16777216 for i in range(5)])
                time.sleep(0.01)

                if self._abort_event.is_set():
                    self.fast_mode_abort_cleanup(det, struck, ab_burst, ab_burst_2,
                        dio_out9, dio_out6, exp_time)
                    break

                if det.get_status() == 0:
                    break #In case you miss the srs trigger

            real_start_time = datetime.datetime.now().isoformat(str(' '))

            self.return_queue.append(['exposing', None])

        return real_start_time

    def get_experiment_status(self, ab_burst, ab_burst_2, det, timeouts):
        # logger.debug('getting experiment status')
        try:
            status = ab_burst.get_status()
            timeouts=0

        except Exception:
            logger.debug('Timed out getting DG645 status')

            try:
                status = det.get_status()
                timeouts = 0

            except Exception:
                timeouts = timeouts + 1
                logger.debug('Timed out getting detector status')

        if (status & 0x1) == 0:
            ret_status_1 = True
        else:
            ret_status_1 = False

        if ab_burst_2 is not None:
            try:
                status_2 = ab_burst_2.get_status()

                if (status_2 & 0x1) == 0:
                    ret_status_2 = True
                else:
                    ret_status_2 = False

            except Exception:
                ret_status_2 = True

        else:
            ret_status_2 = True

        ret_status = ret_status_1 and ret_status_2

        return ret_status, timeouts

    def _inner_fast_exp(self, det, struck, ab_burst,
        ab_burst_2, dio_out6, dio_out9, dio_out10, continuous_exp, wait_for_trig,
        exp_type, data_dir, new_fname, cur_fprefix, log_vals, extra_vals,
        dark_counts, cur_trig, exp_time, exp_period, num_frames, struck_num_meas,
        struck_meas_time, kwargs):

        metadata = kwargs['metadata']

        if det.get_status() !=0:
            try:
                det.abort()
            except (mp.Device_Action_Failed_Error, mp.Unparseable_String_Error):
                pass
            try:
                det.abort()
            except (mp.Device_Action_Failed_Error, mp.Unparseable_String_Error):
                pass

        aborted = False

        struck.stop()
        ab_burst.stop()

        if exp_type == 'muscle':
            ab_burst_2.stop()

        dio_out9.write(0) # Make sure the NM shutter is closed
        dio_out10.write(0) # Make sure the trigger is off

        # det_datadir.put(data_dir)

        # det_filename.put(new_fname)

        det.set_data_dir(data_dir)
        det.set_filename(new_fname)

        dio_out6.write(0) #Open the slow normally closed xia shutter

        ab_burst.get_status() #Maybe need to clear this status?

        det.arm()
        struck.start()
        ab_burst.arm()

        if exp_type == 'muscle':
            ab_burst_2.arm()

        if continuous_exp:
            if not exp_type == 'muscle' and not wait_for_trig:
                dio_out9.write(1)

        time.sleep(1)

        real_start_time = self.wait_for_trigger(wait_for_trig, cur_trig, exp_time, ab_burst,
            ab_burst_2, det, struck, dio_out6, dio_out9, dio_out10)

        if self._abort_event.is_set():
            self.fast_mode_abort_cleanup(det, struck, ab_burst, ab_burst_2,
                dio_out9, dio_out6, exp_time)
            aborted = True
            return False

        metadata['Date:'] = real_start_time

        if exp_type != 'muscle':
            self.write_log_header(data_dir, cur_fprefix, log_vals,
                metadata, extra_vals)

        logger.debug('Exposures started')
        self._exp_event.set()

        last_meas = 0

        timeouts = 0

        header_readout_time = time.time()

        while True:
            #Struck is_busy doesn't work in thread! So have to go elsewhere

            exp_done, timeouts = self.get_experiment_status(ab_burst,
                ab_burst_2, det, timeouts)

            if exp_done:
                break

            if self._abort_event.is_set() or timeouts >= 5:
                if timeouts >= 5:
                    logger.error(("Exposure aborted because current exposure "
                        "status could not be verified"))
                self.fast_mode_abort_cleanup(det, struck, ab_burst, ab_burst_2,
                    dio_out9, dio_out6, exp_time)
                aborted = True
                break

            if exp_type != 'muscle' and time.time()-header_readout_time > exp_time:
                current_meas = struck.get_last_measurement_number()

                if current_meas != last_meas and current_meas != -1:
                    logger.debug('getting struck values')
                    cvals = struck.read_all()

                    if last_meas == 0:
                        prev_meas = -1
                    else:
                        prev_meas = last_meas

                    self.append_log_counters(cvals, prev_meas, current_meas,
                        data_dir, cur_fprefix, exp_period, num_frames,
                        dark_counts, log_vals, extra_vals)

                    last_meas = current_meas

                    header_readout_time = time.time()

            time.sleep(0.1)


        if continuous_exp:
            dio_out9.write(0)

        dio_out6.write(1) #Close the slow normally closed xia shutter

        if exp_type != 'muscle':
            current_meas = struck.get_last_measurement_number()
            if current_meas != last_meas or (current_meas == last_meas and current_meas == 0):
                cvals = struck.read_all()

                if last_meas == 0:
                    prev_meas = -1
                else:
                    prev_meas = last_meas

                self.append_log_counters(cvals, prev_meas, current_meas,
                    data_dir, cur_fprefix, exp_period, num_frames, dark_counts,
                    log_vals, extra_vals)

        else:
            struck.stop()
            measurement = struck.read_all()

            logger.info('Writing counters')
            self.write_counters_muscle(measurement, struck_num_meas, data_dir,
                cur_fprefix, struck_meas_time, dark_counts, log_vals,
                kwargs['metadata'])

        ab_burst.get_status() #Maybe need to clear this status?

        while det.get_status() !=0:
            time.sleep(0.001)
            if self._abort_event.is_set() and not aborted:
                self.fast_mode_abort_cleanup(det, struck, ab_burst, ab_burst_2,
                    dio_out9, dio_out6, exp_time)
                aborted = True
                break

        logger.info('Exposures done')

        if self._abort_event.is_set():
            if not aborted:
                self.fast_mode_abort_cleanup(det, struck, ab_burst, ab_burst_2,
                    dio_out9, dio_out6, exp_time)
                aborted = True
            return False

        return True

    def mar_exposure(self, data_dir, fprefix, num_frames, exp_time, exp_period,
        exp_type='mar', **kwargs):
        logger.debug('Setting up %s exposure', exp_type)
        det = self._mx_data['det']          #Detector

        struck = self._mx_data['struck']    #Struck SIS3820
        s_counters = self._mx_data['struck_ctrs']

        dio_out6 = self._mx_data['dio'][6]      #Xia/wharberton shutter N.C.
        dio_out9 = self._mx_data['dio'][9]      #Shutter control signal (alt.)
        dio_out10 = self._mx_data['dio'][10]    #SRS DG645 trigger

        # det_datadir = self._mx_data['det_datadir']
        # det_filename = self._mx_data['det_filename']
        # det_exp_time = self._mx_data['det_exp_time']
        # det_exp_period = self._mx_data['det_exp_period']

        shutter_speed_open = kwargs['shutter_speed_open']
        shutter_speed_close = kwargs['shutter_speed_close']
        shutter_pad = kwargs['shutter_pad']
        shutter_cycle = kwargs['shutter_cycle']

        total_shutter_speed = shutter_speed_open+shutter_speed_close+shutter_pad
        s_open_time = shutter_speed_open + shutter_pad

        log_vals = kwargs['struck_log_vals']

        if exp_period > exp_time + total_shutter_speed and exp_period >= shutter_cycle:
            logger.info('Shuttered mode')
            continuous_exp = False
        else:
            logger.info('Continuous mode')
            continuous_exp = True

        dark_counts = []
        for i in range(len(s_counters)):
            if log_vals[i]['dark']:
                dark_counts.append(s_counters[i].get_dark_current())
            else:
                dark_counts.append(0)

        extra_vals = []

        # det.set_duration_mode(num_frames)
        # det.set_trigger_mode(1)
        # det_exp_time.put(exp_time)
        # det_exp_period.put(exp_period)

        det.set_num_frames(num_frames)
        det.set_trigger_mode('int_enable')
        det.set_exp_time(exp_time)
        det.set_exp_period(exp_period)

        struck.set_measurement_time(exp_time)   #Ignored for external LNE of Struck
        struck.set_num_measurements(num_frames)
        struck.set_trigger_mode(0x1)    #Sets internal trigger mode

        exp_start_num = '000001'

        cur_fprefix = fprefix

        if self._settings['add_file_postfix']:
            new_fname = '{}_{}.tif'.format(cur_fprefix, exp_start_num)
        else:
            new_fname = cur_fprefix

        if det.get_status() !=0:
            try:
                det.abort()
            except (mp.Device_Action_Failed_Error, mp.Unparseable_String_Error):
                pass
            try:
                det.abort()
            except (mp.Device_Action_Failed_Error, mp.Unparseable_String_Error):
                pass

        aborted = False

        struck.stop()

        dio_out9.write(0) # Make sure the NM shutter is closed

        # det_datadir.put(data_dir)

        # det_filename.put(new_fname)

        det.set_data_dir(data_dir)
        det.set_filename(new_fname)

        dio_out6.write(0) #Open the slow normally closed xia shutter

        if exp_type != 'muscle':
            self.write_log_header(data_dir, cur_fprefix, log_vals,
                kwargs['metadata'])

        time.sleep(1)

        if self._abort_event.is_set():
            self.mar_abort_cleanup(det, dio_out9, dio_out6, exp_time)

            aborted = True
            return False

        logger.debug('Exposures started')
        self._exp_event.set()

        last_meas = 0

        timeouts = 0

        if continuous_exp:
            dio_out9.write(1)

        for i in range(num_frames):
            for scaler in s_counters:
                scaler.clear()
            timer.clear()
            if timer.is_busy():
                timer.stop()

            if not continuous_exp:
                dio_out9.write(1)

            timer.start(exp_time)

            while timer.is_busy() != 0:
                time.sleep(.01)
                if self._abort_event.is_set() and not aborted:
                    self.mar_abort_cleanup(det, dio_out9, dio_out6, exp_time)
                    aborted = True
                    break

            if not continuous_exp:
                dio_out9.write(0)

            result = [str(scaler.read()) for scaler in scalers]

            self.append_log_counters(cvals, prev_meas, current_meas,
                    data_dir, cur_fprefix, exp_period, num_frames,
                    dark_counts, log_vals, extra_vals)

            while det.get_status() !=0:
                time.sleep(0.001)
                if self._abort_event.is_set() and not aborted:
                    self.mar_abort_cleanup(det, dio_out9, dio_out6, exp_time)
                    aborted = True
                    break

        if continuous_exp:
            dio_out9.write(0)

        dio_out6.write(1) #Close the slow normally closed xia shutter


        logger.info('Exposures done')

        if self._abort_event.is_set():
            if not aborted:
                self.fast_mode_abort_cleanup(det, struck, ab_burst, ab_burst_2,
                    dio_out9, dio_out6, exp_time)
                aborted = True
            return False

        self._exp_event.clear()

    def write_log_header(self, data_dir, fprefix, log_vals, metadata,
            extra_vals=None):

        if self._timeout_event.is_set():
            data_dir = os.path.expanduser('~')

        else:
            data_dir = data_dir.replace(self._settings['remote_dir_root'],
                self._settings['local_dir_root'], 1)

            try:
                subprocess.check_call(['test', '-d', data_dir], timeout=30)
            except Exception:
                self._timeout_event.set()
                self.return_queue.append(['timeout', [data_dir, os.path.expanduser('~')]])
                data_dir = os.path.expanduser('~')


        header = self.format_log_header(metadata, log_vals, extra_vals)

        log_file = os.path.join(data_dir, '{}.log'.format(fprefix))


        with open(log_file, 'w') as f:
            f.write(header)

        logger.info(header.split('\n')[-2])

    def append_log_counters(self, cvals, prev_meas, cur_meas, data_dir,
            fprefix, exp_period, num_frames, dark_counts, log_vals,
            extra_vals=None):
        logger.debug('Appending log counters to file')

        if self._timeout_event.is_set():
            data_dir = os.path.expanduser('~')

        else:
            data_dir = data_dir.replace(self._settings['remote_dir_root'],
                self._settings['local_dir_root'], 1)

            try:
                subprocess.check_call(['test', '-d', data_dir], timeout=30)
            except Exception:
                self._timeout_event.set()
                self.return_queue.append(['timeout', [data_dir, os.path.expanduser('~')]])
                data_dir = os.path.expanduser('~')

        zpad = 6

        log_file = os.path.join(data_dir, '{}.log'.format(fprefix))

        with open(log_file, 'a') as f:
            for i in range(prev_meas+1, cur_meas+1):
                val = self.format_log_value(i, fprefix, exp_period, cvals,
                    log_vals, dark_counts, extra_vals, zpad)

                f.write(val)

                logger.info(val.rstrip('\n'))

    def write_counters_struck(self, cvals, num_frames, data_dir,
            fprefix, exp_period, dark_counts, log_vals, metadata,
            extra_vals=None):
        if self._timeout_event.is_set():
            data_dir = os.path.expanduser('~')

        else:
            data_dir = data_dir.replace(self._settings['remote_dir_root'],
                self._settings['local_dir_root'], 1)

            try:
                subprocess.check_call(['test', '-d', data_dir], timeout=30)
            except Exception:
                self._timeout_event.set()
                self.return_queue.append(['timeout', [data_dir, os.path.expanduser('~')]])
                data_dir = os.path.expanduser('~')

        header = self.format_log_header(metadata, log_vals, extra_vals)

        logger.info(header.split('\n')[-2])

        zpad = 6

        log_file = os.path.join(data_dir, '{}.log'.format(fprefix))

        with open(log_file, 'w') as f:
            f.write(header)
            for i in range(num_frames):
                val = self.format_log_value(i, fprefix, exp_period, cvals,
                    log_vals, dark_counts, extra_vals, zpad)

                f.write(val)

    def format_log_header(self, metadata, log_vals, extra_vals):
        header = self._get_header(metadata, log_vals)

        if extra_vals is not None:
            header = header.rstrip('\n')
            for ev in extra_vals:
                header = header + '\t{}'.format(ev[0])
            header = header + '\n'

        return header

    def format_log_value(self, index, fprefix, exp_period, cvals, log_vals, dark_counts,
        extra_vals, zpad):

        if self._settings['add_file_postfix']:
            val = "{0}_{1:0{2}d}.tif".format(fprefix, index+1, zpad)
        else:
            val = "{0}_{1:0{2}d}".format(fprefix, index+1, zpad)

        val = val + "\t{0}".format(exp_period*index)

        exp_time = cvals[0][index]/50.e6
        val = val + "\t{}".format(exp_time)

        for j, log in enumerate(log_vals):
            dark = dark_counts[j]
            scale = log['scale']
            offset = log['offset']
            chan = log['channel']

            counter = (cvals[chan][index]-(dark+offset)*exp_time)/scale

            if log['norm_time'] and exp_time > 0:
                counter = counter/exp_time

            val = val + "\t{}".format(counter)

        if extra_vals is not None:
            for ev in extra_vals:
                val = val + "\t{}".format(ev[1][index])

        val = val + "\n"

        return val

    def write_counters_muscle(self, cvals, num_frames, data_dir, fprefix,
        exp_period, dark_counts, log_vals, metadata, extra_vals=None):
        if self._timeout_event.is_set():
            data_dir = os.path.expanduser('~')

        else:
            data_dir = data_dir.replace(self._settings['remote_dir_root'],
                self._settings['local_dir_root'], 1)

            try:
                subprocess.check_call(['test', '-d', data_dir], timeout=30)
            except Exception:
                self._timeout_event.set()
                self.return_queue.append(['timeout', [data_dir, os.path.expanduser('~')]])
                data_dir = os.path.expanduser('~')

        header = self._get_header(metadata, log_vals)

        if extra_vals is not None:
            header.rstrip('\n')
            for ev in extra_vals:
                header = header + '\t{}'.format(ev[0])
            header = header + '\n'

            ev_len = len(extra_vals)
        else:
            ev_len = 0

        log_file = os.path.join(data_dir, '{}.log'.format(fprefix))
        log_summary_file = os.path.join(data_dir, '{}_summary.log'.format(fprefix))

        filenum = 0
        prev_pil_en_ctr = 0
        write_summary = False
        sum_start = 0
        sum_end = 0

        data = [[] for i in range(len(log_vals)+ev_len+3)]

        avg_index = []
        for l, log in enumerate(log_vals):
            if log['norm_time']:
                avg_index.append(l)

        # logger.debug(avg_index)

        zpad = 6

        with open(log_file, 'w') as f, open(log_summary_file, 'w') as f_sum:
            f.write(header)
            f_sum.write(header)

            for i in range(num_frames):
                write_summary = False

                exp_time = cvals[0][i]/50.e6
                val = "{}\t{}".format(exp_period*i, exp_time)

                data[1].append(exp_period*i)
                data[2].append(exp_time)

                for j, log in enumerate(log_vals):
                    dark = dark_counts[j]
                    scale = log['scale']
                    offset = log['offset']
                    chan = log['channel']

                    counter = (cvals[chan][i]-(dark+offset)*exp_time)/scale

                    if log['norm_time'] and exp_time > 0:
                        counter = counter/exp_time

                    val = val + "\t{}".format(counter)

                    if log['name'] == 'Detector_Enable':
                        if prev_pil_en_ctr < 3.0 and counter > 3.0:
                            filenum = filenum + 1
                            sum_start = i

                        elif prev_pil_en_ctr > 3.0 and counter < 3.0:
                            sum_end = i
                            write_summary = True

                        if counter > 3.0:
                            pil_file = True
                        else:
                            pil_file = False

                        prev_pil_en_ctr = counter

                    data[j+3].append(counter)

                if extra_vals is not None:
                    for k, ev in enumerate(extra_vals):
                        val = val + "\t{}".format(ev[1][i])

                        data[len(log_vals)+3+k].append(ev[1][i])

                if pil_file:
                    fname = "{0}_{1:0{2}d}".format(fprefix, filenum, zpad)

                    if self._settings['add_file_postfix']:
                        fname = fname + ".tif"

                else:
                    fname = "no_image"

                val = fname + '\t' + val
                val = val + "\n"

                f.write(val)

                data[0].append(fname)

                if write_summary:
                    ctr_sum_vals = []

                    for m, ctr in enumerate(data[2:]):
                        if m-1 in avg_index:
                            ctr_sum_vals.append('{}'.format(np.mean(ctr[sum_start:sum_end])))
                        else:
                            ctr_sum_vals.append('{}'.format(np.sum(ctr[sum_start:sum_end])))

                    sum_val = '{}\t{}\t'.format(data[0][sum_start], data[1][sum_start])
                    sum_val = sum_val + '\t'.join(ctr_sum_vals)
                    sum_val = sum_val + '\n'
                    f_sum.write(sum_val)

    def _get_header(self, metadata, log_vals, fname=True):
        header = ''
        for key, value in metadata.items():
            if key == 'Notes:':
                for line in value.split('\n'):
                    header = header + '#{}\t{}\n'.format(key, line)
            else:
                header = header + '#{}\t{}\n'.format(key, value)

        if fname:
            header=header+'#Filename\tstart_time\texposure_time'
        else:
            header=header+'#start_time\texposure_time'

        for log in log_vals:
            header = header+'\t{}'.format(log['name'])

        header = header + '\n'

        return header

    def _add_metadata(self, metadata):
        if self._settings['use_old_i0_gain']:
            i0_gain = self._mx_data['ki0'].get_gain()
        else:
            value = self._mx_data['ki0'].get()
            if value == 0:
                i0_gain = 1e+07
            elif value == 1:
                i0_gain = 1e+06
            elif value == 2:
                i0_gain = 1e+05
            elif value == 3:
                i0_gain = 1e+04
            elif value == 4:
                i0_gain = 1e+02

        metadata['I0 gain:'] = '{:.0e}'.format(i0_gain)
        metadata['I1 gain:'] = '{:.0e}'.format(self._mx_data['ki1'].get_gain())
        metadata['I2 gain:'] = '{:.0e}'.format(self._mx_data['ki2'].get_gain())
        metadata['I3 gain:'] = '{:.0e}'.format(self._mx_data['ki3'].get_gain())

        atten_length = 0
        for atten in sorted(self._mx_data['attenuators'].keys()):
            atten_in = not self._mx_data['attenuators'][atten].read()
            if atten_in:
                atten_str = 'In'
            else:
                atten_str = 'Out'

            metadata['Attenuator, {} foil:'.format(atten)] = atten_str

            if atten_in:
                atten_length = atten_length + atten
        atten_length = 20*atten_length

        atten = np.exp(-atten_length/256.568) #256.568 is Al attenuation length at 12 keV

        if atten > 0.1:
            atten = '{}'.format(round(atten, 3))
        elif atten > 0.01:
            atten = '{}'.format(round(atten, 4))
        else:
            atten = '{}'.format(round(atten, 5))

        metadata['Nominal Transmission (12 keV):'] = atten

        return metadata

    def get_motor(self, motor_name, motor_type, motor_params=None):
        if motor_type == 'MX':
            logger.debug('Motor: {}'.format(motor_name))
            if motor_name in self._mx_data['motors']:
                motor = self._mx_data['motors'][motor_name]
            else:
                motor = self._mx_data['mx_db'].get_record(motor_name)
                self._mx_data['motors'][motor_name] = motor

        elif motor_type == 'Newport':
            if self.xps is None:
                np_group = motor_params['np_group']
                np_index = motor_params['np_index']
                np_axes = motor_params['np_axes']
                motor_ip = motor_params['motor_ip']
                motor_port = int(motor_params['motor_port'])
                self.xps = xps_drivers.XPS()
                motor = motorcon.NewportXPSSingleAxis('Scan', self.xps,
                    motor_ip, motor_port, 20, np_group, np_axes,
                    motor_name, np_index)

        return motor

    def run_test_scan(self, scan_settings, abort_event, end_callback):
        num_scans = scan_settings['num_scans']

        for current_run in range(1,num_scans+1):
            scan_motors = copy.deepcopy(scan_settings['motors'])

            self._inner_scan_test(scan_settings,
                scan_motors, OrderedDict(), current_run, abort_event)

        end_callback()

    def _inner_scan_test(self, scan_settings, scan_motors,
        motor_positions, current_run, abort_event):
        motor_num, motor_params = scan_motors.popitem(False)

        motor_name = motor_params['motor']
        start = motor_params['start']
        stop = motor_params['stop']
        step_size = motor_params['step']
        motor_type = motor_params['type']
        scan_type = motor_params['scan_type']

        if motor_type == 'Newport':
            motor_get_params = copy.deepcopy(motor_params)
            motor_get_params['motor_ip'] = scan_settings['motor_ip']
            motor_get_params['motor_port'] = scan_settings['motor_port']

        motor = self.get_motor(motor_name, motor_type, motor_params)

        initial_motor_position = float(motor.get_position())

        if start < stop:
            mtr_positions = np.arange(start, stop+step_size, step_size)

            if mtr_positions[-1] > stop:
                mtr_positions = mtr_positions[:-1]
        else:
            mtr_positions = np.arange(stop, start+step_size, step_size)

            if mtr_positions[-1] > start:
                mtr_positions = mtr_positions[:-1]

            mtr_positions = mtr_positions[::-1]

        if 'relative' == scan_type.lower():
            mtr_positions += initial_motor_position

        for position in mtr_positions:
            logger.debug('Position: {}'.format(position))
            if abort_event.is_set():
                break

            motor.move_absolute(position)

            while motor.is_busy():
                time.sleep(0.01)
                if abort_event.is_set():
                    motor.stop()
                    motor.move_absolute(initial_motor_position)
                    break

            if abort_event.is_set():
                break

            motor_positions['m{}'.format(motor_num)] = position

            if len(scan_motors) > 0: # Recursive case
                my_scan_motors = copy.deepcopy(scan_motors)

                self._inner_scan_test(scan_settings, my_scan_motors,
                    motor_positions, current_run, abort_event)

            else:   # Base case for recursion:

                time.sleep(0.1)

        motor.move_absolute(initial_motor_position)

        self.test_scan_running = False


    def fast_mode_abort_cleanup(self, det, struck, ab_burst, ab_burst_2, dio_out9,
        dio_out6, exp_time):
        logger.info("Aborting fast exposure")
        if exp_time < 60:
            logger.debug('Aborting detector')
            try:
                det.stop()
            except (mp.Device_Action_Failed_Error, mp.Unparseable_String_Error):
                pass
            try:
                det.abort()
            except (mp.Device_Action_Failed_Error, mp.Unparseable_String_Error):
                pass
        else:
            try:
                det.abort()
            except (mp.Device_Action_Failed_Error, mp.Unparseable_String_Error):
                pass
            try:
                det.abort()
            except (mp.Device_Action_Failed_Error, mp.Unparseable_String_Error):
                pass

        logger.debug('Stopping triggers')
        ab_burst.stop()

        if ab_burst_2 is not None:
            ab_burst_2.stop()

        logger.debug('Stopping Struck')
        struck.stop()

        logger.debug('Closing shutters')
        dio_out9.write(0) #Close the fast shutter
        dio_out6.write(1) #Close the slow normally closed xia shutter

    def mar_abort_cleanup(self, det, dio_out9, dio_out6, exp_time):
        logger.info('Aborting mar exposure')

        if exp_time < 60:
            logger.debug('Aborting detector')
            try:
                det.stop()
            except (mp.Device_Action_Failed_Error, mp.Unparseable_String_Error):
                pass
            try:
                det.abort()
            except (mp.Device_Action_Failed_Error, mp.Unparseable_String_Error):
                pass
        else:
            try:
                det.abort()
            except (mp.Device_Action_Failed_Error, mp.Unparseable_String_Error):
                pass
            try:
                det.abort()
            except (mp.Device_Action_Failed_Error, mp.Unparseable_String_Error):
                pass

        logger.debug('Stopping Struck')
        struck.stop()

        logger.debug('Closing shutters')
        dio_out9.write(0) #Close the fast shutter
        dio_out6.write(1) #Close the slow normally closed xia shutter

    def tr_abort_cleanup(self, det, struck, ab_burst, dio_out9, dio_out6,
        comp_settings, exp_time):
        logger.info("Aborting trsaxs exposure")

        if exp_time < 60:
            try:
                det.stop()
            except (mp.Device_Action_Failed_Error, mp.Unparseable_String_Error):
                pass
            try:
                det.abort()
            except (mp.Device_Action_Failed_Error, mp.Unparseable_String_Error):
                pass
        else:
            try:
                det.abort()
            except (mp.Device_Action_Failed_Error, mp.Unparseable_String_Error):
                pass
            try:
                det.abort()
            except (mp.Device_Action_Failed_Error, mp.Unparseable_String_Error):
                pass

        struck.stop()
        ab_burst.stop()
        dio_out9.write(0) #Close the fast shutter
        dio_out6.write(1) #Close the slow normally closed xia shutter

        if 'trsaxs_scan' in comp_settings:
            tr_scan_settings = comp_settings['trsaxs_scan']

        if 'trsaxs_flow' in comp_settings:
            tr_flow_settings = comp_settings['trsaxs_flow']
            tr_flow = True
        else:
            tr_flow = False

        motor_type = tr_scan_settings['motor_type']
        motor = tr_scan_settings['motor']

        if motor_type == 'Newport_XPS':
            pco_direction = tr_scan_settings['pco_direction']
            x_motor = str(tr_scan_settings['motor_x_name'])
            y_motor = str(tr_scan_settings['motor_y_name'])

        motor.stop()

        if motor_type == 'Newport_XPS':
            if pco_direction == 'x':
                logger.debug('starting x pco')
                motor.start_position_compare(x_motor)
            else:
                logger.debug('starting x pco')
                motor.start_position_compare(y_motor)

        if tr_flow:
            stop_flow_event = tr_flow_settings['stop_flow_event']
            stop_flow_event.set()

    def abort_all(self):
        logger.info("Aborting exposure due to unexpected error")

        det = self._mx_data['det']          #Detector

        struck = self._mx_data['struck']    #Struck SIS3820
        joerger = self._mx_data['joerger']

        ab_burst = self._mx_data['ab_burst']   #Shutter control signal
        ab_burst_2 = self._mx_data['ab_burst_2']   #Shutter control signal

        dio_out6 = self._mx_data['dio'][6]      #Xia/wharberton shutter N.C.
        dio_out9 = self._mx_data['dio'][9]      #Shutter control signal (alt.)
        dio_out10 = self._mx_data['dio'][10]    #SRS DG645 trigger
        dio_out11 = self._mx_data['dio'][11]    #Struck LNE/channel advance signal (alt.)

        try:
            det.abort()
        except (mp.Device_Action_Failed_Error, mp.Unparseable_String_Error):
            pass
        try:
            det.abort()
        except (mp.Device_Action_Failed_Error, mp.Unparseable_String_Error):
            pass

        struck.stop()
        joerger.stop()
        ab_burst.stop()
        ab_burst_2.stop()
        dio_out6.write(1) #Close the slow normally closed xia shutter]
        dio_out9.write(0) #Close the fast shutter
        dio_out10.write(0)
        dio_out11.write(0)

        self._abort_event.set()
        self._exp_event.clear()
        self.command_queue.clear()
        self.return_queue.clear()

    def _abort(self):
        """
        Clears the ``command_queue`` and the ``return_queue``.
        """
        logger.info("Aborting exposure control thread %s current and future commands", self.name)
        self.command_queue.clear()
        self.return_queue.clear()

        self._abort_event.clear()
        logger.debug("Exposure control thread %s aborted", self.name)

    def stop(self):
        """Stops the thread cleanly."""
        logger.info("Starting to clean up and shut down exposure control thread: %s", self.name)
        self._stop_event.set()

class ExpPanel(wx.Panel):
    """
    Exposure panel
    """
    def __init__(self, settings, *args, **kwargs):
        """
        """

        wx.Panel.__init__(self, *args, **kwargs)
        logger.debug('Initializing ExpPanel')

        self.settings = settings
        self._exp_status = 'Ready'
        self._time_remaining = 0
        self.run_number = '_{:03d}'.format(self.settings['run_num'])
        self._preparing_exposure = False

        self.exp_cmd_q = deque()
        self.exp_ret_q = deque()
        self.abort_event = threading.Event()
        self.exp_event = threading.Event()
        self.timeout_event = threading.Event()
        self.exp_con = ExpCommThread(self.exp_cmd_q, self.exp_ret_q, self.abort_event,
            self.exp_event, self.timeout_event, self.settings, 'ExpCon')
        self.exp_con.start()

        # self.exp_con = None #For testing purposes

        self.current_exposure_values = {}

        self.tr_timer = wx.Timer(self)
        self.Bind(wx.EVT_TIMER, self._on_tr_timer, self.tr_timer)

        self.top_sizer = self._create_layout()

        self.SetSizer(self.top_sizer)

        self._initialize()

    def _FromDIP(self, size):
        # This is a hack to provide easy back compatibility with wxpython < 4.1
        try:
            return self.FromDIP(size)
        except Exception:
            return size

    def _create_layout(self):
        """Creates the layout for the panel."""
        self.data_dir = wx.TextCtrl(self, value=self.settings['data_dir'],
            style=wx.TE_READONLY)
        self.data_dir.Bind(wx.EVT_RIGHT_DOWN, self._on_data_dir_right_click)

        file_open = wx.ArtProvider.GetBitmap(wx.ART_FOLDER_OPEN, wx.ART_BUTTON)
        self.change_dir_btn = wx.BitmapButton(self, bitmap=file_open,
            size=self._FromDIP((file_open.GetWidth()+15, -1)))
        self.change_dir_btn.Bind(wx.EVT_BUTTON, self._on_change_dir)

        self.filename = wx.TextCtrl(self, value=self.settings['filename'],
            validator=utils.CharValidator('fname'))
        self.num_frames = wx.TextCtrl(self, value=self.settings['exp_num'],
            size=self._FromDIP((60,-1)), validator=utils.CharValidator('int'))
        self.exp_time = wx.TextCtrl(self, value=self.settings['exp_time'],
            size=self._FromDIP((60,-1)), validator=utils.CharValidator('float'))
        self.exp_period = wx.TextCtrl(self, value=self.settings['exp_period'],
            size=self._FromDIP((60,-1)), validator=utils.CharValidator('float'))
        self.run_num = wx.StaticText(self, label=self.run_number)
        self.wait_for_trig = wx.CheckBox(self, label='Wait for external trigger')
        self.wait_for_trig.SetValue(self.settings['wait_for_trig'])
        self.num_trig = wx.TextCtrl(self, value=self.settings['num_trig'],
            size=self._FromDIP((60,-1)), validator=utils.CharValidator('int'))
        self.muscle_sampling = wx.TextCtrl(self, value=self.settings['struck_measurement_time'],
            size=self._FromDIP((60,-1)), validator=utils.CharValidator('float'))

        if 'trsaxs_scan' in self.settings['components']:
            self.num_frames.SetValue('')
            self.num_frames.Disable()
            self.exp_time.Bind(wx.EVT_TEXT, self._on_change_exp_param)
            self.exp_period.Bind(wx.EVT_TEXT, self._on_change_exp_param)

        file_prefix_sizer = wx.BoxSizer(wx.HORIZONTAL)
        file_prefix_sizer.Add(self.filename, proportion=1)
        file_prefix_sizer.Add(self.run_num, flag=wx.ALIGN_BOTTOM)

        self.exp_name_sizer = wx.GridBagSizer(vgap=self._FromDIP(5),
            hgap=self._FromDIP(5))

        self.exp_name_sizer.Add(wx.StaticText(self, label='Data directory:'), (0,0),
            flag=wx.ALIGN_CENTER_VERTICAL)
        self.exp_name_sizer.Add(self.data_dir, (0,1), flag=wx.ALIGN_CENTER_VERTICAL|wx.EXPAND)
        self.exp_name_sizer.Add(self.change_dir_btn, (0,2), flag=wx.ALIGN_CENTER_VERTICAL)

        self.exp_name_sizer.Add(wx.StaticText(self, label='File prefix:'), (1,0),
            flag=wx.ALIGN_CENTER_VERTICAL)
        self.exp_name_sizer.Add(file_prefix_sizer, (1,1), span=(1,2),
            flag=wx.ALIGN_CENTER_VERTICAL|wx.EXPAND)

        self.exp_name_sizer.AddGrowableCol(1)


        self.exp_time_sizer = wx.BoxSizer(wx.HORIZONTAL)
        self.exp_time_sizer.Add(wx.StaticText(self, label='Number of frames:'),
            flag=wx.ALIGN_CENTER_VERTICAL)
        self.exp_time_sizer.Add(self.num_frames, border=self._FromDIP(5),
            flag=wx.ALIGN_CENTER_VERTICAL|wx.LEFT)
        self.exp_time_sizer.Add(wx.StaticText(self, label='Exp. time [s]:'),
            border=self._FromDIP(5), flag=wx.ALIGN_CENTER_VERTICAL|wx.LEFT)
        self.exp_time_sizer.Add(self.exp_time, border=self._FromDIP(5),
            flag=wx.ALIGN_CENTER_VERTICAL|wx.LEFT)
        self.exp_time_sizer.Add(wx.StaticText(self, label='Exp. period [s]:'),
            border=self._FromDIP(5), flag=wx.ALIGN_CENTER_VERTICAL|wx.LEFT)
        self.exp_time_sizer.Add(self.exp_period, border=self._FromDIP(5),
            flag=wx.ALIGN_CENTER_VERTICAL|wx.LEFT)


        trig_sizer = wx.BoxSizer(wx.HORIZONTAL)
        trig_sizer.Add(self.wait_for_trig, flag=wx.ALIGN_CENTER_VERTICAL)
        trig_sizer.Add(wx.StaticText(self, label='Number of triggers:'),
            border=self._FromDIP(15), flag=wx.LEFT|wx.ALIGN_CENTER_VERTICAL)
        trig_sizer.Add(self.num_trig, border=self._FromDIP(2),
            flag=wx.LEFT|wx.ALIGN_CENTER_VERTICAL)
        trig_sizer.AddStretchSpacer(1)


        self.muscle_sizer = wx.BoxSizer(wx.HORIZONTAL)
        self.muscle_sizer.Add(wx.StaticText(self, label='Parameter sampling time [s]:'),
            flag=wx.ALIGN_CENTER_VERTICAL)
        self.muscle_sizer.Add(self.muscle_sampling, border=self._FromDIP(2),
            flag=wx.ALIGN_CENTER_VERTICAL|wx.LEFT)
        self.muscle_sizer.AddStretchSpacer(1)

        self.advanced_options = wx.StaticBoxSizer(wx.StaticBox(self,
            label='Advanced Options'), wx.VERTICAL)
        self.advanced_options.Add(trig_sizer, border=self._FromDIP(5),
            flag=wx.ALL|wx.EXPAND)
        self.advanced_options.Add(self.muscle_sizer, border=self._FromDIP(5),
            flag=wx.ALL|wx.EXPAND)

        self.start_scan_btn = wx.Button(self, label='Start Scan')
        self.start_scan_btn.Bind(wx.EVT_BUTTON, self._on_start_exp)
        self.start_scan_btn.Hide()

        if ('scan' in self.settings['components']
            or 'trsaxs_scan' in self.settings['components']):
            self.start_scan_btn.Show()

        self.start_exp_btn = wx.Button(self, label='Start Exposure')
        self.start_exp_btn.Bind(wx.EVT_BUTTON, self._on_start_exp)

        self.stop_exp_btn = wx.Button(self, label='Stop Exposure')
        self.stop_exp_btn.Bind(wx.EVT_BUTTON, self._on_stop_exp)
        self.stop_exp_btn.Disable()

        self.exp_btn_sizer = wx.BoxSizer(wx.HORIZONTAL)
        self.exp_btn_sizer.AddStretchSpacer(1)
        self.exp_btn_sizer.Add(self.start_scan_btn, border=self._FromDIP(5),
            flag=wx.ALIGN_CENTER_VERTICAL|wx.RIGHT)
        self.exp_btn_sizer.Add(self.start_exp_btn, border=self._FromDIP(5),
            flag=wx.ALIGN_CENTER_VERTICAL|wx.RIGHT)
        self.exp_btn_sizer.Add(self.stop_exp_btn, border=self._FromDIP(5),
            flag=wx.ALIGN_CENTER_VERTICAL|wx.LEFT)
        self.exp_btn_sizer.AddStretchSpacer(1)

        exp_ctrl_box_sizer = wx.StaticBoxSizer(wx.StaticBox(self,
            label='Exposure Controls'), wx.VERTICAL)

        exp_ctrl_box_sizer.Add(self.exp_name_sizer, border=self._FromDIP(5),
            flag=wx.EXPAND|wx.TOP|wx.LEFT|wx.RIGHT)
        exp_ctrl_box_sizer.Add(self.exp_time_sizer, border=self._FromDIP(5),
            flag=wx.TOP|wx.LEFT|wx.RIGHT)
        exp_ctrl_box_sizer.Add(self.advanced_options, border=self._FromDIP(5),
            flag=wx.TOP|wx.LEFT|wx.RIGHT|wx.EXPAND)
        exp_ctrl_box_sizer.Add(self.exp_btn_sizer, border=self._FromDIP(5),
            flag=wx.EXPAND|wx.ALL)

        exp_ctrl_box_sizer.Show(self.advanced_options,
            self.settings['show_advanced_options'], recursive=True)

        if self.settings['show_advanced_options']:
            self.advanced_options.Show(self.muscle_sizer,
            self.settings['tr_muscle_exp'], recursive=True)


        self.status = wx.StaticText(self, label='Ready', style=wx.ST_NO_AUTORESIZE,
            size=self._FromDIP((150, -1)))
        self.status.SetForegroundColour(wx.RED)
        fsize = self.GetFont().GetPointSize()
        font = wx.Font(fsize, wx.DEFAULT, wx.NORMAL, wx.BOLD)
        self.status.SetFont(font)

        self.time_remaining = wx.StaticText(self, label='0', style=wx.ST_NO_AUTORESIZE,
            size=self._FromDIP((100, -1)))
        self.time_remaining.SetFont(font)

        self.scan_number = wx.StaticText(self, label='1', style=wx.ST_NO_AUTORESIZE,
            size=self._FromDIP((30, -1)))
        self.scan_number.SetFont(font)

        self.scan_num_sizer = wx.BoxSizer(wx.HORIZONTAL)
        self.scan_num_sizer.Add(wx.StaticText(self, label='Current scan:'),
            flag=wx.ALIGN_CENTER_VERTICAL|wx.RESERVE_SPACE_EVEN_IF_HIDDEN)
        self.scan_num_sizer.Add(self.scan_number, border=self._FromDIP(5),
            flag=wx.ALIGN_CENTER_VERTICAL|wx.LEFT|wx.RESERVE_SPACE_EVEN_IF_HIDDEN)

        self.exp_status_sizer = wx.StaticBoxSizer(wx.StaticBox(self,
            label='Exposure Status'), wx.HORIZONTAL)

        self.exp_status_sizer.Add(wx.StaticText(self, label='Status:'),
            border=self._FromDIP(5),
            flag=wx.ALIGN_CENTER_VERTICAL|wx.TOP|wx.LEFT|wx.BOTTOM)
        self.exp_status_sizer.Add(self.status, border=self._FromDIP(5),
            flag=wx.ALIGN_CENTER_VERTICAL|wx.LEFT|wx.TOP|wx.BOTTOM)
        self.exp_status_sizer.Add(wx.StaticText(self, label='Time remaining:'),
            border=self._FromDIP(5),
            flag=wx.ALIGN_CENTER_VERTICAL|wx.LEFT|wx.TOP|wx.BOTTOM)
        self.exp_status_sizer.Add(self.time_remaining, border=self._FromDIP(5),
            flag=wx.ALIGN_CENTER_VERTICAL|wx.LEFT|wx.TOP|wx.BOTTOM)
        self.exp_status_sizer.Add(self.scan_num_sizer, border=self._FromDIP(5),
            flag=wx.ALIGN_CENTER_VERTICAL|wx.ALL|wx.RESERVE_SPACE_EVEN_IF_HIDDEN)
        self.exp_status_sizer.AddStretchSpacer(1)

        self.exp_status_sizer.Hide(self.scan_num_sizer, recursive=True)

        top_sizer = wx.BoxSizer(wx.VERTICAL)
        top_sizer.Add(exp_ctrl_box_sizer, flag=wx.EXPAND)
        top_sizer.Add(self.exp_status_sizer, border=self._FromDIP(10),
            flag=wx.EXPAND|wx.TOP)

        return top_sizer

    def _initialize(self):
        bc_pv, connected = self._initialize_pv(self.settings['beam_current_pv'])
        if connected:
            self.beam_current_pv = bc_pv
        else:
            self.beam_current_pv = None

        fe_pv, connected = self._initialize_pv(self.settings['fe_shutter_pv'])
        if connected:
            self.fe_shutter_pv = fe_pv
        else:
            self.fe_shutter_pv = None

        d_pv, connected = self._initialize_pv(self.settings['d_shutter_pv'])
        if connected:
            self.d_shutter_pv = d_pv
        else:
            self.d_shutter_pv = None

        col_pv, connected = self._initialize_pv(self.settings['col_vac_pv'])
        if connected:
            self.col_vac_pv = col_pv
        else:
            self.col_vac_pv = None

        guard_pv, connected = self._initialize_pv(self.settings['guard_vac_pv'])
        if connected:
            self.guard_vac_pv = guard_pv
        else:
            self.guard_vac_pv = None

        sample_pv, connected = self._initialize_pv(self.settings['sample_vac_pv'])
        if connected:
            self.sample_vac_pv = sample_pv
        else:
            self.sample_vac_pv = None

        sc_pv, connected = self._initialize_pv(self.settings['sc_vac_pv'])
        if connected:
            self.sc_vac_pv = sc_pv
        else:
            self.sc_vac_pv = None

        a_T_pv, connected = self._initialize_pv(self.settings['a_hutch_T_pv'])
        if connected:
            self.a_hutch_T_pv = a_T_pv
        else:
            self.a_hutch_T_pv = None

        a_H_pv, connected = self._initialize_pv(self.settings['a_hutch_H_pv'])
        if connected:
            self.a_hutch_H_pv = a_H_pv
        else:
            self.a_hutch_H_pv = None

        c_T_pv, connected = self._initialize_pv(self.settings['c_hutch_T_pv'])
        if connected:
            self.c_hutch_T_pv = c_T_pv
        else:
            self.c_hutch_T_pv = None

        c_H_pv, connected = self._initialize_pv(self.settings['c_hutch_H_pv'])
        if connected:
            self.c_hutch_H_pv = c_H_pv
        else:
            self.c_hutch_H_pv = None

        d_T_pv, connected = self._initialize_pv(self.settings['d_hutch_T_pv'])
        if connected:
            self.d_hutch_T_pv = d_T_pv
        else:
            self.d_hutch_T_pv = None

        d_H_pv, connected = self._initialize_pv(self.settings['d_hutch_H_pv'])
        if connected:
            self.d_hutch_H_pv = d_H_pv
        else:
            self.d_hutch_H_pv = None

        self.warning_dialog = None
        self.timeout_dialog = None

        self.pipeline_ctrl = None
        self.pipeline_timer = None

    def _initialize_pv(self, pv_name):
        pv = epics.get_pv(pv_name)
        connected = pv.wait_for_connection(5)

        if not connected:
            logger.error('Failed to connect to EPICS PV %s on startup', pv_name)

        return pv, connected


    def _on_change_dir(self, evt):
        with wx.DirDialog(self, "Select Directory", self.data_dir.GetValue()) as fd:
            if fd.ShowModal() == wx.ID_CANCEL:
                return

            pathname = fd.GetPath()

            if pathname.startswith(self.settings['base_data_dir']):
                self.data_dir.SetValue(pathname)
            else:
                msg = ('Directory must be the following directory or one of '
                    'its subdirectories: {}'.format(self.settings['base_data_dir']))
                wx.CallAfter(wx.MessageBox, msg, 'Invalid directory',
                    style=wx.OK|wx.ICON_ERROR)

        return

    def _on_data_dir_right_click(self, evt):
        wx.CallAfter(self._show_data_dir_menu)

    def _show_data_dir_menu(self):

        menu = wx.Menu()

        menu.Append(1, 'Change base directory')

        self.Bind(wx.EVT_MENU, self._on_popup_menu_choice)
        self.PopupMenu(menu)

        menu.Destroy()

    def _on_popup_menu_choice(self, evt):
        if evt.GetId() == 1:
            with wx.DirDialog(self, "Select New Base Directory",
                self.settings['base_data_dir']) as fd:

                if fd.ShowModal() == wx.ID_CANCEL:
                    return

                pathname = fd.GetPath()

                if os.path.exists(pathname):
                    self.settings['base_data_dir'] = pathname
                    self.data_dir.SetValue(pathname)


    def _on_change_exp_param(self, evt):
        if 'trsaxs_scan' in self.settings['components']:
            trsaxs_panel = wx.FindWindowByName('trsaxs_scan')
            trsaxs_panel.update_params()

    def _on_start_exp(self, evt):
        if evt.GetEventObject() == self.start_exp_btn:
            exp_only = True
        else:
            exp_only = False

        if not self._preparing_exposure:
            self._preparing_exposure = True
            self.start_exp(exp_only)

    def _on_stop_exp(self, evt):
        self.stop_exp()

    def start_exp(self, exp_only, exp_values=None, metadata_vals=None, verbose=True):
        self.abort_event.clear()
        self.exp_event.clear()
        self.timeout_event.clear()

        warnings_valid, shutter_msg, vac_msg = self.check_warnings(verbose)

        if not warnings_valid:
            self._preparing_exposure = False
            return

        if exp_values is None:
            exp_values, exp_valid = self.get_exp_values(verbose)
        else:
            exp_valid = True

        self.current_exposure_values = exp_values

        if not exp_valid:
            self._preparing_exposure = False
            return

        metadata, metadata_valid = self._get_metadata(metadata_vals, verbose)

        if metadata_valid:
            exp_values['metadata'] = metadata
        else:
            self._preparing_exposure = False
            return

        if self.pipeline_ctrl is not None:
            data_dir = os.path.join(self.current_exposure_values['data_dir'], 'images')
            self.current_exposure_values['data_dir'] = data_dir

        overwrite_valid = self._check_overwrite(exp_values, verbose)

        if not overwrite_valid:
            self._preparing_exposure = False
            return

        comp_valid, comp_settings = self._check_components(exp_only, verbose)

        if not comp_valid:
            self._preparing_exposure = False
            return

        # Do this twice as some settings get set in _check components and you
        # want the right metdata, but check components starts some things,
        # so you don't want to run that if the metadata is otherwise invalid
        metadata, metadata_valid = self._get_metadata(metadata_vals, False)
        exp_values['metadata'] = metadata

        cont = True

        if (('trsaxs_scan' in self.settings['components'] and exp_only) or
            ('scan' in self.settings['components'] and exp_only)):
            msg = ("Only exposures will be taken, no scan will be done. Are you sure you want to continue?")
            dlg = wx.MessageDialog(None, msg, "Shutter Closed", wx.YES_NO|wx.ICON_EXCLAMATION|wx.NO_DEFAULT)
            result = dlg.ShowModal()
            dlg.Destroy()

            if result == wx.ID_NO:
                cont = False

        if not cont:
            self._preparing_exposure = False
            return

        self._pipeline_start_exp()

        self.set_status('Preparing exposure')
        wx.CallAfter(self.start_exp_btn.Disable)
        wx.CallAfter(self.start_scan_btn.Disable)
        wx.CallAfter(self.stop_exp_btn.Enable)
        self.total_time = exp_values['num_frames']*exp_values['exp_period']

        if self.settings['tr_muscle_exp']:
            exp_values['exp_type'] = 'muscle'
        else:
            exp_values['exp_type'] = 'standard'

        if 'trsaxs_scan' in self.settings['components'] and not exp_only:
            self.total_time = comp_settings['trsaxs_scan']['total_time']+1*comp_settings['trsaxs_scan']['num_scans']
            self.exp_cmd_q.append(('start_tr_exp', (exp_values, comp_settings), {}))

        elif 'scan' in self.settings['components'] and not exp_only:
            self.total_time = self.total_time*comp_settings['scan']['total_steps'] + 4*comp_settings['scan']['total_outer_loop_steps']
            self.exp_cmd_q.append(('start_scan_exp', (exp_values, comp_settings), {}))

        else:
            #Exposure time fudge factors for the overhead and readout
            if exp_values['exp_period'] < exp_values['exp_time'] + self.settings['slow_mode_thres']:
                self.total_time = self.total_time+2

            self.exp_cmd_q.append(('start_exp', (), exp_values))

        self.set_time_remaining(self.total_time)

        if (('trsaxs_scan' in self.settings['components'] and not exp_only) or exp_values['wait_for_trig']
            or ('scan' in self.settings['components'] and not exp_only)):
            wx.CallAfter(self.exp_status_sizer.Show, self.scan_num_sizer, recursive=True)
            wx.CallAfter(self.scan_number.SetLabel, '1')
        else:
            wx.CallAfter(self.exp_status_sizer.Hide, self.scan_num_sizer, recursive=True)

        if 'trsaxs_flow' in self.settings['components'] and not exp_only:
            trsaxs_flow_panel = wx.FindWindowByName('trsaxs_flow')
            trsaxs_flow_panel.prepare_for_exposure(comp_settings['trsaxs_flow'])

        start_thread = threading.Thread(target=self.monitor_exp_status)
        start_thread.daemon = True
        start_thread.start()

        self._preparing_exposure = False
        return

    def stop_exp(self):
        self.abort_event.set()
        self.set_status('Aborting')

    def _on_exp_finish(self):
        self.tr_timer.Stop()

        self.start_exp_btn.Enable()
        self.start_scan_btn.Enable()
        self.stop_exp_btn.Disable()
        self.set_status('Ready')
        self.set_time_remaining(0)
        old_rn = self.run_num.GetLabel()
        run_num = int(old_rn[1:])+1
        self.run_number = '_{:03d}'.format(run_num)
        self.run_num.SetLabel(self.run_number)


        if 'coflow' in self.settings['components']:
            coflow_panel = wx.FindWindowByName('coflow')
            coflow_panel.auto_stop()

        if self.pipeline_ctrl is not None:
            # Note, in the future, for batch mode experiments they may not
            # be stopped, since there may be a buffer/sample yet to colelct
            # Will have to figure that out once I have that metadata available
            self.pipeline_ctrl.stop_current_experiment()

        if 'uv' in self.settings['components']:
            uv_panel = wx.FindWindowByName('uv')
            uv_panel.on_exposure_stop(self)

        if 'trsaxs_flow' in self.settings['components']:
            trsaxs_flow_panel = wx.FindWindowByName('trsaxs_flow')
            trsaxs_flow_panel.on_exposure_stop()

    def set_status(self, status):
        wx.CallAfter(self.status.SetLabel, status)
        self._exp_status = status

    def set_time_remaining(self, tr):
        if tr < 3600:
            tr_str = time.strftime('%M:%S', time.gmtime(tr))
        elif tr < 86400:
            tr_str = time.strftime('%H:%M:%S', time.gmtime(tr))
        else:
            tr_str = time.strftime('%d:%H:%M:%S', time.gmtime(tr))

        self._time_remaining = tr
        wx.CallAfter(self.time_remaining.SetLabel, tr_str)

    def set_scan_number(self, val):
        self.scan_number.SetLabel(str(val))

    def _on_tr_timer(self, evt):
        if self.exp_event.is_set():
            tr = self.total_time - (time.time() - self.initial_time)

            if tr < 0:
                tr = 0

            self.set_time_remaining(tr)

    def monitor_exp_status(self):
        while not self.exp_event.is_set() and not self.abort_event.is_set():
            time.sleep(0.001)
            self._check_exp_status()

        if self.exp_event.is_set():
            self.initial_time = time.time()
            wx.CallAfter(self.tr_timer.Start, 1000)
            wx.CallAfter(self.set_status, 'Exposing')

            while self.exp_event.is_set():
                status, val = self._check_exp_status()
                if status is not None and status == 'scan':
                    if val is not None and int(val) > 1:

                        if self.pipeline_ctrl is not None:
                            self.pipeline_ctrl.stop_current_experiment()

                        self._pipeline_start_exp(int(val))

                        if 'uv' in self.settings['components']:
                            uv_panel = wx.FindWindowByName('uv')
                            uv_values, uv_valid = uv_panel.on_exposure_start(self, int(val))

                time.sleep(0.01)

        wx.CallAfter(self._on_exp_finish)

        return

    def _check_exp_status(self):
        status = None
        val = None

        if len(self.exp_ret_q) > 0:
            status, val = self.exp_ret_q.popleft()

            if status == 'scan':
                wx.CallAfter(self.set_scan_number, val)
            elif status == 'timeout':
                wx.CallAfter(self._show_timeout_dialog, val)
            elif status == 'waiting':
                wx.CallAfter(self.set_status, 'Waiting for Trigger')
            elif status == 'exposing':
                wx.CallAfter(self.set_status, 'Exposing')

        return status, val

    def _pipeline_start_exp(self, scan_num=1):
        if self.pipeline_ctrl is not None:
            exp_type = None

            if 'Experiment type:' in self.current_metadata:
                md_exp_type =  self.current_metadata['Experiment type:']

                if md_exp_type == 'Batch mode SAXS':
                    if ('Needs Separate Buffer Measurement:' in self.current_metadata
                        and not self.current_metadata['Needs Separate Buffer Measurement:']):
                        # batch mode experiments where the running buffer is
                        # good for subtraction can be treated like SEC experiments
                        # in the pipeline
                        exp_type = 'SEC'

                    else:
                        exp_type = 'Batch'

                elif (md_exp_type == 'SEC-SAXS' or md_exp_type == 'SEC-MALS-SAXS'
                    or md_exp_type == 'AF4-MALS-SAXS'):
                    exp_type = 'SEC'

                elif md_exp_type == 'TR-SAXS':
                    exp_type = 'TR'

                else:
                    exp_type = 'Other'

                if exp_type is not None:
                    data_dir = self.current_exposure_values['data_dir']

                    local_data_dir = data_dir.replace(self.settings['remote_dir_root'],
                        self.settings['local_dir_root'], 1)

                    fprefix = self.current_exposure_values['fprefix']
                    num_frames = self.current_exposure_values['num_frames']

                    if exp_type == 'TR':
                        logger.info(self.current_metadata)
                        num_scans = self.current_metadata['Number of scans:']
                        # num_frames *= num_scans

                    else:
                        if self.current_metadata['Wait for trigger:']:
                            num_scans = self.current_metadata['Number of triggers:']
                        else:
                            num_scans = 1

                    if not os.path.exists(local_data_dir):
                        os.mkdir(local_data_dir)

        if self.pipeline_ctrl is not None and exp_type is not None:

            # Note, in the future this should get parameters for batch
            # mode experiments out of the autosampler metadata, where you
            # define number of expeirments, and related sample and buffer
            # experiments and file prefixes. Right now, the only processing
            # the pipeline will do for batch mode is radial averaging, since
            # it doesn't know the associated sample and buffer files
            if exp_type == 'TR':
                scan_fprefix = '{}_{:04}'.format(fprefix, scan_num)
                self.pipeline_ctrl.start_experiment(scan_fprefix, exp_type,
                    local_data_dir, scan_fprefix, num_frames)

            elif num_scans > 1:
                scan_fprefix = '{}_{:04}'.format(fprefix, scan_num)
                self.pipeline_ctrl.start_experiment(scan_fprefix, exp_type,
                    local_data_dir, scan_fprefix, num_frames)
            else:
                self.pipeline_ctrl.start_experiment(fprefix, exp_type, local_data_dir,
                    fprefix, num_frames)

    def _show_warning_dialog(self, msg):
        if self.warning_dialog is None:
            self.warning_dialog = utils.WarningMessage(self, msg, 'WARNING',
                self._on_close_warning_dialog)
            self.warning_dialog.Show()

    def _show_timeout_dialog(self, data):
        old_dir = data[0]
        new_dir = data[1]
        msg = ('BioCon is unable to find the specified data directory: {} . '
            'Any further experimental data, besides images, will be written '
            'in the following directory: {} . Contact your beamline '
            'scientist.'.format(old_dir, new_dir))

        if self.timeout_dialog is None:
            self.timeout_dialog = utils.WarningMessage(self, msg, 'WARNING',
                self._on_close_timeout_dialog)
            self.timeout_dialog.Show()

    def _on_close_warning_dialog(self):
        self.warning_dialog = None

    def _on_close_timeout_dialog(self):
        self.timeout_dialog = None

    def check_warnings(self, verbose=True, check_all=False):
        shutter_valid, shutter_msg = self._check_shutters(verbose)

        if check_all or shutter_valid:
            vac_valid, vac_msg = self._check_vacuum(verbose)
        else:
            vac_valid = True
            vac_msg = ''

        valid = shutter_valid and vac_valid

        return valid, shutter_msg, vac_msg

    def _check_shutters(self, verbose=True):

        cont = True
        msg = ''

        if self.settings['warnings']['shutter']:
            if self.fe_shutter_pv is not None:
                fes_val = self.fe_shutter_pv.get(timeout=2)

                if fes_val is not None:
                    if fes_val == 0:
                        fes = False
                    else:
                        fes = True
            else:
                fes = True

            if self.d_shutter_pv is not None:
                ds_val = self.d_shutter_pv.get(timeout=2)

                if ds_val is not None:
                    if ds_val == 0:
                        ds = False
                    else:
                        ds = True
            else:
                ds = True

            if not fes and not ds:
                msg = ('Both the Front End shutter and the D Hutch '
                    'shutter are closed.')

            elif not fes:
                msg = ('The Front End shutter is closed.')

            elif not ds:
                msg = ('The D Hutch shutter is closed.')

            if msg != '' and verbose:
                msg += ' Are you sure you want to continue?'
                dlg = wx.MessageDialog(None, msg, "Shutter Closed",
                    wx.YES_NO|wx.ICON_EXCLAMATION|wx.NO_DEFAULT)
                result = dlg.ShowModal()
                dlg.Destroy()

                if result == wx.ID_NO:
                    cont = False
                else:
                    if not fes and not ds:
                        logger.info('Front End shutter and D Hutch shutter are closed.')

                    elif not fes:
                        logger.info('Front End shutter is closed.')

                    elif not ds:
                        logger.info('D Hutch shutter is closed.')

        return cont, msg

    def _check_vacuum(self, verbose=True):
        cont = True
        msg = ''

        if self.settings['warnings']['col_vac']['check']:
            thresh = self.settings['warnings']['col_vac']['thresh']

            if self.col_vac_pv is not None:
                vac = self.col_vac_pv.get(timeout=2)
                if vac is None:
                    vac = 0
            else:
                vac = 0

            if  vac > thresh:
                msg = msg + ('\nCollimator vacuum (< {} mtorr): {} mtorr'.format(
                    int(round(thresh*1000)), int(round(vac*1000))))

        if self.settings['warnings']['guard_vac']['check']:
            thresh = self.settings['warnings']['guard_vac']['thresh']

            if self.guard_vac_pv is not None:
                vac = self.guard_vac_pv.get(timeout=2)
                if vac is None:
                    vac = 0
            else:
                vac = 0

            if  vac > thresh:
                msg = msg + ('\n- Guard slit vacuum (< {} mtorr): {} mtorr'.format(
                    int(round(thresh*1000)), int(round(vac*1000))))

        if self.settings['warnings']['sample_vac']['check']:
            thresh = self.settings['warnings']['sample_vac']['thresh']

            if self.sample_vac_pv is not None:
                vac = self.sample_vac_pv.get(timeout=2)
                if vac is None:
                    vac = 0
            else:
                vac = 0

            if  vac > thresh:
                msg = msg + ('\n- Sample vacuum (< {} mtorr): {} mtorr'.format(
                    int(round(thresh*1000)), int(round(vac*1000))))

        if self.settings['warnings']['sc_vac']['check']:
            thresh = self.settings['warnings']['sc_vac']['thresh']

            if self.sc_vac_pv is not None:
                vac = self.sc_vac_pv.get(timeout=2)
                if vac is None:
                    vac = 0
            else:
                vac = 0

            if  vac > thresh:
                msg = msg + ('\n- Flight tube vacuum (< {} mtorr): {} mtorr'.format(
                    int(round(thresh*1000)), int(round(vac*1000))))

        if msg != '' and verbose:
            msg = ('The following vacuum readings are too high, are you sure '
                'you want to continue?') + msg
            dlg = wx.MessageDialog(None, msg, "Possible Bad Vacuum",
                wx.YES_NO|wx.ICON_EXCLAMATION|wx.NO_DEFAULT)
            result = dlg.ShowModal()
            dlg.Destroy()

            if result == wx.ID_NO:
                cont = False

        return cont, msg

    def get_exp_values(self, verbose=True):
        num_frames = self.num_frames.GetValue()
        exp_time = self.exp_time.GetValue()
        exp_period = self.exp_period.GetValue()
        data_dir = self.data_dir.GetValue()
        filename = self.filename.GetValue()
        run_num = self.run_number
        wait_for_trig = self.wait_for_trig.GetValue()
        num_trig = self.num_trig.GetValue()
        shutter_speed_open = self.settings['shutter_speed_open']
        shutter_speed_close = self.settings['shutter_speed_close']
        shutter_cycle = self.settings['shutter_cycle']
        shutter_pad = self.settings['shutter_pad']
        struck_log_vals = self.settings['struck_log_vals']
        joerger_log_vals = self.settings['joerger_log_vals']
        struck_measurement_time = float(self.muscle_sampling.GetValue())

        (num_frames, exp_time, exp_period, data_dir, filename,
            wait_for_trig, num_trig, local_data_dir, struck_num_meas, valid,
            errors) = self._validate_exp_values(
            num_frames, exp_time, exp_period, data_dir, filename,
            wait_for_trig, num_trig, struck_measurement_time, verbose=verbose)

        exp_values = {
            'num_frames'                : num_frames,
            'exp_time'                  : exp_time,
            'exp_period'                : exp_period,
            'data_dir'                  : data_dir,
            'local_data_dir'            : local_data_dir,
            'fprefix'                   : filename+run_num,
            'wait_for_trig'             : wait_for_trig,
            'num_trig'                  : num_trig,
            'shutter_speed_open'        : shutter_speed_open,
            'shutter_speed_close'       : shutter_speed_close,
            'shutter_cycle'             : shutter_cycle,
            'shutter_pad'               : shutter_pad,
            'joerger_log_vals'          : joerger_log_vals,
            'struck_log_vals'           : struck_log_vals,
            'struck_measurement_time'   : struck_measurement_time,
            'struck_num_meas'           : struck_num_meas,
            }

        return exp_values, valid

    def _validate_exp_values(self, num_frames, exp_time, exp_period, data_dir,
        filename, wait_for_trig, num_trig, struck_measurement_time, verbose=True,
        automator=False):

        errors = []

        try:
            num_frames = int(num_frames)
        except Exception:
            errors.append('Number of frames (between 1 and {})'.format(
                self.settings['nframes_max']))

        try:
            exp_time = float(exp_time)
        except Exception:
            errors.append('Exposure time (between {} and {} s)'.format(
                self.settings['exp_time_min'], self.settings['exp_time_max']))

        try:
            exp_period = float(exp_period)
        except Exception:
            errors.append(('Exposure period (between {} and {} s, and at '
                'least {} s greater than the exposure time)'.format(
                self.settings['exp_period_min'], self.settings['exp_period_max'],
                self.settings['exp_period_delta'])))

        if wait_for_trig:
            try:
                num_trig = int(num_trig)
            except Exception:
                errors.append(('Number of triggers (greater than 0)'))

        if self.settings['tr_muscle_exp']:
            try:
                struck_measurement_time = float(struck_measurement_time)
            except Exception:
                errors.append(('Parameter sampling time (greater than 0)'))

        if isinstance(num_frames, int):
            if num_frames < 1 or num_frames > self.settings['nframes_max']:
                errors.append('Number of frames (between 1 and {}'.format(
                    self.settings['nframes_max']))

        if isinstance(exp_time, float):
            if (exp_time < self.settings['exp_time_min']
                or exp_time > self.settings['exp_time_max']):
                errors.append('Exposure time (between {} and {} s)'.format(
                self.settings['exp_time_min'], self.settings['exp_time_max']))

        if isinstance(exp_period, float):
            if (exp_period < self.settings['exp_period_min']
                or exp_period > self.settings['exp_period_max']):

                errors.append(('Exposure period (between {} and {} s, and at '
                    'least {} s greater than the exposure time)'.format(
                    self.settings['exp_period_min'], self.settings['exp_period_max'],
                    self.settings['exp_period_delta'])))

            elif (isinstance(exp_time, float) and exp_period < exp_time
                + self.settings['exp_period_delta']):

                errors.append(('Exposure period (between {} and {} s, and at '
                    'least {} s greater than the exposure time)'.format(
                    self.settings['exp_period_min'], self.settings['exp_period_max'],
                    self.settings['exp_period_delta'])))

        if isinstance(exp_period, float) and isinstance(exp_time, float):
            if exp_time > 2000 and exp_period < exp_time + self.settings['slow_mode_thres']:
                errors.append(('Exposure times greater than {} s must have '
                    'an exposure period at least {} s longer than the '
                    'exposure time.'.format(self.settings['fast_mode_max_exp_time'],
                    self.settings['slow_mode_thres'])))

        if (isinstance(exp_period, float) and isinstance(num_frames, int) and
            isinstance(struck_measurement_time, float) and self.settings['tr_muscle_exp']):
            if exp_period*num_frames/struck_measurement_time > self.settings['nparams_max']:
                errors.append(('Total experiment time (exposure period * number '
                    'of frames) divided by parameter sampling time must be '
                    'less than {}.'.format(self.settings['nparams_max'])))

            if struck_measurement_time >= exp_period*num_frames:
                errors.append(('Total experiment time (exposure period * number '
                    'of frames) must be longer than parameter sampling time.'))

        if filename == '':
            errors.append('Filename (must not be blank)')

        if (data_dir == '' or not os.path.exists(data_dir)) and not automator:
            errors.append('Data directory (must exist, and not be blank)')

        if wait_for_trig:
            if isinstance(num_trig, int):
                if num_trig < 1:
                    errors.append(('Number of triggers (greater than 0)'))

        if len(errors) > 0 and verbose:
            msg = 'The following field(s) have invalid values:'
            for err in errors:
                msg = msg + '\n- ' + err
            msg = msg + ('\n\nPlease correct these errors, then start the exposure.')

            wx.CallAfter(wx.MessageBox, msg, 'Error in exposure parameters',
                style=wx.OK|wx.ICON_ERROR)

            valid = False

            local_data_dir = ''
            data_dir = ''
            struck_num_meas = 0

        else:
            local_data_dir = copy.copy(data_dir)
            data_dir = data_dir.replace(self.settings['local_dir_root'],
                self.settings['remote_dir_root'], 1)

            if self.settings['tr_muscle_exp']:
                struck_num_meas = exp_period*num_frames/struck_measurement_time
                struck_num_meas = int(struck_num_meas+0.5)
            else:
                struck_num_meas = 0



            valid = True

        return (num_frames, exp_time, exp_period, data_dir, filename,
            wait_for_trig, num_trig, local_data_dir, struck_num_meas, valid,
            errors)

    def _check_components(self, exp_only, verbose=True):
        comp_settings = {}
        errors = []

        if 'coflow' in self.settings['components']:
            coflow_panel = wx.FindWindowByName('coflow')
            coflow_started = coflow_panel.auto_start()
        else:
            coflow_started = True

        if 'trsaxs_scan' in self.settings['components'] and not exp_only:
            trsaxs_panel = wx.FindWindowByName('trsaxs_scan')
            trsaxs_values, trsaxs_scan_valid = trsaxs_panel.get_scan_values()
            if trsaxs_scan_valid:
                trsaxs_panel.run_and_wait_for_centering()
                trsaxs_values, trsaxs_scan_valid = trsaxs_panel.get_scan_values()
            comp_settings['trsaxs_scan'] = trsaxs_values
        else:
            trsaxs_scan_valid = True

        if 'trsaxs_flow' in self.settings['components'] and not exp_only:
            trsaxs_panel = wx.FindWindowByName('trsaxs_flow')
            trsaxs_values, trsaxs_flow_valid = trsaxs_panel.get_flow_values()
            comp_settings['trsaxs_flow'] = trsaxs_values
        else:
            trsaxs_flow_valid = True

        if 'scan' in self.settings['components'] and not exp_only:
            scan_panel = wx.FindWindowByName('scan')
            scan_values, scan_valid = scan_panel.get_scan_values()
            comp_settings['scan'] = scan_values
        else:
            scan_valid = True

        if 'uv' in self.settings['components']:
            uv_panel = wx.FindWindowByName('uv')
            uv_values, uv_valid = uv_panel.on_exposure_start(self)
            if uv_values is not None:
                comp_settings['uv'] = uv_values

        else:
            uv_valid = True

        if not coflow_started:
            msg = ('Coflow failed to start, so exposure has been canceled. '
                'Please correct the errors then start the exposure again.')

            wx.CallAfter(wx.MessageBox, msg, 'Error starting coflow',
                style=wx.OK|wx.ICON_ERROR)

        if ('trsaxs_scan' in self.settings['components'] and 'trsaxs_flow' in self.settings['components']
            and trsaxs_scan_valid and trsaxs_flow_valid and not exp_only):
            autoinject = comp_settings['trsaxs_flow']['autoinject']
            autoinject_scan = comp_settings['trsaxs_flow']['autoinject_scan']
            total_scans = comp_settings['trsaxs_scan']['num_scans']

            if autoinject == 'after_scan' and  autoinject_scan >= total_scans:
                errors.append(('Autoinjection scan must be less than the total '
                    'number of scans.'))

        if not uv_valid:
            if self.current_exposure_values['exp_time'] < 0.125:
                errors.append('Exposure time with UV data collection must be >= 0.125 s')

            if (self.current_exposure_values['exp_period']
                - self.current_exposure_values['exp_time'] < 0.01):
                errors.append(('Exposure period must be at least 0.01 s longer '
                    'than exposure time with UV data collection'))

        if len(errors) > 0 and verbose:
            msg = 'The following field(s) have invalid values:'
            for err in errors:
                msg = msg + '\n- ' + err
            msg = msg + ('\n\nPlease correct these errors, then start the exposure.')

            wx.CallAfter(wx.MessageBox, msg, 'Error in component parameters',
                style=wx.OK|wx.ICON_ERROR)

            comp_settings = {}
            valid = False
        elif not verbose:
            valid = True
        else:
            valid = (coflow_started and trsaxs_scan_valid and trsaxs_flow_valid
                and scan_valid and uv_valid)

        return valid, comp_settings

    def _check_overwrite(self, exp_settings, verbose=True):
        data_dir = exp_settings['data_dir']
        fprefix = exp_settings['fprefix']
        num_frames = exp_settings['num_frames']

        data_dir = data_dir.replace(self.settings['remote_dir_root'], self.settings['local_dir_root'], 1)

        cont = self.inner_check_overwrite(data_dir, fprefix, num_frames)

        if not cont and verbose:
            msg = ("Warning: data collection will overwrite existing files "
                "with the same name. Do you want to proceed?")
            dlg = wx.MessageDialog(None, msg, "Confirm data overwrite",
                wx.YES_NO|wx.ICON_EXCLAMATION|wx.NO_DEFAULT)
            result = dlg.ShowModal()
            dlg.Destroy()

            if result == wx.ID_YES:
                cont = True
        else:
            cont = True

        return cont

    def inner_check_overwrite(self, data_dir, fprefix, num_frames):
        log_file = os.path.join(data_dir, '{}.log'.format(fprefix))

        img_check_step = int(num_frames/10)
        if img_check_step == 0:
            img_check_step = 1

        img_nums = range(1, num_frames+1, img_check_step)
        img_files = [os.path.join(data_dir, '{}_{:04d}.tif'.format(fprefix, img_num))
            for img_num in img_nums]

        check_files = [log_file]+img_files

        cont = True

        for f in check_files:
            if os.path.exists(f):
                cont = False
                break

        return cont


    def _get_metadata(self, metadata_vals=None, verbose=True):

        metadata = self.metadata()

        column = None
        flow_rate = None
        errors = []

        if 'coflow' in self.settings['components']:
            coflow_panel = wx.FindWindowByName('coflow')
            coflow_metadata = coflow_panel.metadata()

            for key, value in coflow_metadata.items():
                metadata[key] = value

                if key.startswith('LC flow rate'):
                    try:
                        flow_rate = float(value)
                    except TypeError:
                        flow_rate = 0

        if 'trsaxs_scan' in self.settings['components']:
            trsaxs_panel = wx.FindWindowByName('trsaxs_scan')
            trsaxs_metadata = trsaxs_panel.metadata()

            for key, value in trsaxs_metadata.items():
                metadata[key] = value

        if 'trsaxs_flow' in self.settings['components']:
            trsaxs_panel = wx.FindWindowByName('trsaxs_flow')
            trsaxs_metadata = trsaxs_panel.metadata()

            for key, value in trsaxs_metadata.items():
                metadata[key] = value

        if 'scan' in self.settings['components']:
            scan_panel = wx.FindWindowByName('scan')
            scan_metadata = scan_panel.metadata()

            for key, value in scan_metadata.items():
                metadata[key] = value

        if 'metadata' in self.settings['components'] and metadata_vals is None:
            params_panel = wx.FindWindowByName('metadata')
            params_metadata = params_panel.metadata()

            for key, value in params_metadata.items():
                metadata[key] = value

                if key == 'Column:':
                    column = value

        elif metadata_vals is not None:
            for key, value in metadata_vals.items():
                metadata[key] = value

                if key == 'Column:':
                    column = value

        if 'uv' in self.settings['components']:
            uv_panel = wx.FindWindowByName('uv')
            uv_metadata = uv_panel.metadata()

            for key, value in uv_metadata.items():
                metadata[key] = value

        if ('coflow' in self.settings['components']
            and 'metadata' in self.settings['components']):
            if metadata['Coflow on:']:
                if column is not None and flow_rate is not None:
                    if '10/300' in column:
                        flow_range = (0.4, 0.8)
                    elif '5/150' in column:
                        flow_range = (0.2, 0.5)
                    elif 'Wyatt' in column:
                        flow_range = (0.4, 0.8)
                    else:
                        flow_range = None

                    if flow_range is not None:
                        if flow_rate < flow_range[0] or flow_rate > flow_range[1]:
                            msg = ('Flow rate of {} is not in the usual '
                                'range of {} to {} for column {}'.format(flow_rate,
                                flow_range[0], flow_range[1], column))

                            errors.append(msg)

                if int(metadata['Sheath valve position:']) != 1:
                    msg = ('Sheath valve is in position {}, not the usual '
                        'position 1.'.format(metadata['Sheath valve position:']))

                    errors.append(msg)

            else:
                msg = ('Coflow is not on')
                errors.append(msg)

        if len(errors) == 0 or not verbose:
            metadata_valid = True

        else:
            msg = ('Your settings may be inconsistent:\n')
            msg = msg + '\n-'.join(errors)
            msg = msg + '\n\nDo you wish to start the exposure?'

            dlg = wx.MessageDialog(self, msg, "Confirm exposure start",
                wx.YES_NO|wx.ICON_QUESTION|wx.NO_DEFAULT)
            result = dlg.ShowModal()
            dlg.Destroy()

            if result == wx.ID_YES:
                metadata_valid = True
            else:
                metadata_valid = False

        if metadata_valid:
            self.current_metadata = metadata

        return metadata, metadata_valid

    def metadata(self):
        metadata = OrderedDict()

        if len(self.current_exposure_values)>0:
            metadata['Instrument:'] = 'BioCAT (Sector 18, APS)'
            metadata['Nominal start date:'] = datetime.datetime.now().isoformat(str(' '))
            metadata['File prefix:'] = self.current_exposure_values['fprefix']
            metadata['Save directory:'] = self.current_exposure_values['data_dir']
            metadata['Number of frames:'] = self.current_exposure_values['num_frames']
            metadata['Exposure time/frame [s]:'] = self.current_exposure_values['exp_time']
            metadata['Exposure period/frame [s]:'] = self.current_exposure_values['exp_period']
            metadata['Wait for trigger:'] = self.current_exposure_values['wait_for_trig']

            if self.current_exposure_values['wait_for_trig']:
                metadata['Number of triggers:'] = self.current_exposure_values['num_trig']

            if 'eig' in self.settings['detector'].lower():
                metadata['Number of images per file:'] = self.settings['det_args']['images_per_file']

            if self.beam_current_pv is not None:
                bc_val = self.beam_current_pv.get(timeout=2)

                if bc_val is not None:
                    try:
                        bc_val = round(bc_val, 2)
                    except Exception:
                        pass
                    metadata['Starting storage ring current [mA]:'] = bc_val

            if self.fe_shutter_pv is not None:
                fes_val = self.fe_shutter_pv.get(timeout=2)

                if fes_val is not None:
                    if fes_val == 0:
                        fes = False
                    else:
                        fes = True

                    metadata['Front end shutter open:'] = fes

            if self.d_shutter_pv is not None:
                ds_val = self.d_shutter_pv.get(timeout=2)

                if ds_val is not None:
                    if ds_val == 0:
                        ds = False
                    else:
                        ds = True

                    metadata['D hutch shutter open:'] = ds

            if self.col_vac_pv is not None:
                vac = self.col_vac_pv.get(timeout=2)

                if vac is not None:
                    vac = round(vac*1000, 1)

                    metadata['Collimator vacuum [mtorr]:'] = vac

            if self.guard_vac_pv is not None:
                vac = self.guard_vac_pv.get(timeout=2)

                if vac is not None:
                    vac = round(vac*1000, 1)

                    metadata['Guard slit vacuum [mtorr]:'] = vac

            if self.sample_vac_pv is not None:
                vac = self.sample_vac_pv.get(timeout=2)

                if vac is not None:
                    vac = round(vac*1000, 1)

                    metadata['Sample vacuum [mtorr]:'] = vac

            if self.sc_vac_pv is not None:
                vac = self.sc_vac_pv.get(timeout=2)

                if vac is not None:
                    vac = round(vac*1000, 1)

                    metadata['Flight tube vacuum [mtorr]:'] = vac

            if self.sc_vac_pv is not None:
                vac = self.sc_vac_pv.get(timeout=2)

                if vac is not None:
                    vac = round(vac*1000, 1)

                    metadata['Flight tube vacuum [mtorr]:'] = vac

            if self.a_hutch_T_pv is not None:
                env = self.a_hutch_T_pv.get(timeout=2)

                if env is not None:
                    metadata['A hutch temperature [C]:'] = env

            if self.a_hutch_H_pv is not None:
                env = self.a_hutch_H_pv.get(timeout=2)

                if env is not None:
                    metadata['A hutch humidity [%]:'] = env

            if self.c_hutch_T_pv is not None:
                env = self.c_hutch_T_pv.get(timeout=2)

                if env is not None:
                    metadata['C hutch temperature [C]:'] = env

            if self.c_hutch_H_pv is not None:
                env = self.c_hutch_H_pv.get(timeout=2)

                if env is not None:
                    metadata['C hutch humidity [%]:'] = env

            if self.d_hutch_T_pv is not None:
                env = self.d_hutch_T_pv.get(timeout=2)

                if env is not None:
                    metadata['D hutch temperature [C]:'] = env

            if self.d_hutch_H_pv is not None:
                env = self.d_hutch_H_pv.get(timeout=2)

                if env is not None:
                    metadata['D hutch humidity [%]:'] = env


        return metadata

    def exp_settings_decimal(self):
        exp_settings = {}

        try:
            exp_settings['num_frames'] = int(self.num_frames.GetValue())
        except ValueError:
            pass

        try:
            exp_settings['exp_time'] = D(self.exp_time.GetValue())
        except (ValueError, decimal.InvalidOperation):
            pass

        try:
            exp_settings['exp_period'] = D(self.exp_period.GetValue())
        except (ValueError, decimal.InvalidOperation):
            pass

        try:
            exp_settings['num_trig'] = D(self.num_trig.GetValue())
        except (ValueError, decimal.InvalidOperation):
            pass

        exp_settings['data_dir'] = self.data_dir.GetValue()
        exp_settings['filename'] = self.filename.GetValue()
        exp_settings['run_num'] = self.run_number
        exp_settings['wait_for_trig'] = self.wait_for_trig.GetValue()

        return exp_settings

    def set_exp_settings(self, exp_settings):
        if 'num_frames' in exp_settings:
            self.num_frames.ChangeValue(str(exp_settings['num_frames']))
        if 'exp_time' in exp_settings:
            self.exp_time.ChangeValue(str(exp_settings['exp_time']))
        if 'exp_period' in exp_settings:
            self.exp_period.ChangeValue(str(exp_settings['exp_period']))
        if 'num_trig' in exp_settings:
            self.num_trig.ChangeValue(str(exp_settings['num_trig']))
        if 'local_data_dir' in exp_settings:
            self.data_dir.ChangeValue(str(exp_settings['local_data_dir']))
        if 'filename' in exp_settings:
            self.filename.ChangeValue(str(exp_settings['filename']))
        if 'run_num' in exp_settings:
            self.run_num.ChangeValue(str(exp_settings['run_num']))
        if 'wait_for_trig' in exp_settings:
            self.wait_for_trig.SetValue(exp_settings['wait_for_trig'])

    def set_pipeline_ctrl(self, pipeline_ctrl):
        self.pipeline_ctrl = pipeline_ctrl
        self.pipeline_warning_shown = False
        self.pipeline_timer = wx.Timer(self)
        self.Bind(wx.EVT_TIMER, self._on_pipeline_timer, self.pipeline_timer)
        self.pipeline_timer.Start(1000)

    def _on_pipeline_timer(self, evt):

        if self.pipeline_ctrl.timeout_event.is_set():
            if not self.pipeline_warning_shown:
                msg = 'Warning: Lost connection to SAXS pipeline'
                self._show_warning_dialog(msg)
                self.pipeline_warning_shown = True

        else:
            self.pipeline_warning_shown = False

    def automator_callback(self, cmd_name, cmd_args, cmd_kwargs):
        success = True

        if cmd_name == 'status':
            if (self._exp_status == 'Aborting'
                or self._exp_status == 'Exposing'
                or self._exp_status == 'Waiting for Trigger'):
                state = 'exposing'

            elif self._exp_status == 'Preparing exposure':
                state = 'preparing'

            elif self._exp_status == 'Ready':
                state = 'idle'

            else:
                state = 'idle'

        elif cmd_name == 'abort':
            if (self._exp_status == 'Exposing'
                or self._exp_status == 'Waiting for Trigger'):
                self.stop_exp()

            state = 'idle'

        elif cmd_name == 'expose':
            num_frames = int(cmd_kwargs['num_frames'])
            exp_time = float(cmd_kwargs['exp_time'])
            exp_period = float(cmd_kwargs['exp_period'])
            data_dir = cmd_kwargs['data_dir']
            filename = cmd_kwargs['filename']
            run_num = self.run_number
            wait_for_trig = cmd_kwargs['wait_for_trig']
            num_trig = int(cmd_kwargs['num_trig'])
            shutter_speed_open = self.settings['shutter_speed_open']
            shutter_speed_close = self.settings['shutter_speed_close']
            shutter_cycle = self.settings['shutter_cycle']
            shutter_pad = self.settings['shutter_pad']
            struck_log_vals = self.settings['struck_log_vals']
            joerger_log_vals = self.settings['joerger_log_vals']
            struck_measurement_time = float(cmd_kwargs['struck_measurement_time'])

            if self.settings['tr_muscle_exp']:
                struck_num_meas = exp_period*num_frames/struck_measurement_time
                struck_num_meas = int(struck_num_meas+0.5)
            else:
                struck_num_meas = 0

            local_data_dir = copy.copy(data_dir)
            data_dir = data_dir.replace(self.settings['local_dir_root'],
                self.settings['remote_dir_root'], 1)

            exp_values = {
                'num_frames'                : num_frames,
                'exp_time'                  : exp_time,
                'exp_period'                : exp_period,
                'data_dir'                  : data_dir,
                'local_data_dir'            : local_data_dir,
                'fprefix'                   : filename+run_num,
                'wait_for_trig'             : wait_for_trig,
                'num_trig'                  : num_trig,
                'shutter_speed_open'        : shutter_speed_open,
                'shutter_speed_close'       : shutter_speed_close,
                'shutter_cycle'             : shutter_cycle,
                'shutter_pad'               : shutter_pad,
                'joerger_log_vals'          : joerger_log_vals,
                'struck_log_vals'           : struck_log_vals,
                'struck_measurement_time'   : struck_measurement_time,
                'struck_num_meas'           : struck_num_meas,
                'filename'                  : filename,
                }

            if cmd_kwargs['item_type'] == 'sec_sample':
                exp_type = 'SEC-SAXS'

            elif cmd_kwargs['item_type'] == 'exposure':
                exp_type = cmd_kwargs['exp_type']

            if (exp_type == 'SEC-SAXS' or exp_type == 'SEC-MALS-SAXS' or
                exp_type == 'IEC-SAXS'):
                column = cmd_kwargs['column']

            sample = cmd_kwargs['sample_name']
            buf = cmd_kwargs['buf']

            vol = cmd_kwargs['inj_vol']
            conc = cmd_kwargs['conc']
            notes = cmd_kwargs['notes']

            try:
                temperature = cmd_kwargs['temp']
            except Exception:
                temperature = None

            metadata = {
                'Experiment type:'      : exp_type,
                'Sample:'               : sample,
                'Buffer:'               : buf,
                'Loaded volume [uL]:'   : vol,
                'Concentration [mg/ml]:': conc,
                }

            if temperature is not None:
                metadata['Temperature [C]:'] = temperature

            if (exp_type == 'SEC-SAXS' or exp_type == 'SEC-MALS-SAXS' or
                exp_type == 'IEC-SAXS'):
                metadata['Column:'] = column

            metadata['Notes:'] = notes

            wx.CallAfter(self.set_exp_settings, exp_values)

            params_panel = wx.FindWindowByName('metadata')
            if params_panel is not None:
                if params_panel.saxs_panel.IsShown():
                    wx.CallAfter(params_panel.saxs_panel.set_metadata, metadata)
                else:
                    wx.CallAfter(params_panel.muscle_panel.set_metadata, metadata)

            else:
                metadata = None

            if not os.path.exists(exp_values['local_data_dir']):
                os.makedirs(exp_values['local_data_dir'])


            self.start_exp(True, exp_values, metadata, False)

            state = 'exposing'

        elif cmd_name == 'full_status':
            runtime = round(self._time_remaining/60,1)
            if self._exp_status == 'Ready':
                status = 'Idle'
            else:
                status = copy.copy(self._exp_status)

            state = {
                'status'    : status,
                'runtime'   : str(runtime),
            }

        return state, success


    def on_exit(self):
        if self.pipeline_timer is not None:
            self.pipeline_timer.Stop()

        if self.exp_event.is_set() and not self.abort_event.is_set():
            self.abort_event.set()
            time.sleep(2)

        try:
            self.exp_con.stop()
            self.exp_con.join(10)
            while self.exp_con.is_alive():
                time.sleep(0.001)
        except AttributeError:
            pass #For testing, when there is no exp_con

class ExpFrame(wx.Frame):
    """
    A lightweight frame allowing one to work with arbitrary number of pumps.
    Only meant to be used when the pumpcon module is run directly,
    rather than when it is imported into another program.
    """
    def __init__(self, settings, *args, **kwargs):
        """
        Initializes the pump frame. Takes args and kwargs for the wx.Frame class.
        """
        super(ExpFrame, self).__init__(*args, **kwargs)
        logger.debug('Setting up the ExpFrame')

        self.settings = settings

        self.Bind(wx.EVT_CLOSE, self._on_exit)

        top_sizer = self._create_layout()

        self.SetSizer(top_sizer)

        self.Fit()
        self.Raise()

    def _create_layout(self):
        """Creates the layout"""
        self.exp_panel = ExpPanel(self.settings, self)

        self.exp_sizer = wx.BoxSizer(wx.HORIZONTAL)
        self.exp_sizer.Add(self.exp_panel, proportion=1, flag=wx.EXPAND)

        top_sizer = wx.BoxSizer(wx.VERTICAL)
        top_sizer.Add(self.exp_sizer, flag=wx.EXPAND)

        return top_sizer

    def _on_exit(self, evt):
        """Stops all current pump motions and then closes the frame."""
        logger.debug('Closing the ExpFrame')
        self.exp_panel.on_exit()
        self.Destroy()



############################################################################
default_exposure_settings = {
    'data_dir'              : '',
    'filename'              : '',
    'run_num'               : 1,
    'exp_time'              : '0.5',
    'exp_period'            : '1',
    'exp_num'               : '2',

    # 'exp_time_min'          : 0.00105,  # For Pilatus3 X 1M
    # 'exp_time_max'          : 5184000,
    # 'exp_period_min'        : 0.002,
    # 'exp_period_max'        : 5184000,
    # 'nframes_max'           : 15000, # For Pilatus: 999999, for Struck: 15000 (set by maxChannels in the driver configuration)
    # 'nparams_max'           : 15000, # For muscle experiments with Struck, in case it needs to be set separately from nframes_max
    # 'exp_period_delta'      : 0.00095,
    # 'local_dir_root'        : '/nas_data/Pilatus1M',
    # 'remote_dir_root'       : '/nas_data',
    # 'detector'              : 'pilatus_mx',
    # 'det_args'              : {}, #Allows detector specific keyword arguments
    # 'add_file_postfix'      : True,

    'exp_time_min'          : 0.000000050, #Eiger2 XE 9M
    'exp_time_max'          : 3600,
    'exp_period_min'        : 0.001785714286, #There's an 8bit undocumented mode that can go faster, in theory
    'exp_period_max'        : 5184000, # Not clear there is a maximum, so left it at this
    'nframes_max'           : 15000, # For Eiger: 2000000000, for Struck: 15000 (set by maxChannels in the driver configuration)
    'nparams_max'           : 15000, # For muscle experiments with Struck, in case it needs to be set separately from nframes_max
    'exp_period_delta'      : 0.000000200,
    'local_dir_root'        : '/nas_data/Eiger2x',
    'remote_dir_root'       : '/nas_data/Eiger2x',
    'detector'              : '18ID:EIG2:_epics',
    'det_args'              :  {'use_tiff_writer': False, 'use_file_writer': True,
                                'photon_energy' : 12.0, 'images_per_file': 1000}, #1 image/file for TR, 300 for equilibrium
    'add_file_postfix'      : False,

    # 'shutter_speed_open'    : 0.004, #in s      NM vacuum shutter, broken
    # 'shutter_speed_close'   : 0.004, # in s
    # 'shutter_pad'           : 0.002, #padding for shutter related values
    # 'shutter_cycle'         : 0.02, #In 1/Hz, i.e. minimum time between shutter openings in a continuous duty cycle

    # 'shutter_speed_open'    : 0.001, #in s    Fast shutters
    # 'shutter_speed_close'   : 0.001, # in s
    # 'shutter_pad'           : 0.00, #padding for shutter related values
    # 'shutter_cycle'         : 0.002, #In 1/Hz, i.e. minimum time between shutter openings in a continuous duty cycle

    # 'shutter_speed_open'    : 0.075, #in s      Slow vacuum shutter
    # 'shutter_speed_close'   : 0.075, # in s
    # 'shutter_pad'           : 0.01, #padding for shutter related values
    # 'shutter_cycle'         : 0.2, #In 1/Hz, i.e. minimum time between shutter openings in a continuous duty cycle

    'shutter_speed_open'    : 0.0045, #in s      Normal vacuum shutter
    'shutter_speed_close'   : 0.004, # in s
    'shutter_pad'           : 0.002, #padding for shutter related values
    'shutter_cycle'         : 0.1, #In 1/Hz, i.e. minimum time between shutter openings in a continuous duty cycle

    'struck_measurement_time' : '0.001', #in s
    'tr_muscle_exp'         : False,
    'slow_mode_thres'       : 0.1,
    'fast_mode_max_exp_time': 2000,
    'wait_for_trig'         : True,
    'num_trig'              : '1',
    'show_advanced_options' : True,
    'beam_current_pv'       : 'XFD:srCurrent',
    'fe_shutter_pv'         : 'PA:18ID:STA_A_FES_OPEN_PL',
    'd_shutter_pv'          : 'PA:18ID:STA_D_SDS_OPEN_PL.VAL',
    'col_vac_pv'            : '18ID:VAC:D:Cols',
    'guard_vac_pv'          : '18ID:VAC:D:Guards',
    'sample_vac_pv'         : '18ID:VAC:D:Sample',
    'sc_vac_pv'             : '18ID:VAC:D:ScatterChamber',
    'a_hutch_T_pv'          : '18ID:EnvMon:A:TempC',
    'a_hutch_H_pv'          : '18ID:EnvMon:A:Humid',
    'c_hutch_T_pv'          : '18ID:EnvMon:C:TempC',
    'c_hutch_H_pv'          : '18ID:EnvMon:C:Humid',
    'd_hutch_T_pv'          : '18ID:EnvMon:D:TempC',
    'd_hutch_H_pv'          : '18ID:EnvMon:D:Humid',
    'use_old_i0_gain'       : True,
    'i0_gain_pv'            : '18ID_D_BPM_Gain:Level-SP',

    'struck_log_vals'       : [
        # Format: (mx_record_name, struck_channel, header_name,
        # scale, offset, use_dark_current, normalize_by_exp_time)
        {'mx_record': 'mcs3', 'channel': 2, 'name': 'I0',
        'scale': 1, 'offset': 0, 'dark': True, 'norm_time': False},
        {'mx_record': 'mcs4', 'channel': 3, 'name': 'I1', 'scale': 1,
        'offset': 0, 'dark': True, 'norm_time': False},
        # {'mx_record': 'mcs5', 'channel': 4, 'name': 'I2', 'scale': 1,
        # 'offset': 0, 'dark': True, 'norm_time': False},
        # {'mx_record': 'mcs6', 'channel': 5, 'name': 'I3', 'scale': 1,
        # 'offset': 0, 'dark': True, 'norm_time': False},
        # {'mx_record': 'mcs11', 'channel': 10, 'name': 'Beam_current',
        # 'scale': 5000, 'offset': 0.5, 'dark': False, 'norm_time': True},
        # {'mx_record': 'mcs12', 'channel': 11, 'name': 'Flow_rate',
        # 'scale': 10e6, 'offset': 0, 'dark': True, 'norm_time': True},
        # {'mx_record': 'mcs7', 'channel': 6, 'name': 'Detector_Enable',
        # 'scale': 1e5, 'offset': 0, 'dark': True, 'norm_time': True},
        # {'mx_record': 'mcs12', 'channel': 11, 'name': 'Length_Out',
        # 'scale': 10e6, 'offset': 0, 'dark': False, 'norm_time': True},
        # {'mx_record': 'mcs13', 'channel': 13, 'name': 'Length_In',
        # 'scale': 10e6, 'offset': 0, 'dark': False, 'norm_time': True},
        # {'mx_record': 'mcs13', 'channel': 12, 'name': 'Force',
        # 'scale': 10e6, 'offset': 0, 'dark': False, 'norm_time': True},
        ],
    'joerger_log_vals'      : [{'mx_record': 'j3', 'name': 'I0',
        'scale': 1, 'offset': 0, 'norm_time': False}, #Format: (mx_record_name, struck_channel, header_name, scale, offset, use_dark_current, normalize_by_exp_time)
        {'mx_record': 'j4', 'name': 'I1', 'scale': 1, 'offset': 0,
        'norm_time': False},
        # {'mx_record': 'j5', 'name': 'I2', 'scale': 1, 'offset': 0,
        # 'norm_time': False},
        # {'mx_record': 'j6', 'name': 'I3', 'scale': 1, 'offset': 0,
        # 'norm_time': False},
        # {'mx_record': 'j11', 'name': 'Beam_current', 'scale': 5000,
        # 'offset': 0.5, 'norm_time': True}
        ],
    'warnings'              : {'shutter' : True, 'col_vac' : {'check': True,
        'thresh': 0.04}, 'guard_vac' : {'check': True, 'thresh': 0.04},
        'sample_vac': {'check': True, 'thresh': 0.04}, 'sc_vac':
        {'check': True, 'thresh':0.04}},
    'base_data_dir'         : '/nas_data/Eiger2x/2025_Run1', #CHANGE ME and pipeline local_basedir
    }

default_exposure_settings['data_dir'] = default_exposure_settings['base_data_dir']


if __name__ == '__main__':
    logger = logging.getLogger()
    logger.setLevel(logging.DEBUG)

    h1 = logging.StreamHandler(sys.stdout)
    h1.setLevel(logging.INFO)
    # h1.setLevel(logging.DEBUG)
    formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s')
    h1.setFormatter(formatter)

    logger.addHandler(h1)

    settings = default_exposure_settings
    settings['components'] = ['exposure']

    app = wx.App()

    standard_paths = wx.StandardPaths.Get() #Can't do this until you start the wx app
    info_dir = standard_paths.GetUserLocalDataDir()

    if not os.path.exists(info_dir):
        os.mkdir(info_dir)

    h2 = handlers.RotatingFileHandler(os.path.join(info_dir, 'expcon.log'), maxBytes=10e6, backupCount=5, delay=True)
    h2.setLevel(logging.DEBUG)
    formatter2 = logging.Formatter('%(asctime)s - %(name)s - %(threadName)s - %(levelname)s - %(message)s')
    h2.setFormatter(formatter2)

    logger.addHandler(h2)

    logger.debug('Setting up wx app')
    frame = ExpFrame(settings, None, title='Exposure Control')
    frame.Show()
    app.MainLoop()

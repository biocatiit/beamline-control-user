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

import time
import logging
import sys
import os
import argparse

logger = logging.getLogger(__name__)

import wx
import numpy as np

import utils
utils.set_mppath() #This must be done before importing any Mp Modules.
import Mp as mp

logger = logging.getLogger(__name__)
logger.setLevel(logging.DEBUG)

h1 = logging.StreamHandler(sys.stdout)
h1.setLevel(logging.INFO)
formatter = logging.Formatter('%(asctime)s - %(message)s')
h1.setFormatter(formatter)

logger.addHandler(h1)

parser = argparse.ArgumentParser(description='2D scan and measurement. The scan moves to the motor2 start, then scans motor1. Then it steps motor2 to the next position, and scans motor1, etc.')
parser.add_argument('fprefix', help='The file prefix for the scan data.')
parser.add_argument('start1', type=float, help='The initial motor1 position (absolute, not relative).')
parser.add_argument('end1', type=float, help='The final motor1 position (absolute, not relative).')
parser.add_argument('step1', type=float, help='The motor1 step size to use in the scan.')
parser.add_argument('start2', type=float, help='The initial motor2 position (absolute, not relative).')
parser.add_argument('end2', type=float, help='The final motor2 position (absolute, not relative).')
parser.add_argument('step2', type=float, help='The motor2 step size to use in the scan.')
parser.add_argument('exp_time', type=float, help='The exposure time at each scan point.')
parser.add_argument('exp_period', type=float, help='The exposure period at each scan point.')
parser.add_argument('exp_num', type=int, default=1, nargs='?', help='The number of exposures at each scan point, optional (default: 1).')
parser.add_argument('--out', default='', nargs='?', help='The output sub-directory for the data.')


def fast_exposure(mx_data, data_dir, fprefix, num_frames, exp_time, exp_period,
    continuous_exp, **kwargs):
    logger.debug('Setting up fast exposure')
    det = mx_data['det']          #Detector

    struck = mx_data['struck']    #Struck SIS3820
    s0 = mx_data['struck_ctrs'][0]
    s1 = mx_data['struck_ctrs'][1]
    s2 = mx_data['struck_ctrs'][2]
    s3 = mx_data['struck_ctrs'][3]

    ab_burst = mx_data['ab_burst']   #Shutter control signal

    dio_out6 = mx_data['dio'][6]      #Xia/wharberton shutter N.C.
    dio_out9 = mx_data['dio'][9]      #Shutter control signal (alt.)
    dio_out10 = mx_data['dio'][10]    #SRS DG645 trigger
    dio_out11 = mx_data['dio'][11]    #Struck LNE/channel advance signal (alt.)

    det_datadir = mx_data['det_datadir']
    det_filename = mx_data['det_filename']

    try:
        det.abort()
    except mp.Device_Action_Failed_Error:
        pass
    try:
        det.abort()
    except mp.Device_Action_Failed_Error:
        pass
    struck.stop()
    ab_burst.stop()

    # print('after aborts/stops')
    det_filename.put('{}_0001.tif'.format(fprefix))

    dio_out6.write(0) #Open the slow normally closed xia shutter
    dio_out9.write(0) # Make sure the shutter is closed
    dio_out10.write(0) # Make sure the trigger is off
    dio_out11.write(0) # Make sure the LNE is off

    det.set_duration_mode(num_frames)
    det.arm()

    ab_burst.arm()
    struck.start()

    if continuous_exp:
        dio_out9.write(1)

    dio_out11.write(1)
    time.sleep(0.01)
    dio_out11.write(0)

    time.sleep(0.1)

    dio_out10.write(1)
    logger.info('Exposure started')
    time.sleep(0.01)
    dio_out10.write(0)

    while True:
        # busy = struck.is_busy()
        # print(busy)

        #Struck is_busy doesn't work in thread! So have to go elsewhere
        status = det.get_status()
        if ( ( status & 0x1 ) == 0 ):
            break

        if self._abort_event.is_set():
            logger.info("Aborting fast exposure")
            try:
                det.abort()
            except mp.Device_Action_Failed_Error:
                pass
            struck.stop()
            ab_burst.stop()
            dio_out9.write(0) #Close the fast shutter
            dio_out6.write(1) #Close the slow normally closed xia shutter
            try:
                det.abort()
            except mp.Device_Action_Failed_Error:
                pass
            break

        # if busy == 0:
        #     break

        time.sleep(0.01)

    if continuous_exp:
        dio_out9.write(0)

    dio_out6.write(1) #Close the slow normally closed xia shutter
    measurement = struck.read_all()

    dark_counts = [s0.get_dark_current(), s1.get_dark_current(),
        s2.get_dark_current(), s3.get_dark_current()]

    write_counters_struck(measurement, num_frames, 4, data_dir, fprefix,
        exp_period, dark_counts)
    logger.info('Exposure done')


def slow_exposure2(mx_data, data_dir, fprefix, num_frames, exp_time, exp_period, **kwargs):
    logger.debug('Setting up slow exposure 2')
    det = mx_data['det']     #Detector

    ab_burst = mx_data['ab_burst']   #Shutter control signal
    cd_burst = mx_data['cd_burst']
    ef_burst = mx_data['ef_burst']   #Pilatus trigger signal
    gh_burst = mx_data['gh_burst']
    dg645_trigger_source = mx_data['dg645_trigger_source']

    dio_out6 = mx_data['dio'][6]      #Xia/wharberton shutter N.C.
    dio_out10 = mx_data['dio'][10]    #SRS DG645 trigger

    joerger = mx_data['joerger']

    j2 = mx_data['joerger_ctrs'][0]
    j3 = mx_data['joerger_ctrs'][1]
    j4 = mx_data['joerger_ctrs'][2]
    j5 = mx_data['joerger_ctrs'][3]
    j6 = mx_data['joerger_ctrs'][4]
    # j7 = mx_data['joerger_ctrs'][6]

    scl_list = [j2, j3, j4, j5, j6]

    measurement = [[0 for i in range(num_frames)] for j in range(len(scl_list))]
    exp_start = [0 for i in range(num_frames)]

    det_datadir = mx_data['det_datadir']
    det_filename = mx_data['det_filename']

    try:
        det.abort()
    except mp.Device_Action_Failed_Error:
        pass
    joerger.stop()
    ab_burst.stop()

    det_datadir.put(data_dir)

    #Start writing counter file
    local_data_dir = data_dir.replace(settings['remote_dir_root'], settings['local_dir_root'], 1)
    header = '#Filename\tstart_time\texposure_time\tI0\tI1\tI2\tI3\n'
    log_file = os.path.join(local_data_dir, '{}.log'.format(fprefix))

    with open(log_file, 'w') as f:
        f.write(header)

    dio_out6.write(0) #Open the slow normally closed xia shutter
    dio_out10.write(0) # Make sure the trigger is off

    det.set_duration_mode(1)
    det.set_trigger_mode(2)

    joerger.start(1)
    joerger.stop()
    for scaler in scl_list:
        scaler.read()

    ab_burst.setup(exp_time+0.02, exp_time+0.01, 1, 0, 1, -1)
    cd_burst.setup(exp_time+0.02, exp_time+0.01, 1, 0, 1, -1)
    ef_burst.setup(exp_time+0.02, exp_time, 1, 0.005, 1, -1)
    gh_burst.setup(exp_time+0.02, exp_time, 1, 0, 1, -1)

    dg645_trigger_source.put(1)

    time.sleep(0.1)

    start = time.time()

    for i in range(num_frames):
        if self._abort_event.is_set():
            logger.debug('abort 1')
            slow_mode2_abort_cleanup(det, joerger, ab_burst, dio_out6,
                    measurement, num_frames, data_dir, fprefix, exp_start)
            return

        logger.info( "*** Starting exposure %d ***" % (i+1) )
        # logger.debug( "Time = %f" % (time.time() - start) )

        try:
            det.abort()
        except mp.Device_Action_Failed_Error:
            pass
        joerger.stop()
        ab_burst.stop()

        det_filename.put('{}_{:04d}.tif'.format(fprefix, i+1))
        det.arm()
        # logger.debug( "After det.arm() = %f" % (time.time() - start) )

        ab_burst.arm()

        if i == 0:
            logger.info('Exposure started')
            meas_start = time.time()
            i_meas = time.time()

        while time.time() - meas_start < i*exp_period:
            if self._abort_event.is_set():
                # logger.debug('abort 2')
                slow_mode2_abort_cleanup(det, joerger, ab_burst, dio_out6,
                    measurement, num_frames, data_dir, fprefix, exp_start)
                return

        joerger.start(exp_time+2)
        logger.debug("Measurement start time = %f" %(time.time() - meas_start))
        logger.debug("Delta Measurement start time = %f" %(time.time() - i_meas))
        exp_start[i] = time.time() - meas_start
        # logger.debug("M start time = %f" %(time.time() - start))
        i_meas = time.time()

        dio_out10.write( 1 )
        time.sleep(0.01)
        dio_out10.write( 0 )
        # logger.debug( "After dio_out10 signal = %f" % (time.time() - start) )

        additional_trig = False
        while True:
            status = det.get_status()

            if self._abort_event.is_set():
                # logger.debug('abort 3')
                slow_mode2_abort_cleanup(det, joerger, ab_burst, dio_out6,
                    measurement, num_frames, data_dir, fprefix, exp_start,
                    True, i, scl_list)
                return

            if ( ( status & 0x1 ) == 0 ):
                break

            if time.time()-i_meas > exp_period*1.5 and not additional_trig:
                #Sometimes maybe the dg misses a trigger? So send another.
                logger.error('DG645 did not receive trigger! Sending another!')
                dio_out10.write( 1 )
                time.sleep(0.01)
                dio_out10.write( 0 )

                additional_trig = True

            elif time.time()-i_meas > exp_period*3:
                logger.error('DG645 did not receive trigger! Aborting!')
                # logger.debug('abort 4')
                slow_mode2_abort_cleanup(det, joerger, ab_burst, dio_out6,
                    measurement, num_frames, data_dir, fprefix, exp_start,
                    True, i, scl_list)

                msg = ('Exposure {} failed to start properly. Exposure sequence '
                    'has been aborted. Please contact your beamline scientist '
                    'and restart the exposure sequence.'.format(i+1))

                wx.CallAfter(wx.MessageBox, msg, 'Exposure failed!',
                    style=wx.OK|wx.ICON_ERROR)

                return

            time.sleep(0.001)

        joerger.stop()
        # logger.debug( "After joerger.stop = %f" % \
        #       (time.time() - start ) )

        while True:
            busy = joerger.is_busy()

            if busy == 0:
                ctr_log = ''
                for j, scaler in enumerate(scl_list):
                    sval = scaler.read()
                    measurement[j][i] = sval
                    ctr_log = ctr_log + '{} '.format(sval)


                logger.info('Counter values: ' + ctr_log)

                with open(log_file, 'a') as f:
                    val = "{}_{:04d}.tif\t{}".format(fprefix, i+1, exp_start[i])
                    val = val + "\t{}".format(measurement[0][i]/10.e6)

                    for j in range(1, len(measurement)):
                            val = val + "\t{}".format(measurement[j][i])

                    val = val + '\n'
                    f.write(val)

                break

            time.sleep(0.001)

        # logger.debug('Joerger Done!\n')
        # logger.debug( "After Joerger readout = %f" % \
        #       (time.time() - start ) )

    dio_out6.write(1) #Close the slow normally closed xia shutter
    # self.write_counters_joerger(measurement, num_frames, data_dir, fprefix, exp_start)
    logger.info('Exposure done')

    return

def write_counters_struck(cvals, num_frames, num_counters, data_dir,
        fprefix, exp_period, dark_counts):
    data_dir = data_dir.replace(settings['remote_dir_root'], settings['local_dir_root'], 1)

    header = '#Filename\tstart_time\texposure_time\tI0\tI1\tI2\tI3\n'

    log_file = os.path.join(data_dir, '{}.log'.format(fprefix))

    with open(log_file, 'w') as f:
        f.write(header)
        for i in range(num_frames):
            val = "{}_{:04d}.tif\t{}".format(fprefix, i+1, exp_period*i)

            exp_time = cvals[0][i]/50.e6
            val = val + "\t{}".format(exp_time)

            for j in range(2, num_counters+2):
                val = val + "\t{}".format(cvals[j][i]-dark_counts[j-2]*exp_time)

            val = val + '\n'
            f.write(val)

def write_counters_joerger(cvals, num_frames, data_dir, fprefix, exp_start):
    data_dir = data_dir.replace(settings['remote_dir_root'], settings['local_dir_root'], 1)

    header = '#Filename\tstart_time\texposure_time\tI0\tI1\tI2\tI3\n'

    log_file = os.path.join(data_dir, '{}.log'.format(fprefix))
    with open(log_file, 'w') as f:
        f.write(header)
        for i in range(num_frames):
            val = "{}_{:04d}.tif\t{}".format(fprefix, i+1, exp_start[i])
            val = val + "\t{}".format(cvals[0][i]/10.e6)

            for j in range(1, len(cvals)):
                    val = val + "\t{}".format(cvals[j][i])

            val = val + '\n'
            f.write(val)

def slow_mode2_abort_cleanup(det, joerger, ab_burst, dio_out6,
    measurement, num_frames, data_dir, fprefix, exp_start, read_joerger=False,
    i=0, scl_list=[]):
    logger.info("Aborting slow exposure")
    try:
        det.abort()
    except mp.Device_Action_Failed_Error:
        pass
    joerger.stop()
    ab_burst.stop()
    dio_out6.write(1) #Close the slow normally closed xia shutter
    try:
        det.abort()
    except mp.Device_Action_Failed_Error:
        pass

    if read_joerger:
        while True:
            busy = joerger.is_busy()

            if busy == 0:
                for j, scaler in enumerate(scl_list):
                    sval = scaler.read()
                    measurement[j][i] = sval
                break

    write_counters_joerger(measurement, num_frames, data_dir, fprefix, exp_start)

    return


#User input parameters
args = parser.parse_args()
sub_dir = args.out
fprefix = args.fprefix
num_frames = args.exp_num
exp_time = args.exp_time
exp_period = args.exp_period
mtr1_start = args.start1
mtr1_end = args.end1
mtr1_step = args.step1
mtr2_start = args.start2
mtr2_end = args.end2
mtr2_step = args.step2


settings = {'data_dir': '/nas_data/Pilatus1M/20180917Lavender',
    'filename':'test',
    'run_num': 1,
    'exp_time_min': 0.00105,
    'exp_time_max': 5184000,
    'exp_period_min': 0.002,
    'exp_period_max': 5184000,
    'nframes_max': 999999,
    'exp_period_delta': 0.00095,
    'slow_mode_thres': 0.4,
    'fast_mode_max_exp_time' : 2000,
    'local_dir_root': '/nas_data/Pilatus1M',
    'remote_dir_root': '/nas_data',
    'base_data_dir': '/nas_data/Pilatus1M/20180917Lavender', #CHANGE ME
    }


settings['data_dir'] = settings['base_data_dir']

data_dir = os.path.join(settings['data_dir'], sub_dir)


#MX stuff
try:
    # First try to get the name from an environment variable.
    database_filename = os.environ["MXDATABASE"]
except:
    # If the environment variable does not exist, construct
    # the filename for the default MX database.
    mxdir = utils.get_mxdir()
    database_filename = os.path.join(mxdir, "etc", "mpilatus.dat")
    database_filename = os.path.normpath(database_filename)

mx_database = mp.setup_database(database_filename)
mx_database.set_plot_enable(2)


det = mx_database.get_record('pilatus')

server_record_name = det.get_field('server_record')
remote_det_name = det.get_field('remote_record_name')
server_record = mx_database.get_record(server_record_name)
det_datadir_name = '{}.datafile_directory'.format(remote_det_name)
det_datafile_name = '{}.datafile_pattern'.format(remote_det_name)

det_datadir = mp.Net(server_record, det_datadir_name)
det_filename = mp.Net(server_record, det_datafile_name)

ab_burst = mx_database.get_record('ab_burst')
ab_burst_server_record_name = ab_burst.get_field('server_record')
ab_burst_server_record = mx_database.get_record(ab_burst_server_record_name)
dg645_trigger_source = mp.Net(ab_burst_server_record, 'dg645.trigger_source')

mx_data = {'det': det,
    'det_datadir': det_datadir,
    'det_filename': det_filename,
    'struck': mx_database.get_record('sis3820'),
    'struck_ctrs': [mx_database.get_record('i{}'.format(i)) for i in range(4)],
    'ab_burst': mx_database.get_record('ab_burst'),
    'cd_burst': mx_database.get_record('cd_burst'),
    'ef_burst': mx_database.get_record('ef_burst'),
    'gh_burst': mx_database.get_record('gh_burst'),
    'dg645_trigger_source': dg645_trigger_source,
    'dio': [mx_database.get_record('avme944x_out{}'.format(i)) for i in range(16)],
    'joerger': mx_database.get_record('joerger_timer'),
    'joerger_ctrs':[mx_database.get_record('j{}'.format(i)) for i in range(2,7)],
    'mx_db': mx_database,
    'mtr1': mx_database.get_record('np7'),
    'mtr2': mx_database.get_record('np8'),
    }

if exp_period < exp_time + settings['slow_mode_thres']:
    logger.debug('Choosing fast exposure')
    exp_type = 'fast'
    # fast_exposure(data_dir, fprefix, num_frames, exp_time, exp_period, **kwargs)
else:
    logger.debug('Choosing slow exposure')
    exp_type = 'slow'
    # slow_exposure2(data_dir, fprefix, num_frames, exp_time, exp_period, **kwargs)

mtr1_positions = np.arange(mtr1_start, mtr1_end, mtr1_step)
mtr2_positions = np.arange(mtr2_start, mtr2_end, mtr2_step)

logger.info('Moving motors to start position')

mtr1 = mx_data['mtr1']
mtr2 = mx_data['mtr2']

mtr2.move_absolute(mtr2_positions[0])
mtr1.move_absolute(mtr1_positions[0])

logger.info('Setting up exposure')

det = mx_data['det']          #Detector

struck = mx_data['struck']    #Struck SIS3820
s0 = mx_data['struck_ctrs'][0]
s1 = mx_data['struck_ctrs'][1]
s2 = mx_data['struck_ctrs'][2]
s3 = mx_data['struck_ctrs'][3]

ab_burst = mx_data['ab_burst']   #Shutter control signal
cd_burst = mx_data['cd_burst']   #Struck LNE/channel advance signal
ef_burst = mx_data['ef_burst']   #Pilatus trigger signal
gh_burst = mx_data['gh_burst']
dg645_trigger_source = mx_data['dg645_trigger_source']

dio_out6 = mx_data['dio'][6]      #Xia/wharberton shutter N.C.
dio_out9 = mx_data['dio'][9]      #Shutter control signal (alt.)
dio_out10 = mx_data['dio'][10]    #SRS DG645 trigger
dio_out11 = mx_data['dio'][11]    #Struck LNE/channel advance signal (alt.)

det_datadir = mx_data['det_datadir']
det_filename = mx_data['det_filename']

joerger = mx_data['joerger']

j2 = mx_data['joerger_ctrs'][0]
j3 = mx_data['joerger_ctrs'][1]
j4 = mx_data['joerger_ctrs'][2]
j5 = mx_data['joerger_ctrs'][3]
j6 = mx_data['joerger_ctrs'][4]

try:
    det.abort()
except mp.Device_Action_Failed_Error:
    pass
try:
    det.abort()
except mp.Device_Action_Failed_Error:
    pass
struck.stop()
ab_burst.stop()
joerger.stop()

# print('after aborts/stops')

det_datadir.put(data_dir)

dio_out6.write(0) #Open the slow normally closed xia shutter
dio_out9.write(0) # Make sure the shutter is closed
dio_out10.write(0) # Make sure the trigger is off
dio_out11.write(0) # Make sure the LNE is off

det.set_trigger_mode( 2 )

dg645_trigger_source.put(1)

# logger.debug('Field value: ' + struck.get_field('trigger_mode'))
# struck.set_field('trigger_mode', '2') #Sets for external trigger
# logger.debug('Field value: ' + struck.get_field('trigger_mode'))

if exp_type == 'fast':
    struck.set_measurement_time(exp_time)   #Ignored for external LNE of Struck
    struck.set_num_measurements(num_frames)

    if exp_period > exp_time+0.01 and exp_period >= 0.02:
        #Shutter opens and closes, Takes 4 ms for open and close
        ab_burst.setup(exp_period, exp_time+0.01, num_frames, 0, 1, -1)
        cd_burst.setup(exp_period, 0.0001, num_frames, exp_time+0.006, 1, -1)
        ef_burst.setup(exp_period, exp_time, num_frames, 0.005, 1, -1)
        gh_burst.setup(exp_period, exp_time, num_frames, 0, 1, -1)
        continuous_exp = False
    else:
        #Shutter will be open continuously, via dio_out9
        ab_burst.setup(exp_period, exp_time, num_frames, 0, 1, -1) #Irrelevant
        cd_burst.setup(exp_period, 0.0001, num_frames, exp_time+0.00015, 1, -1)
        ef_burst.setup(exp_period, exp_time, num_frames, 0, 1, -1)
        gh_burst.setup(exp_period, exp_time, num_frames, 0, 1, -1)
        continuous_exp = True

    ab_burst.arm()

else:
    scl_list = [j2, j3, j4, j5, j6]

    joerger.start(1)
    joerger.stop()
    for scaler in scl_list:
        scaler.read()

    ab_burst.setup(exp_time+0.02, exp_time+0.01, 1, 0, 1, -1)
    cd_burst.setup(exp_time+0.02, exp_time+0.01, 1, 0, 1, -1)
    ef_burst.setup(exp_time+0.02, exp_time, 1, 0.005, 1, -1)
    gh_burst.setup(exp_time+0.02, exp_time, 1, 0, 1, -1)

for mtr2_pos in mtr2_positions:
    if mtr2_pos != mtr2_positions[0]:
        logger.info('Moving motor 2 position to {}'.format(mtr2_pos))
        mtr2.move_absolute(mtr2_pos)
    mtr2.wait_for_motor_stop()

    for mtr1_pos in mtr1_positions:
        if mtr1_pos != mtr1_positions[0]:
            logger.info('Moving motor 1 position to {}'.format(mtr1_pos))
            mtr1.move_absolute(mtr1_pos)
        mtr1.wait_for_motor_stop()

        step_prefix = '{}_m2={}_m1={}'.format(fprefix, mtr2_pos, mtr1_pos)

        if exp_type == 'fast':
            fast_exposure(mx_data, data_dir, fprefix, num_frames, exp_time,
                exp_period, continuous_exp)
        else:
            slow_exposure2(mx_data, data_dir, fprefix, num_frames, exp_time,
                exp_period)

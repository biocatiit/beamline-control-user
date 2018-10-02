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
import os

logger = logging.getLogger(__name__)

import wx

import utils
utils.set_mppath() #This must be done before importing any Mp Modules.

import Mp as mp
print(mp.__file__)

print_lock = threading.RLock()

class ExpCommThread(threading.Thread):
    """
    This class creates a control thread for flow meters attached to the system.
    This thread is designed for using a GUI application. For command line
    use, most people will find working directly with a flow meter object much
    more transparent. Below you'll find an example that initializes a
    :py:class:`BFS` and measures the flow. ::

        import collections
        import threading

        pump_cmd_q = collections.deque()
        abort_event = threading.Event()
        my_pumpcon = PumpCommThread(pump_cmd_q, abort_event)
        my_pumpcon.start()

        init_cmd = ('connect', ('COM6', 'pump2', 'VICI_M50'),
            {'flow_cal': 626.2, 'backlash_cal': 9.278})
        flow_rate_cmd = ('set_flow_rate', ('pump2', 2000), {})
        start_cmd = ('start_flow', ('pump2',), {})
        stop_cmd = ('stop', ('pump2',), {})

        pump_cmd_q.append(init_cmd)
        pump_cmd_q.append(start_cmd)
        pump_cmd_q.append(flow_rate_cmd)
        time.sleep(5)
        pump_cmd_q.append(stop_cmd)

        my_pumpcon.stop()
    """

    def __init__(self, command_queue, return_queue, abort_event, exp_event, mx_data,
        settings, name=None):
        """
        Initializes the custom thread. Important parameters here are the
        list of known commands ``_commands`` and known pumps ``known_fms``.

        :param collections.deque command_queue: The queue used to pass commands
            to the thread.

        :param collections.deque return_queue: The queue used to return data
            from the thread.

        :param threading.Event abort_event: An event that is set when the thread
            needs to abort, and otherwise is not set.
        """
        threading.Thread.__init__(self, name=name)

        logger.info("Starting exposure control thread: %s", self.name)

        self.daemon = True

        self.command_queue = command_queue
        self.return_queue = return_queue
        self._abort_event = abort_event
        self._exp_event = exp_event
        self._stop_event = threading.Event()
        self._settings = settings

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

        #
        # #  Create a new record list.
        # #

        # mx_database = mp.RecordList( None )

        # # mx_database.set_network_debug(255)

        # #
        # #  Enable plotting
        # #

        # mx_database.set_plot_enable(1)

        # #
        # #  Read the database in and initialize the corresponding hardware.
        # #

        # mx_database.read_database( database_filename )

        # mx_database.finish_database_initialization()

        # mx_database.initialize_hardware( 0 )

        # mx_database.unsaved_scans = 0




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
            }

        self._mx_data = mx_data

        self._commands = {'start_exp'   : self._start_exp,
                        }

    def run(self):
        """
        Custom run method for the thread.
        """
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
                logger.debug("Processing cmd '%s' with args: %s and kwargs: %s ", command, ', '.join(['{}'.format(a) for a in args]), ', '.join(['{}:{}'.format(kw, item) for kw, item in kwargs.items()]))
                try:
                    self._commands[command](*args, **kwargs)
                except Exception:
                    msg = ("Exposure control thread failed to run command '%s' "
                        "with args: %s and kwargs: %s " %(command,
                        ', '.join(['{}'.format(a) for a in args]),
                        ', '.join(['{}:{}'.format(kw, item) for kw, item in kwargs.items()])))
                    logger.exception(msg)

        if self._stop_event.is_set():
            self._stop_event.clear()
        else:
            self._abort()

        logger.info("Quitting exposure control thread: %s", self.name)

    def _start_exp(self, data_dir, fprefix, num_frames, exp_time, exp_period, **kwargs):
        """
        This method connects to a flow meter by creating a new :py:class:`FlowMeter`
        subclass object (e.g. a new :py:class:`BFS` object). This pump is saved
        in the thread and can be called later to do stuff. All pumps must be
        connected before they can be used.

        :param str device: The device comport

        :param str name: A unique identifier for the pump

        :param str pump_type: A pump type in the ``known_fms`` dictionary.

        :param \*\*kwargs: This function accepts arbitrary keyword args that
            are passed directly to the :py:class:`FlowMeter` subclass that is
            called. For example, for a :py:class:`BFS` you could pass ``bfs_filter``.
        """
        print('Choosing exposure type')
        if exp_period < exp_time + self._settings['slow_mode_thres']:
            logger.debug('Choosing fast exposure')
            self.fast_exposure(data_dir, fprefix, num_frames, exp_time, exp_period, **kwargs)
        else:
            logger.debug('Choosing slow exposure')
            self.slow_exposure2(data_dir, fprefix, num_frames, exp_time, exp_period, **kwargs)

    def fast_exposure(self, data_dir, fprefix, num_frames, exp_time, exp_period, **kwargs):
        logger.debug('Setting up fast exposure')
        det = self._mx_data['det']          #Detector

        struck = self._mx_data['struck']    #Struck SIS3820
        s0 = self._mx_data['struck_ctrs'][0]
        s1 = self._mx_data['struck_ctrs'][1]
        s2 = self._mx_data['struck_ctrs'][2]
        s3 = self._mx_data['struck_ctrs'][3]

        ab_burst = self._mx_data['ab_burst']   #Shutter control signal
        cd_burst = self._mx_data['cd_burst']   #Struck LNE/channel advance signal
        ef_burst = self._mx_data['ef_burst']   #Pilatus trigger signal
        gh_burst = self._mx_data['gh_burst']
        dg645_trigger_source = self._mx_data['dg645_trigger_source']

        dio_out6 = self._mx_data['dio'][6]      #Xia/wharberton shutter N.C.
        dio_out9 = self._mx_data['dio'][9]      #Shutter control signal (alt.)
        dio_out10 = self._mx_data['dio'][10]    #SRS DG645 trigger
        dio_out11 = self._mx_data['dio'][11]    #Struck LNE/channel advance signal (alt.)

        det_datadir = self._mx_data['det_datadir']
        det_filename = self._mx_data['det_filename']

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
        det_datadir.put(data_dir)
        det_filename.put('{}_0001.tif'.format(fprefix))

        dio_out6.write(0) #Open the slow normally closed xia shutter

        det.set_duration_mode(num_frames)
        det.set_trigger_mode( 2 )
        det.arm()

        struck.set_measurement_time(exp_time)   #Ignored for external LNE of Struck
        struck.set_num_measurements(num_frames)
        struck.start()

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

        dg645_trigger_source.put(1)

        ab_burst.arm()

        if continuous_exp:
            dio_out9.write(1)

        dio_out11.write(1)
        time.sleep(0.01)
        dio_out11.write(0)

        time.sleep(0.1)

        dio_out10.write(1)
        logger.info('Exposure started')
        self._exp_event.set()
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

        self.write_counters_struck(measurement, num_frames, 4, data_dir, fprefix,
            exp_period, dark_counts)
        logger.info('Exposure done')
        self._exp_event.clear()

        # print(type(measurement))


    def slow_exposure(self, data_dir, fprefix, num_frames, exp_time, exp_period, **kwargs):
        logger.debug('Setting up slow exposure')
        det = self._mx_data['det']     #Detector
        struck = self._mx_data['struck']   #Struck SIS3820

        ab_burst = self._mx_data['ab_burst']   #Shutter control signal
        cd_burst = self._mx_data['cd_burst']   #Struck LNE/channel advance signal
        ef_burst = self._mx_data['ef_burst']   #Pilatus trigger signal

        dio_out6 = self._mx_data['dio'][6]      #Xia/wharberton shutter N.C.
        dio_out10 = self._mx_data['dio'][10]    #SRS DG645 trigger
        dio_out11 = self._mx_data['dio'][11]    #Struck LNE/channel advance signal (alt.)

        det_datadir = self._mx_data['det_datadir']
        det_filename = self._mx_data['det_filename']

        logger.debug(det)
        logger.debug(struck)
        logger.debug(ab_burst)
        logger.debug(cd_burst)
        logger.debug(ef_burst)
        logger.debug(dio_out10)

        det.abort()
        struck.stop()
        ab_burst.stop()

        det_datadir.put(data_dir)
        det_filename.put('{}_0001.tif'.format(fprefix))

        dio_out6.write(0) #Open the slow normally closed xia shutter

        det.set_duration_mode(num_frames)
        det.set_trigger_mode(2)
        det.arm()

        struck.set_num_measurements(1)

        # ab_burst.setup(1, exp_time+0.02, exp_time+0.01, 1, 0)
        # cd_burst.setup(1, exp_time+0.02, 0.0001, 1, exp_time+0.006)
        # ef_burst.setup(1, exp_time+0.02, exp_time, 1, 0.005)

        # ab_burst.start()

        #Read out the struck initially, takes ~2-3 seconds the first time
        struck.start()
        dio_out11.write(1)
        time.sleep(0.001)
        dio_out11.write(0)

        time.sleep(0.1)

        dio_out11.write(1)
        time.sleep(0.001)
        dio_out11.write(0)

        while True:
            busy = struck.is_busy()

            if (busy == 0):
                measurement = struck.read_all()
                logger.debug( "Initial Struck Readout Done!\n" )
                break

        start = time.time()

        for i in range(num_frames):
            if self._abort_event.is_set():
                logger.info("Aborting slow exposure")
                det.abort()
                struck.stop()
                ab_burst.stop()
                dio_out6.write(1) #Closes the slow normally closed xia shutter
                det.abort()
                self._exp_event.clear()
                return

            logger.debug( "*** i = %d ***" % (i) )
            logger.debug( "Time = %f\n" % (time.time() - start) )

            struck.start()

            logger.debug( "After struck.start() = %f\n" % (time.time() - start) )

            dio_out11.write(1)
            time.sleep(0.001)
            dio_out11.write(0)

            logger.debug('After dio_out11 signal = %f\n' %(time.time()-start))

            if i == 0:
                logger.info('Exposure started')
                self._exp_event.set()
                meas_start = time.time()

            while time.time() - meas_start < i*exp_period:
                time.sleep(0.001)

            logger.debug("Measurement start time = %f\n" %(time.time() - meas_start))

            dio_out10.write(1)
            time.sleep(0.001)
            dio_out10.write(0)
            logger.debug( "After dio_out10 signal = %f\n" % (time.time() - start) )

            while True:
                busy = struck.is_busy()

                if self._abort_event.is_set():
                    logger.info("Aborting slow exposure")
                    det.abort()
                    struck.stop()
                    ab_burst.stop()
                    dio_out6.write(1) #Close the slow normally closed xia shutter
                    det.abort()
                    self._exp_event.clear()
                    return

                if busy == 0:
                    logger.debug( "Struck Done!\n" )

                    logger.debug( "After Struck Done = %f\n" % \
                            (time.time() - start ) )

                    measurement = struck.read_measurement(0)

                    logger.debug( "After Struck Readout = %f\n" % \
                            (time.time() - start ) )

                    logger.info(measurement)
                    break

                time.sleep(0.001)

        dio_out6.write(1) #Close the slow normally closed xia shutter

        logger.info('Exposure done')
        self._exp_event.clear()

        return

    def slow_exposure2(self, data_dir, fprefix, num_frames, exp_time, exp_period, **kwargs):
        logger.debug('Setting up slow exposure 2')
        det = self._mx_data['det']     #Detector

        ab_burst = self._mx_data['ab_burst']   #Shutter control signal
        cd_burst = self._mx_data['cd_burst']
        ef_burst = self._mx_data['ef_burst']   #Pilatus trigger signal
        gh_burst = self._mx_data['gh_burst']
        dg645_trigger_source = self._mx_data['dg645_trigger_source']

        dio_out6 = self._mx_data['dio'][6]      #Xia/wharberton shutter N.C.
        dio_out10 = self._mx_data['dio'][10]    #SRS DG645 trigger

        joerger = self._mx_data['joerger']

        j2 = self._mx_data['joerger_ctrs'][0]
        j3 = self._mx_data['joerger_ctrs'][1]
        j4 = self._mx_data['joerger_ctrs'][2]
        j5 = self._mx_data['joerger_ctrs'][3]
        j6 = self._mx_data['joerger_ctrs'][4]
        # j7 = self._mx_data['joerger_ctrs'][6]

        scl_list = [j2, j3, j4, j5, j6]

        measurement = [[0 for i in range(num_frames)] for j in range(len(scl_list))]
        exp_start = [0 for i in range(num_frames)]

        det_datadir = self._mx_data['det_datadir']
        det_filename = self._mx_data['det_filename']

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
                self.slow_mode2_abort_cleanup(det, joerger, ab_burst, dio_out6,
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
                self._exp_event.set()
                meas_start = time.time()
                i_meas = time.time()

            while time.time() - meas_start < i*exp_period:
                if self._abort_event.is_set():
                    # logger.debug('abort 2')
                    self.slow_mode2_abort_cleanup(det, joerger, ab_burst, dio_out6,
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
                    self.slow_mode2_abort_cleanup(det, joerger, ab_burst, dio_out6,
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
                    self.slow_mode2_abort_cleanup(det, joerger, ab_burst, dio_out6,
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
        self._exp_event.clear()

        return

    def write_counters_struck(self, cvals, num_frames, num_counters, data_dir,
            fprefix, exp_period, dark_counts):
        data_dir = data_dir.replace(settings['remote_dir_root'], settings['local_dir_root'], 1)

        header = '#Filename\tstart_time\texposure_time\tI0\tI1\tI2\tI3\n'

        log_file = os.path.join(data_dir, '{}.log'.format(fprefix))
        with open(log_file, 'w') as f:
            f.write(header)
            for i in range(num_frames):
                val = "{}_{:04d}.tif\t{}".format(fprefix, i+1, exp_period*i)
                val = val + "\t{}".format(cvals[0][i]/50.e6)

                for j in range(3, num_counters+3):
                        val = val + "\t{}".format(cvals[j][i]-dark_counts[j-3])

                val = val + '\n'
                f.write(val)

    def write_counters_joerger(self, cvals, num_frames, data_dir, fprefix, exp_start):
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

    def slow_mode2_abort_cleanup(self, det, joerger, ab_burst, dio_out6,
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

        self.write_counters_joerger(measurement, num_frames, data_dir, fprefix, exp_start)

        self._exp_event.clear()
        return

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
    def __init__(self, mx_data, settings, exp_cmd_q, abort_event, exp_event, exp_con,
        *args, **kwargs):
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

        """

        wx.Panel.__init__(self, *args, **kwargs)
        logger.debug('Initializing ExpPanel')

        self.mx_data = mx_data
        self.settings = settings
        self.exp_cmd_q = exp_cmd_q
        self.abort_event = abort_event
        self.exp_event = exp_event
        self.exp_con = exp_con

        self.tr_timer = wx.Timer(self)
        self.Bind(wx.EVT_TIMER, self._on_tr_timer, self.tr_timer)

        self.top_sizer = self._create_layout()

        self.SetSizer(self.top_sizer)


    def _create_layout(self):
        """Creates the layout for the panel."""
        self.data_dir = wx.TextCtrl(self, value=self.settings['data_dir'],
            style=wx.TE_READONLY)

        file_open = wx.ArtProvider.GetBitmap(wx.ART_FOLDER_OPEN, wx.ART_BUTTON)
        self.change_dir_btn = wx.BitmapButton(self, bitmap=file_open,
            size=(file_open.GetWidth()+15, -1))
        self.change_dir_btn.Bind(wx.EVT_BUTTON, self._on_change_dir)

        self.filename = wx.TextCtrl(self, value=self.settings['filename'],
            validator=utils.CharValidator('fname'))
        self.num_frames = wx.TextCtrl(self, value=self.settings['exp_num'],
            size=(60,-1), validator=utils.CharValidator('int'))
        self.exp_time = wx.TextCtrl(self, value=self.settings['exp_time'],
            size=(60,-1), validator=utils.CharValidator('float'))
        self.exp_period = wx.TextCtrl(self, value=self.settings['exp_period'],
            size=(60,-1), validator=utils.CharValidator('float'))
        self.run_num = wx.StaticText(self, label='_{:03d}'.format(self.settings['run_num']))



        file_prefix_sizer = wx.BoxSizer(wx.HORIZONTAL)
        file_prefix_sizer.Add(self.filename, proportion=1)
        file_prefix_sizer.Add(self.run_num, flag=wx.ALIGN_BOTTOM)

        self.exp_name_sizer = wx.GridBagSizer(vgap=5, hgap=5)

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
        self.exp_time_sizer.Add(self.num_frames, border=5, flag=wx.ALIGN_CENTER_VERTICAL|wx.LEFT)
        self.exp_time_sizer.Add(wx.StaticText(self, label='Exp. time [s]:'),
            border=5, flag=wx.ALIGN_CENTER_VERTICAL|wx.LEFT)
        self.exp_time_sizer.Add(self.exp_time, border=5, flag=wx.ALIGN_CENTER_VERTICAL|wx.LEFT)
        self.exp_time_sizer.Add(wx.StaticText(self, label='Exp. period [s]:'),
            border=5, flag=wx.ALIGN_CENTER_VERTICAL|wx.LEFT)
        self.exp_time_sizer.Add(self.exp_period, border=5, flag=wx.ALIGN_CENTER_VERTICAL|wx.LEFT)


        self.start_exp_btn = wx.Button(self, label='Start Exposure')
        self.start_exp_btn.Bind(wx.EVT_BUTTON, self._on_start_exp)

        self.stop_exp_btn = wx.Button(self, label='Stop Exposure')
        self.stop_exp_btn.Bind(wx.EVT_BUTTON, self._on_stop_exp)
        self.stop_exp_btn.Disable()

        self.exp_btn_sizer = wx.BoxSizer(wx.HORIZONTAL)
        self.exp_btn_sizer.AddStretchSpacer(1)
        self.exp_btn_sizer.Add(self.start_exp_btn, border=5,
            flag=wx.ALIGN_CENTER_VERTICAL|wx.ALIGN_RIGHT|wx.RIGHT)
        self.exp_btn_sizer.Add(self.stop_exp_btn, border=5,
            flag=wx.ALIGN_CENTER_VERTICAL|wx.ALIGN_LEFT|wx.LEFT)
        self.exp_btn_sizer.AddStretchSpacer(1)

        exp_ctrl_box_sizer = wx.StaticBoxSizer(wx.StaticBox(self,
            label='Exposure Controls'), wx.VERTICAL)

        exp_ctrl_box_sizer.Add(self.exp_name_sizer, border=5,
            flag=wx.EXPAND|wx.TOP|wx.LEFT|wx.RIGHT)
        exp_ctrl_box_sizer.Add(self.exp_time_sizer, border=5,
            flag=wx.TOP|wx.LEFT|wx.RIGHT)
        exp_ctrl_box_sizer.Add(self.exp_btn_sizer, border=5,
            flag=wx.EXPAND|wx.ALIGN_CENTER_HORIZONTAL|wx.ALL)


        self.status = wx.StaticText(self, label='Ready', style=wx.ST_NO_AUTORESIZE,
            size=(150, -1))
        self.status.SetForegroundColour(wx.RED)
        fsize = self.GetFont().GetPointSize()
        font = wx.Font(fsize, wx.DEFAULT, wx.NORMAL, wx.BOLD)
        self.status.SetFont(font)

        self.time_remaining = wx.StaticText(self, label='0', style=wx.ST_NO_AUTORESIZE,
            size=(150, -1))
        self.time_remaining.SetFont(font)

        exp_status_sizer = wx.StaticBoxSizer(wx.StaticBox(self,
            label='Exposure Status'), wx.HORIZONTAL)

        exp_status_sizer.Add(wx.StaticText(self, label='Status:'), border=5,
            flag=wx.ALIGN_CENTER_VERTICAL|wx.TOP|wx.LEFT|wx.BOTTOM)
        exp_status_sizer.Add(self.status, border=5,
            flag=wx.ALIGN_CENTER_VERTICAL|wx.LEFT|wx.TOP|wx.BOTTOM)
        exp_status_sizer.Add(wx.StaticText(self, label='Time remaining:'), border=5,
            flag=wx.ALIGN_CENTER_VERTICAL|wx.LEFT|wx.TOP|wx.BOTTOM)
        exp_status_sizer.Add(self.time_remaining, border=5,
            flag=wx.ALIGN_CENTER_VERTICAL|wx.ALL)
        exp_status_sizer.AddStretchSpacer(1)



        top_sizer = wx.BoxSizer(wx.VERTICAL)
        top_sizer.Add(exp_ctrl_box_sizer, flag=wx.EXPAND)
        top_sizer.Add(exp_status_sizer,border=10, flag=wx.EXPAND|wx.TOP)

        return top_sizer

    def _on_change_dir(self, evt):
        with wx.DirDialog(self, "Select Directory", self.data_dir.GetValue()) as fd:
            if fd.ShowModal() == wx.ID_CANCEL:
                return

            pathname = fd.GetPath()

            if pathname.startswith(self.settings['base_data_dir']):
                self.data_dir.SetValue(pathname)
            else:
                msg = ('Directory must be the following directory or one of '
                    'its subdirectories: {}'.format(settings['base_data_dir']))
                wx.CallAfter(wx.MessageBox, msg, 'Invalid directory',
                    style=wx.OK|wx.ICON_ERROR)

        return

    def _on_start_exp(self, evt):
        self.start_exp()

    def _on_stop_exp(self, evt):
        self.stop_exp()

    def start_exp(self):
        self.abort_event.clear()
        self.exp_event.clear()

        exp_values, valid = self._get_exp_values()

        if not valid:
            return

        self.set_status('Preparing exposure')
        self.start_exp_btn.Disable()
        self.stop_exp_btn.Enable()
        self.total_time = exp_values['num_frames']*exp_values['exp_period']

        #Exposure time fudge factors for the overhead and readout
        if exp_values['exp_period'] < exp_values['exp_time'] + self.settings['slow_mode_thres']:
            self.total_time = self.total_time+2

        self.set_time_remaining(self.total_time)

        self.exp_cmd_q.append(('start_exp', (), exp_values))

        start_thread = threading.Thread(target=self._wait_for_exp_start)
        start_thread.daemon = True
        start_thread.start()

        return

    def _wait_for_exp_start(self):
        while not self.exp_event.is_set() and not self.abort_event.is_set():
            time.sleep(0.001)

        if self.abort_event.is_set():
            return
        else:
            self.initial_time = time.time()
            wx.CallAfter(self.tr_timer.Start, 1000)
            wx.CallAfter(self.set_status, 'Exposing')

        return

    def stop_exp(self):
        self.abort_event.set()
        self._on_exp_finish()

    def _on_exp_finish(self):
        self.tr_timer.Stop()
        self.start_exp_btn.Enable()
        self.stop_exp_btn.Disable()
        self.set_status('Ready')
        self.set_time_remaining(0)
        old_rn = self.run_num.GetLabel()
        run_num = int(old_rn[1:])+1
        self.run_num.SetLabel('_{:03d}'.format(run_num))


    def set_status(self, status):
        self.status.SetLabel(status)

    def set_time_remaining(self, tr):
        if tr < 3600:
            tr = time.strftime('%M:%S', time.gmtime(tr))
        elif tr < 86400:
            tr = time.strftime('%H:%M:%S', time.gmtime(tr))
        else:
            tr = time.strftime('%d:%H:%M:%S', time.gmtime(tr))

        self.time_remaining.SetLabel(tr)

    def _on_tr_timer(self, evt):
        if self.exp_event.is_set():
            tr = self.total_time - (time.time() - self.initial_time)

            if tr < 0:
                tr = 0

            self.set_time_remaining(tr)

        else:
            self._on_exp_finish()

    def _get_exp_values(self):
        num_frames = self.num_frames.GetValue()
        exp_time = self.exp_time.GetValue()
        exp_period = self.exp_period.GetValue()
        data_dir = self.data_dir.GetValue()
        filename = self.filename.GetValue()
        run_num = self.run_num.GetLabel()

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

        if isinstance(num_frames, int):
            if num_frames < 1 or num_frames > self.settings['nframes_max']:
                errors.append('Number of frames (between 1 and {}'.format(self.settings['nframes_max']))

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

        if filename == '':
            errors.append('Filename (must not be blank)')

        if data_dir == '' or not os.path.exists(data_dir):
            errors.append('Data directory (must exist, and not be blank)')

        if len(errors) > 0:
            msg = 'The following field(s) have invalid values:'
            for err in errors:
                msg = msg + '\n- ' + err
            msg = msg + ('\n\nPlease correct these errors, then start the exposure.')

            wx.CallAfter(wx.MessageBox, msg, 'Error in exposure parameters',
                style=wx.OK|wx.ICON_ERROR)

            exp_values = {}
            valid = False

        else:
            data_dir = data_dir.replace(settings['local_dir_root'], settings['remote_dir_root'], 1)

            exp_values = {'num_frames': num_frames,
                'exp_time': exp_time,
                'exp_period': exp_period,
                'data_dir': data_dir,
                'fprefix': filename+run_num,
                }
            valid = True

        return exp_values, valid

class ExpFrame(wx.Frame):
    """
    A lightweight frame allowing one to work with arbitrary number of pumps.
    Only meant to be used when the pumpcon module is run directly,
    rather than when it is imported into another program.
    """
    def __init__(self, mx_data, settings, *args, **kwargs):
        """
        Initializes the pump frame. Takes args and kwargs for the wx.Frame class.
        """
        super(ExpFrame, self).__init__(*args, **kwargs)
        logger.debug('Setting up the ExpFrame')

        self.mx_data = mx_data

        self.exp_cmd_q = deque()
        self.exp_ret_q = deque()
        self.abort_event = threading.Event()
        self.exp_event = threading.Event()
        self.exp_con = ExpCommThread(self.exp_cmd_q, self.exp_ret_q, self.abort_event,
            self.exp_event, self.mx_data, settings, 'ExpCon')
        self.exp_con.start()

        # self.exp_con = None #For testing purposes

        self.Bind(wx.EVT_CLOSE, self._on_exit)

        top_sizer = self._create_layout(settings)

        self.SetSizer(top_sizer)

        self.Fit()
        self.Raise()

    def _create_layout(self, settings):
        """Creates the layout"""
        exp_panel = ExpPanel(self.mx_data, settings, self.exp_cmd_q, self.abort_event,
            self.exp_event, self.exp_con, self)

        self.exp_sizer = wx.BoxSizer(wx.HORIZONTAL)
        self.exp_sizer.Add(exp_panel, proportion=1, flag=wx.EXPAND)

        top_sizer = wx.BoxSizer(wx.VERTICAL)
        top_sizer.Add(self.exp_sizer, flag=wx.EXPAND|wx.ALL, border=5)

        return top_sizer

    def _on_exit(self, evt):
        """Stops all current pump motions and then closes the frame."""
        logger.debug('Closing the ExpFrame')
        if self.exp_event.is_set() and not self.abort_event.is_set():
            self.abort_event.set()
            time.sleep(2)

        self.exp_con.stop()
        while self.exp_con.is_alive():
            time.sleep(0.001)
        self.Destroy()


if __name__ == '__main__':
    logger = logging.getLogger(__name__)
    logger.setLevel(logging.DEBUG)
    h1 = logging.StreamHandler(sys.stdout)
    h1.setLevel(logging.DEBUG)
    formatter = logging.Formatter('%(asctime)s - %(name)s - %(threadName)s - %(levelname)s - %(message)s')
    h1.setFormatter(formatter)
    logger.addHandler(h1)

    #Settings for Pilatus 3X 1M
    settings = {'data_dir': '/nas_data/Pilatus1M/20180917Lavender',
        'filename':'test',
        'run_num': 1,
        'exp_time': '0.5',
        'exp_period': '1.5',
        'exp_num': '5',
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

    # #MX stuff
    # try:
    #     # First try to get the name from an environment variable.
    #     database_filename = os.environ["MXDATABASE"]
    # except:
    #     # If the environment variable does not exist, construct
    #     # the filename for the default MX database.
    #     mxdir = utils.get_mxdir()
    #     database_filename = os.path.join(mxdir, "etc", "mpilatus.dat")
    #     database_filename = os.path.normpath(database_filename)

    # mx_database = mp.setup_database(database_filename)
    # mx_database.set_plot_enable(2)

    # det = mx_database.get_record('pilatus')

    # server_record_name = det.get_field('server_record')
    # remote_det_name = det.get_field('remote_record_name')
    # server_record = mx_database.get_record(server_record_name)
    # det_datadir_name = '{}.datafile_directory'.format(remote_det_name)
    # det_datafile_name = '{}.datafile_pattern'.format(remote_det_name)

    # det_datadir = mp.Net(server_record, det_datadir_name)
    # det_filename = mp.Net(server_record, det_datafile_name)

    # mx_data = {'det': det,
    #     'det_datadir': det_datadir,
    #     'det_filename': det_filename,
    #     'struck': mx_database.get_record('sis3820'),
    #     'ab_burst': mx_database.get_record('ab_burst'),
    #     'cd_burst': mx_database.get_record('cd_burst'),
    #     'ef_burst': mx_database.get_record('ef_burst'),
    #     'dio': [mx_database.get_record('avme944x_out{}'.format(i)) for i in range(16)],
    #     'joerger': mx_database.get_record('joerger_timer'),
    #     'joerger_ctrs':[mx_database.get_record('j{}'.format(i)) for i in range(2,8)],
    #     'mx_db': mx_database,
    #     }

    mx_data = {} #Testing only
    app = wx.App()
    logger.debug('Setting up wx app')
    frame = ExpFrame(mx_data, settings, None, title='Exposure Control')
    frame.Show()
    app.MainLoop()


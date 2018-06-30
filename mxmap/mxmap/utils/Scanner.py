from os.path import join
import multiprocessing
try:
    import queue
except ImportError:
    import Queue as queue

import numpy as np

import Mp


class Scanner(multiprocessing.Process):
    """
    This is a separate Process (as opposed to Thread) that runs the ``Mp``
    scan. It has to be a Process because even in a new Thread the scan
    eats all processing resources and essentially locks the GUI while it's
    running.
    """
    def __init__(self, command_queue, return_queue, abort_event):
        """
        Initializes the Process.

        :param multiprocessing.Manager.Queue command_queue: This queue is used
            to pass commands to the scan process.

        :param multiprocessing.Manager.Queue return_queue: This queue is used
            to return values from the scan process.

        :param multiprocessing.Manager.Event abort_event: This event is set when
            a scan needs to be aborted.
        """
        multiprocessing.Process.__init__(self)
        self.daemon = True

        self.command_queue = command_queue
        self.return_queue = return_queue
        self._abort_event = abort_event
        self._stop_event = multiprocessing.Event()

        Mp.set_user_interrupt_function(self._stop_scan)

        self._commands = {'start_mxdb'  : self._start_mxdb,
                        'set_devices'   : self._set_devices,
                        'scan'          : self._run_scan,
                        'get_devices'   : self._get_devices,
                        }


    def run(self):
        """
        Runs the process. It waits for commands to show up in the command_queue,
        and then runs them. It is aborted if the abort_event is set. It is stopped
        when the stop_event is set, and that allows the process to end gracefully.
        """
        while True:
            try:
                cmd, args, kwargs = self.command_queue.get_nowait()
                print(cmd)
            except queue.Empty:
                cmd = None

            if self._abort_event.is_set():
                self._abort()
                cmd = None

            if self._stop_event.is_set():
                self._abort()
                break

            if cmd is not None:
                try:
                    self.working=True
                    self._commands[cmd](*args, **kwargs)
                    self.working=False
                except Exception:
                    self.working=False

        if self._stop_event.is_set():
            self._stop_event.clear()
        else:
            self._abort()

    def _start_mxdb(self, db_path):
        """
        Starts the MX database

        :param str db_path: The path to the MX database.
        """
        self.db_path = db_path
        print("MX Database : %s is being downloaded..."%(self.db_path))
        self.mx_database = Mp.setup_database(self.db_path)
        self.mx_database.set_plot_enable(2)
        print("Database has been set up")

    def _set_devices(self, dir_path, x_motor, x_start, x_step, x_end, y_motor, y_start,
        y_step, y_end, scalers, dwell_time, detector, timer=None, file_name='output'):
        """
        Sets the parameters for the scan.

        :param str dir_path: The directory path where the scan file will be saved.
        :param str x_motor: The MX record name of the x motor.
        :param float x_start: The absolute x start position of the scan.
        :param float x_step: The step x size of the scan.
        :param float x_end: The absolute x stop position of the scan.
        :param str y_motor: The MX record name of the y motor.
        :param float y_start: The absolute y start position of the scan.
        :param float y_step: The step y size of the scan.
        :param float y_end: The absolute y stop position of the scan.
        :param list scalers: A list of the scalers for the scan.
        :param float dwell_time: The count time at each point in the scan.
        :param str timer: The name of the timer to be used for the scan.
        :param str detector: The name of the detector to be used for the scan.
        :param str file_name: The scan name (and output name) for the scan.
        """
        self.dir_path = dir_path
        self.x_motor = x_motor
        self.x_start = x_start
        self.x_step = x_step
        self.x_end = x_end
        self.y_motor = y_motor
        self.y_start = y_start
        self.y_step = y_step
        self.y_end = y_end
        self.scalers = scalers
        self.dwell_time = dwell_time
        self.detector = detector
        self.y_nsteps = abs(int(np.floor((self.y_end - self.y_start) / self.y_step))) + 1
        self.x_nsteps = abs(int(np.floor((self.x_end - self.x_start) / self.x_step))) + 1
        self.timer = timer
        self.output = file_name

    def _run_scan(self):
        """
        Performing scan by create a scan descriptions for each row and perform
        """
        all_records = [r.name for r in self.mx_database.get_all_records()]
        i = 0

        while 'row'+str(i)+'_0' in all_records:
            i += 1
        name = 'row'+str(i)+'_'

        if self._abort_event.is_set():
            self.return_queue.put_nowait(['stop_live_plotting'])
            return

        for i in range(self.y_nsteps):
            self._scan(name, i)
            if self._abort_event.is_set():
                return
        print("All scans are performed. Output files are at %s" %(self.dir_path))

        self.return_queue.put_nowait(['stop_live_plotting'])
        return

    def _get_devices(self, scaler_fields, det_fields):
        """
        Gets a list of all of the relevant devices and returns them to populate
        the scan GUI.

        :param list scaler_fields: A list of the scaler record types to return.
            Defined in the mxmap_config file.
        :param list det_fields: A list of the detector record types to return.
            Defined in the mxmap_config file.
        """
        xmotor_list = []
        ymotor_list = []
        scaler_list = []
        detector_list = []

        record_list = self.mx_database
        list_head_record = record_list.list_head_record
        list_head_name = list_head_record.name
        current_record = list_head_record.get_next_record()

        while (current_record.name != list_head_name):
            current_record_class = current_record.get_field('mx_class')
            current_record_superclass = current_record.get_field('mx_superclass')
            current_record_type = current_record.get_field('mx_type')
            print current_record.name, current_record_class, current_record_superclass, current_record_type

            # if current_record_superclass == 'device':
            #     # ignore a record if it's not a device
            if current_record_class == 'motor':
                # Add a record to x and y motors
                xmotor_list.append(current_record.name)
                ymotor_list.append(current_record.name)
            elif current_record_class in scaler_fields:
                # Add a record to scalers
                scaler_list.append(current_record.name)
            elif current_record_class in det_fields:
                # Add a record to detectors
                detector_list.append(current_record.name)

            current_record = current_record.get_next_record()

        self.return_queue.put_nowait([xmotor_list, ymotor_list, scaler_list, detector_list])

    def _scan(self, name, row):
        """
        Creae a scan record and carry out the scan.

        :param str name: The base name of the scan.
        :param str row: The current row of the scan.
        """

        scan_name = name + str(row)
        print("Scanning %s" % (scan_name))

        # Generate description
        y = self.y_start + self.y_step * row

        description = ("%s scan linear_scan motor_scan \"\" \"\" " % (scan_name))

        num_scans = 1
        num_motors = 2

        num_independent_variables = num_motors

        description = description + ("%d %d %d " % (num_scans, num_independent_variables, num_motors))

        description = description + ("%s " % (str(self.x_motor)))
        description = description + ("%s " % (str(self.y_motor)))

        scalers_detector = list(self.scalers)

        if self.detector is not None:
            scalers_detector.append(self.detector['name'])

        description = description + ("%d " % (len(scalers_detector)))

        for j in range(len(scalers_detector)):
            description = description + ("%s " % (scalers_detector[j]))

        scan_flags = 0x0
        settling_time = 0.0
        measurement_type = "preset_time"
        measurement_time = self.dwell_time

        if self.timer is not None and len(self.timer) > 0:
            timer_name = self.timer
        else:
            timer_name = "joerger_timer"

        description = description + (
                "%x %f %s \"%f %s\" " % (scan_flags, settling_time, measurement_type, measurement_time, timer_name))

        datafile_description = "sff"
        file_name = self.output + '.' + str(row).zfill(4)
        datafile_name = join(self.dir_path, file_name)
        plot_description = "none"
        plot_arguments = "$f[0]"

        description = description + (
                "%s %s %s %s " % (datafile_description, datafile_name, plot_description, plot_arguments))

        description = description + ("%f " % (self.x_start))
        description = description + ("%f " % (y))

        description = description + ("%f " % (self.x_step))
        description = description + ("%f " % (1))

        description = description + ("%d " % (self.x_nsteps))
        description = description + ("%d " % (1))

        print("Description = %s" % (description))

        self.mx_database.create_record_from_description(description)

        scan = self.mx_database.get_record(scan_name)

        scan.finish_record_initialization()

        scan.perform_scan()

        print("%s has been performed" % (scan_name))

        self.return_queue.put_nowait([datafile_name])

    def _abort(self):
        """Clears the ``command_queue`` and aborts all current actions."""
        while True:
            try:
                self.command_queue.get_nowait()
            except queue.Empty:
                break

        self._abort_event.clear()

    def stop(self):
        """Stops the thread cleanly."""
        self._stop_event.set()

    def _stop_scan(self):
        """
        This function is used Mp to abort the scan.

        :returns: Returns 1 if the abort event is set, and that causes Mp to
            abort the running scan. Returns 0 otherwise, which doesn't abort
            anything.
        :rtype: int
        """
        return int(self._abort_event.is_set())

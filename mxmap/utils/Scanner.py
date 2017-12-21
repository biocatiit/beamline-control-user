from os.path import exists, join
from threading import Thread
import time
import Mp
import MpScan
import numpy as np
try:
    import queue
except ImportError:
    import Queue

class Scanner():
    def __init__(self):
        self.queue = Queue.Queue() # Queue for adding commands
        self.wait = True # Waiting for command status
        self.mx_database = None
        self.mx_thread = Thread(target=self.mx_main_loop) # MX Thread
        self.mx_thread.start()

    def setDivices(self, dir, x_motor, x_start, x_step, x_end, y_motor, y_start, y_step, y_end, scalers, dwell_time, detector, timer=None, file_name='output', callback = None, main_win = None):
        self.dir_path = dir
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
        self.callback = callback
        self.y_nsteps = int(np.floor((self.y_end - self.y_start) / self.y_step)) + 1
        self.x_nsteps = int(np.floor((self.x_end - self.x_start) / self.x_step)) + 1
        self.timer = timer
        self.output = file_name
        self.main_win = main_win

    def runCommand(self, cm):
        """
        Trigger mx_thread to run specific cm by adding command to queue
        :param cm: command
        :return:
        """
        self.queue.put(cm)

    def set_db_path(self, path):
        """
        Set MX DB path
        :param path: str full path of MX DB
        :return:
        """
        self.db_path = path

    def mx_main_loop(self):
        """
        Waiting command to perform
        :return:
        """
        while self.wait:
            if not self.queue.empty():
                command = self.queue.get()
                # print(command)
                if command == 'load_db':
                    if len(self.db_path) > 0:
                        # Load DB
                        print("MX Database : %s is being downloaded..."%(self.db_path))
                        self.mx_database = Mp.setup_database(self.db_path)
                        print("Database has been set up")
                    else:
                        print("Error : Please select MX Database")
                elif command == 'scan':
                    # Perform scan
                    print("Scan is performing")
                    self.performScan()
                elif command == 'stop':
                    # Stop thread
                    self.wait = False
                    break
                else:
                    print("%s : invalid command" % (command))
                    self.wait = False
                    break

    def performScan(self):
        """
        Performing scan by create a scan descriptions for each row and perform
        """
        all_records = [r.name for r in self.mx_database.get_all_records()]
        i = 0

        while 'row'+str(i)+'_0' in all_records:
            i += 1
        name = 'row'+str(i)+'_'

        for i in range(self.y_nsteps):
            self.scan(name, i)
        print("All scans are performed. Output files are at %s" %(self.dir_path))

        if self.main_win is not None:
            # Notify main window that all scans are done
            try:
                self.main_win.scan_done()
            except:
                pass

    def scan(self, name, row):
        """
        scan a record
        :param row: record scanning row
        :return:
        """
        if self.callback is not None:
            try:
                # Wait for GUI to be updated before starting scan
                while self.callback.plotting:
                    time.sleep(0.5)
                    continue
            except:
                self.callback = None

        scan_name = name + str(row)
        print("Scanning %s" % (scan_name))

        # Generate description
        max_len = len(str(self.y_nsteps))
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
        timer_name = self.timer if self.timer is not None and len(self.timer) > 0 else "joerger_timer"
        description = description + (
                "%x %f %s \"%f %s\" " % (scan_flags, settling_time, measurement_type, measurement_time, timer_name))

        datafile_description = "sff"
        file_name = self.output + '.' + str(row).zfill(max_len)
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

        # time.sleep(2) # test

        print("%s has been performed" % (scan_name))

        # After scanning is done, trigger gui to plot the row
        if self.callback is not None:
            # self.callback.plot(datafile_name)
            try:
                print("Plotting %s" % (scan_name))
                # t = Thread(target=self.callback.plot, args=(datafile_name,))
                # t.start()
                self.callback.plot(datafile_name)
            except Exception as e:
                print(e)
                print("Warning : Program is unable to plot, but scans are still performing")
                self.callback = None
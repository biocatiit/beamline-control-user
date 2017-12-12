from os.path import exists, join
from threading import Thread
import time
import Mp
import MpScan

DB_PATH = "/etc/mx/mxmotor.dat"
# DB_PATH = "/Users/jiranun/Work/RA/bm_scan/bm_scan/sample/motortst.dat"

def get_record():
    global DB_PATH
    return Mp.setup_database(DB_PATH)

def get_DB_Path():
    global DB_PATH
    return DB_PATH

def set_DB_Path(new_path):
    global DB_PATH
    DB_PATH = new_path

class Scanner():
    def __init__(self, dir, x_motor, x_start, x_step, x_end, y_motor, y_start, y_step, y_end, scalers, dwell_time, detector, callback = None):
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

        self.y_nsteps = int(round((self.y_end - self.y_start) / self.y_step)) + 1
        self.x_nsteps = int(round((self.x_end - self.x_start) / self.x_step)) + 1

        self.output = 'rast'

        # self.record_list = get_record()  # Get record list from DB #jiranun - Add back
        print("Database has been set up")

    def generateScanRecord(self):
        """
        Generate scan record file with scan record descriptions
        """
        # Pick a file name
        file_name = "scan_record.dat"
        if exists(join(self.dir_path, file_name)):
            i = 1
            while exists(join(self.dir_path, 'scan_record_'+str(i)+'.dat')):
                i += 1
            self.scanrecord_file = 'scan_record_'+str(i)+'.dat'
        else:
            self.scanrecord_file = file_name

        file = open(join(self.dir_path, self.scanrecord_file), 'w')

        max_len = len(str(self.y_nsteps))

        for i in range(self.y_nsteps):

            # Start constructing the scan description.
            scan_name = 'row'+str(i)
            y = self.y_start + self.y_step*i

            description = \
                ("%s scan linear_scan motor_scan \"\" \"\" " % (scan_name))

            num_scans = 1

            num_motors = 2
            num_independent_variables = num_motors

            description = description + \
                          ("%d %d %d " % (num_scans, num_independent_variables, num_motors))

            description = description + ("%s " % (str(self.x_motor)))
            description = description + ("%s " % (str(self.y_motor)))

            description = description + ("%d " % (len(self.scalers)))

            for j in range(len(self.scalers)):
                description = description + ("%s " % (self.scalers[j]))

            scan_flags = 0x0
            settling_time = 0.0
            measurement_type = "preset_time"
            measurement_time = self.dwell_time
            timer_name = 'joerger_timer'
            description = description + ("%x %f %s \"%f %s\" " %(scan_flags, settling_time, measurement_type, measurement_time, timer_name))

            datafile_description = "text"
            file_name = self.output +'.'+str(i).zfill(max_len)
            datafile_name = join(self.dir_path, file_name)
            plot_description = "none"
            plot_arguments = "$f[0]"

            description = description + ("%s %s %s %s " %(datafile_description, datafile_name, plot_description, plot_arguments))

            description = description + ("%f " % (self.x_start))
            description = description + ("%f " % (y))

            description = description + ("%f " % (self.x_step))
            description = description + ("%f " % (1))

            description = description + ("%d " % (self.x_nsteps))
            description = description + ("%d " % (1))
            # print(description)

            file.write(description)
            file.write('\n')

        file.close()
        print(str(join(self.dir_path, self.scanrecord_file))+' is generated.')

    def performScan(self):
        """
        Performing scan by using scan record which produced from generateScanRecord()
        and return scan data file name
        """
        #### jiranun - Add back
        # scan_database = join(self.dir_path, "scan_record.dat")
        # scan_database = join(self.dir_path, self.scanrecord_file)
        # MpScan.load_scans(self.record_list, scan_database)

        # Create another thread to scan all records
        t = Thread(target=self.scan_all)
        t.start()


    def scan_all(self):
        for i in range(self.y_nsteps):
            self.scan(i)

    def scan(self, row):
        """
        scan a record
        :param row: record scanning row
        :return:
        """
        scan_name = 'Row' + str(row)
        print("Scanning %s"%(scan_name))
        # scan = self.record_list.get_record(scan_name) # jiranun - Add back
        # scan.perform_scan() # jiranun - Add back

        time.sleep(2) # test

        # After scanning is done, trigger gui to plot the row
        if self.callback is not None:
            max_len = len(str(self.y_nsteps))
            file_name = self.output + '.' + str(row).zfill(max_len)
            full_path = join(self.dir_path, file_name)
            print(str(full_path)+" is ready.")
            self.callback.plot(full_path)
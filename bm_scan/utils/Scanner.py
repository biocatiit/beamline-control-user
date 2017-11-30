from os.path import exists, join
from threading import Thread
import time
import Mp
import MpScan

DB_PATH = "/Users/preawwy/RA/bm_scan/bm_scan/sample/example.dat"

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

        self.y_nsteps = ((self.y_end - self.y_start) / self.y_step) + 1
        self.x_nsteps = ((self.x_end - self.x_start) / self.x_step) + 1

        self.output = 'raster'

        self.record_list = get_record()  # Get record list from DB
        print "Database has been set up"

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

        file = open(self.scanrecord_file, 'w')

        max_len = len(str(self.y_nsteps))

        for i in range(self.y_nsteps):
            y = self.y_start + self.y_step*i

            # Create a setup record
            setup = ['row'+str(i), 'scan', 'linear_scan', 'motor_scan', '\"\" \"\"', '1', '2', '2', self.x_motor, self.y_motor]

            # Add Scalers
            setup.append(len(self.scalers))
            setup.extend(self.scalers)

            # Add Detector : TODO

            setup.extend([0, 0]) # ?? TODO

            file_name = self.output +'.'+str(i).zfill(max_len)
            full_path = join(self.dir_path, file_name)

            setup.extend(['\"'+str(self.dwell_time)+' joerger_timer\"', 'sff', full_path, 'none', '$f[0]', self.x_start, y, self.x_step, 1, self.x_nsteps, 1])

            file.write(" ".join(map(str, setup)))
            file.write('\n')
        file.close()
        print join(self.dir_path, self.scanrecord_file), 'is generated.'

    def performScan(self):
        """
        Performing scan by using scan record which produced from generateScanRecord()
        and return scan data file name
        """

        # scan_database = join(self.dir_path, self.scanrecord_file)
        scan_database = join(self.dir_path, "scan_record.dat")
        MpScan.load_scans(self.record_list, scan_database)

        scan = self.record_list.get_record("example2scan1")
        scan.perform_scan()
        scan = self.record_list.get_record("example2scan2")
        scan.perform_scan()
        scan = self.record_list.get_record("example2scan3")
        scan.perform_scan()

        # Create another thread to scan all records
        # t = Thread(target=self.scan_all)
        # t.start()


    def scan_all(self):
        for i in range(self.y_step):
            self.scan(i)

    def scan(self, name):
        """
        scan a record
        :param name: record scanning name
        :return:
        """
        # scan_name = 'Row' + str(row)
        # scan = self.record_list.get_record(scan_name)
        # scan.perform_scan()

        time.sleep(2) # test

        # After scanning done, trigger gui to plot at row
        if self.callback is not None:
            max_len = len(str(self.y_nsteps))
            file_name = self.output + '.' + str(row).zfill(max_len)
            full_path = join(self.dir_path, file_name)
            print full_path, "is ready."
            self.callback.plot(full_path)